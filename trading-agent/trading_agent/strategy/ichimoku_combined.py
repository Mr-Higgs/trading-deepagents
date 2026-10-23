"""One combined Ichimoku strategy - top-down confluence direction + any trigger + price-action gate.

Generalizes `ichimoku_confluence`: instead of gating a single inner trigger, it runs ALL the Ichimoku
setups (pullback / breakout / cloud-breakout) and takes the FIRST whose signal agrees with the top-down
multi-timeframe direction and passes the price-action confirmation. The confluence score picks the trade
DIRECTION only (no minimum-score threshold); the H4 cloud vote is still a hard filter (never trade against
the higher-TF trend); price-action structure (break-of-structure / S/R confluence) is the required
confirmation. Exits are unchanged - the inner signal's stop/TP plus engine-level management. All price math
stays in code, deterministic; the inner strategies are reused unchanged, so this layer only ever *subtracts*
trades (a quality filter) relative to firing every setup blindly.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

import pandas as pd

from trading_agent.config import PairConfig, StrategyConfig
from trading_agent.strategy import price_action, signals
from trading_agent.strategy.base import StrategyResult

SETUP_ID = "ichimoku_combined"
_MIN_TP_PIPS = 5.0  # an Ichimoku level nearer than this to entry is ignored (avoids a TP on top of entry)
# ponytail: fixed set + order of inner triggers; first match in this order wins on a bar. Add a
# `combined_triggers` config field only when a run needs to reorder/subset them.
_INNER_TRIGGERS = ("ichimoku_pullback", "ichimoku_breakout", "ichimoku_cloud_breakout")


class IchimokuCombined:
    def __init__(self, strategy: StrategyConfig, pair: PairConfig) -> None:
        self.cfg = strategy
        self.pair = pair
        self.version = strategy.version
        # Build every inner trigger from the same config; lazy import avoids a cycle. require_price_action
        # is forced off on the inners so the price-action gate is enforced exactly ONCE, here.
        from trading_agent.strategy import build_strategy

        self._inners = [build_strategy(dataclasses.replace(strategy, name=name, require_price_action=False), pair) for name in _INNER_TRIGGERS]

    def evaluate(self, snapshot: dict[str, pd.DataFrame], now: datetime) -> StrategyResult:  # noqa: PLR0911, PLR0912 - one return per gate is clearest
        cfg = self.cfg

        # 1. Multi-timeframe confluence score (top-down H4 -> H1 -> M15) -> DIRECTION only.
        conf = signals.topdown_confluence(snapshot, cfg)
        if conf is None:
            return StrategyResult(None, "insufficient_history")
        score, per_tf, rows = conf

        direction = "LONG" if score > 0 else "SHORT" if score < 0 else None
        if direction is None:
            return StrategyResult(None, "no_direction")
        # H4 hard directional filter (top-down screen 1): never trade against the higher-TF trend.
        h4_vote = per_tf[cfg.trend_tf]
        if (direction == "LONG" and h4_vote <= 0) or (direction == "SHORT" and h4_vote >= 0):
            return StrategyResult(None, "h4_conflict")

        # 2. First inner trigger that fires in the confluence direction AND passes the price-action gate.
        entry_frame = snapshot[cfg.entry_tf]
        pa_missing: set[str] | None = None  # a direction-matching trigger existed but price-action was short
        for inner in self._inners:
            res = inner.evaluate(snapshot, now)
            sig = res.signal
            if sig is None or sig.direction != direction:
                continue
            pa_fired: list[str] = []
            if cfg.require_price_action:
                want = set(cfg.price_action_signals)
                fired = price_action.matches(
                    cfg.price_action_signals,
                    direction,
                    entry_frame,
                    sig.entry_ref,
                    lookback=cfg.swing_lookback,
                    tolerance=cfg.sr_tolerance_pips * self.pair.pip_size,
                )
                if not want <= set(fired):
                    pa_missing = want - set(fired)
                    continue
                pa_fired = sorted(fired)

            # 3. Optional TP override: the nearest Ichimoku level beyond entry, searched top-down (H1 + H4).
            tp, tp_source = sig.tp, "rr"
            if cfg.tp_mode == "ichimoku_level":
                target = signals.ichimoku_level_target(sig.entry_ref, direction, [rows["bias"], rows["trend"]], _MIN_TP_PIPS * self.pair.pip_size)
                beyond = target is not None and ((target > sig.entry_ref) if direction == "LONG" else (target < sig.entry_ref))
                if beyond:
                    stop_distance = abs(sig.entry_ref - sig.stop)
                    reward = abs(float(target) - sig.entry_ref)
                    if stop_distance > 0 and reward >= stop_distance:  # confluence TP must still clear the 1R floor
                        tp, tp_source = float(target), "ichimoku_level"

            features = {
                **sig.features,
                "combined_score": score,
                "combined_votes": per_tf,
                "trigger_setup": sig.setup_id,
                "price_action": pa_fired,
                "tp_source": tp_source,
                "confluence_direction": direction,
            }
            reason = f"combined_{direction.lower()}"
            out = dataclasses.replace(sig, tp=tp, setup_id=SETUP_ID, features=features, reason=reason)
            return StrategyResult(out, reason)

        if pa_missing is not None:
            return StrategyResult(None, f"no_price_action:{','.join(sorted(pa_missing))}")
        return StrategyResult(None, "no_trigger")
