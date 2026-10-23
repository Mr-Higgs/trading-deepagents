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
    # Optional bid/ask OHLC (OANDA price="MBA"). None => mid-only candle; fills synthesize a spread.
    bo: float | None = None
    bh: float | None = None
    bl: float | None = None
    bc: float | None = None
    ao: float | None = None
    ah: float | None = None
    al: float | None = None
    ac: float | None = None

    @property
    def has_bid_ask(self) -> bool:
        return self.bo is not None and self.ao is not None


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
    kind: Literal["FILL", "EXIT", "FINANCING"]
    price: float
    units: int
    pnl: float | None = None  # set on EXIT / FINANCING, in ACCOUNT currency
    exit_reason: str | None = None  # "STOP" | "TP" | "MANUAL" | "SCALE_OUT" (set on EXIT)
    partial: bool = False  # True when a scale-out leg banks part of the position
    remaining_units: int | None = None  # signed units still open after a partial exit


@dataclass(frozen=True, slots=True)
class OpenTrade:
    """A currently-open position, exposed by a backend for trade management (stop moves)."""

    trade_id: str
    instrument: str
    units: int  # SIGNED original size at fill; used for R and scale-out fraction math
    entry: float
    stop: float  # current stop (may already be moved)
    init_stop: float  # original stop at submit; risk = |entry - init_stop| survives stop moves
    tp: float
    remaining_units: int = 0  # SIGNED current live size (defaults to units when unset at construction)
    legs_filled: int = 0  # scale-out legs already banked


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
    margin_used: float = 0.0  # account-ccy margin already locked by open positions
