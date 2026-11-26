# The forecasting model

## Set-up — `celeris/model.py`
- **One global LightGBM model** for all ~3,000 series, using the direct multi-horizon approach: horizon is a
  feature, and one model covers months 1–19.
- **Training rows:** every series, from an origin every second month since Dec 2019 (plus the backtest origins), for
  each of the next 19 months. That gives about 2.4 M rows with the 63 inputs from `celeris/features.py`.
- **Target:** demand ÷ the series' recent level (last-12-month average, minimum 1).
  - Sample weight = level × the training weight from the demand rebuild (0 for estimated months), so minimising the weighted absolute error of the ratio is the
    same as minimising absolute error in units: the model optimises WAPE directly.
- **The typical forecast (P50) is a weighted average of five models** (`MEMBERS`):
  - one with the Tweedie objective (variance power 1.2), weight 0.7: the usual choice for sparse, lumpy sales;
  - three with the absolute-error (L1) objective and different random seeds / column and row sampling, and one with
    the squared-error (L2) objective, weight 0.075 each.
  - The 0.7 weight was chosen in the backtest on earlier origins only (see "Ensembles, loss functions…" below).
- **Settings:** 600 trees, learning rate 0.05, 63 leaves, at least 200 rows per leaf, 70–90% feature and row sampling.
- **No peeking:** the model for origin O trains only on rows with origin < O and target ≤ O. This is asserted in code.
- **Ranges:** two more models with the quantile objective (α = 0.1 and 0.9) give P10 and P90. Each row is sorted so
  that P10 ≤ P50 ≤ P90.
- **Speed:** `CELERIS_WORKERS=6` trains the replay dates (and the final models) in parallel processes. Results are
  identical to the one-at-a-time run (checked); a 32-core Linux server does the full model steps in about 23 minutes.

## Results — same backtest as the baselines (4 origins × 19 months, the same 218,063 rows)

| Method | WAPE | Bias |
|---|---|---|
| **LightGBM, weighted average of 5 models (final)** | **0.288** | −4.5% |
| LightGBM, average of 4 models (previous) | 0.290 | −5.1% |
| LightGBM, single model (first version) | 0.295 | −6.3% |
| Level × month-of-year pattern (best baseline) | 0.355 | −1.4% |
| Same month last year (v0) | 0.406 | −1.8% |

- **The model wins at every origin:**

  | Origin | Model WAPE | Best baseline WAPE |
  |---|---|---|
  | 2022-03 | 0.333 | 0.417 |
  | 2023-03 | 0.284 | 0.350 |
  | 2024-03 | 0.265 | 0.330 |
  | 2025-03 | 0.276 | 0.329 |

- **Middle East & Africa:** WAPE 0.502 → 0.396, still the hardest region.
- **Range calibration:** actual demand fell below P10 in 10.2% of rows and above P90 in 11.8% (target 10% / 10%).
- **By demand class** (class measured at each origin): smooth 28.2% (best simple 34.3%), intermittent 30.0% (35.6%),
  erratic 29.9% (53.8%), lumpy 43.4% (70.6%). The model wins in every class.
- **What the model relies on (gain share, weighted over the five models):** same month last year 36%, month-of-year
  pattern 18%, the month before last year's 13%, how often the target's calendar month had an order in the last 3
  years 11%.

## Does it generalise?
Every check below is out of time: each forecast was made with only the data known at its origin.

| Check | Result |
|---|---|
| Beats the best simple method, by family | 7 of 7 (smallest gain 5.5 points) |
| … by region | 6 of 6 (Middle East & Africa, the hardest, 39.6% vs 50.2%) |
| … by months ahead | 19 of 19 (error grows from 25.4% at 1 month to 30.1% at 19) |
| … by forecast date | 4 of 4 (33.3 / 28.4 / 26.5 / 27.6%) |
| … by channel, by customer | 4 of 4, 12 of 12 |
| … by demand class | 4 of 4 |
| Series where the model has the lower error | 94.5% (93.2% of volume) |
| Other error measures | MAE 13.6 vs 16.7 units, RMSE 25.3 vs 30.2: the ranking does not depend on the metric |
| Bias at the newest origin (Mar 2025) | −0.2% |

- **No look-ahead:** a test changes every month after the origin and checks that no input moves; the training rows
  for origin O all have targets ≤ O.
- **No tuning on the test:** the final recipe was fixed before scoring the mixes, and choosing on earlier origins only
  picks the same kind of mix (see below).

## First experiments — `experiments/model_variants.py`, `experiments/results_2026-11-24.json`

| Variant | WAPE | Bias | Bias by origin |
|---|---|---|---|
| L1, first feature set | 0.2996 | −5.6% | 0 / −9 / −12 / 0% |
| + one-off orders capped, COVID/strike down-weighted | 0.3043 | −3.7% | +8 / −9 / −12 / 0% |
| L2 (aims at the mean) | 0.3104 | +2.0% | +15 / −4 / −7 / +5% |
| **L1 + group growth and economy-vs-level-window features** | **0.2946** | −6.3% | −6 / −7 / −9 / −3% |

- **Capping and down-weighting did not help**, so they were left out.
- **The bias swings come from market-wide growth.** A single series cannot see it. Group growth (region, family,
  family-region) and the economy at the target month compared with the window the level is measured over fixed it.

## Feature and ensemble upgrade — `experiments/sweep.py`, `experiments/results_2026-11-25.json`
Eleven variants, each scored on the same 218,063 backtest rows. The first is the first-version model run again, to
show the test did not change.

| Variant | WAPE | Bias |
|---|---|---|
| First-version model re-run | 0.2946 | −6.3% |
| **+ 14 new inputs** (demand class, order sizes, timing around last year's month, customer and product growth) | **0.2912** | −6.3% |
| new inputs, other seeds / sampling (3 variants) | 0.2918–0.2923 | −6.7 to −7.1% |
| new inputs, every month as a training origin | 0.2919 | −6.8% |
| new inputs, bigger trees (1,500 × 127 leaves) | 0.2913 | −6.2% |
| new inputs, squared error (L2) | 0.2977 | −0.5% |
| new inputs, Huber / 55th percentile | 0.2955 / 0.2925 | −2.5% / −2.7% |
| **Average of 3 × L1 + 1 × L2 (chosen at this stage)** | **0.2904** | −5.2% |

- **The recipe was fixed before looking at the 162 possible mixes:** seed averaging plus one average-month model.
- **Check against luck:** choosing the best mix on earlier origins only and scoring it on the next one picks a very
  similar mix (3 × L1 + L2) and scores the same as the fixed recipe (28.7 / 26.6 / 27.9% vs 28.8 / 26.7 / 27.9%).
- **Not used:** more origins and bigger trees (no better, 2–3× slower); Huber and the 55th percentile (less bias but
  more error).
- **The full pipeline at this stage** (which retrains everything, including the quantile models) scored 0.290, bias −5.1%.

## Comparison with other approaches — `experiments/zoo.py`, `experiments/results_model_comparison.csv`
Widely used and recent forecasting approaches, each scored on exactly the same 218,063 backtest rows as the chosen
model. Univariate methods see each series' rebuilt demand up to the origin; XGBoost and CatBoost use the same features
and training rows as LightGBM. Rows a method cannot forecast (series with under 3 months of history, 0.5% of rows)
take the best simple baseline's value.

| Approach | Method | WAPE | Bias | RMSE |
|---|---|---|---|---|
| **Gradient boosting (chosen)** | **LightGBM global model, weighted average of 5** | **28.8%** | −4.5% | 25.3 |
| Gradient boosting | XGBoost (same features) | 29.9% | −2.1% | 26.0 |
| Gradient boosting | CatBoost (same features) | 30.0% | −7.5% | 26.0 |
| Deep learning (global) | N-HiTS | 32.1% | −7.6% | 27.4 |
| Deep learning (global) | N-BEATS | 33.4% | −4.9% | 28.7 |
| Deep learning (global) | TFT | 49.5% | −15.7% | 45.9 |
| Pretrained, zero-shot | Chronos-2 | 34.7% | −5.8% | 29.0 |
| Pretrained, zero-shot | TimesFM 2.5 | 36.6% | −11.2% | 31.2 |
| Pretrained, zero-shot | Chronos-Bolt | 47.7% | −12.7% | 41.2 |
| Statistical (per series) | AutoETS | 38.6% | +0.5% | 32.3 |
| Statistical (per series) | AutoARIMA | 39.0% | +3.2% | 32.7 |
| Statistical (per series) | AutoTheta | 49.0% | +5.8% | 40.0 |
| Intermittent demand | Croston (optimised), ADIDA, IMAPA | 51.4–51.5% | −3% | 41.3–41.5 |

- **Combinations:** LightGBM mixed with each of these, with the weight chosen on earlier origins only and scored on the
  next one. The chosen weight is 0 for every method except N-HiTS (0.1 at two origins, gain 0.04 points) and XGBoost
  (worse). None is worth the extra dependencies, so the pipeline keeps LightGBM alone.
- **Why gradient boosting wins here:** the inputs carry what univariate and pretrained models cannot see: each
  customer's ordering rhythm, the demand rebuilt around stockouts, known events in the window (the Orion shutdown,
  price rises), lifecycle dates and group growth.

## Ensembles, loss functions, fine-tuned foundation model — `experiments/advanced.py`, `experiments/results_advanced.json`
All on the same backtest rows; any weight or setting is chosen on earlier origins only and scored on the next one.

| Check | Result |
|---|---|
| Equal-weight average of LightGBM, XGBoost, CatBoost | 29.2% (the LightGBM ensemble alone: 29.0%) |
| Stacked weights over LightGBM, XGBoost, CatBoost, N-HiTS, N-BEATS, Chronos-2, TimesFM | within ±0.1 points of LightGBM alone at every test origin |
| Chronos-2 with calendar, holiday, shutdown and price-rise covariates | 34.5% zero-shot |
| Chronos-2 fine-tuned on this data (LoRA, 2,000 steps per origin) | 33.6% (28.9% at the newest origin, LightGBM 27.7%) |
| New inputs: the customer's ordering calendar across its parts, working days per month | 29.24% vs 29.12% without: not used |
| **LightGBM with the Tweedie loss** (variance power 1.1 / 1.2 / 1.3 / 1.4 / 1.5) | 28.95 / **28.86** / 28.98 / 29.04 / 28.96% |
| **Previous ensemble + Tweedie 1.2, weight chosen on earlier origins** | weight 0.6–0.7 every time; 28.45 / 26.46 / 27.55% at the 2023 / 2024 / 2025 origins vs 28.75 / 26.60 / 27.74% |

The Tweedie loss fits sparse, lumpy sales better than absolute or squared error (it is what the M5 winners used), and
it is the only change in this round that improved every test origin. The pipeline now uses a 0.7 / 0.3 weighting of
the Tweedie model and the previous four-model average (`celeris/model.py`, `MEMBERS`).

## Median vs expected
- **`submission.csv` uses P50**, which gives the best row-by-row WAPE.
- **`forecast.csv` also has `expected` = P50 × 1.0468.** The factor is the ratio of actual to forecast over all
  backtest rows. This version is the one to use for totals (dashboard, procurement, revenue): medians of single months
  add up to less than the expected total.
- **If bias turns out to be part of the score,** the submission can use `expected` instead. It is a one-line change in
  `run.py:step_submit`.

## The real forecast (origin Oct 2026)
- **Next 12 months:** 1.91 M units expected (80% range 1.72–2.11 M), against 1.71 M in the last 12 months (+11.6%).
  19 months: 2.99 M units expected, about $2.68 B revenue at 2027–28 list prices.
- **Orion Driveline North America, Jul–Aug 2027:** 985 and 1,442 units, against about 3,800 in a normal month. The
  model learned this from the 2023 Nexon strike through the `customer_shutdown` feature; there is no hand-written rule.
- **Lifecycle rules are applied to P10, P50 and P90:**
  - new SKUs ramp up from their predecessor's level
  - replaced SKUs wind down to half from their successor's launch
  - everything is 0 after end of life

## Accuracy at planning levels (`run.py insights`, `outputs/reports/accuracy_by_level.csv`)

| Level | Model expected | Model P50 | Best simple |
|---|---|---|---|
| Series-month | 29.2% | 28.8% | 35.5% |
| Family × region-month | 9.3% | 9.8% | 13.3% |
| Family-month | 7.3% | 8.0% | 10.0% |
| Region-month | 6.1% | 7.1% | 10.2% |
| Company-month | 4.6% | 6.0% | 7.5% |

This confirms the P50-for-rows, expected-for-totals split.
- **Blend with the best baseline** (`blend_test.json`): with the weight picked on earlier origins only, the model
  alone wins at every test origin.
- **Hierarchy:** every total (family, region, company) is the sum of the series forecasts, so the levels always agree.

## Known limits
- **Ranges for totals:** summed P10/P90 are far too wide for totals; aggregate ranges use the calibrated ρ blend
  (`celeris/ranges.py`): 80% of family-month totals inside their 80% range (region 86%, family-region 71%, company 93%).
- **Run time:** on a 16 GB laptop the model steps take 30–45 minutes one at a time; on a 32-core server, about 23.
