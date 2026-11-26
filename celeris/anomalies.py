"""Anomaly register (one line per unusual point) and measured impact of every calendar event.

Kinds of anomaly:
  data_error  - the record was wrong; fixed in cleaning
  data_loss   - the value was never recorded; estimated, and excluded from model training
  real_event  - something really happened; kept, explained, and handled in the forecast
  master_data - reference data contradicts the transactions
"""
import numpy as np
import pandas as pd

KEY = ["Date", "SKU_ID", "Region", "Customer_Name", "Product_Family", "Sales_Channel"]

ROW_RULES = [  # (column, value, type, kind, reason, action)
    ("units_status", "fixed_x10", "Units 10x too large", "data_error",
     "Revenue / price shows the true units", "Fixed: units = revenue / price"),
    ("units_status", "fixed_x100", "Units 100x too large", "data_error",
     "Revenue / price shows the true units", "Fixed: units = revenue / price"),
    ("units_status", "fixed_sign", "Negative units", "data_error",
     "Size matches the series' normal level, so a sign typo, not a return", "Fixed: sign flipped"),
    ("units_status", "fixed_per10", "Units counted in packs of 10", "data_error",
     "India Jul-Dec 2021: units ~1/10 of normal and price 10x", "Fixed: units x 10, price / 10"),
    ("units_status", "fixed_per10_exact", "Units counted in packs of 10", "data_error",
     "India Jul-Dec 2021: units ~1/10 of normal and price 10x", "Fixed: exact units from stockout file"),
    ("units_status", "recovered_from_revenue", "Units blank", "data_error",
     "Revenue and price were present", "Fixed: units = revenue / price"),
    ("price_status", "cents", "Price typed in cents", "data_error", "Price 100x the list price", "Fixed: price / 100"),
    ("price_status", "eur", "Price booked in EUR", "data_error",
     "Exactly 0.89x the USD list price, Europe 2022", "Fixed: USD list price, revenue recomputed"),
    ("revenue_status", "x10", "Revenue 10x too large", "data_error", "Units and price are right", "Fixed: revenue recomputed"),
    ("revenue_status", "digit_swap", "Revenue digits swapped", "data_error",
     "Same digits as units x price in another order", "Fixed: revenue recomputed"),
    ("revenue_status", "zero_but_sale", "Price and revenue missing on a real sale", "data_error",
     "Units confirmed by the stockout file", "Fixed: list price, revenue recomputed"),
]

SOURCE_RULES = {  # demand_source -> (type, kind, reason, action)
    "stockout_requested": ("Stockout", "real_event", None, "Demand = requested units from the stockout file"),
    "shortage_estimated": ("Chip shortage (month not in stockout file)", "real_event",
                           "Electrical Assemblies shipped below demand Mar 2021-Sep 2022 (EV-019)",
                           "Demand estimated = shipped x that month's shortfall ratio (tested: 11% error)"),
    "catchup_removed": ("Catch-up shipment after a stockout", "real_event",
                        "Back-ordered units from last month were shipped this month",
                        "Demand = shipped - back-ordered units"),
    "imputed_unlisted_constraint": ("Constrained month missing from stockout file", "real_event",
                                    "Shipped less than last month's back-order", "Demand estimated; weight 0 in training"),
    "imputed_erp_loss": ("Lost in ERP migration", "data_loss",
                         "Latin America Aftermarket & Export, Mar-May 2023 (EV-033): units and revenue blank",
                         "Demand estimated from the same point of the ordering cycle; weight 0 in training"),
    "imputed_missing": ("Month missing from the extract", "data_loss", "No row for this month (missing is not zero)",
                        "Demand estimated from the same point of the ordering cycle; weight 0 in training"),
}


def build_register(panel, sales, master):
    parts = []
    s = sales.merge(panel[["Date", "SKU_ID", "Region", "demand", "shipped"]], on=["Date", "SKU_ID", "Region"])
    for col, val, typ, kind, reason, action in ROW_RULES:
        x = s[s[col] == val]
        parts.append(_rows(x, typ, kind, reason, action))
    yr = s[s.fix_flags.str.contains("date_year_typo")]
    parts.append(_rows(yr, "Year typed as 2062", "data_error", "Load timestamp and Year column say 2026",
                       "Fixed: date = load month - 1"))

    for src, (typ, kind, reason, action) in SOURCE_RULES.items():
        x = panel[panel.demand_source == src]
        if src == "stockout_requested":
            reason = ("Shipped " + x.shipped.fillna(0).astype(int).astype(str) + " of "
                      + x.Requested_Units.astype(int).astype(str) + " requested (" + x.Reason + ")")
        parts.append(_rows(x, typ, kind, reason, action))

    parts.append(one_off_orders(panel))
    parts.append(event_points(panel))
    parts.append(customer_switches(panel))
    parts.append(cycle_changes(panel))
    parts.append(lifecycle_points(panel, master))

    reg = pd.concat([p for p in parts if len(p)], ignore_index=True)
    reg = reg.sort_values(["Date", "SKU_ID", "Region", "type"]).reset_index(drop=True)
    reg.insert(0, "anomaly_id", [f"AN-{i:06d}" for i in range(1, len(reg) + 1)])
    return reg


def _rows(x, typ, kind, reason, action, expected=None):
    out = x[KEY].copy()
    out["type"], out["kind"] = typ, kind
    out["shipped"] = x.get("shipped", x.get("Units_Sold"))
    out["demand"] = x.get("demand")
    out["expected"] = expected if expected is not None else np.nan
    out["reason"], out["action"] = reason, action
    return out


def usual_order_size(panel):
    """Rolling median of a series' non-zero demand over its 9 nearest orders."""
    pos = panel[panel.demand > 0]
    med = pos.groupby(["SKU_ID", "Region"]).demand.transform(
        lambda s: s.rolling(9, center=True, min_periods=4).median())
    return med.reindex(panel.index)


def one_off_orders(panel, factor=3.0, min_extra=50):
    p = panel.copy()
    p["usual"] = usual_order_size(p)
    x = p[(p.demand > factor * p.usual) & (p.demand - p.usual >= min_extra)
          & p.demand_source.isin(["shipped", "catchup_removed", "stockout_requested"])]
    reason = ("Order of " + x.demand.astype(int).astype(str) + " units vs a usual order of about "
              + x.usual.round().astype(int).astype(str))
    return _rows(x, "One-off bulk order", "real_event", reason,
                 "Kept in history; capped at the usual order size for model training", expected=x.usual.round())


EVENT_WINDOWS = [  # region-or-customer scoped disruptions visible at series level
    ("COVID-19 lockdown", "Region", "Asia Pacific", "2020-02", "2020-03", "EV-009"),
    ("COVID-19 lockdown", "Region", "Europe", "2020-03", "2020-05", "EV-010"),
    ("COVID-19 lockdown", "Region", "India", "2020-04", "2020-06", "EV-011"),
    ("COVID-19 lockdown", "Region", "Latin America", "2020-04", "2020-06", "EV-012"),
    ("COVID-19 lockdown", "Region", "North America", "2020-04", "2020-06", "EV-013"),
    ("Strike at customer plants", "Customer", ("Nexon Motor Group", "Europe"), "2023-10", "2023-11", "EV-038"),
]


def event_points(panel):
    p = panel.copy()
    p["usual"] = usual_order_size(p)
    out = []
    for typ, scope, val, a, b, ev in EVENT_WINDOWS:
        if scope == "Region":
            m = p.Region == val
        else:
            m = (p.Customer_Name == val[0]) & (p.Region == val[1])
        x = p[m & p.Date.between(a, b) & (p.usual > 0) & (p.demand < 0.5 * p.usual)]
        out.append(_rows(x, typ, "real_event", f"Demand under half the usual order during {ev} ({a} to {b})",
                         "Kept in history; down-weighted in model training", expected=x.usual.round()))
    return pd.concat(out, ignore_index=True)


def customer_switches(panel):
    p = panel.sort_values(["SKU_ID", "Region", "m"])
    prev = p.groupby(["SKU_ID", "Region"]).Customer_Name.shift()
    x = p[prev.notna() & (prev != p.Customer_Name)]
    reason = "Customer changed from " + prev[x.index] + " to " + x.Customer_Name
    return _rows(x, "Customer switch (account change)", "real_event", reason,
                 "Forecast follows the current customer; earlier history kept as the series' level")


def cycle_changes(panel, split="2023-01"):
    """Series whose usual gap between orders differs between 2019-2022 and 2023-2026, dated where it changed."""
    pos = panel[panel.demand > 0].sort_values(["SKU_ID", "Region", "m"])
    pos = pos.assign(gap=pos.groupby(["SKU_ID", "Region"]).m.diff())
    mode = lambda s: s.dropna().mode().iloc[0] if s.notna().sum() >= 3 else np.nan
    early = pos[pos.Date < split].groupby(["SKU_ID", "Region"]).gap.agg(mode)
    late = pos[pos.Date >= split].groupby(["SKU_ID", "Region"]).gap.agg(mode)
    both = pd.concat([early.rename("early"), late.rename("late")], axis=1).dropna()
    changed = both[both.early != both.late]
    rows = []
    for (sku, reg), r in changed.iterrows():
        g = pos[(pos.SKU_ID == sku) & (pos.Region == reg)]
        # first order after which the gap pattern matches the new cycle for 3 orders in a row
        gaps = g.gap.to_numpy()
        when = g.Date.iloc[-1]
        for i in range(1, len(gaps) - 2):
            if (gaps[i:i + 3] == r.late).all() and g.Date.iloc[i] >= "2021-01":
                when = g.Date.iloc[i]
                break
        row = panel[(panel.SKU_ID == sku) & (panel.Region == reg) & (panel.Date == when)].iloc[0]
        rows.append({**{k: row[k] for k in KEY}, "type": "Ordering rhythm changed", "kind": "real_event",
                     "shipped": row.shipped, "demand": row.demand, "expected": np.nan,
                     "reason": f"Ordered every {int(r.early)} month(s) before, every {int(r.late)} month(s) after",
                     "action": "Forecast uses the recent rhythm"})
    return pd.DataFrame(rows)


def lifecycle_points(panel, master):
    m = master.set_index("SKU_ID")
    out = []
    last = panel.groupby(["SKU_ID", "Region"]).m.idxmax()
    ended = panel.loc[last]
    ended = ended[ended.SKU_ID.map(m.EOL_Date).notna() & (ended.Date < panel.Date.max())]
    out.append(_rows(ended, "Series ends: product discontinued", "real_event",
                     "Last month before the product's end-of-life date", "Forecast = 0 after end of life (rule 5)"))
    launch = panel.SKU_ID.map(m.Launch_Date)
    pre = panel[(panel.Date < launch) & (panel.shipped > 0)]
    out.append(_rows(pre, "Sale before the master launch date", "master_data",
                     "SKU master launch date is later than the first sales", "Sales kept; master launch date flagged"))
    return pd.concat(out, ignore_index=True)


# ------------------------------------------------------------------ event impact

DERIVED_EVENTS = [  # patterns found in the data that the calendar only implies
    {"Event_ID": "DATA-1", "Start_Month": "2022-11", "End_Month": "2022-12", "Scope_Type": "Global", "Scope_Value": "All",
     "Event_Type": "Pull-forward", "Description": "Customers order early before the ~9% price rise announced Nov 2022"},
    {"Event_ID": "DATA-2", "Start_Month": "2023-01", "End_Month": "2023-02", "Scope_Type": "Global", "Scope_Value": "All",
     "Event_Type": "Pull-forward", "Description": "Orders dip after the early buying of Nov-Dec 2022"},
]


def event_effects(panel, events):
    """How much did each calendar event move demand (and shipments)?

    Recurring holidays: compare the affected region with the other regions that have no holiday in the same
    months (difference-in-differences against the 3 months either side).
    Everything else: compare the affected group with ITSELF in the same calendar months of other years, which
    removes normal seasonality and recurring holidays.
    """
    d = panel.groupby(["Date", "Region", "Customer_Name", "Sales_Channel", "Product_Family"])[["demand", "shipped"]].sum()
    d = d.reset_index()
    months = sorted(panel.Date.unique())
    ev = pd.concat([events, pd.DataFrame(DERIVED_EVENTS)], ignore_index=True)
    holidays = ev[ev.Event_Type == "Holiday"]
    rows = []
    for _, e in ev.iterrows():
        a = e.Start_Month
        open_ended = not isinstance(e.End_Month, str) or not e.End_Month
        b = months[-1] if open_ended else e.End_Month          # open-ended events run to the end of history
        if a > months[-1] or a < months[0]:
            continue
        b = min(b, months[-1])
        inside = [m for m in months if a <= m <= b]
        i0, i1 = months.index(inside[0]), months.index(inside[-1])
        around = months[max(0, i0 - 3):i0] + months[i1 + 1:i1 + 4]
        aff = _scope_mask(d, e.Scope_Type, e.Scope_Value)
        res = {"Event_ID": e.Event_ID, "Start": a, "End": "" if open_ended else e.End_Month,
               "Scope": f"{e.Scope_Type}: {e.Scope_Value}", "Event_Type": e.Event_Type, "Description": e.Description}
        if d[aff & d.Date.isin(inside)].demand.sum() == 0:
            res.update(effect_demand_pct=np.nan, effect_shipped_pct=np.nan, method="",
                       verdict="Not applicable: no sales in this scope yet")
            rows.append(res)
            continue
        if e.Event_Type == "Holiday":
            busy = holidays[(holidays.Start_Month <= b) & (holidays.End_Month.fillna(holidays.Start_Month) >= a)]
            ctl = ~aff & (e.Scope_Type == "Region") & ~d.Region.isin(busy.Scope_Value)
            method = "vs other regions without a holiday, same months"
        else:
            ctl = None
            method = "vs the same months in other years"
        for q in ["demand", "shipped"]:
            res[f"effect_{q}_pct"] = round(100 * _did(d, aff, ctl, inside, around, months, q), 1)
        res["method"] = method
        res["verdict"] = _verdict(e, res)
        if len(around) < 6:
            res["verdict"] += "; low confidence (history starts or ends next to it)"
        rows.append(res)
    return pd.DataFrame(rows)


def _scope_mask(d, scope, val):
    if scope == "Region":
        return d.Region == val
    if scope == "Customer":
        cust, reg = [v.strip() for v in val.split("|")]
        return (d.Customer_Name == cust) & (d.Region == reg)
    if scope == "Channel":
        return d.Sales_Channel == val
    if scope == "Product_Family":
        return d.Product_Family == val
    return d.Date.notna()


# How far a month moves by pure chance (95th percentile of |effect| on 300 random event-free months, 2019-2026).
NOISE_BAND = {"Global": 7.3, "Region": 11.7, "Channel": 11.7, "Product_Family": 11.7, "Customer": 17.7}


def _verdict(e, r):
    if e.Event_Type == "Market Entry":
        return "Structural: a new region starts (series ramp up from zero)"
    dem, shp = r["effect_demand_pct"], r["effect_shipped_pct"]
    band = NOISE_BAND.get(e.Scope_Type, 11.7)
    r["noise_band_pct"] = band
    if pd.isna(dem):
        return "Not measurable (not enough comparable history)"
    if abs(dem) < band and abs(shp) >= band:
        return "Shipments/records affected, demand not: handled in the demand rebuild"
    if abs(dem) < band:
        return f"No clear effect (within the ±{band}% chance range)"
    if dem < 0 and shp < dem - 10:
        return "Lowered demand; shipments fell further"
    if shp <= -band and dem > 0:
        return "Shipments cut while demand held up: supply problem, not demand"
    return "Raised demand" if dem > 0 else "Lowered demand"


def _did(d, aff, ctl, inside, around, months, q):
    A = d[aff].groupby("Date")[q].sum()
    ra = A.reindex(inside).sum() / max(A.reindex(around).mean() * len(inside), 1e-9)
    if ctl is not None:
        C = d[ctl].groupby("Date")[q].sum()
        rc = C.reindex(inside).sum() / max(C.reindex(around).mean() * len(inside), 1e-9)
        return ra / rc - 1
    ratios = []
    for shift in (-36, -24, -12, 12, 24, 36):
        ins = [_shift(m, shift) for m in inside]
        aro = [_shift(m, shift) for m in around]
        if all(m in months for m in ins + aro) and A.reindex(ins + aro).fillna(0).min() > 0:
            ratios.append(A.reindex(ins).sum() / max(A.reindex(aro).mean() * len(ins), 1e-9))
    return ra / np.median(ratios) - 1 if ratios else np.nan


def _shift(month, k):
    return str(pd.Period(month, freq="M") + k)
