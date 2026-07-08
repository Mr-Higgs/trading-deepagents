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
