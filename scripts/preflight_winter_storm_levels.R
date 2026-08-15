#!/usr/bin/env Rscript

# Lightweight inventory-only guard for unattended Winter Storm Levels runs.
# This entry point intentionally does not load geospatial packages or retrieve
# deterministic SNOWLVL GRIB byte ranges.

wsl_env <- function(name, default = "") {
  value <- Sys.getenv(name, unset = default)
  if (!nzchar(value)) default else value
}

wsl_project_root <- normalizePath(
  wsl_env("BRIM_WSL_PROJECT_ROOT", getwd()), winslash = "/", mustWork = TRUE
)
setwd(wsl_project_root)
source(file.path("scripts", "winter_storm_levels_helpers.R"))

required_packages <- c("httr2", "jsonlite")
missing_packages <- required_packages[
  !vapply(required_packages, requireNamespace, quietly = TRUE, FUN.VALUE = logical(1))
]
if (length(missing_packages)) {
  stop("Missing required preflight packages: ", paste(missing_packages, collapse = ", "))
}

config <- wsl_read_config(file.path("data", "input", "winter_storm_levels_config.csv"))
canonical_manifest_path <- normalizePath(
  wsl_env(
    "BRIM_WSL_CANONICAL_MANIFEST",
    file.path(
      wsl_project_root, "docs", "data", "winter-storm-levels",
      "winter_storm_levels_manifest.json"
    )
  ),
  winslash = "/", mustWork = FALSE
)
output_path <- normalizePath(
  wsl_env("BRIM_WSL_PREFLIGHT_JSON", file.path(tempdir(), "winter-storm-levels-preflight.json")),
  winslash = "/", mustWork = FALSE
)
now_text <- wsl_env("BRIM_WSL_NOW_UTC", "")
now <- if (nzchar(now_text)) wsl_as_utc(now_text) else wsl_as_utc(Sys.time())
if (is.na(now)) stop("BRIM_WSL_NOW_UTC is invalid.")

result <- wsl_preflight(
  now = now,
  config = config,
  canonical_manifest_path = canonical_manifest_path,
  trigger_type = wsl_env("BRIM_WSL_TRIGGER_TYPE", "unknown")
)
wsl_write_json(result, output_path)

message("Winter Storm Levels preflight outcome: ", result$preflight_outcome)
message("Observation time: ", result$observation_time_utc)
message("Canonical cycle: ", result$canonical_current_cycle %||% "none")
message("Candidate cycles: ", paste(result$candidate_cycles, collapse = ", "))
message("Newest complete cycle: ", result$newest_complete_cycle %||% "none")
message("Preflight result: ", output_path)
