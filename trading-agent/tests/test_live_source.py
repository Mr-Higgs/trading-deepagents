"""LiveOandaSource yields each closed bar exactly once (dedupe by close_time)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from trading_agent.marketdata.base import FRAME_COLUMNS
from trading_agent.marketdata.live import LiveOandaSource

UTC = timezone.utc
START = datetime(2026, 1, 6, 8, 0, tzinfo=UTC)
STEP = timedelta(minutes=15)


def _m15(n: int) -> pd.DataFrame:
    rows = [("AUD_USD", "M15", START + i * STEP, START + (i + 1) * STEP, 0.67, 0.67, 0.67, 0.67, 100, True) for i in range(n)]
    return pd.DataFrame(rows, columns=FRAME_COLUMNS)


def _src() -> LiveOandaSource:
    return LiveOandaSource(client=None, instrument="AUD_USD", timeframes={"trend": "H4", "bias": "H1", "entry": "M15"})


def test_emits_each_close_once():
    src = _src()
    first = src._emit_new(_m15(3))
    assert [c.close_time for c in first] == [START + STEP, START + 2 * STEP, START + 3 * STEP]

    # next poll returns the same 3 plus 1 new bar -> only the new one is emitted
    second = src._emit_new(_m15(4))
    assert [c.close_time for c in second] == [START + 4 * STEP]

    # a poll with nothing new emits nothing
    assert src._emit_new(_m15(4)) == []


def test_stream_calls_on_poll_each_iteration(monkeypatch):
    class _Stop(Exception):
        pass

    class Client:
        def recent_candles(self, instrument, gran, lookback):
            return _m15(4)

    polls = []
    src = LiveOandaSource(client=Client(), instrument="AUD_USD", timeframes={"trend": "H4", "bias": "H1", "entry": "M15"}, on_poll=lambda: polls.append(1))
    sleeps = {"n": 0}

    def fake_sleep(_):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise _Stop

    monkeypatch.setattr("trading_agent.marketdata.live.time.sleep", fake_sleep)
    with pytest.raises(_Stop):
        for _ in src.stream():
            pass
    assert len(polls) == 2
