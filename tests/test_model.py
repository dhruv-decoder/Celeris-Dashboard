"""Checks on the machine-learning model, its ranges, and the final forecast table."""
import json

import numpy as np
import pandas as pd
import pytest

from celeris.config import CLEAN, OUT, REPORTS
from celeris.io import read_raw

pytestmark = pytest.mark.skipif(not (REPORTS / "model_checks.json").exists(),
                                reason="run `python run.py` first (the model step takes ~15 minutes)")


@pytest.fixture(scope="module")
def model_long():
    return pd.read_parquet(OUT / "backtest" / "model_long.parquet")


@pytest.fixture(scope="module")
def table():
    return pd.read_parquet(OUT / "forecast" / "forecast.parquet")


def test_model_is_scored_on_exactly_the_baseline_rows(model_long):
    base = pd.read_parquet(OUT / "backtest" / "baselines_long.parquet")
    base = base[base.method == base.method.iloc[0]]
    key = ["origin", "SKU_ID", "Region", "h"]
    assert len(model_long) == len(base)
    assert model_long[key].sort_values(key).reset_index(drop=True).equals(base[key].sort_values(key).reset_index(drop=True))


def test_model_beats_the_best_baseline():
    m = json.loads((REPORTS / "model_checks.json").read_text())
    b = json.loads((REPORTS / "best_method.json").read_text())
    assert m["wape"] < b["wape"]


def test_ranges_are_calibrated(model_long):
    below = (model_long.actual < model_long.p10.round()).mean()
    above = (model_long.actual > model_long.p90.round()).mean()
    assert 0.05 < below < 0.15 and 0.05 < above < 0.15


def test_forecast_table_covers_test_and_is_ordered(table):
    test = read_raw("test.csv")
    assert len(table) == len(test)
    assert (table[["Date", "SKU_ID", "Region"]].to_numpy() == test[["Date", "SKU_ID", "Region"]].to_numpy()).all()
    assert (table.p10 <= table.p50 + 1e-9).all() and (table.p50 <= table.p90 + 1e-9).all()
    assert table[["p10", "p50", "p90", "expected"]].notna().all().all() and (table.p10 >= 0).all()


def test_ranges_are_zero_after_end_of_life(table):
    eol = table.SKU_ID.map(pd.read_parquet(CLEAN / "sku_master.parquet").set_index("SKU_ID").EOL_Date)
    dead = eol.notna() & (table.Date > eol)
    assert dead.sum() == 156
    assert (table.loc[dead, ["p10", "p50", "p90", "expected"]] == 0).all().all()


def test_expected_is_bias_corrected_p50(table):
    f = json.loads((REPORTS / "model_checks.json").read_text())["bias_factor"]
    assert 1.0 <= f < 1.15
    assert np.allclose(table.expected, table.p50 * f)


def test_revenue_uses_announced_price_rises(table):
    prices = pd.read_parquet(CLEAN / "list_prices.parquet").set_index("SKU_ID")["2026"]
    row = table[(table.SKU_ID == "BSS-001") & (table.Date == "2028-03")].iloc[0]
    assert row.price_usd == pytest.approx(prices["BSS-001"] * 1.045 * 1.04)


def test_submission_uses_the_better_method():
    choice = json.loads((REPORTS / "submission_choice.json").read_text())
    assert choice["backtest_wape"] == min(choice["model_wape"], choice["baseline_wape"])
    sub = pd.read_csv(OUT / "submission" / "submission.csv", keep_default_na=False)
    file = "submission_v2_model.csv" if choice["method"].startswith("LightGBM") else "submission_v1_best_baseline.csv"
    assert sub.equals(pd.read_csv(OUT / "submission" / file, keep_default_na=False))


def test_real_forecast_only_uses_known_history():
    rows = pd.read_parquet(OUT / "forecast" / "model_rows.parquet")
    assert set(rows.origin.astype(str)) == {"2026-10"}
    assert rows.target.astype(str).min() == "2026-11" and rows.target.astype(str).max() == "2028-05"


def test_p50_is_the_weighted_average_of_its_members():
    import lightgbm as lgb
    from celeris.model import CATEGORICAL, MEMBERS, predict, predict_p50
    files = sorted((OUT / "forecast").glob("model_p50_*.txt"))
    assert len(files) == len(MEMBERS)
    assert abs(sum(m["weight"] for m in MEMBERS.values()) - 1) < 1e-9
    models = {f.stem[6:]: lgb.Booster(model_file=str(f)) for f in files}
    rows = pd.read_parquet(OUT / "forecast" / "model_rows.parquet").head(500)
    for c in CATEGORICAL:
        rows[c] = rows[c].astype("category")
    expected = sum(MEMBERS[k[4:]]["weight"] * predict(m, rows) for k, m in models.items())
    assert np.allclose(predict_p50(models, rows), expected)


def test_ensemble_weight_was_chosen_without_peeking():
    rec = json.loads((OUT.parent / "experiments" / "results_advanced.json").read_text())["blend_tweedie"]
    for r in rec["honest"]:                           # weight picked on earlier origins, scored on the next one
        assert r["wape"] < r["current"]
