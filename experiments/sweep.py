"""Model sweep (2026-11-25): can richer features, other settings, more training origins or an average of several
models beat the first-version model? Every variant is scored on exactly the same replay test (4 origins x 19 months, the
same rows as run.py model_backtest). Run from the repo root.

  python experiments/sweep.py build            # feature table with every month as an origin (~10 min)
  python experiments/sweep.py run base feat    # P50 replay test for some variants -> pred_<name>.parquet
  python experiments/sweep.py blend            # scores of averages of the variants already run

Results are appended to outputs/experiments/sweep_results.jsonl and copied to experiments/results_2026-11-25.json.
"""
import itertools
import json
import os
import sys
import time

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, os.getcwd())
from celeris.backtest import ORIGINS, Data, label, month           # noqa: E402
from celeris.features import make_features                         # noqa: E402
from celeris.model import CATEGORICAL, PARAMS, feature_columns, prepare  # noqa: E402

S = "outputs/experiments/"
os.makedirs(S, exist_ok=True)
TABLE = S + "sweep_feat.parquet"

# features added on 2026-11-25 (the first-version model had none of these)
NEW = ["orders_12", "r_max_12", "r_median_order_12", "adi", "cv2", "demand_class", "r_last_order_size",
       "customer_growth_12", "sku_growth_12", "r_same_month_3y_ago", "r_ly_prev", "r_ly_next", "moy_order_rate",
       "gap_at_target"]

B = {**PARAMS, "objective": "l1"}
VARIANTS = {
    # name: (params, rounds, use new features, origin step in months)
    "base":    (B, 600, False, 2),
    "feat":    (B, 600, True, 2),
    "feat_m":  (B, 600, True, 1),
    "big":     ({**B, "learning_rate": 0.03, "num_leaves": 127, "min_data_in_leaf": 100}, 1500, True, 2),
    "big_m":   ({**B, "learning_rate": 0.03, "num_leaves": 127, "min_data_in_leaf": 100}, 1500, True, 1),
    "small":   ({**B, "num_leaves": 31, "min_data_in_leaf": 400, "seed": 2}, 900, True, 2),
    "s1":      ({**B, "seed": 1, "feature_fraction": 0.7, "bagging_fraction": 0.7}, 600, True, 2),
    "s1_m":    ({**B, "seed": 1, "feature_fraction": 0.7, "bagging_fraction": 0.7}, 600, True, 1),
    "l2":      ({**B, "objective": "l2"}, 600, True, 2),
    "huber":   ({**B, "objective": "huber", "alpha": 1.0}, 600, True, 2),
    "s3":      ({**B, "seed": 3, "feature_fraction": 0.9, "bagging_fraction": 0.9}, 600, True, 2),
    "s4":      ({**B, "seed": 4, "feature_fraction": 0.6, "num_leaves": 95, "min_data_in_leaf": 300}, 700, True, 2),
    "q55":     ({**B, "objective": "quantile", "alpha": 0.55}, 600, True, 2),
    "tweedie": ({**B, "objective": "tweedie", "tweedie_variance_power": 1.2}, 600, True, 2),
}


def build():
    t = time.time()
    p = pd.read_parquet("outputs/demand/panel.parquet")
    m = pd.read_parquet("outputs/clean/sku_master.parquet")
    mac = pd.read_parquet("outputs/clean/macro.parquet")
    ev = pd.read_parquet("outputs/clean/events.parquet")
    d = Data(p, m)
    origins = [label(x) for x in range(month("2019-12"), month("2026-10") + 1)]
    parts = []
    for o in origins:
        f = make_features(d, month(o), 19, mac, ev, m)
        num = f.select_dtypes("float64").columns
        f[num] = f[num].astype("float32")
        parts.append(f)
    a = prepare(pd.concat(parts, ignore_index=True))
    a.to_parquet(TABLE)
    print(f"built {len(origins)} origins, {a.shape}, {time.time() - t:.0f}s", flush=True)


def load():
    a = pd.read_parquet(TABLE)
    for c in CATEGORICAL:
        a[c] = a[c].astype("category")
    return a


def origin_set(step):
    first, last = month("2019-12"), month("2026-10")
    return {label(x) for x in range(first, last + 1, step)} | set(ORIGINS)


def wape(pred, y):
    pr = np.round(pred)
    return float(np.abs(pr - y).sum() / y.sum()), float((pr.sum() - y.sum()) / y.sum())


def run(name, a):
    params, rounds, new, step = VARIANTS[name]
    cols = [c for c in feature_columns(a) if new or c not in NEW]
    allowed = origin_set(step)
    out, t = [], time.time()
    for o in ORIGINS:
        tr = a[a.origin.isin(allowed) & (a.origin < o) & (a.target <= o) & a.y.notna() & (a.weight > 0)]
        te = a[(a.origin == o) & a.y.notna() & (a.weight > 0)]
        ds = lgb.Dataset(tr[cols], (tr.y / tr.scale).to_numpy("float32"),
                         weight=(tr.scale * tr.weight).to_numpy("float32"), categorical_feature=CATEGORICAL)
        mdl = lgb.train(params, ds, rounds)
        pred = np.clip(mdl.predict(te[cols]) * te.scale.to_numpy(), 0, None)
        keep = ["origin", "SKU_ID", "Region", "target", "h", "y", "Product_Family"]
        out.append(te[keep].assign(pred=pred))
        if o == ORIGINS[-1]:
            imp = pd.Series(mdl.feature_importance("gain"), index=mdl.feature_name())
            (imp / imp.sum()).sort_values(ascending=False).round(4).to_csv(S + f"importance_{name}.csv")
        del ds, tr
    o = pd.concat(out, ignore_index=True)
    o["Product_Family"] = o.Product_Family.astype(str)
    o.to_parquet(S + f"pred_{name}.parquet")
    w, b = wape(o.pred, o.y)
    res = {"variant": name, "wape": round(w, 4), "bias": round(b, 4), "rows": len(o), "features": len(cols),
           "rounds": rounds, "origin_step": step, "minutes": round((time.time() - t) / 60, 1),
           "by_origin": {g: round(wape(x.pred, x.y)[0], 4) for g, x in o.groupby("origin")}}
    with open(S + "sweep_results.jsonl", "a") as fh:
        fh.write(json.dumps(res) + "\n")
    print(json.dumps(res), flush=True)


def blend():
    preds = {}
    for f in sorted(os.listdir(S)):
        if f.startswith("pred_") and f.endswith(".parquet"):
            preds[f[5:-8]] = pd.read_parquet(S + f).set_index(["origin", "SKU_ID", "Region", "target"]).sort_index()
    names = list(preds)
    y = preds[names[0]].y
    org = preds[names[0]].index.get_level_values(0)
    rows = []
    for k in range(1, min(4, len(names)) + 1):
        for combo in itertools.combinations(names, k):
            p = np.mean([preds[n].pred.to_numpy() for n in combo], axis=0)
            w, b = wape(p, y.to_numpy())
            per = {o: round(wape(p[org == o], y[org == o].to_numpy())[0], 4) for o in ORIGINS}
            rows.append({"combo": "+".join(combo), "wape": round(w, 4), "bias": round(b, 4), "by_origin": per})
    rows.sort(key=lambda r: r["wape"])
    json.dump(rows[:40], open(S + "sweep_blends.json", "w"), indent=1)
    for r in rows[:15]:
        print(r)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "build":
        build()
    elif cmd == "run":
        A = load()
        for n in sys.argv[2:]:
            run(n, A)
    elif cmd == "blend":
        blend()
