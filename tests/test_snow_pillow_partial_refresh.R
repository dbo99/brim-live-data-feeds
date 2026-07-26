#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(dplyr)
  library(jsonlite)
  library(readr)
  library(tibble)
})

source("scripts/snow_pillow_partial_refresh_helpers.R")

assert_true <- function(value, message) {
  if (!isTRUE(value)) {
    stop(message, call. = FALSE)
  }
}

assert_equal <- function(actual, expected, message) {
  if (!identical(actual, expected)) {
    stop(
      message,
      "\nExpected: ",
      paste(capture.output(str(expected)), collapse = " "),
      "\nActual: ",
      paste(capture.output(str(actual)), collapse = " "),
      call. = FALSE
    )
  }
}

assert_error <- function(code, pattern, message) {
  error <- tryCatch(
    {
      force(code)
      NULL
    },
    error = function(e) e
  )
  if (!inherits(error, "error") ||
      !grepl(pattern, conditionMessage(error), fixed = TRUE)) {
    stop(
      message,
      "\nExpected error containing: ",
      pattern,
      "\nActual: ",
      if (inherits(error, "error")) conditionMessage(error) else "no error",
      call. = FALSE
    )
  }
}

scenario <- function(name, code) {
  force(code)
  message("PASS: ", name)
}

test_root <- tempfile("snow-partial-refresh-tests-")
dir.create(test_root, recursive = TRUE)
on.exit(unlink(test_root, recursive = TRUE, force = TRUE), add = TRUE)

current_water_year <- 2026L
max_valid_swe_in <- 250

stations <- tibble::tribble(
  ~station_uid, ~live_provider_key, ~provider, ~provider_station_id,
  "CDEC_A", "cdec_snow_sensor", "CDEC", "A",
  "CDEC_B", "cdec_snow_sensor", "CDEC", "B",
  "NRCS_1", "nrcs_snotel", "NRCS", "1",
  "NRCS_2", "nrcs_snotel", "NRCS", "2"
)

make_latest <- function(provider_id, date, build_time, value_offset = 0) {
  source <- stations |>
    dplyr::filter(.data$live_provider_key == provider_id) |>
    dplyr::arrange(.data$station_uid)

  source |>
    dplyr::mutate(
      station_name = paste("Station", .data$station_uid),
      river_basin = NA_character_,
      latitude = ifelse(.data$live_provider_key == "cdec_snow_sensor", 39, 41),
      longitude = ifelse(.data$live_provider_key == "cdec_snow_sensor", -121, -120),
      latest_swe_in = seq_len(dplyr::n()) + value_offset,
      latest_swe_date_local = date,
      latest_swe_source_class = ifelse(
        .data$live_provider_key == "cdec_snow_sensor",
        "cdec_sno_adj_82",
        "nrcs_wteq"
      ),
      normal_fixed_pct_median_swe = NA_real_,
      normal_rolling_pct_median_swe = NA_real_,
      current_water_year = current_water_year,
      feed_build_time_local = build_time
    )
}

make_trace <- function(provider_id, start_date, value_offset = 0) {
  source <- stations |>
    dplyr::filter(.data$live_provider_key == provider_id) |>
    dplyr::arrange(.data$station_uid)

  dplyr::bind_rows(lapply(seq_len(nrow(source)), function(i) {
    dates <- as.Date(start_date) + 0:1
    tibble::tibble(
      station_uid = source$station_uid[[i]],
      live_provider_key = source$live_provider_key[[i]],
      provider = source$provider[[i]],
      provider_station_id = source$provider_station_id[[i]],
      station_name = paste("Station", source$station_uid[[i]]),
      water_year = current_water_year,
      water_day = as.integer(dates - as.Date("2025-10-01") + 1L),
      obs_date_local = as.character(dates),
      swe_in = c(1, 2) + value_offset + i,
      source_element = if (provider_id == "nrcs_snotel") "WTEQ" else "CDEC_SENSOR_82_SNO_ADJ",
      swe_source_class = if (provider_id == "nrcs_snotel") "nrcs_wteq" else "cdec_sno_adj_82",
      swe_source_label = if (provider_id == "nrcs_snotel") "NRCS WTEQ" else "CDEC SNO ADJ (#82)",
      swe_source_note = "fixture"
    )
  }))
}

prior_latest <- dplyr::bind_rows(
  make_latest("cdec_snow_sensor", "2025-10-02", "old-cdec-build"),
  make_latest("nrcs_snotel", "2025-10-02", "old-nrcs-build")
)
prior_trace <- dplyr::bind_rows(
  make_trace("cdec_snow_sensor", "2025-10-01"),
  make_trace("nrcs_snotel", "2025-10-01")
)
fresh_cdec_latest <- make_latest(
  "cdec_snow_sensor",
  "2025-10-04",
  "fresh-cdec-build",
  value_offset = 10
)
fresh_nrcs_latest <- make_latest(
  "nrcs_snotel",
  "2025-10-04",
  "fresh-nrcs-build",
  value_offset = 20
)
fresh_cdec_trace <- make_trace("cdec_snow_sensor", "2025-10-03", value_offset = 10)
fresh_nrcs_trace <- make_trace("nrcs_snotel", "2025-10-03", value_offset = 20)

required_latest_columns <- names(prior_latest)
required_trace_columns <- names(prior_trace)

make_result <- function(provider_id,
                        success,
                        latest_rows,
                        trace_rows,
                        failure_reason = NA_character_) {
  snow_provider_result(
    provider_id = provider_id,
    success = success,
    fetch_status = if (success) "success" else "failed",
    qa_status = if (success) "passed" else "failed",
    latest_rows = latest_rows,
    trace_rows = trace_rows,
    qa = list(min_sites = 1L),
    failure_reason = failure_reason,
    fetch_started_at_utc = "2025-10-05T00:00:00Z",
    fetch_completed_at_utc = "2025-10-05T00:01:00Z"
  )
}

make_prior_outputs <- function(valid = TRUE) {
  list(
    valid = valid,
    problems = if (valid) character() else "invalid prior fixture",
    latest = prior_latest,
    trace = prior_trace,
    latest_summary = list(),
    trace_summary = list()
  )
}

resolve <- function(results, prior = NULL) {
  snow_resolve_publication(
    provider_results = results,
    prior_outputs = prior,
    stations = stations,
    current_water_year = current_water_year,
    max_valid_swe_in = max_valid_swe_in,
    min_trace_rows = 1L,
    min_latest_swe_rows = 1L,
    required_latest_columns = required_latest_columns,
    required_trace_columns = required_trace_columns
  )
}

coverage_defaults <- snow_provider_completeness_defaults()
coverage_fetch_start <- as.Date("2025-10-01")
coverage_fetch_end <- as.Date("2025-10-10")
coverage_expected_days <- as.integer(coverage_fetch_end - coverage_fetch_start + 1L)

make_provider_observations <- function(provider_id,
                                       indexed_station_count,
                                       observed_station_count,
                                       row_count) {
  prefix <- if (provider_id == "nrcs_snotel") "NRCS" else "CDEC"
  expected_station_uids <- sprintf(
    "%s_%03d",
    prefix,
    seq_len(indexed_station_count)
  )
  observed_station_uids <- expected_station_uids[seq_len(observed_station_count)]
  grid <- expand.grid(
    station_uid = observed_station_uids,
    obs_date = seq(coverage_fetch_start, coverage_fetch_end, by = "day"),
    KEEP.OUT.ATTRS = FALSE,
    stringsAsFactors = FALSE
  )
  if (row_count > nrow(grid)) {
    stop("Synthetic provider fixture requests more rows than unique station-days.")
  }
  grid <- tibble::as_tibble(grid[seq_len(row_count), , drop = FALSE])

  observations <- grid |>
    dplyr::mutate(
      provider = if (provider_id == "nrcs_snotel") "NRCS" else "CDEC",
      provider_station_id = sub("^.*_", "", .data$station_uid),
      swe_in = 10,
      source_element = if (provider_id == "nrcs_snotel") {
        "WTEQ"
      } else {
        "CDEC_SENSOR_82_SNO_ADJ"
      },
      swe_source_class = if (provider_id == "nrcs_snotel") {
        "nrcs_wteq"
      } else {
        "cdec_sno_adj_82"
      },
      swe_source_label = if (provider_id == "nrcs_snotel") {
        "NRCS WTEQ"
      } else {
        "CDEC SNO ADJ (#82)"
      },
      swe_source_note = "deterministic completeness fixture"
    )

  list(
    observations = observations,
    expected_station_uids = expected_station_uids
  )
}

validate_provider_fixture <- function(provider_id,
                                      indexed_station_count,
                                      observed_station_count,
                                      row_count) {
  policy <- coverage_defaults[[provider_id]]
  minimums <- snow_provider_completeness_minimums(
    provider_id = provider_id,
    indexed_station_count = indexed_station_count,
    expected_fetch_days = coverage_expected_days,
    station_fraction = policy$station_fraction,
    row_fraction = policy$row_fraction
  )
  fixture <- make_provider_observations(
    provider_id = provider_id,
    indexed_station_count = indexed_station_count,
    observed_station_count = observed_station_count,
    row_count = row_count
  )
  qa <- snow_validate_provider_observations(
    observations = fixture$observations,
    provider_id = provider_id,
    expected_station_uids = fixture$expected_station_uids,
    min_rows = minimums$min_rows,
    min_sites = minimums$min_sites,
    fetch_start_date = coverage_fetch_start,
    fetch_end_date = coverage_fetch_end,
    max_valid_swe_in = max_valid_swe_in
  )

  list(qa = qa, minimums = minimums)
}

healthy_nrcs_coverage <- validate_provider_fixture(
  provider_id = "nrcs_snotel",
  indexed_station_count = 175L,
  observed_station_count = 164L,
  row_count = 1630L
)
healthy_cdec_coverage <- validate_provider_fixture(
  provider_id = "cdec_snow_sensor",
  indexed_station_count = 129L,
  observed_station_count = 123L,
  row_count = 1162L
)
truncated_nrcs_coverage <- validate_provider_fixture(
  provider_id = "nrcs_snotel",
  indexed_station_count = 175L,
  observed_station_count = 35L,
  row_count = 350L
)
truncated_nrcs_rows_only <- validate_provider_fixture(
  provider_id = "nrcs_snotel",
  indexed_station_count = 175L,
  observed_station_count = 175L,
  row_count = 350L
)
truncated_cdec_coverage <- validate_provider_fixture(
  provider_id = "cdec_snow_sensor",
  indexed_station_count = 129L,
  observed_station_count = 26L,
  row_count = 258L
)
truncated_cdec_rows_only <- validate_provider_fixture(
  provider_id = "cdec_snow_sensor",
  indexed_station_count = 129L,
  observed_station_count = 129L,
  row_count = 258L
)

make_coverage_result <- function(provider_id, coverage, latest_rows, trace_rows) {
  passed <- isTRUE(coverage$qa$passed)
  snow_provider_result(
    provider_id = provider_id,
    success = passed,
    fetch_status = "success",
    qa_status = if (passed) "passed" else "failed",
    latest_rows = if (passed) latest_rows else tibble::tibble(),
    trace_rows = if (passed) trace_rows else tibble::tibble(),
    qa = list(
      min_sites = 1L,
      min_rows = 1L,
      completeness = coverage$qa
    ),
    failure_reason = if (passed) {
      NA_character_
    } else {
      paste(coverage$qa$problems, collapse = "; ")
    },
    fetch_started_at_utc = "2025-10-05T00:00:00Z",
    fetch_completed_at_utc = "2025-10-05T00:01:00Z"
  )
}

scenario("Provider completeness defaults match conservative healthy-range gates", {
  actual_nrcs <- snow_provider_completeness_minimums(
    "nrcs_snotel",
    indexed_station_count = 175L,
    expected_fetch_days = 298L,
    station_fraction = coverage_defaults$nrcs_snotel$station_fraction,
    row_fraction = coverage_defaults$nrcs_snotel$row_fraction
  )
  actual_cdec <- snow_provider_completeness_minimums(
    "cdec_snow_sensor",
    indexed_station_count = 129L,
    expected_fetch_days = 298L,
    station_fraction = coverage_defaults$cdec_snow_sensor$station_fraction,
    row_fraction = coverage_defaults$cdec_snow_sensor$row_fraction
  )

  assert_equal(actual_nrcs$min_sites, 158L, "NRCS station minimum changed.")
  assert_equal(actual_nrcs$min_rows, 46935L, "NRCS row minimum changed.")
  assert_equal(actual_cdec$min_sites, 119L, "CDEC station minimum changed.")
  assert_equal(actual_cdec$min_rows, 32676L, "CDEC row minimum changed.")
})

scenario("Representative healthy NRCS coverage passes", {
  assert_true(
    healthy_nrcs_coverage$qa$passed,
    paste(healthy_nrcs_coverage$qa$problems, collapse = "; ")
  )
  assert_equal(
    healthy_nrcs_coverage$qa$site_count,
    164L,
    "Healthy NRCS observed station count changed."
  )
})

scenario("Representative healthy CDEC coverage passes", {
  assert_true(
    healthy_cdec_coverage$qa$passed,
    paste(healthy_cdec_coverage$qa$problems, collapse = "; ")
  )
  assert_equal(
    healthy_cdec_coverage$qa$site_count,
    123L,
    "Healthy CDEC observed station count changed."
  )
})

scenario("Approximately 20 percent NRCS coverage fails", {
  assert_true(
    !truncated_nrcs_coverage$qa$passed,
    "A 20 percent NRCS response must fail provider QA."
  )
  assert_true(
    all(c("rows too low", "sites too low") %in%
      sub(":.*$", "", truncated_nrcs_coverage$qa$problems)),
    "NRCS truncation should fail both row and station gates."
  )
  assert_true(
    !truncated_nrcs_rows_only$qa$passed &&
      truncated_nrcs_rows_only$qa$site_count == 175L &&
      any(startsWith(truncated_nrcs_rows_only$qa$problems, "rows too low")),
    "A 20 percent NRCS row response must fail even with all stations represented."
  )
})

scenario("Approximately 20 percent CDEC coverage fails", {
  assert_true(
    !truncated_cdec_coverage$qa$passed,
    "A 20 percent CDEC response must fail provider QA."
  )
  assert_true(
    all(c("rows too low", "sites too low") %in%
      sub(":.*$", "", truncated_cdec_coverage$qa$problems)),
    "CDEC truncation should fail both row and station gates."
  )
  assert_true(
    !truncated_cdec_rows_only$qa$passed &&
      truncated_cdec_rows_only$qa$site_count == 129L &&
      any(startsWith(truncated_cdec_rows_only$qa$problems, "rows too low")),
    "A 20 percent CDEC row response must fail even with all stations represented."
  )
})

scenario("Environment overrides are strict and cannot weaken provider gates", {
  assert_equal(
    snow_env_fraction("TEST_FRACTION", 0.90, raw_value = "0.95"),
    0.95,
    "A stricter fraction override should be accepted."
  )
  assert_equal(
    snow_env_integer(
      "TEST_SITES",
      default = 158L,
      minimum_allowed = 158L,
      maximum_allowed = 175L,
      raw_value = "160"
    ),
    160L,
    "A stricter feasible station override should be accepted."
  )

  for (value in c("0", "-0.1", "1.01", "not-a-number", "Inf")) {
    assert_error(
      snow_env_fraction("TEST_FRACTION", 0.90, raw_value = value),
      "must be a finite fraction",
      paste("Invalid fraction override was accepted:", value)
    )
  }
  assert_error(
    snow_env_fraction("TEST_FRACTION", 0.90, raw_value = "0.89"),
    "may not weaken completeness protection",
    "A fraction below the default gate was accepted."
  )
  for (value in c("0", "-1", "10.5", "not-a-number")) {
    assert_error(
      snow_env_integer(
        "TEST_SITES",
        default = 158L,
        minimum_allowed = 158L,
        maximum_allowed = 175L,
        raw_value = value
      ),
      "must be a positive whole number",
      paste("Invalid integer override was accepted:", value)
    )
  }
  assert_error(
    snow_env_integer(
      "TEST_SITES",
      default = 158L,
      minimum_allowed = 158L,
      maximum_allowed = 175L,
      raw_value = "157"
    ),
    "may not weaken completeness protection",
    "A station minimum below the derived gate was accepted."
  )
  assert_error(
    snow_env_integer(
      "TEST_SITES",
      default = 158L,
      minimum_allowed = 158L,
      maximum_allowed = 175L,
      raw_value = "176"
    ),
    "is impossible for the indexed stations and fetch window",
    "An impossible station minimum was accepted."
  )
})

scenario("AWDB succeeds and CDEC succeeds: full refresh", {
  results <- list(
    nrcs_snotel = make_result(
      "nrcs_snotel",
      TRUE,
      fresh_nrcs_latest,
      fresh_nrcs_trace
    ),
    cdec_snow_sensor = make_result(
      "cdec_snow_sensor",
      TRUE,
      fresh_cdec_latest,
      fresh_cdec_trace
    )
  )
  resolved <- resolve(results)

  assert_true(resolved$publish, "Full refresh should be publishable.")
  assert_equal(resolved$decision$mode, "full", "Expected full refresh mode.")
  assert_true(
    all(unname(resolved$decision$actions) == "refreshed"),
    "Both providers should refresh."
  )
  assert_equal(nrow(resolved$latest), 4L, "Full refresh should construct latest rows.")
  assert_equal(nrow(resolved$trace), 8L, "Full refresh should construct trace rows.")
})

scenario("AWDB fails and CDEC succeeds: AWDB carried forward", {
  results <- list(
    nrcs_snotel = make_result(
      "nrcs_snotel",
      FALSE,
      tibble::tibble(),
      tibble::tibble(),
      "AWDB preflight timed out"
    ),
    cdec_snow_sensor = make_result(
      "cdec_snow_sensor",
      TRUE,
      fresh_cdec_latest,
      fresh_cdec_trace
    )
  )
  resolved <- resolve(results, make_prior_outputs())
  metadata <- snow_build_refresh_metadata(
    results,
    resolved,
    "2025-10-05T00:02:00Z"
  )

  carried_latest <- resolved$latest |>
    dplyr::filter(.data$live_provider_key == "nrcs_snotel") |>
    dplyr::arrange(.data$station_uid)
  expected_latest <- prior_latest |>
    dplyr::filter(.data$live_provider_key == "nrcs_snotel") |>
    dplyr::arrange(.data$station_uid)
  carried_trace <- resolved$trace |>
    dplyr::filter(.data$live_provider_key == "nrcs_snotel") |>
    dplyr::arrange(.data$station_uid, .data$obs_date_local)
  expected_trace <- prior_trace |>
    dplyr::filter(.data$live_provider_key == "nrcs_snotel") |>
    dplyr::arrange(.data$station_uid, .data$obs_date_local)

  assert_true(resolved$publish, "Partial AWDB failure should be publishable.")
  assert_equal(resolved$decision$mode, "partial", "Expected partial refresh mode.")
  assert_equal(
    resolved$decision$actions[["nrcs_snotel"]],
    "carried_forward",
    "AWDB should be carried forward."
  )
  assert_equal(
    carried_latest,
    expected_latest,
    "Every AWDB latest field, including timestamps, must remain unchanged."
  )
  assert_equal(
    carried_trace,
    expected_trace,
    "Every AWDB trace field and observation date must remain unchanged."
  )
  assert_equal(metadata$mode, "partial", "Refresh metadata should report partial.")
  assert_true(
    metadata$providers$nrcs_snotel$last_successful_data_preserved,
    "Metadata should report preserved AWDB data."
  )
  assert_equal(
    metadata$providers$nrcs_snotel$publication_action,
    "carried_forward",
    "AWDB metadata action is incorrect."
  )
  assert_equal(
    metadata$providers$nrcs_snotel$carried_forward_latest_rows,
    2L,
    "AWDB carried-forward latest count is incorrect."
  )
  assert_equal(
    metadata$providers$nrcs_snotel$carried_forward_trace_rows,
    4L,
    "AWDB carried-forward trace count is incorrect."
  )
  assert_equal(
    metadata$providers$cdec_snow_sensor$publication_action,
    "refreshed",
    "CDEC metadata action is incorrect."
  )
})

scenario("CDEC fails and AWDB succeeds: CDEC carried forward", {
  results <- list(
    nrcs_snotel = make_result(
      "nrcs_snotel",
      TRUE,
      fresh_nrcs_latest,
      fresh_nrcs_trace
    ),
    cdec_snow_sensor = make_result(
      "cdec_snow_sensor",
      FALSE,
      tibble::tibble(),
      tibble::tibble(),
      "CDEC responses were malformed"
    )
  )
  resolved <- resolve(results, make_prior_outputs())
  metadata <- snow_build_refresh_metadata(
    results,
    resolved,
    "2025-10-05T00:02:00Z"
  )

  carried_latest <- resolved$latest |>
    dplyr::filter(.data$live_provider_key == "cdec_snow_sensor") |>
    dplyr::arrange(.data$station_uid)
  expected_latest <- prior_latest |>
    dplyr::filter(.data$live_provider_key == "cdec_snow_sensor") |>
    dplyr::arrange(.data$station_uid)
  carried_trace <- resolved$trace |>
    dplyr::filter(.data$live_provider_key == "cdec_snow_sensor") |>
    dplyr::arrange(.data$station_uid, .data$obs_date_local)
  expected_trace <- prior_trace |>
    dplyr::filter(.data$live_provider_key == "cdec_snow_sensor") |>
    dplyr::arrange(.data$station_uid, .data$obs_date_local)

  assert_true(resolved$publish, "Partial CDEC failure should be publishable.")
  assert_equal(
    carried_latest,
    expected_latest,
    "Every CDEC latest field, including timestamps, must remain unchanged."
  )
  assert_equal(
    carried_trace,
    expected_trace,
    "Every CDEC trace field and observation date must remain unchanged."
  )
  assert_true(
    metadata$providers$cdec_snow_sensor$last_successful_data_preserved,
    "Metadata should report preserved CDEC data."
  )
  assert_equal(
    metadata$providers$cdec_snow_sensor$carried_forward_latest_rows,
    2L,
    "CDEC carried-forward latest count is incorrect."
  )
  assert_equal(
    metadata$providers$cdec_snow_sensor$carried_forward_trace_rows,
    4L,
    "CDEC carried-forward trace count is incorrect."
  )
})

scenario("Both providers fail: publication refused and files unchanged", {
  output_dir <- file.path(test_root, "both-fail")
  dir.create(output_dir)
  outputs <- file.path(output_dir, c("latest.geojson", "latest.json", "trace.csv", "trace.json"))
  for (i in seq_along(outputs)) {
    writeLines(c("one", "two", "three", "four")[[i]], outputs[[i]])
  }
  before <- unname(tools::md5sum(outputs))

  results <- list(
    nrcs_snotel = make_result(
      "nrcs_snotel",
      FALSE,
      tibble::tibble(),
      tibble::tibble(),
      "AWDB unavailable"
    ),
    cdec_snow_sensor = make_result(
      "cdec_snow_sensor",
      FALSE,
      tibble::tibble(),
      tibble::tibble(),
      "CDEC unavailable"
    )
  )
  resolved <- resolve(results)
  after <- unname(tools::md5sum(outputs))

  assert_true(!resolved$publish, "Both-provider failure must refuse publication.")
  assert_equal(before, after, "Both-provider failure must leave all files unchanged.")
})

scenario("Failed provider without valid prior rows: publication refused", {
  output_dir <- file.path(test_root, "invalid-prior")
  dir.create(output_dir)
  outputs <- file.path(output_dir, c("latest.geojson", "latest.json", "trace.csv", "trace.json"))
  for (i in seq_along(outputs)) {
    writeLines(paste0("prior-", i), outputs[[i]])
  }
  before <- unname(tools::md5sum(outputs))

  results <- list(
    nrcs_snotel = make_result(
      "nrcs_snotel",
      FALSE,
      tibble::tibble(),
      tibble::tibble(),
      "AWDB unavailable"
    ),
    cdec_snow_sensor = make_result(
      "cdec_snow_sensor",
      TRUE,
      fresh_cdec_latest,
      fresh_cdec_trace
    )
  )
  missing_prior <- resolve(results, NULL)
  invalid_prior <- resolve(results, make_prior_outputs(valid = FALSE))
  after <- unname(tools::md5sum(outputs))

  assert_true(!missing_prior$publish, "Missing prior outputs must refuse partial publication.")
  assert_true(!invalid_prior$publish, "Invalid prior outputs must refuse partial publication.")
  assert_equal(before, after, "Missing/invalid prior refusal changed official fixture bytes.")
})

scenario("Malformed or implausibly incomplete provider data fails QA", {
  malformed <- tibble::tibble(station_uid = "NRCS_1")
  malformed_qa <- snow_validate_provider_observations(
    malformed,
    "nrcs_snotel",
    stations$station_uid[stations$live_provider_key == "nrcs_snotel"],
    min_rows = 2L,
    min_sites = 2L,
    fetch_start_date = "2025-10-01",
    fetch_end_date = "2025-10-05",
    max_valid_swe_in = max_valid_swe_in
  )
  incomplete <- tibble::tibble(
    station_uid = "NRCS_1",
    provider = "NRCS",
    provider_station_id = "1",
    obs_date = as.Date("2025-10-01"),
    swe_in = 1,
    source_element = "WTEQ",
    swe_source_class = "nrcs_wteq",
    swe_source_label = "NRCS WTEQ",
    swe_source_note = "fixture"
  )
  incomplete_qa <- snow_validate_provider_observations(
    incomplete,
    "nrcs_snotel",
    stations$station_uid[stations$live_provider_key == "nrcs_snotel"],
    min_rows = 2L,
    min_sites = 2L,
    fetch_start_date = "2025-10-01",
    fetch_end_date = "2025-10-05",
    max_valid_swe_in = max_valid_swe_in
  )

  assert_true(!malformed_qa$passed, "Malformed provider rows must fail QA.")
  assert_true(!incomplete_qa$passed, "Implausibly incomplete provider rows must fail QA.")
})

scenario("Truncated NRCS plus healthy CDEC performs a partial refresh", {
  results <- list(
    nrcs_snotel = make_coverage_result(
      "nrcs_snotel",
      truncated_nrcs_coverage,
      fresh_nrcs_latest,
      fresh_nrcs_trace
    ),
    cdec_snow_sensor = make_coverage_result(
      "cdec_snow_sensor",
      healthy_cdec_coverage,
      fresh_cdec_latest,
      fresh_cdec_trace
    )
  )
  resolved <- resolve(results, make_prior_outputs())
  metadata <- snow_build_refresh_metadata(results, resolved, "2025-10-05T00:02:00Z")
  carried_latest <- resolved$latest |>
    dplyr::filter(.data$live_provider_key == "nrcs_snotel") |>
    dplyr::arrange(.data$station_uid)
  expected_latest <- prior_latest |>
    dplyr::filter(.data$live_provider_key == "nrcs_snotel") |>
    dplyr::arrange(.data$station_uid)
  carried_trace <- resolved$trace |>
    dplyr::filter(.data$live_provider_key == "nrcs_snotel") |>
    dplyr::arrange(.data$station_uid, .data$obs_date_local)
  expected_trace <- prior_trace |>
    dplyr::filter(.data$live_provider_key == "nrcs_snotel") |>
    dplyr::arrange(.data$station_uid, .data$obs_date_local)

  assert_true(resolved$publish, "Healthy CDEC should permit a partial refresh.")
  assert_equal(resolved$decision$mode, "partial", "Expected partial mode.")
  assert_equal(
    metadata$providers$nrcs_snotel$publication_action,
    "carried_forward",
    "Incomplete NRCS was incorrectly labeled refreshed."
  )
  assert_equal(
    metadata$providers$nrcs_snotel$qa_status,
    "failed",
    "Incomplete NRCS was incorrectly labeled as passing QA."
  )
  assert_equal(
    carried_latest,
    expected_latest,
    "Carried NRCS values or original latest timestamps changed."
  )
  assert_equal(
    carried_trace,
    expected_trace,
    "Carried NRCS values or observation timestamps changed."
  )
})

scenario("Truncated CDEC plus healthy NRCS performs a partial refresh", {
  results <- list(
    nrcs_snotel = make_coverage_result(
      "nrcs_snotel",
      healthy_nrcs_coverage,
      fresh_nrcs_latest,
      fresh_nrcs_trace
    ),
    cdec_snow_sensor = make_coverage_result(
      "cdec_snow_sensor",
      truncated_cdec_coverage,
      fresh_cdec_latest,
      fresh_cdec_trace
    )
  )
  resolved <- resolve(results, make_prior_outputs())
  metadata <- snow_build_refresh_metadata(results, resolved, "2025-10-05T00:02:00Z")
  carried_latest <- resolved$latest |>
    dplyr::filter(.data$live_provider_key == "cdec_snow_sensor") |>
    dplyr::arrange(.data$station_uid)
  expected_latest <- prior_latest |>
    dplyr::filter(.data$live_provider_key == "cdec_snow_sensor") |>
    dplyr::arrange(.data$station_uid)
  carried_trace <- resolved$trace |>
    dplyr::filter(.data$live_provider_key == "cdec_snow_sensor") |>
    dplyr::arrange(.data$station_uid, .data$obs_date_local)
  expected_trace <- prior_trace |>
    dplyr::filter(.data$live_provider_key == "cdec_snow_sensor") |>
    dplyr::arrange(.data$station_uid, .data$obs_date_local)

  assert_true(resolved$publish, "Healthy NRCS should permit a partial refresh.")
  assert_equal(resolved$decision$mode, "partial", "Expected partial mode.")
  assert_equal(
    metadata$providers$cdec_snow_sensor$publication_action,
    "carried_forward",
    "Incomplete CDEC was incorrectly labeled refreshed."
  )
  assert_equal(
    metadata$providers$cdec_snow_sensor$qa_status,
    "failed",
    "Incomplete CDEC was incorrectly labeled as passing QA."
  )
  assert_equal(
    carried_latest,
    expected_latest,
    "Carried CDEC values or original latest timestamps changed."
  )
  assert_equal(
    carried_trace,
    expected_trace,
    "Carried CDEC values or observation timestamps changed."
  )
})

scenario("Both truncated providers cause refusal and outputs remain unchanged", {
  output_dir <- file.path(test_root, "both-truncated")
  dir.create(output_dir)
  outputs <- file.path(output_dir, c("latest.geojson", "latest.json", "trace.csv", "trace.json"))
  for (i in seq_along(outputs)) {
    writeLines(paste0("prior-", i), outputs[[i]])
  }
  before <- unname(tools::md5sum(outputs))
  results <- list(
    nrcs_snotel = make_coverage_result(
      "nrcs_snotel",
      truncated_nrcs_coverage,
      fresh_nrcs_latest,
      fresh_nrcs_trace
    ),
    cdec_snow_sensor = make_coverage_result(
      "cdec_snow_sensor",
      truncated_cdec_coverage,
      fresh_cdec_latest,
      fresh_cdec_trace
    )
  )
  resolved <- resolve(results)
  after <- unname(tools::md5sum(outputs))

  assert_true(!resolved$publish, "Two truncated providers must refuse publication.")
  assert_equal(before, after, "Two truncated providers changed existing output bytes.")
})

scenario("Duplicate merge protection rejects provider/station/day collisions", {
  duplicate_trace <- dplyr::bind_rows(fresh_nrcs_trace, fresh_nrcs_trace[1, ])
  results <- list(
    nrcs_snotel = make_result(
      "nrcs_snotel",
      TRUE,
      fresh_nrcs_latest,
      duplicate_trace
    ),
    cdec_snow_sensor = make_result(
      "cdec_snow_sensor",
      TRUE,
      fresh_cdec_latest,
      fresh_cdec_trace
    )
  )
  resolved <- resolve(results)

  assert_true(!resolved$publish, "Duplicate trace keys must refuse publication.")
  assert_true(
    any(grepl("duplicate", resolved$combined_qa$problems)),
    "Duplicate refusal should be visible in combined QA."
  )
})

scenario("Refresh metadata is additive and existing summary meanings remain", {
  existing_summary <- list(
    layer = "Snow pillows / SWE latest",
    build_time_local = "old-build",
    latest_geojson_rows = 4L,
    data_notes = c("existing note")
  )
  results <- list(
    nrcs_snotel = make_result(
      "nrcs_snotel",
      TRUE,
      fresh_nrcs_latest,
      fresh_nrcs_trace
    ),
    cdec_snow_sensor = make_result(
      "cdec_snow_sensor",
      TRUE,
      fresh_cdec_latest,
      fresh_cdec_trace
    )
  )
  resolved <- resolve(results)
  extended_summary <- existing_summary
  extended_summary$refresh <- snow_build_refresh_metadata(
    results,
    resolved,
    "2025-10-05T00:02:00Z"
  )
  path <- file.path(test_root, "metadata.json")
  jsonlite::write_json(extended_summary, path, auto_unbox = TRUE, pretty = TRUE)
  parsed <- jsonlite::fromJSON(path, simplifyVector = FALSE)

  assert_equal(parsed$layer, existing_summary$layer, "Existing layer field changed.")
  assert_equal(
    parsed$build_time_local,
    existing_summary$build_time_local,
    "Existing build-time meaning changed."
  )
  assert_equal(
    as.integer(parsed$latest_geojson_rows),
    existing_summary$latest_geojson_rows,
    "Existing row count changed."
  )
  assert_true(is.list(parsed$refresh), "Additive refresh metadata did not parse.")
})

scenario("Stable output ordering is deterministic", {
  results <- list(
    nrcs_snotel = make_result(
      "nrcs_snotel",
      TRUE,
      fresh_nrcs_latest[2:1, ],
      fresh_nrcs_trace[nrow(fresh_nrcs_trace):1, ]
    ),
    cdec_snow_sensor = make_result(
      "cdec_snow_sensor",
      TRUE,
      fresh_cdec_latest[2:1, ],
      fresh_cdec_trace[nrow(fresh_cdec_trace):1, ]
    )
  )
  first <- resolve(results)
  second <- resolve(results)

  assert_equal(first$latest, second$latest, "Latest ordering is not deterministic.")
  assert_equal(first$trace, second$trace, "Trace ordering is not deterministic.")
  assert_true(
    identical(first$latest$live_provider_key, sort(first$latest$live_provider_key)),
    "Latest rows are not ordered by provider."
  )
})

scenario("Staged validation failure preserves all official fixture bytes", {
  output_dir <- file.path(test_root, "stage-failure")
  dir.create(output_dir)
  final_paths <- c(
    latest_geojson = file.path(output_dir, "latest.geojson"),
    latest_summary = file.path(output_dir, "latest.json"),
    trace_csv = file.path(output_dir, "trace.csv"),
    trace_summary = file.path(output_dir, "trace.json")
  )
  for (name in names(final_paths)) {
    writeLines(paste0("official-", name), final_paths[[name]])
  }
  before <- unname(tools::md5sum(final_paths))

  error <- tryCatch(
    {
      snow_stage_and_promote_outputs(
        final_paths = final_paths,
        write_staged = function(staged_paths) {
          for (name in names(staged_paths)) {
            writeLines(paste0("prospective-", name), staged_paths[[name]])
          }
        },
        validate_staged = function(staged_paths) {
          list(valid = FALSE, problems = "simulated staged validation failure")
        }
      )
      NULL
    },
    error = function(e) e
  )
  after <- unname(tools::md5sum(final_paths))

  assert_true(inherits(error, "error"), "Simulated staged validation failure did not fail.")
  assert_equal(before, after, "Staged validation failure changed official fixture bytes.")
})

scenario("All four staged outputs are promoted only after validation", {
  output_dir <- file.path(test_root, "stage-success")
  dir.create(output_dir)
  final_paths <- c(
    latest_geojson = file.path(output_dir, "latest.geojson"),
    latest_summary = file.path(output_dir, "latest.json"),
    trace_csv = file.path(output_dir, "trace.csv"),
    trace_summary = file.path(output_dir, "trace.json")
  )
  for (name in names(final_paths)) {
    writeLines(paste0("old-", name), final_paths[[name]])
  }

  snow_stage_and_promote_outputs(
    final_paths = final_paths,
    write_staged = function(staged_paths) {
      for (name in names(staged_paths)) {
        writeLines(paste0("new-", name), staged_paths[[name]])
      }
    },
    validate_staged = function(staged_paths) {
      list(valid = all(file.exists(staged_paths)), problems = character())
    }
  )

  contents <- vapply(final_paths, readLines, character(1))
  assert_true(
    all(startsWith(contents, "new-")),
    "The complete staged output set was not promoted."
  )
})

write_fixture_geojson <- function(rows, path) {
  features <- lapply(seq_len(nrow(rows)), function(i) {
    row <- as.list(rows[i, , drop = FALSE])
    row <- lapply(row, function(x) if (length(x) == 1L && is.na(x)) NULL else x)
    list(
      type = "Feature",
      geometry = list(
        type = "Point",
        coordinates = c(rows$longitude[[i]], rows$latitude[[i]])
      ),
      properties = row
    )
  })
  jsonlite::write_json(
    list(type = "FeatureCollection", features = features),
    path,
    auto_unbox = TRUE,
    na = "null",
    null = "null"
  )
}

write_fixture_output_set <- function(paths, latest_rows, trace_rows) {
  write_fixture_geojson(latest_rows, paths[["latest_geojson"]])
  readr::write_csv(trace_rows, paths[["trace_csv"]])
  jsonlite::write_json(
    list(
      layer = "Snow pillows / SWE latest",
      build_time_local = "fixture-build",
      build_date_local = "2025-10-05",
      current_water_year = current_water_year,
      latest_geojson_rows = nrow(latest_rows),
      current_wy_trace_rows = nrow(trace_rows)
    ),
    paths[["latest_summary"]],
    auto_unbox = TRUE,
    na = "null",
    null = "null"
  )
  jsonlite::write_json(
    list(
      layer = "Snow pillows / SWE current water-year trace",
      build_time_local = "fixture-build",
      build_date_local = "2025-10-05",
      current_water_year = current_water_year,
      current_wy_trace_rows = nrow(trace_rows),
      stations_with_trace_rows = dplyr::n_distinct(trace_rows$station_uid),
      providers = sort(unique(trace_rows$live_provider_key))
    ),
    paths[["trace_summary"]],
    auto_unbox = TRUE,
    na = "null",
    null = "null"
  )
}

scenario("All-null serialized properties retain keys and prototype types", {
  path <- file.path(test_root, "all-null-properties.geojson")
  write_fixture_geojson(prior_latest, path)

  raw <- jsonlite::fromJSON(path, simplifyVector = FALSE)
  raw_properties <- lapply(raw$features, function(feature) feature$properties)
  old_bound <- dplyr::bind_rows(raw_properties)
  all_null_fields <- c(
    "river_basin",
    "normal_fixed_pct_median_swe",
    "normal_rolling_pct_median_swe"
  )

  assert_true(
    all(vapply(
      raw_properties,
      function(properties) all(all_null_fields %in% names(properties)),
      logical(1)
    )),
    "The fixture writer omitted a required all-null property key."
  )
  assert_true(
    !any(all_null_fields %in% names(old_bound)),
    "The deterministic old parse/bind reproduction did not lose all-null columns."
  )

  parsed <- snow_read_geojson_properties(
    path,
    required_property_names = names(prior_latest),
    property_prototype = prior_latest,
    label = "Fixture latest GeoJSON"
  )

  assert_true(
    all(all_null_fields %in% names(parsed)),
    "The corrected parser did not preserve all-null property columns."
  )
  assert_true(
    is.character(parsed$river_basin) && all(is.na(parsed$river_basin)),
    "The all-null character property did not retain character-compatible typing."
  )
  assert_true(
    is.double(parsed$normal_fixed_pct_median_swe) &&
      all(is.na(parsed$normal_fixed_pct_median_swe)) &&
      is.double(parsed$normal_rolling_pct_median_swe) &&
      all(is.na(parsed$normal_rolling_pct_median_swe)),
    "The all-null percentage properties did not retain numeric-compatible typing."
  )
  assert_equal(
    names(parsed)[seq_along(names(prior_latest))],
    names(prior_latest),
    "Expected latest-property column order was not preserved."
  )
})

scenario("Healthy full refresh with all-null properties validates and promotes", {
  results <- list(
    nrcs_snotel = make_result(
      "nrcs_snotel",
      TRUE,
      fresh_nrcs_latest,
      fresh_nrcs_trace
    ),
    cdec_snow_sensor = make_result(
      "cdec_snow_sensor",
      TRUE,
      fresh_cdec_latest,
      fresh_cdec_trace
    )
  )
  resolved <- resolve(results)
  assert_true(resolved$publish, "Healthy providers should produce a publishable refresh.")
  assert_equal(resolved$decision$mode, "full", "Healthy providers should select full mode.")
  assert_true(
    all(is.na(resolved$latest$river_basin)) &&
      all(is.na(resolved$latest$normal_fixed_pct_median_swe)) &&
      all(is.na(resolved$latest$normal_rolling_pct_median_swe)),
    "Full-refresh fixture does not exercise the all-null property condition."
  )

  output_dir <- file.path(test_root, "all-null-stage-success")
  dir.create(output_dir)
  final_paths <- c(
    latest_geojson = file.path(output_dir, "latest.geojson"),
    latest_summary = file.path(output_dir, "latest.json"),
    trace_csv = file.path(output_dir, "trace.csv"),
    trace_summary = file.path(output_dir, "trace.json")
  )
  for (name in names(final_paths)) {
    writeLines(paste0("official-", name), final_paths[[name]])
  }
  before <- unname(tools::md5sum(final_paths))

  promoted <- snow_stage_and_promote_outputs(
    final_paths = final_paths,
    write_staged = function(staged_paths) {
      write_fixture_output_set(staged_paths, resolved$latest, resolved$trace)
    },
    validate_staged = function(staged_paths) {
      snow_validate_staged_outputs(
        paths = staged_paths,
        stations = stations,
        current_water_year = current_water_year,
        max_valid_swe_in = max_valid_swe_in,
        min_trace_rows = 1L,
        min_latest_swe_rows = 1L,
        required_latest_columns = required_latest_columns,
        required_trace_columns = required_trace_columns,
        latest_prototype = resolved$latest
      )
    }
  )
  after <- unname(tools::md5sum(final_paths))

  assert_true(
    !identical(before, after),
    "A valid full-refresh fixture was not promoted."
  )
  assert_true(
    isTRUE(promoted$validation$valid) &&
      isTRUE(promoted$validation$combined_qa$passed),
    "The promoted full-refresh fixture did not pass serialized combined QA."
  )
  assert_true(
    is.character(promoted$validation$latest$river_basin) &&
      is.double(promoted$validation$latest$normal_fixed_pct_median_swe) &&
      is.double(promoted$validation$latest$normal_rolling_pct_median_swe),
    "Promoted all-null properties did not retain expected types."
  )
})

scenario("A truly omitted staged property is rejected without replacement", {
  results <- list(
    nrcs_snotel = make_result(
      "nrcs_snotel",
      TRUE,
      fresh_nrcs_latest,
      fresh_nrcs_trace
    ),
    cdec_snow_sensor = make_result(
      "cdec_snow_sensor",
      TRUE,
      fresh_cdec_latest,
      fresh_cdec_trace
    )
  )
  resolved <- resolve(results)
  output_dir <- file.path(test_root, "missing-staged-key")
  dir.create(output_dir)
  final_paths <- c(
    latest_geojson = file.path(output_dir, "latest.geojson"),
    latest_summary = file.path(output_dir, "latest.json"),
    trace_csv = file.path(output_dir, "trace.csv"),
    trace_summary = file.path(output_dir, "trace.json")
  )
  for (name in names(final_paths)) {
    writeLines(paste0("official-", name), final_paths[[name]])
  }
  before <- unname(tools::md5sum(final_paths))

  error <- tryCatch(
    {
      snow_stage_and_promote_outputs(
        final_paths = final_paths,
        write_staged = function(staged_paths) {
          write_fixture_output_set(staged_paths, resolved$latest, resolved$trace)
          malformed <- jsonlite::fromJSON(
            staged_paths[["latest_geojson"]],
            simplifyVector = FALSE
          )
          malformed$features[[1]]$properties <-
            malformed$features[[1]]$properties[
              setdiff(names(malformed$features[[1]]$properties), "river_basin")
            ]
          jsonlite::write_json(
            malformed,
            staged_paths[["latest_geojson"]],
            auto_unbox = TRUE,
            na = "null",
            null = "null"
          )
        },
        validate_staged = function(staged_paths) {
          snow_validate_staged_outputs(
            paths = staged_paths,
            stations = stations,
            current_water_year = current_water_year,
            max_valid_swe_in = max_valid_swe_in,
            min_trace_rows = 1L,
            min_latest_swe_rows = 1L,
            required_latest_columns = required_latest_columns,
            required_trace_columns = required_trace_columns,
            latest_prototype = resolved$latest
          )
        }
      )
      NULL
    },
    error = function(e) e
  )
  after <- unname(tools::md5sum(final_paths))

  assert_true(inherits(error, "error"), "A truly omitted staged property was accepted.")
  assert_true(
    grepl(
      "Staged latest GeoJSON feature 1 (station_uid=CDEC_A) is missing required properties: river_basin",
      conditionMessage(error),
      fixed = TRUE
    ),
    paste("Missing-key error did not identify the feature:", conditionMessage(error))
  )
  assert_equal(
    before,
    after,
    "Missing staged property rejection changed existing output bytes."
  )
})

scenario("Prior latest, summary, trace, and trace-summary validation", {
  output_dir <- file.path(test_root, "prior-validation")
  dir.create(output_dir)
  paths <- c(
    latest_geojson = file.path(output_dir, "latest.geojson"),
    latest_summary = file.path(output_dir, "latest.json"),
    trace_csv = file.path(output_dir, "trace.csv"),
    trace_summary = file.path(output_dir, "trace.json")
  )
  write_fixture_geojson(prior_latest, paths[["latest_geojson"]])
  readr::write_csv(prior_trace, paths[["trace_csv"]])
  jsonlite::write_json(
    list(
      layer = "Snow pillows / SWE latest",
      build_time_local = "old",
      build_date_local = "2025-10-02",
      current_water_year = current_water_year,
      latest_geojson_rows = nrow(prior_latest),
      current_wy_trace_rows = nrow(prior_trace)
    ),
    paths[["latest_summary"]],
    auto_unbox = TRUE
  )
  jsonlite::write_json(
    list(
      layer = "Snow pillows / SWE current water-year trace",
      build_time_local = "old",
      build_date_local = "2025-10-02",
      current_water_year = current_water_year,
      current_wy_trace_rows = nrow(prior_trace),
      stations_with_trace_rows = dplyr::n_distinct(prior_trace$station_uid),
      providers = sort(unique(prior_trace$live_provider_key))
    ),
    paths[["trace_summary"]],
    auto_unbox = TRUE
  )

  validation <- snow_validate_prior_outputs(
    paths = paths,
    stations = stations,
    current_water_year = current_water_year,
    max_valid_swe_in = max_valid_swe_in,
    required_latest_columns = required_latest_columns,
    required_trace_columns = required_trace_columns,
    latest_prototype = prior_latest
  )

  assert_true(
    validation$valid,
    paste("Valid four-file prior fixture was rejected:", paste(validation$problems, collapse = "; "))
  )
  assert_true(
    is.character(validation$latest$river_basin) &&
      is.double(validation$latest$normal_fixed_pct_median_swe) &&
      is.double(validation$latest$normal_rolling_pct_median_swe),
    "Valid prior all-null properties did not retain expected types."
  )

  malformed <- jsonlite::fromJSON(paths[["latest_geojson"]], simplifyVector = FALSE)
  malformed$features[[1]]$properties <-
    malformed$features[[1]]$properties[
      setdiff(names(malformed$features[[1]]$properties), "river_basin")
    ]
  jsonlite::write_json(
    malformed,
    paths[["latest_geojson"]],
    auto_unbox = TRUE,
    na = "null",
    null = "null"
  )
  invalid <- snow_validate_prior_outputs(
    paths = paths,
    stations = stations,
    current_water_year = current_water_year,
    max_valid_swe_in = max_valid_swe_in,
    required_latest_columns = required_latest_columns,
    required_trace_columns = required_trace_columns,
    latest_prototype = prior_latest
  )

  assert_true(!invalid$valid, "Prior GeoJSON with a truly missing key was accepted.")
  assert_true(
    any(grepl(
      "Prior latest GeoJSON feature 1 (station_uid=CDEC_A) is missing required properties: river_basin",
      invalid$problems,
      fixed = TRUE
    )),
    paste("Prior missing-key error was not specific:", paste(invalid$problems, collapse = "; "))
  )
})

scenario("Current full-size tracked product passes provider and combined QA", {
  current_paths <- c(
    latest_geojson = "docs/data/snow_pillow_latest.geojson",
    latest_summary = "docs/data/snow_pillow_latest_summary.json",
    trace_csv = "docs/data/snow_pillow_current_wy_trace.csv",
    trace_summary = "docs/data/snow_pillow_current_wy_trace_summary.json"
  )
  current_stations <- readr::read_csv(
    "data/input/snow_pillow_station_index.csv",
    show_col_types = FALSE
  )
  current_latest <- snow_read_geojson_properties(current_paths[["latest_geojson"]])
  current_trace <- readr::read_csv(
    current_paths[["trace_csv"]],
    show_col_types = FALSE
  )
  current_summary <- snow_read_json_object(
    current_paths[["latest_summary"]],
    "Current latest summary"
  )
  current_expected_days <- as.integer(
    as.Date(current_summary$fetch_end_date) -
      as.Date(current_summary$fetch_start_date) +
      1L
  )

  current_results <- list()
  current_min_trace_rows <- 0L
  for (provider_id in names(coverage_defaults)) {
    policy <- coverage_defaults[[provider_id]]
    provider_stations <- current_stations |>
      dplyr::filter(.data$live_provider_key == provider_id)
    minimums <- snow_provider_completeness_minimums(
      provider_id = provider_id,
      indexed_station_count = nrow(provider_stations),
      expected_fetch_days = current_expected_days,
      station_fraction = policy$station_fraction,
      row_fraction = policy$row_fraction
    )
    observations <- current_trace |>
      dplyr::filter(.data$live_provider_key == provider_id) |>
      dplyr::transmute(
        station_uid = .data$station_uid,
        provider = .data$provider,
        provider_station_id = .data$provider_station_id,
        obs_date = as.Date(.data$obs_date_local),
        swe_in = .data$swe_in,
        source_element = .data$source_element,
        swe_source_class = .data$swe_source_class,
        swe_source_label = .data$swe_source_label,
        swe_source_note = .data$swe_source_note
      )
    qa <- snow_validate_provider_observations(
      observations = observations,
      provider_id = provider_id,
      expected_station_uids = provider_stations$station_uid,
      min_rows = minimums$min_rows,
      min_sites = minimums$min_sites,
      fetch_start_date = current_summary$fetch_start_date,
      fetch_end_date = current_summary$fetch_end_date,
      max_valid_swe_in = max_valid_swe_in
    )
    assert_true(
      qa$passed,
      paste("Current", provider_id, "product failed:", paste(qa$problems, collapse = "; "))
    )
    current_min_trace_rows <- current_min_trace_rows + minimums$min_rows
    current_results[[provider_id]] <- snow_provider_result(
      provider_id = provider_id,
      success = TRUE,
      fetch_status = "success",
      qa_status = "passed",
      latest_rows = current_latest |>
        dplyr::filter(.data$live_provider_key == provider_id),
      trace_rows = current_trace |>
        dplyr::filter(.data$live_provider_key == provider_id),
      qa = qa,
      fetch_started_at_utc = "2026-07-25T00:00:00Z",
      fetch_completed_at_utc = "2026-07-25T00:01:00Z"
    )
  }

  resolved <- snow_resolve_publication(
    provider_results = current_results,
    prior_outputs = NULL,
    stations = current_stations,
    current_water_year = as.integer(current_summary$current_water_year),
    max_valid_swe_in = max_valid_swe_in,
    min_trace_rows = current_min_trace_rows,
    min_latest_swe_rows = 10L,
    required_latest_columns = names(current_latest),
    required_trace_columns = names(current_trace)
  )
  assert_true(
    resolved$publish,
    paste("Current full-size product simulation failed:", resolved$refusal_reason)
  )
  assert_equal(resolved$decision$mode, "full", "Current product should simulate a full refresh.")
})

message("All deterministic snow partial-refresh tests passed.")
