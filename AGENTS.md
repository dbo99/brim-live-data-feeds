# Repository Agent Instructions

These instructions apply to the entire repository. Before changing a file, also
read the nearest nested `AGENTS.md`; nested instructions add requirements for
their directory.

## Mission and boundary

This repository produces and publishes BRIM's public live-data feeds. Scheduled
GitHub Actions call product-specific R builders and commit accepted artifacts
under `docs/data/`. GitHub Pages serves `main/docs`.

A separate private BRIM application consumes these files. Private consumer
behavior may be inspected read-only when the user makes it available and
compatibility verification requires it, but do not copy private source, internal
inventories, credentials, or personal filesystem details into this public
repository.

The official public state comes from `main/docs`. A writer run on a feature branch
updates ordinary product paths on that branch and is not an official publication.

## Start every task safely

Before editing:

1. Identify the exact allowed files and actions.
2. Inspect the repository root, branch, HEAD, staged/unstaged state, and
   untracked files.
3. Read the nearest nested instructions and controlling documentation.
4. Preserve every unrelated tracked or untracked file.

Do not fetch, pull, switch/create/delete branches, reset, clean, stage, commit,
push, dispatch a workflow, merge, change Pages, or alter repository settings
unless explicitly required. A request to inspect, diagnose, review, document, or
build locally does not grant publication authority.

Never use destructive Git or filesystem commands to simplify a dirty working
tree. Work around existing changes or stop if the requested edit would overwrite
them.

## Documentation authority

Use the four technical sources of truth:

- `docs/PRODUCTS.md` for product implementation and paths.
- `docs/BRIM_CONSUMER_CONTRACT.md` for public compatibility.
- `docs/PUBLISHING_AND_OPERATIONS.md` for publication and incidents.
- `docs/WORKFLOW_AND_PRODUCT_STANDARDS.md` for current/target controls.

`docs/README.md` explains the authority model. Architecture, history/risk, build,
security, contribution, README, and handoff documents route to or interpret the
four sources; they must not silently redefine them.

If documentation conflicts with implementation/artifacts, report exact evidence.
Keep current behavior, required procedure, recommendations, and gaps distinct.

## Product and compatibility rules

Treat every published path, field name, unit, timestamp, null meaning, freshness
rule, manifest selection rule, and fallback behavior as API surface. Stable URLs
alone do not prove compatibility.

For public contract changes:

1. Identify all producer outputs and consumer reads.
2. Prefer an additive producer change.
3. Update the product catalog and consumer contract in the same change.
4. Validate consumer support without exposing private implementation.
5. Add consumer support before removing compatibility output.
6. Remove old behavior only with explicit deprecation approval.

Do not infer a common schema or manifest envelope. Rolling forecast consumers
must resolve targets through the product manifest.

## Data integrity

A successful process exit, valid JSON, or nonzero feature count is insufficient.
Validate the live fields the consumer needs. Static station joins can hide an
empty upstream observation response.

For generated data, verify the output allowlist; parseability; required
structure; non-null live coverage; geometry/bounds; finite values; unit/time
semantics; manifest coherence; meaningful count/size/runtime drift; and
last-known-good behavior.

Partial publication must be an explicit product choice: fail-and-retain,
publish-degraded, or publish-partial-with-status. Do not invent a repository-wide
policy where none exists.

Several builders write final tracked paths directly. Prefer temporary
build/validation followed by complete-set replacement, and never claim atomicity
that is not implemented. Read `docs/DEVELOPMENT_HISTORY_AND_RISKS.md` for
current defects and risk priorities.

## File and disclosure discipline

Use repo-relative paths in tracked documentation; `$HOME` is acceptable as a
portable checkout example. Do not add personal paths/usernames, credentials or
credential-shaped examples, authorization material, private consumer excerpts,
or internal-only inventories.

Do not modify generated feeds during a documentation-only task. Do not reformat or
rewrite unrelated files. Do not add a new schema, manifest, license, generated
report, or planning document unless it is in scope.

When editing local files, use precise patches. Treat `.DS_Store` and other
unrelated untracked files as user-owned and leave them unchanged.

## Validation expectations

Use checks proportionate to risk. A normal handoff includes:

```sh
cd "$HOME/Documents/brim-live-data-feeds_source_repo"
git status --short --branch
git diff --name-only
git diff --cached --name-only
git diff --check
```

Also parse relevant YAML/JSON/GeoJSON, inspect CSV structure, validate Markdown
links/repo paths, reconcile inventories when claiming completeness, scan for
sensitive/private material, and confirm the changed/staged set is authorized.

For documentation-only work, read-only parsing of current public artifacts is
appropriate; running or dispatching production builders merely to refresh evidence
is not.

Report commands/checks run, passed/failed results, skipped checks, exact remaining
Git status, current risks, and whether any commit/push/deployment/publication
occurred. Never claim an action that did not succeed.

## Maintainer decision points

David, the repository owner and maintainer, retains the explicit decision to
merge a pull request, dispatch or publish official production products, and
approve an official rollback. Codex or another agent may prepare changes,
commits, pushes, and pull requests only when authorized. An agent must not merge,
dispatch production, publish, or roll back official state without David's
explicit approval.

Stop and request direction if completion requires a choice about a breaking public
contract, previously undefined partial/degraded behavior, generated artifacts to
merge, repository rules/settings, secrets, Pages configuration, licensing,
official dispatch/publication, rollback, or merge.

Make evidence-based assumptions within the explicit task. Do not use an ambiguous
request to expand scope across repositories or operational boundaries.
