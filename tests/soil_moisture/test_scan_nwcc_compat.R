# Run from the repository root: Rscript --vanilla tests/soil_moisture/test_scan_nwcc_compat.R
# Optional offline regression: append paths to a saved report and fetchSCAN.R.
# No production builder is sourced, and every transport call is a fixture.
source("scripts/soil_moisture/scan_nwcc_compat.R")
passed <- 0L
test <- function(name, code) {
  force(code)
  passed <<- passed + 1L
  cat("PASS", name, "\n")
}
reject <- function(body, pattern, ...) {
  error <- tryCatch(pt_scan_parse_nwcc_report(body, ...), error = identity)
  stopifnot(inherits(error, "error"), grepl(pattern, conditionMessage(error)),
            nchar(conditionMessage(error)) < 200L)
}
header <- paste0("Site Id,Date,Time,PRCP.D-1 (in) ,SMS.I-1:-2 (pct)  (loam),",
                 "SMS.I-1:-8 (pct)  (loam),SMS.I-2:-8 (pct)  (loam),")
rows <- c("2218,2026-09-30,,0,12,30,40,",
          "2218,2026-10-01,,0,0,20,40,",
          "2218,2026-10-01,23:59,-99.9,-99.9,-99.9,-99.9,",
          "2218,2026-10-02,,0,,NA,NaN,")
report <- function(preamble = c(rep("", 6L), "Synthetic SCAN daily report; provisional data", ""),
                   data = rows, h = header) {
  paste(c(preamble, h, data, "", "  "), collapse = "\n")
}

# Independent legacy decoding oracle, with its cursor explicitly aligned to a
# fixture's known header. It retains the old normalization and read.table types.
aligned_legacy <- function(body, skip) {
  tc <- textConnection(body)
  on.exit(close(tc))
  h <- unlist(read.table(tc, nrows = 1, skip = skip, header = FALSE,
                        stringsAsFactors = FALSE, sep = ",", quote = "",
                        strip.white = TRUE, na.strings = "-99.9", comment.char = ""))
  h <- as.vector(na.omit(h))
  h <- sapply(strsplit(h, " "), function(i) i[[1L]])
  h <- gsub("-1", "", fixed = TRUE, h)
  h <- gsub(":-", "_", h)
  x <- read.table(tc, header = FALSE, stringsAsFactors = FALSE, sep = ",",
                  quote = "", strip.white = TRUE, na.strings = "-99.9", comment.char = "")
  x[[ncol(x)]] <- NULL
  names(x) <- h
  x$Date <- as.Date(x$Date)
  x
}

test("line-9 layout, CRLF and raw bytes", {
  x <- pt_scan_parse_nwcc_report(report(), 2218, 2026)
  stopifnot(nrow(x) == 4L, identical(x, pt_scan_parse_nwcc_report(charToRaw(report()))),
            identical(x, pt_scan_parse_nwcc_report(gsub("\n", "\r\n", report(), fixed = TRUE))),
            identical(x, aligned_legacy(report(), 8L)))
})
test("shorter and zero preambles; harmless surrounding text", {
  expected <- pt_scan_parse_nwcc_report(report())
  for (preamble in list(character(), rep("", 5L), c("SMS report Date information", "provisional values"))) {
    stopifnot(identical(expected, pt_scan_parse_nwcc_report(report(preamble))))
  }
  stopifnot(identical(expected, aligned_legacy(report(rep("", 5L)), 5L)))
})
test("required header fields, whitespace, units and normalized identities", {
  x <- pt_scan_parse_nwcc_report(report(h = sub("Site Id,Date,Time", " Site Id , Date , Time ", header)))
  stopifnot(identical(names(x), c("Site", "Date", "Time", "PRCP.D", "SMS.I_2", "SMS.I_8", "SMS.I-2_8")))
  reject(report(h = sub("(pct)", "(fraction)", header, fixed = TRUE)), "pct units")
  reject(report(h = sub("SMS.I-2:-8", "SMS.I-1:-8", header, fixed = TRUE)), "duplicate")
})
test("missing or incomplete header", {
  reject("provider returned no data", "missing.*header")
  for (field in c("Site Id", "Date", "Time", "SMS.I")) {
    reject(report(h = gsub(field, "absent", header, fixed = TRUE)), "missing.*header")
  }
  reject(report(data = character()), "no data rows")
})
test("ambiguous repeated headers, including beyond the preamble", {
  reject(report(data = c(rows, header, rows)), "multiple plausible")
  reject(report(data = c(rep(rows[1L], 70L), header, rows)), "multiple plausible")
})
test("HTML and provider rejection responses", {
  reject("<!DOCTYPE html><html><body>Unavailable</body></html>", "HTML or provider rejection")
  reject(paste("Access Denied", report()), "provider rejection")
  reject("Request rejected", "provider rejection")
})
test("malformed/truncated CSV, unexpected trailing data and nonnumeric SMS", {
  reject(report(data = c(rows[1L], "2218,2026-10-02,,0,12,")), "row width")
  reject(report(data = c(rows[1L], paste0(rows[2L], "unexpected"))), "trailing empty")
  reject(report(data = sub(",30,", ",oops,", rows, fixed = TRUE)), "numeric measurement")
  reject(report(data = sub(",30,", ',"30",', rows, fixed = TRUE)), "quoted fields")
  # No trailing junk column is also safe: never drop the last real sensor.
  no_tail <- report(data = sub(",$", "", rows), h = sub(",$", "", header))
  stopifnot(identical(pt_scan_parse_nwcc_report(no_tail), pt_scan_parse_nwcc_report(report())))
})
test("date fidelity, leap days, invalid/missing dates and request identity", {
  x <- pt_scan_parse_nwcc_report(report())
  stopifnot(identical(as.character(x$Date), c("2026-09-30", "2026-10-01", "2026-10-01", "2026-10-02")))
  leap <- sub("2026-09-30", "2024-02-29", rows[1L], fixed = TRUE)
  stopifnot(as.character(pt_scan_parse_nwcc_report(report(data = leap))$Date) == "2024-02-29")
  for (bad in c("", "2026-02-29", "2026-13-01", "2026-10-01junk", "2026-1-01", "-99.9")) {
    reject(report(data = sub("2026-09-30", bad, rows[1L], fixed = TRUE)), "agency Date")
  }
  reject(report(), "Site Id differs", site_code = 2149)
  reject(report(), "Date year differs", year = 2025)
})
test("SMS sensor/depth/value parity, zero and accepted missing values", {
  x <- pt_scan_parse_nwcc_report(report())
  stopifnot(identical(x, aligned_legacy(report(), 8L)), x$SMS.I_2[2L] == 0,
            is.na(x$SMS.I_2[3L]), is.na(x$SMS.I_2[4L]),
            identical(x$SMS.I_8, c("30", "20", NA_character_, "NA")),
            is.nan(x$`SMS.I-2_8`[4L]), identical(x$PRCP.D, c(0L, 0L, NA_integer_, 0L)))
  # Reports with only blank daily times retain the legacy logical NA column.
  blank_times <- report(data = rows[c(1L, 2L)])
  stopifnot(identical(pt_scan_parse_nwcc_report(blank_times), aligned_legacy(blank_times, 8L)))
})
test("duplicate dates remain separate and in provider order", {
  x <- pt_scan_parse_nwcc_report(report(data = c(rows[2L], rows[1L], rows[2L])))
  stopifnot(nrow(x) == 3L, identical(as.character(x$Date), c("2026-10-01", "2026-09-30", "2026-10-01")),
            identical(x$SMS.I_2, c(0L, 12L, 0L)))
})
test("bounded input, preamble, columns, line count and diagnostics", {
  reject(paste(rep("x", 2L * 1024L * 1024L + 1L), collapse = ""), "2 MiB")
  reject(as.raw(c(65, 0, 66)), "NUL")
  reject(report(preamble = rep("", 64L)), "preamble bound")
  stopifnot(nrow(pt_scan_parse_nwcc_report(report(preamble = rep("", 63L)))) == 4L)
  reject(report(data = rep(rows[1L], 4096L)), "4096 lines")
  reject(report(preamble = strrep("x", 16385L)), "16384 bytes")
  reject(report(h = paste(c(header, rep("extra", 64L)), collapse = ",")), "64 columns")
})

# A tiny synthetic upstream namespace exercises the real private-boundary
# adapter without soilDB installed, networking, or package-namespace mutation.
upstream <- new.env(parent = globalenv())
upstream$calls <- list()
upstream$response <- report()
upstream$offline_transport <- function(req) {
  upstream$calls[[length(upstream$calls) + 1L]] <- req
  upstream$response
}
eval(quote(.get_SCAN_data <- function(req) {
  r.content <- offline_transport(req)
  tc <- textConnection(r.content)
  h <- unlist(read.table(tc, nrows = 1, skip = 5, sep = ","))
  h <- gsub("-1", "", fixed = TRUE, h)
  h <- gsub(":-", "_", h)
  stop("legacy fixed-skip tail must not execute")
  x$Date <- as.Date(x$Date)
  return(x)
}), upstream)
eval(quote(fetchSCAN <- function(site.code, year, report, timeseries, tz) {
  req <- list(intervalType = " View Historic ", report = report,
              timeseries = timeseries, format = "copy", sitenum = site.code,
              interval = "YEAR", year = year, month = "CY")
  list(SMS = .get_SCAN_data(req), tz = tz)
}), upstream)
original_get <- upstream$.get_SCAN_data
original_fetch <- upstream$fetchSCAN
compat <- pt_scan_make_fetch_compat(upstream$fetchSCAN)
test("private parser boundary: unchanged transport and one call, no fallback", {
  stopifnot(length(upstream$calls) == 0L)
  out <- compat(2218, 2026)
  stopifnot(identical(out$SMS, pt_scan_parse_nwcc_report(report())), out$tz == "UTC",
            length(upstream$calls) == 1L,
            identical(upstream$calls[[1L]], list(intervalType = " View Historic ", report = "SMS",
              timeseries = "Daily", format = "copy", sitenum = 2218, interval = "YEAR", year = 2026, month = "CY")),
            identical(upstream$.get_SCAN_data, original_get), identical(upstream$fetchSCAN, original_fetch))
  private_fetch <- environment(compat)$fetch_scan
  stopifnot(identical(body(private_fetch), body(original_fetch)),
            identical(body(environment(private_fetch)$.get_SCAN_data)[[2L]], body(original_get)[[2L]]))
  upstream$response <- "Access denied"
  stopifnot(inherits(try(compat(2218, 2026), silent = TRUE), "try-error"), length(upstream$calls) == 2L)
  stopifnot(inherits(try(compat(2218, 2026, timeseries = "Hourly"), silent = TRUE), "try-error"), length(upstream$calls) == 2L)
  unsupported <- new.env(parent = globalenv())
  unsupported$.get_SCAN_data <- function(req) stop("must never execute")
  unsupported$fetchSCAN <- upstream$fetchSCAN
  environment(unsupported$fetchSCAN) <- unsupported
  stopifnot(inherits(try(pt_scan_make_fetch_compat(unsupported$fetchSCAN), silent = TRUE), "try-error"), length(upstream$calls) == 2L)
  # A qualified call would bypass the private environment and must fail closed.
  eval(quote(fetchSCAN <- function() soilDB:::.get_SCAN_data(list())), unsupported)
  unsupported$.get_SCAN_data <- original_get
  environment(unsupported$.get_SCAN_data) <- unsupported
  stopifnot(inherits(try(pt_scan_make_fetch_compat(unsupported$fetchSCAN), silent = TRUE), "try-error"), length(upstream$calls) == 2L)
})

# Extract only pure definitions and chosen transformation expressions. Never
# evaluate the builder's package setup, preflight, retrieval loop or file writes.
suppressPackageStartupMessages(library(dplyr))
builder <- parse("scripts/build_scan_soil_moisture_latest.R")
assignment <- function(expr, name) {
  is.call(expr) && identical(expr[[1L]], as.name("<-")) && identical(expr[[2L]], as.name(name))
}
named_expression <- function(name) {
  matches <- Filter(function(expr) assignment(expr, name), as.list(builder))
  stopifnot(length(matches) == 1L)
  matches[[1L]]
}
test("builder helper path in repository and isolated Actions working directory", local({
  helper_source <- Filter(function(expr) is.call(expr) && identical(expr[[1L]], as.name("source")), as.list(builder))
  stopifnot(length(helper_source) == 1L)
  original_dir <- getwd()
  original_workspace <- Sys.getenv("GITHUB_WORKSPACE", unset = NA_character_)
  on.exit({
    setwd(original_dir)
    if (is.na(original_workspace)) Sys.unsetenv("GITHUB_WORKSPACE") else Sys.setenv(GITHUB_WORKSPACE = original_workspace)
  })
  Sys.unsetenv("GITHUB_WORKSPACE")
  expected_path <- normalizePath("scripts/soil_moisture/scan_nwcc_compat.R")
  stopifnot(identical(normalizePath(eval(helper_source[[1L]][[2L]])), expected_path))
  Sys.setenv(GITHUB_WORKSPACE = original_dir)
  setwd(tempdir())
  stopifnot(identical(normalizePath(eval(helper_source[[1L]][[2L]])), expected_path))
}))
fixture <- new.env(parent = globalenv())
for (name in c("pt_record_fetch_diag", "fetch_one_scan_year", "pt_scan_canary_pairs", "pt_run_scan_canary",
               "pt_current_water_year", "pt_water_year", "pt_water_day", "pt_depth_in_from_sensor",
               "pt_utc_datetime_to_local_label")) eval(named_expression(name), fixture)
fixture$pt_scan_fetch <- compat
fixture$fetch_retries <- 3L
fixture$request_pause_sec <- 0
fixture$fetch_retry_pause_sec <- 0
fixture$station_index <- tibble::tibble(site_code = c(2218L, 2149L))
fixture$scan_canary_site_codes <- c(2218L, 2149L)
fixture$fetch_years <- c(2025L, 2026L)
fixture$scan_canary_max_requests <- 2L
fixture$scan_canary_min_successes <- 1L
test("builder retries, diagnostics, pair budget and fail-closed canary", {
  upstream$calls <- list()
  upstream$response <- "Access denied"
  fixture$scan_fetch_diag_rows <- list()
  failure <- suppressMessages(tryCatch(fixture$pt_run_scan_canary(), error = identity))
  stopifnot(inherits(failure, "error"), grepl("previous hosted feed is preserved", conditionMessage(failure)),
            length(upstream$calls) == 6L, length(fixture$scan_fetch_diag_rows) == 2L,
            all(vapply(fixture$scan_fetch_diag_rows, function(x) x$attempts == 3L && x$rows == 0L && x$status == "empty_or_failed", logical(1))))
  pairs <- fixture$pt_scan_canary_pairs()
  stopifnot(identical(pairs$site_code, c(2218L, 2218L)), identical(pairs$year, c(2026L, 2025L)))
  upstream$calls <- list()
  upstream$response <- report()
  fixture$scan_fetch_diag_rows <- list()
  out <- suppressMessages(fixture$pt_run_scan_canary())
  stopifnot(out$ok, nrow(out$pairs) == 1L, nrow(out$rows) == 4L,
            length(upstream$calls) == 1L, fixture$scan_fetch_diag_rows[[1L]]$attempts == 1L)
})
test("offline builder current-WY/duplicate-depth/zero/date/age fixture", {
  raw <- pt_scan_parse_nwcc_report(report())
  # Explicit fixture at soilDB's existing SMS output boundary: original sensor
  # identity, rounded centimetres and daily noon at the station's UTC-8 offset.
  fixture$sms_raw <- bind_rows(lapply(c("SMS.I_2", "SMS.I_8", "SMS.I-2_8"), function(sensor) {
    depth_in <- if (sensor == "SMS.I_2") 2 else 8
    tibble::tibble(Site = raw$Site, Date = raw$Date, sensor.id = sensor,
      depth = round(depth_in * 2.54), value = suppressWarnings(as.numeric(raw[[sensor]])),
      datetime = as.POSIXct(paste(raw$Date, "20:00:00"), tz = "UTC"))
  }))
  fixture$current_wy_start_date <- as.Date("2026-10-01")
  fixture$fetch_end_date <- as.Date("2026-10-05")
  fixture$display_timezone <- "America/Los_Angeles"
  fixture$depth_style <- tibble::tibble(depth_in = c(2L, 8L), depth_order = c(1L, 2L))
  eval(named_expression("sms_clean"), fixture)
  eval(named_expression("sms_daily"), fixture)
  out <- fixture$sms_daily
  stopifnot(nrow(out) == 2L, identical(out$sms_pct, c(0, 30)),
            identical(out$depth_in, c(2L, 8L)),
            # Existing summarise() counts its already-collapsed sensor_id, so
            # sensor_count is 1 even for this two-sensor mean. Preserve that
            # pre-existing metadata behavior in this parsing-only repair.
            identical(out$sensor_count, c(1L, 1L)),
            out$sensor_id[2L] == "SMS.I_8, SMS.I-2_8" || out$sensor_id[2L] == "SMS.I-2_8, SMS.I_8",
            all(out$obs_date == as.Date("2026-10-01")), all(out$water_year == 2027L),
            all(out$water_day == 1L), all(out$obs_age_days == 4L),
            all(out$obs_datetime_utc == "2026-10-01T20:00:00Z"))
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args)) {
  stopifnot(length(args) == 2L)
  test("optional saved response and locally saved soilDB source parity", {
    size <- file.info(args[[1L]])$size
    stopifnot(!is.na(size), size <= 2L * 1024L * 1024L)
    bytes <- readBin(args[[1L]], "raw", n = size)
    text <- rawToChar(bytes)
    Encoding(text) <- "latin1"
    x <- pt_scan_parse_nwcc_report(bytes, 2218, 2026)
    stopifnot(identical(x, aligned_legacy(text, 8L)), nrow(x) == 279L,
              length(unique(x$Date)) == 278L, !anyNA(x$Date),
              identical(names(x)[5:9], c("SMS.I_2", "SMS.I_4", "SMS.I_8", "SMS.I_20", "SMS.I_40")),
              identical(vapply(x[5:9], function(x) sum(!is.na(x)), integer(1)),
                        c(SMS.I_2 = 272L, SMS.I_4 = 272L, SMS.I_8 = 272L, SMS.I_20 = 270L, SMS.I_40 = 267L)))
    saved <- new.env(parent = globalenv())
    for (expr in parse(args[[2L]])) {
      if (any(vapply(c("fetchSCAN", ".get_SCAN_data", ".make_SCAN_req", ".formatSCAN_soil_sensor_suites"),
                     function(name) assignment(expr, name), logical(1)))) eval(expr, saved)
    }
    saved_fetch <- pt_scan_make_fetch_compat(saved$fetchSCAN)
    patched <- environment(environment(saved_fetch)$fetch_scan)$.get_SCAN_data
    old_statements <- as.list(body(saved$.get_SCAN_data))
    start <- which(vapply(old_statements, identical, logical(1), quote(tc <- textConnection(r.content))))
    stopifnot(identical(as.list(body(patched))[seq_len(start - 1L)], old_statements[seq_len(start - 1L)]))
    # Execute only the saved source's old decoding tail, aligned to the known
    # fixture line, and then its unchanged formatter. No transport can execute.
    legacy_tail <- as.call(c(list(as.name("{")), old_statements[start:length(old_statements)]))
    legacy_tail[[3L]][[3L]][[2L]]$skip <- 8
    legacy <- function(r.content, req) NULL
    body(legacy) <- legacy_tail
    legacy_x <- legacy(text, list(sitenum = 2218, year = 2026))
    stopifnot(identical(x, legacy_x))
    # waterDayYear is unavailable without soilDB. Supply the builder's existing
    # equivalent WY/day helpers equally to both sides of this formatter replay.
    saved$waterDayYear <- function(date, tz) list(wy = fixture$pt_water_year(date), wd = fixture$pt_water_day(date))
    format_sms <- function(d) saved$.formatSCAN_soil_sensor_suites(d, "SMS", data.frame(dataTimeZone = -8), FALSE, "UTC")
    sms <- format_sms(x)
    stopifnot(identical(sms, format_sms(legacy_x)), nrow(sms) == 1353L,
              identical(sort(unique(sms$depth)), c(5, 10, 20, 51, 102)),
              identical(sort(unique(as.character(sms$sensor.id))), sort(names(x)[5:9])),
              all(sms$Time == "12:00"), all(format(sms$datetime, "%H:%M", tz = "UTC") == "20:00"))
    stopifnot(identical(saved$.make_SCAN_req(2218, 2026, "SMS", "Daily"),
      list(intervalType = " View Historic ", report = "SMS", timeseries = "Daily", format = "copy",
           sitenum = 2218, interval = "YEAR", year = 2026, month = "CY")))
    cat("CAPTURE_PARITY rows=279 unique_dates=278 sms_values=1353 sensors=5 depths_cm=5,10,20,51,102\n")
  })
} else {
  cat("SKIP optional saved-response/source regression (no paths supplied)\n")
}
cat("PASS", passed, "offline SCAN compatibility test groups; provider calls=0\n")
