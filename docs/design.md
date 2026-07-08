- Orchestrator agent (Raplh mode) making iterations and consistently reviewing positions trades.
- 3 Sub-Agents -> technical-analyst (Ichimoku Cloud), fundamental-analyst (Macro Momentum Theme: Forex Factory for News, GDP-business cycles, Interest Rates, International Trade), sentimental-analyst (CFTC, & tradingster)
Trading Platform for Paper Trading: Oanda

# Agentic patterns in `examples/` — what to repurpose for the trading gate

## Context

The user asked which agentic patterns in `examples/` (LangChain's vendored Deep Agents monorepo, ~15 example agents) would be worth repurposing for the Phase-2 LLM **confirmation gate** in `trading-agent/` — the "model reasons, code owns the money" approve/veto gate over a code-computed Ichimoku signal.

This is a **report-only** deliverable (no code changes). The key finding, established by reading both the examples and the current gate, is a reframe: **the gate already implements most of the strong patterns in these examples.** So the useful output is a classification — redundant (already have it) vs. genuinely additive vs. YAGNI — plus a couple of concrete, cheap wins to keep on file.

Where the current gate lives:
- `trading-agent/trading_agent/strategy/agent_strategy.py` — the subtractive wrapper (`AgentStrategy.evaluate` → `_gate`); fail-closed logic.
- `trading-agent/trading_agent/strategy/agents/orchestrator.py` — `build_orchestrator` → `create_deep_agent` (opus gate + 3 haiku analysts), `GATE_PROMPT`, analyst prompts.
- `trading-agent/trading_agent/strategy/agents/schemas.py` — `Verdict`, `AnalystView`.
- `trading-agent/trading_agent/strategy/agents/tools.py` — `economic_calendar`, `cftc_cot` (fetch-wraps-pure-parser, degrade-to-neutral-on-error).
- `trading-agent/config/agents.yaml` → `config.py:AgentsConfig`.

---

## 1. Already covered — do NOT rebuild

The gate already embodies these; adopting the example version would be redundant (and in two cases the gate's version is stricter).

| Pattern (source example) | Already present as |
|---|---|
| **Code-owned accept/reject; model only proposes** (`better-harness` — `core.py:935-967`) | `AgentStrategy` is subtractive-only. Prices (`entry/stop/tp/rr`) computed in `IchimokuPullback`; on approve the signal passes through **unchanged** (`agent_strategy.py:41-45`), verdict attached to `features["llm_verdict"]`. Test: `test_approve_returns_same_prices_plus_verdict`. |
| **Fail-closed structured decision** (`llm-wiki` regex parse + skip-default — `query.py`) | Pydantic `Verdict` via `response_format`, fail-closed in **4 places** in `_gate`/`evaluate` (bare-except on invoke, missing `structured_response`, unparseable dict, `None`-verdict). Stricter than llm-wiki's regex. |
| **Read-only reasoner that cannot act** (`text-to-sql` no-DML skill; `llm-wiki` `_review_permissions` deny-all) | Gate agent has `tools=[]`; the entire LLM path holds **no OANDA credentials** (creds route through `oanda_env.py` practice tripwire at the two OANDA seams only). Architecturally can't place/alter an order — no permission layer needed. |
| **"Never report from memory — call a live tool"** (`async-subagent-server` — `supervisor.py:82-85`) | Analysts fetch live each invocation (`economic_calendar`, `cftc_cot`), degrading to a neutral string on any exception. |
| **Heterogeneous multi-model orchestration** (`nvidia_deep_agent`) | opus gate + 3 haiku analysts via `create_deep_agent(subagents=[...])`. |
| **Fresh context per decision** (`ralph_mode` loop) | Engine calls `strategy.evaluate` on each *closed* M15 bar; orchestrator invoked fresh per candidate. No cross-turn drift. |
| **Grep-able audit trail** (`llm-wiki` append-only `log.md`) | JSONL events per loop step (`trading_agent.journal`); verdict rides in `features["llm_verdict"]`, emitted by the `SIGNAL` event with zero engine change. |

## 2. Genuinely additive & cheap — worth keeping on file

1. **`temperature=0` on gate + analyst models** — from `text-to-sql-agent` (`ChatAnthropic(temperature=0)` for determinism).
   Today `init_chat_model(a.orchestrator_model)` / `init_chat_model(a.analyst_model)` in `orchestrator.py` run at **default temperature**, so approve/veto verdicts are not reproducible run-to-run. A ~2-line change would make verdicts deterministic — valuable for a money-adjacent gate and for replayable forward runs. (When implemented, confirm the temperature kwarg path for `init_chat_model` + current model ids `claude-opus-4-8` / `claude-haiku-4-5`.)

2. **`think_tool` forced pre-verdict reflection** — from `deep_research` (`research_agent/tools.py:91-116`): a no-op tool returning its own input, made mandatory by the prompt, forcing the model to write "evidence / contradictions / confidence / decision" before the verdict.
   *Marginal* here: `Verdict.rationale` + the three `AnalystView.note`s already capture reasoning. Only worth it if verdict rationales are turning out too thin to audit. Optional.

## 3. Additive but YAGNI now — note for later, don't build

- **HITL confirm before a live order** — `nvidia_deep_agent`'s `interrupt_on={"<tool>": True}` and `llm-wiki`'s `confirm_ingest_apply` `y/N`. `create_deep_agent` already accepts `interrupt_on` (unused today). **Only relevant when/if going live** — the system is practice-only by tripwire, and per-trade confirmation would defeat paper automation.
- **Parallel multi-pair gates** — `repl_swarm`'s bounded worker-pool (`runSwarm`, concurrency-capped, per-item failure isolation). Useful only at multi-pair; today it's AUD/USD only. Revisit then.
- **YAML-as-data sub-agent loader** — `content-builder-agent`'s `load_subagents(subagents.yaml)` (maps string tool-names → tool objects). `agents.yaml` already exposes the real knobs (models, which analysts enabled); moving full prompts/tools to YAML is indirection without a current payoff.
- **`context_schema` paper/live runtime switch** — `nvidia_deep_agent`'s `backend.py` factory reading `runtime.context`. Already solved differently and well: backend injection (`simulated` vs `oanda_paper`) chosen at wiring time + the practice tripwire.

## 4. Not worth repurposing

- **`rlm_agent`** — recursive self-similar agents; over-engineered for a single approve/veto. Both surveying agents agreed.
- **`deploy-coding-agent`, `deploy-gtm-agent`, `deploy-content-writer`, `deploy-mcp-docs-agent`, `downloading_agents`** — deployment/packaging boilerplate (`deepagents.toml` + `AGENTS.md` + `mcp.json`). Each carries one small idea (skill-bundled scripts; retrieval-before-answer via MCP; user-scoped memory; nested sub-agent folders) but none change the gate's decision logic. Note only: if per-account risk profiles ever matter, `deploy-content-writer`'s `user_id`-scoped memory + its thread-isolation test methodology is the reference.

## 5. Bonus catch (surfaced during the review, not from the examples)

Two `AgentsConfig` fields are **declared and loaded but never consumed** — worth a cleanup pass independent of this review:
- `veto_default` (`config.py:105,219`) — read nowhere. "Default to approve" exists only as prose in `GATE_PROMPT`, not as a code toggle.
- `cftc_code` (`config.py:106,220`) — never passed to `cftc_cot`; the tool uses its own hardcoded `"232741"` (`tools.py:60`) and the analyst LLM picks the arg. Values happen to match but aren't connected.

Either wire them (make `veto_default` a real posture toggle; thread `cftc_code` into the tool) or delete them.

---

## Verification

Not applicable — this is a documentation/report deliverable with no code changes. The classifications above cite exact files/lines in both `examples/` and `trading-agent/`; each claim can be spot-checked by opening the referenced path. If any item in §2 is later chosen for implementation, standard verification applies at that point (run `uv run pytest tests/test_agent_strategy.py`, and a live forward run with `--strategy agent` to confirm verdicts are now reproducible).
