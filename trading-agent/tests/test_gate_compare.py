"""gate_compare: veto counterfactual matches gated decisions to the deterministic trades on the same bar."""

from __future__ import annotations

import json
from pathlib import Path

from trading_agent.gate_compare import compare, to_markdown

T1, T2, T3 = "2026-01-05T08:00:00+00:00", "2026-01-06T08:00:00+00:00", "2026-01-07T08:00:00+00:00"


def _trade(sid: str, ts: str, pnl: float, verdict: bool = False) -> list[dict]:
    feats = {"llm_verdict": {"decision": "approve"}} if verdict else {}
    return [
        {"type": "SIGNAL", "signal_id": sid, "ts": ts, "direction": "LONG", "setup_id": "x", "instrument": "AUD_USD", "entry_ref": 1.0, "features": feats},
        {"type": "SUBMIT", "trade_id": f"{sid}-t1", "signal_id": sid, "ts": ts, "stop": 0.99, "tp": 1.02, "risk_amount": 100.0, "units": 1},
        {"type": "EXIT", "trade_id": f"{sid}-t1", "ts": ts, "price": 1.0, "pnl": pnl, "exit_reason": "TP" if pnl > 0 else "STOP"},
    ]


def _write(path: Path, records: list[dict]) -> Path:
    path.write_text("".join(json.dumps({"run_id": path.stem, **r}) + "\n" for r in records))
    return path


def test_vetoes_score_against_baseline_trades_on_the_same_bar(tmp_path):
    base = _write(tmp_path / "det.jsonl", [*_trade("s1", T1, -100.0), *_trade("s2", T2, 200.0), *_trade("s3", T3, -50.0)])
    gated = _write(tmp_path / "gate.jsonl", [
        {"type": "SKIP", "ts": T1, "reason": "llm_veto:0.80:against"},
        *_trade("s2", T2, 200.0, verdict=True),
        {"type": "SKIP", "ts": T3, "reason": "llm_error:gate_unavailable"},
        {"type": "SKIP", "ts": "2026-01-08T08:00:00+00:00", "reason": "llm_veto:0.60:no baseline trade"},
    ])

    res = compare(base, {"gate": gated})
    g = res["gates"]["gate"]
    assert g["counts"] == {"veto": 2, "error": 1, "approve": 1}
    assert g["veto"]["matched_trades"] == 1 and g["veto"]["pnl"] == -100.0 and g["veto"]["losers"] == 1
    assert g["error"]["pnl"] == -50.0  # errors scored apart from vetoes
    assert g["approve"]["pnl"] == 200.0 and g["approve"]["winners"] == 1
    assert res["runs"]["deterministic"]["trades"] == 3 and res["runs"]["gate"]["trades"] == 1
    assert "| gate | veto | 2 | 1 | -100.00 |" in to_markdown(res)
