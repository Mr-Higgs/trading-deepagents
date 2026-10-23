"""Shared test builders for the trading-agent suite."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pandas as pd

from trading_agent.config import ExecutionConfig, IchimokuParams, PairConfig, RiskConfig, StrategyConfig
from trading_agent.marketdata.base import FRAME_COLUMNS, normalize_frame
from trading_agent.types import AccountState, DayState, Signal

UTC = timezone.utc


def make_frame(n: int = 120, start: float = 1.0, step: float = 0.001, instrument: str = "AUD_USD", tf: str = "M15") -> pd.DataFrame:
    """A normalized rising OHLC candle frame - enough history to define an Ichimoku cloud + ATR."""
    minutes = {"M15": 15, "H1": 60, "H4": 240}.get(tf, 15)
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    rows = []
    for i in range(n):
        px = start + i * step
        ot = t0 + timedelta(minutes=minutes * i)
        rows.append((instrument, tf, ot, ot + timedelta(minutes=minutes), px, px + step * 0.7, px - step * 0.3, px + step * 0.5, 10, True))
    return normalize_frame(pd.DataFrame(rows, columns=FRAME_COLUMNS))


def make_pair(min_units: int = 1, max_units: int = 100_000, quote_ccy: str = "USD") -> PairConfig:
    return PairConfig(symbol="AUD_USD", pip_location=-4, quote_ccy=quote_ccy, min_units=min_units, max_units=max_units)


def make_risk(**overrides) -> RiskConfig:
    base = {
        "account_ccy": "USD",
        "risk_pct": 0.01,
        "max_trades_per_day": 3,
        "max_daily_loss_pct": 0.03,
        "trading_day_reset_utc": time(21, 0),
        "kill_switch": False,
        "news_windows": (),
        "execution": ExecutionConfig("simulated", 1.2, 0.3),
    }
    base.update(overrides)
    return RiskConfig(**base)


def make_strategy(**overrides) -> StrategyConfig:
    base = {
        "version": "v1.0.0",
        "instrument": "AUD_USD",
        "trend_tf": "H4",
        "bias_tf": "H1",
        "entry_tf": "M15",
        "ichimoku": IchimokuParams(9, 26, 52, 26),
        "h1_pullback_band": 0.0015,
        "m15_trigger": "engulfing",
        "stop_rule": "kijun",
        "stop_buffer_pips": 3,
        "rr": 2.0,
    }
    base.update(overrides)
    return StrategyConfig(**base)


def make_signal(direction: str = "LONG", entry: float = 0.6700, stop: float = 0.6680, tp: float | None = None) -> Signal:
    if tp is None:
        tp = entry + 0.02 if direction == "LONG" else entry - 0.02
    return Signal(
        ts=datetime(2026, 7, 3, 8, tzinfo=UTC),
        signal_id="s1",
        instrument="AUD_USD",
        direction=direction,
        entry_ref=entry,
        stop=stop,
        tp=tp,
        rr=2.0,
        setup_id="ichimoku_pullback",
        strategy_version="v1.0.0",
        features={},
        reason="test",
    )


def make_account(equity: float = 10_000.0, open_positions: int = 0) -> AccountState:
    return AccountState(equity=equity, balance=equity, open_positions=open_positions)


def make_day(**overrides) -> DayState:
    base = {"trading_day": date(2026, 7, 3)}
    base.update(overrides)
    return DayState(**base)
