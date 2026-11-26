"""Checks on the demand rebuild, the anomaly register, the event impacts and the submission file."""
import json

import numpy as np
import pandas as pd
import pytest

from celeris import submission
from celeris.config import CLEAN, OUT, REPORTS
from celeris.io import read_raw


@pytest.fixture(scope="session", autouse=True)
def outputs():
    if not (OUT / "submission" / "submission.csv").exists():
        import run
        run.step_clean(); run.step_demand(); run.step_submit()


@pytest.fixture(scope="session")
def panel():
    return pd.read_parquet(OUT / "demand" / "panel.parquet")


@pytest.fixture(scope="session")
def register():
    return pd.read_parquet(REPORTS / "anomaly_register.parquet")


@pytest.fixture(scope="session")
def effects():
    return pd.read_csv(REPORTS / "event_effects.csv").set_index("Event_ID")


# ---------------------------------------------------------------- panel

def test_panel_has_one_row_per_series_month_without_gaps(panel):
    assert not panel.duplicated(["SKU_ID", "Region", "Date"]).any()
    span = panel.groupby(["SKU_ID", "Region"]).m.agg(["min", "max", "size"])
    assert (span["max"] - span["min"] + 1 == span["size"]).all()


def test_demand_is_complete_and_non_negative(panel):
    assert panel.demand.notna().all() and (panel.demand >= 0).all()


def test_shipped_matches_clean_sales(panel):
    sales = pd.read_parquet(CLEAN / "sales_clean.parquet")
    j = panel.merge(sales, on=["Date", "SKU_ID", "Region"])
    assert len(j) == len(sales)
    both = j.Units_Sold.notna()
    assert (j.shipped[both] == j.Units_Sold[both]).all()


def test_stockout_months_use_requested_units(panel):
    so = panel[panel.Requested_Units.notna()]
    assert len(so) == len(pd.read_parquet(CLEAN / "stockouts.parquet"))
    assert (so.demand == so.Requested_Units).all()


def test_catchup_months_subtract_the_backorder(panel):
    c = panel[panel.demand_source == "catchup_removed"]
    assert len(c) > 500
    assert np.allclose(c.demand, c.shipped - c.backorder_in)


def test_shortage_ratios_form_a_v(panel):
    r = json.loads((REPORTS / "demand_checks.json").read_text())["shortage_ratio_by_month"]
    vals = list(r.values())
    assert all(v >= 1 for v in vals)
    assert max(r, key=r.get) in ("2021-09", "2021-10")      # worst point of the shortage
    assert vals[0] < 1.2 and vals[-1] < 1.2                  # mild at both ends


def test_shortage_method_beats_no_correction():
    t = json.loads((REPORTS / "demand_checks.json").read_text())["shortage_method_test"]
    assert t["wape_estimated"] < 0.15 < t["wape_uncorrected"]


def test_only_guessed_months_get_zero_weight(panel):
    guessed = panel.demand_source.str.startswith("imputed")
    assert (panel.weight[guessed] == 0).all() and (panel.weight[~guessed] > 0).all()


def test_erp_loss_is_latin_america_aftermarket_export_mar_may_2023(panel):
    e = panel[panel.demand_source == "imputed_erp_loss"]
    assert set(e.Region) == {"Latin America"}
    assert set(e.Sales_Channel) == {"Aftermarket", "Export"}
    assert e.Date.min() >= "2023-03" and e.Date.max() <= "2023-05"


# ---------------------------------------------------------------- anomaly register

def test_register_is_well_formed(register):
    assert register.anomaly_id.is_unique
    assert set(register.kind) == {"data_error", "data_loss", "real_event", "master_data"}
    assert register.reason.notna().all() and (register.reason.str.len() > 0).all()
    assert register.action.notna().all() and (register.action.str.len() > 0).all()


def test_register_covers_every_corrected_demand_month(register, panel):
    fixed = panel[~panel.demand_source.isin(["shipped"])]
    reg = set(zip(register.Date, register.SKU_ID, register.Region))
    assert all(k in reg for k in zip(fixed.Date, fixed.SKU_ID, fixed.Region))


# ---------------------------------------------------------------- event impacts

def test_known_events_show_up(effects):
    assert effects.loc["EV-038", "effect_demand_pct"] < -50            # Nexon strike
    assert effects.loc["EV-052", "effect_demand_pct"] > 15             # Helios second line
    for ev in ["EV-010", "EV-011", "EV-012", "EV-013"]:                 # COVID
        assert effects.loc[ev, "effect_demand_pct"] < -30
    assert effects.loc["EV-019", "effect_shipped_pct"] < -20 < effects.loc["EV-019", "effect_demand_pct"]
    assert effects.loc["DATA-1", "effect_demand_pct"] > 7.3 and effects.loc["DATA-2", "effect_demand_pct"] < -7.3


def test_non_events_are_not_overclaimed(effects):
    for ev in ["EV-035", "EV-043", "EV-048", "EV-058"]:                 # sales director, trade fair, CRM, conference
        assert effects.loc[ev, "verdict"].startswith("No clear effect")
    assert effects.loc["EV-003", "verdict"].startswith("Not applicable")  # Ramadan 2019, region not open yet
    assert effects.loc["EV-033", "verdict"].startswith("Shipments/records affected")  # ERP loss


# ---------------------------------------------------------------- submission

def test_submission_is_valid():
    sub = pd.read_csv(OUT / "submission" / "submission.csv", dtype={"Units_Sold_Forecast": "int64"}, keep_default_na=False)
    info = submission.validate(sub, read_raw("test.csv"), read_raw("sample_submission.csv"),
                               pd.read_parquet(CLEAN / "sku_master.parquet"))
    assert info["rows"] == 58219


def test_validator_rejects_broken_files():
    test, sample = read_raw("test.csv"), read_raw("sample_submission.csv")
    master = pd.read_parquet(CLEAN / "sku_master.parquet")
    good = pd.read_csv(OUT / "submission" / "submission.csv", keep_default_na=False)
    broken = [good.iloc[:-1], good.assign(Units_Sold_Forecast=-1), good.sample(frac=1, random_state=0),
              good.assign(Units_Sold_Forecast=good.Units_Sold_Forecast + 0.5),
              good.assign(Units_Sold_Forecast=np.where(good.SKU_ID == "PTC-005", 5, good.Units_Sold_Forecast))]
    for b in broken:
        with pytest.raises(AssertionError):
            submission.validate(b.reset_index(drop=True), test, sample, master)


def test_new_launches_ramp_up():
    sub = pd.read_csv(OUT / "submission" / "submission.csv", keep_default_na=False)
    for sku in ["PMP-020", "PTC-029", "THM-012"]:
        s = sub[sub.SKU_ID == sku].groupby("Date").Units_Sold_Forecast.sum()
        assert s.iloc[0] > 0 and s.iloc[0] < s.iloc[5]
