# How the data was cleaned

**In one line:** 248,248 messy rows became 244,566 clean rows (one per month × product × region). About 7,700 rows
needed a correction, and every correction was checked against a second, independent source before being applied.

| | Before | After |
|---|---|---|
| Rows | 248,248 | 244,566 |
| Ways the date was written | 667 | 94 (one per month) |
| Ways the product code was written | 2,659 | 598 |
| Region names | 31 | 6 |
| Sales channel names | 17 | 4 |
| Customer names | 220 | 12 |
| Biggest single order | 16,300 units | 2,002 units |
| Total units | 12.22 M | 11.72 M |
| Total revenue | $8.82 B | $8.58 B |

Units drop by 4% and revenue by 3% because some quantities were typed 10× or 100× too large, and some revenue
figures were 10× too large.

---

## How we decided what was an error

We never "fixed" something just because it looked odd. For each suspected error we looked for a **second source that
tells the truth**, and only then wrote a rule:

- **Revenue ÷ price** tells you the real number of units.
- **The stockout file** lists the exact units shipped for 5,830 product-region-months.
- **The load timestamp** shows that every row was loaded exactly one month after its sales month.
- **The SKU master** gives the official family, business unit and description.
- **The test file** gives the official customer and channel for every series.

Final check: for the 5,762 stockout-file rows we did not use as a source, cleaned units match the shipped units
**100%** of the time (before cleaning: 98.6%).

---

## The fixes, one by one

### Duplicates
- **2,950 rows** were loaded twice, identical. Removed the copy.
- **732 rows** were loaded twice with different units. Kept the later load (business rule 2).

### Dates
- **9,821 rows** used other date formats (`Jan-2019`, `01/2019`, `2019-01-01` …). Converted to `2019-01`.
- **30 rows** said `2062` instead of `2026`. The Year column says 2026 and the load timestamp confirms it.
  This is the one place where we did not trust the Date column, because a date in 2062 is impossible.
- **246 rows** had a Year/Month one month later than Date. Date wins (business rule 1), and the load timestamp agrees.

### Names and codes
- Product codes like `THM008`, `HYD_006`, `PMP-55`, ` BSS-6` → `THM-008`, `HYD-006`, `PMP-055`, `BSS-006`.
- Regions: `APAC`, `EU`, `LATAM`, `MEA`, `india ` … → the 6 official names.
  ⚠️ `NA` means **North America**. Most tools read `NA` as "missing", so we load the files as plain text.
- Channels: `OEM Direct`, `T1 Distributor`, `After-market` … → the 4 official names.
- Customers: `HELIOS AUTO PARTS`, `Vantara Auto Inc.`, `Solaris Auomotive` … → 12 customers.
  All 208 mappings were reviewed by hand. For every product-region still sold, the cleaned customer and channel
  match the test file exactly.
- **737 blank regions** were filled in. For each row, exactly one region existed where the same product, customer
  and channel had no row that month.
- Blank product family (1,228 rows) and description (982 rows) were filled from the SKU master.

### Prices
Each product has one list price per year, the same in every region (business rule 4). 99% of rows show exactly that
price, so finding it is easy. The rest:

| What we saw | Rows | What it really was | Fix |
|---|---|---|---|
| Price 100× too high | 614 | Price typed in cents | ÷ 100 |
| Price 10× too high, **India, Jul–Dec 2021 only** | 385 | Price per **pack of 10**, with units also counted in packs | Price ÷ 10, units × 10 |
| Price exactly 0.89× list, **Europe 2022 only** | 498 | Booked in euros, not dollars | Use the USD price |
| Price 0 but units shipped | 295 | Price missing (units confirmed by stockout file) | Use the list price |

Not errors, and kept as they are:
- **Yearly price rises** of +2.0, +2.5, +5.0, +9.0, +4.5, +3.7 and +3.7% (2020–2026) match the events calendar.
- **A 6% surcharge** applied July–December 2022.

### Units
| What we saw | Rows | Fix | How we know |
|---|---|---|---|
| Blank, but revenue present | 2,545 | revenue ÷ price | Always a whole number; matches the stockout file 70/70 |
| `-` | 217 | 0 | Price and revenue are 0 too, which is how "no order" is recorded |
| Blank with price 0 and revenue 0 | 450 | 0 | Same "no order" pattern |
| Negative (revenue negative too) | 491 | Flip the sign | Their size is normal for the series, so they aren't returns |
| 10× / 100× too large (e.g. `8,200` for 82) | 293 / 75 | revenue ÷ price | Matches the stockout file; revenue ÷ price is normal-sized |
| Packs of 10 (India 2021) | 385 | × 10 | ~0.1× the series' normal size; matches the stockout file |
| Blank, revenue blank too | 332 | 1 recovered from the stockout file; **331 left missing** | A missing value is not a zero (business rule 3) |

### Revenue
Revenue is always recomputed as units × price. The reasons it was wrong before:
- 425 rows ×10
- 309 rows with two digits swapped (`23683.14` instead of `26383.14`)
- 498 rows in euros
- 295 rows with zero revenue on a real sale
- the knock-on effect of the unit fixes above

### Other files
- **Macro indicators:**
  - June 2022 appeared twice. We kept the row whose values sit between May and July.
  - A PMI of `4.79` → 47.9.
  - 2019 auto-production growth was stored as a fraction (−0.015 instead of −1.5%) → × 100.
  - 5 blanks were filled from neighbouring months.
  - The 17% raw-material drop in Jan 2022 is **real**, because the new level continues in the following months.
- **SKU master:**
  - PMP-004 is filed under the wrong business unit.
  - BSS-018 starts selling in Feb 2025 but the master says it launched in Jun 2025.
- **Stockouts:**
  - The `Fill_Rate_Pct` column is a planning estimate (it tracks stockout days), not shipped ÷ requested. We
    recalculate it.
  - 1,070 back-orders have no follow-up month in the file, which confirms the file is incomplete.

---

## What the pipeline produces
`python run.py` writes:
- `outputs/clean/sales_clean.parquet` / `.csv` — the clean history. Each row keeps its original line number and
  says what was changed (`units_status`, `price_status`, `revenue_status`, `fix_flags`).
- `outputs/reports/data_quality.json` / `.csv` — every issue: file, column, category, rows affected, action,
  evidence and before → after examples.
- Clean copies of the macro, stockout, events, master and test files.

`pytest` runs 68 checks. We also confirmed the checks catch problems by breaking the data on purpose (wrong revenue,
unofficial region, a changed price): each break was caught.
