#!/usr/bin/env Rscript
# Offline X2 regression: minimal public DWR text extracted with pdftools from
# the 2026-09-08 Delta Operations Daily Summary; contact/footer text omitted.
# Source: https://water.ca.gov/-/media/DWR-Website/Web-Pages/Programs/State-Water-Project/Operations-And-Maintenance/Files/Operations-Control-Office/Delta-Status-And-Operations/Delta-Operations-Daily-Summary.pdf
# Retrieved 2026-09-09 UTC; source PDF SHA-256:
# 75e317aa7dd852da433d064a0404e15ad5289b532d4a854d93b4268d669c1785

main <- function() {
  builder <- parse("scripts/build_delta_ops_daily_summary.R")
  assigned <- function(expr, name) {
    is.call(expr) && identical(expr[[1]], as.name("<-")) &&
      is.symbol(expr[[2]]) && identical(as.character(expr[[2]]), name)
  }
  index <- function(name) {
    found <- which(vapply(builder, assigned, logical(1), name = name))
    stopifnot(length(found) == 1L)
    found
  }
  parser <- new.env(parent = baseenv())
  eval(builder[[index("pt_parse_x2")]], parser)
  fixture <- paste(readLines("tests/fixtures/delta_ops/dwr_2026-09-08.txt"), collapse = "\n")
  source_row <- "X2 Position (yesterday)   >   81 km"
  replace_row <- function(row) sub(source_row, row, fixture, fixed = TRUE)

  # Exercise the actual builder before/after extraction, bypassing only download
  # and PDF decoding. Every output stays in a new temporary candidate root.
  work <- tempfile("delta-ops-x2-")
  dir.create(work)
  on.exit(unlink(work, recursive = TRUE), add = TRUE)
  overrides <- c(
    DELTA_OPS_OUT_DIR = "",
    DELTA_OPS_SUMMARY_PDF_URL = "https://example.invalid/delta.pdf",
    DELTA_OPS_STATIC_LOCATIONS_CSV = normalizePath("data/input/delta_ops_static_locations.csv"),
    DELTA_OPS_X2_LOOKUP_CSV = normalizePath("data/input/x2_river_km_lookup.csv"),
    DELTA_OPS_SKIP_IF_CURRENT_DATE = "false", DELTA_OPS_FORCE_REFRESH = "false",
    DELTA_OPS_MAX_REPORT_LAG_DAYS = "7", DELTA_OPS_ALLOW_FUTURE_REPORT_DATE = "false"
  )
  old_env <- setNames(Sys.getenv(names(overrides), unset = NA_character_), names(overrides))
  on.exit({
    Sys.unsetenv(names(old_env)[is.na(old_env)])
    if (any(!is.na(old_env))) do.call(Sys.setenv, as.list(old_env[!is.na(old_env)]))
  }, add = TRUE)
  build <- function(text, name) {
    root <- file.path(work, name)
    overrides[["DELTA_OPS_OUT_DIR"]] <- file.path(root, "docs/data")
    do.call(Sys.setenv, as.list(overrides))
    scope <- new.env(parent = globalenv())
    eval(builder[seq_len(index("pdf_tmp") - 1L)], scope)
    scope$feed_build_date_local <- "2026-09-08"
    scope$feed_build_time_utc <- "2026-09-08T19:00:19Z"
    scope$feed_build_time_local <- "2026-09-08 12:00:19 PDT"
    scope$raw_text <- text
    eval(builder[index("x2_value"):length(builder)], scope)
    expected_files <- paste0("delta_ops_", c(
      "daily_summary.json", "daily_summary_summary.json",
      "daily_summary_features.geojson", "x2_reference.geojson"
    ))
    stopifnot(setequal(list.files(overrides[["DELTA_OPS_OUT_DIR"]]), expected_files))
    read <- function(name) jsonlite::read_json(file.path(overrides[["DELTA_OPS_OUT_DIR"]], name))
    validation <- system2("python3", c("scripts/delta_ops_publisher.py", "validate", "--root", shQuote(root)),
                          stdout = TRUE, stderr = TRUE)
    stopifnot(is.null(attr(validation, "status")))
    list(
      values = read("delta_ops_daily_summary.json"),
      summary = read("delta_ops_daily_summary_summary.json"),
      features = read("delta_ops_daily_summary_features.geojson")$features,
      reference = read("delta_ops_x2_reference.geojson")$features
    )
  }
  x2_feature <- function(product) Filter(function(f) f$properties$feature_key == "x2_position_current", product$features)
  other_features <- function(product) Filter(function(f) f$properties$feature_key != "x2_position_current", product$features)

  baseline <- build(replace_row("X2 Position (yesterday) = 81 km"), "equality")
  real <- build(fixture, "real-source")
  stopifnot(real$summary$x2_position_km == 81, real$summary$x2_position_relation == ">",
            real$summary$x2_position_date == "2026-09-07", real$summary$x2_lookup_added,
            real$summary$feature_count == 13, length(real$reference) == 178,
            identical(other_features(real), other_features(baseline)),
            identical(x2_feature(real)[[1]]$geometry, x2_feature(baseline)[[1]]$geometry),
            x2_feature(real)[[1]]$properties$label_text == "X2 9/7: > 81 km")
  real_values <- real$values$values
  baseline_values <- baseline$values$values
  real_values$x2_position_yesterday <- baseline_values$x2_position_yesterday <- NULL
  stopifnot(identical(real_values, baseline_values),
            real_values$controlling_factors == "Delta WQ", real_values$omr_index_daily_value == "-11,200 cfs",
            real_values$outflow_index == "7,000 cfs", real_values$san_luis_total_storage == "1,340 TAF")

  for (relation in c("=", "<", ">", "<=", ">=", "\u2264", "\u2265")) {
    simple <- parser$pt_parse_x2(paste("X2", relation, "73 km"))
    stopifnot(simple$km == 73, simple$relation == relation)
    product <- build(replace_row(paste("X2 Position (yesterday)", relation, "73 km")),
                     paste0("relation-", match(relation, c("=", "<", ">", "<=", ">=", "\u2264", "\u2265"))))
    point <- x2_feature(product)[[1]]
    stopifnot(product$summary$x2_position_km == 73, product$summary$x2_position_relation == relation,
              point$properties$value_numeric == 73, point$properties$x2_position_relation == relation,
              point$properties$units == "km", product$values$x2_position_relation == relation,
              identical(other_features(product), other_features(baseline)))
  }

  for (row in c("X2 Position (yesterday)\t< =\t73.25 km",
                "X2 Position (yesterday)\n>\n= 73.25 km",
                "X2\u00a0Position (yesterday)\n\u2264\u00a073.25\u00a0km")) {
    parsed <- parser$pt_parse_x2(row)
    product <- build(replace_row(row), paste0("whitespace-", utf8ToInt(parsed$relation)[[1]]))
    stopifnot(parsed$km == 73.25, product$summary$x2_position_km == 73.25,
              grepl("73.25 km", x2_feature(product)[[1]]$properties$label_text, fixed = TRUE),
              identical(other_features(product), other_features(baseline)))
  }

  decimal <- build(replace_row("X2 Position (yesterday) = 73.123456 km"), "decimal-precision")
  stopifnot(decimal$summary$x2_position_km == 73.123456,
            x2_feature(decimal)[[1]]$properties$value_numeric == 73.123456,
            x2_feature(decimal)[[1]]$properties$label_text == "X2 9/7: 73.123456 km",
            identical(other_features(decimal), other_features(baseline)))

  invalid <- c("", "no X2 value", "X2 = km", "X2 = NA km", "X2 = 7x3 km", "X2 = 73.2.5 km",
               "X2 = -73 km", "X2 > 73", "X2 = 73 cfs", "X2 <> 73 km", "X2 == 73 km",
               "X2 = 73 km\nX2 = 74 km", "X2 Position (yesterday) unavailable")
  for (row in invalid) {
    parsed <- parser$pt_parse_x2(row)
    stopifnot(is.na(parsed$km), is.na(parsed$relation))
  }
  for (row in c("X2 Position (yesterday) = NA", "X2 Position (yesterday) > 7x3 km")) {
    product <- suppressWarnings(build(replace_row(row), paste0("invalid-", nchar(row))))
    # Existing bind_rows behavior adds nullable X2-date columns to other rows
    # only when an X2 feature exists. Compare the remaining properties exactly.
    expected_other <- lapply(other_features(baseline), function(f) {
      f$properties$x2_position_date <- f$properties$x2_position_date_source <- NULL
      f
    })
    stopifnot(is.null(product$summary$x2_position_km), is.null(product$summary$x2_position_relation),
              !product$summary$x2_lookup_added, length(x2_feature(product)) == 0L,
              length(product$reference) == 0L, identical(other_features(product), expected_other))
  }
  message("PASS: real source, seven relations, decimals/whitespace, missing/malformed values, unchanged other metrics, and publisher validation")
}

main()
