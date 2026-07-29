#!/usr/bin/env python3
"""
fetch_era5_temp.py

Pulls daily 2m temperature for the Kolopis point from Open-Meteo's Historical
Weather API (ERA5-Land reanalysis under the hood) and writes it in the exact
format build_climate_reference.py expects: date, temp_c CSV.

No account, no API key, no auth -- this is a plain HTTP GET. Endpoint and
parameters verified directly against Open-Meteo's docs
(https://open-meteo.com/en/docs/historical-weather-api) on 2026-07-29, not
guessed from memory.

Requires: pip install requests
"""

import csv
import time
from datetime import date, timedelta

import requests

# ============================================================================
# CONFIG
# ============================================================================
LAT, LON = 5.925840, 116.143360   # Kolopis / Kg. Sarapung
START_DATE = "1996-01-01"          # matches the rolling-recent 30yr window in
END_DATE = "2025-12-31"            # build_climate_reference.py -- keep these in sync
OUTPUT_CSV = "era5land_kolopis_daily.csv"
TIMEZONE = "Asia/Singapore"        # same convention used throughout the rest of your pipeline

# ERA5-Land is the default "Best Match" model for this endpoint at this
# resolution/location; being explicit here so a future Open-Meteo default
# change doesn't silently swap your data source under you.
MODEL = "era5_land"

# Requesting 30 years in one call. Open-Meteo markets sub-second responses
# even for decades of data, but if this ever times out or the connection
# drops, CHUNK_YEARS below lets you pull it in smaller pieces instead.
CHUNK_YEARS = None  # e.g. set to 5 to fetch in 5-year chunks if a single call misbehaves


def fetch_range(start_date, end_date):
    """One HTTP call, returns list of (date, temp_c) tuples."""
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "start_date": start_date,
        "end_date": end_date,
        # Request mean + max + min: mean is the primary target (documented
        # choice, matches build_climate_reference.py's expectation), but
        # max/min are in Open-Meteo's confirmed daily parameter table while
        # "mean" wasn't explicitly in that table (only implied by the
        # checkbox UI) -- so max/min are the safety net if "mean" ever
        # turns out unsupported for this specific endpoint/model combo.
        "daily": "temperature_2m_mean,temperature_2m_max,temperature_2m_min",
        "timezone": TIMEZONE,
        "models": MODEL,
    }
    resp = requests.get(url, params=params, timeout=60)
    if not resp.ok:
        raise RuntimeError(f"Open-Meteo request failed ({resp.status_code}): {resp.text[:500]}")
    data = resp.json()
    if "daily" not in data:
        raise RuntimeError(f"Unexpected response shape, no 'daily' key: {data}")

    daily = data["daily"]
    dates = daily["time"]
    means = daily.get("temperature_2m_mean")
    maxs = daily.get("temperature_2m_max")
    mins = daily.get("temperature_2m_min")

    rows = []
    for i, d in enumerate(dates):
        mean_val = means[i] if means else None
        if mean_val is None and maxs and mins and maxs[i] is not None and mins[i] is not None:
            mean_val = round((maxs[i] + mins[i]) / 2, 1)  # fallback if 'mean' unsupported
        if mean_val is not None:
            rows.append((d, mean_val))
    return rows


def main():
    all_rows = []

    if CHUNK_YEARS:
        start_year = int(START_DATE[:4])
        end_year = int(END_DATE[:4])
        y = start_year
        while y <= end_year:
            chunk_end_year = min(y + CHUNK_YEARS - 1, end_year)
            chunk_start = f"{y}-01-01" if y != start_year else START_DATE
            chunk_end = f"{chunk_end_year}-12-31" if chunk_end_year != end_year else END_DATE
            print(f"Fetching {chunk_start} to {chunk_end} ...")
            all_rows.extend(fetch_range(chunk_start, chunk_end))
            y = chunk_end_year + 1
            time.sleep(1)  # light courtesy delay between chunked calls
    else:
        print(f"Fetching {START_DATE} to {END_DATE} in a single call ...")
        all_rows = fetch_range(START_DATE, END_DATE)

    with open(OUTPUT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "temp_c"])
        w.writerows(all_rows)

    print(f"Wrote {len(all_rows)} daily rows to {OUTPUT_CSV}")
    if all_rows:
        print(f"  Range: {all_rows[0][0]} to {all_rows[-1][0]}")
        expected_days = (date.fromisoformat(END_DATE) - date.fromisoformat(START_DATE)).days + 1
        if len(all_rows) < expected_days * 0.99:
            print(f"  WARNING: expected ~{expected_days} days, got {len(all_rows)} -- "
                  f"check for gaps (missing values get dropped, not zero-filled).")


if __name__ == "__main__":
    main()
