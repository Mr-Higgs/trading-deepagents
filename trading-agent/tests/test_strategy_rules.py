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
STEP = {"H4": timedelta(hours=4), "H1": timedelta(hours=1), "M15": timedelta(minutes=15)}


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


# ---- price-action confirmation gate --------------------------------------


def _m15_bos_long(anchor: float) -> pd.DataFrame:
    """7-bar M15: a confirmed swing high at idx2 that the final bullish-engulfing bar's close breaks."""
    step = STEP["M15"]
    b = anchor

    def bar(i, o, h, low, c):
        t0 = START + i * step
        return ("AUD_USD", "M15", t0, t0 + step, o, h, low, c, 100, True)

    rows = [
        bar(0, b + 0.0002, b + 0.0003, b + 0.0001, b + 0.00025),
        bar(1, b + 0.0003, b + 0.0005, b + 0.0002, b + 0.0004),
        bar(2, b + 0.0006, b + 0.0010, b + 0.0005, b + 0.0009),  # swing high (h=b+0.0010)
        bar(3, b + 0.0005, b + 0.0006, b + 0.0004, b + 0.00055),
        bar(4, b + 0.0003, b + 0.0004, b + 0.0002, b + 0.00035),
        bar(5, b + 0.0005, b + 0.00052, b + 0.0001, b + 0.0002),  # bearish (engulfing prev)
        bar(6, b + 0.0001, b + 0.0013, b + 0.0001, b + 0.0013),  # bullish engulf; close breaks b+0.0010
    ]
    return normalize_frame(pd.DataFrame(rows, columns=FRAME_COLUMNS))


def test_price_action_gate_blocks_without_break_of_structure():
    # The happy-path M15 is only 2 bars — too short for a lookback-2 swing — so BOS can't confirm.
    eng = IchimokuPullback(make_strategy(require_price_action=True, price_action_signals=("bos",)), make_pair())
    res = eng.evaluate(_snapshot("LONG"), NOW)
    assert res.signal is None
    assert res.reason == "no_price_action:bos"


def test_price_action_gate_passes_and_records_fired_signals():
    snap = _snapshot("LONG")
    _tenkan, kijun = _kijun(snap["H1"])
    snap["M15"] = _m15_bos_long(kijun)  # a real break of structure on M15
    eng = IchimokuPullback(make_strategy(require_price_action=True, price_action_signals=("bos",)), make_pair())
    res = eng.evaluate(snap, NOW)
    assert res.signal is not None
    assert res.reason == "trend_pullback_long"
    assert res.signal.features["price_action"] == ["bos"]  # fired names recorded for the chart
    assert res.signal.features["candle_trigger"] == "bull_engulf"


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
