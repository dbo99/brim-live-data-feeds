# ==== build_scan_soil_moisture_latest.R =====================================
#
# RF070:
#   - Harden GitHub/NWCC fetch reliability: require healthy HTTP preflight
#     status codes, add a small known-station SMS canary fetch before the full
#     station loop, and abort early when the first main requests are all empty.
#
# RF064:
#   - Add NRCS endpoint preflight, longer fetch timeout, and per-site/year
#     retry diagnostics so GitHub Actions fail fast when wcc.sc.egov.usda.gov
#     is unreachable, while preserving the previous good feed files.
#
# PURPOSE:
#   Fetch current-water-year USDA NRCS SCAN soil-moisture observations and
#   write small static feed files for the BRIM Ops Live SCAN layer.
#
# OUTPUTS:
#   docs/data/scan_soil_moisture_latest.geojson
#   docs/data/scan_soil_moisture_latest_summary.json
#   docs/data/scan_soil_moisture_current_wy_trace.csv
#   docs/data/scan_soil_moisture_current_wy_trace_summary.json
#   docs/data/scan_depth_style.csv
#
# DESIGN:
#   - The station index is exported from the main BRIM project and committed to
#     the live-data feed repo.
#   - Latest/current-water-year soil moisture values are fetched with
#     soilDB::fetchSCAN().
#   - Soil depths are displayed in inches for the BRIM audience.
#   - The feed preserves all available latest depths per station and selects one
#     display depth for map symbology and compact hover text.
#   - A compact current-water-year trace is also written for later popup plots.
#   - A small depth-style table is used as the standard cross-product color
#     scheme for 2/4/8/20/40 inch traces, labels, and future plot legends.
#   - Optional historical water-day percentile context is read from:
#       data/input/scan_sms_waterday_percentiles.csv
#     when present. That file is built locally by the heavier BRIM preprocessor,
#     not by this daily GitHub Action.
#
# HOW TO RUN LOCALLY FROM THE FEED REPOSITORY:
#   Rscript scripts/build_scan_soil_moisture_latest.R
#
# REQUIRED INPUT:
#   data/input/scan_station_index.csv
#
# OPTIONAL INPUT:
#   data/input/scan_sms_waterday_percentiles.csv
#   data/input/scan_depth_style.csv
#
# NOTES:
#   SCAN observations are suitable for screening and situational awareness.
#   Values are provisional and may be delayed, revised, or unavailable when a
#   station/sensor is offline. BRIM should display observation age/staleness.
# ============================================================================

# ---- 1. Packages ------------------------------------------------------------

## RF061:
##   soilDB::fetchSCAN() uses httr at runtime on GitHub Actions, but httr may
##   not be installed as a hard dependency by setup-r-dependencies.  List httr
##   explicitly so the Action fails early if it is missing instead of retrying
##   every station/year fetch with "please install the `httr` package".

required_pkgs <- c(
  "soilDB", "httr", "dplyr", "readr", "lubridate", "jsonlite", "tibble", "purrr", "stringr", "tidyr"
)

missing_pkgs <- required_pkgs[!vapply(required_pkgs, requireNamespace, logical(1), quietly = TRUE)]

if (length(missing_pkgs) > 0) {
  stop(
    "Missing required R packages: ", paste(missing_pkgs, collapse = ", "),
    "\nInstall them before running locally, or let the GitHub Action install them."
  )
}

## Suppress package progress UI; this feed prints its own compact progress.
options(
  cli.progress_show_after = Inf,
  cli.progress_handlers = "none"
)

suppressPackageStartupMessages({
  library(soilDB)
  library(httr)
  library(dplyr)
  library(readr)
  library(lubridate)
  library(jsonlite)
  library(tibble)
  library(purrr)
  library(stringr)
  library(tidyr)
})

# ---- 2. Paths, constants, and switches -------------------------------------

station_index_csv <- Sys.getenv(
  "SCAN_STATION_INDEX_CSV",
  unset = "data/input/scan_station_index.csv"
)

percentiles_csv <- Sys.getenv(
  "SCAN_SMS_PERCENTILES_CSV",
  unset = "data/input/scan_sms_waterday_percentiles.csv"
)

depth_style_csv <- Sys.getenv(
  "SCAN_DEPTH_STYLE_CSV",
  unset = "data/input/scan_depth_style.csv"
)

out_geojson <- Sys.getenv(
  "SCAN_SMS_GEOJSON",
  unset = "docs/data/scan_soil_moisture_latest.geojson"
)

out_summary <- Sys.getenv(
  "SCAN_SMS_SUMMARY_JSON",
  unset = "docs/data/scan_soil_moisture_latest_summary.json"
)

out_trace_csv <- Sys.getenv(
  "SCAN_SMS_CURRENT_WY_TRACE_CSV",
  unset = "docs/data/scan_soil_moisture_current_wy_trace.csv"
)

out_trace_summary <- Sys.getenv(
  "SCAN_SMS_CURRENT_WY_TRACE_SUMMARY_JSON",
  unset = "docs/data/scan_soil_moisture_current_wy_trace_summary.json"
)

out_depth_style_csv <- Sys.getenv(
  "SCAN_DEPTH_STYLE_OUTPUT_CSV",
  unset = "docs/data/scan_depth_style.csv"
)

## Minimum expected station count with any soil-moisture data. This keeps a
## bad service outage from overwriting the last good GitHub-hosted feed with an
## empty or nearly empty product. Adjust only after reviewing QA.
min_stations_with_sms_to_publish <- suppressWarnings(as.integer(Sys.getenv(
  "SCAN_SMS_MIN_STATIONS_TO_PUBLISH",
  unset = "10"
)))
if (is.na(min_stations_with_sms_to_publish) || min_stations_with_sms_to_publish < 1) {
  min_stations_with_sms_to_publish <- 10L
}

## Default depth for map display / hover if available.
## Keep all depths in the feature properties; this order only chooses the
## compact display value. Historical/popup plotting can revisit this later.
display_depth_preference_in <- suppressWarnings(as.integer(strsplit(Sys.getenv(
  "SCAN_SMS_DISPLAY_DEPTH_PREFERENCE_IN",
  unset = "8,20,4,2,40"
), ",", fixed = TRUE)[[1]]))
display_depth_preference_in <- display_depth_preference_in[!is.na(display_depth_preference_in)]
if (length(display_depth_preference_in) == 0) display_depth_preference_in <- c(8L, 20L, 4L, 2L, 40L)

## Standard depth order is finalized after the optional depth-style table is
## read below. Keep this fallback here for early helper defaults.
standard_depth_order_in <- c(2L, 4L, 8L, 20L, 40L)
request_pause_sec <- suppressWarnings(as.numeric(Sys.getenv(
  "SCAN_SMS_REQUEST_PAUSE_SEC",
  unset = "0.15"
)))
if (is.na(request_pause_sec) || request_pause_sec < 0) request_pause_sec <- 0.15

## RF064:
##   GitHub-hosted runners occasionally cannot connect to the NRCS/NWCC SCAN
##   endpoint within the short curl timeout used by lower-level request helpers.
##   Keep the feed protective: do not publish empty replacement files, but fail
##   early and clearly when the endpoint itself is unreachable.
fetch_retries <- suppressWarnings(as.integer(Sys.getenv(
  "SCAN_FETCH_RETRIES",
  unset = "3"
)))
if (is.na(fetch_retries) || fetch_retries < 1) fetch_retries <- 3L

fetch_retry_pause_sec <- suppressWarnings(as.numeric(Sys.getenv(
  "SCAN_FETCH_RETRY_PAUSE_SEC",
  unset = "4"
)))
if (is.na(fetch_retry_pause_sec) || fetch_retry_pause_sec < 0) fetch_retry_pause_sec <- 4

fetch_timeout_sec <- suppressWarnings(as.numeric(Sys.getenv(
  "SCAN_FETCH_TIMEOUT_SEC",
  unset = "60"
)))
if (is.na(fetch_timeout_sec) || fetch_timeout_sec < 5) fetch_timeout_sec <- 60

nrcs_preflight_url <- Sys.getenv(
  "SCAN_NWCC_PREFLIGHT_URL",
  unset = "https://wcc.sc.egov.usda.gov/"
)

nrcs_preflight_retries <- suppressWarnings(as.integer(Sys.getenv(
  "SCAN_NWCC_PREFLIGHT_RETRIES",
  unset = "3"
)))
if (is.na(nrcs_preflight_retries) || nrcs_preflight_retries < 1) nrcs_preflight_retries <- 3L

nrcs_preflight_pause_sec <- suppressWarnings(as.numeric(Sys.getenv(
  "SCAN_NWCC_PREFLIGHT_PAUSE_SEC",
  unset = "10"
)))
if (is.na(nrcs_preflight_pause_sec) || nrcs_preflight_pause_sec < 0) nrcs_preflight_pause_sec <- 10

nrcs_preflight_timeout_sec <- suppressWarnings(as.numeric(Sys.getenv(
  "SCAN_NWCC_PREFLIGHT_TIMEOUT_SEC",
  unset = "20"
)))
if (is.na(nrcs_preflight_timeout_sec) || nrcs_preflight_timeout_sec < 5) nrcs_preflight_timeout_sec <- 20

## RF070:
##   A plain HTTP response is not enough to prove the NRCS/NWCC endpoint is
##   healthy.  A proxy/service 504 response can still be returned while SCAN
##   station fetches mostly return empty tables.  Treat only normal HTTP success
##   statuses as a passed preflight, then verify with a tiny SMS canary fetch.
nrcs_preflight_ok_status_min <- suppressWarnings(as.integer(Sys.getenv(
  "SCAN_NWCC_PREFLIGHT_OK_STATUS_MIN",
  unset = "200"
)))
if (is.na(nrcs_preflight_ok_status_min) || nrcs_preflight_ok_status_min < 100) {
  nrcs_preflight_ok_status_min <- 200L
}

nrcs_preflight_ok_status_max <- suppressWarnings(as.integer(Sys.getenv(
  "SCAN_NWCC_PREFLIGHT_OK_STATUS_MAX",
  unset = "399"
)))
if (is.na(nrcs_preflight_ok_status_max) || nrcs_preflight_ok_status_max < nrcs_preflight_ok_status_min) {
  nrcs_preflight_ok_status_max <- 399L
}

scan_canary_site_codes <- suppressWarnings(as.integer(trimws(strsplit(Sys.getenv(
  "SCAN_CANARY_SITE_CODES",
  unset = "2218,2149,2185,2146"
), ",", fixed = TRUE)[[1]])))
scan_canary_site_codes <- scan_canary_site_codes[!is.na(scan_canary_site_codes)]
if (length(scan_canary_site_codes) == 0) scan_canary_site_codes <- c(2218L, 2149L, 2185L, 2146L)

scan_canary_max_requests <- suppressWarnings(as.integer(Sys.getenv(
  "SCAN_CANARY_MAX_REQUESTS",
  unset = "6"
)))
if (is.na(scan_canary_max_requests) || scan_canary_max_requests < 1) scan_canary_max_requests <- 6L

scan_canary_min_successes <- suppressWarnings(as.integer(Sys.getenv(
  "SCAN_CANARY_MIN_SUCCESSES",
  unset = "1"
)))
if (is.na(scan_canary_min_successes) || scan_canary_min_successes < 1) scan_canary_min_successes <- 1L

scan_initial_empty_abort_requests <- suppressWarnings(as.integer(Sys.getenv(
  "SCAN_INITIAL_EMPTY_ABORT_REQUESTS",
  unset = "12"
)))
if (is.na(scan_initial_empty_abort_requests) || scan_initial_empty_abort_requests < 1) {
  scan_initial_empty_abort_requests <- 12L
}

## Apply a longer httr timeout globally. soilDB::fetchSCAN() uses httr/curl
## under the hood, and this prevents slow-but-working NRCS responses from
## failing at the default ~10-second connection timeout.
httr::set_config(httr::timeout(fetch_timeout_sec))

## Keep UTC fields for machine-readable QA, but create Los Angeles display
## fields for BRIM user-facing popups, hovers, and summaries.
##
## RF044c:
##   BRIM should not display raw UTC/Z timestamps to users.  Use
##   America/Los_Angeles local time labels with PST/PDT where a date-time is
##   shown.  UTC is retained only as a machine/QC field.
display_timezone <- Sys.getenv(
  "BRIM_DISPLAY_TIMEZONE",
  unset = "America/Los_Angeles"
)

feed_build_instant <- Sys.time()
feed_build_time_utc <- format(lubridate::with_tz(feed_build_instant, "UTC"), "%Y-%m-%dT%H:%M:%SZ")
feed_build_time_local <- format(lubridate::with_tz(feed_build_instant, display_timezone), "%Y-%m-%d %I:%M %p %Z")
feed_build_date_local <- as.Date(format(lubridate::with_tz(feed_build_instant, display_timezone), "%Y-%m-%d"))
feed_build_date_utc <- as.Date(format(lubridate::with_tz(feed_build_instant, "UTC"), "%Y-%m-%d"))

pt_current_water_year <- function(date = Sys.Date()) {
  date <- as.Date(date)
  yr <- lubridate::year(date)
  ifelse(lubridate::month(date) >= 10, yr + 1L, yr)
}

pt_water_year <- function(date) {
  date <- as.Date(date)
  yr <- lubridate::year(date)
  ifelse(lubridate::month(date) >= 10, yr + 1L, yr)
}

pt_water_day <- function(date) {
  date <- as.Date(date)
  wy <- pt_water_year(date)
  wy_start <- as.Date(sprintf("%04d-10-01", wy - 1L))
  as.integer(date - wy_start) + 1L
}

current_water_year <- pt_current_water_year(feed_build_date_local)
current_wy_start_date <- as.Date(sprintf("%04d-10-01", current_water_year - 1L))
fetch_start_date <- current_wy_start_date
fetch_end_date <- feed_build_date_local
fetch_years <- sort(unique(lubridate::year(seq(fetch_start_date, fetch_end_date, by = "day"))))

# ---- 3. Depth style ---------------------------------------------------------
##
## RF044:
##   Use one explicit, reusable depth-style table so future products do not
##   invent their own color palettes.  The palette below uses high-contrast,
##   colorblind-aware categorical colors (Okabe-Ito family) rather than a
##   single light-to-dark ramp.
##
##   Important display convention:
##     - Multi-depth traces / labels use these depth colors.
##     - The main selected-depth seasonal plot can still use a black current-WY
##       line over neutral percentile ribbons, matching common NRCS-style plots.

pt_default_depth_style <- function() {
  tibble::tibble(
    depth_in = c(2, 4, 8, 20, 40),
    depth_label = c("2 in", "4 in", "8 in", "20 in", "40 in"),
    depth_order = c(1L, 2L, 3L, 4L, 5L),
    depth_color_hex = c("#E69F00", "#56B4E9", "#009E73", "#D55E00", "#CC79A7"),
    depth_role = c("shallow", "shallow-mid", "default-display", "deep", "deepest")
  )
}

if (file.exists(depth_style_csv)) {
  message("Reading SCAN depth style table: ", depth_style_csv)

  depth_style <- readr::read_csv(depth_style_csv, show_col_types = FALSE)
} else {
  message("SCAN depth style table not found; using built-in fallback colors: ", depth_style_csv)

  depth_style <- pt_default_depth_style()
}

depth_style <- depth_style |>
  dplyr::mutate(
    depth_in = suppressWarnings(as.numeric(depth_in)),
    depth_label = trimws(as.character(depth_label)),
    depth_label = dplyr::na_if(depth_label, ""),
    depth_order = suppressWarnings(as.integer(depth_order)),
    depth_color_hex = trimws(as.character(depth_color_hex)),
    depth_color_hex = toupper(dplyr::na_if(depth_color_hex, "")),
    depth_role = trimws(as.character(depth_role)),
    depth_role = dplyr::na_if(depth_role, "")
  ) |>
  dplyr::filter(!is.na(depth_in)) |>
  dplyr::distinct(depth_in, .keep_all = TRUE) |>
  dplyr::arrange(depth_order, depth_in)

if (!all(c("depth_label", "depth_order", "depth_color_hex") %in% names(depth_style))) {
  stop("SCAN depth style table must include depth_label, depth_order, and depth_color_hex fields.")
}

standard_depth_order_in <- as.integer(depth_style$depth_in)
if (length(standard_depth_order_in) == 0) standard_depth_order_in <- c(2L, 4L, 8L, 20L, 40L)

# ---- 4. Small helpers -------------------------------------------------------

pt_chr <- function(x) {
  x <- as.character(x)
  x <- trimws(x)
  x[x == "" | is.na(x) | toupper(x) %in% c("NA", "NULL", "NAN")] <- NA_character_
  x
}

pt_num <- function(x) {
  suppressWarnings(as.numeric(gsub(",", "", as.character(x))))
}

pt_fmt_num <- function(x, digits = 1, suffix = "") {
  x <- suppressWarnings(as.numeric(x))
  out <- rep(NA_character_, length(x))
  ok <- !is.na(x)
  out[ok] <- paste0(formatC(x[ok], format = "f", digits = digits, big.mark = ","), suffix)
  out <- sub("(\\.\\d*?)0+([^0-9]|$)", "\\1\\2", out)
  out <- sub("\\.([^0-9]|$)", "\\1", out)
  out
}

pt_depth_in_from_sensor <- function(sensor_id, depth_cm) {
  sensor_id <- as.character(sensor_id)

  ## Primary soilDB/NWCC sensor IDs are typically SMS.I_2, SMS.I_4, SMS.I_8,
  ## SMS.I_20, SMS.I_40. Duplicate sensors may appear as SMS.I-2_8, etc. The
  ## regex below intentionally accepts both forms and extracts the final depth.
  from_sensor <- suppressWarnings(as.integer(stringr::str_match(sensor_id, "I(?:-[0-9]+)?_([0-9]+)")[, 2]))
  from_cm <- suppressWarnings(as.integer(round(as.numeric(depth_cm) / 2.54)))
  out <- ifelse(!is.na(from_sensor), from_sensor, from_cm)
  as.integer(out)
}

pt_depth_label <- function(depth_in) {
  x <- suppressWarnings(as.numeric(depth_in))
  out <- paste0(x, " in")
  hit <- match(x, depth_style$depth_in)
  ok <- !is.na(hit)
  out[ok] <- depth_style$depth_label[hit[ok]]
  out[is.na(x)] <- NA_character_
  out
}

pt_depth_color <- function(depth_in) {
  x <- suppressWarnings(as.numeric(depth_in))
  out <- rep("#666666", length(x))
  hit <- match(x, depth_style$depth_in)
  ok <- !is.na(hit)
  out[ok] <- depth_style$depth_color_hex[hit[ok]]
  out[is.na(x)] <- NA_character_
  out
}

pt_stale_class <- function(age_days) {
  dplyr::case_when(
    is.na(age_days)   ~ "missing",
    age_days <= 2     ~ "fresh",
    age_days <= 7     ~ "watch",
    age_days <= 30    ~ "stale",
    TRUE              ~ "very stale"
  )
}

pt_stale_label <- function(age_days) {
  dplyr::case_when(
    is.na(age_days)   ~ "No recent observation",
    age_days <= 2     ~ "Fresh",
    age_days <= 7     ~ "Watch",
    age_days <= 30    ~ "Stale",
    TRUE              ~ "Very stale"
  )
}

pt_age_label <- function(age_days) {
  dplyr::case_when(
    is.na(age_days) ~ "Not available",
    age_days == 0   ~ "today",
    age_days == 1   ~ "1 day old",
    TRUE            ~ paste0(age_days, " days old")
  )
}

pt_context_label <- function(sms_pct, p10, p30, p70, p90) {
  dplyr::case_when(
    is.na(sms_pct) | is.na(p10) | is.na(p30) | is.na(p70) | is.na(p90) ~ "No context",
    sms_pct <= p10 ~ "Much below normal",
    sms_pct <= p30 ~ "Below normal",
    sms_pct <= p70 ~ "Near normal",
    sms_pct <= p90 ~ "Above normal",
    TRUE           ~ "Much above normal"
  )
}

pt_vs_median_label <- function(x) {
  x <- suppressWarnings(as.numeric(x))
  dplyr::case_when(
    is.na(x)  ~ "NA",
    x > 0     ~ paste0("+", pt_fmt_num(x, 1), " pts"),
    x < 0     ~ paste0("-", pt_fmt_num(abs(x), 1), " pts"),
    TRUE      ~ "0 pts"
  )
}

pt_utc_datetime_to_local_label <- function(x) {
  x <- as.character(x)
  out <- rep(NA_character_, length(x))
  ok <- !is.na(x) & nzchar(x)

  if (any(ok)) {
    parsed <- suppressWarnings(lubridate::ymd_hms(x[ok], tz = "UTC"))
    good <- !is.na(parsed)
    tmp <- rep(NA_character_, length(parsed))
    tmp[good] <- format(lubridate::with_tz(parsed[good], display_timezone), "%Y-%m-%d %I:%M %p %Z")
    out[ok] <- tmp
  }

  out
}

pt_depth_table_html <- function(depths, depth_labels, depth_colors, values, dates, ages,
                                sensors, sensor_counts, context_labels, vs_medians, n_years) {
  if (length(depths) == 0 || all(is.na(depths))) {
    return("<span style='color:#666;'>No current-water-year soil-moisture values found.</span>")
  }

  ord <- order(match(depths, standard_depth_order_in), depths, na.last = TRUE)
  depths <- depths[ord]
  depth_labels <- depth_labels[ord]
  depth_colors <- depth_colors[ord]
  values <- values[ord]
  dates <- dates[ord]
  ages <- ages[ord]
  sensors <- sensors[ord]
  sensor_counts <- sensor_counts[ord]
  context_labels <- context_labels[ord]
  vs_medians <- vs_medians[ord]
  n_years <- n_years[ord]

  rows <- purrr::pmap_chr(
    list(depths, depth_labels, depth_colors, values, dates, ages, sensors, sensor_counts, context_labels, vs_medians, n_years),
    function(depth, depth_label, depth_color, value, date, age, sensor, sensor_count, context_label, vs_median, n_years) {
      sensor_note <- ""
      if (!is.na(sensor_count) && sensor_count > 1) {
        sensor_note <- paste0(" <span style='color:#666;'>(avg of ", sensor_count, " sensors)</span>")
      }

      context_txt <- ifelse(is.na(context_label) || context_label == "", "No context", context_label)
      if (!is.na(vs_median)) {
        context_txt <- paste0(context_txt, " (", pt_vs_median_label(vs_median), " vs median)")
      }
      if (!is.na(n_years)) {
        context_txt <- paste0(context_txt, "<br><span style='color:#666;'>", n_years, " yrs</span>")
      }

      paste0(
        "<tr>",
        "<td style='padding:1px 6px 1px 0; text-align:right; white-space:nowrap;'>",
        "<span style='display:inline-block; width:8px; height:8px; border-radius:50%; background:",
        ifelse(is.na(depth_color), "#666666", depth_color),
        "; margin-right:4px;'></span>",
        ifelse(is.na(depth_label), paste0(depth, " in"), depth_label), "</td>",
        "<td style='padding:1px 6px;'>", ifelse(is.na(value), "NA", paste0(pt_fmt_num(value, 1), "%")), sensor_note, "</td>",
        "<td style='padding:1px 6px; color:#555;'>", ifelse(is.na(date), "NA", as.character(date)), "<br><span style='color:#666;'>", pt_age_label(age), "</span></td>",
        "<td style='padding:1px 0; color:#555;'>", context_txt, "</td>",
        "</tr>"
      )
    }
  )

  paste0(
    "<table style='border-collapse:collapse; font-size:12px;'>",
    "<tr><th style='text-align:right; padding-right:6px;'>Depth</th>",
    "<th style='text-align:left; padding-right:6px;'>Latest</th>",
    "<th style='text-align:left; padding-right:6px;'>Date / age</th>",
    "<th style='text-align:left;'>Historical context</th></tr>",
    paste(rows, collapse = ""),
    "</table>"
  )
}

pt_make_popup_html <- function(row) {
  depth_label <- ifelse(is.na(row$display_depth_label), "Not available", row$display_depth_label)
  value_label <- ifelse(is.na(row$display_sms_pct), "Not available", paste0(pt_fmt_num(row$display_sms_pct, 1), "%"))
  median_label <- ifelse(is.na(row$display_median_sms_pct), "Not available", paste0(pt_fmt_num(row$display_median_sms_pct, 1), "%"))
  vs_median_label <- pt_vs_median_label(row$display_vs_median_pctpts)

  link_html <- ""
  if (!is.na(row$site_page_url) && nzchar(row$site_page_url)) {
    link_html <- paste0(
      "<br><a href='", row$site_page_url, "' target='_blank' rel='noopener noreferrer'>Open NRCS station page</a>"
    )
  }

  context_block <- paste0(
    "<b>Historical context:</b> ", ifelse(is.na(row$display_context_label), "No context", row$display_context_label), "<br>",
    "<b>Median for date/depth:</b> ", median_label, "<br>",
    "<b>Difference from median:</b> ", vs_median_label, "<br>",
    "<b>Years in context:</b> ", ifelse(is.na(row$display_context_n_years), "Not available", row$display_context_n_years), "<br>"
  )

  paste0(
    "<b>", row$station_name, "</b><br>",
    "<b>Type:</b> SCAN<br>",
    "<b>Provider:</b> USDA NRCS NWCC<br>",
    "<b>Station ID:</b> ", row$site_code, "<br>",
    "<b>Elevation:</b> ", ifelse(is.na(row$elevation_ft), "Not available", paste0(formatC(row$elevation_ft, format = "f", digits = 0, big.mark = ","), " ft")), "<br>",
    "<b>Location:</b> ", ifelse(is.na(row$county), "", paste0(row$county, ", ")), row$state, "<br>",
    "<b>Lat/Lon:</b> ", sprintf("%.6f, %.6f", row$latitude, row$longitude), "<br>",
    "<hr style='margin:4px 0;'/>",
    "<b>Latest soil moisture:</b> ", value_label, " at ", depth_label, "<br>",
    "<b>Observation date:</b> ", ifelse(is.na(row$display_obs_date), "Not available", row$display_obs_date), "<br>",
    "<b>Observation age:</b> ", pt_age_label(row$display_obs_age_days), "<br>",
    "<b>Status:</b> ", row$display_status_label, "<br>",
    context_block,
    "<b>Available depths:</b> ", ifelse(is.na(row$available_depths_in), "Not available", row$available_depths_in), "<br>",
    "<div style='margin-top:4px;'><b>Depth detail:</b><br>", row$depth_table_html, "</div>",
    "<div style='font-size:11px; color:#555; margin-top:4px;'>Current-water-year trace and historical percentile tables are available in hosted feed files; ribbon plots will be added in the popup sandbox.<br>",
    "Feed built: ", ifelse(is.na(row$feed_build_time_local), "Not available", row$feed_build_time_local), "</div>",
    link_html
  )
}

pt_make_feature <- function(row) {
  props <- as.list(row[setdiff(names(row), c("longitude", "latitude"))])

  ## jsonlite writes data-frame row values as length-one vectors. Convert simple
  ## values to scalars while leaving NA values as JSON null.
  props <- lapply(props, function(v) {
    if (length(v) == 0) return(NULL)
    if (length(v) == 1 && is.na(v)) return(NULL)
    if (length(v) == 1) return(v[[1]])
    v
  })

  list(
    type = "Feature",
    geometry = list(
      type = "Point",
      coordinates = list(as.numeric(row$longitude), as.numeric(row$latitude))
    ),
    properties = props
  )
}

# ---- 4. Read station index --------------------------------------------------

if (!file.exists(station_index_csv)) {
  stop("Missing SCAN station-index CSV: ", station_index_csv)
}

station_index <- readr::read_csv(station_index_csv, show_col_types = FALSE)

required_cols <- c(
  "station_uid", "station_name", "site_code", "state", "county",
  "elevation_ft", "latitude", "longitude", "site_page_url"
)
missing_cols <- setdiff(required_cols, names(station_index))
if (length(missing_cols) > 0) {
  stop("SCAN station index is missing required field(s): ", paste(missing_cols, collapse = ", "))
}

station_index <- station_index |>
  mutate(
    station_uid = pt_chr(station_uid),
    station_name = pt_chr(station_name),
    site_code = suppressWarnings(as.integer(site_code)),
    state = pt_chr(state),
    county = pt_chr(county),
    elevation_ft = pt_num(elevation_ft),
    latitude = pt_num(latitude),
    longitude = pt_num(longitude),
    site_page_url = pt_chr(site_page_url),
    data_page_url = if ("data_page_url" %in% names(station_index)) pt_chr(data_page_url) else NA_character_,
    source_url = if ("source_url" %in% names(station_index)) pt_chr(source_url) else NA_character_,
    start_year = if ("start_year" %in% names(station_index)) suppressWarnings(as.integer(start_year)) else NA_integer_,
    status = if ("status" %in% names(station_index)) pt_chr(status) else NA_character_
  ) |>
  filter(!is.na(site_code), !is.na(latitude), !is.na(longitude)) |>
  distinct(site_code, .keep_all = TRUE)

message("SCAN stations in index: ", nrow(station_index))
message("Current water year: ", current_water_year)
message("Current WY start date: ", current_wy_start_date)
message("Fetch years: ", paste(fetch_years, collapse = ", "))

# ---- 5. Read optional historical percentile context -------------------------

percentiles_available <- file.exists(percentiles_csv)

if (percentiles_available) {
  message("Reading SCAN water-day percentile context: ", percentiles_csv)

  ## RF044b:
  ##   Read the CSV into an explicitly named object before mutating it.  The
  ##   earlier RF044 draft checked names(percentiles) inside the very pipeline
  ##   that was creating percentiles, which fails because that object does not
  ##   exist yet.  Keeping the raw import separate also makes optional-column
  ##   handling clearer for future context fields.
  percentiles_raw <- readr::read_csv(percentiles_csv, show_col_types = FALSE)

  percentiles <- percentiles_raw |>
    mutate(
      site_code = suppressWarnings(as.integer(site_code)),
      depth_in = suppressWarnings(as.numeric(depth_in)),
      water_day = suppressWarnings(as.integer(water_day)),
      p10 = suppressWarnings(as.numeric(p10)),
      p30 = suppressWarnings(as.numeric(p30)),
      p50 = suppressWarnings(as.numeric(p50)),
      p70 = suppressWarnings(as.numeric(p70)),
      p90 = suppressWarnings(as.numeric(p90)),
      n_years = suppressWarnings(as.integer(n_years)),
      climatology_ok = if ("climatology_ok" %in% names(percentiles_raw)) as.logical(climatology_ok) else NA,
      min_years_for_context = if ("min_years_for_context" %in% names(percentiles_raw)) suppressWarnings(as.integer(min_years_for_context)) else 7L
    ) |>
    select(site_code, depth_in, water_day, p10, p30, p50, p70, p90, n_years, climatology_ok, min_years_for_context)

} else {
  message("Optional SCAN percentile context not found; latest feed will run without historical context: ", percentiles_csv)

  percentiles <- tibble::tibble(
    site_code = integer(),
    depth_in = numeric(),
    water_day = integer(),
    p10 = numeric(),
    p30 = numeric(),
    p50 = numeric(),
    p70 = numeric(),
    p90 = numeric(),
    n_years = integer(),
    climatology_ok = logical(),
    min_years_for_context = integer()
  )
}

# ---- 6. Fetch current-water-year soil-moisture data -------------------------

pt_check_nrcs_endpoint <- function() {
  last_error <- NA_character_

  for (attempt in seq_len(nrcs_preflight_retries)) {
    message(
      "Checking NRCS/NWCC endpoint connectivity (attempt ", attempt, "/",
      nrcs_preflight_retries, "): ", nrcs_preflight_url
    )

    resp <- tryCatch(
      httr::GET(
        nrcs_preflight_url,
        httr::timeout(nrcs_preflight_timeout_sec),
        httr::user_agent("BRIM-SCAN-live-feed/1.0")
      ),
      error = function(e) e
    )

    if (inherits(resp, "response")) {
      status <- httr::status_code(resp)
      if (!is.na(status) && status >= nrcs_preflight_ok_status_min && status <= nrcs_preflight_ok_status_max) {
        message("NRCS/NWCC endpoint reachable; HTTP status ", status, ".")
        return(invisible(TRUE))
      }

      last_error <- paste0("HTTP status ", status, " from ", nrcs_preflight_url)
      message(
        "  NRCS/NWCC endpoint returned unhealthy HTTP status ", status,
        "; expected ", nrcs_preflight_ok_status_min, "–", nrcs_preflight_ok_status_max, "."
      )
    } else {
      last_error <- conditionMessage(resp)
      message("  NRCS/NWCC endpoint check failed: ", last_error)
    }

    if (attempt < nrcs_preflight_retries && nrcs_preflight_pause_sec > 0) {
      message("  Retrying endpoint check after ", nrcs_preflight_pause_sec, " sec...")
      Sys.sleep(nrcs_preflight_pause_sec)
    }
  }

  stop(
    "NRCS/NWCC SCAN endpoint is not healthy from this runner after ",
    nrcs_preflight_retries, " preflight attempt(s). ",
    "Not publishing replacement SCAN feed files; the previous hosted feed is preserved. ",
    "Last endpoint result: ", last_error
  )
}

scan_fetch_diag_rows <- list()

pt_record_fetch_diag <- function(site_code, year, attempts, rows, status, message_txt) {
  scan_fetch_diag_rows[[length(scan_fetch_diag_rows) + 1L]] <<- tibble::tibble(
    site_code = as.integer(site_code),
    year = as.integer(year),
    attempts = as.integer(attempts),
    rows = as.integer(rows),
    status = as.character(status),
    message = as.character(message_txt)
  )
  invisible(NULL)
}

fetch_one_scan_year <- function(site_code, year) {

  last_message <- ""

  for (attempt in seq_len(fetch_retries)) {
    message("Fetching SCAN SMS site ", site_code, ", year ", year, " (attempt ", attempt, "/", fetch_retries, ")...")

    warning_messages <- character()

    out <- tryCatch(
      withCallingHandlers(
        {
          x <- soilDB::fetchSCAN(
            site.code = site_code,
            year = year,
            report = "SMS",
            timeseries = "Daily",
            tz = "UTC"
          )

          if (!is.list(x) || !"SMS" %in% names(x) || is.null(x$SMS) || nrow(x$SMS) == 0) {
            tibble::tibble()
          } else {
            tibble::as_tibble(x$SMS)
          }
        },
        warning = function(w) {
          warning_messages <<- c(warning_messages, conditionMessage(w))
          invokeRestart("muffleWarning")
        }
      ),
      error = function(e) {
        last_message <<- conditionMessage(e)
        tibble()
      }
    )

    if (nrow(out) > 0) {
      if (length(warning_messages) > 0) {
        last_message <- paste(unique(warning_messages), collapse = " | ")
      } else {
        last_message <- "ok"
      }

      pt_record_fetch_diag(site_code, year, attempt, nrow(out), "ok", last_message)
      if (request_pause_sec > 0) Sys.sleep(request_pause_sec)
      return(out)
    }

    if (length(warning_messages) > 0) {
      last_message <- paste(unique(warning_messages), collapse = " | ")
    }
    if (!nzchar(last_message)) {
      last_message <- "empty SMS table returned"
    }

    if (attempt < fetch_retries) {
      message(
        "  no SMS rows on attempt ", attempt, "/", fetch_retries,
        " (", last_message, "); retrying after ", fetch_retry_pause_sec, " sec..."
      )
      if (fetch_retry_pause_sec > 0) Sys.sleep(fetch_retry_pause_sec)
    }
  }

  message(
    "  no SMS rows after ", fetch_retries, " attempt(s) for site ",
    site_code, ", year ", year, ": ", last_message
  )

  pt_record_fetch_diag(site_code, year, fetch_retries, 0L, "empty_or_failed", last_message)
  if (request_pause_sec > 0) Sys.sleep(request_pause_sec)
  tibble()
}

pt_scan_canary_pairs <- function() {
  canary_sites <- intersect(scan_canary_site_codes, station_index$site_code)
  if (length(canary_sites) == 0) {
    canary_sites <- head(station_index$site_code, 3)
  }

  pairs <- tidyr::expand_grid(
    site_code = canary_sites,
    year = rev(fetch_years)
  ) |>
    dplyr::slice_head(n = scan_canary_max_requests)

  pairs
}

pt_run_scan_canary <- function() {
  canary_pairs <- pt_scan_canary_pairs()

  if (nrow(canary_pairs) == 0) {
    stop(
      "SCAN canary fetch could not choose any station/year pairs. ",
      "Not publishing replacement feed files; the previous hosted feed is preserved."
    )
  }

  message(
    "\nRunning SCAN SMS canary fetch: up to ", nrow(canary_pairs),
    " station/year request(s); need ", scan_canary_min_successes,
    " request(s) with SMS rows."
  )

  canary_rows <- vector("list", nrow(canary_pairs))
  canary_successes <- 0L

  for (i in seq_len(nrow(canary_pairs))) {
    site_i <- canary_pairs$site_code[[i]]
    year_i <- canary_pairs$year[[i]]
    out_i <- fetch_one_scan_year(site_i, year_i)
    canary_rows[[i]] <- out_i

    if (nrow(out_i) > 0) {
      canary_successes <- canary_successes + 1L
      if (canary_successes >= scan_canary_min_successes) {
        message("SCAN SMS canary passed: ", canary_successes, " successful request(s).")
        return(list(
          ok = TRUE,
          pairs = canary_pairs[seq_len(i), , drop = FALSE],
          rows = dplyr::bind_rows(canary_rows[seq_len(i)])
        ))
      }
    }
  }

  stop(
    "SCAN SMS canary failed: ", canary_successes, " of ", nrow(canary_pairs),
    " canary request(s) returned SMS rows; minimum required is ", scan_canary_min_successes, ". ",
    "This usually indicates a degraded NRCS/NWCC or soilDB session from the GitHub runner. ",
    "Not publishing replacement SCAN feed files; the previous hosted feed is preserved."
  )
}

pt_fetch_scan_grid_with_early_abort <- function(fetch_grid, canary_rows = tibble::tibble()) {
  pieces <- list(canary_rows)
  first_main_success_seen <- FALSE
  initial_empty_count <- 0L

  if (nrow(fetch_grid) == 0) {
    return(dplyr::bind_rows(pieces))
  }

  for (i in seq_len(nrow(fetch_grid))) {
    site_i <- fetch_grid$site_code[[i]]
    year_i <- fetch_grid$year[[i]]
    out_i <- fetch_one_scan_year(site_i, year_i)
    pieces[[length(pieces) + 1L]] <- out_i

    if (nrow(out_i) > 0) {
      first_main_success_seen <- TRUE
    } else if (!first_main_success_seen) {
      initial_empty_count <- initial_empty_count + 1L

      if (initial_empty_count >= scan_initial_empty_abort_requests) {
        stop(
          "SCAN fetch early-abort: the first ", initial_empty_count,
          " main station/year request(s) after the canary returned no SMS rows. ",
          "This is likely a degraded NRCS/NWCC or soilDB session from the GitHub runner. ",
          "Not publishing replacement SCAN feed files; the previous hosted feed is preserved."
        )
      }
    }
  }

  dplyr::bind_rows(pieces)
}

pt_check_nrcs_endpoint()
canary <- pt_run_scan_canary()

fetch_grid <- tidyr::expand_grid(
  site_code = station_index$site_code,
  year = fetch_years
) |>
  dplyr::anti_join(
    canary$pairs |>
      dplyr::mutate(
        site_code = as.integer(.data$site_code),
        year = as.integer(.data$year)
      ),
    by = c("site_code", "year")
  )

sms_raw <- pt_fetch_scan_grid_with_early_abort(
  fetch_grid = fetch_grid,
  canary_rows = canary$rows
)

scan_fetch_diag <- dplyr::bind_rows(scan_fetch_diag_rows)

if (nrow(scan_fetch_diag) > 0) {
  message("\nSCAN fetch diagnostics summary:")
  print(
    scan_fetch_diag |>
      dplyr::count(.data$status, name = "requests") |>
      dplyr::arrange(dplyr::desc(.data$requests)),
    n = Inf
  )

  failed_diag <- scan_fetch_diag |>
    dplyr::filter(.data$status != "ok") |>
    dplyr::select("site_code", "year", "attempts", "rows", "message") |>
    dplyr::slice_head(n = 12)

  if (nrow(failed_diag) > 0) {
    message("\nFirst SCAN fetch failures/empty responses:")
    print(failed_diag, n = Inf, width = Inf)
  }
}

if (nrow(sms_raw) == 0) {
  stop(
    "No SCAN SMS rows were returned across all station/year requests. ",
    "Not publishing replacement feed files; the previous hosted feed is preserved. ",
    "If the preflight succeeded, review the fetch diagnostics above for NRCS/soilDB response details."
  )
}

sms_clean <- sms_raw |>
  mutate(
    site_code = suppressWarnings(as.integer(Site)),
    obs_date = as.Date(Date),
    obs_datetime_utc = if ("datetime" %in% names(sms_raw)) {
      format(lubridate::with_tz(as.POSIXct(datetime, tz = "UTC"), "UTC"), "%Y-%m-%dT%H:%M:%SZ")
    } else {
      NA_character_
    },
    obs_datetime_local = pt_utc_datetime_to_local_label(obs_datetime_utc),
    sms_pct = suppressWarnings(as.numeric(value)),
    depth_in = pt_depth_in_from_sensor(sensor.id, depth),
    sensor_id = as.character(sensor.id),
    water_year = pt_water_year(obs_date),
    water_day = pt_water_day(obs_date)
  ) |>
  filter(
    !is.na(site_code),
    !is.na(depth_in),
    !is.na(obs_date),
    obs_date >= current_wy_start_date,
    obs_date <= fetch_end_date,
    !is.na(sms_pct)
  )

if (nrow(sms_clean) == 0) {
  stop("No current-water-year SCAN SMS rows were returned. Not publishing a replacement feed.")
}

## Normalize duplicate same-depth sensors into one daily value per station/depth.
## This matches the historical RF043 percentile preprocessor and preserves the
## sensor IDs/count so BRIM can disclose averaged duplicate sensors.
sms_daily <- sms_clean |>
  group_by(site_code, obs_date, water_year, water_day, depth_in) |>
  summarise(
    sms_pct = mean(sms_pct, na.rm = TRUE),
    obs_datetime_utc = {
      z <- na.omit(obs_datetime_utc)
      if (length(z) == 0) NA_character_ else z[1]
    },
    obs_datetime_local = {
      z <- na.omit(obs_datetime_local)
      if (length(z) == 0) NA_character_ else z[1]
    },
    sensor_id = paste(sort(unique(sensor_id)), collapse = ", "),
    sensor_count = dplyr::n_distinct(sensor_id),
    .groups = "drop"
  ) |>
  mutate(
    obs_age_days = as.integer(fetch_end_date - obs_date)
  ) |>
  left_join(depth_style, by = "depth_in") |>
  arrange(site_code, depth_order, depth_in, obs_date)

## Latest by station/depth.
depth_latest <- sms_daily |>
  group_by(site_code, depth_in) |>
  filter(obs_date == max(obs_date, na.rm = TRUE)) |>
  slice(1) |>
  ungroup()

# ---- 7. Join historical context to latest depths ----------------------------

depth_latest <- depth_latest |>
  left_join(percentiles, by = c("site_code", "depth_in", "water_day")) |>
  mutate(
    context_climatology_ok = dplyr::coalesce(as.logical(climatology_ok), FALSE) &
      !is.na(n_years) &
      n_years >= dplyr::coalesce(min_years_for_context, 7L),
    context_label = ifelse(
      context_climatology_ok,
      pt_context_label(sms_pct, p10, p30, p70, p90),
      "No context"
    ),
    median_sms_pct = ifelse(context_climatology_ok, p50, NA_real_),
    vs_median_pctpts = ifelse(context_climatology_ok & !is.na(sms_pct) & !is.na(p50), sms_pct - p50, NA_real_),
    context_n_years = n_years,
    context_min_years = min_years_for_context
  )

stations_with_sms <- dplyr::n_distinct(depth_latest$site_code)

if (stations_with_sms < min_stations_with_sms_to_publish) {
  stop(
    "Only ", stations_with_sms, " SCAN stations had current-water-year SMS data; ",
    "minimum required to publish is ", min_stations_with_sms_to_publish, ". ",
    "Not publishing replacement feed files."
  )
}

# ---- 8. Build current-water-year trace output -------------------------------

current_wy_trace <- sms_daily |>
  left_join(
    station_index |>
      select(station_uid, station_name, site_code),
    by = "site_code"
  ) |>
  transmute(
    station_uid,
    station_name,
    site_code,
    depth_in,
    depth_label,
    depth_order,
    depth_color_hex,
    water_year,
    water_day,
    obs_date = as.character(obs_date),
    obs_datetime_local,
    sms_pct = round(sms_pct, 2),
    sensor_count,
    sensor_id
  ) |>
  arrange(site_code, depth_in, obs_date)

# ---- 9. Build feature properties -------------------------------------------

station_depth_summary <- depth_latest |>
  group_by(site_code) |>
  summarise(
    available_depths_in = paste(sort(unique(depth_in), na.last = NA), collapse = ", "),
    available_depth_labels = paste(depth_style$depth_label[match(sort(unique(depth_in)), depth_style$depth_in)], collapse = ", "),
    depth_values_json = jsonlite::toJSON(
      purrr::pmap(
        list(depth_in, depth_label, depth_order, depth_color_hex, sms_pct, obs_date, obs_age_days, sensor_id, sensor_count,
             context_label, median_sms_pct, vs_median_pctpts, context_n_years, context_min_years),
        function(depth_in, depth_label, depth_order, depth_color_hex, sms_pct, obs_date, obs_age_days, sensor_id, sensor_count,
                 context_label, median_sms_pct, vs_median_pctpts, context_n_years, context_min_years) {
          list(
            depth_in = depth_in,
            depth_label = depth_label,
            depth_order = depth_order,
            depth_color_hex = depth_color_hex,
            sms_pct = round(sms_pct, 2),
            obs_date = as.character(obs_date),
            obs_age_days = obs_age_days,
            sensor_id = sensor_id,
            sensor_count = sensor_count,
            context_label = context_label,
            median_sms_pct = ifelse(is.na(median_sms_pct), NA, round(median_sms_pct, 2)),
            vs_median_pctpts = ifelse(is.na(vs_median_pctpts), NA, round(vs_median_pctpts, 2)),
            context_n_years = context_n_years,
            context_min_years = context_min_years
          )
        }
      ),
      auto_unbox = TRUE,
      na = "null"
    ),
    depth_table_html = pt_depth_table_html(
      depth_in, depth_label, depth_color_hex, sms_pct, obs_date, obs_age_days, sensor_id, sensor_count,
      context_label, vs_median_pctpts, context_n_years
    ),
    .groups = "drop"
  )

select_display_depth <- function(depths) {
  depths <- as.integer(depths)
  depths <- depths[!is.na(depths)]
  if (length(depths) == 0) return(NA_integer_)

  pref <- display_depth_preference_in[display_depth_preference_in %in% depths]
  if (length(pref) > 0) return(pref[1])

  depths[order(match(depths, standard_depth_order_in), depths, na.last = TRUE)][1]
}

display_latest <- depth_latest |>
  group_by(site_code) |>
  mutate(display_depth_in_tmp = select_display_depth(depth_in)) |>
  filter(depth_in == dplyr::first(display_depth_in_tmp)) |>
  slice(1) |>
  ungroup() |>
  transmute(
    site_code,
    display_depth_in = depth_in,
    display_depth_label = depth_label,
    display_depth_order = depth_order,
    display_depth_color_hex = depth_color_hex,
    display_sms_pct = round(sms_pct, 2),
    display_obs_date = as.character(obs_date),
    display_obs_datetime_utc = obs_datetime_utc,
    display_obs_datetime_local = obs_datetime_local,
    display_obs_age_days = obs_age_days,
    display_sensor_id = sensor_id,
    display_sensor_count = sensor_count,
    display_status_class = pt_stale_class(obs_age_days),
    display_status_label = pt_stale_label(obs_age_days),
    display_context_label = context_label,
    display_median_sms_pct = round(median_sms_pct, 2),
    display_vs_median_pctpts = round(vs_median_pctpts, 2),
    display_context_n_years = context_n_years,
    display_context_min_years = context_min_years,
    display_context_climatology_ok = context_climatology_ok
  )

features_df <- station_index |>
  left_join(station_depth_summary, by = "site_code") |>
  left_join(display_latest, by = "site_code") |>
  mutate(
    feed_build_time_utc = feed_build_time_utc,
    feed_build_time_local = feed_build_time_local,
    display_timezone = display_timezone,
    current_water_year = current_water_year,
    current_wy_start_date = as.character(current_wy_start_date),
    percentile_context_available = percentiles_available,
    available_depths_in = na_if(available_depths_in, ""),
    available_depth_labels = na_if(available_depth_labels, ""),
    depth_values_json = ifelse(is.na(depth_values_json), "[]", depth_values_json),
    depth_table_html = ifelse(
      is.na(depth_table_html),
      "<span style='color:#666;'>No current-water-year soil-moisture values found.</span>",
      depth_table_html
    ),
    display_status_class = ifelse(is.na(display_status_class), "missing", display_status_class),
    display_status_label = ifelse(is.na(display_status_label), "No current-water-year observation", display_status_label),
    display_context_label = ifelse(is.na(display_context_label), "No context", display_context_label),
    hover_text = paste0(
      station_name,
      "\nSCAN soil moisture",
      "\nDisplay depth: ", ifelse(is.na(display_depth_label), "NA", display_depth_label),
      "\nLatest: ", ifelse(is.na(display_sms_pct), "NA", paste0(pt_fmt_num(display_sms_pct, 1), "%")),
      "\nContext: ", display_context_label,
      "\nAge: ", pt_age_label(display_obs_age_days)
    )
  )

features_df$popup_html <- purrr::pmap_chr(features_df, function(...) {
  row <- tibble::as_tibble(list(...))
  pt_make_popup_html(row[1, ])
})

# ---- 10. Write GeoJSON, current-WY trace, and summaries ---------------------

for (p in c(out_geojson, out_summary, out_trace_csv, out_trace_summary, out_depth_style_csv)) {
  if (!dir.exists(dirname(p))) dir.create(dirname(p), recursive = TRUE, showWarnings = FALSE)
}

features <- purrr::pmap(
  features_df,
  function(...) {
    row <- tibble::as_tibble(list(...))
    pt_make_feature(row[1, ])
  }
)

geojson <- list(
  type = "FeatureCollection",
  name = "BRIM SCAN soil moisture latest",
  feed_build_time_utc = feed_build_time_utc,
  feed_build_time_local = feed_build_time_local,
  display_timezone = display_timezone,
  current_water_year = current_water_year,
  current_wy_start_date = as.character(current_wy_start_date),
  features = features
)

jsonlite::write_json(
  geojson,
  path = out_geojson,
  auto_unbox = TRUE,
  pretty = TRUE,
  na = "null"
)

readr::write_csv(current_wy_trace, out_trace_csv, na = "")
readr::write_csv(depth_style, out_depth_style_csv, na = "")

summary_tbl <- tibble::tibble(
  metric = c(
    "feed_build_time_utc",
    "feed_build_time_local",
    "display_timezone",
    "station_index_rows",
    "features_written",
    "stations_with_any_current_wy_sms",
    "stations_without_current_wy_sms",
    "depth_rows_latest",
    "current_wy_trace_rows",
    "current_water_year",
    "current_wy_start_date",
    "fetch_years",
    "percentile_context_available",
    "percentile_rows_input",
    "display_depth_preference_in",
    "depth_style_output_csv"
  ),
  value = c(
    feed_build_time_utc,
    feed_build_time_local,
    display_timezone,
    as.character(nrow(station_index)),
    as.character(nrow(features_df)),
    as.character(sum(!is.na(features_df$available_depths_in))),
    as.character(sum(is.na(features_df$available_depths_in))),
    as.character(nrow(depth_latest)),
    as.character(nrow(current_wy_trace)),
    as.character(current_water_year),
    as.character(current_wy_start_date),
    paste(fetch_years, collapse = ", "),
    as.character(percentiles_available),
    as.character(nrow(percentiles)),
    paste(display_depth_preference_in, collapse = ", "),
    out_depth_style_csv
  )
)

summary_obj <- list(
  feed_build_time_utc = feed_build_time_utc,
  feed_build_time_local = feed_build_time_local,
  display_timezone = display_timezone,
  station_index_rows = nrow(station_index),
  features_written = nrow(features_df),
  stations_with_any_current_wy_sms = sum(!is.na(features_df$available_depths_in)),
  stations_without_current_wy_sms = sum(is.na(features_df$available_depths_in)),
  depth_rows_latest = nrow(depth_latest),
  current_wy_trace_rows = nrow(current_wy_trace),
  current_water_year = current_water_year,
  current_wy_start_date = as.character(current_wy_start_date),
  fetch_years = fetch_years,
  percentile_context_available = percentiles_available,
  percentile_rows_input = nrow(percentiles),
  display_depth_preference_in = display_depth_preference_in,
  depth_style_output_csv = out_depth_style_csv,
  depth_style = split(depth_style, seq_len(nrow(depth_style))),
  status_counts = as.list(table(features_df$display_status_class, useNA = "ifany")),
  depth_counts_latest = as.list(table(depth_latest$depth_in, useNA = "ifany")),
  trace_depth_counts = as.list(table(current_wy_trace$depth_in, useNA = "ifany")),
  context_counts = as.list(table(features_df$display_context_label, useNA = "ifany"))
)

jsonlite::write_json(
  summary_obj,
  path = out_summary,
  auto_unbox = TRUE,
  pretty = TRUE,
  na = "null"
)

trace_summary_obj <- list(
  feed_build_time_utc = feed_build_time_utc,
  feed_build_time_local = feed_build_time_local,
  display_timezone = display_timezone,
  current_water_year = current_water_year,
  current_wy_start_date = as.character(current_wy_start_date),
  trace_rows = nrow(current_wy_trace),
  stations = dplyr::n_distinct(current_wy_trace$site_code),
  depth_counts = as.list(table(current_wy_trace$depth_in, useNA = "ifany")),
  depth_style_output_csv = out_depth_style_csv,
  first_date = if (nrow(current_wy_trace) == 0) NA_character_ else min(current_wy_trace$obs_date, na.rm = TRUE),
  last_date = if (nrow(current_wy_trace) == 0) NA_character_ else max(current_wy_trace$obs_date, na.rm = TRUE)
)

jsonlite::write_json(
  trace_summary_obj,
  path = out_trace_summary,
  auto_unbox = TRUE,
  pretty = TRUE,
  na = "null"
)

message("Wrote SCAN soil-moisture latest GeoJSON: ", out_geojson)
message("Wrote SCAN soil-moisture latest summary: ", out_summary)
message("Wrote SCAN current-WY trace CSV: ", out_trace_csv)
message("Wrote SCAN current-WY trace summary: ", out_trace_summary)
message("Wrote SCAN depth style CSV: ", out_depth_style_csv)
message("\nSCAN latest/current-WY feed summary:")
print(summary_tbl, n = Inf)
