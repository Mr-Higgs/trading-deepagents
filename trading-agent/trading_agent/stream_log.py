"""StreamingEventLog: an EventLog that also pushes each record to the LangGraph custom stream.

The trading engine (`engine.run`) funnels every event through `EventLog.write`, so this subclass is
the whole seam for streaming the loop to the dashboard - no change to `engine.py`. It still writes the
JSONL file (source of truth + `replay_today` restart-safety), then mirrors the same record onto the
`custom` stream via `get_stream_writer()`. Outside a graph run the writer is unavailable, so it degrades
to a plain EventLog - the CLI can use this class unchanged.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from trading_agent.eventlog import EventLog


class StreamingEventLog(EventLog):
    def write(self, type: str, ts: datetime, **payload: Any) -> None:  # noqa: A002 - `type` is the event-record field name
        seq = self._seq  # the seq the parent is about to stamp on this record
        super().write(type, ts, **payload)
        try:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()  # raises / returns a no-op outside a LangGraph run
        except Exception:  # noqa: BLE001 - no graph context (plain CLI); nothing to stream
            return
        writer(
            {
                "ts": ts.isoformat(),
                "seq": seq,
                "run_id": self.run_id,
                "strategy_version": self.strategy_version,
                "type": type,
                **payload,
            }
        )
