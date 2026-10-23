"""No analyst tool returns data dated after `as_of`. All payloads are hand-built TEST FIXTURES and
the network is replaced by an in-memory fetch (unit tests stay offline)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trading_agent.strategy.agents import tools as t

UTC = timezone.utc
AS_OF = datetime(2025, 3, 10, 12, tzinfo=UTC)  # Monday


COT_ROWS = [  # report dates (Tuesdays); 2025-03-04 was published Fri 2025-03-07
    {"report_date_as_yyyy_mm_dd": "2025-03-11T00:00:00.000", "noncomm_positions_long_all": "999", "noncomm_positions_short_all": "1"},
    {"report_date_as_yyyy_mm_dd": "2025-03-04T00:00:00.000", "noncomm_positions_long_all": "10", "noncomm_positions_short_all": "50"},
    {"report_date_as_yyyy_mm_dd": "2025-02-25T00:00:00.000", "noncomm_positions_long_all": "20", "noncomm_positions_short_all": "50"},
]
ALFRED = {"observations": [
    {"date": "2025-02-01", "realtime_start": "2025-03-01", "value": "4.33"},
    {"date": "2025-03-01", "realtime_start": "2025-03-05", "value": "4.30"},
    {"date": "2025-03-20", "realtime_start": "2025-03-21", "value": "9.99"},  # future observation
    {"date": "2025-02-15", "realtime_start": "2025-04-01", "value": "8.88"},  # future vintage
]}
CAL = """time,currency,impact,title,forecast,previous,actual
2025-03-07T13:30:00Z,USD,High,NFP,160K,143K,151K
2025-03-11T00:30:00Z,AUD,High,NAB Business Confidence,,-1,7
2025-04-20T00:30:00Z,AUD,High,Far Future Event,,,
"""


def _fake_fetch(url, params=None, headers=None):  # noqa: ARG001
    return ALFRED if "stlouisfed" in url else COT_ROWS


def test_cot_uses_publication_date():
    out = t.cot_report("232741", AS_OF, fetch=_fake_fetch)
    assert "2025-03-04" in out and "999" not in out
    before_release = datetime(2025, 3, 7, 12, tzinfo=UTC)  # Friday morning: 03-04 report not out yet
    assert "2025-02-25" in t.cot_report("232741", before_release, fetch=_fake_fetch)


def test_alfred_vintage_excludes_future(monkeypatch):
    assert t.alfred_values_as_of(ALFRED["observations"], AS_OF) == [4.33, 4.30]
    monkeypatch.setenv("FRED_API_KEY", "test-fixture-key")
    out = t.macro_snapshot(AS_OF, fetch=_fake_fetch)
    assert "9.99" not in out and "8.88" not in out


def test_macro_without_key_is_unavailable_not_current(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert t.macro_snapshot(AS_OF, fetch=_fake_fetch) == "Macro context unavailable."


def test_calendar_from_store_hides_unreleased_actuals(tmp_path, monkeypatch):
    p = tmp_path / "cal.csv"
    p.write_text(CAL)
    monkeypatch.setenv(t.CALENDAR_PATH_ENV, str(p))
    with t.backtest_clock(AS_OF):
        out = t.economic_calendar.invoke({"instrument": "AUD_USD", "hours_ahead": 48, "hours_back": 96})
    assert "actual 151K" in out  # released before as_of
    assert "NAB" in out and "actual 7" not in out  # upcoming: no actual leaks
    assert "Far Future" not in out


def test_calendar_unavailable_outside_store_coverage(tmp_path, monkeypatch):
    monkeypatch.setenv(t.CALENDAR_PATH_ENV, str(tmp_path / "missing.csv"))
    with t.backtest_clock(AS_OF):
        assert t.economic_calendar.invoke({"instrument": "AUD_USD"}) == "Economic calendar unavailable."


def test_explicit_as_of_is_clamped_to_backtest_clock():
    with t.backtest_clock(AS_OF):
        assert t.resolve_as_of("2026-01-01T00:00:00Z") == AS_OF
    assert t.resolve_as_of(None) is None


@pytest.mark.parametrize("tool", [t.cftc_cot, t.economic_calendar, t.macro_context])
def test_every_tool_accepts_as_of(tool):
    assert "as_of" in tool.args
