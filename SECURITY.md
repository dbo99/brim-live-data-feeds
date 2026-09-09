# Security

This repository publishes public live-data artifacts. Security review therefore
covers both conventional credential safety and data-integrity controls that can
change what public consumers receive.

To report a suspected vulnerability or exposed credential, contact the repository
owner privately through an established channel. Do not open a public issue
containing exploitable details, credential material, private source, or sensitive
logs. This repository does not currently publish a dedicated security contact
address.

## Public-repository assumptions

Treat every tracked file, pull-request diff, Actions log, uploaded artifact, commit
message, and Pages response as public. Do not add:

- Credentials, cookies, private keys, access values, authorization headers, or
  secret-bearing request captures.
- Personal local paths, usernames, hostnames, or workstation metadata.
- Private consumer source or internal-only architecture inventories.
- Restricted upstream payloads or data whose publication rights are unclear.
- Logs or fixtures that embed sensitive query parameters or response headers.

Use placeholder names that cannot be mistaken for live values. Prefer describing
the required mechanism rather than showing a credential-shaped example.

## Observed workflow permissions

All fifteen production writers use the per-job GitHub Actions `GITHUB_TOKEN`:
`prepare-candidate` has `contents: read`, and only `publish-to-main` has
`contents: write`. The publisher validates candidate integrity, product semantics
and exact owned paths before an ordinary non-force push to `main`. Path limits
are enforced by repository code; `contents: write` itself is not path-scoped.

The wind watchdog has `contents: read` and `actions: write` to dispatch
ASOS/AWOS, GFS, HRRR and NBM wind writers on the selected ref. It does not commit
feed data. Sandbox and preview workflows have read-only repository permission
and upload QA artifacts. No tracked workflow runs on pull requests.

These are current operational facts, not a grant to broaden permissions.
New workflows should start with read-only access and add only the minimum scope
required. Any write-capable workflow must have a documented path allowlist,
reference-safe publication, bounded timeout, and non-force push behavior.

## Credential handling

The groundwater writer and candidate preview pass `API_USGS_PAT` to their build
steps. The streamflow preparation job also passes it to its build step, although
`scripts/build_usgs_streamflow_latest_ca.R` does not read it. Removing that unused
injection remains a separate least-privilege correction; read-only repository
permission does not eliminate exposure of an explicitly supplied secret.

The secret name is safe to document; its value is not. Builders and workflows
must not print it, interpolate it into artifact names, write it to `docs/`, retain
it in debug output, or expose a request header containing it.

For all credentials:

- Store values in the hosting platform's secret facility, not in Git.
- Scope them to the least privilege and shortest practical lifetime.
- Pass them only to the step that requires them.
- Avoid command tracing around secret-bearing commands.
- Sanitize HTTP diagnostics before logs or retained artifacts.
- Rotate immediately after suspected exposure and invalidate the previous value.

Public upstream services that work anonymously should remain anonymous; do not add
a credential merely to avoid handling rate limits correctly.

## Data-integrity threat model

The public files function as an API. Relevant failure and abuse cases include:

- A compromised or over-privileged workflow changing files outside its product.
- An upstream response that is syntactically valid but malicious, empty, partial,
  unexpectedly large, or structurally changed.
- Path traversal or unsafe file naming derived from upstream/model metadata.
- Unbounded downloads, decompression, parsing, or retries exhausting runner
  resources.
- Formula-like CSV content being opened by an operator in a spreadsheet tool.
- Branch confusion causing test artifacts to reach `main`.
- Dependency or GitHub Action compromise.
- A force push or history rewrite obscuring the publication record.

Mitigations include strict path construction, size/time bounds, schema and numeric
checks, allowlisted output staging, least-privilege permissions, the shared
publication queue, immutable action references where practical, maintained
dependencies, and product-specific last-known-good retention.

## Workflow and dependency review

For every workflow or dependency change:

1. Review the full action/source reference and publisher.
2. Check requested permissions and data passed into the action.
3. Confirm the current major/runtime is supported.
4. Preserve timeouts, concurrency, artifact retention, and path allowlists.
5. Inspect transitive system/R dependencies where the risk warrants it.
6. Verify that untrusted pull-request code cannot run with write credentials.
7. Record any accepted version-pinning tradeoff.

The current workflows use tagged action majors rather than immutable commit
digests. Pinning to reviewed commits would reduce tag-movement risk but increases
maintenance burden; adopting it should be a deliberate repository-wide policy.

## Logs, artifacts, and test data

Retained QA artifacts are public/repository-visible to people with appropriate
Actions access and must be treated as potentially discoverable. Include only the
minimum data needed to diagnose a build. Do not upload entire temporary
directories, environment dumps, HTTP header captures, or credential-bearing
configuration.

Test fixtures should be public, minimal, attributed where needed, and reviewed for
license/privacy constraints. Synthetic data should be visibly synthetic and must
not resemble a real access value.

## Repository access and branch controls

Public visibility permits reading, forking and proposing pull requests; it does
not grant upstream write access. Human access is distinct from Actions tokens,
Apps and deploy keys. David, the repository owner and maintainer, retains merge,
manual production-dispatch/rerun and official rollback decisions. Authorized
agents may prepare a branch and PR; that authorization does not transfer these
decisions. Scheduled writers and the scheduled watchdog operate under their
existing workflow policy.

**Verified GitHub state, 2026-09-09 UTC:** the repository is public and the only
direct human collaborator returned by the access API is its owner. The active
`main-history-safety` branch ruleset matches only `refs/heads/main`, contains
`deletion` and `non_fast_forward` rules, and has no bypass actors. Classic
branch protection is absent; required review and status checks are not enabled
by this ruleset. Pages uses `main:/docs`. This limited check does not establish
an exhaustive inventory of tokens, keys or integrations.

**Required procedure:** retain maintainer review and the
[publication procedures](docs/PUBLISHING_AND_OPERATIONS.md). Do not enable a
required-PR, required-check, signature, deployment or general update restriction
without a separately reviewed nonproduction test of the exact human and Actions
identities. Scheduled data writers intentionally push ordinary commits directly
to `main`; a rule that blocks them would interrupt the feeds. The current history
ruleset leaves those ordinary updates available and does not need bypass actors.

Settings are external to Git. A documentation commit cannot activate or roll
back access, rulesets, Actions permissions, secrets, Apps, keys, hooks or Pages.
Recommended access reviews should cover those surfaces and pending invitations
at least annually and after an integration change, permission failure or
unexpected push. Record sanitized findings privately and obtain David's approval
before changing access or settings.

## Security review checklist

- No credential material, private source, or personal absolute path in the diff.
- Workflow permissions are minimal and explicit.
- Publisher code rejects changes outside declared product paths; token scope is
  reviewed separately.
- No untrusted value is used as an unchecked path, command, or Git reference.
- Downloads, retries, parsing, and output sizes are bounded.
- Empty/partial/malformed upstream data fails safely.
- Logs and retained artifacts are sanitized.
- Consumer-visible fields and files have compatibility review.
- Rollback and last-known-good behavior are understood.
- Dependencies/actions are maintained and reviewed.

## Incident response

If a credential or sensitive value may have been exposed:

1. Stop further publication if continuing could expand the exposure.
2. Revoke or rotate the value through its owning service.
3. Restrict/remove public artifacts through the platform's supported process.
4. Preserve sanitized evidence and identify every exposure surface, including Git
   history, logs, artifacts, caches, and forks.
5. Repair the workflow or builder before restoring it.
6. Document impact and follow-up controls without reproducing the value.

If feed integrity rather than secrecy is affected, use the data incident and
rollback procedures in
[docs/PUBLISHING_AND_OPERATIONS.md](docs/PUBLISHING_AND_OPERATIONS.md).
Removing a value from the latest commit alone does not remove it from Git history
or external caches; history remediation requires owner and platform coordination.

If an unexpected human or integration appears able to write, preserve sanitized
access and push evidence and report it privately to David. Do not remove access
during a read-only audit. David determines any suspension, revocation, rotation
or publication pause; verify the affected history and expected automation before
resuming publication.
