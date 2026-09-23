# Dendra D1 local daily feed

This is a local review implementation. No Dendra feed is registered for scheduled
publication or consumed by BRIM. The manual template lives in `templates/` and
has no schedule, publishing job, or write permission. The existing publisher,
SCAN feed, and consumer are unchanged.

## Ownership and dependencies

`scripts/build_dendra_daily.R` owns orchestration, fixed run cutoff, daily and
7/14/30-day calculation, durable generation assembly, and activation. Its
`scripts/dendra/core.R` is the sole new daily numerical implementation.
R requires jsonlite and digest; no package installation occurs in the builder.
Python 3.10+ uses only its standard library for normalized transport, hashes,
state restoration, metadata classification and candidate projection. Candidate
validation invokes the bounded R semantic checker with the same numerical policy;
Python retains transport/inventory/CSV and shared-publisher checks.
The unchanged historical Python daily implementation is external replay evidence,
not another runtime calculation. `scripts/dendra/transport.py` preserves the
completed prototype's transport (input SHA is recorded in the review packet);
its 30 original transport tests are retained with import-path-only edits.
The surrounding bridge adds a persistent global request budget, bounded response
size, redirect rejection and repeated-failure circuit breaker.

The project dependency declaration is `data/input/dendra/dependencies.json`.
The repository's hardened setup action currently uses R 4.6.1; local D1 execution
uses installed R 4.5.1. Hosted runtime parity is untested.

## Numerical and metadata contract

Policy `dendra-daily-1.0.0-frozen-cadence` uses fixed UTC-08:00 days, unshifted UTC
`t`, arithmetic mean of finite nonconflicting samples, 75% valid expected count,
and 75% first-to-last valid span. There is no integration or interpolation.
Known converted moisture outside 0–100 excludes the day; diagnostics retain the
source mean. Temperature is in Celsius and permits negative values. Unknown
unit conversions are retained as diagnostics and excluded from accepted values.
This deliberately holds the old prototype's native-unit eligibility behavior
for unresolved scales; the accepted five-stream archive contains no such case.
Only source dictionary-backed conversions are used; Dimensionless is unresolved.
Precipitation and air temperature are discovery-only. Cumulative precipitation
and reported sums with unverified interval support remain distinct.

Initialization estimates fallback cadence once from the explicit initialization
source evidence (or a documented stream cadence). Its version and evidence hash
travel with durable daily state. Daily modes still take precedence. Updates and
reconciliation never infer a new fallback from a changing retained window.
A changed initialization context is a policy migration requiring review; separate
independent bootstraps are not claimed to be interchangeable. The preceding
observed day's cadence is carried into partitions, and carried suffix transition
flags are reconciled after interval replacement.

Each stream/date remains distinct, including duplicate depths. Public level 3
and explicit nonhidden station/stream metadata are required. Protected station
geometry is omitted. Depth comes from explicit depth value/unit metadata;
orientation never supplies a number. Live catalog verification is required,
and the R runner rejects changes to station, depth, parameter, terms or numeric
conversion while carrying older daily values.

Recent-change windows match accepted map lab 0.2: adjacent equally weighted
7/14/30-day means, ceil(0.8*N) eligible days per window, maximum missing run two,
latest accepted daily lag at most three days, ±0.5 percentage-point deadband with
inclusive boundaries. Frozen replay is always labeled nonlive. Live index expiry
is the earliest cutoff/build/source retrieval timestamp plus three days; the
consumer must reevaluate expiry at read time and also honor each stream's
observation lag. The schema field is a D1 contract, not an installed BRIM timer.

## Commands and durable paths

All commands run in an independent checkout. Set absolute paths beneath a
sandbox; no default cwd output or Actions dependency-cache state is used.

```sh
# Export the supplied validated native checkpoint; no HTTP requests.
python3 scripts/dendra/bridge.py export-checkpoint \
  --catalog data/input/dendra/pilot_catalog.json \
  --checkpoint "$CHECKPOINT/state" --output "$SANDBOX/native"
Rscript --vanilla scripts/build_dendra_daily.R --mode replay \
  --catalog data/input/dendra/pilot_catalog.json \
  --state "$SANDBOX/replay-state" --output "$SANDBOX/replay-artifacts" \
  --native-manifest "$SANDBOX/native/native_manifest.json" \
  --as-of 2026-09-20T07:30:00Z

# Plan only. Backfill is explicitly bounded, never an implicit statewide import.
Rscript --vanilla scripts/build_dendra_daily.R --mode plan \
  --catalog "$LIVE_CATALOG" --state "$SANDBOX/state" \
  --output "$SANDBOX/plan.json" --days 7
Rscript --vanilla scripts/build_dendra_daily.R --mode update \
  --catalog "$LIVE_CATALOG" --state "$SANDBOX/state" \
  --output "$SANDBOX/artifacts" --days 7 --ledger "$SANDBOX/http.json"
# Explicit source correction of a bounded interval. First update state to the
# current cutoff. Reuse request-generation to resume, choose a new one to refetch.
Rscript --vanilla scripts/build_dendra_daily.R --mode reconcile \
  --catalog "$LIVE_CATALOG" --state "$SANDBOX/state" \
  --output "$SANDBOX/artifacts" --start 2026-09-01 --end 2026-09-08 \
  --request-generation reviewed-correction-1 --ledger "$SANDBOX/http.json"
```

`--mode backfill` permits explicit recent initialization (moisture <=30 days,
temperature <=90 days for one stream). Update/reconcile without prior durable
state fail before retrieval. Actual run-start time is captured once. Live `--as-of` must be at or before
that time and no more than 24 hours old. Its completed fixed-PST date cannot
advance past the actual completed day. Metadata verification must also precede
run start and be no more than 24 hours old. Archived cutoffs require replay. Ordinary retries use a stable completed-day/interval key. A completed
interval is reused, including successful empty intervals. New explicit
reconciliation generations force a full interval refetch. Never delete the HTTP
ledger to evade the whole-task 80-attempt limit. Requests are serial (maximum
concurrency one), 25-second timeout, at most two tries, 8 MiB response limit;
Retry-After over 15 seconds aborts this attempt, and two consecutive service
failures open the persistent circuit.

`state/current.json` is the **single authoritative activation pointer**. It names
an immutable generation, full hash inventory, and its validated candidate root.
The adapter stages artifacts but never activates a competing output pointer.
Any transport/validation/activation failure leaves the prior pointer unchanged;
unactivated files are evidence, not published products. The state directory lock
prevents concurrent writers. A crash can leave the lock; inspect the run before
manually removing a confirmed abandoned lock. There is no automatic lock stealing.

State generations retain all daily diagnostics and the cadence context. Completed
native refresh intervals live under `state/intervals`; older supplied raw history
is referenced by input checksum rather than recopied into production Git. Restore
`state/` with the matching catalog and all generation files. Both archive safety
and the exact index/daily checksum inventory are verified. A verified full fetch
replaces every date in its interval; failures never overwrite completed intervals.
Safeguard version `dendra-safeguards-1.1.0` is separate from the unchanged daily
policy/schema. One centralized R loss decision serves the helper and runner.
Only previously retained dates inside the refetch interval enter deletion counts;
newly appended missing dates do not. Loss is computed per date before summing,
so gains elsewhere cannot cancel a deletion.

Per stream, hold when more than half of prior eligible days are removed with at
least two eligible days lost, or when more than half of prior observations are
removed with at least eight samples lost, two previously populated dates and two
affected dates. Across the whole selection, hold when at least two streams are
affected and more than half of retained eligible stream-days are lost (minimum
two), or more than half of retained observations are lost (minimum sixteen and
two affected stream-days). Denominators include the full selection's retained
support in the queried interval. The per-stream gate also applies independently.
These are provisional deletion-review screens, not changes to daily eligibility.
They work at 1/7/14/30 days. A one-day empty correction to one sensor is permitted;
simultaneous complete one-day loss at both populated sensors is held for review.

`state/runs/<run>/loss_assessment.json` records affected stream/date intervals,
prior/replacement counts, fractions, reasons, exact replacement/native hashes and
prior index hash. A hold preserves the prior pointer, daily generation and usable
candidate bytes. Repeated retries reevaluate the same evidence without bypassing
the gate. Complete-empty remains a successful source result.

A legitimate large correction requires review of this evidence and explicit
`--mode reconcile --request-generation <key> --loss-review <review.json>`.
The operator-provided review JSON must contain `decision: approve_exact_removal`,
nonempty `review_id`, `reviewed_by`, `reason`, and the exact assessment's `version`,
`assessment_sha256`, `prior_index_sha256`, and `request_generation`. The assessment
hash binds interval, selected streams, prior support, replacement daily/native
hashes and counts. Different data, parent or request key needs new review. Only
reconciliation accepts this record; there is no force flag. This is a local
operator approval record, not a cryptographic identity or hosted approval system.
The builder never creates approval records. D1R1 exercised synthetic approvals
only; no real source correction or publication was authorized.

## Candidate and publication boundary

The candidate uses fixed path `docs/data/dendra/index.json` plus tightly scoped
owned roots `docs/data/dendra/history`, `diagnostics`, and `companion`. Histories
are keyed by exact stream ID/WY; the index lists every target hash and size.
Diagnostics contain the complete rows and original unit metadata; CSV separates
accepted and diagnostic values. The index is small relative to native history
and selected water-year files load independently.

```sh
python3 scripts/dendra_candidate.py validate --root "$CANDIDATE"
python3 scripts/dendra_candidate.py prepare --root "$CANDIDATE" \
  --source-sha "$RECORDED_CODE_SHA"
```

Both validation and preparation invoke `scripts/dendra/semantic.R`. It uses
`core.R` for eligibility, daily summaries, 7/14/30-day windows and the live time
bound. It checks explicit field presence/types, finite numbers, sample partitions,
count-derived flags, expected count/cadence/coverage, mean-native conversion,
eligibility, complete latest/lag/window summaries, identity and versions, source
interval/time relationships, and exact expiry. Numeric relationships use absolute
tolerance 1e-9; timestamps use one microsecond for derived expiry. Native samples
are not recomputed at preparation: detection of individual outliers, timestamp
irregularity and native mean truth remains the source-processing/replay test's
responsibility. Retained statistics and flags must nevertheless be internally
consistent. Checksums authenticate bytes, not source truth.

Supported unit identifiers must agree with source terms and a supported conversion
rule and dictionary evidence. A made-up status or label cannot authorize an
unknown scale. New history partitions add policy/safeguard version, depth,
orientation and conversion identity. Existing D1 partition fields remain supported
without requiring fields that were absent in D1; present fields are still checked.
Likewise, unchanged legacy D1 snapshots lack captured run start: validation bounds
their as-of by recorded generation time, while new builders record actual start.
All candidates reject build/retrieval times in the future or retrieval after build;
replay is always frozen and expiry is null. Validation never refreshes source time.

Preparation calls actual `main_publisher.prepare_metadata` and
`validate_candidate_metadata` with integrity schema 2, exact fixed paths, and
static owned roots. No shared-publisher changes are needed. There is no live
publish CLI in the adapter. Its pure reconciliation decision requires unchanged
selection/policy and exact parent lineage; stale candidates are no-ops and a
competing same-cutoff parent requires rebuild. This is exercised with mocks;
the shared Git transaction/push is deliberately not run, even against a bare
fixture repository, because D1 authorizes no commits.

Before D2 enables publishing: implement the product callback for the existing
publisher phases and staged validation, bind callback/build SHA, document the
static ownership in repository publication tests, approve a durable state store
and retention/rollback policy, add hosted consumer loading/freshness handling,
and review the real manual workflow. Current artifacts are transfers only and
do not establish durable remote storage. No core allowlist weakening is proposed.

## Verification

```sh
Rscript --vanilla tests/dendra/test_core.R
python3 -m unittest discover -s tests/dendra -p 'test_*.py' -v
Rscript --vanilla tests/dendra/test_template.R
Rscript --vanilla tests/dendra/parity.R "$NATIVE_MANIFEST" "$GENERATION" \
  "$REFERENCE_DAILY" "$PARITY_JSON"
node tests/dendra/window_parity.cjs "$CANDIDATE" "$ACCEPTED_CORE_JS" "$RESULT_JSON"
```

The final two commands require the immutable input packet, not another historical
download. R YAML validation requires the test-only yaml package. In-memory
browser checks of the accepted UI are separate from file/HTTP/hosted origins and
separate from real Actions execution. D1 results and review findings belong in
the external evidence ZIP, not this public source tree.
