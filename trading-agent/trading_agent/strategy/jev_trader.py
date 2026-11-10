"""JevTrader: TypeSafe's Jev picks the setup, the stop / take-profit, and in-trade exits from code-built menus.

`agents.yaml` `trader: jev` swaps this in for the single rule-based strategy. It still speaks the Strategy protocol,
so the engine, RiskEngine (size, min-stop, min-R:R, every hard gate), execution and the event log are unchanged, and
the LLM confirmation gate (`mode: agent`) can still veto whatever Jev picks.

Per entry bar:
  1. Setup: every configured Ichimoku strategy runs; the ones that fire (plus optional `discretionary` long/short,
     plus "no_trade") become one Jev Choice over categorical market facts. No candidates and no discretionary => no call.
  2. Levels: a second call (it depends on the chosen direction) picks a stop and a take-profit from code-computed
     candidates; code takes the highest joint-probability pair that clears `risk.min_rr`.
Per bar with open positions (`manage`, called by the engine): one call, one question per position, choosing
hold / close_now / breakeven / trail_kijun. Stop moves only ever tighten (the options are pre-filtered).

Fail-safe: entry errors => no trade; exit errors => hold (the existing stop and TP stay live). Decisions are cached
by request hash (backtests replay deterministically and free); live runs pass no cache.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from trading_agent.strategy import build_strategy
from trading_agent.strategy.base import StrategyResult
from trading_agent.strategy.jev_levels import (
    EXIT_DESCRIPTIONS,
    Context,
    best_pair,
    context_features,
    exit_menu,
    market_facts,
    stop_options,
    tp_options,
)
from trading_agent.types import Signal

JEV_CACHE = Path(__file__).resolve().parents[2] / "data" / "jev_cache.jsonl"  # trading-agent/data (gitignored)
PROMPT_VERSION = "jev-trader-v1"  # bump on any question/description change so stale cached decisions never replay
TIMEOUT_S = 15.0

SETUP_DESCRIPTIONS = {
    "ichimoku_pullback": "Ichimoku trend pullback: trend timeframe beyond the cloud, bias timeframe pulled back into Tenkan/Kijun/Fib, "
    "reversal candle on the entry timeframe",
    "ichimoku_breakout": "Ichimoku breakout: Tenkan/Kijun cross or a close through the cloud/Kijun on the entry timeframe",
    "ichimoku_cloud_breakout": "Kumo breakout and retest: price broke out of the cloud and pulled back to the broken edge",
    "ichimoku_confluence": "Multi-timeframe confluence: price-vs-Ichimoku-level votes agree across trend, bias and entry timeframes",
}
SETUP_QUESTION = (
    "You are choosing the next forex trade from the candidates below. Each candidate other than the discretionary ones "
    "is a rule-based setup that has just fired. Pick the one most likely to reach its target given the market facts, "
    "or no_trade when the setups conflict, the context is choppy, or the facts run against them."
)
STOP_QUESTION = "Which stop-loss placement best protects this trade: beyond real structure, without being so tight that noise stops it out?"
TP_QUESTION = "Which take-profit is price most likely to reach before reversing, given the market facts and the trade direction?"
EXIT_QUESTION = "What should be done with open position {name} (state.positions.{name}) on this bar?"


class JevDecider:
    """One Jev request = independent Choice questions over a shared state; returns label probabilities per question."""

    def __init__(self, model: str, *, client=None, cache=None) -> None:
        self.model = model
        self._client = client
        self.cache = cache

    @property
    def client(self):
        if self._client is None:
            from typesafe_sdk import RetryPolicy, TypeSafeClient  # lazy: phase2 group only; reads TYPESAFE_API_KEY

            self._client = TypeSafeClient(model=self.model, retry=RetryPolicy(max_retries=1), timeout=TIMEOUT_S)
        return self._client

    def choose(self, state: dict[str, Any], questions: dict[str, tuple[str, dict[str, str]]]) -> dict[str, dict[str, float]]:
        """`questions` maps name -> (instructions, {label: description}). Raises on any API failure."""
        raw = json.dumps({"model": self.model, "v": PROMPT_VERSION, "state": state, "questions": questions}, sort_keys=True)
        key = hashlib.sha256(raw.encode()).hexdigest()
        if self.cache is not None and (hit := self.cache.get(key)) is not None:
            return hit
        from typesafe_sdk import Choice

        resp = self.client.system_one(state, {name: Choice(instructions=ins, criteria=opts) for name, (ins, opts) in questions.items()})
        out = {name: {k: float(v) for k, v in resp.choices[name].probabilities.items()} for name in questions}
        if self.cache is not None:
            self.cache.put(key, out)
        return out


def _pick(options: dict[str, str], probs: dict[str, float]) -> tuple[str, float]:
    """Argmax over the menu (ties resolve to menu order, so replays are stable)."""
    label = max(options, key=lambda k: probs.get(k, 0.0))
    return label, probs.get(label, 0.0)


class JevTrader:
    def __init__(self, cfg, *, decider: JevDecider | None = None, cache=None) -> None:
        self.cfg = cfg
        self.jev = cfg.agents.jev
        self.strategies = {name: build_strategy(replace(cfg.strategy, name=name), cfg.pair) for name in self.jev.setups}
        self.decider = decider or JevDecider(self.jev.model, cache=cache)
        self.version = f"{cfg.strategy.version}+{self.jev.model}"  # e.g. v3.1.0+jev-1.13.0

    def _context(self, snapshot: dict[str, pd.DataFrame]) -> Context | None:
        return Context.build(snapshot, self.cfg.strategy, self.cfg.pair.pip_size)

    def _setup_menu(self, fired: dict[str, Signal]) -> dict[str, str]:
        menu = {label: f"{SETUP_DESCRIPTIONS.get(sig.setup_id, sig.setup_id)}. Direction: {sig.direction}." for label, sig in fired.items()}
        if self.jev.discretionary:
            menu["discretionary_long"] = "Go LONG on the overall context, without a rule-based setup."
            menu["discretionary_short"] = "Go SHORT on the overall context, without a rule-based setup."
        menu["no_trade"] = "Stand aside this bar."
        return menu

    def evaluate(self, snapshot: dict[str, pd.DataFrame], now: datetime) -> StrategyResult:
        fired: dict[str, Signal] = {}
        for name, strat in self.strategies.items():
            sig = strat.evaluate(snapshot, now).signal
            if sig is not None:
                fired.setdefault(f"{name}_{sig.direction.lower()}", sig)
        if not fired and not self.jev.discretionary:
            return StrategyResult(None, "jev_no_setup")  # nothing to choose from; don't spend a call
        ctx = self._context(snapshot)
        if ctx is None:
            return StrategyResult(None, "insufficient_history")
        facts = market_facts(ctx)

        menu = self._setup_menu(fired)
        try:
            probs = self.decider.choose({"market": facts}, {"setup": (SETUP_QUESTION, menu)})["setup"]
        except Exception:  # noqa: BLE001 - any Jev/network failure => no trade (fail closed)
            return StrategyResult(None, "jev_error:setup")
        label, p = _pick(menu, probs)
        if label == "no_trade" or p < self.jev.min_setup_prob:
            return StrategyResult(None, f"jev_no_trade:{label}:{p:.2f}")

        base = fired.get(label)
        direction = base.direction if base else ("LONG" if label.endswith("_long") else "SHORT")
        return self._with_levels(ctx, facts, label, p, base, direction, now)

    def _with_levels(self, ctx: Context, facts: dict, label: str, p_setup: float, base: Signal | None, direction: str, now: datetime) -> StrategyResult:
        s, r = self.cfg.strategy, self.cfg.risk
        stops = stop_options(ctx, s, direction, r.min_stop_pips, base.stop if base else None)
        tps = tp_options(ctx, s, direction, base.tp if base else None)
        if not stops or not tps:
            return StrategyResult(None, "jev_no_levels")
        questions = {
            "stop": (STOP_QUESTION, {lv.label: lv.description for lv in stops}),
            "take_profit": (TP_QUESTION, {lv.label: lv.description for lv in tps}),
        }
        try:
            probs = self.decider.choose({"market": facts, "trade": {"setup": label, "direction": direction}}, questions)
            p_stop, p_tp = probs["stop"], probs["take_profit"]
        except Exception:  # noqa: BLE001 - fail closed
            return StrategyResult(None, "jev_error:levels")
        pair = best_pair(stops, tps, p_stop, p_tp, ctx.close, r.min_rr, r.sizing_cost_pips * ctx.pip)
        if pair is None:
            return StrategyResult(None, "jev_no_valid_levels")
        stop, tp, rr = pair
        decision = {"setup": label, "p_setup": round(p_setup, 4), "stop": stop.label, "p_stop": round(p_stop.get(stop.label, 0.0), 4),
                    "take_profit": tp.label, "p_tp": round(p_tp.get(tp.label, 0.0), 4), "model": self.jev.model}
        features = {**(base.features if base else context_features(ctx, s)), "jev": decision}
        reason = f"jev_{label}"
        sig = Signal(
            ts=now, signal_id=f"{s.instrument}-{now:%Y%m%dT%H%M%SZ}", instrument=s.instrument, direction=direction,
            entry_ref=ctx.close, stop=stop.price, tp=tp.price, rr=rr, setup_id=base.setup_id if base else "jev_discretionary",
            strategy_version=self.version, features=features, reason=reason,
        )
        return StrategyResult(sig, reason)

    def manage(self, backend, bar, snapshot: dict[str, pd.DataFrame], now: datetime, log) -> None:
        """Engine hook: let Jev hold / close / tighten each open position. Errors => hold (stops and TPs stay live)."""
        if not self.jev.manage_exits:
            return
        positions = backend.open_positions()
        if not positions:
            return
        ctx = self._context(snapshot)
        if ctx is None:
            return
        buffer = self.cfg.risk.management.breakeven_buffer_pips
        menus = {f"p{i}": (pos, *exit_menu(ctx, pos, self.cfg.strategy, buffer)) for i, pos in enumerate(positions)}
        options = {name: {a: EXIT_DESCRIPTIONS[a] for a in ("hold", "close_now", *stops)} for name, (_, _, stops) in menus.items()}
        state = {"market": market_facts(ctx), "positions": {name: facts for name, (_, facts, _) in menus.items()}}
        questions = {name: (EXIT_QUESTION.format(name=name), opts) for name, opts in options.items()}
        try:
            answers = self.decider.choose(state, questions)
        except Exception:  # noqa: BLE001 - an exit-model outage must never touch a position
            log.write("MANAGE_SKIP", ts=now, reason="jev_error", positions=len(positions))
            return
        for name, (pos, _, stops) in menus.items():
            if name not in answers:
                continue
            action, p = _pick(options[name], answers[name])
            if action == "hold" or p < self.jev.min_exit_prob:
                continue
            if action == "close_now":
                backend.request_close(pos.trade_id)
                direction = "LONG" if (pos.remaining_units or pos.units) > 0 else "SHORT"
                log.write("CLOSE_SIGNAL", ts=now, reason="jev_close", trade_id=pos.trade_id, direction=direction, p=round(p, 4))
            else:
                backend.modify_stop(pos.trade_id, stops[action])
                log.write("STOP_MODIFIED", ts=now, reason=f"jev_{action}", trade_id=pos.trade_id, old_stop=pos.stop,
                          new_stop=stops[action], entry=pos.entry, p=round(p, 4))
