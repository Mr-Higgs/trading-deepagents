"""Setup #2 - Ichimoku Tenkan/Kijun-cross momentum entry, deterministic (WFX Refined Ichimoku).

Ported from docs/trading-system/tradingview-script.md. On the entry timeframe, LONG when:
  price above the cloud  AND  Tenkan crosses ABOVE Kijun on THIS bar (the WFX trigger).
SHORT mirrors (below cloud + Tenkan crosses below Kijun). An optional Chikou confirmation is
config-gated (`require_chikou`); WFX itself has none.

Stop: ATR-based when `stop_rule == "atr"` (WFX: ATR(atr_length) * atr_mult), else beyond the
trigger candle (low for long / high for short) +/- buffer. TP = entry +/- rr * stop_distance
(WFX uses rr=2.55). ponytail: SuperTrend from the WFX script is plot-only there (drives no
entry/exit condition) so it is intentionally NOT ported.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from trading_agent.config import PairConfig, StrategyConfig
from trading_agent.indicators import atr, ichimoku
from trading_agent.strategy import signals
from trading_agent.strategy.base import StrategyResult
from trading_agent.types import Signal

SETUP_ID = "ichimoku_breakout"


class IchimokuBreakout:
    def __init__(self, strategy: StrategyConfig, pair: PairConfig) -> None:
        self.cfg = strategy
        self.pair = pair
        self.version = strategy.version

    def _ichi(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.cfg.ichimoku
        return ichimoku(df, tenkan=p.tenkan, kijun=p.kijun, senkou_b=p.senkou_b, displacement=p.displacement)

    def evaluate(self, snapshot: dict[str, pd.DataFrame], now: datetime) -> StrategyResult:  # noqa: PLR0911 - one return per gate is clearest
        cfg = self.cfg
        entry = snapshot.get(cfg.entry_tf)
        if entry is None:
            return StrategyResult(None, "missing_timeframe")
        if len(entry) < cfg.ichimoku.min_rows + 1:
            return StrategyResult(None, "insufficient_history")

        ich = self._ichi(entry)
        last = ich.iloc[-1]
        if last[["cloud_top", "cloud_bottom", "tenkan", "kijun"]].isna().any():
            return StrategyResult(None, "insufficient_history")

        curr = entry.iloc[-1]
        cc = float(curr["c"])
        cloud_top, cloud_bottom = float(last["cloud_top"]), float(last["cloud_bottom"])
        tenkan, kijun = float(last["tenkan"]), float(last["kijun"])

        # direction from price vs cloud
        if cc > cloud_top:
            direction = "LONG"
        elif cc < cloud_bottom:
            direction = "SHORT"
        else:
            return StrategyResult(None, "in_cloud")

        # WFX trigger: Tenkan crosses Kijun on THIS bar in the trade direction.
        if not signals.tk_cross(ich, direction):
            return StrategyResult(None, "no_tk_cross")
        # Optional Chikou confirmation (WFX has none; on only when config asks).
        if cfg.require_chikou and not signals.chikou_ok(entry, cfg.ichimoku.displacement, direction):
            return StrategyResult(None, "no_chikou")
        if cfg.require_htf_confluence and not signals.htf_confluent(snapshot, cfg, direction):
            return StrategyResult(None, "no_htf_confluence")

        # stop: ATR-based (WFX) or beyond the trigger candle; tp by rr
        entry_ref = cc
        if cfg.stop_rule == "atr":
            atr_val = float(atr(entry, cfg.atr_length).iloc[-1])
            if not (atr_val > 0):  # NaN during warmup or a zero-range series
                return StrategyResult(None, "invalid_stop")
            stop_distance = atr_val * cfg.atr_mult
            stop = entry_ref - stop_distance if direction == "LONG" else entry_ref + stop_distance
        else:
            buffer = cfg.stop_buffer_pips * self.pair.pip_size
            stop = float(curr["l"]) - buffer if direction == "LONG" else float(curr["h"]) + buffer
            stop_distance = entry_ref - stop if direction == "LONG" else stop - entry_ref
        if stop_distance <= 0:
            return StrategyResult(None, "invalid_stop")
        tp = entry_ref + cfg.rr * stop_distance if direction == "LONG" else entry_ref - cfg.rr * stop_distance
        reason = f"tk_cross_{direction.lower()}"

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
                "entry_close": cc,
                "cloud_top": cloud_top,
                "cloud_bottom": cloud_bottom,
                "tenkan": tenkan,
                "kijun": kijun,
                "stop_rule": cfg.stop_rule,
                "stop_distance": stop_distance,
                "tenkan_gt_kijun": tenkan > kijun,
                "style": cfg.style,
                "stack": f"{cfg.trend_tf}/{cfg.bias_tf}/{cfg.entry_tf}",
            },
            reason=reason,
        )
        return StrategyResult(sig, reason)
