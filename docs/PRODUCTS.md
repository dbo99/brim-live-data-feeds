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

Schedules below are UTC unless a row explicitly names a native IANA timezone.
The two major-basin workflows use `America/Los_Angeles`: CNRFC runs at 10:56
and 16:56 Pacific, and CBRFC runs at 08:14 Pacific. The native timezone follows
Pacific standard/daylight changes. A scheduled writer also supports
`workflow_dispatch` unless noted otherwise.

| Family | Declared product ID | Provider | Writer cadence | Preferred BRIM entry point | Formal manifest |
|---|---|---|---|---|---|
| CDEC reservoir | Not declared | California Data Exchange Center, with limited CNRFC and USACE context | Every 3 hours at :29 | `docs/data/cdec_reservoir_latest.geojson` | No |
| Major water-supply basin forecasts | `major_water_supply_basin_forecasts` | California-Nevada River Forecast Center | 10:56 and 16:56 via native `America/Los_Angeles` schedule | `docs/data/major_water_supply_basin_forecasts.json` | No |
| CBRFC Colorado River water-supply forecasts | `cbrfc_major_water_supply_forecasts` | Colorado Basin River Forecast Center | 08:14 via native `America/Los_Angeles` schedule | `docs/data/cbrfc_major_water_supply_forecasts.json` | No |
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
| Winter Storm Levels | `winter_storm_levels` | NOAA/NWS/NCEP NBM | Manual-only dry run or approved `main` publication; proposed 01:11, 07:11, 13:11, 19:11 after first-release approval | `docs/data/winter-storm-levels/winter_storm_levels_manifest.json` | Yes |

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
| Winter Storm Levels live prototype | 11 NBM valid-time targets; 17-68 contour features per target | About 0.9 MB plus a 14-KB manifest |

GFS, HRRR, NBM wind, and Winter Storm Levels are rolling multi-file products. Their total size changes
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
| Major water-supply basin forecasts | `docs/data/major_water_supply_basin_forecasts.json` | Canonical geometry-free consumer product after the first accepted writer run; the implementation does not seed a hand-authored or live-test payload |
| CBRFC Colorado River water-supply forecasts | `docs/data/cbrfc_major_water_supply_forecasts.json` | Separate canonical geometry-free consumer product after its first accepted writer run; the implementation does not seed a hand-authored or live-test payload |
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
| Winter Storm Levels | `docs/data/winter-storm-levels/winter_storm_levels_manifest.json`<br>`docs/data/winter-storm-levels/nbm/snow-level/*.geojson` | Manifest and selected rolling target become canonical after the first approved publication. No live-test payload is seeded or committed by this implementation change. Attempt diagnostics and standalone QA remain workflow artifacts/nonproduction files. |

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
- **Source:** USGS Water Data API field measurements, normally through
  `dataRetrieval` with a direct OGC request as the per-chunk transport fallback;
  optional candidate-index value fallback and locally prepared history summary.
  `USGS_GW_ALLOW_INDEX_FALLBACK=true` controls only the later value fallback for
  sites without a usable API measurement. It does not enable the direct request
  path and cannot bypass API retrieval QA.
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
  have minimum count checks; the API and output thresholds remain 300 sites.
  Every 60-site outer chunk must finish as usable data or a valid empty response,
  and valid-empty chunks still fail publication when the aggregate API minimum
  is not met. Comparable healthy July 25, July 27 and July 29, 2026 runs
  completed all 38 outer chunks, supporting the 100% chunk-completeness rule.
  After one primary client attempt, the direct fallback retries only transport
  errors and HTTP 408, 429, 500, 502, 503 and 504, using at most three direct
  attempts with bounded exponential backoff, jitter and capped `Retry-After`.
  HTTP 4xx responses other than 408 and 429, malformed payloads and schema
  mismatches are not retried. Three
  consecutive terminal chunk failures stop further requests and record the
  remaining chunks as not attempted. Logs aggregate structured, sanitized
  request outcomes and parser/filter counts. Optional index fallback does not
  remove the API minimum or all-chunk-completeness gates. The GeoJSON and summary
  are staged, validated as one prospective product set, and promoted with
  rollback if either replacement fails.
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

## 12. Major water-supply basin forecasts

- **Purpose:** Direct CNRFC water-year and April-July seasonal forecast values
  for BRIM's 15 reviewed major FNF basin watersheds and three water-supply
  indices, plus direct short-range accumulated-volume forecasts for the 15
  individual major basins. The feed carries no polygon or other geometry.
- **Workflow:** [`.github/workflows/build-major-water-supply-basin-forecasts.yml`](../.github/workflows/build-major-water-supply-basin-forecasts.yml).
- **Producer:** [`scripts/build_major_water_supply_basin_forecasts.R`](../scripts/build_major_water_supply_basin_forecasts.R),
  with source/parser helpers in
  [`scripts/cnrfc_major_water_supply_basin_forecast_helpers.R`](../scripts/cnrfc_major_water_supply_basin_forecast_helpers.R)
  and schema, state, freshness and publication policy in
  [`scripts/cnrfc_major_water_supply_basin_forecast_state.R`](../scripts/cnrfc_major_water_supply_basin_forecast_state.R).
- **Reviewed source roster:**
  [`data/input/cnrfc_major_water_supply_basin_forecast_sources.csv`](../data/input/cnrfc_major_water_supply_basin_forecast_sources.csv)
  contains 51 enabled identities. Durable identity combines RFC, five-character
  NWS LID and product type: 15 `CNRFC:<LID>:WY_FNF` records, direct
  `CNRFC:SACC0:WY_INDEX`, `CNRFC:VNSC0:WY_INDEX` and
  `CNRFC:MLIC0:WY_INDEX` records, 15
  `CNRFC:<LID>:10D_VOLUME_ACCUM` records with product type
  `ten_day_streamflow_volume_accumulation`, and 18
  `CNRFC:<LID>:APR_JUL_VOLUME` records with product type
  `april_july_streamflow_volume_forecast`. Product 2 is intentionally absent
  for the three index LIDs and generalized Colorado geometries. The roster
  retains the reviewed selection provenance: the 15
  `fnf_forecast_watershed`/`major_basin` rows and three
  `fnf_index_watershed`/`index` rows; it does not contain geometry.
- **Source:** Each water-year record is retrieved from its official CNRFC
  `ensembleProduct.php?id=<LID>&prodID=9` page. Those server-rendered pages carry
  the labeled `Median Forecast`, `% of Mean`, `% of Median`, `Issuance Time` and
  water-year context needed by the product. The official bulk AWIPS products do
  not supply the complete percent-of-median contract and are not used as a
  substitute. Each short-range record uses the matching official `prodID=2`
  page and its `Tabular 10-Day Streamflow Volume Accumulation` table. Each
  April-July record cross-checks the official `prodID=7` headline against the
  matching water-year tabular page.
- **Direct-source rule:** Forecast volume, units, percent of mean and percent of
  median are copied from the matching labeled page values. The producer does not
  calculate normals, percentages, basin sums, index values or any hydrologic
  forecast arithmetic. Product-2 values come directly from the third, fifth and
  tenth ordered `50% (Median)` cells and the third and fifth ordered `CNRFC
  Deterministic Forecast` cells. The producer does not derive daily or
  incremental values, accumulate flow rates, read ensemble members, or infer a
  deterministic Day 10. The three indices are requested from their own product-9
  pages. Product-7 `forecast_volume`, `percent_average`, `percent_median` and
  `normal_average_volume` are copied from source labels. `percent_average` is
  the legacy wire key representing percent of mean; `normal_average_volume` is
  the legacy wire key representing mean reference volume. The headline's direct
  `Percent of Mean` value is cross-checked against the tabular percent-of-mean
  value, while the separate headline `Percent of Median` value is retained as
  `percent_median`. The producer does not calculate a percentage, mean reference
  volume or forecast volume.
- **Canonical output:**
  `docs/data/major_water_supply_basin_forecasts.json`. An accepted scheduled
  writer run creates the path when absent and otherwise promotes only a complete
  validated candidate.
- **Format and schema:** Geometry-free JSON schema version `1.0` with
  `product_id: "major_water_supply_basin_forecasts"`, stable
  `roster_version: "cnrfc-major-water-supply-v1.1.0"`, `generated_at`,
  `publication_mode`, exact expected/actual record counts, a reconciled
  `source_summary`, independent `family_health`, operational notices, and 51
  deterministically ordered `records`.
  Adding the direct Product-7 `percent_median` metric is an additive schema-1
  change: forecast keys, record count and roster order are unchanged, so neither
  version advances. The reader upgrades a legacy schema-1 prior that lacks only
  this field to an explicit null/unavailable metric in memory before validation;
  every newly built Product-7 record must use the complete four-metric shape.
  Product-9 records set `forecast_statistic: "median"` because the source label
  is `Median Forecast`; `forecast_volume` must not be reinterpreted as another
  ensemble statistic. Product-2 records carry
  `day_3_median_volume`/`day_3_median_valid_date`, Day 5 and Day 10 median pairs,
  Day 3 and Day 5 deterministic pairs, `normalized_units`, exact `source_units`,
  `source_data_updated_at` and optional separate `forecast_issued_at`. They never
  carry a deterministic Day 10 field. Product-2 values displayed by CNRFC to
  one decimal place are rounded to one decimal place before numeric JSON
  serialization. Product-7 records set
  `forecast_statistic: "50_percent_exceedance"`,
  `forecast_period: "April-July"`, `normalized_units: "kaf"`, exact
  `source_units`, and direct
  `forecast_volume`, `percent_average`, `percent_median` and
  `normal_average_volume`. Product-7 primary-metric order in records and exact
  `metric_state` key order is `forecast_volume`, `percent_average`,
  `percent_median`, `normal_average_volume`. Records
  preserve `last_successful_retrieval_at` separately
  from `last_attempt_at`; source issue/update times remain the forecast-time
  authority. Missing numerics use JSON null, never a substituted zero. Git
  commit time and Pages availability remain publication times outside the payload.
- **Product-2 table validation and units:** The parser requires one product-2
  identity label, one usable accumulated-volume table, exactly ten ordered date
  columns, one semantic `50% (Median)` row, at most one semantic deterministic
  row, and recognizable units. It preserves the exact heading units and maps
  only the reviewed `1000s of Acre-Feet`, `1000s Acre-Feet`, `1000s of Ac-Ft`
  and `1000s Ac-Ft` labels to `kaf`. Date fields come from the ordered table
  headings; `source_data_updated_at`, or `forecast_issued_at` only as a fallback,
  anchors the omitted year. Construction permits at most one unambiguous
  calendar-year rollover and never uses retrieval/current date. Median cumulative values and the
  deterministic Day 3/Day 5 pair may not decrease by more than the 0.1-kaf
  source-display rounding tolerance. Numeric zero remains valid.
- **Product-7 validation:** The semantic parser requires one matching product-7
  identity, one headline `Median Forecast` with its directly published volume,
  `Percent of Mean` and `Percent of Median`, one tabular seasonal-trend table,
  and one ordered `50% Exceed` row. It associates the latest ordered
  forecast-date column with the source issue, mean reference volume and forecast
  volume, then
  cross-checks headline and tabular 50%-exceedance volumes within the 0.05-kaf
  source-display rounding tolerance and headline `Percent of Mean` against
  the tabular percent-of-mean value. The direct median percentage is not derived
  or substituted for the mean reference volume. Parser/signature identity
  `cnrfc_prod7_semantic_v2` binds all three headline values. Duplicate
  labels/tables, wrong identities, unrecognized units, malformed numbers or
  material conflicts fail validation. A recognized unavailable page produces
  an explicit unavailable structural record; an explicitly missing median
  percentage produces a null unavailable metric state without becoming zero.
- **Metric and record state:** Every displayable number has a same-name entry in
  `metric_state` with `status`, `value_origin`, both source-time slots,
  `valid_through`, `stale_since`, `map_eligible`, `popup_eligible` and
  `missing_reason`. Metric statuses are `current`, `source_stale`,
  `stale_last_known_good`, `expired`, `unavailable` and `failed_no_data`;
  origins are `current_source`, `last_known_good` and `none`. Only a validated
  `current` number is map eligible. Stale/LKG/expired numbers remain eligible for
  a clearly labeled popup, while null metrics are eligible for neither.
  Record status is derived from those metric states and may be `current`,
  `current_partial`, `source_stale`, `stale_last_known_good`, `expired`,
  `unavailable` or `failed_no_data`.
- **Freshness and expiry:** Product 9 uses `forecast_issued_at`: it becomes
  source-stale after the configurable 72-hour starting threshold and expires
  after the configurable 168-hour/seven-day threshold. A mismatched Pacific
  water year is also source-stale until hard expiry. Product 2 uses
  `source_data_updated_at`, falling back only to `forecast_issued_at`; it becomes
  source-stale after the configurable 36-hour starting threshold. Each product-2
  metric expires at the first Pacific midnight after its actual table valid
  date. Product 7 independently becomes source-stale 36 hours after
  `forecast_issued_at` and every seasonal metric expires at August 1 00:00
  America/Los_Angeles for its water year. Product-7 season state is active from
  October through July; a valid explicit no-current-issue response may seed an
  out-of-season unavailable structural record. Retrieval/build time never
  extends source freshness or a horizon.
- **Attempt taxonomy:** `attempt_outcome` is `success`, `source_unavailable`,
  `fetch_failed`, `parse_failed` or `validation_failed`; `failure_stage` is null
  for success and otherwise `source`, `fetch`, `parse` or `validate`.
  `unavailable` is reserved for source-confirmed missing values. Transport/HTTP
  failures are fetch failures, unrecognized HTML is a parse failure, and
  recognized semantic/unit/cumulative/source-time failures are validation
  failures. Diagnostics bound error text and preserve the exact stage.
- **Bootstrap and steady state:** With no valid prior payload, bootstrap requires
  all 15 FNF records, all three direct index records, complete median coverage
  for the 14 currently expected-available product-2 basins, and explicit
  source-confirmed product-2 unavailability for MHBC1. Missing deterministic
  rows reduce separately reported deterministic coverage but do not fail a
  valid median bootstrap. Bootstrap also requires all four direct metrics for
  all 18 product-7 records to be valid current/source-stale seasonal data while
  the family is active, or explicit source-confirmed unavailability while it is
  out of season. With a
  valid prior payload, every attempt constructs and validates all 51 structural
  records. Families merge independently and
  outages publish honest last-known-good, expired, unavailable or
  failed-no-data states; ordinary current-success counts are health signals, not
  steady-state blockers. Only roster/schema/serialization/staged-validation or
  other structural failure preserves the prior bytes without replacement.
- **Family health:** `family_health` contains separate summaries for
  `water_year_fnf`, `water_year_index` and
  `ten_day_streamflow_volume_accumulation`, and
  `april_july_streamflow_volume_forecast`, including expected structural and
  available counts, every record-status count, successful/failed attempts and
  `healthy`, `degraded`, `outage_using_last_known_good` or `unusable` health.
  Product 2 reports median and deterministic success coverage separately;
  product 7 reports `season_state`; valid new MHBC1 product-2 data emits a
  roster-availability notice.
- **No-op behavior:** An unchanged successful source issue/signature/value/state
  retains its prior `last_successful_retrieval_at` and `last_attempt_at`.
  Repeated identical outages also retain the canonical attempt time; current QA
  still reports the latest check. `generated_at` changes only when a substantive
  payload is promoted. Source issue, value, signature, status, outcome, failure,
  stale and expiry transitions are substantive.
- **Schedule:** GitHub Actions uses the native IANA timezone schedule
  `56 10,16 * * *` with `America/Los_Angeles`, so the two daily runs are intended
  for 10:56 AM and 4:56 PM Pacific through DST changes. There is no runtime
  exact-minute guard; delayed starts still run. Each run records a stable
  Pacific logical slot (`YYYY-MM-DD-AM` or `YYYY-MM-DD-PM`).
  Product 2 may update more frequently during storms; a faster cadence is a
  future reviewed decision and is not implemented here.
- **Manual execution:** `workflow_dispatch` defaults to a runner-temporary dry
  run. A feature-branch dispatch cannot publish. An explicit publish input is
  honored only on `main`; scheduled publication also runs only from the selected
  `main` branch tip. Shared writer concurrency, exact-path staging, normal
  non-force push and branch-advance rejection remain in force. An official
  manual dispatch still requires maintainer approval under the operations runbook.
- **Fixtures:** Focused excerpts and fuller sanitized representative pages under
  `tests/fixtures/cnrfc_forecasts/` cover product-9 basin/index, product-2 normal
  and explicit unavailability, product-7 basin/index and explicit unavailability,
  an HTTP-200 maintenance page, plus missing, zero, duplicate, malformed,
  mismatch, rollover, unit and source-time adversarial cases.
  Refresh a fixture only after reviewing
  the corresponding official page. Retain only the source identity and semantic
  labels, issue/update time, ordered headers and necessary source values; remove
  unrelated navigation, chart series and personal/local details. Then rerun
  both CNRFC test files. Never derive fixture values through arithmetic.
- **Browser relationship:** The separate BRIM implementation owns the reviewed
  static polygons and joins by `forecast_key`. The live feed owns values and
  status only. The browser must not infer a polygon from the feed or join by LID
  alone. Individual major-basin modes may expose water-year percent of median or
  mean; median accumulated volume through Day 3, 5 or 10; and deterministic
  accumulated volume through Day 3 or 5. Index and generalized Colorado
  polygons remain neutral/unavailable for the product-2 modes. Labels must say
  cumulative accumulated volume, show units and link to the record's product-2
  source as `CNRFC 10-day accumulated-volume forecast`; raw kaf must not be
  described as percent of mean or normalized storm intensity. Product-7 and the
  separate CBRFC GLDA3 April-July record are comparable seasonal-volume sources
  for a California-Colorado supply comparison; the CBRFC water-year record is a
  distinct period. Source-specific periods and mean/median terminology must remain
  visible. Consumer polygons are reviewed display
  geometry, not exact RFC model-basin geometry.

## 13. CBRFC Colorado River water-supply forecasts

- **Purpose and boundary:** Three independent official CBRFC forecast records:
  two existing GLDA3 Lake Powell/Glen Canyon Dam records and one LKSA3 Lake Mead
  local-intervening monthly series. They remain in a separate payload, source
  adapters, family-health entries and publication transaction from the
  51-record CNRFC product. A direct total Lake Mead unregulated-inflow record is
  not included: the reviewed official `LKSA3` structured water-supply request
  currently returns literal `false`, the Lower Colorado list returns no matching
  row, and the public graph contains no direct value. The producer must never
  replace that missing product with Powell-plus-local arithmetic, releases,
  storage changes or other operational values. No geometry or coordinates are
  included.
- **Workflow and producer:**
  [`.github/workflows/build-cbrfc-major-water-supply-forecasts.yml`](../.github/workflows/build-cbrfc-major-water-supply-forecasts.yml)
  calls
  [`scripts/build_cbrfc_major_water_supply_forecasts.R`](../scripts/build_cbrfc_major_water_supply_forecasts.R),
  with parsing in
  [`scripts/cbrfc_major_water_supply_forecast_helpers.R`](../scripts/cbrfc_major_water_supply_forecast_helpers.R)
  and state/publication policy in
  [`scripts/cbrfc_major_water_supply_forecast_state.R`](../scripts/cbrfc_major_water_supply_forecast_state.R).
- **Roster and identities:**
  [`data/input/cbrfc_major_water_supply_forecast_sources.csv`](../data/input/cbrfc_major_water_supply_forecast_sources.csv)
  contains exactly three ordered records: `CBRFC:GLDA3:APR_JUL_WSUP`, product type
  `april_july_water_supply_forecast`, period `Apr 1-Jul 31`; and
  `CBRFC:GLDA3:WATER_YEAR_INFLOW`, product type
  `water_year_unregulated_inflow_forecast`, period `Water Year`; followed by
  `CBRFC:LKSA3:LOCAL_INTERVENING_MONTHLY`, product type
  `lake_mead_local_intervening_monthly_forecast`, period `MONTHLY OUTLOOKS`.
  The GLDA3 records preserve forecast type `Unregulated` and exact source units
  `kaf`. The LKSA3 series preserves exact source labels `ESP 50% EXCEEDANCE`,
  `%Med` and `KAF`, normalized units `kaf`, and identifier `LKSA3 QCMPLCM`.
- **April-July official source strategy:** The authoritative structured point endpoint is
  `espgraph_data_hc.py?id=GLDA3&year=<WY>`. Only official `offdate`, `off50`,
  `offpavg` and `offpmed` fields supply public values. Daily `esp*` guidance and
  a response `dateline` are never substitutes or publication triggers. The
  official list CSV is a secondary QA cross-check: an absent GLDA3 row is
  accepted when the newer point issue is valid, while duplicate or conflicting
  rows fail validation. The human graph page is retained as `source_url`. The
  server-rendered Lake Powell dashboard is also checked for direct Apr-Jul
  forecast-volume and percent-of-mean agreement. Comparison uses half of the
  dashboard's displayed unit as rounding tolerance (for the current integer display, 0.5
  kaf and 0.5 percentage point). A missing, unparseable or older dashboard is an
  operational notice and does not replace a newer complete point record; a
  material same-issue disagreement fails validation. The dashboard HTML exposes
  the table directly and no separate public backend request was identified, so
  it is an official secondary representation, not a demonstrated independent
  measurement source.
- **Water-year official source strategy:** The authoritative CBRFC Upper
  Colorado situational-awareness page is parsed by exact Lake Powell heading,
  ordered table headers and the unique semantic row label `Water Year`. Only its
  directly published forecast-volume and percent-of-mean cells become public
  values. `Obs to Date` is structurally validated but is neither published nor included in the
  semantic signature. The dashboard is both the numerical retrieval and the
  human-facing summary for this record; therefore its summary relationship is
  explicitly non-independent.
  No percent-of-median field is calculated, inferred or exposed because this
  water-year source does not directly provide one.
- **Lake Mead Local official source strategy:** The CBRFC special-products page
  links the current `lakemead.txt` text/CSV product and its dated archive. The
  semantic parser requires the CBRFC issuer, `MONTHLY OUTLOOKS`, date-precision
  issue, exact units/statistic heading, exact ordered six-column header, one
  `LKSA3 QCMPLCM` identity line and exactly 12 consecutive monthly rows starting
  in the issue month. The record contains an ordered `monthly_forecasts` array;
  each item has source `YYYY-MM`, direct volume, direct `%Med`, exact statistic
  and percentage labels, month validity and metric state. It publishes no annual
  or seasonal sum and does not treat the separate calculated-observation or
  historical-mean sections as forecast values. The source identifies the
  first row as the issue month but provides no separate selected active-month
  flag, so consumers select an explicit `forecast_month`.
- **Reviewed source-date correction:** The official July 1, 2026 product
  publishes July-December 2026, then January **2026**, followed by
  February-June 2027. Override
  `CBRFC_LKSA3_LOCAL_JANUARY_ROLLOVER_2026` changes only that raw January label
  to `2027-01`, and only for this exact product/issue/12-row structure. The
  immediately preceding official June 1 archive at
  `espaz_lml/wy26/lakemead.060126.csv` independently confirms January 2027.
  Every item preserves its raw label and explicit applied/ID/reason/evidence/
  prior-issue provenance. More than one bad year, wrong month order, missing or
  duplicate rows, absent/ambiguous prior confirmation, or any other semantic
  drift remains `validation_failed`; volumes, percentages, units, issue date and
  row order are never changed. A top-level operational notice reports the
  correction.
- **Popup source-link roles:** Every record carries reviewed, nonempty
  `source_url`, `retrieval_url`, `summary_url` and `archive_url` roles. GLDA3
  year-specific graph/point URLs are filled from the validated source water year,
  not runner time. LKSA3 uses the current official text as both human product and
  retrieval source, the special-products page as summary, and the official
  archive. Reclamation reservoir-operations links are outside this forecast
  contract.
- **Canonical output and contract:**
  `docs/data/cbrfc_major_water_supply_forecasts.json`, absent until the first
  accepted writer run. Geometry-free schema `1.0`, product ID
  `cbrfc_major_water_supply_forecasts`, roster version
  `cbrfc-colorado-river-v1.3.0`, exact three-record count, separate
  `april_july_water_supply_forecast` and
  `water_year_unregulated_inflow_forecast` families plus
  `lake_mead_local_intervening_monthly_forecast`, aggregate source summary and
  independent record state. Both GLDA3 records directly publish
  `forecast_volume`, exact `source_units`, normalized `kaf`, `percent_average`,
  `forecast_issue_date`, `source_time_precision: "date"`,
  `source_normal_term: "average"`, period, water year, forecast type, URLs,
  signature, diagnostics, role-labeled official URLs, attempt/failure fields and
  metric-level state. Top-level `operational_notices` exposes summary-crosscheck
  limitations and reviewed source corrections. The
  April-July record additionally publishes direct `percent_median` and
  `forecast_statistic: "50_percent_exceedance"`; the water-year record uses
  `forecast_statistic: "official_full_forecast"` and has no `percent_median`
  key. Neither record fabricates an issue timestamp or calculates a value.
  The LKSA3 record instead publishes the ordered monthly array and its own
  source/retrieval/summary/archive URLs; it has no
  record-level aggregate volume or percentage.
- **Sentinels and validation:** For April-July, blank `offdate` plus zero
  official values means source-unavailable, while a dated zero is valid. For
  water year, a wholly blank/sentinel row is source-unavailable; partial
  missing values, malformed cells, duplicate headings/tables/rows or semantic
  drift fail validation. Both adapters reject wrong identity/year/units/type or
  period, negative/nonfinite values, oversized responses and backward source
  dates. Missing `off*` fields never fall back to `esp*` guidance.
  LKSA3 additionally rejects missing/duplicate months, unreviewed month/year rollover,
  unknown units/statistics, malformed percentages and an ambiguous source
  identifier. A dated numeric zero is valid.
- **Freshness and expiry:** The two GLDA3 monthly official sources use a reviewed
  threshold of 40 calendar days after date-precision
  `forecast_issue_date`. April-July metrics expire at August 1 00:00
  America/Los_Angeles for the forecast water year; water-year metrics remain
  valid through September 30 and expire at October 1 00:00. Retrieval time does
  not extend either window. LKSA3 has its own configurable 40-calendar-day
  threshold, reviewed from the current archive's approximately monthly issue
  cadence; historical archive years may also contain mid-month issues. Each
  monthly item is `not_yet_valid` before its month-start Pacific midnight,
  becomes map eligible only during its own month while the source remains
  current, and expires at the next month-start. Future and expired values remain
  popup provenance but are not map eligible.
  The monthly series is year-round; there is no inherited August 1 or October 1
  record expiry. Unchanged official date/value/signature/state is a semantic
  no-op even when retrieval-only, Paria, calculated-observation or historical
  context changes.
- **Bootstrap and steady state:** Each family is fetched, parsed, validated and
  reconciled independently. Bootstrap may establish the payload when any
  official family succeeds; other records remain honest `failed_no_data` or
  source-unavailable structural records. If no family establishes data,
  bootstrap fails unless every inactive family is explicitly unavailable and no
  active family remains unestablished. With a valid prior, one family may advance
  while the others retain only their own validated provenance as current,
  source-stale, last-known-good, expired, unavailable or failed-no-data. Unsafe
  three-record structure/schema/serialization/staging retains prior CBRFC bytes;
  no CBRFC outcome modifies CNRFC bytes.
- **Observed-to-date decision:** The dashboard's two observed-to-date volumes
  remain available through `summary_url` but are deferred from the first-release
  payload. No separate structured source or independent timestamp was found,
  and they are not forecast metrics or publication triggers.
- **Final total-Lake-Mead check:** The Special Forecast Products page, Lake
  Powell and Arizona/Lower Colorado dashboards, reservoir listings, current
  Lake Mead Local product/archive, and direct `LKSA3` structured request were
  reviewed on July 31, 2026. The structured request still returns literal
  `false`; the other sources provide local flow, Powell inflow, or reservoir
  conditions but no direct dated total-Lake-Mead period/volume/statistic/unit
  contract. The fourth record remains deferred.
- **Schedule and publication:** Native `America/Los_Angeles` schedule
  `14 8 * * *` checks once daily at 08:14 Pacific, separate from CNRFC's two
  daily runs and from the existing minute marks in this repository. Manual runs
  default to temporary dry run; feature branches cannot publish; scheduled or
  explicitly requested publication is restricted to selected `main` tip. The
  workflow uses shared non-cancelling writer concurrency, exact-path staging,
  normal non-force push and branch-advance rejection.
- **Comparison and display semantics:** The CBRFC April-July record and CNRFC product 7 both
  provide direct seasonal 50%-exceedance volumes in kaf. The independent CBRFC
  water-year record is a directly published full-forecast volume, not a renamed
  April-July value. The LKSA3 monthly series is local flow below Glen Canyon Dam
  and is neither GLDA3 inflow nor total inflow above Lake Mead. BRIM may compare
  these only with official period, statistic, issue precision and mean/median
  terminology visible. Suggested future context is HUC2 14 for Powell and an
  optional outline-only HUC2 14+15 view with no values. HUC2 15 is not a
  defensible local-intervening forecast polygon because it extends below Lake
  Mead and includes unrelated drainage. Prefer a reviewed generalized outline
  assembled from official CBRFC Lake Mead Local basin/segment boundaries or
  reviewed USGS WBD subbasins below Glen Canyon and above Lake Mead. If a direct
  total product becomes available later, its contributing area contains the
  Powell area; use mutually exclusive thematic modes rather than overlapping
  fills. Consumer geometry remains generalized display context, never exact RFC
  forecast-basin geometry.

## 14. Winter Storm Levels

- **Purpose:** Browser-ready modeled snow-level contours for winter-storm
  operations across California, southern Oregon, Nevada, northwest Arizona, the
  lower Colorado corridor, and adjacent Pacific waters.
- **Workflow and producer:**
  [`.github/workflows/build-winter-storm-levels.yml`](../.github/workflows/build-winter-storm-levels.yml)
  calls [`scripts/build_winter_storm_levels.R`](../scripts/build_winter_storm_levels.R),
  with pure retrieval, contour, manifest, validation, and promotion helpers in
  [`scripts/winter_storm_levels_helpers.R`](../scripts/winter_storm_levels_helpers.R).
  The config is
  [`data/input/winter_storm_levels_config.csv`](../data/input/winter_storm_levels_config.csv).
- **Source and definition:** Deterministic `SNOWLVL` from NOAA/NWS/NCEP NBM
  CONUS Core. The controlling MDL/NWS definition is elevation where wet-bulb
  temperature reaches 0.5 degrees C. Source values are metres MSL and are
  converted to feet MSL. This is not the GFS/HRRR 0-degree-C freezing level.
- **Retrieval:** Exact anonymous byte ranges from the NOAA NBM Open Data bucket,
  identified through exact `.idx` records. NOMADS is recorded as the official
  alternative. The publisher requires all configured horizons from one
  00/06/12/18 UTC cycle.
- **Canonical entry point and rolling set:**
  `docs/data/winter-storm-levels/winter_storm_levels_manifest.json` and its
  relative targets beneath `docs/data/winter-storm-levels/nbm/snow-level/`.
  The paths are intentionally absent until the first approved publication.
  Target filenames include a short content-hash suffix so a same-cycle revision
  cannot overwrite bytes still referenced by an older manifest. Consumers must
  never select by directory listing or filename guessing.
- **Forecast targets:** Hours 1, 6, 12, 18, 24, 30, 36, 42, 48, 60, and 72.
  Two cycles are retained after successful advancement; newest-cycle entries
  win when valid times overlap.
- **Format/domain/CRS:** Each target is a WGS84 GeoJSON FeatureCollection of
  LineStrings clipped exactly to `[-130, 30, -112, 44.5]`. The source is cropped
  with a larger configured buffer. Published contours are 0-20,000 feet MSL at
  1,000-foot intervals, with 750-m projected simplification and five-decimal
  coordinates. Only levels actually crossing the domain appear.
- **Feature contract:** Every feature carries stable product/source/parameter,
  snow-level definition, `level_ft_msl`, label/unit, source cycle, valid time,
  forecast lead, segment, and length properties. Retrieval/build/publication
  timestamps are not repeated on features.
- **Manifest contract:** Schema/contract version, accepted status, exact NOAA source
  attribution/definition/units, domain, contour settings, freshness thresholds,
  source cycle, retrieval/publication times, expected-versus-actual complete-set
  diagnostics, and target entries. Every target
  entry carries cycle/valid/validity-window/lead, exact retrieval and inventory
  provenance, relative path, media type, SHA-256, bytes, feature count, emitted
  levels, source-grid coverage/range, and output bounds.
- **Freshness and expiry:** A target is active within three hours of its valid
  time. Cycle age is current through nine hours, delayed-but-usable through 15,
  stale-last-known-good through 24, and expired afterward. No active target also
  means expired. Consumers recompute status from source/valid times; retrieval,
  publication, commit, and Pages times do not extend validity.
- **NoData and below-ground interpretation:** GRIB NoData `9999` becomes
  missing. Finite zero and small negative source values remain valid input, but
  only nonnegative configured contours are published. The publisher does not
  terrain-mask or replace the NBM-derived field. Where terrain exceeds the
  snow-level height, consumers should explain the transition as at/near the
  surface rather than imply a useful below-ground level.
- **QA/failure policy:** All 11 inventories, exact record identity, range
  response, single-layer decode, metre unit, valid time, CRS, at least 95%
  finite source coverage, physical range, contour metadata/bounds, manifest
  paths/checksums/sizes, feature metadata, bounds, complete horizon set, and
  unique cycle/valid identities must pass. Source unavailable, variable missing,
  fetch, decode, validation, and publication failure remain distinct. Any failure
  retains accepted bytes; new targets promote before a manifest-last atomic
  rename. Transient retries use bounded exponential backoff with jitter and a
  capped numeric `Retry-After`. Retrieval/publication-time-only changes are
  semantic no-ops.
- **Publication controls:** Manual dispatch defaults to a runner-temporary dry
  run, and feature branches cannot publish. Explicit `publish: true` works only
  on `main`. No schedule is active until the maintainer approves the first
  official publication. The proposed collision-resistant cadence is 01:11,
  07:11, 13:11, and 19:11 UTC.
- **Browser QA:** [`qa/winter_storm_levels/index.html`](../qa/winter_storm_levels/index.html)
  is a standalone nonproduction renderer with source/cycle/valid-time selection,
  stale/error display, labeling, optional comparison/overlay loading, and render
  timing. It is not private BRIM code or a canonical feed URL.
- **Attribution and caveats:** NOAA/NWS/NCEP/MDL NBM. Snow level is a modeled
  transition estimate, not surface precipitation type or accumulation. The
  1,000-foot line interval is not model precision. GFS/HRRR multiple freezing
  levels and CNRFC raster guidance remain research comparisons, not silent
  fallbacks. Detailed source evidence is in
  [WINTER_STORM_LEVELS.md](WINTER_STORM_LEVELS.md).

## Nonproduction workflows

The manual HRRR sandbox writes diagnostic files and an Actions artifact only.
The weekly/manual USGS groundwater candidate preview writes a review artifact
only. The wind watchdog reads wind manifests/state and can dispatch ASOS/AWOS,
GFS, HRRR, and NBM writers on the selected ref; it does not itself commit or
publish repository data.

These workflows must not be described as additional product families.

## Change triggers

Update this inventory when any of the following changes:

- a workflow, cadence, entry script or upstream source;
- an official, compatibility or static-context path;
- a manifest or serialized field contract;
- a BRIM entry point or freshness/fallback rule;
- an observed product class changes from active to compatibility/deprecated;
- an empty-result or last-known-good guard changes;
- a material payload or runtime trend changes operational planning.
