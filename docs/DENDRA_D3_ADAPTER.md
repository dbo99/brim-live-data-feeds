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

For the exact Dimensionless target, a `science.terms_attributes_shape` HOLD
can additionally carry `target_scientific_shape`. It has exactly `terms` and
`attributes`; each contains only `present` (boolean) and `json_type` (one of
`missing`, `null`, `object`, `array`, `string`, `integer`, `number`, `boolean`).
`present=false` is paired only with `missing`. Context is captured after complete
page, exact target/station association and public/nonhidden admission, only at
the existing scientific-shape failure. It includes no values, provider key names,
coordinates, other stream fields or new identity values. It is independent of
the generic first-two-row sample and survives generic-field trimming within the
unchanged 12,288-byte ceiling. The optional context retains diagnostic version
`dendra-metadata-diagnostic-1`, reason and parser-site meaning. Success and other
HOLDs omit it; both scientific fields must still be JSON objects. Old diagnostics
without this optional context remain valid and cannot establish these target facts.

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

## Exact Dimensionless metadata readiness (offline only)

`scripts/dendra/history_acquisition/dimensionless_probe.py` adds an independent
one-shot metadata injection boundary, version `dendra-dimensionless-probe-1`.
The original D3 selection, vocabulary requirement, observation runner, transport
and retry policy are unchanged. This boundary does not authorize HTTP, create a
provider campaign, open a journal, resolve scale or enable an acquisition product.

The sole target is station `5d8f7f052da5c3a1bdf65382`, stream
`5d9272a12da5c3cff0f655ed`, already used by the accepted offline acquisition
fixtures. Its hash-bound frozen identity remains null depth/orientation,
`Dimensionless`, `native_only_scale_unresolved`. Selection does not use observed
ranges, geography, presumed scale or a desired result. `make_plan` requires the
accepted complete inventory and these explicit IDs; arbitrary IDs are rejected.
The plan binds the inventory hash, exact identity, all collector source hashes,
two request descriptors and the fixed envelope. A mutated plan or changed source
binding prevents dispatch. Adding this module changes `model.source_binding`.
Prior journals stay immutable and tied to their original source checkpoint.

The proposed later order is:

1. `GET https://api.dendra.science/v2/stations/5d8f7f052da5c3a1bdf65382`
2. Only after successful station admission and receipt persistence:
   `GET https://api.dendra.science/v2/datastreams?station_id=5d8f7f052da5c3a1bdf65382&%24limit=500&%24sort%5B_id%5D=1`

The envelope is at most two logical requests/two HTTP attempts, zero retries,
zero redirects, concurrency one, 25 seconds and 8 MiB per request, 50 seconds and
16 MiB total. There is no metadata pagination, vocabulary, observations, witness,
temperature, POR discovery, other station or automatic continuation. Reads use
the existing total-deadline primitive, bounded chunks and one overflow sentinel;
an oversized or interrupted prefix is charged/hash-bound but not retained as an
original response or represented as a complete body. Any failure terminates the
one-shot instance. It cannot be rerun, including after a failed reservation.

`Probe.run` requires explicit executor, reservation, receipt and clock callbacks.
Synthetic tests inject finite in-memory responses; no live opener is constructed
by this module. Before a future live run, separate authorization must bind the
reviewed source/plan, a fresh exclusive task root, a fixed window and this exact
budget. Its tiny driver must use real clocks, the existing anonymous `NoRedirect`
opener with proxy inheritance disabled and no cookie/auth handlers, exclusive
durable reservation before each executor call, and durable sanitized receipt
persistence before continuation. A crash reservation remains spent: no restart,
new instance or old-journal resume may refund an attempt. The injected executor
and persistence callbacks are a trusted boundary; arbitrary callbacks are not a
certified live transport or durable ledger. No live driver or campaign is created
by the offline readiness gate.

### Explicit temporal-configuration review profile (offline candidate)

`dendra-soil-temporal-config-review-1` is an explicit alternative to
`dendra-soil-conditional-attributes-1`; it does not replace historical D3 or
the singleton profile. Its packet is `dendra-soil-temporal-metadata-review-1`.
The unchanged two-request plan selects only the exact target above and binds
profile, packet version, frozen inventory/identity and collector source hashes.
Old journals remain immutable. New source requires fresh separately authorized
state; no live request or automatic continuation is enabled by this profile.

Authority was inspected on 2026-09-26. The [Release 2 documentation](https://docs.dendra.science/technical/apis/release-2-api/)
links [API v2 OpenAPI](https://api-v2-docs.dendra.science/openapi/openapi.json)
(document version `0.1.0`). `Types.definitions.datapointsConfig` permits a
nonempty array; its instance defines optional `begins_at`/`ends_before` strings
and backend dispatch fields. It does not define or require `interval`.
The [Release 2 repository map](https://docs.dendra.science/technical/github-repositories/)
identifies `dendra-web-api` and `dendra-json-schema`. Inspected schema blob
`1012b30375cea7c5625b7580cc429c926385058b` is identical in both repositories;
the schema package reports `2.0.3`. API source `src/server/lib/datapoints.js`,
blob `1f37c29d2f77a1a8c3563a58aa2a6ee755092328`, documents half-open intervals
and handles unspecified built bounds with backend sentinels. Those sentinels,
merging, exclusion and overlap precedence are **not** implemented here.
Inspected source is not proof of the deployed implementation or target values.

The [configuration guide](https://docs.dendra.science/guides/how-to-manage/configure-datastreams/)
corroborates shared-end/start adjacency and an omitted end for ongoing data,
but includes Release 3 UI behavior. The [background](https://docs.dendra.science/technical/background/)
is architectural context; the [older generated schema](https://dendrascience.github.io/dendra-json-schema/)
is historical corroboration. Neither overrides the captured Release 2 mapping.

The bounded review policy is:

- Retain original zero-based ordinals and whole object/ordered-array/selected
  record/response hashes. Review at most eight configurations; overflow HOLDs
  with explicit omitted count and full-array hash. No duplicate is discarded.
- Compare half-open `[begins_at, ends_before)` periods using exact rational
  timestamps, retaining the original strings. Release 2's inspected schema
  admits UTC `Z` timestamps with a fractional part of up to three digits.
  This profile requires one to three digits and calendar-valid timestamps.
  Other syntactically valid offset/precision strings are retained unchanged as
  `UNSUPPORTED_R2_FORMAT` evidence and HOLD, not rounded or silently normalized.
  The schema's zero-digit fractional-pattern edge is not treated as a valid date.
- Missing start is `ABSENT_UNKNOWN` and HOLD. Missing end is `ABSENT_OPEN_END`,
  the documented ongoing convention, without any invented timestamp. Explicit
  null is distinct and HOLDs: the inspected schema does not permit null.
  Inverted/empty windows HOLD. No alias maps `starts_at` to `begins_at`.
- Temporal order is separate from original order. Check every pair for
  adjacency, gap or overlap. Gaps remain gaps; overlaps and duplicate objects
  HOLD without a winner, merge or backend stitching. Invalid/unknown windows
  are explicitly excluded from temporal comparison, never admission.
  Relations are explicitly pairwise configuration comparisons, not a union of
  observed coverage: another configuration may occupy a pair's separating gap.
- A supplied interval must be a positive numeric value at most `2^53-1`;
  booleans, null and malformed values HOLD. Keep it per configuration with the
  existing local millisecond interpretation, explicitly labeled a legacy local
  provider claim **not defined by the captured Release 2 schema**. Missing
  interval is `ABSENT_UNSPECIFIED`, with no fabricated cadence. No universal
  configured cadence is emitted. This is metadata review, not full provider
  schema validation or a new daily-science authority.

The complete configuration evidence allowlist is `begins_at`, `ends_before`,
`interval`, `connection`, `params`, `path`, and `actions`. Every slot has
presence/type/hash/validation status. Only grammar-valid temporal strings and
valid numeric interval values may be retained. Invalid values are represented
by type/status/hash only. Backend fields retain presence/type/hash/status only;
no connection/path/parameter/action value, expression or nested key escapes.
Supplied `actions` HOLDs as unreviewed transform/exclusion semantics. Unknown
fields have a count and aggregate hash, without names/values, and HOLD. Thus
equal safe projections cannot silently make unknown content irrelevant.
Backend routing omissions do not prove complete scientific interpretation;
`backend_semantics=not_evaluated` remains explicit even on metadata admission.

`dendra-target-temporal-evidence-1` is a separate non-admitting evidence record,
limited to 24,576 bytes. It is created only after fresh station, complete page,
exact target and public-access checks. A later scientific/descriptive HOLD
retains this record. The injected runner persists a HOLD envelope containing
the existing `dendra-metadata-diagnostic-1` diagnostic (still at most 12,288
bytes) and this evidence; the envelope is at most 65,536 bytes and explicitly
`metadata_admitted=false`. No unrestricted rejected body is retained. A later
live driver must recognize this versioned envelope, not treat it as admission.
An admitted temporal packet also embeds/hash-binds the evidence, which alone
never asserts metadata admission. Early access/page/resource failures cannot
produce temporal value evidence. Protected coordinates and optional-Z numbers
are absent from the new packet; the accepted separate station sanitizer remains
unchanged.

Required Soil/VolumetricWaterContent/native-unit, conditional attributes,
frozen depth/orientation and public/privacy checks remain intact. Stream-level
description validation remains, but no single configuration is chosen as the
stream's current activity/end. Existing allowed scientific attribute claims
stay unreviewed claims. No configuration multiplier, boundary or cadence yields
scale evidence, historical continuity, POR, observed sampling, transformation
or permission. Every packet/evidence envelope keeps `raw_eligible=false`,
`observation_acquisition_authorized=false`, `daily_science_accepted=false`,
`browser_publication_eligible=false`, empty scale assertions and `unknown_history`.
The 337 accepted conversions and 97 unresolved identities are unchanged.

The latest real target remains HOLD. A synthetic temporal PASS cannot change
that result. The focused `test_dimensionless_probe.py` suite covers both
profiles, historical authority/guards, timelines, provenance, partial evidence,
privacy and source binding with provider/socket/DNS/real-sleep attempts denied.
No backend service, provider implementation or downloaded code is executed.

### Existing singleton review behavior

Station and first-page list guards share the existing implementations without
broadening the old D3 public entry point. Exact station ID, public level 3,
explicit nonhidden state, deletion/privacy rules, freshness, optional-Z behavior,
cadence and configured-end validation remain required. A list must have an
effective limit from 1 to 500 and fewer rows than that limit, zero offset and,
when supplied, total equal to row count. Duplicate IDs, wrong-station rows, missing
target, a full/ambiguous page or target privacy failure HOLD without pagination.
All rows must belong to this station; other streams contribute only IDs, counts
and response/ID-set hashes. Their scientific or private fields are not projected.

The conditional-attributes profile yields `dendra-soil-metadata-review-1`, a bounded
local review packet. Its original response, selected record, scientific claims
and configuration receive separate hashes. The packet preserves allowlisted
terms and scalar depth/orientation/scale/output-unit/calibration/configuration
fields exactly as provider claims, with omission counts for unknown scientific
fields. The projection slots describe what can be safely retained; synthetic
tests do not prove that a real provider uses those fields. Arbitrary nested values
in recognized leaves HOLD; unrecognized keys/values and coordinates are omitted.
Unknown claims require review, never silent classification or a follow-up call.
The packet's content hash is distinct from the original provider-body hash.

No pinned scientific authority exists for this target beyond frozen identity.
Unlike the existing D3 acquisition gate, this metadata-only gate discovers and
hashes current scientific claims for review; admission is not scientific approval,
dictionary verification, raw acquisition permission or a conversion decision.
Native Unit must still equal `Dimensionless`; that label proves no scale.
The packet always has `scale_assertions=[]`, `claim_review=unreviewed_provider_metadata`
and `historical_applicability={"kind":"unknown_history"}`. Present provider
dates/configuration are retained as claims without inventing historical coverage.

After a separate evidence review, a qualifying exact-stream primary assertion
can reference the saved sanitized packet bytes/hash/version through unchanged
`scale_resolution.Evidence.bind`. That hash proves reviewed bytes, not provider
authority or semantic correctness. The reviewer must establish the source field's
meaning and temporal applicability. Supporting ranges or sister-stream claims
cannot resolve scale; absent continuity cannot resolve whole history. No real
stream is resolved by the synthetic readiness specimens. The accepted 337
baseline conversions and all 97 unresolved roster entries remain unchanged.

Run only the new `test_dimensionless_probe.py` and affected
`test_d3_metadata.py` offline with the explicit accepted `DENDRA_INVENTORY` path.
Both deny sockets/DNS before repository imports; provider entry points and real
sleeps are guarded. Passing tests mean ready for separate lead review and live
authorization, not observed provider compatibility or approval to execute.
