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

## Configuration (versioned)

All rules live in `config/*.yaml` — `pairs`, `sessions`, `risk`, `strategy_v1`. Bump
`strategy_v1.yaml: version` on any rule change; every trade is tagged with the version that produced it.

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

## Discipline mapping (from the spec)

- Max 1% risk/trade, max 2–3 trades/day, max daily-loss halt, kill switch → `risk.py` + `config/risk.yaml`
- Risk engine written before broker execution; OANDA paper backend built last, behind a flag
- LLM = reasoning, code = money → no LLM anywhere in Phase 1
- Observability → append-only JSONL, every skip/reject carries its reason
