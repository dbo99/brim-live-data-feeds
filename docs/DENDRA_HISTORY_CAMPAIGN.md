# Dendra history campaign contract

Status: reviewed eligibility, deterministic planning, durable campaign execution
and conservative resume are implemented. The additive daily handoff and explicit
presentation planner are an offline integration candidate. The separately
authorized first live batch is sealed; that historical evidence grants no later
request authority. A plan, eligibility decision or fabricated authorization
dictionary never supplies maintainer approval.

This is an internal acquisition and offline handoff contract, not an activated
public feed, scheduler or publication policy. The four technical sources of
truth listed in [README.md](README.md) retain authority. Existing acquisition
mechanics are described in [DENDRA_HISTORY_ACQUISITION.md](DENDRA_HISTORY_ACQUISITION.md);
the deliberately narrower live adapter is described in
[DENDRA_D3_ADAPTER.md](DENDRA_D3_ADAPTER.md).

## Implementation and ownership

The candidate additions in `scripts/dendra/history_acquisition/` are:

- `eligibility.py`: explicit review proposals and versioned immutable decisions;
  metadata admission alone does not approve acquisition.
- `campaign.py`: offline state partitions and deterministic interval plans with
  source/profile bindings and accepted coverage inputs.
- `campaign_execution.py`: reconstructs reviewed tasks, binds them to the existing
  Journal and verifies current eligibility before dispatch; derives status from
  durable receipts and seals.
- `journal.py` and `provider_adapter.py`: the existing ledger and HTTP stack also
  accept `campaign_reviewed_adapter`, without changing the narrower D3 policy.
- `campaign_cli.py`: offline plan/status/verify and separately authorized
  collect/resume use these same interfaces; `prepare-product` is an explicit
  offline preparation into fresh scratch, with no publication authority.
- `presentation.py`: reviewed source-start horizons for explicit initial 10-WY
  or full-POR planning; audit estimates never authorize executable horizons.
- `daily_handoff.py` and `daily_prepare.R`: verify sealed native provenance,
  translate to the existing R input contract and delegate numerical science to
  unchanged `scripts/dendra/core.R`.

Reuse `model.Inventory`, `safety`, `scale_resolution`, timestamp parsing and
normalization in `scripts/dendra/transport.py`, and the existing `Journal` object
store, reservations, receipts and seals. No duplicate transport, source ledger,
scale resolver, metadata admission parser, or archive writer is introduced.
There is no BRIM launcher, service, bucket, schedule, or production output here.

Exactly one execution authority is required for a campaign. Its durable
request accounting and concurrency policy are independent of reasoning workers,
threads used for review, or workstation capacity. Initial provider concurrency is
one. Agents must never receive independent provider budgets or restart authority.

## Independent stream state and roster closure

The accepted byte-bound inventory remains 122 stations and 434 streams. Every
campaign report must retain and reconcile all 434, including unselected, held,
unresolved and covered streams. Selection changes a batch, not frozen identity.

Each stream has independent dimensions:

| Dimension | Meaning |
| --- | --- |
| Frozen membership | Exact station/stream association and inventory identity hash |
| Current access | Fresh public/nonhidden evidence, unknown, or access HOLD |
| Metadata review | Missing, proposed, accepted, changed, or review HOLD |
| Scientific identity | Match, unknown, or identity HOLD |
| Scale | Accepted baseline, exact-evidence resolution, conflict, or unresolved |
| Historical applicability | Whole-history authority, bounded authority, or unknown history |
| Native acquisition | Eligibility for explicitly reviewed native query scope |
| Normalized percent | Scale sufficiency for an exact period plus other required gates |
| Queried coverage | Complete query intervals, including complete empty intervals |
| Archived coverage | Intervals with verified durable receipts and archive seals |
| Daily eligibility | Separately accepted daily science, never inferred from retrieval |
| Publication eligibility | Separately accepted product/publication gates |

Partition labels summarize these dimensions; they do not replace them. Current
offline planning reports have four primary partitions: `metadata_review_required`,
`native_eligible`, `access_hold`, and `review_required`. Separate scale counts
retain baseline and unresolved routes. Coverage fields are null (not loaded),
and make no coverage claim. Journal summaries separately reconcile all 434 streams,
including unselected streams, using task counts, verified seals, complete-empty
counts, HOLD counts and `UNQUERIED`, `PARTIAL_OR_HOLD` or `COVERED` query coverage.
They include durable counters and per-task recovery instructions. `COVERED` means
the planned queries completed, not that continuous observations exist. A scale-only
PASS cannot override access or metadata HOLD.

## Explicit metadata-to-acquisition review

Metadata packets deliberately keep `raw_eligible=false` and
`observation_acquisition_authorized=false`. Do not edit those flags. A separate
decision records explicit acceptance of a proposal after review of its exact
evidence and permitted interval scope. Hashes establish byte identity, not
signatures, provider truth, or reviewer authority. Acceptance is an explicitly
trusted maintainer input; fabricating an acceptance object is not approval.

Only exact supported packet/profile versions may enter this boundary:

| Scope | Metadata profile | Packet | Configuration evidence |
| --- | --- | --- | --- |
| Historical exact Dimensionless target | `dendra-soil-temporal-config-review-1` | `dendra-soil-temporal-metadata-review-1` | `dendra-target-temporal-evidence-1` |
| Any exact frozen roster member | `dendra-soil-campaign-temporal-config-review-1` | `dendra-soil-campaign-temporal-metadata-review-1` | `dendra-campaign-temporal-evidence-1` |

The additive campaign profile reuses the temporal/scientific review parser and
requires known depth/orientation to match. It admits no unreviewed projected
scientific field. It does not fetch metadata or convert existing baseline scale
routes into metadata evidence. Historical packet bytes and exact-target semantics
remain unchanged. Unsupported, mixed-version, incomplete or HOLD evidence refuses
eligibility; a successfully parsed packet cannot accept its own review.

Bind inventory bytes and frozen identity, packet bytes/version/profile, source
checkpoint, source response and selected-record hashes, scientific identity,
configuration evidence, scale decision reference, review identity, and permitted
scope. Bind fresh station and stream access evidence, check timestamps, and an
explicit expiry. `dendra-native-eligibility-2` records these identities, decision
hash, native unit/status, scientific and temporal status, access evidence/check
times, historical applicability, scale-state hash, reviewer/policy versions,
evaluation time, expiry and exact HOLD reasons. It separates
`NATIVE_ACQUISITION_ELIGIBLE` / `NATIVE_ACQUISITION_HOLD` from
`NORMALIZED_PERCENT_ELIGIBLE` / `NORMALIZED_PERCENT_HOLD`.

`dendra-native-acquisition-review-1` is a trusted, explicit review input with
`PENDING`, `ACCEPT_NATIVE` or `HOLD` disposition. Acceptance requires a reviewer
reference, a review window of at most 24 hours and these exact acknowledgements:
`backend_not_interpreted`, `history_not_science`,
`native_only_no_scale_inference`, `unknown_identity_stays_unknown`.
Station/stream/packet access checks must be nonfuture and at most 24 hours old;
the earliest metadata or review expiry limits the decision.

`validate_decision` reconstructs the decision from the original packet and trusted
review at its original evaluation time, compares the complete canonical result,
then re-evaluates freshness at dispatch time. Rehashing an edited decision does
not authorize it. The adapter invokes this guard before every page reservation
and immediately before dispatch. There is no automatic metadata refresh. A
provider access refusal holds later tasks for that stream; freshness checks do
not claim to detect an unobserved upstream revocation.

Known frozen depth/orientation require matching reviewed evidence. Frozen unknown
values remain explicitly unknown when accepted evidence does not contradict
them; omission does not invent a depth or orientation. Scientific terms and native
unit must match. Temporal review retains adjacency, gaps and open ends; overlap,
duplicates, unknown fields, unreviewed actions and unsupported bounds HOLD.
Missing interval does not invent cadence. Backend routing claims remain
unreviewed and never become local transforms.

Native acquisition may be accepted with unresolved scale if all non-scale gates
and the explicitly reviewed native scope pass. Unknown historical applicability
must remain visible; it cannot authorize historical conversion. Daily acceptance
and publication remain false unless their separate authorities establish them.

Access restrictions, identity changes, configuration changes and changed bound
scientific evidence invalidate future use of the decision. Descriptive-only
changes can require re-review without declaring previously archived bytes
scientifically invalid. Never rewrite immutable prior receipts to fit new claims.

## Scale policy

The frozen routes remain 177 Percent streams with factor 1, 160
VolumetricWaterContent streams with factor 100, and 97 Dimensionless streams with
`native_only_scale_unresolved` unless accepted exact-stream evidence resolves a
separate derived decision. These are scale routes, not current access approval.

Reuse `scale_resolution` for source/hash/version-bound primary evidence and its
temporal segments. Numeric ranges, sister streams, station family and current
configuration alone cannot resolve scale. Native archives preserve original
values; normalized-percent eligibility must cover the exact product period and
remain separate from daily scientific acceptance. The campaign can progress
without resolving every Dimensionless stream.

## Deterministic planning and task identity

The task unit is one stream and one half-open UTC interval. Select the whole
campaign, an exact stream subset, or an exact station subset resolved against the
frozen roster. Require an explicit campaign ID/version, exact horizon, bounded
maximum task count, and deterministic ordering. No POR discovery, implicit
horizon expansion, or network access occurs during planning.

Split intervals at accepted configuration boundaries and into chunks no longer
than 30 days. Preserve configuration gaps as explicit unplanned/HOLD scope rather
than inferred empty coverage. Captured Release 2 semantics describe half-open
configuration bounds, but are not proof of deployed stitching or scale
continuity. Local splitting keeps provenance clear without implementing backend
routing, precedence, transforms or provider stitching.

Logical task identity binds campaign/source/profile versions, frozen inventory
and stream identity, review decision, configuration evidence, exact interval and
request policy. The associated scale-state reference is a
sidecar outside the native task hash: changing only scale evidence does not
rewrite native task or receipt identity. Batch selectors, report ordering,
agent count and task-count limits must not redefine an otherwise identical task.
Changing bound evidence creates new task identity; it does not migrate old state.

The pure planner checks the campaign against the current `source_binding()`
fingerprint. For an eligible decision, its caller must supply `now` within the
decision's evaluation/validity interval. CLI planning regenerates decisions from
packet bytes and an explicit review using the supplied evaluation time and current
source. The caller must supply an honest current time; offline code does not turn
a caller-provided clock into provider evidence. Status is a saved-state report,
not a freshness re-evaluation.

The pure `completed` parameter remains a trusted internal injection point: it
checks task/campaign/source identity, terminal status and hash shape without
reading receipts. The CLI does not expose it. Actual resume uses
`Journal.completed(task_id)` and verified immutable sealed objects directly;
caller-supplied completion booleans cannot skip provider work. The generalized
journal binding reconstructs the exact reviewed configuration-boundary plan,
including arbitrary canonical UTC cuts, rather than passing it through the old
fixed-PST model planner.

`dendra-campaign-execution-1` binds the campaign manifest, exact source manifest,
inventory, reviewed packet/review/decision bundles, request policy and tasks.
Tasks are keyed by their logical campaign task ID; each journal task retains its
frozen identity, start/end and original native task/request. Observation lookup
uses this exact key, so two intervals for one stream stay separate.
`campaign_execution.prepare` accepts only a complete eligible selection, no
configuration gaps or continuation remainder, positive explicit budgets and at
most 128 tasks. It reconstructs this binding on journal open. A dry continuation
plan never silently becomes a larger execution descriptor.

## Request policy and provider failures

### Offline source-start review, sharding and refresh planning

`presentation.classify_starts` closes over the 337 resolved Percent/VWC streams
in the frozen inventory. The checksum-bound record-age audit is an index only.
It cannot create `REVIEWED_SOURCE_START`. The additive
`dendra-reviewed-first-observation-1` rule requires an explicitly trusted review
of original response bytes and a bound request/retrieval receipt: exact stream
and station identity, anonymous datapoints endpoint, ascending time, limit one,
no range filter, successful complete response, hash/byte count, retrieval time,
and actual returned numeric observation timestamp. Conflicting identity, missing
evidence, malformed/ambiguous results and disagreement with the audit retain a
HOLD. A complete empty response yields no start. Zero and negative observations
are values, not missing data or scientific acceptance. Receipt/review hashes are
integrity checks, not signatures or proof that a review was authorized. Synthetic
test receipts must never be used as real source authority.

`eligibility.dispatch_readiness` separately reports native metadata, temporal,
review, freshness and access states by reconstructing the existing decision from
its original trusted inputs. It does not create or rebind eligibility. A reviewed
start may remain `NOT_READY`. Older source-bound decisions are evidence of earlier
review only; a new collector fingerprint needs separately reviewed fresh state.
Missing configuration or scope authority cannot be replaced by guessed cadence.

`campaign.plan_shards` wraps the existing `make_campaign`, `plan` and execution
preparation functions. It plans completed fixed-PST days within
`INITIAL_PRESENTATION_10_WY`, clamped to the exact reviewed start including its
intraday component. It creates small independent campaign envelopes, at most
30 days each, split at authoritative configuration boundaries. Each envelope uses
the unchanged `dendra-native-task-1` schema and original task algorithm. These are
new campaigns with new identities, not aliases or migrations of older campaigns.
Their tasks equal the ordinary planner's tasks for the same campaign inputs.
Each shard hash binds source authority, campaign identity, ordered native task
IDs, execution policy and explicit planning time. Order is first interval start,
stream ID, final interval end; task order inside a shard remains the existing
planner order. Completion is not an input to packing. Verified seals are reused
within the original independent journal, without repacking later shards.

Planning may use an originally valid, now-expired review solely for historical
configuration/capacity analysis; dispatch readiness is evaluated at the explicit
planning as-of. The executor still rechecks current eligibility on preparation
and every dispatch. Missing, changed-source or incomplete authority produces
explicit planning HOLDs. No guessed task IDs are emitted. A stream whose horizon
cannot close has no executable prefix. Only fully ready shards enter the proposed
first wave; every output retains `network_execution_authorized=false`.

Capacity is measured through the real execution-binding and journal-page shapes
before journal creation. Architectural limits remain separate:

| Layer | Existing controls and applicability |
| --- | --- |
| Campaign planner / preparation | 4,096 total task/gap planning bound; preparation accepts at most 128 tasks with no remainder or configuration gaps; binding plus 4,096-byte margin fits 262,144 bytes |
| Campaign executor | Concurrency 1; spacing at least 1 second; zero retries/redirects; 3 pages/task; 2,016 rows/page; 25 seconds/request; 8 MiB/body |
| Journal | 4,096 event bound; 65,536-byte events; 262,144-byte header/plan pages; 128 entries/page; 8 MiB archive objects; durable reservation/start before dispatch; spent ambiguity is not refunded |
| Provider transport | Generic fetcher defaults and legacy adapter limits are not the active campaign policy; campaign explicitly uses one attempt and three pages |
| Legacy coverage collector | 80 attempts, 300 seconds, 64 MiB, 1,000,000 source rows, 16 new intervals; not used by this planner |
| Legacy pending storage | 4,096 files and 256 MiB; not a campaign journal or active shard limit |

The offline proposal adds conservative packaging bounds, not increased executor
limits: at most seven tasks per shard, a 600-second campaign budget and at most
128 shards in one review package. Configuration splits narrow a shard before the
seven-task limit. Exceeding the package bound preserves already planned pieces
and explicitly HOLDs the remainder; this API does not silently page, truncate or
authorize a larger campaign. A later larger planning package requires review of
that bound. Actual binding/header overflow or a single task that cannot fit
current controls HOLDs without increasing them.

Per-shard capacity distinguishes measured serialized binding/page sizes, hard
body/page/row limits, conservative state components and unknown runtime costs.
Three requests per task at their full 25-second deadlines is an upper request-time
allowance; the 600-second campaign budget also covers local work and can stop
earlier. Spacing-only time is a lower bound, not a runtime prediction. A 10-minute
cadence sensitivity is explicitly hypothetical, never generalized to the roster.
Incomplete pagination, object expansion or exhausted budgets do not become
covered empty. Current failure, durable reservation, seal and recovery rules
remain controlling.

`presentation.refresh_plan` adds planning contracts only:

- `ROUTINE_REFRESH`: seven-day overlap plus catch-up from a caller-supplied,
  verified **contiguous** complete query frontier. The newest observation or the
  maximum end of disconnected seals is not such a frontier. An older frontier
  exposes the entire missed interval.
- `CURRENT_WY_RECONCILIATION`: a separate deeper current-WY interval.
- `WY_CLOSE_RECONCILIATION`: a separate full completed-WY interval after close.

All intervals are half-open, clamped to reviewed source start and exclude the
incomplete current UTC−08:00 day. Only after complete valid refetch may retained
native data **inside exactly that interval** be replaced, including authoritative
empty results; this is not row-by-row newer-wins. Earlier history is untouched.
Recompute only affected completed fixed-PST days using unchanged accepted science.
Reconciliation frequency remains `UNSET`; a bounded recommendation is to review
current-WY reconciliation after the first routine refresh is measured, and review
WY-close reconciliation once after the year closes. No scheduler, dispatcher,
replacement writer, daily computation or publication is implemented here.

`dendra-native-request-policy-1` fixes concurrency 1, retries 0, at most three
pages per task, 2,016 requested rows per page, a 25-second total request deadline,
8 MiB per response, no redirects and at least one second between dispatches.
Explicit positive cumulative budgets bind logical requests, HTTP attempts, total
bytes and wall seconds. Journal limits additionally bind source rows to
`http_attempts * 2016`, selected interval count and 128 sessions. Elapsed accounting
survives process restarts; the CLI authorization window is at most 600 seconds
and cannot exceed the campaign wall budget.

The campaign adapter performs observation GETs only. Its reviewed metadata is
supplied locally; this collection path never refreshes metadata. Every page uses
the existing `Adapter.exchange` reservation/receipt machinery and `DendraFetcher`
with one attempt. It reserves and records started state before dispatch, charges
actual received bytes including rejected responses and an overflow sentinel,
and never refunds spent attempts on resume. A new root is not permission to
repeat uncertain work.

Current D3 behavior is serial, with 25-second request deadlines, 8 MiB response
ceilings, a bounded campaign window, at most two attempts per page and at most
15 seconds of retry delay within remaining budget. It uses bounded reads,
anonymous allowlisted GETs, no redirects and no inherited proxy/auth handlers.
Its journal circuit opens after two recent service failures (429 or 5xx); D3 can
retry the first 429. The campaign mode retains its separate zero-retry policy.

The campaign's stricter first-429 pause is enforced durably from receipt state;
reopening the process does not clear it. There is no automatic retry or cooldown
resume. A later recovery/retry decision requires its own bounded review. Live
transport uses the same anonymous no-proxy/no-redirect opener as D3. Offline tests
inject response and pacing functions into this same path and use no real sleeps.

| Condition | Enforced scope and action |
| --- | --- |
| First 429, 5xx, 408, transport failure or timeout | Campaign PAUSE; retain spent accounting; no further dispatch or automatic retry |
| 401, 403, 404 or 410 | Stream provider-access HOLD; later tasks for that stream cannot dispatch |
| Malformed selected page with known accounting | Task HOLD; an unrelated untouched task may proceed when global guards pass |
| Unknown envelope/row fields, unselected stream or restricted nested/scalar metadata | Campaign STOP for unreviewed schema/privacy/identity shape |
| Unknown received-row count or ambiguous attempt | Campaign PAUSE pending operator review |
| Oversized body or exhausted budget | Stop dispatch; charge received bytes and preserve evidence without refund |
| Incomplete/nonadvancing pagination | Task incomplete/HOLD; never seal or infer empty coverage |
| Local persistence or integrity failure | Campaign STOP; never convert into an HTTP retry |

Each request must be an anonymous GET to the exact datapoints origin/path, with
only the bound stream, inclusive start cursor, exclusive end, ascending sort and
limit. Extra parameters, duplicate query keys, bodies and arbitrary headers are
refused. A continuation cursor must equal the last timestamp of the preceding
full received page and advance. No reservation or dispatch can precede local
request and eligibility checks. The writer lock and main-thread requirement keep
provider concurrency independent of agents or compute worker counts.

## Durable receipt, archive and recovery

Use the existing exclusive campaign registry, single-writer lock, immutable
chained events/anchors, content-addressed objects and interval seals. Attempt
identity adds run, cursor and attempt ordinal to its task. Receipts associate
request specification, reservation, status, timing, original hash/size, safely
retained representation and completion/error classification. Objects and receipts
are never overwritten. The campaign index is derived state, not a second ledger.

Journal task states include `unqueried`, `in_progress`, `incomplete`,
`request_failed`, `held`, `complete_empty` and `complete_nonempty`; attempt state
separately records `reserved`, `started`, `received` or `failure`. A seal supplies
completion evidence. Query completion does not prove continuous observations or
accepted daily science.

| Interruption | Required recovery and new-attempt rule |
| --- | --- |
| 1. Before reservation | No attempt spent; may consume one first attempt only with current eligibility, authorization and remaining budget; a prior run event alone is not a reservation |
| 2. Reservation persisted, request not dispatched | Attempt remains spent; durable state cannot prove nondelivery; operator review, no automatic new attempt |
| 3. Request dispatched, response unknown | Preserve reserved/started ambiguity and pause campaign traffic; no automatic new attempt |
| 4. Response received, receipt not persisted | Response accounting may be unknown; preserve possible orphan page and pause; no automatic new attempt |
| 5. Receipt persisted, archive not persisted | Retain unsealed evidence; no coverage or automatic refetch; a crashed unsealed received page pauses further traffic |
| 6. Archive persisted, interval seal not persisted | Orphan envelope is evidence only; operator review, no automatic promotion or new attempt |
| 7. Interval sealed, summary not refreshed | Verify seal/object and derive summary; reuse complete-empty/nonempty result with no new request |

The current object writer persists page objects before received events and the
normalized envelope before its seal. There is no automatic promotion of orphan
objects. A torn chain, missing anchor or failed persistence remains read-only
evidence; there is no repair/refetch shortcut. Existing reservation counters
survive reopening. Every spent unsealed task refuses automatic replay, including
durably failed tasks. Fully accounted task-local failures may leave unrelated
tasks available, subject to campaign pause and access guards.

Changing source/profile requires a fresh compatible campaign identity and newly
bound reviews, never migration or rewritten historical bytes. `inspect_only`
opens known historical journal modes under a shared lock, verifies stored
manifest/plan/anchor/object integrity and reports source compatibility without
requiring the current collector fingerprint to equal the historical one. It
cannot reserve, append, store objects or dispatch. The CLI exposes this read-only
route separately from current-source offline manifest verification.

Store subdaily native pages, safe normalized envelopes and provenance outside
production Git. Index by campaign/task and station/stream/interval; compact
manifests reference immutable hashes, lengths and receipt/seal identities. Count
raw pages, envelopes, receipts, indexes and temporary headroom separately for
storage estimates. Optional later packing must preserve original hashes and
verified restore ability; it is not implemented here. Do not deploy storage.

Current local limits include 8 MiB objects, 262,144-byte journal headers/index
pages, at most 128 tasks in one prepared execution and 4,096 journal events.
The header carries reviewed evidence, so its byte limit can constrain a selection
before the task ceiling. Capacity is checked before registration. These limits
do not establish capacity for an entire historical backfill. Campaign
segmentation, retained global budgets,
compaction and verified recovery transfer need separate review.

## Native observation admission and daily science

Reuse the existing parser, strict observation shape and seal verification. Exact
stream association may be established by the bound request when rows omit the
stream ID; a supplied conflicting ID rejects the response. Require parseable UTC
timestamps without precision loss, ascending page order, requested half-open
bounds, effective limits and full receipt/page closure. Short or empty terminal
pages prove completion; full final pages or page exhaustion do not.

Preserve zeros and original native numeric values. Preserve null, missing and
invalid-value classifications separately. No guessed percent range, clipping,
conversion, cadence interpolation or backend transformation occurs here. Quality
and local-time fields remain provider claims, not accepted BRIM quality or UTC.

The current normalizer counts duplicate timestamps, deduplicates its derived
rows, retains conflicting value alternatives and flags quality conflicts. Exact
safe raw pages remain available. Conflicting duplicates do **not** currently
prevent a native archive seal; they must remain visible and cannot silently
qualify for downstream science. The standalone normalizer counts/skips
out-of-interval rows, while fetcher and seal reject them before accepted
completion. Do not describe those distinct boundaries as equivalent.

Raw archive admission, daily scientific acceptance and publication are separate.
The fixed-PST completed-day calculations in `scripts/dendra/core.R` remain the
daily numerical authority. No acquisition count, metadata PASS, native archive
seal or scale decision weakens its accepted requirements.

## Partial acquisition and first live batch

A failed task does not erase unrelated verified archives. Per-task failures keep
truthful task status; access/identity/configuration failures hold the affected
stream; budget, throttling, integrity, persistence and source-binding failures
stop the campaign. Product generation must explicitly exclude ineligible input.
This is partial acquisition, not authorization for partial publication or a
change to any existing cross-stream product transaction.

The first live batch requires separate explicit Dave authorization of exact
source, decisions, streams, intervals, root, window and budgets. Its inherited
four-task selection stays fixed:

| Stream | Exact half-open UTC interval | Readiness at the offline integration review |
| --- | --- | --- |
| Percent `63531a67a9b61453fa1ca4ed` | `2024-02-15T08:00:00.000Z` to `2024-03-01T08:00:00.000Z` | `NEEDS_FRESH_METADATA_REVIEW` |
| VWC `5d8e42e72da5c3cc53f6531d` | `2024-02-15T08:00:00.000Z` to `2024-03-01T08:00:00.000Z` | `NEEDS_FRESH_METADATA_REVIEW` |
| Dimensionless `5d9272a12da5c3cff0f655ed` | `2020-07-15T08:00:00.000Z` to `2020-07-16T08:00:00.000Z` | `HOLD_SCALE_ONLY_BUT_NATIVE_ELIGIBLE`, subject to the exact decision expiry |
| Same Dimensionless stream | `2020-07-16T08:00:00.000Z` to `2020-07-17T08:00:00.000Z` | `HOLD_SCALE_ONLY_BUT_NATIVE_ELIGIBLE`, subject to the exact decision expiry |

The accepted real Dimensionless packet retains two valid adjacent windows, both
interval claims absent, Soil / VolumetricWaterContent / Dimensionless, absent
attributes, unknown depth/orientation, unresolved scale and `unknown_history`.
Explicit reviewed acceptance permits native archive acquisition within scope;
normalized-percent eligibility stays HOLD. The integration result's task-local
readiness/decision artifacts carry exact review times and decision/source hashes.
This table grants no continuing permission after those decisions expire.

The table records the earlier integration review, before the separately
authorized baseline metadata review and first live batch. The accepted batch
sealed 2,160 Percent rows, 2,160 VWC rows, 52 native Dimensionless rows and one
covered-empty Dimensionless interval. It consumed six observation attempts with
no retries. Its historical seals remain immutable. Fresh source-bound decisions
and explicit authorization are required before any later acquisition; do not
reuse expired decisions or expand those four tasks implicitly.

The task-local first-batch plan must state selected IDs, exact intervals, task
count, metadata prerequisites, logical/attempt/page limits, concurrency one,
zero retries, byte/time ceilings, first-429 PAUSE, expected receipts/objects/seals
and acceptance criteria. Neither that plan nor representative membership claims
that all 434 streams are currently eligible. Do not execute it in this gate.

## Maintainer CLI and callable contract

The maintainer entry module is `dendra.history_acquisition.campaign_cli`, invoked
from the checkout with `PYTHONPATH=scripts python3 -B -m dendra.history_acquisition.campaign_cli`.
The execution version is `dendra-campaign-execution-1`; campaign/task/request
versions remain `dendra-native-campaign-1`, `dendra-native-task-1` and
`dendra-native-request-policy-1`. Required modes are `plan`, `status`, `collect`,
`resume` and `verify`. All require an explicit `--inventory` and
`--inventory-sha256`; only the frozen inventory hash
`f81b91bd86ade8e5380063e1ce3b37d1dddb1ca5d21368cd3ecb4514688190d9` is accepted.

| Mode | Additional required inputs and behavior |
| --- | --- |
| `plan` | `--config`, `--state-root`, `--now`, `--dry-run`; reconstructs decisions and writes immutable offline artifacts |
| `status`, `verify` for a saved offline manifest | `--manifest`; verifies current-source manifest integrity; status also prints planning partitions |
| `status`, `verify` for durable journal state | `--state-root`, `--campaign-id`; verifies stored archive state with no source migration or provider activity, including older source bindings |
| `collect` | `--execution`, `--authorization`, `--state-root`, and all four explicit budget flags below; exclusively registers new state, then runs exact prepared tasks |
| `resume` | Same execution/authorization/state/budget arguments as collect; opens existing compatible state, reuses sealed tasks and refuses replay of spent unsealed tasks |
| `prepare-product` | `--sealed-root`, `--sealed-manifest-sha256`, `--acquisition-fingerprint`, `--output-root`, `--now`, `--cadence-mode initialize`; verify immutable evidence and initialize local daily output using installed R, with zero provider access |

Input files use absolute paths and the task-owned state root must already exist.
`plan` optionally accepts repeated `--stream` or `--station`, `--max-tasks`
(default 128) and `--after-task`. These select or paginate an offline plan; they
are not collection/resume controls. A continuation plan, blocked selection,
configuration gap, remainder or zero budget cannot yield an execution descriptor.

The legacy planning config contains exactly `campaign_id`, `horizons`, `chunk_days`,
`budgets` and `reviews`. Alternatively, replace `horizons` with `presentation`,
containing exactly `mode`, `as_of` and `source_starts` as described below. The two
forms are mutually exclusive. Horizons map stream IDs to explicit start/end values;
chunk days range from 1 to 30. Budgets must equal the complete policy object from
`campaign.policy`. Every review reference contains exactly `packet_path`,
`review_path` and `review_sha256`. Packet/review inputs are bounded at 65,536
bytes; generic planning and execution files are bounded at 2 MiB.

Plan exclusively writes `manifest.json`, `plan.json` and `status.json` under
`plans/<campaign-id>/<plan-sha256>/` in the state root. It also writes
`execution.json`, containing exactly `binding` and `tasks`, only when the complete
selected plan passes preparation. These are offline artifacts, not campaign
registration, spending authority or a separate ledger. Partial writes are
preserved; subsequent commands never overwrite them. The printed plan result
contains `outcome`, `plan_path`, task/blocked-stream counts, `provider_requests=0`
and `execution_ready=false`.

Collection and resume require all four positive flags:

- `--logical-requests`
- `--http-attempts`
- `--total-bytes`
- `--wall-seconds`

Their values must equal the immutable execution policy exactly. They cannot
increase budget or reset counters. Execution rejects `--dry-run`, `--config`,
`--manifest`, stream/station selectors, `--after-task` and `--campaign-id`.
Live execution also rejects an injected `--now`: it uses real UTC/monotonic
clocks. The Python `main` dependency arguments permit finite synthetic transport,
pacing and clocks for offline tests, using the same CLI/journal/adapter path.

The separate authorization file is bounded at 16,384 bytes and has exactly:

| Field | Required meaning |
| --- | --- |
| `schema_version` | `dendra-campaign-dispatch-1` |
| `approval_reference` | Actual separately granted maintainer approval, 1–128 characters |
| `binding_sha256` | Hash of the exact reviewed execution binding |
| `task_root` | Existing absolute state root, matched to its opened device/inode |
| `window_start`, `window_end` | Current UTC inside a positive window no longer than 600 seconds or the campaign wall budget |

The file records trusted approval; creating it does not grant that approval.
All local bindings, budget values and fresh unfinished-task decisions are checked
before the live opener is constructed. Root traversal rejects symlinks. There is
no environment switch, implicit authorization, metadata refresh, scheduler or
unbounded backfill entry point.

Successful collect/resume prints `outcome`, exact-key `results`, a journal `status`
summary, `receipt_prefix` and `synthetic_transport`. Results contain cache-hit and
native-envelope data or a task HOLD reason. The receipt prefix is
`campaigns/<campaign-id>` under the supplied state root. Durable locations are:

- `registry/<campaign-id>.json`: immutable registration.
- `campaigns/<campaign-id>/manifest.json`, `plan/`, `writer.lock`: bound plan and lock.
- `campaigns/<campaign-id>/events/`: paged immutable receipts.
- `anchors/<campaign-id>/`: independent event/reservation anchors.
- `campaigns/<campaign-id>/objects/<sha256>.bin`: screened pages and sealed native envelopes.

On an execution exception, printed `dispatch_count=consult_durable_receipts`
avoids claiming zero requests after work may have started. Existing receipts and
seals remain authoritative even when no successful command summary was printed.

| Exit | Meaning |
| --- | --- |
| 0 | Successful dry plan, inspection/verification, offline preparation or all selected execution tasks sealed/reused; never itself a publication approval |
| 2 | HOLD or partial task result, expired/missing eligibility, budget/permission refusal, or argument-parser failure |
| 3 | STOP for source/state incompatibility, missing explicit preparation inputs, unreviewed global schema or fatal input/persistence failure |

Saved offline-manifest inspection reports
`saved_offline_manifest_integrity_not_current_access_or_archive_verification`.
Journal inspection reports `hash_checked_archive_state_not_current_access_permission`
and `source_compatible`; neither route refreshes access or grants execution.
Inspection remains read-only. Damaged state requires review, not repair or retry.

This is the bounded callable surface for a later thin maintainer launcher. No
00G repository or R launcher is changed by this integration. Publishing, schedules,
storage deployment and long-campaign segmentation remain separate gates. Every
later live collection requires fresh reviewed eligibility for every intended
task and explicit Dave approval. `00G_LAUNCHER_CONTRACT=UPDATED_RELAY_REQUIRED`
for the newly supported offline preparation and optional presentation config;
existing collect/resume arguments and budgets retain their meanings.

## Offline sealed daily initialization

`prepare-product` implements `dendra-sealed-daily-handoff-1`. The input root must
carry `evidence-manifest.json`, `execution-binding.json`, `source-binding.json`
and every immutable journal, anchor, raw page and parsed object used by its seals.
The caller supplies a trusted manifest SHA-256 and the original acquisition
fingerprint. The adapter checks original review/decision/task identities and
receipt-time eligibility, page closure, hashes, bounds and normalized raw/parsed
agreement. It never resumes or rewrites that older journal. The output separately
binds the current collector and exact R core/wrapper hashes.

The output root must be absolute and absent under an existing parent. Inputs are
verified before creating it. It contains `handoff.json`, `native/<stream>.csv`,
`lineage/<stream>.json`, `daily-output.json`, an R receipt and `result.json`.
Failures preserve partial scratch for diagnosis; there is no overwrite, retry,
publication or automatic promotion. The CSV columns are exactly `t`,
`datastream_id`, `v`, `value_status`, `duplicate_conflict`,
`alternative_out_of_range`; full native rows, interval/configuration identities,
receipts and seal/content hashes survive in lineage sidecars. Normalized archive
row counts are distinct from original raw-page duplicate counts.

`--cadence-mode initialize` is mandatory. This creates and records a fresh frozen
initialization context using accepted `core.R` rules; it is not an update, resume
or migration of an existing daily state. Such an update must retain its original
context through the existing accepted state interface. The wrapper does not
re-estimate an existing context. Observed-day precedence, count/span/cadence QC,
no interpolation, fixed-PST days, ending-year WY and leap alignment stay in R.
Daily rows retain numerical eligibility separately from complete-day query
coverage. Unqueried gaps never become observed zeros. Dimensionless intervals
retain native rows and `COVERED_EMPTY` status without percent daily products.

The additive `dendra-soil-point-semantics-1` preparation records only
`historical_terminal` samples and an empty `latest_instantaneous` collection.
Each terminal sample retains its source time/day, age, native value and accepted
scale result; it carries no latest witness, current-state claim or publication
eligibility. The proposed future marker is separate from `completed_daily` and
requires its own current/latest evidence and consumer review. No such request or
public marker implementation is included here.

## Explicit initial presentation horizon

`dendra-presentation-horizon-1` supports `INITIAL_PRESENTATION_10_WY` and separate
`FULL_POR` modes. Omission preserves the legacy explicit-horizon manifest shape;
no old campaign or journal is migrated. Both modes end at the start of the current
fixed-PST civil day (08:00 UTC), excluding the incomplete day. The initial mode
starts no earlier than October 1 of `current ending-year WY - 10`, so it includes
at most the current WY and preceding nine. Calendar boundaries retain leap days;
this is not a 3,650-day duration. Full POR has no ten-WY floor.
Planning requires an explicit evaluation time at or after the descriptor's
`as_of`; a future presentation cutoff cannot authorize future queries.

Every selected stream supplies an exact frozen station/stream/inventory-bound
source-start record. `REVIEWED_SOURCE_START` includes `start`, `evidence_sha256`,
`reviewer_ref` and `reviewed_at`; its explicit trusted review permits planning
from the later of that exact instant and the mode floor. An intraday start is
retained, and ordinary R QC decides whether that partial source day qualifies.
`AUDIT_ESTIMATED_START` includes a timestamp and evidence hash for estimates only.
`UNKNOWN_SOURCE_START` keeps start null. Both produce non-executable holds, even
when another stream is ready. Hashes bind evidence; they do not authenticate a
review or grant provider permission. A later bounded discovery/review gate can
supply these records without changing this interface.

The planner claims no gap-free coverage or active tail, and never pads short
records to ten years. Existing configuration cuts, at-most-30-day chunks,
eligibility, source bindings, budgets and resume rules still apply. A complete
434-stream campaign exceeds current bounded execution capacity; smaller reviewed
selections and a separately reviewed capacity strategy are still required.
This mode neither computes nor activates reference bands, percentiles or a
climatology. Later Dendra reference science requires a separate review of daily
support and provenance; SCAN science is not imported.

## Journal-backed first-observation authority witness

`authority_witness.prepare` creates `dendra-first-witness-journal-1` for one or
both exact frozen targets already listed in `d3_plan.IDENTITIES`. Its distinct
`dendra-first-witness-request-1` request is an anonymous `GET /v2/datapoints`
with exactly `datastream_id`, `$sort[time]=1` and `$limit=1`, using the existing
query encoding and endpoint/header allowlist. It has no interval bounds or
latest/current semantics. The journal has no history tasks; native logical
history task IDs, campaign limits and coverage states are unchanged.

Preparation requires admitted current-source metadata packets, including the
unchanged identity, scientific, configuration, public-access and 24-hour
freshness checks. Dispatch rechecks freshness. `WitnessAdapter.run` requires a
separate explicit approval reference, exact binding hash and at-most-60-second
window, with injected executor/wait callables. Future authorized live callers
use the existing `anonymous_executor` and `anonymous_wait`; offline tests inject
synthetic responses and clocks through this same adapter. There is no automatic
live entry, background runner or permission inferred from a packet or hash.

The existing `Journal`, `Adapter.exchange`, transport deadline and object store
persist reservation before start/dispatch and immutable original receipt bytes.
Policy is serial, zero retries/redirects, at least one second between request
starts, at most 25 seconds and 8 MiB per response, and one request per selected
stream. Reserved, started, failed and ambiguous attempts remain spent. Reopening
state never silently dispatches again. Rejected/private response bodies are
omitted while their byte/hash accounting remains. Successful first-selection
completion is not a history interval seal or `COVERED_EMPTY` history coverage.

`authority_witness.evidence` rechecks the binding, event/anchor chain, exact
request identity, source fingerprint, reserve/start/receipt identities and
original object hash before returning `dendra-journal-first-evidence-1`. It
binds returned timestamp/result, retrieval time and
`dendra-journal-first-review-1`. `presentation.review_journal_first` requires an
explicit reviewer decision bound to that evidence and passes the original bytes
to the existing `review_first` rule (including its stricter 1 MiB review ceiling).
`classify_starts` accepts `{journal, review}` for this path and retains its audit
contradiction checks. Checksums establish integrity, not reviewer authentication.

An exact nonempty witness may become `REVIEWED_SOURCE_START`; a complete empty
witness has unknown source start. Missing, malformed, mismatched or ambiguous
evidence fails closed. `record_age_audit.json` remains planning-only.
For the exact ascending-first, limit-one request, exactly one otherwise-valid
selected row may omit `total`. If present, `total` must be an integer covering
the returned rows. Zero rows without `total` remain HOLD; complete empty evidence
still requires explicit `total=0`. More than one row is invalid. The shared
`authority_witness.total_complete` predicate applies both at response admission
and source-start review; request, source, timestamp and provenance checks remain
mandatory. Previously rejected bodies are not reconstructed or promoted.
`SOURCE_START_AUTHORITY` never implies `DISPATCH_READINESS`; native-history
eligibility, metadata/configuration freshness and separate execution approval
remain required. No daily science, browser contract or publication changes.
This addition changes the collector fingerprint: older journals remain immutable
and source-bound, and a later approved witness campaign needs fresh state and
metadata packets bound to its reviewed checkpoint. This offline gate makes no
provider requests and grants no live-refresh authority.

## Separate history-page diagnostic

`history_diagnostic.py` adds `dendra-history-page-diagnostic-1`, a local,
explicitly authorized diagnostic interface. It does not change observation
admission or `dendra-witness-diagnostic-1`. `prepare` reads an intact original
failed first-page Journal in inspection mode and binds its task, campaign,
interval, request, spent receipt chain and body hash. A caller-verified commit
identity and the actual current collector fingerprint bind the new diagnostic.
The deterministic diagnostic namespace has no acquisition tasks and a separate
one-attempt budget. Existing acquisition attempts remain spent and immutable.

`HistoryDiagnosticAdapter` uses the existing anonymous request construction,
transport and Journal reservation/start/receipt path. A separate approval must
bind the diagnostic, incident and checkpoint within a <=60-second window. The
runner waits at least one second before its sole first-page request, uses the
saved ascending interval request with limit 2,016, and preserves the 25-second
deadline and 8 MiB body ceiling. Retries, redirects and pagination are forbidden.
Callers must serialize execution, verify the checkpoint and original incident,
and preserve the unique diagnostic state across invocations; a new process/root
or source revision does not authorize a second request or reset any budget.

Only a canonical, validated structural projection <=12,288 bytes is retained.
It distinguishes extra envelope fields, nonobject rows, extra row fields,
explicit stream mismatch, nested values and oversized strings, including a
first offending row late in a full page. It retains fixed guard/site codes,
counts, allowlisted field-slot presence/types and match/bound flags, plus
authorized request/source/incident identifiers and response hash/bytes. Unknown
field names, returned timestamps/IDs/values, coordinates, nested content,
headers and exception text are omitted. Journal persistence recomputes the
projection against the body and binding and checks the receipt classification.
For `row.nested` at the first offending `q` slot only, when `q` is an object,
an additive `q_structure` summarizes the immediate structure. Its fixed slots
are `attrib`, `flag`, `annotation_ids` and `annotationIds`; aliases are never
coalesced. Each slot contains presence and exact JSON type (including missing,
null, integer and number). Arrays/objects additionally contain `count`,
`count_truncated` and `child_type_counts` for the seven JSON types. Counts and
each type histogram bucket independently saturate at 256; a truncated histogram
can sum above the capped count. No array prefixes or element values are emitted.
The object summary also includes capped `total_q_member_count` and
`unknown_q_key_count`, their `_truncated` booleans, and
`both_annotation_aliases_present`. Traversal is one level only: no nested keys,
quality values, IDs, text or unknown key names are copied or interpreted.
The existing 12,288-byte diagnostic ceiling and version remain unchanged.
Historical projections without this optional summary remain schema-readable;
new persistence still requires exact recomputation from the current body.
This extension does not relax observation admission, preserve raw rejected
quality objects, establish quality science or authorize another provider call.
It changes the collector fingerprint; old authority and journals remain bound
to their original source, and later live diagnostics require separately reviewed
current-source readiness and explicit request authorization.
Transport, HTTP, body-limit and unprojectable failures retain omission/accounting
behavior; no original diagnostic response is retained, even if its shape passes.

The diagnostic cannot start an acquisition interval, seal an archive, promote
eligibility/source-start authority, or advance another task. Generic rejected
page classification remains `parse_or_privacy`, paired privacy/identity HOLD,
and zero retries. `PASSED_SHAPE_ONLY` grants no acquisition or scientific
authority. Review of a future response cannot establish which field was present
in a different, previously omitted response. Preparation/tests grant no live
permission; separately reviewed checkpointing and one-request authorization are
required before a live invocation. Old journals are never migrated or rehashed.

## First-witness rejection diagnostics

`witness_diagnostic.py` projects parser rejections into
`dendra-witness-diagnostic-1` through the existing receipt/object path. Admission
still belongs to `observation_shape` and `authority_witness.response_shape`.
The diagnostic binds the exact planned witness request, station/stream, request
ID, source-bound request hash, HTTP status and complete response byte count/hash.
It records normalized rejection codes and allowlisted parser locations, fixed
envelope/row field names and types, row count, timestamp presence/type and
parseability, identity match status, and completeness/selection check results.
These shape checks explain failures; they cannot admit a response or prove order.

Only explicit integer pagination controls (`limit`, `skip`, `total`, `offset`,
`count`) within ±(2^53−1) retain values. Offset/count remain unsupported envelope
fields under the unchanged parser. Larger or noninteger controls retain types,
not values. At most six envelope fields and seven fields in each of the first
two rows are described; unknown names are omitted and counted. No nested values,
observation values, timestamps, returned identifiers, credentials, headers or
coordinates are copied. The complete artifact is capped at 12,288 bytes and
has a deterministic hash. Missing total rejects empty results but is compatible
with one otherwise-valid row. Literal total presence/type checks remain false
when absent; the combined completeness result uses the shared optional-total
rule without changing diagnostic version 1. Nonzero skip, wrong limit, ambiguous
empty results and malformed rows remain rejected.

Journal persistence checks the diagnostic against the actual response and bound
request, permits it only on a rejected HTTP-200 witness receipt, and marks the
object `representation=sanitized`. The legacy `body_retained` flag means an
object was stored; it does not imply retention of the rejected original body.
Original response hashes/bytes and spent attempts remain accounted. Accepted
witnesses still require `representation=original` and the original object hash.
Reopening a diagnostic grants no authority, retry, refund or source-start
promotion. Unknown row accounting and the whole-witness-journal stop guard remain
unchanged. Transport/HTTP/body-limit failures retain their existing omitted-body
behavior. No metadata parser, history observation path or public contract changes.

The historical Deep Canyon failure has no retained raw body; this addition cannot
reconstruct or promote it. Any future live diagnostic requires new authorization
and fresh state bound to the new collector fingerprint.

## Current-source temporal metadata acquisition

`metadata_acquisition.prepare` creates `dendra-temporal-metadata-acquisition-1`
in the existing `Journal`, with no history interval tasks. The explicit selected
set contains one or both frozen targets in `d3_plan.IDENTITIES`. Preparation
binds the unchanged accepted unit authority, complete frozen inventory closure,
actual collector source hashes, existing campaign temporal profile and exact
request descriptors. There is no fingerprint override or old-packet rebinding.

`MetadataAdapter` uses `Adapter.exchange`, its existing request allowlist,
durable reservation/start/receipt/object storage, total deadline, privacy-safe
diagnostics and anonymous transport. It requests the unit vocabulary once,
then the exact station and first datastream page for each selected target.
The vocabulary uses `parse_vocabulary`, stations use `parse_station`, and lists
use the existing generalized `review_packet` with
`dendra-soil-campaign-temporal-config-review-1`. Historical D3 parsing remains
unchanged. No duplicate parser, direct HTTP client, interval workaround or
automatic pagination is introduced.

The metadata phase permits exactly three or five planned request slots, one
attempt each, zero retries/redirects, concurrency one, at least one second
between starts, at most 25 seconds and 8 MiB per response, and a maximum
150-second execution window. The caller must supply a separate explicit approval
reference, exact binding hash, window and executor/wait callables. Future live
callers use the existing `anonymous_executor` and `anonymous_wait`; synthetic
tests exercise the same path. There is no CLI default, implicit permission or
automatic follow-on. When composing separately approved metadata and witness
phases, the caller must preserve the overall request budget and start spacing
across their journals; creating another journal does not reset those limits.

Vocabulary admission precedes station requests. A durable admitted station
receipt precedes its list request. Unknown response accounting, ambiguous spent
attempts, transport/deadline/resource failures and throttling stop traffic.
An accounted stream-local admission HOLD or nonretryable access refusal may
leave the other target available under the unchanged shared accounting guards.
Skipped slots authorize no substitution. Reopening a journal cannot dispatch
again; reading valid saved evidence appends no records and renews no timestamps.

Only sanitized metadata or bounded diagnostics are retained, along with the
original response hash/byte count. `packet_evidence` verifies journal header,
event/anchor closure, canonical request, reservation/start/receipt identities,
sanitized object hashes, packet/source/identity binding, response provenance,
station-to-list binding, check/retrieval times and freshness. The sidecar is
`dendra-journal-temporal-metadata-evidence-1`; its embedded packet retains the
existing `dendra-soil-campaign-temporal-metadata-review-1` schema unchanged.
Current configuration claims retain exact configuration hashes, temporal
boundaries, omissions and `unknown_history`; they assert no historical continuity.
Protected geometry and optional native Z follow the unchanged station parser.

The unchanged packet may enter authority-witness preparation and explicit native
eligibility review. Metadata admission grants neither `REVIEWED_SOURCE_START`
nor `DISPATCH_READY`. A separate exact witness review and current explicit
native-eligibility decision remain necessary; missing, stale or incomplete
evidence stays held. Old-source journals remain readable through `inspect_only`
and `read_object` under their original identity; the new current-source evidence
path rejects incompatible source fingerprints. No history task IDs, campaign
limits, witness rules, R science or browser contracts change. No live refresh,
history acquisition or publication is authorized by this offline implementation.

## Local native quality quarantine

`observation_quality.py` owns `dendra-observation-quality-1`. New reviewed
campaign executions use `dendra-campaign-execution-2` and bind the exact quality
policy plus its SHA-256 separately from logical task identity. The task-ID
schema, campaign limits, metadata and source-start admission remain unchanged.
The collector fingerprint changes with source bytes: prior campaigns and
journals retain their original identities and cannot be silently resumed,
rebound or migrated. Historical native seals remain readable under their
original normalization rules.

Version 1 retains only a closed native `q` representation: absent, null,
bounded JSON primitives, or an object with only `attrib`, `flag`,
`annotation_ids` and `annotationIds`. The aliases remain separate. `attrib`
may contain a primitive or an array of primitives; the other three slots may
contain null or arrays of strings. Root arrays and nested objects/arrays are
unsupported. Arrays have at most eight items, strings at most 256 characters
with no Unicode control/format characters, and finite numbers have magnitude
at most 1e308. Canonical `q` occupies at most 4,096 UTF-8 bytes. No truncation,
coercion, unknown-key deletion or interpretation of flags/annotations occurs.
Unsupported, unsafe or oversized quality is a hard schema HOLD that stops
campaign traffic through the existing persisted receipt guard, also on restart.

Absent quality is `NO_PROVIDER_QUALITY_CLAIM`; explicit null is
`EXPLICIT_NULL`. Neither vetoes science by itself. Empty string and empty object
also have no quality veto. Every other supported non-null claim is quarantined,
including false, numeric zero, or a nonempty object whose members are empty
arrays/nulls. These are conservative eligibility decisions, not provider flag
meaning or statements that an unflagged observation is scientifically valid.

Exact quality stays only in immutable local native pages/archives. Compact
classification records contain fixed codes, types, counts and hashes. Each
timestamp group retains presence/type-sensitive alternative hashes and source
occurrence indices into concatenated original pages in sealed receipt order.
The task, page, receipt and object hashes resolve those indices back to exact
native quality, including alternatives not selected as the first normalized
row. Missing/null, boolean/number and distinct aliases remain distinct. Any
quarantined occurrence vetoes the entire timestamp group; value-conflict
semantics remain independent. Derived preparation lineage drops direct `q`
values and keeps these references. Logs, daily CSV and browser products never
copy exact quality values.

No row is filtered before limit/cursor/pagination checks or sealing. A sealed
nonempty query records `QUERY_COMPLETE` independently of
`OBSERVATIONS_QUARANTINED`; even an all-quarantined query remains nonempty.
`COVERED_EMPTY` still requires a genuinely empty completed query. Failed,
incomplete, unqueried and inactive states are not converted into coverage.
Timestamp, stream, access, configuration, accounting and provenance failures
remain hard refusals. Retry, spent-attempt and full-third-page rules are
unchanged.

The private `dendra-sealed-daily-handoff-2` derives day disposition from verified
native evidence. A completed fixed-PST day `[08:00Z, next 08:00Z)` intersecting
quarantine is `DAILY_VALUE_WITHHELD`. An incompletely queried affected day
remains `QUERY_INCOMPLETE`. No observation on an affected day is supplied to
`core.R` numerical aggregation. Its result has no accepted numeric mean and
retains a separate `provider_quality_unreviewed` reason, never a fabricated
zero, missing measurement or duplicate conflict. Real source timestamps remain
available to unchanged cadence calculations and neighboring-day cadence state.
Original missing/null/invalid and duplicate counts remain distinguishable.
Full-day query coverage is still checked across adjacent task seals; query
completion does not assert continuous valid observations.

`core.R` is unchanged. Historical version-1 preparations remain readable, and
new preparations may conservatively classify original native evidence without
rewriting its seals. Private browser-input validation accepts handoff version 2
and rejects numeric leakage from withheld days or affected historical terminal
points. Public browser schemas, paths and rendering contracts remain unchanged.
This implementation authorizes no publication, daily production or live retry.
Task 37's original spent attempt and unsealed state are preserved; any future
resume/accounting decision and current-source authority require separate review.

### Explicit task-37 cross-source recovery (local only)

`recovery.py` owns `dendra-task37-recovery-1`, a single approved incident
transition using the existing `Journal` and `CampaignAdapter`. It is not a
generic retry, source override, campaign migration, or suffix planner. The
approved predecessor task/header and final anchor are fixed pins. Preparation
opens its original Journal read-only, validates its original task and native
decision, recomputes its receipt/anchor chain and charges, and checks the
separately pinned historical authorization. The omitted failed body remains
omitted; it supplies no observations, cursor, or query coverage.

The predecessor task/campaign identity remains historical. A distinct recovery
slot and execution key explicitly link that identity without repacking it.
The retained `native_task` member supplies only the predecessor request and
configuration ordinal; the Journal dispatch key is the distinct recovery key.
The slot is stable across attempted changes to source, authority or window.
The new binding separately records current HEAD, collector sources, campaign
execution version, `dendra-observation-quality-1`, frozen interval/identity,
configuration hashes, current native-review bundle and an explicitly accepted
Journal-backed source-start review. Current metadata is not an identity rewrite.
The complete configuration hash and the exact covering configuration window
must match the predecessor. Source-start review does not substitute for native
eligibility, access or temporal freshness checks.

`prepare(...)` requires explicit predecessor and witness references, reviewed
native authority and a recovery authorization containing a checkpoint, one
absolute private output root, approval reference, start and deadline. It writes
nothing. A future authorized caller may create the ordinary Journal from that
binding, then use `CampaignAdapter.run`; no new client or acquisition CLI is
added. Every reservation/dispatch path retains the current source, authority,
privacy, configuration and request checks. Real dispatch is separately gated.

The recovery authorization is a new immutable window of at most 600 seconds;
the old expired window is retained verbatim. The real window must be supplied
only by the later approved live invocation. Reopening the same binding cannot
change the root or deadline, and the stable Journal registration rejects a
replacement binding. A caller must reuse the approved root/binding; preparing
another root or approval is not a permitted budget reset. Trusted approval and
root preservation remain operator responsibilities, as for ordinary campaigns;
hashes are integrity checks, not signatures or protection from deliberate
deletion of all evidence.

The Journal is the sole cumulative ledger. Its snapshot starts with the
immutable predecessor's one attempt/logical request, 177,290 bytes and 2,016
source rows, then adds durable recovery events. Limits are four cumulative
attempts/requests, 33,554,432 cumulative bytes and 8,064 cumulative source rows.
At most three new requests/pages remain. Each request still has a 2,016-row
limit, 8 MiB body ceiling, 25-second deadline, serial execution, at least one
second between request starts, zero retries and zero redirects. The three-body
ceiling also remains binding even when cumulative bytes have unused capacity.
Unknown accounting, failed or ambiguous new attempts remain spent and require
review; restart does not silently replay even a received but unsealed page.

Recovery begins at a new page 1 at the original interval start. Only complete,
admitted, full pages with advancing last timestamps can supply page-2/page-3
cursors. A short page may seal; a full third page holds. There is no fourth new
page. Successful seals are reusable with zero requests. No task-38 state or
other interval can be placed in this recovery binding.

Recovered seals distinguish `PREDECESSOR_FAILED_ATTEMPT`, its immutable
receipt/failure/anchor hashes, original authorization and charges, from
`RECOVERY_ADMITTED_PAGES`, the new authorization and successful receipt keys.
`QUERY_COMPLETE` is established entirely by the new sequence. The seal also
records the quality disposition (including `OBSERVATIONS_QUARANTINED`) and
cumulative charges. The failed predecessor is never required to masquerade as
a successful retained page. Daily handoff verifies this lineage and consumes
only new admitted pages, retaining predecessor references/charges in private
lineage. Supported quality quarantine, pagination of all rows and fixed-PST
day withholding remain unchanged; all-quarantined nonempty recovery cannot
become covered-empty. Unknown unsafe quality remains a hard HOLD.

Ordinary historical non-recovery bindings/seals retain their original reader.
Recovery verification requires its pinned predecessor and witness evidence to
remain available; it never rewrites those roots. No public schema, `core.R`,
publication, authority acceptance, history execution or automatic continuation
is authorized by this implementation.

## Offline multi-Journal full-history preparation

`sealed_history.prepare_product` adds private `dendra-multi-journal-seal-set-1`
inputs and `dendra-sealed-daily-handoff-3` output. The caller supplies an absolute
seal-manifest path, its trusted SHA-256, the frozen inventory, an explicit as-of
time and a fresh output root. This is an offline Python API; it grants no provider,
authority-refresh, dispatch or publication permission. Existing single-Journal
CLI preparations and handoff versions 1/2 remain readable.

The manifest names exact ordered stream horizons and original Journal roots,
campaign/task IDs, acquisition fingerprints, header/seal/archive hashes and
half-open intervals. Inputs are capped at 8 MiB, 512 seals, 32 streams, 128 seals
per stream and one million normalized observations across the set. These are
private preparation bounds, not increased acquisition or browser limits. Within
each declared horizon, out-of-order, duplicate, overlapping or missing intervals
refuse preparation. A shorter scope must be explicitly supplied and pinned; no
missing interval is silently treated as coverage.

Each original Journal is opened for inspection only. Existing binding, historical
receipt-time review, object, pagination, normalization and seal checks run before
aggregation. Recovery inspection verifies the original source/approval and
predecessor/witness lineage without rebuilding it against today's collector.
Writable recovery/witness preparation and authorization still require current
source/checkpoint bindings. Inspection cannot refund, resume, promote authority
or rewrite events. A failed predecessor never contributes successful rows.

Per-interval review scope and configuration window must cover the exact task.
Frozen station/stream/unit/orientation/depth, scientific and configuration
identities, accepted scale semantics, privacy and historical access checks remain
strict. Scale reviews may differ only in their derived decision hash, approved
scope and segment bounds; every contributing segment must retain the same
accepted conversion. The original reviews stay immutable. Conflicting scientific
or configuration assertions hold; no new historical applicability is inferred.

The compact private `dendra-compact-sealed-lineage-1` index replaces embedded
native/receipt rows for version 3. It binds the seal-set hash, current preparation
fingerprint, quality policy, exact frozen identity, native CSV and normalized-row
hashes, original per-seal/task/campaign/source/header/archive/receipt/review/scale/
configuration hashes, counts, retrieval bounds and quarantine-day disposition.
It includes neither filesystem locations nor original quality values, annotation
IDs, raw bodies or receipts. Source locations remain solely in the caller-pinned
private seal set. `verify_preparation_sources` independently reconstructs the
index from original evidence; hashes alone are not authenticity claims. The
compact index is a derived audit index, never a replacement archive.

The shared handoff still supplies the same six-column CSV and calls unchanged
`core.R` through `daily_prepare.R`. Version 3 uses the existing fixed-PST complete
day, arithmetic mean, cadence/QC, ending WY, true DOWY, leap plotting and ×1/×100
rules. Quality withholding is aggregated across all intervals; partial boundary
days remain query-incomplete. Covered-empty, quarantined, failed and unqueried
states cannot create zero/fill values. The R result is hash-bound by its receipt
summary; all version-3 browser inputs retain exact caller pins.

Producer-side browser preparation validates the compact schema, hashes, seal
closure, interval/configuration bounds, counts, quarantine and scale identity
before projecting accepted completed rows. Every input file remains capped at
8 MiB. The public `brim-soil-history-1` / `dendra-history-profile-1` schema,
version constants, descriptor paths, hover/capability semantics and activation
behavior are unchanged. No current/latest witness, reference band, official
output path or deployment is introduced.

## Private latest-evidence readiness

`latest_observation.py` owns `dendra-private-latest-evidence-1`. This is an
explicit-input private Python route, not a scheduler or a public delivery.
`prepare` requires an exact frozen station/stream, current checkpoint and source
fingerprint, admitted temporal metadata, explicitly accepted native/scale review,
and the original Journal-backed first-witness review. It reuses the existing
review validators; it neither creates acceptance nor infers authority from the
record-age audit. The reviewed native scope and one unambiguous configuration
window must cover each returned timestamp. Unknown historical applicability
remains unknown. Old-source reviews are never silently rebound.

One caller-authorized window of at most 300 seconds is bound to one absolute
private task root and one campaign. The existing Journal registry, reservation,
start, receipt and object path provide immutable accounting. `LatestAdapter`
uses the shared Adapter and anonymous transport; it issues only the exact
`datastream_id`, descending `$sort[time]=-1`, `$limit=2` datapoints request.
No interval, skip, subsequent page, retry, redirect or alternate stream is
permitted. Concurrency is one; every request is at most 25 seconds and 8 MiB.
The caller supplies the preceding request-start timestamp, no earlier than the
bound first-witness request, so spacing is at least one second across the proof
boundary. Reservation/start precede dispatch. A failed, reserved or ambiguous
attempt remains spent; reopening, renaming the campaign or relocating the approved binding cannot
reset its single-attempt budget. A separate future approval is not implied.

Zero rows is a valid UNAVAILABLE check. One valid row can supply the newest
point. Two rows must be strictly descending; tied newest timestamps hold even
if their values match. Wrong/absent stream identity, unsupported schema, unsafe
quality, ambiguous configuration or provenance failure holds. The optional
`total`, when present, remains a nonnegative integer covering returned rows;
top-two completion is not historical query completeness.

The unchanged `dendra-observation-quality-1` controls native retention. Absent
and explicit-null quality impose no veto; its existing empty-claim behavior is
also unchanged. Supported nonempty quality remains only in the private native
object and makes the newest point UNAVAILABLE with `record=null`. Missing/null
values likewise provide no point. Neither case falls back to the second row.
No flag meaning is inferred. Display conversion comes from the verified scale
decision, not an independent latest conversion rule.

`evidence` reopens/verifies the original binding, event/anchor chain, response
object, accepted reviews and dispatch-time permission. It derives exact source
and retrieval ages. `live_proof=True` additionally requires the current source
and receipt age at most 300 seconds. Historical read-only evidence remains
readable after this proof limit; it cannot claim a fresh check. There is no
86,400-second observation cutoff or other new scientific age threshold. Source
timestamps, including stale timestamps and their precision, are preserved.

`project` provides the separately authorized private real-AVAILABLE route using
the existing four-field latest marker and frozen AVAILABLE record fields in
[DENDRA_BROWSER_PROFILE.md](DENDRA_BROWSER_PROFILE.md). The public historical
exporter still emits its existing UNAVAILABLE marker, and its synthetic-only
AVAILABLE validator remains unchanged. Private evidence is never inserted into
the public descriptor graph by this route. Generation/lineage hashes, actual
source/retrieval/evaluation timestamps, exact ages, native/resolved value and
frozen identity are projected; quality values, annotation IDs, private paths and
review payloads are not. A point is separate instantaneous evidence, never a
partial-day mean, historical terminal, filled gap or replacement for a withheld
day. No current-state or publication claim is granted by private availability.

The successful immutable `latest-evidence.json` and original receipts bind the
latest attempt time, last successful source check, latest eligible timestamp
(null when unavailable), retrieval time, quality disposition, receipt/object
hashes and exact ages. Failed attempts remain represented by their Journal
events and never advance a successful-check field. Latest state is independent
of historical query coverage, the sealed-history manifest and contiguous
coverage frontier, frozen cadence, quarantine/withheld daily disposition,
daily candidate state, publication acknowledgement and last publication.
There are no history tasks in a latest Journal and it cannot seal an interval.
Routine catch-up, overlap replacement and publication coordination remain
separate work.

A future separately approved Deep-only proof may use four prerequisite calls
(vocabulary, exact station, first datastream page, ascending-first witness),
explicit review, then one descending latest call. The maximum is five calls /
41,943,040 bytes; the latest window is at most 300 seconds. This readiness route
does not execute those calls, accept those reviews, or grant follow-on authority.

### Private latest-rejection diagnostic

`latest_observation.prepare_diagnostic` binds
`dendra-latest-rejection-diagnostic-1` and the explicit purpose
`rejection_diagnostic_only` before reservation. It reuses the latest request,
authority checks, separate single-use root, transport and Journal; the purpose
changes the request identity. Ordinary latest bindings remain unchanged.
Changing purpose after reservation, renaming a campaign, reopening a spent
Journal or relocating its approved storage cannot reset accounting.

The exact descending `limit=2` response is inspected only in memory. The
diagnostic stores at most two fixed-schema row fact objects: field presence,
supported type, selected-stream equality, timestamp parseability, year>=1900,
not-after-retrieval, strict relative order and ties. Inapplicable comparisons
are null, not invented passes. Aggregate schema/order facts and hashes bind the
response, request, checkpoint, collector and retrieval-time reference. No raw
returned ID, timestamp, numerical value, quality payload, annotation, coordinate,
unknown key or parser exception text enters the diagnostic object. Unknown
schema/types refuse; rows or malformed values are never repaired or truncated
to pass. The canonical projection ceiling is 12,288 bytes. Response limits
remain one attempt, 25 seconds, 8 MiB, concurrency one, spacing at least one
second, no retry/redirect/pagination, and a window of at most 300 seconds.

`PASSED_STRUCTURE_ONLY`, `REJECTED_PREDICATES` and `REJECTED_SCHEMA` are diagnostic
classifications, never latest admission. All three carry
`latest_available_allowed=false`, `latest_unavailable_allowed=false` and
`publication_allowed=false`. Successful receipt persistence does not mean
privacy/identity admission: those receipt classifications stay `not_evaluated`,
and effective limit/page-completion admission fields stay null. Transport,
decoding or resource failures may instead leave an omitted-body spent receipt
without a diagnostic result. No automatic retry follows either outcome.

Before writing the object, the Journal recomputes the exact safe projection
against the original in-memory response and receipt retrieval time. The received
event binds the sanitized object's hash/bytes plus the original response
hash/bytes/row accounting; no independent ledger or unbound sidecar is used.
`body_retained` in generic Journal accounting means an object exists: for this
purpose its representation is **sanitized**, never the raw provider body.
`diagnostic_evidence` verifies the original reservation/start/receipt/object
chain. Production `evidence`/`project` refuse diagnostic purpose and retain the
rule that ordinary latest may not substitute sanitized evidence. No history
task/seal, latest marker, daily change or public projection is produced.

The original omitted failed response cannot be reconstructed by this feature.
Synthetic predicate fixtures establish readiness only. A later explicitly
approved single live diagnostic, with fresh current-source authority where
required, can distinguish its own response; it cannot prove the old response's
unrecorded contents. This source change does not rebind existing authority or
authorize a provider call.

The separate future ribbon design remains governed by versioned configurable
policy inputs. SCAN's existing 7/200 remains preserved; Dendra/common 200 versus
300 qualifying days and 9 versus 10 reference WYs remain undecided. The ten-WY
trace display direction is not statistical science. No threshold is changed here.
