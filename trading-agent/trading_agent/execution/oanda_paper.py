"""OANDA v20 practice-account execution backend (real paper fills).

Gated: only wired when `execution.backend: oanda`. Built AFTER the simulator was validated. Every
effect is driven by OANDA's transaction stream (idempotent via lastTransactionID), so a restart
resumes without double-counting. The transaction->event parsing is pure and unit-tested; the network
wrapper needs a live practice account to validate end-to-end.

Note: the engine still sizes on its LOCAL equity (starting_equity + realized P/L), not live account
NAV. That is fine for paper validation; wire an AccountProvider if you need exact NAV-based sizing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

import oandapyV20.endpoints.accounts as accounts
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.trades as trades
import oandapyV20.endpoints.transactions as transactions
from oandapyV20 import API

from trading_agent.oanda_env import practice_account_id, practice_environment
from trading_agent.types import Candle, OpenTrade, OrderRequest, TradeEvent

_EXIT_REASON = {"TAKE_PROFIT_ORDER": "TP", "STOP_LOSS_ORDER": "STOP"}


@dataclass
class _OandaOpen:
    oanda_id: str
    instrument: str
    orig_units: int  # signed size at fill
    units: int  # signed remaining size
    entry: float
    stop: float
    init_stop: float
    tp: float
    legs_filled: int = 0


class OandaPaperBackend:
    def __init__(self, api_key: str, account_id: str, instrument: str, environment: str = "practice", price_precision: int = 5) -> None:
        self._api = API(access_token=api_key, environment=environment)
        self._account = account_id
        self._instrument = instrument
        self._precision = price_precision
        self._trade_map: dict[str, str] = {}  # oanda tradeID -> our trade_id
        self._open: dict[str, _OandaOpen] = {}  # our trade_id -> local mirror (entry/stop/init_stop)
        self._pending_meta: dict[str, OrderRequest] = {}  # our trade_id -> submitted order (until fill)
        self._local_partials: set[str] = set()  # trade_ids already accounted via optimistic reduce_position
        self._prime_open_trades()
        self._last_txn_id = self._current_txn_id()

    @classmethod
    def from_env(cls, instrument: str, price_precision: int = 5) -> OandaPaperBackend:
        # guards: refuse a non-practice env or a non-101- (live) account before any order can be placed
        return cls(os.environ["OANDA_API_KEY"], practice_account_id(), instrument, practice_environment(), price_precision)

    def _px(self, p: float) -> str:
        return f"{p:.{self._precision}f}"

    def _current_txn_id(self) -> str:
        return self._api.request(accounts.AccountSummary(self._account))["account"]["lastTransactionID"]

    def _prime_open_trades(self) -> None:
        """Rebuild the id map + local mirror from already-open trades so a restart keeps tracking them."""
        for tr in self._api.request(trades.OpenTrades(self._account)).get("trades", []):
            oanda_id = tr["id"]
            our = tr.get("clientExtensions", {}).get("id", oanda_id)
            self._trade_map[oanda_id] = our
            entry = float(tr.get("price", 0.0))
            sl = float(tr.get("stopLossOrder", {}).get("price", entry))  # ceiling: if already moved, original is lost
            tp = float(tr.get("takeProfitOrder", {}).get("price", 0.0))
            units = int(float(tr.get("currentUnits", tr.get("initialUnits", 0))))
            self._open[our] = _OandaOpen(oanda_id, tr.get("instrument", self._instrument), units, units, entry, sl, sl, tp)

    def submit(self, order: OrderRequest) -> None:
        data = {
            "order": {
                "type": "MARKET",
                "instrument": order.instrument,
                "units": str(order.units),
                "stopLossOnFill": {"price": self._px(order.stop)},
                "takeProfitOnFill": {"price": self._px(order.tp)},
                "clientExtensions": {"id": order.trade_id, "tag": order.strategy_version},
                "tradeClientExtensions": {"id": order.trade_id, "tag": order.strategy_version},
            }
        }
        self._pending_meta[order.trade_id] = order  # remember stop/tp/init_stop until the fill lands
        self._api.request(orders.OrderCreate(self._account, data=data))

    def open_trades(self) -> list[str]:
        return list(self._open)

    def open_positions(self) -> list[OpenTrade]:
        return [
            OpenTrade(tid, t.instrument, t.orig_units, t.entry, t.stop, t.init_stop, t.tp, remaining_units=t.units, legs_filled=t.legs_filled)
            for tid, t in self._open.items()
        ]

    def modify_stop(self, trade_id: str, new_stop: float) -> None:
        t = self._open.get(trade_id)
        if t is None:
            return
        data = {"stopLoss": {"timeInForce": "GTC", "price": self._px(new_stop)}}
        self._api.request(trades.TradeCRCDO(self._account, t.oanda_id, data=data))  # replaces the SL order
        t.stop = new_stop

    def reduce_position(self, trade_id: str, units_to_close: int, price: float, now: datetime) -> TradeEvent | None:
        t = self._open.get(trade_id)
        if t is None or units_to_close <= 0:
            return None
        mag = min(units_to_close, abs(t.units))
        if mag <= 0:
            return None
        closed = mag if t.units > 0 else -mag
        self._api.request(trades.TradeClose(self._account, t.oanda_id, data={"units": str(mag)}))
        pnl = closed * (price - t.entry)
        t.units -= closed
        t.legs_filled += 1
        remaining = t.units
        self._local_partials.add(trade_id)
        if remaining == 0:
            self._open.pop(trade_id, None)
            for oid, our in list(self._trade_map.items()):
                if our == trade_id:
                    self._trade_map.pop(oid, None)
            return TradeEvent(now, trade_id, "EXIT", price, closed, pnl=pnl, exit_reason="SCALE_OUT", partial=False, remaining_units=0)
        return TradeEvent(now, trade_id, "EXIT", price, closed, pnl=pnl, exit_reason="SCALE_OUT", partial=True, remaining_units=remaining)

    def request_close(self, trade_id: str) -> None:
        """Market-close the trade now; the EXIT (reason MANUAL) lands via the next reconcile's txn stream."""
        t = self._open.get(trade_id)
        if t is None:
            return
        self._api.request(trades.TradeClose(self._account, t.oanda_id, data={"units": "ALL"}))

    def reconcile(self, now: datetime, bar: Candle) -> list[TradeEvent]:  # noqa: ARG002 - bar unused; OANDA is source of truth
        resp = self._api.request(transactions.TransactionsSinceID(self._account, params={"id": self._last_txn_id}))
        txns = resp.get("transactions", [])
        events = self._events_from_transactions(txns, now)
        if txns:
            self._last_txn_id = resp["lastTransactionID"]
        return events

    def _events_from_transactions(self, txns: list[dict], now: datetime) -> list[TradeEvent]:
        events: list[TradeEvent] = []
        for t in txns:
            if t.get("type") != "ORDER_FILL":
                continue
            price = float(t["price"])
            opened = t.get("tradeOpened")
            if opened:
                oanda_id = opened["tradeID"]
                our = opened.get("clientExtensions", {}).get("id") or t.get("clientExtensions", {}).get("id") or oanda_id
                self._trade_map[oanda_id] = our
                units = int(float(opened["units"]))
                meta = self._pending_meta.pop(our, None)  # stop/tp we submitted
                stop = meta.stop if meta else price
                tp = meta.tp if meta else 0.0
                self._open[our] = _OandaOpen(oanda_id, opened.get("instrument", self._instrument), units, units, price, stop, stop, tp)
                events.append(TradeEvent(now, our, "FILL", price, units))
            reduced = t.get("tradeReduced")
            if reduced:
                oanda_id = reduced["tradeID"]
                our = self._trade_map.get(oanda_id, oanda_id)
                if our in self._local_partials:
                    self._local_partials.discard(our)
                    continue  # already accounted optimistically in reduce_position
                closed_units = int(float(reduced.get("units", 0)))
                pnl = float(reduced.get("realizedPL", 0.0))
                mirror = self._open.get(our)
                if mirror is not None:
                    mirror.units += closed_units  # OANDA reports closed units as negative of position
                    mirror.legs_filled += 1
                    remaining = mirror.units
                    partial = remaining != 0
                    if not partial:
                        self._open.pop(our, None)
                        self._trade_map.pop(oanda_id, None)
                    events.append(
                        TradeEvent(
                            now,
                            our,
                            "EXIT",
                            price,
                            closed_units,
                            pnl=pnl,
                            exit_reason="SCALE_OUT",
                            partial=partial,
                            remaining_units=remaining if partial else 0,
                        )
                    )
                continue
            for closed in t.get("tradesClosed", []):
                oanda_id = closed["tradeID"]
                our = self._trade_map.pop(oanda_id, oanda_id)
                self._open.pop(our, None)
                self._local_partials.discard(our)
                reason = _EXIT_REASON.get(t.get("reason", ""), "MANUAL")
                events.append(TradeEvent(now, our, "EXIT", price, int(float(closed["units"])), pnl=float(closed.get("realizedPL", 0.0)), exit_reason=reason))
        return events
