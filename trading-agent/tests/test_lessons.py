"""Post-trade lessons: settlement from JSONL, point-in-time recall, review pass, gate prompt injection. Offline."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from trading_agent.lessons import LessonStore, review, settle, settle_logs, write_research


def _rec(ts: str, type_: str, run_id: str = "live-1", **kw) -> dict:
    return {"ts": ts, "run_id": run_id, "strategy_version": "v2", "type": type_, **kw}


def _log(run_id: str = "live-1") -> list[dict]:
    r = lambda ts, t, **kw: _rec(ts, t, run_id, **kw)
    return [
        r("2026-07-01T07:00:00+00:00", "RUN_START"),
        r("2026-07-01T08:00:00+00:00", "SIGNAL", signal_id="s1", instrument="AUD_USD", direction="LONG", setup_id="ichimoku_pullback",
          entry_ref=0.67, features={"style": "swing", "h4_close": 0.671, "noise": 1, "llm_verdict": {"decision": "approve"}}),
        r("2026-07-01T08:00:00+00:00", "SUBMIT", trade_id="s1-t1", signal_id="s1", units=1000, stop=0.668, tp=0.674, risk_amount=50.0),
        r("2026-07-01T10:00:00+00:00", "PARTIAL_EXIT", trade_id="s1-t1", pnl=30.0, exit_reason="SCALE_OUT", remaining_units=500),
        r("2026-07-01T21:00:00+00:00", "FINANCING", trade_id="s1-t1", pnl=-1.0),
        r("2026-07-02T09:00:00+00:00", "EXIT", trade_id="s1-t1", pnl=46.0, exit_reason="TP", remaining_units=0),
        # still open at the end of the log
        r("2026-07-02T10:00:00+00:00", "SIGNAL", signal_id="s2", instrument="AUD_USD", direction="SHORT", setup_id="ichimoku_pullback", entry_ref=0.67),
        r("2026-07-02T10:00:00+00:00", "SUBMIT", trade_id="s2-t1", signal_id="s2", units=1000, stop=0.672, tp=0.666, risk_amount=50.0),
    ]


def test_settle_totals_partials_and_swap_and_skips_open():
    [t] = settle(_log())
    assert t.key == "live-1:s1-t1" and t.source == "live"
    assert t.pnl == 75.0 and t.r_multiple == 1.5
    assert t.resolved == "2026-07-02T09:00:00+00:00" and t.exit_reason == "TP"
    assert t.features == {"style": "swing", "h4_close": 0.671}  # only the whitelisted context keys
    assert t.verdict == {"decision": "approve"}


def test_settle_skips_end_of_window_liquidation_and_keeps_last_rerun():
    log = _log("bt-a")
    log[-3]["exit_reason"] = "END_OF_WINDOW"
    assert settle(log) == []
    rerun = _log("bt-a")  # same run_id appended to the same file: the later segment wins, not a sum
    assert [t.pnl for t in settle(log + rerun)] == [75.0]
    assert settle(rerun)[0].source == "backtest"
    dropped = [r for r in _log("bt-a") if r.get("trade_id") != "s1-t1"]  # the re-run no longer takes s1
    assert settle(_log("bt-a") + dropped) == []


def test_settle_logs_reads_files(tmp_path):
    p = tmp_path / "events.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in _log()) + "\n")
    assert [t.key for t in settle_logs([p])] == ["live-1:s1-t1"]


def test_settle_logs_joins_a_live_trade_across_a_restart(tmp_path):
    log = _log("live-1")
    a, b = tmp_path / "events-live-1.jsonl", tmp_path / "events-live-2.jsonl"
    a.write_text("\n".join(json.dumps(r) for r in log[:4]) + "\n")  # stopped after the partial exit
    restart = [_rec("2026-07-01T20:00:00+00:00", "RUN_START", "live-2")] + [{**r, "run_id": "live-2"} for r in log[4:6]]
    b.write_text("\n".join(json.dumps(r) for r in restart) + "\n")
    [t] = settle_logs([b, a])
    assert t.key == "live-1:s1-t1" and t.pnl == 75.0 and t.resolved == "2026-07-02T09:00:00+00:00"


def test_settle_logs_keeps_live_trades_apart_when_a_replayed_candle_reuses_the_trade_id(tmp_path):
    a, b = tmp_path / "events-live-1.jsonl", tmp_path / "events-live-2.jsonl"
    a.write_text("\n".join(json.dumps(r) for r in _log("live-1")[:6]) + "\n")
    later = [{**r, "ts": r["ts"].replace("2026-07-0", "2026-07-1")} for r in _log("live-2")[:6]]  # same signal/trade ids
    later[-1]["pnl"] = -50.0
    b.write_text("\n".join(json.dumps(r) for r in later) + "\n")
    assert [(t.key, t.pnl) for t in settle_logs([a, b])] == [("live-1:s1-t1", 75.0), ("live-2:s1-t1", -21.0)]


def _row(key: str, resolved: str, lesson: str = "L", instrument: str = "AUD_USD") -> dict:
    return {"key": key, "resolved": resolved, "instrument": instrument, "direction": "LONG", "setup_id": "x", "r_multiple": 1.0,
            "exit_reason": "TP", "lesson": lesson}


def test_past_context_is_point_in_time_and_most_recent_first(tmp_path):
    store = LessonStore(tmp_path / "l.jsonl")
    store.add(_row("a", "2026-07-01T09:00:00+00:00", "old"))
    store.add(_row("b", "2026-07-03T09:00:00+00:00", "new"))
    store.add(_row("c", "2026-07-10T09:00:00+00:00", "future"))
    store.add(_row("d", "2026-07-02T09:00:00+00:00", "other pair", instrument="EUR_USD"))
    ctx = store.past_context("AUD_USD", as_of=datetime(2026, 7, 5, tzinfo=UTC))
    assert "future" not in ctx and "other pair" not in ctx
    assert ctx.index("new") < ctx.index("old")
    assert store.past_context("AUD_USD", as_of=datetime(2026, 7, 5, tzinfo=UTC), n=1).count("\n") == 0
    assert "future" in store.past_context("AUD_USD", as_of=None)


def test_review_is_idempotent_records_failures_and_writes_research(tmp_path):
    trades = settle(_log()) + settle(_log("live-2"))
    store = LessonStore(tmp_path / "l.jsonl")
    calls = []

    def reflect(t):
        calls.append(t.key)
        if t.run_id == "live-2":
            raise RuntimeError("provider down")
        return {"lesson": "Pullback held the kijun; let the runner trail.", "hypothesis": "Test trail_buffer_pips in 8-16."}

    research = tmp_path / "research"
    res = review(trades, reflect, store, research_dir=research, now=datetime(2026, 7, 3, tzinfo=UTC))
    assert [r["key"] for r in res.reviewed] == ["live-1:s1-t1"] and [k for k, _ in res.failed] == ["live-2:s1-t1"]
    assert "Post-trade review 2026-07-03" in (research / "JOURNAL.md").read_text()
    assert "trail_buffer_pips" in (research / "HYPOTHESES.md").read_text()

    calls.clear()
    review(trades, reflect, store, research_dir=None)
    assert calls == ["live-2:s1-t1"]  # the reviewed trade is not paid for twice; the failed one is retried


def test_review_failed_research_write_leaves_trades_unreviewed(tmp_path):
    store = LessonStore(tmp_path / "l.jsonl")
    blocked = tmp_path / "research"
    blocked.write_text("not a dir")
    with pytest.raises(OSError):
        review(settle(_log()), lambda t: {"lesson": "x", "hypothesis": "h"}, store, research_dir=blocked)
    assert store.load() == []


def test_write_research_skips_rows_already_written(tmp_path):
    rows = [{**_row("live-1:s1-t1", "2026-07-02T09:00:00+00:00"), "hypothesis": "Test trail_buffer_pips."}]
    for _ in range(2):  # e.g. the store write failed after the markdown, so the batch is retried
        write_research(rows, tmp_path, datetime(2026, 7, 3, tzinfo=UTC))
    assert (tmp_path / "JOURNAL.md").read_text().count("live-1:s1-t1") == 1
    assert (tmp_path / "HYPOTHESES.md").read_text().count("trail_buffer_pips") == 1


def test_review_limit_takes_most_recent(tmp_path):
    trades = settle(_log("live-1")) + settle(_log("live-2"))
    res = review(trades, lambda t: {"lesson": "x"}, LessonStore(tmp_path / "l.jsonl"), limit=1, research_dir=None)
    assert len(res.reviewed) == 1 and res.pending == 1


# --- gate prompt injection -------------------------------------------------------------------------

pytest.importorskip("pydantic")

from trading_agent.strategy.agent_strategy import AgentStrategy
from trading_agent.strategy.agents.schemas import Verdict
from trading_agent.strategy.base import StrategyResult
from trading_agent.types import Signal

NOW = datetime(2026, 7, 5, 8, tzinfo=UTC)


class _Inner:
    version = "v2"

    def evaluate(self, snapshot, now):
        sig = Signal(ts=now, signal_id="x", instrument="AUD_USD", direction="LONG", entry_ref=0.67, stop=0.668, tp=0.674, rr=2.0,
                     setup_id="ichimoku_pullback", strategy_version="v2", features={}, reason="r")
        return StrategyResult(sig, "r")


class _Orch:
    def __init__(self):
        self.prompts = []

    def invoke(self, payload):
        self.prompts.append(payload["messages"][-1]["content"])
        return {"structured_response": Verdict(decision="approve", confidence=0.7, rationale="ok")}


def test_gate_prompt_gets_only_lessons_known_by_bar_time(tmp_path):
    store = LessonStore(tmp_path / "l.jsonl")
    store.add(_row("a", "2026-07-01T09:00:00+00:00", "kijun held"))
    store.add(_row("b", "2026-07-06T09:00:00+00:00", "from the future"))
    orch = _Orch()
    AgentStrategy(_Inner(), orch, pair=None, point_in_time=True, lessons=store).evaluate({}, NOW)
    assert "kijun held" in orch.prompts[0] and "from the future" not in orch.prompts[0]


def test_gate_prompt_unchanged_without_lessons(tmp_path):
    with_store, without = _Orch(), _Orch()
    AgentStrategy(_Inner(), with_store, pair=None, lessons=LessonStore(tmp_path / "empty.jsonl")).evaluate({}, NOW)
    AgentStrategy(_Inner(), without, pair=None).evaluate({}, NOW)
    assert with_store.prompts == without.prompts  # empty store => same prompt => existing cache keys still hit
