# SM3A local operations contract (review pending)

## Coverage: dendra-coverage-plan-1 / dendra-coverage-collection-1

The R archive2 runner supplies a fully validated acknowledged/explicit seed parent to
`dendra_coverage.plan`. Calculations stay in unchanged `dendra/core.R`; the public archive,
state receipt, validator and reader schemas do not change. Pure planning does not itself
scientifically validate an arbitrary supplied product. The live runner does so first.

A complete date needs `query_by_date.status=queried` tied to the complete source chunk's
interval, content checksum and original retrieval timestamp. Numeric rows alone are not
query proof. Missing internal dates inside the already-approved window are gaps. An
accepted `before_saved_source_start` prefix before the earliest complete query is not an
implicit historical import. The pure planner can represent an unqueried diagnostic, but
never inserts that status into the unchanged validated archive schema. Known complete
empty queries are success; their removals still pass the existing exact R loss guard.

Capture fixed UTC-08:00 completed-day cutoff. Union required gaps with the seven-day
reconciliation overlap, deduplicate and split moisture into <=30-day and temperature
into <=90-day half-open requests. Explicit temperature retention excludes expired dates.
New stream work needs `dendra-observation-windows-1`: identity SHA256, start_date,
end_policy=completed_cutoff, review_id and reason, plus existing additive selection review.
There is no default ten-year import. Plan mode can return a held onboarding plan.

A default collection epoch is bound to the exact parent index SHA, or explicit new
initialization approvals. An explicit request-generation starts a new reconciliation
epoch. Completed interval envelopes bind the source identity/units/privacy, daily policy,
calendar, interval, epoch, collector-source hashes, response completeness and retrieval
metadata. These are integrity checks in trusted local storage, not provider signatures.
Pending completed envelopes are revalidated independently and reused at the next cutoff
where their intervals fit the required work. Remaining earlier work sorts before newer
work. They are collection work, never a new planning parent. Prepared state is rejected.
Changing the parent starts a new overlap epoch; no persistent scientific trust cache exists.
A next-day resume preserves old retrieval timestamps rather than claiming another fetch.

Default upper bounds per collection epoch attempt: 80 HTTP attempts including metadata,
pages and retries; 300 seconds (monotonic plus wall, POSIX response/read alarm and bounded
backoff); 64 MiB response bytes, 1,000,000 source rows, 16 new intervals, 4,096 planned
intervals. Lower overrides only. Each response <=8 MiB, <=20 pages per interval; existing
2-attempt page retry/circuit behavior remains. Templates share metadata/observation ledger.
Partial/failed pages create no completed envelope. Exhaustion saves completed work and
an explicit backlog; it never loops indefinitely or publishes a partial network. Bound
work is not a promise that 434 streams can finish with these budgets. Pilot selection
must fit an expressly approved request envelope. No SM3A request is authorized.

Plan input/checkpoint inventory is bounded (4,096 pending envelopes / 256 MiB); old
checkpoints are not automatically deleted. Exhaustion needs explicit retention review.
Collection remains serial. Response alarms require POSIX main-thread execution. Resource
monitoring separately limits whole local test processes. Post-collection build time is
captured separately from run start; mixed timestamp precision is ordered as UTC time.

## Health: soil-network-result-1 / soil-network-health-1

Local namespace: an independently managed operations root, `runs/{scan|dendra|snotel}/`
with immutable `{run_id}-{attempt}-{prepare|publication}.json`. There are no observations,
coordinates, credentials or status files inside an observation manifest. Snapshot closure
is `soil-health-snapshot-1`, external manifest SHA256, strict paths/no links, <=10,000 records
and <=64 MiB. Restore is fresh-directory only. No new storage or external writer is added.

Each record carries exact source identity; cycle/run/attempt/phase; start/finish/cutoff;
outcome/error class; request counts and budget with explicit unknown basis; required,
completed and backlog intervals; last successful source-check time; latest eligible
observation (Dendra accepted date label or SCAN original UTC timestamp); candidate generation;
and an optional verified committed generation/commit/index checksum and bounded ancestry.
Unknown/null has a reason. Existing SCAN R acquisition does not expose cumulative counts:
these remain null even on failure, not zero or one R call. SNOTEL is always not_enabled.

Results distinguish success_changes, success_no_data_change, provider_failure,
incomplete_catch_up, semantic_hold, data_loss_hold, publication_failure, missed_run and
not_enabled. Successful query and publication are independent. A failed candidate can
still retain a truthful successful query; a new generation need not mean new observations.
Newest authoritative candidate controls latest eligible observation, including an approved
removal or known absence. Failed attempts retain the prior valid observation knowledge.
Timestamp maxima use parsed UTC for source checks; report time is never source freshness.

Run ordering uses start/attempt/phase so a delayed old completion cannot supersede a newer
attempt. Publication ordering requires a unique descendant among verified committed
receipts, using up to 256 actual Git ancestor commits; incomparable/insufficient ancestry
fails closed for review. An old tree observed late cannot replace a newer acknowledgement.
Snapshot/result creation is local. Future public health would require a separately owned
small status product and callback under the existing shared publisher, with its own
sequence/ancestry validation. No public product, consumer URL or competing writer is
implemented or necessary for the current local proof.

## Workflow/policy: soil-morning-proposals-1

Three templates are OUTSIDE active workflows; manual-only proposals. Per-source read-only
prepare precedes only that source's write-permitted publish job. Dendra uses the actual R
entry, exact public state restore and prepared transfer; SCAN uses its existing R builder,
five-file validator and feed_build_time metadata. Both retain unmodified shared publisher
and independent candidate/reconcile/staged callbacks. SNOTEL execution/publication is gated.
Acquisition stays outside the shared publish lock; source publishers reconcile fresh main.
Health artifacts preserve setup failures and collection failures even without a candidate.
A missing checkout/artifact before the helper can run remains a scheduler-inventory missing
result, never fabricated successful health. Hosted artifact durability/syntax remain held.

The pure morning policy takes explicit UTC cycle/recovery window, source enablement,
fresh complete scheduler inventory (<=300 s), and local health. It proposes at most one
idempotency key per source/cycle. Any active run or reserved/attempted retry suppresses
another; a newer terminal run without matching health waits. Healthy checks, silent-but-
checked sensors and semantic/data-loss holds do not trigger automatic acquisition. It
emits decisions only. A future dispatcher must atomically reserve/re-read current run
inventory; pure proposals alone are not an external exactly-once service.

SCAN 10:17, Dendra 10:37, SNOTEL 11:17 UTC are comments only. Existing SWE uses 05:52,
13:52,21:52 UTC at this pinned source. Staggering does not establish aggregate NRCS
compliance: existing per-call retry/backoff remains, cumulative accounting and coordinated
SCAN/SNOTEL/SWE provider budgets need approval before activation. No distributed lock or
new source policy is introduced. Within-network partial optional-companion success remains
future work; across-network independent last-good retention is implemented/tested locally.

## SM3AR1 pending-query handoff and recovery corrections

`dendra_pending.py` implements `dendra-pending-collection-1`. This is completed,
unacknowledged query work, separate from daily products, validation receipts and
`soil-health-snapshot-1`. Restore the exact committed public parent first, restore
health independently, then restore pending work before planning. A manifest SHA256
must come from the selected prior run's reviewed evidence; an artifact name alone
is insufficient. The actual R runner still validates the parent and applies all
scientific, identity, source-deletion and whole-candidate guards.

The snapshot binds the full published pointer, parent-derived query epoch, selected
scientific/privacy identities, integration/onboarding policy, collector/source hashes,
query intervals, complete response/content hashes and original page/retrieval clocks.
Only validated complete envelopes are included; failed pagination is not an empty
response. No acknowledgement is advanced by export or restore. Source-query raw rows
are confined to this bounded private artifact, never added to the public observation
Git product. No persistent semantic trust cache is introduced.

Bounds are 4,096 complete intervals, 256 MiB total envelope bytes, 64 MiB per envelope,
8 MiB manifest, and 1,000,000 normalized rows. Paths/closure/hashes are checked; links
are rejected. Restore validates all content before atomic installation. The proposed
retention is 14 days from the oldest original retrieval (or export for an empty
snapshot); re-export cannot renew old queries. Artifact retention is also proposed at
14 days. These failure-work artifacts are not the durable daily archive or a backup
policy. Expiration, loss, corruption, parent/selection/code changes or setup failures
hold automatic acquisition by default. A separately reviewed `reviewed_bounded_refetch`
choice may discard unavailable progress and start a bounded fresh collection; the
result explicitly says progress was lost. It is not automatic resumability. Repeated
loss of artifacts with an insufficient per-run budget requires operations review,
not silent indefinite re-fetching. No remote retention destination is approved.

The inactive Dendra template carries pending work even when no candidate exists,
alongside independent health, using always-run export/upload steps. The template
requires an independently bound prior pending manifest and restore policy. Its public
parent restore precedes pending restore; source collection follows. Local tests execute
these same shell steps with directory copies replacing the remote artifact transport.
No Actions run or hosted artifact behavior has been demonstrated.

Morning inventory must identify network, cycle, run ID, numeric attempt, kind, status,
explicit UTC attempt start and terminal finish/conclusion. It is fresh and complete
within 300 seconds. Active/reserved work blocks the same network across cycle keys;
retry quota remains current-cycle. Latest attempt start orders terminal work, with
attempt number resolving reruns of the same ID; equal starts of different IDs hold as
ambiguous. A delayed older finish cannot shadow a newer matching result. Missing
attempt/chronology, duplicate identities and reversed attempt chronology fail closed.

Health supplies immutable phase records to match that exact run/attempt. Preparation
success is not delivered success. Verified publication or no-op receipt suppresses
acquisition even if a later workflow step fails (reported as degraded). Successful
preparation or failed receipt with a candidate yields only a
`propose_publication_recovery` decision, conditional on revalidating its exact transfer
and fresh parent. It does not assert artifact availability or bypass semantic/loss
holds. Matching acquisition failure may propose one provider retry; contradictory
scheduler success or absent health waits. No acknowledgement comes from scheduler
text. Reservations/dispatch and acquisition-free publication recovery execution remain
separate implementation/authorization gates.

`publication-result` records verification failure before returning exit 2; verified
success/no-op returns 0. SIGTERM/KeyboardInterrupt during verification becomes a
failure record when finalization can still run. Publication health export is a separate
`always()` step, so receipt-command failure cannot skip it; an earlier transaction
failure remains a failed job. Abrupt runner loss/SIGKILL cannot guarantee a new health
artifact and must be resolved from surviving prior health plus scheduler evidence.

The SCAN no-provider dependency preflight parses the actual pinned builder's literal
requirements and namespace/library uses without sourcing the builder, compares both
active and inactive dependency declarations, and records source/setup checksums and
installed availability. The inactive template now includes `soilDB` and `httr` explicitly.
Audit-only mode reports missing local packages; normal mode fails before acquisition.
No package install, live SCAN execution or clean hosted runner is implied.
