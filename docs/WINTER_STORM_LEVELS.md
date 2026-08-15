# Winter Storm Levels source decision and publisher notes

This focused document records the official-source research, prototype evidence,
and operational caveats for the `winter_storm_levels` product. The authoritative
path/schema summary remains in [PRODUCTS.md](PRODUCTS.md), the public interface in
[BRIM_CONSUMER_CONTRACT.md](BRIM_CONSUMER_CONTRACT.md), and publication authority
in [PUBLISHING_AND_OPERATIONS.md](PUBLISHING_AND_OPERATIONS.md).

## Selected source

The production feed uses the deterministic `SNOWLVL` record in the NOAA/NWS
National Blend of Models (NBM) CONUS Core GRIB2 product. NBM version 5 provides a
2.5-km Lambert Conformal CONUS grid. The current MDL definition is the elevation
where wet-bulb temperature reaches 0.5 degrees C. Output values are metres above
mean sea level; the publisher converts them to feet MSL using the exact factor
`3.28083989501312`.

The publisher retrieves exact byte ranges from the public NOAA NBM Open Data
bucket after identifying one unambiguous deterministic record in each `.idx`
inventory. NOMADS is recorded as the official alternate retrieval base. A
percentile record, wrong level, wrong cycle, missing following byte offset,
non-metre unit, wrong valid time, insufficient finite coverage, or implausible
range fails validation.

This choice adds AWS hosting as a delivery dependency, but not as a weather-data
authority: the bucket is the NOAA NBM Open Data distribution and provides stable
archive-style cycle paths plus HTTP range access. NOMADS is the direct NCEP
alternative. It is documented but is not an automatic fallback until its range
responses and outage semantics receive the same tests.

Authoritative source references:

- NCEP NBM product inventory: <https://www.nco.ncep.noaa.gov/pmb/products/blend/>
- MDL NBM weather elements: <https://vlab.noaa.gov/web/mdl/nbm-weather-elements>
- NBM version history: <https://vlab.noaa.gov/web/mdl/nbm-versions>
- Western Region snow-level method: <https://www.weather.gov/media/wrh/online_publications/TAs/TA1901.pdf>
- NOAA NBM Open Data bucket: <https://noaa-nbm-grib2-pds.s3.amazonaws.com/>
- NCEP NOMADS NBM archive: <https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/>
- NCEP GFS inventory: <https://www.nco.ncep.noaa.gov/pmb/products/gfs/>
- NCEP HRRR inventory: <https://www.nco.ncep.noaa.gov/pmb/products/hrrr/>
- CNRFC freezing-level guidance: <https://www.cnrfc.noaa.gov/fzlvl_guidance.php>
- CNRFC observed basin freezing-level summary: <https://www.cnrfc.noaa.gov/awipsProducts/RNOFSTFZL.php>
- NOAA digital-media/public-domain conditions: <https://sos.noaa.gov/copyright/>

The APRFC snow-level page describes an NBM wet-bulb 0-degree-C level. That
wording conflicts with the current MDL element definition and the documented
NWS Western Region standard. The publisher therefore uses the field without
renaming it and documents the controlling 0.5-degree-C definition.

## Source and reusable-vector audit

| Candidate | Official field and cadence | Directly answers snow level? | Reusable browser vector? | Decision |
|---|---|---:|---:|---|
| NBM Core | Deterministic `SNOWLVL`, 00/06/12/18 UTC cycles selected, 2.5-km CONUS | Yes | No official contour service found | Canonical source; derive compact contours |
| GFS 0.25 degree | `HGT` at `0C isotherm`, four primary cycles daily | No; freezing level | No official contour service found | Comparison/fallback research only |
| HRRR pressure/surface | `HGT` at `0C isotherm`, hourly | No; freezing level | No official contour service found | Comparison/fallback research only |
| CNRFC freezing-level guidance | Observed and forecast freezing-level grids at six-hour steps | No; freezing level | Current interactive implementation loads PNG overlays; KML capability is generic, not a demonstrated stable contour feed | Visual/reference comparison only |
| NDFD snow level | Official graphical/grid element | Yes | Raster/grid and XML access, not a demonstrated stable contour feed | Official alternative for later comparison |

GFS and HRRR also distinguish an ordinary and a highest-tropospheric freezing
level. That exposes the multiple-crossing problem directly: a future fallback
must name the selected crossing and must never be presented as interchangeable
with NBM snow level. CNRFC basin summaries and PNGs are useful independent
checks, but they do not satisfy the geographic/vector/time contract needed here.
The checked-in CNRFC numeric fixture captures the official page served on August
4, 2026 (issued 8:05 AM PDT August 3) for three named basins. Tests confirm its
thousands-of-feet conversion and configured display range, but deliberately do
not assert equality: it is HRRR freezing level, not NBM snow level.

### Audited endpoints and metadata

- **NBM:** anonymous HTTPS GRIB2 and text inventory at
  `https://noaa-nbm-grib2-pds.s3.amazonaws.com/blend.YYYYMMDD/CC/core/blend.tCCz.core.fXXX.co.grib2[.idx]`;
  the direct NCEP alternative is the documented NOMADS `blend/prod/` tree. The
  inventory identifies `SNOWLVL`, `0 m above mean sea level`, source cycle,
  forecast lead, and byte offset. The decoded GRIB supplies valid time, Lambert
  CRS, 2.5-km grid geometry, metre units, and `9999` NoData. NCEP advertises
  hourly CONUS Core cycles and f001-f264; the candidate deliberately selects the
  four primary 00/06/12/18 cycles and 11 leads through f072. Dated object keys
  provide short-term historical inspection; neither path requires credentials.
- **GFS:** anonymous HTTPS GRIB2/inventory at
  `https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.YYYYMMDD/CC/atmos/gfs.tCCz.pgrb2.0p25.fFFF[.idx]`,
  with `HGT` at `0C isotherm` in geopotential metres and a distinct `HGT` at the
  highest tropospheric freezing level. Its 0.25-degree global grid, four primary
  daily cycles, and long forecast range are operationally attractive, but this
  field is freezing level rather than the NBM snow-level definition.
- **HRRR:** anonymous HTTPS GRIB2/inventory at
  `https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod/hrrr.YYYYMMDD/conus/hrrr.tCCz.wrfsfcfFF.grib2[.idx]`,
  with the same ordinary-versus-highest-freezing-level distinction and
  `HGT` in geopotential metres. Hourly, approximately 3-km CONUS guidance is a
  useful higher-frequency comparison, but the crossing choice and semantic
  mismatch prevent silent fallback.
- **CNRFC:** `https://www.cnrfc.noaa.gov/fzlvl_guidance.php` currently drives
  its display with `/data/png/6hrFcst_FzLevel_<step>.png` images. The numeric
  basin reference at
  `https://www.cnrfc.noaa.gov/awipsProducts/RNOFSTFZL.php` reports HRRR freezing
  level in thousands of feet. It is reference material, not a reusable current
  vector contract. No endpoint requires authentication.

On August 4, 2026, availability checks against several NBM Core cycles found the
following approximate delay from source cycle to a complete sampled inventory:

| Cycle sample | Observed delay | Candidate use |
|---|---:|---|
| 00Z | 65 minutes | Selected primary cycle |
| 01Z | 92 minutes | Audited, not selected |
| 02Z-06Z | 38-43 minutes | 06Z selected; hourly cycles audited |

The proposed 71-minute schedule offset covers the selected 00Z/06Z observations
while complete-cycle fallback handles a slower issue. Dated URLs prevent an old
cycle from being mistaken for a newer one; cycle identity is also checked inside
every inventory and decoded message. A `200` maintenance body, wrong range,
truncated body, stale cycle, or missing lead fails closed.

The data are NOAA-produced U.S. government information. NOAA describes its
government-server material as public-domain unless annotated otherwise and asks
users to acknowledge NOAA without implying endorsement. The manifest retains
NOAA/NWS/NCEP/MDL attribution and exact source URLs. AWS hosting is a delivery
dependency only; it does not change the data authority or attribution.

## Product geometry and domain

Line contours were selected instead of filled bands. They preserve the ability
to see an optional precipitation overlay, label exact elevations, and keep the
browser payload small. The output contract is:

- WGS84 longitude/latitude GeoJSON `LineString` features;
- 0 through 20,000 feet MSL at 1,000-foot intervals, emitting only levels that
  intersect the display domain;
- display bounds west -130, east -112, south 30, north 44.5;
- source crop buffer west -132, east -110, south 28.5, north 46;
- raw full-resolution isoband contours from every native cell in the source
  crop, followed by exact display-bound clipping and the minimal v1 treatment
  described below;
- stable feature ordering, coordinate rounding to five decimal places, and no
  volatile per-feature retrieval/build timestamps.

These values live in
[`data/input/winter_storm_levels_config.csv`](../data/input/winter_storm_levels_config.csv),
not in browser code. The domain includes California, southern Oregon, Nevada,
northwest Arizona, the lower Colorado corridor, and enough adjacent Pacific
water for landfall context.

### Selected v1 cartographic treatment

The producer uses `isoband::isolines()` directly on the unsmoothed native NBM
source crop. Configured public levels remain exact feet MSL values, converted to
metres only for contour extraction and assigned back from the configured level
list rather than parsed from floating-point output names.

The active path is deliberately small:

1. reverse Terra's north-to-south matrix rows together with the native y vector
   so isoband receives increasing y without resampling;
2. deterministically convert isolines to native-CRS `LineString` components;
3. clip to the existing product domain and transform to WGS84;
4. remove consecutive duplicates, round to five decimals, remove any duplicates
   created by rounding, and canonicalize component direction/start position;
5. omit and diagnose only a component with fewer than two distinct serialized
   positions; and
6. deterministically order features, recompute lengths/bounds, and validate the
   unchanged target and manifest contracts.

The active path does not apply the legacy 750-m simplification, B filter, S2,
segmentization, Chaikin smoothing, fallback repair, polygonization, node/split
repair, or topology-based serialization disposition. Mathematical non-simplicity
is diagnostic rather than a rejection condition when the LineString is finite,
structurally valid, in bounds, and renderable. The legacy
`simplify_tolerance_m` manifest member remains serialized for version-1 contract
compatibility but does not control the raw-isoband path.

### Retired geometry development chain

Early prototypes used Terra contour extraction with a `maxcells` limit, then a
750-m simplification, fixed B filtering, S2 segmentization/Chaikin smoothing,
and narrow custom fallback and serialization-repair experiments. The contour
engine bake-off selected full-native-grid raw isoband instead. After official
publication and two-cycle/22-target retention proved that path, the unreachable
Terra/B/S2/custom-repair implementation, tests, fixture, and obsolete negative
diagnostics were removed. This paragraph is historical context, not an
alternate or rollback algorithm.

## Interpretation caveats

- Snow level is a modeled rain/snow transition estimate, not a direct surface
  precipitation-type observation and not a guarantee of snow accumulation.
- A transition layer can span several hundred feet. The 1,000-foot contour
  interval is presentation resolution, not model precision.
- NBM supplies the derived height field. The publisher does not substitute a
  surface-temperature threshold, terrain-mask the field, or derive a new
  crossing. If terrain lies above the published level, users should interpret
  the transition as at/near the surface rather than as a below-ground contour.
- GRIB NoData `9999` becomes missing. Finite zero and small negative source
  values are not silently converted to missing; only configured nonnegative
  contour levels are published. Absence of a contour level means the surface
  does not cross that configured value in the domain, not that the value is zero.
- The selected NBM field avoids the multiple-0-degree-C-crossing ambiguity in
  raw temperature profiles. Any GFS/HRRR fallback would require a separate
  contract and consumer label.

## Rolling set, freshness, and failure policy

The manifest is the only selection authority. A current cycle publishes
forecast hours 1, 6, 12, 18, 24, 30, 36, 42, 48, 60, and 72. A target is active
from three hours before through three hours after its valid time. Consumers
prefer the newest cycle for duplicate valid times.

The accepted bundle status is `current` through nine hours of cycle age,
`delayed_but_usable` through 15 hours, `stale_last_known_good` through 24 hours,
and `expired` afterward or whenever no target is active. Consumers must recompute
these transitions from source cycle/target validity; retrieval, publication, Git,
or Pages time does not extend meteorological validity.

The publisher requires all 11 horizons from one cycle. It retries only transport
errors and HTTP 408, 429, 500, 502, 503, and 504, for at most three attempts,
using bounded exponential backoff with jitter and a 30-second cap on numeric
`Retry-After`.
HTTP 403/404 is `source_unavailable`; other terminal HTTP errors are
`fetch_failed`. An absent `SNOWLVL` field is `variable_missing`, a GRIB reader
failure is `decode_failed`, field/schema/units/time/domain/content disagreement
is `validation_failed`, and a complete-set replacement failure is
`publication_failed`. A failed attempt records runner diagnostics separately
and does not rewrite the accepted manifest or target set. New targets are
promoted first and the validated manifest is renamed last; injected pre-manifest
failure tests prove the previous accepted manifest remains byte-identical.

Bootstrap requires one complete `current` 11-target cycle with an active target.
After bootstrap, a genuinely newer cycle strictly validates the prior canonical
manifest and all referenced targets before retaining the prior root cycle. The
result is exactly two complete cycles and 22 targets. Malformed, unsafe,
missing, checksum-invalid, or uncopyable prior retention state fails without
replacing accepted bytes. A third-cycle rotation retains the previous root plus
the new cycle and removes the oldest cycle only after manifest promotion.

Re-fetching an unchanged cycle is a semantic no-op: retrieval time alone cannot
create a data commit. Target filenames include the first 12
hexadecimal characters of their SHA-256, preventing a same-cycle source revision
from overwriting bytes named by an older manifest. Unreferenced same-cycle
revisions are retained until that cycle leaves the two-cycle window, bounding
cleanup without creating a cached-manifest 404 race.

Version 1 always serializes `publication_time_utc` as JSON null. The builder
cannot know the later Git commit, push, or Pages deployment time; release
evidence records those events outside the manifest.

## Prototype evidence and browser QA

The August 4, 2026 06Z live dry run retrieved 11 exact GRIB ranges of about
1.02-1.19 MB each. Every decoded 2,345 by 1,597 grid had 100% finite coverage.
The 11 contour targets were about 46-106 KB each (about 0.9 MB total plus a
14-KB manifest), with 17-68 features per target. The final content-addressed
implementation completed fetch, decode, contour, serialization, and validation
in 8.13 seconds on the local test machine. An immediate repeat completed in 7.91
seconds and correctly reported `semantic_change: false`. These are
measurements, not contractual limits; CI drift should be reviewed against them.

The completed August 12, 2026 consumer study then exercised the exact preserved
producer commit against a fresh 12Z bundle: 11 targets, 832,363 target bytes,
423 contours and 16,395 vertices, followed by successful standalone consumer
validation and Chromium rendering. That evidence remains nonproduction.

The August 13, 2026 implementation-gate dry run found a complete 18Z inventory
and retrieved/processed horizons through f060, where a contour component
collapsed at five-decimal serialization and correctly failed validation before
promotion. The implementation now removes coordinate-collapsed/zero-length
components and covers that case synthetically; the gate authorized exactly one
live run, so no second live attempt was made. No canonical product was written.

One normal build makes 11 inventory requests and 11 range requests and downloads
roughly 12 MB of source messages. At four runs daily that is about 44 target
messages and 48 MB of GRIB transfer before retries, plus roughly 0.9 MB of new
vector payload per advancing cycle. This is why the candidate does not fetch the
full multi-field GRIB files or run every hour.

[`qa/winter_storm_levels/index.html`](../qa/winter_storm_levels/index.html) is a
dependency-free standalone QA page. It loads a manifest by URL or an extracted
workflow artifact, selects source/cycle/valid time, recomputes freshness,
displays stale/expired/error states, labels contours, measures browser render
cost, and can draw an optional comparison manifest and GeoJSON overlay. It is
not private BRIM code and is not a canonical feed path.

The expired manifest/GeoJSON fixture exercises stale-state display. The CNRFC
freezing-level fixture provides an official numeric reference case without
claiming field equivalence. Automated QA also covers missing local targets and
checksum failures; the live browser pass loaded all 11 horizons, selected
different valid times, rendered an expired fixture warning, and showed a clear
source-unavailable state for a missing manifest.

## Publication state

The workflow is intentionally manual-only for the first release. Dispatch
defaults to a runner-temporary dry run; a feature branch cannot publish. An
explicit `publish: true` is honored only on `main`. The proposed later schedule
is 01:11, 07:11, 13:11, and 19:11 UTC, after the corresponding 00/06/12/18 NBM
cycles and separated from existing writer minutes. On August 4 the reviewed 06Z
core fields appeared about 43 minutes after cycle time and 00Z about 65 minutes
after cycle time; a 71-minute offset leaves observed margin, while cycle fallback
and fail-and-retain handle a slower issue. Schedule activation, the
first canonical `docs/data/winter-storm-levels/` publication, merge, and any
official rollback remain maintainer decisions.
