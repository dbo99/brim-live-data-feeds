# Soil SMS hosted qualification

This source-only integration combines the reviewed SCAN request-accounting and
sensor-count repair with the public-selected SNOTEL current-water-year snapshot
source from PR #49. It does not activate either producer or publish observations.
The PR #49 source is included here; review merge ordering together so neither is
merged as an independent, conflicting release.

The three SCAN runtime files and SNOTEL science, reducer, native transport,
publisher and year-end bodies retain their accepted bytes. The public legacy
SNOTEL compact pool remains 32; the NEW snapshot route is separate. SCAN counts
original distinct sensor IDs before making its display string: readings 20/40
remain mean 30 and now report count 2.

## Manual synthetic QA

The existing `qa-snotel-sms-saved-replay.yml` registration has a branch-specific,
manual-only qualification route. It requires the exact reviewed source SHA,
checks out that event SHA without persisted credentials, refuses reruns, and
uses one Ubuntu 24.04 job with read-only repository permission and a 60-minute
total timeout. No matrix, production dispatch, publication step, write token,
OIDC, environment secret, self-hosted runner or live fallback is used.

The existing hardened action supplies R 4.6.1. The qualification selects
soilDB 2.9.2, httr 1.4.9 and R curl 8.1.0. Setup reuses the runner's signed APT
indexes and supported native libraries; it fails if those indexes/prerequisites
are unavailable rather than silently adding an unmetered refresh. Explicit APT
and R package bodies share a 4 GiB counter. Only official allowed endpoints are
used; dependency redirects are refused. Required Depends/Imports/LinkingTo dependencies are resolved from one
bounded CRAN index. Exact package versions, URLs and body hashes are locked in
the setup receipt before qualification; compatible platform packages may be
reused read-only. Suggests and development repositories are excluded. GitHub
VM/action and the existing hardened runtime delivery are separately managed,
not falsely included in the explicit package-body counter.

After setup, tests run without elevated authority in a new Linux network
namespace with loopback only and no external route. Token/secret variables are
removed. A reserved documentation address checks native egress denial; actual
R curl calls prove allowed loopback operation. No real provider is probed.
One serial server reserves complete finite synthetic response bodies plus
request bodies, at most 256 physical requests / 64 MiB, each fixture at most 8 MiB.
Counters describe decoded/application bodies, not TLS/wire volume.

SCAN exercises the actual installed request constructor and formatter, accepted
native connection backend, POST fields, request reservation, EOF/oversize/gzip,
truncation/framing/timeout, redirects/refusal/Retry-After, both fatal catch layers,
exact station scope and sensor counts. Complete synthetic builder cases validate
all five canonical outputs, a transient retry and exact repeat. Failure cases
retain the admitted five-file predecessor, including content and ctime. Cache
IDs authenticate the installed formatter, while names, coordinates and values
are explicitly fabricated; no real roster or capture is uploaded. The dated
Mac real-response parity evidence remains separate and is not copied here.

The NEW SNOTEL snapshot tests require CANDIDATE_READY with 32 completed saved
attempts / 98 synthetic positions, trusted predecessor, two-file successor,
unsupported/UNKNOWN refusal, fresh-process supported reconstruction, exact
repeat, native DAILY/END percent/null/flag semantics and water-year rollover.
The old loopback-forbidden network assertion is excluded because this namespace
intentionally permits loopback; independent external-denial/native-loopback
checks replace that platform assumption. The annual stress suite is not rerun.
The older saved replay remains separately labeled additional coverage; DISABLED
or legacy replay alone is never acceptance. Existing synthetic Git seams are
not represented as actual public predecessor authority.

Tests use temporary output roots outside tracked products. Public artifacts are
an explicit selection of synthetic summaries, source/runtime bindings, bounded
diagnostics and supervision receipts, capped at 64 MiB with 14-day review retention.
They exclude package archives, complete scratch trees, real captures/catalogs,
credentials and production state. Cleanup steps and always-uploads may not
complete after abrupt runner loss. Sampled memory has a 3 GiB preventive trigger
and 4 GiB ceiling when runner capacity permits; sampling is not an instantaneous
hard-cap proof.

## Future SCAN entry remains closed

`build-scan-soil-moisture-latest.yml` retains its schedule expression, five-path
allowlist, last-good validator, main-only publisher and shared non-cancelling
publication queue. It is not dispatched or activated by this integration.

Before any provider check, it requires `SCAN_LIVE_APPROVAL_JSON` plus five named
configuration values: `SCAN_HTTP_MAX_REQUESTS`, `SCAN_HTTP_RESPONSE_BYTES`,
`SCAN_HTTP_TOTAL_BYTES`, `SCAN_HTTP_CAMPAIGN_SECONDS`, and
`SCAN_FETCH_TIMEOUT_SEC`. No real values are installed here. The approval must
explicitly identify the exact source SHA, one workflow run number, first attempt,
finite matching budgets and a current UTC validity interval. The actual run ID
and attempt are bound in receipts. The check repeats after setup; the R entry
also verifies the pinned package versions and available interval margin. A new
run number or rerun cannot reuse that approval silently.

The request account is invocation-local, not a durable restart journal. Runtime
failure receipts retain known/UNKNOWN spending where execution allows. A new
live approval, reviewed dependency configuration and durable failure/accounting
custody are still required for production; this work does not authorize recovery
by resetting an interrupted account. The existing mutable production dependency
installer is guarded by the exact runtime-version check; production provisioning
must be commissioned before activation.

The ten-good/one-missing synthetic fixture retains the existing strict publisher
rejection. Partial refresh is a separate product/consumer decision. All 128 held
current/history joins remain held; absent history is not zero. Broad historical
re-auditing and SWE repair are outside this integration.

## Remaining production decisions

| Provider | Review evidence and remaining decision |
|---|---|
| SCAN | Review source integration and actual hosted receipts. Separately approve real budgets, production dependency lock/custody, acquisition, observation publication and activation. Preserve the existing five-output contract and held history. |
| SNOTEL SMS | Review PR49 relationship and NEW snapshot proof. Real 32/98 roster/native-field disclosure, consumer status/rollover decoding, durable predecessor/accounting custody, acquisition/publication and schedule remain separate. Preserve accepted source and provisional annual holdings. |
| Dendra | Accepted source32B and its separately approved attended cycle remain unchanged. Production still needs approved durable private custody and supported path binding/import/rebinding; copying a database or review ZIP is insufficient recovery. |

GitHub remains the preferred production destination. Eventual commissioning must
prove update, successor retention and a fresh restore/read/repeat with the Mac
unavailable and without duplicate spending. Synthetic QA alone does not prove
private real-state durability, real-provider availability or application support.
