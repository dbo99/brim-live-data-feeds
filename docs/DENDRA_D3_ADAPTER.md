# Dendra D3 adapter readiness

This is an offline-tested, local acquisition boundary for a separately authorized
two-stream probe. It is not a production entrypoint, provider-access approval,
numerical acceptance, publication or activation. The accepted daily R science,
existing transport, bridge and routine production limits are unchanged.

## Explicit execution and identity

`scripts/dendra/history_acquisition/d3_plan.py` builds a pure seven-task plan.
`provider_metadata.py` checks metadata against the explicit accepted inventory
and hash-pinned `data/input/dendra/pilot_catalog.json`. `provider_adapter.py`
wraps the unchanged `DendraFetcher` through its opener and wait injection points.
The existing `Journal` remains the only reservation/receipt ledger. Its two
additional interfaces admit a strictly validated D3 campaign and record sanitized
metadata representations separately from original response hashes/byte counts.
Legacy offline calls keep their original defaults.

Import, construction, plan creation and opening evidence do not issue HTTP.
`Adapter.run(executor=..., wait=...)` is an explicit synchronous injection
boundary used with synthetic responses by tests. There is no default executor,
command-line dispatch, environment activation flag or background worker.
`run_authorized_probe` is the explicit future live entry point. Its required
authorization binds the campaign hash, already existing output directory,
approval reference and at most 300-second resource window. This records a
separate human authorization; filling out a dictionary does not grant permission.

The exact frozen inventory SHA is
`f81b91bd86ade8e5380063e1ce3b37d1dddb1ca5d21368cd3ecb4514688190d9`.
The accepted scientific catalog SHA is
`44e5b22ab113c4f57177fb2ab1c894b4e327ff98e9e93be26050336fd8196e2b`.
The two selected identities are:

| Station | Station ID | Stream ID | Frozen identity |
| --- | --- | --- | --- |
| Camp Cady WA | `635319fcb055ac5348842453` | `63531a67a9b61453fa1ca4ed` | 20 cm, horizontal, Percent ×1 |
| Deep Canyon | `58e68cabdf5ce600012602bd` | `5d8e42e72da5c3cc53f6531d` | null depth, Vertical, VolumetricWaterContent ×100 |

Names are descriptive. Neither refreshed metadata nor geography rewrites this
identity. All other accepted streams, including unresolved scales, remain in the
private campaign roster without becoming selected requests or browser products.
No conversion or daily calculation is performed by this adapter.

## Exact request scope

All calls are anonymous GET requests to `https://api.dendra.science/v2/`.
Only constructed endpoint shapes and exact unique query values are admitted.
Alternate origins, schemes, credentials, cookies, arbitrary headers, write
methods, arbitrary paths, redirects and additional query parameters fail closed.
The live opener disables proxy inheritance and has no cookie/auth handler.

The baseline order is exactly:

1. `vocabularies/dt-unit`.
2. Camp Cady station object.
3. Deep Canyon station object.
4. Camp Cady datastream list, `$limit=500`, `$sort[_id]=1`.
5. Deep Canyon datastream list, same limits.
6. Camp Cady observations.
7. Deep Canyon observations.

Each observation task is exactly
`[2024-02-29T08:00:00Z, 2024-03-01T08:00:00Z)`, with exact `datastream_id`,
`time[$gte]`, `time[$lt]`, `$sort[time]=1` and `$limit=2016`.
The unchanged transport carries repeated-boundary paging and source timestamps
without a DST shift. There are no POR witnesses, temperature discovery,
current-day calls, horizon expansion or automatic continuation.

Seven is the baseline task count, not a guarantee about actual response pages.
Observation pagination uses the existing outer 20-page bound and consumes the
same global request/attempt allowance as metadata and retries. Explicit logical
and attempt budgets cannot exceed 14. A lower explicit logical budget can HOLD
before another page. Metadata never paginates: missing/invalid effective limit,
full page, nonzero offset or inconsistent total holds before observations.

## Metadata and privacy

Station objects require their exact ID, public level 3 and explicit nonhidden
state. Stream lists require complete first-page evidence, unique IDs and exact
station association. Selected streams independently require public level 3 and
explicit nonhidden state. Missing/deleted/404-like responses hold; absence is
not inferred from a failed request.

Scientific comparisons use the catalog's exact selected terms, attributes and
configured cadence as diagnostic claims. Dictionary admission requires the
accepted exact Percent and VolumetricWaterContent term projections. A changed
scientific claim holds. Descriptive names and inactive/ended claims remain a
separate overlay. Configured cadence is not a new inferred daily cadence: the
accepted Camp Cady catalog already notes different short-sample spacing.

The legacy bridge's bounded nested vocabulary search is retained, with exact
vocabulary ID and unique selected terms. Synthetic tests exercise outer nesting;
they do not establish that a current live provider response has that shape.
Unexpected real shapes hold for review without exploratory follow-up calls.

Metadata freshness uses the new source-check receipt, never an old cached
permission. Both station and stream checks must belong to the explicit wave;
private/hidden/missing/stale/scientifically changed input prevents observation
dispatch and clears permissive overlays. Previously completed evidence survives.
Protected or unspecified geometry protection omits coordinates. Unselected
streams are never acquired; only bounded ID diagnostics remain in private local
state. Credentials, unrestricted error bodies and arbitrary provider fields are
not persisted as metadata representations.

Station `Point` positions admit exactly two or three finite numeric elements;
booleans are not numeric coordinates. Longitude and latitude retain their
existing bounds. The optional third element has no additional magnitude bound
and is preserved without conversion in the sanitized station receipt as
`geo_z_native`, with `geo_z_semantics="unverified"`, `geo_z_unit=null` and
`geo_z_datum=null`. It is an optional native vertical coordinate, not a verified
elevation, unit or datum claim. The existing `geometry` field remains the
two-element longitude/latitude projection. Two-coordinate station receipts keep
their previous shape and omit all `geo_z_*` fields.

Protected or unspecified geometry protection suppresses all coordinates,
including every `geo_z_*` field. Private, hidden, deleted or otherwise inadmissible
stations still fail admission. Rejected metadata retains only the existing
bounded diagnostic; it never retains coordinate values or an unrestricted body.
The optional Z lives only in provider-refreshable station receipt metadata. It
does not enter stream identity, stream metadata views, the frozen inventory,
accepted seed coordinates, daily science or public product/UI fields. Determining
its meaning, units, datum or future display requires a separate review.

## Bounds, retry and receipts

The probe envelope is 25 seconds per request, 8 MiB per body, 16 MiB cumulative
response bytes, 20,000 source rows, 14 global HTTP attempts and 300 seconds total.
Concurrency and acquisition workers are both one. One explicit session is
allowed; reopening evidence does not automatically dispatch or reset counters.
The authorization window additionally bounds the live request/read and wait.
These are probe ceilings, not revised production defaults.

The serial POSIX total alarm bounds the open/read operation, including a
slow-drip response; socket inactivity timeout alone is insufficient. Reads are
chunked and stop at the smaller remaining byte allowance plus one sentinel
byte. The consumed prefix is charged even when too large. Oversized/incomplete
bodies are not persisted. Unknown row counts remain unknown and hold further
collection. There is no decompression path; encoded bodies hold.

Only transport failure and HTTP 408/429/500/502/503/504 are retryable. At most two
attempts per page/task are allowed, including prior durable reservations.
The existing persistent HTTP-service failure circuit remains in force.
Retry-After accepts finite nonnegative integer seconds or a UTC HTTP date only
when the delay is at most 15 seconds and fits the remaining budget. Invalid or
larger values hold instead of falling through to permissive low-level parsing.
Tests inject a fake clock/wait; they never actually sleep.

Every attempt is durably reserved and started before the executor is called.
The same received event charges original response bytes/rows exactly once,
records original hash, status, timing, retry decision, pagination and validation
outcome. Metadata objects have separate sanitized hashes/lengths and an explicit
`representation`; `body_retained` does not mean original metadata bytes were
retained. Error bodies and headers are not stored. Observation bodies use a
strict observation-field shape and are eligible for exact raw persistence only
after current public identity admission and budget checks. Envelope pagination
values cannot hide nested restricted metadata.

Local reservation/object/receipt storage failures are nonretryable HOLDs, never
network retries. Reserved crash attempts remain spent. The existing immutable
anchors, source binding, single writer lock, complete-interval seals and read-only
damaged-prefix recovery remain authoritative. No competing ledger, repair,
refetch, cleanup or publication pointer was added. Existing evidence bound to
older source hashes is preserved, not silently migrated to changed code.

## Rejected metadata diagnostics

The metadata admission functions remain unchanged. A rejected HTTP-200 metadata
response now records a `dendra-metadata-diagnostic-1` object through the existing
sanitized-object receipt. Its original body is still omitted; the received event
still charges the original bytes and known/unknown row count. The object binds
that body's hash/length and contains a stable reason code, category, bounded
exception-class label, and the originating parser function/line where available.
For example, `station.id_missing`, `station.id_type`, `station.id_mismatch`,
`access.public_nonhidden_required`, `science.identity_mismatch` and `list.total`
distinguish different conditions without asserting an unproved provider cause.
Unmapped conditions retain a parser location/class, never arbitrary exception text.

Shape diagnostics contain JSON types, container counts, required-key presence
and fixed schema key names. Unknown key names and all field values are omitted,
including IDs, names, coordinates, credentials and private/hidden values.
Traversal is deterministic: at most 64 fields, depth 4, 16 allowlisted keys per
object, two sampled entries per list, 96 characters per path and 12,288 bytes per
diagnostic. Omitted keys and truncation are explicit. Lists and objects expose
cardinality, not their unrestricted content. Invalid JSON has an `unparsed`
shape. These diagnostics do not admit a response, update permission, or authorize
another request.

`UnknownSourceRowCount` is a typed form of the existing journal HOLD. Its
condition, message and accounting are unchanged. After `received` has durably
saved a failed metadata response and its diagnostic, the adapter catches only
that guard to return the originating `MetadataAdmissionHold`. It still records
failure and ends the wave in HOLD. Unknown rows continue to block reservations,
sessions and subsequent collection. Observation handling and all other storage,
integrity, time, byte, row and attempt failures retain their existing behavior.
Reopening does not dispatch, refund consumed work or rewrite the diagnostic.
`body_retained=true` with `representation=sanitized` on a held receipt refers
only to this safe diagnostic object, never the rejected original response.

The earlier live station HOLD cannot be diagnosed retrospectively: its body and
specific reason were not retained. A one-request station diagnostic requires
separate authorization for exact source bytes, station endpoint, fresh output
root, resource window and one-request execution boundary. The existing live
entry point still runs the seven-task plan; it is not a one-request command.
No new live execution mode or provider authorization is introduced here.

## Focused offline verification and later gate

Use explicit accepted inventory and existing task-owned test roots:

```sh
PYTHONDONTWRITEBYTECODE=1 DENDRA_INVENTORY="$FROZEN_INVENTORY" \
DENDRA_TEST_ROOT="$TASK_TEST_ROOT" \
python3 -m unittest discover -s tests/dendra -p test_d3_metadata.py -v
PYTHONDONTWRITEBYTECODE=1 DENDRA_INVENTORY="$FROZEN_INVENTORY" \
DENDRA_TEST_ROOT="$TASK_TEST_ROOT" \
python3 -m unittest discover -s tests/dendra -p test_d3_adapter.py -v
```

Run these serially. Audit hooks deny sockets/DNS before repository imports and
patched sleep fails on any real wait. Synthetic roots and failed specimens are
retained. Only individually affected legacy journal cases need regression checks;
no unrelated suite, saved observation replay, R numerical validation or
performance benchmark is part of this gate.

Passing offline evidence can support `READY_FOR_SEPARATE_AUTHORIZATION`, not
live correctness or a completed D3 probe. The next task must separately approve
the exact campaign, two streams/day, fresh output root, resource window and
budgets. Real access/privacy, response shape, limits, bytes, latency and units
remain measurements for that probe. Full backfill, numerical acceptance, browser
projection, storage capacity and publication remain separate gates.
