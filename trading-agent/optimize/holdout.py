"""One-shot evaluation of a chosen config on the locked holdout (2026-07-01 -> latest). Every use is logged;
a config that was already evaluated is refused unless `--allow-reuse` is passed (and that is logged too).

    uv run --group optimize python -m optimize.holdout --report research/opt_AUD_USD_ichimoku_breakout_<ts>.json
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from optimize.walkforward import HOLDOUT_START

LOG = Path("research/holdout_log.jsonl")


class HoldoutAlreadyUsed(RuntimeError):
    """The same config was already scored on the holdout."""


def config_hash(pair: str, strategy: str, params: dict[str, Any]) -> str:
    blob = json.dumps({"pair": pair, "strategy": strategy, "params": params}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def previous_uses(log: Path, digest: str) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [r for r in (json.loads(line) for line in log.read_text().splitlines() if line) if r.get("config_hash") == digest]


def claim(log: Path, digest: str, entry: dict[str, Any], *, allow_reuse: bool = False) -> dict[str, Any]:
    """Record the holdout use BEFORE running, so a crash or Ctrl-C still counts as a use."""
    prior = previous_uses(log, digest)
    if prior and not allow_reuse:
        msg = f"holdout already used for config {digest} at {prior[0]['used_at']}; pass --allow-reuse to override (logged)"
        raise HoldoutAlreadyUsed(msg)
    rec = {"used_at": datetime.now(UTC).isoformat(), "config_hash": digest, "reuse": bool(prior), "user": getpass.getuser(), **entry}
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")
    return rec


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - needs OANDA data
    from optimize.data import load_frames
    from optimize.evaluate import Evaluator
    from optimize.run import base_config
    from optimize.space import apply_params
    from trading_agent.run import _load_env

    ap = argparse.ArgumentParser(prog="optimize.holdout")
    ap.add_argument("--report", required=True, help="optimizer JSON report; uses its recommended_params")
    ap.add_argument("--end", default=None, help="exclusive end (default: now)")
    ap.add_argument("--config", default="config")
    ap.add_argument("--cache", default="data/candles")
    ap.add_argument("--allow-reuse", action="store_true")
    args = ap.parse_args(argv)
    _load_env()
    report = json.loads(Path(args.report).read_text())
    pair, strategy, params = report["meta"]["pair"], report["meta"]["strategy"], report["recommended_params"]
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC) if args.end else datetime.now(UTC)
    digest = config_hash(pair, strategy, params)
    claim(LOG, digest, {"pair": pair, "strategy": strategy, "params": params, "report": args.report, "window": [HOLDOUT_START.isoformat(), end.isoformat()]}, allow_reuse=args.allow_reuse)
    print(f"HOLDOUT USED for config {digest} (logged to {LOG})")
    cfg = apply_params(base_config(args.config, pair, strategy), params)
    res = Evaluator(load_frames(cfg, HOLDOUT_START, end, cache_dir=args.cache), allow_holdout=True).run(cfg, HOLDOUT_START, end)
    out = {k: res.metrics.get(k) for k in ("trades", "expectancy", "profit_factor", "sharpe", "sortino", "max_drawdown_pct", "total_pnl")}
    with LOG.open("a") as fh:
        fh.write(json.dumps({"config_hash": digest, "result_at": datetime.now(UTC).isoformat(), "metrics": out}, default=str) + "\n")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
