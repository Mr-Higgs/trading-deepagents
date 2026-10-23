"""Expand the training corpus: fetch + cache candles for many instruments (one CSV per tf per pair).

The trade loop stays single-instrument (config's `instrument`); this only widens the DATA a model
trains on. Single instrument over ~1.5y is too few samples for tree ensembles, so we pool the majors.
Reuses `OandaClient.candles_cached` (skips re-fetch when the CSV already exists) and the practice-only
tripwire in the client. Timeframes come from the active config so the corpus matches what the model
will actually see.

    uv run python -m trading_agent.ml.fetch_corpus --start 2022-01-01 --end 2026-07-01
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from trading_agent.config import load_config
from trading_agent.run import _load_env

# Majors + the current instrument. ponytail: a flat default list, override with --pairs; no per-pair
# config needed here because features are ATR-normalized (scale-free), so pooling pairs is valid.
DEFAULT_PAIRS = ("AUD_USD", "EUR_USD", "GBP_USD", "USD_JPY", "USD_CAD", "NZD_USD")


def _utc_day(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def main() -> None:
    _load_env()
    ap = argparse.ArgumentParser(prog="trading_agent.ml.fetch_corpus")
    ap.add_argument("--config", default="config")
    ap.add_argument("--start", default="2022-01-01", help="inclusive UTC date")
    ap.add_argument("--end", default="2026-07-01", help="exclusive UTC date")
    ap.add_argument("--cache", default="data/candles")
    ap.add_argument("--pairs", nargs="*", default=list(DEFAULT_PAIRS))
    args = ap.parse_args()

    from trading_agent.marketdata.oanda_client import OandaClient  # lazy: network stack

    cfg = load_config(args.config).strategy
    granularities = sorted({cfg.trend_tf, cfg.bias_tf, cfg.entry_tf})
    client = OandaClient.from_env()
    start, end = _utc_day(args.start), _utc_day(args.end)

    print(f"fetching {len(args.pairs)} pairs x {granularities} into {args.cache} [{args.start}..{args.end})")
    for inst in args.pairs:
        for gran in granularities:
            df = client.candles_cached(inst, gran, start, end, cache_dir=args.cache)
            print(f"  {inst} {gran}: {len(df)} candles")
    print("corpus ready. next: uv run python -m trading_agent.ml.train")


if __name__ == "__main__":
    main()
