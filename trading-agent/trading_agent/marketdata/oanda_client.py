"""Thin OANDA v20 candle fetcher (data only; order placement lives in execution/oanda_paper.py).

Network code - exercised by the live dry-run, not the unit suite.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from oandapyV20 import API
from oandapyV20.endpoints.instruments import InstrumentsCandles

from trading_agent.marketdata.base import BA_COLUMNS, FRAME_COLUMNS, GRANULARITY_DELTAS as _GRAN_DELTA, normalize_frame
from trading_agent.oanda_env import practice_environment, with_retries

_PRICE_KEY = {"M": "mid", "B": "bid", "A": "ask"}
_MAX_COUNT = 5000  # OANDA per-request candle cap


def _iso_z(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _ohlc(block: dict) -> tuple[float, float, float, float]:
    return float(block["o"]), float(block["h"]), float(block["l"]), float(block["c"])


def _batch_to_rows(batch: list[dict], instrument: str, granularity: str, delta: timedelta, price_key: str) -> list[tuple]:
    """Convert an OANDA candle batch to frame rows, dropping the still-forming candle."""
    rows = []
    for cndl in batch:
        if not cndl.get("complete"):
            continue
        ot = _parse_time(cndl["time"])  # OANDA candle time == bar OPEN
        rows.append((instrument, granularity, ot, ot + delta, *_ohlc(cndl[price_key]), int(cndl["volume"]), True))
    return rows


def batch_to_ba_rows(batch: list[dict], instrument: str, granularity: str, delta: timedelta) -> list[tuple]:
    """Convert an OANDA price="MBA" batch into rows of FRAME_COLUMNS + BA_COLUMNS (mid + bid + ask)."""
    rows = []
    for cndl in batch:
        if not cndl.get("complete"):
            continue
        ot = _parse_time(cndl["time"])
        bid, ask = _ohlc(cndl["bid"]), _ohlc(cndl["ask"])
        mid = _ohlc(cndl["mid"]) if "mid" in cndl else tuple((b + a) / 2 for b, a in zip(bid, ask, strict=True))
        rows.append((instrument, granularity, ot, ot + delta, *mid, int(cndl["volume"]), True, *bid, *ask))
    return rows


def cache_path(cache_dir: Path, instrument: str, granularity: str, start: datetime, end: datetime, price: str = "M") -> Path:
    """Cache filename. Mid keeps the legacy name so existing caches stay valid; BA adds a `_BA` suffix."""
    suffix = "" if price == "M" else "_BA"
    return cache_dir / f"{instrument}_{granularity}_{start:%Y%m%d}_{end:%Y%m%d}{suffix}.csv"


class OandaClient:
    def __init__(self, api_key: str, environment: str = "practice") -> None:
        self._api = with_retries(API(access_token=api_key, environment=environment))  # survive transient connection resets on the live poll

    @classmethod
    def from_env(cls) -> OandaClient:
        return cls(os.environ["OANDA_API_KEY"], practice_environment())  # refuses OANDA_ENV != practice

    def candles(self, instrument: str, granularity: str, start: datetime, end: datetime, price: str = "M") -> pd.DataFrame:
        """Fetch complete candles in [start, end), paginating past the 5000-candle cap.

        `price="M"` returns mid OHLC only. `price="BA"` (or "MBA") requests mid+bid+ask in one call and
        returns FRAME_COLUMNS plus the BA_COLUMNS bid/ask OHLC used by the bid/ask fill model.
        """
        delta = _GRAN_DELTA[granularity]
        bidask = price in {"BA", "MBA"}
        req_price = "MBA" if bidask else price
        pk = None if bidask else _PRICE_KEY[price]
        columns = FRAME_COLUMNS + BA_COLUMNS if bidask else FRAME_COLUMNS
        rows: list[tuple] = []
        frm = start
        while frm < end:
            # `from`+`count` paging: OANDA rejects `count` alongside `to`, and a `from`+`to` range wider than
            # _MAX_COUNT errors ("Maximum value for 'count' exceeded"). So page by count and clip to `end` locally.
            params = {"granularity": granularity, "from": _iso_z(frm), "count": _MAX_COUNT, "price": req_price}
            batch = self._api.request(InstrumentsCandles(instrument=instrument, params=params)).get("candles", [])
            if not batch:
                break
            parsed = batch_to_ba_rows(batch, instrument, granularity, delta) if bidask else _batch_to_rows(batch, instrument, granularity, delta, pk)  # type: ignore[arg-type]
            rows.extend(r for r in parsed if r[2] < end)  # r[2] = open_time
            last_time = _parse_time(batch[-1]["time"])
            nxt = last_time + delta
            if nxt <= frm or last_time >= end:  # no forward progress, or covered the range
                break
            frm = nxt
        return normalize_frame(pd.DataFrame(rows, columns=columns))

    def recent_candles(self, instrument: str, granularity: str, count: int = 250, price: str = "M") -> pd.DataFrame:
        """Fetch the most recent `count` COMPLETE candles (count-based; used by the live poller)."""
        params = {"granularity": granularity, "count": count, "price": price}
        batch = self._api.request(InstrumentsCandles(instrument=instrument, params=params)).get("candles", [])
        rows = _batch_to_rows(batch, instrument, granularity, _GRAN_DELTA[granularity], _PRICE_KEY[price])
        return normalize_frame(pd.DataFrame(rows, columns=FRAME_COLUMNS))

    def candles_cached(
        self, instrument: str, granularity: str, start: datetime, end: datetime, cache_dir: str | Path | None = None, *, price: str = "M"
    ) -> pd.DataFrame:
        """Same as `candles`, but CSV-cache the result so backtests don't re-fetch (no parquet/pyarrow dep)."""
        if cache_dir is None:
            return self.candles(instrument, granularity, start, end, price=price)
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_path(cache_dir, instrument, granularity, start, end, price)
        if path.exists():
            return normalize_frame(pd.read_csv(path))
        df = self.candles(instrument, granularity, start, end, price=price)
        df.to_csv(path, index=False)
        return df
