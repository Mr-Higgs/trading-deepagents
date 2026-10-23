"""Triple-barrier labeling: barrier logic + a real build_dataset pass."""

from __future__ import annotations

from conftest import make_frame, make_strategy

from trading_agent.marketdata.historical import HistoricalOandaSource
from trading_agent.ml.labeling import build_dataset, label_bar, triple_barrier_win


def test_barrier_direction_and_tiebreak():
    up_h, up_l = [10.5, 11.0, 12.0], [9.9, 10.4, 11.0]
    assert triple_barrier_win(up_h, up_l, 10.0, 1.0, 1.0, "LONG", 3)
    assert label_bar(up_h, up_l, 10.0, 1.0, 1.0, 3) == "LONG"

    # bar 1 trades through BOTH stop (low 9.0) and tp (high 11.0); stop-first tie-break => a loss, not a win
    both = ([11.0], [9.0])
    assert not triple_barrier_win(both[0], both[1], 10.0, 1.0, 1.0, "LONG", 1)

    # stop-out before any tp on the long side => the short side wins the bar
    dn_h, dn_l = [10.2, 10.3], [8.5, 8.0]
    assert label_bar(dn_h, dn_l, 10.0, 1.0, 1.0, 2) == "SHORT"

    # time barrier: neither level reached within the horizon => FLAT
    assert label_bar([10.1, 10.2], [9.9, 9.8], 10.0, 1.0, 1.0, 2) == "FLAT"


def test_build_dataset_emits_labeled_feature_rows():
    cfg = make_strategy(name="ml_signal", trend_tf="H4", bias_tf="H1", entry_tf="M15", max_hold_bars=8)
    frames = {"H4": make_frame(300, tf="H4"), "H1": make_frame(300, tf="H1"), "M15": make_frame(300, tf="M15")}
    source = HistoricalOandaSource(frames, entry_tf="M15")

    rows = list(build_dataset(source, cfg, stride=5))
    assert rows, "expected labeled rows past the warmup"
    warmup = cfg.ichimoku.min_rows
    horizon = cfg.max_hold_bars
    n = len(source._frames["M15"])  # noqa: SLF001
    for r in rows:
        assert r["label"] in {"LONG", "SHORT", "FLAT"}
        assert r["instrument"] == "AUD_USD"
        assert "entry_c_vs_kijun" in r  # feature keys ride on the same record as the label
    # every labeled bar has both its warmup history and its forward horizon (no edge peeking)
    assert all(r["ts"] <= source._frames["M15"]["close_time"].iloc[n - 1 - horizon] for r in rows)  # noqa: SLF001
    assert all(r["ts"] >= source._frames["M15"]["close_time"].iloc[warmup] for r in rows)  # noqa: SLF001
