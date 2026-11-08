"""Stored economic calendar + auto-built news blackout windows.

The calendar is a CSV kept on disk (never a live feed in backtests) with columns:
`time` (ISO-8601 UTC event time), `currency`, `impact` (Low|Medium|High|Holiday), `title`, and an
optional `published` (when the event became known; defaults to `time - 7 days`, i.e. the weekly
calendar). Blackouts cover high-impact events for either currency of the traded pair.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta, timezone
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


def _override(row: object, col: str, default: int) -> float:
    v = getattr(row, col, None)
    return default if v is None or pd.isna(v) else v


def pair_currencies(instrument: str) -> tuple[str, ...]:
    return tuple(instrument.upper().split("_"))


def build_news_windows(
    cal: pd.DataFrame,
    instrument: str,
    *,
    minutes_before: int = 30,
    minutes_after: int = 30,
    impacts: tuple[str, ...] = ("High",),
    impact_minutes: dict[str, tuple[int, int]] | None = None,
) -> tuple[NewsWindow, ...]:
    """Blackout windows around each matching event, merged where they overlap.

    Window per event: the row's own `before_min`/`after_min` (e.g. FOMC press conference) if set, else
    `impact_minutes[impact]`, else `minutes_before`/`minutes_after`.
    """
    impact_minutes = {k.lower(): v for k, v in (impact_minutes or {}).items()}
    ccys = set(pair_currencies(instrument))
    wanted = {i.lower() for i in impacts}
    rows = cal[cal["currency"].str.upper().isin(ccys) & cal["impact"].str.lower().isin(wanted)]
    spans = []
    for r in rows.itertuples(index=False):
        t = pd.Timestamp(r.time).to_pydatetime().astimezone(timezone.utc)
        before, after = impact_minutes.get(str(r.impact).lower(), (minutes_before, minutes_after))
        before = int(_override(r, "before_min", before))
        after = int(_override(r, "after_min", after))
        spans.append((t - timedelta(minutes=before), t + timedelta(minutes=after), f"{r.currency} {r.title}"))
    windows: list[NewsWindow] = []
    for start, end, reason in sorted(spans, key=lambda s: s[0]):
        if windows and start <= windows[-1].end:
            last = windows[-1]
            windows[-1] = NewsWindow(start=last.start, end=max(last.end, end), reason=f"{last.reason}; {reason}")
        else:
            windows.append(NewsWindow(start=start, end=end, reason=reason))
    return tuple(windows)


class RefreshingNewsWindows:
    """Live-mode blackout windows that re-pull this week's Forex Factory feed on a cadence.

    Iterating yields the manual windows plus the auto windows rebuilt at the last refresh, so the engine's
    `in_news_window(now, cfg.risk.news_windows)` needs no change. `refresh()` is cheap to call every poll:
    it only does work once `refresh_hours` have elapsed (or when forced). A failed pull warns and keeps the
    previous windows — a flaky feed must never block the loop.
    """

    def __init__(
        self,
        manual: tuple[NewsWindow, ...],
        build: Callable[[], tuple[NewsWindow, ...]],
        merge: Callable[[], object],
        *,
        refresh_hours: float,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._manual = manual
        self._build = build  # rebuild auto windows from the stored CSV
        self._merge = merge  # fold this week's FF feed into the stored CSV
        self._refresh_hours = refresh_hours
        self._now = now
        self._auto: tuple[NewsWindow, ...] = ()
        self.last_refresh: datetime | None = None

    def refresh(self, *, force: bool = False) -> bool:
        """Re-pull the feed and rebuild windows if due (or forced); returns whether a refresh ran."""
        if not force and self.last_refresh is not None and self._now() - self.last_refresh < timedelta(hours=self._refresh_hours):
            return False
        try:
            self._merge()
        except Exception as exc:  # noqa: BLE001 - a flaky feed must never block the trading loop
            warnings.warn(f"Forex Factory refresh failed, using stored calendar only: {exc}", stacklevel=2)
        self._auto = self._build()  # always rebuild from the stored CSV, even after a failed merge
        self.last_refresh = self._now()
        return True

    def __iter__(self) -> Iterator[NewsWindow]:
        return iter((*self._manual, *self._auto))

    def __len__(self) -> int:
        return len(self._manual) + len(self._auto)
