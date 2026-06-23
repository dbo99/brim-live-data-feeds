# ==== build_delta_ops_daily_summary.R ========================================
#
# PURPOSE:
#   Fetch DWR's Delta Operations Daily Summary PDF and publish small static
#   GeoJSON/JSON files for the BRIM Ops Live Delta operations snapshot layer.
#
# OUTPUTS:
#   docs/data/delta_ops_daily_summary_features.geojson
#   docs/data/delta_ops_daily_summary_summary.json
#   docs/data/delta_ops_daily_summary.json
#   docs/data/delta_ops_x2_reference.geojson
#
# INPUTS:
#   data/input/delta_ops_static_locations.csv
#   data/input/x2_river_km_lookup.csv
#
# HOW TO RUN LOCALLY FROM brim-live-data-feeds REPOSITORY ROOT:
#   Rscript scripts/build_delta_ops_daily_summary.R
# ============================================================================

required_pkgs <- c("curl", "pdftools", "jsonlite", "readr", "tibble", "dplyr", "lubridate")
missing_pkgs <- required_pkgs[!vapply(required_pkgs, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing_pkgs) > 0) {
  stop("Missing required R packages: ", paste(missing_pkgs, collapse = ", "),
       "\nInstall them before running locally, or let the GitHub Action install them.")
}

suppressPackageStartupMessages({
  library(curl)
  library(pdftools)
  library(jsonlite)
  library(readr)
  library(tibble)
  library(dplyr)
  library(lubridate)
})

# ---- Paths/constants --------------------------------------------------------

pdf_url <- Sys.getenv(
  "DELTA_OPS_SUMMARY_PDF_URL",
  unset = "https://water.ca.gov/-/media/DWR-Website/Web-Pages/Programs/State-Water-Project/Operations-And-Maintenance/Files/Operations-Control-Office/Delta-Status-And-Operations/Delta-Operations-Daily-Summary.pdf"
)

static_locations_csv <- Sys.getenv(
  "DELTA_OPS_STATIC_LOCATIONS_CSV",
  unset = "data/input/delta_ops_static_locations.csv"
)

x2_lookup_csv <- Sys.getenv(
  "DELTA_OPS_X2_LOOKUP_CSV",
  unset = "data/input/x2_river_km_lookup.csv"
)

out_dir <- Sys.getenv("DELTA_OPS_OUT_DIR", unset = "docs/data")
dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)

out_values_json <- file.path(out_dir, "delta_ops_daily_summary.json")
out_summary_json <- file.path(out_dir, "delta_ops_daily_summary_summary.json")
out_geojson <- file.path(out_dir, "delta_ops_daily_summary_features.geojson")
out_x2_geojson <- file.path(out_dir, "delta_ops_x2_reference.geojson")

local_tz <- "America/Los_Angeles"
feed_build_time <- Sys.time()
feed_build_time_utc <- format(lubridate::with_tz(feed_build_time, "UTC"), "%Y-%m-%dT%H:%M:%SZ")
feed_build_time_local <- format(lubridate::with_tz(feed_build_time, local_tz), "%Y-%m-%d %H:%M:%S %Z")
feed_build_date_local <- as.character(as.Date(lubridate::with_tz(feed_build_time, local_tz)))

# Skip repeated same-day fetches by default. This lets the GitHub Action run several
# morning retries without repeatedly downloading/parsing the PDF once today's report
# has already been published. Set DELTA_OPS_FORCE_REFRESH=true to override.
skip_if_current <- tolower(Sys.getenv("DELTA_OPS_SKIP_IF_CURRENT_DATE", unset = "true")) %in% c("true", "1", "yes", "y")
force_refresh <- tolower(Sys.getenv("DELTA_OPS_FORCE_REFRESH", unset = "false")) %in% c("true", "1", "yes", "y")

# Guardrail: report_date must come from the PDF text and must be plausible.
# The build date is only used to decide whether the parsed PDF date is plausible.
# It is never used as the data/report date.
max_report_lag_days <- suppressWarnings(as.integer(Sys.getenv("DELTA_OPS_MAX_REPORT_LAG_DAYS", unset = "7")))
if (is.na(max_report_lag_days) || max_report_lag_days < 0) max_report_lag_days <- 7L
allow_future_report_date <- tolower(Sys.getenv("DELTA_OPS_ALLOW_FUTURE_REPORT_DATE", unset = "false")) %in% c("true", "1", "yes", "y")

pt_existing_report_date <- function(path) {
  if (!file.exists(path)) return(NA_character_)
  x <- try(jsonlite::read_json(path, simplifyVector = TRUE), silent = TRUE)
  if (inherits(x, "try-error") || is.null(x$report_date)) return(NA_character_)
  as.character(x$report_date)[1]
}

existing_report_date <- pt_existing_report_date(out_summary_json)
if (skip_if_current && !force_refresh && !is.na(existing_report_date) && identical(existing_report_date, feed_build_date_local)) {
  message("Delta Ops output already exists for local date ", feed_build_date_local, "; skipping PDF download/parse. Set DELTA_OPS_FORCE_REFRESH=true to override.")
  quit(save = "no", status = 0)
}

# ---- Helpers ----------------------------------------------------------------

pt_chr <- function(x) {
  x <- as.character(x)
  x <- trimws(x)
  x[x == "" | is.na(x) | toupper(x) %in% c("NA", "NULL", "NAN")] <- NA_character_
  x
}

pt_num <- function(x) {
  x <- as.character(x)
  x <- gsub(",", "", x, fixed = TRUE)
  x <- gsub("[^0-9.\u2212-]", "", x)
  x <- gsub("\u2212", "-", x, fixed = TRUE)
  x[x == "" | x == "-" | x == "."] <- NA_character_
  suppressWarnings(as.numeric(x))
}

pt_clean_lines <- function(text) {
  lines <- unlist(strsplit(text, "\n", fixed = TRUE), use.names = FALSE)
  lines <- trimws(lines)
  lines[!is.na(lines) & lines != ""]
}

pt_find_first <- function(lines, pattern, start = 1L) {
  idx <- grep(pattern, lines, ignore.case = TRUE)
  idx <- idx[idx >= start]
  if (length(idx) == 0) return(NA_integer_)
  idx[[1]]
}

pt_section <- function(lines, start_pattern, end_patterns) {
  start <- pt_find_first(lines, start_pattern)
  if (is.na(start)) return(character())
  end_candidates <- integer()
  for (pat in end_patterns) {
    idx <- grep(pat, lines, ignore.case = TRUE)
    idx <- idx[idx > start]
    if (length(idx) > 0) end_candidates <- c(end_candidates, idx[[1]])
  }
  end <- if (length(end_candidates) > 0) min(end_candidates) - 1L else length(lines)
  lines[start:end]
}

pt_is_junk_or_label <- function(x, labels) {
  z <- trimws(as.character(x))
  if (z == "") return(TRUE)
  if (grepl("^=+$", z)) return(TRUE)
  if (grepl("^~+$", z)) return(TRUE)
  if (grepl("^[-_]+$", z)) return(TRUE)
  if (grepl("^https?://", z, ignore.case = TRUE)) return(TRUE)
  if (grepl("data and reports are available", z, ignore.case = TRUE)) return(TRUE)
  if (grepl("previous 30 days", z, ignore.case = TRUE)) return(TRUE)
  if (toupper(z) %in% toupper(labels)) return(TRUE)
  FALSE
}

pt_values_from_section <- function(section_lines, labels, n_expected) {
  vals <- character()
  for (ln in section_lines) {
    z <- trimws(ln)
    if (grepl("=", z, fixed = TRUE)) z <- trimws(sub("^.*=\\s*", "", z))
    if (pt_is_junk_or_label(z, labels)) next
    if (grepl("^(SCHEDULED EXPORTS|ESTIMATED DELTA HYDROLOGY|DELTA OPERATIONS|RESERVOIR STORAGES|RESERVOIR RELEASES)", z, ignore.case = TRUE)) next
    vals <- c(vals, z)
  }
  vals <- vals[vals != ""]
  if (length(vals) < n_expected) {
    stop("Could not parse enough values from section. Expected ", n_expected,
         "; got ", length(vals), ". Values found: ", paste(vals, collapse = " | "))
  }
  vals[seq_len(n_expected)]
}

pt_make_named <- function(values, keys) {
  out <- as.list(values)
  names(out) <- keys
  out
}

pt_safe_value <- function(x) {
  if (length(x) == 0 || is.null(x) || is.na(x)) return(NA_character_)
  as.character(x)
}

pt_format_cfs <- function(x) {
  n <- pt_num(x)
  if (is.na(n)) return(NA_character_)
  paste0(format(round(n), big.mark = ",", scientific = FALSE), " cfs")
}

pt_format_taf <- function(x) {
  n <- pt_num(x)
  if (is.na(n)) return(NA_character_)
  paste0(format(round(n), big.mark = ",", scientific = FALSE), " TAF")
}

pt_format_percent <- function(x) {
  raw <- pt_chr(x)
  if (is.na(raw)) return(NA_character_)
  if (grepl("%", raw, fixed = TRUE)) return(raw)
  n <- pt_num(raw)
  if (is.na(n)) return(raw)
  paste0(n, "%")
}

pt_read_locations <- function(path) {
  if (!file.exists(path)) stop("Static locations CSV not found: ", path)
  x <- readr::read_csv(path, show_col_types = FALSE, col_types = readr::cols(.default = readr::col_character()))
  required <- c("feature_key", "display_name", "feature_type", "lat", "lon")
  missing <- setdiff(required, names(x))
  if (length(missing) > 0) stop("Static locations CSV missing required column(s): ", paste(missing, collapse = ", "))
  x$lat <- pt_num(x$lat)
  x$lon <- pt_num(x$lon)
  if (!"sort_order" %in% names(x)) x$sort_order <- seq_len(nrow(x))
  x$sort_order <- suppressWarnings(as.integer(x$sort_order))
  for (nm in c("label_dx", "label_dy")) {
    if (!nm %in% names(x)) x[[nm]] <- 0
    x[[nm]] <- suppressWarnings(as.numeric(x[[nm]]))
    x[[nm]][is.na(x[[nm]])] <- 0
  }
  x
}

pt_geojson_from_df <- function(df, name = "BRIM Delta Ops Daily Summary") {
  features <- lapply(seq_len(nrow(df)), function(i) {
    row <- as.list(df[i, , drop = FALSE])
    lon <- as.numeric(row$lon)
    lat <- as.numeric(row$lat)
    row$lon <- NULL
    row$lat <- NULL
    list(
      type = "Feature",
      geometry = list(type = "Point", coordinates = list(lon, lat)),
      properties = row
    )
  })
  list(type = "FeatureCollection", name = name, features = features)
}

# ---- Download/extract PDF ---------------------------------------------------

pdf_tmp <- tempfile(fileext = ".pdf")
message("Downloading DWR Delta Operations Daily Summary PDF...")
curl::curl_download(pdf_url, pdf_tmp, quiet = FALSE, mode = "wb")

raw_text <- paste(pdftools::pdf_text(pdf_tmp), collapse = "\n")
lines <- pt_clean_lines(raw_text)
if (length(lines) < 20) stop("PDF text extraction returned too few lines; cannot parse Delta Ops summary.")

# ---- Parse values -----------------------------------------------------------

report_date_raw <- NA_character_
report_line <- lines[grep("EXECUTIVE OPERATIONS SUMMARY ON", lines, ignore.case = TRUE)][1]

if (!is.na(report_line)) {
  report_date_raw <- sub(".*SUMMARY ON\\s+", "", report_line, ignore.case = TRUE)
  report_date_raw <- trimws(report_date_raw)
  report_date_raw <- sub("\\s+.*$", "", report_date_raw)
}

if (is.na(report_date_raw) || !grepl("^\\d{1,2}/\\d{1,2}/\\d{4}$", report_date_raw)) {
  stop(
    "Could not find an explicit MM/DD/YYYY report date on the PDF 'EXECUTIVE OPERATIONS SUMMARY ON' line. ",
    "Refusing to publish because report_date must be parsed from the PDF, not inferred from build time. ",
    "report_line=", paste0(report_line, collapse = " | ")
  )
}

report_date <- suppressWarnings(as.Date(report_date_raw, format = "%m/%d/%Y"))
if (is.na(report_date)) {
  stop("Could not parse report date from PDF text. report_date_raw=", report_date_raw)
}

build_local_date <- as.Date(feed_build_date_local)
if (!allow_future_report_date && report_date > build_local_date) {
  stop(
    "Parsed PDF report_date is in the future relative to the GitHub/Pacific build date. ",
    "report_date=", as.character(report_date), "; build_local_date=", as.character(build_local_date), ". ",
    "Refusing to publish."
  )
}

lag_days <- as.integer(build_local_date - report_date)
if (!force_refresh && !is.na(lag_days) && lag_days > max_report_lag_days) {
  stop(
    "Parsed PDF report_date is older than the configured guardrail. ",
    "report_date=", as.character(report_date), "; build_local_date=", as.character(build_local_date),
    "; lag_days=", lag_days, "; max_report_lag_days=", max_report_lag_days, ". ",
    "Refusing to publish. Set DELTA_OPS_FORCE_REFRESH=true or increase DELTA_OPS_MAX_REPORT_LAG_DAYS if this is intentional."
  )
}

scheduled_labels <- c("Clifton Court Inflow", "Jones Pumping Plant")
hydro_labels <- c("Total Delta Inflow", "Sacramento River", "San Joaquin River")
ops_labels <- c(
  "Delta Conditions",
  "Delta x-channel Gates (% of day is open)",
  "Outflow Index",
  "% Inflow Diverted",
  "X2 Position (yesterday)",
  "Controlling Factor(s)",
  "OMR Index Daily Value"
)
storage_labels <- c("Shasta Reservoir", "Folsom Reservoir", "Oroville Reservoir", "San Luis Res. Total", "SWP Share")

scheduled_vals <- pt_values_from_section(pt_section(lines, "SCHEDULED EXPORTS FOR TODAY", c("ESTIMATED DELTA HYDROLOGY")), scheduled_labels, 2L)
hydro_vals <- pt_values_from_section(pt_section(lines, "ESTIMATED DELTA HYDROLOGY", c("DELTA OPERATIONS")), hydro_labels, 3L)
ops_vals <- pt_values_from_section(pt_section(lines, "DELTA OPERATIONS", c("RESERVOIR STORAGES")), ops_labels, 7L)
storage_vals <- pt_values_from_section(pt_section(lines, "RESERVOIR STORAGES", c("RESERVOIR RELEASES")), storage_labels, 5L)

vals <- c(
  pt_make_named(scheduled_vals, c("clifton_court_inflow", "jones_pumping_plant")),
  pt_make_named(hydro_vals, c("total_delta_inflow", "sacramento_river", "san_joaquin_river")),
  pt_make_named(ops_vals, c("delta_conditions", "cross_channel_gates_percent_open", "outflow_index", "percent_inflow_diverted", "x2_position_yesterday", "controlling_factors", "omr_index_daily_value")),
  pt_make_named(storage_vals, c("shasta_storage", "folsom_storage", "oroville_storage", "san_luis_total_storage", "san_luis_swp_share"))
)

san_luis_total_taf <- pt_num(vals$san_luis_total_storage)
san_luis_swp_taf <- pt_num(vals$san_luis_swp_share)
san_luis_cvp_taf <- if (!is.na(san_luis_total_taf) && !is.na(san_luis_swp_taf)) san_luis_total_taf - san_luis_swp_taf else NA_real_
x2_km <- pt_num(vals$x2_position_yesterday)
sac_cfs <- pt_num(vals$sacramento_river)
sj_cfs <- pt_num(vals$san_joaquin_river)
total_inflow_cfs <- pt_num(vals$total_delta_inflow)
east_side_cfs <- if (all(is.finite(c(total_inflow_cfs, sac_cfs, sj_cfs)))) total_inflow_cfs - sac_cfs - sj_cfs else NA_real_

parsed <- list(
  source_url = pdf_url,
  source_name = "DWR Delta Operations Daily Summary",
  report_date = as.character(report_date),
  report_date_raw = report_date_raw,
  report_date_source = "parsed_from_dwr_pdf_executive_operations_summary_on_line",
  report_date_guard_build_local_date = as.character(build_local_date),
  report_date_guard_lag_days = lag_days,
  feed_build_time_utc = feed_build_time_utc,
  feed_build_time_local = feed_build_time_local,
  preliminary_notice = "PRELIMINARY DATA; SUBJECT TO REVISION WITHOUT NOTICE",
  values = c(vals, list(
    east_side_streams = if (!is.na(east_side_cfs)) paste0(format(round(east_side_cfs), big.mark = ",", scientific = FALSE), " cfs") else NA_character_,
    san_luis_cvp_share = if (!is.na(san_luis_cvp_taf)) paste0(format(round(san_luis_cvp_taf), big.mark = ",", scientific = FALSE), " TAF") else NA_character_
  ))
)

# If we had to download because the existing file was not current, but the PDF still
# resolves to the same report_date as the existing outputs, stop before rewriting.
# This avoids unnecessary commits on retry runs when DWR has not posted a newer PDF yet.
if (skip_if_current && !force_refresh && !is.na(existing_report_date) && identical(existing_report_date, as.character(report_date))) {
  message("Existing Delta Ops output already has parsed report date ", as.character(report_date), "; skipping writes. Set DELTA_OPS_FORCE_REFRESH=true to override.")
  quit(save = "no", status = 0)
}

# ---- Feature rows -----------------------------------------------------------

loc <- pt_read_locations(static_locations_csv)

metric_rows <- tibble::tibble(
  feature_key = c(
    "cross_channel_gates", "jones_cvp_exports", "banks_swp_exports",
    "delta_outflow_index", "percent_inflow_diverted", "omr_index",
    "sacramento_freeport", "san_joaquin_vernalis", "total_delta_inflow",
    "delta_conditions", "controlling_factors", "san_luis_reservoir"
  ),
  metric_name = c(
    "Delta Cross Channel Gates", "Jones / CVP exports", "Banks / SWP exports",
    "Delta Outflow Index / NDOI", "Percent Inflow Diverted", "OMR Index",
    "Sacramento River input", "San Joaquin River input", "East Side Streams contribution",
    "Delta Conditions", "Controlling Factor(s)", "San Luis Reservoir storage split"
  ),
  value_raw = c(
    vals$cross_channel_gates_percent_open,
    vals$jones_pumping_plant,
    vals$clifton_court_inflow,
    vals$outflow_index,
    vals$percent_inflow_diverted,
    vals$omr_index_daily_value,
    vals$sacramento_river,
    vals$san_joaquin_river,
    if (!is.na(east_side_cfs)) paste0(format(round(east_side_cfs), big.mark = ",", scientific = FALSE), " cfs") else NA_character_,
    vals$delta_conditions,
    vals$controlling_factors,
    paste0("Total ", pt_format_taf(vals$san_luis_total_storage), " | SWP ", pt_format_taf(vals$san_luis_swp_share), " | CVP ", if (!is.na(san_luis_cvp_taf)) paste0(format(round(san_luis_cvp_taf), big.mark = ",", scientific = FALSE), " TAF") else "NA")
  ),
  value_numeric = c(
    pt_num(vals$cross_channel_gates_percent_open),
    pt_num(vals$jones_pumping_plant),
    pt_num(vals$clifton_court_inflow),
    pt_num(vals$outflow_index),
    pt_num(vals$percent_inflow_diverted),
    pt_num(vals$omr_index_daily_value),
    pt_num(vals$sacramento_river),
    pt_num(vals$san_joaquin_river),
    east_side_cfs,
    NA_real_, NA_real_, san_luis_total_taf
  ),
  units = c("percent", "cfs", "cfs", "cfs", "percent_3day_avg", "cfs", "cfs", "cfs", "cfs", "status", "status", "TAF"),
  label_text = c(
    paste0("Delta X-Channel Gates | ", pt_format_percent(vals$cross_channel_gates_percent_open), " open"),
    paste0("Jones/CVP: ", pt_format_cfs(vals$jones_pumping_plant)),
    paste0("Banks/SWP: ", pt_format_cfs(vals$clifton_court_inflow)),
    paste0("Outflow: ", pt_format_cfs(vals$outflow_index)),
    paste0("Diverted: ", pt_format_percent(vals$percent_inflow_diverted), " (3-day avg)"),
    paste0("OMR: ", pt_format_cfs(vals$omr_index_daily_value)),
    paste0("Sac: ", pt_format_cfs(vals$sacramento_river)),
    paste0("SJ: ", pt_format_cfs(vals$san_joaquin_river)),
    paste0("East side streams: ", if (!is.na(east_side_cfs)) paste0(format(round(east_side_cfs), big.mark = ",", scientific = FALSE), " cfs") else "NA cfs"),
    paste0("Delta: ", pt_safe_value(vals$delta_conditions)),
    paste0("Export control: ", pt_safe_value(vals$controlling_factors)),
    paste0("San Luis: Total ", if (!is.na(san_luis_total_taf)) format(round(san_luis_total_taf), big.mark = ",", scientific = FALSE) else "NA",
           " | SWP ", if (!is.na(san_luis_swp_taf)) format(round(san_luis_swp_taf), big.mark = ",", scientific = FALSE) else "NA",
           " | CVP ", if (!is.na(san_luis_cvp_taf)) format(round(san_luis_cvp_taf), big.mark = ",", scientific = FALSE) else "NA", " TAF")
  ),
  symbol_class = c("gate", "export_cvp", "export_swp", "outflow", "diversion_percent", "omr", "inflow", "inflow", "inflow_east_side", "status", "control", "storage_split")
)

features <- loc |>
  dplyr::inner_join(metric_rows, by = "feature_key") |>
  dplyr::mutate(
    source_name = "DWR Delta Operations Daily Summary",
    source_url = pdf_url,
    report_date = as.character(report_date),
    feed_build_time_utc = feed_build_time_utc,
    feed_build_time_local = feed_build_time_local,
    preliminary_notice = "PRELIMINARY DATA; SUBJECT TO REVISION WITHOUT NOTICE"
  ) |>
  dplyr::arrange(.data$sort_order)

x2_lookup_added <- FALSE
if (file.exists(x2_lookup_csv) && !is.na(x2_km)) {
  x2 <- readr::read_csv(x2_lookup_csv, show_col_types = FALSE, col_types = readr::cols(.default = readr::col_character()))
  required_x2 <- c("river_km", "lon", "lat")
  missing_x2 <- setdiff(required_x2, names(x2))
  if (length(missing_x2) > 0) stop("X2 lookup is missing required column(s): ", paste(missing_x2, collapse = ", "))

  x2 <- x2 |>
    dplyr::mutate(
      river_km = pt_num(.data$river_km),
      lon = pt_num(.data$lon),
      lat = pt_num(.data$lat),
      km_diff = abs(.data$river_km - x2_km)
    ) |>
    dplyr::filter(!is.na(.data$river_km), !is.na(.data$lon), !is.na(.data$lat)) |>
    dplyr::arrange(.data$river_km)

  x2_reference <- x2 |>
    dplyr::transmute(
      feature_key = "x2_reference",
      display_name = paste0("X2 ", .data$river_km, " km"),
      feature_type = "x2_reference",
      river_km = .data$river_km,
      lon = .data$lon,
      lat = .data$lat,
      label_text = as.character(.data$river_km),
      source_name = "BRIM X2 river-km reference lookup",
      source_url = "01_raw_data/misc_reference/x2_km.shp"
    )

  jsonlite::write_json(pt_geojson_from_df(x2_reference, "BRIM X2 river-km reference points"), out_x2_geojson, auto_unbox = TRUE, pretty = TRUE, na = "null")

  x2_nearest <- x2 |>
    dplyr::arrange(.data$km_diff) |>
    dplyr::slice(1)

  features <- dplyr::bind_rows(
    features,
    tibble::tibble(
      feature_key = "x2_position_current",
      display_name = "X2 Position",
      feature_type = "x2_current",
      lat = x2_nearest$lat,
      lon = x2_nearest$lon,
      label_anchor = "top",
      label_dx = 0,
      label_dy = 0,
      sort_order = 130L,
      placement_note = "Current X2 position snapped to nearest available river-km reference point.",
      metric_name = "X2 Position (yesterday)",
      value_raw = vals$x2_position_yesterday,
      value_numeric = x2_km,
      units = "km",
      label_text = paste0("X2: ", format(round(x2_km), big.mark = ",", scientific = FALSE), " km"),
      symbol_class = "x2_current",
      source_name = "DWR Delta Operations Daily Summary",
      source_url = pdf_url,
      report_date = as.character(report_date),
      feed_build_time_utc = feed_build_time_utc,
      feed_build_time_local = feed_build_time_local,
      preliminary_notice = "PRELIMINARY DATA; SUBJECT TO REVISION WITHOUT NOTICE"
    )
  )
  x2_lookup_added <- TRUE
} else {
  warning("X2 lookup file not found or X2 value missing; X2 current/reference layer will be incomplete.")
  jsonlite::write_json(pt_geojson_from_df(features[0, ], "BRIM X2 river-km reference points"), out_x2_geojson, auto_unbox = TRUE, pretty = TRUE, na = "null")
}

# ---- Validate and write -----------------------------------------------------

required_feature_keys <- metric_rows$feature_key
missing_features <- setdiff(required_feature_keys, features$feature_key)
if (length(missing_features) > 0) stop("Missing expected dashboard features after location join: ", paste(missing_features, collapse = ", "))
if (nrow(features) < 10) stop("Too few Delta Ops dashboard features to publish: ", nrow(features))
if (!is.na(x2_km) && !x2_lookup_added) stop("Parsed X2 value but did not add the X2 lookup feature.")

geojson <- pt_geojson_from_df(features)

summary <- list(
  source_name = "DWR Delta Operations Daily Summary",
  source_url = pdf_url,
  report_date = as.character(report_date),
  report_date_source = "parsed_from_dwr_pdf_executive_operations_summary_on_line",
  report_date_guard_build_local_date = as.character(build_local_date),
  report_date_guard_lag_days = lag_days,
  feed_build_time_utc = feed_build_time_utc,
  feed_build_time_local = feed_build_time_local,
  feature_count = nrow(features),
  static_location_count = nrow(loc),
  x2_position_km = x2_km,
  x2_lookup_added = x2_lookup_added,
  east_side_streams_cfs = east_side_cfs,
  preliminary_notice = "PRELIMINARY DATA; SUBJECT TO REVISION WITHOUT NOTICE",
  parser_note = "Automated BRIM daily parser for DWR Delta Operations Daily Summary PDF; values are preliminary and subject to revision."
)

jsonlite::write_json(parsed, out_values_json, auto_unbox = TRUE, pretty = TRUE, na = "null")
jsonlite::write_json(summary, out_summary_json, auto_unbox = TRUE, pretty = TRUE, na = "null")
jsonlite::write_json(geojson, out_geojson, auto_unbox = TRUE, pretty = TRUE, na = "null")

message("Delta Ops report date: ", as.character(report_date))
message("Features written: ", nrow(features))
message("Wrote: ", out_values_json)
message("Wrote: ", out_summary_json)
message("Wrote: ", out_geojson)
message("Wrote: ", out_x2_geojson)
message("Key parsed values:")
message("  X2: ", vals$x2_position_yesterday)
message("  X-Channel gates: ", vals$cross_channel_gates_percent_open)
message("  Jones / CVP: ", vals$jones_pumping_plant)
message("  Banks / SWP: ", vals$clifton_court_inflow)
message("  Outflow: ", vals$outflow_index)
message("  OMR: ", vals$omr_index_daily_value)
message("  East side streams: ", if (!is.na(east_side_cfs)) paste0(round(east_side_cfs), " cfs") else "NA")
message("  San Luis total/SWP/CVP: ", vals$san_luis_total_storage, " / ", vals$san_luis_swp_share, " / ", if (!is.na(san_luis_cvp_taf)) paste0(round(san_luis_cvp_taf), " TAF") else "NA")
