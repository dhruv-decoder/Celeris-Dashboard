"""Builds and validates submission.csv (the file the organisers score).

Format (from sample_submission.csv): Date, SKU_ID, Region, Units_Sold_Forecast
- one row for every row of test.csv, in test.csv's order
- whole numbers >= 0, no blanks
- 0 after a SKU's EOL date and before a planned launch (business rule 5)
"""
import numpy as np
import pandas as pd

COLUMNS = ["Date", "SKU_ID", "Region", "Units_Sold_Forecast"]

# Months since launch -> share of the predecessor's level. Median over the 6 replacements in the history
# (ELA-015, HYD-021, PMP-018, PTC-027, PTC-028, THM-010).
LAUNCH_RAMP = {1: 0.17, 2: 0.42, 3: 0.62, 4: 0.74, 5: 0.77, 6: 0.97}
# Old SKU between its successor's launch and its own EOL: about half of its normal level.
OVERLAP_SHARE = 0.5


def forecast(method, panel, test, master):
    """Forecast every test row with `method` (a baseline or model with the backtest signature), then apply the
    lifecycle rules: launch ramp for new SKUs, wind-down of the SKU they replace, 0 after EOL."""
    from .backtest import Data, label, month
    data = Data(panel, master)
    last = panel.Date.max()
    cut = month(last)
    fc = method(data.history(cut), cut, 19)
    long = fc.stack().rename("f").reset_index()
    long.columns = ["SKU_ID", "Region", "m", "f"]
    long["Date"] = long.m.map(label)
    t = test[["Date", "SKU_ID", "Region"]].merge(long[["Date", "SKU_ID", "Region", "f"]], how="left",
                                                 on=["Date", "SKU_ID", "Region"])
    t = apply_launches(t, panel, master, last)
    t["f"] = t.f.fillna(0)
    t = apply_lifecycle_rules(t, master)
    return finalize(t, test)


def forecast_v0(panel, test, master):
    """Baseline v0: same calendar month in the latest year of history."""
    from .baselines import same_month_last_year
    return forecast(same_month_last_year, panel, test, master)


def apply_launches(t, panel, master, last):
    """New SKUs: predecessor's last-12-month level in the same region x launch ramp. The SKU being replaced sells
    about half between the successor's launch and its own EOL."""
    m = master.set_index("SKU_ID")
    successors = m[m.Replaces_SKU.notna() & (m.Lifecycle_Status == "Planned Launch")]
    for sku, row in successors.iterrows():
        old = row.Replaces_SKU
        lvl = panel[(panel.SKU_ID == old) & (panel.Date > _shift(last, -12))].groupby("Region").demand.mean()
        rows = t.SKU_ID == sku
        months_in = t.loc[rows, "Date"].map(lambda d: _months_between(row.Launch_Date, d) + 1)
        share = months_in.map(lambda k: LAUNCH_RAMP.get(k, 1.0))
        t.loc[rows, "f"] = t.loc[rows, "Region"].map(lvl).fillna(lvl.mean()).to_numpy() * share.to_numpy()
        overlap = (t.SKU_ID == old) & (t.Date >= row.Launch_Date)
        t.loc[overlap, "f"] *= OVERLAP_SHARE
    return t


def apply_lifecycle_rules(t, master):
    m = master.set_index("SKU_ID")
    eol = t.SKU_ID.map(m.EOL_Date)
    launch = t.SKU_ID.map(m.Launch_Date)
    t.loc[eol.notna() & (t.Date > eol), "f"] = 0
    t.loc[t.Date < launch, "f"] = 0
    return t


def finalize(t, test):
    out = test[["Date", "SKU_ID", "Region"]].merge(t[["Date", "SKU_ID", "Region", "f"]], on=["Date", "SKU_ID", "Region"],
                                                   how="left", validate="one_to_one")
    out["Units_Sold_Forecast"] = np.clip(np.round(out.f.fillna(0)), 0, None).astype(int)
    return out[COLUMNS]


def validate(sub, test, sample, master):
    """Raises AssertionError with a clear message if the file would be rejected or breaks a business rule."""
    assert list(sub.columns) == list(sample.columns) == COLUMNS, f"columns {list(sub.columns)} != {COLUMNS}"
    assert len(sub) == len(test), f"{len(sub)} rows, test has {len(test)}"
    keys = ["Date", "SKU_ID", "Region"]
    assert (sub[keys].astype(str).to_numpy() == test[keys].astype(str).to_numpy()).all(), "rows not in test.csv order"
    v = pd.to_numeric(sub.Units_Sold_Forecast, errors="coerce")
    assert v.notna().all(), "blank or non-numeric forecasts"
    assert (v >= 0).all(), "negative forecasts"
    assert (v == v.round()).all(), "forecasts must be whole numbers"
    m = master.set_index("SKU_ID")
    eol = sub.SKU_ID.map(m.EOL_Date)
    assert (v[eol.notna() & (sub.Date > eol)] == 0).all(), "sales after EOL"
    assert (v[sub.Date < sub.SKU_ID.map(m.Launch_Date)] == 0).all(), "sales before launch"
    return {"rows": int(len(sub)), "total_units": int(v.sum()), "zero_rows": int((v == 0).sum()),
            "after_eol_rows": int((eol.notna() & (sub.Date > eol)).sum())}


def _months_between(a, b):
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


def _shift(month, k):
    return str(pd.Period(month, freq="M") + k)


def forecast_table(rows, panel, test, master, prices, rises, bias_factor=1.0):
    """Turn model rows (origin = last history month) into the full forecast table for every test row.

    Columns: p10 / p50 / p90 (units), p50 is what goes in submission.csv; `expected` = p50 x bias_factor (use for
    totals: medians of single months add up to less than the expected total); revenue at the list price of the
    target year (2026 list price + announced January rises).
    """
    last = panel.Date.max()
    r = rows[["target", "SKU_ID", "Region", "p10", "p50", "p90"]].rename(columns={"target": "Date"})
    r = r.astype({"Date": str, "SKU_ID": str, "Region": str})
    base = test[["Date", "SKU_ID", "Region"]].merge(r,
                                                    on=["Date", "SKU_ID", "Region"], how="left")
    # ratio of p10/p90 to p50 by family, for products with no history (new launches)
    fam = test.set_index(["Date", "SKU_ID", "Region"]).Product_Family
    base["Product_Family"] = fam.reindex(pd.MultiIndex.from_frame(base[["Date", "SKU_ID", "Region"]])).to_numpy()
    ok = base.p50 > 0
    lo = (base.p10[ok] / base.p50[ok]).groupby(base.Product_Family[ok]).median()
    hi = (base.p90[ok] / base.p50[ok]).groupby(base.Product_Family[ok]).median()

    t = base.rename(columns={"p50": "f"})
    t = apply_launches(t, panel, master, last)
    new = t.p10.isna() & t.f.notna()
    t.loc[new, "p10"] = t.f[new] * t.Product_Family[new].map(lo)
    t.loc[new, "p90"] = t.f[new] * t.Product_Family[new].map(hi)
    m = master.set_index("SKU_ID")
    for col in ["p10", "p90"]:          # the replaced SKU's range winds down with it
        for sku, row in m[m.Replaces_SKU.notna() & (m.Lifecycle_Status == "Planned Launch")].iterrows():
            o = (t.SKU_ID == row.Replaces_SKU) & (t.Date >= row.Launch_Date)
            t.loc[o, col] *= OVERLAP_SHARE
    t["f"] = t.f.fillna(0)
    t = apply_lifecycle_rules(t, master)
    eol = t.SKU_ID.map(m.EOL_Date)
    dead = (eol.notna() & (t.Date > eol)) | (t.Date < t.SKU_ID.map(m.Launch_Date))
    t.loc[dead, ["p10", "p90"]] = 0
    t["p10"], t["p90"] = t.p10.fillna(0), t.p90.fillna(0)
    q = np.sort(t[["p10", "f", "p90"]].to_numpy(dtype=float), axis=1)
    t["p10"], t["p50"], t["p90"] = q[:, 0], q[:, 1], q[:, 2]
    t["expected"] = t.p50 * bias_factor

    lp = prices.set_index("SKU_ID")["2026"]
    year_factor = {2026: 1.0}
    for y in (2027, 2028):
        year_factor[y] = year_factor[y - 1] * (1 + rises.get(y, 0) / 100)
    t["price_usd"] = t.SKU_ID.map(lp).to_numpy() * t.Date.str[:4].astype(int).map(year_factor).to_numpy()
    t["revenue_p50"] = (t.p50.round() * t.price_usd).round(2)
    t["revenue_expected"] = (t.expected * t.price_usd).round(2)
    out = test.merge(t[["Date", "SKU_ID", "Region", "p10", "p50", "p90", "expected", "price_usd", "revenue_p50",
                        "revenue_expected"]], on=["Date", "SKU_ID", "Region"], how="left", validate="one_to_one")
    sub = out[["Date", "SKU_ID", "Region"]].assign(Units_Sold_Forecast=np.clip(np.round(out.p50), 0, None).astype(int))
    return sub, out
