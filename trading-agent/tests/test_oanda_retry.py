"""with_retries mounts a GET-only retry adapter so a transient OANDA connection reset on a long-lived
live run retries idempotent reads instead of killing the run — while order POSTs are never auto-retried
(no double-submit). No network / no oandapyV20 needed: it only touches the requests.Session."""

import requests

from trading_agent.oanda_env import with_retries


class _FakeAPI:
    """Minimal stand-in for oandapyV20.API — has the `.client` requests.Session with_retries hardens."""

    def __init__(self) -> None:
        self.client = requests.Session()


def test_with_retries_hardens_reads_not_orders() -> None:
    api = with_retries(_FakeAPI())
    retry = api.client.get_adapter("https://api-fxpractice.oanda.com/").max_retries
    assert retry.total >= 3, "idempotent reads retry transient connection resets"
    assert retry.connect and retry.read, "retries cover connect + read (ECONNRESET) errors"
    assert "GET" in retry.allowed_methods, "candle poll / reconcile GETs retry"
    assert "POST" not in retry.allowed_methods, "orders must never auto-retry (double-submit risk)"


def test_with_retries_returns_same_api() -> None:
    api = _FakeAPI()
    assert with_retries(api) is api, "returns the same object for chaining"
