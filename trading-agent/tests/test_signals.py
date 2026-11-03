"""Ichimoku alert/signal taxonomy."""

from __future__ import annotations

import pandas as pd

from trading_agent.strategy import signals


def test_self_check():
    signals._demo()  # asserts internally


def test_helpers_direction_aware():
    ichi = pd.DataFrame({"tenkan": [1.0, 1.06], "kijun": [1.02, 1.03]})
    assert signals.tk_momentum(ichi, "LONG") and not signals.tk_momentum(ichi, "SHORT")
    df = pd.DataFrame({"c": [1.0, 2.0]})
    assert signals.chikou_ok(df, 1, "LONG") and not signals.chikou_ok(df, 1, "SHORT")
    # insufficient history -> False, never raises
    assert not signals.chikou_ok(pd.DataFrame({"c": [1.0]}), 26, "LONG")


def test_tk_cross_fires_only_on_the_direction_of_the_crossing():
    up = pd.DataFrame({"tenkan": [1.00, 1.06], "kijun": [1.02, 1.03]})  # Tenkan crosses above Kijun
    assert signals.tk_cross(up, "LONG") and not signals.tk_cross(up, "SHORT")
    down = pd.DataFrame({"tenkan": [1.04, 1.01], "kijun": [1.02, 1.03]})  # Tenkan crosses below Kijun
    assert signals.tk_cross(down, "SHORT") and not signals.tk_cross(down, "LONG")
    # no cross (Tenkan stays above): neither direction fires; <2 rows / NaN -> False, never raises
    flat = pd.DataFrame({"tenkan": [1.05, 1.06], "kijun": [1.02, 1.03]})
    assert not signals.tk_cross(flat, "LONG") and not signals.tk_cross(flat, "SHORT")
    assert not signals.tk_cross(pd.DataFrame({"tenkan": [1.0], "kijun": [1.0]}), "LONG")


def test_price_level_votes_sums_direction_per_level():
    row = pd.Series({"kijun": 1.00, "tenkan": 1.00, "senkou_a": 1.02, "senkou_b": 0.98, "cloud_top": 1.02, "cloud_bottom": 0.98})
    which = ("kijun", "span_a", "span_b", "cloud")
    # 1.01: above kijun(+1), below span_a(-1), above span_b(+1), inside cloud(0) => +1
    assert signals.price_level_votes(1.01, row, which) == 1
    assert signals.price_level_votes(1.05, row, which) == 4  # above everything + above cloud
    assert signals.price_level_votes(0.95, row, which) == -4  # below everything + below cloud
    nan_row = pd.Series({k: float("nan") for k in ("kijun", "senkou_a", "senkou_b", "cloud_top", "cloud_bottom")})
    assert signals.price_level_votes(1.0, nan_row, which) == 0  # NaN levels contribute nothing
    assert signals.price_level_votes(None, row, which) == 0  # no close => 0, never raises


def test_ichimoku_level_target_picks_nearest_beyond_entry():
    row = pd.Series({"kijun": 1.05, "tenkan": 0.98, "senkou_a": 1.10, "senkou_b": 0.90, "cloud_top": 1.10, "cloud_bottom": 0.90})
    assert signals.ichimoku_level_target(1.00, "LONG", [row]) == 1.05  # nearest level above
    assert signals.ichimoku_level_target(1.00, "SHORT", [row]) == 0.98  # nearest level below
    assert signals.ichimoku_level_target(1.00, "LONG", [row], min_dist=0.06) == 1.10  # 1.05 too close, skip to 1.10
    assert signals.ichimoku_level_target(2.00, "LONG", [row]) is None  # nothing beyond entry


def test_exit_cross_on_tk_or_price_kijun_turn():
    df_flat = pd.DataFrame({"c": [1.0, 1.0]})
    # LONG exits on a Tenkan-below-Kijun cross
    tk_down = pd.DataFrame({"tenkan": [1.04, 1.01], "kijun": [1.02, 1.03]})
    assert signals.exit_cross(df_flat, tk_down, "LONG") and not signals.exit_cross(df_flat, tk_down, "SHORT")
    # LONG exits on close crossing below Kijun even without a TK cross (Tenkan stays above)
    no_tk = pd.DataFrame({"tenkan": [1.10, 1.10], "kijun": [1.02, 1.03]})
    price_down = pd.DataFrame({"c": [1.05, 1.00]})  # 1.05 > kijun 1.02 -> 1.00 < kijun 1.03
    assert signals.exit_cross(price_down, no_tk, "LONG")
    # a rising LONG that holds above the Kijun with no cross does NOT exit
    holding = pd.DataFrame({"c": [1.05, 1.06]})
    assert not signals.exit_cross(holding, no_tk, "LONG")
    # SHORT mirrors: Tenkan crossing above Kijun exits the short
    tk_up = pd.DataFrame({"tenkan": [1.00, 1.06], "kijun": [1.02, 1.03]})
    assert signals.exit_cross(df_flat, tk_up, "SHORT") and not signals.exit_cross(df_flat, tk_up, "LONG")


def test_nan_ichimoku_yields_unknown_state_no_crash():
    df = pd.DataFrame({"c": [1.0, 1.0]})
    ichi = pd.DataFrame(
        {k: [float("nan"), float("nan")] for k in ("tenkan", "kijun", "senkou_a", "senkou_b", "cloud_top", "cloud_bottom")}
    )
    out = signals.ichimoku_signals(df, ichi, displacement=1)
    assert out["state"]["price_vs_cloud"] == "unknown"
    assert out["signals"] == []


def _htf_frames(close: float, tenkan: float, kijun: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    return pd.DataFrame({"c": [close]}), pd.DataFrame({"tenkan": [tenkan], "kijun": [kijun]})


def test_htf_confluence_needs_tk_and_price_vs_kijun():
    assert signals.htf_confluence(*_htf_frames(1.10, 1.08, 1.05), "LONG")
    assert not signals.htf_confluence(*_htf_frames(1.04, 1.08, 1.05), "LONG")  # price below Kijun
    assert not signals.htf_confluence(*_htf_frames(1.10, 1.04, 1.05), "LONG")  # Tenkan below Kijun
    assert signals.htf_confluence(*_htf_frames(1.00, 1.02, 1.05), "SHORT")
    assert not signals.htf_confluence(*_htf_frames(1.10, float("nan"), 1.05), "LONG")


def test_htf_confluent_checks_only_timeframes_above_entry(monkeypatch):
    from dataclasses import replace

    from conftest import make_strategy

    cfg = replace(make_strategy(), trend_tf="H1", bias_tf="M15", entry_tf="M15")
    up = pd.DataFrame({"c": [1.0 + i * 0.001 for i in range(60)]})
    down = pd.DataFrame({"c": [1.1 - i * 0.001 for i in range(60)]})
    for df in (up, down):
        df["h"], df["l"], df["o"] = df["c"] + 0.0005, df["c"] - 0.0005, df["c"]
    assert signals.htf_confluent({"H1": up, "M15": down}, cfg, "LONG")  # M15 is the entry tf: ignored
    assert not signals.htf_confluent({"H1": down, "M15": up}, cfg, "LONG")
    assert not signals.htf_confluent({"M15": up}, cfg, "LONG")  # missing higher tf fails closed


def _ohlc(highs, lows, closes) -> pd.DataFrame:
    return pd.DataFrame({"o": closes, "h": highs, "l": lows, "c": closes})


def test_fib_golden_zone_needs_completed_leg_and_retrace():
    # leg 1.00 -> 1.10, then a pullback close at 1.045 (0.55 retrace) => in zone for LONG only
    df = _ohlc([1.00, 1.05, 1.10, 1.08, 1.05], [1.00, 1.03, 1.08, 1.05, 1.04], [1.00, 1.04, 1.09, 1.06, 1.045])
    assert signals.fib_golden_zone(df, 10, "LONG")
    assert not signals.fib_golden_zone(df, 10, "SHORT")
    shallow = df.copy()
    shallow.loc[shallow.index[-1], "c"] = 1.08  # only 0.2 retrace
    assert not signals.fib_golden_zone(shallow, 10, "LONG")


def test_touch_and_hold_and_swing_stop():
    bar = pd.Series({"o": 1.02, "h": 1.03, "l": 0.999, "c": 1.02})
    assert signals.touch_and_hold(bar, 1.0, "LONG")
    assert not signals.touch_and_hold(bar, 1.0, "SHORT")
    assert not signals.touch_and_hold(bar, float("nan"), "LONG")
    df = _ohlc([1.0, 1.2, 1.1], [0.9, 0.95, 1.0], [1.0, 1.1, 1.05])
    assert signals.swing_stop(df, 3, "LONG", 0.01) == 0.89
    assert signals.swing_stop(df, 2, "SHORT", 0.01) == 1.21


def test_close_crossed_only_on_the_crossing_bar():
    df = _ohlc([1.0] * 3, [1.0] * 3, [0.99, 1.00, 1.02])
    level = pd.Series([1.01, 1.01, 1.01])
    assert signals.close_crossed(df, level, "LONG")
    assert not signals.close_crossed(df, level, "SHORT")
    assert not signals.close_crossed(df.iloc[:2], level.iloc[:2], "LONG")


def test_quality_filters_reasons():
    from dataclasses import replace

    from conftest import make_strategy

    n = 8
    ichi = pd.DataFrame({
        "tenkan": [1.0 + 0.001 * i for i in range(n)],
        "kijun": [1.0 + 0.0005 * i for i in range(n)],
        "senkou_a": [1.0 + 0.0002 * i for i in range(n)],
        "senkou_b": [0.99] * n,
    })
    cfg = replace(make_strategy(), slope_lookback=5)
    pip = 0.0001
    assert signals.quality_ok(ichi, cfg, "LONG", pip) is None  # all thresholds 0 => off
    assert signals.quality_ok(ichi, replace(cfg, min_tk_separation_pips=50), "LONG", pip) == "narrow_tk"
    assert signals.quality_ok(ichi, replace(cfg, min_kijun_slope_pips=1), "SHORT", pip) == "flat_kijun"
    assert signals.quality_ok(ichi, replace(cfg, min_cloud_thickness_pips=500), "LONG", pip) == "thin_cloud"
    assert signals.quality_ok(ichi, replace(cfg, min_cloud_slope_pips=20), "LONG", pip) == "flat_cloud"
    assert signals.quality_ok(ichi, replace(cfg, min_cloud_slope_pips=20), "LONG", pip, cloud=False) is None
