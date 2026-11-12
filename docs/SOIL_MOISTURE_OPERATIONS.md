# SNOTEL SMS current-WY operations — inactive source candidate

This page describes implemented source and offline validation, not deployed or
standing acquisition authority. The intended live ownership is exactly
`docs/data/snotel-sms/index.json` and `docs/data/snotel-sms/current-wy.json`.
The accepted catalog is committed separately. Observation acquisition, conditional
first publication, hosted delivery and consumer acceptance remain distinct
transitions; no standing acquisition or scheduling authority is implied.

Native NRCS `DAILY/END` preserves each exact station triplet, signed SMS depth,
ordinal and native `pct` value. Provider date D has source boundary D+1 08:00Z
under fixed GMT−08, independent of Pacific daylight-saving time. Numeric zero is
a real observation; withheld, explicit missing, omitted dates, complete-valid-empty,
failed and unqueried states remain distinct. No averaging, forward fill or depth
substitution is introduced. Last-admitted values retain their original evidence
when a later nonnumeric outcome occurs; they must not appear newly observed.
See the [native consumer contract](BRIM_CONSUMER_CONTRACT.md#snotel-sms-current-wy-snapshot-inactive),
[product description](PRODUCTS.md#snotel-sms-current-wy-snapshot-inactive-offline-implementation)
and [publication procedures](PUBLISHING_AND_OPERATIONS.md).

The manual operating workflow is **Build SNOTEL soil moisture current-WY feed**.
The separate **Build SNOTEL SMS synthetic replay (QA)** workflow retains its
older saved-fixture replay behavior. A default operating dispatch only reports
`DISABLED`, skips checkout and Python, and produces no candidate. It therefore
does not validate the new snapshot, prior-product importer, transport or publisher.
The older QA replay also does not establish Phase A/B hosted readiness. A meaningful
hosted saved-input test of those paths requires a separate source/dispatch decision;
no such route is added here.

## Finite first-product commissioning

The public `data/input/snotel_sms_catalog.json` is the source-authenticated
32-station/98-sensor catalog released by PR #60. Its presence grants no provider
traffic. The manual operating workflow now uses Ubuntu 24.04 and first runs the
focused synthetic integration tests unprivileged in a network namespace with no
external route, followed by the non-stub Git reader against the actual event
checkout, catalog, roster and complete product absence. Its source/run-bound
proof must be no older than five minutes before preparation. The exact canonical
Actions HTTPS origin without `.git` and the two existing `.git` origins are
accepted; near matches remain refused. It installs no packages. This is a prerequisite in the same job,
not a second real campaign or standing schedule.

`scripts/snotel_first_run.py` requires `SNOTEL_SMS_FIRST_RUN_JSON` with the exact
reviewed source SHA, workflow run number, first attempt, catalog/roster pins,
unchanged budgets and finite matching `window_end`. Only the separately approved
R9 identity and run number 2/attempt 1 are admitted, with an activation-to-expiry
interval of at most four hours and absolute expiry no later than November 14,
2026 07:59:59 UTC. R8 remains closed and spent with UNKNOWN provider accounting.
The workflow compares the canonical grant internally, passing only the result
and public pins/clocks to steps so the full private grant is not echoed in logs.
GitHub string equality is case-insensitive; the Python admission independently
enforces the fixed approval, exact event source, pins and finite limits.
Acquisition, publication and bootstrap must all be explicit; the source
and fresh-main product checks still require complete absence for bootstrap.
Wrong event/ref/workflow/run/source, missing grant, rerun, or insufficient full
setup/acquisition/publication/readback headroom refuses before an opener.

One owned preparation child has separate preflight, acquisition and finalization
deadlines. Private phase markers never grant a restart. A killed or incomplete
native reservation remains UNKNOWN; no next station or redispatch follows.
The native 60-second attempt control remains unchanged, and final construction
has a 120-second alarm in addition to parent supervision. Runtime source bytes,
TLS verification, exact request identities, full-prefix science and the unchanged
publisher callbacks remain mandatory.

Only the validated two-file product and minimal shared candidate metadata can be
uploaded. Raw responses, run/grant files and private failure streams stay in
runner-local storage and are not promised as permanent evidence after runner
loss. The public log uses an explicit accounting-field allowlist. First-product
release and Pages verification remain execution outcomes, not claims made by
this source change. Application acceptance and any later acquisition require
separate authority; there is no SNOTEL schedule.

### Current-WY SNOTEL replacement (offline Phase A)

The inactive `snotel_snapshot` path is a versioned compact successor contract;
it does not relax the separate immutable raw-backed offline replay contract. It
reuses native history validation and the extracted admission/reconciliation
functions in `soil_moisture_operations.py`. Legacy replay APIs, WY2027 bounds,
10,000-event/32-MiB bundle/64-MiB expansion limits and other networks are unchanged.
Phase A changes no transport or automatic annual maintenance. The separate Phase B
orchestration candidate below uses these same frozen science interfaces.

`plan_current_wy` is pure. Pin one UTC `as_of`; the source calendar is fixed UTC−08,
and its latest completed date is `date(as_of - 8 hours) - 1 day`. Intersect with
October 1 through September 30 of that source-clock WY. On October 1 there can
be no eligible date and therefore no GET. `check_run_year` rejects a later clock
in a different WY. Publication admission also rejects a build crossing that
boundary or a candidate that does not cover the exact current-WY prefix.
No source lag is invented, and a completed boundary does not prove availability.

`build_saved_snapshot` is a pure saved-input constructor, including independent
full-season capacity tests. It is not an acquisition option for old years.
One native station history is expanded at a time. Trusted exact roster/capture
pins, successful complete body accounting and unchanged metadata are mandatory.
Every required station must validate before any candidate can be written. Unknown
framing, truncation, failure, omitted required capture or contradictory source
identity holds the whole replacement. Native successful empty/missing/withheld
results may update explicit status while retaining historical numeric state.

The 32-MiB ceiling covers the complete two-file product, metadata and all referenced
last-admitted/latest-query provenance. Only unreachable evidence is removed;
referenced history, flags and values are never truncated to fit. Final size is
measured, not inferred from response bytes. Saved-input tests use synthetic data
under OS network denial; their runtime/memory does not establish hosted/provider
performance. No extra provider attempt or response splitting is authorized by a
capacity failure. Later acquisition still needs explicit per-request/aggregate
budgets, fresh resource checks and run-local durable pre-opener reservations.

Recovery means preserving trusted last-good committed bytes and safely rebuilding
a next candidate in a fresh process from authenticated product files and valid
saved source inputs. It does not promise continuation after a vanished runner,
permanent raw-response replay or a new private hosted archive. Missing/ambiguous
attempt state never grants another GET. Existing private accepted raw evidence and
journals retain their original preservation rules. A distinct later acquisition
requires its own authority; temporary Actions artifacts are not a durable backup.

Pure SNOTEL callbacks require exact two-file ownership and shared-publisher metadata
binding to product generation. Same bytes are a no-op; an immediate authenticated
product ancestor is stale; an exact-parent successor must preserve scientific
state and query chronology. Conflicting or unproven ancestry holds. Unrelated
fresh-main feed changes do not alter product parentage. Staged bytes must equal
the validated candidate. Ambiguous acknowledgment requires independently read
committed bytes/hash/generation, never a provider refetch. Worktree file copying
is not itself atomic publication: the unchanged main publisher owns the eventual
Git transaction. The future two-job preparation/publication split and unchanged
`brim-live-main-publish` queue are implemented by the separate manual-first Phase B
candidate below; neither hosted execution nor publication is implied.

Annual maintenance is separate and Dave-triggered. The later task should:

1. Locate the last-good product for the completed ending-year WY in existing Git
   history; verify its manifest, product hash, generation and committed checkout.
2. Record actual source/query/admission coverage and unfinished tail, including
   withheld, missing or unavailable dates; do not infer a complete season.
3. Obtain separate authority for any completed-year finalization and historical
   preservation, keeping exact native identity and science/version bindings.
4. Review reference incorporation under the nine-distinct-completed-WY
   per-calendar-day policy. This producer calculates no ribbons or references.

There is no automatic archive/tag/branch, previous-year fetch mode, annual worker
or second live trace. Git identifies committed products without a self-embedded
future commit SHA; perpetual artifact availability is not promised. Source
integration, consumer and disclosure review, real acquisition/hosted execution,
publication and schedule activation remain separate approval gates.

### Phase B operating candidate and provisional export

The local `snotel-operating-run-1` adapter in `scripts/soil_network_workflow.py`
adds `snotel-prepare`, `snotel-rebuild`, `snotel-publish` and `snotel-acknowledge`.
The legacy SCAN/Dendra routes and the older gated SNOTEL mode are unchanged.
The proposed manual workflow defaults both acquisition and publication off.
Publication without acquisition is rejected; acquisition-only is supported.
The workflow is source preparation, not standing permission to dispatch.

The input contract is a committed `data/input/snotel_sms_catalog.json`, not an
included real inventory. Version `snotel-sms-catalog-1` has exactly `version`,
`disclosure`, `roster_sha256`, and `groups`. Each group contains original pinned
`metadata_base64`, `metadata_sha256` and sorted exact `sensor_identities`.
The canonical roster binding must contain all 32 stations and 98 SMS sensors,
without omitting a selected sensor; no discovery or metadata refresh exists.
Future real operation requires `RELEASE_APPROVED_PUBLIC`, reviewed catalog and
roster SHA-256 repository variables, and explicit public release approval.
Fictional `ZZ` stations and `SYNTHETIC_ONLY` catalogs are restricted to injected
offline test openers and cannot qualify for real acquisition or publication.

`SnotelGitReader` verifies explicit full commit objects, regular blob inventory,
bounded blob sizes and Git object identities. Effective Python source bytes must
equal the selected source revision. The prior product is the exact committed
two-file pair, never mutable checkout files or fabricated private ancestry.
Bootstrap is explicit absence only. A partial/extra/corrupt product, incompatible
WY, same-WY roster change or shrinking interval holds before acquisition.

Future local invocation supplies `--repo`, a fresh outside-repository `--root`,
`--source-sha`, `--prior-commit`, `--catalog-sha256`, `--roster-sha256`, explicit
`--acquire --release-approved`, optional approved `--bootstrap`/`--publish`,
`--window-end` (UTC Z), and exact `--budget-json`:

```json
{"attempts":32,"station_bytes":1048576,"station_seconds":60,"total_bytes":33554432,"total_seconds":1920,"finalization_seconds":120}
```

These implementation ceilings do not grant provider authority. One pinned
`as_of` determines only the full current-WY completed-day prefix. The initial
window needs at least 2,040 seconds; before each station, remaining stations'
60-second ceilings plus 120 seconds finalization must still fit. The native
pre-opener clock also checks the pinned year/window. Separate station lanes
retain reservations, prefixes and ledgers; any failed/unknown body, Retry-After,
source contradiction or cap failure stops before the next station. Consumed
body-read-interface bytes are not wire traffic. Native acquisition excludes
close/persistence; the runner conservatively charges whole station-call elapsed
time against its aggregate budget without claiming whole-process termination.
Reserved lane counts are not falsely reported as proven physical calls after an
interruption. All required stations must succeed to create a replacement.

The private run retains exclusive `run.json`, native station lanes, completion
bindings, `captures.json` and result/failure evidence. A fresh process may use
`snotel-rebuild` with independently retained `--run-sha256` and
`--capture-manifest-sha256` pins and a fresh destination. It reads
only complete authenticated saved lanes; it never calls acquisition or creates
missing lanes. Same build clock reproduces identical product bytes; a later
same-WY rebuild preserves source science/receipts but changes build provenance
and generation. Publishing both competing generations is not permitted by the
exact-parent callbacks. A lost/ambiguous run never grants replacement attempts.
Hosted acquisition reruns (`GITHUB_RUN_ATTEMPT` greater than one) are rejected in
both workflow and adapter before reservations; an independent later dispatch
requires separate acquisition authority, not a rerun to replace missing state.
Temporary hosted runner files are not promised as permanent raw evidence.

Only `transfer/candidate`'s two owned product files and
`transfer/candidate-metadata.json` are uploaded, and only for a validated real
candidate. Raw responses, catalog and private receipts are excluded. Publication
authenticates the recorded source, repeats year admission at each callback, and
uses unchanged `main_publisher.py`. Delivery acknowledgment reads actual bounded
blobs at an explicitly selected committed main revision. A verified immediate
descendant yields `STALE_NO_OP_NEWER_PRODUCT_PRESERVED`, not an exact delivery
acknowledgment; other mismatches hold without refetching.

For later manual annual maintenance, select the exact historical commit and
independently recorded generation/index/product hashes first. Invoke
`python3 -B scripts/soil_moisture/snotel_year_end.py` with `--repo`, `--commit`,
`--generation`, `--index-sha256`, `--product-sha256`, `--expected-year`,
`--approved-parent`, `--destination` and explicit `--protected-root` entries.
The CLI captures actual UTC once. The private existing owned parent and complete
protected-root binding require separate real-destination authority; they are not
discovered or invented by the helper. Offline validation uses temporary synthetic
destinations only. Both-direction overlaps with source/live/protected evidence,
symlinks, partial/extra/corrupt bundles and conflicting destinations hold.
Original bytes plus a deterministic **PROVISIONAL — NOT FINALIZED** manifest are
staged, verified and exposed using an atomic no-replace directory operation;
identical repeats are no-ops. Unsupported platforms hold. Failed staging is
retained for diagnosis and cannot replace last-good. No provider, Git transaction,
automatic archive/rollover, ribbons or new storage service is involved.

Remaining release decisions are catalog/native-field disclosure, actual consumer
decoding/status/rollover acceptance, isolated source integration (including
Phase A dependencies and documentation/Pages effects), one approved hosted
saved-input validation, bounded real acquisition/resource clearance, first
publication and scheduling. The synthetic QA behavior and SCAN/SWE workflows are not changed by this source
candidate; their configured maintenance states require separate verification. Full-season serializer evidence remains Phase A evidence;
small integrated runner tests alone do not establish hosted full-season memory
or provider timing.

The broader provisional year-end preservation direction also covers Dendra soil
moisture, SCAN soil moisture and SNOTEL SWE through their separately bounded work.
This helper handles SNOTEL SMS only. It adds no exporter, destination, automatic
rollover, acquisition authority or reference maintenance for another provider.

### SCAN/SNOTEL synthetic hosted qualification

The selected-source integration and nonpublishing Linux test route are documented
in [Soil SMS hosted qualification](SOIL_SMS_HOSTED_QUALIFICATION.md). This does not
activate acquisition/publication, release real SNOTEL catalog data, or resolve
the held history joins. Production and consumer readiness remain separate.
