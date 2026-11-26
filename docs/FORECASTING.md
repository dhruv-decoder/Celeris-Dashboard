# Features, drivers and baselines

## How accuracy is measured — `celeris/backtest.py`
**Rolling-origin backtest.** Four forecast origins: Mar 2022, Mar 2023, Mar 2024 and Mar 2025. From each one, a
method sees only the months up to that date and forecasts the next **19 months**, the same horizon as the real task.
We then compare the forecasts with what actually happened.

- **What is scored:**
  - Truth is the rebuilt demand (`docs/DEMAND.md`). Months whose demand is itself an estimate (weight 0) are skipped.
  - Only series that already existed at the origin are scored, as in `test.csv`.
  - Forecasts are rounded to whole numbers, as in the submission.
- **WAPE** = Σ|forecast − actual| / Σ actual. It is the main score; lower is better.
- **Bias** = (Σ forecast − Σ actual) / Σ actual. Negative means the forecast is too low.
- A test proves no method can see months after its origin.

## Baselines — `celeris/baselines.py`
Scored over the 4 origins × 19 months, about 218,000 series-months:

| Method | WAPE | Bias |
|---|---|---|
| **Level × month-of-year pattern** (last-12-month average × the series' own calendar-month pattern from the last 3 years) | **0.355** | −1.4% |
| Same month, average of the 2 latest years | 0.368 | −5.3% |
| Same month last year (v0) | 0.406 | −1.8% |
| Croston-SBA (classic method for intermittent demand) | 0.515 | −10.3% |
| TSB (intermittent) | 0.516 | −6.5% |
| Average of the last 12 months | 0.520 | −1.5% |

**Why level × pattern wins.** Customers order every 1, 2 or 3 months, and all of these divide 12. So each customer
orders in the same calendar months every year, and the series' month-of-year pattern captures both seasonality
and its ordering rhythm. Croston and TSB assume zero months happen at random. Here they don't, so these methods
forecast a flat line and lose to the pattern.

**Where the best baseline is weak:**
- **Middle East & Africa:** WAPE 0.50, bias −22%. The region only opened in 2022 and is still growing, so a 12-month
  average lags behind it.
- **Electrical Assemblies:** WAPE 0.40, the highest of the families.

**Error barely grows with distance:** 0.33 one month ahead, 0.36 at 19 months.

**Timing versus size.** A forecaster that *knew exactly which months* each customer will order, and predicted its
recent average order size there, still scores 0.325. Most of the remaining error comes from order sizes varying, so
the best baseline (0.355) already gets most of the timing right. Gains for the model have to come from:
- level and trend (Middle East & Africa, growing families)
- known events (the Orion retooling Jul–Aug 2027)
- better order-size estimates

## Features — `celeris/features.py`
One row per series × origin × horizon. Series history uses only months up to the origin; a test changes all later
months and checks every feature stays identical. Things known in advance about the target month are allowed.

| Group | Features |
|---|---|
| Recent history | average of the last 3/6/12/24 months, variability, share of zero months, last value, trend (last 12 vs previous 12 months), average order size, months of history |
| Ordering rhythm | usual gap between orders, months since the last order (now and at the target month), whether the target month falls on the rhythm, how often the target's calendar month had an order in the last 3 years |
| Demand class (added Nov 25) | Syntetos–Boylan **intermittency class** from the last 24 months: ADI (average months between orders) and CV² (variation of order sizes), cut at 1.32 and 0.49 into smooth / erratic / intermittent / lumpy |
| Order size (added Nov 25) | orders in the last 12 months, biggest month, median order size, size of the last order |
| Seasonality | the series' month-of-year pattern for the target month, level × pattern, same month 1, 2 and 3 years earlier, the months either side of last year's (an order that slipped by a month) |
| Calendar & holidays | target month and year, horizon, share of the month in a regional holiday. Exact dates are used, so Lunar New Year and Ramadan move correctly between years |
| Known events | January price rise (%), customer shutdowns (Orion North America, Jul–Aug 2027), the Helios India capacity step |
| Lifecycle | months to end of life, age of the product at the target month |
| Economy | new vehicle orders, auto production growth and PMI, each 1–3 months before the target month; EV adoption index |
| Growth | region, family, family × region, customer × region and product (all regions): last 12 months vs the 12 before |
| Categories | family, region, channel, customer, powertrain |

## Drivers — `celeris/drivers.py`
**Method.** We correlate each family's year-on-year demand growth with each indicator's year-on-year change, with
the indicator moved 0–8 months earlier. Only series sold through the whole 2019–2026 period are used. Stability is
checked by repeating the calculation on 2021-07..2023-12 and 2024-01..2026-10 (COVID left out).

**What leads demand:**
- **New vehicle orders** lead **Electrical, Hydraulic and Precision Machined** by 1–3 months. The correlation is 0.88–0.95 and stable in both halves.
- The **PMI** leads **Electrical and Hydraulic** by about 2 months (0.83–0.85), also stable.
- **Auto production growth** leads **Brake & Safety, Chassis, Thermal and Precision** by about 1 month (0.73–0.85 overall).
  For Chassis, Thermal and Precision it was strong in 2021–23 (0.81–0.88) but weak in 2024–26 (0.36–0.43), so it
  is **not stable**. Brake & Safety held up better (0.86 → 0.61).

**EV shift.** EV parts went from 0% to 9.9% of demand and petrol-only (ICE) parts from 24% to 13%. Both have been
flat since 2025, as has the EV adoption index (about 89 → 91).

**Caveat.** Indicators move slowly and together, and 2020–2026 contains few independent cycles. So a correlation
says which indicator is *worth trying*; the backtest decides whether it actually helps the forecast.

## Submission
`submission.csv` now uses the best backtested method (level × month-of-year pattern) plus the lifecycle rules.
v0 is kept as `submission_v0_seasonal_naive.csv`.

## Demand classes — `celeris/features.py:intermittency`
The classic way to sort series for forecasting (Syntetos & Boylan): how often orders come (ADI) and how much their
sizes vary (CV²).

| Class | Meaning | Series (Oct 2026) | Share of demand |
|---|---|---|---|
| Smooth | orders most months, steady sizes | 2,212 | 71% |
| Intermittent | gaps between orders, steady sizes | 789 | 25% |
| Erratic | orders most months, sizes jump | 68 | 2% |
| Lumpy | gaps and jumpy sizes | 14 | 1% |

Here "intermittent" mostly means a **regular** rhythm (every 2nd month or quarter), not random gaps, which is why
Croston and TSB lose. The class is a model input and a split in the dashboard's accuracy chart. Replay-test results
by class are in `dashboard/data/meta.json` (`class_accuracy`): the model beats the best simple method in every
class (smooth 28.2% vs 34.3%, intermittent 30.0% vs 35.6%), and the gap classes are where the typical-month forecast
runs lowest (intermittent −7%, lumpy −9%).
