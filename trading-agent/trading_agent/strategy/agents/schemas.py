"""Structured-output schemas for the confirmation gate.

`Verdict` is what the orchestrator returns (`result["structured_response"]`) and the single
machine-validated object the money path reads. `AnalystView` is each sub-agent's structured reply.
pydantic (a langchain dep, in the `phase2` group) so `response_format=` gets a reliable schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Bumped whenever a prompt or this schema changes, so a stale cached verdict is never replayed.
# macro-v1: fundamental analyst rebuilt on AQR Integrated-Macro doctrine + macro_context/dislocation context.
PROMPT_VERSION = "macro-v1"


class AnalystView(BaseModel):
    stance: Literal["supports", "neutral", "against"] = Field(description="Does your analysis support, oppose, or stay neutral on the proposed trade?")
    confidence: float = Field(description="0.0-1.0 confidence in your stance")
    note: str = Field(description="One or two sentences of rationale")


class Verdict(BaseModel):
    decision: Literal["approve", "veto", "modify"] = Field(
        description="approve to let the code-computed trade through, veto to block it, or modify to propose refined prices"
    )
    confidence: float = Field(description="0.0-1.0 confidence in the decision")
    rationale: str = Field(description="Brief reason for the decision, citing the analysts")
    # Only read when decision == "modify" (signal-authority mode). Each None field keeps the deterministic
    # value. Size is never here — it stays code-owned by the RiskEngine.
    direction: Literal["LONG", "SHORT"] | None = Field(default=None, description="Proposed trade direction; None keeps the deterministic one")
    entry: float | None = Field(default=None, description="Proposed reference entry price; None keeps the deterministic one")
    stop: float | None = Field(default=None, description="Proposed stop price; None keeps the deterministic one")
    tp: float | None = Field(default=None, description="Proposed take-profit price; None keeps the deterministic one")
