"""The robust keep/revert gate must reject curve-fits and luck, and keep broad edges.

Pure/offline: hand-built metric dicts, no backtests or network. Exercises the NEW logic added
when the single-window OOS gate became multi-fold + bootstrap + plateau. deepagents is only
imported lazily inside research_loop._build_agent, so importing the gate functions is lean-safe."""
from __future__ import annotations

from trading_agent.analytics import bootstrap_expectancy
from trading_agent.research_loop import _pool_oos, plateau_ok, verdict


def _fold(expectancy: float, pnls: list[float]) -> dict:
    """A minimal per-fold metrics dict, shaped like analyze() output for the fields _pool_oos reads."""
    return {"expectancy": expectancy, "trades": len(pnls), "pnls": pnls, "avg_win_R": 0.0, "avg_loss_R": 0.0, "max_drawdown": 0.0}


def test_pool_oos_concatenates_and_counts_folds():
    pooled = _pool_oos([_fold(10.0, [10.0] * 12), _fold(-5.0, [-5.0] * 12), _fold(8.0, [8.0] * 12)])
    assert pooled["trades"] == 36
    assert pooled["folds_positive"] == 2  # folds 1 and 3
    assert pooled["fold_expectancy"] == [10.0, -5.0, 8.0]


def test_broad_edge_is_kept():
    """3/3 folds positive, plenty of trades, low-variance wins -> bootstrap p05 > 0 -> KEEP."""
    pnls = [40.0, 60.0, 45.0, 55.0, 50.0] * 8  # 40 trades, tight around +50
    oos = _pool_oos([_fold(50.0, pnls[:14]), _fold(50.0, pnls[14:27]), _fold(50.0, pnls[27:])])
    keep, reason = verdict({"expectancy": 60.0}, oos)
    assert keep, reason


def test_one_fold_only_is_rejected():
    """Only 1 of 3 folds positive (a single lucky window) -> fails the majority rule even if pooled > 0."""
    oos = _pool_oos([_fold(200.0, [200.0] * 20), _fold(-10.0, [-10.0] * 20), _fold(-10.0, [-10.0] * 20)])
    assert oos["expectancy"] > 0 and oos["folds_positive"] == 1  # pooled is positive, but concentrated in one fold
    keep, reason = verdict({"expectancy": 50.0}, oos)
    assert not keep, reason


def test_luck_is_rejected_by_bootstrap():
    """Majority of folds positive and pooled > 0, but high variance -> bootstrap 5th-pct dips <= 0 -> REJECT."""
    pnls = ([130.0] * 5 + [-100.0] * 5) * 3  # 30 trades, mean +15 but wide spread
    oos = _pool_oos([_fold(15.0, pnls[:10]), _fold(15.0, pnls[10:20]), _fold(15.0, pnls[20:])])
    assert oos["folds_positive"] == 3 and oos["trades"] == 30 and oos["expectancy"] > 0
    assert bootstrap_expectancy(oos["pnls"])["p05"] <= 0  # this is what should veto it
    keep, reason = verdict({"expectancy": 10.0}, oos)
    assert not keep, reason


def test_plateau_skips_when_no_numeric_lever():
    """A categorical-only proposal has no neighborhood to probe -> passes without any backtest."""
    ok, note = plateau_ok({"name": "ichimoku_confluence", "exit_on_kijun_cross": True})
    assert ok and "no numeric lever" in note
