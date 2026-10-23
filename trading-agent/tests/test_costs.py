"""Hour-of-week spread model. Bid/ask frames here are synthetic TEST FIXTURES, not market data."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from trading_agent.costs import HOURS_PER_WEEK, SpreadModel, WideningProfile, hour_of_week

UTC = timezone.utc
MON = datetime(2025, 1, 6, tzinfo=UTC)  # a Monday


def test_hour_of_week_buckets():
    assert hour_of_week(MON) == 0
    assert hour_of_week(MON + timedelta(days=6, hours=23)) == HOURS_PER_WEEK - 1


def test_profile_widens_rollover_tokyo_and_weekend():
    m = SpreadModel.from_profile(1.0, WideningProfile(rollover_mult=3.0, tokyo_open_mult=1.5, weekend_open_mult=4.0))
    assert m.spread_pips(MON + timedelta(hours=10)) == 1.0
    assert m.spread_pips(MON + timedelta(hours=21)) == 3.0
    assert m.spread_pips(MON + timedelta(hours=0)) == 1.5
    assert m.spread_pips(MON + timedelta(days=6, hours=21)) == 4.0  # Sunday reopen


def _fixture_frame() -> pd.DataFrame:
    """Synthetic fixture: 2 weeks of M5 bars, 1-pip spread except 5 pips at 21:00 UTC."""
    rows = []
    t = MON
    while t < MON + timedelta(days=14):
        spread = 0.0005 if t.hour == 21 else 0.0001
        rows.append({"open_time": t, "bo": 0.65, "ao": 0.65 + spread})
        t += timedelta(minutes=5)
    return pd.DataFrame(rows)


def test_fit_from_bid_ask_recovers_buckets():
    m = SpreadModel.fit_from_frame(_fixture_frame(), 0.0001)
    assert m.source == "fitted"
    assert m.spread_pips(MON + timedelta(hours=21, minutes=30)) == pytest.approx(5.0)
    assert m.spread_pips(MON + timedelta(hours=9)) == pytest.approx(1.0)


def test_empty_bucket_uses_default_and_json_roundtrip(tmp_path):
    df = _fixture_frame()
    df = df[pd.to_datetime(df["open_time"]).dt.weekday != 2]  # no Wednesday bars
    m = SpreadModel.fit_from_frame(df, 0.0001, default_pips=2.0)
    assert m.spread_pips(MON + timedelta(days=2, hours=9)) == 2.0
    p = tmp_path / "s.json"
    p.write_text(m.to_json())
    assert SpreadModel.load(p).spread_pips(MON + timedelta(hours=21)) == pytest.approx(5.0)
