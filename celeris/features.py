"""Features: the inputs a forecasting model learns from.

One row = one series, one forecast origin, one horizon h (1..19 months ahead).
Everything about the series is computed from months up to the origin only, so a model can never peek at the
future. Things known in advance about the target month (calendar, holidays, planned events, price rises,
lifecycle dates, published economic indicators) are allowed: they are also known for the real forecast.
"""
import calendar
import re
import warnings

import numpy as np
import pandas as pd

from .backtest import label, month

warnings.filterwarnings("ignore", message="Mean of empty slice", category=RuntimeWarning)
warnings.filterwarnings("ignore", message="All-NaN slice", category=RuntimeWarning)
warnings.filterwarnings("ignore", message="Degrees of freedom", category=RuntimeWarning)
warnings.filterwarnings("ignore", message="invalid value encountered", category=RuntimeWarning)
warnings.filterwarnings("ignore", message="divide by zero", category=RuntimeWarning)

REGIONS = ["North America", "Europe", "Asia Pacific", "India", "Latin America", "Middle East & Africa"]
MACRO_LAGS = {"New_Vehicle_Orders_Index": (1, 2, 3), "Auto_Production_YoY_Pct": (1, 2, 3), "ISM_Manufacturing_PMI": (1, 2, 3)}
CATEGORICAL = ["Product_Family", "Region", "Sales_Channel", "Customer_Name", "Powertrain"]


# ------------------------------------------------------------------ calendar of known events

def holiday_share(events, first="2019-01", last="2028-12"):
    """Share of each month (0..1) covered by a holiday, per region. Uses exact dates where the calendar has them,
    so moving holidays (Lunar New Year, Ramadan) land in the right month every year."""
    months = pd.period_range(first, last, freq="M")
    out = pd.DataFrame(0.0, index=[str(m) for m in months], columns=REGIONS)
    for _, e in events[(events.Event_Type == "Holiday") & (events.Scope_Type == "Region")].iterrows():
        start, end = _dates(e.Start_Date, e.End_Date)
        for m in months:
            a, b = m.start_time.normalize(), m.end_time.normalize()
            overlap = (min(b, end) - max(a, start)).days + 1
            if overlap > 0:
                out.at[str(m), e.Scope_Value] += overlap / calendar.monthrange(m.year, m.month)[1]
    return out.clip(upper=1.0)


def _dates(start, end):
    end = end if isinstance(end, str) and end else start
    s = pd.Timestamp(start + ("-01" if re.fullmatch(r"\d{4}-\d{2}", start) else ""))
    e = pd.Timestamp(end + ("-01" if re.fullmatch(r"\d{4}-\d{2}", end) else ""))
    if re.fullmatch(r"\d{4}-\d{2}", end):
        e = e + pd.offsets.MonthEnd(0)
    return s, e


def customer_events(events):
    """Customer-specific events as (customer, region, first month, last month, kind)."""
    rows = []
    for _, e in events[events.Scope_Type == "Customer"].iterrows():
        cust, reg = [v.strip() for v in e.Scope_Value.split("|")]
        permanent = not (isinstance(e.End_Month, str) and e.End_Month)
        kind = "capacity_up" if "capacity increase" in e.Description else "shutdown"
        rows.append((cust, reg, e.Start_Month, "2099-12" if permanent else e.End_Month, kind))
    return pd.DataFrame(rows, columns=["Customer_Name", "Region", "start", "end", "kind"])


def price_rise_by_year(events):
    """Announced January list-price rise per year, in percent (e.g. 2027: 4.5)."""
    ev = events[events.Description.str.contains("Annual list-price increase", na=False)]
    return {int(d[:4]): float(re.search(r"([\d.]+)%", t)[1]) for d, t in zip(ev.Start_Date, ev.Description)}


# ------------------------------------------------------------------ the feature table

def make_features(data, cut, H, macro, events, master, with_target=True):
    hist = data.history(cut)
    y = hist.to_numpy()
    cols = np.array(hist.columns)
    exists = ~np.isnan(y).all(axis=1)
    y, idx = y[exists], hist.index[exists]
    meta = data.meta.loc[idx]
    n = len(y)

    def nanmean_last(k):
        return np.nanmean(y[:, -k:], axis=1)

    f = {
        "level_3": nanmean_last(3), "level_6": nanmean_last(6), "level_12": nanmean_last(12), "level_24": nanmean_last(24),
        "std_12": np.nanstd(y[:, -12:], axis=1),
        "zero_share_12": np.nanmean((y[:, -12:] == 0).astype(float) + np.where(np.isnan(y[:, -12:]), np.nan, 0), axis=1),
        "last_value": y[:, -1],
    }
    prev12 = np.nanmean(y[:, -24:-12], axis=1)
    f["trend_12_vs_prev_12"] = np.where(prev12 > 0, f["level_12"] / prev12, np.nan)
    nz = np.where(y[:, -12:] > 0, y[:, -12:], np.nan)
    f["order_size_12"] = np.nanmean(nz, axis=1)
    started = np.argmax(~np.isnan(y), axis=1)
    f["history_months"] = y.shape[1] - started
    f["orders_12"] = (y[:, -12:] > 0).sum(axis=1).astype(float)
    f["max_12"] = np.nanmax(y[:, -12:], axis=1)
    f["median_order_12"] = np.nanmedian(nz, axis=1)
    f["adi"], f["cv2"], f["demand_class"] = intermittency(y[:, -24:])

    # ordering rhythm: months since the last order, and the usual gap between orders (last 24 months)
    pos = (y > 0)
    last_order = np.where(pos.any(axis=1), y.shape[1] - 1 - np.argmax(pos[:, ::-1], axis=1), -1)
    f["months_since_order"] = np.where(last_order >= 0, y.shape[1] - 1 - last_order, 99)
    f["last_order_size"] = np.where(last_order >= 0, y[np.arange(n), np.maximum(last_order, 0)], np.nan)
    f["cycle"] = _cycle(y[:, -24:])

    # month-of-year pattern (same as the best baseline), from the last 36 months
    win = y[:, -36:]
    moy = cols[-36:] % 12
    prof = np.full((n, 12), np.nan)
    for k in range(12):
        prof[:, k] = np.nanmean(win[:, moy == k], axis=1)
    prof = prof / np.nanmean(prof, axis=1, keepdims=True)
    prof = np.where(np.isfinite(prof), prof, 1.0)

    # Group-level growth at the origin (last 12 months vs the 12 before), from history up to the origin only.
    # Single series are too noisy to show a region or family growing; the group total is not.
    all_reg = hist.index.get_level_values(1).to_numpy()
    all_fam = data.meta.loc[hist.index, "Product_Family"].to_numpy()
    row_reg = idx.get_level_values(1).to_numpy()
    row_fam = meta.Product_Family.to_numpy()
    f["region_growth_12"] = _group_growth(hist, all_reg, row_reg)
    f["family_growth_12"] = _group_growth(hist, all_fam, row_fam)
    f["family_region_growth_12"] = _group_growth(hist, all_fam + "|" + all_reg, row_fam + "|" + row_reg)
    all_cust = data.meta.loc[hist.index, "Customer_Name"].to_numpy()
    f["customer_growth_12"] = _group_growth(hist, all_cust + "|" + all_reg, meta.Customer_Name.to_numpy() + "|" + row_reg)
    f["sku_growth_12"] = _group_growth(hist, hist.index.get_level_values(0).to_numpy(), idx.get_level_values(0).to_numpy())
    started_any = pd.DataFrame(hist.notna().to_numpy()).groupby(all_reg).any()
    region_first = started_any.to_numpy().argmax(axis=1)
    f["region_age"] = pd.Series(hist.shape[1] - region_first, index=started_any.index).reindex(row_reg).to_numpy(float)

    hol = holiday_share(events)
    cev = customer_events(events)
    rises = price_rise_by_year(events)
    mac = macro.set_index("Date")
    m_ = master.set_index("SKU_ID")
    eol = idx.get_level_values(0).map(m_.EOL_Date)
    eol_m = np.array([month(e) if isinstance(e, str) else np.nan for e in eol], dtype=float)
    launch_m = np.array([month(d) for d in idx.get_level_values(0).map(m_.Launch_Date)], dtype=float)
    regions = idx.get_level_values(1)
    custs = meta.Customer_Name.to_numpy()

    tables = []
    for h in range(1, H + 1):
        t = cut + h
        tl = label(t)
        row = {k: v for k, v in f.items()}
        row["h"] = np.full(n, h)
        row["target_month"] = np.full(n, t % 12 + 1)
        row["target_year"] = np.full(n, int(tl[:4]))
        row["month_pattern"] = prof[:, t % 12]
        row["level_x_pattern"] = f["level_12"] * prof[:, t % 12]
        src = t - 12 * int(np.ceil(h / 12))
        row["same_month_last_year"] = y[:, list(cols).index(src)] if src in cols else np.nan
        row["same_month_2y_ago"] = y[:, list(cols).index(src - 12)] if (src - 12) in cols else np.nan
        row["same_month_3y_ago"] = y[:, list(cols).index(src - 24)] if (src - 24) in cols else np.nan
        # the months either side of last year's same month: catches an order that slipped by a month
        row["ly_prev"] = y[:, list(cols).index(src - 1)] if (src - 1) in cols else np.nan
        row["ly_next"] = y[:, list(cols).index(src + 1)] if (src + 1) in cols else np.nan
        same = [i for i, c in enumerate(cols[-36:]) if c % 12 == t % 12]
        row["moy_order_rate"] = np.nanmean(np.where(np.isnan(win[:, same]), np.nan, (win[:, same] > 0).astype(float)), axis=1)
        row["gap_at_target"] = np.minimum(f["months_since_order"] + h, 120)
        cyc = f["cycle"]
        lo = np.where(last_order >= 0, cols[0] + last_order, np.nan)
        row["is_rhythm_month"] = np.where(np.isnan(lo), np.nan, ((t - lo) % cyc == 0).astype(float))
        row["holiday_share"] = np.array([hol.at[tl, r] if tl in hol.index else 0.0 for r in regions])
        row["price_rise_pct"] = np.full(n, rises.get(int(tl[:4]), 0.0) if tl.endswith("-01") else 0.0)
        shut = np.zeros(n)
        cap = np.zeros(n)
        for _, e in cev.iterrows():
            hit = (custs == e.Customer_Name) & (regions == e.Region) & (e.start <= tl <= e.end)
            (cap if e.kind == "capacity_up" else shut)[hit] = 1
        row["customer_shutdown"] = shut
        row["customer_capacity_up"] = cap
        row["months_to_eol"] = np.where(np.isnan(eol_m), 99, np.clip(eol_m - t, -1, 99))
        row["age_at_target"] = t - launch_m
        for ind, lags in MACRO_LAGS.items():
            for L in lags:
                ml = label(t - L)
                row[f"{ind}_lag{L}"] = np.full(n, mac.at[ml, ind] if ml in mac.index else np.nan)
        row["EV_Adoption_Index"] = np.full(n, mac.at[tl, "EV_Adoption_Index"] if tl in mac.index else np.nan)
        # The model predicts demand relative to the last 12 months, so what matters is how the economy at the target
        # (2 months before it) compares with the economy during those 12 months.
        window = [label(m) for m in range(cut - 11, cut + 1)]
        for ind in ["New_Vehicle_Orders_Index", "ISM_Manufacturing_PMI", "Auto_Production_YoY_Pct", "EV_Adoption_Index"]:
            base = mac.loc[mac.index.isin(window), ind].mean()
            ml = label(t - 2)
            v = mac.at[ml, ind] if ml in mac.index else np.nan
            row[f"{ind}_vs_level_window"] = np.full(n, (v - base) if ind == "Auto_Production_YoY_Pct" else v / base - 1)
        tab = pd.DataFrame(row)
        tab.insert(0, "Region", regions)
        tab.insert(0, "SKU_ID", idx.get_level_values(0))
        tab.insert(0, "target", tl)
        tab.insert(0, "origin", label(cut))
        for c in ["Product_Family", "Sales_Channel", "Customer_Name", "Powertrain"]:
            tab[c] = meta[c].to_numpy()
        if with_target and t in data.Y.columns:
            tab["y"] = data.Y.loc[idx, t].to_numpy()
            tab["weight"] = data.W.loc[idx, t].to_numpy()
        tables.append(tab)
    return pd.concat(tables, ignore_index=True)


def _group_growth(hist, labels_all, labels_rows):
    """Total demand of each group in the last 12 months / the 12 months before (capped at 5)."""
    tot = pd.DataFrame(hist.to_numpy()).groupby(labels_all).sum(min_count=1)
    last, prev = tot.iloc[:, -12:].sum(axis=1), tot.iloc[:, -24:-12].sum(axis=1)
    ratio = (last / prev.where(prev > 0)).clip(upper=5)
    return ratio.reindex(labels_rows).to_numpy(float)


CLASSES = ["smooth", "erratic", "intermittent", "lumpy"]


def intermittency(win):
    """Syntetos-Boylan demand classes from a window of months (NaN = unknown).

    ADI = known months / months with an order (1 = orders every month). CV2 = squared coefficient of variation of the
    order sizes. Cut-offs 1.32 and 0.49 are the standard ones: smooth (regular, steady sizes), erratic (regular, sizes
    jump), intermittent (gaps, steady sizes), lumpy (gaps and jumpy sizes). Returns adi, cv2, class code 0..3."""
    known = (~np.isnan(win)).sum(axis=1)
    orders = (win > 0).sum(axis=1)
    adi = np.where(orders > 0, known / np.maximum(orders, 1), float(win.shape[1]))
    nzw = np.where(win > 0, win, np.nan)
    mean = np.nanmean(nzw, axis=1)
    cv2 = np.where(orders >= 2, (np.nanstd(nzw, axis=1) / mean) ** 2, 0.0)
    cls = (adi >= 1.32).astype(int) * 2 + (cv2 >= 0.49).astype(int)
    return adi, cv2, cls.astype(float)


def _cycle(win):
    """Most common gap (in months) between orders in the window; 1 if fewer than 2 orders. Capped at 6."""
    out = np.ones(len(win))
    for i, r in enumerate(win):
        o = np.where(r > 0)[0]
        if len(o) >= 3:
            gaps = np.diff(o)
            vals, cnt = np.unique(gaps, return_counts=True)
            out[i] = min(vals[np.argmax(cnt)], 6)
    return out
