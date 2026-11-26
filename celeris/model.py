"""One global LightGBM model for all series (the approach that won the M5 forecasting competition), as a weighted
average of five differently set-up models (MEMBERS) for the typical forecast, plus two quantile models for P10/P90.

Each training row is (series, origin, months ahead) from `features.make_features`. The model predicts demand as a
multiple of the series' recent level ("scale" = last-12-month average): 1.0 = a normal month, 2.5 = a quarterly
order month, 0 = no order. Training weights are the scale, so minimising the absolute error of the multiple is the
same as minimising the absolute error in units, which is what WAPE measures.

No peeking: to forecast from origin O, the model is trained only on rows with origin < O and target month <= O,
i.e. examples whose answers were already known at O.
"""
import gc
import os

import lightgbm as lgb
import numpy as np
import pandas as pd

from .backtest import label, month
from .features import make_features

CATEGORICAL = ["Product_Family", "Region", "Sales_Channel", "Customer_Name", "Powertrain"]
RELATIVE = ["level_3", "level_6", "level_24", "std_12", "last_value", "order_size_12", "same_month_last_year",
            "same_month_2y_ago", "level_x_pattern", "max_12", "median_order_12", "last_order_size", "same_month_3y_ago",
            "ly_prev", "ly_next"]
NOT_FEATURES = {"origin", "target", "SKU_ID", "y", "weight", "scale", "level_12", "p10", "p50", "p90", *RELATIVE}
PARAMS = dict(learning_rate=0.05, num_leaves=63, min_data_in_leaf=200, feature_fraction=0.8, bagging_fraction=0.8,
              bagging_freq=1, verbose=-1, seed=0,
              # 8 by default so results are identical on any machine; set CELERIS_THREADS=32 on a big server for speed
              num_threads=int(os.environ.get("CELERIS_THREADS", 8)))
ROUNDS = 600
QUANTILES = {"p10": 0.1, "p90": 0.9}
# The typical forecast (P50) is a weighted average of five models. One uses the Tweedie loss (the usual choice for
# sparse sales data); four are the earlier set-up: three aim at the typical month (absolute error) with different
# seeds and sampling, one at the average month (squared error). Weights: 0.7 Tweedie, 0.3 the other four together,
# the weight chosen in the backtest on earlier origins only. Evidence: experiments/advanced.py (blend),
# experiments/results_advanced.json; backtest WAPE 0.290 -> 0.288.
MEMBERS = {
    "tweedie": {"objective": "tweedie", "tweedie_variance_power": 1.2, "weight": 0.7},
    "l1": {"objective": "l1", "weight": 0.075},
    "l1_seed1": {"objective": "l1", "seed": 1, "feature_fraction": 0.7, "bagging_fraction": 0.7, "weight": 0.075},
    "l1_seed3": {"objective": "l1", "seed": 3, "feature_fraction": 0.9, "bagging_fraction": 0.9, "weight": 0.075},
    "l2": {"objective": "l2", "weight": 0.075},
}


def training_origins(first="2019-12", last="2026-10", extra=()):
    """Every second month, plus any origin we need to forecast from."""
    base = [label(m) for m in range(month(first), month(last) + 1, 2)]
    return sorted(set(base) | set(extra))


def feature_store(data, macro, events, master, origins):
    parts = []
    for o in origins:
        f = make_features(data, month(o), 19, macro, events, master)
        num = f.select_dtypes("float64").columns
        f[num] = f[num].astype("float32")
        parts.append(f)
    return prepare(pd.concat(parts, ignore_index=True))


def prepare(a):
    a = a.copy()
    for c in CATEGORICAL:
        a[c] = a[c].astype("category")
    a["scale"] = a.level_12.where(a.level_12 > 0, a.order_size_12).fillna(1).clip(lower=1)
    a["log_scale"] = np.log1p(a.scale)
    for c in RELATIVE:
        if c in a:
            a["r_" + c] = (a[c] / a.scale).astype("float32")
    keep = [c for c in a.columns if c not in RELATIVE and c != "level_12"]   # raw copies no longer needed
    return a[keep]


def feature_columns(a):
    return [c for c in a.columns if c not in NOT_FEATURES]


def dataset(train):
    """LightGBM training data, built once and shared by the p50/p10/p90 models (saves memory)."""
    ok = train.y.notna() & (train.weight > 0)
    t = train.loc[ok]
    cols = feature_columns(t)
    return lgb.Dataset(t[cols], (t.y / t.scale).to_numpy("float32"), weight=(t.scale * t.weight).to_numpy("float32"),
                       categorical_feature=CATEGORICAL, free_raw_data=False)


def fit(train_or_ds, objective="l1", alpha=None, rounds=ROUNDS, extra=None):
    params = {**PARAMS, "objective": objective, **{k: v for k, v in (extra or {}).items() if k != "weight"}}
    if alpha is not None:
        params["alpha"] = alpha
    ds = train_or_ds if isinstance(train_or_ds, lgb.Dataset) else dataset(train_or_ds)
    return lgb.train(params, ds, rounds)


def predict(booster, rows):
    return np.clip(booster.predict(rows[booster.feature_name()]) * rows.scale.to_numpy(), 0, None)


def forecast_from(store, origin, quantiles=True):
    """Train on what was known at `origin` and forecast its 19 months. Returns the origin's rows with p50 (and
    p10/p90). The three are sorted per row so that p10 <= p50 <= p90 always holds."""
    rows, _ = forecast_with_models(store, origin, quantiles)
    return rows


WORKERS = int(os.environ.get("CELERIS_WORKERS", 1))   # >1: train models / replay dates in parallel processes
_SHARED = {}


def _fit_spec(spec):
    name, objective, alpha, extra = spec
    return name, fit(_SHARED["ds"], objective, alpha, extra=extra).model_to_string()


def forecast_with_models(store, origin, quantiles=True, workers=None):
    """Like forecast_from, and also returns the trained models (to inspect which features they use)."""
    workers = WORKERS if workers is None else workers
    known = (store.origin < origin) & (store.target <= origin)
    assert (store.target[known] <= origin).all() and (store.origin[known] < origin).all()
    ds = dataset(store.loc[known])
    rows = store.loc[store.origin == origin].copy()
    specs = [(f"p50_{k}", v.get("objective", "l1"), None, v) for k, v in MEMBERS.items()]
    if quantiles:
        specs += [(name, "quantile", a, None) for name, a in QUANTILES.items()]
    if workers > 1:
        # Fork before LightGBM starts any threads in this process (forking after that can deadlock the children);
        # each child builds its own copy of the training data.
        import multiprocessing as mp
        _SHARED["ds"] = ds
        with mp.get_context("fork").Pool(min(workers, len(specs))) as pool:
            models = {n: lgb.Booster(model_str=m) for n, m in pool.map(_fit_spec, specs)}
    else:
        models = {n: fit(ds, obj, a, extra=x) for n, obj, a, x in specs}
    rows["p50"] = predict_p50(models, rows)
    if quantiles:
        for name in QUANTILES:
            rows[name] = predict(models[name], rows)
        q = np.sort(rows[["p10", "p50", "p90"]].to_numpy(), axis=1)
        rows["p10"], rows["p50"], rows["p90"] = q[:, 0], q[:, 1], q[:, 2]
    del ds
    gc.collect()
    return rows, models


def member_weights(models):
    """Weight of each P50 member (from MEMBERS), normalised over the members present."""
    w = {k: MEMBERS.get(k[len("p50_"):], {}).get("weight", 1.0) for k in models if k.startswith("p50")}
    total = sum(w.values())
    return {k: v / total for k, v in w.items()}


def predict_p50(models, rows):
    """Weighted average of the P50 members (models whose name starts with p50)."""
    return sum(w * predict(models[k], rows) for k, w in member_weights(models).items())


def importance(models):
    """Share of the total gain per input, averaged over the P50 members with their weights."""
    parts = []
    for k, w in member_weights(models).items():
        b = models[k]
        imp = pd.Series(b.feature_importance("gain"), index=b.feature_name())
        parts.append(w * imp / imp.sum())
    return pd.concat(parts, axis=1).sum(axis=1).sort_values(ascending=False).round(4)


def _replay_one(args):
    o, quantiles = args
    rows, _ = forecast_with_models(_SHARED["store"], o, quantiles, workers=1)
    return rows


def backtest_long(store, origins, name="LightGBM global model", quantiles=True):
    """Same long format as backtest.run, so the same metrics apply."""
    out = []
    if WORKERS > 1:                                       # one process per replay date, models inside trained in turn
        import multiprocessing as mp
        _SHARED["store"] = store
        with mp.get_context("fork").Pool(min(WORKERS, len(origins))) as pool:
            results = pool.map(_replay_one, [(o, quantiles) for o in origins])
    else:
        results = [forecast_from(store, o, quantiles) for o in origins]
    for r in results:
        r = r[r.y.notna() & (r.weight > 0)]
        cols = ["origin", "SKU_ID", "Region", "h", "y", "weight", "Product_Family", "Sales_Channel", "Customer_Name",
                "Powertrain", "p50"] + (["p10", "p90"] if quantiles else [])
        out.append(r[cols])
    long = pd.concat(out, ignore_index=True).rename(columns={"y": "actual"})
    long["forecast"] = long.p50.round()
    long["method"] = name
    for c in CATEGORICAL:
        if c in long:
            long[c] = long[c].astype(str)
    return long
