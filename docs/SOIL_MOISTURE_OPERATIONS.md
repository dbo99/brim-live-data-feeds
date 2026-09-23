# Soil moisture operating design — inactive

The reviewed direction is three thin, source-specific routine workflows: SCAN,
dendra, and SNOTEL after its scientific gate. It preserves independent source
roots and acknowledged generations beneath the accepted shared map control.
This document grants no deployment authority and adds no active workflow.

The existing SCAN workflow is `.github/workflows/build-scan-soil-moisture-latest.yml`;
its pinned source currently schedules 14:30 UTC, uses soilDB/NWCC acquisition,
`scan_soil_moisture_publisher.py`, and the shared `main_publisher.py`. Do not change
that operation here. The existing SWE builder `build_snow_pillow_latest.R` has
AWDB preflight, bounded chunks and fetch retries; SNOTEL soil moisture must share
NRCS load/backoff decisions with SCAN and SWE rather than multiply incident load.
Reuse verified acquisition/metadata helpers where source contracts actually match.
There is no new universal NRCS daily-statistic conversion in this design.

Dendra's entry remains `build_dendra_daily.R`; `core.R` owns daily arithmetic,
flags, fixed UTC-08:00 completed days and change windows. The inactive archive
manual template uses durable committed-state recovery, explicit selection and a
source-owned candidate. Current conservative within-network whole-candidate holds
remain: a failed stream or companion can hold its network. Future stream-level
partial success requires a separate contract and mutation/race tests; it is not
implemented by the performance changes.

## Planned independence and health

Healthy networks may advance while another retains its last acknowledged product
and original observation/retrieval dates. No all-network success barrier, blanket
success override or obsolete other-network copy is acceptable. Separate source
identities, units, schemas and NRCS agency DAILY labels remain intact. Mixed network
vintages must be visible; one product generation must be internally coherent.

Keep these fields distinct: last attempt, last successful source check, successfully
queried interval, latest eligible observation, and exact acknowledged publication.
Dendra presently retains per-date query status/hash/retrieval, native latest
observation, processed/build/cutoff/expiry clocks, and publication receipts. SM3A adds a local durable source-wide failed-attempt ledger. Public delivery and
source-independent UI health summaries remain deployment decisions. Successful unchanged/empty queries differ from
failure and unqueried dates. A fresh health check never forward-fills an observation.
Identical candidate publication is a no-op; a new successful retrieval may update
query provenance and generation while observation timestamps remain unchanged.

Nightly is the approved direction. Illustrative staggering is SCAN 10:17 UTC,
dendra 10:37 UTC, SNOTEL 11:17 UTC. These are not active entries, measured provider
arrival times or guaranteed low-load windows. Final timing depends on BRIM's other
jobs and shared NRCS/SWE load. Each source captures one run cutoff; unverified NRCS
labels are not shifted onto Dendra's calendar.

## Shared publication and recovery

Collection/preparation stay outside publication concurrency. Current pinned active
writers declare `brim-live-main-publish`, `cancel-in-progress: false`, and `queue: max`.
The inactive integrated/archive Dendra and three network templates declare the same
job-scoped controls. This source alignment does not prove hosted queue behavior or
authorize activation; deployment validation remains required. Cancellation configuration
alone is not a durable queue. The
shared publisher fetches the actual current parent, reconciles owned paths, performs
independent staged validation and ordinarily pushes. A non-fast-forward has one
bounded fresh-parent retry. It does not force-push. Local measurements separate the
publication command span from acquisition, prepare, artifact serialization and
setup; real queue wait, Linux contention and hosted transfer remain unmeasured.

Persist checksummed daily partitions and an exact committed receipt. Restore the
captured committed generation; an unpublished prepared product never becomes the
next parent. Dependency/work caches are accelerators, not sole authority. State
snapshots contain daily sufficient statistics and query provenance, not full native
subdaily history. Backup location, cost and retention require deployment approval;
no S3 bucket or public repository is created or implied.

SM3A implements the routine union of seven completed reconciliation days and
unqueried dates inside the approved source window. Complete response/query provenance,
not row extent or latest numeric observation, determines coverage. Moisture intervals
are at most 30 days; temperature intervals at most 90 days within explicit retention.
Missing internal dates are absent rows/query keys under the existing archive schema;
no invalid `unqueried` status is inserted into a validated product. New selections
require an explicit identity-bound observation horizon as well as additive selection
approval. See [the versioned local operations contract](SOIL_MOISTURE_SM3A_CONTRACT.md).

Completed source envelopes can be reused across cutoffs within a parent-bound collection
epoch. Their original retrieval times survive. Partial pagination never becomes empty
or complete; incomplete required work holds the whole network and keeps the exact
acknowledged parent. No partial optional-temperature release is implemented. Explicit
older reconciliation and frozen replay keep their accepted policies and removal guards.

Three inactive templates under `templates/soil-moisture/` separate preparation and
publication by network. Pure morning proposals require fresh complete scheduler
inventory and matching terminal run identities, suppress active/healthy/previously
retried sources, and never retry semantic or source-loss holds automatically. They
do not dispatch. The source templates remain outside `.github/workflows`; existing
SCAN/SWE schedules are unchanged. Local health snapshots are independently restorable
and carry no observations. Artifact retention is a proposal, not proven hosted backup.

The observed 15-WY file-envelope issue remains: the unchanged 20,000-file/40-page
limits can be exceeded at 434 streams. Efficiency does not extend that envelope or
authorize old-year deletion. Any later layout/storage migration must review producer,
portable state, reader, publisher, backups and compatibility together. Real BLM
membership stays unknown without approved geometry; NRCS SMS DAILY derivation and
SNOTEL production remain separate gates. The next external manual trial still needs
explicit target, permissions, request budget, rollback and user approval.
