"""Bid/ask fill model, slippage, financing and FX in SimulatedBackend. Candles are TEST FIXTURES."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trading_agent.costs import CostModel, SpreadModel
from trading_agent.execution.simulated import SimulatedBackend
from trading_agent.financing import FinancingModel, PairFinancing
from trading_agent.fx_convert import FxConverter
from trading_agent.types import Candle, OrderRequest

UTC = timezone.utc
T0 = datetime(2025, 1, 7, 8, tzinfo=UTC)  # Tuesday
STEP = timedelta(minutes=15)
PIP = 0.0001


def _ba_bar(mid_o, mid_h, mid_l, mid_c, spread=0.0002, t=T0) -> Candle:
    h = spread / 2
    return Candle("AUD_USD", "M15", t, t + STEP, mid_o, mid_h, mid_l, mid_c, 100, True,
                  mid_o - h, mid_h - h, mid_l - h, mid_c - h, mid_o + h, mid_h + h, mid_l + h, mid_c + h)


def _order(units, stop, tp) -> OrderRequest:
    return OrderRequest("AUD_USD", units, stop, tp, "s1", "t1", "v1")


def _backend(slip=0.0, **kw) -> SimulatedBackend:
    return SimulatedBackend(PIP, 0.0, 0.0, cost_model=CostModel(SpreadModel.flat(1.0), slippage_pips=slip, stop_slippage_pips=slip), **kw)


def test_long_fills_at_ask_short_at_bid():
    b = _backend(slip=0.5)
    b.submit(_order(1000, 0.6680, 0.6740))
    fill = b.reconcile(T0, _ba_bar(0.6700, 0.6702, 0.6699, 0.6701))[0]
    assert fill.price == pytest.approx(0.6701 + 0.00005)  # ask open + 0.5 pip slippage
    s = _backend()
    s.submit(_order(-1000, 0.6720, 0.6660))
    assert s.reconcile(T0, _ba_bar(0.6700, 0.6702, 0.6699, 0.6701))[0].price == pytest.approx(0.6699)


def test_long_stop_evaluated_on_bid_side():
    b = _backend()
    b.submit(_order(1000, 0.6690, 0.6740))
    b.reconcile(T0, _ba_bar(0.6700, 0.6702, 0.6699, 0.6701))
    # mid low 0.66905 never touches 0.6690, but BID low (mid - 1 pip) = 0.66895 does
    ev = b.reconcile(T0 + STEP, _ba_bar(0.6695, 0.6696, 0.66905, 0.6693, t=T0 + STEP))
    assert ev[0].exit_reason == "STOP"
    assert ev[0].pnl == pytest.approx(1000 * (0.6690 - 0.6701))


def test_short_stop_evaluated_on_ask_side():
    b = _backend()
    b.submit(_order(-1000, 0.6710, 0.6660))
    b.reconcile(T0, _ba_bar(0.6700, 0.6702, 0.6699, 0.6701))
    ev = b.reconcile(T0 + STEP, _ba_bar(0.6705, 0.67095, 0.6704, 0.6706, t=T0 + STEP))  # ask high 0.67105
    assert ev[0].exit_reason == "STOP"


def test_mid_fallback_synthesizes_spread_when_flag_off():
    b = _backend(use_bid_ask=False)
    b.submit(_order(1000, 0.6680, 0.6740))
    bar = _ba_bar(0.6700, 0.6702, 0.6699, 0.6701, spread=0.0010)  # real spread 10 pips ignored
    assert b.reconcile(T0, bar)[0].price == pytest.approx(0.67005)  # mid + modeled half spread (0.5 pip)


def test_legacy_mode_unchanged():
    b = SimulatedBackend(PIP, 1.2, 0.3)
    b.submit(_order(1000, 0.6680, 0.6740))
    assert b.reconcile(T0, _ba_bar(0.6700, 0.6702, 0.6699, 0.6701))[0].price == pytest.approx(0.67015)


def test_financing_and_fx_conversion():
    fin = FinancingModel(rates={"AUD_USD": PairFinancing(-0.0365, 0.0)})
    b = _backend(financing=fin)
    b.submit(_order(100_000, 0.6000, 0.7500))
    b.reconcile(T0, _ba_bar(0.6500, 0.6501, 0.6499, 0.6500))
    t1 = datetime(2025, 1, 7, 21, 15, tzinfo=UTC)  # crossed Tuesday 21:00 rollover
    ev = b.reconcile(t1, _ba_bar(0.6500, 0.6501, 0.6499, 0.6500, t=t1 - STEP))
    fin_ev = [e for e in ev if e.kind == "FINANCING"]
    assert len(fin_ev) == 1 and fin_ev[0].pnl == pytest.approx(-6.5)

    with pytest.raises(ValueError, match="FxConverter"):
        SimulatedBackend(PIP, 0, 0, quote_ccy="JPY", account_ccy="USD")
    jpy = SimulatedBackend(PIP, 0, 0, fx=FxConverter(static={"USD_JPY": 100.0}), quote_ccy="JPY", account_ccy="USD")
    assert jpy.to_account(1000.0, T0) == pytest.approx(10.0)


def test_no_rollover_swap_on_trade_stopped_inside_the_bar():
    fin = FinancingModel(rates={"AUD_USD": PairFinancing(-0.0365, -0.0365)})
    b = _backend(financing=fin)
    t_fill = datetime(2025, 1, 7, 20, 30, tzinfo=UTC)
    b.submit(_order(1000, 0.6680, 0.6740))
    b.reconcile(t_fill, _ba_bar(0.6700, 0.6702, 0.6699, 0.6701, t=t_fill))
    t_roll = datetime(2025, 1, 7, 21, 0, tzinfo=UTC)  # bar closing at rollover hits the stop
    events = b.reconcile(t_roll, _ba_bar(0.6700, 0.6701, 0.6670, 0.6675, t=t_roll - STEP))
    assert [e.kind for e in events] == ["EXIT"]


def test_scale_out_rejected_when_bid_never_reached_level():
    b = _backend()
    b.submit(_order(1000, 0.6680, 0.6800))
    b.reconcile(T0, _ba_bar(0.6700, 0.6702, 0.6699, 0.6701))
    t1 = T0 + STEP
    b.reconcile(t1, _ba_bar(0.6701, 0.6750, 0.6700, 0.6740, spread=0.0010, t=t1))  # bid high 0.6745
    assert b.reduce_position("t1", 500, 0.6750, t1) is None
    ev = b.reduce_position("t1", 500, 0.6740, t1)
    assert ev is not None and ev.price == pytest.approx(0.67395)


def test_liquidate_closes_open_trades_at_executable_close():
    b = _backend()
    b.submit(_order(1000, 0.6680, 0.6800))
    b.reconcile(T0, _ba_bar(0.6700, 0.6702, 0.6699, 0.6710))
    (ev,) = b.liquidate()
    assert ev.exit_reason == "END_OF_WINDOW" and ev.price == pytest.approx(0.6709)  # bid close
    assert b.open_trades() == [] and b.liquidate() == []


def test_boundary_liquidation_log_is_analyzable(tmp_path):
    from optimize.evaluate import _close_at_boundary
    from trading_agent.analytics import analyze
    from trading_agent.eventlog import EventLog

    b = _backend()
    b.submit(_order(1000, 0.6680, 0.6800))
    b.reconcile(T0, _ba_bar(0.6700, 0.6702, 0.6699, 0.6710))
    path = tmp_path / "log.jsonl"
    log = EventLog(path, run_id="t", strategy_version="v")
    _close_at_boundary(b, log)
    log.close()
    assert analyze(path, starting_equity=10_000.0)["trades"] == 1
