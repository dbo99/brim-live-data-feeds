# Product-specific transport validation for the DWR Delta Operations PDF.

DELTA_OPS_PDF_MIN_BYTES <- 1024L
DELTA_OPS_PDF_EOF_SCAN_BYTES <- 2048L
DELTA_OPS_PDF_MAX_ATTEMPTS <- 3L
DELTA_OPS_PDF_CONNECT_TIMEOUT_SECONDS <- 10
DELTA_OPS_PDF_TIMEOUT_SECONDS <- 30
DELTA_OPS_PDF_BACKOFF_SECONDS <- c(1, 2)
DELTA_OPS_PDF_USER_AGENT <- paste0(
  "BRIM-live-data-feeds Delta-Ops PDF retrieval ",
  "(+https://github.com/dbo99/brim-live-data-feeds)"
)

delta_ops_sha256_file <- function(path) {
  if (!file.exists(path)) return(NA_character_)

  if (requireNamespace("digest", quietly = TRUE)) {
    return(tolower(digest::digest(file = path, algo = "sha256")))
  }

  commands <- list(
    list(command = Sys.which("sha256sum"), args = path),
    list(command = Sys.which("shasum"), args = c("-a", "256", path))
  )
  for (spec in commands) {
    if (!nzchar(spec$command)) next
    output <- suppressWarnings(system2(
      spec$command,
      args = spec$args,
      stdout = TRUE,
      stderr = TRUE
    ))
    if (length(output) > 0L &&
        (identical(attr(output, "status"), 0L) || is.null(attr(output, "status")))) {
      hash <- sub("\\s+.*$", "", output[[1]])
      if (grepl("^[0-9a-fA-F]{64}$", hash)) return(tolower(hash))
    }
  }

  stop(
    "Delta Ops retrieval requires digest, sha256sum, or shasum for bounded diagnostics.",
    call. = FALSE
  )
}

delta_ops_raw_contains <- function(value, target) {
  if (length(target) == 0L) return(TRUE)
  if (length(value) < length(target)) return(FALSE)
  starts <- seq_len(length(value) - length(target) + 1L)
  any(vapply(starts, function(index) {
    identical(value[index:(index + length(target) - 1L)], target)
  }, logical(1)))
}

delta_ops_pdf_envelope <- function(path) {
  bytes <- if (file.exists(path)) as.numeric(file.info(path)$size) else 0
  if (!is.finite(bytes)) bytes <- 0

  magic <- raw()
  eof_tail <- raw()
  if (bytes > 0) {
    connection <- file(path, open = "rb")
    on.exit(close(connection), add = TRUE)
    magic <- readBin(connection, what = "raw", n = 5L)
    tail_bytes <- min(bytes, DELTA_OPS_PDF_EOF_SCAN_BYTES)
    seek(connection, where = bytes - tail_bytes, origin = "start")
    eof_tail <- readBin(connection, what = "raw", n = tail_bytes)
  }

  has_magic <- identical(magic, charToRaw("%PDF-"))
  has_eof <- delta_ops_raw_contains(eof_tail, charToRaw("%%EOF"))
  list(
    bytes = bytes,
    has_magic = has_magic,
    has_eof = has_eof,
    sha256 = delta_ops_sha256_file(path)
  )
}

delta_ops_normalize_content_type <- function(value) {
  if (is.null(value) || length(value) == 0L || is.na(value[[1]])) {
    return(NA_character_)
  }
  normalized <- tolower(trimws(strsplit(as.character(value[[1]]), ";", fixed = TRUE)[[1]][[1]]))
  if (!nzchar(normalized)) NA_character_ else normalized
}

delta_ops_pdf_content_type_is_compatible <- function(content_type) {
  is.na(content_type) || content_type %in% c(
    "application/pdf",
    "application/x-pdf",
    "application/octet-stream",
    "binary/octet-stream"
  )
}

delta_ops_retryable_http_status <- function(status) {
  !is.na(status) && (
    status %in% c(408L, 425L, 429L) || (status >= 500L && status <= 599L)
  )
}

delta_ops_safe_log_value <- function(value, missing = "<missing>") {
  if (is.null(value) || length(value) == 0L || is.na(value[[1]]) || !nzchar(as.character(value[[1]]))) {
    return(missing)
  }
  text <- gsub("[[:space:]]+", "_", as.character(value[[1]]))
  substr(text, 1L, 1000L)
}

delta_ops_safe_log_url <- function(value) {
  text <- delta_ops_safe_log_value(value)
  text <- sub("^(https?://)[^/@]+@", "\\1<redacted>@", text, perl = TRUE)
  gsub(
    "([?&](?:access_token|api_key|apikey|auth|authorization|key|signature|sig|token)=)[^&#]*",
    "\\1<redacted>",
    text,
    ignore.case = TRUE,
    perl = TRUE
  )
}

delta_ops_attempt_metadata <- function(attempt, max_attempts, status, content_type,
                                       envelope, source_url, final_url, outcome,
                                       validation, retry_delay = NA_real_) {
  paste0(
    "attempt=", attempt, "/", max_attempts,
    " outcome=", outcome,
    " status=", if (is.na(status)) "<missing>" else status,
    " content_type=", delta_ops_safe_log_value(content_type),
    " bytes=", format(envelope$bytes, scientific = FALSE, trim = TRUE),
    " sha256=", delta_ops_safe_log_value(envelope$sha256),
    " source_url=", delta_ops_safe_log_url(source_url),
    " final_url=", delta_ops_safe_log_url(final_url),
    " validation=", delta_ops_safe_log_value(validation),
    if (is.finite(retry_delay)) paste0(" retry_in_seconds=", retry_delay) else ""
  )
}

delta_ops_default_pdf_request <- function(url, path, timeout_sec,
                                          connect_timeout_sec, user_agent) {
  handle <- curl::new_handle()
  curl::handle_setopt(
    handle,
    followlocation = TRUE,
    maxredirs = 5L,
    connecttimeout = connect_timeout_sec,
    timeout = timeout_sec,
    failonerror = FALSE,
    useragent = user_agent
  )
  curl::handle_setheaders(handle, Accept = "application/pdf")
  response <- curl::curl_fetch_disk(url, path, handle = handle)
  list(
    status_code = as.integer(response$status_code),
    content_type = response$type,
    final_url = response$url
  )
}

delta_ops_fetch_pdf_text <- function(
    url,
    pdf_reader,
    request_once = delta_ops_default_pdf_request,
    sleep_fun = Sys.sleep,
    max_attempts = DELTA_OPS_PDF_MAX_ATTEMPTS,
    backoff_seconds = DELTA_OPS_PDF_BACKOFF_SECONDS,
    timeout_sec = DELTA_OPS_PDF_TIMEOUT_SECONDS,
    connect_timeout_sec = DELTA_OPS_PDF_CONNECT_TIMEOUT_SECONDS,
    user_agent = DELTA_OPS_PDF_USER_AGENT) {
  if (!is.character(url) || length(url) != 1L || is.na(url) || !nzchar(url)) {
    stop("Delta Ops PDF URL must be one non-empty string.", call. = FALSE)
  }
  max_attempts <- suppressWarnings(as.integer(max_attempts))
  if (is.na(max_attempts) || max_attempts < 1L || max_attempts > 5L) {
    stop("Delta Ops PDF max_attempts must be between 1 and 5.", call. = FALSE)
  }
  if (!is.function(pdf_reader) || !is.function(request_once) || !is.function(sleep_fun)) {
    stop("Delta Ops PDF reader, request, and sleep callbacks must be functions.", call. = FALSE)
  }
  if (length(backoff_seconds) < max_attempts - 1L ||
      any(!is.finite(backoff_seconds)) || any(backoff_seconds < 0)) {
    stop("Delta Ops PDF retry backoff is invalid.", call. = FALSE)
  }

  pdf_tmp <- tempfile("delta-ops-source-", fileext = ".pdf")
  on.exit(unlink(pdf_tmp, force = TRUE), add = TRUE)

  for (attempt in seq_len(max_attempts)) {
    unlink(pdf_tmp, force = TRUE)
    request_error <- NULL
    response <- tryCatch(
      request_once(
        url = url,
        path = pdf_tmp,
        timeout_sec = timeout_sec,
        connect_timeout_sec = connect_timeout_sec,
        user_agent = user_agent
      ),
      error = function(error) {
        request_error <<- error
        NULL
      }
    )
    envelope <- delta_ops_pdf_envelope(pdf_tmp)

    if (!is.null(request_error)) {
      retry_delay <- if (attempt < max_attempts) backoff_seconds[[attempt]] else NA_real_
      metadata <- delta_ops_attempt_metadata(
        attempt, max_attempts, NA_integer_, NA_character_, envelope, url, url,
        "transport_failure", class(request_error)[[1]], retry_delay
      )
      message("DELTA_OPS_RETRIEVAL_ATTEMPT ", metadata)
      if (attempt < max_attempts) {
        sleep_fun(retry_delay)
        next
      }
      stop(
        "DELTA_OPS_UPSTREAM_TRANSPORT_FAILURE ", metadata,
        call. = FALSE
      )
    }

    status <- if (is.null(response$status_code) || length(response$status_code) == 0L) {
      NA_integer_
    } else {
      suppressWarnings(as.integer(response$status_code[[1]]))
    }
    content_type <- delta_ops_normalize_content_type(response$content_type)
    final_url <- if (is.null(response$final_url)) url else response$final_url
    successful_status <- !is.na(status) && status >= 200L && status < 300L

    failures <- character()
    if (successful_status) {
      if (!delta_ops_pdf_content_type_is_compatible(content_type)) {
        failures <- c(failures, "incompatible_content_type")
      }
      if (envelope$bytes < DELTA_OPS_PDF_MIN_BYTES) {
        failures <- c(failures, "implausible_byte_count")
      }
      if (!envelope$has_magic) failures <- c(failures, "missing_pdf_magic")
      if (!envelope$has_eof) failures <- c(failures, "missing_pdf_eof")
    } else if (is.na(status)) {
      failures <- "missing_http_status"
    } else {
      failures <- paste0("http_status_", status)
    }

    if (successful_status && length(failures) == 0L) {
      metadata <- delta_ops_attempt_metadata(
        attempt, max_attempts, status, content_type, envelope, url, final_url,
        "pdf_validated", "passed"
      )
      message("DELTA_OPS_RETRIEVAL_ATTEMPT ", metadata)
      return(pdf_reader(pdf_tmp))
    }

    is_non_pdf <- successful_status
    retryable <- is_non_pdf || is.na(status) || delta_ops_retryable_http_status(status)
    retry_delay <- if (retryable && attempt < max_attempts) {
      backoff_seconds[[attempt]]
    } else {
      NA_real_
    }
    outcome <- if (is_non_pdf) "non_pdf_response" else "http_failure"
    metadata <- delta_ops_attempt_metadata(
      attempt, max_attempts, status, content_type, envelope, url, final_url,
      outcome, paste(failures, collapse = ","), retry_delay
    )
    message("DELTA_OPS_RETRIEVAL_ATTEMPT ", metadata)

    if (retryable && attempt < max_attempts) {
      sleep_fun(retry_delay)
      next
    }

    terminal_code <- if (is_non_pdf) {
      "DELTA_OPS_UPSTREAM_NON_PDF_RESPONSE"
    } else {
      "DELTA_OPS_UPSTREAM_HTTP_RESPONSE"
    }
    stop(terminal_code, " ", metadata, call. = FALSE)
  }

  stop("DELTA_OPS_UPSTREAM_TRANSPORT_FAILURE unreachable retry state", call. = FALSE)
}
