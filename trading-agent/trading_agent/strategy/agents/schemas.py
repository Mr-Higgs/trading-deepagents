"""Structured-output schemas for the confirmation gate.

`Verdict` is what the orchestrator returns (`result["structured_response"]`) and the single
machine-validated object the money path reads. `AnalystView` is each sub-agent's structured reply.
pydantic (a langchain dep, in the `phase2` group) so `response_format=` gets a reliable schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AnalystView(BaseModel):
    stance: Literal["supports", "neutral", "against"] = Field(description="Does your analysis support, oppose, or stay neutral on the proposed trade?")
    confidence: float = Field(description="0.0-1.0 confidence in your stance")
    note: str = Field(description="One or two sentences of rationale")


class Verdict(BaseModel):
    decision: Literal["approve", "veto"] = Field(description="approve to let the trade through, veto to block it")
    confidence: float = Field(description="0.0-1.0 confidence in the decision")
    rationale: str = Field(description="Brief reason for the decision, citing the analysts")
