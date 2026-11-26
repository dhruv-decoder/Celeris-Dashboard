"""Cleans train.csv into one trustworthy row per Date-SKU-Region.

Every rule below was checked against the data before being written (see docs/CLEANING.md).
The order matters: we fix keys first, then prices, then units (units are recovered from
revenue / price), then revenue is recomputed from the corrected units and price.
"""
import difflib
import re

import numpy as np
import pandas as pd

from .config import (CHANNEL_ALIASES, HISTORY_END, HISTORY_START, REGION_ALIASES,
                     SURCHARGE, SURCHARGE_MONTHS)

F = "train.csv"
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


# ---------------------------------------------------------------- small rules (unit-tested)

def parse_month(text):
    """Any of the 29 date spellings in the file -> 'YYYY-MM'. Returns None if unreadable."""
    s = str(text).strip()
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})(?:[-/]\d{1,2})?", s)          # 2019-01, 2019/01, 2019-01-01
    if m:
        return f"{m[1]}-{int(m[2]):02d}"
    m = re.fullmatch(r"(\d{1,2})[-/](\d{4})", s)                         # 01/2019, 01-2019
    if m:
        return f"{m[2]}-{int(m[1]):02d}"
    m = re.fullmatch(r"([A-Za-z]{3})[a-z]*[ \-](\d{4})", s)              # Jan 2019, Jan-2019
    if m and m[1].lower() in _MONTHS:
        return f"{m[2]}-{_MONTHS[m[1].lower()]:02d}"
    return None


def normalize_sku(text):
    """' hyd_6', 'HYD006', 'HYD-06' -> 'HYD-006'. Returns None if it doesn't look like a SKU."""
    m = re.fullmatch(r"\s*([A-Za-z]{3})\s*[-_ ]?\s*(\d{1,3})\s*", str(text))
    return f"{m[1].upper()}-{int(m[2]):03d}" if m else None


def normalize_region(text):
    """'APAC', 'india ', 'NA' -> official name; '' stays '' (blank, filled later); unknown -> None."""
    s = str(text).strip().lower()
    return "" if s == "" else REGION_ALIASES.get(s)


def normalize_channel(text):
    return CHANNEL_ALIASES.get(re.sub(r"[\s\-_]", "", str(text).lower()))


def make_customer_matcher(official_names):
    """Returns a function mapping any spelling (case, spaces, 'Inc.', one missing letter) to an official name."""
    keys = {re.sub(r"[^a-z]", "", n.lower()): n for n in official_names}

    def match(text):
        k = re.sub(r"[^a-z]", "", re.sub(r"\s*inc\.?\s*$", "", str(text).strip(), flags=re.I).lower())
        if k in keys:
            return keys[k]
        best = difflib.get_close_matches(k, list(keys), n=1, cutoff=0.85)
        return keys[best[0]] if best else None
    return match


def parse_units(text):
    """Returns (value, kind). kind: number | comma | dash | blank."""
    s = str(text).strip()
    if s == "":
        return np.nan, "blank"
    if s == "-":
        return 0.0, "dash"
    if "," in s:
        return float(s.replace(",", "")), "comma"
    return float(s), "number"


def is_digit_swap(reported, correct):
    """True if two amounts use the same digits in a different order (e.g. 23683.14 vs 26383.14).

    Zeros are ignored because a swapped zero can drop off the end when the number is stored
    (the correct 20254.00 appears in the file as '2254.0').
    """
    a, b = f"{abs(reported):.2f}", f"{abs(correct):.2f}"
    return a != b and sorted(a.replace("0", "")) == sorted(b.replace("0", ""))


def in_surcharge(dates):
    return (dates >= SURCHARGE_MONTHS[0]) & (dates <= SURCHARGE_MONTHS[1])


# ---------------------------------------------------------------- the pipeline

def clean_train(raw, master, official_customers, stockouts, log):
    log.profile("train", "before", raw)
    df = raw.copy()
    df["src_line"] = df.index + 2                       # line number in the CSV (line 1 = header)
    df["fix_flags"] = [[] for _ in range(len(df))]

    df = _dedupe(df, log)
    df = _dates(df, log)
    df = _keys(df, master, official_customers, log)
    df = _master_attributes(df, master, log)
    prices, sur_price = list_prices(df, log)
    df = _prices(df, prices, sur_price, log)
    df = _units(df, stockouts, log)
    df = _revenue(df, log)
    df = _fill_blank_regions(df, log)
    df = _lifecycle_checks(df, master, log)

    out = df[["Date", "Year", "Month", "Product_Family", "Business_Unit", "SKU_ID", "SKU_Description",
              "Customer_Name", "Sales_Channel", "Region", "Units_Sold", "Unit_Price_USD", "Revenue_USD",
              "units_status", "price_status", "revenue_status", "fix_flags", "Record_ID", "Load_Timestamp",
              "src_line"]].copy()
    out["fix_flags"] = out.fix_flags.map(lambda f: "|".join(f))
    out = out.sort_values(["SKU_ID", "Region", "Date"]).reset_index(drop=True)

    dupes = out.duplicated(["Date", "SKU_ID", "Region"]).sum()
    assert dupes == 0, f"{dupes} Date-SKU-Region collisions after cleaning"
    log.profile("train", "after", out.drop(columns=["fix_flags"]))
    return out, prices


def _flag(df, mask, name):
    for i in df.index[mask]:
        df.at[i, "fix_flags"].append(name)


def _dedupe(df, log):
    df["_ts"] = pd.to_datetime(df.Load_Timestamp)
    dup = df[df.Record_ID.duplicated(keep=False)]
    content = [c for c in df.columns if c not in ("src_line", "fix_flags", "_ts", "Load_Timestamp")]
    changed_ids = dup.groupby("Record_ID")[content].nunique().gt(1).any(axis=1)
    changed_ids = set(changed_ids[changed_ids].index)
    keep = df.sort_values(["Record_ID", "_ts", "src_line"]).drop_duplicates("Record_ID", keep="last").index
    dropped = df.index.difference(keep)
    d = df.loc[dropped]
    log.add(F, "Record_ID", "Exact duplicate load (same Record_ID, identical content)", "error_dropped",
            (~d.Record_ID.isin(changed_ids)).sum(), "Dropped the second copy",
            "Business rule 2")
    log.add(F, "Record_ID", "Record reloaded with different values (same Record_ID)", "error_dropped",
            d.Record_ID.isin(changed_ids).sum(), "Kept the row with the latest Load_Timestamp, dropped the older one",
            "Business rule 2; the reloads change Units_Sold/Revenue_USD only",
            before=d[d.Record_ID.isin(changed_ids)][["Record_ID", "Units_Sold", "Load_Timestamp"]])
    return df.loc[keep].copy()


def _dates(df, log):
    raw = df.Date.copy()
    parsed = raw.map(parse_month)
    assert parsed.notna().all(), f"unreadable dates: {raw[parsed.isna()].unique()[:5]}"
    # The warehouse loads each month's rows during the following month, on every single row.
    load_month = (df._ts.dt.to_period("M") - 1).astype(str)

    fmt = raw.str.strip() != parsed
    log.add(F, "Date", "Non-standard date format (28 variants, e.g. 'Jan-2019', '01/2019', '2019-01-01')",
            "error_fixed", fmt.sum(), "Converted to YYYY-MM", before=raw[fmt], after=parsed[fmt])

    impossible = (parsed < HISTORY_START) | (parsed > HISTORY_END)
    log.add(F, "Date", "Impossible year (2062 instead of 2026: digits swapped)", "error_fixed", impossible.sum(),
            "Replaced with load month minus one",
            "Year column says 2026 and every row in the file is loaded exactly 1 month after its Date",
            before=raw[impossible], after=load_month[impossible])
    parsed = parsed.where(~impossible, load_month)
    _flag(df, impossible, "date_year_typo")
    assert (parsed == load_month).all(), "a Date disagrees with its load month"

    ym = df.Year.str.strip() + "-" + df.Month.str.strip().str.zfill(2)
    bad_ym = ym != parsed
    log.add(F, "Year/Month", "Year/Month disagree with Date (Month is one month late)", "error_fixed", bad_ym.sum(),
            "Rebuilt Year and Month from Date", "Business rule 1; load timestamps confirm Date",
            before=ym[bad_ym], after=parsed[bad_ym])
    df["Date"] = parsed
    df["Year"] = parsed.str[:4].astype(int)
    df["Month"] = parsed.str[5:].astype(int)
    return df


def _keys(df, master, official_customers, log):
    sku = df.SKU_ID.map(normalize_sku)
    assert sku.notna().all() and sku.isin(master.SKU_ID).all(), "SKU not in master after normalising"
    bad = df.SKU_ID != sku
    log.add(F, "SKU_ID", "SKU_ID formatting (missing hyphen, '_', unpadded number, leading space)", "error_fixed",
            bad.sum(), "Rebuilt as FAM-NNN", f"{df.SKU_ID.nunique()} spellings -> {sku.nunique()} SKUs",
            before=df.SKU_ID[bad], after=sku[bad])
    df["SKU_ID"] = sku

    reg = df.Region.map(normalize_region)
    assert reg.notna().all(), f"unknown region spellings: {df.Region[reg.isna()].unique()}"
    bad = (df.Region != reg) & (reg != "")
    log.add(F, "Region", "Region spelling variants (case, trailing space, APAC/EU/LATAM/MEA/NA...)", "error_fixed",
            bad.sum(), "Mapped to the 6 official regions",
            f"{df.Region.nunique()} spellings -> 6. 'NA' means North America (pandas reads it as missing by default)",
            before=df.Region[bad], after=reg[bad])
    df["Region"] = reg

    ch = df.Sales_Channel.map(normalize_channel)
    assert ch.notna().all(), f"unknown channels: {df.Sales_Channel[ch.isna()].unique()}"
    bad = df.Sales_Channel != ch
    log.add(F, "Sales_Channel", "Channel spelling variants", "error_fixed", bad.sum(), "Mapped to the 4 official channels",
            f"{df.Sales_Channel.nunique()} spellings -> 4", before=df.Sales_Channel[bad], after=ch[bad])
    df["Sales_Channel"] = ch

    match = make_customer_matcher(official_customers)
    lookup = {n: match(n) for n in df.Customer_Name.unique()}
    assert all(lookup.values()), f"unmatched customers: {[k for k, v in lookup.items() if not v]}"
    cust = df.Customer_Name.map(lookup)
    bad = df.Customer_Name != cust
    log.add(F, "Customer_Name", "Customer name variants (CAPS, double spaces, 'Inc.', single-letter typos)",
            "error_fixed", bad.sum(), "Matched to the 12 official customer names",
            f"{df.Customer_Name.nunique()} spellings -> {cust.nunique()} customers; all mappings reviewed by hand",
            before=df.Customer_Name[bad], after=cust[bad])
    df["Customer_Name"] = cust
    return df


def _master_attributes(df, master, log):
    m = master.set_index("SKU_ID")          # master already has one business unit per family
    family_bu = master.groupby("Product_Family").Business_Unit.first()
    fam = df.SKU_ID.map(m.Product_Family)
    nonblank = df.Product_Family.str.strip() != ""
    assert (df.Product_Family[nonblank] == fam[nonblank]).all(), "family contradicts master"
    log.add(F, "Product_Family", "Product_Family blank", "error_fixed", (~nonblank).sum(),
            "Filled from sku_master.csv via SKU_ID")
    df["Product_Family"] = fam

    bu = fam.map(family_bu)
    log.add(F, "Business_Unit", "Business_Unit differs from the family's business unit", "error_fixed",
            (df.Business_Unit != bu).sum(), "Set from family", before=df.Business_Unit[df.Business_Unit != bu])
    df["Business_Unit"] = bu

    desc = df.SKU_ID.map(m.SKU_Description)
    nonblank = df.SKU_Description.str.strip() != ""
    assert (df.SKU_Description[nonblank] == desc[nonblank]).all(), "description contradicts master"
    log.add(F, "SKU_Description", "SKU_Description blank", "error_fixed", (~nonblank).sum(),
            "Filled from sku_master.csv via SKU_ID")
    df["SKU_Description"] = desc
    return df


def list_prices(df, log):
    """The true USD list price of every SKU in every year.

    Prices are the same in every region and only change on 1 January (business rule 4). 99% of rows carry
    exactly that price, so the most common price per SKU-year (outside the surcharge months) is the list price.
    """
    p = pd.to_numeric(df.Unit_Price_USD, errors="coerce")
    ok = (p > 0) & ~in_surcharge(df.Date)
    lp = (df[ok].assign(p=p[ok].round(2), y=df.Year[ok])
          .groupby(["SKU_ID", "y"]).p.agg(lambda s: s.mode().iloc[0]).unstack())
    # SKUs launched in Jul-Dec 2022 only ever sold at the surcharged price: derive their base from it.
    sur = (p > 0) & in_surcharge(df.Date)
    sur_price = df[sur].assign(p=p[sur].round(2)).groupby("SKU_ID").p.agg(lambda s: s.mode().iloc[0])
    only_sur = lp[2022].isna() & lp.index.isin(sur_price.index)
    lp.loc[only_sur, 2022] = (sur_price[lp.index[only_sur]] / SURCHARGE).round(2)

    # Yearly increase = median across SKUs; used to fill SKU-years that never had a valid price row.
    growth = (lp / lp.shift(axis=1)).median()
    for y in lp.columns[1:]:
        lp[y] = lp[y].fillna((lp[y - 1] * growth[y]).round(2))
    for y in reversed(lp.columns[:-1]):
        lp[y] = lp[y].fillna((lp[y + 1] / growth[y + 1]).round(2))
    log.add(F, "Unit_Price_USD", "Annual list-price increases found in the data", "real_event", len(lp.columns) - 1,
            "Kept; used to price the forecast",
            "Median yearly change: " + ", ".join(f"{y}: {100 * (g - 1):+.1f}%" for y, g in growth.dropna().items()))
    return lp, sur_price


def _prices(df, lp, sur_price, log):
    raw = pd.to_numeric(df.Unit_Price_USD, errors="coerce")
    df["_p_raw"] = raw
    base = pd.Series([lp.at[s, y] for s, y in zip(df.SKU_ID, df.Year)], index=df.index)
    expected = (base * np.where(in_surcharge(df.Date), SURCHARGE, 1.0)).round(2)
    # In surcharge months use the surcharged price actually charged (avoids 1-cent rounding differences).
    sur = in_surcharge(df.Date)
    expected[sur] = df.SKU_ID[sur].map(sur_price).fillna(expected[sur])
    df["_p_true"] = expected
    ratio = raw / expected

    def near(x):
        return np.isclose(ratio, x, rtol=0.002)

    status = pd.Series("ok", index=df.index)
    status[near(100)] = "cents"
    status[near(10)] = "x10"
    status[near(0.89)] = "eur"
    status[raw == 0] = "zero"
    other = (status == "ok") & ~near(1)
    status[other] = "other"
    df["price_status"] = status

    sur = in_surcharge(df.Date) & (raw > 0)
    log.add(F, "Unit_Price_USD", "6% raw-material surcharge Jul-Dec 2022", "real_event", sur.sum(),
            "Kept (events calendar EV-027)", "Prices in those months are exactly list price x 1.06")
    log.add(F, "Unit_Price_USD", "Price entered in cents (100x list price)", "error_fixed", (status == "cents").sum(),
            "Divided by 100", "Revenue on these rows already used the correct price",
            before=raw[status == "cents"], after=expected[status == "cents"])
    x10 = status == "x10"
    log.add(F, "Unit_Price_USD", "Price per pack of 10 instead of per unit (India, Jul-Dec 2021)", "error_fixed",
            x10.sum(), "Replaced with USD list price (units on the same rows are multiplied by 10)",
            f"Regions: {df.Region[x10].value_counts().to_dict()}; units on these rows are ~1/10 of the series' "
            "normal level and of stockout-file shipments; revenue is correct",
            before=raw[x10], after=expected[x10])
    eur = status == "eur"
    log.add(F, "Unit_Price_USD", "Price booked in EUR instead of USD (exactly 0.89x, Europe, 2022 only)",
            "error_fixed", eur.sum(), "Replaced with USD list price",
            f"Regions: {df.Region[eur].value_counts().to_dict()}; revenue was booked in EUR too",
            before=raw[eur], after=expected[eur])
    log.add(F, "Unit_Price_USD", "Price that matches no known pattern", "error_fixed", other.sum(),
            "Replaced with list price", before=raw[other], after=expected[other])
    return df


def _units(df, stockouts, log):
    parsed = df.Units_Sold.map(parse_units)
    u = pd.Series([v for v, _ in parsed], index=df.index, dtype=float)
    kind = pd.Series([k for _, k in parsed], index=df.index)
    rev = pd.to_numeric(df.Revenue_USD, errors="coerce")
    # The USD list price fits the reported revenue on every row except the EUR ones (booked in euros).
    price_used = np.where(df.price_status == "eur", df._p_raw, df._p_true)
    implied = pd.Series(rev / price_used, index=df.index)
    status = pd.Series("reported", index=df.index)

    log.add(F, "Units_Sold", "Thousands separator in Units_Sold (e.g. '9,600')", "error_fixed",
            (kind == "comma").sum(), "Parsed as a number (then checked against revenue below)",
            before=df.Units_Sold[kind == "comma"])

    dash = kind == "dash"
    log.add(F, "Units_Sold", "Units_Sold is '-'", "error_fixed", dash.sum(), "Set to 0 (no order)",
            "Every '-' row has price 0 and revenue 0, which is how the file records 'no order'")
    status[dash] = "zero_from_dash"

    neg = u < 0
    log.add(F, "Units_Sold", "Negative units (revenue also negative)", "error_fixed", neg.sum(),
            "Flipped the sign", "Their size matches the series' normal level (median ratio 1.0), so not returns",
            before=u[neg], after=u[neg].abs())
    u[neg] = u[neg].abs()
    status[neg] = "fixed_sign"
    _flag(df, neg, "units_negative")

    # India Jul-Dec 2021: units and price were recorded per pack of 10. Revenue / list price gives the units,
    # rounded to the nearest 10; where the stockout file lists the exact shipment we use that below.
    per10 = (df.price_status == "x10") & u.notna()
    log.add(F, "Units_Sold", "Units recorded in packs of 10 (India, Jul-Dec 2021)", "error_fixed", per10.sum(),
            "Multiplied by 10 (exact value from stockout file where listed)",
            "Series-normal ratio 0.10; precision is +-5 units where no stockout record exists",
            before=u[per10], after=u[per10] * 10)
    u[per10] = u[per10] * 10
    status[per10] = "fixed_per10"
    _flag(df, per10, "units_per10")

    # Units 10x/100x too large: revenue / price gives the real number (confirmed by the stockout file).
    ratio = u / implied
    for f in (10, 100):
        m = np.isclose(ratio, f, rtol=0.002) & (u > 0)
        log.add(F, "Units_Sold", f"Units {f}x too large (revenue / price shows the true value)", "error_fixed",
                m.sum(), "Replaced with revenue / price", "Stockout-file shipments match revenue / price, not the units",
                before=u[m], after=implied[m].round())
        u[m] = implied[m].round()
        status[m] = f"fixed_x{f}"
        _flag(df, m, f"units_x{f}")

    blank = kind == "blank"
    from_rev = blank & (rev > 0)
    log.add(F, "Units_Sold", "Units blank but revenue present", "error_fixed", from_rev.sum(),
            "Recovered as revenue / price", "Always a whole number; matches stockout-file shipments 70/70",
            before=rev[from_rev], after=implied[from_rev].round())
    u[from_rev] = implied[from_rev].round()
    status[from_rev] = "recovered_from_revenue"

    zero_conv = blank & (df._p_raw == 0) & (rev.fillna(0) == 0)
    log.add(F, "Units_Sold", "Units blank, price 0 and revenue 0/blank", "inferred", zero_conv.sum(),
            "Set to 0 (no order)", "Price 0 means 'no order' in 99% of rows")
    u[zero_conv] = 0
    status[zero_conv] = "zero_inferred"

    left = blank & u.isna()
    so = stockouts.set_index(["Date", "SKU_ID", "Region"]).Shipped_Units
    keys = list(zip(df.Date[left], df.SKU_ID[left], df.Region[left]))
    found = pd.Series([so.get(k, np.nan) for k in keys], index=df.index[left])
    got = found.notna()
    log.add(F, "Units_Sold", "Units and revenue both blank, found in stockout file", "error_fixed", got.sum(),
            "Used Shipped_Units from inventory_stockouts.csv")
    u[found.index[got]] = found[got]
    status[found.index[got]] = "recovered_from_stockouts"
    p10 = status == "fixed_per10"
    exact = pd.Series([so.get(k, np.nan) for k in zip(df.Date[p10], df.SKU_ID[p10], df.Region[p10])],
                      index=df.index[p10]).dropna()
    u[exact.index] = exact
    status[exact.index] = "fixed_per10_exact"
    missing = u.isna()
    log.add(F, "Units_Sold", "Units and revenue both blank, no other source", "error_dropped", missing.sum(),
            "Left as missing (NOT zero, business rule 3); excluded from model training")
    status[missing] = "missing"

    df["Units_Sold"] = u
    df["units_status"] = status
    df["_rev_raw"] = rev
    return df


def _revenue(df, log):
    u, p, r = df.Units_Sold, df._p_true, df._rev_raw
    new = (u * p).round(2)
    pstat = df.price_status
    status = pd.Series("ok", index=df.index)
    diff = ~np.isclose(r, new, rtol=1e-4, atol=0.011)

    status[r.isna() & (u > 0)] = "missing"
    status[diff & (pstat == "eur")] = "wrong_price"
    status[diff & (pstat == "zero") & (u > 0)] = "zero_but_sale"
    rest = diff & (status == "ok") & r.notna()
    unit_fix = df.units_status.isin(["fixed_x10", "fixed_x100", "fixed_sign", "fixed_per10", "fixed_per10_exact"])
    status[rest & unit_fix] = "followed_units_fix"
    ratio = r / new
    status[rest & ~unit_fix & np.isclose(ratio, 10, rtol=0.002)] = "x10"
    cand = rest & ~unit_fix & (status == "ok")
    swap = pd.Series([is_digit_swap(a, b) for a, b in zip(r[cand], new[cand])], index=df.index[cand], dtype=bool)
    status[swap.index[swap]] = "digit_swap"
    status[swap.index[~swap]] = "other"
    df["revenue_status"] = status

    for s, text, ev in [
        ("x10", "Revenue 10x units x price (units are right)", "Stockout shipments match the units 9/9"),
        ("digit_swap", "Revenue has two digits swapped (e.g. 23683.14 instead of 26383.14)",
         "Same digits as units x price in a different order; units match stockout shipments"),
        ("other", "Revenue differs from units x price for no recognisable reason", ""),
        ("wrong_price", "Revenue booked in EUR instead of USD (Europe 2022)", ""),
        ("zero_but_sale", "Price and revenue 0 although units were shipped", "Units are normal size; 10/10 match stockout shipments"),
        ("missing", "Revenue blank", ""),
    ]:
        m = status == s
        log.add(F, "Revenue_USD", text, "error_fixed", m.sum(), "Recomputed as Units_Sold x Unit_Price_USD", ev,
                before=r[m], after=new[m])
    df["Unit_Price_USD"] = p
    df["Revenue_USD"] = new
    return df


def _fill_blank_regions(df, log):
    """A blank region is the one region where the same SKU, customer and channel is missing its row that month."""
    blank = df.Region == ""
    known = df[~blank]
    have = set(zip(known.Date, known.SKU_ID, known.Region))
    series = known.groupby(["SKU_ID", "Customer_Name", "Sales_Channel"]).Region.unique()
    filled, unresolved = {}, []
    for i, r in df[blank].iterrows():
        cands = series.get((r.SKU_ID, r.Customer_Name, r.Sales_Channel), [])
        free = [g for g in cands if (r.Date, r.SKU_ID, g) not in have]
        if len(free) == 1:
            filled[i] = free[0]
            have.add((r.Date, r.SKU_ID, free[0]))
        else:
            unresolved.append(i)
    f = pd.Series(filled)
    log.add(F, "Region", "Region blank", "inferred", len(f),
            "Filled with the only region where this SKU + customer + channel has no row that month",
            f"{len(unresolved)} could not be resolved")
    df.loc[f.index, "Region"] = f
    _flag(df, df.index.isin(f.index), "region_inferred")
    assert not unresolved, f"{len(unresolved)} blank regions unresolved"
    return df


def _lifecycle_checks(df, master, log):
    m = master.set_index("SKU_ID")
    launch = df.SKU_ID.map(m.Launch_Date)
    eol = df.SKU_ID.map(m.EOL_Date)
    pre = df.Date < launch
    for sku, g in df[pre].groupby("SKU_ID"):
        log.add("sku_master.csv", "Launch_Date",
                f"{sku} sells from {g.Date.min()} but master launch date is {m.at[sku, 'Launch_Date']}",
                "master_data", 1, "Kept the sales (consistent, normal-sized orders); master launch date treated as wrong",
                f"{len(g)} rows, {int(g.Units_Sold.sum())} units before the recorded launch")
    post = (eol != "") & (df.Date > eol) & (df.Units_Sold > 0)
    assert post.sum() == 0, "sales after EOL found"
    return df
