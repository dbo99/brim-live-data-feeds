#!/usr/bin/env Rscript

# Parse the production builder; never source its provider calls or output writes.
builder <- parse("scripts/build_snow_pillow_latest.R")
assignment <- function(name) {
  matches <- Filter(function(expr) {
    is.call(expr) && identical(expr[[1L]], as.name("<-")) &&
      identical(expr[[2L]], as.name(name))
  }, as.list(builder))
  if (length(matches) != 1L) stop("Expected one builder assignment for ", name)
  matches[[1L]]
}

assert_equal <- function(actual, expected, label) {
  if (!identical(actual, expected)) {
    stop(label, ": expected ", paste(expected, collapse = ", "),
         "; got ", paste(actual, collapse = ", "), call. = FALSE)
  }
}

configuration <- function(instant_utc, fetch_start = NULL, fetch_end = NULL, limits = NULL) {
  context <- new.env(parent = baseenv())
  context$Sys.time <- function() as.POSIXct(instant_utc, tz = "UTC")
  overrides <- c(SNOW_PILLOW_FETCH_START_DATE = fetch_start,
                 SNOW_PILLOW_FETCH_END_DATE = fetch_end, limits)
  context$Sys.getenv <- function(name, unset = "") {
    if (name %in% names(overrides)) overrides[[name]] else unset
  }
  # These helpers contain only offline definitions. Their environment also uses
  # the mocked configuration, so a developer's shell overrides cannot leak in.
  sys.source("scripts/snow_pillow_partial_refresh_helpers.R", envir = context)
  context$snotel_stations <- data.frame(
    station_uid = sprintf("NRCS_%03d", 1:175),
    nrcs_station_triplet = sprintf("fixture_%03d", 1:175)
  )
  context$cdec_stations <- data.frame(
    station_uid = sprintf("CDEC_%03d", 1:129),
    cdec_id = sprintf("fixture_%03d", 1:129)
  )
  context$stations <- data.frame(station_uid = c(
    context$snotel_stations$station_uid, context$cdec_stations$station_uid
  ))
  fields <- c(
    "LOCAL_TZ", "TODAY_LOCAL", "CURRENT_WY", "DEFAULT_WY_START",
    "FETCH_START_DATE", "FETCH_END_DATE", "completed_fetch_end_date",
    "expected_fetch_days", "coverage_days", "snotel_requested_station_days",
    "cdec_requested_station_days", "in_low_snow_reporting_season",
    "provider_completeness_defaults", "qa_min_snotel_station_fraction",
    "qa_min_snotel_row_fraction", "qa_min_cdec_station_fraction",
    "qa_min_cdec_row_fraction", "snotel_completeness", "cdec_completeness",
    "qa_min_snotel_wteq_rows", "qa_min_snotel_snwd_rows", "qa_min_cdec_swe_rows",
    "qa_min_snotel_sites", "qa_min_cdec_sites", "qa_min_trace_rows_default",
    "qa_min_trace_rows", "MAX_VALID_SWE_IN", "pt_num", "pt_swe_num",
    "pt_water_year", "pt_water_day", "CDEC_RAW_SWE_SENSOR", "CDEC_REVISED_SWE_SENSOR"
  )
  for (name in fields) eval(assignment(name), envir = context)
  stopifnot(inherits(context$completed_fetch_end_date, "Date"))
  context
}

check_calendar <- function(instant, date, wy, start, low_snow) {
  context <- configuration(instant)
  expected <- list(
    LOCAL_TZ = "America/Los_Angeles", TODAY_LOCAL = as.Date(date),
    CURRENT_WY = wy, DEFAULT_WY_START = as.Date(start),
    FETCH_START_DATE = as.Date(start), FETCH_END_DATE = as.Date(date),
    in_low_snow_reporting_season = low_snow
  )
  for (name in names(expected)) {
    assert_equal(context[[name]], expected[[name]], paste(instant, name))
  }
  message("PASS: Pacific calendar at ", instant, " UTC")
}

check_calendar("2026-09-29 01:22:45", "2026-09-28", 2026L, "2025-10-01", TRUE)
check_calendar("2026-10-01 06:59:59", "2026-09-30", 2026L, "2025-10-01", TRUE)
check_calendar("2026-10-01 07:00:00", "2026-10-01", 2027L, "2026-10-01", FALSE)
check_calendar("2027-01-01 07:59:59", "2026-12-31", 2027L, "2026-10-01", FALSE)
check_calendar("2027-01-01 08:00:00", "2027-01-01", 2027L, "2026-10-01", FALSE)

oct6 <- configuration("2026-10-06 12:18:50")
assert_equal(oct6$completed_fetch_end_date, as.Date("2026-10-05"), "Completed end")
assert_equal(oct6$coverage_days, 5L, "October 6 completed days")
assert_equal(oct6$qa_min_snotel_wteq_rows, 788L, "October 6 WTEQ minimum")
assert_equal(oct6$qa_min_snotel_snwd_rows, 788L, "October 6 SNWD minimum")
assert_equal(oct6$qa_min_cdec_swe_rows, 549L, "October 6 CDEC minimum")
assert_equal(oct6$qa_min_trace_rows, 1337L, "Combined trace minimum")
stopifnot(804L >= oct6$qa_min_snotel_wteq_rows,
          838L >= oct6$qa_min_snotel_snwd_rows,
          563L >= oct6$qa_min_cdec_swe_rows)
assert_equal(oct6$qa_min_snotel_row_fraction, 0.90, "NRCS row fraction")
assert_equal(oct6$qa_min_cdec_row_fraction, 0.85, "CDEC row fraction")
assert_equal(oct6$qa_min_snotel_station_fraction, 0.90, "NRCS station fraction")
assert_equal(oct6$qa_min_cdec_station_fraction, 0.92, "CDEC station fraction")
assert_equal(oct6$qa_min_snotel_sites, 158L, "NRCS site minimum")
assert_equal(oct6$qa_min_cdec_sites, 119L, "CDEC site minimum")
assert_equal(oct6$expected_fetch_days, 6L, "All six requested days remain in guardrail accounting")
strict <- configuration("2026-10-06 12:18:50", limits = c(
  SNOW_PILLOW_QA_MIN_SNOTEL_WTEQ_ROWS = "1050",
  SNOW_PILLOW_QA_MIN_CDEC_SWE_ROWS = "774"
))
assert_equal(strict$qa_min_snotel_wteq_rows, 1050L, "Stricter full-request NRCS minimum")
assert_equal(strict$qa_min_cdec_swe_rows, 774L, "Stricter full-request CDEC minimum")
oct13 <- configuration("2026-10-13 13:03:43")
assert_equal(oct13$coverage_days, 12L, "Twelve completed Pacific days on October 13")
assert_equal(oct13$qa_min_snotel_wteq_rows, 1890L, "October 13 NRCS row minimum")
assert_equal(oct13$qa_min_cdec_swe_rows, 1316L, "October 13 CDEC row minimum")
stopifnot(1955L >= oct13$qa_min_snotel_wteq_rows, 2046L >= oct13$qa_min_snotel_snwd_rows,
          1384L >= oct13$qa_min_cdec_swe_rows)
message("PASS: October 6 observed row counts clear unchanged completeness fractions")

oct1 <- configuration("2026-10-01 07:00:00")
assert_equal(oct1$completed_fetch_end_date, as.Date("2026-09-30"), "No completed WY day")
assert_equal(oct1$coverage_days, 1L, "October 1 minimum-one-day floor")
assert_equal(oct1$qa_min_snotel_wteq_rows, 158L, "October 1 NRCS minimum")
assert_equal(oct1$qa_min_cdec_swe_rows, 110L, "October 1 CDEC minimum")
assert_equal(oct1$FETCH_END_DATE, as.Date("2026-10-01"), "October 1 acquisition end")
assert_equal(oct1$pt_water_day(as.Date(c(NA_character_, "2026-09-30", "2026-10-01"))),
             c(NA_integer_, 365L, 1L), "Missing first station preserves null and adjacent WY days")
assert_equal(oct1$pt_water_day(as.Date(NA_character_)), NA_integer_, "All-missing water day")
oct2 <- configuration("2026-10-02 12:00:00")
assert_equal(oct2$coverage_days, 1L, "October 2 completed day")
message("PASS: October 1 retains one-day guard; October 2 counts its completed day")

historical <- configuration("2026-10-06 12:18:50", fetch_end = "2026-10-04")
assert_equal(historical$completed_fetch_end_date, as.Date("2026-10-04"), "Historical end")
assert_equal(historical$coverage_days, 4L, "Full historical interval")
mid_wy <- configuration("2027-01-15 20:00:00")
assert_equal(mid_wy$coverage_days, 106L, "Mid-WY completed days")
assert_equal(mid_wy$completed_fetch_end_date, as.Date("2027-01-14"), "Mid-WY completed end")
assert_equal(mid_wy$FETCH_END_DATE, as.Date("2027-01-15"), "Mid-WY acquisition end")
explicit <- configuration("2027-01-15 20:00:00", "2027-01-01", "2027-01-10")
assert_equal(explicit$coverage_days, 10L, "Explicit completed historical interval")
future <- configuration("2026-10-06 12:18:50", fetch_end = "2026-10-09")
assert_equal(future$coverage_days, 5L, "Future days are not required complete")
assert_equal(future$FETCH_END_DATE, as.Date("2026-10-09"), "Configured future acquisition end")
message("PASS: historical, mid-WY and future-end completeness cases")

# Execute the actual provider-attempt assignments using only recording stubs.
check_acquisition <- function(context) {
  calls <- list()
  record <- function(...) {
    calls[[length(calls) + 1L]] <<- list(...)
    data.frame()
  }
  context$run_awdb_preflight <- function(...) {
    record(...)
    list(passed = TRUE)
  }
  context$fetch_awdb_element <- record
  context$fetch_cdec_swe <- record
  eval(assignment("awdb_attempt"), envir = context)
  eval(assignment("cdec_attempt"), envir = context)
  stopifnot(context$awdb_attempt$completed, context$cdec_attempt$completed,
            length(calls) == 5L)
  for (call in calls) {
    assert_equal(call$begin_date, context$FETCH_START_DATE, "Provider request start")
    assert_equal(call$end_date, context$FETCH_END_DATE, "Provider request end")
  }
}
for (context in list(oct1, oct6, historical, mid_wy, future)) check_acquisition(context)
message("PASS: AWDB preflight, WTEQ, SNWD and both CDEC requests retain configured end")

make_observations <- function(context, station_uids, row_count) {
  grid <- expand.grid(
    station_uid = station_uids,
    obs_date = seq(context$FETCH_START_DATE, context$FETCH_END_DATE, by = "day"),
    stringsAsFactors = FALSE
  )[seq_len(row_count), , drop = FALSE]
  # Include an observation from the in-progress day, without duplicating a key.
  grid$obs_date[1L] <- context$FETCH_END_DATE
  grid$provider <- "fixture"
  grid$provider_station_id <- grid$station_uid
  grid$swe_in <- 0
  grid$source_element <- "fixture"
  grid$swe_source_class <- "fixture"
  grid$swe_source_label <- "fixture"
  grid$swe_source_note <- "offline fixture"
  grid
}
validate_observations <- function(context, observations, cdec = FALSE) {
  prefix <- if (cdec) "cdec" else "snotel"
  context[[paste0(prefix, "_swe_obs")]] <- observations
  eval(assignment(paste0(prefix, "_swe_valid")), envir = context)
  qa_name <- if (cdec) "cdec_provider_qa" else "awdb_swe_qa"
  eval(assignment(qa_name), envir = context)
  context[[qa_name]]
}
nrcs <- make_observations(oct6, oct6$snotel_stations$station_uid, 804L)
cdec <- make_observations(oct6, oct6$cdec_stations$station_uid, 563L)
stopifnot(validate_observations(oct6, nrcs)$passed,
          validate_observations(oct6, cdec, cdec = TRUE)$passed,
          any(oct6$snotel_swe_valid$obs_date == oct6$TODAY_LOCAL),
          any(oct6$cdec_swe_valid$obs_date == oct6$TODAY_LOCAL))
assert_equal(oct6$pt_swe_num(c("0", NA_character_, "")), c(0, NA_real_, NA_real_),
             "Zero is valid; missing stays missing")
missing <- nrcs
missing$swe_in[] <- NA_real_
stopifnot(!validate_observations(oct6, missing)$passed,
          nrow(oct6$snotel_swe_valid) == 0L,
          !validate_observations(oct6, nrcs[1:787, ])$passed,
          !validate_observations(oct6, cdec[1:548, ], cdec = TRUE)$passed)
message("PASS: current-day zeros may be used; missing and insufficient rows fail closed")

outside <- cdec
outside$obs_date[1L] <- as.Date("2026-09-30")
qa <- validate_observations(oct6, outside, cdec = TRUE)
stopifnot(!qa$passed, "observation dates fall outside the requested fetch window" %in% qa$problems)
assert_equal(oct6$cdec_swe_valid$obs_date, outside$obs_date, "Prior-WY date is not rewritten")
assert_equal(oct6$pt_water_year(as.Date(c("2026-09-30", "2026-10-01"))),
             c(2026L, 2027L), "Observation water years remain distinct")
outside$obs_date[1L] <- as.Date("2026-10-07")
qa <- validate_observations(oct6, outside, cdec = TRUE)
stopifnot(!qa$passed, "observation dates fall outside the requested fetch window" %in% qa$problems)
message("PASS: unchanged CDEC date gate rejects prior-WY and future observations")

message("All offline snow-pillow local-date and completed-day tests passed.")
