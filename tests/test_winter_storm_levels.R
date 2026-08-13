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

check(identical(wsl_freshness_status(now - 8 * 3600, now, config), "current"), "current freshness")
check(identical(wsl_freshness_status(now - 10 * 3600, now, config), "delayed_but_usable"), "delayed freshness")
check(identical(wsl_freshness_status(now - 18 * 3600, now, config), "stale_last_known_good"), "last-known-good freshness")
check(identical(wsl_freshness_status(now - 25 * 3600, now, config), "expired"), "expired freshness")
check(identical(wsl_freshness_status(now, now, config, FALSE), "expired"), "no active target expires")

synthetic_record <- list(
  cycle_time_utc = "2026-01-15T06:00:00Z",
  valid_time_utc = "2026-01-15T12:00:00Z",
  lead_hours = 6L,
  grib_url = "https://example.invalid/source.grib2",
  index_url = "https://example.invalid/source.grib2.idx",
  inventory_line = "1:0:d=2026011506:SNOWLVL:0 m above mean sea level:6 hour fcst:"
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
stats <- wsl_validate_raster(source_raster, synthetic_record, config)
check(stats$finite_coverage == 1 && stats$min_m >= 0, "finite synthetic raster validation")

wrong_unit <- source_raster
units(wrong_unit) <- "K"
check_error(wsl_validate_raster(wrong_unit, synthetic_record, config), pattern = "units must be metres")
units(source_raster) <- "m"
wrong_time <- source_raster
time(wrong_time) <- wsl_as_utc("2026-01-15T13:00:00Z")
check_error(wsl_validate_raster(wrong_time, synthetic_record, config), pattern = "valid time")
time(source_raster) <- wsl_as_utc(synthetic_record$valid_time_utc)
negative_raster <- source_raster
values(negative_raster)[1:10] <- -100
check(wsl_validate_raster(negative_raster, synthetic_record, config)$min_m == -100, "documented near-surface negative values accepted")
values(source_raster) <- 400 + (xy$x - config$source_west) * 120 + (xy$y - config$source_south) * 35

contour <- wsl_make_contours(source_raster, synthetic_record, config)
check(contour$feature_count > 0L, "non-empty contour output")
check(all(contour$contour_levels %% config$contour_interval_ft == 0), "configured contour interval")
check(contour$bbox[1L] >= config$west && contour$bbox[3L] <= config$east &&
      contour$bbox[2L] >= config$south && contour$bbox[4L] <= config$north, "exact output bounds")
properties <- contour$geojson$features[[1L]]$properties
check(identical(properties$cycle_time_utc, synthetic_record$cycle_time_utc) &&
      identical(properties$valid_time_utc, synthetic_record$valid_time_utc), "stable feature times")
check(!any(c("retrieval_time_utc", "publication_time_utc", "generated_at") %in% names(properties)), "no volatile per-feature timestamps")

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
same$publication_time_utc <- "2026-01-15T09:00:00Z"
wsl_write_json(same, file.path(candidate_root, "winter_storm_levels_manifest.json"))
check(wsl_is_semantic_noop(file.path(candidate_root, "winter_storm_levels_manifest.json"),
                           file.path(canonical_root, "winter_storm_levels_manifest.json")), "timestamps do not defeat semantic no-op")
same$status <- "delayed_but_usable"
wsl_write_json(same, file.path(candidate_root, "winter_storm_levels_manifest.json"))
check(!wsl_is_semantic_noop(file.path(candidate_root, "winter_storm_levels_manifest.json"),
                            file.path(canonical_root, "winter_storm_levels_manifest.json")), "status change is substantive")

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
