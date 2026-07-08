"""Multi-timeframe Ichimoku confluence strategy (docs/trading-system/Ichimoku.pdf).

Combines the two Ichimoku setups: a code-computed CONFLUENCE SCORE — how many price-vs-level Ichimoku
signals align, read top-down across H4/H1/M15 — GATES an existing inner trigger (pullback or breakout).
More aligned signals => higher probability price reaches its next Ichimoku level ("an alert is weak and
requires other signals"). The take-profit can target that next level top-down (Kijun -> Span A = "best
profit") instead of a flat R:R. All price math stays in code; deterministic; the two inner strategies
are reused unchanged, so the confluence layer only ever *subtracts* trades (a quality filter).
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

import pandas as pd

from trading_agent.config import PairConfig, StrategyConfig
from trading_agent.indicators import ichimoku
from trading_agent.strategy import signals
from trading_agent.strategy.base import StrategyResult

SETUP_ID = "ichimoku_confluence"
# Top-down weights: the higher timeframe dominates the bias (triple-screen screen 1 outranks the trigger TF).
_TF_WEIGHT = {"trend": 3, "bias": 2, "entry": 1}
_MIN_TP_PIPS = 5.0  # an Ichimoku level nearer than this to entry is ignored (avoids a TP on top of entry)


class IchimokuConfluence:
    def __init__(self, strategy: StrategyConfig, pair: PairConfig) -> None:
        self.cfg = strategy
        self.pair = pair
        self.version = strategy.version
        # Build the inner trigger (pullback | breakout) from the same config; lazy import avoids a cycle.
        from trading_agent.strategy import build_strategy

        inner_name = "ichimoku_breakout" if strategy.entry_trigger == "breakout" else "ichimoku_pullback"
        self._inner = build_strategy(dataclasses.replace(strategy, name=inner_name), pair)

    def _ichi(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.cfg.ichimoku
        return ichimoku(df, tenkan=p.tenkan, kijun=p.kijun, senkou_b=p.senkou_b, displacement=p.displacement)

    def evaluate(self, snapshot: dict[str, pd.DataFrame], now: datetime) -> StrategyResult:  # noqa: PLR0911 - one return per gate is clearest
        cfg = self.cfg
        p = cfg.ichimoku

        # 1. Multi-timeframe confluence score (top-down H4 -> H1 -> M15).
        score = 0
        per_tf: dict[str, int] = {}
        rows: dict[str, pd.Series] = {}
        for role, tf in (("trend", cfg.trend_tf), ("bias", cfg.bias_tf), ("entry", cfg.entry_tf)):
            frame = snapshot.get(tf)
            if frame is None or len(frame) < p.min_rows:
                return StrategyResult(None, "insufficient_history")
            row = self._ichi(frame).iloc[-1]
            if row[["cloud_top", "cloud_bottom", "kijun"]].isna().any():
                return StrategyResult(None, "insufficient_history")
            rows[role] = row
            vote = signals.price_level_votes(float(frame["c"].iloc[-1]), row, cfg.confluence_signals)
            per_tf[tf] = vote
            score += _TF_WEIGHT[role] * vote

        direction = "LONG" if score > 0 else "SHORT" if score < 0 else None
        if direction is None:
            return StrategyResult(None, "low_confluence")
        # H4 hard directional filter (top-down screen 1): never trade against the higher-TF trend.
        h4_vote = per_tf[cfg.trend_tf]
        if (direction == "LONG" and h4_vote <= 0) or (direction == "SHORT" and h4_vote >= 0):
            return StrategyResult(None, "h4_conflict")
        if abs(score) < cfg.confluence_min_score:
            return StrategyResult(None, "low_confluence")

        # 2. Delegate the entry trigger + stop to the inner strategy; require its direction to agree.
        res = self._inner.evaluate(snapshot, now)
        if res.signal is None:
            return res
        sig = res.signal
        if sig.direction != direction:
            return StrategyResult(None, "confluence_conflict")

        # 3. Optional TP override: the nearest Ichimoku level beyond entry, searched top-down (H1 + H4).
        tp, tp_source = sig.tp, "rr"
        if cfg.tp_mode == "ichimoku_level":
            target = signals.ichimoku_level_target(sig.entry_ref, direction, [rows["bias"], rows["trend"]], _MIN_TP_PIPS * self.pair.pip_size)
            beyond = target is not None and ((target > sig.entry_ref) if direction == "LONG" else (target < sig.entry_ref))
            if beyond:
                stop_distance = abs(sig.entry_ref - sig.stop)
                reward = abs(float(target) - sig.entry_ref)
                min_rr = 1.0  # confluence TP must still clear the global R:R floor
                if stop_distance > 0 and reward >= min_rr * stop_distance:
                    tp, tp_source = float(target), "ichimoku_level"

        features = {**sig.features, "confluence_score": score, "confluence_votes": per_tf, "tp_source": tp_source, "confluence_direction": direction}
        reason = f"confluence_{direction.lower()}"
        out = dataclasses.replace(sig, tp=tp, setup_id=SETUP_ID, features=features, reason=reason)
        return StrategyResult(out, reason)
