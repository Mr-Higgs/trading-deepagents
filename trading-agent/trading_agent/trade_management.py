"""In-trade management: stop moves (breakeven -> trail), scale-out partials, and the WFX signal-based early exit.

Deterministic. Runs at the engine seam (after `reconcile` settles this bar's exits, before the entry gates)
for BOTH backends. Stop moves via `ExecutionBackend.modify_stop` never loosen a stop (only-improve =>
idempotent); trailing engages only once breakeven is reached. Scale-out banks fractions at Ichimoku levels.
The signal exit (`signal_exit`) closes a position via `ExecutionBackend.request_close` on a Kijun / Tenkan-Kijun
cross against it. Both are config-gated (`risk.management.breakeven_enabled` / `strategy.exit_on_kijun_cross` off => no-op).
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from trading_agent.config import AppConfig, ManagementConfig, PairConfig, ScaleOutConfig
from trading_agent.eventlog import EventLog
from trading_agent.execution.base import ExecutionBackend
from trading_agent.indicators import ichimoku
from trading_agent.strategy import signals
from trading_agent.types import Candle, OpenTrade, TradeEvent

_LEVEL_COL = {"kijun": "kijun", "tenkan": "tenkan", "span_a": "senkou_a", "span_b": "senkou_b"}


def _num(v) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def _level_price(level: str, ichi_row) -> float | None:
    col = _LEVEL_COL.get(level)
    if col is None:
        return None
    return _num(ichi_row.get(col))


def _breakeven_reached(bar: Candle, entry: float, pip: float, mgmt: ManagementConfig, risk: float, long: bool) -> bool:
    """True when the bar's favorable extreme has run far enough to arm breakeven."""
    if mgmt.breakeven_trigger_pips > 0:
        trigger = entry + mgmt.breakeven_trigger_pips * pip if long else entry - mgmt.breakeven_trigger_pips * pip
        return bar.h >= trigger if long else bar.l <= trigger
    trigger = entry + mgmt.breakeven_trigger_r * risk if long else entry - mgmt.breakeven_trigger_r * risk
    return bar.h >= trigger if long else bar.l <= trigger


def compute_managed_stop(
    pos: OpenTrade,
    bar: Candle,
    pip: float,
    mgmt: ManagementConfig,
    kijun: float | None,
    *,
    trail_mode: str | None = None,
) -> tuple[float, str] | None:
    """New (stop, reason) if the stop should move this bar, else None. reason in {breakeven, trail}."""
    risk = abs(pos.entry - pos.init_stop)
    if risk <= 0:
        return None
    long = pos.remaining_units > 0 if pos.remaining_units else pos.units > 0
    at_be = pos.stop >= pos.entry if long else pos.stop <= pos.entry
    new = pos.stop
    reason = "trail"
    effective_trail = trail_mode if trail_mode is not None else mgmt.trail_mode

    # 1. breakeven once price runs the trigger in favor (pip-based or R-multiple)
    if not at_be and _breakeven_reached(bar, pos.entry, pip, mgmt, risk, long):
        new = pos.entry + mgmt.breakeven_buffer_pips * pip if long else pos.entry - mgmt.breakeven_buffer_pips * pip
        reason = "breakeven"
        at_be = True

    # 2. trail once at/after breakeven
    if at_be and effective_trail != "off":
        trail: float | None = None
        if effective_trail == "kijun" and kijun is not None:
            # dedicated trail gap so the Kijun trail can breathe independently of the tight breakeven buffer
            trail_gap = (mgmt.trail_buffer_pips if mgmt.trail_buffer_pips > 0 else mgmt.breakeven_buffer_pips) * pip
            trail = kijun - trail_gap if long else kijun + trail_gap
        elif effective_trail == "pips":
            trail = bar.h - mgmt.trail_distance_pips * pip if long else bar.l + mgmt.trail_distance_pips * pip
        if trail is not None:
            better = max(new, trail) if long else min(new, trail)
            if better != new:
                new = better
                if reason != "breakeven":
                    reason = "trail"

    improved = new > pos.stop if long else new < pos.stop
    return (new, reason) if improved else None


def _leg_reached(bar: Candle, level: float, long: bool) -> bool:
    return bar.h >= level if long else bar.l <= level


def _in_profit_level(entry: float, level: float, long: bool) -> bool:
    return level > entry if long else level < entry


def _scale_out_events(
    backend: ExecutionBackend,
    pos: OpenTrade,
    bar: Candle,
    scale: ScaleOutConfig,
    ichi_row,
    pair: PairConfig,
    now: datetime,
    mgmt: ManagementConfig,
    log: EventLog,
) -> list[TradeEvent]:
    """Bank the next scale-out leg if price reached an in-profit Ichimoku level."""
    events: list[TradeEvent] = []
    if not scale.enabled or not scale.legs or pos.legs_filled >= len(scale.legs):
        return events
    long = pos.remaining_units > 0 if pos.remaining_units else pos.units > 0
    leg = scale.legs[pos.legs_filled]
    level = _level_price(leg.level, ichi_row)
    if level is None or not _in_profit_level(pos.entry, level, long) or not _leg_reached(bar, level, long):
        return events

    orig_mag = abs(pos.units)
    close_mag = round(leg.fraction * orig_mag)
    if close_mag < pair.min_units:
        return events  # fold into next leg on a later bar when combined size qualifies
    remaining_mag = abs(pos.remaining_units if pos.remaining_units else pos.units)
    close_mag = min(close_mag, remaining_mag)
    if close_mag < pair.min_units:
        return events

    ev = backend.reduce_position(pos.trade_id, close_mag, level, now)
    if ev is None:
        return events
    events.append(ev)

    if scale.move_be_after_first and pos.legs_filled == 0:
        be_stop = pos.entry + mgmt.breakeven_buffer_pips * pair.pip_size if long else pos.entry - mgmt.breakeven_buffer_pips * pair.pip_size
        backend.modify_stop(pos.trade_id, be_stop)
        log.write("STOP_MODIFIED", ts=now, reason="breakeven", trade_id=pos.trade_id, old_stop=pos.stop, new_stop=be_stop, entry=pos.entry)

    return events


def manage_open_trades(
    backend: ExecutionBackend,
    bar: Candle,
    snapshot: dict[str, pd.DataFrame],
    now: datetime,
    cfg: AppConfig,
    log: EventLog,
) -> list[TradeEvent]:
    """Move stops and scale-out partials on open positions; return partial EXIT events for P/L accounting."""
    mgmt = cfg.risk.management
    if not mgmt.breakeven_enabled and not mgmt.scale_out.enabled:
        return []
    positions = backend.open_positions()
    if not positions:
        return []

    pip = cfg.pair.pip_size
    events: list[TradeEvent] = []

    kijun: float | None = None
    ichi_row = None
    frame = snapshot.get(cfg.strategy.entry_tf)
    p = cfg.strategy.ichimoku
    if frame is not None and len(frame) >= p.min_rows:
        ich = ichimoku(frame, tenkan=p.tenkan, kijun=p.kijun, senkou_b=p.senkou_b, displacement=p.displacement)
        ichi_row = ich.iloc[-1]
        k = ichi_row["kijun"]
        kijun = None if pd.isna(k) else float(k)

    for pos in positions:
        if mgmt.scale_out.enabled and ichi_row is not None:
            events.extend(_scale_out_events(backend, pos, bar, mgmt.scale_out, ichi_row, cfg.pair, now, mgmt, log))

    if not mgmt.breakeven_enabled:
        return events

    for pos in backend.open_positions():  # refresh after partials so legs_filled / stops are current
        runner = mgmt.scale_out.enabled and pos.legs_filled > 0
        trail_mode = mgmt.scale_out.runner_trail if runner else mgmt.trail_mode
        result = compute_managed_stop(pos, bar, pip, mgmt, kijun, trail_mode=trail_mode)
        if result is None:
            continue
        new_stop, reason = result
        backend.modify_stop(pos.trade_id, new_stop)
        log.write("STOP_MODIFIED", ts=now, reason=reason, trade_id=pos.trade_id, old_stop=pos.stop, new_stop=new_stop, entry=pos.entry)

    return events


def signal_exit(backend: ExecutionBackend, snapshot: dict[str, pd.DataFrame], now: datetime, cfg: AppConfig, log: EventLog) -> None:
    """WFX early exit: close any open position whose Kijun/Tenkan-Kijun turns against it (config-gated)."""
    if not cfg.strategy.exit_on_kijun_cross:
        return
    positions = backend.open_positions()
    if not positions:
        return
    frame = snapshot.get(cfg.strategy.entry_tf)
    p = cfg.strategy.ichimoku
    if frame is None or len(frame) < p.min_rows:
        return
    ich = ichimoku(frame, tenkan=p.tenkan, kijun=p.kijun, senkou_b=p.senkou_b, displacement=p.displacement)
    for pos in positions:
        direction = "LONG" if (pos.remaining_units or pos.units) > 0 else "SHORT"
        if signals.exit_cross(frame, ich, direction):
            backend.request_close(pos.trade_id)
            log.write("CLOSE_SIGNAL", ts=now, reason="kijun_cross", trade_id=pos.trade_id, direction=direction)


def confluence_exit(backend: ExecutionBackend, snapshot: dict[str, pd.DataFrame], now: datetime, cfg: AppConfig, log: EventLog) -> None:
    """Confluence-flip exit: close a position when the top-down multi-TF score turns against it (config-gated).

    Cuts a losing trade early - when the weighted H4/H1/M15 confluence weakens past the threshold - instead of
    riding it to the full ATR stop. LONG exits when score <= threshold; SHORT exits when score >= -threshold
    (threshold 0 => exit on a flip to the opposite/neutral sign). Uses the SAME score as the entry
    (signals.topdown_confluence), so a position is held only while the picture that opened it still holds.
    """
    if not cfg.strategy.exit_on_confluence_flip:
        return
    positions = backend.open_positions()
    if not positions:
        return
    conf = signals.topdown_confluence(snapshot, cfg.strategy)
    if conf is None:
        return
    score, _, _ = conf
    thr = cfg.strategy.exit_confluence_threshold
    for pos in positions:
        long = (pos.remaining_units or pos.units) > 0
        if score <= thr if long else score >= -thr:
            backend.request_close(pos.trade_id)
            log.write("CLOSE_SIGNAL", ts=now, reason="confluence_flip", trade_id=pos.trade_id, direction="LONG" if long else "SHORT", score=score)


def _demo() -> None:
    from datetime import timezone

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def bar(h: float, low: float) -> Candle:
        return Candle("AUD_USD", "M15", t0, t0, low, h, low, (h + low) / 2, 100, complete=True)

    pip = 0.0001
    mgmt = ManagementConfig(breakeven_enabled=True, breakeven_trigger_pips=10.0, breakeven_buffer_pips=0.0, trail_mode="kijun")
    long = OpenTrade("t1", "AUD_USD", 1000, entry=1.0000, stop=0.9950, init_stop=0.9950, tp=1.0100, remaining_units=1000)

    assert compute_managed_stop(long, bar(1.0005, 0.9990), pip, mgmt, None) is None  # +5 pips: no move yet
    moved = compute_managed_stop(long, bar(1.0015, 0.9990), pip, mgmt, None)  # +15 pips hit
    assert moved is not None and abs(moved[0] - 1.0000) < 1e-9 and moved[1] == "breakeven", moved

    be = OpenTrade("t1", "AUD_USD", 1000, entry=1.0000, stop=1.0000, init_stop=0.9950, tp=1.0100, remaining_units=1000)
    trailed = compute_managed_stop(be, bar(1.0060, 1.0020), pip, mgmt, kijun=1.0030)  # trail to kijun-buffer
    assert trailed is not None and abs(trailed[0] - 1.0030) < 1e-9 and trailed[1] == "trail", trailed
    assert compute_managed_stop(be, bar(1.0060, 1.0020), pip, mgmt, kijun=1.0000) is None  # kijun below stop: never loosen

    # trail_buffer_pips decouples the Kijun gap from the (tight) breakeven buffer: 10 pips below kijun 1.0050
    loose = ManagementConfig(breakeven_enabled=True, breakeven_buffer_pips=0.0, trail_mode="kijun", trail_buffer_pips=10.0)
    wide = compute_managed_stop(be, bar(1.0070, 1.0040), pip, loose, kijun=1.0050)
    assert wide is not None and abs(wide[0] - 1.0040) < 1e-9, wide  # 1.0050 - 10 pips = 1.0040, not 1.0050

    short = OpenTrade("s1", "AUD_USD", -1000, entry=1.0000, stop=1.0050, init_stop=1.0050, tp=0.9900, remaining_units=-1000)
    sm = compute_managed_stop(short, bar(1.0010, 0.9985), pip, mgmt, None)  # -15 pips hit
    assert sm is not None and abs(sm[0] - 1.0000) < 1e-9 and sm[1] == "breakeven", sm
    print("trade_management self-check OK")


if __name__ == "__main__":
    _demo()
