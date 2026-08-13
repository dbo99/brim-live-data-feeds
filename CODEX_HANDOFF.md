# Codex Handoff

This file orients a new coding-agent session to the BRIM live-data feed
repository. It is a safety and routing guide, not permission to publish.

## Repository identity

The canonical local checkout may be:

```text
$HOME/Documents/brim-live-data-feeds_source_repo
```

Resolve the actual repository root with Git rather than assuming the example
exists. This repository is the public feed producer. A separate private BRIM
workspace is the consumer. Use the consumer only for read-only compatibility
verification unless the user explicitly scopes a consumer change; never copy its
source or internal-only inventory into this repository.

The official Pages source is `main/docs`. A feature-branch build is not official
publication.

## First five minutes

From the resolved repository root, inspect without mutating:

```sh
cd "$(git rev-parse --show-toplevel)"
git status --short --branch
git rev-parse --show-toplevel
git rev-parse HEAD
git diff --name-only
git diff --cached --name-only
```

Then:

1. Read the user request and list exact in-scope files/actions.
2. Read root [AGENTS.md](AGENTS.md).
3. Read the nearest nested `AGENTS.md` for every file to be changed.
4. Read [README.md](README.md) and [docs/README.md](docs/README.md).
5. Route product, consumer, operations, and standards questions to the four
   technical sources of truth.
6. Account for every existing modified/untracked file; preserve unrelated work.

Do not fetch, pull, switch branches, reset, clean, stage, commit, push, dispatch,
merge, alter Pages, or change repository settings unless the request explicitly
requires that action. Never infer publication authority from a request to inspect,
document, diagnose, or build locally.

## Source-of-truth routing

| Question | Authority |
|---|---|
| What products, paths, workflows, scripts, units, schedules, or gates exist? | [docs/PRODUCTS.md](docs/PRODUCTS.md) |
| What does BRIM load, and what is backward compatible? | [docs/BRIM_CONSUMER_CONTRACT.md](docs/BRIM_CONSUMER_CONTRACT.md) |
| How is a run dispatched, published, monitored, rolled back, backed up, or recovered? | [docs/PUBLISHING_AND_OPERATIONS.md](docs/PUBLISHING_AND_OPERATIONS.md) |
| What is current versus the target implementation standard? | [docs/WORKFLOW_AND_PRODUCT_STANDARDS.md](docs/WORKFLOW_AND_PRODUCT_STANDARDS.md) |
| How does the system fit together? | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| What defects and operational risks are known? | [docs/DEVELOPMENT_HISTORY_AND_RISKS.md](docs/DEVELOPMENT_HISTORY_AND_RISKS.md) |
| How can a builder be reproduced safely? | [BUILD.md](BUILD.md) |
| What security and disclosure rules apply? | [SECURITY.md](SECURITY.md) |
| What evidence should a proposed change contain? | [CONTRIBUTING.md](CONTRIBUTING.md) |

If documents disagree with tracked implementation, report the exact conflict.
Do not silently choose whichever description makes the task easier.

## Task routing

| Task | Evidence to inspect first | Primary documents | Typical validation |
|---|---|---|---|
| Failed runner | Failing step/log, run ref/SHA, prior healthy run, upstream status, branch/product state | Operations, product catalog, standards, risk history | Classify upstream/runtime/QA/Git/Pages stage; prove last-known-good |
| Add a new product | Upstream contract, intended consumer need, output set, schedule/cost, failure policy | Products, consumer contract, architecture, standards, contributing | ExecPlan; fixture/sandbox evidence; schema/path/units/time/null and rollback review |
| Change product schema or fields | Current artifacts, producer serialization, consumer reads/fallback | Consumer contract, products, contributing | Old/new fixtures; additive compatibility; empty/null/stale cases |
| Change workflow schedule | Current cron, runtime, upstream cadence, overlapping writers, freshness | Products, standards, operations | YAML parse; UTC schedule review; concurrency/runtime/staleness impact |
| Update dependency or GitHub Action | Every current occurrence, release/runtime notes, permissions, retained inputs | Standards, security, risk history | Exact affected workflows/builders; nonproduction run; warnings and artifact behavior |
| Change publication path | Current Git path, Pages URL, workflow staging, manifest/registry references | Consumer contract, operations, products | Producer/consumer overlap plan; link/manifest resolution; rollback |
| Update the BRIM consumer contract | Tracked products/manifests, producer fields, read-only registry/helper behavior | Consumer contract, products | Exact path/type/unit/time/null cross-check without copying private source |
| Documentation-only change | Branch/status, implementation/artifacts, approved file allowlist | Documentation guide and controlling source document | Link/path/disclosure/inventory audit; no source/generated/staged changes |

## Product map

Production writer workflow and builder pairs:

| Product | Workflow | Builder |
|---|---|---|
| CDEC reservoirs | `.github/workflows/build-cdec-reservoir-feed.yml` | `scripts/build_cdec_reservoir_latest.R` |
| CoCoRaHS precipitation | `.github/workflows/build-cocorahs-daily-precip-feed.yml` | `scripts/build_cocorahs_daily_precip_latest.R` |
| Delta operations | `.github/workflows/build-delta-ops-daily-summary.yml` | `scripts/build_delta_ops_daily_summary.R` |
| GFS wind | `.github/workflows/build-gfs-wind-latest.yml` | `scripts/build_gfs_wind_latest.R` |
| HRRR wind | `.github/workflows/build-hrrr-wind-latest.yml` | `scripts/build_hrrr_wind_latest.R` |
| NBM guidance | `.github/workflows/build-nbm-wind-guidance-latest.yml` | `scripts/build_nbm_wind_guidance_latest.R` |
| ASOS/AWOS wind | `.github/workflows/build-asos-awos-wind-latest.yml` | `scripts/build_asos_awos_wind_latest.R` |
| SCAN soil moisture | `.github/workflows/build-scan-soil-moisture-latest.yml` | `scripts/build_scan_soil_moisture_latest.R` |
| Snow pillow SWE | `.github/workflows/build-snow-pillow-latest.yml` | `scripts/build_snow_pillow_latest.R` |
| USGS groundwater | `.github/workflows/build-usgs-groundwater-latest-ca.yml` | `scripts/build_usgs_groundwater_latest_ca.R` |
| USGS streamflow | `.github/workflows/build-usgs-streamflow-latest-ca.yml` | `scripts/build_usgs_streamflow_latest_ca.R` |
| Winter Storm Levels | `.github/workflows/build-winter-storm-levels.yml` | `scripts/build_winter_storm_levels.R` |

Supporting workflows are the HRRR sandbox, wind watchdog, and groundwater
candidate preview. The watchdog reads wind manifests/state and can dispatch
ASOS/AWOS, GFS, HRRR, and NBM writers on the selected ref, but does not itself
commit or publish repository data. Support workflows have different permission
and publication behavior; do not generalize from them to production writers.

## High-risk invariants

- Official public state comes from `main/docs`.
- Writers push to the selected reference, use the shared reference-scoped
  concurrency group, wait instead of cancelling another writer, and never
  force-push.
- The changed path set must equal the product's declared output set.
- A builder exit code alone does not prove a usable feed.
- Validate non-null live measurements, not only features joined from a static
  station inventory.
- Manifests and target files must be coherent in the same generation.
- Time fields, time zones, units, and null meanings are consumer contracts.
- Current files are not backed by one universal schema or manifest envelope.
- Several local builders write final tracked paths directly; preserve the working
  tree and verify last-known-good behavior.
- The observed-wind official contract is RTW019; RTW020-oriented integration
  material is not automatically the Pages contract.
- The streamflow minimum-latest-value configuration and implementation are not
  aligned.
- `main` had no observed branch protection/ruleset and the repository had no
  license file at the documentation baseline.

## Safe task workflow

### Documentation or review

Use read-only repository inspection, compare exact paths/fields, and keep the diff
to requested Markdown files. Do not refresh generated feeds to make documentation
look current.

### Builder or workflow implementation

Read the scoped agent file and product contract first. Define output allowlists,
failure behavior, consumer impact, and validation before editing. Reproduce in an
isolated working copy where possible. Do not dispatch an official writer as an
implicit test.

### Contract change

Follow the additive migration sequence in
[docs/BRIM_CONSUMER_CONTRACT.md](docs/BRIM_CONSUMER_CONTRACT.md). Validate producer
and consumer behavior without disclosing private implementation.

### Incident diagnosis

Separate upstream, builder, validation, Git publication, and Pages delivery
failures. Preserve the previous publication unless a reviewed rollback is needed.
Use the incident template in
[docs/PUBLISHING_AND_OPERATIONS.md](docs/PUBLISHING_AND_OPERATIONS.md).

## Validation gate

Before handing work back:

```sh
cd "$(git rev-parse --show-toplevel)"
git status --short --branch
git diff --name-only
git diff --cached --name-only
git diff --check
```

Also run checks appropriate to the changed files:

- Parse workflow YAML and changed JSON/GeoJSON.
- Inspect CSV structure and representative data.
- Validate repo-relative Markdown links and referenced paths.
- Confirm workflow and builder inventories are complete when documentation claims
  completeness.
- Check the diff for personal paths, credential material, private source, and
  unexpected generated files.
- Confirm no unrelated file was staged, modified, removed, or reformatted.

Report what ran, what passed, what was not run, residual risks, and exact Git
status. Do not claim a commit, push, merge, deployment, or publication occurred
unless it actually succeeded and was explicitly authorized.

## Maintainer decisions

David, the repository owner and maintainer, retains the explicit decision to
merge a pull request, dispatch or publish official production products, and
approve an official rollback. Codex or another agent may prepare changes,
commits, pushes, and pull requests only when authorized. An agent must not merge,
dispatch production, publish, or roll back official state without David's
explicit approval.

Stop and request direction when completion requires a choice about:

- Breaking or removing a public path/field.
- Changing units, time semantics, freshness, or fallback behavior.
- Publishing partial or degraded data where no current rule exists.
- Merging generated branch artifacts.
- Enabling repository rules, changing secrets, or altering Pages settings.
- Selecting a code/data license.
- Merging, dispatching, publishing, or rolling back official state.

Make safe, evidence-based implementation assumptions inside the user's stated
scope. Do not expand authority across repositories or publication boundaries.
