"""Analyst-tool parsers vs canned JSON (the httpx wrapper is validated in the live dry-run)."""

from __future__ import annotations

import pytest

pytest.importorskip("langchain_core")  # tools.py imports @tool; only present under the phase2 group

from trading_agent.strategy.agents.tools import summarize_calendar, summarize_cot, summarize_macro


def test_summarize_cot_net_long():
    rows = [{"report_date_as_yyyy_mm_dd": "2026-06-30T00:00:00.000", "noncomm_positions_long_all": "80000", "noncomm_positions_short_all": "50000"}]
    s = summarize_cot(rows)
    assert "net long" in s
    assert "30000" in s
    assert "2026-06-30" in s


def test_summarize_cot_net_short():
    rows = [{"report_date_as_yyyy_mm_dd": "2026-06-30", "noncomm_positions_long_all": "20000", "noncomm_positions_short_all": "60000"}]
    assert "net short" in summarize_cot(rows)
    assert "40000" in summarize_cot(rows)


def test_summarize_cot_empty():
    assert "unavailable" in summarize_cot([]).lower()


def test_summarize_calendar_flags_high_impact_first():
    events = [  # Forex Factory shape: country=currency, impact string, forecast/previous
        {"title": "Unemployment Claims", "country": "USD", "impact": "Medium", "forecast": "218K", "previous": "215K"},
        {"title": "RBA Rate Decision", "country": "AUD", "impact": "High", "forecast": "3.85%", "previous": "3.85%"},
    ]
    s = summarize_calendar(events)
    assert "1 high-impact of 2" in s
    assert s.index("RBA Rate Decision") < s.index("Unemployment Claims")  # high-impact ranked first
    assert "forecast 3.85%" in s


def test_summarize_calendar_empty():
    assert "No scheduled" in summarize_calendar([])


def test_summarize_calendar_shows_actual_vs_forecast():
    # A release already out (actual populated) is the Systematic-Macro "surprise" -> actual vs forecast.
    events = [{"title": "AU CPI", "country": "AUD", "impact": "High", "actual": "1.0%", "forecast": "0.8%", "previous": "0.7%"}]
    s = summarize_calendar(events)
    assert "actual 1.0% vs forecast 0.8%" in s


def test_summarize_macro_rate_differential_and_sentiment():
    data = {
        "US policy rate": [4.30, 4.33],  # rising
        "AU policy rate": [4.10, 4.10],  # flat
        "Metals index": [120.0, 123.0],  # rising
        "VIX": [22.0, 15.0],  # falling -> risk-on
    }
    s = summarize_macro(data)
    assert "differential -0.23pp" in s  # AU 4.10 - US 4.33
    assert "AUD carry negative" in s
    assert "risk-on" in s
    assert "rising" in s  # metals / US rate direction


def test_summarize_macro_empty():
    assert "unavailable" in summarize_macro({}).lower()
    assert "unavailable" in summarize_macro({"VIX": []}).lower()
