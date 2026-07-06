"""Build the deepagents confirmation-gate orchestrator + 3 analyst sub-agents.

Copied shape from `examples/nvidia_deep_agent/src/agent.py`: declarative sub-agent dicts, each with
its own model / tools / system_prompt / response_format; the orchestrator returns a structured
`Verdict`. Imported lazily by `run.py` (only the `--strategy agent` live path needs deepagents).
"""

from __future__ import annotations

from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model

from trading_agent.config import AppConfig
from trading_agent.strategy.agents.schemas import AnalystView, Verdict
from trading_agent.strategy.agents.tools import cftc_cot, economic_calendar

GATE_PROMPT = (
    "You are a trade gate for a disciplined forex system. A deterministic Ichimoku trend-pullback "
    "strategy has ALREADY decided to open the trade described in the user message, with entry, stop, "
    "and take-profit prices computed in code. You do NOT set prices, change sizes, or invent trades. "
    "Delegate to the technical-analyst, fundamental-analyst, and sentiment-analyst sub-agents, weigh "
    "their views, then decide ONLY whether to approve or veto THIS specific trade. Default to approve "
    "- the deterministic edge is the baseline. Veto only when an analyst surfaces a concrete, "
    "near-term risk the trade walks into: imminent high-impact news on the instrument, speculative "
    "positioning stretched hard against the trade, or higher-timeframe context that clearly "
    "contradicts the setup. Return a Verdict."
)

TECHNICAL_PROMPT = (
    "You are a technical analyst. The delegated message contains the current Ichimoku context "
    "(H4 trend/cloud, H1 tenkan/kijun pullback zone, M15 trigger) for a proposed trade. Judge only "
    "whether this technical context supports or contradicts the trade. Do not fetch anything; reason "
    "over the numbers given. Return an AnalystView."
)

FUNDAMENTAL_PROMPT = (
    "You are a macro/fundamental analyst. Use the economic_calendar tool to check for imminent "
    "high-impact economic events on the instrument (rate decisions, CPI, employment). A major event "
    "due very soon is a reason to be cautious. Return an AnalystView."
)

SENTIMENT_PROMPT = (
    "You are a positioning/sentiment analyst. Use the cftc_cot tool to check the latest CFTC "
    "Commitments of Traders positioning for the instrument's base currency. Speculative positioning "
    "stretched hard against the proposed trade direction is a caution; positioning aligned with it is "
    "support. Return an AnalystView."
)


def build_orchestrator(cfg: AppConfig):
    """Compile the orchestrator graph from config. Requires ANTHROPIC_API_KEY at call time."""
    a = cfg.agents
    analyst_model = init_chat_model(a.analyst_model)

    subagents = []
    if a.technical:
        subagents.append(
            {"name": "technical-analyst", "description": "Judges the Ichimoku technical context of the proposed trade.",
             "system_prompt": TECHNICAL_PROMPT, "model": analyst_model, "tools": [], "response_format": AnalystView}
        )
    if a.fundamental:
        subagents.append(
            {"name": "fundamental-analyst", "description": "Checks the economic calendar for imminent high-impact events.",
             "system_prompt": FUNDAMENTAL_PROMPT, "model": analyst_model, "tools": [economic_calendar], "response_format": AnalystView}
        )
    if a.sentiment:
        subagents.append(
            {"name": "sentiment-analyst", "description": "Checks CFTC COT speculative positioning.",
             "system_prompt": SENTIMENT_PROMPT, "model": analyst_model, "tools": [cftc_cot], "response_format": AnalystView}
        )

    return create_deep_agent(
        model=init_chat_model(a.orchestrator_model),
        tools=[],
        system_prompt=GATE_PROMPT,
        subagents=subagents,
        response_format=Verdict,
    )
