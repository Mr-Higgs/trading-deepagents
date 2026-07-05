"""Load and validate the versioned YAML configs into typed, frozen config objects."""

from __future__ import annotations

from dataclasses import dataclass
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
class RiskConfig:
    account_ccy: str
    risk_pct: float
    max_trades_per_day: int
    max_daily_loss_pct: float
    trading_day_reset_utc: time
    kill_switch: bool
    news_windows: tuple[NewsWindow, ...]
    execution: ExecutionConfig


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
    m15_trigger: str
    stop_rule: str  # "kijun" | "cloud"
    stop_buffer_pips: float
    rr: float


@dataclass(frozen=True)
class AppConfig:
    pairs: dict[str, PairConfig]
    session: SessionConfig
    risk: RiskConfig
    strategy: StrategyConfig
    config_dir: Path

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

    london = sessions_raw["london"]
    session = SessionConfig(
        timezone=sessions_raw["timezone"],
        start=_parse_time(london["start"]),
        end=_parse_time(london["end"]),
        weekdays=frozenset(_WEEKDAYS[d] for d in london["weekdays"]),
    )

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
    risk = RiskConfig(
        account_ccy=risk_raw["account_ccy"],
        risk_pct=float(risk_raw["risk_pct"]),
        max_trades_per_day=int(risk_raw["max_trades_per_day"]),
        max_daily_loss_pct=float(risk_raw["max_daily_loss_pct"]),
        trading_day_reset_utc=_parse_time(risk_raw["trading_day_reset_utc"]),
        kill_switch=bool(risk_raw["kill_switch"]),
        news_windows=news,
        execution=execution,
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
        m15_trigger=setup["m15_trigger"],
        stop_rule=setup["stop_rule"],
        stop_buffer_pips=float(setup["stop_buffer_pips"]),
        rr=float(setup["rr"]),
    )

    if strategy.instrument not in pairs:
        msg = f"strategy instrument {strategy.instrument!r} not defined in pairs.yaml"
        raise ValueError(msg)

    return AppConfig(pairs=pairs, session=session, risk=risk, strategy=strategy, config_dir=config_dir)
