"""Unit tests for validate_scaleout metrics (no network)."""

from __future__ import annotations

import json
from pathlib import Path

from trading_agent.validate_scaleout import _metrics, _trade_pnls


def test_trade_pnls_sums_partial_and_final(tmp_path: Path):
    records = [
        {"type": "PARTIAL_EXIT", "trade_id": "t1", "pnl": 10.0, "remaining_units": 500},
        {"type": "EXIT", "trade_id": "t1", "pnl": 20.0, "partial": False},
        {"type": "EXIT", "trade_id": "t2", "pnl": -5.0, "partial": False},
    ]
    assert sorted(_trade_pnls(records)) == [-5.0, 30.0]

    log = tmp_path / "e.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    m = _metrics(log)
    assert m["trades"] == 2
    assert m["total_pnl"] == 25.0
    assert m["expectancy"] == 12.5
