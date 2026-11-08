"""Auto news blackouts from a stored calendar. Events are TEST FIXTURES."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from conftest import make_risk

from trading_agent.config import NewsAutoConfig, NewsWindow
from trading_agent.news import RefreshingNewsWindows, build_news_windows, known_as_of, load_calendar
from trading_agent.sessions import in_news_window

UTC = timezone.utc
CSV = """time,currency,impact,title,forecast,previous,actual
2025-02-04T03:30:00Z,AUD,High,RBA Rate,4.10%,4.35%,4.10%
2025-02-04T03:45:00Z,AUD,High,RBA Statement,,,
2025-02-07T13:30:00Z,USD,High,NFP,170K,256K,143K
2025-02-07T15:00:00Z,USD,Low,Consumer Credit,,,
2025-02-07T13:30:00Z,EUR,High,ECB Speak,,,
"""


def _cal(tmp_path):
    p = tmp_path / "cal.csv"
    p.write_text(CSV)
    return load_calendar(p)


def test_windows_cover_pair_high_impact_and_merge(tmp_path):
    wins = build_news_windows(_cal(tmp_path), "AUD_USD", minutes_before=15, minutes_after=30)
    assert len(wins) == 2  # two RBA events merged; EUR + low impact dropped
    assert wins[0].start == datetime(2025, 2, 4, 3, 15, tzinfo=UTC)
    assert wins[0].end == datetime(2025, 2, 4, 4, 15, tzinfo=UTC)
    assert in_news_window(datetime(2025, 2, 7, 13, 50, tzinfo=UTC), wins)
    assert not in_news_window(datetime(2025, 2, 7, 14, 1, tzinfo=UTC), wins)


def test_missing_calendar_is_empty_and_published_default(tmp_path):
    assert load_calendar(tmp_path / "none.csv").empty
    cal = _cal(tmp_path)
    assert len(known_as_of(cal, datetime(2025, 1, 28, 3, 29, tzinfo=UTC))) == 0  # weekly calendar not out yet
    assert len(known_as_of(cal, datetime(2025, 1, 28, 3, 30, tzinfo=UTC) + timedelta(days=1))) == 2


def test_config_wiring(tmp_path):
    from trading_agent.backtest_setup import with_news_windows
    from trading_agent.config import load_config

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "cal.csv").write_text(CSV)
    cfg = load_config("config")
    risk = replace(make_risk(), news_auto=NewsAutoConfig(enabled=True, calendar="data/cal.csv"))
    out = with_news_windows(replace(cfg, risk=risk), root=tmp_path)
    assert len(out.risk.news_windows) == 2


def test_missing_calendar_warns(tmp_path):
    import pytest

    from trading_agent.backtest_setup import with_news_windows
    from trading_agent.config import load_config

    cfg = load_config("config")
    risk = replace(make_risk(), news_auto=NewsAutoConfig(enabled=True, calendar="data/missing.csv"))
    with pytest.warns(UserWarning, match="no news blackouts"):
        out = with_news_windows(replace(cfg, risk=risk), root=tmp_path)
    assert out.risk.news_windows == ()


def test_refreshing_windows_cadence_and_failure():
    clock = [datetime(2026, 11, 2, 12, 0, tzinfo=UTC)]
    auto = NewsWindow(start=datetime(2026, 11, 3, 13, 0, tzinfo=UTC), end=datetime(2026, 11, 3, 14, 0, tzinfo=UTC), reason="auto")
    manual = (NewsWindow(start=datetime(2026, 11, 5, 0, 0, tzinfo=UTC), end=datetime(2026, 11, 5, 1, 0, tzinfo=UTC), reason="manual"),)
    counts = {"build": 0, "merge": 0}

    def build():
        counts["build"] += 1
        return (auto,)

    def merge():
        counts["merge"] += 1

    w = RefreshingNewsWindows(manual, build, merge, refresh_hours=6.0, now=lambda: clock[0])
    assert list(w) == list(manual)  # constructor does not fetch
    assert w.refresh(force=True) is True
    assert counts == {"build": 1, "merge": 1}
    assert list(w) == [*manual, auto]
    assert len(w) == 2

    assert w.refresh() is False  # inside the 6h cadence
    assert counts == {"build": 1, "merge": 1}

    clock[0] += timedelta(hours=6)
    assert w.refresh() is True
    assert counts == {"build": 2, "merge": 2}

    clock[0] += timedelta(hours=6)

    def boom():
        counts["merge"] += 1
        raise RuntimeError("boom")

    w._merge = boom
    with pytest.warns(UserWarning, match="Forex Factory refresh failed"):
        assert w.refresh() is True
    assert counts == {"build": 3, "merge": 3}
    assert list(w) == [*manual, auto]  # windows rebuilt from the stored calendar despite the failed pull
