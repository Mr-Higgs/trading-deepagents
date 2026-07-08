"""SimulatedBackend fill + exit model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_agent.types import Candle, OpenTrade, OrderRequest, TradeEvent


@dataclass
class _Open:
    orig_units: int  # signed size at fill (for scale-out fraction math)
    units: int  # signed remaining size
    entry: float
    stop: float  # mutable: trade management moves it
    tp: float
    init_stop: float  # original stop (never mutated); risk = |entry - init_stop|
    instrument: str = ""
    legs_filled: int = 0


class SimulatedBackend:
    def __init__(self, pip_size: float, spread_pips: float, slippage_pips: float) -> None:
        self.pip_size = pip_size
        self._cost = (spread_pips + slippage_pips) * pip_size
        self._pending: list[OrderRequest] = []
        self._pending_close: set[str] = set()  # trade_ids to close at the next bar's open (WFX signal exit)
        self._open: dict[str, _Open] = {}

    def submit(self, order: OrderRequest) -> None:
        self._pending.append(order)

    def request_close(self, trade_id: str) -> None:
        if trade_id in self._open:  # idempotent: a second request before the fill is a no-op
            self._pending_close.add(trade_id)

    def open_trades(self) -> list[str]:
        return list(self._open)

    def open_positions(self) -> list[OpenTrade]:
        return [
            OpenTrade(
                tid,
                t.instrument,
                t.orig_units,
                t.entry,
                t.stop,
                t.init_stop,
                t.tp,
                remaining_units=t.units,
                legs_filled=t.legs_filled,
            )
            for tid, t in self._open.items()
        ]

    def modify_stop(self, trade_id: str, new_stop: float) -> None:
        t = self._open.get(trade_id)
        if t is not None:
            t.stop = new_stop  # honored immediately by _exit_price on subsequent bars

    def reduce_position(self, trade_id: str, units_to_close: int, price: float, now: datetime) -> TradeEvent | None:
        t = self._open.get(trade_id)
        if t is None or units_to_close <= 0:
            return None
        mag = min(units_to_close, abs(t.units))
        if mag <= 0:
            return None
        closed = mag if t.units > 0 else -mag
        pnl = closed * (price - t.entry)
        t.units -= closed
        t.legs_filled += 1
        remaining = t.units
        if remaining == 0:
            del self._open[trade_id]
            return TradeEvent(now, trade_id, "EXIT", price, closed, pnl=pnl, exit_reason="SCALE_OUT", partial=False, remaining_units=0)
        return TradeEvent(now, trade_id, "EXIT", price, closed, pnl=pnl, exit_reason="SCALE_OUT", partial=True, remaining_units=remaining)

    def reconcile(self, now: datetime, bar: Candle) -> list[TradeEvent]:
        events: list[TradeEvent] = []
        # 1. fill orders queued last tick at this bar's open (+/- cost against us)
        for order in self._pending:
            fill = bar.o + self._cost if order.units > 0 else bar.o - self._cost
            self._open[order.trade_id] = _Open(order.units, order.units, fill, order.stop, order.tp, order.stop, order.instrument)
            events.append(TradeEvent(now, order.trade_id, "FILL", fill, order.units))
        self._pending.clear()

        # 1b. signal-close requests fill at THIS bar's open (adverse by cost), before stop/TP settle
        for tid in list(self._pending_close):
            t = self._open.get(tid)
            if t is None:
                self._pending_close.discard(tid)
                continue
            price = bar.o - self._cost if t.units > 0 else bar.o + self._cost
            events.append(TradeEvent(now, tid, "EXIT", price, t.units, pnl=t.units * (price - t.entry), exit_reason="MANUAL"))
            del self._open[tid]
            self._pending_close.discard(tid)

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
