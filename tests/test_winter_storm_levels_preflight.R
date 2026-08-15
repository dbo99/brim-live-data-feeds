#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(httr2)
  library(jsonlite)
})
source(file.path("scripts", "winter_storm_levels_helpers.R"))

checks <- 0L
check <- function(value, message) {
  checks <<- checks + 1L
  if (!isTRUE(value)) stop("Check failed: ", message, call. = FALSE)
}

config <- wsl_read_config(file.path("data", "input", "winter_storm_levels_config.csv"))
config$forecast_lead_hours <- c(1L, 6L)
now <- wsl_as_utc("2026-08-15T20:08:00Z")
fixture_root <- tempfile("winter-storm-level-preflight-")
dir.create(fixture_root, recursive = TRUE)
on.exit(unlink(fixture_root, recursive = TRUE, force = TRUE), add = TRUE)

write_canonical <- function(cycle_time) {
  path <- file.path(fixture_root, "winter_storm_levels_manifest.json")
  manifest <- list(
    product_id = "winter_storm_levels",
    cycle_time_utc = cycle_time,
    targets = list(list(cycle_time_utc = cycle_time))
  )
  jsonlite::write_json(manifest, path, auto_unbox = TRUE, pretty = TRUE)
  path
}

fixture_fetch <- function(complete_cycles = character(), error_cycle = NULL) {
  requests <- character()
  fetch <- function(url, config) {
    requests <<- c(requests, url)
    cycle_token <- sub(".*blend\\.([0-9]{8})/([0-9]{2})/.*", "\\1\\2", url)
    lead <- as.integer(sub(".*f([0-9]{3})\\.co.*", "\\1", url))
    if (!is.null(error_cycle) && identical(cycle_token, error_cycle)) {
      wsl_stop("fetch_failed_transient", "simulated inventory timeout")
    }
    if (!cycle_token %in% complete_cycles) {
      wsl_stop("source_unavailable", "simulated cycle not ready")
    }
    paste0(
      "1:100:d=", cycle_token,
      ":SNOWLVL:0 m above mean sea level:", lead, " hour fcst:\n",
      "2:200:d=", cycle_token, ":TMP:2 m above ground:", lead, " hour fcst:\n"
    )
  }
  list(fetch = fetch, requests = function() requests)
}

canonical <- write_canonical("2026-08-15T12:00:00Z")
source <- fixture_fetch(complete_cycles = "2026081512")
result <- wsl_preflight(now, config, canonical, source$fetch, "schedule")
check(identical(result$preflight_outcome, "SOURCE_NOT_READY"),
      "primary before 18Z readiness is a guarded source-not-ready no-op")
check(!isTRUE(result$build_started) && !isTRUE(result$publication_attempted),
      "source-not-ready preflight cannot start build or publication")
check(identical(result$newest_nominal_candidate, "2026-08-15T18:00:00Z") &&
        identical(result$newest_complete_cycle, "2026-08-15T12:00:00Z"),
      "preflight reports nominal and newest complete cycles separately")
check(all(endsWith(source$requests(), ".idx")),
      "preflight requests inventory URLs only")

source <- fixture_fetch(complete_cycles = c("2026081518", "2026081512"))
result <- wsl_preflight(now, config, canonical, source$fetch, "schedule")
check(identical(result$preflight_outcome, "NEW_CYCLE") &&
        identical(result$newest_complete_cycle, "2026-08-15T18:00:00Z") &&
        isTRUE(result$strictly_newer_complete_cycle),
      "primary with complete 18Z inventory is build eligible")
check(length(source$requests()) == length(config$forecast_lead_hours),
      "complete newest cycle needs exactly one index request per configured lead")

canonical <- write_canonical("2026-08-15T18:00:00Z")
source <- fixture_fetch(complete_cycles = "2026081518")
result <- wsl_preflight(now, config, canonical, source$fetch, "schedule")
check(identical(result$preflight_outcome, "NO_NEW_CYCLE") &&
        identical(result$newest_complete_cycle, "2026-08-15T18:00:00Z"),
      "fallback after primary success is a successful no-op")
check(!isTRUE(result$build_started) && !isTRUE(result$publication_attempted),
      "same-cycle fallback cannot start build or publication")
check(length(source$requests()) == 0L,
      "fallback short-circuits without inventory when canonical covers the newest nominal cycle")

source <- fixture_fetch(complete_cycles = "2026081512")
result <- wsl_preflight(now, config, canonical, source$fetch, "schedule")
check(identical(result$preflight_outcome, "NO_NEW_CYCLE") && length(source$requests()) == 0L,
      "delayed old scheduled work cannot regress an 18Z canonical cycle")

canonical <- write_canonical("2026-08-15T06:00:00Z")
source <- fixture_fetch(complete_cycles = c("2026081518", "2026081512"))
result <- wsl_preflight(now, config, canonical, source$fetch, "schedule")
check(identical(result$preflight_outcome, "NEW_CYCLE") &&
        identical(result$newest_complete_cycle, "2026-08-15T18:00:00Z"),
      "multiple-cycle jump selects the newest complete cycle")

canonical <- write_canonical("2026-08-15T12:00:00Z")
source <- fixture_fetch(error_cycle = "2026081518")
result <- wsl_preflight(now, config, canonical, source$fetch, "schedule")
check(identical(result$preflight_outcome, "SOURCE_ERROR") &&
        grepl("simulated inventory timeout", result$error, fixed = TRUE),
      "unexpected newest-cycle inventory failure remains a diagnostic error")
check(!isTRUE(result$build_started) && !isTRUE(result$publication_attempted),
      "source error cannot start build or publication")

cat(sprintf("Winter Storm Levels preflight tests passed (%d checks).\n", checks))
