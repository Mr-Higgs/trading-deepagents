# trading-agent — Phase 1 (deterministic forex money-core)

A disciplined, **deterministic** forex paper-trading system. No LLM in the trade loop: the model
reasons, code owns the money. Phase 1 ships one setup end-to-end; Phase 2 adds a deepagents
orchestrator + analyst sub-agents that only *propose* into the same risk + execution core.

- **Setup:** Ichimoku trend-pullback (H4 trend → H1 pullback → M15 trigger)
- **Instrument / session:** AUD/USD, London
- **Data + paper fills:** OANDA v20 practice account
- **Pipeline:** OANDA data → rule signals → risk engine → execution (sim, then OANDA paper) → JSONL journal

## Setup

```bash
uv sync                       # installs pandas, pyyaml, oandapyV20, requests, pytest
cp .env.example .env          # then fill in your OANDA practice token + account id
```

`.env` (never touches a live account):

```
OANDA_API_KEY=<practice personal access token>
OANDA_ACCOUNT_ID=<xxx-xxx-xxxxxxxx-xxx>
OANDA_ENV=practice
```

## Run

```bash
# Backtest historical candles -> event log + summary
uv run python -m trading_agent.run backtest --start 2025-01-01 --end 2025-06-30

# Render markdown journal from a log
uv run python -m trading_agent.journal data/logs/events-bt-2025-01-01-2025-06-30.jsonl

# Live paper loop (simulated fills by default; polls OANDA candles)
uv run python -m trading_agent.run live --poll 60

# Tests
uv run pytest
```

**OANDA paper auto-execution** is off by default. Only after the backtest looks right, flip
`execution.backend: oanda` in `config/risk.yaml`. `kill_switch: true` blocks all new entries (takes
effect on restart in v1).

## Dashboard (LangGraph server + UI)

The repo-root Next.js dashboard streams this package's graphs. Two terminals:

```bash
# 1. from trading-agent/ — serve the graphs on :2024
uv sync --group phase2
uv run langgraph dev

# 2. from the repo root — the UI on http://localhost:3000
yarn install
yarn dev
```

In the UI's settings dialog enter **Deployment URL** `http://127.0.0.1:2024` and **Assistant ID**
`trade_loop`. That is the only ID you type in; the dashboard calls the other five by name.

`langgraph.json` serves six graphs (assistant ID = graph name):

| Assistant ID | Entry | What it's for | Key inputs | Access |
|---|---|---|---|---|
| `trade_loop` | `loop_graph.py:loop` | Runs `engine.run` and streams every SIGNAL/FILL/EXIT/… on the LangGraph `custom` stream; the dashboard renders it. Also hosts the research loop. | `mode` (`backtest`/`live`/`research`), `start`/`end`, `poll`, `equity`, `strategy`, `execution` (`simulated`/`oanda`), `agents` overrides, `iterations`/`model` | Places practice orders only in `live` with `execution: oanda` |
| `trade_gate` | `graph.py:agent` | The Phase-2 confirmation gate on its own: APPROVE/VETO a proposed signal. | `messages` | Read-only (LLM) |
| `account` | `account_graph.py:account` | Snapshot of the OANDA practice account: summary, positions, open trades, recent `closed_trades`. Polled by `AccountProvider`. | — | Read-only |
| `account_action` | `account_action_graph.py:account_action` | Manual close or SL/TP modify of a practice trade from the dashboard. | `action` (`close`/`modify`), `trade_id` (ticket or `@clientId`), `units`, `stop`, `tp`, `instrument` | **Writes** (practice `101-` only, tripwire-guarded) |
| `config` | `config_graph.py:config` | Get/set the risk, strategy and session YAML behind the UI forms. `set` validates first and rejects bad input without writing; the next run picks up edits. | `action` (`get`/`set`), `risk`, `strategy`, `sessions` | **Writes** `config/*.yaml` on `set` |
| `candles` | `candles_graph.py:candles` | OHLC + Ichimoku lines + the alert/signal taxonomy for the Chart tab. | `instrument`, `timeframe`, `count` | Read-only |

**Devin CLI gate:** when `config/agents.yaml` sets `orchestrator_model: devin-cli:<model>`,
`build_orchestrator` returns a plain `DevinCliGate` (just `.invoke()`), not a compiled graph.
`graph.py` wraps it in a one-node `StateGraph` so `trade_gate` still loads. Without that wrapper the
server fails at boot with `Variable 'agent' ... is not a Graph or Graph factory function`.
`anthropic:` models are served as-is.

## Strategy R&D: research loop → walk-forward optimizer → locked holdout

Three stages, each with a different job, so that *no single step can both propose and approve a change*.
The model proposes; plain code judges; the final exam is taken once.

### Dependency groups

`uv sync` is intentionally lean (Phase 1 only). Extra tooling lives in optional groups:

| Command | Adds | Needed for |
|---|---|---|
| `uv sync` | pandas, pyyaml, oandapyV20, requests | backtest / live loop, tests |
| `uv sync --group phase2` | deepagents, langchain-anthropic, langgraph-cli | LLM gate, `langgraph dev`, research loop |
| `uv sync --group optimize` | **optuna** (+ sqlalchemy, alembic, numpy) | `optimize.run`, `optimize.holdout` |

Syncing a group only changes `.venv/` (gitignored) — nothing in the repo moves, and `uv.lock` already pins
the versions. Groups are additive: `uv sync --group phase2 --group optimize` installs both. Every `uv run`
must name the group too (`uv run --group optimize …`), otherwise uv re-syncs to the lean set and the import fails.

### 1. Research loop — propose (LLM)

```bash
uv run python -m trading_agent.research_loop --iterations 5      # or mode:"research" from the dashboard
```

Each iteration a fresh deep-agent reads the latest in-sample analytics, `research/JOURNAL.md` and
`research/HYPOTHESES.md`, may probe ideas with an **in-sample-only** `backtest` tool (capped at 3 probes),
and returns one hypothesis plus a **bounded search space** (which `strategy_v1.yaml` keys may move, and
between which values). Nothing is accepted here — the loop only validates the space against
`optimize.space` and saves it:

```
data/research_runs/research-<timestamp>/
  search_spaces/iter-001.json … iter-00N.json   # inputs for stage 2
  journal.md                                    # hypothesis + rationale + the exact "next:" command per iteration
  iterations.jsonl, meta.json, report.md, config/  # frozen copy of the config the run saw
```

Why no keep/revert inside the loop: repeatedly selecting on one out-of-sample window *is* a fit to that
window. So the loop deliberately has no authority to change config.

### 2. Walk-forward optimizer — judge (code)

```bash
RUN=data/research_runs/research-<timestamp>/search_spaces
uv run --group optimize python -m optimize.run --strategy ichimoku_pullback --space $RUN/iter-001.json --jobs 4
```

`optimize.run` takes one search space and runs an Optuna study **per walk-forward fold** (default: 12-month
train / 3-month test, rolling from `--start 2024-01-01`; `--anchored` for expanding windows). Each trial is a
full deterministic `engine.run` backtest in simulated execution with the LLM gate forced off, so the result
measures the rules, not the model. Useful flags: `--trials` (total, split across folds, default 200),
`--objective sharpe|…`, `--min-trades 30` (fewer trades = trial discarded), `--seed`, `--pair`.

Output: `research/opt_<PAIR>_<strategy>_<timestamp>.{md,json}` (also printed). The report ranks
parameters by **plateau** (robust neighbourhood, not the single best point) and attaches the
overfitting statistics — deflated Sharpe ratio, PBO/CSCV probability of backtest overfitting, bootstrap
CIs — plus `recommended_params`, the only thing stage 3 reads.

The optimizer **cannot** see bars at or after `HOLDOUT_START` (`optimize/walkforward.py`, 2026-07-01);
`--end` is capped there. Run every iteration's space through it; the markdown reports are directly comparable.

### 3. Holdout — final exam (once per config)

```bash
uv run --group optimize python -m optimize.holdout --report research/opt_AUD_USD_ichimoku_pullback_<ts>.json
```

Scores the report's `recommended_params` on the locked window `2026-07-01 → now`. Every use is appended
to `research/holdout_log.jsonl` **before** the backtest runs (a Ctrl-C still counts), and the same
`(pair, strategy, params)` hash is refused a second time unless `--allow-reuse` is passed — which is also
logged. The holdout is the one number you can trust precisely because it was looked at once; treat a
reuse as burning it. Only after a config passes here should its values be written into
`config/strategy_v1.yaml` (bump `version`).

### Evaluation tools — measure a component before trusting it

```bash
# LLM gate A/B: one deterministic log vs one or more gated logs over the same window
uv run python -m trading_agent.gate_compare data/logs/gate_ab/det/events-*.jsonl \
    gate=data/logs/gate_ab/gate/events-*.jsonl --out research/llm_gate_ab.md

# News blackout sweep: post-release window 0/15/30/60 min (pre-release fixed at 30), IS/OOS split
uv run python -m trading_agent.news_compare --start 2025-09-03 --end 2026-07-01 --split 2026-03-01
```

- **`gate_compare`** prints the usual edge metrics per run, plus a **veto counterfactual**: each `llm_veto`
  SKIP is matched by bar to the trade the deterministic run opened there, so the table shows the P/L the
  gate avoided (or gave up). It's approximate because a veto frees the slot for a later signal. `llm_error`
  SKIPs are fail-closed no-trades, not decisions, and are counted separately.
- **`news_compare`** reruns the deterministic strategy for each variant and isolates the trades submitted
  within 60 min after a High event. Last run (`research/news_window_compare_BA.md`, bid/ask candles):
  in-sample, any blackout beat none (expectancy 7.95 → 11.55 per trade). The post-release length (0–60 min)
  made no difference, and out-of-sample had no post-release trades at all.

## Configuration (versioned)

All rules live in `config/*.yaml` — `pairs`, `sessions`, `risk`, `strategy_v1`. Bump
`strategy_v1.yaml: version` on any rule change; every trade is tagged with the version that produced it.

## News blackouts

No new entries open inside a blackout window. Windows come from two places in `config/risk.yaml`:
`news_windows` (manual extras) and `news_auto`, which builds them from the stored calendar
`data/calendar/economic_calendar.csv` for either currency of the pair. The default blackout is 30/30 min for
High and 15/15 for Medium; per-event columns win (FOMC 30/90, RBA 30/60).

```bash
# history from the publishers (Fed, FRED release calendar, ISM, RBA, ABS) — needs FRED_API_KEY
uv run python -m trading_agent.calendar_build --start 2021-01-01 --end 2026-07-01
# fold this week's Forex Factory feed (Medium/High) into the same file
uv run python -m trading_agent.calendar_build --merge-ff
```

- **Backtests and `run.py`** build the windows once from the stored CSV, so backtests stay point-in-time
  and deterministic. If the file is missing you get a warning and no blackouts.
- **Dashboard live runs** (`trade_loop` `mode:"live"`) use `RefreshingNewsWindows`. They force a Forex Factory
  pull at start, then re-pull every `news_auto.refresh_hours` (6) on the live poll cadence, with no extra
  thread. A failed pull only warns and keeps the stored windows, because a flaky feed must never block the
  loop.
- The RBA scraper skips a year whose decisions page isn't published yet (warning, no AUD rate rows).

## Architecture (one loop, swap the edges)

Backtest and live run the **same** `engine.run` loop; only two injected objects differ:

| Seam | Backtest | Live |
|---|---|---|
| `MarketDataSource` | `HistoricalOandaSource` | `LiveOandaSource` |
| `ExecutionBackend` | `SimulatedBackend` | `SimulatedBackend` or `OandaPaperBackend` |

The decision clock is each closed **M15** bar's `close_time`; the loop never reads the wall clock, so
backtests are byte-for-byte deterministic. `snapshot(now)` only ever returns candles closed by `now`
(no lookahead). `Signal` (in `types.py`) is the Phase-2 boundary: an LLM strategy will emit the same
dataclass and the deterministic `RiskEngine` + backend will consume it unchanged.

Every signal, skip, rejection, fill, and exit is one JSONL line with a `reason` (`eventlog.py`).

## Post-trade lessons

`trading_agent.lessons` reviews closed trades and keeps a lesson for each. It never changes config.

```bash
uv run --group phase2 python -m trading_agent.lessons data/logs/events-live-*.jsonl --limit 20   # --dry-run to preview
```

- **Settle:** replays each JSONL log into closed trades (total P/L including partials and swap,
  `R = P/L / risk_amount`, `resolved` = time of the final exit). Open trades and backtest
  `END_OF_WINDOW` liquidations are skipped; a re-run appended to the same log replaces the earlier run. Live logs are
  settled together, so a trade opened before a restart and closed after it is still found.
- **Reflect:** one structured call per not-yet-reviewed trade (`--model`, default `anthropic:claude-haiku-4-5`)
  returns a 2-4 sentence lesson and, only if the trade points at one, a testable config hypothesis.
- **Record:** `data/lessons.jsonl` (idempotent by `run_id:trade_id`; a failed reflection is retried next pass), a
  dated section in `research/JOURNAL.md`, and hypotheses appended to `research/HYPOTHESES.md`, which the research
  loop already reads. `--source live` reviews paper/live trades only; `--no-research` skips the markdown.
- **Gate (opt-in):** `lessons: true` in `config/agents.yaml` appends the last `lessons_n` lessons on the instrument
  to the gate prompt. Only lessons whose trade had closed by the bar time are shown, so backtests stay
  point-in-time. The lessons are part of the prompt, so the verdict cache keys on them; with the flag off (or an
  empty store) prompts and cache keys are unchanged.

Adapted from [TradingAgents](https://github.com/TauricResearch/TradingAgents) (Apache-2.0) memory log, settlement
and reflection.

## Roadmap

Phases 1 and 2 are built. The original foundation spec (`docs/trading-system/forex_agent_foundation_spec.pdf`)
only defined Phase 1; everything below is derived from its 30-day build path and from what the code is still
waiting on. Scope guardrails from the spec still apply to every phase: **forex only, practice account only,
one session until stable, no gold/crypto/multi-asset.**

| Phase | Status | Summary |
|---|---|---|
| 1 | done | Deterministic money-core: OANDA data → Ichimoku rules → risk engine → simulated/paper execution → JSONL journal |
| 2 | done | deepagents confirmation gate (APPROVE/VETO, fail-closed) + research loop + walk-forward optimizer + locked holdout |
| 3 | **current** | Prove the edge and forward-test it |
| 4 | planned | Learn from real fills and widen scope |

### Phase 3 — prove the edge and forward-test it

Nothing has yet earned its way into `config/strategy_v1.yaml` through the full propose → walk-forward →
holdout chain, and `execution.backend` is still `simulated`. Phase 3 closes that gap.

1. **Score the proposed search spaces.** Run every `search_spaces/iter-*.json` from the latest research run
   through `optimize.run` (same `--trials`, `--seed`, folds) so the `research/opt_*.md` reports are comparable.
   Exit: one candidate chosen on plateau rank + DSR/PBO, not on the single best trial.
2. **Holdout once.** `optimize.holdout --report <winner>.json`. Exit: positive expectancy on 2026-07-01 → now
   with ≥ `--min-trades`. A fail means back to step 1 with a new research run — never `--allow-reuse`.
3. **Promote.** Write the recommended params into `strategy_v1.yaml`, bump `version`, commit. Every trade from
   here on is tagged with that version.
4. **Gate vs. no-gate on the same window.** Backtest the promoted config with `--strategy agent` (cached
   verdicts) and without, same dates, then score both with `gate_compare`. Exit: a written decision in `research/JOURNAL.md` on whether the LLM gate
   adds expectancy or only removes trades. If it doesn't help, live runs without it.
5. **Flip to OANDA paper.** `execution.backend: oanda` in `config/risk.yaml`, `uv run python -m trading_agent.run live`
   on the practice account for 4–8 weeks of London sessions. Track fills vs. simulated assumptions (spread,
   slippage, partial fills) and false positives. Exit: live expectancy within the holdout's bootstrap CI and
   no tripwire/risk-engine incidents.

### Phase 4 — learn from real fills and widen scope

Only starts after a config survives the Phase 3 forward test.

1. **LLM post-trade review.** Built as a manual step: `trading_agent.lessons` (see [Post-trade lessons](#post-trade-lessons))
   settles closed trades from the JSONL logs and appends lessons / hypotheses to `research/JOURNAL.md` and
   `research/HYPOTHESES.md`, and can optionally show past lessons to the gate. It writes markdown only; it never
   touches config. Remaining: run it automatically after each live session, so the chain is a closed loop:
   fills → lessons → hypotheses → search spaces → optimizer → holdout → config.
2. **Forward-test log.** Per-run markdown under `research/forward/` (one file per live run: config version,
   window, fills, deviation from backtest) so the paper record is reviewable without SQLite.
3. **Second pair.** Add one of EUR/USD, GBP/USD or USD/JPY to `pairs.yaml`, with its own spread file under
   `config/costs/`, and repeat Phase 3 steps 1–5 for it. Same setup, same session — one new variable at a time.
4. **Second setup or session** — only after the second pair is stable. Candidates already in the registry:
   `ichimoku_breakout`, `ichimoku_cloud_breakout`; or extend `sessions.yaml` to New York.

**Dropped:** the spec's Obsidian vault. Its job (setup cards, playbooks, hard rules, lessons) is already
served by plain markdown in `research/` and `docs/`, which the agents read and write directly through the
filesystem. No separate knowledge-base tool.

## Discipline mapping (from the spec)

- Max 1% risk/trade, max 2–3 trades/day, max daily-loss halt, kill switch → `risk.py` + `config/risk.yaml`
- Risk engine written before broker execution; OANDA paper backend built last, behind a flag
- LLM = reasoning, code = money → no LLM anywhere in Phase 1
- Observability → append-only JSONL, every skip/reject carries its reason
