"""store.py — the local SQLite index: write/query roundtrip, idempotent upserts, and reindex rebuild."""

from __future__ import annotations

import json

from trading_agent import store


def test_record_and_query_roundtrip(tmp_path) -> None:
    db = tmp_path / "t.db"
    store.record_run("research-1", kind="research", strategy_version="v2.0.0", mode="research", model="anthropic:claude-sonnet-5", started_at="2026-07-08T00:00:00Z", db=db)
    store.record_iteration("research-1", 1, status="kept", hypothesis="tighten stop", overrides={"stop_rule": "cloud"}, is_metrics={"expectancy": 50.0}, oos_metrics={"expectancy": 12.0, "trades": 40}, keep=True, improved=True, version="v2.0.0+r1", best_oos=12.0, db=db)
    store.record_strategy("v2.0.0+r1", name="ichimoku_cloud_breakout", config_yaml="name: ichimoku_cloud_breakout\n", oos_expectancy=12.0, source_run_id="research-1", db=db)
    store.finish_run("research-1", {"accepted": 1, "iterations": 1, "best_oos": 12.0}, finished_at="2026-07-08T00:05:00Z", db=db)

    runs = store.runs(db=db)
    assert len(runs) == 1 and runs[0]["run_id"] == "research-1"
    assert runs[0]["strategy_version"] == "v2.0.0" and runs[0]["finished_at"] == "2026-07-08T00:05:00Z"
    assert json.loads(runs[0]["summary_json"])["accepted"] == 1  # finish_run kept the descriptive fields too

    its = store.iterations("research-1", db=db)
    assert len(its) == 1 and its[0]["status"] == "kept" and its[0]["oos_expectancy"] == 12.0
    assert json.loads(its[0]["overrides_json"]) == {"stop_rule": "cloud"}

    strats = store.strategies(db=db)
    assert len(strats) == 1 and strats[0]["version"] == "v2.0.0+r1"
    assert store.best_strategies(db=db)[0]["oos_expectancy"] == 12.0


def test_iteration_upsert_replaces(tmp_path) -> None:
    db = tmp_path / "t.db"
    store.record_iteration("r", 1, status="running", db=db)
    store.record_iteration("r", 1, status="reverted", oos_metrics={"expectancy": -3.0, "trades": 55}, db=db)
    its = store.iterations("r", db=db)
    assert len(its) == 1 and its[0]["status"] == "reverted" and its[0]["oos_trades"] == 55  # one row, latest wins


def test_profit_factor_inf_is_nulled(tmp_path) -> None:
    db = tmp_path / "t.db"
    # oos expectancy inf must not blow up json/sqlite — _num maps it to NULL
    store.record_iteration("r", 1, status="kept", oos_metrics={"expectancy": float("inf"), "trades": 30}, best_oos=float("inf"), db=db)
    assert store.iterations("r", db=db)[0]["oos_expectancy"] is None


def test_reindex_idempotent(tmp_path) -> None:
    db = tmp_path / "t.db"
    runs_dir = tmp_path / "research_runs"
    run_dir = runs_dir / "research-x"
    (run_dir / "strategies").mkdir(parents=True)
    (run_dir / "meta.json").write_text(json.dumps({"strategy_version": "v2.0.0", "model": "m", "started_at": "2026-07-08T00:00:00Z", "summary": {"accepted": 1, "iterations": 1, "best_oos": 9.0}}))
    (run_dir / "iterations.jsonl").write_text(json.dumps({"iteration": 1, "status": "kept", "hypothesis": "h", "overrides": {"name": "ichimoku_cloud_breakout", "rr": 2.0}, "oos_metrics": {"expectancy": 9.0, "trades": 33}, "keep": True, "improved": True, "version": "v2.0.0+r1"}) + "\n")
    (run_dir / "strategies" / "v2.0.0+r1.yaml").write_text("name: ichimoku_cloud_breakout\nrr: 2.0\n")
    empty_logs = tmp_path / "logs"  # empty -> reindex never calls analytics.analyze (no pandas needed)
    empty_logs.mkdir()

    c1 = store.reindex(db=db, runs_dir=runs_dir, log_dir=empty_logs)
    n_runs, n_its, n_strats = len(store.runs(db=db)), len(store.iterations("research-x", db=db)), len(store.strategies(db=db))
    c2 = store.reindex(db=db, runs_dir=runs_dir, log_dir=empty_logs)  # second rebuild
    assert c1 == c2 == {"research_runs": 1, "backtest_runs": 0}
    assert (n_runs, n_its, n_strats) == (1, 1, 1)
    assert (len(store.runs(db=db)), len(store.iterations("research-x", db=db)), len(store.strategies(db=db))) == (1, 1, 1)  # no dupes
    assert store.strategies(db=db)[0]["config_yaml"].startswith("name: ichimoku_cloud_breakout")
