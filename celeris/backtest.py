"""Replaying history to measure forecast accuracy fairly (a rolling-origin backtest).

For each forecast origin, a method sees only the months up to and including the origin and forecasts
the next 19 months, exactly like the real task. We then compare with what actually happened.

Scored on rebuilt demand (what customers asked for), skipping months whose demand is itself a guess (weight 0).
Only series that already existed at the origin are scored, as in test.csv; launches are handled by lifecycle rules.
"""
import numpy as np
import pandas as pd

H = 19
ORIGINS = ["2022-03", "2023-03", "2024-03", "2025-03"]   # forecast windows end 2023-10, 2024-10, 2025-10, 2026-10


def month(d):
    return pd.Period(d, freq="M").ordinal


def label(m):
    return str(pd.Period(ordinal=int(m), freq="M"))


class Data:
    """Demand as a matrix: one row per series, one column per month (period ordinal)."""

    def __init__(self, panel, master):
        p = panel.copy()
        p["m"] = p.m.astype(int)
        self.Y = p.pivot_table(index=["SKU_ID", "Region"], columns="m", values="demand")
        self.W = p.pivot_table(index=["SKU_ID", "Region"], columns="m", values="weight").reindex_like(self.Y)
        first = p.groupby(["SKU_ID", "Region"])[["Product_Family", "Business_Unit", "Sales_Channel", "Customer_Name"]].last()
        self.meta = first.reindex(self.Y.index)
        self.meta["Powertrain"] = self.meta.index.get_level_values(0).map(master.set_index("SKU_ID").Powertrain_Application)
        self.panel = p

    def history(self, cut):
        return self.Y.loc[:, :cut]


def run(method, data, origins=ORIGINS, name=None):
    """Long table: one row per (origin, series, target month) with forecast and actual."""
    rows = []
    for o in origins:
        cut = month(o)
        hist = data.history(cut)
        exists = hist.notna().any(axis=1)
        fc = method(hist, cut, H)
        targets = [cut + h for h in range(1, H + 1)]
        act = data.Y.reindex(columns=targets)
        w = data.W.reindex(columns=targets)
        f = fc.reindex(index=data.Y.index, columns=targets)
        ok = act.notna() & (w > 0) & exists.to_numpy()[:, None]
        long = pd.DataFrame({
            "origin": o,
            "series": np.repeat(np.arange(len(data.Y)), H),
            "h": np.tile(np.arange(1, H + 1), len(data.Y)),
            "forecast": f.to_numpy().ravel(),
            "actual": act.to_numpy().ravel(),
            "weight": w.to_numpy().ravel(),
            "keep": ok.to_numpy().ravel(),
        })
        long = long[long.keep].drop(columns="keep")
        long["forecast"] = long.forecast.fillna(0).clip(lower=0).round()   # submissions are whole numbers
        rows.append(long)
    out = pd.concat(rows, ignore_index=True)
    out["method"] = name or getattr(method, "__name__", "method")
    idx = data.Y.index
    out["SKU_ID"] = idx.get_level_values(0)[out.series]
    out["Region"] = idx.get_level_values(1)[out.series]
    for c in ["Product_Family", "Sales_Channel", "Customer_Name", "Powertrain"]:
        out[c] = data.meta[c].to_numpy()[out.series]
    return out


def metrics(long, by=None):
    """WAPE = sum |forecast - actual| / sum actual.  Bias = (sum forecast - sum actual) / sum actual."""
    by = [by] if isinstance(by, str) else (by or [])
    g = long.assign(err=(long.forecast - long.actual).abs()).groupby(["method"] + by)
    m = g.agg(abs_err=("err", "sum"), forecast=("forecast", "sum"), actual=("actual", "sum"), rows=("actual", "size"))
    m["wape"] = (m.abs_err / m.actual).round(4)
    m["bias"] = ((m.forecast - m.actual) / m.actual).round(4)
    return m[["wape", "bias", "actual", "rows"]].reset_index()


def timing_oracle(data, origins=ORIGINS):
    """Diagnostic: a forecaster that KNOWS which future months have an order, and predicts the series' average
    order size of the last 12 months in exactly those months. Its error is roughly what is left from order
    sizes varying alone, i.e. a realistic floor for methods that only get the timing right."""
    err = tot = 0.0
    for o in origins:
        cut = month(o)
        hist = data.history(cut)
        size = hist.iloc[:, -12:].where(hist.iloc[:, -12:] > 0).mean(axis=1)
        targets = [cut + h for h in range(1, H + 1)]
        act, w = data.Y.reindex(columns=targets), data.W.reindex(columns=targets)
        ok = act.notna() & (w > 0) & hist.notna().any(axis=1).to_numpy()[:, None]
        f = (act > 0).astype(float).mul(size, axis=0).fillna(0).round()
        err += float((f - act).abs()[ok].sum().sum())
        tot += float(act[ok].sum().sum())
    return round(err / tot, 4)
