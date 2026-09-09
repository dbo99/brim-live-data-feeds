# Development History and Risks

This is an evidence-based maintenance record for the live-feed system. It
distinguishes observed repository facts from recommended controls. It is not a
release changelog and does not replace Git history.
Publication/governance reconciliation: 2026-09-09 UTC against `febf1cae`.

## History that shaped the current design

### Product expansion

The repository now has fifteen scheduled product families, including Winter
Storm Levels and NBM QPF. Product-specific scripts, schedules, outputs and
validation remain necessary because upstream services and consumer semantics
differ. The current inventory is in [PRODUCTS.md](PRODUCTS.md).

### Publication-safety repairs

July 2026 repairs corrected selected-reference publication and stale checkout:
`169ec10` (merged by PR #2 at `a121115`), `4bb8da8` and `e90b637`.
NBM run `29891617635` had built seven entries before a rebase conflict exposed
the stale-event failure mode. That history explains the ban on pull/rebase/merge
publication; it does not describe today's publication topology.

The August shared-publisher migration began with PR #19 and extended across all
fifteen writers. Preparation now has read-only repository permission and hands a
validated candidate to a separate main-only publisher. Only publication shares
`brim-live-main-publish`; each attempt reconciles against fresh `main`, and a
non-fast-forward rejection permits at most one fresh transaction. The older
reference-scoped whole-workflow queue and feature-branch data pushes are obsolete.
See [operations](PUBLISHING_AND_OPERATIONS.md#branch-safe-writer-behavior) for
current source-binding differences and the bounded retry procedure.

### Runtime maintenance

Commit `6cdb8cd` updated artifact uploads to the Node 24-backed major release used
by the current workflows. The lesson is to treat action-runtime warnings as
planned maintenance: confirm every occurrence, update all matching workflows, and
preserve retention and artifact naming.

### Operational failures

Notable incidents established product-specific safeguards:

- Snow run `29933106803` timed out on all three NRCS/AWDB preflight stations;
  publication stopped and a later scheduled run recovered. Current snow logic
  attempts both providers independently and permits one-provider carry-forward
  only with validated prior rows and completeness checks. This incident alone
  does not prove workflow-runtime exhaustion.
- Snow run `30177980668` passed retrieval but staged validation falsely rejected
  required all-null GeoJSON properties after row binding dropped their columns.
  Publication was skipped. Separate prior/staged validators now preserve typed
  null properties while rejecting genuinely omitted keys.
- Groundwater run `30206927617` on July 26 returned zero raw/API-latest rows
  after all 38 chunks reached fallback. Matching source/dependency versions and
  subsequent recovery support a transient upstream failure; the exact statuses
  are unavailable in the retained log. The 300-site gate preserved the prior
  product. Bounded retry classification, parser accounting, all-chunk completeness
  and rollback-capable replacement followed.

### Local Git-index incident (August 2026)

The reconciliation handoff identifies an August local Git-index incident, but
retained repository evidence does not establish an actor or root cause. Recovery
commit `2aa421e1` preserves documentation work; it is not forensic proof of what
changed the index. Preserve staged, unstaged and untracked work, resolve the
worktree-specific index, and compare recovered edits with current `main` before
reapplying them. The required preservation and repair procedure is in
[operations](PUBLISHING_AND_OPERATIONS.md#local-git-index-recovery).

### September 2026 parser and precision repairs

- [PR #43](https://github.com/dbo99/brim-live-data-feeds/pull/43) preserves IEEE
  float values during NBM wind regridding, preventing source-scaled repacking
  from crossing otherwise ordered percentiles. Synthetic GRIB regression fixtures
  exercise the transformation; strict percentile and seven-target publication
  checks remain intact. The lesson is to repair precision loss rather than relax
  the validator.
- [PR #44](https://github.com/dbo99/brim-live-data-feeds/pull/44) accepts Delta X2
  inequalities and decimal thresholds, preserves the relation additively, and
  keeps missing/malformed X2 unavailable. Existing numeric fields continue to
  represent the reported threshold. Manual dispatch still cannot replace an
  already-published same-date report through the existing publisher; a newer
  report is required. Parser repair does not authorize a publication override.

### Integration drift

The official observed-wind feed identifies its current public contract as
RTW019. Separate integration material contains RTW020-oriented work that is not
the official Pages contract. Treat that difference as an intentional review
boundary until the producer, consumer, and public contract are changed together.

## Current strengths

- Fifteen writers have explicit ownership, bounded timeouts, isolated candidate
  artifacts and a shared non-cancelling publication queue.
- Candidate hashes, static path ownership and staged validation protect the Git
  transaction. Git history preserves prior publications; Pages delivery remains
  a separate step.
- Rolling forecast products use manifests, with compatibility views retained
  where the consumer contract requires them.
- Snow and groundwater have offline failure/completeness tests and validated
  local replacement. Snow exposes provider-level partial-refresh status;
  groundwater retains its API-latest minimum and all-chunk gate.
- CNRFC and CBRFC use separate reviewed rosters, semantic source fixtures,
  metric freshness/expiry and explicit family degradation. Their contracts do
  not infer unreported statistics or total Lake Mead inflow.
- Winter Storm Levels and NBM QPF validate complete retained cycles and target
  identity. Product-specific manifest/asset validation and failure tests protect
  last-known-good; neither accepts arbitrary partial-cycle publication.
- Sandbox/preview workflows support investigation without data publication.

Exact acceptance rules and current exceptions belong in [PRODUCTS.md](PRODUCTS.md)
and [WORKFLOW_AND_PRODUCT_STANDARDS.md](WORKFLOW_AND_PRODUCT_STANDARDS.md).

## Risk register

Priority uses `P0` for a confirmed active integrity/security emergency or current
outage, `P1` for the highest-priority latent integrity/reliability defect or
missing control, `P2` for material maintainability or reliability risk, and `P3`
for improvement work. The streamflow guard defect is P1 because the failure mode
is present but no active bad publication was confirmed at the audited baseline.
No item is P0 under this definition at that baseline; confirmed current
corruption or outage would require immediate P0 reclassification.

| Priority | Risk and evidence | Consequence | Recommended next control |
|---|---|---|---|
| P1 (highest current latent defect) | Streamflow workflow defines `USGS_STREAMFLOW_MIN_LATEST_Q_TO_PUBLISH`, while the builder reads a differently scoped site-count gate and can join an empty latest-value result to the static site set. | A successful publication may contain many features but effectively no current discharge values. | Align the configuration name and implementation; gate on non-null live observations and test empty-upstream behavior before publishing. |
| P1 | The verified history ruleset blocks deletion and non-fast-forward updates; ordinary updates still depend on maintainer review. See [SECURITY.md](../SECURITY.md#repository-access-and-branch-controls). | An unsafe ordinary push can change the Pages source; an incompatible new restriction could stop scheduled data writers. | Preserve human review and test exact human/Actions behavior before adopting any PR, check or update requirement. |
| P1 | Builders do not uniformly stage a complete output set and atomically replace final paths. Snow and groundwater now stage, validate, back up and promote their respective multi-file sets; Winter Storm Levels stages the bundle and promotes its manifest last. The control is not repository-wide and separate path replacements are not one filesystem transaction. | An interrupted local run in another builder can leave mixed-generation artifacts; a poorly scoped later commit could publish them. | Extend complete-set staging and failure tests product by product; retain product-specific rollback or manifest-last designs where one multi-path atomic primitive is unavailable. |
| P1 | There is no common machine-readable schema or compatibility test for public feeds. | A syntactically valid producer change can silently break a consumer. | Add versioned schemas or contract fixtures and run producer/consumer compatibility checks for changed products. |
| P2 | CoCoRaHS retrieval lacks a strong repository-wide minimum-completeness or pagination gate. | A partial upstream response can look like a valid low-count day. | Record expected coverage signals and classify partial responses as degraded or failed. |
| P2 | GFS and HRRR can publish useful but incomplete target sets under product-specific rules. | Target-time choices available to consumers may shrink unexpectedly. | Declare completeness/degraded status in manifests and alert on missing required targets. |
| P2 | Manifest identity and freshness fields are inconsistent across ASOS/AWOS, GFS, HRRR, and NBM. | Generic consumer and monitoring logic requires special cases and can misclassify freshness. | Define a common additive manifest envelope while retaining current product fields. |
| P2 | SCAN and snow combine generated live outputs with static/reference context tables. | An algorithm change can make live and context files semantically inconsistent. | Document per-file ownership and validate compatible water-year/date and unit assumptions together. |
| P2 | Public provenance/checksum fields remain product-specific; shared candidate integrity metadata is retained only as a short-lived artifact. | Public bytes alone may not establish the originating build or upstream response after artifact expiry. | Preserve sanitized release evidence and add public provenance only through a reviewed product/consumer change. |
| P2 | Eight writers use event-SHA metadata and publisher callbacks without separately recording preparation HEAD; see the [checkout matrix](PUBLISHING_AND_OPERATIONS.md#checkout-and-source-identity). | A branch advance can separate candidate-building code from its publisher callback version. | Extend actual build-SHA binding in a focused workflow change; record all available SHAs during incidents. |
| P2 | Scheduled data commits and code changes share `main`. | High-volume automated history can obscure review and complicate code rollbacks. | Preserve clear commit messages and consider a controlled publication branch only after evaluating Pages and consumer impact. |
| P2 | Feature-branch workflows now produce isolated evidence, while local builders can still write ordinary product paths. | A reviewer may mistake candidate artifacts for official data or accidentally commit local test output. | Label evidence, inspect exact changed paths and exclude generated test data unless David explicitly approves its publication. |
| P2 | Upstream rate limits, latency, format drift, and transient outages are external dependencies. Groundwater now has product-specific retry classification, a circuit breaker, parser accounting and all-chunk completeness, but these controls are not uniform. | Builds can time out, partially populate, or fail repeatedly without a source-code defect. | Extend bounded retries and concise diagnostics product by product, distinguish outage from schema change, and alert on sustained staleness. |
| P2 | Streamflow injects `API_USGS_PAT` into a build step whose script does not read it. | Unnecessary credential exposure remains even though preparation has read-only repository permission. | Remove the unused injection in a focused workflow change; follow [credential handling](../SECURITY.md#credential-handling). |
| P2 | Winter Storm Levels depends on NBM deterministic `SNOWLVL` inventories and the NOAA Open Data bucket, with NOMADS only documented as an alternate. No stable official contour-vector service or operational independent snow-level comparison was found. | Field naming/definition, bucket layout, inventory, or upstream model changes could stop publication; a visually plausible contour set could otherwise hide semantic drift. | Keep exact field/level/unit/time checks, monitor official NBM change notices, exercise NOMADS retrieval before declaring it an automatic fallback, and compare winter cases against CNRFC/NDFD guidance without silently changing the contract. |
| P2 | The CNRFC major water-supply product depends on labeled server-rendered HTML and inline chart-title/table text because the reviewed official bulk products omit percent-of-median, product 2 is a page table and product 7 requires headline/tabular cross-checking. MHBC1 currently has no official product-2 table. | A CNRFC presentation or per-location availability change can degrade records even when other hydrologic products still exist upstream. | Retain focused and fuller sanitized fixtures, fail ambiguous labels and table conflicts, alert from independent family/metric health, treat new MHBC1 availability as a roster notice, and review an official structured source if CNRFC publishes one with the complete direct-value contract. |
| P2 | The CBRFC GLDA3 point endpoint is structured but currently served with a non-JSON content type; its official list may omit a newer point issue, and blank-date zero fields are source sentinels. | Loose content-type, sentinel or list-fallback handling could reject valid official data or publish raw guidance/false zeros. | Keep the point `off*` fields authoritative, require a valid official issue date for numeric values, bound and parse the response strictly, treat the list as secondary QA, and retain adversarial fixtures. |
| P2 | The CBRFC Lake Powell dashboard is server-rendered with no reviewed separate public backend; it is the water-year primary and only a second official representation, not proven independent evidence, for April-July. It supplies percent of mean but no water-year percent of median. | Presentation drift, lag or loose row selection could swap periods, infer a missing statistic, or silently publish the wrong value. | Require the exact Lake Powell heading, ordered headers and semantic rows; compare current Apr-Jul representations at source-displayed precision, notice omission/lag, fail material conflict, bind direct water-year values into the signature, omit percent of median, and isolate period failures. |
| P2 | The CBRFC Lake Mead Local special product is a sectioned text/CSV file; the July 1, 2026 issue regresses January to 2026 within an otherwise July 2026-June 2027 series, while the direct total-Lake-Mead structured request returns `false`. | A broad repair could hide a future source defect, publish a duplicate/backward month, sum a monthly series, or invent total Lake Mead inflow from Powell plus local flow. | Keep the correction keyed to `CBRFC_LKSA3_LOCAL_JANUARY_ROLLOVER_2026`, the exact issue/row and the June 1 archived confirmation; publish raw/corrected provenance and an operational notice; reject every other ambiguity, publish no aggregate, and defer total Lake Mead until CBRFC exposes a direct dated product. |
| P2 | No repository license file exists at the baseline. | Contributors and data reusers do not have an explicit repository-wide grant. | Have the owner select and add appropriate code and data licensing; do not infer terms in the meantime. |
| P3 | Workflow wiring remains repeated although Git publication and R setup now use shared helpers. | Callback/source/path wiring can drift across writers. | Preserve the shared safety tests and compare all fifteen workflows after a common-control change. |
| P3 | Documentation can drift from schedules, actions, scripts, and public paths. | Operators may follow obsolete instructions. | Run the documentation audit in [README.md](README.md) after workflow or contract changes. |

## Risk themes

### Empty is not the same as failed

HTTP success, valid JSON, and a nonzero feature count do not prove that a live feed
contains useful current values. Joins against static station inventories can mask
an empty observation response. Gates should measure the live fields consumers
need, and summaries should expose null/non-null counts.

### Partial is not automatically safe

Some products remain useful with partial coverage; others become misleading.
Every product should explicitly choose among fail-and-retain, publish-degraded, or
publish-partial-with-status. The current repository does not implement one common
policy, so product-specific rules in [PRODUCTS.md](PRODUCTS.md) remain controlling.

### Git safety is part of data safety

Reference selection, concurrency, path allowlists, pull behavior, and non-force
pushes affect the public dataset. A change that modifies these controls deserves
the same review as a schema or unit change.

### Public artifacts form an API

File names, field names, units, time interpretation, nullability, target selection,
and freshness behavior are consumer-visible. Stable URLs alone do not provide
compatibility.

## Decision record for maintainers

When resolving a risk or making an architectural change, record:

1. The observed problem and evidence.
2. The affected product paths and consumer behavior.
3. The chosen fail/degrade/fallback behavior.
4. Validation and rollback evidence.
5. Whether the public contract, product catalog, operations guide, and standards
   were updated.

Avoid marking a risk resolved because documentation now mentions it. Resolution
requires an implemented control and evidence that the failure mode is covered.

## Review cadence

Review P1 items before any related producer or workflow change. Review the entire
register after adding a product, changing a public path/schema/unit, changing
publication logic, or responding to a feed incident. A periodic documentation
audit should compare this register to repository state and recent Actions history.
