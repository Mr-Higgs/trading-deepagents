"""Deterministic local fill simulator.

Model (conservative on purpose):
  - A queued market order fills at the NEXT bar's open, moved adverse by (spread + slippage) pips.
  - Stops/TPs are checked against each subsequent bar's high/low. If a bar's open gapped through the
    level (weekend gap), the fill is the worse gapped open, not the level.
  - If a single bar touches BOTH stop and take-profit, assume STOP first (we cannot see intrabar order).
  - P/L (account ccy) = units * (exit - entry); valid while quote_ccy == account_ccy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_agent.types import Candle, OrderRequest, TradeEvent


@dataclass
class _Open:
    units: int  # signed
    entry: float
    stop: float
    tp: float


class SimulatedBackend:
    def __init__(self, pip_size: float, spread_pips: float, slippage_pips: float) -> None:
        self.pip_size = pip_size
        self._cost = (spread_pips + slippage_pips) * pip_size
        self._pending: list[OrderRequest] = []
        self._open: dict[str, _Open] = {}

    def submit(self, order: OrderRequest) -> None:
        self._pending.append(order)

    def open_trades(self) -> list[str]:
        return list(self._open)

    def reconcile(self, now: datetime, bar: Candle) -> list[TradeEvent]:
        events: list[TradeEvent] = []
        # 1. fill orders queued last tick at this bar's open (+/- cost against us)
        for order in self._pending:
            fill = bar.o + self._cost if order.units > 0 else bar.o - self._cost
            self._open[order.trade_id] = _Open(order.units, fill, order.stop, order.tp)
            events.append(TradeEvent(now, order.trade_id, "FILL", fill, order.units))
        self._pending.clear()

        # 2. settle exits (STOP checked before TP => pessimistic tie-break)
        for tid, t in list(self._open.items()):
            price, reason = _exit_price(t, bar)
            if price is None:
                continue
            pnl = t.units * (price - t.entry)
            events.append(TradeEvent(now, tid, "EXIT", price, t.units, pnl=pnl, exit_reason=reason))
            del self._open[tid]
        return events


def _exit_price(t: _Open, bar: Candle) -> tuple[float | None, str | None]:
    if t.units > 0:  # LONG: stop below, tp above
        if bar.o <= t.stop:  # gapped through stop at the open
            return bar.o, "STOP"
        if bar.l <= t.stop:
            return t.stop, "STOP"
        if bar.o >= t.tp:  # gapped through tp at the open
            return bar.o, "TP"
        if bar.h >= t.tp:
            return t.tp, "TP"
    else:  # SHORT: stop above, tp below
        if bar.o >= t.stop:
            return bar.o, "STOP"
        if bar.h >= t.stop:
            return t.stop, "STOP"
        if bar.o <= t.tp:
            return bar.o, "TP"
        if bar.l <= t.tp:
            return t.tp, "TP"
    return None, None
