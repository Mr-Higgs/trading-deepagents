"""Stored economic calendar + auto-built news blackout windows.

The calendar is a CSV kept on disk (never a live feed in backtests) with columns:
`time` (ISO-8601 UTC event time), `currency`, `impact` (Low|Medium|High|Holiday), `title`, and an
optional `published` (when the event became known; defaults to `time - 7 days`, i.e. the weekly
calendar). Blackouts cover high-impact events for either currency of the traded pair.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from trading_agent.config import NewsWindow

CALENDAR_COLUMNS = ["time", "currency", "impact", "title"]
DEFAULT_LEAD = timedelta(days=7)


def load_calendar(path: str | Path) -> pd.DataFrame:
    """Load a stored calendar CSV; returns an empty frame (same columns) when the file is missing."""
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=[*CALENDAR_COLUMNS, "published"])
    df = pd.read_csv(p)
    missing = [c for c in CALENDAR_COLUMNS if c not in df.columns]
    if missing:
        msg = f"calendar {p} missing columns {missing}"
        raise ValueError(msg)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df["published"] = pd.to_datetime(df["published"], utc=True) if "published" in df.columns else df["time"] - DEFAULT_LEAD
    return df.sort_values("time").reset_index(drop=True)


def known_as_of(cal: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    """Rows already published at `as_of` (the point-in-time view of the calendar)."""
    return cal[cal["published"] <= pd.Timestamp(as_of)]


def pair_currencies(instrument: str) -> tuple[str, ...]:
    return tuple(instrument.upper().split("_"))


def build_news_windows(
    cal: pd.DataFrame,
    instrument: str,
    *,
    minutes_before: int = 30,
    minutes_after: int = 30,
    impacts: tuple[str, ...] = ("High",),
) -> tuple[NewsWindow, ...]:
    """Blackout windows around each matching event, merged where they overlap."""
    ccys = set(pair_currencies(instrument))
    wanted = {i.lower() for i in impacts}
    rows = cal[cal["currency"].str.upper().isin(ccys) & cal["impact"].str.lower().isin(wanted)]
    windows: list[NewsWindow] = []
    for r in rows.itertuples(index=False):
        t = pd.Timestamp(r.time).to_pydatetime().astimezone(timezone.utc)
        start, end = t - timedelta(minutes=minutes_before), t + timedelta(minutes=minutes_after)
        reason = f"{r.currency} {r.title}"
        if windows and start <= windows[-1].end:
            last = windows[-1]
            windows[-1] = NewsWindow(start=last.start, end=max(last.end, end), reason=f"{last.reason}; {reason}")
        else:
            windows.append(NewsWindow(start=start, end=end, reason=reason))
    return tuple(windows)
