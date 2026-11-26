# Rebuilding demand, the anomaly register, and event effects

**Why:** business rule 7 says to forecast what customers **ask for**, not what Celeris managed to ship. The sales
file records shipments. So before modelling, every month gets two numbers: `shipped` and `demand`.

Output: `outputs/demand/panel.parquet`. It has one row per series (SKU × region) per month, from the series' first
month to Oct 2026 (or to end of life). 245,538 rows: 3,083 series × their active months.

## How `demand` is set (the first rule that applies wins)

| # | Situation | Demand = | Months | Training weight |
|---|---|---|---|---|
| 1 | Month listed in the stockout file | `Requested_Units` (exact) | 5,830 | 1 |
| 2 | Month after a listed back-order | shipped − back-ordered units | 1,034 | 1 |
| 3 | Electrical Assemblies, Mar 2021 – Sep 2022, not listed | shipped × that month's shortfall ratio | 1,925 | 0.7 |
| 4 | Units unknown (missing row, ERP loss, constrained catch-up) | same point of the ordering cycle + same month in neighbouring years | 1,291 | 0 |
| 5 | Everything else | shipped | 235,458 | 1 |

Result: 11.89 M units of demand against 11.72 M shipped.

### Evidence for each rule
- **Rule 2:** in 141 months the shipment equals the previous month's back-order exactly, meaning the customer
  placed no new order that month. When a month shipped *less* than the back-order (14 cases), it was constrained
  too, so its demand is treated as unknown.
- **Rule 3:** during the chip shortage the stockout file lists about 70% of Electrical Assemblies months. The
  unlisted months were shipped at the same reduced level (82% of normal, against 102% for other families), so they
  were constrained too.
  - The ratio of requested to shipped units by month follows a V: ×1.11 in Mar 2021, ×2.08 in Sep 2021, back to
    ×1.11 in Sep 2022.
  - Tested by hiding 25% of the listed months: estimate error is 11.4% (WAPE), against 33.9% if left uncorrected.
    Scaling by region, by series history, or by pre-shortage level did no better.
- **Rule 4:**
  - **ERP loss:** the 331 rows with blank units *and* revenue are all in Latin America, Aftermarket + Export,
    Mar–May 2023. That matches the ERP migration in the events calendar (EV-033).
  - **Missing rows:** 946 months have no row at all. A missing row is not a zero (rule 3).
  - **How they are filled:** each series has an ordering rhythm. 2,480 series order monthly, 365 every 2nd month,
    238 quarterly. Filling a month from the same point of the rhythm scored 35% error on 4,000 hidden months. A
    plain average of the neighbouring months scored 54%, because it smears quarterly orders across empty months.

## Anomaly register — `outputs/reports/anomaly_register.csv`
18,770 entries, one per flagged series-month. Each entry has a `kind`, `type`, `reason` and `action`.

| Kind | Entries | Examples |
|---|---|---|
| data_error | 5,960 | Units 10×/100×, negative units, packs of 10, price in cents or EUR, swapped revenue digits |
| data_loss | 1,277 | ERP migration (331), months missing from the extract (946) |
| real_event | 11,517 | Stockouts, chip shortage, catch-ups, one-off bulk orders (547), COVID, Nexon strike, customer switches (23), ordering-rhythm changes (145), discontinued series (25) |
| master_data | 16 | BSS-018 sells 4 months before its master launch date |

**One-off orders** are measured against the customer's usual *order size* (rolling median of non-zero months),
not the usual month. The month-based rule flagged 1,720, mostly ordinary quarterly orders.

## Event effects — `outputs/reports/event_effects.csv`
Every calendar event is measured on demand and on shipments:
- **Holidays:** compared with regions that have no holiday in the same months.
- **Everything else:** the affected group is compared with *itself* in the same months of other years. This
  removes normal seasonality and recurring holidays.

A **placebo test** on 300 random event-free months sets the chance range. 95% of them move less than ±7.3% for the
whole company, ±11.7% for a region and ±17.7% for a customer. Effects inside that range are reported as
"no clear effect".

| Event | Demand effect |
|---|---|
| NA July retooling / EU August shutdowns | about −44% / about −54% every year |
| Lunar New Year (Asia Pacific) | −10% to −40% |
| COVID lockdowns (2020) | −16% (APAC, Feb–Mar) to −69% (India) |
| Nexon strike, Europe (Oct–Nov 2023) | −81% |
| Helios second Indian line (from Sep 2025) | +23%, permanent |
| Early buying before the 2023 price rise | +13% (Nov–Dec 2022), then −11% (Jan–Feb 2023) |
| Chip shortage (Electrical Assemblies) | shipments −37%, demand +10%: a supply problem, not a demand one |
| ERP migration (Latin America) | shipments −25%, demand −2% after the rebuild |
| Rebrand, trade fair, new sales director, dealer conference, most January price rises | no clear effect |

An earlier version compared regions with each other. It reported a +18% "new sales director" effect that was really
North America's July shutdown depressing the comparison group.

## Submission — `outputs/submission/submission.csv`
- **v0 baseline:** for each series, the same calendar month from the latest year of rebuilt demand.
- **Lifecycle rules:**
  - 0 after end of life (156 rows).
  - New SKUs ramp from about 17% of the predecessor's level in month 1 to 100% by month 6. This is the median of the
    6 replacements in the history.
  - A replaced SKU sells about half between its successor's launch and its own end of life.
- `celeris/submission.py:validate` checks:
  - columns match `sample_submission.csv`
  - row count and order match `test.csv`
  - values are whole numbers ≥ 0 with no blanks
  - nothing after end of life or before launch

  A test breaks the file five ways and confirms each one is rejected.

## Accounts (`run.py insights`, `outputs/reports/account_changes*.csv`)
- No customer was lost outright: every customer-region is within −7% to +19.5% of the previous year.
- 23 SKU-region accounts changed customer:

  | Customer | Won | Lost |
  |---|---|---|
  | Apex Tier One | 6 | 2 |
  | Quantum Drive Systems | 4 | 1 |
  | Nexon Motor Group | 1 | 4 |
  | Orion Driveline | 1 | 3 |

- In the 12 months after a switch, demand was a median 36% higher than the 12 months before.
