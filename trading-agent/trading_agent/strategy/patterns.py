"""Candlestick pattern detectors - pure OHLC, no pandas.

Reference: docs/trading-system/"Candlesticks Patterns.new.pdf" (concept/formation) +
"What are Candlesticks.pdf" (body = momentum, wick = rejection) + "Price Action.pdf" (confluence /
wait-for-close). Each detector returns a bool; `matches` dispatches a set of pattern names for a
trade direction ("any pattern in the set fires"). `PATTERN_INFO` classifies every name (type / bias /
candle count) - reference data the LLM gate + price-action confluence scoring will read; it does not
change trade math here.

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
_LARGE_BODY = 0.6  # a "conviction" body (marubozu, three soldiers/crows)
_TWEEZER_TOL = 0.1  # matching high/low tolerance, as a fraction of the larger candle range


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


def marubozu(o: float, h: float, low: float, c: float) -> bool:
    """Large body with shaven (negligible) wicks both ends -> strong conviction/momentum."""
    rng = h - low
    if rng <= 0:
        return False
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - low
    return body >= _LARGE_BODY * rng and upper <= _SMALL_WICK * rng and lower <= _SMALL_WICK * rng


def spinning_top(o: float, h: float, low: float, c: float) -> bool:
    """Small (non-doji) body with meaningful wicks on both sides -> indecision (neutral)."""
    rng = h - low
    if rng <= 0:
        return False
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - low
    return _DOJI_BODY * rng < body <= _SMALL_BODY * rng and upper >= _SMALL_WICK * rng and lower >= _SMALL_WICK * rng


def long_legged_doji(o: float, h: float, low: float, c: float) -> bool:
    """Doji body with long wicks on both sides -> strong indecision (neutral)."""
    rng = h - low
    if rng <= 0:
        return False
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - low
    return body <= _DOJI_BODY * rng and upper >= _SMALL_WICK * rng and lower >= _SMALL_WICK * rng


def dragonfly_doji(o: float, h: float, low: float, c: float) -> bool:
    """Doji body at the top, long lower wick, negligible upper -> bullish reversal (hammer variant)."""
    rng = h - low
    if rng <= 0:
        return False
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - low
    return body <= _DOJI_BODY * rng and lower >= _LONG_WICK * rng and upper <= _SMALL_WICK * rng


def gravestone_doji(o: float, h: float, low: float, c: float) -> bool:
    """Doji body at the bottom, long upper wick, negligible lower -> bearish reversal (star variant)."""
    rng = h - low
    if rng <= 0:
        return False
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - low
    return body <= _DOJI_BODY * rng and upper >= _LONG_WICK * rng and lower <= _SMALL_WICK * rng


# --- two-candle patterns: prev/curr are (o, h, l, c) tuples ---


def piercing_line(prev: tuple, curr: tuple) -> bool:
    """Prev bearish, curr bullish closing above the midpoint of prev's body -> bullish reversal."""
    po, _ph, _pl, pc = prev
    co, _ch, _cl, cc = curr
    mid = (po + pc) / 2
    return pc < po and cc > co and co <= pc and mid < cc < po


def dark_cloud_cover(prev: tuple, curr: tuple) -> bool:
    """Prev bullish, curr bearish closing below the midpoint of prev's body -> bearish reversal."""
    po, _ph, _pl, pc = prev
    co, _ch, _cl, cc = curr
    mid = (po + pc) / 2
    return pc > po and cc < co and co >= pc and po < cc < mid


def bull_harami(prev: tuple, curr: tuple) -> bool:
    """Large prev bearish body containing a smaller curr bullish body -> bullish reversal (inside bar)."""
    po, _ph, _pl, pc = prev
    co, _ch, _cl, cc = curr
    return pc < po and cc > co and pc <= co and cc <= po and abs(cc - co) < abs(po - pc)


def bear_harami(prev: tuple, curr: tuple) -> bool:
    """Large prev bullish body containing a smaller curr bearish body -> bearish reversal (inside bar)."""
    po, _ph, _pl, pc = prev
    co, _ch, _cl, cc = curr
    return pc > po and cc < co and po <= cc and co <= pc and abs(co - cc) < abs(pc - po)


def tweezer_bottom(prev: tuple, curr: tuple) -> bool:
    """Prev bearish then curr bullish sharing (near-)equal lows -> bullish reversal."""
    po, ph, pl, pc = prev
    co, ch, cl, cc = curr
    tol = _TWEEZER_TOL * max(ph - pl, ch - cl)
    return pc < po and cc > co and tol > 0 and abs(pl - cl) <= tol


def tweezer_top(prev: tuple, curr: tuple) -> bool:
    """Prev bullish then curr bearish sharing (near-)equal highs -> bearish reversal."""
    po, ph, pl, pc = prev
    co, ch, cl, cc = curr
    tol = _TWEEZER_TOL * max(ph - pl, ch - cl)
    return pc > po and cc < co and tol > 0 and abs(ph - ch) <= tol


# --- three-candle patterns: c1/c2/c3 are (o, h, l, c) tuples, oldest first ---


def morning_star(c1: tuple, c2: tuple, c3: tuple) -> bool:
    """Bearish, small-body, bullish sequence closing back into the first body -> bullish reversal."""
    o1, _h1, _l1, cc1 = c1
    o2, h2, l2, cc2 = c2
    o3, _h3, _l3, cc3 = c3
    rng2 = h2 - l2
    small2 = rng2 > 0 and abs(cc2 - o2) <= _SMALL_BODY * rng2
    return cc1 < o1 and small2 and cc3 > o3 and cc3 > (o1 + cc1) / 2


def evening_star(c1: tuple, c2: tuple, c3: tuple) -> bool:
    """Bullish, small-body, bearish sequence closing back into the first body -> bearish reversal."""
    o1, _h1, _l1, cc1 = c1
    o2, h2, l2, cc2 = c2
    o3, _h3, _l3, cc3 = c3
    rng2 = h2 - l2
    small2 = rng2 > 0 and abs(cc2 - o2) <= _SMALL_BODY * rng2
    return cc1 > o1 and small2 and cc3 < o3 and cc3 < (o1 + cc1) / 2


def _three_trend(c1: tuple, c2: tuple, c3: tuple, bull: bool) -> bool:
    """Three consecutive large bodies, same colour, progressing (soldiers up / crows down)."""
    for o, h, low, c in (c1, c2, c3):
        rng = h - low
        if rng <= 0 or abs(c - o) < _LARGE_BODY * rng or (c > o) != bull:
            return False
    o1, _1h, _1l, cc1 = c1
    o2, _2h, _2l, cc2 = c2
    o3, _3h, _3l, cc3 = c3
    if bull:  # each opens within the prior real body, closes make higher highs
        return cc1 < cc2 < cc3 and o1 <= o2 <= cc1 and o2 <= o3 <= cc2
    return cc1 > cc2 > cc3 and cc1 <= o2 <= o1 and cc2 <= o3 <= o2


def three_white_soldiers(c1: tuple, c2: tuple, c3: tuple) -> bool:
    """Three strong bullish candles with rising closes -> bullish reversal."""
    return _three_trend(c1, c2, c3, bull=True)


def three_black_crows(c1: tuple, c2: tuple, c3: tuple) -> bool:
    """Three strong bearish candles with falling closes -> bearish reversal."""
    return _three_trend(c1, c2, c3, bull=False)


# names `matches` recognizes in `m15_trigger` (single source of truth; config_graph imports this).
# The three neutral tokens are accepted but never fire a directional trigger (detection-only).
TRIGGER_NAMES = (
    "engulfing",
    "hammer",
    "shooting_star",
    "pin_bar",
    "marubozu",
    "dragonfly_doji",
    "gravestone_doji",
    "piercing_line",
    "dark_cloud_cover",
    "harami",
    "tweezer",
    "star",
    "three_white_soldiers",
    "three_black_crows",
    "doji",
    "spinning_top",
    "long_legged_doji",
)

# name -> (type, bias, candle count) for downstream reasoning (LLM gate / price-action confluence).
# type: "reversal" | "continuation" | "indecision"; bias: "bull" | "bear" | "both" | "neutral".
PATTERN_INFO: dict[str, tuple[str, str, int]] = {
    "engulfing": ("reversal", "both", 2),
    "hammer": ("reversal", "bull", 1),
    "shooting_star": ("reversal", "bear", 1),
    "pin_bar": ("reversal", "both", 1),
    "marubozu": ("continuation", "both", 1),
    "dragonfly_doji": ("reversal", "bull", 1),
    "gravestone_doji": ("reversal", "bear", 1),
    "piercing_line": ("reversal", "bull", 2),
    "dark_cloud_cover": ("reversal", "bear", 2),
    "harami": ("reversal", "both", 2),
    "tweezer": ("reversal", "both", 2),
    "star": ("reversal", "both", 3),
    "three_white_soldiers": ("reversal", "bull", 3),
    "three_black_crows": ("reversal", "bear", 3),
    "doji": ("indecision", "neutral", 1),
    "spinning_top": ("indecision", "neutral", 1),
    "long_legged_doji": ("indecision", "neutral", 1),
}


def matches(names, direction: str, prev: tuple, curr: tuple, prev2: tuple | None = None) -> bool:
    """True if any pattern in `names` fires for `direction`. prev/curr/prev2 are (o, h, l, c) tuples.

    `names` accepts a single pattern string or an iterable of them. `prev2` is the candle *before*
    `prev` (oldest of three); 3-candle patterns (star / three_white_soldiers / three_black_crows) are
    skipped when it is None. Neutral tokens (doji / spinning_top / long_legged_doji) never fire.
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
        if n == "marubozu" and marubozu(o, h, low, c) and (c > o) == long:
            return True
        if n == "dragonfly_doji" and long and dragonfly_doji(o, h, low, c):
            return True
        if n == "gravestone_doji" and not long and gravestone_doji(o, h, low, c):
            return True
        if n == "piercing_line" and long and piercing_line(prev, curr):
            return True
        if n == "dark_cloud_cover" and not long and dark_cloud_cover(prev, curr):
            return True
        if n == "harami" and (bull_harami(prev, curr) if long else bear_harami(prev, curr)):
            return True
        if n == "tweezer" and (tweezer_bottom(prev, curr) if long else tweezer_top(prev, curr)):
            return True
        if prev2 is not None:
            if n == "star" and (morning_star(prev2, prev, curr) if long else evening_star(prev2, prev, curr)):
                return True
            if n == "three_white_soldiers" and long and three_white_soldiers(prev2, prev, curr):
                return True
            if n == "three_black_crows" and not long and three_black_crows(prev2, prev, curr):
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

    # single-candle additions
    maru_c = (10.0, 10.95, 9.98, 10.9)  # big body, shaven -> bullish marubozu
    spin_c = (10.0, 10.5, 9.5, 10.15)  # small body, wicks both sides -> indecision
    long_doji_c = (10.0, 10.5, 9.5, 10.02)  # doji body, long wicks both sides
    dragon_c = (10.0, 10.05, 9.5, 10.02)  # doji body, long lower wick
    grave_c = (10.0, 10.5, 9.95, 9.98)  # doji body, long upper wick
    assert marubozu(*maru_c) and maru_c[3] > maru_c[0]
    assert spinning_top(*spin_c) and not doji(*spin_c) and not marubozu(*spin_c)
    assert long_legged_doji(*long_doji_c) and doji(*long_doji_c)
    assert dragonfly_doji(*dragon_c) and not gravestone_doji(*dragon_c)
    assert gravestone_doji(*grave_c) and not dragonfly_doji(*grave_c)

    # two-candle additions
    pl_prev, pl_curr = (10.0, 10.05, 9.4, 9.5), (9.45, 9.9, 9.4, 9.85)  # piercing line
    dcc_prev, dcc_curr = (9.5, 10.05, 9.45, 10.0), (10.05, 10.1, 9.55, 9.6)  # dark cloud cover
    bh_prev, bh_curr = (10.5, 10.6, 9.9, 10.0), (10.1, 10.2, 10.05, 10.3)  # bull harami
    brh_prev, brh_curr = (10.0, 10.6, 9.9, 10.5), (10.4, 10.45, 10.15, 10.2)  # bear harami
    tb_prev, tb_curr = (10.2, 10.25, 9.9, 10.0), (10.0, 10.3, 9.91, 10.25)  # tweezer bottom
    tt_prev, tt_curr = (10.0, 10.5, 9.95, 10.3), (10.3, 10.51, 10.0, 10.05)  # tweezer top
    assert piercing_line(pl_prev, pl_curr) and not dark_cloud_cover(pl_prev, pl_curr)
    assert dark_cloud_cover(dcc_prev, dcc_curr) and not piercing_line(dcc_prev, dcc_curr)
    assert bull_harami(bh_prev, bh_curr) and not bear_harami(bh_prev, bh_curr)
    assert bear_harami(brh_prev, brh_curr) and not bull_harami(brh_prev, brh_curr)
    assert tweezer_bottom(tb_prev, tb_curr) and not tweezer_top(tb_prev, tb_curr)
    assert tweezer_top(tt_prev, tt_curr) and not tweezer_bottom(tt_prev, tt_curr)

    # three-candle additions
    ms = ((10.5, 10.55, 9.9, 10.0), (9.95, 10.05, 9.85, 9.98), (10.0, 10.6, 9.98, 10.4))  # morning star
    es = ((10.0, 10.55, 9.95, 10.5), (10.55, 10.65, 10.5, 10.58), (10.5, 10.55, 9.9, 10.0))  # evening star
    tws = ((10.0, 10.42, 9.98, 10.4), (10.2, 10.82, 10.18, 10.8), (10.6, 11.22, 10.58, 11.2))  # 3 white soldiers
    tbc = ((10.4, 10.42, 9.98, 10.0), (10.2, 10.22, 9.78, 9.8), (10.0, 10.02, 9.58, 9.6))  # 3 black crows
    assert morning_star(*ms) and not evening_star(*ms)
    assert evening_star(*es) and not morning_star(*es)
    assert three_white_soldiers(*tws) and not three_black_crows(*tws)
    assert three_black_crows(*tbc) and not three_white_soldiers(*tbc)

    # dispatcher: right pattern for the right direction only
    prev_bear = (10.0, 10.05, 9.75, 9.8)
    assert matches(["engulfing"], "LONG", prev_bear, (9.7, 10.15, 9.65, 10.1))
    assert matches(["hammer", "pin_bar"], "LONG", prev_bear, hammer_c)
    assert not matches(["hammer"], "SHORT", prev_bear, hammer_c)  # hammer is not a short trigger
    assert matches(["pin_bar"], "SHORT", prev_bear, star_c)
    assert matches("marubozu", "LONG", prev_bear, maru_c) and not matches("marubozu", "SHORT", prev_bear, maru_c)
    assert matches("piercing_line", "LONG", pl_prev, pl_curr) and not matches("piercing_line", "SHORT", pl_prev, pl_curr)
    assert matches("harami", "LONG", bh_prev, bh_curr) and not matches("harami", "SHORT", bh_prev, bh_curr)
    assert matches("tweezer", "LONG", tb_prev, tb_curr) and not matches("tweezer", "LONG", tt_prev, tt_curr)
    # 3-candle patterns need prev2 (the oldest candle); without it they no-op
    assert matches("star", "LONG", ms[1], ms[2], prev2=ms[0])
    assert not matches("star", "LONG", ms[1], ms[2])  # no prev2 -> skipped
    assert matches("three_white_soldiers", "LONG", tws[1], tws[2], prev2=tws[0])
    # neutral tokens never fire a trigger
    assert not matches("spinning_top", "LONG", prev_bear, spin_c)
    assert not matches("doji", "LONG", prev_bear, doji_c)
    print("patterns self-check OK")


if __name__ == "__main__":
    _demo()
