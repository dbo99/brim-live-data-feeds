# Pure helpers for the BRIM Winter Storm Levels NBM publisher.

WSL_METERS_TO_FEET <- 3.28083989501312
WSL_TRANSIENT_HTTP_STATUS <- c(408L, 429L, 500L, 502L, 503L, 504L)
WSL_CARTOGRAPHIC_CRS <- 5070
WSL_TARGET_LEADS <- c(1L, seq.int(6L, 240L, by = 6L))
WSL_LEGACY_TARGET_LEADS <- c(1L, 6L, 12L, 18L, 24L, 30L, 36L, 42L, 48L, 60L, 72L)

wsl_stop <- function(class, ..., call. = FALSE) {
  condition <- structure(
    list(message = paste0(...), call = NULL),
    class = c(class, "winter_storm_levels_error", "error", "condition")
  )
  stop(condition)
}

wsl_as_utc <- function(x) {
  if (inherits(x, "POSIXt")) {
    return(as.POSIXct(as.numeric(x), origin = "1970-01-01", tz = "UTC"))
  }
  if (is.numeric(x)) return(as.POSIXct(x, origin = "1970-01-01", tz = "UTC"))
  parsed <- as.POSIXct(x, tz = "UTC", format = "%Y-%m-%dT%H:%M:%SZ")
  if (is.na(parsed)) parsed <- as.POSIXct(x, tz = "UTC")
  parsed
}

wsl_iso_utc <- function(x) {
  format(wsl_as_utc(x), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
}

wsl_parse_integer_csv <- function(value, field) {
  values <- suppressWarnings(as.integer(trimws(strsplit(as.character(value), ",", fixed = TRUE)[[1]])))
  if (!length(values) || anyNA(values)) stop("Invalid integer list in ", field, ".")
  sort(unique(values))
}

wsl_read_config <- function(path) {
  config <- utils::read.csv(path, stringsAsFactors = FALSE, check.names = FALSE)
  if (nrow(config) != 1L) stop("Winter Storm Levels config must contain exactly one data row.")
  row <- as.list(config[1L, , drop = FALSE])
  row$source_cycle_hours_utc <- wsl_parse_integer_csv(row$source_cycle_hours_utc, "source_cycle_hours_utc")
  row$forecast_lead_hours <- wsl_parse_integer_csv(row$forecast_lead_hours, "forecast_lead_hours")
  numeric_fields <- c(
    "west", "east", "south", "north", "source_west", "source_east",
    "source_south", "source_north", "contour_min_ft", "contour_max_ft",
    "contour_interval_ft", "simplify_tolerance_m", "coordinate_digits",
    "current_after_hours", "delayed_after_hours", "expire_after_hours",
    "valid_tolerance_hours", "retain_cycle_count", "cycle_lookback_hours",
    "http_max_tries", "http_timeout_seconds"
  )
  for (field in numeric_fields) row[[field]] <- as.numeric(row[[field]])

  if (any(!is.finite(unlist(row[numeric_fields])))) stop("Config contains non-finite numeric values.")
  if (!(row$west < row$east && row$south < row$north)) stop("Invalid display bounds.")
  if (!(row$source_west <= row$west && row$source_east >= row$east &&
        row$source_south <= row$south && row$source_north >= row$north)) {
    stop("Source crop must fully buffer the display domain.")
  }
  if (!(row$contour_min_ft >= 0 && row$contour_max_ft > row$contour_min_ft &&
        row$contour_interval_ft > 0)) stop("Invalid contour range.")
  if (!(row$current_after_hours < row$delayed_after_hours &&
        row$delayed_after_hours < row$expire_after_hours)) stop("Invalid freshness thresholds.")
  if (row$retain_cycle_count != 2 || row$retain_cycle_count %% 1 != 0) {
    stop("Winter Storm Levels contract version 1 requires exactly two retained cycles.")
  }
  if (any(!row$source_cycle_hours_utc %in% 0:23)) stop("Invalid source cycle hour.")
  if (any(row$forecast_lead_hours < 0)) stop("Forecast lead hours cannot be negative.")
  if (!identical(as.integer(row$forecast_lead_hours), WSL_TARGET_LEADS)) {
    stop("Winter Storm Levels requires f001 plus every six hours from f006 through f240.")
  }
  row
}

wsl_floor_cycle <- function(now, cycle_hours = c(0L, 6L, 12L, 18L)) {
  now <- wsl_as_utc(now)
  day <- as.POSIXct(format(now, "%Y-%m-%d 00:00:00", tz = "UTC"), tz = "UTC")
  candidates <- day + as.integer(cycle_hours) * 3600
  candidates <- candidates[candidates <= now]
  if (!length(candidates)) return(day - (24L - max(cycle_hours)) * 3600)
  max(candidates)
}

wsl_candidate_cycles <- function(now, config) {
  newest <- wsl_floor_cycle(now, config$source_cycle_hours_utc)
  count <- floor(config$cycle_lookback_hours / 6) + 1L
  newest - seq.int(0L, count - 1L) * 6L * 3600
}

wsl_cycle_token <- function(cycle_time) {
  format(wsl_as_utc(cycle_time), "%Y%m%d%H", tz = "UTC")
}

wsl_nbm_urls <- function(cycle_time, lead_hour) {
  date <- format(wsl_as_utc(cycle_time), "%Y%m%d", tz = "UTC")
  hour <- format(wsl_as_utc(cycle_time), "%H", tz = "UTC")
  filename <- sprintf("blend.t%sz.core.f%03d.co.grib2", hour, as.integer(lead_hour))
  base <- sprintf(
    "https://noaa-nbm-grib2-pds.s3.amazonaws.com/blend.%s/%s/core/%s",
    date, hour, filename
  )
  list(grib = base, index = paste0(base, ".idx"))
}

wsl_classify_http_status <- function(status) {
  status <- as.integer(status)
  if (status >= 200L && status < 300L) return("ok")
  if (status %in% c(403L, 404L)) return("source_unavailable")
  if (status %in% WSL_TRANSIENT_HTTP_STATUS) return("fetch_failed_transient")
  "fetch_failed_permanent"
}

wsl_retry_delay <- function(response, attempt, jitterer = function(max_seconds) {
  stats::runif(1L, min = 0, max = max_seconds)
}) {
  if (!is.null(response) && !inherits(response, "error")) {
    retry_after <- httr2::resp_header(response, "retry-after")
    if (!is.null(retry_after) && grepl("^[0-9]+(?:\\.[0-9]+)?$", retry_after)) {
      return(min(as.numeric(retry_after), 30))
    }
  }
  base <- min(2 ^ (as.integer(attempt) - 1L), 4)
  min(base + max(0, as.numeric(jitterer(0.25))), 5)
}

wsl_http_request <- function(
    request,
    max_tries = 3L,
    performer = httr2::req_perform,
    sleeper = Sys.sleep,
    jitterer = function(max_seconds) stats::runif(1L, min = 0, max = max_seconds)
) {
  max_tries <- max(1L, as.integer(max_tries))
  last_error <- NULL
  for (attempt in seq_len(max_tries)) {
    response <- tryCatch(performer(request), error = function(error) error)
    if (!inherits(response, "error")) {
      status <- httr2::resp_status(response)
      classification <- wsl_classify_http_status(status)
      if (identical(classification, "ok")) return(response)
      if (!identical(classification, "fetch_failed_transient") || attempt == max_tries) {
        wsl_stop(classification, "HTTP ", status, " after ", attempt, " attempt(s).")
      }
      last_error <- paste0("HTTP ", status)
    } else {
      last_error <- conditionMessage(response)
      if (attempt == max_tries) {
        wsl_stop("fetch_failed_transient", "Transport failure after ", attempt,
                 " attempt(s): ", last_error)
      }
    }
    sleeper(wsl_retry_delay(response, attempt, jitterer))
  }
  wsl_stop("fetch_failed_transient", "Request failed: ", last_error)
}

wsl_fetch_text <- function(url, config) {
  request <- httr2::request(url) |>
    httr2::req_user_agent("BRIM-Winter-Storm-Levels/1.0") |>
    httr2::req_timeout(config$http_timeout_seconds) |>
    httr2::req_error(is_error = function(response) FALSE)
  response <- wsl_http_request(request, config$http_max_tries)
  httr2::resp_body_string(response)
}

wsl_validate_range_response <- function(response, start_byte, end_byte) {
  expected_length <- as.integer(end_byte - start_byte + 1)
  status <- httr2::resp_status(response)
  if (status != 206L) wsl_stop("validation_failed", "Range request returned HTTP ", status, ".")
  content_range <- httr2::resp_header(response, "content-range")
  expected_range <- sprintf("bytes %d-%d/", start_byte, end_byte)
  if (is.null(content_range) || !startsWith(content_range, expected_range)) {
    wsl_stop("validation_failed", "Unexpected Content-Range response: ", content_range %||% "<missing>")
  }
  body <- httr2::resp_body_raw(response)
  if (length(body) != expected_length) {
    wsl_stop("validation_failed", "Range response length mismatch: expected ",
             expected_length, ", received ", length(body), ".")
  }
  body
}

wsl_fetch_range <- function(url, start_byte, end_byte, path, config) {
  request <- httr2::request(url) |>
    httr2::req_user_agent("BRIM-Winter-Storm-Levels/1.0") |>
    httr2::req_headers(Range = sprintf("bytes=%d-%d", start_byte, end_byte)) |>
    httr2::req_timeout(config$http_timeout_seconds) |>
    httr2::req_error(is_error = function(response) FALSE)
  response <- wsl_http_request(request, config$http_max_tries)
  body <- wsl_validate_range_response(response, start_byte, end_byte)
  writeBin(body, path)
  invisible(path)
}

`%||%` <- function(x, y) {
  if (is.null(x) || length(x) == 0L || all(is.na(x))) y else x
}

wsl_parse_index <- function(text, cycle_time, lead_hour) {
  lines <- strsplit(text, "\n", fixed = TRUE)[[1]]
  lines <- lines[nzchar(lines)]
  parts <- strsplit(lines, ":", fixed = TRUE)
  offsets <- suppressWarnings(vapply(parts, function(x) as.numeric(x[2L]), numeric(1)))
  expected_cycle <- paste0("d=", wsl_cycle_token(cycle_time))
  expected_forecast <- sprintf("%d hour fcst", as.integer(lead_hour))
  matches <- vapply(parts, function(fields) {
    length(fields) >= 6L &&
      identical(fields[3L], expected_cycle) &&
      identical(fields[4L], "SNOWLVL") &&
      identical(fields[5L], "0 m above mean sea level") &&
      identical(fields[6L], expected_forecast) &&
      !any(grepl("percentileValue=", fields, fixed = TRUE))
  }, logical(1))
  positions <- which(matches)
  variable_present <- any(vapply(parts, function(fields) {
    length(fields) >= 4L && identical(fields[4L], "SNOWLVL")
  }, logical(1)))
  if (!variable_present) {
    wsl_stop("variable_missing", "The source inventory does not contain SNOWLVL.")
  }
  if (length(positions) != 1L) {
    wsl_stop("validation_failed", "Expected one deterministic SNOWLVL record for ",
             expected_cycle, " f", sprintf("%03d", lead_hour), "; found ", length(positions), ".")
  }
  position <- positions[[1L]]
  if (position >= length(lines) || !is.finite(offsets[position + 1L])) {
    wsl_stop("validation_failed", "SNOWLVL record has no following byte offset.")
  }
  list(
    record_number = as.integer(parts[[position]][1L]),
    start_byte = offsets[position],
    end_byte = offsets[position + 1L] - 1,
    cycle_time_utc = wsl_iso_utc(cycle_time),
    valid_time_utc = wsl_iso_utc(wsl_as_utc(cycle_time) + as.integer(lead_hour) * 3600),
    lead_hours = as.integer(lead_hour),
    inventory_line = lines[position]
  )
}

wsl_discover_cycle <- function(now, config, fetch_text = wsl_fetch_text) {
  attempts <- list()
  for (cycle in wsl_candidate_cycles(now, config)) {
    records <- list()
    cycle_ok <- TRUE
    reason <- NULL
    for (lead in config$forecast_lead_hours) {
      urls <- wsl_nbm_urls(cycle, lead)
      result <- tryCatch(
        wsl_parse_index(fetch_text(urls$index, config), cycle, lead),
        error = function(error) error
      )
      if (inherits(result, "error")) {
        cycle_ok <- FALSE
        reason <- conditionMessage(result)
        outcome <- if ("variable_missing" %in% class(result)) {
          "variable_missing"
        } else if ("validation_failed" %in% class(result)) {
          "validation_failed"
        } else if (any(c("fetch_failed_transient", "fetch_failed_permanent") %in% class(result))) {
          "fetch_failed"
        } else {
          "source_unavailable"
        }
        break
      }
      result$grib_url <- urls$grib
      result$index_url <- urls$index
      records[[as.character(lead)]] <- result
    }
    attempts[[length(attempts) + 1L]] <- list(
      cycle_time_utc = wsl_iso_utc(cycle), complete = cycle_ok,
      outcome = if (cycle_ok) "success" else outcome, reason = reason
    )
    if (cycle_ok) return(list(cycle_time = wsl_as_utc(cycle), records = records, attempts = attempts))
  }
  outcomes <- vapply(attempts, `[[`, character(1), "outcome")
  final_class <- if ("validation_failed" %in% outcomes) {
    "validation_failed"
  } else if ("variable_missing" %in% outcomes) {
    "variable_missing"
  } else if ("fetch_failed" %in% outcomes) {
    "fetch_failed_transient"
  } else {
    "source_unavailable"
  }
  condition <- structure(
    list(
      message = "No complete NBM cycle was found within the configured lookback.",
      call = NULL,
      attempts = attempts
    ),
    class = c(final_class, "winter_storm_levels_error", "error", "condition")
  )
  stop(condition)
}

wsl_read_canonical_cycle <- function(path, expected_product_id = "winter_storm_levels") {
  if (!file.exists(path)) {
    wsl_stop("validation_failed", "Canonical Winter Storm Levels manifest is missing.")
  }
  manifest <- tryCatch(
    jsonlite::fromJSON(path, simplifyVector = FALSE),
    error = function(error) {
      wsl_stop("validation_failed", "Canonical manifest is not valid JSON: ", conditionMessage(error))
    }
  )
  if (!identical(manifest$product_id, expected_product_id)) {
    wsl_stop("validation_failed", "Canonical manifest product identity is invalid.")
  }
  if (!is.character(manifest$cycle_time_utc) || length(manifest$cycle_time_utc) != 1L) {
    wsl_stop("validation_failed", "Canonical manifest root cycle is missing or invalid.")
  }
  root_cycle <- wsl_as_utc(manifest$cycle_time_utc)
  if (is.na(root_cycle)) {
    wsl_stop("validation_failed", "Canonical manifest root cycle is not a valid UTC time.")
  }
  if (!is.list(manifest$targets) || !length(manifest$targets)) {
    wsl_stop("validation_failed", "Canonical manifest has no target entries.")
  }
  target_cycles <- vapply(manifest$targets, function(entry) {
    if (!is.character(entry$cycle_time_utc) || length(entry$cycle_time_utc) != 1L) {
      wsl_stop("validation_failed", "Canonical manifest target cycle is missing or invalid.")
    }
    cycle <- wsl_as_utc(entry$cycle_time_utc)
    if (is.na(cycle)) {
      wsl_stop("validation_failed", "Canonical manifest target cycle is not a valid UTC time.")
    }
    as.numeric(cycle)
  }, numeric(1))
  if (!identical(as.numeric(root_cycle), max(target_cycles))) {
    wsl_stop("validation_failed", "Canonical manifest root cycle is not its newest target cycle.")
  }
  root_cycle
}

wsl_cycle_is_strictly_newer <- function(candidate_cycle, canonical_cycle) {
  candidate <- wsl_as_utc(candidate_cycle)
  canonical <- wsl_as_utc(canonical_cycle)
  !is.na(candidate) && !is.na(canonical) && candidate > canonical
}

wsl_preflight <- function(
    now,
    config,
    canonical_manifest_path,
    fetch_text = wsl_fetch_text,
    trigger_type = "unknown"
) {
  observation_time <- wsl_as_utc(now)
  candidate_times <- wsl_candidate_cycles(observation_time, config)
  candidate_cycles <- vapply(candidate_times, wsl_iso_utc, character(1))
  result <- list(
    product_id = config$product_id,
    trigger_type = as.character(trigger_type),
    observation_time_utc = wsl_iso_utc(observation_time),
    candidate_cycles = unname(candidate_cycles),
    newest_nominal_candidate = candidate_cycles[[1L]],
    newest_complete_cycle = NULL,
    canonical_current_cycle = NULL,
    strictly_newer_complete_cycle = FALSE,
    preflight_outcome = "SOURCE_ERROR",
    build_started = FALSE,
    publication_attempted = FALSE,
    source_attempts = list()
  )

  canonical <- tryCatch(
    wsl_read_canonical_cycle(canonical_manifest_path, config$product_id),
    error = function(error) error
  )
  if (inherits(canonical, "error")) {
    result$error <- conditionMessage(canonical)
    return(result)
  }
  result$canonical_current_cycle <- wsl_iso_utc(canonical)

  newest_nominal <- candidate_times[[1L]]
  if (!wsl_cycle_is_strictly_newer(newest_nominal, canonical)) {
    # A validated canonical cycle is already at least as new as every normal
    # candidate. The fallback can therefore no-op without any source request.
    result$newest_complete_cycle <- result$canonical_current_cycle
    result$preflight_outcome <- "NO_NEW_CYCLE"
    return(result)
  }

  discovery <- tryCatch(
    wsl_discover_cycle(observation_time, config, fetch_text),
    error = function(error) error
  )
  result$source_attempts <- discovery$attempts %||% list()
  newest_attempt <- Filter(function(attempt) {
    identical(attempt$cycle_time_utc, result$newest_nominal_candidate)
  }, result$source_attempts)
  newest_attempt <- if (length(newest_attempt)) newest_attempt[[1L]] else NULL
  unexpected_newest_failure <- !is.null(newest_attempt) &&
    !isTRUE(newest_attempt$complete) &&
    !identical(newest_attempt$outcome, "source_unavailable")

  if (inherits(discovery, "error")) {
    if (unexpected_newest_failure || is.null(newest_attempt)) {
      result$preflight_outcome <- "SOURCE_ERROR"
      result$error <- if (unexpected_newest_failure) {
        newest_attempt$reason %||% conditionMessage(discovery)
      } else {
        conditionMessage(discovery)
      }
    } else {
      result$preflight_outcome <- "SOURCE_NOT_READY"
    }
    return(result)
  }

  result$newest_complete_cycle <- wsl_iso_utc(discovery$cycle_time)
  result$strictly_newer_complete_cycle <- wsl_cycle_is_strictly_newer(
    discovery$cycle_time, canonical
  )
  if (unexpected_newest_failure) {
    result$preflight_outcome <- "SOURCE_ERROR"
    result$error <- newest_attempt$reason %||% "Unexpected newest-cycle inventory failure."
  } else if (isTRUE(result$strictly_newer_complete_cycle)) {
    result$preflight_outcome <- "NEW_CYCLE"
  } else {
    result$preflight_outcome <- "SOURCE_NOT_READY"
  }
  result
}

wsl_decode_grib <- function(path, reader = terra::rast) {
  tryCatch(
    reader(path),
    error = function(error) wsl_stop("decode_failed", "GRIB decode failed: ", conditionMessage(error))
  )
}

wsl_parse_grib_metadata <- function(lines, read_error = NULL) {
  lines <- trimws(as.character(lines))
  errors <- character()
  if (!is.null(read_error)) errors <- c(errors, paste0("metadata read failed: ", read_error))

  band_count <- sum(grepl("^Band [0-9]+(?: |$)", lines))
  if (band_count != 1L) {
    errors <- c(errors, paste0("expected one GRIB band; found ", band_count))
  }

  metadata_value <- function(key) {
    prefix <- paste0(key, "=")
    matches <- lines[startsWith(lines, prefix)]
    if (length(matches) != 1L) {
      errors <<- c(errors, paste0("expected one ", key, "; found ", length(matches)))
      return(NA_character_)
    }
    substring(matches[[1L]], nchar(prefix) + 1L)
  }
  numeric_value <- function(key) {
    text <- metadata_value(key)
    value <- suppressWarnings(as.numeric(text))
    if (length(value) != 1L || !is.finite(value) || value != floor(value)) {
      errors <<- c(errors, paste0(key, " is not a finite integer"))
      return(NA_real_)
    }
    value
  }

  list(
    band_count = as.integer(band_count),
    reference_time_epoch = numeric_value("GRIB_REF_TIME"),
    forecast_seconds = numeric_value("GRIB_FORECAST_SECONDS"),
    valid_time_epoch = numeric_value("GRIB_VALID_TIME"),
    element = metadata_value("GRIB_ELEMENT"),
    level = metadata_value("GRIB_SHORT_NAME"),
    pdtn = numeric_value("GRIB_PDS_PDTN"),
    parse_errors = unique(errors)
  )
}

wsl_read_grib_metadata <- function(path, describer = terra::describe) {
  description <- tryCatch(describer(path), error = function(error) error)
  if (inherits(description, "error")) {
    return(wsl_parse_grib_metadata(character(), conditionMessage(description)))
  }
  wsl_parse_grib_metadata(description)
}

wsl_optional_iso_utc <- function(value) {
  if (length(value) != 1L || is.na(value)) return(NA_character_)
  tryCatch(wsl_iso_utc(value), error = function(error) NA_character_)
}

wsl_runtime_diagnostics <- function() {
  list(
    terra_version = tryCatch(as.character(utils::packageVersion("terra")),
                             error = function(error) NA_character_),
    gdal_version = tryCatch(as.character(terra::gdal()),
                            error = function(error) NA_character_)
  )
}

wsl_source_diagnostic <- function(metadata, record, terra_time,
                                  runtime = wsl_runtime_diagnostics()) {
  expected_reference_epoch <- as.numeric(wsl_as_utc(record$cycle_time_utc))
  expected_forecast_seconds <- as.numeric(record$lead_hours) * 3600
  expected_valid_epoch <- as.numeric(wsl_as_utc(record$valid_time_utc))
  finite_number <- function(value) {
    length(value) == 1L && is.finite(value)
  }
  number_matches <- function(actual, expected) {
    finite_number(actual) && finite_number(expected) && isTRUE(all.equal(actual, expected))
  }

  reference_present <- finite_number(metadata$reference_time_epoch)
  forecast_present <- finite_number(metadata$forecast_seconds)
  valid_present <- finite_number(metadata$valid_time_epoch)
  element_present <- length(metadata$element) == 1L && !is.na(metadata$element) && nzchar(metadata$element)
  level_present <- length(metadata$level) == 1L && !is.na(metadata$level) && nzchar(metadata$level)
  pdtn_present <- finite_number(metadata$pdtn)
  required_present <- reference_present && forecast_present && valid_present &&
    element_present && level_present && pdtn_present && !length(metadata$parse_errors)

  terra_time_utc <- wsl_optional_iso_utc(terra_time)
  authoritative_valid_time_utc <- if (valid_present) {
    wsl_optional_iso_utc(metadata$valid_time_epoch)
  } else {
    NA_character_
  }
  terra_time_available <- length(terra_time_utc) == 1L && !is.na(terra_time_utc)
  terra_time_agrees <- if (terra_time_available && !is.na(authoritative_valid_time_utc)) {
    identical(terra_time_utc, authoritative_valid_time_utc)
  } else {
    NA
  }

  checks <- list(
    required_metadata_present = required_present,
    single_message_band = identical(as.integer(metadata$band_count), 1L),
    reference_matches_cycle = number_matches(metadata$reference_time_epoch, expected_reference_epoch),
    forecast_seconds_match_lead = number_matches(metadata$forecast_seconds, expected_forecast_seconds),
    valid_equals_reference_plus_step = reference_present && forecast_present && valid_present &&
      number_matches(metadata$valid_time_epoch,
                     metadata$reference_time_epoch + metadata$forecast_seconds),
    valid_matches_inventory = number_matches(metadata$valid_time_epoch, expected_valid_epoch),
    element_is_snowlvl = element_present && identical(metadata$element, "SNOWLVL"),
    level_is_zero_gpml = level_present && identical(metadata$level, "0-GPML"),
    temporal_type_is_instantaneous = pdtn_present && number_matches(metadata$pdtn, 0)
  )

  list(
    inventory_record = record$inventory_line %||% NA_character_,
    expected_cycle_time_utc = record$cycle_time_utc,
    expected_lead_hours = as.integer(record$lead_hours),
    expected_valid_time_utc = record$valid_time_utc,
    grib_reference_time_utc = if (reference_present) {
      wsl_optional_iso_utc(metadata$reference_time_epoch)
    } else NA_character_,
    grib_forecast_seconds = if (forecast_present) metadata$forecast_seconds else NA_real_,
    grib_valid_time_utc = authoritative_valid_time_utc,
    authoritative_valid_time_utc = authoritative_valid_time_utc,
    terra_time_utc = terra_time_utc,
    terra_time_agrees = terra_time_agrees,
    decoder_divergence = isTRUE(terra_time_available && !isTRUE(terra_time_agrees)),
    element = metadata$element,
    level = metadata$level,
    pdtn = if (pdtn_present) metadata$pdtn else NA_real_,
    temporal_type = if (pdtn_present && number_matches(metadata$pdtn, 0)) {
      "instantaneous"
    } else if (pdtn_present) {
      paste0("unsupported_pdtn_", format(metadata$pdtn, scientific = FALSE))
    } else {
      NA_character_
    },
    message_band_count = as.integer(metadata$band_count),
    metadata_parse_errors = metadata$parse_errors,
    validation_checks = checks,
    authoritative_metadata_accepted = all(unlist(checks), na.rm = FALSE),
    runtime = runtime
  )
}

wsl_require_source_diagnostic <- function(diagnostic) {
  checks <- diagnostic$validation_checks
  if (!isTRUE(checks$required_metadata_present) || !isTRUE(checks$single_message_band)) {
    detail <- diagnostic$metadata_parse_errors
    if (!length(detail)) detail <- "one or more required fields are missing"
    stop("Authoritative GRIB metadata are missing or ambiguous: ", paste(detail, collapse = "; "))
  }
  if (!isTRUE(checks$reference_matches_cycle)) {
    stop("GRIB reference time does not match the selected inventory cycle.")
  }
  if (!isTRUE(checks$forecast_seconds_match_lead)) {
    stop("GRIB forecast seconds do not match the selected inventory lead.")
  }
  if (!isTRUE(checks$valid_equals_reference_plus_step)) {
    stop("GRIB valid time does not equal reference time plus forecast seconds.")
  }
  if (!isTRUE(checks$valid_matches_inventory)) {
    stop("GRIB valid time does not match the selected inventory record.")
  }
  if (!isTRUE(checks$element_is_snowlvl)) {
    stop("GRIB element is not deterministic SNOWLVL.")
  }
  if (!isTRUE(checks$level_is_zero_gpml)) {
    stop("GRIB level is not 0 m GPML.")
  }
  if (!isTRUE(checks$temporal_type_is_instantaneous)) {
    stop("GRIB PDTN is not the expected instantaneous forecast type.")
  }
  invisible(TRUE)
}

wsl_log_source_diagnostic <- function(diagnostic) {
  value <- function(x) if (length(x) != 1L || is.na(x)) "missing" else as.character(x)
  message(
    "NBM source timing: cycle=", value(diagnostic$expected_cycle_time_utc),
    " lead_hours=", value(diagnostic$expected_lead_hours),
    " expected_valid=", value(diagnostic$expected_valid_time_utc),
    " grib_reference=", value(diagnostic$grib_reference_time_utc),
    " grib_forecast_seconds=", value(diagnostic$grib_forecast_seconds),
    " grib_valid=", value(diagnostic$grib_valid_time_utc),
    " terra_time=", value(diagnostic$terra_time_utc),
    " terra_agrees=", value(diagnostic$terra_time_agrees),
    " element=", value(diagnostic$element),
    " level=", value(diagnostic$level),
    " pdtn=", value(diagnostic$pdtn),
    " temporal_type=", value(diagnostic$temporal_type)
  )
  if (isTRUE(diagnostic$decoder_divergence)) {
    message(
      "NBM decoder-time divergence accepted after authoritative metadata validation: terra_time=",
      value(diagnostic$terra_time_utc), " authoritative_valid=",
      value(diagnostic$authoritative_valid_time_utc)
    )
  }
  invisible(diagnostic)
}

wsl_validate_raster <- function(raster, record, config, source_diagnostic) {
  if (!inherits(raster, "SpatRaster")) stop("Decoded source is not a SpatRaster.")
  if (terra::nlyr(raster) != 1L) stop("SNOWLVL message must decode to one raster layer.")
  if (missing(source_diagnostic) || is.null(source_diagnostic)) {
    stop("Authoritative GRIB source diagnostics are required for raster validation.")
  }
  wsl_require_source_diagnostic(source_diagnostic)
  if (!nzchar(terra::crs(raster))) stop("SNOWLVL raster has no coordinate reference system.")
  units <- tolower(trimws(terra::units(raster)[1L] %||% ""))
  if (!units %in% c("m", "meter", "metre", "meters", "metres")) {
    stop("SNOWLVL raster units must be metres; received: ", units)
  }
  values <- terra::values(raster, mat = FALSE)
  finite <- is.finite(values) & values < 9999
  coverage <- mean(finite)
  if (!is.finite(coverage) || coverage < 0.95) stop("SNOWLVL finite coverage is below 95%.")
  range_m <- range(values[finite])
  if (range_m[1L] < -500 || range_m[2L] > 12000) {
    stop("SNOWLVL values are outside the physical validation range (-500..12000 m).")
  }
  list(
    finite_coverage = unname(coverage),
    min_m = unname(range_m[1L]),
    max_m = unname(range_m[2L]),
    rows = terra::nrow(raster), columns = terra::ncol(raster),
    resolution_m = unname(mean(terra::res(raster))),
    valid_time_utc = source_diagnostic$authoritative_valid_time_utc,
    decoder_divergence = isTRUE(source_diagnostic$decoder_divergence)
  )
}

wsl_bbox_polygon <- function(west, east, south, north, crs = "EPSG:4326") {
  sf::st_as_sfc(sf::st_bbox(c(xmin = west, ymin = south, xmax = east, ymax = north), crs = crs))
}

wsl_remove_consecutive_duplicates <- function(coordinates) {
  if (nrow(coordinates) < 2L) return(coordinates)
  changed <- c(
    TRUE,
    rowSums(abs(coordinates[-1L, , drop = FALSE] -
                  coordinates[-nrow(coordinates), , drop = FALSE])) > 0
  )
  coordinates[changed, , drop = FALSE]
}

wsl_lexicographic_less <- function(left, right) {
  left <- as.numeric(t(left))
  right <- as.numeric(t(right))
  difference <- which(left != right)
  if (!length(difference)) return(FALSE)
  left[difference[[1L]]] < right[difference[[1L]]]
}

wsl_canonical_coordinate_key <- function(coordinates, digits) {
  paste(
    formatC(as.numeric(t(coordinates)), format = "f", digits = as.integer(digits)),
    collapse = ","
  )
}

wsl_canonicalize_isoband_line <- function(coordinates, digits) {
  coordinates <- tryCatch(
    unclass(coordinates)[, 1:2, drop = FALSE],
    error = function(error) NULL
  )
  if (is.null(coordinates) || any(!is.finite(coordinates))) {
    wsl_stop("validation_failed", "Isoband contour coordinates are non-finite or malformed.")
  }
  coordinates <- wsl_remove_consecutive_duplicates(coordinates)
  coordinates <- round(coordinates, as.integer(digits))
  coordinates <- wsl_remove_consecutive_duplicates(coordinates)
  if (nrow(coordinates) < 2L || nrow(unique(coordinates)) < 2L) return(NULL)

  closed <- all(coordinates[1L, ] == coordinates[nrow(coordinates), ])
  if (!closed) {
    reversed <- coordinates[nrow(coordinates):1L, , drop = FALSE]
    if (wsl_lexicographic_less(reversed, coordinates)) coordinates <- reversed
    return(coordinates)
  }

  ring <- coordinates[-nrow(coordinates), , drop = FALSE]
  if (!nrow(ring)) return(NULL)
  minimum_x <- min(ring[, 1L])
  minimum_y <- min(ring[ring[, 1L] == minimum_x, 2L])
  canonical_candidates <- list()
  for (candidate_ring in list(ring, ring[nrow(ring):1L, , drop = FALSE])) {
    starts <- which(candidate_ring[, 1L] == minimum_x & candidate_ring[, 2L] == minimum_y)
    for (start in starts) {
      order <- c(
        seq.int(start, nrow(candidate_ring)),
        if (start > 1L) seq_len(start - 1L) else integer()
      )
      rotated <- candidate_ring[order, , drop = FALSE]
      canonical_candidates[[length(canonical_candidates) + 1L]] <- rbind(
        rotated, rotated[1L, , drop = FALSE]
      )
    }
  }
  best <- canonical_candidates[[1L]]
  if (length(canonical_candidates) > 1L) {
    for (candidate in canonical_candidates[-1L]) {
      if (wsl_lexicographic_less(candidate, best)) best <- candidate
    }
  }
  best
}

wsl_isoband_native_lines <- function(raster, levels_ft_msl) {
  if (terra::nlyr(raster) != 1L) {
    wsl_stop("validation_failed", "Isoband contour input must have exactly one raster layer.")
  }
  levels_ft_msl <- as.numeric(levels_ft_msl)
  if (!length(levels_ft_msl) || any(!is.finite(levels_ft_msl))) {
    wsl_stop("validation_failed", "Isoband contour levels are missing or non-finite.")
  }
  values_m <- terra::as.matrix(raster, wide = TRUE)
  if (!all(dim(values_m) == c(terra::nrow(raster), terra::ncol(raster)))) {
    wsl_stop("validation_failed", "Isoband contour matrix dimensions do not match the raster grid.")
  }
  x <- terra::xFromCol(raster, seq_len(terra::ncol(raster)))
  # Terra matrices run north-to-south. Isoband requires increasing y, so both
  # the y vector and matrix rows are reversed together without resampling.
  y <- terra::yFromRow(raster, terra::nrow(raster):1L)
  values_m <- values_m[terra::nrow(raster):1L, , drop = FALSE]
  levels_m <- levels_ft_msl / WSL_METERS_TO_FEET
  isolines <- isoband::isolines(x, y, values_m, levels = levels_m)

  geometries <- list()
  elevations <- numeric()
  level_indices <- integer()
  source_isoline_ids <- integer()
  for (level_index in seq_along(levels_ft_msl)) {
    isoline <- isolines[[level_index]]
    if (!length(isoline$x)) next
    for (source_id in unique(isoline$id)) {
      keep <- isoline$id == source_id
      coordinates <- cbind(isoline$x[keep], isoline$y[keep])
      coordinates <- wsl_remove_consecutive_duplicates(coordinates)
      if (nrow(coordinates) < 2L || any(!is.finite(coordinates))) {
        wsl_stop(
          "validation_failed", "Isoband emitted malformed native linework at level index ",
          level_index, " source isoline ", source_id, "."
        )
      }
      geometries[[length(geometries) + 1L]] <- sf::st_linestring(coordinates)
      elevations <- c(elevations, levels_ft_msl[[level_index]])
      level_indices <- c(level_indices, as.integer(level_index))
      source_isoline_ids <- c(source_isoline_ids, as.integer(source_id))
    }
  }
  if (!length(geometries)) {
    return(sf::st_sf(
      level_ft_msl = numeric(), level_index = integer(), source_isoline_id = integer(),
      geometry = sf::st_sfc(crs = sf::st_crs(terra::crs(raster)))
    ))
  }
  sf::st_sf(
    level_ft_msl = elevations,
    level_index = level_indices,
    source_isoline_id = source_isoline_ids,
    geometry = sf::st_sfc(geometries, crs = sf::st_crs(terra::crs(raster)))
  )
}

wsl_prepare_isoband_component <- function(
    coordinates, level_ft_msl, lineage, coordinate_digits
) {
  coordinates <- tryCatch(
    unclass(coordinates)[, 1:2, drop = FALSE],
    error = function(error) NULL
  )
  if (is.null(coordinates) || any(!is.finite(coordinates))) {
    wsl_stop("validation_failed", "Non-finite isoband component coordinates: ", lineage)
  }
  vertices_before <- nrow(coordinates)
  serialized_preview <- wsl_remove_consecutive_duplicates(coordinates)
  serialized_preview <- round(serialized_preview, as.integer(coordinate_digits))
  serialized_preview <- wsl_remove_consecutive_duplicates(serialized_preview)
  distinct_serialized_positions <- nrow(unique(serialized_preview))
  canonical <- wsl_canonicalize_isoband_line(coordinates, coordinate_digits)
  if (is.null(canonical)) {
    return(list(
      retained = FALSE,
      removal = list(
        lineage = lineage,
        level_ft_msl = as.integer(level_ft_msl),
        vertices_before = vertices_before,
        distinct_serialized_positions = distinct_serialized_positions
      )
    ))
  }
  distinct_positions <- nrow(unique(canonical))
  if (nrow(canonical) < 2L || distinct_positions < 2L) {
    return(list(
      retained = FALSE,
      removal = list(
        lineage = lineage,
        level_ft_msl = as.integer(level_ft_msl),
        vertices_before = vertices_before,
        distinct_serialized_positions = distinct_positions
      )
    ))
  }

  projected <- sf::st_transform(
    wsl_geometry_from_coordinates(canonical, 4326), WSL_CARTOGRAPHIC_CRS
  )
  if (isTRUE(sf::st_is_empty(projected)) || !isTRUE(sf::st_is_valid(projected))) {
    wsl_stop("validation_failed", "Isoband component is empty or structurally invalid: ", lineage)
  }
  length_m <- suppressWarnings(as.numeric(sf::st_length(projected)))
  if (length(length_m) != 1L || !is.finite(length_m) || length_m <= 0) {
    wsl_stop("validation_failed", "Isoband component has non-positive length: ", lineage)
  }
  list(
    retained = TRUE,
    row = list(
      level = as.integer(level_ft_msl),
      length_m = round(length_m, 1),
      coordinates = canonical,
      key = sprintf(
        "%05d|%s", as.integer(level_ft_msl),
        wsl_canonical_coordinate_key(canonical, coordinate_digits)
      ),
      lineage = lineage,
      non_simple = !isTRUE(sf::st_is_simple(projected)),
      vertices_before = vertices_before,
      vertices_after = nrow(canonical)
    )
  )
}

wsl_geometry_from_coordinates <- function(coordinates, crs = 4326) {
  sf::st_sfc(sf::st_linestring(coordinates), crs = crs)
}

wsl_make_contours <- function(raster, record, config) {
  source_crs <- terra::crs(raster)
  source_box <- terra::vect(wsl_bbox_polygon(
    config$source_west, config$source_east, config$source_south, config$source_north
  ))
  source_box <- terra::project(source_box, source_crs)
  cropped <- terra::crop(raster, source_box, snap = "out")
  cropped[cropped >= 9999] <- NA
  levels_ft_msl <- as.integer(seq(
    config$contour_min_ft, config$contour_max_ft, by = config$contour_interval_ft
  ))
  contours_sf <- wsl_isoband_native_lines(cropped, levels_ft_msl)
  if (!nrow(contours_sf)) {
    stop("No configured snow-level contours intersect the source crop.")
  }

  processing_crs <- source_crs
  if (isTRUE(sf::st_is_longlat(contours_sf))) {
    processing_crs <- WSL_CARTOGRAPHIC_CRS
    contours_sf <- sf::st_transform(contours_sf, processing_crs)
  }

  extract_lines <- function(value) {
    value <- value[!sf::st_is_empty(value), , drop = FALSE]
    if (!nrow(value)) return(value)
    value <- suppressWarnings(sf::st_collection_extract(value, "LINESTRING"))
    value <- suppressWarnings(sf::st_cast(value, "LINESTRING"))
    value[!sf::st_is_empty(value), , drop = FALSE]
  }

  display <- sf::st_transform(wsl_bbox_polygon(
    config$west, config$east, config$south, config$north
  ), processing_crs)
  previous_s2 <- suppressMessages(sf::sf_use_s2(FALSE))
  on.exit(suppressMessages(sf::sf_use_s2(previous_s2)), add = TRUE)
  clipped <- suppressMessages(suppressWarnings(sf::st_intersection(contours_sf, display)))
  clipped <- extract_lines(clipped)
  if (!nrow(clipped)) stop("No snow-level contours intersect the display domain.")
  # A final planar lon/lat clip keeps serialized API bounds exact.
  clipped <- sf::st_transform(clipped, 4326)
  clipped <- suppressMessages(suppressWarnings(sf::st_intersection(
    clipped,
    wsl_bbox_polygon(config$west, config$east, config$south, config$north)
  )))
  clipped <- extract_lines(clipped)
  if (!nrow(clipped)) stop("No WGS84 snow-level contours intersect the display domain.")
  clipped$clip_part <- seq_len(nrow(clipped))

  rows <- list()
  serialization_collapse_removals <- list()
  for (index in seq_len(nrow(clipped))) {
    level <- as.integer(clipped$level_ft_msl[[index]])
    lineage <- sprintf(
      "level_index=%d source_isoline_id=%d clip_part=%d",
      as.integer(clipped$level_index[[index]]),
      as.integer(clipped$source_isoline_id[[index]]),
      as.integer(clipped$clip_part[[index]])
    )
    coordinates <- sf::st_coordinates(sf::st_geometry(clipped)[[index]])[, 1:2, drop = FALSE]
    prepared <- wsl_prepare_isoband_component(
      coordinates, level, lineage, config$coordinate_digits
    )
    if (!isTRUE(prepared$retained)) {
      serialization_collapse_removals[[length(serialization_collapse_removals) + 1L]] <-
        prepared$removal
      next
    }
    rows[[length(rows) + 1L]] <- prepared$row
  }
  rows <- rows[order(vapply(rows, `[[`, character(1), "key"))]
  if (!length(rows)) stop("Canonical serialization removed every isoband contour line.")

  level_counts <- integer()
  features <- lapply(seq_along(rows), function(index) {
    row <- rows[[index]]
    key <- as.character(row$level)
    level_counts[key] <<- (level_counts[key] %||% 0L) + 1L
    feature_id <- sprintf(
      "%s_f%03d_%05d_%03d", wsl_cycle_token(record$cycle_time_utc),
      record$lead_hours, row$level, level_counts[key]
    )
    list(
      type = "Feature",
      id = feature_id,
      properties = list(
        product_id = config$product_id,
        source_id = config$source_id,
        parameter = "snow_level",
        definition = "height of the wet-bulb 0.5 degree C surface",
        level_ft_msl = row$level,
        label = sprintf("%s ft MSL", format(row$level, big.mark = ",", scientific = FALSE)),
        unit = "ft_msl",
        cycle_time_utc = record$cycle_time_utc,
        valid_time_utc = record$valid_time_utc,
        lead_hours = record$lead_hours,
        segment = level_counts[key],
        length_m = row$length_m
      ),
      geometry = list(type = "LineString", coordinates = unname(split(row$coordinates, row(row$coordinates))))
    )
  })
  all_coordinates <- do.call(rbind, lapply(rows, `[[`, "coordinates"))
  bbox <- c(min(all_coordinates[, 1L]), min(all_coordinates[, 2L]),
            max(all_coordinates[, 1L]), max(all_coordinates[, 2L]))
  base_geojson <- list(
    type = "FeatureCollection",
    contract_version = config$contract_version,
    bbox = unname(bbox),
    features = features
  )
  closed <- vapply(rows, function(row) {
    coordinates <- row$coordinates
    all(coordinates[1L, ] == coordinates[nrow(coordinates), ])
  }, logical(1))
  diagnostics <- list(
    contour_engine = "isoband",
    source_raster_rows = terra::nrow(raster),
    source_raster_columns = terra::ncol(raster),
    source_raster_cells = terra::ncell(raster),
    contour_input_rows = terra::nrow(cropped),
    contour_input_columns = terra::ncol(cropped),
    contour_input_cells = terra::ncell(cropped),
    full_native_source_crop_passed = TRUE,
    raster_resampled = FALSE,
    levels_requested_ft_msl = levels_ft_msl,
    levels_requested_m = unname(levels_ft_msl / WSL_METERS_TO_FEET),
    source_isoline_count = nrow(contours_sf),
    clipped_component_count = nrow(clipped),
    components_retained = length(rows),
    open_count = sum(!closed),
    closed_count = sum(closed),
    non_simple_count = sum(vapply(rows, `[[`, logical(1), "non_simple")),
    vertices_before_serialization = sum(vapply(rows, `[[`, integer(1), "vertices_before")),
    vertices = sum(vapply(rows, `[[`, integer(1), "vertices_after")),
    serialization_collapse_removed_count = length(serialization_collapse_removals),
    serialization_collapse_removals = serialization_collapse_removals
  )
  list(
    geojson = base_geojson,
    feature_count = length(features),
    contour_levels = sort(unique(vapply(features, function(feature) {
      as.integer(feature$properties$level_ft_msl)
    }, integer(1)))),
    bbox = base_geojson$bbox,
    cartography = diagnostics
  )
}

wsl_write_json <- function(value, path, pretty = TRUE) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  text <- jsonlite::toJSON(value, auto_unbox = TRUE, null = "null", na = "null",
                           digits = NA, pretty = pretty)
  writeLines(c(text, ""), path, useBytes = TRUE)
  invisible(path)
}

wsl_sha256 <- function(path) digest::digest(file = path, algo = "sha256")

wsl_safe_target_path <- function(path) {
  expected <- "^nbm/snow-level/winter_storm_levels_nbm_snow_level_[0-9]{10}_f[0-9]{3}_[0-9a-f]{12}\\.geojson$"
  if (length(path) != 1L || is.na(path) || !nzchar(path) || startsWith(path, "/") ||
      grepl("\\\\", path) || any(strsplit(path, "/", fixed = TRUE)[[1L]] %in% c("", ".", "..")) ||
      !grepl(expected, path)) {
    stop("Unsafe Winter Storm Levels target path: ", path)
  }
  path
}

wsl_target_relative_path <- function(record, content_sha256 = NULL) {
  if (is.null(content_sha256) || !grepl("^[0-9a-f]{64}$", content_sha256)) {
    stop("A full lowercase SHA-256 is required for a Winter Storm Levels target path.")
  }
  content_suffix <- paste0("_", substr(content_sha256, 1L, 12L))
  sprintf(
    "nbm/snow-level/winter_storm_levels_nbm_snow_level_%s_f%03d%s.geojson",
    wsl_cycle_token(record$cycle_time_utc), record$lead_hours, content_suffix
  )
}

wsl_freshness_status <- function(cycle_time, now, config, has_active_target = TRUE) {
  age_hours <- as.numeric(difftime(wsl_as_utc(now), wsl_as_utc(cycle_time), units = "hours"))
  if (!has_active_target || age_hours > config$expire_after_hours) return("expired")
  if (age_hours > config$delayed_after_hours) return("stale_last_known_good")
  if (age_hours > config$current_after_hours) return("delayed_but_usable")
  "current"
}

wsl_target_entry <- function(record, relative_path, stats, contour, file_path, config) {
  valid <- wsl_as_utc(record$valid_time_utc)
  list(
    source_id = config$source_id,
    cycle_time_utc = record$cycle_time_utc,
    valid_time_utc = record$valid_time_utc,
    valid_from_utc = wsl_iso_utc(valid - config$valid_tolerance_hours * 3600),
    valid_through_utc = wsl_iso_utc(valid + config$valid_tolerance_hours * 3600),
    lead_hours = record$lead_hours,
    retrieval_url = record$grib_url,
    inventory_url = record$index_url,
    inventory_record = record$inventory_line,
    path = relative_path,
    media_type = "application/geo+json",
    sha256 = wsl_sha256(file_path),
    bytes = unname(file.info(file_path)$size),
    feature_count = contour$feature_count,
    contour_levels_ft_msl = contour$contour_levels,
    source_grid = list(
      rows = stats$rows, columns = stats$columns,
      resolution_m = round(stats$resolution_m, 3),
      finite_coverage = round(stats$finite_coverage, 6),
      min_m = round(stats$min_m, 3), max_m = round(stats$max_m, 3)
    ),
    output_bbox_wgs84 = contour$bbox
  )
}

wsl_manifest <- function(cycle_time, entries, now, config) {
  has_active <- any(vapply(entries, function(entry) {
    wsl_as_utc(now) >= wsl_as_utc(entry$valid_from_utc) &&
      wsl_as_utc(now) <= wsl_as_utc(entry$valid_through_utc)
  }, logical(1)))
  selected_cycle <- wsl_iso_utc(cycle_time)
  current_cycle_count <- sum(vapply(entries, function(entry) {
    identical(entry$cycle_time_utc, selected_cycle)
  }, logical(1)))
  list(
    product_id = config$product_id,
    schema_version = config$contract_version,
    contract_version = config$contract_version,
    status = wsl_freshness_status(cycle_time, now, config, has_active),
    source = list(
      id = config$source_id,
      name = "NOAA/NWS National Blend of Models deterministic snow level",
      agency = "NOAA/NWS/NCEP Meteorological Development Laboratory",
      parameter = "SNOWLVL",
      definition = "Elevation where wet-bulb temperature reaches 0.5 degrees C",
      source_unit = "m above mean sea level",
      output_unit = "ft above mean sea level",
      product_url = "https://www.nco.ncep.noaa.gov/pmb/products/blend/",
      retrieval_base_url = "https://noaa-nbm-grib2-pds.s3.amazonaws.com/",
      alternate_retrieval_base_url = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/"
    ),
    domain = list(
      id = config$domain_id, label = config$domain_label,
      bbox_wgs84 = c(config$west, config$south, config$east, config$north)
    ),
    contour = list(
      geometry = "LineString", datum = "mean_sea_level", unit = "ft_msl",
      minimum_ft = config$contour_min_ft, maximum_ft = config$contour_max_ft,
      interval_ft = config$contour_interval_ft,
      simplify_tolerance_m = config$simplify_tolerance_m
    ),
    freshness = list(
      current_after_hours = config$current_after_hours,
      delayed_after_hours = config$delayed_after_hours,
      expire_after_hours = config$expire_after_hours,
      valid_tolerance_hours = config$valid_tolerance_hours
    ),
    cycle_time_utc = wsl_iso_utc(cycle_time),
    retrieval_time_utc = wsl_iso_utc(now),
    # Git commit, push, and Pages deployment happen after the builder completes.
    # Version 1 therefore leaves publication time explicitly unknown.
    publication_time_utc = NULL,
    target_count = length(entries),
    diagnostics = list(
      expected_current_cycle_target_count = length(config$forecast_lead_hours),
      actual_current_cycle_target_count = current_cycle_count,
      retained_cycle_count = length(unique(vapply(entries, `[[`, character(1), "cycle_time_utc"))),
      complete_bundle_validated = TRUE
    ),
    targets = entries
  )
}

wsl_validate_geojson <- function(path, record, config) {
  payload <- jsonlite::fromJSON(path, simplifyVector = FALSE)
  if (!all(c("type", "contract_version", "bbox", "features") %in% names(payload))) {
    stop("GeoJSON root fields are incomplete.")
  }
  if (!identical(payload$type, "FeatureCollection")) stop("GeoJSON root is not a FeatureCollection.")
  if (!identical(payload$contract_version, config$contract_version)) {
    stop("GeoJSON contract version mismatch.")
  }
  if (!length(payload$features)) stop("GeoJSON has no contour features.")
  required <- c("product_id", "source_id", "parameter", "definition", "level_ft_msl",
                "label", "unit", "cycle_time_utc", "valid_time_utc", "lead_hours",
                "segment", "length_m")
  ids <- character()
  all_coordinates <- list()
  for (feature in payload$features) {
    if (!all(c("type", "id", "properties", "geometry") %in% names(feature)) ||
        !identical(feature$type, "Feature") || !is.character(feature$id) ||
        length(feature$id) != 1L || !nzchar(feature$id)) {
      stop("GeoJSON feature identity is incomplete.")
    }
    if (!identical(feature$geometry$type, "LineString")) stop("Unexpected GeoJSON geometry type.")
    if (!all(required %in% names(feature$properties))) stop("GeoJSON feature properties are incomplete.")
    properties <- feature$properties
    if (!identical(properties$product_id, config$product_id) ||
        !identical(properties$source_id, config$source_id) ||
        !identical(properties$parameter, "snow_level") ||
        !identical(properties$definition, "height of the wet-bulb 0.5 degree C surface") ||
        !identical(properties$unit, "ft_msl")) {
      stop("GeoJSON feature product/source/parameter/unit semantics are invalid.")
    }
    if (!identical(feature$properties$cycle_time_utc, record$cycle_time_utc) ||
        !identical(feature$properties$valid_time_utc, record$valid_time_utc) ||
        as.numeric(feature$properties$lead_hours) != as.numeric(record$lead_hours)) {
      stop("GeoJSON feature time metadata does not match its target.")
    }
    level <- as.numeric(properties$level_ft_msl)
    segment <- as.numeric(properties$segment)
    length_m <- as.numeric(properties$length_m)
    if (!is.finite(level) || level < config$contour_min_ft || level > config$contour_max_ft ||
        level %% config$contour_interval_ft != 0 || !is.character(properties$label) ||
        length(properties$label) != 1L || !nzchar(properties$label) ||
        !is.finite(segment) || segment < 1 || segment %% 1 != 0 ||
        !is.finite(length_m) || length_m <= 0) {
      stop("GeoJSON contour level, label, segment, or length is invalid.")
    }
    flat_coordinates <- as.numeric(unlist(feature$geometry$coordinates))
    if (length(flat_coordinates) < 4L || length(flat_coordinates) %% 2L != 0L) {
      stop("GeoJSON LineString coordinates are incomplete.")
    }
    coordinates <- matrix(
      flat_coordinates, ncol = 2L, byrow = TRUE
    )
    if (any(!is.finite(coordinates))) stop("GeoJSON coordinates contain non-finite values.")
    if (any(coordinates[, 1L] < config$west - 1e-6 | coordinates[, 1L] > config$east + 1e-6 |
            coordinates[, 2L] < config$south - 1e-6 | coordinates[, 2L] > config$north + 1e-6)) {
      stop("GeoJSON coordinates exceed the configured output bounds.")
    }
    if (any(rowSums(abs(
      coordinates[-1L, , drop = FALSE] - coordinates[-nrow(coordinates), , drop = FALSE]
    )) == 0)) {
      stop("GeoJSON coordinates contain adjacent duplicates.")
    }
    projected_line <- sf::st_transform(
      wsl_geometry_from_coordinates(coordinates, 4326), WSL_CARTOGRAPHIC_CRS
    )
    if (sf::st_is_empty(projected_line) || !isTRUE(sf::st_is_valid(projected_line))) {
      stop("GeoJSON contains empty or structurally invalid linework.")
    }
    ids <- c(ids, feature$id)
    all_coordinates[[length(all_coordinates) + 1L]] <- coordinates
  }
  if (anyDuplicated(ids)) stop("GeoJSON feature IDs are not unique.")
  coordinates <- do.call(rbind, all_coordinates)
  actual_bbox <- c(min(coordinates[, 1L]), min(coordinates[, 2L]),
                   max(coordinates[, 1L]), max(coordinates[, 2L]))
  declared_bbox <- as.numeric(unlist(payload$bbox))
  if (length(declared_bbox) != 4L || any(!is.finite(declared_bbox)) ||
      any(abs(declared_bbox - actual_bbox) > 10 ^ (-config$coordinate_digits))) {
    stop("GeoJSON bbox does not match its serialized coordinates.")
  }
  invisible(payload)
}

wsl_validate_manifest <- function(path, root, config, now = Sys.time()) {
  manifest <- jsonlite::fromJSON(path, simplifyVector = FALSE)
  scalar_number <- function(value) {
    length(value) == 1L && is.numeric(value) && is.finite(value)
  }
  root_fields <- c("product_id", "schema_version", "contract_version", "status", "source", "domain",
                   "contour", "freshness", "cycle_time_utc", "retrieval_time_utc",
                   "publication_time_utc", "target_count", "diagnostics", "targets")
  if (!all(root_fields %in% names(manifest))) stop("Manifest root fields are incomplete.")
  if (!identical(manifest$product_id, config$product_id)) stop("Manifest product_id mismatch.")
  if (!identical(manifest$schema_version, config$contract_version)) stop("Manifest schema version mismatch.")
  if (!identical(manifest$contract_version, config$contract_version)) stop("Manifest contract version mismatch.")
  if (!is.null(manifest$publication_time_utc)) {
    stop("Manifest publication_time_utc must be null; the builder cannot know publication time.")
  }
  if (!identical(manifest$source$id, config$source_id) ||
      !identical(manifest$source$parameter, "SNOWLVL") ||
      !identical(manifest$source$source_unit, "m above mean sea level") ||
      !identical(manifest$source$output_unit, "ft above mean sea level")) {
    stop("Manifest source semantics mismatch.")
  }
  if (!identical(as.numeric(unlist(manifest$domain$bbox_wgs84)),
                 c(config$west, config$south, config$east, config$north))) {
    stop("Manifest domain bounds mismatch.")
  }
  if (!identical(manifest$contour$geometry, "LineString") ||
      !identical(manifest$contour$datum, "mean_sea_level") ||
      !identical(manifest$contour$unit, "ft_msl") ||
      as.numeric(manifest$contour$minimum_ft) != config$contour_min_ft ||
      as.numeric(manifest$contour$maximum_ft) != config$contour_max_ft ||
      as.numeric(manifest$contour$interval_ft) != config$contour_interval_ft) {
    stop("Manifest contour contract mismatch.")
  }
  expected_freshness <- c(config$current_after_hours, config$delayed_after_hours,
                          config$expire_after_hours, config$valid_tolerance_hours)
  actual_freshness <- as.numeric(unlist(manifest$freshness[c(
    "current_after_hours", "delayed_after_hours", "expire_after_hours", "valid_tolerance_hours"
  )]))
  if (length(actual_freshness) != 4L || !identical(actual_freshness, expected_freshness)) {
    stop("Manifest freshness contract mismatch.")
  }
  if (!length(manifest$targets)) stop("Manifest has no targets.")
  if (!scalar_number(manifest$target_count) || manifest$target_count %% 1 != 0 ||
      length(manifest$targets) != manifest$target_count) stop("Manifest target count mismatch.")
  if (!scalar_number(manifest$diagnostics$expected_current_cycle_target_count) ||
      as.numeric(manifest$diagnostics$expected_current_cycle_target_count) !=
        length(config$forecast_lead_hours) ||
      !scalar_number(manifest$diagnostics$actual_current_cycle_target_count) ||
      as.numeric(manifest$diagnostics$actual_current_cycle_target_count) !=
        sum(vapply(manifest$targets, function(entry) {
          identical(entry$cycle_time_utc, manifest$cycle_time_utc)
        }, logical(1))) ||
      !scalar_number(manifest$diagnostics$retained_cycle_count) ||
      as.numeric(manifest$diagnostics$retained_cycle_count) !=
        length(unique(vapply(manifest$targets, `[[`, character(1), "cycle_time_utc"))) ||
      !identical(manifest$diagnostics$complete_bundle_validated, TRUE)) {
    stop("Manifest diagnostics summary mismatch.")
  }
  if (!is.character(manifest$status) || length(manifest$status) != 1L ||
      !manifest$status %in% c("current", "delayed_but_usable", "stale_last_known_good", "expired")) {
    stop("Manifest status is invalid.")
  }
  keys <- character()
  for (entry in manifest$targets) {
    entry_fields <- c(
      "source_id", "cycle_time_utc", "valid_time_utc", "valid_from_utc",
      "valid_through_utc", "lead_hours", "retrieval_url", "inventory_url",
      "inventory_record", "path", "media_type", "sha256", "bytes",
      "feature_count", "contour_levels_ft_msl", "source_grid", "output_bbox_wgs84"
    )
    if (!all(entry_fields %in% names(entry))) stop("Manifest target fields are incomplete.")
    relative_path <- wsl_safe_target_path(entry$path)
    target <- file.path(root, relative_path)
    if (!file.exists(target)) stop("Manifest target is missing: ", entry$path)
    if (!identical(entry$source_id, config$source_id)) stop("Manifest target source mismatch.")
    if (!is.character(entry$sha256) || length(entry$sha256) != 1L ||
        !grepl("^[0-9a-f]{64}$", entry$sha256)) stop("Manifest target SHA-256 is invalid.")
    if (!scalar_number(entry$bytes) || entry$bytes < 1 || entry$bytes %% 1 != 0) {
      stop("Manifest target byte count is invalid.")
    }
    expected_path <- wsl_target_relative_path(
      list(cycle_time_utc = entry$cycle_time_utc, lead_hours = entry$lead_hours), entry$sha256
    )
    if (!identical(relative_path, expected_path)) stop("Manifest target path identity mismatch.")
    cycle <- wsl_as_utc(entry$cycle_time_utc)
    valid <- wsl_as_utc(entry$valid_time_utc)
    if (is.na(cycle) || is.na(valid) ||
        !identical(wsl_iso_utc(cycle + as.numeric(entry$lead_hours) * 3600), entry$valid_time_utc)) {
      stop("Manifest target cycle/lead/valid time mismatch.")
    }
    if (!identical(wsl_iso_utc(valid - config$valid_tolerance_hours * 3600), entry$valid_from_utc) ||
        !identical(wsl_iso_utc(valid + config$valid_tolerance_hours * 3600), entry$valid_through_utc)) {
      stop("Manifest target validity window mismatch.")
    }
    if (!identical(entry$media_type, "application/geo+json") ||
        !scalar_number(entry$feature_count) || entry$feature_count < 1 ||
        entry$feature_count %% 1 != 0) {
      stop("Manifest target media type or feature count is invalid.")
    }
    if (!is.character(entry$retrieval_url) || length(entry$retrieval_url) != 1L ||
        !startsWith(entry$retrieval_url, "https://") ||
        !is.character(entry$inventory_url) || length(entry$inventory_url) != 1L ||
        !startsWith(entry$inventory_url, "https://") ||
        !endsWith(entry$inventory_url, ".idx") ||
        !is.character(entry$inventory_record) || length(entry$inventory_record) != 1L ||
        !grepl(":SNOWLVL:0 m above mean sea level:", entry$inventory_record, fixed = TRUE)) {
      stop("Manifest target source provenance is invalid.")
    }
    if (!identical(wsl_sha256(target), entry$sha256)) stop("Manifest target checksum mismatch: ", entry$path)
    if (!identical(unname(file.info(target)$size), as.numeric(entry$bytes))) stop("Manifest target size mismatch.")
    payload <- wsl_validate_geojson(
      target,
      list(cycle_time_utc = entry$cycle_time_utc,
           valid_time_utc = entry$valid_time_utc,
           lead_hours = entry$lead_hours),
      config
    )
    levels <- sort(unique(vapply(payload$features, function(feature) {
      as.numeric(feature$properties$level_ft_msl)
    }, numeric(1))))
    if (length(payload$features) != as.integer(entry$feature_count) ||
        !identical(levels, sort(as.numeric(unlist(entry$contour_levels_ft_msl)))) ||
        any(abs(as.numeric(unlist(payload$bbox)) -
                as.numeric(unlist(entry$output_bbox_wgs84))) > 10 ^ (-config$coordinate_digits))) {
      stop("Manifest target feature count, contour levels, or bounds mismatch.")
    }
    keys <- c(keys, paste(entry$cycle_time_utc, entry$valid_time_utc, sep = "|"))
  }
  if (anyDuplicated(keys)) stop("Manifest contains duplicate cycle/valid-time entries.")
  cycles <- unique(vapply(manifest$targets, `[[`, character(1), "cycle_time_utc"))
  if (length(cycles) > as.integer(config$retain_cycle_count)) {
    stop("Manifest exceeds the configured retained-cycle limit.")
  }
  for (cycle in cycles) {
    cycle_leads <- sort(vapply(Filter(function(entry) entry$cycle_time_utc == cycle,
                                      manifest$targets),
                               function(entry) as.integer(entry$lead_hours), integer(1)))
    if (!identical(cycle_leads, as.integer(config$forecast_lead_hours))) {
      stop("Manifest cycle forecast-hour set is incomplete or unexpected: ", cycle)
    }
  }
  expected_target_count <- length(cycles) * length(config$forecast_lead_hours)
  if (length(manifest$targets) != expected_target_count) {
    stop("Manifest does not contain exactly one complete target set per retained cycle.")
  }
  target_cycles <- vapply(manifest$targets, function(entry) wsl_as_utc(entry$cycle_time_utc), as.POSIXct(NA))
  if (!identical(wsl_iso_utc(max(target_cycles)), manifest$cycle_time_utc)) {
    stop("Manifest root cycle is not the newest target cycle.")
  }
  invisible(manifest)
}

wsl_copy_retained_cycles <- function(
    candidate_root,
    entries,
    selected_cycle,
    config,
    canonical_root,
    manifest_name = "winter_storm_levels_manifest.json",
    now = Sys.time(),
    copy_file = file.copy
) {
  prior_manifest_path <- file.path(canonical_root, manifest_name)
  if (!file.exists(prior_manifest_path)) return(entries)

  # The prior manifest is the retention authority. Any malformed manifest,
  # unsafe path, missing target, checksum mismatch, or incomplete cycle fails
  # before the candidate can reach canonical promotion.
  legacy_config <- config
  legacy_config$forecast_lead_hours <- WSL_LEGACY_TARGET_LEADS
  legacy_state <- FALSE
  current_validation <- tryCatch(
    {
      wsl_validate_manifest(prior_manifest_path, canonical_root, config, now)
      NULL
    },
    error = function(error) error
  )
  if (inherits(current_validation, "error")) {
    legacy_validation <- tryCatch(
      {
        wsl_validate_manifest(prior_manifest_path, canonical_root, legacy_config, now)
        NULL
      },
      error = function(error) error
    )
    if (inherits(legacy_validation, "error")) {
      wsl_stop(
        "validation_failed",
        "Prior canonical Snow state is neither complete f240 nor the exact legacy migration state: ",
        conditionMessage(current_validation), "; legacy: ", conditionMessage(legacy_validation)
      )
    }
    legacy_state <- TRUE
  }
  prior <- jsonlite::fromJSON(prior_manifest_path, simplifyVector = FALSE)
  selected_cycle_utc <- wsl_iso_utc(selected_cycle)
  selected_cycle_time <- wsl_as_utc(selected_cycle_utc)
  prior_root_time <- wsl_as_utc(prior$cycle_time_utc)
  if (is.na(selected_cycle_time) || is.na(prior_root_time)) {
    wsl_stop("validation_failed", "Selected or prior root cycle is invalid.")
  }
  if (isTRUE(legacy_state) && selected_cycle_time <= prior_root_time) {
    wsl_stop(
      "validation_failed",
      "Selected f240 migration cycle must be newer than legacy canonical cycle ",
      prior$cycle_time_utc, "."
    )
  }
  if (selected_cycle_time < prior_root_time) {
    wsl_stop(
      "validation_failed",
      "Selected cycle ", selected_cycle_utc,
      " is older than prior canonical root cycle ", prior$cycle_time_utc, "."
    )
  }
  if (isTRUE(legacy_state)) {
    # Sparse legacy cycles cannot be represented as complete f240 cycles. The
    # first accepted f240 run therefore establishes one complete migration
    # cycle; the next accepted run restores normal exact two-cycle retention.
    return(entries)
  }

  prior_cycles <- unique(vapply(prior$targets, `[[`, character(1), "cycle_time_utc"))
  prior_cycles <- sort(prior_cycles, decreasing = TRUE)
  if (selected_cycle_time > prior_root_time) {
    keep_cycles <- prior$cycle_time_utc
  } else {
    keep_cycles <- head(
      setdiff(prior_cycles, selected_cycle_utc),
      as.integer(config$retain_cycle_count) - 1L
    )
  }
  retained <- Filter(function(entry) entry$cycle_time_utc %in% keep_cycles, prior$targets)
  expected_retained <- length(keep_cycles) * length(config$forecast_lead_hours)
  if (length(retained) != expected_retained) {
    wsl_stop("validation_failed", "Prior canonical retention set is incomplete.")
  }

  copied <- list()
  for (entry in retained) {
    relative_path <- wsl_safe_target_path(entry$path)
    source <- file.path(canonical_root, relative_path)
    destination <- file.path(candidate_root, relative_path)
    if (!file.exists(source)) {
      wsl_stop("validation_failed", "Retained canonical target is missing: ", relative_path)
    }
    if (!identical(wsl_sha256(source), entry$sha256)) {
      wsl_stop("validation_failed", "Retained canonical target checksum mismatch: ", relative_path)
    }
    dir.create(dirname(destination), recursive = TRUE, showWarnings = FALSE)
    copied_ok <- tryCatch(
      isTRUE(copy_file(source, destination, overwrite = TRUE, copy.mode = TRUE)),
      error = function(error) FALSE
    )
    if (!copied_ok || !file.exists(destination) ||
        !identical(wsl_sha256(destination), entry$sha256)) {
      wsl_stop("publication_failed", "Could not retain canonical target: ", relative_path)
    }
    copied[[length(copied) + 1L]] <- entry
  }

  result <- c(entries, copied)
  result_cycles <- unique(vapply(result, `[[`, character(1), "cycle_time_utc"))
  if (length(result_cycles) > as.integer(config$retain_cycle_count) ||
      length(result) != length(result_cycles) * length(config$forecast_lead_hours)) {
    wsl_stop("validation_failed", "Candidate retention result is not exactly one or two complete cycles.")
  }
  result
}

wsl_require_current_bootstrap <- function(manifest, now, config, has_prior_canonical) {
  if (isTRUE(has_prior_canonical)) return(invisible(TRUE))
  root_targets <- Filter(function(entry) {
    identical(entry$cycle_time_utc, manifest$cycle_time_utc)
  }, manifest$targets)
  has_active_root_target <- any(vapply(root_targets, function(entry) {
    wsl_as_utc(now) >= wsl_as_utc(entry$valid_from_utc) &&
      wsl_as_utc(now) <= wsl_as_utc(entry$valid_through_utc)
  }, logical(1)))
  complete_single_cycle <- length(root_targets) == length(config$forecast_lead_hours) &&
    length(manifest$targets) == length(config$forecast_lead_hours) &&
    as.numeric(manifest$diagnostics$retained_cycle_count) == 1
  if (!identical(manifest$status, "current") || !has_active_root_target ||
      !complete_single_cycle) {
    wsl_stop(
      "validation_failed",
      "First canonical Winter Storm Levels seed must be one complete current cycle with an active target."
    )
  }
  invisible(TRUE)
}

wsl_semantic_manifest <- function(manifest) {
  manifest$retrieval_time_utc <- NULL
  manifest$publication_time_utc <- NULL
  manifest
}

wsl_is_semantic_noop <- function(candidate_path, canonical_path) {
  if (!file.exists(canonical_path)) return(FALSE)
  candidate <- jsonlite::fromJSON(candidate_path, simplifyVector = FALSE)
  canonical <- jsonlite::fromJSON(canonical_path, simplifyVector = FALSE)
  identical(wsl_semantic_manifest(candidate), wsl_semantic_manifest(canonical))
}

wsl_promote_bundle <- function(candidate_root, canonical_root, manifest_name, config,
                               fail_before_manifest = FALSE, now = Sys.time()) {
  candidate_manifest_path <- file.path(candidate_root, manifest_name)
  wsl_validate_manifest(candidate_manifest_path, candidate_root, config, now)
  candidate <- jsonlite::fromJSON(candidate_manifest_path, simplifyVector = FALSE)
  dir.create(canonical_root, recursive = TRUE, showWarnings = FALSE)
  canonical_manifest_path <- file.path(canonical_root, manifest_name)
  if (wsl_is_semantic_noop(candidate_manifest_path, canonical_manifest_path)) {
    return(list(changed = FALSE, promoted = character(), removed = character()))
  }

  promoted <- character()
  created <- character()
  backup_root <- tempfile("winter-storm-level-target-backup-", tmpdir = canonical_root)
  dir.create(backup_root, recursive = TRUE, showWarnings = FALSE)
  on.exit(unlink(backup_root, recursive = TRUE, force = TRUE), add = TRUE)
  backups <- list()
  rollback_targets <- function() {
    if (length(created)) unlink(created, force = TRUE)
    if (length(backups)) {
      for (destination in names(backups)) {
        dir.create(dirname(destination), recursive = TRUE, showWarnings = FALSE)
        file.copy(backups[[destination]], destination, overwrite = TRUE, copy.mode = TRUE)
      }
    }
  }
  for (entry in candidate$targets) {
    relative_path <- wsl_safe_target_path(entry$path)
    source <- file.path(candidate_root, relative_path)
    destination <- file.path(canonical_root, relative_path)
    if (!file.exists(source) || !identical(wsl_sha256(source), entry$sha256)) {
      rollback_targets()
      stop("Candidate target is missing or does not match its checksum: ", relative_path)
    }
    dir.create(dirname(destination), recursive = TRUE, showWarnings = FALSE)
    if (file.exists(destination) && identical(wsl_sha256(destination), entry$sha256)) next
    if (file.exists(destination)) {
      backup <- file.path(backup_root, entry$path)
      dir.create(dirname(backup), recursive = TRUE, showWarnings = FALSE)
      if (!file.copy(destination, backup, overwrite = TRUE, copy.mode = TRUE)) {
        rollback_targets()
        wsl_stop("publication_failed", "Could not back up existing target: ", entry$path)
      }
      backups[[destination]] <- backup
    } else {
      created <- c(created, destination)
    }
    if (!file.copy(source, destination, overwrite = TRUE, copy.mode = TRUE)) {
      rollback_targets()
      wsl_stop("publication_failed", "Could not promote target: ", entry$path)
    }
    promoted <- c(promoted, destination)
  }
  if (isTRUE(fail_before_manifest)) {
    rollback_targets()
    wsl_stop("publication_failed", "Injected failure before manifest promotion.")
  }
  staged_manifest <- paste0(canonical_manifest_path, ".candidate")
  if (!file.copy(candidate_manifest_path, staged_manifest, overwrite = TRUE, copy.mode = TRUE)) {
    rollback_targets()
    wsl_stop("publication_failed", "Could not stage manifest.")
  }
  if (!file.rename(staged_manifest, canonical_manifest_path)) {
    rollback_targets()
    unlink(staged_manifest)
    wsl_stop("publication_failed", "Could not atomically promote manifest.")
  }

  previous_targets <- list.files(canonical_root, pattern = "^winter_storm_levels_.*\\.geojson$",
                                 recursive = TRUE, full.names = TRUE)
  retained_cycles <- unique(vapply(candidate$targets, function(entry) {
    wsl_cycle_token(entry$cycle_time_utc)
  }, character(1)))
  target_cycles <- sub(
    ".*_([0-9]{10})_f[0-9]{3}(?:_[0-9a-f]{12})?\\.geojson$", "\\1",
    basename(previous_targets), perl = TRUE
  )
  recognized <- grepl("^[0-9]{10}$", target_cycles)
  removed <- previous_targets[recognized & !target_cycles %in% retained_cycles]
  removed <- normalizePath(removed, winslash = "/", mustWork = FALSE)
  if (length(removed)) unlink(removed)
  list(changed = TRUE, promoted = promoted, removed = removed)
}
