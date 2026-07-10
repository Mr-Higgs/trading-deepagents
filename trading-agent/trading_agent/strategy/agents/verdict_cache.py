"""Verdict cache: makes agent backtests deterministic and free to re-run.

The gate's decision is a pure function of its prompt (which is a pure function of the signal) + the
orchestrator model + prompt version. So we key the cache on a hash of exactly those and replay the
stored Verdict on a hit — a re-run of the same backtest spends zero tokens and returns identical
trades, restoring the engine's determinism invariant that a live LLM call would otherwise break.

Storage is append-only JSONL under the gitignored `data/`. Only successful verdicts are cached, never
errors. ponytail: a dict loaded from one file; swap for sqlite only if the file grows unwieldy.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# trading_agent/strategy/agents/verdict_cache.py -> trading-agent/
_DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "verdict_cache.jsonl"


def key_for(prompt: str, orchestrator_model: str, prompt_version: str) -> str:
    """Stable cache key. Same prompt + model + prompt version => same key => same verdict."""
    raw = f"{orchestrator_model}\x1f{prompt_version}\x1f{prompt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class VerdictCache:
    def __init__(self, path: Path | None = None, enabled: bool = True, refresh: bool = False) -> None:
        self.path = path or _DEFAULT_PATH
        self.enabled = enabled
        self.refresh = refresh  # bypass lookups (force fresh LLM calls) but still write results
        self._mem: dict[str, dict] = {}
        if enabled and self.path.exists():
            for line in self.path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    self._mem[rec["key"]] = rec["verdict"]
                except (json.JSONDecodeError, KeyError):
                    continue  # skip a corrupt line rather than crash the run

    def get(self, key: str) -> dict | None:
        if not self.enabled or self.refresh:
            return None
        return self._mem.get(key)

    def put(self, key: str, verdict: dict) -> None:
        if not self.enabled:
            return
        self._mem[key] = verdict
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            fh.write(json.dumps({"key": key, "verdict": verdict}) + "\n")


def _selfcheck() -> None:
    """assert-based self-check: hit / miss / refresh / persistence across reloads."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vc.jsonl"
        k = key_for("prompt A", "anthropic:claude-opus-4-8", "signal-v1")
        assert k == key_for("prompt A", "anthropic:claude-opus-4-8", "signal-v1")  # stable
        assert k != key_for("prompt B", "anthropic:claude-opus-4-8", "signal-v1")  # content-sensitive
        assert k != key_for("prompt A", "anthropic:claude-haiku-4-5", "signal-v1")  # model-sensitive

        c = VerdictCache(p)
        assert c.get(k) is None  # miss
        c.put(k, {"decision": "approve", "confidence": 0.8})
        assert c.get(k) == {"decision": "approve", "confidence": 0.8}  # hit

        c2 = VerdictCache(p)  # reload from disk
        assert c2.get(k) == {"decision": "approve", "confidence": 0.8}  # persisted

        c3 = VerdictCache(p, refresh=True)
        assert c3.get(k) is None  # refresh bypasses the lookup...
        c3.put(k, {"decision": "veto", "confidence": 0.5})  # ...but still writes
        assert VerdictCache(p).get(k) == {"decision": "veto", "confidence": 0.5}

        c4 = VerdictCache(p, enabled=False)
        assert c4.get(k) is None  # disabled never hits
    print("verdict_cache self-check OK")


if __name__ == "__main__":
    _selfcheck()
