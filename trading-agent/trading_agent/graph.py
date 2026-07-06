"""LangGraph server entry: exposes the Phase-2 confirmation gate over HTTP for the Deep Agents UI.

`langgraph dev` loads `.env` (per langgraph.json) before importing this module, so ANTHROPIC_API_KEY
is present when build_orchestrator -> init_chat_model runs. This is the SAME compiled graph the CLI
invokes in `AgentStrategy._gate`; here it's just served instead of called in-process.
"""

from __future__ import annotations

from pathlib import Path

from trading_agent.config import load_config
from trading_agent.strategy.agents.orchestrator import build_orchestrator

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"  # trading-agent/config, cwd-independent
agent = build_orchestrator(load_config(_CONFIG_DIR))  # agents.yaml -> opus gate + haiku analysts

