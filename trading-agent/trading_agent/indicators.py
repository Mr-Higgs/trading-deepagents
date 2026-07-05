"""Ichimoku Kinko Hyo - pure pandas, lookahead-safe.

The leading spans (Senkou A/B) are returned already shifted FORWARD by `displacement`: `senkou_a[t]`
is the cloud value plotted AT bar t, computed from data at `t - displacement`. So reading
`senkou_a[t]` / `senkou_b[t]` / `cloud_top[t]` for a decision at bar t uses only past data - no lookahead.
"""

from __future__ import annotations

import pandas as pd


def _mid(high: pd.Series, low: pd.Series, n: int) -> pd.Series:
    """Ichimoku midline: (highest high + lowest low) / 2 over the last n bars."""
    return (high.rolling(n).max() + low.rolling(n).min()) / 2.0


def ichimoku(
    df: pd.DataFrame,
    *,
    tenkan: int = 9,
    kijun: int = 26,
    senkou_b: int = 52,
    displacement: int = 26,
) -> pd.DataFrame:
    """Compute Ichimoku lines for a candle frame.

    Args:
        df: candle frame with at least columns `h`, `l`, `c` (high, low, close), time-ascending.
        tenkan, kijun, senkou_b, displacement: standard Ichimoku periods.

    Returns:
        A frame on the same index with columns: tenkan, kijun, senkou_a, senkou_b, chikou,
        cloud_top, cloud_bottom. Leading spans are forward-shifted (lookahead-safe, see module doc).
    """
    high, low, close = df["h"], df["l"], df["c"]
    tenkan_s = _mid(high, low, tenkan)
    kijun_s = _mid(high, low, kijun)
    # Leading spans plotted forward => the value AT bar t was computed at t-displacement (known at t).
    senkou_a = ((tenkan_s + kijun_s) / 2.0).shift(displacement)
    senkou_b_s = _mid(high, low, senkou_b).shift(displacement)
    # Lagging span plotted backward: charting/journal only. It is NaN near the right edge, so it is
    # never a decision input. ponytail: kept for chart parity; the strategy must not read it.
    chikou = close.shift(-displacement)

    out = pd.DataFrame(
        {
            "tenkan": tenkan_s,
            "kijun": kijun_s,
            "senkou_a": senkou_a,
            "senkou_b": senkou_b_s,
            "chikou": chikou,
        },
        index=df.index,
    )
    out["cloud_top"] = out[["senkou_a", "senkou_b"]].max(axis=1)
    out["cloud_bottom"] = out[["senkou_a", "senkou_b"]].min(axis=1)
    return out
