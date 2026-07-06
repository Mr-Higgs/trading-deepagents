"""Practice-only OANDA credential resolution + a hard live-account tripwire.

The system is paper-trading only (spec: never live). Live creds may sit in .env, but code must refuse
to touch a live account. OANDA v20 site-id convention: account ids start with '101-' (practice) or
'001-' (live). Both OANDA seams (candle client, paper backend) resolve credentials through here.
"""

from __future__ import annotations

import os

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
