# Celeris Control Tower: the dashboard

**Live:** https://celeris-control-tower.vercel.app (public, no login). It is the same files as `dashboard/`.

## Run it locally
```bash
.venv/bin/python run.py export                       # writes dashboard/data/*.json from the pipeline outputs
.venv/bin/python -m http.server 8000 -d dashboard    # then open http://localhost:8000
```
Nothing to install beyond Python: ECharts is stored in `dashboard/vendor/`. Fonts load from Google Fonts when online
and fall back to system fonts offline. `dashboard/docs/` holds the report PDF that the app links to.

**Deploy** (static files, no build step): `cd dashboard && vercel deploy --prod`. `.vercelignore` keeps scripts and
local settings out; `vercel.json` sets caching for `data/`.

## Design
- **One app, two parts, shared filters.** Family, product, region, channel and customer sit in one bar and apply to
  every view. A filter in use turns blue. The sidebar groups the views into *What happened* (training data), *What
  will happen* (prediction) and *Plan*.
- **One continuous timeline.** History (blue: what customers asked for, rebuilt where stock ran out) runs into the
  forecast (orange: expected; dashed: typical month P50; band: 80% range P10–P90). A "Today" line marks Oct 2026.
  Numbered pins mark the events that moved demand (COVID, chip shortage, Nexon strike, Orion retooling in 2027, …);
  hover a pin to read it, and "Show only this" filters to its scope.
- **Reads pipeline outputs only.** `celeris/export.py` turns the outputs into six JSON files; the browser filters and
  adds up any selection itself. Even the findings on the overview are written by `celeris/insights.py:findings`
  from the outputs. `tests/test_dashboard.py` checks the totals against the pipeline.
- **Shareable state.** The address bar holds the view and filters (`#forecast?fam=Hydraulic%20Systems&reg=India`), so
  "Copy link" shares exactly what is on screen.
- **Finder and keys.** `/` opens a finder for products, customers, regions, families and views; `?` opens the
  reading guide; `t` switches light / dark (light is the default; the choice is remembered in the browser).
- **Type:** Archivo for headings and headline numbers, Public Sans for text, JetBrains Mono for numbers in tables.
- **Colour has one meaning each:** blue = history, orange = forecast, red / green = worse / better, violet =
  estimated data. Dark mode has its own colour steps. Categorical colours come from a palette checked for
  colour-blind readers.

## Views
The menu follows the brief: **Part 1 · Training data** (what happened) and **Part 2 · Prediction** (what will
happen); the five required views are marked "required", and the seven optional views use the brief's names. The
**All / Part 1 / Part 2** switch above the menu shows only the views for one part of the brief.

| View | Brief | Shows |
|---|---|---|
| Executive overview | required | Headline 19-month forecast (units and revenue); units **and** revenue from 2019 into the forecast on one timeline, with range and event pins; mix by family, region and channel; 12 findings, each with a number, an action and a link to its evidence |
| Brief checklist | – | Every requirement of the problem statement (5 workstreams × 3 tiers, 7 business rules, hand-in), how it is met and a link to where it is shown; counts read from the data |
| Data health | required | Issues by type and by file with rows affected; before vs after cleaning; the stockout answer key; where rebuilt demand comes from; every issue with evidence and before → after examples |
| Demand explorer | required | Drill-down family → product → region → customer with lifecycle per row; asked for vs shipped; seasonality; seasonality by region; zero months; ordering rhythm; demand classes; ABC volume classes; order calendar |
| Anomaly monitor | required | Counts by kind; flagged points on the demand line with reasons; anomalies by type; the register with type, reason and action; accounts that changed hands |
| Stockouts & lost sales | optional | Lost units by month and cause, back-orders, fill rate, hidden shortage, worst series |
| Forecast & accuracy | required | Forecast vs history with P10/P50/P90; backtest WAPE **and** bias by family, region and months ahead (all shown at once); splits by forecast date and demand class; error at each planning level; every method tried (21, incl. statistical, deep-learning and pretrained models); what the model uses |
| Backtest replay | – | Pick a forecast origin, see the forecast made then, reveal what happened, compare with the best simple method |
| Lifecycle: EOL & launches | optional | The three replacements as old → new charts, products that are not simply active, the EV shift |
| What-if scenarios | optional | Sliders for PMI, new vehicle orders and auto production re-scale the forecast from saved model runs; presets; impact by family; sensitivity |
| Procurement plan (P90) | optional | Service-level slider (50–99%): plan, buffer, buffer value, chance of running short; cost-of-safety curve; plan table and CSV |
| Exception list | optional | Series to check first, filterable by reason |
| Macro lead / lag heatmap | optional | Indicator × family heatmap with stability; lag profiles; indicators over time; measured effect of every event |
| Data download | optional | Forecast, submission-format, history and anomaly CSVs for the current filters; the report |

All 7 optional views from the brief are built; the Expert level asks for 5 + 4, deployed or polished.

## Ranges for totals — `celeris/ranges.py`
Per-series P10/P90 are calibrated in the backtest. For a total, neither simple approach works:

| Approach for family-month totals | Share inside the 80% range |
|---|---|
| Treat series as independent | far too narrow |
| Add up the P10s and P90s | 100% (far too wide) |
| **Blend: σ_total = √((1−ρ)·Σσ² + ρ·(Σσ)²), ρ fitted in the backtest** | **about 80%** |

The dashboard uses this blend for every total, including the procurement plan. The service-level slider moves the
plan to expected + z·σ_total, where z is the normal quantile of the chosen level (z = 1.28 at 90%).

## What-if — `celeris/scenarios.py`
The saved P50 models are re-run with one indicator moved for the whole forecast window, at 4 steps each (PMI ±3/±6
points, new vehicle orders ±5/±10%, auto production growth ±3/±6 points). The export keeps, per series, the
19-month ratio to the base forecast, and per family the monthly shape. The sliders interpolate between steps and
multiply when several move. A check run moves all three together in one model run and compares it with the
multiplied single effects (`outputs/reports/scenarios.json`, `combination_check`): −6.6% against −5.9% for the
"mild slowdown" preset, close enough for a planning slider.

## Testing
- `tests/ui_check.py` opens every view in a headless browser (light and dark, plus filtered states) and fails on any
  JavaScript error or a view that draws no chart. Screenshots go to `outputs/ui/`.
- `tests/ui_interactions.py` clicks through the controls (filters, product search, finder, Part 1 / Part 2 switch, help, theme, replay,
  what-if presets, service-level slider, CSV download, shared links) and checks each result.
- `tests/test_dashboard.py` checks the data files against the pipeline outputs, that every finding carries a number
  and links to a real view, that the what-if grid is complete, and that the code reads nothing but the data files.
