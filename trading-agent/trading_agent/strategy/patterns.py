"""Candlestick pattern detectors - pure OHLC, no pandas.

Reference: docs/trading-system/"What are Candlesticks.pdf" + the Ichimoku doc's confirmation section
(engulfing / hammer / shooting star / pin bar). Each returns a bool; `matches` dispatches a set of
pattern names for a trade direction ("any pattern in the set fires").

ponytail: the source doc's *pin bar* wording is inverted vs standard TA (it calls a long-UPPER-shadow
candle a "bullish pin bar"). A long lower shadow = buyers rejecting lows = bullish (a hammer); a long
upper shadow = sellers rejecting highs = bearish (a shooting star). We use the standard/correct meaning
and treat "pin_bar" as an alias: hammer for LONG, shooting star for SHORT. Thresholds are fractions of
the candle range - tune in one place if signals are too loose/tight.
"""

from __future__ import annotations

# wick/body thresholds as a fraction of the full candle range (h - l)
_LONG_WICK = 0.5
_SMALL_WICK = 0.15
_SMALL_BODY = 0.35
_DOJI_BODY = 0.10


def bull_engulf(po: float, pc: float, co: float, cc: float) -> bool:
    """Current candle is bullish and its body engulfs a prior bearish body."""
    return cc > co and pc < po and cc >= po and co <= pc


def bear_engulf(po: float, pc: float, co: float, cc: float) -> bool:
    return cc < co and pc > po and cc <= po and co >= pc


def hammer(o: float, h: float, low: float, c: float) -> bool:
    """Small body, long lower shadow, negligible upper shadow -> bullish reversal."""
    rng = h - low
    if rng <= 0:
        return False
    body = abs(c - o)
    lower = min(o, c) - low
    upper = h - max(o, c)
    return body <= _SMALL_BODY * rng and lower >= _LONG_WICK * rng and upper <= _SMALL_WICK * rng


def shooting_star(o: float, h: float, low: float, c: float) -> bool:
    """Small body, long upper shadow, negligible lower shadow -> bearish reversal."""
    rng = h - low
    if rng <= 0:
        return False
    body = abs(c - o)
    lower = min(o, c) - low
    upper = h - max(o, c)
    return body <= _SMALL_BODY * rng and upper >= _LONG_WICK * rng and lower <= _SMALL_WICK * rng


def doji(o: float, h: float, low: float, c: float) -> bool:
    """Tiny body relative to range -> indecision (detection only; never a directional trigger)."""
    rng = h - low
    return rng > 0 and abs(c - o) <= _DOJI_BODY * rng


# names accepted as directional entry triggers (doji is neutral -> excluded)
TRIGGER_NAMES = ("engulfing", "hammer", "shooting_star", "pin_bar")


def matches(names, direction: str, prev: tuple, curr: tuple) -> bool:
    """True if any pattern in `names` fires for `direction`. prev/curr are (o, h, l, c) tuples.

    `names` accepts a single pattern string or an iterable of them.
    """
    if isinstance(names, str):
        names = [names]
    po, _ph, _pl, pc = prev
    o, h, low, c = curr
    long = direction == "LONG"
    for n in names:
        if n == "engulfing" and (bull_engulf(po, pc, o, c) if long else bear_engulf(po, pc, o, c)):
            return True
        if n in ("hammer", "pin_bar") and long and hammer(o, h, low, c):
            return True
        if n in ("shooting_star", "pin_bar") and not long and shooting_star(o, h, low, c):
            return True
    return False


def _demo() -> None:
    hammer_c = (10.0, 10.25, 9.5, 10.2)  # long lower wick
    star_c = (10.0, 10.5, 9.75, 9.8)  # long upper wick
    doji_c = (10.0, 10.3, 9.7, 10.01)  # tiny body
    assert hammer(*hammer_c) and not shooting_star(*hammer_c)
    assert shooting_star(*star_c) and not hammer(*star_c)
    assert doji(*doji_c) and not hammer(*doji_c) and not shooting_star(*doji_c)
    assert bull_engulf(10.0, 9.8, 9.7, 10.1) and not bear_engulf(10.0, 9.8, 9.7, 10.1)
    assert bear_engulf(9.8, 10.0, 10.1, 9.7)
    # dispatcher: right pattern for the right direction only
    prev_bear = (10.0, 10.05, 9.75, 9.8)
    assert matches(["engulfing"], "LONG", prev_bear, (9.7, 10.15, 9.65, 10.1))
    assert matches(["hammer", "pin_bar"], "LONG", prev_bear, hammer_c)
    assert not matches(["hammer"], "SHORT", prev_bear, hammer_c)  # hammer is not a short trigger
    assert matches(["pin_bar"], "SHORT", prev_bear, star_c)
    print("patterns self-check OK")


if __name__ == "__main__":
    _demo()
