"""Trade management: breakeven + trailing, scale-out partials, and the simulated backend honoring a moved stop."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from trading_agent import trade_management
from trading_agent.config import ManagementConfig, ScaleLeg, ScaleOutConfig, load_config
from trading_agent.execution.simulated import SimulatedBackend
from trading_agent.trade_management import compute_managed_stop, manage_open_trades
from trading_agent.types import Candle, OpenTrade, OrderRequest

UTC = timezone.utc
T = datetime(2026, 1, 6, 8, 30, tzinfo=UTC)


def _bar(o: float, h: float, low: float, c: float) -> Candle:
    return Candle("AUD_USD", "M15", T, T, o, h, low, c, 100, complete=True)


def test_self_check():
    trade_management._demo()  # asserts internally


def test_breakeven_arms_at_10_pips_long():
    pip = 0.0001
    mgmt = ManagementConfig(breakeven_enabled=True, breakeven_trigger_pips=10.0, breakeven_buffer_pips=0.0)
    pos = OpenTrade("t1", "AUD_USD", 1000, entry=1.0000, stop=0.9950, init_stop=0.9950, tp=1.0100, remaining_units=1000)
    assert compute_managed_stop(pos, _bar(1.0, 1.0005, 0.9990, 1.0002), pip, mgmt, None) is None
    moved = compute_managed_stop(pos, _bar(1.0, 1.0015, 0.9990, 1.0010), pip, mgmt, None)
    assert moved is not None and abs(moved[0] - 1.0000) < 1e-9 and moved[1] == "breakeven"


def test_breakeven_arms_at_10_pips_short():
    pip = 0.0001
    mgmt = ManagementConfig(breakeven_enabled=True, breakeven_trigger_pips=10.0, breakeven_buffer_pips=0.0)
    pos = OpenTrade("s1", "AUD_USD", -1000, entry=1.0000, stop=1.0050, init_stop=1.0050, tp=0.9900, remaining_units=-1000)
    assert compute_managed_stop(pos, _bar(1.0, 1.0005, 0.9995, 1.0000), pip, mgmt, None) is None
    moved = compute_managed_stop(pos, _bar(1.0, 1.0005, 0.9985, 0.9990), pip, mgmt, None)
    assert moved is not None and abs(moved[0] - 1.0000) < 1e-9 and moved[1] == "breakeven"


def test_sim_honors_moved_stop_to_breakeven():
    pip = 0.0001
    sim = SimulatedBackend(pip_size=pip, spread_pips=0, slippage_pips=0)
    sim.submit(OrderRequest("AUD_USD", 1000, stop=0.9950, tp=1.0100, signal_id="s", trade_id="s-t1", strategy_version="v"))
    (fill,) = sim.reconcile(T, _bar(1.0000, 1.0000, 1.0000, 1.0000))
    assert fill.kind == "FILL" and abs(fill.price - 1.0000) < 1e-9

    sim.modify_stop("s-t1", 1.0000)
    pos = sim.open_positions()[0]
    assert abs(pos.stop - 1.0000) < 1e-9 and abs(pos.init_stop - 0.9950) < 1e-9

    exits = [e for e in sim.reconcile(T, _bar(1.0005, 1.0006, 1.0000, 1.0002)) if e.kind == "EXIT"]
    assert len(exits) == 1
    assert exits[0].exit_reason == "STOP"
    assert abs(exits[0].price - 1.0000) < 1e-9
    assert exits[0].pnl == 0.0


def test_sim_original_stop_would_not_exit_without_move():
    pip = 0.0001
    sim = SimulatedBackend(pip_size=pip, spread_pips=0, slippage_pips=0)
    sim.submit(OrderRequest("AUD_USD", 1000, stop=0.9950, tp=1.0100, signal_id="s", trade_id="s-t2", strategy_version="v"))
    sim.reconcile(T, _bar(1.0000, 1.0000, 1.0000, 1.0000))
    assert not [e for e in sim.reconcile(T, _bar(1.0005, 1.0006, 1.0000, 1.0002)) if e.kind == "EXIT"]


def test_sim_reduce_position_partial_then_full():
    pip = 0.0001
    sim = SimulatedBackend(pip_size=pip, spread_pips=0, slippage_pips=0)
    sim.submit(OrderRequest("AUD_USD", 1000, stop=0.9950, tp=1.0100, signal_id="s", trade_id="s-t3", strategy_version="v"))
    sim.reconcile(T, _bar(1.0000, 1.0000, 1.0000, 1.0000))
    partial = sim.reduce_position("s-t3", 500, 1.0020, T)
    assert partial is not None and partial.partial and partial.remaining_units == 500
    assert partial.pnl == 500 * (1.0020 - 1.0000)
    assert sim.open_positions()[0].remaining_units == 500
    final = sim.reduce_position("s-t3", 500, 1.0030, T)
    assert final is not None and not final.partial
    assert sim.open_trades() == []


class _StubBackend:
    def __init__(self, positions: list[OpenTrade]) -> None:
        self._positions = positions
        self.moved: list[tuple[str, float]] = []
        self.reduced: list[tuple[str, int, float]] = []

    def open_positions(self) -> list[OpenTrade]:
        return self._positions

    def modify_stop(self, trade_id: str, new_stop: float) -> None:
        self.moved.append((trade_id, new_stop))

    def reduce_position(self, trade_id: str, units_to_close: int, price: float, now: datetime) -> None:
        self.reduced.append((trade_id, units_to_close, price))
        return None


class _StubLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def write(self, type: str, ts, **payload) -> None:  # noqa: A002
        self.events.append((type, payload))


def test_manage_open_trades_emits_stop_modified_after_10_pip_trigger():
    cfg = load_config("config")
    assert cfg.risk.management.breakeven_enabled
    pos = OpenTrade("s-t1", "AUD_USD", 1000, entry=1.0000, stop=0.9950, init_stop=0.9950, tp=1.0100, remaining_units=1000)
    backend = _StubBackend([pos])
    log = _StubLog()
    manage_open_trades(backend, _bar(1.0040, 1.0015, 1.0035, 1.0010), {}, T, cfg, log)  # type: ignore[arg-type]
    assert len(backend.moved) == 1
    tid, new_stop = backend.moved[0]
    assert tid == "s-t1" and abs(new_stop - 1.0000) < 1e-9
    assert log.events and log.events[0][0] == "STOP_MODIFIED" and log.events[0][1]["reason"] == "breakeven"


def test_manage_open_trades_no_move_before_trigger():
    cfg = load_config("config")
    pos = OpenTrade("s-t1", "AUD_USD", 1000, entry=1.0000, stop=0.9950, init_stop=0.9950, tp=1.0100, remaining_units=1000)
    backend = _StubBackend([pos])
    log = _StubLog()
    manage_open_trades(backend, _bar(1.0020, 1.0005, 1.0015, 1.0003), {}, T, cfg, log)  # only +5 pips
    assert not backend.moved and not log.events


def test_scale_out_long_banks_at_kijun(monkeypatch):
    import dataclasses

    from trading_agent import trade_management as tm

    cfg = load_config("config")
    scale = ScaleOutConfig(enabled=True, legs=(ScaleLeg("kijun", 0.5),), move_be_after_first=True, runner_trail="off")
    mgmt = dataclasses.replace(cfg.risk.management, breakeven_enabled=False, scale_out=scale)
    cfg = dataclasses.replace(cfg, risk=dataclasses.replace(cfg.risk, management=mgmt))

    pip = 0.0001
    sim = SimulatedBackend(pip_size=pip, spread_pips=0, slippage_pips=0)
    sim.submit(OrderRequest("AUD_USD", 1000, stop=0.9950, tp=1.0100, signal_id="s", trade_id="s-t4", strategy_version="v"))
    sim.reconcile(T, _bar(1.0000, 1.0000, 1.0000, 1.0000))

    frame = pd.DataFrame({"o": [1.0] * 80, "h": [1.005] * 80, "l": [1.0] * 80, "c": [1.004] * 80})
    ich_df = pd.DataFrame({"kijun": [1.002], "senkou_a": [1.008], "tenkan": [1.001], "senkou_b": [0.99], "cloud_top": [1.01], "cloud_bottom": [0.99]})
    monkeypatch.setattr(tm, "ichimoku", lambda df, **kwargs: ich_df)  # noqa: ARG005

    events = manage_open_trades(sim, _bar(1.0, 1.005, 1.0, 1.004), {cfg.strategy.entry_tf: frame}, T, cfg, _StubLog())
    assert len(events) == 1 and events[0].partial
    assert events[0].units == 500
    assert sim.open_positions()[0].remaining_units == 500
