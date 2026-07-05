"""SimulatedBackend fill + exit model."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trading_agent.execution.simulated import SimulatedBackend
from trading_agent.types import Candle, OrderRequest

UTC = timezone.utc
T0 = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
STEP = timedelta(minutes=15)


def _bar(o, h, l, c, t=T0) -> Candle:  # noqa: E741
    return Candle("AUD_USD", "M15", t, t + STEP, o, h, l, c, 100, True)


def _order(units, stop, tp, tid="t1") -> OrderRequest:
    return OrderRequest("AUD_USD", units, stop, tp, "s1", tid, "v1.0.0")


def _backend() -> SimulatedBackend:
    return SimulatedBackend(pip_size=0.0001, spread_pips=1.0, slippage_pips=0.0)  # cost = 1 pip = 0.0001


def test_fills_next_bar_open_plus_cost():
    b = _backend()
    b.submit(_order(1000, stop=0.6680, tp=0.6740))
    events = b.reconcile(T0, _bar(0.6700, 0.6705, 0.6698, 0.6702))
    assert len(events) == 1
    fill = events[0]
    assert fill.kind == "FILL"
    assert fill.price == pytest.approx(0.6701)  # open 0.6700 + 1 pip
    assert b.open_trades() == ["t1"]


def test_long_take_profit():
    b = _backend()
    b.submit(_order(1000, 0.6680, 0.6740))
    b.reconcile(T0, _bar(0.6700, 0.6705, 0.6698, 0.6702))  # fill at 0.6701
    events = b.reconcile(T0 + STEP, _bar(0.6710, 0.6745, 0.6708, 0.6740))
    exit_ev = events[-1]
    assert exit_ev.kind == "EXIT"
    assert exit_ev.exit_reason == "TP"
    assert exit_ev.price == pytest.approx(0.6740)
    assert exit_ev.pnl == pytest.approx(1000 * (0.6740 - 0.6701))
    assert b.open_trades() == []


def test_long_stop():
    b = _backend()
    b.submit(_order(1000, 0.6680, 0.6740))
    b.reconcile(T0, _bar(0.6700, 0.6705, 0.6698, 0.6702))
    events = b.reconcile(T0 + STEP, _bar(0.6695, 0.6698, 0.6675, 0.6690))
    assert events[-1].exit_reason == "STOP"
    assert events[-1].price == pytest.approx(0.6680)
    assert events[-1].pnl == pytest.approx(1000 * (0.6680 - 0.6701))


def test_both_hit_assumes_stop_first():
    b = _backend()
    b.submit(_order(1000, 0.6680, 0.6740))
    b.reconcile(T0, _bar(0.6700, 0.6705, 0.6698, 0.6702))
    events = b.reconcile(T0 + STEP, _bar(0.6700, 0.6745, 0.6675, 0.6690))  # touches both
    assert events[-1].exit_reason == "STOP"
    assert events[-1].price == pytest.approx(0.6680)


def test_weekend_gap_through_stop_fills_at_open():
    b = _backend()
    b.submit(_order(1000, 0.6680, 0.6740))
    b.reconcile(T0, _bar(0.6700, 0.6705, 0.6698, 0.6702))
    events = b.reconcile(T0 + STEP, _bar(0.6670, 0.6672, 0.6665, 0.6668))  # opened below stop
    assert events[-1].exit_reason == "STOP"
    assert events[-1].price == pytest.approx(0.6670)  # worse gapped open, not 0.6680


def test_short_take_profit_sign():
    b = _backend()
    b.submit(_order(-1000, stop=0.6720, tp=0.6680))
    b.reconcile(T0, _bar(0.6700, 0.6702, 0.6698, 0.6700))  # fill at 0.6700 - 1 pip = 0.6699
    events = b.reconcile(T0 + STEP, _bar(0.6695, 0.6698, 0.6678, 0.6680))
    assert events[-1].exit_reason == "TP"
    assert events[-1].price == pytest.approx(0.6680)
    assert events[-1].pnl == pytest.approx(-1000 * (0.6680 - 0.6699))  # short profits as price falls
