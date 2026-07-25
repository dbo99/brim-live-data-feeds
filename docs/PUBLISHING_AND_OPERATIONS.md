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

1. A scheduled writer retrieves, transforms and validates data, stages only its
   declared `docs/data/...` paths, commits them, and pushes to the branch that
   triggered the run.
2. GitHub Pages separately deploys `main:/docs`.

The public URL is `https://dbo99.github.io/brim-live-data-feeds/`.

A successful product commit and a successful Pages deployment are related but
not the same event. During a Pages delay or failure, Git `main` may contain a
newer product than the hosted site.

## Branch-safe writer behavior

All eleven scheduled writers currently:

- support schedules and manual dispatch;
- request `contents: write`;
- use the shared concurrency group
  `live-data-feed-writes-${{ github.ref }}`;
- set `cancel-in-progress: false` and `queue: max`;
- check out `ref: ${{ github.ref }}` with full history;
- reject non-branch publication refs;
- stage declared product paths;
- commit only when those paths changed;
- push non-force to `HEAD:${GITHUB_REF}`;
- fail if the branch advanced unexpectedly.

They do not pull, merge or rebase generated-product commits.

Consequences:

- Scheduled events run from the default branch and can update official
  `main`.
- Manual dispatch can target a feature branch and will write the normal product
  paths on that feature branch.
- A feature-branch run cannot update `main` through the writer's push command.
- GitHub Pages publishes only `main:/docs`, so feature-branch products are not
  official hosted products.
- No producer workflow runs on `pull_request`.

`main` is currently not protected by a branch protection rule or ruleset. These
workflow properties are therefore important procedural safeguards, not a
replacement for repository-level protection.

## Concurrency, cancellation and queueing

All production writers share one group per ref. On `main`, that serializes
product-writing jobs so two writers do not push at the same time.

`queue: max` allows multiple pending runs instead of replacing the existing
pending run. Ordering should not be treated as a data guarantee. Every queued
writer re-resolves the selected branch when the job begins.

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
| `github-pages` | GitHub-controlled Pages deployment | Platform deployment bundle, not a feed product | Observed short platform retention, approximately one day at the audit baseline |

An Actions artifact may disappear while its associated Git commit remains.
Conversely, a successful QA artifact does not prove that Git publication or Pages
deployment completed.

## Current QA gates

The repository has product-specific, not universal, QA:

- ASOS requires a minimum feature count and valid recent wind observations.
- CDEC validates usable latest/daily storage and minimum parsed coverage.
- CoCoRaHS retries and deduplicates, but lacks a strong completeness threshold.
- Delta validates PDF structure, date and a minimum feature set.
- GFS validates downloads/JSON and at least one entry, but can retain partial
  target coverage.
- HRRR validates GRIB inventory, grid orientation/dimensions and target
  proximity.
- NBM validates field sets, grid coverage, masks and retained point counts.
- SCAN validates endpoint access, canary retrieval and station coverage.
- Snow validates provider access and output/trace coverage.
- Groundwater validates index, API and output counts.
- Streamflow validates only its static station index; the workflow's declared
  minimum current-discharge threshold is not implemented by the script.

The final two gaps mean "workflow success" is not universally equivalent to
"complete current product."

## Last-known-good matrix

| Failure point | Current remote behavior | Prior official product |
|---|---|---|
| Upstream retrieval stops the script | No commit step | Remains in Git and Pages |
| Transformation error stops the script | No commit step | Remains |
| Implemented QA gate fails | No commit step | Remains |
| Wind QA artifact upload fails | Later default-success commit step is skipped | Remains |
| No artifact files with `if-no-files-found: ignore` | Artifact step succeeds without files | Product may still publish |
| Branch advances before push | Non-force push is rejected | Remains |
| Product commit succeeds, Pages fails | Git has new product; hosted site may remain older | Git and hosted states diverge temporarily |
| Script produces a degraded result that passes its checks | Commit may succeed | Prior product is replaced |
| Local script fails after direct writes | Local checkout may contain partial files | Remote remains until a later push |

Last-known-good protection is therefore partial, not universal.

## Atomicity

At the remote Git-ref boundary, one successful commit updates all staged paths
together. A rejected push updates none of them.

The scripts do not universally construct complete product sets in a separate
staging directory and atomically rename them into place. A local manual run can
therefore alter final `docs/data` paths before later processing fails.

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
  prior official product remained during the failed run.
- NBM run `29891617635` built seven entries and uploaded QA, then hit conflicts
  while rebasing a stale event-SHA product commit. PR #1 replaced rebase
  publication with latest-selected-branch checkout and reject-on-advance push.
- A Node 20 annotation on older artifact runs led to PR #4, which moved every
  source `actions/upload-artifact` reference to `@v7`. Scheduled ASOS/GFS runs
  have since exercised the updated action; preview and HRRR sandbox still need
  a future nonproduction post-change run for runtime evidence.

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
3. Confirm the remote branch advanced and determine which writer committed.
4. Run again from the latest branch tip if a fresh product is still needed.

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
