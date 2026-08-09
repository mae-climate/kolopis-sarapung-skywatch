#!/usr/bin/env python3
"""
Fetches the nearest real DOE APIMS air-quality station reading (Kolopis /
Penampang area) and writes a compact JSON file for the Gapu Floodwatch
front end to consume.

Malaysia's DOE (Department of Environment) does not publish a public JSON
API for APIMS -- apims.doe.gov.my / eqms.doe.gov.my are dashboards only,
and the formal bulk-data channel (btm.doe.gov.my/permohonandata) is an
application process, not a live feed. The practical path to DOE's real
station data is the World Air Quality Index project (aqicn.org), which
mirrors DOE's ~68-station EQMP network station-by-station (their
"my.apims" network) through its own free, token-gated API.

Source (needs a free token, see below -- NOT keyless):
  WAQI geo-lookup: https://api.waqi.info/feed/geo:{lat};{lon}/?token=...
  Auto-selects whatever DOE-attributed station is nearest the given
  coordinates and returns its name, so this script never hardcodes a
  specific station ID.

Auth:
  Reads the token from the WAQI_TOKEN environment variable. Get a free
  token at https://aqicn.org/data-platform/token -- do NOT commit it to
  the repo or paste it into a chat/issue; it belongs in this repo's
  GitHub Actions secret (Settings -> Secrets and variables -> Actions),
  referenced from .github/workflows/update-haze.yml as
  ${{ secrets.WAQI_TOKEN }} and passed to this script as an env var.
  This script will refuse to run without it (see main()).

CONFIRMED (Aug 2026) — cross-checked by hand against eqms.doe.gov.my for the
Kota Kinabalu station: WAQI's `data.aqi` field matched DOE's own published
reading for that station. So for a DOE-attributed station, WAQI is passing
through DOE's own computed Air Pollutant Index rather than re-deriving a
different (e.g. US EPA) scale -- the banding in index.html treating this
number as DOE's own API is correct. Only checked against one station/one
snapshot in time, not a running guarantee -- if a future reading looks
obviously off (e.g. wildly disagrees with DOE APIMS during an active haze
event), re-check before trusting it blindly.

Note this does NOT extend to the individual `iaqi.*` sub-index fields
(pm25, pm10, etc.) below -- whether those are raw concentrations or
already-computed sub-indices is still unconfirmed, and they aren't
currently used for anything (kept only for future debugging).

Usage:
  WAQI_TOKEN=xxx python fetch_haze.py       # live fetch, writes data/haze.json
  python fetch_haze.py --test-fixtures      # parse a local sample response (offline test)
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

WAQI_URL_TMPL = "https://api.waqi.info/feed/geo:{lat};{lon}/?token={token}"

# Same coordinates index.html uses for the Open-Meteo modelled card (latPenampang /
# lonPenampang in index.html) -- keep these in sync if that ever changes.
LAT = 5.925840
LON = 116.143360

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

OUTPUT_PATH = Path("data/haze.json")

# If the station's own reported observation time is older than this, something is
# wrong upstream (station offline, feed stuck serving a cached reading) -- DOE
# stations are known to go offline for extended stretches, so fail loudly rather
# than silently commit a stale-looking-fresh reading. Generous window because
# APIMS stations don't all update hourly.
MAX_STATION_AGE_HOURS = 48


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_waqi_response(payload: dict) -> dict:
    if payload.get("status") != "ok":
        raise ValueError(f"WAQI API returned non-ok status: {payload}")

    data = payload.get("data")
    if not data or data.get("aqi") is None:
        raise ValueError(f"WAQI response missing data.aqi: {payload}")

    city = data.get("city", {}) or {}
    time_info = data.get("time", {}) or {}
    # WAQI gives both a human "s" string (station-local time, no offset guaranteed)
    # and an ISO "iso" string (with offset) -- keep both; the front end just
    # displays observed_local as-is, no further date parsing needed client-side.
    observed_local = time_info.get("s") or time_info.get("iso") or None
    observed_iso = time_info.get("iso") or None

    return {
        "name": city.get("name", "Unknown station"),
        "aqi": data.get("aqi"),  # confirmed against DOE APIMS for KK, Aug 2026 -- see docstring
        "dominant_pollutant": data.get("dominentpol"),
        # Individual pollutant sub-indices as WAQI returns them -- kept for
        # debugging/future use, NOT currently used to compute anything client-side.
        # Unlike the top-level "aqi" above, it's still not confirmed whether these
        # iaqi values are raw concentrations or
        # already-computed sub-indices.
        "iaqi": data.get("iaqi"),
        "observed_local": observed_local,
        "observed_iso": observed_iso,
        "station_url": (data.get("city", {}) or {}).get("url"),
        "attributions": data.get("attributions"),
    }


def main():
    test_mode = "--test-fixtures" in sys.argv

    if test_mode:
        fixtures = Path(__file__).parent / "test_fixtures"
        sample_path = fixtures / "waqi_sample.json"
        payload = json.loads(sample_path.read_text())
    else:
        token = os.environ.get("WAQI_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "WAQI_TOKEN environment variable is not set. Get a free token at "
                "https://aqicn.org/data-platform/token and add it as a GitHub "
                "Actions secret named WAQI_TOKEN (Settings -> Secrets and "
                "variables -> Actions) -- never pass it on the command line or "
                "commit it. Refusing to run without it."
            )
        url = WAQI_URL_TMPL.format(lat=LAT, lon=LON, token=token)
        payload = fetch_json(url)

    station = parse_waqi_response(payload)

    if not test_mode and station["observed_iso"]:
        try:
            obs_dt = datetime.fromisoformat(station["observed_iso"])
            if obs_dt.tzinfo is None:
                obs_dt = obs_dt.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - obs_dt).total_seconds() / 3600
            if age_hours > MAX_STATION_AGE_HOURS:
                raise RuntimeError(
                    f"Station '{station['name']}' reading looks stale: observed "
                    f"{station['observed_iso']} ({age_hours:.0f}h old, threshold is "
                    f"{MAX_STATION_AGE_HOURS}h). Likely the station is offline or "
                    f"WAQI is serving a cached reading. Not writing output."
                )
        except ValueError:
            # Timestamp didn't parse as ISO -- don't block the whole run over a
            # formatting quirk, just skip the staleness check for this run.
            pass

    output = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "station": station,
    }

    out_path = Path(__file__).parent / "haze_test_output.json" if test_mode else OUTPUT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))
    print(f"Wrote {out_path}")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
