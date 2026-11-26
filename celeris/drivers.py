"""Which economic indicator leads which product family, by how many months, and is it stable? Plus the EV shift.

Method: for each family, demand growth vs the same month a year earlier (log ratio) is correlated with each
indicator's change vs a year earlier, shifted by 0-8 months (indicator first). Year-on-year changes remove
seasonality. Only series sold for the whole 2019-2026 period are used, so new regions and launches don't fake growth.
Stability: the same calculation on 2021-07..2023-12 and 2024-01..2026-10 separately (COVID months left out).
"""
import numpy as np
import pandas as pd

LAGS = range(0, 9)
WINDOWS = {"all": ("2020-01", "2026-10"), "early": ("2021-07", "2023-12"), "late": ("2024-01", "2026-10")}
INDICATORS = ["ISM_Manufacturing_PMI", "Auto_Production_YoY_Pct", "Vehicle_Inventory_Days", "Raw_Material_Cost_Index",
              "New_Vehicle_Orders_Index", "Industrial_Util_Rate_Pct", "Fuel_Price_Index", "EV_Adoption_Index"]


def family_growth(panel):
    span = panel.groupby(["SKU_ID", "Region"]).Date.agg(["min", "max"])
    full = span[(span["min"] == panel.Date.min()) & (span["max"] == panel.Date.max())].index
    q = panel.set_index(["SKU_ID", "Region"]).loc[full].reset_index()
    fam = q.pivot_table(index="Date", columns="Product_Family", values="demand", aggfunc="sum")
    return np.log(fam).diff(12)


def lead_lag(panel, macro):
    g = family_growth(panel)
    mac = macro.set_index("Date")[INDICATORS].diff(12)
    rows = []
    for fam in g.columns:
        for ind in INDICATORS:
            for L in LAGS:
                x = mac[ind].shift(L).reindex(g.index)
                rec = {"family": fam, "indicator": ind, "lag": L}
                for w, (a, b) in WINDOWS.items():
                    m = x.notna() & g[fam].notna() & (g.index >= a) & (g.index <= b)
                    rec[f"corr_{w}"] = round(float(np.corrcoef(x[m], g[fam][m])[0, 1]), 3)
                rows.append(rec)
    return pd.DataFrame(rows)


def lead_lag_summary(ll):
    """Best positive lead per family and indicator, and whether it holds in both halves."""
    out = []
    for (fam, ind), g in ll.groupby(["family", "indicator"]):
        best = g.loc[g.corr_all.idxmax()]
        e = g.loc[g.corr_early.idxmax()]
        l = g.loc[g.corr_late.idxmax()]
        stable = (abs(int(e.lag) - int(l.lag)) <= 2) and e.corr_early >= 0.5 and l.corr_late >= 0.5
        out.append({"family": fam, "indicator": ind, "best_lag": int(best.lag), "corr": best.corr_all,
                    "early_lag": int(e.lag), "early_corr": e.corr_early, "late_lag": int(l.lag), "late_corr": l.corr_late,
                    "stable": bool(stable)})
    s = pd.DataFrame(out)
    s["strength"] = pd.cut(s["corr"], [-1, 0.5, 0.75, 1], labels=["weak", "moderate", "strong"]).astype(str)
    return s.sort_values(["family", "corr"], ascending=[True, False])


def ev_shift(panel, master, macro):
    pt = panel.SKU_ID.map(master.set_index("SKU_ID").Powertrain_Application)
    year = panel.Date.str[:4]
    full_years = year.value_counts()[lambda s: s.index < "2026"].index    # 2026 has only 10 months
    t = panel.assign(Powertrain=pt, Year=year).groupby(["Year", "Powertrain"]).demand.sum().unstack()
    share = (t.div(t.sum(axis=1), axis=0) * 100).round(1)
    ev = macro.groupby(macro.Date.str[:4]).EV_Adoption_Index.mean().round(1)
    share["EV_Adoption_Index"] = ev.reindex(share.index)
    share["full_year"] = share.index.isin(full_years)
    return share.reset_index()
