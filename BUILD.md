# Build and Local Validation

This guide explains how to reproduce and inspect BRIM feed builders without
publishing official data. It does not grant release authority. Product contracts
are in [docs/PRODUCTS.md](docs/PRODUCTS.md), and publication procedures are in
[docs/PUBLISHING_AND_OPERATIONS.md](docs/PUBLISHING_AND_OPERATIONS.md).

## Safety first

Builders write tracked paths under `docs/data/`, and several write those final
paths directly. Run them only in an isolated working copy or on a feature branch
whose generated changes you are prepared to inspect and discard through a
recoverable method. Do not run a production writer merely to test workflow syntax.

Before a local build:

```sh
cd "$HOME/Documents/brim-live-data-feeds_source_repo"
git status --short --branch
git rev-parse --show-toplevel
```

Confirm the intended branch and account for every existing change. Do not clean,
reset, or overwrite unrelated work. If the repository is not at the canonical
checkout path, use its actual path; examples use the canonical location only.

For highest isolation, create a disposable Git worktree at a specific commit using
normal project practice, run there, retain required evidence, and remove it only
after confirming it contains no work to preserve. Worktree creation/removal is
outside this guide because local layout and retention decisions belong to the
maintainer.

## Toolchains

All builders require R. The exact packages and system dependencies are declared by
the corresponding workflow and script and should be treated as executable
requirements rather than duplicated here.

Additional command-line dependencies include:

- `wgrib2` for HRRR and NBM GRIB2 extraction.
- The geospatial libraries needed by the R spatial packages used by GFS and HRRR.
- Poppler utilities for the Delta operations PDF workflow.
- Network and TLS access to each product's public upstream services.

Use the workflow's setup steps when reproducing CI. Do not assume a locally newer
package or operating system is equivalent to the hosted runner.

## Builder entry points

| Product family | R entry script |
|---|---|
| CDEC reservoir | `scripts/build_cdec_reservoir_latest.R` |
| CoCoRaHS precipitation | `scripts/build_cocorahs_daily_precip_latest.R` |
| Delta operations | `scripts/build_delta_ops_daily_summary.R` |
| GFS wind | `scripts/build_gfs_wind_latest.R` |
| HRRR wind | `scripts/build_hrrr_wind_latest.R` |
| NBM wind guidance | `scripts/build_nbm_wind_guidance_latest.R` |
| ASOS/AWOS observed wind | `scripts/build_asos_awos_wind_latest.R` |
| SCAN soil moisture | `scripts/build_scan_soil_moisture_latest.R` |
| Snow-pillow SWE | `scripts/build_snow_pillow_latest.R` |
| USGS groundwater | `scripts/build_usgs_groundwater_latest_ca.R` |
| USGS streamflow | `scripts/build_usgs_streamflow_latest_ca.R` |
| CNRFC major water-supply basin forecasts | `scripts/build_major_water_supply_basin_forecasts.R` |
| CBRFC Colorado River water-supply forecasts | `scripts/build_cbrfc_major_water_supply_forecasts.R` |

Run exactly one intended builder. Do not paste or execute the complete
entry-point inventory as a batch.

Use this single-builder template from the repository root:

```sh
cd "$HOME/Documents/brim-live-data-feeds_source_repo"

# Replace the example with exactly one reviewed builder entry point.
Rscript scripts/build_cdec_reservoir_latest.R
```

Non-production investigation entry points are:

```sh
cd "$HOME/Documents/brim-live-data-feeds_source_repo"
Rscript scripts/build_hrrr_wind_sandbox.R
Rscript scripts/preview_usgs_groundwater_candidate_discovery.R
```

Other scripts under `scripts/` preprocess or export supporting material and are not
production writer entry points. Read the script and its scoped instructions before
running one.

There is no universal dry-run, fixture, offline, or output-directory switch across
the builders. Some scripts accept product-specific environment configuration, but
an undocumented override must not be assumed to provide publication isolation.

## Reproduce the workflow, not just the R call

For a faithful reproduction:

1. Read the matching `.github/workflows/*.yml`.
2. Install the same system tools and R dependencies.
3. Use the same working directory and any explicitly declared non-sensitive
   environment settings.
4. Run the same builder command.
5. Apply the workflow's declared path list and validation expectations.
6. Compare the resulting structure and semantics to
   [docs/PRODUCTS.md](docs/PRODUCTS.md).

Do not reproduce a secret-bearing request by printing configuration. The
groundwater builder's workflow can supply `API_USGS_PAT`; local reproduction
should use a securely injected value only if the upstream service requires it.

The CNRFC and CBRFC major water-supply parsers have complete offline fixture
suites. Run them without touching `docs/data/`:

```sh
cd "$HOME/Documents/brim-live-data-feeds_source_repo"
Rscript tests/test_major_water_supply_basin_forecasts.R
python3 -m unittest -v tests/test_cnrfc_workflow_safety.py
Rscript tests/test_cbrfc_major_water_supply_forecasts.R
python3 -m unittest -v tests/test_cbrfc_workflow_safety.py
```

Do not run the CNRFC production entry point merely to test parsing. It retrieves
18 official product-9 pages, 15 official product-2 pages, and headline/tabular
product-7 pages for 18 identities, and may replace its canonical output after
bootstrap or steady-state structural validation passes.
For live inspection, set both `CNRFC_FORECAST_OUTPUT_JSON` and
`CNRFC_FORECAST_QA_JSON` to paths under `/tmp`.

The CBRFC production entry point independently retrieves the official GLDA3
April-July point response, secondary list CSV and Lake Powell dashboard
cross-check; the same official dashboard is the water-year primary and
human-facing summary. It also retrieves the Lake Mead Local monthly
special-product text and, only for the reviewed July 1, 2026 January-label
defect, the exact June 1 archived confirmation. For isolated live inspection, set both
`CBRFC_FORECAST_OUTPUT_JSON` and
`CBRFC_FORECAST_QA_JSON` to paths under `/tmp`.

## Inspect changes

After a build, start with:

```sh
cd "$HOME/Documents/brim-live-data-feeds_source_repo"
git status --short
git diff --stat
git diff --name-only
```

The changed-file set must be limited to the product's declared outputs. A builder
that touches another product, workflow, script, documentation file, or an
unrelated local file has not passed isolation review.

For JSON and GeoJSON, validate parsing and inspect the top-level shape:

```sh
cd "$HOME/Documents/brim-live-data-feeds_source_repo"
jq empty docs/data/cdec_reservoir_latest.geojson
jq 'keys' docs/data/cdec_reservoir_latest_summary.json
```

For CSV, inspect the header, record count, delimiters, quoting, missing-value
representation, and a few boundary rows. Do not use a spreadsheet application as
the only validation because it may silently reinterpret identifiers or
timestamps.

## Product validation

Every build should provide evidence for:

- Output paths and exact changed-file allowlist.
- Successful JSON/GeoJSON parsing or CSV structural checks.
- Required top-level keys and required feature properties.
- Nonempty and non-null counts that measure live data, not only static station
  inventory.
- Geometry type, longitude/latitude order, coordinate bounds, and finite values.
- Units, time zones, timestamp formats, and forecast valid-time relationships.
- Source/model cycle and generation time.
- Product-specific freshness and completeness rules.
- Manifest entries that resolve to files in the same build set.
- File-size and runtime changes large enough to indicate a regression.
- Retention of the previous published set when validation fails.

The detailed current gates and known gaps are documented in
[docs/PRODUCTS.md](docs/PRODUCTS.md). Recommended cross-product checks are in
[docs/WORKFLOW_AND_PRODUCT_STANDARDS.md](docs/WORKFLOW_AND_PRODUCT_STANDARDS.md).

## Failure reproduction

Capture enough information to distinguish:

- Upstream unavailability, throttling, or format change.
- Local dependency or runtime mismatch.
- Retrieval success followed by an empty or partial dataset.
- Transformation/schema failure.
- Validation rejection.
- Git publication conflict.
- Pages deployment/cache delay after an accepted commit.

Record the product, commit, builder/workflow, UTC time window, upstream status
without credentials, exit status, output-path state, and whether last-known-good
files were preserved. Use the incident template in
[docs/PUBLISHING_AND_OPERATIONS.md](docs/PUBLISHING_AND_OPERATIONS.md).

## Before proposing a change

Run validation proportionate to the change and report:

- Commands executed and their results.
- Files changed, including generated evidence.
- Current versus recommended behavior when fixing a known gap.
- Consumer compatibility assessment.
- Expected schedule/runtime and file-size impact.
- Failure and rollback behavior.
- Checks not run and why.

Do not commit generated feed changes from a diagnostic run unless those exact
artifacts are intended for review and publication. Do not push, merge, dispatch an
official writer, or alter Pages settings without explicit maintainer direction.
