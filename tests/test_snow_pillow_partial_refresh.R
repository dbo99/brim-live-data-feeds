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
      latitude = ifelse(.data$live_provider_key == "cdec_snow_sensor", 39, 41),
      longitude = ifelse(.data$live_provider_key == "cdec_snow_sensor", -121, -120),
      latest_swe_in = seq_len(dplyr::n()) + value_offset,
      latest_swe_date_local = date,
      latest_swe_source_class = ifelse(
        .data$live_provider_key == "cdec_snow_sensor",
        "cdec_sno_adj_82",
        "nrcs_wteq"
      ),
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
    na = "null"
  )
}

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
    required_trace_columns = required_trace_columns
  )

  assert_true(
    validation$valid,
    paste("Valid four-file prior fixture was rejected:", paste(validation$problems, collapse = "; "))
  )
})

message("All deterministic snow partial-refresh tests passed.")
