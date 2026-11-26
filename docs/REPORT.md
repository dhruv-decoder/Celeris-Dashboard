# Celeris Control Tower: demand forecasting report
**Dhruv Tibarewal** · MKS AI & Data Hackathon · November 2026

- **Live dashboard:** https://celeris-control-tower.vercel.app (public, no login, works on a phone)
- **Code:** https://github.com/dhruv-decoder/Celeris-Dashboard

---

## In one minute
- **The task.** Celeris sells 598 parts in 6 regions. We had to clean eight years of messy ERP sales history, explain
  its unusual points, and forecast monthly demand for 3,068 part-region series from Nov 2026 to May 2028
  (58,219 rows). We also had to build a dashboard planners can use.
- **What makes it hard.** The brief grades two things above all:
  - telling **data errors** (which you fix) apart from **real events** (which you keep and explain)
  - forecasting **what customers ask for**, not what Celeris managed to ship during stockouts
- **What we did.** One command runs the whole pipeline: clean → rebuild demand → register anomalies → study drivers →
  backtest → model → dashboard. 118 automatic tests guard it.
- **Results.**
  - Every data fix is proved with a second source. The cleaned data matches an independent answer key on
    **5,762 / 5,762** rows.
  - The forecast is **28.8%** off per part per month, against **35.5%** for the best simple method (19% less error).
    That is measured by replaying four past years, 19 months ahead each time.
  - At the level planners buy at, the error is **7.3% per family-month** and **4.6% for the company's monthly total**.
  - Every forecast comes with a calibrated low–high range (P10–P90), for single series and for totals.
  - The dashboard is **live on the web** and opens with twelve findings, each with a number, an action and a link to
    its evidence.

| What | File |
|---|---|
| Submission | `outputs/submission/submission.csv` |
| Forecast with ranges and revenue | `outputs/forecast/forecast.csv` |
| Data-quality report | `outputs/reports/data_quality.json` |
| Anomaly register | `outputs/reports/anomaly_register.csv` |
| Dashboard | https://celeris-control-tower.vercel.app, or locally `python -m http.server 8000 -d dashboard` |
| Step-by-step detail | `docs/CLEANING.md`, `DEMAND.md`, `FORECASTING.md`, `MODEL.md`, `DASHBOARD.md` |

---

## 1. Cleaning: prove before fixing
A value is changed only when another source shows the true value. The sources:
- revenue ÷ price
- the stockout file
- load timestamps (every row loads exactly one month after its sales month)
- the product master
- the test file

**The results:**
- 248,248 raw rows → 244,566 clean rows, one per month × part × region.
- 48 kinds of problem logged, with about 7,700 rows corrected.

| Problem | Rows | Fix |
|---|---|---|
| Same record loaded twice | 3,682 | Keep the latest load (business rule 2) |
| 29 ways of writing a month; "2062" for 2026 | ~9,850 | Standard YYYY-MM; the load date proves 2026 |
| 220 spellings of 12 customers; 31 of 6 regions ("NA" = North America) | ~8,600 | Mapped and reviewed by hand |
| Price typed in cents / booked in euros (Europe 2022) | 614 / 498 | List price in USD |
| India Jul–Dec 2021 counted in packs of 10 | 385 | Units × 10, price ÷ 10 |
| Units blank, 10× or 100× too big, negative | 3,404 | Revenue ÷ price shows the true units |
| Revenue 10× too big or two digits swapped | 734 | Units × price |

**Proof.** The stockout file lists exact shipments for thousands of months and was never used to build the fixes.
After cleaning, it matches on **5,762 of 5,762** rows (98.6% before). Every rule has a unit test, and the tests were
shown to fail on deliberately broken data.

## 2. What customers really wanted, and every unusual point
Every month now has two numbers: **shipped** and **demand** (what customers asked for).

**How demand was rebuilt:**
- **Stockout months (5,830):** demand = the units customers requested, from the stockout file.
- **Catch-up months (1,034):** the month after a stockout ships last month's back-order on top of new orders. That
  extra is removed. In 141 months the shipment equals the back-order exactly, which confirms the rule.
- **Chip shortage, Mar 2021 – Sep 2022 (Electrical Assemblies):**
  - Celeris could ship as little as **48%** of orders.
  - About 30% of those months are missing from the stockout file. We estimate them from that month's overall shortfall.
  - Tested on months where the truth is known: **11% error**, against 34% if left uncorrected.
- **Lost records:**
  - 331 blank rows turned out to be Latin America aftermarket & export, Mar–May 2023: the ERP migration in the
    events calendar.
  - Another 946 months have no row at all.
  - Both are estimated from each customer's ordering rhythm and **ignored when training the model** (a missing value
    is not a zero).

**Anomaly register** (18,770 points), each with a kind, reason and action:
- data errors (5,960)
- data loss (1,277)
- real events (11,517)
- master-data conflicts (16)

Real events include 547 one-off bulk orders (measured against each customer's usual *order size*), COVID, a strike,
145 changes in how often customers order, and 25 products reaching end of life.

**Did events really move demand?** Each calendar event is compared with the same group in other years (holidays:
with regions on no holiday). A **placebo test** on 300 random months shows how much a month moves by chance: ±7%
company, ±12% region, ±18% customer.

| Event | Effect on demand |
|---|---|
| COVID lockdowns | −16% to −69% by region |
| Nexon strike | −81% |
| Early buying before the 2023 price rise | +13%, then −11% |
| Helios second Indian line | +23%, permanent |
| North America July / Europe August shutdowns | −44% / −54%, every year |
| Rebrand, trade fair, new sales director, CRM upgrade | no real effect |

**Accounts.**
- No customer was lost outright: every customer-region is within −7% to +19.5% of the year before.
- **23 product accounts changed hands.** Apex Tier One won 6 and lost 2; Nexon lost 4 and won 1.
- After a switch, the new customer ordered a median **36% more**.

## 3. What drives demand
- **Customers order on a rhythm.** 2,480 series order monthly, 365 every second month, 238 every quarter. Because 1,
  2 and 3 all divide 12, each customer's order months **repeat on the calendar**. This is the single most useful fact
  for forecasting.
- **Each region has its own seasonal dip** (last 3 years, 100 = an average month): Europe August 51 (plant
  shutdowns), North America July 65 (retooling weeks), Asia Pacific February 69 (Lunar New Year), Middle East & Africa
  around Ramadan (91). Latin America (December 74) and India (January and November 91) dip too, although the events
  calendar lists no holiday for them; the model learns these from each series' own month-of-year pattern. Europe and
  North America make up part of the gap the month after (115 and 111).
- **Demand classes.** Sorted the standard way (how often orders come, how much their sizes vary): 2,212 series are
  smooth (71% of volume), 789 intermittent (25%), 68 erratic and 14 lumpy. Here "intermittent" mostly means a
  regular rhythm, not random gaps, which is why the textbook intermittent-demand methods lose.
- **Volume is spread out.** It takes 59% of the series to reach 80% of volume (ABC analysis in the dashboard), so
  planning cannot focus on a handful of parts.
- **The economy gives 1–3 months' warning for some families.** New vehicle orders lead Electrical, Hydraulic and
  Precision Machined parts by 1–3 months (correlation 0.88–0.95), in both 2021–23 and 2024–26.
- **Not every link is stable.** Auto production led the car-body families in 2021–23 (0.81–0.88) but only 0.36–0.43
  afterwards.
- **The shift to electric has paused.** EV-only parts grew from 0% to 9.9% of demand and petrol-only from 24% to 13%.
  Both have been flat since 2025, like the EV adoption index.

## 4. Forecasting
**How accuracy is measured.**
- Forecasts were made as of Mar 2022, 2023, 2024 and 2025 (rolling origin). Each time, a method saw only the past and forecast the next 19
  months, the same task as the real one. Its forecasts were then compared with what customers actually asked for.
- Every method is scored on the same 218,063 part-months.
- WAPE = total error ÷ total demand.

| Method | Error (WAPE) | Bias |
|---|---|---|
| **Machine-learning model (global LightGBM, weighted average of 5)** | **28.8%** | −4.5% |
| Recent level × the part's month-of-year pattern | 35.5% | −1.4% |
| Same month, average of 2 years | 36.8% | −5.3% |
| Same month last year | 40.6% | −1.8% |
| Croston / TSB (textbook intermittent-demand methods) | 51.5% / 51.6% | −10% / −7% |
| Average of the last 12 months | 52.0% | −1.5% |

**The model.**
- **One model learns from all series at once.** It sees about 2.4 million examples, each made of what was known at a
  forecast origin plus what customers asked for 1–19 months later.
- **It predicts demand relative to each part's recent level**, scored by absolute error weighted by that level, which
  is exactly what WAPE rewards.
- **Its 63 inputs:**
  - recent history and order sizes
  - ordering rhythm, demand class, and how often the target's calendar month had an order
  - month-of-year pattern, the same month 1–3 years earlier and the months either side of it
  - holidays by exact date (Lunar New Year and Ramadan move each year)
  - known events: price rises, customer shutdowns
  - product lifecycle
  - region, family, customer and product growth
  - the economy 1–3 months earlier
- **It never sees the future:** this is enforced in code and tested.
- **Five versions are averaged.** The main one (70% of the weight) uses the Tweedie loss, which suits sparse,
  lumpy sales; the other four (30%) aim at the typical month (three random seeds) or the average month.

**Results:**
- **It wins at all four forecast origins:** 33.3 / 28.4 / 26.5 / 27.6% against 41.7 / 35.0 / 33.0 / 32.9%.
- **It wins in every demand class:** smooth 28% vs 34%, intermittent 30% vs 36%, erratic 30% vs 54%, lumpy 43% vs 71%.
- **Middle East & Africa,** a young and growing region, improved most: 50.2% → 39.6%.
- **Most of the remaining error is order size, not timing.** A forecaster that knew exactly which months have orders
  would still be 32.5% off.
- **How the final set-up was chosen.** Every change was scored on the same backtest rows, with any weight chosen on
  earlier years only. New inputs took the error from 29.5% to 29.1%; averaging four models brought it to 29.0%;
  adding a Tweedie-loss model (70% of the weight, chosen on earlier years) to 28.8%, and the bias from −6.3% to
  −4.5%. More training dates, bigger trees, a customer ordering-calendar input and working days did not help.
- **Every common alternative was tried on the same rows** (`experiments/zoo.py`): XGBoost 29.9%, CatBoost 30.0%,
  N-HiTS 32.1%, N-BEATS 33.4%, Chronos-2 34.7%, TimesFM 2.5 36.6%, AutoETS 38.6%, AutoARIMA 39.0%, TFT 49.5%,
  Croston / ADIDA / IMAPA 51%; Chronos-2 with covariates and fine-tuned on this data 33.6%. Stacked with the model using
  weights chosen on earlier years, none improves it by more than 0.1 points.
- **It generalises.** Out of time, it beats the best simple method in every family (7/7), region (6/6), month ahead
  (19/19), forecast date (4/4), channel (4/4) and customer (12/12), on 94.5% of individual series, and on MAE and RMSE
  as well as WAPE (RMSE 25.3 vs 30.2 units), so the ranking does not depend on the scoring metric.
- **Tested and rejected:**
  - capping one-off orders and down-weighting COVID months (slightly worse)
  - a squared-error model on its own (less biased, but more error)
  - blending with the best simple method: with the weight chosen on earlier years only, the best mix is 100% model

**Accuracy where planners plan.** Errors in different parts cancel out in totals:

| Level | Model (expected) | Best simple method |
|---|---|---|
| One part, one region, one month | 29.2% | 35.5% |
| Family × region, per month | 9.3% | 13.3% |
| Family, per month | **7.3%** | 10.0% |
| Region, per month | 6.1% | 10.2% |
| Whole company, per month | **4.6%** | 7.5% |

**Uncertainty:**
- **Each forecast has a low (P10) and high (P90) value.** In the backtest, 10.2% of months fell below P10 and
  11.8% above P90; the target is 10% each.
- **Ranges for totals.** Adding up P90s over-buys: 100% of family totals fall inside, so the range is useless.
  Treating parts as independent under-covers: 21%. A calibrated blend puts **80%** of family totals inside their
  80% range.
- **"Typical" vs "expected."** The typical-month forecast runs about 4.5% below the average month for lumpy
  demand.
  - **The submission** keeps the typical value per row (best per-row error).
  - **Totals** use expected = typical × 1.05, which cuts the company-total error from 6.0% to 4.6%.
- **Forecasts add up across the hierarchy.** Family, region and company figures are sums of the part-level
  forecasts, so every level agrees with every other.

**Products and events in the forecast window:**
- **New products** (PMP-020, PTC-029, THM-012) start from the part they replace. They ramp from about 17% of its
  volume in month 1 to 100% by month 6, learned from 6 past replacements.
- **Retiring products** halve once their successor launches and are 0 after end of life (156 rows).
- **The Orion Driveline North America shutdown (Jul–Aug 2027)** is forecast at 985 and 1,442 units against about
  3,800 in a normal month. The model learned this from the 2023 Nexon strike; no hand-written rule.

**The forecast:**
- 1.91 M units expected in the next 12 months (+11.6%, 80% range 1.72–2.11 M)
- 2.99 M units over 19 months (80% range 2.69–3.29 M)
- about **$2.68 B** revenue at the announced 2027 (+4.5%) and 2028 (+4.0%) prices

**What if the economy changes?** We re-ran the saved models with each indicator moved for the whole window:

| Scenario | Change in 19-month demand |
|---|---|
| Factory index (PMI) 3 points lower | −2.9% |
| Auto production growth 3 points lower | −3.0% |
| New vehicle orders 5% lower | −0.1% |
| All three together | −6.6% |

A downturn hurts more than an upswing helps: PMI 6 points lower takes 4.8% off; 6 points higher adds 2.6%.

## 5. What a planner should do
Each finding is computed from the outputs by `celeris/insights.py` and shown on the dashboard's first page with a
button that opens its evidence.

| Finding | Number | Action |
|---|---|---|
| Middle East & Africa grows fastest | +14% next 12 months | Check capacity and stock there first |
| Orion Driveline retools in Jul–Aug 2027 | about −68% in those months | Cut production for Orion North America; orders return in September |
| The chip shortage hid real demand | 79k units (shipments −34%) | Never plan from shipments in a shortage period |
| Revenue lost to stockouts | about $82M; 67% from chip allocation | Secure supply for the parts chip allocation hit most |
| Three products hand over to successors | PTC-005, PMP-003, THM-001 | Schedule changeovers; stop buying parts for old products before end of life |
| Plan budgets at family level | 7.3% error per family-month | Budget on family totals; cover single parts with P90 |
| New vehicle orders move first | 2–3 months ahead of Electrical | Watch the index monthly as an early warning |
| The move to electric has levelled off | 9.9% of demand | No extra EV-only capacity needed in this window |
| No early-buying rush before the 2027 rise | recent rises of 3.7–4.5%: no effect | No extra stock for a pre-buy rush in Nov–Dec 2026 |
| Summer shutdowns, every year | −44% (NA July), −54% (EU August) | Plan lower output and deliveries in those months |
| Parts ordered with gaps run below forecast | −7% bias (intermittent) | Plan those from expected or P90, not the typical month |
| Nexon Motor Group is losing accounts | lost 4, won 1 | Review the relationship; the forecast follows each account's owner |

## 6. The dashboard
**https://celeris-control-tower.vercel.app**: one app with the same filters everywhere (family, part, region,
channel, customer). History flows into the forecast on one timeline, with the range shaded and numbered pins on the
events that moved demand. It reads only the pipeline's output files, and a test checks that its totals match them.

- **5 required views:** executive overview, data health, demand explorer, anomaly monitor, forecast & accuracy.
- **All 7 optional views:** stockouts & lost sales, launches & end of life, what-if, procurement plan, exception
  list, economy lead/lag, downloads. Plus **Replay the past**.
- **Things to play with:**
  - *Replay the past:* pick a past date, press Reveal, watch what really happened draw in against the forecast made then.
  - *What-if sliders* for PMI, new vehicle orders and auto production, with presets.
  - *Service level:* plan for 50–99%; see the buffer, its dollar value and the chance of running short.
  - *Accuracy* as error or bias, split by months ahead, family, region, demand class or forecast date.
  - *Finder* (`/`) for any product, customer or region, and links that keep the filters.
- **Tested in a browser:** every view is opened in light and dark themes and at phone width, with no errors.

**How a planner uses it:**
1. Read the findings and check the exception list.
2. Open a series' forecast and range.
3. Plan purchases with the service-level slider: at 90%, about +10% over expected (304k units over 19 months).
4. Test what-if scenarios.

## 7. Limits and next steps
**Limits:**
- Middle East & Africa is still the hardest region (39.6% error).
- Ranges for totals are tuned on family totals; other levels land at 71–93% instead of 80%.
- The submission uses the typical value per row. If bias is also scored, switch to `expected` with one line in
  `run.py:step_submit`.
- The what-if sliders multiply single-indicator effects (−5.9% for the three together, against −6.6% from one run).
- The data is synthetic. Patterns as clean as the 1/2/3-month ordering rhythm may be weaker in real ERP data.

**Next:**
- tune ranges per level
- model each customer's order timing and size separately
- retrain monthly; the pipeline already runs in one command

## Reproduce
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
unzip <competition data zip> -d data
.venv/bin/python run.py            # all steps (model steps: 30–45 min on a laptop)
CELERIS_WORKERS=6 .venv/bin/python run.py    # or in parallel on a bigger machine (~23 min on a 32-core server)
.venv/bin/python -m pytest         # 118 checks
```
The pipeline was run on two machines (a laptop and a Linux server): the scores were identical, and 120 of 58,219
submission rows differed by ±1 unit from rounding differences between chip types. The final model steps ran on the
server.
