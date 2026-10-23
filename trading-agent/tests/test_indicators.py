"""Ichimoku math + the lookahead-safe forward-shift convention.

Uses a linear price ramp so every rolling max/min has a closed form we can assert exactly,
rather than a giant hand-typed golden CSV.
"""

import numpy as np
import pandas as pd

from trading_agent.indicators import atr, ichimoku, price_dislocation


def _ramp(n: int) -> pd.DataFrame:
    """close = i, symmetric 2-wide range (h=i+1, l=i-1). Rolling extremes have closed forms."""
    i = np.arange(n, dtype=float)
    return pd.DataFrame({"o": i, "h": i + 1.0, "l": i - 1.0, "c": i, "volume": 1}, index=pd.RangeIndex(n))


def test_line_formulas_and_forward_shift():
    ich = ichimoku(_ramp(120))
    # tenkan[t] = (max(h,9) + min(l,9))/2 = ((t+1)+((t-8)-1))/2 = t-4
    assert ich["tenkan"].iloc[60] == 56.0
    # kijun[t] = ((t+1)+((t-25)-1))/2 = t-12.5
    assert ich["kijun"].iloc[60] == 47.5
    # senkou_a plotted forward 26: value at t = raw_mid at t-26 = (t-26) - 8.25
    assert ich["senkou_a"].iloc[80] == 80 - 34.25
    # senkou_b plotted forward 26: value at t = (t-26) - 25.5 (valid t>=77)
    assert ich["senkou_b"].iloc[100] == 100 - 51.5
    # cloud edges at t=100: a=65.75, b=48.5 (uptrend => a above b)
    assert ich["cloud_top"].iloc[100] == 65.75
    assert ich["cloud_bottom"].iloc[100] == 48.5


def test_no_lookahead_leading_span_equals_past_raw():
    df = _ramp(120)
    ich = ichimoku(df)
    tenkan = (df["h"].rolling(9).max() + df["l"].rolling(9).min()) / 2
    kijun = (df["h"].rolling(26).max() + df["l"].rolling(26).min()) / 2
    raw_a = (tenkan + kijun) / 2
    # The plotted value at bar t must equal the raw span computed at t-26 (only past data).
    for t in (60, 80, 100):
        assert ich["senkou_a"].iloc[t] == raw_a.iloc[t - 26]


def test_mid_uses_high_max_and_low_min():
    # A spike high and a dip low in one window must both feed the midline (not swapped).
    df = pd.DataFrame(
        {
            "o": [1.0] * 9,
            "h": [1, 1, 1, 1, 9, 1, 1, 1, 1],
            "l": [1, 1, 1, 1, 1, 1, 1, 1, 0],
            "c": [1.0] * 9,
            "volume": [1] * 9,
        }
    )
    ich = ichimoku(df, tenkan=9, kijun=9, senkou_b=9, displacement=0)
    # (max high 9 + min low 0) / 2 = 4.5
    assert ich["tenkan"].iloc[8] == 4.5


def test_chikou_is_backward_and_nan_at_edge():
    ich = ichimoku(_ramp(60))
    assert ich["chikou"].iloc[0] == 26  # close[0+26]
    assert pd.isna(ich["chikou"].iloc[-1])  # no future close => NaN at the decision edge


def test_atr_constant_true_range_and_warmup():
    # On the ramp every bar has TR = max(h-l=2, |h-prev_c|=2, |l-prev_c|=0) = 2, so RMA(TR) == 2.0.
    a = atr(_ramp(120), length=14)
    assert pd.isna(a.iloc[12])  # NaN before the length-bar warmup
    assert a.iloc[13] == 2.0
    assert a.iloc[-1] == 2.0


def test_price_dislocation_flags_stretch_and_flat():
    flat = pd.DataFrame({"c": [1.0] * 30})
    assert pd.isna(price_dislocation(flat))  # zero variance -> NaN, never a divide-by-zero
    # 40 bars at 1.0 then a spike to 2.0: last close sits far above the window mean -> large positive z.
    stretched = pd.DataFrame({"c": [1.0] * 40 + [2.0]})
    z = price_dislocation(stretched, lookback=60, min_periods=20)
    assert z > 2.0
    # A downside spike is symmetric (negative z), and too-short history is NaN (fail-soft).
    assert price_dislocation(pd.DataFrame({"c": [1.0] * 40 + [0.0]})) < -2.0
    assert pd.isna(price_dislocation(pd.DataFrame({"c": [1.0] * 5})))


def test_ichimoku_matches_pandas_rolling_reference():
    import numpy as np

    rng = np.random.default_rng(7)
    c = 0.66 + np.cumsum(rng.normal(0, 0.0004, 400))
    df = pd.DataFrame({"h": c + rng.uniform(0, 0.0005, 400), "l": c - rng.uniform(0, 0.0005, 400), "c": c})
    df.loc[150, "h"] = np.nan  # gaps propagate like rolling()

    def mid(n):
        return (df["h"].rolling(n).max() + df["l"].rolling(n).min()) / 2.0

    ref = pd.DataFrame({"tenkan": mid(9), "kijun": mid(26), "senkou_a": ((mid(9) + mid(26)) / 2).shift(26), "senkou_b": mid(52).shift(26), "chikou": df["c"].shift(-26)})
    ref["cloud_top"] = ref[["senkou_a", "senkou_b"]].max(axis=1)
    ref["cloud_bottom"] = ref[["senkou_a", "senkou_b"]].min(axis=1)
    pd.testing.assert_frame_equal(ichimoku(df), ref)
    assert ichimoku(df.iloc[:5])["tenkan"].isna().all()
