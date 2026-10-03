# Private local native acquisition

`scripts/dendra/acquire_native.R` is an ordinary R entrypoint invoking the installed
Python interpreter and repository collectors. It requires no agent, model service,
R package installation, or networked orchestration. It produces private native
evidence only. It does not produce daily science, browser feeds, or publication.
The acquisition and scientific rules in [DENDRA_HISTORY_CAMPAIGN.md](DENDRA_HISTORY_CAMPAIGN.md)
remain controlling. Public contracts, scheduled feeds and historical launchers are unchanged.

## Configuration and source gate

Use one explicit, private configuration for one job. Version
`dendra-local-native-job-1` binds the frozen inventory, original metadata catalog
checksum, exact selected stream IDs, source HEAD/tree/collector fingerprint, R
entrypoint checksum, WY2026 scope, original archive references, attribution
evidence, storage reserve, destination and limits. `enabled=false` is the delivered
default. A later reviewed checkpoint and a configuration bound to that committed
source are required before live use; a dirty or untracked implementation refuses
live dispatch. Do not change configuration, source or authority inside a job.

The supported selection is the reviewed 28-candidate CDFW cluster. Selection is
not admission. Versions 1 and 2 retain the maximum scope
`[2025-10-01T08:00:00Z,2026-10-01T08:00:00Z)`.
Whole-job limits may only be reduced: 1,500 total attempts, at most 64
metadata/witness attempts, and 1 GiB response bodies. The original execution
window lasts at most 7,200 seconds from the first durable provider reservation.
Explicit continuation windows below preserve those cumulative ceilings.
The parent directory must already exist under the private task
evidence area. The new job destination must not exist when preparing it.

The configured free-space reserve must be at least 8 MiB. Preparation checks
four times the response budget plus that reserve, allowing native/normalized
copies and temporary staging; capacity is checked again at child boundaries.
This is a conservative space guard, not a storage reservation or backup.

## Operator commands

From the repository root, substitute the reviewed private config/review paths:

```sh
Rscript --vanilla scripts/dendra/acquire_native.R --help
Rscript --vanilla scripts/dendra/acquire_native.R inspect /path/to/config.json
Rscript --vanilla scripts/dendra/acquire_native.R prepare /path/to/config.json
Rscript --vanilla scripts/dendra/acquire_native.R status /path/to/config.json
Rscript --vanilla scripts/dendra/acquire_native.R metadata /path/to/config.json --allow-provider
Rscript --vanilla scripts/dendra/acquire_native.R review /path/to/config.json --review /path/to/review.json
Rscript --vanilla scripts/dendra/acquire_native.R acquire /path/to/config.json --allow-provider
Rscript --vanilla scripts/dendra/acquire_native.R stop /path/to/config.json
Rscript --vanilla scripts/dendra/acquire_native.R resume /path/to/config.json --allow-provider
```

Only `metadata`, `acquire`, and `resume` can contact the provider. They require
both an enabled configuration and `--allow-provider`. Help, import, inspect,
prepare, review, status and stop never dispatch. `prepare` creates the job;
`metadata` uses the existing authority-package metadata/witness collectors,
then produces one consolidated `review-request.json` and pauses. No command
automatically accepts a review or refreshes authority. A job containing a
checksum-bound offline fixture is permanently synthetic and denies socket/DNS
operations; it cannot be converted into a live job. `--offline-now` is forbidden
for live execution.

The review input has the exact job ID and every selected stream, either explicitly
excluded or carrying `source_review`, `native_review`, `placement_review`, and
`scope`. Existing committed source-start/native review schemas remain mandatory.
The additional placement acknowledgement pins the station metadata and
configuration evidence hashes, exact known depth in cm, original evidence file
hashes, scoped deployment, public EPSG:4326 station geometry, and the timestamp
meaning `UTC t; preserve native timestamps`. Unknown depth cannot be guessed.
Elevation is optional; a native Z is not interpreted as elevation or feet MSL.
Review inputs are supplied by the authorized reviewer, never synthesized by the
runner. Configuration gaps, stale reviews and incompatible archive bindings HOLD.

## Exact reviewed current scopes

Version `dendra-local-native-job-3` uses the same configuration fields as version 1,
with a finite, explicitly chosen `scope` and admitted `streams`. Timestamps must
use canonical UTC serialization (`YYYY-MM-DDTHH:MM:SS.sssZ`). Scope dates are
preparation intent only: they grant no observation acquisition permission.
The mechanism supports future current catch-up intervals without a water-year
conditional. Versions 1 and 2 keep their original scope and review semantics;
existing jobs are never migrated or expanded.

Use the ordinary `inspect`, `prepare`, `metadata`, and `review` commands above.
The original review input must bind the exact config-derived `job_id`, every
selected stream, and the exact configured start and end in each native and
placement review. Source-start, fresh native metadata, and explicit placement
acceptance remain mandatory. Excluded, held, or missing entries refuse admission;
a reviewed subset requires its own explicitly selected config and job. The runner
never manufactures acceptance or renews original evidence timestamps.

Successful review writes immutable `scope-binding.json`, pinning source identity,
serialized config/job identity, ordered frozen station/stream identities, exact
scope, the original review file path and byte hash, and review/catalog/plan/reuse
hashes. Preserve that original review file at its bound path. Date-only config
edits, roster changes, changed reviews or plans, and changed job identity fail
closed. Planning still subtracts compatible sealed coverage and splits only the
remaining reviewed intervals; empty, failed and unqueried retain their meanings.

Validate the accepted scope offline before acquiring:

```sh
Rscript --vanilla scripts/dendra/acquire_native.R validate-scope /path/to/config.json
Rscript --vanilla scripts/dendra/acquire_native.R acquire /path/to/config.json --allow-provider
Rscript --vanilla scripts/dendra/acquire_native.R status /path/to/config.json
Rscript --vanilla scripts/dendra/acquire_native.R resume /path/to/config.json --allow-provider
```

`validate-scope` opens an existing reviewed job, checks its bindings/currentness,
and reports its exact interval, roster and task count without provider dispatch.
Acquire and resume recheck the same authority, including before each history
dispatch. Expiry can therefore stop an already-running job; it never extends the
original reservation-based deadline. Every history request must match a bound
plan task's stream and end, with a start inside that task for pagination. All
existing attempt/byte budgets, serial pacing, single writer, completeness and
spent-unsealed rules remain unchanged. These commands affect private evidence
only; they do not schedule, publish, or fetch historical backfill automatically.

`tests/dendra/test_reviewed_job_scope.py` exercises exact WY2026 compatibility,
a fixed synthetic current interval, fresh R readback, rejected scope/roster/review
mutations, mandatory authority, dispatch-time expiry, and unchanged resume
accounting. Synthetic acceptance never authorizes a real current-data job.

## Exact per-stream reviewed scopes

Version `dendra-local-native-job-4` adds the required `stream_scopes` object to
the version-3 configuration fields. It maps every selected stream ID, and no
others, to its exact canonical `{start,end}` half-open interval. Different
starts and a common end are supported; different ends are also permitted.
The existing `scope` field must equal the envelope from the earliest start to
the latest end. That envelope is for reporting and grants no acquisition
permission outside an individual stream's interval.

Each source/native/placement review remains mandatory. Each stream's native and
placement review scopes must equal its configured interval, and the original
accepted input binds the complete config-derived job ID. `scope-binding.json`
includes the exact `stream_scopes` map along with the existing identity, source,
review, catalog, plan and reuse hashes. The planner requires the complete admitted
map, subtracts compatible coverage per stream, and emits only tasks inside that
stream's interval. Validation, acquire, resume and each observation dispatch
enforce the individual bounds and original review bindings. Changing dates,
swapping assignments, adding a held stream or widening one interval on resume
fails closed before observation access. Expiry and the original provider window
remain enforced during acquisition.

Use the same R commands as version 3. `validate-scope` also returns the exact
per-stream map for fresh-process readback. No automatic review or authority
renewal is added. New metadata preparation starts that job's provider window;
prepare a later wave offline and defer its metadata/review until it can run.
Versions 1/2 and shared-scope version 3 retain their exact serialized fields and
meanings; existing jobs are never rewritten or reinterpreted. The focused scope
tests cover three distinct historical starts, per-stream task boundaries,
binding/expiry failures, and finite synthetic acquire/resume without refetch.

## Explicit continuation execution windows

For an existing version-3 or version-4 reviewed job, `continue-window` opens one
additional execution window offline, under the existing exclusive writer lock:

```sh
Rscript --vanilla scripts/dendra/acquire_native.R continue-window /path/to/config.json
Rscript --vanilla scripts/dendra/acquire_native.R validate-scope /path/to/config.json
Rscript --vanilla scripts/dendra/acquire_native.R resume /path/to/config.json --allow-provider
Rscript --vanilla scripts/dendra/acquire_native.R status /path/to/config.json
```

The prior window must have expired. The operation requires current original
authority, exact immutable job/config/review/roster/scope/plan/metadata/placement
bindings, no spent-unsealed or partially initialized children, and remaining
unstarted tasks. Remaining attempts must cover three per remaining task; remaining
bytes and storage must cover the next bounded child. The cumulative byte ceiling
continues to apply and can still stop acquisition. A continuation is a time-window
authorization, never a new scientific review, retry, budget refund or replacement
job. At most 16 explicit continuation windows are supported; none opens implicitly.
Versions 1/2 retain their previous semantics and do not support this operation.

Each `continuation-windows/NNNN/opened.json` is created once, with its own checksum,
previous-window hash, original artifact hashes, source provenance, cumulative
accounting snapshot and remaining task ordinals. Its `opened_at` starts the new
window; its deadline is that instant plus the existing configured duration
(at most 7,200 seconds). `first_attempt_at` is initially null because opening makes
no provider request. A separate immutable `first-attempt.json` records the first
durable reservation, reconstructed from original Journals after a crash if needed.
That later first attempt never extends the deadline. Original `window.json` remains
unchanged. Status retains the original `accounting.window` and adds
`continuation_windows` and the effective `execution_window` only for opted-in jobs.

Sealed children are skipped. Every new child still uses the existing Journal,
transport, page limits and current authority checks. Scope validation and dispatch
check continuation history and immutable pins; expiry can stop a running window.
An active-window ordinary resume behaves as before. A second `continue-window`
while the latest window is active refuses, so the operator block must not reopen
a window already prepared by the reviewer.

An existing job may retain its original supervisor fingerprint after a compatible
source update. This explicit path verifies the captured sources against local Git
objects and permits changes only to `history_acquisition/local_job.py`; every
captured parser, Journal, transport, science and R entrypoint byte must match.
The opening record pins the current execution source. Original config, job ID,
reviews, metadata, timestamps and seals are never rewritten. New child execution
provenance derives only the executor fingerprint from that compatibility record;
original acceptance and expiry are revalidated unchanged. Further execution-source
changes require another explicit window after expiry. No branch/ref changes or
network requests are performed by this compatibility check.

`tests/dendra/test_continuation_windows.py` covers fresh-R continuation and
supervisor compatibility, unchanged old seals/accounting, remaining-only execution,
expiry, writer exclusion, budgets, spent work and altered window/authority pins.

## One catalog and durable source organization

`catalog.json` is the one job series catalog. It begins pending and gains explicit
review bindings at the review stage. The immutable job config pins all attribution
inputs; each reopening validates their hashes and catalog consistency. Each series
has `series_key`, frozen `identity`, scoped deployment/configuration binding, and
`source_organization`. Organization fields are `subprovider_key` (source ID),
`subprovider_name` (exact name), `subprovider_label` (explicit ID-to-display mapping
prefixed with `Dendra-`), `status`, and evidence-backed `claims`. The platform stays
`dendra`. Names alone never invent an organization ID or default to CDFW. Partial
claims remain `MISSING`; contradictory claims remain `CONFLICT` in the consolidated
review. Each claim retains its source path/hash/record pointer and whether it was
a station or stream record. Historical applicability is not inferred.

Display labels are not station/stream/series identity. `plan.json`, the completed
asset entries in `status.json`, and reused archive associations all carry a
`series_metadata_reference` into this catalog. Series keys include the frozen
station/stream/depth/orientation, scope and reviewed configuration binding. They
do not use an organization display label. No flat observation export is produced;
a future authorized exporter would need the subprovider key and label on each row.

## Persistence, reuse and stopping

Existing Journals are the sole per-request ledger. Whole-job counters are
reconstructed from their reservations/starts/receipts; no refund, hidden retry,
budget renewal or second request ledger exists. `window.json` preserves the
deadline from the first reservation, including review pauses and restarts.
Capacity reserves the worst-case next child envelope before starting it. Existing
serial transport, one-second start spacing, zero retries/redirects, 25-second
deadlines, 8 MiB bodies and three-page/2,016-row history limits remain in force.

`stop` records a request without interrupting an in-flight collector. The worker
finishes its current metadata package or history task and stops at the boundary.
`resume` acknowledges that request but retains the original job identity, attempt
and byte counters, completed seals, and deadline. An exclusive OS lock refuses
another writer; process exit releases it without deleting evidence. A crash after
reservation remains spent. A partially initialized or spent-unsealed child HOLDs
for explicit review; this bridge does not recover it by retrying or splicing pages.
Missing/corrupt state and decreased recorded accounting fail closed. Command HOLD
logs and original Journal failures are preserved. Deleting or moving state to
reset a job is not supported.

`asset-map.json` retains original archive/Journal roots and immutable hashes.
The existing sealed reader verifies each referenced interval before subtracting
coverage from the reviewed scope. Complete-valid-empty intervals are reusable.
Configuration identity must agree. Reuse creates only a catalog association,
preserving original retrieval timestamps, raw responses, Journals and seals.
New archives stay under `history/<ordinal>/campaigns/.../objects/`; status/log files
are not raw archives. No migration, cleanup or duplication of historical archives
is performed. All output is private task evidence, outside public product paths.

## Offline verification

`tests/dendra/test_local_job.py` exercises actual R subprocesses with finite
synthetic responses through the committed Journal/adapters. Supply the already
checksum-bound `DENDRA_INVENTORY` and a private `DENDRA_TEST_ROOT` under the task
evidence area. Tests cover nonempty/empty seals, fresh-process reuse and stopping,
spent crashes and errors, limits/deadline exhaustion, invalid state/review,
concurrency/storage guards, and organization persistence/conflict/label behavior.
Synthetic acceptance applies only to synthetic fixture records. Passing these
tests does not admit any real candidate or authorize acquisition.

## Reviewed preparation import and finalization

Version `dendra-local-native-job-2` provides an offline reference route for an
already reviewed CDFW preparation. It pins the donor config, consolidated recovery
request, explicit final review and supported recovery interpretation. The donor
must remain a preparation-only job. Its Journals, original producer identity,
receipt hashes, retrieval/review timestamps, attempt spending and authorization
windows remain unchanged. Original preparation counters are linked provenance;
they are never transferred into or refunded by the new campaign.

`bind-import` validates the original candidate closure, exact admitted subset,
successful witnesses, source-start/native/placement decisions, attribution and
archive map. Captured parser, science, Journal and policy source files must still
match. Differences are restricted to the explicit import/accounting/supervisor
assembly modules; current validators also check the original evidence. This
compatibility assessment is recorded, including both source closures. The native
execution assessment derives from the original review at its original evaluation
time with the new executor fingerprint. It retains the original capture-source
fingerprint, reviewed-at, expires-at and evidence hashes.

Metadata must still satisfy its original freshness deadline, and the source and
native reviews must apply to the exact evidence and requested interval. The
effective expiry is the earliest original metadata/native validity limit. An
expired preparation execution window does not refresh or expire these independent
capture/review limits. Expired or inapplicable authority blocks import and later
requests; the importer never renews timestamps or repeats metadata/witness calls.

The canonical serialized configuration hash is the job identity. Changing
`enabled` changes that identity. Finalize the disabled config before creating the
job: `finalize-import --enable` writes a new enabled config and a derived transfer
record bound to those exact bytes. It leaves the original review and disabled
config intact. The config pins original inputs; the derived transfer pins the
config, avoiding a circular hash dependency. No job exists until `prepare`.
Disabled imported configs cannot initialize jobs or dispatch, even with fixtures.

From the repository root, these are the supported operations with example paths
inside an existing private task directory. Live activation and provider access
always require separate authorization:

```sh
Rscript --vanilla scripts/dendra/acquire_native.R bind-import .l01-soil-integration/task/template.json --donor-config .l01-soil-integration/donor/config.json --request .l01-soil-integration/review/CONSOLIDATED_REVIEW_REQUEST.json --final-review .l01-soil-integration/review/FINAL_REVIEW_INPUT.json --recovery .l01-soil-integration/review/RECOVERY_INTERPRETATION.json --output-config .l01-soil-integration/task/bulk-disabled.json --job-root "$PWD/.l01-soil-integration/task/bulk-job"
Rscript --vanilla scripts/dendra/acquire_native.R validate-import .l01-soil-integration/task/bulk-disabled.json
Rscript --vanilla scripts/dendra/acquire_native.R finalize-import .l01-soil-integration/task/bulk-disabled.json --enable --output-config .l01-soil-integration/task/bulk-enabled.json
Rscript --vanilla scripts/dendra/acquire_native.R prepare .l01-soil-integration/task/bulk-enabled.json
Rscript --vanilla scripts/dendra/acquire_native.R import-authority .l01-soil-integration/task/bulk-enabled.json
Rscript --vanilla scripts/dendra/acquire_native.R acquire .l01-soil-integration/task/bulk-enabled.json --allow-provider
Rscript --vanilla scripts/dendra/acquire_native.R status .l01-soil-integration/task/bulk-enabled.json
Rscript --vanilla scripts/dendra/acquire_native.R resume .l01-soil-integration/task/bulk-enabled.json --allow-provider
```

`bind-import`, `validate-import` and `finalize-import` do not initialize the job,
start its provider clock or reserve attempts. `import-authority` installs the
derived review and admitted plan in an already prepared job without requests.
The runtime validates the pinned originals, derived job/review identity and
deterministic plan on acquire/resume, rechecks stream applicability before each
child, and checks config/review pins at dispatch. The imported route permits only
reviewed history requests and refuses `metadata` or replacement `review` commands.
Missing/changed evidence, a held stream, a changed plan or unknown accounting
without the supported bound recovery diagnostic HOLDs before provider access.
Partial import files are preserved and require review; they are not overwritten.

Archive reuse verifies original manifests, record chains, seal/object/page hashes,
query closure and receipt-time authority without replaying every historical
observation. It retains original attribution and retrieval times. The prospective
plan reports logical tasks separately from pages/attempts: each history child can
spend at most three attempts, with no automatic retries under this plan. Whole-job
caps may stop a campaign before all tasks complete. Fresh processes retain the
new job's first-reservation deadline, counters and spent/unsealed children.

`tests/dendra/test_evidence_import.py` exercises this complete R sequence with
finite synthetic donor responses and network denial, including enabled config
binding, nonempty/valid-empty completion, reuse, interruption, spent crashes,
limits, writer exclusion and negative review/evidence/config cases. Test artifacts
stay under `DENDRA_TEST_ROOT`; no real donor or archive is rewritten.
