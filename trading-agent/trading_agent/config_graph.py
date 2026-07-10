"""LangGraph `config`: read (get) / write (set) the risk + strategy YAML from the dashboard.

`get` returns the current editable config so the UI forms can prefill. `set` VALIDATES money-critical
scalars, rejects out-of-range input WITHOUT writing, and otherwise rewrites only the changed keys in place,
preserving comments/formatting. `trade_loop` calls `load_config` fresh per run, so the next run picks up edits.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, TypedDict

import yaml
from langgraph.graph import END, START, StateGraph

from trading_agent.config import load_config
from trading_agent.strategy.patterns import TRIGGER_NAMES
from trading_agent.strategy.price_action import PA_SIGNALS

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_DIR = _ROOT / "config"
_RISK_YAML = _CONFIG_DIR / "risk.yaml"
_STRAT_YAML = _CONFIG_DIR / "strategy_v1.yaml"
_SESSIONS_YAML = _CONFIG_DIR / "sessions.yaml"

# Editable keys -> validation spec. num: (lo, hi); choice: allowed set; bool/str type markers.
_RISK_RULES: dict[str, Any] = {
    "risk_pct": ("num", 0.0, 0.05),
    "max_trades_per_day": ("int", 0, 50),
    "max_concurrent_positions": ("int", 1, 50),
    "min_stop_pips": ("num", 0.0, 200.0),
    "min_rr": ("num", 0.0, 10.0),
    "max_daily_loss_pct": ("num", 0.0, 0.5),
    "kill_switch": ("bool",),
    # trade management
    "breakeven_enabled": ("bool",),
    "breakeven_trigger_r": ("num", 0.0, 10.0),
    "breakeven_trigger_pips": ("num", 0.0, 200.0),
    "breakeven_buffer_pips": ("num", 0.0, 100.0),
    "trail_mode": ("choice", {"off", "kijun", "pips"}),
    "trail_buffer_pips": ("num", 0.0, 100.0),
    "trail_distance_pips": ("num", 0.0, 500.0),
}
_PATTERN_NAMES = set(TRIGGER_NAMES)  # single source of truth: whatever patterns.matches recognizes
_CONFLUENCE_SIGNALS = {"kijun", "tenkan", "span_a", "span_b", "cloud"}
_STRAT_RULES: dict[str, Any] = {
    "version": ("str",),
    "name": ("choice", {"ichimoku_pullback", "ichimoku_breakout", "ichimoku_confluence"}),
    "style": ("choice", {"scalping", "intraday", "swing", "position"}),  # expands to the triple-screen stack in config.py
    "rr": ("num", 0.1, 10.0),
    "stop_rule": ("choice", {"kijun", "cloud", "atr"}),
    "stop_buffer_pips": ("num", 0.0, 100.0),
    "atr_length": ("int", 1, 200),
    "atr_mult": ("num", 0.1, 10.0),
    "exit_on_kijun_cross": ("bool",),
    "confluence_min_score": ("num", 0.0, 20.0),
    "confluence_signals": ("patterns", _CONFLUENCE_SIGNALS),
    "entry_trigger": ("choice", {"pullback", "breakout"}),
    "tp_mode": ("choice", {"ichimoku_level", "rr"}),
    "require_thinning_cloud": ("bool",),
    "h1_pullback_band": ("num", 0.0, 0.05),
    "min_trend_distance_pips": ("num", 0.0, 200.0),
    "m15_trigger": ("patterns", _PATTERN_NAMES),
    "require_tk_momentum": ("bool",),
    "require_chikou": ("bool",),
    "require_candle_trigger": ("bool",),
    "require_price_action": ("bool",),
    "price_action_signals": ("patterns", set(PA_SIGNALS)),
    "swing_lookback": ("int", 1, 10),
    "sr_tolerance_pips": ("num", 0.0, 100.0),
    "breakout_ref": ("choice", {"cloud", "base"}),
    "tenkan": ("int", 1, 200),
    "kijun": ("int", 1, 200),
    "senkou_b": ("int", 1, 200),
    "displacement": ("int", 1, 200),
}


class ConfigState(TypedDict, total=False):
    action: str  # "get" (default) | "set"
    risk: dict[str, Any]
    strategy: dict[str, Any]
    sessions: dict[str, Any]  # {"active": [...], "available": [...]}
    ok: bool
    errors: list[str]


def _session_presets() -> list[str]:
    """Named session blocks defined in sessions.yaml (everything except the `active` list)."""
    raw = yaml.safe_load(_SESSIONS_YAML.read_text()) or {}
    return [n for n in raw if n != "active" and isinstance(raw[n], dict)]


def _current() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    cfg = load_config(_CONFIG_DIR)
    r, s = cfg.risk, cfg.strategy
    risk = {
        "risk_pct": r.risk_pct,
        "max_trades_per_day": r.max_trades_per_day,
        "max_concurrent_positions": r.max_concurrent_positions,
        "min_stop_pips": r.min_stop_pips,
        "min_rr": r.min_rr,
        "max_daily_loss_pct": r.max_daily_loss_pct,
        "kill_switch": r.kill_switch,
        "breakeven_enabled": r.management.breakeven_enabled,
        "breakeven_trigger_r": r.management.breakeven_trigger_r,
        "breakeven_trigger_pips": r.management.breakeven_trigger_pips,
        "breakeven_buffer_pips": r.management.breakeven_buffer_pips,
        "trail_mode": r.management.trail_mode,
        "trail_buffer_pips": r.management.trail_buffer_pips,
        "trail_distance_pips": r.management.trail_distance_pips,
        "account_ccy": r.account_ccy,  # read-only (for display)
    }
    strat = {
        "version": s.version,
        "instrument": s.instrument,  # read-only
        "style": s.style,  # editable dropdown; drives the three *_tf below
        "trend_tf": s.trend_tf,  # read-only (derived from style)
        "bias_tf": s.bias_tf,  # read-only (derived from style)
        "entry_tf": s.entry_tf,  # read-only (derived from style)
        "name": s.name,
        "m15_trigger": list(s.m15_trigger),
        "require_tk_momentum": s.require_tk_momentum,
        "require_chikou": s.require_chikou,
        "require_candle_trigger": s.require_candle_trigger,
        "require_price_action": s.require_price_action,
        "price_action_signals": list(s.price_action_signals),
        "swing_lookback": s.swing_lookback,
        "sr_tolerance_pips": s.sr_tolerance_pips,
        "breakout_ref": s.breakout_ref,
        "rr": s.rr,
        "stop_rule": s.stop_rule,
        "stop_buffer_pips": s.stop_buffer_pips,
        "atr_length": s.atr_length,
        "atr_mult": s.atr_mult,
        "exit_on_kijun_cross": s.exit_on_kijun_cross,
        "confluence_min_score": s.confluence_min_score,
        "confluence_signals": list(s.confluence_signals),
        "entry_trigger": s.entry_trigger,
        "tp_mode": s.tp_mode,
        "require_thinning_cloud": s.require_thinning_cloud,
        "h1_pullback_band": s.h1_pullback_band,
        "min_trend_distance_pips": s.min_trend_distance_pips,
        "tenkan": s.ichimoku.tenkan,
        "kijun": s.ichimoku.kijun,
        "senkou_b": s.ichimoku.senkou_b,
        "displacement": s.ichimoku.displacement,
    }
    raw = yaml.safe_load(_SESSIONS_YAML.read_text()) or {}
    sessions = {"active": list(raw.get("active") or []), "available": _session_presets()}
    return risk, strat, sessions


def _validate(rules: dict[str, Any], data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    clean: dict[str, Any] = {}
    errors: list[str] = []
    for k, v in data.items():
        spec = rules.get(k)
        if spec is None:
            errors.append(f"'{k}' is not editable")
            continue
        kind = spec[0]
        try:
            if kind == "bool":
                clean[k] = bool(v)
            elif kind == "choice":
                if v not in spec[1]:
                    errors.append(f"'{k}' must be one of {sorted(spec[1])}")
                else:
                    clean[k] = v
            elif kind == "patterns":
                names = [v] if isinstance(v, str) else list(v)
                bad = [n for n in names if n not in spec[1]]
                if not names or bad:
                    errors.append(f"'{k}' must be a non-empty subset of {sorted(spec[1])}")
                else:
                    clean[k] = names
            elif kind == "str":
                sv = str(v).strip()
                if not sv or len(sv) > 40:
                    errors.append(f"'{k}' must be a non-empty string <= 40 chars")
                else:
                    clean[k] = sv
            elif kind == "int":
                iv = int(v)
                if not (spec[1] <= iv <= spec[2]):
                    errors.append(f"'{k}' must be an integer in [{spec[1]}, {spec[2]}]")
                else:
                    clean[k] = iv
            else:  # num
                fv = float(v)
                if not (spec[1] <= fv <= spec[2]):
                    errors.append(f"'{k}' must be a number in [{spec[1]}, {spec[2]}]")
                else:
                    clean[k] = fv
        except (TypeError, ValueError):
            errors.append(f"'{k}' has the wrong type")
    return clean, errors


def _fmt(k: str, v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[" + ", ".join(str(x) for x in v) + "]"  # YAML flow list
    if k == "version":
        return f'"{v}"'  # keep the quoted style
    return str(v)


def _set_scalar(text: str, key: str, value: str) -> str:
    """Replace the value of a unique top-level-or-nested `key:` line, preserving indentation + trailing comment.

    ponytail: works because every editable key name is unique within each file; the assert fails loud if that
    ever stops holding (upgrade path: a round-trip YAML lib like ruamel.yaml).
    """
    pat = re.compile(rf"^(?P<pre>[ \t]*{re.escape(key)}:[ \t]*)(?P<val>[^#\n]*?)(?P<post>[ \t]*(?:#.*)?)$", re.M)
    if len(pat.findall(text)) != 1:
        msg = f"expected exactly one '{key}:' line in the config file"
        raise ValueError(msg)
    return pat.sub(lambda m: f"{m.group('pre')}{value}{m.group('post')}", text, count=1)


def configure(state: ConfigState) -> ConfigState:
    if state.get("action") != "set":
        risk, strat, sessions = _current()
        return {"ok": True, "risk": risk, "strategy": strat, "sessions": sessions, "errors": []}

    risk_in, strat_in = state.get("risk") or {}, state.get("strategy") or {}
    sess_in = state.get("sessions") or {}
    risk_clean, e1 = _validate(_RISK_RULES, risk_in)
    strat_clean, e2 = _validate(_STRAT_RULES, strat_in)
    # Session presets come from the YAML itself, so adding a preset there needs no code change.
    sess_clean, e3 = _validate({"active": ("patterns", set(_session_presets()))}, sess_in)
    errors = e1 + e2 + e3
    if errors:
        return {"ok": False, "errors": errors}

    if risk_clean:
        txt = _RISK_YAML.read_text()
        for k, v in risk_clean.items():
            txt = _set_scalar(txt, k, _fmt(k, v))
        _RISK_YAML.write_text(txt)
    if strat_clean:
        txt = _STRAT_YAML.read_text()
        for k, v in strat_clean.items():
            txt = _set_scalar(txt, k, _fmt(k, v))
        _STRAT_YAML.write_text(txt)
    if sess_clean:
        txt = _SESSIONS_YAML.read_text()
        for k, v in sess_clean.items():  # only "active"
            txt = _set_scalar(txt, k, _fmt(k, v))
        _SESSIONS_YAML.write_text(txt)

    load_config(_CONFIG_DIR)  # validate the files still parse after the edit
    risk, strat, sessions = _current()
    return {"ok": True, "risk": risk, "strategy": strat, "sessions": sessions, "errors": []}


config = StateGraph(ConfigState).add_node("configure", configure).add_edge(START, "configure").add_edge("configure", END).compile()
