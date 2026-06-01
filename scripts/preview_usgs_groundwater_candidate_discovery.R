# ==== preview_usgs_groundwater_candidate_discovery.R ==========================
##
## PURPOSE:
##   Preview the USGS groundwater recent-feed candidate universe for BRIM without
##   modifying production inputs, GeoJSON, history summaries, or the final map.
##
## DESIGN:
##   This script is intentionally lean for GitHub Actions. It does NOT use
##   dataRetrieval, dplyr, readr, tibble, sf, httr2, or the R curl package.
##   It uses only base R plus jsonlite, and fetches USGS Water Data API JSON via
##   the system curl command when available.
##
## OUTPUTS:
##   qa/usgs_groundwater_candidate_discovery_preview/<timestamp>/
##     usgs_gw_candidate_discovery_preview_summary.csv
##     usgs_gw_candidate_discovery_preview_summary.json
##     usgs_gw_candidate_discovered_latest_<N>d.csv
##     usgs_gw_candidate_additions_vs_current_<N>d.csv
##     usgs_gw_candidate_current_missing_from_preview_<N>d.csv
## ============================================================================

# ---- 1. Minimal dependency check --------------------------------------------

required_pkgs <- c("jsonlite")
missing_pkgs <- required_pkgs[!vapply(required_pkgs, requireNamespace, logical(1), quietly = TRUE)]

if (length(missing_pkgs) > 0) {
  stop(
    "Missing required R packages: ", paste(missing_pkgs, collapse = ", "),
    "\nInstall jsonlite before running locally, or let the GitHub Action install it."
  )
}

# ---- 2. Configuration -------------------------------------------------------

DEFAULT_LOOKBACK_DAYS <- c(800L, 1826L)
DEFAULT_BBOX <- c(-124.6, 32.3, -113.8, 42.2)
DEFAULT_PARAMETER_CODE <- "72019"
DEFAULT_KNOWN_SITES <- c("362402116280901")

pt_parse_int_vec <- function(x, default) {
  x <- trimws(as.character(x))
  if (length(x) == 0 || is.na(x) || x == "") return(default)
  vals <- suppressWarnings(as.integer(trimws(strsplit(x, ",", fixed = TRUE)[[1]])))
  vals <- vals[!is.na(vals) & vals > 0]
  if (length(vals) == 0) default else unique(vals)
}

pt_parse_num_vec <- function(x, default, n = 4L) {
  x <- trimws(as.character(x))
  if (length(x) == 0 || is.na(x) || x == "") return(default)
  vals <- suppressWarnings(as.numeric(trimws(strsplit(x, ",", fixed = TRUE)[[1]])))
  vals <- vals[!is.na(vals)]
  if (length(vals) != n) default else vals
}

pt_parse_chr_vec <- function(x, default) {
  x <- trimws(as.character(x))
  if (length(x) == 0 || is.na(x) || x == "") return(default)
  vals <- trimws(strsplit(x, ",", fixed = TRUE)[[1]])
  vals <- vals[!is.na(vals) & vals != ""]
  if (length(vals) == 0) default else unique(vals)
}

lookback_days <- pt_parse_int_vec(
  Sys.getenv("BRIM_GW_PREVIEW_LOOKBACK_DAYS", unset = ""),
  default = DEFAULT_LOOKBACK_DAYS
)

bbox <- pt_parse_num_vec(
  Sys.getenv("BRIM_GW_PREVIEW_BBOX", unset = ""),
  default = DEFAULT_BBOX,
  n = 4L
)

parameter_code <- Sys.getenv("BRIM_GW_PREVIEW_PARAMETER_CODE", unset = "")
if (!nzchar(parameter_code)) parameter_code <- DEFAULT_PARAMETER_CODE

known_sites <- pt_parse_chr_vec(
  Sys.getenv("BRIM_GW_PREVIEW_KNOWN_SITES", unset = ""),
  default = DEFAULT_KNOWN_SITES
)

run_time <- format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
stamp <- format(Sys.time(), "%Y%m%d_%H%M%S", tz = "UTC")

out_root <- file.path("qa", "usgs_groundwater_candidate_discovery_preview")
out_dir <- file.path(out_root, stamp)
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

message("BRIM USGS groundwater candidate-discovery preview")
message("Working directory: ", normalizePath(getwd(), winslash = "/", mustWork = FALSE))
message("Output directory: ", normalizePath(out_dir, winslash = "/", mustWork = FALSE))
message("Lookback days: ", paste(lookback_days, collapse = ", "))
message("BBox: ", paste(bbox, collapse = ","))
message("Parameter code: ", parameter_code)

# ---- 3. Helpers -------------------------------------------------------------

pt_norm_site_no <- function(x) {
  x <- as.character(x)
  x <- trimws(x)
  x <- gsub("^USGS-", "", x, ignore.case = TRUE)
  x <- gsub("\\.0$", "", x)
  x <- gsub("[^0-9]", "", x)
  x[x == ""] <- NA_character_
  x
}

pt_urlencode <- function(x) {
  utils::URLencode(as.character(x), reserved = TRUE)
}

pt_fetch_text <- function(url) {
  tmp <- tempfile(fileext = ".json")
  on.exit(unlink(tmp), add = TRUE)

  curl_bin <- Sys.which("curl")

  if (nzchar(curl_bin)) {
    args <- c(
      "--fail",
      "--silent",
      "--show-error",
      "--location",
      "--retry", "3",
      "--retry-delay", "2",
      "--max-time", "240",
      "--output", tmp,
      url
    )

    status <- system2(curl_bin, args = args)

    if (identical(status, 0L) && file.exists(tmp) && file.info(tmp)$size > 0) {
      return(paste(readLines(tmp, warn = FALSE, encoding = "UTF-8"), collapse = "\n"))
    }
  }

  ## Local fallback if system curl is unavailable.
  utils::download.file(url, tmp, mode = "wb", quiet = TRUE)
  paste(readLines(tmp, warn = FALSE, encoding = "UTF-8"), collapse = "\n")
}

pt_build_field_measurement_url <- function(start_date, end_date) {
  props <- paste(
    c(
      "monitoring_location_id",
      "parameter_code",
      "time",
      "value",
      "unit_of_measure",
      "qualifier",
      "approval_status",
      "measuring_agency"
    ),
    collapse = ","
  )

  query <- paste0(
    "f=json",
    "&lang=en-US",
    "&skipGeometry=TRUE",
    "&bbox=", pt_urlencode(paste(bbox, collapse = ",")),
    "&properties=", pt_urlencode(props),
    "&parameter_code=", pt_urlencode(parameter_code),
    "&time=", pt_urlencode(paste0(start_date, "/", end_date)),
    "&limit=50000"
  )

  paste0(
    "https://api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/items?",
    query
  )
}

pt_features_to_df <- function(features) {
  if (length(features) == 0) return(data.frame())

  rows <- lapply(features, function(f) {
    p <- f$properties
    if (is.null(p)) p <- list()
    as.data.frame(p, stringsAsFactors = FALSE, optional = TRUE)
  })

  all_names <- unique(unlist(lapply(rows, names), use.names = FALSE))

  rows <- lapply(rows, function(x) {
    missing <- setdiff(all_names, names(x))
    for (nm in missing) x[[nm]] <- NA
    x[all_names]
  })

  out <- do.call(rbind, rows)
  rownames(out) <- NULL
  out
}

pt_fetch_field_measurements <- function(start_date, end_date) {
  url <- pt_build_field_measurement_url(start_date, end_date)
  message("Requesting:\n", url)

  txt <- pt_fetch_text(url)
  parsed <- jsonlite::fromJSON(txt, simplifyVector = FALSE)

  features <- parsed$features
  if (is.null(features)) features <- list()

  out <- pt_features_to_df(features)

  ## The preview windows tested so far are below the 50k limit. If this warning
  ## appears, we should add OGC pagination before using the result for decisions.
  if (nrow(out) >= 50000) {
    warning(
      "USGS preview query returned >= 50,000 rows, which may indicate truncation. ",
      "Add pagination before relying on this lookback window."
    )
  }

  out
}

pt_read_current_candidates <- function() {
  path <- file.path("data", "input", "usgs_groundwater_latest_index_ca.csv")

  if (!file.exists(path)) {
    warning("Current production candidate CSV not found: ", path)
    return(data.frame(site_no = character(), stringsAsFactors = FALSE))
  }

  x <- utils::read.csv(path, stringsAsFactors = FALSE, check.names = FALSE)

  if (!"site_no" %in% names(x)) {
    warning("Current candidate CSV has no site_no column: ", path)
    return(data.frame(site_no = character(), stringsAsFactors = FALSE))
  }

  data.frame(
    site_no = unique(na.omit(pt_norm_site_no(x$site_no))),
    stringsAsFactors = FALSE
  )
}

pt_latest_by_site <- function(fm) {
  if (nrow(fm) == 0) {
    return(data.frame(site_no = character(), stringsAsFactors = FALSE))
  }

  if (!"monitoring_location_id" %in% names(fm)) {
    stop("USGS response did not include monitoring_location_id.")
  }

  fm$site_no <- pt_norm_site_no(fm$monitoring_location_id)

  if ("time" %in% names(fm)) {
    fm$time_utc <- suppressWarnings(as.POSIXct(fm$time, tz = "UTC"))
  } else {
    fm$time_utc <- as.POSIXct(NA, tz = "UTC")
  }

  fm <- fm[!is.na(fm$site_no), , drop = FALSE]

  if (nrow(fm) == 0) {
    return(data.frame(site_no = character(), stringsAsFactors = FALSE))
  }

  ord <- order(fm$site_no, fm$time_utc, decreasing = FALSE, na.last = TRUE)
  fm <- fm[ord, , drop = FALSE]

  keep_idx <- !duplicated(fm$site_no, fromLast = TRUE)
  latest <- fm[keep_idx, , drop = FALSE]

  latest <- latest[order(latest$site_no), , drop = FALSE]
  rownames(latest) <- NULL
  latest
}

pt_count_latest_age <- function(latest, threshold_days, end_date) {
  if (!"time_utc" %in% names(latest) || nrow(latest) == 0) return(0L)
  end_time <- as.POSIXct(paste0(end_date, " 23:59:59"), tz = "UTC")
  age_days <- as.numeric(difftime(end_time, latest$time_utc, units = "days"))
  sum(!is.na(age_days) & age_days <= threshold_days)
}

# ---- 4. Main preview loop ---------------------------------------------------

current_candidates <- pt_read_current_candidates()
current_sites <- unique(current_candidates$site_no)

summary_rows <- list()
end_date <- as.character(Sys.Date())

for (lb in lookback_days) {
  message("\n--- Preview lookback: ", lb, " days ---")

  start_date <- as.character(Sys.Date() - lb)
  message("Query window: ", start_date, " / ", end_date)

  fm <- pt_fetch_field_measurements(start_date, end_date)
  latest <- pt_latest_by_site(fm)

  discovered_sites <- unique(latest$site_no)
  discovered_sites <- discovered_sites[!is.na(discovered_sites) & discovered_sites != ""]

  additions <- setdiff(discovered_sites, current_sites)
  missing_from_preview <- setdiff(current_sites, discovered_sites)

  known_found <- intersect(pt_norm_site_no(known_sites), discovered_sites)
  known_missing <- setdiff(pt_norm_site_no(known_sites), discovered_sites)

  latest_out <- latest
  additions_out <- latest[latest$site_no %in% additions, , drop = FALSE]
  missing_out <- data.frame(site_no = missing_from_preview, stringsAsFactors = FALSE)

  utils::write.csv(
    latest_out,
    file.path(out_dir, paste0("usgs_gw_candidate_discovered_latest_", lb, "d.csv")),
    row.names = FALSE,
    na = ""
  )

  utils::write.csv(
    additions_out,
    file.path(out_dir, paste0("usgs_gw_candidate_additions_vs_current_", lb, "d.csv")),
    row.names = FALSE,
    na = ""
  )

  utils::write.csv(
    missing_out,
    file.path(out_dir, paste0("usgs_gw_candidate_current_missing_from_preview_", lb, "d.csv")),
    row.names = FALSE,
    na = ""
  )

  summary_rows[[as.character(lb)]] <- data.frame(
    run_time_utc = run_time,
    lookback_days = lb,
    parameter_code = parameter_code,
    bbox = paste(bbox, collapse = ","),
    query_start_date = start_date,
    query_end_date = end_date,
    raw_field_measurements_rows = nrow(fm),
    discovered_sites = length(discovered_sites),
    current_candidate_sites = length(current_sites),
    additions_vs_current = length(additions),
    current_missing_from_preview = length(missing_from_preview),
    latest_30d_count = pt_count_latest_age(latest, 30, end_date),
    latest_90d_count = pt_count_latest_age(latest, 90, end_date),
    latest_1y_count = pt_count_latest_age(latest, 365, end_date),
    latest_2y_count = pt_count_latest_age(latest, 730, end_date),
    latest_3y_count = pt_count_latest_age(latest, 1095, end_date),
    known_sites_found = paste(known_found, collapse = ";"),
    known_sites_missing = paste(known_missing, collapse = ";"),
    notes = "Preview only; production candidate CSV, history summary, GeoJSON, and BRIM HTML are not modified.",
    stringsAsFactors = FALSE
  )
}

summary_tbl <- do.call(rbind, summary_rows)
rownames(summary_tbl) <- NULL

summary_csv <- file.path(out_dir, "usgs_gw_candidate_discovery_preview_summary.csv")
summary_json <- file.path(out_dir, "usgs_gw_candidate_discovery_preview_summary.json")

utils::write.csv(summary_tbl, summary_csv, row.names = FALSE, na = "")
jsonlite::write_json(summary_tbl, summary_json, pretty = TRUE, dataframe = "rows", na = "null")

message("\nUSGS groundwater candidate discovery preview complete.")
message("Saved preview QA to: ", normalizePath(out_dir, winslash = "/", mustWork = FALSE))
print(summary_tbl)
