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
##   BRIM's groundwater layer has two related update paths:
##     1. GitHub latest-feed refresh for the current candidate list.
##     2. Local candidate/history refresh for expanding the candidate universe,
##        backfilling history, and rebuilding the static-map cache.
##
##   This preview workflow gives early warning when USGS recent measurements
##   would add/drop sites under one or more lookback windows. It helps decide
##   when to run the heavier local refresh chain.
##
## DOMAIN:
##   The default bbox is intentionally broader than California. It is the BRIM
##   groundwater discovery envelope: California plus nearby border-context areas
##   useful for hydrologic screening, including southern Oregon, western Nevada,
##   and far western/southwestern Arizona. Treat output as the BRIM discovery
##   domain, not strictly California-only.
##
## OUTPUTS:
##   qa/usgs_groundwater_candidate_discovery_preview/<timestamp>/
##     - README_first.txt
##     - usgs_gw_candidate_discovery_preview_summary.csv
##     - usgs_gw_candidate_additions_vs_current_by_lookback.csv
##     - usgs_gw_candidate_current_missing_by_lookback.csv
##
##   The output is intentionally small and review-friendly. Earlier versions
##   wrote one file per lookback and per category, which was too many files for
##   a weekly QA artifact. This version writes one summary plus two combined
##   detail tables.
##
## ENVIRONMENT VARIABLES:
##   BRIM_GW_PREVIEW_LOOKBACK_DAYS  Comma-separated lookbacks; default below.
##   BRIM_GW_PREVIEW_BBOX           Expert/local override only; not exposed in
##                                  the GitHub workflow UI.
##   BRIM_GW_PREVIEW_PARAMETER_CODE USGS parameter; default 72019.
##
## IMPORTANT:
##   This version intentionally uses dataRetrieval again. The raw-curl preview
##   was leaner but too fragile. This script uses the same official retrieval
##   pathway as the local/production groundwater refresh scripts.
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

# ---- CLI/progress safety ----------------------------------------------------
##
## The preview runs in batch mode. Disable progress bars to avoid local/RStudio
## or GitHub console formatting issues while dataRetrieval/curl requests run.

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

  names(out) <- c("xmin", "ymin", "xmax", "ymax")

  if (!(out[["xmin"]] < out[["xmax"]] && out[["ymin"]] < out[["ymax"]])) {
    warning("Invalid bbox coordinate order; using default bbox.")
    return(default)
  }

  ## Guard against accidental CONUS/global scrapes. This still allows the BRIM
  ## hydrologic-California plus border-context envelope.
  allowed <- c(xmin = -125.5, ymin = 31.0, xmax = -109.0, ymax = 43.5)
  if (out[["xmin"]] < allowed[["xmin"]] || out[["xmax"]] > allowed[["xmax"]] ||
      out[["ymin"]] < allowed[["ymin"]] || out[["ymax"]] > allowed[["ymax"]]) {
    warning("BBox is outside the allowed BRIM preview envelope; using default bbox.")
    return(default)
  }

  as.numeric(out)
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

pt_empty_discovered <- function() {
  tibble(
    site_no = character(),
    monitoring_location_id = character(),
    latest_time_utc = as.POSIXct(character(), tz = "UTC"),
    latest_date = as.Date(character()),
    latest_age_days = numeric(),
    latest_value_ft_bgs = numeric(),
    measurement_rows = integer()
  )
}


pt_nice_int <- function(x) {
  formatC(as.integer(x), format = "d", big.mark = ",")
}

pt_make_summary_sentence <- function(row) {
  paste0(
    row$lookback_days,
    " days: discovered ", pt_nice_int(row$discovered_sites),
    " site(s); +", pt_nice_int(row$additions_vs_current),
    " vs current; ", pt_nice_int(row$current_missing_from_preview),
    " current candidate(s) missing; ", pt_nice_int(row$latest_90d_count),
    " site(s) measured within 90 days."
  )
}

# ---- Configuration ----------------------------------------------------------

## Single source of truth for the default preview lookback windows.
DEFAULT_LOOKBACK_DAYS <- c(800L, 1826L)

## Fixed BRIM groundwater discovery envelope. Broader than CA-only because
## groundwater screening often needs adjacent-basin context.
DEFAULT_BBOX <- c(-124.6, 32.3, -113.8, 42.2)

lookback_days <- pt_parse_int_vec(
  Sys.getenv(
    "BRIM_GW_PREVIEW_LOOKBACK_DAYS",
    unset = paste(DEFAULT_LOOKBACK_DAYS, collapse = ",")
  ),
  default = DEFAULT_LOOKBACK_DAYS
)

bbox <- pt_parse_bbox(
  Sys.getenv("BRIM_GW_PREVIEW_BBOX", unset = paste(DEFAULT_BBOX, collapse = ",")),
  default = DEFAULT_BBOX
)

parameter_code <- Sys.getenv("BRIM_GW_PREVIEW_PARAMETER_CODE", unset = "72019")
if (is.na(parameter_code) || trimws(parameter_code) == "") parameter_code <- "72019"

run_time_utc <- format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ")
run_stamp <- format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y%m%d_%H%M%S")

out_dir <- file.path("qa", "usgs_groundwater_candidate_discovery_preview", run_stamp)
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

run_diag_path <- file.path(out_dir, "run_diagnostics.txt")
cat(
  paste0(
    "BRIM groundwater candidate discovery preview\n",
    "Run time UTC: ", run_time_utc, "\n",
    "Lookbacks: ", paste(lookback_days, collapse = ", "), "\n",
    "Parameter code: ", parameter_code, "\n",
    "Bbox: ", paste(bbox, collapse = ","), "\n",
    "Output directory: ", normalizePath(out_dir, winslash = "/", mustWork = FALSE), "\n"
  ),
  file = run_diag_path
)

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
message("Output directory: ", normalizePath(out_dir, winslash = "/", mustWork = FALSE))

summary_rows <- list()
addition_rows <- list()
missing_rows <- list()

# ---- Discovery loop ---------------------------------------------------------

for (lb in lookback_days) {
  lb <- as.integer(lb)
  start_date <- Sys.Date() - lb
  end_date <- Sys.Date() + 1L

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
    discovered_latest <- pt_empty_discovered()
  } else {
    fm2 <- fm |>
      mutate(
        monitoring_location_id = pt_chr_col(fm, "monitoring_location_id"),
        parameter_code_out = pt_chr_col(fm, "parameter_code"),
        site_no = pt_norm_site_no(.data$monitoring_location_id),
        time_utc = pt_time_col(fm, "time"),
        value_num = pt_num_col(fm, "value")
      ) |>
      filter(
        !is.na(.data$site_no),
        .data$site_no != "",
        is.na(.data$parameter_code_out) | .data$parameter_code_out == parameter_code
      )

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
    latest_5y_count = sum(discovered_latest$latest_age_days <= 1826, na.rm = TRUE),
    known_sites_found = known_found,
    known_sites_missing = known_missing,
    notes = paste(
      "Preview only; no production candidate files were modified.",
      "Bbox is the BRIM groundwater discovery envelope and includes border-context wells."
    )
  )

  summary_rows[[as.character(lb)]] <- summary_row

  ## Keep weekly artifacts review-friendly: collect details into one combined
  ## additions table and one combined missing-current table rather than writing
  ## one CSV per lookback.
  addition_rows[[as.character(lb)]] <- additions |>
    mutate(lookback_days = lb, .before = 1)

  missing_rows[[as.character(lb)]] <- missing_from_preview |>
    mutate(lookback_days = lb, .before = 1)

  message("Raw field-measurement rows: ", nrow(fm))
  message("Discovered sites: ", length(discovered_sites))
  message("Additions vs current candidates: ", nrow(additions))
  message("Current candidates missing from preview: ", nrow(missing_from_preview))

  cat(
    paste0(
      "\nLookback ", lb, " days\n",
      "  Raw rows: ", nrow(fm), "\n",
      "  Discovered sites: ", length(discovered_sites), "\n",
      "  Additions vs current: ", nrow(additions), "\n",
      "  Current missing from preview: ", nrow(missing_from_preview), "\n",
      "  Known sites found: ", known_found, "\n",
      "  Known sites missing: ", known_missing, "\n"
    ),
    file = run_diag_path,
    append = TRUE
  )
}

summary_tbl <- bind_rows(summary_rows)
summary_tbl$summary_statement <- vapply(
  seq_len(nrow(summary_tbl)),
  function(i) pt_make_summary_sentence(summary_tbl[i, , drop = FALSE]),
  character(1)
)

additions_tbl <- bind_rows(addition_rows)
if (nrow(additions_tbl) == 0) {
  additions_tbl <- tibble(
    lookback_days = integer(),
    site_no = character(),
    monitoring_location_id = character(),
    latest_time_utc = as.POSIXct(character(), tz = "UTC"),
    latest_date = as.Date(character()),
    latest_age_days = numeric(),
    latest_value_ft_bgs = numeric(),
    measurement_rows = integer()
  )
}

missing_tbl <- bind_rows(missing_rows)
if (nrow(missing_tbl) == 0) {
  missing_tbl <- tibble(
    lookback_days = integer(),
    site_no = character(),
    station_nm = character(),
    current_latest_wl_date = character(),
    current_latest_age_days = character(),
    current_candidate_source = character()
  )
}

readr::write_csv(
  summary_tbl,
  file.path(out_dir, "usgs_gw_candidate_discovery_preview_summary.csv")
)

readr::write_csv(
  additions_tbl,
  file.path(out_dir, "usgs_gw_candidate_additions_vs_current_by_lookback.csv")
)

readr::write_csv(
  missing_tbl,
  file.path(out_dir, "usgs_gw_candidate_current_missing_by_lookback.csv")
)

quick_read <- c(
  "BRIM USGS groundwater candidate-discovery preview",
  "",
  paste0("Run time UTC: ", run_time_utc),
  paste0("Current production candidate sites: ", pt_nice_int(length(current_sites))),
  paste0("Parameter code: ", parameter_code),
  paste0("BRIM groundwater discovery bbox: ", paste(bbox, collapse = ",")),
  "",
  "Quick read:",
  paste0("- ", summary_tbl$summary_statement),
  "",
  "How to use this artifact:",
  "- Start with usgs_gw_candidate_discovery_preview_summary.csv.",
  "- Review additions in usgs_gw_candidate_additions_vs_current_by_lookback.csv.",
  "- Review current production candidates missing from the preview in usgs_gw_candidate_current_missing_by_lookback.csv; this should usually be zero.",
  "- This preview does not modify production candidate, history, GeoJSON, or BRIM HTML files.",
  "",
  "Recommendation cue:",
  if (all(summary_tbl$current_missing_from_preview == 0) && any(summary_tbl$additions_vs_current > 0)) {
    paste0(
      "Current production candidates are all still found. Consider a local groundwater refresh when additions_vs_current is large enough to matter for the next BRIM release."
    )
  } else if (any(summary_tbl$current_missing_from_preview > 0)) {
    "Some current production candidates were not found in the preview window. Review the missing-current CSV before changing production settings."
  } else {
    "No major candidate-universe changes were detected."
  }
)

writeLines(quick_read, file.path(out_dir, "README_first.txt"))

## Fail safely if the preview produced an obviously unusable output. This keeps
## a weekly artifact from looking successful when the underlying API query did
## not actually discover BRIM groundwater sites.
if (all(summary_tbl$discovered_sites == 0)) {
  stop("Preview discovered zero sites for all lookbacks; output is not usable.")
}

if (any(summary_tbl$known_sites_missing != "")) {
  stop(
    "Known QA site(s) missing from at least one preview lookback: ",
    paste(summary_tbl$known_sites_missing[summary_tbl$known_sites_missing != ""], collapse = "; ")
  )
}

message("\nUSGS groundwater candidate discovery preview complete.")
message("Saved preview QA to: ", normalizePath(out_dir, winslash = "/", mustWork = FALSE))
print(summary_tbl, n = Inf)
