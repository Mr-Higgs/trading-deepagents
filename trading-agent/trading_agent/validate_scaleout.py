"""Compare backtest metrics with scale-out enabled vs disabled (before/after validation).

Usage (from trading-agent/, needs OANDA practice creds + cached candles):
    uv run python -m trading_agent.validate_scaleout --start 2025-01-01 --end 2026-01-01

Prints expectancy, win rate, max drawdown, and total P/L for both configs.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path

from trading_agent import engine
from trading_agent.config import ScaleOutConfig, load_config
from trading_agent.eventlog import EventLog
from trading_agent.marketdata.historical import HistoricalOandaSource
from trading_agent.risk import RiskEngine
from trading_agent.run import _load_env, _simulated_backend, _utc_day
from trading_agent.strategy import build_strategy


def _metrics(log_path: Path) -> dict[str, float]:
    records = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    trade_pnls = _trade_pnls(records)
    equity = 10_000.0
    peak = equity
    max_dd = 0.0
    for rec in records:
        if rec.get("type") in {"EXIT", "PARTIAL_EXIT"} and rec.get("pnl") is not None:
            equity += float(rec["pnl"])
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
    wins = sum(1 for p in trade_pnls if p > 0)
    losses = sum(1 for p in trade_pnls if p < 0)
    total = sum(trade_pnls)
    n = len(trade_pnls)
    return {
        "trades": float(n),
        "total_pnl": total,
        "expectancy": total / n if n else 0.0,
        "win_rate": wins / (wins + losses) if wins + losses else 0.0,
        "max_drawdown": max_dd,
        "pnl_over_dd": total / max_dd if max_dd > 0 else total,
    }


def _trade_pnls(records: list[dict]) -> list[float]:
    """Per-trade total P/L (sums partials + final exit per trade_id)."""
    by_trade: dict[str, float] = {}
    for rec in records:
        if rec.get("type") not in {"EXIT", "PARTIAL_EXIT"}:
            continue
        tid = rec.get("trade_id")
        if not tid:
            continue
        by_trade[tid] = by_trade.get(tid, 0.0) + float(rec.get("pnl") or 0.0)
        if rec.get("type") == "EXIT" and not rec.get("partial"):
            pass  # final exit included in running sum
    # Only count trades that fully closed (had a non-partial EXIT or partial with remaining 0)
    closed: set[str] = set()
    for rec in records:
        if rec.get("type") == "EXIT" and not rec.get("partial"):
            closed.add(rec["trade_id"])
        if rec.get("type") == "PARTIAL_EXIT" and rec.get("remaining_units") == 0:
            closed.add(rec["trade_id"])
    return [by_trade[t] for t in closed if t in by_trade]


def _run(label: str, cfg, source, logdir: Path, equity: float) -> dict[str, float]:
    path = logdir / f"validate-{label}.jsonl"
    log = EventLog(path, run_id=label, strategy_version=cfg.strategy_version)
    final = engine.run(source, build_strategy(cfg.strategy, cfg.pair), RiskEngine(cfg.risk, cfg.pair), _simulated_backend(cfg), log, cfg, starting_equity=equity)
    log.close()
    m = _metrics(path)
    m["final_equity"] = final
    return m


def main() -> None:
    import os

    from trading_agent.marketdata.oanda_client import OandaClient

    _load_env()  # same as run.py — langgraph dev loads .env; standalone CLIs must do it themselves

    ap = argparse.ArgumentParser(prog="trading_agent.validate_scaleout")
    ap.add_argument("--config", default="config")
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2026-01-01")
    ap.add_argument("--cache", default="data/candles")
    ap.add_argument("--logdir", default="data/logs")
    ap.add_argument("--equity", type=float, default=10_000.0)
    args = ap.parse_args()

    if not os.environ.get("OANDA_API_KEY"):
        raise SystemExit("OANDA_API_KEY not set — copy trading-agent/.env.example to .env and add your practice key")

    cfg = load_config(args.config)
    client = OandaClient.from_env()
    tfs = cfg.strategy.timeframes()
    source = HistoricalOandaSource.from_oanda(client, cfg.pair.symbol, tfs, _utc_day(args.start), _utc_day(args.end), cache_dir=args.cache)

    off = dataclasses.replace(cfg.risk.management, scale_out=ScaleOutConfig(enabled=False))
    cfg_off = dataclasses.replace(cfg, risk=dataclasses.replace(cfg.risk, management=off))

    logdir = Path(args.logdir)
    m_off = _run("scale-off", cfg_off, source, logdir, args.equity)
    m_on = _run("scale-on", cfg, source, logdir, args.equity)

    print(f"validate {args.start} -> {args.end}")
    for name, m in [("scale_out OFF", m_off), ("scale_out ON", m_on)]:
        print(
            f"  {name}: trades={m['trades']:.0f} expectancy={m['expectancy']:.2f} "
            f"win_rate={m['win_rate']:.1%} max_dd={m['max_drawdown']:.2f} "
            f"pnl={m['total_pnl']:.2f} pnl/dd={m['pnl_over_dd']:.3f} equity={m['final_equity']:.2f}"
        )


if __name__ == "__main__":
    main()
