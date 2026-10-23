"""Risk-adjusted performance statistics on a realized-equity curve (pure, no pandas required).

Daily returns are formed from realized P/L (exits, partials, financing) bucketed by UTC calendar day,
with flat (zero-return) weekdays filled in between the first and last day so idle days count.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta

TRADING_DAYS = 252


def daily_returns(events: Iterable[tuple[datetime, float]], starting_equity: float) -> list[float]:
    """Simple daily returns from (timestamp, pnl) realizations."""
    by_day: dict[date, float] = defaultdict(float)
    for ts, pnl in events:
        by_day[ts.date()] += pnl
    if not by_day:
        return []
    first, last = min(by_day), max(by_day)
    equity, out = starting_equity, []
    d = first
    while d <= last:
        if d.weekday() < 5 or d in by_day:  # noqa: PLR2004 - Mon..Fri
            pnl = by_day.get(d, 0.0)
            out.append(pnl / equity if equity > 0 else 0.0)
            equity += pnl
        d += timedelta(days=1)
    return out


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: Sequence[float]) -> float:
    if len(xs) < 2:  # noqa: PLR2004
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def sharpe(returns: Sequence[float], periods: int = TRADING_DAYS) -> float:
    """Annualized Sharpe (zero risk-free rate); 0 when undefined."""
    sd = _std(returns)
    return _mean(returns) / sd * math.sqrt(periods) if sd > 0 else 0.0


def sortino(returns: Sequence[float], periods: int = TRADING_DAYS) -> float:
    """Annualized Sortino using downside deviation vs 0 over ALL periods; 0 when undefined."""
    if not returns:
        return 0.0
    dd = math.sqrt(sum(min(r, 0.0) ** 2 for r in returns) / len(returns))
    return _mean(returns) / dd * math.sqrt(periods) if dd > 0 else 0.0


def max_drawdown_pct(returns: Sequence[float]) -> float:
    """Peak-to-trough drawdown of the compounded curve, as a positive fraction."""
    eq = peak = 1.0
    worst = 0.0
    for r in returns:
        eq *= 1.0 + r
        peak = max(peak, eq)
        worst = max(worst, (peak - eq) / peak)
    return worst


def cagr(returns: Sequence[float], periods: int = TRADING_DAYS) -> float:
    if not returns:
        return 0.0
    growth = math.prod(1.0 + r for r in returns)
    years = len(returns) / periods
    return growth ** (1.0 / years) - 1.0 if growth > 0 else -1.0


def mar(returns: Sequence[float], periods: int = TRADING_DAYS) -> float:
    """MAR ratio: CAGR / max drawdown (0 when there is no drawdown)."""
    dd = max_drawdown_pct(returns)
    return cagr(returns, periods) / dd if dd > 0 else 0.0
