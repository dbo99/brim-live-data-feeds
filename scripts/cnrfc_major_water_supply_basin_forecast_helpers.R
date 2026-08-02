CNRFC_FORECAST_SCHEMA_VERSION <- "1.0"
CNRFC_FORECAST_PRODUCT_ID <- "major_water_supply_basin_forecasts"
CNRFC_ROSTER_SCHEMA_VERSION <- "1.0.0"
CNRFC_ROSTER_VERSION <- "cnrfc-major-water-supply-v1.1.0"
CNRFC_WATER_YEAR_STALE_AFTER_HOURS <- 72
CNRFC_WATER_YEAR_EXPIRES_AFTER_HOURS <- 168
CNRFC_ACCUMULATION_STALE_AFTER_HOURS <- 36
CNRFC_APRIL_JULY_STALE_AFTER_HOURS <- 36
CNRFC_ACCUMULATION_ROUNDING_TOLERANCE_KAF <- 0.1
CNRFC_APRIL_JULY_MATCH_TOLERANCE_KAF <- 0.05
CNRFC_MAX_RESPONSE_BYTES <- 4L * 1024L * 1024L
CNRFC_ALLOWED_STATUSES <- c(
  "current",
  "current_partial",
  "source_stale",
  "stale_last_known_good",
  "expired",
  "unavailable",
  "failed_no_data"
)
CNRFC_ALLOWED_METRIC_STATUSES <- c(
  "current", "source_stale", "stale_last_known_good",
  "unavailable", "expired", "failed_no_data"
)
CNRFC_ALLOWED_VALUE_ORIGINS <- c("current_source", "last_known_good", "none")
CNRFC_ALLOWED_ATTEMPT_OUTCOMES <- c(
  "success", "source_unavailable", "fetch_failed", "parse_failed", "validation_failed"
)
CNRFC_ALLOWED_FAILURE_STAGES <- c("source", "fetch", "parse", "validate")
CNRFC_USER_AGENT <- paste0(
  "BRIM-live-data-feeds/1.0 ",
  "(+https://github.com/dbo99/brim-live-data-feeds; public CNRFC forecast retrieval)"
)

cnrfc_stop <- function(...) {
  stop(..., call. = FALSE)
}

cnrfc_source_unavailable <- function(message) {
  stop(structure(
    list(message = as.character(message), call = NULL),
    class = c("cnrfc_source_unavailable", "error", "condition")
  ))
}

cnrfc_validation_error <- function(message) {
  stop(structure(
    list(message = as.character(message), call = NULL),
    class = c("cnrfc_validation_error", "error", "condition")
  ))
}

cnrfc_normalize_space <- function(x) {
  x <- as.character(x)
  x <- gsub("[\r\n\t\u00a0]+", " ", x, perl = TRUE)
  trimws(gsub("[[:space:]]+", " ", x, perl = TRUE))
}

cnrfc_iso_utc <- function(x = Sys.time()) {
  format(as.POSIXct(x, tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
}

cnrfc_parse_utc <- function(x) {
  parsed <- as.POSIXct(
    as.character(x),
    format = "%Y-%m-%dT%H:%M:%SZ",
    tz = "UTC"
  )
  if (length(parsed) != 1L || is.na(parsed)) {
    cnrfc_stop("Invalid UTC timestamp: ", x)
  }
  parsed
}

cnrfc_expected_roster <- function() {
  basin_lids <- c(
    "SCSC1", "TMDC1", "SHDC1", "CEGC1", "ORDC1", "HLEC1",
    "FOLC1", "NDPC1", "EXQC1", "FRAC1", "PFTC1", "ISAC1",
    "MHBC1", "CMPC1", "NMSC1"
  )
  data.frame(
    nws_lid = c(
      basin_lids, "SACC0", "VNSC0", "MLIC0", basin_lids,
      basin_lids, "SACC0", "VNSC0", "MLIC0"
    ),
    product_type = c(
      rep("water_year_fnf", 15L),
      rep("water_year_index", 3L),
      rep("ten_day_streamflow_volume_accumulation", 15L),
      rep("april_july_streamflow_volume_forecast", 18L)
    ),
    stringsAsFactors = FALSE
  )
}

cnrfc_validate_roster <- function(roster) {
  required <- c(
    "schema_version", "forecast_key", "rfc", "nws_lid", "product_type",
    "source_role", "view_group", "display_name", "source_url", "enabled",
    "expected_product_label", "display_order"
  )
  missing <- setdiff(required, names(roster))
  if (length(missing) > 0L) {
    cnrfc_stop("CNRFC forecast roster is missing column(s): ", paste(missing, collapse = ", "))
  }

  forbidden <- grep(
    "geometry|polygon|latitude|longitude|coordinates|component",
    names(roster),
    ignore.case = TRUE,
    value = TRUE
  )
  if (length(forbidden) > 0L) {
    cnrfc_stop("Geometry/component fields are forbidden in the live-value roster: ", paste(forbidden, collapse = ", "))
  }

  enabled <- as.character(roster$enabled)
  enabled <- tolower(enabled) %in% c("true", "t", "1")
  if (!all(enabled)) {
    cnrfc_stop("The reviewed initial CNRFC forecast roster must contain exactly 51 enabled records.")
  }

  roster$nws_lid <- toupper(cnrfc_normalize_space(roster$nws_lid))
  roster$forecast_key <- cnrfc_normalize_space(roster$forecast_key)
  roster$rfc <- toupper(cnrfc_normalize_space(roster$rfc))
  roster$product_type <- cnrfc_normalize_space(roster$product_type)
  roster$source_role <- cnrfc_normalize_space(roster$source_role)
  roster$view_group <- cnrfc_normalize_space(roster$view_group)
  roster$display_name <- cnrfc_normalize_space(roster$display_name)
  roster$source_url <- cnrfc_normalize_space(roster$source_url)
  roster$expected_product_label <- cnrfc_normalize_space(roster$expected_product_label)
  roster$display_order <- suppressWarnings(as.integer(roster$display_order))

  expected <- cnrfc_expected_roster()
  if (nrow(roster) != nrow(expected)) {
    cnrfc_stop("Expected exactly 51 reviewed CNRFC forecast records; found ", nrow(roster), ".")
  }
  if (anyDuplicated(roster$forecast_key)) cnrfc_stop("forecast_key values must be unique.")
  lid_product <- paste(roster$nws_lid, roster$product_type, sep = "|")
  if (anyDuplicated(lid_product)) cnrfc_stop("nws_lid/product_type pairs must be unique.")
  if (anyDuplicated(roster$display_order) || anyNA(roster$display_order)) {
    cnrfc_stop("display_order values must be unique whole numbers.")
  }
  if (!all(as.character(roster$schema_version) == CNRFC_ROSTER_SCHEMA_VERSION)) {
    cnrfc_stop("Every roster row must use schema_version ", CNRFC_ROSTER_SCHEMA_VERSION, ".")
  }
  if (!all(roster$rfc == "CNRFC")) cnrfc_stop("The initial roster is CNRFC-only.")

  expected$source_role <- c(
    rep("fnf_forecast_watershed", 15L),
    rep("fnf_index_watershed", 3L),
    rep("fnf_forecast_watershed", 15L),
    rep("fnf_forecast_watershed", 15L),
    rep("fnf_index_watershed", 3L)
  )
  expected$view_group <- c(
    rep("major_basin", 15L), rep("index", 3L), rep("major_basin", 15L),
    rep("major_basin", 15L), rep("index", 3L)
  )
  actual_identity <- roster[, c("nws_lid", "product_type", "source_role", "view_group")]
  rownames(actual_identity) <- NULL
  if (!identical(actual_identity, expected)) {
    cnrfc_stop(
      "Roster identities/order do not match the reviewed 18 water-year, 15 accumulation, ",
      "and 18 April-July records."
    )
  }

  key_suffix <- c(
    water_year_fnf = "WY_FNF",
    water_year_index = "WY_INDEX",
    ten_day_streamflow_volume_accumulation = "10D_VOLUME_ACCUM",
    april_july_streamflow_volume_forecast = "APR_JUL_VOLUME"
  )
  expected_keys <- paste0("CNRFC:", roster$nws_lid, ":", unname(key_suffix[roster$product_type]))
  if (!identical(roster$forecast_key, unname(expected_keys))) {
    cnrfc_stop("forecast_key values do not match RFC/LID/product identity.")
  }

  product_id <- ifelse(
    roster$product_type == "ten_day_streamflow_volume_accumulation",
    "2",
    ifelse(roster$product_type == "april_july_streamflow_volume_forecast", "7", "9")
  )
  expected_urls <- paste0(
    "https://www.cnrfc.noaa.gov/ensembleProduct.php?id=",
    roster$nws_lid,
    "&prodID=",
    product_id
  )
  if (!identical(roster$source_url, unname(expected_urls))) {
    cnrfc_stop(
      "Every source_url must be the matching official CNRFC prodID=9, prodID=2, or prodID=7 page."
    )
  }

  if (any(roster$display_name == "") || any(roster$expected_product_label == "")) {
    cnrfc_stop("Roster display and expected product labels must be nonempty.")
  }

  roster
}

cnrfc_read_roster <- function(path) {
  if (!file.exists(path)) cnrfc_stop("CNRFC forecast roster not found: ", path)
  roster <- utils::read.csv(path, stringsAsFactors = FALSE, check.names = FALSE)
  cnrfc_validate_roster(roster)
}

cnrfc_decode_html <- function(raw_content) {
  text <- rawToChar(raw_content)
  decoded <- iconv(text, from = "latin1", to = "UTF-8", sub = "byte")
  if (is.na(decoded)) decoded <- iconv(text, from = "", to = "UTF-8", sub = "byte")
  if (is.na(decoded)) cnrfc_stop("CNRFC response could not be decoded as text.")
  decoded
}

cnrfc_default_fetch_once <- function(url,
                                     timeout_sec,
                                     connect_timeout_sec,
                                     user_agent) {
  handle <- curl::new_handle()
  curl::handle_setopt(
    handle,
    useragent = user_agent,
    followlocation = TRUE,
    timeout = timeout_sec,
    connecttimeout = connect_timeout_sec,
    httpheader = c(
      "Accept" = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
      "Cache-Control" = "no-cache"
    )
  )
  curl::curl_fetch_memory(url, handle = handle)
}

cnrfc_retryable_http_status <- function(status) {
  status %in% c(408L, 429L, 500L, 502L, 503L, 504L)
}

cnrfc_fetch_page <- function(url,
                             timeout_sec = 30,
                             connect_timeout_sec = 10,
                             max_attempts = 3L,
                             max_response_bytes = CNRFC_MAX_RESPONSE_BYTES,
                             user_agent = CNRFC_USER_AGENT,
                             fetch_once = cnrfc_default_fetch_once,
                             sleep_fun = Sys.sleep) {
  max_attempts <- as.integer(max_attempts)
  if (is.na(max_attempts) || max_attempts < 1L || max_attempts > 3L) {
    cnrfc_stop("max_attempts must be between 1 and 3.")
  }
  if (!is.finite(timeout_sec) || timeout_sec < 5 || timeout_sec > 60) {
    cnrfc_stop("timeout_sec must be between 5 and 60 seconds.")
  }
  if (!is.finite(connect_timeout_sec) || connect_timeout_sec < 1 ||
      connect_timeout_sec > timeout_sec) {
    cnrfc_stop("connect_timeout_sec must be positive and no greater than timeout_sec.")
  }

  attempt_diagnostics <- vector("list", max_attempts)
  for (attempt in seq_len(max_attempts)) {
    started <- Sys.time()
    response <- tryCatch(
      fetch_once(url, timeout_sec, connect_timeout_sec, user_agent),
      error = function(e) e
    )
    elapsed <- as.numeric(difftime(Sys.time(), started, units = "secs"))

    if (inherits(response, "error")) {
      attempt_diagnostics[[attempt]] <- list(
        attempt = attempt,
        outcome = "transport_error",
        message = substr(conditionMessage(response), 1L, 240L),
        elapsed_seconds = round(elapsed, 3)
      )
      retryable <- TRUE
    } else {
      status <- as.integer(response$status_code)
      content <- response$content
      headers <- response$headers
      content_type <- if (!is.null(response$type)) as.character(response$type) else ""
      if (!is.null(headers) && "content-type" %in% names(headers)) {
        content_type <- as.character(headers[["content-type"]])
      }
      response_bytes <- length(content)
      retryable <- cnrfc_retryable_http_status(status)
      attempt_diagnostics[[attempt]] <- list(
        attempt = attempt,
        outcome = if (status >= 200L && status < 300L) "http_success" else "http_error",
        http_status = status,
        content_type = content_type,
        response_bytes = response_bytes,
        elapsed_seconds = round(elapsed, 3)
      )

      if (status >= 200L && status < 300L) {
        if (response_bytes < 200L) {
          return(list(
            success = FALSE,
            failure_class = "response_too_small",
            message = paste0("CNRFC response was only ", response_bytes, " bytes."),
            source_url = url,
            attempts = attempt_diagnostics[seq_len(attempt)]
          ))
        }
        if (response_bytes > max_response_bytes) {
          return(list(
            success = FALSE,
            failure_class = "response_too_large",
            message = paste0("CNRFC response exceeded ", max_response_bytes, " bytes."),
            source_url = url,
            attempts = attempt_diagnostics[seq_len(attempt)]
          ))
        }
        if (nzchar(content_type) && !grepl("text/html|application/xhtml", content_type, ignore.case = TRUE)) {
          return(list(
            success = FALSE,
            failure_class = "unexpected_content_type",
            message = paste0("Unexpected CNRFC content type: ", content_type),
            source_url = url,
            attempts = attempt_diagnostics[seq_len(attempt)]
          ))
        }
        html <- tryCatch(cnrfc_decode_html(content), error = function(e) e)
        if (inherits(html, "error") || !nzchar(html)) {
          return(list(
            success = FALSE,
            failure_class = "decode_failed",
            message = if (inherits(html, "error")) conditionMessage(html) else "Decoded response was empty.",
            source_url = url,
            attempts = attempt_diagnostics[seq_len(attempt)]
          ))
        }
        return(list(
          success = TRUE,
          html = html,
          retrieved_at = cnrfc_iso_utc(),
          source_url = url,
          attempts = attempt_diagnostics[seq_len(attempt)]
        ))
      }
    }

    if (!retryable || attempt == max_attempts) break
    sleep_fun(min(4, 2^(attempt - 1L)))
  }

  last <- attempt_diagnostics[[max(which(!vapply(attempt_diagnostics, is.null, logical(1))))]]
  list(
    success = FALSE,
    failure_class = if (identical(last$outcome, "transport_error")) "retrieval_failed" else "http_failed",
    message = if (!is.null(last$message)) last$message else paste0("CNRFC returned HTTP ", last$http_status, "."),
    source_url = url,
    attempts = attempt_diagnostics[!vapply(attempt_diagnostics, is.null, logical(1))]
  )
}

cnrfc_extract_unique_labeled_table_value <- function(document, label) {
  rows <- xml2::xml_find_all(document, ".//tr")
  values <- character()
  for (row in rows) {
    cells <- xml2::xml_find_all(row, "./th|./td")
    if (length(cells) < 2L) next
    cell_text <- cnrfc_normalize_space(xml2::xml_text(cells))
    matches <- which(cell_text == label)
    for (index in matches) {
      if (index < length(cells)) values <- c(values, cell_text[[index + 1L]])
    }
  }
  if (length(values) != 1L) {
    cnrfc_stop("Expected exactly one labeled '", label, "' value; found ", length(values), ".")
  }
  values[[1L]]
}

cnrfc_fixed_count <- function(text, pattern) {
  match <- gregexpr(pattern, text, fixed = TRUE)[[1L]]
  if (identical(match[[1L]], -1L)) 0L else length(match)
}

cnrfc_strip_markup <- function(text) {
  text <- gsub("<[^>]*>", " ", text, perl = TRUE)
  text <- gsub("&nbsp;|&#160;", " ", text, ignore.case = TRUE)
  cnrfc_normalize_space(text)
}

cnrfc_is_missing_token <- function(token) {
  normalized <- toupper(cnrfc_normalize_space(token))
  grepl("^(N/?A|NA|--+|UNAVAILABLE|NOT AVAILABLE|MISSING|BLANK)$", normalized)
}

cnrfc_parse_volume_token <- function(token) {
  token <- cnrfc_normalize_space(token)
  if (cnrfc_is_missing_token(token) || grepl(
    "^(N/?A|NA|--+|UNAVAILABLE|NOT AVAILABLE|MISSING|BLANK)(?:\\s+[A-Za-z][A-Za-z0-9^./ -]{0,39})$",
    toupper(token),
    perl = TRUE
  )) {
    return(list(value = NULL, units = NULL, missing_reason = "forecast_volume_unavailable"))
  }
  matched <- regexec(
    "^([-+]?[0-9][0-9,]*(?:\\.[0-9]+)?)\\s*(.*?)$",
    token,
    perl = TRUE
  )
  parts <- regmatches(token, matched)[[1L]]
  if (length(parts) != 3L) {
    cnrfc_validation_error(paste0("Could not parse labeled Median Forecast token: ", token))
  }
  value <- suppressWarnings(as.numeric(gsub(",", "", parts[[2L]], fixed = TRUE)))
  units <- cnrfc_normalize_space(parts[[3L]])
  if (!is.finite(value)) cnrfc_validation_error(paste0("Median Forecast was not finite: ", token))
  if (value == -9999) {
    return(list(
      value = NULL,
      units = if (nzchar(units)) units else NULL,
      missing_reason = "forecast_volume_sentinel_-9999"
    ))
  }
  if (value < 0 || value > 1000000) {
    cnrfc_validation_error(paste0("Median Forecast failed plausibility bounds: ", token))
  }
  if (!nzchar(units) || nchar(units) > 40L) {
    cnrfc_validation_error(paste0("Median Forecast units were missing or invalid: ", token))
  }
  list(value = value, units = units, missing_reason = NULL)
}

cnrfc_parse_percent_token <- function(token, field_name) {
  token <- cnrfc_normalize_space(token)
  token_without_percent <- cnrfc_normalize_space(gsub("%", "", token, fixed = TRUE))
  if (cnrfc_is_missing_token(token_without_percent)) {
    return(list(value = NULL, missing_reason = paste0(field_name, "_unavailable")))
  }
  if (!grepl("^[-+]?[0-9][0-9,]*(?:\\.[0-9]+)?%?$", token, perl = TRUE)) {
    cnrfc_validation_error(paste0("Could not parse labeled ", field_name, " token: ", token))
  }
  value <- suppressWarnings(as.numeric(gsub(",", "", token_without_percent, fixed = TRUE)))
  if (!is.finite(value)) cnrfc_validation_error(paste0(field_name, " was not finite: ", token))
  if (value == -9999) {
    return(list(value = NULL, missing_reason = paste0(field_name, "_sentinel_-9999")))
  }
  if (value < 0 || value > 10000) {
    cnrfc_validation_error(paste0(field_name, " failed plausibility bounds: ", token))
  }
  list(value = value, missing_reason = NULL)
}

cnrfc_parse_local_source_time <- function(text, field_name, comma_after_day) {
  text <- cnrfc_normalize_space(text)
  comma_pattern <- if (isTRUE(comma_after_day)) "," else ""
  matched <- regexec(
    paste0(
      "^([A-Za-z]{3}) ([0-9]{1,2})", comma_pattern,
      " ([0-9]{4}) at ([0-9]{1,2}):([0-9]{2}) (AM|PM) (PST|PDT)$"
    ),
    text,
    perl = TRUE
  )
  parts <- regmatches(text, matched)[[1L]]
  if (length(parts) != 8L) {
    cnrfc_validation_error(paste0("Unrecognized CNRFC ", field_name, ": ", text))
  }
  months <- setNames(seq_len(12L), month.abb)
  month_number <- unname(months[[parts[[2L]]]])
  if (is.null(month_number)) {
    cnrfc_validation_error(paste0("Unrecognized month in CNRFC ", field_name, ": ", text))
  }
  hour <- as.integer(parts[[5L]])
  if (parts[[7L]] == "PM" && hour != 12L) hour <- hour + 12L
  if (parts[[7L]] == "AM" && hour == 12L) hour <- 0L
  local_text <- sprintf(
    "%04d-%02d-%02d %02d:%02d:00",
    as.integer(parts[[4L]]),
    month_number,
    as.integer(parts[[3L]]),
    hour,
    as.integer(parts[[6L]])
  )
  instant <- as.POSIXct(local_text, format = "%Y-%m-%d %H:%M:%S", tz = "America/Los_Angeles")
  if (is.na(instant)) cnrfc_validation_error(paste0("Invalid CNRFC ", field_name, ": ", text))
  observed_zone <- format(instant, "%Z", tz = "America/Los_Angeles")
  if (!identical(observed_zone, parts[[8L]])) {
    cnrfc_validation_error(paste0(
      "CNRFC ", field_name, " zone did not match America/Los_Angeles: ", text
    ))
  }
  offset <- format(instant, "%z", tz = "America/Los_Angeles")
  offset <- paste0(substr(offset, 1L, 3L), ":", substr(offset, 4L, 5L))
  list(
    instant = instant,
    iso = paste0(format(instant, "%Y-%m-%dT%H:%M:%S", tz = "America/Los_Angeles"), offset)
  )
}

cnrfc_parse_issue_time <- function(text) {
  cnrfc_parse_local_source_time(text, "Issuance Time", comma_after_day = TRUE)
}

cnrfc_parse_data_updated_time <- function(text) {
  cnrfc_parse_local_source_time(text, "Data Updated time", comma_after_day = FALSE)
}

cnrfc_current_water_year <- function(instant) {
  year <- as.integer(format(instant, "%Y", tz = "America/Los_Angeles"))
  month <- as.integer(format(instant, "%m", tz = "America/Los_Angeles"))
  if (month >= 10L) year + 1L else year
}

cnrfc_parse_water_year_page <- function(html,
                                        roster_row,
                                        retrieved_at,
                                        stale_after_hours = CNRFC_WATER_YEAR_STALE_AFTER_HOURS) {
  if (nrow(roster_row) != 1L) cnrfc_stop("cnrfc_parse_page requires exactly one roster row.")
  document <- tryCatch(
    xml2::read_html(html, options = c("RECOVER", "NOERROR", "NOWARNING")),
    error = function(e) e
  )
  if (inherits(document, "error")) cnrfc_stop("CNRFC HTML could not be parsed: ", conditionMessage(document))

  titles <- cnrfc_normalize_space(xml2::xml_text(xml2::xml_find_all(document, ".//title")))
  expected_title <- paste0("CNRFC - Ensemble Products - ", roster_row$nws_lid[[1L]])
  if (length(titles) != 1L || !identical(titles[[1L]], expected_title)) {
    cnrfc_stop("Source page title did not match expected NWS LID ", roster_row$nws_lid[[1L]], ".")
  }

  period_labels <- cnrfc_normalize_space(xml2::xml_text(xml2::xml_find_all(document, ".//i")))
  period_matches <- grep("^[0-9]{4} Water Year Trend Plot$", period_labels, value = TRUE)
  if (length(period_matches) != 1L) {
    cnrfc_stop("Expected exactly one '<year> Water Year Trend Plot' label; found ", length(period_matches), ".")
  }
  water_year <- as.integer(sub(" .*", "", period_matches[[1L]]))

  scripts <- xml2::xml_text(xml2::xml_find_all(document, ".//script"))
  summary_scripts <- scripts[grepl("Median Forecast:", scripts, fixed = TRUE)]
  label_count <- sum(vapply(summary_scripts, cnrfc_fixed_count, integer(1), pattern = "Median Forecast:"))
  if (length(summary_scripts) != 1L || label_count != 1L) {
    cnrfc_stop("Expected exactly one labeled Median Forecast summary; found ", label_count, ".")
  }
  summary_text <- cnrfc_strip_markup(summary_scripts[[1L]])
  matched <- regexec(
    "Median Forecast:\\s*([^|]+?)\\s*\\|\\s*([^|]+?)\\s*of Mean\\s*\\|\\s*([^|]+?)\\s*of Median",
    summary_text,
    perl = TRUE
  )
  parts <- regmatches(summary_text, matched)[[1L]]
  if (length(parts) != 4L) cnrfc_stop("The CNRFC forecast summary labels or delimiters changed.")

  if (roster_row$product_type[[1L]] == "water_year_index") {
    if (!grepl("\\bWSI\\b", summary_text, perl = TRUE)) {
      cnrfc_stop("Expected a direct CNRFC WSI index product for ", roster_row$nws_lid[[1L]], ".")
    }
  } else if (grepl("\\bWSI\\b", summary_text, perl = TRUE)) {
    cnrfc_stop("FNF basin record unexpectedly resolved to a WSI index product.")
  }

  volume <- cnrfc_parse_volume_token(parts[[2L]])
  percent_mean <- cnrfc_parse_percent_token(parts[[3L]], "percent_mean")
  percent_median <- cnrfc_parse_percent_token(parts[[4L]], "percent_median")
  missing_reasons <- Filter(
    Negate(is.null),
    list(volume$missing_reason, percent_mean$missing_reason, percent_median$missing_reason)
  )

  if (is.null(volume$value) && (!is.null(percent_mean$value) || !is.null(percent_median$value))) {
    cnrfc_validation_error("CNRFC published percentages without a usable labeled Median Forecast volume.")
  }

  issue_text <- cnrfc_extract_unique_labeled_table_value(document, "Issuance Time:")
  issue <- NULL
  if (nzchar(issue_text) && !cnrfc_is_missing_token(issue_text)) {
    issue <- cnrfc_parse_issue_time(issue_text)
  }
  any_numeric <- !is.null(volume$value) || !is.null(percent_mean$value) || !is.null(percent_median$value)
  if (any_numeric && is.null(issue)) {
    cnrfc_validation_error("CNRFC published forecast values without a usable Issuance Time.")
  }
  if (is.null(issue)) missing_reasons <- c(missing_reasons, "forecast_issue_time_unavailable")

  retrieved_instant <- cnrfc_parse_utc(retrieved_at)
  if (!is.null(issue) && as.numeric(difftime(issue$instant, retrieved_instant, units = "hours")) > 1) {
    cnrfc_validation_error("CNRFC forecast issue time was implausibly later than retrieval time.")
  }

  if (is.null(volume$value) || is.null(percent_mean$value) || is.null(percent_median$value)) {
    status <- "unavailable"
  } else {
    age_hours <- as.numeric(difftime(retrieved_instant, issue$instant, units = "hours"))
    water_year_stale <- water_year != cnrfc_current_water_year(retrieved_instant)
    status <- if (age_hours > stale_after_hours || water_year_stale) "source_stale" else "current"
  }

  signature_material <- paste(
    expected_title,
    period_matches[[1L]],
    issue_text,
    parts[[2L]],
    parts[[3L]],
    parts[[4L]],
    sep = "|"
  )

  list(
    forecast_key = roster_row$forecast_key[[1L]],
    rfc = roster_row$rfc[[1L]],
    nws_lid = roster_row$nws_lid[[1L]],
    product_type = roster_row$product_type[[1L]],
    display_name = roster_row$display_name[[1L]],
    forecast_statistic = "median",
    forecast_volume = volume$value,
    forecast_volume_units = volume$units,
    percent_mean = percent_mean$value,
    percent_median = percent_median$value,
    water_year = water_year,
    forecast_period = paste(water_year, "Water Year"),
    forecast_issued_at = if (is.null(issue)) NULL else issue$iso,
    status = status,
    missing_reason = if (length(missing_reasons) == 0L) NULL else paste(unique(missing_reasons), collapse = "; "),
    source_url = roster_row$source_url[[1L]],
    source_page_signature = digest::digest(signature_material, algo = "sha256", serialize = FALSE),
    diagnostic = list(
      parser = "cnrfc_prod9_semantic_v1",
      source_page_title = expected_title,
      source_product_label = period_matches[[1L]],
      expected_product_label = roster_row$expected_product_label[[1L]],
      error_class = NULL,
      error_message = NULL
    )
  )
}

cnrfc_semantic_label <- function(x) {
  tolower(gsub("[^A-Za-z0-9]+", "", cnrfc_normalize_space(x), perl = TRUE))
}

cnrfc_normalize_accumulation_units <- function(source_units) {
  source_units <- cnrfc_normalize_space(source_units)
  recognized <- c(
    "1000s of Acre-Feet",
    "1000s Acre-Feet",
    "1000s of Ac-Ft",
    "1000s Ac-Ft"
  )
  match_index <- match(tolower(source_units), tolower(recognized))
  if (is.na(match_index)) {
    cnrfc_validation_error(paste0(
      "Unrecognized CNRFC accumulated-volume units: ",
      if (nzchar(source_units)) source_units else "<missing>"
    ))
  }
  "kaf"
}

cnrfc_parse_accumulation_value <- function(token, field_name) {
  token <- cnrfc_normalize_space(token)
  if (!nzchar(token) || cnrfc_is_missing_token(token)) return(NULL)
  if (!grepl("^[-+]?[0-9][0-9,]*(?:\\.[0-9]+)?$", token, perl = TRUE)) {
    cnrfc_validation_error(paste0("Malformed ", field_name, " accumulated-volume cell: ", token))
  }
  value <- suppressWarnings(as.numeric(gsub(",", "", token, fixed = TRUE)))
  if (!is.finite(value)) {
    cnrfc_validation_error(paste0(field_name, " accumulated-volume value was not finite."))
  }
  if (value == -9999) return(NULL)
  if (value < 0 || value > 1000000) {
    cnrfc_validation_error(paste0(
      field_name, " accumulated-volume value failed plausibility bounds: ", token
    ))
  }
  round(value, 1L)
}

cnrfc_validate_cumulative_series <- function(values, series_name) {
  available <- which(vapply(values, function(value) !is.null(value), logical(1)))
  if (length(available) < 2L) return(invisible(TRUE))
  numeric_values <- vapply(values[available], as.numeric, numeric(1))
  decreases <- diff(numeric_values) < -CNRFC_ACCUMULATION_ROUNDING_TOLERANCE_KAF
  if (any(decreases)) {
    first <- which(decreases)[[1L]]
    cnrfc_validation_error(paste0(
      series_name, " cumulative series materially decreased from forecast column ",
      available[[first]], " to ", available[[first + 1L]], "."
    ))
  }
  invisible(TRUE)
}

cnrfc_parse_forecast_dates <- function(labels, source_updated_instant) {
  if (length(labels) != 10L) {
    cnrfc_stop("Expected exactly ten ordered forecast-date columns; found ", length(labels), ".")
  }
  compact <- gsub("[[:space:]]+", "", labels, perl = TRUE)
  parts <- regmatches(compact, regexec("^([A-Za-z]{3})([0-9]{2})$", compact, perl = TRUE))
  if (any(lengths(parts) != 3L)) {
    cnrfc_validation_error("One or more CNRFC forecast-date headings were malformed.")
  }
  months <- setNames(seq_len(12L), month.abb)
  month_numbers <- vapply(parts, function(value) {
    month <- unname(months[[value[[2L]]]])
    if (is.null(month)) NA_integer_ else as.integer(month)
  }, integer(1))
  days <- vapply(parts, function(value) as.integer(value[[3L]]), integer(1))
  if (anyNA(month_numbers) || anyNA(days)) {
    cnrfc_validation_error(
      "One or more CNRFC forecast-date headings used an unrecognized month or day."
    )
  }

  year <- as.integer(format(source_updated_instant, "%Y", tz = "America/Los_Angeles"))
  source_month <- as.integer(format(source_updated_instant, "%m", tz = "America/Los_Angeles"))
  if (source_month == 12L && month_numbers[[1L]] == 1L) year <- year + 1L
  years <- integer(10L)
  rollover_count <- 0L
  for (index in seq_len(10L)) {
    if (index > 1L && month_numbers[[index]] < month_numbers[[index - 1L]]) {
      rollover_count <- rollover_count + 1L
      year <- year + 1L
    }
    years[[index]] <- year
  }
  if (rollover_count > 1L) {
    cnrfc_validation_error("CNRFC forecast-date headings contained more than one calendar-year rollover.")
  }
  dates <- as.Date(sprintf("%04d-%02d-%02d", years, month_numbers, days))
  if (anyNA(dates) || any(diff(as.numeric(dates)) <= 0)) {
    cnrfc_validation_error("CNRFC forecast-date headings were invalid, duplicated, or out of order.")
  }
  updated_date <- as.Date(format(source_updated_instant, "%Y-%m-%d", tz = "America/Los_Angeles"))
  if (dates[[1L]] < updated_date || dates[[10L]] > updated_date + 15L) {
    cnrfc_validation_error(
      "CNRFC forecast-date headings were ambiguous or implausibly distant from Data Updated time."
    )
  }
  format(dates, "%Y-%m-%d")
}

cnrfc_parse_accumulation_page <- function(html,
                                          roster_row,
                                          retrieved_at,
                                          stale_after_hours = CNRFC_ACCUMULATION_STALE_AFTER_HOURS) {
  if (nrow(roster_row) != 1L) cnrfc_stop("cnrfc_parse_accumulation_page requires exactly one roster row.")
  document <- tryCatch(
    xml2::read_html(html, options = c("RECOVER", "NOERROR", "NOWARNING")),
    error = function(e) e
  )
  if (inherits(document, "error")) cnrfc_stop("CNRFC HTML could not be parsed: ", conditionMessage(document))

  expected_title <- paste0("CNRFC - Ensemble Products - ", roster_row$nws_lid[[1L]])
  titles <- cnrfc_normalize_space(xml2::xml_text(xml2::xml_find_all(document, ".//title")))
  if (length(titles) != 1L || !identical(titles[[1L]], expected_title)) {
    cnrfc_stop("Source page title did not match expected NWS LID ", roster_row$nws_lid[[1L]], ".")
  }

  page_text <- cnrfc_normalize_space(xml2::xml_text(document))
  if (grepl(
    "Selected Ensemble Product NOT Available for this Location",
    page_text,
    fixed = TRUE
  )) {
    cnrfc_source_unavailable("CNRFC explicitly reports product 2 unavailable for this location.")
  }

  product_labels <- cnrfc_normalize_space(xml2::xml_text(xml2::xml_find_all(document, ".//i")))
  product_matches <- product_labels[product_labels == "10-Day Accumulated Volume Plot"]
  if (length(product_matches) != 1L) {
    cnrfc_stop("Expected exactly one product-2 10-Day Accumulated Volume Plot label; found ", length(product_matches), ".")
  }

  tables <- xml2::xml_find_all(document, ".//table")
  table_text <- cnrfc_normalize_space(xml2::xml_text(tables))
  accumulation_table_indices <- which(grepl(
    "Tabular 10-Day Streamflow Volume Accumulation",
    table_text,
    fixed = TRUE
  ))
  accumulation_tables <- tables[accumulation_table_indices]
  if (length(accumulation_tables) != 1L) {
    cnrfc_stop("Expected exactly one usable 10-day accumulated-volume table; found ", length(accumulation_tables), ".")
  }
  accumulation_table <- accumulation_tables[[1L]]

  heading_nodes <- xml2::xml_find_all(accumulation_table, ".//strong")
  heading_text <- cnrfc_normalize_space(xml2::xml_text(heading_nodes))
  heading_matches <- heading_text[grepl(
    "^Tabular 10-Day Streamflow Volume Accumulation",
    heading_text
  )]
  if (length(heading_matches) != 1L) {
    cnrfc_stop("Expected exactly one accumulated-volume heading with units and Data Updated time.")
  }
  heading <- heading_matches[[1L]]
  heading_parts <- regmatches(
    heading,
    regexec(
      "^Tabular 10-Day Streamflow Volume Accumulation \\(([^()]*)\\)\\s*Data Updated:\\s*(.+)$",
      heading,
      perl = TRUE
    )
  )[[1L]]
  if (length(heading_parts) != 3L) {
    cnrfc_stop("Accumulated-volume heading was missing recognizable units or Data Updated time.")
  }
  source_units <- cnrfc_normalize_space(heading_parts[[2L]])
  normalized_units <- cnrfc_normalize_accumulation_units(source_units)
  source_updated <- cnrfc_parse_data_updated_time(heading_parts[[3L]])

  rows <- xml2::xml_find_all(accumulation_table, ".//tr")
  row_cells <- lapply(rows, function(row) xml2::xml_find_all(row, "./th|./td"))
  row_labels <- vapply(row_cells, function(cells) {
    if (length(cells) == 0L) "" else cnrfc_semantic_label(xml2::xml_text(cells[[1L]]))
  }, character(1))

  header_indices <- which(row_labels == "probability")
  if (length(header_indices) != 1L) {
    cnrfc_stop("Expected exactly one Probability header row in the accumulated-volume table.")
  }
  header_cells <- row_cells[[header_indices[[1L]]]]
  if (length(header_cells) != 11L) {
    cnrfc_stop("Expected exactly ten ordered forecast-date columns; found ", length(header_cells) - 1L, ".")
  }
  date_labels <- cnrfc_normalize_space(xml2::xml_text(header_cells[-1L]))
  valid_dates <- cnrfc_parse_forecast_dates(date_labels, source_updated$instant)

  median_indices <- which(row_labels == "50median")
  if (length(median_indices) != 1L) {
    cnrfc_stop("Expected exactly one semantic 50% (Median) row; found ", length(median_indices), ".")
  }
  median_cells <- row_cells[[median_indices[[1L]]]]
  if (length(median_cells) != 11L) {
    cnrfc_stop("The 50% (Median) row did not contain exactly ten forecast values.")
  }
  median_tokens <- cnrfc_normalize_space(xml2::xml_text(median_cells[-1L]))
  median_values <- lapply(seq_along(median_tokens), function(index) {
    cnrfc_parse_accumulation_value(median_tokens[[index]], paste0("median Day ", index))
  })
  cnrfc_validate_cumulative_series(median_values, "Median")

  deterministic_indices <- which(row_labels == "cnrfcdeterministicforecast")
  if (length(deterministic_indices) > 1L) {
    cnrfc_stop("Expected at most one semantic CNRFC Deterministic Forecast row; found ", length(deterministic_indices), ".")
  }
  deterministic_tokens <- rep("", 10L)
  deterministic_values <- rep(list(NULL), 10L)
  deterministic_reason <- NULL
  if (length(deterministic_indices) == 0L) {
    deterministic_reason <- "deterministic_row_missing"
  } else {
    deterministic_cells <- row_cells[[deterministic_indices[[1L]]]]
    if (length(deterministic_cells) != 11L) {
      cnrfc_stop("The CNRFC Deterministic Forecast row did not contain exactly ten forecast cells.")
    }
    deterministic_tokens <- cnrfc_normalize_space(xml2::xml_text(deterministic_cells[-1L]))
    deterministic_values <- lapply(seq_along(deterministic_tokens), function(index) {
      cnrfc_parse_accumulation_value(
        deterministic_tokens[[index]],
        paste0("deterministic Day ", index)
      )
    })
    cnrfc_validate_cumulative_series(deterministic_values, "Deterministic")
    if (is.null(deterministic_values[[3L]])) {
      deterministic_reason <- "deterministic_day_3_unavailable"
    }
    if (is.null(deterministic_values[[5L]])) {
      deterministic_reason <- paste(
        Filter(nzchar, c(deterministic_reason, "deterministic_day_5_unavailable")),
        collapse = "; "
      )
    }
  }

  median_selected <- median_values[c(3L, 5L, 10L)]
  median_missing <- c("median_day_3_unavailable", "median_day_5_unavailable", "median_day_10_unavailable")[
    vapply(median_selected, is.null, logical(1))
  ]
  missing_reasons <- c(median_missing, deterministic_reason)
  missing_reasons <- missing_reasons[nzchar(missing_reasons)]

  issue_text <- cnrfc_extract_unique_labeled_table_value(document, "Issuance Time:")
  issue <- NULL
  if (nzchar(issue_text) && !cnrfc_is_missing_token(issue_text)) issue <- cnrfc_parse_issue_time(issue_text)

  retrieved_instant <- cnrfc_parse_utc(retrieved_at)
  if (as.numeric(difftime(source_updated$instant, retrieved_instant, units = "hours")) > 1) {
    cnrfc_validation_error("CNRFC Data Updated time was implausibly later than retrieval time.")
  }
  if (!is.null(issue) && as.numeric(difftime(issue$instant, retrieved_instant, units = "hours")) > 1) {
    cnrfc_validation_error("CNRFC forecast issue time was implausibly later than retrieval time.")
  }

  median_complete <- all(!vapply(median_selected, is.null, logical(1)))
  deterministic_complete <- !is.null(deterministic_values[[3L]]) && !is.null(deterministic_values[[5L]])
  age_hours <- as.numeric(difftime(retrieved_instant, source_updated$instant, units = "hours"))
  status <- if (!median_complete) {
    "unavailable"
  } else if (age_hours > stale_after_hours) {
    "source_stale"
  } else if (!deterministic_complete) {
    "current_partial"
  } else {
    "current"
  }

  signature_material <- paste(
    expected_title,
    product_matches[[1L]],
    heading,
    paste(date_labels, collapse = "|"),
    paste(median_tokens, collapse = "|"),
    paste(deterministic_tokens, collapse = "|"),
    issue_text,
    sep = "||"
  )

  list(
    forecast_key = roster_row$forecast_key[[1L]],
    rfc = roster_row$rfc[[1L]],
    nws_lid = roster_row$nws_lid[[1L]],
    product_type = roster_row$product_type[[1L]],
    display_name = roster_row$display_name[[1L]],
    day_3_median_volume = median_values[[3L]],
    day_3_median_valid_date = if (is.null(median_values[[3L]])) NULL else valid_dates[[3L]],
    day_5_median_volume = median_values[[5L]],
    day_5_median_valid_date = if (is.null(median_values[[5L]])) NULL else valid_dates[[5L]],
    day_10_median_volume = median_values[[10L]],
    day_10_median_valid_date = if (is.null(median_values[[10L]])) NULL else valid_dates[[10L]],
    day_3_deterministic_volume = deterministic_values[[3L]],
    day_3_deterministic_valid_date = if (is.null(deterministic_values[[3L]])) NULL else valid_dates[[3L]],
    day_5_deterministic_volume = deterministic_values[[5L]],
    day_5_deterministic_valid_date = if (is.null(deterministic_values[[5L]])) NULL else valid_dates[[5L]],
    normalized_units = normalized_units,
    source_units = source_units,
    source_data_updated_at = source_updated$iso,
    forecast_issued_at = if (is.null(issue)) NULL else issue$iso,
    status = status,
    missing_reason = if (length(missing_reasons) == 0L) NULL else paste(unique(missing_reasons), collapse = "; "),
    source_url = roster_row$source_url[[1L]],
    source_page_signature = digest::digest(signature_material, algo = "sha256", serialize = FALSE),
    diagnostic = list(
      parser = "cnrfc_prod2_semantic_v1",
      source_page_title = expected_title,
      source_product_label = product_matches[[1L]],
      expected_product_label = roster_row$expected_product_label[[1L]],
      deterministic_status = if (deterministic_complete) "available" else deterministic_reason,
      table_fingerprint = list(
        selected_table_index = as.integer(accumulation_table_indices[[1L]]),
        candidate_table_count = as.integer(length(accumulation_tables)),
        normalized_headers = as.list(date_labels),
        matched_row_labels = as.list(row_labels[nzchar(row_labels)]),
        unit_label = source_units,
        source_data_updated_at = source_updated$iso,
        forecast_issued_at = if (is.null(issue)) NULL else issue$iso,
        inferred_date_span = list(start = valid_dates[[1L]], end = valid_dates[[10L]])
      ),
      error_class = NULL,
      error_message = NULL
    )
  )
}

cnrfc_normalize_seasonal_units <- function(source_units) {
  source_units <- cnrfc_normalize_space(source_units)
  recognized <- c(
    "kaf",
    "1000s of Acre-Feet",
    "1000s Acre-Feet",
    "1000s of Ac-Ft",
    "1000s Ac-Ft"
  )
  if (!tolower(source_units) %in% tolower(recognized)) {
    cnrfc_validation_error(paste0(
      "Unrecognized CNRFC April-July volume units: ",
      if (nzchar(source_units)) source_units else "<missing>"
    ))
  }
  "kaf"
}

cnrfc_parse_seasonal_volume_token <- function(token, field_name) {
  token <- cnrfc_normalize_space(token)
  missing <- regmatches(
    token,
    regexec(
      "^(N/?A|NA|--+|UNAVAILABLE|NOT AVAILABLE|MISSING|BLANK)(?:\\s+(.*))?$",
      token,
      ignore.case = TRUE,
      perl = TRUE
    )
  )[[1L]]
  if (length(missing) > 0L) {
    units <- if (length(missing) >= 3L) cnrfc_normalize_space(missing[[3L]]) else ""
    if (nzchar(units)) cnrfc_normalize_seasonal_units(units)
    return(list(value = NULL, units = if (nzchar(units)) units else NULL))
  }
  parts <- regmatches(
    token,
    regexec("^([-+]?[0-9][0-9,]*(?:\\.[0-9]+)?)\\s+(.+)$", token, perl = TRUE)
  )[[1L]]
  if (length(parts) != 3L) {
    cnrfc_validation_error(paste0("Malformed ", field_name, " volume token: ", token))
  }
  value <- suppressWarnings(as.numeric(gsub(",", "", parts[[2L]], fixed = TRUE)))
  units <- cnrfc_normalize_space(parts[[3L]])
  if (!is.finite(value) || value < 0 || value > 1000000) {
    cnrfc_validation_error(paste0(field_name, " volume failed plausibility bounds: ", token))
  }
  cnrfc_normalize_seasonal_units(units)
  list(value = value, units = units)
}

cnrfc_parse_product7_issue <- function(text, field_name) {
  comma_after_day <- grepl("^[A-Za-z]{3} [0-9]{1,2}, ", cnrfc_normalize_space(text))
  cnrfc_parse_local_source_time(text, field_name, comma_after_day)
}

cnrfc_product7_tabular_url <- function(roster_row, water_year) {
  paste0(
    "https://www.cnrfc.noaa.gov/ensembleProductTabular.php?id=",
    roster_row$nws_lid[[1L]],
    "&prodID=7&year=",
    as.integer(water_year)
  )
}

cnrfc_parse_april_july_pages <- function(headline_html,
                                         tabular_html,
                                         roster_row,
                                         retrieved_at,
                                         stale_after_hours = CNRFC_APRIL_JULY_STALE_AFTER_HOURS) {
  if (nrow(roster_row) != 1L) {
    cnrfc_stop("cnrfc_parse_april_july_pages requires exactly one roster row.")
  }
  documents <- lapply(list(headline_html, tabular_html), function(html) {
    parsed <- tryCatch(
      xml2::read_html(html, options = c("RECOVER", "NOERROR", "NOWARNING")),
      error = function(e) e
    )
    if (inherits(parsed, "error")) {
      cnrfc_stop("CNRFC product-7 HTML could not be parsed: ", conditionMessage(parsed))
    }
    parsed
  })
  headline <- documents[[1L]]
  tabular <- documents[[2L]]
  expected_title <- paste0("CNRFC - Ensemble Products - ", roster_row$nws_lid[[1L]])
  for (document in documents) {
    titles <- cnrfc_normalize_space(xml2::xml_text(xml2::xml_find_all(document, ".//title")))
    if (length(titles) != 1L || !identical(titles[[1L]], expected_title)) {
      cnrfc_stop("Source page title did not match expected NWS LID ", roster_row$nws_lid[[1L]], ".")
    }
  }

  headline_labels <- cnrfc_normalize_space(xml2::xml_text(xml2::xml_find_all(headline, ".//i")))
  headline_matches <- grep(
    "^[0-9]{4} Seasonal Trend Plot \\(Year View\\)$",
    headline_labels,
    value = TRUE
  )
  tabular_labels <- cnrfc_normalize_space(xml2::xml_text(xml2::xml_find_all(tabular, ".//i")))
  tabular_matches <- grep(
    "^[0-9]{4} Seasonal Trend Tabular \\(Apr-Jul\\)$",
    tabular_labels,
    value = TRUE
  )
  if (length(headline_matches) != 1L || length(tabular_matches) != 1L) {
    cnrfc_stop("Expected exactly one matching product-7 headline and tabular identity label.")
  }
  water_year <- as.integer(sub(" .*", "", headline_matches[[1L]]))
  tabular_water_year <- as.integer(sub(" .*", "", tabular_matches[[1L]]))
  if (!identical(water_year, tabular_water_year)) {
    cnrfc_validation_error("CNRFC product-7 headline and tabular water years did not match.")
  }

  headline_issue_text <- cnrfc_extract_unique_labeled_table_value(headline, "Issuance Time:")
  tabular_text <- cnrfc_normalize_space(xml2::xml_text(tabular))
  explicit_unavailable <- grepl(
    "Seasonal Trend Tabular Data (April-July) Currently Unavailable",
    tabular_text,
    fixed = TRUE
  )
  if (cnrfc_is_missing_token(headline_issue_text) && explicit_unavailable) {
    cnrfc_source_unavailable(paste0(
      "CNRFC explicitly reports product 7 unavailable for ",
      roster_row$nws_lid[[1L]], " water year ", water_year, "."
    ))
  }
  if (cnrfc_is_missing_token(headline_issue_text)) {
    cnrfc_validation_error("CNRFC product-7 headline lacked an issue time without explicit unavailability.")
  }
  issue <- cnrfc_parse_product7_issue(headline_issue_text, "product-7 Issuance Time")
  tabular_issue_text <- cnrfc_extract_unique_labeled_table_value(tabular, "Issuance Time:")
  tabular_issue <- cnrfc_parse_product7_issue(tabular_issue_text, "product-7 tabular Issuance Time")
  if (as.numeric(issue$instant) != as.numeric(tabular_issue$instant)) {
    cnrfc_validation_error("CNRFC product-7 headline and tabular issue times did not match.")
  }
  retrieved_instant <- cnrfc_parse_utc(retrieved_at)
  if (as.numeric(difftime(issue$instant, retrieved_instant, units = "hours")) > 1) {
    cnrfc_validation_error("CNRFC product-7 issue time was implausibly later than retrieval time.")
  }

  scripts <- xml2::xml_text(xml2::xml_find_all(headline, ".//script"))
  summary_scripts <- scripts[grepl("Median Forecast:", scripts, fixed = TRUE)]
  headline_count <- sum(vapply(
    summary_scripts, cnrfc_fixed_count, integer(1), pattern = "Median Forecast:"
  ))
  if (length(summary_scripts) > 1L || headline_count > 1L) {
    cnrfc_validation_error(paste0(
      "Expected exactly one product-7 Median Forecast headline; found ",
      headline_count, "."
    ))
  }
  if (length(summary_scripts) != 1L || headline_count != 1L) {
    cnrfc_stop("Expected exactly one product-7 Median Forecast headline; found ", headline_count, ".")
  }
  headline_summary <- cnrfc_strip_markup(summary_scripts[[1L]])
  headline_parts <- regmatches(
    headline_summary,
    regexec(
      "Median Forecast:\\s*([^|]+?)\\s*\\|\\s*([^|]+?)\\s*of Mean\\s*\\|\\s*([^|]+?)\\s*of Median",
      headline_summary,
      perl = TRUE
    )
  )[[1L]]
  if (length(headline_parts) != 4L) {
    cnrfc_stop("The CNRFC product-7 Median Forecast headline changed.")
  }
  headline_volume <- cnrfc_parse_seasonal_volume_token(
    headline_parts[[2L]], "headline Median Forecast"
  )
  headline_percent_mean <- cnrfc_parse_percent_token(
    headline_parts[[3L]], "headline_percent_mean"
  )
  headline_percent_median <- cnrfc_parse_percent_token(
    headline_parts[[4L]], "headline_percent_median"
  )

  pre_nodes <- xml2::xml_find_all(tabular, ".//pre")
  pre_text <- xml2::xml_text(pre_nodes)
  candidate_indices <- which(
    grepl(
      paste0("Tabular Seasonal Trend Plot for ", roster_row$nws_lid[[1L]]),
      pre_text,
      fixed = TRUE
    ) & grepl("Latest Forecast April-July Percent of Average:", pre_text, fixed = TRUE)
  )
  if (length(candidate_indices) != 1L) {
    cnrfc_stop(
      "Expected exactly one product-7 candidate tabular block; found ",
      length(candidate_indices), "."
    )
  }
  selected_pre <- pre_text[[candidate_indices[[1L]]]]
  lines <- strsplit(selected_pre, "\\r?\\n", perl = TRUE)[[1L]]
  normalized_lines <- cnrfc_normalize_space(lines)
  percent_lines <- normalized_lines[grepl(
    "^# Latest Forecast April-July Percent of Average:", normalized_lines
  )]
  mean_lines <- normalized_lines[grepl(
    "^# 30 Year April-July Volume Mean:", normalized_lines
  )]
  header_lines <- normalized_lines[grepl(
    "^# Min 90% 75% 50% 25% 10% Max$", normalized_lines
  )]
  if (length(percent_lines) != 1L || length(mean_lines) != 1L || length(header_lines) != 1L) {
    cnrfc_stop("Product-7 tabular semantic labels or 50% exceedance header were duplicated or missing.")
  }
  latest_parts <- regmatches(
    percent_lines[[1L]],
    regexec(
      "^# Latest Forecast April-July Percent of Average:\\s*(.*?)\\s*%\\s*\\(\\s*(.*?)\\s*\\)$",
      percent_lines[[1L]],
      perl = TRUE
    )
  )[[1L]]
  if (length(latest_parts) != 3L) {
    cnrfc_stop("Product-7 percent-of-mean source summary changed.")
  }
  percent_average <- cnrfc_parse_percent_token(latest_parts[[2L]], "percent_average")
  tabular_summary_volume <- cnrfc_parse_seasonal_volume_token(
    latest_parts[[3L]], "tabular latest forecast"
  )
  mean_token <- sub(
    "^# 30 Year April-July Volume Mean:\\s*",
    "",
    mean_lines[[1L]],
    perl = TRUE
  )
  mean_reference_volume <- cnrfc_parse_seasonal_volume_token(
    mean_token, "mean reference volume"
  )

  data_lines <- lines[grepl("^\\s*[0-9]{2}/[0-9]{2}/[0-9]{4}\\s+", lines, perl = TRUE)]
  if (length(data_lines) == 0L) {
    cnrfc_stop("Product-7 tabular block did not contain forecast rows.")
  }
  row_tokens <- lapply(data_lines, function(line) {
    regmatches(
      line,
      regexec(
        "^\\s*([0-9]{2}/[0-9]{2}/[0-9]{4})\\s+(\\S+)\\s+(\\S+)\\s+(\\S+)\\s+(\\S+)(?:\\s|$)",
        line,
        perl = TRUE
      )
    )[[1L]]
  })
  if (any(lengths(row_tokens) != 6L)) {
    cnrfc_stop("One or more product-7 tabular forecast rows lacked the ordered 50% value.")
  }
  row_dates <- as.Date(vapply(row_tokens, `[[`, character(1), 2L), format = "%m/%d/%Y")
  if (anyNA(row_dates) || any(diff(as.numeric(row_dates)) <= 0)) {
    cnrfc_validation_error("Product-7 tabular forecast dates were invalid, duplicated, or out of order.")
  }
  last_tokens <- row_tokens[[length(row_tokens)]]
  table_50 <- cnrfc_parse_accumulation_value(last_tokens[[6L]], "product-7 latest 50% exceedance")
  issue_date <- as.Date(format(issue$instant, "%Y-%m-%d", tz = "America/Los_Angeles"))
  if (!identical(row_dates[[length(row_dates)]], issue_date)) {
    cnrfc_validation_error("Product-7 latest tabular row date did not match the source issue date.")
  }

  volume_values <- list(headline_volume$value, tabular_summary_volume$value, table_50)
  available_volumes <- unlist(Filter(Negate(is.null), volume_values), use.names = FALSE)
  if (length(available_volumes) > 1L &&
      diff(range(available_volumes)) > CNRFC_APRIL_JULY_MATCH_TOLERANCE_KAF) {
    cnrfc_validation_error("Product-7 headline and tabular 50%-exceedance volumes materially conflicted.")
  }
  if (length(available_volumes) > 0L && length(available_volumes) != 3L) {
    cnrfc_validation_error("Product-7 headline and tabular forecast-volume availability conflicted.")
  }
  percent_values <- list(headline_percent_mean$value, percent_average$value)
  available_percents <- unlist(Filter(Negate(is.null), percent_values), use.names = FALSE)
  if (length(available_percents) == 1L ||
      (length(available_percents) == 2L && abs(diff(available_percents)) > 0.01)) {
    cnrfc_validation_error("Product-7 headline and tabular percent-of-mean values conflicted.")
  }

  source_units <- Filter(
    function(value) !is.null(value) && nzchar(value),
    list(headline_volume$units, tabular_summary_volume$units, mean_reference_volume$units)
  )
  if (length(source_units) > 0L && length(unique(tolower(unlist(source_units)))) != 1L) {
    cnrfc_validation_error("Product-7 source-unit labels did not match across direct values.")
  }
  source_units <- if (length(source_units) == 0L) NULL else source_units[[1L]]
  normalized_units <- if (is.null(source_units)) NULL else cnrfc_normalize_seasonal_units(source_units)
  missing_reasons <- c(
    if (is.null(headline_volume$value)) "forecast_volume_unavailable" else NULL,
    percent_average$missing_reason,
    headline_percent_median$missing_reason,
    if (is.null(mean_reference_volume$value)) "normal_average_volume_unavailable" else NULL
  )

  signature_material <- paste(
    expected_title,
    headline_matches[[1L]],
    tabular_matches[[1L]],
    headline_issue_text,
    headline_parts[[2L]],
    headline_parts[[3L]],
    headline_parts[[4L]],
    percent_lines[[1L]],
    mean_lines[[1L]],
    last_tokens[[2L]],
    last_tokens[[6L]],
    sep = "||"
  )
  list(
    forecast_key = roster_row$forecast_key[[1L]],
    rfc = roster_row$rfc[[1L]],
    nws_lid = roster_row$nws_lid[[1L]],
    product_type = roster_row$product_type[[1L]],
    display_name = roster_row$display_name[[1L]],
    forecast_statistic = "50_percent_exceedance",
    forecast_volume = headline_volume$value,
    normalized_units = normalized_units,
    source_units = source_units,
    percent_average = percent_average$value,
    percent_median = headline_percent_median$value,
    normal_average_volume = mean_reference_volume$value,
    water_year = water_year,
    forecast_period = "April-July",
    forecast_issued_at = issue$iso,
    status = "current",
    missing_reason = if (length(missing_reasons) == 0L) NULL else paste(missing_reasons, collapse = "; "),
    source_url = roster_row$source_url[[1L]],
    source_page_signature = digest::digest(signature_material, algo = "sha256", serialize = FALSE),
    diagnostic = list(
      parser = "cnrfc_prod7_semantic_v2",
      source_page_title = expected_title,
      source_product_label = headline_matches[[1L]],
      expected_product_label = roster_row$expected_product_label[[1L]],
      tabular_source_url = cnrfc_product7_tabular_url(roster_row, water_year),
      tabular_product_label = tabular_matches[[1L]],
      headline_statistic_label = "Median Forecast",
      tabular_statistic_label = "50% Exceed",
      error_class = NULL,
      error_message = NULL
    )
  )
}

source("scripts/cnrfc_major_water_supply_basin_forecast_state.R")
