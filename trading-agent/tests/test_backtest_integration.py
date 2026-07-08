"""End-to-end engine loop: a 1-win + 1-loss scenario, exact event sequence, and determinism.

Drives the loop with a stub strategy + hand-built bars so the loop wiring (reconcile -> gate ->
signal -> risk -> submit -> exit -> log) is what's under test, not the Ichimoku rules (unit-tested
elsewhere). Two identical runs must produce byte-identical JSONL.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from conftest import make_pair, make_risk

from trading_agent import engine
from trading_agent.config import AppConfig, SessionConfig
from trading_agent.eventlog import EventLog
from trading_agent.execution.simulated import SimulatedBackend
from trading_agent.risk import RiskEngine
from trading_agent.strategy.base import StrategyResult
from trading_agent.types import Candle, Signal

UTC = timezone.utc
STEP = timedelta(minutes=15)
DAY = datetime(2026, 1, 6, 8, 0, tzinfo=UTC)  # Tuesday, 08:00 UTC == 08:00 London (GMT in Jan) => session open


def _bar(i: int, o, h, l, c) -> Candle:  # noqa: E741
    ot = DAY + i * STEP
    return Candle("AUD_USD", "M15", ot, ot + STEP, o, h, l, c, 100, True)


# 7 bars: signal A -> fill -> TP (win); signal B -> fill -> STOP (loss); trailing skip.
_BARS = [
    _bar(0, 0.6700, 0.6700, 0.6700, 0.6700),  # A fires at close 08:15
    _bar(1, 0.6700, 0.6705, 0.6698, 0.6702),  # fill A at open 0.6700
    _bar(2, 0.6720, 0.6742, 0.6718, 0.6740),  # A tp 0.6740 hit -> win
    _bar(3, 0.6740, 0.6740, 0.6740, 0.6740),  # B fires at close 09:00
    _bar(4, 0.6740, 0.6743, 0.6737, 0.6739),  # fill B at open 0.6740
    _bar(5, 0.6735, 0.6738, 0.6718, 0.6722),  # B stop 0.6720 hit -> loss
    _bar(6, 0.6722, 0.6722, 0.6722, 0.6722),  # nothing
]


def _sig(now: datetime, entry: float, stop: float, tp: float) -> Signal:
    return Signal(now, f"S-{now:%H%M}", "AUD_USD", "LONG", entry, stop, tp, 2.0, "stub", "v1.0.0", {}, "stub_long")


class _FakeSource:
    entry_tf = "M15"

    def __init__(self, bars: list[Candle]) -> None:
        self._bars = bars

    def stream(self) -> Iterator[Candle]:
        yield from self._bars

    def snapshot(self, now: datetime) -> dict:
        return {}


class _StubStrategy:
    version = "v1.0.0"

    def __init__(self) -> None:
        # keyed by decision time (bar close): A at 08:15, B at 09:00
        self._by_time = {
            DAY + STEP: _sig(DAY + STEP, 0.6700, 0.6680, 0.6740),  # dist .002 -> 50000 units
            DAY + 4 * STEP: _sig(DAY + 4 * STEP, 0.6740, 0.6720, 0.6780),
        }

    def evaluate(self, snapshot: dict, now: datetime) -> StrategyResult:
        sig = self._by_time.get(now)
        return StrategyResult(sig, sig.reason if sig else "no_setup")


def _cfg() -> AppConfig:
    session = SessionConfig(timezone="Europe/London", start=datetime(2000, 1, 1, 7, 0).time(), end=datetime(2000, 1, 1, 16, 0).time(), weekdays=frozenset({0, 1, 2, 3, 4}))
    from trading_agent.config import StrategyConfig  # local import keeps the fixture self-contained

    from conftest import make_strategy

    return AppConfig(pairs={"AUD_USD": make_pair()}, sessions=(session,), risk=make_risk(), strategy=make_strategy(), config_dir=None)


def _run(path) -> float:
    log = EventLog(path, run_id="test", strategy_version="v1.0.0")
    cfg = _cfg()
    equity = engine.run(_FakeSource(_BARS), _StubStrategy(), RiskEngine(cfg.risk, cfg.pair), SimulatedBackend(0.0001, 1.0, 0.0), log, cfg, starting_equity=10_000.0)
    log.close()
    return equity


def test_event_sequence_and_pnl(tmp_path):
    import json

    path = tmp_path / "events.jsonl"
    equity = _run(path)

    records = [json.loads(x) for x in path.read_text().splitlines()]
    types = [r["type"] for r in records]
    assert types == [
        "RUN_START",
        "SIGNAL", "SUBMIT",  # bar0
        "FILL", "SKIP",      # bar1
        "EXIT", "SKIP",      # bar2
        "SIGNAL", "SUBMIT",  # bar3
        "FILL", "SKIP",      # bar4
        "EXIT", "SKIP",      # bar5
        "SKIP",              # bar6
        "RUN_END",
    ]

    submits = [r for r in records if r["type"] == "SUBMIT"]
    fills = {r["trade_id"]: r for r in records if r["type"] == "FILL"}
    exits = [r for r in records if r["type"] == "EXIT"]

    # Sizing compounds on equity: A sized at 10000 (50000 units); B sized after the +195 win (50975).
    assert submits[0]["units"] == 50_000
    assert submits[1]["units"] == 50_975

    # pnl of each exit is internally consistent with its own fill price and units.
    for ex in exits:
        fill = fills[ex["trade_id"]]
        assert ex["pnl"] == pytest.approx(ex["units"] * (ex["price"] - fill["price"]))
    assert exits[0]["exit_reason"] == "TP"
    assert exits[0]["pnl"] > 0  # win
    assert exits[1]["exit_reason"] == "STOP"
    assert exits[1]["pnl"] < 0  # loss
    assert exits[0]["price"] == pytest.approx(0.6740)
    assert exits[1]["price"] == pytest.approx(0.6720)
    assert equity == pytest.approx(10_000 + exits[0]["pnl"] + exits[1]["pnl"])
    # every skip/reject/halt carries a reason
    for r in records:
        if r["type"] in {"SKIP", "REJECT", "HALT"}:
            assert r.get("reason")


def test_determinism_byte_identical(tmp_path):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    _run(a)
    _run(b)
    assert a.read_bytes() == b.read_bytes()
