"""Analyst-tool parsers vs canned JSON (the httpx wrapper is validated in the live dry-run)."""

from __future__ import annotations

import pytest

pytest.importorskip("langchain_core")  # tools.py imports @tool; only present under the phase2 group

from trading_agent.strategy.agents.tools import summarize_calendar, summarize_cot


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
