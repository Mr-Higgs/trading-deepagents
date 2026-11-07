"""IchimokuPullback rule chain: the LONG/SHORT happy paths and every skip reason.

Frames are synthetic ramps. Tenkan/Kijun depend only on highs/lows, so we build the H1 ramp, read
its Kijun, then anchor the H1 pullback close and the M15 trigger candles to that level.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from conftest import make_pair, make_strategy

from trading_agent.indicators import ichimoku
from trading_agent.marketdata.base import FRAME_COLUMNS, normalize_frame
from trading_agent.strategy.ichimoku_pullback import IchimokuPullback

UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)
NOW = datetime(2026, 1, 30, tzinfo=UTC)
STEP = {"D": timedelta(days=1), "H4": timedelta(hours=4), "H1": timedelta(hours=1), "M15": timedelta(minutes=15)}


def _ramp(tf: str, n: int, base: float, slope: float) -> pd.DataFrame:
    px = base + slope * np.arange(n)
    step = STEP[tf]
    rows = [("AUD_USD", tf, START + j * step, START + (j + 1) * step, px[j], px[j], px[j], px[j], 100, True) for j in range(n)]
    return normalize_frame(pd.DataFrame(rows, columns=FRAME_COLUMNS))


def _kijun(h1: pd.DataFrame) -> tuple[float, float]:
    row = ichimoku(h1).iloc[-1]
    return float(row["tenkan"]), float(row["kijun"])


def _two_m15(anchor: float, *, direction: str, trigger: bool) -> pd.DataFrame:
    step = STEP["M15"]
    if direction == "LONG":
        # bearish prev then bullish engulfing curr (entry_ref = curr close, above kijun anchor)
        po, pc, co, cc = (anchor + 0.0006, anchor + 0.0002, anchor + 0.0001, anchor + 0.0007) if trigger else (anchor + 0.0001, anchor + 0.0003, anchor + 0.0003, anchor + 0.0004)
    else:
        po, pc, co, cc = (anchor - 0.0006, anchor - 0.0002, anchor - 0.0001, anchor - 0.0007) if trigger else (anchor - 0.0001, anchor - 0.0003, anchor - 0.0003, anchor - 0.0004)
    rows = [
        ("AUD_USD", "M15", START, START + step, po, max(po, pc), min(po, pc), pc, 100, True),
        ("AUD_USD", "M15", START + step, START + 2 * step, co, max(co, cc), min(co, cc), cc, 100, True),
    ]
    return normalize_frame(pd.DataFrame(rows, columns=FRAME_COLUMNS))


def _snapshot(direction: str = "LONG", *, pullback: bool = True, trigger: bool = True, h4_flat: bool = False) -> dict[str, pd.DataFrame]:
    if h4_flat:
        h4 = _ramp("H4", 90, 0.6700, 0.0)  # price inside a flat cloud
    elif direction == "LONG":
        h4 = _ramp("H4", 90, 0.6500, 0.0004)  # strong uptrend => price above cloud
    else:
        h4 = _ramp("H4", 90, 0.7000, -0.0004)  # downtrend => price below cloud

    slope = 0.0002 if direction == "LONG" else -0.0002
    base = 0.6600 if direction == "LONG" else 0.6900
    h1 = _ramp("H1", 90, base, slope)
    tenkan, kijun = _kijun(h1)
    last = h1.index[-1]
    h1 = h1.copy()
    h1.loc[last, "c"] = (tenkan + kijun) / 2 if pullback else max(tenkan, kijun) + 0.01

    m15 = _two_m15(kijun, direction=direction, trigger=trigger)
    return {"H4": h4, "H1": h1, "M15": m15}


def _engine() -> IchimokuPullback:
    return IchimokuPullback(make_strategy(), make_pair())


def test_long_signal_happy_path():
    res = _engine().evaluate(_snapshot("LONG"), NOW)
    assert res.reason == "trend_pullback_long"
    sig = res.signal
    assert sig is not None
    assert sig.direction == "LONG"
    assert sig.stop < sig.entry_ref < sig.tp
    # tp is rr * stop_distance beyond entry
    assert sig.tp == sig.entry_ref + 2.0 * (sig.entry_ref - sig.stop)
    assert sig.setup_id == "ichimoku_pullback"
    assert sig.strategy_version == "v1.0.0"


def test_short_signal_happy_path():
    res = _engine().evaluate(_snapshot("SHORT"), NOW)
    assert res.reason == "trend_pullback_short"
    sig = res.signal
    assert sig is not None
    assert sig.direction == "SHORT"
    assert sig.tp < sig.entry_ref < sig.stop
    assert sig.tp == sig.entry_ref - 2.0 * (sig.stop - sig.entry_ref)


def test_h4_in_cloud_skips():
    res = _engine().evaluate(_snapshot("LONG", h4_flat=True), NOW)
    assert res.signal is None
    assert res.reason == "h4_in_cloud"


def test_no_pullback_skips():
    res = _engine().evaluate(_snapshot("LONG", pullback=False), NOW)
    assert res.signal is None
    assert res.reason == "no_pullback"


def test_no_trigger_skips():
    res = _engine().evaluate(_snapshot("LONG", trigger=False), NOW)
    assert res.signal is None
    assert res.reason == "no_trigger"


def test_insufficient_history_skips():
    snap = _snapshot("LONG")
    snap["H4"] = snap["H4"].head(30)  # < 78 rows
    res = _engine().evaluate(snap, NOW)
    assert res.signal is None
    assert res.reason == "insufficient_history"


# ---- v1.1 trend-strength filter --------------------------------------------


def test_weak_trend_skips():
    # An absurdly high min distance rejects even the strong-trend happy path.
    eng = IchimokuPullback(make_strategy(min_trend_distance_pips=1_000_000), make_pair())
    res = eng.evaluate(_snapshot("LONG"), NOW)
    assert res.signal is None
    assert res.reason == "weak_trend"


def test_trend_filter_allows_genuine_trend():
    # The configured 8-pip floor still lets a real trend through (happy-path distance is far larger).
    eng = IchimokuPullback(make_strategy(min_trend_distance_pips=8), make_pair())
    res = eng.evaluate(_snapshot("LONG"), NOW)
    assert res.signal is not None
    assert res.signal.features["trend_distance"] >= 8 * make_pair().pip_size


# ---- v1.2 confirmations (Tenkan/Kijun momentum) ----------------------------


def _long_snapshot_h1_down() -> dict[str, pd.DataFrame]:
    """LONG bias (H4 up) but H1 declining => Tenkan < Kijun, so momentum disagrees with LONG."""
    h4 = _ramp("H4", 90, 0.6500, 0.0004)
    h1 = _ramp("H1", 90, 0.6600, -0.0002)
    tenkan, kijun = _kijun(h1)
    last = h1.index[-1]
    h1 = h1.copy()
    h1.loc[last, "c"] = (tenkan + kijun) / 2  # still a valid pullback into the zone
    m15 = _two_m15(kijun, direction="LONG", trigger=True)
    return {"H4": h4, "H1": h1, "M15": m15}


def test_momentum_gate_blocks_when_tk_disagree():
    eng = IchimokuPullback(make_strategy(require_tk_momentum=True), make_pair())
    res = eng.evaluate(_long_snapshot_h1_down(), NOW)
    assert res.signal is None
    assert res.reason == "no_momentum"


def test_momentum_gate_allows_happy_path():
    eng = IchimokuPullback(make_strategy(require_tk_momentum=True), make_pair())
    res = eng.evaluate(_snapshot("LONG"), NOW)  # H1 uptrend => Tenkan > Kijun
    assert res.signal is not None
    assert res.reason == "trend_pullback_long"


# ---- Setup #2 breakout (skip gates; entries proven by the backtest, not brittle synthetic clouds) ----


def test_breakout_insufficient_history():
    from trading_agent.strategy.ichimoku_breakout import IchimokuBreakout

    entry = _ramp("M15", 40, 0.6700, 0.0001)  # < min_rows + 1
    res = IchimokuBreakout(make_strategy(name="ichimoku_breakout"), make_pair()).evaluate({"M15": entry}, NOW)
    assert res.signal is None
    assert res.reason == "insufficient_history"


def test_breakout_in_cloud_skips():
    from trading_agent.strategy.ichimoku_breakout import IchimokuBreakout

    entry = _ramp("M15", 120, 0.6700, 0.0)  # perfectly flat => price sits inside the (degenerate) cloud
    res = IchimokuBreakout(make_strategy(name="ichimoku_breakout"), make_pair()).evaluate({"M15": entry}, NOW)
    assert res.signal is None
    assert res.reason == "in_cloud"


def test_breakout_no_tk_cross_skips():
    # Monotonic uptrend: price clears the cloud (direction resolves LONG) but Tenkan and Kijun run
    # parallel and never cross, so the WFX TK-cross trigger never fires => no_tk_cross.
    from trading_agent.strategy.ichimoku_breakout import IchimokuBreakout

    entry = _ramp("M15", 120, 0.6700, 0.0002)
    res = IchimokuBreakout(make_strategy(name="ichimoku_breakout"), make_pair()).evaluate({"M15": entry}, NOW)
    assert res.signal is None
    assert res.reason == "no_tk_cross"


def test_breakout_cloud_trigger_needs_a_cross_this_bar():
    from trading_agent.strategy.ichimoku_breakout import IchimokuBreakout

    cfg = make_strategy(name="ichimoku_breakout", breakout_trigger="cloud")
    trending = _ramp("M15", 120, 0.6700, 0.0002)  # already above the cloud: no cross on the last bar
    assert IchimokuBreakout(cfg, make_pair()).evaluate({"M15": trending}, NOW).reason == "no_cloud_cross"
    jump = _ramp("M15", 120, 0.6700, 0.0).copy()  # flat, then the last candle closes through the cloud
    last = jump.index[-1]
    jump.loc[last, ["h", "c"]] = 0.6720
    res = IchimokuBreakout(cfg, make_pair()).evaluate({"M15": jump}, NOW)
    assert res.signal is None and res.reason == "no_momentum"  # Tenkan == Kijun: Setup #2 condition 1 fails


def test_pullback_swing_stop_and_zone_config():
    snap = _snapshot("LONG")
    eng = IchimokuPullback(make_strategy(stop_rule="swing", swing_lookback=2, stop_buffer_pips=1), make_pair())
    res = eng.evaluate(snap, NOW)
    assert res.signal is not None
    assert abs(res.signal.stop - (float(snap["M15"]["l"].min()) - 0.0001)) < 1e-12
    bad = IchimokuPullback(make_strategy(pullback_zones=("bogus",)), make_pair())
    import pytest

    with pytest.raises(ValueError, match="unknown pullback zone"):
        bad.evaluate(snap, NOW)


# ---- ichimoku_confluence (multi-TF gate; entries proven by the backtest, not brittle synthetic clouds) ----


def _confluence(**overrides):
    from trading_agent.strategy.ichimoku_confluence import IchimokuConfluence

    return IchimokuConfluence(make_strategy(name="ichimoku_confluence", **overrides), make_pair())


def test_confluence_insufficient_history():
    # The existing happy-path snapshot only carries a 2-row M15 frame; the confluence needs full
    # Ichimoku on every timeframe, so it stops at the entry TF.
    res = _confluence().evaluate(_snapshot("LONG"), NOW)
    assert res.signal is None
    assert res.reason == "insufficient_history"


def test_confluence_low_confluence_on_flat_market():
    # Flat ramps => every price-vs-level vote is 0 => no directional bias => skip.
    flat = {tf: _ramp(tf, 90, 0.6700, 0.0) for tf in ("H4", "H1", "M15")}
    res = _confluence().evaluate(flat, NOW)
    assert res.signal is None
    assert res.reason == "low_confluence"


def test_confluence_gate_passes_then_delegates_to_inner():
    # All three TFs trend up => strong bullish confluence clears the gate; it then delegates to the
    # inner pullback, which reaches its M15 candlestick gate and finds no reversal in a flat-bodied
    # ramp => the inner's skip flows through (proving the gate passed and delegation happened).
    up = {"H4": _ramp("H4", 90, 0.6500, 0.0004), "H1": _ramp("H1", 90, 0.6600, 0.0002), "M15": _ramp("M15", 90, 0.6700, 0.0001)}
    res = _confluence().evaluate(up, NOW)
    assert res.signal is None
    assert res.reason == "no_trigger"  # not low_confluence/insufficient/h4_conflict => inner was called


# ---- daily regime screen (top-down D / H4 / H1 / M15) ----


def test_regime_ok_trend_range_and_missing():
    from trading_agent.strategy import signals

    cfg = make_strategy(regime_tf="D", require_regime=True)
    up = _ramp("D", 120, 0.6500, 0.0010)
    assert signals.regime_ok({"D": up}, cfg, "LONG") is None
    assert signals.regime_ok({"D": up}, cfg, "SHORT") == "regime_against"
    flat = _ramp("D", 120, 0.6500, 0.0)
    assert signals.regime_ok({"D": flat}, cfg, "LONG") == "regime_ranging"
    assert signals.regime_ok({"D": up.iloc[:50]}, cfg, "LONG") == "regime_insufficient_history"
    assert signals.regime_ok({}, cfg, "LONG") == "regime_insufficient_history"


def test_pullback_blocked_by_daily_regime():
    snap = _snapshot("LONG")
    snap["D"] = _ramp("D", 120, 0.7500, -0.0010)  # daily downtrend vs an H4 long
    cfg = make_strategy(regime_tf="D", require_regime=True)
    res = IchimokuPullback(cfg, make_pair()).evaluate(snap, NOW)
    assert res.signal is None and res.reason == "regime_against"
    assert IchimokuPullback(make_strategy(regime_tf="D"), make_pair()).evaluate(snap, NOW).signal is not None
