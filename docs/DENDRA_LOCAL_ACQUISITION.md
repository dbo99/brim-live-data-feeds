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
not admission. The maximum scope is `[2025-10-01T08:00:00Z,2026-10-01T08:00:00Z)`.
Limits may only be reduced: 1,500 total attempts, at most 64 metadata/witness
attempts, 1 GiB response bodies, and 7,200 seconds from the first durable provider
reservation. The parent directory must already exist under the private task
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
