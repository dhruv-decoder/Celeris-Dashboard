"""Checks on the cleaned outputs. Each test is a promise the clean data must keep."""
import re

import numpy as np
import pandas as pd
import pytest

from celeris.config import CHANNELS, CLEAN, REGIONS, REPORTS
from celeris.io import read_raw


@pytest.fixture(scope="session", autouse=True)
def pipeline_outputs():
    if not (CLEAN / "sales_clean.parquet").exists():
        from run import step_clean
        step_clean()


@pytest.fixture(scope="session")
def sales():
    return pd.read_parquet(CLEAN / "sales_clean.parquet")


@pytest.fixture(scope="session")
def master():
    return pd.read_parquet(CLEAN / "sku_master.parquet")


def test_one_row_per_record_id(sales):
    raw = read_raw("train.csv")
    assert len(sales) == raw.Record_ID.nunique()
    assert sales.Record_ID.is_unique


def test_one_row_per_date_sku_region(sales):
    assert not sales.duplicated(["Date", "SKU_ID", "Region"]).any()


def test_keys_are_official(sales, master):
    assert sales.Region.isin(REGIONS).all()
    assert sales.Sales_Channel.isin(CHANNELS).all()
    assert sales.SKU_ID.isin(master.SKU_ID).all()
    assert sales.Customer_Name.nunique() == 12
    assert sales.Date.str.fullmatch(r"\d{4}-\d{2}").all()
    assert sales.Date.between("2019-01", "2026-10").all()
    assert (sales.Year.astype(str) + "-" + sales.Month.astype(str).str.zfill(2) == sales.Date).all()


def test_each_family_has_one_business_unit(sales, master):
    assert (sales.groupby("Product_Family").Business_Unit.nunique() == 1).all()
    assert (master.groupby("Product_Family").Business_Unit.nunique() == 1).all()


def test_units_are_whole_and_non_negative(sales):
    u = sales.Units_Sold.dropna()
    assert (u >= 0).all() and (u == u.round()).all()
    assert (sales.units_status == "missing").sum() == sales.Units_Sold.isna().sum()


def test_revenue_equals_units_times_price(sales):
    s = sales.dropna(subset=["Units_Sold"])
    assert np.allclose(s.Revenue_USD, (s.Units_Sold * s.Unit_Price_USD).round(2), atol=0.011)


def test_one_price_per_sku_month_in_every_region(sales):
    assert (sales.groupby(["SKU_ID", "Date"]).Unit_Price_USD.nunique() == 1).all()


def test_prices_only_change_where_an_error_was_flagged(sales):
    raw = read_raw("train.csv")
    raw_price = pd.to_numeric(raw.Unit_Price_USD.values[sales.src_line - 2])
    ok = (sales.price_status == "ok").values
    assert np.allclose(sales.Unit_Price_USD.values[ok], raw_price[ok], atol=0.011)
    assert ok.mean() > 0.84            # the other rows are known error classes or 'no order' rows (price 0)
    assert set(sales.price_status) <= {"ok", "cents", "x10", "eur", "zero"}


def test_price_changes_only_in_january_or_surcharge(sales):
    p = sales.groupby(["SKU_ID", "Date"]).Unit_Price_USD.first().reset_index().sort_values(["SKU_ID", "Date"])
    p["prev"] = p.groupby("SKU_ID").Unit_Price_USD.shift()
    changed = p[p.prev.notna() & ~np.isclose(p.Unit_Price_USD, p.prev)]
    month = changed.Date.str[5:]
    assert month.isin(["01", "07"]).all(), changed[~month.isin(["01", "07"])].head()


def test_yearly_increase_matches_events_calendar(sales):
    ev = read_raw("events_calendar.csv")
    ev = ev[ev.Description.str.contains("Annual list-price increase")]
    announced = {int(d[:4]): float(re.search(r"([\d.]+)%", t)[1]) for d, t in zip(ev.Start_Date, ev.Description)}
    jan = sales[sales.Month == 1].groupby(["SKU_ID", "Year"]).Unit_Price_USD.first().unstack()
    for y in range(2020, 2027):
        actual = 100 * ((jan[y] / jan[y - 1]).median() - 1)
        assert abs(actual - announced[y]) < 0.3, (y, actual, announced[y])


def test_surcharge_is_six_percent(sales):
    p = sales[sales.Year == 2022].groupby(["SKU_ID", sales.Date.between("2022-07", "2022-12")]).Unit_Price_USD.first()
    p = p.unstack().dropna()
    assert np.allclose(p[True] / p[False], 1.06, atol=0.001)


def test_units_match_stockout_shipments(sales):
    st = pd.read_parquet(CLEAN / "stockouts.parquet")
    j = st.merge(sales, on=["Date", "SKU_ID", "Region"])
    independent = j[j.units_status != "fixed_per10_exact"]
    assert (independent.Shipped_Units == independent.Units_Sold).mean() == 1.0


def test_no_sales_after_eol(sales, master):
    eol = sales.SKU_ID.map(master.set_index("SKU_ID").EOL_Date)
    assert not ((eol.notna()) & (sales.Date > eol) & (sales.Units_Sold > 0)).any()


def test_latest_customer_and_channel_match_test_file(sales):
    test = read_raw("test.csv")
    last = sales.sort_values("Date").groupby(["SKU_ID", "Region"])[["Customer_Name", "Sales_Channel"]].last()
    t = test.groupby(["SKU_ID", "Region"])[["Customer_Name", "Sales_Channel"]].first()
    j = t.join(last.add_suffix("_train"), how="inner")
    assert len(j) == 3058
    assert (j.Customer_Name == j.Customer_Name_train).all()
    assert (j.Sales_Channel == j.Sales_Channel_train).all()


def test_macro_is_complete_and_plausible():
    m = pd.read_parquet(CLEAN / "macro.parquet")
    assert len(m) == 113 and m.Date.is_unique and not m.isna().any().any()
    assert m.ISM_Manufacturing_PMI.between(30, 70).all()
    assert m.Auto_Production_YoY_Pct.abs().max() < 60
    assert (m[m.Year == 2019].Auto_Production_YoY_Pct.abs() > 1).all()


def test_quality_report_written():
    import json
    q = json.loads((REPORTS / "data_quality.json").read_text())
    assert q["summary"]["issues"] > 30
    assert {"error_fixed", "error_dropped", "inferred", "real_event", "master_data"} <= set(q["summary"]["by_category"])
