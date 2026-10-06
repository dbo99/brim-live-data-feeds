# Candidate-3 offline delivery contract

`dendra-candidate3-delivery-1` is an unpublished, non-production producer-local
export of frozen `dendra-00g-local-candidate-3`. It has no configured public URL,
workflow, publisher integration or activated consumer. Every generation declares
`publication_state=offline_export_only` and `publication_eligible=false`.

This is independent of [Dendra browser profile 1](DENDRA_BROWSER_PROFILE.md).
It neither replaces nor extends `dendra-history-profile-1`, its accepted-history
shape, sealed-acquisition binding or limits. Any future mapping into a browser or
BRIM contract requires separate counterpart review. No obligation for a private
consumer follows from this export. The [storage and time-series
contract](DENDRA_DATA_STORAGE_AND_TIMESERIES_CONTRACT.md) remains the authority for
the underlying daily science. This adapter performs delivery only.

## Frozen source and admission

The CLI admits exactly the reviewed Candidate-3 manifest:
`23efbf1811f90ace46e7cb7ddec67b406d913659b3f560b43fdd9b492cee5296`.
Its seven freeze pins are explicit in
[`candidate3_delivery.py`](../scripts/dendra/history_acquisition/candidate3_delivery.py).
`DAILY_ASSET_MANIFEST.json` binds 310 exact streams, 620 daily CSV/Parquet assets
and 930 science/input/proof files. All pins and asset byte counts are checked.
The `series`, `science` and `evidence` directories must have exact closure;
`series` additionally contains one type sidecar per stream.

The delivered source binding identifies:

| Identity | Frozen SHA-256 |
| --- | --- |
| `DAILY_SERIES_CATALOG.csv` | `c3af4e04a807fc3a9a96fe61c223ff9ec80dadefa6ca9ccf7016595c3b108bf4` |
| `DAILY_SERIES_CATALOG.parquet` | `5abac5186acc57433e064ca444c45a5661aebdf8298171f6604f5c877b298d92` |
| `00G_CANDIDATE_SCHEMA.json` | `2e3a9ddcbc3c56cb4bd633fa2b3fcadb78e255bfef9b7fb907a6793610342c3d` |

The catalog CSV is checked against the hash-pinned review ZIP member. The catalog
Parquet digest was independently recorded during the freeze audit and is pinned
by this adapter. The type sidecar is hashed and both catalog representations
must agree on every typed cell. Catalog rows without daily assets remain outside
the daily export; all materialized daily entries must match the manifest exactly.
One additional frozen catalog row is an explicit `DUPLICATE_EXPORT_ALIAS`. Its
`duplicate_of` key must resolve to the sole materialized canonical row for that
asset, with identical station/stream/depth/unit identity. The alias is recorded in
`catalog_aliases` in the source binding and adds no stream or row. Implicit
deduplication and conflicting aliases fail; this is not merging independent sensors.

The source-binding component records portable relative names, byte counts and
SHA-256 for every consumed source file, including every daily twin, type sidecar
and science/provenance file. It also records the producer's `as_of`, completed-day
boundary, daily policy, historical bulk policy and hash identities of native
catalogs, producer sources, retained coverage/metadata/impact evidence and the R2
manifest. Those upstream hashes are references bound by the frozen manifest;
the adapter does not follow its workstation paths, reopen journals or rebuild
native observations. Science/input/proof bodies are checked but not copied into
delivery metadata. No workstation paths are exported.

Parquet is the canonical daily representation. Both twins must pass their frozen
hashes, schemas, row counts and exact typed-cell comparison before a stream is
used. CSV numbers parse to the same finite binary64 numbers as Parquet doubles;
no tolerance or decimal rounding is allowed. Empty nullable cells become JSON
null, booleans retain their type, and `flags` is decoded from its JSON array string.
The twins represent one logical stream, never two. Type sidecars, catalog,
manifest, exact row identities and schema jointly bind that stream.

## Complete daily history

Every frozen daily row occurs exactly once, keyed by exact station ID, exact
stream ID and date. Depth, labels, unit, organization and display names never
merge streams. The source census is an admission check, not a target to synthesize:

| Census | Count |
| --- | ---: |
| Stations | 97 |
| Exact streams | 310 |
| All rows | 623241 |
| `ACCEPTED` | 559122 |
| `WITHHELD_BY_EXISTING_SCREEN` | 36145 |
| `MISSING` | 27873 |
| `UNRESOLVED_SEMANTICS` | 101 |

There is no filling, interpolation, calendar expansion, filtering of nonaccepted
states, depth inference, new cadence estimate or scientific recomputation.
Percent retains its ×1 semantics and resolved VolumetricWaterContent its already
stored ×100 interpretation. Native-only unresolved Dimensionless must have no
`mean_percent` or `daily_mean_vwc_percent`. Numeric zero remains zero; null remains
null. `UNKNOWN` depth remains null. The frozen record schema requires a numeric
`conversion_multiplier`; its mere presence never authorizes normalizing an
unresolved unit. No actual frozen Candidate-3 daily rows are Dimensionless; the
native-only rule is additionally exercised by synthetic fixtures.

One frozen stream has catalog depth status `CONFLICTING`, one withheld daily row
with that status, and 3374 historical daily rows marked `UNKNOWN`. All have null
depth. The descriptor reports `catalog_depth_status` separately from
`daily_depth_status_counts`; each row retains its original status. The adapter
does not adjudicate this historical metadata difference. Exact station/stream,
depth value, native unit and multiplier must still agree across source layers.

Days are completed fixed UTC-08 days year-round. `water_year` is the ending year;
`dowy`/`water_day` are true DOWY, and `water_day_aligned`/`plot_day_aligned` are the
separate leap-aligned plotting coordinate. The adapter validates these stored
coordinates, then shards by the stored WY without replacing any value. It never
carries prior-WY values forward. Generation time is solely an export-build time,
not a new observation, source, retrieval or provider-freshness timestamp.
Latest instantaneous observations are not derived from daily rows.

Accepted provenance permits exactly these triplets:

| `processing_version` | `source_quality_status` | `source_quality_reason` | Rows |
| --- | --- | --- | ---: |
| `dendra-bulk-daily-2` | `RESOLVED_CLEAR` | `EXACT_DAY_MATCH_NO_PROVIDER_QUALITY_VETO` | 138735 |
| `dendra-bulk-daily-3` | `PROVIDER_READY_TO_USE` | `API_Q_UNAVAILABLE_BULK_READYTOUSE` | 420356 |
| `dendra-bulk-daily-3` | `PROVIDER_READY_TO_USE` | `API_Q_PARTIALLY_KNOWN_BULK_READYTOUSE` | 31 |

`PARTIALLY_KNOWN` is not a q=good claim. No unavailable q is manufactured, no reason
is renamed and no status is promoted. Any additional accepted triplet fails and
requires review. All nonaccepted status/reason/version fields remain exactly as
frozen. Reconstruction detects any drift in their inventories as well.

## Closed daily row

The row object is the actual closed `records.items` definition under
`properties.examples.properties.unknown_depth` in `00G_CANDIDATE_SCHEMA.json`.
Its `known_percent` and `known_fraction` definitions must be identical. The
fixture envelope itself is not reused: its small example limits do not constrain
this complete history. All 49 fields are required, with no additional fields:

| JSON type | Exact fields |
| --- | --- |
| integer | `water_year`, `dowy`, `water_day`, `water_year_days`, `water_day_aligned`, `n_valid`, `n_total`, `n_null`, `n_invalid`, `n_missing`, `n_duplicate_conflicts`, `n_duplicate_rows`, `n_out_of_range`, `observation_count`, `nominal_range_observations`, `plot_day_aligned`, `valid_sample_count` |
| number or null | `mean_native`, `mean_percent`, `mean_value`, `daily_mean_vwc_percent`, `expected_samples`, `cadence_seconds`, `coverage_fraction`, `temporal_span_fraction`, `depth_cm`, `expected_sample_count`, `sample_count_fraction`, `first_last_span_fraction` |
| boolean | `plot_eligible`, `query_complete`, `source_empty` |
| string | `date`, `cadence_source`, `daily_status`, `source_quality_status`, `source_quality_reason`, `stream_id`, `export_local_series_key`, `station_id`, `station_name`, `depth_status`, `native_unit`, `processing_version`, `date_pst_fixed` |
| string or null | `accepted_stream_id`, `latest_source_timestamp_utc` |
| string array | `flags` |
| number | `conversion_multiplier` |

Both dates retain `YYYY-MM-DD` spelling. Numbers must be finite. Accepted values
and all frozen diagnostic/native means on other states remain numerically exact;
integer-versus-double spelling in a number field may become canonical Parquet
double spelling. No count, flag, coverage fraction or query/source provenance
field is dropped. The root source binding embeds the exact row schema and its
original schema-file identity.

## Output files and fields

All components are UTF-8 canonical JSON: sorted keys, compact separators, ASCII
escapes, finite numbers, no duplicate keys and one trailing LF. Arrays have the
explicit orders below. Every component has `schema_version` and `kind`.
`<sha256>` is the full lowercase SHA-256 of that component's bytes.

```text
manifest.json
source-binding.<sha256>.json
stations.<sha256>.json
streams/<station_id>/<stream_id>.<sha256>.json
history/<station_id>/<stream_id>/wy-<ending_year>.<sha256>.json
```

The root `kind=generation` contains `candidate_id`, the two publication fields,
`export_built_at`, `generation_sha256`, `candidate_manifest_sha256`, `adapter`,
`source_binding`, `station_index`, `census`, `wy_shards`, `processing_versions`,
`accepted_provenance`, `files`, `output_file_count` and `closure_sha256`.
`adapter` binds the policy identifier, adapter source SHA and embedded Arrow reader
source SHA. `processing_versions` maps each exact version to its all-state count;
`accepted_provenance` is sorted by the three exact string fields and includes
`rows`. `census.states` includes all four states, even when zero in a synthetic test.

Every file descriptor is exactly `{path,bytes,sha256}`. `files` lists all components
except the root itself, sorted by path. `output_file_count` includes the root.
`closure_sha256` hashes the canonical `files` array. The root is externally pinned
by its SHA-256 in the CLI result, avoiding a self-referential checksum. The
generation identity hashes `{schema_version,source_binding,census,adapter,
closure_sha256}`. `export_built_at` is intentionally excluded from that identity;
holding it fixed gives byte-identical roots for identical source and adapter.

`kind=station_index` contains `stations`, sorted by exact station ID. Each entry
is `{station_id,streams}`; `streams` is sorted by exact stream ID and each entry
is `{stream_id,descriptor}`. Every reference resolves to exactly one descriptor.

`kind=stream` contains `identity`, `source`, `rows`, `states`, `history`,
`catalog_depth_status` and `daily_depth_status_counts`.
`identity` preserves the eight row identity fields: `station_id`, `station_name`,
`stream_id`, `accepted_stream_id`, `export_local_series_key`, `depth_cm`,
`native_unit`, `conversion_multiplier`. All rows must match it. Daily depth status
remains a row field, with exact status counts alongside the separate catalog status.
`source` binds both twin descriptors, science/input/proof hashes, native-asset and
original-CSV hashes, `observation_api_q`, `provider_purpose` and
`historical_source_route`. `history` is sorted by stored WY; each entry is
`{water_year,rows,states,file}`. Only years with frozen rows appear.

`kind=history` contains `station_id`, `stream_id`, `water_year` and `records`.
Records are ordered by date with no duplicate stream/date key. `states` in stream
and shard summaries counts only present states, matching the source manifest.
The source-binding component is described above; its sorted source `files` list
is separate from the output `files` list and never resolves to output files.

## Offline execution and failure behavior

The local prerequisites are Python 3, `clang++` with C++17, `pkg-config`, and the
same installed Arrow/Parquet C++ libraries used by the frozen bulk producer. No
packages are installed and no provider or network client is invoked. A small
read-only Arrow bridge is compiled in a fresh retained system temporary directory.
It accepts already hash-verified bytes on stdin, never paths from source metadata,
and emits double values at round-trip precision. Compiler calls time out; each
reader call has a 60-second timeout. Input/component files are limited to 64 MiB,
decoded tables to 128 MiB, tables to 100000 rows and 128 columns, individual
strings to 65536 bytes, streams to 2000 and output entries to 20000. These resource
guards accommodate all frozen Candidate 3; they do not import profile-1 limits.
Construction retains at most one stream's daily records, plus compact metadata.

Both roots must be explicit absolute paths. The output's parent must already
exist; the output directory must be absent (including an existing empty directory).
Roots must be disjoint. `docs/data` destinations are refused. Rooted no-follow I/O
rejects symlink ancestors, traversal, symlink files and hardlinked files. Output
creation uses exclusive writes; a failure retains partial evidence without
overwriting any file. Only a completed, validated root constitutes an export.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/dendra/history_acquisition/candidate3_delivery_cli.py \
  --source-root /absolute/frozen-candidate \
  --output-root /absolute/fresh-evidence/export
```

The JSON result includes the manifest, generation, source-binding and closure
hashes, exact census, shard/file/byte counts, provenance and processing-version
inventories and validation results. Revalidation uses the same two roots plus
`--validate-manifest-sha256` with that saved manifest digest; it writes no delivery
files. Library expectation overrides exist solely for compact synthetic fixtures;
the CLI exposes no production pin/census override.

Validation reopens all source pins, twins and provenance, reconstructs every
component deterministically, and compares every output byte against that
reconstruction. It checks the complete file and directory closure, including
unexpected empty directories. This proves every source stream and row appears
once, every per-WY total and state/provenance inventory agrees, all references
resolve, and all values (including zeros/nulls) are conserved. Merely updating an
output checksum after changing a row cannot pass. Reconstruction writes no second
export and verifies every shard, exceeding a representative-only comparison.

Failures include source/hash/census/twin disagreement, duplicate identities or
daily keys, illegal provenance, missing or altered components, value/state/version
drift, wrong WY, fabricated normalized values and unsafe paths. There is no
silent filtering, automatic repair, partial acceptance, fallback publication or
mutation of the frozen source. The adapter does not touch publisher/pending/state
machinery or rebind acquisition fingerprints.

Focused synthetic tests are in
[`test_candidate3_delivery.py`](../tests/dendra/test_candidate3_delivery.py):

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/dendra -p 'test_candidate3_delivery.py' -v
```

These fixtures use real local CSV/Parquet twins, all states and accepted triplets,
native/normalized values, precision, unknown depths, independent streams,
determinism and adversarial integrity/path failures. Tests deny socket/DNS calls.
Large-candidate evidence and generated files belong only in the explicitly
authorized untracked evidence root, never in tracked source or `docs/data`.
