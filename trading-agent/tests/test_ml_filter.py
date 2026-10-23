"""MLFilter (meta-labeling): registry wiring, keep/veto/fail-closed, and build_meta_dataset.

The inner primary + the model are stubbed so these tests exercise the FILTER plumbing, not sklearn: a
kept signal must be the inner's signal untouched (plus ml_pwin), a veto must carry the candidate, and any
model problem must veto (never raise).
"""

from __future__ import annotations

from datetime import datetime, timezone

from conftest import make_frame, make_pair, make_signal, make_strategy

from trading_agent.ml.features import feature_names
from trading_agent.ml.labeling import build_meta_dataset
from trading_agent.strategy import build_strategy
from trading_agent.strategy.base import StrategyResult
from trading_agent.strategy.ml_filter import MLFilter

UTC = timezone.utc
NOW = datetime(2025, 1, 5, 8, tzinfo=UTC)


class StubModel:
    def __init__(self, probs=(0.1, 0.9)):  # (P_loss, P_win)
        self.probs = list(probs)

    def predict_proba(self, _row):
        return [self.probs]


class StubInner:
    """Inner primary stub: fires a LONG at the latest close (or a skip when signal=None)."""

    version = "stub-v1"

    def __init__(self, fires=True):
        self.fires = fires

    def evaluate(self, snapshot, now):
        if not self.fires:
            return StrategyResult(None, "stub_skip")
        c = float(snapshot["M15"]["c"].iloc[-1])
        sig = make_signal("LONG", entry=c, stop=c - 0.001, tp=c + 0.001)
        return StrategyResult(sig, "stub_fire")


def _snapshot():
    frame = make_frame(140)
    return {"H4": frame, "H1": frame, "M15": frame}


def _artifact(cfg, probs=(0.1, 0.9)):
    return {"model": StubModel(probs), "features": feature_names(cfg), "classes": [0, 1], "meta": {"mode": "filter"}}


def _filter(cfg, inner, artifact=None):
    f = MLFilter(cfg, make_pair())
    f.inner = inner  # swap the real inner for a stub to isolate the filter logic
    if artifact is not None:
        f._loaded, f._artifact = True, artifact  # noqa: SLF001
    return f


def test_registry_builds_ml_filter():
    strat = build_strategy(make_strategy(name="ml_filter", filter_inner="ichimoku_breakout"), make_pair())
    assert isinstance(strat, MLFilter) and strat.inner is not None


def test_passthrough_when_inner_skips():
    cfg = make_strategy(name="ml_filter")
    res = _filter(cfg, StubInner(fires=False), _artifact(cfg)).evaluate(_snapshot(), NOW)
    assert res.signal is None and res.reason == "stub_skip"  # no candidate -> nothing to filter


def test_keep_high_pwin():
    cfg = make_strategy(name="ml_filter", prob_threshold=0.5)
    res = _filter(cfg, StubInner(), _artifact(cfg, probs=(0.1, 0.9))).evaluate(_snapshot(), NOW)
    assert res.signal is not None and res.signal.direction == "LONG"
    assert res.signal.features["ml_pwin"] == 0.9 and "+ml0.90" in res.reason


def test_veto_low_pwin_carries_candidate():
    cfg = make_strategy(name="ml_filter", prob_threshold=0.6)
    res = _filter(cfg, StubInner(), _artifact(cfg, probs=(0.7, 0.3))).evaluate(_snapshot(), NOW)
    assert res.signal is None and res.reason == "ml_filter_veto:0.30"
    assert res.candidate is not None and res.candidate.direction == "LONG"  # geometry preserved for logging


def test_fails_closed_without_model():
    cfg = make_strategy(name="ml_filter", model_path="")
    res = _filter(cfg, StubInner()).evaluate(_snapshot(), NOW)  # no artifact injected -> _load hits no_model_path
    assert res.signal is None and res.reason == "ml_filter_unavailable:no_model_path" and res.candidate is not None


def test_build_meta_dataset_binary_labels():
    from trading_agent.marketdata.historical import HistoricalOandaSource

    cfg = make_strategy(name="ml_filter", trend_tf="H4", bias_tf="H1", entry_tf="M15")
    frames = {"H4": make_frame(300, tf="H4"), "H1": make_frame(300, tf="H1"), "M15": make_frame(300, tf="M15")}
    source = HistoricalOandaSource(frames, entry_tf="M15")

    rows = list(build_meta_dataset(source, cfg, StubInner(), stride=10, horizon=20))
    assert rows, "the firing inner should yield candidate rows"
    for r in rows:
        assert r["label"] in (0, 1) and r["direction"] == "LONG"
        assert "entry_c_vs_kijun" in r  # features ride on the same record as the win/loss label
