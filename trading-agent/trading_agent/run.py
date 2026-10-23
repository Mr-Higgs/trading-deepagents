"""CLI entry point. `backtest` replays historical OANDA candles; `live` polls OANDA (see task 9).

    uv run python -m trading_agent.run backtest --start 2025-01-01 --end 2025-06-30
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from trading_agent import engine
from trading_agent.backtest_setup import build_simulated_backend, with_news_windows
from trading_agent.config import load_config
from trading_agent.eventlog import EventLog
from trading_agent.execution.simulated import SimulatedBackend
from trading_agent.marketdata.historical import HistoricalOandaSource
from trading_agent.risk import RiskEngine
from trading_agent.strategy import build_strategy


def _utc_day(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _simulated_backend(cfg) -> SimulatedBackend:
    return build_simulated_backend(cfg)


def _make_backend(cfg, override: str | None = None):
    """Choose the execution backend: local simulator (default) or OANDA paper account.

    `override` ("simulated"|"oanda") forces the choice regardless of `cfg.risk.execution.backend` — used
    by the dashboard's `trade_loop` graph to pick real demo orders per run without flipping global config.
    """
    backend = override or cfg.risk.execution.backend
    if backend == "simulated":
        return _simulated_backend(cfg)
    if backend == "oanda":
        from trading_agent.execution.oanda_paper import OandaPaperBackend

        precision = -cfg.pair.pip_location + 1  # pip_location -4 => price precision 5
        return OandaPaperBackend.from_env(cfg.pair.symbol, price_precision=precision)
    msg = f"unknown execution.backend {backend!r} (expected 'simulated' or 'oanda')"
    raise ValueError(msg)


def _load_env(path: Path = Path(".env")) -> None:
    """Minimal .env loader so the CLI sees OANDA/ANTHROPIC keys.

    `langgraph dev` loads .env itself (per langgraph.json); the CLI doesn't, so do it here. Real
    exported env vars win (setdefault). ponytail: stdlib parse, no python-dotenv dep for the lean path.
    """
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def _make_strategy(cfg, choice: str, *, refresh: bool = False, use_cache: bool = True, point_in_time: bool = False):
    """Deterministic IchimokuPullback, or (agent) wrap it in the deepagents decision layer.

    `use_cache` gates the verdict cache (subject to `cfg.agents.cache`): on for backtests so re-runs are
    deterministic/free, off for live so decisions are always fresh. `refresh` forces fresh calls (still writes).
    """
    inner = build_strategy(cfg.strategy, cfg.pair)
    mode = cfg.agents.mode if choice == "config" else choice
    if mode != "agent":
        return inner
    # lazy: only the agent path pulls in the deepagents/langchain stack (uv sync --group phase2)
    from trading_agent.strategy.agent_strategy import AgentStrategy
    from trading_agent.strategy.agents.orchestrator import build_orchestrator
    from trading_agent.strategy.agents.verdict_cache import VerdictCache

    cache = VerdictCache(enabled=True, refresh=refresh) if (use_cache and cfg.agents.cache) else None
    return AgentStrategy(
        inner, build_orchestrator(cfg), cfg.pair,
        authority=cfg.agents.authority, cache=cache, orchestrator_model=cfg.agents.orchestrator_model,
        point_in_time=point_in_time,
    )


def cmd_backtest(args: argparse.Namespace) -> None:
    from trading_agent.marketdata.oanda_client import OandaClient  # lazy: keeps CLI parsing free of the network stack

    cfg = with_news_windows(load_config(args.config))
    tfs = {"trend": cfg.strategy.trend_tf, "bias": cfg.strategy.bias_tf, "entry": cfg.strategy.entry_tf}
    price = "BA" if cfg.risk.execution.price_mode == "bidask" else "M"
    source = HistoricalOandaSource.from_oanda(OandaClient.from_env(), cfg.pair.symbol, tfs, _utc_day(args.start), _utc_day(args.end), cache_dir=args.cache, price=price)
    strategy = _make_strategy(cfg, args.strategy, refresh=args.refresh, point_in_time=True)  # agent mode is cached => re-runs stay deterministic
    run_id = f"bt-{args.start}-{args.end}"
    log_path = Path(args.logdir) / f"events-{run_id}.jsonl"
    log = EventLog(log_path, run_id=run_id, strategy_version=cfg.strategy_version)
    equity = engine.run(source, strategy, RiskEngine(cfg.risk, cfg.pair), _simulated_backend(cfg), log, cfg, starting_equity=args.equity)
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
    strategy = _make_strategy(cfg, args.strategy, use_cache=False)  # live decisions are always fresh, never replayed
    run_id = f"live-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"  # wall clock is fine here (live, not a deterministic backtest)
    log_path = Path(args.logdir) / f"events-{run_id}.jsonl"
    log = EventLog(log_path, run_id=run_id, strategy_version=cfg.strategy_version)
    print(f"live: {cfg.pair.symbol} {cfg.strategy.entry_tf} strategy={strategy.version} backend={cfg.risk.execution.backend} poll={args.poll}s | log {log_path}")
    print("Ctrl-C to stop.")
    try:
        engine.run(source, strategy, RiskEngine(cfg.risk, cfg.pair), _make_backend(cfg), log, cfg, starting_equity=args.equity)
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        log.close()


def main() -> None:
    _load_env()  # load ./.env (run from the trading-agent dir) so OANDA/ANTHROPIC keys are visible
    p = argparse.ArgumentParser(prog="trading_agent.run")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backtest", help="replay historical OANDA candles")
    b.add_argument("--config", default="config")
    b.add_argument("--start", required=True, help="inclusive UTC date, e.g. 2025-01-01")
    b.add_argument("--end", required=True, help="exclusive UTC date")
    b.add_argument("--cache", default="data/candles")
    b.add_argument("--logdir", default="data/logs")
    b.add_argument("--equity", type=float, default=10_000.0)
    b.add_argument("--strategy", choices=["config", "deterministic", "agent"], default="deterministic", help="'agent' runs the LLM decision layer; 'config' uses agents.yaml mode")
    b.add_argument("--refresh", action="store_true", help="agent mode: ignore cached verdicts and call the LLM fresh (still writes)")
    b.set_defaults(func=cmd_backtest)

    live = sub.add_parser("live", help="poll OANDA and paper-trade (simulated fills by default)")
    live.add_argument("--config", default="config")
    live.add_argument("--logdir", default="data/logs")
    live.add_argument("--poll", type=int, default=60, help="seconds between polls")
    live.add_argument("--equity", type=float, default=10_000.0)
    live.add_argument("--strategy", choices=["config", "deterministic", "agent"], default="config", help="'config' uses agents.yaml mode")
    live.set_defaults(func=cmd_live)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
