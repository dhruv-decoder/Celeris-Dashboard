"""What-if: re-run the trained P50 models with a different economy across the whole forecast window.

The model sees each indicator in two ways (features.py): its value 1-3 months before the target month, and how the
value 2 months before the target compares with its average over the 12 months before the forecast origin. A scenario
shifts the indicator in the forecast window only (the past is fixed), so both are recomputed consistently.
"""
import lightgbm as lgb
import numpy as np
import pandas as pd

from .backtest import label, month
from .model import predict_p50

# Slider grid for the dashboard: each indicator the model uses, moved by these steps for the whole forecast window.
GRID = {
    "ISM_Manufacturing_PMI": {"label": "Factory index (PMI)", "kind": "points", "steps": [-6.0, -3.0, 3.0, 6.0]},
    "New_Vehicle_Orders_Index": {"label": "New vehicle orders", "kind": "percent", "steps": [-0.10, -0.05, 0.05, 0.10]},
    "Auto_Production_YoY_Pct": {"label": "Auto production growth", "kind": "points", "steps": [-6.0, -3.0, 3.0, 6.0]},
}

SCENARIOS = {
    "pmi_minus_3": {"label": "Factory index (PMI) 3 points lower", "indicator": "ISM_Manufacturing_PMI",
                    "kind": "points", "delta": -3.0},
    "orders_minus_5pct": {"label": "New vehicle orders 5% lower", "indicator": "New_Vehicle_Orders_Index",
                          "kind": "percent", "delta": -0.05},
    "auto_production_minus_3": {"label": "Auto production growth 3 points lower", "indicator": "Auto_Production_YoY_Pct",
                                "kind": "points", "delta": -3.0},
}


def shifted(rows, macro, sc):
    """Rows with one indicator shifted for months after the origin."""
    r = rows.copy()
    ind = sc["indicator"]
    origin = str(r.origin.iloc[0])
    cut = month(origin)
    mac = macro.set_index("Date")[ind]
    base = mac[[label(m) for m in range(cut - 11, cut + 1)]].mean()
    target = r.target.astype(str).map(month)

    def move(v):
        return v + sc["delta"] if sc["kind"] == "points" else v * (1 + sc["delta"])

    for lag in (1, 2, 3):
        col = f"{ind}_lag{lag}"
        future = (target - lag) > cut                    # only months after the origin can change
        r.loc[future, col] = move(r.loc[future, col])
    col = f"{ind}_vs_level_window"
    future = (target - 2) > cut
    if ind == "Auto_Production_YoY_Pct":                 # stored as a difference in points
        r.loc[future, col] = r.loc[future, col] + sc["delta"]
    else:                                                # stored as a ratio to the window average
        v = (r.loc[future, col] + 1) * base
        r.loc[future, col] = move(v) / base - 1
    return r


def _frame(key, rows, base, p):
    return pd.DataFrame({"scenario": key, "target": rows.target.astype(str).to_numpy(),
                         "SKU_ID": rows.SKU_ID.astype(str).to_numpy(), "Region": rows.Region.astype(str).to_numpy(),
                         "base_p50": base, "p50": p, "ratio": np.where(base > 0, p / np.maximum(base, 1e-9), 1.0)})


def run_all(rows, macro, folder):
    """The three named scenarios, the slider grid, and one combined run that checks whether multiplying single
    effects (what the dashboard does when several sliders move) is close to moving them together."""
    models = {f.stem[len("model_"):]: lgb.Booster(model_file=str(f)) for f in sorted(folder.glob("model_p50*.txt"))}
    base = predict_p50(models, rows)
    out = []
    for key, sc in SCENARIOS.items():
        out.append(_frame(key, rows, base, predict_p50(models, shifted(rows, macro, sc))))
    for ind, g in GRID.items():
        for d in g["steps"]:
            sc = {"indicator": ind, "kind": g["kind"], "delta": d}
            out.append(_frame(f"{ind}|{d:g}", rows, base, predict_p50(models, shifted(rows, macro, sc))))
    both = rows
    for key in SCENARIOS:
        both = shifted(both, macro, SCENARIOS[key])
    combined = predict_p50(models, both)
    # rows forecast at under half a unit are left out of the ratio (dividing by ~0 explodes)
    singles = np.prod([np.where(base > 0.5, o.p50.to_numpy() / np.maximum(base, 0.5), 1.0)
                       for o in out[:len(SCENARIOS)]], axis=0) * base
    check = {"scenarios": list(SCENARIOS), "together_change": round(float(combined.sum() / base.sum() - 1), 4),
             "multiplied_change": round(float(singles.sum() / base.sum() - 1), 4)}
    return pd.concat(out, ignore_index=True), check
