"""ExecutionBackend protocol - the run-mode seam #2.

Backtest uses SimulatedBackend; live/paper swaps in OandaPaperBackend. The engine calls `reconcile`
at the top of every tick (settle exits + fill queued orders) BEFORE evaluating new signals.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from trading_agent.types import Candle, OrderRequest, TradeEvent


class ExecutionBackend(Protocol):
    def submit(self, order: OrderRequest) -> None:
        """Queue a market order; it fills on the NEXT tick's bar open (never same-bar close)."""
        ...

    def reconcile(self, now: datetime, bar: Candle) -> list[TradeEvent]:
        """Fill queued orders at `bar` open and settle any stop/TP exits, returning FILL/EXIT events."""
        ...

    def open_trades(self) -> list[str]:
        """Trade ids currently open."""
        ...
