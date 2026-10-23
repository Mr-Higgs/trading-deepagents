"""Feature builder: stable schema, finite values, and train/serve PARITY.

Parity is the point of a single build_features: the vector the offline dataset builds for a bar must be
bit-identical to the one MLStrategy computes for that same bar live. Here we compute it both ways - via
a HistoricalOandaSource snapshot (the dataset path) and by tailing the frame directly - and require
equality.
"""

from __future__ import annotations

import math

from conftest import make_frame, make_strategy

from trading_agent.marketdata.historical import HistoricalOandaSource
from trading_agent.ml.features import build_features, feature_names
from trading_agent.ml.labeling import build_dataset


def _cfg():
    return make_strategy(name="ml_signal", trend_tf="H4", bias_tf="H1", entry_tf="M15")


def test_schema_is_stable_and_finite():
    cfg = _cfg()
    empty = build_features({}, cfg)
    assert set(empty.values()) == {0.0}  # nothing computable from an empty snapshot
    frame = make_frame(160)
    feats = build_features({"H4": frame, "H1": frame, "M15": frame}, cfg)
    assert list(feats.keys()) == feature_names(cfg)  # identical, ordered schema regardless of input
    assert all(isinstance(v, float) and math.isfinite(v) for v in feats.values())


def test_train_serve_parity():
    """A dataset row's features (offline path) == build_features on the snapshot at that same ts
    (serve path). MLStrategy calls the identical function, so matching here is matching live."""
    cfg = _cfg()
    frames = {"H4": make_frame(300, tf="H4"), "H1": make_frame(300, tf="H1"), "M15": make_frame(300, tf="M15")}
    source = HistoricalOandaSource(frames, entry_tf="M15")
    names = feature_names(cfg)

    rec = next(iter(build_dataset(source, cfg, stride=1)))  # first labeled dataset row
    offline = {k: rec[k] for k in names}
    serve = build_features(source.snapshot(rec["ts"]), cfg)  # the exact call the engine makes at that bar
    assert offline == serve and offline  # identical, non-empty
    assert set(rec) == set(names) | {"label", "ts", "instrument"}
    assert rec["label"] in {"LONG", "SHORT", "FLAT"}
