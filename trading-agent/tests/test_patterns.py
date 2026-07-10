"""Candlestick pattern detectors."""

from __future__ import annotations

from trading_agent.strategy import patterns


def test_self_check():
    patterns._demo()  # asserts internally


def test_hammer_and_star_are_direction_specific():
    hammer = (10.0, 10.25, 9.5, 10.2)  # long lower wick -> bullish
    star = (10.0, 10.5, 9.75, 9.8)  # long upper wick -> bearish
    prev = (10.0, 10.05, 9.75, 9.8)
    assert patterns.matches(["hammer"], "LONG", prev, hammer)
    assert not patterns.matches(["hammer"], "SHORT", prev, hammer)
    assert patterns.matches(["shooting_star"], "SHORT", prev, star)
    assert not patterns.matches(["shooting_star"], "LONG", prev, star)


def test_pin_bar_aliases_by_direction():
    hammer = (10.0, 10.25, 9.5, 10.2)
    star = (10.0, 10.5, 9.75, 9.8)
    prev = (10.0, 10.05, 9.75, 9.8)
    assert patterns.matches(["pin_bar"], "LONG", prev, hammer)  # pin_bar -> hammer for long
    assert patterns.matches(["pin_bar"], "SHORT", prev, star)  # pin_bar -> shooting star for short


def test_matches_accepts_bare_string():
    prev_bear = (10.0, 10.05, 9.75, 9.8)
    curr = (9.7, 10.15, 9.65, 10.1)  # bullish engulfing
    assert patterns.matches("engulfing", "LONG", prev_bear, curr)


def test_marubozu_fires_for_matching_direction_only():
    bull_maru = (10.0, 10.95, 9.98, 10.9)  # big body, shaven wicks, close > open
    prev = (10.0, 10.05, 9.75, 9.8)
    assert patterns.matches("marubozu", "LONG", prev, bull_maru)
    assert not patterns.matches("marubozu", "SHORT", prev, bull_maru)


def test_two_candle_patterns_are_direction_specific():
    pl = ((10.0, 10.05, 9.4, 9.5), (9.45, 9.9, 9.4, 9.85))  # piercing line -> bullish
    dcc = ((9.5, 10.05, 9.45, 10.0), (10.05, 10.1, 9.55, 9.6))  # dark cloud cover -> bearish
    bh = ((10.5, 10.6, 9.9, 10.0), (10.1, 10.2, 10.05, 10.3))  # bull harami
    tb = ((10.2, 10.25, 9.9, 10.0), (10.0, 10.3, 9.91, 10.25))  # tweezer bottom
    tt = ((10.0, 10.5, 9.95, 10.3), (10.3, 10.51, 10.0, 10.05))  # tweezer top
    assert patterns.matches("piercing_line", "LONG", *pl)
    assert not patterns.matches("piercing_line", "SHORT", *pl)
    assert patterns.matches("dark_cloud_cover", "SHORT", *dcc)
    assert not patterns.matches("dark_cloud_cover", "LONG", *dcc)
    assert patterns.matches("harami", "LONG", *bh) and not patterns.matches("harami", "SHORT", *bh)
    assert patterns.matches("tweezer", "LONG", *tb)
    assert patterns.matches("tweezer", "SHORT", *tt)


def test_three_candle_patterns_require_prev2():
    ms = ((10.5, 10.55, 9.9, 10.0), (9.95, 10.05, 9.85, 9.98), (10.0, 10.6, 9.98, 10.4))  # morning star
    tws = ((10.0, 10.42, 9.98, 10.4), (10.2, 10.82, 10.18, 10.8), (10.6, 11.22, 10.58, 11.2))  # 3 soldiers
    assert patterns.matches("star", "LONG", ms[1], ms[2], prev2=ms[0])
    assert not patterns.matches("star", "LONG", ms[1], ms[2])  # no prev2 -> no-op
    assert patterns.matches("three_white_soldiers", "LONG", tws[1], tws[2], prev2=tws[0])


def test_neutral_tokens_never_trigger():
    prev = (10.0, 10.05, 9.75, 9.8)
    spin = (10.0, 10.5, 9.5, 10.15)  # spinning top
    long_doji = (10.0, 10.5, 9.5, 10.02)  # long-legged doji
    for name in ("doji", "spinning_top", "long_legged_doji"):
        assert not patterns.matches(name, "LONG", prev, spin)
        assert not patterns.matches(name, "SHORT", prev, long_doji)
