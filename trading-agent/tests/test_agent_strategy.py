"""AgentStrategy gate logic, fully offline: stub inner strategy + stub orchestrator, no LLM/network."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("pydantic")  # schemas.Verdict uses pydantic; only present under the phase2 group

from trading_agent.strategy.agent_strategy import AgentStrategy
from trading_agent.strategy.agents.schemas import Verdict
from trading_agent.strategy.base import StrategyResult
from trading_agent.types import Signal

NOW = datetime(2026, 1, 6, 8, 30, tzinfo=timezone.utc)


def _signal(direction: str = "LONG") -> Signal:
    return Signal(
        ts=NOW, signal_id="AUD_USD-x", instrument="AUD_USD", direction=direction,
        entry_ref=0.67000, stop=0.66800, tp=0.67400, rr=2.0, setup_id="ichimoku_pullback",
        strategy_version="v1.0.0", features={"h4_close": 0.671}, reason="trend_pullback_long",
    )


class _StubInner:
    version = "v1.0.0"

    def __init__(self, result: StrategyResult) -> None:
        self._result = result

    def evaluate(self, snapshot, now):
        return self._result


class _StubOrch:
    def __init__(self, verdict: Verdict | None = None, raises: bool = False) -> None:
        self._verdict = verdict
        self._raises = raises
        self.called = False

    def invoke(self, payload):
        self.called = True
        if self._raises:
            raise RuntimeError("boom")
        return {"structured_response": self._verdict}


def test_no_candidate_skips_the_llm():
    orch = _StubOrch()
    out = AgentStrategy(_StubInner(StrategyResult(None, "no_setup")), orch, pair=None).evaluate({}, NOW)
    assert out.signal is None
    assert out.reason == "no_setup"
    assert orch.called is False  # no candidate => no tokens spent


def test_approve_returns_same_prices_plus_verdict():
    sig = _signal()
    orch = _StubOrch(Verdict(decision="approve", confidence=0.8, rationale="analysts agree"))
    out = AgentStrategy(_StubInner(StrategyResult(sig, "trend_pullback_long")), orch, pair=None).evaluate({}, NOW)
    assert out.signal is not None
    assert (out.signal.entry_ref, out.signal.stop, out.signal.tp) == (sig.entry_ref, sig.stop, sig.tp)  # LLM never touches prices
    assert out.signal.features["llm_verdict"]["decision"] == "approve"
    assert out.reason == "trend_pullback_long"


def test_veto_blocks_and_records_reason():
    orch = _StubOrch(Verdict(decision="veto", confidence=0.7, rationale="RBA rate decision in 20 minutes"))
    out = AgentStrategy(_StubInner(StrategyResult(_signal(), "trend_pullback_long")), orch, pair=None).evaluate({}, NOW)
    assert out.signal is None
    assert out.reason.startswith("llm_veto:")
    assert "RBA rate decision" in out.reason


def test_llm_failure_fails_closed():
    orch = _StubOrch(raises=True)
    out = AgentStrategy(_StubInner(StrategyResult(_signal(), "trend_pullback_long")), orch, pair=None).evaluate({}, NOW)
    assert out.signal is None
    assert out.reason.startswith("llm_error:")


def test_dict_structured_response_is_tolerated():
    orch = _StubOrch()
    orch._verdict = {"decision": "approve", "confidence": 0.6, "rationale": "ok"}  # dict, not a Verdict instance
    out = AgentStrategy(_StubInner(StrategyResult(_signal(), "trend_pullback_long")), orch, pair=None).evaluate({}, NOW)
    assert out.signal is not None
    assert out.signal.features["llm_verdict"]["confidence"] == 0.6


def _agent_signal(orch) -> AgentStrategy:
    return AgentStrategy(_StubInner(StrategyResult(_signal(), "trend_pullback_long")), orch, pair=None, authority="signal")


def test_signal_modify_applies_new_prices_and_recomputes_rr():
    orch = _StubOrch(Verdict(decision="modify", confidence=0.7, rationale="tighter stop fits the pullback", entry=0.67050, stop=0.66850, tp=0.67500))
    out = _agent_signal(orch).evaluate({}, NOW)
    assert out.signal is not None
    assert (out.signal.entry_ref, out.signal.stop, out.signal.tp) == (0.67050, 0.66850, 0.67500)
    assert out.signal.rr == round((0.67500 - 0.67050) / (0.67050 - 0.66850), 4)  # code recomputes RR, never trusts the LLM's
    assert out.signal.features["llm_verdict"]["decision"] == "modify"


def test_signal_modify_omitted_field_keeps_baseline():
    sig = _signal()
    orch = _StubOrch(Verdict(decision="modify", confidence=0.6, rationale="just move the target", tp=0.67600))  # entry/stop/direction omitted
    out = _agent_signal(orch).evaluate({}, NOW)
    assert out.signal is not None
    assert (out.signal.entry_ref, out.signal.stop) == (sig.entry_ref, sig.stop)  # unset fields keep the deterministic value
    assert out.signal.tp == 0.67600


def test_signal_modify_invalid_ordering_fails_closed():
    orch = _StubOrch(Verdict(decision="modify", confidence=0.9, rationale="bad", stop=0.67500))  # stop above entry for a LONG
    out = _agent_signal(orch).evaluate({}, NOW)
    assert out.signal is None
    assert out.reason.startswith("llm_invalid_signal:")


def test_signal_modify_absurd_price_fails_closed():
    orch = _StubOrch(Verdict(decision="modify", confidence=0.9, rationale="fat finger", entry=5.0, stop=4.9, tp=5.2))  # miles off baseline
    out = _agent_signal(orch).evaluate({}, NOW)
    assert out.signal is None
    assert out.reason.startswith("llm_invalid_signal:")


def test_signal_authority_still_vetoes():
    orch = _StubOrch(Verdict(decision="veto", confidence=0.7, rationale="news risk"))
    out = _agent_signal(orch).evaluate({}, NOW)
    assert out.signal is None
    assert out.reason.startswith("llm_veto:")


def test_gate_mode_treats_modify_as_veto():
    # In gate mode the LLM has no price authority; a stray "modify" must never change prices -> no trade.
    orch = _StubOrch(Verdict(decision="modify", confidence=0.9, rationale="ignored", entry=0.6, stop=0.5, tp=0.8))
    out = AgentStrategy(_StubInner(StrategyResult(_signal(), "trend_pullback_long")), orch, pair=None, authority="gate").evaluate({}, NOW)
    assert out.signal is None
    assert out.reason.startswith("llm_veto:")
