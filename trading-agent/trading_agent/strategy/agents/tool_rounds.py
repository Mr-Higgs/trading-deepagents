"""Cap an agent's tool rounds, then make it answer.

deepagents compiles every agent with a 9_999-step recursion limit, so an analyst that keeps calling tools
burns tokens until the gate times out and the signal ends as `llm_error:gate_unavailable` (no trade). After
`max_rounds` model turns that called tools, this middleware strips the agent's tools and asks for its final
answer; when the agent has a structured `response_format`, that answer is forced through it.

Adapted from TradingAgents (Apache-2.0, Tauric Research): graph/setup.py `_analyst_graph` wrap-up node,
agents/analysts/turn.py `WRAP_UP` — https://github.com/TauricResearch/TradingAgents
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, HumanMessage

log = logging.getLogger(__name__)

WRAP_UP = (
    "You have used every tool round this task allows. Give your final answer now from the tool results above, "
    "and say which data you could not retrieve."
)


def tool_rounds(messages: list[Any]) -> int:
    return sum(1 for m in messages if isinstance(m, AIMessage) and m.tool_calls)


class ToolRoundCap(AgentMiddleware):
    """After `max_rounds` tool-calling turns, the next model call gets no tools and a wrap-up instruction."""

    def __init__(self, max_rounds: int, label: str = "agent") -> None:
        super().__init__()
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")
        self.max_rounds = max_rounds
        self.label = label

    def _capped(self, request: ModelRequest) -> ModelRequest:
        if tool_rounds(request.messages) < self.max_rounds:
            return request
        calls = Counter(c["name"] for m in request.messages if isinstance(m, AIMessage) for c in m.tool_calls)
        log.warning("%s used its %d tool rounds (%s); asking for its answer", self.label, self.max_rounds,
                    ", ".join(f"{n} x{k}" for n, k in calls.most_common()))
        overrides: dict[str, Any] = {"tools": [], "messages": [*request.messages, HumanMessage(WRAP_UP)]}
        if request.response_format is not None:
            # ToolStrategy keeps the structured-output tool bound (forced via tool_choice) even with no other tools,
            # so the answer still parses and providers that require tool definitions for a tool-use history accept it.
            overrides["response_format"] = ToolStrategy(schema=getattr(request.response_format, "schema", request.response_format))
        return request.override(**overrides)

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        return handler(self._capped(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        return await handler(self._capped(request))
