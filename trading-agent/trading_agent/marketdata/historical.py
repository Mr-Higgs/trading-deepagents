"""HistoricalOandaSource: pre-fetched candles replayed for backtests.

Takes ready-made frames (so it is trivially testable without a network client); `from_oanda` is the
convenience constructor that fetches + caches the three timeframes.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pandas as pd

from trading_agent.marketdata.base import closed_only, normalize_frame, row_to_candle
from trading_agent.types import Candle


class HistoricalOandaSource:
    def __init__(self, frames: dict[str, pd.DataFrame], entry_tf: str = "M15") -> None:
        if entry_tf not in frames:
            msg = f"entry timeframe {entry_tf!r} missing from frames {sorted(frames)}"
            raise ValueError(msg)
        self.entry_tf = entry_tf
        self._frames = {tf: normalize_frame(df) for tf, df in frames.items()}

    def stream(self) -> Iterator[Candle]:
        for row in self._frames[self.entry_tf].itertuples(index=False):
            yield row_to_candle(row)

    def snapshot(self, now: datetime) -> dict[str, pd.DataFrame]:
        return {tf: closed_only(df, now) for tf, df in self._frames.items()}

    @classmethod
    def from_oanda(
        cls,
        client: object,
        instrument: str,
        timeframes: dict[str, str],
        start: datetime,
        end: datetime,
        cache_dir: str | Path | None = None,
    ) -> HistoricalOandaSource:
        """timeframes maps role->granularity, e.g. {"trend":"H4","bias":"H1","entry":"M15"}."""
        entry_tf = timeframes["entry"]
        frames = {gran: client.candles_cached(instrument, gran, start, end, cache_dir) for gran in timeframes.values()}  # type: ignore[attr-defined]
        return cls(frames, entry_tf=entry_tf)
