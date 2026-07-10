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

# FRED CSV download (no API key). One fetch shape per series; a wrong/retired id fails soft to
# "unavailable" so the gate never crashes on a data hiccup. Series chosen to cover the AQR themes.
_FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
_FRED_SERIES = {  # label -> FRED series id
    "US policy rate": "DFF",  # effective fed funds (daily)
    "AU policy rate": "IR3TIB01AUM156N",  # Australia 3-month interbank (RBA-cash proxy, monthly)
    "Metals index": "PMETAINDEXM",  # IMF global metals price index (AUD terms-of-trade proxy, monthly)
    "VIX": "VIXCLS",  # CBOE volatility index (risk sentiment, daily)
}


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
    """Compact events summary (Forex Factory shape), high-impact first.

    Each event: {title, country (=currency), impact 'High'|'Medium'|'Low', date, forecast, previous,
    actual}. A populated `actual` marks a RECENT release -> we show actual-vs-forecast (the "surprise"
    that drives the Systematic-Macro business-cycle / monetary-policy read); an empty `actual` marks an
    UPCOMING event -> we show forecast/prev (the event risk the trade may walk into).
    """
    if not events:
        return "No scheduled high-impact economic events in the window."

    def rank(e: dict) -> int:
        return _IMPACT_RANK.get(str(e.get("impact", "")).lower(), 0)

    ordered = sorted(events, key=lambda e: (rank(e), str(e.get("date", ""))), reverse=True)
    high = [e for e in ordered if rank(e) >= 3]
    lines = []
    for e in ordered[:8]:
        head = f"- {e.get('title', 'event')} [{e.get('country', '')}, {e.get('impact', '?')}]"
        actual, fc, pv = e.get("actual") or "", e.get("forecast") or "", e.get("previous") or ""
        if actual:  # a release already out -> the surprise vs forecast
            detail = f" actual {actual} vs forecast {fc}" if fc else f" actual {actual}"
        elif fc or pv:  # still upcoming -> event risk
            detail = f" (forecast {fc} / prev {pv})"
        else:
            detail = ""
        lines.append(f"{head}{detail} @ {e.get('date', '')}")
    return f"{len(high)} high-impact of {len(events)} events:\n" + "\n".join(lines)


def summarize_macro(data: dict[str, list[float]]) -> str:
    """Compact macro-context read from a few recent time series (chronological, latest last).

    Maps the AQR Macro-Momentum themes to free series: policy rates (monetary policy), a metals index
    (international trade / AUD terms of trade), and VIX (macro sentiment). Reports each level + its
    recent direction, plus the rate differential (carry). Pure/testable; the fetch wrapper feeds it.
    """
    if not any(data.get(k) for k in data):
        return "Macro context unavailable."

    def read(label: str) -> tuple[float, str] | None:
        vals = data.get(label) or []
        if not vals:
            return None
        latest = vals[-1]
        trend = ""
        if len(vals) >= 2:
            trend = " rising" if latest > vals[-2] else " falling" if latest < vals[-2] else " flat"
        return latest, trend

    us, au = read("US policy rate"), read("AU policy rate")
    metals, vix = read("Metals index"), read("VIX")
    lines = []
    if us and au:
        diff = au[0] - us[0]
        carry = "AUD carry positive" if diff > 0 else "AUD carry negative" if diff < 0 else "carry flat"
        lines.append(f"- Monetary policy: AU {au[0]:g}%{au[1]} vs US {us[0]:g}%{us[1]} => differential {diff:+.2f}pp ({carry})")
    elif us or au:
        r = us or au
        lines.append(f"- Monetary policy: {'US' if us else 'AU'} rate {r[0]:g}%{r[1]}")
    if metals:
        lines.append(f"- Terms of trade (metals index): {metals[0]:g}{metals[1]}")
    if vix:
        mood = "risk-off" if vix[0] >= 20 else "risk-on"
        lines.append(f"- Macro sentiment (VIX): {vix[0]:g}{vix[1]} ({mood})")
    return "Macro context (latest + recent direction):\n" + "\n".join(lines) if lines else "Macro context unavailable."


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
def economic_calendar(instrument: str = "AUD_USD", hours_ahead: int = 48, hours_back: int = 72) -> str:
    """High-impact economic events for an instrument's currencies (Forex Factory): recent releases
    (actual vs forecast = the Systematic-Macro surprise) plus upcoming events (the event risk).

    Args:
        instrument: instrument symbol, e.g. AUD_USD (events for BOTH legs are included).
        hours_ahead: look-ahead window in hours (upcoming event risk).
        hours_back: look-back window in hours (recent releases already out).
    """
    currencies = set(instrument.replace("/", "_").split("_"))
    try:
        resp = httpx.get(_FF_CAL_URL, headers={"User-Agent": _UA}, timeout=10.0, follow_redirects=True)
        resp.raise_for_status()
        now = datetime.now(timezone.utc)
        horizon, floor = now + timedelta(hours=hours_ahead), now - timedelta(hours=hours_back)
        window = []
        for e in resp.json():
            if e.get("country") not in currencies or str(e.get("impact", "")).lower() != "high":
                continue
            try:
                when = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
            except (KeyError, TypeError, ValueError):
                continue
            if floor <= when <= horizon:
                window.append(e)
        return summarize_calendar(window)
    except Exception:  # noqa: BLE001 - any failure degrades to neutral; the gate must not crash on a flaky feed
        return "Economic calendar unavailable."


def _fred_recent(series_id: str, cosd: str, keep: int = 6) -> list[float]:
    """Last few valid observations of a FRED series via the no-auth CSV download. Missing values are
    dot-marked ('.') and skipped. Raises on any HTTP/parse failure (the caller degrades to a string)."""
    resp = httpx.get(_FRED_CSV_URL, params={"id": series_id, "cosd": cosd}, timeout=10.0, follow_redirects=True)
    resp.raise_for_status()
    vals: list[float] = []
    for line in resp.text.splitlines()[1:]:  # skip the "observation_date,SERIES" header
        cell = line.split(",")[-1].strip()
        if cell and cell != ".":
            try:
                vals.append(float(cell))
            except ValueError:
                continue
    return vals[-keep:]


@tool(parse_docstring=True)
def macro_context(instrument: str = "AUD_USD") -> str:
    """Current macro backdrop for a forex pair: policy-rate differential, terms-of-trade (metals),
    and risk sentiment (VIX) - the live inputs for the AQR Systematic-Macro themes. Free FRED data.

    Args:
        instrument: instrument symbol, e.g. AUD_USD (used to label; series are AU/US + global proxies).
    """
    try:
        cosd = (datetime.now(timezone.utc) - timedelta(days=400)).date().isoformat()  # bound CSV size
        data = {}
        for label, series_id in _FRED_SERIES.items():
            try:
                data[label] = _fred_recent(series_id, cosd)
            except Exception:  # noqa: BLE001 - one dead series must not sink the whole read
                continue
        return summarize_macro(data)
    except Exception:  # noqa: BLE001 - any failure degrades to neutral; the gate must not crash on a flaky feed
        return "Macro context unavailable."
