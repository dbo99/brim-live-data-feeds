# Run from the checkout root with soilDB 2.9.2. The in-process backend supplies
# all response bytes; source, installed request construction and parser are real.
root <- normalizePath(".")
source("scripts/run_scan_operational.R")
stopifnot(as.character(packageVersion("soilDB")) == "2.9.2")
Sys.setenv(GITHUB_WORKSPACE=root, SCAN_HTTP_MAX_REQUESTS="87", SCAN_HTTP_RESPONSE_BYTES="2097151",
           SCAN_HTTP_TOTAL_BYTES="182452223", SCAN_HTTP_CAMPAIGN_SECONDS="1200",
           SCAN_FETCH_TIMEOUT_SEC="20", SCAN_NWCC_PREFLIGHT_TIMEOUT_SEC="20",
           SCAN_NWCC_PREFLIGHT_RETRIES="1", SCAN_FETCH_RETRIES="1", SCAN_SMS_REQUEST_PAUSE_SEC="0",
           SCAN_CANARY_SITE_CODES="2218", SCAN_CANARY_MAX_REQUESTS="1", SCAN_CANARY_MIN_SUCCESSES="1",
           SCAN_STATION_INDEX_CSV=file.path(root, "data/input/scan_station_index.csv"),
           SCAN_SMS_PERCENTILES_CSV=file.path(root, "data/input/scan_sms_waterday_percentiles.csv"),
           SCAN_DEPTH_STYLE_CSV=file.path(root, "data/input/scan_depth_style.csv"))

fixture_backend <- function(method, url, form, timeout, read_limit, consume, check) {
  text <- "OK"
  if (method == "POST") {
    stopifnot(as.character(form$report) == "SMS", as.character(form$timeseries) == "Daily")
    dates <- if (form$year == 2026) c("2026-10-01","2026-12-30","2026-12-31") else "2027-01-01"
    text <- paste(c("Site Id,Date,Time,SMS.I-1:-2 (pct),SMS.I-1:-8 (pct),",
                    paste0(form$sitenum, ",", dates, ",,0,25,"),
                    paste0(form$sitenum, ",", form$year, "-11-01,,-99.9,-99.9,")), collapse="\n")
  }
  consume(charToRaw(text))
  list(status=200L, complete=TRUE)
}
options(brim.scan.http.backend=fixture_backend)
work <- tempfile("scan-calendar-")
dir.create(work)
for (day in c("2026-12-31", "2027-01-01", "2027-01-02")) {
  setwd(root)
  cutoff <- as.character(as.Date(day) - 1L)
  Sys.setenv(SCAN_SOURCE_CUTOFF_DATE=cutoff)
  generate <- paste("from pathlib import Path; import sys; sys.path.insert(0,'scripts');",
                    "from scan_operational_guard import request_plan;",
                    "print(*request_plan(Path.cwd(), sys.argv[1]), sep='\\n')")
  plan <- system2("python3", c("-B", "-c", shQuote(generate), cutoff), stdout=TRUE)
  count <- if (day == "2027-01-02") 57L else 29L
  stopifnot(length(plan) == count)
  dest <- file.path(work, day)
  artifact <- file.path(dest, "artifact")
  control <- file.path(dest, "scan-control")
  candidate <- file.path(artifact, "candidate")
  dir.create(candidate, recursive=TRUE)
  dir.create(control)
  account <- file.path(control, "runtime-account.json")
  reservation <- list(state="UNKNOWN_RESERVED_BEFORE_LAUNCH", source_cutoff_date=cutoff, request_plan=plan)
  jsonlite::write_json(reservation, file.path(control, "campaign-reservation.json"), auto_unbox=TRUE)
  setwd(candidate)
  builder <- function(scope) {
    scope$Sys.time <- function() as.POSIXct(paste(day, "20:00:00"), tz="UTC")
    source(file.path(root, "scripts/build_scan_soil_moisture_latest.R"), local=scope)
  }
  stopifnot(scan_run(builder, function() plan, account) == 0L)
  receipt <- jsonlite::fromJSON(account)
  stopifnot(identical(receipt$account$receipts$request_key, plan),
            identical(receipt$account$requests, count))
  trace <- read.csv("docs/data/scan_soil_moisture_current_wy_trace.csv")
  stopifnot(length(unique(trace$site_code)) == 28, setequal(unique(trace$depth_in), c(2,8)),
            all(trace$sms_pct[trace$depth_in == 2] == 0), all(trace$sms_pct[trace$depth_in == 8] == 25),
            !"2026-11-01" %in% trace$obs_date, min(trace$obs_date) == "2026-10-01",
            max(trace$obs_date) == cutoff)
  validator <- file.path(root, "scripts/scan_soil_moisture_publisher.py")
  stopifnot(system2("python3", c("-B", shQuote(validator), "validate", "--root", ".")) == 0L)
  files <- list.files("docs/data", full.names=TRUE)
  summary <- jsonlite::fromJSON("docs/data/scan_soil_moisture_latest_summary.json")
  paths <- c("scan_soil_moisture_latest.geojson", "scan_soil_moisture_latest_summary.json",
             "scan_soil_moisture_current_wy_trace.csv", "scan_soil_moisture_current_wy_trace_summary.json",
             "scan_depth_style.csv")
  allowlist <- as.vector(rbind("--allowlist", file.path("docs/data", paths)))
  prepare <- c("-B", file.path(root, "scripts/main_publisher.py"), "prepare", "--candidate-root", candidate,
               "--output", file.path(artifact, "candidate-metadata.json"), "--product-id", "scan-soil-moisture-latest",
               "--semantic-key-type", "feed_build_time_utc", "--semantic-key", summary$feed_build_time_utc,
               "--source-event-sha", strrep("a",40), allowlist)
  stopifnot(system2("python3", shQuote(prepare)) == 0L)
  audit <- c("-B", file.path(root, "scripts/run_scan_operational.py"), "audit-candidate", artifact, account)
  stopifnot(system2("python3", shQuote(audit)) == 0L)
  hashes <- tools::md5sum(files)
  stopifnot(length(files) == 5L)
  # Repeat against the saved outputs with an unreconciled second transfer.
  options(brim.scan.http.backend=function(method, ...) {
    if (method == "POST") stop("synthetic interrupted transfer")
    fixture_backend(method, ...)
  })
  stopifnot(scan_run(builder, function() plan, account) == 1L)
  failed <- jsonlite::fromJSON(account)
  stopifnot(identical(failed$account$receipts$state, c("COMPLETE","UNKNOWN")),
            !failed$account_predicate, identical(hashes, tools::md5sum(files)))
  stopifnot(system2("python3", shQuote(audit)) != 0L)
  options(brim.scan.http.backend=fixture_backend)
  cat("PASS operating day", day, "cutoff", cutoff, "requests", count, "last-good files", length(files), "\n")
}
setwd(root)
