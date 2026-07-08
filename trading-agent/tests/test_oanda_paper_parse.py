"""OANDA transaction-stream -> TradeEvent parsing (the network wrapper is validated live)."""

from __future__ import annotations

from datetime import datetime, timezone

from trading_agent.execution.oanda_paper import OandaPaperBackend

NOW = datetime(2026, 1, 6, 8, 30, tzinfo=timezone.utc)


def _backend() -> OandaPaperBackend:
    b = OandaPaperBackend.__new__(OandaPaperBackend)  # bypass network __init__
    b._trade_map = {}
    b._open = {}
    b._pending_meta = {}
    b._local_partials = set()
    b._instrument = "AUD_USD"
    return b


def test_open_fill_then_take_profit():
    b = _backend()
    open_txn = {"type": "ORDER_FILL", "price": "0.67010", "reason": "MARKET_ORDER", "tradeOpened": {"tradeID": "101", "units": "50000", "clientExtensions": {"id": "S-t1"}}}
    (fill,) = b._events_from_transactions([open_txn], NOW)
    assert fill.kind == "FILL"
    assert fill.trade_id == "S-t1"
    assert fill.price == 0.6701
    assert fill.units == 50000
    assert b._trade_map == {"101": "S-t1"}

    tp_txn = {"type": "ORDER_FILL", "price": "0.67400", "reason": "TAKE_PROFIT_ORDER", "tradesClosed": [{"tradeID": "101", "units": "-50000", "realizedPL": "195.0"}]}
    (exit_ev,) = b._events_from_transactions([tp_txn], NOW)
    assert exit_ev.kind == "EXIT"
    assert exit_ev.exit_reason == "TP"
    assert exit_ev.trade_id == "S-t1"
    assert exit_ev.pnl == 195.0
    assert exit_ev.price == 0.674
    assert "101" not in b._trade_map  # closed => untracked


def test_stop_loss_close():
    b = _backend()
    b._trade_map = {"102": "S-t2"}
    sl_txn = {"type": "ORDER_FILL", "price": "0.66800", "reason": "STOP_LOSS_ORDER", "tradesClosed": [{"tradeID": "102", "units": "-50000", "realizedPL": "-105.0"}]}
    (exit_ev,) = b._events_from_transactions([sl_txn], NOW)
    assert exit_ev.exit_reason == "STOP"
    assert exit_ev.pnl == -105.0
    assert exit_ev.trade_id == "S-t2"


def test_non_fill_transactions_ignored():
    assert _backend()._events_from_transactions([{"type": "HEARTBEAT"}, {"type": "ORDER_CANCEL"}], NOW) == []


def test_trade_reduced_partial_close():
    from trading_agent.execution.oanda_paper import _OandaOpen

    b = _backend()
    b._trade_map = {"101": "S-t1"}
    b._open["S-t1"] = _OandaOpen("101", "AUD_USD", 10000, 10000, 0.67, 0.66, 0.66, 0.68)
    reduced_txn = {
        "type": "ORDER_FILL",
        "price": "0.67200",
        "reason": "MARKET_ORDER",
        "tradeReduced": {"tradeID": "101", "units": "-5000", "realizedPL": "10.0"},
    }
    (exit_ev,) = b._events_from_transactions([reduced_txn], NOW)
    assert exit_ev.partial
    assert exit_ev.exit_reason == "SCALE_OUT"
    assert exit_ev.pnl == 10.0
    assert b._open["S-t1"].units == 5000
