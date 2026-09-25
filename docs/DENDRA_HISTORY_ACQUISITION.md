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

Known Percent ×1 and VWC ×100 are identity-route metadata only. This layer
performs no conversion or daily calculation and exposes no percent product
eligibility. All 97 unresolved streams retain null multiplier/offset and
unresolved_native_diagnostic; they can retain synthetic native input without
creating percent-VWC data/capabilities. Existing production eligibility guards
are not weakened.

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
