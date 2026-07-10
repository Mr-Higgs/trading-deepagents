"""Price-action structure detectors."""

from __future__ import annotations

import pandas as pd

from trading_agent.strategy import price_action


def _frame() -> pd.DataFrame:
    # confirmed swing high @2 (h=12), swing low @5 (l=7); last bar (h=13) is the global max but unconfirmable
    return pd.DataFrame(
        {
            "o": [10, 10, 11, 11, 10, 10, 9, 10, 11],
            "h": [10, 11, 12, 11, 10, 9, 10, 11, 13],
            "l": [9, 10, 11, 8, 9, 7, 8, 9, 12],
            "c": [10, 11, 11, 10, 10, 9, 10, 11, 12.5],
        }
    )


def test_self_check():
    price_action._demo()  # asserts internally


def test_no_lookahead():
    # The highest high in the whole frame is the LAST bar. It can only become a swing once `lookback`
    # more bars print, so it must NOT be returned. Removing the confirmation ceiling makes this fail.
    df = _frame()
    lookback = 2
    highs = price_action.swing_highs(df, lookback)
    assert len(df) - 1 not in highs
    assert highs and max(highs) <= len(df) - 1 - lookback
    assert price_action._confirmed_hi(len(df), lookback) == len(df) - 1 - lookback
    # ...and low side too
    lows = price_action.swing_lows(df, lookback)
    assert all(i <= len(df) - 1 - lookback for i in lows)


def test_swing_detection_is_strict_fractal():
    df = _frame()
    assert price_action.swing_highs(df, 2) == [2]
    assert price_action.swing_lows(df, 2) == [5]


def test_break_of_structure_is_direction_specific():
    df = _frame()  # close[-1] = 12.5, confirmed high = 12, confirmed low = 7
    assert price_action.break_of_structure(df, "LONG", 2) is True
    assert price_action.break_of_structure(df, "SHORT", 2) is False
    df_short = df.copy()
    df_short.loc[df_short.index[-1], "c"] = 6.5
    assert price_action.break_of_structure(df_short, "SHORT", 2) is True
    assert price_action.break_of_structure(df_short, "LONG", 2) is False


def test_sr_clustering_merges_within_tolerance():
    df = pd.DataFrame(
        {"o": [10] * 7, "h": [10, 12.00, 10, 11, 12.01, 11, 9], "l": [9] * 7, "c": [10] * 7}
    )
    two = price_action.sr_levels(df, lookback=1, tolerance=0.0)
    assert len(two) == 2 and abs(two[0] - 12.0) < 1e-9 and abs(two[1] - 12.01) < 1e-9
    one = price_action.sr_levels(df, lookback=1, tolerance=0.05)
    assert len(one) == 1 and abs(one[0] - 12.005) < 1e-9


def test_matches_reports_fired_set_for_any_and_all_gating():
    df = _frame()
    both = price_action.matches(("bos", "sr_confluence"), "LONG", df, 12.0, lookback=2, tolerance=0.5)
    assert both == ("bos", "sr_confluence")  # sorted tuple, not a bool
    assert bool(both)  # ANY-of truthiness
    assert set(("bos", "sr_confluence")) <= set(both)  # ALL-of subset
    only_bos = price_action.matches(("bos", "sr_confluence"), "LONG", df, 12.5, lookback=2, tolerance=0.05)
    assert only_bos == ("bos",)
    assert not (set(("bos", "sr_confluence")) <= set(only_bos))
    assert price_action.matches("bos", "SHORT", df, 12.5, lookback=2) == ()


def test_short_frame_returns_empty_and_never_raises():
    tiny = pd.DataFrame({"o": [1, 1, 1], "h": [1, 2, 1], "l": [1, 1, 1], "c": [1, 2, 1]})
    assert price_action.swing_highs(tiny, 2) == []
    assert price_action.swing_lows(tiny, 2) == []
    assert price_action.break_of_structure(tiny, "LONG", 2) is False
    assert price_action.sr_levels(tiny, 2) == []
    assert price_action.matches(("bos", "sr_confluence"), "LONG", tiny, 1.5, lookback=2) == ()
