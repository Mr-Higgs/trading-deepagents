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


def any_open(now: datetime, sessions: Iterable[SessionConfig]) -> bool:
    """Trade if the bar falls in ANY active session (each checked in its own local zone)."""
    return any(is_open(now, s) for s in sessions)


def in_news_window(now: datetime, windows: Iterable[NewsWindow]) -> bool:
    return any(w.start <= now < w.end for w in windows)
