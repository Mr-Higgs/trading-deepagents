"""Code-built option menus for the Jev trader: market facts, stop / take-profit candidates, exit actions.

Jev returns probabilities over options it is given; it never writes a price. So every price Jev can pick is
computed here from the lookahead-safe snapshot (Ichimoku levels, swings, ATR multiples), filtered to valid ones
(right side of entry, min stop, sanity band), and described in categorical / pre-computed terms because Jev is
weak at numeric comparison. Pure functions, no SDK import: unit-testable and usable on a lean install.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from trading_agent.indicators import atr, ichimoku, price_dislocation
from trading_agent.strategy import signals

_SANITY_BAND = 0.2  # a candidate price must sit within ±20% of entry (same bound as AgentStrategy modify)
STOP_ATR_MULTS = (1.5, 2.0, 3.0)
TP_ATR_MULTS = (2.0, 3.0, 4.0)
_LEVEL_COLS = {"tenkan": "tenkan", "kijun": "kijun", "span_a": "senkou_a", "span_b": "senkou_b"}


@dataclass(frozen=True)
class Level:
    label: str
    price: float
    description: str


def side(value: object, low: object, high: object | None = None) -> str | None:
    """'above' / 'below' (/ 'inside' for a band) of `value` vs `low`[..`high`]; None when any input is missing."""
    try:
        v, lo = float(value), float(low)
        hi = float(high) if high is not None else lo
    except (TypeError, ValueError):
        return None
    if pd.isna(v) or pd.isna(lo) or pd.isna(hi):
        return None
    lo, hi = min(lo, hi), max(lo, hi)
    return "above" if v > hi else "below" if v < lo else "inside"


def _ichi(df: pd.DataFrame, cfg) -> pd.DataFrame:
    p = cfg.ichimoku
    return ichimoku(df, tenkan=p.tenkan, kijun=p.kijun, senkou_b=p.senkou_b, displacement=p.displacement)


def _num(v: object) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


@dataclass(frozen=True)
class Context:
    """Per-bar snapshot digest shared by setup choice, level menus and exit decisions."""

    entry_df: pd.DataFrame
    bias_df: pd.DataFrame
    trend_df: pd.DataFrame
    entry_ichi: pd.DataFrame
    bias_ichi: pd.DataFrame
    trend_ichi: pd.DataFrame
    close: float
    atr: float | None
    pip: float

    @classmethod
    def build(cls, snapshot: dict[str, pd.DataFrame], cfg, pip: float) -> Context | None:
        """None when a timeframe is missing or too short for Ichimoku (callers skip / hold)."""
        frames = [snapshot.get(tf) for tf in (cfg.entry_tf, cfg.bias_tf, cfg.trend_tf)]
        if any(f is None or len(f) < cfg.ichimoku.min_rows for f in frames):
            return None
        entry, bias, trend = frames
        a = _num(atr(entry, cfg.atr_length).iloc[-1])
        return cls(entry, bias, trend, _ichi(entry, cfg), _ichi(bias, cfg), _ichi(trend, cfg), float(entry["c"].iloc[-1]), a, pip)

    def pips(self, distance: float) -> float:
        return round(abs(distance) / self.pip, 1)

    def atrs(self, distance: float) -> float | None:
        return round(abs(distance) / self.atr, 2) if self.atr else None


def market_facts(ctx: Context) -> dict[str, Any]:
    """Categorical market state (no raw prices) for the setup and exit questions."""
    t, b, e = ctx.trend_ichi.iloc[-1], ctx.bias_ichi.iloc[-1], ctx.entry_ichi.iloc[-1]
    facts: dict[str, Any] = {
        "trend_close_vs_cloud": side(ctx.trend_df["c"].iloc[-1], t["cloud_bottom"], t["cloud_top"]),
        "trend_cloud_color": side(t["senkou_a"], t["senkou_b"]) and ("bullish" if t["senkou_a"] > t["senkou_b"] else "bearish"),
        "bias_close_vs_kijun": side(ctx.bias_df["c"].iloc[-1], b["kijun"]),
        "bias_tenkan_vs_kijun": side(b["tenkan"], b["kijun"]),
        "bias_close_vs_cloud": side(ctx.bias_df["c"].iloc[-1], b["cloud_bottom"], b["cloud_top"]),
        "entry_close_vs_kijun": side(ctx.close, e["kijun"]),
        "entry_tenkan_vs_kijun": side(e["tenkan"], e["kijun"]),
    }
    bar = ctx.entry_df.iloc[-1]
    rng = float(bar["h"]) - float(bar["l"])
    body = float(bar["c"]) - float(bar["o"])
    facts["entry_last_candle"] = "doji" if rng <= 0 or abs(body) < 0.1 * rng else "bullish" if body > 0 else "bearish"
    atr_series = atr(ctx.entry_df).dropna()
    if ctx.atr and len(atr_series) >= 50:
        med = float(atr_series.iloc[-50:].median())
        facts["volatility"] = "high" if ctx.atr > 1.5 * med else "low" if ctx.atr < 0.67 * med else "normal"
    z = price_dislocation(ctx.trend_df)
    if not pd.isna(z):
        facts["price_dislocation"] = "stretched_up" if z >= 2.0 else "stretched_down" if z <= -2.0 else "normal"
    return {k: v for k, v in facts.items() if v is not None}


def context_features(ctx: Context, cfg) -> dict[str, Any]:
    """Raw-value features in the deterministic strategies' key names, so the LLM gate prompt reads them unchanged."""
    t, b = ctx.trend_ichi.iloc[-1], ctx.bias_ichi.iloc[-1]
    return {
        "h4_close": float(ctx.trend_df["c"].iloc[-1]),
        "h4_cloud_top": _num(t["cloud_top"]),
        "h4_cloud_bottom": _num(t["cloud_bottom"]),
        "h1_close": float(ctx.bias_df["c"].iloc[-1]),
        "h1_tenkan": _num(b["tenkan"]),
        "h1_kijun": _num(b["kijun"]),
        "style": cfg.style,
        "stack": f"{cfg.trend_tf}/{cfg.bias_tf}/{cfg.entry_tf}",
    }


def _describe(ctx: Context, what: str, distance: float) -> str:
    a = ctx.atrs(distance)
    return f"{what}: {ctx.pips(distance)} pips from entry" + (f" ({a}x ATR)" if a is not None else "")


def _valid(entry: float, price: float, long: bool, *, beyond: bool, min_distance: float) -> bool:
    """`beyond=False` => protective side (stop), True => profit side (TP)."""
    if not pd.notna(price) or entry <= 0 or abs(price - entry) / entry > _SANITY_BAND:
        return False
    signed = (price - entry) if long else (entry - price)
    return signed >= min_distance if beyond else -signed >= min_distance


def _dedupe(levels: list[Level], ctx: Context) -> list[Level]:
    """Drop near-duplicates (within 1 pip) keeping the first; preserves menu order."""
    out: list[Level] = []
    for lv in levels:
        if all(abs(lv.price - o.price) >= ctx.pip for o in out):
            out.append(lv)
    return out


def stop_options(ctx: Context, cfg, direction: str, min_stop_pips: float, setup_stop: float | None = None) -> list[Level]:
    """Candidate stops: the setup's own, bias Kijun, trend cloud edge, entry-tf swing, ATR multiples."""
    long = direction == "LONG"
    buf = cfg.stop_buffer_pips * ctx.pip
    b, t = ctx.bias_ichi.iloc[-1], ctx.trend_ichi.iloc[-1]
    raw: list[tuple[str, str, float | None]] = [("setup", "the setup's own rule stop", setup_stop)]
    kijun = _num(b["kijun"])
    raw.append(("bias_kijun", f"beyond {cfg.bias_tf} Kijun", None if kijun is None else kijun - buf if long else kijun + buf))
    edge = _num(t["cloud_bottom"] if long else t["cloud_top"])
    raw.append(("trend_cloud", f"beyond the {cfg.trend_tf} cloud", None if edge is None else edge - buf if long else edge + buf))
    raw.append(("entry_swing", f"beyond the last {cfg.swing_lookback}-bar {cfg.entry_tf} swing", signals.swing_stop(ctx.entry_df, cfg.swing_lookback, direction, buf)))
    if ctx.atr:
        raw += [(f"atr_{m:g}", f"{m:g}x ATR", ctx.close - m * ctx.atr if long else ctx.close + m * ctx.atr) for m in STOP_ATR_MULTS]
    min_d = max(min_stop_pips * ctx.pip, ctx.pip)
    levels = [Level(lbl, p, _describe(ctx, what, p - ctx.close)) for lbl, what, p in raw if p is not None and _valid(ctx.close, p, long, beyond=False, min_distance=min_d)]
    return _dedupe(levels, ctx)


def tp_options(ctx: Context, cfg, direction: str, setup_tp: float | None = None) -> list[Level]:
    """Candidate take-profits: the setup's own, ATR multiples, the bias-tf swing extreme, Ichimoku levels ahead."""
    long = direction == "LONG"
    raw: list[tuple[str, str, float | None]] = [("setup", "the setup's own target", setup_tp)]
    if ctx.atr:
        raw += [(f"atr_{m:g}", f"{m:g}x ATR", ctx.close + m * ctx.atr if long else ctx.close - m * ctx.atr) for m in TP_ATR_MULTS]
    w = ctx.bias_df.iloc[-cfg.fib_lookback :]
    raw.append(("bias_swing", f"the recent {cfg.bias_tf} swing {'high' if long else 'low'}", float(w["h"].max() if long else w["l"].min())))
    for tf, ichi in ((cfg.bias_tf, ctx.bias_ichi), (cfg.trend_tf, ctx.trend_ichi)):
        row = ichi.iloc[-1]
        prefix = "bias" if ichi is ctx.bias_ichi else "trend"
        raw += [(f"{prefix}_{name}", f"{tf} {name.replace('_', ' ').title()}", _num(row[col])) for name, col in _LEVEL_COLS.items()]
    levels = [Level(lbl, p, _describe(ctx, what, p - ctx.close)) for lbl, what, p in raw if p is not None and _valid(ctx.close, p, long, beyond=True, min_distance=ctx.pip)]
    return _dedupe(levels, ctx)


def best_pair(
    stops: list[Level], tps: list[Level], p_stop: dict[str, float], p_tp: dict[str, float], entry: float, min_rr: float, cost: float = 0.0
) -> tuple[Level, Level, float] | None:
    """Highest joint-probability (stop, tp) pair that clears the R:R floor; None if no pair qualifies.

    `cost` (price units) mirrors RiskEngine's spread/slippage charge, so a picked pair is never rejected as `rr_below_floor`.
    The returned rr is the raw (cost-free) ratio, as the rule strategies report it.
    """
    best: tuple[float, Level, Level, float] | None = None
    for s in stops:
        risk = abs(entry - s.price)
        for t in tps:
            rr = abs(t.price - entry) / risk if risk > 0 else 0.0
            if abs(t.price - entry) - cost < min_rr * (risk + cost):
                continue
            score = p_stop.get(s.label, 0.0) * p_tp.get(t.label, 0.0)
            if best is None or score > best[0]:
                best = (score, s, t, rr)
    return None if best is None else (best[1], best[2], round(best[3], 4))


EXIT_DESCRIPTIONS = {
    "hold": "Keep the position as is; the stop and target stay in place.",
    "close_now": "Close at market now: the trade thesis is broken or the risk of giving back profit is high.",
    "breakeven": "Move the stop to entry (plus a small buffer) to remove the risk of a loss.",
    "trail_kijun": "Tighten the stop to just beyond the entry-timeframe Kijun to lock in part of the move.",
}


def exit_menu(ctx: Context, pos, cfg, buffer_pips: float) -> tuple[dict[str, Any], dict[str, float]]:
    """Facts about one open position plus the stop for each valid stop-moving action (only-tighten, valid side)."""
    size = pos.remaining_units or pos.units
    long = size > 0
    direction = "LONG" if long else "SHORT"
    risk = abs(pos.entry - pos.init_stop)
    r_now = ((ctx.close - pos.entry) if long else (pos.entry - ctx.close)) / risk if risk > 0 else 0.0
    at_be = pos.stop >= pos.entry if long else pos.stop <= pos.entry
    e, t = ctx.entry_ichi.iloc[-1], ctx.trend_ichi.iloc[-1]
    kijun = _num(e["kijun"])
    to_tp = abs(pos.tp - ctx.close) / risk if risk > 0 else None
    facts = {
        "direction": direction,
        "unrealized_r": round(r_now, 1),
        "status": "in_profit" if r_now > 0.2 else "in_loss" if r_now < -0.2 else "flat",
        "stop_at_or_past_breakeven": at_be,
        "remaining_r_to_target": None if to_tp is None else round(to_tp, 1),
        "entry_close_vs_kijun": side(ctx.close, e["kijun"]),
        "kijun_against_position": None if kijun is None else bool(ctx.close < kijun if long else ctx.close > kijun),
        "tk_cross_against_position": bool(signals.exit_cross(ctx.entry_df, ctx.entry_ichi, direction)),
        "trend_cloud_supports_position": side(ctx.trend_df["c"].iloc[-1], t["cloud_bottom"], t["cloud_top"]) == ("above" if long else "below"),
    }
    buf = buffer_pips * ctx.pip
    stops: dict[str, float] = {}

    def _ok(new: float) -> bool:  # tighter than the current stop and still on the protective side of price
        return (pos.stop < new < ctx.close) if long else (ctx.close < new < pos.stop)

    be = pos.entry + buf if long else pos.entry - buf
    if not at_be and _ok(be):
        stops["breakeven"] = be
    if kijun is not None:
        trail = kijun - buf if long else kijun + buf
        if _ok(trail):
            stops["trail_kijun"] = trail
    return {k: v for k, v in facts.items() if v is not None}, stops
