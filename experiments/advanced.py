"""Further model checks, all on the same backtest rows as the pipeline's model (outputs/backtest/model_long.parquet).
Run from the repo root on the GPU machine (experiments/requirements-experiments.txt + optuna).

  python experiments/advanced.py ensemble   # averages and stacked weights across model families (saved predictions)
  python experiments/advanced.py features   # new inputs (customer ordering calendar, working days) and a Tweedie loss
  python experiments/advanced.py tweedie    # Tweedie loss at several variance powers (pipeline inputs)
  python experiments/advanced.py blend      # current model combined with Tweedie, chosen on earlier origins only
  python experiments/advanced.py tune       # Optuna search for the Tweedie member, tuned on 2022-03 / 2023-03, tested on 2024-03 / 2025-03
  python experiments/advanced.py chronos2   # Chronos-2 with calendar and event covariates: zero-shot and fine-tuned (LoRA)

Results go to outputs/experiments/advanced/ and are summarised in experiments/results_advanced.json.
"""
import itertools
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.getcwd())
ORIGINS = ["2022-03", "2023-03", "2024-03", "2025-03"]
H = 19
OUT = "outputs/experiments/advanced/"
ZOO = "outputs/experiments/zoo/"
os.makedirs(OUT, exist_ok=True)
KEY = ["origin", "SKU_ID", "Region", "h"]


def wape(p, y):
    return float(np.abs(np.round(p) - y).sum() / y.sum())


def bias(p, y):
    return float((np.round(p).sum() - y.sum()) / y.sum())


def record(name, obj):
    path = "experiments/results_advanced.json"
    allres = json.load(open(path)) if os.path.exists(path) else {}
    allres[name] = obj
    json.dump(allres, open(path, "w"), indent=1, default=float)


# ------------------------------------------------------------------------------------------------ ensembles
def predictions():
    k = pd.read_parquet("outputs/backtest/model_long.parquet", columns=KEY + ["actual", "p50"]).rename(columns={"p50": "LightGBM"})
    for f, cols in [("gbdt", ["XGBoost", "CatBoost"]), ("neural", ["NHITS", "NBEATS"]), ("chronos", ["Chronos2"]),
                    ("timesfm", ["TimesFM"])]:
        d = pd.read_parquet(ZOO + f + ".parquet")
        d["origin"] = d.origin.astype(str)
        k = k.merge(d[KEY + cols], on=KEY, how="left")
    best = json.load(open("outputs/reports/best_method.json"))["method"]
    bl = pd.read_parquet("outputs/backtest/baselines_long.parquet")
    bl = bl[bl.method == best][KEY + ["forecast"]].rename(columns={"forecast": "_base"})
    k = k.merge(bl, on=KEY, how="left")
    for c in ["XGBoost", "CatBoost", "NHITS", "NBEATS", "Chronos2", "TimesFM"]:
        k[c] = k[c].fillna(k._base).fillna(0.0)
    return k.drop(columns="_base")


def simplex(m, step):
    """All weight vectors on the simplex with the given step."""
    n = int(round(1 / step))
    for c in itertools.product(range(n + 1), repeat=m):
        if sum(c) == n:
            yield np.array(c) / n


def run_ensemble():
    k = predictions()
    y, org = k.actual.to_numpy(), k.origin.to_numpy()
    sets = {"GBDT trio": ["LightGBM", "XGBoost", "CatBoost"],
            "GBDT trio + N-HiTS": ["LightGBM", "XGBoost", "CatBoost", "NHITS"],
            "LightGBM + deep + pretrained": ["LightGBM", "NHITS", "NBEATS", "Chronos2"],
            "All seven": ["LightGBM", "XGBoost", "CatBoost", "NHITS", "NBEATS", "Chronos2", "TimesFM"]}
    res = {"lightgbm_alone": {o: wape(k.LightGBM[org == o], y[org == o]) for o in ORIGINS}}
    for name, cols in sets.items():
        X = k[cols].to_numpy()
        eq = X.mean(axis=1)
        med = np.median(X, axis=1)
        r = {"equal_weights": {o: wape(eq[org == o], y[org == o]) for o in ORIGINS} | {"all": wape(eq, y)},
             "median": {o: wape(med[org == o], y[org == o]) for o in ORIGINS} | {"all": wape(med, y)}, "stacked": []}
        step = 0.1 if len(cols) <= 4 else 0.25
        for i, o in enumerate(ORIGINS[1:], 1):                 # weights fitted on earlier origins only
            tr, te = np.isin(org, ORIGINS[:i]), org == o
            best = min(simplex(len(cols), step), key=lambda w: wape(X[tr] @ w, y[tr]))
            r["stacked"].append({"test_origin": o, "weights": dict(zip(cols, best.round(2).tolist())),
                                 "wape": wape(X[te] @ best, y[te]), "lightgbm": res["lightgbm_alone"][o]})
        res[name] = r
        print(name, "equal", round(r["equal_weights"]["all"], 4), "| stacked",
              [(s["test_origin"], round(s["wape"], 4), round(s["lightgbm"], 4)) for s in r["stacked"]], flush=True)
    res["lightgbm_alone"]["all"] = wape(k.LightGBM, y)
    record("ensemble", res)


# ------------------------------------------------------------------------------------------------ features
NEW3 = ["customer_ly_order_share", "weekdays"]


def add_candidate_inputs(f, d, cut):
    """Two candidate inputs tested here (not in the pipeline, they did not help): the share of the same customer's
    series (same region) that ordered in last year's same month, and the number of weekdays in the target month."""
    from celeris.backtest import label
    hist = d.history(cut)
    exists = hist.notna().any(axis=1).to_numpy()
    y, cols = hist.to_numpy()[exists], list(hist.columns)
    groups = (d.meta.loc[hist.index[exists], "Customer_Name"] + "|" + hist.index[exists].get_level_values(1)).to_numpy()
    rows_g = (f.Customer_Name + "|" + f.Region).to_numpy()
    share = np.full(len(f), np.nan)
    for h in range(1, H + 1):
        src = cut + h - 12 * int(np.ceil(h / 12))
        sel = (f.h == h).to_numpy()
        if src in cols:
            v = y[:, cols.index(src)]
            s = pd.Series(np.where(np.isnan(v), np.nan, (v > 0).astype(float))).groupby(groups).mean()
            share[sel] = s.reindex(rows_g[sel]).to_numpy(float)
    f["customer_ly_order_share"] = share.astype("float32")
    wd = {}
    for t in f.target.unique():
        per = pd.Period(t, "M")
        days = pd.date_range(per.start_time, per.end_time.normalize(), freq="D")
        wd[t] = float((days.dayofweek < 5).sum())
    f["weekdays"] = f.target.map(wd).astype("float32")
    return f


def build_table():
    from celeris.backtest import Data, label, month
    from celeris.features import make_features
    from celeris.model import prepare
    p = pd.read_parquet("outputs/demand/panel.parquet")
    m = pd.read_parquet("outputs/clean/sku_master.parquet")
    mac = pd.read_parquet("outputs/clean/macro.parquet")
    ev = pd.read_parquet("outputs/clean/events.parquet")
    d = Data(p, m)
    origins = sorted({label(x) for x in range(month("2019-12"), month("2026-10") + 1, 2)} | set(ORIGINS))
    parts = []
    for o in origins:
        f = add_candidate_inputs(make_features(d, month(o), H, mac, ev, m), d, month(o))
        num = f.select_dtypes("float64").columns
        f[num] = f[num].astype("float32")
        parts.append(f)
    a = prepare(pd.concat(parts, ignore_index=True))
    a.to_parquet(OUT + "table.parquet")
    return a


def load_table():
    from celeris.model import CATEGORICAL
    a = pd.read_parquet(OUT + "table.parquet") if os.path.exists(OUT + "table.parquet") else build_table()
    for c in CATEGORICAL:
        a[c] = a[c].astype("category")
    return a


def backtest(a, cols, params, rounds, origins=ORIGINS):
    import lightgbm as lgb
    from celeris.model import CATEGORICAL
    out = []
    for o in origins:
        tr = a[(a.origin < o) & (a.target <= o) & a.y.notna() & (a.weight > 0)]
        te = a[(a.origin == o) & a.y.notna() & (a.weight > 0)]
        ds = lgb.Dataset(tr[cols], (tr.y / tr.scale).to_numpy("float32"), weight=(tr.scale * tr.weight).to_numpy("float32"),
                         categorical_feature=[c for c in CATEGORICAL if c in cols], free_raw_data=True)
        mdl = lgb.train(params, ds, rounds)
        out.append(te[KEY + ["y"]].assign(pred=np.clip(mdl.predict(te[cols]) * te.scale.to_numpy(), 0, None)))
    o = pd.concat(out, ignore_index=True)
    return o, {g: wape(x.pred, x.y) for g, x in o.groupby("origin")} | {"all": wape(o.pred, o.y), "bias": bias(o.pred, o.y)}


def base_params(threads=16):
    from celeris.model import PARAMS
    return {**PARAMS, "objective": "l1", "num_threads": threads}


def run_features():
    from celeris.model import feature_columns
    a = load_table()
    allc = feature_columns(a)
    old = [c for c in allc if c not in NEW3]
    res = {}
    for name, cols, params in [("without new inputs", old, base_params()), ("with new inputs", allc, base_params()),
                               ("with new inputs, Tweedie loss", allc, {**base_params(), "objective": "tweedie",
                                                                       "tweedie_variance_power": 1.3})]:
        t = time.time()
        _, r = backtest(a, cols, params, 600)
        res[name] = r
        print(name, {k: round(v, 4) for k, v in r.items()}, round(time.time() - t), "s", flush=True)
    record("features", res)


# ------------------------------------------------------------------------------------------------ Tweedie loss
def run_tweedie(powers=(1.1, 1.2, 1.3, 1.4, 1.5)):
    """Tweedie loss (the usual choice for sparse sales data, used by the M5 winners) with the pipeline's own inputs,
    for several variance powers. Predictions are saved for the combination check (blend)."""
    from celeris.model import feature_columns
    a = load_table()
    cols = [c for c in feature_columns(a) if c not in NEW3]
    res = {}
    for pw in powers:
        t = time.time()
        o, r = backtest(a, cols, {**base_params(), "objective": "tweedie", "tweedie_variance_power": pw}, 600)
        o.to_parquet(OUT + f"pred_tweedie_{pw}.parquet")
        res[f"power {pw}"] = r
        print(f"tweedie {pw}", {k: round(v, 4) for k, v in r.items()}, round(time.time() - t), "s", flush=True)
    record("tweedie", res)


def run_blend():
    """Current pipeline model (average of 3 L1 + 1 L2) combined with Tweedie models. The Tweedie power and the weight
    are chosen on earlier origins only and scored on the next one."""
    k = pd.read_parquet("outputs/backtest/model_long.parquet", columns=KEY + ["actual", "p50"])
    powers = sorted(float(f[len("pred_tweedie_"):-8]) for f in os.listdir(OUT) if f.startswith("pred_tweedie_"))
    for pw in powers:
        d = pd.read_parquet(OUT + f"pred_tweedie_{pw}.parquet")[KEY + ["pred"]].rename(columns={"pred": f"tw{pw}"})
        k = k.merge(d, on=KEY, how="left")
    y, org = k.actual.to_numpy(), k.origin.to_numpy()
    tw = [f"tw{pw}" for pw in powers]
    k["tw_avg"] = k[tw].mean(axis=1)
    res = {"current": {o: wape(k.p50[org == o], y[org == o]) for o in ORIGINS} | {"all": wape(k.p50, y)},
           "tweedie_average_of_powers": {o: wape(k.tw_avg[org == o], y[org == o]) for o in ORIGINS} | {"all": wape(k.tw_avg, y)},
           "honest": []}
    for i, o in enumerate(ORIGINS[1:], 1):
        tr, te = np.isin(org, ORIGINS[:i]), org == o
        best = min(((c, w) for c in tw + ["tw_avg"] for w in np.arange(0, 1.01, 0.1)),
                   key=lambda cw: wape((1 - cw[1]) * k.p50.to_numpy()[tr] + cw[1] * k[cw[0]].to_numpy()[tr], y[tr]))
        p = (1 - best[1]) * k.p50.to_numpy()[te] + best[1] * k[best[0]].to_numpy()[te]
        res["honest"].append({"test_origin": o, "tweedie": best[0], "weight_on_tweedie": round(float(best[1]), 1),
                              "wape": wape(p, y[te]), "bias": bias(p, y[te]), "current": wape(k.p50.to_numpy()[te], y[te])})
    for w in np.arange(0, 1.01, 0.1):
        p = (1 - w) * k.p50.to_numpy() + w * k.tw_avg.to_numpy()
        res[f"fixed weight {w:.1f}"] = {o: wape(p[org == o], y[org == o]) for o in ORIGINS} | {"all": wape(p, y), "bias": bias(p, y)}
    print(json.dumps({k_: v for k_, v in res.items() if not k_.startswith("fixed")}, indent=1), flush=True)
    print({k_: round(v["all"], 4) for k_, v in res.items() if k_.startswith("fixed")}, flush=True)
    record("blend_tweedie", res)


# ------------------------------------------------------------------------------------------------ tuning
def run_tune(trials=20):
    import optuna
    from celeris.model import feature_columns
    a = load_table()
    cols = [c for c in feature_columns(a) if c not in NEW3]     # the pipeline's inputs
    tune_o, test_o = ORIGINS[:2], ORIGINS[2:]

    tw = {"objective": "tweedie", "tweedie_variance_power": 1.2}       # the pipeline's main member

    def params_of(t):
        lr = t.suggest_float("learning_rate", 0.03, 0.1, log=True)
        p = {**base_params(8), **tw, "num_leaves": t.suggest_int("num_leaves", 31, 255, log=True),
             "min_data_in_leaf": t.suggest_int("min_data_in_leaf", 50, 1000, log=True),
             "feature_fraction": t.suggest_float("feature_fraction", 0.5, 1.0),
             "bagging_fraction": t.suggest_float("bagging_fraction", 0.5, 1.0),
             "lambda_l2": t.suggest_float("lambda_l2", 1e-3, 30, log=True), "learning_rate": lr}
        return p, int(round(30 / lr))

    def objective(t):
        p, rounds = params_of(t)
        _, r = backtest(a, cols, p, rounds, tune_o)
        return float(np.mean([r[o] for o in tune_o]))

    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=0))
    study.enqueue_trial({"learning_rate": 0.05, "num_leaves": 63, "min_data_in_leaf": 200, "feature_fraction": 0.8,
                         "bagging_fraction": 0.8, "lambda_l2": 1e-3})          # the current settings, as trial 0
    study.optimize(objective, n_trials=trials, n_jobs=2)
    best = study.best_trial
    p, rounds = params_of(optuna.trial.FixedTrial(best.params))
    _, tuned = backtest(a, cols, {**p, "num_threads": 16}, rounds, test_o)
    _, default = backtest(a, cols, {**base_params(), **tw}, 600, test_o)
    res = {"tuned_on": tune_o, "tested_on": test_o, "best_params": best.params, "rounds": rounds,
           "tune_score_default": study.trials[0].value, "tune_score_best": best.value,
           "test_tuned": tuned, "test_default": default}
    print(json.dumps(res, indent=1, default=float), flush=True)
    record("tune", res)


# ------------------------------------------------------------------------------------------------ Chronos-2
FT_MODE = os.environ.get("CH2_MODE", "lora")                       # "lora" needs the peft package
FT_LR = float(os.environ.get("CH2_LR", "1e-5"))                     # the library's recommendation for LoRA
FT_STEPS = int(os.environ.get("CH2_STEPS", "2000"))


def run_chronos2():
    import torch
    from chronos import BaseChronosPipeline
    from celeris.backtest import label, month
    from celeris.features import customer_events, holiday_share, price_rise_by_year
    ev = pd.read_parquet("outputs/clean/events.parquet")
    hol, cev, rises = holiday_share(ev), customer_events(ev), price_rise_by_year(ev)
    p = pd.read_parquet("outputs/demand/panel.parquet", columns=["SKU_ID", "Region", "Customer_Name", "Date", "demand"])
    cust = p.sort_values("Date").groupby(["SKU_ID", "Region"]).Customer_Name.last()

    def covs(months, region, customer):
        lab = [label(m) for m in months]
        moy = np.array([m % 12 for m in months], dtype=np.float32)
        shut = np.zeros(len(months), dtype=np.float32)
        for e in cev[(cev.Customer_Name == customer) & (cev.Region == region) & (cev.kind == "shutdown")].itertuples():
            shut += np.array([e.start <= l <= e.end for l in lab], dtype=np.float32)
        return {"month_sin": np.sin(2 * np.pi * moy / 12).astype(np.float32), "month_cos": np.cos(2 * np.pi * moy / 12).astype(np.float32),
                "holiday": np.array([hol.at[l, region] if l in hol.index else 0.0 for l in lab], dtype=np.float32),
                "shutdown": np.minimum(shut, 1),
                "price_rise": np.array([rises.get(int(l[:4]), 0.0) if l.endswith("-01") else 0.0 for l in lab], dtype=np.float32)}

    base = BaseChronosPipeline.from_pretrained("amazon/chronos-2", device_map="cuda")
    frames = []
    for o in ORIGINS:
        t = time.time()
        cut = month(o)
        hist = p[p.Date <= o]
        ids, pred_in, fit_in = [], [], []
        for (sku, reg), g in hist.groupby(["SKU_ID", "Region"], sort=False):
            g = g.sort_values("Date")
            y = g.demand.astype(float).fillna(0).clip(lower=0).to_numpy(np.float32)[-120:]
            if len(y) < 3:
                continue
            past_m = list(range(cut - len(y) + 1, cut + 1))
            fut_m = list(range(cut + 1, cut + H + 1))
            c_all = covs(past_m + fut_m, reg, cust.get((sku, reg), ""))
            past = {k: v[:len(y)] for k, v in c_all.items()}
            fut = {k: v[len(y):] for k, v in c_all.items()}
            ids.append(f"{sku}|{reg}")
            pred_in.append({"target": y, "past_covariates": past, "future_covariates": fut})
            fit_in.append({"target": y, "past_covariates": past, "future_covariates": {k: None for k in fut}})

        def median(pipe):
            out = []
            for i in range(0, len(pred_in), 256):
                q, _ = pipe.predict_quantiles(pred_in[i:i + 256], prediction_length=H, quantile_levels=[0.5], cross_learning=True)
                out.append(np.stack([t_.float().cpu().numpy().reshape(-1, H)[0] for t_ in q]))
            return np.concatenate(out)

        zero = median(base)
        tuned = base.fit(inputs=fit_in, prediction_length=H, finetune_mode=FT_MODE, learning_rate=FT_LR, num_steps=FT_STEPS,
                         batch_size=128, output_dir=OUT + f"chronos2_ft_{FT_MODE}_{o}", disable_data_parallel=True)
        ft = median(tuned)
        del tuned
        torch.cuda.empty_cache()
        rows = pd.DataFrame({"origin": o, "uid": np.repeat(ids, H), "h": np.tile(np.arange(1, H + 1), len(ids)),
                             "Chronos2_cov": np.clip(zero.ravel(), 0, None), "Chronos2_ft": np.clip(ft.ravel(), 0, None)})
        rows["SKU_ID"], rows["Region"] = rows.uid.str.split("|").str[0], rows.uid.str.split("|").str[1]
        frames.append(rows.drop(columns="uid"))
        print(o, "chronos2", round(time.time() - t), "s", flush=True)
    out = pd.concat(frames, ignore_index=True)
    out.to_parquet(OUT + f"chronos2_{FT_MODE}.parquet")
    k = pd.read_parquet("outputs/backtest/model_long.parquet", columns=KEY + ["actual", "p50"]).merge(out, on=KEY, how="left")
    best = json.load(open("outputs/reports/best_method.json"))["method"]
    bl = pd.read_parquet("outputs/backtest/baselines_long.parquet")
    k = k.merge(bl[bl.method == best][KEY + ["forecast"]].rename(columns={"forecast": "_base"}), on=KEY, how="left")
    for c in ["Chronos2_cov", "Chronos2_ft"]:
        k[c] = k[c].fillna(k._base).fillna(0.0)   # series too short to forecast take the best simple baseline
    org = k.origin.to_numpy()
    res = {c: {o: wape(k[c][org == o], k.actual[org == o]) for o in ORIGINS} | {"all": wape(k[c], k.actual), "bias": bias(k[c], k.actual)}
           for c in ["p50", "Chronos2_cov", "Chronos2_ft"]}
    print(json.dumps(res, indent=1), flush=True)
    record(f"chronos2_{FT_MODE}", res | {"settings": {"mode": FT_MODE, "learning_rate": FT_LR, "steps": FT_STEPS}})


if __name__ == "__main__":
    {"ensemble": run_ensemble, "features": run_features, "tweedie": run_tweedie, "blend": run_blend, "tune": run_tune,
     "chronos2": run_chronos2}[sys.argv[1]]()
