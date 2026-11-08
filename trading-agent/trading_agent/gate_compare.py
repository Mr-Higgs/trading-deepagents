"""A/B the LLM gate: a deterministic backtest log vs one or more gated logs over the same window.

Two views:
- run vs run: the usual edge metrics per log (`analytics.analyze`).
- veto counterfactual: each `llm_veto` SKIP is matched (by bar timestamp) to the trade the deterministic run
  opened on that same bar; that trade's realized P/L is what the veto avoided. Path-dependent and therefore
  approximate (a veto frees the slot for a later signal), but it scores the gate's decisions directly.
`llm_error` SKIPs are fail-closed no-trades, not decisions, and are counted separately.

    uv run python -m trading_agent.gate_compare data/logs/gate_ab/det/events-*.jsonl \\
        gate=data/logs/gate_ab/gate/events-*.jsonl --out research/llm_gate_ab.md
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any

from trading_agent.analytics import _trade_pnls_and_closed, analyze
from trading_agent.journal import _load


def bar_pnls(records: list[dict[str, Any]]) -> dict[str, float]:
    """Realized P/L of every closed trade, keyed by the bar timestamp of the SIGNAL that opened it."""
    signal_ts = {r["signal_id"]: r["ts"] for r in records if r.get("type") == "SIGNAL"}
    pnls, closed = _trade_pnls_and_closed(records)
    out: dict[str, float] = defaultdict(float)
    for r in records:
        if r.get("type") == "SUBMIT" and r.get("trade_id") in closed and r.get("signal_id") in signal_ts:
            out[signal_ts[r["signal_id"]]] += pnls.get(r["trade_id"], 0.0)
    return dict(out)


def gate_decisions(records: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Bar timestamps of each gate outcome: veto, error (fail-closed), approve (SIGNAL carrying a verdict)."""
    out: dict[str, list[str]] = {"veto": [], "error": [], "approve": []}
    for r in records:
        reason = str(r.get("reason") or "")
        if r.get("type") == "SKIP" and reason.startswith("llm_veto"):
            out["veto"].append(r["ts"])
        elif r.get("type") == "SKIP" and reason.startswith("llm_error"):
            out["error"].append(r["ts"])
        elif r.get("type") == "SIGNAL" and (r.get("features") or {}).get("llm_verdict"):
            out["approve"].append(r["ts"])
    return out


def counterfactual(baseline: dict[str, float], stamps: list[str]) -> dict[str, float]:
    """Score gate decisions against the deterministic trades opened on the same bars."""
    hit = [baseline[t] for t in stamps if t in baseline]
    losers = [p for p in hit if p < 0]
    winners = [p for p in hit if p > 0]
    return {
        "decisions": len(stamps),
        "matched_trades": len(hit),
        "pnl": sum(hit),
        "losers": len(losers),
        "losers_pnl": sum(losers),
        "winners": len(winners),
        "winners_pnl": sum(winners),
    }


def compare(baseline_log: str | Path, gated: dict[str, str | Path]) -> dict[str, Any]:
    base_records = _load(baseline_log)
    base = bar_pnls(base_records)
    runs = {"deterministic": analyze(baseline_log)}
    gates: dict[str, dict[str, Any]] = {}
    for name, path in gated.items():
        runs[name] = analyze(path)
        d = gate_decisions(_load(path))
        gates[name] = {
            "counts": {k: len(v) for k, v in d.items()},
            "veto": counterfactual(base, d["veto"]),
            "error": counterfactual(base, d["error"]),
            "approve": counterfactual(base, d["approve"]),
        }
    return {"runs": runs, "gates": gates}


def _pf(x: float) -> str:
    return "inf" if x == float("inf") else f"{x:.2f}"


def to_markdown(res: dict[str, Any]) -> str:
    lines = [
        "| run | trades | expectancy | PF | win rate | total P/L | max DD | Sharpe | avg R |",
        "|---|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for name, m in res["runs"].items():
        lines.append(
            f"| {name} | {m['trades']} | {m['expectancy']:.2f} | {_pf(m['profit_factor'])} | {m['win_rate']:.1%} | "
            f"{m['total_pnl']:.2f} | {m['max_drawdown']:.2f} | {m.get('sharpe', 0.0):.2f} | {m.get('avg_R', 0.0):.2f} |"
        )
    lines += [
        "",
        (
            "**Gate decisions vs the deterministic trade opened on the same bar** "
            "(veto P/L < 0 means the vetoes avoided losses; matched = the deterministic run actually traded that bar)"
        ),
        "",
        "| run | decision | count | matched trades | their det. P/L | losers (P/L) | winners (P/L) |",
        "|---|---|--:|--:|--:|--:|--:|",
    ]
    for name, g in res["gates"].items():
        for kind in ("approve", "veto", "error"):
            c = g[kind]
            lines.append(
                f"| {name} | {kind} | {c['decisions']} | {c['matched_trades']} | {c['pnl']:.2f} | "
                f"{c['losers']} ({c['losers_pnl']:.2f}) | {c['winners']} ({c['winners_pnl']:.2f}) |"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(prog="trading_agent.gate_compare")
    ap.add_argument("baseline", help="deterministic run's event log")
    ap.add_argument("gated", nargs="+", help="name=path of each gated run's event log")
    ap.add_argument("--out", default=None, help="write the markdown table here")
    args = ap.parse_args()
    gated = dict(g.split("=", 1) for g in args.gated)
    md = to_markdown(compare(args.baseline, gated))
    if args.out:
        Path(args.out).write_text(md)
    print(md)


if __name__ == "__main__":
    main()
