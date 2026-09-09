# DWR PDF retrieval boundary, reconciled from historical PR #40.
# This validates transport only; Poppler and the current builder own parsing.

delta_ops_pdf_sha256 <- function(path) {
  if (!file.exists(path)) return(NA_character_)
  if (requireNamespace("digest", quietly = TRUE)) {
    return(digest::digest(file = path, algo = "sha256"))
  }
  # Both hosted Linux and local macOS have a system fallback; no new R dependency.
  for (command in c("sha256sum", "shasum")) {
    executable <- Sys.which(command)
    if (!nzchar(executable)) next
    args <- c(if (command == "shasum") c("-a", "256"), shQuote(path))
    output <- suppressWarnings(system2(executable, args, stdout = TRUE, stderr = TRUE))
    if (length(output) && is.null(attr(output, "status"))) {
      hash <- sub("\\s+.*$", "", output[[1]])
      if (grepl("^[0-9a-fA-F]{64}$", hash)) return(tolower(hash))
    }
  }
  stop("Delta Ops PDF diagnostics require digest, sha256sum or shasum.", call. = FALSE)
}

delta_ops_pdf_envelope <- function(path) {
  bytes <- if (file.exists(path)) as.numeric(file.info(path)$size) else 0
  magic <- tail <- raw()
  if (bytes > 0) {
    con <- file(path, "rb")
    on.exit(close(con), add = TRUE)
    magic <- readBin(con, "raw", n = 5L)
    seek(con, max(0, bytes - 2048), origin = "start")
    tail <- readBin(con, "raw", n = 2048L)
  }
  # Only PDF whitespace may follow the final marker. An interior %%EOF from an
  # earlier incremental revision does not prove a complete download.
  nonspace <- which(!as.integer(tail) %in% c(0L, 9L, 10L, 12L, 13L, 32L))
  end <- if (length(nonspace)) max(nonspace) else 0L
  has_eof <- end >= 6L && identical(tail[(end - 4L):end], charToRaw("%%EOF")) &&
    as.integer(tail[end - 5L]) %in% c(10L, 13L)
  list(bytes = bytes, has_magic = identical(magic, charToRaw("%PDF-")),
       has_eof = has_eof, sha256 = delta_ops_pdf_sha256(path))
}

delta_ops_pdf_log_value <- function(value, limit = 200L) {
  if (is.null(value) || !length(value) || is.na(value[[1]]) || !nzchar(value[[1]])) {
    return("<missing>")
  }
  substr(gsub("[[:cntrl:][:space:]]+", "_", as.character(value[[1]])), 1L, limit)
}

delta_ops_pdf_log_url <- function(url) {
  # Strip all query/fragment values and userinfo BEFORE bounding the URL.
  url <- sub("[?#].*$", "", url)
  url <- sub("^(https?://)[^/]*@", "\\1", url, ignore.case = TRUE)
  delta_ops_pdf_log_value(url, 500L)
}

delta_ops_pdf_request <- function(url, path) {
  handle <- curl::new_handle(
    followlocation = TRUE, maxredirs = 5L, failonerror = FALSE,
    connecttimeout = 10, timeout = 30,
    useragent = "BRIM-live-data-feeds Delta-Ops (+https://github.com/dbo99/brim-live-data-feeds)"
  )
  curl::handle_setheaders(handle, Accept = "application/pdf")
  response <- curl::curl_fetch_disk(url, path, handle = handle)
  list(status = response$status_code, mime = response$type, final_url = response$url)
}

delta_ops_pdf_retryable_error <- function(error) {
  # Do not retry malformed URLs, certificate failures, file errors or code bugs.
  any(class(error) %in% paste0("curl_error_", c(
    "couldnt_resolve_proxy", "couldnt_resolve_host", "couldnt_connect",
    "operation_timedout", "send_error", "recv_error", "got_nothing",
    "partial_file", "http2", "http2_stream", "http3", "quic_connect_error",
    "again"
  )))
}

delta_ops_fetch_pdf_text <- function(url, pdf_reader = pdftools::pdf_text,
                                   request_once = delta_ops_pdf_request,
                                   sleep_fun = Sys.sleep) {
  pdf_tmp <- tempfile("delta-ops-source-", fileext = ".pdf")
  on.exit(unlink(pdf_tmp), add = TRUE)
  for (attempt in 1:3) {
    unlink(pdf_tmp) # A failed attempt must not reuse a prior response's bytes.
    response <- tryCatch(request_once(url, pdf_tmp), error = identity)
    envelope <- delta_ops_pdf_envelope(pdf_tmp)
    status <- NA_integer_
    mime <- NA_character_
    final_url <- NA_character_
    failures <- character()
    if (inherits(response, "error")) {
      outcome <- "transport_failure"
      failures <- class(response)[[1]] # Never log the raw exception/body/headers.
      retryable <- delta_ops_pdf_retryable_error(response)
      terminal <- "DELTA_OPS_UPSTREAM_TRANSPORT_FAILURE"
    } else {
      if (length(response$status)) status <- suppressWarnings(as.integer(response$status[[1]]))
      if (length(response$mime) && !is.na(response$mime[[1]])) {
        mime <- tolower(trimws(sub(";.*$", "", response$mime[[1]])))
        if (!nzchar(mime)) mime <- NA_character_
      }
      final_url <- response$final_url
      success <- !is.na(status) && status >= 200L && status < 300L
      if (success) {
        if (!is.na(mime) && !mime %in% c("application/pdf", "application/x-pdf",
                                       "application/octet-stream", "binary/octet-stream")) {
          failures <- c(failures, "incompatible_content_type")
        }
        if (envelope$bytes < 1024) failures <- c(failures, "implausible_byte_count")
        if (!envelope$has_magic) failures <- c(failures, "missing_pdf_magic")
        if (!envelope$has_eof) failures <- c(failures, "missing_pdf_eof")
        outcome <- if (length(failures)) "non_pdf_response" else "pdf_validated"
        retryable <- length(failures) > 0L
        terminal <- "DELTA_OPS_UPSTREAM_NON_PDF_RESPONSE"
      } else {
        failures <- if (is.na(status)) "missing_http_status" else paste0("http_status_", status)
        outcome <- "http_failure"
        retryable <- is.na(status) || status %in% c(408L, 425L, 429L) ||
          (status >= 500L && status <= 599L)
        terminal <- "DELTA_OPS_UPSTREAM_HTTP_RESPONSE"
      }
    }
    delay <- if (retryable && attempt < 3L) attempt else NA_integer_
    metadata <- paste0(
      "attempt=", attempt, "/3 outcome=", outcome,
      " status=", if (is.na(status)) "<missing>" else status,
      " content_type=", delta_ops_pdf_log_value(mime),
      " bytes=", format(envelope$bytes, scientific = FALSE, trim = TRUE),
      " sha256=", delta_ops_pdf_log_value(envelope$sha256),
      " source_url=", delta_ops_pdf_log_url(url),
      " final_url=", delta_ops_pdf_log_url(final_url),
      " validation=", if (length(failures)) delta_ops_pdf_log_value(paste(failures, collapse = ",")) else "passed",
      if (!is.na(delay)) paste0(" retry_in_seconds=", delay) else ""
    )
    message("DELTA_OPS_RETRIEVAL_ATTEMPT ", metadata)
    if (!length(failures)) return(pdf_reader(pdf_tmp))
    if (is.na(delay)) stop(terminal, " ", metadata, call. = FALSE)
    sleep_fun(delay)
  }
}
