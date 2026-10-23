"""build_features: the ONE feature vector, shared by offline training and live inference.

Same function, same output, whether called by `ml.labeling.build_dataset` (to build the training set)
or by `strategy.ml_strategy.MLStrategy` (at decision time). That is the whole point - train/serve
parity - so this file has no training/serving branches.

Two invariants:
  1. LOOKAHEAD-SAFE. It reads only the snapshot the engine already truncated to `closed_only(now)`,
     and reuses the lookahead-safe primitives in indicators.py / strategy/{signals,price_action,
     patterns}.py. It never peeks forward.
  2. INSTRUMENT-NORMALIZED. Every price distance is divided by that timeframe's ATR (a per-instrument
     volatility unit), so AUD/USD and USD/JPY produce comparable features and can be pooled into one
     training set. No raw prices leak in (raw prices are not comparable across pairs and would let the
     model memorize an instrument instead of a pattern).

`_WINDOW` tails every frame before computing. That (a) bounds cost to O(window) per bar instead of
O(n) - a dataset over 200k bars would otherwise be O(n^2) - and (b) guarantees dataset-build, backtest
and live all see the identically-sized frame, so features match to the bit. The window is comfortably
larger than any indicator's warmup (ichimoku ~78, dislocation 60, momentum 12).
"""

from __future__ import annotations

import math

import pandas as pd

from trading_agent.indicators import atr, ichimoku
from trading_agent.strategy import patterns, price_action, signals

_WINDOW = 260  # bars kept from each frame before computing (see module docstring)
_MOMENTUM_BARS = (4, 12)  # entry-frame lookbacks for a normalized recent-return feature
_SR_TOL_ATR = 0.5  # S/R confluence tolerance as a fraction of entry ATR (keeps it scale-free)
_ROLES = ("trend", "bias", "entry")


def _safe(x: object) -> float:
    """Coerce to a finite float; NaN/inf/None/unparseable -> 0.0 (trees handle a neutral 0 fine)."""
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def _ndist(a: float, b: float, scale: float) -> float:
    """Normalized distance (a-b)/scale, or 0.0 when scale is non-positive/degenerate."""
    return _safe((a - b) / scale) if scale > 0 else 0.0


def _cloud_num(c: float, top: float, bottom: float) -> float:
    """price-vs-cloud as a number: +1 above / -1 below / 0 inside."""
    loc = signals.price_vs_cloud(c, top, bottom)
    return 1.0 if loc == "bullish" else -1.0 if loc == "bearish" else 0.0


def _frame_features(role: str, frame: pd.DataFrame | None, cfg) -> dict[str, float]:
    """Ichimoku-geometry features for one timeframe frame, all normalized by that frame's ATR."""
    keys = ("c_vs_kijun", "c_vs_tenkan", "c_vs_cloudtop", "c_vs_cloudbot", "cloud_thick", "tenkan_vs_kijun", "price_vs_cloud", "tk_up", "atr_pct")
    out = {f"{role}_{k}": 0.0 for k in keys}
    p = cfg.ichimoku
    if frame is None or len(frame) < p.min_rows:
        return out
    ich = ichimoku(frame, tenkan=p.tenkan, kijun=p.kijun, senkou_b=p.senkou_b, displacement=p.displacement)
    last = ich.iloc[-1]
    c = _safe(frame["c"].iloc[-1])
    av = _safe(atr(frame, cfg.atr_length).iloc[-1])
    kijun, tenkan = _safe(last["kijun"]), _safe(last["tenkan"])
    top, bottom = _safe(last["cloud_top"]), _safe(last["cloud_bottom"])
    out[f"{role}_c_vs_kijun"] = _ndist(c, kijun, av)
    out[f"{role}_c_vs_tenkan"] = _ndist(c, tenkan, av)
    out[f"{role}_c_vs_cloudtop"] = _ndist(c, top, av)
    out[f"{role}_c_vs_cloudbot"] = _ndist(c, bottom, av)
    out[f"{role}_cloud_thick"] = _ndist(top, bottom, av)
    out[f"{role}_tenkan_vs_kijun"] = _ndist(tenkan, kijun, av)
    out[f"{role}_price_vs_cloud"] = _cloud_num(c, top, bottom)
    out[f"{role}_tk_up"] = 1.0 if signals.tk_momentum(ich, "LONG") else 0.0
    out[f"{role}_atr_pct"] = _safe(av / c) if c > 0 else 0.0  # volatility regime, scale-free
    return out


def build_features(snapshot: dict[str, pd.DataFrame], cfg) -> dict[str, float]:
    """The shared feature vector for a decision at the snapshot's latest closed bar.

    `snapshot` is `{tf: frame}` truncated to closed candles (from `MarketDataSource.snapshot(now)`);
    `cfg` is a StrategyConfig (needs `ichimoku`, `trend_tf`/`bias_tf`/`entry_tf`, `confluence_signals`,
    `atr_length`, `swing_lookback`). Returns a flat, insertion-ordered dict of finite floats. Never
    raises - a sub-feature that cannot be computed defaults to 0.0.
    """
    tailed = {tf: (df.tail(_WINDOW) if df is not None else None) for tf, df in snapshot.items()}
    tfs = {"trend": cfg.trend_tf, "bias": cfg.bias_tf, "entry": cfg.entry_tf}

    feats: dict[str, float] = {}
    for role in _ROLES:
        feats.update(_frame_features(role, tailed.get(tfs[role]), cfg))

    feats.update(_entry_dynamics(tailed.get(cfg.entry_tf), cfg))

    # multi-timeframe confluence score (trend x3 / bias x2 / entry x1); 0 when any TF lacks history
    conf = signals.topdown_confluence(tailed, cfg)
    feats["confluence_score"] = _safe(conf[0]) if conf is not None else 0.0
    return feats


def _entry_dynamics(entry: pd.DataFrame | None, cfg) -> dict[str, float]:
    """Entry-frame dynamics: crosses, dislocation, momentum, structure, candlestick bias.

    Returns a FIXED-ORDER key set (zeros when the frame lacks warmup) so the feature schema is identical
    regardless of input - the model's column order must never depend on whether a bar was computable.
    """
    keys = ("entry_tk_cross_up", "entry_tk_cross_dn", "dislocation_z", *[f"entry_ret_{k}" for k in _MOMENTUM_BARS], "bos_long", "bos_short", "sr_confluence", "candle_bull", "candle_bear")
    out = {k: 0.0 for k in keys}
    p = cfg.ichimoku
    if entry is None or len(entry) < p.min_rows:
        return out
    ich = ichimoku(entry, tenkan=p.tenkan, kijun=p.kijun, senkou_b=p.senkou_b, displacement=p.displacement)
    av = _safe(atr(entry, cfg.atr_length).iloc[-1])
    c = _safe(entry["c"].iloc[-1])
    out["entry_tk_cross_up"] = 1.0 if signals.tk_cross(ich, "LONG") else 0.0
    out["entry_tk_cross_dn"] = 1.0 if signals.tk_cross(ich, "SHORT") else 0.0
    out["dislocation_z"] = _safe(_dislocation(entry))
    for k in _MOMENTUM_BARS:
        past = _safe(entry["c"].iloc[-1 - k]) if len(entry) > k else c
        out[f"entry_ret_{k}"] = _ndist(c, past, av)
    out["bos_long"] = 1.0 if price_action.break_of_structure(entry, "LONG", cfg.swing_lookback) else 0.0
    out["bos_short"] = 1.0 if price_action.break_of_structure(entry, "SHORT", cfg.swing_lookback) else 0.0
    out["sr_confluence"] = 1.0 if price_action.sr_confluence(entry, c, cfg.swing_lookback, _SR_TOL_ATR * av) else 0.0
    out["candle_bull"] = 1.0 if _candle(entry, "LONG") else 0.0
    out["candle_bear"] = 1.0 if _candle(entry, "SHORT") else 0.0
    return out


def _dislocation(entry: pd.DataFrame) -> float:
    from trading_agent.indicators import price_dislocation

    z = price_dislocation(entry)
    return 0.0 if pd.isna(z) else z


def _candle(entry: pd.DataFrame, direction: str) -> bool:
    """Whether any configured candlestick trigger fires for `direction` on the latest closed bar."""
    if len(entry) < 2:
        return False
    curr = entry.iloc[-1]
    prev = entry.iloc[-2]
    prev2 = entry.iloc[-3] if len(entry) >= 3 else None
    curr_t = (_safe(curr["o"]), _safe(curr["h"]), _safe(curr["l"]), _safe(curr["c"]))
    prev_t = (_safe(prev["o"]), _safe(prev["h"]), _safe(prev["l"]), _safe(prev["c"]))
    prev2_t = None if prev2 is None else (_safe(prev2["o"]), _safe(prev2["h"]), _safe(prev2["l"]), _safe(prev2["c"]))
    return patterns.first_match(patterns.TRIGGER_NAMES, direction, prev_t, curr_t, prev2_t) is not None


def feature_names(cfg) -> list[str]:
    """Stable ordered feature-name list (built by running build_features on an empty snapshot)."""
    return list(build_features({}, cfg).keys())


def _demo() -> None:
    from datetime import datetime, timedelta, timezone

    from trading_agent.config import IchimokuParams, StrategyConfig

    cfg = StrategyConfig(
        version="t", instrument="X", trend_tf="H4", bias_tf="H1", entry_tf="M15",
        ichimoku=IchimokuParams(9, 26, 52, 26), h1_pullback_band=0.0, m15_trigger=("engulfing",),
        stop_rule="atr", stop_buffer_pips=0.0, rr=2.0,
    )
    # empty snapshot -> every feature 0.0, and feature_names is stable
    empty = build_features({}, cfg)
    assert set(empty.values()) == {0.0}, empty
    assert feature_names(cfg) == list(empty.keys())
    assert "entry_c_vs_kijun" in empty and "confluence_score" in empty

    # a real rising frame -> finite features, bullish price-vs-cloud, and identical keys to the empty run
    n = 200
    base = 1.0
    rows = []
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for i in range(n):
        px = base + i * 0.001
        rows.append(("X", "M15", t0 + timedelta(minutes=15 * i), t0 + timedelta(minutes=15 * (i + 1)), px, px + 0.0007, px - 0.0003, px + 0.0005, 10, True))
    cols = ["instrument", "tf", "open_time", "close_time", "o", "h", "l", "c", "volume", "complete"]
    frame = pd.DataFrame(rows, columns=cols)
    snap = {"H4": frame, "H1": frame, "M15": frame}
    f = build_features(snap, cfg)
    assert list(f.keys()) == list(empty.keys()), "feature schema must be stable regardless of input"
    assert all(math.isfinite(v) for v in f.values())
    assert f["entry_price_vs_cloud"] == 1.0, f["entry_price_vs_cloud"]  # rising series closes above its cloud
    print(f"features self-check OK: {len(f)} features, e.g. entry_c_vs_kijun={f['entry_c_vs_kijun']:.3f}")


if __name__ == "__main__":
    _demo()
