"""Convert quote-currency amounts into the account currency via the relevant cross.

P/L on a pair is earned in its QUOTE currency (USD for AUD_USD, JPY for EUR_JPY). Sizing and equity
live in the ACCOUNT currency. `FxConverter.rate(src, dst, ts)` returns how many `dst` one `src` buys,
using the last CLOSED candle of a direct (`SRC_DST`) or inverse (`DST_SRC`) instrument at/before `ts`,
so conversion never peeks ahead. Static rates are supported for tests and fixed-rate studies.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd


class FxRateUnavailable(LookupError):
    """No cross (direct or inverse) is loaded for the requested currency pair at that time."""


class FxConverter:
    def __init__(self, frames: dict[str, pd.DataFrame] | None = None, static: dict[str, float] | None = None) -> None:
        """`frames` maps instrument (e.g. "USD_JPY") -> candle frame with close_time + c (mid close).
        `static` maps instrument -> fixed rate (used when no frame is loaded)."""
        self._series: dict[str, pd.Series] = {}
        for inst, df in (frames or {}).items():
            ct = pd.to_datetime(df["close_time"], utc=True)
            self._series[inst] = pd.Series(df["c"].astype(float).to_numpy(), index=ct).sort_index()
        self._static = dict(static or {})

    def _px(self, inst: str, ts: datetime) -> float | None:
        s = self._series.get(inst)
        if s is not None:
            idx = s.index.searchsorted(pd.Timestamp(ts), side="right")
            if idx > 0:
                return float(s.iloc[idx - 1])
        return self._static.get(inst)

    def rate(self, src: str, dst: str, ts: datetime) -> float:
        """Units of `dst` per one unit of `src` at `ts` (lookahead-safe)."""
        if src == dst:
            return 1.0
        direct = self._px(f"{src}_{dst}", ts)
        if direct:
            return direct
        inverse = self._px(f"{dst}_{src}", ts)
        if inverse:
            return 1.0 / inverse
        msg = f"no {src}_{dst} or {dst}_{src} rate loaded at {ts.isoformat()}"
        raise FxRateUnavailable(msg)

    def convert(self, amount: float, src: str, dst: str, ts: datetime) -> float:
        return amount * self.rate(src, dst, ts)


def conversion_instrument(quote_ccy: str, account_ccy: str) -> str | None:
    """Cross needed to convert `quote_ccy` P/L into `account_ccy` (None when they match).

    Returned in the OANDA-listed orientation for the common majors (USD_JPY, not JPY_USD);
    FxConverter handles either orientation.
    """
    if quote_ccy == account_ccy:
        return None
    majors_first = ("EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY")
    a, b = sorted((quote_ccy, account_ccy), key=lambda c: majors_first.index(c) if c in majors_first else len(majors_first))
    return f"{a}_{b}"
