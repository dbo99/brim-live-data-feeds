# Dendra history acquisition: offline state contract

Status: D1+D2 local implementation, **offline only**. This is internal acquisition
state, not a public feed or a replacement for archive2, pending-query state,
acknowledged publication, or the daily scientific contract. The four technical
sources of truth retain their authority. No existing runtime entrypoint imports
this package. There is no live HTTP adapter, command to acquire POR, scheduler,
publisher, daily builder or browser-history exporter in this slice.

## Code and ownership

The additive package is scripts/dendra/history_acquisition/:

- safety.py: strict JSON, hashing, descriptor-relative no-follow file access.
- model.py: immutable accepted inventory, identity/metadata separation,
  explicit campaign binding and bounded fixed-PST plans.
- journal.py: exclusive campaign registration, one writer, persistent
  reservations, response descriptors, chained receipts and interval seals.
- offline.py: finite byte/status replay using the existing Dendra transport's
  pagination and normalization with an explicitly injected in-memory opener.
- scale_resolution.py: separate offline, evidence-bound derived scale decisions;
  no source acquisition, value conversion or receipt mutation.

scripts/dendra/core.R remains the sole daily numerical authority and is not
executed or modified by this package. Existing routine-update budgets,
production metadata guards, publisher logic, SCAN and SNOTEL remain unchanged.
Nothing under docs/data is an output of this package.

## Exact input identity

Inventory.load(path, expected_sha256) requires an explicit absolute regular
file path, the accepted SHA-256
f81b91bd86ade8e5380063e1ce3b37d1dddb1ca5d21368cd3ecb4514688190d9,
and the exact 3,534,468 input bytes. Equivalent JSON reserialization fails.
Symlinked ancestors/leaves, hard-linked leaves, duplicate JSON keys, stations
or streams fail. The complete closure is 122 stations, 434 moisture streams:
177 Percent, 160 VolumetricWaterContent and 97 Dimensionless unresolved scales.
No geography-based membership rule is introduced. The internal immutable
closure preserves station association, depth/null, orientation/case/null,
native unit and accepted unit status. Full identity lists are not embedded in
tracked source or this document.

check_document also provides a field-level comparison against that frozen
closure; it does not authorize a changed input hash. Plans may select explicit
subsets only. Every campaign retains the entire 434-entry roster, including
inaccessible and unresolved streams.

Metadata claims are an explicit **offline normalized input**, not a new parser
for real provider metadata. metadata_view produces a separate bounded,
sanitized overlay. Public names may change without changing identity. Missing,
private, hidden, incomplete, stale, changed-unit/depth/orientation or changed
scientific/dictionary bindings hold collection. Fresh complete evidence is
required before trusting a less restrictive privacy state. Geometry is omitted
when protected or access is unverified. Unrecognized raw fields are not copied
into metadata receipts. A later live adapter must verify and derive these
claims from exact provider responses; supplied hashes alone are not source truth.
Scientific claims include a normalized field object whose computed hash must
match the campaign binding; repeating an old declared hash cannot hide changed
terms/cadence. Diagnostics retain claim hashes, cadence claims, witness timestamps
and receipt hashes, pagination counts and unexpected-ID counts/hash. They do not
change identity or extend the roster. Activity/end/update clocks remain separate.

Known Percent ×1 and VWC ×100 in D1+D2's unit_route are identity-route metadata only. This route
performs no conversion or daily calculation and exposes no percent product
eligibility. All 97 unresolved streams retain null multiplier/offset and
unresolved_native_diagnostic; they can retain synthetic native input without
creating percent-VWC data/capabilities. Existing production eligibility guards
are not weakened.

## Separate derived scale policy

The additive offline module uses `dendra-scale-policy-1`,
`dendra-scale-evidence-1`, `dendra-scale-decision-1` and `dendra-scale-state-1`.
It leaves `unit_route`, frozen identity, provider admission and daily science
unchanged. Its complete state always contains the accepted 122 stations and 434
streams. All remain scale-policy eligible for native acquisition/archive storage,
subject to separately authorized acquisition and current provider access/privacy.

The 177 Percent identities retain `accepted_resolved_percent` and factor 1;
the 160 VolumetricWaterContent identities retain `accepted_resolved_fraction` and
factor 100. New primary assertions about those already accepted baseline routes
require a separate review and are refused by this gate. The 97 Dimensionless
native identities remain `native_only_scale_unresolved`, even when a separate
derived decision later becomes `evidence_resolved_percent` (factor 1) or
`evidence_resolved_fraction` (factor 100). An `unresolved` decision has null
conversion/normalized unit and false normalized/absolute-percent eligibility.

The derived eligibility booleans express **scale sufficiency only**. They do not
override access/privacy, accepted daily validation, whole-candidate holds,
publication or browser capability gates. No active product or campaign reads this
new state automatically; no numeric observations are converted here.

### Reviewed evidence boundary

`Evidence.bind(claim, source_bytes)` accepts a small normalized, explicitly
reviewed assertion and verifies its saved source SHA-256. Each assertion binds
the exact station/stream, source reference/hash/version, role/kind, explicit scale
category and temporal applicability. Source references are safe relative names
with optional fragments. Raw source bytes are not serialized into derived state.
Hashes prove byte identity, **not** authority, historical continuity or truth.
The caller must review those facts before designating a primary assertion.
There is no generic live metadata parser or automatic promotion of saved numeric
data to authoritative evidence in this API.

Primary kinds are authoritative datastream metadata, unit/dictionary metadata,
sensor/output configuration, explicit provider scale statements, or equivalent
version/hash-bound authority. Every primary assertion must directly establish
scale for that exact stream/sensor and period. An isolated generic dictionary
term, without the reviewed exact-stream binding, does not meet that requirement.
Supporting kinds are range/distribution, sister-stream and sensor-family
comparisons. Supporting assertions cannot resolve scale singly or in combination,
cannot masquerade as primary kinds, and cannot override primary ambiguity or
conflict. The module accepts no numeric confidence or range heuristic.

### Temporal decisions

Evidence applicability is explicit: `whole_history`, bounded UTC `interval`
with inclusive start/exclusive end, or `unknown_history`. A current source check
does not imply a historical interval; use `unknown_history` unless authority
establishes continuity. Unknown-history primary evidence conservatively leaves
the requested scope unresolved, including when other primary evidence exists.
No issue time or saved receipt time is repurposed as an effective date.

`resolve(inventory, stream_id, evidence, scope=...)` defaults to whole history;
it can also evaluate an explicit interval. It partitions that scope at primary
evidence boundaries, retaining interval-specific conversions, conflicts and
unsupported gaps. A null outer segment boundary denotes an unbounded unknown
past/future, not observed POR coverage. Whole-history eligibility needs explicit
whole-history evidence; finite interval evidence cannot fill those outer gaps.
Conflicting overlapping primary claims or an ambiguous applicable claim leave
that segment unresolved. Different, nonoverlapping historical scales retain
separate segments; the aggregate decision stays unresolved rather than selecting
one conversion. Disjoint conflicts do not poison a separately bounded query.

Top-level conversion is populated only when every requested segment is resolved
with the same conversion. Otherwise it is null and consumers must respect each
segment's explicit status. Categorical evidence status is `accepted_baseline`,
`primary_evidence_resolved`, `primary_evidence_conflict`,
`insufficient_primary_evidence` or `temporal_scope_unresolved`.

### Deterministic state and preservation

Every decision contains frozen inventory/identity hashes and unchanged native
fields separately from derived status, factor, normalized unit, scale eligibility,
evidence references, temporal segments and unresolved reason. Its
`decision_sha256` covers the canonical payload without that hash field. Evidence
order and exact duplicates do not change the result; source bytes, source
version/reference, applicability or policy changes create a different decision
identity. No clock or subjective confidence enters the decision.

`build_state` produces all 434 records in stable stream-ID order and a
`state_sha256`. `restore_state` requires the same accepted inventory and explicitly
supplied original source blobs by SHA-256; it reconstructs the decisions and
rejects altered, missing-source, incompatible-version or noncanonical state.
This is a separate sidecar suitable for immutable, content-addressed saves in a
task-owned directory. It neither overwrites a prior state nor appends anything to
an acquisition receipt. The API performs no filesystem or network writes.

Bounds are 64 assertions per stream, 1,024 per complete state, 8 KiB per normalized
assertion, 8 MiB per supplied source body, 256 KiB per decision and 2 MiB per
complete scale state. These are internal scale-state bounds, not revisions to
existing campaign, provider, archive or product limits.

Adding a Python module changes the existing collector-source fingerprint because
`source_binding` includes all package modules. Prior acquisition journals remain
bound to their original source checkpoint and are not reopened, migrated,
rehashed or repaired by scale reconciliation. Within one source checkpoint,
changing derived scale evidence/state does not change native campaign/plan/receipt
identity. Future integration must keep this separation; no automatic backfill
or native receipt rewrite is supplied here.

Focused offline verification is isolated to `tests/dendra/test_scale_resolution.py`
with the existing `DENDRA_INVENTORY` and task-owned `DENDRA_TEST_ROOT` inputs.
Tests deny provider entries, socket/DNS operations and real sleep before repository
imports. Any later live metadata/scale probe needs separate exact approval.

## Campaign and plan

The version is dendra-history-acquisition-offline-1; the request-policy binding
is dendra-history-request-1. campaign(...) requires explicit selected IDs,
campaign ID, as-of/completed cutoff, per-stream approved horizons when present,
scientific and dictionary hash bindings, request generation, collector/parser
source hashes, and an optional explicit seed/published parent hash. Publication
success is not acquisition identity.

Every budget is explicit: logical requests, attempts, response bytes, source
rows, distinct intervals, elapsed milliseconds and sessions. There are no live
budget defaults and no full-backfill planning ceilings embedded as authorization.
Zero attempt budgets are valid and cannot be exceeded.

plan(binding, intervals) is pure. Each input is an exact stream and half-open
UTC interval inside its approved horizon. Endpoints are 08:00Z at fixed UTC−08,
with no DST conversion. The planner splits into at most 30-day intervals.
Leap dates and Oct 1 remain calendar dates; it does not aggregate values or
invent daily scientific policy. Overlapping inputs, implicit horizon expansion,
duplicate/out-of-scope IDs and unfinished-day horizons fail.

Interval key = hash of campaign binding, exact stream identity, start/end and
request generation. Page logical key = hash of task, interval-run ordinal and
cursor. Attempt key adds the attempt ordinal. At most two reservations may use
one page logical key. Restarting a partial interval begins a new run at its
original start, spending the same cumulative campaign budget. A normal resume
does not change request generation.

## Local layout and durability

The caller supplies an existing absolute **task-owned** root. The I/O layer
confines all descendants via open directory descriptors and O_NOFOLLOW,
rejecting traversal, symlinks and multiply linked files. It does not decide
whether an arbitrary caller-supplied root has organizational authorization;
that root must be approved by the invoking task.

~~~text
registry/<campaign-id>.json
anchors/<campaign-id>/<sequence>.json
campaigns/<campaign-id>/
  manifest.json
  writer.lock
  plan/<page>.json
  events/<page>/<sequence>.json
  objects/<sha256>.bin
~~~

Registry names and state filenames are fixed; callers cannot pick a new ledger
filename to reset counters. Creating an existing campaign ID fails; reopening
requires byte-identical manifest/plan/source/budget bindings. A separate immutable
anchor is written before every event. Replacing the event directory with an empty
one fails against the anchors. This is integrity inside trusted local storage,
not a signature or protection against an external actor deleting/replacing the
entire task root. A copied or separately initialized root grants no authorization.

Records contain sequence, preceding hash, campaign header hash, timestamp, event
kind/data and record hash. Files use exclusive creation, file/directory fsync
and are never replaced. A nonblocking process lock prevents simultaneous writers.
There is no lock stealing, truncation, deletion or cleanup API.

Manifest/index pages are bounded to 262,144 bytes / 128 entries; plans and journal
replay are bounded to 4,096 entries/events. Events are at most 65,536 bytes;
objects are at most 8 MiB. Index byte limits may produce shorter pages. This
bounded offline format uses individual content-addressed objects, **not** the
proposed production-scale packed POR library. Pack sealing, long-campaign
segmentation/retention and full-backfill capacity require later implementation
review; existing archive2 file/page limits are untouched.

Each attempt is durably reserved before a fake transport response can be read,
then started, received or failed. A crash after reservation consumes an attempt.
Counters are replayed from immutable records, including retries and failed
reservations. Received bytes/source rows are charged before the next request;
an oversized/over-budget response is recorded and held, never refunded.
Malformed or restricted bodies are omitted while byte/hash receipts remain;
unparseable row counts remain null with an unknown-response counter and hold
further budget use for review, rather than being counted as zero provider rows.
Wall time includes time between reopenings conservatively, with a monotonic
within-process floor, and clock reversal holds. Session count is persistent.

Metadata injection in this offline package is not HTTP and does not manufacture
a provider-attempt receipt. The general reservation interface also supports
selected metadata tasks; a future real adapter must route every actual
metadata/page/retry call through it.

## Completion, privacy and resume

collect(journal, interval_key, transport=ReplayTransport(...)) accepts only the
concrete finite in-memory replay transport: byte bodies or synthetic HTTP status
codes. No arbitrary callback/subclass or default real transport is accepted.
Omitted/custom transport raises OfflineOnly before reserving an attempt.
The existing parser's HTTP-shaped Request object only reaches the replay opener;
it never reaches the network. Offline retry performs no real sleep.

Before replay, require fresh complete public metadata for the exact stream.
Response screening rejects restricted geometry/credential-shaped fields, hidden
metadata and out-of-selection stream IDs. Original synthetic bytes are retained
only after screening. The existing Dendra parser enforces effective limits,
ascending canonical UTC timestamps, half-open bounds, overlap paging, duplicate
conflicts and no precision-losing timestamp coercion. Full final pages cannot
seal. Nonadvancing cursors and page exhaustion hold.

The interval seal independently binds every successful received page to one run,
checks cursor continuity, raw hashes/lengths and short/empty completion, and
recomputes normalized rows/diagnostics from those exact raw bytes using the
existing normalization helper. A caller-supplied query_complete flag alone
cannot establish completion. Partial pages from a different run cannot be
spliced into a seal.

The state distinguishes unqueried, in-progress, incomplete after reopening,
complete-empty, complete-nonempty, request-failed and held. It keeps last attempt,
last successful source check, latest source observation and optional externally
supplied eligible daily date separate. A failed later explicit synthetic recheck
retains the earlier complete seal and original source-check time. Normal resume
reuses complete-empty and complete-nonempty intervals without taking a response;
other incomplete intervals remain independently retryable.
Current private/stale/incomplete metadata also holds cached reuse through collect;
the prior archived seal remains available for explicit local evidence inspection.

daily_evidence only records the hash/date of an explicitly supplied later result
for resolved complete input; it is marked unverified here. It performs no daily
validation and cannot authorize a product. Unresolved streams cannot use that
percent-daily evidence route.

Torn records, missing anchors, hash corruption or changed referenced objects hold
normal reopening. recovery=True exposes only the independently verified prefix,
including any earlier complete seals, and forbids all new writes/collection.
It never accepts a torn tail, promotes an anchor intent to a completed interval,
repairs files, refetches observations or deletes damage. Human-reviewed repair
or a separately designed verified recovery transfer remains a later gate.

## Focused offline verification

Supply the accepted inventory and an existing task-owned test root explicitly:

~~~sh
PYTHONDONTWRITEBYTECODE=1 \
DENDRA_INVENTORY="$FROZEN_INVENTORY" \
DENDRA_TEST_ROOT="$TASK_TEST_ROOT" \
python3 -m unittest discover -s tests/dendra -p test_history_acquisition.py -v
~~~

Tests use the checksum-bound inventory plus synthetic metadata/response bytes.
A process-wide audit hook rejects socket operations. Test roots, including
damaged specimens, are preserved. No saved observation replay, production builder,
R numerical test, browser test, unrelated acceptance suite or performance cycle
is part of this command.

## D3 readiness boundary

D1+D2 does not make D3 executable. A separately reviewed live adapter still needs
real provider metadata/dictionary parsing and privacy verification, exact request
allowlisting, campaign authorization, whole-session/response deadlines, measured
Retry-After and response-stream bounds, reservation of every real attempt, and
safe capture of actual provider bytes. The current replay class cannot be
switched to live by a flag.

Dave must separately approve the two-stream leap-day probe, its output root,
resource window and at most 14 total attempts / 300 seconds. Later small-batch,
full-backfill, scientific/daily integration, Dendra browser projection,
storage-capacity migration and publication remain separate gates. No automatic
D3 continuation is authorized by this contract.
