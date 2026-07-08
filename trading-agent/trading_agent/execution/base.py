"""ExecutionBackend protocol - the run-mode seam #2.

Backtest uses SimulatedBackend; live/paper swaps in OandaPaperBackend. The engine calls `reconcile`
at the top of every tick (settle exits + fill queued orders) BEFORE evaluating new signals.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from trading_agent.types import Candle, OpenTrade, OrderRequest, TradeEvent


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

    def open_positions(self) -> list[OpenTrade]:
        """Open positions with entry/stop/init_stop, for trade management."""
        ...

    def modify_stop(self, trade_id: str, new_stop: float) -> None:
        """Move an open trade's stop-loss to `new_stop` (breakeven / trailing)."""
        ...

    def reduce_position(self, trade_id: str, units_to_close: int, price: float, now: datetime) -> TradeEvent | None:
        """Close `units_to_close` magnitude of units at `price` now; keep the remainder open.

        Returns a partial EXIT event, or None if the trade is missing / size is invalid.
        """
        ...

    def request_close(self, trade_id: str) -> None:
        """Close an open trade at market (WFX signal exit). Simulated: fills next bar open as an
        EXIT/MANUAL; OANDA: issues a TradeClose, the EXIT lands via the transaction stream. Idempotent."""
        ...
