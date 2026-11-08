"""Devin CLI as the gate model: `orchestrator_model: devin-cli:<model>` in agents.yaml.

A drop-in for the deepagents orchestrator: same `.invoke({"messages": [...]})` -> `{"structured_response": Verdict}`
shape, so AgentStrategy (cache, fail-closed, point-in-time clock) is unchanged. Devin runs as one agent, so the
analyst *data* is fetched here in code (same tools, same point-in-time clock) and handed over in the prompt.

The Devin CLI is a full coding agent, not a bare model, so each call is boxed in:
  - cwd is a fresh empty temp dir (not the repo), so there is no .env or code to read;
  - OANDA_* / ANTHROPIC_API_KEY are stripped from its environment;
  - --permission-mode auto = read-only tools only (never "dangerous").
Any non-zero exit, timeout, or unparseable reply raises -> AgentStrategy turns it into "no trade".
"""

from __future__ import annotations

import os
import subprocess
import tempfile

from trading_agent.strategy.agents.schemas import Verdict

PREFIX = "devin-cli:"
TIMEOUT_S = 180  # ponytail: fixed; an agent call is slow but a gate that hangs a bar is worse. Make it config if it bites.
_SECRET_PREFIXES = ("OANDA_", "ANTHROPIC_API_KEY")

DEVIN_GATE_PROMPT = (
    "You are a trade gate for a disciplined forex system. A deterministic strategy has ALREADY decided to open the "
    "trade below, with entry, stop and take-profit computed in code. You do NOT set prices, change sizes, or invent "
    "trades. Do not run commands or edit files: reason only over the text given. Weigh the technical context and the "
    "analyst data below, then decide ONLY whether to approve or veto THIS trade. Veto on a concrete risk against it: "
    "imminent high-impact news, speculative positioning stretched hard against it, a macro trend or extreme price "
    "dislocation contradicting the direction, or higher-timeframe context contradicting the setup."
)
DEVIN_SIGNAL_PROMPT = (
    "You are the decision-maker for a disciplined forex system. A deterministic strategy proposed the trade below as a "
    "BASELINE. You have authority over its PRICES and DIRECTION only; size and hard risk limits stay owned by code and "
    "re-validate whatever you return. Do not run commands or edit files: reason only over the text given. Return "
    "decision='approve' to take it unchanged, 'veto' to stand down, or 'modify' with a refined direction/entry/stop/tp."
)
_OUTPUT_RULE = (
    "\n\nReply with ONLY one JSON object and nothing else, exactly these keys: "
    '{"decision": "approve" | "veto" | "modify", "confidence": <0.0-1.0>, "rationale": "<one or two sentences>", '
    '"direction": null, "entry": null, "stop": null, "tp": null}'
)


def parse_verdict(text: str) -> Verdict:
    """Pull the JSON object out of Devin's plain-text reply (no JSON print mode exists yet). Raises on anything off."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in Devin reply")
    return Verdict.model_validate_json(text[start : end + 1])


def _scrubbed_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith(_SECRET_PREFIXES)}


def _run_devin(model: str, prompt: str) -> str:
    with tempfile.TemporaryDirectory(prefix="devin-gate-") as box:
        out = subprocess.run(
            ["devin", "--model", model, "--permission-mode", "auto", "--respect-workspace-trust", "false",
             "--verbosity", "0", "-p", prompt],
            cwd=box, env=_scrubbed_env(), capture_output=True, text=True, timeout=TIMEOUT_S, check=True,
        )
    return out.stdout


class DevinCliGate:
    def __init__(self, cfg, run=_run_devin) -> None:
        a = cfg.agents
        self.model = a.orchestrator_model.removeprefix(PREFIX)
        self.instrument = cfg.strategy.instrument
        self.cftc_code = a.cftc_code
        self.fundamental, self.sentiment = a.fundamental, a.sentiment
        base = DEVIN_SIGNAL_PROMPT if a.authority == "signal" else DEVIN_GATE_PROMPT
        posture = (
            " Bias toward veto: approve only when the data clearly supports the trade."
            if a.veto_default
            else " Default to approve the deterministic baseline; only veto on a concrete reason."
        )
        self.system = base + posture
        self.run = run

    def _analyst_data(self) -> str:
        """Same tools the deepagents analysts call; they read the point-in-time clock and fail soft to 'unavailable'."""
        from trading_agent.strategy.agents.tools import cftc_cot, economic_calendar, macro_context

        parts = []
        if self.fundamental:
            parts.append("Economic calendar: " + economic_calendar.invoke({"instrument": self.instrument}))
            parts.append("Macro context: " + macro_context.invoke({"instrument": self.instrument}))
        if self.sentiment:
            parts.append("CFTC COT positioning: " + cftc_cot.invoke({"cftc_code": self.cftc_code}))
        return "\n".join(parts) or "No analyst data enabled."

    def invoke(self, inputs: dict) -> dict:
        trade = inputs["messages"][-1]["content"]
        prompt = f"{self.system}\n\nTRADE:\n{trade}\n\nANALYST DATA:\n{self._analyst_data()}{_OUTPUT_RULE}"
        return {"structured_response": parse_verdict(self.run(self.model, prompt))}
