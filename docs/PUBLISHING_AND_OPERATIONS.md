# Publishing and operations

This runbook describes current GitHub Actions publication, last-known-good
behavior, incident response, rollback and recovery for the public live-feed
repository.

It distinguishes observed controls from recommended future controls. Following
this document does not grant permission to dispatch a production writer,
publish a product, merge a pull request or change repository settings.

Baseline audited: 2026-07-24; the audited commit is recorded in
[README.md](README.md).
Publication/governance reconciliation: 2026-09-09 UTC against `febf1cae`
(after PRs #43 and #44). Product acceptance details remain in
[PRODUCTS.md](PRODUCTS.md) and [BRIM_CONSUMER_CONTRACT.md](BRIM_CONSUMER_CONTRACT.md).

## Official publication architecture

The official product has two stages:

1. Each of the fifteen scheduled writers retrieves, transforms and validates
   data in a read-only `prepare-candidate` job, then delegates a validated,
   run-local candidate artifact to a separate `main`-authorized
   `publish-to-main` job for bounded fresh-main reconciliation.
2. GitHub Pages separately deploys `main:/docs`.

The public URL is `https://dbo99.github.io/brim-live-data-feeds/`.

A successful product commit and a successful Pages deployment are related but
not the same event. During a Pages delay or failure, Git `main` may contain a
newer product than the hosted site.

## Branch-safe writer behavior

All fifteen scheduled production writers currently:

- support schedules and manual dispatch;
- allow their isolated `prepare-candidate` jobs to run concurrently;
- grant preparation only `contents: read` and publication `contents: write`;
- serialize only `publish-to-main` through `brim-live-main-publish`, with
  `cancel-in-progress: false` and `queue: max`;
- follow their documented checkout/ref contract with full history;
- preserve product-specific validation and last-known-good behavior.

After the publisher job enters the queue, [`scripts/main_publisher.py`](../scripts/main_publisher.py)
checks candidate inventory, hashes and source identity, fetches current
`origin/main`, and creates a detached temporary transaction worktree at that tip.
The product callback validates and reconciles the candidate with that current
product. Fixed-file candidates use integrity metadata schema 1; candidates with
owned target roots use schema 2. Neither is a common public feed schema.

Only a product-classified new candidate can advance the product. Same/stale
candidates are no-ops according to the product callback; ambiguous identity or
invalid state fails. The workflow supplies static `--allowlist` paths and, for
rolling products, `--owned-root` boundaries. Candidate metadata cannot expand
that ownership. The publisher checks changes before staging, stages only those
paths, validates the staged product, and issues an ordinary non-force
`HEAD:refs/heads/main` push. See the concurrency section for the bounded retry.

### Checkout and source identity

Preparation selects the intended branch tip. The checked-out build SHA, publisher callback SHA
and fresh-main transaction SHA are distinct evidence and must not be conflated:

| Writers | Preparation checkout | Publisher callback/provenance checkout |
|---|---|---|
| CDEC, CoCoRaHS, Delta, SCAN, streamflow, groundwater, ASOS/AWOS, snow | `github.ref` with full history | Event `github.sha`; candidate metadata carries `GITHUB_SHA` |
| CNRFC, CBRFC, GFS, HRRR, NBM wind, NBM QPF, Winter Storm Levels | `main` for schedules; `github.ref_name` for manual runs, with full history | Actual preparation HEAD recorded as `source-sha`, then used for metadata, checkout and publisher `GITHUB_SHA` |

The first group does not record preparation HEAD separately. If the branch
advances after the event, its candidate code and event-SHA callbacks can differ.
Fresh-main reconciliation does not prove those code versions match. Investigate
that distinction during source-related failures; extending recorded build-SHA
binding is a separate workflow change, not an implemented universal guarantee.

Consequences:

- Scheduled events run from the default branch and can update official
  `main`.
- Manual dispatch can target a feature branch for candidate or dry-run evidence.
- A feature-branch run cannot update `main` through the writer's push command.
- GitHub Pages publishes only `main:/docs`, so feature-branch products are not
  official hosted products.
- No producer workflow runs on `pull_request`.

## Maintainer authority and repository settings

David approves merges, manual production dispatches/reruns and official
rollbacks. Permission to diagnose, edit documentation or prepare a PR does not
authorize those actions. Existing schedules and watchdog dispatch policy remain
separate from a new manual intervention.

Human access, automation identities, the dated GitHub ruleset verification and
compatible settings guidance are owned by
[SECURITY.md](../SECURITY.md#repository-access-and-branch-controls). Public read
access is not write access. A Git commit cannot change or verify hosting-platform
settings, and blanket PR/check/update requirements can block scheduled writers.

## Concurrency, cancellation and queueing

Production writers have no top-level workflow concurrency group. Their
read-only preparation jobs may overlap because each candidate and its integrity
metadata remain isolated to one workflow run and cross the job boundary only as
a short-lived artifact.

Every `publish-to-main` job uses the constant repository-wide group
`brim-live-main-publish` with `cancel-in-progress: false` and `queue: max`.
Only publication is serialized. After entering that group, each publisher
fetches fresh `origin/main`, reconciles its own candidate against that state,
stages only its declared product paths, and pushes normally to `main`.

A non-fast-forward rejection receives exactly one retry: fetch fresh `main`,
create a new transaction, rerun candidate validation, product reconciliation and
staged validation, and try a new ordinary push if the product is still new.
This separately implemented recovery design supersedes the older
reject-on-first-advance behavior. It never pulls, merges, rebases or force-pushes
the rejected generated commit. A second race, non-race push error, unexpected
path or validation failure stops; there is no third attempt or broader recovery
fallback. A rejected attempt makes no remote update.

`queue: max` retains multiple pending publication jobs instead of replacing an
existing pending publisher. Ordering should not be treated as a data guarantee;
fresh-main reconciliation is required for each publisher in the order it
actually enters the group.

The HRRR sandbox and wind watchdog use separate groups and
`cancel-in-progress: true`; they are safe to supersede because they do not
directly publish product commits. The groundwater preview has no production
write permission.

## R runtime setup

All fifteen production writers and the two R-based support workflows call the
repository-owned
[`setup-r-hardened`](../.github/actions/setup-r-hardened/action.yml) composite
action. It fixes the hosted runtime at R `4.6.1`, the version demonstrated by
healthy recent `ubuntu-24.04` runs, and makes at most three
`r-lib/actions/setup-r@v2` attempts with 5- and 15-second backoff.

An exact version does not remove r-hub from `setup-r`: the action still requests
`api.r-hub.io/rversions/resolve/4.6.1/...` to obtain the platform package URL.
The bounded attempts materially reduce a transient resolver single point but do
not make a sustained r-hub outage successful. The floating `release` alias is
not used because it would also permit an unreviewed runtime change.

APT hardening is a separate layer. Each affected job still runs
`scripts/configure_apt_mirror_order.sh` before R setup; the helper, subsequent
`apt-get` commands, and product package declarations are unchanged. An R
resolver failure should not be classified as an Ubuntu mirror failure merely
because `setup-r` later performs package-system work after resolution succeeds.

Advance the R runtime deliberately in one reviewed change: identify the target
release, verify its Ubuntu 24.04 package and repository-wide dependency/test
compatibility, update the single composite-action pin, and use the minimum
hosted canary described by the change. Do not change workflows back to
`release` or add an automated floating-version updater.

## Scheduled and manual execution

Schedules are owned by the workflow files and summarized in
[PRODUCTS.md](PRODUCTS.md).

Every production publisher job is gated to `main`; a feature-branch dispatch
can produce runner/artifact evidence but cannot push even to that feature branch.
Most writers have no separate confirmation input, so a manual run on `main` can
publish after its gates pass. CNRFC, CBRFC and Winter Storm Levels expose a
`publish` boolean that defaults false and use temporary dry-run output. No
production publisher job declares a GitHub Environment approval gate. David's
manual production approval is therefore a required process control.

Winter Storm Levels also runs at two guarded UTC offsets for each selected NBM
00/06/12/18 cycle:

- primary: 02:08, 08:08, 14:08 and 20:08 (+128 minutes);
- fallback: 02:54, 08:54, 14:54 and 20:54 (+174 minutes).

The primary and fallback use the same producer discovery/completeness semantics
through an inventory-only preflight. `NEW_CYCLE` continues to the full producer
and normal official publication path. `SOURCE_NOT_READY` and `NO_NEW_CYCLE` are
successful guarded no-ops; the fallback is therefore normally expected to
no-op after primary success. `SOURCE_ERROR` fails visibly without starting the
producer. There is no internal polling. Manual `publish: true` on `main` uses
the same guard, while manual `publish: false` preserves diagnostic current-cycle
rebuild behavior.

The seeded NBM QPF workflow has primary attempts at 02:18, 08:18,
14:18 and 20:18 (+138 minutes) with fallbacks at 03:10, 09:10, 15:10 and 21:10
(+190 minutes). The primary remains ten minutes behind the established Snow
Levels source-readiness probe, while the fallback avoids the start-of-hour
high-load period. Manual dispatch and official publication remain maintainer
decisions.

Before any manual writer dispatch, the operator must record:

- reason and expected product;
- selected branch/ref;
- whether the intent is feature-branch evidence or official publication;
- current official product health;
- expected artifact and product paths;
- approval for official publication;
- how success and failure will be verified.

The wind watchdog reads wind manifests/state and may dispatch ASOS/AWOS, GFS,
HRRR, or NBM writers on the selected ref when checks indicate a repair is
needed. It does not itself commit or publish repository data. A manual watchdog
run on a feature branch can therefore initiate feature-branch writers.

Before dispatching a stale product, the watchdog queries that target workflow's
runs on the selected branch. It suppresses repair when an equivalent run is
queued, requested, waiting, pending, or in progress, or when a successful run
completed less than 15 minutes earlier. Failed and cancelled completed runs do
not start that cooldown. This run-history check also prevents a following
watchdog execution from repeating a dispatch once the first target run is
visible.

On `main`, the same shared decision policy applies bounded UTC schedule grace.
ASOS/AWOS defers for 15 minutes before and 5 minutes after its normal slots.
Hourly GFS and HRRR defer for 10 minutes before and 45 minutes after. Six-hourly
NBM defers for 15 minutes before and 45 minutes after. These windows leave at
least one nominal watchdog check outside grace for every product, so a stale
feed with no viable normal run still receives a last-resort repair opportunity.
Feature-branch watchdog runs do not apply `main`'s scheduled-run grace because
normal writer schedules target `main`.

The run-history GET has three bounded attempts. If all fail, the watchdog logs
`STALE_SUPPRESSED_RUN_STATE_UNAVAILABLE`, withholds that product's dispatch, and
fails visibly so a later scheduled watchdog can retry. The dispatch POST is not
automatically retried: a timeout can be ambiguous after GitHub accepts the
request, and blindly repeating it could create the duplicate the guard is meant
to prevent. Normal results use `HEALTHY_NO_ACTION`, `STALE_DISPATCHED`,
`STALE_SUPPRESSED_ACTIVE_RUN`, `STALE_SUPPRESSED_RECENT_RUN`, or
`STALE_SUPPRESSED_IMMINENT_SCHEDULE` and include bounded run or schedule detail.

## Sandbox and preview

### HRRR sandbox

`.github/workflows/build-hrrr-wind-sandbox.yml` is manual, uses
`contents: read`, writes debug/QA output, and uploads `hrrr-wind-sandbox`. It
has no Git publication step.

### Groundwater preview

`.github/workflows/preview-usgs-groundwater-candidates.yml` is weekly/manual,
uses `contents: read`, writes a review artifact, and does not update the
candidate index or official groundwater product.

Sandbox/preview artifacts are evidence, not official products. Copying their
contents into `docs/data` is a separate publication change requiring review.

## Actions artifact inventory

Artifacts are temporary run evidence, not official products or an independent
durable backup. Default retention is controlled by repository/platform settings
and can change. Git publication and Pages deployment remain separate stages.

| Artifact | Producing workflow/platform | Purpose | Baseline retention |
|---|---|---|---|
| `asos-awos-wind-qa` | ASOS/AWOS production workflow | Build QA and diagnostics | Default/observed repository retention, approximately 90 days |
| `gfs-wind-qa` | GFS production workflow | Build QA and diagnostics | Default/observed repository retention, approximately 90 days |
| `hrrr-wind-qa` | HRRR production workflow | Build QA evidence | Explicit 14 days |
| `nbm-wind-guidance-qa` | NBM production workflow | Build QA evidence | Explicit 14 days |
| `hrrr-wind-sandbox` | HRRR sandbox workflow | Nonproduction review output | Explicit 14 days |
| `usgs-groundwater-candidate-discovery-preview` | Groundwater preview workflow | Nonproduction candidate-discovery review | Explicit 30 days |
| `major-water-supply-basin-forecasts-qa` | CNRFC forecast writer | Per-page retrieval/parser and acceptance diagnostics; no source-page bodies | Explicit 14 days |
| `cbrfc-major-water-supply-forecasts-qa` | CBRFC Colorado River forecast writer | Three-record point/list/dashboard and Lake Mead Local current/archive-evidence retrieval, parser and per-family acceptance diagnostics; dry-run payload when applicable; no source bodies | Explicit 14 days |
| `winter-storm-levels-qa` | Winter Storm Levels writer | Complete manifest/GeoJSON bundle and browser QA when a build starts; concise inventory-preflight diagnostics for guarded no-build outcomes; no full GRIB files | Explicit 14 days |
| Product-specific `*-candidate-<run>-<attempt>` | All fifteen production workflows; exact prefixes are in their upload/download steps | Validated product candidate plus inventory/hash/source metadata crossing the prepare/publish boundary; metadata is artifact-only, not secret or a public feed envelope | Explicit 2 days |
| `nbm-qpf-qa-<run>-<attempt>` | Seeded NBM QPF workflow | R producer preflight and validation diagnostics; no official publication status | Explicit 14 days |
| `github-pages` | GitHub-controlled Pages deployment | Platform deployment bundle, not a feed product | Observed short platform retention, approximately one day at the audit baseline |

An Actions artifact may disappear while its associated Git commit remains.
Conversely, a successful QA artifact does not prove that Git publication or Pages
deployment completed.

## Current QA gates

The repository has product-specific, not universal, QA:

- ASOS requires a minimum feature count and valid recent wind observations.
- CDEC validates usable latest/daily storage and minimum parsed coverage. Its
  product publisher revalidates feature/count/time/geometry invariants before
  artifact creation and after fresh-main staging, and proves artifact hashes and
  the exact two-path allowlist without reserializing product bytes.
- CoCoRaHS retries and deduplicates. After latest-report selection it omits zero
  through 10 observations with unusable station names before the CA/CONUS split;
  11 or more fails closed. Every run logs before/omitted/after counts, threshold
  10, and at most 10 station/report identifiers. This narrow malformed-metadata
  policy does not supply the still-missing overall completeness threshold.
- Delta checks HTTP status, MIME, minimum size, leading PDF magic and terminal
  EOF before Poppler, then validates report date and a minimum feature set.
  The product-specific retrieval policy in [PRODUCTS.md](PRODUCTS.md#3-delta-operations)
  bounds transient retries to three attempts. Attempt logs include status,
  normalized MIME, byte count, SHA-256, source/final URL and validation outcome;
  response bodies and raw request exceptions are omitted, URL userinfo/query/
  fragment values are removed and logged values are length-limited.
- GFS validates downloads/JSON and at least one entry, but can retain partial
  target coverage.
- HRRR validates GRIB inventory, grid orientation/dimensions and target
  proximity.
- NBM validates field sets, grid coverage, masks and retained point counts.
- SCAN validates endpoint access, canary retrieval and station coverage.
- Snow attempts AWDB and CDEC independently; validates provider-specific
  row/site coverage, required fields, dates, SWE bounds, source selection and
  duplicates; requires NRCS coverage of at least 90% of indexed stations and
  90% of expected station-days and CDEC coverage of at least 92% of indexed
  stations and 85% of expected station-days; validates any required prior
  four-file product set; and reruns combined latest/trace QA before replacement.
  A response near 20% station or row coverage is a provider failure. Environment
  overrides may tighten these gates but invalid, impossible, or weaker values
  stop the build.
- Groundwater validates index, API and output counts, requires every request
  chunk to complete as usable data or a valid empty response, aggregates
  structured request/parser diagnostics, and stages and validates the GeoJSON
  and summary before rollback-capable promotion. Its API and output minimums
  remain 300 sites; candidate-index value fallback cannot satisfy or bypass the
  API gate.
- Streamflow's R builder gates its static station index. Its publication adapter
  additionally checks structure, geometry, live-value flags and matching summary
  counts, but neither enforces the workflow-declared current-discharge minimum.
  Consistent zero-live-value counts can still pass.
- Major water-supply basin forecasts validate the exact reviewed 51-record
  roster: 18 product-9 identities, 15 major-basin-only product-2 identities and
  18 product-7 April-July identities.
  Checks cover official LID/product identity, unique semantic labels, source
  units and times, ordered product-2 dates, finite/plausible values, cumulative
  nondecrease, source-time monotonicity, per-metric freshness/expiry, null
  sentinels, reconciled family health, deterministic payload ordering and a
  geometry-free schema. Product-7 checks also require unique headline/tabular
  semantics, direct-source fields and a matching 50%-exceedance volume.
  Bootstrap requires all 15 FNF, all three indices, all 14
  expected-available product-2 median series and explicit source-confirmed MHBC1
  product-2 unavailability, plus all 18 product-7 identities while active or
  explicit source-unavailable records out of season. Once a valid prior exists, current-success counts are
  health/alerting signals rather than publication gates: the writer publishes a
  complete degraded 51-record snapshot unless structural or staged validation
  fails.
- The separate CBRFC writer validates exactly three ordered GLDA3/LKSA3
  structural records and separate family health for April-July, water year and
  Lake Mead Local monthly outlooks. April-July
  accepts public values only from official `off*` fields with a valid
  date-precision issue; its point endpoint remains authoritative when the
  secondary list omits a newer valid issue. The server-rendered Lake Powell
  dashboard cross-checks same-issue Apr-Jul volume and percent average at its
  displayed precision; omission, maintenance or lag creates an operational
  notice, while material current disagreement fails validation. Water year
  accepts only direct `Full
  Fcst` and `%Avg` values from the unique semantic `Water Year` row on the
  official situational-awareness page, and never infers percent median. Both
  adapters reject malformed/oversized/error responses, identity/year/unit/type/
  period drift, negative/nonfinite values, backward dates and ambiguous source
  structures. The local adapter requires exact source labels, one LKSA3 identity
  and 12 source months, and never sums them. Its sole reviewed date correction
  changes raw `January 2026` to `2027-01` only for the exact July 1, 2026 issue
  after the official June 1 archive confirms January 2027; the payload preserves
  complete override provenance and an operational notice. Future monthly items
  are popup-only `not_yet_valid`; only the valid current month is map eligible
  while source freshness holds. Bootstrap may establish
  one valid family while others remain explicit failed/unavailable structural
  records; it fails if no active family establishes official data.
- Winter Storm Levels requires one complete NBM cycle with all 41 configured
  horizons (f001 plus f006 through f240 every six hours). It validates exact
  deterministic `SNOWLVL` inventory identity and
  byte ranges, metre units, decoded valid time/CRS, at least 95% finite grid
  coverage, physical range, contour metadata/bounds, stable target structure,
  manifest paths/checksums/sizes, and unique cycle/valid identities. Only
  transport errors and HTTP 408/429/500/502/503/504 receive bounded retries.
  Wrong field/level/unit/time, malformed content, partial horizon coverage, or
  unsafe bundle structure fails and preserves the accepted set. A first seed
  must be one complete current cycle with an active target. Every genuinely
  newer mature publication strictly validates the prior canonical bundle and
  produces exactly two complete cycles; malformed, unsafe, missing,
  checksum-invalid, or uncopyable retained state fails instead of publishing a
  one-cycle degradation.
- NBM QPF requires one complete 40-lead deterministic surface-APCP cycle, f006
  through f240 every six hours, with a lossless WebP and numeric grid per target.
  The R producer and Python validator check source/time identity, complete paired
  targets, decode/dimensions, palette, numeric encoding, bounds and hashes.
  Bootstrap is one explicit complete cycle; steady state retains exactly current
  and previous complete cycles (80 paired targets). The precisely validated
  legacy ten-lead state is only a migration source for a newer complete cycle.

The CoCoRaHS completeness gap and streamflow current-value gap mean "workflow
success" is not universally equivalent to "complete current product."

## Last-known-good matrix

| Failure point | Current remote behavior | Prior official product |
|---|---|---|
| Upstream retrieval stops the script | No commit step | Remains in Git and Pages |
| Delta receives persistent non-PDF bytes or exhausts retrieval retries | `DELTA_OPS_UPSTREAM_NON_PDF_RESPONSE`, `DELTA_OPS_UPSTREAM_HTTP_RESPONSE` or `DELTA_OPS_UPSTREAM_TRANSPORT_FAILURE` stops preparation before output writes/candidate upload; publication is skipped | Prior canonical Delta four-file product remains unchanged |
| CoCoRaHS has 11 or more retained observations without a usable station name, or its strict candidate validator rejects any feature | Candidate construction/publication stops; diagnostics report the bounded station-name omission evidence | Prior canonical four-file product remains unchanged in Git and Pages |
| One or more CNRFC pages fail after a valid prior exists | Successful families/records advance; failed metrics become unexpired `stale_last_known_good`, `expired`, explicit source `unavailable`, or `failed_no_data` | A complete, honestly degraded 51-record snapshot may replace the prior; values retain their original source and successful-retrieval provenance |
| All CNRFC families fail after a valid prior exists | Complete 51-record degraded snapshot is constructed; family health becomes `outage_using_last_known_good` where provenance values remain, otherwise `unusable` | Prior values may remain as non-map LKG/expired provenance; the prior payload is not left online falsely marked current |
| First CNRFC bootstrap lacks any required 15 FNF, three index, 14 expected-available product-2 median series, MHBC1 source-unavailable confirmation, or 18 valid/seasonally unavailable product-7 identities | No canonical replacement or commit step | No canonical seed is created |
| CNRFC roster/schema/serialization/staged validation fails | No canonical replacement or commit step | Prior canonical bytes remain unchanged |
| One CBRFC family attempt fails after a valid prior exists | Only that family becomes unexpired `stale_last_known_good`, `expired`, explicit source `unavailable`, or `failed_no_data` as evidence permits; independently successful families advance | A complete, honestly degraded three-record snapshot may replace the prior; no family erases another's validated provenance |
| First CBRFC bootstrap establishes at least one valid family, including expired period provenance or the year-round monthly series | The valid family is accepted and the others remain explicit `failed_no_data` or source-unavailable structural records; CNRFC is unaffected | A complete, honestly partial three-record CBRFC seed can be created year-round |
| First CBRFC bootstrap establishes no official family and an active family has an unexplained fetch/parse/validation failure | No CBRFC canonical replacement or commit step; CNRFC is unaffected; the failure is not relabeled out of season | No CBRFC canonical seed is created |
| CBRFC roster/schema/serialization/staged validation fails | No CBRFC canonical replacement or commit step; CNRFC is unaffected | Prior CBRFC canonical bytes remain unchanged |
| Winter Storm Levels source is unavailable, its variable is missing, fetch/decode/validation/publication fails, or any of 41 horizons is missing | Attempt diagnostics preserve the distinct failure class; no manifest promotion or commit step | Prior manifest and referenced contours remain unchanged and age naturally into delayed, stale LKG, then expired states |
| Winter Storm Levels scheduled preflight finds no newer complete cycle | `NO_NEW_CYCLE` or `SOURCE_NOT_READY`; geospatial setup, producer, and publication transaction do not run | Prior manifest and referenced contours remain unchanged; fallback may make one later inventory-only attempt |
| Winter Storm Levels candidate is unchanged except retrieval time | Semantic no-op; no data commit | Prior accepted bytes remain authoritative |
| NBM QPF preflight/candidate/public validation fails, has an incomplete image/numeric pair, or conflicts with the same canonical cycle | No publication transaction or commit | Prior manifest and exact referenced paired assets remain unchanged |
| NBM QPF candidate is SAME or STALE relative to fresh canonical `main` | Shared publisher succeeds as a no-op | Current and previous canonical cycles do not regress |
| Exactly one snow provider fails and valid prior rows exist | Snow publishes a partial-refresh commit containing fresh healthy-provider rows and unchanged prior failed-provider rows, with additive summary status | Failed provider remains last-known-good; healthy provider advances |
| Both snow providers fail, or failed-provider prior rows are invalid/unavailable | No snow commit step | Entire prior snow product remains |
| Transformation error stops the script | No commit step | Remains |
| Implemented QA gate fails | No commit step | Remains |
| Wind QA artifact upload fails | Later default-success commit step is skipped | Remains |
| No artifact files with `if-no-files-found: ignore` | Artifact step succeeds without files | Product may still publish |
| Branch advances before push | Rejected attempt makes no remote update; the shared publisher reconciles once more against fresh `main` | A valid new candidate may advance on retry; same/stale becomes a no-op, while a second race or failure leaves the then-current remote state intact |
| Product commit succeeds, Pages fails | Git has new product; hosted site may remain older | Git and hosted states diverge temporarily |
| Script produces a degraded result that passes its checks | Commit may succeed | Prior product is replaced |
| Local script fails after direct writes | Local checkout may contain partial files; snow and groundwater are product-specific staged-replacement exceptions | Remote remains until a later push |

Last-known-good protection is therefore partial, not universal.

## Atomicity

At the remote Git-ref boundary, a successful push advances all committed product
paths together. A rejected push updates none of them.

The scripts do not universally construct complete product sets in a separate
staging directory and atomically rename them into place. Snow, groundwater, and
Winter Storm Levels are product-specific exceptions: each constructs and
validates its complete prospective output set before promotion. Snow and
groundwater preserve backups and restore already-promoted members if a later
replacement fails. Winter Storm Levels promotes immutable cycle targets first
and atomically renames the validated manifest last; a pre-manifest failure
removes new unreferenced targets and leaves the prior manifest bytes unchanged.
The filesystem still does not provide one atomic multi-path rename. Other local
manual runs can alter final `docs/data` paths before later processing fails.

Safe local reproduction uses:

- a disposable worktree or isolated copy;
- explicit output overrides where the producer supports them;
- review of every changed product path;
- no push or Pages action.

Do not claim local writes are atomic merely because Git publication is.

## Failure investigation

Start with evidence, not dependency upgrades.

Record:

1. Workflow display name and file.
2. Run ID and URL.
3. Trigger, ref, event SHA, preparation HEAD, publisher callback SHA and each
   transaction's fresh-main base SHA, where retained evidence supplies them.
4. Job and first meaningful failing step.
5. Minimal relevant log excerpt.
6. Most recent comparable successful run.
7. Upstream status/schema evidence.
8. Runtime, action or dependency difference.
9. QA artifact status.
10. Git branch/product status.
11. Hosted Pages status.
12. Private BRIM user impact.
13. Cause classification.
14. Minimal proposed fix.
15. Nonproduction verification.
16. Rollback/recovery proof.

Cause classifications:

- upstream unavailable;
- upstream schema/format change;
- authentication/permissions;
- rate limit;
- runtime/dependency;
- action version;
- transformation;
- QA;
- timeout/resource;
- artifact;
- Git publication;
- workflow configuration;
- consumer contract.

Notable incidents and their evidence limits are summarized in
[DEVELOPMENT_HISTORY_AND_RISKS.md](DEVELOPMENT_HISTORY_AND_RISKS.md).

## Incident playbooks

### Upstream outage or timeout

For Delta, an HTTP 200 response can still be a DWR rejection HTML page.
Historical [PR #40](https://github.com/dbo99/brim-live-data-feeds/pull/40) supplied
transport-hardening evidence; its old availability observation is not evidence
of a continuing outage. The current offline fixtures reproduce the rejection
without contacting DWR. Bounded retries improve transient recovery and diagnosis
but cannot restore access during a sustained upstream rejection. Neither the
current implementation nor PR #40 contains Gmail/email ingestion; a dedicated
email contingency remains separate future work, with no automatic CWC fallback.
Same-report-date candidates remain publisher no-ops, including manual attempts.

1. Confirm the first failing request and upstream provider.
2. Compare with a successful run and provider status.
3. Confirm no product commit occurred.
4. Inspect official manifest/summary age and Pages availability.
5. Avoid reducing minimums or enabling degraded publication merely to obtain a
   green run.
6. Retry only when provider recovery or the existing retry policy justifies it;
   obtain David's approval for a manual production dispatch or rerun.

### Upstream schema change

1. Retain a minimal sanitized response/schema diff where policy allows.
2. Identify the first missing/changed field and affected transformations.
3. Test parser changes in isolated output or an artifact.
4. Verify empty/partial responses still fail safely.
5. Coordinate private BRIM if serialized output changes.

### Stale product

1. Distinguish stale upstream observations from a stale producer build and a
   stale Pages deployment.
2. Check writer, watchdog, manifest/summary and Pages statuses.
3. Verify the product's actual freshness semantics; build time alone may be
   insufficient.
4. Dispatch production only with explicit approval.

### Git push rejection

1. Treat rejection as last-known-good protection.
2. Do not force, pull, merge or rebase generated output in the runner.
3. For any production writer, inspect both bounded attempts and distinguish a
   successful retry, safe no-op, second race or non-race failure.
4. Identify the intervening remote commit and compare the product's current
   semantic state; do not assume every branch advance changes this product.
5. If a fresh product is still needed, seek David's approval for a new manual
   run from the intended latest branch tip. Do not extend the retry loop or
   weaken freshness/completeness checks as an incident workaround.

### Artifact failure

1. Determine whether the artifact is a publication gate or diagnostic only.
2. Confirm official Git paths were not committed.
3. Check path expressions, retention and artifact-action/runtime annotations.
4. Do not change product publication solely to bypass missing QA evidence.

### Permission failure

1. Compare requested workflow permission with the failing operation.
2. Check repository policy/settings read-only.
3. Do not broaden token permissions globally without review.
4. Validate the narrowest change on a nonproduction path.

### Pages failure

1. Compare Git `main` product commit with deployed Pages status/content.
2. Treat source publication and web deployment as separate incident stages.
3. Avoid rewriting correct products merely to retrigger Pages without evidence.
4. Verify consumer URLs after recovery.

## Rollback

The normal rollback unit is an auditable Git commit, not a local file copy.

For a bad official product:

1. Identify the last compatible product commit and all paths in the product
   set.
2. Determine whether the producer schema/code or only generated data is wrong.
3. Confirm private BRIM compatibility with the intended restored product.
4. Prepare a forward or revert commit on a reviewed branch.
5. Obtain explicit approval for official publication.
6. Verify Git `main`, Pages content, manifest/summary and BRIM behavior.

Do not restore one file from a multi-file product while leaving its manifest or
summary inconsistent.

## Backup and recovery model

### Local Git-index recovery

A suspicious local index or lock is not evidence of a remote feed incident or
of a particular actor. Start read-only from the actual worktree root:

```sh
git rev-parse --show-toplevel
git rev-parse --git-dir --git-common-dir --git-path index
git rev-parse HEAD
git status --short --branch
git diff --name-only
git diff --cached --name-only
git ls-files --others --exclude-standard
```

Check whether `GIT_INDEX_FILE`, `GIT_DIR` or `GIT_WORK_TREE` overrides are set
without dumping the environment. In a linked worktree, `.git` is normally a
pointer file and the index belongs to that worktree's Git directory; never assume
the canonical checkout's index is the repair target.

Before an approved repair, preserve the index/lock evidence, available staged and
unstaged patches, and independent copies of affected tracked/untracked files
outside the public repository. If a Git read fails, retain its sanitized error
and preserve files directly. A lock may belong to an active Git process: establish
ownership before any removal. Do not reset, clean, rebuild an index or switch
branches merely to make status clean. Agree on the exact repair with David after
preservation, then reconcile saved work against the intended current base file by
file, verify staged intent and unrelated files, and review the resulting diff.
Local repair does not authorize a push, rollback or production dispatch.

### Backup coverage

| Asset | What protects it now | Limitation |
|---|---|---|
| Workflow/script history | Git/GitHub history | No independent archive documented |
| Tracked products/manifests | Git history and current Pages source | High-frequency history grows; Pages is not a backup service |
| Hosted last-known-good | Last successful Pages deployment | Deployment retention/restore is not a contract |
| Actions logs/artifacts | GitHub retention | Temporary; retention varies by workflow |
| Static/manual context | Git plus external preparation workspace | Refresh provenance is split |
| Integration/recovery ASOS RTW020 material | Separate recovery material | Not canonical Git history; vulnerable to drift |
| Private BRIM compatibility | Separate private Git history | Does not archive public product payloads |

Recommended independent baseline contents:

- repository bundle or mirror;
- current product/manifests with SHA-256 checksums;
- workflow/product commit map;
- static-context provenance;
- private BRIM compatibility note;
- restore instructions and a dated validation record.

Any future synchronization of an integration copy must first preserve
integration-only files in a dated recovery bundle with hashes. The unreviewed
RTW020 ASOS producer is the current known example.

## Recovery verification

A recovery is complete only when:

- repository and product commits are identified;
- every file in the product set parses;
- manifest relative paths resolve;
- counts/sizes are plausible but not treated as sole QA;
- timestamps have their documented meaning;
- Pages serves the intended bytes;
- private BRIM loads the product and handles stale/missing behavior;
- no unrelated product or source path changed;
- the recovery action is auditable.

## Incident report

Use this compact final structure:

```text
Incident:
Workflow/run/ref:
First meaningful failure:
Classification:
Upstream evidence:
Artifact status:
Git product status:
Pages status:
BRIM impact:
Last-known-good proof:
Minimal fix:
Nonproduction validation:
Rollback/recovery:
Remaining uncertainty:
```
