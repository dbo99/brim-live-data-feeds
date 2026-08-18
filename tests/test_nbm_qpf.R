#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(digest)
  library(httr2)
  library(jsonlite)
  library(png)
  library(terra)
})
source(file.path("scripts", "nbm_qpf_helpers.R"))

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
    stop("Expected error class ", class, "; received ",
         paste(base::class(error), collapse = ", "), call. = FALSE)
  }
  if (!is.null(pattern) && !grepl(pattern, conditionMessage(error), fixed = TRUE)) {
    stop("Error did not contain expected text: ", pattern, call. = FALSE)
  }
  invisible(error)
}
clone_value <- function(value) unserialize(serialize(value, NULL))

palette_path <- file.path("data", "input", "nbm_qpf_palette.csv")
palette <- qpf_read_palette(palette_path)
check(nrow(palette) == 22L, "locked palette includes transparency plus 21 painted classes")
check(identical(QPF_SUPPORTED_LEADS,
                c(6L, 12L, 18L, 24L, 30L, 36L, 42L, 48L, 60L, 72L)),
      "exact ten-lead product set")
check(identical(qpf_iso_utc(qpf_floor_cycle(qpf_as_utc("2026-08-18T01:00:00Z"))),
                "2026-08-18T00:00:00Z"), "primary cycle flooring")
check_error(qpf_validate_cycle("2026-08-18T03:00:00Z"), "validation_failed",
            "canonical 00/06/12/18")
urls <- qpf_nbm_urls("2026-08-17T06:00:00Z", 60L)
check(endsWith(urls$grib, "blend.20260817/06/core/blend.t06z.core.f060.co.grib2"),
      "NBM Core CONUS URL contract")

make_index <- function(cycle_utc, lead, duplicate = FALSE,
                       interval_start = lead - 6L, interval_end = lead) {
  token <- qpf_cycle_token(cycle_utc)
  expected <- sprintf(
    "3:300:d=%s:APCP:surface:%d-%d hour acc fcst:",
    token, interval_start, interval_end
  )
  lines <- c(
    sprintf("1:100:d=%s:APCP:surface:%d-%d hour acc fcst:prob >0.254:prob fcst 255/255",
            token, interval_start, interval_end),
    sprintf("2:200:d=%s:APCP:surface:%d-%d hour acc fcst:",
            token, max(0L, interval_end - 1L), interval_end),
    expected,
    sprintf("4:500:d=%s:TMP:2 m above ground:%d hour fcst:", token, lead)
  )
  if (duplicate) lines <- append(lines, sub("3:300", "31:400", expected, fixed = TRUE), 3L)
  paste(lines, collapse = "\n")
}

cycle <- "2026-08-17T06:00:00Z"
record <- qpf_parse_index(make_index(cycle, 6L), cycle, 6L)
check(record$start_byte == 300 && record$end_byte == 499 && record$expected_bytes == 200,
      "exact deterministic APCP byte range")
check(identical(record$accumulation_start_utc, cycle) &&
        identical(record$accumulation_end_utc, "2026-08-17T12:00:00Z") &&
        identical(record$valid_time_utc, record$accumulation_end_utc),
      "native six-hour temporal interval")
check(!grepl("prob", record$inventory_line, fixed = TRUE), "probability record excluded")
check_error(qpf_parse_index(make_index(cycle, 6L, duplicate = TRUE), cycle, 6L),
            "validation_failed", "found 2")
check_error(qpf_parse_index(make_index(cycle, 6L, interval_start = 1L), cycle, 6L),
            "validation_failed", "found 0")
check_error(qpf_parse_index(make_index(cycle, 6L, interval_end = 5L), cycle, 6L),
            "validation_failed", "found 0")
check_error(qpf_parse_index(gsub(":APCP:surface:", ":TMP:surface:",
                                 make_index(cycle, 6L), fixed = TRUE), cycle, 6L),
            "variable_missing", "does not contain surface APCP")
malformed_index <- sub("4:500", "bad:500", make_index(cycle, 6L), fixed = TRUE)
check_error(qpf_parse_index(malformed_index, cycle, 6L), "validation_failed", "malformed")
last_record <- paste(
  "1:100:d=2026081706:TMP:surface:6 hour fcst:",
  "2:300:d=2026081706:APCP:surface:0-6 hour acc fcst:",
  sep = "\n"
)
check_error(qpf_parse_index(last_record, cycle, 6L), "validation_failed",
            "requires the GRIB object size")
last <- qpf_parse_index(last_record, cycle, 6L, object_size = 800)
check(last$start_byte == 300 && last$end_byte == 799, "last-record byte range uses object size")

fake_inventory <- function(url) {
  lead <- as.integer(sub(".*f([0-9]{3})\\.co.*", "\\1", url))
  cycle_token <- sub(".*blend\\.([0-9]{8})/([0-9]{2})/.*", "\\1\\2", url)
  cycle_time <- as.POSIXct(cycle_token, format = "%Y%m%d%H", tz = "UTC")
  text <- make_index(qpf_iso_utc(cycle_time), lead)
  raw <- charToRaw(text)
  list(text = text, bytes = length(raw), sha256 = qpf_sha256_raw(raw))
}
records <- qpf_preflight_cycle(cycle, fake_inventory, function(url) 1000)
check(length(records) == 10L &&
        identical(vapply(records, `[[`, integer(1), "lead_hours"), QPF_SUPPORTED_LEADS),
      "all-ten inventory preflight")
check_error(qpf_validate_preflight(records[-1L]), "validation_failed", "exactly ten")
bad_records <- clone_value(records)
bad_records[[2L]]$lead_hours <- bad_records[[1L]]$lead_hours
check_error(qpf_validate_preflight(bad_records), "validation_failed", "duplicated")
bad_records <- clone_value(records)
bad_records[[2L]]$cycle_utc <- "2026-08-17T00:00:00Z"
check_error(qpf_validate_preflight(bad_records), "validation_failed", "mixes source cycles")
bad_records <- clone_value(records)
bad_records[[2L]]$accumulation_hours <- 1L
check_error(qpf_validate_preflight(bad_records), "validation_failed", "temporal")
bad_records <- clone_value(records)
bad_records[[2L]]$accumulation_end_utc <- "2026-08-17T17:00:00Z"
check_error(qpf_validate_preflight(bad_records), "validation_failed", "temporal")

discovery_calls <- 0L
discovered <- qpf_discover_cycle(
  now = qpf_as_utc("2026-08-17T07:00:00Z"),
  lookback_hours = 6,
  fetch_inventory = function(url) {
    discovery_calls <<- discovery_calls + 1L
    if (grepl("blend.20260817/06/", url, fixed = TRUE)) {
      qpf_stop("source_unavailable", "new cycle is incomplete")
    }
    fake_inventory(url)
  },
  fetch_object_size = function(url) 1000
)
check(identical(qpf_iso_utc(discovered$cycle), "2026-08-17T00:00:00Z") &&
        length(discovered$attempts) == 2L && discovery_calls == 11L,
      "cycle discovery refuses incomplete newest cycle and selects complete fallback")
check_error(qpf_discover_cycle(
  explicit_cycle = cycle,
  fetch_inventory = function(url) qpf_stop("source_unavailable", "missing lead"),
  fetch_object_size = function(url) 1000
), "source_unavailable", "missing lead")

check(identical(qpf_classify_http_status(404), "source_unavailable"), "404 source state")
check(identical(qpf_classify_http_status(429), "fetch_failed_transient"), "429 retry state")
range_body <- qpf_validate_range_response(
  response(206, headers = list("content-range" = "bytes 10-19/100"), body = as.raw(1:10)),
  10, 19
)
check(identical(range_body, as.raw(1:10)), "exact partial content response")
check_error(qpf_validate_range_response(response(200, body = charToRaw("maintenance")), 10, 19),
            "validation_failed", "HTTP 200")
check_error(qpf_validate_range_response(
  response(206, headers = list("content-range" = "bytes 11-20/100"), body = as.raw(1:10)),
  10, 19
), "validation_failed", "Unexpected Content-Range")

metadata_for <- function(record) {
  list(
    band_count = 1L,
    reference_time_epoch = as.numeric(qpf_as_utc(record$cycle_utc)),
    forecast_seconds = as.numeric(record$lead_hours - 6L) * 3600,
    valid_time_epoch = as.numeric(qpf_as_utc(record$valid_time_utc)),
    element = "QPF06",
    level = "0-SFC",
    pdtn = 8,
    unit = "[kg/(m^2)]",
    comment = "06 hr Total precipitation [kg/(m^2)]",
    discipline = "0(Meteorological)",
    ids = paste0("CENTER=7(US-NCEP) SUBCENTER=14(Meteorological Development Laboratory (MDL)) ",
                 "TYPE=1(Forecast)"),
    parse_errors = character()
  )
}
diagnostic <- qpf_source_diagnostic(metadata_for(records[[1L]]), records[[1L]],
                                    records[[1L]]$valid_time_utc,
                                    list(test_runtime = TRUE))
check(isTRUE(diagnostic$authoritative_metadata_accepted) &&
        isTRUE(qpf_require_source_diagnostic(diagnostic)),
      "exact APCP metadata accepted")
bad_metadata <- metadata_for(records[[1L]])
bad_metadata$forecast_seconds <- 3600
check_error(qpf_require_source_diagnostic(qpf_source_diagnostic(
  bad_metadata, records[[1L]], records[[1L]]$valid_time_utc, list(test_runtime = TRUE)
)), "validation_failed", "accumulation start")
bad_metadata <- metadata_for(records[[1L]])
bad_metadata$valid_time_epoch <- bad_metadata$valid_time_epoch - 3600
check_error(qpf_require_source_diagnostic(qpf_source_diagnostic(
  bad_metadata, records[[1L]], records[[1L]]$valid_time_utc, list(test_runtime = TRUE)
)), "validation_failed", "valid time")
bad_metadata <- metadata_for(records[[1L]])
bad_metadata$comment <- "01 hr Total precipitation [kg/(m^2)]"
check_error(qpf_require_source_diagnostic(qpf_source_diagnostic(
  bad_metadata, records[[1L]], records[[1L]]$valid_time_utc, list(test_runtime = TRUE)
)), "validation_failed", "six-hour precipitation total")
bad_metadata <- metadata_for(records[[1L]])
bad_metadata$element <- "APCP"
check_error(qpf_require_source_diagnostic(qpf_source_diagnostic(
  bad_metadata, records[[1L]], records[[1L]]$valid_time_utc, list(test_runtime = TRUE)
)), "validation_failed", "six-hour QPF")
malformed_metadata <- qpf_parse_grib_metadata(c("Band 1", "GRIB_ELEMENT=QPF06"))
check(length(malformed_metadata$parse_errors) > 0L, "malformed source metadata is diagnosed")

amounts <- c(NA, 0, 0.009999, 0.01, 0.099999, 0.10, 0.25, 3.0, 20.0, 25.0)
check(identical(qpf_class_index(amounts, palette),
                c(0L, 0L, 0L, 1L, 1L, 2L, 3L, 9L, 21L, 21L)),
      "lower-inclusive upper-exclusive palette boundaries")
check(identical(qpf_class_color(c(0.1, 3.0, 20.0), palette),
                c("#A6DBA0", "#F79441", "#F1C6E7")),
      "fixed palette color mapping")
reference_colors <- qpf_class_color(c(0, 0.01, 0.5, 3, 20), palette)
check(all(vapply(c("f006", "f072", "dry", "storm", "previous"), function(context) {
  identical(qpf_class_color(c(0, 0.01, 0.5, 3, 20), palette), reference_colors)
}, logical(1))), "palette is invariant across target and cycle contexts")
near_zero_rgba <- qpf_rgba_array(matrix(c(NA, 0, 0.009999, 0.01) * 25.4, nrow = 2), palette)
check(all(round(near_zero_rgba[, , 4L] * 255) == matrix(c(0, 0, 0, 255), nrow = 2)),
      "nodata and finite QPF below 0.01 inch are transparent")

legend_cases <- data.frame(
  maximum = c(0, 0.09, 2.8, 3.0, 5.3, 8.6, 12.7, 19.5, 25.0),
  cap = c(3, 3, 3, 4, 6, 10, 15, 20, 20),
  overflow = c(FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, TRUE)
)
for (index in seq_len(nrow(legend_cases))) {
  result <- qpf_legend_cap(legend_cases$maximum[[index]], palette)
  check(result$legend_cap_in == legend_cases$cap[[index]] &&
          identical(result$legend_overflow, legend_cases$overflow[[index]]),
        paste("legend boundary", legend_cases$maximum[[index]]))
}

template <- qpf_destination_template()
values(template) <- 0
template_stats <- qpf_validate_output_raster(template)
check(template_stats$rows == 733L && template_stats$columns == 720L &&
        terra::same.crs(template, "EPSG:3857"), "locked output dimensions and CRS")
check(all(abs(c(xmin(template), ymin(template), xmax(template), ymax(template)) -
                QPF_EXTENT_3857) < 1e-6) &&
        all(abs(res(template) - QPF_PIXEL_SIZE_M) < 1e-6),
      "locked output extent and pixel size")
subset_source <- rast(ncols = 40, nrows = 30, xmin = -140, xmax = -100,
                      ymin = 20, ymax = 50, crs = "EPSG:4326")
values(subset_source) <- 1
subset <- qpf_source_subset(subset_source)
check(xmin(subset) <= QPF_BOUNDS_WGS84[["west"]] - 2 * res(subset_source)[[1L]] &&
        xmax(subset) >= QPF_BOUNDS_WGS84[["east"]] + 2 * res(subset_source)[[1L]] &&
        ymin(subset) <= QPF_BOUNDS_WGS84[["south"]] - 2 * res(subset_source)[[2L]] &&
        ymax(subset) >= QPF_BOUNDS_WGS84[["north"]] + 2 * res(subset_source)[[2L]],
      "source crop retains the required two-native-cell buffer")
pipeline_body <- paste(deparse(body(qpf_build_target)), collapse = "\n")
check(regexpr("qpf_project_numeric", pipeline_body, fixed = TRUE)[[1L]] <
        regexpr("qpf_write_rgba_png", pipeline_body, fixed = TRUE)[[1L]] &&
        grepl('method = "bilinear"', paste(deparse(body(qpf_project_numeric)), collapse = "\n"),
              fixed = TRUE),
      "numeric bilinear reprojection occurs before palette classification")
orientation <- matrix(0, nrow = QPF_IMAGE_HEIGHT, ncol = QPF_IMAGE_WIDTH)
orientation[1L, ] <- 25.4
orientation[QPF_IMAGE_HEIGHT, ] <- 0.254
orientation_rgba <- qpf_rgba_array(orientation, palette)
top_rgb <- as.integer(round(orientation_rgba[1L, 1L, 1:3] * 255))
bottom_rgb <- as.integer(round(orientation_rgba[QPF_IMAGE_HEIGHT, 1L, 1:3] * 255))
check(identical(top_rgb, as.integer(grDevices::col2rgb("#146B38"))) &&
        identical(bottom_rgb, as.integer(grDevices::col2rgb("#D9F0D3"))),
      "north-to-south raster rows are not inverted")
wrong_crs <- template
crs(wrong_crs) <- "EPSG:4326"
check_error(qpf_validate_output_raster(wrong_crs), "validation_failed", "CRS")
wrong_extent <- template
ext(wrong_extent) <- ext(xmin(template) + 1000, xmax(template) + 1000,
                         ymin(template), ymax(template))
check_error(qpf_validate_output_raster(wrong_extent), "validation_failed", "extent")
alignment <- qpf_alignment_diagnostics()
check(alignment$max_pixel_center_error_m < max(QPF_PIXEL_SIZE_M) &&
        min(alignment$rejected_stretch_offset_range_km) > 20 &&
        max(alignment$rejected_stretch_offset_range_km) < 40,
      "alignment calibration detects systematic latitude-grid stretch")

image_root <- tempfile("nbm-qpf-image-tests-")
dir.create(image_root, recursive = TRUE)
on.exit(unlink(image_root, recursive = TRUE, force = TRUE), add = TRUE)
image_mm <- matrix(0, nrow = QPF_IMAGE_HEIGHT, ncol = QPF_IMAGE_WIDTH)
image_mm[100:110, 200:210] <- 25.4 * 3.0
image_mm[300:305, 500:510] <- NA_real_
reference_png <- file.path(image_root, "reference.png")
webp_path <- file.path(image_root, "valid.webp")
reference <- qpf_write_rgba_png(image_mm, reference_png, palette)
qpf_encode_lossless_webp(reference_png, webp_path)
webp_stats <- qpf_validate_webp(webp_path, palette, reference)
check(isTRUE(webp_stats$lossless_vp8l) && webp_stats$width == 720L &&
        webp_stats$height == 733L && webp_stats$painted_pixel_count > 0L,
      "lossless VP8L RGBA encode/decode and exact pixels")
check(identical(webp_stats$alpha_values, c(0L, 255L)),
      "WebP diagnostics retain only distinct binary alpha values")
zero_png <- file.path(image_root, "all-zero.png")
zero_webp <- file.path(image_root, "all-zero.webp")
zero_reference <- qpf_write_rgba_png(
  matrix(0.009 * 25.4, nrow = QPF_IMAGE_HEIGHT, ncol = QPF_IMAGE_WIDTH),
  zero_png,
  palette
)
qpf_encode_lossless_webp(zero_png, zero_webp)
zero_stats <- qpf_validate_webp(zero_webp, palette, zero_reference)
check(zero_stats$painted_pixel_count == 0L &&
        zero_stats$transparent_pixel_count == QPF_IMAGE_WIDTH * QPF_IMAGE_HEIGHT &&
        identical(zero_stats$alpha_values, 0L),
      "all-zero and near-zero target remains a valid fully transparent VP8L image")
small_png <- file.path(image_root, "small.png")
small_webp <- file.path(image_root, "small.webp")
png::writePNG(array(0, dim = c(10, 10, 4)), small_png)
qpf_encode_lossless_webp(small_png, small_webp)
check_error(qpf_validate_webp(small_webp, palette, width = 720, height = 733),
            "validation_failed", "dimensions")

candidate_root <- tempfile("nbm-qpf-candidate-fixture-")
dir.create(candidate_root, recursive = TRUE)
on.exit(unlink(candidate_root, recursive = TRUE, force = TRUE), add = TRUE)
targets <- list()
target_validations <- list()
maxima <- seq(0.1, 1.0, length.out = 10)
webp_sha <- qpf_sha256_file(webp_path)
for (index in seq_along(records)) {
  record <- records[[index]]
  filename <- qpf_target_filename(record, webp_sha)
  image_path <- file.path(QPF_ASSET_ROOT, filename)
  destination <- file.path(candidate_root, image_path)
  dir.create(dirname(destination), recursive = TRUE, showWarnings = FALSE)
  check(file.copy(webp_path, destination, overwrite = FALSE),
        paste("fixture target copy", record$lead_hours))
  target <- qpf_target_entry(record, image_path, destination)
  targets[[index]] <- target
  target_validations[[index]] <- list(
    lead_hours = record$lead_hours,
    cycle_utc = record$cycle_utc,
    accumulation_start_utc = record$accumulation_start_utc,
    accumulation_end_utc = record$accumulation_end_utc,
    accumulation_hours = 6L,
    inventory_sha256 = record$inventory_sha256,
    inventory_record = record$inventory_line,
    source_bytes = 1000,
    source_sha256 = paste(rep("a", 64), collapse = ""),
    source_grid = list(rows = 1597L, columns = 2345L),
    source_metadata = list(authoritative_metadata_accepted = TRUE),
    output_grid = list(rows = 733L, columns = 720L),
    target_max_qpf_in = maxima[[index]],
    webp = list(path = image_path, bytes = target$bytes, sha256 = target$sha256)
  )
}
manifest <- qpf_candidate_manifest(cycle, targets, maxima, palette)
validation <- qpf_candidate_validation(
  manifest, target_validations, list(test_runtime = TRUE), list(performed = FALSE)
)
qpf_write_json(manifest, file.path(candidate_root, QPF_CANDIDATE_MANIFEST))
qpf_write_json(validation, file.path(candidate_root, QPF_VALIDATION_FILE))
qpf_write_preflight(records, file.path(candidate_root, QPF_PREFLIGHT_FILE))
check(is.list(qpf_validate_candidate(candidate_root, palette_path)),
      "complete candidate manifest-target closure")

copy_candidate <- function(source) {
  destination <- tempfile("nbm-qpf-bad-candidate-")
  dir.create(destination, recursive = TRUE)
  files <- list.files(source, recursive = TRUE, all.files = TRUE, no.. = TRUE,
                      full.names = FALSE)
  for (relative in files) {
    dir.create(dirname(file.path(destination, relative)), recursive = TRUE,
               showWarnings = FALSE)
    if (!file.copy(file.path(source, relative), file.path(destination, relative))) {
      stop("Could not copy candidate fixture.")
    }
  }
  destination
}
mutate_manifest <- function(root, callback) {
  path <- file.path(root, QPF_CANDIDATE_MANIFEST)
  value <- jsonlite::fromJSON(path, simplifyVector = FALSE)
  value <- callback(value)
  qpf_write_json(value, path)
}

bad <- copy_candidate(candidate_root)
unlink(file.path(bad, targets[[1L]]$image_path))
check_error(qpf_validate_candidate(bad, palette_path), "validation_failed", "missing")
unlink(bad, recursive = TRUE)

bad <- copy_candidate(candidate_root)
writeLines("unexpected", file.path(bad, "unexpected.txt"))
check_error(qpf_validate_candidate(bad, palette_path), "validation_failed", "unexpected")
unlink(bad, recursive = TRUE)

bad <- copy_candidate(candidate_root)
mutate_manifest(bad, function(value) {
  value$cycle$targets[[1L]]$sha256 <- paste(rep("0", 64), collapse = "")
  value
})
check_error(qpf_validate_candidate(bad, palette_path), "validation_failed", "identity mismatch")
unlink(bad, recursive = TRUE)

bad <- copy_candidate(candidate_root)
mutate_manifest(bad, function(value) {
  value$cycle$targets[[2L]] <- value$cycle$targets[[1L]]
  value
})
check_error(qpf_validate_candidate(bad, palette_path), "validation_failed")
unlink(bad, recursive = TRUE)

bad <- copy_candidate(candidate_root)
mutate_manifest(bad, function(value) {
  value$cycle$targets <- value$cycle$targets[-10L]
  value$cycle$target_count <- 9L
  value
})
check_error(qpf_validate_candidate(bad, palette_path), "validation_failed", "completeness")
unlink(bad, recursive = TRUE)

bad <- copy_candidate(candidate_root)
mutate_manifest(bad, function(value) {
  value$cycle$targets[[2L]]$accumulation_hours <- 1L
  value
})
check_error(qpf_validate_candidate(bad, palette_path), "validation_failed", "time/unit/spatial")
unlink(bad, recursive = TRUE)

bad <- copy_candidate(candidate_root)
mutate_manifest(bad, function(value) {
  value$cycle$targets[[2L]]$accumulation_end_utc <- "2026-08-17T17:00:00Z"
  value
})
check_error(qpf_validate_candidate(bad, palette_path), "validation_failed", "time/unit/spatial")
unlink(bad, recursive = TRUE)

bad <- copy_candidate(candidate_root)
mutate_manifest(bad, function(value) {
  value$cycle$targets[[2L]]$cycle_utc <- "2026-08-17T00:00:00Z"
  value
})
check_error(qpf_validate_candidate(bad, palette_path), "validation_failed", "time/unit/spatial")
unlink(bad, recursive = TRUE)

check_error(
  qpf_assert_offline_output_root(file.path(getwd(), "docs", "data", "nbm-qpf-test"), getwd()),
  "validation_failed", "may not write under canonical docs/data"
)

cat("NBM QPF tests passed:", checks, "checks\n")
