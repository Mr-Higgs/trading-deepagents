"""News blackout after-window sweep: how long to stay out AFTER a high-impact release.

The pre-release blackout is held fixed (30 min); only the post-release half of `impact_minutes[High]`
varies. Per-event row overrides in the calendar (FOMC 30/90, RBA 30/60) are kept in every variant, so the
sweep answers "how soon after a scheduled data release may the deterministic strategy re-enter?".

Besides whole-run metrics, each row isolates the POST-RELEASE trades (submitted within 60 min after a
High event for either currency of the pair) because the aggregate delta between variants is small and
those trades are the only thing that actually changes.

    uv run python -m trading_agent.news_compare --start 2025-09-03 --end 2026-07-01 --split 2026-03-01
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from trading_agent import engine
from trading_agent.analytics import _trade_pnls_and_closed, analyze
from trading_agent.backtest_setup import auto_news_windows, build_simulated_backend, load_fx_converter
from trading_agent.config import AppConfig, load_config
from trading_agent.eventlog import EventLog
from trading_agent.fx_convert import FxConverter
from trading_agent.journal import _build_trades, _load
from trading_agent.marketdata.historical import HistoricalOandaSource
from trading_agent.news import load_calendar, pair_currencies
from trading_agent.risk import RiskEngine
from trading_agent.strategy import build_strategy

# name -> High-impact (before, after) minutes; None = news_auto disabled (upper bound on exposure)
VARIANTS: dict[str, tuple[int, int] | None] = {
    "none": None,
    "after_0": (30, 0),
    "after_15": (30, 15),
    "after_30": (30, 30),
    "after_60": (30, 60),
}
POST_RELEASE_MINUTES = 60
_COLS = ("trades", "expectancy", "profit_factor", "win_rate", "total_pnl", "max_drawdown", "pnl_over_dd")
_POST_COLS = ("post_n", "post_pnl", "post_win_rate", "post_avg_R")


def with_variant(cfg: AppConfig, variant: tuple[int, int] | None, *, root: Path | None = None) -> AppConfig:
    """Rebuild the auto windows for one variant onto the manual `news_windows` from config."""
    na = cfg.risk.news_auto
    if variant is None:
        return replace(cfg, risk=replace(cfg.risk, news_auto=replace(na, enabled=False)))
    cfg_v = replace(cfg, risk=replace(cfg.risk, news_auto=replace(na, impact_minutes={**na.impact_minutes, "High": variant})))
    auto = auto_news_windows(cfg_v, root=root)
    return replace(cfg_v, risk=replace(cfg_v.risk, news_windows=(*cfg.risk.news_windows, *auto)))


def high_event_times(cal: pd.DataFrame, instrument: str) -> list[datetime]:
    ccys = set(pair_currencies(instrument))
    rows = cal[cal["currency"].str.upper().isin(ccys) & (cal["impact"].str.lower() == "high")]
    return [pd.Timestamp(t).to_pydatetime().astimezone(UTC) for t in rows["time"]]


def post_release_stats(log_path: Path, events: list[datetime], minutes: int = POST_RELEASE_MINUTES) -> dict[str, Any]:
    """Trades submitted in (event, event + minutes] for any High event: count, pnl, win rate, avg R."""
    records = _load(log_path)
    meta = _build_trades(records)
    pnls, closed = _trade_pnls_and_closed(records)
    span = timedelta(minutes=minutes)
    picked = []
    for tid in closed:
        ts = meta.get(tid, {}).get("submit_ts")
        if ts is None:
            continue
        t = datetime.fromisoformat(ts).astimezone(UTC)
        if any(e < t <= e + span for e in events):
            picked.append((pnls.get(tid, 0.0), meta[tid].get("risk_amount")))
    n = len(picked)
    wins = sum(1 for p, _ in picked if p > 0)
    rs = [p / r for p, r in picked if r]
    return {"post_n": n, "post_pnl": sum(p for p, _ in picked), "post_win_rate": wins / n if n else 0.0, "post_avg_R": sum(rs) / len(rs) if rs else 0.0}


def run_one(cfg: AppConfig, source: HistoricalOandaSource, log_path: Path, equity: float, fx: FxConverter | None = None) -> dict[str, Any]:
    """One backtest on a fresh log (EventLog appends and the engine replays, so stale logs would leak in)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.unlink(missing_ok=True)
    log = EventLog(log_path, run_id=log_path.stem, strategy_version=cfg.strategy_version)
    try:
        engine.run(source, build_strategy(cfg.strategy, cfg.pair), RiskEngine(cfg.risk, cfg.pair, fx=fx), build_simulated_backend(cfg, fx), log, cfg, starting_equity=equity, liquidate_at_end=True)
    finally:
        log.close()
    return analyze(log_path, starting_equity=equity)


def to_markdown(rows: list[dict[str, Any]]) -> str:
    cols = (*_COLS, *_POST_COLS)
    head = "| period | variant | " + " | ".join(cols) + " |\n|---|---|" + "--:|" * len(cols)
    lines = [head]
    for r in rows:
        cells = [f"{r[c]:.3f}" if isinstance(r[c], float) else str(r[c]) for c in cols]
        lines.append(f"| {r['period']} | {r['variant']} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:  # pragma: no cover - needs OANDA data
    from trading_agent.marketdata.oanda_client import OandaClient

    ap = argparse.ArgumentParser(prog="trading_agent.news_compare")
    ap.add_argument("--config", default="config")
    ap.add_argument("--start", default="2025-09-03")
    ap.add_argument("--end", default="2026-07-01", help="exclusive")
    ap.add_argument("--split", default="2026-03-01", help="IS = [start, split), OOS = [split, end)")
    ap.add_argument("--price", default="BA", help="candle price component: BA (real bid/ask) | M (mid; pair with --price-mode mid)")
    ap.add_argument("--price-mode", default=None, help="override risk.execution.price_mode (bidask|mid|legacy)")
    ap.add_argument("--cache", default="data/candles")
    ap.add_argument("--out", default="research/news_window_compare.md")
    ap.add_argument("--equity", type=float, default=10_000.0)
    args = ap.parse_args()
    base = load_config(args.config)
    if args.price_mode:
        base = replace(base, risk=replace(base.risk, execution=replace(base.risk.execution, price_mode=args.price_mode)))
    root = base.config_dir.parent
    events = high_event_times(load_calendar(root / base.risk.news_auto.calendar), base.pair.symbol)
    client = OandaClient.from_env()
    tfs = base.strategy.timeframes()
    periods = {"IS": (args.start, args.split), "OOS": (args.split, args.end)}
    rows = []
    for period, (s, e) in periods.items():
        start, end = (datetime.fromisoformat(d).replace(tzinfo=UTC) for d in (s, e))
        src = HistoricalOandaSource.from_oanda(client, base.pair.symbol, tfs, start, end, cache_dir=args.cache, price=args.price)
        fx = load_fx_converter(base, client, start, end, cache_dir=args.cache)
        for name, variant in VARIANTS.items():
            cfg = with_variant(base, variant, root=root)
            log_path = Path("data/logs") / f"events-news-{period}-{name}.jsonl"
            m = run_one(cfg, src, log_path, args.equity, fx)
            rows.append({"period": period, "variant": name, **{c: m.get(c, 0.0) for c in _COLS}, **post_release_stats(log_path, events)})
    md = to_markdown(rows)
    header = f"# News after-window sweep ({args.start} .. {args.end}, split {args.split}, price {args.price}, strategy {base.strategy.name})\n\n"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(header + md)
    print(md)


if __name__ == "__main__":
    main()
