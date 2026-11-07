"""The practice-only guard: prefers the demo account, refuses live env / live (001-) accounts."""

from __future__ import annotations

import pytest

from trading_agent.oanda_env import LiveAccountRefused, practice_account_id, practice_environment

_LIVE = "001-001-21810409-001"
_DEMO = "101-001-12345678-001"


def test_prefers_demo_account_over_live(monkeypatch):
    monkeypatch.setenv("OANDA_ACCOUNT_ID", _LIVE)
    monkeypatch.setenv("OANDA_DEMO_ACCOUNT_ID", _DEMO)
    assert practice_account_id() == _DEMO


def test_falls_back_to_account_id_when_demo_absent(monkeypatch):
    monkeypatch.delenv("OANDA_DEMO_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("OANDA_ACCOUNT_ID", _DEMO)  # a 101- account in the fallback var is allowed
    assert practice_account_id() == _DEMO


def test_refuses_live_account(monkeypatch):
    monkeypatch.delenv("OANDA_DEMO_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("OANDA_ACCOUNT_ID", _LIVE)
    with pytest.raises(LiveAccountRefused):
        practice_account_id()


def test_refuses_missing_account(monkeypatch):
    monkeypatch.delenv("OANDA_DEMO_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)
    with pytest.raises(LiveAccountRefused):
        practice_account_id()


def test_environment_allows_practice(monkeypatch):
    monkeypatch.setenv("OANDA_ENV", "practice")
    assert practice_environment() == "practice"


def test_environment_refuses_live(monkeypatch):
    monkeypatch.setenv("OANDA_ENV", "live")
    with pytest.raises(LiveAccountRefused):
        practice_environment()
