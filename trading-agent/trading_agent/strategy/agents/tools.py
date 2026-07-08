"""Analyst data tools: Forex Factory economic calendar + CFTC COT positioning.

Each tool is an httpx fetch wrapped around a pure `summarize_*` parser (the parsers are what the
tests exercise, against canned JSON — the network wrapper is validated in the live dry-run, exactly
like `tests/test_oanda_paper_parse.py` treats the OANDA transaction stream). Any fetch failure
degrades to a plain "unavailable" string so a flaky feed never crashes the gate.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from langchain_core.tools import tool

_CFTC_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
# Forex Factory weekly calendar (free, no auth). Replaced OANDA's retired labs calendar (now 403).
# ponytail: current week only — misses events past this week for large hours_ahead; merge
# ff_calendar_nextweek.json if you ever need a longer horizon than a few days.
_FF_CAL_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_UA = "Mozilla/5.0 (trading-agent economic-calendar)"
_IMPACT_RANK = {"high": 3, "medium": 2, "low": 1}  # Forex Factory impact strings


def summarize_cot(rows: list[dict]) -> str:
    """Latest CFTC non-commercial net positioning from a Socrata COT response."""
    if not rows:
        return "COT data unavailable."
    r = rows[0]
    long_ = int(float(r.get("noncomm_positions_long_all", 0) or 0))
    short = int(float(r.get("noncomm_positions_short_all", 0) or 0))
    net = long_ - short
    report_date = str(r.get("report_date_as_yyyy_mm_dd", "?"))[:10]
    lean = "net long" if net > 0 else "net short" if net < 0 else "flat"
    return f"CFTC COT (report {report_date}): non-commercials {long_} long / {short} short => {lean} {abs(net)} contracts."


def summarize_calendar(events: list[dict]) -> str:
    """Compact upcoming-events summary (Forex Factory shape), high-impact first.

    Each event: {title, country (=currency), impact 'High'|'Medium'|'Low', date, forecast, previous}.
    """
    if not events:
        return "No scheduled high-impact economic events in the window."

    def rank(e: dict) -> int:
        return _IMPACT_RANK.get(str(e.get("impact", "")).lower(), 0)

    ordered = sorted(events, key=rank, reverse=True)
    high = [e for e in ordered if rank(e) >= 3]
    lines = []
    for e in ordered[:8]:
        fc, pv = e.get("forecast") or "", e.get("previous") or ""
        fp = f" (forecast {fc} / prev {pv})" if (fc or pv) else ""
        lines.append(f"- {e.get('title', 'event')} [{e.get('country', '')}, {e.get('impact', '?')}]{fp} @ {e.get('date', '')}")
    return f"{len(high)} high-impact of {len(events)} upcoming events:\n" + "\n".join(lines)


@tool(parse_docstring=True)
def cftc_cot(cftc_code: str = "232741") -> str:
    """Latest CFTC Commitments of Traders speculative positioning for a currency future.

    Args:
        cftc_code: CFTC contract market code (Australian dollar = 232741).
    """
    try:
        resp = httpx.get(
            _CFTC_URL,
            params={"cftc_contract_market_code": cftc_code, "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 1},
            timeout=10.0,
        )
        resp.raise_for_status()
        return summarize_cot(resp.json())
    except Exception:  # noqa: BLE001 - any failure degrades to neutral; the gate must not crash on a flaky feed
        return "COT data unavailable."


@tool(parse_docstring=True)
def economic_calendar(instrument: str = "AUD_USD", hours_ahead: int = 48) -> str:
    """Upcoming high-impact economic events for an instrument's currencies (Forex Factory).

    Args:
        instrument: instrument symbol, e.g. AUD_USD (events for BOTH legs are included).
        hours_ahead: look-ahead window in hours.
    """
    currencies = set(instrument.replace("/", "_").split("_"))
    try:
        resp = httpx.get(_FF_CAL_URL, headers={"User-Agent": _UA}, timeout=10.0, follow_redirects=True)
        resp.raise_for_status()
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=hours_ahead)
        upcoming = []
        for e in resp.json():
            if e.get("country") not in currencies or str(e.get("impact", "")).lower() != "high":
                continue
            try:
                when = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
            except (KeyError, TypeError, ValueError):
                continue
            if now <= when <= horizon:
                upcoming.append(e)
        return summarize_calendar(upcoming)
    except Exception:  # noqa: BLE001 - any failure degrades to neutral; the gate must not crash on a flaky feed
        return "Economic calendar unavailable."
