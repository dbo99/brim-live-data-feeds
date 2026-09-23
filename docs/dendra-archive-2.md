# Dendra archive2 — local SM2A contract

Status: review slice, saved observations only. No deployment, new provider observations,
Actions run, schedule, or scientific-policy change is established here.

`build_dendra_daily.R` dispatches a catalog declaring `dendra-integration-2` to
`dendra/archive.R`. `core.R` remains the only numerical implementation. Integration1
and its strict validators/reader remain available for immutable accepted examples.

## Windows and partitions

The schema `dendra-daily-2.0.0` separates all acquired moisture daily rows from the
default current water year plus nine prior display years. The reference descriptor
is `not_computed`. Display selection (`default` or `all`) cannot authorize deletion.
Temperature remains recent context; `integration.temperature_days` explicitly selects
1–3660 completed days, default 90. No pre-record temperature dates are manufactured.

Each stream has an immutable manifest and per-WY history, full diagnostic/query
provenance, and exact CSV partitions. Closed partitions omit volatile generation
clocks. All source query intervals, retrieval times, content hashes and frozen cadence
are retained. Selected map summaries include latest accepted value even when stale;
`acquired_through_date` is per stream. A newer build cutoff never fills an unqueried
date. A successful queried empty day has explicit evidence and diagnostic rows.

Limits are versioned in `dendra_archive.LIMITS` and mirrored by the actual reader:
256000-byte root, 262144-byte catalog/stream/inventory shard, 1000000-byte JSON WY,
512000-byte CSV WY, 20000 files, 500 files/page, 40 pages, 128 catalog shards,
1024 streams, 1500 stations, 256 WY/stream, 93696 selected daily rows, 32MB explicit
CSV. Exceeding a bound holds the candidate; it never deletes older acquired rows.
Reader history/text cache: 24 entries and 12 MB serialized UTF-8, finite eviction.
Streaming limits apply before complete body allocation. Memory figures are measured
separately from these serialized-byte budgets. Common Dendra catalogs above 262144
bytes use `brim-soil-moisture-2` with up to 128 immutable shards of 262144 bytes.
All shards must validate before source activation. SCAN's existing codec and 6 MB/12 MB
compact/expanded bounds remain unchanged.

## Lineage, recovery and corrections

`--mode bootstrap --seed-manifest ...` imports checksum-bound saved daily products
into a fresh state and writes `seed.json` plus a prepared `current.json`. It never
writes `published.json`. A seed is replay-only, has null parents, and binds its own
generation and seed checksum. Descendants bind the seed and state parent. Updates
choose acknowledged `published.json`, otherwise the explicit seed; they never choose
an unacknowledged prepared child. Publication uses the unchanged shared publisher's
scoped callbacks and existing four owned roots. Public index, history and durable
state have one acknowledged generation, recovered from committed public paths.

Portable state version2 contains partitions, checksum inventory and a role receipt,
without native subdaily records or original absolute runtime paths. `export --role
seed|prepared|published` is explicit. Only committed live products can hydrate an
acknowledged role. Restores validate bounds, scope, semantics and hashes before
copying; destination must be new. The old raw-state tar helper is not the archive2
portable-state contract. Portable JSON manifest cap is 8 MB / 20000 files.

Selected IDs cannot disappear. Additions require `dendra-selection-add-1` binding
SHA256 of newline-joined sorted prior IDs, exact added IDs, review_id and reason.
One stream failure holds the whole candidate. Suspicious correction losses still use
unchanged core safeguards and exact reviewed reconciliation evidence. A legitimate
single-stream, single-day removal remains allowed by the accepted policy; selection-
wide one-day losses and large 1/7/14/30-day losses are tested accordingly.
`replay-update` and `replay-reconcile` consume explicitly supplied saved native
manifests and remain frozen. Live modes use the actual transport boundary and require
explicit budgets and fresh complete public metadata. SM2A only injects synthetic
transport/clock fixtures; its actual provider budget is zero.

## Presentation and operational gate

The accepted search/filter/legend/pane interface stays in place. Catalog membership
is independent of loaded histories. Unknown scale and unavailable values remain
unknown. Current native protection/withheld geometry overrides older catalog positions.
Companion controls use exact depth/orientation and sensor identity; multiple candidates
require explicit choice. The real preview contains only the verified 20 cm temperature.
No mean-based NRCS capabilities or reference ribbons are added.

`templates/build-dendra-daily-archive.template.yml` is inactive and outside workflows.
It requires a separately approved selection/metadata plan and deployment variable;
no automatic live bootstrap is provided. Its R/Python/YAML dependency and shell checks
are static checks only. Broad live metadata acquisition, actual Actions, publication,
BLM geometry and statewide acquisition remain separate operational/review gates.

## SM2B bounded full scientific validation (local review)

Every archive validation still checks every retained daily sufficient-statistic row
through R `core.R` policy. `semantic.R` uses vectorized calendar/timestamp parsing;
the Python driver runs at most four workers, sixteen streams per batch, a120second
batch timeout and900second invocation deadline. In-flight workers can take their
remaining batch bound to drain after another worker fails. Coverage totals must
match all archive rows/streams. There is no persistent semantic cache or historical
skip. An interrupted or failed validation cannot activate or acknowledge state.

The R archive producer now processes one daily product at a time on disk and uses
column-based JSON serialization of the same scalars, explicit nulls and flag
arrays. Mixed scalar types are rejected before serialization. Scientific arithmetic,
source-loss review, parent lineage and the public format are unchanged. Publisher
callbacks retain complete candidate/current/staged checks; only a repeated check
of the same candidate inside one reconcile call is removed, with activation-time
freshness reevaluated. The validation-only temporary view uses hard links with
copy fallback; it is never written through.

The434-stream SYNTHETIC full-pipeline evidence is distinct from actual California
observation coverage. The20,000file/40inventory-page limits remain. At fixed434
moisture streams with three partitions per acquired WY, the metadata growth model
crosses those limits around15acquired WYs. Exceeding a limit holds the candidate;
it never truncates acquired history. Any broader storage/limit migration needs a
joint producer, state, publisher, preflight and reader review. Local performance
and disposable Git tests do not certify Actions or hosted operation.

## SM2C operation-local validation and recovery

The public archive schema, acquired history, R daily policy and shared publisher
remain unchanged. Cold validation reads every retained diagnostic partition through
the actual R semantic verifier. R checks descriptor hashes before assembling the
same daily product in memory. Structural projections/CSV/calendar and whole-file
closure are still checked in Python, with before/after content binding. Parent
materialization retains the already checked daily view instead of rebuilding it.

`dendra_validation.ValidationOperation` is a private process-local object, bounded
by four distinct checked byte sets and 1800 seconds on monotonic and wall clocks.
No serialized report, envelope or receipt can populate it. Reuse requires actual
complete-file hashes plus source hashes for the numerical policy, validator,
producer, state/publisher code and unit dictionary. A changed/unknown candidate
runs full R validation; code change, clock reversal/expiry and mutations during
validation hold. Freshness is reevaluated. Separate commands and all three shared
publisher callback phases start cold; there is no persistent semantic trust cache.

Combined archive build/state preparation validates once and transfers ownership
of the newly produced candidate into the explicit state path. Existing build-only
calls keep their output contract. Durable copy/export boundaries use independent
files and require equality to the exact selected checked bytes before installation;
no writable hardlinks alias acknowledged state. A completed run's temporary prior
view is removed only after R has consumed it; acquired partitions remain intact.

Committed restore resolves one commit, enumerates its plain blobs, reads exact blob
IDs through one bounded Git process, validates every row, and exposes new state only
when the captured HEAD still matches. No repeated moving `HEAD:path` reads or
unacknowledged prepared parent is accepted. Existing portable seed/published snapshots
retain their versioned format and cold validation.

The inactive archive template now exports `dendra-prepared-transfer-1`: one
`publication/{candidate,candidate-metadata.json}` subtree plus `prepared-state.json`.
The envelope has an exact fixed candidate path, source-event SHA, generation,
complete-content hash, root-index hash, metadata hash and role `prepared`. It avoids
a second identical archive copy. `restore-transfer --source-sha EXPECTED` validates
the complete artifact and runs cold R before restoring **prepared only**. It creates
neither seed nor published acknowledgement; that restored candidate cannot become
the next update's parent by itself. Candidate metadata closure remains the unchanged
shared publisher's exact publication subtree. Public index/history/reader formats
and the shared search/filter interface do not change.

The operating design is recorded in [SOIL_MOISTURE_OPERATIONS.md](SOIL_MOISTURE_OPERATIONS.md).
SM2C does not activate it or implement coverage-driven catch-up, partial stream
success, BLM joins, NRCS statistic migration, new storage, schedules or a remote trial.
