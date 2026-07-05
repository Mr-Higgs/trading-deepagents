"""CLI entry point. `backtest` replays historical OANDA candles; `live` polls OANDA (see task 9).

    uv run python -m trading_agent.run backtest --start 2025-01-01 --end 2025-06-30
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from trading_agent import engine
from trading_agent.config import load_config
from trading_agent.eventlog import EventLog
from trading_agent.execution.simulated import SimulatedBackend
from trading_agent.marketdata.historical import HistoricalOandaSource
from trading_agent.risk import RiskEngine
from trading_agent.strategy.ichimoku_pullback import IchimokuPullback


def _utc_day(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _simulated_backend(cfg) -> SimulatedBackend:
    ex = cfg.risk.execution
    return SimulatedBackend(cfg.pair.pip_size, ex.sim_spread_pips, ex.sim_slippage_pips)


def _make_backend(cfg):
    """Choose the execution backend from config: local simulator (default) or OANDA paper account."""
    backend = cfg.risk.execution.backend
    if backend == "simulated":
        return _simulated_backend(cfg)
    if backend == "oanda":
        from trading_agent.execution.oanda_paper import OandaPaperBackend

        precision = -cfg.pair.pip_location + 1  # pip_location -4 => price precision 5
        return OandaPaperBackend.from_env(cfg.pair.symbol, price_precision=precision)
    msg = f"unknown execution.backend {backend!r} (expected 'simulated' or 'oanda')"
    raise ValueError(msg)


def cmd_backtest(args: argparse.Namespace) -> None:
    from trading_agent.marketdata.oanda_client import OandaClient  # lazy: keeps CLI parsing free of the network stack

    cfg = load_config(args.config)
    tfs = {"trend": cfg.strategy.trend_tf, "bias": cfg.strategy.bias_tf, "entry": cfg.strategy.entry_tf}
    source = HistoricalOandaSource.from_oanda(OandaClient.from_env(), cfg.pair.symbol, tfs, _utc_day(args.start), _utc_day(args.end), cache_dir=args.cache)
    run_id = f"bt-{args.start}-{args.end}"
    log_path = Path(args.logdir) / f"events-{run_id}.jsonl"
    log = EventLog(log_path, run_id=run_id, strategy_version=cfg.strategy_version)
    equity = engine.run(source, IchimokuPullback(cfg.strategy, cfg.pair), RiskEngine(cfg.risk, cfg.pair), _simulated_backend(cfg), log, cfg, starting_equity=args.equity)
    log.close()
    print(f"backtest done: final equity {equity:.2f} | log {log_path}")
    print(f"render journal: uv run python -m trading_agent.journal {log_path}")


def cmd_live(args: argparse.Namespace) -> None:
    from trading_agent.marketdata.live import LiveOandaSource
    from trading_agent.marketdata.oanda_client import OandaClient

    cfg = load_config(args.config)
    client = OandaClient.from_env()
    tfs = {"trend": cfg.strategy.trend_tf, "bias": cfg.strategy.bias_tf, "entry": cfg.strategy.entry_tf}
    source = LiveOandaSource(client, cfg.pair.symbol, tfs, poll_seconds=args.poll)
    run_id = f"live-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"  # wall clock is fine here (live, not a deterministic backtest)
    log_path = Path(args.logdir) / f"events-{run_id}.jsonl"
    log = EventLog(log_path, run_id=run_id, strategy_version=cfg.strategy_version)
    print(f"live: {cfg.pair.symbol} {cfg.strategy.entry_tf} backend={cfg.risk.execution.backend} poll={args.poll}s | log {log_path}")
    print("Ctrl-C to stop.")
    try:
        engine.run(source, IchimokuPullback(cfg.strategy, cfg.pair), RiskEngine(cfg.risk, cfg.pair), _make_backend(cfg), log, cfg, starting_equity=args.equity)
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        log.close()


def main() -> None:
    p = argparse.ArgumentParser(prog="trading_agent.run")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backtest", help="replay historical OANDA candles")
    b.add_argument("--config", default="config")
    b.add_argument("--start", required=True, help="inclusive UTC date, e.g. 2025-01-01")
    b.add_argument("--end", required=True, help="exclusive UTC date")
    b.add_argument("--cache", default="data/candles")
    b.add_argument("--logdir", default="data/logs")
    b.add_argument("--equity", type=float, default=10_000.0)
    b.set_defaults(func=cmd_backtest)

    live = sub.add_parser("live", help="poll OANDA and paper-trade (simulated fills by default)")
    live.add_argument("--config", default="config")
    live.add_argument("--logdir", default="data/logs")
    live.add_argument("--poll", type=int, default=60, help="seconds between polls")
    live.add_argument("--equity", type=float, default=10_000.0)
    live.set_defaults(func=cmd_live)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
