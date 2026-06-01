# ==== preview_usgs_groundwater_candidate_discovery.R =========================
##
## PURPOSE:
##   Preview which USGS groundwater-level monitoring locations would enter or
##   leave BRIM's Ops Live groundwater candidate universe under one or more
##   recent-measurement lookback windows.
##
## DESIGN:
##   Preview/QA only. This script does NOT overwrite the production candidate
##   CSV, history CSV, GeoJSON feed, or BRIM map outputs.
##
## DEPENDENCIES:
##   Keep this workflow intentionally lean for GitHub Actions:
##     - base R
##     - jsonlite
##     - system curl binary when available, with utils::download.file fallback
##
##   IMPORTANT:
##     System curl is called through system2() with an argument vector and
##     --url, not through a pasted shell command. Do not manually shell-quote
##     the URL; system2() handles argument passing. This avoids the common
##     GitHub Actions failure mode where query-string ampersands are parsed by
##     the shell and the request silently becomes the unfiltered endpoint.
##
## OUTPUT:
##   qa/usgs_groundwater_candidate_discovery_preview/<timestamp>/
##
## ============================================================================

# ---- 0. Lean runtime configuration -----------------------------------------

options(
  cli.progress_show_after = Inf,
  cli.progress_clear = FALSE,
  cli.dynamic = FALSE,
  timeout = max(600, getOption("timeout", 60))
)

Sys.setenv(
  CLI_NO_PROGRESS = "true",
  R_CLI_NUM_COLORS = "1"
)

required_pkgs <- c("jsonlite")
missing_pkgs <- required_pkgs[!vapply(required_pkgs, requireNamespace, logical(1), quietly = TRUE)]

if (length(missing_pkgs) > 0) {
  stop(
    "Missing required R packages: ", paste(missing_pkgs, collapse = ", "),
    "\nInstall them locally or let the GitHub Action install them."
  )
}

# ---- 1. Helpers -------------------------------------------------------------

pt_now_stamp <- function() {
  format(Sys.time(), "%Y%m%d_%H%M%S", tz = "UTC")
}

pt_now_iso <- function() {
  format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
}

pt_parse_int_vec <- function(x, default, min_value = 1L, max_value = 3660L) {
  x <- trimws(as.character(x))
  if (length(x) == 0 || is.na(x) || x == "") return(default)

  vals <- suppressWarnings(as.integer(trimws(strsplit(x, ",", fixed = TRUE)[[1]])))
  vals <- vals[!is.na(vals)]

  if (length(vals) == 0) {
    warning("Lookback override did not contain any valid integers; using defaults: ", paste(default, collapse = ","))
    return(default)
  }

  bad <- vals < min_value | vals > max_value
  if (any(bad)) {
    stop(
      "Invalid BRIM_GW_PREVIEW_LOOKBACK_DAYS value(s): ",
      paste(vals[bad], collapse = ","),
      ". Allowed range is ", min_value, " to ", max_value, " days."
    )
  }

  sort(unique(vals))
}

pt_validate_bbox <- function(x) {
  x <- suppressWarnings(as.numeric(x))

  if (length(x) != 4 || any(is.na(x))) {
    stop("Bbox must contain exactly four numeric values: xmin,ymin,xmax,ymax")
  }

  names(x) <- c("xmin", "ymin", "xmax", "ymax")

  if (!(x[["xmin"]] < x[["xmax"]] && x[["ymin"]] < x[["ymax"]])) {
    stop("Invalid bbox order; expected xmin < xmax and ymin < ymax.")
  }

  if (x[["xmin"]] < -180 || x[["xmax"]] > 180 || x[["ymin"]] < -90 || x[["ymax"]] > 90) {
    stop("Bbox values are outside valid longitude/latitude ranges.")
  }

  ## BRIM-specific guardrail. This workflow is intentionally a California +
  ## nearby-border-context preview, not a general USGS scraper. The generous
  ## allowed envelope leaves room for the current Upper Amargosa / western
  ## context while preventing accidental CONUS/global requests.
  allowed <- c(xmin = -126.5, ymin = 31.0, xmax = -112.0, ymax = 43.5)
  if (x[["xmin"]] < allowed[["xmin"]] || x[["xmax"]] > allowed[["xmax"]] ||
      x[["ymin"]] < allowed[["ymin"]] || x[["ymax"]] > allowed[["ymax"]]) {
    stop(
      "Bbox is outside the allowed BRIM western preview envelope. ",
      "Provided: ", paste(x, collapse = ","),
      "; allowed envelope: ", paste(allowed, collapse = ",")
    )
  }

  unname(x)
}

pt_parse_num_vec <- function(x, default) {
  x <- trimws(as.character(x))
  if (length(x) == 0 || is.na(x) || x == "") return(pt_validate_bbox(default))
  vals <- suppressWarnings(as.numeric(trimws(strsplit(x, ",", fixed = TRUE)[[1]])))
  pt_validate_bbox(vals)
}

pt_norm_site_no <- function(x) {
  x <- as.character(x)
  x <- trimws(x)
  x <- sub("^USGS-", "", x, ignore.case = TRUE)
  x <- gsub("\\.0$", "", x)
  x <- gsub("[^0-9]", "", x)
  x[x == ""] <- NA_character_
  x
}

pt_repo_root <- function() {
  ## GitHub Actions starts in the repo root. Locally, users sometimes source
  ## this file from the parent PortaTreasure2 directory. If so, move into the
  ## live-feed repo folder. This keeps Ctrl-A/source runs from scattering QA
  ## output into the wrong folder.
  if (file.exists("data/input/usgs_groundwater_latest_index_ca.csv") && dir.exists("scripts")) {
    return(normalizePath(getwd(), winslash = "/", mustWork = TRUE))
  }

  if (dir.exists("brim-live-data-feeds") &&
      file.exists(file.path("brim-live-data-feeds", "data/input/usgs_groundwater_latest_index_ca.csv"))) {
    setwd("brim-live-data-feeds")
    return(normalizePath(getwd(), winslash = "/", mustWork = TRUE))
  }

  normalizePath(getwd(), winslash = "/", mustWork = TRUE)
}

pt_urlencode <- function(x) {
  utils::URLencode(as.character(x), reserved = TRUE)
}

pt_build_url <- function(base_url, params) {
  keys <- names(params)
  query <- paste0(pt_urlencode(keys), "=", vapply(params, pt_urlencode, character(1)))
  paste0(base_url, "?", paste(query, collapse = "&"))
}

pt_validate_request_url <- function(url, lookback_day_count) {
  needed <- c(
    "collections/field-measurements/items",
    "parameter_code=72019",
    "bbox=",
    "time=",
    "limit=50000"
  )

  missing <- needed[!vapply(needed, grepl, logical(1), x = url, fixed = TRUE)]
  if (length(missing) > 0) {
    stop(
      "Refusing to issue malformed USGS preview request for lookback ",
      lookback_day_count, " day(s). Missing URL component(s): ",
      paste(missing, collapse = ", "),
      "\nURL: ", url
    )
  }

  invisible(TRUE)
}

pt_have_system_curl <- function() {
  nzchar(Sys.which("curl"))
}

pt_read_first_text <- function(path, n = 500L) {
  if (!file.exists(path) || is.na(file.info(path)$size) || file.info(path)$size <= 0) {
    return("")
  }
  con <- file(path, open = "rb")
  on.exit(close(con), add = TRUE)
  raw <- readBin(con, what = "raw", n = min(n, file.info(path)$size))
  rawToChar(raw, multiple = FALSE)
}

pt_download_json_file <- function(url, label = "USGS API request", max_attempts = 3L) {
  last_msg <- character(0)

  for (attempt in seq_len(max_attempts)) {
    tmp <- tempfile(fileext = ".json")

    message("Requesting: ", url)
    message("Attempt ", attempt, " of ", max_attempts)

    if (pt_have_system_curl()) {
      args <- c(
        "--fail",
        "--silent",
        "--show-error",
        "--location",
        "--globoff",
        "--retry", "3",
        "--retry-delay", "5",
        "--connect-timeout", "30",
        "--max-time", "300",
        "--output", tmp,
        "--url", url
      )

      res <- system2("curl", args = args, stdout = TRUE, stderr = TRUE)
      status <- attr(res, "status")
      if (is.null(status)) status <- 0L
      last_msg <- c(last_msg, res)

    } else {
      status <- tryCatch({
        utils::download.file(url, destfile = tmp, mode = "wb", quiet = TRUE)
        0L
      }, error = function(e) {
        last_msg <<- c(last_msg, conditionMessage(e))
        1L
      })
    }

    size <- if (file.exists(tmp)) file.info(tmp)$size else 0
    first <- pt_read_first_text(tmp, n = 500L)
    first_trim <- sub("^[[:space:]]+", "", first)

    if (identical(status, 0L) && is.finite(size) && size > 0 && grepl("^[{\\[]", first_trim)) {
      return(tmp)
    }

    msg <- paste(
      label, "did not return valid non-empty JSON.",
      "curl/download status:", status,
      "file size:", size,
      "first bytes:", substr(gsub("[\r\n\t]+", " ", first), 1, 300)
    )
    warning(msg)

    if (attempt < max_attempts) Sys.sleep(5 * attempt)
  }

  stop(
    label, " failed after ", max_attempts, " attempt(s).\n",
    "Last curl/download messages:\n",
    paste(utils::tail(last_msg, 20), collapse = "\n")
  )
}

pt_from_json_file <- function(path, label = "JSON") {
  tryCatch(
    jsonlite::fromJSON(path, flatten = TRUE),
    error = function(e) {
      first <- pt_read_first_text(path, n = 1000L)
      stop(
        "Could not parse ", label, " as JSON: ", conditionMessage(e),
        "\nFile: ", path,
        "\nFile size: ", ifelse(file.exists(path), file.info(path)$size, NA),
        "\nFirst bytes:\n", substr(first, 1, 1000)
      )
    }
  )
}

pt_col <- function(x, candidates, default = NA_character_) {
  for (nm in candidates) {
    if (nm %in% names(x)) return(x[[nm]])
  }
  rep(default, NROW(x))
}

pt_write_csv <- function(x, path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  utils::write.csv(x, path, row.names = FALSE, na = "")
}

# ---- 2. Configuration -------------------------------------------------------

repo_root <- pt_repo_root()

DEFAULT_LOOKBACK_DAYS <- c(800L, 1826L)
DEFAULT_BBOX <- c(-124.6, 32.3, -113.8, 42.2)
DEFAULT_PARAMETER_CODE <- "72019"
DEFAULT_KNOWN_SITES <- c(
  "362402116280901",  # Inyo-BLM 1 example from BRIM testing
  "362402116280902"   # Inyo-BLM 1a companion example
)

lookback_days <- pt_parse_int_vec(
  Sys.getenv("BRIM_GW_PREVIEW_LOOKBACK_DAYS", unset = ""),
  default = DEFAULT_LOOKBACK_DAYS
)

bbox <- pt_parse_num_vec(
  Sys.getenv("BRIM_GW_PREVIEW_BBOX", unset = ""),
  default = DEFAULT_BBOX
)

parameter_code <- Sys.getenv(
  "BRIM_GW_PREVIEW_PARAMETER_CODE",
  unset = DEFAULT_PARAMETER_CODE
)
parameter_code <- trimws(parameter_code)
if (is.na(parameter_code) || parameter_code == "") parameter_code <- DEFAULT_PARAMETER_CODE

known_sites <- Sys.getenv("BRIM_GW_PREVIEW_KNOWN_SITES", unset = "")
known_sites <- if (nzchar(known_sites)) {
  pt_norm_site_no(strsplit(known_sites, ",", fixed = TRUE)[[1]])
} else {
  DEFAULT_KNOWN_SITES
}
known_sites <- known_sites[!is.na(known_sites)]

run_time_utc <- pt_now_iso()
out_dir <- file.path(
  "qa",
  "usgs_groundwater_candidate_discovery_preview",
  pt_now_stamp()
)
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

# Write an early diagnostic so artifact upload has something useful even if an
# API request fails midway.
writeLines(
  c(
    paste0("run_time_utc=", run_time_utc),
    paste0("repo_root=", repo_root),
    paste0("lookback_days=", paste(lookback_days, collapse = ",")),
    paste0("parameter_code=", parameter_code),
    paste0("bbox=", paste(bbox, collapse = ",")),
    paste0("script_dependency_mode=base R + jsonlite + system2 curl --url")
  ),
  con = file.path(out_dir, "run_diagnostics.txt")
)

current_candidate_path <- file.path("data", "input", "usgs_groundwater_latest_index_ca.csv")
if (!file.exists(current_candidate_path)) {
  stop("Current candidate CSV not found: ", current_candidate_path)
}

current_candidates <- utils::read.csv(
  current_candidate_path,
  stringsAsFactors = FALSE,
  colClasses = "character"
)

if (!"site_no" %in% names(current_candidates)) {
  stop("Current candidate CSV does not contain required column: site_no")
}

current_sites <- unique(pt_norm_site_no(current_candidates$site_no))
current_sites <- current_sites[!is.na(current_sites)]

message("BRIM groundwater candidate-discovery preview")
message("Run time UTC: ", run_time_utc)
message("Lookbacks: ", paste(lookback_days, collapse = ", "))
message("Parameter code: ", parameter_code)
message("Request mode: system2 curl --url with validated request parameters; server-side filters verified after download")
message("Bbox: ", paste(bbox, collapse = ","))
message("Current candidate sites: ", length(current_sites))
message("Output directory: ", normalizePath(out_dir, winslash = "/", mustWork = FALSE))

# ---- 3. USGS API query ------------------------------------------------------

pt_fetch_field_measurements <- function(lookback_day_count) {
  query_start <- Sys.Date() - as.integer(lookback_day_count)
  query_end <- Sys.Date() + 1L

  message("\n--- Preview lookback: ", lookback_day_count, " days ---")
  message("Query window: ", query_start, "/", query_end)

  base_url <- "https://api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/items"

  props <- c(
    "monitoring_location_id",
    "parameter_code",
    "time",
    "value",
    "unit_of_measure",
    "qualifier",
    "approval_status",
    "measuring_agency"
  )

  url <- pt_build_url(
    base_url,
    list(
      f = "json",
      lang = "en-US",
      skipGeometry = "TRUE",
      bbox = paste(bbox, collapse = ","),
      properties = paste(props, collapse = ","),
      parameter_code = parameter_code,
      time = paste0(query_start, "/", query_end),
      limit = "50000"
    )
  )

  pt_validate_request_url(url, lookback_day_count)

  writeLines(
    url,
    con = file.path(out_dir, paste0("request_url_", lookback_day_count, "d.txt"))
  )

  json_path <- pt_download_json_file(
    url,
    label = paste0("USGS field-measurements lookback ", lookback_day_count, "d")
  )

  j <- pt_from_json_file(json_path, label = paste0("USGS field-measurements lookback ", lookback_day_count, "d"))

  features <- j$features
  if (is.null(features) || NROW(features) == 0) {
    return(data.frame(
      site_no = character(),
      monitoring_location_id = character(),
      measurement_time_utc = character(),
      value = numeric(),
      unit_of_measure = character(),
      qualifier = character(),
      approval_status = character(),
      measuring_agency = character(),
      stringsAsFactors = FALSE
    ))
  }

  if (!is.data.frame(features)) {
    features <- as.data.frame(features, stringsAsFactors = FALSE)
  }

  monitoring_location_id <- as.character(pt_col(
    features,
    c("properties.monitoring_location_id", "monitoring_location_id")
  ))

  measurement_time_utc <- as.character(pt_col(
    features,
    c("properties.time", "time")
  ))

  parameter_code_returned <- as.character(pt_col(
    features,
    c("properties.parameter_code", "parameter_code")
  ))

  value <- suppressWarnings(as.numeric(pt_col(
    features,
    c("properties.value", "value"),
    default = NA_character_
  )))

  out <- data.frame(
    site_no = pt_norm_site_no(monitoring_location_id),
    monitoring_location_id = monitoring_location_id,
    parameter_code = parameter_code_returned,
    measurement_time_utc = measurement_time_utc,
    value = value,
    unit_of_measure = as.character(pt_col(features, c("properties.unit_of_measure", "unit_of_measure"))),
    qualifier = as.character(pt_col(features, c("properties.qualifier", "qualifier"))),
    approval_status = as.character(pt_col(features, c("properties.approval_status", "approval_status"))),
    measuring_agency = as.character(pt_col(features, c("properties.measuring_agency", "measuring_agency"))),
    stringsAsFactors = FALSE
  )

  out <- out[!is.na(out$site_no), , drop = FALSE]

  ## Defensive validation/filtering. The USGS API should apply parameter and
  ## time filters server-side. These checks keep a shell-quoting or request
  ## regression from silently producing misleading preview QA.
  out_time <- suppressWarnings(as.POSIXct(out$measurement_time_utc, tz = "UTC"))
  query_start_time <- as.POSIXct(query_start, tz = "UTC")
  query_end_time <- as.POSIXct(query_end + 1L, tz = "UTC")

  if ("parameter_code" %in% names(out)) {
    bad_param <- !is.na(out$parameter_code) & out$parameter_code != parameter_code
    if (any(bad_param, na.rm = TRUE)) {
      warning(
        "USGS response included ", sum(bad_param, na.rm = TRUE),
        " row(s) with parameter_code other than ", parameter_code,
        "; dropping them from preview QA."
      )
    }
    out <- out[is.na(out$parameter_code) | out$parameter_code == parameter_code, , drop = FALSE]
    out_time <- suppressWarnings(as.POSIXct(out$measurement_time_utc, tz = "UTC"))
  }

  in_window <- !is.na(out_time) & out_time >= query_start_time & out_time < query_end_time
  if (any(!in_window, na.rm = TRUE)) {
    warning(
      "USGS response included ", sum(!in_window, na.rm = TRUE),
      " row(s) outside requested time window; dropping them from preview QA."
    )
  }
  out <- out[in_window, , drop = FALSE]

  if (NROW(features) <= 10 && nrow(out) <= 10) {
    warning(
      "USGS response returned only ", NROW(features),
      " raw feature(s). For the default BRIM groundwater preview this is suspicious; ",
      "check the request URL and query-string handling if counts look wrong."
    )
  }

  if (NROW(features) >= 50000 || nrow(out) >= 50000) {
    stop(
      "USGS response hit or approached the 50,000-feature request limit. ",
      "Split the preview query before trusting counts. Raw features: ",
      NROW(features), "; filtered rows: ", nrow(out)
    )
  }

  out
}

pt_latest_by_site <- function(x) {
  if (nrow(x) == 0) return(x)
  t <- suppressWarnings(as.POSIXct(x$measurement_time_utc, tz = "UTC"))
  t_num <- as.numeric(t)
  t_num[is.na(t_num)] <- -Inf
  ord <- order(x$site_no, -t_num)
  x <- x[ord, , drop = FALSE]
  x[!duplicated(x$site_no), , drop = FALSE]
}

summary_list <- list()

for (lb in lookback_days) {
  fm <- pt_fetch_field_measurements(lb)
  latest <- pt_latest_by_site(fm)

  discovered_sites <- unique(latest$site_no)
  discovered_sites <- discovered_sites[!is.na(discovered_sites)]

  additions <- sort(setdiff(discovered_sites, current_sites))
  missing_from_preview <- sort(setdiff(current_sites, discovered_sites))

  latest_time <- suppressWarnings(as.POSIXct(latest$measurement_time_utc, tz = "UTC"))
  latest_date <- as.Date(latest_time)
  latest_age_days <- as.numeric(Sys.Date() - latest_date)

  latest$latest_date <- as.character(latest_date)
  latest$latest_age_days <- latest_age_days

  latest_out <- latest[order(latest$site_no), , drop = FALSE]
  additions_out <- latest_out[latest_out$site_no %in% additions, , drop = FALSE]
  missing_out <- data.frame(site_no = missing_from_preview, stringsAsFactors = FALSE)

  suffix <- paste0(lb, "d")
  pt_write_csv(latest_out, file.path(out_dir, paste0("usgs_gw_candidate_discovered_latest_", suffix, ".csv")))
  pt_write_csv(additions_out, file.path(out_dir, paste0("usgs_gw_candidate_additions_vs_current_", suffix, ".csv")))
  pt_write_csv(missing_out, file.path(out_dir, paste0("usgs_gw_candidate_current_missing_from_preview_", suffix, ".csv")))

  known_found <- sort(intersect(known_sites, discovered_sites))
  known_missing <- sort(setdiff(known_sites, discovered_sites))

  summary_list[[as.character(lb)]] <- data.frame(
    run_time_utc = run_time_utc,
    lookback_days = as.integer(lb),
    parameter_code = parameter_code,
    bbox = paste(bbox, collapse = ","),
    query_start_date = as.character(Sys.Date() - as.integer(lb)),
    query_end_date = as.character(Sys.Date() + 1L),
    raw_field_measurements_rows = nrow(fm),
    discovered_sites = length(discovered_sites),
    current_candidate_sites = length(current_sites),
    additions_vs_current = length(additions),
    current_missing_from_preview = length(missing_from_preview),
    latest_30d_count = sum(latest_age_days <= 30, na.rm = TRUE),
    latest_90d_count = sum(latest_age_days <= 90, na.rm = TRUE),
    latest_1y_count = sum(latest_age_days <= 365, na.rm = TRUE),
    latest_2y_count = sum(latest_age_days <= 730, na.rm = TRUE),
    latest_3y_count = sum(latest_age_days <= 1095, na.rm = TRUE),
    known_sites_found = paste(known_found, collapse = ";"),
    known_sites_missing = paste(known_missing, collapse = ";"),
    notes = "Preview only; production candidate/history/GeoJSON files were not modified.",
    stringsAsFactors = FALSE
  )

  message(
    "Lookback ", lb, "d: raw rows=", nrow(fm),
    "; discovered sites=", length(discovered_sites),
    "; additions vs current=", length(additions),
    "; current missing from preview=", length(missing_from_preview)
  )
}

summary_tbl <- do.call(rbind, summary_list)
row.names(summary_tbl) <- NULL

summary_csv <- file.path(out_dir, "usgs_gw_candidate_discovery_preview_summary.csv")
summary_json <- file.path(out_dir, "usgs_gw_candidate_discovery_preview_summary.json")

pt_write_csv(summary_tbl, summary_csv)
jsonlite::write_json(summary_tbl, summary_json, pretty = TRUE, auto_unbox = TRUE, na = "null")

message("\nUSGS groundwater candidate discovery preview complete.")
message("Saved preview QA to: ", normalizePath(out_dir, winslash = "/", mustWork = FALSE))
print(summary_tbl)
