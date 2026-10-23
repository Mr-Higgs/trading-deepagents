# Trading System — Research Findings

_Last updated: 2026-07-10. A synthesis of what we've learned building the `trading-agent/`
forex paper-trading system. Written honestly, conclusions first. The per-run numbers behind
these conclusions live in `trading-agent/research/` and `trading-agent/data/reports/`; this
doc distills them — it is not a chronology._

---

## TL;DR

1. **No proven positive edge yet.** Every deterministic Ichimoku variant we've built
   (pullback → breakout → WFX → confluence → combined) **loses money over a full year on
   AUD/USD.** Short windows occasionally show a small profit — those are favorable-window
   noise, not edge.
2. **The exit is the leak, not the entry.** On the best variant, STOPs beat TPs roughly
   **4:1**; trades die before they arm break-even. The single biggest improvement to date
   came from an *exit* change (Kijun-cross), not any entry filter.
3. **AI belongs in research and veto — never at the trigger.** The LLM's job is (a) offline
   hypothesis generation + reasoning in the research loop, and (b) an online *fail-closed
   veto* of a code-computed signal. It never sets prices or sizes. Giving it trade authority
   would add a slow, un-backtestable layer on top of a strategy that has to earn its edge in
   code first.
4. **Risk/quality filters cut drawdown; they are not alpha.** Concurrency caps, min-stop,
   min-R:R, the confluence gate, the H4 direction lock — all reduce bleed and improve trade
   quality. None of them *created* a positive expectancy.
5. **Win rate ≠ profit.** A 51% win rate with a 0.34 profit factor still bleeds. The research
   loop optimizes **expectancy ($/trade)**, deliberately not win rate.
6. **Finding a real entry edge (or a regime/pair/session filter that gates one) is THE open
   problem.** More rules on the same signal have not fixed it.

---

## 1. Philosophy & architecture

**"The model reasons, code owns the money."** All price math, sizing, and risk enforcement
live in deterministic Python. The LLM only ever *reasons about* a trade the code already
computed.

- **One decision loop, two seams** (`trading_agent/engine.py`): backtest and live share
  `engine.run` byte-for-byte. Only two injected objects differ — the `MarketDataSource`
  (historical vs live OANDA) and the `ExecutionBackend` (`SimulatedBackend` vs
  `OandaPaperBackend`). The decision clock is each *closed* M15 bar's `close_time`, never the
  wall clock → **backtests are deterministic and lookahead-safe**.
- **Practice-only tripwire** (`trading_agent/oanda_env.py`): requires `OANDA_ENV=practice`
  AND an account id starting `101-`; anything else raises `LiveAccountRefused`. Both OANDA
  seams route credentials through it. No real money is reachable by construction.
- **Versioned config** (`config/*.yaml` → frozen dataclasses in `config.py`): every trade is
  tagged with the strategy version.

## 2. The AI-role decision

The recurring question — *"do we refine the strategy then hand the LLM control?"* — has a
settled answer: **no.** An LLM has no price-prediction alpha, is slow and non-deterministic,
and its trade-by-trade decisions can't be cheaply backtested. Its leverage is in two places,
both already built:

| Role | Where | What it can do | Guardrail |
|---|---|---|---|
| **Offline researcher** | `trading_agent/research_loop.py` | Propose ONE strategy-config change per iteration, reason over the metrics report | Code backtests it; **code** (not the model) decides keep/revert |
| **Online veto gate** | `trading_agent/strategy/agent_strategy.py` (`gate` mode) | Approve or veto a code-computed `Signal` | **Fail-closed**: any LLM/network error/timeout = veto = no trade; prices byte-identical on approve |

**Not allowed:** the LLM setting entry/stop/tp or size. A `signal`-authority mode exists in
`agent_strategy.py` but is **kept off** — it stays a config flag, not a live behavior, until
(and only if) it's ever proven out in backtest, which is unlikely to be worth it.

## 3. Strategy research — the honest record

All figures AUD/USD, real OANDA candles, simulated fills with modeled spread+slippage.
**Read every "positive" short-window result as noise until it survives a full year.**

| Variant | What changed | Representative result | Verdict |
|---|---|---|---|
| **v1.0 → v1.1** | Concurrency cap + min-stop + min-trend-distance | May–Jul '26: −24% / 4 halts / 16% win → **−3.4% / 0 halts / 29% win** (maxDD 2411 → 541) | Risk *containment* — halves upside, cuts downside ~85%. No edge. |
| **v1.2** | TK-momentum + Chikou + candlestick-set confirmations | 12-mo: pullback −7.0% (35% win), v1.2 **−8.7%** (34%), breakout −20.6% | Confirmations did **not** help. |
| **WFX v2.0** | TK-cross entry, ATR×2.75 stop, 2.55R, 9/26/52/26 periods (TradingView port) | Mar–Jul '26 −1.5%; the *old* breakout it replaced was +1.8% same window; both −20%+ over 12mo | "Hardening" **removed** the small window-dependent edge. |
| **`ichimoku_confluence`** | Top-down multi-TF vote gate + H4 lock wrapping an inner trigger | Mar–Jul '26 confluence(breakout) **+0.3%, 51% win, 35 trades** (first net-positive config) | Edge did **not** hold: 12-mo OOS all lose; best relative (−17% vs −25% base), ~1/3 less bleed. |
| **`ichimoku_combined`** | One strategy running all triggers, top-down score picks direction, PA gate | Win A (6mo) PF 0.40, Win B (1yr) PF 0.46 — best PF of the family, **both still net-negative** | Better quality filter, no alpha. |

**Diagnosis (the important part):** on `ichimoku_combined`, **STOPs beat TPs ~4:1** — trades
die before break-even arms. The leak is the *exit*, not the entry.

The v2.0.0 research-loop baseline makes the point numerically: 2025 in-sample, **306 trades,
51.3% win rate, but −20.47 expectancy, profit factor 0.34** (avg loss −1.11R vs avg win
0.34R; only 29/306 reached the 3R target). High win rate, deeply unprofitable.

## 4. What helps vs what doesn't

**Helps (drawdown / quality — not alpha):**
- Concurrency cap, `min_stop_pips`, `min_rr` floor (`config/risk.yaml`) → fewer correlated
  losers, no oversized positions, every trade ≥2:1.
- Confluence gate + H4 hard direction filter → ~40% fewer trades, higher win rate, lowest
  drawdown of the family.
- **Kijun-cross exit (`exit_on_kijun_cross`) — the one real lever.** On `ichimoku_combined`
  it flipped Window A (6mo) from PF 0.44 → **1.91** (maxDD 310 → 77) and improved Window B
  (1yr) PF 0.53 → 0.63, net −350 → −163, **maxDD 525 → 165** — it replaces ~16 full stop-outs
  with early MANUAL closes. (Caveat: earlier it *hurt* on WFX-breakout over ~4mo; it clearly
  helps on `combined` over two windows. Window-dependent — not yet a green light.)

**Doesn't create edge:**
- Extra entry confirmations (TK-momentum, Chikou, candlestick sets) — v1.2 was worse.
- Break-even + trail management — raises win rate but **lowers expectancy** (BE at +1R clips
  winners that would've run to 2R): 12-mo pullback no-mgmt −871 → BE-only −1532 → BE+trail
  −1341.
- The confluence-flip exit — the H4-weighted score is too slow to cut a fast loser.

## 5. Edge-discovery methodology

The honest way to find an edge is to let the LLM *propose* and let code *judge*, hard.

- **The research loop** (`trading_agent/research_loop.py`): each iteration a fresh deep-agent
  reads the latest metrics report + hypotheses + journal, probes ideas with a read-only
  `backtest` tool, and proposes ONE config change. Then **deterministic code** runs the
  in-sample + out-of-sample backtests and applies the keep/revert gate. The model never
  touches the verdict.
- **The hardened gate (built 2026-07-10)** — the anti-overfit spine. A change is adopted only
  if it:
  1. is **positive in ≥2 of 3 out-of-sample folds** (walk-forward-lite over 2026-01…07), not
     one lucky window;
  2. survives a **bootstrap luck test** — the 5th percentile of resampled per-trade
     expectancy is > 0 (`analytics.bootstrap_expectancy`, fixed seed = deterministic);
  3. sits on a **parameter plateau** — the accepted numeric value stays OOS-positive at ±1
     step (`research_loop.plateau_ok`), rejecting curve-fit spikes.

  This closed a real hole: the old gate hill-climbed against a *single* 4-month OOS window,
  which silently became a training set. Verified on real data — the losing committed baseline
  is now correctly rejected (per-fold expectancy `[-69.3, -22.6, -29.2]`, folds-positive 0/3,
  bootstrap p05 −45.3).

Run it: `cd trading-agent && uv run python -m trading_agent.research_loop --iterations 10`
(`--no-commit` for a dry run; a UI-triggered run sandboxes onto a copy of `config/`).

## 6. Data, metrics & tooling

- **`trading_agent/analytics.py`** — the honest metrics engine: expectancy (headline),
  profit factor, R-multiples, max drawdown, per-bucket breakdowns (setup / exit-reason /
  direction / hour), a SKIP/REJECT attribution histogram, and MAE/MFE per trade (the tell for
  stops-too-tight / targets-too-far). Win rate is reported but never the headline.
- **Determinism** — cached candles under `data/candles/`; LLM verdicts are cached so agent
  backtests re-run free and deterministic.
- **`trading_agent/store.py`** — SQLite read-model indexing runs/iterations and a versioned
  strategy library ranked by out-of-sample expectancy.
- **`trading-agent/research/`** — `HYPOTHESES.md` (the tunable-lever backlog), `JOURNAL.md`
  (per-iteration log), `latest_report.md` (**stale** — a saved snapshot of the losing v2.0.0
  baseline; current run reports land under `data/research_runs/<id>/`).

## 7. Open problems & next steps

**The core unsolved problem: no proven entry edge.** More rules on the same Ichimoku signal
have not produced one. Candidate directions, roughly in order:

1. **Attack the exit, since that's the leak** — the Kijun-cross exit result suggests exit
   logic is where expectancy is won or lost on this family. Validate it robustly through the
   hardened gate.
2. **A regime / session / pair filter** — the strategy may only have an edge in specific
   conditions; find the filter rather than a new entry.
3. **A genuinely different entry trigger** — not another Ichimoku confirmation.
4. **Validate the online veto gate in backtest** — deterministic vs Phase-2-gated (via cached
   verdicts) on expectancy/drawdown, before it ever runs live. Only worthwhile once the base
   strategy is positive.

**What we have NOT done (be honest about the gaps):**
- No config/strategy has passed the hardened OOS gate — the committed strategy still loses.
- Cost/slippage stress is **not** yet part of the gate (needs a `cfg.risk` execution-override
  path; `research_loop._apply` currently only replaces `cfg.strategy`).
- Single instrument (AUD/USD), single pair of OOS windows. No second instrument, no rolling
  anchored multi-fold grid, no Sharpe/Sortino.
- The online LLM veto gate has been validated *live* for plumbing (a real `Verdict` returned),
  but never *backtested for value*.

---

_Reference material (in `docs/trading-system/`): `forex_agent_foundation_spec.pdf` (the
"LLM reasons, code owns money" spec), `Ichimoku.pdf`, `tradingview-script.md` (the WFX port
source), `AQR Fundamental Trends...pdf` (the macro doctrine for the LLM fundamental analyst),
plus the candlestick / price-action / confluence / top-down PDFs. `docs/design.md` is the
system design doc._
