# State, freshness, validation, and publication policy for the CBRFC feed.

cbrfc_parse_instant <- function(value, field_name = "timestamp") {
  if (is.null(value) || !is.character(value) || length(value) != 1L || !nzchar(value)) {
    cbrfc_stop("Missing or invalid ", field_name, ".")
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
  if (length(parsed) != 1L || is.na(parsed)) cbrfc_stop("Invalid ", field_name, ": ", value)
  parsed
}

cbrfc_iso_local <- function(instant) {
  offset <- format(instant, "%z", tz = "America/Los_Angeles")
  offset <- paste0(substr(offset, 1L, 3L), ":", substr(offset, 4L, 5L))
  paste0(format(instant, "%Y-%m-%dT%H:%M:%S", tz = "America/Los_Angeles"), offset)
}

cbrfc_parse_date <- function(value, field_name = "date") {
  if (is.null(value) || !is.character(value) || length(value) != 1L ||
      !grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", value)) {
    cbrfc_stop("Missing or invalid ", field_name, ".")
  }
  parsed <- as.Date(value)
  if (is.na(parsed)) cbrfc_stop("Invalid ", field_name, ": ", value)
  parsed
}

cbrfc_policy_midnight <- function(date) {
  instant <- as.POSIXct(
    paste(as.character(date), "00:00:00"),
    format = "%Y-%m-%d %H:%M:%S",
    tz = "America/Los_Angeles"
  )
  cbrfc_iso_local(instant)
}

cbrfc_stale_at <- function(issue_date, stale_after_days) {
  cbrfc_policy_midnight(cbrfc_parse_date(issue_date, "forecast_issue_date") + as.integer(stale_after_days))
}

cbrfc_valid_through <- function(water_year, product_type = "april_july_water_supply_forecast") {
  water_year <- suppressWarnings(as.integer(water_year))
  if (is.na(water_year) || water_year < 1900L || water_year > 2200L) {
    cbrfc_stop("Invalid CBRFC water_year: ", water_year)
  }
  end_date <- switch(
    product_type,
    april_july_water_supply_forecast = sprintf("%04d-08-01", water_year),
    water_year_unregulated_inflow_forecast = sprintf("%04d-10-01", water_year),
    cbrfc_stop("Unsupported CBRFC product_type for validity: ", product_type)
  )
  cbrfc_policy_midnight(as.Date(end_date))
}

cbrfc_active_season <- function(instant, product_type = "april_july_water_supply_forecast") {
  month <- as.integer(format(instant, "%m", tz = "America/Los_Angeles"))
  switch(
    product_type,
    april_july_water_supply_forecast = month <= 7L,
    water_year_unregulated_inflow_forecast = month <= 9L,
    lake_mead_local_intervening_monthly_forecast = TRUE,
    cbrfc_stop("Unsupported CBRFC product_type for season state: ", product_type)
  )
}

cbrfc_metric_fields <- function(product_type = "april_july_water_supply_forecast") {
  switch(
    product_type,
    april_july_water_supply_forecast = c(
      "forecast_volume", "percent_average", "percent_median"
    ),
    water_year_unregulated_inflow_forecast = c(
      "forecast_volume", "percent_average"
    ),
    lake_mead_local_intervening_monthly_forecast = character(),
    cbrfc_stop("Unsupported CBRFC product_type for metric fields: ", product_type)
  )
}

cbrfc_metric_state_value <- function(status,
                                     value_origin,
                                     source_issue_date = NULL,
                                     valid_through = NULL,
                                     stale_since = NULL,
                                     missing_reason = NULL,
                                     has_value = FALSE) {
  list(
    status = status,
    value_origin = value_origin,
    source_issue_date = source_issue_date,
    valid_through = valid_through,
    stale_since = stale_since,
    map_eligible = isTRUE(has_value) && identical(status, "current"),
    popup_eligible = isTRUE(has_value) && status %in% c(
      "current", "not_yet_valid", "source_stale", "stale_last_known_good", "expired"
    ),
    missing_reason = missing_reason
  )
}

cbrfc_record_status_from_metrics <- function(metric_state, attempt_outcome) {
  statuses <- vapply(metric_state, `[[`, character(1), "status")
  if (all(statuses == "current")) return("current")
  if (any(statuses == "source_stale")) return("source_stale")
  if (any(statuses == "stale_last_known_good")) return("stale_last_known_good")
  if (any(statuses == "expired")) return("expired")
  if (identical(attempt_outcome, "source_unavailable") || all(statuses == "unavailable")) {
    return("unavailable")
  }
  "failed_no_data"
}

cbrfc_record_origin_from_metrics <- function(metric_state) {
  origins <- unique(vapply(metric_state, `[[`, character(1), "value_origin"))
  if ("last_known_good" %in% origins) return("last_known_good")
  if ("current_source" %in% origins) return("current_source")
  "none"
}

cbrfc_finalize_current_record <- function(record,
                                          retrieved_at,
                                          stale_after_days = CBRFC_STALE_AFTER_DAYS) {
  retrieved_instant <- cbrfc_parse_utc(retrieved_at)
  issue_date <- cbrfc_parse_date(record$forecast_issue_date, "forecast_issue_date")
  retrieved_date <- as.Date(format(retrieved_instant, "%Y-%m-%d", tz = "America/Los_Angeles"))
  if (issue_date > retrieved_date) {
    cbrfc_validation_error("CBRFC forecast_issue_date was later than retrieval date.")
  }
  stale_since <- cbrfc_stale_at(record$forecast_issue_date, stale_after_days)
  valid_through <- cbrfc_valid_through(record$water_year, record$product_type)
  expired <- retrieved_instant >= cbrfc_parse_instant(valid_through, "valid_through")
  stale <- retrieved_instant >= cbrfc_parse_instant(stale_since, "stale_since")
  metric_status <- if (expired) "expired" else if (stale) "source_stale" else "current"
  reason <- if (expired) {
    if (identical(record$product_type, "april_july_water_supply_forecast")) {
      "forecast_period_expired_after_july_31"
    } else {
      "water_year_forecast_expired_after_september_30"
    }
  } else if (stale) {
    paste0("source_age_exceeded_", as.integer(stale_after_days), "_calendar_days")
  } else {
    NULL
  }
  fields <- cbrfc_metric_fields(record$product_type)
  metric_state <- stats::setNames(lapply(fields, function(field) {
    cbrfc_metric_state_value(
      status = metric_status,
      value_origin = "current_source",
      source_issue_date = record$forecast_issue_date,
      valid_through = valid_through,
      stale_since = if (identical(metric_status, "current")) NULL else {
        if (expired) valid_through else stale_since
      },
      missing_reason = reason,
      has_value = TRUE
    )
  }), fields)
  record$last_successful_retrieval_at <- retrieved_at
  record$last_attempt_at <- retrieved_at
  record$attempt_outcome <- "success"
  record["failure_stage"] <- list(NULL)
  record$metric_state <- metric_state
  record$status <- cbrfc_record_status_from_metrics(metric_state, "success")
  record$value_origin <- cbrfc_record_origin_from_metrics(metric_state)
  record$valid_through <- valid_through
  record["stale_since"] <- list(if (identical(metric_status, "current")) NULL else {
    if (expired) valid_through else stale_since
  })
  record["missing_reason"] <- list(reason)
  record
}

cbrfc_month_validity <- function(forecast_month) {
  if (!is.character(forecast_month) || length(forecast_month) != 1L ||
      !grepl("^[0-9]{4}-[0-9]{2}$", forecast_month)) {
    cbrfc_stop("Invalid CBRFC forecast_month: ", forecast_month)
  }
  start <- as.Date(paste0(forecast_month, "-01"))
  if (is.na(start) || !identical(format(start, "%Y-%m"), forecast_month)) {
    cbrfc_stop("Invalid CBRFC forecast_month: ", forecast_month)
  }
  next_month <- seq(start, by = "month", length.out = 2L)[[2L]]
  list(
    valid_from = cbrfc_policy_midnight(start),
    valid_through = cbrfc_policy_midnight(next_month)
  )
}

cbrfc_monthly_item_status <- function(item, issue_date, retrieved_instant, stale_since) {
  bounds <- cbrfc_month_validity(item$forecast_month)
  future <- retrieved_instant < cbrfc_parse_instant(bounds$valid_from, "monthly valid_from")
  expired <- retrieved_instant >= cbrfc_parse_instant(bounds$valid_through, "monthly valid_through")
  stale <- retrieved_instant >= cbrfc_parse_instant(stale_since, "monthly stale_since")
  status <- if (expired) {
    "expired"
  } else if (stale) {
    "source_stale"
  } else if (future) {
    "not_yet_valid"
  } else {
    "current"
  }
  reason <- if (expired) {
    "forecast_month_expired"
  } else if (stale) {
    "monthly_source_age_exceeded_reviewed_threshold"
  } else if (future) {
    "forecast_month_not_yet_valid"
  } else {
    NULL
  }
  states <- stats::setNames(lapply(c("forecast_volume", "percent_median"), function(field) {
    cbrfc_metric_state_value(
      status = status,
      value_origin = "current_source",
      source_issue_date = issue_date,
      valid_through = bounds$valid_through,
      stale_since = if (expired) {
        bounds$valid_through
      } else if (stale) {
        stale_since
      } else {
        NULL
      },
      missing_reason = reason,
      has_value = TRUE
    )
  }), c("forecast_volume", "percent_median"))
  item$valid_from <- bounds$valid_from
  item$valid_through <- bounds$valid_through
  item$metric_state <- states
  item$status <- status
  item$value_origin <- "current_source"
  item["stale_since"] <- list(if (expired) {
    bounds$valid_through
  } else if (stale) {
    stale_since
  } else {
    NULL
  })
  item["missing_reason"] <- list(reason)
  item
}

cbrfc_monthly_record_status <- function(items, attempt_outcome) {
  if (length(items) == 0L) {
    return(if (identical(attempt_outcome, "source_unavailable")) "unavailable" else "failed_no_data")
  }
  statuses <- vapply(items, `[[`, character(1), "status")
  if (all(statuses == "current")) return("current")
  if (any(statuses == "current")) return("current_partial")
  if (any(statuses == "source_stale")) return("source_stale")
  if (any(statuses == "stale_last_known_good")) return("stale_last_known_good")
  if (any(statuses == "expired")) return("expired")
  if (identical(attempt_outcome, "source_unavailable")) "unavailable" else "failed_no_data"
}

cbrfc_finalize_monthly_record <- function(record,
                                          retrieved_at,
                                          stale_after_days = CBRFC_LOCAL_MONTHLY_STALE_AFTER_DAYS) {
  retrieved_instant <- cbrfc_parse_utc(retrieved_at)
  issue_date <- cbrfc_parse_date(record$forecast_issue_date, "forecast_issue_date")
  retrieved_date <- as.Date(format(retrieved_instant, "%Y-%m-%d", tz = "America/Los_Angeles"))
  if (issue_date > retrieved_date) {
    cbrfc_validation_error("CBRFC Lake Mead Local forecast_issue_date was later than retrieval date.")
  }
  stale_since <- cbrfc_stale_at(record$forecast_issue_date, stale_after_days)
  record$monthly_forecasts <- lapply(
    record$monthly_forecasts,
    cbrfc_monthly_item_status,
    issue_date = record$forecast_issue_date,
    retrieved_instant = retrieved_instant,
    stale_since = stale_since
  )
  statuses <- vapply(record$monthly_forecasts, `[[`, character(1), "status")
  record$last_successful_retrieval_at <- retrieved_at
  record$last_attempt_at <- retrieved_at
  record$attempt_outcome <- "success"
  record["failure_stage"] <- list(NULL)
  record$status <- cbrfc_monthly_record_status(record$monthly_forecasts, "success")
  record$value_origin <- "current_source"
  record$valid_through <- tail(record$monthly_forecasts, 1L)[[1L]]$valid_through
  record["stale_since"] <- list(if (all(statuses == "expired")) {
    record$valid_through
  } else if (any(statuses == "source_stale")) {
    stale_since
  } else {
    NULL
  })
  record["missing_reason"] <- list(if (identical(record$status, "current")) NULL else {
    if (identical(record$status, "current_partial")) {
      "one_or_more_forecast_months_not_current"
    } else if (identical(record$status, "source_stale")) {
      "monthly_source_age_exceeded_reviewed_threshold"
    } else {
      "all_forecast_months_expired"
    }
  })
  record
}

cbrfc_failure_stage_for <- function(attempt_outcome) {
  unname(c(
    source_unavailable = "source",
    fetch_failed = "fetch",
    parse_failed = "parse",
    validation_failed = "validate"
  )[[attempt_outcome]])
}

cbrfc_failure_result <- function(attempt_outcome,
                                 message,
                                 last_attempt_at,
                                 fetch_attempts = list(),
                                 failure_stage = NULL) {
  if (!attempt_outcome %in% CBRFC_ALLOWED_ATTEMPT_OUTCOMES ||
      identical(attempt_outcome, "success")) {
    cbrfc_stop("Invalid CBRFC failed-attempt outcome: ", attempt_outcome)
  }
  if (is.null(failure_stage)) failure_stage <- cbrfc_failure_stage_for(attempt_outcome)
  if (!identical(failure_stage, cbrfc_failure_stage_for(attempt_outcome))) {
    cbrfc_stop("CBRFC failure_stage does not match attempt_outcome.")
  }
  list(
    success = FALSE,
    attempt_outcome = attempt_outcome,
    failure_stage = failure_stage,
    message = substr(cbrfc_normalize_space(message), 1L, 500L),
    last_attempt_at = last_attempt_at,
    fetch_attempts = fetch_attempts
  )
}

cbrfc_success_result <- function(record, fetch_attempts = list()) {
  list(success = TRUE, record = record, fetch_attempts = fetch_attempts)
}

cbrfc_enforce_source_monotonicity <- function(result, prior_record) {
  if (!isTRUE(result$success) || is.null(prior_record) ||
      is.null(prior_record$forecast_issue_date)) return(result)
  candidate <- cbrfc_parse_date(result$record$forecast_issue_date, "candidate forecast_issue_date")
  prior <- cbrfc_parse_date(prior_record$forecast_issue_date, "prior forecast_issue_date")
  if (candidate < prior) {
    return(cbrfc_failure_result(
      "validation_failed",
      paste0(
        "Candidate source date ", result$record$forecast_issue_date,
        " moved backward from prior accepted source date ", prior_record$forecast_issue_date, "."
      ),
      result$record$last_attempt_at,
      result$fetch_attempts,
      "validate"
    ))
  }
  result
}

cbrfc_empty_record <- function(roster_row, water_year) {
  if (identical(
    roster_row$product_type[[1L]], "lake_mead_local_intervening_monthly_forecast"
  )) {
    return(list(
      forecast_key = roster_row$forecast_key[[1L]],
      rfc = roster_row$rfc[[1L]],
      nws_lid = roster_row$nws_lid[[1L]],
      source_identifier = NULL,
      product_type = roster_row$product_type[[1L]],
      display_name = roster_row$display_name[[1L]],
      forecast_statistic = "50_percent_exceedance",
      normalized_units = NULL,
      source_units = NULL,
      forecast_issue_date = NULL,
      source_time_precision = "date",
      source_normal_term = roster_row$source_normal_term[[1L]],
      forecast_period = roster_row$forecast_period[[1L]],
      water_year = as.integer(water_year),
      forecast_type = roster_row$expected_forecast_type[[1L]],
      monthly_forecasts = list(),
      source_url = roster_row$source_url[[1L]],
      retrieval_url = roster_row$retrieval_url_template[[1L]],
      summary_url = roster_row$summary_url[[1L]],
      archive_url = roster_row$archive_url[[1L]],
      source_page_signature = NULL
    ))
  }
  record <- list(
    forecast_key = roster_row$forecast_key[[1L]],
    rfc = roster_row$rfc[[1L]],
    nws_lid = roster_row$nws_lid[[1L]],
    product_type = roster_row$product_type[[1L]],
    display_name = roster_row$display_name[[1L]],
    forecast_statistic = if (identical(
      roster_row$product_type[[1L]], "april_july_water_supply_forecast"
    )) "50_percent_exceedance" else "official_full_forecast",
    forecast_volume = NULL,
    normalized_units = NULL,
    source_units = NULL,
    percent_average = NULL,
    forecast_issue_date = NULL,
    source_time_precision = "date",
    source_normal_term = roster_row$source_normal_term[[1L]],
    forecast_period = roster_row$forecast_period[[1L]],
    water_year = as.integer(water_year),
    forecast_type = roster_row$expected_forecast_type[[1L]],
    source_url = cbrfc_fill_water_year_url(roster_row$source_url[[1L]], water_year),
    retrieval_url = cbrfc_fill_water_year_url(roster_row$retrieval_url_template[[1L]], water_year),
    summary_url = roster_row$summary_url[[1L]],
    archive_url = roster_row$archive_url[[1L]],
    source_page_signature = NULL
  )
  if (identical(roster_row$product_type[[1L]], "april_july_water_supply_forecast")) {
    record["percent_median"] <- list(NULL)
    ordered <- c(
      "forecast_key", "rfc", "nws_lid", "product_type", "display_name",
      "forecast_statistic", "forecast_volume", "normalized_units", "source_units",
      "percent_average", "percent_median", "forecast_issue_date", "source_time_precision",
      "source_normal_term", "forecast_period", "water_year", "forecast_type", "source_url",
      "retrieval_url", "summary_url", "archive_url", "source_page_signature"
    )
    record <- record[ordered]
  }
  record
}

cbrfc_failed_or_retained_monthly_record <- function(roster_row,
                                                     failure,
                                                     prior_record = NULL,
                                                     water_year) {
  record <- if (is.null(prior_record)) {
    cbrfc_empty_record(roster_row, water_year)
  } else {
    prior_record
  }
  attempt_instant <- cbrfc_parse_instant(failure$last_attempt_at, "last_attempt_at")
  reason <- paste0(failure$attempt_outcome, ": ", failure$message)
  if (length(record$monthly_forecasts) > 0L) {
    record$monthly_forecasts <- lapply(record$monthly_forecasts, function(item) {
      expired <- attempt_instant >= cbrfc_parse_instant(item$valid_through, "monthly valid_through")
      status <- if (expired) "expired" else "stale_last_known_good"
      stale_since <- if (expired) {
        item$valid_through
      } else if (identical(item$status, "stale_last_known_good") && !is.null(item$stale_since)) {
        item$stale_since
      } else {
        failure$last_attempt_at
      }
      item$metric_state <- stats::setNames(lapply(
        c("forecast_volume", "percent_median"),
        function(field) cbrfc_metric_state_value(
          status = status,
          value_origin = "last_known_good",
          source_issue_date = record$forecast_issue_date,
          valid_through = item$valid_through,
          stale_since = stale_since,
          missing_reason = reason,
          has_value = TRUE
        )
      ), c("forecast_volume", "percent_median"))
      item$status <- status
      item$value_origin <- "last_known_good"
      item$stale_since <- stale_since
      item$missing_reason <- reason
      item
    })
  }
  record["last_successful_retrieval_at"] <- list(if (is.null(prior_record)) NULL else {
    prior_record$last_successful_retrieval_at
  })
  record$last_attempt_at <- failure$last_attempt_at
  record$attempt_outcome <- failure$attempt_outcome
  record$failure_stage <- failure$failure_stage
  record$status <- cbrfc_monthly_record_status(record$monthly_forecasts, failure$attempt_outcome)
  record$value_origin <- if (length(record$monthly_forecasts) > 0L) "last_known_good" else "none"
  record["valid_through"] <- list(if (length(record$monthly_forecasts) > 0L) {
    tail(record$monthly_forecasts, 1L)[[1L]]$valid_through
  } else {
    NULL
  })
  stale_values <- Filter(
    function(value) !is.null(value),
    lapply(record$monthly_forecasts, `[[`, "stale_since")
  )
  record["stale_since"] <- list(if (length(stale_values) > 0L) stale_values[[1L]] else NULL)
  record$missing_reason <- reason
  record$diagnostic <- list(
    parser = "cbrfc_lksa3_local_monthly_semantic_v1",
    source_name = roster_row$expected_source_name[[1L]],
    source_identifier = if (is.null(record$source_identifier)) "LKSA3 QCMPLCM" else {
      record$source_identifier
    },
    source_forecast_label = "ESP 50% EXCEEDANCE",
    source_percentage_label = "%Med",
    source_date_override_count = as.integer(sum(vapply(
      record$monthly_forecasts,
      function(item) isTRUE(item$source_date_override_applied),
      logical(1)
    ))),
    source_date_override_ids = unique(Filter(
      Negate(is.null),
      lapply(record$monthly_forecasts, `[[`, "source_date_override_id")
    )),
    error_class = failure$attempt_outcome,
    error_message = failure$message,
    failure_stage = failure$failure_stage
  )
  record
}

cbrfc_failed_or_retained_record <- function(roster_row,
                                            failure,
                                            prior_record = NULL,
                                            water_year) {
  if (identical(
    roster_row$product_type[[1L]], "lake_mead_local_intervening_monthly_forecast"
  )) {
    return(cbrfc_failed_or_retained_monthly_record(
      roster_row, failure, prior_record, water_year
    ))
  }
  record <- if (is.null(prior_record)) {
    cbrfc_empty_record(roster_row, water_year)
  } else {
    prior_record
  }
  fields <- cbrfc_metric_fields(record$product_type)
  attempt_instant <- cbrfc_parse_instant(failure$last_attempt_at, "last_attempt_at")
  prior_states <- if (is.list(record$metric_state)) record$metric_state else list()
  metric_state <- stats::setNames(lapply(fields, function(field) {
    value <- record[[field]]
    prior_state <- prior_states[[field]]
    valid_through <- if (!is.null(prior_state$valid_through)) {
      prior_state$valid_through
    } else {
      cbrfc_valid_through(record$water_year, record$product_type)
    }
    if (is.null(value)) {
      status <- if (identical(failure$attempt_outcome, "source_unavailable")) {
        "unavailable"
      } else {
        "failed_no_data"
      }
      return(cbrfc_metric_state_value(
        status = status,
        value_origin = "none",
        source_issue_date = record$forecast_issue_date,
        valid_through = valid_through,
        missing_reason = paste0(failure$attempt_outcome, ": ", failure$message),
        has_value = FALSE
      ))
    }
    expired <- attempt_instant >= cbrfc_parse_instant(valid_through, "valid_through")
    status <- if (expired) "expired" else "stale_last_known_good"
    stale_since <- if (expired) {
      valid_through
    } else if (!is.null(prior_state$stale_since) &&
               identical(prior_state$status, "stale_last_known_good")) {
      prior_state$stale_since
    } else {
      failure$last_attempt_at
    }
    cbrfc_metric_state_value(
      status = status,
      value_origin = "last_known_good",
      source_issue_date = record$forecast_issue_date,
      valid_through = valid_through,
      stale_since = stale_since,
      missing_reason = paste0(failure$attempt_outcome, ": ", failure$message),
      has_value = TRUE
    )
  }), fields)
  record["last_successful_retrieval_at"] <- list(if (is.null(prior_record)) NULL else {
    prior_record$last_successful_retrieval_at
  })
  record$last_attempt_at <- failure$last_attempt_at
  record$attempt_outcome <- failure$attempt_outcome
  record$failure_stage <- failure$failure_stage
  record$metric_state <- metric_state
  record$status <- cbrfc_record_status_from_metrics(metric_state, failure$attempt_outcome)
  record$value_origin <- cbrfc_record_origin_from_metrics(metric_state)
  record$valid_through <- cbrfc_valid_through(record$water_year, record$product_type)
  stale_values <- Filter(
    function(value) !is.null(value),
    lapply(metric_state, `[[`, "stale_since")
  )
  record["stale_since"] <- list(
    if (length(stale_values) == 0L) NULL else stale_values[[1L]]
  )
  record$missing_reason <- paste0(failure$attempt_outcome, ": ", failure$message)
  record$diagnostic <- if (identical(
    roster_row$product_type[[1L]], "april_july_water_supply_forecast"
  )) {
    list(
      parser = "cbrfc_glda3_official_v1",
      source_name = roster_row$expected_source_name[[1L]],
      official_field_prefix = "off",
      secondary_list_status = NULL,
      secondary_list_diagnostic = NULL,
      error_class = failure$attempt_outcome,
      error_message = failure$message,
      failure_stage = failure$failure_stage
    )
  } else {
    list(
      parser = "cbrfc_glda3_water_year_semantic_v1",
      source_name = roster_row$expected_source_name[[1L]],
      semantic_row_label = "Water Year",
      source_forecast_label = "Full Fcst",
      source_percentage_label = "%Avg",
      error_class = failure$attempt_outcome,
      error_message = failure$message,
      failure_stage = failure$failure_stage
    )
  }
  record
}

cbrfc_records_same_except_attempt_times <- function(candidate, prior) {
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

cbrfc_preserve_noop_timestamps <- function(candidate, prior) {
  if (!cbrfc_records_same_except_attempt_times(candidate, prior)) return(candidate)
  candidate$last_successful_retrieval_at <- prior$last_successful_retrieval_at
  candidate$last_attempt_at <- prior$last_attempt_at
  candidate
}

cbrfc_validate_metric_state <- function(record, field) {
  state <- record$metric_state[[field]]
  required <- c(
    "status", "value_origin", "source_issue_date", "valid_through", "stale_since",
    "map_eligible", "popup_eligible", "missing_reason"
  )
  if (!is.list(state) || !all(required %in% names(state))) {
    cbrfc_stop("CBRFC metric state is incomplete for ", field, ".")
  }
  if (!state$status %in% CBRFC_ALLOWED_STATUSES ||
      !state$value_origin %in% c("current_source", "last_known_good", "none")) {
    cbrfc_stop("CBRFC metric state status or value_origin is invalid.")
  }
  value <- record[[field]]
  has_value <- !is.null(value)
  expected_map <- has_value && identical(state$status, "current")
  expected_popup <- has_value && state$status %in% c(
    "current", "source_stale", "stale_last_known_good", "expired"
  )
  if (!identical(state$map_eligible, expected_map) ||
      !identical(state$popup_eligible, expected_popup)) {
    cbrfc_stop("CBRFC metric eligibility does not reconcile for ", field, ".")
  }
  if (!identical(state$source_issue_date, record$forecast_issue_date)) {
    cbrfc_stop("CBRFC metric source issue date does not reconcile for ", field, ".")
  }
  if (!identical(state$valid_through, record$valid_through)) {
    cbrfc_stop("CBRFC metric validity does not reconcile for ", field, ".")
  }
  if (has_value && (!is.numeric(value) || length(value) != 1L || !is.finite(value) || value < 0)) {
    cbrfc_stop("CBRFC metric is not a finite nonnegative number: ", field)
  }
  if (has_value && identical(state$value_origin, "none")) {
    cbrfc_stop("CBRFC numerical metric cannot have value_origin none.")
  }
  if (!has_value && !identical(state$value_origin, "none")) {
    cbrfc_stop("CBRFC null metric must have value_origin none.")
  }
  if (!state$status %in% c("current") && is.null(state$missing_reason)) {
    cbrfc_stop("CBRFC noncurrent metric requires missing_reason.")
  }
  cbrfc_parse_instant(state$valid_through, "metric valid_through")
  invisible(TRUE)
}

cbrfc_required_record_fields <- function(product_type) {
  common <- c(
    "forecast_key", "rfc", "nws_lid", "product_type", "display_name",
    "forecast_statistic", "forecast_volume", "normalized_units", "source_units",
    "percent_average", "forecast_issue_date", "source_time_precision",
    "source_normal_term", "forecast_period", "water_year", "forecast_type",
    "last_successful_retrieval_at", "last_attempt_at", "attempt_outcome", "failure_stage",
    "stale_since", "valid_through", "value_origin", "metric_state", "status",
    "missing_reason", "source_url", "retrieval_url", "summary_url", "archive_url",
    "source_page_signature", "diagnostic"
  )
  if (identical(product_type, "april_july_water_supply_forecast")) {
    append(common, "percent_median", after = match("percent_average", common))
  } else {
    common
  }
}

cbrfc_validate_monthly_record <- function(record, roster_row) {
  required <- c(
    "forecast_key", "rfc", "nws_lid", "source_identifier", "product_type",
    "display_name", "forecast_statistic", "normalized_units", "source_units",
    "forecast_issue_date", "source_time_precision", "source_normal_term",
    "forecast_period", "water_year", "forecast_type", "monthly_forecasts",
    "last_successful_retrieval_at", "last_attempt_at", "attempt_outcome",
    "failure_stage", "stale_since", "valid_through", "value_origin", "status",
    "missing_reason", "source_url", "retrieval_url", "archive_url",
    "summary_url", "source_page_signature", "diagnostic"
  )
  missing <- setdiff(required, names(record))
  unexpected <- setdiff(names(record), required)
  if (length(missing) > 0L || length(unexpected) > 0L) {
    cbrfc_stop(
      "CBRFC Lake Mead Local record fields changed; missing: ",
      paste(missing, collapse = ", "), "; unexpected: ", paste(unexpected, collapse = ", ")
    )
  }
  if (!record$status %in% CBRFC_ALLOWED_STATUSES ||
      !record$attempt_outcome %in% CBRFC_ALLOWED_ATTEMPT_OUTCOMES) {
    cbrfc_stop("CBRFC Lake Mead Local status or attempt outcome is invalid.")
  }
  expected_stage <- if (identical(record$attempt_outcome, "success")) NULL else {
    cbrfc_failure_stage_for(record$attempt_outcome)
  }
  if (!identical(record$failure_stage, expected_stage) ||
      !identical(record$forecast_key, roster_row$forecast_key[[1L]]) ||
      !identical(record$rfc, "CBRFC") || !identical(record$nws_lid, "LKSA3") ||
      !identical(record$product_type, "lake_mead_local_intervening_monthly_forecast") ||
      !identical(record$forecast_statistic, "50_percent_exceedance") ||
      !identical(record$source_time_precision, "date") ||
      !identical(record$source_normal_term, "median") ||
      !identical(record$forecast_period, "MONTHLY OUTLOOKS") ||
      !identical(record$forecast_type, "ESP 50% EXCEEDANCE") ||
      !identical(record$source_url, roster_row$source_url[[1L]]) ||
      !identical(record$retrieval_url, roster_row$retrieval_url_template[[1L]]) ||
      !identical(record$summary_url, roster_row$summary_url[[1L]]) ||
      !identical(record$archive_url, roster_row$archive_url[[1L]])) {
    cbrfc_stop("CBRFC Lake Mead Local identity, semantics, or URLs changed.")
  }
  if (!is.integer(record$water_year) || length(record$water_year) != 1L ||
      !is.list(record$monthly_forecasts)) {
    cbrfc_stop("CBRFC Lake Mead Local water year or monthly series is invalid.")
  }
  has_values <- length(record$monthly_forecasts) > 0L
  if (!has_values) {
    if (!is.null(record$source_identifier) || !is.null(record$normalized_units) ||
        !is.null(record$source_units) || !is.null(record$forecast_issue_date) ||
        !is.null(record$valid_through) || !is.null(record$source_page_signature) ||
        !identical(record$value_origin, "none") ||
        !record$status %in% c("unavailable", "failed_no_data")) {
      cbrfc_stop("CBRFC Lake Mead Local empty record fabricated source values or provenance.")
    }
  } else {
    if (length(record$monthly_forecasts) != 12L ||
        !identical(record$source_identifier, "LKSA3 QCMPLCM") ||
        !identical(record$normalized_units, "kaf") ||
        !identical(record$source_units, "KAF") ||
        is.null(record$forecast_issue_date) || is.null(record$source_page_signature)) {
      cbrfc_stop("CBRFC Lake Mead Local populated record lacks its exact reviewed series provenance.")
    }
    issue_date <- cbrfc_parse_date(record$forecast_issue_date, "forecast_issue_date")
    expected_water_year <- as.integer(format(issue_date, "%Y")) +
      as.integer(as.integer(format(issue_date, "%m")) >= 10L)
    if (!identical(record$water_year, as.integer(expected_water_year))) {
      cbrfc_stop("CBRFC Lake Mead Local water year does not reconcile to its issue date.")
    }
    expected_months <- format(
      seq(as.Date(paste0(substr(record$forecast_issue_date, 1L, 7L), "-01")),
          by = "month", length.out = 12L),
      "%Y-%m"
    )
    actual_months <- vapply(record$monthly_forecasts, `[[`, character(1), "forecast_month")
    if (!identical(actual_months, expected_months)) {
      cbrfc_stop("CBRFC Lake Mead Local monthly order is not consecutive from the issue month.")
    }
    item_fields <- c(
      "raw_forecast_month_label", "forecast_month", "source_date_override_applied",
      "source_date_override_id", "source_date_override_reason",
      "source_date_override_evidence_url", "source_date_override_prior_issue_date",
      "forecast_volume", "percent_median", "source_statistic",
      "source_percentage_label", "valid_from",
      "valid_through", "metric_state", "status", "value_origin", "stale_since",
      "missing_reason"
    )
    for (item in record$monthly_forecasts) {
      if (!identical(names(item), item_fields) ||
          !is.character(item$raw_forecast_month_label) ||
          length(item$raw_forecast_month_label) != 1L ||
          !is.logical(item$source_date_override_applied) ||
          length(item$source_date_override_applied) != 1L ||
          !is.numeric(item$forecast_volume) || length(item$forecast_volume) != 1L ||
          !is.finite(item$forecast_volume) || item$forecast_volume < 0 ||
          !is.numeric(item$percent_median) || length(item$percent_median) != 1L ||
          !is.finite(item$percent_median) || item$percent_median < 0 ||
          !identical(item$source_statistic, "ESP 50% EXCEEDANCE") ||
          !identical(item$source_percentage_label, "%Med") ||
          !item$status %in% CBRFC_ALLOWED_STATUSES ||
          !item$value_origin %in% c("current_source", "last_known_good")) {
        cbrfc_stop("CBRFC Lake Mead Local monthly item fields or values are invalid.")
      }
      source_date <- as.Date(paste0(item$forecast_month, "-01"))
      expected_raw_label <- paste(format(source_date, "%B"), format(source_date, "%Y"))
      if (isTRUE(item$source_date_override_applied)) {
        if (!identical(record$forecast_issue_date, "2026-07-01") ||
            !identical(item$forecast_month, "2027-01") ||
            !identical(item$raw_forecast_month_label, "January 2026") ||
            !identical(item$source_date_override_id, CBRFC_LOCAL_DATE_OVERRIDE_ID) ||
            !identical(item$source_date_override_reason, CBRFC_LOCAL_DATE_OVERRIDE_REASON) ||
            !identical(
              item$source_date_override_evidence_url,
              CBRFC_LOCAL_DATE_OVERRIDE_EVIDENCE_URL
            ) ||
            !identical(
              item$source_date_override_prior_issue_date,
              CBRFC_LOCAL_DATE_OVERRIDE_PRIOR_ISSUE_DATE
            )) {
          cbrfc_stop("CBRFC Lake Mead Local date override provenance changed.")
        }
      } else if (!identical(item$raw_forecast_month_label, expected_raw_label) ||
                 !is.null(item$source_date_override_id) ||
                 !is.null(item$source_date_override_reason) ||
                 !is.null(item$source_date_override_evidence_url) ||
                 !is.null(item$source_date_override_prior_issue_date)) {
        cbrfc_stop("CBRFC Lake Mead Local uncorrected month has invalid override provenance.")
      }
      bounds <- cbrfc_month_validity(item$forecast_month)
      if (!identical(item$valid_from, bounds$valid_from) ||
          !identical(item$valid_through, bounds$valid_through) ||
          !is.list(item$metric_state) ||
          !identical(names(item$metric_state), c("forecast_volume", "percent_median"))) {
        cbrfc_stop("CBRFC Lake Mead Local monthly validity or metric-state keys changed.")
      }
      for (field in c("forecast_volume", "percent_median")) {
        state <- item$metric_state[[field]]
        expected_map <- identical(item$status, "current")
        expected_popup <- item$status %in% c(
          "current", "not_yet_valid", "source_stale", "stale_last_known_good", "expired"
        )
        if (!is.list(state) ||
            !identical(state$status, item$status) ||
            !identical(state$value_origin, item$value_origin) ||
            !identical(state$source_issue_date, record$forecast_issue_date) ||
            !identical(state$valid_through, item$valid_through) ||
            !identical(state$map_eligible, expected_map) ||
            !identical(state$popup_eligible, expected_popup) ||
            !identical(state$missing_reason, item$missing_reason)) {
          cbrfc_stop("CBRFC Lake Mead Local monthly metric state does not reconcile.")
        }
      }
    }
    override_count <- sum(vapply(
      record$monthly_forecasts, `[[`, logical(1), "source_date_override_applied"
    ))
    if (!override_count %in% c(0L, 1L) ||
        !identical(
          as.integer(record$diagnostic$source_date_override_count),
          as.integer(override_count)
        )) {
      cbrfc_stop("CBRFC Lake Mead Local override count does not reconcile.")
    }
    if (!identical(record$valid_through, tail(record$monthly_forecasts, 1L)[[1L]]$valid_through) ||
        !identical(record$status, cbrfc_monthly_record_status(
          record$monthly_forecasts, record$attempt_outcome
        )) ||
        !identical(record$value_origin, if (identical(
          record$attempt_outcome, "success"
        )) "current_source" else "last_known_good")) {
      cbrfc_stop("CBRFC Lake Mead Local record state does not reconcile to its monthly series.")
    }
  }
  if (identical(record$status, "unavailable") &&
      !identical(record$attempt_outcome, "source_unavailable")) {
    cbrfc_stop("CBRFC Lake Mead Local unavailable requires source confirmation.")
  }
  cbrfc_parse_instant(record$last_attempt_at, "last_attempt_at")
  if (!is.null(record$last_successful_retrieval_at)) {
    cbrfc_parse_instant(record$last_successful_retrieval_at, "last_successful_retrieval_at")
  }
  invisible(TRUE)
}

cbrfc_validate_record <- function(record, roster_row) {
  product_type <- roster_row$product_type[[1L]]
  if (identical(product_type, "lake_mead_local_intervening_monthly_forecast")) {
    return(cbrfc_validate_monthly_record(record, roster_row))
  }
  allowed_fields <- cbrfc_required_record_fields(product_type)
  missing <- setdiff(allowed_fields, names(record))
  if (length(missing) > 0L) cbrfc_stop("CBRFC record is missing field(s): ", paste(missing, collapse = ", "))
  unexpected <- setdiff(names(record), allowed_fields)
  if (length(unexpected) > 0L) {
    cbrfc_stop("CBRFC record contains unreviewed field(s): ", paste(unexpected, collapse = ", "))
  }
  if (!record$status %in% CBRFC_ALLOWED_STATUSES ||
      !record$attempt_outcome %in% CBRFC_ALLOWED_ATTEMPT_OUTCOMES) {
    cbrfc_stop("CBRFC record status or attempt_outcome is invalid.")
  }
  expected_stage <- if (identical(record$attempt_outcome, "success")) {
    NULL
  } else {
    cbrfc_failure_stage_for(record$attempt_outcome)
  }
  if (!identical(record$failure_stage, expected_stage)) {
    cbrfc_stop("CBRFC failure_stage does not match attempt_outcome.")
  }
  expected_statistic <- if (identical(product_type, "april_july_water_supply_forecast")) {
    "50_percent_exceedance"
  } else {
    "official_full_forecast"
  }
  if (!identical(record$forecast_key, roster_row$forecast_key[[1L]]) ||
      !identical(record$rfc, "CBRFC") || !identical(record$nws_lid, "GLDA3") ||
      !identical(record$product_type, product_type) ||
      !identical(record$forecast_statistic, expected_statistic) ||
      !identical(record$source_time_precision, "date") ||
      !identical(record$source_normal_term, "average") ||
      !identical(record$forecast_period, roster_row$forecast_period[[1L]]) ||
      !identical(record$forecast_type, "Unregulated")) {
    cbrfc_stop("CBRFC record identity or source semantics changed.")
  }
  if (!is.integer(record$water_year) || length(record$water_year) != 1L) {
    cbrfc_stop("CBRFC water_year must be a scalar integer.")
  }
  expected_source <- cbrfc_fill_water_year_url(roster_row$source_url[[1L]], record$water_year)
  expected_retrieval <- cbrfc_fill_water_year_url(
    roster_row$retrieval_url_template[[1L]], record$water_year
  )
  if (!identical(record$source_url, expected_source) ||
      !identical(record$retrieval_url, expected_retrieval) ||
      !identical(record$summary_url, roster_row$summary_url[[1L]]) ||
      !identical(record$archive_url, roster_row$archive_url[[1L]])) {
    cbrfc_stop("CBRFC record URLs do not match its water year and roster.")
  }
  role_urls <- unlist(record[c("source_url", "retrieval_url", "summary_url", "archive_url")])
  if (any(!nzchar(role_urls)) ||
      any(grepl("localhost|127[.]0[.]0[.]1|file:|feature/|/Users/", role_urls,
                ignore.case = TRUE))) {
    cbrfc_stop("CBRFC record exposed an empty, local, or feature-branch source link.")
  }
  if (identical(product_type, "water_year_unregulated_inflow_forecast") &&
      "percent_median" %in% names(record)) {
    cbrfc_stop("CBRFC water-year record must not publish percent_median without a direct source value.")
  }
  expected_valid_through <- cbrfc_valid_through(record$water_year, product_type)
  if (!identical(record$valid_through, expected_valid_through)) {
    cbrfc_stop("CBRFC record valid_through does not match its forecast period.")
  }
  fields <- cbrfc_metric_fields(product_type)
  if (!is.list(record$metric_state) || !identical(names(record$metric_state), fields)) {
    cbrfc_stop("CBRFC metric-state keys/order changed.")
  }
  for (field in fields) cbrfc_validate_metric_state(record, field)
  has_values <- any(!vapply(record[fields], is.null, logical(1)))
  if (has_values) {
    if (!identical(record$normalized_units, "kaf") || !identical(record$source_units, "kaf") ||
        is.null(record$forecast_issue_date)) {
      cbrfc_stop("CBRFC numerical values require reviewed units and forecast_issue_date.")
    }
    cbrfc_parse_date(record$forecast_issue_date, "forecast_issue_date")
  } else if (!is.null(record$forecast_issue_date)) {
    cbrfc_stop("CBRFC null official metrics must not retain a fabricated issue date.")
  }
  if (identical(record$status, "unavailable") &&
      !identical(record$attempt_outcome, "source_unavailable")) {
    cbrfc_stop("CBRFC unavailable is reserved for source-confirmed unavailability.")
  }
  if (!identical(record$status, cbrfc_record_status_from_metrics(
    record$metric_state, record$attempt_outcome
  )) || !identical(record$value_origin, cbrfc_record_origin_from_metrics(record$metric_state))) {
    cbrfc_stop("CBRFC record status/origin does not reconcile to metric state.")
  }
  cbrfc_parse_instant(record$last_attempt_at, "last_attempt_at")
  if (!is.null(record$last_successful_retrieval_at)) {
    cbrfc_parse_instant(record$last_successful_retrieval_at, "last_successful_retrieval_at")
  }
  invisible(TRUE)
}

cbrfc_family_summary <- function(record, generated_at) {
  active <- cbrfc_active_season(
    cbrfc_parse_instant(generated_at), record$product_type
  )
  status <- record$status
  success <- identical(record$attempt_outcome, "success")
  unavailable <- identical(record$attempt_outcome, "source_unavailable")
  expected_available <- if (active) 1L else 0L
  healthy <- if (active) {
    success && status %in% c("current", "current_partial", "source_stale")
  } else {
    (success && identical(status, "expired")) ||
      (unavailable && identical(status, "unavailable"))
  }
  health <- if (healthy) {
    "healthy"
  } else if (status %in% c("current", "current_partial", "source_stale")) {
    "degraded"
  } else if (status %in% c("stale_last_known_good", "expired") && !success) {
    "outage_using_last_known_good"
  } else {
    "unusable"
  }
  list(
    expected_structural_count = 1L,
    expected_available_count = expected_available,
    current_count = as.integer(status == "current"),
    current_partial_count = as.integer(status == "current_partial"),
    source_stale_count = as.integer(status == "source_stale"),
    stale_last_known_good_count = as.integer(status == "stale_last_known_good"),
    expired_count = as.integer(status == "expired"),
    source_unavailable_count = as.integer(unavailable),
    failed_no_data_count = as.integer(status == "failed_no_data"),
    successful_attempt_count = as.integer(success),
    failed_attempt_count = as.integer(!success),
    health = health,
    season_state = if (active) "active" else "inactive"
  )
}

cbrfc_source_summary <- function(records) {
  statuses <- vapply(records, `[[`, character(1), "status")
  outcomes <- vapply(records, `[[`, character(1), "attempt_outcome")
  list(
    rfc = "CBRFC",
    successful_attempt_count = as.integer(sum(outcomes == "success")),
    failed_attempt_count = as.integer(sum(outcomes != "success")),
    current_count = as.integer(sum(statuses == "current")),
    current_partial_count = as.integer(sum(statuses == "current_partial")),
    source_stale_count = as.integer(sum(statuses == "source_stale")),
    stale_last_known_good_count = as.integer(sum(statuses == "stale_last_known_good")),
    expired_count = as.integer(sum(statuses == "expired")),
    source_unavailable_count = as.integer(sum(outcomes == "source_unavailable")),
    failed_no_data_count = as.integer(sum(statuses == "failed_no_data"))
  )
}

cbrfc_operational_notices <- function(records) {
  notices <- list()
  for (record in records) {
    if (identical(record$product_type, "april_july_water_supply_forecast") &&
        identical(record$attempt_outcome, "success")) {
      crosscheck_status <- record$diagnostic$dashboard_crosscheck_status
      if (!is.null(crosscheck_status) && !identical(crosscheck_status, "matched")) {
        message <- switch(
          crosscheck_status,
          lagging = "The Lake Powell dashboard lagged the accepted structured official issue; the dashboard was not used to replace the newer primary values.",
          missing_field = "The Lake Powell dashboard omitted a direct Apr-Jul cross-check field; the complete structured official point remained authoritative.",
          fetch_failed = "The Lake Powell dashboard could not be retrieved for secondary cross-checking; the complete structured official point remained authoritative.",
          unavailable_or_unparseable = "The Lake Powell dashboard was unavailable or not parseable for secondary cross-checking; the complete structured official point remained authoritative.",
          "The Lake Powell dashboard cross-check was not complete."
        )
        notices[[length(notices) + 1L]] <- list(
          notice_id = paste0(
            "CBRFC_GLDA3_APR_JUL_DASHBOARD_",
            toupper(gsub("[^A-Za-z0-9]+", "_", crosscheck_status))
          ),
          forecast_key = record$forecast_key,
          notice_type = "summary_crosscheck",
          severity = if (crosscheck_status %in% c("lagging", "missing_field")) "info" else "warning",
          message = message,
          evidence_url = record$summary_url
        )
      }
    }
    if (identical(record$product_type, "lake_mead_local_intervening_monthly_forecast") &&
        length(record$monthly_forecasts) > 0L) {
      corrected <- Filter(
        function(item) isTRUE(item$source_date_override_applied),
        record$monthly_forecasts
      )
      if (length(corrected) == 1L) {
        item <- corrected[[1L]]
        notices[[length(notices) + 1L]] <- list(
          notice_id = item$source_date_override_id,
          forecast_key = record$forecast_key,
          notice_type = "reviewed_source_date_override",
          severity = "warning",
          message = item$source_date_override_reason,
          evidence_url = item$source_date_override_evidence_url
        )
      }
    }
  }
  notices
}

cbrfc_bootstrap_acceptance <- function(record, generated_at) {
  active <- cbrfc_active_season(
    cbrfc_parse_instant(generated_at), record$product_type
  )
  if (identical(record$product_type, "lake_mead_local_intervening_monthly_forecast")) {
    established <- identical(record$attempt_outcome, "success") &&
      length(record$monthly_forecasts) == 12L &&
      record$status %in% c("current", "current_partial", "source_stale")
    return(list(accepted = established, active_season = TRUE))
  }
  fields <- cbrfc_metric_fields(record$product_type)
  established <- identical(record$attempt_outcome, "success") &&
    all(!vapply(record[fields], is.null, logical(1))) &&
    all(vapply(record$metric_state[fields], function(state) {
      state$status %in% if (active) c("current", "source_stale") else "expired"
    }, logical(1)))
  explicit_unavailable <- !active && identical(record$attempt_outcome, "source_unavailable") &&
    identical(record$status, "unavailable")
  list(accepted = established || explicit_unavailable, active_season = active)
}

cbrfc_validate_payload <- function(payload, roster) {
  required <- c(
    "schema_version", "product_id", "roster_version", "generated_at",
    "publication_mode", "expected_record_count", "actual_record_count",
    "source_summary", "family_health", "operational_notices", "records"
  )
  if (!is.list(payload) || !all(required %in% names(payload))) {
    cbrfc_stop("CBRFC payload is missing required top-level fields.")
  }
  unexpected <- setdiff(names(payload), required)
  if (length(unexpected) > 0L) {
    cbrfc_stop("CBRFC payload contains unreviewed top-level field(s): ", paste(unexpected, collapse = ", "))
  }
  if (!identical(payload$schema_version, CBRFC_FORECAST_SCHEMA_VERSION) ||
      !identical(payload$product_id, CBRFC_FORECAST_PRODUCT_ID) ||
      !identical(payload$roster_version, CBRFC_ROSTER_VERSION) ||
      !payload$publication_mode %in% c("bootstrap", "steady_state")) {
    cbrfc_stop("Unsupported CBRFC schema, product, roster, or publication mode.")
  }
  cbrfc_parse_utc(payload$generated_at, "generated_at")
  expected_count <- as.integer(nrow(roster))
  if (!identical(as.integer(payload$expected_record_count), expected_count) ||
      !identical(as.integer(payload$actual_record_count), expected_count) ||
      !is.list(payload$records) || length(payload$records) != expected_count) {
    cbrfc_stop("CBRFC payload must contain exactly three structural records.")
  }
  for (index in seq_len(nrow(roster))) {
    cbrfc_validate_record(payload$records[[index]], roster[index, , drop = FALSE])
  }
  if (!identical(
    vapply(payload$records, `[[`, character(1), "forecast_key"),
    roster$forecast_key
  )) {
    cbrfc_stop("CBRFC payload ordering/identity changed.")
  }
  recursive_names <- function(value) {
    if (!is.list(value)) return(character())
    c(names(value), unlist(lapply(value, recursive_names), use.names = FALSE))
  }
  forbidden <- grep(
    "geometry|polygon|coordinates|latitude|longitude|huc|dateline|^esp|ensemble_member",
    unique(unlist(lapply(payload$records, recursive_names), use.names = FALSE)),
    ignore.case = TRUE,
    value = TRUE
  )
  if (length(forbidden) > 0L) {
    cbrfc_stop("CBRFC payload contains forbidden geometry or raw-guidance fields: ", paste(forbidden, collapse = ", "))
  }
  family_names <- roster$product_type
  if (!is.list(payload$family_health) || !identical(names(payload$family_health), family_names)) {
    cbrfc_stop("CBRFC family_health must contain exactly the three reviewed product families.")
  }
  for (index in seq_along(family_names)) {
    expected_family <- cbrfc_family_summary(payload$records[[index]], payload$generated_at)
    if (!identical(
      jsonlite::toJSON(payload$family_health[[family_names[[index]]]], auto_unbox = TRUE, null = "null", digits = NA),
      jsonlite::toJSON(expected_family, auto_unbox = TRUE, null = "null", digits = NA)
    )) {
      cbrfc_stop("CBRFC family health does not reconcile for ", family_names[[index]], ".")
    }
  }
  if (!identical(
    jsonlite::toJSON(payload$source_summary, auto_unbox = TRUE, null = "null", digits = NA),
    jsonlite::toJSON(cbrfc_source_summary(payload$records), auto_unbox = TRUE, null = "null", digits = NA)
  )) {
    cbrfc_stop("CBRFC source summary does not reconcile to its record.")
  }
  if (!is.list(payload$operational_notices) || !identical(
    jsonlite::toJSON(payload$operational_notices, auto_unbox = TRUE, null = "null", digits = NA),
    jsonlite::toJSON(cbrfc_operational_notices(payload$records), auto_unbox = TRUE, null = "null", digits = NA)
  )) {
    cbrfc_stop("CBRFC operational notices do not reconcile to the records.")
  }
  invisible(TRUE)
}

cbrfc_build_payload <- function(roster,
                                attempt_results,
                                prior_payload = NULL,
                                generated_at = cbrfc_iso_utc(),
                                water_year = cbrfc_current_water_year(cbrfc_parse_instant(generated_at))) {
  if (!is.null(prior_payload)) cbrfc_validate_payload(prior_payload, roster)
  if (!is.list(attempt_results) || length(attempt_results) != nrow(roster)) {
    cbrfc_stop("CBRFC build requires exactly one attempt result per roster record.")
  }
  prior_by_key <- if (is.null(prior_payload)) list() else stats::setNames(
    prior_payload$records,
    vapply(prior_payload$records, `[[`, character(1), "forecast_key")
  )
  records <- lapply(seq_len(nrow(roster)), function(index) {
    roster_row <- roster[index, , drop = FALSE]
    prior_record <- prior_by_key[[roster_row$forecast_key[[1L]]]]
    result <- cbrfc_enforce_source_monotonicity(attempt_results[[index]], prior_record)
    record <- if (isTRUE(result$success)) {
      result$record
    } else {
      cbrfc_failed_or_retained_record(roster_row, result, prior_record, water_year)
    }
    cbrfc_preserve_noop_timestamps(record, prior_record)
  })
  family_names <- roster$product_type
  mode <- if (is.null(prior_payload)) "bootstrap" else "steady_state"
  payload <- list(
    schema_version = CBRFC_FORECAST_SCHEMA_VERSION,
    product_id = CBRFC_FORECAST_PRODUCT_ID,
    roster_version = CBRFC_ROSTER_VERSION,
    generated_at = generated_at,
    publication_mode = mode,
    expected_record_count = as.integer(nrow(roster)),
    actual_record_count = as.integer(length(records)),
    source_summary = cbrfc_source_summary(records),
    family_health = stats::setNames(lapply(
      records, cbrfc_family_summary, generated_at = generated_at
    ), family_names),
    operational_notices = cbrfc_operational_notices(records),
    records = records
  )
  cbrfc_validate_payload(payload, roster)
  if (identical(mode, "bootstrap")) {
    acceptance <- lapply(records, cbrfc_bootstrap_acceptance, generated_at = generated_at)
    inactive <- !vapply(acceptance, `[[`, logical(1), "active_season")
    explicit_inactive <- vapply(records, function(record) {
      identical(record$attempt_outcome, "source_unavailable") &&
        identical(record$status, "unavailable")
    }, logical(1)) & inactive
    established <- vapply(records, function(record) {
      if (identical(record$product_type, "lake_mead_local_intervening_monthly_forecast")) {
        return(identical(record$attempt_outcome, "success") &&
          length(record$monthly_forecasts) == 12L)
      }
      identical(record$attempt_outcome, "success") &&
        any(!vapply(record[cbrfc_metric_fields(record$product_type)], is.null, logical(1)))
    }, logical(1))
    if (!any(established) && !all(explicit_inactive)) {
      cbrfc_stop(
        "CBRFC bootstrap acceptance failed: no reviewed CBRFC family established official data. ",
        "Prior canonical payload retained."
      )
    }
  }
  payload
}

cbrfc_read_payload <- function(path, roster) {
  if (!file.exists(path)) return(NULL)
  payload <- jsonlite::fromJSON(path, simplifyVector = FALSE)
  cbrfc_validate_payload(payload, roster)
  payload
}

cbrfc_substantive_view <- function(payload) {
  view <- payload
  view$generated_at <- NULL
  # Bootstrap describes how the accepted file was first established; a later
  # identical steady-state retrieval must remain a semantic no-op.
  view$publication_mode <- NULL
  view
}

cbrfc_payload_changed <- function(candidate, prior_payload) {
  if (is.null(prior_payload)) return(TRUE)
  !identical(
    jsonlite::toJSON(cbrfc_substantive_view(candidate), auto_unbox = TRUE, null = "null", na = "null", digits = NA),
    jsonlite::toJSON(cbrfc_substantive_view(prior_payload), auto_unbox = TRUE, null = "null", na = "null", digits = NA)
  )
}

cbrfc_write_json <- function(object, path) {
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

cbrfc_stage_and_promote_payload <- function(payload, output_path, roster, prior_payload = NULL) {
  if (!cbrfc_payload_changed(payload, prior_payload)) {
    message("No substantive CBRFC forecast changes; output payload left byte-for-byte unchanged.")
    return(FALSE)
  }
  dir.create(dirname(output_path), recursive = TRUE, showWarnings = FALSE)
  staged <- tempfile(".cbrfc-major-water-supply-", tmpdir = dirname(output_path), fileext = ".json")
  on.exit(if (file.exists(staged)) unlink(staged), add = TRUE)
  cbrfc_write_json(payload, staged)
  staged_payload <- cbrfc_read_payload(staged, roster)
  cbrfc_validate_payload(staged_payload, roster)
  if (!file.rename(staged, output_path)) {
    cbrfc_stop("Could not promote validated CBRFC payload to ", output_path, ".")
  }
  TRUE
}
