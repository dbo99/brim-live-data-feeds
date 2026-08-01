CBRFC_FORECAST_SCHEMA_VERSION <- "1.0"
CBRFC_FORECAST_PRODUCT_ID <- "cbrfc_major_water_supply_forecasts"
CBRFC_ROSTER_SCHEMA_VERSION <- "1.1.0"
CBRFC_ROSTER_VERSION <- "cbrfc-colorado-river-v1.3.0"
CBRFC_APRIL_JULY_STALE_AFTER_DAYS <- 40L
CBRFC_WATER_YEAR_STALE_AFTER_DAYS <- 40L
CBRFC_LOCAL_MONTHLY_STALE_AFTER_DAYS <- 40L
CBRFC_STALE_AFTER_DAYS <- CBRFC_APRIL_JULY_STALE_AFTER_DAYS
CBRFC_MAX_RESPONSE_BYTES <- 4L * 1024L * 1024L
CBRFC_MATCH_TOLERANCE_KAF <- 0.05
CBRFC_LOCAL_DATE_OVERRIDE_ID <- "CBRFC_LKSA3_LOCAL_JANUARY_ROLLOVER_2026"
CBRFC_LOCAL_DATE_OVERRIDE_PRIOR_ISSUE_DATE <- "2026-06-01"
CBRFC_LOCAL_DATE_OVERRIDE_EVIDENCE_URL <- paste0(
  "https://www.cbrfc.noaa.gov/product/hydrofcst/espaz_lml/wy26/",
  "lakemead.060126.csv"
)
CBRFC_LOCAL_DATE_OVERRIDE_REASON <- paste0(
  "The official 2026-07-01 source labels January as 2026 between December 2026 ",
  "and February 2027; the immediately preceding official 2026-06-01 archive ",
  "confirms January 2027."
)
CBRFC_ALLOWED_STATUSES <- c(
  "current", "current_partial", "not_yet_valid", "source_stale",
  "stale_last_known_good", "expired", "unavailable", "failed_no_data"
)
CBRFC_ALLOWED_ATTEMPT_OUTCOMES <- c(
  "success", "source_unavailable", "fetch_failed", "parse_failed", "validation_failed"
)
CBRFC_ALLOWED_FAILURE_STAGES <- c("source", "fetch", "parse", "validate")
CBRFC_USER_AGENT <- paste0(
  "BRIM-live-data-feeds/1.0 ",
  "(+https://github.com/dbo99/brim-live-data-feeds; public CBRFC forecast retrieval)"
)

cbrfc_stop <- function(...) stop(..., call. = FALSE)

cbrfc_source_unavailable <- function(message) {
  stop(structure(
    list(message = as.character(message), call = NULL),
    class = c("cbrfc_source_unavailable", "error", "condition")
  ))
}

cbrfc_validation_error <- function(...) {
  stop(structure(
    list(message = paste0(...), call = NULL),
    class = c("cbrfc_validation_error", "error", "condition")
  ))
}

cbrfc_normalize_space <- function(x) {
  x <- as.character(x)
  x <- gsub("[\r\n\t\u00a0]+", " ", x, perl = TRUE)
  trimws(gsub("[[:space:]]+", " ", x, perl = TRUE))
}

cbrfc_iso_utc <- function(x = Sys.time()) {
  format(as.POSIXct(x, tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
}

cbrfc_parse_utc <- function(value, field_name = "UTC timestamp") {
  parsed <- as.POSIXct(as.character(value), format = "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
  if (length(parsed) != 1L || is.na(parsed)) cbrfc_stop("Invalid ", field_name, ": ", value)
  parsed
}

cbrfc_expected_roster <- function() {
  data.frame(
    forecast_key = c(
      "CBRFC:GLDA3:APR_JUL_WSUP",
      "CBRFC:GLDA3:WATER_YEAR_INFLOW",
      "CBRFC:LKSA3:LOCAL_INTERVENING_MONTHLY"
    ),
    rfc = rep("CBRFC", 3L),
    nws_lid = c("GLDA3", "GLDA3", "LKSA3"),
    product_type = c(
      "april_july_water_supply_forecast",
      "water_year_unregulated_inflow_forecast",
      "lake_mead_local_intervening_monthly_forecast"
    ),
    stringsAsFactors = FALSE
  )
}

cbrfc_validate_roster <- function(roster) {
  required <- c(
    "schema_version", "forecast_key", "rfc", "nws_lid", "product_type",
    "display_name", "expected_source_name", "source_url", "retrieval_url_template",
    "summary_url", "archive_url",
    "forecast_period", "source_normal_term", "expected_forecast_type",
    "expected_units", "enabled", "display_order"
  )
  missing <- setdiff(required, names(roster))
  if (length(missing) > 0L) {
    cbrfc_stop("CBRFC roster is missing column(s): ", paste(missing, collapse = ", "))
  }
  forbidden <- grep(
    "geometry|polygon|latitude|longitude|coordinates|huc",
    names(roster),
    ignore.case = TRUE,
    value = TRUE
  )
  if (length(forbidden) > 0L) {
    cbrfc_stop("Geometry and coordinate fields are forbidden in the CBRFC roster.")
  }
  enabled <- tolower(as.character(roster$enabled)) %in% c("true", "t", "1")
  if (nrow(roster) != 3L || !all(enabled)) {
    cbrfc_stop("The reviewed CBRFC roster must contain exactly three enabled records.")
  }
  character_fields <- setdiff(required, c("enabled", "display_order"))
  for (field in character_fields) roster[[field]] <- cbrfc_normalize_space(roster[[field]])
  roster$rfc <- toupper(roster$rfc)
  roster$nws_lid <- toupper(roster$nws_lid)
  roster$display_order <- suppressWarnings(as.integer(roster$display_order))
  expected <- cbrfc_expected_roster()
  actual <- roster[, names(expected), drop = FALSE]
  rownames(actual) <- NULL
  if (!identical(actual, expected)) cbrfc_stop("CBRFC roster identity does not match the reviewed products.")
  if (!all(roster$schema_version == CBRFC_ROSTER_SCHEMA_VERSION) ||
      !identical(roster$display_order, c(10L, 20L, 30L))) {
    cbrfc_stop("CBRFC roster schema_version or display_order changed.")
  }
  expected_source_url <- c(
    "https://www.cbrfc.noaa.gov/wsup/graph/espgraph_hc.html?id=GLDA3&year={WY}",
    "https://www.cbrfc.noaa.gov/dash/lp.php",
    "https://www.cbrfc.noaa.gov/product/hydrofcst/espaz_lml/lakemead.txt"
  )
  expected_retrieval <- c(
    paste0(
      "https://www.cbrfc.noaa.gov/dbdata/station/espgraph/",
      "espgraph_data_hc.py?id=GLDA3&year={WY}"
    ),
    "https://www.cbrfc.noaa.gov/dash/lp.php",
    "https://www.cbrfc.noaa.gov/product/hydrofcst/espaz_lml/lakemead.txt"
  )
  if (!identical(roster$source_url, expected_source_url) ||
      !identical(roster$retrieval_url_template, expected_retrieval)) {
    cbrfc_stop("CBRFC roster URLs do not match the reviewed official sources.")
  }
  expected_summary <- c(
    "https://www.cbrfc.noaa.gov/dash/lp.php",
    "https://www.cbrfc.noaa.gov/dash/lp.php",
    "https://www.cbrfc.noaa.gov/product/hydrofcst/hydrofcst.php"
  )
  expected_archive <- c(
    paste0(
      "https://www.cbrfc.noaa.gov/product/hydrofcst/hydrofcst_archive.php?",
      "pdes=Upper+Basin+Reservoirs+%283+month+++Apr-Jul%29&",
      "pdir=espstr&pname=slcespstr"
    ),
    paste0(
      "https://www.cbrfc.noaa.gov/product/hydrofcst/hydrofcst_archive.php?",
      "pdes=Upper+Basin+Reservoirs+%28Water+Year%29&",
      "pdir=espstr&pname=espstr.wy.csv"
    ),
        paste0(
          "https://www.cbrfc.noaa.gov/product/hydrofcst/hydrofcst_archive.php?",
          "pdes=Lake+Mead+Local&pdir=espaz_lml&pname=lakemead.txt"
        )
  )
  if (!identical(roster$summary_url, expected_summary) ||
      !identical(roster$archive_url, expected_archive)) {
    cbrfc_stop("CBRFC supporting source URLs changed from the reviewed contract.")
  }
  role_urls <- roster[, c("source_url", "retrieval_url_template", "summary_url", "archive_url")]
  if (any(!nzchar(as.matrix(role_urls))) ||
      any(grepl("localhost|127[.]0[.]0[.]1|file:|feature/|/Users/", as.matrix(role_urls),
                ignore.case = TRUE))) {
    cbrfc_stop("CBRFC role-labeled URLs must be nonempty reviewed public URLs.")
  }
  if (!identical(roster$forecast_period, c("Apr 1-Jul 31", "Water Year", "MONTHLY OUTLOOKS")) ||
      !identical(roster$source_normal_term, c("average", "average", "median")) ||
      !identical(roster$expected_forecast_type, c(
        "Unregulated", "Unregulated", "ESP 50% EXCEEDANCE"
      )) ||
      !identical(roster$expected_units, c("kaf", "kaf", "KAF")) ||
      !identical(roster$expected_source_name, c(
        "Colorado - Lake Powell, Glen Cyn Dam, At",
        "Lake Powell Unregulated Inflow",
        "Lake Mead Local Intervening Flow"
      ))) {
    cbrfc_stop("CBRFC roster source semantics changed from the reviewed contract.")
  }
  roster
}

cbrfc_read_roster <- function(path) {
  if (!file.exists(path)) cbrfc_stop("CBRFC roster not found: ", path)
  cbrfc_validate_roster(utils::read.csv(path, stringsAsFactors = FALSE, check.names = FALSE))
}

cbrfc_fill_water_year_url <- function(template, water_year) {
  if (!grepl("{WY}", template, fixed = TRUE)) return(template)
  sub("{WY}", as.character(as.integer(water_year)), template, fixed = TRUE)
}

cbrfc_roster_row <- function(roster, product_type) {
  matches <- roster[roster$product_type == product_type, , drop = FALSE]
  if (nrow(matches) != 1L) {
    cbrfc_stop("CBRFC roster did not contain exactly one ", product_type, " record.")
  }
  matches
}

cbrfc_list_url <- function(issue_date) {
  paste0(
    "https://www.cbrfc.noaa.gov/wsup/graph/wsocond_data.py?fdate=",
    issue_date,
    "&area=UC&sort=Area&otype=csv&residual=0"
  )
}

cbrfc_current_water_year <- function(instant) {
  year <- as.integer(format(instant, "%Y", tz = "America/Los_Angeles"))
  month <- as.integer(format(instant, "%m", tz = "America/Los_Angeles"))
  if (month >= 10L) year + 1L else year
}

cbrfc_default_fetch_once <- function(url, timeout_sec, connect_timeout_sec, user_agent) {
  handle <- curl::new_handle()
  curl::handle_setopt(
    handle,
    useragent = user_agent,
    followlocation = TRUE,
    timeout = timeout_sec,
    connecttimeout = connect_timeout_sec,
    httpheader = c("Accept" = "application/json,text/csv,text/plain;q=0.9,*/*;q=0.1")
  )
  curl::curl_fetch_memory(url, handle = handle)
}

cbrfc_retryable_http_status <- function(status) {
  status %in% c(408L, 429L, 500L, 502L, 503L, 504L)
}

cbrfc_fetch_text <- function(url,
                             allowed_content_type,
                             allow_empty = FALSE,
                             timeout_sec = 30,
                             connect_timeout_sec = 10,
                             max_attempts = 3L,
                             max_response_bytes = CBRFC_MAX_RESPONSE_BYTES,
                             fetch_once = cbrfc_default_fetch_once,
                             sleep_fun = Sys.sleep) {
  max_attempts <- as.integer(max_attempts)
  if (is.na(max_attempts) || max_attempts < 1L || max_attempts > 3L) {
    cbrfc_stop("max_attempts must be between 1 and 3.")
  }
  attempts <- vector("list", max_attempts)
  for (attempt in seq_len(max_attempts)) {
    started <- Sys.time()
    response <- tryCatch(
      fetch_once(url, timeout_sec, connect_timeout_sec, CBRFC_USER_AGENT),
      error = function(e) e
    )
    elapsed <- round(as.numeric(difftime(Sys.time(), started, units = "secs")), 3)
    if (inherits(response, "error")) {
      attempts[[attempt]] <- list(
        attempt = attempt,
        outcome = "transport_error",
        message = substr(conditionMessage(response), 1L, 240L),
        elapsed_seconds = elapsed
      )
      retryable <- TRUE
    } else {
      status <- as.integer(response$status_code)
      content <- response$content
      content_type <- if (!is.null(response$type)) as.character(response$type) else ""
      response_bytes <- length(content)
      attempts[[attempt]] <- list(
        attempt = attempt,
        outcome = if (status >= 200L && status < 300L) "http_success" else "http_error",
        http_status = status,
        content_type = content_type,
        response_bytes = response_bytes,
        elapsed_seconds = elapsed
      )
      retryable <- cbrfc_retryable_http_status(status)
      if (status >= 200L && status < 300L) {
        if (!isTRUE(allow_empty) && response_bytes == 0L) {
          return(list(
            success = FALSE, failure_class = "response_empty",
            message = "CBRFC response was empty.", attempts = attempts[seq_len(attempt)]
          ))
        }
        if (response_bytes > max_response_bytes) {
          return(list(
            success = FALSE, failure_class = "response_too_large",
            message = paste0("CBRFC response exceeded ", max_response_bytes, " bytes."),
            attempts = attempts[seq_len(attempt)]
          ))
        }
        if (nzchar(content_type) && !grepl(allowed_content_type, content_type, ignore.case = TRUE)) {
          return(list(
            success = FALSE, failure_class = "unexpected_content_type",
            message = paste0("Unexpected CBRFC content type: ", content_type),
            attempts = attempts[seq_len(attempt)]
          ))
        }
        text <- rawToChar(content)
        text <- iconv(text, from = "UTF-8", to = "UTF-8", sub = "byte")
        if (is.na(text)) {
          return(list(
            success = FALSE, failure_class = "decode_failed",
            message = "CBRFC response could not be decoded as UTF-8.",
            attempts = attempts[seq_len(attempt)]
          ))
        }
        return(list(
          success = TRUE,
          text = text,
          retrieved_at = cbrfc_iso_utc(),
          attempts = attempts[seq_len(attempt)]
        ))
      }
    }
    if (!retryable || attempt == max_attempts) break
    sleep_fun(min(4, 2^(attempt - 1L)))
  }
  used <- attempts[!vapply(attempts, is.null, logical(1))]
  last <- used[[length(used)]]
  list(
    success = FALSE,
    failure_class = if (identical(last$outcome, "transport_error")) "retrieval_failed" else "http_failed",
    message = if (!is.null(last$message)) last$message else paste0("CBRFC returned HTTP ", last$http_status, "."),
    attempts = used
  )
}

cbrfc_scalar_number <- function(value, field_name) {
  if (!is.numeric(value) || length(value) != 1L || !is.finite(value) || value < 0) {
    cbrfc_validation_error("CBRFC ", field_name, " must be one finite nonnegative number.")
  }
  as.numeric(value)
}

cbrfc_parse_point_response <- function(json_text, roster_row, expected_water_year) {
  parsed <- tryCatch(
    jsonlite::fromJSON(json_text, simplifyVector = FALSE),
    error = function(e) e
  )
  if (inherits(parsed, "error") || !is.list(parsed) || is.null(names(parsed))) {
    cbrfc_stop(
      "CBRFC structured response was malformed JSON or not an object: ",
      if (inherits(parsed, "error")) conditionMessage(parsed) else "non-object response"
    )
  }
  required <- c(
    "id", "name", "wy", "offdate", "off50", "offpavg", "offpmed", "esptype", "names"
  )
  missing <- setdiff(required, names(parsed))
  if (length(missing) > 0L) {
    cbrfc_stop("CBRFC structured response is missing field(s): ", paste(missing, collapse = ", "))
  }
  if (!identical(cbrfc_normalize_space(parsed$id), roster_row$nws_lid[[1L]])) {
    cbrfc_validation_error("CBRFC response identifier did not match GLDA3.")
  }
  if (!identical(cbrfc_normalize_space(parsed$name), roster_row$expected_source_name[[1L]])) {
    cbrfc_validation_error("CBRFC response source name did not match the reviewed GLDA3 identity.")
  }
  water_year <- suppressWarnings(as.integer(parsed$wy))
  if (length(water_year) != 1L || is.na(water_year) ||
      !identical(water_year, as.integer(expected_water_year))) {
    cbrfc_validation_error("CBRFC response water year did not match the requested water year.")
  }
  if (!is.list(parsed$names) || is.null(parsed$names$units)) {
    cbrfc_validation_error("CBRFC response omitted names.units.")
  }
  source_units <- cbrfc_normalize_space(parsed$names$units)
  if (!identical(source_units, roster_row$expected_units[[1L]])) {
    cbrfc_validation_error("CBRFC response used unrecognized units: ", source_units)
  }
  forecast_type <- cbrfc_normalize_space(parsed$esptype)
  if (!identical(forecast_type, roster_row$expected_forecast_type[[1L]])) {
    cbrfc_validation_error("CBRFC response forecast type changed: ", forecast_type)
  }
  offdate <- cbrfc_normalize_space(parsed$offdate)
  if (!nzchar(offdate)) {
    sentinel_fields <- c("off50", "offpavg", "offpmed")
    sentinel_values <- vapply(sentinel_fields, function(field) {
      value <- parsed[[field]]
      is.numeric(value) && length(value) == 1L && is.finite(value) && value == 0
    }, logical(1))
    if (!all(sentinel_values)) {
      cbrfc_validation_error("CBRFC blank offdate was paired with non-sentinel official values.")
    }
    cbrfc_source_unavailable(paste0(
      "CBRFC GLDA3 has no official forecast issue for water year ", water_year,
      "; blank-date zero sentinels were not accepted as forecasts."
    ))
  }
  if (!grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", offdate)) {
    cbrfc_validation_error("CBRFC offdate was not YYYY-MM-DD.")
  }
  issue_date <- as.Date(offdate)
  if (is.na(issue_date) || as.integer(format(issue_date, "%Y")) != water_year) {
    cbrfc_validation_error("CBRFC offdate was invalid or outside the requested water year.")
  }
  forecast_volume <- cbrfc_scalar_number(parsed$off50, "off50")
  percent_average <- cbrfc_scalar_number(parsed$offpavg, "offpavg")
  percent_median <- cbrfc_scalar_number(parsed$offpmed, "offpmed")
  signature <- digest::digest(
    paste(
      parsed$id, cbrfc_normalize_space(parsed$name), water_year, offdate,
      format(forecast_volume, scientific = FALSE, trim = TRUE),
      format(percent_average, scientific = FALSE, trim = TRUE),
      format(percent_median, scientific = FALSE, trim = TRUE),
      source_units, forecast_type,
      sep = "|"
    ),
    algo = "sha256",
    serialize = FALSE
  )
  list(
    id = roster_row$nws_lid[[1L]],
    source_name = roster_row$expected_source_name[[1L]],
    water_year = water_year,
    forecast_issue_date = offdate,
    forecast_volume = forecast_volume,
    percent_average = percent_average,
    percent_median = percent_median,
    source_units = source_units,
    forecast_type = forecast_type,
    source_page_signature = signature
  )
}

cbrfc_csv_number <- function(value, field_name) {
  parsed <- suppressWarnings(as.numeric(as.character(value)))
  if (length(parsed) != 1L || !is.finite(parsed) || parsed < 0) {
    cbrfc_validation_error("CBRFC list ", field_name, " was not one finite nonnegative number.")
  }
  parsed
}

cbrfc_parse_list_crosscheck <- function(csv_text, point, roster_row, fetch_diagnostic = NULL) {
  if (is.null(csv_text)) {
    return(list(status = "fetch_failed", diagnostic = fetch_diagnostic))
  }
  if (!nzchar(trimws(csv_text))) return(list(status = "absent_for_issue_date"))
  table <- tryCatch(
    utils::read.csv(text = csv_text, stringsAsFactors = FALSE, check.names = FALSE),
    error = function(e) e
  )
  if (inherits(table, "error")) {
    cbrfc_stop("CBRFC official-list CSV could not be parsed: ", conditionMessage(table))
  }
  required <- c(
    "NWS_ID", "Forecast_Date", "Forecast_Period", "MP_50", "Avg", "Med", "Pct_Avg", "Pct_Med"
  )
  missing <- setdiff(required, names(table))
  if (length(missing) > 0L) {
    cbrfc_stop("CBRFC official-list CSV is missing field(s): ", paste(missing, collapse = ", "))
  }
  matches <- table[toupper(cbrfc_normalize_space(table$NWS_ID)) == roster_row$nws_lid[[1L]], , drop = FALSE]
  if (nrow(matches) == 0L) return(list(status = "absent_for_issue_date"))
  if (nrow(matches) != 1L) {
    cbrfc_validation_error("CBRFC official-list CSV contained duplicate GLDA3 rows.")
  }
  row <- matches[1L, , drop = FALSE]
  if (!identical(cbrfc_normalize_space(row$Forecast_Date[[1L]]), point$forecast_issue_date)) {
    cbrfc_validation_error("CBRFC official-list row date did not match point offdate.")
  }
  if (!identical(cbrfc_normalize_space(row$Forecast_Period[[1L]]), roster_row$forecast_period[[1L]])) {
    cbrfc_validation_error("CBRFC official-list forecast period did not match Apr 1-Jul 31.")
  }
  list_volume <- cbrfc_csv_number(row$MP_50[[1L]], "MP_50")
  list_percent_average <- cbrfc_csv_number(row$Pct_Avg[[1L]], "Pct_Avg")
  list_percent_median <- cbrfc_csv_number(row$Pct_Med[[1L]], "Pct_Med")
  if (abs(list_volume - point$forecast_volume) > CBRFC_MATCH_TOLERANCE_KAF ||
      abs(list_percent_average - point$percent_average) > 0.01 ||
      abs(list_percent_median - point$percent_median) > 0.01) {
    cbrfc_validation_error("CBRFC official-list values materially conflicted with the point endpoint.")
  }
  list(
    status = "matched",
    historical_average_volume = cbrfc_csv_number(row$Avg[[1L]], "Avg"),
    historical_median_volume = cbrfc_csv_number(row$Med[[1L]], "Med")
  )
}

cbrfc_html_cells <- function(row) {
  vapply(
    xml2::xml_find_all(row, "./th|./td"),
    function(cell) cbrfc_normalize_space(xml2::xml_text(cell)),
    character(1)
  )
}

cbrfc_source_number <- function(value, field_name, percent = FALSE) {
  value <- cbrfc_normalize_space(value)
  if (!nzchar(value) || value %in% c("-", "--", "NA", "N/A")) return(NULL)
  pattern <- if (percent) {
    "^[+]?[0-9]+(?:[.][0-9]+)?%$"
  } else {
    "^[+]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:[.][0-9]+)?$"
  }
  if (!grepl(pattern, value, perl = TRUE)) {
    cbrfc_validation_error("CBRFC water-year ", field_name, " was malformed: ", value)
  }
  parsed <- suppressWarnings(as.numeric(gsub("[% ,]", "", value)))
  if (length(parsed) != 1L || !is.finite(parsed) || parsed < 0) {
    cbrfc_validation_error("CBRFC water-year ", field_name, " was not finite and nonnegative.")
  }
  parsed
}

cbrfc_source_display_value <- function(value, field_name, percent = FALSE) {
  raw <- cbrfc_normalize_space(value)
  parsed <- cbrfc_source_number(raw, field_name, percent = percent)
  if (is.null(parsed)) return(list(value = NULL, raw = raw, tolerance = NULL))
  numeric_text <- gsub("[% ,]", "", raw)
  decimal_digits <- if (grepl("[.]", numeric_text)) {
    nchar(sub("^[^.]*[.]", "", numeric_text))
  } else {
    0L
  }
  list(
    value = parsed,
    raw = raw,
    tolerance = 0.5 * (10 ^ (-as.integer(decimal_digits)))
  )
}

cbrfc_extract_powell_dashboard <- function(html_text, roster_row, expected_water_year) {
  document <- tryCatch(xml2::read_html(html_text), error = function(e) e)
  if (inherits(document, "error")) {
    cbrfc_stop("CBRFC situational-awareness response was malformed HTML: ", conditionMessage(document))
  }
  body_text <- cbrfc_normalize_space(xml2::xml_text(document))
  if (!grepl("Upper Colorado Situational Awareness", body_text, fixed = TRUE)) {
    cbrfc_stop("CBRFC situational-awareness response was not the reviewed official page.")
  }
  heading_pattern <- paste0(
    "Lake Powell Unregulated Inflow \\(([^)]+)\\) ",
    "Water Year ([0-9]{4}) Forecasts as of ([0-9]{4}-[0-9]{2}-[0-9]{2})"
  )
  heading_locations <- gregexpr(heading_pattern, body_text, perl = TRUE)[[1L]]
  if (identical(heading_locations[[1L]], -1L)) {
    cbrfc_validation_error("CBRFC situational-awareness page omitted the reviewed Lake Powell heading.")
  }
  if (length(heading_locations) != 1L) {
    cbrfc_validation_error("CBRFC situational-awareness page contained duplicate Lake Powell headings.")
  }
  heading <- regmatches(body_text, regexec(heading_pattern, body_text, perl = TRUE))[[1L]]
  source_units <- heading[[2L]]
  water_year <- suppressWarnings(as.integer(heading[[3L]]))
  issue_date <- heading[[4L]]
  if (!identical(source_units, roster_row$expected_units[[1L]])) {
    cbrfc_validation_error("CBRFC water-year source used unrecognized units: ", source_units)
  }
  if (!identical(water_year, as.integer(expected_water_year))) {
    cbrfc_source_unavailable(paste0(
      "CBRFC Lake Powell situational awareness publishes water year ", water_year,
      ", not requested water year ", as.integer(expected_water_year), "."
    ))
  }
  parsed_issue <- as.Date(issue_date)
  if (is.na(parsed_issue) || as.integer(format(parsed_issue, "%Y")) != water_year) {
    cbrfc_validation_error("CBRFC water-year forecast issue date was invalid for its water year.")
  }

  tables <- xml2::xml_find_all(document, ".//table")
  candidate <- Filter(function(table) {
    rows <- xml2::xml_find_all(table, ".//tr")
    length(rows) > 0L && identical(
      cbrfc_html_cells(rows[[1L]]),
      c("Period", "Obs to Date", "Full Fcst", "%Avg")
    )
  }, as.list(tables))
  if (length(candidate) == 0L) {
    cbrfc_stop("CBRFC situational-awareness page omitted the reviewed inflow table.")
  }
  if (length(candidate) != 1L) {
    cbrfc_validation_error("CBRFC situational-awareness page contained duplicate candidate inflow tables.")
  }
  rows <- xml2::xml_find_all(candidate[[1L]], ".//tr[position()>1]")
  row_cells <- lapply(rows, cbrfc_html_cells)
  parse_semantic_row <- function(label) {
    matches <- row_cells[vapply(row_cells, function(cells) {
      length(cells) > 0L && identical(cells[[1L]], label)
    }, logical(1))]
    if (length(matches) == 0L) return(NULL)
    if (length(matches) != 1L) {
      cbrfc_validation_error(
        "CBRFC situational-awareness table contained duplicate ", label, " rows."
      )
    }
    cells <- matches[[1L]]
    if (length(cells) != 4L) {
      cbrfc_validation_error("CBRFC ", label, " row did not contain exactly four ordered cells.")
    }
    list(
      label = label,
      observed_to_date = cbrfc_source_display_value(
        cells[[2L]], paste0(label, " observed-to-date volume")
      ),
      forecast_volume = cbrfc_source_display_value(
        cells[[3L]], paste0(label, " full forecast volume")
      ),
      percent_average = cbrfc_source_display_value(
        cells[[4L]], paste0(label, " percent average"), percent = TRUE
      )
    )
  }
  list(
    source_name = roster_row$expected_source_name[[1L]],
    water_year = water_year,
    forecast_issue_date = issue_date,
    source_units = source_units,
    april_july = parse_semantic_row("Apr-Jul"),
    water_year_row = parse_semantic_row("Water Year")
  )
}

cbrfc_extract_water_year_source <- function(html_text, roster_row, expected_water_year) {
  dashboard <- cbrfc_extract_powell_dashboard(html_text, roster_row, expected_water_year)
  row <- dashboard$water_year_row
  if (is.null(row)) {
    cbrfc_source_unavailable("CBRFC situational-awareness table omitted the Water Year forecast row.")
  }
  values <- list(
    row$observed_to_date$value,
    row$forecast_volume$value,
    row$percent_average$value
  )
  if (all(vapply(values, is.null, logical(1)))) {
    cbrfc_source_unavailable("CBRFC Water Year row contained source-unavailable value sentinels.")
  }
  if (any(vapply(values, is.null, logical(1)))) {
    cbrfc_validation_error("CBRFC Water Year row contained partially missing source values.")
  }
  signature <- digest::digest(
    paste(
      roster_row$nws_lid[[1L]], roster_row$expected_source_name[[1L]], dashboard$water_year,
      dashboard$forecast_issue_date, "Water Year", row$forecast_volume$value,
      row$percent_average$value, dashboard$source_units,
      roster_row$expected_forecast_type[[1L]],
      sep = "|"
    ),
    algo = "sha256",
    serialize = FALSE
  )
  list(
    source_name = roster_row$expected_source_name[[1L]],
    water_year = dashboard$water_year,
    forecast_issue_date = dashboard$forecast_issue_date,
    observed_to_date = row$observed_to_date$value,
    forecast_volume = row$forecast_volume$value,
    percent_average = row$percent_average$value,
    source_units = dashboard$source_units,
    forecast_type = roster_row$expected_forecast_type[[1L]],
    source_page_signature = signature
  )
}

cbrfc_dashboard_crosscheck <- function(point,
                                       dashboard_html,
                                       roster_row,
                                       fetch_diagnostic = NULL) {
  if (is.null(dashboard_html)) {
    return(list(
      status = "fetch_failed",
      dashboard_issue_date = NULL,
      diagnostic = fetch_diagnostic
    ))
  }
  dashboard <- tryCatch(
    cbrfc_extract_powell_dashboard(dashboard_html, roster_row, point$water_year),
    error = function(e) e
  )
  if (inherits(dashboard, "error")) {
    return(list(
      status = "unavailable_or_unparseable",
      dashboard_issue_date = NULL,
      diagnostic = substr(cbrfc_normalize_space(conditionMessage(dashboard)), 1L, 300L)
    ))
  }
  dashboard_date <- cbrfc_parse_date(
    dashboard$forecast_issue_date, "dashboard forecast issue date"
  )
  point_date <- cbrfc_parse_date(point$forecast_issue_date, "point forecast issue date")
  if (dashboard_date < point_date) {
    return(list(
      status = "lagging",
      dashboard_issue_date = dashboard$forecast_issue_date,
      diagnostic = paste0(
        "Dashboard issue ", dashboard$forecast_issue_date,
        " lags structured point issue ", point$forecast_issue_date, "."
      )
    ))
  }
  if (dashboard_date > point_date) {
    cbrfc_validation_error(
      "CBRFC dashboard issue ", dashboard$forecast_issue_date,
      " was newer than structured point issue ", point$forecast_issue_date,
      "; neither official representation was selected silently."
    )
  }
  row <- dashboard$april_july
  if (is.null(row) || is.null(row$forecast_volume$value) ||
      is.null(row$percent_average$value)) {
    return(list(
      status = "missing_field",
      dashboard_issue_date = dashboard$forecast_issue_date,
      diagnostic = "Dashboard omitted the Apr-Jul row, full forecast, or percent-average field."
    ))
  }
  volume_difference <- abs(row$forecast_volume$value - point$forecast_volume)
  percent_difference <- abs(row$percent_average$value - point$percent_average)
  if (volume_difference > row$forecast_volume$tolerance + .Machine$double.eps ||
      percent_difference > row$percent_average$tolerance + .Machine$double.eps) {
    cbrfc_validation_error(paste0(
      "CBRFC dashboard materially conflicted with the current structured point: ",
      "volume ", row$forecast_volume$raw, " versus ", point$forecast_volume,
      "; percent average ", row$percent_average$raw, " versus ", point$percent_average, "."
    ))
  }
  list(
    status = "matched",
    dashboard_issue_date = dashboard$forecast_issue_date,
    forecast_volume_display_tolerance = row$forecast_volume$tolerance,
    percent_average_display_tolerance = row$percent_average$tolerance,
    diagnostic = NULL
  )
}

cbrfc_parse_water_year_response <- function(html_text,
                                            roster_row,
                                            retrieved_at,
                                            expected_water_year,
                                            stale_after_days = CBRFC_WATER_YEAR_STALE_AFTER_DAYS) {
  source <- cbrfc_extract_water_year_source(html_text, roster_row, expected_water_year)
  record <- list(
    forecast_key = roster_row$forecast_key[[1L]],
    rfc = roster_row$rfc[[1L]],
    nws_lid = roster_row$nws_lid[[1L]],
    product_type = roster_row$product_type[[1L]],
    display_name = roster_row$display_name[[1L]],
    forecast_statistic = "official_full_forecast",
    forecast_volume = source$forecast_volume,
    normalized_units = "kaf",
    source_units = source$source_units,
    percent_average = source$percent_average,
    forecast_issue_date = source$forecast_issue_date,
    source_time_precision = "date",
    source_normal_term = roster_row$source_normal_term[[1L]],
    forecast_period = roster_row$forecast_period[[1L]],
    water_year = source$water_year,
    forecast_type = source$forecast_type,
    status = "current",
    missing_reason = NULL,
    source_url = roster_row$source_url[[1L]],
    retrieval_url = roster_row$retrieval_url_template[[1L]],
    summary_url = roster_row$summary_url[[1L]],
    archive_url = roster_row$archive_url[[1L]],
    source_page_signature = source$source_page_signature,
    diagnostic = list(
      parser = "cbrfc_glda3_water_year_semantic_v1",
      source_name = source$source_name,
      semantic_row_label = "Water Year",
      source_forecast_label = "Full Fcst",
      source_percentage_label = "%Avg",
      primary_source_role = "server_rendered_situational_awareness_dashboard",
      summary_source_relationship = "same_url_as_retrieval_not_independent",
      observed_to_date_policy = "available_on_summary_not_published",
      error_class = NULL,
      error_message = NULL
    )
  )
  cbrfc_finalize_current_record(record, retrieved_at, stale_after_days)
}

cbrfc_parse_response <- function(point_json,
                                 list_csv,
                                 roster_row,
                                 retrieved_at,
                                 expected_water_year,
                                 list_fetch_diagnostic = NULL,
                                 stale_after_days = CBRFC_APRIL_JULY_STALE_AFTER_DAYS,
                                 dashboard_html = NULL,
                                 dashboard_fetch_diagnostic = NULL) {
  point <- cbrfc_parse_point_response(point_json, roster_row, expected_water_year)
  list_qa <- cbrfc_parse_list_crosscheck(
    list_csv, point, roster_row, list_fetch_diagnostic
  )
  dashboard_qa <- cbrfc_dashboard_crosscheck(
    point, dashboard_html, roster_row, dashboard_fetch_diagnostic
  )
  source_url <- cbrfc_fill_water_year_url(roster_row$source_url[[1L]], point$water_year)
  retrieval_url <- cbrfc_fill_water_year_url(
    roster_row$retrieval_url_template[[1L]], point$water_year
  )
  record <- list(
    forecast_key = roster_row$forecast_key[[1L]],
    rfc = roster_row$rfc[[1L]],
    nws_lid = roster_row$nws_lid[[1L]],
    product_type = roster_row$product_type[[1L]],
    display_name = roster_row$display_name[[1L]],
    forecast_statistic = "50_percent_exceedance",
    forecast_volume = point$forecast_volume,
    normalized_units = "kaf",
    source_units = point$source_units,
    percent_average = point$percent_average,
    percent_median = point$percent_median,
    forecast_issue_date = point$forecast_issue_date,
    source_time_precision = "date",
    source_normal_term = roster_row$source_normal_term[[1L]],
    forecast_period = roster_row$forecast_period[[1L]],
    water_year = point$water_year,
    forecast_type = point$forecast_type,
    status = "current",
    missing_reason = NULL,
    source_url = source_url,
    retrieval_url = retrieval_url,
    summary_url = roster_row$summary_url[[1L]],
    archive_url = roster_row$archive_url[[1L]],
    source_page_signature = point$source_page_signature,
    diagnostic = list(
      parser = "cbrfc_glda3_official_v1",
      source_name = point$source_name,
      official_field_prefix = "off",
      secondary_list_status = list_qa$status,
      secondary_list_diagnostic = list_qa$diagnostic,
      dashboard_crosscheck_status = dashboard_qa$status,
      dashboard_issue_date = dashboard_qa$dashboard_issue_date,
      dashboard_crosscheck_diagnostic = dashboard_qa$diagnostic,
      dashboard_volume_display_tolerance = dashboard_qa$forecast_volume_display_tolerance,
      dashboard_percent_average_display_tolerance = dashboard_qa$percent_average_display_tolerance,
      summary_source_relationship = "server_rendered_official_representation_not_proven_independent",
      observed_to_date_policy = "available_on_summary_not_published",
      error_class = NULL,
      error_message = NULL
    )
  )
  cbrfc_finalize_current_record(record, retrieved_at, stale_after_days)
}

cbrfc_parse_local_issue_date <- function(value) {
  value <- cbrfc_normalize_space(value)
  if (!grepl("^(?:[1-9]|1[0-2])/(?:[1-9]|[12][0-9]|3[01])/[0-9]{4}$", value)) {
    cbrfc_validation_error("CBRFC Lake Mead Local issue date was not M/D/YYYY.")
  }
  parsed <- as.Date(value, format = "%m/%d/%Y")
  if (is.na(parsed) || !identical(format(parsed, "%m/%d/%Y"), sprintf(
    "%02d/%02d/%04d",
    as.integer(strsplit(value, "/", fixed = TRUE)[[1L]][[1L]]),
    as.integer(strsplit(value, "/", fixed = TRUE)[[1L]][[2L]]),
    as.integer(strsplit(value, "/", fixed = TRUE)[[1L]][[3L]])
  ))) {
    cbrfc_validation_error("CBRFC Lake Mead Local issue date was invalid.")
  }
  format(parsed, "%Y-%m-%d")
}

cbrfc_local_number <- function(value, field_name, percent = FALSE) {
  value <- cbrfc_normalize_space(value)
  pattern <- if (percent) "^[0-9]+(?:[.][0-9]+)?%$" else "^[0-9]+(?:[.][0-9]+)?$"
  if (!grepl(pattern, value, perl = TRUE)) {
    cbrfc_validation_error("CBRFC Lake Mead Local ", field_name, " was malformed: ", value)
  }
  parsed <- suppressWarnings(as.numeric(sub("%$", "", value)))
  if (length(parsed) != 1L || !is.finite(parsed) || parsed < 0) {
    cbrfc_validation_error("CBRFC Lake Mead Local ", field_name, " was not finite and nonnegative.")
  }
  parsed
}

cbrfc_parse_local_monthly_source <- function(
    source_text,
    roster_row,
    prior_issue_text = NULL,
    override_evidence_url = CBRFC_LOCAL_DATE_OVERRIDE_EVIDENCE_URL,
    allow_reviewed_override = TRUE) {
  if (!is.character(source_text) || length(source_text) != 1L || !nzchar(trimws(source_text))) {
    cbrfc_stop("CBRFC Lake Mead Local response was empty or invalid text.")
  }
  lines <- strsplit(gsub("\r", "", source_text, fixed = TRUE), "\n", fixed = TRUE)[[1L]]
  table <- tryCatch(
    utils::read.csv(
      text = paste(lines, collapse = "\n"), header = FALSE, fill = TRUE,
      blank.lines.skip = FALSE, colClasses = "character", stringsAsFactors = FALSE,
      check.names = FALSE
    ),
    error = function(e) e
  )
  if (inherits(table, "error") || ncol(table) != 6L || nrow(table) < 19L) {
    cbrfc_stop("CBRFC Lake Mead Local response was not the reviewed six-column CSV structure.")
  }
  table[] <- lapply(table, cbrfc_normalize_space)
  scalar_row <- function(index) {
    values <- unname(unlist(table[index, , drop = FALSE], use.names = FALSE))
    if (any(nzchar(values[-1L]))) {
      cbrfc_validation_error("CBRFC Lake Mead Local preamble contained unexpected cells.")
    }
    values[[1L]]
  }
  if (!identical(scalar_row(1L), "COLORADO BASIN RIVER FORECAST CENTER") ||
      !identical(scalar_row(2L), roster_row$forecast_period[[1L]])) {
    cbrfc_validation_error("CBRFC Lake Mead Local issuer or product title changed.")
  }
  issue_date <- cbrfc_parse_local_issue_date(scalar_row(3L))
  volume_heading <- scalar_row(4L)
  expected_heading <- paste0(
    "VOLUMES (", roster_row$expected_units[[1L]], ") BASED ON ",
    roster_row$expected_forecast_type[[1L]], " VALUES"
  )
  if (!identical(volume_heading, expected_heading)) {
    cbrfc_validation_error("CBRFC Lake Mead Local units or forecast statistic changed: ", volume_heading)
  }
  headers <- unname(unlist(table[6L, , drop = FALSE], use.names = FALSE))
  expected_headers <- c(
    "Year", "Month", roster_row$expected_source_name[[1L]], "%Med",
    "Paria Creek nr Lees Ferry", "%Med"
  )
  if (!identical(headers, expected_headers)) {
    cbrfc_validation_error("CBRFC Lake Mead Local ordered forecast headers changed.")
  }
  after_header <- seq.int(7L, nrow(table))
  blank_rows <- after_header[vapply(after_header, function(index) {
    !any(nzchar(unname(unlist(table[index, , drop = FALSE], use.names = FALSE))))
  }, logical(1))]
  if (length(blank_rows) == 0L) {
    cbrfc_validation_error("CBRFC Lake Mead Local monthly section had no terminator.")
  }
  monthly_end <- blank_rows[[1L]] - 1L
  if (monthly_end < 7L || monthly_end - 6L != 12L) {
    cbrfc_validation_error("CBRFC Lake Mead Local monthly section must contain exactly 12 rows.")
  }
  month_names <- month.name
  raw_monthly <- lapply(seq.int(7L, monthly_end), function(index) {
    cells <- unname(unlist(table[index, , drop = FALSE], use.names = FALSE))
    year <- suppressWarnings(as.integer(cells[[1L]]))
    month <- match(cells[[2L]], month_names)
    if (is.na(year) || year < 1900L || year > 2200L || is.na(month)) {
      cbrfc_validation_error("CBRFC Lake Mead Local monthly row had an invalid year or month.")
    }
    list(
      raw_year = year,
      month_number = as.integer(month),
      month_name = cells[[2L]],
      raw_forecast_month_label = paste(cells[[2L]], cells[[1L]]),
      forecast_volume = cbrfc_local_number(cells[[3L]], "forecast volume"),
      percent_median = cbrfc_local_number(cells[[4L]], "percent median", percent = TRUE)
    )
  })
  issue_month <- as.Date(paste0(substr(issue_date, 1L, 7L), "-01"))
  expected_dates <- seq(issue_month, by = "month", length.out = 12L)
  expected_months <- format(expected_dates, "%Y-%m")
  expected_month_names <- format(expected_dates, "%B")
  actual_month_names <- vapply(raw_monthly, `[[`, character(1), "month_name")
  if (!identical(actual_month_names, expected_month_names)) {
    cbrfc_validation_error(paste0(
      "CBRFC Lake Mead Local month names were not the exact ordered 12-month sequence ",
      "beginning with the source issue month."
    ))
  }
  raw_months <- vapply(raw_monthly, function(item) {
    sprintf("%04d-%02d", item$raw_year, item$month_number)
  }, character(1))
  mismatch <- which(raw_months != expected_months)
  override_index <- integer()
  if (length(mismatch) > 0L) {
    reviewed_identity <- identical(
      roster_row$forecast_key[[1L]], "CBRFC:LKSA3:LOCAL_INTERVENING_MONTHLY"
    ) && identical(
      roster_row$product_type[[1L]], "lake_mead_local_intervening_monthly_forecast"
    )
    reviewed_case <- isTRUE(allow_reviewed_override) && reviewed_identity &&
      identical(issue_date, "2026-07-01") && length(mismatch) == 1L &&
      identical(as.integer(mismatch), 7L) &&
      identical(raw_monthly[[7L]]$raw_forecast_month_label, "January 2026") &&
      identical(expected_months[[7L]], "2027-01")
    if (!reviewed_case) {
      cbrfc_validation_error(paste0(
        "CBRFC Lake Mead Local months were not 12 consecutive issue-month forecasts; expected ",
        paste(expected_months, collapse = ","), " but received ",
        paste(raw_months, collapse = ","), "."
      ))
    }
    if (is.null(prior_issue_text)) {
      cbrfc_validation_error(
        "CBRFC reviewed January correction requires the immediately preceding official issue."
      )
    }
    if (!identical(override_evidence_url, CBRFC_LOCAL_DATE_OVERRIDE_EVIDENCE_URL)) {
      cbrfc_validation_error("CBRFC reviewed January correction evidence URL changed.")
    }
    prior <- cbrfc_parse_local_monthly_source(
      prior_issue_text,
      roster_row,
      prior_issue_text = NULL,
      override_evidence_url = override_evidence_url,
      allow_reviewed_override = FALSE
    )
    prior_months <- vapply(prior$monthly_forecasts, `[[`, character(1), "forecast_month")
    prior_january <- which(prior_months == "2027-01")
    if (!identical(prior$forecast_issue_date, CBRFC_LOCAL_DATE_OVERRIDE_PRIOR_ISSUE_DATE) ||
        length(prior_january) != 1L ||
        !identical(
          prior$monthly_forecasts[[prior_january]]$raw_forecast_month_label,
          "January 2027"
        )) {
      cbrfc_validation_error(
        "CBRFC immediately preceding official issue did not uniquely confirm January 2027."
      )
    }
    corrected_dates <- as.Date(paste0(expected_months, "-01"))
    if (any(diff(as.integer(corrected_dates)) < 28L) ||
        length(unique(expected_months)) != 12L) {
      cbrfc_validation_error("CBRFC reviewed January correction was not uniquely monotonic.")
    }
    override_index <- mismatch
  }
  monthly <- lapply(seq_along(raw_monthly), function(index) {
    item <- raw_monthly[[index]]
    applied <- index %in% override_index
    list(
      raw_forecast_month_label = item$raw_forecast_month_label,
      forecast_month = expected_months[[index]],
      source_date_override_applied = applied,
      source_date_override_id = if (applied) CBRFC_LOCAL_DATE_OVERRIDE_ID else NULL,
      source_date_override_reason = if (applied) CBRFC_LOCAL_DATE_OVERRIDE_REASON else NULL,
      source_date_override_evidence_url = if (applied) override_evidence_url else NULL,
      source_date_override_prior_issue_date = if (applied) {
        CBRFC_LOCAL_DATE_OVERRIDE_PRIOR_ISSUE_DATE
      } else {
        NULL
      },
      forecast_volume = item$forecast_volume,
      percent_median = item$percent_median,
      source_statistic = roster_row$expected_forecast_type[[1L]],
      source_percentage_label = "%Med"
    )
  })
  identifier_lines <- lines[grepl(
    "^LKSA3 QCMPLCM OCT-SEP total average:", trimws(lines), perl = TRUE
  )]
  if (length(identifier_lines) != 1L) {
    cbrfc_validation_error("CBRFC Lake Mead Local source identifier line was missing or ambiguous.")
  }
  source_identifier <- "LKSA3 QCMPLCM"
  water_year <- as.integer(format(issue_month, "%Y")) +
    as.integer(as.integer(format(issue_month, "%m")) >= 10L)
  signature_parts <- vapply(monthly, function(item) paste(
    item$raw_forecast_month_label,
    item$forecast_month,
    if (item$source_date_override_applied) item$source_date_override_id else "none",
    format(item$forecast_volume, scientific = FALSE, trim = TRUE),
    format(item$percent_median, scientific = FALSE, trim = TRUE),
    sep = "/"
  ), character(1))
  list(
    source_name = roster_row$expected_source_name[[1L]],
    source_identifier = source_identifier,
    forecast_issue_date = issue_date,
    water_year = as.integer(water_year),
    source_units = roster_row$expected_units[[1L]],
    forecast_type = roster_row$expected_forecast_type[[1L]],
    monthly_forecasts = monthly,
    source_page_signature = digest::digest(
      paste(
        roster_row$forecast_key[[1L]], issue_date, source_identifier,
        roster_row$expected_units[[1L]], roster_row$expected_forecast_type[[1L]],
        paste(signature_parts, collapse = "|"), sep = "|"
      ),
      algo = "sha256", serialize = FALSE
    )
  )
}

cbrfc_parse_local_monthly_response <- function(source_text,
                                               roster_row,
                                               retrieved_at,
                                               stale_after_days = CBRFC_LOCAL_MONTHLY_STALE_AFTER_DAYS,
                                               prior_issue_text = NULL,
                                               override_evidence_url = CBRFC_LOCAL_DATE_OVERRIDE_EVIDENCE_URL) {
  source <- cbrfc_parse_local_monthly_source(
    source_text,
    roster_row,
    prior_issue_text = prior_issue_text,
    override_evidence_url = override_evidence_url
  )
  record <- list(
    forecast_key = roster_row$forecast_key[[1L]],
    rfc = roster_row$rfc[[1L]],
    nws_lid = roster_row$nws_lid[[1L]],
    source_identifier = source$source_identifier,
    product_type = roster_row$product_type[[1L]],
    display_name = roster_row$display_name[[1L]],
    forecast_statistic = "50_percent_exceedance",
    normalized_units = "kaf",
    source_units = source$source_units,
    forecast_issue_date = source$forecast_issue_date,
    source_time_precision = "date",
    source_normal_term = roster_row$source_normal_term[[1L]],
    forecast_period = roster_row$forecast_period[[1L]],
    water_year = source$water_year,
    forecast_type = source$forecast_type,
    monthly_forecasts = source$monthly_forecasts,
    status = "current",
    missing_reason = NULL,
    source_url = roster_row$source_url[[1L]],
    retrieval_url = roster_row$retrieval_url_template[[1L]],
    summary_url = roster_row$summary_url[[1L]],
    archive_url = roster_row$archive_url[[1L]],
    source_page_signature = source$source_page_signature,
    diagnostic = list(
      parser = "cbrfc_lksa3_local_monthly_semantic_v1",
      source_name = source$source_name,
      source_identifier = source$source_identifier,
      source_forecast_label = "ESP 50% EXCEEDANCE",
      source_percentage_label = "%Med",
      source_date_override_count = as.integer(sum(vapply(
        source$monthly_forecasts, `[[`, logical(1), "source_date_override_applied"
      ))),
      source_date_override_ids = unique(Filter(
        Negate(is.null),
        lapply(source$monthly_forecasts, `[[`, "source_date_override_id")
      )),
      error_class = NULL,
      error_message = NULL
    )
  )
  cbrfc_finalize_monthly_record(record, retrieved_at, stale_after_days)
}

source("scripts/cbrfc_major_water_supply_forecast_state.R")
