# SM1 common prepared view, version brim-soil-moisture-1

This is a review adapter contract, not a production publication or scientific
policy change. There is no joint generation/atomic publication across networks.

## Delivery and identity

Each provider supplies `{schema,source,generation,note,sensor_defaults,stations,
counts}`. Source is `scan`, `dendra`, or `snotel`; labels are SCAN, dendra, SNOTEL.
Counts explicitly distinguish stations, catalog sensors, and available imported
moisture histories. The accepted Dendra temperature companion is additional.

A station has key `source + ':' + id`, original id/triplet, name, documented
aliases, provider, public coordinates `[longitude,latitude]` or null, coordinate
provenance, source URL/generation, primary_sensor and sensors. Network `SNTL`
is retained. Nearby coordinates and equal numeric IDs never merge networks.
The dated dendra catalog includes122 stations/434 moisture entries;97 unresolved
native scales have null percent units. No inventory-only value is fabricated.

Expand a sensor as `{...sensor_defaults,...sensor}`. Repeated fields may be
factored into defaults; IDs, depth and observation/history remain explicit.
Native identity includes original stream or element/depth/ordinal and source
station. Preserve native depth/unit/sign, canonical exact millimetres, orientation,
primary rank/policy, native/target units and conversion evidence, public access,
source-era information, precision where known and null reasons. 8in=203.2mm;
20cm=200mm. No nearest-depth or best-value selection.

Observation fields distinguish source value/date/time/statistic/timezone,
eligibility/QC/null reason, genuine sample/coverage support, last retrieval,
build time and generation. Date-only source values do not acquire a made-up
observed hour or sampling support. All eligible common VWC values are finite
percent values0..100 with valid dates. Raw returned source values and flags stay
in their saved response even when not eligible for common display.

Capabilities are Boolean and evidence-based per sensor: latest_vwc,
recent_change, source_context, common_reference, companion. Imported Dendra
moisture has authoritative7/14/30 change summaries; only20cm has the accepted
same-depth temperature companion. SCAN/SNOTEL common change and reference are
false. SCAN historical context remains available through its original plot code.
Unsupported common mode is neutral/unavailable, never another quantity.

History windows separate display, retained archive and reference. SCAN retains
original current-WY, monthly and prior-WY/context products and their original
POA/support fields in its station bundle. SNOTEL has only the bounded90-day
sample; dendra has accepted imported water years. No new percentiles/ribbons,
retention policy, or calendar/statistical equivalence is established here.

Indices are capped256KiB/source,1500 stations,10000 sensors. SCAN/SNOTEL bundle
paths are permitted basenames, with SHA256/exact bytes (max6MB). Compact bundles are cached by provider generation+station+hash under both an
eight-entry and 12,000,000 serialized UTF-8 byte limit. This accounting is not a
JavaScript heap claim; decoded tables live only for the selected popup. On integrity failure,
selection reports unavailable. Dendra uses the existing256KiB index/2000-file
reader and exact daily hash/schema contracts. Shared popup mode cannot silently
advance that reader while common map data still use an older generation; reload
the page for a coordinated static reload. These limits remain scale work.

## Source differences

SCAN preserves each published `sms_pct`, native sensor ID list/count and date.
The original depth table age is labeled Snapshot age; shared header/filter age
uses the current clock. The collector averages duplicate instruments at one depth; the result is a
published composite. Its generated soilDB noon label is not an observation
instant. NRCS Daily temporal reduction has not been established as the accepted
Dendra arithmetic mean. Age uses the unshifted source date and an explicit
UTC−08 date-label reference; source/display timezone details are retained.

Dendra preserves accepted fixed-PST completed-day arithmetic means, thresholds,
flags, exact streams and current comparison summaries. No calculation source
is modified. Metadata-only units are not inferred from nearby/other sensors.

SNOTEL preserves negative native inches as below-ground depth, ordinal1, pct
units, precision1, begin/end/derived metadata, QC/QA/original values and data
clock−08. The actual DAILY reduction remains unresolved. Seven hourly days
are separately reduced by R diagnostic policy: range0..100, QC=V, unshifted
source dates00:00..23:00, support/expected24 retained, incomplete date excluded.
That diagnostic is never substituted into Daily. Six observed next-midnights
match preceding daily values; this supports a follow-up interval-ending
interpretation study, not a claim that Daily is an arithmetic mean.

## Predicates and transitions

Enabled-source membership is an immediate outer predicate. Search AND station
BLM criteria AND sensor value/age criteria define both result list and map.
Search uses Local Reference's normalization plus Unicode accent decomposition;
exact ID/name/alias first, prefix then all tokens contained, stable name/key
order. Only genuine aliases are indexed. No geocoder or viewport restriction.

Displayed sensor is the explicit site primary, or a fixed-rank exact-depth
sensor (ID tie-break). Default numeric filters test that displayed sensor.
Advanced Any needs at least one selected depth whose single fixed sensor passes
both value AND age. Every needs a qualifying sensor at every requested depth;
missing/invalid/stale depths fail. Empty requested depths are invalid. Admission
via another depth uses one escaped explanation in both list and pointer hover,
including matching value, source date and current age from the applied result.
Pending criteria cannot enter this explanation; map still displays its own sensor.
Thresholds use genuine unrounded values; display rounding does not alter them.
Unknowns fail active numeric/geography predicates, while unrestricted inventory
context may remain. Invalid/nonfinite/min>max drafts preserve applied criteria.

Auto starts on; typing debounces200ms, valid discrete edits apply immediately.
Auto off stages criteria until Apply/Enter. Turning Auto off cancels its timer;
turning it on validates/applies pending criteria once. Source switches always
act immediately on existing applied criteria and preserve pending edits. Mode,
window and label-unit controls do not apply unrelated pending criteria.

Reset filters clears criteria/applies immediately while preserving networks,
Auto and Auto-zoom preferences. It cancels old popup work. Auto-zoom defaults
off and only fits on user-applied transactions; source arrival/passive aging
never fits. Explicit Zoom to results uses every coordinate-bearing result,
not the20-row rendered page. Click/Enter uses padded fit/maxZoom13 then actual
source popup. Null location reports unavailable; empty fit never selects a site.

Close hides only the card; reopen retains state. All sources off leaves a
recoverable card. Clear Ops/All cancels activation/detail timers and requests,
removes shared controls/markers/listeners, restores documented defaults and is
idempotent. Clear Local does not own Ops state. Epoch/source guards prevent
late data resurrection. DOM source text is escaped; public links require HTTPS.
Search/filter/list operations make zero observation/history requests.

## BLM metadata and compatibility

All real adapters use unknown BLM joins because permitted public geometry was
absent. Fields retain on_blm_ca, mi/ft distance, BLM-California managed scope,
coordinate and geometry hashes, EPSG3310 method, calculation date/null reason.
The pure R helper extracts USGS48/49: make valid, project/union, intersects,
distance, on-land zero, miles rounded3 places, feet whole. Cache invalidates on
coordinate/key or boundary hash change, not daily values. Synthetic parity
never populates real stations. No new boundary download or protected input.

Opt-in is additive. Shared mode suppresses only SCAN's two old cards and Dendra's
old map/card owner, reusing SCAN plot/table functions and Dendra's accepted popup.
Non-opt-in factories retain their existing behavior. No publisher/collector
ownership, existing public URLs, machine IDs or workflow is migrated. Public
dendra branding is lowercase. Production flags/URLs remain off/empty; promotion
and D2B each require a subsequent explicit review/authorization.

## SM1R1 transport and preparation checks

SCAN `sm1-scan-popup-2` factors each of the five original tables into its exact
ordered `columns` and string-cell `rows`. Python and the browser decoder enforce
the fixed version-2 header schemas, table inventory, row width and station identity.
The decoder restores the original `sm1-scan-popup-1` feature/table objects for
unchanged SCAN plot functions; it does not round or convert CSV cells. Ruby drops
from 7,308,293 to 2,809,802 bytes; all 28 stations fit the unchanged 6,000,000-byte
wire bound. Decoded JSON is capped at 12,000,000 bytes, 1,000,000 cells total,
50,000 rows per table and 64 columns. SNOTEL remains its original schema.

The offline adapter requires `--consumer-check PATH_TO_BRIM_QA/preflight_soil_moisture_products.js`
and a new output directory. It stages the saved products, validates every advertised
index and companion/history against Python bounds/hash/schema, then runs the actual
JavaScript controller/transport and unchanged Dendra reader checks. Only both passes
install the output with `preflight.json`. Failure removes the staging directory and
leaves no completed target; an existing output is never replaced. Repair the saved
input/configuration and retry into a fresh directory; do not weaken limits or fetch
new data to conceal failure. This is local review preparation, without publication.

Common circle markers use BRIM's existing `pane_ops` (560), above context polygons
and below tooltip/popup panes. The focused build uses `pt_add_panes()` like the
actual map. Clear Local/context reactivation does not change marker ownership.
