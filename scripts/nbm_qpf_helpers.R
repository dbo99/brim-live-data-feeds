# R helpers for the offline BRIM NBM QPF complete-cycle candidate producer.
#
# This module intentionally contains no Git or publication logic. Candidate
# roots are build artifacts and are never written to canonical docs/data.

QPF_PRODUCT_ID <- "nbm_qpf"
QPF_SOURCE_ID <- "noaa_nbm_core_conus_apcp"
QPF_SUPPORTED_LEADS <- seq.int(6L, 240L, by = 6L)
QPF_PRIMARY_CYCLE_HOURS <- c(0L, 6L, 12L, 18L)
QPF_ACCUMULATION_HOURS <- 6L
QPF_BOUNDS_WGS84 <- c(west = -130, south = 30, east = -112, north = 44.5)
QPF_EXTENT_3857 <- c(
  xmin = -14471533.803125564,
  ymin = 3503549.843504374,
  xmax = -12467782.96884664,
  ymax = 5543147.203861799
)
QPF_IMAGE_WIDTH <- 720L
QPF_IMAGE_HEIGHT <- 733L
QPF_GRID_CONTRACT_ID <- "nbm_qpf_lossless_webp_v1"
QPF_PIXEL_SIZE_M <- c(x = 2782.9872698322874, y = 2782.533915903717)
QPF_SOURCE_COLUMNS <- 2345L
QPF_SOURCE_ROWS <- 1597L
QPF_SOURCE_CRS <- paste(
  "+proj=lcc +lat_0=25 +lon_0=-95 +lat_1=25 +lat_2=25",
  "+x_0=0 +y_0=0 +R=6371200 +units=m +no_defs"
)
QPF_PALETTE_ID <- "brim_nbm_qpf_6h_west_v1"
QPF_PALETTE_VERSION <- 1L
QPF_STANDARD_CAPS_IN <- c(3, 4, 6, 8, 10, 12, 15, 20)
QPF_MINIMUM_CAP_IN <- 3
QPF_MIN_FINITE_COVERAGE <- 0.99
QPF_NEGATIVE_TOLERANCE_MM <- 1e-6
QPF_MAX_INDEX_BYTES <- 5 * 1024 * 1024
QPF_MAX_RANGE_BYTES <- 20 * 1024 * 1024
QPF_TRANSIENT_HTTP_STATUS <- c(408L, 429L, 500L, 502L, 503L, 504L)
QPF_ASSET_ROOT <- "docs/data/nbm-qpf/nbm/qpf"
QPF_NUMERIC_ENCODING <- "uint16_le"
QPF_NUMERIC_COMPRESSION <- "gzip"
QPF_NUMERIC_SCALE <- 0.001
QPF_NUMERIC_OFFSET <- 0
QPF_NUMERIC_NODATA <- 65535L
QPF_NUMERIC_VALID_MAX <- 65534L
QPF_NUMERIC_UNCOMPRESSED_BYTES <- QPF_IMAGE_WIDTH * QPF_IMAGE_HEIGHT * 2L
QPF_FORECAST_STATE_BINDING <- "nbm_qpf_forecast_state_sha256_v1"
QPF_CANDIDATE_MANIFEST <- "nbm_qpf_candidate.json"
QPF_VALIDATION_FILE <- "validation.json"
QPF_PREFLIGHT_FILE <- "preflight.csv"

`%||%` <- function(x, y) {
  if (is.null(x) || length(x) == 0L || all(is.na(x))) y else x
}

qpf_stop <- function(class, ..., call. = FALSE) {
  condition <- structure(
    list(message = paste0(...), call = NULL),
    class = c(class, "nbm_qpf_error", "error", "condition")
  )
  stop(condition)
}

qpf_as_utc <- function(value) {
  if (inherits(value, "POSIXt")) {
    return(as.POSIXct(as.numeric(value), origin = "1970-01-01", tz = "UTC"))
  }
  if (is.numeric(value)) {
    return(as.POSIXct(value, origin = "1970-01-01", tz = "UTC"))
  }
  parsed <- as.POSIXct(value, format = "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
  if (is.na(parsed)) parsed <- as.POSIXct(value, tz = "UTC")
  parsed
}

qpf_iso_utc <- function(value) {
  format(qpf_as_utc(value), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
}

qpf_validate_cycle <- function(value) {
  cycle <- qpf_as_utc(value)
  if (length(cycle) != 1L || is.na(cycle) ||
      !as.integer(format(cycle, "%H", tz = "UTC")) %in% QPF_PRIMARY_CYCLE_HOURS ||
      format(cycle, "%M:%S", tz = "UTC") != "00:00") {
    qpf_stop("validation_failed", "NBM QPF cycle must be a canonical 00/06/12/18 UTC hour.")
  }
  cycle
}

qpf_cycle_token <- function(value, filename = FALSE) {
  format(
    qpf_validate_cycle(value),
    if (isTRUE(filename)) "%Y%m%dT%H%M%SZ" else "%Y%m%d%H",
    tz = "UTC"
  )
}

qpf_floor_cycle <- function(now) {
  now <- qpf_as_utc(now)
  day <- as.POSIXct(format(now, "%Y-%m-%d 00:00:00", tz = "UTC"), tz = "UTC")
  candidates <- day + QPF_PRIMARY_CYCLE_HOURS * 3600
  candidates <- candidates[candidates <= now]
  if (length(candidates)) max(candidates) else day - 6 * 3600
}

qpf_candidate_cycles <- function(now, lookback_hours = 36) {
  newest <- qpf_floor_cycle(now)
  count <- floor(as.numeric(lookback_hours) / 6) + 1L
  newest - seq.int(0L, count - 1L) * 6L * 3600
}

qpf_nbm_urls <- function(cycle, lead_hour) {
  cycle <- qpf_validate_cycle(cycle)
  lead_hour <- as.integer(lead_hour)
  date <- format(cycle, "%Y%m%d", tz = "UTC")
  hour <- format(cycle, "%H", tz = "UTC")
  filename <- sprintf("blend.t%sz.core.f%03d.co.grib2", hour, lead_hour)
  base <- sprintf(
    "https://noaa-nbm-grib2-pds.s3.amazonaws.com/blend.%s/%s/core/%s",
    date, hour, filename
  )
  list(grib = base, index = paste0(base, ".idx"))
}

qpf_sha256_file <- function(path) {
  digest::digest(file = path, algo = "sha256")
}

qpf_sha256_raw <- function(value) {
  digest::digest(value, algo = "sha256", serialize = FALSE)
}

qpf_classify_http_status <- function(status) {
  status <- as.integer(status)
  if (status >= 200L && status < 300L) return("ok")
  if (status %in% c(403L, 404L)) return("source_unavailable")
  if (status %in% QPF_TRANSIENT_HTTP_STATUS) return("fetch_failed_transient")
  "fetch_failed_permanent"
}

qpf_retry_delay <- function(response, attempt, jitterer = function(max_seconds) {
  stats::runif(1L, min = 0, max = max_seconds)
}) {
  if (!is.null(response) && !inherits(response, "error")) {
    retry_after <- httr2::resp_header(response, "retry-after")
    if (!is.null(retry_after) && grepl("^[0-9]+(?:\\.[0-9]+)?$", retry_after)) {
      return(min(as.numeric(retry_after), 30))
    }
  }
  min(2 ^ (as.integer(attempt) - 1L) + max(0, jitterer(0.25)), 5)
}

qpf_http_request <- function(
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
      classification <- qpf_classify_http_status(status)
      if (identical(classification, "ok")) return(response)
      if (!identical(classification, "fetch_failed_transient") || attempt == max_tries) {
        qpf_stop(classification, "HTTP ", status, " after ", attempt, " attempt(s).")
      }
      last_error <- paste0("HTTP ", status)
    } else {
      last_error <- conditionMessage(response)
      if (attempt == max_tries) {
        qpf_stop(
          "fetch_failed_transient", "Transport failure after ", attempt,
          " attempt(s): ", last_error
        )
      }
    }
    sleeper(qpf_retry_delay(response, attempt, jitterer))
  }
  qpf_stop("fetch_failed_transient", "Request failed: ", last_error)
}

qpf_fetch_inventory <- function(url, timeout_seconds = 120, max_tries = 3L) {
  request <- httr2::request(url) |>
    httr2::req_user_agent("BRIM-NBM-QPF-Candidate/1.0") |>
    httr2::req_timeout(timeout_seconds) |>
    httr2::req_error(is_error = function(response) FALSE)
  response <- qpf_http_request(request, max_tries)
  body <- httr2::resp_body_raw(response)
  if (!length(body) || length(body) > QPF_MAX_INDEX_BYTES) {
    qpf_stop("validation_failed", "NBM inventory response size is empty or exceeds the limit.")
  }
  list(
    text = rawToChar(body),
    bytes = length(body),
    sha256 = qpf_sha256_raw(body)
  )
}

qpf_fetch_object_size <- function(url, timeout_seconds = 120, max_tries = 3L) {
  request <- httr2::request(url) |>
    httr2::req_method("HEAD") |>
    httr2::req_user_agent("BRIM-NBM-QPF-Candidate/1.0") |>
    httr2::req_timeout(timeout_seconds) |>
    httr2::req_error(is_error = function(response) FALSE)
  response <- qpf_http_request(request, max_tries)
  value <- suppressWarnings(as.numeric(httr2::resp_header(response, "content-length")))
  if (length(value) != 1L || !is.finite(value) || value < 1 || value != floor(value)) {
    qpf_stop("validation_failed", "NBM GRIB object has no valid Content-Length.")
  }
  value
}

qpf_parse_index <- function(text, cycle, lead_hour, object_size = NULL) {
  cycle <- qpf_validate_cycle(cycle)
  lead_hour <- as.integer(lead_hour)
  if (length(lead_hour) != 1L || is.na(lead_hour) || lead_hour < QPF_ACCUMULATION_HOURS) {
    qpf_stop("validation_failed", "Invalid QPF lead hour.")
  }
  lines <- strsplit(as.character(text), "\n", fixed = TRUE)[[1L]]
  lines <- sub("\r$", "", lines)
  lines <- lines[nzchar(lines)]
  if (!length(lines) || any(nchar(lines, type = "bytes") > 4096L)) {
    qpf_stop("validation_failed", "NBM inventory is empty or contains an oversized line.")
  }
  parts <- strsplit(lines, ":", fixed = TRUE)
  valid_prefix <- vapply(parts, function(fields) {
    length(fields) >= 2L && grepl("^[0-9]+$", fields[[1L]]) &&
      grepl("^[0-9]+$", fields[[2L]])
  }, logical(1))
  if (!all(valid_prefix)) qpf_stop("validation_failed", "NBM inventory has a malformed record prefix.")
  offsets <- suppressWarnings(vapply(parts, function(fields) as.numeric(fields[[2L]]), numeric(1)))
  if (any(!is.finite(offsets)) || any(offsets < 0) || any(diff(offsets) <= 0)) {
    qpf_stop("validation_failed", "NBM inventory byte offsets are not strictly increasing.")
  }

  start_lead <- lead_hour - QPF_ACCUMULATION_HOURS
  expected_cycle <- paste0("d=", qpf_cycle_token(cycle))
  expected_interval <- sprintf("%d-%d hour acc fcst", start_lead, lead_hour)
  matches <- vapply(parts, function(fields) {
    length(fields) == 6L &&
      identical(fields[[3L]], expected_cycle) &&
      identical(fields[[4L]], "APCP") &&
      identical(fields[[5L]], "surface") &&
      identical(fields[[6L]], expected_interval)
  }, logical(1))
  variable_present <- any(vapply(parts, function(fields) {
    length(fields) >= 5L && identical(fields[[4L]], "APCP") && identical(fields[[5L]], "surface")
  }, logical(1)))
  positions <- which(matches)
  if (!variable_present) {
    qpf_stop("variable_missing", "The source inventory does not contain surface APCP.")
  }
  if (length(positions) != 1L) {
    qpf_stop(
      "validation_failed", "Expected one deterministic APCP record for ",
      expected_cycle, " f", sprintf("%03d", lead_hour), " interval ",
      expected_interval, "; found ", length(positions), "."
    )
  }
  position <- positions[[1L]]
  start_byte <- offsets[[position]]
  if (position < length(lines)) {
    end_byte <- offsets[[position + 1L]] - 1
  } else {
    if (is.null(object_size)) {
      qpf_stop("validation_failed", "Last inventory record requires the GRIB object size.")
    }
    end_byte <- as.numeric(object_size) - 1
  }
  expected_bytes <- end_byte - start_byte + 1
  if (!is.finite(end_byte) || end_byte < start_byte || expected_bytes < 1 ||
      expected_bytes > QPF_MAX_RANGE_BYTES) {
    qpf_stop("validation_failed", "Selected APCP byte range is invalid or exceeds the limit.")
  }
  valid <- cycle + lead_hour * 3600
  accumulation_start <- valid - QPF_ACCUMULATION_HOURS * 3600
  list(
    record_number = as.integer(parts[[position]][[1L]]),
    start_byte = unname(start_byte),
    end_byte = unname(end_byte),
    expected_bytes = unname(expected_bytes),
    cycle_utc = qpf_iso_utc(cycle),
    lead_hours = lead_hour,
    lead_end_hours = lead_hour,
    accumulation_start_lead_hours = start_lead,
    accumulation_start_utc = qpf_iso_utc(accumulation_start),
    accumulation_end_utc = qpf_iso_utc(valid),
    valid_time_utc = qpf_iso_utc(valid),
    accumulation_hours = QPF_ACCUMULATION_HOURS,
    inventory_semantics = expected_interval,
    inventory_line = lines[[position]]
  )
}

qpf_validate_preflight <- function(records) {
  if (!is.list(records) || length(records) != length(QPF_SUPPORTED_LEADS)) {
    qpf_stop(
      "validation_failed", "QPF preflight must contain exactly ",
      length(QPF_SUPPORTED_LEADS), " records."
    )
  }
  leads <- vapply(records, function(record) as.integer(record$lead_hours), integer(1))
  if (anyDuplicated(leads) || !identical(sort(leads), QPF_SUPPORTED_LEADS)) {
    qpf_stop("validation_failed", "QPF preflight lead set is incomplete, duplicated, or unexpected.")
  }
  cycles <- unique(vapply(records, `[[`, character(1), "cycle_utc"))
  if (length(cycles) != 1L) qpf_stop("validation_failed", "QPF preflight mixes source cycles.")
  for (record in records) {
    cycle <- qpf_validate_cycle(record$cycle_utc)
    lead <- as.integer(record$lead_hours)
    expected_end <- cycle + lead * 3600
    expected_start <- expected_end - QPF_ACCUMULATION_HOURS * 3600
    if (as.integer(record$accumulation_hours) != QPF_ACCUMULATION_HOURS ||
        !identical(record$accumulation_end_utc, qpf_iso_utc(expected_end)) ||
        !identical(record$valid_time_utc, qpf_iso_utc(expected_end)) ||
        !identical(record$accumulation_start_utc, qpf_iso_utc(expected_start)) ||
        !identical(as.integer(record$lead_end_hours), lead) ||
        !is.character(record$inventory_sha256) ||
        !grepl("^[0-9a-f]{64}$", record$inventory_sha256)) {
      qpf_stop("validation_failed", "QPF preflight temporal or inventory metadata are invalid.")
    }
  }
  records[order(leads)]
}

qpf_preflight_cycle <- function(
    cycle,
    fetch_inventory = qpf_fetch_inventory,
    fetch_object_size = qpf_fetch_object_size
) {
  cycle <- qpf_validate_cycle(cycle)
  records <- list()
  for (lead in QPF_SUPPORTED_LEADS) {
    urls <- qpf_nbm_urls(cycle, lead)
    inventory <- fetch_inventory(urls$index)
    if (is.character(inventory)) {
      raw <- charToRaw(inventory)
      inventory <- list(text = inventory, bytes = length(raw), sha256 = qpf_sha256_raw(raw))
    }
    record <- tryCatch(
      qpf_parse_index(inventory$text, cycle, lead),
      error = function(error) error
    )
    if (inherits(record, "error") &&
        grepl("Last inventory record requires", conditionMessage(record), fixed = TRUE)) {
      record <- qpf_parse_index(
        inventory$text, cycle, lead, object_size = fetch_object_size(urls$grib)
      )
    } else if (inherits(record, "error")) {
      stop(record)
    }
    record$grib_url <- urls$grib
    record$index_url <- urls$index
    record$inventory_bytes <- as.numeric(inventory$bytes)
    record$inventory_sha256 <- as.character(inventory$sha256)
    records[[length(records) + 1L]] <- record
  }
  qpf_validate_preflight(records)
}

qpf_discover_cycle <- function(
    now = Sys.time(),
    lookback_hours = 36,
    explicit_cycle = NULL,
    fetch_inventory = qpf_fetch_inventory,
    fetch_object_size = qpf_fetch_object_size
) {
  candidates <- if (!is.null(explicit_cycle) && nzchar(explicit_cycle)) {
    qpf_validate_cycle(explicit_cycle)
  } else {
    qpf_candidate_cycles(now, lookback_hours)
  }
  attempts <- list()
  for (cycle in candidates) {
    result <- tryCatch(
      qpf_preflight_cycle(cycle, fetch_inventory, fetch_object_size),
      error = function(error) error
    )
    if (!inherits(result, "error")) {
      attempts[[length(attempts) + 1L]] <- list(
        cycle_utc = qpf_iso_utc(cycle), complete = TRUE, outcome = "success", reason = NULL
      )
      return(list(cycle = qpf_validate_cycle(cycle), records = result, attempts = attempts))
    }
    attempts[[length(attempts) + 1L]] <- list(
      cycle_utc = qpf_iso_utc(cycle), complete = FALSE,
      outcome = class(result)[[1L]], reason = conditionMessage(result)
    )
    if (!is.null(explicit_cycle) && nzchar(explicit_cycle)) {
      attr(result, "attempts") <- attempts
      stop(result)
    }
    if (!any(c("source_unavailable", "variable_missing") %in% class(result))) {
      attr(result, "attempts") <- attempts
      stop(result)
    }
  }
  condition <- structure(
    list(
      message = "No complete NBM QPF cycle was found within the configured lookback.",
      call = NULL,
      attempts = attempts
    ),
    class = c("source_unavailable", "nbm_qpf_error", "error", "condition")
  )
  stop(condition)
}

qpf_validate_range_response <- function(response, start_byte, end_byte) {
  expected_length <- as.numeric(end_byte - start_byte + 1)
  status <- httr2::resp_status(response)
  if (status != 206L) {
    qpf_stop("validation_failed", "Range request returned HTTP ", status, ".")
  }
  content_range <- httr2::resp_header(response, "content-range")
  expected_prefix <- sprintf("bytes %d-%d/", start_byte, end_byte)
  if (is.null(content_range) || !startsWith(content_range, expected_prefix)) {
    qpf_stop(
      "validation_failed", "Unexpected Content-Range response: ",
      content_range %||% "<missing>"
    )
  }
  body <- httr2::resp_body_raw(response)
  if (length(body) != expected_length || length(body) > QPF_MAX_RANGE_BYTES) {
    qpf_stop(
      "validation_failed", "Range response length mismatch: expected ",
      expected_length, ", received ", length(body), "."
    )
  }
  body
}

qpf_fetch_range <- function(record, path, timeout_seconds = 120, max_tries = 3L) {
  request <- httr2::request(record$grib_url) |>
    httr2::req_user_agent("BRIM-NBM-QPF-Candidate/1.0") |>
    httr2::req_headers(Range = sprintf("bytes=%d-%d", record$start_byte, record$end_byte)) |>
    httr2::req_timeout(timeout_seconds) |>
    httr2::req_error(is_error = function(response) FALSE)
  response <- qpf_http_request(request, max_tries)
  body <- qpf_validate_range_response(response, record$start_byte, record$end_byte)
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  writeBin(body, path)
  invisible(path)
}

qpf_acquire_sources <- function(records, root, fetch_range = qpf_fetch_range) {
  records <- qpf_validate_preflight(records)
  dir.create(root, recursive = TRUE, showWarnings = FALSE)
  sources <- list()
  for (record in records) {
    path <- file.path(root, sprintf("f%03d_apcp_%d-%d.grib2",
                                   record$lead_hours,
                                   record$accumulation_start_lead_hours,
                                   record$lead_hours))
    fetch_range(record, path)
    bytes <- unname(file.info(path)$size)
    if (!is.finite(bytes) || bytes != record$expected_bytes) {
      qpf_stop("validation_failed", "Downloaded APCP range byte count is invalid.")
    }
    sources[[as.character(record$lead_hours)]] <- list(
      path = path,
      bytes = bytes,
      sha256 = qpf_sha256_file(path)
    )
  }
  if (!identical(sort(as.integer(names(sources))), QPF_SUPPORTED_LEADS)) {
    qpf_stop("validation_failed", "Acquired QPF source set is incomplete.")
  }
  sources
}

qpf_decode_grib <- function(path, reader = terra::rast) {
  tryCatch(
    reader(path),
    error = function(error) {
      qpf_stop("decode_failed", "APCP GRIB decode failed: ", conditionMessage(error))
    }
  )
}

qpf_parse_grib_metadata <- function(lines, read_error = NULL) {
  lines <- trimws(as.character(lines))
  errors <- character()
  if (!is.null(read_error)) errors <- c(errors, paste0("metadata read failed: ", read_error))
  band_count <- sum(grepl("^Band [0-9]+(?: |$)", lines))
  if (band_count != 1L) errors <- c(errors, paste0("expected one GRIB band; found ", band_count))

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
    unit = metadata_value("GRIB_UNIT"),
    comment = metadata_value("GRIB_COMMENT"),
    discipline = metadata_value("GRIB_DISCIPLINE"),
    ids = metadata_value("GRIB_IDS"),
    parse_errors = unique(errors)
  )
}

qpf_read_grib_metadata <- function(path, describer = terra::describe) {
  description <- tryCatch(describer(path), error = function(error) error)
  if (inherits(description, "error")) {
    return(qpf_parse_grib_metadata(character(), conditionMessage(description)))
  }
  qpf_parse_grib_metadata(description)
}

qpf_optional_iso_utc <- function(value) {
  if (length(value) != 1L || is.na(value)) return(NA_character_)
  tryCatch(qpf_iso_utc(value), error = function(error) NA_character_)
}

qpf_runtime_diagnostics <- function() {
  command_version <- function(command, args = "-version") {
    path <- Sys.which(command)
    if (!nzchar(path)) return(NA_character_)
    output <- tryCatch(system2(path, args, stdout = TRUE, stderr = TRUE),
                       error = function(error) character())
    if (length(output)) output[[1L]] else NA_character_
  }
  list(
    r_version = R.version.string,
    terra_version = tryCatch(as.character(utils::packageVersion("terra")),
                             error = function(error) NA_character_),
    gdal_version = tryCatch(as.character(terra::gdal()),
                            error = function(error) NA_character_),
    cwebp_version = command_version("cwebp"),
    dwebp_version = command_version("dwebp"),
    webpinfo_version = command_version("webpinfo")
  )
}

qpf_source_diagnostic <- function(metadata, record, terra_time,
                                  runtime = qpf_runtime_diagnostics()) {
  expected_reference <- as.numeric(qpf_as_utc(record$cycle_utc))
  expected_start_seconds <- as.numeric(record$lead_hours - QPF_ACCUMULATION_HOURS) * 3600
  expected_valid <- as.numeric(qpf_as_utc(record$valid_time_utc))
  finite_number <- function(value) length(value) == 1L && is.finite(value)
  number_matches <- function(actual, expected) {
    finite_number(actual) && finite_number(expected) && isTRUE(all.equal(actual, expected))
  }
  text_present <- function(value) {
    is.character(value) && length(value) == 1L && !is.na(value) && nzchar(value)
  }

  reference_present <- finite_number(metadata$reference_time_epoch)
  forecast_present <- finite_number(metadata$forecast_seconds)
  valid_present <- finite_number(metadata$valid_time_epoch)
  required_present <- reference_present && forecast_present && valid_present &&
    all(vapply(metadata[c("element", "level", "unit", "comment", "discipline", "ids")],
               text_present, logical(1))) && finite_number(metadata$pdtn) &&
    !length(metadata$parse_errors)
  authoritative_valid <- if (valid_present) qpf_optional_iso_utc(metadata$valid_time_epoch) else NA_character_
  terra_time_utc <- qpf_optional_iso_utc(terra_time)
  terra_available <- length(terra_time_utc) == 1L && !is.na(terra_time_utc)
  terra_agrees <- if (terra_available && !is.na(authoritative_valid)) {
    identical(terra_time_utc, authoritative_valid)
  } else {
    NA
  }
  normalized_unit <- gsub(
    " ", "",
    gsub("]", "", gsub("[", "", metadata$unit %||% "", fixed = TRUE), fixed = TRUE),
    fixed = TRUE
  )

  checks <- list(
    required_metadata_present = required_present,
    single_message_band = identical(as.integer(metadata$band_count), 1L),
    reference_matches_cycle = number_matches(metadata$reference_time_epoch, expected_reference),
    accumulation_start_seconds_match = number_matches(
      metadata$forecast_seconds, expected_start_seconds
    ),
    valid_matches_interval_end = number_matches(metadata$valid_time_epoch, expected_valid),
    duration_is_six_hours = reference_present && forecast_present && valid_present &&
      number_matches(
        metadata$valid_time_epoch -
          (metadata$reference_time_epoch + metadata$forecast_seconds),
        QPF_ACCUMULATION_HOURS * 3600
      ),
    element_is_qpf06 = identical(metadata$element, "QPF06"),
    level_is_surface = identical(metadata$level, "0-SFC"),
    product_definition_is_accumulation = number_matches(metadata$pdtn, 8),
    unit_is_kg_per_square_metre = normalized_unit %in% c("kg/(m^2)", "kg/m^2"),
    comment_is_six_hour_total = grepl(
      "^06 hr Total precipitation \\[kg/\\(m\\^2\\)\\]$",
      metadata$comment %||% ""
    ),
    discipline_is_meteorological = startsWith(
      metadata$discipline %||% "", "0(Meteorological)"
    ),
    center_is_ncep_mdl = grepl("CENTER=7\\(US-NCEP\\)", metadata$ids %||% "") &&
      grepl("SUBCENTER=14\\(Meteorological Development Laboratory \\(MDL\\)\\)",
            metadata$ids %||% "")
  )
  list(
    inventory_record = record$inventory_line,
    expected_cycle_utc = record$cycle_utc,
    expected_lead_hours = as.integer(record$lead_hours),
    expected_accumulation_start_utc = record$accumulation_start_utc,
    expected_accumulation_end_utc = record$accumulation_end_utc,
    grib_reference_time_utc = if (reference_present) {
      qpf_optional_iso_utc(metadata$reference_time_epoch)
    } else NA_character_,
    grib_forecast_seconds = if (forecast_present) metadata$forecast_seconds else NA_real_,
    grib_valid_time_utc = authoritative_valid,
    terra_time_utc = terra_time_utc,
    terra_time_agrees = terra_agrees,
    decoder_divergence = isTRUE(terra_available && !isTRUE(terra_agrees)),
    element = metadata$element,
    level = metadata$level,
    pdtn = if (finite_number(metadata$pdtn)) metadata$pdtn else NA_real_,
    unit = metadata$unit,
    comment = metadata$comment,
    metadata_parse_errors = metadata$parse_errors,
    validation_checks = checks,
    authoritative_metadata_accepted = all(unlist(checks), na.rm = FALSE),
    runtime = runtime
  )
}

qpf_require_source_diagnostic <- function(diagnostic) {
  checks <- diagnostic$validation_checks
  if (!isTRUE(checks$required_metadata_present) || !isTRUE(checks$single_message_band)) {
    detail <- diagnostic$metadata_parse_errors
    if (!length(detail)) detail <- "one or more required fields are missing"
    qpf_stop(
      "validation_failed", "Authoritative APCP GRIB metadata are missing or ambiguous: ",
      paste(detail, collapse = "; ")
    )
  }
  messages <- c(
    reference_matches_cycle = "GRIB reference time does not match the selected source cycle.",
    accumulation_start_seconds_match = "GRIB accumulation start does not equal lead minus six hours.",
    valid_matches_interval_end = "GRIB valid time does not match the accumulation end.",
    duration_is_six_hours = "GRIB accumulation duration is not six hours.",
    element_is_qpf06 = "GRIB element is not deterministic six-hour QPF.",
    level_is_surface = "GRIB level is not surface.",
    product_definition_is_accumulation = "GRIB product definition is not the expected accumulation type.",
    unit_is_kg_per_square_metre = "GRIB native units are not kg/m^2.",
    comment_is_six_hour_total = "GRIB comment does not declare a six-hour precipitation total.",
    discipline_is_meteorological = "GRIB discipline is not meteorological.",
    center_is_ncep_mdl = "GRIB source center is not NCEP/MDL."
  )
  for (name in names(messages)) {
    if (!isTRUE(checks[[name]])) qpf_stop("validation_failed", messages[[name]])
  }
  invisible(TRUE)
}

qpf_log_source_diagnostic <- function(diagnostic) {
  value <- function(x) if (length(x) != 1L || is.na(x)) "missing" else as.character(x)
  message(
    "NBM QPF source timing: cycle=", value(diagnostic$expected_cycle_utc),
    " lead_hours=", value(diagnostic$expected_lead_hours),
    " expected_start=", value(diagnostic$expected_accumulation_start_utc),
    " expected_end=", value(diagnostic$expected_accumulation_end_utc),
    " grib_reference=", value(diagnostic$grib_reference_time_utc),
    " grib_forecast_seconds=", value(diagnostic$grib_forecast_seconds),
    " grib_valid=", value(diagnostic$grib_valid_time_utc),
    " terra_time=", value(diagnostic$terra_time_utc),
    " element=", value(diagnostic$element),
    " level=", value(diagnostic$level),
    " pdtn=", value(diagnostic$pdtn),
    " unit=", value(diagnostic$unit)
  )
  invisible(diagnostic)
}

qpf_validate_source_raster <- function(raster, record, source_diagnostic) {
  if (!inherits(raster, "SpatRaster")) {
    qpf_stop("validation_failed", "Decoded APCP source is not a SpatRaster.")
  }
  if (terra::nlyr(raster) != 1L) {
    qpf_stop("validation_failed", "APCP message must decode to exactly one raster layer.")
  }
  qpf_require_source_diagnostic(source_diagnostic)
  if (terra::ncol(raster) != QPF_SOURCE_COLUMNS || terra::nrow(raster) != QPF_SOURCE_ROWS) {
    qpf_stop("validation_failed", "APCP source grid dimensions do not match Core CONUS.")
  }
  if (!nzchar(terra::crs(raster)) || !terra::same.crs(raster, QPF_SOURCE_CRS)) {
    qpf_stop("validation_failed", "APCP source CRS does not match the locked NBM Lambert grid.")
  }
  unit <- gsub(" ", "", tolower(terra::units(raster)[[1L]] %||% ""))
  if (!unit %in% c("kg/(m^2)", "kg/m^2")) {
    qpf_stop("validation_failed", "APCP raster units must be kg/m^2; received: ", unit)
  }
  minimum <- as.numeric(terra::global(raster, "min", na.rm = TRUE)[1L, 1L])
  maximum <- as.numeric(terra::global(raster, "max", na.rm = TRUE)[1L, 1L])
  if (!is.finite(minimum) || !is.finite(maximum)) {
    qpf_stop("validation_failed", "APCP source contains no finite values.")
  }
  if (minimum < -QPF_NEGATIVE_TOLERANCE_MM) {
    qpf_stop("validation_failed", "APCP source contains materially negative precipitation.")
  }
  if (maximum > 1000) {
    qpf_stop("validation_failed", "APCP source exceeds the physical six-hour validation range.")
  }
  list(
    rows = terra::nrow(raster),
    columns = terra::ncol(raster),
    minimum_mm = minimum,
    maximum_mm = maximum,
    crs = terra::crs(raster, proj = TRUE),
    units = terra::units(raster)[[1L]],
    valid_time_utc = source_diagnostic$grib_valid_time_utc,
    decoder_divergence = isTRUE(source_diagnostic$decoder_divergence)
  )
}

qpf_destination_template <- function() {
  terra::rast(
    ncols = QPF_IMAGE_WIDTH,
    nrows = QPF_IMAGE_HEIGHT,
    extent = terra::ext(
      QPF_EXTENT_3857[["xmin"]], QPF_EXTENT_3857[["xmax"]],
      QPF_EXTENT_3857[["ymin"]], QPF_EXTENT_3857[["ymax"]]
    ),
    crs = "EPSG:3857"
  )
}

qpf_source_subset <- function(raster) {
  domain <- terra::as.polygons(
    terra::ext(
      QPF_BOUNDS_WGS84[["west"]], QPF_BOUNDS_WGS84[["east"]],
      QPF_BOUNDS_WGS84[["south"]], QPF_BOUNDS_WGS84[["north"]]
    ),
    crs = "EPSG:4326"
  )
  native_domain <- terra::project(domain, terra::crs(raster))
  native_extent <- terra::ext(native_domain)
  buffer <- abs(terra::res(raster)) * 2
  expanded <- terra::ext(
    native_extent$xmin - buffer[[1L]], native_extent$xmax + buffer[[1L]],
    native_extent$ymin - buffer[[2L]], native_extent$ymax + buffer[[2L]]
  )
  expanded <- terra::intersect(expanded, terra::ext(raster))
  if (is.null(expanded)) {
    qpf_stop("validation_failed", "APCP source does not intersect the locked output domain.")
  }
  subset <- terra::crop(raster, expanded, snap = "out")
  if (terra::ncell(subset) < 1L) {
    qpf_stop("validation_failed", "APCP source crop is empty.")
  }
  subset
}

qpf_validate_output_raster <- function(raster) {
  if (!inherits(raster, "SpatRaster") || terra::nlyr(raster) != 1L) {
    qpf_stop("validation_failed", "Projected QPF output is not one numeric raster layer.")
  }
  if (terra::ncol(raster) != QPF_IMAGE_WIDTH || terra::nrow(raster) != QPF_IMAGE_HEIGHT) {
    qpf_stop("validation_failed", "Projected QPF dimensions do not match 720 x 733.")
  }
  if (!terra::same.crs(raster, "EPSG:3857")) {
    qpf_stop("validation_failed", "Projected QPF CRS is not EPSG:3857.")
  }
  actual_extent <- c(
    xmin = terra::xmin(raster), ymin = terra::ymin(raster),
    xmax = terra::xmax(raster), ymax = terra::ymax(raster)
  )
  if (any(abs(actual_extent - QPF_EXTENT_3857) > 1e-6)) {
    qpf_stop("validation_failed", "Projected QPF extent does not match the locked bounds.")
  }
  if (any(abs(unname(terra::res(raster)) - unname(QPF_PIXEL_SIZE_M)) > 1e-6)) {
    qpf_stop("validation_failed", "Projected QPF pixel size does not match the locked grid.")
  }
  values <- terra::values(raster, mat = FALSE)
  finite <- is.finite(values)
  coverage <- mean(finite)
  if (!is.finite(coverage) || coverage < QPF_MIN_FINITE_COVERAGE) {
    qpf_stop("validation_failed", "Projected QPF finite coverage is below 99%.")
  }
  finite_values <- values[finite]
  minimum <- min(finite_values)
  maximum <- max(finite_values)
  if (minimum < -QPF_NEGATIVE_TOLERANCE_MM || maximum > 1000) {
    qpf_stop("validation_failed", "Projected QPF values fail the physical range gate.")
  }
  list(
    finite_coverage = unname(coverage),
    minimum_mm = unname(minimum),
    maximum_mm = unname(maximum),
    maximum_in = unname(maximum / 25.4),
    rows = terra::nrow(raster),
    columns = terra::ncol(raster),
    resolution_m = unname(terra::res(raster)),
    extent_m = unname(actual_extent)
  )
}

qpf_project_numeric <- function(raster) {
  source_subset <- qpf_source_subset(raster)
  source_subset <- terra::ifel(
    source_subset < 0 & source_subset >= -QPF_NEGATIVE_TOLERANCE_MM,
    0,
    source_subset
  )
  output <- terra::project(
    source_subset,
    qpf_destination_template(),
    method = "bilinear"
  )
  output <- terra::ifel(output < 0 & output >= -QPF_NEGATIVE_TOLERANCE_MM, 0, output)
  qpf_validate_output_raster(output)
  output
}

qpf_read_palette <- function(path) {
  palette <- utils::read.csv(
    path, stringsAsFactors = FALSE, check.names = FALSE, na.strings = c("", "NA")
  )
  required <- c(
    "class_index", "lower_inclusive_in", "upper_exclusive_in", "display_label",
    "color_hex", "alpha_u8", "interval_rule", "palette_id", "palette_version",
    "official_palette_claim"
  )
  if (nrow(palette) != 22L || !all(required %in% names(palette)) ||
      !identical(as.integer(palette$class_index), 0:21)) {
    qpf_stop("validation_failed", "NBM QPF palette structure is invalid.")
  }
  expected_breaks <- c(
    0, 0.01, 0.10, 0.25, 0.50, 1.00, 1.50, 2.00, 2.50, 3.00, 3.50,
    4.00, 4.50, 5.00, 5.50, 6.00, 7.00, 8.00, 10.00, 12.00, 15.00, 20.00
  )
  expected_colors <- c(
    NA, "#D9F0D3", "#A6DBA0", "#62BD73", "#2F9E55", "#146B38",
    "#FFF59D", "#FFE066", "#FDBE55", "#F79441", "#F05A3C", "#D73027",
    "#BD1F2D", "#9E1737", "#7A123D", "#5B0B55", "#480A6A", "#5F0A87",
    "#7D1A9A", "#A542B0", "#CD86CF", "#F1C6E7"
  )
  expected_upper <- c(expected_breaks[-1L], NA_real_)
  if (any(abs(as.numeric(palette$lower_inclusive_in) - expected_breaks) > 1e-12) ||
      any(abs(as.numeric(palette$upper_exclusive_in) - expected_upper) > 1e-12,
          na.rm = TRUE) ||
      !identical(is.na(palette$upper_exclusive_in), is.na(expected_upper)) ||
      !identical(toupper(palette$color_hex), expected_colors) ||
      !identical(as.integer(palette$alpha_u8), c(0L, rep(255L, 21L))) ||
      !all(palette$interval_rule == "lower-inclusive upper-exclusive") ||
      !all(palette$palette_id == QPF_PALETTE_ID) ||
      !all(as.integer(palette$palette_version) == QPF_PALETTE_VERSION) ||
      any(as.logical(palette$official_palette_claim))) {
    qpf_stop("validation_failed", "NBM QPF palette does not match the locked contract.")
  }
  palette
}

qpf_class_index <- function(amount_in, palette) {
  breaks <- as.numeric(palette$lower_inclusive_in[-1L])
  index <- findInterval(amount_in, breaks)
  index[!is.finite(amount_in) | amount_in < breaks[[1L]]] <- 0L
  as.integer(index)
}

qpf_class_color <- function(amount_in, palette) {
  index <- qpf_class_index(amount_in, palette)
  colors <- palette$color_hex[-1L]
  result <- rep(NA_character_, length(index))
  result[index > 0L] <- colors[index[index > 0L]]
  result
}

qpf_legend_cap <- function(cycle_max_in, palette) {
  if (length(cycle_max_in) != 1L || !is.finite(cycle_max_in) || cycle_max_in < 0) {
    qpf_stop("validation_failed", "Cycle maximum must be one finite nonnegative value.")
  }
  classes <- palette[-1L, , drop = FALSE]
  index <- qpf_class_index(cycle_max_in, palette)
  if (index == 0L) {
    return(list(
      legend_cap_in = QPF_MINIMUM_CAP_IN,
      legend_overflow = FALSE,
      active_class_upper_in = 0.01
    ))
  }
  upper <- as.numeric(classes$upper_exclusive_in[[index]])
  if (is.na(upper)) {
    return(list(
      legend_cap_in = 20,
      legend_overflow = TRUE,
      active_class_upper_in = NULL
    ))
  }
  candidates <- QPF_STANDARD_CAPS_IN[
    QPF_STANDARD_CAPS_IN >= max(QPF_MINIMUM_CAP_IN, upper)
  ]
  list(
    legend_cap_in = if (length(candidates)) candidates[[1L]] else 20,
    legend_overflow = FALSE,
    active_class_upper_in = upper
  )
}

qpf_rgba_array <- function(qpf_mm, palette) {
  values_mm <- if (inherits(qpf_mm, "SpatRaster")) {
    as.matrix(qpf_mm, wide = TRUE)
  } else {
    as.matrix(qpf_mm)
  }
  amount_in <- values_mm / 25.4
  index <- qpf_class_index(amount_in, palette)
  rgba <- array(0, dim = c(nrow(values_mm), ncol(values_mm), 4L))
  colors <- palette$color_hex[-1L]
  rgb <- grDevices::col2rgb(colors) / 255
  for (class_index in seq_along(colors)) {
    cells <- is.finite(values_mm) & index == class_index
    if (!any(cells)) next
    rgba[, , 1L][cells] <- rgb[1L, class_index]
    rgba[, , 2L][cells] <- rgb[2L, class_index]
    rgba[, , 3L][cells] <- rgb[3L, class_index]
    rgba[, , 4L][cells] <- 1
  }
  rgba
}

qpf_write_rgba_png <- function(qpf_mm, path, palette) {
  rgba <- qpf_rgba_array(qpf_mm, palette)
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  png::writePNG(rgba, target = path)
  invisible(rgba)
}

qpf_run_tool <- function(command, args, label = command) {
  executable <- Sys.which(command)
  if (!nzchar(executable)) {
    qpf_stop("validation_failed", "Required tool is not installed: ", command)
  }
  output <- tryCatch(
    system2(executable, args, stdout = TRUE, stderr = TRUE),
    error = function(error) error
  )
  if (inherits(output, "error")) {
    qpf_stop("validation_failed", label, " failed: ", conditionMessage(output))
  }
  status <- attr(output, "status")
  if (!is.null(status) && status != 0L) {
    qpf_stop("validation_failed", label, " failed: ", paste(output, collapse = "\n"))
  }
  output
}

qpf_encode_lossless_webp <- function(png_path, webp_path) {
  qpf_run_tool(
    "cwebp",
    c(
      "-quiet", "-lossless", "-exact", "-z", "9",
      shQuote(png_path), "-o", shQuote(webp_path)
    ),
    "cwebp lossless encoding"
  )
  invisible(webp_path)
}

qpf_decode_webp <- function(webp_path, png_path) {
  qpf_run_tool(
    "dwebp", c("-quiet", shQuote(webp_path), "-o", shQuote(png_path)),
    "dwebp decoding"
  )
  invisible(png_path)
}

qpf_webp_info <- function(path) {
  output <- qpf_run_tool("webpinfo", c("-summary", shQuote(path)), "webpinfo validation")
  text <- paste(output, collapse = "\n")
  width <- suppressWarnings(as.integer(sub(".*Width: *([0-9]+).*", "\\1",
                                           grep("Width:", output, value = TRUE)[[1L]])))
  height <- suppressWarnings(as.integer(sub(".*Height: *([0-9]+).*", "\\1",
                                            grep("Height:", output, value = TRUE)[[1L]])))
  list(
    width = width,
    height = height,
    lossless_vp8l = grepl("Chunk VP8L", text, fixed = TRUE) &&
      grepl("Format: Lossless", text, fixed = TRUE),
    alpha = grepl("Alpha: 1", text, fixed = TRUE),
    animation = grepl("Animation: 1", text, fixed = TRUE),
    valid = grepl("No error detected", text, fixed = TRUE)
  )
}

qpf_palette_rgb_keys <- function(palette) {
  colors <- grDevices::col2rgb(palette$color_hex[-1L])
  apply(colors, 2L, function(value) paste(as.integer(value), collapse = ","))
}

qpf_validate_rgba <- function(rgba, palette, reference = NULL,
                              width = QPF_IMAGE_WIDTH, height = QPF_IMAGE_HEIGHT) {
  if (length(dim(rgba)) != 3L || dim(rgba)[[1L]] != height ||
      dim(rgba)[[2L]] != width || dim(rgba)[[3L]] != 4L) {
    qpf_stop("validation_failed", "Decoded WebP RGBA dimensions are invalid.")
  }
  bytes <- as.integer(round(rgba * 255))
  dim(bytes) <- dim(rgba)
  alpha <- bytes[, , 4L]
  if (any(!alpha %in% c(0L, 255L))) {
    qpf_stop("validation_failed", "Decoded WebP contains non-binary alpha values.")
  }
  transparent <- alpha == 0L
  if (any(transparent) && any(bytes[, , 1L][transparent] != 0L |
                              bytes[, , 2L][transparent] != 0L |
                              bytes[, , 3L][transparent] != 0L)) {
    qpf_stop("validation_failed", "Transparent WebP pixels do not preserve exact zero RGB.")
  }
  painted <- alpha == 255L
  if (any(painted)) {
    keys <- paste(
      bytes[, , 1L][painted], bytes[, , 2L][painted], bytes[, , 3L][painted], sep = ","
    )
    if (any(!keys %in% qpf_palette_rgb_keys(palette))) {
      qpf_stop("validation_failed", "Decoded WebP contains a color outside the locked palette.")
    }
  }
  if (!is.null(reference)) {
    reference_bytes <- as.integer(round(reference * 255))
    dim(reference_bytes) <- dim(reference)
    if (!identical(bytes, reference_bytes)) {
      qpf_stop("validation_failed", "Decoded WebP pixels do not match the RGBA reference.")
    }
  }
  list(
    transparent_pixel_count = sum(transparent),
    painted_pixel_count = sum(painted),
    distinct_painted_rgba_count = if (any(painted)) length(unique(keys)) else 0L,
    alpha_values = sort(unique(as.integer(alpha)))
  )
}

qpf_validate_webp <- function(path, palette, reference = NULL,
                              width = QPF_IMAGE_WIDTH, height = QPF_IMAGE_HEIGHT) {
  if (!file.exists(path) || unname(file.info(path)$size) < 1) {
    qpf_stop("validation_failed", "WebP target is missing or empty.")
  }
  signature <- readBin(path, what = "raw", n = 12L)
  if (length(signature) != 12L || rawToChar(signature[1:4]) != "RIFF" ||
      rawToChar(signature[9:12]) != "WEBP") {
    qpf_stop("validation_failed", "WebP target does not have a RIFF/WEBP signature.")
  }
  info <- qpf_webp_info(path)
  if (!isTRUE(info$valid) || !isTRUE(info$lossless_vp8l) || !isTRUE(info$alpha) ||
      isTRUE(info$animation) || info$width != width || info$height != height) {
    qpf_stop("validation_failed", "WebP encoding, alpha, animation, or dimensions are invalid.")
  }
  decoded <- tempfile("nbm-qpf-decoded-", fileext = ".png")
  on.exit(unlink(decoded, force = TRUE), add = TRUE)
  qpf_decode_webp(path, decoded)
  rgba <- png::readPNG(decoded)
  pixel_stats <- qpf_validate_rgba(rgba, palette, reference, width, height)
  c(info, pixel_stats)
}

qpf_alignment_diagnostics <- function() {
  earth_radius_m <- 6378137
  mercator_x <- function(lon) earth_radius_m * lon * pi / 180
  mercator_y <- function(lat) earth_radius_m * log(tan(pi / 4 + lat * pi / 360))
  inverse_mercator_y <- function(y) {
    (2 * atan(exp(y / earth_radius_m)) - pi / 2) * 180 / pi
  }
  cities <- data.frame(
    id = c("san_francisco", "sacramento", "reno", "los_angeles", "medford"),
    lon = c(-122.4194, -121.4944, -119.8138, -118.2437, -122.8756),
    lat = c(37.7749, 38.5816, 39.5296, 34.0522, 42.3265),
    stringsAsFactors = FALSE
  )
  pixel_x <- QPF_PIXEL_SIZE_M[["x"]]
  pixel_y <- QPF_PIXEL_SIZE_M[["y"]]
  cities$x_m <- mercator_x(cities$lon)
  cities$y_m <- mercator_y(cities$lat)
  cities$column <- round((cities$x_m - QPF_EXTENT_3857[["xmin"]]) / pixel_x + 0.5)
  cities$row <- round((QPF_EXTENT_3857[["ymax"]] - cities$y_m) / pixel_y + 0.5)
  center_x <- QPF_EXTENT_3857[["xmin"]] + (cities$column - 0.5) * pixel_x
  center_y <- QPF_EXTENT_3857[["ymax"]] - (cities$row - 0.5) * pixel_y
  cities$pixel_center_error_m <- sqrt((center_x - cities$x_m) ^ 2 + (center_y - cities$y_m) ^ 2)
  latitude_fraction <- (QPF_BOUNDS_WGS84[["north"]] - cities$lat) /
    (QPF_BOUNDS_WGS84[["north"]] - QPF_BOUNDS_WGS84[["south"]])
  stretched_y <- QPF_EXTENT_3857[["ymax"]] - latitude_fraction *
    (QPF_EXTENT_3857[["ymax"]] - QPF_EXTENT_3857[["ymin"]])
  cities$rejected_latitude_grid_offset_km <-
    (inverse_mercator_y(stretched_y) - cities$lat) * 111.32
  list(
    cities = cities,
    max_pixel_center_error_m = max(cities$pixel_center_error_m),
    rejected_stretch_offset_range_km = range(cities$rejected_latitude_grid_offset_km),
    row_order = "north_to_south",
    column_order = "west_to_east"
  )
}

qpf_target_filename <- function(record, sha256) {
  if (!is.character(sha256) || length(sha256) != 1L ||
      !grepl("^[0-9a-f]{64}$", sha256)) {
    qpf_stop("validation_failed", "A full lowercase SHA-256 is required for a QPF target.")
  }
  sprintf(
    "nbm_qpf_%s_f%03d_%s.webp",
    qpf_cycle_token(record$cycle_utc, filename = TRUE),
    as.integer(record$lead_hours),
    substr(sha256, 1L, 12L)
  )
}

qpf_safe_image_path <- function(path) {
  expected <- paste0(
    "^", QPF_ASSET_ROOT,
    "/nbm_qpf_[0-9]{8}T[0-9]{6}Z_f[0-9]{3}_[0-9a-f]{12}\\.webp$"
  )
  if (!is.character(path) || length(path) != 1L || is.na(path) || !nzchar(path) ||
      startsWith(path, "/") || grepl("\\\\", path) ||
      any(strsplit(path, "/", fixed = TRUE)[[1L]] %in% c("", ".", "..")) ||
      !grepl(expected, path)) {
    qpf_stop("validation_failed", "Unsafe NBM QPF target path: ", path)
  }
  path
}

qpf_palette_manifest <- function(palette) {
  classes <- palette[-1L, , drop = FALSE]
  list(
    palette_id = QPF_PALETTE_ID,
    palette_version = QPF_PALETTE_VERSION,
    display_units = "in",
    class_interval = "lower-inclusive upper-exclusive",
    below_0_01_in = "transparent",
    nodata = "transparent",
    overflow = ">=20 in uses the final fixed class",
    classes = lapply(seq_len(nrow(classes)), function(index) {
      list(
        lower_inclusive_in = as.numeric(classes$lower_inclusive_in[[index]]),
        upper_exclusive_in = if (is.na(classes$upper_exclusive_in[[index]])) {
          NULL
        } else {
          as.numeric(classes$upper_exclusive_in[[index]])
        },
        color_hex = classes$color_hex[[index]],
        alpha_u8 = as.integer(classes$alpha_u8[[index]])
      )
    })
  )
}

qpf_spatial_manifest <- function() {
  list(
    contract_id = QPF_GRID_CONTRACT_ID,
    media_type = "image/webp",
    encoding = "lossless VP8L RGBA8 WebP",
    crs = "EPSG:3857",
    bounds_wgs84 = unname(QPF_BOUNDS_WGS84),
    extent_m = unname(QPF_EXTENT_3857),
    image_width = QPF_IMAGE_WIDTH,
    image_height = QPF_IMAGE_HEIGHT,
    pixel_size_m = unname(QPF_PIXEL_SIZE_M),
    row_order = "north_to_south",
    column_order = "west_to_east",
    pixel_is_area = TRUE,
    leaflet_bounds = list(
      c(QPF_BOUNDS_WGS84[["south"]], QPF_BOUNDS_WGS84[["west"]]),
      c(QPF_BOUNDS_WGS84[["north"]], QPF_BOUNDS_WGS84[["east"]])
    ),
    default_leaflet_opacity = 0.55
  )
}

qpf_numeric_manifest <- function() {
  list(
    contract_id = "nbm_qpf_uint16_le_gzip_v1",
    media_type = "application/octet-stream",
    encoding = QPF_NUMERIC_ENCODING,
    compression = QPF_NUMERIC_COMPRESSION,
    stored_units = "in",
    scale = QPF_NUMERIC_SCALE,
    offset = QPF_NUMERIC_OFFSET,
    nodata = QPF_NUMERIC_NODATA,
    valid_stored_min = 0L,
    valid_stored_max = QPF_NUMERIC_VALID_MAX,
    represented_min = 0,
    represented_max = QPF_NUMERIC_VALID_MAX * QPF_NUMERIC_SCALE,
    uncompressed_bytes = QPF_NUMERIC_UNCOMPRESSED_BYTES,
    grid_contract_id = QPF_GRID_CONTRACT_ID,
    columns = QPF_IMAGE_WIDTH,
    rows = QPF_IMAGE_HEIGHT,
    crs = "EPSG:3857",
    bounds_wgs84 = unname(QPF_BOUNDS_WGS84),
    extent_m = unname(QPF_EXTENT_3857),
    row_order = "north_to_south",
    column_order = "west_to_east",
    pixel_is_area = TRUE
  )
}

qpf_forecast_state_manifest <- function() {
  list(
    algorithm = QPF_FORECAST_STATE_BINDING,
    digest = "sha256",
    canonicalization = "ordered UTF-8 key=value lines",
    binds = c("forecast metadata", "image path and SHA-256", "numeric path and SHA-256")
  )
}

qpf_numeric_filename <- function(record, sha256) {
  if (!is.character(sha256) || length(sha256) != 1L ||
      !grepl("^[0-9a-f]{64}$", sha256)) {
    qpf_stop("validation_failed", "A full lowercase SHA-256 is required for a numeric target.")
  }
  sprintf(
    "nbm_qpf_%s_f%03d_%s.u16le.gz",
    qpf_cycle_token(record$cycle_utc, filename = TRUE),
    as.integer(record$lead_hours),
    substr(sha256, 1L, 12L)
  )
}

qpf_safe_numeric_path <- function(path) {
  expected <- paste0(
    "^", QPF_ASSET_ROOT,
    "/nbm_qpf_[0-9]{8}T[0-9]{6}Z_f[0-9]{3}_[0-9a-f]{12}\\.u16le\\.gz$"
  )
  if (!is.character(path) || length(path) != 1L || is.na(path) || !nzchar(path) ||
      startsWith(path, "/") || grepl("\\\\", path) ||
      any(strsplit(path, "/", fixed = TRUE)[[1L]] %in% c("", ".", "..")) ||
      !grepl(expected, path)) {
    qpf_stop("validation_failed", "Unsafe NBM QPF numeric target path: ", path)
  }
  path
}

qpf_numeric_raw <- function(qpf_mm) {
  values_mm <- if (inherits(qpf_mm, "SpatRaster")) {
    as.matrix(qpf_mm, wide = TRUE)
  } else {
    as.matrix(qpf_mm)
  }
  if (!identical(dim(values_mm), c(QPF_IMAGE_HEIGHT, QPF_IMAGE_WIDTH))) {
    qpf_stop("validation_failed", "Numeric QPF grid dimensions are invalid.")
  }
  values_in <- as.vector(t(values_mm / 25.4))
  finite <- is.finite(values_in)
  if (any(values_in[finite] < 0)) {
    qpf_stop("validation_failed", "Numeric QPF values cannot be negative.")
  }
  stored <- rep.int(QPF_NUMERIC_NODATA, length(values_in))
  stored[finite] <- as.integer(round(
    (values_in[finite] - QPF_NUMERIC_OFFSET) / QPF_NUMERIC_SCALE
  ))
  if (anyNA(stored[finite]) || any(stored[finite] > QPF_NUMERIC_VALID_MAX)) {
    qpf_stop("validation_failed", "Numeric QPF uint16 encoding overflow.")
  }
  decoded <- stored[finite] * QPF_NUMERIC_SCALE + QPF_NUMERIC_OFFSET
  errors <- abs(decoded - values_in[finite])
  maximum_error <- if (length(errors)) max(errors) else 0
  if (!is.finite(maximum_error) || maximum_error >= QPF_NUMERIC_SCALE / 2) {
    qpf_stop("validation_failed", "Numeric QPF quantization error is not below 0.0005 inches.")
  }
  payload <- raw(length(stored) * 2L)
  low <- seq.int(1L, length(payload), by = 2L)
  payload[low] <- as.raw(stored %% 256L)
  payload[low + 1L] <- as.raw(stored %/% 256L)
  list(
    payload = payload,
    stored = stored,
    finite_count = sum(finite),
    nodata_count = sum(!finite),
    maximum_quantization_error_in = unname(maximum_error)
  )
}

qpf_write_numeric <- function(qpf_mm, path) {
  encoded <- qpf_numeric_raw(qpf_mm)
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  connection <- gzfile(path, open = "wb", compression = 9L)
  tryCatch(
    writeBin(encoded$payload, connection),
    finally = close(connection)
  )
  c(
    encoded[c("finite_count", "nodata_count", "maximum_quantization_error_in")],
    list(
      compressed_bytes = unname(file.info(path)$size),
      uncompressed_bytes = length(encoded$payload),
      sha256 = qpf_sha256_file(path)
    )
  )
}

qpf_read_numeric <- function(path) {
  if (!file.exists(path) || unname(file.info(path)$size) < 1) {
    qpf_stop("validation_failed", "Numeric QPF target is missing or empty.")
  }
  compressed <- readBin(path, "raw", n = unname(file.info(path)$size))
  payload <- tryCatch(
    memDecompress(compressed, type = "gzip"),
    error = function(error) {
      qpf_stop("validation_failed", "Numeric QPF gzip payload is corrupt: ", conditionMessage(error))
    }
  )
  if (length(payload) != QPF_NUMERIC_UNCOMPRESSED_BYTES) {
    qpf_stop("validation_failed", "Numeric QPF uncompressed byte count is invalid.")
  }
  low <- seq.int(1L, length(payload), by = 2L)
  stored <- as.integer(payload[low]) + 256L * as.integer(payload[low + 1L])
  if (length(stored) != QPF_IMAGE_WIDTH * QPF_IMAGE_HEIGHT) {
    qpf_stop("validation_failed", "Numeric QPF decoded cell count is invalid.")
  }
  stored
}

qpf_forecast_state_id <- function(record, image_path, image_sha256,
                                  numeric_path, numeric_sha256) {
  fields <- c(
    binding = QPF_FORECAST_STATE_BINDING,
    product_id = QPF_PRODUCT_ID,
    source_id = QPF_SOURCE_ID,
    parameter = "APCP",
    level = "surface",
    cycle_utc = record$cycle_utc,
    lead_hours = as.character(as.integer(record$lead_hours)),
    valid_time_utc = record$valid_time_utc,
    accumulation_start_utc = record$accumulation_start_utc,
    accumulation_end_utc = record$accumulation_end_utc,
    accumulation_hours = as.character(QPF_ACCUMULATION_HOURS),
    source_inventory_semantics = record$inventory_semantics %||%
      record$source_inventory_semantics,
    native_units = "kg/m^2",
    normalized_units = "mm",
    stored_numeric_units = "in",
    display_units = "in",
    grid_contract_id = QPF_GRID_CONTRACT_ID,
    columns = as.character(QPF_IMAGE_WIDTH),
    rows = as.character(QPF_IMAGE_HEIGHT),
    crs = "EPSG:3857",
    bounds_wgs84 = "-130,30,-112,44.5",
    extent_m = "-14471533.803125564,3503549.843504374,-12467782.96884664,5543147.203861799",
    row_order = "north_to_south",
    column_order = "west_to_east",
    pixel_is_area = "true",
    palette_id = QPF_PALETTE_ID,
    palette_version = as.character(QPF_PALETTE_VERSION),
    image_path = image_path,
    image_sha256 = image_sha256,
    image_media_type = "image/webp",
    image_encoding = "lossless_vp8l_rgba8",
    numeric_path = numeric_path,
    numeric_sha256 = numeric_sha256,
    numeric_media_type = "application/octet-stream",
    numeric_encoding = QPF_NUMERIC_ENCODING,
    numeric_compression = QPF_NUMERIC_COMPRESSION,
    numeric_scale = "0.001",
    numeric_offset = "0",
    numeric_nodata = as.character(QPF_NUMERIC_NODATA)
  )
  qpf_sha256_raw(charToRaw(paste(names(fields), fields, sep = "=", collapse = "\n")))
}

qpf_target_entry <- function(record, image_path, image_file_path,
                             numeric_path, numeric_file_path) {
  image_path <- qpf_safe_image_path(image_path)
  numeric_path <- qpf_safe_numeric_path(numeric_path)
  image_sha256 <- qpf_sha256_file(image_file_path)
  numeric_sha256 <- qpf_sha256_file(numeric_file_path)
  image_bytes <- unname(file.info(image_file_path)$size)
  numeric_bytes <- unname(file.info(numeric_file_path)$size)
  list(
    product_id = QPF_PRODUCT_ID,
    forecast_state_id = qpf_forecast_state_id(
      record, image_path, image_sha256, numeric_path, numeric_sha256
    ),
    source_id = QPF_SOURCE_ID,
    parameter = "APCP",
    level = "surface",
    cycle_utc = record$cycle_utc,
    lead_hours = as.integer(record$lead_hours),
    lead_end_hours = as.integer(record$lead_hours),
    accumulation_start_utc = record$accumulation_start_utc,
    accumulation_end_utc = record$accumulation_end_utc,
    valid_time_utc = record$valid_time_utc,
    accumulation_hours = QPF_ACCUMULATION_HOURS,
    source_parameter = "APCP",
    source_level = "surface",
    source_inventory_semantics = record$inventory_semantics,
    native_units = "kg/m^2",
    normalized_units = "mm",
    stored_numeric_units = "in",
    display_units = "in",
    grid_contract_id = QPF_GRID_CONTRACT_ID,
    columns = QPF_IMAGE_WIDTH,
    rows = QPF_IMAGE_HEIGHT,
    crs = "EPSG:3857",
    extent_m = unname(QPF_EXTENT_3857),
    row_order = "north_to_south",
    column_order = "west_to_east",
    pixel_is_area = TRUE,
    image_path = image_path,
    image_media_type = "image/webp",
    image_encoding = "lossless_vp8l_rgba8",
    image_width = QPF_IMAGE_WIDTH,
    image_height = QPF_IMAGE_HEIGHT,
    bounds_wgs84 = unname(QPF_BOUNDS_WGS84),
    bytes = image_bytes,
    sha256 = image_sha256,
    image = list(
      path = image_path,
      media_type = "image/webp",
      encoding = "lossless_vp8l_rgba8",
      bytes = image_bytes,
      sha256 = image_sha256
    ),
    numeric = list(
      path = numeric_path,
      media_type = "application/octet-stream",
      encoding = QPF_NUMERIC_ENCODING,
      compression = QPF_NUMERIC_COMPRESSION,
      stored_units = "in",
      scale = QPF_NUMERIC_SCALE,
      offset = QPF_NUMERIC_OFFSET,
      nodata = QPF_NUMERIC_NODATA,
      compressed_bytes = numeric_bytes,
      uncompressed_bytes = QPF_NUMERIC_UNCOMPRESSED_BYTES,
      sha256 = numeric_sha256
    ),
    palette_id = QPF_PALETTE_ID,
    palette_version = QPF_PALETTE_VERSION
  )
}

qpf_candidate_manifest <- function(cycle, targets, target_maxima_in, palette) {
  cycle <- qpf_validate_cycle(cycle)
  if (length(targets) != length(QPF_SUPPORTED_LEADS) ||
      length(target_maxima_in) != length(QPF_SUPPORTED_LEADS) ||
      any(!is.finite(target_maxima_in))) {
    qpf_stop(
      "validation_failed", "A candidate manifest requires ",
      length(QPF_SUPPORTED_LEADS), " validated target maxima."
    )
  }
  cycle_max <- max(target_maxima_in)
  legend <- qpf_legend_cap(cycle_max, palette)
  list(
    candidate_schema_version = "1.0.0",
    candidate_kind = "offline_complete_cycle",
    product_id = QPF_PRODUCT_ID,
    publication_ready = FALSE,
    mutable_public_manifest_included = FALSE,
    source = list(
      source_id = QPF_SOURCE_ID,
      agency = "NOAA/NWS/NCEP/MDL",
      dataset = "National Blend of Models",
      family = "core",
      domain = "conus",
      parameter = "APCP",
      level = "surface",
      field_kind = "deterministic accumulated precipitation",
      native_units = "kg/m^2",
      normalized_units = "mm",
      display_units = "in"
    ),
    palette = qpf_palette_manifest(palette),
    spatial_representation = qpf_spatial_manifest(),
    numeric_representation = qpf_numeric_manifest(),
    forecast_state_binding = qpf_forecast_state_manifest(),
    cycle = list(
      cycle_utc = qpf_iso_utc(cycle),
      cycle_status = "complete",
      cycle_max_qpf_in = unname(cycle_max),
      legend_cap_in = unname(legend$legend_cap_in),
      legend_overflow = isTRUE(legend$legend_overflow),
      target_count = length(targets),
      complete_required_leads_hours = QPF_SUPPORTED_LEADS,
      targets = targets
    )
  )
}

qpf_write_json <- function(value, path, pretty = TRUE) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  text <- jsonlite::toJSON(
    value, auto_unbox = TRUE, null = "null", na = "null", digits = NA, pretty = pretty
  )
  writeLines(c(text, ""), path, useBytes = TRUE)
  invisible(path)
}

qpf_json_normalize <- function(value) {
  jsonlite::fromJSON(
    jsonlite::toJSON(
      value, auto_unbox = TRUE, null = "null", na = "null", digits = NA, pretty = FALSE
    ),
    simplifyVector = FALSE
  )
}

qpf_preflight_data_frame <- function(records) {
  records <- qpf_validate_preflight(records)
  data.frame(
    cycle_utc = vapply(records, `[[`, character(1), "cycle_utc"),
    lead_hours = vapply(records, `[[`, integer(1), "lead_hours"),
    accumulation_start_lead_hours = vapply(
      records, `[[`, integer(1), "accumulation_start_lead_hours"
    ),
    accumulation_hours = vapply(records, `[[`, integer(1), "accumulation_hours"),
    accumulation_start_utc = vapply(records, `[[`, character(1), "accumulation_start_utc"),
    accumulation_end_utc = vapply(records, `[[`, character(1), "accumulation_end_utc"),
    valid_time_utc = vapply(records, `[[`, character(1), "valid_time_utc"),
    inventory_semantics = vapply(records, `[[`, character(1), "inventory_semantics"),
    record_number = vapply(records, `[[`, integer(1), "record_number"),
    start_byte = vapply(records, `[[`, numeric(1), "start_byte"),
    end_byte = vapply(records, `[[`, numeric(1), "end_byte"),
    expected_bytes = vapply(records, `[[`, numeric(1), "expected_bytes"),
    inventory_bytes = vapply(records, `[[`, numeric(1), "inventory_bytes"),
    inventory_sha256 = vapply(records, `[[`, character(1), "inventory_sha256"),
    inventory_line = vapply(records, `[[`, character(1), "inventory_line"),
    index_url = vapply(records, `[[`, character(1), "index_url"),
    grib_url = vapply(records, `[[`, character(1), "grib_url"),
    stringsAsFactors = FALSE
  )
}

qpf_write_preflight <- function(records, path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  utils::write.csv(qpf_preflight_data_frame(records), path, row.names = FALSE, na = "")
  invisible(path)
}

qpf_read_preflight <- function(path) {
  table <- utils::read.csv(path, stringsAsFactors = FALSE, check.names = FALSE)
  if (nrow(table) != length(QPF_SUPPORTED_LEADS)) {
    qpf_stop(
      "validation_failed", "Candidate preflight CSV must contain ",
      length(QPF_SUPPORTED_LEADS), " records."
    )
  }
  table
}

qpf_build_target <- function(record, source, candidate_root, palette,
                             runtime = qpf_runtime_diagnostics()) {
  raster <- qpf_decode_grib(source$path)
  metadata <- qpf_read_grib_metadata(source$path)
  terra_time <- tryCatch(terra::time(raster)[[1L]], error = function(error) NA)
  diagnostic <- qpf_source_diagnostic(metadata, record, terra_time, runtime)
  qpf_log_source_diagnostic(diagnostic)
  source_stats <- qpf_validate_source_raster(raster, record, diagnostic)
  output <- qpf_project_numeric(raster)
  output_stats <- qpf_validate_output_raster(output)

  reference_png <- tempfile(sprintf("nbm-qpf-f%03d-reference-", record$lead_hours),
                            fileext = ".png")
  temporary_webp <- tempfile(sprintf("nbm-qpf-f%03d-", record$lead_hours), fileext = ".webp")
  temporary_numeric <- tempfile(
    sprintf("nbm-qpf-f%03d-numeric-", record$lead_hours), fileext = ".u16le.gz"
  )
  on.exit(unlink(c(reference_png, temporary_webp, temporary_numeric), force = TRUE), add = TRUE)
  numeric_stats <- qpf_write_numeric(output, temporary_numeric)
  qpf_read_numeric(temporary_numeric)
  reference <- qpf_write_rgba_png(output, reference_png, palette)
  qpf_encode_lossless_webp(reference_png, temporary_webp)
  webp_stats <- qpf_validate_webp(temporary_webp, palette, reference)
  sha256 <- qpf_sha256_file(temporary_webp)
  filename <- qpf_target_filename(record, sha256)
  image_path <- file.path(QPF_ASSET_ROOT, filename)
  destination <- file.path(candidate_root, image_path)
  dir.create(dirname(destination), recursive = TRUE, showWarnings = FALSE)
  if (!file.copy(temporary_webp, destination, overwrite = FALSE, copy.mode = TRUE)) {
    qpf_stop("validation_failed", "Could not finalize content-addressed QPF target.")
  }
  numeric_sha256 <- qpf_sha256_file(temporary_numeric)
  numeric_filename <- qpf_numeric_filename(record, numeric_sha256)
  numeric_path <- file.path(QPF_ASSET_ROOT, numeric_filename)
  numeric_destination <- file.path(candidate_root, numeric_path)
  if (!file.copy(
    temporary_numeric, numeric_destination, overwrite = FALSE, copy.mode = TRUE
  )) {
    qpf_stop("validation_failed", "Could not finalize content-addressed numeric QPF target.")
  }
  target <- qpf_target_entry(
    record, image_path, destination, numeric_path, numeric_destination
  )
  if (!identical(target$sha256, sha256)) {
    qpf_stop("validation_failed", "Final QPF target checksum changed during finalization.")
  }
  if (!identical(target$numeric$sha256, numeric_sha256)) {
    qpf_stop("validation_failed", "Final numeric QPF checksum changed during finalization.")
  }
  validation <- list(
    lead_hours = as.integer(record$lead_hours),
    cycle_utc = record$cycle_utc,
    accumulation_start_utc = record$accumulation_start_utc,
    accumulation_end_utc = record$accumulation_end_utc,
    accumulation_hours = QPF_ACCUMULATION_HOURS,
    inventory_sha256 = record$inventory_sha256,
    inventory_record = record$inventory_line,
    source_bytes = as.numeric(source$bytes),
    source_sha256 = source$sha256,
    source_grid = source_stats,
    source_metadata = diagnostic,
    output_grid = output_stats,
    target_max_qpf_in = output_stats$maximum_in,
    webp = c(
      list(path = image_path, bytes = target$bytes, sha256 = target$sha256),
      webp_stats
    ),
    numeric = c(
      list(
        path = numeric_path,
        compressed_bytes = target$numeric$compressed_bytes,
        uncompressed_bytes = target$numeric$uncompressed_bytes,
        sha256 = target$numeric$sha256
      ),
      numeric_stats[c("finite_count", "nodata_count", "maximum_quantization_error_in")]
    ),
    forecast_state_id = target$forecast_state_id
  )
  rm(raster, output)
  invisible(gc(FALSE))
  list(target = target, validation = validation, maximum_in = output_stats$maximum_in)
}

qpf_candidate_validation <- function(manifest, target_validations, runtime,
                                     determinism = list(performed = FALSE)) {
  list(
    validation_schema_version = "1.0.0",
    product_id = QPF_PRODUCT_ID,
    validation_result = "passed",
    complete_cycle = TRUE,
    source_record_count = length(target_validations),
    target_count = length(target_validations),
    numeric_reprojection_before_classification = TRUE,
    interpolation = "bilinear",
    manifest_target_closure = TRUE,
    content_hashes_validated = TRUE,
    image_numeric_state_binding_validated = TRUE,
    numeric_encoding = QPF_NUMERIC_ENCODING,
    numeric_compression = QPF_NUMERIC_COMPRESSION,
    numeric_uncompressed_bytes_per_target = QPF_NUMERIC_UNCOMPRESSED_BYTES,
    cycle_image_bytes = sum(vapply(
      manifest$cycle$targets, function(target) as.numeric(target$bytes), numeric(1)
    )),
    cycle_numeric_compressed_bytes = sum(vapply(
      manifest$cycle$targets,
      function(target) as.numeric(target$numeric$compressed_bytes), numeric(1)
    )),
    cycle_numeric_uncompressed_bytes = length(manifest$cycle$targets) *
      QPF_NUMERIC_UNCOMPRESSED_BYTES,
    cycle_max_qpf_in = manifest$cycle$cycle_max_qpf_in,
    legend_cap_in = manifest$cycle$legend_cap_in,
    legend_overflow = manifest$cycle$legend_overflow,
    alignment = qpf_alignment_diagnostics(),
    runtime = runtime,
    targets = target_validations,
    determinism = determinism
  )
}

qpf_build_candidate <- function(records, sources, candidate_root, palette_path) {
  records <- qpf_validate_preflight(records)
  if (dir.exists(candidate_root) && length(list.files(candidate_root, all.files = TRUE,
                                                      no.. = TRUE))) {
    qpf_stop("validation_failed", "Candidate output root must be absent or empty.")
  }
  dir.create(candidate_root, recursive = TRUE, showWarnings = FALSE)
  palette <- qpf_read_palette(palette_path)
  runtime <- qpf_runtime_diagnostics()
  targets <- list()
  validations <- list()
  maxima <- numeric()
  for (record in records) {
    source <- sources[[as.character(record$lead_hours)]]
    if (is.null(source) || !file.exists(source$path) ||
        !identical(qpf_sha256_file(source$path), source$sha256)) {
      qpf_stop("validation_failed", "QPF source input is missing or does not match its hash.")
    }
    message("Building NBM QPF ", record$cycle_utc, " f", sprintf("%03d", record$lead_hours))
    built <- qpf_build_target(record, source, candidate_root, palette, runtime)
    targets[[length(targets) + 1L]] <- built$target
    validations[[length(validations) + 1L]] <- built$validation
    maxima <- c(maxima, built$maximum_in)
  }
  cycle <- qpf_validate_cycle(records[[1L]]$cycle_utc)
  manifest <- qpf_candidate_manifest(cycle, targets, maxima, palette)
  qpf_write_json(manifest, file.path(candidate_root, QPF_CANDIDATE_MANIFEST))
  qpf_write_preflight(records, file.path(candidate_root, QPF_PREFLIGHT_FILE))
  validation <- qpf_candidate_validation(manifest, validations, runtime)
  qpf_write_json(validation, file.path(candidate_root, QPF_VALIDATION_FILE))
  qpf_validate_candidate(candidate_root, palette_path)
  list(manifest = manifest, validation = validation, root = candidate_root)
}

qpf_scalar_number <- function(value) {
  length(value) == 1L && is.numeric(value) && is.finite(value)
}

qpf_validate_candidate <- function(root, palette_path) {
  manifest_path <- file.path(root, QPF_CANDIDATE_MANIFEST)
  validation_path <- file.path(root, QPF_VALIDATION_FILE)
  preflight_path <- file.path(root, QPF_PREFLIGHT_FILE)
  if (!all(file.exists(c(manifest_path, validation_path, preflight_path)))) {
    qpf_stop("validation_failed", "Candidate metadata files are incomplete.")
  }
  palette <- qpf_read_palette(palette_path)
  manifest <- tryCatch(
    jsonlite::fromJSON(manifest_path, simplifyVector = FALSE),
    error = function(error) {
      qpf_stop("validation_failed", "Candidate manifest is invalid JSON: ", conditionMessage(error))
    }
  )
  validation <- tryCatch(
    jsonlite::fromJSON(validation_path, simplifyVector = FALSE),
    error = function(error) {
      qpf_stop("validation_failed", "Candidate validation record is invalid JSON: ",
               conditionMessage(error))
    }
  )
  root_fields <- c(
    "candidate_schema_version", "candidate_kind", "product_id", "publication_ready",
    "mutable_public_manifest_included", "source", "palette", "spatial_representation",
    "numeric_representation", "forecast_state_binding", "cycle"
  )
  if (!all(root_fields %in% names(manifest)) ||
      !identical(manifest$candidate_schema_version, "1.0.0") ||
      !identical(manifest$candidate_kind, "offline_complete_cycle") ||
      !identical(manifest$product_id, QPF_PRODUCT_ID) ||
      !identical(manifest$publication_ready, FALSE) ||
      !identical(manifest$mutable_public_manifest_included, FALSE)) {
    qpf_stop("validation_failed", "Candidate manifest root contract is invalid.")
  }
  if (!identical(manifest$source$source_id, QPF_SOURCE_ID) ||
      !identical(manifest$source$family, "core") ||
      !identical(manifest$source$domain, "conus") ||
      !identical(manifest$source$parameter, "APCP") ||
      !identical(manifest$source$level, "surface") ||
      !identical(manifest$source$native_units, "kg/m^2") ||
      !identical(manifest$source$display_units, "in")) {
    qpf_stop("validation_failed", "Candidate source contract is invalid.")
  }
  if (!identical(manifest$palette, qpf_json_normalize(qpf_palette_manifest(palette)))) {
    qpf_stop("validation_failed", "Candidate embedded palette differs from the locked palette.")
  }
  if (!identical(
    manifest$spatial_representation,
    qpf_json_normalize(qpf_spatial_manifest())
  )) {
    qpf_stop("validation_failed", "Candidate spatial/WebP contract is invalid.")
  }
  if (!identical(
    manifest$numeric_representation,
    qpf_json_normalize(qpf_numeric_manifest())
  ) || !identical(
    manifest$forecast_state_binding,
    qpf_json_normalize(qpf_forecast_state_manifest())
  )) {
    qpf_stop("validation_failed", "Candidate numeric/state-binding contract is invalid.")
  }

  cycle <- manifest$cycle
  cycle_fields <- c(
    "cycle_utc", "cycle_status", "cycle_max_qpf_in", "legend_cap_in",
    "legend_overflow", "target_count", "complete_required_leads_hours", "targets"
  )
  if (!all(cycle_fields %in% names(cycle)) ||
      !identical(cycle$cycle_status, "complete") ||
      !qpf_scalar_number(cycle$target_count) ||
      as.integer(cycle$target_count) != length(QPF_SUPPORTED_LEADS) ||
      length(cycle$targets) != length(QPF_SUPPORTED_LEADS) ||
      !identical(as.integer(unlist(cycle$complete_required_leads_hours)), QPF_SUPPORTED_LEADS)) {
    qpf_stop("validation_failed", "Candidate cycle completeness contract is invalid.")
  }
  cycle_time <- qpf_validate_cycle(cycle$cycle_utc)
  required_target_fields <- c(
    "product_id", "forecast_state_id", "source_id", "parameter", "level",
    "cycle_utc", "lead_hours", "lead_end_hours",
    "accumulation_start_utc", "accumulation_end_utc", "valid_time_utc",
    "accumulation_hours", "source_parameter", "source_level",
    "source_inventory_semantics", "native_units", "normalized_units",
    "stored_numeric_units", "display_units", "grid_contract_id",
    "columns", "rows", "crs", "extent_m", "row_order", "column_order",
    "pixel_is_area",
    "image_path", "image_media_type", "image_encoding", "image_width", "image_height",
    "bounds_wgs84", "bytes", "sha256", "image", "numeric",
    "palette_id", "palette_version"
  )
  leads <- integer()
  image_paths <- character()
  numeric_paths <- character()
  for (target in cycle$targets) {
    if (!all(required_target_fields %in% names(target))) {
      qpf_stop("validation_failed", "Candidate target fields are incomplete.")
    }
    lead <- as.integer(target$lead_hours)
    leads <- c(leads, lead)
    expected_end <- cycle_time + lead * 3600
    expected_start <- expected_end - QPF_ACCUMULATION_HOURS * 3600
    expected_semantics <- sprintf("%d-%d hour acc fcst", lead - 6L, lead)
    if (!identical(target$product_id, QPF_PRODUCT_ID) ||
        !identical(target$source_id, QPF_SOURCE_ID) ||
        !identical(target$parameter, "APCP") ||
        !identical(target$level, "surface") ||
        !identical(target$cycle_utc, cycle$cycle_utc) ||
        as.integer(target$lead_end_hours) != lead ||
        as.integer(target$accumulation_hours) != QPF_ACCUMULATION_HOURS ||
        !identical(target$accumulation_start_utc, qpf_iso_utc(expected_start)) ||
        !identical(target$accumulation_end_utc, qpf_iso_utc(expected_end)) ||
        !identical(target$valid_time_utc, qpf_iso_utc(expected_end)) ||
        !identical(target$source_inventory_semantics, expected_semantics) ||
        !identical(target$source_parameter, "APCP") ||
        !identical(target$source_level, "surface") ||
        !identical(target$native_units, "kg/m^2") ||
        !identical(target$normalized_units, "mm") ||
        !identical(target$stored_numeric_units, "in") ||
        !identical(target$display_units, "in") ||
        !identical(target$grid_contract_id, QPF_GRID_CONTRACT_ID) ||
        as.integer(target$columns) != QPF_IMAGE_WIDTH ||
        as.integer(target$rows) != QPF_IMAGE_HEIGHT ||
        !identical(target$crs, "EPSG:3857") ||
        length(unlist(target$extent_m)) != length(QPF_EXTENT_3857) ||
        any(abs(as.numeric(unlist(target$extent_m)) - unname(QPF_EXTENT_3857)) > 1e-6) ||
        !identical(target$row_order, "north_to_south") ||
        !identical(target$column_order, "west_to_east") ||
        !identical(target$pixel_is_area, TRUE) ||
        !identical(target$image_media_type, "image/webp") ||
        !identical(target$image_encoding, "lossless_vp8l_rgba8") ||
        as.integer(target$image_width) != QPF_IMAGE_WIDTH ||
        as.integer(target$image_height) != QPF_IMAGE_HEIGHT ||
        !identical(as.numeric(unlist(target$bounds_wgs84)), unname(QPF_BOUNDS_WGS84)) ||
        !identical(target$palette_id, QPF_PALETTE_ID) ||
        as.integer(target$palette_version) != QPF_PALETTE_VERSION) {
      qpf_stop("validation_failed", "Candidate target product/time/unit/spatial contract is invalid.")
    }
    path <- qpf_safe_image_path(target$image_path)
    image_paths <- c(image_paths, path)
    file_path <- file.path(root, path)
    if (!file.exists(file_path)) qpf_stop("validation_failed", "Manifest target is missing: ", path)
    if (!is.character(target$sha256) || length(target$sha256) != 1L ||
        !grepl("^[0-9a-f]{64}$", target$sha256) ||
        !qpf_scalar_number(target$bytes) || target$bytes < 1 || target$bytes %% 1 != 0) {
      qpf_stop("validation_failed", "Candidate target hash or byte declaration is invalid.")
    }
    expected_name <- qpf_target_filename(
      list(cycle_utc = target$cycle_utc, lead_hours = lead), target$sha256
    )
    if (!identical(basename(path), expected_name) ||
        !identical(qpf_sha256_file(file_path), target$sha256) ||
        !identical(unname(file.info(file_path)$size), as.numeric(target$bytes))) {
      qpf_stop("validation_failed", "Candidate target path/hash/byte identity mismatch: ", path)
    }
    qpf_validate_webp(file_path, palette)

    numeric <- target$numeric
    numeric_fields <- c(
      "path", "media_type", "encoding", "compression", "stored_units",
      "scale", "offset", "nodata", "compressed_bytes", "uncompressed_bytes", "sha256"
    )
    if (!is.list(numeric) || !identical(sort(names(numeric)), sort(numeric_fields)) ||
        !identical(numeric$media_type, "application/octet-stream") ||
        !identical(numeric$encoding, QPF_NUMERIC_ENCODING) ||
        !identical(numeric$compression, QPF_NUMERIC_COMPRESSION) ||
        !identical(numeric$stored_units, "in") ||
        as.numeric(numeric$scale) != QPF_NUMERIC_SCALE ||
        as.numeric(numeric$offset) != QPF_NUMERIC_OFFSET ||
        as.integer(numeric$nodata) != QPF_NUMERIC_NODATA ||
        as.integer(numeric$uncompressed_bytes) != QPF_NUMERIC_UNCOMPRESSED_BYTES ||
        !qpf_scalar_number(numeric$compressed_bytes) || numeric$compressed_bytes < 1 ||
        numeric$compressed_bytes %% 1 != 0 ||
        !is.character(numeric$sha256) || length(numeric$sha256) != 1L ||
        !grepl("^[0-9a-f]{64}$", numeric$sha256)) {
      qpf_stop("validation_failed", "Candidate numeric target contract is invalid.")
    }
    numeric_path <- qpf_safe_numeric_path(numeric$path)
    numeric_paths <- c(numeric_paths, numeric_path)
    numeric_file <- file.path(root, numeric_path)
    expected_numeric_name <- qpf_numeric_filename(
      list(cycle_utc = target$cycle_utc, lead_hours = lead), numeric$sha256
    )
    if (!file.exists(numeric_file) ||
        !identical(basename(numeric_path), expected_numeric_name) ||
        !identical(qpf_sha256_file(numeric_file), numeric$sha256) ||
        !identical(unname(file.info(numeric_file)$size), as.numeric(numeric$compressed_bytes))) {
      qpf_stop(
        "validation_failed", "Candidate numeric path/hash/byte identity mismatch: ",
        numeric_path
      )
    }
    qpf_read_numeric(numeric_file)
    image <- target$image
    if (!is.list(image) || !identical(
      sort(names(image)), sort(c("path", "media_type", "encoding", "bytes", "sha256"))
    ) || !identical(image$path, target$image_path) ||
        !identical(image$media_type, target$image_media_type) ||
        !identical(image$encoding, target$image_encoding) ||
        as.numeric(image$bytes) != as.numeric(target$bytes) ||
        !identical(image$sha256, target$sha256)) {
      qpf_stop("validation_failed", "Candidate nested image identity is invalid.")
    }
    expected_state_id <- qpf_forecast_state_id(
      target, target$image_path, target$sha256, numeric_path, numeric$sha256
    )
    if (!is.character(target$forecast_state_id) || length(target$forecast_state_id) != 1L ||
        !identical(target$forecast_state_id, expected_state_id)) {
      qpf_stop("validation_failed", "Candidate image/numeric forecast-state binding is invalid.")
    }
  }
  if (anyDuplicated(leads) || anyDuplicated(image_paths) || anyDuplicated(numeric_paths) ||
      !identical(leads, QPF_SUPPORTED_LEADS)) {
    qpf_stop("validation_failed", "Candidate target order/set is incomplete, duplicated, or unexpected.")
  }

  if (!identical(validation$product_id, QPF_PRODUCT_ID) ||
      !identical(validation$validation_result, "passed") ||
      !identical(validation$complete_cycle, TRUE) ||
      !identical(validation$manifest_target_closure, TRUE) ||
      !identical(validation$content_hashes_validated, TRUE) ||
      !identical(validation$image_numeric_state_binding_validated, TRUE) ||
      !identical(validation$numeric_encoding, QPF_NUMERIC_ENCODING) ||
      !identical(validation$numeric_compression, QPF_NUMERIC_COMPRESSION) ||
      as.integer(validation$numeric_uncompressed_bytes_per_target) !=
        QPF_NUMERIC_UNCOMPRESSED_BYTES ||
      as.numeric(validation$cycle_image_bytes) != sum(vapply(
        cycle$targets, function(target) as.numeric(target$bytes), numeric(1)
      )) ||
      as.numeric(validation$cycle_numeric_compressed_bytes) != sum(vapply(
        cycle$targets,
        function(target) as.numeric(target$numeric$compressed_bytes), numeric(1)
      )) ||
      as.numeric(validation$cycle_numeric_uncompressed_bytes) !=
        length(QPF_SUPPORTED_LEADS) * QPF_NUMERIC_UNCOMPRESSED_BYTES ||
      as.integer(validation$source_record_count) != length(QPF_SUPPORTED_LEADS) ||
      as.integer(validation$target_count) != length(QPF_SUPPORTED_LEADS) ||
      length(validation$targets) != length(QPF_SUPPORTED_LEADS)) {
    qpf_stop("validation_failed", "Candidate validation summary is invalid.")
  }
  validation_leads <- vapply(
    validation$targets, function(target) as.integer(target$lead_hours), integer(1)
  )
  if (anyDuplicated(validation_leads) || !identical(validation_leads, QPF_SUPPORTED_LEADS)) {
    qpf_stop("validation_failed", "Candidate validation target set is incomplete or duplicated.")
  }
  target_maxima <- numeric()
  for (index in seq_along(validation$targets)) {
    target_validation <- validation$targets[[index]]
    target <- cycle$targets[[index]]
    if (!identical(target_validation$cycle_utc, cycle$cycle_utc) ||
        as.integer(target_validation$lead_hours) != as.integer(target$lead_hours) ||
        as.integer(target_validation$accumulation_hours) != QPF_ACCUMULATION_HOURS ||
        !identical(target_validation$accumulation_start_utc, target$accumulation_start_utc) ||
        !identical(target_validation$accumulation_end_utc, target$accumulation_end_utc) ||
        !identical(target_validation$webp$path, target$image_path) ||
        !identical(target_validation$webp$sha256, target$sha256) ||
        as.numeric(target_validation$webp$bytes) != as.numeric(target$bytes) ||
        !identical(target_validation$numeric$path, target$numeric$path) ||
        !identical(target_validation$numeric$sha256, target$numeric$sha256) ||
        as.numeric(target_validation$numeric$compressed_bytes) !=
          as.numeric(target$numeric$compressed_bytes) ||
        as.integer(target_validation$numeric$uncompressed_bytes) !=
          QPF_NUMERIC_UNCOMPRESSED_BYTES ||
        !identical(target_validation$forecast_state_id, target$forecast_state_id) ||
        !isTRUE(target_validation$source_metadata$authoritative_metadata_accepted) ||
        !qpf_scalar_number(target_validation$target_max_qpf_in)) {
      qpf_stop("validation_failed", "Candidate target validation does not close over its manifest target.")
    }
    target_maxima <- c(target_maxima, as.numeric(target_validation$target_max_qpf_in))
  }
  cycle_max <- max(target_maxima)
  legend <- qpf_legend_cap(cycle_max, palette)
  if (abs(as.numeric(cycle$cycle_max_qpf_in) - cycle_max) > 1e-12 ||
      as.numeric(cycle$legend_cap_in) != legend$legend_cap_in ||
      !identical(cycle$legend_overflow, isTRUE(legend$legend_overflow)) ||
      abs(as.numeric(validation$cycle_max_qpf_in) - cycle_max) > 1e-12 ||
      as.numeric(validation$legend_cap_in) != legend$legend_cap_in ||
      !identical(validation$legend_overflow, isTRUE(legend$legend_overflow))) {
    qpf_stop("validation_failed", "Candidate cycle maximum or legend cap does not recompute.")
  }

  preflight <- qpf_read_preflight(preflight_path)
  if (!identical(as.integer(preflight$lead_hours), QPF_SUPPORTED_LEADS) ||
      length(unique(preflight$cycle_utc)) != 1L ||
      !identical(unique(preflight$cycle_utc), cycle$cycle_utc)) {
    qpf_stop("validation_failed", "Candidate preflight CSV does not match the manifest cycle.")
  }
  for (index in seq_len(nrow(preflight))) {
    target <- cycle$targets[[index]]
    if (as.integer(preflight$accumulation_hours[[index]]) != QPF_ACCUMULATION_HOURS ||
        !identical(preflight$accumulation_start_utc[[index]], target$accumulation_start_utc) ||
        !identical(preflight$accumulation_end_utc[[index]], target$accumulation_end_utc) ||
        !identical(preflight$valid_time_utc[[index]], target$valid_time_utc) ||
        !identical(preflight$inventory_semantics[[index]], target$source_inventory_semantics) ||
        !grepl("^[0-9a-f]{64}$", preflight$inventory_sha256[[index]]) ||
        !startsWith(preflight$index_url[[index]], "https://") ||
        !endsWith(preflight$index_url[[index]], ".idx") ||
        !startsWith(preflight$grib_url[[index]], "https://")) {
      qpf_stop("validation_failed", "Candidate preflight source/time contract is invalid.")
    }
  }

  actual_files <- list.files(root, recursive = TRUE, all.files = TRUE, no.. = TRUE,
                             full.names = FALSE)
  actual_files <- gsub("\\\\", "/", actual_files)
  expected_files <- c(QPF_CANDIDATE_MANIFEST, QPF_VALIDATION_FILE, QPF_PREFLIGHT_FILE,
                      image_paths, numeric_paths)
  if (!identical(sort(actual_files), sort(expected_files))) {
    qpf_stop("validation_failed", "Candidate contains a missing or unexpected file.")
  }
  invisible(list(manifest = manifest, validation = validation, preflight = preflight))
}

qpf_compare_candidate_builds <- function(first_root, second_root, palette_path) {
  first <- qpf_validate_candidate(first_root, palette_path)
  second <- qpf_validate_candidate(second_root, palette_path)
  first_targets <- first$manifest$cycle$targets
  second_targets <- second$manifest$cycle$targets
  if (!identical(first$manifest, second$manifest)) {
    qpf_stop("validation_failed", "Repeat-build candidate manifests are not semantically identical.")
  }
  proof <- vector("list", length(QPF_SUPPORTED_LEADS))
  for (index in seq_along(first_targets)) {
    first_target <- first_targets[[index]]
    second_target <- second_targets[[index]]
    first_path <- file.path(first_root, first_target$image_path)
    second_path <- file.path(second_root, second_target$image_path)
    first_raw <- readBin(first_path, "raw", n = unname(file.info(first_path)$size))
    second_raw <- readBin(second_path, "raw", n = unname(file.info(second_path)$size))
    byte_identical <- identical(first_raw, second_raw)
    sha_identical <- identical(first_target$sha256, second_target$sha256) &&
      identical(qpf_sha256_file(first_path), qpf_sha256_file(second_path))
    if (!byte_identical || !sha_identical ||
        !identical(first_target$image_path, second_target$image_path)) {
      qpf_stop("validation_failed", "Repeat-build WebP bytes, hashes, or names differ.")
    }
    first_numeric_path <- file.path(first_root, first_target$numeric$path)
    second_numeric_path <- file.path(second_root, second_target$numeric$path)
    first_numeric_raw <- readBin(
      first_numeric_path, "raw", n = unname(file.info(first_numeric_path)$size)
    )
    second_numeric_raw <- readBin(
      second_numeric_path, "raw", n = unname(file.info(second_numeric_path)$size)
    )
    numeric_byte_identical <- identical(first_numeric_raw, second_numeric_raw)
    numeric_sha_identical <-
      identical(first_target$numeric$sha256, second_target$numeric$sha256) &&
      identical(qpf_sha256_file(first_numeric_path), qpf_sha256_file(second_numeric_path))
    if (!numeric_byte_identical || !numeric_sha_identical ||
        !identical(first_target$numeric$path, second_target$numeric$path) ||
        !identical(first_target$forecast_state_id, second_target$forecast_state_id)) {
      qpf_stop(
        "validation_failed",
        "Repeat-build numeric bytes, hashes, names, or forecast-state bindings differ."
      )
    }
    proof[[index]] <- list(
      lead_hours = as.integer(first_target$lead_hours),
      forecast_state_id = first_target$forecast_state_id,
      image = list(
        bytes = as.numeric(first_target$bytes),
        sha256 = first_target$sha256,
        byte_identical = TRUE,
        sha_identical = TRUE,
        name_identical = TRUE
      ),
      numeric = list(
        bytes = as.numeric(first_target$numeric$compressed_bytes),
        sha256 = first_target$numeric$sha256,
        byte_identical = TRUE,
        sha_identical = TRUE,
        name_identical = TRUE
      )
    )
  }
  list(
    performed = TRUE,
    source_inputs_reused = TRUE,
    target_count = length(QPF_SUPPORTED_LEADS),
    webp_byte_identity_count = length(QPF_SUPPORTED_LEADS),
    webp_sha_identity_count = length(QPF_SUPPORTED_LEADS),
    numeric_byte_identity_count = length(QPF_SUPPORTED_LEADS),
    numeric_sha_identity_count = length(QPF_SUPPORTED_LEADS),
    forecast_state_identity_count = length(QPF_SUPPORTED_LEADS),
    manifest_semantic_identity = TRUE,
    palette_identity = identical(first$manifest$palette, second$manifest$palette),
    legend_cap_identity = identical(
      first$manifest$cycle[c("cycle_max_qpf_in", "legend_cap_in", "legend_overflow")],
      second$manifest$cycle[c("cycle_max_qpf_in", "legend_cap_in", "legend_overflow")]
    ),
    targets = proof
  )
}

qpf_record_determinism <- function(root, proof, palette_path) {
  validation_path <- file.path(root, QPF_VALIDATION_FILE)
  validation <- jsonlite::fromJSON(validation_path, simplifyVector = FALSE)
  validation$determinism <- proof
  qpf_write_json(validation, validation_path)
  qpf_validate_candidate(root, palette_path)
  invisible(proof)
}

qpf_assert_offline_output_root <- function(output_root, project_root) {
  output_parent <- normalizePath(dirname(output_root), winslash = "/", mustWork = TRUE)
  output <- file.path(output_parent, basename(output_root))
  project <- normalizePath(project_root, winslash = "/", mustWork = TRUE)
  canonical <- normalizePath(file.path(project, "docs", "data"), winslash = "/", mustWork = TRUE)
  if (identical(output, canonical) || startsWith(paste0(output, "/"), paste0(canonical, "/"))) {
    qpf_stop("validation_failed", "Offline NBM QPF candidates may not write under canonical docs/data.")
  }
  if (dir.exists(output) || file.exists(output)) {
    qpf_stop("validation_failed", "Offline candidate delivery root must not already exist.")
  }
  output
}

qpf_deliver_candidate <- function(candidate_root, output_root, palette_path, project_root) {
  qpf_validate_candidate(candidate_root, palette_path)
  output <- qpf_assert_offline_output_root(output_root, project_root)
  parent <- dirname(output)
  dir.create(parent, recursive = TRUE, showWarnings = FALSE)
  stage <- tempfile("nbm-qpf-delivery-", tmpdir = parent)
  dir.create(stage, recursive = TRUE, showWarnings = FALSE)
  on.exit(if (dir.exists(stage)) unlink(stage, recursive = TRUE, force = TRUE), add = TRUE)
  files <- list.files(candidate_root, recursive = TRUE, all.files = TRUE, no.. = TRUE,
                      full.names = FALSE)
  for (relative in files) {
    source <- file.path(candidate_root, relative)
    destination <- file.path(stage, relative)
    dir.create(dirname(destination), recursive = TRUE, showWarnings = FALSE)
    if (!file.copy(source, destination, overwrite = FALSE, copy.mode = TRUE)) {
      qpf_stop("validation_failed", "Could not copy candidate delivery file: ", relative)
    }
  }
  qpf_validate_candidate(stage, palette_path)
  if (!file.rename(stage, output)) {
    qpf_stop("validation_failed", "Could not atomically finalize offline candidate delivery root.")
  }
  qpf_validate_candidate(output, palette_path)
  invisible(output)
}
