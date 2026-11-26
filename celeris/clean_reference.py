"""Cleans the smaller files: macro indicators, stockouts, events calendar, SKU master and test."""
import numpy as np
import pandas as pd

from .clean_sales import parse_month

MACRO_COLS = ["ISM_Manufacturing_PMI", "Auto_Production_YoY_Pct", "Vehicle_Inventory_Days",
              "Raw_Material_Cost_Index", "New_Vehicle_Orders_Index", "Industrial_Util_Rate_Pct",
              "Fuel_Price_Index", "EV_Adoption_Index"]


def clean_macro(raw, log):
    F = "macro_indicators.csv"
    log.profile("macro", "before", raw)
    df = raw.copy()
    df["Year"] = df.Year.astype(int)
    df["Month"] = df.Month.astype(int)
    for c in MACRO_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Duplicate month: keep the row whose values sit between the previous and next month.
    dup_dates = df.Date[df.Date.duplicated()].unique()
    for d in dup_dates:
        rows = df[df.Date == d]
        i = rows.index[0]
        prev, nxt = df.loc[i - 1, MACRO_COLS], df.loc[rows.index[-1] + 1, MACRO_COLS]
        mid = (prev + nxt) / 2
        dist = rows[MACRO_COLS].sub(mid).abs().sum(axis=1)
        drop = dist.idxmax()
        log.add(F, "Date", f"Month {d} appears twice with different values", "error_dropped", 1,
                f"Kept the row closest to the neighbouring months (row {dist.idxmin()})",
                f"Distance to neighbour midpoint: {dist.round(2).to_dict()}", before=df.loc[[drop], MACRO_COLS])
        df = df.drop(index=drop)
    df = df.reset_index(drop=True)

    # PMI with a misplaced decimal point (4.79 instead of 47.9).
    pmi = df.ISM_Manufacturing_PMI
    bad = pmi < 20
    log.add(F, "ISM_Manufacturing_PMI", "Misplaced decimal point (e.g. 4.79 instead of 47.9)", "error_fixed",
            bad.sum(), "Multiplied by 10", "PMI is never below 20; neighbours are 47.2 and 46.1",
            before=pmi[bad], after=pmi[bad] * 10)
    df.loc[bad, "ISM_Manufacturing_PMI"] = pmi[bad] * 10

    # Auto production growth stored as a fraction (-0.015) instead of percent (-1.5) in 2019.
    ap = df.Auto_Production_YoY_Pct
    frac = (df.Year == 2019) & (ap.abs() < 1)
    assert frac.sum() == (df.Year == 2019).sum() == 12, "expected all 12 months of 2019 to be fractions"
    log.add(F, "Auto_Production_YoY_Pct", "Stored as a fraction instead of percent (all of 2019)", "error_fixed",
            frac.sum(), "Multiplied by 100",
            "2019 values are -0.01 to -0.06 while 2020-01 is -6.4; x100 joins the series smoothly",
            before=ap[frac], after=ap[frac] * 100)
    df.loc[frac, "Auto_Production_YoY_Pct"] = ap[frac] * 100

    for c in MACRO_COLS:
        miss = df[c].isna()
        if miss.any():
            log.add(F, c, "Missing value", "error_fixed", miss.sum(), "Linear interpolation between neighbouring months",
                    before=df.Date[miss])
            df[c] = df[c].interpolate(limit_direction="both")

    log.add(F, "Raw_Material_Cost_Index", "Drop of 17% in Jan 2022 (149.3 -> 123.6)", "real_event", 1,
            "Kept", "The new level persists in the following months, so it is not a one-month typo")
    months = pd.period_range(df.Date.min(), df.Date.max(), freq="M").astype(str)
    assert list(df.Date) == list(months), "macro months not continuous"
    log.profile("macro", "after", df)
    return df


def clean_stockouts(raw, log):
    F = "inventory_stockouts.csv"
    df = raw.copy()
    num = ["Requested_Units", "Carried_In_Units", "Shipped_Units", "Backordered_Units", "Lost_Units",
           "Fill_Rate_Pct", "Stockout_Days"]
    for c in num:
        df[c] = pd.to_numeric(df[c])
    assert not df.duplicated(["Date", "SKU_ID", "Region"]).any()
    ident = df.Requested_Units + df.Carried_In_Units - df.Shipped_Units - df.Backordered_Units - df.Lost_Units
    assert (ident == 0).all(), "Lost_Units identity broken"

    calc = 100 * df.Shipped_Units / (df.Requested_Units + df.Carried_In_Units)
    off = ~np.isclose(df.Fill_Rate_Pct, calc, atol=0.06) & calc.notna()
    log.add(F, "Fill_Rate_Pct", "Fill_Rate_Pct does not equal Shipped / (Requested + Carried_In)", "info", off.sum(),
            "Recomputed as Fill_Rate_Calc; original kept",
            "Original tracks 1 - Stockout_Days/30 (correlation 0.999): a planning estimate, not the actual fill")
    df["Fill_Rate_Calc"] = calc.round(1)

    nxt = set(zip(df.SKU_ID, df.Region, pd.PeriodIndex(df.Date, freq="M") - 1))
    p = pd.PeriodIndex(df.Date, freq="M")
    bo = df.Backordered_Units > 0
    unlisted = bo & ~pd.Series([(s, r, m) in nxt for s, r, m in zip(df.SKU_ID, df.Region, p)], index=df.index)
    log.add(F, "(file)", "Back-ordered units whose follow-up month is not listed (file is incomplete)", "info",
            unlisted.sum(), "Handled when estimating unconstrained demand (catch-up shipments inflate the next month)")
    return df


def clean_events(raw, log):
    df = raw.copy()
    df["Start_Month"] = df.Start_Date.map(parse_month)
    df["End_Month"] = df.End_Date.map(lambda s: parse_month(s) if str(s).strip() else None)
    open_ended = df.End_Month.isna()
    log.add("events_calendar.csv", "End_Date", "Open-ended event (no end date)", "info", open_ended.sum(),
            "Treated as lasting through the forecast window", ", ".join(df.Event_ID[open_ended]))
    df["Exact_Dates"] = df.Start_Date.str.len() == 10
    return df


def clean_master(raw, log):
    """Business rule: each family belongs to exactly one business unit. The master breaks this once."""
    df = raw.copy()
    family_bu = df.groupby("Product_Family").Business_Unit.agg(lambda s: s.mode().iloc[0])
    for _, r in df[df.Business_Unit != df.Product_Family.map(family_bu)].iterrows():
        log.add("sku_master.csv", "Business_Unit",
                f"{r.SKU_ID}: master says '{r.Business_Unit}' but family '{r.Product_Family}' belongs to "
                f"'{family_bu[r.Product_Family]}'", "master_data", 1,
                "Use the family's business unit", "Every train.csv row for this SKU uses the family's unit")
    df["Business_Unit"] = df.Product_Family.map(family_bu)
    df["EOL_Date"] = df.EOL_Date.replace("", None)
    df["Replaces_SKU"] = df.Replaces_SKU.replace("", None)
    return df


def check_test(test, master, log):
    F = "test.csv"
    assert not test.duplicated(["Date", "SKU_ID", "Region"]).any()
    assert test.SKU_ID.isin(master.SKU_ID).all()
    m = master.set_index("SKU_ID")
    eol = test.SKU_ID.map(m.EOL_Date)
    after = eol.notna() & (test.Date > eol)
    log.add(F, "Date", "Rows after the SKU's EOL date", "info", after.sum(),
            "Forecast = 0 (business rule 5)", ", ".join(sorted(test.SKU_ID[after].unique())))
    new = test.SKU_ID.map(m.Lifecycle_Status) == "Planned Launch"
    log.add(F, "SKU_ID", "Rows for SKUs that have not launched yet (no history)", "info", new.sum(),
            "Forecast from the predecessor SKU (Replaces_SKU)", ", ".join(sorted(test.SKU_ID[new].unique())))
    return test
