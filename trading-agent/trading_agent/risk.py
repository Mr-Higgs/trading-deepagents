"""The risk engine: the deterministic gate between a signal proposal and a real order.

Signals are proposals; nothing trades until `RiskEngine.evaluate` approves and sizes it. This is
money logic - every rejection carries the rule name that fired, and the day's kill/loss state lives
in `DayState` (rebuilt from the event log at startup).
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone

from trading_agent.config import PairConfig, RiskConfig
from trading_agent.fx_convert import FxConverter
from trading_agent.types import AccountState, DayState, RiskDecision, Signal, TradeEvent


def trading_day(now: datetime, reset: time) -> date:
    """Trading day for a UTC instant, labeled by the date its session STARTED.

    A trading day runs from `reset` (UTC) to `reset` the next day (broker rollover, e.g. 21:00 UTC),
    so it does NOT align to calendar midnight. Used to key `DayState` and detect day rollover.
    """
    now_utc = now.astimezone(timezone.utc)
    return now_utc.date() if now_utc.time() >= reset else now_utc.date() - timedelta(days=1)


def update_day_state(day: DayState, ev: TradeEvent, equity: float, max_daily_loss_pct: float) -> DayState:
    """Fold a trade EXIT into the day's realized P/L and trip the halt if the loss cap is breached.

    Mutates and returns `day`. FILL events (no realized P/L yet) are ignored here.
    """
    if ev.kind == "EXIT" and ev.pnl is not None:
        day.realized_pnl += ev.pnl
        if not day.halted and day.realized_pnl <= -abs(max_daily_loss_pct) * equity:
            day.halted = True
            day.halt_reason = "daily_loss_breached"
    return day


class RiskEngine:
    """Approves/sizes/rejects signals against the hard risk rules."""

    def __init__(self, risk: RiskConfig, pair: PairConfig, *, fx: FxConverter | None = None) -> None:
        """Args:
        risk: Hard risk rules.
        pair: Instrument being traded.
        fx: Converts quote/base currency into the account currency for sizing and margin. Without
            it, currencies that differ from the account currency raise `FxRateUnavailable` at sizing time.
        """
        self.risk = risk
        self.pair = pair
        self.fx = fx or FxConverter()

    def quote_to_account(self, now: datetime) -> float:
        """Account-currency value of one unit of the pair's quote currency at `now`."""
        return self.fx.rate(self.pair.quote_ccy, self.risk.account_ccy, now)

    def margin_per_unit(self, price: float, now: datetime) -> float:
        """Account-currency margin for one unit (notional / leverage); 0 when the check is off."""
        if not self.risk.leverage:
            return 0.0
        return price * self.quote_to_account(now) / self.risk.leverage

    def _reject(self, sig: Signal, reason: str) -> RiskDecision:
        return RiskDecision(approved=False, units=0, risk_amount=0.0, reason=reason, signal=sig)

    def evaluate(self, sig: Signal, acct: AccountState, day: DayState, now: datetime) -> RiskDecision:
        r = self.risk
        # Rejection order matters: cheapest / highest-authority checks first.
        if r.kill_switch:
            return self._reject(sig, "kill_switch_on")
        if day.halted:
            return self._reject(sig, day.halt_reason or "day_halted")
        if day.trades_opened >= r.max_trades_per_day:
            return self._reject(sig, "max_trades_reached")
        if acct.open_positions >= r.max_concurrent_positions:  # v1.1: no correlated stacking of the same setup
            return self._reject(sig, "max_concurrent")
        if day.realized_pnl <= -abs(r.max_daily_loss_pct) * acct.equity:
            return self._reject(sig, "daily_loss_breached")

        stop_distance = abs(sig.entry_ref - sig.stop)
        if stop_distance <= 0:
            return self._reject(sig, "stop_distance_invalid")
        if stop_distance < r.min_stop_pips * self.pair.pip_size:  # v1.1: too-tight stop => oversized position + cost drag
            return self._reject(sig, "below_min_stop")

        reward = abs(sig.tp - sig.entry_ref)
        if sig.direction == "LONG":
            if not (sig.stop < sig.entry_ref < sig.tp):
                if sig.stop >= sig.entry_ref:
                    return self._reject(sig, "stop_wrong_side")
                return self._reject(sig, "tp_wrong_side")
        elif not (sig.tp < sig.entry_ref < sig.stop):
            if sig.stop <= sig.entry_ref:
                return self._reject(sig, "stop_wrong_side")
            return self._reject(sig, "tp_wrong_side")
        if reward < r.min_rr * stop_distance:
            return self._reject(sig, "rr_below_floor")

        risk_amount = acct.equity * r.risk_pct
        # Epsilon before floor absorbs float noise (0.6700-0.6680 is not exactly 0.002, which would
        # otherwise floor 50000 -> 49999). 1e-9 is far below one integer unit, so it only cancels
        # representation error. ponytail: switch to Decimal if sub-unit precision ever matters.
        units_mag = math.floor(risk_amount / (stop_distance * self.quote_to_account(now)) + 1e-9)
        if units_mag < self.pair.min_units:
            return self._reject(sig, "below_min_units")
        units_mag = min(units_mag, self.pair.max_units)
        per_unit = self.margin_per_unit(sig.entry_ref, now)
        if per_unit > 0:  # shrink to fit free margin rather than skip outright
            free = acct.equity * r.max_margin_usage - acct.margin_used
            units_mag = min(units_mag, math.floor(max(free, 0.0) / per_unit + 1e-9))
            if units_mag < self.pair.min_units:
                return self._reject(sig, "insufficient_margin")
        units = units_mag if sig.direction == "LONG" else -units_mag
        return RiskDecision(approved=True, units=units, risk_amount=risk_amount, reason="approved", signal=sig)
