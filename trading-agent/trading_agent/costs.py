"""Transaction-cost model: hour-of-week spread table + configurable slippage.

FX spreads are not flat. They widen sharply around the 21:00 UTC broker rollover, at the Tokyo open,
and when the market reopens on Sunday. `SpreadModel` stores one spread (in pips) per UTC
hour-of-week bucket (Mon 00:00 = 0 ... Sun 23:00 = 167). It is fitted from bid/ask candle history
(`fit_from_frame`) or built from a flat baseline with configurable widening multipliers
(`from_profile`) when no bid/ask history is cached yet.

    uv run python -m trading_agent.costs fit data/candles/AUD_USD_M5_20250101_20260101_BA.csv \\
        --pip 0.0001 --out data/costs/AUD_USD_spread.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

HOURS_PER_WEEK = 168


def hour_of_week(ts: datetime) -> int:
    """UTC hour-of-week bucket: Monday 00:00 UTC = 0, Sunday 23:00 UTC = 167."""
    t = ts.astimezone(timezone.utc)
    return t.weekday() * 24 + t.hour


@dataclass(frozen=True)
class WideningProfile:
    """Multipliers applied to a flat baseline spread when no fitted table exists.

    Hours are UTC. Defaults reflect typical OANDA AUD_USD behavior; tune in risk.yaml.
    """

    rollover_hour: int = 21
    rollover_mult: float = 3.0
    tokyo_open_hour: int = 0
    tokyo_open_mult: float = 1.5
    weekend_open_hours: tuple[int, ...] = (21, 22)  # Sunday reopen
    weekend_open_mult: float = 4.0


@dataclass(frozen=True)
class SpreadModel:
    """Spread in pips per UTC hour-of-week, with a fallback for empty buckets."""

    table: tuple[float, ...]
    default_pips: float
    source: str = "flat"

    def spread_pips(self, ts: datetime) -> float:
        v = self.table[hour_of_week(ts)] if self.table else float("nan")
        return self.default_pips if pd.isna(v) else float(v)

    @classmethod
    def flat(cls, pips: float) -> SpreadModel:
        return cls(table=tuple([pips] * HOURS_PER_WEEK), default_pips=pips, source="flat")

    @classmethod
    def from_profile(cls, base_pips: float, profile: WideningProfile | None = None) -> SpreadModel:
        """Flat baseline widened at rollover / Tokyo open / Sunday reopen (pre-fit fallback)."""
        p = profile or WideningProfile()
        table = []
        for how in range(HOURS_PER_WEEK):
            day, hour = divmod(how, 24)
            mult = 1.0
            if hour == p.tokyo_open_hour:
                mult = max(mult, p.tokyo_open_mult)
            if hour == p.rollover_hour:
                mult = max(mult, p.rollover_mult)
            if day == 6 and hour in p.weekend_open_hours:  # Sunday
                mult = max(mult, p.weekend_open_mult)
            table.append(base_pips * mult)
        return cls(table=tuple(table), default_pips=base_pips, source="profile")

    @classmethod
    def fit_from_frame(cls, df: pd.DataFrame, pip_size: float, *, quantile: float = 0.5, default_pips: float | None = None) -> SpreadModel:
        """Fit the table from bid/ask candles: spread at each bar OPEN (fills happen at the open).

        Uses the `quantile` (median by default) per bucket so a few news spikes don't dominate.
        """
        opens = pd.to_datetime(df["open_time"], utc=True)
        spread = (df["ao"].astype(float) - df["bo"].astype(float)) / pip_size
        buckets = opens.dt.weekday * 24 + opens.dt.hour
        per = spread.groupby(buckets).quantile(quantile)
        overall = float(spread.quantile(quantile)) if len(spread) else (default_pips or 1.0)
        table = tuple(float(per.get(h, float("nan"))) for h in range(HOURS_PER_WEEK))
        return cls(table=table, default_pips=default_pips if default_pips is not None else overall, source="fitted")

    def to_json(self) -> str:
        return json.dumps({"source": self.source, "default_pips": self.default_pips, "table": [None if pd.isna(v) else round(v, 4) for v in self.table]}, indent=1)

    @classmethod
    def load(cls, path: str | Path) -> SpreadModel:
        raw = json.loads(Path(path).read_text())
        table = tuple(float("nan") if v is None else float(v) for v in raw["table"])
        return cls(table=table, default_pips=float(raw["default_pips"]), source=str(raw.get("source", "fitted")))


@dataclass(frozen=True)
class CostModel:
    """Spread + slippage. Entry/market exits pay `slippage_pips`; stop exits pay `stop_slippage_pips`."""

    spread: SpreadModel
    slippage_pips: float = 0.3
    stop_slippage_pips: float = 0.3
    notes: dict[str, str] = field(default_factory=dict)

    def half_spread_pips(self, ts: datetime) -> float:
        return self.spread.spread_pips(ts) / 2.0


def _main() -> None:
    ap = argparse.ArgumentParser(prog="trading_agent.costs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fit", help="fit an hour-of-week spread table from a bid/ask candle CSV")
    f.add_argument("candles")
    f.add_argument("--pip", type=float, default=0.0001)
    f.add_argument("--quantile", type=float, default=0.5)
    f.add_argument("--out", required=True)
    args = ap.parse_args()
    df = pd.read_csv(args.candles)
    model = SpreadModel.fit_from_frame(df, args.pip, quantile=args.quantile)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(model.to_json())
    print(f"fitted spread table ({len(df)} bars, default {model.default_pips:.2f} pips) -> {out}")


if __name__ == "__main__":
    _main()
