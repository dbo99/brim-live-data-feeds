#!/usr/bin/env Rscript

source("scripts/cnrfc_major_water_supply_basin_forecast_helpers.R")

roster_path <- Sys.getenv(
  "CNRFC_FORECAST_ROSTER_CSV",
  unset = "data/input/cnrfc_major_water_supply_basin_forecast_sources.csv"
)
output_path <- Sys.getenv(
  "CNRFC_FORECAST_OUTPUT_JSON",
  unset = "docs/data/major_water_supply_basin_forecasts.json"
)
prior_path <- Sys.getenv("CNRFC_FORECAST_PRIOR_JSON", unset = output_path)
qa_path <- Sys.getenv(
  "CNRFC_FORECAST_QA_JSON",
  unset = file.path(tempdir(), "major_water_supply_basin_forecasts_run.json")
)

water_year_stale_after_hours <- suppressWarnings(as.numeric(Sys.getenv(
  "CNRFC_FORECAST_WATER_YEAR_STALE_HOURS",
  unset = as.character(CNRFC_WATER_YEAR_STALE_AFTER_HOURS)
)))
water_year_expires_after_hours <- suppressWarnings(as.numeric(Sys.getenv(
  "CNRFC_FORECAST_WATER_YEAR_EXPIRES_HOURS",
  unset = as.character(CNRFC_WATER_YEAR_EXPIRES_AFTER_HOURS)
)))
accumulation_stale_after_hours <- suppressWarnings(as.numeric(Sys.getenv(
  "CNRFC_FORECAST_ACCUMULATION_STALE_HOURS",
  unset = as.character(CNRFC_ACCUMULATION_STALE_AFTER_HOURS)
)))
april_july_stale_after_hours <- suppressWarnings(as.numeric(Sys.getenv(
  "CNRFC_FORECAST_APRIL_JULY_STALE_HOURS",
  unset = as.character(CNRFC_APRIL_JULY_STALE_AFTER_HOURS)
)))
request_pause_sec <- suppressWarnings(as.numeric(Sys.getenv(
  "CNRFC_FORECAST_REQUEST_PAUSE_SEC",
  unset = "0.25"
)))
if (!is.finite(request_pause_sec) || request_pause_sec < 0 || request_pause_sec > 5) {
  cnrfc_stop("CNRFC_FORECAST_REQUEST_PAUSE_SEC must be between 0 and 5 seconds.")
}
if (!is.finite(water_year_stale_after_hours) || water_year_stale_after_hours <= 0 ||
    !is.finite(water_year_expires_after_hours) ||
    water_year_expires_after_hours <= water_year_stale_after_hours ||
    !is.finite(accumulation_stale_after_hours) || accumulation_stale_after_hours <= 0 ||
    !is.finite(april_july_stale_after_hours) || april_july_stale_after_hours <= 0) {
  cnrfc_stop("CNRFC freshness configuration must be positive and water-year expiry must exceed staleness.")
}

roster <- cnrfc_read_roster(roster_path)
prior_payload <- tryCatch(
  cnrfc_read_payload(prior_path, roster),
  error = function(e) cnrfc_stop("Existing canonical payload is invalid: ", conditionMessage(e))
)
prior_map <- cnrfc_prior_record_map(prior_payload)
run_started_at <- cnrfc_iso_utc()
logical_slot <- Sys.getenv("CNRFC_FORECAST_LOGICAL_SLOT", unset = "")
attempt_results <- stats::setNames(vector("list", nrow(roster)), roster$forecast_key)
consecutive_retrieval_failures <- 0L
circuit_open <- FALSE

tag_fetch_attempts <- function(attempts, endpoint) {
  lapply(attempts, function(attempt) {
    attempt$endpoint <- endpoint
    attempt
  })
}

for (index in seq_len(nrow(roster))) {
  row <- roster[index, , drop = FALSE]
  key <- row$forecast_key[[1L]]
  message("CNRFC forecast ", index, "/", nrow(roster), ": ", key)

  if (circuit_open) {
    attempt_results[[key]] <- cnrfc_failure_result(
      "fetch_failed",
      "Three consecutive terminal retrieval failures opened the run circuit breaker.",
      cnrfc_iso_utc(),
      failure_stage = "fetch"
    )
    next
  }

  is_april_july <- identical(
    row$product_type[[1L]],
    "april_july_streamflow_volume_forecast"
  )
  fetch <- cnrfc_fetch_page(row$source_url[[1L]])
  fetch$attempts <- tag_fetch_attempts(fetch$attempts, "headline")
  tabular_fetch <- NULL
  if (isTRUE(fetch$success) && is_april_july) {
    source_water_year <- cnrfc_current_water_year(cnrfc_parse_utc(fetch$retrieved_at))
    tabular_url <- cnrfc_product7_tabular_url(row, source_water_year)
    tabular_fetch <- cnrfc_fetch_page(tabular_url)
    tabular_fetch$attempts <- tag_fetch_attempts(tabular_fetch$attempts, "tabular")
    if (!isTRUE(tabular_fetch$success)) {
      fetch <- list(
        success = FALSE,
        failure_class = paste0("tabular_", tabular_fetch$failure_class),
        message = tabular_fetch$message,
        attempts = c(fetch$attempts, tabular_fetch$attempts)
      )
    }
  }
  if (!isTRUE(fetch$success)) {
    consecutive_retrieval_failures <- consecutive_retrieval_failures + 1L
    attempt_results[[key]] <- cnrfc_failure_result(
      "fetch_failed",
      paste0(fetch$failure_class, ": ", fetch$message),
      cnrfc_iso_utc(),
      fetch$attempts,
      "fetch"
    )
    if (consecutive_retrieval_failures >= 3L) circuit_open <- TRUE
  } else {
    consecutive_retrieval_failures <- 0L
    record_retrieved_at <- if (!is.null(tabular_fetch) && isTRUE(tabular_fetch$success)) {
      tabular_fetch$retrieved_at
    } else {
      fetch$retrieved_at
    }
    combined_attempts <- if (is.null(tabular_fetch)) {
      fetch$attempts
    } else {
      c(fetch$attempts, tabular_fetch$attempts)
    }
    parsed <- tryCatch(
      cnrfc_parse_page(
        fetch$html,
        row,
        record_retrieved_at,
        water_year_stale_after_hours,
        water_year_expires_after_hours,
        accumulation_stale_after_hours,
        april_july_stale_after_hours,
        tabular_html = if (is.null(tabular_fetch)) NULL else tabular_fetch$html
      ),
      error = function(e) e
    )
    if (inherits(parsed, "error")) {
      attempt_outcome <- if (inherits(parsed, "cnrfc_source_unavailable")) {
        "source_unavailable"
      } else if (inherits(parsed, "cnrfc_validation_error")) {
        "validation_failed"
      } else {
        "parse_failed"
      }
      attempt_results[[key]] <- cnrfc_failure_result(
        attempt_outcome,
        conditionMessage(parsed),
        record_retrieved_at,
        combined_attempts
      )
    } else {
      attempt_results[[key]] <- cnrfc_enforce_source_monotonicity(
        cnrfc_success_result(parsed, combined_attempts),
        prior_map[[key]]
      )
    }
  }

  if (index < nrow(roster) && request_pause_sec > 0) Sys.sleep(request_pause_sec)
}

payload_or_error <- tryCatch(
  cnrfc_build_payload(
    roster = roster,
    attempt_results = attempt_results,
    prior_payload = prior_payload,
    generated_at = cnrfc_iso_utc()
  ),
  error = function(e) e
)

qa <- list(
  schema_version = "1.0",
  run_started_at = run_started_at,
  run_completed_at = cnrfc_iso_utc(),
  output_path = output_path,
  prior_payload_path = prior_path,
  logical_slot = if (nzchar(logical_slot)) logical_slot else NULL,
  publication_mode = if (is.null(prior_payload)) "bootstrap" else "steady_state",
  source_record_count = nrow(roster),
  freshness_policy = list(
    water_year_stale_after_hours = water_year_stale_after_hours,
    water_year_expires_after_hours = water_year_expires_after_hours,
    accumulation_stale_after_hours = accumulation_stale_after_hours,
    april_july_stale_after_hours = april_july_stale_after_hours
  ),
  circuit_breaker_opened = circuit_open,
  accepted = !inherits(payload_or_error, "error"),
  acceptance_error = if (inherits(payload_or_error, "error")) conditionMessage(payload_or_error) else NULL,
  family_health = if (inherits(payload_or_error, "error")) NULL else payload_or_error$family_health,
  source_summary = if (inherits(payload_or_error, "error")) NULL else payload_or_error$source_summary,
  operational_notices = if (inherits(payload_or_error, "error")) list() else payload_or_error$operational_notices,
  attempts = lapply(roster$forecast_key, function(key) {
    result <- attempt_results[[key]]
    outcome <- if (isTRUE(result$success)) result$record$attempt_outcome else result$attempt_outcome
    stage <- if (isTRUE(result$success)) result$record$failure_stage else result$failure_stage
    list(
      forecast_key = key,
      attempt_outcome = outcome,
      failure_stage = stage,
      message = if (isTRUE(result$success)) NULL else result$message,
      fetch_attempts = result$fetch_attempts
    )
  })
)
cnrfc_write_json(qa, qa_path)
message("Run diagnostics: ", qa_path)

if (inherits(payload_or_error, "error")) {
  cnrfc_stop(conditionMessage(payload_or_error))
}

updated <- cnrfc_stage_and_promote_payload(
  payload_or_error,
  output_path,
  roster,
  prior_payload
)
message("Output payload updated: ", if (updated) "yes" else "no")
message("Current records: ", payload_or_error$source_summary$current_count)
message("Current partial records: ", payload_or_error$source_summary$current_partial_count)
message("Source-stale records: ", payload_or_error$source_summary$source_stale_count)
message("Last-known-good records: ", payload_or_error$source_summary$stale_last_known_good_count)
message("Expired records: ", payload_or_error$source_summary$expired_count)
message("Failed current attempts: ", payload_or_error$source_summary$failed_attempt_count)
