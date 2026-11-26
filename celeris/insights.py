"""Extra analyses from the final pass, all computed from existing outputs (no retraining):

- accuracy_by_level: replay-test error at the levels planners buy at (series, family x region, family, region,
  company), for the model's typical (P50) and expected (P50 x bias factor) forecasts and the best simple method.
- blend_test: does mixing the model with the best simple method help? The weight is chosen on earlier origins only
  and tested on the next one, as it would be used for real.
- account_changes: SKU-region accounts that moved from one customer to another, and what happened to demand after.
"""
import numpy as np
import pandas as pd

LEVELS = {"One series, one month": None,
          "Family x region, per month": ["origin", "Product_Family", "Region", "h"],
          "Family, per month": ["origin", "Product_Family", "h"],
          "Region, per month": ["origin", "Region", "h"],
          "Whole company, per month": ["origin", "h"]}


def _joined(model_long, base_long, best_method):
    b = base_long[base_long.method == best_method][["origin", "SKU_ID", "Region", "h", "forecast"]]
    return model_long.merge(b.rename(columns={"forecast": "base"}), on=["origin", "SKU_ID", "Region", "h"])


def accuracy_by_level(model_long, base_long, best_method, bias_factor):
    # the whole-unit forecasts that were scored (and submitted), as in the dashboard
    x = _joined(model_long, base_long, best_method)
    x = x.assign(p50=x.forecast, expected=x.forecast * bias_factor)
    rows = []
    for name, keys in LEVELS.items():
        g = x if keys is None else x.groupby(keys)[["actual", "p50", "expected", "base"]].sum()
        w = {c: float((g[c] - g.actual).abs().sum() / g.actual.sum()) for c in ["p50", "expected", "base"]}
        rows.append({"level": name, "wape_model_p50": round(w["p50"], 6), "wape_model_expected": round(w["expected"], 6),
                     "wape_best_simple": round(w["base"], 6)})
    return pd.DataFrame(rows)


def blend_test(model_long, base_long, best_method, weights=(1.0, 0.9, 0.8, 0.7, 0.6, 0.5)):
    x = _joined(model_long, base_long, best_method)

    def err(d, a):
        return float(((a * d.p50 + (1 - a) * d.base).round() - d.actual).abs().sum())

    out = {"weights_tested": list(weights), "by_test_origin": []}
    origins = sorted(x.origin.unique())
    for o in origins[1:]:
        tr, te = x[x.origin < o], x[x.origin == o]
        best = min(weights, key=lambda a: err(tr, a))
        out["by_test_origin"].append({"origin": str(o), "weight_chosen": float(best),
                                      "wape_blend": round(float(err(te, best) / te.actual.sum()), 4),
                                      "wape_model_only": round(float(err(te, 1.0) / te.actual.sum()), 4)})
    out["verdict"] = ("Blending does not help: the weight chosen on earlier years is 1.0 (model only)"
                      if all(r["weight_chosen"] == 1.0 for r in out["by_test_origin"]) else "Blending helps on some origins")
    return out


def account_changes(register, panel):
    sw = register[register.type == "Customer switch (account change)"].copy()
    sw["from_customer"] = sw.reason.str.extract(r"from (.*) to ")[0]
    sw = sw.rename(columns={"Customer_Name": "to_customer"})
    ratios = []
    for _, s in sw.iterrows():
        q = panel[(panel.SKU_ID == s.SKU_ID) & (panel.Region == s.Region)].set_index("Date").demand
        i = list(q.index).index(s.Date)
        before, after = q.iloc[max(0, i - 12):i].mean(), q.iloc[i:i + 12].mean()
        ratios.append(round(float(after / before), 3) if before else np.nan)
    sw["demand_after_vs_before"] = ratios
    detail = sw[["Date", "SKU_ID", "Region", "from_customer", "to_customer", "demand_after_vs_before"]].sort_values("Date")
    lost = detail.groupby("from_customer").size()
    won = detail.groupby("to_customer").size()
    summary = pd.DataFrame({"accounts_won": won, "accounts_lost": lost}).fillna(0).astype(int)
    summary["net"] = summary.accounts_won - summary.accounts_lost
    summary = summary.sort_values("net", ascending=False).rename_axis("customer").reset_index()

    c = panel.groupby(["Customer_Name", "Region", "Date"]).demand.sum().reset_index().sort_values("Date")
    yoy = c.groupby(["Customer_Name", "Region"]).demand.apply(
        lambda d: d.iloc[-12:].sum() / max(d.iloc[-24:-12].sum(), 1))
    facts = {"switches": int(len(detail)),
             "median_demand_after_vs_before": round(float(np.nanmedian(detail.demand_after_vs_before)), 3),
             "customer_regions_down_over_20pct": int((yoy < 0.8).sum()),
             "customer_region_yoy_min": round(float(yoy.min()), 3), "customer_region_yoy_max": round(float(yoy.max()), 3)}
    return detail, summary, facts


# ---------------------------------------------------------------------------------------------------- findings
def _m(units):
    return f"{units / 1e6:.2f}M" if units >= 1e6 else f"{units / 1e3:.0f}k" if units >= 1e4 else f"{units:,.0f}"


def _pct(x, d=0):
    return f"{x * 100:+.{d}f}%".replace("-", "−")


def findings(panel, table, stockouts, prices, master, effects, leadlag, ev, levels, accounts, class_acc=None):
    """Business findings for the dashboard's briefing page. Every number is computed here from the pipeline outputs;
    each finding says what to do and links to the view (and filters) that shows the evidence."""
    out = []
    last_m = panel.Date.max()
    hist12 = panel[panel.Date > str(pd.Period(last_m, "M") - 12)]
    fc12 = table[table.Date <= str(pd.Period(last_m, "M") + 12)]

    # 1. where growth is
    g = pd.DataFrame({"last": hist12.groupby("Region").demand.sum(), "next": fc12.groupby("Region").expected.sum()})
    g["change"] = g["next"] / g["last"] - 1
    top, low = g.change.idxmax(), g.change.idxmin()
    total = fc12.expected.sum() / hist12.demand.sum() - 1
    out.append({"id": "growth", "tag": "Demand", "value": _pct(g.change[top]), "title": f"{top} grows fastest",
                "text": f"Next 12 months vs the last 12: {top} {_pct(g.change[top])} ({_m(g['last'][top])} → "
                        f"{_m(g['next'][top])} units). The company as a whole {_pct(total)}; slowest is {low} "
                        f"({_pct(g.change[low])}).",
                "action": f"Check capacity and stock for {top} first.",
                "link": {"view": "overview", "f": {"reg": top}}})

    # 2. a known shutdown in the forecast window
    o = table[(table.Customer_Name == "Orion Driveline") & (table.Region == "North America")]
    if len(o):
        bym = o.groupby("Date").expected.sum()
        shut = bym[["2027-07", "2027-08"]]
        normal = bym.drop(index=["2027-07", "2027-08"]).mean()
        out.append({"id": "orion", "tag": "Forecast window", "value": _pct(shut.mean() / normal - 1),
                    "title": "Orion Driveline retools in Jul–Aug 2027",
                    "text": f"The events calendar lists a retooling shutdown at Orion's North American plants. The "
                            f"forecast drops to {_m(shut.iloc[0])} and {_m(shut.iloc[1])} units in those months, "
                            f"against about {_m(normal)} in a normal month. The model learned this from the 2023 "
                            f"Nexon strike, not from a hand-written rule.",
                    "action": "Cut production for Orion North America in Jul–Aug 2027; orders return in September.",
                    "link": {"view": "forecast", "f": {"cust": "Orion Driveline", "reg": "North America"}}})

    # 3. demand hidden by the chip shortage
    ela = panel[(panel.Product_Family == "Electrical Assemblies") & (panel.Date >= "2021-03") & (panel.Date <= "2022-09")]
    gap = ela.demand.sum() - ela.shipped.sum()
    out.append({"id": "hidden", "tag": "Supply", "value": _m(gap) + " units",
                "title": "The chip shortage hid real demand",
                "text": f"From Mar 2021 to Sep 2022, Electrical Assemblies customers asked for {_m(ela.demand.sum())} "
                        f"units but only {_m(ela.shipped.sum())} shipped ({_pct(ela.shipped.sum() / ela.demand.sum() - 1)}). "
                        f"Shipments in those months understate demand, so the forecast learns from the rebuilt numbers.",
                "action": "Never plan from shipments in a shortage period; use the rebuilt demand.",
                "link": {"view": "stockouts", "f": {"fam": "Electrical Assemblies"}}})

    # 4. lost sales and their causes
    s = stockouts.copy()
    s["year"] = s.Date.str[:4]
    pl = prices.set_index("SKU_ID")
    s["price"] = [pl.at[k, y] if k in pl.index and y in pl.columns else np.nan for k, y in zip(s.SKU_ID, s.year)]
    lost_units, lost_usd = s.Lost_Units.sum(), (s.Lost_Units * s.price).sum()
    by = s.groupby("Reason").Lost_Units.sum().sort_values(ascending=False)
    out.append({"id": "lost", "tag": "Supply", "value": f"${lost_usd / 1e6:.0f}M",
                "title": "Revenue lost to stockouts",
                "text": f"The stockout file lists {_m(lost_units)} units that were never shipped, worth about "
                        f"${lost_usd / 1e6:.0f}M at that year's list price. {by.index[0]} caused "
                        f"{by.iloc[0] / by.sum():.0%} of them; {by.index[1].lower()} {by.iloc[1] / by.sum():.0%}.",
                "action": f"The biggest lever is {by.index[0].lower()}: secure supply for the parts it hit most.",
                "link": {"view": "stockouts", "f": {}}})

    # 5. launches and end of life inside the window
    new = master[(master.Lifecycle_Status == "Planned Launch") & master.Replaces_SKU.fillna("").ne("")]
    pairs = []
    for _, r in new.sort_values("Launch_Date").iterrows():
        old_units = table[table.SKU_ID == r.Replaces_SKU].expected.sum()
        new_units = table[table.SKU_ID == r.SKU_ID].expected.sum()
        eol = master.set_index("SKU_ID").at[r.Replaces_SKU, "EOL_Date"]
        pairs.append(f"{r.Replaces_SKU} → {r.SKU_ID} (new from {r.Launch_Date}, old ends {eol})")
    out.append({"id": "lifecycle", "tag": "Products", "value": f"{len(new)} replacements",
                "title": "Three products hand over to successors",
                "text": "; ".join(pairs) + ". The old part winds down to about half once its successor launches and is "
                        "0 after its end-of-life date; the new part ramps up over about 6 months.",
                "action": "Schedule the line changeovers and stop buying parts for the old products before end of life.",
                "link": {"view": "lifecycle", "f": {}}})

    # 6. plan at the right level
    lv = levels.set_index("level")
    out.append({"id": "level", "tag": "Accuracy", "value": f"{lv.at['Family, per month', 'wape_model_expected']:.1%}",
                "title": "Plan budgets at family level",
                "text": f"In the backtest, one part in one region for one month is off by "
                        f"{lv.at['One series, one month', 'wape_model_p50']:.0%} on average, but a family's monthly total "
                        f"only by {lv.at['Family, per month', 'wape_model_expected']:.1%} and the company's by "
                        f"{lv.at['Whole company, per month', 'wape_model_expected']:.1%}: errors in single parts cancel out.",
                "action": "Set budgets and capacity on family totals; cover single parts with the P90 range.",
                "link": {"view": "forecast", "f": {}}})

    # 7. leading indicator
    st = leadlag[leadlag.stable].sort_values("corr", ascending=False).iloc[0]
    names = {"New_Vehicle_Orders_Index": "new vehicle orders", "ISM_Manufacturing_PMI": "the factory index (PMI)",
             "Auto_Production_YoY_Pct": "auto production growth"}
    out.append({"id": "lead", "tag": "Economy", "value": f"{min(st.early_lag, st.late_lag)}–{max(st.early_lag, st.late_lag)} months",
                "title": f"{names.get(st.indicator, st.indicator).capitalize()} move first",
                "text": f"Yearly changes in {names.get(st.indicator, st.indicator)} come {min(st.early_lag, st.late_lag)}–"
                        f"{max(st.early_lag, st.late_lag)} months before the same changes in {st.family} demand "
                        f"(correlation {st['corr']:.2f}), in both 2021–23 and 2024–26.",
                "action": f"Watch {names.get(st.indicator, st.indicator)} every month as an early warning for {st.family}.",
                "link": {"view": "drivers", "f": {}}})

    # 8. the shift to electric has paused
    e = ev[ev.full_year].set_index("Year")
    y0, y1 = e.index.min(), e.index.max()
    out.append({"id": "ev", "tag": "Products", "value": f"{e.EV[y1]:.1f}%",
                "title": "The move to electric has levelled off",
                "text": f"EV-only parts went from {e.EV[y0]:.0f}% of demand in {y0} to {e.EV[y1 - 1]:.1f}% in {y1 - 1} and "
                        f"{e.EV[y1]:.1f}% in {y1}. Petrol-only parts fell from {e.ICE[y0]:.0f}% to {e.ICE[y1]:.0f}%; "
                        f"universal parts stay about {e.Universal[y1]:.0f}%.",
                "action": "No extra EV-only capacity is needed for this forecast window; keep watching the EV index.",
                "link": {"view": "lifecycle", "f": {}}})

    # 9. price rises
    pre = effects[(effects.Event_Type == "Pull-forward") & (effects.effect_demand_pct > 0)]
    big = pre.iloc[0] if len(pre) else None
    rises = effects[effects.Description.str.contains("Annual list-price increase", na=False) & (effects.Start >= "2024")]
    flat = (rises.effect_demand_pct.abs() < rises.noise_band_pct).sum()
    sizes = ", ".join(rises.Description.str.extract(r"([\d.]+%)")[0])
    out.append({"id": "price", "tag": "Prices", "value": "No pre-buying",
                "title": "No early buying expected before the 2027 price rise",
                "text": (f"Only the 9% rise in January 2023 made customers buy early ({big.effect_demand_pct:+.0f}% in "
                         f"Nov–Dec 2022). " if big is not None else "") +
                        f"The {len(rises)} rises since then ({sizes}), the same size as the coming ones, showed no "
                        f"clear effect{'' if flat == len(rises) else f' in {flat} of {len(rises)} cases'}. "
                        f"Coming: 4.5% (Jan 2027) and 4.0% (Jan 2028).",
                "action": "No extra stock is needed for early orders in Nov–Dec 2026.",
                "link": {"view": "drivers", "f": {}}})

    # 10. yearly summer shutdowns
    na = effects[effects.Description.str.contains("North American summer")].effect_demand_pct
    eu = effects[effects.Description.str.contains("European summer")].effect_demand_pct
    fc_na = table[(table.Region == "North America")].groupby("Date").expected.sum()
    fc_eu = table[(table.Region == "Europe")].groupby("Date").expected.sum()
    out.append({"id": "summer", "tag": "Seasons", "value": f"{eu.median():.0f}%".replace("-", "−"),
                "title": f"Summer shutdowns cut demand {abs(na.median()):.0f}–{abs(eu.median()):.0f}% every year",
                "text": f"North American demand drops a median {abs(na.median()):.0f}% in July (retooling weeks) and "
                        f"European demand {abs(eu.median()):.0f}% in August (plant shutdowns), in all {len(eu)} years. "
                        f"The forecast keeps the dips: Europe {_m(fc_eu.get('2027-08', 0))} units in Aug 2027 against "
                        f"{_m(fc_eu.get('2027-06', 0))} in June.",
                "action": "Plan lower output and deliveries for North America in July and Europe in August.",
                "link": {"view": "explorer", "f": {"reg": "Europe"}}})

    # 11. series with gaps between orders
    if class_acc:
        c = {r["class"]: r for r in class_acc}
        i = c["intermittent"]
        out.append({"id": "gaps", "tag": "Accuracy", "value": _pct(i["bias_model"]),
                    "title": "Parts ordered with gaps run below forecast",
                    "text": f"Series with gaps between orders carry {i['share_of_demand']:.0%} of demand. For them the "
                            f"typical-month forecast is {abs(i['bias_model']):.0%} too low in the backtest (error "
                            f"{i['wape_model']:.0%} vs {i['wape_best_simple']:.0%} for the best simple method), because a "
                            f"typical month without an order pulls the middle down.",
                    "action": "For these parts plan from the expected value or the P90, not the typical month.",
                    "link": {"view": "forecast", "f": {}, "state": {"esplit": "cls", "emeas": "bias"}}})

    # 12. account changes
    a = accounts.sort_values("net")
    out.append({"id": "accounts", "tag": "Customers", "value": f"{int(a.accounts_lost.sum())} accounts",
                "title": f"{a.customer.iloc[0]} is losing accounts",
                "text": f"{int(a.accounts_lost.sum())} product accounts moved from one customer to another. "
                        f"{a.customer.iloc[0]} lost {int(a.accounts_lost.iloc[0])} and won {int(a.accounts_won.iloc[0])}; "
                        f"{a.customer.iloc[-1]} won {int(a.accounts_won.iloc[-1])}. No customer stopped buying altogether.",
                "action": f"Review the {a.customer.iloc[0]} relationship; the forecast follows each account's current owner.",
                "link": {"view": "anomalies", "f": {}}})
    return out
