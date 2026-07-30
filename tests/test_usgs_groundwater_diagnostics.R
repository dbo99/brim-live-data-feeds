#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(dplyr)
  library(jsonlite)
  library(lubridate)
  library(tibble)
})

source("scripts/usgs_groundwater_diagnostics_helpers.R")

assert_true <- function(value, message) {
  if (!isTRUE(value)) stop(message, call. = FALSE)
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

assert_near <- function(actual, expected, tolerance, message) {
  if (length(actual) != length(expected) ||
      any(abs(actual - expected) > tolerance)) {
    stop(message, call. = FALSE)
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

file_digest <- function(path) {
  unname(tools::md5sum(path))
}

test_root <- tempfile("usgs-groundwater-diagnostics-tests-")
dir.create(test_root, recursive = TRUE)
on.exit(unlink(test_root, recursive = TRUE, force = TRUE), add = TRUE)

official_geojson <- "docs/data/usgs_groundwater_latest_ca.geojson"
official_summary <- "docs/data/usgs_groundwater_latest_ca_summary.json"
official_hashes_before <- c(
  geojson = file_digest(official_geojson),
  summary = file_digest(official_summary)
)

measurement_row <- function(site_no,
                            value = 10,
                            time = "2026-07-25T12:00:00Z",
                            parameter_code = "72019") {
  tibble::tibble(
    monitoring_location_id = paste0("USGS-", site_no),
    parameter_code = parameter_code,
    time = time,
    value = value,
    unit_of_measure = "ft",
    qualifier = NA_character_,
    approval_status = "Approved",
    observing_procedure = "steel tape",
    vertical_datum = "land surface datum",
    measuring_agency = "USGS",
    field_visit_id = paste0("visit-", site_no),
    last_modified = "2026-07-25T13:00:00Z"
  )
}

properties_list <- function(site_no,
                            value = 10,
                            time = "2026-07-25T12:00:00Z",
                            parameter_code = "72019") {
  as.list(measurement_row(site_no, value, time, parameter_code)[1, ])
}

feature_collection_body <- function(properties) {
  jsonlite::toJSON(
    list(
      type = "FeatureCollection",
      features = lapply(properties, function(x) {
        list(type = "Feature", properties = x)
      })
    ),
    auto_unbox = TRUE,
    null = "null"
  )
}

http_response <- function(status,
                          body,
                          content_type = "application/geo+json",
                          retry_after = NULL) {
  headers <- list(`content-type` = content_type)
  if (!is.null(retry_after)) headers[["retry-after"]] <- retry_after
  list(
    status_code = as.integer(status),
    headers = headers,
    content = charToRaw(as.character(body))
  )
}

sequence_fetch <- function(responses) {
  index <- 0L
  function(url, timeout_sec, connect_timeout_sec) {
    index <<- index + 1L
    value <- responses[[min(index, length(responses))]]
    if (inherits(value, "error")) stop(value)
    value
  }
}

request_fields <- c(
  "chunk_id",
  "chunk_ordinal",
  "request_label",
  "endpoint_class",
  "attempt_count",
  "elapsed_seconds",
  "transport_outcome",
  "http_status",
  "content_type",
  "response_bytes",
  "retryable",
  "terminal_outcome"
)

scenario("healthy multi-chunk retrieval preserves latest-value behavior", {
  site_ids <- sprintf("%08d", seq_len(300L))
  primary_fetch <- function(monitoring_location_id,
                            parameter_code,
                            time,
                            properties,
                            skipGeometry) {
    ids <- sub("^USGS-", "", monitoring_location_id)
    dplyr::bind_rows(lapply(seq_along(ids), function(i) {
      measurement_row(
        ids[[i]],
        value = i,
        time = "2026-07-25T12:00:00Z"
      )
    }))
  }

  retrieval <- gw_retrieve_chunks(
    site_ids = site_ids,
    start_date = "2024-05-16",
    end_date = "2026-07-26",
    parameter_code = "72019",
    chunk_size = 60L,
    request_pause_sec = 0,
    primary_fetch_fun = primary_fetch,
    sleep_fun = function(x) NULL,
    jitter_sec = 0
  )
  parsed <- gw_parse_latest(retrieval$raw, "72019", site_ids)
  summary <- gw_retrieval_summary(retrieval, parsed$accounting)

  assert_equal(summary$total_chunks, 5L, "Healthy fixture should use five chunks.")
  assert_equal(
    summary$successful_data_chunks,
    5L,
    "Every healthy chunk should contain data."
  )
  assert_equal(summary$incomplete_chunks, 0L, "Healthy retrieval must be complete.")
  assert_equal(
    summary$unique_site_count,
    300L,
    "Healthy fixture should retain all 300 sites."
  )
  assert_true(
    all(request_fields %in% names(retrieval$results[[1]])),
    "Structured request results are missing required diagnostic fields."
  )
  assert_true(
    !grepl("\\?", retrieval$results[[1]]$endpoint_class),
    "Endpoint diagnostic must omit query parameters."
  )
  gw_enforce_publication(summary, 300L)
})

scenario("duplicate collapse keeps the latest observation", {
  raw <- dplyr::bind_rows(
    measurement_row("00000001", 12, "2026-07-24T12:00:00Z"),
    measurement_row("00000001", 14, "2026-07-25T12:00:00Z")
  )
  parsed <- gw_parse_latest(raw, "72019", "00000001")
  assert_equal(
    parsed$accounting$duplicate_collapse_removed,
    1L,
    "Duplicate accounting should report one removed row."
  )
  assert_near(
    parsed$latest$api_latest_wl_ft_bgs,
    14,
    1e-12,
    "Latest-value selection changed."
  )
})

scenario("HTTP 429 retries, respects Retry-After, then succeeds", {
  delays <- numeric()
  fetch <- sequence_fetch(list(
    http_response(
      429,
      '{"error":"rate limited"}',
      content_type = "application/json",
      retry_after = "2"
    ),
    http_response(
      200,
      feature_collection_body(list(properties_list("00000001")))
    )
  ))
  result <- gw_http_request(
    url = paste0(
      "https://api.waterdata.usgs.gov/ogcapi/v0/collections/",
      "field-measurements/items?monitoring_location_id=USGS-00000001"
    ),
    chunk_id = "1/1",
    chunk_ordinal = 1L,
    total_chunks = 1L,
    request_label = "429 fixture",
    max_attempts = 3L,
    jitter_sec = 0,
    http_fetch_fun = fetch,
    sleep_fun = function(x) delays <<- c(delays, x)
  )
  assert_equal(result$terminal_outcome, "success_data", "429 recovery did not succeed.")
  assert_equal(result$attempt_count, 2L, "429 recovery attempt count is wrong.")
  assert_equal(delays, 2, "Retry-After was not respected.")
  assert_true(
    !grepl("?", result$endpoint_class, fixed = TRUE),
    "Request result leaked query material."
  )
})

scenario("HTTP 503 retries are bounded and terminally transient", {
  delays <- numeric()
  fetch <- sequence_fetch(rep(list(
    http_response(503, '{"error":"unavailable"}', "application/json")
  ), 3))
  result <- gw_http_request(
    url = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/items",
    chunk_id = "1/1",
    chunk_ordinal = 1L,
    total_chunks = 1L,
    request_label = "503 fixture",
    max_attempts = 3L,
    base_backoff_sec = 1,
    max_backoff_sec = 8,
    jitter_sec = 0,
    http_fetch_fun = fetch,
    sleep_fun = function(x) delays <<- c(delays, x)
  )
  assert_equal(
    result$terminal_outcome,
    "transient_failure",
    "503 should end as transient failure."
  )
  assert_equal(result$attempt_count, 3L, "503 retry count should be bounded at three.")
  assert_equal(delays, c(1, 2), "503 backoff should be stepped and bounded.")
})

scenario("retry configuration cannot exceed hard safety caps", {
  delays <- numeric()
  fetch_calls <- 0L
  result <- gw_http_request(
    url = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/items",
    chunk_id = "1/1",
    chunk_ordinal = 1L,
    total_chunks = 1L,
    request_label = "hard-cap fixture",
    max_attempts = 99L,
    base_backoff_sec = 99,
    max_backoff_sec = 99,
    max_retry_after_sec = 99,
    jitter_sec = 99,
    http_fetch_fun = function(...) {
      fetch_calls <<- fetch_calls + 1L
      http_response(
        429,
        '{"error":"rate limited"}',
        "application/json",
        retry_after = "99"
      )
    },
    sleep_fun = function(x) delays <<- c(delays, x),
    jitter_fun = function(...) 0.25
  )
  assert_equal(fetch_calls, 3L, "Retry attempts exceeded the hard cap of three.")
  assert_equal(result$attempt_count, 3L, "Hard-capped attempt count is wrong.")
  assert_equal(
    delays,
    c(30, 30),
    "Retry-After delays exceeded the hard cap of 30 seconds."
  )
})

scenario("transport timeout retries are bounded", {
  delays <- numeric()
  result <- gw_http_request(
    url = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/items",
    chunk_id = "1/1",
    chunk_ordinal = 1L,
    total_chunks = 1L,
    request_label = "timeout fixture",
    max_attempts = 3L,
    base_backoff_sec = 1,
    jitter_sec = 0,
    http_fetch_fun = function(url, timeout_sec, connect_timeout_sec) {
      stop("Timeout was reached")
    },
    sleep_fun = function(x) delays <<- c(delays, x)
  )
  assert_equal(
    result$terminal_outcome,
    "transient_failure",
    "Timeout should end as transient failure."
  )
  assert_equal(result$attempt_count, 3L, "Timeout retry count should be bounded.")
  assert_equal(result$transport_outcome, "transport_error", "Timeout class is wrong.")
  assert_equal(delays, c(1, 2), "Timeout backoff is wrong.")
})

scenario("permanent HTTP 400 and 404 do not retry", {
  for (status in c(400L, 404L)) {
    result <- gw_http_request(
      url = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/items",
      chunk_id = "1/1",
      chunk_ordinal = 1L,
      total_chunks = 1L,
      request_label = paste(status, "fixture"),
      max_attempts = 3L,
      http_fetch_fun = sequence_fetch(list(
        http_response(status, '{"error":"bad request"}', "application/json")
      )),
      sleep_fun = function(x) stop("Permanent failures must not sleep.")
    )
    assert_equal(
      result$terminal_outcome,
      "permanent_http_failure",
      paste("HTTP", status, "was misclassified.")
    )
    assert_equal(result$attempt_count, 1L, "Permanent 4xx response was retried.")
  }
})

scenario("valid empty GeoJSON is success_empty and remains insufficient", {
  result <- gw_http_request(
    url = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/items",
    chunk_id = "1/1",
    chunk_ordinal = 1L,
    total_chunks = 1L,
    request_label = "empty fixture",
    http_fetch_fun = sequence_fetch(list(
      http_response(200, '{"type":"FeatureCollection","features":[]}')
    )),
    sleep_fun = function(x) NULL
  )
  assert_equal(
    result$terminal_outcome,
    "success_empty",
    "Valid empty response was not distinguished from malformed data."
  )
  retrieval <- list(results = list(result), raw = tibble::tibble())
  parsed <- gw_parse_latest(retrieval$raw, "72019", "00000001")
  summary <- gw_retrieval_summary(retrieval, parsed$accounting)
  assert_error(
    gw_enforce_publication(summary, 300L),
    "valid-but-insufficient data",
    "Valid empty retrieval should still refuse publication."
  )
})

scenario("null features is a schema mismatch, not a valid empty response", {
  result <- gw_http_request(
    url = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/items",
    chunk_id = "1/1",
    chunk_ordinal = 1L,
    total_chunks = 1L,
    request_label = "null-features fixture",
    http_fetch_fun = sequence_fetch(list(
      http_response(200, '{"type":"FeatureCollection","features":null}')
    )),
    sleep_fun = function(x) NULL
  )
  assert_equal(
    result$terminal_outcome,
    "schema_mismatch",
    "A null features member must not count as a valid empty response."
  )
})

scenario("HTTP 200 error JSON and HTML are not treated as empty data", {
  error_json <- gw_http_request(
    url = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/items",
    chunk_id = "1/1",
    chunk_ordinal = 1L,
    total_chunks = 1L,
    request_label = "error JSON fixture",
    http_fetch_fun = sequence_fetch(list(
      http_response(200, '{"error":"backend unavailable"}', "application/json")
    )),
    sleep_fun = function(x) NULL
  )
  html <- gw_http_request(
    url = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/items",
    chunk_id = "1/1",
    chunk_ordinal = 1L,
    total_chunks = 1L,
    request_label = "HTML fixture",
    http_fetch_fun = sequence_fetch(list(
      http_response(200, "<html>gateway error</html>", "text/html")
    )),
    sleep_fun = function(x) NULL
  )
  assert_equal(
    error_json$terminal_outcome,
    "schema_mismatch",
    "HTTP 200 error JSON should be a schema mismatch."
  )
  assert_equal(
    html$terminal_outcome,
    "malformed_payload",
    "HTTP 200 HTML should be malformed payload."
  )
  assert_true(
    nchar(html$safe_sample) <= 183L,
    "Non-data error sample is not bounded."
  )
})

scenario("missing required JSON keys are an explicit schema mismatch", {
  incomplete <- properties_list("00000001")
  incomplete$value <- NULL
  result <- gw_http_request(
    url = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/items",
    chunk_id = "1/1",
    chunk_ordinal = 1L,
    total_chunks = 1L,
    request_label = "missing-key fixture",
    http_fetch_fun = sequence_fetch(list(
      http_response(200, feature_collection_body(list(incomplete)))
    )),
    sleep_fun = function(x) NULL
  )
  assert_equal(
    result$terminal_outcome,
    "schema_mismatch",
    "Missing required key should be a schema mismatch."
  )
  assert_equal(result$raw_record_count, 1L, "Raw response count should remain visible.")
  assert_true(
    "value" %in% result$missing_required_keys,
    "Missing-key diagnostic did not identify value."
  )
})

scenario("parser accounting identifies the exact collapse stage", {
  raw <- tibble::tibble(
    monitoring_location_id = c("", "USGS-00000001", "USGS-00000002", "USGS-00000003"),
    parameter_code = c("72019", "99999", "72019", "72019"),
    time = c(
      "2026-07-25T12:00:00Z",
      "2026-07-25T12:00:00Z",
      "not-a-date",
      "2026-07-25T12:00:00Z"
    ),
    value = c("1", "1", "1", "not-a-number")
  )
  parsed <- gw_parse_latest(raw, "72019", sprintf("%08d", 1:3))
  result <- gw_request_result(
    chunk_id = "1/1",
    chunk_ordinal = 1L,
    total_chunks = 1L,
    request_label = "parser fixture",
    endpoint_class = "api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/items",
    attempt_count = 1L,
    elapsed_seconds = 0,
    transport_outcome = "response_received",
    terminal_outcome = "success_data",
    source_path = "dataRetrieval",
    records = raw,
    raw_record_count = nrow(raw)
  )
  summary <- gw_retrieval_summary(list(results = list(result), raw = raw), parsed$accounting)
  assert_equal(summary$raw_records_received, 4L, "Raw count should be nonzero.")
  assert_equal(summary$records_after_site_id, 3L, "Site-ID rejection count is wrong.")
  assert_equal(summary$records_after_parameter, 2L, "Parameter rejection count is wrong.")
  assert_equal(summary$records_after_datetime, 1L, "Date rejection count is wrong.")
  assert_equal(summary$records_after_value, 0L, "Value rejection count is wrong.")
  assert_equal(
    gw_failure_class(summary, 300L),
    "parser/filter collapse",
    "Final failure class should expose parser collapse."
  )
})

scenario("mixed chunk outcomes fail closed even above 300 sites", {
  rows_a <- dplyr::bind_rows(lapply(seq_len(200L), function(i) {
    measurement_row(sprintf("%08d", i))
  }))
  rows_b <- dplyr::bind_rows(lapply(201:400, function(i) {
    measurement_row(sprintf("%08d", i))
  }))
  success_result <- function(rows, ordinal) {
    gw_request_result(
      chunk_id = paste0(ordinal, "/3"),
      chunk_ordinal = ordinal,
      total_chunks = 3L,
      request_label = "mixed fixture",
      endpoint_class = "api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/items",
      attempt_count = 1L,
      elapsed_seconds = 0,
      transport_outcome = "response_received",
      terminal_outcome = "success_data",
      source_path = "dataRetrieval",
      records = rows,
      raw_record_count = nrow(rows)
    )
  }
  failed <- gw_request_result(
    chunk_id = "2/3",
    chunk_ordinal = 2L,
    total_chunks = 3L,
    request_label = "mixed fixture",
    endpoint_class = "api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/items",
    attempt_count = 4L,
    elapsed_seconds = 0,
    transport_outcome = "response_received",
    http_status = 503L,
    retryable = TRUE,
    terminal_outcome = "transient_failure",
    source_path = "direct_ogc_fallback"
  )
  raw <- dplyr::bind_rows(rows_a, rows_b)
  retrieval <- list(
    results = list(success_result(rows_a, 1L), failed, success_result(rows_b, 3L)),
    raw = raw
  )
  parsed <- gw_parse_latest(raw, "72019", sprintf("%08d", 1:400))
  summary <- gw_retrieval_summary(retrieval, parsed$accounting)
  assert_equal(summary$unique_site_count, 400L, "Mixed fixture should exceed 300 sites.")
  assert_equal(summary$incomplete_chunks, 1L, "Mixed failure must be explicit.")
  assert_error(
    gw_enforce_publication(summary, 300L),
    "incomplete chunk retrieval",
    "Incomplete retrieval must not publish even above the site minimum."
  )
})

scenario("circuit breaker prevents a 38-chunk retry storm", {
  primary_calls <- 0L
  direct_calls <- 0L
  retrieval <- gw_retrieve_chunks(
    site_ids = sprintf("%08d", seq_len(380L)),
    start_date = "2024-05-16",
    end_date = "2026-07-26",
    parameter_code = "72019",
    chunk_size = 10L,
    request_pause_sec = 0,
    max_consecutive_failures = 99L,
    max_attempts = 1L,
    primary_fetch_fun = function(...) {
      primary_calls <<- primary_calls + 1L
      stop("HTTP 503 Service Unavailable")
    },
    http_fetch_fun = function(...) {
      direct_calls <<- direct_calls + 1L
      http_response(503, '{"error":"unavailable"}', "application/json")
    },
    sleep_fun = function(x) NULL,
    jitter_sec = 0
  )
  assert_equal(primary_calls, 3L, "Circuit breaker should stop primary calls after three chunks.")
  assert_equal(direct_calls, 3L, "Circuit breaker should stop direct calls after three chunks.")
  assert_equal(length(retrieval$results), 38L, "All chunk ordinals must remain accounted for.")
  assert_equal(
    retrieval$results[[4]]$transport_outcome,
    "not_attempted_circuit_breaker",
    "Unattempted chunks need an explicit circuit-breaker result."
  )
  parsed <- gw_parse_latest(
    retrieval$raw,
    "72019",
    sprintf("%08d", seq_len(380L))
  )
  summary <- gw_retrieval_summary(retrieval, parsed$accounting)
  assert_equal(
    summary$not_attempted_chunks,
    35L,
    "Circuit-breaker summary should distinguish 35 unattempted chunks."
  )
  assert_equal(
    summary$transiently_failed_chunks,
    3L,
    "Only the three attempted failed chunks should count as transient failures."
  )
})

scenario("warning aggregation is bounded and counted", {
  primary_fetch <- function(monitoring_location_id,
                            parameter_code,
                            time,
                            properties,
                            skipGeometry) {
    warning("repeated provider warning")
    dplyr::bind_rows(lapply(
      sub("^USGS-", "", monitoring_location_id),
      measurement_row
    ))
  }
  retrieval <- gw_retrieve_chunks(
    site_ids = sprintf("%08d", seq_len(6L)),
    start_date = "2024-05-16",
    end_date = "2026-07-26",
    parameter_code = "72019",
    chunk_size = 2L,
    request_pause_sec = 0,
    primary_fetch_fun = primary_fetch,
    sleep_fun = function(x) NULL,
    jitter_sec = 0
  )
  parsed <- gw_parse_latest(retrieval$raw, "72019", sprintf("%08d", seq_len(6L)))
  summary <- gw_retrieval_summary(retrieval, parsed$accounting)
  warning_rows <- summary$warning_summary |>
    dplyr::filter(.data$message == "repeated provider warning")
  assert_equal(nrow(warning_rows), 1L, "Repeated warning should have one summary row.")
  assert_equal(warning_rows$count[[1]], 3L, "Repeated warning count is wrong.")
  log_lines <- capture.output(gw_log_retrieval_summary(summary), type = "message")
  repeated_lines <- grep("repeated provider warning", log_lines, value = TRUE)
  assert_equal(
    length(repeated_lines),
    2L,
    "Repeated warnings should be limited to the first sample and one counted summary."
  )
})

scenario("minimum-site guard remains exactly 300", {
  make_complete_summary <- function(site_count) {
    rows <- dplyr::bind_rows(lapply(seq_len(site_count), function(i) {
      measurement_row(sprintf("%08d", i))
    }))
    result <- gw_request_result(
      chunk_id = "1/1",
      chunk_ordinal = 1L,
      total_chunks = 1L,
      request_label = "minimum fixture",
      endpoint_class = "api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/items",
      attempt_count = 1L,
      elapsed_seconds = 0,
      transport_outcome = "response_received",
      terminal_outcome = "success_data",
      source_path = "dataRetrieval",
      records = rows,
      raw_record_count = nrow(rows)
    )
    parsed <- gw_parse_latest(
      rows,
      "72019",
      sprintf("%08d", seq_len(site_count))
    )
    gw_retrieval_summary(list(results = list(result), raw = rows), parsed$accounting)
  }
  assert_error(
    gw_enforce_publication(make_complete_summary(299L), 300L),
    "minimum remains 300",
    "299 sites must still fail."
  )
  gw_enforce_publication(make_complete_summary(300L), 300L)
})

scenario("promotion rollback preserves both prior files byte-for-byte", {
  target_geo <- file.path(test_root, "target.geojson")
  target_summary <- file.path(test_root, "target.json")
  staged_geo <- file.path(test_root, "staged.geojson")
  staged_summary <- file.path(test_root, "staged.json")
  writeLines("old-geojson-bytes", target_geo, useBytes = TRUE)
  writeLines("old-summary-bytes", target_summary, useBytes = TRUE)
  writeLines("new-geojson-bytes", staged_geo, useBytes = TRUE)
  writeLines("new-summary-bytes", staged_summary, useBytes = TRUE)
  before <- c(file_digest(target_geo), file_digest(target_summary))
  rename_calls <- 0L
  flaky_rename <- function(from, to) {
    rename_calls <<- rename_calls + 1L
    if (rename_calls == 4L) return(FALSE)
    file.rename(from, to)
  }

  assert_error(
    gw_promote_complete_set(
      staged_paths = c(staged_geo, staged_summary),
      target_paths = c(target_geo, target_summary),
      rename_fun = flaky_rename
    ),
    "promotion failed",
    "Injected second-file promotion failure should stop."
  )
  after <- c(file_digest(target_geo), file_digest(target_summary))
  assert_equal(after, before, "Rollback did not preserve prior bytes.")
})

scenario("validation failure leaves existing output bytes unchanged", {
  target_geo <- file.path(test_root, "validated-target.geojson")
  target_summary <- file.path(test_root, "validated-target.json")
  writeLines("existing-geojson", target_geo, useBytes = TRUE)
  writeLines("existing-summary", target_summary, useBytes = TRUE)
  before <- c(file_digest(target_geo), file_digest(target_summary))
  assert_error(
    gw_write_staged_outputs(
      geojson = list(type = "FeatureCollection", features = list()),
      summary = list(output_feature_count = 0L, api_latest_site_count = 0L),
      out_geojson = target_geo,
      out_summary = target_summary,
      min_features = 300L
    ),
    "output QA failure",
    "Invalid staged output should fail before promotion."
  )
  after <- c(file_digest(target_geo), file_digest(target_summary))
  assert_equal(after, before, "Validation failure changed prior output bytes.")
})

scenario("current tracked output passes full schema and geometry QA", {
  gw_validate_output_files(official_geojson, official_summary, 300L)
})

scenario("full-size tracked-product simulation preserves API identities and values", {
  current_geojson <- jsonlite::fromJSON(official_geojson)
  current_summary <- jsonlite::fromJSON(official_summary)
  props <- current_geojson$features$properties
  api_rows <- props |>
    dplyr::filter(.data$has_api_latest_wl %in% TRUE) |>
    dplyr::transmute(
      monitoring_location_id = paste0("USGS-", .data$site_no),
      parameter_code = "72019",
      time = .data$api_latest_wl_datetime_utc,
      value = .data$api_latest_wl_ft_bgs,
      unit_of_measure = .data$api_latest_wl_units,
      qualifier = .data$api_latest_wl_qualifier,
      approval_status = .data$api_latest_wl_status,
      observing_procedure = .data$api_latest_wl_procedure,
      vertical_datum = .data$api_latest_wl_vertical_datum,
      measuring_agency = .data$api_latest_wl_measuring_agency,
      field_visit_id = .data$api_latest_wl_field_visit_id,
      last_modified = .data$api_latest_wl_last_modified_utc
    )
  parsed <- gw_parse_latest(api_rows, "72019", props$site_no)
  actual <- parsed$latest |>
    dplyr::arrange(.data$site_no)
  expected <- props |>
    dplyr::filter(.data$has_api_latest_wl %in% TRUE) |>
    dplyr::arrange(.data$site_no)

  assert_equal(
    nrow(actual),
    as.integer(current_summary$api_latest_site_count),
    "Full-size parser site count changed."
  )
  assert_equal(
    actual$site_no,
    expected$site_no,
    "Full-size parser identities changed."
  )
  assert_equal(
    actual$api_latest_wl_datetime_utc,
    expected$api_latest_wl_datetime_utc,
    "Full-size parser timestamps changed."
  )
  assert_near(
    actual$api_latest_wl_ft_bgs,
    expected$api_latest_wl_ft_bgs,
    1e-8,
    "Full-size parser values changed."
  )
  assert_equal(
    parsed$accounting$site_join_mismatch_removed,
    0L,
    "Tracked full-size simulation should have no site-index mismatch."
  )
})

scenario("official tracked products remained byte-for-byte unchanged", {
  official_hashes_after <- c(
    geojson = file_digest(official_geojson),
    summary = file_digest(official_summary)
  )
  assert_equal(
    official_hashes_after,
    official_hashes_before,
    "Offline tests modified official tracked groundwater products."
  )
})

message("All USGS groundwater diagnostic and retry tests passed.")
