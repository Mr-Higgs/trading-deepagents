"""Overfitting statistics: Deflated Sharpe Ratio, PBO via CSCV, bootstrap CIs, plateau ranking.

References: Bailey & Lopez de Prado (2014) "The Deflated Sharpe Ratio"; Bailey, Borwein, Lopez de
Prado & Zhu (2015) "The Probability of Backtest Overfitting".
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Callable, Sequence
from itertools import combinations
from statistics import NormalDist

_EULER = 0.5772156649015329
_N = NormalDist()


def per_period_sharpe(returns: Sequence[float]) -> float:
    if len(returns) < 2:  # noqa: PLR2004
        return 0.0
    sd = statistics.stdev(returns)
    return statistics.fmean(returns) / sd if sd > 0 else 0.0


def _moments(returns: Sequence[float]) -> tuple[float, float]:
    """Sample skewness and (non-excess) kurtosis."""
    n = len(returns)
    mu = statistics.fmean(returns)
    m2 = sum((r - mu) ** 2 for r in returns) / n
    if m2 == 0:
        return 0.0, 3.0
    m3 = sum((r - mu) ** 3 for r in returns) / n
    m4 = sum((r - mu) ** 4 for r in returns) / n
    return m3 / m2**1.5, m4 / m2**2


def expected_max_sharpe(n_trials: int, trial_sharpe_var: float) -> float:
    """E[max SR] of `n_trials` independent zero-skill strategies (per-period units)."""
    if n_trials <= 1 or trial_sharpe_var <= 0:
        return 0.0
    sd = math.sqrt(trial_sharpe_var)
    return sd * ((1 - _EULER) * _N.inv_cdf(1 - 1 / n_trials) + _EULER * _N.inv_cdf(1 - 1 / (n_trials * math.e)))


def probabilistic_sharpe(returns: Sequence[float], benchmark: float = 0.0) -> float:
    """P(true SR > `benchmark`) given the sample's length, skew and kurtosis (per-period units)."""
    n = len(returns)
    if n < 3:  # noqa: PLR2004
        return 0.0
    sr = per_period_sharpe(returns)
    skew, kurt = _moments(returns)
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    if denom <= 0:
        return 0.0
    return _N.cdf((sr - benchmark) * math.sqrt(n - 1) / math.sqrt(denom))


def deflated_sharpe(returns: Sequence[float], *, n_trials: int, trial_sharpes: Sequence[float]) -> float:
    """DSR: PSR against the Sharpe the best of `n_trials` noise strategies would reach by luck."""
    var = statistics.pvariance(trial_sharpes) if len(trial_sharpes) > 1 else 0.0
    return probabilistic_sharpe(returns, expected_max_sharpe(n_trials, var))


def pbo_cscv(matrix: Sequence[Sequence[float]], *, n_splits: int = 16, metric: Callable[[Sequence[float]], float] = per_period_sharpe) -> float:
    """Probability of Backtest Overfitting by combinatorially symmetric cross-validation.

    Args:
        matrix: one return series per trial, all over the same dates (rows = trials).
        n_splits: even number of contiguous blocks; every half/half split is evaluated.
        metric: performance statistic per block-set.

    Returns:
        Fraction of splits where the in-sample winner ranks at or below the out-of-sample median.
    """
    if n_splits % 2 or n_splits < 2:  # noqa: PLR2004
        msg = "n_splits must be even and >= 2"
        raise ValueError(msg)
    n_trials = len(matrix)
    length = min((len(r) for r in matrix), default=0)
    if n_trials < 2 or length < n_splits:  # noqa: PLR2004
        return float("nan")
    size = length // n_splits
    blocks = [range(i * size, (i + 1) * size) for i in range(n_splits)]
    below = total = 0
    for is_idx in combinations(range(n_splits), n_splits // 2):
        oos_idx = [i for i in range(n_splits) if i not in is_idx]
        is_perf = [metric([row[t] for b in is_idx for t in blocks[b]]) for row in matrix]
        oos_perf = [metric([row[t] for b in oos_idx for t in blocks[b]]) for row in matrix]
        best = max(range(n_trials), key=lambda k: is_perf[k])
        rank = sum(1 for v in oos_perf if v < oos_perf[best]) + 1  # 1 = worst
        w = rank / (n_trials + 1)
        below += math.log(w / (1 - w)) <= 0
        total += 1
    return below / total


def bootstrap_ci(values: Sequence[float], stat: Callable[[Sequence[float]], float], *, n: int = 2000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float]:
    """Percentile bootstrap CI (iid resampling; deterministic for a given `seed`)."""
    if len(values) < 2:  # noqa: PLR2004
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    k = len(values)
    draws = sorted(stat([values[rng.randrange(k)] for _ in range(k)]) for _ in range(n))
    return draws[int(alpha / 2 * n)], draws[min(n - 1, int((1 - alpha / 2) * n))]


def neighborhood_scores(
    points: Sequence[tuple[tuple[float, ...], tuple[object, ...], float]], *, radius: float = 0.15, min_neighbors: int = 3
) -> list[float]:
    """Median objective of each point's neighborhood (same categoricals, numeric L-inf distance <= radius).

    A sharp isolated peak scores like its (worse) neighbors; a broad plateau keeps its level. Points with
    fewer than `min_neighbors` (self included) are unproven: their neighborhood is padded with the worst
    score seen, so an isolated spike cannot outrank a populated plateau.
    """
    floor = min((v for *_, v in points), default=0.0)
    out = []
    for num, cat, _ in points:
        vals = [v for n2, c2, v in points if c2 == cat and all(abs(a - b) <= radius for a, b in zip(num, n2, strict=True))]
        out.append(statistics.median(vals + [floor] * max(0, min_neighbors - len(vals))))
    return out
