# Workflow Agent Instructions

These instructions apply to `.github/workflows/`. Root `AGENTS.md` also applies.
Workflow changes are production-control changes because they determine what can
write public feed paths.

## Before editing

Read:

- `docs/PRODUCTS.md` for the affected workflow's exact trigger, builder, outputs,
  format, and current gaps.
- `docs/PUBLISHING_AND_OPERATIONS.md` for dispatch and publication behavior.
- `docs/WORKFLOW_AND_PRODUCT_STANDARDS.md` for current versus recommended workflow
  controls.
- `docs/BRIM_CONSUMER_CONTRACT.md` if any public file, manifest, field, unit, time,
  or fallback behavior may change.

Compare the proposed edit with every production writer when it affects a shared
pattern. Do not assume a safe change in one workflow was already applied to the
others.

## Workflow classes

Production writers have scheduled/manual triggers and Git publication authority.
The HRRR sandbox and groundwater preview are read-only, artifact-only support
workflows. The wind watchdog reads wind manifests/state and can dispatch
ASOS/AWOS, GFS, HRRR, and NBM writers on the selected ref; it does not itself
commit or publish repository data.

Do not give support workflows production writer permissions or publication steps
without an explicit design change.

## Production writer invariants

Preserve or deliberately improve all of these:

- Explicit least-privilege `contents: write`.
- Checkout of the selected reference.
- Shared concurrency group scoped to the selected Git reference.
- `cancel-in-progress: false` so another product writer cannot be cancelled midway
  through publication.
- Bounded job timeout with margin for validation and Git publication.
- One documented builder entry point.
- An exact output-path allowlist matching `docs/PRODUCTS.md`.
- Product-specific validation before Git staging/commit.
- No-op success when accepted outputs are unchanged.
- Product-specific generated-data commit message and normal non-force push back
  to the selected branch.
- No broad staging of `docs/`, the repository root, or unrelated products.

All writers share the branch because they commit generated files. Changing the
concurrency group is a cross-product safety change. A manual run on a feature
branch may write ordinary product paths on that branch; it does not update
official Pages served from `main/docs`.

## Triggers and permissions

Retain the current schedule/manual shape unless the task explicitly changes it.
Evaluate schedules in UTC, upstream availability, overlapping writers, runtime,
rate limits, staleness tolerance, and Pages churn.

There are no pull-request triggers in the current workflow set. Do not expose
write permissions or repository secrets to untrusted pull-request code. Prefer a
separate read-only validation workflow if pull-request execution becomes a
requirement.

The wind watchdog's `actions: write` scope exists for dispatching ASOS/AWOS,
GFS, HRRR, and NBM writers on the selected ref. Sandbox and preview jobs should
remain `contents: read`.

## Dependencies and actions

Review publisher, version/runtime support, inputs, permissions, and data exposure
for every action update. Apply shared runtime remediations consistently unless a
documented exception exists. Preserve artifact identity/retention unless it is in
scope, and keep system/R dependencies aligned with the builder. Current versions
and product-specific tools are documented in the workflow standards.

## Retrieval and secrets

Pass secrets only to the step that needs them. The groundwater workflow may inject
`API_USGS_PAT`; never print, serialize, upload, or echo its value. Keep debug logs
and artifacts free of request headers and environment dumps.

Bound network operations with the job timeout and builder-level retries/timeouts.
More retries are not always safer: leave time for validation and publication.

## Publication block review

For changes to Git commands, verify:

1. The checked-out reference, concurrency reference, commit target, and push
   target are the same selected branch.
2. The path list includes every intended output and nothing else.
3. A rejected/failed builder cannot create an accepted commit.
4. An unchanged build exits without an empty commit.
5. Concurrent writers serialize rather than force-push over each other.
6. A push conflict fails visibly and preserves remote state.
7. Generated files are coherent before staging.

Do not add automatic force, destructive reset, or branch fallback behavior.

## Validation

Parse changed YAML and inspect rendered triggers. Verify permissions, concurrency,
timeout, checkout ref, action versions, builder command, artifact behavior,
staged paths, commit message, and push target. Search equivalent workflows after
a shared-control edit, compare declared paths to the product catalog, and review
the diff for accidental schedule/permission/secret changes. Prefer read-only or
sandbox evidence when it is safe and in scope.

For incidents, retain the ref/SHA, first failing step, relevant sanitized log,
artifact state, product/Pages state, comparable success, and last-known-good
proof. Route the full investigation through the operations guide.

Do not dispatch a production workflow without explicit maintainer authorization.
Report any behavior that could not be tested and the remaining publication risk.
