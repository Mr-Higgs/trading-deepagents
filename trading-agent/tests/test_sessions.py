"""Session gating: any_open matches ANY active session, and config resolves the `active` list."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from trading_agent.config import SessionConfig, load_config
from trading_agent.sessions import any_open, is_open

_WEEKDAYS = frozenset({0, 1, 2, 3, 4})


def _session(tz: str, start: tuple[int, int], end: tuple[int, int]) -> SessionConfig:
    return SessionConfig(timezone=tz, start=datetime(2000, 1, 1, *start).time(), end=datetime(2000, 1, 1, *end).time(), weekdays=_WEEKDAYS)


def test_any_open_matches_any_session() -> None:
    london = _session("Europe/London", (7, 0), (16, 0))
    new_york = _session("America/New_York", (8, 0), (17, 0))
    # Mon 2025-01-06 20:00 UTC = 20:00 London (closed) but 15:00 New York (open); Jan → no DST either side.
    t = datetime(2025, 1, 6, 20, 0, tzinfo=timezone.utc)
    assert not is_open(t, london)
    assert is_open(t, new_york)
    assert any_open(t, [london, new_york])  # NY carries it
    assert not any_open(t, [london])  # London-only would skip this bar


def test_config_resolves_active_sessions() -> None:
    cfg = load_config(Path(__file__).resolve().parent.parent / "config")
    assert isinstance(cfg.sessions, tuple)
    assert len(cfg.sessions) >= 1
    assert any(s.timezone == "Europe/London" for s in cfg.sessions)  # default active: [london]
