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

Production writers use the per-job GitHub Actions `GITHUB_TOKEN` and require
`contents: write` to commit declared outputs. The wind watchdog uses
`contents: read` to inspect wind manifests/state and `actions: write` to dispatch
ASOS/AWOS, GFS, HRRR, and NBM writers on the selected ref. It does not itself
commit or publish repository data. Sandbox and preview workflows use read-only
repository access and upload QA artifacts. No current workflow runs on a pull
request.

These are current operational facts, not a grant to broaden permissions.
New workflows should start with read-only access and add only the minimum scope
required. Any write-capable workflow must have a documented path allowlist,
reference-safe publication, bounded timeout, and non-force push behavior.

## Credential handling

The groundwater writer and candidate-preview workflow can provide the secret
named `API_USGS_PAT` to their builders. The streamflow workflow also currently
injects that secret, although its builder does not read it; removing that
unnecessary exposure is a separate least-privilege correction. The name is safe
to document; its value is not. Builders and workflows must not print it,
interpolate it into artifact names, write it to `docs/`, retain it in debug
output, or expose a request header containing it.

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
checks, allowlisted output staging, least-privilege permissions, reference-scoped
concurrency, immutable action references where practical, maintained dependencies,
and last-known-good retention.

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

## Branch and publication controls

The repository access policy is that the maintainer is the only human account
with upstream write or administration access. Public users may read, fork and
open pull requests, but should not be direct collaborators. The expected
repository-content automation is the GitHub Actions `GITHUB_TOKEN` used by the
declared production writers; the GitHub Pages bot deploys the configured Pages
source but does not author feed commits.

At the documentation baseline, `main` had no observed branch protection or
ruleset. Settings are external to Git, so a branch or pull request cannot make a
ruleset active. The minimum compatible target control is a branch ruleset that
matches only `main`, restricts deletion and blocks force pushes, and has no
bypass actors. It must leave ordinary non-force updates unrestricted so the
scheduled writers, maintainer pushes and reviewed pull-request merges continue
to work.

Do not require pull requests, status checks, signed commits, deployments, or a
general update restriction on `main` until an isolated test proves the exact
maintainer and GitHub Actions bypass behavior. Review direct collaborators,
pending invitations, deploy keys, installed Apps, webhooks, repository
credentials, Actions defaults and ruleset bypass actors at least annually and
after any access/integration change or unexpected push.

Until compatible platform controls are enabled, contributors must treat
maintainer review and the publication checklist as mandatory process controls.

## Security review checklist

- No credential material, private source, or personal absolute path in the diff.
- Workflow permissions are minimal and explicit.
- Write jobs can modify only declared product outputs.
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

If an unexpected human, App, key, token or integration appears able to write:

1. Stop the audit or settings change and report the actor and evidence privately
   to the maintainer; do not remove access during a read-only review.
2. Preserve the relevant access record, push/ref, workflow run and settings state
   without reproducing credentials.
3. Have the maintainer revoke or suspend the exact access path, rotate any
   affected credential, and review `main`, workflows, rulesets, secrets and Pages.
4. Restore publication only after the expected writer inventory and branch
   history are verified.
