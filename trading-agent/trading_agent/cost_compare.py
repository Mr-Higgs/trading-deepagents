"""Old vs new cost model on the same history: the PR 1 before/after table.

Runs each strategy twice over [start, end): `legacy` (flat spread + slippage at entry, mid exits) and
`bidask` (side-correct fills, hour-of-week spread, stop slippage, financing). Requires OANDA practice
credentials (or a warm `data/candles` cache with `_BA` files).

    uv run python -m trading_agent.cost_compare --start 2025-01-01 --end 2026-01-01 \\
        --strategies ichimoku_breakout ichimoku_pullback
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trading_agent import engine
from trading_agent.analytics import analyze
from trading_agent.backtest_setup import build_simulated_backend, load_fx_converter, with_news_windows
from trading_agent.config import AppConfig, load_config
from trading_agent.eventlog import EventLog
from trading_agent.fx_convert import FxConverter
from trading_agent.marketdata.historical import HistoricalOandaSource
from trading_agent.risk import RiskEngine
from trading_agent.strategy import build_strategy

COST_MODES: dict[str, dict[str, Any]] = {
    "old": {"price_mode": "legacy", "financing": False},
    "new": {"price_mode": "bidask", "financing": True},
}
_COLS = ("trades", "expectancy", "profit_factor", "win_rate", "sharpe", "max_drawdown_pct", "total_pnl", "financing_pnl")


def with_cost_mode(cfg: AppConfig, mode: str, *, spread_model: str | None = None) -> AppConfig:
    kw = dict(COST_MODES[mode])
    if spread_model and mode == "new":
        kw["spread_model"] = spread_model
    return replace(cfg, risk=replace(cfg.risk, execution=replace(cfg.risk.execution, **kw)))


def run_one(cfg: AppConfig, source: HistoricalOandaSource, log_path: Path, equity: float, fx: FxConverter | None = None) -> dict[str, Any]:
    """One backtest on a fresh log (EventLog appends and the engine replays, so stale logs would leak in)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.unlink(missing_ok=True)
    log = EventLog(log_path, run_id=log_path.stem, strategy_version=cfg.strategy_version)
    try:
        engine.run(source, build_strategy(cfg.strategy, cfg.pair), RiskEngine(cfg.risk, cfg.pair, fx=fx), build_simulated_backend(cfg, fx), log, cfg, starting_equity=equity)
    finally:
        log.close()
    return analyze(log_path, starting_equity=equity)


def to_markdown(rows: list[dict[str, Any]]) -> str:
    head = "| strategy | cost model | " + " | ".join(_COLS) + " |\n|---|---|" + "--:|" * len(_COLS)
    lines = [head]
    for r in rows:
        cells = [f"{r[c]:.3f}" if isinstance(r[c], float) else str(r[c]) for c in _COLS]
        lines.append(f"| {r['strategy']} | {r['mode']} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:  # pragma: no cover - needs OANDA data
    from trading_agent.marketdata.oanda_client import OandaClient

    ap = argparse.ArgumentParser(prog="trading_agent.cost_compare")
    ap.add_argument("--config", default="config")
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2026-01-01")
    ap.add_argument("--strategies", nargs="+", default=["ichimoku_breakout", "ichimoku_pullback"])
    ap.add_argument("--spread-model", default=None, help="override new-mode spread model (flat|profile|fitted)")
    ap.add_argument("--cache", default="data/candles")
    ap.add_argument("--out", default="research/cost_compare_2025.md")
    ap.add_argument("--equity", type=float, default=10_000.0)
    args = ap.parse_args()
    start, end = (datetime.fromisoformat(d).replace(tzinfo=timezone.utc) for d in (args.start, args.end))
    base = with_news_windows(load_config(args.config))
    client = OandaClient.from_env()
    rows = []
    for name in args.strategies:
        cfg0 = replace(base, strategy=replace(base.strategy, name=name))
        tfs = {"trend": cfg0.strategy.trend_tf, "bias": cfg0.strategy.bias_tf, "entry": cfg0.strategy.entry_tf}
        src = HistoricalOandaSource.from_oanda(client, cfg0.pair.symbol, tfs, start, end, cache_dir=args.cache, price="BA")
        fx = load_fx_converter(cfg0, client, start, end, cache_dir=args.cache)
        for mode in COST_MODES:
            cfg = with_cost_mode(cfg0, mode, spread_model=args.spread_model)
            m = run_one(cfg, src, Path("data/logs") / f"events-cost-{name}-{mode}.jsonl", args.equity, fx)
            rows.append({"strategy": name, "mode": mode, **{c: m.get(c, 0.0) for c in _COLS}})
    md = to_markdown(rows)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(f"# Cost model before/after ({args.start} .. {args.end})\n\n" + md)
    print(md)


if __name__ == "__main__":
    main()
