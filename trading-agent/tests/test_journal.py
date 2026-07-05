"""Journal rendering: per-trade notes + run summary from a JSONL log."""

from __future__ import annotations

import json

from trading_agent.journal import render

_RECORDS = [
    {"ts": "2026-01-06T08:15:00+00:00", "type": "RUN_START", "run_id": "bt", "strategy_version": "v1.0.0", "reason": "run_start"},
    {"ts": "2026-01-06T08:15:00+00:00", "type": "SIGNAL", "run_id": "bt", "signal_id": "s1", "instrument": "AUD_USD", "direction": "LONG", "entry_ref": 0.67, "setup_id": "ichimoku_pullback", "strategy_version": "v1.0.0", "reason": "trend_pullback_long", "features": {"h1_kijun": 0.669}},
    {"ts": "2026-01-06T08:15:00+00:00", "type": "SUBMIT", "run_id": "bt", "trade_id": "s1-t1", "signal_id": "s1", "units": 50000, "stop": 0.668, "tp": 0.674, "risk_amount": 100.0, "reason": "approved"},
    {"ts": "2026-01-06T08:30:00+00:00", "type": "FILL", "run_id": "bt", "trade_id": "s1-t1", "price": 0.6701, "units": 50000, "reason": "fill"},
    {"ts": "2026-01-06T08:45:00+00:00", "type": "EXIT", "run_id": "bt", "trade_id": "s1-t1", "price": 0.674, "units": 50000, "pnl": 195.0, "exit_reason": "TP", "reason": "TP"},
    {"ts": "2026-01-06T09:00:00+00:00", "type": "SKIP", "run_id": "bt", "reason": "no_setup"},
    {"ts": "2026-01-06T09:00:00+00:00", "type": "RUN_END", "run_id": "bt", "final_equity": 10195.0, "reason": "run_end"},
]


def test_render_writes_notes_and_summary(tmp_path):
    log = tmp_path / "events.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in _RECORDS) + "\n")
    out = tmp_path / "journal"

    msg = render(log, out)

    note = (out / "s1-t1.md").read_text()
    assert "AUD_USD LONG" in note
    assert "trend_pullback_long" in note
    assert "195.0" in note
    assert "#win" in note and "#tp" in note

    summary = (out / "summary.md").read_text()
    assert "Win rate: 100%" in summary
    assert "1W/0L" in msg
