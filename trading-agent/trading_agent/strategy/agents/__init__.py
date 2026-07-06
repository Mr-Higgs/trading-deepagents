"""Phase-2 LLM confirmation-gate: orchestrator + 3 analyst sub-agents.

Kept import-free on purpose so `from ...agents.schemas import Verdict` does not drag in the
deepagents/langchain stack (only `orchestrator.py` needs it, and it's imported lazily).
"""
