# Run from repository root. All default transports are in-process saved bytes.
# Optional SCAN_TEST_LOOPBACK_PORT enables only a separately supervised fixture
# server; the test harness must independently deny every other network route.
source("scripts/soil_moisture/scan_metered_http.R")
source("scripts/soil_moisture/scan_nwcc_compat.R")
passed <- 0L
test <- function(name, code) { force(code); passed <<- passed + 1L; cat("PASS", name, "\n") }
reject <- function(code, class) {
  e <- suppressMessages(tryCatch(force(code), error = identity))
  stopifnot(inherits(e, class)); invisible(e)
}
endpoint <- "https://wcc.sc.egov.usda.gov/"
view <- paste0(endpoint, "nwcc/view")
form <- getFromNamespace(".make_SCAN_req", "soilDB")(2218, 2026, "SMS", "Daily")
saved_backend <- function(body = charToRaw("ok"), status = 200L, observer = function(...) NULL) {
  force(body); force(status)
  function(method, url, form, timeout, read_limit, consume, check) {
    observer(method, url, form, timeout)
    pos <- 1L
    while (pos <= length(body)) {
      n <- min(read_limit(), length(body) - pos + 1L)
      consume(body[seq.int(pos, length.out = n)])
      pos <- pos + n
    }
    list(status = status, complete = TRUE)
  }
}
meter <- function(backend = saved_backend(), requests = 10, response = 2^21,
                  total = 2^24, seconds = 30, timeout = 5, clock = function() unname(proc.time()[["elapsed"]])) {
  pt_scan_make_http(requests, response, total, seconds, timeout, backend, clock)
}
test("explicit finite limits and exact endpoint before dispatch", {
  for (bad in list(NA_real_, Inf, 0, 1.5)) reject(meter(requests = bad), "scan_integrity_error")
  h <- meter(); reject(h$request("GET", paste0(endpoint, "unknown")), "scan_integrity_error")
  stopifnot(h$snapshot()$requests == 0)
})
test("reserve before physical attempt; shared GET/POST limit and no refund", {
  h <- meter(requests = 2, backend = saved_backend(observer = function(...) {
    stopifnot(h$snapshot()$requests >= 1)
  }))
  h$request("GET", endpoint); h$request("POST", view, form)
  reject(h$request("POST", view, form), "scan_budget_exhausted")
  stopifnot(h$snapshot()$requests == 2, h$snapshot()$body_bytes == 4)
})
test("exact cap EOF accepted; one-byte response and shared-body overshoot spent", {
  h <- meter(saved_backend(charToRaw("1234")), response = 4)
  stopifnot(identical(h$request("GET", endpoint)$body, charToRaw("1234")))
  h <- meter(saved_backend(charToRaw("123456789")), response = 4)
  reject(h$request("GET", endpoint), "scan_budget_exhausted")
  reject(h$request("GET", endpoint), "scan_budget_exhausted")
  stopifnot(h$snapshot()$requests == 1, h$snapshot()$body_bytes == 5,
            h$snapshot()$receipts[[1]]$state == "UNKNOWN")
  h <- meter(saved_backend(charToRaw("abc")), total = 5)
  h$request("GET", endpoint); reject(h$request("POST", view, form), "scan_budget_exhausted")
  stopifnot(h$snapshot()$body_bytes == 6, h$snapshot()$requests == 2)
})
test("partial/unknown transport is fatal and remains charged", {
  h <- meter(function(method, url, form, timeout, read_limit, consume, check) {
    consume(charToRaw("abc")); stop("synthetic reset")
  })
  reject(h$request("GET", endpoint), "scan_accounting_error")
  reject(h$request("GET", endpoint), "scan_accounting_error")
  stopifnot(h$snapshot()$requests == 1, h$snapshot()$body_bytes == 3,
            h$snapshot()$receipts[[1]]$state == "UNKNOWN")
})
test("error bodies charged; complete transient retry; refusal and redirect latch", {
  h <- meter(saved_backend(charToRaw("error"), 503L))
  reject(h$request("GET", endpoint), "scan_http_error")
  reject(h$request("POST", view, form), "scan_http_error")
  stopifnot(h$snapshot()$requests == 2, h$snapshot()$body_bytes == 10, is.null(h$snapshot()$fatal))
  for (status in c(301L, 307L, 401L, 403L, 429L)) {
    h <- meter(saved_backend(charToRaw("refused"), status))
    reject(h$request("GET", endpoint), "scan_fatal")
    reject(h$request("GET", endpoint), "scan_fatal")
    stopifnot(h$snapshot()$requests == 1, h$snapshot()$body_bytes == 7)
  }
})
test("request clipped to remaining campaign time; expiry before/after body", {
  now <- 0; observed <- NULL
  h <- meter(clock = function() now, seconds = 3, timeout = 5,
    backend = saved_backend(observer = function(method, url, form, timeout) observed <<- timeout))
  now <- 2; h$request("GET", endpoint); stopifnot(observed == 1)
  now <- 3; reject(h$request("GET", endpoint), "scan_deadline_exceeded")
  stopifnot(h$snapshot()$requests == 1)
  now <- 0
  h <- meter(clock = function() now, seconds = 3, backend = function(method, url, form, timeout, read_limit, consume, check) {
    now <<- 4; consume(charToRaw("late")); list(status = 200L, complete = TRUE)
  })
  reject(h$request("GET", endpoint), "scan_deadline_exceeded")
  stopifnot(h$snapshot()$body_bytes == 4)
  now <- 0
  h <- meter(clock = function() now, seconds = 3,
    backend = function(method, url, form, timeout, read_limit, consume, check) {
      stopifnot(timeout == 3)
      now <<- 2
      stopifnot(check() == 1) # Native backend reuses this immediately before open.
      list(status = 200L, complete = TRUE)
    })
  h$request("GET", endpoint)
})
test("provider Retry-After remains a global stop even on 2xx or 503", {
  for (status in c(200L, 503L)) {
    h <- meter(function(method, url, form, timeout, read_limit, consume, check) {
      consume(charToRaw("wait"))
      list(status = status, headers = charToRaw("HTTP/1.1 200 OK\r\nRetry-After: 600\r\n"), complete = TRUE)
    })
    reject(h$request("GET", endpoint), "scan_provider_refusal")
    reject(h$request("GET", endpoint), "scan_provider_refusal")
    stopifnot(h$snapshot()$requests == 1, h$snapshot()$body_bytes == 4)
  }
})

ns <- asNamespace("soilDB")
original <- lapply(c("fetchSCAN", ".get_SCAN_data", "SCAN_site_metadata", ".make_SCAN_req", ".formatSCAN_soil_sensor_suites"), get, envir = ns)
report <- charToRaw(paste(c("Synthetic fixture", "Site Id,Date,Time,SMS.I-1:-2 (pct),SMS.I-1:-8 (pct),SMS.I-2:-8 (pct),",
  "2218,2026-10-01,,0,20,40,", "2218,2026-10-02,,-99.9,NA,NaN,"), collapse = "\n"))
test("actual installed formatter, request fields, zero, sentinel and duplicate depth", {
  seen <- NULL
  h <- meter(saved_backend(report, observer = function(method, url, body, timeout) {
    stopifnot(method == "POST", url == view); seen <<- body
  }))
  f <- pt_scan_make_fetch_compat(h, 2218)
  x <- suppressMessages(f(2218, 2026))
  stopifnot(identical(vapply(seen, as.character, character(1)), vapply(form, as.character, character(1))),
            nrow(x$SMS) == 3, identical(x$SMS$value, c(0, 20, 40)),
            identical(x$SMS$depth, c(5, 20, 20)), all(x$SMS$Date == as.Date("2026-10-01")),
            all(format(x$SMS$datetime, "%H:%M", tz = "UTC") == "20:00"))
})
test("empty/all-invalid/mixed/out-of-scope station requests dispatch zero", {
  for (ids in list(numeric(), 999999, c(2218, 999999), c(2218, NA), c(2218, 2218), "2218", 2218.1)) {
    h <- meter(saved_backend(report)); f <- pt_scan_make_fetch_compat(h, 2218)
    reject(f(ids, 2026), "scan_integrity_error"); stopifnot(h$snapshot()$requests == 0)
  }
  h <- meter(); reject(pt_scan_make_fetch_compat(h, 999999), "scan_integrity_error")
  stopifnot(h$snapshot()$requests == 0)
})
test("installed inner catch preserves fatal type and prevents next station/year", {
  for (kind in c("oversize", "unknown", "parser")) {
    backend <- switch(kind, oversize = saved_backend(report), parser = saved_backend(charToRaw("Access denied")),
      unknown = function(...) stop("synthetic read failure"))
    h <- meter(backend, response = if (kind == "oversize") 10 else 2^21)
    f <- pt_scan_make_fetch_compat(h, c(2218, 2149))
    reject(f(c(2218, 2149), c(2025, 2026)), "scan_fatal")
    stopifnot(h$snapshot()$requests == 1)
  }
})
test("builder outer retry preserves fatal type; complete transient failure stays bounded", {
  suppressPackageStartupMessages(library(dplyr))
  env <- new.env(parent = globalenv())
  expressions <- parse("scripts/build_scan_soil_moisture_latest.R")
  for (expr in expressions) if (is.call(expr) && identical(expr[[1]], as.name("<-")) && is.symbol(expr[[2]]) &&
    as.character(expr[[2]]) %in% c("fetch_one_scan_year", "pt_record_fetch_diag", "pt_check_nrcs_endpoint")) eval(expr, env)
  env$fetch_retries <- 3L; env$fetch_retry_pause_sec <- 0; env$request_pause_sec <- 0
  env$scan_fetch_diag_rows <- list()
  h <- meter(saved_backend(report), response = 10)
  env$pt_scan_fetch <- pt_scan_make_fetch_compat(h, 2218)
  reject(env$fetch_one_scan_year(2218, 2026), "scan_budget_exhausted")
  stopifnot(h$snapshot()$requests == 1, length(env$scan_fetch_diag_rows) == 0)
  h <- meter(saved_backend(charToRaw("busy"), 503L), requests = 2)
  env$pt_scan_fetch <- pt_scan_make_fetch_compat(h, 2218)
  reject(env$fetch_one_scan_year(2218, 2026), "scan_budget_exhausted")
  stopifnot(h$snapshot()$requests == 2, h$snapshot()$body_bytes == 8)
  env$pt_scan_http <- h; env$nrcs_preflight_retries <- 3L
  env$nrcs_preflight_url <- endpoint; env$nrcs_preflight_timeout_sec <- 5
  reject(env$pt_check_nrcs_endpoint(), "scan_budget_exhausted")
  stopifnot(h$snapshot()$requests == 2)
})
test("installed namespace functions remain unchanged", {
  after <- lapply(c("fetchSCAN", ".get_SCAN_data", "SCAN_site_metadata", ".make_SCAN_req", ".formatSCAN_soil_sensor_suites"), get, envir = ns)
  stopifnot(identical(original, after))
})
args <- commandArgs(trailingOnly = TRUE)
if (length(args)) test("retained real2218 through actual installed repaired route", {
  stopifnot(length(args) == 2L, file.info(args[1])$size <= 2^21)
  bytes <- readBin(args[1], "raw", n = file.info(args[1])$size)
  h <- meter(saved_backend(bytes)); f <- pt_scan_make_fetch_compat(h, 2218)
  x <- suppressMessages(f(2218, 2026)); previous <- readRDS(args[2])
  # The retained oracle contains the full installed fetch result, or SMS alone.
  expected <- if (is.list(previous) && "SMS" %in% names(previous)) previous$SMS else previous
  stopifnot(identical(x$SMS, expected), nrow(x$SMS) == 1353,
            length(unique(pt_scan_parse_nwcc_report(bytes, 2218, 2026)$Date)) == 278,
            length(unique(x$SMS$sensor.id)) == 5, h$snapshot()$requests == 1,
            h$snapshot()$body_bytes == length(bytes))
})

port <- Sys.getenv("SCAN_TEST_LOOPBACK_PORT", unset = "")
if (nzchar(port)) {
  stopifnot(grepl("^[0-9]{4,5}$", port), as.integer(port) < 65536)
  local_backend <- function(path) {
    force(path)
    function(method, url, form, timeout, read_limit, consume, check) {
      pt_scan_curl_backend(method, paste0("http://127.0.0.1:", port, path), form,
                           timeout, read_limit, consume, check)
    }
  }
  for (path in c("/exact", "/unknown-exact")) test(paste("native exact EOF", path), {
    h <- meter(local_backend(path), response = 1024)
    stopifnot(length(h$request("POST", view, form)$body) == 1024, h$snapshot()$body_bytes == 1024)
  })
  for (path in c("/oversize", "/unknown", "/gzip")) test(paste("native capped body", path), {
    h <- meter(local_backend(path), response = 1024)
    reject(h$request("POST", view, form), "scan_budget_exhausted")
    stopifnot(h$snapshot()$requests == 1, h$snapshot()$body_bytes == 1025)
  })
  for (path in c("/truncated", "/invalid-framing", "/timeout")) test(paste("native unknown/fatal", path), {
    h <- meter(local_backend(path), timeout = .25)
    reject(h$request("POST", view, form), "scan_fatal")
    stopifnot(h$snapshot()$requests == 1)
  })
  test("native redirect blocked without follow; error body charged", {
    h <- meter(local_backend("/redirect")); reject(h$request("POST", view, form), "scan_fatal")
    stopifnot(h$snapshot()$requests == 1)
    h <- meter(local_backend("/error")); reject(h$request("POST", view, form), "scan_http_error")
    stopifnot(h$snapshot()$body_bytes == 512)
  })
  test("native installed formatter uses actual form POST", {
    h <- meter(local_backend("/report")); f <- pt_scan_make_fetch_compat(h, 2218)
    x <- suppressMessages(f(2218, 2026)); stopifnot(nrow(x$SMS) == 3, h$snapshot()$requests == 1)
  })
}
cat("PASS", passed, "SCAN metering groups; installed versions:",
    paste(vapply(c("soilDB", "httr", "curl"), function(n) as.character(packageVersion(n)), character(1)), collapse = "/"), "\n")
