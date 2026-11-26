"""Each cleaning rule tested on hand-made examples taken from the real file."""
import math

import pytest

from celeris.clean_sales import (is_digit_swap, make_customer_matcher, normalize_channel, normalize_region,
                                 normalize_sku, parse_month, parse_units)


@pytest.mark.parametrize("raw, expected", [
    ("2019-01", "2019-01"), ("2019/01", "2019-01"), ("2019-01-01", "2019-01"),
    ("01/2019", "2019-01"), ("01-2019", "2019-01"), ("Jan 2019", "2019-01"), ("Jan-2019", "2019-01"),
    ("Dec-2024", "2024-12"), (" Sep 2020 ", "2020-09"), ("2062-03", "2062-03"),   # 2062 is fixed later, not here
])
def test_parse_month(raw, expected):
    assert parse_month(raw) == expected


def test_parse_month_rejects_garbage():
    assert parse_month("soon") is None


@pytest.mark.parametrize("raw, expected", [
    ("HYD-018", "HYD-018"), ("THM008", "THM-008"), ("HYD_006", "HYD-006"), ("PMP-55", "PMP-055"),
    ("BSS-6", "BSS-006"), (" HYD-041", "HYD-041"), ("hyd-41", "HYD-041"),
])
def test_normalize_sku(raw, expected):
    assert normalize_sku(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("NA", "North America"), ("N. America", "North America"), ("north america", "North America"),
    ("APAC", "Asia Pacific"), ("Asia-Pacific", "Asia Pacific"), ("EU", "Europe"), ("EUROPE", "Europe"),
    ("India ", "India"), ("LATAM", "Latin America"), ("LatAm", "Latin America"),
    ("ME&A", "Middle East & Africa"), ("Middle East and Africa", "Middle East & Africa"), ("", ""),
])
def test_normalize_region(raw, expected):
    assert normalize_region(raw) == expected


def test_unknown_region_is_not_silently_accepted():
    assert normalize_region("Antarctica") is None


@pytest.mark.parametrize("raw, expected", [
    ("Direct OEM", "Direct OEM"), ("DIRECT OEM", "Direct OEM"), ("OEM Direct", "Direct OEM"),
    ("Direct-OEM", "Direct OEM"), ("T1 Distributor", "Tier-1 Distributor"), ("Tier 1 Distributor", "Tier-1 Distributor"),
    ("After-market", "Aftermarket"), ("AfterMarket", "Aftermarket"), ("Export ", "Export"), ("EXPORT", "Export"),
])
def test_normalize_channel(raw, expected):
    assert normalize_channel(raw) == expected


OFFICIAL = ["Apex Tier One", "Atlas Mideast Components", "Crestline Manufacturing", "Falcon Gulf Motors",
            "Helios Auto Parts", "Kestrel Mobility", "Meridian Assembly", "Nexon Motor Group", "Orion Driveline",
            "Quantum Drive Systems", "Solaris Automotive", "Vantara Auto"]


@pytest.mark.parametrize("raw, expected", [
    ("HELIOS AUTO PARTS", "Helios Auto Parts"), ("Helios  Auto Parts", "Helios Auto Parts"),
    ("Vantara Auto Inc.", "Vantara Auto"), ("Solaris Auomotive", "Solaris Automotive"),
    ("NexonMotor Group", "Nexon Motor Group"), ("Orin Driveline", "Orion Driveline"), ("Vntara Auto", "Vantara Auto"),
])
def test_customer_matcher(raw, expected):
    assert make_customer_matcher(OFFICIAL)(raw) == expected


def test_customer_matcher_rejects_unrelated_names():
    assert make_customer_matcher(OFFICIAL)("Totally Different Corp") is None


def test_parse_units():
    assert parse_units("57") == (57.0, "number")
    assert parse_units("9,600") == (9600.0, "comma")
    assert parse_units("-") == (0.0, "dash")
    assert parse_units("-35") == (-35.0, "number")
    v, kind = parse_units("")
    assert math.isnan(v) and kind == "blank"


def test_digit_swap():
    assert is_digit_swap(23683.14, 26383.14)
    assert is_digit_swap(2254.0, 20254.00)          # swapped zero dropped off the end
    assert not is_digit_swap(26383.14, 26383.14)    # identical is not a swap
    assert not is_digit_swap(26383.15, 26383.14)
