"""analytics metrics: arithmetic self-check + correct per-trade P/L summation for scale-out trades."""

from __future__ import annotations

import json

from trading_agent.analytics import _demo, analyze


def test_analytics_self_check() -> None:
    _demo()  # asserts expectancy/profit_factor/win_rate on a known 2-trade fixture


def _write(tmp_path, records):
    p = tmp_path / "events.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return p


def test_scale_out_pnl_is_summed(tmp_path) -> None:
    """A trade that banks a partial then stops out must count TOTAL P/L (partial + final), not just the final leg."""
    records = [
        {"type": "RUN_START", "run_id": "r", "ts": "2025-01-01T00:00:00+00:00"},
        {"type": "SIGNAL", "signal_id": "s1", "ts": "2025-01-01T00:00:00+00:00", "direction": "LONG", "setup_id": "x", "instrument": "AUD_USD", "entry_ref": 1.0, "reason": "r", "features": {}, "strategy_version": "t"},
        {"type": "SUBMIT", "trade_id": "s1-t1", "signal_id": "s1", "ts": "2025-01-01T00:00:00+00:00", "stop": 0.99, "tp": 1.03, "risk_amount": 100.0, "units": 2},
        {"type": "PARTIAL_EXIT", "trade_id": "s1-t1", "ts": "2025-01-01T01:00:00+00:00", "price": 1.01, "pnl": 60.0, "exit_reason": "SCALE_OUT", "partial": True, "remaining_units": 1},
        {"type": "EXIT", "trade_id": "s1-t1", "ts": "2025-01-01T02:00:00+00:00", "price": 1.005, "pnl": 25.0, "exit_reason": "STOP"},
    ]
    m = analyze(_write(tmp_path, records))
    assert m["trades"] == 1
    # total P/L is 60 + 25 = 85, not the final leg's 25.
    assert abs(m["total_pnl"] - 85.0) < 1e-9, m
    assert abs(m["expectancy"] - 85.0) < 1e-9, m
    assert m["by_exit_reason"]["STOP"]["n"] == 1  # bucketed by the terminal exit reason
    assert m["win_rate"] == 1.0


def test_open_trade_excluded(tmp_path) -> None:
    """A trade with no terminal exit is still open and must not count in closed-trade metrics."""
    records = [
        {"type": "SIGNAL", "signal_id": "s1", "ts": "2025-01-01T00:00:00+00:00", "direction": "LONG", "setup_id": "x", "instrument": "AUD_USD", "entry_ref": 1.0, "reason": "r", "features": {}, "strategy_version": "t"},
        {"type": "SUBMIT", "trade_id": "s1-t1", "signal_id": "s1", "ts": "2025-01-01T00:00:00+00:00", "stop": 0.99, "tp": 1.03, "risk_amount": 100.0, "units": 1},
        {"type": "FILL", "trade_id": "s1-t1", "ts": "2025-01-01T00:15:00+00:00", "price": 1.0, "units": 1},
    ]
    m = analyze(_write(tmp_path, records))
    assert m["trades"] == 0
