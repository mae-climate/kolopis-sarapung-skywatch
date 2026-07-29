#!/usr/bin/env python3
"""
build_climate_reference.py

Builds the static climate-reference JSON consumed by the Penampang Weather &
Hazard Watch web app's two new features:
  1. The 30-year climatology reference line/tab on the 14-day rain & temp charts
  2. The "This Day in Climate History" section

This is an OFFLINE, occasional batch job (run it once, then re-run maybe
annually as a new year of data becomes available). The web app never touches
CHIRPS/ERA5/ONI directly -- it only ever fetches the small JSON this script
produces. That split matters: climatology doesn't change day to day, so
there's no reason for the browser to ever do this work.

------------------------------------------------------------------------
WHAT YOU NEED TO SUPPLY (this script does NOT download these for you --
point the CONFIG paths below at files you've already pulled with your
existing CHIRPS/ERA5 pipeline):

  RAIN_CSV_PATH: CHIRPS daily rainfall for the Kolopis grid cell(s),
    columns: date (YYYY-MM-DD), rain_mm
    -> full period of record, ideally 1981-present

  TEMP_CSV_PATH: ERA5-Land daily 2m temperature for the same location,
    columns: date (YYYY-MM-DD), temp_c
    -> use daily mean T2m (documented choice -- change to max if you'd
       rather the temp climatology track daily highs instead)

If your existing pipeline already extracts point time series in a different
shape, just adjust load_rain_csv() / load_temp_csv() below -- the rest of
the script only cares about getting a plain {date: value} dict back.
------------------------------------------------------------------------

ENSO CLASSIFICATION
This uses NOAA CPC's Oceanic Nino Index (ONI) -- the same index your
existing "Rainfall behaviour by ENSO phase" chart is built on, so this
stays consistent with that work rather than introducing a second,
different ENSO definition.

  NOTE: NOAA switched to RONI (Relative ONI) as the *official* index for
  current ENSO monitoring/prediction in Feb 2026 (NWS PNS 26-05). This
  script still uses ONI for the *historical* classification below, since
  the whole point here is consistency with your existing chart and with
  the full historical record. If you build a live "current ENSO phase"
  indicator elsewhere in the app, use RONI for that instead -- it's now
  the operational standard for present-tense conditions.

  The seasonal ONI table below was copied directly from NOAA CPC
  (https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/ensostuff/ONI_v5.php),
  fetched 2026-07-29, covering 1979-2026 (2026 is partial -- NOAA updates
  monthly). UPDATE THIS TABLE when you re-run the pipeline with a new
  year of data.

  Episode classification follows NOAA's OFFICIAL rule, not naive
  thresholding: a season only counts as part of an El Nino (La Nina)
  episode if it belongs to a run of >=5 CONSECUTIVE overlapping seasons
  all >=+0.5C (<=-0.5C). A single season poking above 0.5 in isolation
  does not count -- see classify_enso_episodes() below.
"""

import csv
import json
from datetime import date, timedelta

# ============================================================================
# CONFIG -- change these to match your setup
# ============================================================================
CLIMATOLOGY_START_YEAR = 1996          # rolling recent 30-yr window (1996-2025).
CLIMATOLOGY_END_YEAR = 2025            # Swap to 1991/2020 for the WMO standard
                                        # window instead -- see chat notes on
                                        # why rolling-recent was the working pick.

SMOOTHING_WINDOW_DAYS = 7              # +/- days pooled around each calendar day
                                        # for the plain (all-years) climatology
ENSO_SMOOTHING_WINDOW_DAYS = 10        # wider window for ENSO-stratified composites:
                                        # each phase bucket only has ~1/3 the sample
                                        # size of the plain climatology, so it needs
                                        # more pooling to stay statistically stable

RAIN_CSV_PATH = "chirps_kolopis_daily.csv"
TEMP_CSV_PATH = "era5land_kolopis_daily.csv"
OUTPUT_JSON_PATH = "climate_reference.json"

# Minimum years required in an ENSO bucket before we trust it enough to
# publish a number for it. Below this, the composite gets flagged as
# low-confidence in the output rather than silently shown as equal-weight
# to the other three boxes.
MIN_YEARS_FOR_ENSO_COMPOSITE = 6


# ============================================================================
# ONI SEASONAL TABLE
# Source: NOAA CPC, https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/
#         ensostuff/ONI_v5.php -- fetched 2026-07-29.
# Each row: 12 overlapping 3-month seasons, DJF JFM FMA MAM AMJ MJJ JJA JAS
# ASO SON OND NDJ. Indexed to the season's CENTER month (DJF->Jan, JFM->Feb,
# ..., NDJ->Dec) when mapping a calendar date to its enclosing season below.
# `None` = not yet published (partial current year).
# ============================================================================
SEASON_LABELS = ["DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ",
                  "JJA", "JAS", "ASO", "SON", "OND", "NDJ"]

ONI = {
    1979: [0.0, 0.1, 0.2, 0.3, 0.2, 0.0, 0.0, 0.2, 0.3, 0.5, 0.5, 0.6],
    1980: [0.6, 0.5, 0.3, 0.4, 0.5, 0.5, 0.3, 0.0, -0.1, 0.0, 0.1, 0.0],
    1981: [-0.3, -0.5, -0.5, -0.4, -0.3, -0.3, -0.3, -0.2, -0.2, -0.1, -0.2, -0.1],
    1982: [0.0, 0.1, 0.2, 0.5, 0.7, 0.7, 0.8, 1.1, 1.6, 2.0, 2.2, 2.2],
    1983: [2.2, 1.9, 1.5, 1.3, 1.1, 0.7, 0.3, -0.1, -0.5, -0.8, -1.0, -0.9],
    1984: [-0.6, -0.4, -0.3, -0.4, -0.5, -0.4, -0.3, -0.2, -0.2, -0.6, -0.9, -1.1],
    1985: [-1.0, -0.8, -0.8, -0.8, -0.8, -0.6, -0.5, -0.5, -0.4, -0.3, -0.3, -0.4],
    1986: [-0.5, -0.5, -0.3, -0.2, -0.1, 0.0, 0.2, 0.4, 0.7, 0.9, 1.1, 1.2],
    1987: [1.2, 1.2, 1.1, 0.9, 1.0, 1.2, 1.5, 1.7, 1.6, 1.5, 1.3, 1.1],
    1988: [0.8, 0.5, 0.1, -0.3, -0.9, -1.3, -1.3, -1.1, -1.2, -1.5, -1.8, -1.8],
    1989: [-1.7, -1.4, -1.1, -0.8, -0.6, -0.4, -0.3, -0.3, -0.2, -0.2, -0.2, -0.1],
    1990: [0.1, 0.2, 0.3, 0.3, 0.3, 0.3, 0.3, 0.4, 0.4, 0.3, 0.4, 0.4],
    1991: [0.4, 0.3, 0.2, 0.3, 0.5, 0.6, 0.7, 0.6, 0.6, 0.8, 1.2, 1.5],
    1992: [1.7, 1.6, 1.5, 1.3, 1.1, 0.7, 0.4, 0.1, -0.1, -0.2, -0.3, -0.1],
    1993: [0.1, 0.3, 0.5, 0.7, 0.7, 0.6, 0.3, 0.3, 0.2, 0.1, 0.0, 0.1],
    1994: [0.1, 0.1, 0.2, 0.3, 0.4, 0.4, 0.4, 0.4, 0.6, 0.7, 1.0, 1.1],
    1995: [1.0, 0.7, 0.5, 0.3, 0.1, 0.0, -0.2, -0.5, -0.8, -1.0, -1.0, -1.0],
    1996: [-0.9, -0.8, -0.6, -0.4, -0.3, -0.3, -0.3, -0.3, -0.4, -0.4, -0.4, -0.5],
    1997: [-0.5, -0.4, -0.1, 0.3, 0.8, 1.2, 1.6, 1.9, 2.1, 2.3, 2.4, 2.4],
    1998: [2.2, 1.9, 1.4, 1.0, 0.5, -0.1, -0.8, -1.1, -1.3, -1.4, -1.5, -1.6],
    1999: [-1.5, -1.3, -1.1, -1.0, -1.0, -1.0, -1.1, -1.1, -1.2, -1.3, -1.5, -1.7],
    2000: [-1.7, -1.4, -1.1, -0.8, -0.7, -0.6, -0.6, -0.5, -0.5, -0.6, -0.7, -0.7],
    2001: [-0.7, -0.5, -0.4, -0.3, -0.3, -0.1, -0.1, -0.1, -0.2, -0.3, -0.3, -0.3],
    2002: [-0.1, 0.0, 0.1, 0.2, 0.4, 0.7, 0.8, 0.9, 1.0, 1.2, 1.3, 1.1],
    2003: [0.9, 0.6, 0.4, 0.0, -0.3, -0.2, 0.1, 0.2, 0.3, 0.3, 0.4, 0.4],
    2004: [0.4, 0.3, 0.2, 0.2, 0.2, 0.3, 0.5, 0.6, 0.7, 0.7, 0.7, 0.7],
    2005: [0.6, 0.6, 0.4, 0.4, 0.3, 0.1, -0.1, -0.1, -0.1, -0.3, -0.6, -0.8],
    2006: [-0.9, -0.8, -0.6, -0.4, -0.1, 0.0, 0.1, 0.3, 0.5, 0.8, 0.9, 0.9],
    2007: [0.7, 0.2, -0.1, -0.3, -0.4, -0.5, -0.6, -0.8, -1.1, -1.3, -1.5, -1.6],
    2008: [-1.6, -1.5, -1.3, -1.0, -0.8, -0.6, -0.4, -0.2, -0.2, -0.4, -0.6, -0.7],
    2009: [-0.8, -0.8, -0.6, -0.3, 0.0, 0.3, 0.5, 0.6, 0.7, 1.0, 1.4, 1.6],
    2010: [1.5, 1.2, 0.8, 0.4, -0.2, -0.7, -1.0, -1.3, -1.6, -1.6, -1.6, -1.5],
    2011: [-1.3, -1.0, -0.8, -0.6, -0.5, -0.4, -0.4, -0.6, -0.8, -1.0, -1.0, -0.9],
    2012: [-0.7, -0.6, -0.5, -0.4, -0.2, 0.1, 0.3, 0.4, 0.4, 0.3, 0.1, -0.1],
    2013: [-0.3, -0.3, -0.2, -0.2, -0.3, -0.3, -0.4, -0.3, -0.2, -0.1, -0.1, -0.2],
    2014: [-0.3, -0.3, -0.1, 0.2, 0.3, 0.2, 0.1, 0.1, 0.3, 0.5, 0.7, 0.8],
    2015: [0.7, 0.6, 0.7, 0.8, 1.0, 1.3, 1.6, 1.9, 2.2, 2.5, 2.6, 2.8],
    2016: [2.6, 2.3, 1.7, 1.0, 0.5, 0.0, -0.3, -0.5, -0.6, -0.6, -0.6, -0.5],
    2017: [-0.2, 0.0, 0.2, 0.3, 0.4, 0.4, 0.2, -0.1, -0.3, -0.6, -0.8, -0.9],
    2018: [-0.8, -0.7, -0.6, -0.4, -0.1, 0.1, 0.1, 0.3, 0.5, 0.8, 1.0, 0.9],
    2019: [0.9, 0.9, 0.8, 0.8, 0.6, 0.5, 0.3, 0.2, 0.2, 0.4, 0.6, 0.7],
    2020: [0.6, 0.6, 0.5, 0.3, 0.0, -0.2, -0.4, -0.5, -0.8, -1.1, -1.2, -1.1],
    2021: [-0.9, -0.8, -0.7, -0.5, -0.4, -0.3, -0.3, -0.4, -0.6, -0.8, -0.9, -0.9],
    2022: [-0.8, -0.8, -0.9, -1.0, -0.9, -0.8, -0.8, -0.9, -1.0, -0.9, -0.8, -0.7],
    2023: [-0.5, -0.3, 0.0, 0.3, 0.6, 0.8, 1.1, 1.4, 1.6, 1.8, 2.0, 2.1],
    2024: [1.9, 1.6, 1.3, 0.8, 0.5, 0.2, 0.1, -0.1, -0.2, -0.2, -0.3, -0.4],
    2025: [-0.4, -0.2, -0.1, 0.0, 0.0, 0.0, -0.1, -0.3, -0.4, -0.5, -0.6, -0.5],
    2026: [-0.4, -0.1, 0.1, 0.5, None, None, None, None, None, None, None, None],
}

ONI_THRESHOLD = 0.5
MIN_CONSECUTIVE_SEASONS = 5  # NOAA's official persistence rule


# ============================================================================
# STEP 1: classify every season as El Nino / La Nina / Neutral, respecting
# the persistence rule (not just "is this one season >= threshold")
# ============================================================================
def classify_enso_episodes(oni_table):
    """
    Returns {(year, season_index): 'el_nino' | 'la_nina' | 'neutral'}
    season_index is 0=DJF, 1=JFM, ... 11=NDJ, matching SEASON_LABELS.

    Implements NOAA's actual rule: a season only counts as El Nino/La Nina
    if it's part of a run of >=5 consecutive overlapping seasons all past
    the threshold in the same direction. This matters -- naive single-season
    thresholding overcounts weak, non-persistent blips as full episodes.
    """
    # Flatten to one chronological sequence of (year, season_idx, value)
    flat = []
    for year in sorted(oni_table.keys()):
        for si, val in enumerate(oni_table[year]):
            flat.append((year, si, val))

    labels = ["neutral"] * len(flat)

    # Find runs of consecutive seasons (skipping Nones / gaps) past threshold
    def find_runs(predicate):
        runs = []
        run_start = None
        for i, (_, _, val) in enumerate(flat):
            ok = (val is not None) and predicate(val)
            if ok and run_start is None:
                run_start = i
            elif not ok and run_start is not None:
                runs.append((run_start, i - 1))
                run_start = None
        if run_start is not None:
            runs.append((run_start, len(flat) - 1))
        return runs

    for start, end in find_runs(lambda v: v >= ONI_THRESHOLD):
        if end - start + 1 >= MIN_CONSECUTIVE_SEASONS:
            for i in range(start, end + 1):
                labels[i] = "el_nino"

    for start, end in find_runs(lambda v: v <= -ONI_THRESHOLD):
        if end - start + 1 >= MIN_CONSECUTIVE_SEASONS:
            for i in range(start, end + 1):
                labels[i] = "la_nina"

    result = {}
    for (year, si, _), label in zip(flat, labels):
        result[(year, si)] = label
    return result


# Season whose CENTER month equals a given calendar month.
# DJF centers on Jan(1), JFM on Feb(2), ..., NDJ on Dec(12).
def season_index_for_month(month):
    return (month - 1) % 12


def enso_phase_for_date(d, episode_map):
    """
    Which ENSO phase was 'in effect' for a given calendar date, using the
    season whose center month contains that date. DJF (season 0) is centered
    on January but spans Dec(year-1)-Jan(year)-Feb(year) -- so a date in
    December looks up next year's DJF season, not this year's.
    """
    month = d.month
    season_idx = season_index_for_month(month)
    lookup_year = d.year + 1 if month == 12 else d.year
    return episode_map.get((lookup_year, season_idx), "neutral")


# ============================================================================
# STEP 2: load your daily rain/temp series
# ============================================================================
def load_daily_csv(path, value_col):
    """Returns {date: float}. Adjust this if your CSV columns differ."""
    out = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                d = date.fromisoformat(row["date"])
                out[d] = float(row[value_col])
            except (ValueError, KeyError):
                continue  # skip malformed/missing rows rather than crash the run
    return out


def load_rain_csv(path=RAIN_CSV_PATH):
    return load_daily_csv(path, "rain_mm")


def load_temp_csv(path=TEMP_CSV_PATH):
    return load_daily_csv(path, "temp_c")


# ============================================================================
# STEP 3: day-of-year climatology, all-years and ENSO-stratified
# ============================================================================
def day_of_year_key(d):
    """Use (month, day) rather than a 1-366 ordinal so Feb 29 doesn't shift
    every other day-of-year by one in leap years."""
    return (d.month, d.day)


def dates_within_window(center_month, center_day, window_days, year):
    """All calendar dates within +/- window_days of (center_month, center_day)
    in a given year, handling year-end wraparound."""
    try:
        center = date(year, center_month, center_day)
    except ValueError:
        center = date(year, 3, 1) if center_month == 2 else date(year, center_month, center_day)
    return [center + timedelta(days=off) for off in range(-window_days, window_days + 1)]


def mean(values):
    return sum(values) / len(values) if values else None


def percentile(values, p):
    if not values:
        return None
    s = sorted(values)
    idx = (len(s) - 1) * p
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    frac = idx - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def build_climatology(daily_data, start_year, end_year, window_days,
                        episode_map=None, phase_filter=None):
    """
    For every (month, day) in a year, pool all values within +/- window_days
    across start_year..end_year (optionally restricted to a single ENSO
    phase), and return {(month, day): {mean, p10, p90, n_years}}.

    n_years is the count of *distinct years* contributing, not raw sample
    count -- what you actually want to know for "is this composite trustworthy".
    """
    result = {}
    for month in range(1, 13):
        days_in_month = 29 if month == 2 else (30 if month in (4, 6, 9, 11) else 31)
        for day in range(1, days_in_month + 1):
            pooled = []
            years_seen = set()
            for year in range(start_year, end_year + 1):
                for d in dates_within_window(month, day, window_days, year):
                    if d not in daily_data:
                        continue
                    if phase_filter is not None:
                        if episode_map is None:
                            raise ValueError("phase_filter given without episode_map")
                        if enso_phase_for_date(d, episode_map) != phase_filter:
                            continue
                    pooled.append(daily_data[d])
                    years_seen.add(d.year)
            result[(month, day)] = {
                "mean": mean(pooled),
                "p10": percentile(pooled, 0.10),
                "p90": percentile(pooled, 0.90),
                "n_years": len(years_seen),
            }
    return result


# ============================================================================
# STEP 4: "this day last year" + the four comparison boxes
# ============================================================================
def build_today_in_history(daily_rain, daily_temp, climatology_rain,
                             climatology_temp, enso_climatologies_rain,
                             enso_climatologies_temp, today):
    last_year_date = today.replace(year=today.year - 1)
    mmdd = (last_year_date.month, last_year_date.day)

    actual_rain = daily_rain.get(last_year_date)
    actual_temp = daily_temp.get(last_year_date)
    enso_phase_last_year = None  # filled in by caller if episode_map is in scope

    def box(value, clim_entry, min_years=MIN_YEARS_FOR_ENSO_COMPOSITE):
        if value is None or clim_entry is None or clim_entry["mean"] is None:
            return None
        low_confidence = clim_entry["n_years"] < min_years
        return {
            "actual": round(value, 1),
            "normal": round(clim_entry["mean"], 1),
            "delta": round(value - clim_entry["mean"], 1),
            "pct_of_normal": round(100 * value / clim_entry["mean"], 0) if clim_entry["mean"] else None,
            "n_years": clim_entry["n_years"],
            "low_confidence": low_confidence,
        }

    return {
        "date": last_year_date.isoformat(),
        "rain": {
            "actual_mm": actual_rain,
            "vs_30yr": box(actual_rain, climatology_rain.get(mmdd)),
            "vs_el_nino": box(actual_rain, enso_climatologies_rain["el_nino"].get(mmdd)),
            "vs_la_nina": box(actual_rain, enso_climatologies_rain["la_nina"].get(mmdd)),
            "vs_neutral": box(actual_rain, enso_climatologies_rain["neutral"].get(mmdd)),
        },
        "temp": {
            "actual_c": actual_temp,
            "vs_30yr": box(actual_temp, climatology_temp.get(mmdd)),
            "vs_el_nino": box(actual_temp, enso_climatologies_temp["el_nino"].get(mmdd)),
            "vs_la_nina": box(actual_temp, enso_climatologies_temp["la_nina"].get(mmdd)),
            "vs_neutral": box(actual_temp, enso_climatologies_temp["neutral"].get(mmdd)),
        },
    }


# ============================================================================
# STEP 5: build the 14-day-chart climatology reference (mean line, for every
# day-of-year the chart could ever need to show)
# ============================================================================
def climatology_as_lookup_list(clim_dict):
    """Flatten {(month,day): {...}} to a JSON-friendly list of 366 entries."""
    out = []
    for month in range(1, 13):
        days_in_month = 29 if month == 2 else (30 if month in (4, 6, 9, 11) else 31)
        for day in range(1, days_in_month + 1):
            entry = clim_dict.get((month, day), {})
            out.append({
                "month": month, "day": day,
                "mean": round(entry["mean"], 1) if entry.get("mean") is not None else None,
                "p10": round(entry["p10"], 1) if entry.get("p10") is not None else None,
                "p90": round(entry["p90"], 1) if entry.get("p90") is not None else None,
                "n_years": entry.get("n_years", 0),
            })
    return out


# ============================================================================
# MAIN
# ============================================================================
def main():
    print(f"Loading rain data from {RAIN_CSV_PATH} ...")
    daily_rain = load_rain_csv()
    print(f"  {len(daily_rain)} daily values loaded")

    print(f"Loading temp data from {TEMP_CSV_PATH} ...")
    daily_temp = load_temp_csv()
    print(f"  {len(daily_temp)} daily values loaded")

    print("Classifying ENSO episodes from ONI table ...")
    episode_map = classify_enso_episodes(ONI)

    print(f"Building {CLIMATOLOGY_START_YEAR}-{CLIMATOLOGY_END_YEAR} climatology (rain) ...")
    clim_rain = build_climatology(daily_rain, CLIMATOLOGY_START_YEAR,
                                    CLIMATOLOGY_END_YEAR, SMOOTHING_WINDOW_DAYS)

    print(f"Building {CLIMATOLOGY_START_YEAR}-{CLIMATOLOGY_END_YEAR} climatology (temp) ...")
    clim_temp = build_climatology(daily_temp, CLIMATOLOGY_START_YEAR,
                                    CLIMATOLOGY_END_YEAR, SMOOTHING_WINDOW_DAYS)

    print("Building ENSO-stratified composites (rain) ...")
    enso_clim_rain = {
        phase: build_climatology(daily_rain, CLIMATOLOGY_START_YEAR, CLIMATOLOGY_END_YEAR,
                                   ENSO_SMOOTHING_WINDOW_DAYS, episode_map, phase)
        for phase in ("el_nino", "la_nina", "neutral")
    }

    print("Building ENSO-stratified composites (temp) ...")
    enso_clim_temp = {
        phase: build_climatology(daily_temp, CLIMATOLOGY_START_YEAR, CLIMATOLOGY_END_YEAR,
                                   ENSO_SMOOTHING_WINDOW_DAYS, episode_map, phase)
        for phase in ("el_nino", "la_nina", "neutral")
    }

    today = date.today()
    print(f"Building 'This Day in Climate History' for {today.isoformat()} ...")
    today_in_history = build_today_in_history(
        daily_rain, daily_temp, clim_rain, clim_temp,
        enso_clim_rain, enso_clim_temp, today
    )

    output = {
        "generated": today.isoformat(),
        "climatology_period": f"{CLIMATOLOGY_START_YEAR}-{CLIMATOLOGY_END_YEAR}",
        "smoothing_window_days": SMOOTHING_WINDOW_DAYS,
        "enso_smoothing_window_days": ENSO_SMOOTHING_WINDOW_DAYS,
        "enso_index_used": "ONI (NOAA CPC) -- see script docstring re: RONI transition Feb 2026",
        "chart_climatology": {
            "rain": climatology_as_lookup_list(clim_rain),
            "temp": climatology_as_lookup_list(clim_temp),
        },
        "today_in_history": today_in_history,
    }

    with open(OUTPUT_JSON_PATH, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Wrote {OUTPUT_JSON_PATH}")


if __name__ == "__main__":
    main()
