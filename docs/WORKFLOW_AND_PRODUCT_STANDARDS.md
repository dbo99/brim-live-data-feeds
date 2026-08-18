# Workflow and product standards

This document separates current implemented conventions from recommended
future standards. A recommendation here is not an implemented repository
control and does not authorize a workflow or product change.

Baseline audited: 2026-07-24; the audited commit is recorded in
[README.md](README.md).

## Status labels

- **Implemented** — verified in current workflow/script/product source.
- **Product-specific** — intentionally differs by family and must be documented.
- **Recommended** — desirable future work requiring its own review.
- **Gap** — current behavior does not meet the proposed standard.

## Workflow inventory

| Workflow file | Display name | Trigger/cadence UTC | Entry point | Publication |
|---|---|---|---|---|
| `.github/workflows/build-asos-awos-wind-latest.yml` | Build ASOS/METAR observed wind latest | Manual; :17/:42 hourly | `scripts/build_asos_awos_wind_latest.R` | Three ASOS wind files |
| `.github/workflows/build-cdec-reservoir-feed.yml` | Build CDEC reservoir latest GeoJSON | Manual; :29 every 3h | `scripts/build_cdec_reservoir_latest.R` | CDEC GeoJSON/summary |
| `.github/workflows/build-major-water-supply-basin-forecasts.yml` | Build major water-supply basin forecasts | Manual dry run by default; native-IANA 10:56/16:56 Pacific | `scripts/build_major_water_supply_basin_forecasts.R` | One geometry-free CNRFC JSON payload; scheduled/explicit manual publication on `main` only |
| `.github/workflows/build-cbrfc-major-water-supply-forecasts.yml` | Build CBRFC major water-supply forecasts | Manual dry run by default; native-IANA 08:14 Pacific daily | `scripts/build_cbrfc_major_water_supply_forecasts.R` | Separate three-record GLDA3/LKSA3 JSON payload; scheduled/explicit manual publication on `main` only |
| `.github/workflows/build-cocorahs-daily-precip-feed.yml` | Build CoCoRaHS daily precip GeoJSON | Manual; 00:37/08:37/16:37 | `scripts/build_cocorahs_daily_precip_latest.R` | CA/CONUS products |
| `.github/workflows/build-delta-ops-daily-summary.yml` | Build Delta Ops daily summary GeoJSON | Manual; five daily attempts | `scripts/build_delta_ops_daily_summary.R` | Four Delta files |
| `.github/workflows/build-gfs-wind-latest.yml` | Build GFS wind latest | Manual; hourly :47 | `scripts/build_gfs_wind_latest.R` | Rolling set/manifest/compatibility |
| `.github/workflows/build-hrrr-wind-latest.yml` | Build HRRR wind latest | Manual; hourly :33 | `scripts/build_hrrr_wind_latest.R` | Rolling set/manifest/compatibility |
| `.github/workflows/build-nbm-wind-guidance-latest.yml` | Build NBM wind guidance latest | Manual; four times daily | `scripts/build_nbm_wind_guidance_latest.R` | Rolling set/manifest/summary |
| `.github/workflows/build-nbm-qpf.yml` | Build NBM QPF live feed | Manual; approved +138m primary and +190m fallback for 00/06/12/18Z; first publication separately gated | `scripts/build_nbm_qpf_candidate.R` | Unseeded complete-cycle artifact; separate bounded `main` publisher |
| `.github/workflows/build-scan-soil-moisture-latest.yml` | Build SCAN soil moisture latest feed | Manual; daily 14:30 | `scripts/build_scan_soil_moisture_latest.R` | Latest/trace/style |
| `.github/workflows/build-snow-pillow-latest.yml` | Build snow pillow SWE latest feed | Manual; three times daily | `scripts/build_snow_pillow_latest.R` | Latest/trace |
| `.github/workflows/build-usgs-groundwater-latest-ca.yml` | Build USGS CA groundwater latest GeoJSON | Manual; daily 13:41 | `scripts/build_usgs_groundwater_latest_ca.R` | GeoJSON/summary |
| `.github/workflows/build-usgs-streamflow-latest-ca.yml` | Build USGS CA streamflow latest GeoJSON | Manual; :23 every 4h | `scripts/build_usgs_streamflow_latest_ca.R` | GeoJSON/summary |
| `.github/workflows/build-winter-storm-levels.yml` | Build Winter Storm Levels | Manual dry run by default; guarded primary +128m and fallback +174m for 00/06/12/18Z | `scripts/build_winter_storm_levels.R` | Complete manifest/contour artifact; scheduled or explicit `main` publication only after a newer complete-cycle preflight |
| `.github/workflows/build-hrrr-wind-sandbox.yml` | Build HRRR wind sandbox | Manual | `scripts/build_hrrr_wind_sandbox.R` | Artifact only |
| `.github/workflows/check-wind-feeds.yml` | Check BRIM wind feeds | Manual; every 15m | Inline Python | May dispatch wind writers |
| `.github/workflows/preview-usgs-groundwater-candidates.yml` | Preview USGS groundwater candidate discovery | Manual input; Mondays 13:37 | `scripts/preview_usgs_groundwater_candidate_discovery.R` | Artifact only |

GitHub's dynamic `pages build and deployment` workflow is a nineteenth active
workflow but is not source-controlled in `.github/workflows`.

## Names and entry points

**Implemented**

- Workflow filenames describe one product or diagnostic responsibility.
- Display names identify product and action.
- Writers call one principal R entry script.
- Publication paths are explicit in each workflow's `git add`.

**Recommended**

- Preserve stable workflow filenames because dispatch APIs and operational
  references use them.
- Keep one public product family per writer unless files form one atomic
  contract.
- If producer logic becomes shared, use a reviewed helper rather than copying
  retrieval/publication logic among scripts.
- Document renamed workflow/script pairs in product and operations docs in the
  same change.

## Triggers and manual inputs

**Implemented**

- Fifteen writers have schedules and `workflow_dispatch`.
- The CNRFC forecast writer uses GitHub Actions' native
  `America/Los_Angeles` timezone at 10:56 and 16:56. It has no runtime
  exact-minute guard, records a stable Pacific AM/PM logical slot, defaults
  manual dispatch to runner-temporary dry-run output, and permits scheduled or
  explicitly requested publication only from the selected `main` branch tip.
- The CBRFC forecast writer independently uses the native
  `America/Los_Angeles` timezone once daily at 08:14. It defaults manual
  dispatch to runner-temporary dry-run output and permits scheduled or explicitly
  requested publication only from the selected `main` branch tip.
- The Winter Storm Levels writer defaults manual dispatch to runner-temporary
  dry-run output and accepts publication only from `main`. Scheduled primary
  attempts run at 02:08/08:08/14:08/20:08 UTC and fallbacks at
  02:54/08:54/14:54/20:54 UTC. Scheduled and explicit manual publication first
  run the shared-logic, inventory-only cycle preflight; only `NEW_CYCLE` reaches
  the full producer. Manual `publish: false` remains available for diagnostic
  current-cycle rebuilds.
- The unseeded NBM QPF workflow performs the same ten-lead inventory discovery
  before its unchanged R producer, prepares a bounded candidate on any selected
  ref, and permits its separate publisher job only on `main`. Its approved +138/
  +190-minute timing does not authorize merge, dispatch, or first publication.
- Writers do not run on pull requests.
- HRRR sandbox is manual only.
- Groundwater preview accepts `lookback_days`.
- Delta manual dispatch bypasses the scheduled same-date precheck.

**Gaps**

- Most production manual dispatches have no confirmation/production-target
  input; the CNRFC, CBRFC, and Winter Storm Levels forecast writers are
  product-specific exceptions.
- Most writers can be manually targeted at a feature branch and write ordinary
  product paths on that branch; the CNRFC, CBRFC, and Winter Storm Levels
  forecast writers are product-specific dry-run exceptions.
- There is no GitHub Environment approval for official publication.

**Recommended**

- Keep production dispatch explicit.
- If manual modes are added, distinguish `artifact`, `feature_branch`, and
  `official` behavior unambiguously.
- Never default a new test input to official publication.

## Permissions

**Implemented**

- Two direct writers request `contents: write`. Thirteen migrated workflows use
  `contents: read` for preparation and `contents: write` only in their
  `publish-to-main` job.
- Wind watchdog requests `contents: read` to inspect wind manifests/state and
  `actions: write` to dispatch ASOS/AWOS, GFS, HRRR, and NBM writers on the
  selected ref; it does not itself commit or publish repository data.
- Sandbox and preview request `contents: read`.

**Gap**

The two remaining direct writers share build and publication under a write-
capable token; migrated workflows have separate least-privilege build and
publisher jobs.

**Recommended**

- Request only permissions required by the job.
- Separate untrusted retrieval/build work from a narrow publisher when the
  complexity and risk justify it.
- Do not add secret or OIDC permissions without a documented identity and
  threat-model review.

## Concurrency and publication ref

**Implemented**

- Writers use `live-data-feed-writes-${{ github.ref }}`,
  `cancel-in-progress: false`, and `queue: max`.
- Migrated publisher jobs additionally use `brim-live-main-publish`, reconcile
  against fresh `origin/main`, and push normally only to `main`.
- Direct writers check out the selected ref and push non-force under their
  product-specific branch rules. Unexpected branch advancement fails the run.

**Recommended**

- Preserve one serialization group for writers that share a branch.
- Include the ref in any new nonproduction group.
- Do not reintroduce `git pull`, merge, rebase, force push or hardcoded
  feature-to-main publication.
- Re-evaluate queue behavior when schedules or runtime increase materially.
- Winter Storm Levels and NBM wind guidance remain direct writers
  inside the existing whole-workflow queue. Their migration and later removal
  of the compatibility lock require separate review.

## Runner and action versions

**Implemented**

- Jobs use `ubuntu-latest`.
- Checkout is `actions/checkout@v6`.
- R actions are `r-lib/actions/*@v2`.
- Micromamba is `mamba-org/setup-micromamba@v3`.
- Every upload reference is `actions/upload-artifact@v7`.

**Gaps**

- Action major tags are mutable references rather than commit SHAs.
- `ubuntu-latest`, R `release`, apt packages and most R packages are not locked
  to one reproducible environment.
- HRRR sandbox and groundwater preview have not yet produced post-PR #4 runtime
  evidence for their updated artifact action.

**Recommended**

- Review upstream release notes, runner minimums and behavior changes before an
  action major update.
- Preserve artifact name, path, archive/compression, retention and no-file
  behavior unless the product of the change is intentional.
- Consider action SHA pinning and an R dependency lock in a separate,
  operationally tested initiative.

## Dependency setup

**Implemented**

- R producers use workflow-declared package sets.
- HRRR/NBM use wgrib2 `3.8.*` through micromamba.
- Delta installs Poppler for PDF text extraction.
- GFS/HRRR install geospatial system libraries.

**Recommended**

- Keep workflow packages synchronized with actual script namespaces.
- Do not upgrade a package to diagnose an upstream/network failure.
- Record system/runtime changes in the pull request and risk history.
- Test the exact producer whose dependency surface changed.

## Upstream retrieval

**Implemented**

- Scripts declare upstream endpoints and request timeouts.
- Most station/API producers implement bounded retries or chunking.
- Groundwater classifies primary and direct-request outcomes per outer chunk,
  retries only transport errors and designated transient HTTP statuses with
  bounded backoff, and stops after three consecutive terminal chunk failures.
- Wind model scripts use model-cycle/lead selection.
- Source-specific index/lookup CSVs are tracked under `data/input`.

**Recommended**

- Use explicit user agents and bounded connect/read timeouts.
- Bound retries and include backoff/pause appropriate to the provider.
- Treat pagination completion, tile completion and required model fields as QA.
- Preserve observation/source timestamps independently from retrieval time.
- Never place credentials in URLs or logs.

## Temporary files and local output

**Implemented**

- Wind producers create local `data/cache`, `debug` and `qa` material.
- Model tools use working files before writing JSON/GeoJSON.
- Official product paths are directly writable by several scripts.
- The snow producer constructs and validates its four writer-managed outputs in
  a same-directory staging area before replacement, keeps backups during
  promotion, and restores already-promoted members after a later promotion
  failure.
- The groundwater producer applies the same staged-validation and rollback
  pattern to its GeoJSON and summary pair.
- The CNRFC major water-supply producer writes and validates its one canonical
  JSON path in the destination directory, then renames it into place only when
  substantive source/status fields changed.
- The CBRFC producer applies the same staged validation and semantic no-op rule
  to its separate exact three-record canonical JSON path.
- Winter Storm Levels builds its complete target/manifest set under a unique
  temporary root, validates every member, promotes immutable cycle targets
  first, and renames its manifest last. A failed pre-manifest promotion test
  preserves the prior accepted manifest byte-for-byte. Retrieval time is
  excluded from semantic no-op comparison; publication time remains null.

**Gap**

Complete product sets are not universally built under an isolated staging
directory and atomically promoted locally.

**Recommended**

- Build to a unique temporary directory on the same filesystem.
- Validate every member of the product set.
- Rename/promote only after all checks pass where practical.
- Keep cache/debug/QA paths outside the staged product allowlist.
- Add a reviewed ignore policy in a separate repository-hygiene change.

## Determinism

**Implemented**

- Producers generally normalize types, deduplicate and sort before writing.
- Rolling manifests use explicit entry metadata.
- Timestamps and live observations necessarily change output bytes.

**Recommended**

- Use stable ordering for features, rows, fields and manifest entries.
- Keep selection tie-breakers explicit.
- Do not describe a time-varying product as byte-reproducible.
- Separate deterministic transformation tests from live retrieval tests.

## Empty and partial products

**Implemented**

- Several producers enforce minimum counts and source-specific validation.
- Failed scripts do not reach Git publication.
- Snow implements product-specific partial publication: exactly one healthy
  provider may refresh while validated prior latest/trace rows for the failed
  provider are carried forward unchanged. Provider health includes
  provider-specific station and expected-station-day completeness gates; a
  response near 20% coverage fails rather than being labeled refreshed.
  Both-provider failure, invalid prior files, unavailable failed-provider rows,
  or failed combined QA retains the complete prior output set.
- Groundwater requires every outer request chunk to complete as usable data or a
  valid empty response and still requires at least 300 API-latest sites and 300
  output features. Candidate-index fallback cannot bypass either retrieval gate.
- CNRFC major water-supply basin forecast bootstrap requires all 15 FNF and all
  three direct-index product-9 identities, 14 expected-available product-2 median
  series plus explicit MHBC1 source unavailability, and all 18 product-7
  identities while active or explicit source-unavailable records out of season.
  Product 2 and product 7 run in the same workflow and schedule as product 9.
  After bootstrap, per-record failures retain only matching validated prior
  values with explicit state and families advance independently in the complete
  51-record payload.
- The separate three-record CBRFC payload keeps the GLDA3 April-July official
  `off*` source and semantic water-year situational-page source in independent
  families, and adds a third independent LKSA3 Lake Mead Local 12-month series
  from the official special-product text. Bootstrap may establish any valid
  family while another remains explicitly failed/unavailable, but rejects a run
  where no active family establishes official data. Each family can advance or
  retain only its own validated prior provenance. The local adapter applies one
  reviewed, provenance-preserving January label correction to the exact July 1,
  2026 issue only after the immediately preceding official archive confirms the
  year; all other rollover errors fail. Future monthly items are not map
  eligible before their month. Controlled-date tests cover July 31, August 1,
  September 1, October 1, January 1 and future pre-issuance bootstrap. CBRFC source
  failures and its publication transaction cannot block or alter CNRFC.
- Winter Storm Levels uses fail-and-retain for source, completeness, decode,
  contour, contract, and publication failures. It does not publish a partial
  target set. Bootstrap requires one complete current cycle with an active
  target. Every genuinely newer mature candidate strictly validates and retains
  the prior root cycle, producing exactly two complete cycles and 22 targets;
  failed retention rejects the candidate. Old accepted values age through
  explicit delayed, stale-LKG, and expired states without restamping.
- NBM QPF rejects every partial or mixed-cycle candidate. Its public adapter
  validates one explicit bootstrap cycle or exactly two steady cycles, ten
  targets per cycle, locked six-hour timing/science/spatial/palette identity,
  WebP decode and content hashes. Fresh-main NEW advances and prunes only the old
  previous cycle; SAME and STALE do not rewrite or regress canonical state.

**Gaps**

- CoCoRaHS lacks a contractual minimum and strong page-completion gate.
- GFS/HRRR can represent partial target coverage.
- Streamflow's workflow-declared current-value minimum is not read by the
  producer.

**Recommended**

- Distinguish a legitimate empty domain/time period from a failed retrieval.
- Validate required pages/tiles/fields and record optional failures separately.
- Gate publication on consumer-usable records, not merely static features.
- Never lower a threshold merely to make an incident run green.

## Geospatial products

**Implemented**

- Point products use GeoJSON longitude/latitude.
- GFS/HRRR headers describe their longitude/latitude grids.
- NBM masks offshore/out-of-domain points.
- Winter Storm Levels crops with a configured source buffer, passes every native
  source-crop cell to isoband without raster resampling or vector smoothing,
  then clips, transforms, and canonically serializes WGS84 lines to exact
  configured bounds.
- Private BRIM analytical processing may use another CRS before exported
  producer inputs arrive here.

**Recommended**

- Declare coordinate reference and axis order in the future common envelope.
- Validate latitude/longitude ranges and geometry type.
- Document clipping/masks and retain provider/source domain meaning.
- Never silently swap coordinate order or longitude convention.

## Units and missing values

**Implemented**

- Product-specific field names often encode units.
- Source values and nulls are retained where possible.
- BRIM performs some display conversion, especially m/s to mph.

**Recommended**

- Declare canonical machine units and user display units.
- Preserve zero as a valid value when the source defines it.
- Serialize missing numeric values as JSON null, not text placeholders.
- Do not infer vertical datum, storage, flow or uncertainty.

## Timestamps and freshness

**Implemented**

- Machine times are normally UTC.
- User-facing local time is normally Pacific.
- Wind products separate model cycle and valid time.
- Station products carry observation time/age where available.
- Snow summaries distinguish the current build UTC from carried-forward station
  observation and row-level build/fetch fields; carried-forward rows are not
  restamped.

**Gaps**

- Retrieval, build and publication times are inconsistent or absent across
  product families.
- Summary field names vary.
- The four wind manifests and Winter Storm Levels express product-specific
  machine freshness metadata.
- Winter Storm Levels version 1 explicitly serializes a null publication time;
  Git commit, push, and Pages deployment times remain external release evidence.

**Recommended**

- Use separate nullable fields for retrieval, observation, initialization,
  valid, build and publication time.
- Include timezone/offset in serialized timestamps.
- Define stale thresholds by product semantics, not build cadence alone.
- Do not call a successfully fetched stale observation current.

## Manifests, schemas and checksums

**Implemented**

- ASOS, GFS, HRRR, NBM, and Winter Storm Levels have product manifests.
- NBM QPF has an implemented schema-1 manifest contract and validator but no
  tracked or hosted manifest until a separately approved first publication.
- Other families have ad hoc summaries.

**Gaps**

- The remaining families lack formal manifests.
- Manifest envelopes and versions differ.
- No JSON Schemas or repository-wide checksum convention exists; Winter Storm
  Levels has product-specific SHA-256 target checksums.
- Producer workflow/commit and publication time remain absent or inconsistent
  across most products.

**Recommended**

- Introduce an additive common envelope rather than replacing strong
  product-specific entries.
- Version schemas independently from producer releases.
- Validate manifests and referenced paths before publication.
- Add checksums and producer provenance during a coordinated migration.

## QA artifacts and logs

**Implemented**

- ASOS/GFS use default artifact retention.
- HRRR/NBM and HRRR sandbox use 14 days.
- Groundwater preview uses 30 days.
- CNRFC and CBRFC major water-supply forecast run diagnostics use 14 days and
  exclude full source bodies.
- Winter Storm Levels retains its dry-run vector bundle, browser QA page, and
  attempt diagnostics for 14 days and excludes full GRIB messages.
- Artifacts may contain QA packages and debug logs.
- The groundwater writer emits bounded, sanitized request summaries and
  parser/filter counts, including aggregated warning classes instead of
  repeated full request URLs or payloads.

**Recommended**

- Keep artifacts small, sanitized and named by stable product family.
- Never include secret values, auth headers, signed URLs or nonpublic payloads.
- Treat artifact absence separately from product QA where appropriate.
- Record artifact ID/retention in incident evidence.

## Official versus test paths

**Implemented**

- Official products live under `docs/data`.
- HRRR sandbox and groundwater preview do not publish there.
- Feature-branch writers can change ordinary product paths only on that branch;
  Pages remains `main:/docs`.

**Gap**

Production writers lack a dedicated artifact-only mode or test prefix.

**Recommended**

- Use artifacts, an explicit sandbox directory or branch-only evidence for
  tests.
- Never configure Pages or another production host to publish a test branch.
- Verify the official path/bytes remained unchanged during nonproduction tests.

## Consumer compatibility

**Implemented**

- Private BRIM consumes paths through a central base URL registry.
- Wind consumers are manifest-driven except ASOS, which consumes the latest
  GeoJSON with summary/manifest metadata.
- GFS/HRRR compatibility latest files remain tracked.

**Recommended**

- Follow the rollout in [BRIM_CONSUMER_CONTRACT.md](BRIM_CONSUMER_CONTRACT.md).
- Maintain compatibility outputs only with explicit owner and retirement plan.
- Exercise stale, missing, malformed and old/new cases before a breaking
  switch.

## Performance and cost

Current pressure comes from:

- large groundwater/streamflow and CONUS precipitation payloads;
- rolling GFS/HRRR/NBM snapshots;
- high-frequency generated commits;
- repeated R/system dependency setup;
- Pages build and artifact storage.

Recommended measurement:

- retrieval and total run time;
- row/feature/entry count;
- individual and total payload bytes;
- artifact bytes and retention;
- Git history growth;
- browser parse/render cost in private BRIM.

Payload budgets must be evidence-based and product-specific. None is declared
by this document.

## Validation for workflow or producer changes

At minimum:

1. Parse changed YAML and R.
2. Run `git diff --check`.
3. Confirm trigger, permission, concurrency and publication ref.
4. Enumerate exact staged product paths.
5. Validate empty/partial/retry behavior.
6. Validate output JSON/CSV and manifest references.
7. Inspect secret/log/artifact exposure.
8. Use an artifact, sandbox or feature branch for nonproduction evidence.
9. Prove official `main` and hosted paths were not changed by the test.
10. Test private BRIM when path/schema/time/unit/fallback behavior changed.
11. Record remaining uncertainty and obtain explicit publication approval.
