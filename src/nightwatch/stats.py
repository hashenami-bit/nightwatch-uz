"""Statistics for the test nights, exactly as in docs/evaluation_protocol.md C2 (DECISIONS.md D015).

Everything works on a pandas Series d indexed by the real calendar date of each test night.
Missing nights stay missing: they are simply not in the index, and two nights count as neighbours
only when their dates are exactly one calendar day apart.

Diebold-Mariano (DM), absolute loss, h = 1:
    d_t   = mean over the night's complete points of |model error| - |comparison error|
    mean  = (1/n) sum d_t                     n = number of test nights
    g0    = (1/n) sum (d_t - mean)^2
    g1    = (1/n) sum over neighbour pairs (t, t + 1 day) of (d_t - mean)(d_{t+1} - mean)
    V     = g0 + 2 * (1/2) * g1               Newey-West, Bartlett weight 1/2 for lag 1
    DM    = mean / sqrt(V / n)
    DM*   = DM * sqrt((n - 1) / n)            Harvey-Leybourne-Newbold, h = 1
    p     = two-sided, t distribution with n - 1 degrees of freedom

Bootstrap: circular moving blocks of 2 nights. A block is a test night and the next one in date
order (the last wraps to the first), and it is usable only if the two dates are exactly one day
apart; blocks never cross a gap.

No p-value and no interval (C2) if n < 5, V <= 0, or fewer than 3 usable blocks. The nightly
differences are then shown in a dated table instead.
"""
from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pandas as pd
from scipy import stats as sps

MIN_NIGHTS = 5
MIN_BLOCKS = 3
BLOCK = 2
ALPHA = 0.05
V_ZERO = 1e-12      # V at or below this counts as 0: rounding can leave a tiny positive rest (D015)
ONE_DAY = timedelta(days=1)


def night_differences(table: pd.DataFrame, model: str, comparison: str, target: str = "y") -> pd.Series:
    """d_t per calendar night: mean over its points of |model error| - |comparison error|."""
    loss = (table[model] - table[target]).abs() - (table[comparison] - table[target]).abs()
    d = loss.groupby(table["night_date"]).mean().sort_index()
    d.name = "d"
    return d


def _sorted(d: pd.Series) -> pd.Series:
    if d.index.has_duplicates:
        raise ValueError("one value per night: the dates must be unique")
    return d.sort_index().astype(float)


def neighbour_pairs(dates, lag: int = 1) -> list[tuple]:
    """Pairs of test nights exactly `lag` calendar days apart. Pairs across a gap are not made."""
    present = set(dates)
    return [(t, t + lag * ONE_DAY) for t in sorted(present) if t + lag * ONE_DAY in present]


def autocovariance(d: pd.Series, lag: int) -> float:
    """(1/n) sum over pairs exactly `lag` days apart of (d_t - mean)(d_{t+lag} - mean)."""
    d = _sorted(d)
    e = d - d.mean()
    return float(sum(e[a] * e[b] for a, b in neighbour_pairs(d.index, lag)) / len(d))


def autocorrelation(d: pd.Series, lag: int) -> float | None:
    """Lag-1 or lag-2 autocorrelation of d_t, reported with the test (proposal v3 section 8)."""
    g0 = autocovariance(d, 0)
    return None if g0 <= 0 else autocovariance(d, lag) / g0


def usable_blocks(dates) -> list[tuple]:
    """Circular 2-night blocks that do not cross a gap.

    The candidates are each night with the next one, the last wrapping to the first. The wrap
    always goes back in time, so it is never usable: in practice the usable blocks are the
    neighbour pairs (D015).
    """
    dates = sorted(set(dates))
    n = len(dates)
    if n < BLOCK:
        return []
    candidates = [(dates[i], dates[(i + 1) % n]) for i in range(n)]
    return [(a, b) for a, b in candidates if b - a == ONE_DAY]


def no_report_reasons(d: pd.Series, variance: float | None = None) -> list[str]:
    """Why no p-value or interval may be given (C2). Empty list = they may be given."""
    d = _sorted(d)
    n = len(d)
    if variance is None:
        variance = autocovariance(d, 0) + autocovariance(d, 1) if n else 0.0
    reasons = []
    if n < MIN_NIGHTS:
        reasons.append(f"only {n} test nights (need {MIN_NIGHTS})")
    if variance <= V_ZERO:
        reasons.append("the variance estimate V is not above zero")
    blocks = len(usable_blocks(d.index))
    if blocks < MIN_BLOCKS:
        reasons.append(f"only {blocks} usable 2-night blocks (need {MIN_BLOCKS})")
    return reasons


def diebold_mariano(d: pd.Series) -> dict:
    """The DM test with the HLN correction. p_value is None when C2 says not to report one."""
    d = _sorted(d)
    n = len(d)
    if n == 0:
        raise ValueError("no test nights")
    mean = float(d.mean())
    g0, g1 = autocovariance(d, 0), autocovariance(d, 1)
    variance = g0 + 2 * 0.5 * g1
    out = {"n": n, "mean_d": mean, "gamma0": g0, "gamma1": g1, "V": variance,
           "n_pairs": len(neighbour_pairs(d.index)), "dm": None, "dm_hln": None, "df": n - 1,
           "p_value": None, "reasons": no_report_reasons(d, variance)}
    if variance > V_ZERO:
        out["dm"] = mean / math.sqrt(variance / n)
        out["dm_hln"] = out["dm"] * math.sqrt((n - 1) / n)
    if not out["reasons"]:
        out["p_value"] = float(2 * sps.t.sf(abs(out["dm_hln"]), df=n - 1))
    return out


def block_bootstrap(d: pd.Series, n_boot: int = 10_000, seed: int = 0) -> dict:
    """95% interval for mean d_t from circular 2-night blocks that never cross a gap.

    Each resample draws ceil(n / 2) usable blocks with replacement, joins them and keeps the
    first n values. lo and hi are None when C2 says not to report an interval.
    """
    d = _sorted(d)
    n = len(d)
    blocks = usable_blocks(d.index)
    out = {"n": n, "mean_d": float(d.mean()) if n else None, "n_blocks": len(blocks),
           "nights_in_blocks": len({t for b in blocks for t in b}), "lo": None, "hi": None,
           "reasons": no_report_reasons(d), "note": None}
    if out["nights_in_blocks"] < n:
        out["note"] = (f"{n - out['nights_in_blocks']} of {n} test nights have no neighbour, are in no "
                       "block and are never drawn: the interval describes the other nights only")
    if out["reasons"]:
        return out
    values = np.array([[d[a], d[b]] for a, b in blocks])
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(blocks), size=(n_boot, math.ceil(n / BLOCK)))
    means = values[picks].reshape(n_boot, -1)[:, :n].mean(axis=1)
    out["lo"], out["hi"] = (float(x) for x in np.quantile(means, [ALPHA / 2, 1 - ALPHA / 2]))
    return out


def metrics(y, pred) -> dict:
    """MAE (primary), RMSE (second check) and bias (mean of prediction minus outcome)."""
    error = np.asarray(pred, dtype=float) - np.asarray(y, dtype=float)
    return {"n": int(error.size), "mae": float(np.mean(np.abs(error))),
            "rmse": float(np.sqrt(np.mean(error ** 2))), "bias": float(np.mean(error))}
