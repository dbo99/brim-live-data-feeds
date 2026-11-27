# Offline by default. The optional URL names a loopback-only fixture server.
source("scripts/snow_pillow_daily_http.R")
awdb <- "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data?x=1"
cdec <- "https://cdec.water.ca.gov/dynamicapp/req/JSONDataServlet?x=1"
refuses <- function(expr, pattern) {
  error <- tryCatch({ force(expr); NULL }, error = identity)
  stopifnot(inherits(error, "error"), grepl(pattern, conditionMessage(error)))
}
budget <- function(body, code = 200L, headers = raw()) {
  snow_daily_http(function(url, handle) list(content = body, status_code = code, headers = headers))
}
one <- budget(charToRaw("0"))
suppressMessages(for (i in seq_len(32)) stopifnot(length(one$get(awdb, 1)$content) == 1L))
refuses(one$get(awdb, 1), "attempt cap")
stopifnot(rawToChar(one$get(cdec, 1)$content) == "0")
one <- budget(raw())
suppressMessages(for (i in seq_len(288)) one$get(cdec, 1))
refuses(one$get(cdec, 1), "attempt cap")
one <- budget(raw(4 * 1024^2))
suppressMessages(for (i in seq_len(16)) one$get(cdec, 1))
refuses(one$get(awdb, 1), "aggregate body cap")
refuses(budget(raw())$get("https://example.invalid/", 1), "endpoint")

# Exercise the actual retry/parser/fallback callers without loading a live builder.
syntax <- as.list(parse("scripts/build_snow_pillow_latest.R"))
for (name in c("pt_chr", "pt_date", "pt_num", "pt_empty_cdec", "first_existing_col",
               "pt_fetch_text", "pt_json_from_url", "pt_parse_cdec_json_response", "fetch_cdec_swe")) {
  expr <- Filter(function(e) is.call(e) && identical(e[[1L]], quote(`<-`)) &&
                   identical(e[[2L]], as.name(name)), syntax)
  stopifnot(length(expr) == 1L)
  eval(expr[[1L]])
}
PT_FETCH_ATTEMPTS <- 3L
PT_FETCH_TIMEOUT_SEC <- 90L
CDEC_DAILY_INTERVAL <- "D"
LOCAL_TZ <- "America/Los_Angeles"
calls <- 0L
daily_http <- snow_daily_http(function(url, handle) {
  calls <<- calls + 1L
  body <- switch(calls, "retry", "[]", '[{"stationId":"AAA","date":"2026-11-24","value":0}]')
  list(status_code = if (calls == 1L) 500L else 200L, content = charToRaw(body), headers = raw())
})
saved <- fetch_cdec_swe("AAA", 82L, "adjusted", as.Date("2026-10-01"), as.Date("2026-11-25"), 0)
stopifnot(calls == 3L, nrow(saved) == 1L, saved$value == 0,
          saved$obs_date == as.Date("2026-11-24"), saved$sensor_num == 82L)
daily_http <- budget(charToRaw("https://example.invalid/"))
stopifnot(is.null(pt_json_from_url(cdec, "malformed")))
for (status in c(302L, 401L, 403L, 429L, 503L)) {
  headers <- if (status == 503L) charToRaw("rEtRy-AfTeR: 30\r\n") else raw()
  daily_http <- budget(raw(), status, headers)
  refuses(pt_fetch_text(cdec, "refused"), "backoff")
  refuses(daily_http$get(awdb, 1), "backoff")
}

args <- commandArgs(TRUE)
if (length(args)) {
  stopifnot(length(args) == 1L, grepl("^http://127[.]0[.]0[.]1:[0-9]+$", args))
  at <- function(path) snow_daily_http(function(url, handle) {
    curl::curl_fetch_memory(paste0(args, path), handle = handle)
  })
  stopifnot(identical(at("/ok")$get(awdb, 5)$content, charToRaw("[]")))
  stopifnot(at("/error")$get(awdb, 5)$status_code == 500L)
  for (path in c("/redirect", "/backoff")) refuses(at(path)$get(cdec, 5), "backoff")
  for (path in c("/large", "/chunked")) {
    h <- at(path)
    refuses(h$get(awdb, 10), "body cap")
    refuses(h$get(cdec, 1), "body cap")
  }
  message("Native no-redirect, refusal and known/unknown-length size tests passed")
}
message("Daily SWE HTTP accounting and collector integration passed")
