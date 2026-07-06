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
from datetime import datetime

import oandapyV20.endpoints.accounts as accounts
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.trades as trades
import oandapyV20.endpoints.transactions as transactions
from oandapyV20 import API

from trading_agent.oanda_env import practice_account_id, practice_environment
from trading_agent.types import Candle, OrderRequest, TradeEvent

_EXIT_REASON = {"TAKE_PROFIT_ORDER": "TP", "STOP_LOSS_ORDER": "STOP"}


class OandaPaperBackend:
    def __init__(self, api_key: str, account_id: str, instrument: str, environment: str = "practice", price_precision: int = 5) -> None:
        self._api = API(access_token=api_key, environment=environment)
        self._account = account_id
        self._instrument = instrument
        self._precision = price_precision
        self._trade_map: dict[str, str] = {}  # oanda tradeID -> our trade_id
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
        """Rebuild the id map from already-open trades so a restart keeps tracking them."""
        for tr in self._api.request(trades.OpenTrades(self._account)).get("trades", []):
            our = tr.get("clientExtensions", {}).get("id", tr["id"])
            self._trade_map[tr["id"]] = our

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
        self._api.request(orders.OrderCreate(self._account, data=data))

    def open_trades(self) -> list[str]:
        return list(self._trade_map.values())

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
                events.append(TradeEvent(now, our, "FILL", price, int(float(opened["units"]))))
            for closed in t.get("tradesClosed", []):
                oanda_id = closed["tradeID"]
                our = self._trade_map.pop(oanda_id, oanda_id)
                reason = _EXIT_REASON.get(t.get("reason", ""), "MANUAL")
                events.append(TradeEvent(now, our, "EXIT", price, int(float(closed["units"])), pnl=float(closed.get("realizedPL", 0.0)), exit_reason=reason))
        return events
