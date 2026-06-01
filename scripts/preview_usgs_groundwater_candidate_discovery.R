# ==== preview_usgs_groundwater_candidate_discovery.R =========================
##
## PURPOSE:
##   GitHub Actions preview/QA for BRIM Ops Live groundwater candidate discovery.
##
##   This script DOES NOT overwrite production candidate inputs or GeoJSON feeds.
##   It queries recent USGS Water Data API field measurements for groundwater
##   depth-to-water parameter 72019 across the BRIM groundwater discovery
##   envelope, compares discovered sites to the current production candidate CSV,
##   and writes QA artifacts for review.
##
## WHY THIS EXISTS:
##   BRIM's groundwater layer has two related but different update paths:
##     1. GitHub latest-feed refresh for the current candidate list.
##     2. Local candidate/history refresh for expanding the candidate universe,
##        backfilling history, and rebuilding the static-map cache.
##
##   This preview workflow gives early warning when USGS recent measurements
##   would add/drop sites under one or more lookback windows. It helps decide
##   when to run the heavier local refresh chain.
##
## OUTPUTS:
##   qa/usgs_groundwater_candidate_discovery_preview/<timestamp>/
##     - usgs_gw_candidate_discovery_preview_summary.csv
##     - usgs_gw_candidate_discovery_preview_summary.json
##     - usgs_gw_candidate_discovered_latest_<lookback>d.csv
##     - usgs_gw_candidate_additions_vs_current_<lookback>d.csv
##     - usgs_gw_candidate_current_missing_from_preview_<lookback>d.csv
##
## ENVIRONMENT VARIABLES:
##   BRIM_GW_PREVIEW_LOOKBACK_DAYS  Comma-separated lookbacks; default is set once below.
##   BRIM_GW_PREVIEW_BBOX           xmin,ymin,xmax,ymax; default broad CA + border.
##   BRIM_GW_PREVIEW_PARAMETER_CODE USGS parameter; default 72019.
##
## IMPORTANT:
##   The broad bbox intentionally captures nearby border-context wells useful for
##   regional BRIM screening (for example upper Amargosa / western Nevada). Treat
##   bbox output as BRIM discovery-envelope sites, not strictly California-only.
## ============================================================================

required_pkgs <- c("dataRetrieval", "dplyr", "readr", "jsonlite", "tibble")
missing_pkgs <- required_pkgs[!vapply(required_pkgs, requireNamespace, logical(1), quietly = TRUE)]

if (length(missing_pkgs) > 0) {
  stop(
    "Missing required R packages: ", paste(missing_pkgs, collapse = ", "),
    "\nInstall them before running locally, or let the GitHub Action install them."
  )
}

suppressPackageStartupMessages({
  library(dplyr)
  library(readr)
  library(jsonlite)
  library(tibble)
})

# ---- CLI/progress safety -----------------------------------------------------
##
## dataRetrieval uses the modern USGS Water Data API and may trigger cli/curl
## progress reporting in interactive R sessions. Some Windows/RStudio/package
## combinations can fail while formatting the progress ETA, even though the API
## request itself is valid. The preview script is a QA/batch script, so progress
## bars are not needed. Disable them for more reliable local and GitHub runs.

options(
  cli.progress_show_after = Inf,
  cli.progress_clear = FALSE,
  cli.dynamic = FALSE
)

Sys.setenv(
  CLI_NO_PROGRESS = "true",
  R_CLI_NUM_COLORS = "1"
)


# ---- Small helpers ----------------------------------------------------------

pt_norm_site_no <- function(x) {
  x <- as.character(x)
  x <- trimws(x)
  x <- gsub("^USGS-", "", x, ignore.case = TRUE)
  x <- gsub("\\.0$", "", x)
  x <- gsub("[^0-9]", "", x)
  x[x == ""] <- NA_character_
  x
}

pt_parse_int_vec <- function(x, default) {
  x <- as.character(x)
  if (length(x) == 0 || is.na(x) || trimws(x) == "") return(default)
  out <- suppressWarnings(as.integer(strsplit(x, ",", fixed = TRUE)[[1]]))
  out <- out[!is.na(out) & out > 0]
  if (length(out) == 0) default else unique(out)
}

pt_parse_bbox <- function(x, default) {
  x <- as.character(x)
  if (length(x) == 0 || is.na(x) || trimws(x) == "") return(default)
  out <- suppressWarnings(as.numeric(strsplit(x, ",", fixed = TRUE)[[1]]))
  if (length(out) != 4 || any(is.na(out))) {
    warning("Invalid BRIM_GW_PREVIEW_BBOX; using default bbox.")
    return(default)
  }
  out
}

pt_chr_col <- function(x, nm) {
  if (nm %in% names(x)) as.character(x[[nm]]) else rep(NA_character_, nrow(x))
}

pt_num_col <- function(x, nm) {
  if (nm %in% names(x)) suppressWarnings(as.numeric(x[[nm]])) else rep(NA_real_, nrow(x))
}

pt_time_col <- function(x, nm) {
  if (!nm %in% names(x)) return(rep(as.POSIXct(NA), nrow(x)))
  suppressWarnings(as.POSIXct(x[[nm]], tz = "UTC"))
}

# ---- Configuration ----------------------------------------------------------

## Single source of truth for the preview lookback windows.
##
## WHY:
##   Keep the default in one place so local testing and scheduled GitHub runs do
##   not drift apart. To test a different set locally or through workflow inputs,
##   set BRIM_GW_PREVIEW_LOOKBACK_DAYS; otherwise this vector controls the
##   default preview windows.

DEFAULT_LOOKBACK_DAYS <- c(800L, 1826L)

lookback_days <- pt_parse_int_vec(
  Sys.getenv(
    "BRIM_GW_PREVIEW_LOOKBACK_DAYS",
    unset = paste(DEFAULT_LOOKBACK_DAYS, collapse = ",")
  ),
  default = DEFAULT_LOOKBACK_DAYS
)

bbox <- pt_parse_bbox(
  Sys.getenv("BRIM_GW_PREVIEW_BBOX", unset = "-124.6,32.3,-113.8,42.2"),
  default = c(-124.6, 32.3, -113.8, 42.2)
)

parameter_code <- Sys.getenv("BRIM_GW_PREVIEW_PARAMETER_CODE", unset = "72019")
if (is.na(parameter_code) || trimws(parameter_code) == "") parameter_code <- "72019"

run_time_utc <- format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ")
run_stamp <- format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y%m%d_%H%M%S")

out_dir <- file.path("qa", "usgs_groundwater_candidate_discovery_preview", run_stamp)
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

candidate_path <- file.path("data", "input", "usgs_groundwater_latest_index_ca.csv")

if (file.exists(candidate_path)) {
  current_candidates <- readr::read_csv(
    candidate_path,
    show_col_types = FALSE,
    col_types = readr::cols(.default = readr::col_character())
  ) |>
    mutate(site_no_norm = pt_norm_site_no(.data$site_no)) |>
    filter(!is.na(.data$site_no_norm), .data$site_no_norm != "") |>
    distinct(.data$site_no_norm, .keep_all = TRUE)
} else {
  warning("Current candidate CSV not found: ", candidate_path)
  current_candidates <- tibble(site_no_norm = character())
}

current_sites <- current_candidates$site_no_norm

known_sites <- c(
  "362402116280901" # Inyo-BLM 1; known recent-measurement QA site.
)

message("BRIM USGS groundwater candidate discovery preview")
message("Run time UTC: ", run_time_utc)
message("Parameter code: ", parameter_code)
message("Lookback day(s): ", paste(lookback_days, collapse = ", "))
message("Bbox: ", paste(bbox, collapse = ","))
message("Current production candidate sites: ", length(current_sites))

summary_rows <- list()

# ---- Discovery loop ---------------------------------------------------------

for (lb in lookback_days) {
  lb <- as.integer(lb)
  start_date <- Sys.Date() - lb
  end_date <- Sys.Date() + 1

  message("\n--- Preview lookback: ", lb, " days ---")
  message("Query window: ", start_date, " / ", end_date)

  fm <- dataRetrieval::read_waterdata_field_measurements(
    parameter_code = parameter_code,
    time = paste0(start_date, "/", end_date),
    bbox = bbox,
    properties = c(
      "monitoring_location_id",
      "parameter_code",
      "time",
      "value",
      "unit_of_measure",
      "qualifier",
      "approval_status",
      "measuring_agency"
    ),
    skipGeometry = TRUE
  )

  if (!is.data.frame(fm) || nrow(fm) == 0) {
    discovered_latest <- tibble(
      site_no = character(),
      monitoring_location_id = character(),
      latest_time_utc = as.POSIXct(character()),
      latest_date = as.Date(character()),
      latest_age_days = numeric(),
      latest_value_ft_bgs = numeric(),
      measurement_rows = integer()
    )
  } else {
    fm2 <- fm |>
      mutate(
        monitoring_location_id = pt_chr_col(cur_data_all(), "monitoring_location_id"),
        site_no = pt_norm_site_no(.data$monitoring_location_id),
        time_utc = pt_time_col(cur_data_all(), "time"),
        value_num = pt_num_col(cur_data_all(), "value")
      ) |>
      filter(!is.na(.data$site_no), .data$site_no != "")

    discovered_latest <- fm2 |>
      arrange(.data$site_no, desc(.data$time_utc)) |>
      group_by(.data$site_no) |>
      summarize(
        monitoring_location_id = first(.data$monitoring_location_id),
        latest_time_utc = first(.data$time_utc),
        latest_date = as.Date(first(.data$time_utc)),
        latest_age_days = as.numeric(Sys.Date() - as.Date(first(.data$time_utc))),
        latest_value_ft_bgs = first(.data$value_num),
        measurement_rows = dplyr::n(),
        .groups = "drop"
      ) |>
      arrange(.data$latest_age_days, .data$site_no)
  }

  discovered_sites <- discovered_latest$site_no

  additions <- discovered_latest |>
    filter(!.data$site_no %in% current_sites) |>
    arrange(.data$latest_age_days, .data$site_no)

  missing_from_preview <- current_candidates |>
    filter(!.data$site_no_norm %in% discovered_sites) |>
    transmute(
      site_no = .data$site_no_norm,
      station_nm = if ("station_nm" %in% names(current_candidates)) .data$station_nm else NA_character_,
      current_latest_wl_date = if ("latest_wl_date" %in% names(current_candidates)) .data$latest_wl_date else NA_character_,
      current_latest_age_days = if ("latest_age_days" %in% names(current_candidates)) .data$latest_age_days else NA_character_,
      current_candidate_source = if ("candidate_source" %in% names(current_candidates)) .data$candidate_source else NA_character_
    ) |>
    arrange(.data$site_no)

  known_found <- paste(known_sites[known_sites %in% discovered_sites], collapse = ";")
  known_missing <- paste(known_sites[!known_sites %in% discovered_sites], collapse = ";")

  summary_row <- tibble(
    run_time_utc = run_time_utc,
    lookback_days = lb,
    parameter_code = parameter_code,
    bbox = paste(bbox, collapse = ","),
    query_start_date = as.character(start_date),
    query_end_date = as.character(end_date),
    raw_field_measurement_rows = nrow(fm),
    discovered_sites = length(discovered_sites),
    current_candidate_sites = length(current_sites),
    additions_vs_current = nrow(additions),
    current_missing_from_preview = nrow(missing_from_preview),
    latest_30d_count = sum(discovered_latest$latest_age_days <= 30, na.rm = TRUE),
    latest_90d_count = sum(discovered_latest$latest_age_days <= 90, na.rm = TRUE),
    latest_1y_count = sum(discovered_latest$latest_age_days <= 365, na.rm = TRUE),
    latest_2y_count = sum(discovered_latest$latest_age_days <= 730, na.rm = TRUE),
    latest_3y_count = sum(discovered_latest$latest_age_days <= 1095, na.rm = TRUE),
    known_sites_found = known_found,
    known_sites_missing = known_missing,
    notes = paste(
      "Preview only; no production candidate files were modified.",
      "Bbox is the BRIM discovery envelope and may include border-context wells."
    )
  )

  summary_rows[[as.character(lb)]] <- summary_row

  readr::write_csv(
    discovered_latest,
    file.path(out_dir, paste0("usgs_gw_candidate_discovered_latest_", lb, "d.csv"))
  )

  readr::write_csv(
    additions,
    file.path(out_dir, paste0("usgs_gw_candidate_additions_vs_current_", lb, "d.csv"))
  )

  readr::write_csv(
    missing_from_preview,
    file.path(out_dir, paste0("usgs_gw_candidate_current_missing_from_preview_", lb, "d.csv"))
  )

  message("Raw field-measurement rows: ", nrow(fm))
  message("Discovered sites: ", length(discovered_sites))
  message("Additions vs current candidates: ", nrow(additions))
  message("Current candidates missing from preview: ", nrow(missing_from_preview))
}

summary_tbl <- bind_rows(summary_rows)

readr::write_csv(
  summary_tbl,
  file.path(out_dir, "usgs_gw_candidate_discovery_preview_summary.csv")
)

jsonlite::write_json(
  list(
    run_time_utc = run_time_utc,
    output_dir = out_dir,
    summary = summary_tbl
  ),
  path = file.path(out_dir, "usgs_gw_candidate_discovery_preview_summary.json"),
  pretty = TRUE,
  auto_unbox = TRUE,
  na = "null"
)

message("\nUSGS groundwater candidate discovery preview complete.")
message("Saved preview QA to: ", out_dir)
print(summary_tbl, n = Inf)
