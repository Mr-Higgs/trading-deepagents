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
