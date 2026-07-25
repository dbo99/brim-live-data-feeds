# Contributing

Contributions should preserve the reliability and compatibility of BRIM's public
live-data feeds. Small changes can affect scheduled publication and downstream
maps, so start by classifying the change and reading the controlling contract.

There is no repository license file at this baseline. Public visibility does not
by itself define reuse or contribution licensing; ask the owner if terms affect
your proposed contribution.

## Start here

Read:

1. [README.md](README.md) for repository scope.
2. [docs/README.md](docs/README.md) for documentation authority.
3. [docs/PRODUCTS.md](docs/PRODUCTS.md) for the affected product.
4. [docs/BRIM_CONSUMER_CONTRACT.md](docs/BRIM_CONSUMER_CONTRACT.md) for public
   compatibility.
5. The nearest `AGENTS.md` before editing a scoped directory.

For workflow, operational, security, or local-build changes, also read the
corresponding guide.

## Change classes

| Class | Examples | Minimum review focus |
|---|---|---|
| Documentation-only | Clarification, corrected path, current-state audit | Accuracy, links, disclosure, and no generated-data changes |
| Builder implementation | Retrieval, transformation, validation, performance | Output allowlist, upstream behavior, empty/partial cases, units/time, last-known-good |
| Workflow control | Schedule, permissions, setup, timeout, concurrency, publication | Least privilege, selected reference, path list, runtime, collision behavior |
| Public contract | File path, schema, field meaning, unit, freshness, target selection | Producer and consumer compatibility, migration, rollback, documentation |
| Generated publication | Intended refreshed feed artifacts | Builder evidence, coherent output set, freshness, file size, declared paths |
| Static context | SCAN/snow reference tables or support material | Provenance, ownership, date/unit compatibility, affected builders/consumers |

Avoid combining unrelated classes in one change when separation makes review and
rollback clearer.

## Branch and working-tree discipline

Work on a feature branch. Before editing, inspect the branch, repository root, and
working tree. Existing modifications belong to the person who made them; do not
reset, clean, overwrite, stage, or reformat unrelated files.

Production writers push accepted outputs back to the selected reference. A manual
run on a feature branch changes ordinary product paths on that branch and must not
be described as official publication. Only the maintainer decides when to merge,
publish, or roll back official state.

## Plan changes that cross boundaries

Use an execution plan when a change affects multiple products, producer and
consumer behavior, workflow controls, schemas/paths, or publication safety. Keep
the plan with the pull-request description or agreed project record; do not add a
standalone planning document unless the maintainer requests it.

An ExecPlan is required for:

- A new product family.
- A breaking schema migration.
- A publication-architecture change.
- A coordinated producer/private-consumer rollout.
- A major GitHub Actions refactor.
- A credentials, identity, or permissions change.
- A performance or reliability initiative with broad effects.

A useful plan contains:

```text
Objective:
Current behavior and evidence:
In-scope files and declared generated outputs:
Consumer-visible changes:
Implementation sequence:
Validation and failure cases:
Compatibility or migration sequence:
Rollback:
Open decisions and owner:
```

Update the plan as evidence changes. Separate observed behavior from the proposed
standard.

## Implementation rules

- Keep public paths stable unless a reviewed migration requires a new path.
- Treat units, timestamps, nullability, ordering assumptions, and freshness as API
  behavior.
- Build from public upstream sources and do not copy private consumer source.
- Retrieve and validate before replacing the full intended output set where
  practical.
- Reject unusable empty data and explicitly classify allowed partial data.
- Keep workflow permissions minimal and pushes non-force.
- Use the selected Git reference consistently.
- Change only the product's declared outputs.
- Update authoritative documentation in the same change when behavior changes.
- Do not add credentials, personal absolute paths, or sensitive logs.

Directory-specific rules in `.github/workflows/AGENTS.md` and `scripts/AGENTS.md`
apply in addition to these repository-wide expectations.

## Validate

Run the smallest complete set of checks that can disprove the change. At minimum:

- Parse changed workflow YAML and changed JSON/GeoJSON.
- Validate CSV headers and representative rows.
- Confirm every changed/generated path is declared.
- Check required keys/properties, live non-null counts, bounds, units, and time.
- Exercise upstream empty, partial, malformed, and timeout behavior when relevant.
- Confirm failure retains last-known-good output.
- Compare runtime, record count, and file size with a recent healthy build.
- Check manifest entries and compatibility views against their referenced files.
- Review consumer impact using the crosswalk.
- Check documentation links and repo-relative paths.
- Scan the diff for credentials, private source, and personal local paths.

Use [BUILD.md](BUILD.md) for local commands and
[docs/WORKFLOW_AND_PRODUCT_STANDARDS.md](docs/WORKFLOW_AND_PRODUCT_STANDARDS.md)
for current and recommended checks.

If a check cannot be run, state that explicitly with the reason and residual risk.
Do not call a change validated solely because the builder exited successfully.

## Pull-request evidence

Describe:

- The problem and user/operational impact.
- Current behavior, proposed behavior, and why they differ.
- Exact files and public paths affected.
- Commands/checks run and summarized results.
- Generated artifacts included or intentionally excluded.
- Consumer compatibility and documentation changes.
- Failure, last-known-good, and rollback behavior.
- Schedule/runtime/size impact.
- Known gaps and follow-up ownership.

Screenshots are useful for visible map changes but do not replace structural
feed validation.

## Contract changes

For a path, schema, unit, or meaning change:

1. Add backward-compatible producer output where possible.
2. Update the product catalog and consumer contract.
3. Validate the private consumer behavior without copying it into this repository.
4. Deploy consumer support before removing an old contract.
5. Observe official publication and freshness.
6. Remove compatibility output only after an explicit deprecation decision.

When producer and consumer facts conflict, do not silently choose one. Record the
conflict and follow the resolution procedure in
[docs/BRIM_CONSUMER_CONTRACT.md](docs/BRIM_CONSUMER_CONTRACT.md).

## Review and completion

A change is ready only when its scope is clean, validation evidence is available,
documentation agrees with implementation, and rollback is feasible. Maintainer
approval remains required for merge and official publication.

David, the repository owner and maintainer, retains the explicit decision to
merge a pull request, dispatch or publish official production products, and
approve an official rollback. Codex or another agent may prepare changes,
commits, pushes, and pull requests only when authorized. An agent must not merge,
dispatch production, publish, or roll back official state without David's
explicit approval.

Do not bundle a generated-data refresh into a documentation-only change. Do not
commit or push on someone else's behalf unless explicitly asked.
