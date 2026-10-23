"""Analyst data tools: Forex Factory economic calendar + CFTC COT positioning.

Point-in-time safety: every tool takes `as_of` (ISO-8601 UTC). In a backtest the engine's bar
timestamp is installed with `backtest_clock(now)` and used whenever the model omits `as_of`. With a
clock (or explicit `as_of`) the tools ONLY return data that was published by then: COT by release
date (Friday after the Tuesday as-of date), FRED via ALFRED vintages, the calendar from the stored
CSV. If no point-in-time source is available the tool returns "unavailable" - never current data.

Each tool is an httpx fetch wrapped around a pure `summarize_*` parser (the parsers are what the
tests exercise, against canned JSON — the network wrapper is validated in the live dry-run, exactly
like `tests/test_oanda_paper_parse.py` treats the OANDA transaction stream). Any fetch failure
degrades to a plain "unavailable" string so a flaky feed never crashes the gate.
"""

from __future__ import annotations

import contextlib
import contextvars
import math
import os
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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


_ALFRED_URL = "https://api.stlouisfed.org/fred/series/observations"
# COT positions are as of Tuesday; CFTC publishes them Friday 15:30 ET (~19:30-20:30 UTC). Use the
# later bound so a backtest never sees a report before it was actually public.
COT_PUBLICATION_LAG = timedelta(days=3, hours=20, minutes=30)
CALENDAR_PATH_ENV = "TRADING_AGENT_CALENDAR"
_DEFAULT_CALENDAR = Path(__file__).resolve().parents[3] / "data" / "calendar" / "economic_calendar.csv"
_CLOCK: contextvars.ContextVar[datetime | None] = contextvars.ContextVar("backtest_clock", default=None)

Fetch = Callable[..., Any]


@contextlib.contextmanager
def backtest_clock(now: datetime) -> Iterator[None]:
    """Install the bar timestamp as the point-in-time clock for tool calls in this context."""
    token = _CLOCK.set(now)
    try:
        yield
    finally:
        _CLOCK.reset(token)


def resolve_as_of(as_of: str | None) -> datetime | None:
    """Explicit `as_of` wins (clamped to the backtest clock so a model can't ask for the future)."""
    clock = _CLOCK.get()
    if not as_of:
        return clock
    ts = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return min(ts, clock) if clock else ts


def cot_published_at(report_date: str) -> datetime:
    d = datetime.fromisoformat(report_date[:10]).replace(tzinfo=timezone.utc)
    return d + COT_PUBLICATION_LAG


def cot_rows_as_of(rows: list[dict], as_of: datetime) -> list[dict]:
    """Rows already published at `as_of`, newest first."""
    known = [r for r in rows if cot_published_at(str(r.get("report_date_as_yyyy_mm_dd", ""))) <= as_of]
    return sorted(known, key=lambda r: str(r["report_date_as_yyyy_mm_dd"]), reverse=True)


def _http_json(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
    resp = httpx.get(url, params=params, headers=headers, timeout=10.0, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def cot_report(cftc_code: str, as_of: datetime | None, fetch: Fetch = _http_json) -> str:
    if as_of is None:
        params = {"cftc_contract_market_code": cftc_code, "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 1}
        return summarize_cot(fetch(_CFTC_URL, params))
    cutoff = (as_of - COT_PUBLICATION_LAG).date().isoformat()
    params = {
        "cftc_contract_market_code": cftc_code,
        "$where": f"report_date_as_yyyy_mm_dd <= '{cutoff}T00:00:00.000'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": 4,
    }
    rows = cot_rows_as_of(fetch(_CFTC_URL, params), as_of)
    return summarize_cot(rows[:1]) if rows else "COT data unavailable."


@tool(parse_docstring=True)
def cftc_cot(cftc_code: str = "232741", as_of: str | None = None) -> str:
    """CFTC Commitments of Traders speculative positioning for a currency future, as published by `as_of`.

    Args:
        cftc_code: CFTC contract market code (Australian dollar = 232741).
        as_of: ISO-8601 UTC point in time; only reports PUBLISHED by then are used. Omit for latest.
    """
    try:
        return cot_report(cftc_code, resolve_as_of(as_of))
    except Exception:  # noqa: BLE001 - any failure degrades to neutral; the gate must not crash on a flaky feed
        return "COT data unavailable."


def _calendar_path() -> Path:
    return Path(os.environ.get(CALENDAR_PATH_ENV) or _DEFAULT_CALENDAR)


def calendar_events_as_of(cal: Any, instrument: str, as_of: datetime, hours_ahead: int, hours_back: int) -> list[dict]:
    """Forex-Factory-shaped events known at `as_of`; `actual` is blanked for events not yet released."""
    currencies = set(instrument.replace("/", "_").split("_"))
    lo, hi = as_of - timedelta(hours=hours_back), as_of + timedelta(hours=hours_ahead)
    out = []
    for r in cal.to_dict("records"):
        when = r["time"].to_pydatetime()
        if r["published"].to_pydatetime() > as_of or not (lo <= when <= hi):
            continue
        if str(r["currency"]) not in currencies or str(r["impact"]).lower() != "high":
            continue
        released = when <= as_of
        out.append({
            "title": r["title"], "country": r["currency"], "impact": r["impact"], "date": when.isoformat(),
            "forecast": _cell(r.get("forecast")), "previous": _cell(r.get("previous")),
            "actual": _cell(r.get("actual")) if released else "",
        })
    return out


def _cell(v: object) -> str:
    return "" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v)


def _ff_live(instrument: str, hours_ahead: int, hours_back: int) -> str:
    currencies = set(instrument.replace("/", "_").split("_"))
    now = datetime.now(timezone.utc)
    horizon, floor = now + timedelta(hours=hours_ahead), now - timedelta(hours=hours_back)
    window = []
    for e in _http_json(_FF_CAL_URL, headers={"User-Agent": _UA}):
        if e.get("country") not in currencies or str(e.get("impact", "")).lower() != "high":
            continue
        try:
            when = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
        except (KeyError, TypeError, ValueError):
            continue
        if floor <= when <= horizon:
            window.append(e)
    return summarize_calendar(window)


@tool(parse_docstring=True)
def economic_calendar(instrument: str = "AUD_USD", hours_ahead: int = 48, hours_back: int = 72, as_of: str | None = None) -> str:
    """High-impact economic events for an instrument's currencies: recent releases (actual vs
    forecast = the Systematic-Macro surprise) plus upcoming events (the event risk).

    Args:
        instrument: instrument symbol, e.g. AUD_USD (events for BOTH legs are included).
        hours_ahead: look-ahead window in hours (upcoming event risk).
        hours_back: look-back window in hours (recent releases already out).
        as_of: ISO-8601 UTC point in time; read from the stored calendar. Omit for the live feed.
    """
    try:
        ts = resolve_as_of(as_of)
        if ts is None:
            return _ff_live(instrument, hours_ahead, hours_back)
        from trading_agent.news import load_calendar  # lazy: keeps the tool import light

        cal = load_calendar(_calendar_path())
        if cal.empty or cal["published"].min().to_pydatetime() > ts or cal["time"].max().to_pydatetime() < ts:
            return "Economic calendar unavailable."  # stored calendar doesn't cover as_of
        return summarize_calendar(calendar_events_as_of(cal, instrument, ts, hours_ahead, hours_back))
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


def alfred_values_as_of(observations: list[dict], as_of: datetime, keep: int = 6) -> list[float]:
    """Values from an ALFRED vintage response: drop observations dated after `as_of`, and any whose
    vintage (`realtime_start`) is later than `as_of`."""
    day = as_of.date().isoformat()
    vals = []
    for o in sorted(observations, key=lambda o: o["date"]):
        if o["date"] > day or str(o.get("realtime_start", day)) > day or o.get("value") in (None, "."):
            continue
        try:
            vals.append(float(o["value"]))
        except ValueError:
            continue
    return vals[-keep:]


def _alfred_recent(series_id: str, as_of: datetime, api_key: str, fetch: Fetch = _http_json) -> list[float]:
    day = as_of.date().isoformat()
    params = {
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "realtime_start": day, "realtime_end": day,
        "observation_start": (as_of - timedelta(days=400)).date().isoformat(), "observation_end": day,
    }
    return alfred_values_as_of(fetch(_ALFRED_URL, params).get("observations", []), as_of)


def macro_snapshot(as_of: datetime | None, fetch: Fetch = _http_json) -> str:
    data: dict[str, list[float]] = {}
    if as_of is None:
        cosd = (datetime.now(timezone.utc) - timedelta(days=400)).date().isoformat()  # bound CSV size
        for label, series_id in _FRED_SERIES.items():
            with contextlib.suppress(Exception):  # one dead series must not sink the whole read
                data[label] = _fred_recent(series_id, cosd)
        return summarize_macro(data)
    key = os.environ.get("FRED_API_KEY")
    if not key:
        return "Macro context unavailable."  # ALFRED vintages need a key; never fall back to revised data
    for label, series_id in _FRED_SERIES.items():
        with contextlib.suppress(Exception):
            data[label] = _alfred_recent(series_id, as_of, key, fetch)
    return summarize_macro(data)


@tool(parse_docstring=True)
def macro_context(instrument: str = "AUD_USD", as_of: str | None = None) -> str:  # noqa: ARG001 - instrument labels the call
    """Macro backdrop for a forex pair: policy-rate differential, terms-of-trade (metals), and risk
    sentiment (VIX) - inputs for the AQR Systematic-Macro themes. FRED (live) or ALFRED vintages (as_of).

    Args:
        instrument: instrument symbol, e.g. AUD_USD (used to label; series are AU/US + global proxies).
        as_of: ISO-8601 UTC point in time; uses the data vintage available then. Omit for latest.
    """
    try:
        return macro_snapshot(resolve_as_of(as_of))
    except Exception:  # noqa: BLE001 - any failure degrades to neutral; the gate must not crash on a flaky feed
        return "Macro context unavailable."
