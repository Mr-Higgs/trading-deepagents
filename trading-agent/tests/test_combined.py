"""ichimoku_combined: top-down score picks direction, any trigger fires, price-action gate confirms."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
from conftest import make_pair, make_strategy
from trading_agent.strategy import ichimoku_combined as mod
from trading_agent.strategy.base import StrategyResult
from trading_agent.types import Signal

UTC = timezone.utc
NOW = datetime(2026, 1, 6, 8, 0, tzinfo=UTC)


def _ramp(n: int, start: float, step: float) -> pd.DataFrame:
    rows = [{"o": (c := start + i * step), "h": c + step, "l": c - step, "c": c, "complete": True} for i in range(n)]
    return pd.DataFrame(rows)


def _up() -> dict[str, pd.DataFrame]:
    # strongly rising across all TFs -> every confluence vote is bullish -> direction LONG, no h4 conflict
    return {"H4": _ramp(90, 0.6500, 0.0004), "H1": _ramp(90, 0.6600, 0.0002), "M15": _ramp(90, 0.6700, 0.0001)}


def _inner_sig(direction: str = "LONG", setup_id: str = "ichimoku_pullback") -> Signal:
    return Signal(NOW, "inner-1", "AUD_USD", direction, 0.6700, 0.6680, 0.6740, 2.0, setup_id, "v1", {}, "trig")


def _combined(**overrides) -> mod.IchimokuCombined:
    return mod.IchimokuCombined(make_strategy(name="ichimoku_combined", tp_mode="rr", **overrides), make_pair())


def test_insufficient_history():
    snap = {"H4": _ramp(5, 0.65, 0.0004), "H1": _ramp(5, 0.66, 0.0002), "M15": _ramp(5, 0.67, 0.0001)}
    assert _combined().evaluate(snap, NOW).reason == "insufficient_history"


def test_no_direction_when_score_zero(monkeypatch):
    monkeypatch.setattr(mod.signals, "price_level_votes", lambda *a, **k: 0)
    res = _combined().evaluate(_up(), NOW)
    assert res.signal is None and res.reason == "no_direction"


def test_h4_conflict_when_trend_tf_disagrees(monkeypatch):
    # H4 votes 0 (weight 3) but H1+M15 push the net score positive -> LONG against a non-bullish H4.
    votes = iter([0, 1, 1])  # order: trend, bias, entry
    monkeypatch.setattr(mod.signals, "price_level_votes", lambda *a, **k: next(votes))
    res = _combined().evaluate(_up(), NOW)
    assert res.signal is None and res.reason == "h4_conflict"


def test_first_qualifying_trigger_wins_and_shortcircuits():
    strat = _combined(require_price_action=False)
    m_pull = MagicMock(evaluate=MagicMock(return_value=StrategyResult(_inner_sig(setup_id="ichimoku_pullback"), "trig")))
    m_break = MagicMock(evaluate=MagicMock(return_value=StrategyResult(_inner_sig(setup_id="ichimoku_breakout"), "trig")))
    strat._inners = [m_pull, m_break]
    res = strat.evaluate(_up(), NOW)
    assert res.signal is not None
    assert res.signal.setup_id == "ichimoku_combined"
    assert res.signal.features["trigger_setup"] == "ichimoku_pullback"
    assert res.reason == "combined_long"
    assert res.signal.features["combined_score"] > 0
    m_break.evaluate.assert_not_called()  # first match short-circuits


def test_direction_conflict_skips_to_next_trigger():
    strat = _combined(require_price_action=False)
    m_short = MagicMock(evaluate=MagicMock(return_value=StrategyResult(_inner_sig(direction="SHORT", setup_id="ichimoku_pullback"), "trig")))
    m_long = MagicMock(evaluate=MagicMock(return_value=StrategyResult(_inner_sig(direction="LONG", setup_id="ichimoku_breakout"), "trig")))
    strat._inners = [m_short, m_long]
    res = strat.evaluate(_up(), NOW)
    assert res.signal is not None and res.signal.features["trigger_setup"] == "ichimoku_breakout"


def test_no_trigger_when_none_fire():
    strat = _combined(require_price_action=False)
    strat._inners = [MagicMock(evaluate=MagicMock(return_value=StrategyResult(None, "no_trigger")))]
    res = strat.evaluate(_up(), NOW)
    assert res.signal is None and res.reason == "no_trigger"


def test_price_action_gate_blocks(monkeypatch):
    strat = _combined(require_price_action=True, price_action_signals=("bos",))
    strat._inners = [MagicMock(evaluate=MagicMock(return_value=StrategyResult(_inner_sig(), "trig")))]
    monkeypatch.setattr(mod.price_action, "matches", lambda *a, **k: ())
    res = strat.evaluate(_up(), NOW)
    assert res.signal is None and res.reason == "no_price_action:bos"


def test_price_action_gate_passes(monkeypatch):
    strat = _combined(require_price_action=True, price_action_signals=("bos",))
    strat._inners = [MagicMock(evaluate=MagicMock(return_value=StrategyResult(_inner_sig(), "trig")))]
    monkeypatch.setattr(mod.price_action, "matches", lambda *a, **k: ("bos",))
    res = strat.evaluate(_up(), NOW)
    assert res.signal is not None
    assert res.signal.features["price_action"] == ["bos"]
    assert res.signal.features["confluence_direction"] == "LONG"
