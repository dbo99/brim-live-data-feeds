# Soil history: `brim-soil-history-1`

Status: additive **SCAN offline export only**. This version does not activate a
workflow, publish a URL, replace an existing SCAN product, or establish consumer
acceptance. The [product catalog](PRODUCTS.md#10-scan-soil-moisture) and
[consumer contract](BRIM_CONSUMER_CONTRACT.md#scan-soil-moisture) remain the
authorities for the existing products. No Dendra or SNOTEL acquisition is part
of this slice. Provider request budget is zero.

Contract closure `scan-history-gate1-closure-1` retains this schema/version.
The approved SCAN-specific shared-hover exception and dedicated Last-3 transport
are specified below. This consumer transport decision is not application
implementation, deployment acceptance, or an exception for any other network.

## Inputs and authority

[`scan_history.py`](../scripts/soil_moisture/scan_history.py) requires an explicit
saved `scan_sms_daily_history.rds`, current-product directory, checksum manifest,
manifest SHA-256, and a fresh output directory. The adapter
[`read_scan_history_rds.R`](../scripts/soil_moisture/read_scan_history_rds.R) uses
base R only; it does not source a builder, load soilDB, or acquire observations.
Only trusted saved RDS inputs are supported. A checksum binds bytes; it is not a
claim of provider authentication or an independent scientific audit.

The canonical input manifest has schema `brim-soil-history-1-inputs`, `scope`
(`stations`, `histories`, sorted `archive_only` site/depth pairs), and `files`:
a mapping from each of the nine exact filenames below to `bytes` and `sha256`.
JSON is ASCII, sorted keys, compact separators, finite numbers, one trailing LF.
Absolute machine paths are CLI arguments, never fields in the public contract.

- `scan_sms_daily_history.rds`
- `scan_soil_moisture_latest.geojson`
- `scan_soil_moisture_latest_summary.json`
- `scan_soil_moisture_current_wy_trace.csv`
- `scan_soil_moisture_current_wy_trace_summary.json`
- `scan_depth_style.csv`
- `scan_sms_monthly_context.csv`
- `scan_sms_prior_wy_fallback_traces.csv`
- `scan_sms_waterday_percentiles.csv`

The latter four context files are checksum-bound and retained unchanged. They
are not recalculated, copied over, or substituted for full daily history. The
existing saved products supply the current-WY number, Oct 1 start, observation
cutoff (`last_date`), and original feed build time. Export time never refreshes
those dates or the observations' freshness.

The saved latest GeoJSON alone establishes browser station/depth membership.
The fixed reviewed slice is **28 stations / 131 exact depth histories**. The
archive contains 135 pairs; these four are inventory-only:

| SCAN site | Depth (inches) |
| --- | ---: |
| 2116 | 20 |
| 2116 | 40 |
| 2149 | 40 |
| 2190 | 40 |

They receive no consumer shard, index entry, or hover series. Their counts,
date coverage, and saved-input identity remain in archive inventory; their
observations remain in the unchanged source RDS. Any unexpected membership
difference fails export rather than silently broadening scope.

## Daily semantics

An exact key is `(site_code, depth_in, date)`; `station_uid` must equal
`NRCS_scan_<site_code>`. No nearest-depth matching or station substitution is
allowed. Depth is inches; moisture is volumetric water-content **percent**, not
a fraction. This SCAN slice supports exact saved depths 2, 4, 8, 20 and 40 inches.

Archive dates strictly before the current-WY start are eligible. For the entire
current WY, the saved current trace has sole authority. Every archive row in
that interval is ignored, including rows on dates missing from the current
trace. A missing date stays missing; no interpolation, carry-forward or fallback
fill is performed. Duplicate dates, future archive dates beyond the saved
cutoff, missing values, and nonfinite numbers fail export.

Each shard uses these ordered `columns`, with one array per observed day:

```text
[date, sms_pct, water_year, water_day, sensor_count, sensor_ids]
```

`date` is the unshifted source civil date (`YYYY-MM-DD`), not a reconstructed UTC
instant. `water_year` is the year ending September 30; `water_day` is actual
elapsed calendar days since October 1 plus one. February 29 is retained. This is
not the aligned climatology day used by a reference percentile table.

`sensor_ids` is a sorted unique array, split from saved provenance (archive
semicolon delimiter; current CSV comma delimiter). Its size equals
`sensor_count`, and all sensors must match the exact shard depth. Historical
same-depth daily composites are retained with their saved values and membership;
they are never averaged again or replaced by a single sensor. Current values
retain the published precision. R numeric values use 17 significant digits in
the temporary CSV bridge for binary-double round-trip.

Zero is a real value. The saved archive contains finite values above 100%; this
transport preserves them and reports `outside_0_100` counts per history and in
validation. It does not silently clamp, drop, scientifically approve, or infer
quality flags for them. Scientific treatment of those values needs lead review.

## Delivery paths and selection

All paths are relative to one fresh delivery root; there are no activated
public URLs in this slice. `<sha256>` is the complete lowercase SHA-256 of that
file's exact bytes, including the trailing LF.

| Path | Role |
| --- | --- |
| `manifest.json` | Explicit entrypoint; pin its SHA-256 out of band |
| `history-index-<sha256>.json` | Immutable browser roster and exact shard descriptors |
| `history/<site>-<depth>-<sha256>.json` | One immutable full daily history per advertised exact pair |
| `last3/<site>-<depth>-<sha256>.json` | Latest up-to-three usable completed WYs for that exact pair |
| `hover-30d-<sha256>.json` | Compact 30-calendar-day product for the same roster |
| `archive-inventory-<sha256>.json` | Archive coverage/counts, including inventory-only depths |
| `reference/scan_sms_waterday_percentiles-<sha256>.csv` | Byte-identical frozen copy of the authoritative existing reference table |

Every JSON object identifies `schema: brim-soil-history-1`, `network: SCAN`, its
`kind`, and `input_manifest_sha256`. The manifest embeds the canonical input
binding, expected scope, authority, policy, the three selected top-level file
descriptors, a complete `files` allowlist, total `payload_bytes`, and
`publication: offline_export_only`. A descriptor is `{path, bytes, sha256}`.
The manifest's own size/hash is measured separately to avoid self-reference.

The index includes units, authority, policy, station count, and sorted
`histories`. Each history identifies site, station UID/name, depth, row count,
first/last dates, zero/out-of-range/composite counts and its exact `file`
descriptor. Select through this index; never guess a shard filename or advertise
archive inventory as browser membership. Unchanged shard contents produce the
same filenames under the same input binding. A changed binding intentionally
produces a separate generation; files from generations must not be mixed.

Hover includes `first_date`, `last_date`, `days: 30`, authority and sorted
histories containing site, depth and a 30-element `values` array. Element zero
corresponds to `first_date`; each subsequent slot advances one calendar date.
`null` means no saved observation on that exact date, while numeric `0` remains
zero. The window ends at the saved trace cutoff, not export time. At WY rollover
it may include eligible archive dates before Oct 1; from Oct 1 onward the current
trace alone supplies values. Hover contains no reconstructed sensor detail;
resolve the exact history shard for the daily provenance. Consumers must break
lines at gaps and keep observation age visible.

## Explicit capabilities and request rules

The closure manifest, index and every advertised pair declare `hover_30d`,
`history_last3`, `history_all_available`, and `reference_band`. Every capability
has `status` and `generation`. Available payloads have exact `file` descriptors
(`path`, `bytes`, `sha256`), or a producer-supplied reference to their descriptor:

| Capability | Manifest resolution | Exact pair resolution |
| --- | --- | --- |
| `hover_30d` | `capabilities.hover_30d.file` | `manifest_capability: hover_30d` plus exact site/depth selector |
| `history_last3` | `capabilities.history_last3.index`, then `entry_key` | `capabilities.history_last3.file`, `selected_years`, `policy_ref` |
| `history_all_available` | `capabilities.history_all_available.index`, then `entry_key` | `capabilities.history_all_available.file`, `policy_ref` |
| `reference_band` | `capabilities.reference_band.file` and `source` | `manifest_capability: reference_band` plus exact site/depth selector |

The index's top-level capability declarations refer by `manifest_capability` to
the corresponding manifest declarations. `policy_ref` resolves to the named
manifest capability's `policy`. All referenced paths are producer supplied;
consumers must not construct filenames. Global availability means the product
is implemented and resolvable; the selected pair's status also applies. An
unresolvable pair declares `status: unavailable` and a nonempty `reason`, with
no payload target. The fixed 131-pair closure has all four capabilities available.

`generation` equals the checksum-bound `input_manifest_sha256`: it identifies
the saved source generation. The manifest SHA-256 separately pins the exact
delivery/contract revision. Legacy hover/All-available bodies retain their
existing `input_manifest_sha256` field and exact bytes. That field must equal
the selected capability's generation. Cache identity includes both generation
and payload SHA-256; neither a matching filename nor generation alone suffices.
Every body must pass the selected descriptor's size/hash checks. A new source
generation invalidates cached products; a changed descriptor requires its exact
new bytes even if source generation is unchanged. Missing files, mismatched
hashes, unknown generations or cross-generation substitutions are unavailable,
with no fallback to another generation.

**Normal soil activation requires zero history and zero hover body bytes.**
`normal_activation` records those zeros and no required capability payloads.
Metadata discovery is distinct from body loading. For SCAN only,
`SHARED_SCAN_HOVER_EXCEPTION=APPROVED`: the existing **23,518-byte** network-wide
body covers all 131 advertised pairs. Request it only on the **first SCAN hover
cache miss**, then reuse it within the same generation and hash. Do not split it
or prefetch it on activation. History bodies are requested only for the selected
pair and mode; loading Last-3 does not require loading All-available first.
These are producer contract requirements; no browser implementation is included.

## Last-3 selection and reference preservation

For each exact station/depth, the producer chooses the latest up-to-three usable
**completed** water years, declares their ascending `selected_years`, and writes
one dedicated content-addressed Last-3 file. A usable year has at least one
accepted saved finite observation for that pair; no new coverage threshold or
universal daily statistic is introduced. Completed means a WY less than the
saved `current_water_year`, not a claim of a gap-free record. Zero and finite
values above 100% remain usable saved observations. Years need not be adjacent.
With no such year the capability is explicitly unavailable; consumers do not
derive, infer or substitute years.

Last-3 uses the existing history `columns` and exact saved rows for those years,
plus `selected_years`, `selection_policy` and `current_water_year`. It preserves
dates, leap days, sparse gaps, zeroes, above-100 values and composite membership;
it does not pad absent dates or fill gaps. Current-WY rows are excluded and remain
in the existing separate current-product trace. All-available retains its
original scientific meaning, including the existing current-WY authority rule.
Closure copies All-available payloads byte-for-byte, without reprocessing them.

`reference_band` binds the existing authoritative
`docs/data/scan_sms_waterday_percentiles.csv`. Its `source` records that path,
the table's original `build_time_utc` as `source_generation`, and its source
SHA-256. A local immutable copy has exactly the same CSV bytes and a descriptor
bound into this source generation. Its original reference generation need not
equal the later current-feed timestamp: the input binding explicitly pins both.
No reference period, ribbon value, threshold, water-day alignment or statistic
is recomputed. Honor existing `climatology_ok`, `min_years_for_context`, year
ranges and current-WY exclusion fields through the existing SCAN reference
behavior. Availability of the table does not assert that every row passes its
existing reference-eligibility rules. The reference CSV retains its original
inventory, including archive-only records, but it does not create browser
capabilities for those four pairs. Monthly context and other reference products
remain untouched; nothing here defines Dendra/SNOTEL behavior.

## Failure behavior and validation

Export refuses an existing output directory, including an empty directory or a
symlink; it also refuses the published `docs/data` directory and descendants or
an output inside the current-product inputs. Inputs are checked before reading
and again before writing the delivery. Source files are never written. A failed
export can leave a new task-owned incomplete directory for diagnosis; it is not
accepted or published. Previous deliveries remain untouched. No atomic remote
publication is claimed.

The validator requires the external manifest SHA-256 and checks schema,
generation binding, immutable names, exact file sizes/hashes, path allowlists,
symlinks, selected shard identities, browser/archive membership, finite values,
sorted unique dates, water years/days, sensor/depth consistency, counts, date
bounds, and hover equivalence including gaps. It also rejects orphan/unlisted
files. Export additionally checks every serialized history row against its
combined saved-input rows. Standalone validation proves delivery integrity and
internal semantics; it does not independently reacquire or validate provider
observations. Acceptance must retain the trusted input binding and source review.

Closure validation also checks the four capabilities, zero-body activation
policy, generation bindings, exact producer-selected completed years, Last-3
row equality against accepted All-available rows, reference source identity,
and every advertised target. The offline `capability_payload` probe returns
unavailable for missing/mismatched bodies and does not implement a browser/cache.

Bounds are 64 MB per saved input, 256 KB for binding/manifest, 16 MB per output
payload file, 128 MB combined payload, 256 browser histories / 64 stations,
50,000 rows per history, 2,000,000 total rows, 1–8 sensors per daily value, and
dates 1900–2200. The explicit binding narrows those limits to the requested
28/131 roster. R normalization has a 120-second timeout and a 256 MB temporary
CSV limit. No decompression limit is claimed for arbitrary untrusted RDS.

Example with task-owned paths and a pre-bound saved input manifest:

```sh
python3 scripts/soil_moisture/scan_history.py export \
  --archive-rds "$SAVED_SCAN_RDS" --current-dir "$SAVED_SCAN_PRODUCTS" \
  --input-manifest "$TASK/input-binding.json" \
  --input-manifest-sha256 "$INPUT_MANIFEST_SHA256" --output "$TASK/fixture"
python3 scripts/soil_moisture/scan_history.py validate \
  --output "$TASK/fixture" --manifest-sha256 "$DELIVERY_MANIFEST_SHA256"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/soil_moisture -p test_scan_history.py -v
```

To close an already accepted Gate 1 delivery without reopening its RDS, use a
fresh task-owned output. The old fixture and evidence remain unchanged:

```sh
python3 scripts/soil_moisture/scan_history.py close \
  --accepted-fixture "$ACCEPTED_FIXTURE" \
  --manifest-sha256 "$ACCEPTED_MANIFEST_SHA256" \
  --reference-csv "$SAVED_SCAN_PRODUCTS/scan_sms_waterday_percentiles.csv" \
  --output "$TASK/closed-fixture"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/soil_moisture -p 'test_scan_history*.py' -v
```

Promotion requires lead review of this producer fixture and consumer support
before any activation decision. Missing soilDB for live SCAN acquisition, the
separate performance hold, and hosted/provider/operational gates are unchanged.
