# SNOTEL SMS current-WY operations

The official product is the committed pair
`docs/data/snotel-sms/index.json` and `docs/data/snotel-sms/current-wy.json`.
The separately released catalog identifies the approved stations and sensors.
The R11 retained release established the first generation. PR71 added native
successor admission, and manual run 3 delivered a successor through November 24,
2026. Use the deployed finite policy, scoped clearance and independently
acknowledged predecessor for each authorized acquisition.

Native NRCS `DAILY/END` preserves each exact station triplet, signed SMS depth,
ordinal and native `pct` value. Provider date D has source boundary D+1 08:00Z
under fixed GMT−08, independent of Pacific daylight-saving time. Numeric zero is
a real observation; withheld, explicit missing, omitted dates, complete-valid-empty,
failed and unqueried states remain distinct. No averaging, forward fill or depth
substitution is introduced. Last-admitted values retain their original evidence
when a later nonnumeric outcome occurs; they must not appear newly observed.
See the [native consumer contract](BRIM_CONSUMER_CONTRACT.md#snotel-sms-current-wy-successor-pair),
[product description](PRODUCTS.md#snotel-sms-current-wy-successor-product)
and [publication procedures](PUBLISHING_AND_OPERATIONS.md).

The operating workflow is **Build SNOTEL soil moisture current-WY feed**.
The separate **Build SNOTEL SMS synthetic replay (QA)** workflow retains its
older saved-fixture replay behavior. A default operating dispatch only reports
`DISABLED`, skips checkout and Python, and produces no candidate. It therefore
does not validate the new snapshot, prior-product importer, transport or publisher.
Use the [hosted qualification record](SOIL_SMS_HOSTED_QUALIFICATION.md) for the
scope of saved-input evidence. Use authenticated operating receipts for real
acquisition, delivery and spending.

## Finite successor operation

The successor uses the committed two-file product as its predecessor. Its manual
dispatch requires acquisition and publication, bootstrap false and a matching
UTC deadline. Both jobs verify the main event, reviewed source, catalog, roster,
generation, hosted run history and resource clearance. Each run admits at most
one serial campaign under the original 32-station limits. The existing builder
queries the complete eligible water-year prefix and the shared publisher
reconciles its result against fresh main. A failed, skipped or uncertain spend
requires reconciliation before another policy can be approved.

The owner supplies one compact JSON policy through the masked Actions secret
`SNOTEL_SMS_SUCCESSOR_POLICY_JSON`. It contains operating limits and public
source identities. Only the admission steps receive it. A second, run-specific
repository variable, `SNOTEL_SMS_NRCS_CLEARANCE`, binds the event, exact policy
digest, run number, cutoff, observed prior generation, issuance and expiry (slash-separated).
The clearance must be at most 30 minutes old at acquisition admission and cover
the complete three-hour slot.
Install it only after checking shared NRCS activity, incident/backoff and
resource release for the full window. The SCAN provider hold is also checked.
Removing clearance fences future slots; cancel an already admitted job if an
immediate stop is required, then reconcile its spending.

A separate daily policy can cover one to seven consecutive 18:11 UTC slots,
each three hours long. Each slot requires current clearance, a continuous
first-attempt run history, the approved runtime and acknowledgment of the
preceding publication. A missed slot or missing proof closes the sequence.
A later authorized full-prefix query can cover the intervening days.
The cron creates run numbers even when its acquisition job is disabled.
Verify the actual next number before installing a policy. Independent
main and Pages readback completes the operating review.

## Historical finite first-product commissioning

R9 addressed a missing initial product. Its run identity and grant are spent.
Retain the following source, accounting and recovery facts for replay. An active
successor must start from the acknowledged pair under the procedure above.

The public `data/input/snotel_sms_catalog.json` is the source-authenticated
32-station/98-sensor catalog released by PR #60. Its presence grants no provider
traffic. The R9 manual route used Ubuntu 24.04 and first ran the
focused synthetic integration tests unprivileged in a network namespace with no
external route, followed by the non-stub Git reader against the actual event
checkout, catalog, roster and complete product absence. The proof had to bind
the source and run within five minutes of preparation. The route accepted the
canonical Actions HTTPS origin without `.git` and the two approved `.git`
origins. It refused near matches and installed no packages.

For that first-product route, `scripts/snotel_first_run.py` required
`SNOTEL_SMS_FIRST_RUN_JSON` with the exact
reviewed source SHA, workflow run number, first attempt, catalog/roster pins,
unchanged budgets and finite matching `window_end`. Only R9 run 2, attempt 1
qualified under an activation-to-expiry interval of at most four hours and an
absolute expiry of November 14, 2026 07:59:59 UTC. R8 remains closed and spent
with UNKNOWN provider accounting.
The workflow compares the canonical grant internally, passing only the result
and public pins/clocks to steps so the full private grant is not echoed in logs.
GitHub string equality is case-insensitive; the Python admission independently
enforces the fixed approval, exact event source, pins and finite limits.
R9 required explicit acquisition, publication and bootstrap. Its source and
fresh-main checks required complete product absence. A wrong event, ref,
workflow, run or source, a missing grant, a rerun or insufficient headroom
for setup through readback refused before provider access.

One owned preparation child has separate preflight, acquisition and finalization
deadlines. Private phase markers never grant a restart. A killed or incomplete
native reservation remains UNKNOWN; no next station or redispatch follows.
The native 60-second attempt control remains unchanged, and final construction
has a 120-second alarm in addition to parent supervision. Runtime source bytes,
TLS verification, exact request identities, full-prefix science and the unchanged
publisher callbacks remain mandatory.

SNOTEL preparation and saved-only rebuild retain the actual subsecond UTC build
clock, matching the precision supported by native retrieval receipts. A build
clock is never rounded up, substituted for a receipt, or allowed to precede a
capture. This corrects a reproducible whole-second truncation failure; it does
not establish the inaccessible original R9 failure. Held results include a
fixed phase label and exception type; detailed exception text stays private.
R8 and R9 grants remain spent, and this correction grants no new hosted run.

Only the validated two-file product and minimal shared candidate metadata can be
uploaded. Raw responses, run/grant files and private failure streams stay in
runner-local storage and are not promised as permanent evidence after runner
loss. The public log uses an explicit accounting-field allowlist. R11 subsequently
delivered the first pair. The successor procedure governs later acquisitions.
Verify reader acceptance independently.

<a id="current-wy-snotel-replacement-offline-phase-a"></a>

### Phase A saved-input qualification

Phase A established the versioned `snotel_snapshot` serializer and its saved
fixtures. Native successor operation now uses that contract. The serializer
reuses native history validation and the extracted admission/reconciliation
functions in `soil_moisture_operations.py`. Legacy replay APIs, WY2027 bounds,
10,000-event/32-MiB bundle/64-MiB expansion limits and other networks are unchanged.
Phase B reused these science interfaces. Keep the Phase A fixtures for replay.

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
Git transaction. The operating workflow now separates preparation from the main-only
`brim-live-main-publish` job. Its source alone cannot prove delivery.

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

<a id="phase-b-operating-candidate-and-provisional-export"></a>

### Phase B design and provisional export

Phase B introduced the `snotel-operating-run-1` adapter in
`scripts/soil_network_workflow.py` with `snotel-prepare`, `snotel-rebuild`,
`snotel-publish` and `snotel-acknowledge`.
The legacy SCAN/Dendra routes and the older gated SNOTEL mode are unchanged.
The Phase B candidate supported acquisition-only preparation. The deployed
finite successor requires acquisition and publication with bootstrap false.
A default manual dispatch is disabled.

The input is the released, committed `data/input/snotel_sms_catalog.json`.
Version `snotel-sms-catalog-1` has exactly `version`,
`disclosure`, `roster_sha256`, and `groups`. Each group contains original pinned
`metadata_base64`, `metadata_sha256` and sorted exact `sensor_identities`.
The canonical roster binding must contain all 32 stations and 98 SMS sensors,
without omitting a selected sensor; no discovery or metadata refresh exists.
Native operation requires `RELEASE_APPROVED_PUBLIC` and the approved catalog
and roster identities.
Fictional `ZZ` stations and `SYNTHETIC_ONLY` catalogs are restricted to injected
offline test openers and cannot qualify for real acquisition or publication.

`SnotelGitReader` verifies explicit full commit objects, regular blob inventory,
bounded blob sizes and Git object identities. Effective Python source bytes must
equal the selected source revision. The prior product is the exact committed
two-file pair, never mutable checkout files or fabricated private ancestry.
Historical bootstrap required complete absence. A current successor requires its
authenticated predecessor. A partial/extra/corrupt product, incompatible
WY, same-WY roster change or shrinking interval holds before acquisition.

The Phase B local invocation specified `--repo`, a fresh outside-repository `--root`,
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

Phase A/B fixtures remain evidence for serializer capacity and recovery. Native
successor publication, consumer decoding and operational timing each require
their own evidence. The provisional exporter described here is SNOTEL-specific.

### SCAN/SNOTEL synthetic hosted qualification

The selected-source integration and nonpublishing Linux test route are documented
in [Soil SMS hosted qualification](SOIL_SMS_HOSTED_QUALIFICATION.md). This does not
activate acquisition/publication, release real SNOTEL catalog data, or resolve
the held history joins. Production and consumer readiness remain separate.
