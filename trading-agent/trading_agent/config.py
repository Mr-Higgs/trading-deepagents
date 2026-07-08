"""Load and validate the versioned YAML configs into typed, frozen config objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Any

import yaml

_WEEKDAYS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


def _parse_time(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


@dataclass(frozen=True)
class PairConfig:
    symbol: str
    pip_location: int
    quote_ccy: str
    min_units: int
    max_units: int

    @property
    def pip_size(self) -> float:
        return 10.0**self.pip_location


@dataclass(frozen=True)
class SessionConfig:
    timezone: str
    start: time
    end: time
    weekdays: frozenset[int]


@dataclass(frozen=True)
class ExecutionConfig:
    backend: str  # "simulated" | "oanda"
    sim_spread_pips: float
    sim_slippage_pips: float


@dataclass(frozen=True)
class NewsWindow:
    start: datetime
    end: datetime
    reason: str


@dataclass(frozen=True)
class ScaleLeg:
    """One scale-out leg: bank `fraction` of the original position at an Ichimoku `level`."""

    level: str  # "kijun" | "span_a" | "span_b" | "tenkan"
    fraction: float


@dataclass(frozen=True)
class ScaleOutConfig:
    """Scaled exit: partial banks at Ichimoku levels, runner trails the remainder."""

    enabled: bool = False
    legs: tuple[ScaleLeg, ...] = ()
    move_be_after_first: bool = True  # after leg 1, move stop to breakeven + buffer
    runner_trail: str = "off"  # "off" | "kijun" | "pips" — trail mode for the final remainder


@dataclass(frozen=True)
class ManagementConfig:
    """In-trade stop management (defaults are no-ops => omitting the block = hold-to-stop/TP behavior)."""

    breakeven_enabled: bool = False  # master switch for move-to-breakeven + trailing
    breakeven_trigger_r: float = 1.0  # R-multiple BE arm when breakeven_trigger_pips == 0
    breakeven_trigger_pips: float = 0.0  # arm BE once price runs this many pips in favor (>0 => pip trigger wins)
    breakeven_buffer_pips: float = 1.0  # BE stop offset from entry (0 = true entry / risk-free at 0)
    trail_mode: str = "off"  # "off" | "kijun" | "pips" — how to trail once at breakeven
    trail_buffer_pips: float = 0.0  # (kijun trail) gap from the Kijun; 0 => fall back to breakeven_buffer_pips
    trail_distance_pips: float = 10.0  # trail distance when trail_mode == "pips"
    scale_out: ScaleOutConfig = field(default_factory=ScaleOutConfig)


@dataclass(frozen=True)
class RiskConfig:
    account_ccy: str
    risk_pct: float
    max_trades_per_day: int
    max_daily_loss_pct: float
    trading_day_reset_utc: time
    kill_switch: bool
    news_windows: tuple[NewsWindow, ...]
    execution: ExecutionConfig
    # v1.1 containment (defaults are no-ops so a config omitting them behaves exactly like v1.0):
    max_concurrent_positions: int = 1_000_000  # cap simultaneous open positions (kills correlated stacking)
    min_stop_pips: float = 0.0  # reject signals whose stop is tighter than this (avoids oversized positions + cost drag)
    min_rr: float = 1.0  # reject signals whose reward/risk ratio is below this floor
    management: ManagementConfig = field(default_factory=ManagementConfig)


@dataclass(frozen=True)
class IchimokuParams:
    tenkan: int
    kijun: int
    senkou_b: int
    displacement: int

    @property
    def min_rows(self) -> int:
        """Rows needed before the cloud at the newest bar is fully defined."""
        return self.senkou_b + self.displacement


@dataclass(frozen=True)
class StrategyConfig:
    version: str
    instrument: str
    trend_tf: str
    bias_tf: str
    entry_tf: str
    ichimoku: IchimokuParams
    h1_pullback_band: float
    m15_trigger: tuple[str, ...]  # candlestick pattern(s) that trigger entry; any match fires
    stop_rule: str  # "kijun" | "cloud" | "atr"
    stop_buffer_pips: float
    rr: float
    min_trend_distance_pips: float = 0.0  # v1.1: price must clear the cloud by this much (skip thin-cloud chop); 0 = no filter
    # v1.2 (defaults are no-ops so a config omitting them behaves exactly like v1.1):
    name: str = "ichimoku_pullback"  # which strategy: "ichimoku_pullback" | "ichimoku_breakout"
    require_tk_momentum: bool = False  # Tenkan/Kijun must agree with direction
    require_chikou: bool = False  # Chikou (close) vs price 26 bars ago must confirm
    breakout_ref: str = "cloud"  # ichimoku_breakout entry ref: "cloud" | "base"
    # v2.0 WFX:
    atr_length: int = 14  # ATR lookback for the volatility stop (only used when stop_rule == "atr")
    atr_mult: float = 2.75  # stop distance = atr(atr_length) * atr_mult (WFX default)
    exit_on_kijun_cross: bool = False  # WFX early exit: close a position on a Kijun / Tenkan-Kijun cross against it
    # v3.0 confluence (only used when name == "ichimoku_confluence"; no-op defaults otherwise):
    confluence_min_score: float = 4.0  # min |weighted multi-TF vote| to allow a trade
    confluence_signals: tuple[str, ...] = ("kijun", "span_a", "span_b", "cloud")  # price-vs-level votes to count
    entry_trigger: str = "pullback"  # which inner strategy triggers: "pullback" | "breakout"
    tp_mode: str = "ichimoku_level"  # "ichimoku_level" (next level top-down) | "rr" (inner's R:R TP)
    require_thinning_cloud: bool = False  # reserved: favor breakouts when the M15 cloud is diminishing


@dataclass(frozen=True)
class AgentsConfig:
    """Phase-2 LLM confirmation gate. Defaults => deterministic; agents.yaml is optional."""

    mode: str = "deterministic"  # "deterministic" | "agent"
    orchestrator_model: str = "anthropic:claude-opus-4-8"
    analyst_model: str = "anthropic:claude-haiku-4-5"
    technical: bool = True
    fundamental: bool = True
    sentiment: bool = True
    veto_default: bool = False
    cftc_code: str = "232741"


@dataclass(frozen=True)
class AppConfig:
    pairs: dict[str, PairConfig]
    sessions: tuple[SessionConfig, ...]  # active sessions; a bar trades if it falls in ANY of them
    risk: RiskConfig
    strategy: StrategyConfig
    config_dir: Path
    agents: AgentsConfig = field(default_factory=AgentsConfig)

    @property
    def strategy_version(self) -> str:
        return self.strategy.version

    @property
    def pair(self) -> PairConfig:
        return self.pairs[self.strategy.instrument]


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def load_config(config_dir: str | Path) -> AppConfig:
    config_dir = Path(config_dir)
    pairs_raw = _load_yaml(config_dir / "pairs.yaml")
    sessions_raw = _load_yaml(config_dir / "sessions.yaml")
    risk_raw = _load_yaml(config_dir / "risk.yaml")
    strat_raw = _load_yaml(config_dir / "strategy_v1.yaml")

    pairs = {
        sym: PairConfig(
            symbol=v.get("oanda_symbol", sym),
            pip_location=int(v["pip_location"]),
            quote_ccy=v["quote_ccy"],
            min_units=int(v["min_units"]),
            max_units=int(v["max_units"]),
        )
        for sym, v in pairs_raw.items()
    }

    # Each named block is a session preset (its own timezone); `active` lists which to trade.
    presets = {
        name: SessionConfig(
            timezone=blk["timezone"],
            start=_parse_time(blk["start"]),
            end=_parse_time(blk["end"]),
            weekdays=frozenset(_WEEKDAYS[d] for d in blk["weekdays"]),
        )
        for name, blk in sessions_raw.items()
        if name != "active" and isinstance(blk, dict)
    }
    active_names = sessions_raw.get("active") or ["london"]
    missing = [n for n in active_names if n not in presets]
    if missing:
        msg = f"sessions.yaml: active session(s) {missing} not defined (have {sorted(presets)})"
        raise ValueError(msg)
    sessions = tuple(presets[n] for n in active_names)

    ex = risk_raw["execution"]
    execution = ExecutionConfig(
        backend=ex["backend"],
        sim_spread_pips=float(ex["sim_spread_pips"]),
        sim_slippage_pips=float(ex["sim_slippage_pips"]),
    )
    news = tuple(
        NewsWindow(
            start=datetime.fromisoformat(w["start"]),
            end=datetime.fromisoformat(w["end"]),
            reason=w.get("reason", "news"),
        )
        for w in (risk_raw.get("news_windows") or [])
    )
    mgmt_raw = risk_raw.get("management") or {}
    scale_raw = mgmt_raw.get("scale_out") or {}
    scale_legs = tuple(
        ScaleLeg(level=str(leg["level"]), fraction=float(leg["fraction"]))
        for leg in (scale_raw.get("legs") or [])
    )
    scale_out = ScaleOutConfig(
        enabled=bool(scale_raw.get("enabled", False)),
        legs=scale_legs,
        move_be_after_first=bool(scale_raw.get("move_be_after_first", True)),
        runner_trail=str(scale_raw.get("runner_trail", "off")),
    )
    management = ManagementConfig(
        breakeven_enabled=bool(mgmt_raw.get("breakeven_enabled", False)),
        breakeven_trigger_r=float(mgmt_raw.get("breakeven_trigger_r", 1.0)),
        breakeven_trigger_pips=float(mgmt_raw.get("breakeven_trigger_pips", 0.0)),
        breakeven_buffer_pips=float(mgmt_raw.get("breakeven_buffer_pips", 1.0)),
        trail_mode=str(mgmt_raw.get("trail_mode", "off")),
        trail_buffer_pips=float(mgmt_raw.get("trail_buffer_pips", 0.0)),
        trail_distance_pips=float(mgmt_raw.get("trail_distance_pips", 10.0)),
        scale_out=scale_out,
    )
    risk = RiskConfig(
        account_ccy=risk_raw["account_ccy"],
        risk_pct=float(risk_raw["risk_pct"]),
        max_trades_per_day=int(risk_raw["max_trades_per_day"]),
        max_daily_loss_pct=float(risk_raw["max_daily_loss_pct"]),
        trading_day_reset_utc=_parse_time(risk_raw["trading_day_reset_utc"]),
        kill_switch=bool(risk_raw["kill_switch"]),
        news_windows=news,
        execution=execution,
        max_concurrent_positions=int(risk_raw.get("max_concurrent_positions", 1_000_000)),
        min_stop_pips=float(risk_raw.get("min_stop_pips", 0.0)),
        min_rr=float(risk_raw.get("min_rr", 1.0)),
        management=management,
    )

    tf = strat_raw["timeframes"]
    ich = strat_raw["ichimoku"]
    setup = strat_raw["setup"]
    strategy = StrategyConfig(
        version=str(strat_raw["version"]),
        instrument=strat_raw["instrument"],
        trend_tf=tf["trend"],
        bias_tf=tf["bias"],
        entry_tf=tf["entry"],
        ichimoku=IchimokuParams(
            tenkan=int(ich["tenkan"]),
            kijun=int(ich["kijun"]),
            senkou_b=int(ich["senkou_b"]),
            displacement=int(ich["displacement"]),
        ),
        h1_pullback_band=float(setup["h1_pullback_band"]),
        m15_trigger=tuple(setup["m15_trigger"]) if isinstance(setup["m15_trigger"], list) else (setup["m15_trigger"],),
        stop_rule=setup["stop_rule"],
        stop_buffer_pips=float(setup["stop_buffer_pips"]),
        rr=float(setup["rr"]),
        min_trend_distance_pips=float(setup.get("min_trend_distance_pips", 0.0)),
        name=str(strat_raw.get("name", "ichimoku_pullback")),
        require_tk_momentum=bool(setup.get("require_tk_momentum", False)),
        require_chikou=bool(setup.get("require_chikou", False)),
        breakout_ref=str(setup.get("breakout_ref", "cloud")),
        atr_length=int(setup.get("atr_length", 14)),
        atr_mult=float(setup.get("atr_mult", 2.75)),
        exit_on_kijun_cross=bool(setup.get("exit_on_kijun_cross", False)),
        confluence_min_score=float(setup.get("confluence_min_score", 4.0)),
        confluence_signals=tuple(setup["confluence_signals"]) if isinstance(setup.get("confluence_signals"), list) else ("kijun", "span_a", "span_b", "cloud"),
        entry_trigger=str(setup.get("entry_trigger", "pullback")),
        tp_mode=str(setup.get("tp_mode", "ichimoku_level")),
        require_thinning_cloud=bool(setup.get("require_thinning_cloud", False)),
    )

    if strategy.instrument not in pairs:
        msg = f"strategy instrument {strategy.instrument!r} not defined in pairs.yaml"
        raise ValueError(msg)

    agents_path = config_dir / "agents.yaml"
    agents_raw = _load_yaml(agents_path) if agents_path.exists() else {}
    an = agents_raw.get("analysts") or {}
    agents = AgentsConfig(
        mode=agents_raw.get("mode", "deterministic"),
        orchestrator_model=agents_raw.get("orchestrator_model", "anthropic:claude-opus-4-8"),
        analyst_model=agents_raw.get("analyst_model", "anthropic:claude-haiku-4-5"),
        technical=bool(an.get("technical", True)),
        fundamental=bool(an.get("fundamental", True)),
        sentiment=bool(an.get("sentiment", True)),
        veto_default=bool(agents_raw.get("veto_default", False)),
        cftc_code=str(agents_raw.get("cftc_code", "232741")),
    )

    return AppConfig(pairs=pairs, sessions=sessions, risk=risk, strategy=strategy, config_dir=config_dir, agents=agents)
