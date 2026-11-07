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
from trading_agent.sessions import any_open, in_news_window
from trading_agent.strategy.base import Strategy
from trading_agent.trade_management import manage_open_trades, signal_exit
from trading_agent.types import AccountState, DayState, OrderRequest, TradeEvent


def _apply_exit(
    ev: TradeEvent,
    equity: float,
    day: DayState,
    cfg: AppConfig,
    log: EventLog,
    now,
) -> float:
    """Fold an EXIT (full or partial) into equity, day-state, and the event log."""
    if ev.kind != "EXIT" or ev.pnl is None:
        return equity
    log_type = "PARTIAL_EXIT" if ev.partial else "EXIT"
    log.write(
        log_type,
        ts=now,
        reason=ev.exit_reason or "exit",
        trade_id=ev.trade_id,
        price=ev.price,
        units=ev.units,
        pnl=ev.pnl,
        exit_reason=ev.exit_reason,
        remaining_units=ev.remaining_units,
    )
    equity += ev.pnl
    was_halted = day.halted
    update_day_state(day, ev, equity, cfg.risk.max_daily_loss_pct)
    if day.halted and not was_halted:
        log.write("HALT", ts=now, reason=day.halt_reason)
    return equity


def _apply_financing(ev: TradeEvent, equity: float, log: EventLog, now) -> float:
    """Swap accrues into equity (and the log) but not the day's realized trading P/L."""
    if ev.pnl is None:
        return equity
    log.write("FINANCING", ts=now, reason="rollover", trade_id=ev.trade_id, units=ev.units, pnl=ev.pnl, rollover=ev.ts.isoformat())
    return equity + ev.pnl


def _margin_used(backend: ExecutionBackend, risk: RiskEngine, now) -> float:
    if not risk.risk.leverage or not hasattr(backend, "open_positions"):
        return 0.0
    return sum(abs(p.remaining_units or p.units) * risk.margin_per_unit(p.entry, now) for p in backend.open_positions())


def _open_risk(backend: ExecutionBackend, risk: RiskEngine, now) -> float:
    """Account-ccy loss if every open position were stopped at its current stop (0 for stops past entry)."""
    if not hasattr(backend, "open_positions"):
        return 0.0
    total = 0.0
    for p in backend.open_positions():
        size = p.remaining_units or p.units
        per_unit = (p.entry - p.stop) if size > 0 else (p.stop - p.entry)
        total += max(per_unit, 0.0) * abs(size)
    return total * risk.quote_to_account(now) if total else 0.0


def _signal_rr(sig) -> float:
    risk = abs(sig.entry_ref - sig.stop)
    if risk <= 0:
        return 0.0
    return abs(sig.tp - sig.entry_ref) / risk


def run(
    source: MarketDataSource,
    strategy: Strategy,
    risk: RiskEngine,
    backend: ExecutionBackend,
    log: EventLog,
    cfg: AppConfig,
    starting_equity: float = 10_000.0,
    *,
    liquidate_at_end: bool = False,
) -> float:
    """Drive the loop to exhaustion of `source.stream()`; returns final equity.

    `liquidate_at_end` (backtests) closes positions still open at the last bar so final equity includes them.
    """
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
            elif ev.kind == "FINANCING":
                equity = _apply_financing(ev, equity, log, now)
            else:  # EXIT
                equity = _apply_exit(ev, equity, day, cfg, log, now)

        # snapshot once per bar (reused by management + strategy)
        snapshot = source.snapshot(now)

        # 1b. manage open trades (scale-out partials, stop moves, then WFX signal exit) - unconditional, above the gates
        for partial in manage_open_trades(backend, bar, snapshot, now, cfg, log):
            equity = _apply_exit(partial, equity, day, cfg, log, now)
        signal_exit(backend, snapshot, now, cfg, log)

        # 2. gates (each blocked entry logged with its reason)
        if day.halted:
            log.write("SKIP", ts=now, reason=day.halt_reason or "day_halted")
            continue
        if not any_open(now, cfg.sessions):
            log.write("SKIP", ts=now, reason="session_closed")
            continue
        if in_news_window(now, cfg.risk.news_windows):
            log.write("SKIP", ts=now, reason="news_window")
            continue

        # 3. strategy proposal
        res = strategy.evaluate(snapshot, now)
        if res.signal is None:
            log.write("SKIP", ts=now, reason=res.reason)
            continue
        sig = res.signal
        payload = asdict(sig)
        payload.pop("ts", None)
        log.write("SIGNAL", ts=now, **payload)

        # 4. risk gate + sizing
        acct = AccountState(equity=equity, balance=equity, open_positions=len(backend.open_trades()), margin_used=_margin_used(backend, risk, now), open_risk=_open_risk(backend, risk, now))
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
        log.write(
            "SUBMIT",
            ts=now,
            reason="approved",
            trade_id=trade_id,
            signal_id=sig.signal_id,
            units=dec.units,
            stop=sig.stop,
            tp=sig.tp,
            risk_amount=dec.risk_amount,
            rr=round(_signal_rr(sig), 4),
        )

    if now is not None and liquidate_at_end and hasattr(backend, "liquidate"):
        for ev in backend.liquidate():
            equity = _apply_exit(ev, equity, day, cfg, log, now)
    if now is not None:
        log.write("RUN_END", ts=now, reason="run_end", final_equity=equity)
    return equity
