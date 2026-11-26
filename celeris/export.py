"""Exports everything the dashboard needs into dashboard/data/*.json.

The dashboard never contains numbers typed by hand: it only reads these files, which are rebuilt from the pipeline
outputs by `python run.py export`. Series are stored as compact arrays (one per series, months in order) so the
browser can filter by family / product / region / channel / customer and add up any selection instantly.
"""
import json
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .config import CLEAN, OUT, REPORTS, ROOT
from .backtest import Data
from .features import CLASSES, intermittency, price_rise_by_year
from .insights import findings

DASH = ROOT / "dashboard" / "data"
SOURCE_CODE = {"shipped": 0, "stockout_requested": 1, "shortage_estimated": 2, "catchup_removed": 3,
               "imputed_missing": 4, "imputed_erp_loss": 5, "imputed_unlisted_constraint": 6}


def _months(a, b):
    return [str(p) for p in pd.period_range(a, b, freq="M")]


def _clean(x):
    """JSON-safe: NaN -> None, numpy -> python."""
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if math.isnan(x) else float(x)
    return x


def _write(name, obj, sizes):
    text = json.dumps(_clean(obj), separators=(",", ":"), allow_nan=False)
    (DASH / name).write_text(text)
    sizes[name] = len(text)


def _arr(values, digits=0):
    out = []
    for v in values:
        if v is None or pd.isna(v):
            out.append(None)
        else:
            out.append(int(round(v)) if digits == 0 else round(float(v), digits))
    return out


def export_dashboard():
    DASH.mkdir(parents=True, exist_ok=True)
    sizes = {}
    panel = pd.read_parquet(OUT / "demand" / "panel.parquet")
    sales = pd.read_parquet(CLEAN / "sales_clean.parquet")
    master = pd.read_parquet(CLEAN / "sku_master.parquet").set_index("SKU_ID")
    table = pd.read_parquet(OUT / "forecast" / "forecast.parquet")
    scen = pd.read_parquet(OUT / "forecast" / "scenarios.parquet")
    model_long = pd.read_parquet(OUT / "backtest" / "model_long.parquet")
    base_long = pd.read_parquet(OUT / "backtest" / "baselines_long.parquet")
    best = json.loads((REPORTS / "best_method.json").read_text())
    checks = json.loads((REPORTS / "model_checks.json").read_text())

    hist_m = _months(panel.Date.min(), panel.Date.max())
    fc_m = _months("2026-11", "2028-05")
    hi = {m: i for i, m in enumerate(hist_m)}
    fi = {m: i for i, m in enumerate(fc_m)}

    # ---------------------------------------------------------------- series
    keys = sorted(set(zip(panel.SKU_ID, panel.Region)) | set(zip(table.SKU_ID, table.Region)))
    sidx = {k: i for i, k in enumerate(keys)}
    attrs = (pd.concat([panel[["SKU_ID", "Region", "Product_Family", "Sales_Channel", "Customer_Name", "Date"]],
                        table[["SKU_ID", "Region", "Product_Family", "Sales_Channel", "Customer_Name", "Date"]]])
             .sort_values("Date").groupby(["SKU_ID", "Region"]).last())
    cyc = panel.sort_values("Date").groupby(["SKU_ID", "Region"]).cycle.last()
    fams = sorted(master.Product_Family.unique())
    regions = sorted(set(panel.Region) | set(table.Region))
    channels = sorted(set(attrs.Sales_Channel))
    customers = sorted(set(attrs.Customer_Name))
    skus = sorted(master.index)

    rev = sales.groupby(["SKU_ID", "Region", "Date"]).Revenue_USD.sum().rename("revenue").reset_index()
    p = panel.merge(rev, on=["SKU_ID", "Region", "Date"], how="left").assign(src=panel.demand_source.map(SOURCE_CODE).to_numpy())
    S = {k: [] for k in ["sku", "fam", "reg", "ch", "cust", "cyc", "h0", "demand", "shipped", "revenue", "src",
                         "f0", "p10", "p50", "p90", "exp", "frev"]}
    hist_groups = dict(tuple(p.groupby(["SKU_ID", "Region"])))
    fc_groups = dict(tuple(table.groupby(["SKU_ID", "Region"])))
    for k in keys:
        a = attrs.loc[k]
        S["sku"].append(skus.index(k[0]))
        S["fam"].append(fams.index(a.Product_Family))
        S["reg"].append(regions.index(k[1]))
        S["ch"].append(channels.index(a.Sales_Channel))
        S["cust"].append(customers.index(a.Customer_Name))
        S["cyc"].append(int(cyc.get(k, 1)))
        g = hist_groups.get(k)
        if g is not None:
            g = g.sort_values("Date")
            S["h0"].append(hi[g.Date.iloc[0]])
            S["demand"].append(_arr(g.demand))
            S["shipped"].append(_arr(g.shipped))
            S["revenue"].append(_arr(g.revenue))
            S["src"].append(g.src.fillna(0).astype(int).tolist())
        else:
            for c in ["demand", "shipped", "revenue", "src"]:
                S[c].append([])
            S["h0"].append(None)
        f = fc_groups.get(k)
        if f is not None:
            f = f.sort_values("Date")
            S["f0"].append(fi[f.Date.iloc[0]])
            for c, col, d in [("p10", "p10", 1), ("p50", "p50", 1), ("p90", "p90", 1), ("exp", "expected", 1),
                              ("frev", "revenue_expected", 0)]:
                S[c].append(_arr(f[col], d))
        else:
            S["f0"].append(None)
            for c in ["p10", "p50", "p90", "exp", "frev"]:
                S[c].append([])

    # demand class (Syntetos-Boylan) from the last 24 months of rebuilt demand
    Y = Data(panel, master.reset_index()).Y
    adi, cv2, cls = intermittency(Y.to_numpy()[:, -24:])
    ci = {k: i for i, k in enumerate(Y.index)}
    S["adi"] = [round(float(adi[ci[k]]), 2) if k in ci else None for k in keys]
    S["cv2"] = [round(float(cv2[ci[k]]), 3) if k in ci else None for k in keys]
    S["cls"] = [int(cls[ci[k]]) if k in ci else None for k in keys]

    # what-if sliders: per series, scenario / base over the 19 months; per family and month, the same ratio, so the
    # dashboard can rebuild the monthly shape of any selection (several sliders: effects multiply, checked in run.py)
    grid = scen[scen.scenario.str.contains("|", regex=False)].copy()
    grid["Product_Family"] = grid.SKU_ID.map(master.Product_Family)
    wf = {"series": {}, "fam_month": {}}
    for key, g in grid.groupby("scenario"):
        r = g.groupby(["SKU_ID", "Region"])[["p50", "base_p50"]].sum()
        ratio = (r.p50 / r.base_p50.where(r.base_p50 > 0)).fillna(1.0)
        wf["series"][key] = [round(float(ratio.get(k, 1.0)), 4) for k in keys]
        fm = g.groupby(["Product_Family", "target"])[["p50", "base_p50"]].sum()
        fm = (fm.p50 / fm.base_p50).unstack().reindex(index=fams, columns=fc_m).fillna(1.0)
        ft = g.groupby("Product_Family")[["p50", "base_p50"]].sum()
        ft = (ft.p50 / ft.base_p50).reindex(fams).fillna(1.0)
        wf["fam_month"][key] = (fm.div(ft, axis=0)).round(4).to_numpy().tolist()
    S["whatif"] = wf

    # backtest: per series, per origin, 19 months of actual / model / best baseline (None where not scored)
    origins = best["origins"]
    bl = base_long[base_long.method == best["method"]]

    def grid(df, col):
        g = df.pivot_table(index=["SKU_ID", "Region", "origin"], columns="h", values=col, aggfunc="first")
        return g.reindex(columns=range(1, 20))

    act, mod, bas = grid(model_long, "actual"), grid(model_long, "forecast"), grid(bl, "forecast")
    bas = bas.reindex(act.index)
    bt = {"origins": origins, "act": [], "model": [], "base": [], "cls": []}
    cls_o = _classes_at(panel, master.reset_index(), origins)
    have = set(act.index.droplevel(2))
    for k in keys:
        bt["cls"].append([cls_o[o].get(k) for o in origins] if k in have else [])
        for name, g in (("act", act), ("model", mod), ("base", bas)):
            if k not in have:
                bt[name].append([])
                continue
            rows = []
            for o in origins:
                key = (k[0], k[1], o)
                rows.append(_arr(g.loc[key].to_numpy()) if key in g.index else [None] * 19)
            bt[name].append(rows)
    S["bt"] = bt

    sku_info = [{"id": s, "desc": master.at[s, "SKU_Description"], "family": master.at[s, "Product_Family"],
                 "powertrain": master.at[s, "Powertrain_Application"], "status": master.at[s, "Lifecycle_Status"],
                 "launch": master.at[s, "Launch_Date"], "eol": master.at[s, "EOL_Date"],
                 "replaces": master.at[s, "Replaces_SKU"], "size": master.at[s, "Size_Class"]} for s in skus]
    _write("series.json", {"months": hist_m, "fc_months": fc_m, "families": fams, "regions": regions,
                           "channels": channels, "customers": customers, "skus": sku_info,
                           "source_codes": SOURCE_CODE, "classes": CLASSES, **S}, sizes)

    # ---------------------------------------------------------------- anomalies
    reg = pd.read_parquet(REPORTS / "anomaly_register.parquet")
    reg = reg[np.array([(s, r) in sidx for s, r in zip(reg.SKU_ID, reg.Region)]) & reg.Date.isin(list(hi)).to_numpy()]
    types = sorted(reg.type.unique())
    kinds = ["data_error", "data_loss", "real_event", "master_data"]
    actions = sorted(reg.action.unique())
    _write("anomalies.json", {
        "types": types, "kinds": kinds, "actions": actions,
        "type_kind": {t: reg[reg.type == t].kind.iloc[0] for t in types},
        "rows": [[sidx[(r.SKU_ID, r.Region)], hi[r.Date], types.index(r.type), _num(r.shipped), _num(r.demand),
                  _num(r.expected), r.reason, actions.index(r.action)] for r in reg.itertuples()],
    }, sizes)

    # ---------------------------------------------------------------- stockouts
    st = pd.read_parquet(CLEAN / "stockouts.parquet")
    st = st[[(s, r) in sidx for s, r in zip(st.SKU_ID, st.Region)]]
    reasons = sorted(st.Reason.unique())
    _write("stockouts.json", {"reasons": reasons, "rows": [
        [sidx[(r.SKU_ID, r.Region)], hi[r.Date], int(r.Requested_Units), int(r.Shipped_Units), int(r.Backordered_Units),
         int(r.Lost_Units), reasons.index(r.Reason), int(r.Stockout_Days)] for r in st.itertuples()]}, sizes)

    # ---------------------------------------------------------------- reports
    quality = json.loads((REPORTS / "data_quality.json").read_text())
    raw = pd.read_csv(ROOT / "data" / "student_data" / "train.csv", dtype=str, keep_default_na=False)
    before_after = [{"what": w, "before": int(b), "after": int(a)} for w, b, a in [
        ("Rows", len(raw), len(sales)), ("Month spellings", raw.Date.nunique(), sales.Date.nunique()),
        ("SKU spellings", raw.SKU_ID.nunique(), sales.SKU_ID.nunique()),
        ("Region names", raw.Region.nunique(), sales.Region.nunique()),
        ("Channel names", raw.Sales_Channel.nunique(), sales.Sales_Channel.nunique()),
        ("Customer names", raw.Customer_Name.nunique(), sales.Customer_Name.nunique())]]
    st_all = pd.read_parquet(CLEAN / "stockouts.parquet").merge(sales, on=["Date", "SKU_ID", "Region"])
    indep = st_all[st_all.units_status != "fixed_per10_exact"]
    answer_key = {"rows": int(len(indep)), "match": int((indep.Shipped_Units == indep.Units_Sold).sum())}
    _write("quality.json", {"summary": quality["summary"], "issues": quality["issues"], "before_after": before_after,
                            "answer_key": answer_key,
                            "demand_checks": json.loads((REPORTS / "demand_checks.json").read_text())}, sizes)

    macro = pd.read_parquet(CLEAN / "macro.parquet")
    _write("drivers.json", {
        "summary": pd.read_csv(REPORTS / "macro_leadlag_summary.csv").to_dict("records"),
        "leadlag": pd.read_csv(REPORTS / "macro_leadlag.csv").to_dict("records"),
        "ev": pd.read_csv(REPORTS / "ev_shift.csv").to_dict("records"),
        "macro": macro.drop(columns=["Year", "Month"]).to_dict("list"),
        "events": pd.read_csv(REPORTS / "event_effects.csv").to_dict("records"),
        "calendar": pd.read_parquet(CLEAN / "events.parquet")[["Event_ID", "Start_Month", "End_Month", "Scope_Type",
                                                                "Scope_Value", "Event_Type", "Description"]].to_dict("records"),
    }, sizes)

    class_acc = _class_accuracy(model_long, bl, panel, master.reset_index())
    _write("meta.json", {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "model": checks, "best_baseline": best,
        "choice": json.loads((REPORTS / "submission_choice.json").read_text()),
        "methods": pd.read_csv(REPORTS / "backtest_all_methods.csv").to_dict("records"),
        # wider comparison (statistical, deep-learning, pretrained, other GBDT) from experiments/zoo.py, same rows
        "comparison": (pd.read_csv(ROOT / "experiments" / "results_model_comparison.csv").to_dict("records")
                       if (ROOT / "experiments" / "results_model_comparison.csv").exists() else []),
        "methods_by_origin": pd.read_csv(REPORTS / "backtest_all_methods_by_origin.csv").to_dict("records"),
        "importance": pd.read_csv(REPORTS / "model_feature_importance.csv", index_col=0).gain_share.head(15).to_dict(),
        "scenarios": json.loads((REPORTS / "scenarios.json").read_text()),
        "price_rises": price_rise_by_year(pd.read_parquet(CLEAN / "events.parquet")),
        "accuracy_by_level": pd.read_csv(REPORTS / "accuracy_by_level.csv").to_dict("records"),
        "blend_test": json.loads((REPORTS / "blend_test.json").read_text()),
        "accounts": {"by_customer": pd.read_csv(REPORTS / "account_changes_by_customer.csv").to_dict("records"),
                     "changes": pd.read_csv(REPORTS / "account_changes.csv").to_dict("records"),
                     "facts": json.loads((REPORTS / "account_facts.json").read_text())},
        "author": "Dhruv Tibarewal",
        "class_accuracy": class_acc,
        "findings": findings(panel, table, pd.read_parquet(CLEAN / "stockouts.parquet"),
                             pd.read_parquet(CLEAN / "list_prices.parquet"), master.reset_index(),
                             pd.read_csv(REPORTS / "event_effects.csv"),
                             pd.read_csv(REPORTS / "macro_leadlag_summary.csv"), pd.read_csv(REPORTS / "ev_shift.csv"),
                             pd.read_csv(REPORTS / "accuracy_by_level.csv"),
                             pd.read_csv(REPORTS / "account_changes_by_customer.csv"), class_acc),
        "story": story_events(pd.read_csv(REPORTS / "event_effects.csv"), pd.read_parquet(CLEAN / "events.parquet")),
    }, sizes)
    return sizes


STORY = [("COVID", "COVID lockdowns"), ("semiconductor", "Chip shortage"), ("opens Middle East", "New region opens"),
("Customers order early", "Early buying before 9% rise"),
         ("ERP migration", "ERP migration"), ("strike", "Nexon strike"), ("second Indian", "Helios adds a line"),
         ("retooling shutdown at Orion", "Orion retooling"), ("list-price increase", "Price rise")]


def story_events(effects, calendar):
    """Events worth marking on the main timeline: those with a measured effect outside the chance range (not the
    yearly holidays), plus customer events and price rises inside the forecast window. COVID entries are merged."""
    out = []
    keep = effects[(effects.Event_Type != "Holiday") & ~effects.verdict.str.startswith(("No clear", "Not applicable"))]
    covid = keep[keep.Description.str.contains("COVID")]
    if len(covid):
        out.append({"start": covid.Start.min(), "end": covid.End.max(), "label": "COVID lockdowns", "past": True, "scope": "Global: All",
                    "text": "Lockdowns and plant closures. Demand change: " + ", ".join(
                        f"{r.Scope.split(': ')[1]} {r.effect_demand_pct:+.0f}%" for r in covid.itertuples()) + "."})
    for r in keep[~keep.Description.str.contains("COVID") & ~keep.Description.str.contains("list-price")].itertuples():
        label = next((l for k, l in STORY if k in r.Description), None)
        if label is None or r.Description.startswith("Orders dip"):
            continue
        eff = (r.verdict.split(": ", 1)[1].capitalize() if r.verdict.startswith("Structural") else
               f"Shipments {r.effect_shipped_pct:+.0f}%, demand {r.effect_demand_pct:+.0f}%"
               if abs(r.effect_shipped_pct - r.effect_demand_pct) > 5 else f"Demand {r.effect_demand_pct:+.0f}%")
        out.append({"start": r.Start, "end": r.End if isinstance(r.End, str) else r.Start, "label": label, "past": True, "scope": r.Scope,
                    "text": f"{r.Description}. {eff} ({r.Scope})."})
    fut = calendar[(calendar.Start_Month >= "2026-11") & calendar.Event_Type.isin(["Customer Event", "Price Change"])]
    for r in fut.itertuples():
        label = next((l for k, l in STORY if k in r.Description), r.Event_Type)
        out.append({"start": r.Start_Month, "end": r.End_Month if isinstance(r.End_Month, str) and r.End_Month else r.Start_Month,
                    "label": label, "past": False, "scope": f"{r.Scope_Type}: {r.Scope_Value}",
                    "text": f"{r.Description} ({r.Scope_Value}). In the forecast."})
    return sorted(out, key=lambda e: e["start"])


def _classes_at(panel, master, origins):
    """Demand class of every series at each origin, from the 24 months before it: {origin: {(sku, region): code}}."""
    d = Data(panel, master)
    out = {}
    for o in origins:
        y = d.history(pd.Period(o, "M").ordinal)
        _, _, c = intermittency(y.to_numpy()[:, -24:])
        out[o] = dict(zip(y.index, c.astype(int).tolist()))
    return out


def _class_accuracy(model_long, base_long, panel, master):
    """Replay-test error by demand class (class measured at each origin from the 24 months before it)."""
    d = Data(panel, master)
    rows = []
    for o in model_long.origin.unique():
        y = d.history(pd.Period(o, "M").ordinal)
        _, _, c = intermittency(y.to_numpy()[:, -24:])
        rows.append(pd.DataFrame({"origin": o, "SKU_ID": y.index.get_level_values(0), "Region": y.index.get_level_values(1),
                                  "cls": c.astype(int)}))
    cls = pd.concat(rows)
    b = base_long[["origin", "SKU_ID", "Region", "h", "forecast"]].rename(columns={"forecast": "base"})
    x = model_long.merge(b, on=["origin", "SKU_ID", "Region", "h"]).merge(cls, on=["origin", "SKU_ID", "Region"])
    out = []
    for k, g in x.groupby("cls"):
        out.append({"class": CLASSES[k], "rows": int(len(g)), "share_of_demand": round(float(g.actual.sum() / x.actual.sum()), 4),
                    "wape_model": round(float((g.forecast - g.actual).abs().sum() / g.actual.sum()), 4),
                    "wape_best_simple": round(float((g.base - g.actual).abs().sum() / g.actual.sum()), 4),
                    "bias_model": round(float((g.forecast.sum() - g.actual.sum()) / g.actual.sum()), 4)})
    return out


def _num(v):
    return None if v is None or pd.isna(v) else round(float(v), 1)
