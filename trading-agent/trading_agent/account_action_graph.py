"""LangGraph `account_action`: manual close / modify of an OANDA practice trade from the dashboard.

A *human* action (the trader clicking Close or dragging a stop) — NOT the automated loop, so it sits
outside "code owns the money." It is still tripwire-guarded (101- practice only, via oanda_env) and
`with_retries`-wrapped, so it structurally cannot touch a live account. Two actions:

  - close:  market-close a trade — full ("ALL", default) or partial (a positive unit count).
  - modify: replace stop-loss and/or take-profit prices (TradeCRCDO); omitted legs are left untouched.

A trade is addressed by its OANDA ticket id ("71"), or by our client id when prefixed with "@" — the
loop tags every order with clientExtensions.id, so run-placed trades are reachable by that id too.
"""

from __future__ import annotations

import os
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from oandapyV20 import API
from oandapyV20.endpoints import trades

from trading_agent.oanda_env import practice_account_id, practice_environment, with_retries


class ActionState(TypedDict, total=False):
    action: str  # "close" | "modify"
    trade_id: str  # OANDA ticket id, or "@<clientId>"
    units: str  # close: "ALL" (default) or a positive integer count
    stop: float  # modify: new SL price (omit to leave unchanged)
    tp: float  # modify: new TP price (omit to leave unchanged)
    instrument: str  # for price precision (3dp for JPY pairs, else 5)
    ok: bool
    result: dict[str, Any]
    error: str


def _spec(trade_id: str) -> str:
    """OANDA tradeSpecifier: a numeric ticket as-is, else our client id as '@<id>'."""
    tid = str(trade_id)
    return tid if tid.lstrip("-").isdigit() or tid.startswith("@") else f"@{tid}"


def _px(price: float, instrument: str | None) -> str:
    prec = 3 if instrument and "JPY" in instrument else 5
    return f"{float(price):.{prec}f}"


def act(state: ActionState) -> ActionState:
    action = state.get("action")
    tid = state.get("trade_id")
    if action not in ("close", "modify") or not tid:
        return {"ok": False, "error": "action ('close'|'modify') and trade_id are required"}
    # validate before any network so bad calls are cheap + unit-testable without credentials
    if action == "modify" and state.get("stop") is None and state.get("tp") is None:
        return {"ok": False, "error": "modify requires stop and/or tp"}
    try:
        api = with_retries(API(access_token=os.environ["OANDA_API_KEY"], environment=practice_environment()))
        acct = practice_account_id()  # refuses anything not a 101- practice account
        spec = _spec(str(tid))
        if action == "close":
            resp = api.request(trades.TradeClose(acct, spec, data={"units": str(state.get("units", "ALL"))}))
            return {"ok": True, "result": resp}
        data: dict[str, Any] = {}
        if state.get("stop") is not None:
            data["stopLoss"] = {"timeInForce": "GTC", "price": _px(state["stop"], state.get("instrument"))}
        if state.get("tp") is not None:
            data["takeProfit"] = {"timeInForce": "GTC", "price": _px(state["tp"], state.get("instrument"))}
        resp = api.request(trades.TradeCRCDO(acct, spec, data=data))
        return {"ok": True, "result": resp}
    except Exception as e:  # noqa: BLE001 - surface the error to the UI instead of crashing the graph
        return {"ok": False, "error": str(e)}


account_action = StateGraph(ActionState).add_node("act", act).add_edge(START, "act").add_edge("act", END).compile()
