"""Triple-barrier labeling: turn a candle history into (features -> LONG/SHORT/FLAT) training rows.

A signal GENERATOR cannot harvest labels from a base strategy's trade log (there is no base strategy).
So for each decision bar we define the same stop/TP geometry the live strategy will use (ATR*mult stop,
rr*stop TP) and look FORWARD up to `max_bars` to see which barrier a trade would have hit first
(López de Prado, AFML ch. 3). The forward look is legitimate here because it happens ONLY offline to
assign a label; the features on that same row are strictly backward-looking, and at inference nothing
looks forward.

Label:
  - "LONG"  if a long would have hit TP before its stop (and a short would not have)
  - "SHORT" if a short would have (and a long would not have)
  - "FLAT"  otherwise (neither cleanly wins, both do, or the time barrier expires first)

Same-bar tie-break matches execution/simulated.py `_exit_price`: the stop is checked before the TP
(pessimistic), so a bar that trades through both counts as a loss, never a win.
"""

from __future__ import annotations

from collections.abc import Iterator

import pandas as pd

from trading_agent.indicators import atr
from trading_agent.marketdata.base import closed_only
from trading_agent.marketdata.historical import HistoricalOandaSource
from trading_agent.ml.features import _WINDOW, build_features


def triple_barrier_win(highs, lows, entry: float, stop_dist: float, tp_dist: float, direction: str, max_bars: int) -> bool:
    """Would a trade opened at `entry` in `direction` hit its take-profit before its stop within
    `max_bars` forward bars? `highs`/`lows` are the FUTURE bars (bar i+1..). Stop checked first."""
    if stop_dist <= 0 or tp_dist <= 0:
        return False
    if direction == "LONG":
        stop, tp = entry - stop_dist, entry + tp_dist
        for h, low in zip(highs[:max_bars], lows[:max_bars]):
            if low <= stop:  # stop first (pessimistic same-bar tie-break)
                return False
            if h >= tp:
                return True
        return False
    stop, tp = entry + stop_dist, entry - tp_dist
    for h, low in zip(highs[:max_bars], lows[:max_bars]):
        if h >= stop:
            return False
        if low <= tp:
            return True
    return False


def label_bar(highs, lows, entry: float, stop_dist: float, tp_dist: float, max_bars: int) -> str:
    """Directional label for one bar: LONG / SHORT / FLAT (see module docstring)."""
    long_win = triple_barrier_win(highs, lows, entry, stop_dist, tp_dist, "LONG", max_bars)
    short_win = triple_barrier_win(highs, lows, entry, stop_dist, tp_dist, "SHORT", max_bars)
    if long_win and not short_win:
        return "LONG"
    if short_win and not long_win:
        return "SHORT"
    return "FLAT"


def build_dataset(source: HistoricalOandaSource, cfg, *, stride: int = 1) -> Iterator[dict]:
    """Yield one labeled record per (strided) entry-timeframe bar of `source`.

    Each record = build_features(snapshot at that bar) + {label, ts, instrument}. Stop geometry is
    ATR(atr_length)*atr_mult and TP = rr*stop, computed vectorized over the whole entry frame (fast;
    an EWM-ATR converges to the same value the live strategy reads). Bars are skipped when they lack
    the warmup for a defined cloud or the forward bars to resolve a barrier - so a record is only
    emitted when both its features and its label are well-defined.
    """
    entry_frame = source._frames[cfg.entry_tf]  # noqa: SLF001 - source built here; we own the frames
    n = len(entry_frame)
    atrv = atr(entry_frame, cfg.atr_length).to_numpy()
    highs = entry_frame["h"].to_numpy()
    lows = entry_frame["l"].to_numpy()
    closes = entry_frame["c"].to_numpy()
    close_times = entry_frame["close_time"]
    instrument = str(entry_frame["instrument"].iloc[0]) if n else "?"
    warmup = cfg.ichimoku.min_rows
    max_bars = cfg.max_hold_bars

    for i in range(warmup, n - max_bars, max(1, stride)):
        a = atrv[i]
        if not (a > 0):  # NaN during warmup or a flat series -> no valid stop geometry
            continue
        stop_dist = a * cfg.atr_mult
        label = label_bar(highs[i + 1:], lows[i + 1:], float(closes[i]), stop_dist, cfg.rr * stop_dist, max_bars)
        now = close_times.iloc[i]
        snapshot = {tf: closed_only(df, now) for tf, df in source._frames.items()}  # noqa: SLF001
        rec = build_features(snapshot, cfg)
        rec["label"] = label
        rec["ts"] = now
        rec["instrument"] = instrument
        yield rec


_META_HORIZON = 192  # forward bars to resolve a candidate's win/loss (M15: ~2 trading days)


def build_meta_dataset(source: HistoricalOandaSource, cfg, inner, *, stride: int = 1, horizon: int = _META_HORIZON):
    """Meta-labeling dataset: one row per candidate the `inner` strategy FIRES, labeled win(1)/loss(0).

    This is López de Prado meta-labeling: the primary (deterministic) strategy picks direction + stop/TP;
    the ML secondary learns which of those bets to KEEP. Unlike `build_dataset` (every bar, ~91% FLAT), only
    signal bars become rows and the classes are roughly balanced (win vs loss of REAL trades), which is why
    a filter can learn where a generator could not. The label uses the signal's OWN stop/TP geometry and
    direction (not a symmetric ATR barrier) scanned forward, stop-before-TP tie-break — matching the engine.
    Features are the same `build_features` vector, so the trained filter serves live with parity.
    """
    entry_frame = source._frames[cfg.entry_tf]  # noqa: SLF001 - source built here; we own the frames
    n = len(entry_frame)
    highs = entry_frame["h"].to_numpy()
    lows = entry_frame["l"].to_numpy()
    close_times = entry_frame["close_time"]
    instrument = str(entry_frame["instrument"].iloc[0]) if n else "?"
    warmup = cfg.ichimoku.min_rows

    for i in range(warmup, n - 1, max(1, stride)):
        now = close_times.iloc[i]
        # tail each closed frame (like build_features) so the inner strategy is O(window), not O(i), per
        # bar -> the whole pass is O(n) not O(n^2). Lookahead-safe (recent closed bars) and identical for
        # a loose breakout inner, which only reads the last rows (ichimoku tail / tk_cross / atr).
        snapshot = {tf: closed_only(df, now).tail(_WINDOW) for tf, df in source._frames.items()}  # noqa: SLF001
        res = inner.evaluate(snapshot, now)
        sig = res.signal
        if sig is None:
            continue
        entry = float(sig.entry_ref)
        stop_dist = abs(entry - float(sig.stop))
        tp_dist = abs(float(sig.tp) - entry)
        win = triple_barrier_win(highs[i + 1:], lows[i + 1:], entry, stop_dist, tp_dist, sig.direction, horizon)
        rec = build_features(snapshot, cfg)
        rec["label"] = int(win)
        rec["ts"] = now
        rec["instrument"] = instrument
        rec["direction"] = sig.direction
        yield rec


def _demo() -> None:
    # up-run then down-run: a LONG at the start clears TP before stop; a stop-out-first bar is not a win.
    highs = [10.5, 11.0, 12.0]
    lows = [9.9, 10.4, 11.0]
    assert triple_barrier_win(highs, lows, entry=10.0, stop_dist=1.0, tp_dist=1.0, direction="LONG", max_bars=3)
    assert label_bar(highs, lows, 10.0, 1.0, 1.0, 3) == "LONG"

    # stop is hit on bar 1 (low 8.5 <= 9.0) before any TP -> not a long win; symmetric short wins
    h2 = [10.2, 10.3, 10.4]
    l2 = [8.5, 8.0, 7.0]
    assert not triple_barrier_win(h2, l2, 10.0, 1.0, 1.0, "LONG", 3)
    assert triple_barrier_win(h2, l2, 10.0, 1.0, 1.0, "SHORT", 3)
    assert label_bar(h2, l2, 10.0, 1.0, 1.0, 3) == "SHORT"

    # neither barrier reached within the horizon -> FLAT (time barrier)
    flat_h = [10.1, 10.2, 10.1]
    flat_l = [9.9, 9.8, 9.9]
    assert label_bar(flat_h, flat_l, 10.0, 1.0, 1.0, 3) == "FLAT"
    print("labeling self-check OK")


if __name__ == "__main__":
    _demo()
