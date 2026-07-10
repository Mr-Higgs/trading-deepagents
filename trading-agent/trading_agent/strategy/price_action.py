"""Price-action structure detectors - swings, break-of-structure, S/R confluence over an OHLC frame.

Reference: docs/trading-system/"Price Action.pdf" (market structure = swing highs/lows; breaking the most
recent CONFIRMED swing = a break of structure; horizontal S/R = clustered swing points, trade the
confluence). Sibling of `patterns.py`: pure functions over a newest-row-last DataFrame, a `PA_SIGNALS` name
tuple, and a `matches` dispatcher that reports WHICH price-action signals fired for a trade direction. Used
as a *confirmation* gate on top of the Ichimoku entry; its fired-name list is recorded into
`Signal.features` for chart labelling. Every function follows the signals.py idiom: NaN-guarded, never raises.

LOOKAHEAD INVARIANT (the whole point of this module). The frame is newest-row-last and every row has already
CLOSED (`marketdata.closed_only`), so `df.iloc[-1]` is the decision bar at `now`. A fractal swing at bar `i`
is only CONFIRMED once `lookback` bars print AFTER it, so the newest index this module may ever return is
`len(df) - 1 - lookback` (== `_confirmed_hi`). The last `lookback` bars (including the decision bar) can never
be swings yet: claiming one either invents bars that do not exist live (IndexError / a truncated,
falsely-confirmed window) or, if a full frame were ever passed in a backtest, peeks at genuinely future bars
and makes the run optimistic and non-reproducible. The ceiling is enforced in ONE place; `_demo()` asserts a
frame whose highest bar is the LAST bar yields no swing there - that assertion fails if the guard is removed.

ponytail: `sr_levels` is 1-D single-linkage clustering (sort, split on gaps > tolerance, take each cluster's
mean). Ceiling: it does not weight a level by touch-count or recency, and it does not label support vs
resistance (a flipped level clusters into one). Upgrade path: keep only levels with >= N touches / decay old
ones - all inside that one function.
"""

from __future__ import annotations

import pandas as pd

# names `matches` recognizes (single source of truth; config_graph imports this, like TRIGGER_NAMES).
PA_SIGNALS = ("bos", "sr_confluence")


def _num(v) -> float | None:
    """NaN/None-safe float coercion (signals.py idiom); keeps every function from ever raising."""
    return None if v is None or pd.isna(v) else float(v)


def _confirmed_hi(n: int, lookback: int) -> int:
    """Newest bar index that can be a CONFIRMED fractal - it needs `lookback` bars printed AFTER it.

    This is the lookahead firewall. Do not inline it or scan past it: any index above this peeks at bars
    that have not closed at decision time (see module docstring).
    """
    return n - 1 - lookback


def _fractals(values: list[float | None], lookback: int, high: bool) -> list[int]:
    """Indices where `values[i]` strictly exceeds (high) / undercuts (low) all `lookback` neighbours.

    Scans only `lookback <= i <= _confirmed_hi(n, lookback)`, so an unconfirmed edge fractal is never
    returned and no out-of-range read occurs. NaN at `i` or any compared neighbour disqualifies `i`.
    """
    n = len(values)
    out: list[int] = []
    for i in range(lookback, _confirmed_hi(n, lookback) + 1):  # empty when n < 2*lookback + 1
        pivot = values[i]
        if pivot is None:
            continue
        ok = True
        for j in range(1, lookback + 1):
            left, right = values[i - j], values[i + j]
            if left is None or right is None:
                ok = False
                break
            if high and not (pivot > left and pivot > right):
                ok = False
                break
            if not high and not (pivot < left and pivot < right):
                ok = False
                break
        if ok:
            out.append(i)
    return out


def swing_highs(df: pd.DataFrame, lookback: int = 2) -> list[int]:
    """Confirmed fractal swing-HIGH bar indices (column `h`). Newest index <= len(df)-1-lookback."""
    return _fractals([_num(v) for v in df["h"].tolist()], lookback, high=True)


def swing_lows(df: pd.DataFrame, lookback: int = 2) -> list[int]:
    """Confirmed fractal swing-LOW bar indices (column `l`). Newest index <= len(df)-1-lookback."""
    return _fractals([_num(v) for v in df["l"].tolist()], lookback, high=False)


def break_of_structure(df: pd.DataFrame, direction: str, lookback: int = 2) -> bool:
    """Latest close breaks the most-recent CONFIRMED swing high (LONG) / swing low (SHORT).

    LONG: close[-1] > that swing's high; SHORT: close[-1] < that swing's low. NOTE the comparison is
    against a prior swing's HIGH/LOW, not a prior close - stricter than a naive reading, and intended
    market-structure semantics (not a bug to "fix"). Uses only closed bars: the swing is confirmed
    `lookback` bars back, the close is the decision bar. False on no swing / NaN close / fewer than
    2*lookback+1 rows. Never raises.
    """
    cc = _num(df["c"].iloc[-1]) if len(df) else None
    if cc is None:
        return False
    if direction == "LONG":
        highs = swing_highs(df, lookback)
        if not highs:
            return False
        level = _num(df["h"].iloc[highs[-1]])  # most recent confirmed swing high
        return level is not None and cc > level
    lows = swing_lows(df, lookback)
    if not lows:
        return False
    level = _num(df["l"].iloc[lows[-1]])  # most recent confirmed swing low
    return level is not None and cc < level


def sr_levels(df: pd.DataFrame, lookback: int = 2, tolerance: float = 0.0) -> list[float]:
    """Support/resistance levels: confirmed swing highs+lows clustered by proximity, ascending.

    Single-linkage 1-D clustering: sort the swing prices, start a new cluster whenever the gap to the
    previous price exceeds `tolerance` (an ABSOLUTE price distance - the caller converts pips via
    `pair.pip_size`), and represent each cluster by its mean. `tolerance=0.0` merges only exactly-equal
    prices. Returns [] when there are no confirmed swings. Never raises.
    """
    raw = [_num(df["h"].iloc[i]) for i in swing_highs(df, lookback)]
    raw += [_num(df["l"].iloc[i]) for i in swing_lows(df, lookback)]
    pts = sorted(p for p in raw if p is not None)
    if not pts:
        return []
    levels: list[float] = []
    cluster = [pts[0]]
    for p in pts[1:]:
        if p - cluster[-1] > tolerance:
            levels.append(float(sum(cluster) / len(cluster)))
            cluster = [p]
        else:
            cluster.append(p)
    levels.append(float(sum(cluster) / len(cluster)))  # float() keeps numpy out of features/logs (determinism)
    return levels


def sr_confluence(df: pd.DataFrame, price: float, lookback: int = 2, tolerance: float = 0.0) -> bool:
    """`price` sits within `tolerance` (absolute) of a clustered S/R level. False if none / NaN. Never raises."""
    p = _num(price)
    if p is None:
        return False
    return any(abs(p - lvl) <= tolerance for lvl in sr_levels(df, lookback, tolerance))


def matches(
    names,
    direction: str,
    df: pd.DataFrame,
    price: float,
    *,
    lookback: int = 2,
    tolerance: float = 0.0,
) -> tuple[str, ...]:
    """Which of `names` fire for `direction` at `price` -> SORTED tuple of fired names (subset of `names`).

    Mirrors `patterns.matches` but returns the FIRED SET instead of a bool, so the caller can (a) gate
    ANY-of via truthiness `if matches(...):`, (b) gate ALL-of via `set(names) <= set(matches(...))` - the
    defensible posture for a confirmation gate - and (c) record exactly which signals fired into
    `Signal.features` for the chart. `names` accepts a bare string or an iterable. Never raises.
    """
    if isinstance(names, str):
        names = [names]
    fired: list[str] = []
    for n in set(names):
        if n == "bos" and break_of_structure(df, direction, lookback):
            fired.append(n)
        elif n == "sr_confluence" and sr_confluence(df, price, lookback, tolerance):
            fired.append(n)
    return tuple(sorted(fired))


def _demo() -> None:
    # --- main frame: 9 bars, lookback=2 -> confirmed ceiling is index 6 (last 2 bars can't be swings) ---
    df = pd.DataFrame(
        {
            "o": [10, 10, 11, 11, 10, 10, 9, 10, 11],
            "h": [10, 11, 12, 11, 10, 9, 10, 11, 13],  # index 8 (=13) is the GLOBAL max but is unconfirmable
            "l": [9, 10, 11, 8, 9, 7, 8, 9, 12],
            "c": [10, 11, 11, 10, 10, 9, 10, 11, 12.5],
        }
    )
    assert _confirmed_hi(len(df), 2) == 6
    assert swing_highs(df, 2) == [2]  # h[2]=12 is the only confirmed swing high
    assert swing_lows(df, 2) == [5]  # l[5]=7 is the only confirmed swing low

    # THE lookahead guard: the highest bar in the frame is the LAST bar; it must be excluded. If someone
    # removes the ceiling (scans to the edge), the next two asserts fail (or the scan IndexErrors).
    assert len(df) - 1 not in swing_highs(df, 2)
    assert max(swing_highs(df, 2)) <= len(df) - 1 - 2

    # break of structure uses close[-1]=12.5 vs the confirmed levels (high 12 / low 7), both directions
    assert break_of_structure(df, "LONG", 2) is True  # 12.5 > 12
    assert break_of_structure(df, "SHORT", 2) is False  # 12.5 is not < 7
    df_short = df.copy()
    df_short.loc[df_short.index[-1], "c"] = 6.5
    assert break_of_structure(df_short, "SHORT", 2) is True  # 6.5 < 7
    assert break_of_structure(df_short, "LONG", 2) is False

    # S/R levels: the two swings, far apart -> two clusters; confluence is proximity to a level
    assert [round(x, 4) for x in sr_levels(df, 2, 0.0)] == [7.0, 12.0]
    assert sr_confluence(df, 12.0, 2, 0.5) is True
    assert sr_confluence(df, 9.5, 2, 0.5) is False

    # clustering merges nearby swings within tolerance (lookback=1 frame with two near-equal highs)
    dfc = pd.DataFrame(
        {"o": [10] * 7, "h": [10, 12.00, 10, 11, 12.01, 11, 9], "l": [9] * 7, "c": [10] * 7}
    )
    two = sr_levels(dfc, 1, 0.0)
    assert len(two) == 2 and abs(two[0] - 12.0) < 1e-9 and abs(two[1] - 12.01) < 1e-9
    one = sr_levels(dfc, 1, 0.05)
    assert len(one) == 1 and abs(one[0] - 12.005) < 1e-9

    # dispatcher reports the fired set; supports ANY-of (truthiness) and ALL-of (subset)
    both = matches(("bos", "sr_confluence"), "LONG", df, 12.0, lookback=2, tolerance=0.5)
    assert both == ("bos", "sr_confluence") and bool(both)
    assert set(("bos", "sr_confluence")) <= set(both)  # ALL-of confirmation passes
    only_bos = matches(("bos", "sr_confluence"), "LONG", df, 12.5, lookback=2, tolerance=0.05)
    assert only_bos == ("bos",)  # 12.5 is not within 0.05 of any level
    assert not (set(("bos", "sr_confluence")) <= set(only_bos))  # ALL-of confirmation fails
    assert matches("bos", "SHORT", df, 12.5, lookback=2) == ()  # short bos does not fire here

    # empty / short frame (< 2*lookback+1 rows) -> everything degrades to empty/False, never raises
    tiny = pd.DataFrame({"o": [1, 1, 1], "h": [1, 2, 1], "l": [1, 1, 1], "c": [1, 2, 1]})
    assert swing_highs(tiny, 2) == [] and swing_lows(tiny, 2) == []
    assert break_of_structure(tiny, "LONG", 2) is False
    assert sr_levels(tiny, 2) == []

    print("price_action self-check OK")


if __name__ == "__main__":
    _demo()
