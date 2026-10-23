"""OANDA price="MBA" parsing + cache naming. Payload is a hand-built TEST FIXTURE."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from trading_agent.marketdata.base import BA_COLUMNS, FRAME_COLUMNS, has_bid_ask, normalize_frame, row_to_candle
from trading_agent.marketdata.oanda_client import batch_to_ba_rows, cache_path

UTC = timezone.utc
BATCH = [
    {"time": "2025-01-06T08:00:00.000000000Z", "complete": True, "volume": 10,
     "bid": {"o": "0.6200", "h": "0.6210", "l": "0.6190", "c": "0.6205"},
     "ask": {"o": "0.6202", "h": "0.6212", "l": "0.6192", "c": "0.6207"}},
    {"time": "2025-01-06T08:05:00.000000000Z", "complete": False, "volume": 1,
     "bid": {"o": "1", "h": "1", "l": "1", "c": "1"}, "ask": {"o": "1", "h": "1", "l": "1", "c": "1"}},
]


def test_ba_rows_and_candle_roundtrip():
    rows = batch_to_ba_rows(BATCH, "AUD_USD", "M5", timedelta(minutes=5))
    assert len(rows) == 1  # incomplete candle dropped
    df = normalize_frame(pd.DataFrame(rows, columns=FRAME_COLUMNS + BA_COLUMNS))
    assert has_bid_ask(df)
    c = row_to_candle(next(df.itertuples(index=False)))
    assert c.has_bid_ask and c.o == (0.62 + 0.6202) / 2 and c.ao == 0.6202 and c.bl == 0.619


def test_cache_path_keeps_mid_name_and_suffixes_ba(tmp_path):
    s, e = datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)
    assert cache_path(tmp_path, "AUD_USD", "M5", s, e).name == "AUD_USD_M5_20250101_20260101.csv"
    assert cache_path(tmp_path, "AUD_USD", "M5", s, e, "BA").name == "AUD_USD_M5_20250101_20260101_BA.csv"
