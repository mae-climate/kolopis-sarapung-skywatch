#!/usr/bin/env python3
"""
Fetches current ENSO (relative Nino3.4 SST anomaly, RONI-consistent) and
MJO (BOM RMM) status and writes a compact JSON file for the Gapu Floodwatch
front end to consume.

Sources (both plain text, no API key, updated on their own schedule):
  ENSO: https://www.cpc.ncep.noaa.gov/data/indices/rel_wksst9120.txt   (weekly)
  MJO:  http://www.bom.gov.au/climate/mjo/graphics/rmm.74toRealtime.txt (daily)

Usage:
  python fetch_climate_indices.py                # live fetch, writes data/climate_indices.json
  python fetch_climate_indices.py --test-fixtures # parse local sample files instead (offline test)
"""

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENSO_URL = "https://www.cpc.ncep.noaa.gov/data/indices/rel_wksst9120.txt"
MJO_URL = "http://www.bom.gov.au/climate/mjo/graphics/rmm.74toRealtime.txt"

HEADERS = {"User-Agent": "Gapu-Floodwatch/1.0 (contact: gapu-floodwatch-project)"}

OUTPUT_PATH = Path("data/climate_indices.json")

MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
)}

MJO_IMPACT = {
    (4, 5): ("active", "Active wave over the Maritime Continent — favours "
                        "monsoon rain bursts and storm outbreaks over Sabah."),
    (8, 1, 2): ("suppressed", "Suppressed wave (Indian Ocean / W. Hemisphere phase) — "
                               "sinking dry air, sunny/hot spells favoured over Sabah."),
    (3, 6, 7): ("transitional", "Transitional phase — MJO influence over the "
                                 "Maritime Continent is weak or shifting."),
}


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


def classify_enso(nino34_anom: float) -> str:
    if nino34_anom >= 0.5:
        return "El Nino"
    if nino34_anom <= -0.5:
        return "La Nina"
    return "Neutral"


def classify_strength(nino34_anom: float) -> str:
    # Approximate ONI-derived strength bins, applied to the relative index.
    # RONI was rescaled to match the variance of the traditional index, so
    # these conventional bins are a reasonable (not official) approximation.
    a = abs(nino34_anom)
    if a < 0.5:
        return "N/A"
    if a < 1.0:
        return "Weak"
    if a < 1.5:
        return "Moderate"
    if a < 2.0:
        return "Strong"
    return "Very Strong"


def parse_enso(raw: str) -> dict:
    """
    File layout (fixed-width-ish, whitespace separated):
      line 1: title
      line 2: region header (Nino1+2  Nino3  Nino34  Nino4)
      line 3: column header (Week  SSTA SSTA SSTA SSTA)
      data lines: DDMMMYYYY  v1  v2  v3  v4   (v3 = Nino3.4 anomaly)
    We take the most recent data line, and also compute a trailing
    ~3-month (13-week) running mean of Nino3.4 as a steadier, more
    RONI-like read than the single noisy weekly value.
    """
    date_re = re.compile(r"^(\d{2})([A-Z]{3})(\d{4})$")
    rows = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        m = date_re.match(parts[0])
        if not m:
            continue
        try:
            vals = [float(x) for x in parts[1:5]]
        except ValueError:
            continue
        day, mon_str, year = m.groups()
        mon = MONTHS.get(mon_str)
        if not mon:
            continue
        date_iso = f"{year}-{mon:02d}-{int(day):02d}"
        rows.append((date_iso, vals))

    if not rows:
        raise ValueError("No parsable ENSO data rows found")

    rows.sort(key=lambda r: r[0])
    latest_date, latest_vals = rows[-1]
    latest_nino34 = latest_vals[2]

    trailing = [r[1][2] for r in rows[-13:]]  # ~13 weeks ≈ 3 months
    running_avg = round(sum(trailing) / len(trailing), 2)

    return {
        "latest_week_ending": latest_date,
        "nino34_weekly_anomaly_c": round(latest_nino34, 2),
        "nino34_3mo_running_avg_c": running_avg,
        "phase": classify_enso(running_avg),
        "strength": classify_strength(running_avg),
        "note": "3-month running average used for phase/strength classification "
                "(closer to official RONI methodology than the single latest week).",
        "source": ENSO_URL,
    }


def parse_mjo(raw: str) -> dict:
    """
    File layout: 2 header lines, then whitespace/comma separated:
      year month day RMM1 RMM2 phase amplitude missing_flag
    """
    rows = []
    for line in raw.splitlines():
        tokens = re.split(r"[,\s]+", line.strip())
        if len(tokens) < 7:
            continue
        try:
            year, month, day = int(tokens[0]), int(tokens[1]), int(tokens[2])
            rmm1, rmm2 = float(tokens[3]), float(tokens[4])
            phase = int(tokens[5])
            amplitude = float(tokens[6])
        except ValueError:
            continue
        if not (1 <= month <= 12 and 1 <= day <= 31 and 1 <= phase <= 8):
            continue
        rows.append({
            "date": f"{year:04d}-{month:02d}-{day:02d}",
            "rmm1": rmm1, "rmm2": rmm2, "phase": phase, "amplitude": amplitude,
        })

    if not rows:
        raise ValueError("No parsable MJO data rows found")

    rows.sort(key=lambda r: r["date"])
    latest = rows[-1]
    active = latest["amplitude"] >= 1.0

    impact_key = "transitional"
    impact_text = "Weak/incoherent MJO signal (amplitude < 1.0) — little influence expected."
    if active:
        for phases, (key, text) in MJO_IMPACT.items():
            if latest["phase"] in phases:
                impact_key, impact_text = key, text
                break

    return {
        "date": latest["date"],
        "rmm1": round(latest["rmm1"], 3),
        "rmm2": round(latest["rmm2"], 3),
        "phase": latest["phase"],
        "amplitude": round(latest["amplitude"], 3),
        "active": active,
        "impact_category": impact_key,
        "impact_text": impact_text,
        "source": MJO_URL,
    }


def main():
    test_mode = "--test-fixtures" in sys.argv

    if test_mode:
        fixtures = Path(__file__).parent / "test_fixtures"
        enso_raw = (fixtures / "rel_wksst9120_sample.txt").read_text()
        mjo_raw = (fixtures / "rmm_sample.txt").read_text()
    else:
        enso_raw = fetch_text(ENSO_URL)
        mjo_raw = fetch_text(MJO_URL)

    output = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "enso": parse_enso(enso_raw),
        "mjo": parse_mjo(mjo_raw),
    }

    out_path = Path(__file__).parent / "climate_indices.json" if test_mode else OUTPUT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))
    print(f"Wrote {out_path}")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
