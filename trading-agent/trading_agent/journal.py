"""Render the JSONL event log into human-readable markdown: one note per trade + a run summary.

Stdlib `str.format` over the templates in ../templates - no jinja. ponytail: a single run summary
(not per-calendar-day files); split by day later if a run ever spans many days.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def _load(log_path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(log_path).read_text().splitlines() if line.strip()]


def _build_trades(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    signals = {r["signal_id"]: r for r in records if r["type"] == "SIGNAL"}
    trades: dict[str, dict[str, Any]] = {}
    for r in records:
        t = r["type"]
        if t == "SUBMIT":
            trades[r["trade_id"]] = {
                "trade_id": r["trade_id"],
                "signal_id": r.get("signal_id"),
                "units": r.get("units"),
                "stop": r.get("stop"),
                "tp": r.get("tp"),
                "risk_amount": r.get("risk_amount"),
                "submit_ts": r["ts"],
            }
        elif t == "FILL":
            trades.setdefault(r["trade_id"], {"trade_id": r["trade_id"]})["fill_price"] = r["price"]
        elif t == "EXIT":
            tr = trades.setdefault(r["trade_id"], {"trade_id": r["trade_id"]})
            tr.update(exit_price=r["price"], pnl=r["pnl"], exit_reason=r["exit_reason"], exit_ts=r["ts"])
    for tr in trades.values():
        sig = signals.get(tr.get("signal_id"), {})
        tr["direction"] = sig.get("direction", "?")
        tr["instrument"] = sig.get("instrument", "?")
        tr["setup_id"] = sig.get("setup_id", "?")
        tr["strategy_version"] = sig.get("strategy_version", "?")
        tr["signal_reason"] = sig.get("reason", "?")
        tr["entry_ref"] = sig.get("entry_ref")
        tr["features"] = sig.get("features", {})
    return trades


def _result(tr: dict[str, Any]) -> str:
    if "pnl" not in tr:
        return "OPEN"
    if tr["pnl"] > 0:
        return "WIN"
    return "LOSS" if tr["pnl"] < 0 else "BE"


def _tags(tr: dict[str, Any], result: str) -> str:
    parts = [tr.get("instrument", ""), tr.get("direction", ""), tr.get("exit_reason", ""), result]
    return " ".join(f"#{p.lower()}" for p in parts if p)


def render(log_path: str | Path, out_dir: str | Path) -> str:
    records = _load(log_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    trade_tmpl = (_TEMPLATES / "trade_note.md").read_text()
    summary_tmpl = (_TEMPLATES / "daily_summary.md").read_text()

    trades = _build_trades(records)
    wins = losses = open_ = 0
    pnl_total = 0.0
    rows = []
    for tr in trades.values():
        result = _result(tr)
        wins += result == "WIN"
        losses += result == "LOSS"
        open_ += result == "OPEN"
        pnl_total += tr.get("pnl") or 0.0
        fields = {
            "features": json.dumps(tr.get("features", {})),
            "result": result,
            "tags": _tags(tr, result),
            "exit_price": tr.get("exit_price", "-"),
            "exit_reason": tr.get("exit_reason", "-"),
            "exit_ts": tr.get("exit_ts", "-"),
            "pnl": tr.get("pnl", "-"),
            "fill_price": tr.get("fill_price", "-"),
            "entry_ref": tr.get("entry_ref", "-"),
            "risk_amount": tr.get("risk_amount", "-"),
            **{k: tr.get(k, "-") for k in ("trade_id", "instrument", "direction", "setup_id", "strategy_version", "signal_reason", "submit_ts", "units", "stop", "tp")},
        }
        (out / f"{tr['trade_id']}.md").write_text(trade_tmpl.format(**fields))
        rows.append(f"| {tr['trade_id']} | {tr.get('direction', '?')} | {result} | {tr.get('pnl', '-')} |")

    closed = wins + losses
    win_rate = f"{wins / closed:.0%}" if closed else "n/a"
    skips = Counter(r.get("reason", "?") for r in records if r["type"] in {"SKIP", "REJECT", "HALT"})
    skip_table = "\n".join(f"- {reason}: {n}" for reason, n in skips.most_common()) or "- none"
    trade_table = "\n".join(["| trade | dir | result | pnl |", "|---|---|---|---|", *rows]) if rows else "_no trades_"
    final_equity = next((r["final_equity"] for r in reversed(records) if r["type"] == "RUN_END"), "-")
    run_id = records[0]["run_id"] if records else "-"

    summary = summary_tmpl.format(
        run_id=run_id,
        n_trades=len(trades),
        wins=wins,
        losses=losses,
        open=open_,
        win_rate=win_rate,
        pnl=round(pnl_total, 2),
        final_equity=final_equity,
        skip_table=skip_table,
        trade_table=trade_table,
    )
    (out / "summary.md").write_text(summary)
    return f"journal: {len(trades)} trades, {wins}W/{losses}L, win rate {win_rate}, P/L {pnl_total:.2f} -> {out}"


def main() -> None:
    ap = argparse.ArgumentParser(prog="trading_agent.journal")
    ap.add_argument("logfile")
    ap.add_argument("--out", default="data/journal")
    args = ap.parse_args()
    print(render(args.logfile, args.out))


if __name__ == "__main__":
    main()
