"""Paths and the reference lists every cleaning rule checks against."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "student_data"
OUT = ROOT / "outputs"
CLEAN = OUT / "clean"
REPORTS = OUT / "reports"

HISTORY_START, HISTORY_END = "2019-01", "2026-10"
FORECAST_START, FORECAST_END = "2026-11", "2028-05"

REGIONS = ["North America", "Europe", "Asia Pacific", "India", "Latin America", "Middle East & Africa"]
CHANNELS = ["Direct OEM", "Tier-1 Distributor", "Aftermarket", "Export"]

# Every spelling seen in train.csv, lower-cased and stripped -> official name.
REGION_ALIASES = {
    "north america": "North America", "n. america": "North America", "na": "North America",
    "europe": "Europe", "eu": "Europe",
    "asia pacific": "Asia Pacific", "asia-pacific": "Asia Pacific", "apac": "Asia Pacific",
    "india": "India",
    "latin america": "Latin America", "latin-america": "Latin America", "latam": "Latin America",
    "middle east & africa": "Middle East & Africa", "middle east and africa": "Middle East & Africa",
    "me&a": "Middle East & Africa", "mea": "Middle East & Africa",
}

# Channel spellings with case, spaces and hyphens removed -> official name.
CHANNEL_ALIASES = {
    "directoem": "Direct OEM", "oemdirect": "Direct OEM",
    "tier1distributor": "Tier-1 Distributor", "t1distributor": "Tier-1 Distributor",
    "aftermarket": "Aftermarket",
    "export": "Export",
}

# Jul-Dec 2022: temporary 6% raw-material surcharge on every invoice (events calendar EV-027).
SURCHARGE_MONTHS = ("2022-07", "2022-12")
SURCHARGE = 1.06
