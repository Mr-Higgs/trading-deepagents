"""JevTrader: setup choice, level menus, exit management, and the engine's strategy-owned exit hook.

A scripted decider stands in for Jev (no network); one test drives the real SDK through an in-process transport.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from conftest import make_pair, make_risk, make_strategy

from trading_agent.config import AgentsConfig, AppConfig, JevTraderConfig
from trading_agent.marketdata.base import FRAME_COLUMNS, normalize_frame
from trading_agent.strategy.agents.verdict_cache import VerdictCache
from trading_agent.strategy.base import StrategyResult
from trading_agent.strategy.jev_levels import Context, Level, best_pair, exit_menu, stop_options, tp_options
from trading_agent.strategy.jev_trader import JevDecider, JevTrader
from trading_agent.types import OpenTrade, Signal

START = datetime(2026, 1, 1, tzinfo=UTC)
NOW = datetime(2026, 1, 6, 8, 30, tzinfo=UTC)
STEP = {"H4": timedelta(hours=4), "H1": timedelta(hours=1), "M15": timedelta(minutes=15)}
PIP = 0.0001


def _walk(tf: str, n: int, base: float, drift: float, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = base + np.cumsum(drift + rng.normal(0, 0.0004, n))
    rows = []
    for j, c in enumerate(closes):
        o = closes[j - 1] if j else base
        hi, lo = max(o, c) + 0.0003, min(o, c) - 0.0003
        rows.append(("AUD_USD", tf, START + j * STEP[tf], START + (j + 1) * STEP[tf], o, hi, lo, c, 100, True))
    return normalize_frame(pd.DataFrame(rows, columns=FRAME_COLUMNS))


def _snapshot(drift: float = 0.0003) -> dict[str, pd.DataFrame]:
    return {"H4": _walk("H4", 140, 0.65, drift, 1), "H1": _walk("H1", 140, 0.66, drift, 2), "M15": _walk("M15", 140, 0.67, drift / 2, 3)}


def _cfg(**jev) -> AppConfig:
    agents = AgentsConfig(trader="jev", jev=JevTraderConfig(**jev))
    return AppConfig(pairs={"AUD_USD": make_pair()}, sessions=(), risk=make_risk(min_rr=1.5, min_stop_pips=3.0),
                     strategy=make_strategy(), config_dir=Path("."), agents=agents)


class _Decider:
    """Scripted Jev: `script(name, labels)` -> probabilities for that question; records every request."""

    def __init__(self, script) -> None:
        self.script = script
        self.calls: list[tuple[dict, dict]] = []

    def choose(self, state, questions):
        self.calls.append((state, questions))
        if isinstance(self.script, Exception):
            raise self.script
        return {name: self.script(name, list(opts)) for name, (_, opts) in questions.items()}


def _prefer(**wanted: str):
    """Put 0.9 on `wanted[question]` (or on the first label for unnamed questions)."""

    def script(name: str, labels: list[str]) -> dict[str, float]:
        pick = wanted.get(name, labels[0])
        return {lbl: (0.9 if lbl == pick else 0.1 / max(len(labels) - 1, 1)) for lbl in labels}

    return script


class _Fires:
    def __init__(self, sig: Signal | None) -> None:
        self.sig = sig

    def evaluate(self, snapshot, now):
        return StrategyResult(self.sig, self.sig.reason if self.sig else "no_setup")


def _trader(decider, fired: Signal | None = None, **jev) -> JevTrader:
    t = JevTrader(_cfg(**jev), decider=decider)
    t.strategies = {"ichimoku_pullback": _Fires(fired)}
    return t


def _rule_signal(snapshot, direction: str = "LONG") -> Signal:
    close = float(snapshot["M15"]["c"].iloc[-1])
    stop = close - 0.0020 if direction == "LONG" else close + 0.0020
    tp = close + 0.0050 if direction == "LONG" else close - 0.0050
    return Signal(NOW, "s", "AUD_USD", direction, close, stop, tp, 2.5, "ichimoku_pullback", "v1.0.0", {"h4_close": 0.7}, "trend_pullback_long")


def test_no_setup_and_no_discretionary_skips_without_calling_jev():
    d = _Decider(_prefer())
    out = _trader(d).evaluate(_snapshot(), NOW)
    assert out.signal is None and out.reason == "jev_no_setup" and d.calls == []


def test_jev_picks_setup_then_levels_from_code_menus():
    snap = _snapshot()
    d = _Decider(_prefer(setup="ichimoku_pullback_long", stop="bias_kijun", take_profit="atr_3"))
    out = _trader(d, _rule_signal(snap)).evaluate(snap, NOW)
    sig = out.signal
    assert sig is not None and sig.direction == "LONG" and out.reason == "jev_ichimoku_pullback_long"
    ctx = Context.build(snap, make_strategy(), PIP)
    stops = {lv.label: lv.price for lv in stop_options(ctx, make_strategy(), "LONG", 3.0, _rule_signal(snap).stop)}
    assert sig.stop == stops[sig.features["jev"]["stop"]]  # the price comes from the code menu, never from Jev
    assert sig.stop < sig.entry_ref < sig.tp and sig.rr >= 1.5
    assert sig.features["jev"]["setup"] == "ichimoku_pullback_long" and sig.features["h4_close"] == 0.7
    assert len(d.calls) == 2 and d.calls[1][0]["trade"] == {"setup": "ichimoku_pullback_long", "direction": "LONG"}
    assert "no_trade" in d.calls[0][1]["setup"][1]


@pytest.mark.parametrize("script", [_prefer(setup="no_trade"), lambda n, labels: {lbl: 1 / len(labels) for lbl in labels}])
def test_no_trade_or_low_confidence_stands_aside(script):
    snap = _snapshot()
    out = _trader(_Decider(script), _rule_signal(snap), min_setup_prob=0.6).evaluate(snap, NOW)  # uniform 2-way = 0.5
    assert out.signal is None and out.reason.startswith("jev_no_trade:")


@pytest.mark.parametrize("stage", ["setup", "levels"])
def test_jev_failure_fails_closed(stage):
    snap = _snapshot()

    def script(name, labels):
        if (stage == "setup") == (name == "setup"):
            raise RuntimeError("down")
        return _prefer(setup="ichimoku_pullback_long")(name, labels)

    out = _trader(_Decider(script), _rule_signal(snap)).evaluate(snap, NOW)
    assert out.signal is None and out.reason == f"jev_error:{stage}"


def test_discretionary_short_builds_a_signal_without_a_rule_setup():
    snap = _snapshot(drift=-0.0003)
    out = _trader(_Decider(_prefer(setup="discretionary_short")), None, discretionary=True).evaluate(snap, NOW)
    sig = out.signal
    assert sig is not None and sig.direction == "SHORT" and sig.setup_id == "jev_discretionary"
    assert sig.tp < sig.entry_ref < sig.stop and "h4_cloud_top" in sig.features  # gate prompt keys present


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_level_menus_are_on_the_right_side(direction):
    s = make_strategy()
    ctx = Context.build(_snapshot(), s, PIP)
    stops, tps = stop_options(ctx, s, direction, 3.0), tp_options(ctx, s, direction)
    assert stops and tps
    long = direction == "LONG"
    assert all((lv.price < ctx.close) if long else (lv.price > ctx.close) for lv in stops)
    assert all(abs(lv.price - ctx.close) >= 3.0 * PIP for lv in stops)
    assert all((lv.price > ctx.close) if long else (lv.price < ctx.close) for lv in tps)
    assert len({round(lv.price, 5) for lv in stops}) == len(stops)  # deduped


def test_best_pair_honors_the_rr_floor():
    stops = [Level("tight", 0.9990, ""), Level("wide", 0.9900, "")]
    tps = [Level("near", 1.0015, ""), Level("far", 1.0030, "")]
    pair = best_pair(stops, tps, {"tight": 0.2, "wide": 0.8}, {"near": 0.9, "far": 0.1}, 1.0, 1.5)
    assert pair is not None and (pair[0].label, pair[1].label) == ("tight", "near")  # wide stop fails 1.5R with either TP
    assert best_pair(stops, tps, {}, {}, 1.0, 10.0) is None
    # 1.5R clears raw, but not once the risk engine's 1-pip cost is charged to both legs: (15-1)/(10+1) < 1.5
    assert best_pair(stops[:1], tps[:1], {}, {}, 1.0, 1.5, cost=0.0001) is None


class _Backend:
    def __init__(self, positions: list[OpenTrade]) -> None:
        self.positions = positions
        self.closed: list[str] = []
        self.moved: list[tuple[str, float]] = []

    def open_positions(self):
        return self.positions

    def request_close(self, trade_id):
        self.closed.append(trade_id)

    def modify_stop(self, trade_id, stop):
        self.moved.append((trade_id, stop))


class _Log:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict]] = []

    def write(self, type, ts, **kw):
        self.records.append((type, kw))


def _long_in_profit(snap) -> OpenTrade:
    close = float(snap["M15"]["c"].iloc[-1])
    return OpenTrade("t1", "AUD_USD", 1000, entry=close - 0.0030, stop=close - 0.0060, init_stop=close - 0.0060, tp=close + 0.0050, remaining_units=1000)


def test_manage_moves_stop_to_breakeven_only_tightening():
    snap = _snapshot()
    pos = _long_in_profit(snap)
    backend, log = _Backend([pos]), _Log()
    d = _Decider(lambda name, labels: _prefer(**{name: "breakeven"})(name, labels))
    _trader(d).manage(backend, None, snap, NOW, log)
    assert backend.moved and backend.moved[0][0] == "t1"
    new = backend.moved[0][1]
    assert pos.stop < new < float(snap["M15"]["c"].iloc[-1]) and log.records[0][0] == "STOP_MODIFIED"
    assert log.records[0][1]["reason"] == "jev_breakeven"
    assert d.calls[0][0]["positions"]["p0"]["status"] == "in_profit"


def test_manage_close_and_confidence_floor_and_errors():
    snap = _snapshot()
    backend, log = _Backend([_long_in_profit(snap)]), _Log()
    _trader(_Decider(lambda n, labels: _prefer(**{n: "close_now"})(n, labels))).manage(backend, None, snap, NOW, log)
    assert backend.closed == ["t1"] and log.records[0][1]["reason"] == "jev_close"

    quiet = _Backend([_long_in_profit(snap)])
    _trader(_Decider(lambda n, labels: {lbl: 1 / len(labels) for lbl in labels})).manage(quiet, None, snap, NOW, _Log())
    assert quiet.closed == [] and quiet.moved == []  # below min_exit_prob => hold

    broken, blog = _Backend([_long_in_profit(snap)]), _Log()
    _trader(_Decider(RuntimeError("down"))).manage(broken, None, snap, NOW, blog)
    assert broken.closed == [] and broken.moved == [] and blog.records[0][0] == "MANAGE_SKIP"

    off = _Backend([_long_in_profit(snap)])
    d = _Decider(_prefer())
    _trader(d, manage_exits=False).manage(off, None, snap, NOW, _Log())
    assert d.calls == []


def test_exit_menu_never_offers_a_looser_stop():
    snap = _snapshot()
    ctx = Context.build(snap, make_strategy(), PIP)
    pos = replace(_long_in_profit(snap), stop=ctx.close - PIP / 2)  # stop already hugging price
    _, stops = exit_menu(ctx, pos, make_strategy(), 0.0)
    assert stops == {}


def test_agent_gate_forwards_the_exit_hook():
    pytest.importorskip("pydantic")
    from trading_agent.strategy.agent_strategy import AgentStrategy

    snap = _snapshot()
    backend = _Backend([_long_in_profit(snap)])
    gate = AgentStrategy(_trader(_Decider(lambda n, labels: _prefer(**{n: "close_now"})(n, labels))), orchestrator=None, pair=None)
    gate.manage(backend, None, snap, NOW, _Log())
    assert backend.closed == ["t1"]


def test_engine_calls_the_strategy_exit_hook_every_bar(tmp_path):
    from test_backtest_integration import _BARS, _FakeSource, _StubStrategy
    from test_backtest_integration import _cfg as engine_cfg

    from trading_agent import engine
    from trading_agent.eventlog import EventLog
    from trading_agent.execution.simulated import SimulatedBackend
    from trading_agent.risk import RiskEngine

    class _Managed(_StubStrategy):
        def __init__(self) -> None:
            super().__init__()
            self.seen: list[int] = []

        def manage(self, backend, bar, snapshot, now, log):
            self.seen.append(len(backend.open_positions()))

    cfg, strat = engine_cfg(), _Managed()
    log = EventLog(tmp_path / "e.jsonl", run_id="t", strategy_version="v")
    engine.run(_FakeSource(_BARS), strat, RiskEngine(cfg.risk, cfg.pair), SimulatedBackend(0.0001, 1.0, 0.0), log, cfg)
    log.close()
    assert len(strat.seen) == len(_BARS) and max(strat.seen) == 1  # called on every bar, sees the open trade


def test_real_sdk_choice_roundtrip_and_cache(tmp_path):
    httpx2 = pytest.importorskip("httpx2")
    typesafe_sdk = pytest.importorskip("typesafe_sdk")
    sent: list[dict] = []

    def handle(request):
        body = json.loads(request.content)
        sent.append(body)
        answers = {n: {"type": "choice", "choice": next(iter(q["criteria"])), "confidence": 0.8,
                       "probabilities": {lbl: (0.8 if i == 0 else 0.2) for i, lbl in enumerate(q["criteria"])}}
                   for n, q in body["questions"].items()}
        return httpx2.Response(200, json={"model": "jev-1.13.0", "usage": {"input_tokens": 50}, "answers": answers})

    client = typesafe_sdk.TypeSafeClient(api_key="test", model="jev-1.13.0", transport=httpx2.MockTransport(handle),
                                         retry=typesafe_sdk.RetryPolicy(max_retries=0))
    decider = JevDecider("jev-1.13.0", client=client, cache=VerdictCache(tmp_path / "jev.jsonl"))
    snap = _snapshot()
    t = JevTrader(_cfg(), decider=decider)
    t.strategies = {"ichimoku_pullback": _Fires(_rule_signal(snap))}
    first = t.evaluate(snap, NOW)
    assert first.signal is not None and sent[0]["questions"]["setup"]["type"] == "choice"
    assert len(sent) == 2  # setup + levels
    again = t.evaluate(snap, NOW)
    assert len(sent) == 2 and again.signal == first.signal  # replayed from cache, byte-identical decision
