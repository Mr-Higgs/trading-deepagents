"""Local SQLite index of runs / loops / iterations / strategies — the queryable history.

A DERIVED read-model, not a source of truth: the JSONL event logs (`data/logs/events-*.jsonl`) and the
per-run research artifacts (`data/research_runs/<run_id>/`) are authoritative; `reindex` rebuilds this DB
from them. stdlib `sqlite3` only — no server, no ORM. The DB lives under gitignored `data/`, so it's local
and disposable (delete it and `reindex` to rebuild).

    python -m trading_agent.store runs                 # list runs (newest first)
    python -m trading_agent.store iterations <run_id>  # a research run's propose->gate rounds
    python -m trading_agent.store strategies           # the versioned strategy library
    python -m trading_agent.store reindex              # rebuild the DB from data/ artifacts
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_DB = _ROOT / "data" / "trading.db"
_RUNS_DIR = _ROOT / "data" / "research_runs"
_LOG_DIR = _ROOT / "data" / "logs"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, kind TEXT, mode TEXT, strategy_version TEXT, model TEXT,
  started_at TEXT, finished_at TEXT, summary_json TEXT
);
CREATE TABLE IF NOT EXISTS iterations (
  run_id TEXT, iteration INTEGER, status TEXT, hypothesis TEXT, overrides_json TEXT,
  is_expectancy REAL, oos_expectancy REAL, oos_trades INTEGER, keep INTEGER, improved INTEGER,
  version TEXT, gate TEXT, best_oos REAL,
  PRIMARY KEY (run_id, iteration)
);
CREATE TABLE IF NOT EXISTS strategies (
  version TEXT PRIMARY KEY, name TEXT, config_yaml TEXT, oos_expectancy REAL,
  source_run_id TEXT, created_at TEXT
);
"""


def connect(db: str | Path = _DB) -> sqlite3.Connection:
    """Open (creating parent dir + schema) a WAL-mode connection with Row access."""
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    return con


def _num(x: Any) -> float | None:
    """Coerce to float, mapping inf/None to a DB-friendly value (None) — profit_factor can be inf."""
    if x is None or x == float("inf") or x == float("-inf"):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---- writes (all idempotent: safe to call again / during reindex) --------------------------------

def record_run(run_id: str, kind: str, strategy_version: str, *, mode: str | None = None, model: str | None = None, started_at: str | None = None, db: str | Path = _DB) -> None:
    """Upsert a run's descriptive fields; preserves finished_at/summary set later by finish_run."""
    with connect(db) as con:
        con.execute(
            "INSERT INTO runs (run_id, kind, mode, strategy_version, model, started_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(run_id) DO UPDATE SET kind=excluded.kind, mode=excluded.mode, "
            "strategy_version=excluded.strategy_version, model=excluded.model, started_at=excluded.started_at",
            (run_id, kind, mode, strategy_version, model, started_at),
        )


def finish_run(run_id: str, summary: dict[str, Any] | None = None, *, finished_at: str | None = None, db: str | Path = _DB) -> None:
    """Mark a run done with its summary. Upsert-safe so it works even if record_run wasn't called."""
    with connect(db) as con:
        con.execute(
            "INSERT INTO runs (run_id, finished_at, summary_json) VALUES (?,?,?) "
            "ON CONFLICT(run_id) DO UPDATE SET finished_at=excluded.finished_at, summary_json=excluded.summary_json",
            (run_id, finished_at, json.dumps(summary) if summary is not None else None),
        )


def record_iteration(run_id: str, iteration: int, *, status: str, hypothesis: str | None = None, overrides: dict | None = None, is_metrics: dict | None = None, oos_metrics: dict | None = None, keep: bool | None = None, improved: bool | None = None, version: str | None = None, gate: str | None = None, best_oos: float | None = None, db: str | Path = _DB) -> None:
    with connect(db) as con:
        con.execute(
            "INSERT OR REPLACE INTO iterations (run_id, iteration, status, hypothesis, overrides_json, "
            "is_expectancy, oos_expectancy, oos_trades, keep, improved, version, gate, best_oos) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id, iteration, status, hypothesis, json.dumps(overrides) if overrides is not None else None,
                _num((is_metrics or {}).get("expectancy")), _num((oos_metrics or {}).get("expectancy")),
                (oos_metrics or {}).get("trades"), None if keep is None else int(keep), None if improved is None else int(improved),
                version, gate, _num(best_oos),
            ),
        )


def record_strategy(version: str, name: str, config_yaml: str, *, oos_expectancy: float | None = None, source_run_id: str | None = None, created_at: str | None = None, db: str | Path = _DB) -> None:
    with connect(db) as con:
        con.execute(
            "INSERT OR REPLACE INTO strategies (version, name, config_yaml, oos_expectancy, source_run_id, created_at) VALUES (?,?,?,?,?,?)",
            (version, name, config_yaml, _num(oos_expectancy), source_run_id, created_at),
        )


# ---- reads (thin SELECTs for the CLI + a future read graph) --------------------------------------

def runs(limit: int = 50, db: str | Path = _DB) -> list[dict[str, Any]]:
    with connect(db) as con:
        return [dict(r) for r in con.execute("SELECT * FROM runs ORDER BY started_at DESC, run_id DESC LIMIT ?", (limit,))]


def iterations(run_id: str, db: str | Path = _DB) -> list[dict[str, Any]]:
    with connect(db) as con:
        return [dict(r) for r in con.execute("SELECT * FROM iterations WHERE run_id=? ORDER BY iteration", (run_id,))]


def strategies(db: str | Path = _DB) -> list[dict[str, Any]]:
    with connect(db) as con:
        return [dict(r) for r in con.execute("SELECT * FROM strategies ORDER BY created_at DESC, version DESC")]


def best_strategies(limit: int = 10, db: str | Path = _DB) -> list[dict[str, Any]]:
    with connect(db) as con:
        return [dict(r) for r in con.execute("SELECT * FROM strategies WHERE oos_expectancy IS NOT NULL ORDER BY oos_expectancy DESC LIMIT ?", (limit,))]


# ---- reindex: rebuild the whole DB from the durable files (idempotent) ---------------------------

def summarize_run(m: dict[str, Any]) -> dict[str, Any]:
    """Slim, JSON-safe run summary from analytics.analyze() output (profit_factor inf -> 'inf')."""
    pf = m.get("profit_factor")
    return {
        "trades": m.get("trades"), "expectancy": round(m.get("expectancy", 0.0), 2),
        "win_rate": round(m.get("win_rate", 0.0), 3), "total_pnl": round(m.get("total_pnl", 0.0), 2),
        "max_drawdown": round(m.get("max_drawdown", 0.0), 2), "profit_factor": ("inf" if pf == float("inf") else round(pf, 2) if pf is not None else None),
    }


def reindex(db: str | Path = _DB, runs_dir: Path = _RUNS_DIR, log_dir: Path = _LOG_DIR) -> dict[str, int]:
    """Wipe and rebuild runs/iterations/strategies from data/research_runs + data/logs. Idempotent."""
    from trading_agent.analytics import analyze  # lazy: pulls pandas/journal; only reindex needs it

    with connect(db) as con:
        con.executescript("DELETE FROM runs; DELETE FROM iterations; DELETE FROM strategies;")
    counts = {"research_runs": 0, "backtest_runs": 0}

    # Research runs: meta.json (run header) + iterations.jsonl + strategies/*.yaml snapshots.
    for run_dir in sorted(p for p in runs_dir.glob("*") if p.is_dir()) if runs_dir.exists() else []:
        counts["research_runs"] += 1
        run_id = run_dir.name
        meta = _read_json(run_dir / "meta.json")
        record_run(run_id, kind="research", mode="research", strategy_version=meta.get("strategy_version", "-"), model=meta.get("model"), started_at=meta.get("started_at"), db=db)
        summary = meta.get("summary")
        if summary is not None:
            finish_run(run_id, summary, finished_at=meta.get("finished_at"), db=db)
        for it in _read_jsonl(run_dir / "iterations.jsonl"):
            record_iteration(
                run_id, int(it["iteration"]), status=it.get("status", "?"), hypothesis=it.get("hypothesis"),
                overrides=it.get("overrides"), is_metrics=it.get("is_metrics"), oos_metrics=it.get("oos_metrics"),
                keep=it.get("keep"), improved=it.get("improved"), version=it.get("version"), gate=it.get("gate"),
                best_oos=it.get("best_oos"), db=db,
            )
            snap = run_dir / "strategies" / f"{it.get('version')}.yaml"
            if it.get("version") and snap.exists():
                record_strategy(it["version"], name=(it.get("overrides") or {}).get("name", meta.get("name", "?")), config_yaml=snap.read_text(), oos_expectancy=(it.get("oos_metrics") or {}).get("expectancy"), source_run_id=run_id, created_at=meta.get("started_at"), db=db)

    # Backtest/live runs: replay each event log into a slim summary.
    for log in sorted(log_dir.glob("events-*.jsonl")) if log_dir.exists() else []:
        try:
            m = analyze(log)
        except Exception:  # noqa: BLE001 - a malformed/partial log shouldn't abort the whole reindex
            continue
        run_id = m.get("run_id", log.stem)
        kind = "live" if run_id.startswith("live-") else "backtest"
        record_run(run_id, kind=kind, mode=kind, strategy_version=m.get("strategy_version", "-"), started_at=_first_ts(log), db=db)
        finish_run(run_id, summarize_run(m), db=db)
        counts["backtest_runs"] += 1
    return counts


def _first_ts(log: Path) -> str | None:
    """The ts of a log's first event — a start time for reindexed historical runs (ordering + display)."""
    try:
        with log.open() as fh:
            for line in fh:
                if line.strip():
                    return json.loads(line).get("ts")
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _read_json(p: Path) -> dict[str, Any]:
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _read_jsonl(p: Path) -> list[dict[str, Any]]:
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(prog="trading_agent.store")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("runs")
    it = sub.add_parser("iterations")
    it.add_argument("run_id")
    sub.add_parser("strategies")
    sub.add_parser("reindex")
    args = ap.parse_args()

    if args.cmd == "runs":
        for r in runs():
            s = json.loads(r["summary_json"]) if r["summary_json"] else {}
            extra = f"exp {s['expectancy']} pnl {s['total_pnl']}" if "expectancy" in s else (f"accepted {s.get('accepted')}/{s.get('iterations')} best_oos {s.get('best_oos')}" if "best_oos" in s else "")
            print(f"{r['started_at'] or '?':<20} {r['kind']:<9} {r['run_id']:<34} {r['strategy_version'] or '-':<12} {extra}")
    elif args.cmd == "iterations":
        for r in iterations(args.run_id):
            print(f"  #{r['iteration']:<3} {r['status']:<9} IS {r['is_expectancy']} OOS {r['oos_expectancy']} keep={r['keep']} {r['version'] or ''}  {r['hypothesis'] or ''}")
    elif args.cmd == "strategies":
        for r in strategies():
            print(f"{r['version']:<16} {r['name']:<22} OOS {r['oos_expectancy']}  <- {r['source_run_id']}")
    elif args.cmd == "reindex":
        print("reindexed:", reindex())


if __name__ == "__main__":
    main()
