# Publishing and operations

This runbook describes current GitHub Actions publication, last-known-good
behavior, incident response, rollback and recovery for the public live-feed
repository.

It distinguishes observed controls from recommended future controls. Following
this document does not grant permission to dispatch a production writer,
publish a product, merge a pull request or change repository settings.

Baseline audited: 2026-07-24; the audited commit is recorded in
[README.md](README.md).

## Official publication architecture

The official product has two stages:

1. A scheduled writer retrieves, transforms and validates data. Thirteen
   writers still stage their declared `docs/data/...` paths, commit them, and
   push to the selected branch when their product-specific publication guard
   allows. The Phase 1 CDEC canary instead
   prepares a validated two-file candidate artifact; only its separate
   `main`-authorized publisher job can reconcile and commit that candidate.
2. GitHub Pages separately deploys `main:/docs`.

The public URL is `https://dbo99.github.io/brim-live-data-feeds/`.

A successful product commit and a successful Pages deployment are related but
not the same event. During a Pages delay or failure, Git `main` may contain a
newer product than the hosted site.

## Branch-safe writer behavior

All fourteen scheduled production writers currently:

- support schedules and manual dispatch;
- use the shared concurrency group
  `live-data-feed-writes-${{ github.ref }}`;
- set `cancel-in-progress: false` and `queue: max`;
- follow their documented checkout/ref contract with full history;
- preserve product-specific validation and last-known-good behavior.

Thirteen unmigrated writers still request `contents: write`, reject
non-branch publication refs, stage their declared product paths, commit only
when those paths changed, push non-force to `HEAD:${GITHUB_REF}`, and fail if
the branch advanced unexpectedly.

The CDEC canary has a read-only `prepare-candidate` job and a write-capable
`publish-to-main` job separated by a short-lived Actions artifact. The artifact
contains exactly the two CDEC product files plus nonpublic integrity metadata:
an internal product identifier, source event SHA, preparation time, semantic
`feed_build_time_utc`, exact inventory, byte counts, and SHA-256 hashes. The
publisher validates that artifact, fetches current `origin/main` after entering
the publication queue, and invokes the CDEC callback to classify the candidate
as new, same, or stale. Only a new candidate is copied byte-for-byte and staged;
same and stale candidates are successful no-ops. A non-fast-forward push gets
at most one fresh-main reconciliation retry. A second race, an unexpected path,
an ambiguous same-time byte change, or any validation failure stops without a
force push. Feature-branch CDEC dispatches now produce the candidate artifact
but cannot publish any branch.

They do not pull, merge or rebase generated-product commits.

Winter Storm Levels uses the same shared non-cancelling concurrency and
normal-push contract. Manual dispatch defaults to runner-temporary dry-run
output and explicit publication is accepted only from `main`. Its primary and
fallback schedule paths perform an inventory-only newer-cycle preflight before
installing geospatial dependencies or starting the producer.

The established writers retain their existing documented checkout behavior.
The two major-basin forecast workflows select the current `main` branch tip for
scheduled runs and the explicitly dispatched branch tip for manual dry runs.
Manual publication for either major-basin feed remains permitted only from
`main`; a feature-branch dispatch remains nonpublishing.
Winter Storm Levels likewise checks out the selected branch tip and cannot
publish from a feature branch. A scheduled event resolves to guarded official
publication only when preflight finds a strictly newer complete cycle.

Consequences:

- Scheduled events run from the default branch and can update official
  `main`.
- Manual dispatch can target a feature branch. Thirteen unmigrated writers
  retain their documented branch behavior; the CDEC canary produces only its
  validated candidate artifact on a feature branch.
- A feature-branch run cannot update `main` through the writer's push command.
- GitHub Pages publishes only `main:/docs`, so feature-branch products are not
  official hosted products.
- No producer workflow runs on `pull_request`.

`main` has the active `main-history-safety` ruleset, which blocks deletion and
non-fast-forward history with no bypass actors. Classic branch protection and
required pull-request review/status checks remain absent. These workflow
properties are therefore important safeguards, not a replacement for the
remaining repository-level controls.

## Concurrency, cancellation and queueing

All production writers retain one whole-workflow group per ref. On `main`, that
continues to serialize the migrated CDEC canary with every unmigrated writer
during the mixed migration state.

`queue: max` allows multiple pending runs instead of replacing the existing
pending run. Ordering should not be treated as a data guarantee. Every queued
writer re-resolves the selected branch when the job begins.

The CDEC `publish-to-main` job additionally uses the constant repository-wide
group `brim-live-main-publish` with `cancel-in-progress: false` and `queue: max`.
The new lock applies only to that publisher job. Retaining the old whole-workflow
lock is deliberate until every main writer uses the new publisher: it prevents a
migrated publisher from colliding with a still-direct writer. This Phase 1
canary therefore proves the artifact and fresh-main transaction boundaries but
does not yet provide parallel builds.

Initial Winter Storm Levels scheduling continues to hold only the established
whole-workflow lock. It is not migrated in Phase 1. Before NBM QPF is added, the
remaining writers require product-specific prepare/reconcile migrations; only
after the mixed state ends may the old whole-workflow lock be removed in a
separate reviewed change.

The HRRR sandbox and wind watchdog use separate groups and
`cancel-in-progress: true`; they are safe to supersede because they do not
directly publish product commits. The groundwater preview has no production
write permission.

## Scheduled and manual execution

Schedules are owned by the workflow files and summarized in
[PRODUCTS.md](PRODUCTS.md).

Manual dispatch is an explicit operator action, but current production writers
do not have:

- a confirmation input;
- a GitHub Environment approval;
- a `main`-only manual condition;
- an automatic nonproduction mode.

The two major-basin forecast writers and Winter Storm Levels are documented
exceptions: they expose a boolean publication input that defaults false, use
temporary output for dry runs, and refuse feature-branch publication.

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
| `cdec-reservoir-candidate-<run>-<attempt>` | CDEC production workflow | Validated two-file candidate plus nonpublic inventory/hash/source metadata crossing the prepare/publish job boundary | Explicit 2 days |
| `github-pages` | GitHub-controlled Pages deployment | Platform deployment bundle, not a feed product | Observed short platform retention, approximately one day at the audit baseline |

An Actions artifact may disappear while its associated Git commit remains.
Conversely, a successful QA artifact does not prove that Git publication or Pages
deployment completed.

## Current QA gates

The repository has product-specific, not universal, QA:

- ASOS requires a minimum feature count and valid recent wind observations.
- CDEC validates usable latest/daily storage and minimum parsed coverage. Its
  canary publisher revalidates feature/count/time/geometry invariants before
  artifact creation and after fresh-main staging, and proves artifact hashes and
  the exact two-path allowlist without reserializing product bytes.
- CoCoRaHS retries and deduplicates, but lacks a strong completeness threshold.
- Delta validates PDF structure, date and a minimum feature set.
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
- Streamflow validates only its static station index; the workflow's declared
  minimum current-discharge threshold is not implemented by the script.
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
- Winter Storm Levels requires one complete NBM cycle with all 11 configured
  horizons. It validates exact deterministic `SNOWLVL` inventory identity and
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

The CoCoRaHS completeness gap and streamflow current-value gap mean "workflow
success" is not universally equivalent to "complete current product."

## Last-known-good matrix

| Failure point | Current remote behavior | Prior official product |
|---|---|---|
| Upstream retrieval stops the script | No commit step | Remains in Git and Pages |
| One or more CNRFC pages fail after a valid prior exists | Successful families/records advance; failed metrics become unexpired `stale_last_known_good`, `expired`, explicit source `unavailable`, or `failed_no_data` | A complete, honestly degraded 51-record snapshot may replace the prior; values retain their original source and successful-retrieval provenance |
| All CNRFC families fail after a valid prior exists | Complete 51-record degraded snapshot is constructed; family health becomes `outage_using_last_known_good` where provenance values remain, otherwise `unusable` | Prior values may remain as non-map LKG/expired provenance; the prior payload is not left online falsely marked current |
| First CNRFC bootstrap lacks any required 15 FNF, three index, 14 expected-available product-2 median series, MHBC1 source-unavailable confirmation, or 18 valid/seasonally unavailable product-7 identities | No canonical replacement or commit step | No canonical seed is created |
| CNRFC roster/schema/serialization/staged validation fails | No canonical replacement or commit step | Prior canonical bytes remain unchanged |
| One CBRFC family attempt fails after a valid prior exists | Only that family becomes unexpired `stale_last_known_good`, `expired`, explicit source `unavailable`, or `failed_no_data` as evidence permits; independently successful families advance | A complete, honestly degraded three-record snapshot may replace the prior; no family erases another's validated provenance |
| First CBRFC bootstrap establishes at least one valid family, including expired period provenance or the year-round monthly series | The valid family is accepted and the others remain explicit `failed_no_data` or source-unavailable structural records; CNRFC is unaffected | A complete, honestly partial three-record CBRFC seed can be created year-round |
| First CBRFC bootstrap establishes no official family and an active family has an unexplained fetch/parse/validation failure | No CBRFC canonical replacement or commit step; CNRFC is unaffected; the failure is not relabeled out of season | No CBRFC canonical seed is created |
| CBRFC roster/schema/serialization/staged validation fails | No CBRFC canonical replacement or commit step; CNRFC is unaffected | Prior CBRFC canonical bytes remain unchanged |
| Winter Storm Levels source is unavailable, its variable is missing, fetch/decode/validation/publication fails, or any of 11 horizons is missing | Attempt diagnostics preserve the distinct failure class; no manifest promotion or commit step | Prior manifest and referenced contours remain unchanged and age naturally into delayed, stale LKG, then expired states |
| Winter Storm Levels scheduled preflight finds no newer complete cycle | `NO_NEW_CYCLE` or `SOURCE_NOT_READY`; geospatial setup, producer, and publication transaction do not run | Prior manifest and referenced contours remain unchanged; fallback may make one later inventory-only attempt |
| Winter Storm Levels candidate is unchanged except retrieval time | Semantic no-op; no data commit | Prior accepted bytes remain authoritative |
| Exactly one snow provider fails and valid prior rows exist | Snow publishes a partial-refresh commit containing fresh healthy-provider rows and unchanged prior failed-provider rows, with additive summary status | Failed provider remains last-known-good; healthy provider advances |
| Both snow providers fail, or failed-provider prior rows are invalid/unavailable | No snow commit step | Entire prior snow product remains |
| Transformation error stops the script | No commit step | Remains |
| Implemented QA gate fails | No commit step | Remains |
| Wind QA artifact upload fails | Later default-success commit step is skipped | Remains |
| No artifact files with `if-no-files-found: ignore` | Artifact step succeeds without files | Product may still publish |
| Branch advances before push | Non-force push is rejected | Remains |
| Product commit succeeds, Pages fails | Git has new product; hosted site may remain older | Git and hosted states diverge temporarily |
| Script produces a degraded result that passes its checks | Commit may succeed | Prior product is replaced |
| Local script fails after direct writes | Local checkout may contain partial files; snow and groundwater are product-specific staged-replacement exceptions | Remote remains until a later push |

Last-known-good protection is therefore partial, not universal.

## Atomicity

At the remote Git-ref boundary, one successful commit updates all staged paths
together. A rejected push updates none of them.

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
3. Trigger, ref, event SHA and checked-out SHA.
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

### Evidence-backed examples

- Snow run `29933106803` timed out on all three NRCS/AWDB preflight stations
  and stopped before the full fetch. A later comparable run succeeded; the
  prior official product remained during the failed run. The snow producer now
  continues to the CDEC attempt and can publish a partial refresh only when
  CDEC passes and the prior AWDB latest/trace rows and all four prior product
  files validate.
- NBM run `29891617635` built seven entries and uploaded QA, then hit conflicts
  while rebasing a stale event-SHA product commit. PR #1 replaced rebase
  publication with latest-selected-branch checkout and reject-on-advance push.
- A Node 20 annotation on older artifact runs led to PR #4, which moved every
  source `actions/upload-artifact` reference to `@v7`. Scheduled ASOS/GFS runs
  have since exercised the updated action; preview and HRRR sandbox still need
  a future nonproduction post-change run for runtime evidence.
- Groundwater run `30206927617` on July 26, 2026 sent all 38 outer chunks from
  the primary client into its direct-request fallback, produced zero raw rows
  and zero API-latest sites, then reported 50 or more warnings without exposing
  their individual classifications. The source and dependency versions matched
  successful July 25 and July 27 runs, while a July 29 run exposed one primary
  HTTP 502 that the direct path recovered. This supports a transient upstream
  response failure, likely widespread 5xx behavior, rather than a parser/filter
  rejection; the exact failed statuses and bodies cannot be recovered from the
  retained log. The unchanged 300-site gate prevented publication, the prior
  official product remained, and the next three scheduled runs succeeded.
  Groundwater now uses bounded retry classification, a three-consecutive-chunk
  circuit breaker, concise structured diagnostics, parser/filter accounting,
  all-chunk completeness, and staged two-file promotion.

## Incident playbooks

### Upstream outage or timeout

1. Confirm the first failing request and upstream provider.
2. Compare with a successful run and provider status.
3. Confirm no product commit occurred.
4. Inspect official manifest/summary age and Pages availability.
5. Avoid reducing minimums or enabling degraded publication merely to obtain a
   green run.
6. Retry only when provider recovery or the existing retry policy justifies it.

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
3. For the CDEC canary, confirm whether its one automatic fresh-main
   reconciliation retry published or completed as a safe no-op; there is no
   third attempt.
4. For an unresolved CDEC retry or an unmigrated writer, confirm the remote
   branch advanced and determine which writer committed.
5. Run again from the latest branch tip if a fresh product is still needed.

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
