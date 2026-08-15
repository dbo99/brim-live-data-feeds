# BRIM producer-consumer contract

This document defines the public interface between this repository and the
separate private BRIM consumer. It documents current accepted paths and
semantics without reproducing private source.

The public producer owns retrieval, transformation, product QA, tracked
publication paths, manifests, and operational build evidence. The private BRIM
consumer owns lazy loading, consumer-side validation, display, source and time
presentation, failure isolation, and in-session fallback.

The human product inventory is [PRODUCTS.md](PRODUCTS.md). Machine-readable
files remain authoritative for the fields they currently serialize.

Baseline audited: 2026-07-24; the audited commit is recorded in
[README.md](README.md).

## Hosting and path rule

The official Pages root is:

`https://dbo99.github.io/brim-live-data-feeds/`

A tracked repository path beneath `docs/` is published without the `docs/`
prefix. For example:

`docs/data/cdec_reservoir_latest.geojson`

is consumed as:

`https://dbo99.github.io/brim-live-data-feeds/data/cdec_reservoir_latest.geojson`

The private BRIM registry centralizes this base URL and constructs product URLs
from Pages-relative `data/...` paths. Their repository counterparts are under
`docs/data/...`.

## Producer-consumer crosswalk

| Family | Producer workflow | Build script | Canonical repository path(s) | Manifest-driven | Private BRIM consumer |
|---|---|---|---|---|---|
| CDEC reservoir | `.github/workflows/build-cdec-reservoir-feed.yml` | `scripts/build_cdec_reservoir_latest.R` | `docs/data/cdec_reservoir_latest.geojson`<br>`docs/data/cdec_reservoir_latest_summary.json` | No | Reservoir Ops helper |
| Major water-supply basin forecasts | `.github/workflows/build-major-water-supply-basin-forecasts.yml` | `scripts/build_major_water_supply_basin_forecasts.R` | `docs/data/major_water_supply_basin_forecasts.json` | No | Coordinated feature-branch layer; static geometry is consumer-owned and integration verification remains required before official publication |
| CBRFC Colorado River water-supply forecasts | `.github/workflows/build-cbrfc-major-water-supply-forecasts.yml` | `scripts/build_cbrfc_major_water_supply_forecasts.R` | `docs/data/cbrfc_major_water_supply_forecasts.json` | No | Coordinated feature-branch layer; separate three-record GLDA3/LKSA3 contract and publication transaction; static geometry is consumer-owned |
| CoCoRaHS | `.github/workflows/build-cocorahs-daily-precip-feed.yml` | `scripts/build_cocorahs_daily_precip_latest.R` | `docs/data/cocorahs_daily_precip_ca_latest.geojson` and summary<br>`docs/data/cocorahs_daily_precip_conus_latest.geojson` and summary | No | CoCoRaHS Ops helper |
| Delta operations | `.github/workflows/build-delta-ops-daily-summary.yml` | `scripts/build_delta_ops_daily_summary.R` | `docs/data/delta_ops_daily_summary_features.geojson`<br>`docs/data/delta_ops_daily_summary_summary.json`<br>`docs/data/delta_ops_x2_reference.geojson` | No | Delta Ops helper |
| GFS wind | `.github/workflows/build-gfs-wind-latest.yml` | `scripts/build_gfs_wind_latest.R` | `docs/data/wind/gfs_surface_wind_feed_manifest.json` and selected target | Yes | Shared wind helper |
| HRRR wind | `.github/workflows/build-hrrr-wind-latest.yml` | `scripts/build_hrrr_wind_latest.R` | `docs/data/wind/hrrr_surface_wind_feed_manifest.json` and selected target | Yes | HRRR wind helper |
| NBM guidance | `.github/workflows/build-nbm-wind-guidance-latest.yml` | `scripts/build_nbm_wind_guidance_latest.R` | `docs/data/wind/nbm_wind_guidance_feed_manifest.json` and selected target | Yes | NBM wind helper |
| ASOS/AWOS | `.github/workflows/build-asos-awos-wind-latest.yml` | `scripts/build_asos_awos_wind_latest.R` | `docs/data/wind/asos_awos_wind_latest.geojson`, summary, and manifest | Both | Observed-wind helper |
| USGS streamflow | `.github/workflows/build-usgs-streamflow-latest-ca.yml` | `scripts/build_usgs_streamflow_latest_ca.R` | `docs/data/usgs_streamflow_latest_ca.geojson`<br>`docs/data/usgs_streamflow_latest_ca_summary.json` | No | Streamflow helper |
| USGS groundwater | `.github/workflows/build-usgs-groundwater-latest-ca.yml` | `scripts/build_usgs_groundwater_latest_ca.R` | `docs/data/usgs_groundwater_latest_ca.geojson`<br>`docs/data/usgs_groundwater_latest_ca_summary.json` | No | Groundwater helper |
| SCAN | `.github/workflows/build-scan-soil-moisture-latest.yml` | `scripts/build_scan_soil_moisture_latest.R` | Latest GeoJSON/summary, current-WY trace, depth style, water-day percentiles, monthly context, and prior-WY fallback under `docs/data/` | No | SCAN helper |
| Snow-pillow | `.github/workflows/build-snow-pillow-latest.yml` | `scripts/build_snow_pillow_latest.R` | Latest GeoJSON/summary, current-WY trace, water-day percentiles, monthly context, prior-WY fallback, and normal medians under `docs/data/` | No | Snow-pillow helper |
| Winter Storm Levels | `.github/workflows/build-winter-storm-levels.yml` | `scripts/build_winter_storm_levels.R` | `docs/data/winter-storm-levels/winter_storm_levels_manifest.json` and selected target | Yes | Separate integration task not started; additive public contract is ready for consumer implementation after publisher readiness review and the canonical path remains absent until first approved publication |

The established relationships were verified from the producer workflow/script,
tracked output, private registry path, and corresponding private helper. The new
forecast rows record coordinated additive contracts being prepared; their
canonical files are intentionally absent until each product's first accepted
writer run, and consumer integration must be verified before official
publication. The
complete repository path classification, including compatibility and producer/QA
files, is maintained in [PRODUCTS.md](PRODUCTS.md).

## Common contract semantics

### Format and compression

- Official browser products are uncompressed JSON, GeoJSON, or CSV served by
  GitHub Pages.
- Actions QA artifacts are ZIP archives and are not product URLs.
- GFS and HRRR wind grids are arrays of U and V records with grid headers and
  data arrays.
- NBM guidance and point products are GeoJSON FeatureCollections. Winter Storm
  Levels targets are GeoJSON line FeatureCollections selected only through their
  manifest.
- CSV context products must retain a header row and product-specific key
  columns used by the consumer.

Changing format, compression, array shape, root JSON type, CSV delimiter or
filename is a contract change.

### Coordinates and CRS

Point GeoJSON and context coordinates are longitude/latitude compatible with
WGS84/EPSG:4326 display. GFS/HRRR grid headers describe their
longitude/latitude grid. Current products do not share a formal machine CRS
field, so documentation must not claim a universal serialized `crs` property.

Changing coordinate order, longitude convention, geometry type, grid scan
orientation or CRS requires coordinated consumer support.

### Null, missing and zero

- JSON null and absent fields are not equivalent to numeric zero.
- Zero precipitation, zero SWE and calm/zero wind can be valid observations.
- Missing storage is not zero storage.
- Missing discharge/stage means the streamflow feature is not drawable as a
  current observation.
- CSV blank cells are missing unless a product explicitly defines otherwise.
- NaN and Infinity must not appear in strict JSON.

Producers should serialize explicit nulls where they are already part of the
product convention. Consumers must not infer zero from absence.

### Time

UTC is the machine and QA standard. Pacific Time
(`America/Los_Angeles`) is the normal user-facing display.

These concepts must remain distinct:

- **Retrieval time:** when the upstream response was obtained.
- **Observation time:** when a station value was observed.
- **Model initialization/reference time:** the model cycle.
- **Forecast valid time:** when a forecast field applies.
- **Build time:** when the producer assembled the product.
- **Publication time:** when an official commit/deployment became available.

Current products do not contain all six concepts and do not name them
uniformly. A missing concept must remain missing or explicit null; build time
must not be relabeled as observation or publication time.

### Attribution and limitations

Provider identity, official source link, provisional-data warning, units and
known product limitations must remain available through product fields,
manifests, summaries or stable documentation. BRIM is a screening and
situational-awareness consumer, not the legal or scientific source of record.

## Verified minimum consumer contracts

These tables record the compact minimum verified from tracked public products,
producer serialization, manifests, and the read-only consumer audit. They are not
JSON Schemas, do not make every producer field mandatory, and do not prevent
additive fields. JSON types are shown explicitly; `number|null` and `string|null`
mean the property is present in the audited product but may be JSON null. CSV
types are logical interpretations because CSV does not encode types.

Paths are repository-relative; Pages removes the leading `docs/`. Observed record
counts and payload sizes are in the
[PRODUCTS.md observed payload snapshot](PRODUCTS.md#observed-payload-snapshot).
They are operational expectations, not hard limits unless a product later
defines a formal limit.

### Point and operational products

| Family and canonical path | Root/geometry | Minimum identity/location | Minimum values and units | Time, status, source, and missing behavior | Companion/compatibility status |
|---|---|---|---|---|---|
| CDEC reservoir<br>`docs/data/cdec_reservoir_latest.geojson` | GeoJSON `FeatureCollection`; Point coordinates `[longitude, latitude]` numbers | `cdec_id:string`, `reservoir_name:string` | `display_storage_af:number|null` acre-feet, `display_storage_maf:number|null`, `display_storage_source:string`; optional `capacity_af:number|null`, `pct_capacity:number|null` | `display_obs_datetime_utc:string|null` is selected observation time; `display_obs_age_hours:number|null`; `feed_build_time_utc:string`; `obs_stale_12h:boolean`, `obs_stale_24h:boolean`; `has_storage_value:boolean`; `source_url:string`. Null storage is missing, not zero. | `docs/data/cdec_reservoir_latest_summary.json` is a canonical JSON status/coverage object; no compatibility path is classified. |
| Major water-supply basin forecasts<br>`docs/data/major_water_supply_basin_forecasts.json` | Geometry-free JSON object; `schema_version:string`, `product_id:string`, `roster_version:string`, `generated_at:string`, `publication_mode:string`, exact expected/actual counts, `source_summary:object`, `family_health:object`, `operational_notices:array`, `records:array` | `forecast_key:string` is the RFC/LID/product identity; `rfc:string`, `nws_lid:string`, `product_type:string`, `display_name:string`. Join only by `forecast_key`. | Water-year variant: `forecast_statistic:"median"`, `forecast_volume:number|null`, `forecast_volume_units:string|null`, `percent_mean:number|null`, `percent_median:number|null`. Ten-day variant: Day 3/5/10 median volume/date pairs, Day 3/5 deterministic volume/date pairs; no deterministic Day 10. April-July variant: `forecast_statistic:"50_percent_exceedance"`, direct `forecast_volume:number|null`, `percent_average:number|null`, `percent_median:number|null`, `normal_average_volume:number|null`, `forecast_period:"April-July"`. Seasonal and ten-day records carry reviewed `normalized_units` and exact `source_units`. | All variants: source issue/update time as applicable, `last_successful_retrieval_at:string|null`, `last_attempt_at:string`, `attempt_outcome:string`, `failure_stage:string|null`, `stale_since:string|null`, `valid_through:string|null`, `value_origin:string`, `metric_state:object`, `status:string`, `missing_reason:string|null`, `source_url:string`, `source_page_signature:string|null`, `diagnostic:object`. Missing is null, never zero. | One canonical 51-record CNRFC payload; no compatibility geometry or manifest. Each metric state supplies source time, validity, eligibility and missing semantics. The browser joins consumer-owned reviewed polygons by `forecast_key`. |
| CBRFC Colorado River water-supply forecasts<br>`docs/data/cbrfc_major_water_supply_forecasts.json` | Separate geometry-free JSON object; schema/product/roster/generation/publication metadata, exact three-record count, aggregate `source_summary:object`, three independent `family_health` entries, `operational_notices:array`, `records:array` | Ordered keys `CBRFC:GLDA3:APR_JUL_WSUP`, `CBRFC:GLDA3:WATER_YEAR_INFLOW`, `CBRFC:LKSA3:LOCAL_INTERVENING_MONTHLY`; product types are `april_july_water_supply_forecast`, `water_year_unregulated_inflow_forecast`, `lake_mead_local_intervening_monthly_forecast` | GLDA3 records retain their direct scalar contracts. LKSA3 has no aggregate volume: `monthly_forecasts:array` contains 12 ordered items with raw/corrected month and override provenance, direct `forecast_volume:number`, direct `percent_median:number`, exact `source_statistic:"ESP 50% EXCEEDANCE"`, `source_percentage_label:"%Med"`, monthly validity and metric state; record units are exact `source_units:"KAF"`, normalized `kaf`. | Every record independently carries attempt/failure, source-anchored freshness/expiry, LKG, exact `source_url`, `retrieval_url`, `summary_url`, `archive_url`, signature and diagnostics. GLDA3 expiry is period-specific. LKSA3 source freshness is independently configured at 40 calendar days; each item is `not_yet_valid`, current, stale or expired according to source age and its Pacific month boundaries. Zero is valid when directly dated/published; null is never zero. | No CNRFC coupling, geometry, coordinates, manifest, Reclamation operations link or compatibility path. The direct total-Lake-Mead endpoint currently publishes no record, so no total key or Powell-plus-local calculation exists. The reviewed July 2026 January-label correction is identified by `CBRFC_LKSA3_LOCAL_JANUARY_ROLLOVER_2026`. |
| CoCoRaHS<br>`docs/data/cocorahs_daily_precip_ca_latest.geojson`<br>`docs/data/cocorahs_daily_precip_conus_latest.geojson` | GeoJSON `FeatureCollection`; Point geometry; properties also carry `latitude:number`, `longitude:number` | `stationNumber:string`, `stationName:string` | `precip:number`, `gaugeCatch:number`, `precipIsTrace:boolean`, `gaugeCatchIsTrace:boolean`, `units:string` (current producer uses English units) | `obsDateTime:string` is station observation time; `feedBuildTimeUtc:string`; `sourceWindowStartDate:string`, `sourceWindowEndDate:string`; `source:string`, `stationUrl:string`. Trace is represented by its boolean, not by treating zero as missing. | Paired CA/CONUS summary JSON objects are canonical. Optional snow/notes properties may be null; no manifest or compatibility path. |
| Delta operations<br>`docs/data/delta_ops_daily_summary_features.geojson` | GeoJSON `FeatureCollection`; operational Point features; X2 reference is a separate Point FeatureCollection | `feature_key:string`, `display_name:string`, `feature_type:string`, `metric_name:string` | `value_raw:string`, `value_numeric:number|null`, `units:string`, `label_text:string`; current units include cfs, km, TAF, KAF/day, percent, and status text | `report_date:string` is the source report date; `feed_build_time_utc:string`; `source_name:string`, `source_url:string`, `preliminary_notice:string`; null numeric values retain their raw/status representation. | Compact summary and `docs/data/delta_ops_x2_reference.geojson` are canonical. `docs/data/delta_ops_daily_summary.json` is producer/QA metadata with unresolved consumer ownership. |
| ASOS/AWOS observed wind<br>`docs/data/wind/asos_awos_wind_latest.geojson` | GeoJSON `FeatureCollection`; Point; root also carries product metadata and bounding box | `station_id:string`; Point location | `wind_dir_degrees:number`, `wind_speed_kt:number`, `wind_speed_mph:number`, `wind_speed_ms:number`, `wind_gust_mph:number|null`, `has_gust:boolean` | `observation_time_utc:string`; `age_hours:number`; `source:string`. Null gust plus `has_gust:false` means no gust reported, not zero gust. | Summary and manifest are canonical status products. Manifest `public_files:object` selects `geojson:string`, `summary_json:string`, and `manifest_json:string`; `stale_after_hours:number` and `is_stale:boolean` report feed state. No separate compatibility output. |
| USGS streamflow<br>`docs/data/usgs_streamflow_latest_ca.geojson` | GeoJSON `FeatureCollection`; Point | `site_no:string`, `station_nm:string` | `q_cfs:number|null`, `stage_ft:number|null`; `has_latest_iv_q:boolean`, `has_latest_iv_stage:boolean` | `q_datetime_utc:string|null`, `q_obs_age_hours:number|null`, `stage_datetime_utc:string|null`, `stage_obs_age_hours:number|null`; `q_stale_6h:boolean`, `q_stale_24h:boolean`, `q_stale_72h:boolean`; `latest_status:string`; `feed_build_time_utc:string`. Both live values null means no drawable current observation, not zero. | `docs/data/usgs_streamflow_latest_ca_summary.json` is a canonical JSON coverage/status object. The current-value publication guard is not reliably enforced; no compatibility path. |
| USGS groundwater<br>`docs/data/usgs_groundwater_latest_ca.geojson` | GeoJSON `FeatureCollection`; Point | `site_no:string`, `station_nm:string` | `latest_wl_ft_bgs:number`, `latest_wl_units:string` (feet in current product), `latest_wl_source:string`; `has_api_latest_wl:boolean` distinguishes API measurement from index fallback | `latest_wl_datetime_utc:string`, `latest_wl_date:string`, `latest_age_days:number`; `latest_wl_status:string`, `latest_status:string`; `feed_build_time_utc:string`; source-specific optional API/history fields may be null or absent. | `docs/data/usgs_groundwater_latest_ca_summary.json` is a canonical JSON coverage/status object; no compatibility path. |
| SCAN soil moisture<br>`docs/data/scan_soil_moisture_latest.geojson` | GeoJSON `FeatureCollection`; Point; canonical trace is CSV | `station_uid:string`, `station_name:string`, `site_code:number` | `display_sms_pct:number` volumetric percent, `display_depth_in:number`; trace logical keys `station_uid`, `site_code`, `depth_in`, `water_year`, `water_day`, `sms_pct` | `display_obs_datetime_utc:string`, `display_obs_age_days:number`, `display_status_class:string`, `feed_build_time_utc:string`; `source_url:string`. Optional context failure must not invalidate valid latest data. | Latest summary and current-WY trace are canonical. Trace summary is producer/QA. `scan_depth_style.csv`, percentiles, monthly context, and prior-WY fallback are consumer context; only depth style is staged by the scheduled writer. |
| Snow-pillow SWE<br>`docs/data/snow_pillow_latest.geojson` | GeoJSON `FeatureCollection`; Point; canonical trace is CSV | `station_uid:string`, `provider_station_id:string`, `station_name:string`, `provider:string` | `latest_swe_in:number|null`, optional `latest_snow_depth_in:number|null`; trace logical keys `station_uid`, `provider_station_id`, `water_year`, `water_day`, `swe_in` | `latest_swe_date_local:string|null`, `latest_swe_age_days:number|null`, `latest_swe_staleness_class:string`, `latest_swe_report_status:string`, `latest_swe_source_class:string|null`, `feed_build_time_local:string`; null SWE is missing/no recent value, while numeric zero can be valid. | Latest summary and current-WY trace are canonical. Latest summary and producer/QA trace summary add a `refresh:object` without changing station or trace rows. Percentiles, monthly context, prior-WY fallback, and normal medians are static/manual consumer context. |

### Manifest-selected forecast products

| Family and manifest | Manifest selection contract | Selected product structure and minimum values | Time, status, missing/error behavior | Compatibility status |
|---|---|---|---|---|
| GFS<br>`docs/data/wind/gfs_surface_wind_feed_manifest.json` | Root `entries:array`; each usable entry has `product_id:string`, `model_cycle_utc:string`, `forecast_hour:number`, `valid_time_utc:string`, `relative_url:string`, `file_bytes:number`, and `status:string`. `browser_selection.supported_lead_hours:array` documents Current/+24 selection; `stale_after_hours:number` is feed freshness metadata. | Entry target is a two-element JSON array of U/V objects. Each has `header:object` and numeric `data:array`; headers require `parameterNumber:number`, `parameterNumberName:string`, `parameterUnit:string`, `nx:number`, `ny:number`, `lo1:number`, `la1:number`, `lo2:number`, `la2:number`, `dx:number`, `dy:number`, `refTime:string`, and `forecastTime:number`. Wind component unit is m/s. | `model_cycle_utc` is model init; `forecast_hour` plus init identifies `valid_time_utc`; manifest `generated_utc` is build generation. A missing/unresolvable entry is unusable. `status` describes entry production/reuse, not meteorological validity. | `docs/data/wind/gfs_surface_wind_latest.json` and its summary are compatibility outputs; current BRIM selection is manifest-driven. |
| HRRR<br>`docs/data/wind/hrrr_surface_wind_feed_manifest.json` | Root `entries:array`; entries require `product_id:string`, `requested_lead_hours:number`, `target_valid_time_utc:string`, `target_distance_minutes:number`, `model_cycle_utc:string`, `forecast_hour:number`, `valid_time_utc:string`, `relative_url:string`, `file_bytes:number`. `browser_selection.supported_lead_hours:array` defines Current/+6/+12; `stale_after_hours:number`. | Selected target is the same two-record U/V JSON structure and header fields as GFS, with numeric component arrays in m/s. `domain:object`, `grid:object`, and `earth_relative_winds_confirmed:boolean` carry spatial/vector state. | Target time and actual valid time remain distinct; the consumer rejects entries outside its target tolerance. Root `target_failures:array` can report unavailable targets. A load failure may retain the previous in-session field. | `docs/data/wind/hrrr_surface_wind_latest.json` and its summary are compatibility outputs; current BRIM selection is manifest-driven. |
| NBM<br>`docs/data/wind/nbm_wind_guidance_feed_manifest.json` | Root `entries:array`; each entry requires `feed_version:string`, `target_lead_hours:number`, `model_cycle_utc:string`, `forecast_hour:number`, `valid_time_utc:string`, `relative_url:string`, `feature_count:number`. Root `target_lead_hours:array`, `published_support_lead_hours:array`, and `support_window_hours:number` describe selection support. | Selected target is a GeoJSON `FeatureCollection` of Points. Minimum properties are `grid_i:number`, `grid_j:number`, `wind_dir_degrees:number`, `wind_p10_mph:number`, `wind_p50_mph:number`, `wind_p90_mph:number`, `gust_p10_mph:number`, `gust_p50_mph:number`, `gust_p90_mph:number`. | Init, forecast hour, valid time, and manifest `generated_at_utc` are distinct. The current manifest has no common `product_id`, stale field, or universal error/status envelope. Missing/unresolvable entries are unusable; the browser may retain its prior in-session field. | `docs/data/wind/nbm_wind_guidance_latest_summary.json` is producer/QA metadata, not a compatibility latest field. |
| Winter Storm Levels<br>`docs/data/winter-storm-levels/winter_storm_levels_manifest.json` | Root requires `product_id:"winter_storm_levels"`, supported `schema_version` and `contract_version`, accepted `status`, `source`, `domain`, `contour`, `freshness`, `cycle_time_utc`, retrieval/publication times, `target_count`, complete-set `diagnostics`, and `targets`. Each target requires source/cycle/valid/validity-window/lead, exact provenance URLs/inventory record, relative `path`, media type, SHA-256, bytes, feature count, contour levels, source-grid diagnostics, and output bounds. Consumers select the newest supported cycle and explicit valid-time entry; never list the directory. | Target is a GeoJSON `FeatureCollection` of `LineString` features in WGS84. Required properties are `product_id`, `source_id`, `parameter:"snow_level"`, `definition`, numeric `level_ft_msl`, `label`, `unit:"ft_msl"`, `cycle_time_utc`, `valid_time_utc`, numeric `lead_hours`, segment, and length. Contour elevations are configured 1,000-foot multiples; absence of a level is not zero. | Consumer recomputes active target windows and cycle status: current through 9 h, delayed usable through 15 h, stale LKG through 24 h, then expired; no active target also expires. Target validity is ±3 h. Missing/unresolvable/checksum-invalid targets are load failures. `source_unavailable`, `variable_missing`, `fetch_failed`, `decode_failed`, `validation_failed`, and `publication_failed` are attempt diagnostics and never make prior values look current. | No compatibility latest file. Canonical manifest/targets remain absent until the first approved publication. Browser integration must accept the manifest contract before any later removal or breaking change. |

### NoData, null, empty, and error representation

- There is no universal literal `NoData` sentinel. JSON null, an absent optional
  property, and numeric zero are distinct.
- An empty `features` or `entries` array is syntactically valid JSON but is not
  proof of an acceptable product. Product-specific producer gates and consumer
  requirements decide whether it is usable.
- An empty object has no repository-wide success meaning. Required manifest,
  header, identity, and measurement members must still be present.
- Current GFS entries expose `status`; HRRR exposes `target_failures`; ASOS
  summary/manifest expose `status`/`is_stale`; station products expose
  product-specific status/stale/source fields. NBM has no equivalent universal
  error field. Do not invent one in consumer code.
- Producers may serialize optional source/context properties as null or omit
  them. Consumers must tolerate both only for fields documented as optional;
  required selection/identity fields remain mandatory.
- HTTP errors, malformed roots, missing manifest targets, unequal U/V arrays, and
  non-finite numeric data are load failures, not empty successful products.

## Product-specific acceptance

This section identifies the stable concepts the consumer currently depends on.
It deliberately avoids reproducing full private parsing code or copying every
serialized field. Current field sets can be inspected in the tracked products
and producer scripts.

### CDEC reservoir

- Required entry point: GeoJSON FeatureCollection.
- Consumer identity: reservoir/CDEC identifiers and station name.
- Core display concepts: display storage value/source, observation time/age,
  capacity where available, data-quality state and source links.
- Units: acre-feet, percent capacity, feet and cfs where applicable.
- Missing storage remains a visible no-current-value state.
- Fallback priority: CDEC latest, CDEC daily-midnight, selective CNRFC
  observed/current fallback.
- Current consumer stale states: older than 12 hours and older than 24 hours.

### Major water-supply basin forecasts

- The producer publishes 51 deterministically ordered roster records: the
  reviewed 15 CNRFC FNF basin and direct SACC0, VNSC0 and MLIC0 water-year
  identities; one short-range accumulation identity for each of the 15
  individual major basins; and 18 April-July identities covering the same 15
  basins plus the three direct indices. Product 2 does not apply to the three
  index identities or generalized Colorado geometries. CBRFC is not embedded in
  this schema; it has its own schema-1 payload and publication transaction.
- `forecast_key`, not LID alone, is the durable join key because RFC and product
  type are part of identity. Consumers must reject duplicate keys, treat a
  missing expected key as unavailable rather than crashing, and tolerate unknown
  additive keys with a warning. They must reject unsupported schema major
  versions. A roster key removal, rename or semantic reuse is a breaking change;
  add a new key and preserve/deprecate the old key through explicit review.
- The consumer owns static reviewed polygons. The public live payload must have
  no geometry, coordinates or component-watershed inventory.
- Product-9 forecast volume, units, percent of mean and percent of median are
  direct CNRFC page values. Product-2 median Day 3/5/10 and deterministic Day 3/5
  fields are direct cumulative table values selected by ordered forecast-date
  column, with each actual table date retained. Consumers must not reconstruct
  missing values, calculate accumulated volume from flow, read ensemble members,
  derive percentages, sum basins, build indices from components, or infer a
  deterministic Day 10 value.
- Product-7 `forecast_volume`, `percent_average`, `percent_median` and
  `normal_average_volume` are direct source values. `Median Forecast` is
  cross-checked against the matching tabular `50% Exceed` value and published as
  `forecast_statistic: "50_percent_exceedance"`. Consumers must preserve
  the direct percent of mean under `percent_average`, the legacy wire key
  representing percent of mean; preserve the distinct headline
  `Percent of Median` as `percent_median`; and treat `normal_average_volume` as
  the legacy wire key representing mean reference volume. Neither percentage
  may be derived from volume or a reference denominator, and wire-key names must
  not be exposed as human-facing labels.
- `forecast_issued_at` and product-2 `source_data_updated_at` are source times;
  `last_successful_retrieval_at` is the retrieval that supplied current-source
  values, `last_attempt_at` is the last materially distinct canonical attempt,
  and top-level `generated_at` changes only on promotion. Consumers must use
  source time—not retrieval, generation, commit or Pages time—for freshness.
- Each display number has a same-name `metric_state` entry. Consumers select
  eligibility from that metric, never infer it only from record status:

  | Metric status | Value origin | Numerical value | Map eligible | Popup eligible | Meaning |
  |---|---|---:|---:|---:|---|
  | `current` | `current_source` | yes, including zero | yes | yes | Valid source metric within freshness and validity policy |
  | `source_stale` | `current_source` | yes | no | yes | Current attempt parsed, but source age/water-year policy is stale |
  | `stale_last_known_good` | `last_known_good` | yes | no | yes | Current attempt failed; unexpired prior value retained |
  | `expired` | `current_source` or `last_known_good` | yes | no | yes | Validity window ended; value retained only as labeled provenance |
  | `unavailable` | `none` | no | no | no | Source explicitly confirms no value |
  | `failed_no_data` | `none` | no | no | no | Fetch/parse/validation failed and no value can be retained |

  Null must never be coerced to zero; numeric zero remains a valid current value.
  Record `current_partial` means at least one metric is current and at least one
  is not; the metric states identify the exact usable fields. Other record
  summary states are `current`, `source_stale`, `stale_last_known_good`,
  `expired`, `unavailable` and `failed_no_data`.

  | Record status | Derived summary rule |
  |---|---|
  | `current` | Every display metric is current |
  | `current_partial` | At least one display metric is current and at least one is not |
  | `source_stale` | No current metric; at least one successfully parsed metric is source-stale |
  | `stale_last_known_good` | No current/source-stale metric; at least one unexpired prior metric is retained |
  | `expired` | No current/stale-LKG metric; at least one numerical provenance value is expired |
  | `unavailable` | Source-confirmed absence and no retained numerical value |
  | `failed_no_data` | Current attempt failed and no current/prior numerical value is usable |
- Product-9 metrics become source-stale 72 hours after
  `forecast_issued_at` (or on water-year mismatch) and expire after 168 hours.
  Product-2 metrics become source-stale 36 hours after
  `source_data_updated_at`, with `forecast_issued_at` only a documented fallback;
  each expires at the first America/Los_Angeles midnight after its actual table
  valid date. Product-7 metrics become source-stale 36 hours after
  `forecast_issued_at` and expire at August 1 00:00 America/Los_Angeles for the
  record's water year. These are reviewed configurable starting policies.
- `attempt_outcome` separates `success`, `source_unavailable`, `fetch_failed`,
  `parse_failed` and `validation_failed`. `failure_stage` is null on success and
  otherwise `source`, `fetch`, `parse` or `validate`. An HTTP/DNS/timeout error is
  not a parse failure; recognized invalid units, cumulative decreases and
  backward source times are validation failures.
- Top-level `family_health` independently summarizes `water_year_fnf`,
  `water_year_index`, `ten_day_streamflow_volume_accumulation` and
  `april_july_streamflow_volume_forecast`. Every family
  reports `expected_structural_count`, `expected_available_count`,
  `current_count`, `current_partial_count`, `source_stale_count`,
  `stale_last_known_good_count`, `expired_count`,
  `source_unavailable_count`, `failed_no_data_count`,
  `successful_attempt_count`, `failed_attempt_count` and `health` as `healthy`, `degraded`,
  `outage_using_last_known_good` or `unusable`. Product 2 additionally reports
  median/deterministic coverage. `source_summary` exposes the unambiguous
  `water_year_fnf_success_count`, `water_year_index_success_count`,
  `april_july_success_count`,
  `accumulation_median_success_count` and
  `accumulation_deterministic_success_count`; it does not call a fetch-only event
  a retrieval success.
- Bootstrap with no valid prior payload requires 15/15 FNF, 3/3 index, 14/14
  expected-available product-2 median series, explicit source-confirmed MHBC1
  product-2 unavailability, all four direct metrics for all 18 product-7
  identities while active or explicit out-of-season source-unavailable states,
  and the exact validated 51-key
  schema/roster. Deterministic coverage is separate and not a bootstrap gate.
  With a valid prior payload, steady-state publication constructs all 51 records and lets
  families advance independently using current, partial, LKG, expired,
  unavailable or failed-no-data states. Ordinary outages publish degraded state;
  only unsafe structure/schema/serialization/staged validation preserves prior
  bytes without promotion.
- An accepted scheduled writer run creates the canonical path when absent.
  Absence of the file is a product-unavailable state, not an empty 51-record
  success.
- For individual major-basin polygons, the future browser contract may expose
  water-year percent of median/mean, median cumulative inflow through Day 3/5/10,
  and deterministic cumulative inflow through Day 3/5. Product-2 modes must show
  cumulative-volume wording and units; indices and generalized Colorado
  geometries remain neutral/unavailable. For absolute-volume modes, the dynamic
  domain is calculated only from current, map-eligible individual CNRFC major
  basins; indices and Colorado are excluded, zero is included, and stale,
  expired, unavailable and failed metrics are excluded. Every absolute-volume
  mode uses the same square-root transform; legend ticks stay in original kaf
  and state that the range is recalculated for each update and horizon. An
  equal-value domain uses one midpoint tone, while too small a current sample
  neutralizes the choropleth. Exact values remain in popups. Percent-of-mean and
  percent-of-median modes retain fixed percentage classes; fixed absolute-volume
  classes are not part of this contract. A popup may use `source_url` for a link
  labeled `CNRFC 10-day accumulated-volume forecast` alongside the water-year
  source link. Raw kaf must not be labeled percent of mean or normalized storm
  intensity. April-July modes may use the direct product-7 volume, percent of
  mean or percent of median, including the three index records where the
  consumer owns an applicable reviewed display polygon. Product-7 and the
  separate GLDA3 feed are
  comparable seasonal-volume products, but exact source period and mean/median labels
  must remain visible and neither consumer polygon may be described as exact RFC
  forecast-basin geometry.

### CBRFC Colorado River water-supply forecasts

- This is a separate schema-`1.0` three-record payload with product ID
  `cbrfc_major_water_supply_forecasts` and roster version
  `cbrfc-colorado-river-v1.3.0`. Its exact ordered roster is
  `CBRFC:GLDA3:APR_JUL_WSUP` / `april_july_water_supply_forecast`, followed by
  `CBRFC:GLDA3:WATER_YEAR_INFLOW` /
  `water_year_unregulated_inflow_forecast`, followed by
  `CBRFC:LKSA3:LOCAL_INTERVENING_MONTHLY` /
  `lake_mead_local_intervening_monthly_forecast`. Consumers must reject an unsupported
  schema major, duplicate/missing reviewed records, reordering, or identity
  reuse. The first two are periods for one GLDA3 location; the third is local
  intervening flow below Glen Canyon Dam, not a GLDA3 alias or total Lake Mead
  inflow. Top-level `operational_notices` reports nonblocking dashboard
  cross-check limitations and the reviewed LKSA3 source-date correction.
- For April-July, only official CBRFC `offdate`, `off50`, `offpavg` and
  `offpmed` fields are public values. The producer maps them directly to
  `forecast_issue_date`, `forecast_volume`, `percent_average` and
  `percent_median`; it never substitutes `espdate`, `esp50`, `esppavg` or
  `esppmed`. The point endpoint is authoritative; an official list row, when
  present, is a secondary identity/period/value cross-check and may lag or omit
  a newer point issue. The Lake Powell dashboard is a second official
  representation for direct forecast-volume/percent-of-mean comparison.
  Same-issue values must agree within half of the dashboard's displayed precision. Omission,
  maintenance or an older dashboard produces an operational notice without
  replacing a complete newer point record; material current disagreement is a
  validation failure.
- For water year, only the directly published forecast volume and
  percent-of-mean values from the unique semantic `Water Year` row on the official Upper Colorado
  situational-awareness page are public. They map to `forecast_volume` and
  `percent_average`. The source's `Obs to Date` value is structurally validated
  but is neither a public metric nor a semantic-publication trigger. It remains
  available through the dashboard `summary_url`. The dashboard is also this
  record's retrieval source, so the summary is explicitly not an independent
  cross-check. The source does not
  directly publish a water-year percent median, so this record has no
  `percent_median` key; consumers must not calculate, infer or substitute one.
- Both GLDA3 records preserve `source_time_precision: "date"`,
  `source_normal_term: "average"`, forecast type `Unregulated`, exact
  `source_units` and reviewed `normalized_units: "kaf"`. April-July preserves
  period `Apr 1-Jul 31` and statistic `50_percent_exceedance`; water year
  preserves period `Water Year` and statistic `official_full_forecast`.
  Consumers must not invent a midnight/timestamp, calculate percentages or
  volumes, collapse the two periods, or treat retrieval/excluded-page changes as
  a new official issue.
- The LKSA3 record preserves `source_identifier: "LKSA3 QCMPLCM"`, issue-date
  precision, exact source units `KAF`, normalized units `kaf`, source term
  `median`, period `MONTHLY OUTLOOKS`, and statistic label
  `ESP 50% EXCEEDANCE`. It contains exactly 12 consecutive items beginning in
  the issue month. Every item supplies `raw_forecast_month_label`, exact
  `forecast_month`, direct `forecast_volume`, direct `percent_median`, the exact
  source statistic and `%Med` labels, date-override applied/ID/reason/evidence/
  prior-issue fields, `valid_from`, `valid_through`, item `status`, `value_origin`,
  `stale_since`, `missing_reason`, and separate metric states for volume and
  percent median. There is no record-level aggregate volume/percentage and no
  source-selected active-month flag. Consumers select an explicit month and
  must never sum the series into a seasonal or annual forecast. Before its month
  begins an item is `not_yet_valid`, popup eligible and not map eligible; only
  the current month can be map eligible, subject to source freshness.
- Blank April-July `offdate` with official zero sentinels is source-confirmed
  unavailable. A wholly blank/sentinel water-year row is likewise unavailable;
  a partially missing row is invalid. A directly published, validly dated zero
  remains zero. Null is never coerced to zero. Every public display metric has
  its own state and uses the same current/current-partial/source-stale/LKG/
  expired/unavailable/failed-no-data meanings defined above. LKSA3 additionally
  rejects any missing, duplicate, nonconsecutive or year-regressing month,
  malformed `%Med`, unknown unit/statistic or ambiguous identity, except for one
  reviewed rule. For the exact July 1, 2026 source, the raw `January 2026` row is
  published as `2027-01` only when the exact 12-month sequence otherwise holds
  and the immediately preceding June 1 official archive uniquely confirms
  January 2027. Override ID
  `CBRFC_LKSA3_LOCAL_JANUARY_ROLLOVER_2026`, reason, evidence URL and prior issue
  date are preserved; values, order, issue, units and percentages do not change.
  All other rollover cases fail validation.
- Each family is retrieved, validated and reconciled independently. Bootstrap
  may proceed when any family establishes valid official data; the others remain
  explicit structural failed/unavailable records. Bootstrap fails when no
  active family establishes data. In steady state, failure of any source may
  retain only that record's validated prior provenance and does not erase or
  invalidate either other family. All-products failure with a valid prior emits
  an honestly degraded three-record candidate rather than marking old data
  current.
- The reviewed GLDA3 stale threshold is 40 calendar days after each official
  issue date, reflecting their monthly official issue cadence. April-July metrics
  expire at August 1 00:00 America/Los_Angeles for the water year. Water-year
  metrics remain valid through September 30 and expire at October 1 00:00.
  LKSA3 uses its own independently configured 40-day threshold, based on the
  current product archive's approximately monthly cadence; archived years may
  include mid-month updates. Every LKSA3 item expires at the first Pacific
  midnight of its following month and remains popup-only provenance thereafter.
  The year-round series does not inherit either GLDA3 record expiry. Retrieval,
  generation, commit and Pages times do not extend freshness or validity.
- Every record supplies exact role-labeled `source_url`, `retrieval_url`,
  `summary_url` and `archive_url`; the producer constructs no URL from consumer
  identifiers. GLDA3 year-specific URLs use the validated record water year,
  not runner time. Reclamation reservoir-operations links are intentionally
  excluded and belong in BRIM's separate related-links registry.
  `CBRFC:GLDA3:APR_JUL_WSUP` uses the `espgraph_hc.html?id=GLDA3&year=<WY>`
  graph, `espgraph_data_hc.py?id=GLDA3&year=<WY>` point source, `dash/lp.php`
  summary and Upper Basin Apr-Jul archive. The water-year record uses
  `dash/lp.php` for source/retrieval/summary and the Upper Basin Water Year
  archive. LKSA3 uses current `espaz_lml/lakemead.txt` for source/retrieval,
  the Special Forecast Products page for summary and the Lake Mead Local
  archive.
- The canonical path is absent until its first accepted writer run. Its daily
  08:14 Pacific workflow, three family-health entries and publication transaction
  are independent of CNRFC. Unsafe roster/schema/serialization/staged validation
  preserves prior CBRFC bytes and never blocks or rewrites CNRFC. The payload has
  no geometry, coordinates, HUC, manifest or compatibility path. The consumer
  owns reviewed display geometry and must not describe it as exact CBRFC
  model-basin geometry.
- The official structured request for total Lake Mead (`LKSA3`) currently
  returns literal `false`, and the reviewed official list has no total row.
  Consequently the roster has no `TOTAL_UNREGULATED_INFLOW` key. Consumers must
  not derive one from GLDA3, local flow, releases, reservoir conditions or
  storage change. Revisit this only when CBRFC publishes a direct, dated period,
  volume, unit and statistic contract.

### CoCoRaHS precipitation

- Separate CA and CONUS GeoJSON products are both canonical.
- Consumer identity: station number or stable fallback identity.
- Core concepts: coordinates, station name, precipitation/trace indicator,
  units, observation time and station URL.
- Units are currently English; precipitation is treated as an approximate
  prior-24-hour station report ending at that station's time.
- Station reporting times vary; one layer-wide observation window must not be
  inferred.

### Delta operations

- Canonical consumer inputs are operational features, human summary and X2
  reference GeoJSON.
- Feature keys, numeric/raw values, labels, units, report date and source URL
  are contract-sensitive.
- Current display units include cfs, km, TAF, KAF/day and percentages.
- `delta_ops_daily_summary.json` is not a current registry entry and must be
  treated as producer/QA metadata or compatibility output until ownership is
  decided.

### GFS and HRRR wind

- The manifest is the canonical entry point.
- Each usable entry needs a valid-time concept and a relative product URL.
- Each selected JSON product needs paired U/V records with compatible grid
  headers, reference time, forecast hour and equal-length data arrays.
- Record units are m/s; BRIM derives display mph.
- GFS current consumption targets Current and +24 hours.
- HRRR targets Current, +6 and +12 hours and accepts a maximum 2.1-hour target
  error.
- Latest JSON and summary files are compatibility outputs, not the current BRIM
  selection mechanism.

### NBM wind guidance

- The manifest is the canonical entry point.
- Usable entries need cycle, forecast-hour/valid-time, and relative URL
  concepts.
- GeoJSON points provide wind/gust p10, p50 and p90 in mph plus direction.
- BRIM targets +6, +12, +24 and +48 hours with a maximum 3.1-hour target error.
- Percentile ranges are guidance ranges, not formal confidence limits.

### ASOS/AWOS observed wind

- GeoJSON is the current observation product; summary and manifest provide
  status/metadata.
- Required concepts include station ID, coordinates, observation time, age,
  speed, gust presence/value, direction and source.
- Current serialized units include knots, mph and m/s.
- The current official producer is RTW019. Integration-only RTW020 station
  enrichment is not part of the current contract.
- Consumer age presentation distinguishes recent, caution and stale
  observations; producer manifest stale threshold is two hours.

### USGS streamflow

- GeoJSON and summary are canonical.
- Consumer identity: USGS site number.
- Drawable current-value concepts: discharge in cfs or gage height in feet,
  with observation time/age.
- BRIM excludes features that have neither a current discharge nor stage.
- Product stale flags use 6-, 24- and 72-hour discharge bands.
- The current producer does not enforce the workflow's declared minimum
  current-discharge count. File presence and static feature count do not prove
  a usable live layer.

### USGS groundwater

- GeoJSON and summary are canonical.
- Consumer identity: USGS site number.
- Core concepts: latest water level in feet below ground/land surface,
  measurement time/date/age, source, status and official links.
- Source units and vertical datum must not be silently normalized away.
- BRIM uses 90-day, one-year and two-year age bands and distinguishes API
  measurements from index fallback.
- Optional history fields enrich popups; their absence must not prevent the
  latest-only layer from loading.

### SCAN soil moisture

- Latest GeoJSON is required for the live layer.
- Current-WY trace, depth style, daily percentile, monthly and fallback CSVs
  provide optional context.
- Station/depth keys, observation date/time, soil-moisture percent and depth in
  inches are contract-sensitive.
- Failure of optional context should not convert a valid latest product into a
  failed latest layer.
- Daily/monthly/fallback files have split producer ownership and must not be
  assumed to refresh with the scheduled writer.

### Snow-pillow SWE

- Latest GeoJSON is required for the live layer.
- Current-WY trace and historical context CSVs are optional enrichments.
- Station identity, provider, latest SWE in inches, observation date/age,
  source-element class and staleness class are contract-sensitive.
- BRIM freshness classes are 0–2, 3–7, 8–21 and greater than 21 days.
- CDEC adjusted element 82 is preferred; element 3 has a limited tail/fallback
  role that must remain visible.
- `live_provider_key` distinguishes `nrcs_snotel` from `cdec_snow_sensor` in
  both latest and trace rows. When exactly one provider fails retrieval or QA,
  the producer may combine fresh rows from the healthy provider with validated
  prior rows for the failed provider.
- Carried-forward provider rows retain all prior observation dates, values,
  source classifications and row-level build/fetch fields. The new combined
  summary build time does not make those station observations newly observed.
- Latest and trace summaries expose additive `refresh.mode`,
  `refresh.build_time_utc`, `refresh.last_known_good_provider_data_preserved`,
  and provider-keyed fetch/QA/action/count/failure metadata. Consumers must
  continue tolerating additive summary members.
- Both-provider failure, invalid prior products, or unavailable valid prior rows
  for the failed provider produces no replacement product.
- Failure of optional context should not invalidate a usable latest layer.

### Winter Storm Levels

- Version 1 requires `publication_time_utc` to be JSON null because the builder
  runs before the Git commit, push, and Pages deployment. Consumers must not
  interpret retrieval or build time as publication time.
- The first accepted canonical bundle is one complete current 11-target cycle
  with an active target. A genuinely newer mature bundle has exactly two
  complete cycles and 22 targets. If prior retained state cannot be strictly
  validated and copied, the producer fails and retains the prior canonical
  bytes rather than publishing a one-cycle degradation.
- Load only
  `docs/data/winter-storm-levels/winter_storm_levels_manifest.json`, require
  product ID `winter_storm_levels` and a supported contract major, then resolve
  relative target paths from that manifest. Directory listing, newest filename,
  file modification time, retrieval time, and Git time are not selection rules.
- Prefer the newest cycle for overlapping valid times and only display a target
  while the current time lies between its `valid_from_utc` and
  `valid_through_utc`. Expose source cycle and valid time separately in Pacific
  Time while retaining UTC for calculations.
- Recompute `current`, `delayed_but_usable`, `stale_last_known_good`, and
  `expired` from the manifest thresholds. Stale LKG may remain visible only with
  explicit warning; expired targets must not remain an active map layer.
- Treat `level_ft_msl` as a modeled NBM snow-level elevation in feet above mean
  sea level. Do not relabel it as surface temperature, freezing level, snow
  depth, snow accumulation, or observed precipitation type. Display the source
  definition and modeled-transition caveat. The producer derives contour
  linework from the full native buffered NBM crop and applies only clipping,
  coordinate transformation, and deterministic serialization cleanup; modeled
  elevations, source identity, cycle, valid time, and forecast lead remain
  unchanged. Mathematical line simplicity is diagnostic, not a consumer or
  publication requirement for these display LineStrings.
- A GeoJSON target must be a nonempty WGS84 LineString FeatureCollection within
  the manifest domain, with finite coordinates and the required stable feature
  properties. A missing level or missing feature is not numerical zero. Reject
  nonfinite coordinates, unsupported units/datum, time disagreement, missing
  target, or checksum mismatch without crashing unrelated layers.
- The public producer does not ship a terrain surface. Where a contour height is
  below local terrain, explain it as a transition at/near the surface; do not
  calculate or display a below-ground depth from this product alone.
- The private integration is additive and separate. No private source is copied
  into this repository, and no consumer change is required to validate this
  production candidate. First official publication, schedule activation, and
  later consumer release remain coordinated maintainer decisions.

## Compatibility and fallback

There is no repository-wide, versioned compatibility policy today.

Current compatibility behavior includes:

- GFS and HRRR latest JSON/summary outputs retained beside manifests.
- ASOS latest, summary and manifest retained together.
- Browser retention of a previously displayed HRRR/NBM field after a selected
  entry fails.
- Static/manual SCAN and snow context fetched beside scheduled outputs.
- Snow provider-level carry-forward: one healthy provider can refresh while the
  other provider's validated prior latest and current-WY trace rows remain
  unchanged, with summary-level partial-refresh status.

These behaviors are current implementation, not an indefinite support
guarantee. Before removing or renaming any path, identify all consumers,
announce the replacement, support an overlap period where practical, and
verify private BRIM against both old and new contracts.

## Breaking change sequence

A breaking change includes a removed/renamed path or field, incompatible type,
changed root structure, altered coordinate order/CRS, incompatible units,
changed null semantics, changed timestamp meaning, removed manifest entry, or
meaningful payload expansion.

Required sequence:

1. Describe and version the proposed contract.
2. Produce it at a nonproduction path or artifact.
3. Add and test private BRIM support without removing old support.
4. Exercise current, stale, empty, malformed and missing cases.
5. Validate both contracts where fallback matters.
6. Review producer and consumer changes together.
7. Merge in an order that preserves the old official consumer.
8. Switch the official path only after the repository maintainer explicitly
   approves publication.
9. Retain or retire compatibility output deliberately and document the date.

No producer-only merge should place a breaking schema on an official path
before the consumer can accept it.

## Future common contract envelope

A later additive envelope should be able to represent:

- product ID/name and schema version;
- producer repository, workflow and commit;
- provider/upstream source;
- retrieval, observation, model initialization, valid, build and publication
  times as separate nullable fields;
- format, compression, CRS, units and spatial domain;
- record count, payload bytes and checksum;
- QA/freshness status and stale threshold;
- publication path, fallback product, attribution and known limitations.

Product-specific details should remain in a nested object or entry array rather
than forcing all families into misleading common fields. JSON Schemas should
be proposed and tested in a separate producer-consumer migration; this
documentation change creates none.

## Conflict resolution

When evidence disagrees:

1. Workflow and producer code describe current producer behavior.
2. Tracked manifests/products describe current serialization.
3. Private BRIM registry/helper code describes current consumer acceptance.
4. This document describes the intended shared interface and must be corrected
   when it disagrees with those facts.

Documentation does not silently override implementation, and implementation
does not make an undocumented breaking change acceptable.
