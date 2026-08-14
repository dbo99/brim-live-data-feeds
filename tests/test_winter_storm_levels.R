#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(digest)
  library(httr2)
  library(jsonlite)
  library(sf)
  library(terra)
})
source(file.path("scripts", "winter_storm_levels_helpers.R"))

checks <- 0L
check <- function(value, message) {
  checks <<- checks + 1L
  if (!isTRUE(value)) stop("Check failed: ", message, call. = FALSE)
}
check_error <- function(expression, class = NULL, pattern = NULL) {
  checks <<- checks + 1L
  error <- tryCatch({ force(expression); NULL }, error = function(error) error)
  if (is.null(error)) stop("Expected an error but none was raised.", call. = FALSE)
  if (!is.null(class) && !class %in% base::class(error)) {
    stop("Expected error class ", class, "; received ", paste(base::class(error), collapse = ", "), call. = FALSE)
  }
  if (!is.null(pattern) && !grepl(pattern, conditionMessage(error), fixed = TRUE)) {
    stop("Error did not contain expected text: ", pattern, call. = FALSE)
  }
  invisible(error)
}

config <- wsl_read_config(file.path("data", "input", "winter_storm_levels_config.csv"))
check(identical(config$product_id, "winter_storm_levels"), "config product id")
check(identical(config$source_id, "nbm_snow_level"), "canonical source is the derived NBM snow-level field")
check(identical(config$forecast_lead_hours, c(1L, 6L, 12L, 18L, 24L, 30L, 36L, 42L, 48L, 60L, 72L)), "configured leads")
check(config$source_west < config$west && config$source_north > config$north, "source crop buffers display domain")
check(abs(1000 * WSL_METERS_TO_FEET - 3280.83989501312) < 1e-9, "exact metre-to-foot conversion")

now <- as.POSIXct("2026-08-04 08:00:00", tz = "UTC")
cycles <- wsl_candidate_cycles(now, config)
check(identical(wsl_iso_utc(cycles[1L]), "2026-08-04T06:00:00Z"), "cycle floor")
check(length(cycles) == 7L, "bounded cycle lookback")
urls <- wsl_nbm_urls(cycles[1L], 6L)
check(grepl("blend.20260804/06/core/blend.t06z.core.f006.co.grib2$", urls$grib), "NBM URL naming")

index_text <- paste(
  "10:100:d=2026080406:SNOWLVL:surface:6 hour fcst:",
  "11:200:d=2026080406:SNOWLVL:0 m above mean sea level:6 hour fcst:percentileValue=50:",
  "12:300:d=2026080406:SNOWLVL:0 m above mean sea level:6 hour fcst:",
  "13:500:d=2026080406:TMP:2 m above ground:6 hour fcst:",
  sep = "\n"
)
record <- wsl_parse_index(index_text, cycles[1L], 6L)
check(record$start_byte == 300 && record$end_byte == 499, "exact deterministic byte range")
check(identical(record$valid_time_utc, "2026-08-04T12:00:00Z"), "valid time derivation")
check(!grepl("percentileValue", record$inventory_line, fixed = TRUE), "percentile excluded")
check_error(wsl_parse_index(gsub("d=2026080406", "d=2026080400", index_text, fixed = TRUE),
                            cycles[1L], 6L), "validation_failed", "found 0")
check_error(wsl_parse_index(gsub("SNOWLVL", "HGT", index_text, fixed = TRUE), cycles[1L], 6L),
            "variable_missing", "does not contain SNOWLVL")
check_error(wsl_parse_index(gsub("0 m above mean sea level", "surface", index_text, fixed = TRUE), cycles[1L], 6L), "validation_failed", "found 0")
check_error(wsl_parse_index(sub("13:500", "12:450:d=2026080406:SNOWLVL:0 m above mean sea level:6 hour fcst:\n13:500", index_text, fixed = TRUE), cycles[1L], 6L), "validation_failed", "found 2")

check(identical(wsl_classify_http_status(404), "source_unavailable"), "404 classification")
check(identical(wsl_classify_http_status(429), "fetch_failed_transient"), "429 retry classification")
check(identical(wsl_classify_http_status(400), "fetch_failed_permanent"), "400 non-retry classification")
responses <- list(response(503), response(429), response(200, body = charToRaw("ok")))
request_count <- 0L
performer <- function(request) { request_count <<- request_count + 1L; responses[[request_count]] }
response_ok <- wsl_http_request(request("https://example.invalid"), 3L, performer, function(seconds) NULL)
check(resp_status(response_ok) == 200L && request_count == 3L, "bounded transient retry")
request_count <- 0L
check_error(wsl_http_request(request("https://example.invalid"), 3L,
                             function(request) { request_count <<- request_count + 1L; response(400) },
                             function(seconds) NULL), "fetch_failed_permanent")
check(request_count == 1L, "permanent HTTP failure is not retried")
retry_delays <- numeric()
retry_responses <- list(
  response(429, headers = list("retry-after" = "7")),
  response(200, body = charToRaw("ok"))
)
retry_count <- 0L
invisible(wsl_http_request(
  request("https://example.invalid"), 3L,
  function(request) { retry_count <<- retry_count + 1L; retry_responses[[retry_count]] },
  function(seconds) retry_delays <<- c(retry_delays, seconds),
  function(max_seconds) 0
))
check(identical(retry_delays, 7), "bounded numeric Retry-After is respected")
check(identical(wsl_retry_delay(response(503, headers = list("retry-after" = "120")), 1L,
                                function(max_seconds) 0), 30),
      "Retry-After is capped at 30 seconds")
range_body <- wsl_validate_range_response(
  response(206, headers = list("content-range" = "bytes 10-19/100"), body = as.raw(1:10)),
  10, 19
)
check(identical(range_body, as.raw(1:10)), "exact partial-content response accepted")
check_error(wsl_validate_range_response(
  response(200, headers = list("content-type" = "text/html"), body = charToRaw("maintenance")),
  10, 19
), "validation_failed", "HTTP 200")
check_error(wsl_validate_range_response(
  response(206, headers = list("content-range" = "bytes 10-19/100"), body = as.raw(1:5)),
  10, 19
), "validation_failed", "length mismatch")
check_error(wsl_validate_range_response(
  response(206, headers = list("content-range" = "bytes 11-20/100"), body = as.raw(1:10)),
  10, 19
), "validation_failed", "Unexpected Content-Range")

small_config <- config
small_config$forecast_lead_hours <- c(1L, 6L)
fake_index <- function(url, config) {
  if (grepl("blend.20260804/06/", url, fixed = TRUE)) wsl_stop("source_unavailable", "not ready")
  lead <- as.integer(sub(".*f([0-9]{3})\\.co.*", "\\1", url))
  paste0(
    "1:100:d=2026080400:SNOWLVL:0 m above mean sea level:", lead, " hour fcst:\n",
    "2:200:d=2026080400:TMP:2 m above ground:", lead, " hour fcst:\n"
  )
}
discovery <- wsl_discover_cycle(now, small_config, fake_index)
check(identical(wsl_iso_utc(discovery$cycle_time), "2026-08-04T00:00:00Z"), "cycle fallback")
check(length(discovery$attempts) == 2L && !discovery$attempts[[1L]]$complete, "discovery diagnostics")
check_error(wsl_discover_cycle(now, small_config, function(url, config) wsl_stop("source_unavailable", "missing")), "source_unavailable")
fetch_discovery_error <- check_error(
  wsl_discover_cycle(now, small_config, function(url, config) wsl_stop("fetch_failed_transient", "timeout")),
  "fetch_failed_transient"
)
check(length(fetch_discovery_error$attempts) == length(wsl_candidate_cycles(now, small_config)), "failed discovery retains attempt diagnostics")
missing_lead_error <- check_error(
  wsl_discover_cycle(now, small_config, function(url, config) {
    lead <- as.integer(sub(".*f([0-9]{3})\\.co.*", "\\1", url))
    if (lead == 6L) wsl_stop("source_unavailable", "configured horizon missing")
    cycle_token <- sub(".*blend\\.([0-9]{8})/([0-9]{2})/.*", "\\1\\2", url)
    paste0(
      "1:100:d=", cycle_token, ":SNOWLVL:0 m above mean sea level:", lead, " hour fcst:\n",
      "2:200:d=", cycle_token, ":TMP:2 m above ground:", lead, " hour fcst:\n"
    )
  }),
  "source_unavailable"
)
check(all(!vapply(missing_lead_error$attempts, `[[`, logical(1), "complete")),
      "a missing forecast horizon rejects every incomplete cycle")
check_error(
  wsl_discover_cycle(now, small_config, function(url, config) wsl_stop("validation_failed", "wrong field")),
  "validation_failed"
)
check_error(
  wsl_discover_cycle(now, small_config, function(url, config) wsl_stop("variable_missing", "missing field")),
  "variable_missing"
)
check_error(wsl_decode_grib("malformed.grib2", function(path) stop("truncated message")),
            "decode_failed", "truncated message")

metadata_fixture_path <- file.path(
  "tests", "fixtures", "winter_storm_levels", "valid_time_divergence_metadata.json"
)
metadata_fixture <- jsonlite::fromJSON(metadata_fixture_path, simplifyVector = FALSE)
metadata_record <- metadata_fixture$record
metadata_record$lead_hours <- as.integer(metadata_record$lead_hours)
metadata_lines <- unname(unlist(metadata_fixture$gdal_describe_lines))
diagnosed_metadata <- wsl_parse_grib_metadata(metadata_lines)
metadata_diagnostic <- wsl_source_diagnostic(
  diagnosed_metadata, metadata_record, metadata_fixture$simulated_terra_time_utc,
  list(terra_version = "simulated-runner", gdal_version = "simulated-runner")
)
check(isTRUE(metadata_diagnostic$authoritative_metadata_accepted),
      "coherent authoritative GRIB metadata accepted")
check(identical(metadata_diagnostic$authoritative_valid_time_utc,
                metadata_fixture$expected_authoritative_valid_time_utc),
      "authoritative GRIB valid time controls normalized source time")
check(isTRUE(metadata_diagnostic$decoder_divergence) &&
      identical(metadata_diagnostic$terra_time_utc,
                metadata_fixture$simulated_terra_time_utc),
      "divergent terra convenience time is recorded without controlling authority")
check(isTRUE(wsl_require_source_diagnostic(metadata_diagnostic)),
      "coherent metadata passes the fail-closed authority gate")

metadata_clone <- function(value) unserialize(serialize(value, NULL))
diagnostic_for <- function(metadata = diagnosed_metadata, record = metadata_record,
                           terra_time = metadata_fixture$simulated_terra_time_utc) {
  wsl_source_diagnostic(
    metadata, record, terra_time,
    list(terra_version = "test", gdal_version = "test")
  )
}

bad_metadata <- metadata_clone(diagnosed_metadata)
bad_metadata$reference_time_epoch <- bad_metadata$reference_time_epoch - 6 * 3600
check_error(wsl_require_source_diagnostic(diagnostic_for(bad_metadata)),
            pattern = "GRIB reference time does not match")
bad_metadata <- metadata_clone(diagnosed_metadata)
bad_metadata$forecast_seconds <- 7200
check_error(wsl_require_source_diagnostic(diagnostic_for(bad_metadata)),
            pattern = "GRIB forecast seconds do not match")
bad_metadata <- metadata_clone(diagnosed_metadata)
bad_metadata$valid_time_epoch <- bad_metadata$valid_time_epoch + 3600
check_error(wsl_require_source_diagnostic(diagnostic_for(bad_metadata)),
            pattern = "GRIB valid time does not equal reference time plus forecast seconds")
bad_record <- metadata_clone(metadata_record)
bad_record$valid_time_utc <- "2026-08-14T14:00:00Z"
check_error(wsl_require_source_diagnostic(diagnostic_for(record = bad_record)),
            pattern = "GRIB valid time does not match the selected inventory record")
bad_metadata <- metadata_clone(diagnosed_metadata)
bad_metadata$element <- "TMP"
check_error(wsl_require_source_diagnostic(diagnostic_for(bad_metadata)),
            pattern = "GRIB element is not deterministic SNOWLVL")
bad_metadata <- metadata_clone(diagnosed_metadata)
bad_metadata$level <- "surface"
check_error(wsl_require_source_diagnostic(diagnostic_for(bad_metadata)),
            pattern = "GRIB level is not 0 m GPML")
bad_metadata <- metadata_clone(diagnosed_metadata)
bad_metadata$pdtn <- 8
check_error(wsl_require_source_diagnostic(diagnostic_for(bad_metadata)),
            pattern = "GRIB PDTN is not the expected instantaneous forecast type")
bad_metadata <- metadata_clone(diagnosed_metadata)
bad_metadata$valid_time_epoch <- NA_real_
check_error(wsl_require_source_diagnostic(diagnostic_for(bad_metadata)),
            pattern = "Authoritative GRIB metadata are missing or ambiguous")

successful_record <- metadata_clone(metadata_record)
successful_record$cycle_time_utc <- "2026-08-13T18:00:00Z"
successful_record$valid_time_utc <- "2026-08-13T19:00:00Z"
successful_record$inventory_line <- paste0(
  "131:134479767:d=2026081318:SNOWLVL:0 m above mean sea level:1 hour fcst:"
)
successful_metadata <- metadata_clone(diagnosed_metadata)
successful_metadata$reference_time_epoch <- as.numeric(wsl_as_utc(successful_record$cycle_time_utc))
successful_metadata$valid_time_epoch <- as.numeric(wsl_as_utc(successful_record$valid_time_utc))
successful_diagnostic <- diagnostic_for(
  successful_metadata, successful_record, successful_record$valid_time_utc
)
check(isTRUE(wsl_require_source_diagnostic(successful_diagnostic)),
      "previously successful August 13 timing semantics remain accepted")

check(identical(wsl_freshness_status(now - 8 * 3600, now, config), "current"), "current freshness")
check(identical(wsl_freshness_status(now - 10 * 3600, now, config), "delayed_but_usable"), "delayed freshness")
check(identical(wsl_freshness_status(now - 18 * 3600, now, config), "stale_last_known_good"), "last-known-good freshness")
check(identical(wsl_freshness_status(now - 25 * 3600, now, config), "expired"), "expired freshness")
check(identical(wsl_freshness_status(now, now, config, FALSE), "expired"), "no active target expires")
check(is.null(wsl_normalize_line(
  rbind(c(-120, 40), c(-119.9999999, 40.0000001)), config$coordinate_digits
)), "coordinate rounding removes a collapsed degenerate line")
zero_length_line <- st_sf(
  level_ft_msl = 1000,
  geometry = st_sfc(st_linestring(rbind(c(-120, 40), c(-120, 40))), crs = 4326)
)
check(as.numeric(st_length(st_transform(zero_length_line, 5070))) == 0,
      "zero-length source component is detectable before serialization")

synthetic_record <- list(
  cycle_time_utc = "2026-01-15T06:00:00Z",
  valid_time_utc = "2026-01-15T12:00:00Z",
  lead_hours = 6L,
  grib_url = "https://example.invalid/source.grib2",
  index_url = "https://example.invalid/source.grib2.idx",
  inventory_line = "1:0:d=2026011506:SNOWLVL:0 m above mean sea level:6 hour fcst:"
)

# Exact v1 B-filter and S2 cartographic treatment.
wsl_test_projected_coordinates <- function(offsets_m) {
  center <- st_coordinates(st_transform(
    st_sfc(st_point(c(-120, 37)), crs = 4326), WSL_CARTOGRAPHIC_CRS
  ))[1L, 1:2]
  projected <- sweep(offsets_m, 2L, center, "+")
  st_coordinates(st_transform(
    wsl_geometry_from_coordinates(projected, WSL_CARTOGRAPHIC_CRS), 4326
  ))[, 1:2, drop = FALSE]
}
wsl_test_cartographic_feature <- function(id, coordinates, level = 8000L) {
  list(
    type = "Feature",
    id = id,
    properties = list(
      product_id = config$product_id,
      source_id = config$source_id,
      parameter = "snow_level",
      definition = "height of the wet-bulb 0.5 degree C surface",
      level_ft_msl = as.integer(level),
      label = sprintf("%s ft MSL", format(level, big.mark = ",", scientific = FALSE)),
      unit = "ft_msl",
      cycle_time_utc = synthetic_record$cycle_time_utc,
      valid_time_utc = synthetic_record$valid_time_utc,
      lead_hours = synthetic_record$lead_hours,
      segment = 1L,
      length_m = 1
    ),
    geometry = list(
      type = "LineString",
      coordinates = unname(split(coordinates, row(coordinates)))
    )
  )
}

geometry_fixture_path <- file.path(
  "tests", "fixtures", "winter_storm_levels", "pre_s2_geometry_robustness.json"
)
geometry_fixture <- jsonlite::fromJSON(geometry_fixture_path, simplifyVector = FALSE)
wsl_test_fixture_coordinates <- function(value) {
  matrix(as.numeric(unlist(value)), ncol = 2L, byrow = TRUE)
}
study_grid_spacing_m <- as.numeric(geometry_fixture$grid_spacing_m)

fallback_original <- wsl_test_fixture_coordinates(
  geometry_fixture$simplification_fallback$pre_simplification_coordinates_wgs84
)
fallback_simplified <- wsl_test_fixture_coordinates(
  geometry_fixture$simplification_fallback$simplified_coordinates_wgs84
)
fallback_original_state <- wsl_component_geometry_state(fallback_original, TRUE)
fallback_simplified_state <- wsl_component_geometry_state(fallback_simplified, TRUE)
check(isTRUE(fallback_original_state$safe) && nrow(fallback_original) == 11L,
      "_001 pre-simplification fixture is an 11-vertex safe closed component")
check(!isTRUE(fallback_simplified_state$safe) &&
        "non_simple" %in% fallback_simplified_state$reasons &&
        nrow(fallback_simplified) == 5L,
      "_001 simplified fixture is non-simple in the operational CRS")
fallback_selection <- wsl_select_pre_s2_component(
  fallback_original, fallback_simplified, TRUE, study_grid_spacing_m, config, "fixture-_001"
)
check(identical(fallback_selection$action, "use_original") &&
        isTRUE(fallback_selection$simplification_fallback) &&
        identical(fallback_selection$coordinates, fallback_original),
      "_001 selects the preserved original without polygonization")
fallback_feature <- wsl_test_cartographic_feature(
  "fixture-_001", fallback_selection$coordinates, level = 10000L
)
fallback_profile <- wsl_component_profile(list(fallback_feature), study_grid_spacing_m, config)
check(!fallback_profile$remove[[1L]] && fallback_profile$vertices[[1L]] == 11L,
      "_001 preserved component passes B and is retained")
fallback_s2 <- wsl_smooth_component_s2(
  fallback_feature, study_grid_spacing_m, config$coordinate_digits
)
fallback_s2_state <- wsl_component_geometry_state(
  wsl_feature_coordinates(fallback_s2), TRUE
)
check(isTRUE(fallback_s2_state$safe) &&
        wsl_is_closed_coordinates(wsl_feature_coordinates(fallback_s2)),
      "_001 preserved component passes unchanged S2 and final geometry QA")

precision_original <- wsl_test_fixture_coordinates(
  geometry_fixture$precision_collapse$pre_simplification_coordinates_wgs84
)
precision_simplified <- wsl_test_fixture_coordinates(
  geometry_fixture$precision_collapse$simplified_coordinates_wgs84
)
precision_rounded <- wsl_test_fixture_coordinates(
  geometry_fixture$precision_collapse$rounded_coordinates_wgs84
)
precision_original_state <- wsl_component_geometry_state(precision_original, TRUE)
precision_simplified_state <- wsl_component_geometry_state(precision_simplified, TRUE)
precision_original_serialization <- wsl_canonical_serialization_state(
  precision_original, TRUE, config$coordinate_digits
)
precision_simplified_serialization <- wsl_canonical_serialization_state(
  precision_simplified, TRUE, config$coordinate_digits
)
check(isTRUE(precision_original_state$safe) && nrow(precision_original) == 7L &&
        isTRUE(precision_simplified_state$safe) && nrow(precision_simplified) == 5L,
      "_011 original and simplified fixtures are safe before canonical serialization")
check(all(precision_original_serialization$coordinates == precision_rounded) &&
        all(precision_simplified_serialization$coordinates == precision_rounded) &&
        precision_original_serialization$distinct_coordinates == 2L &&
        precision_simplified_serialization$distinct_coordinates == 2L,
      "_011 original and simplified fixtures both canonicalize to the same A-B-A retrace")
check(!isTRUE(precision_original_serialization$safe) &&
        !isTRUE(precision_simplified_serialization$safe) &&
        isTRUE(precision_original_serialization$structurally_unrepresentable_closed) &&
        isTRUE(precision_simplified_serialization$structurally_unrepresentable_closed) &&
        all(c("closure_changed", "insufficient_distinct_ring_coordinates", "non_simple") %in%
              precision_original_serialization$state$reasons),
      "_011 five-decimal quantization is a structural closed-ring collapse")
precision_profile <- wsl_component_profile_for_coordinates(
  precision_original, TRUE, study_grid_spacing_m, config, "fixture-_011"
)
check(!precision_profile$remove[[1L]] &&
        precision_profile$vertices[[1L]] == 7L &&
        precision_profile$enclosed_area_km2[[1L]] < study_grid_spacing_m ^ 2 / 1e6 &&
        precision_profile$max_projected_span_m[[1L]] > 2 * study_grid_spacing_m,
      "_011 is tiny-area but exceeds the existing B two-grid span limit")
precision_selection <- wsl_select_pre_s2_component(
  precision_original, precision_simplified, TRUE,
  study_grid_spacing_m, config, "fixture-_011"
)
check(identical(precision_selection$action, "remove_serialization_degenerate") &&
        isTRUE(precision_selection$serialization_degenerate) &&
        !isTRUE(precision_selection$b_profile$remove[[1L]]) &&
        precision_selection$b_profile$vertices[[1L]] == 7L &&
        is.null(precision_selection$coordinates),
      "_011 is accounted as serialization-degenerate after B retains the safe unrounded original")
isolated_precision_diagnostics <- wsl_pre_s2_disposition_diagnostics(
  list(precision_selection)
)
check(isolated_precision_diagnostics$serialization_degenerate_removed_count == 1L &&
        isolated_precision_diagnostics$precision_collapse_count == 1L &&
        isolated_precision_diagnostics$precision_collapse_b_removed_count == 0L &&
        isolated_precision_diagnostics$unrecoverable_geometry_count == 0L,
      "isolated _011 reports one serialization-degenerate removal and no B removal")

meaningful_narrow <- wsl_test_fixture_coordinates(
  geometry_fixture$meaningful_narrow_closed$coordinates_wgs84
)
meaningful_serialization <- wsl_canonical_serialization_state(
  meaningful_narrow, TRUE, config$coordinate_digits
)
meaningful_selection <- wsl_select_pre_s2_component(
  meaningful_narrow, meaningful_narrow, TRUE,
  study_grid_spacing_m, config, "fixture-meaningful-narrow"
)
check(isTRUE(wsl_component_geometry_state(meaningful_narrow, TRUE)$safe) &&
        isTRUE(meaningful_serialization$safe) &&
        identical(meaningful_selection$action, "use_simplified") &&
        !isTRUE(meaningful_selection$serialization_degenerate),
      "a meaningful narrow closed ring survives and cannot use serialization-degenerate removal")

serialization_fallback_original <- wsl_test_fixture_coordinates(
  geometry_fixture$simplified_collapse_original_survives$
    pre_simplification_coordinates_wgs84
)
serialization_fallback_simplified <- wsl_test_fixture_coordinates(
  geometry_fixture$simplified_collapse_original_survives$simplified_coordinates_wgs84
)
serialization_fallback_selection <- wsl_select_pre_s2_component(
  serialization_fallback_original, serialization_fallback_simplified, TRUE,
  study_grid_spacing_m, config, "fixture-serialization-fallback"
)
check(identical(serialization_fallback_selection$action, "use_original") &&
        isTRUE(serialization_fallback_selection$simplification_fallback) &&
        isTRUE(serialization_fallback_selection$precision_collapse) &&
        !isTRUE(serialization_fallback_selection$serialization_degenerate) &&
        identical(serialization_fallback_selection$coordinates,
                  serialization_fallback_original),
      "a simplified serialization collapse falls back to a safely serializable original")

rounded_topology_failure <- wsl_test_fixture_coordinates(
  geometry_fixture$rounded_topology_failure$coordinates_wgs84
)
rounded_topology_state <- wsl_canonical_serialization_state(
  rounded_topology_failure, TRUE, config$coordinate_digits
)
check(isTRUE(wsl_component_geometry_state(rounded_topology_failure, TRUE)$safe) &&
        !isTRUE(rounded_topology_state$safe) &&
        rounded_topology_state$distinct_coordinates >= 3L &&
        !isTRUE(rounded_topology_state$structurally_unrepresentable_closed),
      "rounded topology fixture retains meaningful vertices and is outside the narrow rule")
check_error(
  wsl_select_pre_s2_component(
    rounded_topology_failure, rounded_topology_failure, TRUE,
    study_grid_spacing_m, config, "fixture-rounded-topology"
  ),
  class = "validation_failed",
  pattern = "not an authorized closed-component degeneracy"
)

pre_round_non_simple <- wsl_test_fixture_coordinates(
  geometry_fixture$pre_round_non_simple$coordinates_wgs84
)
check(!isTRUE(wsl_component_geometry_state(pre_round_non_simple, TRUE)$safe) &&
        "non_simple" %in% wsl_component_geometry_state(pre_round_non_simple, TRUE)$reasons,
      "pre-round-invalid fixture exposes its non-simple topology")
check_error(
  wsl_select_pre_s2_component(
    pre_round_non_simple, pre_round_non_simple, TRUE,
    study_grid_spacing_m, config, "fixture-pre-round-invalid"
  ),
  class = "validation_failed",
  pattern = "no authorized safe disposition"
)

open_precision_collapse <- rbind(
  c(config$west, 37), c(config$west + 1e-7, 37 + 1e-7)
)
check_error(
  wsl_select_pre_s2_component(
    open_precision_collapse, open_precision_collapse, FALSE,
    study_grid_spacing_m, config, "fixture-open-precision-collapse"
  ),
  class = "validation_failed",
  pattern = "not an authorized closed-component degeneracy"
)

b_eligible_precision <- wsl_test_fixture_coordinates(
  geometry_fixture$b_eligible_precision_collapse$unrounded_coordinates_wgs84
)
b_eligible_selection <- wsl_select_pre_s2_component(
  b_eligible_precision, b_eligible_precision, TRUE,
  study_grid_spacing_m, config, "fixture-b-eligible-collapse"
)
check(identical(b_eligible_selection$action, "remove_precision_collapse") &&
        isTRUE(b_eligible_selection$precision_collapse) &&
        !isTRUE(b_eligible_selection$serialization_degenerate) &&
        isTRUE(b_eligible_selection$b_profile$remove[[1L]]),
      "a B-eligible precision collapse remains distinct from serialization-degenerate removal")

normal_closed_coordinates <- wsl_test_fixture_coordinates(
  geometry_fixture$normal_closed$coordinates_wgs84
)
normal_closed_selection <- wsl_select_pre_s2_component(
  normal_closed_coordinates, normal_closed_coordinates, TRUE,
  study_grid_spacing_m, config, "fixture-normal-closed"
)
check(identical(normal_closed_selection$action, "use_simplified") &&
        identical(normal_closed_selection$coordinates, normal_closed_coordinates),
      "normal safe closed geometry follows the existing simplified path byte-for-byte")

normal_open_coordinates <- wsl_test_fixture_coordinates(
  geometry_fixture$normal_open$coordinates_wgs84
)
normal_open_selection <- wsl_select_pre_s2_component(
  normal_open_coordinates, normal_open_coordinates, FALSE,
  study_grid_spacing_m, config, "fixture-normal-open"
)
check(identical(normal_open_selection$action, "use_simplified") &&
        identical(normal_open_selection$coordinates, normal_open_coordinates) &&
        all(normal_open_selection$coordinates[1L, ] == normal_open_coordinates[1L, ]) &&
        all(normal_open_selection$coordinates[nrow(normal_open_selection$coordinates), ] ==
              normal_open_coordinates[nrow(normal_open_coordinates), ]),
      "normal safe open geometry and endpoints follow the existing path unchanged")

tiny_closed <- wsl_test_cartographic_feature("tiny-closed", wsl_test_projected_coordinates(rbind(
  c(0, 0), c(1000, 0), c(1000, 1000), c(0, 1000), c(0, 0)
)))
large_area_closed <- wsl_test_cartographic_feature("large-area-closed", wsl_test_projected_coordinates(rbind(
  c(0, 0), c(3000, 0), c(3000, 3000), c(0, 3000), c(0, 0)
)))
wide_closed <- wsl_test_cartographic_feature("wide-closed", wsl_test_projected_coordinates(rbind(
  c(0, 0), c(6000, 0), c(6000, 500), c(0, 500), c(0, 0)
)))
tiny_interior_open <- wsl_test_cartographic_feature(
  "tiny-interior-open", wsl_test_projected_coordinates(rbind(c(0, 0), c(1000, 0)))
)
boundary_open <- wsl_test_cartographic_feature(
  "boundary-open", rbind(c(config$west, 35), c(config$west + 0.01, 35))
)
long_open <- wsl_test_cartographic_feature(
  "long-open", wsl_test_projected_coordinates(rbind(c(0, 0), c(6000, 0)))
)
cartographic_features <- list(
  tiny_closed, large_area_closed, wide_closed,
  tiny_interior_open, boundary_open, long_open
)

b_filter <- wsl_filter_components_b(cartographic_features, study_grid_spacing_m, config)
removed_ids <- b_filter$profile$feature_id[b_filter$profile$remove]
retained_ids <- vapply(b_filter$features, `[[`, character(1), "id")
check("tiny-closed" %in% removed_ids, "B removes a closed loop below both area and span thresholds")
check("large-area-closed" %in% retained_ids,
      "B retains a closed loop that fails the area criterion")
check("wide-closed" %in% retained_ids,
      "B retains a closed loop that fails the maximum-span criterion")
check("tiny-interior-open" %in% removed_ids, "B removes a short interior open fragment")
check("boundary-open" %in% retained_ids, "B retains a short boundary-adjacent open fragment")
check("long-open" %in% retained_ids, "B retains a normal long open contour")
check(b_filter$diagnostics$closed_removed == 1L && b_filter$diagnostics$open_removed == 1L,
      "B reports closed/open removals separately")

projected_open <- rbind(c(0, 0), c(1000, 0), c(1000, 1000))
chaikin_one <- wsl_chaikin(projected_open, FALSE, 1L)
chaikin_two <- wsl_chaikin(projected_open, FALSE, WSL_S2_CHAIKIN_PASSES)
check(WSL_S2_CHAIKIN_PASSES == 2L && nrow(chaikin_one) == 6L && nrow(chaikin_two) == 12L,
      "S2 performs exactly two Chaikin passes")
check(identical(chaikin_two, wsl_chaikin_once(chaikin_one, FALSE)),
      "two-pass Chaikin output equals two explicit corner-cutting applications")
check(all(chaikin_two[1L, ] == projected_open[1L, ]) &&
      all(chaikin_two[nrow(chaikin_two), ] == projected_open[nrow(projected_open), ]),
      "open Chaikin endpoints are preserved")
projected_closed <- rbind(c(0, 0), c(1000, 0), c(1000, 1000), c(0, 1000), c(0, 0))
chaikin_closed <- wsl_chaikin(projected_closed, TRUE, WSL_S2_CHAIKIN_PASSES)
check(nrow(chaikin_closed) == 17L && wsl_is_closed_coordinates(chaikin_closed),
      "closed Chaikin smoothing is cyclic and explicitly reclosed")
segmentized <- wsl_segmentize_coordinates(rbind(c(0, 0), c(12000, 0)),
                                          WSL_S2_DENSIFY_GRID_CELLS * study_grid_spacing_m)
check(max(sqrt(rowSums((segmentized[-1L, ] - segmentized[-nrow(segmentized), ]) ^ 2))) <=
        WSL_S2_DENSIFY_GRID_CELLS * study_grid_spacing_m,
      "S2 segmentization enforces the two-grid maximum spacing")
check(nrow(wsl_remove_consecutive_duplicates(rbind(
  c(0, 0), c(0, 0), c(1, 1), c(1, 1), c(2, 2)
))) == 3L, "post-rounding consecutive duplicate cleanup works")

cartographic_geojson <- list(
  type = "FeatureCollection", contract_version = config$contract_version,
  bbox = c(config$west, config$south, config$east, config$north),
  features = cartographic_features
)
s2_fixture <- wsl_apply_s2_cartography(cartographic_geojson, study_grid_spacing_m, config)
s2_features <- s2_fixture$geojson$features
s2_ids <- vapply(s2_features, `[[`, character(1), "id")
check(identical(s2_ids, retained_ids) && length(s2_features) == 4L,
      "S2 preserves component identities without cross-component merging")
check(all(vapply(s2_features, function(feature) {
  source_feature <- cartographic_features[[match(feature$id, vapply(
    cartographic_features, `[[`, character(1), "id"
  ))]]
  all(vapply(c("product_id", "source_id", "parameter", "definition", "level_ft_msl",
               "label", "unit", "cycle_time_utc", "valid_time_utc", "lead_hours", "segment"),
             function(name) identical(feature$properties[[name]], source_feature$properties[[name]]),
             logical(1)))
}, logical(1))), "S2 preserves source/model/time/lead/elevation properties")
check(all(vapply(s2_features, function(feature) {
  line <- wsl_geometry_from_coordinates(wsl_feature_coordinates(feature))
  isTRUE(st_is_valid(line)) && isTRUE(st_is_simple(line)) && !isTRUE(st_is_empty(line))
}, logical(1))), "S2 emits no invalid, non-simple, or empty geometry")
check(all(vapply(s2_features, function(feature) {
  coordinates <- wsl_feature_coordinates(feature)
  nrow(coordinates) >= 2L && !any(rowSums(abs(
    coordinates[-1L, , drop = FALSE] - coordinates[-nrow(coordinates), , drop = FALSE]
  )) == 0)
}, logical(1))), "S2 emits no adjacent duplicate coordinates")
check(all(vapply(Filter(function(feature) grepl("closed", feature$id, fixed = TRUE), s2_features),
                 function(feature) wsl_is_closed_coordinates(wsl_feature_coordinates(feature)), logical(1))),
      "S2 retained closed loops remain closed")
check(all(vapply(Filter(function(feature) feature$id %in% c("boundary-open", "long-open"), s2_features),
                 function(feature) {
                   source_feature <- cartographic_features[[match(feature$id, vapply(
                     cartographic_features, `[[`, character(1), "id"
                   ))]]
                   coordinates <- wsl_feature_coordinates(feature)
                   source_coordinates <- wsl_feature_coordinates(source_feature)
                   all(coordinates[1L, ] == round(source_coordinates[1L, ], config$coordinate_digits)) &&
                     all(coordinates[nrow(coordinates), ] ==
                           round(source_coordinates[nrow(source_coordinates), ], config$coordinate_digits))
                 }, logical(1))), "S2 final output preserves open endpoints exactly")
check(all(vapply(s2_features, function(feature) {
  actual <- as.numeric(st_length(st_transform(
    wsl_geometry_from_coordinates(wsl_feature_coordinates(feature)), WSL_CARTOGRAPHIC_CRS
  )))
  abs(actual - feature$properties$length_m) <= 0.051
}, logical(1))), "S2 recomputes geometry-derived component lengths")
s2_coordinates <- do.call(rbind, lapply(s2_features, wsl_feature_coordinates))
check(identical(as.numeric(s2_fixture$geojson$bbox), c(
  min(s2_coordinates[, 1L]), min(s2_coordinates[, 2L]),
  max(s2_coordinates[, 1L]), max(s2_coordinates[, 2L])
)), "S2 recomputes geometry-derived target bounds")

collapsed_feature <- wsl_test_cartographic_feature(
  "collapsed-retained", rbind(c(-120, 37), c(-119.999999, 37))
)
check_error(
  wsl_finalize_s2_feature(
    collapsed_feature,
    st_transform(wsl_geometry_from_coordinates(wsl_feature_coordinates(collapsed_feature)),
                 WSL_CARTOGRAPHIC_CRS),
    config$coordinate_digits
  ),
  class = "validation_failed", pattern = "collapsed retained component"
)

single_horizon_config <- config
single_horizon_config$forecast_lead_hours <- 6L
source_raster <- rast(nrows = 120, ncols = 160, xmin = config$source_west,
                      xmax = config$source_east, ymin = config$source_south,
                      ymax = config$source_north, crs = "EPSG:4326")
xy <- crds(source_raster, df = TRUE)
values(source_raster) <- 400 + (xy$x - config$source_west) * 120 + (xy$y - config$source_south) * 35
time(source_raster) <- wsl_as_utc(synthetic_record$valid_time_utc)
units(source_raster) <- "m"
wsl_test_metadata_for_record <- function(record) {
  list(
    band_count = 1L,
    reference_time_epoch = as.numeric(wsl_as_utc(record$cycle_time_utc)),
    forecast_seconds = as.numeric(record$lead_hours) * 3600,
    valid_time_epoch = as.numeric(wsl_as_utc(record$valid_time_utc)),
    element = "SNOWLVL",
    level = "0-GPML",
    pdtn = 0,
    parse_errors = character()
  )
}
synthetic_metadata <- wsl_test_metadata_for_record(synthetic_record)
synthetic_diagnostic <- wsl_source_diagnostic(
  synthetic_metadata, synthetic_record, terra::time(source_raster)[1L]
)
stats <- wsl_validate_raster(source_raster, synthetic_record, config, synthetic_diagnostic)
check(stats$finite_coverage == 1 && stats$min_m >= 0, "finite synthetic raster validation")

wrong_unit <- source_raster
units(wrong_unit) <- "K"
check_error(wsl_validate_raster(wrong_unit, synthetic_record, config, synthetic_diagnostic),
            pattern = "units must be metres")
units(source_raster) <- "m"
wrong_time <- source_raster
time(wrong_time) <- wsl_as_utc("2026-01-15T13:00:00Z")
wrong_time_diagnostic <- wsl_source_diagnostic(
  synthetic_metadata, synthetic_record, terra::time(wrong_time)[1L]
)
wrong_time_stats <- wsl_validate_raster(
  wrong_time, synthetic_record, config, wrong_time_diagnostic
)
check(isTRUE(wrong_time_stats$decoder_divergence) &&
      identical(wrong_time_stats$valid_time_utc, synthetic_record$valid_time_utc),
      "divergent terra time remains diagnostic after authoritative validation")
time(source_raster) <- wsl_as_utc(synthetic_record$valid_time_utc)
negative_raster <- source_raster
values(negative_raster)[1:10] <- -100
check(wsl_validate_raster(negative_raster, synthetic_record, config,
                          synthetic_diagnostic)$min_m == -100,
      "documented near-surface negative values accepted")

fixture_raster <- source_raster
time(fixture_raster) <- wsl_as_utc(metadata_fixture$simulated_terra_time_utc)
fixture_stats <- wsl_validate_raster(
  fixture_raster, metadata_record, config, metadata_diagnostic
)
check(identical(fixture_stats$valid_time_utc, "2026-08-14T13:00:00Z") &&
      isTRUE(fixture_stats$decoder_divergence),
      "diagnosed f001 fixture validates with authoritative 13Z product time")
values(source_raster) <- 400 + (xy$x - config$source_west) * 120 + (xy$y - config$source_south) * 35

contour <- wsl_make_contours(source_raster, synthetic_record, config)
check(contour$feature_count > 0L, "non-empty contour output")
check(all(contour$contour_levels %% config$contour_interval_ft == 0), "configured contour interval")
check(all(c(
  "source_components_before_disposition", "normal_simplified_count",
  "simplification_fallback_count", "precision_collapse_count",
  "precision_collapse_b_removed_count", "serialization_degenerate_removed_count",
  "unrecoverable_geometry_count", "ordinary_b_removed_count",
  "simplification_fallbacks", "precision_collapses",
  "precision_collapse_b_removals", "serialization_degenerate_removals"
) %in% names(contour$cartography)), "pre-S2 robustness diagnostics are complete")
check(contour$cartography$unrecoverable_geometry_count == 0L &&
        contour$cartography$precision_collapse_count ==
          length(contour$cartography$precision_collapses) &&
        contour$cartography$precision_collapse_b_removed_count ==
          length(contour$cartography$precision_collapse_b_removals) &&
        contour$cartography$serialization_degenerate_removed_count ==
          length(contour$cartography$serialization_degenerate_removals) &&
        contour$cartography$source_components_before_disposition ==
          contour$cartography$normal_simplified_count +
          contour$cartography$simplification_fallback_count +
          contour$cartography$precision_collapse_b_removed_count +
          contour$cartography$serialization_degenerate_removed_count,
      "successful contour output has no unrecoverable or unaccounted precision collapse")
check(contour$bbox[1L] >= config$west && contour$bbox[3L] <= config$east &&
      contour$bbox[2L] >= config$south && contour$bbox[4L] <= config$north, "exact output bounds")
properties <- contour$geojson$features[[1L]]$properties
check(identical(properties$cycle_time_utc, synthetic_record$cycle_time_utc) &&
      identical(properties$valid_time_utc, synthetic_record$valid_time_utc), "stable feature times")
check(!any(c("retrieval_time_utc", "publication_time_utc", "generated_at") %in% names(properties)), "no volatile per-feature timestamps")

wsl_test_clone <- function(value) unserialize(serialize(value, NULL))

wsl_test_cycle_entries <- function(root, cycle_time, config, contour, stats) {
  dir.create(root, recursive = TRUE, showWarnings = FALSE)
  lapply(config$forecast_lead_hours, function(lead) {
    record <- list(
      cycle_time_utc = wsl_iso_utc(cycle_time),
      valid_time_utc = wsl_iso_utc(wsl_as_utc(cycle_time) + lead * 3600),
      lead_hours = as.integer(lead),
      grib_url = sprintf("https://example.invalid/f%03d.grib2", lead),
      index_url = sprintf("https://example.invalid/f%03d.grib2.idx", lead),
      inventory_line = sprintf(
        "1:0:d=%s:SNOWLVL:0 m above mean sea level:%d hour fcst:",
        wsl_cycle_token(cycle_time), lead
      )
    )
    cycle_contour <- wsl_test_clone(contour)
    for (index in seq_along(cycle_contour$geojson$features)) {
      feature <- cycle_contour$geojson$features[[index]]
      feature$properties$cycle_time_utc <- record$cycle_time_utc
      feature$properties$valid_time_utc <- record$valid_time_utc
      feature$properties$lead_hours <- record$lead_hours
      feature$id <- sprintf(
        "%s_f%03d_%05d_%03d",
        wsl_cycle_token(cycle_time), lead,
        as.integer(feature$properties$level_ft_msl),
        as.integer(feature$properties$segment)
      )
      cycle_contour$geojson$features[[index]] <- feature
    }
    working <- tempfile("wsl-contract-target-", tmpdir = root, fileext = ".geojson")
    wsl_write_json(cycle_contour$geojson, working)
    relative <- wsl_target_relative_path(record, wsl_sha256(working))
    destination <- file.path(root, relative)
    dir.create(dirname(destination), recursive = TRUE, showWarnings = FALSE)
    check(file.rename(working, destination), "contract target finalized")
    wsl_target_entry(record, relative, stats, cycle_contour, destination, config)
  })
}

wsl_test_write_manifest <- function(root, cycle_time, entries, now, config) {
  manifest <- wsl_manifest(cycle_time, entries, now, config)
  path <- file.path(root, "winter_storm_levels_manifest.json")
  wsl_write_json(manifest, path)
  wsl_validate_manifest(path, root, config, now)
  manifest
}

wsl_test_copy_tree <- function(source, destination) {
  dir.create(destination, recursive = TRUE, showWarnings = FALSE)
  files <- list.files(source, recursive = TRUE, full.names = TRUE)
  relatives <- substring(files, nchar(source) + 2L)
  destinations <- file.path(destination, relatives)
  invisible(lapply(dirname(destinations), dir.create, recursive = TRUE, showWarnings = FALSE))
  copied <- file.copy(files, destinations, overwrite = TRUE, copy.mode = TRUE)
  check(length(copied) == length(files) && all(copied), "contract test tree copied")
  invisible(destination)
}

temp_root <- tempfile("winter-storm-level-tests-")
dir.create(temp_root, recursive = TRUE)
on.exit(unlink(temp_root, recursive = TRUE, force = TRUE), add = TRUE)
working_target_path <- file.path(temp_root, "working.geojson")
wsl_write_json(contour$geojson, working_target_path)
wsl_validate_geojson(working_target_path, synthetic_record, config)
invalid_output <- contour$geojson
invalid_output$features[[1L]]$properties$unit <- "m"
invalid_output_path <- file.path(temp_root, "invalid-output.geojson")
wsl_write_json(invalid_output, invalid_output_path)
check_error(wsl_validate_geojson(invalid_output_path, synthetic_record, config),
            pattern = "parameter/unit semantics")
duplicate_output <- contour$geojson
duplicate_coordinates <- wsl_feature_coordinates(duplicate_output$features[[1L]])
duplicate_coordinates <- rbind(duplicate_coordinates[1L, ], duplicate_coordinates)
duplicate_output$features[[1L]] <- wsl_coordinates_to_feature(
  duplicate_output$features[[1L]], duplicate_coordinates
)
duplicate_output_path <- file.path(temp_root, "duplicate-output.geojson")
wsl_write_json(duplicate_output, duplicate_output_path)
check_error(wsl_validate_geojson(duplicate_output_path, synthetic_record, config),
            pattern = "adjacent duplicates")
relative_path <- wsl_target_relative_path(synthetic_record, wsl_sha256(working_target_path))
target_path <- file.path(temp_root, relative_path)
dir.create(dirname(target_path), recursive = TRUE)
check(file.rename(working_target_path, target_path), "content-addressed test target rename")
first_bytes <- readBin(target_path, "raw", n = file.info(target_path)$size)
hashed_path <- wsl_target_relative_path(synthetic_record, wsl_sha256(target_path))
check(grepl("_[0-9a-f]{12}\\.geojson$", hashed_path), "content-addressed target path")
check_error(wsl_safe_target_path("../outside.geojson"), pattern = "Unsafe")
wsl_write_json(wsl_make_contours(source_raster, synthetic_record, config)$geojson, target_path)
second_bytes <- readBin(target_path, "raw", n = file.info(target_path)$size)
check(identical(first_bytes, second_bytes), "byte-deterministic contour output")

unsimplified_config <- config
unsimplified_config$simplify_tolerance_m <- 0
unsimplified_path <- file.path(temp_root, "unsimplified.geojson")
wsl_write_json(wsl_make_contours(source_raster, synthetic_record, unsimplified_config)$geojson, unsimplified_path)
simple_sf <- suppressWarnings(st_read(target_path, quiet = TRUE))
full_sf <- suppressWarnings(st_read(unsimplified_path, quiet = TRUE))
check(all(st_is_valid(simple_sf)), "simplified output geometries are valid")
check(nrow(simple_sf) >= 1L && nrow(simple_sf) < 5000L, "synthetic feature-count bounds")
check(file.info(target_path)$size > 100 && file.info(target_path)$size < 500000,
      "synthetic target-size bounds")
hausdorff <- as.numeric(st_distance(
  st_union(st_transform(full_sf, 5070)), st_union(st_transform(simple_sf, 5070)),
  which = "Hausdorff"
))
check(is.finite(hausdorff) && hausdorff <= config$simplify_tolerance_m + 25, "simplification deviation bound")

entry <- wsl_target_entry(synthetic_record, relative_path,
                          stats, contour, target_path, config)
manifest <- wsl_manifest(wsl_as_utc(synthetic_record$cycle_time_utc), list(entry),
                         wsl_as_utc("2026-01-15T10:00:00Z"), single_horizon_config)
manifest_path <- file.path(temp_root, "winter_storm_levels_manifest.json")
wsl_write_json(manifest, manifest_path)
wsl_validate_manifest(manifest_path, temp_root, single_horizon_config)
check(identical(manifest$status, "current"), "manifest accepted status")
check(identical(manifest$schema_version, "1.0.0") &&
      identical(manifest$diagnostics$expected_current_cycle_target_count, 1L) &&
      identical(manifest$diagnostics$actual_current_cycle_target_count, 1L),
      "manifest schema and expected-versus-actual diagnostics")
check(identical(manifest$targets[[1L]]$sha256, wsl_sha256(target_path)), "manifest checksum")
check(grepl("0.5 degrees C", manifest$source$definition, fixed = TRUE),
      "manifest preserves the official derived snow-level definition")
published_manifest <- manifest
published_manifest$publication_time_utc <- "2026-01-15T10:00:00Z"
published_manifest_path <- file.path(temp_root, "published_manifest.json")
wsl_write_json(published_manifest, published_manifest_path)
check_error(wsl_validate_manifest(published_manifest_path, temp_root, single_horizon_config),
            pattern = "publication_time_utc must be null")
duplicate_manifest <- manifest
duplicate_manifest$targets <- list(entry, entry)
duplicate_manifest$target_count <- 2L
duplicate_manifest$diagnostics$actual_current_cycle_target_count <- 2L
duplicate_manifest_path <- file.path(temp_root, "duplicate_manifest.json")
wsl_write_json(duplicate_manifest, duplicate_manifest_path)
check_error(wsl_validate_manifest(duplicate_manifest_path, temp_root, single_horizon_config),
            pattern = "duplicate cycle/valid-time")

tampered <- readLines(target_path, warn = FALSE)
writeLines(c(tampered, " "), target_path)
check_error(wsl_validate_manifest(manifest_path, temp_root, single_horizon_config), pattern = "checksum mismatch")
wsl_write_json(contour$geojson, target_path)

canonical_root <- tempfile("winter-storm-level-canonical-")
candidate_root <- tempfile("winter-storm-level-candidate-")
dir.create(canonical_root, recursive = TRUE)
dir.create(candidate_root, recursive = TRUE)
on.exit(unlink(c(canonical_root, candidate_root), recursive = TRUE, force = TRUE), add = TRUE)
old_record <- synthetic_record
old_record$cycle_time_utc <- "2026-01-15T00:00:00Z"
old_record$valid_time_utc <- "2026-01-15T06:00:00Z"
old_relative <- wsl_target_relative_path(old_record, wsl_sha256(target_path))
dir.create(dirname(file.path(canonical_root, old_relative)), recursive = TRUE)
invisible(file.copy(target_path, file.path(canonical_root, old_relative)))
old_entry <- entry
old_entry$cycle_time_utc <- old_record$cycle_time_utc
old_entry$valid_time_utc <- old_record$valid_time_utc
old_entry$path <- old_relative
old_entry$sha256 <- wsl_sha256(file.path(canonical_root, old_relative))
old_entry$bytes <- unname(file.info(file.path(canonical_root, old_relative))$size)
old_manifest <- wsl_manifest(wsl_as_utc(old_record$cycle_time_utc), list(old_entry),
                             wsl_as_utc("2026-01-15T02:00:00Z"), single_horizon_config)
wsl_write_json(old_manifest, file.path(canonical_root, "winter_storm_levels_manifest.json"))
old_manifest_bytes <- readBin(file.path(canonical_root, "winter_storm_levels_manifest.json"), "raw",
                              n = file.info(file.path(canonical_root, "winter_storm_levels_manifest.json"))$size)

dir.create(dirname(file.path(candidate_root, entry$path)), recursive = TRUE)
invisible(file.copy(target_path, file.path(candidate_root, entry$path)))
entry$sha256 <- wsl_sha256(file.path(candidate_root, entry$path))
entry$bytes <- unname(file.info(file.path(candidate_root, entry$path))$size)
wsl_write_json(manifest <- wsl_manifest(wsl_as_utc(synthetic_record$cycle_time_utc), list(entry),
                                        wsl_as_utc("2026-01-15T10:00:00Z"), single_horizon_config),
               file.path(candidate_root, "winter_storm_levels_manifest.json"))
incomplete_manifest <- manifest
incomplete_manifest$diagnostics$expected_current_cycle_target_count <- length(config$forecast_lead_hours)
wsl_write_json(incomplete_manifest, file.path(candidate_root, "winter_storm_levels_manifest.json"))
check_error(wsl_validate_manifest(file.path(candidate_root, "winter_storm_levels_manifest.json"),
                                  candidate_root, config), pattern = "forecast-hour set is incomplete")
check_error(wsl_promote_bundle(candidate_root, canonical_root, "winter_storm_levels_manifest.json",
                               config), pattern = "forecast-hour set is incomplete")
after_incomplete <- readBin(file.path(canonical_root, "winter_storm_levels_manifest.json"), "raw",
                            n = file.info(file.path(canonical_root, "winter_storm_levels_manifest.json"))$size)
check(identical(old_manifest_bytes, after_incomplete),
      "referenced but incomplete horizon set cannot replace accepted manifest")
wsl_write_json(manifest, file.path(candidate_root, "winter_storm_levels_manifest.json"))
partial_root <- tempfile("winter-storm-level-partial-")
dir.create(partial_root, recursive = TRUE)
on.exit(unlink(partial_root, recursive = TRUE, force = TRUE), add = TRUE)
invisible(file.copy(file.path(candidate_root, "winter_storm_levels_manifest.json"),
                    file.path(partial_root, "winter_storm_levels_manifest.json")))
check_error(wsl_validate_manifest(file.path(partial_root, "winter_storm_levels_manifest.json"),
                                  partial_root, single_horizon_config), pattern = "target is missing")
check_error(wsl_promote_bundle(partial_root, canonical_root, "winter_storm_levels_manifest.json",
                               single_horizon_config),
            pattern = "target is missing")
after_partial <- readBin(file.path(canonical_root, "winter_storm_levels_manifest.json"), "raw",
                         n = file.info(file.path(canonical_root, "winter_storm_levels_manifest.json"))$size)
check(identical(old_manifest_bytes, after_partial), "partial candidate cannot replace accepted manifest")
check_error(wsl_promote_bundle(candidate_root, canonical_root, "winter_storm_levels_manifest.json",
                               single_horizon_config, TRUE),
            class = "publication_failed", pattern = "Injected failure")
after_failure <- readBin(file.path(canonical_root, "winter_storm_levels_manifest.json"), "raw",
                         n = file.info(file.path(canonical_root, "winter_storm_levels_manifest.json"))$size)
check(identical(old_manifest_bytes, after_failure), "failed promotion preserves accepted manifest bytes")
check(!file.exists(file.path(canonical_root, entry$path)), "failed promotion removes unreferenced new target")

promotion <- wsl_promote_bundle(candidate_root, canonical_root, "winter_storm_levels_manifest.json",
                                single_horizon_config)
check(promotion$changed && file.exists(file.path(canonical_root, entry$path)), "successful complete-set promotion")
check(!file.exists(file.path(canonical_root, old_relative)), "bounded target cleanup after manifest promotion")
wsl_validate_manifest(file.path(canonical_root, "winter_storm_levels_manifest.json"), canonical_root,
                      single_horizon_config)

same <- manifest
same$retrieval_time_utc <- "2026-01-15T09:00:00Z"
wsl_write_json(same, file.path(candidate_root, "winter_storm_levels_manifest.json"))
check(wsl_is_semantic_noop(file.path(candidate_root, "winter_storm_levels_manifest.json"),
                           file.path(canonical_root, "winter_storm_levels_manifest.json")), "timestamps do not defeat semantic no-op")
same$status <- "delayed_but_usable"
wsl_write_json(same, file.path(candidate_root, "winter_storm_levels_manifest.json"))
check(!wsl_is_semantic_noop(file.path(candidate_root, "winter_storm_levels_manifest.json"),
                            file.path(canonical_root, "winter_storm_levels_manifest.json")), "status change is substantive")

# Version-1 bootstrap and contractual two-cycle retention.
contract_root <- tempfile("winter-storm-level-contract-")
bootstrap_candidate <- file.path(contract_root, "bootstrap-candidate")
contract_canonical <- file.path(contract_root, "canonical")
dir.create(bootstrap_candidate, recursive = TRUE)
on.exit(unlink(contract_root, recursive = TRUE, force = TRUE), add = TRUE)
cycle_a <- wsl_as_utc("2026-01-15T00:00:00Z")
cycle_b <- wsl_as_utc("2026-01-15T06:00:00Z")
cycle_c <- wsl_as_utc("2026-01-15T12:00:00Z")
cycle_a_entries <- wsl_test_cycle_entries(bootstrap_candidate, cycle_a, config, contour, stats)
bootstrap_manifest <- wsl_test_write_manifest(
  bootstrap_candidate, cycle_a, cycle_a_entries, cycle_a + 2 * 3600, config
)
check(identical(bootstrap_manifest$status, "current"), "fresh bootstrap status is current")
check(length(bootstrap_manifest$targets) == 11L, "fresh bootstrap has exactly 11 targets")
check(is.null(bootstrap_manifest$publication_time_utc), "builder publication time is null")
check(isTRUE(wsl_require_current_bootstrap(
  bootstrap_manifest, cycle_a + 2 * 3600, config, FALSE
)), "fresh complete bootstrap accepted")
bootstrap_promotion <- wsl_promote_bundle(
  bootstrap_candidate, contract_canonical, "winter_storm_levels_manifest.json",
  config, now = cycle_a + 2 * 3600
)
check(bootstrap_promotion$changed, "fresh bootstrap promoted in isolated contract test")

noncurrent_bootstraps <- list(
  delayed = list(age_hours = 10, status = "delayed_but_usable"),
  stale = list(age_hours = 16, status = "stale_last_known_good"),
  expired = list(age_hours = 25, status = "expired")
)
for (label in names(noncurrent_bootstraps)) {
  fixture <- noncurrent_bootstraps[[label]]
  candidate <- file.path(contract_root, paste0(label, "-bootstrap-candidate"))
  canonical <- file.path(contract_root, paste0(label, "-bootstrap-canonical"))
  entries <- wsl_test_cycle_entries(candidate, cycle_a, config, contour, stats)
  observation_time <- cycle_a + fixture$age_hours * 3600
  manifest <- wsl_test_write_manifest(candidate, cycle_a, entries, observation_time, config)
  check(identical(manifest$status, fixture$status),
        paste(label, "bootstrap fixture has the expected noncurrent status"))
  check_error(
    wsl_require_current_bootstrap(manifest, observation_time, config, FALSE),
    class = "validation_failed", pattern = "First canonical"
  )
  check(!file.exists(file.path(canonical, "winter_storm_levels_manifest.json")),
        paste("rejected", label, "bootstrap does not promote a canonical manifest"))
}

cycle_b_candidate <- file.path(contract_root, "cycle-b-candidate")
cycle_b_entries <- wsl_test_cycle_entries(cycle_b_candidate, cycle_b, config, contour, stats)
cycle_b_complete <- wsl_copy_retained_cycles(
  cycle_b_candidate, cycle_b_entries, cycle_b, config, contract_canonical,
  now = cycle_b + 2 * 3600
)
cycle_b_manifest <- wsl_test_write_manifest(
  cycle_b_candidate, cycle_b, cycle_b_complete, cycle_b + 2 * 3600, config
)
check(length(cycle_b_manifest$targets) == 22L, "second cycle produces exactly 22 targets")
check(length(unique(vapply(cycle_b_manifest$targets, `[[`, character(1), "cycle_time_utc"))) == 2L,
      "second cycle produces exactly two complete cycles")
check(identical(cycle_b_manifest$cycle_time_utc, wsl_iso_utc(cycle_b)),
      "second cycle becomes manifest root")
invisible(wsl_promote_bundle(
  cycle_b_candidate, contract_canonical, "winter_storm_levels_manifest.json",
  config, now = cycle_b + 2 * 3600
))
check(all(vapply(cycle_a_entries, function(entry) {
  target <- file.path(contract_canonical, entry$path)
  file.exists(target) && identical(wsl_sha256(target), entry$sha256)
}, logical(1))), "all first-cycle retained paths and hashes remain valid")

valid_prior_snapshot <- file.path(contract_root, "valid-prior-snapshot")
wsl_test_copy_tree(contract_canonical, valid_prior_snapshot)
new_cycle_candidate <- function(label) {
  root <- file.path(contract_root, label)
  entries <- wsl_test_cycle_entries(root, cycle_c, config, contour, stats)
  list(root = root, entries = entries)
}

malformed_prior <- file.path(contract_root, "malformed-prior")
wsl_test_copy_tree(valid_prior_snapshot, malformed_prior)
writeLines("{", file.path(malformed_prior, "winter_storm_levels_manifest.json"))
candidate <- new_cycle_candidate("candidate-malformed-prior")
check_error(wsl_copy_retained_cycles(
  candidate$root, candidate$entries, cycle_c, config, malformed_prior
))

unsafe_prior <- file.path(contract_root, "unsafe-prior")
wsl_test_copy_tree(valid_prior_snapshot, unsafe_prior)
unsafe_manifest <- jsonlite::fromJSON(
  file.path(unsafe_prior, "winter_storm_levels_manifest.json"), simplifyVector = FALSE
)
unsafe_manifest$targets[[1L]]$path <- "../unsafe.geojson"
wsl_write_json(unsafe_manifest, file.path(unsafe_prior, "winter_storm_levels_manifest.json"))
candidate <- new_cycle_candidate("candidate-unsafe-prior")
check_error(wsl_copy_retained_cycles(
  candidate$root, candidate$entries, cycle_c, config, unsafe_prior
), pattern = "Unsafe Winter Storm Levels target path")

missing_prior <- file.path(contract_root, "missing-prior")
wsl_test_copy_tree(valid_prior_snapshot, missing_prior)
missing_manifest <- jsonlite::fromJSON(
  file.path(missing_prior, "winter_storm_levels_manifest.json"), simplifyVector = FALSE
)
unlink(file.path(missing_prior, missing_manifest$targets[[1L]]$path))
candidate <- new_cycle_candidate("candidate-missing-prior")
check_error(wsl_copy_retained_cycles(
  candidate$root, candidate$entries, cycle_c, config, missing_prior
), pattern = "target is missing")

checksum_prior <- file.path(contract_root, "checksum-prior")
wsl_test_copy_tree(valid_prior_snapshot, checksum_prior)
checksum_manifest <- jsonlite::fromJSON(
  file.path(checksum_prior, "winter_storm_levels_manifest.json"), simplifyVector = FALSE
)
writeLines("tampered", file.path(checksum_prior, checksum_manifest$targets[[1L]]$path))
candidate <- new_cycle_candidate("candidate-checksum-prior")
check_error(wsl_copy_retained_cycles(
  candidate$root, candidate$entries, cycle_c, config, checksum_prior
), pattern = "checksum mismatch")

candidate <- new_cycle_candidate("candidate-copy-failure")
check_error(wsl_copy_retained_cycles(
  candidate$root, candidate$entries, cycle_c, config, valid_prior_snapshot,
  copy_file = function(...) FALSE
), class = "publication_failed", pattern = "Could not retain canonical target")

cycle_c_candidate <- file.path(contract_root, "cycle-c-candidate")
cycle_c_entries <- wsl_test_cycle_entries(cycle_c_candidate, cycle_c, config, contour, stats)
cycle_c_complete <- wsl_copy_retained_cycles(
  cycle_c_candidate, cycle_c_entries, cycle_c, config, contract_canonical,
  now = cycle_c + 2 * 3600
)
cycle_c_manifest <- wsl_test_write_manifest(
  cycle_c_candidate, cycle_c, cycle_c_complete, cycle_c + 2 * 3600, config
)
cycle_c_cycles <- sort(unique(vapply(
  cycle_c_manifest$targets, `[[`, character(1), "cycle_time_utc"
)))
check(identical(cycle_c_cycles, sort(c(wsl_iso_utc(cycle_b), wsl_iso_utc(cycle_c)))),
      "third-cycle rotation retains exactly B and C")
check(length(cycle_c_manifest$targets) == 22L, "third-cycle rotation remains exactly 22 targets")
rotation <- wsl_promote_bundle(
  cycle_c_candidate, contract_canonical, "winter_storm_levels_manifest.json",
  config, now = cycle_c + 2 * 3600
)
check(length(rotation$removed) == 11L, "third-cycle rotation evicts all 11 cycle-A targets")
check(!any(vapply(cycle_a_entries, function(entry) {
  file.exists(file.path(contract_canonical, entry$path))
}, logical(1))), "third-cycle rotation removes cycle A")
rotated <- jsonlite::fromJSON(
  file.path(contract_canonical, "winter_storm_levels_manifest.json"), simplifyVector = FALSE
)
check(identical(rotated$cycle_time_utc, wsl_iso_utc(cycle_c)) &&
      length(rotated$targets) == 22L, "rotated canonical root is cycle C with 22 targets")

same_cycle_candidate <- file.path(contract_root, "same-cycle-candidate")
same_cycle_entries <- wsl_test_cycle_entries(same_cycle_candidate, cycle_c, config, contour, stats)
same_cycle_complete <- wsl_copy_retained_cycles(
  same_cycle_candidate, same_cycle_entries, cycle_c, config, contract_canonical,
  now = cycle_c + 3 * 3600
)
same_cycle_manifest <- wsl_test_write_manifest(
  same_cycle_candidate, cycle_c, same_cycle_complete, cycle_c + 3 * 3600, config
)
check(is.null(same_cycle_manifest$publication_time_utc), "same-cycle candidate publication time remains null")
check(wsl_is_semantic_noop(
  file.path(same_cycle_candidate, "winter_storm_levels_manifest.json"),
  file.path(contract_canonical, "winter_storm_levels_manifest.json")
), "same-cycle retrieval-only change remains a semantic no-op")
same_cycle_promotion <- wsl_promote_bundle(
  same_cycle_candidate, contract_canonical, "winter_storm_levels_manifest.json",
  config, now = cycle_c + 3 * 3600
)
check(!same_cycle_promotion$changed, "same-cycle semantic no-op creates no replacement")

constant <- source_raster
values(constant) <- -100
check_error(wsl_make_contours(constant, synthetic_record, config), pattern = "No configured snow-level contours")

fixture_root <- file.path("tests", "fixtures", "winter_storm_levels")
fixture_manifest_path <- file.path(fixture_root, "expired_manifest.json")
wsl_validate_manifest(fixture_manifest_path, fixture_root, single_horizon_config)
fixture_manifest <- jsonlite::fromJSON(fixture_manifest_path, simplifyVector = FALSE)
fixture_target <- fixture_manifest$targets[[1L]]
wsl_validate_geojson(
  file.path(fixture_root, fixture_target$path),
  list(
    cycle_time_utc = fixture_target$cycle_time_utc,
    valid_time_utc = fixture_target$valid_time_utc,
    lead_hours = fixture_target$lead_hours
  ),
  config
)
check(identical(fixture_manifest$status, "expired"), "browser expired-state fixture")

cnrfc_reference <- jsonlite::fromJSON(
  file.path(fixture_root, "cnrfc_freezing_level_reference.json"), simplifyVector = FALSE
)
check(identical(cnrfc_reference$reference_type, "freezing_level") &&
      identical(cnrfc_reference$canonical_field, "snow_level") &&
      identical(cnrfc_reference$comparable_to_canonical, FALSE),
      "CNRFC fixture cannot be mislabeled as canonical snow level")
cnrfc_values_ft <- unlist(lapply(cnrfc_reference$basins, function(basin) {
  as.numeric(unlist(basin$values_thousands_ft)) * 1000
}))
check(all(is.finite(cnrfc_values_ft)) &&
      all(cnrfc_values_ft >= config$contour_min_ft & cnrfc_values_ft <= config$contour_max_ft),
      "official CNRFC numeric reference lies inside the configured display range")
check(identical(cnrfc_reference$basins[[1L]]$values_thousands_ft[[1L]] * 1000, 17070),
      "CNRFC thousands-of-feet unit conversion")

cat(sprintf("Winter Storm Levels tests passed (%d checks).\n", checks))
