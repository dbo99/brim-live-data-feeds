# Development History and Risks

This is an evidence-based maintenance record for the live-feed system. It
distinguishes observed repository facts from recommended controls. It is not a
release changelog and does not replace Git history.

## History that shaped the current design

### Product expansion

The repository grew from individual uploaded datasets into eleven independently
scheduled product families. The resulting architecture deliberately keeps
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
  before replacement. The incident was an upstream availability/timeout event;
  it is not evidence by itself of general workflow-runtime exhaustion.

Run identifiers are operational evidence, not stable documentation links. Retain
them in the relevant incident record when an event is investigated.

### Integration drift

The official observed-wind feed identifies its current public contract as
RTW019. Separate integration material contains RTW020-oriented work that is not
the official Pages contract. Treat that difference as an intentional review
boundary until the producer, consumer, and public contract are changed together.

## Current strengths

- Eleven product families have explicit workflow and builder ownership.
- Production writers use bounded timeouts and reference-scoped non-cancelling
  concurrency.
- Git commits retain prior published states and support normal rollback.
- Wind forecast products expose manifests instead of requiring directory listing.
- GeoJSON feeds are paired with compact summaries where the current contract
  defines them.
- Sandbox/preview workflows separate selected investigation from production
  publication.
- Current artifact upload steps use the same maintained major release.
- Snow has deterministic provider-failure tests, provider-level partial-refresh
  status, validated last-known-good carry-forward, and staged four-file local
  replacement.

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
| P1 | `main` had no observed branch protection or ruleset at the baseline. | A direct push or insufficiently reviewed merge can immediately change the official Pages source. | Require pull requests, successful checks, and restricted direct pushes for `main`. |
| P1 | Builders do not uniformly stage a complete output set and atomically replace final paths. Snow now stages, validates, backs up and promotes its four-file set, but the control is not repository-wide and four separate path replacements are not one filesystem transaction. | An interrupted local run in another builder can leave mixed-generation artifacts; a poorly scoped later commit could publish them. | Extend complete-set staging and failure tests product by product; retain product-specific rollback where one multi-path atomic primitive is unavailable. |
| P1 | There is no common machine-readable schema or compatibility test for public feeds. | A syntactically valid producer change can silently break a consumer. | Add versioned schemas or contract fixtures and run producer/consumer compatibility checks for changed products. |
| P2 | CoCoRaHS retrieval lacks a strong repository-wide minimum-completeness or pagination gate. | A partial upstream response can look like a valid low-count day. | Record expected coverage signals and classify partial responses as degraded or failed. |
| P2 | GFS and HRRR can publish useful but incomplete target sets under product-specific rules. | Target-time choices available to consumers may shrink unexpectedly. | Declare completeness/degraded status in manifests and alert on missing required targets. |
| P2 | Manifest identity and freshness fields are inconsistent across ASOS/AWOS, GFS, HRRR, and NBM. | Generic consumer and monitoring logic requires special cases and can misclassify freshness. | Define a common additive manifest envelope while retaining current product fields. |
| P2 | SCAN and snow combine generated live outputs with static/reference context tables. | An algorithm change can make live and context files semantically inconsistent. | Document per-file ownership and validate compatible water-year/date and unit assumptions together. |
| P2 | Product-specific QA has no shared checksum, provenance, or build metadata standard. | It is harder to prove which upstream response and builder version created a file. | Add non-sensitive provenance and checksums to summaries/manifests or retained build reports. |
| P2 | Scheduled data commits and code changes share `main`. | High-volume automated history can obscure review and complicate code rollbacks. | Preserve clear commit messages and consider a controlled publication branch only after evaluating Pages and consumer impact. |
| P2 | Feature-branch manual writers use ordinary product paths on that branch. | A reviewer may mistake branch artifacts for official data or accidentally merge generated changes. | Label evidence clearly, review the changed-file set, and do not merge test-generated feed files unless that is the intended release. |
| P2 | Upstream rate limits, latency, format drift, and transient outages are external dependencies. | Builds can time out, partially populate, or fail repeatedly without a source-code defect. | Bound retries, retain concise diagnostics, distinguish outage from schema change, and alert on sustained staleness. |
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
