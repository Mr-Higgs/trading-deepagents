"""research_loop deterministic backbone: search-space proposals for optimize/ + comment-preserving YAML writes.

The LLM proposer is stubbed (no network). The loop must never select on an out-of-sample window."""

from __future__ import annotations

import json
import shutil
import types

import pytest

import trading_agent.research_loop as R
from trading_agent.config import load_config
from trading_agent.research_loop import apply_to_yaml, save_proposal


def test_save_proposal_validates_and_persists(tmp_path) -> None:
    space = '[{"name": "rr", "kind": "float", "low": 1.5, "high": 3.0, "step": 0.25}, {"name": "trail_mode", "kind": "cat", "choices": ["off", "kijun"]}]'
    path = save_proposal(tmp_path, 2, strategy="ichimoku_breakout", hypothesis="h", space_json=space, rationale="r")
    from optimize.space import load_space

    assert path.name == "iter-002.json" and [p.name for p in load_space(path)] == ["rr", "trail_mode"]


def test_save_proposal_rejects_risk_params(tmp_path) -> None:
    with pytest.raises(ValueError, match="not tunable"):
        save_proposal(tmp_path, 1, strategy="s", hypothesis="h", space_json='[{"name": "risk_pct", "kind": "float", "low": 0.5, "high": 2}]', rationale="r")


def test_no_out_of_sample_selection_surface() -> None:
    import inspect

    src = inspect.getsource(R.loop)
    assert "verdict(" not in src and "evaluate(" not in src and "OOS_RANGE" not in src  # legacy shims unused


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


def test_loop_streams_events_and_saves_search_spaces(monkeypatch, tmp_path) -> None:
    """RESEARCH_META -> ITER_START/ITER_RESULT -> RESEARCH_END; a proposal becomes a saved search space and
    only in-sample backtests run (stubbed proposer/backtests/store)."""
    emitted: list[dict] = []
    ranges: list[tuple[str, str]] = []
    monkeypatch.setattr(R, "_emit", lambda payload: emitted.append(payload))
    monkeypatch.setattr(R, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(R, "_seed_files", lambda: None)
    monkeypatch.setattr(R, "_write_report", lambda cfg: None)
    monkeypatch.setattr(R, "_journal", lambda entry: None)
    monkeypatch.setattr(R, "_load_env", lambda: None)
    for fn in ("record_run", "record_iteration", "record_strategy", "finish_run"):
        monkeypatch.setattr(R.store, fn, lambda *a, **k: None)
    monkeypatch.setattr(R, "load_config", lambda cfg: types.SimpleNamespace(strategy=types.SimpleNamespace(version="v1", name="ichimoku_pullback")))
    src_cfg = tmp_path / "config"
    src_cfg.mkdir()
    (src_cfg / "strategy_v1.yaml").write_text("name: ichimoku_pullback\nversion: v1\n")

    def fake_run_backtest(ov, start, end, **kw):
        ranges.append((start, end))
        return {"trades": 90, "expectancy": -20.0, "win_rate": 0.5, "profit_factor": 0.3, "avg_win_R": 0.4, "avg_loss_R": -1.1, "total_pnl": -1800.0, "max_drawdown": 1800.0}

    monkeypatch.setattr(R, "run_backtest", fake_run_backtest)
    space = '[{"name": "stop_rule", "kind": "cat", "choices": ["cloud", "kijun"]}, {"name": "rr", "kind": "float", "low": 1.5, "high": 3.0}]'
    proposal = types.SimpleNamespace(hypothesis="cloud stops cut -1.1R losers", space_json=space, rationale="losers run to -1.1R")
    fake_agent = types.SimpleNamespace(invoke=lambda _messages: {"structured_response": proposal})
    monkeypatch.setattr(R, "_build_agent", lambda model: (fake_agent, lambda: None))

    R.loop(iterations=1, model="stub", config_dir=str(src_cfg), commit=False)

    core = [e["type"] for e in emitted if e["type"] in {"RESEARCH_META", "ITER_START", "ITER_RESULT", "RESEARCH_END"}]
    assert core == ["RESEARCH_META", "ITER_START", "ITER_RESULT", "RESEARCH_END"]
    result = next(e for e in emitted if e["type"] == "ITER_RESULT")
    assert result["status"] == "proposed"
    assert set(ranges) == {R.IS_RANGE}  # never touched any other window
    assert all("run_id" in e for e in emitted)
    rd = next((tmp_path / "runs").glob("research-*"))
    assert json.loads((rd / "meta.json").read_text())["summary"] == {"proposed": 1, "iterations": 1}
    assert json.loads((rd / "search_spaces" / "iter-001.json").read_text())["hypothesis"] == proposal.hypothesis


def test_legacy_verdict_still_importable_but_deprecated():
    import pytest

    with pytest.warns(DeprecationWarning):
        keep, _ = R.verdict({"expectancy": 10.0}, {"expectancy": 8.0, "trades": 40})
    assert keep


def test_candles_path_follows_entry_timeframe(tmp_path) -> None:
    (tmp_path / "AUD_USD_M5_20250101_20250201.csv").write_text("")
    assert R._candles_path("2025-01-01", "2025-02-01", str(tmp_path), "AUD_USD", "M5").endswith("AUD_USD_M5_20250101_20250201.csv")
    assert R._candles_path("2025-01-01", "2025-02-01", str(tmp_path), "AUD_USD", "M15") is None
