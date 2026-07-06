"""AgentStrategy: the Phase-2 plug. Wraps the deterministic IchimokuPullback with an LLM gate.

The inner strategy still computes the candidate Signal (all price math in code). The orchestrator
only CONFIRMS or VETOES it. Implements the same `Strategy` protocol and returns the same
`StrategyResult`, so `engine.run` / RiskEngine / execution / the event log are unchanged. Fail-closed:
any LLM error, refusal, or timeout vetoes the trade.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pandas as pd

from trading_agent.config import PairConfig
from trading_agent.strategy.agents.schemas import Verdict
from trading_agent.strategy.base import StrategyResult
from trading_agent.strategy.ichimoku_pullback import IchimokuPullback
from trading_agent.types import Signal


class AgentStrategy:
    def __init__(self, inner: IchimokuPullback, orchestrator, pair: PairConfig | None) -> None:
        self.inner = inner
        self.orchestrator = orchestrator
        self.pair = pair
        self.version = f"{inner.version}+agent-v1"

    def evaluate(self, snapshot: dict[str, pd.DataFrame], now: datetime) -> StrategyResult:
        res = self.inner.evaluate(snapshot, now)
        if res.signal is None:
            return res  # no deterministic candidate -> nothing to gate; don't spend tokens

        verdict = self._gate(res.signal)
        if verdict is None:
            return StrategyResult(None, "llm_error:gate_unavailable")  # ponytail: fail closed = veto
        if verdict.decision.strip().lower() != "approve":
            return StrategyResult(None, f"llm_veto:{verdict.confidence:.2f}:{verdict.rationale[:120]}")

        sig = replace(  # attach the verdict into features so the SIGNAL event logs it (engine unchanged)
            res.signal,
            features={**res.signal.features, "llm_verdict": {"decision": verdict.decision, "confidence": verdict.confidence, "rationale": verdict.rationale}},
        )
        return StrategyResult(sig, res.reason)

    def _gate(self, signal: Signal) -> Verdict | None:
        try:
            result = self.orchestrator.invoke({"messages": [{"role": "user", "content": self._prompt(signal)}]})
        except Exception:  # noqa: BLE001 - any orchestrator/LLM/network failure -> veto (no trade)
            return None
        v = result.get("structured_response") if isinstance(result, dict) else None
        if isinstance(v, Verdict):
            return v
        if isinstance(v, dict):  # tolerate a dict-shaped structured response
            try:
                return Verdict(**v)
            except Exception:  # noqa: BLE001
                return None
        return None

    @staticmethod
    def _prompt(signal: Signal) -> str:
        f = signal.features
        return (
            f"A deterministic Ichimoku trend-pullback setup proposes a {signal.direction} trade on {signal.instrument}. "
            f"Code-computed prices: entry={signal.entry_ref:.5f} stop={signal.stop:.5f} take_profit={signal.tp:.5f} rr={signal.rr}. "
            f"Context: H4 close={f.get('h4_close')} cloud=[{f.get('h4_cloud_bottom')}, {f.get('h4_cloud_top')}]; "
            f"H1 close={f.get('h1_close')} tenkan={f.get('h1_tenkan')} kijun={f.get('h1_kijun')}; M15 trigger={f.get('m15_trigger')}. "
            f"Consult the analysts and return your approve/veto Verdict for THIS trade only. Do not propose different prices."
        )
