"""ichimoku_cloud_breakout: breakout-scan logic + end-to-end self-check."""

from __future__ import annotations

import pandas as pd

from trading_agent.strategy.ichimoku_cloud_breakout import _demo, _recent_breakout


def test_self_check() -> None:
    _demo()


def test_recent_breakout_up() -> None:
    close = [1.00, 0.98, 0.99, 1.06, 1.05]  # close crosses cloud_top (1.05) at bar 3
    df = pd.DataFrame({"c": close, "h": [c + 0.01 for c in close], "l": [c - 0.01 for c in close]})
    ich = pd.DataFrame({"cloud_top": [1.05] * 5, "cloud_bottom": [1.00] * 5})
    assert _recent_breakout(df, ich, lookback=6) == ("LONG", 3)


def test_recent_breakout_down() -> None:
    close = [1.00, 1.02, 1.01, 0.94, 0.95]  # close crosses cloud_bottom (0.95) at bar 3
    df = pd.DataFrame({"c": close, "h": [c + 0.01 for c in close], "l": [c - 0.01 for c in close]})
    ich = pd.DataFrame({"cloud_top": [1.00] * 5, "cloud_bottom": [0.95] * 5})
    assert _recent_breakout(df, ich, lookback=6) == ("SHORT", 3)


def test_recent_breakout_none_and_lookback_window() -> None:
    # no crossing at all
    flat = pd.DataFrame({"c": [1.2, 1.2, 1.2], "h": [1.21] * 3, "l": [1.19] * 3})
    ich = pd.DataFrame({"cloud_top": [1.0] * 3, "cloud_bottom": [0.9] * 3})
    assert _recent_breakout(flat, ich, lookback=6) is None
    # a breakout that is older than the lookback window is not returned
    close = [0.98, 1.06, 1.07, 1.08, 1.09, 1.10, 1.11]  # break at bar 1, current bar is 6
    df = pd.DataFrame({"c": close, "h": [c + 0.01 for c in close], "l": [c - 0.01 for c in close]})
    ich2 = pd.DataFrame({"cloud_top": [1.05] * 7, "cloud_bottom": [1.00] * 7})
    assert _recent_breakout(df, ich2, lookback=2) is None
