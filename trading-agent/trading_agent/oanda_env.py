"""Practice-only OANDA credential resolution + a hard live-account tripwire.

The system is paper-trading only (spec: never live). Live creds may sit in .env, but code must refuse
to touch a live account. OANDA v20 site-id convention: account ids start with '101-' (practice) or
'001-' (live). Both OANDA seams (candle client, paper backend) resolve credentials through here.
"""

from __future__ import annotations

import os

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_PRACTICE_PREFIX = "101-"  # OANDA v20: 101- = fxTrade Practice, 001- = live fxTrade


class LiveAccountRefused(RuntimeError):
    """Raised when the resolved OANDA environment or account is not a practice one."""


def practice_environment() -> str:
    """Return the OANDA environment, refusing anything but 'practice'."""
    env = os.environ.get("OANDA_ENV", "practice")
    if env != "practice":
        msg = f"OANDA_ENV={env!r}: this system is practice-only; refusing to run against a live environment"
        raise LiveAccountRefused(msg)
    return env


def practice_account_id() -> str:
    """Resolve the demo account id (preferred) and guard it to a 101- practice account."""
    acct = os.environ.get("OANDA_DEMO_ACCOUNT_ID") or os.environ.get("OANDA_ACCOUNT_ID", "")
    if not acct:
        msg = "no OANDA_DEMO_ACCOUNT_ID or OANDA_ACCOUNT_ID set"
        raise LiveAccountRefused(msg)
    if not acct.startswith(_PRACTICE_PREFIX):
        msg = f"account id {acct!r} is not a practice ({_PRACTICE_PREFIX}) account; refusing to place orders on a live account"
        raise LiveAccountRefused(msg)
    return acct


def with_retries(api):
    """Mount a urllib3 retry adapter on the API's requests.Session so a transient connection reset
    (a stale keep-alive socket on a long-lived live run → ECONNRESET, seen as
    ConnectionError(ProtocolError('Connection aborted.', ...))) retries idempotent GETs instead of
    killing the run. Order POSTs are NOT auto-retried (allowed_methods={GET}) — the money path stays
    fail-closed, no double-submit. Both OANDA seams route their API construction through here.
    """
    retry = Retry(total=4, connect=4, read=4, status=0, backoff_factor=1.0, allowed_methods=frozenset(["GET"]), raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retry)
    api.client.mount("https://", adapter)
    api.client.mount("http://", adapter)
    return api
