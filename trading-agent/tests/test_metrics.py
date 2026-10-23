"""Risk-adjusted metrics on hand-built return series (TEST FIXTURES)."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import pytest

from trading_agent import metrics as m
from trading_agent.analytics import analyze

UTC = timezone.utc


def test_daily_returns_fill_idle_weekdays():
    ev = [(datetime(2025, 1, 6, 10, tzinfo=UTC), 100.0), (datetime(2025, 1, 8, 10, tzinfo=UTC), -50.0)]
    r = m.daily_returns(ev, 10_000.0)
    assert r == pytest.approx([0.01, 0.0, -50 / 10_100])


def test_sharpe_sortino_mdd_mar():
    r = [0.01, -0.005, 0.02, -0.01, 0.0]
    mu = sum(r) / 5
    sd = math.sqrt(sum((x - mu) ** 2 for x in r) / 4)
    assert m.sharpe(r) == pytest.approx(mu / sd * math.sqrt(252))
    assert m.sortino(r) > m.sharpe(r)
    assert m.max_drawdown_pct([0.1, -0.5, 0.2]) == pytest.approx(0.5)
    assert m.sharpe([0.01, 0.01]) == 0.0
    assert m.mar([0.01, -0.02, 0.01]) == pytest.approx(m.cagr([0.01, -0.02, 0.01]) / m.max_drawdown_pct([0.01, -0.02, 0.01]))


def test_analyze_reports_new_metrics_and_financing(tmp_path):
    recs = [
        {"type": "SUBMIT", "trade_id": "a", "ts": "2025-01-06T08:00:00+00:00", "risk_amount": 100.0, "signal_id": "a"},
        {"type": "FILL", "trade_id": "a", "ts": "2025-01-06T08:15:00+00:00", "price": 1.0, "units": 1000},
        {"type": "FINANCING", "trade_id": "a", "ts": "2025-01-06T21:00:00+00:00", "pnl": -2.0},
        {"type": "EXIT", "trade_id": "a", "ts": "2025-01-07T10:00:00+00:00", "price": 1.2, "pnl": 200.0, "exit_reason": "TP"},
    ]
    p = tmp_path / "log.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    out = analyze(p)
    assert out["total_pnl"] == pytest.approx(198.0)
    assert out["financing_pnl"] == pytest.approx(-2.0)
    assert out["avg_R"] == pytest.approx(1.98)
    for k in ("sharpe", "sortino", "max_drawdown_pct", "mar"):
        assert k in out


def test_daily_returns_span_run_bounds():
    from datetime import date

    ev = [(datetime(2025, 1, 8, 12, tzinfo=timezone.utc), 100.0)]
    rets = m.daily_returns(ev, 10_000.0, start=date(2025, 1, 6), end=date(2025, 1, 10))
    assert rets == [0.0, 0.0, 0.01, 0.0, 0.0]
    assert m.daily_returns([], 10_000.0, start=date(2025, 1, 6), end=date(2025, 1, 7)) == [0.0, 0.0]
