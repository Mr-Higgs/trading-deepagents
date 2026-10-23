"""Honest edge metrics from a JSONL event log — the analysis half of the backtest iteration loop.

Replays a run's log into the numbers that actually decide whether an edge is real and *where* it
leaks: expectancy, profit factor, R-multiples, drawdown, plus per-bucket breakdowns (setup / exit
reason / direction / hour), a SKIP/REJECT attribution histogram (why candidates were dropped), and
MAE/MFE per trade (how far each trade ran against / for you before exiting — the tell for stops that
are too tight or targets too far). Win rate is reported but never the headline: a 90% win rate with
a huge stop and a tiny target still bleeds money.

Deterministic and network-free — pure post-processing of the log (+ the entry-TF candle CSV for
MAE/MFE). `analyze()` is the programmatic entry point the research loop calls; the CLI renders a
markdown report to data/reports/.

    uv run python -m trading_agent.analytics data/logs/events-bt-2025-01-01-2026-01-01.jsonl \\
        --candles data/candles/AUD_USD_M15_20250101_20260101.csv
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from trading_agent.journal import _build_trades, _load

_TERMINAL = {"EXIT", "PARTIAL_EXIT"}


def _trade_pnls_and_closed(records: list[dict[str, Any]]) -> tuple[dict[str, float], set[str]]:
    """Per-trade TOTAL P/L (partials + final exit) and the set of trades that fully closed.

    `_build_trades` keeps only the final EXIT's pnl, so scale-out trades would be mis-summed; we
    aggregate here instead (same logic as validate_scaleout._trade_pnls)."""
    total: dict[str, float] = defaultdict(float)
    closed: set[str] = set()
    for r in records:
        if r.get("type") not in _TERMINAL:
            continue
        tid = r.get("trade_id")
        if not tid:
            continue
        total[tid] += float(r.get("pnl") or 0.0)
        if r["type"] == "EXIT" and not r.get("partial"):
            closed.add(tid)
        elif r["type"] == "PARTIAL_EXIT" and r.get("remaining_units") == 0:
            closed.add(tid)
    return dict(total), closed


def _max_drawdown(records: list[dict[str, Any]], starting_equity: float) -> float:
    """Peak-to-trough of the realized-equity curve over terminal-exit P/L, in seq order."""
    equity = peak = starting_equity
    max_dd = 0.0
    for r in records:
        if r.get("type") in _TERMINAL and r.get("pnl") is not None:
            equity += float(r["pnl"])
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
    return max_dd


def _bucket_stats(trades: list[dict[str, Any]], key: str) -> dict[str, dict[str, float]]:
    """Group closed trades by trade[key] -> {n, win_rate, expectancy, total_pnl}."""
    groups: dict[str, list[float]] = defaultdict(list)
    for tr in trades:
        groups[str(tr.get(key, "?"))].append(tr["pnl"])
    out: dict[str, dict[str, float]] = {}
    for name, pnls in sorted(groups.items()):
        wins = sum(1 for p in pnls if p > 0)
        losses = sum(1 for p in pnls if p < 0)
        out[name] = {
            "n": len(pnls),
            "win_rate": wins / (wins + losses) if wins + losses else 0.0,
            "expectancy": sum(pnls) / len(pnls) if pnls else 0.0,
            "total_pnl": sum(pnls),
        }
    return out


def _excursions(trades: list[dict[str, Any]], candles_path: str | Path) -> dict[str, float]:
    """Average MAE/MFE (in R) for winners vs losers, from the entry-TF candle path.

    R = price excursion / risk distance (|entry - stop|). MAE = worst adverse move while open;
    MFE = best favorable move. Losers with high MFE => exits/targets sit too far; winners with high
    MAE => stops sit close to the pain threshold. ponytail: window starts at submit_ts (fill is the
    next bar) — a one-bar over-inclusion; exact fill-time slicing only if it ever matters.
    """
    import pandas as pd

    from trading_agent.marketdata.base import normalize_frame

    df = normalize_frame(pd.read_csv(candles_path))
    ct = df["close_time"]
    acc: dict[str, list[float]] = {"win_mae": [], "win_mfe": [], "loss_mae": [], "loss_mfe": []}
    for tr in trades:
        entry, stop, submit_ts, exit_ts = tr.get("entry_ref"), tr.get("stop"), tr.get("submit_ts"), tr.get("exit_ts")
        if None in (entry, stop, submit_ts, exit_ts):
            continue
        risk = abs(float(entry) - float(stop))
        if risk <= 0:
            continue
        win = df[(ct >= pd.Timestamp(submit_ts)) & (ct <= pd.Timestamp(exit_ts))]
        if win.empty:
            continue
        hi, lo = float(win["h"].max()), float(win["l"].min())
        if tr["direction"] == "LONG":
            mfe, mae = (hi - float(entry)) / risk, (float(entry) - lo) / risk
        else:
            mfe, mae = (float(entry) - lo) / risk, (hi - float(entry)) / risk
        tag = "win" if tr["pnl"] > 0 else "loss"
        acc[f"{tag}_mae"].append(mae)
        acc[f"{tag}_mfe"].append(mfe)
    return {k: (sum(v) / len(v) if v else 0.0) for k, v in acc.items()}


def bootstrap_expectancy(pnls: list[float], n: int = 1000, seed: int = 1337) -> dict[str, float]:
    """Resample closed-trade P/L with replacement to gauge whether expectancy is luck.

    Returns the 5th / 50th percentile of the resampled per-trade expectancy over `n`
    reshuffles. A 5th percentile > 0 means even an unlucky reshuffle of these exact
    trades still profits — the edge is distinguishable from noise, not one lucky run.
    Deterministic (fixed seed) so backtests stay reproducible. ponytail: nearest-rank
    percentile, exact enough at n=1000; bump n if the CI ever needs finer resolution.
    """
    if not pnls:
        return {"p05": 0.0, "p50": 0.0, "n": 0}
    rng = random.Random(seed)
    k = len(pnls)
    means = sorted(sum(rng.choice(pnls) for _ in range(k)) / k for _ in range(n))
    return {"p05": means[int(0.05 * n)], "p50": means[int(0.50 * n)], "n": n}


def analyze(log_path: str | Path, candles_path: str | Path | None = None, starting_equity: float = 10_000.0) -> dict[str, Any]:
    """Fold a run's JSONL log into the edge metrics. Pure/deterministic; no network."""
    records = _load(log_path)
    meta = _build_trades(records)  # reuse the SIGNAL+SUBMIT+FILL join for per-trade metadata
    total_pnl, closed_ids = _trade_pnls_and_closed(records)

    trades: list[dict[str, Any]] = []
    for tid in closed_ids:
        tr = dict(meta.get(tid, {"trade_id": tid}))
        tr["pnl"] = total_pnl.get(tid, 0.0)
        feats = tr.get("features") or {}
        trig = feats.get("m15_trigger")
        tr["hour"] = _hour(tr.get("submit_ts"))
        tr["trigger_set"] = ",".join(trig) if isinstance(trig, list) else str(trig or "-")
        trades.append(tr)

    pnls = [tr["pnl"] for tr in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    n = len(pnls)
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    r_mults = [(tr["pnl"] / tr["risk_amount"]) for tr in trades if tr.get("risk_amount")]
    win_r = [r for r in r_mults if r > 0]
    loss_r = [r for r in r_mults if r < 0]
    max_dd = _max_drawdown(records, starting_equity)

    metrics: dict[str, Any] = {
        "run_id": records[0].get("run_id", "-") if records else "-",
        "strategy_version": records[0].get("strategy_version", "-") if records else "-",
        "trades": n,
        "win_rate": len(wins) / (len(wins) + len(losses)) if wins or losses else 0.0,
        "expectancy": sum(pnls) / n if n else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss else float("inf") if gross_win else 0.0,
        "avg_win_R": sum(win_r) / len(win_r) if win_r else 0.0,
        "avg_loss_R": sum(loss_r) / len(loss_r) if loss_r else 0.0,
        "total_pnl": sum(pnls),
        "pnls": pnls,  # raw closed-trade P/L; the research gate feeds this to bootstrap_expectancy. _brief() strips it before streaming.
        "max_drawdown": max_dd,
        "pnl_over_dd": sum(pnls) / max_dd if max_dd > 0 else sum(pnls),
        "by_setup": _bucket_stats(trades, "setup_id"),
        "by_exit_reason": _bucket_stats(trades, "exit_reason"),
        "by_direction": _bucket_stats(trades, "direction"),
        "by_hour": _bucket_stats(trades, "hour"),
        "skips": dict(Counter(r.get("reason", "?") for r in records if r.get("type") in {"SKIP", "REJECT", "HALT"}).most_common()),
    }
    if candles_path is not None:
        metrics["excursions"] = _excursions(trades, candles_path)
    return metrics


def _hour(ts: str | None) -> str:
    if not ts:
        return "?"
    return f"{datetime.fromisoformat(ts).hour:02d}h"


def _fmt_bucket(title: str, buckets: dict[str, dict[str, float]]) -> str:
    if not buckets:
        return f"**{title}**: _none_\n"
    lines = [f"**{title}**", "", "| bucket | n | win% | expectancy | total P/L |", "|---|--:|--:|--:|--:|"]
    for name, s in buckets.items():
        lines.append(f"| {name} | {s['n']:.0f} | {s['win_rate']:.0%} | {s['expectancy']:.2f} | {s['total_pnl']:.2f} |")
    return "\n".join(lines) + "\n"


def to_markdown(m: dict[str, Any]) -> str:
    pf = m["profit_factor"]
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    head = [
        f"# Backtest analysis — {m['run_id']} ({m['strategy_version']})",
        "",
        "| metric | value |",
        "|---|--:|",
        f"| trades | {m['trades']} |",
        f"| **expectancy** ($/trade) | **{m['expectancy']:.2f}** |",
        f"| profit factor | {pf_s} |",
        f"| win rate | {m['win_rate']:.1%} |",
        f"| avg win (R) | {m['avg_win_R']:.2f} |",
        f"| avg loss (R) | {m['avg_loss_R']:.2f} |",
        f"| total P/L | {m['total_pnl']:.2f} |",
        f"| max drawdown | {m['max_drawdown']:.2f} |",
        f"| P/L ÷ drawdown | {m['pnl_over_dd']:.2f} |",
        "",
    ]
    if "excursions" in m:
        e = m["excursions"]
        head += [
            "**MAE / MFE (avg R while open)**",
            "",
            "| | MAE (adverse) | MFE (favorable) |",
            "|---|--:|--:|",
            f"| winners | {e['win_mae']:.2f} | {e['win_mfe']:.2f} |",
            f"| losers | {e['loss_mae']:.2f} | {e['loss_mfe']:.2f} |",
            "",
        ]
    body = [
        _fmt_bucket("By setup", m["by_setup"]),
        _fmt_bucket("By exit reason", m["by_exit_reason"]),
        _fmt_bucket("By direction", m["by_direction"]),
        _fmt_bucket("By hour (UTC, entry)", m["by_hour"]),
    ]
    skips = m["skips"]
    skip_lines = "\n".join(f"- {r}: {n}" for r, n in skips.items()) or "- none"
    return "\n".join(head + body) + "\n**Why candidates were dropped (SKIP/REJECT/HALT)**\n\n" + skip_lines + "\n"


def render(log_path: str | Path, out_dir: str | Path = "data/reports", candles_path: str | Path | None = None) -> tuple[Path, dict[str, Any]]:
    m = analyze(log_path, candles_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / f"{m['run_id']}.md"
    md_path.write_text(to_markdown(m))
    (out / f"{m['run_id']}.json").write_text(json.dumps(m, indent=2))
    return md_path, m


def _demo() -> None:
    """Self-check: 2 closed trades (1 win +2R, 1 loss -1R) -> known metrics."""
    recs = [
        {"type": "RUN_START", "run_id": "demo", "ts": "2025-01-01T00:00:00+00:00"},
        {"type": "SIGNAL", "signal_id": "s1", "ts": "2025-01-01T00:00:00+00:00", "direction": "LONG", "setup_id": "x", "instrument": "AUD_USD", "entry_ref": 1.0, "reason": "r", "features": {}, "strategy_version": "t"},
        {"type": "SUBMIT", "trade_id": "s1-t1", "signal_id": "s1", "ts": "2025-01-01T00:00:00+00:00", "stop": 0.99, "tp": 1.02, "risk_amount": 100.0, "units": 1},
        {"type": "EXIT", "trade_id": "s1-t1", "ts": "2025-01-01T01:00:00+00:00", "price": 1.02, "pnl": 200.0, "exit_reason": "TP"},
        {"type": "SIGNAL", "signal_id": "s2", "ts": "2025-01-02T00:00:00+00:00", "direction": "SHORT", "setup_id": "x", "instrument": "AUD_USD", "entry_ref": 1.0, "reason": "r", "features": {}, "strategy_version": "t"},
        {"type": "SUBMIT", "trade_id": "s2-t1", "signal_id": "s2", "ts": "2025-01-02T00:00:00+00:00", "stop": 1.01, "tp": 0.98, "risk_amount": 100.0, "units": -1},
        {"type": "EXIT", "trade_id": "s2-t1", "ts": "2025-01-02T01:00:00+00:00", "price": 1.01, "pnl": -100.0, "exit_reason": "STOP"},
    ]
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
        path = fh.name
    m = analyze(path)
    assert m["trades"] == 2, m
    assert abs(m["win_rate"] - 0.5) < 1e-9, m
    assert abs(m["expectancy"] - 50.0) < 1e-9, m  # (200 - 100) / 2
    assert abs(m["profit_factor"] - 2.0) < 1e-9, m  # 200 / 100
    assert abs(m["avg_win_R"] - 2.0) < 1e-9, m  # 200/100
    assert abs(m["avg_loss_R"] - (-1.0)) < 1e-9, m
    assert m["by_direction"]["LONG"]["n"] == 1 and m["by_direction"]["SHORT"]["n"] == 1, m
    # bootstrap: 2 trades (+200/-100) is luck-dominated -> unlucky 5th pct dips negative; a broad
    # all-winner set stays positive at the 5th pct. This is the gate's "is the edge real?" test.
    luck = bootstrap_expectancy(m["pnls"])
    assert luck["p05"] < 0 < luck["p50"], luck
    real = bootstrap_expectancy([80.0, 120.0, 95.0, 110.0, 90.0, 105.0, 100.0, 115.0])
    assert real["p05"] > 0, real
    print("analytics self-check OK:", {k: m[k] for k in ("trades", "expectancy", "profit_factor", "win_rate")}, "bootstrap OK")


def main() -> None:
    ap = argparse.ArgumentParser(prog="trading_agent.analytics")
    ap.add_argument("logfile")
    ap.add_argument("--candles", default=None, help="entry-TF candle CSV, enables MAE/MFE")
    ap.add_argument("--out", default="data/reports")
    args = ap.parse_args()
    path, m = render(args.logfile, args.out, args.candles)
    pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
    print(f"analysis: {m['trades']} trades, expectancy {m['expectancy']:.2f}, PF {pf}, win {m['win_rate']:.0%}, P/L {m['total_pnl']:.2f} -> {path}")


if __name__ == "__main__":
    main()
