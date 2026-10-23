"""optimize/: folds, holdout lock, search space, overfitting stats, plateau pick, reports.

Every series/frame below is a synthetic TEST FIXTURE (seeded noise or hand-built), not market data."""

from __future__ import annotations

import json
import math
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from optimize import holdout as H
from optimize.evaluate import Evaluator, RunResult, daily_series, weekdays
from optimize.report import to_markdown, to_payload
from optimize.space import DEFAULT_SPACES, FIXED_RISK_FIELDS, Param, apply_params, resolve_strategy, sample, space_from_json, space_to_json
from optimize.stats import bootstrap_ci, deflated_sharpe, expected_max_sharpe, neighborhood_scores, pbo_cscv, per_period_sharpe, probabilistic_sharpe
from optimize.study import Settings, plateau_pick, run_walk_forward, score
from optimize.walkforward import HOLDOUT_START, HoldoutViolation, add_months, make_folds
from trading_agent.config import load_config
from trading_agent.marketdata.base import FRAME_COLUMNS, normalize_frame

D = lambda s: datetime.fromisoformat(s).replace(tzinfo=UTC)  # noqa: E731


# ---------------------------------------------------------------- walk-forward + holdout
def test_rolling_folds_tile_test_windows() -> None:
    folds = make_folds(D("2024-01-01"), D("2026-07-01"), train_months=12, test_months=3)
    assert len(folds) == 6
    assert folds[0].train_start == D("2024-01-01") and folds[0].test_start == D("2025-01-01")
    assert folds[1].train_start == D("2024-04-01")  # rolling
    assert all(a.test_end == b.test_start for a, b in zip(folds, folds[1:], strict=False))
    assert folds[-1].test_end == HOLDOUT_START


def test_anchored_folds_keep_train_start() -> None:
    folds = make_folds(D("2024-01-01"), D("2026-01-01"), anchored=True)
    assert {f.train_start for f in folds} == {D("2024-01-01")}
    assert folds[-1].train_end - folds[0].train_end > timedelta(days=180)


def test_folds_refuse_holdout() -> None:
    with pytest.raises(HoldoutViolation):
        make_folds(D("2024-01-01"), D("2026-08-01"))
    with pytest.raises(ValueError, match="no complete"):
        make_folds(D("2025-01-01"), D("2025-06-01"))


def test_add_months_wraps_year() -> None:
    assert add_months(D("2025-11-01"), 3) == D("2026-02-01")


# ---------------------------------------------------------------- search space
def test_apply_params_leaves_risk_fixed() -> None:
    cfg = load_config("config")
    out = apply_params(cfg, {"rr": 3.0, "breakeven_trigger_r": 0.75, "trail_mode": "pips", "partial_exit_pct": 0.25})
    assert out.strategy.rr == 3.0 and out.risk.management.trail_mode == "pips"
    assert out.risk.management.scale_out.legs[0].fraction == 0.25
    for f in FIXED_RISK_FIELDS:
        assert getattr(out.risk, f) == getattr(cfg.risk, f)
    assert not apply_params(cfg, {"partial_exit_pct": 0.0}).risk.management.scale_out.enabled


def test_risk_fields_are_not_tunable() -> None:
    with pytest.raises(ValueError, match="not tunable"):
        Param("risk_pct", "float", 0.1, 2.0)
    with pytest.raises(ValueError, match="untunable"):
        apply_params(load_config("config"), {"max_daily_loss_pct": 9.0})


def test_space_json_roundtrip_and_sampling() -> None:
    import optuna

    space = DEFAULT_SPACES[resolve_strategy("breakout")]
    assert space_from_json(space_to_json(space)) == space
    names = {p.name for p in space}
    assert {"breakeven_trigger_r", "trail_mode", "partial_exit_pct", "rr"} <= names
    study = optuna.create_study(sampler=optuna.samplers.RandomSampler(seed=1))
    params = sample(study.ask(), space)
    assert set(params) == names


# ---------------------------------------------------------------- stats (seeded synthetic returns)
def _noise(n: int, seed: int, mu: float = 0.0) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(mu, 0.01) for _ in range(n)]


def test_psr_and_dsr() -> None:
    good, flat = _noise(500, 1, 0.002), _noise(500, 2, 0.0)
    assert probabilistic_sharpe(good) > 0.95 > probabilistic_sharpe(flat)
    assert expected_max_sharpe(1000, 0.01) > expected_max_sharpe(10, 0.01) > 0
    trial_srs = [per_period_sharpe(_noise(500, s)) for s in range(50)]
    assert deflated_sharpe(good, n_trials=1000, trial_sharpes=trial_srs) < probabilistic_sharpe(good)


def test_pbo_noise_vs_real_edge() -> None:
    noise = [_noise(320, s) for s in range(20)]
    assert 0.25 < pbo_cscv(noise, n_splits=8) < 0.75  # pure noise: IS winner is a coin flip OOS
    edge = [*noise[:19], _noise(320, 99, 0.004)]
    assert pbo_cscv(edge, n_splits=8) < 0.1  # a persistent edge wins OOS too


def test_bootstrap_ci_deterministic_and_brackets_mean() -> None:
    xs = _noise(200, 3, 0.001)
    lo, hi = bootstrap_ci(xs, lambda v: sum(v) / len(v), seed=7)
    assert lo < sum(xs) / len(xs) < hi
    assert (lo, hi) == bootstrap_ci(xs, lambda v: sum(v) / len(v), seed=7)


def test_plateau_beats_isolated_spike() -> None:
    space = (Param("rr", "float", 1.0, 4.0),)
    trials = [({"rr": 1.0}, 9.0)] + [({"rr": 3.0 + d}, 5.0) for d in (-0.1, 0.0, 0.1, 0.2)] + [({"rr": 2.0}, 0.0)]
    params, own, plateau = plateau_pick(space, trials, radius=0.1, min_neighbors=3)
    assert 2.8 < params["rr"] < 3.3 and own == 5.0 and plateau == 5.0
    assert neighborhood_scores([((0.0,), (), 9.0)], min_neighbors=1) == [9.0]


def test_score_penalizes_thin_samples() -> None:
    res = lambda n, d: RunResult({"trades": n, "expectancy": 10.0}, d)  # noqa: E731
    daily = _noise(250, 4, 0.002)
    assert score(res(60, daily), "sharpe", 30) > score(res(10, daily), "sharpe", 30) > 0
    assert score(res(0, daily), "sharpe", 30) < -1000
    assert score(res(60, daily), "expectancy", 30) == 10.0


def test_daily_series_aligns_to_weekday_calendar() -> None:
    days = weekdays(D("2025-01-06"), D("2025-01-13"))  # Mon..Fri
    recs = [{"type": "EXIT", "ts": "2025-01-07T10:00:00+00:00", "pnl": 100.0}, {"type": "FINANCING", "ts": "2025-01-11T21:00:00+00:00", "pnl": -1.0}]
    out = daily_series(recs, days, 10_000.0)
    assert len(out) == 5 and out[1] == pytest.approx(0.01) and out[0] == 0.0


# ---------------------------------------------------------------- holdout one-shot log
def test_holdout_claim_logs_and_refuses_reuse(tmp_path) -> None:
    log = tmp_path / "holdout_log.jsonl"
    digest = H.config_hash("AUD_USD", "ichimoku_breakout", {"rr": 2.0})
    assert digest == H.config_hash("AUD_USD", "ichimoku_breakout", {"rr": 2.0})
    H.claim(log, digest, {"pair": "AUD_USD"})
    with pytest.raises(H.HoldoutAlreadyUsed):
        H.claim(log, digest, {"pair": "AUD_USD"})
    rec = H.claim(log, digest, {"pair": "AUD_USD"}, allow_reuse=True)
    assert rec["reuse"] is True and len(log.read_text().splitlines()) == 2


# ---------------------------------------------------------------- engine integration (synthetic fixture frames)
def _frame(tf: str, step: timedelta, start: datetime, end: datetime, seed: int) -> pd.DataFrame:
    rng, rows, px, t, i = random.Random(seed), [], 0.65, start, 0
    while t < end:
        drift = 0.0004 * math.sin(i / 40)
        o, c = px, px + drift + rng.gauss(0, 0.0006)
        rows.append(("AUD_USD", tf, t, t + step, o, max(o, c) + 0.0003, min(o, c) - 0.0003, c, 100, True))
        px, t, i = c, t + step, i + 1
    return normalize_frame(pd.DataFrame(rows, columns=FRAME_COLUMNS))


def _synthetic_setup():
    cfg = load_config("config")
    cfg = replace(cfg, strategy=replace(cfg.strategy, name="ichimoku_breakout", trend_tf="D", bias_tf="H4", entry_tf="H1"))
    start, end = D("2026-01-01"), D("2026-08-01")  # deliberately runs past the holdout start
    frames = {tf: _frame(tf, step, start - timedelta(days=90), end, n) for n, (tf, step) in enumerate((("D", timedelta(days=1)), ("H4", timedelta(hours=4)), ("H1", timedelta(hours=1))))}
    return cfg, frames


def test_evaluator_cuts_holdout_bars_and_refuses_holdout_windows() -> None:
    cfg, frames = _synthetic_setup()
    ev = Evaluator(frames)
    assert all(df["close_time"].max() <= pd.Timestamp(HOLDOUT_START) for df in ev.frames.values())
    with pytest.raises(HoldoutViolation):
        ev.run(cfg, D("2026-06-01"), D("2026-07-15"))


def test_walk_forward_end_to_end_on_fixture(tmp_path) -> None:
    cfg, frames = _synthetic_setup()
    folds = make_folds(D("2026-03-01"), D("2026-06-01"), train_months=1, test_months=1)
    space = (Param("rr", "float", 1.5, 3.0, 0.5), Param("partial_exit_pct", "cat", choices=(0.0, 0.5)))
    settings = Settings(trials=4, min_trades=1, pbo_splits=2)
    res = run_walk_forward(Evaluator(frames, workdir=tmp_path), cfg, space, folds, settings)
    assert len(res.folds) == 2 and res.n_trials == 4
    assert len(res.oos_daily) == sum(len(weekdays(f.test_start, f.test_end)) for f in folds)
    payload = to_payload(res, meta={"pair": "AUD_USD", "strategy": "ichimoku_breakout", "start": "2026-03-01", "end": "2026-06-01", "holdout_start": "2026-07-01", "anchored": False, "train_months": 1, "test_months": 1}, settings=settings)
    assert json.loads(json.dumps(payload, default=str))["summary"]["n_trials"] == 4
    assert "Concatenated out-of-sample" in to_markdown(payload)


# ---------------------------------------------------------------- review fixes
def test_fold_budgets_spend_exact_trial_count() -> None:
    from optimize.study import fold_budgets

    assert fold_budgets(200, 6) == [34, 34, 33, 33, 33, 33]
    assert sum(fold_budgets(7, 3)) == 7 and fold_budgets(2, 3) == [1, 1, 1]


def test_trailing_partial_test_window_is_scored() -> None:
    folds = make_folds(D("2024-01-01"), D("2025-05-15"), train_months=12, test_months=3)
    assert folds[-1].test_start == D("2025-04-01") and folds[-1].test_end == D("2025-05-15")


def test_pbo_undefined_when_all_trials_tie() -> None:
    flat = [[0.001 * ((i % 3) - 1) for i in range(64)]] * 5
    assert math.isnan(pbo_cscv(flat, n_splits=8))


def test_weekend_financing_stays_in_window_ending_monday() -> None:
    days = weekdays(D("2025-01-06"), D("2025-01-13"))  # window ends (exclusive) on Monday 13th
    recs = [{"type": "FINANCING", "ts": "2025-01-11T21:00:00+00:00", "pnl": -10.0}]
    out = daily_series(recs, days, 10_000.0)
    assert out[-1] == pytest.approx(-0.001)  # booked on Friday, not dropped


def test_breakeven_pip_trigger_can_be_disabled_in_default_space() -> None:
    names = {p.name: p for p in DEFAULT_SPACES["ichimoku_breakout"]}
    assert 0.0 in names["breakeven_trigger_pips"].choices
