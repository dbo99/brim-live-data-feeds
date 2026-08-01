#!/usr/bin/env Rscript

source("scripts/cbrfc_major_water_supply_forecast_helpers.R")

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

assert_error <- function(code, pattern, message, expected_class = NULL) {
  error <- tryCatch({ force(code); NULL }, error = function(e) e)
  if (!inherits(error, "error") || !grepl(pattern, conditionMessage(error), fixed = TRUE)) {
    stop(
      message,
      "\nExpected error containing: ", pattern,
      "\nActual: ", if (inherits(error, "error")) conditionMessage(error) else "no error",
      call. = FALSE
    )
  }
  if (!is.null(expected_class) && !inherits(error, expected_class)) {
    stop(message, "\nExpected error class: ", expected_class, call. = FALSE)
  }
  invisible(error)
}

scenario <- function(name, code) {
  force(code)
  message("PASS: ", name)
}

fixture <- function(name) {
  paste(readLines(file.path("tests/fixtures/cbrfc_forecasts", name), warn = FALSE), collapse = "\n")
}

mutate_json <- function(text, changes) {
  value <- jsonlite::fromJSON(text, simplifyVector = FALSE)
  for (name in names(changes)) value[[name]] <- changes[[name]]
  as.character(jsonlite::toJSON(value, auto_unbox = TRUE, null = "null", digits = NA))
}

roster <- cbrfc_read_roster("data/input/cbrfc_major_water_supply_forecast_sources.csv")
april_july_row <- cbrfc_roster_row(roster, "april_july_water_supply_forecast")
water_year_row <- cbrfc_roster_row(roster, "water_year_unregulated_inflow_forecast")
local_monthly_row <- cbrfc_roster_row(
  roster, "lake_mead_local_intervening_monthly_forecast"
)
normal_json <- fixture("glda3_normal.json")
absent_csv <- fixture("list_absent_july.csv")
situational_html <- fixture("glda3_situational_normal.html")
local_monthly_csv <- fixture("lksa3_local_monthly_normal.csv")
local_rollover_csv <- fixture("lksa3_local_monthly_bad_rollover.csv")
retrieved_at <- "2026-07-31T22:30:00Z"

normal_record <- function(point = normal_json,
                          list = absent_csv,
                          retrieved = retrieved_at,
                          water_year = 2026L,
                          dashboard = situational_html) {
  cbrfc_parse_response(
    point, list, april_july_row, retrieved, water_year,
    dashboard_html = dashboard
  )
}

normal_water_year_record <- function(html = situational_html,
                                     retrieved = retrieved_at,
                                     water_year = 2026L) {
  cbrfc_parse_water_year_response(
    html, water_year_row, retrieved, water_year
  )
}

normal_local_monthly_record <- function(source = local_monthly_csv,
                                        retrieved = retrieved_at,
                                        prior_issue = NULL) {
  cbrfc_parse_local_monthly_response(
    source, local_monthly_row, retrieved, prior_issue_text = prior_issue
  )
}

corrected_local_monthly_record <- function(retrieved = retrieved_at) {
  normal_local_monthly_record(local_rollover_csv, retrieved, local_monthly_csv)
}

success_results <- function(april = normal_record(),
                            water_year = normal_water_year_record(),
                            local_monthly = normal_local_monthly_record()) {
  list(
    cbrfc_success_result(april),
    cbrfc_success_result(water_year),
    cbrfc_success_result(local_monthly)
  )
}

scenario("reviewed CBRFC roster contains exactly three ordered records and no geometry", {
  assert_equal(nrow(roster), 3L, "CBRFC roster count changed.")
  assert_equal(roster$forecast_key, c(
    "CBRFC:GLDA3:APR_JUL_WSUP",
    "CBRFC:GLDA3:WATER_YEAR_INFLOW",
    "CBRFC:LKSA3:LOCAL_INTERVENING_MONTHLY"
  ), "CBRFC keys/order changed.")
  assert_equal(roster$product_type, c(
    "april_july_water_supply_forecast",
    "water_year_unregulated_inflow_forecast",
    "lake_mead_local_intervening_monthly_forecast"
  ), "CBRFC product types/order changed.")
  assert_true(!any(grepl(
    "geometry|coordinates|latitude|longitude|huc",
    names(roster),
    ignore.case = TRUE
  )), "CBRFC roster contains geometry fields.")
})

scenario("popup source links have exact public roles and contain no reservoir-operations link", {
  required_roles <- c("source_url", "retrieval_url_template", "summary_url", "archive_url")
  assert_true(all(required_roles %in% names(roster)), "CBRFC popup URL roles are incomplete.")
  assert_true(all(nzchar(as.matrix(roster[, required_roles]))), "A required CBRFC URL is empty.")
  assert_true(!any(grepl(
    "usbr[.]gov|localhost|127[.]0[.]0[.]1|file:|feature/|/Users/",
    as.matrix(roster[, required_roles]),
    ignore.case = TRUE
  )), "CBRFC popup links include an operations, local, or branch URL.")
  april <- normal_record(water_year = 2026L)
  assert_true(grepl("year=2026$", april$source_url), "Human graph URL used runner time.")
  assert_true(grepl("year=2026$", april$retrieval_url), "Structured URL used runner time.")
  assert_equal(april$summary_url, "https://www.cbrfc.noaa.gov/dash/lp.php",
               "April-July summary URL changed.")
})

scenario("Lake Mead Local parser preserves 12 direct monthly values and percent medians", {
  record <- normal_local_monthly_record(
    local_monthly_csv, "2026-06-02T18:00:00Z"
  )
  assert_equal(record$source_identifier, "LKSA3 QCMPLCM", "Local source identifier changed.")
  assert_equal(record$forecast_issue_date, "2026-06-01", "Local issue date changed.")
  assert_equal(record$source_units, "KAF", "Local exact source units changed.")
  assert_equal(record$normalized_units, "kaf", "Local normalized units changed.")
  assert_equal(record$source_normal_term, "median", "Local normal terminology changed.")
  assert_equal(length(record$monthly_forecasts), 12L, "Local monthly count changed.")
  assert_equal(record$monthly_forecasts[[1L]]$forecast_month, "2026-06", "First month changed.")
  assert_equal(record$monthly_forecasts[[1L]]$forecast_volume, 57, "First local volume changed.")
  assert_equal(record$monthly_forecasts[[1L]]$percent_median, 95, "First percent median changed.")
  assert_equal(record$monthly_forecasts[[1L]]$source_statistic, "ESP 50% EXCEEDANCE",
               "Local source statistic changed.")
  assert_equal(record$monthly_forecasts[[1L]]$source_percentage_label, "%Med",
               "Local source percentage label changed.")
  assert_equal(record$monthly_forecasts[[12L]]$forecast_month, "2027-05", "Last month changed.")
  assert_true(!any(c(
    "forecast_volume", "percent_median", "total_forecast_volume",
    "water_year_forecast_volume"
  ) %in% setdiff(names(record), "monthly_forecasts")),
  "Local record published a calculated aggregate.")
})

scenario("Lake Mead Local zero is valid and malformed, missing, and duplicate rows fail", {
  zero <- sub(
    "2026,June,57,95%,,", "2026,June,0,0%,,",
    local_monthly_csv, fixed = TRUE
  )
  zero_record <- normal_local_monthly_record(zero, "2026-06-02T18:00:00Z")
  assert_equal(zero_record$monthly_forecasts[[1L]]$forecast_volume, 0, "Local zero became missing.")
  assert_true(
    zero_record$monthly_forecasts[[1L]]$metric_state$forecast_volume$map_eligible,
    "Current local zero is not map eligible."
  )
  malformed <- sub(
    "2026,June,57,95%,,", "2026,June,57 kaf,ninety%,,",
    local_monthly_csv, fixed = TRUE
  )
  missing <- sub("2026,July,63,89%,,\n", "", local_monthly_csv, fixed = TRUE)
  duplicate <- sub(
    "2026,July,63,89%,,\n",
    "2026,June,63,89%,,\n2026,July,63,89%,,\n",
    local_monthly_csv,
    fixed = TRUE
  )
  cases <- list(
    list(text = malformed, expected = "forecast volume was malformed"),
    list(text = missing, expected = "exactly 12 rows"),
    list(text = duplicate, expected = "exactly 12 rows"),
    list(
      text = sub("VOLUMES (KAF)", "VOLUMES (MAF)", local_monthly_csv, fixed = TRUE),
      expected = "units or forecast statistic changed"
    )
  )
  for (case in cases) {
    assert_error(
      normal_local_monthly_record(case$text, "2026-07-31T22:30:00Z"),
      case$expected,
      "Invalid Lake Mead Local source was accepted.",
      "cbrfc_validation_error"
    )
  }
})

scenario("Lake Mead Local month validity and source staleness remain independent", {
  current <- normal_local_monthly_record(local_monthly_csv, "2026-06-02T18:00:00Z")
  partial <- normal_local_monthly_record(local_monthly_csv, "2026-07-01T07:00:00Z")
  stale <- normal_local_monthly_record(local_monthly_csv, "2026-07-15T18:00:00Z")
  assert_equal(current$status, "current_partial", "Fresh local series did not isolate its valid month.")
  assert_equal(partial$status, "current_partial", "Expired June did not produce partial state.")
  assert_equal(partial$monthly_forecasts[[1L]]$status, "expired", "June did not expire July 1.")
  assert_equal(partial$monthly_forecasts[[2L]]$status, "current", "July did not remain current.")
  assert_equal(stale$status, "source_stale", "Local source did not stale after 40 days.")
  assert_equal(
    current$monthly_forecasts[[1L]]$valid_through,
    "2026-07-01T00:00:00-07:00",
    "Monthly expiration boundary changed."
  )
})

scenario("reviewed July 2026 January label correction preserves every source value and provenance", {
  record <- corrected_local_monthly_record("2026-07-31T22:30:00Z")
  january <- record$monthly_forecasts[[7L]]
  assert_equal(january$raw_forecast_month_label, "January 2026", "Raw January label was lost.")
  assert_equal(january$forecast_month, "2027-01", "January was not corrected to 2027-01.")
  assert_true(january$source_date_override_applied, "Reviewed correction was not marked.")
  assert_equal(january$source_date_override_id, CBRFC_LOCAL_DATE_OVERRIDE_ID,
               "Reviewed correction ID changed.")
  assert_equal(january$source_date_override_reason, CBRFC_LOCAL_DATE_OVERRIDE_REASON,
               "Reviewed correction reason changed.")
  assert_equal(january$source_date_override_evidence_url,
               CBRFC_LOCAL_DATE_OVERRIDE_EVIDENCE_URL,
               "Reviewed correction evidence URL changed.")
  assert_equal(january$source_date_override_prior_issue_date, "2026-06-01",
               "Prior issue evidence date changed.")
  assert_equal(
    vapply(record$monthly_forecasts, `[[`, numeric(1), "forecast_volume"),
    c(64, 72, 68, 70, 67, 72, 74, 78, 96, 80, 76, 62),
    "A source-published volume changed during date correction."
  )
  assert_equal(
    vapply(record$monthly_forecasts, `[[`, numeric(1), "percent_median"),
    c(90, 83, 93, 93, 96, 95, 95, 104, 103, 108, 110, 103),
    "A source-published percent median changed during date correction."
  )
  assert_equal(record$diagnostic$source_date_override_count, 1L,
               "Correction diagnostic count changed.")
  payload <- cbrfc_build_payload(
    roster,
    success_results(local_monthly = record),
    generated_at = "2026-07-31T22:31:00Z",
    water_year = 2026L
  )
  correction_notices <- Filter(function(notice) {
    identical(notice$notice_id, CBRFC_LOCAL_DATE_OVERRIDE_ID)
  }, payload$operational_notices)
  assert_equal(length(correction_notices), 1L,
               "Reviewed correction operational notice is missing or duplicated.")
})

scenario("date correction is narrow, archive-confirmed, and rejects every unreviewed repair", {
  valid <- normal_local_monthly_record(local_monthly_csv, "2026-06-02T18:00:00Z")
  assert_true(!any(vapply(
    valid$monthly_forecasts, `[[`, logical(1), "source_date_override_applied"
  )), "A fully valid sequence received an override.")
  assert_error(
    normal_local_monthly_record(local_rollover_csv, retrieved_at),
    "requires the immediately preceding official issue",
    "The correction was applied without preceding-issue confirmation.",
    "cbrfc_validation_error"
  )
  wrong_prior <- sub("2027,January,72,92%,,", "2026,January,72,92%,,",
                     local_monthly_csv, fixed = TRUE)
  assert_error(
    normal_local_monthly_record(local_rollover_csv, retrieved_at, wrong_prior),
    "not 12 consecutive issue-month forecasts",
    "A prior issue without January 2027 confirmed the correction."
  )
  two_bad <- sub("2026,August,72,83%,,", "2025,August,72,83%,,",
                 local_rollover_csv, fixed = TRUE)
  assert_error(
    normal_local_monthly_record(two_bad, retrieved_at, local_monthly_csv),
    "not 12 consecutive issue-month forecasts",
    "Two bad year labels were repaired."
  )
  swapped <- sub("2026,August,72,83%,,", "ROW_SWAP", local_rollover_csv, fixed = TRUE)
  swapped <- sub("2026,September,68,93%,,", "2026,August,72,83%,,",
                 swapped, fixed = TRUE)
  swapped <- sub("ROW_SWAP", "2026,September,68,93%,,", swapped, fixed = TRUE)
  assert_error(
    normal_local_monthly_record(swapped, retrieved_at, local_monthly_csv),
    "month names were not the exact ordered 12-month sequence",
    "Incorrect month order was repaired."
  )
  ambiguous_prior <- sub("2027,February,77,103%,,", "2027,January,77,103%,,",
                         local_monthly_csv, fixed = TRUE)
  assert_error(
    normal_local_monthly_record(local_rollover_csv, retrieved_at, ambiguous_prior),
    "month names were not the exact ordered 12-month sequence",
    "Ambiguous prior confirmation was accepted."
  )
  missing_issue <- sub("7/1/2026,,,,,", ",,,,,", local_rollover_csv, fixed = TRUE)
  assert_error(
    normal_local_monthly_record(missing_issue, retrieved_at, local_monthly_csv),
    "issue date was not M/D/YYYY",
    "Missing issue date was repaired."
  )
  future_format <- sub("Year,Month,Lake Mead", "CalendarYear,Month,Lake Mead",
                       local_rollover_csv, fixed = TRUE)
  assert_error(
    normal_local_monthly_record(future_format, retrieved_at, local_monthly_csv),
    "ordered forecast headers changed",
    "Future source format drift was accepted."
  )
})

scenario("unavailable total Lake Mead endpoint is not converted or synthesized", {
  assert_equal(trimws(fixture("lksa3_total_endpoint_unavailable.txt")), "false",
               "Reviewed unavailable total endpoint fixture changed.")
  assert_true(
    !"CBRFC:LKSA3:TOTAL_UNREGULATED_INFLOW" %in% roster$forecast_key,
    "Unsupported total Lake Mead product entered the roster."
  )
})

scenario("water-year semantic parser preserves direct official values and omits percent median", {
  source <- cbrfc_extract_water_year_source(situational_html, water_year_row, 2026L)
  record <- normal_water_year_record()
  assert_equal(source$observed_to_date, 3281, "Water-year observed-to-date source value changed.")
  assert_equal(record$forecast_volume, 3481, "Water-year full forecast changed.")
  assert_equal(record$percent_average, 36, "Water-year percent average changed.")
  assert_equal(record$forecast_period, "Water Year", "Water-year period changed.")
  assert_equal(record$forecast_issue_date, "2026-07-01", "Water-year issue date changed.")
  assert_equal(record$source_time_precision, "date", "Water-year source precision changed.")
  assert_equal(record$forecast_type, "Unregulated", "Water-year forecast type changed.")
  assert_equal(record$forecast_statistic, "official_full_forecast", "Water-year statistic label changed.")
  assert_true(!"percent_median" %in% names(record), "Water-year percent median was inferred or published.")
  assert_true(!any(grepl(
    "geometry|coordinates|latitude|longitude|huc",
    names(record),
    ignore.case = TRUE
  )), "Water-year record contains geometry fields.")
})

scenario("April-July and water-year values remain independent and distinct", {
  april <- normal_record()
  water_year <- normal_water_year_record()
  assert_equal(april$forecast_volume, 1080, "April-July volume changed.")
  assert_equal(april$percent_average, 17, "April-July percent average changed.")
  assert_equal(april$percent_median, 18, "April-July direct percent median changed.")
  assert_equal(water_year$forecast_volume, 3481, "Water-year volume changed.")
  assert_equal(water_year$percent_average, 36, "Water-year percent average changed.")
  assert_true(!identical(april$forecast_key, water_year$forecast_key), "Period identities collapsed.")
})

scenario("dashboard cross-check matches at display precision and preserves point percent median", {
  record <- normal_record()
  assert_equal(record$diagnostic$dashboard_crosscheck_status, "matched",
               "Matching dashboard was not recognized.")
  assert_equal(record$diagnostic$dashboard_volume_display_tolerance, 0.5,
               "Dashboard integer display tolerance changed.")
  assert_equal(record$percent_median, 18, "Dashboard replaced structured percent median.")
})

scenario("dashboard omission, lag, and maintenance are notices while a current conflict fails", {
  missing <- normal_record(
    dashboard = fixture("glda3_dashboard_missing_april_value.html")
  )
  lagging <- normal_record(dashboard = fixture("glda3_dashboard_lagging.html"))
  maintenance <- normal_record(dashboard = fixture("glda3_dashboard_maintenance.html"))
  assert_equal(missing$diagnostic$dashboard_crosscheck_status, "missing_field",
               "Dashboard missing field invalidated the primary.")
  assert_equal(lagging$diagnostic$dashboard_crosscheck_status, "lagging",
               "Lagging dashboard was not diagnosed.")
  assert_equal(maintenance$diagnostic$dashboard_crosscheck_status,
               "unavailable_or_unparseable",
               "Dashboard maintenance response invalidated the primary.")
  notice_payload <- cbrfc_build_payload(
    roster,
    success_results(april = missing),
    generated_at = "2026-07-31T22:31:00Z",
    water_year = 2026L
  )
  assert_true(any(vapply(notice_payload$operational_notices, function(notice) {
    identical(notice$notice_type, "summary_crosscheck")
  }, logical(1))), "Dashboard omission did not create an operational notice.")
  assert_error(
    normal_record(dashboard = fixture("glda3_dashboard_conflict.html")),
    "materially conflicted",
    "Materially conflicting current official values were silently resolved.",
    "cbrfc_validation_error"
  )
})

scenario("GLDA3 parser uses official off fields and excludes raw ESP and volatile metadata", {
  record <- normal_record()
  assert_equal(record$forecast_volume, 1080, "GLDA3 off50 changed.")
  assert_equal(record$percent_average, 17, "GLDA3 offpavg changed.")
  assert_equal(record$percent_median, 18, "GLDA3 offpmed changed.")
  assert_equal(record$forecast_issue_date, "2026-07-01", "GLDA3 offdate changed.")
  assert_equal(record$source_time_precision, "date", "CBRFC date precision changed.")
  assert_equal(record$source_normal_term, "average", "CBRFC source normal term changed.")
  assert_equal(record$forecast_period, "Apr 1-Jul 31", "CBRFC period changed.")
  assert_equal(record$forecast_statistic, "50_percent_exceedance", "CBRFC statistic changed.")
  assert_equal(record$forecast_type, "Unregulated", "CBRFC forecast type changed.")
  assert_equal(record$source_units, "kaf", "CBRFC source units changed.")
  assert_equal(record$normalized_units, "kaf", "CBRFC normalized units changed.")
  assert_equal(record$diagnostic$secondary_list_status, "absent_for_issue_date", "July list absence changed.")
  assert_true(!any(grepl(
    "^esp|dateline|geometry|coordinates|latitude|longitude|huc",
    names(record),
    ignore.case = TRUE
  )), "CBRFC output leaked raw guidance, volatile time, or geometry.")
})

scenario("matching official-list row is a secondary QA cross-check", {
  record <- normal_record(
    fixture("glda3_june.json"),
    fixture("list_matching_june.csv"),
    "2026-06-02T18:00:00Z",
    dashboard = fixture("glda3_dashboard_lagging.html")
  )
  assert_equal(record$forecast_volume, 950, "Matching list changed point-authoritative volume.")
  assert_equal(record$diagnostic$secondary_list_status, "matched", "Matching list was not recognized.")
})

scenario("valid official zero is accepted only with an issue date", {
  zero <- mutate_json(normal_json, list(off50 = 0, offpavg = 0, offpmed = 0))
  record <- normal_record(zero, dashboard = NULL)
  assert_equal(record$forecast_volume, 0, "Valid official zero became null.")
  assert_equal(record$percent_average, 0, "Valid official zero percent became null.")
  assert_true(record$metric_state$forecast_volume$map_eligible, "Current official zero is not map eligible.")
})

scenario("blank-date zero sentinels are explicit source unavailability", {
  assert_error(
    cbrfc_parse_response(
      fixture("glda3_future_sentinel.json"), absent_csv, april_july_row,
      retrieved_at, 2027L
    ),
    "blank-date zero sentinels were not accepted as forecasts",
    "Future-WY sentinels were accepted as real zeros.",
    "cbrfc_source_unavailable"
  )
})

scenario("missing official fields never fall back to daily ESP guidance", {
  assert_error(
    cbrfc_parse_response(
      fixture("glda3_missing_official_with_esp.json"), absent_csv,
      april_july_row, retrieved_at, 2026L
    ),
    "off50 must be one finite nonnegative number",
    "Daily ESP guidance replaced missing official values.",
    "cbrfc_validation_error"
  )
})

scenario("malformed JSON, HTML, false, and truncated responses fail parsing", {
  for (text in c(
    fixture("http_200_error.html"),
    "false",
    fixture("truncated.json")
  )) {
    assert_error(
      cbrfc_parse_point_response(text, april_july_row, 2026L),
      "malformed JSON or not an object",
      "Malformed CBRFC response was accepted."
    )
  }
})

scenario("wrong ID, year, units, and forecast type are validation failures", {
  cases <- list(
    list(change = list(id = "CLRU1"), message = "identifier did not match"),
    list(change = list(wy = 2025L), message = "water year did not match"),
    list(change = list(names = list(units = "cfs")), message = "unrecognized units"),
    list(change = list(esptype = "Regulated"), message = "forecast type changed")
  )
  for (case in cases) {
    assert_error(
      cbrfc_parse_point_response(mutate_json(normal_json, case$change), april_july_row, 2026L),
      case$message,
      "CBRFC identity/unit/type drift was accepted.",
      "cbrfc_validation_error"
    )
  }
})

scenario("wrong period and duplicate official-list rows fail validation", {
  matching <- fixture("list_matching_june.csv")
  wrong_period <- sub("Apr 1-Jul 31", "Jan 1-Jul 31", matching, fixed = TRUE)
  duplicate <- paste(matching, tail(strsplit(matching, "\n", fixed = TRUE)[[1L]], 1L), sep = "\n")
  point <- cbrfc_parse_point_response(fixture("glda3_june.json"), april_july_row, 2026L)
  assert_error(
    cbrfc_parse_list_crosscheck(wrong_period, point, april_july_row),
    "forecast period did not match", "Wrong CBRFC forecast period was accepted.",
    "cbrfc_validation_error"
  )
  assert_error(
    cbrfc_parse_list_crosscheck(duplicate, point, april_july_row),
    "duplicate GLDA3 rows", "Duplicate CBRFC list rows were accepted.",
    "cbrfc_validation_error"
  )
})

scenario("point/list conflicts and negative official values fail validation", {
  matching <- sub(",950.0,1200.0", ",951.0,1200.0", fixture("list_matching_june.csv"), fixed = TRUE)
  point <- cbrfc_parse_point_response(fixture("glda3_june.json"), april_july_row, 2026L)
  assert_error(
    cbrfc_parse_list_crosscheck(matching, point, april_july_row),
    "materially conflicted", "Point/list conflict was silently resolved.",
    "cbrfc_validation_error"
  )
  assert_error(
    cbrfc_parse_point_response(mutate_json(normal_json, list(off50 = -1)), april_july_row, 2026L),
    "finite nonnegative", "Negative official CBRFC volume was accepted.",
    "cbrfc_validation_error"
  )
})

scenario("40-day active-season staleness and August 1 expiration are source-date anchored", {
  january <- mutate_json(normal_json, list(offdate = "2026-01-01"))
  stale <- normal_record(
    january, absent_csv, "2026-02-11T20:00:00Z", dashboard = NULL
  )
  expired <- normal_record(normal_json, absent_csv, "2026-08-01T08:00:00Z")
  assert_equal(stale$status, "source_stale", "CBRFC did not become stale after 40 calendar days.")
  assert_true(!stale$metric_state$forecast_volume$map_eligible, "Stale CBRFC volume is map eligible.")
  assert_equal(expired$status, "expired", "CBRFC did not expire after July 31.")
  assert_true(expired$metric_state$forecast_volume$popup_eligible, "Expired CBRFC provenance is hidden.")
})

scenario("water-year freshness is monthly-source anchored and validity ends after September 30", {
  august_first <- normal_water_year_record(
    situational_html, "2026-08-01T08:00:00Z"
  )
  stale <- normal_water_year_record(
    situational_html, "2026-08-11T20:00:00Z"
  )
  expired <- normal_water_year_record(
    situational_html, "2026-10-01T08:00:00Z"
  )
  assert_equal(august_first$status, "current", "Water-year record incorrectly expired on August 1.")
  assert_equal(
    august_first$valid_through, "2026-10-01T00:00:00-07:00",
    "Water-year validity did not follow the official water-year period."
  )
  assert_equal(stale$status, "source_stale", "Water-year record did not stale after 40 days.")
  assert_equal(expired$status, "expired", "Water-year record did not expire after September 30.")
  assert_equal(
    stale$metric_state$forecast_volume$source_issue_date, "2026-07-01",
    "Retrieval time extended water-year source freshness."
  )
})

scenario("controlled-date bootstrap remains possible across period boundaries", {
  cases <- list(
    list(local = "2026-07-31 23:59", utc = "2026-08-01T06:59:00Z", water_year = 2026L),
    list(local = "2026-08-01 00:01", utc = "2026-08-01T07:01:00Z", water_year = 2026L),
    list(local = "2026-09-01 12:00", utc = "2026-09-01T19:00:00Z", water_year = 2026L),
    list(local = "2026-10-01 00:01", utc = "2026-10-01T07:01:00Z", water_year = 2027L),
    list(local = "2027-01-01 12:00", utc = "2027-01-01T20:00:00Z", water_year = 2027L)
  )
  payloads <- lapply(cases, function(case) {
    cbrfc_build_payload(
      roster,
      success_results(
        april = normal_record(retrieved = case$utc),
        water_year = normal_water_year_record(retrieved = case$utc),
        local_monthly = corrected_local_monthly_record(case$utc)
      ),
      generated_at = case$utc,
      water_year = case$water_year
    )
  })
  july_end <- payloads[[1L]]
  august_start <- payloads[[2L]]
  september <- payloads[[3L]]
  october <- payloads[[4L]]
  january <- payloads[[5L]]
  assert_equal(july_end$records[[1L]]$status, "current",
               "April-July was not current through July 31.")
  assert_equal(august_start$records[[1L]]$status, "expired",
               "April-July did not expire at August 1 local midnight.")
  assert_true(!august_start$records[[1L]]$metric_state$forecast_volume$map_eligible,
              "Expired April-July provenance remained map eligible.")
  assert_equal(august_start$records[[2L]]$status, "current",
               "Water-year state was coupled to April-July expiration.")
  assert_equal(september$records[[2L]]$status, "source_stale",
               "Water-year source-age policy was bypassed in September.")
  assert_equal(october$records[[2L]]$status, "expired",
               "Water-year record did not expire on October 1.")
  july_items <- july_end$records[[3L]]$monthly_forecasts
  august_items <- august_start$records[[3L]]$monthly_forecasts
  assert_equal(july_items[[1L]]$status, "current", "July local month was not current.")
  assert_equal(july_items[[2L]]$status, "not_yet_valid", "August was eligible before August 1.")
  assert_equal(august_items[[1L]]$status, "expired", "July local month did not expire.")
  assert_equal(august_items[[2L]]$status, "current", "August did not become current.")
  assert_equal(sum(vapply(august_items, function(item) {
    isTRUE(item$metric_state$forecast_volume$map_eligible)
  }, logical(1))), 1L, "Exactly one local monthly volume was not map eligible.")
  assert_equal(january$records[[3L]]$monthly_forecasts[[7L]]$forecast_month,
               "2027-01", "Corrected January did not survive year-round bootstrap.")
  assert_equal(january$records[[1L]]$water_year, 2026L,
               "Runner water year rewrote validated source water year.")
  assert_true(grepl("year=2026$", january$records[[1L]]$source_url),
              "Year-specific source URL followed the runner rather than source provenance.")
  assert_true(all(vapply(payloads, function(payload) {
    identical(vapply(payload$records, `[[`, character(1), "forecast_key"), roster$forecast_key)
  }, logical(1))), "A controlled-date bootstrap lost a structural key.")
})

scenario("future pre-issuance sentinels remain explicit while unexplained failures stay failures", {
  unavailable <- cbrfc_failure_result(
    "source_unavailable",
    "blank-date zero sentinels were not accepted as forecasts",
    "2027-01-01T20:00:00Z"
  )
  local <- cbrfc_success_result(corrected_local_monthly_record("2027-01-01T20:00:00Z"))
  future <- cbrfc_build_payload(
    roster,
    list(unavailable, unavailable, local),
    generated_at = "2027-01-01T20:00:01Z",
    water_year = 2027L
  )
  assert_true(all(vapply(future$records[1:2], function(record) {
    identical(record$status, "unavailable") && is.null(record$forecast_volume)
  }, logical(1))), "Future Powell sentinels became zeros or nonstructural records.")
  failure <- cbrfc_failure_result("fetch_failed", "timeout", "2026-10-01T07:01:00Z")
  unexplained <- cbrfc_build_payload(
    roster,
    list(failure, failure, cbrfc_success_result(
      corrected_local_monthly_record("2026-10-01T07:01:00Z")
    )),
    generated_at = "2026-10-01T07:01:01Z",
    water_year = 2027L
  )
  assert_true(all(vapply(unexplained$records[1:2], function(record) {
    identical(record$attempt_outcome, "fetch_failed") &&
      identical(record$status, "failed_no_data")
  }, logical(1))), "Unexplained failures were relabeled out of season.")
})

scenario("water-year missing rows and sentinel values are source unavailable", {
  for (name in c(
    "glda3_situational_missing_water_year.html",
    "glda3_situational_missing_values.html"
  )) {
    assert_error(
      normal_water_year_record(fixture(name)),
      if (grepl("missing_water_year", name, fixed = TRUE)) {
        "omitted the Water Year forecast row"
      } else {
        "source-unavailable value sentinels"
      },
      "Missing water-year source values were accepted.",
      "cbrfc_source_unavailable"
    )
  }
  future <- sub("Water Year 2026", "Water Year 2025", situational_html, fixed = TRUE)
  assert_error(
    normal_water_year_record(future),
    "not requested water year 2026",
    "Prior-water-year situational data was accepted for the current water year.",
    "cbrfc_source_unavailable"
  )
})

scenario("water-year duplicate semantics and malformed numbers fail closed", {
  duplicate_row <- sub(
    "</table>",
    "<tr><td>Water Year</td><td>3281</td><td>3481</td><td>36%</td></tr></table>",
    situational_html,
    fixed = TRUE
  )
  malformed <- sub(">3481<", ">3.5 maf<", situational_html, fixed = TRUE)
  assert_error(
    normal_water_year_record(duplicate_row),
    "duplicate Water Year rows",
    "Duplicate water-year rows were accepted.",
    "cbrfc_validation_error"
  )
  assert_error(
    normal_water_year_record(malformed),
    "full forecast volume was malformed",
    "Malformed water-year forecast was accepted.",
    "cbrfc_validation_error"
  )
})

scenario("oversized responses fail before parsing", {
  fetched <- cbrfc_fetch_text(
    "https://example.invalid/oversized",
    allowed_content_type = "application/json",
    max_response_bytes = 10L,
    fetch_once = function(...) list(
      status_code = 200L,
      type = "application/json",
      content = charToRaw('{"payload":"too large"}')
    )
  )
  assert_true(!fetched$success, "Oversized CBRFC response was accepted.")
  assert_equal(fetched$failure_class, "response_too_large", "Oversized response taxonomy changed.")
})

prior_payload <- cbrfc_build_payload(
  roster,
  success_results(),
  generated_at = "2026-07-31T22:31:00Z",
  water_year = 2026L
)

scenario("bootstrap retains one valid period when the other source fails", {
  failure <- cbrfc_failure_result("fetch_failed", "timeout", "2026-07-31T23:00:00Z")
  april_only <- cbrfc_build_payload(
    roster,
    list(cbrfc_success_result(normal_record()), failure, failure),
    generated_at = "2026-07-31T23:00:01Z",
    water_year = 2026L
  )
  water_year_only <- cbrfc_build_payload(
    roster,
    list(failure, cbrfc_success_result(normal_water_year_record()), failure),
    generated_at = "2026-07-31T23:00:01Z",
    water_year = 2026L
  )
  assert_equal(april_only$records[[1L]]$status, "current", "Valid April-July bootstrap was invalidated.")
  assert_equal(april_only$records[[2L]]$status, "failed_no_data", "Missing water-year source was hidden.")
  assert_equal(water_year_only$records[[1L]]$status, "failed_no_data", "Missing April-July source was hidden.")
  assert_equal(water_year_only$records[[2L]]$status, "current", "Valid water-year bootstrap was invalidated.")
  assert_error(
    cbrfc_build_payload(
      roster, list(failure, failure, failure),
      generated_at = "2026-07-31T23:00:01Z", water_year = 2026L
    ),
    "no reviewed CBRFC family established official data",
    "All-failed active bootstrap was accepted."
  )
})

scenario("source-confirmed missing periods remain isolated in either direction", {
  unavailable <- cbrfc_failure_result(
    "source_unavailable", "reviewed period row unavailable", "2026-07-31T23:05:00Z"
  )
  april_only <- cbrfc_build_payload(
    roster,
    list(cbrfc_success_result(normal_record()), unavailable, unavailable),
    generated_at = "2026-07-31T23:05:01Z",
    water_year = 2026L
  )
  water_year_only <- cbrfc_build_payload(
    roster,
    list(unavailable, cbrfc_success_result(normal_water_year_record()), unavailable),
    generated_at = "2026-07-31T23:05:01Z",
    water_year = 2026L
  )
  assert_equal(april_only$records[[1L]]$status, "current", "Valid April-July source was erased.")
  assert_equal(april_only$records[[2L]]$status, "unavailable", "Missing water-year source was not explicit.")
  assert_equal(water_year_only$records[[1L]]$status, "unavailable", "Missing April-July source was not explicit.")
  assert_equal(water_year_only$records[[2L]]$status, "current", "Valid water-year source was erased.")
})

scenario("inactive Powell periods coexist with a valid year-round local series", {
  unavailable <- cbrfc_failure_result(
    "source_unavailable", "no official issue", "2026-10-15T20:00:00Z"
  )
  inactive <- cbrfc_build_payload(
    roster,
    list(
      unavailable,
      unavailable,
      cbrfc_success_result(normal_local_monthly_record(
        local_monthly_csv, "2026-10-15T20:00:00Z"
      ))
    ),
    generated_at = "2026-10-15T20:00:01Z",
    water_year = 2027L
  )
  assert_true(all(vapply(inactive$records[1:2], function(record) {
    identical(record$status, "unavailable")
  }, logical(1))), "Inactive Powell structural states changed.")
  assert_true(all(vapply(inactive$family_health[1:2], function(family) {
    identical(family$health, "healthy")
  }, logical(1))), "Inactive Powell family health changed.")
  assert_equal(inactive$records[[3L]]$attempt_outcome, "success",
               "Year-round local family did not establish bootstrap.")
})

scenario("water-year source-date regression retains only newer water-year provenance", {
  older_html <- sub("2026-07-01", "2026-06-01", situational_html, fixed = TRUE)
  older_water_year <- normal_water_year_record(
    older_html, "2026-07-31T23:10:00Z"
  )
  payload <- cbrfc_build_payload(
    roster,
    success_results(water_year = older_water_year),
    prior_payload,
    "2026-07-31T23:10:01Z",
    2026L
  )
  assert_equal(payload$records[[1L]]$attempt_outcome, "success", "April-July was affected by water-year regression.")
  assert_equal(payload$records[[2L]]$attempt_outcome, "validation_failed", "Water-year regression was not rejected.")
  assert_equal(payload$records[[2L]]$forecast_issue_date, "2026-07-01", "Older water-year issue replaced provenance.")
  assert_equal(payload$records[[2L]]$value_origin, "last_known_good", "Water-year regression did not retain LKG.")
})

scenario("each period can fail without erasing or invalidating the other", {
  failure <- cbrfc_failure_result("fetch_failed", "timeout", "2026-07-31T23:45:00Z")
  water_year_failed <- cbrfc_build_payload(
    roster,
    list(
      cbrfc_success_result(normal_record()),
      failure,
      cbrfc_success_result(normal_local_monthly_record())
    ),
    prior_payload,
    "2026-07-31T23:45:01Z",
    2026L
  )
  april_failed <- cbrfc_build_payload(
    roster,
    list(
      failure,
      cbrfc_success_result(normal_water_year_record()),
      cbrfc_success_result(normal_local_monthly_record())
    ),
    prior_payload,
    "2026-07-31T23:45:01Z",
    2026L
  )
  assert_equal(water_year_failed$records[[1L]]$status, "current", "April-July was erased by water-year failure.")
  assert_equal(water_year_failed$records[[2L]]$status, "stale_last_known_good", "Water-year LKG was not retained.")
  assert_equal(april_failed$records[[1L]]$status, "stale_last_known_good", "April-July LKG was not retained.")
  assert_equal(april_failed$records[[2L]]$status, "current", "Water-year was erased by April-July failure.")
  assert_equal(
    water_year_failed$family_health$water_year_unregulated_inflow_forecast$health,
    "outage_using_last_known_good",
    "Water-year family outage was not isolated."
  )
})

scenario("Lake Mead Local failure is isolated from both Powell records and retains only its own LKG", {
  failure <- cbrfc_failure_result(
    "validation_failed", "monthly source malformed", "2026-07-31T23:46:00Z"
  )
  payload <- cbrfc_build_payload(
    roster,
    list(
      cbrfc_success_result(normal_record()),
      cbrfc_success_result(normal_water_year_record()),
      failure
    ),
    prior_payload,
    "2026-07-31T23:46:01Z",
    2026L
  )
  assert_equal(payload$records[[1L]]$attempt_outcome, "success", "April-July was affected.")
  assert_equal(payload$records[[2L]]$attempt_outcome, "success", "Water year was affected.")
  assert_equal(payload$records[[3L]]$attempt_outcome, "validation_failed", "Local failure was hidden.")
  assert_equal(payload$records[[3L]]$value_origin, "last_known_good", "Local LKG was not retained.")
  assert_equal(length(payload$records[[3L]]$monthly_forecasts), 12L, "Local LKG series was erased.")
})

scenario("all CBRFC families can fail independently while valid prior provenance is retained", {
  failure <- cbrfc_failure_result("fetch_failed", "source outage", "2026-07-31T23:46:30Z")
  payload <- cbrfc_build_payload(
    roster,
    list(failure, failure, failure),
    prior_payload,
    "2026-07-31T23:46:31Z",
    2026L
  )
  assert_true(all(vapply(payload$records, function(record) {
    identical(record$attempt_outcome, "fetch_failed") &&
      identical(record$value_origin, "last_known_good")
  }, logical(1))), "All-family outage lost or mislabeled prior provenance.")
  assert_equal(payload$source_summary$failed_attempt_count, 3L,
               "All-family outage summary count changed.")
})

scenario("Lake Mead Local source-date regression retains the newer local series", {
  older <- sub("6/1/2026", "5/1/2026", local_monthly_csv, fixed = TRUE)
  older <- sub("2027,May,75,109%,,\n", "", older, fixed = TRUE)
  older <- sub(
    "Year,Month,Lake Mead Local Intervening Flow,%Med,Paria Creek nr Lees Ferry,%Med\n",
    paste0(
      "Year,Month,Lake Mead Local Intervening Flow,%Med,Paria Creek nr Lees Ferry,%Med\n",
      "2026,May,55,90%,,\n"
    ),
    older,
    fixed = TRUE
  )
  older_record <- normal_local_monthly_record(older, "2026-07-31T23:47:00Z")
  payload <- cbrfc_build_payload(
    roster,
    success_results(local_monthly = older_record),
    prior_payload,
    "2026-07-31T23:47:01Z",
    2026L
  )
  assert_equal(payload$records[[3L]]$attempt_outcome, "validation_failed",
               "Local source-date regression was accepted.")
  assert_equal(payload$records[[3L]]$forecast_issue_date, "2026-06-01",
               "Older local issue replaced accepted provenance.")
})

scenario("excluded guidance, observation, HTML, and retrieval-only changes are semantic no-ops", {
  changed_point <- mutate_json(normal_json, list(
    espdate = "2026-07-31",
    esp50 = 99999,
    esppavg = 999,
    esppmed = 999,
    dateline = "2030-01-01 00:00Z"
  ))
  changed_html <- sub(">3281<", ">3290<", situational_html, fixed = TRUE)
  changed_html <- sub("</body>", "<p>Unrelated page navigation changed.</p></body>", changed_html, fixed = TRUE)
  changed_dashboard <- sub(">1060<", ">1061<", situational_html, fixed = TRUE)
  changed_dashboard <- sub(
    "</body>", "<p>Volatile dashboard presentation changed.</p></body>",
    changed_dashboard, fixed = TRUE
  )
  changed_local <- sub(",,\n2026,July", ",999,999%\n2026,July", local_monthly_csv, fixed = TRUE)
  changed_local <- sub("total average: 990", "total average: 1000", changed_local, fixed = TRUE)
  candidate <- cbrfc_build_payload(
    roster,
    success_results(
      normal_record(
        changed_point, absent_csv, "2026-07-31T23:30:00Z",
        dashboard = changed_dashboard
      ),
      normal_water_year_record(changed_html, "2026-07-31T23:30:00Z"),
      normal_local_monthly_record(changed_local, "2026-07-31T23:30:00Z")
    ),
    prior_payload,
    "2026-07-31T23:30:01Z",
    2026L
  )
  assert_true(!cbrfc_payload_changed(candidate, prior_payload), "Semantic no-op forced publication.")
  assert_true(all(vapply(seq_along(candidate$records), function(index) {
    identical(
      candidate$records[[index]]$last_attempt_at,
      prior_payload$records[[index]]$last_attempt_at
    )
  }, logical(1))), "Semantic no-op changed a CBRFC attempt time.")
})

scenario("repeated retrieval of the same corrected local source is a semantic no-op", {
  corrected_prior <- cbrfc_build_payload(
    roster,
    success_results(local_monthly = corrected_local_monthly_record(
      "2026-07-31T22:30:00Z"
    )),
    generated_at = "2026-07-31T22:31:00Z",
    water_year = 2026L
  )
  corrected_candidate <- cbrfc_build_payload(
    roster,
    success_results(
      april = normal_record(retrieved = "2026-07-31T23:30:00Z"),
      water_year = normal_water_year_record(retrieved = "2026-07-31T23:30:00Z"),
      local_monthly = corrected_local_monthly_record("2026-07-31T23:30:00Z")
    ),
    corrected_prior,
    generated_at = "2026-07-31T23:30:01Z",
    water_year = 2026L
  )
  assert_true(!cbrfc_payload_changed(corrected_candidate, corrected_prior),
              "Repeated corrected source retrieval forced publication.")
  assert_equal(corrected_candidate$operational_notices,
               corrected_prior$operational_notices,
               "Correction operational notice changed on repeated retrieval.")
})

scenario("a direct water-year full-forecast revision is substantive", {
  revised_html <- sub(">3481<", ">3479<", situational_html, fixed = TRUE)
  candidate <- cbrfc_build_payload(
    roster,
    success_results(
      water_year = normal_water_year_record(revised_html, "2026-07-31T23:35:00Z")
    ),
    prior_payload,
    "2026-07-31T23:35:01Z",
    2026L
  )
  assert_equal(candidate$records[[2L]]$forecast_volume, 3479, "Direct source revision was not preserved.")
  assert_true(cbrfc_payload_changed(candidate, prior_payload), "Direct source revision became a semantic no-op.")
})

scenario("a direct Lake Mead Local monthly revision is substantive without aggregation", {
  revised <- sub("2026,July,63,89%,,", "2026,July,65,91%,,", local_monthly_csv, fixed = TRUE)
  candidate <- cbrfc_build_payload(
    roster,
    success_results(
      local_monthly = normal_local_monthly_record(revised, "2026-07-31T23:36:00Z")
    ),
    prior_payload,
    "2026-07-31T23:36:01Z",
    2026L
  )
  assert_equal(candidate$records[[3L]]$monthly_forecasts[[2L]]$forecast_volume, 65,
               "Direct local monthly revision was not preserved.")
  assert_true(cbrfc_payload_changed(candidate, prior_payload),
              "Direct local monthly revision became a semantic no-op.")
})

scenario("payload roundtrip is exact, three-record, geometry-free, and cleanly numeric", {
  cbrfc_validate_payload(prior_payload, roster)
  test_dir <- tempfile("cbrfc-roundtrip-")
  dir.create(test_dir)
  on.exit(unlink(test_dir, recursive = TRUE, force = TRUE), add = TRUE)
  output <- file.path(test_dir, "payload.json")
  cbrfc_write_json(prior_payload, output)
  roundtrip <- cbrfc_read_payload(output, roster)
  assert_equal(length(roundtrip$records), 3L, "CBRFC payload did not retain three records.")
  assert_equal(as.numeric(roundtrip$records[[1L]]$forecast_volume), 1080, "April-July volume did not round trip.")
  assert_equal(as.numeric(roundtrip$records[[2L]]$forecast_volume), 3481, "Water-year volume did not round trip.")
  assert_true(!"percent_median" %in% names(roundtrip$records[[2L]]), "Water-year percent median appeared after serialization.")
  assert_equal(
    as.numeric(roundtrip$records[[3L]]$monthly_forecasts[[1L]]$forecast_volume),
    57,
    "Local monthly volume did not round trip."
  )
  fields <- unique(unlist(lapply(roundtrip$records, names), use.names = FALSE))
  assert_true(!any(grepl(
    "geometry|coordinates|latitude|longitude|huc|dateline|^esp",
    fields,
    ignore.case = TRUE
  )), "CBRFC roundtrip contains forbidden source or geometry fields.")
  text <- paste(readLines(output, warn = FALSE), collapse = "\n")
  assert_true(!grepl("1080.000000|3481.000000", text), "CBRFC numeric serialization gained a floating tail.")

  before <- unname(tools::md5sum(output))
  invalid <- prior_payload
  invalid$records <- invalid$records[1L]
  invalid$actual_record_count <- 1L
  assert_error(
    cbrfc_stage_and_promote_payload(invalid, output, roster, prior_payload),
    "exactly three structural records", "Invalid CBRFC structure was promoted."
  )
  assert_equal(unname(tools::md5sum(output)), before, "Invalid CBRFC structure changed prior bytes.")
})

message("All CBRFC major water-supply forecast tests passed.")
