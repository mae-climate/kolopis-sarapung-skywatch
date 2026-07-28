# Kolopis SkyWatch

A hyperlocal flood/hazard early-warning webapp for Kolopis & Sarapung, Penampang,
Sabah (5.925840, 116.143360) — successor to the earlier "Gapu Floodwatch" /
"Penampang Geo-Watch EWS" prototype. Built and run by one person (a PhD
atmospheric scientist), no backend, static site + GitHub Actions.

## Current state

- `index.html` — main dashboard (formerly `index-new-2.html`). Single-page,
  Tailwind + Chart.js + Tesseract.js. Fetches live data client-side from
  Open-Meteo (keyless, works globally by lat/lon) plus two scraped sources
  via a public CORS proxy (`api.allorigins.win`): a MetMalaysia warning image
  (OCR'd with Tesseract) and the InfoBanjir Moyog river-level table.
- `ensemble.html` — secondary page showing the raw 51-member ECMWF ensemble
  precipitation forecast via Open-Meteo's ensemble API.
- `scripts/fetch_climate_indices.py` — NEW. Fetches + parses ENSO (NOAA CPC
  weekly relative Nino3.4 SST anomaly, RONI-consistent) and MJO (BOM RMM
  index) and writes `data/climate_indices.json`. Run
  `python3 scripts/fetch_climate_indices.py --test-fixtures` to test offline
  against `scripts/test_fixtures/`. Real fetch needs live network access to
  `cpc.ncep.noaa.gov` and `bom.gov.au` (works fine in GitHub Actions).
- `.github/workflows/update-indices.yml` — runs the script every 6h, commits
  `data/climate_indices.json` if changed.

## Known issues in index.html (to fix)

1. **ENSO/MJO panel is hardcoded** (search for "ACCURATE ENSO STATUS" and
   "ACCURATE MJO STATUS" comments around line 363-402). Replace with a fetch
   of `data/climate_indices.json` and populate `#mjo-summary-text`,
   `#mjo-cross-analysis`, and the ENSO gauge/text dynamically.
2. **Silent failure into false "all clear"**: `checkMetMalaysiaWarning()` and
   `fetchMoyogLevel()` both catch errors and display a green
   "NORMAL"/"ONLINE" badge on failure. This must change to an explicit
   amber/gray "data unavailable" state — a false all-clear during a real
   flood-hazard fetch failure is unacceptable for a safety tool.
3. **Everything has equal visual weight.** Household tips (laundry/AC/garden/
   spray), flood hazard status, ENSO/MJO, and satellite maps are all styled
   as identical white cards in one long scroll. Needs a clear hierarchy (see
   Task 3 below).

## Tasks

### 1. Geofencing (scope to Kolopis/Sarapung only)
Home point + radius check is sufficient — no polygon/boundary needed.
Use the existing `latPenampang`/`lonPenampang` constants as center,
haversine distance, ~3-5 km radius. Outside the radius: show the household
weather tips (they generalize fine anywhere via Open-Meteo) but hide/replace
the flood-hazard panel with a message that hazard calibration doesn't extend
to their location yet.

### 2. Bilingual EN/BM toggle
Hardcode translations — do NOT call a live translation API. The app's
dynamic text is a small fixed set of badge labels + short action sentences
(maybe 30-50 strings total), not open-ended content, so a static i18n
lookup table is more reliable, has zero latency, and avoids mistranslating
a flood-safety instruction. Structure: `i18n = { en: {...}, bm: {...} }`,
toggle button flips a `lang` variable and re-renders. Translate LAST, after
the layout rewrite (task 3) so we're not translating strings that get
replaced anyway.

### 3. Layout restructure (currently too crowded)
Replace the flat card grid with a clear hierarchy:
- **Tier 1** (always visible, top of page): one large, unmissable flood-risk
  banner — status badge + one-line action, nothing else.
- **Tier 2** (one tap away, collapsed by default): the "why" — river level,
  rain totals, household tips.
- **Tier 3** (secondary/deep-dive, could be a separate tab): ENSO/MJO,
  sounding numbers (CAPE/CIN/shear), satellite iframes, 14-day chart,
  official feed links.
Household tips (laundry/AC/garden/spray) should be visually demoted below
the flood status — they're a nice-to-have, not the core value prop for a
hazard-watch tool.

### 4. Flood report / feedback button
"Does it flood here?" button, embedded in-page (use a form service like
Formspree — no backend needed, and the user stays on the page). Fields:
- Coordinates, pre-filled via `navigator.geolocation` but shown and
  editable (someone may be reporting a different location than where
  they're standing).
- Severity dropdown: "just rain" / "water on road" / "water entering house".
- Optional free-text note.
Every submission becomes ground-truth flood evidence for future catchment
calibration outside Kolopis — this is intentional, it's how the flood-panel
coverage area eventually expands to other locations.

## Constraints / things to preserve

- No backend, no paid services, no API keys required for the core app
  (Open-Meteo is keyless). Formspree free tier is fine for the feedback form.
- The flood-hazard thresholds (Kolopis ponding, Sarapung slope, Moyog gauge
  levels, Babagon drought baseline) are empirically calibrated to this one
  catchment from a small number of documented 2026 flood events. Do not
  generalize these numbers to other locations without explicit new
  calibration work — that's a scientific validity issue, not just a
  styling one.
- Keep the MetMalaysia/InfoBanjir failure states honest (see Known Issue 2)
  in any refactor — don't reintroduce a silent green fallback.
