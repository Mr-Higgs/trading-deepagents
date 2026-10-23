"""The lookahead firewall: snapshots must never reveal a candle that hadn't closed by `now`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from trading_agent.marketdata.base import FRAME_COLUMNS, last_closed, window
from trading_agent.marketdata.historical import HistoricalOandaSource

UTC = timezone.utc
START = datetime(2026, 7, 3, 7, 0, tzinfo=UTC)


def _frame(tf: str, n: int, step: timedelta, start: datetime = START) -> pd.DataFrame:
    rows = []
    for i in range(n):
        ot = start + i * step
        rows.append(("AUD_USD", tf, ot, ot + step, 1.0, 1.0, 1.0, 1.0, 100, True))
    return pd.DataFrame(rows, columns=FRAME_COLUMNS)


def _source() -> HistoricalOandaSource:
    return HistoricalOandaSource(
        {
            "H4": _frame("H4", 60, timedelta(hours=4)),
            "H1": _frame("H1", 200, timedelta(hours=1)),
            "M15": _frame("M15", 400, timedelta(minutes=15)),
        }
    )


def test_snapshot_never_reveals_future_candles():
    src = _source()
    now = START + timedelta(hours=12, minutes=30)  # arbitrary mid-stream instant
    snap = src.snapshot(now)
    for tf, df in snap.items():
        assert (df["close_time"] <= now).all(), f"{tf} leaked a future candle"


def test_forming_higher_tf_bar_is_excluded():
    src = _source()
    # A moment strictly inside an H4 bar: previous H4 closed at +12h, next closes at +16h.
    now = START + timedelta(hours=13)
    h4 = src.snapshot(now)["H4"]
    assert h4["close_time"].max() == START + timedelta(hours=12)  # the forming +16h bar is not present


def test_last_closed_and_window():
    df = _source()._frames["M15"]
    now = START + timedelta(minutes=15) * 100  # 100th bar's close (>=78 bars now available)
    lc = last_closed(df, now)
    assert lc is not None
    assert lc["close_time"] == now
    w = window(df, now, 78)  # ichimoku needs >= 78 rows
    assert len(w) == 78
    assert (w["close_time"] <= now).all()


def test_last_closed_none_before_any_bar():
    df = _source()._frames["M15"]
    assert last_closed(df, START - timedelta(days=1)) is None


def test_stream_is_ascending_and_complete():
    times = [c.close_time for c in _source().stream()]
    assert times == sorted(times)
    assert len(times) == 400


def test_snapshot_lookback_caps_rows_like_live_poller():
    src = _source()
    now = src._frames[src.entry_tf]["close_time"].iloc[-1].to_pydatetime()  # noqa: SLF001
    full = src.snapshot(now)
    capped = HistoricalOandaSource(src._frames, entry_tf=src.entry_tf, lookback=5).snapshot(now)  # noqa: SLF001
    for tf, df in capped.items():
        assert len(df) == min(5, len(full[tf]))
        assert df["close_time"].iloc[-1] == full[tf]["close_time"].iloc[-1]
