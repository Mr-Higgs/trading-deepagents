"""Ichimoku alert/signal taxonomy (docs/trading-system/Ichimoku.pdf).

Computed from a candle frame + the `indicators.ichimoku` frame. Shared by:
  - the strategies (as entry gates, via `tk_momentum` / `chikou_ok`)
  - the `candles` graph (full taxonomy for the dashboard's Alerts & Signals panel)

`state` = current-bar booleans/enums; `signals` = crosses that fired on the LATEST bar (prev->now).
Per the doc: cloud breakout / Tenkan-Kijun cross / Chikou cross are strong signals; price-vs-line
crosses and the cloud twist are weaker alerts. Lookahead-safe: Senkou spans are already forward-shifted
in `indicators.ichimoku`; the Chikou test uses raw close[t] vs close[t-displacement], never the
NaN-tailed shifted column.
"""

from __future__ import annotations

import pandas as pd

_STRONG = {"cloud_breakout", "tk_cross", "chikou_cross"}


def _num(v) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def _cross(prev_diff: float | None, now_diff: float | None) -> str | None:
    """Sign flip between two consecutive differences -> 'up' / 'down' / None."""
    if prev_diff is None or now_diff is None:
        return None
    if prev_diff <= 0 < now_diff:
        return "up"
    if prev_diff >= 0 > now_diff:
        return "down"
    return None


def tk_momentum(ichi: pd.DataFrame, direction: str) -> bool:
    """Tenkan/Kijun agree with the trade direction on the latest bar (short-term momentum)."""
    t, k = _num(ichi["tenkan"].iloc[-1]), _num(ichi["kijun"].iloc[-1])
    if t is None or k is None:
        return False
    return t > k if direction == "LONG" else t < k


def tk_cross(ichi: pd.DataFrame, direction: str) -> bool:
    """Tenkan crossed Kijun on the LATEST bar in the trade direction (prev->now sign flip).

    LONG needs an up-cross (Tenkan crossing above Kijun), SHORT a down-cross. This is the WFX
    Refined Ichimoku entry trigger. Returns False on <2 rows or NaN lines (never raises).
    """
    if len(ichi) < 2:
        return False
    pt, pk = _num(ichi["tenkan"].iloc[-2]), _num(ichi["kijun"].iloc[-2])
    t, k = _num(ichi["tenkan"].iloc[-1]), _num(ichi["kijun"].iloc[-1])
    prev_diff = None if (pt is None or pk is None) else pt - pk
    now_diff = None if (t is None or k is None) else t - k
    return _cross(prev_diff, now_diff) == ("up" if direction == "LONG" else "down")


def htf_confluence(df: pd.DataFrame, ichi: pd.DataFrame, direction: str) -> bool:
    """Higher-timeframe confirmation: Tenkan vs Kijun AND close vs Kijun both agree with `direction`.

    LONG needs Tenkan > Kijun with price closed above the Kijun (SHORT mirrors). False on NaN lines.
    """
    c, k = _num(df["c"].iloc[-1]), _num(ichi["kijun"].iloc[-1])
    if c is None or k is None or not tk_momentum(ichi, direction):
        return False
    return c > k if direction == "LONG" else c < k


def regime_ok(snapshot: dict[str, pd.DataFrame], cfg, direction: str) -> str | None:
    """Top-down regime screen (e.g. daily); returns the failing skip reason or None.

    Trending in the trade direction = regime close beyond its cloud on the trade side (inside = ranging)
    and Tenkan/Kijun aligned. Fails closed when the regime frame is missing or too short.
    """
    from trading_agent.indicators import ichimoku

    p = cfg.ichimoku
    df = snapshot.get(cfg.regime_tf) if cfg.regime_tf else None
    if df is None or len(df) < p.senkou_b + p.displacement:
        return "regime_insufficient_history"
    ichi = ichimoku(df, tenkan=p.tenkan, kijun=p.kijun, senkou_b=p.senkou_b, displacement=p.displacement)
    c, top, bot = _num(df["c"].iloc[-1]), _num(ichi["cloud_top"].iloc[-1]), _num(ichi["cloud_bottom"].iloc[-1])
    if c is None or top is None or bot is None:
        return "regime_insufficient_history"
    if bot <= c <= top:
        return "regime_ranging"
    if (c > top) != (direction == "LONG"):
        return "regime_against"
    if not tk_momentum(ichi, direction):
        return "regime_tk_against"
    return None


def htf_confluent(snapshot: dict[str, pd.DataFrame], cfg, direction: str) -> bool:
    """`htf_confluence` holds on every configured timeframe above the entry one (trend, then bias).

    `cfg` is a `StrategyConfig`. Timeframes equal to the entry timeframe are skipped (they are not higher).
    """
    from trading_agent.indicators import ichimoku  # noqa: PLC0415 - keeps this module import-light

    p = cfg.ichimoku
    for tf in dict.fromkeys((cfg.trend_tf, cfg.bias_tf)):
        if tf == cfg.entry_tf:
            continue
        df = snapshot.get(tf)
        if df is None or len(df) < p.kijun:
            return False
        ichi = ichimoku(df, tenkan=p.tenkan, kijun=p.kijun, senkou_b=p.senkou_b, displacement=p.displacement)
        if not htf_confluence(df, ichi, direction):
            return False
    return True


def _sign(direction: str) -> int:
    return 1 if direction == "LONG" else -1


def touch_and_hold(bar, level: float | None, direction: str, band: float = 0.0) -> bool:
    """Bar tagged `level` (within `band`) and closed back on the trend side: a rejection of that line."""
    lvl = _num(level)
    if lvl is None:
        return False
    if direction == "LONG":
        return float(bar["l"]) <= lvl + band and float(bar["c"]) > lvl
    return float(bar["h"]) >= lvl - band and float(bar["c"]) < lvl


def fib_golden_zone(df: pd.DataFrame, lookback: int, direction: str, lo: float = 0.5, hi: float = 0.618) -> bool:
    """Latest close sits in the `lo`..`hi` retracement of the last impulse leg within `lookback` bars.

    LONG: the leg runs from the lowest low up to the later highest high; the zone is measured down from that
    high. SHORT mirrors. False when the extreme comes first (no completed leg in the trend direction).
    """
    w = df.iloc[-lookback:]
    if len(w) < 3:
        return False
    hi_pos, lo_pos = int(w["h"].to_numpy().argmax()), int(w["l"].to_numpy().argmin())
    top, bot = float(w["h"].iloc[hi_pos]), float(w["l"].iloc[lo_pos])
    rng = top - bot
    c = float(w["c"].iloc[-1])
    if rng <= 0:
        return False
    if direction == "LONG":
        return lo_pos < hi_pos < len(w) - 1 and top - hi * rng <= c <= top - lo * rng
    return hi_pos < lo_pos < len(w) - 1 and bot + lo * rng <= c <= bot + hi * rng


def swing_stop(df: pd.DataFrame, lookback: int, direction: str, buffer: float) -> float:
    """Beyond the most recent swing: lowest low (LONG) / highest high (SHORT) over `lookback` bars, +/- buffer."""
    w = df.iloc[-lookback:]
    return float(w["l"].min()) - buffer if direction == "LONG" else float(w["h"].max()) + buffer


def directional_slope(series: pd.Series, n: int, direction: str) -> float:
    """Change of `series` over `n` bars, signed so a move in the trade direction is positive; -inf if unknown."""
    if len(series) <= n:
        return float("-inf")
    now, past = _num(series.iloc[-1]), _num(series.iloc[-1 - n])
    if now is None or past is None:
        return float("-inf")
    return (now - past) * _sign(direction)


def close_crossed(df: pd.DataFrame, level: pd.Series, direction: str) -> bool:
    """Close crossed `level` on the latest bar in the trade direction (prev close on/behind it)."""
    if len(df) < 2 or len(level) < 2:
        return False
    pc, cc = _num(df["c"].iloc[-2]), _num(df["c"].iloc[-1])
    pl, lv = _num(level.iloc[-2]), _num(level.iloc[-1])
    prev_diff = None if (pc is None or pl is None) else pc - pl
    now_diff = None if (cc is None or lv is None) else cc - lv
    return _cross(prev_diff, now_diff) == ("up" if direction == "LONG" else "down")


def quality_ok(ichi: pd.DataFrame, cfg, direction: str, pip: float, *, tk: bool = True, cloud: bool = True) -> str | None:
    """Ichimoku.pdf signal-strength filters; returns the failing skip reason or None.

    `tk`: Tenkan/Kijun separation and Kijun slope (a steep, wide cross is strong). `cloud`: cloud thickness and
    Span A slope (a thin or flat cloud is a fragile / ranging market). Thresholds of 0 disable a check.
    """
    last = ichi.iloc[-1]
    n = cfg.slope_lookback
    if tk:
        t, k = _num(last["tenkan"]), _num(last["kijun"])
        if cfg.min_tk_separation_pips > 0 and (t is None or k is None or abs(t - k) < cfg.min_tk_separation_pips * pip):
            return "narrow_tk"
        if cfg.min_kijun_slope_pips > 0 and directional_slope(ichi["kijun"], n, direction) < cfg.min_kijun_slope_pips * pip:
            return "flat_kijun"
    if cloud:
        a, b = _num(last["senkou_a"]), _num(last["senkou_b"])
        if cfg.min_cloud_thickness_pips > 0 and (a is None or b is None or abs(a - b) < cfg.min_cloud_thickness_pips * pip):
            return "thin_cloud"
        if cfg.min_cloud_slope_pips > 0 and directional_slope(ichi["senkou_a"], n, direction) < cfg.min_cloud_slope_pips * pip:
            return "flat_cloud"
    return None


def exit_cross(df: pd.DataFrame, ichi: pd.DataFrame, direction: str) -> bool:
    """WFX early exit for an OPEN position: close-vs-Kijun OR Tenkan-vs-Kijun crosses against it.

    For a LONG, True when close crosses below Kijun OR Tenkan crosses below Kijun on the latest bar
    (SHORT mirrors). This is `exitLongSignal`/`exitShortSignal` from the WFX script. Never raises.
    """
    if len(ichi) < 2 or len(df) < 2:
        return False
    against = "SHORT" if direction == "LONG" else "LONG"
    if tk_cross(ichi, against):  # Tenkan/Kijun cross against the position
        return True
    pc, cc = _num(df["c"].iloc[-2]), _num(df["c"].iloc[-1])
    pk, k = _num(ichi["kijun"].iloc[-2]), _num(ichi["kijun"].iloc[-1])
    prev_diff = None if (pc is None or pk is None) else pc - pk
    now_diff = None if (cc is None or k is None) else cc - k
    return _cross(prev_diff, now_diff) == ("down" if direction == "LONG" else "up")


def chikou_ok(df: pd.DataFrame, displacement: int, direction: str) -> bool:
    """Chikou confirmation: current close vs the close `displacement` bars ago."""
    if len(df) <= displacement:
        return False
    now, past = _num(df["c"].iloc[-1]), _num(df["c"].iloc[-1 - displacement])
    if now is None or past is None:
        return False
    return now > past if direction == "LONG" else now < past


def price_vs_cloud(c: float | None, top: float | None, bottom: float | None) -> str:
    if c is None or top is None or bottom is None:
        return "unknown"
    if c > top:
        return "bullish"
    if c < bottom:
        return "bearish"
    return "neutral"


# price-vs-level confluence: which line each signal name votes against (cloud handled separately).
_VOTE_LEVEL = {"kijun": "kijun", "tenkan": "tenkan", "span_a": "senkou_a", "span_b": "senkou_b"}
# Ichimoku levels searched for a top-down take-profit target.
_TARGET_KEYS = ("kijun", "tenkan", "senkou_a", "senkou_b", "cloud_top", "cloud_bottom")


def price_level_votes(c: float | None, ichi_row, which) -> int:
    """Net directional vote (+1 above / -1 below / 0 inside-or-NaN) summed over `which` Ichimoku levels.

    `which` is any subset of {"kijun","tenkan","span_a","span_b","cloud"}. `ichi_row` is a single row
    (Series) from `indicators.ichimoku`. Used to score multi-timeframe confluence. Never raises.
    """
    cc = _num(c)
    if cc is None:
        return 0
    total = 0
    for name in which:
        if name == "cloud":
            loc = price_vs_cloud(cc, _num(ichi_row.get("cloud_top")), _num(ichi_row.get("cloud_bottom")))
            total += 1 if loc == "bullish" else -1 if loc == "bearish" else 0
            continue
        key = _VOTE_LEVEL.get(name)
        if key is None:
            continue
        lvl = _num(ichi_row.get(key))
        if lvl is not None:
            total += 1 if cc > lvl else -1 if cc < lvl else 0
    return total


def ichimoku_level_target(entry: float, direction: str, ichi_rows, min_dist: float = 0.0) -> float | None:
    """Nearest Ichimoku level beyond `entry` in the trade direction across `ichi_rows` (top-down TP).

    LONG → smallest level >= entry+min_dist; SHORT → largest level <= entry-min_dist. None if none qualify.
    """
    levels = [
        v
        for row in ichi_rows
        for k in _TARGET_KEYS
        if (v := _num(row.get(k))) is not None
    ]
    if direction == "LONG":
        above = [v for v in levels if v >= entry + min_dist]
        return min(above) if above else None
    below = [v for v in levels if v <= entry - min_dist]
    return max(below) if below else None


def ichimoku_signals(df: pd.DataFrame, ichi: pd.DataFrame, displacement: int = 26) -> dict:
    """Full taxonomy for the latest bar. df needs column `c`; ichi from indicators.ichimoku."""
    n = len(df)
    c = _num(df["c"].iloc[-1])
    top, bot = _num(ichi["cloud_top"].iloc[-1]), _num(ichi["cloud_bottom"].iloc[-1])
    t, k = _num(ichi["tenkan"].iloc[-1]), _num(ichi["kijun"].iloc[-1])

    state = {
        "price_vs_cloud": price_vs_cloud(c, top, bot),
        "tenkan_gt_kijun": None if (t is None or k is None) else t > k,
        "chikou_above_price": chikou_ok(df, displacement, "LONG") if n > displacement else None,
        "price_above_kijun": None if (c is None or k is None) else c > k,
        "price_above_tenkan": None if (c is None or t is None) else c > t,
    }

    signals: list[dict] = []
    if n >= 2:
        pc, cc = _num(df["c"].iloc[-2]), c
        pt, pk = _num(ichi["tenkan"].iloc[-2]), _num(ichi["kijun"].iloc[-2])
        ptop, pbot = _num(ichi["cloud_top"].iloc[-2]), _num(ichi["cloud_bottom"].iloc[-2])
        psa, psb = _num(ichi["senkou_a"].iloc[-2]), _num(ichi["senkou_b"].iloc[-2])
        sa, sb = _num(ichi["senkou_a"].iloc[-1]), _num(ichi["senkou_b"].iloc[-1])

        def diff(a, b):
            return None if a is None or b is None else a - b

        checks = {
            "tk_cross": _cross(diff(pt, pk), diff(t, k)),
            "price_cross_kijun": _cross(diff(pc, pk), diff(cc, k)),
            "price_cross_tenkan": _cross(diff(pc, pt), diff(cc, t)),
            "cloud_twist": _cross(diff(psa, psb), diff(sa, sb)),
        }
        # cloud breakout: close crossing the far cloud edge in the break direction
        if None not in (pc, cc, ptop, top, pbot, bot):
            if pc <= ptop and cc > top:
                checks["cloud_breakout"] = "up"
            elif pc >= pbot and cc < bot:
                checks["cloud_breakout"] = "down"
        # chikou cross: (close[t]-close[t-disp]) sign flip
        if n > displacement + 1:
            now_d = diff(cc, _num(df["c"].iloc[-1 - displacement]))
            prev_d = diff(pc, _num(df["c"].iloc[-2 - displacement]))
            checks["chikou_cross"] = _cross(prev_d, now_d)

        for name, d in checks.items():
            if d:
                signals.append({"name": name, "dir": d, "strength": "strong" if name in _STRONG else "alert"})

    return {"state": state, "signals": signals}


def _demo() -> None:
    # two bars: bar0 inside cloud & tenkan<kijun; bar1 closes above cloud & tenkan>kijun
    df = pd.DataFrame({"o": [1.00, 1.00], "h": [1.05, 1.10], "l": [0.95, 1.00], "c": [1.00, 1.08]})
    ichi = pd.DataFrame(
        {
            "tenkan": [1.00, 1.06],
            "kijun": [1.02, 1.03],
            "senkou_a": [1.03, 1.05],
            "senkou_b": [1.05, 1.04],  # A crosses above B -> cloud twist up
            "cloud_top": [1.05, 1.05],
            "cloud_bottom": [1.03, 1.04],
        }
    )
    out = ichimoku_signals(df, ichi, displacement=1)
    fired = {s["name"]: s["dir"] for s in out["signals"]}
    assert out["state"]["price_vs_cloud"] == "bullish", out["state"]
    assert out["state"]["tenkan_gt_kijun"] is True
    assert fired.get("tk_cross") == "up", fired
    assert fired.get("cloud_breakout") == "up", fired
    assert fired.get("cloud_twist") == "up", fired
    assert tk_momentum(ichi, "LONG") and not tk_momentum(ichi, "SHORT")
    assert chikou_ok(df, 1, "LONG") and not chikou_ok(df, 1, "SHORT")
    print("signals self-check OK:", fired)


if __name__ == "__main__":
    _demo()
