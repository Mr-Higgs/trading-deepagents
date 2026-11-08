"""LangGraph server entry: exposes the Phase-2 confirmation gate over HTTP for the Deep Agents UI.

`langgraph dev` loads `.env` (per langgraph.json) before importing this module, so ANTHROPIC_API_KEY
is present when build_orchestrator -> init_chat_model runs. This is the SAME compiled graph the CLI
invokes in `AgentStrategy._gate`; here it's just served instead of called in-process.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.graph import END, START, MessagesState, StateGraph

from trading_agent.config import load_config
from trading_agent.strategy.agents.orchestrator import build_orchestrator

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"  # trading-agent/config, cwd-independent
_gate = build_orchestrator(load_config(_CONFIG_DIR))  # agents.yaml -> opus gate + haiku analysts


class GateState(MessagesState):
    structured_response: Any


def _wrap(gate):
    """devin-cli gates are plain objects with .invoke(); give the server a one-node graph around them."""
    g = StateGraph(GateState)
    g.add_node("gate", lambda s: gate.invoke({"messages": [{"role": m.type, "content": m.content} for m in s["messages"]]}))
    g.add_edge(START, "gate")
    g.add_edge("gate", END)
    return g.compile()


agent = _gate if hasattr(_gate, "get_graph") else _wrap(_gate)

