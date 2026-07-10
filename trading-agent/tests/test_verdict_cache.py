"""VerdictCache integration: an identical signal replays a cached verdict (0 LLM calls); refresh re-calls."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("pydantic")  # Verdict uses pydantic; only present under the phase2 group

from trading_agent.strategy.agent_strategy import AgentStrategy
from trading_agent.strategy.agents.schemas import Verdict
from trading_agent.strategy.agents.verdict_cache import VerdictCache
from trading_agent.strategy.base import StrategyResult
from trading_agent.types import Signal

NOW = datetime(2026, 1, 6, 8, 30, tzinfo=timezone.utc)


def _signal() -> Signal:
    return Signal(
        ts=NOW, signal_id="AUD_USD-x", instrument="AUD_USD", direction="LONG",
        entry_ref=0.67000, stop=0.66800, tp=0.67400, rr=2.0, setup_id="ichimoku_pullback",
        strategy_version="v1.0.0", features={"h4_close": 0.671}, reason="trend_pullback_long",
    )


class _Inner:
    version = "v1.0.0"

    def evaluate(self, snapshot, now):
        return StrategyResult(_signal(), "trend_pullback_long")


class _CountingOrch:
    def __init__(self, verdict: Verdict) -> None:
        self.verdict = verdict
        self.calls = 0

    def invoke(self, payload):
        self.calls += 1
        return {"structured_response": self.verdict}


def _agent(orch, cache):
    return AgentStrategy(_Inner(), orch, pair=None, authority="gate", cache=cache, orchestrator_model="m")


def test_second_run_replays_from_cache(tmp_path):
    v = Verdict(decision="approve", confidence=0.8, rationale="ok")
    path = tmp_path / "vc.jsonl"

    orch1 = _CountingOrch(v)
    _agent(orch1, VerdictCache(path)).evaluate({}, NOW)
    assert orch1.calls == 1  # cold: one real call, written to disk

    orch2 = _CountingOrch(v)  # fresh cache reading the same file, identical signal
    out = _agent(orch2, VerdictCache(path)).evaluate({}, NOW)
    assert orch2.calls == 0  # served from cache -> deterministic, zero tokens
    assert out.signal is not None


def test_refresh_bypasses_cache(tmp_path):
    v = Verdict(decision="approve", confidence=0.8, rationale="ok")
    path = tmp_path / "vc.jsonl"

    orch1 = _CountingOrch(v)
    _agent(orch1, VerdictCache(path)).evaluate({}, NOW)
    assert orch1.calls == 1

    orch2 = _CountingOrch(v)
    _agent(orch2, VerdictCache(path, refresh=True)).evaluate({}, NOW)
    assert orch2.calls == 1  # refresh ignores the populated cache and re-calls
