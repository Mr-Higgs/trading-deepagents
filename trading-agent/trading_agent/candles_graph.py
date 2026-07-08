"""LangGraph `candles`: OHLC + Ichimoku lines + the alert/signal taxonomy for the dashboard.

Read-only market data (candles + indicators), tripwire-guarded via OandaClient. The UI polls it for the
Chart tab and the Alerts & Signals panel. No account access. NaN Ichimoku values (near the edges) are
serialized as null so the chart can skip them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

import pandas as pd
from langgraph.graph import END, START, StateGraph

from trading_agent.config import load_config
from trading_agent.indicators import ichimoku
from trading_agent.strategy.signals import ichimoku_signals

_CONFIG = Path(__file__).resolve().parent.parent / "config"
_DEFAULT_COUNT = 250


class CandlesState(TypedDict, total=False):
    instrument: str
    timeframe: str
    count: int
    candles: list[dict[str, Any]]
    ichimoku: dict[str, list]
    signals: dict[str, Any]
    error: str


def _col(series: pd.Series) -> list:
    return [None if pd.isna(v) else round(float(v), 6) for v in series]


def snapshot(state: CandlesState) -> CandlesState:
    from trading_agent.marketdata.oanda_client import OandaClient  # lazy: network stack

    try:
        cfg = load_config(_CONFIG)
        instrument = state.get("instrument") or cfg.pair.symbol
        timeframe = state.get("timeframe") or cfg.strategy.entry_tf
        count = int(state.get("count") or _DEFAULT_COUNT)

        df = OandaClient.from_env().recent_candles(instrument, timeframe, count=count)
        p = cfg.strategy.ichimoku
        ichi = ichimoku(df, tenkan=p.tenkan, kijun=p.kijun, senkou_b=p.senkou_b, displacement=p.displacement)

        candles = [
            {"t": int(ot.timestamp()), "o": float(o), "h": float(h), "l": float(low), "c": float(c)}
            for ot, o, h, low, c in zip(df["open_time"], df["o"], df["h"], df["l"], df["c"])
        ]
        lines = {k: _col(ichi[k]) for k in ("tenkan", "kijun", "senkou_a", "senkou_b", "chikou")}
        sigs = ichimoku_signals(df, ichi, displacement=p.displacement)
        return {"instrument": instrument, "timeframe": timeframe, "candles": candles, "ichimoku": lines, "signals": sigs}
    except Exception as e:  # noqa: BLE001 - surface fetch/parse errors to the UI instead of crashing the graph
        return {"error": str(e)}


candles = StateGraph(CandlesState).add_node("snapshot", snapshot).add_edge(START, "snapshot").add_edge("snapshot", END).compile()
