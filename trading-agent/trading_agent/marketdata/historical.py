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
    def __init__(
        self, frames: dict[str, pd.DataFrame], entry_tf: str = "M15", *, stream_start: datetime | None = None, stream_end: datetime | None = None
    ) -> None:
        """`stream_start`/`stream_end` bound the decision clock while earlier bars stay visible to
        `snapshot` as indicator warm-up (walk-forward test segments start "warm", never peeking ahead)."""
        if entry_tf not in frames:
            msg = f"entry timeframe {entry_tf!r} missing from frames {sorted(frames)}"
            raise ValueError(msg)
        self.entry_tf = entry_tf
        self._frames = {tf: normalize_frame(df) for tf, df in frames.items()}
        self._stream_start = stream_start
        self._stream_end = stream_end

    def stream(self) -> Iterator[Candle]:
        df = self._frames[self.entry_tf]
        if self._stream_start is not None:
            df = df[df["open_time"] >= pd.Timestamp(self._stream_start)]
        if self._stream_end is not None:
            df = df[df["close_time"] <= pd.Timestamp(self._stream_end)]
        for row in df.itertuples(index=False):
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
        *,
        price: str = "M",
    ) -> HistoricalOandaSource:
        """timeframes maps role->granularity, e.g. {"trend":"H4","bias":"H1","entry":"M15"}.

        `price="BA"` fetches bid/ask for the ENTRY timeframe only (fills happen there); higher
        timeframes stay mid since indicators read mid prices.
        """
        entry_tf = timeframes["entry"]
        frames = {
            gran: client.candles_cached(instrument, gran, start, end, cache_dir, price=price if gran == entry_tf else "M")  # type: ignore[attr-defined]
            for gran in set(timeframes.values())
        }
        return cls(frames, entry_tf=entry_tf)
