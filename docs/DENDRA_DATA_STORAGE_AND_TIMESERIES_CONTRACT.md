# Dendra data storage and time-series assembly contract

**Status:** Prepared by L03 for installation in `dbo99/brim-live-data-feeds`. Once committed and referenced by the applicable Dendra acquisition/processing instructions, this document is a governing repository contract for Dendra archive interpretation and derived time-series assembly.  
**Scope:** Dendra native observations, station/stream/deployment identity, archive discovery, canonical raw-resolution tables, fixed-PST daily products, provenance and human-readable exports.  
**Non-scope:** BRIM application source, deployment/storage-service creation, provider permissions, scheduler activation, SCAN/SNOTEL semantics, reference preprocessing.

## 1. Purpose

Dendra native acquisition is deliberately provenance-first and machine-auditable. Provider responses are retained in content-addressed objects under Journal/task roots, with seals, receipts and exact scope bindings.

That representation is not by itself sufficient for durable human understanding. A future operator must be able to answer, without reverse-engineering acquisition code:

- Where is the authoritative native data for this exact station/depth stream?
- Which native objects belong to the same exact provider stream?
- What dates/intervals were actually queried?
- Which intervals were valid-empty, failed, unqueried or inactive?
- How are overlapping fetched intervals assembled without duplicating observations?
- Which depth/unit meaning applies?
- How is native UTC time assigned to the fixed-PST daily product?
- What file can an analyst open in R, Python, Excel, DuckDB or another common tool?
- How can the derived table be traced back to immutable native evidence?

This contract prevents semantic loss even when all native bytes still exist.

### Historical website bulk-export route

`dendra-historical-bulk-readytouse-1` applies only to historical Dendra website
bulk exports. Exact CSV hash/column identity, the existing corroborated/exact
datastream-mapping threshold, target VWC, resolved unit/scale, applicable reviewed
accepted or corroborated fixed UTC-08 timestamps, explicit provider `ReadytoUse`
purpose, and no affirmative source/configuration conflict admit a nonempty trace
to the unchanged BRIM daily range/cadence/coverage screen. Unknown depth alone
does not block admission; it remains null, and conflicting depth remains explicit.

`PROVIDER_READY_TO_USE` is source-processing eligibility, not observation QA.
Unavailable original API `q` stays `UNAVAILABLE`; a trace with retained API
evidence for only some intervals is `PARTIALLY_KNOWN`. Neither means q=good.
Catalogs, QA and private provenance retain the source route, purpose, admission
basis, API-quality availability and daily dispositions independently. Known API
quality vetoes and applicable approved source exclusion/quality-claim intervals
remain effective. Original values and range diagnostics remain preserved, with
no clipping, interpolation, forward-fill, unit reinterpretation or depth guessing.

Raw, StatusInformation, unknown/conflicting purpose, unresolved units, ambiguous
identity, unresolved time and explicit source/configuration conflicts remain held.
All-null selections remain cataloged without fabricated observations. Bulk source
gaps do not claim valid-empty API queries or complete acquired POR. Completed-day
science, exact stream isolation, conversions and calendar semantics are unchanged.

The local bulk adapter retains candidate-2 consumer record fields/schema; its
existing `source_quality_status` string can explicitly state
`PROVIDER_READY_TO_USE`, with an unavailable/partially-known API-quality reason.
Actual matched API days retain their existing `RESOLVED_CLEAR`/quarantine/hold
semantics. Prior accepted, withheld and missing R2 rows remain exact; only days
previously unresolved solely for missing historical-bulk/API quality evidence
may be re-screened. Observation-set differences and other blockers remain held.

Adapter row limits are resource safeguards, not scientific eligibility. Historical
bulk series above the ordinary 1,500,000-row whole-series bound use two bounded
passes over complete fixed UTC-08 days. The first preserves the core's normalized
within-day interval mode; the second uses that frozen context and carries the
previous observed day's cadence through the unchanged core. Transport batches
contain at most 65,536 rows, complete days at most 1,500,000 raw rows, and cadence
summaries at most 65,536 interval bins. The offline input guard is 50,000,000 rows
per series. A capacity/order failure stops the candidate; it never clips,
downsamples, fills gaps or changes a daily screen.

This route does not weaken API/native acquisition, incremental API updates or
live API quality contracts. Those routes continue to preserve and use actual
provider quality/status evidence under their existing policies. The bulk policy
does not authorize acquisition, backfill, live activation or publication.

## 2. Four-layer authority model

Dendra data are interpreted through four distinct layers. Do not collapse their roles.

### Layer A — immutable native evidence

Authoritative evidence includes:

- provider response bodies stored as content-addressed native objects;
- Journal/task records;
- reservations/attempt receipts;
- seals;
- exact query intervals;
- source/query/retrieval timestamps;
- provider status/completion evidence;
- reviewed-scope/config/authority bindings.

Typical object path:

`objects/<sha256>.bin`

The `.bin` filename is an opaque content-addressed container name. Never infer serialization, station, depth, unit or time range from that filename alone.

Native bundles must not be manually edited, reserialized or renamed to make them easier to read.

### Layer B — station/stream/deployment catalog

One catalog is the authority for interpretation identity:

- provider;
- subprovider key/name/label;
- station ID;
- station name;
- provider stream/datastream ID;
- source-supported numerical depth and depth units;
- sensor/deployment identity where required;
- native unit;
- accepted unit-resolution status;
- station coordinates and source precision;
- supported source start;
- known identity/depth/unit/placement epochs if a real change exists;
- evidence locators.

No guessed depth. No nearest-depth substitution. No transfer of depth between streams.

### Layer C — asset-location map

One generated asset map tells readers where authoritative bundles and derived products physically live.

The map must identify, at minimum:

- exact station ID;
- exact stream ID;
- depth;
- logical product type;
- physical path/root;
- task/interval coverage;
- seal/object hashes or manifest identity;
- current/superseded status;
- product version;
- generation time.

Paths may change through an approved migration; identities and provenance must not.

The asset map, not workstation folklore, is the durable answer to "where is the data?"

### Layer D — canonical analyst-facing time-series products

Every accepted station-depth stream should eventually have:

1. a canonical native/raw-temporal-resolution table;
2. a canonical fixed-PST daily table;
3. human-readable CSV export(s);
4. checksummed manifests linking the tables to Layers A-C.

Derived products are convenient representations, not replacements for native evidence.

## 3. Canonical file formats

### 3.1 Efficient canonical table: Apache Parquet

Use Parquet for canonical normalized tabular products unless a later versioned contract explicitly replaces it.

Reasons:

- typed columns;
- compact storage;
- efficient column/range reads;
- common support in R, Python, Arrow, DuckDB and other tools;
- practical for long native-resolution series.

Do not store the only authoritative interpretation in a proprietary or application-specific binary format.

### 3.2 Human-readable interchange: CSV

Generate CSV mirrors/exports for accepted streams.

Daily CSV should normally be one file per station-depth stream.

Native-resolution CSV may be:

- one file per station-depth stream when practical; or
- partitioned by water year for large records.

CSV is for inspection/interchange. Parquet remains the canonical normalized table when both exist.

CSV output must use UTF-8, an explicit header row, ISO-8601 timestamps/dates and unambiguous null representation.

### 3.3 Git policy

Do **not** put full native/subdaily Dendra histories into production Git.

Git may contain:

- this contract;
- schemas;
- code/tests;
- small fixtures/examples;
- compact catalogs/manifests;
- product-contract examples.

Large native and normalized histories live in the approved external data library and are located through the asset map.

## 4. Logical product identity and naming

A product must be identifiable by exact provider identity, not only a friendly station name.

Preferred filename stem:

`<station_id>__<stream_id>__depth-<NNN>cm`

Examples of logical filenames:

- `<stem>__native.parquet`
- `<stem>__native.csv`
- `<stem>__daily_fixed_pst.parquet`
- `<stem>__daily_fixed_pst.csv`
- `<stem>__manifest.json`

If native CSV is partitioned:

- `<stem>__native__WY2024.csv`
- `<stem>__native__WY2025.csv`

Friendly station names belong in columns/metadata and may appear in directories, but exact IDs remain part of canonical identity.

## 5. Canonical native-resolution table

One row represents one accepted source observation for one exact provider stream.

Minimum required columns:

| Column | Meaning |
|---|---|
| `provider` | `Dendra` |
| `subprovider_key` | exact provider organization/subprovider key when known |
| `subprovider_name` | source organization name |
| `station_id` | exact provider station ID |
| `station_name` | human-readable station name |
| `stream_id` | exact provider stream/datastream ID |
| `depth_cm` | source-supported exact numerical depth in cm |
| `source_timestamp_utc` | actual provider observation timestamp |
| `value_native` | source-reported numerical value before BRIM unit conversion |
| `unit_native` | source/native unit label |
| `value_vwc_percent` | accepted normalized VWC-percent value when scientifically resolved; otherwise null |
| `unit_resolution_status` | percent / resolved VWC conversion / native-only unresolved or equivalent |
| `source_flag` | source quality/status flag if provided |
| `query_start_utc` | exact containing query/task interval start |
| `query_end_utc` | exact containing query/task interval end, exclusive |
| `retrieved_at_utc` | actual retrieval time or authoritative attempt-receipt time |
| `task_id` | acquisition task identity |
| `archive_object_sha256` | native object identity |
| `normalization_version` | normalized-table transformation version |

Additional provider fields should be retained when useful and source-supported.

Never overwrite the native value with the converted value. Keep both when conversion is accepted.

## 6. Canonical daily fixed-PST table

One row represents one fixed-PST calendar day for one exact station-depth stream.

Minimum required columns:

| Column | Meaning |
|---|---|
| `station_id` | exact station ID |
| `station_name` | human-readable station name |
| `stream_id` | exact stream ID |
| `depth_cm` | exact depth |
| `date_pst_fixed` | fixed UTC-08 day label |
| `water_year` | ending-year WY |
| `dowy` | true day-of-water-year |
| `plot_day_aligned` | separate leap-aligned plotting coordinate |
| `daily_mean_vwc_percent` | arithmetic mean when unit is resolved and day is eligible |
| `valid_sample_count` | count used in the mean |
| `expected_sample_count` | expected count when scientifically established |
| `sample_count_fraction` | valid / expected when expected is established |
| `first_last_span_fraction` | daily temporal-span diagnostic when implemented |
| `daily_status` | accepted / withheld / missing / source-empty / other explicit state |
| `latest_source_timestamp_utc` | latest actual source observation contributing to the day |
| `processing_version` | daily-processing contract/version |

Unresolved native-only Dimensionless streams do not receive a fabricated percent-valued daily mean.

## 7. Exact stream assembly rules

### 7.1 One exact stream at a time

Never combine different stream IDs into one continuous trace merely because:

- station is the same;
- labels look similar;
- depths are equal;
- one stream appears to replace another.

A real versioned deployment/stream handoff may be represented only by an explicit catalog rule with evidence.

### 7.2 One exact depth at a time

Do not:

- average across depths;
- choose nearest depth;
- fill a missing depth from another sensor;
- infer depth from model/name suffix;
- silently relabel depth.

### 7.3 Historical continuity

For an exact provider stream, a source-supported numerical depth applies across its observed record from supported source start unless affirmative evidence shows a relevant change.

Do not require a redundant versioned "depth effective date" when none exists.

### 7.4 Query-interval union

Native task intervals may be numerous and may overlap because of recovery/catch-up.

To assemble a stream:

1. select only sealed/accepted native task coverage for the exact stream;
2. order task intervals by exact UTC interval;
3. read the sealed native objects tied to those tasks;
4. retain original source timestamps and values;
5. union observations by exact source identity;
6. do not count duplicate retrieval copies as additional observations.

Do not concatenate files blindly by directory order.

### 7.5 Duplicate observations

If two retained rows have the same exact stream ID, timestamp, value and compatible source flags, they may be treated as duplicate copies of the same source observation for normalized output.

Preserve enough provenance to show every native object in which the observation appeared.

If the same stream/timestamp has conflicting value or scientifically consequential flags:

- do not choose "latest file wins";
- preserve conflict evidence;
- mark/hold the affected normalized observation according to the applicable processing rule;
- require explicit resolution before publishing a single authoritative value.

## 8. Coverage-state rules

The following are distinct:

- **observation exists**;
- **zero observation value**;
- **null/missing value**;
- **complete-valid-empty query interval**;
- **failed query interval**;
- **unqueried interval**;
- **inactive/pre-source-start interval**.

Never convert one into another.

A valid-empty query proves that the provider returned a complete valid response with no observations for that interval. It is not zero soil moisture.

A gap in observations inside successfully queried coverage remains a source gap.

No forward-fill.

## 9. POR definition

For this program, **period of record (POR)** for an admitted exact stream means:

> source-supported start through the current declared acquisition cutoff has been queried/covered according to accepted archive semantics, with source gaps and valid-empty intervals preserved honestly.

POR does **not** mean:

- every timestamp exists;
- every day is eligible for a daily mean;
- every observation passes quality filters;
- the source operated continuously;
- hosted/publication freshness is current.

POR status and display-window selection are separate.

## 10. Time semantics

### 10.1 Source/native time

Provider observation timestamp `t` is UTC.

Keep the actual UTC source timestamp in every native-resolution product.

### 10.2 Fixed-PST daily day

Accepted Dendra daily science uses a fixed UTC-08 day all year.

A fixed-PST day is bounded by:

`08:00:00Z` to the next day's `08:00:00Z`

Do not apply daylight-saving transitions.

### 10.3 Daily statistic

For an eligible completed fixed-PST day:

- use the arithmetic mean of accepted observations for the exact stream;
- do not mix sensors/depths;
- do not forward-fill;
- retain sampling/coverage diagnostics.

### 10.4 Water-year fields

- water year uses the ending year;
- `dowy` is the true day of water year;
- leap-aligned plotting coordinate is a separate field and must not replace true DOWY.

### 10.5 Latest instantaneous

`latest_instantaneous` is separate from daily means and retains its actual source timestamp.

## 11. Unit semantics

Accepted current rules:

- `Percent` -> multiplier x1 for VWC percent;
- resolved `VolumetricWaterContent` -> multiplier x100;
- unresolved `Dimensionless` -> native-only unless separately accepted.

Never infer conversion solely because values "look like fractions" or "look like percentages."

Both native value/unit and normalized value/status must remain inspectable.

## 12. Provenance manifest

Each canonical normalized product must have a manifest or equivalent asset-map entry recording:

- exact station/stream/depth identity;
- source-supported start;
- product coverage end;
- POR status;
- native unit and conversion decision;
- source native bundle/task/seal/object references;
- input object SHA-256s or an authoritative aggregate manifest hash;
- normalization/daily-processing code version;
- generation timestamp;
- output file path;
- output SHA-256;
- row count;
- first/last source timestamp;
- gap/valid-empty/conflict summaries;
- supersedes/superseded-by identity if applicable.

A derived file without lineage is not an authoritative BRIM-Live product.

## 13. Human-facing index

Generate a compact human-readable index of accepted products.

For each station-depth stream, show at minimum:

- station name;
- station ID;
- stream ID;
- depth;
- native unit / normalized unit status;
- source-supported start;
- acquired-through time;
- water years represented;
- full water years;
- POR yes/no;
- native Parquet path;
- native CSV path(s);
- daily Parquet path;
- daily CSV path;
- manifest path;
- any unresolved warnings.

This index should make it possible to find a usable time series without inspecting Journal internals.

## 14. Rebuildability and authority

Normalized Parquet/CSV products are rebuildable from:

1. immutable native bundles;
2. catalog identity;
3. asset map;
4. this contract and versioned processing code.

They must not become the only surviving copy of source evidence.

An optional SQLite/DuckDB index may accelerate discovery/query but is rebuildable and is never the sole authority.

## 15. Migration and backup

Move whole native bundles with Journals/receipts/anchors, not detached `.bin` objects.

Before an approved move:

1. record original root;
2. verify independent backup/target;
3. copy/move whole bundle;
4. verify membership/bytes/hashes and required modes/links;
5. run fresh-process readback;
6. update asset map;
7. retire old mapping only after verification.

Do not rewrite embedded provenance just to make paths prettier.

Storage service/bucket creation, retention policy and costs remain separate deployment decisions.

## 16. Repository integration requirement

After this file is installed, applicable Dendra acquisition/processing instructions must point to it explicitly.

At minimum, `docs/DENDRA_LOCAL_ACQUISITION.md` should state that any task that:

- interprets existing native objects;
- assembles history;
- generates normalized/native-resolution tables;
- creates daily products;
- migrates archive bundles; or
- prepares a consumer fixture

must read and comply with this contract.

If repository `AGENTS.md`/nearest instructions have an established documentation-reference mechanism, add the narrowest appropriate reference there as well. Do not weaken or replace higher-precedence repository instructions.

## 17. Tests required for implementation

When normalized product generation is implemented, tests must cover at least:

- exact stream/depth isolation;
- per-stream source starts;
- overlapping acquisition-task deduplication;
- duplicate identical observations;
- conflicting same-timestamp observations;
- valid-empty versus failed versus unqueried;
- zero versus null;
- fixed UTC-08 day boundaries across DST calendar changes;
- water-year and true DOWY;
- leap-aligned plot coordinate;
- Percent x1;
- resolved VolumetricWaterContent x100;
- unresolved Dimensionless native-only;
- manifest/hash linkage;
- CSV/Parquet semantic equivalence for a small fixture.

Do not require a full-row statewide re-audit merely to validate product serialization.

## 18. Current implementation state

At the time this contract was prepared:

- native Dendra acquisition bundles/Journals/seals exist and remain authoritative;
- CDFW has 23 admitted exact station-depth streams acquired to source-supported POR through a fixed WY2027 catch-up cutoff;
- a single program-wide station/stream/deployment catalog and asset-location map remain required durable control artifacts;
- canonical program-wide Parquet/CSV raw-resolution and daily exports are a required next productization step, not yet claimed complete;
- production Git must not absorb the full native/subdaily archive.

Do not misstate "contract prepared" as "all normalized products generated."
