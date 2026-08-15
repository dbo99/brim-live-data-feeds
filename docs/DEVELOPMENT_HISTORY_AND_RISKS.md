# Development History and Risks

This is an evidence-based maintenance record for the live-feed system. It
distinguishes observed repository facts from recommended controls. It is not a
release changelog and does not replace Git history.

## History that shaped the current design

### Product expansion

The repository grew from individual uploaded datasets into thirteen independently
scheduled product families plus the manual-first Winter Storm Levels production
candidate. The resulting architecture deliberately keeps
product-specific scripts, schedules, outputs, and validation because the upstream
services and consumer semantics differ substantially.

### Publication-safety repairs

In July 2026, publication behavior was corrected in stages:

- Commit `169ec10` made the ASOS/AWOS and GFS writers publish back to the selected
  branch.
- Pull request 2 merged that change at `a121115`.
- Commit `4bb8da8` extended branch-safe publication to the remaining feed writers.
- Commit `e90b637` corrected a stale-checkout failure mode in the NBM publisher.

The durable lesson is that checking out one reference and pushing another is a
data-integrity problem, not just a developer inconvenience. Writer workflows now
derive both serialization and push behavior from the selected reference and use a
shared reference-scoped writer concurrency group.

### Runtime maintenance

Commit `6cdb8cd` updated artifact uploads to the Node 24-backed major release used
by the current workflows. The lesson is to treat action-runtime warnings as
planned maintenance: confirm every occurrence, update all matching workflows, and
preserve retention and artifact naming.

### Operational failures

Historical runs exposed two recurring classes of failure:

- An NBM publication could build against stale checked-out state, motivating the
  explicit checkout correction.
- In a snow-pillow incident, all three NRCS/AWDB preflight stations timed out.
  The workflow stopped before the full producer fetch or publication, the prior
  official product remained, and a later scheduled run succeeded. The producer
  now attempts AWDB and CDEC independently: one healthy provider can refresh
  while validated prior rows for the failed provider are preserved, but
  both-provider failure or invalid/unavailable prior provider rows still stops
  before replacement. A follow-up audit of 20 successful tracked products set
  provider-specific completeness gates below observed healthy coverage but far
  above the former approximately 20% defaults; invalid or weakening environment
  overrides now fail closed. The incident was an upstream
  availability/timeout event; it is not evidence by itself of general
  workflow-runtime exhaustion.
- Snow run `30177980668`, the first scheduled run after pull request #6, passed
  both provider and completeness checks and selected a full refresh, then
  falsely rejected three required GeoJSON properties during staged validation.
  All three keys were serialized but null for every feature; `jsonlite` retained
  the keys as null list members, while the subsequent `dplyr::bind_rows()` call
  dropped the resulting all-null columns. The commit step was skipped, so the
  official four-file product remained unchanged. The correction gives prior
  carry-forward and prospective staged outputs distinct validation entry points,
  preserves typed all-null properties during parsing, and continues to reject a
  truly omitted required key.
- Groundwater run `30206927617` on July 26, 2026 routed all 38 outer chunks from
  the primary client to its direct-request fallback, retrieved zero raw rows and
  zero API-latest sites, and ended with 50 or more unexpanded warnings. The
  source and dependency versions matched successful July 25 and July 27 runs;
  July 29 independently exposed one primary HTTP 502 that the direct path
  recovered. The evidence supports a transient upstream response failure,
  likely widespread 5xx behavior, rather than downstream parsing, although the
  exact failed statuses and bodies are unrecoverable from the retained log. The
  300-site gate retained the prior official product and the next three
  scheduled runs recovered. The producer now uses bounded retry classification,
  concise per-chunk diagnostics, parser/filter accounting, a
  three-consecutive-failure circuit breaker, all-chunk completeness, and staged
  two-file promotion.

Run identifiers are operational evidence, not stable documentation links. Retain
them in the relevant incident record when an event is investigated.

### Integration drift

The official observed-wind feed identifies its current public contract as
RTW019. Separate integration material contains RTW020-oriented work that is not
the official Pages contract. Treat that difference as an intentional review
boundary until the producer, consumer, and public contract are changed together.

## Current strengths

- Fourteen product families or coordinated production candidates have explicit
  workflow and builder ownership.
- Production writers use bounded timeouts and reference-scoped non-cancelling
  concurrency.
- Git commits retain prior published states and support normal rollback.
- Rolling forecast products expose manifests instead of requiring directory listing.
- GeoJSON feeds are paired with compact summaries where the current contract
  defines them.
- Sandbox/preview workflows separate selected investigation from production
  publication.
- Current artifact upload steps use the same maintained major release.
- Snow has deterministic provider-failure tests, provider-level partial-refresh
  status, provider-specific completeness and override tests, validated
  last-known-good carry-forward, and staged four-file local replacement.
- Groundwater has deterministic offline tests for retry classification,
  circuit breaking, parser/filter accounting, all-chunk completeness, its
  unchanged 300-site minimum, and rollback-capable two-file replacement.
- CNRFC major water-supply basin forecasts have a reviewed 51-record
  RFC/LID/product roster, semantic-label fixtures for products 9, 2 and 7,
  direct-index and ordered accumulation-horizon tests, strict bootstrap gates,
  independent steady-state family degradation, metric-level freshness/expiry,
  explicit attempt taxonomy, no-op canonical comparison and native Pacific
  scheduling. A July 31, 2026 design probe found the official MHBC1 product-2
  control disabled and its page explicitly unavailable; the other 14 reviewed
  product-2 pages and all 18 product-7 identities used the expected direct-value
  structures. Product-7 semantic parser/signature v2 now retains the directly
  published Percent of Median alongside forecast volume, Percent of Mean and the
  tabular mean reference volume; all four metrics have independent state and
  no percentage arithmetic. The separate CBRFC product has two reviewed GLDA3
  period identities and one LKSA3 Lake Mead Local monthly-series identity,
  structured official-field, semantic-page and special-product fixtures,
  date-precision period-specific freshness/expiry, raw-guidance exclusion,
  display-precision dashboard cross-checking, explicit popup-link roles, one
  archive-confirmed January label correction, controlled-date year-round
  bootstrap, independent family degradation and its own daily workflow/
  publication transaction. A direct total-Lake-Mead product remains deferred
  after the special-products pages, Upper and Lower dashboards, reservoir
  listings and official structured request exposed no complete direct record.
- Winter Storm Levels has an official NBM snow-level source, exact inventory and
  range retrieval, a versioned checksum manifest, complete-cycle gating,
  deterministic full-grid isoband contour/bounds/serialization tests, semantic
  no-op behavior, manifest-last promotion with injected-failure coverage, and
  standalone browser QA. The first official publication and recurring schedule
  remain deliberately unapproved.

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
| P1 | `main` now has an active no-deletion/no-non-fast-forward history ruleset with no bypass actors, but classic protection and required pull-request review/status checks remain absent. | A direct fast-forward push or insufficiently reviewed merge can immediately change the official Pages source. | Retain the history ruleset and add required pull requests, successful checks, and restricted direct pushes for `main`. |
| P1 | Builders do not uniformly stage a complete output set and atomically replace final paths. Snow and groundwater now stage, validate, back up and promote their respective multi-file sets; Winter Storm Levels stages the bundle and promotes its manifest last. The control is not repository-wide and separate path replacements are not one filesystem transaction. | An interrupted local run in another builder can leave mixed-generation artifacts; a poorly scoped later commit could publish them. | Extend complete-set staging and failure tests product by product; retain product-specific rollback or manifest-last designs where one multi-path atomic primitive is unavailable. |
| P1 | There is no common machine-readable schema or compatibility test for public feeds. | A syntactically valid producer change can silently break a consumer. | Add versioned schemas or contract fixtures and run producer/consumer compatibility checks for changed products. |
| P2 | CoCoRaHS retrieval lacks a strong repository-wide minimum-completeness or pagination gate. | A partial upstream response can look like a valid low-count day. | Record expected coverage signals and classify partial responses as degraded or failed. |
| P2 | GFS and HRRR can publish useful but incomplete target sets under product-specific rules. | Target-time choices available to consumers may shrink unexpectedly. | Declare completeness/degraded status in manifests and alert on missing required targets. |
| P2 | Manifest identity and freshness fields are inconsistent across ASOS/AWOS, GFS, HRRR, and NBM. | Generic consumer and monitoring logic requires special cases and can misclassify freshness. | Define a common additive manifest envelope while retaining current product fields. |
| P2 | SCAN and snow combine generated live outputs with static/reference context tables. | An algorithm change can make live and context files semantically inconsistent. | Document per-file ownership and validate compatible water-year/date and unit assumptions together. |
| P2 | Product-specific QA has no shared checksum, provenance, or build metadata standard. | It is harder to prove which upstream response and builder version created a file. | Add non-sensitive provenance and checksums to summaries/manifests or retained build reports. |
| P2 | Scheduled data commits and code changes share `main`. | High-volume automated history can obscure review and complicate code rollbacks. | Preserve clear commit messages and consider a controlled publication branch only after evaluating Pages and consumer impact. |
| P2 | Most feature-branch manual writers use ordinary product paths on that branch; the CNRFC, CBRFC, and Winter Storm Levels forecast writers are dry-run-only exceptions. | A reviewer may mistake branch artifacts for official data or accidentally merge generated changes. | Label evidence clearly, review the changed-file set, and do not merge test-generated feed files unless that is the intended release. |
| P2 | Upstream rate limits, latency, format drift, and transient outages are external dependencies. Groundwater now has product-specific retry classification, a circuit breaker, parser accounting and all-chunk completeness, but these controls are not uniform. | Builds can time out, partially populate, or fail repeatedly without a source-code defect. | Extend bounded retries and concise diagnostics product by product, distinguish outage from schema change, and alert on sustained staleness. |
| P2 | Winter Storm Levels depends on NBM deterministic `SNOWLVL` inventories and the NOAA Open Data bucket, with NOMADS only documented as an alternate. No stable official contour-vector service or operational independent snow-level comparison was found. | Field naming/definition, bucket layout, inventory, or upstream model changes could stop publication; a visually plausible contour set could otherwise hide semantic drift. | Keep exact field/level/unit/time checks, monitor official NBM change notices, exercise NOMADS retrieval before declaring it an automatic fallback, and compare winter cases against CNRFC/NDFD guidance without silently changing the contract. |
| P2 | The CNRFC major water-supply product depends on labeled server-rendered HTML and inline chart-title/table text because the reviewed official bulk products omit percent-of-median, product 2 is a page table and product 7 requires headline/tabular cross-checking. MHBC1 currently has no official product-2 table. | A CNRFC presentation or per-location availability change can degrade records even when other hydrologic products still exist upstream. | Retain focused and fuller sanitized fixtures, fail ambiguous labels and table conflicts, alert from independent family/metric health, treat new MHBC1 availability as a roster notice, and review an official structured source if CNRFC publishes one with the complete direct-value contract. |
| P2 | The CBRFC GLDA3 point endpoint is structured but currently served with a non-JSON content type; its official list may omit a newer point issue, and blank-date zero fields are source sentinels. | Loose content-type, sentinel or list-fallback handling could reject valid official data or publish raw guidance/false zeros. | Keep the point `off*` fields authoritative, require a valid official issue date for numeric values, bound and parse the response strictly, treat the list as secondary QA, and retain adversarial fixtures. |
| P2 | The CBRFC Lake Powell dashboard is server-rendered with no reviewed separate public backend; it is the water-year primary and only a second official representation, not proven independent evidence, for April-July. It supplies percent of mean but no water-year percent of median. | Presentation drift, lag or loose row selection could swap periods, infer a missing statistic, or silently publish the wrong value. | Require the exact Lake Powell heading, ordered headers and semantic rows; compare current Apr-Jul representations at source-displayed precision, notice omission/lag, fail material conflict, bind direct water-year values into the signature, omit percent of median, and isolate period failures. |
| P2 | The CBRFC Lake Mead Local special product is a sectioned text/CSV file; the July 1, 2026 issue regresses January to 2026 within an otherwise July 2026-June 2027 series, while the direct total-Lake-Mead structured request returns `false`. | A broad repair could hide a future source defect, publish a duplicate/backward month, sum a monthly series, or invent total Lake Mead inflow from Powell plus local flow. | Keep the correction keyed to `CBRFC_LKSA3_LOCAL_JANUARY_ROLLOVER_2026`, the exact issue/row and the June 1 archived confirmation; publish raw/corrected provenance and an operational notice; reject every other ambiguity, publish no aggregate, and defer total Lake Mead until CBRFC exposes a direct dated product. |
| P2 | No repository license file exists at the baseline. | Contributors and data reusers do not have an explicit repository-wide grant. | Have the owner select and add appropriate code and data licensing; do not infer terms in the meantime. |
| P3 | Workflow setup and Git publication blocks are repeated. | Safety fixes must be applied consistently across many files. | Introduce reuse only if it keeps product paths, permissions, and validation legible. |
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
