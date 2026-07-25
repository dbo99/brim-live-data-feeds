# BRIM live-feed product inventory

This is the authoritative human-readable inventory for products published by
the public live-feed repository. It describes current implementation without
turning volatile sample counts or file sizes into guarantees.

Machine-readable files remain authoritative for their current serialized
fields. The producer-to-consumer interface and change policy are owned by
[BRIM_CONSUMER_CONTRACT.md](BRIM_CONSUMER_CONTRACT.md). Publication and
last-known-good behavior are owned by
[PUBLISHING_AND_OPERATIONS.md](PUBLISHING_AND_OPERATIONS.md).

Baseline audited: 2026-07-24; the audited commit is recorded in
[README.md](README.md).

## Product status vocabulary

- **Canonical consumer product** — a path directly fetched by the private BRIM
  consumer.
- **Compatibility output** — retained for older or non-BRIM consumers; not the
  preferred current BRIM entry point.
- **Producer/QA metadata** — summary or trace metadata useful for diagnosis but
  not currently fetched by BRIM.
- **Static/manual context** — tracked context prepared outside the scheduled
  writer and fetched with a live product.
- **Sandbox/preview** — diagnostic output that is not an official product.
- **Ownership unclear** — tracked output whose current consumer or refresh
  owner has not been established.

## Inventory at a glance

All schedules below are UTC. A scheduled writer also supports
`workflow_dispatch` unless noted otherwise.

| Family | Declared product ID | Provider | Writer cadence | Preferred BRIM entry point | Formal manifest |
|---|---|---|---|---|---|
| CDEC reservoir | Not declared | California Data Exchange Center, with limited CNRFC and USACE context | Every 3 hours at :29 | `docs/data/cdec_reservoir_latest.geojson` | No |
| CoCoRaHS precipitation | Not declared | CoCoRaHS | 00:37, 08:37, 16:37 | CA and CONUS GeoJSON files | No |
| Delta operations | Not declared | California DWR | Five attempts between 15:41 and 17:41 | Features, summary, and X2 reference | No |
| GFS wind | `gfs_surface_wind` | NOAA/NCEP GFS | Hourly at :47 | GFS manifest | Yes |
| HRRR wind | `hrrr_surface_wind` | NOAA/NCEP HRRR | Hourly at :33 | HRRR manifest | Yes |
| NBM wind guidance | Not declared | NOAA/NCEP NBM | 01:23, 07:23, 13:23, 19:23 | NBM manifest | Yes |
| ASOS/AWOS observed wind | `asos_awos_wind_obs` | NOAA Aviation Weather Center METAR cache | Twice hourly at :17 and :42 | GeoJSON, summary, and manifest | Yes |
| USGS streamflow | Not declared | USGS Water Data API | Every 4 hours at :23 | GeoJSON and summary | No |
| USGS groundwater | Not declared | USGS Water Data API | Daily at 13:41 | GeoJSON and summary | No |
| SCAN soil moisture | Not declared | USDA NRCS AWDB | Daily at 14:30 | GeoJSON plus current-WY and historical context | No |
| Snow-pillow SWE | Not declared | USDA NRCS AWDB and CDEC | 05:52, 13:52, 21:52 | GeoJSON plus current-WY and historical context | No |

The absence of a declared product ID or manifest is a current contract gap,
not permission to assign an identifier casually. A later versioned manifest
migration may establish IDs after producer and consumer review.

## Observed payload snapshot

The following values describe tracked output near baseline commit `d39a128`.
They are operational observations, not acceptance limits.

| Product | Observed records | Approximate primary payload |
|---|---:|---:|
| CDEC reservoir | 126 features | 0.5 MB |
| CoCoRaHS California | 262 features | 0.26 MB |
| CoCoRaHS CONUS | 13,811 features | 13.7 MB |
| Delta operations | 13 operational features and 178 X2 reference points | 18 KB and 86 KB |
| ASOS/AWOS | 200 stations | 0.19 MB |
| USGS streamflow | 2,387 station features | 8.9 MB |
| USGS groundwater | 2,263 well features | 11.9 MB |
| SCAN | 28 station features | 0.29 MB, plus context CSVs |
| Snow-pillow | 304 station features | 0.70 MB, plus context CSVs |

GFS, HRRR, and NBM are rolling multi-file products. Their total size changes
with retained model cycles and forecast entries. Do not infer a payload budget
from one checkout.

## Writer ownership and public path classification

All paths in this table are repository-relative. GitHub Pages removes the leading
`docs/` segment from the hosted URL. A wildcard identifies a rolling set, not
permission for a workflow to stage arbitrary files in that directory.

“Writer-managed” means generated or explicitly staged by the scheduled workflow.
It does not mean every such file is a canonical consumer product.

| Family | Scheduled writer-managed outputs | Consumer/operations role |
|---|---|---|
| CDEC reservoir | `docs/data/cdec_reservoir_latest.geojson`<br>`docs/data/cdec_reservoir_latest_summary.json` | Both are canonical consumer products |
| CoCoRaHS | `docs/data/cocorahs_daily_precip_ca_latest.geojson`<br>`docs/data/cocorahs_daily_precip_ca_latest_summary.json`<br>`docs/data/cocorahs_daily_precip_conus_latest.geojson`<br>`docs/data/cocorahs_daily_precip_conus_latest_summary.json` | All four are canonical consumer products |
| Delta operations | `docs/data/delta_ops_daily_summary.json`<br>`docs/data/delta_ops_daily_summary_features.geojson`<br>`docs/data/delta_ops_daily_summary_summary.json`<br>`docs/data/delta_ops_x2_reference.geojson` | Features, compact summary, and X2 reference are canonical; `delta_ops_daily_summary.json` is producer/QA metadata with unresolved consumer ownership |
| GFS wind | `docs/data/wind/gfs_surface_wind_feed_manifest.json`<br>`docs/data/wind/gfs/surface/*.json`<br>`docs/data/wind/gfs_surface_wind_latest.json`<br>`docs/data/wind/gfs_surface_wind_latest_summary.json` | Manifest and selected rolling target are canonical; latest JSON/summary are compatibility outputs |
| HRRR wind | `docs/data/wind/hrrr_surface_wind_feed_manifest.json`<br>`docs/data/wind/hrrr/surface/*.json`<br>`docs/data/wind/hrrr_surface_wind_latest.json`<br>`docs/data/wind/hrrr_surface_wind_latest_summary.json` | Manifest and selected rolling target are canonical; latest JSON/summary are compatibility outputs |
| NBM wind guidance | `docs/data/wind/nbm_wind_guidance_feed_manifest.json`<br>`docs/data/wind/nbm/guidance/*.geojson`<br>`docs/data/wind/nbm_wind_guidance_latest_summary.json` | Manifest and selected target are canonical; summary is producer/QA metadata |
| ASOS/AWOS | `docs/data/wind/asos_awos_wind_latest.geojson`<br>`docs/data/wind/asos_awos_wind_latest_summary.json`<br>`docs/data/wind/asos_awos_wind_feed_manifest.json` | All three are canonical consumer/status products |
| USGS streamflow | `docs/data/usgs_streamflow_latest_ca.geojson`<br>`docs/data/usgs_streamflow_latest_ca_summary.json` | Both are canonical consumer products |
| USGS groundwater | `docs/data/usgs_groundwater_latest_ca.geojson`<br>`docs/data/usgs_groundwater_latest_ca_summary.json` | Both are canonical consumer products |
| SCAN soil moisture | `docs/data/scan_soil_moisture_latest.geojson`<br>`docs/data/scan_soil_moisture_latest_summary.json`<br>`docs/data/scan_soil_moisture_current_wy_trace.csv`<br>`docs/data/scan_soil_moisture_current_wy_trace_summary.json`<br>`docs/data/scan_depth_style.csv` | Latest, summary, and trace are canonical; trace summary is producer/QA metadata; depth style is consumer context. `docs/data/scan_sms_waterday_percentiles.csv`, `docs/data/scan_sms_monthly_context.csv`, and `docs/data/scan_sms_prior_wy_fallback_traces.csv` are separately managed consumer context |
| Snow-pillow SWE | `docs/data/snow_pillow_latest.geojson`<br>`docs/data/snow_pillow_latest_summary.json`<br>`docs/data/snow_pillow_current_wy_trace.csv`<br>`docs/data/snow_pillow_current_wy_trace_summary.json` | Latest, summary, and trace are canonical; trace summary is producer/QA metadata. `docs/data/snow_pillow_swe_waterday_percentiles.csv`, `docs/data/snow_pillow_swe_monthly_context.csv`, `docs/data/snow_pillow_swe_prior_wy_fallback_traces.csv`, and `docs/data/snow_pillow_swe_normal_medians.csv` are static/manual consumer context |

Context files are part of consumer behavior even when the scheduled writer does
not regenerate them. A file's presence in this table does not change its current
ownership.

## 1. CDEC reservoir

- **Purpose:** Current reservoir storage and operational source links for
  California-focused screening.
- **Workflow:** [`.github/workflows/build-cdec-reservoir-feed.yml`](../.github/workflows/build-cdec-reservoir-feed.yml).
- **Producer:** [`scripts/build_cdec_reservoir_latest.R`](../scripts/build_cdec_reservoir_latest.R).
- **Sources:** CDEC latest sensor-15 table, CDEC daily reservoir report,
  curated station/capacity inputs, selective CNRFC observed storage fallback,
  and USACE availability context.
- **Canonical outputs:**
  [`cdec_reservoir_latest.geojson`](data/cdec_reservoir_latest.geojson) and
  [`cdec_reservoir_latest_summary.json`](data/cdec_reservoir_latest_summary.json).
- **Format/domain/CRS:** GeoJSON points for the curated California reservoir
  index; longitude/latitude is WGS84-compatible. No machine CRS declaration is
  currently standardized.
- **Principal units:** acre-feet, million acre-feet, percent capacity, feet,
  and cubic feet per second where present.
- **Time model:** Feed build time, source observation time, display observation
  time, source-specific daily report date, and calculated age are distinct.
  UTC is retained for machine fields; BRIM presents Pacific Time.
- **Freshness:** Feature properties identify observations older than 12 and 24
  hours. BRIM renders these as caution and stale states.
- **Fallback:** Display priority is CDEC latest, then CDEC daily-midnight
  storage, then selective CNRFC observed/current fallback. Missing is not
  treated as zero.
- **QA/empty policy:** The producer requires a minimum parsed latest-row count
  unless degraded publication is explicitly enabled. If neither latest nor
  daily storage yields usable values, the build stops.
- **Attribution:** California DWR/CDEC, CNRFC, USACE, and source URLs carried in
  the product.
- **Known gaps:** No manifest, common schema version, checksum, producer commit,
  or contractual payload range.

## 2. CoCoRaHS precipitation

- **Purpose:** Volunteer-observed approximate prior-24-hour precipitation,
  offered as separate California and CONUS layers.
- **Workflow:** [`.github/workflows/build-cocorahs-daily-precip-feed.yml`](../.github/workflows/build-cocorahs-daily-precip-feed.yml).
- **Producer:** [`scripts/build_cocorahs_daily_precip_latest.R`](../scripts/build_cocorahs_daily_precip_latest.R).
- **Source:** CoCoRaHS DailyPrecipObs API. The normal producer retrieves a
  paginated CONUS result and derives the California subset from those station
  records.
- **Canonical outputs:** CA and CONUS
  `cocorahs_daily_precip_*_latest.geojson` files and their summaries under
  [`docs/data`](data/).
- **Format/domain/CRS:** GeoJSON points in longitude/latitude for the retrieved
  CONUS set and its derived California subset.
- **Principal units:** English-unit precipitation and snow fields. Each station
  has its own observation time; this is not one uniform layer-wide clock.
- **Time model:** Observation, entry, source-window, and feed-build times are
  separate. BRIM displays user-facing times in Pacific Time.
- **Freshness/fallback:** No formal feed-wide stale threshold or fallback
  product is implemented. A prior valid official product remains only when the
  workflow fails before publication.
- **QA/empty policy:** Requests retry and station reports are deterministically
  deduplicated, but the producer lacks a contractual minimum feature count and
  a strong all-pages completeness gate. A valid but truncated response is a
  known risk.
- **Attribution:** Community Collaborative Rain, Hail and Snow Network with
  station URLs.
- **Known gaps:** No manifest or schema version; completeness, freshness,
  maximum payload, and empty-result policy are not formalized.

## 3. Delta operations

- **Purpose:** A compact map representation of DWR Delta Operations Daily
  Summary metrics and X2 river-kilometer context.
- **Workflow:** [`.github/workflows/build-delta-ops-daily-summary.yml`](../.github/workflows/build-delta-ops-daily-summary.yml).
- **Producer:** [`scripts/build_delta_ops_daily_summary.R`](../scripts/build_delta_ops_daily_summary.R).
- **Source:** California DWR Delta Operations Daily Summary PDF plus curated
  location and X2 lookup inputs.
- **Canonical BRIM outputs:**
  [`delta_ops_daily_summary_features.geojson`](data/delta_ops_daily_summary_features.geojson),
  [`delta_ops_daily_summary_summary.json`](data/delta_ops_daily_summary_summary.json),
  and [`delta_ops_x2_reference.geojson`](data/delta_ops_x2_reference.geojson).
- **Producer/QA metadata:** [`delta_ops_daily_summary.json`](data/delta_ops_daily_summary.json)
  contains parsed values but is not referenced by the current BRIM registry.
- **Format/domain/CRS:** GeoJSON operational points and X2 reference points in
  WGS84-compatible longitude/latitude, plus JSON summaries.
- **Principal units:** cfs, km, TAF, KAF/day, and source-specific percentages.
- **Time model:** Report date, X2 position date, feed build UTC, and Pacific
  build time are distinct.
- **Freshness:** The producer rejects future report dates by default and a
  report lag greater than seven days. Scheduled attempts skip work when the
  current local report date is already published; manual dispatch forces a
  refresh attempt.
- **QA/empty policy:** PDF length, recognizable content, report date, and a
  minimum operational feature set are validated before output.
- **Attribution:** California Department of Water Resources; preliminary data
  warning remains visible.
- **Known gaps:** No manifest/schema version and no documented status for the
  parsed-values compatibility/metadata file.

## 4. GFS wind

- **Purpose:** Manifest-selected NOAA GFS 10-m wind fields for browser targets
  around current time and +24 hours.
- **Workflow:** [`.github/workflows/build-gfs-wind-latest.yml`](../.github/workflows/build-gfs-wind-latest.yml).
- **Producer:** [`scripts/build_gfs_wind_latest.R`](../scripts/build_gfs_wind_latest.R).
- **Source:** NOAA/NCEP GFS 0.25-degree model data.
- **Canonical entry point:**
  [`gfs_surface_wind_feed_manifest.json`](data/wind/gfs_surface_wind_feed_manifest.json).
- **Rolling product:** JSON U/V grids beneath
  [`docs/data/wind/gfs/surface`](data/wind/gfs/surface/).
- **Compatibility outputs:** `docs/data/wind/gfs_surface_wind_latest.json` and
  `docs/data/wind/gfs_surface_wind_latest_summary.json`.
  Current BRIM consumption is manifest-driven.
- **Format/domain/CRS:** Gridded JSON with paired U/V records, grid headers,
  reference time and forecast hour. Coordinates are longitude/latitude.
- **Principal units:** m/s in grid records; BRIM derives user-facing mph.
- **Time model:** Model cycle/reference, forecast hour, valid time, generation
  time, and local display time are separate.
- **Freshness:** Current manifest declares a nine-hour stale threshold. The
  watchdog separately checks manifest age and required time coverage.
- **QA/empty policy:** Minimum GRIB and JSON sizes, data decoding, finite wind
  statistics, and at least one usable entry are required. The current producer
  can still publish a partial target set if some entries fail.
- **Attribution:** NOAA/NCEP GFS.
- **Known gaps:** No JSON Schema, checksum, producer commit, publication time,
  or strict required-target completeness gate.

## 5. HRRR wind

- **Purpose:** High-resolution rolling 10-m wind fields for Current, +6-hour,
  and +12-hour browser targets.
- **Workflow:** [`.github/workflows/build-hrrr-wind-latest.yml`](../.github/workflows/build-hrrr-wind-latest.yml).
- **Producer:** [`scripts/build_hrrr_wind_latest.R`](../scripts/build_hrrr_wind_latest.R).
- **Source:** NOAA/NCEP HRRR model data processed with wgrib2 and R.
- **Canonical entry point:**
  [`hrrr_surface_wind_feed_manifest.json`](data/wind/hrrr_surface_wind_feed_manifest.json).
- **Rolling product:** JSON grids beneath
  [`docs/data/wind/hrrr/surface`](data/wind/hrrr/surface/).
- **Compatibility outputs:** `docs/data/wind/hrrr_surface_wind_latest.json` and
  `docs/data/wind/hrrr_surface_wind_latest_summary.json`.
- **Format/domain/CRS:** Paired U/V grid records in longitude/latitude JSON.
- **Principal units:** m/s in product records; BRIM displays mph.
- **Time model:** Model cycle, forecast hour, valid time, generation time and
  Pacific display time.
- **Freshness:** Current manifest declares four hours. BRIM selects the nearest
  entry with a maximum target error of 2.1 hours.
- **QA/empty policy:** GRIB inventory, earth-relative wind, grid orientation,
  expected dimensions, file size, decoded entries and target proximity are
  checked. Some target failures can remain represented in the manifest.
- **Fallback:** If a selected field later fails to load, BRIM may keep the
  previously displayed in-session field. That browser behavior is not a
  repository-level archived fallback.
- **Attribution:** NOAA/NCEP HRRR.
- **Known gaps:** No common schema/checksum/provenance envelope.

## 6. NBM wind guidance

- **Purpose:** NBM sustained-wind and gust percentile guidance for +6, +12,
  +24, and +48 hour choices.
- **Workflow:** [`.github/workflows/build-nbm-wind-guidance-latest.yml`](../.github/workflows/build-nbm-wind-guidance-latest.yml).
- **Producer:** [`scripts/build_nbm_wind_guidance_latest.R`](../scripts/build_nbm_wind_guidance_latest.R).
- **Source:** NOAA/NCEP National Blend of Models.
- **Canonical entry point:**
  [`nbm_wind_guidance_feed_manifest.json`](data/wind/nbm_wind_guidance_feed_manifest.json).
- **Rolling product:** GeoJSON files beneath
  [`docs/data/wind/nbm/guidance`](data/wind/nbm/guidance/).
- **Producer/QA metadata:**
  `docs/data/wind/nbm_wind_guidance_latest_summary.json`.
- **Format/domain/CRS:** Land-masked GeoJSON grid points in longitude/latitude.
- **Principal units:** mph for p10/p50/p90 sustained wind and gust; direction in
  degrees/cardinal text.
- **Time model:** Model cycle, forecast hour, valid time, and generation time.
- **Freshness:** Manifest coverage and cycle availability are more meaningful
  than one simple stale field. BRIM selects within 3.1 hours of a target.
- **QA/empty policy:** Complete source field sets, field coverage, grid
  alignment, finite values and minimum retained land points are required.
- **Fallback:** BRIM may retain the previously displayed in-session field after
  a selected-entry failure.
- **Attribution:** NOAA/NCEP NBM.
- **Known gaps:** Manifest has `feed_version` but no `product_id`, common stale
  field, checksum, schema version or producer commit.

## 7. ASOS/AWOS observed wind

- **Purpose:** Recent METAR/ASOS station wind speed, direction and gusts.
- **Workflow:** [`.github/workflows/build-asos-awos-wind-latest.yml`](../.github/workflows/build-asos-awos-wind-latest.yml).
- **Producer:** [`scripts/build_asos_awos_wind_latest.R`](../scripts/build_asos_awos_wind_latest.R).
- **Source:** NOAA Aviation Weather Center compressed METAR cache.
- **Canonical outputs:** ASOS GeoJSON, summary, and
  [`asos_awos_wind_feed_manifest.json`](data/wind/asos_awos_wind_feed_manifest.json).
- **Format/domain/CRS:** “Hydrologic California + adjacent basins” GeoJSON
  station points in longitude/latitude. The current bounding box is west
  -125.5, east -112.0, south 31.0, north 44.0.
- **Principal units:** knots, mph and m/s; direction in degrees/cardinal text.
- **Time model:** Observation time UTC/Pacific, age, build time and generation
  time.
- **Freshness:** Producer manifest declares stale after two hours. BRIM
  distinguishes recent, caution and older observations using observation age.
- **QA/empty policy:** Invalid coordinates/winds and observations outside the
  configured age/domain window are removed; fewer than 25 features stops the
  producer.
- **Attribution:** NOAA Aviation Weather Center and raw METAR text.
- **Known gap:** Canonical Git contains RTW019. Separate RTW020-oriented
  integration/recovery material has not been promoted into canonical history.
  It is not current official source.

## 8. USGS streamflow

- **Purpose:** Latest continuous discharge and gage height for a curated
  California station index, with optional compact history and CNRFC links.
- **Workflow:** [`.github/workflows/build-usgs-streamflow-latest-ca.yml`](../.github/workflows/build-usgs-streamflow-latest-ca.yml).
- **Producer:** [`scripts/build_usgs_streamflow_latest_ca.R`](../scripts/build_usgs_streamflow_latest_ca.R).
- **Source:** USGS Water Data API latest-continuous values and optional daily
  context.
- **Canonical outputs:**
  [`docs/data/usgs_streamflow_latest_ca.geojson`](data/usgs_streamflow_latest_ca.geojson)
  and
  [`docs/data/usgs_streamflow_latest_ca_summary.json`](data/usgs_streamflow_latest_ca_summary.json).
- **Format/domain/CRS:** Curated station GeoJSON in longitude/latitude.
- **Principal units:** discharge in cfs, stage in feet, observation age in
  hours.
- **Time model:** Discharge and stage timestamps/ages, query window, optional
  history window and feed build time.
- **Freshness:** Feature flags identify discharge older than 6, 24 and 72
  hours. BRIM draws only stations with a current discharge or stage value.
- **QA/empty policy:** The script validates only the static station-index count.
  The workflow declares a minimum latest-discharge count and a no-degraded
  policy, but the producer does not read those variables. If latest retrieval
  is empty, all static stations can still be serialized with no current value.
- **Attribution:** USGS provisional data, with official station/rating links
  and optional CNRFC/HADS/APRFC links.
- **Known gap:** This is the highest-priority current reliability defect. It is
  a latent integrity risk rather than a confirmed active outage at the audited
  baseline; documentation must not imply the declared current-value threshold
  is enforced.

## 9. USGS groundwater

- **Purpose:** Latest/recent groundwater field measurements for a curated
  California-centered candidate index, with optional compact history context.
- **Workflow:** [`.github/workflows/build-usgs-groundwater-latest-ca.yml`](../.github/workflows/build-usgs-groundwater-latest-ca.yml).
- **Producer:** [`scripts/build_usgs_groundwater_latest_ca.R`](../scripts/build_usgs_groundwater_latest_ca.R).
- **Source:** USGS Water Data API field measurements; optional index fallback
  and locally prepared history summary.
- **Canonical outputs:**
  [`docs/data/usgs_groundwater_latest_ca.geojson`](data/usgs_groundwater_latest_ca.geojson)
  and
  [`docs/data/usgs_groundwater_latest_ca_summary.json`](data/usgs_groundwater_latest_ca_summary.json).
- **Format/domain/CRS:** Point GeoJSON in longitude/latitude.
- **Principal units:** depth to water in feet below ground/land surface where
  applicable; source units and vertical datum are retained.
- **Time model:** Measurement date/time, last-modified time, query window,
  measurement age and feed build time.
- **Freshness:** BRIM distinguishes measurements within 90 days, one year and
  two years. These are display/selection bands, not a claim that groundwater
  changes on an hourly cadence.
- **QA/empty policy:** The station index, API result and final feature set each
  have minimum count checks. Optional index fallback does not remove the API
  minimum gate.
- **Attribution:** USGS Water Data; values are measurements, not storage
  calculations.
- **Known gaps:** No manifest/schema version; candidate-index and optional
  history ownership cross repository boundaries.

## 10. SCAN soil moisture

- **Purpose:** Latest USDA NRCS SCAN soil-moisture values by depth with
  current-water-year traces and historical context.
- **Workflow:** [`.github/workflows/build-scan-soil-moisture-latest.yml`](../.github/workflows/build-scan-soil-moisture-latest.yml).
- **Producer:** [`scripts/build_scan_soil_moisture_latest.R`](../scripts/build_scan_soil_moisture_latest.R).
- **Source:** USDA NRCS AWDB.
- **Scheduled writer-managed outputs:** Latest GeoJSON, latest summary,
  current-WY trace, trace summary, and depth style.
- **Consumer/operations roles:** Latest GeoJSON, summary, and trace are
  canonical consumer products; trace summary is producer/QA metadata; depth
  style is consumer context.
- **Separately managed context:** Water-day percentiles, monthly context, and
  prior-WY fallback traces are consumer context that the scheduled writer does
  not rebuild.
- **Format/domain/CRS:** Station GeoJSON plus CSV context; longitude/latitude.
- **Principal units:** volumetric soil moisture percent, depth in inches,
  elevation in feet.
- **Time model:** Observation UTC/Pacific, observation date/age, water year and
  feed build time.
- **Freshness:** Current status is expressed in product fields and BRIM
  presentation; no formal common manifest threshold exists.
- **QA/empty policy:** Endpoint preflight, canary requests, retry behavior,
  parsed-row checks and a minimum station count protect publication.
- **Attribution:** USDA NRCS AWDB/SCAN.
- **Known gaps:** Split ownership and provenance for static context; no common
  manifest or checksum.

## 11. Snow-pillow SWE

- **Purpose:** Latest snow-water equivalent and snow depth with current-WY and
  historical station context.
- **Workflow:** [`.github/workflows/build-snow-pillow-latest.yml`](../.github/workflows/build-snow-pillow-latest.yml).
- **Producer:** [`scripts/build_snow_pillow_latest.R`](../scripts/build_snow_pillow_latest.R).
- **Sources:** USDA NRCS AWDB/SNOTEL and CDEC snow sensors.
- **Scheduled writer-managed outputs:** Latest GeoJSON, latest summary,
  current-WY trace, and trace summary.
- **Consumer/operations roles:** Latest GeoJSON, summary, and trace are
  canonical consumer products; trace summary is producer/QA metadata.
- **Static/manual context:** Water-day percentiles, monthly context,
  prior-WY fallback traces, and normal medians are consumer context that the
  scheduled writer does not rebuild.
- **Format/domain/CRS:** Station GeoJSON plus CSV context in
  longitude/latitude.
- **Principal units:** SWE and snow depth in inches; age in days.
- **Time model:** Latest SWE/snow-depth dates, water day/year, provider fetch
  window and Pacific feed build time.
- **Freshness:** BRIM classifies 0–2 days as fresh, 3–7 as recent, 8–21 as
  stale, and more than 21 days as very stale.
- **Fallback:** CDEC adjusted SWE element 82 is preferred; element 3 may fill a
  limited recent tail or act as current-WY fallback. Historical context remains
  conceptually distinct. At the provider-family level, if exactly one of AWDB
  or CDEC fails retrieval or provider QA, the producer refreshes the healthy
  provider and carries forward the failed provider's validated prior latest and
  trace rows unchanged.
- **QA/empty policy:** AWDB and CDEC are attempted and validated independently.
  Provider row/site minimums, required fields, identifiers, dates, SWE bounds,
  AWDB depth coverage, CDEC source selection, duplicate protection, and combined
  latest/trace checks stop unusable publication. Both-provider failure, or one
  provider failure without a valid four-file prior product set and valid prior
  rows for that provider, stops without replacing any output.
- **Provider-completeness baseline and gates:** A read-only audit of 20 recent
  successful tracked products from July 18–25, 2026 found the following normal
  current-WY coverage:

  - NRCS/SNOTEL indexed 175 stations and observed 164 (93.71%) in every
    product. Expected station-days ranged from 50,925 to 52,150; valid SWE rows
    ranged from 47,493 to 48,674, or 92.92%–93.33%.
  - CDEC indexed 129 stations and observed 123 (95.35%) in every product.
    Expected station-days ranged from 37,539 to 38,442; valid SWE rows ranged
    from 33,876 to 34,675, or 89.93%–90.26%.

  The default NRCS gates require at least 90% of indexed stations and 90% of
  expected station-days. The default CDEC gates require at least 92% of indexed
  stations and 85% of expected station-days. Expected station-days are indexed
  stations multiplied by days in the requested fetch window. The thresholds
  leave 2.92–4.93 percentage points below the observed healthy row minima and
  3.35–3.71 points below the observed healthy station fractions, while responses
  near 20% fail both gates.
- **Completeness overrides:** Fraction overrides
  `SNOW_PILLOW_QA_MIN_SNOTEL_STATION_FRACTION`,
  `SNOW_PILLOW_QA_MIN_SNOTEL_ROW_FRACTION`,
  `SNOW_PILLOW_QA_MIN_CDEC_STATION_FRACTION`, and
  `SNOW_PILLOW_QA_MIN_CDEC_ROW_FRACTION` must be finite, greater than zero, at
  most one, and no lower than the defaults. Existing absolute provider row/site
  overrides must be positive whole numbers, may not fall below the derived
  fraction gate, and may not exceed the indexed-station or expected-station-day
  maximum. Invalid or protection-weakening overrides stop the build.
- **Refresh metadata and timestamps:** The latest and trace summaries contain an
  additive `refresh` object describing full/partial mode, provider fetch and QA
  state, refreshed/carried-forward actions and row/site counts. Carried-forward
  station and trace rows retain their prior observation, source, value and
  row-level build/fetch fields; they are not restamped as new observations.
- **Local replacement:** All four writer-managed outputs are constructed and
  validated in a staging directory before complete-set replacement is attempted.
- **Attribution:** USDA NRCS and California DWR/CDEC.
- **Known gaps:** Split static-context ownership, no manifest/schema version,
  no common provenance/checksum envelope, and no filesystem primitive that can
  atomically rename four paths in one operation; replacement retains backups and
  restores already-promoted members if a later promotion fails.

## Nonproduction workflows

The manual HRRR sandbox writes diagnostic files and an Actions artifact only.
The weekly/manual USGS groundwater candidate preview writes a review artifact
only. The wind watchdog reads wind manifests/state and can dispatch ASOS/AWOS,
GFS, HRRR, and NBM writers on the selected ref; it does not itself commit or
publish repository data.

These workflows must not be described as a twelfth product family.

## Change triggers

Update this inventory when any of the following changes:

- a workflow, cadence, entry script or upstream source;
- an official, compatibility or static-context path;
- a manifest or serialized field contract;
- a BRIM entry point or freshness/fallback rule;
- an observed product class changes from active to compatibility/deprecated;
- an empty-result or last-known-good guard changes;
- a material payload or runtime trend changes operational planning.
