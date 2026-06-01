# ==== preview_usgs_groundwater_candidate_discovery.R =========================
#
# Weekly/manual preview for BRIM groundwater candidate discovery.
#
# This script is preview/QA only. It does not overwrite production candidate,
# history, GeoJSON, or map files.
# ============================================================================

options(
  cli.progress_show_after = Inf,
  cli.progress_clear = FALSE,
  cli.dynamic = FALSE
)

Sys.setenv(
  CLI_NO_PROGRESS = "true",
  R_CLI_NUM_COLORS = "1"
)

required_pkgs <- c("jsonlite")
missing_pkgs <- required_pkgs[!vapply(required_pkgs, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing_pkgs) > 0) {
  stop(
    "Missing required R package(s): ",
    paste(missing_pkgs, collapse = ", "),
    ". Install locally, or let the GitHub Action install them."
  )
}

# ---- Helpers ----------------------------------------------------------------

pt_parse_int_vec <- function(x, default) {
  x <- trimws(as.character(x))
  if (length(x) == 0 || is.na(x) || x == "") return(default)

  out <- suppressWarnings(as.integer(trimws(unlist(strsplit(x, ",", fixed = TRUE)))))
  out <- out[!is.na(out) & out > 0]

  if (length(out) == 0) default else unique(out)
}

pt_parse_bbox <- function(x, default) {
  x <- trimws(as.character(x))
  if (length(x) == 0 || is.na(x) || x == "") return(default)

  out <- suppressWarnings(as.numeric(trimws(unlist(strsplit(x, ",", fixed = TRUE)))))
  if (length(out) != 4 || any(is.na(out))) {
    warning("Invalid BRIM_GW_PREVIEW_BBOX; using default bbox.")
    return(default)
  }

  out
}

pt_site_no <- function(x) {
  x <- as.character(x)
  x <- trimws(x)
  x <- sub("^USGS-", "", x)
  x <- gsub("\\.0$", "", x)
  x <- gsub("[^0-9]", "", x)
  x[x == ""] <- NA_character_
  x
}

pt_csv_quote <- function(x) {
  x <- as.character(x)
  x[is.na(x)] <- ""
  needs_quote <- grepl("[,\"\n\r]", x)
  x <- gsub('"', '""', x, fixed = TRUE)
  x[needs_quote] <- paste0('"', x[needs_quote], '"')
  x
}

pt_write_csv <- function(x, path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)

  if (!is.data.frame(x)) x <- as.data.frame(x, stringsAsFactors = FALSE)

  con <- file(path, open = "w", encoding = "UTF-8")
  on.exit(close(con), add = TRUE)

  writeLines(paste(pt_csv_quote(names(x)), collapse = ","), con = con)

  if (nrow(x) > 0) {
    for (i in seq_len(nrow(x))) {
      writeLines(paste(pt_csv_quote(x[i, , drop = TRUE]), collapse = ","), con = con)
    }
  }

  invisible(path)
}

pt_urlencode <- function(x) {
  utils::URLencode(as.character(x), reserved = TRUE)
}

pt_curl_json <- function(url) {
  tf <- tempfile(fileext = ".json")
  on.exit(unlink(tf), add = TRUE)

  cmd <- c(
    "-fsSL",
    "--retry", "3",
    "--retry-delay", "5",
    "--connect-timeout", "30",
    "--max-time", "240",
    "-o", tf,
    url
  )

  status <- system2("curl", args = cmd)
  if (!identical(status, 0L)) {
    stop("curl request failed with status ", status, ": ", url)
  }

  jsonlite::fromJSON(tf, simplifyVector = TRUE)
}

pt_extract_field_measurements <- function(x) {
  if (is.null(x$features)) {
    return(data.frame())
  }

  features <- x$features

  if (is.data.frame(features) && "properties" %in% names(features)) {
    props <- features$properties
  } else if (is.list(features) && length(features) > 0) {
    props <- lapply(features, function(z) z$properties)
    props <- jsonlite::rbind_pages(props)
  } else {
    return(data.frame())
  }

  if (is.null(props) || nrow(props) == 0) {
    return(data.frame())
  }

  props <- as.data.frame(props, stringsAsFactors = FALSE)

  needed <- c(
    "monitoring_location_id",
    "parameter_code",
    "time",
    "value",
    "unit_of_measure",
    "qualifier",
    "approval_status",
    "measuring_agency"
  )

  for (nm in needed) {
    if (!nm %in% names(props)) props[[nm]] <- NA_character_
  }

  props[, needed, drop = FALSE]
}

pt_read_current_candidates <- function(path) {
  if (!file.exists(path)) {
    warning("Current candidate CSV not found: ", path)
    return(data.frame(site_no = character(), stringsAsFactors = FALSE))
  }

  x <- utils::read.csv(path, stringsAsFactors = FALSE, check.names = FALSE)

  if (!"site_no" %in% names(x)) {
    warning("Current candidate CSV lacks site_no column: ", path)
    return(data.frame(site_no = character(), stringsAsFactors = FALSE))
  }

  data.frame(
    site_no = unique(stats::na.omit(pt_site_no(x$site_no))),
    stringsAsFactors = FALSE
  )
}

# ---- Configuration ----------------------------------------------------------

DEFAULT_LOOKBACK_DAYS <- c(800L, 1826L)
DEFAULT_BBOX <- c(-124.6, 32.3, -113.8, 42.2)
DEFAULT_PARAMETER_CODE <- "72019"

lookback_days <- pt_parse_int_vec(
  Sys.getenv("BRIM_GW_PREVIEW_LOOKBACK_DAYS", unset = ""),
  default = DEFAULT_LOOKBACK_DAYS
)

bbox <- pt_parse_bbox(
  Sys.getenv("BRIM_GW_PREVIEW_BBOX", unset = ""),
  default = DEFAULT_BBOX
)

parameter_code <- Sys.getenv("BRIM_GW_PREVIEW_PARAMETER_CODE", unset = DEFAULT_PARAMETER_CODE)
parameter_code <- trimws(parameter_code)
if (is.na(parameter_code) || parameter_code == "") parameter_code <- DEFAULT_PARAMETER_CODE

run_time_utc <- format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ")
out_stamp <- format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y%m%d_%H%M%S")

out_root <- file.path("qa", "usgs_groundwater_candidate_discovery_preview")
out_dir <- file.path(out_root, out_stamp)
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

candidate_path <- file.path("data", "input", "usgs_groundwater_latest_index_ca.csv")
current_candidates <- pt_read_current_candidates(candidate_path)
current_sites <- current_candidates$site_no

known_sites <- c("362402116280901")

message("BRIM groundwater candidate-discovery preview")
message("Run time UTC: ", run_time_utc)
message("Lookbacks: ", paste(lookback_days, collapse = ", "))
message("Parameter code: ", parameter_code)
message("Bbox: ", paste(bbox, collapse = ","))
message("Current candidate sites: ", length(current_sites))
message("Output directory: ", normalizePath(out_dir, winslash = "/", mustWork = FALSE))

# ---- Preview loop -----------------------------------------------------------

summary_rows <- list()

for (lb in lookback_days) {

  query_start_date <- as.character(Sys.Date() - lb)
  query_end_date <- as.character(Sys.Date() + 1)
  time_window <- paste0(query_start_date, "/", query_end_date)

  base_url <- "https://api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/items"

  query <- paste0(
    "f=json",
    "&lang=en-US",
    "&skipGeometry=TRUE",
    "&bbox=", pt_urlencode(paste(bbox, collapse = ",")),
    "&properties=", pt_urlencode(paste(c(
      "monitoring_location_id",
      "parameter_code",
      "time",
      "value",
      "unit_of_measure",
      "qualifier",
      "approval_status",
      "measuring_agency"
    ), collapse = ",")),
    "&parameter_code=", pt_urlencode(parameter_code),
    "&time=", pt_urlencode(time_window),
    "&limit=50000"
  )

  url <- paste0(base_url, "?", query)

  message("\n--- Preview lookback: ", lb, " days ---")
  message("Query window: ", time_window)
  message("Requesting: ", url)

  json <- pt_curl_json(url)
  fm <- pt_extract_field_measurements(json)

  if (nrow(fm) == 0) {
    discovered_latest <- data.frame(
      site_no = character(),
      monitoring_location_id = character(),
      latest_time = character(),
      latest_value = numeric(),
      unit_of_measure = character(),
      qualifier = character(),
      approval_status = character(),
      measuring_agency = character(),
      stringsAsFactors = FALSE
    )
  } else {
    fm$site_no <- pt_site_no(fm$monitoring_location_id)
    fm$value_num <- suppressWarnings(as.numeric(fm$value))
    fm$time_chr <- as.character(fm$time)

    fm <- fm[!is.na(fm$site_no) & fm$site_no != "", , drop = FALSE]
    fm <- fm[order(fm$site_no, fm$time_chr, decreasing = c(FALSE, TRUE)), , drop = FALSE]
    fm_latest <- fm[!duplicated(fm$site_no), , drop = FALSE]

    discovered_latest <- data.frame(
      site_no = fm_latest$site_no,
      monitoring_location_id = fm_latest$monitoring_location_id,
      latest_time = fm_latest$time_chr,
      latest_value = fm_latest$value_num,
      unit_of_measure = as.character(fm_latest$unit_of_measure),
      qualifier = as.character(fm_latest$qualifier),
      approval_status = as.character(fm_latest$approval_status),
      measuring_agency = as.character(fm_latest$measuring_agency),
      stringsAsFactors = FALSE
    )

    discovered_latest <- discovered_latest[order(discovered_latest$site_no), , drop = FALSE]
  }

  discovered_sites <- discovered_latest$site_no

  additions <- discovered_latest[!discovered_latest$site_no %in% current_sites, , drop = FALSE]
  current_missing <- current_candidates[!current_candidates$site_no %in% discovered_sites, , drop = FALSE]

  latest_dates <- as.Date(substr(discovered_latest$latest_time, 1, 10))
  latest_age_days <- as.integer(Sys.Date() - latest_dates)

  discovered_latest$latest_date <- as.character(latest_dates)
  discovered_latest$latest_age_days <- latest_age_days

  additions <- discovered_latest[discovered_latest$site_no %in% additions$site_no, , drop = FALSE]

  known_found <- known_sites[known_sites %in% discovered_sites]
  known_missing <- known_sites[!known_sites %in% discovered_sites]

  notes <- c()
  if (nrow(fm) >= 50000) {
    notes <- c(notes, "Raw field-measurement row count reached 50000; inspect for possible API limit/truncation.")
  }
  if (length(notes) == 0) notes <- "Preview only; production candidate/history files were not modified."

  summary_rows[[as.character(lb)]] <- data.frame(
    run_time_utc = run_time_utc,
    lookback_days = lb,
    parameter_code = parameter_code,
    bbox = paste(bbox, collapse = ","),
    query_start_date = query_start_date,
    query_end_date = query_end_date,
    raw_field_measurements_rows = nrow(fm),
    discovered_sites = length(discovered_sites),
    current_candidate_sites = length(current_sites),
    additions_vs_current = nrow(additions),
    current_missing_from_preview = nrow(current_missing),
    latest_30d_count = sum(latest_age_days <= 30, na.rm = TRUE),
    latest_90d_count = sum(latest_age_days <= 90, na.rm = TRUE),
    latest_1y_count = sum(latest_age_days <= 365, na.rm = TRUE),
    latest_2y_count = sum(latest_age_days <= 730, na.rm = TRUE),
    latest_3y_count = sum(latest_age_days <= 1095, na.rm = TRUE),
    known_sites_found = paste(known_found, collapse = ";"),
    known_sites_missing = paste(known_missing, collapse = ";"),
    notes = paste(notes, collapse = " "),
    stringsAsFactors = FALSE
  )

  prefix <- paste0("usgs_gw_candidate_")
  suffix <- paste0("_", lb, "d.csv")

  pt_write_csv(
    discovered_latest,
    file.path(out_dir, paste0(prefix, "discovered_latest", suffix))
  )

  pt_write_csv(
    additions,
    file.path(out_dir, paste0(prefix, "additions_vs_current", suffix))
  )

  pt_write_csv(
    current_missing,
    file.path(out_dir, paste0(prefix, "current_missing_from_preview", suffix))
  )

  message("Raw field-measurement rows: ", nrow(fm))
  message("Discovered sites: ", length(discovered_sites))
  message("Additions vs current: ", nrow(additions))
  message("Current missing from preview: ", nrow(current_missing))
}

summary_tbl <- do.call(rbind, summary_rows)
row.names(summary_tbl) <- NULL

summary_csv <- file.path(out_dir, "usgs_gw_candidate_discovery_preview_summary.csv")
summary_json <- file.path(out_dir, "usgs_gw_candidate_discovery_preview_summary.json")

pt_write_csv(summary_tbl, summary_csv)
jsonlite::write_json(summary_tbl, summary_json, pretty = TRUE, auto_unbox = TRUE, na = "null")

message("\nUSGS groundwater candidate discovery preview complete.")
message("Saved preview QA to: ", normalizePath(out_dir, winslash = "/", mustWork = FALSE))
print(summary_tbl)
