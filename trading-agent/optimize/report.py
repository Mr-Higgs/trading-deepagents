"""Markdown + JSON reports for a walk-forward run (written under `research/`)."""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from optimize.study import Settings, WalkForwardResult


def _fmt(v: Any) -> str:  # noqa: ANN401 - table cells
    if isinstance(v, float):
        return "n/a" if math.isnan(v) else f"{v:.3f}"
    if isinstance(v, tuple):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    return str(v)


def to_payload(result: WalkForwardResult, *, meta: dict[str, Any], settings: Settings) -> dict[str, Any]:
    folds = [
        {
            "train": [f.fold.train_start.isoformat(), f.fold.train_end.isoformat()],
            "test": [f.fold.test_start.isoformat(), f.fold.test_end.isoformat()],
            "params": f.params, "train_score": f.train_score, "plateau_score": f.plateau_score, "pbo": f.pbo,
            "oos": {k: f.oos.metrics.get(k) for k in ("trades", "expectancy", "profit_factor", "sharpe", "max_drawdown_pct", "total_pnl")},
        }
        for f in result.folds
    ]
    recommended = result.folds[-1].params if result.folds else {}
    return {"meta": meta, "settings": asdict(settings), "summary": result.summary(seed=settings.seed), "folds": folds, "recommended_params": recommended}


def to_markdown(payload: dict[str, Any]) -> str:
    meta, summ = payload["meta"], payload["summary"]
    lines = [
        f"# Walk-forward optimization: {meta['pair']} {meta['strategy']}",
        "",
        f"Range {meta['start']} .. {meta['end']} (holdout from {meta['holdout_start']} untouched), "
        f"{'anchored' if meta['anchored'] else 'rolling'} {meta['train_months']}m train / {meta['test_months']}m test, "
        f"objective `{payload['settings']['objective']}`, {summ['n_trials']} trials.",
        "",
        "## Concatenated out-of-sample",
        "",
        "| metric | value |",
        "|---|--:|",
        *[f"| {k} | {_fmt(v)} |" for k, v in summ.items()],
        "",
        "## Folds",
        "",
        "| test window | OOS trades | OOS expectancy | OOS Sharpe | train score | plateau score | PBO |",
        "|---|--:|--:|--:|--:|--:|--:|",
    ]
    for f in payload["folds"]:
        o = f["oos"]
        lines.append(f"| {f['test'][0][:10]}..{f['test'][1][:10]} | {o['trades']} | {_fmt(o['expectancy'])} | {_fmt(o['sharpe'])} | {_fmt(f['train_score'])} | {_fmt(f['plateau_score'])} | {_fmt(f['pbo'])} |")
    lines += ["", "## Recommended params (last fold's plateau pick)", "", "```json", json.dumps(payload["recommended_params"], indent=2), "```", ""]
    return "\n".join(lines)


def write_reports(payload: dict[str, Any], out_dir: str | Path, *, now: datetime) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"opt_{payload['meta']['pair']}_{payload['meta']['strategy']}_{now:%Y%m%dT%H%M%SZ}"
    js, md = out / f"{stem}.json", out / f"{stem}.md"
    js.write_text(json.dumps(payload, indent=2, default=str))
    md.write_text(to_markdown(payload))
    return md, js
