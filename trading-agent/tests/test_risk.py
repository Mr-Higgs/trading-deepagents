"""Position sizing, every rejection rule, the trading-day boundary, and the daily-loss halt."""

from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest
from conftest import make_account, make_day, make_pair, make_risk, make_signal

from trading_agent.fx_convert import FxConverter, FxRateUnavailable
from trading_agent.risk import RiskEngine, trading_day, update_day_state
from trading_agent.types import TradeEvent

UTC = timezone.utc
NOW = datetime(2026, 7, 3, 8, tzinfo=UTC)


def _engine(pair=None, risk=None) -> RiskEngine:
    return RiskEngine(risk or make_risk(), pair or make_pair())


# ---- sizing ----------------------------------------------------------------


def test_long_sizing():
    # equity 10000 * 1% = $100 risk; stop_distance 0.0020 => 50000 units, LONG => positive.
    dec = _engine().evaluate(make_signal("LONG", 0.6700, 0.6680), make_account(), make_day(), NOW)
    assert dec.approved
    assert dec.units == 50_000
    assert dec.risk_amount == 100.0


def test_short_is_negative_units():
    dec = _engine().evaluate(make_signal("SHORT", 0.6700, 0.6720), make_account(), make_day(), NOW)
    assert dec.approved
    assert dec.units == -50_000


def test_units_clamped_to_max():
    # tiny 5-pip stop would size 200000 units; max_units caps it.
    dec = _engine().evaluate(make_signal("LONG", 0.6700, 0.6695), make_account(), make_day(), NOW)
    assert dec.units == 100_000


# ---- rejection rules -------------------------------------------------------


def test_below_min_units_rejected():
    eng = _engine(pair=make_pair(min_units=60_000))  # 50000 sized < 60000 floor
    dec = eng.evaluate(make_signal(), make_account(), make_day(), NOW)
    assert not dec.approved
    assert dec.reason == "below_min_units"


def test_stop_distance_invalid_rejected():
    dec = _engine().evaluate(make_signal("LONG", 0.6700, 0.6700), make_account(), make_day(), NOW)
    assert dec.reason == "stop_distance_invalid"
    assert dec.units == 0


def test_kill_switch_wins_over_everything():
    eng = _engine(risk=make_risk(kill_switch=True))
    # also breach max trades + halted; kill switch must still be the reported reason.
    day = make_day(trades_opened=99, halted=True, halt_reason="daily_loss_breached")
    dec = eng.evaluate(make_signal(), make_account(), day, NOW)
    assert dec.reason == "kill_switch_on"


def test_day_halted_rejected_with_halt_reason():
    day = make_day(halted=True, halt_reason="daily_loss_breached")
    dec = _engine().evaluate(make_signal(), make_account(), day, NOW)
    assert dec.reason == "daily_loss_breached"


def test_max_trades_reached_rejected():
    dec = _engine().evaluate(make_signal(), make_account(), make_day(trades_opened=3), NOW)
    assert dec.reason == "max_trades_reached"


# ---- v1.1 containment rules ------------------------------------------------


def test_max_concurrent_positions_rejected():
    eng = _engine(risk=make_risk(max_concurrent_positions=1))
    dec = eng.evaluate(make_signal(), make_account(open_positions=1), make_day(), NOW)
    assert not dec.approved
    assert dec.reason == "max_concurrent"


def test_max_concurrent_allows_when_flat():
    eng = _engine(risk=make_risk(max_concurrent_positions=1))
    dec = eng.evaluate(make_signal("LONG", 0.6700, 0.6680), make_account(open_positions=0), make_day(), NOW)
    assert dec.approved


def test_below_min_stop_rejected():
    # min_stop_pips=8 => threshold 0.0008; a 5-pip stop (0.0005) is too tight.
    eng = _engine(risk=make_risk(min_stop_pips=8))
    dec = eng.evaluate(make_signal("LONG", 0.6700, 0.6695), make_account(), make_day(), NOW)
    assert not dec.approved
    assert dec.reason == "below_min_stop"


def test_min_stop_allows_adequate_stop():
    eng = _engine(risk=make_risk(min_stop_pips=8))
    dec = eng.evaluate(make_signal("LONG", 0.6700, 0.6680), make_account(), make_day(), NOW)  # 20-pip stop
    assert dec.approved


def test_daily_loss_gate_without_halt_flag():
    # realized -301 vs threshold -300 (3% of 10000), halt flag not yet set => gate still fires.
    day = make_day(realized_pnl=-301.0)
    dec = _engine().evaluate(make_signal(), make_account(), day, NOW)
    assert dec.reason == "daily_loss_breached"


def test_quote_account_mismatch_without_rate_raises_at_sizing():
    eng = RiskEngine(make_risk(), make_pair(quote_ccy="JPY"))
    with pytest.raises(FxRateUnavailable):
        eng.evaluate(make_signal("LONG", 0.6700, 0.6680), make_account(), make_day(), NOW)


def test_quote_account_mismatch_sizes_through_cross():
    # Hand-built: quote JPY, account USD, USD_JPY = 150 => 1 JPY = 1/150 USD.
    # $100 risk / (0.0020 JPY stop * 1/150) = 7_500_000 units (clamped to max).
    pair = make_pair(quote_ccy="JPY", max_units=10_000_000)
    eng = RiskEngine(make_risk(), pair, fx=FxConverter(static={"USD_JPY": 150.0}))
    dec = eng.evaluate(make_signal("LONG", 0.6700, 0.6680), make_account(), make_day(), NOW)
    assert dec.approved
    assert abs(dec.units - 7_500_000) <= 1  # float floor noise on the 1/150 cross


def test_margin_check_shrinks_units_to_free_margin():
    # leverage 20 at 0.67 => 0.0335 USD margin/unit; 10000 equity * 0.5 usage => 149253 units cap.
    eng = RiskEngine(make_risk(leverage=20.0, max_margin_usage=0.5), make_pair(max_units=1_000_000))
    dec = eng.evaluate(make_signal("LONG", 0.6700, 0.6695), make_account(), make_day(), NOW)
    assert dec.approved
    assert dec.units == 149_253


def test_margin_check_rejects_when_no_free_margin():
    eng = RiskEngine(make_risk(leverage=20.0), make_pair())
    acct = make_account()
    acct.margin_used = 10_000.0
    dec = eng.evaluate(make_signal("LONG", 0.6700, 0.6680), acct, make_day(), NOW)
    assert dec.reason == "insufficient_margin"


def test_tp_wrong_side_long_rejected():
    sig = make_signal("LONG", 0.6700, 0.6680, tp=0.6690)  # tp below entry
    dec = _engine().evaluate(sig, make_account(), make_day(), NOW)
    assert dec.reason == "tp_wrong_side"


def test_stop_wrong_side_short_rejected():
    sig = make_signal("SHORT", 0.6700, 0.6680)  # stop below entry for short
    dec = _engine().evaluate(sig, make_account(), make_day(), NOW)
    assert dec.reason == "stop_wrong_side"


def test_rr_below_floor_rejected():
    sig = make_signal("LONG", 0.6700, 0.6680, tp=0.6705)  # 0.25R reward
    dec = _engine(risk=make_risk(min_rr=1.0)).evaluate(sig, make_account(), make_day(), NOW)
    assert dec.reason == "rr_below_floor"


# ---- trading-day boundary (21:00 UTC rollover) -----------------------------


@pytest.mark.parametrize(
    ("ts", "expected"),
    [
        (datetime(2026, 7, 3, 20, 59, tzinfo=UTC), date(2026, 7, 2)),  # before rollover -> prev day
        (datetime(2026, 7, 3, 21, 0, tzinfo=UTC), date(2026, 7, 3)),  # at rollover -> new day
        (datetime(2026, 7, 4, 5, 0, tzinfo=UTC), date(2026, 7, 3)),  # early morning -> same session
    ],
)
def test_trading_day_rollover(ts, expected):
    assert trading_day(ts, time(21, 0)) == expected


# ---- daily-loss halt via update_day_state ----------------------------------


def test_update_day_state_trips_halt_on_cumulative_loss():
    day = make_day()
    update_day_state(day, TradeEvent(NOW, "t1", "EXIT", 0.66, 1, pnl=-200.0), equity=10_000, max_daily_loss_pct=0.03)
    assert not day.halted
    update_day_state(day, TradeEvent(NOW, "t2", "EXIT", 0.66, 1, pnl=-150.0), equity=10_000, max_daily_loss_pct=0.03)
    assert day.halted
    assert day.halt_reason == "daily_loss_breached"
    assert day.realized_pnl == -350.0


def test_update_day_state_ignores_fills():
    day = make_day()
    update_day_state(day, TradeEvent(NOW, "t1", "FILL", 0.66, 1), equity=10_000, max_daily_loss_pct=0.03)
    assert day.realized_pnl == 0.0
    assert not day.halted
