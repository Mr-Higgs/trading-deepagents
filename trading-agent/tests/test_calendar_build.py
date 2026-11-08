"""Calendar builder parsers. HTML snippets and dates are TEST FIXTURES shaped like the publishers' pages."""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import pandas as pd
import pytest

from trading_agent import calendar_build as cb
from trading_agent.news import build_news_windows

FOMC_HTML = """
<h4><a id="1">2025 FOMC Meetings</a></h4>
<div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>January</strong></div>
<div class="fomc-meeting__date">28-29</div> Minutes (Released February 19, 2025)</div>
<div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>August</strong></div>
<div class="fomc-meeting__date">22 (notation vote)</div></div>
<div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>Oct/Nov</strong></div>
<div class="fomc-meeting__date">31-1</div></div>
"""


def test_parse_fomc_statement_minutes_and_skips_notation():
    rows = cb.parse_fomc(FOMC_HTML)
    assert [(r["time"], r["impact"], r["after_min"]) for r in rows] == [
        ("2025-01-29T19:00:00Z", "High", 90),  # 14:00 EST
        ("2025-02-19T19:00:00Z", "Medium", None),
        ("2025-11-01T18:00:00Z", "High", 90),  # 14:00 EDT; second day of a cross-month meeting
    ]


def test_one_per_month_drops_revision_dates():
    ds = [date(2021, 2, 8), date(2021, 2, 10), date(2021, 3, 10), date(2021, 1, 13)]
    assert cb.one_per_month(ds) == [date(2021, 1, 13), date(2021, 2, 10), date(2021, 3, 10)]


def test_fred_rows_use_release_time_in_et():
    def get(url, params):
        return {"release_dates": [{"date": "2025-07-03"}]} if params["release_id"] == 50 else {"release_dates": []}

    rows = cb.fred_rows(date(2025, 7, 1), date(2025, 8, 1), "k", get)
    assert rows == [cb._row(datetime(2025, 7, 3, 12, 30, tzinfo=timezone.utc), "USD", "High",
                            "Non-Farm Payrolls / Unemployment Rate", "fred:50")]


def test_ism_first_and_third_business_day_skip_holidays():
    rows = cb.ism_rows(date(2025, 9, 1), date(2025, 9, 30))  # Sep 1 = Labor Day
    assert [r["time"] for r in rows] == ["2025-09-02T14:00:00Z", "2025-09-04T14:00:00Z"]


def test_parse_rba_and_abs():
    rows = cb.parse_rba_year("<td>18 February 2025</td><td>1 April 2025</td><td>3 March 2024</td><td>4 June 2025</td>", 2025)
    assert [r["time"] for r in rows] == ["2025-02-18T03:30:00Z", "2025-04-01T03:30:00Z"]  # 14:30 AEDT
    when = cb.parse_abs_release("<p>Release date and time</p> <p>16/10/2025 11:30am AEDT</p>")
    assert when.astimezone(timezone.utc) == datetime(2025, 10, 16, 0, 30, tzinfo=timezone.utc)
    assert cb.parse_abs_release("<p>nothing</p>") is None


def test_abs_editions_switch_cpi_to_monthly():
    cpi = next(r for r in cb.ABS_RELEASES if r.edition_suffix)
    eds = cb.abs_editions(cpi, date(2025, 7, 1), date(2026, 1, 1))
    assert "jun-quarter-2025" in eds and "nov-2025" in eds and "dec-2025" in eds and "dec-quarter-2025" not in eds
    assert cb.plausible_release("jun-quarter-2025", datetime(2025, 7, 30, tzinfo=timezone.utc))
    assert not cb.plausible_release("jun-2021", datetime(2021, 10, 19, tzinfo=timezone.utc))


def test_ff_rows_and_merge_dedupe():
    ev = [{"title": "CPI m/m", "country": "USD", "impact": "High", "date": "2026-06-10T08:30:00-04:00"},
          {"title": "Bank Holiday", "country": "USD", "impact": "Holiday", "date": "2026-06-19T00:00:00-04:00"},
          {"title": "ECB", "country": "EUR", "impact": "High", "date": "2026-06-11T08:15:00-04:00"}]
    rows = cb.ff_rows(ev, {"AUD", "USD"})
    assert [(r["time"], r["title"]) for r in rows] == [("2026-06-10T12:30:00Z", "CPI m/m")]
    df = cb.merge(pd.DataFrame(rows), rows)
    assert len(df) == 1


def test_row_overrides_and_impact_minutes_in_windows():
    cal = pd.DataFrame([
        {"time": pd.Timestamp("2025-01-29T19:00Z"), "currency": "USD", "impact": "High", "title": "FOMC", "before_min": 30, "after_min": 90},
        {"time": pd.Timestamp("2025-02-03T15:00Z"), "currency": "USD", "impact": "Medium", "title": "JOLTS", "before_min": None, "after_min": None},
    ])
    w = build_news_windows(cal, "AUD_USD", impacts=("High", "Medium"), impact_minutes={"Medium": (15, 15)})
    assert [(x.start.strftime("%H:%M"), x.end.strftime("%H:%M")) for x in w] == [("18:30", "20:30"), ("14:45", "15:15")]


def test_merge_ff_week_writes_and_counts(tmp_path):
    out = tmp_path / "cal.csv"
    pd.DataFrame([{"time": "2026-11-09T00:00:00Z", "currency": "USD", "impact": "High", "title": "Old",
                   "before_min": None, "after_min": None, "source": "fred:50"}]).to_csv(out, index=False)
    get = lambda url, params=None: [
        {"title": "CPI y/y", "country": "AUD", "impact": "High", "date": "2026-11-10T00:30:00+00:00"},
        {"title": "Low thing", "country": "AUD", "impact": "Low", "date": "2026-11-10T01:00:00+00:00"},
        {"title": "EUR thing", "country": "EUR", "impact": "High", "date": "2026-11-10T02:00:00+00:00"},
    ]
    assert cb.merge_ff_week(out, get=get) == 1
    df = pd.read_csv(out)
    assert len(df) == 2
    new = df[df["title"] == "CPI y/y"].iloc[0]
    assert new["source"] == "forexfactory"
    assert cb.merge_ff_week(out, get=get) == 0  # dedupe on re-run


def test_rba_rows_tolerates_unpublished_year(monkeypatch):
    def fake_text(url):
        if "2027" in url:
            raise httpx.HTTPStatusError("404", request=httpx.Request("GET", url), response=httpx.Response(404, request=httpx.Request("GET", url)))
        return "<td>17 February 2026</td>"  # a Tuesday; parse_rba_year drops non-Tuesday dates

    monkeypatch.setattr(cb, "_http_text", fake_text)
    rows_2026 = cb._rba_rows(2026)
    assert [r["currency"] for r in rows_2026] == ["AUD"] and rows_2026[0]["source"] == "rba.gov.au"
    with pytest.warns(UserWarning, match="RBA decisions page for 2027 not published yet"):
        assert cb._rba_rows(2027) == []
