"""Benchmark of widely used forecasting approaches on the replay test (4 origins x 19 months), scored on exactly the
rows the pipeline's model is scored on (outputs/backtest/model_long.parquet). Run from the repo root on a machine
with a GPU; dependencies are in experiments/requirements-experiments.txt (kept out of the pipeline's requirements).

  python experiments/zoo.py stats     # statsforecast: AutoETS, AutoTheta, AutoARIMA, Croston / ADIDA / IMAPA / TSB
  python experiments/zoo.py neural    # neuralforecast: N-HiTS, N-BEATS, TFT (global deep-learning models)
  python experiments/zoo.py chronos   # Chronos-Bolt and Chronos-2 (pretrained time-series models, zero-shot)
  python experiments/zoo.py timesfm   # TimesFM 2.5 (pretrained, zero-shot)
  python experiments/zoo.py gbdt      # CatBoost and XGBoost on the same feature table as LightGBM
  python experiments/zoo.py score     # one table for every method + combinations chosen on earlier origins only

Univariate methods see each series' rebuilt demand up to the origin (no peeking); the GBDTs use the same features
and training rows as the pipeline's LightGBM (experiments/sweep.py builds the table).
"""
import itertools
import json
import os
import sys
import time

import numpy as np
import pandas as pd

ORIGINS = ["2022-03", "2023-03", "2024-03", "2025-03"]
H = 19
OUT = "outputs/experiments/zoo/"
os.makedirs(OUT, exist_ok=True)


def keys():
    k = pd.read_parquet("outputs/backtest/model_long.parquet", columns=["origin", "SKU_ID", "Region", "h", "actual", "p50"])
    return k.rename(columns={"p50": "lightgbm"})


def history(origin):
    p = pd.read_parquet("outputs/demand/panel.parquet", columns=["SKU_ID", "Region", "Date", "demand"])
    p = p[p.Date <= origin].copy()
    p["unique_id"] = p.SKU_ID + "|" + p.Region
    p["ds"] = pd.to_datetime(p.Date + "-01")
    p["y"] = p.demand.astype(float).fillna(0.0).clip(lower=0)
    return p.sort_values(["unique_id", "ds"])[["unique_id", "ds", "y"]].reset_index(drop=True)


def to_long(fc, origin, cols):
    """statsforecast / neuralforecast style frame (unique_id, ds, model columns) -> origin, SKU_ID, Region, h, model."""
    fc = fc.reset_index() if "unique_id" not in fc.columns else fc
    o = pd.Period(origin, "M")
    out = pd.DataFrame({"origin": origin,
                        "SKU_ID": fc.unique_id.str.split("|").str[0], "Region": fc.unique_id.str.split("|").str[1],
                        "h": [(pd.Period(d, "M") - o).n for d in fc.ds]})
    for c in cols:
        out[c] = np.clip(fc[c].to_numpy(dtype=float), 0, None)
    return out[(out.h >= 1) & (out.h <= H)]


def save(name, frames):
    df = pd.concat(frames, ignore_index=True)
    df.to_parquet(OUT + f"{name}.parquet")
    print(f"saved {name}: {df.shape}", flush=True)


# ------------------------------------------------------------------------------------------------ statistical
def run_stats():
    from statsforecast import StatsForecast
    from statsforecast.models import (ADIDA, IMAPA, TSB, AutoARIMA, AutoETS, AutoTheta, CrostonOptimized, Naive,
                                      SeasonalNaive)
    names = ["ETS", "Theta", "ARIMA", "Croston", "ADIDA", "IMAPA", "TSB", "SNaive"]
    frames = []
    for o in ORIGINS:
        t = time.time()
        hist = history(o)
        hist = hist[hist.groupby("unique_id").y.transform("size") >= 3]
        models = [AutoETS(season_length=12, alias="ETS"), AutoTheta(season_length=12, alias="Theta"),
                  AutoARIMA(season_length=12, alias="ARIMA"), CrostonOptimized(alias="Croston"), ADIDA(alias="ADIDA"),
                  IMAPA(alias="IMAPA"), TSB(alpha_d=0.1, alpha_p=0.1, alias="TSB"), SeasonalNaive(season_length=12, alias="SNaive")]
        sf = StatsForecast(models=models, freq="MS", n_jobs=16, fallback_model=Naive())
        fc = sf.forecast(df=hist, h=H)
        frames.append(to_long(fc, o, names))
        print(o, "stats", round(time.time() - t), "s", flush=True)
    save("stats", frames)


# ------------------------------------------------------------------------------------------------ deep learning
def run_neural():
    import torch
    from neuralforecast import NeuralForecast
    from neuralforecast.losses.pytorch import MAE
    from neuralforecast.models import NBEATS, NHITS, TFT
    torch.set_float32_matmul_precision("high")
    frames = []
    for o in ORIGINS:
        t = time.time()
        hist = history(o)
        hist = hist[hist.groupby("unique_id").y.transform("size") >= 3]
        common = dict(h=H, input_size=36, loss=MAE(), scaler_type="robust", start_padding_enabled=True,
                      batch_size=256, random_seed=7, accelerator="gpu", devices=1, enable_progress_bar=False,
                      enable_model_summary=False, logger=False)
        models = [NHITS(max_steps=1500, alias="NHITS", **common), NBEATS(max_steps=1500, alias="NBEATS", **common),
                  TFT(max_steps=1000, hidden_size=64, alias="TFT", **common)]
        nf = NeuralForecast(models=models, freq="MS")
        nf.fit(df=hist)
        fc = nf.predict()
        frames.append(to_long(fc, o, ["NHITS", "NBEATS", "TFT"]))
        print(o, "neural", round(time.time() - t), "s", flush=True)
    save("neural", frames)


# ------------------------------------------------------------------------------------------------ pretrained models
def _contexts(o, length):
    hist = history(o)
    ids, ctx = [], []
    for uid, g in hist.groupby("unique_id", sort=False):
        ids.append(uid)
        ctx.append(g.y.to_numpy(dtype=np.float32)[-length:])
    return ids, ctx


def _frame(o, ids, arr, name):
    rows = pd.DataFrame(arr[:, :H], index=ids, columns=range(1, H + 1)).stack().rename(name).reset_index()
    rows.columns = ["uid", "h", name]
    return pd.DataFrame({"origin": o, "SKU_ID": rows.uid.str.split("|").str[0], "Region": rows.uid.str.split("|").str[1],
                         "h": rows.h.astype(int), name: np.clip(rows[name].to_numpy(), 0, None)})


def run_chronos():
    import torch
    from chronos import BaseChronosPipeline
    frames = []
    for model_id, name in [("amazon/chronos-bolt-base", "ChronosBolt"), ("amazon/chronos-2", "Chronos2")]:
        try:
            pipe = BaseChronosPipeline.from_pretrained(model_id, device_map="cuda", torch_dtype=torch.float32)
        except Exception as e:                                      # model not available in this version
            print("skip", model_id, repr(e)[:200], flush=True)
            continue
        part = []
        for o in ORIGINS:
            ids, ctx = _contexts(o, 512)
            med = []
            for i in range(0, len(ctx), 256):
                q, _ = pipe.predict_quantiles([torch.tensor(c) for c in ctx[i:i + 256]], prediction_length=H,
                                              quantile_levels=[0.5])
                if isinstance(q, (list, tuple)):                    # Chronos-2: one tensor per series
                    med.append(np.stack([t.float().cpu().numpy().reshape(-1, H)[0] for t in q]))
                else:
                    med.append(q[..., 0].float().cpu().numpy())
            part.append(_frame(o, ids, np.concatenate(med), name))
            print(o, name, flush=True)
        frames.append(pd.concat(part, ignore_index=True))
        del pipe
        torch.cuda.empty_cache()
    out = frames[0]
    for f in frames[1:]:
        out = out.merge(f, on=["origin", "SKU_ID", "Region", "h"], how="outer")
    out.to_parquet(OUT + "chronos.parquet")
    print("saved chronos", out.shape, flush=True)


def run_timesfm():
    import timesfm
    import torch
    torch.set_float32_matmul_precision("high")
    model = timesfm.TimesFM_2p5_200M_torch.from_pretrained("google/timesfm-2.5-200m-pytorch")
    model.compile(timesfm.ForecastConfig(max_context=512, max_horizon=32, normalize_inputs=True,
                                         use_continuous_quantile_head=True, force_flip_invariance=True,
                                         infer_is_positive=True, fix_quantile_crossing=True))
    frames = []
    for o in ORIGINS:
        ids, ctx = _contexts(o, 512)
        point, quant = model.forecast(horizon=H, inputs=[c.astype(np.float64) for c in ctx])
        med = quant[:, :, 5] if quant is not None and quant.shape[-1] >= 10 else point     # [mean, q10..q90]
        frames.append(_frame(o, ids, np.asarray(med), "TimesFM"))
        print(o, "timesfm", flush=True)
    save("timesfm", frames)


# ------------------------------------------------------------------------------------------------ other GBDTs
def run_gbdt():
    sys.path.insert(0, os.getcwd())
    from celeris.model import CATEGORICAL, feature_columns
    a = pd.read_parquet("outputs/experiments/sweep_feat.parquet")
    allowed = {str(pd.Period("2019-12", "M") + k) for k in range(0, 83, 2)} | set(ORIGINS)
    cols = feature_columns(a)
    for c in CATEGORICAL:
        a[c] = a[c].astype(str).astype("category")
    frames = []
    for o in ORIGINS:
        t = time.time()
        tr = a[a.origin.isin(allowed) & (a.origin < o) & (a.target <= o) & a.y.notna() & (a.weight > 0)]
        te = a[(a.origin == o) & a.y.notna() & (a.weight > 0)]
        y, w = (tr.y / tr.scale).to_numpy("float32"), (tr.scale * tr.weight).to_numpy("float32")
        res = te[["origin", "SKU_ID", "Region", "h"]].copy()
        import xgboost as xgb
        m = xgb.XGBRegressor(objective="reg:absoluteerror", tree_method="hist", device="cuda", n_estimators=1200,
                             learning_rate=0.05, max_depth=8, min_child_weight=50, subsample=0.8, colsample_bytree=0.8,
                             enable_categorical=True, max_cat_to_onehot=1, random_state=0)
        m.fit(tr[cols], y, sample_weight=w)
        res["XGBoost"] = np.clip(m.predict(te[cols]) * te.scale.to_numpy(), 0, None)
        from catboost import CatBoostRegressor, Pool
        trc, tec = tr[cols].copy(), te[cols].copy()
        for c in CATEGORICAL:
            trc[c], tec[c] = trc[c].astype(str), tec[c].astype(str)
        try:
            cb = CatBoostRegressor(loss_function="Quantile:alpha=0.5", iterations=3000, learning_rate=0.08, depth=8,
                                   task_type="GPU", devices="0", verbose=0, random_seed=0)
            cb.fit(Pool(trc, y, weight=w, cat_features=CATEGORICAL))
        except Exception as e:                                       # loss not available on GPU: CPU instead
            print("catboost on CPU:", repr(e)[:120], flush=True)
            cb = CatBoostRegressor(loss_function="MAE", iterations=1500, learning_rate=0.1, depth=8, thread_count=16,
                                   verbose=0, random_seed=0)
            cb.fit(Pool(trc, y, weight=w, cat_features=CATEGORICAL))
        res["CatBoost"] = np.clip(cb.predict(Pool(tec, cat_features=CATEGORICAL)) * te.scale.to_numpy(), 0, None)
        res["SKU_ID"], res["Region"] = res.SKU_ID.astype(str), res.Region.astype(str)
        frames.append(res)
        print(o, "gbdt", round(time.time() - t), "s", flush=True)
    save("gbdt", frames)


# ------------------------------------------------------------------------------------------------ scoring
def metrics(pred, y):
    p = np.round(pred)
    return {"wape": float(np.abs(p - y).sum() / y.sum()), "bias": float((p.sum() - y.sum()) / y.sum()),
            "rmse": float(np.sqrt(((p - y) ** 2).mean())), "mae": float(np.abs(p - y).mean())}


def score():
    k = keys()
    for f in ["stats", "neural", "chronos", "timesfm", "gbdt"]:
        if os.path.exists(OUT + f + ".parquet"):
            d = pd.read_parquet(OUT + f + ".parquet")
            d["origin"] = d.origin.astype(str)
            k = k.merge(d, on=["origin", "SKU_ID", "Region", "h"], how="left")
    methods = [c for c in k.columns if c not in {"origin", "SKU_ID", "Region", "h", "actual"}]
    missing = {m: int(k[m].isna().sum()) for m in methods}
    # rows a method cannot forecast (series with under 3 months of history) get the best simple baseline's value,
    # so no method is penalised for them
    best = json.load(open("outputs/reports/best_method.json"))["method"]
    bl = pd.read_parquet("outputs/backtest/baselines_long.parquet")
    bl = bl[bl.method == best][["origin", "SKU_ID", "Region", "h", "forecast"]].rename(columns={"forecast": "_base"})
    k = k.merge(bl, on=["origin", "SKU_ID", "Region", "h"], how="left")
    for m in methods:
        k[m] = k[m].fillna(k._base).fillna(0.0)
    k = k.drop(columns="_base")
    y = k.actual.to_numpy()
    org = k.origin.to_numpy()
    rows = []
    for m in methods:
        r = {"method": m, **metrics(k[m].to_numpy(), y), "missing_rows": missing[m]}
        r.update({f"wape_{o}": metrics(k[m].to_numpy()[org == o], y[org == o])["wape"] for o in ORIGINS})
        rows.append(r)
    table = pd.DataFrame(rows).sort_values("wape")
    # combinations with LightGBM: weight chosen on earlier origins only, scored on the next origin
    combos = []
    for m in [x for x in methods if x != "lightgbm"]:
        res = {"partner": m, "test": []}
        for i, o in enumerate(ORIGINS[1:], 1):
            tr, te = np.isin(org, ORIGINS[:i]), org == o
            best = min(np.arange(0, 1.01, 0.1), key=lambda a: metrics(
                (1 - a) * k.lightgbm.to_numpy()[tr] + a * k[m].to_numpy()[tr], y[tr])["wape"])
            blend = (1 - best) * k.lightgbm.to_numpy()[te] + best * k[m].to_numpy()[te]
            res["test"].append({"origin": o, "weight_on_partner": round(float(best), 1),
                                "wape_blend": metrics(blend, y[te])["wape"], "wape_lightgbm": metrics(k.lightgbm.to_numpy()[te], y[te])["wape"]})
        res["mean_gain"] = float(np.mean([t["wape_lightgbm"] - t["wape_blend"] for t in res["test"]]))
        combos.append(res)
    combos.sort(key=lambda r: -r["mean_gain"])
    json.dump({"table": table.round(4).to_dict("records"), "blends_with_lightgbm": combos}, open(OUT + "zoo_results.json", "w"), indent=1)
    family = {"lightgbm": "Chosen model (pipeline)", "ETS": "Statistical", "Theta": "Statistical", "ARIMA": "Statistical",
              "SNaive": "Statistical", "Croston": "Intermittent", "ADIDA": "Intermittent", "IMAPA": "Intermittent",
              "TSB": "Intermittent", "NHITS": "Deep learning", "NBEATS": "Deep learning", "TFT": "Deep learning",
              "ChronosBolt": "Pretrained", "Chronos2": "Pretrained", "TimesFM": "Pretrained", "XGBoost": "Gradient boosting",
              "CatBoost": "Gradient boosting"}
    names = {"lightgbm": "LightGBM global model", "ETS": "AutoETS", "Theta": "AutoTheta", "ARIMA": "AutoARIMA",
             "SNaive": "Seasonal naive", "NHITS": "N-HiTS", "NBEATS": "N-BEATS", "TFT": "TFT", "ChronosBolt": "Chronos-Bolt (zero-shot)",
             "Chronos2": "Chronos-2 (zero-shot)", "TimesFM": "TimesFM 2.5 (zero-shot)", "Croston": "Croston (optimised)"}
    out = table.assign(family=table.method.map(family), method=table.method.map(lambda m: names.get(m, m)))
    out = out[~out.method.isin(["Seasonal naive", "TSB"])]          # already in the pipeline's baselines
    out[["method", "family", "wape", "bias", "mae", "rmse"]].round(4).to_csv(OUT + "results_model_comparison.csv", index=False)
    print(table.round(4).to_string(index=False))
    for c in combos[:6]:
        print(c["partner"], round(c["mean_gain"], 4), [(t["origin"], t["weight_on_partner"], round(t["wape_blend"], 4)) for t in c["test"]])


if __name__ == "__main__":
    {"stats": run_stats, "neural": run_neural, "chronos": run_chronos, "timesfm": run_timesfm, "gbdt": run_gbdt,
     "score": score}[sys.argv[1]]()
