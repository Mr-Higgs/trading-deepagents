"""Walk-forward Optuna search: tune on each train window, pick a plateau, score only the next test window."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

from optimize.evaluate import Evaluator, RunResult
from optimize.space import Space, apply_params, normalize, sample
from optimize.stats import bootstrap_ci, deflated_sharpe, neighborhood_scores, pbo_cscv, per_period_sharpe
from optimize.walkforward import Fold
from trading_agent.config import AppConfig

OBJECTIVES = ("sharpe", "expectancy", "mar")


@dataclass(frozen=True)
class Settings:
    trials: int = 200  # TOTAL across folds (split evenly); also the DSR trial count
    objective: str = "sharpe"
    min_trades: int = 30  # per train window; below it the objective is scaled down linearly
    radius: float = 0.15
    min_neighbors: int = 3
    seed: int = 0
    pbo_splits: int = 16
    jobs: int = 1  # folds run in parallel processes when > 1


@dataclass
class FoldResult:
    fold: Fold
    params: dict[str, Any]
    train_score: float
    plateau_score: float
    pbo: float
    oos: RunResult


@dataclass
class WalkForwardResult:
    folds: list[FoldResult]
    oos_daily: list[float]
    oos_trade_pnls: list[float]
    n_trials: int
    trial_sharpes: list[float] = field(default_factory=list)

    def summary(self, *, seed: int = 0) -> dict[str, Any]:
        trades = len(self.oos_trade_pnls)
        sr = per_period_sharpe(self.oos_daily)
        pbos = [f.pbo for f in self.folds if not math.isnan(f.pbo)]
        return {
            "oos_trades": trades,
            "oos_expectancy": statistics.fmean(self.oos_trade_pnls) if trades else 0.0,
            "oos_expectancy_ci95": bootstrap_ci(self.oos_trade_pnls, statistics.fmean, seed=seed),
            "oos_sharpe_ann": sr * math.sqrt(252),
            "oos_sharpe_ann_ci95": tuple(v * math.sqrt(252) for v in bootstrap_ci(self.oos_daily, per_period_sharpe, seed=seed)),
            "oos_total_pnl": sum(self.oos_trade_pnls),
            "deflated_sharpe": deflated_sharpe(self.oos_daily, n_trials=self.n_trials, trial_sharpes=self.trial_sharpes),
            "pbo_mean": statistics.fmean(pbos) if pbos else float("nan"),
            "n_trials": self.n_trials,
            "folds": len(self.folds),
        }


def score(res: RunResult, objective: str, min_trades: int) -> float:
    """Objective on a window: annualized Sharpe (default), $ expectancy or MAR, shrunk when trades < `min_trades`."""
    if objective not in OBJECTIVES:
        msg = f"objective must be one of {OBJECTIVES}"
        raise ValueError(msg)
    n = res.metrics.get("trades", 0)
    if objective == "sharpe":
        raw = per_period_sharpe(res.daily) * math.sqrt(252)
    elif objective == "expectancy":
        raw = float(res.metrics.get("expectancy", 0.0))
    else:
        raw = float(res.metrics.get("mar", 0.0))
    if n == 0:
        return -1e6  # never prefer "no trades"
    factor = min(1.0, n / min_trades) if min_trades > 0 else 1.0
    return raw * factor if raw > 0 else raw - (1.0 - factor)  # thin samples never look better


def plateau_pick(space: Space, trials: list[tuple[dict[str, Any], float]], *, radius: float, min_neighbors: int) -> tuple[dict[str, Any], float, float]:
    """Best candidate by neighborhood-median score (ties -> higher own score). Returns (params, own, plateau)."""
    points = [(*normalize(space, p), v) for p, v in trials]
    plateau = neighborhood_scores(points, radius=radius, min_neighbors=min_neighbors)
    best = max(range(len(trials)), key=lambda i: (plateau[i], trials[i][1]))
    return trials[best][0], trials[best][1], plateau[best]


def fold_budgets(trials: int, n_folds: int) -> list[int]:
    """Split `trials` across folds exactly (earlier folds take the remainder), at least one per fold."""
    base, extra = divmod(max(trials, n_folds), n_folds)
    return [base + (i < extra) for i in range(n_folds)]


def _run_fold(evaluator: Evaluator, base: AppConfig, space: Space, fold: Fold, index: int, trials: int, settings: Settings) -> tuple[FoldResult, list[float]]:
    """Tune on the fold's train window, pick a plateau, score it on the test window."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    rows: list[tuple[dict[str, Any], float]] = []
    daily: list[list[float]] = []

    def objective(trial: optuna.Trial) -> float:
        params = sample(trial, space)
        res = evaluator.run(apply_params(base, params), fold.train_start, fold.train_end)
        value = score(res, settings.objective, settings.min_trades)
        rows.append((params, value))
        daily.append(res.daily)
        return value

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=settings.seed + index))
    study.optimize(objective, n_trials=trials)
    params, own, plateau = plateau_pick(space, rows, radius=settings.radius, min_neighbors=settings.min_neighbors)
    oos = evaluator.run(apply_params(base, params), fold.test_start, fold.test_end)
    return FoldResult(fold, params, own, plateau, pbo_cscv(daily, n_splits=settings.pbo_splits), oos), [per_period_sharpe(d) for d in daily]


_FORK_STATE: tuple[Evaluator, AppConfig, Space, Settings] | None = None  # inherited by forked fold workers (no frame pickling)


def _fold_worker(fold: Fold, index: int, trials: int) -> tuple[FoldResult, list[float]]:
    assert _FORK_STATE is not None  # noqa: S101 - set by run_walk_forward before forking
    evaluator, base, space, settings = _FORK_STATE
    return _run_fold(evaluator, base, space, fold, index, trials, settings)


def run_walk_forward(evaluator: Evaluator, base: AppConfig, space: Space, folds: list[Fold], settings: Settings) -> WalkForwardResult:
    """Each fold's study is independent (own seed, own windows), so `settings.jobs > 1` runs folds in forked
    processes with identical results to the serial path."""
    global _FORK_STATE  # noqa: PLW0603 - fork-inherited worker context
    budget = fold_budgets(settings.trials, len(folds))
    if settings.jobs > 1 and len(folds) > 1:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor

        _FORK_STATE = (evaluator, base, space, settings)
        try:
            with ProcessPoolExecutor(max_workers=min(settings.jobs, len(folds)), mp_context=mp.get_context("fork")) as pool:
                outs = list(pool.map(_fold_worker, folds, range(len(folds)), budget))
        finally:
            _FORK_STATE = None
    else:
        outs = [_run_fold(evaluator, base, space, fold, i, budget[i], settings) for i, fold in enumerate(folds)]
    results = [r for r, _ in outs]
    return WalkForwardResult(
        results,
        [r for f in results for r in f.oos.daily],
        [p for f in results for p in f.oos.trade_pnls],
        sum(budget),
        [s for _, sharpes in outs for s in sharpes],
    )
