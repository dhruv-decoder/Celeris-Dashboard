"""Checks on the backtest, the baselines, the features and the driver analysis."""
import json

import numpy as np
import pandas as pd
import pytest

from celeris.backtest import H, ORIGINS, Data, metrics, month, run
from celeris.baselines import croston_sba, level_x_profile, same_month_last_year
from celeris.config import CLEAN, OUT, REPORTS
from celeris.features import holiday_share, make_features


@pytest.fixture(scope="session", autouse=True)
def outputs():
    if not (REPORTS / "best_method.json").exists():
        import run as pipeline
        for step in ["step_clean", "step_demand", "step_drivers", "step_backtest", "step_features", "step_submit"]:
            getattr(pipeline, step)()


@pytest.fixture(scope="session")
def data():
    return Data(pd.read_parquet(OUT / "demand" / "panel.parquet"), pd.read_parquet(CLEAN / "sku_master.parquet"))


def toy(values, start="2024-01"):
    cols = [month(start) + i for i in range(len(values))]
    return pd.DataFrame([values], index=pd.MultiIndex.from_tuples([("X-001", "Europe")]), columns=cols, dtype=float)


# ---------------------------------------------------------------- backtest

def test_methods_never_see_the_future(data):
    seen = []

    def spy(hist, cut, h):
        seen.append(hist.columns.max() <= cut)
        return same_month_last_year(hist, cut, h)
    run(spy, data)
    assert len(seen) == len(ORIGINS) and all(seen)


def test_wape_and_bias_by_hand():
    long = pd.DataFrame({"method": "m", "forecast": [10, 0, 5], "actual": [8, 2, 5]})
    m = metrics(long).iloc[0]
    assert m.wape == pytest.approx((2 + 2 + 0) / 15, abs=1e-4)
    assert m.bias == pytest.approx((15 - 15) / 15, abs=1e-4)


def test_backtest_scores_19_months_from_each_origin(data):
    long = run(same_month_last_year, data)
    assert set(long.origin) == set(ORIGINS) and long.h.between(1, H).all()
    assert (long.weight > 0).all()


# ---------------------------------------------------------------- baselines

def test_same_month_last_year_on_a_toy_series():
    y = toy(list(range(1, 25)))                               # Jan 2024 .. Dec 2025 = 1..24
    f = same_month_last_year(y, y.columns[-1], 13)
    assert f.iloc[0, 0] == 13                                 # Jan 2026 copies Jan 2025
    assert f.iloc[0, 12] == 13                                # Jan 2027 also copies Jan 2025 (latest available)


def test_level_x_pattern_keeps_a_quarterly_rhythm():
    y = toy([90, 0, 0] * 12)                                  # orders every quarter in Jan/Apr/Jul/Oct
    f = level_x_profile(y, y.columns[-1], 6).iloc[0].to_numpy()
    assert np.allclose(f, [90, 0, 0, 90, 0, 0])


def test_croston_on_steady_demand():
    y = toy([10.0] * 24)
    assert croston_sba(y, y.columns[-1], 3).iloc[0, 0] == pytest.approx(10 * (1 - 0.05))


def test_best_baseline_beats_v0_and_the_average():
    b = pd.read_csv(REPORTS / "backtest_baselines.csv").set_index("method").wape
    best = json.loads((REPORTS / "best_method.json").read_text())["method"]
    assert b[best] < b["Same month last year"] < b["Average of last 12 months"]


# ---------------------------------------------------------------- features

def test_features_do_not_peek_at_the_future(data):
    macro, events = pd.read_parquet(CLEAN / "macro.parquet"), pd.read_parquet(CLEAN / "events.parquet")
    master = pd.read_parquet(CLEAN / "sku_master.parquet")
    cut = month("2024-03")
    a = make_features(data, cut, H, macro, events, master)
    changed = Data(pd.read_parquet(OUT / "demand" / "panel.parquet"), master)
    future = changed.Y.columns > cut
    changed.Y.loc[:, future] = changed.Y.loc[:, future] * 7 + 3
    b = make_features(changed, cut, H, macro, events, master)
    cols = [c for c in a.columns if c not in ("y", "weight")]
    assert a[cols].equals(b[cols]) and not a.y.equals(b.y)


def test_moving_holidays_land_in_the_right_month():
    hs = holiday_share(pd.read_parquet(CLEAN / "events.parquet"))
    assert hs.at["2027-02", "Asia Pacific"] == pytest.approx(10 / 28)      # Lunar New Year 6-15 Feb 2027
    assert hs.at["2027-02", "Middle East & Africa"] == pytest.approx(21 / 28)  # Ramadan from 8 Feb 2027
    assert hs.at["2027-07", "North America"] == 1 and hs.at["2027-08", "Europe"] == 1
    assert hs.at["2027-05", "Europe"] == 0


def test_forecast_features_know_the_orion_shutdown():
    f = pd.read_parquet(OUT / "features" / "forecast_origin_features.parquet")
    hit = f[f.customer_shutdown == 1]
    assert set(hit.Customer_Name) == {"Orion Driveline"} and set(hit.Region) == {"North America"}
    assert set(hit.target) == {"2027-07", "2027-08"}


# ---------------------------------------------------------------- drivers

def test_orders_lead_electrical_and_hydraulic_stably():
    s = pd.read_csv(REPORTS / "macro_leadlag_summary.csv")
    for fam in ["Electrical Assemblies", "Hydraulic Systems"]:
        r = s[(s.family == fam) & (s.indicator == "New_Vehicle_Orders_Index")].iloc[0]
        assert r["corr"] > 0.9 and r.stable and 0 <= r.best_lag <= 3


def test_intermittency_classes_on_toy_series():
    from celeris.features import CLASSES, intermittency
    win = np.array([
        [100.0] * 24,                                   # every month, same size
        [10.0, 100.0] * 12,                             # every month, sizes jump
        [100.0, 0, 0] * 8,                              # every quarter, same size
        [10.0, 0, 0, 300.0, 0, 0] * 4,                  # every quarter, sizes jump
    ])
    adi, cv2, cls = intermittency(win)
    assert [CLASSES[int(c)] for c in cls] == ["smooth", "erratic", "intermittent", "lumpy"]
    assert adi[0] == 1 and adi[2] == 3
    assert cv2[0] == 0


def test_intermittency_ignores_unknown_months():
    from celeris.features import intermittency
    win = np.array([[100.0, np.nan] * 12])              # unknown is not zero: still an order in every known month
    adi, _, cls = intermittency(win)
    assert adi[0] == 1 and cls[0] == 0
