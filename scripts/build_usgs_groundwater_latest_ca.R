# ==== build_usgs_groundwater_latest_ca.R =====================================
#
# PURPOSE:
#   Fetch latest/recent USGS California groundwater-level field measurements
#   for the BRIM Ops Live groundwater layer and write a small static GeoJSON
#   feed suitable for GitHub Pages.
#
# OUTPUTS:
#   docs/data/usgs_groundwater_latest_ca.geojson
#   docs/data/usgs_groundwater_latest_ca_summary.json
#
# DESIGN:
#   - The station/candidate index is committed to the live-data feed repo.
#   - Latest groundwater levels come from the modern USGS Water Data API
#     field-measurements endpoint through dataRetrieval.
#   - The feed uses parameter 72019, depth to water level in feet below land
#     surface / below ground surface, because that is the value BRIM needs for
#     quick screening popups.
#   - The input index remains the stable backbone for locations and well
#     construction/aquifer fields; API field measurements are refreshed daily.
#
# HOW TO RUN LOCALLY FROM THE FEED REPOSITORY:
#   Rscript scripts/build_usgs_groundwater_latest_ca.R
#
# REQUIRED INPUT:
#   data/input/usgs_groundwater_latest_index_ca.csv
#
# NOTES:
#   USGS groundwater field measurements are low-frequency, site-visit data and
#   may be provisional. They are appropriate for screening/situational awareness,
#   not for final hydrogeologic conclusions without review of well construction,
#   datum, aquifer/screen interval, and measurement history.
# ============================================================================

# ---- 1. Packages ------------------------------------------------------------

required_pkgs <- c(
  "dataRetrieval", "dplyr", "readr", "lubridate", "jsonlite", "tibble", "curl"
)

missing_pkgs <- required_pkgs[!vapply(required_pkgs, requireNamespace, logical(1), quietly = TRUE)]

if (length(missing_pkgs) > 0) {
  stop(
    "Missing required R packages: ", paste(missing_pkgs, collapse = ", "),
    "\nInstall them before running locally, or let the GitHub Action install them."
  )
}

required_dataretrieval_funs <- c("read_waterdata_field_measurements")

missing_dataretrieval_funs <- required_dataretrieval_funs[
  !vapply(required_dataretrieval_funs, exists, logical(1), where = asNamespace("dataRetrieval"), inherits = FALSE)
]

if (length(missing_dataretrieval_funs) > 0) {
  stop(
    "Installed dataRetrieval package is too old for RF027b. Missing function(s): ",
    paste(missing_dataretrieval_funs, collapse = ", "),
    "\nUpdate dataRetrieval from CRAN, then rerun."
  )
}

## Suppress package progress UI; this feed prints its own compact progress.
options(
  cli.progress_show_after = Inf,
  cli.progress_handlers = "none"
)

suppressPackageStartupMessages({
  library(dataRetrieval)
  library(dplyr)
  library(readr)
  library(lubridate)
  library(jsonlite)
  library(tibble)
  library(curl)
})

source("scripts/usgs_groundwater_diagnostics_helpers.R")

# ---- 2. Paths, constants, and switches -------------------------------------

station_index_csv <- Sys.getenv(
  "USGS_GW_STATION_INDEX_CSV",
  unset = "data/input/usgs_groundwater_latest_index_ca.csv"
)

## RF029:
##   Optional compact history-summary table produced by the local BRIM-side
##   groundwater history preprocessor. If this CSV is present in the live-feed
##   repo input folder, the daily latest feed joins it into the GeoJSON feature
##   properties. If it is absent, the latest-only feed still runs normally.
history_summary_csv <- Sys.getenv(
  "USGS_GW_HISTORY_SUMMARY_CSV",
  unset = "data/input/usgs_groundwater_history_summary_ca.csv"
)

out_geojson <- Sys.getenv(
  "USGS_GW_GEOJSON",
  unset = "docs/data/usgs_groundwater_latest_ca.geojson"
)

out_summary <- Sys.getenv(
  "USGS_GW_SUMMARY_JSON",
  unset = "docs/data/usgs_groundwater_latest_ca_summary.json"
)

gw_parameter_code <- Sys.getenv(
  "USGS_GW_PARAMETER_CODE",
  unset = "72019"
)

field_measurements_lookback_days <- suppressWarnings(as.integer(Sys.getenv(
  "USGS_GW_FIELD_MEASUREMENTS_LOOKBACK_DAYS",
  unset = "800"
)))
if (is.na(field_measurements_lookback_days) || field_measurements_lookback_days < 30) {
  field_measurements_lookback_days <- 800L
}

chunk_size <- suppressWarnings(as.integer(Sys.getenv(
  "USGS_GW_CHUNK_SIZE",
  unset = "60"
)))
if (is.na(chunk_size) || chunk_size < 1) chunk_size <- 60L

request_pause_sec <- suppressWarnings(as.numeric(Sys.getenv(
  "USGS_GW_REQUEST_PAUSE_SEC",
  unset = "0.20"
)))
if (is.na(request_pause_sec) || request_pause_sec < 0) request_pause_sec <- 0.20

request_max_attempts <- suppressWarnings(as.integer(Sys.getenv(
  "USGS_GW_REQUEST_MAX_ATTEMPTS",
  unset = "3"
)))
if (is.na(request_max_attempts) ||
    request_max_attempts < 1 ||
    request_max_attempts > 3) {
  request_max_attempts <- 3L
}

request_base_backoff_sec <- suppressWarnings(as.numeric(Sys.getenv(
  "USGS_GW_REQUEST_BASE_BACKOFF_SEC",
  unset = "1"
)))
if (is.na(request_base_backoff_sec) ||
    request_base_backoff_sec < 0 ||
    request_base_backoff_sec > 8) {
  request_base_backoff_sec <- 1
}

request_max_backoff_sec <- suppressWarnings(as.numeric(Sys.getenv(
  "USGS_GW_REQUEST_MAX_BACKOFF_SEC",
  unset = "8"
)))
if (is.na(request_max_backoff_sec) ||
    request_max_backoff_sec < request_base_backoff_sec ||
    request_max_backoff_sec > 8) {
  request_max_backoff_sec <- 8
}

request_max_retry_after_sec <- suppressWarnings(as.numeric(Sys.getenv(
  "USGS_GW_REQUEST_MAX_RETRY_AFTER_SEC",
  unset = "30"
)))
if (is.na(request_max_retry_after_sec) ||
    request_max_retry_after_sec < 0 ||
    request_max_retry_after_sec > 30) {
  request_max_retry_after_sec <- 30
}

request_jitter_sec <- suppressWarnings(as.numeric(Sys.getenv(
  "USGS_GW_REQUEST_JITTER_SEC",
  unset = "0.25"
)))
if (is.na(request_jitter_sec) ||
    request_jitter_sec < 0 ||
    request_jitter_sec > 0.25) {
  request_jitter_sec <- 0.25
}

max_consecutive_chunk_failures <- suppressWarnings(as.integer(Sys.getenv(
  "USGS_GW_MAX_CONSECUTIVE_CHUNK_FAILURES",
  unset = "3"
)))
if (is.na(max_consecutive_chunk_failures) ||
    max_consecutive_chunk_failures < 1 ||
    max_consecutive_chunk_failures > 3) {
  max_consecutive_chunk_failures <- 3L
}

min_api_sites_to_publish <- suppressWarnings(as.integer(Sys.getenv(
  "USGS_GW_MIN_API_SITES_TO_PUBLISH",
  unset = "300"
)))
if (is.na(min_api_sites_to_publish) || min_api_sites_to_publish < 1) {
  min_api_sites_to_publish <- 300L
}

min_features_to_publish <- suppressWarnings(as.integer(Sys.getenv(
  "USGS_GW_MIN_FEATURES_TO_PUBLISH",
  unset = "300"
)))
if (is.na(min_features_to_publish) || min_features_to_publish < 1) {
  min_features_to_publish <- 300L
}

allow_index_fallback <- tolower(Sys.getenv(
  "USGS_GW_ALLOW_INDEX_FALLBACK",
  unset = "true"
)) %in% c("true", "t", "1", "yes", "y")

feed_build_time <- Sys.time()
feed_build_time_utc <- format(lubridate::with_tz(feed_build_time, "UTC"), "%Y-%m-%dT%H:%M:%SZ")

run_date_utc <- as.Date(lubridate::with_tz(feed_build_time, "UTC"))
query_start_date <- as.character(run_date_utc - field_measurements_lookback_days)
query_end_date <- as.character(run_date_utc + 1)

# ---- 3. Small helpers -------------------------------------------------------

pt_chr <- function(x) {
  x <- as.character(x)
  x <- trimws(x)
  x[x == "" | is.na(x) | toupper(x) %in% c("NA", "NULL", "NAN")] <- NA_character_
  x
}

pt_site_no <- function(x) {
  x <- pt_chr(x)
  x <- gsub("\\.0$", "", x)
  x <- gsub("[^0-9]", "", x)
  x[nchar(x) == 0] <- NA_character_
  x
}

pt_num <- function(x) {
  suppressWarnings(as.numeric(as.character(x)))
}

pt_bool <- function(x) {
  tolower(trimws(as.character(x))) %in% c("true", "t", "1", "yes", "y")
}

pt_ensure_cols <- function(df, cols) {
  for (nm in cols) {
    if (!nm %in% names(df)) df[[nm]] <- NA_character_
  }
  df
}

pt_depth_class <- function(x) {
  x <- pt_num(x)

  dplyr::case_when(
    is.na(x) ~ NA_character_,
    x < 0 ~ "above land surface / flowing or anomalous",
    x <= 25 ~ "0-25 ft bgs",
    x <= 50 ~ "25-50 ft bgs",
    x <= 100 ~ "50-100 ft bgs",
    x <= 250 ~ "100-250 ft bgs",
    x <= 500 ~ "250-500 ft bgs",
    TRUE ~ ">500 ft bgs"
  )
}

pt_make_feature <- function(row) {
  props <- as.list(row)
  props$longitude <- NULL
  props$latitude <- NULL

  list(
    type = "Feature",
    geometry = list(
      type = "Point",
      coordinates = list(as.numeric(row$longitude), as.numeric(row$latitude))
    ),
    properties = props
  )
}

# ---- 4. Read groundwater candidate index -----------------------------------

if (!file.exists(station_index_csv)) {
  stop(
    "USGS groundwater station index CSV not found: ", station_index_csv,
    "\nRun source('02_preprocess/30_export_usgs_groundwater_live_inputs.R') from the BRIM project, ",
    "then commit data/input/usgs_groundwater_latest_index_ca.csv to the feed repo."
  )
}

station_index_raw <- readr::read_csv(
  station_index_csv,
  show_col_types = FALSE,
  col_types = readr::cols(.default = readr::col_character())
)

station_index_raw <- pt_ensure_cols(
  station_index_raw,
  c(
    "site_no", "station_nm", "latitude", "longitude", "status",
    "latest_wl_ft_bgs", "latest_wl_datetime_utc", "latest_wl_date",
    "latest_age_days", "well_depth_ft", "hole_depth_ft",
    "screen_top_ft", "screen_bottom_ft",
    "aqfr_cd", "aqfr_type_cd", "nat_aqfr_cd",
    "usgs_monitoring_location_url", "usgs_gw_levels_url",
    "latest_wl_status", "latest_wl_procedure", "latest_wl_qualifier",
    "latest_wl_units", "latest_wl_source",
    "on_blm_ca", "dist_to_blm_mi", "dist_to_blm_ft"
  )
)

station_index <- station_index_raw |>
  dplyr::transmute(
    site_no = pt_site_no(.data$site_no),
    station_nm = pt_chr(.data$station_nm),
    latitude = pt_num(.data$latitude),
    longitude = pt_num(.data$longitude),
    status = pt_chr(.data$status),

    index_latest_wl_ft_bgs = pt_num(.data$latest_wl_ft_bgs),
    index_latest_wl_datetime_utc = pt_chr(.data$latest_wl_datetime_utc),
    index_latest_wl_date = suppressWarnings(as.Date(.data$latest_wl_date)),
    index_latest_wl_status = pt_chr(.data$latest_wl_status),
    index_latest_wl_procedure = pt_chr(.data$latest_wl_procedure),
    index_latest_wl_qualifier = pt_chr(.data$latest_wl_qualifier),
    index_latest_wl_units = pt_chr(.data$latest_wl_units),
    index_latest_wl_source = pt_chr(.data$latest_wl_source),

    well_depth_ft = pt_num(.data$well_depth_ft),
    hole_depth_ft = pt_num(.data$hole_depth_ft),
    screen_top_ft = pt_num(.data$screen_top_ft),
    screen_bottom_ft = pt_num(.data$screen_bottom_ft),
    aqfr_cd = pt_chr(.data$aqfr_cd),
    aqfr_type_cd = pt_chr(.data$aqfr_type_cd),
    nat_aqfr_cd = pt_chr(.data$nat_aqfr_cd),

    on_blm_ca = pt_bool(.data$on_blm_ca),
    dist_to_blm_mi = pt_num(.data$dist_to_blm_mi),
    dist_to_blm_ft = pt_num(.data$dist_to_blm_ft),

    usgs_monitoring_location_url = dplyr::coalesce(
      pt_chr(.data$usgs_monitoring_location_url),
      paste0("https://waterdata.usgs.gov/monitoring-location/USGS-", pt_site_no(.data$site_no), "/")
    ),
    usgs_gw_levels_url = dplyr::coalesce(
      pt_chr(.data$usgs_gw_levels_url),
      paste0("https://waterdata.usgs.gov/monitoring-location/USGS-", pt_site_no(.data$site_no), "/all-graphs")
    )
  ) |>
  dplyr::filter(!is.na(.data$site_no), !is.na(.data$latitude), !is.na(.data$longitude)) |>
  dplyr::arrange(.data$site_no) |>
  dplyr::distinct(.data$site_no, .keep_all = TRUE)

if (nrow(station_index) < min_features_to_publish) {
  stop(
    "Groundwater station index has only ", nrow(station_index), " site(s), below minimum ",
    min_features_to_publish, ". Refusing to publish."
  )
}

message("USGS groundwater station index rows: ", nrow(station_index))

# ---- 5. Read optional RF029 groundwater history summary ---------------------

history_summary <- tibble::tibble(site_no = character())
history_summary_rows <- 0L
history_summary_joined_fields <- character(0)

if (file.exists(history_summary_csv)) {
  history_summary_raw <- readr::read_csv(
    history_summary_csv,
    show_col_types = FALSE,
    col_types = readr::cols(.default = readr::col_character())
  )

  if (!"site_no" %in% names(history_summary_raw)) {
    warning("Groundwater history summary CSV exists but is missing site_no: ", history_summary_csv)
  } else {
    history_summary <- history_summary_raw |>
      dplyr::mutate(site_no = pt_site_no(.data$site_no)) |>
      dplyr::filter(!is.na(.data$site_no)) |>
      dplyr::distinct(.data$site_no, .keep_all = TRUE)

    history_summary_rows <- nrow(history_summary)
    history_summary_joined_fields <- setdiff(names(history_summary), "site_no")
    message("USGS groundwater RF029 history summary rows read: ", history_summary_rows)
  }
} else {
  message("No optional RF029 groundwater history summary CSV found: ", history_summary_csv)
}

# ---- 6. Fetch latest groundwater field measurements ------------------------

message(
  "Fetching USGS groundwater field measurements in ",
  length(gw_chunks(station_index$site_no, chunk_size)),
  " chunk(s)."
)
message(
  "USGS field-measurements time interval: ",
  gw_time_interval(query_start_date, query_end_date)
)
message("USGS groundwater parameter code: ", gw_parameter_code)

retrieval_result <- gw_retrieve_chunks(
  site_ids = station_index$site_no,
  start_date = query_start_date,
  end_date = query_end_date,
  parameter_code = gw_parameter_code,
  chunk_size = chunk_size,
  request_pause_sec = request_pause_sec,
  max_consecutive_failures = max_consecutive_chunk_failures,
  primary_fetch_fun = dataRetrieval::read_waterdata_field_measurements,
  max_attempts = request_max_attempts,
  base_backoff_sec = request_base_backoff_sec,
  max_backoff_sec = request_max_backoff_sec,
  max_retry_after_sec = request_max_retry_after_sec,
  jitter_sec = request_jitter_sec
)

parsed_result <- gw_parse_latest(
  raw = retrieval_result$raw,
  parameter_code = gw_parameter_code,
  station_index_site_ids = station_index$site_no
)
retrieval_diagnostics <- gw_retrieval_summary(
  retrieval = retrieval_result,
  parser_accounting = parsed_result$accounting
)
gw_log_retrieval_summary(retrieval_diagnostics)
gw_enforce_publication(
  summary = retrieval_diagnostics,
  min_api_sites = min_api_sites_to_publish
)

api_latest <- parsed_result$latest |>
  dplyr::filter(.data$site_no %in% station_index$site_no)
api_latest_count <- nrow(api_latest)
fetch_result <- list(
  raw = parsed_result$raw,
  latest = api_latest,
  accounting = parsed_result$accounting,
  retrieval = retrieval_result,
  diagnostics = retrieval_diagnostics
)

message("USGS groundwater API latest site count: ", api_latest_count)

# ---- 7. Join and create feed table -----------------------------------------

latest_tbl <- station_index |>
  dplyr::left_join(api_latest, by = "site_no") |>
  dplyr::left_join(history_summary, by = "site_no") |>
  dplyr::mutate(
    has_api_latest_wl = !is.na(.data$api_latest_wl_ft_bgs),

    latest_wl_ft_bgs = dplyr::if_else(
      .data$has_api_latest_wl,
      .data$api_latest_wl_ft_bgs,
      if (allow_index_fallback) .data$index_latest_wl_ft_bgs else NA_real_
    ),
    latest_wl_datetime_utc = dplyr::if_else(
      .data$has_api_latest_wl,
      .data$api_latest_wl_datetime_utc,
      if (allow_index_fallback) .data$index_latest_wl_datetime_utc else NA_character_
    ),
    latest_wl_date = dplyr::if_else(
      .data$has_api_latest_wl,
      .data$api_latest_wl_date,
      if (allow_index_fallback) .data$index_latest_wl_date else as.Date(NA)
    ),
    latest_wl_status = dplyr::if_else(
      .data$has_api_latest_wl,
      .data$api_latest_wl_status,
      if (allow_index_fallback) .data$index_latest_wl_status else NA_character_
    ),
    latest_wl_procedure = dplyr::if_else(
      .data$has_api_latest_wl,
      .data$api_latest_wl_procedure,
      if (allow_index_fallback) .data$index_latest_wl_procedure else NA_character_
    ),
    latest_wl_qualifier = dplyr::if_else(
      .data$has_api_latest_wl,
      .data$api_latest_wl_qualifier,
      if (allow_index_fallback) .data$index_latest_wl_qualifier else NA_character_
    ),
    latest_wl_units = dplyr::if_else(
      .data$has_api_latest_wl,
      .data$api_latest_wl_units,
      if (allow_index_fallback) .data$index_latest_wl_units else NA_character_
    ),
    latest_wl_source = dplyr::if_else(
      .data$has_api_latest_wl,
      "USGS Water Data API field-measurements endpoint, parameter 72019",
      if (allow_index_fallback) paste0("Candidate-index fallback: ", dplyr::coalesce(.data$index_latest_wl_source, "prior BRIM export")) else NA_character_
    ),

    latest_age_days = as.numeric(run_date_utc - .data$latest_wl_date),
    latest_wl_depth_class = pt_depth_class(.data$latest_wl_ft_bgs),
    is_artesian_or_above_land_surface = !is.na(.data$latest_wl_ft_bgs) & .data$latest_wl_ft_bgs < 0,
    has_well_depth = !is.na(.data$well_depth_ft),
    has_hole_depth = !is.na(.data$hole_depth_ft),
    has_screen_interval = !is.na(.data$screen_top_ft) | !is.na(.data$screen_bottom_ft),
    has_aquifer_code = !is.na(.data$aqfr_cd) | !is.na(.data$aqfr_type_cd) | !is.na(.data$nat_aqfr_cd),

    latest_status = dplyr::case_when(
      is.na(.data$latest_wl_ft_bgs) ~ "no_recent_groundwater_level",
      !is.na(.data$latest_age_days) & .data$latest_age_days <= 90 ~ "latest_groundwater_level_90d",
      !is.na(.data$latest_age_days) & .data$latest_age_days <= 365 ~ "latest_groundwater_level_1y",
      !is.na(.data$latest_age_days) & .data$latest_age_days <= 365 * 2 ~ "latest_groundwater_level_2y",
      !is.na(.data$latest_age_days) & .data$latest_age_days <= field_measurements_lookback_days ~ "latest_groundwater_level_query_window",
      TRUE ~ "stale_or_index_fallback_groundwater_level"
    ),

    usgs_monitoring_location_url = paste0("https://waterdata.usgs.gov/monitoring-location/USGS-", .data$site_no, "/"),
    usgs_all_graphs_url = paste0("https://waterdata.usgs.gov/monitoring-location/USGS-", .data$site_no, "/all-graphs"),
    usgs_gw_levels_url = dplyr::coalesce(
      .data$usgs_gw_levels_url,
      .data$usgs_all_graphs_url
    ),

    feed_build_time_utc = feed_build_time_utc,
    feed_source = "USGS Water Data API field-measurements, parameter 72019, joined to BRIM groundwater candidate index",
    feed_scope = "California active/recent USGS groundwater candidates from BRIM static well index",
    field_measurements_query_start_date = query_start_date,
    field_measurements_query_end_date = query_end_date,
    field_measurements_lookback_days = field_measurements_lookback_days,
    data_quality_note = paste(
      "USGS groundwater field measurements are low-frequency site-visit measurements and may be provisional.",
      "Depth-to-water values use parameter 72019 when available and should be interpreted with well construction, datum, aquifer/screen interval, and measurement frequency."
    )
  ) |>
  dplyr::filter(!is.na(.data$latest_wl_ft_bgs)) |>
  dplyr::arrange(.data$site_no)

if (nrow(latest_tbl) < min_features_to_publish) {
  stop(
    "Groundwater output has only ", nrow(latest_tbl), " feature(s), below minimum ",
    min_features_to_publish, ". Refusing to publish."
  )
}

index_fallback_count <- sum(!latest_tbl$has_api_latest_wl, na.rm = TRUE)

message("USGS groundwater output feature count: ", nrow(latest_tbl))
message("USGS groundwater index-fallback feature count: ", index_fallback_count)

# ---- 8. Write GeoJSON and summary ------------------------------------------

features <- lapply(seq_len(nrow(latest_tbl)), function(i) pt_make_feature(latest_tbl[i, ]))

geojson <- list(
  type = "FeatureCollection",
  name = "USGS Groundwater Latest CA",
  metadata = list(
    feed_build_time_utc = feed_build_time_utc,
    station_index_csv = station_index_csv,
    history_summary_csv = ifelse(file.exists(history_summary_csv), history_summary_csv, NA_character_),
    history_summary_rows = history_summary_rows,
    scope = "CA",
    parameter_code = gw_parameter_code,
    retrieval_backend = "dataRetrieval::read_waterdata_field_measurements with direct OGC fallback",
    field_measurements_lookback_days = field_measurements_lookback_days,
    allow_index_fallback = allow_index_fallback
  ),
  features = features
)

summary <- list(
  feed_build_time_utc = feed_build_time_utc,
  scope = "CA",
  retrieval_backend = "dataRetrieval::read_waterdata_field_measurements with direct OGC fallback",
  parameter_code = gw_parameter_code,
  station_index_rows = nrow(station_index),
  history_summary_csv_found = file.exists(history_summary_csv),
  history_summary_rows = history_summary_rows,
  history_summary_joined_field_count = length(history_summary_joined_fields),
  history_sites_with_plot_json = if ("hist_plot_wy_mean_json" %in% names(latest_tbl)) sum(!is.na(latest_tbl$hist_plot_wy_mean_json) & latest_tbl$hist_plot_wy_mean_json != "", na.rm = TRUE) else 0L,
  history_sites_with_por_percentile = if ("hist_por_deeper_pctile" %in% names(latest_tbl)) sum(!is.na(latest_tbl$hist_por_deeper_pctile), na.rm = TRUE) else 0L,
  history_sites_with_seasonal_percentile = if ("hist_seasonal_deeper_pctile" %in% names(latest_tbl)) sum(!is.na(latest_tbl$hist_seasonal_deeper_pctile), na.rm = TRUE) else 0L,
  output_feature_count = nrow(latest_tbl),
  api_latest_site_count = api_latest_count,
  index_fallback_count = index_fallback_count,
  raw_field_measurements_rows = nrow(fetch_result$raw),
  field_measurements_query_start_date = query_start_date,
  field_measurements_query_end_date = query_end_date,
  field_measurements_lookback_days = field_measurements_lookback_days,
  latest_90d_count = sum(!is.na(latest_tbl$latest_age_days) & latest_tbl$latest_age_days <= 90, na.rm = TRUE),
  latest_1y_count = sum(!is.na(latest_tbl$latest_age_days) & latest_tbl$latest_age_days <= 365, na.rm = TRUE),
  latest_2y_count = sum(!is.na(latest_tbl$latest_age_days) & latest_tbl$latest_age_days <= 365 * 2, na.rm = TRUE),
  well_depth_count = sum(!is.na(latest_tbl$well_depth_ft), na.rm = TRUE),
  hole_depth_count = sum(!is.na(latest_tbl$hole_depth_ft), na.rm = TRUE),
  screen_interval_count = sum(latest_tbl$has_screen_interval, na.rm = TRUE),
  aquifer_code_count = sum(latest_tbl$has_aquifer_code, na.rm = TRUE),
  blm_distance_available_count = if ("dist_to_blm_mi" %in% names(latest_tbl)) sum(!is.na(latest_tbl$dist_to_blm_mi), na.rm = TRUE) else 0L,
  on_blm_ca_count = if ("on_blm_ca" %in% names(latest_tbl)) sum(latest_tbl$on_blm_ca %in% TRUE, na.rm = TRUE) else 0L,
  within_1mi_blm_count = if ("dist_to_blm_mi" %in% names(latest_tbl)) sum(!is.na(latest_tbl$dist_to_blm_mi) & latest_tbl$dist_to_blm_mi <= 1, na.rm = TRUE) else 0L,
  within_5mi_blm_count = if ("dist_to_blm_mi" %in% names(latest_tbl)) sum(!is.na(latest_tbl$dist_to_blm_mi) & latest_tbl$dist_to_blm_mi <= 5, na.rm = TRUE) else 0L,
  min_dist_to_blm_mi = if ("dist_to_blm_mi" %in% names(latest_tbl)) suppressWarnings(min(latest_tbl$dist_to_blm_mi, na.rm = TRUE)) else NA_real_,
  max_dist_to_blm_mi = if ("dist_to_blm_mi" %in% names(latest_tbl)) suppressWarnings(max(latest_tbl$dist_to_blm_mi, na.rm = TRUE)) else NA_real_,
  min_latest_wl_ft_bgs = suppressWarnings(min(latest_tbl$latest_wl_ft_bgs, na.rm = TRUE)),
  max_latest_wl_ft_bgs = suppressWarnings(max(latest_tbl$latest_wl_ft_bgs, na.rm = TRUE)),
  mean_latest_wl_ft_bgs = suppressWarnings(mean(latest_tbl$latest_wl_ft_bgs, na.rm = TRUE)),
  min_latest_age_days = suppressWarnings(min(latest_tbl$latest_age_days, na.rm = TRUE)),
  max_latest_age_days = suppressWarnings(max(latest_tbl$latest_age_days, na.rm = TRUE)),
  allow_index_fallback = allow_index_fallback,
  notes = c(
    "Latest groundwater levels are from USGS Water Data API field-measurements parameter 72019 where available.",
    "The BRIM input index provides stable site locations and well construction/aquifer fields.",
    "Index fallback is used only for sites without an API result in the query window when enabled; fallback rows are flagged with has_api_latest_wl = false.",
    "Screen/perforation interval fields are preserved if available in the input, but the current public USGS API/index fields may not provide populated screen interval fields.",
    "USGS groundwater field measurements are low-frequency and may be provisional; interpret values with well construction, datum, aquifer/screen interval, and measurement history.",
    "If RF029 history summary CSV is present, compact water-year history and percentile fields are joined into the hosted GeoJSON for popup/mini-plot use."
  )
)

if (!is.finite(summary$min_dist_to_blm_mi)) summary$min_dist_to_blm_mi <- NA_real_
if (!is.finite(summary$max_dist_to_blm_mi)) summary$max_dist_to_blm_mi <- NA_real_
if (!is.finite(summary$min_latest_wl_ft_bgs)) summary$min_latest_wl_ft_bgs <- NA_real_
if (!is.finite(summary$max_latest_wl_ft_bgs)) summary$max_latest_wl_ft_bgs <- NA_real_
if (!is.finite(summary$mean_latest_wl_ft_bgs)) summary$mean_latest_wl_ft_bgs <- NA_real_
if (!is.finite(summary$min_latest_age_days)) summary$min_latest_age_days <- NA_real_
if (!is.finite(summary$max_latest_age_days)) summary$max_latest_age_days <- NA_real_

gw_write_staged_outputs(
  geojson = geojson,
  summary = summary,
  out_geojson = out_geojson,
  out_summary = out_summary,
  min_features = min_features_to_publish,
  min_api_sites = min_api_sites_to_publish
)

message("Saved GeoJSON: ", out_geojson)
message("Saved summary: ", out_summary)
message("USGS groundwater live feed complete.")
print(summary)
