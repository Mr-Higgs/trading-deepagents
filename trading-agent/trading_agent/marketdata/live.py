"""LiveOandaSource: poll OANDA and yield each M15 bar once, as it closes.

Feeds the SAME engine loop as the backtest - the only difference is that `stream()` blocks polling
for the next completed candle instead of iterating a fixed history.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterator
from datetime import datetime

import pandas as pd

from trading_agent.marketdata.base import LIVE_LOOKBACK, row_to_candle
from trading_agent.types import Candle


class LiveOandaSource:
    def __init__(self, client: object, instrument: str, timeframes: dict[str, str], poll_seconds: int = 60, lookback: int = LIVE_LOOKBACK, *, on_poll: Callable[[], object] | None = None) -> None:
        self._client = client
        self._instrument = instrument
        self._timeframes = timeframes  # role -> granularity
        self.entry_tf = timeframes["entry"]
        self._poll = poll_seconds
        self._lookback = lookback
        self._on_poll = on_poll  # hooks like the news-calendar refresh ride the poll cadence so they need no thread
        self._seen: set[datetime] = set()  # ponytail: unbounded; ~96 M15/day, bound it for multi-week runs

    def _emit_new(self, df: pd.DataFrame) -> list[Candle]:
        """Return candles not yet yielded, marking them seen (dedupe by close_time)."""
        out = []
        for row in df.itertuples(index=False):
            if row.close_time in self._seen:
                continue
            self._seen.add(row.close_time)
            out.append(row_to_candle(row))
        return out

    def stream(self) -> Iterator[Candle]:
        while True:
            if self._on_poll:
                self._on_poll()
            df = self._client.recent_candles(self._instrument, self.entry_tf, self._lookback)  # type: ignore[attr-defined]
            new = self._emit_new(df)
            yield from new
            if not new:
                print(f"[live] polled {self._instrument} {self.entry_tf}: no new bar", file=sys.stderr)
            time.sleep(self._poll)

    def snapshot(self, now: datetime) -> dict[str, pd.DataFrame]:
        out = {}
        for gran in set(self._timeframes.values()):
            df = self._client.recent_candles(self._instrument, gran, self._lookback)  # type: ignore[attr-defined]
            out[gran] = df[df["close_time"] <= now]
        return out
