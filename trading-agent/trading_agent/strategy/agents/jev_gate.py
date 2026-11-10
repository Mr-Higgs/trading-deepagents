"""TypeSafe Jev as the gate model: `orchestrator_model: typesafe:<model>` in agents.yaml.

Jev is a "System One" decision model. It doesn't generate text. It takes a state and typed questions and returns
calibrated probabilities. That maps onto the approve/veto gate as one yes/no question (a "noul"):
p(take_trade) >= threshold => approve, otherwise veto. It is a drop-in for the deepagents orchestrator, with the same
`.invoke({"messages": [...]})` -> `{"structured_response": Verdict}` shape, so AgentStrategy (cache, fail-closed,
point-in-time clock) and the engine are unchanged.

Gate authority only: Jev picks between options it is given and cannot invent prices, so `authority: signal` is refused.
Jev is weak at comparing numbers, so the code-computed relations (price vs cloud, tenkan vs kijun, dislocation) go in
as categorical `facts`, not left for the model to derive from raw floats.
Any API/network error or missing answer raises -> AgentStrategy turns it into "no trade".
"""

from __future__ import annotations

from typing import Any

from trading_agent.strategy.agents.schemas import Verdict
from trading_agent.strategy.jev_levels import side as _side
from trading_agent.types import Signal

PREFIX = "typesafe:"
DEFAULT_THRESHOLD = 0.5
VETO_THRESHOLD = 0.65  # veto_default posture: approve only when Jev is clearly for the trade
TIMEOUT_S = 15.0  # a decision call is ~0.1-0.5s; anything near this is an outage, so fail closed fast
DECISION = "take_trade"


def _questions() -> dict:
    from typesafe_sdk import Noul  # lazy: phase2 group only

    return {
        DECISION: Noul(
            instructions=(
                "A deterministic forex strategy already computed this trade (entry, stop, take-profit, size are fixed in code). "
                "Should it be taken, given the technical facts and analyst data?"
            ),
            criteria={
                "true": "The context supports the trade, or raises no concrete risk against it.",
                "false": (
                    "There is a concrete risk against it: imminent high-impact news, speculative positioning stretched hard "
                    "against it, macro trend or an extreme price dislocation contradicting the direction, or "
                    "higher-timeframe context contradicting the setup."
                ),
            },
        ),
        # Diagnostics only: logged in the rationale so thresholds and failure modes can be studied offline.
        "news_risk": Noul(instructions="Is a high-impact economic event for either currency scheduled within the next few hours?"),
        "htf_conflict": Noul(instructions="Does the higher-timeframe context contradict the trade direction?"),
    }


def signal_facts(signal: Signal) -> dict[str, Any]:
    """Categorical, code-computed relations from the signal features. Keys with missing inputs are omitted."""
    f = signal.features
    long = signal.direction == "LONG"
    facts: dict[str, Any] = {"direction": signal.direction, "reward_to_risk_at_least_2": signal.rr >= 2.0}
    cloud = _side(f.get("h4_close"), f.get("h4_cloud_bottom"), f.get("h4_cloud_top"))
    if cloud:
        facts["trend_close_vs_cloud"] = cloud
        facts["trend_cloud_agrees_with_direction"] = cloud == ("above" if long else "below")
    if kijun := _side(f.get("h1_close"), f.get("h1_kijun")):
        facts["bias_close_vs_kijun"] = kijun
    if tk := _side(f.get("h1_tenkan"), f.get("h1_kijun")):
        facts["bias_tenkan_vs_kijun"] = tk
    try:
        z = float(f["dislocation_z"])
    except (KeyError, TypeError, ValueError):
        pass
    else:
        stretched = "stretched_up" if z >= 2.0 else "stretched_down" if z <= -2.0 else "normal"
        facts["price_dislocation"] = stretched
        facts["dislocation_against_direction"] = stretched == ("stretched_up" if long else "stretched_down")
    return facts


def to_verdict(p: dict[str, float], threshold: float) -> Verdict:
    """Threshold the calibrated p(take_trade) into a gate Verdict. Confidence is the probability of the chosen side."""
    take = p[DECISION]
    approve = take >= threshold
    extras = " ".join(f"{k}={v:.2f}" for k, v in p.items() if k != DECISION)
    return Verdict(
        decision="approve" if approve else "veto",
        confidence=round(take if approve else 1.0 - take, 4),
        rationale=f"jev p_take={take:.2f} {'>=' if approve else '<'} {threshold:.2f} {extras}".strip(),
    )


class JevGate:
    accepts_signal = True  # AgentStrategy passes the Signal so facts can be computed in code

    def __init__(self, cfg, client=None) -> None:
        a = cfg.agents
        if a.authority != "gate":
            msg = f"typesafe: gate supports authority 'gate' only (got {a.authority!r}); Jev cannot propose prices"
            raise ValueError(msg)
        self.model = a.orchestrator_model.removeprefix(PREFIX)
        threshold = getattr(a, "jev_threshold", None)
        self.threshold = threshold if threshold is not None else VETO_THRESHOLD if a.veto_default else DEFAULT_THRESHOLD
        self.instrument = cfg.strategy.instrument
        self.cftc_code = a.cftc_code
        self.fundamental, self.sentiment = a.fundamental, a.sentiment
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from typesafe_sdk import RetryPolicy, TypeSafeClient  # reads TYPESAFE_API_KEY

            self._client = TypeSafeClient(model=self.model, retry=RetryPolicy(max_retries=1), timeout=TIMEOUT_S)
        return self._client

    def _analyst_data(self) -> str:
        from trading_agent.strategy.agents.tools import analyst_digest

        return analyst_digest(self.instrument, self.cftc_code, fundamental=self.fundamental, sentiment=self.sentiment)

    def invoke(self, inputs: dict) -> dict:
        signal = inputs.get("signal")
        state = {
            "trade": inputs["messages"][-1]["content"],
            "facts": signal_facts(signal) if isinstance(signal, Signal) else {},
            "analyst_data": self._analyst_data(),
        }
        questions = _questions()
        resp = self.client.system_one(state, questions)
        p = {name: float(resp.nouls[name].noul) for name in questions}  # a missing answer raises -> fail closed
        return {"structured_response": to_verdict(p, self.threshold)}
