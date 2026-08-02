# State, freshness, validation, and publication policy for the CNRFC forecast feed.

cnrfc_parse_instant <- function(value, field_name = "timestamp") {
  if (is.null(value) || length(value) != 1L || !is.character(value) || !nzchar(value)) {
    cnrfc_stop("Missing or invalid ", field_name, ".")
  }
  normalized <- if (grepl("Z$", value)) {
    sub("Z$", "+0000", value)
  } else {
    sub("([+-][0-9]{2}):([0-9]{2})$", "\\1\\2", value)
  }
  parsed <- as.POSIXct(
    strptime(normalized, format = "%Y-%m-%dT%H:%M:%S%z", tz = "UTC"),
    tz = "UTC"
  )
  if (length(parsed) != 1L || is.na(parsed)) cnrfc_stop("Invalid ", field_name, ": ", value)
  parsed
}

cnrfc_iso_local <- function(instant) {
  offset <- format(instant, "%z", tz = "America/Los_Angeles")
  offset <- paste0(substr(offset, 1L, 3L), ":", substr(offset, 4L, 5L))
  paste0(format(instant, "%Y-%m-%dT%H:%M:%S", tz = "America/Los_Angeles"), offset)
}

cnrfc_add_hours_iso <- function(value, hours) {
  cnrfc_iso_utc(cnrfc_parse_instant(value) + as.numeric(hours) * 3600)
}

cnrfc_valid_date_through <- function(value) {
  if (is.null(value)) return(NULL)
  if (!is.character(value) || length(value) != 1L ||
      !grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", value)) {
    cnrfc_stop("Invalid forecast valid date: ", value)
  }
  date <- as.Date(value)
  if (is.na(date)) cnrfc_stop("Invalid forecast valid date: ", value)
  instant <- as.POSIXct(
    paste(as.character(date + 1L), "00:00:00"),
    format = "%Y-%m-%d %H:%M:%S",
    tz = "America/Los_Angeles"
  )
  cnrfc_iso_local(instant)
}

cnrfc_april_july_valid_through <- function(water_year) {
  water_year <- suppressWarnings(as.integer(water_year))
  if (is.na(water_year) || water_year < 1900L || water_year > 2200L) {
    cnrfc_stop("Invalid April-July water year: ", water_year)
  }
  instant <- as.POSIXct(
    sprintf("%04d-08-01 00:00:00", water_year),
    format = "%Y-%m-%d %H:%M:%S",
    tz = "America/Los_Angeles"
  )
  cnrfc_iso_local(instant)
}

cnrfc_april_july_active_season <- function(instant) {
  month <- as.integer(format(instant, "%m", tz = "America/Los_Angeles"))
  month <= 7L || month >= 10L
}

cnrfc_metric_fields <- function(product_type) {
  if (identical(product_type, "ten_day_streamflow_volume_accumulation")) {
    return(c(
      "day_3_median_volume", "day_5_median_volume", "day_10_median_volume",
      "day_3_deterministic_volume", "day_5_deterministic_volume"
    ))
  }
  if (identical(product_type, "april_july_streamflow_volume_forecast")) {
    return(c(
      "forecast_volume", "percent_average", "percent_median", "normal_average_volume"
    ))
  }
  c("forecast_volume", "percent_mean", "percent_median")
}

cnrfc_metric_date_field <- function(field) {
  unname(c(
    day_3_median_volume = "day_3_median_valid_date",
    day_5_median_volume = "day_5_median_valid_date",
    day_10_median_volume = "day_10_median_valid_date",
    day_3_deterministic_volume = "day_3_deterministic_valid_date",
    day_5_deterministic_volume = "day_5_deterministic_valid_date"
  )[[field]])
}

cnrfc_metric_state_value <- function(status,
                                     value_origin,
                                     source_issue_at = NULL,
                                     source_data_updated_at = NULL,
                                     valid_through = NULL,
                                     stale_since = NULL,
                                     missing_reason = NULL,
                                     has_value = FALSE) {
  list(
    status = status,
    value_origin = value_origin,
    source_issue_at = source_issue_at,
    source_data_updated_at = source_data_updated_at,
    valid_through = valid_through,
    stale_since = stale_since,
    map_eligible = isTRUE(has_value) && identical(status, "current"),
    popup_eligible = isTRUE(has_value) && status %in% c(
      "current", "source_stale", "stale_last_known_good", "expired"
    ),
    missing_reason = missing_reason
  )
}

cnrfc_latest_timestamp <- function(values) {
  values <- Filter(function(value) !is.null(value) && nzchar(value), values)
  if (length(values) == 0L) return(NULL)
  instants <- vapply(values, function(value) as.numeric(cnrfc_parse_instant(value)), numeric(1))
  values[[which.max(instants)]]
}

cnrfc_earliest_timestamp <- function(values) {
  values <- Filter(function(value) !is.null(value) && nzchar(value), values)
  if (length(values) == 0L) return(NULL)
  instants <- vapply(values, function(value) as.numeric(cnrfc_parse_instant(value)), numeric(1))
  values[[which.min(instants)]]
}

cnrfc_record_status_from_metrics <- function(metric_state, attempt_outcome) {
  statuses <- vapply(metric_state, function(state) state$status, character(1))
  if (length(statuses) == 0L) cnrfc_stop("Record has no metric states.")
  if (all(statuses == "current")) return("current")
  if (any(statuses == "current")) return("current_partial")
  if (any(statuses == "source_stale")) return("source_stale")
  if (any(statuses == "stale_last_known_good")) return("stale_last_known_good")
  if (any(statuses == "expired")) return("expired")
  if (identical(attempt_outcome, "source_unavailable") || all(statuses == "unavailable")) {
    return("unavailable")
  }
  "failed_no_data"
}

cnrfc_record_origin_from_metrics <- function(metric_state) {
  origins <- unique(vapply(metric_state, function(state) state$value_origin, character(1)))
  if ("last_known_good" %in% origins) return("last_known_good")
  if ("current_source" %in% origins) return("current_source")
  "none"
}

cnrfc_metric_missing_reason <- function(record, field) {
  if (grepl("deterministic", field, fixed = TRUE) &&
      !is.null(record$diagnostic$deterministic_status) &&
      !identical(record$diagnostic$deterministic_status, "available")) {
    return(record$diagnostic$deterministic_status)
  }
  paste0(field, "_unavailable")
}

cnrfc_finalize_current_record <- function(record,
                                          retrieved_at,
                                          water_year_stale_after_hours,
                                          water_year_expires_after_hours,
                                          accumulation_stale_after_hours,
                                          april_july_stale_after_hours = CNRFC_APRIL_JULY_STALE_AFTER_HOURS) {
  product_type <- record$product_type
  fields <- cnrfc_metric_fields(product_type)
  retrieved_instant <- cnrfc_parse_utc(retrieved_at)
  is_accumulation <- identical(product_type, "ten_day_streamflow_volume_accumulation")
  is_april_july <- identical(product_type, "april_july_streamflow_volume_forecast")
  source_issue_at <- record$forecast_issued_at
  source_data_updated_at <- if (is_accumulation) record$source_data_updated_at else NULL
  source_anchor <- if (!is.null(source_data_updated_at)) source_data_updated_at else source_issue_at
  has_any_value <- any(vapply(record[fields], function(value) !is.null(value), logical(1)))
  attempt_outcome <- if (has_any_value) "success" else "source_unavailable"
  failure_stage <- if (identical(attempt_outcome, "source_unavailable")) "source" else NULL

  metric_state <- stats::setNames(lapply(fields, function(field) {
    value <- record[[field]]
    valid_through <- if (is_accumulation) {
      cnrfc_valid_date_through(record[[cnrfc_metric_date_field(field)]])
    } else if (is_april_july && !is.null(record$water_year)) {
      cnrfc_april_july_valid_through(record$water_year)
    } else if (!is.null(source_issue_at)) {
      cnrfc_add_hours_iso(source_issue_at, water_year_expires_after_hours)
    } else {
      NULL
    }
    if (is.null(value)) {
      return(cnrfc_metric_state_value(
        status = "unavailable",
        value_origin = "none",
        source_issue_at = source_issue_at,
        source_data_updated_at = source_data_updated_at,
        valid_through = valid_through,
        missing_reason = cnrfc_metric_missing_reason(record, field),
        has_value = FALSE
      ))
    }
    if (is.null(source_anchor)) {
      cnrfc_stop("A current numerical metric is missing its source-time anchor: ", field)
    }
    anchor_instant <- cnrfc_parse_instant(source_anchor, "source-time anchor")
    expired <- !is.null(valid_through) &&
      retrieved_instant >= cnrfc_parse_instant(valid_through, paste0(field, " valid_through"))
    stale_after <- if (is_accumulation) {
      accumulation_stale_after_hours
    } else if (is_april_july) {
      april_july_stale_after_hours
    } else {
      water_year_stale_after_hours
    }
    stale <- as.numeric(difftime(retrieved_instant, anchor_instant, units = "hours")) > stale_after
    if (!is_accumulation && !is_april_july && !is.null(record$water_year)) {
      stale <- stale || record$water_year != cnrfc_current_water_year(retrieved_instant)
    }
    status <- if (expired) "expired" else if (stale) "source_stale" else "current"
    stale_since <- if (expired) {
      valid_through
    } else if (stale) {
      cnrfc_add_hours_iso(source_anchor, stale_after)
    } else {
      NULL
    }
    reason <- if (expired) {
      "validity_window_expired"
    } else if (stale) {
      paste0("source_age_exceeded_", as.integer(stale_after), "_hours")
    } else {
      NULL
    }
    cnrfc_metric_state_value(
      status = status,
      value_origin = "current_source",
      source_issue_at = source_issue_at,
      source_data_updated_at = source_data_updated_at,
      valid_through = valid_through,
      stale_since = stale_since,
      missing_reason = reason,
      has_value = TRUE
    )
  }), fields)

  record["last_successful_retrieval_at"] <- list(
    if (identical(attempt_outcome, "success")) retrieved_at else NULL
  )
  record$last_attempt_at <- retrieved_at
  record$attempt_outcome <- attempt_outcome
  record["failure_stage"] <- list(failure_stage)
  record$metric_state <- metric_state
  record$status <- cnrfc_record_status_from_metrics(metric_state, attempt_outcome)
  record$value_origin <- cnrfc_record_origin_from_metrics(metric_state)
  record["valid_through"] <- list(
    cnrfc_latest_timestamp(lapply(metric_state, `[[`, "valid_through"))
  )
  record["stale_since"] <- list(
    cnrfc_earliest_timestamp(lapply(metric_state, `[[`, "stale_since"))
  )
  reasons <- unique(Filter(
    function(value) !is.null(value) && nzchar(value),
    lapply(metric_state, `[[`, "missing_reason")
  ))
  record["missing_reason"] <- list(
    if (length(reasons) == 0L) NULL else paste(reasons, collapse = "; ")
  )
  record
}

cnrfc_parse_page <- function(html,
                             roster_row,
                             retrieved_at,
                             water_year_stale_after_hours = CNRFC_WATER_YEAR_STALE_AFTER_HOURS,
                             water_year_expires_after_hours = CNRFC_WATER_YEAR_EXPIRES_AFTER_HOURS,
                             accumulation_stale_after_hours = CNRFC_ACCUMULATION_STALE_AFTER_HOURS,
                             april_july_stale_after_hours = CNRFC_APRIL_JULY_STALE_AFTER_HOURS,
                             tabular_html = NULL) {
  if (nrow(roster_row) != 1L) cnrfc_stop("cnrfc_parse_page requires exactly one roster row.")
  record <- if (identical(
    roster_row$product_type[[1L]],
    "ten_day_streamflow_volume_accumulation"
  )) {
    cnrfc_parse_accumulation_page(
      html, roster_row, retrieved_at, accumulation_stale_after_hours
    )
  } else if (identical(
    roster_row$product_type[[1L]],
    "april_july_streamflow_volume_forecast"
  )) {
    if (is.null(tabular_html)) {
      cnrfc_stop("Product-7 parsing requires the matching official tabular page.")
    }
    cnrfc_parse_april_july_pages(
      html, tabular_html, roster_row, retrieved_at, april_july_stale_after_hours
    )
  } else {
    cnrfc_parse_water_year_page(
      html, roster_row, retrieved_at, water_year_stale_after_hours
    )
  }
  cnrfc_finalize_current_record(
    record,
    retrieved_at,
    water_year_stale_after_hours,
    water_year_expires_after_hours,
    accumulation_stale_after_hours,
    april_july_stale_after_hours
  )
}

cnrfc_success_result <- function(record, fetch_attempts = list()) {
  list(success = TRUE, record = record, fetch_attempts = fetch_attempts)
}

cnrfc_failure_stage_for <- function(attempt_outcome) {
  unname(c(
    source_unavailable = "source",
    fetch_failed = "fetch",
    parse_failed = "parse",
    validation_failed = "validate"
  )[[attempt_outcome]])
}

cnrfc_failure_result <- function(attempt_outcome,
                                 message,
                                 last_attempt_at,
                                 fetch_attempts = list(),
                                 failure_stage = NULL) {
  if (!attempt_outcome %in% CNRFC_ALLOWED_ATTEMPT_OUTCOMES || identical(attempt_outcome, "success")) {
    cnrfc_stop("Invalid failed-attempt outcome: ", attempt_outcome)
  }
  if (is.null(failure_stage)) failure_stage <- cnrfc_failure_stage_for(attempt_outcome)
  if (!failure_stage %in% CNRFC_ALLOWED_FAILURE_STAGES ||
      !identical(failure_stage, cnrfc_failure_stage_for(attempt_outcome))) {
    cnrfc_stop("failure_stage does not match failed-attempt outcome: ", attempt_outcome)
  }
  list(
    success = FALSE,
    attempt_outcome = attempt_outcome,
    failure_stage = failure_stage,
    message = substr(cnrfc_normalize_space(message), 1L, 500L),
    last_attempt_at = as.character(last_attempt_at),
    fetch_attempts = fetch_attempts
  )
}

cnrfc_source_anchor <- function(record) {
  if (identical(record$product_type, "ten_day_streamflow_volume_accumulation")) {
    if (!is.null(record$source_data_updated_at)) return(record$source_data_updated_at)
  }
  record$forecast_issued_at
}

cnrfc_enforce_source_monotonicity <- function(result, prior_record) {
  if (!isTRUE(result$success) || is.null(prior_record)) return(result)
  candidate_anchor <- cnrfc_source_anchor(result$record)
  prior_anchor <- cnrfc_source_anchor(prior_record)
  if (is.null(candidate_anchor) || is.null(prior_anchor)) return(result)
  if (cnrfc_parse_instant(candidate_anchor) < cnrfc_parse_instant(prior_anchor)) {
    return(cnrfc_failure_result(
      "validation_failed",
      paste0(
        "Candidate source time ", candidate_anchor,
        " moved backward from prior accepted source time ", prior_anchor, "."
      ),
      result$record$last_attempt_at,
      result$fetch_attempts,
      "validate"
    ))
  }
  result
}

cnrfc_prior_record_map <- function(prior_payload) {
  if (is.null(prior_payload) || !is.list(prior_payload$records)) return(list())
  keys <- vapply(prior_payload$records, function(record) as.character(record$forecast_key), character(1))
  stats::setNames(prior_payload$records, keys)
}

cnrfc_empty_record <- function(roster_row) {
  is_accumulation <- identical(
    roster_row$product_type[[1L]],
    "ten_day_streamflow_volume_accumulation"
  )
  is_april_july <- identical(
    roster_row$product_type[[1L]],
    "april_july_streamflow_volume_forecast"
  )
  common <- list(
    forecast_key = roster_row$forecast_key[[1L]],
    rfc = roster_row$rfc[[1L]],
    nws_lid = roster_row$nws_lid[[1L]],
    product_type = roster_row$product_type[[1L]],
    display_name = roster_row$display_name[[1L]]
  )
  product <- if (is_accumulation) {
    list(
      day_3_median_volume = NULL,
      day_3_median_valid_date = NULL,
      day_5_median_volume = NULL,
      day_5_median_valid_date = NULL,
      day_10_median_volume = NULL,
      day_10_median_valid_date = NULL,
      day_3_deterministic_volume = NULL,
      day_3_deterministic_valid_date = NULL,
      day_5_deterministic_volume = NULL,
      day_5_deterministic_valid_date = NULL,
      normalized_units = NULL,
      source_units = NULL,
      source_data_updated_at = NULL,
      forecast_issued_at = NULL
    )
  } else if (is_april_july) {
    list(
      forecast_statistic = "50_percent_exceedance",
      forecast_volume = NULL,
      normalized_units = NULL,
      source_units = NULL,
      percent_average = NULL,
      percent_median = NULL,
      normal_average_volume = NULL,
      water_year = NULL,
      forecast_period = "April-July",
      forecast_issued_at = NULL
    )
  } else {
    list(
      forecast_statistic = "median",
      forecast_volume = NULL,
      forecast_volume_units = NULL,
      percent_mean = NULL,
      percent_median = NULL,
      water_year = NULL,
      forecast_period = NULL,
      forecast_issued_at = NULL
    )
  }
  c(common, product)
}

cnrfc_failed_or_retained_record <- function(roster_row, failure, prior_record = NULL) {
  record <- if (is.null(prior_record)) cnrfc_empty_record(roster_row) else prior_record
  record$display_name <- roster_row$display_name[[1L]]
  record$source_url <- roster_row$source_url[[1L]]
  fields <- cnrfc_metric_fields(record$product_type)
  attempt_instant <- cnrfc_parse_instant(failure$last_attempt_at, "last_attempt_at")
  prior_states <- if (is.list(record$metric_state)) record$metric_state else list()
  source_issue_at <- record$forecast_issued_at
  source_data_updated_at <- if (identical(
    record$product_type,
    "ten_day_streamflow_volume_accumulation"
  )) record$source_data_updated_at else NULL
  is_april_july <- identical(
    record$product_type,
    "april_july_streamflow_volume_forecast"
  )
  prior_has_any_value <- !is.null(prior_record) && any(vapply(
    prior_record[fields], function(value) !is.null(value), logical(1)
  ))

  metric_state <- stats::setNames(lapply(fields, function(field) {
    value <- record[[field]]
    prior_state <- prior_states[[field]]
    valid_through <- if (!is.null(prior_state$valid_through)) {
      prior_state$valid_through
    } else if (identical(record$product_type, "ten_day_streamflow_volume_accumulation")) {
      cnrfc_valid_date_through(record[[cnrfc_metric_date_field(field)]])
    } else if (is_april_july && !is.null(record$water_year)) {
      cnrfc_april_july_valid_through(record$water_year)
    } else if (!is.null(source_issue_at)) {
      cnrfc_add_hours_iso(source_issue_at, CNRFC_WATER_YEAR_EXPIRES_AFTER_HOURS)
    } else {
      NULL
    }
    if (is.null(value)) {
      prior_unavailable <- prior_has_any_value && !is.null(prior_state) &&
        identical(prior_state$status, "unavailable")
      status <- if (identical(failure$attempt_outcome, "source_unavailable") || prior_unavailable) {
        "unavailable"
      } else {
        "failed_no_data"
      }
      return(cnrfc_metric_state_value(
        status = status,
        value_origin = "none",
        source_issue_at = source_issue_at,
        source_data_updated_at = source_data_updated_at,
        valid_through = valid_through,
        missing_reason = paste0(failure$attempt_outcome, ": ", failure$message),
        has_value = FALSE
      ))
    }
    expired <- !is.null(valid_through) &&
      attempt_instant >= cnrfc_parse_instant(valid_through, paste0(field, " valid_through"))
    status <- if (expired) "expired" else "stale_last_known_good"
    stale_since <- if (expired) {
      valid_through
    } else if (!is.null(prior_state$stale_since) &&
               identical(prior_state$status, "stale_last_known_good")) {
      prior_state$stale_since
    } else {
      failure$last_attempt_at
    }
    cnrfc_metric_state_value(
      status = status,
      value_origin = "last_known_good",
      source_issue_at = source_issue_at,
      source_data_updated_at = source_data_updated_at,
      valid_through = valid_through,
      stale_since = stale_since,
      missing_reason = paste0(failure$attempt_outcome, ": ", failure$message),
      has_value = TRUE
    )
  }), fields)

  record["last_successful_retrieval_at"] <- list(if (!is.null(prior_record)) {
    prior_record$last_successful_retrieval_at
  } else NULL)
  record$last_attempt_at <- failure$last_attempt_at
  record$attempt_outcome <- failure$attempt_outcome
  record$failure_stage <- failure$failure_stage
  record$metric_state <- metric_state
  record$status <- cnrfc_record_status_from_metrics(metric_state, failure$attempt_outcome)
  record$value_origin <- cnrfc_record_origin_from_metrics(metric_state)
  record["valid_through"] <- list(
    cnrfc_latest_timestamp(lapply(metric_state, `[[`, "valid_through"))
  )
  record["stale_since"] <- list(
    cnrfc_earliest_timestamp(lapply(metric_state, `[[`, "stale_since"))
  )
  record$missing_reason <- paste0(failure$attempt_outcome, ": ", failure$message)
  if (is.null(record$source_page_signature)) record["source_page_signature"] <- list(NULL)
  record$diagnostic <- list(
    parser = if (identical(
      record$product_type,
      "ten_day_streamflow_volume_accumulation"
    )) {
      "cnrfc_prod2_semantic_v1"
    } else if (is_april_july) {
      "cnrfc_prod7_semantic_v2"
    } else {
      "cnrfc_prod9_semantic_v1"
    },
    source_page_title = paste0("CNRFC - Ensemble Products - ", roster_row$nws_lid[[1L]]),
    source_product_label = NULL,
    expected_product_label = roster_row$expected_product_label[[1L]],
    error_class = failure$attempt_outcome,
    error_message = failure$message,
    failure_stage = failure$failure_stage
  )
  record
}

cnrfc_required_record_fields <- function(product_type) {
  common <- c(
    "forecast_key", "rfc", "nws_lid", "product_type", "display_name",
    "last_successful_retrieval_at", "last_attempt_at", "attempt_outcome",
    "failure_stage", "stale_since", "valid_through", "value_origin",
    "metric_state", "status", "missing_reason", "source_url",
    "source_page_signature", "diagnostic"
  )
  if (identical(product_type, "ten_day_streamflow_volume_accumulation")) {
    return(c(
      common[1:5],
      "day_3_median_volume", "day_3_median_valid_date",
      "day_5_median_volume", "day_5_median_valid_date",
      "day_10_median_volume", "day_10_median_valid_date",
      "day_3_deterministic_volume", "day_3_deterministic_valid_date",
      "day_5_deterministic_volume", "day_5_deterministic_valid_date",
      "normalized_units", "source_units", "source_data_updated_at", "forecast_issued_at",
      common[6:length(common)]
    ))
  }
  if (identical(product_type, "april_july_streamflow_volume_forecast")) {
    return(c(
      common[1:5],
      "forecast_statistic", "forecast_volume", "normalized_units", "source_units",
      "percent_average", "percent_median", "normal_average_volume", "water_year", "forecast_period",
      "forecast_issued_at",
      common[6:length(common)]
    ))
  }
  c(
    common[1:5],
    "forecast_statistic", "forecast_volume", "forecast_volume_units",
    "percent_mean", "percent_median", "water_year", "forecast_period", "forecast_issued_at",
    common[6:length(common)]
  )
}

cnrfc_record_has_valid_forecast <- function(record, roster_row) {
  if (!is.list(record)) return(FALSE)
  identity_ok <- identical(record$forecast_key, roster_row$forecast_key[[1L]]) &&
    identical(record$rfc, roster_row$rfc[[1L]]) &&
    identical(record$nws_lid, roster_row$nws_lid[[1L]]) &&
    identical(record$product_type, roster_row$product_type[[1L]])
  if (!identity_ok) return(FALSE)
  fields <- cnrfc_metric_fields(record$product_type)
  any(vapply(record[fields], function(value) {
    is.numeric(value) && length(value) == 1L && is.finite(value) && value >= 0
  }, logical(1)))
}

cnrfc_validate_metric_state <- function(record, field) {
  state <- record$metric_state[[field]]
  required <- c(
    "status", "value_origin", "source_issue_at", "source_data_updated_at",
    "valid_through", "stale_since", "map_eligible", "popup_eligible", "missing_reason"
  )
  if (!is.list(state) || !all(required %in% names(state))) {
    cnrfc_stop("Metric state is incomplete for ", record$forecast_key, "/", field, ".")
  }
  if (!state$status %in% CNRFC_ALLOWED_METRIC_STATUSES) {
    cnrfc_stop("Invalid metric status for ", record$forecast_key, "/", field, ".")
  }
  if (!state$value_origin %in% CNRFC_ALLOWED_VALUE_ORIGINS) {
    cnrfc_stop("Invalid metric value_origin for ", record$forecast_key, "/", field, ".")
  }
  if (!is.logical(state$map_eligible) || length(state$map_eligible) != 1L ||
      !is.logical(state$popup_eligible) || length(state$popup_eligible) != 1L) {
    cnrfc_stop("Metric eligibility flags must be scalar booleans.")
  }
  value <- record[[field]]
  has_value <- !is.null(value)
  expected_map <- has_value && identical(state$status, "current")
  expected_popup <- has_value && state$status %in% c(
    "current", "source_stale", "stale_last_known_good", "expired"
  )
  if (!identical(state$map_eligible, expected_map) ||
      !identical(state$popup_eligible, expected_popup)) {
    cnrfc_stop("Metric eligibility does not match status/value for ", record$forecast_key, "/", field, ".")
  }
  if (has_value && identical(state$value_origin, "none")) {
    cnrfc_stop("A numerical metric cannot have value_origin none.")
  }
  if (!has_value && !identical(state$value_origin, "none")) {
    cnrfc_stop("A null metric must have value_origin none.")
  }
  if (has_value && state$status %in% c("unavailable", "failed_no_data")) {
    cnrfc_stop("Unavailable/failed_no_data metric cannot retain a numerical value.")
  }
  if (!has_value && state$status %in% c(
    "current", "source_stale", "stale_last_known_good", "expired"
  )) {
    cnrfc_stop("Usable/provenance metric status requires a numerical value.")
  }
  if (has_value && is.null(state$valid_through)) {
    cnrfc_stop("A numerical metric is missing valid_through: ", record$forecast_key, "/", field, ".")
  }
  if (!identical(state$status, "current") && is.null(state$missing_reason)) {
    cnrfc_stop("A noncurrent metric requires missing_reason: ", record$forecast_key, "/", field, ".")
  }
  expected_issue <- record$forecast_issued_at
  expected_updated <- if (identical(
    record$product_type,
    "ten_day_streamflow_volume_accumulation"
  )) record$source_data_updated_at else NULL
  if (!identical(state$source_issue_at, expected_issue) ||
      !identical(state$source_data_updated_at, expected_updated)) {
    cnrfc_stop("Metric source time does not reconcile to its record: ", record$forecast_key, "/", field, ".")
  }
  if (!is.null(state$valid_through)) cnrfc_parse_instant(state$valid_through, "metric valid_through")
  invisible(TRUE)
}

cnrfc_validate_record <- function(record, roster_row) {
  product_type <- roster_row$product_type[[1L]]
  is_accumulation <- identical(product_type, "ten_day_streamflow_volume_accumulation")
  is_april_july <- identical(product_type, "april_july_streamflow_volume_forecast")
  missing <- setdiff(cnrfc_required_record_fields(product_type), names(record))
  if (length(missing) > 0L) {
    cnrfc_stop("Record ", roster_row$forecast_key[[1L]], " is missing field(s): ", paste(missing, collapse = ", "))
  }
  if (!record$status %in% CNRFC_ALLOWED_STATUSES) {
    cnrfc_stop("Record ", roster_row$forecast_key[[1L]], " has invalid status: ", record$status)
  }
  if (!record$attempt_outcome %in% CNRFC_ALLOWED_ATTEMPT_OUTCOMES) {
    cnrfc_stop("Record has an invalid attempt_outcome: ", record$forecast_key)
  }
  expected_stage <- if (identical(record$attempt_outcome, "success")) {
    NULL
  } else {
    cnrfc_failure_stage_for(record$attempt_outcome)
  }
  if (!identical(record$failure_stage, expected_stage)) {
    cnrfc_stop("failure_stage does not match attempt_outcome: ", record$forecast_key)
  }
  if (!identical(record$forecast_key, roster_row$forecast_key[[1L]]) ||
      !identical(record$rfc, roster_row$rfc[[1L]]) ||
      !identical(record$nws_lid, roster_row$nws_lid[[1L]]) ||
      !identical(record$product_type, product_type) ||
      !identical(record$source_url, roster_row$source_url[[1L]])) {
    cnrfc_stop("Record identity/source does not match roster: ", roster_row$forecast_key[[1L]])
  }
  fields <- cnrfc_metric_fields(product_type)
  if (!is.list(record$metric_state) || !identical(names(record$metric_state), fields)) {
    cnrfc_stop("Metric-state keys/order do not match display metrics: ", record$forecast_key)
  }
  for (field in fields) {
    value <- record[[field]]
    if (!is.null(value) && (!is.numeric(value) || length(value) != 1L || !is.finite(value) || value < 0)) {
      cnrfc_stop("Record ", record$forecast_key, " has invalid numeric field ", field, ".")
    }
    cnrfc_validate_metric_state(record, field)
  }
  derived_status <- cnrfc_record_status_from_metrics(record$metric_state, record$attempt_outcome)
  derived_origin <- cnrfc_record_origin_from_metrics(record$metric_state)
  if (!identical(record$status, derived_status) || !identical(record$value_origin, derived_origin)) {
    cnrfc_stop("Record summary status/origin does not reconcile to metric states: ", record$forecast_key)
  }
  if (is.null(record$last_attempt_at)) cnrfc_stop("Record is missing last_attempt_at: ", record$forecast_key)
  cnrfc_parse_instant(record$last_attempt_at, "last_attempt_at")
  if (!is.null(record$last_successful_retrieval_at)) {
    cnrfc_parse_instant(record$last_successful_retrieval_at, "last_successful_retrieval_at")
  }
  if (identical(record$attempt_outcome, "success") && is.null(record$last_successful_retrieval_at)) {
    cnrfc_stop("Successful record is missing last_successful_retrieval_at: ", record$forecast_key)
  }
  if (identical(record$status, "unavailable") &&
      !identical(record$attempt_outcome, "source_unavailable")) {
    cnrfc_stop("Record unavailable is reserved for source-confirmed unavailability.")
  }
  expected_valid_through <- cnrfc_latest_timestamp(lapply(record$metric_state, `[[`, "valid_through"))
  expected_stale_since <- cnrfc_earliest_timestamp(lapply(record$metric_state, `[[`, "stale_since"))
  if (!identical(record$valid_through, expected_valid_through) ||
      !identical(record$stale_since, expected_stale_since)) {
    cnrfc_stop("Record freshness summary does not reconcile to metric states: ", record$forecast_key)
  }
  if (!is_accumulation && !is_april_july && !identical(record$forecast_statistic, "median")) {
    cnrfc_stop("Record ", record$forecast_key, " must preserve the source Median Forecast statistic.")
  }
  if (is_april_july) {
    if (!identical(record$forecast_statistic, "50_percent_exceedance")) {
      cnrfc_stop("Product-7 forecast_statistic must be 50_percent_exceedance.")
    }
    if (!identical(record$forecast_period, "April-July")) {
      cnrfc_stop("Product-7 forecast_period must preserve the April-July source period.")
    }
    if ("percent_mean" %in% names(record)) {
      cnrfc_stop(
        "Product-7 must not substitute percent_mean for the legacy percent_average wire field."
      )
    }
    values <- Filter(Negate(is.null), record[fields])
    if (length(values) > 0L) {
      if (!identical(record$normalized_units, "kaf") || is.null(record$source_units)) {
        cnrfc_stop("Product-7 units must preserve source_units and normalize only to kaf.")
      }
      cnrfc_normalize_seasonal_units(record$source_units)
    }
    if (!is.null(record$water_year) &&
        (!is.integer(record$water_year) || length(record$water_year) != 1L)) {
      cnrfc_stop("Product-7 water_year must be a scalar integer.")
    }
  }
  if (is_accumulation) {
    if (any(c("day_10_deterministic_volume", "day_10_deterministic_valid_date") %in% names(record))) {
      cnrfc_stop("A deterministic Day 10 field must not be published or inferred.")
    }
    pairs <- list(
      c("day_3_median_volume", "day_3_median_valid_date"),
      c("day_5_median_volume", "day_5_median_valid_date"),
      c("day_10_median_volume", "day_10_median_valid_date"),
      c("day_3_deterministic_volume", "day_3_deterministic_valid_date"),
      c("day_5_deterministic_volume", "day_5_deterministic_valid_date")
    )
    for (pair in pairs) {
      if (!identical(is.null(record[[pair[[1L]]]]), is.null(record[[pair[[2L]]]]))) {
        cnrfc_stop("Accumulated-volume value/date availability did not match for ", pair[[1L]], ".")
      }
      if (!is.null(record[[pair[[2L]]]]) &&
          !grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", record[[pair[[2L]]]])) {
        cnrfc_stop("Accumulated-volume valid date was not YYYY-MM-DD for ", pair[[1L]], ".")
      }
    }
    cnrfc_validate_cumulative_series(
      record[c("day_3_median_volume", "day_5_median_volume", "day_10_median_volume")],
      "Selected median"
    )
    cnrfc_validate_cumulative_series(
      record[c("day_3_deterministic_volume", "day_5_deterministic_volume")],
      "Selected deterministic"
    )
    values <- Filter(Negate(is.null), record[fields])
    if (length(values) > 0L && any(vapply(values, function(value) {
      abs(value * 10 - round(value * 10)) > 1e-8
    }, logical(1)))) {
      cnrfc_stop("Product-2 values must be rounded to one decimal place before serialization.")
    }
    if (length(values) > 0L) {
      if (!identical(record$normalized_units, "kaf") || is.null(record$source_units)) {
        cnrfc_stop("Accumulated-volume units must preserve source_units and normalize only to kaf.")
      }
      cnrfc_normalize_accumulation_units(record$source_units)
    }
  }
  invisible(TRUE)
}

cnrfc_record_primary_missing <- function(record) {
  fields <- if (identical(
    record$product_type,
    "ten_day_streamflow_volume_accumulation"
  )) {
    c("day_3_median_volume", "day_5_median_volume", "day_10_median_volume")
  } else if (identical(record$product_type, "april_july_streamflow_volume_forecast")) {
    c("forecast_volume", "percent_average", "percent_median", "normal_average_volume")
  } else {
    c("forecast_volume", "percent_mean", "percent_median")
  }
  any(vapply(record[fields], is.null, logical(1)))
}

cnrfc_family_expected_available <- function(product_type, generated_at = NULL) {
  if (identical(product_type, "ten_day_streamflow_volume_accumulation")) return(14L)
  if (identical(product_type, "water_year_fnf")) return(15L)
  if (identical(product_type, "water_year_index")) return(3L)
  if (identical(product_type, "april_july_streamflow_volume_forecast")) {
    if (is.null(generated_at)) cnrfc_stop("April-July family health requires generated_at.")
    return(if (cnrfc_april_july_active_season(cnrfc_parse_instant(generated_at))) 18L else 0L)
  }
  cnrfc_stop("Unknown CNRFC product family: ", product_type)
}

cnrfc_family_summary <- function(records, product_type, generated_at = NULL) {
  family <- Filter(function(record) identical(record$product_type, product_type), records)
  statuses <- vapply(family, function(record) record$status, character(1))
  expected_structural <- length(family)
  expected_available <- cnrfc_family_expected_available(product_type, generated_at)
  successful <- sum(vapply(family, function(record) identical(record$attempt_outcome, "success"), logical(1)))
  current_count <- sum(statuses == "current")
  partial_count <- sum(statuses == "current_partial")
  source_stale_count <- sum(statuses == "source_stale")
  lkg_count <- sum(statuses == "stale_last_known_good")
  expired_count <- sum(statuses == "expired")
  unavailable_count <- sum(vapply(family, function(record) {
    identical(record$attempt_outcome, "source_unavailable")
  }, logical(1)))
  failed_count <- sum(statuses == "failed_no_data")
  expected_unavailable <- expected_structural - expected_available
  april_july_inactive <- identical(product_type, "april_july_streamflow_volume_forecast") &&
    identical(expected_available, 0L)
  healthy <- if (april_july_inactive) {
    current_count == 0L && partial_count == 0L && source_stale_count == 0L &&
      lkg_count == 0L && failed_count == 0L &&
      expired_count + unavailable_count == expected_structural &&
      successful + unavailable_count == expected_structural
  } else {
    current_count + partial_count == expected_available &&
      unavailable_count == expected_unavailable && lkg_count == 0L &&
      expired_count == 0L && source_stale_count == 0L && failed_count == 0L
  }
  health <- if (healthy) {
    "healthy"
  } else if (current_count + partial_count + source_stale_count > 0L) {
    "degraded"
  } else if (lkg_count + expired_count > 0L) {
    "outage_using_last_known_good"
  } else {
    "unusable"
  }
  summary <- list(
    expected_structural_count = as.integer(expected_structural),
    expected_available_count = as.integer(expected_available),
    current_count = as.integer(current_count),
    current_partial_count = as.integer(partial_count),
    source_stale_count = as.integer(source_stale_count),
    stale_last_known_good_count = as.integer(lkg_count),
    expired_count = as.integer(expired_count),
    source_unavailable_count = as.integer(unavailable_count),
    failed_no_data_count = as.integer(failed_count),
    successful_attempt_count = as.integer(successful),
    failed_attempt_count = as.integer(expected_structural - successful),
    health = health
  )
  if (identical(product_type, "april_july_streamflow_volume_forecast")) {
    summary$season_state <- if (april_july_inactive) "inactive" else "active"
  }
  if (identical(product_type, "ten_day_streamflow_volume_accumulation")) {
    median_fields <- c("day_3_median_volume", "day_5_median_volume", "day_10_median_volume")
    deterministic_fields <- c("day_3_deterministic_volume", "day_5_deterministic_volume")
    summary$median_success_count <- as.integer(sum(vapply(family, function(record) {
      identical(record$attempt_outcome, "success") &&
        all(!vapply(record[median_fields], is.null, logical(1)))
    }, logical(1))))
    summary$deterministic_success_count <- as.integer(sum(vapply(family, function(record) {
      identical(record$attempt_outcome, "success") &&
        all(!vapply(record[deterministic_fields], is.null, logical(1)))
    }, logical(1))))
  }
  summary
}

cnrfc_source_summary <- function(records) {
  success_count <- function(product_type) sum(vapply(records, function(record) {
    identical(record$product_type, product_type) && identical(record$attempt_outcome, "success")
  }, logical(1)))
  accumulation <- Filter(function(record) {
    identical(record$product_type, "ten_day_streamflow_volume_accumulation")
  }, records)
  median_fields <- c("day_3_median_volume", "day_5_median_volume", "day_10_median_volume")
  deterministic_fields <- c("day_3_deterministic_volume", "day_5_deterministic_volume")
  statuses <- vapply(records, function(record) record$status, character(1))
  list(
    rfc = "CNRFC",
    successful_attempt_count = as.integer(sum(vapply(
      records, function(record) identical(record$attempt_outcome, "success"), logical(1)
    ))),
    failed_attempt_count = as.integer(sum(vapply(
      records, function(record) !identical(record$attempt_outcome, "success"), logical(1)
    ))),
    water_year_fnf_success_count = as.integer(success_count("water_year_fnf")),
    water_year_index_success_count = as.integer(success_count("water_year_index")),
    april_july_success_count = as.integer(success_count("april_july_streamflow_volume_forecast")),
    accumulation_median_success_count = as.integer(sum(vapply(accumulation, function(record) {
      identical(record$attempt_outcome, "success") &&
        all(!vapply(record[median_fields], is.null, logical(1)))
    }, logical(1)))),
    accumulation_deterministic_success_count = as.integer(sum(vapply(accumulation, function(record) {
      identical(record$attempt_outcome, "success") &&
        all(!vapply(record[deterministic_fields], is.null, logical(1)))
    }, logical(1)))),
    current_count = as.integer(sum(statuses == "current")),
    current_partial_count = as.integer(sum(statuses == "current_partial")),
    source_stale_count = as.integer(sum(statuses == "source_stale")),
    stale_last_known_good_count = as.integer(sum(statuses == "stale_last_known_good")),
    expired_count = as.integer(sum(statuses == "expired")),
    source_unavailable_count = as.integer(sum(vapply(records, function(record) {
      identical(record$attempt_outcome, "source_unavailable")
    }, logical(1)))),
    failed_no_data_count = as.integer(sum(statuses == "failed_no_data"))
  )
}

cnrfc_operational_notices <- function(records) {
  mhbc <- Filter(function(record) {
    identical(record$forecast_key, "CNRFC:MHBC1:10D_VOLUME_ACCUM")
  }, records)
  if (length(mhbc) == 1L && identical(mhbc[[1L]]$attempt_outcome, "success") &&
      !cnrfc_record_primary_missing(mhbc[[1L]])) {
    return(list(list(
      code = "product_2_expected_availability_changed",
      forecast_key = mhbc[[1L]]$forecast_key,
      message = "MHBC1 now publishes a valid product-2 median table; review the expected-availability roster."
    )))
  }
  list()
}

cnrfc_records_same_except_attempt_times <- function(candidate, prior) {
  if (is.null(prior)) return(FALSE)
  candidate_view <- candidate
  prior_view <- prior
  candidate_view$last_successful_retrieval_at <- NULL
  candidate_view$last_attempt_at <- NULL
  prior_view$last_successful_retrieval_at <- NULL
  prior_view$last_attempt_at <- NULL
  identical(
    jsonlite::toJSON(candidate_view, auto_unbox = TRUE, null = "null", na = "null", digits = NA),
    jsonlite::toJSON(prior_view, auto_unbox = TRUE, null = "null", na = "null", digits = NA)
  )
}

cnrfc_preserve_noop_timestamps <- function(candidate, prior) {
  if (!cnrfc_records_same_except_attempt_times(candidate, prior)) return(candidate)
  candidate["last_successful_retrieval_at"] <- list(prior$last_successful_retrieval_at)
  candidate["last_attempt_at"] <- list(prior$last_attempt_at)
  candidate
}

cnrfc_bootstrap_acceptance <- function(records, generated_at) {
  established <- function(record, fields) {
    identical(record$attempt_outcome, "success") &&
      all(!vapply(record[fields], is.null, logical(1))) &&
      all(vapply(record$metric_state[fields], function(state) {
        state$status %in% c("current", "source_stale")
      }, logical(1)))
  }
  fnf <- Filter(function(record) identical(record$product_type, "water_year_fnf"), records)
  index <- Filter(function(record) identical(record$product_type, "water_year_index"), records)
  accumulation <- Filter(function(record) {
    identical(record$product_type, "ten_day_streamflow_volume_accumulation")
  }, records)
  april_july <- Filter(function(record) {
    identical(record$product_type, "april_july_streamflow_volume_forecast")
  }, records)
  april_july_active <- cnrfc_april_july_active_season(cnrfc_parse_instant(generated_at))
  accumulation_expected <- Filter(function(record) !identical(record$nws_lid, "MHBC1"), accumulation)
  mhbc <- Filter(function(record) identical(record$nws_lid, "MHBC1"), accumulation)
  water_fields <- c("forecast_volume", "percent_mean", "percent_median")
  median_fields <- c("day_3_median_volume", "day_5_median_volume", "day_10_median_volume")
  april_july_fields <- c(
    "forecast_volume", "percent_average", "percent_median", "normal_average_volume"
  )
  april_july_acceptable <- function(record) {
    if (april_july_active) return(established(record, april_july_fields))
    established_expired <- identical(record$attempt_outcome, "success") &&
      all(!vapply(record[april_july_fields], is.null, logical(1))) &&
      all(vapply(record$metric_state[april_july_fields], function(state) {
        identical(state$status, "expired")
      }, logical(1)))
    explicit_unavailable <- identical(record$attempt_outcome, "source_unavailable") &&
      identical(record$status, "unavailable")
    established_expired || explicit_unavailable
  }
  counts <- list(
    water_year_fnf = sum(vapply(fnf, established, logical(1), fields = water_fields)),
    water_year_index = sum(vapply(index, established, logical(1), fields = water_fields)),
    accumulation_median = sum(vapply(
      accumulation_expected, established, logical(1), fields = median_fields
    )),
    april_july = sum(vapply(april_july, april_july_acceptable, logical(1))),
    april_july_active = april_july_active,
    mhbc_source_unavailable = length(mhbc) == 1L &&
      identical(mhbc[[1L]]$attempt_outcome, "source_unavailable") &&
      identical(mhbc[[1L]]$status, "unavailable")
  )
  accepted <- identical(as.integer(counts$water_year_fnf), 15L) &&
    identical(as.integer(counts$water_year_index), 3L) &&
    identical(as.integer(counts$accumulation_median), 14L) &&
    identical(as.integer(counts$april_july), 18L) &&
    isTRUE(counts$mhbc_source_unavailable)
  list(accepted = accepted, counts = counts)
}

cnrfc_validate_payload <- function(payload, roster) {
  required_root <- c(
    "schema_version", "product_id", "roster_version", "generated_at",
    "publication_mode", "expected_record_count", "actual_record_count",
    "source_summary", "family_health", "operational_notices", "records"
  )
  if (!is.list(payload) || !all(required_root %in% names(payload))) {
    cnrfc_stop("CNRFC forecast payload is missing required top-level fields.")
  }
  if (!identical(payload$schema_version, CNRFC_FORECAST_SCHEMA_VERSION) ||
      !identical(payload$product_id, CNRFC_FORECAST_PRODUCT_ID) ||
      !identical(payload$roster_version, CNRFC_ROSTER_VERSION)) {
    cnrfc_stop("Unsupported CNRFC forecast schema, product, or roster version.")
  }
  if (!payload$publication_mode %in% c("bootstrap", "steady_state")) {
    cnrfc_stop("Invalid publication_mode.")
  }
  cnrfc_parse_utc(payload$generated_at)
  if (!identical(as.integer(payload$expected_record_count), 51L) ||
      !identical(as.integer(payload$actual_record_count), 51L) ||
      !is.list(payload$records) || length(payload$records) != 51L) {
    cnrfc_stop("CNRFC forecast payload must contain exactly 51 structural records.")
  }
  keys <- vapply(payload$records, function(record) as.character(record$forecast_key), character(1))
  if (anyDuplicated(keys) || !identical(keys, roster$forecast_key)) {
    cnrfc_stop("CNRFC payload ordering/identity does not match the reviewed roster.")
  }
  for (index in seq_len(nrow(roster))) {
    cnrfc_validate_record(payload$records[[index]], roster[index, , drop = FALSE])
  }
  serialized_names <- unique(unlist(lapply(payload$records, names), use.names = FALSE))
  forbidden <- grep(
    "geometry|polygon|coordinates|latitude|longitude|component|normal_value|ensemble_member|flow_rate",
    serialized_names,
    ignore.case = TRUE,
    value = TRUE
  )
  if (length(forbidden) > 0L) {
    cnrfc_stop(paste0(
      "Geometry, derived arithmetic/reference, and ensemble fields are forbidden: ",
      paste(forbidden, collapse = ", ")
    ))
  }
  family_names <- c(
    "water_year_fnf", "water_year_index", "ten_day_streamflow_volume_accumulation",
    "april_july_streamflow_volume_forecast"
  )
  if (!is.list(payload$family_health) || !identical(names(payload$family_health), family_names)) {
    cnrfc_stop("family_health is missing a reviewed product family.")
  }
  for (family_name in family_names) {
    expected <- cnrfc_family_summary(payload$records, family_name, payload$generated_at)
    actual <- payload$family_health[[family_name]]
    if (!identical(
      jsonlite::toJSON(actual, auto_unbox = TRUE, null = "null", digits = NA),
      jsonlite::toJSON(expected, auto_unbox = TRUE, null = "null", digits = NA)
    )) {
      cnrfc_stop("family_health counts do not reconcile for ", family_name, ".")
    }
  }
  expected_summary <- cnrfc_source_summary(payload$records)
  if (!identical(
    jsonlite::toJSON(payload$source_summary, auto_unbox = TRUE, null = "null", digits = NA),
    jsonlite::toJSON(expected_summary, auto_unbox = TRUE, null = "null", digits = NA)
  )) {
    cnrfc_stop("CNRFC source_summary counts do not reconcile to records.")
  }
  invisible(TRUE)
}

cnrfc_build_payload <- function(roster,
                                attempt_results,
                                prior_payload = NULL,
                                generated_at = cnrfc_iso_utc()) {
  expected_keys <- roster$forecast_key
  if (!is.list(attempt_results) || is.null(names(attempt_results)) ||
      !setequal(names(attempt_results), expected_keys)) {
    cnrfc_stop("Attempt results must be a named complete set matching the reviewed roster.")
  }
  if (!is.null(prior_payload)) cnrfc_validate_payload(prior_payload, roster)
  prior_map <- cnrfc_prior_record_map(prior_payload)
  records <- vector("list", nrow(roster))
  for (index in seq_len(nrow(roster))) {
    row <- roster[index, , drop = FALSE]
    key <- row$forecast_key[[1L]]
    result <- cnrfc_enforce_source_monotonicity(attempt_results[[key]], prior_map[[key]])
    record <- if (isTRUE(result$success)) {
      result$record
    } else {
      cnrfc_failed_or_retained_record(row, result, prior_map[[key]])
    }
    records[[index]] <- cnrfc_preserve_noop_timestamps(record, prior_map[[key]])
  }
  family_names <- c(
    "water_year_fnf", "water_year_index", "ten_day_streamflow_volume_accumulation",
    "april_july_streamflow_volume_forecast"
  )
  family_health <- stats::setNames(lapply(
    family_names, function(product_type) cnrfc_family_summary(records, product_type, generated_at)
  ), family_names)
  mode <- if (is.null(prior_payload)) "bootstrap" else "steady_state"
  payload <- list(
    schema_version = CNRFC_FORECAST_SCHEMA_VERSION,
    product_id = CNRFC_FORECAST_PRODUCT_ID,
    roster_version = CNRFC_ROSTER_VERSION,
    generated_at = generated_at,
    publication_mode = mode,
    expected_record_count = 51L,
    actual_record_count = length(records),
    source_summary = cnrfc_source_summary(records),
    family_health = family_health,
    operational_notices = cnrfc_operational_notices(records),
    records = records
  )
  cnrfc_validate_payload(payload, roster)
  if (identical(mode, "bootstrap")) {
    acceptance <- cnrfc_bootstrap_acceptance(records, generated_at)
    if (!isTRUE(acceptance$accepted)) {
      cnrfc_stop(
        "Bootstrap acceptance failed: ", acceptance$counts$water_year_fnf,
        "/15 water_year_fnf established, ", acceptance$counts$water_year_index,
        "/3 water_year_index established, ", acceptance$counts$accumulation_median,
        "/14 expected-available accumulation median series established, MHBC1 source-unavailable=",
        tolower(as.character(acceptance$counts$mhbc_source_unavailable)),
        ", ", acceptance$counts$april_july,
        "/18 April-July records acceptable (active-season=",
        tolower(as.character(acceptance$counts$april_july_active)), ")",
        ". Prior canonical payload retained."
      )
    }
  }
  payload
}

cnrfc_upgrade_legacy_product7_percent_median <- function(payload) {
  if (!is.list(payload) || !is.list(payload$records)) return(payload)
  upgraded <- FALSE
  for (index in seq_along(payload$records)) {
    record <- payload$records[[index]]
    if (!identical(record$product_type, "april_july_streamflow_volume_forecast")) next
    field_missing <- !"percent_median" %in% names(record)
    state_missing <- is.list(record$metric_state) &&
      !"percent_median" %in% names(record$metric_state)
    if (!field_missing || !state_missing) next
    field_position <- match("percent_average", names(record))
    state_position <- match("percent_average", names(record$metric_state))
    if (is.na(field_position) || is.na(state_position)) next

    reason <- "percent_median_unavailable_in_prior_contract"
    record <- append(record, list(percent_median = NULL), after = field_position)
    legacy_state <- cnrfc_metric_state_value(
      status = "unavailable",
      value_origin = "none",
      source_issue_at = record$forecast_issued_at,
      source_data_updated_at = NULL,
      valid_through = if (is.null(record$water_year)) {
        NULL
      } else {
        cnrfc_april_july_valid_through(record$water_year)
      },
      missing_reason = reason,
      has_value = FALSE
    )
    record$metric_state <- append(
      record$metric_state,
      list(percent_median = legacy_state),
      after = state_position
    )
    record$status <- cnrfc_record_status_from_metrics(record$metric_state, record$attempt_outcome)
    record$value_origin <- cnrfc_record_origin_from_metrics(record$metric_state)
    record["valid_through"] <- list(
      cnrfc_latest_timestamp(lapply(record$metric_state, `[[`, "valid_through"))
    )
    record["stale_since"] <- list(
      cnrfc_earliest_timestamp(lapply(record$metric_state, `[[`, "stale_since"))
    )
    if (is.null(record$missing_reason)) record$missing_reason <- reason
    payload$records[[index]] <- record
    upgraded <- TRUE
  }
  if (!upgraded) return(payload)

  family_names <- c(
    "water_year_fnf", "water_year_index", "ten_day_streamflow_volume_accumulation",
    "april_july_streamflow_volume_forecast"
  )
  payload$source_summary <- cnrfc_source_summary(payload$records)
  payload$family_health <- stats::setNames(lapply(
    family_names,
    function(product_type) cnrfc_family_summary(
      payload$records, product_type, payload$generated_at
    )
  ), family_names)
  payload
}

cnrfc_read_payload <- function(path, roster) {
  if (!file.exists(path)) return(NULL)
  payload <- jsonlite::fromJSON(path, simplifyVector = FALSE)
  payload <- cnrfc_upgrade_legacy_product7_percent_median(payload)
  cnrfc_validate_payload(payload, roster)
  payload
}

cnrfc_substantive_view <- function(payload) {
  view <- payload
  view$generated_at <- NULL
  view
}

cnrfc_payload_changed <- function(candidate, prior_payload) {
  if (is.null(prior_payload)) return(TRUE)
  candidate_json <- jsonlite::toJSON(
    cnrfc_substantive_view(candidate),
    auto_unbox = TRUE,
    null = "null",
    na = "null",
    digits = NA
  )
  prior_json <- jsonlite::toJSON(
    cnrfc_substantive_view(prior_payload),
    auto_unbox = TRUE,
    null = "null",
    na = "null",
    digits = NA
  )
  !identical(candidate_json, prior_json)
}

cnrfc_write_json <- function(object, path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  jsonlite::write_json(
    object,
    path = path,
    auto_unbox = TRUE,
    null = "null",
    na = "null",
    pretty = TRUE,
    digits = NA
  )
}

cnrfc_stage_and_promote_payload <- function(payload, output_path, roster, prior_payload = NULL) {
  if (!cnrfc_payload_changed(payload, prior_payload)) {
    message("No substantive CNRFC forecast changes; output payload left byte-for-byte unchanged.")
    return(FALSE)
  }
  dir.create(dirname(output_path), recursive = TRUE, showWarnings = FALSE)
  staged <- tempfile(
    ".major-water-supply-basin-forecasts-",
    tmpdir = dirname(output_path),
    fileext = ".json"
  )
  on.exit(if (file.exists(staged)) unlink(staged), add = TRUE)
  cnrfc_write_json(payload, staged)
  staged_payload <- cnrfc_read_payload(staged, roster)
  cnrfc_validate_payload(staged_payload, roster)
  if (!file.rename(staged, output_path)) {
    cnrfc_stop("Could not promote the validated CNRFC forecast payload to ", output_path, ".")
  }
  TRUE
}
