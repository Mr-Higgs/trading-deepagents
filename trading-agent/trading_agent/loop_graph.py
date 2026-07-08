"""LangGraph entry `trade_loop`: runs the real trading loop and streams every event to the dashboard.

One node runs `engine.run(...)` to exhaustion with a `StreamingEventLog`, so each SIGNAL/FILL/EXIT/…
is pushed onto the LangGraph `custom` stream as it happens (single super-step - no recursion-limit
concern). Backtest replays a historical range deterministically; live polls OANDA. Same wiring as the
CLI (`run.py`), reused rather than duplicated.

Served via langgraph.json as `trade_loop`; `langgraph dev` loads `.env` so OANDA keys are present.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from trading_agent import engine
from trading_agent.config import load_config
from trading_agent.risk import RiskEngine
from trading_agent.run import _live_strategy, _make_backend, _simulated_backend, _utc_day
from trading_agent.strategy import build_strategy
from trading_agent.stream_log import StreamingEventLog

_ROOT = Path(__file__).resolve().parent.parent  # trading-agent/, cwd-independent
_CONFIG_DIR = _ROOT / "config"
_LOG_DIR = _ROOT / "data" / "logs"
_CACHE_DIR = _ROOT / "data" / "candles"


class LoopState(TypedDict, total=False):
    mode: str  # "backtest" (default) | "live"
    start: str  # backtest: inclusive UTC date, e.g. "2026-06-01"
    end: str  # backtest: exclusive UTC date
    poll: int  # live: seconds between polls
    equity: float  # starting equity
    strategy: str  # live: "config" | "deterministic" | "agent"
    execution: str  # live: "simulated" | "oanda" (real 101- demo orders); backtest is always simulated
    status: str
    final_equity: float


def _mask_account(acct: str) -> str:
    """101-004-12345678-001 -> 101-…-0001 (enough to confirm it's the practice account, not the full id)."""
    return f"{acct[:3]}…{acct[-4:]}" if len(acct) > 8 else acct


def run_loop(state: LoopState) -> LoopState:
    cfg = load_config(_CONFIG_DIR)
    tfs = {"trend": cfg.strategy.trend_tf, "bias": cfg.strategy.bias_tf, "entry": cfg.strategy.entry_tf}
    equity = float(state.get("equity", 10_000.0))
    mode = state.get("mode", "backtest")

    from trading_agent.marketdata.oanda_client import OandaClient  # lazy: network stack

    client = OandaClient.from_env()  # routed through the practice tripwire (oanda_env.py)

    if mode == "live":
        from trading_agent.marketdata.live import LiveOandaSource

        exec_mode = state.get("execution") or cfg.risk.execution.backend
        source = LiveOandaSource(client, cfg.pair.symbol, tfs, poll_seconds=int(state.get("poll", 60)))
        strategy = _live_strategy(cfg, state.get("strategy", "config"))
        backend = _make_backend(cfg, override=exec_mode)  # "oanda" => real 101- demo orders (tripwire-guarded)
        run_id = f"live-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
        meta_extra = {"poll": int(state.get("poll", 60)), "strategy_mode": state.get("strategy", "config")}
    else:
        from trading_agent.marketdata.historical import HistoricalOandaSource

        exec_mode = "simulated"  # backtest cannot place historical orders
        start, end = state.get("start", "2026-05-01"), state.get("end", "2026-07-01")
        source = HistoricalOandaSource.from_oanda(client, cfg.pair.symbol, tfs, _utc_day(start), _utc_day(end), cache_dir=_CACHE_DIR)
        strategy = build_strategy(cfg.strategy, cfg.pair)  # backtest is deterministic-only
        backend = _simulated_backend(cfg)
        # Unique per run: EventLog appends + replay_today seeds day-state from the file, so a stable
        # bt-{start}-{end} name would double-count prior re-runs and trip spurious daily-loss halts.
        run_id = f"bt-{start}-{end}-{datetime.now(timezone.utc):%H%M%S}"
        meta_extra = {"start": start, "end": end}

    # Authoritative source for the dashboard's execution badge (SIMULATED vs OANDA PAPER). Emitted before
    # the loop so the UI can label the run immediately. No engine.py change - rides the custom stream.
    account = None
    if exec_mode == "oanda":
        from trading_agent.oanda_env import practice_account_id

        account = _mask_account(practice_account_id())
    try:
        from langgraph.config import get_stream_writer

        get_stream_writer()(
            {
                "type": "RUN_META",
                "run_id": run_id,
                "strategy_version": cfg.strategy_version,
                "mode": mode,
                "backend": exec_mode,
                "account": account,
                "instrument": cfg.strategy.instrument,
                **meta_extra,
            }
        )
    except Exception:  # noqa: BLE001 - not in a graph run (e.g. imported/tested directly); skip the badge event
        pass

    log = StreamingEventLog(_LOG_DIR / f"events-{run_id}.jsonl", run_id=run_id, strategy_version=cfg.strategy_version)
    try:
        final = engine.run(source, strategy, RiskEngine(cfg.risk, cfg.pair), backend, log, cfg, starting_equity=equity)
    finally:
        log.close()
    return {"status": "done", "final_equity": final}


loop = StateGraph(LoopState).add_node("run_loop", run_loop).add_edge(START, "run_loop").add_edge("run_loop", END).compile()
