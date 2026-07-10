"""Strategy protocol + result type.

`evaluate` returns a StrategyResult, not a bare Signal, so that a *skip* still carries the reason it
skipped - the spec requires every skip to be logged with a reason. Phase 2's LLM-analyst strategy
implements the same protocol and returns the same StrategyResult.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import pandas as pd

from trading_agent.types import Signal


@dataclass(frozen=True)
class StrategyResult:
    signal: Signal | None
    reason: str  # setup name when a signal fires, else the skip reason
    candidate: Signal | None = None  # a code-computed signal the LLM gate then vetoed (so the engine can log its geometry)


class Strategy(Protocol):
    version: str

    def evaluate(self, snapshot: dict[str, pd.DataFrame], now: datetime) -> StrategyResult:
        """Given a lookahead-safe multi-timeframe snapshot, propose a trade or explain the skip."""
        ...
