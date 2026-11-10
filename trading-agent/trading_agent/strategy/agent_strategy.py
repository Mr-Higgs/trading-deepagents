"""AgentStrategy: the Phase-2 plug. Wraps the deterministic IchimokuPullback with an LLM decision layer.

The inner deterministic strategy always computes the candidate Signal first (the proven baseline).
Two authority levels then apply:

- authority="gate" (default): the orchestrator only APPROVES or VETOES the code-computed signal;
  prices are byte-identical on approve. Original Phase-2 behavior.
- authority="signal": the orchestrator has full authority over the trade's PRICES and DIRECTION - it
  may approve, veto, or MODIFY (emit a refined direction/entry/stop/tp). Position size and every hard
  risk gate stay code-owned: a modified Signal is re-validated here and then by the RiskEngine exactly
  like any other, so `engine.run` / RiskEngine / execution / the event log are unchanged.

Fail-closed throughout: any LLM error, refusal, timeout, or invalid modification => no trade. When a
cache is supplied, verdicts are memoized by prompt so a backtest re-run is deterministic and free.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime

import pandas as pd

from trading_agent.config import PairConfig
from trading_agent.indicators import price_dislocation
from trading_agent.lessons import LessonStore
from trading_agent.strategy.agents.schemas import PROMPT_VERSION, Verdict
from trading_agent.strategy.agents.verdict_cache import VerdictCache, key_for
from trading_agent.strategy.base import Strategy, StrategyResult
from trading_agent.types import Signal

_SANITY_BAND = 0.2  # a modified price must sit within ±20% of the baseline entry (rejects absurd values)


class AgentStrategy:
    def __init__(
        self,
        inner: Strategy,
        orchestrator,
        pair: PairConfig | None,
        authority: str = "gate",
        cache: VerdictCache | None = None,
        orchestrator_model: str = "",
        *,
        point_in_time: bool = False,
        veto_default: bool = False,
        lessons: LessonStore | None = None,
        lessons_n: int = 5,
    ) -> None:
        """`point_in_time=True` (backtests) pins every analyst tool call to the bar timestamp.

        `veto_default` only keys the verdict cache: the posture lives in the orchestrator's system prompt, so a
        veto-biased gate must never replay (or overwrite) default-approve verdicts for the same trade prompt.

        `lessons` appends the last `lessons_n` post-trade lessons on the instrument whose outcome was known by the
        bar time. They are part of the prompt, so the verdict cache keys on them too.
        """
        self.inner = inner
        self.point_in_time = point_in_time
        self.veto_default = veto_default
        self.lessons = lessons
        self.lessons_n = lessons_n
        self._now: datetime | None = None
        self.orchestrator = orchestrator
        self.pair = pair
        self.authority = authority
        self.cache = cache
        self.orchestrator_model = orchestrator_model
        self.version = f"{inner.version}+agent-{authority}-v1"

    def evaluate(self, snapshot: dict[str, pd.DataFrame], now: datetime) -> StrategyResult:
        res = self.inner.evaluate(snapshot, now)
        if res.signal is None:
            return res  # no deterministic candidate -> nothing to decide on; don't spend tokens

        self._now = now
        signal = self._annotate_dislocation(res.signal, snapshot)  # AQR Opportunistic-Macro context
        verdict = self._gate(signal)
        if verdict is None:
            return StrategyResult(None, "llm_error:gate_unavailable")  # ponytail: fail closed = no trade
        decision = verdict.decision.strip().lower()

        if decision == "approve":
            return StrategyResult(self._with_verdict(signal, verdict), res.reason)
        if self.authority == "signal" and decision == "modify":
            sig = self._build_modified(signal, verdict)
            if sig is None:
                return StrategyResult(None, f"llm_invalid_signal:{verdict.confidence:.2f}:{verdict.rationale[:100]}")
            return StrategyResult(sig, res.reason)
        # veto, or any non-approve decision in gate mode (incl. a stray "modify") -> no trade
        return StrategyResult(None, f"llm_veto:{verdict.confidence:.2f}:{verdict.rationale[:120]}")

    def manage(self, backend, bar, snapshot: dict[str, pd.DataFrame], now: datetime, log) -> None:
        """Forward the engine's exit hook to an inner strategy that manages its own trades (JevTrader); else no-op."""
        inner = getattr(self.inner, "manage", None)
        if inner is not None:
            inner(backend, bar, snapshot, now, log)

    def _annotate_dislocation(self, signal: Signal, snapshot: dict[str, pd.DataFrame]) -> Signal:
        """Attach a code-computed price-dislocation z-score (AQR Opportunistic-Macro proxy) into features
        so the macro analyst can reason over how far price sits from a recent fair-value anchor. Fail-soft:
        on any error / insufficient history the signal is returned unchanged (the prompt just omits it)."""
        try:
            trend_tf = getattr(getattr(self.inner, "cfg", None), "trend_tf", None)
            df = snapshot.get(trend_tf) if trend_tf else None
            if df is None:  # defensive fallback: the widest-history frame present
                df = max((f for f in snapshot.values() if f is not None), key=len, default=None)
            z = price_dislocation(df) if df is not None else float("nan")
            if pd.isna(z):
                return signal
            return replace(signal, features={**signal.features, "dislocation_z": round(z, 2)})
        except Exception:  # noqa: BLE001 - a context extra must never break the gate
            return signal

    def _gate(self, signal: Signal) -> Verdict | None:
        prompt = self._prompt(signal, self.authority) + self._lessons_note(signal)
        version = f"{PROMPT_VERSION}+pit" if self.point_in_time else PROMPT_VERSION  # PIT verdicts never replay live-data ones
        if self.veto_default:
            version += "+veto"  # posture-specific key; default-approve keys stay unchanged
        key = key_for(prompt, self.orchestrator_model, version) if self.cache is not None else None
        if key is not None:
            cached = self.cache.get(key)
            if cached is not None:
                return _coerce(cached)  # replay a prior decision: deterministic, zero tokens
        inputs: dict = {"messages": [{"role": "user", "content": prompt}]}
        if getattr(self.orchestrator, "accepts_signal", False):
            inputs["signal"] = signal  # typed gates (Jev) derive code-computed facts; the prompt already reflects these features
        try:
            if self.point_in_time and self._now is not None:
                from trading_agent.strategy.agents.tools import backtest_clock

                with backtest_clock(self._now):
                    result = self.orchestrator.invoke(inputs)
            else:
                result = self.orchestrator.invoke(inputs)
        except Exception:  # noqa: BLE001 - any orchestrator/LLM/network failure -> veto (no trade)
            return None
        v = _coerce(result.get("structured_response") if isinstance(result, dict) else None)
        if v is not None and key is not None:
            self.cache.put(key, v.model_dump())  # only successful decisions are cached, never errors
        return v

    def _lessons_note(self, signal: Signal) -> str:
        """Past lessons block for the prompt; empty when disabled, none qualify, or the store can't be read."""
        if self.lessons is None:
            return ""
        try:
            ctx = self.lessons.past_context(signal.instrument, as_of=self._now, n=self.lessons_n)
        except Exception:  # noqa: BLE001 - a context extra must never break the gate
            return ""
        if not ctx:
            return ""
        return (
            "\n\nLessons from past closed trades on this instrument (most recent first; each is one trade, weigh them "
            f"as context, not rules):\n{ctx}"
        )

    def _with_verdict(self, signal: Signal, verdict: Verdict) -> Signal:
        """Attach the verdict into features so the SIGNAL event logs it (engine unchanged)."""
        return replace(signal, features={**signal.features, "llm_verdict": _verdict_dict(verdict)})

    def _build_modified(self, candidate: Signal, verdict: Verdict) -> Signal | None:
        """Construct a Signal from the LLM's overrides (None fields keep the baseline). Validate finiteness,
        price ordering, a sanity band, and a positive stop distance; return None (fail-closed) if anything
        is off. The RiskEngine still re-checks min-stop / min-RR / all hard gates downstream."""
        direction = verdict.direction or candidate.direction
        entry = verdict.entry if verdict.entry is not None else candidate.entry_ref
        stop = verdict.stop if verdict.stop is not None else candidate.stop
        tp = verdict.tp if verdict.tp is not None else candidate.tp

        if not all(math.isfinite(x) for x in (entry, stop, tp)):
            return None
        ordered = (stop < entry < tp) if direction == "LONG" else (tp < entry < stop)
        if not ordered:
            return None
        base = candidate.entry_ref
        if base > 0 and any(abs(x - base) / base > _SANITY_BAND for x in (entry, stop, tp)):
            return None  # absurdly far from the baseline -> reject
        risk = abs(entry - stop)
        if risk <= 0:
            return None
        rr = round(abs(tp - entry) / risk, 4)
        return replace(
            candidate,
            direction=direction,
            entry_ref=entry,
            stop=stop,
            tp=tp,
            rr=rr,
            features={**candidate.features, "llm_verdict": _verdict_dict(verdict)},
        )

    @staticmethod
    def _prompt(signal: Signal, authority: str) -> str:
        f = signal.features
        closing = (
            "Approve, veto, or modify this trade. If modifying, return a refined direction/entry/stop/tp "
            "(omit fields to keep the baseline); size stays code-owned."
            if authority == "signal"
            else "Consult the analysts and return your approve/veto Verdict for THIS trade only. Do not propose different prices."
        )
        # Labels track the actual triple-screen stack (from the `stack` feature) so the gate reasons about
        # the real timeframes under any style; the feature-value keys (h4_/h1_/m15_) are internal names.
        trend_tf, bias_tf, entry_tf = (str(f.get("stack") or "H4/H1/M15").split("/") + ["", "", ""])[:3]
        return (
            f"A deterministic Ichimoku setup ({f.get('style', 'custom')} style, {trend_tf}/{bias_tf}/{entry_tf}) "
            f"proposes a {signal.direction} trade on {signal.instrument}. "
            f"Code-computed prices: entry={signal.entry_ref:.5f} stop={signal.stop:.5f} take_profit={signal.tp:.5f} rr={signal.rr}. "
            f"Context: {trend_tf} close={f.get('h4_close')} cloud=[{f.get('h4_cloud_bottom')}, {f.get('h4_cloud_top')}]; "
            f"{bias_tf} close={f.get('h1_close')} tenkan={f.get('h1_tenkan')} kijun={f.get('h1_kijun')}; {entry_tf} trigger={f.get('m15_trigger')}. "
            f"{_dislocation_note(f.get('dislocation_z'))}"
            f"{closing}"
        )


def _dislocation_note(z: object) -> str:
    """Render the Opportunistic-Macro dislocation for the prompt; empty when it couldn't be computed."""
    if z is None:
        return ""
    try:
        stretched = abs(float(z)) >= 2.0
    except (TypeError, ValueError):
        return ""
    return f"Price dislocation: dislocation_z={z} ({'stretched >=2sigma' if stretched else 'within normal range'}). "


def _verdict_dict(verdict: Verdict) -> dict:
    d = {"decision": verdict.decision, "confidence": verdict.confidence, "rationale": verdict.rationale}
    if verdict.decision.strip().lower() == "modify":  # record what the LLM proposed
        d["proposed"] = {"direction": verdict.direction, "entry": verdict.entry, "stop": verdict.stop, "tp": verdict.tp}
    return d


def _coerce(v: object) -> Verdict | None:
    """Accept a Verdict instance or a dict-shaped structured response; anything else -> None."""
    if isinstance(v, Verdict):
        return v
    if isinstance(v, dict):
        try:
            return Verdict(**v)
        except Exception:  # noqa: BLE001
            return None
    return None
