"""CLI: walk-forward Optuna optimization of one pair/strategy; never touches the holdout.

    uv run --group optimize python -m optimize.run --pair AUD_USD --strategy breakout --trials 200
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import UTC, datetime

from optimize.data import load_frames
from optimize.evaluate import Evaluator
from optimize.report import to_payload, write_reports
from optimize.space import DEFAULT_SPACES, load_space, resolve_strategy
from optimize.study import OBJECTIVES, Settings, run_walk_forward
from optimize.walkforward import HOLDOUT_START, make_folds
from trading_agent.backtest_setup import with_news_windows
from trading_agent.config import AppConfig, load_config


def _day(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="optimize.run")
    ap.add_argument("--pair", default="AUD_USD")
    ap.add_argument("--strategy", default="breakout", help="breakout | pullback | cloud_breakout | confluence (or full registry name)")
    ap.add_argument("--trials", type=int, default=200, help="total trials, split evenly across folds")
    ap.add_argument("--start", default="2024-01-01", help="first training bar")
    ap.add_argument("--end", default=HOLDOUT_START.date().isoformat(), help="exclusive end; capped at the holdout start")
    ap.add_argument("--train-months", type=int, default=12)
    ap.add_argument("--test-months", type=int, default=3)
    ap.add_argument("--anchored", action="store_true")
    ap.add_argument("--objective", choices=OBJECTIVES, default="sharpe")
    ap.add_argument("--min-trades", type=int, default=30)
    ap.add_argument("--space", default=None, help="search-space JSON (e.g. proposed by research_loop); default per strategy")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--config", default="config")
    ap.add_argument("--cache", default="data/candles")
    ap.add_argument("--out", default="research")
    return ap


def base_config(config_dir: str, pair: str, strategy: str) -> AppConfig:
    cfg = load_config(config_dir)
    if pair not in cfg.pairs:
        msg = f"pair {pair!r} not in config/pairs.yaml"
        raise SystemExit(msg)
    cfg = replace(cfg, strategy=replace(cfg.strategy, name=strategy, instrument=pair), agents=replace(cfg.agents, mode="deterministic"))
    return with_news_windows(cfg)


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - needs OANDA data
    from trading_agent.run import _load_env

    args = build_parser().parse_args(argv)
    _load_env()
    strategy = resolve_strategy(args.strategy)
    start, end = _day(args.start), min(_day(args.end), HOLDOUT_START)
    folds = make_folds(start, end, train_months=args.train_months, test_months=args.test_months, anchored=args.anchored)
    cfg = base_config(args.config, args.pair, strategy)
    space = load_space(args.space) if args.space else DEFAULT_SPACES[strategy]
    settings = Settings(trials=args.trials, objective=args.objective, min_trades=args.min_trades, seed=args.seed)
    evaluator = Evaluator(load_frames(cfg, start, end, cache_dir=args.cache))
    result = run_walk_forward(evaluator, cfg, space, folds, settings)
    meta = {
        "pair": args.pair, "strategy": strategy, "start": args.start, "end": end.date().isoformat(), "holdout_start": HOLDOUT_START.date().isoformat(),
        "anchored": args.anchored, "train_months": args.train_months, "test_months": args.test_months,
        "price_mode": cfg.risk.execution.price_mode, "entry_tf": cfg.strategy.entry_tf, "space": args.space or "default",
    }
    md, js = write_reports(to_payload(result, meta=meta, settings=settings), args.out, now=datetime.now(UTC))
    print(md.read_text())
    print(f"wrote {md} and {js}")


if __name__ == "__main__":
    main()
