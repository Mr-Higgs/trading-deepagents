"""Setup #3 — Ichimoku cloud (Kumo) breakout, then pull-back retest of the broken edge.

The unexplored setup from docs/trading-system/Ichimoku.pdf ("Breaking into the Cloud"): price breaks
CLEAN THROUGH the cloud, then — as the doc notes — usually pulls back to retest the far edge, which
now flips from resistance to support (or support to resistance). We enter on that retest when a
candlestick pattern confirms the edge is holding. All price math in code; deterministic.

Sequence on the entry timeframe:
  1. Breakout: within the last `breakout_lookback` bars, a close crossed the far cloud edge (up = up
     through cloud_top; down = down through cloud_bottom). Direction comes from the break.
  2. Retest: price is still on the breakout side AND has pulled back to within `retest_band_pips` of
     the (now current) cloud edge — the bar's wick tags the edge and the close holds beyond it.
  3. Confirmation: a candlestick trigger fires in the trend direction (+ optional TK / Chikou gates).

Stop: ATR when `stop_rule == "atr"`, else beyond the retest candle (its low for long / high for short)
+/- buffer. TP = entry +/- rr * stop_distance. Mirrors ichimoku_breakout's stop/TP so the same config
knobs and the research loop apply unchanged.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from trading_agent.config import PairConfig, StrategyConfig
from trading_agent.indicators import atr, ichimoku
from trading_agent.strategy import patterns, signals
from trading_agent.strategy.base import StrategyResult
from trading_agent.types import Signal

SETUP_ID = "ichimoku_cloud_breakout"


def _recent_breakout(df: pd.DataFrame, ich: pd.DataFrame, lookback: int) -> tuple[str, int] | None:
    """Most recent bar (within `lookback`, excluding the current bar) whose close broke the far cloud edge.

    Returns (direction, bar_index) or None. Same crossing test as signals.ichimoku_signals:177-182.
    """
    n = len(df)
    close = df["c"].to_numpy()
    top = ich["cloud_top"].to_numpy()
    bot = ich["cloud_bottom"].to_numpy()
    # scan from the bar before current backwards (the current bar is the retest, not the break)
    for i in range(n - 2, max(n - 2 - lookback, 0), -1):
        pc, cc, ptop, tp, pbot, bt = close[i - 1], close[i], top[i - 1], top[i], bot[i - 1], bot[i]
        if any(pd.isna(v) for v in (pc, cc, ptop, tp, pbot, bt)):
            continue
        if pc <= ptop and cc > tp:
            return "LONG", i
        if pc >= pbot and cc < bt:
            return "SHORT", i
    return None


class IchimokuCloudBreakout:
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
        if len(entry) < cfg.ichimoku.min_rows + cfg.breakout_lookback + 1:
            return StrategyResult(None, "insufficient_history")

        ich = self._ichi(entry)
        last = ich.iloc[-1]
        if last[["cloud_top", "cloud_bottom", "tenkan", "kijun"]].isna().any():
            return StrategyResult(None, "insufficient_history")

        # 1. a cloud breakout in the recent window sets the direction
        brk = _recent_breakout(entry, ich, cfg.breakout_lookback)
        if brk is None:
            return StrategyResult(None, "no_breakout")
        direction, _ = brk

        curr = entry.iloc[-1]
        cc = float(curr["c"])
        cloud_top, cloud_bottom = float(last["cloud_top"]), float(last["cloud_bottom"])
        band = cfg.retest_band_pips * self.pair.pip_size

        # 2. pullback retest: still on the breakout side AND the wick tagged the broken edge within the band
        if direction == "LONG":
            edge = cloud_top
            retest = cc > edge and float(curr["l"]) <= edge + band
        else:
            edge = cloud_bottom
            retest = cc < edge and float(curr["h"]) >= edge - band
        if not retest:
            return StrategyResult(None, "no_retest")

        # 3. candlestick confirmation (+ optional TK / Chikou gates, reused from the other setups)
        prev, curr_row = entry.iloc[-2], curr
        prev_ohlc = (float(prev["o"]), float(prev["h"]), float(prev["l"]), float(prev["c"]))
        curr_ohlc = (float(curr_row["o"]), float(curr_row["h"]), float(curr_row["l"]), cc)
        prev2 = entry.iloc[-3] if len(entry) >= 3 else None
        prev2_ohlc = None if prev2 is None else (float(prev2["o"]), float(prev2["h"]), float(prev2["l"]), float(prev2["c"]))
        candle = patterns.first_match(cfg.m15_trigger, direction, prev_ohlc, curr_ohlc, prev2_ohlc)
        if candle is None:
            return StrategyResult(None, "no_trigger")
        if cfg.require_tk_momentum and not signals.tk_momentum(ich, direction):
            return StrategyResult(None, "no_momentum")
        if cfg.require_chikou and not signals.chikou_ok(entry, cfg.ichimoku.displacement, direction):
            return StrategyResult(None, "no_chikou")

        # stop: ATR (WFX) or beyond the retest candle; tp by rr
        entry_ref = cc
        if cfg.stop_rule == "atr":
            atr_val = float(atr(entry, cfg.atr_length).iloc[-1])
            if not (atr_val > 0):
                return StrategyResult(None, "invalid_stop")
            stop_distance = atr_val * cfg.atr_mult
            stop = entry_ref - stop_distance if direction == "LONG" else entry_ref + stop_distance
        else:
            buffer = cfg.stop_buffer_pips * self.pair.pip_size
            stop = float(curr_row["l"]) - buffer if direction == "LONG" else float(curr_row["h"]) + buffer
            stop_distance = entry_ref - stop if direction == "LONG" else stop - entry_ref
        if stop_distance <= 0:
            return StrategyResult(None, "invalid_stop")
        tp = entry_ref + cfg.rr * stop_distance if direction == "LONG" else entry_ref - cfg.rr * stop_distance
        reason = f"cloud_retest_{direction.lower()}"

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
                "cloud_edge": edge,
                "retest_band_pips": cfg.retest_band_pips,
                "stop_rule": cfg.stop_rule,
                "stop_distance": stop_distance,
                "m15_trigger": list(cfg.m15_trigger),
                "candle_trigger": candle,
                "style": cfg.style,
                "stack": f"{cfg.trend_tf}/{cfg.bias_tf}/{cfg.entry_tf}",
            },
            reason=reason,
        )
        return StrategyResult(sig, reason)


def _demo() -> None:
    """Self-check the novel logic: the breakout scan finds the right bar/direction, and a full
    evaluate() runs end-to-end on real Ichimoku math without raising."""
    # 1. _recent_breakout: close crosses cloud_top (0.99 -> 1.06) at bar 3; current bar 4 is the retest.
    close = [1.00, 0.98, 0.99, 1.06, 1.05]
    df = pd.DataFrame({"c": close, "h": [c + 0.01 for c in close], "l": [c - 0.01 for c in close]})
    ich = pd.DataFrame({"cloud_top": [1.05, 1.05, 1.05, 1.05, 1.05], "cloud_bottom": [1.00, 1.00, 1.00, 1.00, 1.00]})
    assert _recent_breakout(df, ich, lookback=6) == ("LONG", 3), _recent_breakout(df, ich, 6)
    # no cross anywhere -> None
    flat = pd.DataFrame({"c": [1.2, 1.2, 1.2], "h": [1.21] * 3, "l": [1.19] * 3})
    ich2 = pd.DataFrame({"cloud_top": [1.0] * 3, "cloud_bottom": [0.9] * 3})
    assert _recent_breakout(flat, ich2, lookback=6) is None

    # 2. end-to-end smoke: evaluate() runs on computed Ichimoku and returns a known reason (no raise).
    from trading_agent.config import IchimokuParams

    pair = PairConfig(symbol="AUD_USD", pip_location=-4, quote_ccy="USD", min_units=1, max_units=100000)
    cfg = StrategyConfig(
        version="test", instrument="AUD_USD", trend_tf="H4", bias_tf="H1", entry_tf="M15",
        ichimoku=IchimokuParams(tenkan=2, kijun=3, senkou_b=4, displacement=1),
        h1_pullback_band=0.0, m15_trigger=("hammer", "engulfing"), stop_rule="kijun", stop_buffer_pips=1.0, rr=2.0,
        name="ichimoku_cloud_breakout", breakout_lookback=6, retest_band_pips=50.0,
    )
    base = [0.90, 0.905, 0.91, 0.915, 0.93, 0.95, 0.965]
    rows = [{"o": c, "h": c + 0.001, "l": c - 0.001, "c": c} for c in base]
    rows.append({"o": 0.962, "h": 0.9635, "l": 0.945, "c": 0.9625})  # hammer retest
    frame = pd.DataFrame(rows)
    frame["instrument"], frame["tf"], frame["complete"] = "AUD_USD", "M15", True
    frame["open_time"] = pd.date_range("2025-01-01", periods=len(frame), freq="15min", tz="UTC")
    frame["close_time"] = frame["open_time"] + pd.Timedelta("15min")
    frame["volume"] = 1
    res = IchimokuCloudBreakout(cfg, pair).evaluate({"M15": frame}, frame["close_time"].iloc[-1].to_pydatetime())
    known = {"cloud_retest_long", "no_breakout", "no_retest", "no_trigger", "no_momentum", "no_chikou", "insufficient_history", "invalid_stop"}
    assert res.reason in known, res.reason
    if res.signal is not None:
        assert res.signal.stop < res.signal.entry_ref < res.signal.tp, res.signal
    print("cloud_breakout self-check OK: scan=('LONG',3), evaluate ->", res.reason)


if __name__ == "__main__":
    _demo()
