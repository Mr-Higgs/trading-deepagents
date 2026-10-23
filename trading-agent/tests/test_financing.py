"""Rollover swap. Rates are hand-picked TEST FIXTURES, not real broker rates."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trading_agent.financing import FinancingModel, PairFinancing, load_financing_yaml, parse_oanda_financing

UTC = timezone.utc
MODEL = FinancingModel(rates={"AUD_USD": PairFinancing(long_rate=-0.0365, short_rate=0.0365)})


def test_rollovers_between_counts_2100_utc_crossings():
    rs = MODEL.rollovers_between(datetime(2025, 1, 6, 20, tzinfo=UTC), datetime(2025, 1, 8, 22, tzinfo=UTC))
    assert [r.day for r in rs] == [6, 7, 8]
    assert MODEL.rollovers_between(datetime(2025, 1, 6, 21, tzinfo=UTC), datetime(2025, 1, 6, 21, 5, tzinfo=UTC)) == []


def test_charge_sign_and_wednesday_triple():
    tue = datetime(2025, 1, 7, 21, tzinfo=UTC)
    wed = datetime(2025, 1, 8, 21, tzinfo=UTC)
    # 100k units * 0.65 * 3.65% / 365 = 6.5 quote per day
    assert MODEL.charge_quote("AUD_USD", 100_000, 0.65, tue) == pytest.approx(-6.5)
    assert MODEL.charge_quote("AUD_USD", -100_000, 0.65, tue) == pytest.approx(6.5)
    assert MODEL.charge_quote("AUD_USD", 100_000, 0.65, wed) == pytest.approx(-19.5)
    assert MODEL.charge_quote("EUR_USD", 100_000, 1.1, tue) == 0.0


def test_yaml_and_oanda_payload(tmp_path):
    p = tmp_path / "financing.yaml"
    p.write_text("rollover_utc: '21:00'\npairs:\n  AUD_USD: {long_rate: -0.01, short_rate: 0.002}\n")
    m = load_financing_yaml(p)
    assert m is not None and m.rates["AUD_USD"].short_rate == 0.002
    assert load_financing_yaml(tmp_path / "missing.yaml") is None
    pf, days = parse_oanda_financing({"financing": {"longRate": "-0.02", "shortRate": "0.001", "financingDaysOfWeek": [{"dayOfWeek": "WEDNESDAY", "daysCharged": 3}]}})
    assert pf.long_rate == -0.02 and days == {2: 3}
