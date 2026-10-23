"""SimulatedBackend fill + exit model.

Two cost models share one backend:

* legacy (no `cost_model`): one flat (spread + slippage) charge at entry and on signal closes;
  stops/targets settle at their mid price. This is the pre-realism behavior, kept for comparisons.
* bid/ask (`cost_model` given): longs buy at the ASK and sell at the BID, shorts mirrored. Stops and
  targets are evaluated on the side that would actually trade (long exits on bid, short exits on
  ask). Bid/ask come from the candle when it carries them (OANDA price="BA"), otherwise from
  mid +/- the hour-of-week half spread (`use_bid_ask=False` forces this mid fallback).

P/L is computed in the pair's quote currency and converted to the account currency with `fx`.
Open positions accrue rollover financing (`financing`), emitted as FINANCING events.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_agent.costs import CostModel
from trading_agent.financing import FinancingModel
from trading_agent.fx_convert import FxConverter
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


@dataclass(frozen=True)
class _Side:
    o: float
    h: float
    l: float  # noqa: E741 - OHLC naming matches Candle
    c: float


class SimulatedBackend:
    def __init__(
        self,
        pip_size: float,
        spread_pips: float,
        slippage_pips: float,
        *,
        cost_model: CostModel | None = None,
        use_bid_ask: bool = True,
        financing: FinancingModel | None = None,
        fx: FxConverter | None = None,
        quote_ccy: str | None = None,
        account_ccy: str | None = None,
    ) -> None:
        """Args:
        pip_size: Price size of one pip.
        spread_pips: Legacy flat spread (ignored when `cost_model` is set).
        slippage_pips: Legacy flat slippage (ignored when `cost_model` is set).
        cost_model: Enables the bid/ask fill model.
        use_bid_ask: Use candle bid/ask when present; False forces mid +/- modeled half spread.
        financing: Rollover swap model; None disables financing.
        fx: Converter for quote -> account currency P/L.
        quote_ccy: Pair quote currency (needed with `fx`).
        account_ccy: Account currency (needed with `fx`).
        """
        self.pip_size = pip_size
        self._cost = (spread_pips + slippage_pips) * pip_size
        self._model = cost_model
        self._use_ba = use_bid_ask
        self._financing = financing
        self._fx = fx
        self._quote = quote_ccy
        self._acct = account_ccy
        if quote_ccy and account_ccy and quote_ccy != account_ccy and fx is None:
            msg = f"quote {quote_ccy} != account {account_ccy}: pass an FxConverter to convert P/L"
            raise ValueError(msg)
        self._pending: list[OrderRequest] = []
        self._pending_close: set[str] = set()  # trade_ids to close at the next bar's open (WFX signal exit)
        self._open: dict[str, _Open] = {}
        self._last_ts: datetime | None = None

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

    def to_account(self, amount_quote: float, now: datetime) -> float:
        """Convert a quote-currency amount to the account currency at `now`."""
        if self._fx is None or not self._quote or not self._acct:
            return amount_quote
        return self._fx.convert(amount_quote, self._quote, self._acct, now)

    def _pnl(self, units: int, exit_px: float, entry: float, now: datetime) -> float:
        return self.to_account(units * (exit_px - entry), now)

    def reduce_position(self, trade_id: str, units_to_close: int, price: float, now: datetime) -> TradeEvent | None:
        t = self._open.get(trade_id)
        if t is None or units_to_close <= 0:
            return None
        mag = min(units_to_close, abs(t.units))
        if mag <= 0:
            return None
        closed = mag if t.units > 0 else -mag
        if self._model is not None:  # management levels are mid prices: cross half the spread to close
            half = self._model.half_spread_pips(now) * self.pip_size
            price = price - half if closed > 0 else price + half
        pnl = self._pnl(closed, price, t.entry, now)
        t.units -= closed
        t.legs_filled += 1
        remaining = t.units
        if remaining == 0:
            del self._open[trade_id]
            return TradeEvent(now, trade_id, "EXIT", price, closed, pnl=pnl, exit_reason="SCALE_OUT", partial=False, remaining_units=0)
        return TradeEvent(now, trade_id, "EXIT", price, closed, pnl=pnl, exit_reason="SCALE_OUT", partial=True, remaining_units=remaining)

    def sides(self, bar: Candle) -> tuple[_Side, _Side]:
        """(bid, ask) OHLC for `bar`: real when cached and allowed, else mid +/- modeled half spread."""
        if self._use_ba and bar.has_bid_ask:
            return (
                _Side(bar.bo, bar.bh, bar.bl, bar.bc),  # type: ignore[arg-type]
                _Side(bar.ao, bar.ah, bar.al, bar.ac),  # type: ignore[arg-type]
            )
        half = (self._model.half_spread_pips(bar.open_time) if self._model else 0.0) * self.pip_size
        return (
            _Side(bar.o - half, bar.h - half, bar.l - half, bar.c - half),
            _Side(bar.o + half, bar.h + half, bar.l + half, bar.c + half),
        )

    def reconcile(self, now: datetime, bar: Candle) -> list[TradeEvent]:
        events = self._accrue_financing(now, bar)
        if self._model is None:
            events += self._reconcile_legacy(now, bar)
        else:
            events += self._reconcile_bid_ask(now, bar, self._model)
        self._last_ts = now
        return events

    def _accrue_financing(self, now: datetime, bar: Candle) -> list[TradeEvent]:
        if self._financing is None or self._last_ts is None or not self._open:
            return []
        events = []
        for at in self._financing.rollovers_between(self._last_ts, now):
            for tid, t in self._open.items():
                amt = self._financing.charge_quote(t.instrument, t.units, bar.o, at)
                if amt:
                    events.append(TradeEvent(at, tid, "FINANCING", bar.o, t.units, pnl=self.to_account(amt, at), exit_reason="FINANCING"))
        return events

    def _reconcile_legacy(self, now: datetime, bar: Candle) -> list[TradeEvent]:
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
            events.append(TradeEvent(now, tid, "EXIT", price, t.units, pnl=self._pnl(t.units, price, t.entry, now), exit_reason="MANUAL"))
            del self._open[tid]
            self._pending_close.discard(tid)

        # 2. settle exits (STOP checked before TP => pessimistic tie-break)
        side = _Side(bar.o, bar.h, bar.l, bar.c)
        for tid, t in list(self._open.items()):
            price, reason = _exit_price(t, side)
            if price is None:
                continue
            events.append(TradeEvent(now, tid, "EXIT", price, t.units, pnl=self._pnl(t.units, price, t.entry, now), exit_reason=reason))
            del self._open[tid]
        return events

    def _reconcile_bid_ask(self, now: datetime, bar: Candle, model: CostModel) -> list[TradeEvent]:
        events: list[TradeEvent] = []
        bid, ask = self.sides(bar)
        slip = model.slippage_pips * self.pip_size
        stop_slip = model.stop_slippage_pips * self.pip_size
        for order in self._pending:
            fill = ask.o + slip if order.units > 0 else bid.o - slip
            self._open[order.trade_id] = _Open(order.units, order.units, fill, order.stop, order.tp, order.stop, order.instrument)
            events.append(TradeEvent(now, order.trade_id, "FILL", fill, order.units))
        self._pending.clear()

        for tid in list(self._pending_close):
            t = self._open.get(tid)
            if t is None:
                self._pending_close.discard(tid)
                continue
            price = bid.o - slip if t.units > 0 else ask.o + slip
            events.append(TradeEvent(now, tid, "EXIT", price, t.units, pnl=self._pnl(t.units, price, t.entry, now), exit_reason="MANUAL"))
            del self._open[tid]
            self._pending_close.discard(tid)

        for tid, t in list(self._open.items()):
            closing_side = bid if t.units > 0 else ask  # longs sell at bid, shorts buy at ask
            price, reason = _exit_price(t, closing_side)
            if price is None:
                continue
            if reason == "STOP":
                price = price - stop_slip if t.units > 0 else price + stop_slip
            events.append(TradeEvent(now, tid, "EXIT", price, t.units, pnl=self._pnl(t.units, price, t.entry, now), exit_reason=reason))
            del self._open[tid]
        return events


def _exit_price(t: _Open, bar: _Side | Candle) -> tuple[float | None, str | None]:
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
