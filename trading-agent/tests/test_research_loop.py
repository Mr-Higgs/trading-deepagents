"""research_loop deterministic backbone: the OOS keep/revert gate + comment-preserving YAML writes.

The LLM proposer is not exercised here (that needs the network); these are the pure pieces that own
the verdict and persist accepted changes."""

from __future__ import annotations

import json
import shutil
import types

import trading_agent.research_loop as R
from trading_agent.config import load_config
from trading_agent.research_loop import apply_to_yaml

# The keep/revert gate is now multi-fold + bootstrap + plateau; its unit tests live in
# test_research_gate.py. This file keeps the YAML-persistence and end-to-end loop-streaming tests.


def test_apply_to_yaml_preserves_comments(tmp_path) -> None:
    cfg_dir = tmp_path / "config"
    shutil.copytree("config", cfg_dir)
    apply_to_yaml(str(cfg_dir), {"rr": 2.0, "stop_rule": "cloud", "name": "ichimoku_cloud_breakout", "m15_trigger": ["hammer", "engulfing"]}, "v9.9.9+r1")
    c = load_config(str(cfg_dir))
    assert c.strategy.rr == 2.0
    assert c.strategy.stop_rule == "cloud"
    assert c.strategy.name == "ichimoku_cloud_breakout"
    assert c.strategy.m15_trigger == ("hammer", "engulfing")
    assert c.strategy.version == "v9.9.9+r1"
    # a value edit must not strip the human comments/structure
    assert "WFX-standard periods" in (cfg_dir / "strategy_v1.yaml").read_text()


def test_apply_to_yaml_ignores_disallowed_keys(tmp_path) -> None:
    cfg_dir = tmp_path / "config"
    shutil.copytree("config", cfg_dir)
    before = (cfg_dir / "strategy_v1.yaml").read_text()
    apply_to_yaml(str(cfg_dir), {"risk_pct": 0.5, "not_a_field": 1}, "v9.9.9+r2")  # neither is a StrategyConfig field
    c = load_config(str(cfg_dir))
    assert c.risk.risk_pct != 0.5  # untouched — risk lives in a different file and is out of scope
    assert "not_a_field" not in (cfg_dir / "strategy_v1.yaml").read_text()
    assert before.split("setup:")[0].count("\n") == (cfg_dir / "strategy_v1.yaml").read_text().split("setup:")[0].count("\n")


def test_loop_streams_events_and_compounds(monkeypatch, tmp_path) -> None:
    """The loop emits RESEARCH_META -> ITER_START/ITER_RESULT -> RESEARCH_END and keeps a profitable OOS change.

    Stubs the LLM proposer, the backtests, and the store/DB so only the streaming + verdict + compounding logic runs."""
    emitted: list[dict] = []
    monkeypatch.setattr(R, "_emit", lambda payload: emitted.append(payload))
    monkeypatch.setattr(R, "_RUNS_DIR", tmp_path / "runs")  # per-run artifacts land in tmp, not real data/
    monkeypatch.setattr(R, "_seed_files", lambda: None)
    monkeypatch.setattr(R, "_write_report", lambda cfg: None)
    monkeypatch.setattr(R, "_journal", lambda entry: None)
    monkeypatch.setattr(R, "apply_to_yaml", lambda cfg, ov, ver: None)
    monkeypatch.setattr(R, "_load_env", lambda: None)
    for fn in ("record_run", "record_iteration", "record_strategy", "finish_run"):  # don't touch the real trading.db
        monkeypatch.setattr(R.store, fn, lambda *a, **k: None)
    monkeypatch.setattr(R, "load_config", lambda cfg: types.SimpleNamespace(strategy=types.SimpleNamespace(version="v1", name="ichimoku_pullback")))
    src_cfg = tmp_path / "config"  # real dir so the commit=False sandbox copytree + strategy snapshot read work
    src_cfg.mkdir()
    (src_cfg / "strategy_v1.yaml").write_text("name: ichimoku_pullback\nversion: v1\n")

    def fake_run_backtest(ov, start, end, **kw):
        # analyze() now returns a per-window `pnls` list; the pooled gate derives expectancy/trades from it,
        # so the stub must carry one too. 3 identical OOS folds -> pooled 90 trades. stop_rule=cloud is the
        # "good" proposal: every fold profitable. (stop_rule is categorical -> plateau check skips, no extra probes.)
        base = {"trades": 30, "expectancy": -20.0, "win_rate": 0.5, "profit_factor": 0.3, "avg_win_R": 0.4, "avg_loss_R": -1.1, "total_pnl": -600.0, "max_drawdown": 600.0, "pnls": [-20.0] * 30}
        if ov.get("stop_rule") == "cloud":  # the "good" proposal: profitable OOS in every fold
            base = {**base, "expectancy": 12.0, "profit_factor": 1.4, "total_pnl": 360.0, "pnls": [12.0] * 30}
        return base

    monkeypatch.setattr(R, "run_backtest", fake_run_backtest)

    proposal = types.SimpleNamespace(hypothesis="tighten stop to the cloud", overrides_json='{"stop_rule": "cloud"}', rationale="losers run to -1.1R")
    fake_agent = types.SimpleNamespace(invoke=lambda _messages: {"structured_response": proposal})
    monkeypatch.setattr(R, "_build_agent", lambda model: (fake_agent, lambda: None))  # (agent, reset_probes)

    R.loop(iterations=1, model="stub", config_dir=str(src_cfg), commit=False)

    types_seen = [e["type"] for e in emitted]
    # keep-warm PROGRESS/BACKTEST heartbeats interleave; the core research sequence must still be in order.
    core = [t for t in types_seen if t in {"RESEARCH_META", "ITER_START", "ITER_RESULT", "RESEARCH_END"}]
    assert core == ["RESEARCH_META", "ITER_START", "ITER_RESULT", "RESEARCH_END"], types_seen
    assert "PROGRESS" in types_seen  # baseline + per-iteration heartbeats keep the custom stream warm
    result = next(e for e in emitted if e["type"] == "ITER_RESULT")
    assert result["status"] == "kept", result  # -20 baseline OOS -> +12 passes the gate and beats best
    assert result["overrides"] == {"stop_rule": "cloud"}
    assert emitted[-1]["type"] == "RESEARCH_END" and emitted[-1]["best_oos"] == 12.0  # last event; compounded to the new best
    assert all("run_id" in e for e in emitted)  # every custom event carries run_id (UI guard requires it)

    # persistence: the run left durable artifacts under its per-run dir (no more discard).
    run_dirs = list((tmp_path / "runs").glob("research-*"))
    assert len(run_dirs) == 1, run_dirs
    rd = run_dirs[0]
    assert json.loads((rd / "meta.json").read_text())["summary"] == {"accepted": 1, "iterations": 1, "best_oos": 12.0}
    logged = [json.loads(x) for x in (rd / "iterations.jsonl").read_text().splitlines() if x.strip()]
    assert len(logged) == 1 and logged[0]["status"] == "kept"
    assert (rd / "strategies" / "v1+r1.yaml").exists()  # accepted variant snapshot (base v1 + round 1)
