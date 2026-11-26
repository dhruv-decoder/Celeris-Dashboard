"""Ranges for totals (a family, a region, everything) built from the per-series P10/P90.

Adding up P90s overstates the high case (all series having a high month at once is very unlikely), while treating
series as independent understates it (when the market rises, many series rise together). The truth is in between:

    sigma_total = sqrt( (1 - rho) * sum(sigma_i^2) + rho * (sum sigma_i)^2 )

rho = 0 is "independent", rho = 1 is "all move together". rho is chosen so that, in the replay test, family-month
totals fall inside their 80% range (P10..P90) 80% of the time. Each series' sigma comes from its own range:
sigma_i = (P90 - P10) / (2 x 1.2816), since P10 and P90 sit 1.2816 standard deviations either side of the middle.
"""
import numpy as np
import pandas as pd

Z = 1.2816


def series_sigma(p10, p90):
    return (np.asarray(p90, float) - np.asarray(p10, float)) / (2 * Z)


def total_range(expected, sigma, rho):
    """expected and sigma are arrays for the series in the total; returns (p10, p90) of the total."""
    s = np.sqrt((1 - rho) * np.sum(np.square(sigma)) + rho * np.sum(sigma) ** 2)
    e = float(np.sum(expected))
    return max(0.0, e - Z * s), e + Z * s


def _coverage(long, keys, rho, factor):
    g = long.assign(e=long.p50 * factor, s=series_sigma(long.p10, long.p90), s2=series_sigma(long.p10, long.p90) ** 2)
    a = g.groupby(keys).agg(actual=("actual", "sum"), e=("e", "sum"), s=("s", "sum"), s2=("s2", "sum"))
    sd = np.sqrt((1 - rho) * a.s2 + rho * a.s ** 2)
    lo, hi = (a.e - Z * sd).clip(lower=0), a.e + Z * sd
    return float(((a.actual >= lo) & (a.actual <= hi)).mean())


def calibrate(long, factor, target=0.8):
    """Pick rho on family-month totals; report the coverage it gives at other levels too."""
    fam = ["origin", "Product_Family", "h"]
    grid = np.round(np.arange(0, 1.0001, 0.01), 2)
    cov = [_coverage(long, fam, r, factor) for r in grid]
    rho = float(grid[int(np.argmin(np.abs(np.array(cov) - target)))])
    levels = {"family-month": fam, "region-month": ["origin", "Region", "h"],
              "family-region-month": ["origin", "Product_Family", "Region", "h"], "total-month": ["origin", "h"]}
    report = {name: {"coverage_with_rho": round(_coverage(long, k, rho, factor), 3),
                     "coverage_if_independent": round(_coverage(long, k, 0.0, factor), 3),
                     "coverage_if_summed_p10_p90": round(_coverage(long, k, 1.0, factor), 3)} for name, k in levels.items()}
    return {"rho": rho, "target": target, "levels": report}
