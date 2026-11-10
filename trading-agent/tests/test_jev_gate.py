"""Jev (TypeSafe) gate adapter, offline: the real SDK client talks to an in-process mock transport."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("langchain_core")  # analyst tools are langchain @tools; phase2 group only
httpx2 = pytest.importorskip("httpx2")
typesafe_sdk = pytest.importorskip("typesafe_sdk")

from trading_agent.strategy.agent_strategy import AgentStrategy
from trading_agent.strategy.agents.jev_gate import JevGate, signal_facts, to_verdict
from trading_agent.strategy.agents.verdict_cache import VerdictCache
from trading_agent.strategy.base import StrategyResult
from trading_agent.types import Signal

NOW = datetime(2026, 1, 6, 8, 30, tzinfo=UTC)
FEATURES = {"h4_close": 0.671, "h4_cloud_bottom": 0.660, "h4_cloud_top": 0.665, "h1_close": 0.670, "h1_tenkan": 0.6695,
            "h1_kijun": 0.669, "dislocation_z": 2.4}
SIG = Signal(
    ts=NOW, signal_id="AUD_USD-x", instrument="AUD_USD", direction="LONG", entry_ref=0.67, stop=0.668, tp=0.674,
    rr=2.0, setup_id="ichimoku_pullback", strategy_version="v1", features=FEATURES, reason="trend_pullback_long",
)


class _Inner:
    version = "v1"

    def evaluate(self, snapshot, now):
        return StrategyResult(SIG, "trend_pullback_long")


def _cfg(authority: str = "gate", *, veto_default: bool = False, threshold: float | None = None):
    agents = SimpleNamespace(orchestrator_model="typesafe:jev-1.13.0", authority=authority, veto_default=veto_default,
                             fundamental=False, sentiment=False, cftc_code="232741", jev_threshold=threshold)  # no analysts -> no network
    return SimpleNamespace(agents=agents, strategy=SimpleNamespace(instrument="AUD_USD"))


def _client(p_take: float | Exception, seen: list) -> typesafe_sdk.TypeSafeClient:
    def handle(request):
        seen.append(json.loads(request.content))
        if isinstance(p_take, Exception):
            raise p_take
        answers = {q: {"type": "noul", "noul": p_take if q == "take_trade" else 0.1} for q in seen[-1]["questions"]}
        return httpx2.Response(200, json={"model": "jev-1.13.0", "usage": {"input_tokens": 100}, "answers": answers})

    return typesafe_sdk.TypeSafeClient(api_key="test", model="jev-1.13.0", transport=httpx2.MockTransport(handle),
                                       retry=typesafe_sdk.RetryPolicy(max_retries=0))


def _strategy(p_take: float | Exception, cfg=None, cache=None) -> tuple[AgentStrategy, list]:
    seen: list = []
    gate = JevGate(cfg or _cfg(), client=_client(p_take, seen))
    return AgentStrategy(_Inner(), gate, pair=None, cache=cache, orchestrator_model="typesafe:jev-1.13.0"), seen


def test_approve_keeps_code_prices_and_logs_probability():
    strat, seen = _strategy(0.72)
    out = strat.evaluate({}, NOW)
    assert (out.signal.entry_ref, out.signal.stop, out.signal.tp) == (0.67, 0.668, 0.674)
    verdict = out.signal.features["llm_verdict"]
    assert verdict["decision"] == "approve" and verdict["confidence"] == 0.72 and "p_take=0.72" in verdict["rationale"]
    assert seen[0]["model"] == "jev-1.13.0" and seen[0]["questions"]["take_trade"]["type"] == "noul"


def test_low_probability_vetoes():
    strat, _ = _strategy(0.3)
    out = strat.evaluate({}, NOW)
    assert out.signal is None and out.reason.startswith("llm_veto:0.70:") and "p_take=0.30" in out.reason


@pytest.mark.parametrize(("p", "decision"), [(0.5, "approve"), (0.4999, "veto")])
def test_threshold_boundary_is_inclusive(p, decision):
    assert to_verdict({"take_trade": p}, 0.5).decision == decision


def test_veto_default_and_explicit_threshold():
    assert JevGate(_cfg(veto_default=True), client=object()).threshold == 0.65
    assert JevGate(_cfg(veto_default=True, threshold=0.4), client=object()).threshold == 0.4
    strat, _ = _strategy(0.6, cfg=_cfg(veto_default=True))
    assert strat.evaluate({}, NOW).signal is None  # 0.6 passes the default bar but not the veto-biased one


def test_api_failure_fails_closed():
    strat, _ = _strategy(httpx2.ConnectError("down"))
    out = strat.evaluate({}, NOW)
    assert out.signal is None and out.reason == "llm_error:gate_unavailable"


def test_signal_authority_is_refused():
    with pytest.raises(ValueError, match="authority 'gate' only"):
        JevGate(_cfg("signal"), client=object())


def test_state_carries_code_computed_facts():
    strat, seen = _strategy(0.8)
    strat.evaluate({}, NOW)
    facts = seen[0]["state"]["facts"]
    assert facts["trend_close_vs_cloud"] == "above" and facts["trend_cloud_agrees_with_direction"] is True
    assert facts["bias_tenkan_vs_kijun"] == "above" and facts["price_dislocation"] == "stretched_up"
    assert facts["dislocation_against_direction"] is True  # LONG into a +2.4 sigma stretch is chasing


def test_facts_omit_missing_inputs():
    bare = replace(SIG, features={}, direction="SHORT", rr=1.5)
    assert signal_facts(bare) == {"direction": "SHORT", "reward_to_risk_at_least_2": False}


def test_cache_replays_without_calling_jev(tmp_path):
    cache = VerdictCache(tmp_path / "vc.jsonl")
    strat, seen = _strategy(0.9, cache=cache)
    strat.evaluate({}, NOW)
    strat.evaluate({}, NOW)
    assert len(seen) == 1


def test_build_orchestrator_dispatches_and_graph_wraps_it():
    from trading_agent.graph import _wrap
    from trading_agent.strategy.agents.orchestrator import build_orchestrator

    assert isinstance(build_orchestrator(_cfg()), JevGate)
    seen: list = []
    gate = JevGate(_cfg(), client=_client(0.2, seen))
    out = _wrap(gate).invoke({"messages": [{"role": "user", "content": "LONG AUD_USD @ 0.67"}]})
    assert out["structured_response"].decision == "veto" and seen[0]["state"]["facts"] == {}
