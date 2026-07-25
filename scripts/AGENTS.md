# Builder Agent Instructions

These instructions apply to `scripts/`. Root `AGENTS.md` also applies. Builder
changes can alter the public API even when output file names do not change.

## Before editing

Read:

- The product section in `docs/PRODUCTS.md`.
- `docs/BRIM_CONSUMER_CONTRACT.md` for consumed paths and semantics.
- The matching workflow in `.github/workflows/`.
- `docs/WORKFLOW_AND_PRODUCT_STANDARDS.md`.
- `BUILD.md` for safe local reproduction.

Identify every output the script can touch, every upstream dependency, relevant
environment configuration, and failure behavior before changing code.

## Retrieval and temporary state

- Use public, documented upstream services.
- Set finite connection/request timeouts and bounded retries with backoff.
- Distinguish transient transport failure from an upstream schema/meaning change.
- Do not log credentials, request authorization, cookies, or full environment
  state.
- Keep downloaded/intermediate files in a temporary location.
- Derive file names from allowlisted/validated identifiers, never raw upstream
  paths.
- Bound response, decompression, and parsed-object sizes where practical.
- Clean up temporary state without deleting user or repository paths.

Several current builders write final tracked files directly. For new work, prefer
building and validating the complete output set outside `docs/data/`, then
replacing the declared files together. Do not rewrite an existing builder solely
for style when atomic publication is not the requested change.

## Empty, partial, and degraded data

Define the product's acceptance rule before implementation:

- **Fail and retain:** no publication; prior files remain.
- **Publish degraded:** publish with an explicit machine-readable status.
- **Publish partial:** allowed only with a product-specific completeness rule and
  visible coverage.

Check non-null current measurements, not just rows/features; static inventory
joins can mask an empty observation response. Evaluate page/tile/target
completeness explicitly. Do not silently change acceptance rules without
consumer/operations review.

## Serialization contracts

Preserve documented:

- Repo-relative output paths and compatibility views.
- JSON/GeoJSON top-level shape and required properties.
- CSV headers, ordering guarantees where documented, delimiter/quoting, and
  missing-value representation.
- WGS84 longitude/latitude order and valid coordinate bounds.
- Field units and conversions.
- UTC/offset-bearing timestamp formats and the difference between observation,
  valid, model-cycle, forecast-hour, generation, and publication times.
- Null meanings and fallback behavior.
- Manifest selection and target-file identity.

Unknown fields should normally be additive. Removing, renaming, retyping, changing
units, changing null behavior, or changing time meaning requires the contract
migration sequence.

Current manifests are not one schema; do not normalize them incidentally. Keep
multi-file products generation-coherent and never reference a missing target.
Confirm generated/static ownership before rewriting context. Exact file sets and
current QA are authoritative in `docs/PRODUCTS.md`.

## Validation

Test the smallest complete set that exposes changed failure modes: parse outputs;
check keys/types/nullability; measure non-null coverage; validate coordinates,
ranges, units, and times; resolve manifests; compare count/size/runtime; exercise
empty/partial/malformed/slow cases where feasible; preserve last-known-good;
confirm only declared paths changed; and assess consumer compatibility.

There is no universal dry-run or fixture mode. Run production builders only in an
isolated working copy/intentional feature branch, and never dispatch or publish as
an implicit test.

## Code-change discipline

Keep product-specific code legible. Use shared abstractions only when tested and
clearly safer; do not impose a common schema/framework because products look
similar.

Preserve stable, deterministic output ordering and formatting where practical to
keep generated diffs reviewable. Avoid embedding volatile build-only detail unless
it is a documented contract field.

Report commands run, upstream conditions, files changed, counts/coverage, known
unexercised failure cases, and whether generated output is intended for review.
Do not stage, commit, push, merge, or publish without explicit authorization.
