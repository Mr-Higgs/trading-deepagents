"""News after-window sweep helpers. Calendar rows and event logs are TEST FIXTURES."""

from __future__ import annotations

import json
from pathlib import Path

from trading_agent.config import load_config
from trading_agent.news import load_calendar
from trading_agent.news_compare import high_event_times, post_release_stats, with_variant

_ROOT = Path(__file__).resolve().parents[1]
CSV = """time,currency,impact,title,before_min,after_min,source
2026-11-10T13:30:00Z,USD,High,NFP,,,fred:50
2026-11-11T19:00:00Z,USD,High,FOMC Statement,30,90,federalreserve.gov
2026-11-12T00:30:00Z,AUD,Medium,Monthly CPI,,,abs
"""


def _cfg(tmp_path: Path):
    cfg = load_config(_ROOT / "config")
    cal = tmp_path / cfg.risk.news_auto.calendar
    cal.parent.mkdir(parents=True, exist_ok=True)
    cal.write_text(CSV)
    return cfg


def test_with_variant_none_disables_news_auto(tmp_path):
    cfg = with_variant(_cfg(tmp_path), None, root=tmp_path)
    assert not cfg.risk.news_auto.enabled
    assert tuple(cfg.risk.news_windows) == ()


def test_with_variant_changes_only_high_after_and_keeps_row_overrides(tmp_path):
    cfg = with_variant(_cfg(tmp_path), (30, 0), root=tmp_path)
    spans = {w.reason: (w.start.strftime("%m-%d %H:%M"), w.end.strftime("%m-%d %H:%M")) for w in cfg.risk.news_windows}
    assert spans["USD NFP"] == ("11-10 13:00", "11-10 13:30")  # pre-release only
    assert spans["USD FOMC Statement"] == ("11-11 18:30", "11-11 20:30")  # row override (30/90) wins
    assert spans["AUD Monthly CPI"] == ("11-12 00:15", "11-12 00:45")  # Medium untouched (15/15)


def test_post_release_stats_picks_trades_submitted_after_high_events(tmp_path):
    cal = load_calendar(_make_calendar(tmp_path))
    events = high_event_times(cal, "AUD_USD")
    assert len(events) == 2
    log = tmp_path / "events.jsonl"
    recs = [
        *_trade("s1", "2026-11-10T13:45:00+00:00", "2026-11-10T15:00:00+00:00", pnl=150.0),  # 15 min after NFP
        *_trade("s2", "2026-11-10T13:00:00+00:00", "2026-11-10T13:20:00+00:00", pnl=-50.0),  # before NFP
        *_trade("s3", "2026-11-11T19:45:00+00:00", "2026-11-11T21:00:00+00:00", pnl=-100.0),  # 45 min after FOMC
        *_trade("s4", "2026-11-10T15:00:00+00:00", "2026-11-10T16:00:00+00:00", pnl=80.0),  # 90 min after: outside
    ]
    log.write_text("".join(json.dumps(r) + "\n" for r in recs))
    stats = post_release_stats(log, events)
    assert stats == {"post_n": 2, "post_pnl": 50.0, "post_win_rate": 0.5, "post_avg_R": 0.25}


def _make_calendar(tmp_path: Path) -> Path:
    p = tmp_path / "cal.csv"
    p.write_text(CSV)
    return p


def _trade(sid: str, submit: str, exit_ts: str, *, pnl: float) -> list[dict]:
    tid = f"{sid}-t1"
    return [
        {"type": "SIGNAL", "signal_id": sid, "ts": submit, "direction": "LONG", "setup_id": "x", "instrument": "AUD_USD", "entry_ref": 1.0, "reason": "r", "features": {}, "strategy_version": "t"},
        {"type": "SUBMIT", "trade_id": tid, "signal_id": sid, "ts": submit, "stop": 0.99, "tp": 1.02, "risk_amount": 100.0, "units": 1},
        {"type": "EXIT", "trade_id": tid, "ts": exit_ts, "price": 1.0, "pnl": pnl, "exit_reason": "TP"},
    ]
