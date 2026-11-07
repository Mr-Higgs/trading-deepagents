"""Devin CLI gate adapter, offline: a fake `run` stands in for the devin subprocess."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("langchain_core")  # analyst tools are langchain @tools; phase2 group only

from trading_agent.strategy.agent_strategy import AgentStrategy
from trading_agent.strategy.agents import devin_gate
from trading_agent.strategy.agents.devin_gate import DevinCliGate, parse_verdict
from trading_agent.strategy.base import StrategyResult
from trading_agent.types import Signal

NOW = datetime(2026, 1, 6, 8, 30, tzinfo=UTC)
SIG = Signal(
    ts=NOW, signal_id="AUD_USD-x", instrument="AUD_USD", direction="LONG", entry_ref=0.67, stop=0.668, tp=0.674,
    rr=2.0, setup_id="ichimoku_pullback", strategy_version="v1", features={}, reason="trend_pullback_long",
)


class _Inner:
    version = "v1"

    def evaluate(self, snapshot, now):
        return StrategyResult(SIG, "trend_pullback_long")


def _cfg(authority: str = "gate"):
    agents = SimpleNamespace(orchestrator_model="devin-cli:swe-2-high", authority=authority, veto_default=False,
                             fundamental=False, sentiment=False, cftc_code="232741")  # no analysts -> no network
    return SimpleNamespace(agents=agents, strategy=SimpleNamespace(instrument="AUD_USD"))


def _strategy(reply: str | Exception) -> tuple[AgentStrategy, list]:
    calls = []

    def run(model, prompt):
        calls.append((model, prompt))
        if isinstance(reply, Exception):
            raise reply
        return reply

    return AgentStrategy(_Inner(), DevinCliGate(_cfg(), run=run), pair=None), calls


def test_parse_verdict_tolerates_chatter_around_the_json():
    v = parse_verdict('Sure.\n```json\n{"decision": "veto", "confidence": 0.7, "rationale": "RBA in 20m"}\n```')
    assert v.decision == "veto" and v.confidence == 0.7


def test_veto_from_devin_blocks_the_trade():
    strat, calls = _strategy('{"decision": "veto", "confidence": 0.6, "rationale": "CPI in 30 minutes"}')
    out = strat.evaluate({}, NOW)
    assert out.signal is None and out.reason.startswith("llm_veto:") and "CPI" in out.reason
    assert calls[0][0] == "swe-2-high"  # prefix stripped before it reaches --model


def test_approve_keeps_code_prices():
    strat, _ = _strategy('{"decision": "approve", "confidence": 0.8, "rationale": "clean"}')
    out = strat.evaluate({}, NOW)
    assert (out.signal.entry_ref, out.signal.stop, out.signal.tp) == (0.67, 0.668, 0.674)


@pytest.mark.parametrize("reply", ["I think it's fine!", '{"decision": "approve"}', RuntimeError("devin exited 1")])
def test_garbage_or_failure_fails_closed(reply):
    strat, _ = _strategy(reply)
    out = strat.evaluate({}, NOW)
    assert out.signal is None and out.reason == "llm_error:gate_unavailable"


def test_devin_never_sees_broker_or_model_secrets(monkeypatch):
    monkeypatch.setenv("OANDA_API_KEY", "x")
    monkeypatch.setenv("OANDA_DEMO_ACCOUNT_ID", "101-x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    env = devin_gate._scrubbed_env()
    assert not any(k.startswith("OANDA_") for k in env) and "ANTHROPIC_API_KEY" not in env


def test_as_graph_serves_the_gate_for_langgraph_dev():
    gate = DevinCliGate(_cfg(), run=lambda model, prompt: '{"decision": "veto", "confidence": 0.6, "rationale": "NFP"}')
    out = gate.as_graph().invoke({"messages": [{"role": "user", "content": "LONG AUD_USD @ 0.67"}]})
    assert out["structured_response"]["decision"] == "veto" and '"veto"' in out["messages"][-1].content
