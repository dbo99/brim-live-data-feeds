# NWCC SMS/Daily decoding only. Transport, metadata and sensor formatting stay
# in soilDB; the builder continues to own retries, canaries and publication QA.
pt_scan_parse_nwcc_report <- function(body, site_code = NULL, year = NULL) {
  fail <- function(reason) stop("SCAN NWCC parse: ", reason, call. = FALSE)
  # A single station/calendar-year daily report is small. Bound both the input
  # and the work done while finding a header, including malformed responses.
  max_bytes <- 2L * 1024L * 1024L
  if (is.raw(body)) {
    if (length(body) > max_bytes) fail("body exceeds 2 MiB")
    if (any(body == as.raw(0))) fail("body contains NUL bytes")
    body <- rawToChar(body)
    Encoding(body) <- "latin1" # NWCC copy reports use ISO-8859-1
  }
  if (!is.character(body) || length(body) != 1L || is.na(body)) {
    fail("expected one report text string or raw vector")
  }
  if (nchar(body, type = "bytes") > max_bytes) fail("body exceeds 2 MiB")
  lines <- strsplit(body, "\r\n|\n|\r", perl = TRUE)[[1L]]
  if (!length(lines) || length(lines) > 4096L) fail("empty report or more than 4096 lines")
  if (any(nchar(lines, type = "bytes") > 16384L)) fail("report line exceeds 16384 bytes")
  if (any(grepl("<(!doctype[[:space:]]+html|html|head|body)\\b|access denied|request rejected|service unavailable",
                lines, ignore.case = TRUE, perl = TRUE))) {
    fail("HTML or provider rejection response")
  }

  # Appending a sentinel preserves the empty final CSV field in strsplit().
  fields <- lapply(lines, function(line) {
    parts <- strsplit(paste0(line, ",#"), ",", fixed = TRUE)[[1L]]
    trimws(parts[-length(parts)])
  })
  candidates <- which(vapply(fields, function(h) {
    all(c("Site Id", "Date", "Time") %in% h) && any(grepl("^SMS\\.I", h))
  }, logical(1)))
  if (length(candidates) == 0L) fail("missing Site Id/Date/Time/SMS header (or nondata response)")
  if (length(candidates) != 1L) fail("multiple plausible report headers")
  header_line <- candidates[[1L]]
  if (header_line > 64L) fail("header occurs beyond the 64-line preamble bound")
  h <- fields[[header_line]]
  if (length(h) > 64L) fail("header exceeds 64 columns")
  trailing_empty <- tail(h, 1L) == ""
  if (trailing_empty) h <- head(h, -1L)
  if (any(!nzchar(h)) || anyDuplicated(h) ||
      any(vapply(c("Site Id", "Date", "Time"), function(n) sum(h == n), integer(1)) != 1L)) {
    fail("empty or duplicate header fields")
  }
  sms <- grepl("^SMS\\.I", h)
  if (any(!grepl("^SMS\\.I-[1-9][0-9]*:-[0-9]+[[:space:]]+\\(pct\\)([[:space:]].*)?$", h[sms]))) {
    fail("SMS header lacks a sensor/depth identity or pct units")
  }
  # Match soilDB's accepted field normalization, including duplicate sensors
  # such as SMS.I-2_8. Do not change units, depths, values or the daily clock.
  normalized <- vapply(strsplit(h, " ", fixed = TRUE), `[`, character(1), 1L)
  normalized <- gsub("-1", "", normalized, fixed = TRUE)
  normalized <- gsub(":-", "_", normalized, fixed = TRUE)
  if (anyDuplicated(normalized)) fail("header fields normalize to duplicate names")

  rows <- which(seq_along(lines) > header_line & nzchar(trimws(lines)))
  if (!length(rows)) fail("report contains no data rows")
  if (any(lengths(fields[rows]) != length(fields[[header_line]]))) {
    fail("data row width differs from header (malformed/truncated CSV)")
  }
  if (trailing_empty && any(vapply(fields[rows], function(row) tail(row, 1L) != "", logical(1)))) {
    fail("unexpected data in the trailing empty column")
  }
  if (any(grepl('"', lines[c(header_line, rows)], fixed = TRUE))) {
    fail("quoted fields are not supported by the NWCC copy-report contract")
  }
  x <- tryCatch(read.table(
    text = paste(lines[rows], collapse = "\n"), header = FALSE,
    stringsAsFactors = FALSE, sep = ",", quote = "", strip.white = TRUE,
    na.strings = "-99.9", comment.char = "", fill = FALSE
  ), error = function(e) fail("malformed CSV data"))
  if (trailing_empty) x[[ncol(x)]] <- NULL
  names(x) <- normalized

  date_text <- as.character(x$Date)
  dates <- suppressWarnings(as.Date(date_text, format = "%Y-%m-%d"))
  if (anyNA(date_text) || anyNA(dates) ||
      any(!grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", date_text)) ||
      any(format(dates, "%Y-%m-%d") != date_text)) {
    fail("missing or invalid agency Date")
  }
  if (anyNA(x$Site) || any(!grepl("^[0-9]+$", as.character(x$Site)))) fail("invalid Site Id")
  # Daily rows normally have an empty Time; soilDB assigns its existing daily
  # noon timestamp later. Preserve blanks and any explicit provider times.
  if (any(!is.na(x$Time) & !grepl("^$|^([01][0-9]|2[0-3]):[0-5][0-9]$", x$Time))) fail("invalid Time")
  if (!is.null(site_code) && any(as.character(x$Site) != as.character(site_code))) fail("Site Id differs from request")
  if (!is.null(year) && any(format(dates, "%Y") != as.character(year))) fail("Date year differs from request")
  for (column in setdiff(names(x), c("Site", "Date", "Time"))) {
    values <- suppressWarnings(as.numeric(x[[column]]))
    # Keep read.table's original types/sentinels. soilDB's formatter converts
    # these to double and drops missing values; zero is never treated as NA.
    missing <- is.na(x[[column]]) | as.character(x[[column]]) %in% c("NA", "NaN")
    if (any(!missing & !is.finite(values))) fail("invalid numeric measurement")
  }
  x$Date <- dates
  x
}

pt_scan_make_fetch_compat <- function(fetch_scan = soilDB::fetchSCAN) {
  unsupported <- function() stop(
    "SCAN NWCC compatibility: unsupported soilDB parsing boundary; review before fetching",
    call. = FALSE
  )
  if (!is.function(fetch_scan)) unsupported()
  ns <- environment(fetch_scan)
  get_data <- get0(".get_SCAN_data", envir = ns, inherits = FALSE)
  direct_calls <- function(expr) {
    if (missing(expr) || !is.call(expr)) return(0L)
    as.integer(identical(expr[[1L]], as.name(".get_SCAN_data"))) +
      sum(vapply(as.list(expr), direct_calls, integer(1)))
  }
  calls <- direct_calls(body(fetch_scan))
  if (!is.function(get_data) || !identical(names(formals(get_data)), "req") ||
      !identical(environment(get_data), ns) ||
      calls == 0L || calls != sum(all.names(body(fetch_scan)) == ".get_SCAN_data")) unsupported()

  # Copy closures into a private environment, never patch the package namespace.
  # Retain the installed helper's transport/status/content handling verbatim and
  # replace only its fixed-skip parsing tail. Unknown layouts fail before I/O.
  statements <- as.list(body(get_data))
  boundary <- which(vapply(statements, identical, logical(1), quote(tc <- textConnection(r.content))))
  if (length(boundary) != 1L || boundary < 2L ||
      !identical(statements[[1L]], as.name("{")) ||
      !identical(tail(statements, 2L), list(quote(x$Date <- as.Date(x$Date)), quote(return(x))))) unsupported()
  header <- statements[[boundary + 1L]]
  if (!is.call(header) || !identical(header[[1L]], as.name("<-")) ||
      !identical(header[[2L]], as.name("h")) ||
      !is.call(header[[3L]]) || !identical(header[[3L]][[1L]], as.name("unlist"))) unsupported()
  header_read <- header[[3L]][[2L]]
  if (!is.call(header_read) || !identical(header_read[[1L]], as.name("read.table")) ||
      !identical(header_read[[2L]], as.name("tc")) ||
      !identical(header_read$skip, 5) || !identical(header_read$sep, ",")) unsupported()
  for (normalization in list(quote(h <- gsub("-1", "", fixed = TRUE, h)), quote(h <- gsub(":-", "_", h)))) {
    if (!any(vapply(statements, identical, logical(1), normalization))) unsupported()
  }

  private <- new.env(parent = ns)
  private$pt_scan_parse_nwcc_report <- pt_scan_parse_nwcc_report
  body(get_data) <- as.call(c(statements[seq_len(boundary - 1L)], list(quote(
    pt_scan_parse_nwcc_report(r.content, site_code = req$sitenum, year = req$year)
  ))))
  environment(get_data) <- private
  private$.get_SCAN_data <- get_data
  environment(fetch_scan) <- private
  # This adapter is intentionally confined to the builder's SMS/Daily call.
  function(site.code, year, report = "SMS", timeseries = "Daily", tz = "UTC") {
    if (!identical(report, "SMS") || !identical(timeseries, "Daily")) {
      stop("SCAN NWCC compatibility supports SMS/Daily only", call. = FALSE)
    }
    fetch_scan(site.code = site.code, year = year, report = report, timeseries = timeseries, tz = tz)
  }
}
