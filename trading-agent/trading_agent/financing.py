"""Overnight financing (swap) charged on open positions at each broker rollover.

OANDA credits/debits financing for every position held through 17:00 New York (21:00 UTC in this
system's config). The Wednesday rollover is charged THREE days (it carries the weekend). Rates are
annualized fractions per pair and side (`long_rate`, `short_rate`; negative = you pay), configured
in config/financing.yaml or loaded from OANDA's instrument financing endpoint via
`load_oanda_financing` (the "hook"). Amounts are computed in the quote currency
(|units| x price x rate / days_in_year x days_charged) and converted to the account currency by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

# Mon..Sun -> days charged. Wednesday carries the weekend; Sat/Sun rollovers don't happen.
DEFAULT_DAYS_CHARGED = {0: 1, 1: 1, 2: 3, 3: 1, 4: 1, 5: 0, 6: 0}
_DOW = {"MONDAY": 0, "TUESDAY": 1, "WEDNESDAY": 2, "THURSDAY": 3, "FRIDAY": 4, "SATURDAY": 5, "SUNDAY": 6}


@dataclass(frozen=True)
class PairFinancing:
    long_rate: float  # annualized fraction, e.g. -0.012 = pay 1.2%/yr to hold a long
    short_rate: float


@dataclass(frozen=True)
class FinancingModel:
    rates: dict[str, PairFinancing]
    rollover_utc: time = time(21, 0)
    days_charged: dict[int, int] = field(default_factory=lambda: dict(DEFAULT_DAYS_CHARGED))
    days_in_year: int = 365

    def rollovers_between(self, prev: datetime, now: datetime) -> list[datetime]:
        """Rollover instants R with prev < R <= now."""
        out = []
        d = prev.astimezone(timezone.utc).date()
        end = now.astimezone(timezone.utc)
        while True:
            r = datetime.combine(d, self.rollover_utc, tzinfo=timezone.utc)
            if r > end:
                break
            if r > prev:
                out.append(r)
            d += timedelta(days=1)
        return out

    def charge_quote(self, instrument: str, units: int, price: float, at: datetime) -> float:
        """Signed financing in QUOTE currency for one rollover `at` (0 when the pair has no rate)."""
        pf = self.rates.get(instrument)
        days = self.days_charged.get(at.astimezone(timezone.utc).weekday(), 0)
        if pf is None or days == 0 or units == 0:
            return 0.0
        rate = pf.long_rate if units > 0 else pf.short_rate
        return abs(units) * price * rate / self.days_in_year * days


def load_financing_yaml(path: str | Path) -> FinancingModel | None:
    p = Path(path)
    if not p.exists():
        return None
    raw: dict[str, Any] = yaml.safe_load(p.read_text()) or {}
    rates = {sym: PairFinancing(float(v["long_rate"]), float(v["short_rate"])) for sym, v in (raw.get("pairs") or {}).items()}
    hh, mm = str(raw.get("rollover_utc", "21:00")).split(":")
    days = {_DOW[k.upper()]: int(v) for k, v in (raw.get("days_charged") or {}).items()} or dict(DEFAULT_DAYS_CHARGED)
    return FinancingModel(rates=rates, rollover_utc=time(int(hh), int(mm)), days_charged=days, days_in_year=int(raw.get("days_in_year", 365)))


def parse_oanda_financing(instrument_payload: dict[str, Any]) -> tuple[PairFinancing, dict[int, int]]:
    """Parse one entry of OANDA `GET /v3/accounts/{id}/instruments` into rates + days-charged."""
    fin = instrument_payload.get("financing") or {}
    pf = PairFinancing(float(fin.get("longRate", 0.0)), float(fin.get("shortRate", 0.0)))
    days = {_DOW[d["dayOfWeek"]]: int(d["daysCharged"]) for d in fin.get("financingDaysOfWeek", []) if d.get("dayOfWeek") in _DOW}
    return pf, days or dict(DEFAULT_DAYS_CHARGED)


def load_oanda_financing(api: Any, account_id: str, instrument: str) -> tuple[PairFinancing, dict[int, int]]:  # pragma: no cover - network
    """Hook: fetch CURRENT financing rates for `instrument` from the OANDA practice account.

    Note these are today's rates; for historical backtests prefer a dated rates table in financing.yaml.
    """
    from oandapyV20.endpoints.accounts import AccountInstruments

    resp = api.request(AccountInstruments(accountID=account_id, params={"instruments": instrument}))
    return parse_oanda_financing(resp["instruments"][0])
