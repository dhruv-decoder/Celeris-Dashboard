"""Simple forecasting methods. Every real model must beat the best of these to be worth using.

Each takes `hist` (series x months up to the origin, NaN before a series starts), the origin month `cut` and
horizon `H`, and returns a DataFrame (series x the next H months).
"""
import warnings

import numpy as np
import pandas as pd

# Series that haven't started yet are all-NaN in early months; averaging them gives NaN on purpose (handled later).
warnings.filterwarnings("ignore", message="Mean of empty slice", category=RuntimeWarning)


def _out(hist, cut, H, values):
    return pd.DataFrame(values, index=hist.index, columns=[cut + h for h in range(1, H + 1)])


def moving_average_12(hist, cut, H):
    """The average of the last 12 months, repeated for every future month."""
    lvl = np.nanmean(hist.iloc[:, -12:].to_numpy(), axis=1)
    return _out(hist, cut, H, np.repeat(lvl[:, None], H, axis=1))


def same_month_last_year(hist, cut, H):
    """Each future month copies the same calendar month from the latest year available (seasonal naive)."""
    cols = {}
    for h in range(1, H + 1):
        src = cut + h - 12 * int(np.ceil(h / 12))
        cols[cut + h] = hist[src] if src in hist.columns else np.nan
    return pd.DataFrame(cols)


def same_month_avg_2y(hist, cut, H):
    """Average of the same calendar month in the two latest years."""
    cols = {}
    for h in range(1, H + 1):
        base = cut + h - 12 * int(np.ceil(h / 12))
        srcs = [s for s in (base, base - 12) if s in hist.columns]
        cols[cut + h] = hist[srcs].mean(axis=1)
    return pd.DataFrame(cols)


def level_x_profile(hist, cut, H, level_months=12, profile_months=36):
    """Recent level (last 12-month average) x the series' own month-of-year pattern (last 3 years).

    Ordering rhythms of 1, 2 and 3 months all divide 12, so a customer orders in the same calendar months every
    year; the month-of-year pattern captures both seasonality and the ordering rhythm.
    """
    y = hist.to_numpy()
    lvl = np.nanmean(y[:, -level_months:], axis=1)
    win = y[:, -profile_months:]
    moy = np.array([c % 12 for c in hist.columns[-profile_months:]])        # 0 = January
    prof = np.full((len(y), 12), np.nan)
    with np.errstate(all="ignore"):
        for k in range(12):
            prof[:, k] = np.nanmean(win[:, moy == k], axis=1)
        prof = prof / np.nanmean(prof, axis=1, keepdims=True)
    prof = np.where(np.isfinite(prof), prof, 1.0)
    vals = np.stack([lvl * prof[:, (cut + h) % 12] for h in range(1, H + 1)], axis=1)
    return _out(hist, cut, H, vals)


def croston_sba(hist, cut, H, alpha=0.1):
    """Croston's method with the Syntetos-Boylan correction, the classic method for intermittent demand.
    Smooths the order size and the gap between orders separately; forecast = size / gap (flat)."""
    y = hist.to_numpy()
    n = len(y)
    z = np.full(n, np.nan)
    p = np.full(n, np.nan)
    q = np.zeros(n)
    for t in range(y.shape[1]):
        v = y[:, t]
        live = ~np.isnan(v)
        q[live] += 1
        hit = live & (v > 0)
        first = hit & np.isnan(z)
        z[first], p[first] = v[first], q[first]
        upd = hit & ~first
        z[upd] = z[upd] + alpha * (v[upd] - z[upd])
        p[upd] = p[upd] + alpha * (q[upd] - p[upd])
        q[hit] = 0
    f = np.nan_to_num((1 - alpha / 2) * z / p)
    return _out(hist, cut, H, np.repeat(f[:, None], H, axis=1))


def tsb(hist, cut, H, alpha=0.1, beta=0.1):
    """Teunter-Syntetos-Babai: smooths the chance of an order each month and the order size; forecast = chance x size."""
    y = hist.to_numpy()
    n = len(y)
    prob = np.full(n, np.nan)
    size = np.full(n, np.nan)
    for t in range(y.shape[1]):
        v = y[:, t]
        live = ~np.isnan(v)
        start = live & np.isnan(prob)
        prob[start] = (v[start] > 0).astype(float)
        size[start & (v > 0)] = v[start & (v > 0)]
        upd = live & ~start
        prob[upd] = prob[upd] + beta * ((v[upd] > 0) - prob[upd])
        hit = upd & (v > 0)
        fresh = hit & np.isnan(size)
        size[fresh] = v[fresh]
        old = hit & ~fresh
        size[old] = size[old] + alpha * (v[old] - size[old])
    f = np.nan_to_num(prob * size)
    return _out(hist, cut, H, np.repeat(f[:, None], H, axis=1))


BASELINES = {
    "Average of last 12 months": moving_average_12,
    "Same month last year": same_month_last_year,
    "Same month, average of 2 years": same_month_avg_2y,
    "Croston-SBA (intermittent)": croston_sba,
    "TSB (intermittent)": tsb,
    "Level x month-of-year pattern": level_x_profile,
}
