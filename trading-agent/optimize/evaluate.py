"""One deterministic engine run over a window -> metrics, aligned daily returns and per-trade P/L."""

from __future__ import annotations

import bisect
import json
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from optimize.walkforward import HOLDOUT_START
from trading_agent import engine
from trading_agent.analytics import analyze
from trading_agent.backtest_setup import build_simulated_backend
from trading_agent.config import AppConfig
from trading_agent.eventlog import EventLog
from trading_agent.execution.simulated import SimulatedBackend
from trading_agent.marketdata.base import LIVE_LOOKBACK
from trading_agent.marketdata.historical import HistoricalOandaSource
from trading_agent.risk import RiskEngine
from trading_agent.strategy import build_strategy

_REALIZED = {"EXIT", "PARTIAL_EXIT", "FINANCING"}


@dataclass
class RunResult:
    metrics: dict[str, Any]
    daily: list[float]  # one simple return per weekday in [start, end), zeros on flat days
    trade_pnls: list[float] = field(default_factory=list)  # per-trade total (partials + exit + swap)


def weekdays(start: datetime, end: datetime) -> list[date]:
    out, d = [], start.date()
    while d < end.date():
        if d.weekday() < 5:  # noqa: PLR2004 - Mon..Fri
            out.append(d)
        d += timedelta(days=1)
    return out


def daily_series(records: list[dict[str, Any]], days: list[date], starting_equity: float) -> list[float]:
    """Realized P/L bucketed onto a fixed weekday calendar, so every trial's series aligns for CSCV."""
    pnl: dict[date, float] = defaultdict(float)
    if not days:
        return []
    for r in records:
        if r.get("type") in _REALIZED and r.get("pnl") is not None:
            pnl[_attribute(datetime.fromisoformat(r["ts"]).date(), days)] += float(r["pnl"])
    equity, out = starting_equity, []
    for d in days:
        out.append(pnl.get(d, 0.0) / equity if equity > 0 else 0.0)
        equity += pnl.get(d, 0.0)
    return out


def _attribute(day: date, days: list[date]) -> date:
    """Calendar day a realization is booked on: itself if a weekday in the window, else the last earlier
    window weekday (weekend swap stays in the window even when it ends on a Monday), else the first."""
    idx = bisect.bisect_right(days, day)
    return days[idx - 1] if idx > 0 else days[0]


class Evaluator:
    """Runs `engine.run` on preloaded frames. With `allow_holdout=False` (the optimizer) frames are cut at
    `HOLDOUT_START` on construction, so no code path in a study can read holdout bars."""

    def __init__(self, frames: dict[str, pd.DataFrame], *, starting_equity: float = 10_000.0, allow_holdout: bool = False, workdir: str | Path | None = None) -> None:
        if not allow_holdout:
            frames = {tf: df[df["close_time"] <= pd.Timestamp(HOLDOUT_START)] for tf, df in frames.items()}
        self.frames = frames
        self.starting_equity = starting_equity
        self.workdir = Path(workdir or tempfile.gettempdir())
        self.allow_holdout = allow_holdout

    def run(self, cfg: AppConfig, start: datetime, end: datetime) -> RunResult:
        if not self.allow_holdout and end > HOLDOUT_START:
            from optimize.walkforward import HoldoutViolation

            msg = f"optimizer evaluation window ends {end.date()}, inside the locked holdout"
            raise HoldoutViolation(msg)
        source = HistoricalOandaSource(self.frames, entry_tf=cfg.strategy.entry_tf, stream_start=start, stream_end=end, lookback=LIVE_LOOKBACK)
        self.workdir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".jsonl", dir=self.workdir, delete=False) as fh:
            path = Path(fh.name)
        try:
            log = EventLog(path, run_id=f"opt-{start.date()}-{end.date()}", strategy_version=cfg.strategy_version)
            backend = build_simulated_backend(cfg)
            try:
                engine.run(source, build_strategy(cfg.strategy, cfg.pair), RiskEngine(cfg.risk, cfg.pair), backend, log, cfg, starting_equity=self.starting_equity)
                _close_at_boundary(backend, log)
            finally:
                log.close()
            metrics = analyze(path, starting_equity=self.starting_equity)
            records = [json.loads(line) for line in path.read_text().splitlines() if line]
        finally:
            path.unlink(missing_ok=True)
        return RunResult(metrics, daily_series(records, weekdays(start, end), self.starting_equity), _trade_pnls(records))


def _close_at_boundary(backend: SimulatedBackend, log: EventLog) -> None:
    """Each fold starts flat on a fresh backend, so positions open at the window end are closed (marked to the
    executable close of the last bar) and logged; otherwise their P/L and exposure would vanish from the fold."""
    for ev in backend.liquidate():
        log.write("EXIT", ts=ev.ts, reason=ev.exit_reason, trade_id=ev.trade_id, price=ev.price, units=ev.units, pnl=ev.pnl)


def _trade_pnls(records: list[dict[str, Any]]) -> list[float]:
    total: dict[str, float] = defaultdict(float)
    order: list[str] = []
    for r in records:
        tid = r.get("trade_id")
        if r.get("type") in _REALIZED and tid and r.get("pnl") is not None:
            if tid not in total:
                order.append(tid)
            total[tid] += float(r["pnl"])
    return [total[t] for t in order]
