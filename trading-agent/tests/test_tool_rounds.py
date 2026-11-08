"""Tool-round cap: a model that never stops calling tools is made to answer. Offline (fake chat model)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("langchain")

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from trading_agent.strategy.agents import orchestrator
from trading_agent.strategy.agents.schemas import AnalystView
from trading_agent.strategy.agents.tool_rounds import WRAP_UP, ToolRoundCap


@tool
def lookup() -> str:
    """Fetch a datum."""
    return "42"


class _Looper(BaseChatModel):
    """Calls `lookup` whenever it is bound; otherwise answers through whatever tool is left (the structured one)."""

    bound: list[str] = []
    seen: list[list[str]] = []

    @property
    def _llm_type(self) -> str:
        return "looper"

    def bind_tools(self, tools: Any, **kw: Any):  # noqa: ARG002
        names = [t.name if hasattr(t, "name") else t["function"]["name"] if "function" in t else t["name"] for t in tools]
        return self.model_copy(update={"bound": names})

    def _generate(self, messages, stop=None, run_manager=None, **kw):  # noqa: ARG002
        self.seen.append(list(self.bound))
        n = len(messages)
        if "lookup" in self.bound:
            msg = AIMessage("", tool_calls=[{"name": "lookup", "args": {}, "id": f"c{n}"}])
        elif self.bound:
            args = {"stance": "neutral", "confidence": 0.5, "note": "wrapped up" if WRAP_UP in str(messages[-1].content) else "?"}
            msg = AIMessage("", tool_calls=[{"name": self.bound[0], "args": args, "id": f"c{n}"}])
        else:
            msg = AIMessage("plain answer")
        return ChatResult(generations=[ChatGeneration(message=msg)])


def test_looping_agent_is_forced_to_its_structured_answer():
    model = _Looper(seen=[])
    agent = create_agent(model, tools=[lookup], response_format=AnalystView, middleware=[ToolRoundCap(3)])
    out = agent.invoke({"messages": [HumanMessage("judge it")]}, {"recursion_limit": 30})
    assert out["structured_response"].note == "wrapped up"
    assert [("lookup" in b) for b in model.seen] == [True, True, True, False]  # 3 tool rounds, then the forced answer


def test_without_response_format_the_capped_call_has_no_tools():
    model = _Looper(seen=[])
    agent = create_agent(model, tools=[lookup], middleware=[ToolRoundCap(2)])
    out = agent.invoke({"messages": [HumanMessage("go")]}, {"recursion_limit": 30})
    assert out["messages"][-1].content == "plain answer" and model.seen[-1] == []


def test_cap_must_be_positive():
    with pytest.raises(ValueError):
        ToolRoundCap(0)


def test_build_orchestrator_caps_every_agent(monkeypatch):
    captured: dict[str, Any] = {}
    monkeypatch.setattr(orchestrator, "init_chat_model", lambda name: name)
    monkeypatch.setattr(orchestrator, "create_deep_agent", lambda **kw: captured.update(kw) or "graph")
    agents = SimpleNamespace(orchestrator_model="anthropic:x", analyst_model="anthropic:y", authority="gate", veto_default=False,
                             technical=True, fundamental=True, sentiment=True, cftc_code="232741", max_tool_rounds=4)
    assert orchestrator.build_orchestrator(SimpleNamespace(agents=agents, strategy=SimpleNamespace(instrument="AUD_USD"))) == "graph"
    caps = [m for s in captured["subagents"] for m in s["middleware"]] + list(captured["middleware"])
    assert [(c.label, c.max_rounds) for c in caps] == [
        ("technical-analyst", 4), ("fundamental-analyst", 4), ("sentiment-analyst", 4), ("orchestrator", 4)]
