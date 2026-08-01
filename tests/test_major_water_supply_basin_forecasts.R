#!/usr/bin/env Rscript

source("scripts/cnrfc_major_water_supply_basin_forecast_helpers.R")

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

capture_error <- function(code) tryCatch({ force(code); NULL }, error = function(e) e)

assert_error <- function(code, pattern, message, expected_class = NULL) {
  error <- capture_error(code)
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
  paste(readLines(file.path("tests/fixtures/cnrfc_forecasts", name), warn = FALSE), collapse = "\n")
}

roster <- cnrfc_read_roster("data/input/cnrfc_major_water_supply_basin_forecast_sources.csv")
row_for <- function(lid) {
  roster[roster$nws_lid == lid & roster$product_type %in% c(
    "water_year_fnf", "water_year_index"
  ), , drop = FALSE]
}
accum_row_for <- function(lid) {
  roster[roster$nws_lid == lid & roster$product_type == "ten_day_streamflow_volume_accumulation", , drop = FALSE]
}
april_july_row_for <- function(lid) {
  roster[roster$nws_lid == lid & roster$product_type == "april_july_streamflow_volume_forecast", , drop = FALSE]
}
record_for <- function(payload, key) {
  payload$records[[which(vapply(payload$records, `[[`, character(1), "forecast_key") == key)]]
}
retrieved_at <- "2026-07-31T18:00:00Z"

scenario("reviewed roster is exact, ordered, and product-specific", {
  assert_equal(nrow(roster), 51L, "Roster count changed.")
  assert_equal(sum(roster$product_type == "water_year_fnf"), 15L, "FNF count changed.")
  assert_equal(sum(roster$product_type == "water_year_index"), 3L, "Index count changed.")
  assert_equal(sum(roster$product_type == "ten_day_streamflow_volume_accumulation"), 15L, "Product-2 count changed.")
  assert_equal(sum(roster$product_type == "april_july_streamflow_volume_forecast"), 18L, "Product-7 count changed.")
  assert_equal(anyDuplicated(roster$forecast_key), 0L, "forecast_key is not unique.")
  assert_equal(roster$nws_lid, cnrfc_expected_roster()$nws_lid, "Reviewed roster order changed.")
  assert_true(!any(roster$nws_lid %in% c("SACC0", "VNSC0", "MLIC0") &
    roster$product_type == "ten_day_streamflow_volume_accumulation"), "Index LID received product 2.")
  assert_equal(
    sum(roster$view_group == "index" & roster$product_type == "april_july_streamflow_volume_forecast"),
    3L,
    "Product-7 index roster changed."
  )
})

scenario("fuller sanitized product-9 basin and index pages parse direct values", {
  basin <- cnrfc_parse_page(fixture("full_normal_major_basin.html"), row_for("SHDC1"), retrieved_at)
  index <- cnrfc_parse_page(fixture("full_normal_index.html"), row_for("SACC0"), retrieved_at)
  assert_equal(basin$forecast_volume, 4980, "Basin volume changed.")
  assert_equal(basin$percent_mean, 89, "Basin percent mean changed.")
  assert_equal(basin$percent_median, 93, "Basin percent median changed.")
  assert_equal(index$forecast_volume, 16000, "Direct index volume changed.")
  assert_equal(index$forecast_key, "CNRFC:SACC0:WY_INDEX", "Index identity changed.")
  assert_equal(basin$status, "current", "Fresh basin record is not current.")
})

scenario("product-9 source staleness and seven-day expiration are issue-time anchored", {
  stale <- cnrfc_parse_page(
    fixture("normal_major_basin.html"), row_for("SHDC1"), "2026-08-03T20:00:00Z"
  )
  expired <- cnrfc_parse_page(
    fixture("normal_major_basin.html"), row_for("SHDC1"), "2026-08-08T15:00:00Z"
  )
  assert_equal(stale$status, "source_stale", "72-hour policy was not applied.")
  assert_true(!stale$metric_state$forecast_volume$map_eligible, "Stale value remained map eligible.")
  assert_equal(expired$status, "expired", "Seven-day hard expiry was not applied.")
  assert_true(expired$metric_state$forecast_volume$popup_eligible, "Expired provenance was hidden from popup.")
  assert_equal(expired$metric_state$forecast_volume$value_origin, "current_source", "Current attempt origin changed.")
})

scenario("product-7 major-basin and index pages preserve direct 50-percent-exceedance values", {
  basin <- cnrfc_parse_page(
    fixture("prod7_normal_major_basin_headline.html"),
    april_july_row_for("SHDC1"),
    retrieved_at,
    tabular_html = fixture("prod7_normal_major_basin_tabular.html")
  )
  index <- cnrfc_parse_page(
    fixture("prod7_normal_index_headline.html"),
    april_july_row_for("SACC0"),
    retrieved_at,
    tabular_html = fixture("prod7_normal_index_tabular.html")
  )
  assert_equal(basin$forecast_volume, 1160, "Product-7 basin forecast volume changed.")
  assert_equal(basin$percent_average, 68, "Product-7 Percent of Average changed.")
  assert_equal(basin$normal_average_volume, 1700, "Product-7 normal average changed.")
  assert_equal(basin$forecast_statistic, "50_percent_exceedance", "Product-7 statistic changed.")
  assert_equal(basin$forecast_period, "April-July", "Product-7 period changed.")
  assert_equal(basin$source_units, "kaf", "Product-7 exact source units changed.")
  assert_equal(basin$normalized_units, "kaf", "Product-7 normalized units changed.")
  assert_equal(index$forecast_volume, 3850, "Product-7 index volume changed.")
  assert_equal(index$percent_average, 64, "Product-7 index Percent of Average changed.")
  assert_true(!"percent_median" %in% names(basin), "Product-7 inferred or substituted percent_median.")
  assert_true(!"geometry" %in% names(basin), "Product-7 published geometry.")
})

scenario("product-7 source staleness and August 1 expiration follow seasonal semantics", {
  headline <- fixture("prod7_normal_major_basin_headline.html")
  tabular <- fixture("prod7_normal_major_basin_tabular.html")
  old_headline <- gsub("Jul 31, 2026", "Jun 01, 2026", headline, fixed = TRUE)
  old_tabular <- gsub("Jul 31 2026", "Jun 01 2026", tabular, fixed = TRUE)
  old_tabular <- gsub("07/31/2026", "06/01/2026", old_tabular, fixed = TRUE)
  old_tabular <- gsub("07/30/2026", "05/31/2026", old_tabular, fixed = TRUE)
  stale <- cnrfc_parse_page(
    old_headline,
    april_july_row_for("SHDC1"),
    "2026-06-03T18:00:00Z",
    tabular_html = old_tabular
  )
  expired <- cnrfc_parse_page(
    headline,
    april_july_row_for("SHDC1"),
    "2026-08-01T08:00:00Z",
    tabular_html = tabular
  )
  assert_equal(stale$status, "source_stale", "Daily product-7 source was not stale after 36 hours.")
  assert_equal(expired$status, "expired", "Product-7 did not expire after July 31.")
  assert_true(!expired$metric_state$forecast_volume$map_eligible, "Expired product-7 volume is map eligible.")
  assert_true(expired$metric_state$forecast_volume$popup_eligible, "Expired product-7 provenance is hidden.")
})

scenario("product-7 explicit out-of-season state is source-confirmed unavailability", {
  assert_error(
    cnrfc_parse_page(
      fixture("prod7_unavailable_headline.html"),
      april_july_row_for("SHDC1"),
      retrieved_at,
      tabular_html = fixture("prod7_unavailable_tabular.html")
    ),
    "explicitly reports product 7 unavailable",
    "Product-7 out-of-season response was not classified as source unavailable.",
    "cnrfc_source_unavailable"
  )
})

scenario("product-7 zero, percent above 100, and missing metrics remain distinct", {
  headline <- fixture("prod7_normal_major_basin_headline.html")
  tabular <- fixture("prod7_normal_major_basin_tabular.html")
  zero <- cnrfc_parse_page(
    gsub("1160", "0", headline, fixed = TRUE),
    april_july_row_for("SHDC1"),
    retrieved_at,
    tabular_html = gsub("1160", "0", tabular, fixed = TRUE)
  )
  high <- cnrfc_parse_page(
    gsub("68%", "168%", headline, fixed = TRUE),
    april_july_row_for("SHDC1"),
    retrieved_at,
    tabular_html = gsub("68 %", "168 %", tabular, fixed = TRUE)
  )
  missing_percent <- cnrfc_parse_page(
    gsub("68%", "N/A%", headline, fixed = TRUE),
    april_july_row_for("SHDC1"),
    retrieved_at,
    tabular_html = gsub("68 %", "N/A %", tabular, fixed = TRUE)
  )
  missing_volume_headline <- gsub("1160", "N/A", headline, fixed = TRUE)
  missing_volume_headline <- gsub("68%", "N/A%", missing_volume_headline, fixed = TRUE)
  missing_volume_tabular <- gsub("1160", "N/A", tabular, fixed = TRUE)
  missing_volume_tabular <- gsub("68 %", "N/A %", missing_volume_tabular, fixed = TRUE)
  missing_volume <- cnrfc_parse_page(
    missing_volume_headline,
    april_july_row_for("SHDC1"),
    retrieved_at,
    tabular_html = missing_volume_tabular
  )
  assert_equal(zero$forecast_volume, 0, "Valid product-7 zero changed or became null.")
  assert_equal(high$percent_average, 168, "Product-7 Percent of Average above 100 was rejected.")
  assert_true(is.null(missing_percent$percent_average), "Missing Percent of Average became zero.")
  assert_equal(missing_percent$status, "current_partial", "Missing Percent of Average did not remain metric-level partial.")
  assert_true(is.null(missing_volume$forecast_volume), "Missing product-7 forecast volume became zero.")
  assert_equal(missing_volume$normal_average_volume, 1700, "Direct normal average was discarded with a missing forecast.")
})

scenario("product-7 ambiguity, unit drift, and headline/table conflict fail closed", {
  headline <- fixture("prod7_normal_major_basin_headline.html")
  tabular <- fixture("prod7_normal_major_basin_tabular.html")
  duplicate_script <- sub("</body>", "<script>Median Forecast: 1160 kaf | 68% of Mean | 83% of Median</script></body>", headline, fixed = TRUE)
  duplicate_pre <- sub("</body>", paste0(
    "<pre>", sub("(?s).*?<pre>(.*?)</pre>.*", "\\1", tabular, perl = TRUE), "</pre></body>"
  ), tabular, fixed = TRUE)
  assert_error(
    cnrfc_parse_page(duplicate_script, april_july_row_for("SHDC1"), retrieved_at, tabular_html = tabular),
    "exactly one product-7 Median Forecast", "Duplicate product-7 headline was accepted."
  )
  assert_error(
    cnrfc_parse_page(headline, april_july_row_for("SHDC1"), retrieved_at, tabular_html = duplicate_pre),
    "exactly one product-7 candidate tabular block", "Duplicate product-7 table was accepted."
  )
  assert_error(
    cnrfc_parse_page(
      gsub("kaf", "cfs", headline, fixed = TRUE),
      april_july_row_for("SHDC1"),
      retrieved_at,
      tabular_html = gsub("kaf", "cfs", tabular, fixed = TRUE)
    ),
    "Unrecognized CNRFC April-July volume units", "Product-7 unit drift was guessed.",
    "cnrfc_validation_error"
  )
  assert_error(
    cnrfc_parse_page(
      headline,
      april_july_row_for("SHDC1"),
      retrieved_at,
      tabular_html = gsub("1160", "1150", tabular, fixed = TRUE)
    ),
    "materially conflicted", "Product-7 headline/table mismatch was silently resolved.",
    "cnrfc_validation_error"
  )
  assert_error(
    cnrfc_parse_page(
      fixture("full_http_200_maintenance.html"),
      april_july_row_for("SHDC1"),
      retrieved_at,
      tabular_html = tabular
    ),
    "did not match expected NWS LID", "HTTP 200 maintenance page was accepted as product 7."
  )
})

scenario("mean and median remain distinct and percentages above 100 are accepted", {
  record <- cnrfc_parse_page(fixture("percent_over_100.html"), row_for("FOLC1"), retrieved_at)
  assert_equal(record$percent_mean, 87, "Mean and median were swapped.")
  assert_equal(record$percent_median, 105, "Percent above 100 was rejected.")
})

scenario("source-confirmed missing product-9 values preserve null and never become zero", {
  sentinel <- cnrfc_parse_page(fixture("missing_sentinel.html"), row_for("SHDC1"), retrieved_at)
  unavailable <- cnrfc_parse_page(fixture("unavailable_out_of_season.html"), row_for("SACC0"), retrieved_at)
  assert_true(is.null(sentinel$forecast_volume), "-9999 became a numerical forecast.")
  assert_true(is.null(sentinel$percent_mean), "N/A became zero.")
  assert_equal(sentinel$status, "unavailable", "Source-confirmed missing state changed.")
  assert_equal(sentinel$attempt_outcome, "source_unavailable", "Unavailable attempt taxonomy changed.")
  assert_equal(unavailable$status, "unavailable", "Out-of-season source is not unavailable.")
})

scenario("malformed, ambiguous, wrong-LID, and maintenance pages fail as parse errors", {
  assert_error(
    cnrfc_parse_page(fixture("malformed_changed_page.html"), row_for("SHDC1"), retrieved_at),
    "exactly one labeled Median Forecast", "Changed source page did not fail."
  )
  assert_error(
    cnrfc_parse_page(fixture("ambiguous_duplicate.html"), row_for("SHDC1"), retrieved_at),
    "exactly one labeled Median Forecast", "Duplicate source summary did not fail."
  )
  assert_error(
    cnrfc_parse_page(fixture("normal_major_basin.html"), row_for("FOLC1"), retrieved_at),
    "did not match expected NWS LID", "Wrong source LID was accepted."
  )
  assert_error(
    cnrfc_parse_page(fixture("full_http_200_maintenance.html"), row_for("SHDC1"), retrieved_at),
    "did not match expected NWS LID", "HTTP 200 maintenance page was accepted."
  )
})

scenario("full product-2 page extracts exact median/deterministic values, dates, and units", {
  record <- cnrfc_parse_page(fixture("full_prod2_normal_shdc1.html"), accum_row_for("SHDC1"), retrieved_at)
  expected_values <- c(17.4, 28.8, 57.3, 16.9, 28.3)
  actual_values <- unlist(record[c(
    "day_3_median_volume", "day_5_median_volume", "day_10_median_volume",
    "day_3_deterministic_volume", "day_5_deterministic_volume"
  )], use.names = FALSE)
  assert_equal(actual_values, expected_values, "Selected source values changed.")
  assert_equal(record$day_3_median_valid_date, "2026-08-03", "Day 3 date changed.")
  assert_equal(record$day_5_median_valid_date, "2026-08-05", "Day 5 date changed.")
  assert_equal(record$day_10_median_valid_date, "2026-08-10", "Day 10 date changed.")
  assert_equal(record$day_3_deterministic_valid_date, "2026-08-03", "Deterministic Day 3 date changed.")
  assert_equal(record$day_5_deterministic_valid_date, "2026-08-05", "Deterministic Day 5 date changed.")
  assert_equal(record$source_units, "1000s of Acre-Feet", "Source units were not preserved.")
  assert_equal(record$normalized_units, "kaf", "Units were not normalized to kaf.")
  assert_true(!"day_10_deterministic_volume" %in% names(record), "Deterministic Day 10 was inferred.")
  fingerprint <- record$diagnostic$table_fingerprint
  assert_equal(fingerprint$candidate_table_count, 1L, "Candidate-table diagnostic changed.")
  assert_equal(fingerprint$inferred_date_span$end, "2026-08-10", "Date-span diagnostic changed.")
})

scenario("product-2 zero is current and all supported horizons preserve zero", {
  record <- cnrfc_parse_page(fixture("prod2_zero.html"), accum_row_for("SHDC1"), retrieved_at)
  fields <- cnrfc_metric_fields(record$product_type)
  assert_true(all(vapply(record[fields], function(value) identical(value, 0), logical(1))), "A zero changed or became null.")
  assert_true(all(vapply(record$metric_state, function(state) state$map_eligible, logical(1))), "Current zero was not map eligible.")
  assert_equal(record$source_units, "1000s Ac-Ft", "Reviewed unit variant changed.")
})

scenario("missing deterministic row is current_partial with exact metric eligibility", {
  record <- cnrfc_parse_page(fixture("prod2_missing_deterministic.html"), accum_row_for("SHDC1"), retrieved_at)
  assert_equal(record$status, "current_partial", "Missing deterministic row did not produce current_partial.")
  assert_true(record$metric_state$day_3_median_volume$map_eligible, "Current median is not map eligible.")
  assert_equal(record$metric_state$day_3_deterministic_volume$status, "unavailable", "Missing deterministic metric state changed.")
  assert_true(!record$metric_state$day_3_deterministic_volume$map_eligible, "Missing deterministic metric is map eligible.")
  assert_true(!record$metric_state$day_3_deterministic_volume$popup_eligible, "Null deterministic metric is popup eligible.")
  assert_true(grepl("deterministic_row_missing", record$missing_reason, fixed = TRUE), "Precise deterministic diagnostic missing.")
})

scenario("explicit product-2 unavailability is a source-stage condition", {
  error <- assert_error(
    cnrfc_parse_page(fixture("full_prod2_unavailable_mhbc1.html"), accum_row_for("MHBC1"), retrieved_at),
    "explicitly reports product 2 unavailable", "Source-side unavailability was not diagnosed.",
    "cnrfc_source_unavailable"
  )
  assert_true(inherits(error, "cnrfc_source_unavailable"), "Wrong unavailable condition class.")
})

scenario("product-2 row/table ambiguity and cardinality fail closed", {
  normal <- fixture("prod2_normal_shdc1.html")
  table_markup <- regmatches(
    normal,
    regexpr("(?s)<table class=\"standardTable\">.*?</table>", normal, perl = TRUE)
  )
  second_table <- sub("</body>", paste0(table_markup, "</body>"), normal, fixed = TRUE)
  assert_error(
    cnrfc_parse_page(second_table, accum_row_for("SHDC1"), retrieved_at),
    "exactly one usable 10-day accumulated-volume table", "Multiple candidate tables were accepted."
  )
  assert_error(
    cnrfc_parse_page(fixture("prod2_duplicate_rows.html"), accum_row_for("SHDC1"), retrieved_at),
    "exactly one semantic 50% (Median) row", "Duplicate median rows were accepted."
  )
  duplicate <- fixture("prod2_duplicate_rows.html")
  first_median <- paste0(
    "  <tr><td>50% (Median)</td><td>5.8</td><td>11.6</td><td>17.4</td><td>23.1</td>",
    "<td>28.8</td><td>34.6</td><td>40.3</td><td>46.0</td><td>51.6</td><td>57.3</td></tr>\n"
  )
  assert_error(
    cnrfc_parse_page(sub(first_median, "", duplicate, fixed = TRUE), accum_row_for("SHDC1"), retrieved_at),
    "at most one semantic CNRFC Deterministic Forecast row", "Duplicate deterministic rows were accepted."
  )
})

scenario("reordered semantic rows are accepted but extra/nine/eleven columns are rejected", {
  normal <- fixture("prod2_normal_shdc1.html")
  median_row <- regmatches(normal, regexpr("<tr><td><b>50%<br>\\(Median\\)</b></td>.*?</tr>", normal, perl = TRUE))
  det_row <- regmatches(normal, regexpr("<tr><td><b>CNRFC<br>Deterministic Forecast</b></td>.*?</tr>", normal, perl = TRUE))
  reordered <- sub(median_row, "__MEDIAN__", normal, fixed = TRUE)
  reordered <- sub(det_row, median_row, reordered, fixed = TRUE)
  reordered <- sub("__MEDIAN__", det_row, reordered, fixed = TRUE)
  record <- cnrfc_parse_page(reordered, accum_row_for("SHDC1"), retrieved_at)
  assert_equal(record$day_3_median_volume, 17.4, "Reordered semantic median was confused.")
  fewer <- sub("<td><b>Aug<br>10</b></td>", "", normal, fixed = TRUE)
  more <- sub("<td><b>Aug<br>10</b></td></tr>", "<td><b>Aug<br>10</b></td><td>Notes</td></tr>", normal, fixed = TRUE)
  assert_error(cnrfc_parse_page(fewer, accum_row_for("SHDC1"), retrieved_at), "found 9", "Nine columns were accepted.")
  assert_error(cnrfc_parse_page(more, accum_row_for("SHDC1"), retrieved_at), "found 11", "Extra nonforecast column was accepted.")
})

scenario("unit ambiguity, malformed selected cells, and material decreases are validation failures", {
  normal <- fixture("prod2_normal_shdc1.html")
  assert_error(
    cnrfc_parse_page(sub("1000s of Acre-Feet", "kaf or cfs", normal, fixed = TRUE), accum_row_for("SHDC1"), retrieved_at),
    "Unrecognized CNRFC accumulated-volume units", "Ambiguous units were guessed.", "cnrfc_validation_error"
  )
  assert_error(
    cnrfc_parse_page(fixture("prod2_malformed_selected.html"), accum_row_for("SHDC1"), retrieved_at),
    "Malformed median Day 3", "Malformed cell was accepted.", "cnrfc_validation_error"
  )
  assert_error(
    cnrfc_parse_page(sub("<td>28.3</td>", "<td>16.0</td>", normal, fixed = TRUE), accum_row_for("SHDC1"), retrieved_at),
    "Deterministic cumulative series materially decreased", "Material decrease was accepted.", "cnrfc_validation_error"
  )
  tiny <- sub("<td>23.1</td>", "<td>17.35</td>", normal, fixed = TRUE)
  tiny <- sub("<td>28.8</td>", "<td>17.36</td>", tiny, fixed = TRUE)
  tiny_record <- cnrfc_parse_page(tiny, accum_row_for("SHDC1"), retrieved_at)
  assert_equal(tiny_record$day_5_median_volume, 17.4, "Tiny source rounding decrease was not normalized.")
})

prod2_with_dates <- function(updated, labels, retrieved) {
  html <- fixture("prod2_normal_shdc1.html")
  html <- sub("Jul 31 2026 at 7:20 AM PDT", updated, html, fixed = TRUE)
  header <- paste0(
    "<tr><td><b>Probability</b></td>",
    paste0("<td><b>", substr(labels, 1L, 3L), "<br>", substr(labels, 4L, 5L), "</b></td>", collapse = ""),
    "</tr>"
  )
  html <- sub("<tr><td><b>Probability</b></td>.*?</tr>", header, html, perl = TRUE)
  cnrfc_parse_page(html, accum_row_for("SHDC1"), retrieved)
}

scenario("source-anchored dates handle both year-end sequences and leap February", {
  dec26 <- prod2_with_dates(
    "Dec 26 2026 at 7:20 AM PST",
    c("Dec26", "Dec27", "Dec28", "Dec29", "Dec30", "Dec31", "Jan01", "Jan02", "Jan03", "Jan04"),
    "2026-12-26T18:00:00Z"
  )
  dec31 <- prod2_with_dates(
    "Dec 31 2026 at 7:20 AM PST",
    c("Dec31", "Jan01", "Jan02", "Jan03", "Jan04", "Jan05", "Jan06", "Jan07", "Jan08", "Jan09"),
    "2026-12-31T18:00:00Z"
  )
  leap <- prod2_with_dates(
    "Feb 26 2028 at 7:20 AM PST",
    c("Feb27", "Feb28", "Feb29", "Mar01", "Mar02", "Mar03", "Mar04", "Mar05", "Mar06", "Mar07"),
    "2028-02-26T18:00:00Z"
  )
  assert_equal(dec26$day_10_median_valid_date, "2027-01-04", "Dec 26 rollover changed.")
  assert_equal(dec31$day_10_median_valid_date, "2027-01-09", "Dec 31 rollover changed.")
  assert_equal(leap$day_3_median_valid_date, "2028-02-29", "Leap day was rejected.")
})

scenario("retrieval date never anchors product-2 headings and ambiguous rollover is rejected", {
  after_midnight <- cnrfc_parse_page(
    fixture("prod2_normal_shdc1.html"), accum_row_for("SHDC1"), "2026-08-01T08:10:00Z"
  )
  assert_equal(after_midnight$day_3_median_valid_date, "2026-08-03", "Retrieval date shifted the horizon.")
  stale <- cnrfc_parse_page(
    fixture("prod2_normal_shdc1.html"), accum_row_for("SHDC1"), "2026-08-02T22:00:00Z"
  )
  assert_equal(stale$metric_state$day_10_median_volume$status, "source_stale", "Old source table was called current.")
  assert_error(
    prod2_with_dates(
      "Dec 30 2026 at 7:20 AM PST",
      c("Dec30", "Jan01", "Dec31", "Jan02", "Jan03", "Jan04", "Jan05", "Jan06", "Jan07", "Jan08"),
      "2026-12-30T18:00:00Z"
    ),
    "more than one calendar-year rollover", "Ambiguous rollover was accepted.", "cnrfc_validation_error"
  )
})

scenario("product parsers cannot be confused", {
  assert_error(
    cnrfc_parse_page(fixture("prod2_normal_shdc1.html"), row_for("SHDC1"), retrieved_at),
    "Water Year Trend Plot", "Product 2 was accepted as product 9."
  )
  assert_error(
    cnrfc_parse_page(fixture("normal_major_basin.html"), accum_row_for("SHDC1"), retrieved_at),
    "product-2 10-Day Accumulated Volume Plot", "Product 9 was accepted as product 2."
  )
  assert_error(
    cnrfc_parse_page(
      fixture("prod7_normal_major_basin_headline.html"), row_for("SHDC1"), retrieved_at
    ),
    "Water Year Trend Plot", "Product 7 was accepted as product 9."
  )
  assert_error(
    cnrfc_parse_page(
      fixture("normal_major_basin.html"),
      april_july_row_for("SHDC1"),
      retrieved_at,
      tabular_html = fixture("prod7_normal_major_basin_tabular.html")
    ),
    "product-7 headline and tabular identity label", "Product 9 was accepted as product 7."
  )
})

scenario("fetch retries are bounded and nonretryable HTTP failures stop", {
  calls <- 0L
  delays <- numeric()
  fetched <- cnrfc_fetch_page(
    row_for("SHDC1")$source_url[[1L]],
    fetch_once = function(url, timeout_sec, connect_timeout_sec, user_agent) {
      calls <<- calls + 1L
      if (calls == 1L) stop("temporary transport failure")
      list(status_code = 200L, type = "text/html", headers = raw(), content = charToRaw(fixture("normal_major_basin.html")))
    },
    sleep_fun = function(value) delays <<- c(delays, value)
  )
  assert_true(fetched$success, "Transient retry did not recover.")
  assert_equal(delays, 1, "Retry backoff changed.")
  failed <- cnrfc_fetch_page(
    row_for("SHDC1")$source_url[[1L]],
    fetch_once = function(...) list(status_code = 404L, type = "text/html", headers = raw(), content = charToRaw("not found")),
    sleep_fun = function(value) stop("404 unexpectedly retried")
  )
  assert_true(!failed$success, "HTTP 404 was accepted.")
})

synthetic_raw_record <- function(row,
                                 value,
                                 retrieved = retrieved_at,
                                 source_time = NULL,
                                 deterministic = TRUE) {
  is_accumulation <- identical(row$product_type[[1L]], "ten_day_streamflow_volume_accumulation")
  if (is_accumulation) {
    record <- list(
      forecast_key = row$forecast_key[[1L]], rfc = row$rfc[[1L]], nws_lid = row$nws_lid[[1L]],
      product_type = row$product_type[[1L]], display_name = row$display_name[[1L]],
      day_3_median_volume = round(as.numeric(value), 1), day_3_median_valid_date = "2026-08-03",
      day_5_median_volume = round(as.numeric(value + 1), 1), day_5_median_valid_date = "2026-08-05",
      day_10_median_volume = round(as.numeric(value + 2), 1), day_10_median_valid_date = "2026-08-10",
      day_3_deterministic_volume = if (deterministic) round(as.numeric(value), 1) else NULL,
      day_3_deterministic_valid_date = if (deterministic) "2026-08-03" else NULL,
      day_5_deterministic_volume = if (deterministic) round(as.numeric(value + 1), 1) else NULL,
      day_5_deterministic_valid_date = if (deterministic) "2026-08-05" else NULL,
      normalized_units = "kaf", source_units = "1000s of Acre-Feet",
      source_data_updated_at = if (is.null(source_time)) "2026-07-31T07:20:00-07:00" else source_time,
      forecast_issued_at = "2026-07-31T07:14:00-07:00",
      status = "current", missing_reason = if (deterministic) NULL else "deterministic_row_missing",
      source_url = row$source_url[[1L]], source_page_signature = paste0("signature-", row$nws_lid[[1L]], "-prod2"),
      diagnostic = list(
        parser = "cnrfc_prod2_semantic_v1", source_page_title = paste0("CNRFC - Ensemble Products - ", row$nws_lid[[1L]]),
        source_product_label = "10-Day Accumulated Volume Plot", expected_product_label = row$expected_product_label[[1L]],
        deterministic_status = if (deterministic) "available" else "deterministic_row_missing",
        error_class = NULL, error_message = NULL
      )
    )
  } else if (identical(row$product_type[[1L]], "april_july_streamflow_volume_forecast")) {
    record <- list(
      forecast_key = row$forecast_key[[1L]], rfc = row$rfc[[1L]], nws_lid = row$nws_lid[[1L]],
      product_type = row$product_type[[1L]], display_name = row$display_name[[1L]],
      forecast_statistic = "50_percent_exceedance", forecast_volume = as.numeric(value),
      normalized_units = "kaf", source_units = "kaf",
      percent_average = as.numeric(60 + value %% 10),
      normal_average_volume = as.numeric(value + 1000),
      water_year = 2026L, forecast_period = "April-July",
      forecast_issued_at = if (is.null(source_time)) "2026-07-31T07:00:00-07:00" else source_time,
      status = "current", missing_reason = NULL,
      source_url = row$source_url[[1L]], source_page_signature = paste0("signature-", row$nws_lid[[1L]], "-prod7"),
      diagnostic = list(
        parser = "cnrfc_prod7_semantic_v1", source_page_title = paste0("CNRFC - Ensemble Products - ", row$nws_lid[[1L]]),
        source_product_label = "2026 Seasonal Trend Plot (Year View)", expected_product_label = row$expected_product_label[[1L]],
        headline_statistic_label = "Median Forecast", tabular_statistic_label = "50% Exceed",
        error_class = NULL, error_message = NULL
      )
    )
  } else {
    record <- list(
      forecast_key = row$forecast_key[[1L]], rfc = row$rfc[[1L]], nws_lid = row$nws_lid[[1L]],
      product_type = row$product_type[[1L]], display_name = row$display_name[[1L]],
      forecast_statistic = "median", forecast_volume = as.numeric(value), forecast_volume_units = "kaf",
      percent_mean = as.numeric(80 + value %% 10), percent_median = as.numeric(90 + value %% 10),
      water_year = 2026L, forecast_period = "2026 Water Year",
      forecast_issued_at = if (is.null(source_time)) "2026-07-31T07:00:00-07:00" else source_time,
      status = "current", missing_reason = NULL,
      source_url = row$source_url[[1L]], source_page_signature = paste0("signature-", row$nws_lid[[1L]]),
      diagnostic = list(
        parser = "cnrfc_prod9_semantic_v1", source_page_title = paste0("CNRFC - Ensemble Products - ", row$nws_lid[[1L]]),
        source_product_label = "2026 Water Year Trend Plot", expected_product_label = row$expected_product_label[[1L]],
        error_class = NULL, error_message = NULL
      )
    )
  }
  record
}

synthetic_record <- function(row, value, retrieved = retrieved_at, source_time = NULL, deterministic = TRUE) {
  cnrfc_finalize_current_record(
    synthetic_raw_record(row, value, retrieved, source_time, deterministic),
    retrieved,
    CNRFC_WATER_YEAR_STALE_AFTER_HOURS,
    CNRFC_WATER_YEAR_EXPIRES_AFTER_HOURS,
    CNRFC_ACCUMULATION_STALE_AFTER_HOURS,
    CNRFC_APRIL_JULY_STALE_AFTER_HOURS
  )
}

successful_attempts <- function(retrieved = retrieved_at, deterministic = TRUE) {
  attempts <- lapply(seq_len(nrow(roster)), function(index) {
    row <- roster[index, , drop = FALSE]
    if (identical(row$forecast_key[[1L]], "CNRFC:MHBC1:10D_VOLUME_ACCUM")) {
      return(cnrfc_failure_result(
        "source_unavailable", "CNRFC explicitly reports product 2 unavailable for this location.", retrieved
      ))
    }
    cnrfc_success_result(synthetic_record(row, index, retrieved, deterministic = deterministic))
  })
  stats::setNames(attempts, roster$forecast_key)
}

prior_payload <- cnrfc_build_payload(
  roster, successful_attempts(), generated_at = "2026-07-31T18:01:00Z"
)

scenario("bootstrap accepts exact established roster and reports independent family health", {
  assert_equal(prior_payload$publication_mode, "bootstrap", "Initial publication mode changed.")
  assert_equal(prior_payload$expected_record_count, 51L, "Expected count changed.")
  assert_equal(prior_payload$actual_record_count, 51L, "Actual count changed.")
  assert_equal(prior_payload$roster_version, CNRFC_ROSTER_VERSION, "Roster version changed.")
  assert_equal(prior_payload$family_health$water_year_fnf$health, "healthy", "FNF family health changed.")
  assert_equal(prior_payload$family_health$water_year_index$health, "healthy", "Index family health changed.")
  assert_equal(prior_payload$family_health$ten_day_streamflow_volume_accumulation$health, "healthy", "Product-2 health changed.")
  assert_equal(prior_payload$family_health$april_july_streamflow_volume_forecast$health, "healthy", "Product-7 health changed.")
  assert_equal(prior_payload$source_summary$water_year_fnf_success_count, 15L, "FNF success count changed.")
  assert_equal(prior_payload$source_summary$water_year_index_success_count, 3L, "Index success count changed.")
  assert_equal(prior_payload$source_summary$april_july_success_count, 18L, "Product-7 success count changed.")
  assert_equal(prior_payload$source_summary$accumulation_median_success_count, 14L, "Median coverage changed.")
  assert_equal(prior_payload$source_summary$accumulation_deterministic_success_count, 14L, "Deterministic coverage changed.")
})

scenario("bootstrap rejects missing required families or missing MHBC source confirmation", {
  bootstrap_generated_at <- "2026-07-31T19:00:00Z"
  fnf_failed <- successful_attempts()
  fnf_failed[["CNRFC:SCSC1:WY_FNF"]] <- cnrfc_failure_result("parse_failed", "changed page", retrieved_at)
  assert_error(
    cnrfc_build_payload(roster, fnf_failed, generated_at = bootstrap_generated_at),
    "14/15 water_year_fnf",
    "Incomplete FNF bootstrap was accepted."
  )
  median_failed <- successful_attempts()
  median_failed[["CNRFC:SCSC1:10D_VOLUME_ACCUM"]] <- cnrfc_failure_result("validation_failed", "bad median", retrieved_at)
  assert_error(
    cnrfc_build_payload(roster, median_failed, generated_at = bootstrap_generated_at),
    "13/14 expected-available accumulation",
    "Incomplete median bootstrap was accepted."
  )
  product7_failed <- successful_attempts()
  product7_failed[["CNRFC:SCSC1:APR_JUL_VOLUME"]] <- cnrfc_failure_result("parse_failed", "changed page", retrieved_at)
  assert_error(
    cnrfc_build_payload(roster, product7_failed, generated_at = bootstrap_generated_at),
    "17/18 April-July",
    "Incomplete product-7 bootstrap was accepted."
  )
  mhbc_current <- successful_attempts()
  mhbc_row <- accum_row_for("MHBC1")
  mhbc_current[["CNRFC:MHBC1:10D_VOLUME_ACCUM"]] <- cnrfc_success_result(synthetic_record(mhbc_row, 20))
  assert_error(
    cnrfc_build_payload(roster, mhbc_current, generated_at = bootstrap_generated_at),
    "MHBC1 source-unavailable=false",
    "Changed MHBC availability bypassed review."
  )
})

scenario("deterministic coverage is reported but does not gate bootstrap", {
  attempts <- successful_attempts()
  row <- accum_row_for("SHDC1")
  attempts[[row$forecast_key[[1L]]]] <- cnrfc_success_result(synthetic_record(row, 21, deterministic = FALSE))
  payload <- cnrfc_build_payload(roster, attempts, generated_at = "2026-07-31T18:02:00Z")
  assert_equal(payload$source_summary$accumulation_median_success_count, 14L, "Median coverage changed.")
  assert_equal(payload$source_summary$accumulation_deterministic_success_count, 13L, "Deterministic coverage was not separate.")
  assert_equal(record_for(payload, row$forecast_key[[1L]])$status, "current_partial", "Partial deterministic record changed.")
})

scenario("failure taxonomy separates attempt outcome, failure stage, and display state", {
  cases <- list(
    source_unavailable = "source", fetch_failed = "fetch", parse_failed = "parse", validation_failed = "validate"
  )
  for (outcome in names(cases)) {
    failed <- cnrfc_failed_or_retained_record(
      row_for("SHDC1"), cnrfc_failure_result(outcome, "bounded diagnostic", retrieved_at)
    )
    assert_equal(failed$attempt_outcome, outcome, "Attempt outcome changed.")
    assert_equal(failed$failure_stage, cases[[outcome]], "Failure stage changed.")
    expected <- if (outcome == "source_unavailable") "unavailable" else "failed_no_data"
    assert_equal(failed$status, expected, "No-prior display status changed.")
  }
})

scenario("steady state accepts independent family outage and retains only that family", {
  attempts <- successful_attempts("2026-08-01T18:00:00Z")
  accumulation_keys <- roster$forecast_key[roster$product_type == "ten_day_streamflow_volume_accumulation"]
  for (key in accumulation_keys[accumulation_keys != "CNRFC:MHBC1:10D_VOLUME_ACCUM"]) {
    attempts[[key]] <- cnrfc_failure_result("fetch_failed", "timeout", "2026-08-01T18:00:00Z")
  }
  payload <- cnrfc_build_payload(roster, attempts, prior_payload, "2026-08-01T18:01:00Z")
  assert_equal(payload$publication_mode, "steady_state", "Steady-state mode changed.")
  assert_equal(payload$family_health$ten_day_streamflow_volume_accumulation$health, "outage_using_last_known_good", "Product-2 outage health changed.")
  assert_equal(payload$family_health$water_year_fnf$health, "healthy", "Product-2 outage degraded FNF.")
  assert_equal(record_for(payload, "CNRFC:SHDC1:10D_VOLUME_ACCUM")$status, "stale_last_known_good", "Product-2 LKG was not retained.")
  assert_equal(record_for(payload, "CNRFC:SHDC1:WY_FNF")$status, "current", "Product-9 record did not advance.")
})

scenario("FNF, index, and April-July families can each fail while other families advance", {
  for (family_name in c(
    "water_year_fnf", "water_year_index", "april_july_streamflow_volume_forecast"
  )) {
    attempts <- successful_attempts("2026-08-01T18:00:00Z")
    keys <- roster$forecast_key[roster$product_type == family_name]
    for (key in keys) {
      attempts[[key]] <- cnrfc_failure_result(
        "fetch_failed", paste(family_name, "outage"), "2026-08-01T18:00:00Z"
      )
    }
    payload <- cnrfc_build_payload(
      roster, attempts, prior_payload, "2026-08-01T18:02:00Z"
    )
    assert_equal(
      payload$family_health[[family_name]]$health,
      "outage_using_last_known_good",
      paste(family_name, "outage health changed.")
    )
    other_names <- setdiff(
      c(
        "water_year_fnf", "water_year_index", "ten_day_streamflow_volume_accumulation",
        "april_july_streamflow_volume_forecast"
      ),
      family_name
    )
    assert_true(all(vapply(other_names, function(name) {
      payload$family_health[[name]]$health == "healthy"
    }, logical(1))), paste(family_name, "outage degraded another family."))
  }
})

scenario("complete outage with prior produces a degraded 51-record snapshot", {
  attempts <- stats::setNames(lapply(roster$forecast_key, function(key) {
    cnrfc_failure_result("fetch_failed", "complete source outage", "2026-08-01T19:00:00Z")
  }), roster$forecast_key)
  payload <- cnrfc_build_payload(roster, attempts, prior_payload, "2026-08-01T19:01:00Z")
  assert_equal(length(payload$records), 51L, "Complete outage lost structural records.")
  assert_true(all(vapply(payload$family_health, function(family) {
    family$health == "outage_using_last_known_good"
  }, logical(1))), "A complete outage was not reported independently for every family.")
  assert_equal(record_for(payload, "CNRFC:MHBC1:10D_VOLUME_ACCUM")$status, "failed_no_data", "No-data fetch failure was called unavailable.")
})

scenario("expired product-2 LKG remains popup provenance and is never map eligible", {
  attempts <- successful_attempts("2026-08-12T18:00:00Z")
  key <- "CNRFC:SHDC1:10D_VOLUME_ACCUM"
  attempts[[key]] <- cnrfc_failure_result("parse_failed", "table changed", "2026-08-12T18:00:00Z")
  payload <- cnrfc_build_payload(roster, attempts, prior_payload, "2026-08-12T18:01:00Z")
  record <- record_for(payload, key)
  assert_equal(record$status, "expired", "Expired LKG record summary changed.")
  assert_equal(record$metric_state$day_3_median_volume$status, "expired", "Day 3 did not expire by its valid date.")
  assert_equal(record$metric_state$day_3_median_volume$value_origin, "last_known_good", "Expired origin changed.")
  assert_true(!record$metric_state$day_10_median_volume$map_eligible, "Expired Day 10 is map eligible.")
  assert_true(record$metric_state$day_10_median_volume$popup_eligible, "Expired Day 10 provenance is not popup eligible.")
})

scenario("backward source time is validation_failed and retains newer prior data", {
  attempts <- successful_attempts("2026-08-01T18:00:00Z")
  key <- "CNRFC:SHDC1:WY_FNF"
  row <- row_for("SHDC1")
  attempts[[key]] <- cnrfc_success_result(synthetic_record(
    row, 999, "2026-08-01T18:00:00Z", "2026-07-30T07:00:00-07:00"
  ))
  payload <- cnrfc_build_payload(roster, attempts, prior_payload, "2026-08-01T18:01:00Z")
  record <- record_for(payload, key)
  assert_equal(record$attempt_outcome, "validation_failed", "Backward source time taxonomy changed.")
  assert_equal(record$failure_stage, "validate", "Backward source time stage changed.")
  assert_equal(record$forecast_volume, record_for(prior_payload, key)$forecast_volume, "Older candidate replaced prior value.")
})

scenario("backward product-7 issue time is validation_failed and retains newer seasonal data", {
  attempts <- successful_attempts("2026-08-01T18:00:00Z")
  key <- "CNRFC:SHDC1:APR_JUL_VOLUME"
  row <- april_july_row_for("SHDC1")
  attempts[[key]] <- cnrfc_success_result(synthetic_record(
    row, 999, "2026-08-01T18:00:00Z", "2026-07-30T07:00:00-07:00"
  ))
  payload <- cnrfc_build_payload(roster, attempts, prior_payload, "2026-08-01T18:01:00Z")
  record <- record_for(payload, key)
  assert_equal(record$attempt_outcome, "validation_failed", "Backward product-7 source time taxonomy changed.")
  assert_equal(record$failure_stage, "validate", "Backward product-7 source time stage changed.")
  assert_equal(
    record$forecast_volume,
    record_for(prior_payload, key)$forecast_volume,
    "Older product-7 candidate replaced prior value."
  )
})

scenario("MHBC availability change emits a reviewed operational notice in steady state", {
  attempts <- successful_attempts("2026-08-01T18:00:00Z")
  row <- accum_row_for("MHBC1")
  attempts[[row$forecast_key[[1L]]]] <- cnrfc_success_result(synthetic_record(row, 20, "2026-08-01T18:00:00Z"))
  payload <- cnrfc_build_payload(roster, attempts, prior_payload, "2026-08-01T18:01:00Z")
  assert_equal(length(payload$operational_notices), 1L, "Availability-change notice missing.")
  assert_equal(payload$operational_notices[[1L]]$code, "product_2_expected_availability_changed", "Notice code changed.")
})

scenario("semantic no-op preserves successful and repeated-failure attempt timestamps", {
  steady <- cnrfc_build_payload(roster, successful_attempts(), prior_payload, "2026-07-31T18:05:00Z")
  rechecked <- cnrfc_build_payload(
    roster, successful_attempts("2026-07-31T19:00:00Z"), steady, "2026-07-31T19:01:00Z"
  )
  assert_true(!cnrfc_payload_changed(rechecked, steady), "Successful unchanged recheck was substantive.")
  assert_equal(rechecked$records[[1L]]$last_attempt_at, steady$records[[1L]]$last_attempt_at, "No-op changed last_attempt_at.")
  failed_once_attempts <- successful_attempts("2026-08-01T18:00:00Z")
  failed_once_attempts[[roster$forecast_key[[1L]]]] <- cnrfc_failure_result("fetch_failed", "timeout", "2026-08-01T18:00:00Z")
  failed_once <- cnrfc_build_payload(roster, failed_once_attempts, steady, "2026-08-01T18:01:00Z")
  failed_twice_attempts <- successful_attempts("2026-08-01T19:00:00Z")
  failed_twice_attempts[[roster$forecast_key[[1L]]]] <- cnrfc_failure_result("fetch_failed", "timeout", "2026-08-01T19:00:00Z")
  failed_twice <- cnrfc_build_payload(roster, failed_twice_attempts, failed_once, "2026-08-01T19:01:00Z")
  assert_true(!cnrfc_payload_changed(failed_twice, failed_once), "Repeated identical outage forced a commit.")
})

scenario("new source issue, changed value, outcome, and expiry transitions are substantive", {
  steady <- cnrfc_build_payload(roster, successful_attempts(), prior_payload, "2026-07-31T18:05:00Z")
  changed_attempts <- successful_attempts("2026-07-31T19:00:00Z")
  row <- row_for("SHDC1")
  changed_attempts[[row$forecast_key[[1L]]]] <- cnrfc_success_result(synthetic_record(
    row, 3, "2026-07-31T19:00:00Z", "2026-07-31T08:00:00-07:00"
  ))
  changed <- cnrfc_build_payload(roster, changed_attempts, steady, "2026-07-31T19:01:00Z")
  assert_true(cnrfc_payload_changed(changed, steady), "New issue/value was treated as no-op.")
})

scenario("payload validation is exact, deterministic, geometry-free, and null-safe", {
  reversed <- rev(successful_attempts())
  payload <- cnrfc_build_payload(roster, reversed, generated_at = "2026-07-31T18:10:00Z")
  keys <- vapply(payload$records, `[[`, character(1), "forecast_key")
  assert_equal(keys, roster$forecast_key, "Payload order depends on attempt order.")
  cnrfc_validate_payload(payload, roster)
  all_fields <- unique(unlist(lapply(payload$records, names), use.names = FALSE))
  assert_true(!any(grepl("geometry|coordinates|latitude|longitude|flow_rate|ensemble_member", all_fields, ignore.case = TRUE)), "Forbidden fields exist.")
  test_dir <- tempfile("cnrfc-roundtrip-")
  dir.create(test_dir)
  on.exit(unlink(test_dir, recursive = TRUE, force = TRUE), add = TRUE)
  output <- file.path(test_dir, "payload.json")
  cnrfc_write_json(payload, output)
  roundtrip <- cnrfc_read_payload(output, roster)
  assert_true(is.null(record_for(roundtrip, "CNRFC:MHBC1:10D_VOLUME_ACCUM")$day_3_median_volume), "JSON null did not round trip.")
  text <- paste(readLines(output, warn = FALSE), collapse = "\n")
  assert_true(!grepl("17.399999999", text, fixed = TRUE), "Binary floating-point tail was serialized.")
  exact <- jsonlite::toJSON(
    list(values = c(17.4, 28.8, 57.3, 16.9, 28.3)),
    auto_unbox = TRUE,
    digits = NA
  )
  assert_equal(
    as.character(exact),
    '{"values":[17.4,28.8,57.3,16.9,28.3]}',
    "Reviewed one-decimal values did not serialize cleanly as JSON numbers."
  )
})

scenario("staged promotion is atomic, no-op preserves bytes, and invalid structure preserves prior", {
  steady <- cnrfc_build_payload(roster, successful_attempts(), prior_payload, "2026-07-31T18:05:00Z")
  test_dir <- tempfile("cnrfc-promote-")
  dir.create(test_dir)
  on.exit(unlink(test_dir, recursive = TRUE, force = TRUE), add = TRUE)
  output <- file.path(test_dir, "canonical.json")
  cnrfc_write_json(steady, output)
  before <- unname(tools::md5sum(output))
  no_op <- cnrfc_build_payload(roster, successful_attempts("2026-07-31T19:00:00Z"), steady, "2026-07-31T19:01:00Z")
  assert_true(!cnrfc_stage_and_promote_payload(no_op, output, roster, steady), "No-op was promoted.")
  assert_equal(unname(tools::md5sum(output)), before, "No-op changed canonical bytes.")
  invalid <- steady
  invalid$records <- invalid$records[-1L]
  invalid$actual_record_count <- 50L
  assert_error(
    cnrfc_stage_and_promote_payload(invalid, output, roster, steady),
    "exactly 51 structural records", "Invalid staged payload was promoted."
  )
  assert_equal(unname(tools::md5sum(output)), before, "Structural failure changed canonical bytes.")
})

message("All major water-supply basin forecast tests passed.")
