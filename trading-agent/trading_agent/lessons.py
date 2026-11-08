"""Post-trade review: settle closed trades from JSONL event logs, turn each into a short lesson, keep them.

The roadmap's Phase-4 "post-trade analysis and lessons". Read-only with respect to the money path: it
reads event logs and writes markdown + `data/lessons.jsonl`, never config.

    uv run --group phase2 python -m trading_agent.lessons data/logs/events-live-*.jsonl --limit 20

1. Settle: replay each log into closed trades (total P/L incl. partials + swap, R = P/L / risk_amount,
   `resolved` = timestamp of the final exit). Open trades and backtest end-of-window liquidations are skipped.
2. Reflect: one LLM call per not-yet-reviewed trade -> 2-4 sentence lesson + optional testable hypothesis.
3. Record: append to `data/lessons.jsonl` (idempotent by run_id:trade_id), a dated section in
   `research/JOURNAL.md`, and any hypotheses to `research/HYPOTHESES.md` (which the research loop reads).

`LessonStore.past_context(as_of=...)` only returns lessons whose outcome was known by `as_of`, so a
backtest gate (agents.yaml `lessons: true`) never reads a trade that had not closed yet at that bar.

Adapted from TradingAgents (Apache-2.0, Tauric Research): memory/log.py, memory/settlement.py,
memory/reflection.py — https://github.com/TauricResearch/TradingAgents
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent  # trading-agent/
DEFAULT_STORE = _ROOT / "data" / "lessons.jsonl"
RESEARCH_DIR = _ROOT / "research"
_SKIP_EXIT_REASONS = {"END_OF_WINDOW"}  # backtest liquidation at the end of the data, not a trade outcome
_FEATURE_KEYS = ("style", "stack", "h4_close", "h4_cloud_bottom", "h4_cloud_top", "h1_tenkan", "h1_kijun", "m15_trigger", "dislocation_z")


@dataclass(frozen=True)
class ClosedTrade:
    key: str  # run_id:trade_id
    run_id: str
    trade_id: str
    source: str  # "backtest" | "live" | other run_id prefix
    instrument: str
    direction: str
    setup_id: str
    strategy_version: str
    opened: str  # SUBMIT ts
    resolved: str  # final EXIT ts: when the outcome became known
    pnl: float
    r_multiple: float | None
    exit_reason: str
    entry_ref: float | None
    stop: float | None
    tp: float | None
    features: dict = field(default_factory=dict)
    verdict: dict | None = None  # the LLM gate's verdict on this trade, when there was one


def _source(run_id: str) -> str:
    prefix = run_id.split("-", 1)[0]
    return {"bt": "backtest", "live": "live"}.get(prefix, prefix or "?")


def _segments(records: list[dict[str, Any]]) -> Iterable[list[dict[str, Any]]]:
    """EventLog appends, so a re-run of the same run_id lands in the same file: split at each RUN_START."""
    seg: list[dict[str, Any]] = []
    for r in records:
        if r.get("type") == "RUN_START" and seg:
            yield seg
            seg = []
        seg.append(r)
    if seg:
        yield seg


def _settle_segment(seg: list[dict[str, Any]]) -> list[ClosedTrade]:
    signals = {r["signal_id"]: r for r in seg if r.get("type") == "SIGNAL" and r.get("signal_id")}
    submits = {r["trade_id"]: r for r in seg if r.get("type") == "SUBMIT" and r.get("trade_id")}
    pnl: dict[str, float] = {}
    closed: dict[str, dict[str, Any]] = {}
    for r in seg:
        t, tid = r.get("type"), r.get("trade_id")
        if tid not in submits or t not in {"EXIT", "PARTIAL_EXIT", "FINANCING"}:
            continue
        pnl[tid] = pnl.get(tid, 0.0) + float(r.get("pnl") or 0.0)
        if t == "EXIT" or (t == "PARTIAL_EXIT" and r.get("remaining_units") == 0):
            closed[tid] = r
    out = []
    for tid, ex in closed.items():
        if ex.get("exit_reason") in _SKIP_EXIT_REASONS:
            continue
        sub = submits[tid]
        sig = signals.get(sub.get("signal_id"), {})
        risk = sub.get("risk_amount")
        run_id = str(sub.get("run_id", "?"))
        feats = sig.get("features") or {}
        out.append(ClosedTrade(
            key=f"{run_id}:{tid}", run_id=run_id, trade_id=tid, source=_source(run_id),
            instrument=sig.get("instrument", "?"), direction=sig.get("direction", "?"), setup_id=sig.get("setup_id", "?"),
            strategy_version=str(sub.get("strategy_version", "?")), opened=sub["ts"], resolved=ex["ts"],
            pnl=round(pnl[tid], 2), r_multiple=round(pnl[tid] / float(risk), 2) if risk else None,
            exit_reason=str(ex.get("exit_reason") or "?"), entry_ref=sig.get("entry_ref"), stop=sub.get("stop"), tp=sub.get("tp"),
            features={k: feats[k] for k in _FEATURE_KEYS if k in feats}, verdict=feats.get("llm_verdict"),
        ))
    return sorted(out, key=lambda t: t.resolved)


def settle(records: list[dict[str, Any]]) -> list[ClosedTrade]:
    """Closed trades in one run's event log, in close order. A re-run appended to the same file
    (a later RUN_START) replaces the earlier run entirely, including trades the re-run no longer takes."""
    segs = list(_segments(records))
    return _settle_segment(segs[-1]) if segs else []


def settle_logs(paths: Iterable[str | Path]) -> list[ClosedTrade]:
    """Backtest logs settle per file. Live logs settle as one stream: each restart starts a new log but the
    OANDA backend keeps tracking trades opened before it, so a trade's SUBMIT and EXIT can sit in different files
    (OANDA trade ids are account-unique, so merging is safe)."""
    trades: dict[str, ClosedTrade] = {}
    live: list[dict[str, Any]] = []
    for p in paths:
        records = [json.loads(line) for line in Path(p).read_text().splitlines() if line.strip()]
        if records and _source(str(records[0].get("run_id", ""))) == "live":
            live += records
            continue
        for t in settle(records):
            trades[t.key] = t
    for t in _settle_segment(sorted(live, key=lambda r: _ts(r["ts"]))):
        trades[t.key] = t
    return sorted(trades.values(), key=lambda t: t.resolved)


# --------------------------------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------------------------------


def _ts(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


class LessonStore:
    """Append-only JSONL of reviewed trades: the ClosedTrade fields plus `lesson` / `hypothesis`."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_STORE

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # skip a corrupt line rather than crash the gate
        return rows

    def keys(self) -> set[str]:
        return {r.get("key") for r in self.load()}

    def add(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")

    def past_context(self, instrument: str, as_of: datetime | None, n: int = 5) -> str:
        """The `n` most recent lessons on `instrument` whose outcome was known by `as_of` (None = no cutoff)."""
        rows = [r for r in self.load() if r.get("lesson") and r.get("instrument") == instrument and r.get("resolved")]
        if as_of is not None:
            rows = [r for r in rows if _ts(r["resolved"]) <= as_of]
        rows = sorted(rows, key=lambda r: _ts(r["resolved"]), reverse=True)[:n]
        return "\n".join(_lesson_line(r) for r in rows)


def _lesson_line(r: dict[str, Any]) -> str:
    rr = f"{r['r_multiple']:+.2f}R" if r.get("r_multiple") is not None else f"P/L {r.get('pnl')}"
    return f"- [{r['resolved'][:10]} {r.get('direction')} {r.get('setup_id')}, {rr}, {r.get('exit_reason')}] {r['lesson']}"


# --------------------------------------------------------------------------------------------------
# Reflection
# --------------------------------------------------------------------------------------------------

REFLECT_PROMPT = (
    "You are reviewing one closed trade from a disciplined, rule-based forex system (Ichimoku setups, code-owned "
    "risk; an optional LLM gate approves or vetoes each signal). The outcome is known.\n"
    "Write `lesson`: exactly 2-4 sentences of plain prose (no bullets, no markdown), covering in order:\n"
    "1. What the result in R says about this setup instance (cite the R-multiple and exit reason). One trade is a "
    "sample of one: say so if the outcome looks like noise rather than a flaw.\n"
    "2. Which entry context (trend cloud, pullback, trigger, dislocation, gate rationale) the outcome supports or undercuts.\n"
    "3. One concrete thing to check on the next similar setup.\n"
    "Set `hypothesis` to one testable sentence naming a strategy-config lever (e.g. rr, atr_mult, stop_rule, "
    "min_trend_distance_pips, require_chikou, breakeven/trail settings) ONLY if this trade points at one; otherwise null. "
    "Your output is stored verbatim and re-read by future gates and researchers, so every word must earn its place."
)


def reflection_input(t: ClosedTrade) -> str:
    rr = f"{t.r_multiple:+.2f}R" if t.r_multiple is not None else "n/a"
    verdict = json.dumps(t.verdict) if t.verdict else "none (no LLM gate on this run)"
    return (
        f"Trade {t.trade_id} ({t.source}, {t.strategy_version}): {t.direction} {t.instrument}, setup {t.setup_id}.\n"
        f"Opened {t.opened}, closed {t.resolved} by {t.exit_reason}.\n"
        f"Entry ref {t.entry_ref} stop {t.stop} take-profit {t.tp}. Result: P/L {t.pnl} = {rr}.\n"
        f"Entry context: {json.dumps(t.features)}\nGate verdict: {verdict}"
    )


Reflector = Callable[[ClosedTrade], dict[str, Any]]  # -> {"lesson": str, "hypothesis": str | None}


def llm_reflector(model: str) -> Reflector:
    """Structured-output reflector over `init_chat_model(model)` (phase2 group, e.g. anthropic:claude-haiku-4-5)."""
    from langchain.chat_models import init_chat_model
    from pydantic import BaseModel, Field

    class Lesson(BaseModel):
        lesson: str = Field(description="2-4 sentences of plain prose")
        hypothesis: str | None = Field(default=None, description="one testable config hypothesis, or null")

    llm = init_chat_model(model).with_structured_output(Lesson)

    def reflect(t: ClosedTrade) -> dict[str, Any]:
        out = llm.invoke([("system", REFLECT_PROMPT), ("human", reflection_input(t))])
        if out is None:
            raise ValueError("model returned no structured lesson")
        return out.model_dump()

    return reflect


# --------------------------------------------------------------------------------------------------
# Review pass
# --------------------------------------------------------------------------------------------------


@dataclass
class Review:
    reviewed: list[dict[str, Any]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (key, reason)
    pending: int = 0  # closed + unreviewed trades left for a later pass (over --limit)


def review(
    trades: list[ClosedTrade],
    reflect: Reflector,
    store: LessonStore,
    *,
    limit: int = 20,
    research_dir: Path | None = RESEARCH_DIR,
    now: datetime | None = None,
) -> Review:
    """Reflect on the `limit` most recently closed trades not yet in `store`. The research markdown is written
    before the store, so a failed write leaves the batch unreviewed (retried next pass) rather than losing it."""
    seen = store.keys()
    todo = [t for t in trades if t.key not in seen]
    batch = todo[-limit:] if limit > 0 else todo
    result = Review(pending=len(todo) - len(batch))
    for t in batch:
        try:
            out = reflect(t)
            lesson = str(out.get("lesson") or "").strip()
            if not lesson:
                raise ValueError("empty lesson")
        except Exception as e:  # noqa: BLE001 - one failed reflection leaves that trade for the next pass
            result.failed.append((t.key, str(e)))
            continue
        hyp = (out.get("hypothesis") or "").strip() or None
        result.reviewed.append({**asdict(t), "lesson": lesson, "hypothesis": hyp})
    if research_dir is not None and result.reviewed:
        write_research(result.reviewed, research_dir, now or datetime.now(UTC))
    for row in result.reviewed:
        store.add(row)
    return result


def write_research(rows: list[dict[str, Any]], research_dir: Path, now: datetime) -> None:
    """Append a dated section to JOURNAL.md and any hypotheses to HYPOTHESES.md (markdown only, never config)."""
    research_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"\n## Post-trade review {now:%Y-%m-%d %H:%M}Z ({len(rows)} trades)\n"]
    lines += [f"{_lesson_line(r)} `{r['key']}`" for r in rows]
    with (research_dir / "JOURNAL.md").open("a") as fh:
        fh.write("\n".join(lines) + "\n")
    hyps = [r for r in rows if r.get("hypothesis")]
    if hyps:
        with (research_dir / "HYPOTHESES.md").open("a") as fh:
            fh.write("".join(f"- (post-trade {r['resolved'][:10]}, {r['key']}) {r['hypothesis']}\n" for r in hyps))


def main() -> None:
    ap = argparse.ArgumentParser(prog="trading_agent.lessons", description="Reflect on closed trades and record lessons.")
    ap.add_argument("logs", nargs="+", help="JSONL event logs (data/logs/events-*.jsonl)")
    ap.add_argument("--model", default="anthropic:claude-haiku-4-5", help="reflector model (init_chat_model spec)")
    ap.add_argument("--limit", type=int, default=20, help="max trades reflected per pass, most recent first (0 = all)")
    ap.add_argument("--source", choices=["all", "live", "backtest"], default="all")
    ap.add_argument("--store", default=None, help=f"lesson store (default {DEFAULT_STORE})")
    ap.add_argument("--no-research", action="store_true", help="skip appending to research/JOURNAL.md and HYPOTHESES.md")
    ap.add_argument("--dry-run", action="store_true", help="list the trades that would be reviewed; no LLM calls, no writes")
    args = ap.parse_args()

    trades = [t for t in settle_logs(args.logs) if args.source == "all" or t.source == args.source]
    store = LessonStore(args.store)
    if args.dry_run:
        seen = store.keys()
        todo = [t for t in trades if t.key not in seen]
        for t in todo[-args.limit:] if args.limit > 0 else todo:
            print(reflection_input(t).splitlines()[0], f"-> {t.r_multiple}R")
        print(f"{len(todo)} unreviewed of {len(trades)} closed trades")
        return

    from trading_agent.run import _load_env

    _load_env()
    res = review(trades, llm_reflector(args.model), store, limit=args.limit, research_dir=None if args.no_research else RESEARCH_DIR)
    for key, why in res.failed:
        print(f"  failed {key}: {why}")
    print(f"reviewed {len(res.reviewed)}, failed {len(res.failed)}, left for later {res.pending} -> {store.path}")


if __name__ == "__main__":
    main()
