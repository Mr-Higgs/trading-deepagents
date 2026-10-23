"""Account-currency conversion. Rates are TEST FIXTURES."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from trading_agent.fx_convert import FxConverter, FxRateUnavailable, conversion_instrument

UTC = timezone.utc
T = datetime(2025, 3, 3, 12, tzinfo=UTC)


def test_identity_direct_inverse():
    fx = FxConverter(static={"USD_JPY": 150.0, "EUR_USD": 1.1})
    assert fx.rate("USD", "USD", T) == 1.0
    assert fx.rate("USD", "JPY", T) == 150.0
    assert fx.rate("JPY", "USD", T) == pytest.approx(1 / 150.0)
    assert fx.convert(100.0, "EUR", "USD", T) == pytest.approx(110.0)
    with pytest.raises(FxRateUnavailable):
        fx.rate("GBP", "USD", T)


def test_frame_rate_is_lookahead_safe():
    df = pd.DataFrame({"close_time": [T, T + timedelta(hours=1)], "c": [150.0, 160.0]})
    fx = FxConverter(frames={"USD_JPY": df})
    assert fx.rate("USD", "JPY", T + timedelta(minutes=30)) == 150.0  # next close not yet known
    assert fx.rate("USD", "JPY", T + timedelta(hours=1)) == 160.0
    with pytest.raises(FxRateUnavailable):
        fx.rate("USD", "JPY", T - timedelta(minutes=1))


def test_conversion_instrument():
    assert conversion_instrument("USD", "USD") is None
    assert conversion_instrument("JPY", "USD") == "USD_JPY"
    assert conversion_instrument("USD", "EUR") == "EUR_USD"
