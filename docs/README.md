# Documentation Guide

This directory contains the public feed artifacts served by GitHub Pages and the
authoritative documentation for the BRIM live-data producer. Markdown files
describe contracts and operations; `data/` contains generated or maintained
consumer-facing datasets.

Baseline audited: 2026-07-24 at
`d39a12843148e05ed87346c309b962eb2d02318c`.

## Read by task

| Need | Start here |
|---|---|
| Understand the repository | [../README.md](../README.md) |
| Find a product path, workflow, script, schedule, unit, or QA rule | [PRODUCTS.md](PRODUCTS.md) |
| Understand what BRIM consumes and what counts as compatible | [BRIM_CONSUMER_CONTRACT.md](BRIM_CONSUMER_CONTRACT.md) |
| Dispatch, monitor, diagnose, roll back, back up, or recover | [PUBLISHING_AND_OPERATIONS.md](PUBLISHING_AND_OPERATIONS.md) |
| Review or design a builder/workflow | [WORKFLOW_AND_PRODUCT_STANDARDS.md](WORKFLOW_AND_PRODUCT_STANDARDS.md) |
| Understand boundaries and data flow | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Review history, known defects, and prioritized risks | [DEVELOPMENT_HISTORY_AND_RISKS.md](DEVELOPMENT_HISTORY_AND_RISKS.md) |
| Reproduce a builder locally | [../BUILD.md](../BUILD.md) |
| Propose a change | [../CONTRIBUTING.md](../CONTRIBUTING.md) |
| Apply security controls or report exposure | [../SECURITY.md](../SECURITY.md) |
| Orient a new coding-agent session | [../CODEX_HANDOFF.md](../CODEX_HANDOFF.md) |

## Authority model

Four documents are the technical sources of truth:

1. [PRODUCTS.md](PRODUCTS.md) owns the product inventory and current
   implementation facts.
2. [BRIM_CONSUMER_CONTRACT.md](BRIM_CONSUMER_CONTRACT.md) owns public path,
   semantics, compatibility, and producer/consumer change sequencing.
3. [PUBLISHING_AND_OPERATIONS.md](PUBLISHING_AND_OPERATIONS.md) owns execution,
   publication, incident, rollback, backup, and recovery procedures.
4. [WORKFLOW_AND_PRODUCT_STANDARDS.md](WORKFLOW_AND_PRODUCT_STANDARDS.md) separates
   current mechanics from recommended implementation standards.

Architecture, history/risk, build, security, contribution, handoff, and agent
instructions summarize or route to those sources. If a summary conflicts with a
source-of-truth document, correct the summary. If a source document conflicts with
repository behavior, record the discrepancy and resolve it through evidence
rather than silently rewriting history.

The files under `docs/data/` are the actual public artifacts. They remain decisive
for current serialized structure at a particular commit, while the consumer
contract determines which aspects are supported behavior rather than incidental
sample details.

## Current versus recommended

Documentation uses these labels:

- **Current** — directly observed in tracked implementation or artifacts at the
  reviewed baseline.
- **Required procedure** — the operational or contributor rule to follow now.
- **Recommended** — a proposed control or target state that is not yet uniformly
  implemented.
- **Known gap/risk** — observed absence, inconsistency, or failure mode.

Do not describe a recommendation as active merely because it appears in a guide.

## Ownership and update triggers

The repository maintainer owns final contract and publication decisions.
Contributors should update documentation in the same change when they modify:

- Product paths, schemas, properties, units, null behavior, or time semantics.
- Workflow schedules, permissions, concurrency, timeouts, action versions, or
  publication steps.
- Builder dependencies, upstream sources, completeness gates, or output sets.
- Manifest selection, compatibility views, freshness, fallback, or deprecation.
- Incident, recovery, or security procedures.
- Static context ownership for SCAN or snow.

Review the entire documentation set when adding/removing a production product or
changing the producer/consumer boundary.

## Maintenance cadence

Refresh the documentation:

- Every two weeks during active development.
- After adding or removing a product.
- After a product-contract change.
- After a workflow incident.
- After a major dependency or GitHub Action update.
- Before an archival or release baseline.

The audit must include the root and nested `AGENTS.md` byte sizes and the normal
root-to-leaf instruction totals. The normal chain ceiling is 32 KiB (32,768
bytes): root only for most files, root plus `.github/workflows/AGENTS.md` for
workflow files, and root plus `scripts/AGENTS.md` for builder files.

```sh
cd "$HOME/Documents/brim-live-data-feeds_source_repo"
wc -c AGENTS.md .github/workflows/AGENTS.md scripts/AGENTS.md
```

### Reusable Codex documentation-audit prompt

```text
Audit the BRIM live-data-feed documentation against the current checked-out
repository without changing source, generated products, private source, settings,
or publication state. Preserve unrelated work and read root/nested AGENTS.md.
Record branch and commit; inventory workflows, workflow-invoked scripts, all 12
product families, declared/staged outputs, manifests, artifacts, schedules,
permissions, concurrency, selected-ref publication, current QA, and known gaps.
Parse current JSON/GeoJSON and the four wind manifests; inspect CSV headers and
manifest target resolution. Compare public paths, exact field/type/unit/time/null
semantics, freshness/fallback, and compatibility status with the read-only
consumer contract without copying private source. Check current versus
recommended wording, Pages versus Git publication, public/private boundaries,
local Markdown links, repo-relative paths, credentials/authorization material,
and personal paths. Measure root and nested AGENTS.md plus normal root-to-leaf
chains against the 32,768-byte ceiling. Report exact discrepancies, evidence,
validation, unresolved uncertainty, and Git status; do not stage, commit, push,
dispatch, merge, or publish.
```

## Repository audit procedure

Run this audit after a material workflow/contract change and periodically during
maintenance:

1. Inventory tracked workflows, builder entry points, and files below `docs/data/`.
2. Map every production writer to its trigger, permission set, concurrency,
   timeout, dependency setup, command, artifact, declared outputs, commit message,
   and push reference.
3. Parse representative/current JSON and GeoJSON; inspect CSV headers, record
   counts, time fields, units, geometry, manifest entries, and output sizes.
4. Compare public producer paths and semantics with the consumer registry and
   helper behavior without copying private source into this repository.
5. Review recent workflow and data-commit history for failures, runtime warnings,
   partial sets, unexpected counts, and publication-safety changes.
6. Reconcile current facts across the four source documents.
7. Scan all changed documentation for broken relative links, nonexistent
   repo-relative paths, personal paths, credential-shaped material, and private
   implementation excerpts.
8. Confirm the diff contains only intended files and no generated feed or source
   changes unless explicitly part of the task.
9. Record unresolved ambiguity as a risk or open decision with evidence and an
   owner; do not invent a fact to close the audit.

For a documentation-only audit, do not run production builders or dispatch
workflows merely to refresh evidence. Use tracked state and read-only inspection.

## Conflict resolution

When implementation, artifacts, producer documentation, and consumer behavior
disagree:

1. Stop any proposed removal or incompatible publication.
2. Identify the exact commit, path, field, and product involved.
3. Determine whether the discrepancy is implementation drift, stale docs,
   malformed generated data, or an intentional compatibility layer.
4. Prefer an additive producer correction that preserves the current consumer.
5. Update producer and consumer in the order defined by
   [BRIM_CONSUMER_CONTRACT.md](BRIM_CONSUMER_CONTRACT.md).
6. Preserve rollback evidence and document the decision.

No document may authorize copying private consumer source, credentials, personal
absolute paths, or internal-only inventories into this public repository.
