"""Ichimoku Kinko Hyo - pure pandas, lookahead-safe.

The leading spans (Senkou A/B) are returned already shifted FORWARD by `displacement`: `senkou_a[t]`
is the cloud value plotted AT bar t, computed from data at `t - displacement`. So reading
`senkou_a[t]` / `senkou_b[t]` / `cloud_top[t]` for a decision at bar t uses only past data - no lookahead.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


def _mid(high: pd.Series, low: pd.Series, n: int) -> pd.Series:
    """Ichimoku midline: (highest high + lowest low) / 2 over the last n bars."""
    return pd.Series(_mid_np(high.to_numpy(float), low.to_numpy(float), n), index=high.index)


def _mid_np(high: np.ndarray, low: np.ndarray, n: int) -> np.ndarray:
    """`_mid` on arrays; NaN until n bars (or any NaN in the window), like `rolling(n)`."""
    out = np.full(len(high), np.nan)
    if n > 0 and len(high) >= n:
        out[n - 1 :] = (sliding_window_view(high, n).max(axis=1) + sliding_window_view(low, n).min(axis=1)) / 2.0
    return out


def _shift(values: np.ndarray, k: int) -> np.ndarray:
    """`Series.shift(k)` on an array (k may be negative)."""
    out = np.full(len(values), np.nan)
    if k == 0:
        out[:] = values
    elif 0 < k < len(values):
        out[k:] = values[:-k]
    elif 0 < -k < len(values):
        out[:k] = values[-k:]
    return out


def ichimoku(
    df: pd.DataFrame,
    *,
    tenkan: int = 9,
    kijun: int = 26,
    senkou_b: int = 52,
    displacement: int = 26,
) -> pd.DataFrame:
    """Compute Ichimoku lines for a candle frame.

    Called once per bar per strategy in backtests, so it works on numpy arrays rather than pandas
    rolling windows (same values, an order of magnitude less overhead).

    Args:
        df: candle frame with at least columns `h`, `l`, `c` (high, low, close), time-ascending.
        tenkan, kijun, senkou_b, displacement: standard Ichimoku periods.

    Returns:
        A frame on the same index with columns: tenkan, kijun, senkou_a, senkou_b, chikou,
        cloud_top, cloud_bottom. Leading spans are forward-shifted (lookahead-safe, see module doc).
    """
    high, low, close = (df[c].to_numpy(float) for c in ("h", "l", "c"))
    tenkan_s = _mid_np(high, low, tenkan)
    kijun_s = _mid_np(high, low, kijun)
    # Leading spans plotted forward => the value AT bar t was computed at t-displacement (known at t).
    senkou_a = _shift((tenkan_s + kijun_s) / 2.0, displacement)
    senkou_b_s = _shift(_mid_np(high, low, senkou_b), displacement)
    # Lagging span plotted backward: charting/journal only. It is NaN near the right edge, so it is
    # never a decision input. ponytail: kept for chart parity; the strategy must not read it.
    chikou = _shift(close, -displacement)

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
    with np.errstate(invalid="ignore"):
        out["cloud_top"] = np.fmax(senkou_a, senkou_b_s)  # NaN-skipping, like DataFrame.max(axis=1)
        out["cloud_bottom"] = np.fmin(senkou_a, senkou_b_s)
    return out


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Average True Range via Wilder/RMA smoothing (matches TradingView `ta.atr`). Lookahead-safe.

    TR = max(high-low, |high-prev_close|, |low-prev_close|); RMA = ewm(alpha=1/length, adjust=False).
    ponytail: RMA is seeded from the first bar rather than an SMA(length) seed like Pine; the
    difference decays geometrically and is negligible after the ~66-bar Ichimoku warmup we already gate on.
    """
    high, low, close = df["h"], df["l"], df["c"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def price_dislocation(df: pd.DataFrame, lookback: int = 60, min_periods: int = 20) -> float:
    """Z-score of the latest close vs its own rolling window - a proxy for the AQR "Opportunistic
    Macro" dislocation (how far price sits from a recent fair-value anchor). |z| >= ~2 is a stretch.

    Returns NaN when there is too little history or the window is flat (std == 0). Lookahead-safe:
    reads only the trailing `lookback` closes up to and including the last (decision) bar.
    ponytail: a rolling-window z-score, not a real fair-value model (PPP / real-exchange-rate); it is
    the cheap deterministic stand-in for the model to reason over. Upgrade path if it proves too noisy.
    """
    close = df["c"].astype(float)
    if len(close) < min_periods:
        return float("nan")
    window = close.iloc[-lookback:]
    mean, std = window.mean(), window.std(ddof=0)
    if pd.isna(std) or std == 0:
        return float("nan")
    return float((close.iloc[-1] - mean) / std)
