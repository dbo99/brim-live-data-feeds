# Soil SMS hosted qualification

PR #52 qualified the SCAN request-accounting and sensor-count repair with the
selected SNOTEL snapshot source from PR #49. This is a record of that synthetic
qualification and its replay boundary. Current deployed controls are in
[SCAN operations](PUBLISHING_AND_OPERATIONS.md#scan-finite-hosted-commissioning-and-inactive-private-mode)
and [SNOTEL finite successor operation](SOIL_MOISTURE_OPERATIONS.md#finite-successor-operation).

At qualification, the three SCAN runtime files and SNOTEL science, reducer,
native transport, publisher and year-end bodies retained their accepted bytes.
The legacy SNOTEL compact pool had 32 entries. The snapshot used a separate route. SCAN counts
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

<a id="future-scan-entry-remains-closed"></a>

## Historical SCAN entry at PR #52

At PR #52 the workflow retained its five-path allowlist, last-good validator,
main-only publisher and shared queue. The qualification did not activate it.
PR #57 subsequently commissioned finite SCAN, and PR #68 added renewable dates.

The guarded entry required `SCAN_LIVE_APPROVAL_JSON` and five configuration
values before provider access: `SCAN_HTTP_MAX_REQUESTS`, `SCAN_HTTP_RESPONSE_BYTES`,
`SCAN_HTTP_TOTAL_BYTES`, `SCAN_HTTP_CAMPAIGN_SECONDS`, and
`SCAN_FETCH_TIMEOUT_SEC`. The qualification installed no real values. An approval
had to identify the exact source SHA, one workflow run number, first attempt,
finite matching budgets and a current UTC validity interval. The actual run ID
and attempt were bound in receipts. After setup, the R entry also checked the
pinned package versions and remaining time. The approval excluded a new run
number or rerun.

The request account was invocation-local. Recovery relies on retained receipts
and authenticated spending evidence. An interrupted or uncertain account cannot
be reset to resume acquisition. See the current operations guide for the native
runtime, pre-spend checks and finite manual or daily route.

The ten-good/one-missing synthetic fixture retains the existing strict publisher
rejection. Partial refresh is a separate product/consumer decision. All 128 held
current/history joins remain held; absent history is not zero. Broad historical
re-auditing and SWE repair are outside this integration.

<a id="remaining-production-decisions"></a>

## Later production checkpoints

PR #71 established SNOTEL successors from the published two-file predecessor.
The retained replay and synthetic fixtures remain available to test their
original boundaries. The separate Dendra `source32B` checkpoint retained its
own custody and recovery requirements. They confer no authority for a provider call, another
feed's private custody or acceptance by a particular application. Use the
[product catalog](PRODUCTS.md) for dated delivery evidence.
