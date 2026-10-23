"""MarketDataSource protocol + lookahead-safe alignment helpers.

Backtest and live differ ONLY in which MarketDataSource they use. Every helper here enforces the
one invariant that keeps a backtest honest: **a decision at `now` may only see candles that had
already CLOSED by `now`** (`close_time <= now`). The still-forming higher-timeframe bar is excluded.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Protocol

import pandas as pd

from trading_agent.types import Candle

FRAME_COLUMNS = ["instrument", "tf", "open_time", "close_time", "o", "h", "l", "c", "volume", "complete"]
# Optional bid/ask OHLC columns (present when candles were fetched with price="MBA"/"BA").
BA_COLUMNS = ["bo", "bh", "bl", "bc", "ao", "ah", "al", "ac"]

# OANDA candle granularities the system can fetch (codes are OANDA's own). Single source of truth:
# oanda_client builds its request/delta map off this, config.py validates the triple-screen against it.
GRANULARITY_DELTAS = {
    "M1": timedelta(minutes=1),
    "M5": timedelta(minutes=5),
    "M15": timedelta(minutes=15),
    "H1": timedelta(hours=1),
    "H4": timedelta(hours=4),
    "D": timedelta(days=1),
}
SUPPORTED_GRANULARITIES = frozenset(GRANULARITY_DELTAS)


class MarketDataSource(Protocol):
    entry_tf: str

    def stream(self) -> Iterator[Candle]:
        """Yield entry-timeframe CLOSED candles in ascending time order (the decision clock)."""
        ...

    def snapshot(self, now: datetime) -> dict[str, pd.DataFrame]:
        """Return {tf: frame} where each frame holds only candles closed at/before `now`."""
        ...


def normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Keep complete candles only, coerce times to tz-aware UTC, sort ascending by close_time."""
    out = df[df["complete"].astype(bool)].copy()
    out["open_time"] = pd.to_datetime(out["open_time"], utc=True)
    out["close_time"] = pd.to_datetime(out["close_time"], utc=True)
    return out.sort_values("close_time").reset_index(drop=True)


def candles_to_frame(candles: list[Candle]) -> pd.DataFrame:
    rows = [(c.instrument, c.tf, c.open_time, c.close_time, c.o, c.h, c.l, c.c, c.volume, c.complete) for c in candles]
    return normalize_frame(pd.DataFrame(rows, columns=FRAME_COLUMNS))


def closed_only(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Rows whose candle had closed by `now`. The lookahead firewall.

    `normalize_frame` sorts frames ascending by `close_time`, so the cutoff is found with a
    binary search (O(log n)) rather than a full boolean scan (O(n)). This matters because the
    engine calls this once per bar; a linear scan makes a long backtest O(n^2) and appears to
    hang. Falls back to the boolean mask if the column is not monotonic.
    """
    ct = df["close_time"]
    if ct.is_monotonic_increasing:
        idx = ct.searchsorted(pd.Timestamp(now), side="right")
        return df.iloc[:idx]
    return df[ct <= now]


def last_closed(df: pd.DataFrame, now: datetime) -> pd.Series | None:
    """Newest candle closed at/before `now`, or None if none has."""
    m = closed_only(df, now)
    return None if m.empty else m.iloc[-1]


def window(df: pd.DataFrame, now: datetime, n: int) -> pd.DataFrame:
    """Last `n` closed candles ending at `now`."""
    return closed_only(df, now).tail(n)


def has_bid_ask(df: pd.DataFrame) -> bool:
    """True when a frame carries bid/ask OHLC columns (fetched with price="MBA")."""
    return all(c in df.columns for c in BA_COLUMNS)


def _ba(row: object) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for col in BA_COLUMNS:
        v = getattr(row, col, None)
        out[col] = None if v is None or pd.isna(v) else float(v)
    return out


def row_to_candle(row: object) -> Candle:
    """Build a Candle from an itertuples(index=False) row of a normalized frame (bid/ask kept if present)."""
    return Candle(
        instrument=row.instrument,  # type: ignore[attr-defined]
        tf=row.tf,  # type: ignore[attr-defined]
        open_time=row.open_time.to_pydatetime(),  # type: ignore[attr-defined]
        close_time=row.close_time.to_pydatetime(),  # type: ignore[attr-defined]
        o=float(row.o),  # type: ignore[attr-defined]
        h=float(row.h),  # type: ignore[attr-defined]
        l=float(row.l),  # type: ignore[attr-defined]
        c=float(row.c),  # type: ignore[attr-defined]
        volume=int(row.volume),  # type: ignore[attr-defined]
        complete=bool(row.complete),  # type: ignore[attr-defined]
        **_ba(row),
    )
