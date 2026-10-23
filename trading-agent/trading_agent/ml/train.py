"""Train the ml_signal model: pool the cached corpus, walk-forward split, fit, report, save.

Pipeline: read the cached CSV corpus for every pair -> build_dataset (features + triple-barrier labels)
-> pool into one frame -> TEMPORAL split (train on bars before `--oos-start`, test on/after; never a
random split, which would leak future bars into training) -> fit a RandomForest/XGBoost classifier ->
report per-class precision/recall on the untouched OOS slice -> save a joblib artifact carrying the
model, its exact feature-name order and class order (so live inference enforces train/serve parity).

The honest edge test (does ml_signal beat the deterministic baseline on money?) is deliberately NOT
here - it is the same `run backtest` + `analytics` path every strategy uses; this script prints that
command at the end. sklearn / xgboost / joblib are imported lazily (uv sync --group ml).

    uv run python -m trading_agent.ml.train --model random_forest --oos-start 2026-01-01
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from trading_agent.config import load_config
from trading_agent.marketdata.base import normalize_frame
from trading_agent.marketdata.historical import HistoricalOandaSource
from trading_agent.ml.features import feature_names
from trading_agent.ml.labeling import build_dataset
from trading_agent.run import _load_env

CLASSES = ("FLAT", "LONG", "SHORT")  # fixed order; the model's classes_ indexes into this
DEFAULT_PAIRS = ("AUD_USD", "EUR_USD", "GBP_USD", "USD_JPY", "USD_CAD", "NZD_USD")


def _utc_day(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _source_from_cache(cache_dir: Path, instrument: str, tfs: dict[str, str], start: datetime, end: datetime) -> HistoricalOandaSource | None:
    """Rebuild a HistoricalOandaSource by reading the CSVs fetch_corpus wrote (no network)."""
    frames = {}
    for role, gran in tfs.items():
        path = cache_dir / f"{instrument}_{gran}_{start:%Y%m%d}_{end:%Y%m%d}.csv"
        if not path.exists():
            print(f"  ! missing {path.name} (run fetch_corpus first) - skipping {instrument}")
            return None
        frames[gran] = normalize_frame(pd.read_csv(path))
    return HistoricalOandaSource(frames, entry_tf=tfs["entry"])


def _build_model(name: str):
    """RF/XGB registry. ponytail: fixed sensible regularization; add a search only if this underperforms."""
    if name == "random_forest":
        from sklearn.ensemble import RandomForestClassifier

        return RandomForestClassifier(n_estimators=300, max_depth=6, min_samples_leaf=50, random_state=1337, n_jobs=-1)
    if name == "xgboost":
        from xgboost import XGBClassifier

        return XGBClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, random_state=1337, n_jobs=-1, tree_method="hist")
    msg = f"unknown model {name!r} (expected 'random_forest' or 'xgboost')"
    raise ValueError(msg)


def main() -> None:
    _load_env()
    ap = argparse.ArgumentParser(prog="trading_agent.ml.train")
    ap.add_argument("--config", default="config")
    ap.add_argument("--mode", choices=["generator", "filter"], default="generator", help="generator: predict LONG/SHORT/FLAT per bar. filter: meta-label an inner strategy's signals as win/loss.")
    ap.add_argument("--model", choices=["random_forest", "xgboost"], default="random_forest")
    ap.add_argument("--start", default="2022-01-01", help="corpus start (matches fetch_corpus)")
    ap.add_argument("--end", default="2026-07-01", help="corpus end (matches fetch_corpus)")
    ap.add_argument("--oos-start", default="2026-01-01", help="temporal split: bars on/after this date are the OOS test set")
    ap.add_argument("--cache", default="data/candles")
    ap.add_argument("--pairs", nargs="*", default=list(DEFAULT_PAIRS))
    ap.add_argument("--stride", type=int, default=1, help="sample every Nth entry bar (raise to build a smaller set faster)")
    ap.add_argument("--out", default="data/models")
    args = ap.parse_args()

    from sklearn.metrics import classification_report
    from sklearn.utils.class_weight import compute_sample_weight

    app = load_config(args.config)
    cfg = app.strategy
    tfs = {"trend": cfg.trend_tf, "bias": cfg.bias_tf, "entry": cfg.entry_tf}
    cache_dir, start, end = Path(args.cache), _utc_day(args.start), _utc_day(args.end)

    # filter mode meta-labels an inner strategy's signals (win/loss); generator mode labels every bar (LONG/SHORT/FLAT)
    inner = None
    if args.mode == "filter":
        from dataclasses import replace

        from trading_agent.ml.labeling import build_meta_dataset
        from trading_agent.strategy import build_strategy

        inner = build_strategy(replace(cfg, name=cfg.filter_inner), app.pair)
        target_names, all_labels = ("loss", "win"), [0, 1]

    print(f"building dataset ({args.mode}): pairs={args.pairs} tfs={tfs} stride={args.stride}" + (f" inner={cfg.filter_inner}" if inner else ""))
    records: list[dict] = []
    for inst in args.pairs:
        source = _source_from_cache(cache_dir, inst, tfs, start, end)
        if source is None:
            continue
        n0 = len(records)
        rows = build_meta_dataset(source, cfg, inner, stride=args.stride) if inner else build_dataset(source, cfg, stride=args.stride)
        records.extend(rows)
        print(f"  {inst}: {len(records) - n0} rows")
    if not records:
        raise SystemExit("no data - run `python -m trading_agent.ml.fetch_corpus` first")

    df = pd.DataFrame.from_records(records).sort_values("ts").reset_index(drop=True)
    names = feature_names(cfg)
    x = df[names]
    if args.mode == "filter":
        y = df["label"].astype(int)  # already 0=loss / 1=win
    else:
        y = df["label"].map(CLASSES.index)  # -> 0/1/2 in CLASSES order
        target_names, all_labels = CLASSES, list(range(len(CLASSES)))
    oos_start = _utc_day(args.oos_start)
    is_mask = df["ts"] < oos_start
    x_tr, y_tr = x[is_mask], y[is_mask]
    x_te, y_te = x[~is_mask], y[~is_mask]
    print(f"\ndataset: {len(df)} rows | train(<{args.oos_start})={len(x_tr)} oos={len(x_te)}")
    print("label distribution:", df["label"].value_counts().to_dict())
    if x_tr.empty or x_te.empty:
        raise SystemExit(f"empty train or OOS split at --oos-start {args.oos_start}; widen the corpus or move the split")

    model = _build_model(args.model)
    model.fit(x_tr, y_tr, sample_weight=compute_sample_weight("balanced", y_tr))
    classes = [CLASSES[i] for i in model.classes_] if args.mode == "generator" else [int(c) for c in model.classes_]

    pred = model.predict(x_te)
    print(f"\nOOS classification report ({args.mode}, {args.model}):")
    print(classification_report(y_te, pred, labels=all_labels, target_names=list(target_names), zero_division=0))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / f"{args.model}.joblib"
    import joblib

    joblib.dump(
        {
            "model": model,
            "features": names,
            "classes": classes,
            "meta": {"mode": args.mode, "model": args.model, "inner": cfg.filter_inner if inner else None, "pairs": args.pairs, "oos_start": args.oos_start, "max_hold_bars": cfg.max_hold_bars, "atr_mult": cfg.atr_mult, "rr": cfg.rr, "stride": args.stride},
        },
        model_path,
    )
    strat_name = "ml_filter" if args.mode == "filter" else "ml_signal"
    print(f"\nsaved {model_path}")
    print(f"next: set strategy_v1.yaml -> name: {strat_name} and setup.model_path: {model_path}")
    print(f"      then honest edge test: uv run python -m trading_agent.run backtest --start {args.oos_start} --end {args.end}")


if __name__ == "__main__":
    main()
