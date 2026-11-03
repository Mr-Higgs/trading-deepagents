"""Search spaces: strategy params AND trade-management params; risk sizing / kill-switch stay fixed."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from trading_agent.config import AppConfig, ScaleLeg

# Never tunable: position sizing and the daily kill-switch are risk policy, not edge parameters.
FIXED_RISK_FIELDS = ("risk_pct", "max_daily_loss_pct", "max_trades_per_day", "kill_switch", "max_concurrent_positions", "leverage")

_STRATEGY_FIELDS = {
    "rr", "stop_rule", "stop_buffer_pips", "atr_length", "atr_mult", "exit_on_kijun_cross", "require_tk_momentum",
    "require_chikou", "require_htf_confluence", "require_regime", "min_trend_distance_pips", "h1_pullback_band", "breakout_ref", "breakout_lookback",
    "retest_band_pips", "entry_trigger", "tp_mode", "confluence_min_score", "fib_lookback", "swing_lookback", "breakout_trigger",
    "breakout_require_pattern", "min_tk_separation_pips", "min_kijun_slope_pips", "min_cloud_thickness_pips", "min_cloud_slope_pips", "quality_min_pass",
}
_MANAGEMENT_FIELDS = {"breakeven_enabled", "breakeven_trigger_r", "breakeven_trigger_pips", "breakeven_buffer_pips", "trail_mode", "trail_buffer_pips", "trail_distance_pips"}
_SPECIAL = {"partial_exit_pct"}  # fraction of the first scale-out leg; 0 disables scale-out
KINDS = ("float", "int", "cat", "bool")


@dataclass(frozen=True)
class Param:
    """One tunable dimension. `low`/`high` for numeric kinds, `choices` for `cat`."""

    name: str
    kind: str
    low: float = 0.0
    high: float = 1.0
    step: float | None = None
    choices: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            msg = f"param {self.name!r}: kind must be one of {KINDS}"
            raise ValueError(msg)
        if self.name not in _STRATEGY_FIELDS | _MANAGEMENT_FIELDS | _SPECIAL:
            msg = f"param {self.name!r} is not tunable (risk sizing / kill-switch are fixed: {FIXED_RISK_FIELDS})"
            raise ValueError(msg)
        if self.kind == "cat" and not self.choices:
            msg = f"param {self.name!r}: cat needs choices"
            raise ValueError(msg)
        if self.kind in ("float", "int") and self.high < self.low:
            msg = f"param {self.name!r}: high < low"
            raise ValueError(msg)


Space = tuple[Param, ...]

_MANAGEMENT_SPACE: Space = (
    Param("breakeven_enabled", "bool"),
    Param("breakeven_trigger_pips", "cat", choices=(0.0, 5.0, 10.0, 15.0)),  # > 0 overrides the R trigger
    Param("breakeven_trigger_r", "float", 0.5, 2.5, 0.25),
    Param("trail_mode", "cat", choices=("off", "kijun", "pips")),
    Param("trail_distance_pips", "float", 5.0, 30.0, 2.5),
    Param("partial_exit_pct", "cat", choices=(0.0, 0.25, 0.5, 0.75)),
)
_COMMON: Space = (
    Param("rr", "float", 1.0, 4.0, 0.25),
    Param("stop_rule", "cat", choices=("kijun", "cloud", "atr", "swing")),
    Param("swing_lookback", "int", 4, 20, 2),
    Param("atr_mult", "float", 1.0, 4.0, 0.25),
    Param("stop_buffer_pips", "float", 0.0, 10.0, 1.0),
    Param("require_tk_momentum", "bool"),
    Param("require_chikou", "bool"),
    Param("require_htf_confluence", "bool"),
    Param("require_regime", "bool"),
    Param("min_trend_distance_pips", "float", 0.0, 20.0, 2.0),
    Param("min_tk_separation_pips", "float", 0.0, 10.0, 1.0),
    Param("min_kijun_slope_pips", "float", 0.0, 10.0, 1.0),
    Param("min_cloud_thickness_pips", "float", 0.0, 20.0, 2.0),
    Param("min_cloud_slope_pips", "float", 0.0, 10.0, 1.0),
    Param("quality_min_pass", "int", 0, 4, 1),
)
DEFAULT_SPACES: dict[str, Space] = {
    "ichimoku_breakout": (
        *_COMMON,
        Param("breakout_trigger", "cat", choices=("tk_cross", "cloud", "kijun")),
        Param("breakout_require_pattern", "bool"),
        *_MANAGEMENT_SPACE,
    ),
    "ichimoku_pullback": (*_COMMON, Param("h1_pullback_band", "float", 0.0, 0.01, 0.001), Param("fib_lookback", "int", 20, 80, 10), *_MANAGEMENT_SPACE),
    "ichimoku_cloud_breakout": (*_COMMON, Param("breakout_lookback", "int", 3, 20), Param("retest_band_pips", "float", 2.0, 30.0, 2.0), *_MANAGEMENT_SPACE),
    "ichimoku_confluence": (*_COMMON, Param("confluence_min_score", "float", 2.0, 6.0, 0.5), Param("tp_mode", "cat", choices=("ichimoku_level", "rr")), *_MANAGEMENT_SPACE),
}
STRATEGY_ALIASES = {"breakout": "ichimoku_breakout", "pullback": "ichimoku_pullback", "cloud_breakout": "ichimoku_cloud_breakout", "confluence": "ichimoku_confluence"}


def resolve_strategy(name: str) -> str:
    """Map a CLI alias (`breakout`) to the registry name (`ichimoku_breakout`)."""
    return STRATEGY_ALIASES.get(name, name)


def sample(trial: Any, space: Space) -> dict[str, Any]:  # noqa: ANN401 - optuna.Trial, imported lazily
    """Draw one parameter set from an Optuna trial."""
    out: dict[str, Any] = {}
    for p in space:
        if p.kind == "float":
            out[p.name] = trial.suggest_float(p.name, p.low, p.high, step=p.step)
        elif p.kind == "int":
            out[p.name] = trial.suggest_int(p.name, int(p.low), int(p.high), step=int(p.step or 1))
        elif p.kind == "bool":
            out[p.name] = trial.suggest_categorical(p.name, (False, True))
        else:
            out[p.name] = trial.suggest_categorical(p.name, p.choices)
    return out


def apply_params(cfg: AppConfig, params: dict[str, Any]) -> AppConfig:
    """Return `cfg` with strategy/management overrides; risk sizing and kill-switch are left untouched."""
    bad = set(params) - (_STRATEGY_FIELDS | _MANAGEMENT_FIELDS | _SPECIAL)
    if bad:
        msg = f"untunable params: {sorted(bad)}"
        raise ValueError(msg)
    strat = replace(cfg.strategy, **{k: v for k, v in params.items() if k in _STRATEGY_FIELDS})
    mgmt = replace(cfg.risk.management, **{k: v for k, v in params.items() if k in _MANAGEMENT_FIELDS})
    if "partial_exit_pct" in params:
        mgmt = replace(mgmt, scale_out=_with_partial(mgmt.scale_out, float(params["partial_exit_pct"])))
    return replace(cfg, strategy=strat, risk=replace(cfg.risk, management=mgmt))


def _with_partial(scale: Any, pct: float) -> Any:  # noqa: ANN401 - ScaleOutConfig
    if pct <= 0:
        return replace(scale, enabled=False)
    legs = scale.legs or (ScaleLeg("kijun", pct),)
    return replace(scale, enabled=True, legs=(replace(legs[0], fraction=pct), *legs[1:]))


def space_to_json(space: Space) -> list[dict[str, Any]]:
    return [{"name": p.name, "kind": p.kind, "low": p.low, "high": p.high, "step": p.step, "choices": list(p.choices)} for p in space]


def space_from_json(items: list[dict[str, Any]]) -> Space:
    """Parse/validate a search space (the format `research_loop` proposes)."""
    return tuple(
        Param(d["name"], d["kind"], float(d.get("low", 0.0)), float(d.get("high", 1.0)), d.get("step"), tuple(d.get("choices") or ()))
        for d in items
    )


def load_space(path: str | Path) -> Space:
    data = json.loads(Path(path).read_text())
    return space_from_json(data["space"] if isinstance(data, dict) else data)


def normalize(space: Space, params: dict[str, Any]) -> tuple[tuple[float, ...], tuple[Any, ...]]:
    """Numeric params scaled to [0,1] and the categorical/bool values, for neighborhood distance."""
    num, cat = [], []
    for p in space:
        v = params[p.name]
        if p.kind in ("float", "int"):
            num.append(0.0 if p.high == p.low else (float(v) - p.low) / (p.high - p.low))
        else:
            cat.append(v)
    return tuple(num), tuple(cat)
