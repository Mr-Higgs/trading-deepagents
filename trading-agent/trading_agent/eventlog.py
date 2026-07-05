"""Append-only JSONL event log - the single source of truth for observability.

Every signal, skip, rejection, submission, fill, exit, and halt is one line with a `reason`.
`replay_today` reconstructs the day's counters (trades / realized P/L / halt) so a restart resumes
without double-counting.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from trading_agent.risk import trading_day
from trading_agent.types import DayState


def _default(o: object) -> str:
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    return str(o)


class EventLog:
    def __init__(self, path: str | Path, run_id: str, strategy_version: str) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.strategy_version = strategy_version
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a")
        self._seq = 0

    def write(self, type: str, ts: datetime, **payload: Any) -> None:  # noqa: A002 - `type` is the event-record field name
        rec = {"ts": ts.isoformat(), "seq": self._seq, "run_id": self.run_id, "strategy_version": self.strategy_version, "type": type}
        rec.update(payload)
        self._fh.write(json.dumps(rec, default=_default) + "\n")
        self._fh.flush()
        self._seq += 1

    def close(self) -> None:
        self._fh.close()

    @staticmethod
    def replay_today(path: str | Path, day: date, reset: object) -> DayState:
        """Rebuild DayState for `day` (a trading-day date) from an existing log; empty log => fresh state."""
        state = DayState(trading_day=day)
        p = Path(path)
        if not p.exists():
            return state
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            ts = datetime.fromisoformat(rec["ts"])
            if trading_day(ts, reset) != day:  # type: ignore[arg-type]
                continue
            t = rec.get("type")
            if t == "SUBMIT":
                state.trades_opened += 1
            elif t == "EXIT" and rec.get("pnl") is not None:
                state.realized_pnl += rec["pnl"]
            elif t == "HALT":
                state.halted = True
                state.halt_reason = rec.get("reason")
        return state
