"""LangGraph entry `trade_loop`: runs the real trading loop and streams every event to the dashboard.

One node runs `engine.run(...)` to exhaustion with a `StreamingEventLog`, so each SIGNAL/FILL/EXIT/…
is pushed onto the LangGraph `custom` stream as it happens (single super-step - no recursion-limit
concern). Backtest replays a historical range deterministically; live polls OANDA. Same wiring as the
CLI (`run.py`), reused rather than duplicated.

Served via langgraph.json as `trade_loop`; `langgraph dev` loads `.env` so OANDA keys are present.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from trading_agent import engine
from trading_agent.backtest_setup import auto_news_windows, with_news_windows
from trading_agent.config import AppConfig, load_config
from trading_agent.news import RefreshingNewsWindows
from trading_agent.risk import RiskEngine
from trading_agent.run import _make_backend, _make_strategy, _simulated_backend, _utc_day
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
    strategy: str  # "config" | "deterministic" | "agent" (applies to backtest AND live)
    execution: str  # live: "simulated" | "oanda" (real 101- demo orders); backtest is always simulated
    agents: dict  # per-run overrides onto agents.yaml (mode/authority/analysts/models/cache/refresh) from the UI settings panel
    iterations: int  # research: how many propose->backtest->gate rounds
    model: str  # research: proposer model spec (init_chat_model)
    status: str
    final_equity: float


def _mask_account(acct: str) -> str:
    """101-004-12345678-001 -> 101-…-0001 (enough to confirm it's the practice account, not the full id)."""
    return f"{acct[:3]}…{acct[-4:]}" if len(acct) > 8 else acct


_AGENT_FIELDS = {"mode", "authority", "orchestrator_model", "analyst_model", "technical", "fundamental", "sentiment", "veto_default", "cftc_code", "cache", "max_tool_rounds"}


def _apply_agent_overrides(cfg, overrides: dict | None):
    """Overlay the UI's per-run agent settings onto cfg.agents (no YAML write). `refresh` is handled separately."""
    if not overrides:
        return cfg
    changes = {k: v for k, v in overrides.items() if k in _AGENT_FIELDS and v is not None}
    return replace(cfg, agents=replace(cfg.agents, **changes)) if changes else cfg


def _news_aware(cfg: AppConfig, mode: str, *, merge_ff: Callable[[Path], int] | None = None, root: Path | None = None, now: Callable[[], datetime] | None = None) -> AppConfig:
    """Append the auto news-blackout windows so dashboard runs gate like the CLI (`run.py`).

    Live runs get `RefreshingNewsWindows`: the feed refresh rides the live poll cadence (see
    `LiveOandaSource.on_poll`) after an initial forced refresh; a failed pull only warns —
    a flaky feed must never block the loop.
    """
    na = cfg.risk.news_auto
    if not na.enabled:
        return cfg
    if mode != "live":
        return with_news_windows(cfg, root=root)
    from trading_agent import calendar_build  # lazy: httpx

    base = root or _ROOT
    path = base / na.calendar
    windows = RefreshingNewsWindows(
        tuple(cfg.risk.news_windows),  # original manual windows; auto_news_windows must see this cfg, not the replaced one
        lambda: auto_news_windows(cfg, root=root),
        lambda: (merge_ff or calendar_build.merge_ff_week)(path),
        refresh_hours=na.refresh_hours,
        **({"now": now} if now else {}),
    )
    windows.refresh(force=True)
    return replace(cfg, risk=replace(cfg.risk, news_windows=windows))


def run_loop(state: LoopState) -> LoopState:
    cfg = _apply_agent_overrides(load_config(_CONFIG_DIR), state.get("agents"))
    refresh = bool((state.get("agents") or {}).get("refresh"))
    tfs = cfg.strategy.timeframes()
    equity = float(state.get("equity", 10_000.0))
    mode = state.get("mode", "backtest")

    # "research" = the backtest iteration loop (streams RESEARCH_META/ITER_*/RESEARCH_END). loop() persists
    # each run to data/research_runs/<run_id>/ + data/trading.db and sandboxes the config itself
    # (commit=False → the live config/ and git are never touched; those writes stay under gitignored,
    # watcher-safe data/). No temp-dir dance needed — the artifacts are the point now, not discarded.
    if mode == "research":
        from trading_agent import research_loop as rl

        rl.loop(int(state.get("iterations", 5)), state.get("model") or "anthropic:claude-sonnet-5", config_dir=str(_CONFIG_DIR), commit=False)
        return {"status": "done"}

    cfg = _news_aware(cfg, mode)

    from trading_agent.marketdata.oanda_client import OandaClient  # lazy: network stack

    client = OandaClient.from_env()  # routed through the practice tripwire (oanda_env.py)

    if mode == "live":
        from trading_agent.marketdata.live import LiveOandaSource

        exec_mode = state.get("execution") or cfg.risk.execution.backend
        source = LiveOandaSource(client, cfg.pair.symbol, tfs, poll_seconds=int(state.get("poll", 60)), on_poll=getattr(cfg.risk.news_windows, "refresh", None))
        strategy = _make_strategy(cfg, state.get("strategy", "config"), use_cache=False)  # live decisions are always fresh
        backend = _make_backend(cfg, override=exec_mode)  # "oanda" => real 101- demo orders (tripwire-guarded)
        run_id = f"live-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
        meta_extra = {"poll": int(state.get("poll", 60)), "strategy_mode": strategy.version, "authority": cfg.agents.authority}
    else:
        from trading_agent.marketdata.historical import HistoricalOandaSource

        exec_mode = "simulated"  # backtest cannot place historical orders
        start, end = state.get("start", "2026-05-01"), state.get("end", "2026-07-01")
        source = HistoricalOandaSource.from_oanda(client, cfg.pair.symbol, tfs, _utc_day(start), _utc_day(end), cache_dir=_CACHE_DIR)
        strategy = _make_strategy(cfg, state.get("strategy", "config"), refresh=refresh)  # deterministic, or the cached LLM decision layer
        backend = _simulated_backend(cfg)
        # Unique per run: EventLog appends + replay_today seeds day-state from the file, so a stable
        # bt-{start}-{end} name would double-count prior re-runs and trip spurious daily-loss halts.
        run_id = f"bt-{start}-{end}-{datetime.now(timezone.utc):%H%M%S}"
        meta_extra = {"start": start, "end": end, "strategy_mode": strategy.version, "authority": cfg.agents.authority}

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

    from trading_agent import store

    log_path = _LOG_DIR / f"events-{run_id}.jsonl"
    store.record_run(run_id, kind=mode, strategy_version=cfg.strategy_version, mode=exec_mode, started_at=datetime.now(timezone.utc).isoformat())
    log = StreamingEventLog(log_path, run_id=run_id, strategy_version=cfg.strategy_version)
    try:
        final = engine.run(source, strategy, RiskEngine(cfg.risk, cfg.pair), backend, log, cfg, starting_equity=equity)
    finally:
        log.close()
    # Index the finished run so it's queryable alongside research loops. Never let indexing break the run.
    try:
        from trading_agent.analytics import analyze

        summary = store.summarize_run(analyze(log_path))
        summary["final_equity"] = round(final, 2)
        store.finish_run(run_id, summary, finished_at=datetime.now(timezone.utc).isoformat())
    except Exception:  # noqa: BLE001 - the money path already ran; a failed index is cosmetic
        pass
    return {"status": "done", "final_equity": final}


loop = StateGraph(LoopState).add_node("run_loop", run_loop).add_edge(START, "run_loop").add_edge("run_loop", END).compile()
