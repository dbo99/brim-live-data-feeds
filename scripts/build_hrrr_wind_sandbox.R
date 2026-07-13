# BRIM HRRR wind sandbox builder ---------------------------------------------
#
# PURPOSE
#   Build a manual-review sandbox for NOAA/NCEP HRRR 10-m wind over the same
#   Hydrologic California + adjacent basins domain used by the BRIM METAR/ASOS
#   layer. The output is NOT published to docs/ and is NOT yet a BRIM live feed.
#
# METHOD
#   1. Try the NOAA NOMADS HRRR GRIB filter for only 10-m UGRD/VGRD.
#   2. Fall back to NOAA's public HRRR AWS bucket using GRIB byte ranges.
#   3. Use wgrib2 vector-aware interpolation with -new_grid_winds earth.
#   4. Regrid to a regular 0.05-degree lon/lat grid for leaflet-velocity.
#   5. Build an embedded-data HTML sandbox and an HRRR-vs-METAR QA table.
#
# REQUIREMENT
#   wgrib2 must be on PATH and include -new_grid support. The companion GitHub
#   workflow installs the conda-forge wgrib2 package for the sandbox run.

brim_hrrr_env <- function(name, default = "") {
  value <- Sys.getenv(name, unset = default)
  if (!nzchar(value)) default else value
}

brim_hrrr_bool <- function(x, default = FALSE) {
  if (is.null(x) || !nzchar(x)) return(default)
  tolower(x) %in% c("1", "true", "yes", "y", "on")
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

brim_hrrr_version <- "RTW-HRRR-SBX001"
brim_hrrr_local_tz <- brim_hrrr_env("BRIM_HRRR_LOCAL_TZ", "America/Los_Angeles")
brim_hrrr_force_refresh <- brim_hrrr_bool(brim_hrrr_env("BRIM_HRRR_FORCE_REFRESH", "false"), FALSE)
brim_hrrr_open_html <- brim_hrrr_bool(brim_hrrr_env("BRIM_HRRR_OPEN_HTML", "true"), TRUE)
brim_hrrr_cycle_lookback_hours <- as.integer(brim_hrrr_env("BRIM_HRRR_CYCLE_LOOKBACK_HOURS", "8"))
brim_hrrr_max_forecast_hour <- as.integer(brim_hrrr_env("BRIM_HRRR_MAX_FORECAST_HOUR", "6"))
brim_hrrr_request_timeout_seconds <- as.numeric(brim_hrrr_env("BRIM_HRRR_REQUEST_TIMEOUT_SECONDS", "120"))
brim_hrrr_min_grib_bytes <- as.integer(brim_hrrr_env("BRIM_HRRR_MIN_GRIB_BYTES", "10000"))
brim_hrrr_min_json_bytes <- as.integer(brim_hrrr_env("BRIM_HRRR_MIN_JSON_BYTES", "200000"))

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
  cache = file.path(brim_hrrr_project_root, "data", "cache", "wind", "hrrr_sandbox"),
  debug = file.path(brim_hrrr_project_root, "debug", "wind", "hrrr_sandbox"),
  html = file.path(brim_hrrr_project_root, "debug", "wind", "hrrr_sandbox", "html"),
  qa = file.path(brim_hrrr_project_root, "qa", "wind", "hrrr_sandbox")
)
for (d in brim_hrrr_dirs) dir.create(d, recursive = TRUE, showWarnings = FALSE)

brim_hrrr_install_missing <- function(pkgs) {
  missing <- pkgs[!vapply(pkgs, requireNamespace, quietly = TRUE, FUN.VALUE = logical(1))]
  if (length(missing)) install.packages(missing, repos = "https://cloud.r-project.org")
}
brim_hrrr_install_missing(c("httr2", "jsonlite", "terra"))

brim_hrrr_wgrib2 <- Sys.which("wgrib2")
if (!nzchar(brim_hrrr_wgrib2)) {
  stop(
    "wgrib2 was not found on PATH. Run the companion GitHub sandbox workflow, ",
    "or install wgrib2 locally before sourcing this script."
  )
}

brim_hrrr_stamp <- format(Sys.time(), "%Y%m%d_%H%M%S")
brim_hrrr_log_file <- file.path(brim_hrrr_dirs$debug, paste0("hrrr_sandbox_run_", brim_hrrr_stamp, ".log"))

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
brim_hrrr_log(paste(brim_hrrr_run(brim_hrrr_wgrib2, "-version", "wgrib2 version check"), collapse = " | "))

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

brim_hrrr_candidate_table <- function(now_utc = Sys.time()) {
  now_utc <- brim_hrrr_as_utc(now_utc)
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
        distance_minutes = abs(as.numeric(difftime(valid, now_utc, units = "mins"))),
        is_future = valid > now_utc,
        stringsAsFactors = FALSE
      )
    }
  }
  out <- do.call(rbind, rows)
  out <- out[out$distance_minutes <= 180, , drop = FALSE]
  out <- out[order(out$distance_minutes, out$is_future, -as.numeric(out$cycle_time), out$forecast_hour), , drop = FALSE]
  rownames(out) <- NULL
  out
}

brim_hrrr_http_download <- function(url, dest, min_bytes = brim_hrrr_min_grib_bytes) {
  req <- httr2::request(url) |>
    httr2::req_user_agent("BRIM-HRRR-wind-sandbox/0.1") |>
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
    httr2::req_user_agent("BRIM-HRRR-wind-sandbox/0.1") |>
    httr2::req_timeout(brim_hrrr_request_timeout_seconds) |>
    httr2::req_retry(max_tries = 2)
  resp <- httr2::req_perform(req)
  if (httr2::resp_status(resp) != 200L) stop("HTTP ", httr2::resp_status(resp), " for ", url)
  httr2::resp_body_string(resp)
}

brim_hrrr_fetch_range <- function(url, start, end) {
  if (!is.finite(start) || !is.finite(end) || end < start) stop("Invalid HTTP byte range.")
  req <- httr2::request(url) |>
    httr2::req_user_agent("BRIM-HRRR-wind-sandbox/0.1") |>
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
  probs <- c(min = 0, p50 = .50, p75 = .75, p90 = .90, p95 = .95, max = 1)
  ms <- stats::quantile(speed, probs = probs, names = TRUE, type = 7, na.rm = TRUE)
  mph <- ms * 2.23694
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

brim_hrrr_fetch_asos_geojson <- function() {
  url <- brim_hrrr_env(
    "BRIM_HRRR_ASOS_GEOJSON_URL",
    "https://raw.githubusercontent.com/dbo99/brim-live-data-feeds/main/docs/data/wind/asos_awos_wind_latest.geojson"
  )
  result <- tryCatch({
    text <- brim_hrrr_fetch_text(url)
    obj <- jsonlite::fromJSON(text, simplifyVector = FALSE)
    if (!identical(obj$type, "FeatureCollection") || !length(obj$features)) stop("ASOS object is not a nonempty FeatureCollection.")
    list(object = obj, url = url, error = NULL)
  }, error = function(e) list(object = list(type = "FeatureCollection", features = list()), url = url, error = conditionMessage(e)))
  if (!is.null(result$error)) brim_hrrr_log("ASOS comparison feed unavailable: ", result$error)
  result
}

brim_hrrr_circular_difference <- function(a, b) {
  abs(((a - b + 180) %% 360) - 180)
}

brim_hrrr_as_num <- function(x) {
  if (is.null(x) || length(x) == 0L) return(NA_real_)
  suppressWarnings(as.numeric(x[[1]]))
}

brim_hrrr_compare_asos <- function(asos, u, v, valid_time) {
  features <- asos$object$features
  if (!length(features)) return(list(object = asos$object, table = data.frame()))

  coords <- lapply(features, function(f) f$geometry$coordinates)
  lon <- vapply(coords, function(x) brim_hrrr_as_num(x[1]), numeric(1))
  lat <- vapply(coords, function(x) brim_hrrr_as_num(x[2]), numeric(1))
  stations <- vapply(features, function(f) as.character(f$properties$station_id %||% NA_character_), character(1))

  points <- terra::vect(data.frame(lon = lon, lat = lat), geom = c("lon", "lat"), crs = "EPSG:4326")
  extracted <- terra::extract(c(u, v), points, method = "bilinear", ID = FALSE)
  model_u <- as.numeric(extracted[[1]])
  model_v <- as.numeric(extracted[[2]])
  model_speed_mph <- sqrt(model_u^2 + model_v^2) * 2.23694
  model_from <- (atan2(-model_u, -model_v) * 180 / pi + 360) %% 360

  obs_speed <- vapply(features, function(f) brim_hrrr_as_num(f$properties$wind_speed_mph), numeric(1))
  obs_from <- vapply(features, function(f) {
    p <- f$properties
    value <- brim_hrrr_as_num(p$wind_from_degrees)
    if (!is.finite(value)) value <- brim_hrrr_as_num(p$wind_dir_degrees)
    value
  }, numeric(1))
  obs_time <- vapply(features, function(f) as.character(f$properties$observation_time_utc %||% NA_character_), character(1))
  speed_diff <- model_speed_mph - obs_speed
  dir_diff <- brim_hrrr_circular_difference(model_from, obs_from)
  dir_diff[!is.finite(obs_from) | !is.finite(model_from) | obs_speed < 2] <- NA_real_

  table <- data.frame(
    station_id = stations,
    longitude = lon,
    latitude = lat,
    observation_time_utc = obs_time,
    hrrr_valid_time_utc = brim_hrrr_fmt_iso_utc(valid_time),
    observed_speed_mph = obs_speed,
    hrrr_speed_mph = model_speed_mph,
    speed_difference_mph = speed_diff,
    observed_wind_from_degrees = obs_from,
    hrrr_wind_from_degrees = model_from,
    direction_difference_degrees = dir_diff,
    stringsAsFactors = FALSE
  )

  for (i in seq_along(features)) {
    p <- features[[i]]$properties
    p$hrrr_speed_mph <- if (is.finite(model_speed_mph[i])) unname(model_speed_mph[i]) else NULL
    p$hrrr_wind_from_degrees <- if (is.finite(model_from[i])) unname(model_from[i]) else NULL
    p$hrrr_speed_difference_mph <- if (is.finite(speed_diff[i])) unname(speed_diff[i]) else NULL
    p$hrrr_direction_difference_degrees <- if (is.finite(dir_diff[i])) unname(dir_diff[i]) else NULL
    p$hrrr_valid_time_utc <- brim_hrrr_fmt_iso_utc(valid_time)
    features[[i]]$properties <- p
  }
  asos$object$features <- features
  list(object = asos$object, table = table)
}

`%||%` <- function(x, y) {
  if (is.null(x) || length(x) == 0L || all(is.na(x))) y else x
}

brim_hrrr_json_for_script <- function(x) {
  text <- jsonlite::toJSON(x, auto_unbox = TRUE, digits = 6, na = "null", null = "null")
  gsub("</", "<\\/", text, fixed = TRUE)
}

brim_hrrr_build_html <- function(wind_json_text, asos_obj, summary, output) {
  summary_json <- brim_hrrr_json_for_script(summary)
  asos_json <- brim_hrrr_json_for_script(asos_obj)

  html <- paste0(
'<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BRIM HRRR wind sandbox</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
html,body,#map{width:100%;height:100%;margin:0;background:#fff}
.info{background:rgba(228,240,240,.94);padding:10px 12px;border:1px solid #8aa5a7;border-radius:6px;box-shadow:0 1px 7px rgba(0,0,0,.28);font:12px/1.3 Arial,sans-serif;max-width:365px}
.info strong{font-size:14px}.muted{color:#526267}.warn{color:#8b3f00;font-weight:700}
.hrrr-barb{background:transparent!important;border:0!important}.hrrr-barb svg{overflow:visible}
.station-popup{font:12px/1.3 Arial,sans-serif}.station-popup h3{font-size:14px;margin:0 0 5px}.station-popup table{border-collapse:collapse}.station-popup td{padding:1px 6px 1px 0;vertical-align:top}.station-popup td:first-child{font-weight:700;color:#666}
.leaflet-control-layers{font:12px/1.3 Arial,sans-serif}
</style>
</head>
<body><div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet-velocity@2.1.4/dist/leaflet-velocity.min.js"></script>
<script>
const windData=', wind_json_text, ';
const asosData=', asos_json, ';
const meta=', summary_json, ';
const map=L.map("map",{preferCanvas:true}).fitBounds([[meta.domain.south,meta.domain.west],[meta.domain.north,meta.domain.east]]);
const hydro=L.tileLayer("https://basemap.nationalmap.gov/arcgis/rest/services/USGSHydroCached/MapServer/tile/{z}/{y}/{x}",{maxZoom:20,attribution:"USGS The National Map — Hydrography"}).addTo(map);
const dark=L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",{maxZoom:19,attribution:"OpenStreetMap contributors & CARTO"});
const scale=Number(meta.recommended_velocity_scale_ms||20);
const velocity=L.velocityLayer({displayValues:true,displayOptions:{velocityType:"HRRR 10-m wind",position:"bottomleft",emptyString:"No wind data",angleConvention:"bearingCW",speedUnit:"mph",showCardinal:true},data:windData,minVelocity:0,maxVelocity:scale,velocityScale:.0065,particleAge:85,particleMultiplier:1/240,lineWidth:1.15,colorScale:["#7bb6ff","#b7dcff","#d8f5e0","#fff0a6","#ffc16e","#ff7d62","#d83b62"]}).addTo(map);
L.rectangle([[meta.domain.south,meta.domain.west],[meta.domain.north,meta.domain.east]],{color:"#435f76",weight:1,fill:false,dashArray:"5 4",interactive:false}).addTo(map);
function n(v){const x=Number(v);return Number.isFinite(x)?x:null}
function esc(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/\"/g,"&quot;")}
function barbSvg(p){let sp=n(p.wind_barb_speed_kt);if(sp==null)sp=n(p.wind_speed_kt);sp=sp==null?0:Math.max(0,sp);let dir=n(p.wind_from_degrees);if(dir==null)dir=n(p.wind_dir_degrees);if(sp<2.5||p.calm===true)return `<svg viewBox="0 0 44 44" width="44" height="44"><circle cx="22" cy="22" r="5.5" fill="rgba(255,255,255,.85)" stroke="#111" stroke-width="2"/></svg>`;if(dir==null)return `<svg viewBox="0 0 44 44" width="44" height="44"><circle cx="22" cy="22" r="6" fill="#fff" stroke="#111" stroke-width="2"/><text x="22" y="25" text-anchor="middle" font-size="7">VRB</text></svg>`;dir=((dir%360)+360)%360;const rounded=Math.round(sp/5)*5;let flags=Math.floor(rounded/50),rem=rounded-flags*50,full=Math.floor(rem/10),half=(rem-full*10)>=5?1:0,y=5;let lines=[[22,22,22,3]],polys=[];for(let i=0;i<flags;i++){polys.push(`22,${y} 32,${y+4} 22,${y+8}`);y+=8}for(let i=0;i<full;i++){lines.push([22,y,32,y+5]);y+=4}if(half)lines.push([22,y,28,y+3]);const drawLines=(c,w)=>lines.map(q=>`<line x1="${q[0]}" y1="${q[1]}" x2="${q[2]}" y2="${q[3]}" stroke="${c}" stroke-width="${w}" stroke-linecap="round"/>`).join("");const drawPoly=(c,s,w)=>polys.map(q=>`<polygon points="${q}" fill="${c}" stroke="${s}" stroke-width="${w}"/>`).join("");return `<svg viewBox="0 0 44 44" width="44" height="44"><g transform="rotate(${dir.toFixed(1)} 22 22)">${drawLines("#fff",5)}${drawPoly("#fff","#fff",4)}${drawLines("#111",2)}${drawPoly("#111","#111",1)}</g></svg>`}
function popup(p){const os=n(p.wind_speed_mph),hs=n(p.hrrr_speed_mph),od=n(p.wind_from_degrees),hd=n(p.hrrr_wind_from_degrees),dd=n(p.hrrr_direction_difference_degrees);return `<div class="station-popup"><h3>${esc(p.station_name||p.station_id||"METAR station")}</h3><div>${esc(p.station_id||"")}</div><table><tr><td>Observed</td><td>${os==null?"n/a":os.toFixed(1)+" mph"}${od==null?"":" · from "+Math.round(od)+"°"}</td></tr><tr><td>HRRR</td><td>${hs==null?"n/a":hs.toFixed(1)+" mph"}${hd==null?"":" · from "+Math.round(hd)+"°"}</td></tr><tr><td>Direction Δ</td><td>${dd==null?"n/a":Math.round(dd)+"°"}</td></tr><tr><td>Observed time</td><td>${esc(p.observation_time_local||p.observation_time_utc||"n/a")}</td></tr></table><div style="margin-top:5px;color:#666">Point observation versus nearby gridded model field; terrain and exposure can differ.</div></div>`}
const asos=L.layerGroup();(asosData.features||[]).forEach(f=>{if(!f.geometry||!f.geometry.coordinates)return;const p=f.properties||{},c=f.geometry.coordinates;L.marker([c[1],c[0]],{icon:L.divIcon({className:"hrrr-barb",html:barbSvg(p),iconSize:[44,44],iconAnchor:[22,22]})}).bindPopup(popup(p)).addTo(asos)});asos.addTo(map);
const info=L.control({position:"topleft"});info.onAdd=()=>{const d=L.DomUtil.create("div","info");const valid=new Date(meta.valid_time_utc).toLocaleString(undefined,{timeZone:"America/Los_Angeles",timeZoneName:"short"});const cycle=new Date(meta.model_cycle_utc).toLocaleString(undefined,{timeZone:"America/Los_Angeles",timeZoneName:"short"});d.innerHTML=`<strong>NOAA HRRR 10-m wind sandbox</strong><br>${esc(meta.domain.label)}<br><b>Valid:</b> ${valid} · hourly model snapshot<br><b>HRRR run:</b> ${cycle} · ${esc(meta.forecast_hour_label)}<br><b>Grid:</b> ${meta.grid.nx} × ${meta.grid.ny} · ${meta.grid.resolution_degrees}°<br><b>Scale:</b> p95 ${Math.round(meta.speed_mph.p95)} mph · max ${Math.round(meta.speed_mph.max)} mph<br><b>Source:</b> ${esc(meta.source)}<br><b>Vector handling:</b> wgrib2 earth-relative regrid<br><span class="muted">Black barbs are METAR/ASOS sustained winds. Click a station for observed-versus-model QA. Model data are for situational awareness; use NWS products for decisions.</span>`;return d};info.addTo(map);
L.control.layers({"USGS Hydrography":hydro,"Dark":dark},{"HRRR particles":velocity,"METAR/ASOS barbs":asos},{collapsed:false}).addTo(map);
map.on("movestart zoomstart",()=>{if(map.hasLayer(velocity)&&velocity._windy)velocity._windy.stop()});map.on("moveend zoomend",()=>{if(map.hasLayer(velocity)&&velocity._clearAndRestart)velocity._clearAndRestart()});
</script></body></html>')

  writeLines(html, output, useBytes = TRUE)
  output
}

brim_hrrr_package_qa <- function(files, summary) {
  manifest_path <- file.path(brim_hrrr_dirs$qa, "HRRR_sandbox_QA_manifest.txt")
  lines <- c(
    "BRIM HRRR wind sandbox QA",
    paste0("Version: ", brim_hrrr_version),
    paste0("Generated UTC: ", brim_hrrr_fmt_iso_utc(Sys.time())),
    paste0("Model cycle UTC: ", summary$model_cycle_utc),
    paste0("Forecast hour: ", summary$forecast_hour_label),
    paste0("Valid UTC: ", summary$valid_time_utc),
    paste0("Source: ", summary$source),
    paste0("Source URL: ", summary$source_url),
    paste0("Domain: ", summary$domain$west, ", ", summary$domain$east, ", ", summary$domain$south, ", ", summary$domain$north),
    paste0("Grid: ", summary$grid$nx, " x ", summary$grid$ny, " at ", summary$grid$resolution_degrees, " degrees"),
    paste0("Earth-relative output confirmed: ", summary$earth_relative_winds_confirmed),
    paste0("Velocity JSON bytes: ", file.info(files$wind_json)$size),
    paste0("ASOS comparison rows: ", summary$asos_comparison$station_count),
    paste0("Median absolute speed difference mph: ", summary$asos_comparison$median_absolute_speed_difference_mph),
    paste0("Median direction difference degrees: ", summary$asos_comparison$median_direction_difference_degrees),
    paste0("HTML: ", files$html)
  )
  writeLines(lines, manifest_path, useBytes = TRUE)

  zip_path <- file.path(brim_hrrr_dirs$qa, "HRRR_sandbox_QA_upload.zip")
  if (file.exists(zip_path)) unlink(zip_path)
  candidates <- unique(c(unlist(files), manifest_path, brim_hrrr_log_file))
  candidates <- candidates[file.exists(candidates)]
  old <- getwd()
  on.exit(setwd(old), add = TRUE)
  setwd(brim_hrrr_project_root)
  relative <- vapply(candidates, function(x) {
    sub(paste0("^", normalizePath(brim_hrrr_project_root, winslash = "/"), "/?"), "", normalizePath(x, winslash = "/"))
  }, character(1))
  utils::zip(zipfile = zip_path, files = relative, flags = "-r9X")
  zip_path
}

# ---- Select and fetch the closest usable HRRR product -----------------------
now_utc <- brim_hrrr_as_utc(Sys.time())
candidates <- brim_hrrr_candidate_table(now_utc)
if (!nrow(candidates)) stop("No HRRR candidate products were generated.")
write.csv(candidates, file.path(brim_hrrr_dirs$debug, "hrrr_candidate_order.csv"), row.names = FALSE, na = "")

selected <- NULL
attempt_errors <- character()
for (i in seq_len(nrow(candidates))) {
  cycle <- brim_hrrr_as_utc(candidates$cycle_time[i])
  fh <- candidates$forecast_hour[i]
  valid <- brim_hrrr_as_utc(candidates$valid_time[i])
  brim_hrrr_log(
    "Trying HRRR cycle ", brim_hrrr_fmt_iso_utc(cycle),
    " f", sprintf("%02d", fh),
    " valid ", brim_hrrr_fmt_iso_utc(valid)
  )
  result <- tryCatch(brim_hrrr_download_candidate(cycle, fh), error = function(e) e)
  if (inherits(result, "error")) {
    attempt_errors <- c(attempt_errors, paste0(brim_hrrr_fmt_iso_utc(cycle), " f", sprintf("%02d", fh), ": ", conditionMessage(result)))
    next
  }
  selected <- c(result, list(cycle_time = cycle, forecast_hour = fh, valid_time = valid))
  break
}
if (is.null(selected)) {
  stop("No usable HRRR product was found. Attempts:\n", paste(attempt_errors, collapse = "\n"))
}
writeLines(attempt_errors, file.path(brim_hrrr_dirs$debug, "hrrr_failed_attempts.txt"), useBytes = TRUE)

brim_hrrr_assert_uv_inventory(selected$path)
regridded_path <- file.path(brim_hrrr_dirs$debug, "hrrr_10m_uv_earth_relative_0p05.grib2")
regrid <- brim_hrrr_regrid(selected$path, regridded_path)

r <- terra::rast(regridded_path)
idx <- brim_hrrr_identify_uv_layers(r)
u <- r[[idx[["u"]]]]
v <- r[[idx[["v"]]]]
if (!terra::compareGeom(u, v, stopOnError = FALSE)) stop("Regridded U/V geometries do not match.")
names(u) <- "UGRD_10m_earth_relative"
names(v) <- "VGRD_10m_earth_relative"

wind_json <- file.path(brim_hrrr_dirs$debug, "hrrr_surface_wind_sandbox.json")
brim_hrrr_write_velocity_json(u, v, wind_json, selected$cycle_time, selected$forecast_hour)
stats <- brim_hrrr_speed_stats(u, v)

asos <- brim_hrrr_fetch_asos_geojson()
comparison <- brim_hrrr_compare_asos(asos, u, v, selected$valid_time)
comparison_csv <- file.path(brim_hrrr_dirs$qa, "hrrr_vs_asos_station_comparison.csv")
if (nrow(comparison$table)) write.csv(comparison$table, comparison_csv, row.names = FALSE, na = "") else writeLines("No ASOS comparison rows available.", comparison_csv)

valid_speed <- comparison$table$speed_difference_mph
valid_dir <- comparison$table$direction_difference_degrees
comparison_summary <- list(
  station_count = nrow(comparison$table),
  station_count_with_speed_comparison = sum(is.finite(valid_speed)),
  station_count_with_direction_comparison = sum(is.finite(valid_dir)),
  median_absolute_speed_difference_mph = if (any(is.finite(valid_speed))) unname(stats::median(abs(valid_speed), na.rm = TRUE)) else NA_real_,
  median_direction_difference_degrees = if (any(is.finite(valid_dir))) unname(stats::median(valid_dir, na.rm = TRUE)) else NA_real_,
  note = "Point observations and gridded HRRR values need not match because terrain, exposure, elevation, observation time, and representativeness differ."
)

summary <- list(
  version = brim_hrrr_version,
  product_id = "hrrr_surface_wind_sandbox",
  status = "sandbox_complete",
  model = "NOAA/NCEP HRRR",
  level = "10 m above ground",
  variables = c("UGRD", "VGRD"),
  model_cycle_utc = brim_hrrr_fmt_iso_utc(selected$cycle_time),
  model_cycle_local = brim_hrrr_fmt_local(selected$cycle_time, brim_hrrr_local_tz),
  forecast_hour = as.integer(selected$forecast_hour),
  forecast_hour_label = paste0("f", sprintf("%02d", as.integer(selected$forecast_hour))),
  valid_time_utc = brim_hrrr_fmt_iso_utc(selected$valid_time),
  valid_time_local = brim_hrrr_fmt_local(selected$valid_time, brim_hrrr_local_tz),
  valid_lag_minutes_at_build = unname(as.numeric(difftime(now_utc, selected$valid_time, units = "mins"))),
  source = selected$source,
  source_url = selected$source_url,
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
  vector_regrid_method = "wgrib2 -new_grid_winds earth; bilinear interpolation to regular lon/lat",
  speed_ms = stats$speed_ms,
  speed_mph = stats$speed_mph,
  recommended_velocity_scale_ms = stats$recommended_velocity_scale_ms,
  recommended_velocity_scale_mph = stats$recommended_velocity_scale_mph,
  velocity_scale_reference = stats$velocity_scale_reference,
  asos_source_url = asos$url,
  asos_source_error = asos$error,
  asos_comparison = comparison_summary,
  generated_utc = brim_hrrr_fmt_iso_utc(Sys.time()),
  generated_local = brim_hrrr_fmt_local(Sys.time(), brim_hrrr_local_tz),
  intended_use = "BRIM sandbox review only; not an operational feed or official forecast product."
)
summary_json <- file.path(brim_hrrr_dirs$debug, "hrrr_surface_wind_sandbox_summary.json")
jsonlite::write_json(summary, summary_json, auto_unbox = TRUE, pretty = TRUE, digits = 6, na = "null", null = "null")

wind_json_text <- paste(readLines(wind_json, warn = FALSE, encoding = "UTF-8"), collapse = "")
html_path <- file.path(brim_hrrr_dirs$html, paste0("brim_hrrr_wind_sandbox_", brim_hrrr_stamp, ".html"))
brim_hrrr_build_html(wind_json_text, comparison$object, summary, html_path)
latest_html <- file.path(brim_hrrr_dirs$html, "brim_hrrr_wind_sandbox_latest.html")
file.copy(html_path, latest_html, overwrite = TRUE)

files <- list(
  wind_json = wind_json,
  summary_json = summary_json,
  comparison_csv = comparison_csv,
  regridded_grib = regridded_path,
  html = html_path,
  latest_html = latest_html,
  input_inventory = file.path(brim_hrrr_dirs$debug, "hrrr_input_inventory.txt"),
  output_inventory = file.path(brim_hrrr_dirs$debug, "hrrr_regridded_inventory.txt"),
  candidate_order = file.path(brim_hrrr_dirs$debug, "hrrr_candidate_order.csv"),
  failed_attempts = file.path(brim_hrrr_dirs$debug, "hrrr_failed_attempts.txt")
)
qa_zip <- brim_hrrr_package_qa(files, summary)

brim_hrrr_log("HRRR sandbox complete.")
brim_hrrr_log("Selected: ", summary$model_cycle_utc, " ", summary$forecast_hour_label, " valid ", summary$valid_time_utc)
brim_hrrr_log("Source: ", summary$source)
brim_hrrr_log("Velocity JSON: ", wind_json, " (", file.info(wind_json)$size, " bytes)")
brim_hrrr_log("Sandbox HTML: ", latest_html)
brim_hrrr_log("QA ZIP: ", qa_zip)

cat(
  "\nHRRR sandbox complete.\n",
  "Selected model cycle: ", summary$model_cycle_local, "\n",
  "Forecast hour: ", summary$forecast_hour_label, "\n",
  "Valid: ", summary$valid_time_local, "\n",
  "Source: ", summary$source, "\n",
  "Grid: ", summary$grid$nx, " x ", summary$grid$ny, " at ", summary$grid$resolution_degrees, " degrees\n",
  "Sandbox HTML: ", normalizePath(latest_html, winslash = "/", mustWork = FALSE), "\n",
  "QA ZIP: ", normalizePath(qa_zip, winslash = "/", mustWork = FALSE), "\n",
  sep = ""
)

if (interactive() && brim_hrrr_open_html && file.exists(latest_html)) {
  utils::browseURL(normalizePath(latest_html, winslash = "/", mustWork = TRUE))
}
