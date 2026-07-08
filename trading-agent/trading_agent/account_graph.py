"""LangGraph `account`: a read-only snapshot of the OANDA practice account for the dashboard.

One node fetches AccountSummary + open positions + open trades and returns them as state. Read-only and
tripwire-guarded (creds via `oanda_env`), so it structurally cannot touch a live account. The UI polls it.
"""

from __future__ import annotations

import os
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from oandapyV20 import API
from oandapyV20.endpoints import accounts, positions, trades

from trading_agent.oanda_env import practice_account_id, practice_environment

# Fields worth surfacing from AccountSummary (subset of the full response).
_SUMMARY_KEYS = (
    "NAV",
    "balance",
    "unrealizedPL",
    "pl",
    "marginUsed",
    "marginAvailable",
    "marginRate",
    "openTradeCount",
    "openPositionCount",
    "positionValue",
    "currency",
)


class AccountGraphState(TypedDict, total=False):
    account: str
    summary: dict[str, Any]
    positions: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    closed_trades: list[dict[str, Any]]
    error: str


def _mask(acct: str) -> str:
    return f"{acct[:3]}…{acct[-4:]}" if len(acct) > 8 else acct


def snapshot(state: AccountGraphState) -> AccountGraphState:  # noqa: ARG001 - no input; reads the configured practice account
    try:
        api = API(access_token=os.environ["OANDA_API_KEY"], environment=practice_environment())
        acct = practice_account_id()  # refuses anything not 101-…
        summ = api.request(accounts.AccountSummary(acct))["account"]
        pos = api.request(positions.OpenPositions(acct)).get("positions", [])
        trs = api.request(trades.OpenTrades(acct)).get("trades", [])
        closed = api.request(trades.TradesList(acct, params={"state": "CLOSED", "count": 50})).get("trades", [])
        return {"account": _mask(acct), "summary": {k: summ.get(k) for k in _SUMMARY_KEYS}, "positions": pos, "trades": trs, "closed_trades": closed}
    except Exception as e:  # noqa: BLE001 - surface read/credential errors to the UI instead of crashing the graph
        return {"error": str(e)}


account = StateGraph(AccountGraphState).add_node("snapshot", snapshot).add_edge(START, "snapshot").add_edge("snapshot", END).compile()
