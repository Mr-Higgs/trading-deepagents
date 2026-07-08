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
