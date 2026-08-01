#!/usr/bin/env Rscript

source("scripts/cbrfc_major_water_supply_forecast_helpers.R")

roster_path <- Sys.getenv(
  "CBRFC_FORECAST_ROSTER_CSV",
  unset = "data/input/cbrfc_major_water_supply_forecast_sources.csv"
)
output_path <- Sys.getenv(
  "CBRFC_FORECAST_OUTPUT_JSON",
  unset = "docs/data/cbrfc_major_water_supply_forecasts.json"
)
prior_path <- Sys.getenv("CBRFC_FORECAST_PRIOR_JSON", unset = output_path)
qa_path <- Sys.getenv(
  "CBRFC_FORECAST_QA_JSON",
  unset = file.path(tempdir(), "cbrfc_major_water_supply_forecasts_run.json")
)
april_july_stale_after_days <- suppressWarnings(as.integer(Sys.getenv(
  "CBRFC_FORECAST_STALE_AFTER_DAYS",
  unset = as.character(CBRFC_APRIL_JULY_STALE_AFTER_DAYS)
)))
water_year_stale_after_days <- suppressWarnings(as.integer(Sys.getenv(
  "CBRFC_FORECAST_WATER_YEAR_STALE_AFTER_DAYS",
  unset = as.character(CBRFC_WATER_YEAR_STALE_AFTER_DAYS)
)))
local_monthly_stale_after_days <- suppressWarnings(as.integer(Sys.getenv(
  "CBRFC_FORECAST_LOCAL_MONTHLY_STALE_AFTER_DAYS",
  unset = as.character(CBRFC_LOCAL_MONTHLY_STALE_AFTER_DAYS)
)))
for (setting in list(
  CBRFC_FORECAST_STALE_AFTER_DAYS = april_july_stale_after_days,
  CBRFC_FORECAST_WATER_YEAR_STALE_AFTER_DAYS = water_year_stale_after_days,
  CBRFC_FORECAST_LOCAL_MONTHLY_STALE_AFTER_DAYS = local_monthly_stale_after_days
)) {
  if (is.na(setting) || setting < 1L || setting > 90L) {
    cbrfc_stop("CBRFC source-stale day settings must be between 1 and 90.")
  }
}

tag_attempts <- function(attempts, endpoint, forecast_key) {
  lapply(attempts, function(attempt) {
    attempt$endpoint <- endpoint
    attempt$forecast_key <- forecast_key
    attempt
  })
}

result_from_error <- function(error, attempted_at, fetch_attempts) {
  outcome <- if (inherits(error, "cbrfc_source_unavailable")) {
    "source_unavailable"
  } else if (inherits(error, "cbrfc_validation_error")) {
    "validation_failed"
  } else {
    "parse_failed"
  }
  cbrfc_failure_result(
    outcome,
    conditionMessage(error),
    attempted_at,
    fetch_attempts
  )
}

build_april_july_attempt <- function(roster_row, water_year) {
  retrieval_url <- cbrfc_fill_water_year_url(
    roster_row$retrieval_url_template[[1L]], water_year
  )
  point_fetch <- cbrfc_fetch_text(
    retrieval_url,
    allowed_content_type = "application/json|text/plain|text/html",
    allow_empty = FALSE
  )
  point_fetch$attempts <- tag_attempts(
    point_fetch$attempts, "official_point", roster_row$forecast_key[[1L]]
  )
  if (!isTRUE(point_fetch$success)) {
    return(cbrfc_failure_result(
      "fetch_failed",
      paste0(point_fetch$failure_class, ": ", point_fetch$message),
      cbrfc_iso_utc(),
      point_fetch$attempts,
      "fetch"
    ))
  }
  point <- tryCatch(
    cbrfc_parse_point_response(point_fetch$text, roster_row, water_year),
    error = function(e) e
  )
  if (inherits(point, "error")) {
    return(result_from_error(point, point_fetch$retrieved_at, point_fetch$attempts))
  }
  list_fetch <- cbrfc_fetch_text(
    cbrfc_list_url(point$forecast_issue_date),
    allowed_content_type = "text/csv|text/plain|text/html",
    allow_empty = TRUE
  )
  list_fetch$attempts <- tag_attempts(
    list_fetch$attempts, "official_list_secondary", roster_row$forecast_key[[1L]]
  )
  completed_at <- if (isTRUE(list_fetch$success)) list_fetch$retrieved_at else cbrfc_iso_utc()
  list_diagnostic <- if (isTRUE(list_fetch$success)) NULL else paste0(
    list_fetch$failure_class, ": ", list_fetch$message
  )
  dashboard_fetch <- cbrfc_fetch_text(
    roster_row$summary_url[[1L]],
    allowed_content_type = "text/html",
    allow_empty = FALSE
  )
  dashboard_fetch$attempts <- tag_attempts(
    dashboard_fetch$attempts,
    "official_situational_awareness_secondary",
    roster_row$forecast_key[[1L]]
  )
  dashboard_diagnostic <- if (isTRUE(dashboard_fetch$success)) NULL else paste0(
    dashboard_fetch$failure_class, ": ", dashboard_fetch$message
  )
  completed_at <- if (isTRUE(dashboard_fetch$success)) {
    dashboard_fetch$retrieved_at
  } else {
    completed_at
  }
  record <- tryCatch(
    cbrfc_parse_response(
      point_fetch$text,
      if (isTRUE(list_fetch$success)) list_fetch$text else NULL,
      roster_row,
      completed_at,
      water_year,
      list_fetch_diagnostic = list_diagnostic,
      stale_after_days = april_july_stale_after_days,
      dashboard_html = if (isTRUE(dashboard_fetch$success)) dashboard_fetch$text else NULL,
      dashboard_fetch_diagnostic = dashboard_diagnostic
    ),
    error = function(e) e
  )
  combined_attempts <- c(
    point_fetch$attempts, list_fetch$attempts, dashboard_fetch$attempts
  )
  if (inherits(record, "error")) {
    return(result_from_error(record, completed_at, combined_attempts))
  }
  cbrfc_success_result(record, combined_attempts)
}

build_water_year_attempt <- function(roster_row, water_year) {
  retrieval_url <- cbrfc_fill_water_year_url(
    roster_row$retrieval_url_template[[1L]], water_year
  )
  source_fetch <- cbrfc_fetch_text(
    retrieval_url,
    allowed_content_type = "text/html",
    allow_empty = FALSE
  )
  source_fetch$attempts <- tag_attempts(
    source_fetch$attempts, "official_situational_awareness", roster_row$forecast_key[[1L]]
  )
  if (!isTRUE(source_fetch$success)) {
    return(cbrfc_failure_result(
      "fetch_failed",
      paste0(source_fetch$failure_class, ": ", source_fetch$message),
      cbrfc_iso_utc(),
      source_fetch$attempts,
      "fetch"
    ))
  }
  record <- tryCatch(
    cbrfc_parse_water_year_response(
      source_fetch$text,
      roster_row,
      source_fetch$retrieved_at,
      water_year,
      stale_after_days = water_year_stale_after_days
    ),
    error = function(e) e
  )
  if (inherits(record, "error")) {
    return(result_from_error(record, source_fetch$retrieved_at, source_fetch$attempts))
  }
  cbrfc_success_result(record, source_fetch$attempts)
}

build_local_monthly_attempt <- function(roster_row) {
  source_fetch <- cbrfc_fetch_text(
    roster_row$retrieval_url_template[[1L]],
    allowed_content_type = "text/csv|text/plain|application/octet-stream",
    allow_empty = FALSE
  )
  source_fetch$attempts <- tag_attempts(
    source_fetch$attempts,
    "official_lake_mead_local_monthly",
    roster_row$forecast_key[[1L]]
  )
  if (!isTRUE(source_fetch$success)) {
    return(cbrfc_failure_result(
      "fetch_failed",
      paste0(source_fetch$failure_class, ": ", source_fetch$message),
      cbrfc_iso_utc(),
      source_fetch$attempts,
      "fetch"
    ))
  }
  needs_reviewed_override <- grepl(
    "(^|\\n)7/1/2026,.*($|\\n)",
    gsub("\\r", "", source_fetch$text),
    perl = TRUE
  ) && grepl(
    "(^|\\n)2026,January,",
    gsub("\\r", "", source_fetch$text),
    perl = TRUE
  )
  evidence_fetch <- NULL
  prior_issue_text <- NULL
  completed_at <- source_fetch$retrieved_at
  if (needs_reviewed_override) {
    evidence_fetch <- cbrfc_fetch_text(
      CBRFC_LOCAL_DATE_OVERRIDE_EVIDENCE_URL,
      allowed_content_type = "text/csv|text/plain|application/octet-stream",
      allow_empty = FALSE
    )
    evidence_fetch$attempts <- tag_attempts(
      evidence_fetch$attempts,
      "official_lake_mead_local_prior_issue_evidence",
      roster_row$forecast_key[[1L]]
    )
    if (!isTRUE(evidence_fetch$success)) {
      return(cbrfc_failure_result(
        "fetch_failed",
        paste0(
          "Reviewed January correction evidence unavailable: ",
          evidence_fetch$failure_class, ": ", evidence_fetch$message
        ),
        cbrfc_iso_utc(),
        c(source_fetch$attempts, evidence_fetch$attempts),
        "fetch"
      ))
    }
    prior_issue_text <- evidence_fetch$text
    completed_at <- evidence_fetch$retrieved_at
  }
  record <- tryCatch(
    cbrfc_parse_local_monthly_response(
      source_fetch$text,
      roster_row,
      completed_at,
      stale_after_days = local_monthly_stale_after_days,
      prior_issue_text = prior_issue_text,
      override_evidence_url = CBRFC_LOCAL_DATE_OVERRIDE_EVIDENCE_URL
    ),
    error = function(e) e
  )
  combined_attempts <- c(
    source_fetch$attempts,
    if (is.null(evidence_fetch)) list() else evidence_fetch$attempts
  )
  if (inherits(record, "error")) {
    return(result_from_error(record, completed_at, combined_attempts))
  }
  cbrfc_success_result(record, combined_attempts)
}

roster <- cbrfc_read_roster(roster_path)
april_july_row <- cbrfc_roster_row(roster, "april_july_water_supply_forecast")
water_year_row <- cbrfc_roster_row(roster, "water_year_unregulated_inflow_forecast")
local_monthly_row <- cbrfc_roster_row(
  roster, "lake_mead_local_intervening_monthly_forecast"
)
prior_payload <- tryCatch(
  cbrfc_read_payload(prior_path, roster),
  error = function(e) cbrfc_stop("Existing CBRFC canonical payload is invalid: ", conditionMessage(e))
)
run_started_at <- cbrfc_iso_utc()
water_year <- cbrfc_current_water_year(cbrfc_parse_utc(run_started_at))
attempt_results <- list(
  build_april_july_attempt(april_july_row, water_year),
  build_water_year_attempt(water_year_row, water_year),
  build_local_monthly_attempt(local_monthly_row)
)

payload_or_error <- tryCatch(
  cbrfc_build_payload(
    roster,
    attempt_results,
    prior_payload,
    generated_at = cbrfc_iso_utc(),
    water_year = water_year
  ),
  error = function(e) e
)

qa_attempts <- lapply(seq_len(nrow(roster)), function(index) {
  result <- attempt_results[[index]]
  list(
    forecast_key = roster$forecast_key[[index]],
    attempt_outcome = if (isTRUE(result$success)) {
      result$record$attempt_outcome
    } else {
      result$attempt_outcome
    },
    failure_stage = if (isTRUE(result$success)) {
      result$record$failure_stage
    } else {
      result$failure_stage
    },
    message = if (isTRUE(result$success)) NULL else result$message,
    fetch_attempts = result$fetch_attempts
  )
})
qa <- list(
  schema_version = "1.0",
  run_started_at = run_started_at,
  run_completed_at = cbrfc_iso_utc(),
  output_path = output_path,
  prior_payload_path = prior_path,
  water_year = water_year,
  publication_mode = if (is.null(prior_payload)) "bootstrap" else "steady_state",
  freshness_policy = list(
    april_july_water_supply_forecast = list(
      official_cadence = "monthly",
      stale_after_calendar_days = april_july_stale_after_days,
      forecast_period_ends = sprintf("%04d-07-31", water_year),
      expires_at = cbrfc_valid_through(
        water_year, "april_july_water_supply_forecast"
      )
    ),
    water_year_unregulated_inflow_forecast = list(
      official_cadence = "monthly",
      stale_after_calendar_days = water_year_stale_after_days,
      forecast_period_ends = sprintf("%04d-09-30", water_year),
      expires_at = cbrfc_valid_through(
        water_year, "water_year_unregulated_inflow_forecast"
      )
    ),
    lake_mead_local_intervening_monthly_forecast = list(
      official_cadence = "monthly; archive history may include additional mid-month issues",
      stale_after_calendar_days = local_monthly_stale_after_days,
      item_validity = "Each forecast month is valid from its first local midnight through the next month's first local midnight.",
      series_rule = "Exactly 12 consecutive source-published months beginning with the issue month; no aggregate is calculated."
    )
  ),
  accepted = !inherits(payload_or_error, "error"),
  acceptance_error = if (inherits(payload_or_error, "error")) conditionMessage(payload_or_error) else NULL,
  family_health = if (inherits(payload_or_error, "error")) NULL else payload_or_error$family_health,
  source_summary = if (inherits(payload_or_error, "error")) NULL else payload_or_error$source_summary,
  operational_notices = if (inherits(payload_or_error, "error")) {
    NULL
  } else {
    payload_or_error$operational_notices
  },
  source_link_contract = lapply(seq_len(nrow(roster)), function(index) list(
    forecast_key = roster$forecast_key[[index]],
    source_url = roster$source_url[[index]],
    retrieval_url_template = roster$retrieval_url_template[[index]],
    summary_url = roster$summary_url[[index]],
    archive_url = roster$archive_url[[index]]
  )),
  observed_to_date_policy = "Available on the Lake Powell summary dashboard; deferred from the first-release payload.",
  attempts = qa_attempts
)
cbrfc_write_json(qa, qa_path)
message("CBRFC run diagnostics: ", qa_path)

if (inherits(payload_or_error, "error")) cbrfc_stop(conditionMessage(payload_or_error))

updated <- cbrfc_stage_and_promote_payload(payload_or_error, output_path, roster, prior_payload)
message("CBRFC output payload updated: ", if (updated) "yes" else "no")
for (index in seq_along(payload_or_error$records)) {
  record <- payload_or_error$records[[index]]
  message("CBRFC record status [", record$forecast_key, "]: ", record$status)
  message(
    "CBRFC family health [", record$product_type, "]: ",
    payload_or_error$family_health[[record$product_type]]$health
  )
}
