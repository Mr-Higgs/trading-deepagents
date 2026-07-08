"""Deterministic strategies. The Strategy protocol is the Phase-2 plug point for LLM analysts."""

from __future__ import annotations

from trading_agent.config import PairConfig, StrategyConfig
from trading_agent.strategy.base import Strategy


def build_strategy(strategy_cfg: StrategyConfig, pair: PairConfig) -> Strategy:
    """Pick the deterministic strategy by `strategy_cfg.name`. Both share the Strategy protocol."""
    # lazy imports so this package's __init__ has no import cycle with the strategy modules
    from trading_agent.strategy.ichimoku_breakout import IchimokuBreakout
    from trading_agent.strategy.ichimoku_confluence import IchimokuConfluence
    from trading_agent.strategy.ichimoku_pullback import IchimokuPullback

    registry = {"ichimoku_pullback": IchimokuPullback, "ichimoku_breakout": IchimokuBreakout, "ichimoku_confluence": IchimokuConfluence}
    try:
        cls = registry[strategy_cfg.name]
    except KeyError:
        raise ValueError(f"unknown strategy name {strategy_cfg.name!r}; choose one of {sorted(registry)}") from None
    return cls(strategy_cfg, pair)
