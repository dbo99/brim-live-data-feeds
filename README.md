# BRIM Live Data Feeds

This repository builds and publishes the public, static live-data feeds consumed by
BRIM. Scheduled GitHub Actions retrieve public observations and forecasts, run
product-specific R builders, validate accepted outputs, and commit them under
`docs/data/`. GitHub Pages serves the `docs/` directory from `main`.

The repository is a producer and publication boundary. It is intentionally
separate from the private BRIM application so feed refreshes can be operated,
reviewed, and rolled back without coupling them to the interface code.

## Production products

The current production system contains fifteen product families:

1. CDEC reservoir storage
2. CoCoRaHS daily precipitation
3. Delta operations daily summary
4. GFS surface wind forecast
5. HRRR surface wind forecast
6. NBM wind guidance
7. ASOS/AWOS observed wind
8. SCAN soil moisture
9. Snow pillow snow-water equivalent
10. USGS California groundwater
11. USGS California streamflow
12. CNRFC major water-supply basin forecasts
13. CBRFC Colorado River water-supply forecasts
14. Winter Storm Levels NBM snow-level contours
15. NBM six-hour QPF image and numeric grids

The authoritative paths, workflows, scripts, formats, schedules, time/unit
semantics, QA gates, and known gaps are in
[docs/PRODUCTS.md](docs/PRODUCTS.md).

The HRRR sandbox, wind watchdog, and groundwater candidate preview support
operations but are not separate production products.

## How publication works

Each production writer, subject to documented product-specific branch controls:

- Checks out the selected Git reference.
- Runs one product-specific builder.
- Allows only its declared output paths.
- Commits accepted artifacts with a product-specific generated-data message.
- Pushes normally, without force, back to its authorized selected reference.
- Shares a reference-scoped, non-cancelling concurrency group with other writers.

The CNRFC, CBRFC, Winter Storm Levels, and NBM QPF forecast writers are the
current stricter exceptions: manual work is runner-temporary dry-run output or
artifact-only preparation, feature branches cannot publish, and publication is
restricted to `main`. Winter Storm Levels also uses guarded primary and fallback
scheduled attempts for each
00/06/12/18 UTC NBM cycle; only a strictly newer complete cycle can reach its
existing publication transaction.

The official Pages source is `main/docs`. Except for the nonpublishing
feature-branch behavior of CNRFC, CBRFC, Winter Storm Levels, and NBM QPF, a
manual writer run on a feature branch changes that branch's ordinary product paths; it does not
publish the official feed. When a
builder rejects a response before commit, the previous committed
files remain the last-known-good publication.

See [docs/PUBLISHING_AND_OPERATIONS.md](docs/PUBLISHING_AND_OPERATIONS.md) for
dispatch, incident, rollback, backup, and recovery procedures.

## Use the feeds

Public consumers should use only documented paths and semantics. Rolling
forecast sets must be selected through their manifests rather than by listing
directories. Consumers should tolerate additive fields, enforce product-specific
freshness, and preserve usable prior state where the contract defines a fallback.

Read [docs/BRIM_CONSUMER_CONTRACT.md](docs/BRIM_CONSUMER_CONTRACT.md) for the
producer-to-consumer path crosswalk, compatibility policy, and change sequence.

## Develop safely

Builders write tracked files, and several write final paths directly. Reproduce a
build in an isolated working copy or on a deliberate feature branch, inspect every
changed path, and never treat a successful process exit as sufficient product
validation.

- [BUILD.md](BUILD.md) — toolchains, builder entry points, local reproduction,
  inspection, and failure evidence
- [CONTRIBUTING.md](CONTRIBUTING.md) — change classes, planning, validation, and
  review expectations
- [SECURITY.md](SECURITY.md) — public-repository, workflow-permission, credential,
  dependency, and incident rules
- [CODEX_HANDOFF.md](CODEX_HANDOFF.md) — safe orientation and routing for a new
  coding-agent session

Read the nearest `AGENTS.md` before editing. Root instructions apply everywhere;
`.github/workflows/AGENTS.md` and `scripts/AGENTS.md` add scoped requirements.

## Documentation

[docs/README.md](docs/README.md) is the documentation map and authority guide. The
four technical sources of truth are:

- [Product catalog](docs/PRODUCTS.md)
- [BRIM consumer contract](docs/BRIM_CONSUMER_CONTRACT.md)
- [Publishing and operations](docs/PUBLISHING_AND_OPERATIONS.md)
- [Workflow and product standards](docs/WORKFLOW_AND_PRODUCT_STANDARDS.md)

Architecture and the risk register interpret those sources without replacing
them:

- [Architecture](docs/ARCHITECTURE.md)
- [Development history and risks](docs/DEVELOPMENT_HISTORY_AND_RISKS.md)

## Current limitations

This repository does not yet have a common schema registry, uniform manifest
envelope, cross-product completeness policy, or consistently atomic local output
replacement. Product-specific checks remain authoritative. The streamflow
minimum-latest-value configuration and implementation also require alignment
before its count gate can be relied on as a live-data completeness guarantee.

`main` now has the active `main-history-safety` ruleset, which blocks branch
deletion and non-fast-forward history changes with no bypass actors. Classic
branch protection remains absent, and the ruleset does not require pull-request
review or status checks. The repository also has no license file. These are
documented controls and remaining risks, not implicit permissions or assurances. See the
[risk register](docs/DEVELOPMENT_HISTORY_AND_RISKS.md).
