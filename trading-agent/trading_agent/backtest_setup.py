"""Build the realism components (cost model, financing, FX, news blackouts) from config.

Shared by the `run backtest` CLI and the optimizer so both simulate identically.
"""

from __future__ import annotations

import warnings
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from trading_agent.config import AppConfig
from trading_agent.costs import CostModel, SpreadModel, WideningProfile
from trading_agent.execution.simulated import SimulatedBackend
from trading_agent.financing import load_financing_yaml
from trading_agent.fx_convert import FxConverter, conversion_instrument
from trading_agent.news import build_news_windows, load_calendar


def build_cost_model(cfg: AppConfig) -> CostModel | None:
    """None for the legacy flat model; otherwise the configured hour-of-week spread + slippage."""
    ex = cfg.risk.execution
    if ex.price_mode == "legacy":
        return None
    if ex.spread_model == "fitted":
        if not ex.spread_table:
            msg = "execution.spread_model=fitted requires execution.spread_table"
            raise ValueError(msg)
        spread = SpreadModel.load(Path(cfg.config_dir or "config") / ex.spread_table)
    elif ex.spread_model == "profile":
        profile_kw = {k: (tuple(int(h) for h in v) if isinstance(v, list) else (int(v) if k.endswith("_hour") else v)) for k, v in ex.widening.items()}
        spread = SpreadModel.from_profile(ex.sim_spread_pips, WideningProfile(**profile_kw))
    else:
        spread = SpreadModel.flat(ex.sim_spread_pips)
    stop_slip = ex.sim_slippage_pips if ex.stop_slippage_pips is None else ex.stop_slippage_pips
    return CostModel(spread=spread, slippage_pips=ex.sim_slippage_pips, stop_slippage_pips=stop_slip)


def build_simulated_backend(cfg: AppConfig, fx: FxConverter | None = None) -> SimulatedBackend:
    ex = cfg.risk.execution
    fin = load_financing_yaml(Path(cfg.config_dir or "config") / "financing.yaml") if ex.financing else None
    return SimulatedBackend(
        cfg.pair.pip_size,
        ex.sim_spread_pips,
        ex.sim_slippage_pips,
        cost_model=build_cost_model(cfg),
        use_bid_ask=ex.price_mode == "bidask",
        financing=fin,
        fx=fx,
        quote_ccy=cfg.pair.quote_ccy,
        account_ccy=cfg.risk.account_ccy,
    )


def with_news_windows(cfg: AppConfig, *, root: Path | None = None) -> AppConfig:
    """Return `cfg` with auto-built blackout windows appended (no-op unless `news_auto.enabled`)."""
    na = cfg.risk.news_auto
    if not na.enabled:
        return cfg
    base = root or cfg.config_dir.parent
    if not (base / na.calendar).exists():
        warnings.warn(f"news_auto is enabled but {base / na.calendar} is missing: no news blackouts will apply", stacklevel=2)
    cal = load_calendar(base / na.calendar)
    auto = build_news_windows(cal, cfg.pair.symbol, minutes_before=na.minutes_before, minutes_after=na.minutes_after, impacts=na.impacts, impact_minutes=na.impact_minutes)
    return replace(cfg, risk=replace(cfg.risk, news_windows=(*cfg.risk.news_windows, *auto)))


def load_fx_converter(cfg: AppConfig, client: Any, start: datetime, end: datetime, *, cache_dir: str | None = None) -> FxConverter | None:
    """Point-in-time converter for quote->account P/L; `None` when the pair is quoted in the account currency.

    Args:
        cfg: App config (pair quote currency and account currency).
        client: `OandaClient`-like object exposing `candles_cached`.
        start: First bar needed.
        end: Exclusive end.
        cache_dir: Candle cache directory.
    """
    inst = conversion_instrument(cfg.pair.quote_ccy, cfg.risk.account_ccy)
    if inst is None:
        return None
    return FxConverter({inst: client.candles_cached(inst, "H1", start, end, cache_dir, price="M")})
