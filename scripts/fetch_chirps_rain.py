#!/usr/bin/env python3
"""
fetch_chirps_rain.py

Pulls daily CHIRPS rainfall for the Kolopis point from Google Earth Engine
and writes it in the exact format build_climate_reference.py expects:
date, rain_mm CSV.

WHY GOOGLE EARTH ENGINE (not direct file download):
CHIRPS is distributed as one global GeoTIFF per day. Downloading and
extracting a single pixel from ~10,600 individual global raster files
(1996-2025) is slow and wasteful -- gigabytes of download for one number
per file. Earth Engine holds the whole CHIRPS archive server-side and lets
you query a point time series directly, which is the practical way to do
this specific extraction. This is a real setup cost (below) in exchange for
a MUCH faster pull.

Dataset verified directly against Google's Earth Engine Data Catalog on
2026-07-29: UCSB-CHG/CHIRPS/DAILY, band 'precipitation', 0.05 deg (~5.5km),
1981-present, units mm/day -- not guessed from memory.

------------------------------------------------------------------------
ONE-TIME SETUP (skip if you've already got GEE working):

1. Sign up for Earth Engine access (free, non-commercial):
   https://signup.earthengine.google.com/
   You'll need a Google Cloud project -- if you don't have one, the signup
   flow creates one for you. Note the project ID, you need it below.

2. pip install earthengine-api

3. Run once interactively to authenticate (opens a browser for OAuth):
   python3 -c "import ee; ee.Authenticate()"
   This caches a token locally -- you only need to do this once per machine.

4. Set EE_PROJECT below to your Cloud project ID. This became a required
   parameter in a past Earth Engine API change; ee.Initialize() will fail
   with an unhelpful error if it's missing or wrong.
------------------------------------------------------------------------

Requires: pip install earthengine-api
"""

import csv
from datetime import date

import ee

# ============================================================================
# CONFIG
# ============================================================================
LAT, LON = 5.925840, 116.143360   # Kolopis / Kg. Sarapung
START_YEAR = 1996                  # matches the rolling-recent 30yr window in
END_YEAR = 2025                    # build_climate_reference.py -- keep in sync
OUTPUT_CSV = "/Users/maeleong/Work/ClimateData-Projects/sabah-climate-data/chirps/kolopis_daily.csv"

EE_PROJECT = "sabah-climate-data"  # <-- REQUIRED: set to your Earth Engine Cloud project ID

CHIRPS_COLLECTION = "UCSB-CHG/CHIRPS/DAILY"
BAND = "precipitation"
SCALE_METRES = 5566  # native CHIRPS pixel size, per Earth Engine catalog

# Optional: average over a small buffer around the point instead of a single
# pixel. Given the ~4.16 sq km micro-catchment is already roughly CHIRPS-pixel-
# sized, a point extraction is a reasonable default -- set this if you'd
# rather smooth across a couple of neighboring pixels.
BUFFER_METRES = None  # e.g. 2000 for a 2km buffer average; None = single pixel at the point


def fetch_year(point_geom, year):
    """One year at a time -- keeps each getRegion() call well within Earth
    Engine's computation timeout/size limits. A single 30-year call in one
    shot is the kind of thing that silently times out on GEE's servers for
    a daily collection this size; chunking is the standard mitigation."""
    start = f"{year}-01-01"
    end = f"{year + 1}-01-01"  # exclusive upper bound

    collection = (
        ee.ImageCollection(CHIRPS_COLLECTION)
        .filterDate(start, end)
        .select(BAND)
    )

    if BUFFER_METRES:
        region = point_geom.buffer(BUFFER_METRES)
        # reduceRegion per image, mapped over the collection -- still one
        # server-side computation, still within Earth Engine's normal limits
        # for a single year of daily images.
        def reduce_image(img):
            val = img.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=region, scale=SCALE_METRES
            ).get(BAND)
            return img.set("date", img.date().format("YYYY-MM-dd")).set("value", val)
        features = collection.map(reduce_image)
        result = features.aggregate_array("date").getInfo()
        values = features.aggregate_array("value").getInfo()
        return list(zip(result, values))
    else:
        # getRegion at a single point is the efficient path for point time
        # series -- one call returns [id, lon, lat, time, band] per image.
        raw = collection.getRegion(point_geom, SCALE_METRES).getInfo()
        header, rows = raw[0], raw[1:]
        time_idx = header.index("time")
        band_idx = header.index(BAND)
        out = []
        for r in rows:
            if r[band_idx] is None:
                continue
            d = date.fromtimestamp(r[time_idx] / 1000).isoformat()
            out.append((d, round(r[band_idx], 2)))
        return out


def main():
    print(f"Initializing Earth Engine (project={EE_PROJECT}) ...")
    if EE_PROJECT == "your-gcp-project-id":
        raise SystemExit(
            "Set EE_PROJECT to your actual Earth Engine Cloud project ID before running. "
            "See the setup instructions in this file's docstring."
        )
    ee.Initialize(project=EE_PROJECT)

    point = ee.Geometry.Point([LON, LAT])
    all_rows = []

    for year in range(START_YEAR, END_YEAR + 1):
        print(f"Fetching {year} ...")
        year_rows = fetch_year(point, year)
        all_rows.extend(year_rows)
        print(f"  {len(year_rows)} days")

    all_rows.sort(key=lambda r: r[0])

    with open(OUTPUT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "rain_mm"])
        w.writerows(all_rows)

    print(f"\nWrote {len(all_rows)} daily rows to {OUTPUT_CSV}")
    if all_rows:
        print(f"  Range: {all_rows[0][0]} to {all_rows[-1][0]}")
        expected_days = (date(END_YEAR, 12, 31) - date(START_YEAR, 1, 1)).days + 1
        if len(all_rows) < expected_days * 0.99:
            print(f"  WARNING: expected ~{expected_days} days, got {len(all_rows)} -- "
                  f"check for gaps.")


if __name__ == "__main__":
    main()
