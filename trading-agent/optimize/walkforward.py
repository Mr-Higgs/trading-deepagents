"""Walk-forward folds and the locked final holdout."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

# Final holdout: 2026-07-01 -> latest data. The optimizer never loads bars at/after this instant;
# only `optimize.holdout` may, once per config, and it logs the use.
HOLDOUT_START = datetime(2026, 7, 1, tzinfo=UTC)


class HoldoutViolation(ValueError):
    """Raised when an optimization window reaches into the locked holdout."""


@dataclass(frozen=True)
class Fold:
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


def add_months(ts: datetime, months: int) -> datetime:
    y, m = divmod(ts.month - 1 + months, 12)
    return ts.replace(year=ts.year + y, month=m + 1, day=1) if ts.day != 1 else ts.replace(year=ts.year + y, month=m + 1)


def guard_holdout(end: datetime) -> None:
    if end > HOLDOUT_START:
        msg = f"window end {end.isoformat()} is inside the locked holdout (>= {HOLDOUT_START.date()})"
        raise HoldoutViolation(msg)


def make_folds(start: datetime, end: datetime, *, train_months: int = 12, test_months: int = 3, anchored: bool = False) -> list[Fold]:
    """Rolling (or anchored) train/test folds whose test segments tile `[start + train, end)` without overlap.

    Args:
        start: first bar of the first training window.
        end: exclusive end of the last test window; must not exceed `HOLDOUT_START`.
        train_months: training length (rolling); anchored folds grow from `start` instead.
        test_months: test length, also the step between folds.
        anchored: keep `train_start` fixed at `start`.
    """
    guard_holdout(end)
    folds = []
    train_start = start
    test_start = add_months(start, train_months)
    while add_months(test_start, test_months) <= end:
        test_end = add_months(test_start, test_months)
        folds.append(Fold(start if anchored else train_start, test_start, test_start, test_end))
        test_start = test_end
        train_start = add_months(train_start, test_months)
    if not folds:
        msg = f"no complete {train_months}m/{test_months}m fold fits in {start.date()}..{end.date()}"
        raise ValueError(msg)
    return folds
