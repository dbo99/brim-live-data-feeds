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

## Verified producer-consumer crosswalk

| Family | Producer workflow | Build script | Canonical repository path(s) | Manifest-driven | Private BRIM consumer |
|---|---|---|---|---|---|
| CDEC reservoir | `.github/workflows/build-cdec-reservoir-feed.yml` | `scripts/build_cdec_reservoir_latest.R` | `docs/data/cdec_reservoir_latest.geojson`<br>`docs/data/cdec_reservoir_latest_summary.json` | No | Reservoir Ops helper |
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

The relationship in every row was verified from the producer workflow/script,
tracked output, private registry path, and corresponding private helper.
The complete repository path classification, including compatibility and
producer/QA files, is maintained in [PRODUCTS.md](PRODUCTS.md).

## Common contract semantics

### Format and compression

- Official browser products are uncompressed JSON, GeoJSON, or CSV served by
  GitHub Pages.
- Actions QA artifacts are ZIP archives and are not product URLs.
- GFS and HRRR wind grids are arrays of U and V records with grid headers and
  data arrays.
- NBM guidance and point products are GeoJSON FeatureCollections.
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
