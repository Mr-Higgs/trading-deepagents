"""Session and news-window gating. Times compare in the session's local zone (DST via zoneinfo)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from zoneinfo import ZoneInfo

from trading_agent.config import NewsWindow, SessionConfig


def is_open(now: datetime, session: SessionConfig) -> bool:
    local = now.astimezone(ZoneInfo(session.timezone))
    if local.weekday() not in session.weekdays:
        return False
    return session.start <= local.time() < session.end


def in_news_window(now: datetime, windows: Iterable[NewsWindow]) -> bool:
    return any(w.start <= now < w.end for w in windows)
