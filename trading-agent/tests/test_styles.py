"""Trading styles expand to the triple-screen stack; unsupported granularities are rejected."""

import re
import shutil
from pathlib import Path

import pytest

from trading_agent.config import load_config

REAL_CONFIG = Path(__file__).resolve().parent.parent / "config"


def _config(tmp_path: Path, transform) -> Path:
    """Copy the real config dir, apply `transform` to strategy_v1.yaml text, return the dir."""
    dst = tmp_path / "config"
    shutil.copytree(REAL_CONFIG, dst)
    strat = dst / "strategy_v1.yaml"
    strat.write_text(transform(strat.read_text()))
    return dst


def _set_style(line: str):
    return lambda text: re.sub(r"(?m)^style:.*$", line, text)


def test_scalping_style_expands_stack(tmp_path: Path) -> None:
    cfg = load_config(_config(tmp_path, _set_style("style: scalping")))
    s = cfg.strategy
    assert (s.trend_tf, s.bias_tf, s.entry_tf) == ("M15", "M5", "M1")
    assert s.style == "scalping"


def test_intraday_style_enters_on_m15(tmp_path: Path) -> None:
    cfg = load_config(_config(tmp_path, _set_style("style: intraday")))
    s = cfg.strategy
    assert (s.trend_tf, s.bias_tf, s.entry_tf) == ("H4", "H1", "M15")


def test_position_style_expands_stack(tmp_path: Path) -> None:
    cfg = load_config(_config(tmp_path, _set_style("style: position")))
    s = cfg.strategy
    assert (s.trend_tf, s.bias_tf, s.entry_tf) == ("D", "H4", "H1")


def test_unknown_style_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown style"):
        load_config(_config(tmp_path, _set_style("style: bogus")))


def test_explicit_timeframes_used_when_style_omitted(tmp_path: Path) -> None:
    # drop the style line -> fall back to the explicit `timeframes:` block (H4/H1/M15)
    cfg = load_config(_config(tmp_path, _set_style("")))
    assert (cfg.strategy.trend_tf, cfg.strategy.entry_tf) == ("H4", "M15")
    assert cfg.strategy.style == "custom"


def test_unsupported_granularity_rejected(tmp_path: Path) -> None:
    # no style, and a hand-typed timeframes block asking for W1 (not fetchable) -> clear error
    def transform(text: str) -> str:
        text = re.sub(r"(?m)^style:.*$", "", text)
        return re.sub(r"(?m)^(\s*entry:).*$", r"\1 W", text)

    with pytest.raises(ValueError, match="not a fetchable granularity"):
        load_config(_config(tmp_path, transform))
