"""Confluence ichimoku_level TP must respect min R:R floor."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from conftest import make_pair, make_strategy
from trading_agent.strategy.base import StrategyResult
from trading_agent.types import Signal

UTC = timezone.utc
NOW = datetime(2026, 1, 6, 8, 0, tzinfo=UTC)


def test_confluence_tp_floor_keeps_rr_tp_when_level_too_close(monkeypatch):
    from trading_agent.strategy import ichimoku_confluence as ic_mod

    inner_sig = Signal(
        NOW,
        "inner-1",
        "AUD_USD",
        "LONG",
        0.6700,
        0.6680,
        0.6740,
        2.0,
        "ichimoku_breakout",
        "v1",
        {},
        "tk_cross_long",
    )
    mock_inner = MagicMock()
    mock_inner.evaluate.return_value = StrategyResult(inner_sig, "tk_cross_long")

    strat = ic_mod.IchimokuConfluence(make_strategy(name="ichimoku_confluence", tp_mode="ichimoku_level"), make_pair())
    strat._inner = mock_inner

    # Near target: only 5 pips above entry while risk is 20 pips => below 1R floor
    monkeypatch.setattr(ic_mod.signals, "ichimoku_level_target", lambda *a, **k: 0.6705)

    up = {
        "H4": _ramp("H4", 90, 0.6500, 0.0004),
        "H1": _ramp("H1", 90, 0.6600, 0.0002),
        "M15": _ramp("M15", 90, 0.6700, 0.0001),
    }
    res = strat.evaluate(up, NOW)
    assert res.signal is not None
    assert res.signal.tp == 0.6740
    assert res.signal.features.get("tp_source") == "rr"


def _ramp(tf: str, n: int, start: float, step: float):
    import pandas as pd

    rows = []
    for i in range(n):
        c = start + i * step
        rows.append({"o": c, "h": c + step, "l": c - step, "c": c, "complete": True})
    return pd.DataFrame(rows)
