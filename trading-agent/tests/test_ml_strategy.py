"""MLStrategy: fail-closed behavior, inference->Signal geometry, and a real joblib round-trip.

The model itself is stubbed (a fixed predict_proba) so these tests need no sklearn - they check the
STRATEGY plumbing: schema guard, threshold, code-owned stop/TP, and that any load problem degrades to a
reasoned skip instead of an exception into the engine.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from conftest import make_frame, make_strategy

from trading_agent.ml.features import feature_names
from trading_agent.strategy.ml_strategy import MLStrategy

UTC = timezone.utc
NOW = datetime(2025, 1, 5, 8, tzinfo=UTC)


class StubModel:
    """A module-level (picklable) classifier stub returning a fixed probability vector."""

    def __init__(self, probs=(0.0, 1.0, 0.0)):
        self.probs = list(probs)

    def predict_proba(self, _row):
        return [self.probs]


def _snapshot():
    frame = make_frame(140)
    return {"H4": frame, "H1": frame, "M15": frame}


def _artifact(cfg, probs=(0.0, 1.0, 0.0)):
    return {"model": StubModel(probs), "features": feature_names(cfg), "classes": ["FLAT", "LONG", "SHORT"], "meta": {}}


def test_fails_closed_without_a_model():
    cfg = make_strategy(name="ml_signal", model_path="")
    res = MLStrategy(cfg, None).evaluate(_snapshot(), NOW)
    assert res.signal is None and res.reason == "ml_skip:no_model_path"


def test_predicts_long_and_owns_stop_tp():
    cfg = make_strategy(name="ml_signal", stop_rule="atr", atr_length=14, atr_mult=2.0, rr=2.0, prob_threshold=0.5)
    strat = MLStrategy(cfg, None)
    strat._loaded, strat._artifact = True, _artifact(cfg)  # inject a LONG-predicting model  # noqa: SLF001

    res = strat.evaluate(_snapshot(), NOW)
    sig = res.signal
    assert sig is not None and sig.direction == "LONG" and sig.setup_id == "ml_signal"
    assert sig.stop < sig.entry_ref < sig.tp  # code-owned geometry, correctly ordered for a long
    assert sig.rr == cfg.rr and sig.features["stop_distance"] > 0
    assert sig.features["ml_class"] == "LONG" and sig.features["ml_score"] == 1.0


def test_skips_below_threshold():
    cfg = make_strategy(name="ml_signal", prob_threshold=0.8)
    strat = MLStrategy(cfg, None)
    strat._loaded, strat._artifact = True, _artifact(cfg, probs=(0.4, 0.35, 0.25))  # LONG only 0.35 < 0.8  # noqa: SLF001
    res = strat.evaluate(_snapshot(), NOW)
    assert res.signal is None and res.reason.startswith("ml_skip:")


def test_joblib_roundtrip_and_missing_file(tmp_path):
    joblib = pytest.importorskip("joblib")
    cfg = make_strategy(name="ml_signal", atr_mult=2.0, rr=2.0)

    path = tmp_path / "m.joblib"
    joblib.dump(_artifact(cfg), path)
    res = MLStrategy(make_strategy(name="ml_signal", atr_mult=2.0, rr=2.0, model_path=str(path)), None).evaluate(_snapshot(), NOW)
    assert res.signal is not None and res.signal.direction == "LONG"  # loaded from disk, predicted, emitted

    missing = MLStrategy(make_strategy(name="ml_signal", model_path=str(tmp_path / "nope.joblib")), None).evaluate(_snapshot(), NOW)
    assert missing.signal is None and missing.reason == "ml_skip:model_missing"
