"""confluence_exit: close an open position when the top-down score turns against it (config-gated).

The score math (signals.topdown_confluence) is exercised via the combined strategy tests; here we verify the
exit decision + execution seam in isolation by stubbing the score.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from trading_agent import trade_management as tm
from trading_agent.types import OpenTrade

UTC = timezone.utc
NOW = datetime(2026, 1, 6, 8, 0, tzinfo=UTC)


class _Backend:
    def __init__(self, positions):
        self._pos = positions
        self.closed: list[str] = []

    def open_positions(self):
        return self._pos

    def request_close(self, trade_id):
        self.closed.append(trade_id)


class _Log:
    def write(self, *a, **k):
        pass


def _long():
    return OpenTrade("t1", "AUD_USD", 1000, entry=0.67, stop=0.665, init_stop=0.665, tp=0.68, remaining_units=1000)


def _short():
    return OpenTrade("s1", "AUD_USD", -1000, entry=0.67, stop=0.675, init_stop=0.675, tp=0.66, remaining_units=-1000)


def _cfg(enabled=True, threshold=0.0):
    return SimpleNamespace(strategy=SimpleNamespace(exit_on_confluence_flip=enabled, exit_confluence_threshold=threshold))


def _stub_score(monkeypatch, score):
    monkeypatch.setattr(tm.signals, "topdown_confluence", lambda snap, strat: (score, {}, {}))


def test_long_closes_when_score_flips_negative(monkeypatch):
    _stub_score(monkeypatch, -1)
    be = _Backend([_long()])
    tm.confluence_exit(be, {}, NOW, _cfg(), _Log())
    assert be.closed == ["t1"]


def test_long_held_while_score_positive(monkeypatch):
    _stub_score(monkeypatch, 5)
    be = _Backend([_long()])
    tm.confluence_exit(be, {}, NOW, _cfg(), _Log())
    assert be.closed == []


def test_short_closes_when_score_flips_positive(monkeypatch):
    _stub_score(monkeypatch, 1)
    be = _Backend([_short()])
    tm.confluence_exit(be, {}, NOW, _cfg(), _Log())
    assert be.closed == ["s1"]


def test_threshold_exits_on_decay(monkeypatch):
    # long still weakly bullish (score 1) but below the decay threshold (2) => exit early
    _stub_score(monkeypatch, 1)
    be = _Backend([_long()])
    tm.confluence_exit(be, {}, NOW, _cfg(threshold=2.0), _Log())
    assert be.closed == ["t1"]


def test_disabled_is_noop(monkeypatch):
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        return (-9, {}, {})

    monkeypatch.setattr(tm.signals, "topdown_confluence", _boom)
    be = _Backend([_long()])
    tm.confluence_exit(be, {}, NOW, _cfg(enabled=False), _Log())
    assert be.closed == [] and called["n"] == 0  # gated out before computing the score
