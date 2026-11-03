"""Build the stored economic calendar (`data/calendar/economic_calendar.csv`) from official schedules.

Backtests need a point-in-time history of scheduled releases; Forex Factory only serves the current week,
so history comes from the publishers themselves:

- USD: FOMC statements/minutes (federalreserve.gov), BLS/BEA/ADP/UMich release dates (FRED release
  calendar, needs FRED_API_KEY), ISM PMIs (rule: 1st / 3rd US business day, 10:00 ET).
- AUD: RBA cash-rate decisions (rba.gov.au), ABS Labour Force / CPI / monthly CPI indicator / GDP edition
  pages (each states its release date and time).
- Live: `--merge-ff` folds this week's Forex Factory feed (Medium/High) into the same file.

Impact tags follow docs (Economic News.pdf): its USD list is High; secondary releases are Medium.
Usage: python -m trading_agent.calendar_build --start 2021-01-01 --end 2026-07-01 [--merge-ff]
"""

from __future__ import annotations

import argparse
import os
import re
import time as _time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

ET = ZoneInfo("America/New_York")
SYD = ZoneInfo("Australia/Sydney")
UA = "Mozilla/5.0 (trading-agent calendar builder)"
COLUMNS = ["time", "currency", "impact", "title", "before_min", "after_min", "source"]
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data" / "calendar" / "economic_calendar.csv"

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
RBA_URL = "https://www.rba.gov.au/monetary-policy/int-rate-decisions/{year}/"
ABS_URL = "https://www.abs.gov.au/statistics/{path}/{edition}"
FRED_DATES_URL = "https://api.stlouisfed.org/fred/release/dates"
FF_WEEK_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


@dataclass(frozen=True)
class FredRelease:
    release_id: int
    title: str
    impact: str
    et: time
    monthly: bool = True  # keep one date per month (FRED also lists revision-only dates)


FRED_RELEASES = (
    FredRelease(50, "Non-Farm Payrolls / Unemployment Rate", "High", time(8, 30)),
    FredRelease(10, "CPI", "High", time(8, 30)),
    FredRelease(46, "PPI", "High", time(8, 30)),
    FredRelease(53, "GDP", "High", time(8, 30)),
    FredRelease(54, "PCE / Personal Income", "High", time(8, 30)),
    FredRelease(194, "ADP Non-Farm Employment", "High", time(8, 15)),
    FredRelease(9, "Retail Sales", "Medium", time(8, 30)),
    FredRelease(192, "JOLTS Job Openings", "Medium", time(10, 0)),
    FredRelease(180, "Unemployment Claims", "Medium", time(8, 30), monthly=False),
)


@dataclass(frozen=True)
class AbsRelease:
    path: str
    title: str
    impact: str
    quarterly: bool
    edition_suffix: str = ""


ABS_RELEASES = (
    AbsRelease("labour/employment-and-unemployment/labour-force-australia", "Employment Change / Unemployment Rate", "High", False),
    AbsRelease("economy/price-indexes-and-inflation/consumer-price-index-australia", "CPI q/q", "High", True, "-quarter"),
    AbsRelease("economy/price-indexes-and-inflation/monthly-consumer-price-index-indicator", "Monthly CPI Indicator", "Medium", False),
    AbsRelease("economy/national-accounts/australian-national-accounts-national-income-expenditure-and-product", "GDP q/q", "Medium", True),
)


def _row(when: datetime, ccy: str, impact: str, title: str, source: str, before: int | None = None, after: int | None = None) -> dict:
    return {"time": when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "currency": ccy, "impact": impact,
            "title": title, "before_min": before, "after_min": after, "source": source}


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# ---- USD -------------------------------------------------------------------------------------------

def parse_fomc(html: str) -> list[dict]:
    """FOMC statements (14:00 ET on the decision day; press conference => long after-window) and minutes."""
    rows = []
    for blk in re.split(r'<h4><a id="\d+">', html)[1:]:
        year = int(re.search(r"(\d{4}) FOMC Meetings", blk).group(1))
        for meeting in re.split(r'(?=<div[^>]*fomc-meeting__month)', blk)[1:]:
            m = re.search(r'fomc-meeting__month[^>]*>(?:<strong>)?([A-Za-z/]+)<.*?fomc-meeting__date[^>]*>([^<]+)<', meeting, re.S)
            if not m or "notation" in m.group(2).lower() or "unscheduled" in meeting.lower():
                continue
            month = m.group(1).split("/")[-1][:3].lower()
            day = int(re.findall(r"\d+", m.group(2))[-1])
            decided = datetime.combine(date(year, _MONTHS[month], day), time(14, 0), ET)
            rows.append(_row(decided, "USD", "High", "FOMC Statement / Fed Funds Rate", "federalreserve.gov", 30, 90))
            rel = re.search(r"Released ([A-Z][a-z]+) (\d{1,2}), (\d{4})", _text(meeting))
            if rel:
                d = datetime.strptime(" ".join(rel.groups()), "%B %d %Y").date()
                rows.append(_row(datetime.combine(d, time(14, 0), ET), "USD", "Medium", "FOMC Meeting Minutes", "federalreserve.gov"))
    return rows


def one_per_month(dates: Iterable[date]) -> list[date]:
    """FRED lists revision-only dates too; keep, per month, the date nearest the release's usual day."""
    ds = sorted(set(dates))
    if not ds:
        return []
    typical = pd.Series([d.day for d in ds]).median()
    best: dict[tuple[int, int], date] = {}
    for d in ds:
        k = (d.year, d.month)
        if k not in best or abs(d.day - typical) < abs(best[k].day - typical):
            best[k] = d
    return sorted(best.values())


def fred_release_dates(release_id: int, start: date, end: date, api_key: str, get: Callable[..., dict]) -> list[date]:
    params = {"release_id": release_id, "api_key": api_key, "file_type": "json", "realtime_start": start.isoformat(),
              "realtime_end": end.isoformat(), "include_release_dates_with_no_data": "true", "limit": 10000, "sort_order": "asc"}
    return [date.fromisoformat(r["date"]) for r in get(FRED_DATES_URL, params)["release_dates"]]


def fred_rows(start: date, end: date, api_key: str, get: Callable[..., dict]) -> list[dict]:
    rows = []
    for rel in FRED_RELEASES:
        ds = fred_release_dates(rel.release_id, start, end, api_key, get)
        ds = one_per_month(ds) if rel.monthly else ds
        rows += [_row(datetime.combine(d, rel.et, ET), "USD", rel.impact, rel.title, f"fred:{rel.release_id}") for d in ds]
    return rows


def us_business_days(start: date, end: date) -> pd.DatetimeIndex:
    from pandas.tseries.holiday import USFederalHolidayCalendar

    return pd.bdate_range(start, end, freq="C", holidays=USFederalHolidayCalendar().holidays(start, end))


def ism_rows(start: date, end: date) -> list[dict]:
    """ISM Manufacturing PMI on the 1st and Services PMI on the 3rd US business day, 10:00 ET."""
    days = us_business_days(start, end)
    rows = []
    for _, grp in pd.Series(days, index=days).groupby([days.year, days.month]):
        if len(grp) >= 3:
            rows.append(_row(datetime.combine(grp.iloc[0].date(), time(10, 0), ET), "USD", "High", "ISM Manufacturing PMI", "rule:ism"))
            rows.append(_row(datetime.combine(grp.iloc[2].date(), time(10, 0), ET), "USD", "High", "ISM Services PMI", "rule:ism"))
    return rows


# ---- AUD -------------------------------------------------------------------------------------------

def parse_rba_year(html: str, year: int) -> list[dict]:
    """RBA cash-rate decisions (14:30 Sydney time) listed on the year's decisions page."""
    found = re.findall(rf"(\d{{1,2}}) (January|February|March|April|May|June|July|August|September|October|November|December) {year}", _text(html))
    days = sorted({datetime.strptime(f"{d} {m} {year}", "%d %B %Y").date() for d, m in found})
    days = [d for d in days if d.weekday() == 1]  # decisions are on a Tuesday; drops other dates on the page
    return [_row(datetime.combine(d, time(14, 30), SYD), "AUD", "High", "RBA Cash Rate / Statement", "rba.gov.au", 30, 60) for d in days]


def parse_abs_release(html: str) -> datetime | None:
    """'Release date and time 16/10/2025 11:30am AEDT' on an ABS edition page -> aware datetime."""
    m = re.search(r"Release date and time (\d{2}/\d{2}/\d{4}) (\d{1,2}):(\d{2})(am|pm)", _text(html))
    if not m:
        return None
    d = datetime.strptime(m.group(1), "%d/%m/%Y").date()
    hour = int(m.group(2)) % 12 + (12 if m.group(4) == "pm" else 0)
    return datetime.combine(d, time(hour, int(m.group(3))), SYD)


def abs_editions(rel: AbsRelease, start: date, end: date) -> list[str]:
    """Edition slugs whose release could fall in [start, end): reference periods up to ~3 months earlier."""
    months = pd.period_range(pd.Period(start, "M") - 3, pd.Period(end, "M"), freq="M")
    out = []
    for p in months:
        monthly_cpi = bool(rel.edition_suffix) and (p.year, p.month) >= (2025, 10)  # ABS full monthly CPI from Oct-2025
        if rel.quarterly and p.month not in (3, 6, 9, 12) and not monthly_cpi:
            continue
        suffix = "" if monthly_cpi else rel.edition_suffix
        out.append(f"{p.strftime('%b').lower()}{suffix}-{p.year}")
    return out


def plausible_release(edition: str, when: datetime) -> bool:
    """ABS redirects unknown slugs to other editions; keep a date only if it is 0-100 days after the period."""
    mon, *_, year = edition.split("-")
    period_end = pd.Period(year=int(year), month=_MONTHS[mon], freq="M").end_time.date()
    return 0 < (when.date() - period_end).days <= 100


# ---- live: Forex Factory weekly feed ---------------------------------------------------------------

def ff_rows(events: list[dict], currencies: set[str], impacts: set[str] = frozenset({"High", "Medium"})) -> list[dict]:
    rows = []
    for e in events:
        if e.get("country") in currencies and e.get("impact") in impacts:
            try:
                when = datetime.fromisoformat(e["date"])
            except (KeyError, TypeError, ValueError):
                continue
            rows.append(_row(when, e["country"], e["impact"], str(e.get("title", "event")), "forexfactory"))
    return rows


# ---- assembly --------------------------------------------------------------------------------------

def merge(existing: pd.DataFrame, rows: list[dict], start: date | None = None, end: date | None = None) -> pd.DataFrame:
    """Union keyed on (time, currency, title); newest source wins; optional [start, end) clip."""
    df = pd.concat([existing.reindex(columns=COLUMNS), pd.DataFrame(rows, columns=COLUMNS)], ignore_index=True)
    df = df.drop_duplicates(subset=["time", "currency", "title"], keep="last")
    t = pd.to_datetime(df["time"], utc=True)
    if start:
        df = df[t >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[pd.to_datetime(df["time"], utc=True) < pd.Timestamp(end, tz="UTC")]
    return df.sort_values(["time", "currency", "title"]).reset_index(drop=True)


def _http_text(url: str) -> str:
    r = httpx.get(url, headers={"User-Agent": UA}, timeout=30.0, follow_redirects=True)
    r.raise_for_status()
    return r.text


def _http_json(url: str, params: dict | None = None) -> dict | list:
    r = httpx.get(url, params=params, headers={"User-Agent": UA}, timeout=30.0, follow_redirects=True)
    r.raise_for_status()
    return r.json()


def build(start: date, end: date, *, fred_key: str, pause: float = 0.5) -> list[dict]:
    rows = parse_fomc(_http_text(FOMC_URL)) + fred_rows(start, end, fred_key, _http_json) + ism_rows(start, end)
    for year in range(start.year, end.year + 1):
        rows += parse_rba_year(_http_text(RBA_URL.format(year=year)), year)
    for rel in ABS_RELEASES:
        for ed in abs_editions(rel, start, end):
            try:
                when = parse_abs_release(_http_text(ABS_URL.format(path=rel.path, edition=ed)))
            except httpx.HTTPStatusError:
                when = None
            if when and plausible_release(ed, when):
                rows.append(_row(when, "AUD", rel.impact, rel.title, f"abs:{ed}", 30, 30))
            _time.sleep(pause)
    return rows


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-07-01", help="exclusive")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--merge-ff", action="store_true", help="only fold this week's Forex Factory feed into --out")
    ap.add_argument("--currencies", default="AUD,USD")
    a = ap.parse_args(argv)
    existing = pd.read_csv(a.out) if a.out.exists() else pd.DataFrame(columns=COLUMNS)
    if a.merge_ff:
        df = merge(existing, ff_rows(_http_json(FF_WEEK_URL), set(a.currencies.split(","))))
    else:
        key = os.environ.get("FRED_API_KEY")
        if not key:
            raise SystemExit("FRED_API_KEY is required for the US release calendar")
        start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
        df = merge(existing, build(start, end, fred_key=key), start, end)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(a.out, index=False)
    print(f"{len(df)} events -> {a.out}\n{df.groupby(['currency', 'impact']).size().to_string()}")


if __name__ == "__main__":
    main()
