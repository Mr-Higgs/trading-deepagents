"""WFX signal-based early exit: the simulated backend's request_close primitive.

The exit_cross rule itself is unit-tested in test_signals.py; here we verify the execution seam -
a queued close fills at the NEXT bar's open as an EXIT/MANUAL, symmetric with how orders fill.
"""

from __future__ import annotations

from datetime import datetime, timezone

from trading_agent.execution.simulated import SimulatedBackend
from trading_agent.types import Candle, OrderRequest

UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _bar(o: float) -> Candle:
    return Candle("AUD_USD", "M15", T0, T0, o, o + 0.001, o - 0.001, o, 100, complete=True)


def test_request_close_fills_next_bar_open_as_manual_exit():
    be = SimulatedBackend(0.0001, 0.0, 0.0)  # zero cost => exact prices
    be.submit(OrderRequest("AUD_USD", 1000, stop=0.9950, tp=1.0100, signal_id="s1", trade_id="t1", strategy_version="v2.0.0"))
    be.reconcile(T0, _bar(1.0000))  # fills t1 at 1.0000
    assert be.open_trades() == ["t1"]

    be.request_close("t1")
    events = be.reconcile(T0, _bar(1.0050))  # closes at this bar's open, before any stop/TP
    exits = [e for e in events if e.kind == "EXIT"]
    assert len(exits) == 1
    assert exits[0].exit_reason == "MANUAL"
    assert exits[0].price == 1.0050
    assert abs(exits[0].pnl - 1000 * (1.0050 - 1.0000)) < 1e-9
    assert be.open_trades() == []

    # idempotent / safe after the trade is gone: no queue, no crash, no phantom exit
    be.request_close("t1")
    assert be.reconcile(T0, _bar(1.0060)) == []
