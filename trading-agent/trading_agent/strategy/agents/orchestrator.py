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
from trading_agent.strategy.agents.tools import cftc_cot, economic_calendar, macro_context

GATE_PROMPT = (
    "You are a trade gate for a disciplined forex system. A deterministic Ichimoku trend-pullback "
    "strategy has ALREADY decided to open the trade described in the user message, with entry, stop, "
    "and take-profit prices computed in code. You do NOT set prices, change sizes, or invent trades. "
    "Delegate to the technical-analyst, fundamental-analyst, and sentiment-analyst sub-agents, weigh "
    "their views, then decide ONLY whether to approve or veto THIS specific trade. "
    "Veto when an analyst surfaces a concrete risk that runs against the trade: imminent high-impact "
    "news on the instrument, speculative positioning stretched hard against the trade, a fundamental "
    "macro trend or an extreme price dislocation (per the Integrated-Macro fundamental view) that "
    "clearly contradicts the direction, or higher-timeframe context that contradicts the setup. Return a Verdict."
)

# authority == "signal": the LLM has full decision authority over prices/direction (size + hard risk
# limits stay code-owned). The deterministic candidate is the seed, not the ceiling.
SIGNAL_PROMPT = (
    "You are the decision-maker for a disciplined forex system. A deterministic Ichimoku trend-pullback "
    "strategy has proposed the trade in the user message as a BASELINE candidate, with code-computed "
    "entry, stop, and take-profit. You have full authority over this trade's PRICES and DIRECTION only "
    "- position size and all hard risk limits (min stop distance, min reward:risk, daily-loss and "
    "trade-count halts) remain owned by code and will re-validate whatever you return. "
    "Delegate to the technical-, fundamental-, and sentiment-analyst sub-agents, weigh their views (the "
    "fundamental-analyst returns an Integrated-Macro read: fundamental trend + price dislocation), then: "
    "return decision='approve' to take the baseline trade unchanged; decision='veto' to stand down; or "
    "decision='modify' with a refined direction/entry/stop/tp (omit any field to keep the baseline value) "
    "when a better trade fits the current context. Keep stops and targets on the correct side of entry "
    "for the direction. Return a Verdict."
)

TECHNICAL_PROMPT = (
    "You are a technical analyst. The delegated message contains the current Ichimoku context "
    "(trend-timeframe cloud, bias-timeframe tenkan/kijun pullback zone, entry-timeframe trigger) for a proposed trade. Judge only "
    "whether this technical context supports or contradicts the trade. Do not fetch anything; reason "
    "over the numbers given. Return an AnalystView."
)

# The fundamental analyst reasons with AQR's Integrated Macro doctrine (Bakrania/Doheny/Fader/Heinrichs,
# "Fundamental Trends and Dislocated Markets", in docs/trading-system/): combine a Systematic view
# (fundamental-trend continuation across 4 Macro-Momentum themes) with an Opportunistic view (fade
# extreme dislocations). ponytail: doctrine distilled inline like every other prompt; the PDF is the
# human source-of-record. Upgrade path = load from a frameworks/*.md if it needs editing without code.
FUNDAMENTAL_PROMPT = (
    "You are the macro/fundamental analyst. Judge the proposed trade (direction + instrument given in "
    "your task) through AQR's Integrated Macro lens - two complementary views.\n"
    "SYSTEMATIC (fundamental trends; markets UNDERREACT to macro news, so trends persist). Weigh four "
    "themes and decide whether the fundamental trend BACKS the trade direction: (1) business cycle - "
    "growth/inflation surprises (economic_calendar: recent actual vs forecast); (2) monetary policy - "
    "the policy-rate differential and its direction (macro_context); (3) international trade / terms of "
    "trade - the base currency's commodity backdrop (macro_context metals); (4) macro sentiment - "
    "risk-on/off (macro_context VIX). Improving fundamentals for the base currency support a LONG, "
    "deteriorating support a SHORT.\n"
    "OPPORTUNISTIC (dislocations; prices occasionally detach from fair value and MEAN-REVERT). Read "
    "dislocation_z in your task: |z|>=2 is a stretch. A trade that FADES INTO an extreme stretch "
    "(buying already-stretched-high or selling already-stretched-low) is a caution; a trade that moves "
    "WITH the reversion is support. Ignore weak or ambiguous dislocations.\n"
    "Call economic_calendar and macro_context, integrate both views, and return ONE AnalystView: stance "
    "supports/against/neutral for THIS direction, a confidence, and a note naming the decisive theme and "
    "the dislocation read. An imminent high-impact event the trade walks into is still a caution."
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

    # sentiment analyst: tell it which CFTC contract to pull so a non-AUD instrument uses the right code
    # (the cftc_cot tool defaults to AUD; the config value flows in via the prompt).
    sentiment_prompt = SENTIMENT_PROMPT + f" Call cftc_cot with cftc_code='{a.cftc_code}' for {cfg.strategy.instrument}."
    # fundamental/macro analyst: pin the instrument so its data tools pull the right pair's currencies.
    fundamental_prompt = FUNDAMENTAL_PROMPT + f" Call economic_calendar and macro_context with instrument='{cfg.strategy.instrument}'."

    subagents = []
    if a.technical:
        subagents.append(
            {"name": "technical-analyst", "description": "Judges the Ichimoku technical context of the proposed trade.",
             "system_prompt": TECHNICAL_PROMPT, "model": analyst_model, "tools": [], "response_format": AnalystView}
        )
    if a.fundamental:
        subagents.append(
            {"name": "fundamental-analyst", "description": "Integrated-Macro view: fundamental-trend (rates/trade/sentiment/surprises) + price dislocation.",
             "system_prompt": fundamental_prompt, "model": analyst_model, "tools": [economic_calendar, macro_context], "response_format": AnalystView}
        )
    if a.sentiment:
        subagents.append(
            {"name": "sentiment-analyst", "description": "Checks CFTC COT speculative positioning.",
             "system_prompt": sentiment_prompt, "model": analyst_model, "tools": [cftc_cot], "response_format": AnalystView}
        )

    base_prompt = SIGNAL_PROMPT if a.authority == "signal" else GATE_PROMPT
    posture = (
        " Bias toward veto: approve (or modify) only when the analysts clearly support the trade."
        if a.veto_default
        else " Default to approve the deterministic baseline - it is the proven edge; only diverge on a concrete reason."
    )

    return create_deep_agent(
        model=init_chat_model(a.orchestrator_model),
        tools=[],
        system_prompt=base_prompt + posture,
        subagents=subagents,
        response_format=Verdict,
    )
