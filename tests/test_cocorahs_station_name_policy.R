#!/usr/bin/env Rscript

source("scripts/cocorahs_station_name_policy.R")

check <- function(condition, label) {
  if (!isTRUE(condition)) stop("FAILED: ", label, call. = FALSE)
  message("PASS: ", label)
}

mixed <- tibble::tibble(
  stationNumber = sprintf("CO-TEST-%02d", 1:10),
  stationName = list(
    "Valid station",
    NULL,
    NA_character_,
    NaN,
    character(),
    "",
    " \t ",
    "NA",
    "NULL",
    "NaN"
  ),
  unchanged_value = seq_len(10)
)

mixed_result <- suppressMessages(cocorahs_apply_station_name_policy(mixed))
check(mixed_result$retained_before == 10L, "mixed fixture retained-before count")
check(mixed_result$omitted_count == 9L, "all unusable station-name forms are omitted")
check(mixed_result$retained_after == 1L, "mixed fixture retained-after count")
check(
  identical(mixed_result$rows$unchanged_value, 1L),
  "valid retained observation is unchanged"
)

one_invalid <- tibble::tibble(
  stationNumber = c("CA-GOOD-1", "CO-BAD-1"),
  stationName = list("Good station", NULL),
  unchanged_value = c("keep", "omit")
)
one_result <- suppressMessages(cocorahs_apply_station_name_policy(one_invalid))
check(
  one_result$omitted_count == 1L &&
    identical(one_result$rows$unchanged_value, "keep"),
  "one invalid retained observation is omitted and the candidate remains allowed"
)

ten_invalid <- tibble::tibble(
  stationNumber = sprintf("CO-TEN-%02d", 1:10),
  stationName = rep(list(NA_character_), 10)
)
ten_result <- suppressMessages(cocorahs_apply_station_name_policy(ten_invalid))
check(
  ten_result$omitted_count == 10L && nrow(ten_result$rows) == 0L,
  "exactly ten invalid retained observations are allowed and omitted"
)
check(
  length(ten_result$omitted_identifiers) == 10L,
  "allowed omission identifiers are bounded at the threshold"
)

eleven_invalid <- tibble::tibble(
  stationNumber = sprintf("CO-ELEVEN-%02d", 1:11),
  stationName = rep(list(NA_character_), 11)
)
threshold_error <- NULL
threshold_messages <- capture.output(
  tryCatch(
    cocorahs_apply_station_name_policy(eleven_invalid),
    error = function(error) threshold_error <<- error
  ),
  type = "message"
)
check(inherits(threshold_error, "error"), "eleven invalid retained observations fail closed")
check(
  grepl("omitted_missing_station_name=11; threshold=10", conditionMessage(threshold_error), fixed = TRUE),
  "threshold failure reports exact diagnostic counts"
)
identifier_message <- threshold_messages[grepl("omitted missing-station-name identifiers", threshold_messages)]
check(length(identifier_message) == 1L, "threshold failure emits one bounded identifier diagnostic")
check(
  grepl("stationNumber=CO-ELEVEN-10", identifier_message, fixed = TRUE) &&
    !grepl("stationNumber=CO-ELEVEN-11", identifier_message, fixed = TRUE),
  "threshold failure logs no more than ten identifiers"
)

message("All CoCoRaHS station-name policy tests passed.")
