# One serial SCAN request account. Limits are supplied by the caller; this file
# grants no provider authority. Body counts are decoded bytes returned by curl,
# not TLS/wire bytes. An EOF probe can consume at most one byte beyond a cap.
pt_scan_fatal <- function(e) {
  inherits(e, c("scan_fatal", "scan_budget_exhausted", "scan_deadline_exceeded",
                "scan_accounting_error", "scan_integrity_error"))
}

pt_scan_condition <- function(message, class = "scan_integrity_error") {
  structure(list(message = message, call = NULL),
            class = c(class, "scan_fatal", "error", "condition"))
}

# Separate, injectable physical backend. Production uses a fresh native handle
# per attempt: no redirects, proxy, shared handle, automatic retry or auth retry.
# The installed curl connection supports bounded readBin(), including a one-byte
# EOF probe; no full-body httr::content() or curl_fetch_memory() occurs here.
pt_scan_curl_backend <- function(method, url, form, timeout, read_limit, consume, check) {
  h <- curl::new_handle()
  curl::handle_setopt(h, followlocation = FALSE, maxredirs = 0L, proxy = "",
                      fresh_connect = TRUE, forbid_reuse = TRUE,
                      http_version = 2L, accept_encoding = "")
  curl::handle_setheaders(h, "User-Agent" = "brim-live-data-feeds SCAN",
                         "Referer" = "https://wcc.sc.egov.usda.gov/nwcc/")
  if (identical(method, "POST")) {
    encoded <- paste(paste0(curl::curl_escape(names(form)), "=",
                            curl::curl_escape(vapply(form, as.character, character(1)))),
                     collapse = "&")
    curl::handle_setopt(h, post = TRUE, postfields = encoded)
    curl::handle_setheaders(h, "Content-Type" = "application/x-www-form-urlencoded",
                           "User-Agent" = "brim-live-data-feeds SCAN",
                           "Referer" = "https://wcc.sc.egov.usda.gov/nwcc/",
                           "Expect" = "")
  }
  con <- curl::curl(url, open = "", handle = h)
  on.exit(close(con), add = TRUE)
  # Reclip after local handle/form setup, immediately before the native call.
  # check() returns time left to the fixed absolute request/campaign deadline.
  timeout <- min(timeout, check())
  curl::handle_setopt(h, timeout_ms = max(1L, as.integer(floor(timeout * 1000))),
                      connecttimeout_ms = max(1L, as.integer(floor(timeout * 1000))))
  open(con, "rbf")
  repeat {
    check()
    block <- readBin(con, "raw", n = read_limit())
    if (length(block)) consume(block)
    if (!isIncomplete(con)) break
  }
  check()
  result <- curl::handle_data(h)
  list(status = result$status_code, headers = result$headers, complete = TRUE)
}

pt_scan_make_http <- function(max_requests, response_bytes, total_bytes,
                              campaign_seconds, request_seconds,
                              backend = pt_scan_curl_backend,
                              clock = function() unname(proc.time()[["elapsed"]])) {
  integer_limit <- function(x) is.numeric(x) && length(x) == 1L && is.finite(x) &&
    x >= 1 && x <= 2^31 - 1 && x == floor(x)
  if (!all(vapply(list(max_requests, response_bytes, total_bytes), integer_limit, logical(1))) ||
      !all(vapply(list(campaign_seconds, request_seconds), function(x)
        is.numeric(x) && length(x) == 1L && is.finite(x) && x > 0 && x <= 86400,
        logical(1))) || !is.function(backend) || !is.function(clock)) {
    stop(pt_scan_condition("SCAN requires explicit finite request/body/time limits"))
  }
  requests <- 0L; consumed <- 0; receipts <- list(); fatal <- NULL
  deadline <- clock() + campaign_seconds
  fail <- function(message, class = "scan_integrity_error") {
    if (is.null(fatal)) fatal <<- pt_scan_condition(message, class)
    stop(fatal)
  }
  check <- function() {
    if (!is.null(fatal)) stop(fatal)
    now <- clock()
    if (length(now) != 1L || !is.finite(now)) fail("SCAN clock is unavailable", "scan_accounting_error")
    if (now >= deadline) fail("SCAN campaign deadline reached", "scan_deadline_exceeded")
    invisible(TRUE)
  }
  request <- function(method, url, form = NULL, timeout = request_seconds) {
    check()
    if (!identical(method, "GET") && !identical(method, "POST")) fail("Undeclared SCAN HTTP method")
    expected <- if (method == "POST") "https://wcc.sc.egov.usda.gov/nwcc/view" else "https://wcc.sc.egov.usda.gov/"
    if (!identical(url, expected)) fail("Undeclared SCAN HTTP endpoint")
    if (method == "GET" && !is.null(form)) fail("Unexpected SCAN GET body")
    if (method == "POST" && (!is.list(form) || !identical(names(form),
        c("intervalType", "report", "timeseries", "format", "sitenum", "interval", "year", "month")) ||
        !all(vapply(form, function(x) length(x) == 1L && !is.na(x), logical(1))))) fail("Invalid SCAN form")
    if (!is.numeric(timeout) || length(timeout) != 1L || !is.finite(timeout) || timeout <= 0) fail("Invalid SCAN timeout")
    if (requests >= max_requests || consumed >= total_bytes) fail("SCAN request/body budget exhausted", "scan_budget_exhausted")
    admitted <- clock()
    request_deadline <- min(deadline, admitted + timeout, admitted + request_seconds)
    remaining <- request_deadline - admitted
    if (remaining < .001) fail("SCAN deadline leaves no dispatch time", "scan_deadline_exceeded")
    # Reserve BEFORE invoking the physical backend. Failed/unknown work stays spent.
    requests <<- requests + 1L
    id <- requests; bytes <- 0; chunks <- list()
    receipts[[id]] <<- list(attempt = id, method = method, state = "UNKNOWN", body_bytes = 0,
                           body_allowance_reserved = min(response_bytes, total_bytes - consumed),
                           timeout_seconds = remaining, status = NULL)
    on.exit(message("SCAN_HTTP attempt=", id, " method=", method,
                    " state=", receipts[[id]]$state, " decoded_body_bytes=", bytes,
                    " cumulative_attempts=", requests, " cumulative_decoded_body_bytes=", consumed), add = TRUE)
    request_check <- function() {
      check()
      left <- request_deadline - clock()
      if (left < .001) fail("SCAN request deadline reached", "scan_deadline_exceeded")
      left
    }
    read_limit <- function() as.integer(min(32768, response_bytes - bytes + 1,
                                             total_bytes - consumed + 1))
    consume <- function(block) {
      if (!is.raw(block)) fail("SCAN backend returned non-byte data", "scan_accounting_error")
      expected_limit <- read_limit()
      n <- length(block); bytes <<- bytes + n; consumed <<- consumed + n
      receipts[[id]]$body_bytes <<- bytes
      if (n > expected_limit) fail("SCAN backend exceeded read contract", "scan_accounting_error")
      if (bytes > response_bytes || consumed > total_bytes) fail("SCAN response/body cap exceeded (EOF probe included)", "scan_budget_exhausted")
      request_check()
      chunks[[length(chunks) + 1L]] <<- block
      invisible(NULL)
    }
    result <- tryCatch(backend(method, url, form, remaining, read_limit, consume, request_check),
      error = function(e) {
        if (pt_scan_fatal(e)) { fatal <<- e; stop(e) }
        fail(paste("SCAN transport consumption is unreconciled:", conditionMessage(e)), "scan_accounting_error")
      })
    request_check()
    if (!is.list(result) || !identical(result$complete, TRUE) ||
        length(result$status) != 1L || !is.finite(result$status) || result$status < 100 || result$status > 599) {
      fail("SCAN backend did not establish a complete response", "scan_accounting_error")
    }
    receipts[[id]]$status <<- result$status
    receipts[[id]]$state <<- "COMPLETE"
    # A completed HTTP response may still instruct the caller to back off.
    # Stop this campaign rather than retry sooner or invent a new waiting policy.
    if (!is.null(result$headers)) {
      if (!is.raw(result$headers)) fail("SCAN response headers are unverified", "scan_accounting_error")
      lines <- strsplit(rawToChar(result$headers), "\r\n|\n", perl = TRUE)[[1L]]
      if (any(grepl("^Retry-After[[:space:]]*:", lines, ignore.case = TRUE))) {
        fail("SCAN provider Retry-After; no retry in this campaign", "scan_provider_refusal")
      }
    }
    if (result$status >= 300 && result$status < 400) fail("SCAN redirects are forbidden")
    if (result$status %in% c(401L, 403L, 429L)) fail("SCAN provider refusal; no retry", "scan_provider_refusal")
    if (result$status < 200 || result$status >= 300) {
      stop(structure(list(message = paste("SCAN HTTP status", result$status), call = NULL),
                     class = c("scan_http_error", "error", "condition")))
    }
    list(status = result$status, body = if (length(chunks)) do.call(c, chunks) else raw(),
         body_bytes = bytes)
  }
  list(request = request, check = check, fail = fail,
       snapshot = function() list(requests = requests, body_bytes = consumed,
         fatal = if (is.null(fatal)) NULL else class(fatal)[[1L]], receipts = receipts,
         byte_scope = "decoded body bytes delivered by curl; at most one EOF-probe byte over cap"))
}

pt_scan_http_from_env <- function() {
  required <- function(name) suppressWarnings(as.numeric(Sys.getenv(name, unset = NA_character_)))
  pt_scan_make_http(required("SCAN_HTTP_MAX_REQUESTS"), required("SCAN_HTTP_RESPONSE_BYTES"),
    required("SCAN_HTTP_TOTAL_BYTES"), required("SCAN_HTTP_CAMPAIGN_SECONDS"),
    required("SCAN_FETCH_TIMEOUT_SEC"), backend = getOption("brim.scan.http.backend", pt_scan_curl_backend))
}
