"""Reads the raw files as plain text so nothing is silently converted.

Why `keep_default_na=False`: pandas treats the text 'NA' as missing by default, but in this data
'NA' is a spelling of North America.
"""
import pandas as pd

from .config import RAW


def read_raw(name):
    return pd.read_csv(RAW / name, dtype=str, keep_default_na=False)


def load_all():
    return {n: read_raw(f"{n}.csv") for n in
            ["train", "test", "macro_indicators", "sku_master", "events_calendar", "inventory_stockouts",
             "sample_submission"]}
