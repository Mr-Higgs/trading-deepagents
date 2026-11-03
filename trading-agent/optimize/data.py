"""Load the multi-timeframe frames once (OANDA practice + cache), with warm-up before the first window."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from trading_agent.config import AppConfig
from trading_agent.marketdata.historical import HistoricalOandaSource

WARMUP = timedelta(days=120)  # Ichimoku senkou_b + displacement (78 bars) on the highest timeframe, daily included


def load_frames(cfg: AppConfig, start: datetime, end: datetime, *, cache_dir: str) -> dict[str, pd.DataFrame]:  # pragma: no cover - network
    from trading_agent.marketdata.oanda_client import OandaClient

    tfs = cfg.strategy.timeframes()
    price = "BA" if cfg.risk.execution.price_mode == "bidask" else "M"
    src = HistoricalOandaSource.from_oanda(OandaClient.from_env(), cfg.pair.symbol, tfs, start - WARMUP, end, cache_dir=cache_dir, price=price)
    return src._frames  # noqa: SLF001 - reuse the normalized frames across thousands of runs
