# Dendra browser projection profile 1 (offline review only)

`brim-soil-history-1`, network `Dendra`, profile `dendra-history-profile-1`
defines an additive delivery rooted at one caller-supplied directory. It has no
activated URL, `docs/data` ownership, scheduler, publisher or BRIM implementation.
The [consumer contract](BRIM_CONSUMER_CONTRACT.md#dendra-browser-projection-profile-unpublished)
and [product catalog](PRODUCTS.md#dendra-browser-projection-profile-unpublished)
govern compatibility and ownership. SCAN's existing profile and products do not
change. A common family does not imply a common filename or scientific policy.

The offline library is
[`browser_projection.py`](../scripts/dendra/history_acquisition/browser_projection.py).
`export(prepared_root, pins=..., prepared_fingerprint=..., output_root=...)`
requires absolute explicit roots and a fresh absent output under an existing
parent. It accepts only checksum-bound `handoff.json`, `daily-output.json`,
`result.json`, `r-receipt.json`, and each exact selected `lineage/<stream>.json`
from accepted preparation. Pins have exactly `path`, `bytes`, `sha256`.
The original preparation fingerprint is explicit; it is not replaced by the
new projection package fingerprint. It checks successful preparation, identical
science bindings, unchanged R core, interval/seal/scale identity and completed
daily eligibility. No R or provider is called. Pins require a trusted caller;
hashes alone are not scientific acceptance or authentication.

This is a tested library entrypoint, not a new CLI command. `prepare-product`,
collect/resume/verify and historical journals are unchanged. An operator CLI
can be separately reviewed if needed. Errors raise and leave any fresh partial
output for diagnosis; no overwrite, cleanup, fallback or publication occurs.

## Versioned schemas and serialization

Canonical serialization is UTF-8 JSON with sorted keys, compact separators,
ASCII escapes, no nonfinite numbers, and exactly one final newline. Duplicate
JSON keys reject. Numeric booleans are invalid. Unexpected fields reject through
the deterministic closure validator. All dates are ISO civil `YYYY-MM-DD`;
timestamps use explicit UTC `Z`. IDs are lowercase 24-hex strings and hashes
lowercase 64-hex SHA-256. The object shapes below and the module's closed-field
validators are normative. The review packet supplies machine-readable shapes,
fixed bodies and their hashes so the consumer need not infer fields.

Every delivered body has `schema_version`, `network`, `profile`, `generation`,
`kind`. Their constants are the family/network/profile above, a SHA-256 generation,
and respectively `manifest`, `map-index`, `streams`, `hover`, or `history`.

| Kind | Additional version field | Exact additional fields |
| --- | --- | --- |
| manifest | `manifest_schema=dendra-browser-manifest-1` | `publication_state`, `input_binding`, `projection_policy`, `map_indexes`, `files`, `presentation_horizon`, `capability_policy`, `normal_activation`, `limits`, `closure`, `full_por_complete`, `publication_receipt` |
| map-index | `payload_schema=dendra-map-index-1` | `entries` |
| streams | `payload_schema=dendra-stream-descriptor-1` | `identity`, `scale_state`, `normalized_target_unit`, `coverage`, `freshness`, `last_accepted_completed_day`, `presentation_horizon`, `capabilities`, `history_shards`, `point_semantics`, `latest_instantaneous`, `full_por_complete`, `full_por_state`, `lineage_sha256` |
| hover | `payload_schema=dendra-hover-1` | `identity`, `days`, `completed_before`, `slots` |
| history | `payload_schema=dendra-completed-daily-projection-1` | `representation`, `identity`, `water_year`, `lineage_sha256`, `rows` |

`identity` has exactly `station_id`, `stream_id`, `native_unit`, `unit_status`,
`depth_cm`, `orientation`. Depth is finite numeric or null, orientation string or
null. Null remains unknown. Browser identity is the **station_id/stream_id pair**;
depth, orientation and labels never merge independent sensors or replacements.
Units are `Percent`, `VolumetricWaterContent`, `Dimensionless`; unit status is
`verified_percent_conversion` for the first two or `native_only_scale_unresolved`.

## Descriptor and path grammar

A file descriptor has exactly:

```text
kind, path, bytes, sha256, generation, station_id, stream_id, water_year,
row_count, first_date, last_date
```

It binds exact response bytes and identity. `bytes` is a positive integer;
`row_count` is a nonnegative integer. History bounds count accepted rows; hover
bounds count calendar slots, including gaps. Metadata descriptors use row_count
zero and null first/last dates. Index station/stream/WY are null; stream/hover WY
is null. History WY is an integer ending year. Filenames must match exactly:

```text
manifest.json
map-index/0-<sha256>.json
streams/<stream_id>-<sha256>.json
hover/<stream_id>-<sha256>.json
history/<stream_id>/<ending_WY>-<sha256>.json
```

Only one compact page (page `0`) is supported by this bounded profile. Paths are
relative POSIX paths within one delivery root. Only `[a-z0-9/.-]` characters are
allowed, with no empty, `.` or `..` components. Absolute paths, backslashes,
percent encodings (including double encodings), URL schemes, query/fragment
syntax and symlinks reject. There is nothing to URL-decode or canonicalize into
traversal. The kind/prefix, stream/WY filename, hash suffix, descriptor and payload
identity must agree. Producer reads use directory-fd anchored no-follow access,
including ancestor directories, and reject hardlinked/nonregular files.

**This grammar validates a supplied path; it is not a consumer filename recipe.**
Resolve the root's `map_indexes` descriptors, each entry's `descriptor`, then the
selected stream's capability `file`/`files`/`years[].file`. The index entry has
exactly `station_id`, `stream_id`, `descriptor`. Never guess a path from IDs.
The root's sorted `files` array is the complete non-root closure. It repeats the
same descriptors, not extra body download requirements.

## Input, generation and root revision

`input_binding` has exactly `schema_version=dendra-browser-input-1`,
`inventory_sha256`, `preparation_files`, `preparation_science`, `acquisition`,
`as_of`, `daily_schema`, `numerical_policy`, `streams`. Preparation science binds
`collector_fingerprint`, `core_sha256`, `wrapper_sha256`. Acquisition binds
`acquisition_fingerprint`, `evidence_manifest_sha256`, `execution_binding_sha256`,
`task_binding_sha256`. The sealed acquisition retains its old identity. Daily
schema/policy remain `dendra-daily-1.0.0` and
`dendra-daily-1.0.0-frozen-cadence`. Input file pins also bind cadence contexts,
QC, eligibility decisions and original source/query/retrieval records.

The `streams` map is keyed by exact stream ID. Each value has exactly `identity`,
`intervals`, `lineage_sha256`, `metadata_review_sha256`, `scale_decision_sha256`,
`retrieval_first_utc`, `retrieval_last_utc`,
`historical_terminal_source_timestamp`, `source_start_authority`, `rejected_dates`.
Metadata-review SHA binds the ordered accepted decisions; lineage binds their
original metadata/privacy evidence. No metadata values, geometry, endpoints,
private paths or raw observations are copied into the browser product.
`source_start_authority=UNKNOWN_SOURCE_START` deliberately grants no historical
source-start authority, regardless of acquired interval start.

`generation` is SHA-256 of canonical
`{input_binding: ..., projection_policy: ...}`. Policy is the exact module
`POLICY` object: version, fixed-PST offset, limits, four capability rules,
geometry omission, unavailable-latest rule and activation-byte rule. No browser
output body/hash is inside the input binding that the same body embeds.
The **root manifest SHA-256**, supplied out of band with `manifest.json`, pins
the exact delivery revision separately. There is no impossible self-hash.
`publication_state=offline_export_only`, `publication_receipt=null`.

Consumers must retain root revision, generation, identity, descriptor hash/size
and active-selection token while a request is in flight. Cancel/discard stale
selection or generation results. Never mix bodies across generations or retain
a previous hash under a changed descriptor. A build timestamp does not refresh
source observations. Original observation and retrieval vintages remain distinct.

## Coverage, horizons and capabilities

`presentation_horizon` has exactly `mode=INITIAL_PRESENTATION_10_WY`,
`maximum_water_year_count=10`, `current_water_year`, `earliest_water_year`,
`fixed_pst_offset=-08:00`, `completed_before`, `last_completed_date`, `floor_date`,
`full_por_complete=false`, `full_por_state=not_established`. Cutoff is the beginning
of the current fixed-PST day; no DST. The floor is October 1 of current WY minus
ten, inclusive. `as_of` comes from saved preparation, not export wall-clock time.
No observed data are invented to fill that maximum horizon.

`coverage` has exactly `acquired_start`, `acquired_end`,
`completeness=listed_intervals_complete_only`, `horizon_complete=false`,
`intervals`, `source_start_authority`. Min/max acquired bounds are an envelope,
not gap-free coverage. Each nonoverlapping interval has exactly `task_id`, `start`,
`end`, `query_state`, `seal_record_sha256`, `content_sha256`, `parsed_sha256`,
`row_count`. The half-open query is complete at those original bounds.
`COVERED_EMPTY` requires zero native rows; `COMPLETE_NONEMPTY` positive native rows.
Unqueried spans are not acquired-empty; failed/unsealed queries cannot enter
this projection. Empty does not infer a configuration discontinuity.

`freshness` has exactly `evaluated_at`, `retrieval_first_utc`, `retrieval_last_utc`,
`historical_terminal_source_timestamp`, `current_state_claim=false`. The terminal
timestamp is historical evidence, never a current/latest marker. Age may be
computed against the declared evaluation time without moving the observation.

Every capability has `status`, nonempty `reason`, and `generation`.
`capability_policy` names the four explicit Dendra rules; no cross-provider
statistic or SCAN Last-3 rule is imported.

| Capability | Available extra fields and behavior |
| --- | --- |
| hover_30d | `file`; selected-stream body only, requested on first explicit hover needing that stream's data |
| history_last3 | `years`; each has `water_year`, `state`, `reason`, `query_complete_for_water_year`, `file` (descriptor or null) |
| history_all_available | `files`; only selected stream's accepted acquired WY shards in this product horizon |
| reference_band | Always `status=unavailable`, `reason=not_computed`; no numeric array or file |

Last-3 requests exactly current ending-year WY and prior two (e.g. 2024/2025/2026).
Per-year states are `available_acquired_shard`, `acquired_empty` (the acquired
subset is empty, not necessarily the whole year), `unqueried_not_acquired`, or
`unavailable_held` (acquired native input but no accepted daily shard). Whole-WY
query completeness is separate and includes no claim that the current WY is
complete. Never substitute older populated WYs. Each request transfers only that
selected stream/WY shard; it does not download an all-years shard to slice.

All-available means accepted acquired daily history in this product horizon;
it never means full provider POR. Reference/context remain unavailable. UI copy
belongs to the consumer. Empty available file lists are honest empty products.

Resolved streams have `scale_state=RESOLVED_PERCENT`, target unit `Percent`.
Dimensionless has `NORMALIZED_PERCENT_HOLD`, target null, empty history_shards,
and unavailable hover/Last-3/All-available with reason `scale_unresolved`.
It cannot enter percent-VWC filters, context or numeric markers. Its 52 native
records remain in the sealed archive, while coverage retains the count and
the separate complete-empty subsequent interval.

`normal_activation` is exactly `{history_body_bytes:0, hover_body_bytes:0,
required_capability_payloads:[]}`. Root/index/stream metadata discovery is separate
from capability body transfer. Do not eagerly fetch bodies listed in closure.

Hover has exactly 30 ascending completed fixed-PST dates ending at
`last_completed_date`. Each slot has `date`, `state`, `mean_percent`.
States are `accepted`, `ineligible`, `covered_empty`, `missing_measurement`,
`unqueried`; only accepted has a finite percent value, including numeric zero.
Known incomplete query coverage remains unqueried. No interpolation or stale-date
shifting occurs. History rows, unlike hover slots, contain accepted days only;
consumers must break the line across absent calendar dates.

## Completed daily and instantaneous point shapes

History preserves accepted R rows unchanged. Their exact fields are:

```text
date, water_year, dowy, water_day, water_year_days, water_day_aligned,
mean_native, mean_percent, mean_value,
n_valid, n_total, n_null, n_invalid, n_missing, n_duplicate_conflicts,
n_duplicate_rows, n_out_of_range, expected_samples, cadence_seconds,
cadence_source, coverage_fraction, temporal_span_fraction, plot_eligible, flags,
representation, identity, query_complete, presentation_eligible, source_intervals
```

`representation=completed_daily`; plot/query/presentation eligibility must all be
true. Dates are unique, sorted and completed; WY, true DOWY, WY length and
leap-aligned water-day must agree. Actual observed `n_*` count fields are
nonnegative integers with positive `n_valid`. `expected_samples` is a finite
positive JSON number, integer or fractional; null, strings and booleans reject.
It preserves the accepted R value `86400 / cadence_seconds` without rounding or
integer conversion (for example, cadence 17400 yields 4.96551724137931).
This coordinated backward-compatible widening retains `dendra-history-profile-1`;
the descriptor shapes, paths and all other validation rules remain unchanged.
Means/cadence/fractions are finite (no numeric nulls); cadence
positive, fractions in [0,1]. `flags` is a string array. `mean_value=mean_percent`,
native ×1 for Percent or ×100 for VWC (round-trip tolerance 1e-10).
No mean is recomputed. Each `source_intervals` element has exactly `task_id`,
`query_state`, `seal_record_sha256`, `content_sha256`, `parsed_sha256`; contributors
must cover the complete day. Generation is inherited from the shard header;
its lineage hash and identity bind all rows.

Stream `point_semantics=completed_daily_separate_from_latest_instantaneous`.
The real fixture's point state has exactly:

```json
{"schema_version":"dendra-latest-instantaneous-projection-1","status":"UNAVAILABLE","reason":"no_latest_witness_in_sealed_historical_input","record":null}
```

The separate AVAILABLE specimen has `schema_version` above, `status=AVAILABLE`,
`reason=synthetic_contract_example`, `synthetic=true`, `publication_allowed=false`,
`current_state_claim=false`, and `record`. Its record has exactly:

```text
representation, identity, native_value, normalized_percent, source_timestamp,
retrieved_at, evaluated_at, observation_age_seconds, retrieval_age_seconds,
scale_state, generation, source_lineage_sha256, presentation_eligible, latest_witness
```

Representation is `latest_instantaneous`, scale resolved, both eligibility and
latest_witness true. Numeric native/normalized values obey the same ×1/×100 rule.
Actual source time ≤ retrieval ≤ evaluation; ages are their exact elapsed seconds.
The test-only station/stream IDs are 24 zeroes/ones. The stale example keeps its
original February 29 timestamp. Never append/connect this point to a daily trace
or move it to today. It cannot validate as a daily mean or historical terminal.

This gate only admits AVAILABLE in the explicitly synthetic specimen validator;
real exporter always emits UNAVAILABLE. The shape is frozen for consumer testing.
A future real latest-evidence admission route needs separate authorization and
review; a historical terminal can never supply that authority. The synthetic
specimen is outside the real closure and must not be served as provider evidence.

## Bounds and validation

This bounded profile supports at most 32 selected streams, 10 WYs, 366 rows per
shard, 1,048,576 bytes per body, 262,144 bytes for root, 386 total files and
33,554,432 total delivery bytes. Pinned input files are at most 8,388,608 bytes
each. One page is supported; this is not a 434-stream production capacity claim.
`closure` carries stream_count, station_count, body_file_count, body_bytes and
accepted_daily_rows. Total size adds the actual root bytes; it is measured out of
band to avoid a recursive root size/hash dependency.

`validate_delivery(root, manifest_sha256=..., generation=...)` verifies the exact
root revision, bounds, safe paths and all descriptor bodies. It reconstructs the
canonical graph from the input metadata and accepted history rows and compares
every byte. This validates stream/hover/history semantics, descriptor references,
capabilities and exact file closure, including rejection of unrelated files.
It does not recalculate R science or authenticate untrusted pins. The producer's
full offline check is not a browser eager-fetch requirement: consumers validate
the selected response size/hash/header/identity/bounds against the pinned graph.

Missing/tampered data, incompatible generation/source binding, unresolved numeric
routes, duplicate daily identity, invalid WY/date/QC encoding or path escape
fails closed. No retry, generation fallback or silent empty substitution occurs.
Consumer/launcher implementation, builds, release, latest acquisition, reference
science, sharding capacity and publication remain separate gates.
