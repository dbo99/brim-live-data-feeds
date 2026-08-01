# Architecture

This repository is the public producer and publication boundary for BRIM live-data
feeds. Scheduled GitHub Actions retrieve upstream observations or forecasts, run
product-specific R builders, validate the result, commit accepted artifacts to the
selected branch, and expose the `docs/` tree through GitHub Pages. The BRIM
application is a separate consumer.

The authoritative product inventory is [PRODUCTS.md](PRODUCTS.md). Consumer-facing
meanings and compatibility rules are in
[BRIM_CONSUMER_CONTRACT.md](BRIM_CONSUMER_CONTRACT.md). Operational behavior is in
[PUBLISHING_AND_OPERATIONS.md](PUBLISHING_AND_OPERATIONS.md), and implementation
standards are in
[WORKFLOW_AND_PRODUCT_STANDARDS.md](WORKFLOW_AND_PRODUCT_STANDARDS.md).

## System boundary

```text
public upstream services
        |
        v
scheduled/manual GitHub Actions
        |
        v
product-specific R builder ----> temporary downloads and transformations
        |
        v
validation and publication gate
        |
        v
docs/data on the selected Git branch
        |
        v
GitHub Pages from main/docs ----> BRIM and other public consumers
```

This repository owns retrieval, normalization, feed validation, public artifact
paths, and publication history. It does not own the private BRIM interface, map
rendering, end-user authentication, or business logic outside the public feed
contract. Public documentation must describe the consumer contract without
copying private application source.

## Repository map

| Area | Responsibility |
|---|---|
| `.github/workflows/` | Schedules, permissions, runtime setup, builder execution, artifact capture, and branch-safe Git publication |
| `scripts/` | Product retrieval, normalization, validation, serialization, and selected local QA outputs |
| `docs/data/` | Published feed artifacts and small static context datasets |
| `docs/index.html` and `docs/.nojekyll` | Pages landing surface and static-site behavior |
| Root and `docs/*.md` | Contributor guidance, public contracts, operations, risks, and handoff |

The nested `AGENTS.md` files add directory-specific instructions. They do not
replace the contracts above.

## Control plane and data plane

The control plane is the set of workflow definitions: triggers, permissions,
concurrency, toolchain setup, timeouts, artifact retention, and Git publication.
The data plane is the builder output under `docs/data/`.

Keeping those concerns explicit matters because a valid dataset can still be
published unsafely, and a safe workflow can still publish semantically invalid
data. Every writer therefore needs two independent kinds of evidence:

1. Product evidence that structure, counts, timestamps, units, domains, and
   required fields are acceptable.
2. Publication evidence that the intended paths and selected branch are the only
   Git changes and that a rejected build leaves the last-known-good files intact.

## Product architecture

The thirteen production families fall into five broad shapes:

| Shape | Product families | Publication pattern |
|---|---|---|
| Current geospatial snapshot | CDEC reservoirs, CoCoRaHS precipitation, ASOS/AWOS wind, USGS streamflow, USGS groundwater | GeoJSON plus a summary; ASOS/AWOS also has a manifest |
| Current operational snapshot | Delta operations | JSON summary, GeoJSON features, compact summary, and X2 reference |
| Geometry-free forecast snapshot | CNRFC major water-supply basin forecasts; CBRFC Colorado River water-supply forecasts | One 51-record CNRFC JSON payload of direct product-9 water-year, product-7 April-July and major-basin-only product-2 accumulated-volume values; one separate three-record CBRFC JSON payload with two direct GLDA3 scalar periods and one direct LKSA3 monthly local-intervening series. CNRFC and CBRFC have separate schemas and publication transactions; all three CBRFC source families reconcile independently. Browser geometry remains outside this repository. |
| Snapshot with historical/context tables | SCAN soil moisture, snow pillow SWE | GeoJSON and summaries plus trace/context CSV files |
| Rolling forecast set | GFS, HRRR, NBM wind | Manifest/index plus target-time files; GFS and HRRR also expose latest/summary compatibility views |

These shapes are related but are not interchangeable schemas. A common envelope
is a future direction, not a current invariant. Product-specific field and file
details remain authoritative in [PRODUCTS.md](PRODUCTS.md).

## Publication topology

Production writer workflows use a concurrency group derived from the selected Git
reference, wait rather than cancel an in-progress writer, check out that reference,
and push a normal non-force commit back to it. Most manual writers on a feature
branch therefore write ordinary product paths on that branch; CNRFC and CBRFC
forecast dispatches instead default to temporary dry runs and cannot publish
from a feature branch. Neither behavior updates the official Pages feed.

The official public site is served from `main/docs`. A successful branch build is
only branch-level evidence until reviewed and merged. Pages deployment is a
separate platform action after `main` changes.

Non-publishing workflows are intentionally different:

- The HRRR sandbox has read-only repository access, uses a cancel-in-progress
  concurrency policy, and uploads QA artifacts without committing feed files.
- The groundwater candidate preview is read-only and uploads a review artifact.
- The wind watchdog reads wind manifests/state and can dispatch ASOS/AWOS, GFS,
  HRRR, and NBM writers on the selected ref when checks call for a rebuild. It
  does not itself commit or publish repository data.

## State, identity, and time

The repository contains several distinct timestamps:

- Upstream observation or model valid time describes the data.
- Model cycle/init time and forecast hour identify a forecast target.
- Builder generation time describes serialization.
- Git commit time records branch publication.
- Pages availability time is downstream of the commit.

Consumers must not treat those timestamps as synonyms. Staleness should be derived
from the product's data-valid semantics, not merely from the newest Git commit.

Rolling forecast products add another identity layer. Their manifests select
target files and, where present, declare product/version identity and stale
thresholds. Current manifests are similar but not uniform; in particular NBM uses
`feed_version` and does not currently expose all identity/freshness fields used by
the other wind manifests. See the contract before generalizing across them.

## Validation and last-known-good behavior

Builders retrieve and transform into temporary or intermediate locations where
implemented, then run product-specific checks before the workflow stages declared
outputs. When a builder exits nonzero before the Git commit, the published branch
retains its previous files. This is the principal last-known-good mechanism.

It is not a transaction across the local filesystem. Several builders write final
paths directly, so a locally interrupted process can leave a partial working tree.
The workflow's later commit is the durable publication boundary, but future work
should prefer build-to-temporary plus atomic replacement for complete output sets.

Current QA is intentionally product-specific. There is no repository-wide schema
registry, checksum catalog, provenance envelope, compatibility checker, or common
minimum-completeness gate. Those absences are constraints to manage, not features
to assume.

## Consumer relationship

Consumers should:

- Resolve only documented Pages paths.
- Use manifests to select rolling forecast targets.
- Tolerate additive fields and ignore unknown properties.
- Preserve an already rendered layer when a newly retrieved response is
  unavailable or rejected where the product contract calls for that behavior.
- Apply product-specific freshness and fallback semantics.
- Avoid deriving undocumented invariants from sample ordering, file size, Git
  history, or implementation details.

Producer changes should be evaluated against both public artifact structure and
the documented consumer behavior. The crosswalk and change sequence are in
[BRIM_CONSUMER_CONTRACT.md](BRIM_CONSUMER_CONTRACT.md).

## Development and release flow

Normal development happens on a feature branch. Builders may be reproduced
locally in an isolated working copy, and read-only/sandbox workflows should be
preferred when they provide the necessary evidence. A product-path change requires
producer validation, consumer-impact review, documentation updates, and maintainer
approval before merge. Only the repository maintainer merges or intentionally
publishes official data.

The detailed local procedure is in [../BUILD.md](../BUILD.md), contributor rules
are in [../CONTRIBUTING.md](../CONTRIBUTING.md), and the operational release and
rollback sequence is in
[PUBLISHING_AND_OPERATIONS.md](PUBLISHING_AND_OPERATIONS.md).

## Design goals

The architecture favors:

- Public, cacheable, dependency-light files.
- Small independent product pipelines so one upstream outage does not block all
  feeds.
- Human-inspectable GeoJSON, JSON, and CSV.
- Branch-isolated publication and reviewable Git history.
- Explicit last-known-good behavior.
- Stable public paths with additive evolution.
- Bounded workflow runtime and retained QA evidence.

## Known architectural constraints

The highest-impact constraints are:

- Direct writes to final local paths are not uniformly atomic.
- Product validation and manifest fields are inconsistent.
- Several feeds can accept partial upstream coverage without a common degraded
  publication policy.
- The streamflow workflow declares a minimum latest-value quality setting that the
  current builder does not enforce as named, allowing a structurally populated
  layer with effectively empty live measurements.
- Context tables for SCAN and snow are split between generated and static
  ownership, increasing coordination risk.
- `main` had no observed branch protection or ruleset at the documentation
  baseline, so process discipline is carrying controls that should be enforced by
  the hosting platform.
- There is no repository license file; public visibility must not be interpreted
  as an affirmative reuse license.

The evidence, priority, and mitigation options are maintained in
[DEVELOPMENT_HISTORY_AND_RISKS.md](DEVELOPMENT_HISTORY_AND_RISKS.md).
