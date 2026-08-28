#!/usr/bin/env Rscript

source("scripts/delta_ops_pdf_transport.R")

assert_true <- function(value, message) {
  if (!isTRUE(value)) stop(message, call. = FALSE)
}

assert_equal <- function(actual, expected, message) {
  if (!identical(actual, expected)) {
    stop(
      message,
      "\nExpected: ", paste(capture.output(str(expected)), collapse = " "),
      "\nActual: ", paste(capture.output(str(actual)), collapse = " "),
      call. = FALSE
    )
  }
}

scenario <- function(name, code) {
  force(code)
  message("PASS: ", name)
}

capture_run <- function(code) {
  messages <- character()
  error <- withCallingHandlers(
    tryCatch(
      {
        force(code)
        NULL
      },
      error = function(error) error
    ),
    message = function(message) {
      messages <<- c(messages, conditionMessage(message))
      invokeRestart("muffleMessage")
    }
  )
  list(error = error, messages = messages)
}

assert_failure <- function(result, pattern, message) {
  if (!inherits(result$error, "error") ||
      !grepl(pattern, conditionMessage(result$error), fixed = TRUE)) {
    stop(
      message,
      "\nExpected error containing: ", pattern,
      "\nActual: ",
      if (inherits(result$error, "error")) conditionMessage(result$error) else "no error",
      call. = FALSE
    )
  }
}

valid_transport_bytes <- c(
  charToRaw("%PDF-1.7\n"),
  rep(as.raw(0x20), 1400L),
  charToRaw("\n%%EOF\n")
)

historical_prefix <- charToRaw(paste0(
  "<html><head><title>Request Rejected</title></head>",
  "<body>DWR Request Rejected synthetic-support-marker"
))
historical_rejection_bytes <- c(
  historical_prefix,
  rep(charToRaw("x"), 244L - length(historical_prefix))
)
stopifnot(length(historical_rejection_bytes) == 244L)

larger_html_bytes <- c(
  charToRaw("<html><head><title>Maintenance</title></head><body>"),
  rep(charToRaw("m"), 3000L),
  charToRaw("</body></html>")
)
arbitrary_bytes <- rep(as.raw(0x41), 1800L)
truncated_pdf_bytes <- c(
  charToRaw("%PDF-1.7\n"),
  rep(as.raw(0x20), 1800L)
)

http_response <- function(status, body, content_type = "application/pdf",
                          final_url = "https://water.ca.gov/final.pdf") {
  list(
    status_code = as.integer(status),
    content_type = content_type,
    final_url = final_url,
    body = body
  )
}

sequence_request <- function(responses) {
  state <- new.env(parent = emptyenv())
  state$calls <- 0L
  state$request <- function(url, path, timeout_sec, connect_timeout_sec, user_agent) {
    state$calls <- state$calls + 1L
    value <- responses[[min(state$calls, length(responses))]]
    if (inherits(value, "error")) stop(value)
    writeBin(value$body, path)
    value$body <- NULL
    value
  }
  state
}

tracking_reader <- function(result = "parsed") {
  state <- new.env(parent = emptyenv())
  state$calls <- 0L
  state$reader <- function(path) {
    state$calls <- state$calls + 1L
    assert_true(file.exists(path), "Validated PDF path was not available to the parser.")
    result
  }
  state
}

fetch <- function(request, reader, sleep_fun = function(seconds) NULL) {
  delta_ops_fetch_pdf_text(
    url = "https://water.ca.gov/source.pdf",
    pdf_reader = reader$reader,
    request_once = request$request,
    sleep_fun = sleep_fun
  )
}

scenario("SHA-256 diagnostics work without an additional R package", {
  local({
    fixture <- tempfile("delta-ops-sha256-")
    on.exit(unlink(fixture, force = TRUE), add = TRUE)
    writeBin(charToRaw("abc"), fixture)
    had_override <- exists("requireNamespace", envir = .GlobalEnv, inherits = FALSE)
    previous_override <- if (had_override) get("requireNamespace", envir = .GlobalEnv) else NULL
    assign("requireNamespace", function(package, quietly = FALSE) FALSE, envir = .GlobalEnv)
    on.exit({
      if (had_override) {
        assign("requireNamespace", previous_override, envir = .GlobalEnv)
      } else {
        rm("requireNamespace", envir = .GlobalEnv)
      }
    }, add = TRUE)
    assert_equal(
      delta_ops_sha256_file(fixture),
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
      "System SHA-256 fallback changed."
    )
  })
})

scenario("valid PDF with application/pdf reaches the parser once", {
  request <- sequence_request(list(http_response(200, valid_transport_bytes)))
  reader <- tracking_reader("valid-pdf")
  result <- fetch(request, reader)
  assert_equal(result, "valid-pdf", "Validated PDF parser result changed.")
  assert_equal(request$calls, 1L, "Valid PDF should need one request.")
  assert_equal(reader$calls, 1L, "Valid PDF should reach the parser once.")
})

scenario("valid PDF with missing MIME is accepted by magic and envelope", {
  request <- sequence_request(list(http_response(
    200,
    valid_transport_bytes,
    content_type = NA_character_
  )))
  reader <- tracking_reader("missing-mime")
  assert_equal(fetch(request, reader), "missing-mime", "Missing-MIME PDF was rejected.")
  assert_equal(reader$calls, 1L, "Missing-MIME valid PDF did not reach the parser.")
})

scenario("historical 244-byte DWR rejection is stopped before Poppler", {
  legacy_reader <- tracking_reader("legacy")
  legacy_path <- tempfile(fileext = ".pdf")
  on.exit(unlink(legacy_path, force = TRUE), add = TRUE)
  writeBin(historical_rejection_bytes, legacy_path)
  legacy_reader$reader(legacy_path)
  assert_equal(
    legacy_reader$calls,
    1L,
    "Historical direct-download behavior should demonstrate parser exposure."
  )

  request <- sequence_request(list(http_response(
    200,
    historical_rejection_bytes,
    content_type = "text/html"
  )))
  reader <- tracking_reader()
  result <- capture_run(fetch(request, reader))
  assert_failure(
    result,
    "DELTA_OPS_UPSTREAM_NON_PDF_RESPONSE",
    "Historical DWR rejection did not receive the precise upstream diagnostic."
  )
  assert_equal(request$calls, 3L, "Historical rejection did not use three bounded attempts.")
  assert_equal(reader$calls, 0L, "Historical rejection reached the parser.")
  diagnostic <- paste(c(result$messages, conditionMessage(result$error)), collapse = "\n")
  assert_true(grepl("attempt=3/3", diagnostic, fixed = TRUE), "Terminal attempt was not logged.")
  assert_true(grepl("content_type=text/html", diagnostic, fixed = TRUE), "MIME was not logged.")
  assert_true(grepl("bytes=244", diagnostic, fixed = TRUE), "Historical byte count was not logged.")
  assert_true(grepl("sha256=", diagnostic, fixed = TRUE), "Response SHA-256 was not logged.")
  assert_true(
    !grepl("synthetic-support-marker", diagnostic, fixed = TRUE),
    "Non-PDF response body leaked into diagnostics."
  )
})

for (case in list(
  list(name = "larger HTML page", body = larger_html_bytes, type = "text/html"),
  list(name = "arbitrary non-PDF bytes", body = arbitrary_bytes, type = "application/octet-stream"),
  list(name = "truncated PDF-like response", body = truncated_pdf_bytes, type = "application/pdf")
)) {
  local({
    current <- case
    scenario(paste(current$name, "never reaches the parser"), {
      request <- sequence_request(list(http_response(
        200,
        current$body,
        content_type = current$type
      )))
      reader <- tracking_reader()
      result <- capture_run(fetch(request, reader))
      assert_failure(
        result,
        "DELTA_OPS_UPSTREAM_NON_PDF_RESPONSE",
        paste(current$name, "did not fail as a non-PDF response.")
      )
      assert_equal(request$calls, 3L, paste(current$name, "retry count is not bounded at three."))
      assert_equal(reader$calls, 0L, paste(current$name, "reached the parser."))
    })
  })
}

for (status in c(429L, 500L, 502L, 503L)) {
  local({
    current_status <- status
    scenario(paste("HTTP", current_status, "retries three times then fails closed"), {
      request <- sequence_request(list(http_response(
        current_status,
        arbitrary_bytes,
        content_type = "text/plain"
      )))
      reader <- tracking_reader()
      result <- capture_run(fetch(request, reader))
      assert_failure(
        result,
        "DELTA_OPS_UPSTREAM_HTTP_RESPONSE",
        paste("HTTP", current_status, "did not receive the HTTP upstream diagnostic.")
      )
      assert_equal(request$calls, 3L, paste("HTTP", current_status, "attempt count changed."))
      assert_equal(reader$calls, 0L, paste("HTTP", current_status, "reached the parser."))
    })
  })
}

for (failure in list(
  list(name = "timeout", error = structure(simpleError("operation timed out"), class = c("curl_error_operation_timedout", "error", "condition"))),
  list(name = "connection reset", error = structure(simpleError("connection reset"), class = c("curl_error_recv_error", "error", "condition")))
)) {
  local({
    current <- failure
    scenario(paste(current$name, "is retried and remains pre-parser"), {
      request <- sequence_request(list(current$error))
      reader <- tracking_reader()
      result <- capture_run(fetch(request, reader))
      assert_failure(
        result,
        "DELTA_OPS_UPSTREAM_TRANSPORT_FAILURE",
        paste(current$name, "did not receive the transport diagnostic.")
      )
      assert_equal(request$calls, 3L, paste(current$name, "attempt count changed."))
      assert_equal(reader$calls, 0L, paste(current$name, "reached the parser."))
    })
  })
}

scenario("success after one retry parses normally", {
  delays <- numeric()
  request <- sequence_request(list(
    http_response(200, historical_rejection_bytes, content_type = "text/html"),
    http_response(200, valid_transport_bytes)
  ))
  reader <- tracking_reader("retry-success")
  result <- fetch(request, reader, function(seconds) delays <<- c(delays, seconds))
  assert_equal(result, "retry-success", "Second-attempt success was not parsed.")
  assert_equal(request$calls, 2L, "Second-attempt success request count changed.")
  assert_equal(reader$calls, 1L, "Second-attempt success parser count changed.")
  assert_equal(delays, 1, "First retry delay changed.")
})

scenario("success on the final allowed retry parses normally", {
  delays <- numeric()
  request <- sequence_request(list(
    http_response(500, arbitrary_bytes, content_type = "text/plain"),
    http_response(429, arbitrary_bytes, content_type = "text/plain"),
    http_response(200, valid_transport_bytes)
  ))
  reader <- tracking_reader("final-success")
  result <- fetch(request, reader, function(seconds) delays <<- c(delays, seconds))
  assert_equal(result, "final-success", "Final-attempt success was not parsed.")
  assert_equal(request$calls, 3L, "Final-attempt success request count changed.")
  assert_equal(reader$calls, 1L, "Final-attempt success parser count changed.")
  assert_equal(delays, c(1, 2), "Increasing retry delays changed.")
})

scenario("terminal retrieval failure creates no candidate or publication marker", {
  output_root <- tempfile("delta-ops-failed-candidate-")
  dir.create(output_root)
  on.exit(unlink(output_root, recursive = TRUE, force = TRUE), add = TRUE)
  candidate_marker <- file.path(output_root, "candidate-created")
  publication_marker <- file.path(output_root, "publication-created")
  request <- sequence_request(list(http_response(
    200,
    historical_rejection_bytes,
    content_type = "text/html"
  )))
  reader <- tracking_reader()
  reader$reader <- function(path) {
    reader$calls <- reader$calls + 1L
    file.create(candidate_marker)
    file.create(publication_marker)
    "unexpected"
  }
  result <- capture_run(fetch(request, reader))
  assert_failure(result, "DELTA_OPS_UPSTREAM_NON_PDF_RESPONSE", "Failure did not stop the build.")
  assert_true(!file.exists(candidate_marker), "Candidate marker was created after retrieval failure.")
  assert_true(!file.exists(publication_marker), "Publication marker was created after retrieval failure.")
})

scenario("valid PDF bytes preserve Poppler text semantics", {
  if (!requireNamespace("pdftools", quietly = TRUE)) {
    message("SKIP: pdftools is unavailable for the valid-PDF Poppler regression.")
  } else {
    fixture_pdf <- tempfile("delta-ops-valid-regression-", fileext = ".pdf")
    on.exit(unlink(fixture_pdf, force = TRUE), add = TRUE)
    grDevices::pdf(fixture_pdf, width = 8.5, height = 11, useDingbats = FALSE)
    graphics::plot.new()
    graphics::text(
      0.05,
      0.95,
      labels = paste(
        "EXECUTIVE OPERATIONS SUMMARY ON 08/20/2026",
        "SCHEDULED EXPORTS FOR TODAY",
        "Clifton Court Inflow = 300 cfs",
        "Jones Pumping Plant = 900 cfs",
        sep = "\n"
      ),
      adj = c(0, 1)
    )
    grDevices::dev.off()

    fixture_connection <- file(fixture_pdf, open = "rb")
    fixture_bytes <- readBin(
      fixture_connection,
      what = "raw",
      n = file.info(fixture_pdf)$size
    )
    close(fixture_connection)
    direct_text <- pdftools::pdf_text(fixture_pdf)

    request <- sequence_request(list(http_response(200, fixture_bytes)))
    reader <- new.env(parent = emptyenv())
    reader$calls <- 0L
    reader$reader <- function(path) {
      reader$calls <- reader$calls + 1L
      pdftools::pdf_text(path)
    }
    hardened_text <- fetch(request, reader)
    assert_equal(
      hardened_text,
      direct_text,
      "Hardened retrieval changed valid-PDF Poppler text semantics."
    )
    assert_equal(reader$calls, 1L, "Valid-PDF regression did not call Poppler exactly once.")
  }
})

message("Delta Ops PDF transport tests passed.")
