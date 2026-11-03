"""v1.2 setup: Ichimoku trend-pullback, fully deterministic.

Rule chain (each failure returns a distinct skip reason):
  1. Trend gate     - trend-tf price above cloud => LONG-only bias; below => SHORT-only; inside => skip.
  2. Pullback       - bias-tf price pulled back into any configured `pullback_zones` (Ichimoku.pdf Setup #1):
                      tk_band (Tenkan/Kijun zone +/- band), fib (0.5-0.618 golden zone of the last leg), or a
                      tenkan / kijun / span_a touch-and-hold. Optional quality filters (TK separation, Kijun and
                      cloud slope, cloud thickness) skip weak trends.
  3. Confirmations  - (config-gated) Tenkan/Kijun momentum, Chikou vs price 26 bars ago, and top-down
                      confluence (Tenkan/Kijun and close/Kijun agree on every timeframe above entry).
  4. Entry trigger  - a candlestick pattern (engulfing/pin bar/...) on the entry tf back in the trend direction.
Feature keys keep their historical `h4_*` / `h1_*` / `m15_*` names; they mean trend / bias / entry timeframe.
On a pass: stop beyond Kijun, cloud or the last entry-tf swing +/- buffer, take-profit at rr * stop_distance.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from trading_agent.config import PairConfig, StrategyConfig
from trading_agent.indicators import ichimoku
from trading_agent.strategy import patterns, signals
from trading_agent.strategy.base import StrategyResult
from trading_agent.types import Signal

SETUP_ID = "ichimoku_pullback"


class IchimokuPullback:
    def __init__(self, strategy: StrategyConfig, pair: PairConfig) -> None:
        self.cfg = strategy
        self.pair = pair
        self.version = strategy.version

    def _ichi(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.cfg.ichimoku
        return ichimoku(df, tenkan=p.tenkan, kijun=p.kijun, senkou_b=p.senkou_b, displacement=p.displacement)

    def _in_zone(self, bias: pd.DataFrame, bi: pd.Series, direction: str) -> bool:
        cfg = self.cfg
        bar = bias.iloc[-1]
        close = float(bar["c"])
        band = cfg.h1_pullback_band
        for zone in cfg.pullback_zones:
            if zone == "tk_band":
                lo, hi = sorted((float(bi["tenkan"]), float(bi["kijun"])))
                if lo - band <= close <= hi + band:
                    return True
            elif zone == "fib":
                if signals.fib_golden_zone(bias, cfg.fib_lookback, direction, cfg.fib_low, cfg.fib_high):
                    return True
            elif zone in ("tenkan", "kijun", "span_a"):
                if signals.touch_and_hold(bar, bi["senkou_a" if zone == "span_a" else zone], direction, band):
                    return True
            else:
                msg = f"unknown pullback zone {zone!r}"
                raise ValueError(msg)
        return False

    def evaluate(self, snapshot: dict[str, pd.DataFrame], now: datetime) -> StrategyResult:  # noqa: PLR0911 - one return per rule-gate is the clearest form here
        cfg = self.cfg
        trend, bias, entry = snapshot.get(cfg.trend_tf), snapshot.get(cfg.bias_tf), snapshot.get(cfg.entry_tf)
        if trend is None or bias is None or entry is None:
            return StrategyResult(None, "missing_timeframe")
        if len(trend) < cfg.ichimoku.min_rows or len(bias) < cfg.ichimoku.min_rows or len(entry) < 2:
            return StrategyResult(None, "insufficient_history")

        # 1. H4 trend gate
        trend_ichi = self._ichi(trend)
        ti = trend_ichi.iloc[-1]
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

        # v1.1 trend-strength: price must clear the cloud edge by min_trend_distance_pips (skip thin-cloud chop).
        cloud_edge = cloud_top if direction == "LONG" else cloud_bottom
        trend_distance = abs(h4_close - cloud_edge)
        if trend_distance < cfg.min_trend_distance_pips * self.pair.pip_size:
            return StrategyResult(None, "weak_trend")

        # 2. bias-tf pullback into a configured zone
        bias_ichi = self._ichi(bias)
        bi = bias_ichi.iloc[-1]
        if pd.isna(bi["tenkan"]) or pd.isna(bi["kijun"]):
            return StrategyResult(None, "insufficient_history")
        h1_close = float(bias["c"].iloc[-1])
        tenkan, kijun = float(bi["tenkan"]), float(bi["kijun"])
        if not self._in_zone(bias, bi, direction):
            return StrategyResult(None, "no_pullback")
        pip = self.pair.pip_size
        weak = signals.quality_ok(trend_ichi, cfg, direction, pip, tk=False) or signals.quality_ok(bias_ichi, cfg, direction, pip, cloud=False)
        if weak:
            return StrategyResult(None, weak)

        # 3. v1.2 confirmations (config-gated; defaults off => v1.1 behavior)
        if cfg.require_tk_momentum and not ((tenkan > kijun) if direction == "LONG" else (tenkan < kijun)):
            return StrategyResult(None, "no_momentum")
        if cfg.require_chikou and not signals.chikou_ok(entry, cfg.ichimoku.displacement, direction):
            return StrategyResult(None, "no_chikou")
        regime = signals.regime_ok(snapshot, cfg, direction) if cfg.require_regime else None
        if regime:
            return StrategyResult(None, regime)
        if cfg.require_htf_confluence and not signals.htf_confluent(snapshot, cfg, direction):
            return StrategyResult(None, "no_htf_confluence")

        # 4. M15 candlestick trigger
        prev, curr = entry.iloc[-2], entry.iloc[-1]
        cc = float(curr["c"])
        prev_ohlc = (float(prev["o"]), float(prev["h"]), float(prev["l"]), float(prev["c"]))
        curr_ohlc = (float(curr["o"]), float(curr["h"]), float(curr["l"]), cc)
        # third candle back, for 3-candle triggers (star / three soldiers|crows); None -> those no-op
        prev2 = entry.iloc[-3] if len(entry) >= 3 else None
        prev2_ohlc = None if prev2 is None else (float(prev2["o"]), float(prev2["h"]), float(prev2["l"]), float(prev2["c"]))
        if not patterns.matches(cfg.m15_trigger, direction, prev_ohlc, curr_ohlc, prev2_ohlc):
            return StrategyResult(None, "no_trigger")

        # Build the proposal
        entry_ref = cc
        buffer = cfg.stop_buffer_pips * self.pair.pip_size
        if cfg.stop_rule == "swing":
            stop = signals.swing_stop(entry, cfg.swing_lookback, direction, buffer)
        elif cfg.stop_rule == "cloud":
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
                "h1_tenkan_gt_kijun": tenkan > kijun,
                "m15_trigger": list(cfg.m15_trigger),
                "pullback_zones": list(cfg.pullback_zones),
                "trend_distance": trend_distance,
                "style": cfg.style,
                "stack": f"{cfg.trend_tf}/{cfg.bias_tf}/{cfg.entry_tf}",
            },
            reason=reason,
        )
        return StrategyResult(sig, reason)
