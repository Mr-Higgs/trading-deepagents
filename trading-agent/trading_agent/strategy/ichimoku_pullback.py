"""v1 setup: Ichimoku trend-pullback, fully deterministic.

Rule chain (each failure returns a distinct skip reason):
  1. H4 trend gate  - price above cloud => LONG-only bias; below => SHORT-only; inside => skip.
  2. H1 pullback    - price pulled back into the Tenkan/Kijun zone (within a config band).
  3. M15 trigger    - a reversal (engulfing) candle back in the trend direction.
On a pass: stop beyond Kijun (or cloud) +/- buffer, take-profit at rr * stop_distance.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from trading_agent.config import PairConfig, StrategyConfig
from trading_agent.indicators import ichimoku
from trading_agent.strategy.base import StrategyResult
from trading_agent.types import Signal

SETUP_ID = "ichimoku_pullback"


def _bull_engulf(po: float, pc: float, co: float, cc: float) -> bool:
    """Current candle is bullish and its body engulfs a prior bearish body."""
    return cc > co and pc < po and cc >= po and co <= pc


def _bear_engulf(po: float, pc: float, co: float, cc: float) -> bool:
    return cc < co and pc > po and cc <= po and co >= pc


class IchimokuPullback:
    def __init__(self, strategy: StrategyConfig, pair: PairConfig) -> None:
        self.cfg = strategy
        self.pair = pair
        self.version = strategy.version

    def _ichimoku_last(self, df: pd.DataFrame) -> pd.Series:
        p = self.cfg.ichimoku
        return ichimoku(df, tenkan=p.tenkan, kijun=p.kijun, senkou_b=p.senkou_b, displacement=p.displacement).iloc[-1]

    def evaluate(self, snapshot: dict[str, pd.DataFrame], now: datetime) -> StrategyResult:  # noqa: PLR0911 - one return per rule-gate is the clearest form here
        cfg = self.cfg
        trend, bias, entry = snapshot.get(cfg.trend_tf), snapshot.get(cfg.bias_tf), snapshot.get(cfg.entry_tf)
        if trend is None or bias is None or entry is None:
            return StrategyResult(None, "missing_timeframe")
        if len(trend) < cfg.ichimoku.min_rows or len(bias) < cfg.ichimoku.min_rows or len(entry) < 2:
            return StrategyResult(None, "insufficient_history")

        # 1. H4 trend gate
        ti = self._ichimoku_last(trend)
        h4_close = float(trend["c"].iloc[-1])
        if pd.isna(ti["cloud_top"]) or pd.isna(ti["cloud_bottom"]):
            return StrategyResult(None, "insufficient_history")
        cloud_top, cloud_bottom = float(ti["cloud_top"]), float(ti["cloud_bottom"])
        if h4_close > cloud_top:
            direction = "LONG"
        elif h4_close < cloud_bottom:
            direction = "SHORT"
        else:
            return StrategyResult(None, "h4_in_cloud")

        # 2. H1 pullback into the Tenkan/Kijun zone
        bi = self._ichimoku_last(bias)
        if pd.isna(bi["tenkan"]) or pd.isna(bi["kijun"]):
            return StrategyResult(None, "insufficient_history")
        h1_close = float(bias["c"].iloc[-1])
        tenkan, kijun = float(bi["tenkan"]), float(bi["kijun"])
        zone_lo, zone_hi = min(tenkan, kijun), max(tenkan, kijun)
        band = cfg.h1_pullback_band
        if not (zone_lo - band <= h1_close <= zone_hi + band):
            return StrategyResult(None, "no_pullback")

        # 3. M15 reversal trigger
        prev, curr = entry.iloc[-2], entry.iloc[-1]
        po, pc, co, cc = float(prev["o"]), float(prev["c"]), float(curr["o"]), float(curr["c"])
        if cfg.m15_trigger != "engulfing":
            return StrategyResult(None, f"unknown_trigger:{cfg.m15_trigger}")
        triggered = _bull_engulf(po, pc, co, cc) if direction == "LONG" else _bear_engulf(po, pc, co, cc)
        if not triggered:
            return StrategyResult(None, "no_trigger")

        # Build the proposal
        entry_ref = cc
        buffer = cfg.stop_buffer_pips * self.pair.pip_size
        if cfg.stop_rule == "cloud":
            stop = cloud_bottom - buffer if direction == "LONG" else cloud_top + buffer
        else:  # kijun
            stop = kijun - buffer if direction == "LONG" else kijun + buffer
        stop_distance = entry_ref - stop if direction == "LONG" else stop - entry_ref
        if stop_distance <= 0:
            return StrategyResult(None, "invalid_stop")
        tp = entry_ref + cfg.rr * stop_distance if direction == "LONG" else entry_ref - cfg.rr * stop_distance
        reason = f"trend_pullback_{direction.lower()}"

        sig = Signal(
            ts=now,
            signal_id=f"{cfg.instrument}-{now:%Y%m%dT%H%M%SZ}",
            instrument=cfg.instrument,
            direction=direction,
            entry_ref=entry_ref,
            stop=stop,
            tp=tp,
            rr=cfg.rr,
            setup_id=SETUP_ID,
            strategy_version=self.version,
            features={
                "h4_close": h4_close,
                "h4_cloud_top": cloud_top,
                "h4_cloud_bottom": cloud_bottom,
                "h1_close": h1_close,
                "h1_tenkan": tenkan,
                "h1_kijun": kijun,
                "m15_trigger": cfg.m15_trigger,
            },
            reason=reason,
        )
        return StrategyResult(sig, reason)
