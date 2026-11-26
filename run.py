"""One command for the whole pipeline:  .venv/bin/python run.py
Run only some steps (in this order):  .venv/bin/python run.py submit export

  clean           raw CSVs -> outputs/clean/ + data-quality report
  demand          monthly panel with rebuilt (unconstrained) demand + anomaly register + event impacts
  drivers         which economic indicator leads which family (and is it stable?), EV shift
  backtest        replay 4 past origins x 19 months for every baseline; pick the best
  features        feature table for the real forecast origin (Oct 2026)
  model_backtest  global LightGBM replay test with P10/P50/P90 + ranges for totals   (10-25 min)
  model_final     train on all history, forecast Nov 2026 - May 2028, save the models  (5-10 min)
  scenarios       what-if: the same models with a weaker economy
  insights        accuracy at planning levels, blend test, account changes (from existing outputs)
  submit          submission.csv from whichever scored better in the replay test, plus the forecast table
  export          data files for the dashboard (dashboard/data/)
"""
import sys
import json
import time

import pandas as pd

from celeris.anomalies import build_register, event_effects
from celeris.clean_reference import check_test, clean_events, clean_macro, clean_master, clean_stockouts
from celeris.clean_sales import clean_train
from celeris.config import CLEAN, OUT, REPORTS
from celeris.demand import build_panel, validate_imputation, validate_shortage_method
from celeris.io import load_all, read_raw
from celeris.quality import QualityLog
from celeris import submission
from celeris.backtest import ORIGINS, Data, metrics, month, run, timing_oracle
from celeris.baselines import BASELINES
from celeris.drivers import ev_shift, lead_lag, lead_lag_summary
from celeris.features import make_features, price_rise_by_year
from celeris.model import backtest_long, feature_store, forecast_with_models, importance, training_origins
from celeris.ranges import calibrate
from celeris import scenarios as whatif
from celeris import insights

DEMAND = OUT / "demand"
SUBMIT = OUT / "submission"
BACKTEST = OUT / "backtest"
FEATURES = OUT / "features"
FORECAST = OUT / "forecast"


def step_clean():
    t = time.time()
    raw = load_all()
    log = QualityLog()

    master = clean_master(raw["sku_master"], log)
    stockouts = clean_stockouts(raw["inventory_stockouts"], log)
    macro = clean_macro(raw["macro_indicators"], log)
    events = clean_events(raw["events_calendar"], log)
    test = check_test(raw["test"], master, log)
    customers = sorted(test.Customer_Name.unique())
    sales, prices = clean_train(raw["train"], master, customers, stockouts, log)

    CLEAN.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    sales.to_parquet(CLEAN / "sales_clean.parquet", index=False)
    sales.drop(columns=["fix_flags"]).to_csv(CLEAN / "sales_clean.csv", index=False)
    prices.columns = prices.columns.astype(str)
    prices.reset_index().to_parquet(CLEAN / "list_prices.parquet", index=False)
    master.to_parquet(CLEAN / "sku_master.parquet", index=False)
    stockouts.to_parquet(CLEAN / "stockouts.parquet", index=False)
    macro.to_parquet(CLEAN / "macro.parquet", index=False)
    events.to_parquet(CLEAN / "events.parquet", index=False)
    test.to_parquet(CLEAN / "test.parquet", index=False)
    log.write(REPORTS / "data_quality.json")
    log.table().to_csv(REPORTS / "data_quality.csv", index=False)
    print(f"[1 clean]  {time.time() - t:.1f}s  {len(raw['train']):,} raw rows -> {len(sales):,} clean rows, "
          f"{len(log.issues)} issue types logged")


def step_demand():
    t = time.time()
    sales = pd.read_parquet(CLEAN / "sales_clean.parquet")
    master = pd.read_parquet(CLEAN / "sku_master.parquet")
    stockouts = pd.read_parquet(CLEAN / "stockouts.parquet")
    events = pd.read_parquet(CLEAN / "events.parquet")

    panel, ratios = build_panel(sales, master, stockouts)
    register = build_register(panel, sales, master)
    effects = event_effects(panel, events)
    checks = {
        "panel_rows": int(len(panel)),
        "shipped_total": int(panel.shipped.sum()),
        "demand_total": int(panel.demand.sum()),
        "demand_sources": panel.demand_source.value_counts().to_dict(),
        "shortage_ratio_by_month": ratios.to_dict(),
        "shortage_method_test": validate_shortage_method(panel),
        "imputation_test": validate_imputation(panel),
    }

    DEMAND.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(DEMAND / "panel.parquet", index=False)
    register.to_csv(REPORTS / "anomaly_register.csv", index=False)
    register.to_parquet(REPORTS / "anomaly_register.parquet", index=False)
    effects.to_csv(REPORTS / "event_effects.csv", index=False)
    (REPORTS / "demand_checks.json").write_text(json.dumps(checks, indent=2, default=str))
    print(f"[2 demand] {time.time() - t:.1f}s  panel {len(panel):,} rows, shipped {checks['shipped_total']:,} -> "
          f"demand {checks['demand_total']:,}; {len(register):,} anomalies; {len(effects)} events measured")


def step_drivers():
    t = time.time()
    panel = pd.read_parquet(DEMAND / "panel.parquet")
    macro = pd.read_parquet(CLEAN / "macro.parquet")
    master = pd.read_parquet(CLEAN / "sku_master.parquet")
    ll = lead_lag(panel, macro)
    summary = lead_lag_summary(ll)
    ll.to_csv(REPORTS / "macro_leadlag.csv", index=False)
    summary.to_csv(REPORTS / "macro_leadlag_summary.csv", index=False)
    ev_shift(panel, master, macro).to_csv(REPORTS / "ev_shift.csv", index=False)
    print(f"[3 drivers] {time.time() - t:.1f}s  {int(summary.stable.sum())} stable indicator-family links of "
          f"{len(summary)}")


def step_backtest():
    t = time.time()
    panel = pd.read_parquet(DEMAND / "panel.parquet")
    master = pd.read_parquet(CLEAN / "sku_master.parquet")
    data = Data(panel, master)
    long = pd.concat([run(f, data, name=n) for n, f in BASELINES.items()], ignore_index=True)
    rhythm = panel.groupby(["SKU_ID", "Region"]).cycle.last()
    long["rhythm"] = [rhythm.get(k, 1) for k in zip(long.SKU_ID, long.Region)]

    overall = metrics(long).sort_values("wape")
    per_origin = metrics(long, "origin")
    breakdown = pd.concat([metrics(long, by).rename(columns={by: "group"}).assign(dimension=by)
                           for by in ["Product_Family", "Region", "Sales_Channel", "h", "rhythm", "origin"]])
    BACKTEST.mkdir(parents=True, exist_ok=True)
    long.to_parquet(BACKTEST / "baselines_long.parquet", index=False)
    overall.to_csv(REPORTS / "backtest_baselines.csv", index=False)
    per_origin.to_csv(REPORTS / "backtest_baselines_by_origin.csv", index=False)
    breakdown.to_csv(REPORTS / "backtest_baselines_breakdown.csv", index=False)
    best = overall.iloc[0]
    (REPORTS / "best_method.json").write_text(json.dumps({"method": best.method, "wape": best.wape, "bias": best.bias,
                                                         "origins": ORIGINS, "timing_oracle_wape": timing_oracle(data)},
                                                        indent=2))
    print(f"[4 backtest] {time.time() - t:.1f}s  {len(BASELINES)} baselines x {len(ORIGINS)} origins; best: "
          f"{best.method} (WAPE {best.wape:.3f}, bias {best.bias:+.3f})")


def step_features():
    t = time.time()
    panel = pd.read_parquet(DEMAND / "panel.parquet")
    master = pd.read_parquet(CLEAN / "sku_master.parquet")
    macro = pd.read_parquet(CLEAN / "macro.parquet")
    events = pd.read_parquet(CLEAN / "events.parquet")
    data = Data(panel, master)
    feats = make_features(data, month(panel.Date.max()), 19, macro, events, master, with_target=False)
    FEATURES.mkdir(parents=True, exist_ok=True)
    feats.to_parquet(FEATURES / "forecast_origin_features.parquet", index=False)
    print(f"[5 features] {time.time() - t:.1f}s  {len(feats):,} rows x {feats.shape[1]} columns for the real forecast")


def _store():
    panel = pd.read_parquet(DEMAND / "panel.parquet")
    master = pd.read_parquet(CLEAN / "sku_master.parquet")
    macro = pd.read_parquet(CLEAN / "macro.parquet")
    events = pd.read_parquet(CLEAN / "events.parquet")
    last = panel.Date.max()
    return feature_store(Data(panel, master), macro, events, master, training_origins(last=last, extra=ORIGINS)), last


def step_model_backtest():
    t = time.time()
    store, _ = _store()
    long = backtest_long(store, ORIGINS)
    del store
    long.to_parquet(BACKTEST / "model_long.parquet", index=False)
    base = pd.read_parquet(BACKTEST / "baselines_long.parquet")
    both = pd.concat([base, long[base.columns.intersection(long.columns)]], ignore_index=True)
    metrics(both).sort_values("wape").to_csv(REPORTS / "backtest_all_methods.csv", index=False)
    metrics(both, "origin").to_csv(REPORTS / "backtest_all_methods_by_origin.csv", index=False)
    pd.concat([metrics(long, by).rename(columns={by: "group"}).assign(dimension=by)
               for by in ["Product_Family", "Region", "Sales_Channel", "h", "origin"]]).to_csv(
        REPORTS / "backtest_model_breakdown.csv", index=False)
    m = metrics(long).iloc[0]
    factor = round(float(long.actual.sum() / long.p50.sum()), 4)
    checks = {
        "wape": float(m.wape), "bias": float(m.bias), "bias_factor": factor,
        "below_p10": round(float((long.actual < long.p10.round()).mean()), 4),
        "above_p90": round(float((long.actual > long.p90.round()).mean()), 4),
        "rows": int(len(long)),
        "total_ranges": calibrate(long, factor),
    }
    (REPORTS / "model_checks.json").write_text(json.dumps(checks, indent=2))
    print(f"[model_backtest] {time.time() - t:.0f}s  WAPE {checks['wape']:.3f}, bias {checks['bias']:+.3f}; "
          f"below P10 {checks['below_p10']:.1%}, above P90 {checks['above_p90']:.1%}; "
          f"totals rho {checks['total_ranges']['rho']}")


def step_model_final():
    t = time.time()
    store, last = _store()
    rows, models = forecast_with_models(store, last)
    del store
    FORECAST.mkdir(parents=True, exist_ok=True)
    rows.drop(columns=[c for c in ["y", "weight"] if c in rows]).to_parquet(FORECAST / "model_rows.parquet", index=False)
    for name, booster in models.items():
        booster.save_model(str(FORECAST / f"model_{name}.txt"))
    for old in FORECAST.glob("model_p50*.txt"):
        if old.stem not in {f"model_{k}" for k in models}:
            old.unlink()
    importance(models).rename("gain_share").to_csv(REPORTS / "model_feature_importance.csv")
    print(f"[model_final] {time.time() - t:.0f}s  forecast rows {len(rows):,}; models saved")


def step_scenarios():
    t = time.time()
    rows = pd.read_parquet(FORECAST / "model_rows.parquet")
    macro = pd.read_parquet(CLEAN / "macro.parquet")
    out, check = whatif.run_all(rows, macro, FORECAST)
    out.to_parquet(FORECAST / "scenarios.parquet", index=False)
    change = out.groupby("scenario").apply(lambda g: round(float(g.p50.sum() / g.base_p50.sum() - 1), 4)).to_dict()
    summary = {k: change[k] for k in whatif.SCENARIOS}
    grid = {k: v for k, v in change.items() if "|" in k}
    (REPORTS / "scenarios.json").write_text(json.dumps({"total_change": summary, "definitions": whatif.SCENARIOS,
                                                        "grid": whatif.GRID, "grid_change": grid,
                                                        "combination_check": check}, indent=2))
    print(f"[scenarios] {time.time() - t:.0f}s  " + ", ".join(f"{k}: {v:+.1%}" for k, v in summary.items()) +
          f"; all three together {check['together_change']:+.1%} vs multiplied {check['multiplied_change']:+.1%}")


def step_insights():
    t = time.time()
    model_long = pd.read_parquet(BACKTEST / "model_long.parquet")
    base_long = pd.read_parquet(BACKTEST / "baselines_long.parquet")
    best = json.loads((REPORTS / "best_method.json").read_text())
    factor = json.loads((REPORTS / "model_checks.json").read_text())["bias_factor"]
    levels = insights.accuracy_by_level(model_long, base_long, best["method"], factor)
    levels.to_csv(REPORTS / "accuracy_by_level.csv", index=False)
    blend = insights.blend_test(model_long, base_long, best["method"])
    (REPORTS / "blend_test.json").write_text(json.dumps(blend, indent=2))
    detail, summary, facts = insights.account_changes(pd.read_parquet(REPORTS / "anomaly_register.parquet"),
                                                      pd.read_parquet(DEMAND / "panel.parquet"))
    detail.to_csv(REPORTS / "account_changes.csv", index=False)
    summary.to_csv(REPORTS / "account_changes_by_customer.csv", index=False)
    (REPORTS / "account_facts.json").write_text(json.dumps(facts, indent=2))
    fam = levels.set_index("level").loc["Family, per month"]
    print(f"[insights] {time.time() - t:.1f}s  family-month error {fam.wape_model_expected:.1%} (simple {fam.wape_best_simple:.1%}); "
          f"{blend['verdict']}; {facts['switches']} account changes")


def step_submit():
    t = time.time()
    panel = pd.read_parquet(DEMAND / "panel.parquet")
    master = pd.read_parquet(CLEAN / "sku_master.parquet")
    events = pd.read_parquet(CLEAN / "events.parquet")
    prices = pd.read_parquet(CLEAN / "list_prices.parquet")
    test, sample = read_raw("test.csv"), read_raw("sample_submission.csv")
    best = json.loads((REPORTS / "best_method.json").read_text())
    model = json.loads((REPORTS / "model_checks.json").read_text())

    SUBMIT.mkdir(parents=True, exist_ok=True)
    FORECAST.mkdir(parents=True, exist_ok=True)
    v0 = submission.forecast_v0(panel, test, master)
    submission.validate(v0, test, sample, master)
    v0.to_csv(SUBMIT / "submission_v0_seasonal_naive.csv", index=False)
    v1 = submission.forecast(BASELINES[best["method"]], panel, test, master)
    submission.validate(v1, test, sample, master)
    v1.to_csv(SUBMIT / "submission_v1_best_baseline.csv", index=False)

    rows = pd.read_parquet(FORECAST / "model_rows.parquet")
    v2, table = submission.forecast_table(rows, panel, test, master, prices, price_rise_by_year(events),
                                          bias_factor=model["bias_factor"])
    info2 = submission.validate(v2, test, sample, master)
    v2.to_csv(SUBMIT / "submission_v2_model.csv", index=False)
    table.to_parquet(FORECAST / "forecast.parquet", index=False)
    table.to_csv(FORECAST / "forecast.csv", index=False)

    use_model = model["wape"] < best["wape"]
    chosen = v2 if use_model else v1
    chosen.to_csv(SUBMIT / "submission.csv", index=False)
    name = "LightGBM global model" if use_model else best["method"]
    wape = model["wape"] if use_model else best["wape"]
    (REPORTS / "submission_choice.json").write_text(json.dumps({"method": name, "backtest_wape": wape,
                                                               "model_wape": model["wape"], "baseline_wape": best["wape"]}, indent=2))
    print(f"[7 submit] {time.time() - t:.1f}s  submission.csv valid: {info2['rows']:,} rows; using {name} "
          f"(backtest WAPE {wape:.3f}); forecast table with P10/P50/P90 + revenue written")


def step_export():
    from celeris.export import export_dashboard
    t = time.time()
    sizes = export_dashboard()
    print(f"[export] {time.time() - t:.1f}s  dashboard/data: " + ", ".join(f"{k} {v / 1e6:.1f} MB" for k, v in sizes.items()))


STEPS = {"clean": step_clean, "demand": step_demand, "drivers": step_drivers, "backtest": step_backtest,
         "features": step_features, "model_backtest": step_model_backtest, "model_final": step_model_final,
         "scenarios": step_scenarios, "insights": step_insights, "submit": step_submit, "export": step_export}


if __name__ == "__main__":
    wanted = sys.argv[1:] or list(STEPS)
    unknown = [w for w in wanted if w not in STEPS]
    if unknown:
        sys.exit(f"unknown step(s) {unknown}; choose from {list(STEPS)}")
    for name in STEPS:
        if name in wanted:
            STEPS[name]()
