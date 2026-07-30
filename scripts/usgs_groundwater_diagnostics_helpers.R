# Product-specific retrieval, diagnostic, parser-accounting, and output-safety
# helpers for scripts/build_usgs_groundwater_latest_ca.R.

gw_required_measurement_columns <- c(
  "monitoring_location_id",
  "parameter_code",
  "time",
  "value"
)

gw_requested_measurement_columns <- c(
  gw_required_measurement_columns,
  "unit_of_measure",
  "qualifier",
  "approval_status",
  "observing_procedure",
  "vertical_datum",
  "measuring_agency",
  "field_visit_id",
  "last_modified"
)

gw_terminal_outcomes <- c(
  "success_data",
  "success_empty",
  "transient_failure",
  "permanent_http_failure",
  "malformed_payload",
  "schema_mismatch",
  "parse_failure"
)

gw_chr <- function(x) {
  x <- as.character(x)
  x <- trimws(x)
  x[x == "" | is.na(x) | toupper(x) %in% c("NA", "NULL", "NAN")] <- NA_character_
  x
}

gw_site_no <- function(x) {
  x <- gw_chr(x)
  x <- gsub("\\.0$", "", x)
  x <- gsub("[^0-9]", "", x)
  x[nchar(x) == 0] <- NA_character_
  x
}

gw_num <- function(x) {
  suppressWarnings(as.numeric(as.character(x)))
}

gw_chunks <- function(x, n) {
  x <- unique(as.character(x))
  x <- x[!is.na(x) & x != ""]
  split(x, ceiling(seq_along(x) / n))
}

gw_monitoring_location_ids <- function(site_ids) {
  site_ids <- gw_site_no(site_ids)
  out <- ifelse(
    !is.na(site_ids) & site_ids != "",
    paste0("USGS-", site_ids),
    NA_character_
  )
  out[!is.na(out)]
}

gw_time_interval <- function(start_date, end_date) {
  paste0(as.character(start_date), "/", as.character(end_date))
}

gw_endpoint_class <- function(url) {
  sub("\\?.*$", "", as.character(url))
}

gw_safe_text <- function(x, max_chars = 220L) {
  if (is.null(x) || length(x) == 0 || is.na(x[[1]])) {
    return(NA_character_)
  }

  out <- as.character(x[[1]])
  out <- gsub("[[:cntrl:]]+", " ", out)
  out <- gsub("[[:space:]]+", " ", out)
  out <- gsub(
    "(https?://[^?[:space:]]+)\\?[^[:space:]]+",
    "\\1?[query omitted]",
    out,
    perl = TRUE
  )
  out <- gsub(
    "(?i)(authorization|api[-_ ]?key|token)[=: ]+[^,; }]+",
    "\\1=[redacted]",
    out,
    perl = TRUE
  )
  out <- trimws(out)

  if (nchar(out) > max_chars) {
    out <- paste0(substr(out, 1L, max_chars), "...")
  }

  if (identical(out, "")) NA_character_ else out
}

gw_diagnostic <- function(class, message) {
  list(
    class = as.character(class),
    message = gw_safe_text(message)
  )
}

gw_request_result <- function(chunk_id,
                              chunk_ordinal,
                              total_chunks,
                              request_label,
                              endpoint_class,
                              attempt_count,
                              elapsed_seconds,
                              transport_outcome,
                              http_status = NA_integer_,
                              content_type = NA_character_,
                              response_bytes = NA_integer_,
                              retryable = FALSE,
                              terminal_outcome,
                              source_path,
                              records = tibble::tibble(),
                              raw_record_count = 0L,
                              missing_required_keys = character(),
                              safe_sample = NA_character_,
                              diagnostics = list(),
                              attempts = list(),
                              primary_result = NULL) {
  if (!terminal_outcome %in% gw_terminal_outcomes) {
    stop("Unsupported groundwater request terminal outcome: ", terminal_outcome)
  }

  list(
    chunk_id = as.character(chunk_id),
    chunk_ordinal = as.integer(chunk_ordinal),
    total_chunks = as.integer(total_chunks),
    request_label = as.character(request_label),
    endpoint_class = as.character(endpoint_class),
    attempt_count = as.integer(attempt_count),
    elapsed_seconds = as.numeric(elapsed_seconds),
    transport_outcome = as.character(transport_outcome),
    http_status = if (length(http_status) == 0 || is.na(http_status)) {
      NA_integer_
    } else {
      as.integer(http_status)
    },
    content_type = gw_chr(content_type)[[1]],
    response_bytes = if (length(response_bytes) == 0 || is.na(response_bytes)) {
      NA_integer_
    } else {
      as.integer(response_bytes)
    },
    retryable = isTRUE(retryable),
    terminal_outcome = as.character(terminal_outcome),
    source_path = as.character(source_path),
    records = tibble::as_tibble(records),
    raw_record_count = as.integer(raw_record_count),
    missing_required_keys = as.character(missing_required_keys),
    safe_sample = gw_safe_text(safe_sample),
    diagnostics = diagnostics,
    attempts = attempts,
    primary_result = primary_result
  )
}

gw_header_value <- function(headers, name) {
  if (is.null(headers) || length(headers) == 0) {
    return(NA_character_)
  }

  parsed <- headers
  if (is.raw(headers)) {
    parsed <- tryCatch(
      curl::parse_headers_list(headers),
      error = function(e) list()
    )
  }

  if (is.list(parsed) && !is.null(names(parsed))) {
    idx <- which(tolower(names(parsed)) == tolower(name))
    if (length(idx) > 0) {
      return(gw_chr(parsed[[idx[[length(idx)]]]])[[1]])
    }
  }

  if (is.character(parsed) && !is.null(names(parsed))) {
    idx <- which(tolower(names(parsed)) == tolower(name))
    if (length(idx) > 0) {
      return(gw_chr(parsed[[idx[[length(idx)]]]])[[1]])
    }
  }

  NA_character_
}

gw_retryable_status <- function(status) {
  !is.na(status) && status %in% c(408L, 429L, 500L, 502L, 503L, 504L)
}

gw_retry_delay <- function(retry_after,
                           attempt,
                           base_backoff_sec,
                           max_backoff_sec,
                           max_retry_after_sec,
                           jitter_sec,
                           now_fun,
                           jitter_fun) {
  retry_after_delay <- NA_real_

  if (!is.na(retry_after) && nzchar(retry_after)) {
    numeric_retry <- suppressWarnings(as.numeric(retry_after))
    if (is.finite(numeric_retry) && numeric_retry >= 0) {
      retry_after_delay <- numeric_retry
    } else {
      retry_time <- suppressWarnings(as.POSIXct(
        retry_after,
        format = "%a, %d %b %Y %H:%M:%S GMT",
        tz = "UTC"
      ))
      now_value <- now_fun()
      if (!is.na(retry_time) && inherits(now_value, "POSIXt")) {
        retry_after_delay <- max(
          0,
          as.numeric(difftime(retry_time, now_value, units = "secs"))
        )
      }
    }
  }

  if (is.finite(retry_after_delay)) {
    return(min(retry_after_delay, max_retry_after_sec))
  }

  stepped <- min(
    max_backoff_sec,
    base_backoff_sec * (2 ^ max(0L, as.integer(attempt) - 1L))
  )
  jitter <- if (jitter_sec > 0) {
    as.numeric(jitter_fun(1L, min = 0, max = jitter_sec))
  } else {
    0
  }
  stepped + jitter
}

gw_extract_properties <- function(parsed) {
  if (!is.list(parsed) || is.null(names(parsed)) || !"features" %in% names(parsed)) {
    return(list(
      outcome = "schema_mismatch",
      records = tibble::tibble(),
      raw_record_count = 0L,
      missing_required_keys = "features",
      diagnostic = gw_diagnostic(
        "schema_mismatch",
        "JSON root is missing the required features member."
      )
    ))
  }

  features <- parsed$features
  if (is.null(features)) {
    return(list(
      outcome = "schema_mismatch",
      records = tibble::tibble(),
      raw_record_count = 0L,
      missing_required_keys = "features[]",
      diagnostic = gw_diagnostic(
        "schema_mismatch",
        "The features member is null instead of an array."
      )
    ))
  }
  if (length(features) == 0) {
    return(list(
      outcome = "success_empty",
      records = tibble::tibble(),
      raw_record_count = 0L,
      missing_required_keys = character(),
      diagnostic = NULL
    ))
  }

  if (!is.list(features)) {
    return(list(
      outcome = "schema_mismatch",
      records = tibble::tibble(),
      raw_record_count = length(features),
      missing_required_keys = "features[]",
      diagnostic = gw_diagnostic(
        "schema_mismatch",
        "The features member is not an array of feature objects."
      )
    ))
  }

  properties <- lapply(features, function(feature) {
    if (!is.list(feature) || is.null(feature$properties) ||
        !is.list(feature$properties)) {
      return(NULL)
    }
    feature$properties
  })

  missing_properties <- which(vapply(properties, is.null, logical(1)))
  if (length(missing_properties) > 0) {
    return(list(
      outcome = "schema_mismatch",
      records = tibble::tibble(),
      raw_record_count = length(features),
      missing_required_keys = "features[].properties",
      diagnostic = gw_diagnostic(
        "schema_mismatch",
        paste0(
          length(missing_properties),
          " feature(s) are missing a properties object."
        )
      )
    ))
  }

  missing_by_feature <- lapply(
    properties,
    function(x) setdiff(gw_required_measurement_columns, names(x))
  )
  missing_keys <- sort(unique(unlist(missing_by_feature, use.names = FALSE)))
  missing_feature_count <- sum(lengths(missing_by_feature) > 0)

  if (length(missing_keys) > 0) {
    return(list(
      outcome = "schema_mismatch",
      records = tibble::tibble(),
      raw_record_count = length(features),
      missing_required_keys = missing_keys,
      diagnostic = gw_diagnostic(
        "schema_mismatch",
        paste0(
          missing_feature_count,
          " feature(s) are missing required key(s): ",
          paste(missing_keys, collapse = ", "),
          "."
        )
      )
    ))
  }

  properties <- lapply(properties, function(property) {
    null_keys <- names(property)[vapply(property, is.null, logical(1))]
    for (key in null_keys) property[[key]] <- NA
    property
  })

  records <- tryCatch(
    dplyr::bind_rows(properties),
    error = function(e) e
  )

  if (inherits(records, "error")) {
    return(list(
      outcome = "parse_failure",
      records = tibble::tibble(),
      raw_record_count = length(features),
      missing_required_keys = character(),
      diagnostic = gw_diagnostic("parse_failure", conditionMessage(records))
    ))
  }

  list(
    outcome = if (nrow(records) == 0) "success_empty" else "success_data",
    records = tibble::as_tibble(records),
    raw_record_count = length(features),
    missing_required_keys = character(),
    diagnostic = NULL
  )
}

gw_result_from_records <- function(records,
                                   chunk_id,
                                   chunk_ordinal,
                                   total_chunks,
                                   request_label,
                                   endpoint_class,
                                   attempt_count,
                                   elapsed_seconds,
                                   source_path,
                                   diagnostics = list()) {
  records <- tryCatch(tibble::as_tibble(records), error = function(e) e)
  if (inherits(records, "error")) {
    return(gw_request_result(
      chunk_id = chunk_id,
      chunk_ordinal = chunk_ordinal,
      total_chunks = total_chunks,
      request_label = request_label,
      endpoint_class = endpoint_class,
      attempt_count = attempt_count,
      elapsed_seconds = elapsed_seconds,
      transport_outcome = "response_received",
      retryable = FALSE,
      terminal_outcome = "parse_failure",
      source_path = source_path,
      diagnostics = c(
        diagnostics,
        list(gw_diagnostic("parse_failure", conditionMessage(records)))
      )
    ))
  }

  if (nrow(records) == 0) {
    return(gw_request_result(
      chunk_id = chunk_id,
      chunk_ordinal = chunk_ordinal,
      total_chunks = total_chunks,
      request_label = request_label,
      endpoint_class = endpoint_class,
      attempt_count = attempt_count,
      elapsed_seconds = elapsed_seconds,
      transport_outcome = "response_received",
      retryable = FALSE,
      terminal_outcome = "success_empty",
      source_path = source_path,
      records = records,
      raw_record_count = 0L,
      diagnostics = diagnostics
    ))
  }

  missing <- setdiff(gw_required_measurement_columns, names(records))
  if (length(missing) > 0) {
    return(gw_request_result(
      chunk_id = chunk_id,
      chunk_ordinal = chunk_ordinal,
      total_chunks = total_chunks,
      request_label = request_label,
      endpoint_class = endpoint_class,
      attempt_count = attempt_count,
      elapsed_seconds = elapsed_seconds,
      transport_outcome = "response_received",
      retryable = FALSE,
      terminal_outcome = "schema_mismatch",
      source_path = source_path,
      raw_record_count = nrow(records),
      missing_required_keys = missing,
      diagnostics = c(
        diagnostics,
        list(gw_diagnostic(
          "schema_mismatch",
          paste0(
            "Response table is missing required key(s): ",
            paste(missing, collapse = ", "),
            "."
          )
        ))
      )
    ))
  }

  gw_request_result(
    chunk_id = chunk_id,
    chunk_ordinal = chunk_ordinal,
    total_chunks = total_chunks,
    request_label = request_label,
    endpoint_class = endpoint_class,
    attempt_count = attempt_count,
    elapsed_seconds = elapsed_seconds,
    transport_outcome = "response_received",
    retryable = FALSE,
    terminal_outcome = "success_data",
    source_path = source_path,
    records = records,
    raw_record_count = nrow(records),
    diagnostics = diagnostics
  )
}

gw_classify_error <- function(message) {
  message <- gw_safe_text(message)
  status_match <- regexec("HTTP[ /:]?([0-9]{3})", message, perl = TRUE)
  status_parts <- regmatches(message, status_match)[[1]]
  status <- if (length(status_parts) >= 2) {
    suppressWarnings(as.integer(status_parts[[2]]))
  } else {
    NA_integer_
  }

  if (!is.na(status)) {
    if (gw_retryable_status(status)) {
      return(list(
        terminal_outcome = "transient_failure",
        retryable = TRUE,
        http_status = status,
        class = if (status == 429L) "rate_limit" else "transient_http"
      ))
    }

    if (status >= 400L) {
      return(list(
        terminal_outcome = "permanent_http_failure",
        retryable = FALSE,
        http_status = status,
        class = "permanent_http"
      ))
    }
  }

  if (grepl(
    paste(
      c(
        "timed? out",
        "timeout",
        "could not resolve",
        "temporary failure",
        "connection (reset|refused|aborted)",
        "failure when receiving data",
        "empty reply",
        "network is unreachable"
      ),
      collapse = "|"
    ),
    message,
    ignore.case = TRUE,
    perl = TRUE
  )) {
    return(list(
      terminal_outcome = "transient_failure",
      retryable = TRUE,
      http_status = NA_integer_,
      class = "transport"
    ))
  }

  list(
    terminal_outcome = "parse_failure",
    retryable = FALSE,
    http_status = status,
    class = "primary_client_error"
  )
}

gw_primary_request <- function(site_ids,
                               start_date,
                               end_date,
                               parameter_code,
                               chunk_id,
                               chunk_ordinal,
                               total_chunks,
                               primary_fetch_fun) {
  started <- proc.time()[["elapsed"]]
  captured_diagnostics <- list()

  value <- tryCatch(
    withCallingHandlers(
      primary_fetch_fun(
        monitoring_location_id = gw_monitoring_location_ids(site_ids),
        parameter_code = parameter_code,
        time = gw_time_interval(start_date, end_date),
        properties = gw_requested_measurement_columns,
        skipGeometry = TRUE
      ),
      message = function(m) {
        invokeRestart("muffleMessage")
      },
      warning = function(w) {
        captured_diagnostics[[length(captured_diagnostics) + 1L]] <<-
          gw_diagnostic("primary_warning", conditionMessage(w))
        invokeRestart("muffleWarning")
      }
    ),
    error = function(e) e
  )
  elapsed <- proc.time()[["elapsed"]] - started
  label <- paste0("groundwater field-measurements chunk ", chunk_id)
  endpoint <- "api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/items"

  if (!inherits(value, "error")) {
    return(gw_result_from_records(
      records = value,
      chunk_id = chunk_id,
      chunk_ordinal = chunk_ordinal,
      total_chunks = total_chunks,
      request_label = label,
      endpoint_class = endpoint,
      attempt_count = 1L,
      elapsed_seconds = elapsed,
      source_path = "dataRetrieval",
      diagnostics = captured_diagnostics
    ))
  }

  classified <- gw_classify_error(conditionMessage(value))
  gw_request_result(
    chunk_id = chunk_id,
    chunk_ordinal = chunk_ordinal,
    total_chunks = total_chunks,
    request_label = label,
    endpoint_class = endpoint,
    attempt_count = 1L,
    elapsed_seconds = elapsed,
    transport_outcome = if (classified$class == "transport") {
      "transport_error"
    } else {
      "client_error"
    },
    http_status = classified$http_status,
    retryable = classified$retryable,
    terminal_outcome = classified$terminal_outcome,
    source_path = "dataRetrieval",
    diagnostics = c(
      captured_diagnostics,
      list(gw_diagnostic(classified$class, conditionMessage(value)))
    )
  )
}

gw_ogc_query_url <- function(site_ids, start_date, end_date, parameter_code) {
  base <- paste0(
    "https://api.waterdata.usgs.gov/ogcapi/v0/collections/",
    "field-measurements/items"
  )
  params <- list(
    f = "json",
    lang = "en-US",
    skipGeometry = "TRUE",
    properties = paste(gw_requested_measurement_columns, collapse = ","),
    monitoring_location_id = paste(
      gw_monitoring_location_ids(site_ids),
      collapse = ","
    ),
    parameter_code = parameter_code,
    time = gw_time_interval(start_date, end_date),
    limit = "50000"
  )
  query <- paste(
    paste0(
      names(params),
      "=",
      vapply(params, utils::URLencode, character(1), reserved = TRUE)
    ),
    collapse = "&"
  )
  paste0(base, "?", query)
}

gw_default_http_fetch <- function(url, timeout_sec, connect_timeout_sec) {
  handle <- curl::new_handle(
    timeout = timeout_sec,
    connecttimeout = connect_timeout_sec,
    useragent = "BRIM live groundwater feed"
  )

  api_key <- Sys.getenv("API_USGS_PAT")
  if (nzchar(api_key)) {
    curl::handle_setheaders(handle, "X-Api-Key" = api_key)
  }

  curl::curl_fetch_memory(url, handle = handle)
}

gw_http_request <- function(url,
                            chunk_id,
                            chunk_ordinal,
                            total_chunks,
                            request_label,
                            source_path = "direct_ogc_fallback",
                            max_attempts = 3L,
                            timeout_sec = 45,
                            connect_timeout_sec = 15,
                            base_backoff_sec = 1,
                            max_backoff_sec = 8,
                            max_retry_after_sec = 30,
                            jitter_sec = 0.25,
                            http_fetch_fun = gw_default_http_fetch,
                            sleep_fun = Sys.sleep,
                            now_fun = Sys.time,
                            jitter_fun = stats::runif) {
  max_attempts <- min(3L, max(1L, as.integer(max_attempts)))
  base_backoff_sec <- min(8, max(0, as.numeric(base_backoff_sec)))
  max_backoff_sec <- min(
    8,
    max(base_backoff_sec, as.numeric(max_backoff_sec))
  )
  max_retry_after_sec <- min(
    30,
    max(0, as.numeric(max_retry_after_sec))
  )
  jitter_sec <- min(0.25, max(0, as.numeric(jitter_sec)))
  endpoint <- gw_endpoint_class(url)
  started <- proc.time()[["elapsed"]]
  attempts <- list()
  diagnostics <- list()

  for (attempt in seq_len(max_attempts)) {
    attempt_started <- proc.time()[["elapsed"]]
    response <- tryCatch(
      http_fetch_fun(url, timeout_sec, connect_timeout_sec),
      error = function(e) e
    )
    attempt_elapsed <- proc.time()[["elapsed"]] - attempt_started

    if (inherits(response, "error")) {
      message <- conditionMessage(response)
      retryable <- TRUE
      attempts[[attempt]] <- list(
        attempt = attempt,
        elapsed_seconds = attempt_elapsed,
        transport_outcome = "transport_error",
        http_status = NA_integer_,
        content_type = NA_character_,
        response_bytes = NA_integer_,
        retryable = retryable,
        retry_delay_seconds = NA_real_
      )
      diagnostics[[length(diagnostics) + 1L]] <- gw_diagnostic(
        "transport",
        message
      )

      if (attempt < max_attempts) {
        delay <- gw_retry_delay(
          retry_after = NA_character_,
          attempt = attempt,
          base_backoff_sec = base_backoff_sec,
          max_backoff_sec = max_backoff_sec,
          max_retry_after_sec = max_retry_after_sec,
          jitter_sec = jitter_sec,
          now_fun = now_fun,
          jitter_fun = jitter_fun
        )
        attempts[[attempt]]$retry_delay_seconds <- delay
        sleep_fun(delay)
        next
      }

      return(gw_request_result(
        chunk_id = chunk_id,
        chunk_ordinal = chunk_ordinal,
        total_chunks = total_chunks,
        request_label = request_label,
        endpoint_class = endpoint,
        attempt_count = attempt,
        elapsed_seconds = proc.time()[["elapsed"]] - started,
        transport_outcome = "transport_error",
        retryable = TRUE,
        terminal_outcome = "transient_failure",
        source_path = source_path,
        diagnostics = diagnostics,
        attempts = attempts
      ))
    }

    status <- if (is.null(response$status_code)) {
      NA_integer_
    } else {
      as.integer(response$status_code)
    }
    content <- if (is.null(response$content)) raw() else response$content
    if (!is.raw(content)) content <- charToRaw(as.character(content))
    response_bytes <- length(content)
    content_type <- gw_header_value(response$headers, "content-type")
    retry_after <- gw_header_value(response$headers, "retry-after")
    retryable <- gw_retryable_status(status)

    attempts[[attempt]] <- list(
      attempt = attempt,
      elapsed_seconds = attempt_elapsed,
      transport_outcome = "response_received",
      http_status = status,
      content_type = content_type,
      response_bytes = response_bytes,
      retryable = retryable,
      retry_delay_seconds = NA_real_
    )

    if (is.na(status)) {
      diagnostics[[length(diagnostics) + 1L]] <- gw_diagnostic(
        "malformed_response",
        "HTTP response did not include a status code."
      )
      return(gw_request_result(
        chunk_id = chunk_id,
        chunk_ordinal = chunk_ordinal,
        total_chunks = total_chunks,
        request_label = request_label,
        endpoint_class = endpoint,
        attempt_count = attempt,
        elapsed_seconds = proc.time()[["elapsed"]] - started,
        transport_outcome = "response_received",
        content_type = content_type,
        response_bytes = response_bytes,
        terminal_outcome = "malformed_payload",
        source_path = source_path,
        diagnostics = diagnostics,
        attempts = attempts
      ))
    }

    if (status >= 400L) {
      error_body <- tryCatch(
        rawToChar(content),
        error = function(e) NA_character_
      )
      sample <- gw_safe_text(error_body, max_chars = 180L)
      class <- if (status == 429L) {
        "rate_limit"
      } else if (retryable) {
        "transient_http"
      } else {
        "permanent_http"
      }
      diagnostics[[length(diagnostics) + 1L]] <- gw_diagnostic(
        class,
        paste0("HTTP ", status, if (!is.na(sample)) paste0(": ", sample) else "")
      )

      if (retryable && attempt < max_attempts) {
        delay <- gw_retry_delay(
          retry_after = retry_after,
          attempt = attempt,
          base_backoff_sec = base_backoff_sec,
          max_backoff_sec = max_backoff_sec,
          max_retry_after_sec = max_retry_after_sec,
          jitter_sec = jitter_sec,
          now_fun = now_fun,
          jitter_fun = jitter_fun
        )
        attempts[[attempt]]$retry_delay_seconds <- delay
        sleep_fun(delay)
        next
      }

      return(gw_request_result(
        chunk_id = chunk_id,
        chunk_ordinal = chunk_ordinal,
        total_chunks = total_chunks,
        request_label = request_label,
        endpoint_class = endpoint,
        attempt_count = attempt,
        elapsed_seconds = proc.time()[["elapsed"]] - started,
        transport_outcome = "response_received",
        http_status = status,
        content_type = content_type,
        response_bytes = response_bytes,
        retryable = retryable,
        terminal_outcome = if (retryable) {
          "transient_failure"
        } else {
          "permanent_http_failure"
        },
        source_path = source_path,
        safe_sample = sample,
        diagnostics = diagnostics,
        attempts = attempts
      ))
    }

    if (status == 204L) {
      return(gw_request_result(
        chunk_id = chunk_id,
        chunk_ordinal = chunk_ordinal,
        total_chunks = total_chunks,
        request_label = request_label,
        endpoint_class = endpoint,
        attempt_count = attempt,
        elapsed_seconds = proc.time()[["elapsed"]] - started,
        transport_outcome = "response_received",
        http_status = status,
        content_type = content_type,
        response_bytes = response_bytes,
        terminal_outcome = "success_empty",
        source_path = source_path,
        attempts = attempts
      ))
    }

    if (response_bytes == 0L) {
      diagnostics[[length(diagnostics) + 1L]] <- gw_diagnostic(
        "malformed_payload",
        "HTTP success response had an empty body instead of a GeoJSON object."
      )
      return(gw_request_result(
        chunk_id = chunk_id,
        chunk_ordinal = chunk_ordinal,
        total_chunks = total_chunks,
        request_label = request_label,
        endpoint_class = endpoint,
        attempt_count = attempt,
        elapsed_seconds = proc.time()[["elapsed"]] - started,
        transport_outcome = "response_received",
        http_status = status,
        content_type = content_type,
        response_bytes = 0L,
        terminal_outcome = "malformed_payload",
        source_path = source_path,
        diagnostics = diagnostics,
        attempts = attempts
      ))
    }

    body <- tryCatch(rawToChar(content), error = function(e) e)
    if (inherits(body, "error")) {
      diagnostics[[length(diagnostics) + 1L]] <- gw_diagnostic(
        "parse_failure",
        conditionMessage(body)
      )
      return(gw_request_result(
        chunk_id = chunk_id,
        chunk_ordinal = chunk_ordinal,
        total_chunks = total_chunks,
        request_label = request_label,
        endpoint_class = endpoint,
        attempt_count = attempt,
        elapsed_seconds = proc.time()[["elapsed"]] - started,
        transport_outcome = "response_received",
        http_status = status,
        content_type = content_type,
        response_bytes = response_bytes,
        terminal_outcome = "parse_failure",
        source_path = source_path,
        diagnostics = diagnostics,
        attempts = attempts
      ))
    }

    is_json_type <- !is.na(content_type) &&
      grepl("(^|[+/])json([; ]|$)", content_type, ignore.case = TRUE)
    if (!is_json_type) {
      sample <- gw_safe_text(body, max_chars = 180L)
      diagnostics[[length(diagnostics) + 1L]] <- gw_diagnostic(
        "malformed_payload",
        paste0(
          "HTTP success response used non-JSON content type ",
          ifelse(is.na(content_type), "<missing>", content_type),
          if (!is.na(sample)) paste0(": ", sample) else ""
        )
      )
      return(gw_request_result(
        chunk_id = chunk_id,
        chunk_ordinal = chunk_ordinal,
        total_chunks = total_chunks,
        request_label = request_label,
        endpoint_class = endpoint,
        attempt_count = attempt,
        elapsed_seconds = proc.time()[["elapsed"]] - started,
        transport_outcome = "response_received",
        http_status = status,
        content_type = content_type,
        response_bytes = response_bytes,
        terminal_outcome = "malformed_payload",
        source_path = source_path,
        safe_sample = sample,
        diagnostics = diagnostics,
        attempts = attempts
      ))
    }

    parsed <- tryCatch(
      jsonlite::fromJSON(body, simplifyVector = FALSE),
      error = function(e) e
    )
    if (inherits(parsed, "error")) {
      diagnostics[[length(diagnostics) + 1L]] <- gw_diagnostic(
        "malformed_payload",
        conditionMessage(parsed)
      )
      return(gw_request_result(
        chunk_id = chunk_id,
        chunk_ordinal = chunk_ordinal,
        total_chunks = total_chunks,
        request_label = request_label,
        endpoint_class = endpoint,
        attempt_count = attempt,
        elapsed_seconds = proc.time()[["elapsed"]] - started,
        transport_outcome = "response_received",
        http_status = status,
        content_type = content_type,
        response_bytes = response_bytes,
        terminal_outcome = "malformed_payload",
        source_path = source_path,
        safe_sample = gw_safe_text(body, max_chars = 180L),
        diagnostics = diagnostics,
        attempts = attempts
      ))
    }

    extracted <- gw_extract_properties(parsed)
    if (!is.null(extracted$diagnostic)) {
      diagnostics[[length(diagnostics) + 1L]] <- extracted$diagnostic
    }
    return(gw_request_result(
      chunk_id = chunk_id,
      chunk_ordinal = chunk_ordinal,
      total_chunks = total_chunks,
      request_label = request_label,
      endpoint_class = endpoint,
      attempt_count = attempt,
      elapsed_seconds = proc.time()[["elapsed"]] - started,
      transport_outcome = "response_received",
      http_status = status,
      content_type = content_type,
      response_bytes = response_bytes,
      retryable = FALSE,
      terminal_outcome = extracted$outcome,
      source_path = source_path,
      records = extracted$records,
      raw_record_count = extracted$raw_record_count,
      missing_required_keys = extracted$missing_required_keys,
      diagnostics = diagnostics,
      attempts = attempts
    ))
  }

  stop("Groundwater HTTP request loop exited without a terminal result.")
}

gw_fetch_chunk <- function(site_ids,
                           start_date,
                           end_date,
                           parameter_code,
                           chunk_ordinal,
                           total_chunks,
                           primary_fetch_fun,
                           http_fetch_fun = gw_default_http_fetch,
                           sleep_fun = Sys.sleep,
                           now_fun = Sys.time,
                           jitter_fun = stats::runif,
                           max_attempts = 3L,
                           timeout_sec = 45,
                           connect_timeout_sec = 15,
                           base_backoff_sec = 1,
                           max_backoff_sec = 8,
                           max_retry_after_sec = 30,
                           jitter_sec = 0.25) {
  chunk_id <- paste0(chunk_ordinal, "/", total_chunks)
  primary <- gw_primary_request(
    site_ids = site_ids,
    start_date = start_date,
    end_date = end_date,
    parameter_code = parameter_code,
    chunk_id = chunk_id,
    chunk_ordinal = chunk_ordinal,
    total_chunks = total_chunks,
    primary_fetch_fun = primary_fetch_fun
  )

  if (primary$terminal_outcome %in% c("success_data", "success_empty")) {
    return(primary)
  }

  direct <- gw_http_request(
    url = gw_ogc_query_url(site_ids, start_date, end_date, parameter_code),
    chunk_id = chunk_id,
    chunk_ordinal = chunk_ordinal,
    total_chunks = total_chunks,
    request_label = paste0("groundwater field-measurements chunk ", chunk_id),
    source_path = "direct_ogc_fallback",
    max_attempts = max_attempts,
    timeout_sec = timeout_sec,
    connect_timeout_sec = connect_timeout_sec,
    base_backoff_sec = base_backoff_sec,
    max_backoff_sec = max_backoff_sec,
    max_retry_after_sec = max_retry_after_sec,
    jitter_sec = jitter_sec,
    http_fetch_fun = http_fetch_fun,
    sleep_fun = sleep_fun,
    now_fun = now_fun,
    jitter_fun = jitter_fun
  )
  direct$attempt_count <- primary$attempt_count + direct$attempt_count
  direct$elapsed_seconds <- primary$elapsed_seconds + direct$elapsed_seconds
  direct$diagnostics <- c(primary$diagnostics, direct$diagnostics)
  direct$primary_result <- primary
  direct
}

gw_circuit_breaker_result <- function(chunk_ordinal,
                                      total_chunks,
                                      failure_class) {
  chunk_id <- paste0(chunk_ordinal, "/", total_chunks)
  gw_request_result(
    chunk_id = chunk_id,
    chunk_ordinal = chunk_ordinal,
    total_chunks = total_chunks,
    request_label = paste0("groundwater field-measurements chunk ", chunk_id),
    endpoint_class = "api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/items",
    attempt_count = 0L,
    elapsed_seconds = 0,
    transport_outcome = "not_attempted_circuit_breaker",
    retryable = FALSE,
    terminal_outcome = if (failure_class %in% c(
      "malformed_payload",
      "schema_mismatch",
      "parse_failure",
      "permanent_http_failure"
    )) {
      failure_class
    } else {
      "transient_failure"
    },
    source_path = "circuit_breaker",
    diagnostics = list(gw_diagnostic(
      "circuit_breaker",
      paste0(
        "Chunk was not attempted after repeated terminal chunk failures; ",
        "preceding failure class was ",
        failure_class,
        "."
      )
    ))
  )
}

gw_retrieve_chunks <- function(site_ids,
                               start_date,
                               end_date,
                               parameter_code,
                               chunk_size = 60L,
                               request_pause_sec = 0.20,
                               max_consecutive_failures = 3L,
                               primary_fetch_fun,
                               http_fetch_fun = gw_default_http_fetch,
                               sleep_fun = Sys.sleep,
                               now_fun = Sys.time,
                               jitter_fun = stats::runif,
                               max_attempts = 3L,
                               timeout_sec = 45,
                               connect_timeout_sec = 15,
                               base_backoff_sec = 1,
                               max_backoff_sec = 8,
                               max_retry_after_sec = 30,
                               jitter_sec = 0.25) {
  chunks <- gw_chunks(site_ids, chunk_size)
  total_chunks <- length(chunks)
  max_consecutive_failures <- min(
    3L,
    max(1L, as.integer(max_consecutive_failures))
  )
  results <- vector("list", total_chunks)
  consecutive_failures <- 0L
  circuit_failure_class <- NA_character_

  for (i in seq_along(chunks)) {
    if (consecutive_failures >= max_consecutive_failures) {
      results[[i]] <- gw_circuit_breaker_result(
        chunk_ordinal = i,
        total_chunks = total_chunks,
        failure_class = circuit_failure_class
      )
      next
    }

    results[[i]] <- gw_fetch_chunk(
      site_ids = chunks[[i]],
      start_date = start_date,
      end_date = end_date,
      parameter_code = parameter_code,
      chunk_ordinal = i,
      total_chunks = total_chunks,
      primary_fetch_fun = primary_fetch_fun,
      http_fetch_fun = http_fetch_fun,
      sleep_fun = sleep_fun,
      now_fun = now_fun,
      jitter_fun = jitter_fun,
      max_attempts = max_attempts,
      timeout_sec = timeout_sec,
      connect_timeout_sec = connect_timeout_sec,
      base_backoff_sec = base_backoff_sec,
      max_backoff_sec = max_backoff_sec,
      max_retry_after_sec = max_retry_after_sec,
      jitter_sec = jitter_sec
    )

    if (results[[i]]$terminal_outcome %in% c("success_data", "success_empty")) {
      consecutive_failures <- 0L
      circuit_failure_class <- NA_character_
    } else {
      consecutive_failures <- consecutive_failures + 1L
      circuit_failure_class <- results[[i]]$terminal_outcome
    }

    if (i < total_chunks && request_pause_sec > 0) {
      sleep_fun(request_pause_sec)
    }
  }

  raw <- dplyr::bind_rows(lapply(results, function(x) {
    if (x$terminal_outcome %in% c("success_data", "success_empty")) {
      x$records
    } else {
      tibble::tibble()
    }
  }))

  list(
    chunks = chunks,
    results = results,
    raw = raw
  )
}

gw_normalize_measurements <- function(raw) {
  if (is.null(raw) || nrow(raw) == 0) {
    return(tibble::tibble())
  }

  raw <- tibble::as_tibble(raw)
  char_cols <- intersect(
    setdiff(gw_requested_measurement_columns, "value"),
    names(raw)
  )
  if (length(char_cols) > 0) {
    raw <- raw |>
      dplyr::mutate(dplyr::across(dplyr::all_of(char_cols), as.character))
  }
  if ("value" %in% names(raw)) {
    raw <- raw |>
      dplyr::mutate(value = gw_num(.data$value))
  }
  raw
}

gw_empty_latest <- function() {
  tibble::tibble(
    site_no = character(),
    api_latest_wl_ft_bgs = numeric(),
    api_latest_wl_datetime_utc = character(),
    api_latest_wl_date = as.Date(character()),
    api_latest_wl_status = character(),
    api_latest_wl_procedure = character(),
    api_latest_wl_qualifier = character(),
    api_latest_wl_units = character(),
    api_latest_wl_vertical_datum = character(),
    api_latest_wl_measuring_agency = character(),
    api_latest_wl_field_visit_id = character(),
    api_latest_wl_last_modified_utc = character()
  )
}

gw_parse_latest <- function(raw,
                            parameter_code,
                            station_index_site_ids = character()) {
  raw <- gw_normalize_measurements(raw)
  raw_count <- nrow(raw)
  accounting <- list(
    raw_records_bound = raw_count,
    records_with_required_columns = 0L,
    records_after_site_id = 0L,
    records_after_parameter = 0L,
    records_after_datetime = 0L,
    records_after_value = 0L,
    records_parsed = 0L,
    duplicate_collapse_removed = 0L,
    unique_site_count_before_join = 0L,
    site_join_matches = 0L,
    site_join_mismatch_removed = 0L,
    unique_site_count = 0L,
    missing_required_columns = character()
  )

  if (raw_count == 0L) {
    return(list(raw = raw, latest = gw_empty_latest(), accounting = accounting))
  }

  missing <- setdiff(gw_required_measurement_columns, names(raw))
  accounting$missing_required_columns <- missing
  if (length(missing) > 0) {
    return(list(raw = raw, latest = gw_empty_latest(), accounting = accounting))
  }
  accounting$records_with_required_columns <- raw_count

  raw2 <- raw |>
    dplyr::mutate(
      site_no = gw_site_no(.data$monitoring_location_id),
      parameter_code = as.character(.data$parameter_code),
      obs_datetime = suppressWarnings(lubridate::as_datetime(.data$time, tz = "UTC")),
      obs_value = gw_num(.data$value),
      unit_of_measure = if ("unit_of_measure" %in% names(raw)) gw_chr(.data$unit_of_measure) else NA_character_,
      observing_procedure = if ("observing_procedure" %in% names(raw)) gw_chr(.data$observing_procedure) else NA_character_,
      vertical_datum = if ("vertical_datum" %in% names(raw)) gw_chr(.data$vertical_datum) else NA_character_,
      measuring_agency = if ("measuring_agency" %in% names(raw)) gw_chr(.data$measuring_agency) else NA_character_,
      field_visit_id = if ("field_visit_id" %in% names(raw)) gw_chr(.data$field_visit_id) else NA_character_,
      approval_status = if ("approval_status" %in% names(raw)) gw_chr(.data$approval_status) else NA_character_,
      qualifier = if ("qualifier" %in% names(raw)) gw_chr(.data$qualifier) else NA_character_,
      last_modified = if ("last_modified" %in% names(raw)) gw_chr(.data$last_modified) else NA_character_,
      last_modified_utc = suppressWarnings(lubridate::as_datetime(.data$last_modified, tz = "UTC"))
    )

  after_site <- raw2 |>
    dplyr::filter(!is.na(.data$site_no))
  accounting$records_after_site_id <- nrow(after_site)

  after_parameter <- after_site |>
    dplyr::filter(.data$parameter_code == .env$parameter_code)
  accounting$records_after_parameter <- nrow(after_parameter)

  after_datetime <- after_parameter |>
    dplyr::filter(!is.na(.data$obs_datetime))
  accounting$records_after_datetime <- nrow(after_datetime)

  parsed <- after_datetime |>
    dplyr::filter(!is.na(.data$obs_value))
  accounting$records_after_value <- nrow(parsed)
  accounting$records_parsed <- nrow(parsed)

  if (nrow(parsed) == 0) {
    return(list(raw = raw, latest = gw_empty_latest(), accounting = accounting))
  }

  latest <- parsed |>
    dplyr::arrange(.data$site_no, .data$obs_datetime) |>
    dplyr::group_by(.data$site_no) |>
    dplyr::slice_tail(n = 1) |>
    dplyr::ungroup() |>
    dplyr::mutate(
      api_latest_wl_datetime_utc = format(
        lubridate::with_tz(.data$obs_datetime, "UTC"),
        "%Y-%m-%dT%H:%M:%SZ"
      ),
      api_latest_wl_date = as.Date(.data$obs_datetime),
      api_latest_wl_last_modified_utc = dplyr::if_else(
        is.na(.data$last_modified_utc),
        NA_character_,
        format(
          lubridate::with_tz(.data$last_modified_utc, "UTC"),
          "%Y-%m-%dT%H:%M:%SZ"
        )
      )
    ) |>
    dplyr::transmute(
      site_no = .data$site_no,
      api_latest_wl_ft_bgs = .data$obs_value,
      api_latest_wl_datetime_utc = .data$api_latest_wl_datetime_utc,
      api_latest_wl_date = .data$api_latest_wl_date,
      api_latest_wl_status = .data$approval_status,
      api_latest_wl_procedure = .data$observing_procedure,
      api_latest_wl_qualifier = .data$qualifier,
      api_latest_wl_units = .data$unit_of_measure,
      api_latest_wl_vertical_datum = .data$vertical_datum,
      api_latest_wl_measuring_agency = .data$measuring_agency,
      api_latest_wl_field_visit_id = .data$field_visit_id,
      api_latest_wl_last_modified_utc = .data$api_latest_wl_last_modified_utc
    )

  accounting$duplicate_collapse_removed <- nrow(parsed) - nrow(latest)
  accounting$unique_site_count_before_join <- nrow(latest)
  if (length(station_index_site_ids) > 0) {
    index_ids <- unique(gw_site_no(station_index_site_ids))
    matches <- latest$site_no %in% index_ids
    accounting$site_join_matches <- sum(matches)
    accounting$site_join_mismatch_removed <- sum(!matches)
    accounting$unique_site_count <- sum(matches)
  } else {
    accounting$site_join_matches <- nrow(latest)
    accounting$site_join_mismatch_removed <- 0L
    accounting$unique_site_count <- nrow(latest)
  }

  list(raw = raw, latest = latest, accounting = accounting)
}

gw_warning_table <- function(results) {
  diagnostics <- unlist(
    lapply(results, function(result) result$diagnostics),
    recursive = FALSE
  )
  diagnostics <- diagnostics[
    vapply(diagnostics, function(x) {
      is.list(x) && !is.null(x$class) && !is.na(x$message)
    }, logical(1))
  ]

  if (length(diagnostics) == 0) {
    return(tibble::tibble(
      class = character(),
      message = character(),
      count = integer()
    ))
  }

  tibble::tibble(
    class = vapply(diagnostics, `[[`, character(1), "class"),
    message = vapply(diagnostics, `[[`, character(1), "message")
  ) |>
    dplyr::count(.data$class, .data$message, name = "count", sort = TRUE)
}

gw_retrieval_summary <- function(retrieval, parser_accounting) {
  outcomes <- vapply(
    retrieval$results,
    `[[`,
    character(1),
    "terminal_outcome"
  )
  source_paths <- vapply(
    retrieval$results,
    `[[`,
    character(1),
    "source_path"
  )
  attempted <- source_paths != "circuit_breaker"
  completed <- outcomes %in% c("success_data", "success_empty")
  warnings <- gw_warning_table(retrieval$results)
  first_sample <- NA_character_
  samples <- vapply(
    retrieval$results,
    function(x) ifelse(is.na(x$safe_sample), NA_character_, x$safe_sample),
    character(1)
  )
  samples <- samples[!is.na(samples)]
  if (length(samples) > 0) {
    first_sample <- samples[[1]]
  } else if (nrow(warnings) > 0) {
    first_sample <- warnings$message[[1]]
  }

  list(
    source_path = if (any(source_paths == "direct_ogc_fallback")) {
      "dataRetrieval_then_direct_ogc_fallback"
    } else {
      "dataRetrieval"
    },
    total_chunks = length(outcomes),
    successful_data_chunks = sum(outcomes == "success_data"),
    successful_empty_chunks = sum(outcomes == "success_empty"),
    transiently_failed_chunks = sum(
      attempted & outcomes == "transient_failure"
    ),
    permanently_failed_chunks = sum(
      attempted & outcomes == "permanent_http_failure"
    ),
    malformed_or_schema_mismatch_chunks = sum(attempted & outcomes %in% c(
      "malformed_payload",
      "schema_mismatch",
      "parse_failure"
    )),
    not_attempted_chunks = sum(!attempted),
    completed_chunks = sum(completed),
    incomplete_chunks = sum(!completed),
    attempts_total = sum(vapply(
      retrieval$results,
      `[[`,
      integer(1),
      "attempt_count"
    )),
    raw_records_received = sum(vapply(
      retrieval$results,
      `[[`,
      integer(1),
      "raw_record_count"
    )),
    raw_records_bound = parser_accounting$raw_records_bound,
    records_with_required_columns = parser_accounting$records_with_required_columns,
    records_after_site_id = parser_accounting$records_after_site_id,
    records_after_parameter = parser_accounting$records_after_parameter,
    records_after_datetime = parser_accounting$records_after_datetime,
    records_after_value = parser_accounting$records_after_value,
    records_parsed = parser_accounting$records_parsed,
    missing_required_columns = parser_accounting$missing_required_columns,
    duplicate_collapse_removed = parser_accounting$duplicate_collapse_removed,
    unique_site_count_before_join = parser_accounting$unique_site_count_before_join,
    site_join_matches = parser_accounting$site_join_matches,
    site_join_mismatch_removed = parser_accounting$site_join_mismatch_removed,
    unique_site_count = parser_accounting$unique_site_count,
    warning_class_count = nrow(warnings),
    warning_event_count = if (nrow(warnings) == 0) 0L else sum(warnings$count),
    first_safe_warning_or_error = first_sample,
    warning_summary = warnings
  )
}

gw_log_retrieval_summary <- function(summary, max_warning_classes = 5L) {
  message(
    "USGS groundwater chunk summary | source_path=", summary$source_path,
    " | total=", summary$total_chunks,
    " | success_data=", summary$successful_data_chunks,
    " | success_empty=", summary$successful_empty_chunks,
    " | transient_failure=", summary$transiently_failed_chunks,
    " | permanent_failure=", summary$permanently_failed_chunks,
    " | malformed_or_schema=", summary$malformed_or_schema_mismatch_chunks,
    " | not_attempted=", summary$not_attempted_chunks,
    " | incomplete=", summary$incomplete_chunks,
    " | attempts=", summary$attempts_total
  )
  message(
    "USGS groundwater parser accounting | raw_received=",
    summary$raw_records_received,
    " | raw_bound=", summary$raw_records_bound,
    " | required_columns=", summary$records_with_required_columns,
    " | site_id=", summary$records_after_site_id,
    " | parameter=", summary$records_after_parameter,
    " | datetime=", summary$records_after_datetime,
    " | value=", summary$records_after_value,
    " | parsed=", summary$records_parsed,
    " | duplicate_collapse=", summary$duplicate_collapse_removed,
    " | pre_join_sites=", summary$unique_site_count_before_join,
    " | join_mismatch=", summary$site_join_mismatch_removed,
    " | unique_sites=", summary$unique_site_count
  )
  if (length(summary$missing_required_columns) > 0) {
    message(
      "USGS groundwater parser missing required columns: ",
      paste(summary$missing_required_columns, collapse = ", ")
    )
  }

  if (!is.na(summary$first_safe_warning_or_error)) {
    message(
      "USGS groundwater first safe warning/error sample: ",
      summary$first_safe_warning_or_error
    )
  }

  warnings <- summary$warning_summary
  if (nrow(warnings) > 0) {
    limit <- min(nrow(warnings), as.integer(max_warning_classes))
    for (i in seq_len(limit)) {
      message(
        "USGS groundwater warning summary | class=", warnings$class[[i]],
        " | count=", warnings$count[[i]],
        " | sample=", warnings$message[[i]]
      )
    }
    if (nrow(warnings) > limit) {
      message(
        "USGS groundwater warning summary | additional_classes=",
        nrow(warnings) - limit
      )
    }
  }
}

gw_failure_class <- function(summary, min_api_sites) {
  if (summary$incomplete_chunks > 0) {
    if (summary$transiently_failed_chunks > 0 ||
        summary$permanently_failed_chunks > 0) {
      return("incomplete chunk retrieval")
    }
    return("malformed/unexpected API response")
  }
  if (summary$raw_records_received == 0L) {
    return("valid-but-insufficient data")
  }
  if (summary$records_parsed == 0L || summary$unique_site_count == 0L) {
    return("parser/filter collapse")
  }
  if (summary$unique_site_count < min_api_sites) {
    return("valid-but-insufficient data")
  }
  "adequate data"
}

gw_enforce_publication <- function(summary, min_api_sites) {
  class <- gw_failure_class(summary, min_api_sites)

  if (summary$incomplete_chunks > 0L) {
    stop(
      "Groundwater publication refused: ", class, ". ",
      summary$completed_chunks, "/", summary$total_chunks,
      " chunks completed; complete retrieval of all chunks is required. ",
      "API latest unique sites: ", summary$unique_site_count,
      "; minimum remains ", min_api_sites, ".",
      call. = FALSE
    )
  }

  if (summary$unique_site_count < min_api_sites) {
    stop(
      "Groundwater publication refused: ", class, ". ",
      "Raw records received: ", summary$raw_records_received,
      "; records parsed: ", summary$records_parsed,
      "; API latest unique sites: ", summary$unique_site_count,
      "; minimum remains ", min_api_sites, ".",
      call. = FALSE
    )
  }

  invisible(TRUE)
}

gw_required_output_properties <- c(
  "site_no",
  "station_nm",
  "latest_wl_ft_bgs",
  "latest_wl_units",
  "latest_wl_source",
  "has_api_latest_wl",
  "latest_wl_datetime_utc",
  "latest_wl_date",
  "latest_age_days",
  "latest_wl_status",
  "latest_status",
  "feed_build_time_utc"
)

gw_validate_output_objects <- function(geojson,
                                       summary,
                                       min_features,
                                       min_api_sites = min_features) {
  if (!is.list(geojson) || !identical(geojson$type, "FeatureCollection") ||
      !is.list(geojson$features)) {
    stop("Groundwater output QA failure: GeoJSON root is not a FeatureCollection.")
  }
  if (length(geojson$features) < min_features) {
    stop(
      "Groundwater output QA failure: only ",
      length(geojson$features),
      " feature(s); minimum remains ",
      min_features,
      "."
    )
  }

  for (i in seq_along(geojson$features)) {
    feature <- geojson$features[[i]]
    if (!is.list(feature) || !identical(feature$type, "Feature") ||
        !is.list(feature$geometry) ||
        !identical(feature$geometry$type, "Point")) {
      stop("Groundwater output QA failure: invalid Point feature at ordinal ", i, ".")
    }
    coordinates <- unlist(feature$geometry$coordinates, use.names = FALSE)
    if (length(coordinates) != 2L || any(!is.finite(coordinates)) ||
        coordinates[[1]] < -180 || coordinates[[1]] > 180 ||
        coordinates[[2]] < -90 || coordinates[[2]] > 90) {
      stop("Groundwater output QA failure: invalid coordinates at feature ", i, ".")
    }
    if (!is.list(feature$properties)) {
      stop("Groundwater output QA failure: missing properties at feature ", i, ".")
    }
    missing <- setdiff(gw_required_output_properties, names(feature$properties))
    if (length(missing) > 0) {
      stop(
        "Groundwater output QA failure: feature ",
        i,
        " missing required properties: ",
        paste(missing, collapse = ", "),
        "."
      )
    }
  }

  if (!is.list(summary) || is.null(summary$output_feature_count) ||
      as.integer(summary$output_feature_count) != length(geojson$features)) {
    stop("Groundwater output QA failure: summary feature count is incoherent.")
  }
  api_latest_site_count <- suppressWarnings(
    as.integer(summary$api_latest_site_count)
  )
  if (length(api_latest_site_count) != 1L ||
      is.na(api_latest_site_count) ||
      api_latest_site_count < min_api_sites ||
      api_latest_site_count > length(geojson$features)) {
    stop(
      "Groundwater output QA failure: summary API latest count is outside ",
      "the valid range ",
      min_api_sites,
      "..",
      length(geojson$features),
      "."
    )
  }

  invisible(TRUE)
}

gw_validate_output_files <- function(geojson_path,
                                     summary_path,
                                     min_features,
                                     min_api_sites = min_features) {
  geojson <- jsonlite::read_json(geojson_path, simplifyVector = FALSE)
  summary <- jsonlite::read_json(summary_path, simplifyVector = FALSE)
  gw_validate_output_objects(
    geojson,
    summary,
    min_features,
    min_api_sites
  )
}

gw_promote_complete_set <- function(staged_paths,
                                    target_paths,
                                    rename_fun = file.rename,
                                    exists_fun = file.exists,
                                    remove_fun = unlink) {
  if (length(staged_paths) != length(target_paths) || length(staged_paths) == 0) {
    stop("Groundwater promotion requires equal nonempty staged and target sets.")
  }

  backup_paths <- vapply(
    target_paths,
    function(target) {
      tempfile(
        pattern = paste0(".", basename(target), ".prior-"),
        tmpdir = dirname(target)
      )
    },
    character(1)
  )
  had_prior <- vapply(target_paths, exists_fun, logical(1))
  promoted <- rep(FALSE, length(target_paths))
  backed_up <- rep(FALSE, length(target_paths))

  rollback <- function() {
    failures <- character()
    for (i in rev(seq_along(target_paths))) {
      if (promoted[[i]] && exists_fun(target_paths[[i]])) {
        remove_fun(target_paths[[i]])
        if (exists_fun(target_paths[[i]])) {
          failures <- c(failures, target_paths[[i]])
        }
      }
      if (backed_up[[i]] && exists_fun(backup_paths[[i]])) {
        if (!isTRUE(rename_fun(backup_paths[[i]], target_paths[[i]]))) {
          failures <- c(failures, backup_paths[[i]])
        }
      }
    }
    unique(failures)
  }

  for (i in seq_along(target_paths)) {
    if (had_prior[[i]]) {
      if (!isTRUE(rename_fun(target_paths[[i]], backup_paths[[i]]))) {
        rollback_failures <- rollback()
        stop(
          "Groundwater output promotion failed while backing up ",
          target_paths[[i]],
          if (length(rollback_failures) > 0) {
            paste0(
              "; rollback also failed for ",
              paste(rollback_failures, collapse = ", ")
            )
          } else {
            ""
          },
          "."
        )
      }
      backed_up[[i]] <- TRUE
    }

    if (!isTRUE(rename_fun(staged_paths[[i]], target_paths[[i]]))) {
      rollback_failures <- rollback()
      stop(
        "Groundwater output promotion failed for ",
        target_paths[[i]],
        if (length(rollback_failures) > 0) {
          paste0(
            "; rollback also failed for ",
            paste(rollback_failures, collapse = ", ")
          )
        } else {
          ""
        },
        "."
      )
    }
    promoted[[i]] <- TRUE
  }

  for (i in seq_along(backup_paths)) {
    if (backed_up[[i]] && exists_fun(backup_paths[[i]])) {
      remove_fun(backup_paths[[i]])
    }
  }

  invisible(TRUE)
}

gw_write_staged_outputs <- function(geojson,
                                    summary,
                                    out_geojson,
                                    out_summary,
                                    min_features,
                                    min_api_sites = min_features) {
  if (!identical(dirname(out_geojson), dirname(out_summary))) {
    stop("Groundwater outputs must share one directory for complete-set promotion.")
  }

  gw_validate_output_objects(
    geojson,
    summary,
    min_features,
    min_api_sites
  )
  dir.create(dirname(out_geojson), recursive = TRUE, showWarnings = FALSE)
  stage_dir <- tempfile(
    pattern = ".usgs-groundwater-stage-",
    tmpdir = dirname(out_geojson)
  )
  dir.create(stage_dir, recursive = TRUE)
  on.exit(unlink(stage_dir, recursive = TRUE, force = TRUE), add = TRUE)

  staged_geojson <- file.path(stage_dir, basename(out_geojson))
  staged_summary <- file.path(stage_dir, basename(out_summary))

  jsonlite::write_json(
    geojson,
    staged_geojson,
    auto_unbox = TRUE,
    null = "null",
    na = "null",
    digits = 8,
    pretty = FALSE
  )
  jsonlite::write_json(
    summary,
    staged_summary,
    auto_unbox = TRUE,
    null = "null",
    na = "null",
    digits = 8,
    pretty = TRUE
  )
  gw_validate_output_files(
    staged_geojson,
    staged_summary,
    min_features,
    min_api_sites
  )

  gw_promote_complete_set(
    staged_paths = c(staged_geojson, staged_summary),
    target_paths = c(out_geojson, out_summary)
  )
  invisible(TRUE)
}
