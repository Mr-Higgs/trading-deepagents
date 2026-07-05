"""The single decision loop, shared byte-for-byte by backtest and live/paper.

Only the injected `source` (historical vs live) and `backend` (simulated vs OANDA) differ between
modes. The decision clock is each closed entry-timeframe bar's close_time; nothing here reads the
wall clock, so a backtest is deterministic and a live run is just the same loop fed one bar at a time.
"""

from __future__ import annotations

from dataclasses import asdict

from trading_agent.config import AppConfig
from trading_agent.eventlog import EventLog
from trading_agent.execution.base import ExecutionBackend
from trading_agent.marketdata.base import MarketDataSource
from trading_agent.risk import RiskEngine, trading_day, update_day_state
from trading_agent.sessions import is_open, in_news_window
from trading_agent.strategy.base import Strategy
from trading_agent.types import AccountState, OrderRequest


def run(
    source: MarketDataSource,
    strategy: Strategy,
    risk: RiskEngine,
    backend: ExecutionBackend,
    log: EventLog,
    cfg: AppConfig,
    starting_equity: float = 10_000.0,
) -> float:
    """Drive the loop to exhaustion of `source.stream()`; returns final equity."""
    reset = cfg.risk.trading_day_reset_utc
    equity = starting_equity
    day = None
    now = None
    started = False

    for bar in source.stream():
        now = bar.close_time
        if not started:
            log.write("RUN_START", ts=now, reason="run_start", instrument=cfg.strategy.instrument, starting_equity=starting_equity)
            started = True

        td = trading_day(now, reset)
        if day is None or day.trading_day != td:
            day = EventLog.replay_today(log.path, td, reset)  # restart-safe seed (empty log => fresh)

        # 1. settle fills/exits from prior ticks BEFORE considering new entries
        for ev in backend.reconcile(now, bar):
            if ev.kind == "FILL":
                log.write("FILL", ts=now, reason="fill", trade_id=ev.trade_id, price=ev.price, units=ev.units)
            else:  # EXIT
                log.write("EXIT", ts=now, reason=ev.exit_reason, trade_id=ev.trade_id, price=ev.price, units=ev.units, pnl=ev.pnl, exit_reason=ev.exit_reason)
                equity += ev.pnl or 0.0
                was_halted = day.halted
                update_day_state(day, ev, equity, cfg.risk.max_daily_loss_pct)
                if day.halted and not was_halted:
                    log.write("HALT", ts=now, reason=day.halt_reason)

        # 2. gates (each blocked entry logged with its reason)
        if day.halted:
            log.write("SKIP", ts=now, reason=day.halt_reason or "day_halted")
            continue
        if not is_open(now, cfg.session):
            log.write("SKIP", ts=now, reason="session_closed")
            continue
        if in_news_window(now, cfg.risk.news_windows):
            log.write("SKIP", ts=now, reason="news_window")
            continue

        # 3. strategy proposal
        res = strategy.evaluate(source.snapshot(now), now)
        if res.signal is None:
            log.write("SKIP", ts=now, reason=res.reason)
            continue
        sig = res.signal
        payload = asdict(sig)
        payload.pop("ts", None)
        log.write("SIGNAL", ts=now, **payload)

        # 4. risk gate + sizing
        acct = AccountState(equity=equity, balance=equity, open_positions=len(backend.open_trades()))
        dec = risk.evaluate(sig, acct, day, now)
        if not dec.approved:
            log.write("REJECT", ts=now, reason=dec.reason, signal_id=sig.signal_id)
            continue

        # 5. submit (fills next tick)
        trade_id = f"{sig.signal_id}-t{day.trades_opened + 1}"
        backend.submit(
            OrderRequest(
                instrument=sig.instrument,
                units=dec.units,
                stop=sig.stop,
                tp=sig.tp,
                signal_id=sig.signal_id,
                trade_id=trade_id,
                strategy_version=sig.strategy_version,
            )
        )
        day.trades_opened += 1
        log.write("SUBMIT", ts=now, reason="approved", trade_id=trade_id, signal_id=sig.signal_id, units=dec.units, stop=sig.stop, tp=sig.tp, risk_amount=dec.risk_amount)

    if now is not None:
        log.write("RUN_END", ts=now, reason="run_end", final_equity=equity)
    return equity
