"""Core dataclass contracts shared across the pipeline.

These are the stable interfaces every module speaks. `Signal` in particular is the Phase-2
boundary: a deepagents orchestrator will later emit the same dataclass, and the deterministic
risk + execution core will consume it unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

Direction = Literal["LONG", "SHORT"]


@dataclass(frozen=True, slots=True)
class Candle:
    """One OHLCV bar. Only `complete=True` candles ever enter the engine."""

    instrument: str
    tf: str  # "H4" | "H1" | "M15"
    open_time: datetime  # tz-aware UTC, candle OPEN
    close_time: datetime  # tz-aware UTC, candle CLOSE == decision time when tf == entry_tf
    o: float
    h: float
    l: float  # noqa: E741 - OHLC low; single-letter is the domain convention
    c: float
    volume: int
    complete: bool


@dataclass(frozen=True, slots=True)
class Signal:
    """A trade PROPOSAL. Deterministic strategies (Phase 1) and LLM analysts (Phase 2) both emit this."""

    ts: datetime
    signal_id: str
    instrument: str
    direction: Direction
    entry_ref: float  # reference price at signal time (fills happen next bar)
    stop: float
    tp: float
    rr: float
    setup_id: str
    strategy_version: str
    features: dict[str, object]  # e.g. {"h4_above_cloud": True, "h1_zone": "kijun", ...}
    reason: str


@dataclass(frozen=True, slots=True)
class OrderRequest:
    instrument: str
    units: int  # SIGNED: >0 long, <0 short
    stop: float
    tp: float
    signal_id: str
    trade_id: str
    strategy_version: str
    entry_type: str = "MARKET"


@dataclass(frozen=True, slots=True)
class TradeEvent:
    """Emitted by an ExecutionBackend when a trade fills or exits."""

    ts: datetime
    trade_id: str
    kind: Literal["FILL", "EXIT"]
    price: float
    units: int
    pnl: float | None = None  # set on EXIT
    exit_reason: str | None = None  # "STOP" | "TP" | "MANUAL" (set on EXIT)


@dataclass
class RiskDecision:
    approved: bool
    units: int  # signed, sized; 0 when rejected
    risk_amount: float
    reason: str  # rule name, whether pass or fail
    signal: Signal


@dataclass
class DayState:
    """Per-trading-day counters, rebuilt by replaying the event log at startup."""

    trading_day: date
    trades_opened: int = 0
    realized_pnl: float = 0.0
    halted: bool = False
    halt_reason: str | None = None


@dataclass
class AccountState:
    equity: float
    balance: float
    open_positions: int = 0
