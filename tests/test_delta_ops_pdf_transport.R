#!/usr/bin/env Rscript
# Deterministic transport fixtures; no network and no canonical output writes.
source("scripts/delta_ops_pdf_transport.R")

main <- function() {
  valid <- c(charToRaw("%PDF-1.7\n"), rep(as.raw(32), 1400), charToRaw("\n%%EOF\r\n"))
  rejection <- charToRaw(paste0("<html><title>Request Rejected</title>",
                               "synthetic-body-marker</html>"))
  response <- function(body = valid, status = 200L, mime = "application/pdf") {
    list(body = body, status = status, mime = mime,
         final_url = "https://example.invalid/final.pdf?opaque=synthetic-query-marker#fragment")
  }
  transport_error <- function(kind) {
    structure(simpleError("synthetic-exception-marker"),
              class = c(paste0("curl_error_", kind), "curl_error", "error", "condition"))
  }
  run <- function(responses, reader = function(path) "decoded") {
    calls <- reads <- 0L
    delays <- integer()
    paths <- logs <- character()
    value <- withCallingHandlers(tryCatch(delta_ops_fetch_pdf_text(
      "https://example.invalid/source.pdf?opaque=synthetic-query-marker#fragment",
      pdf_reader = function(path) {
        reads <<- reads + 1L
        stopifnot(file.exists(path))
        reader(path)
      },
      request_once = function(url, path) {
        calls <<- calls + 1L
        paths <<- c(paths, path)
        stopifnot(!file.exists(path))
        item <- responses[[min(calls, length(responses))]]
        if (inherits(item, "error")) stop(item)
        writeBin(item$body, path)
        item$body <- NULL
        item
      },
      sleep_fun = function(delay) delays <<- c(delays, delay)
    ), error = identity), message = function(m) {
      logs <<- c(logs, conditionMessage(m))
      invokeRestart("muffleMessage")
    })
    stopifnot(!any(file.exists(paths)), all(nchar(logs) < 1800L))
    text <- paste(c(logs, if (inherits(value, "error")) conditionMessage(value)), collapse = "\n")
    stopifnot(!grepl("synthetic-(body|exception|query)-marker|#fragment", text))
    list(value = value, calls = calls, reads = reads, delays = delays, logs = logs)
  }
  failure <- function(result, code, calls = 3L, validation = NULL) {
    stopifnot(inherits(result$value, "error"),
              grepl(code, conditionMessage(result$value), fixed = TRUE),
              result$calls == calls, result$reads == 0L,
              identical(result$delays, if (calls == 3L) 1:2 else integer()))
    if (!is.null(validation)) stopifnot(grepl(validation, conditionMessage(result$value), fixed = TRUE))
  }
  for (mime in list("application/pdf", " APPLICATION/PDF ; charset=binary", "application/x-pdf",
                   "application/octet-stream", "binary/octet-stream", NA_character_, NULL, "", "  ")) {
    result <- run(list(response(mime = mime)))
    stopifnot(identical(result$value, "decoded"), result$calls == 1L, result$reads == 1L)
  }
  # Reject HTML even with lying/missing MIME; reject a PDF with explicit wrong MIME.
  for (case in list(
    list(response(rejection, mime = "text/html"), "incompatible_content_type"),
    list(response(rejection), "missing_pdf_magic"),
    list(response(c(rejection, rep(as.raw(32), 2000)), mime = NA_character_), "missing_pdf_magic"),
    list(response(mime = "text/plain"), "incompatible_content_type"),
    list(response(charToRaw("%PDF-1.7\n%%EOF\n")), "implausible_byte_count"),
    list(response(c(charToRaw("wrong"), valid[-(1:5)])), "missing_pdf_magic"),
    list(response(valid[1:1409]), "missing_pdf_eof"),
    list(response(c(valid, charToRaw("unfinished incremental revision"))), "missing_pdf_eof"),
    list(response(raw()), "implausible_byte_count")
  )) failure(run(list(case[[1]])), "DELTA_OPS_UPSTREAM_NON_PDF_RESPONSE", validation = case[[2]])

  # Exact size boundary and 2xx policy, not merely curl's usual >=400 rejection.
  minimum <- c(charToRaw("%PDF-1.7\n"), rep(as.raw(32), 1009), charToRaw("\n%%EOF"))
  stopifnot(length(minimum) == 1024L, run(list(response(minimum, 206L)))$reads == 1L)
  failure(run(list(response(minimum[-9L]))), "DELTA_OPS_UPSTREAM_NON_PDF_RESPONSE")
  for (status in c(408L, 425L, 429L, 500L, 502L, 503L, 504L, 599L, NA_integer_)) {
    failure(run(list(response(status = status))), "DELTA_OPS_UPSTREAM_HTTP_RESPONSE")
  }
  for (status in c(199L, 301L, 304L, 400L, 401L, 403L, 404L, 600L)) {
    failure(run(list(response(status = status))), "DELTA_OPS_UPSTREAM_HTTP_RESPONSE", 1L)
  }
  for (kind in c("couldnt_resolve_proxy", "couldnt_resolve_host", "couldnt_connect",
                 "operation_timedout", "send_error", "recv_error", "got_nothing",
                 "partial_file", "http2", "http2_stream", "http3", "quic_connect_error", "again")) {
    failure(run(list(transport_error(kind))), "DELTA_OPS_UPSTREAM_TRANSPORT_FAILURE")
  }
  for (error in list(transport_error("url_malformat"), transport_error("peer_failed_verification"),
                     transport_error("write_error"), simpleError("synthetic-exception-marker"))) {
    failure(run(list(error)), "DELTA_OPS_UPSTREAM_TRANSPORT_FAILURE", 1L)
  }
  for (first in list(response(rejection, mime = "text/html"), response(status = 503L),
                     transport_error("operation_timedout"))) {
    result <- run(list(first, response()))
    stopifnot(result$calls == 2L, result$reads == 1L, identical(result$delays, 1L))
  }
  result <- run(list(response(status = 429L), transport_error("recv_error"), response()))
  stopifnot(result$calls == 3L, result$reads == 1L, identical(result$delays, 1:2))
  result <- run(list(response()), reader = function(path) stop("synthetic-parser-error"))
  stopifnot(inherits(result$value, "error"), result$calls == 1L, result$reads == 1L,
            grepl("synthetic-parser-error", conditionMessage(result$value)), !length(result$delays))

  # Hash fallback works with spaces and shell metacharacters in a temporary path.
  work <- tempfile("delta-pdf-tests-")
  dir.create(work)
  on.exit(unlink(work, recursive = TRUE), add = TRUE)
  path <- file.path(work, "synthetic file $(unused).pdf")
  writeBin(charToRaw("abc"), path)
  scope <- new.env(parent = globalenv())
  sys.source("scripts/delta_ops_pdf_transport.R", scope)
  scope$requireNamespace <- function(...) FALSE
  stopifnot(scope$delta_ops_pdf_sha256(path) ==
              "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
  log <- run(list(response()))$logs[[1]]
  stopifnot(grepl("status=200 content_type=application/pdf bytes=1417 sha256=[a-f0-9]{64}", log),
            grepl("source_url=https://example.invalid/source.pdf", log, fixed = TRUE),
            grepl("final_url=https://example.invalid/final.pdf", log, fixed = TRUE))
  stopifnot(nchar(delta_ops_pdf_log_url(paste0("https://example.invalid/", strrep("x", 2000)))) == 500L,
            delta_ops_pdf_log_url("https://synthetic-user@example.invalid/a?arbitrary=omitted") ==
              "https://example.invalid/a")
  message("PASS: PDF envelope/MIME/status, retries/backoff, cleanup, parser errors, bounded sanitized diagnostics and SHA-256")
}

main()
