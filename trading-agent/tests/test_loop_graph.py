"""`trade_loop` graph news-blackout wiring (`_news_aware`). Calendar rows are TEST FIXTURES."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("langgraph")  # loop_graph imports langgraph at module level

from trading_agent.config import load_config
from trading_agent.loop_graph import _ROOT, _news_aware
from trading_agent.news import RefreshingNewsWindows

CSV = """time,currency,impact,title,before_min,after_min,source
2026-11-10T13:30:00Z,USD,High,NFP,,,forexfactory
"""


def _cfg_and_calendar(tmp_path: Path):
    cfg = load_config(_ROOT / "config")
    cal = tmp_path / cfg.risk.news_auto.calendar
    cal.parent.mkdir(parents=True, exist_ok=True)
    cal.write_text(CSV)
    return cfg


def test_backtest_gets_windows_without_ff_merge(tmp_path):
    cfg = _cfg_and_calendar(tmp_path)
    out = _news_aware(cfg, "backtest", merge_ff=lambda p: pytest.fail("must not merge in backtest"), root=tmp_path)
    assert len(out.risk.news_windows) == 1
    w = out.risk.news_windows[0]
    assert (w.start.strftime("%H:%M"), w.end.strftime("%H:%M")) == ("13:00", "14:30")  # risk.yaml High: [30, 60]


def test_live_merges_ff_then_builds_windows(tmp_path):
    cfg = _cfg_and_calendar(tmp_path)
    calls = []
    out = _news_aware(cfg, "live", merge_ff=lambda p: calls.append(p) or 0, root=tmp_path)
    assert calls == [tmp_path / cfg.risk.news_auto.calendar]  # forced refresh at startup
    assert isinstance(out.risk.news_windows, RefreshingNewsWindows)
    assert len(out.risk.news_windows) == 1


def test_live_ff_failure_warns_but_still_gates(tmp_path):
    cfg = _cfg_and_calendar(tmp_path)

    def boom(p):
        raise RuntimeError("boom")

    with pytest.warns(UserWarning, match="Forex Factory refresh failed"):
        out = _news_aware(cfg, "live", merge_ff=boom, root=tmp_path)
    assert len(out.risk.news_windows) == 1


def test_live_refresh_rides_poll(tmp_path):
    cfg = _cfg_and_calendar(tmp_path)
    clock = [datetime(2026, 11, 2, 12, 0, tzinfo=UTC)]
    merges = []
    out = _news_aware(cfg, "live", merge_ff=lambda p: merges.append(p) or 0, root=tmp_path, now=lambda: clock[0])
    windows = out.risk.news_windows
    assert len(merges) == 1  # the forced startup refresh

    assert windows.refresh() is False  # poll inside the cadence: cheap no-op
    clock[0] += timedelta(hours=7)
    assert windows.refresh() is True
    assert len(merges) == 2
    assert len(windows) == 1


def test_news_auto_disabled_returns_cfg_unchanged(tmp_path):
    cfg = _cfg_and_calendar(tmp_path)
    cfg = dataclasses.replace(cfg, risk=dataclasses.replace(cfg.risk, news_auto=dataclasses.replace(cfg.risk.news_auto, enabled=False)))
    assert _news_aware(cfg, "live", root=tmp_path) is cfg
