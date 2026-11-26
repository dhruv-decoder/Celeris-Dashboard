"""The dashboard's data files must match the pipeline outputs exactly (no numbers typed in by hand)."""
import json

import numpy as np
import pandas as pd
import pytest

from celeris.config import OUT, REPORTS, ROOT

DATA = ROOT / "dashboard" / "data"
pytestmark = pytest.mark.skipif(not (DATA / "series.json").exists(), reason="run `python run.py export` first")


@pytest.fixture(scope="module")
def series():
    return json.loads((DATA / "series.json").read_text())


def _total(arrays):
    return sum(v for a in arrays for v in a if v is not None)


def test_every_series_is_there(series):
    panel = pd.read_parquet(OUT / "demand" / "panel.parquet")
    table = pd.read_parquet(OUT / "forecast" / "forecast.parquet")
    keys = set(zip(panel.SKU_ID, panel.Region)) | set(zip(table.SKU_ID, table.Region))
    assert len(series["sku"]) == len(keys)


def test_history_totals_match_the_panel(series):
    panel = pd.read_parquet(OUT / "demand" / "panel.parquet")
    assert _total(series["demand"]) == pytest.approx(panel.demand.round().sum(), abs=1)
    assert _total(series["shipped"]) == pytest.approx(panel.shipped.round().sum(), abs=1)


def test_forecast_totals_match_the_forecast_table(series):
    table = pd.read_parquet(OUT / "forecast" / "forecast.parquet")
    assert _total(series["exp"]) == pytest.approx(table.expected.sum(), rel=1e-4)
    assert _total(series["p50"]) == pytest.approx(table.p50.sum(), rel=1e-4)
    assert _total(series["frev"]) == pytest.approx(table.revenue_expected.sum(), rel=1e-4)


def test_backtest_matches_the_model_replay(series):
    long = pd.read_parquet(OUT / "backtest" / "model_long.parquet")
    act = _total(x for s in series["bt"]["act"] for x in s)
    err = sum(abs(m - a) for ms, as_ in zip(series["bt"]["model"], series["bt"]["act"])
              for mo, ao in zip(ms, as_) for m, a in zip(mo, ao) if a is not None)
    assert act == pytest.approx(long.actual.sum(), abs=1)
    assert err / act == pytest.approx(json.loads((REPORTS / "model_checks.json").read_text())["wape"], abs=1e-3)


def test_anomalies_and_reports_are_complete():
    a = json.loads((DATA / "anomalies.json").read_text())
    assert len(a["rows"]) == len(pd.read_parquet(REPORTS / "anomaly_register.parquet"))
    meta = json.loads((DATA / "meta.json").read_text())
    assert meta["model"]["total_ranges"]["rho"] > 0
    assert set(meta["scenarios"]["total_change"]) == {"pmi_minus_3", "orders_minus_5pct", "auto_production_minus_3"}


def test_dashboard_code_reads_only_data_files():
    js = (ROOT / "dashboard" / "app.js").read_text()
    for name in ["series.json", "meta.json", "anomalies.json", "quality.json", "drivers.json", "stockouts.json"]:
        assert f'"{name}"' in js
    assert "fetch(" in js and js.count("fetch(") == 1


def test_findings_are_computed_and_link_to_real_views():
    meta = json.loads((DATA / "meta.json").read_text())
    js = (ROOT / "dashboard" / "app.js").read_text()
    assert len(meta["findings"]) >= 10
    for f in meta["findings"]:
        assert f["value"] and f["title"] and f["text"] and f["action"]
        assert f'"{f["link"]["view"]}"' in js                     # the view exists in the app
        assert any(ch.isdigit() for ch in f["text"])              # every finding carries a number from the data


def test_whatif_grid_is_complete(series):
    meta = json.loads((DATA / "meta.json").read_text())
    grid = meta["scenarios"]["grid"]
    keys = {f"{ind}|{d:g}" for ind, g in grid.items() for d in g["steps"]}
    assert set(series["whatif"]["series"]) == keys == set(series["whatif"]["fam_month"])
    for k in keys:
        assert len(series["whatif"]["series"][k]) == len(series["sku"])
    chk = meta["scenarios"]["combination_check"]
    assert abs(chk["together_change"] - chk["multiplied_change"]) < 0.01   # multiplying slider effects is fair


def test_events_on_the_timeline_are_well_formed():
    meta = json.loads((DATA / "meta.json").read_text())
    starts = [e["start"] for e in meta["story"]]
    assert starts == sorted(starts)
    for e in meta["story"]:
        assert e["end"] >= e["start"] and ": " in e["scope"] and e["text"]
    assert any(e["label"] == "Orion retooling" and not e["past"] for e in meta["story"])


def test_demand_classes_exported(series):
    assert len(series["cls"]) == len(series["sku"])
    assert set(c for c in series["cls"] if c is not None) <= {0, 1, 2, 3}
    assert all(len(c) in (0, len(series["bt"]["origins"])) for c in series["bt"]["cls"])
