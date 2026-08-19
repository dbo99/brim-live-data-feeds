# BRIM HRRR wind production feed builder --------------------------------------
#
# PURPOSE
#   Publish a compact, browser-ready NOAA/NCEP HRRR 10-m wind feed over the
#   Hydrologic California + adjacent basins domain used by BRIM METAR/ASOS.
#
# METHOD
#   1. Try the NOAA NOMADS HRRR GRIB filter for only 10-m UGRD/VGRD.
#   2. Fall back to NOAA's public HRRR AWS bucket using GRIB byte ranges.
#   3. Use wgrib2 vector-aware interpolation with -new_grid_winds earth.
#   4. Regrid to a regular 0.05-degree lon/lat grid for leaflet-velocity.
#   5. Publish Current, +6 hr, and +12 hr target fields in one hourly run,
#      plus backward-compatible latest JSON/summary files.
#
# REQUIREMENT
#   wgrib2 must be on PATH and include -new_grid support. The companion GitHub
#   workflow installs the conda-forge wgrib2 package.

brim_hrrr_env <- function(name, default = "") {
  value <- Sys.getenv(name, unset = default)
  if (!nzchar(value)) default else value
}

brim_hrrr_bool <- function(x, default = FALSE) {
  if (is.null(x) || !nzchar(x)) return(default)
  tolower(x) %in% c("1", "true", "yes", "y", "on")
}

brim_hrrr_parse_integer_csv <- function(x, default) {
  if (is.null(x) || length(x) == 0L || is.na(x)) x <- ""
  x <- trimws(as.character(x))
  if (!nzchar(x)) return(as.integer(default))
  values <- suppressWarnings(as.integer(trimws(strsplit(x, ",", fixed = TRUE)[[1]])))
  values <- values[is.finite(values) & values >= 0L]
  if (!length(values)) as.integer(default) else sort(unique(values))
}

brim_hrrr_as_utc <- function(x) {
  if (inherits(x, "POSIXt")) {
    return(as.POSIXct(as.numeric(x), origin = "1970-01-01", tz = "UTC"))
  }
  if (is.numeric(x)) {
    return(as.POSIXct(x, origin = "1970-01-01", tz = "UTC"))
  }
  as.POSIXct(x, tz = "UTC")
}

brim_hrrr_floor_hour <- function(x) {
  x <- brim_hrrr_as_utc(x)
  as.POSIXct(format(x, "%Y-%m-%d %H:00:00", tz = "UTC"), tz = "UTC")
}

brim_hrrr_floor_second <- function(x) {
  x <- brim_hrrr_as_utc(x)
  brim_hrrr_as_utc(floor(as.numeric(x)))
}

brim_hrrr_summary_freshness <- function(build_time, valid_time, stale_after_hours) {
  # JSON timestamps are serialized to whole seconds, so normalize first and
  # derive every companion freshness field from that exact instant.
  build_time <- brim_hrrr_floor_second(build_time)
  valid_time <- brim_hrrr_as_utc(valid_time)
  valid_lag_minutes <- unname(
    as.numeric(difftime(build_time, valid_time, units = "mins"))
  )
  valid_time_age_hours <- valid_lag_minutes / 60
  list(
    build_time = build_time,
    valid_lag_minutes_at_build = valid_lag_minutes,
    valid_time_age_hours = valid_time_age_hours,
    # Equality remains fresh; an entry becomes stale only after four hours.
    is_stale = isTRUE(valid_time_age_hours > stale_after_hours)
  )
}

brim_hrrr_fmt_iso_utc <- function(x) {
  format(brim_hrrr_as_utc(x), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
}

brim_hrrr_fmt_local <- function(x, tz = "America/Los_Angeles") {
  format(brim_hrrr_as_utc(x), "%Y-%m-%d %I:%M:%S %p %Z", tz = tz)
}

brim_hrrr_project_root <- normalizePath(
  brim_hrrr_env("BRIM_HRRR_PROJECT_ROOT", getwd()),
  winslash = "/",
  mustWork = FALSE
)
if (!dir.exists(brim_hrrr_project_root)) {
  stop("Project root does not exist: ", brim_hrrr_project_root)
}
setwd(brim_hrrr_project_root)

brim_hrrr_version <- "RTW-HRRR002"
brim_hrrr_local_tz <- brim_hrrr_env("BRIM_HRRR_LOCAL_TZ", "America/Los_Angeles")
brim_hrrr_force_refresh <- brim_hrrr_bool(brim_hrrr_env("BRIM_HRRR_FORCE_REFRESH", "false"), FALSE)
brim_hrrr_cycle_lookback_hours <- as.integer(brim_hrrr_env("BRIM_HRRR_CYCLE_LOOKBACK_HOURS", "8"))
brim_hrrr_target_lead_hours <- brim_hrrr_parse_integer_csv(
  brim_hrrr_env("BRIM_HRRR_TARGET_LEAD_HOURS", "0,6,12"),
  c(0L, 6L, 12L)
)
brim_hrrr_max_forecast_hour <- as.integer(brim_hrrr_env("BRIM_HRRR_MAX_FORECAST_HOUR", "18"))
brim_hrrr_request_timeout_seconds <- as.numeric(brim_hrrr_env("BRIM_HRRR_REQUEST_TIMEOUT_SECONDS", "120"))
brim_hrrr_min_grib_bytes <- as.integer(brim_hrrr_env("BRIM_HRRR_MIN_GRIB_BYTES", "10000"))
brim_hrrr_min_json_bytes <- as.integer(brim_hrrr_env("BRIM_HRRR_MIN_JSON_BYTES", "200000"))
brim_hrrr_stale_after_hours <- as.numeric(brim_hrrr_env("BRIM_HRRR_STALE_AFTER_HOURS", "4"))
brim_hrrr_retain_past_hours <- as.numeric(brim_hrrr_env("BRIM_HRRR_RETAIN_PAST_HOURS", "4"))
brim_hrrr_retain_future_hours <- as.numeric(brim_hrrr_env("BRIM_HRRR_RETAIN_FUTURE_HOURS", "14"))
brim_hrrr_max_manifest_entries <- as.integer(brim_hrrr_env("BRIM_HRRR_MAX_MANIFEST_ENTRIES", "24"))

# Same footprint as the BRIM observed METAR/ASOS feed. It intentionally covers
# all of California plus southern Oregon, Nevada, western Arizona, nearby ocean,
# and the Pit/Walker/Carson/Amargosa/lower-Colorado context.
brim_hrrr_domain <- list(
  id = "hydrologic_ca_adjacent",
  label = "Hydrologic California + adjacent basins",
  west = as.numeric(brim_hrrr_env("BRIM_HRRR_WEST", "-125.5")),
  east = as.numeric(brim_hrrr_env("BRIM_HRRR_EAST", "-112.0")),
  south = as.numeric(brim_hrrr_env("BRIM_HRRR_SOUTH", "31.0")),
  north = as.numeric(brim_hrrr_env("BRIM_HRRR_NORTH", "43.5")),
  resolution_degrees = as.numeric(brim_hrrr_env("BRIM_HRRR_RESOLUTION_DEGREES", "0.05")),
  request_buffer_degrees = as.numeric(brim_hrrr_env("BRIM_HRRR_REQUEST_BUFFER_DEGREES", "1.0"))
)

if (!is.finite(brim_hrrr_domain$resolution_degrees) || brim_hrrr_domain$resolution_degrees <= 0) {
  stop("BRIM_HRRR_RESOLUTION_DEGREES must be positive.")
}

brim_hrrr_dirs <- list(
  cache = file.path(brim_hrrr_project_root, "data", "cache", "wind", "hrrr"),
  debug = file.path(brim_hrrr_project_root, "debug", "wind", "hrrr"),
  qa = file.path(brim_hrrr_project_root, "qa", "wind", "hrrr"),
  publish = file.path(brim_hrrr_project_root, "docs", "data", "wind"),
  timeset = file.path(brim_hrrr_project_root, "docs", "data", "wind", "hrrr", "surface")
)
for (d in brim_hrrr_dirs) dir.create(d, recursive = TRUE, showWarnings = FALSE)

brim_hrrr_files <- list(
  wind_json = file.path(brim_hrrr_dirs$publish, "hrrr_surface_wind_latest.json"),
  summary_json = file.path(brim_hrrr_dirs$publish, "hrrr_surface_wind_latest_summary.json"),
  manifest_json = file.path(brim_hrrr_dirs$publish, "hrrr_surface_wind_feed_manifest.json")
)

brim_hrrr_install_missing <- function(pkgs) {
  missing <- pkgs[!vapply(pkgs, requireNamespace, quietly = TRUE, FUN.VALUE = logical(1))]
  if (length(missing)) install.packages(missing, repos = "https://cloud.r-project.org")
}
brim_hrrr_install_missing(c("httr2", "jsonlite", "terra"))

brim_hrrr_wgrib2 <- Sys.which("wgrib2")
if (!nzchar(brim_hrrr_wgrib2)) {
  stop(
    "wgrib2 was not found on PATH. Run the companion GitHub production workflow, ",
    "or install wgrib2 locally before sourcing this script."
  )
}

brim_hrrr_stamp <- format(Sys.time(), "%Y%m%d_%H%M%S")
brim_hrrr_log_file <- file.path(brim_hrrr_dirs$debug, paste0("hrrr_wind_run_", brim_hrrr_stamp, ".log"))

brim_hrrr_log <- function(...) {
  text <- paste0(...)
  message(text)
  try(cat(text, "\n", file = brim_hrrr_log_file, append = TRUE, sep = ""), silent = TRUE)
}

brim_hrrr_run <- function(command, args, label = basename(command)) {
  output <- system2(command, args = args, stdout = TRUE, stderr = TRUE)
  status <- attr(output, "status")
  if (is.null(status)) status <- 0L
  if (!identical(as.integer(status), 0L)) {
    stop(label, " failed with status ", status, ":\n", paste(output, collapse = "\n"))
  }
  output
}

brim_hrrr_log("Starting ", brim_hrrr_version)
brim_hrrr_log("Project root: ", brim_hrrr_project_root)
brim_hrrr_log("wgrib2: ", brim_hrrr_wgrib2)

# wgrib2 3.8.0 can print its version but return status 8 when no GRIB input
# file is supplied. The executable path check above is authoritative; keep the
# version call as a nonfatal diagnostic rather than passing it through the
# strict command runner used for real GRIB operations.
brim_hrrr_wgrib2_version <- suppressWarnings(
  system2(brim_hrrr_wgrib2, args = "-version", stdout = TRUE, stderr = TRUE)
)
if (length(brim_hrrr_wgrib2_version)) {
  brim_hrrr_log(
    "wgrib2 version probe: ",
    paste(brim_hrrr_wgrib2_version, collapse = " | ")
  )
} else {
  brim_hrrr_log("wgrib2 version probe returned no text; continuing.")
}

brim_hrrr_url <- function(base, query) {
  paste0(
    base,
    "?",
    paste0(
      utils::URLencode(names(query), reserved = TRUE),
      "=",
      utils::URLencode(as.character(unname(query)), reserved = TRUE),
      collapse = "&"
    )
  )
}

brim_hrrr_nomads_url <- function(cycle_time, forecast_hour) {
  cycle_time <- brim_hrrr_as_utc(cycle_time)
  ymd <- format(cycle_time, "%Y%m%d", tz = "UTC")
  hh <- format(cycle_time, "%H", tz = "UTC")
  ff <- sprintf("%02d", as.integer(forecast_hour))
  b <- brim_hrrr_domain$request_buffer_degrees

  query <- c(
    file = sprintf("hrrr.t%sz.wrfsfcf%s.grib2", hh, ff),
    lev_10_m_above_ground = "on",
    var_UGRD = "on",
    var_VGRD = "on",
    subregion = "",
    leftlon = brim_hrrr_domain$west - b,
    rightlon = brim_hrrr_domain$east + b,
    toplat = brim_hrrr_domain$north + b,
    bottomlat = brim_hrrr_domain$south - b,
    dir = sprintf("/hrrr.%s/conus", ymd)
  )

  brim_hrrr_url("https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl", query)
}

brim_hrrr_aws_urls <- function(cycle_time, forecast_hour) {
  cycle_time <- brim_hrrr_as_utc(cycle_time)
  ymd <- format(cycle_time, "%Y%m%d", tz = "UTC")
  hh <- format(cycle_time, "%H", tz = "UTC")
  ff <- sprintf("%02d", as.integer(forecast_hour))
  key <- sprintf("hrrr.%s/conus/hrrr.t%sz.wrfsfcf%s.grib2", ymd, hh, ff)
  base <- paste0("https://noaa-hrrr-bdp-pds.s3.amazonaws.com/", key)
  list(grib = base, idx = paste0(base, ".idx"), key = key)
}

brim_hrrr_candidate_table <- function(target_utc, now_utc = Sys.time()) {
  now_utc <- brim_hrrr_as_utc(now_utc)
  target_utc <- brim_hrrr_as_utc(target_utc)
  current_hour <- brim_hrrr_floor_hour(now_utc)
  cycles <- current_hour - seq.int(0L, brim_hrrr_cycle_lookback_hours) * 3600
  rows <- list()
  for (cycle_index in seq_along(cycles)) {
    cycle <- brim_hrrr_as_utc(cycles[cycle_index])
    for (forecast_hour in 0:brim_hrrr_max_forecast_hour) {
      valid <- cycle + forecast_hour * 3600
      rows[[length(rows) + 1L]] <- data.frame(
        cycle_time = cycle,
        forecast_hour = as.integer(forecast_hour),
        valid_time = valid,
        target_time = target_utc,
        distance_minutes = abs(as.numeric(difftime(valid, target_utc, units = "mins"))),
        is_after_target = valid > target_utc,
        stringsAsFactors = FALSE
      )
    }
  }
  out <- do.call(rbind, rows)
  out <- out[out$distance_minutes <= 180, , drop = FALSE]
  out <- out[
    order(
      out$distance_minutes,
      out$is_after_target,
      -as.numeric(out$cycle_time),
      out$forecast_hour
    ),
    ,
    drop = FALSE
  ]
  rownames(out) <- NULL
  out
}

brim_hrrr_http_download <- function(url, dest, min_bytes = brim_hrrr_min_grib_bytes) {
  req <- httr2::request(url) |>
    httr2::req_user_agent("BRIM-HRRR-wind-feed/1.0") |>
    httr2::req_timeout(brim_hrrr_request_timeout_seconds) |>
    httr2::req_retry(max_tries = 2)
  resp <- httr2::req_perform(req, path = dest)
  status <- httr2::resp_status(resp)
  bytes <- if (file.exists(dest)) unname(file.info(dest)$size) else 0
  if (status != 200L || is.na(bytes) || bytes < min_bytes) {
    if (file.exists(dest)) unlink(dest)
    stop("HTTP ", status, "; downloaded bytes: ", bytes)
  }
  list(path = dest, bytes = bytes, status = status)
}

brim_hrrr_download_nomads <- function(cycle_time, forecast_hour, dest) {
  url <- brim_hrrr_nomads_url(cycle_time, forecast_hour)
  brim_hrrr_log("NOMADS request: ", url)
  result <- brim_hrrr_http_download(url, dest)
  c(result, list(source = "NOAA NOMADS GRIB filter", source_url = url))
}

brim_hrrr_parse_idx <- function(text) {
  lines <- strsplit(text, "\n", fixed = TRUE)[[1]]
  lines <- lines[nzchar(lines)]
  parts <- strsplit(lines, ":", fixed = TRUE)
  offsets <- suppressWarnings(vapply(parts, function(x) if (length(x) >= 2L) as.numeric(x[2]) else NA_real_, numeric(1)))
  data.frame(line = lines, offset = offsets, stringsAsFactors = FALSE)
}

brim_hrrr_fetch_text <- function(url) {
  req <- httr2::request(url) |>
    httr2::req_user_agent("BRIM-HRRR-wind-feed/1.0") |>
    httr2::req_timeout(brim_hrrr_request_timeout_seconds) |>
    httr2::req_retry(max_tries = 2)
  resp <- httr2::req_perform(req)
  if (httr2::resp_status(resp) != 200L) stop("HTTP ", httr2::resp_status(resp), " for ", url)
  httr2::resp_body_string(resp)
}

brim_hrrr_fetch_range <- function(url, start, end) {
  if (!is.finite(start) || !is.finite(end) || end < start) stop("Invalid HTTP byte range.")
  req <- httr2::request(url) |>
    httr2::req_user_agent("BRIM-HRRR-wind-feed/1.0") |>
    httr2::req_headers(Range = sprintf("bytes=%.0f-%.0f", start, end)) |>
    httr2::req_timeout(brim_hrrr_request_timeout_seconds) |>
    httr2::req_retry(max_tries = 2)
  resp <- httr2::req_perform(req)
  status <- httr2::resp_status(resp)
  if (status != 206L) stop("Expected HTTP 206 for range request; received ", status)
  httr2::resp_body_raw(resp)
}

brim_hrrr_download_aws_ranges <- function(cycle_time, forecast_hour, dest) {
  urls <- brim_hrrr_aws_urls(cycle_time, forecast_hour)
  brim_hrrr_log("AWS index request: ", urls$idx)
  idx <- brim_hrrr_parse_idx(brim_hrrr_fetch_text(urls$idx))
  if (!nrow(idx) || anyNA(idx$offset)) stop("Could not parse HRRR AWS GRIB index.")

  u_idx <- grep(":UGRD:10 m above ground:", idx$line, fixed = TRUE)
  v_idx <- grep(":VGRD:10 m above ground:", idx$line, fixed = TRUE)
  if (!length(u_idx) || !length(v_idx)) {
    stop("AWS index did not contain both 10-m UGRD and VGRD records.")
  }
  u_idx <- u_idx[1]
  v_idx <- v_idx[1]
  if (u_idx >= nrow(idx) || v_idx >= nrow(idx)) stop("Could not determine byte-range end from AWS index.")

  u_raw <- brim_hrrr_fetch_range(urls$grib, idx$offset[u_idx], idx$offset[u_idx + 1L] - 1)
  v_raw <- brim_hrrr_fetch_range(urls$grib, idx$offset[v_idx], idx$offset[v_idx + 1L] - 1)

  con <- file(dest, open = "wb")
  on.exit(close(con), add = TRUE)
  writeBin(u_raw, con)
  writeBin(v_raw, con)
  close(con)
  on.exit(NULL, add = FALSE)

  bytes <- unname(file.info(dest)$size)
  if (is.na(bytes) || bytes < brim_hrrr_min_grib_bytes) {
    if (file.exists(dest)) unlink(dest)
    stop("AWS U/V subset was unexpectedly small: ", bytes, " bytes")
  }
  list(
    path = dest,
    bytes = bytes,
    status = 206L,
    source = "NOAA HRRR AWS public bucket byte-range fallback",
    source_url = urls$grib,
    source_index_url = urls$idx
  )
}

brim_hrrr_download_candidate <- function(cycle_time, forecast_hour) {
  stamp <- format(brim_hrrr_as_utc(cycle_time), "%Y%m%d_%Hz", tz = "UTC")
  base <- sprintf("hrrr_%s_f%02d_10m_uv", stamp, as.integer(forecast_hour))
  nomads_dest <- file.path(brim_hrrr_dirs$cache, paste0(base, "_nomads.grib2"))
  aws_dest <- file.path(brim_hrrr_dirs$cache, paste0(base, "_aws.grib2"))

  if (!brim_hrrr_force_refresh && file.exists(nomads_dest) && file.info(nomads_dest)$size >= brim_hrrr_min_grib_bytes) {
    return(list(
      path = nomads_dest,
      bytes = unname(file.info(nomads_dest)$size),
      source = "cached NOAA NOMADS GRIB subset",
      source_url = brim_hrrr_nomads_url(cycle_time, forecast_hour)
    ))
  }

  nomads <- tryCatch(
    brim_hrrr_download_nomads(cycle_time, forecast_hour, nomads_dest),
    error = function(e) e
  )
  if (!inherits(nomads, "error")) return(nomads)
  brim_hrrr_log("NOMADS failed: ", conditionMessage(nomads))

  if (!brim_hrrr_force_refresh && file.exists(aws_dest) && file.info(aws_dest)$size >= brim_hrrr_min_grib_bytes) {
    urls <- brim_hrrr_aws_urls(cycle_time, forecast_hour)
    return(list(
      path = aws_dest,
      bytes = unname(file.info(aws_dest)$size),
      source = "cached NOAA HRRR AWS byte-range subset",
      source_url = urls$grib,
      source_index_url = urls$idx
    ))
  }

  aws <- tryCatch(
    brim_hrrr_download_aws_ranges(cycle_time, forecast_hour, aws_dest),
    error = function(e) e
  )
  if (!inherits(aws, "error")) return(aws)
  stop(
    "NOMADS and AWS failed for cycle ", brim_hrrr_fmt_iso_utc(cycle_time),
    " f", sprintf("%02d", forecast_hour), ". NOMADS: ", conditionMessage(nomads),
    " | AWS: ", conditionMessage(aws)
  )
}

brim_hrrr_assert_uv_inventory <- function(grib_path) {
  inv <- brim_hrrr_run(brim_hrrr_wgrib2, c(grib_path, "-s", "-vector_dir"), "wgrib2 input inventory")
  writeLines(inv, file.path(brim_hrrr_dirs$debug, "hrrr_input_inventory.txt"), useBytes = TRUE)
  u <- grep(":UGRD:10 m above ground:", inv, fixed = TRUE)
  v <- grep(":VGRD:10 m above ground:", inv, fixed = TRUE)
  if (!length(u) || !length(v)) stop("Downloaded GRIB does not contain both 10-m UGRD and VGRD.")
  if (u[1] > v[1]) stop("VGRD precedes UGRD; vector-aware interpolation requires U then V.")
  invisible(inv)
}

brim_hrrr_regrid <- function(input_grib, output_grib) {
  res <- brim_hrrr_domain$resolution_degrees
  nx <- as.integer(round((brim_hrrr_domain$east - brim_hrrr_domain$west) / res) + 1L)
  ny <- as.integer(round((brim_hrrr_domain$north - brim_hrrr_domain$south) / res) + 1L)
  lon_spec <- sprintf("%.6f:%d:%.6f", brim_hrrr_domain$west, nx, res)
  lat_spec <- sprintf("%.6f:%d:%.6f", brim_hrrr_domain$south, ny, res)

  if (file.exists(output_grib)) unlink(output_grib)
  args <- c(
    input_grib,
    "-set_grib_type", "same",
    "-new_grid_winds", "earth",
    "-new_grid_interpolation", "bilinear",
    "-new_grid", "latlon", lon_spec, lat_spec,
    output_grib
  )
  brim_hrrr_log("Regridding with wgrib2 to ", nx, " x ", ny, " at ", res, " degrees.")
  brim_hrrr_run(brim_hrrr_wgrib2, args, "wgrib2 vector-aware regrid")
  if (!file.exists(output_grib) || file.info(output_grib)$size < brim_hrrr_min_grib_bytes) {
    stop("Regridded GRIB was not created or was unexpectedly small.")
  }
  inv <- brim_hrrr_run(brim_hrrr_wgrib2, c(output_grib, "-s", "-vector_dir"), "wgrib2 output inventory")
  writeLines(inv, file.path(brim_hrrr_dirs$debug, "hrrr_regridded_inventory.txt"), useBytes = TRUE)
  if (!any(grepl("winds\\(N/S\\)", inv))) {
    stop("Regridded HRRR output was not confirmed as earth-relative winds(N/S).")
  }
  list(path = output_grib, nx = nx, ny = ny, resolution = res, inventory = inv)
}

brim_hrrr_identify_uv_layers <- function(r) {
  labels <- paste(names(r), terra::longnames(r), sep = " | ")
  u <- grep("(^|[^A-Z])UGRD|U-component|u-component|10 metre U", labels, ignore.case = TRUE)
  v <- grep("(^|[^A-Z])VGRD|V-component|v-component|10 metre V", labels, ignore.case = TRUE)
  if (!length(u) || !length(v)) {
    stop("Could not identify U/V layers in regridded GRIB:\n", paste(labels, collapse = "\n"))
  }
  c(u = u[1], v = v[1])
}

brim_hrrr_flatten_north_to_south <- function(layer) {
  matrix <- terra::as.matrix(layer, wide = TRUE)
  ys <- terra::yFromRow(layer, seq_len(terra::nrow(layer)))
  if (length(ys) > 1L && ys[1] < ys[length(ys)]) matrix <- matrix[nrow(matrix):1, , drop = FALSE]
  as.numeric(t(matrix))
}

brim_hrrr_velocity_record <- function(layer, component, cycle_time, forecast_hour) {
  ex <- terra::ext(layer)
  rs <- terra::res(layer)
  list(
    header = list(
      parameterCategory = 2,
      parameterNumber = if (component == "u") 2 else 3,
      parameterNumberName = if (component == "u") "U-component_of_wind" else "V-component_of_wind",
      parameterUnit = "m.s-1",
      nx = terra::ncol(layer),
      ny = terra::nrow(layer),
      lo1 = terra::xmin(ex) + rs[1] / 2,
      la1 = terra::ymax(ex) - rs[2] / 2,
      lo2 = terra::xmax(ex) - rs[1] / 2,
      la2 = terra::ymin(ex) + rs[2] / 2,
      dx = rs[1],
      dy = rs[2],
      refTime = brim_hrrr_fmt_iso_utc(cycle_time),
      forecastTime = as.integer(forecast_hour)
    ),
    data = brim_hrrr_flatten_north_to_south(layer)
  )
}

brim_hrrr_speed_stats <- function(u, v) {
  uv <- cbind(terra::values(u, mat = FALSE), terra::values(v, mat = FALSE))
  speed <- sqrt(uv[, 1]^2 + uv[, 2]^2)
  speed <- speed[is.finite(speed)]
  if (!length(speed)) stop("No finite wind speeds in regridded HRRR field.")

  # quantile() normally labels results as 0%, 50%, 75%, etc., even when the
  # input probability vector is named. Assign stable feed-facing names
  # explicitly so later p95/max lookups and JSON fields are deterministic.
  probs <- c(min = 0, p50 = .50, p75 = .75, p90 = .90, p95 = .95, max = 1)
  ms <- stats::quantile(
    speed,
    probs = unname(probs),
    names = FALSE,
    type = 7,
    na.rm = TRUE
  )
  names(ms) <- names(probs)

  if (length(ms) != length(probs) || any(!is.finite(ms))) {
    stop("Could not calculate complete finite HRRR wind-speed statistics.")
  }

  mph <- stats::setNames(unname(ms) * 2.23694, names(ms))
  recommended_ms <- min(max(unname(ms[["p95"]]) * 1.10, 8), 35)

  list(
    speed_ms = as.list(ms),
    speed_mph = as.list(mph),
    recommended_velocity_scale_ms = recommended_ms,
    recommended_velocity_scale_mph = recommended_ms * 2.23694,
    velocity_scale_reference = "p95 * 1.10, clamped to 8-35 m/s"
  )
}

brim_hrrr_write_velocity_json <- function(u, v, out, cycle_time, forecast_hour) {
  velocity <- list(
    brim_hrrr_velocity_record(u, "u", cycle_time, forecast_hour),
    brim_hrrr_velocity_record(v, "v", cycle_time, forecast_hour)
  )
  jsonlite::write_json(velocity, out, auto_unbox = TRUE, digits = 5, pretty = FALSE, na = "null")
  if (file.info(out)$size < brim_hrrr_min_json_bytes) {
    stop("HRRR velocity JSON was unexpectedly small: ", file.info(out)$size, " bytes")
  }
  out
}


`%||%` <- function(x, y) {
  if (is.null(x) || length(x) == 0L) y else x
}

brim_hrrr_parse_iso_utc <- function(x) {
  as.POSIXct(strptime(x, format = "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"), tz = "UTC")
}

brim_hrrr_product_filename <- function(cycle_time, forecast_hour) {
  paste0(
    "hrrr_surface_wind_",
    format(brim_hrrr_as_utc(cycle_time), "%Y%m%dT%HZ", tz = "UTC"),
    "_f", sprintf("%02d", as.integer(forecast_hour)),
    ".json"
  )
}

brim_hrrr_relative_url <- function(path) {
  publish_root <- normalizePath(brim_hrrr_dirs$publish, winslash = "/", mustWork = FALSE)
  normalized <- normalizePath(path, winslash = "/", mustWork = FALSE)
  sub(paste0("^", publish_root, "/?"), "", normalized)
}

brim_hrrr_existing_entries <- function() {
  if (!file.exists(brim_hrrr_files$manifest_json)) return(list())
  obj <- tryCatch(
    jsonlite::read_json(brim_hrrr_files$manifest_json, simplifyVector = FALSE),
    error = function(e) NULL
  )
  if (is.null(obj) || !is.list(obj$entries)) return(list())
  obj$entries
}

brim_hrrr_entry_is_usable <- function(entry, now_utc) {
  if (!is.list(entry)) return(FALSE)
  valid <- tryCatch(brim_hrrr_parse_iso_utc(as.character(entry$valid_time_utc)), error = function(e) as.POSIXct(NA))
  if (!is.finite(as.numeric(valid))) return(FALSE)
  lag_hours <- as.numeric(difftime(valid, now_utc, units = "hours"))
  if (lag_hours < -brim_hrrr_retain_past_hours || lag_hours > brim_hrrr_retain_future_hours) return(FALSE)
  rel <- as.character(entry$relative_url %||% "")
  if (!nzchar(rel)) return(FALSE)
  file.exists(file.path(brim_hrrr_dirs$publish, rel))
}

brim_hrrr_merge_entries <- function(old_entries, new_entries, now_utc) {
  if (is.null(new_entries)) new_entries <- list()
  if (is.list(new_entries) && !is.null(new_entries$product_id)) new_entries <- list(new_entries)
  entries <- c(old_entries, new_entries)
  entries <- entries[vapply(entries, brim_hrrr_entry_is_usable, logical(1), now_utc = now_utc)]
  if (!length(entries)) return(list())

  valid_key <- vapply(entries, function(x) as.character(x$valid_time_utc %||% ""), character(1))
  cycle_num <- vapply(entries, function(x) {
    t <- tryCatch(brim_hrrr_parse_iso_utc(as.character(x$model_cycle_utc)), error = function(e) as.POSIXct(NA))
    if (is.finite(as.numeric(t))) as.numeric(t) else -Inf
  }, numeric(1))
  fh <- vapply(entries, function(x) suppressWarnings(as.integer(x$forecast_hour %||% 999L)), integer(1))

  # For duplicate valid hours, keep the newest model cycle; if cycles tie,
  # prefer the smaller forecast hour.
  keep <- logical(length(entries))
  for (key in unique(valid_key)) {
    idx <- which(valid_key == key)
    ord <- order(cycle_num[idx], -fh[idx], na.last = TRUE)
    keep[idx[ord[length(ord)]]] <- TRUE
  }
  entries <- entries[keep]

  valid_num <- vapply(entries, function(x) as.numeric(brim_hrrr_parse_iso_utc(x$valid_time_utc)), numeric(1))
  if (length(entries) > brim_hrrr_max_manifest_entries) {
    distance <- abs(valid_num - as.numeric(now_utc))
    chosen <- order(distance, valid_num > as.numeric(now_utc), -valid_num)[seq_len(brim_hrrr_max_manifest_entries)]
    entries <- entries[chosen]
    valid_num <- valid_num[chosen]
  }
  entries[order(valid_num)]
}

brim_hrrr_select_best_entry <- function(entries, now_utc) {
  if (!length(entries)) return(NULL)
  valid <- vapply(entries, function(x) as.numeric(brim_hrrr_parse_iso_utc(x$valid_time_utc)), numeric(1))
  cycle <- vapply(entries, function(x) as.numeric(brim_hrrr_parse_iso_utc(x$model_cycle_utc)), numeric(1))
  fh <- vapply(entries, function(x) as.integer(x$forecast_hour), integer(1))
  delta <- valid - as.numeric(now_utc)
  ord <- order(abs(delta), delta > 0, -cycle, fh)
  entries[[ord[1]]]
}

brim_hrrr_cleanup_timeset <- function(keep_relative_urls) {
  files <- list.files(
    brim_hrrr_dirs$timeset,
    pattern = "^hrrr_surface_wind_.*_f[0-9]{2}\\.json$",
    full.names = TRUE
  )
  if (!length(files)) return(invisible(character()))
  keep_abs <- normalizePath(
    file.path(brim_hrrr_dirs$publish, keep_relative_urls),
    winslash = "/",
    mustWork = FALSE
  )
  files_norm <- normalizePath(files, winslash = "/", mustWork = FALSE)
  drop <- files[!files_norm %in% keep_abs]
  if (length(drop)) {
    brim_hrrr_log("Removing old HRRR time-set files: ", length(drop))
    unlink(drop)
  }
  invisible(drop)
}

brim_hrrr_build_target_entry <- function(target_time, requested_lead_hours, now_utc) {
  target_time <- brim_hrrr_as_utc(target_time)
  candidates <- brim_hrrr_candidate_table(target_time, now_utc)
  if (!nrow(candidates)) {
    stop("No HRRR candidate products were generated for target ", brim_hrrr_fmt_iso_utc(target_time), ".")
  }

  candidates$requested_lead_hours <- as.integer(requested_lead_hours)
  selected <- NULL
  attempt_errors <- character()

  for (i in seq_len(nrow(candidates))) {
    cycle <- brim_hrrr_as_utc(candidates$cycle_time[i])
    fh <- candidates$forecast_hour[i]
    valid <- brim_hrrr_as_utc(candidates$valid_time[i])
    brim_hrrr_log(
      "Trying HRRR target +", requested_lead_hours, "h: cycle ",
      brim_hrrr_fmt_iso_utc(cycle), " f", sprintf("%02d", fh),
      " valid ", brim_hrrr_fmt_iso_utc(valid)
    )

    result <- tryCatch(brim_hrrr_download_candidate(cycle, fh), error = function(e) e)
    if (inherits(result, "error")) {
      attempt_errors <- c(
        attempt_errors,
        paste0(
          "+", requested_lead_hours, "h | ",
          brim_hrrr_fmt_iso_utc(cycle), " f", sprintf("%02d", fh), ": ",
          conditionMessage(result)
        )
      )
      next
    }
    selected <- c(result, list(cycle_time = cycle, forecast_hour = fh, valid_time = valid))
    break
  }

  if (is.null(selected)) {
    stop(
      "No usable HRRR product was found for +", requested_lead_hours,
      "h target. Attempts:\n", paste(attempt_errors, collapse = "\n")
    )
  }

  tag <- paste0(
    format(selected$cycle_time, "%Y%m%dT%HZ", tz = "UTC"),
    "_f", sprintf("%02d", selected$forecast_hour)
  )
  brim_hrrr_assert_uv_inventory(selected$path)
  regridded_path <- file.path(
    brim_hrrr_dirs$debug,
    paste0("hrrr_10m_uv_earth_relative_", tag, ".grib2")
  )
  brim_hrrr_regrid(selected$path, regridded_path)

  r <- terra::rast(regridded_path)
  uv_idx <- brim_hrrr_identify_uv_layers(r)
  u <- r[[uv_idx[["u"]]]]
  v <- r[[uv_idx[["v"]]]]
  if (!terra::compareGeom(u, v, stopOnError = FALSE)) {
    stop("Regridded U/V geometries do not match for ", tag, ".")
  }
  names(u) <- "UGRD_10m_earth_relative"
  names(v) <- "VGRD_10m_earth_relative"

  product_path <- file.path(
    brim_hrrr_dirs$timeset,
    brim_hrrr_product_filename(selected$cycle_time, selected$forecast_hour)
  )
  brim_hrrr_write_velocity_json(
    u, v, product_path, selected$cycle_time, selected$forecast_hour
  )
  stats <- brim_hrrr_speed_stats(u, v)

  entry <- list(
    product_id = "hrrr_surface_wind",
    product = "NOAA/NCEP HRRR 10-m wind",
    model = "NOAA/NCEP HRRR",
    level = "10 m above ground",
    variables = c("UGRD", "VGRD"),
    requested_lead_hours = as.integer(requested_lead_hours),
    target_valid_time_utc = brim_hrrr_fmt_iso_utc(target_time),
    target_valid_time_local = brim_hrrr_fmt_local(target_time, brim_hrrr_local_tz),
    target_distance_minutes = unname(
      abs(as.numeric(difftime(selected$valid_time, target_time, units = "mins")))
    ),
    model_cycle_utc = brim_hrrr_fmt_iso_utc(selected$cycle_time),
    model_cycle_local = brim_hrrr_fmt_local(selected$cycle_time, brim_hrrr_local_tz),
    forecast_hour = as.integer(selected$forecast_hour),
    forecast_hour_label = paste0("f", sprintf("%02d", as.integer(selected$forecast_hour))),
    valid_time_utc = brim_hrrr_fmt_iso_utc(selected$valid_time),
    valid_time_local = brim_hrrr_fmt_local(selected$valid_time, brim_hrrr_local_tz),
    valid_lag_minutes_at_build = unname(
      as.numeric(difftime(now_utc, selected$valid_time, units = "mins"))
    ),
    relative_url = brim_hrrr_relative_url(product_path),
    filename = basename(product_path),
    file_bytes = unname(file.info(product_path)$size),
    source = selected$source,
    source_request_url = selected$source_url,
    source_index_url = selected$source_index_url %||% NULL,
    source_grib_bytes = selected$bytes,
    domain = c(brim_hrrr_domain[c("id", "label", "west", "east", "south", "north")]),
    grid = list(
      nx = terra::ncol(u),
      ny = terra::nrow(u),
      resolution_degrees = terra::res(u)[1],
      cell_count = terra::ncell(u)
    ),
    earth_relative_winds_confirmed = TRUE,
    vector_regrid_method =
      "wgrib2 -new_grid_winds earth; bilinear interpolation to regular lon/lat",
    speed_ms = stats$speed_ms,
    speed_mph = stats$speed_mph,
    recommended_velocity_scale_ms = stats$recommended_velocity_scale_ms,
    recommended_velocity_scale_mph = stats$recommended_velocity_scale_mph,
    velocity_scale_reference = stats$velocity_scale_reference
  )

  list(entry = entry, candidates = candidates, errors = attempt_errors)
}

brim_hrrr_package_qa <- function(summary, manifest) {
  manifest_txt <- file.path(brim_hrrr_dirs$qa, "HRRR_QA_manifest.txt")
  lines <- c(
    "BRIM HRRR wind production-feed QA",
    paste0("Version: ", brim_hrrr_version),
    paste0("Generated UTC: ", summary$build_time_utc),
    paste0("Model cycle UTC: ", summary$model_cycle_utc),
    paste0("Forecast hour: ", summary$forecast_hour_label),
    paste0("Valid UTC: ", summary$valid_time_utc),
    paste0("Source: ", summary$source),
    paste0("Domain: ", summary$domain$west, ", ", summary$domain$east, ", ", summary$domain$south, ", ", summary$domain$north),
    paste0("Grid: ", summary$grid$nx, " x ", summary$grid$ny, " at ", summary$grid$resolution_degrees, " degrees"),
    paste0("Earth-relative output confirmed: ", summary$earth_relative_winds_confirmed),
    paste0("Supported browser lead hours: ", paste(summary$supported_browser_lead_hours, collapse = ", ")),
    paste0("Manifest entries: ", length(manifest$entries)),
    paste0("Latest JSON bytes: ", file.info(brim_hrrr_files$wind_json)$size),
    paste0("p95 mph: ", round(summary$speed_mph$p95, 1)),
    paste0("domain max mph: ", round(summary$speed_mph$max, 1)),
    paste0("recommended velocity scale mph: ", round(summary$recommended_velocity_scale_mph, 1))
  )
  writeLines(lines, manifest_txt, useBytes = TRUE)

  zip_path <- file.path(brim_hrrr_dirs$qa, "HRRR_latest_QA_upload.zip")
  if (file.exists(zip_path)) unlink(zip_path)
  candidates <- c(
    manifest_txt,
    brim_hrrr_log_file,
    brim_hrrr_files$summary_json,
    brim_hrrr_files$manifest_json,
    file.path(brim_hrrr_dirs$debug, "hrrr_input_inventory.txt"),
    file.path(brim_hrrr_dirs$debug, "hrrr_regridded_inventory.txt"),
    file.path(brim_hrrr_dirs$debug, "hrrr_target_times.csv"),
    file.path(brim_hrrr_dirs$debug, "hrrr_candidate_order.csv"),
    file.path(brim_hrrr_dirs$debug, "hrrr_failed_attempts.txt")
  )
  candidates <- unique(candidates[file.exists(candidates)])
  old <- getwd()
  on.exit(setwd(old), add = TRUE)
  setwd(brim_hrrr_project_root)
  relative <- vapply(candidates, function(x) {
    sub(
      paste0("^", normalizePath(brim_hrrr_project_root, winslash = "/"), "/?"),
      "",
      normalizePath(x, winslash = "/")
    )
  }, character(1))
  utils::zip(zipfile = zip_path, files = relative, flags = "-r9X")
  zip_path
}

brim_hrrr_log("BRIM HRRR wind production build started: ", format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z"))
brim_hrrr_log("Version: ", brim_hrrr_version)
brim_hrrr_log("Target lead hours: ", paste(brim_hrrr_target_lead_hours, collapse = ", "))

run_error <- NULL
run_summary <- NULL
run_manifest <- NULL
qa_zip <- NULL

tryCatch({
  now_utc <- brim_hrrr_floor_second(Sys.time())

  # Round to the nearest model-valid hour before applying the user-facing leads.
  # The workflow runs at :33, so this normally anchors to the next whole hour.
  target_base <- brim_hrrr_floor_hour(now_utc + 1800)
  target_times <- target_base + brim_hrrr_target_lead_hours * 3600
  target_table <- data.frame(
    requested_lead_hours = brim_hrrr_target_lead_hours,
    target_time_utc = brim_hrrr_fmt_iso_utc(target_times),
    stringsAsFactors = FALSE
  )
  write.csv(
    target_table,
    file.path(brim_hrrr_dirs$debug, "hrrr_target_times.csv"),
    row.names = FALSE,
    na = ""
  )

  new_entries <- list()
  all_candidates <- list()
  all_attempt_errors <- character()
  target_failures <- character()

  for (i in seq_along(brim_hrrr_target_lead_hours)) {
    lead <- brim_hrrr_target_lead_hours[i]
    target <- target_times[i]
    result <- tryCatch(
      brim_hrrr_build_target_entry(target, lead, now_utc),
      error = function(e) e
    )

    if (inherits(result, "error")) {
      msg <- paste0("+", lead, "h target failed: ", conditionMessage(result))
      brim_hrrr_log(msg)
      target_failures <- c(target_failures, msg)
      next
    }

    new_entries[[length(new_entries) + 1L]] <- result$entry
    all_candidates[[length(all_candidates) + 1L]] <- result$candidates
    all_attempt_errors <- c(all_attempt_errors, result$errors)
  }

  if (length(all_candidates)) {
    candidate_audit <- do.call(rbind, all_candidates)
    write.csv(
      candidate_audit,
      file.path(brim_hrrr_dirs$debug, "hrrr_candidate_order.csv"),
      row.names = FALSE,
      na = ""
    )
  }
  writeLines(
    c(all_attempt_errors, target_failures),
    file.path(brim_hrrr_dirs$debug, "hrrr_failed_attempts.txt"),
    useBytes = TRUE
  )

  entries <- brim_hrrr_merge_entries(
    brim_hrrr_existing_entries(),
    new_entries,
    now_utc
  )
  if (!length(entries)) {
    stop("No usable HRRR entries were available after building the requested targets.")
  }

  selected_entry <- brim_hrrr_select_best_entry(entries, now_utc)
  if (is.null(selected_entry)) stop("Could not select a closest HRRR manifest entry.")

  current_distance_hours <- abs(
    as.numeric(
      difftime(
        brim_hrrr_parse_iso_utc(selected_entry$valid_time_utc),
        now_utc,
        units = "hours"
      )
    )
  )
  if (!is.finite(current_distance_hours) || current_distance_hours > 3) {
    stop("The closest HRRR entry is more than 3 hours from current browser context.")
  }

  selected_path <- file.path(brim_hrrr_dirs$publish, selected_entry$relative_url)
  if (!file.exists(selected_path)) stop("Selected HRRR JSON is missing: ", selected_path)
  file.copy(selected_path, brim_hrrr_files$wind_json, overwrite = TRUE)

  valid_time <- brim_hrrr_parse_iso_utc(selected_entry$valid_time_utc)
  freshness <- brim_hrrr_summary_freshness(
    Sys.time(), valid_time, brim_hrrr_stale_after_hours
  )
  build_time <- freshness$build_time

  nearest_lead_availability <- lapply(brim_hrrr_target_lead_hours, function(lead) {
    target_ms <- as.numeric(now_utc + lead * 3600)
    valid_ms <- vapply(
      entries,
      function(x) as.numeric(brim_hrrr_parse_iso_utc(x$valid_time_utc)),
      numeric(1)
    )
    distance_hours <- min(abs(valid_ms - target_ms) / 3600, na.rm = TRUE)
    list(
      lead_hours = as.integer(lead),
      nearest_distance_hours = unname(distance_hours),
      available_within_2_hours = isTRUE(distance_hours <= 2)
    )
  })

  run_summary <- list(
    product_id = "hrrr_surface_wind",
    product = "NOAA/NCEP HRRR 10-m wind over Hydrologic California + adjacent basins",
    feed_mode = "multi_target_time_set_with_legacy_latest",
    version = brim_hrrr_version,
    status = "success",
    selection_strategy = "closest valid_time_utc to build time; BRIM targets Current, +6 hr, or +12 hr using browser time",
    source = selected_entry$source,
    model_cycle_utc = selected_entry$model_cycle_utc,
    model_cycle_local = selected_entry$model_cycle_local,
    forecast_hour = selected_entry$forecast_hour,
    forecast_hour_label = selected_entry$forecast_hour_label,
    valid_time_utc = selected_entry$valid_time_utc,
    valid_time_local = selected_entry$valid_time_local,
    build_time_utc = brim_hrrr_fmt_iso_utc(build_time),
    build_time_local = brim_hrrr_fmt_local(build_time, brim_hrrr_local_tz),
    local_time_zone = brim_hrrr_local_tz,
    valid_lag_minutes_at_build = freshness$valid_lag_minutes_at_build,
    valid_time_age_hours = freshness$valid_time_age_hours,
    stale_after_hours = brim_hrrr_stale_after_hours,
    is_stale = freshness$is_stale,
    supported_browser_lead_hours = brim_hrrr_target_lead_hours,
    target_base_utc = brim_hrrr_fmt_iso_utc(target_base),
    target_build_results = nearest_lead_availability,
    target_failures = target_failures,
    domain = selected_entry$domain,
    grid = selected_entry$grid,
    earth_relative_winds_confirmed = TRUE,
    vector_regrid_method = selected_entry$vector_regrid_method,
    speed_ms = selected_entry$speed_ms,
    speed_mph = selected_entry$speed_mph,
    recommended_velocity_scale_ms = selected_entry$recommended_velocity_scale_ms,
    recommended_velocity_scale_mph = selected_entry$recommended_velocity_scale_mph,
    velocity_scale_reference = selected_entry$velocity_scale_reference,
    selected_entry = selected_entry,
    available_entry_count = length(entries),
    retain_past_hours = brim_hrrr_retain_past_hours,
    retain_future_hours = brim_hrrr_retain_future_hours,
    output_json_bytes = unname(file.info(brim_hrrr_files$wind_json)$size),
    output_files = list(
      wind_json = "docs/data/wind/hrrr_surface_wind_latest.json",
      summary_json = "docs/data/wind/hrrr_surface_wind_latest_summary.json",
      manifest_json = "docs/data/wind/hrrr_surface_wind_feed_manifest.json",
      time_set_directory = "docs/data/wind/hrrr/surface"
    ),
    intended_use = "BRIM operational situational-awareness feed; use NWS products for official forecasts and warnings."
  )

  run_manifest <- list(
    product_id = "hrrr_surface_wind",
    product = "NOAA/NCEP HRRR 10-m wind Current/+6/+12 time set",
    feed_mode = "multi_target_time_set",
    version = brim_hrrr_version,
    description = "HRRR 10-m U/V wind converted to Earth-relative regular lon/lat fields for Current, +6 hr, and +12 hr Leaflet particle-flow views over Hydrologic California + adjacent basins.",
    source = "NOAA/NCEP HRRR via NOMADS with NOAA AWS byte-range fallback",
    domain = c(brim_hrrr_domain[c("id", "label", "west", "east", "south", "north")]),
    local_time_zone = brim_hrrr_local_tz,
    generated_utc = brim_hrrr_fmt_iso_utc(build_time),
    generated_local = brim_hrrr_fmt_local(build_time, brim_hrrr_local_tz),
    browser_selection = list(
      recommended = TRUE,
      supported_lead_hours = brim_hrrr_target_lead_hours,
      strategy = "fetch this manifest first; choose entry with valid_time_utc closest to Date.now() plus the selected lead hours; prefer recent past over future for equal-distance ties; then fetch entry.relative_url relative to this manifest directory"
    ),
    dynamic_particle_scaling = list(
      recommended = TRUE,
      note = "Use selected entry recommended_velocity_scale_ms, with speed_ms.p95 as fallback."
    ),
    legacy_latest = list(
      note = "Latest files are retained for compatibility; preferred BRIM integration uses manifest entries.",
      wind_json = "docs/data/wind/hrrr_surface_wind_latest.json",
      summary_json = "docs/data/wind/hrrr_surface_wind_latest_summary.json",
      selected_entry = selected_entry
    ),
    stale_after_hours = brim_hrrr_stale_after_hours,
    supported_browser_lead_hours = brim_hrrr_target_lead_hours,
    target_base_utc = brim_hrrr_fmt_iso_utc(target_base),
    target_failures = target_failures,
    retain_past_hours = brim_hrrr_retain_past_hours,
    retain_future_hours = brim_hrrr_retain_future_hours,
    entry_count = length(entries),
    entries = entries
  )

  jsonlite::write_json(
    run_summary, brim_hrrr_files$summary_json,
    auto_unbox = TRUE, pretty = TRUE, digits = 6, na = "null", null = "null"
  )
  jsonlite::write_json(
    run_manifest, brim_hrrr_files$manifest_json,
    auto_unbox = TRUE, pretty = TRUE, digits = 6, na = "null", null = "null"
  )
  brim_hrrr_cleanup_timeset(vapply(entries, function(x) x$relative_url, character(1)))
  qa_zip <- brim_hrrr_package_qa(run_summary, run_manifest)

  brim_hrrr_log("HRRR production feed complete.")
  brim_hrrr_log("Selected current field: ", run_summary$model_cycle_utc, " ",
                run_summary$forecast_hour_label, " valid ", run_summary$valid_time_utc)
  brim_hrrr_log("Requested leads: ", paste(brim_hrrr_target_lead_hours, collapse = ", "), " hours")
  brim_hrrr_log("New entries built: ", length(new_entries), "; manifest entries: ", length(entries))
  brim_hrrr_log("Latest JSON: ", brim_hrrr_files$wind_json, " (",
                file.info(brim_hrrr_files$wind_json)$size, " bytes)")

}, error = function(e) {
  run_error <<- e
  brim_hrrr_log("ERROR: ", conditionMessage(e))
})

if (!is.null(run_error)) stop(run_error)

cat(
  "\nHRRR production feed complete.\n",
  "Selected model cycle: ", run_summary$model_cycle_local, "\n",
  "Forecast hour: ", run_summary$forecast_hour_label, "\n",
  "Valid: ", run_summary$valid_time_local, "\n",
  "Manifest entries: ", run_summary$available_entry_count, "\n",
  "Latest JSON: ", normalizePath(brim_hrrr_files$wind_json, winslash = "/", mustWork = FALSE), "\n",
  "Manifest: ", normalizePath(brim_hrrr_files$manifest_json, winslash = "/", mustWork = FALSE), "\n",
  "QA ZIP: ", normalizePath(qa_zip, winslash = "/", mustWork = FALSE), "\n",
  sep = ""
)
