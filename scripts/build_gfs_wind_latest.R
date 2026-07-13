# BRIM GFS wind live-feed builder -------------------------------------------
# Converts NOAA/NCEP GFS 0.25-degree 10-m U/V wind into Leaflet-velocity
# compatible JSON.  RTW014/RTW015 changes the product from one fixed f003 field to a
# short current-ish time set so BRIM can choose the closest valid hour at
# layer-enable time. RTW015 adds robust wind-speed distribution statistics so
# BRIM can scale particle colors dynamically without scanning U/V arrays in the
# browser.

brim_rtw_env <- function(name, default = "") {
  value <- Sys.getenv(name, unset = default)
  if (!nzchar(value)) default else value
}

brim_rtw_bool <- function(x, default = FALSE) {
  if (is.null(x) || !nzchar(x)) return(default)
  tolower(x) %in% c("1", "true", "yes", "y", "on")
}

brim_rtw_null <- function(x, y) {
  if (is.null(x) || length(x) == 0L || is.na(x)) y else x
}

brim_rtw_project_root <- normalizePath(
  brim_rtw_env("BRIM_RTW_PROJECT_ROOT", getwd()),
  winslash = "/",
  mustWork = FALSE
)
if (!dir.exists(brim_rtw_project_root)) {
  stop("Project root does not exist: ", brim_rtw_project_root)
}
setwd(brim_rtw_project_root)

brim_rtw_version <- "RTW015"
brim_rtw_local_tz <- brim_rtw_env("BRIM_RTW_LOCAL_TZ", "America/Los_Angeles")
brim_rtw_build_sandbox <- brim_rtw_bool(brim_rtw_env("BRIM_RTW_BUILD_SANDBOX", "true"), TRUE)
brim_rtw_build_qa_zip <- brim_rtw_bool(brim_rtw_env("BRIM_RTW_BUILD_QA_ZIP", "true"), TRUE)
brim_rtw_force_refresh <- brim_rtw_bool(brim_rtw_env("BRIM_RTW_FORCE_REFRESH", "false"), FALSE)

# Time-set strategy.  BRIM should use the manifest and select the closest valid
# hour in the browser.  The legacy latest.json is still written as the closest
# valid field at build time so the older one-file integration keeps working.
brim_rtw_target_past_hours <- as.integer(brim_rtw_env("BRIM_RTW_TARGET_PAST_HOURS", "2"))
brim_rtw_target_future_hours <- as.integer(brim_rtw_env("BRIM_RTW_TARGET_FUTURE_HOURS", "8"))
brim_rtw_max_forecast_hour <- as.integer(brim_rtw_env("BRIM_RTW_MAX_FORECAST_HOUR", "9"))
brim_rtw_cycle_lookback_hours <- as.integer(brim_rtw_env("BRIM_RTW_CYCLE_LOOKBACK_HOURS", "24"))
brim_rtw_stale_after_hours <- as.numeric(brim_rtw_env("BRIM_RTW_STALE_AFTER_HOURS", "9"))
brim_rtw_min_json_bytes <- as.integer(brim_rtw_env("BRIM_RTW_MIN_JSON_BYTES", "100000"))
brim_rtw_min_grib_bytes <- as.integer(brim_rtw_env("BRIM_RTW_MIN_GRIB_BYTES", "10000"))

# Dynamic particle-color scaling.  The browser should normally use the selected
# manifest entry's recommended_velocity_scale_ms, or the same field in the
# legacy summary. This is intentionally based on a robust upper percentile
# rather than the absolute max.
brim_rtw_scale_quantile <- as.numeric(brim_rtw_env("BRIM_RTW_SCALE_QUANTILE", "0.95"))
brim_rtw_scale_multiplier <- as.numeric(brim_rtw_env("BRIM_RTW_SCALE_MULTIPLIER", "1.10"))
brim_rtw_scale_floor_ms <- as.numeric(brim_rtw_env("BRIM_RTW_SCALE_FLOOR_MS", "8"))
brim_rtw_scale_ceiling_ms <- as.numeric(brim_rtw_env("BRIM_RTW_SCALE_CEILING_MS", "35"))

# Broad North Pacific / North America domain. NOMADS uses 0-360 longitudes.
brim_rtw_domain <- list(
  id = "broad_npac_namerica",
  label = "Broad North Pacific / North America",
  leftlon_360 = 180,
  rightlon_360 = 300,
  bottomlat = 0,
  toplat = 75,
  west_display = -180,
  east_display = -60
)

brim_rtw_dirs <- list(
  cache_grib = file.path(brim_rtw_project_root, "data", "cache", "wind", "grib"),
  logs = file.path(brim_rtw_project_root, "debug", "wind"),
  qa = file.path(brim_rtw_project_root, "qa", "wind"),
  sandbox = file.path(brim_rtw_project_root, "debug", "wind", "html"),
  publish = file.path(brim_rtw_project_root, "docs", "data", "wind"),
  timeset = file.path(brim_rtw_project_root, "docs", "data", "wind", "gfs", "surface")
)
for (d in brim_rtw_dirs) dir.create(d, recursive = TRUE, showWarnings = FALSE)

brim_rtw_files <- list(
  wind_json = file.path(brim_rtw_dirs$publish, "gfs_surface_wind_latest.json"),
  summary_json = file.path(brim_rtw_dirs$publish, "gfs_surface_wind_latest_summary.json"),
  manifest_json = file.path(brim_rtw_dirs$publish, "gfs_surface_wind_feed_manifest.json")
)

brim_rtw_install_missing <- function(pkgs) {
  missing <- pkgs[!vapply(pkgs, requireNamespace, quietly = TRUE, FUN.VALUE = logical(1))]
  if (length(missing)) install.packages(missing, repos = "https://cloud.r-project.org")
}
brim_rtw_install_missing(c("httr2", "jsonlite", "terra"))

brim_rtw_log_file <- file.path(
  brim_rtw_dirs$logs,
  paste0("gfs_wind_run_", format(Sys.time(), "%Y%m%d_%H%M%S"), ".log")
)

brim_rtw_log <- function(...) {
  txt <- paste0(...)
  message(txt)
  try(cat(txt, "\n", file = brim_rtw_log_file, append = TRUE, sep = ""), silent = TRUE)
}

brim_rtw_fmt_iso_utc <- function(x) {
  format(as.POSIXct(x, tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
}

brim_rtw_fmt_local <- function(x) {
  format(as.POSIXct(x, tz = "UTC"), "%Y-%m-%d %I:%M:%S %p %Z", tz = brim_rtw_local_tz)
}

brim_rtw_parse_iso_utc <- function(x) {
  as.POSIXct(strptime(x, format = "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"), tz = "UTC")
}

brim_rtw_as_utc_posix <- function(x) {
  if (inherits(x, "POSIXt")) return(as.POSIXct(x, tz = "UTC"))
  as.POSIXct(as.numeric(x), origin = "1970-01-01", tz = "UTC")
}

brim_rtw_floor_hour <- function(x) {
  x <- brim_rtw_as_utc_posix(x)
  as.POSIXct(format(x, "%Y-%m-%d %H:00:00", tz = "UTC"), tz = "UTC")
}

brim_rtw_cycle_candidates <- function(now_utc = Sys.time(), lookback_hours = 24L) {
  now_utc <- brim_rtw_as_utc_posix(now_utc)
  start_hour <- as.integer(format(now_utc, "%H", tz = "UTC"))
  cycle_hour <- max(c(0L, 6L, 12L, 18L)[c(0L, 6L, 12L, 18L) <= start_hour])
  anchor <- as.POSIXct(
    sprintf("%s %02d:00:00", format(now_utc, "%Y-%m-%d", tz = "UTC"), cycle_hour),
    tz = "UTC"
  )
  n <- ceiling(lookback_hours / 6) + 1L
  anchor - seq.int(0, by = 6 * 3600, length.out = n)
}

brim_rtw_target_valid_times <- function(now_utc = Sys.time()) {
  now_hour <- brim_rtw_floor_hour(now_utc)
  offsets <- seq.int(-brim_rtw_target_past_hours, brim_rtw_target_future_hours)
  now_hour + offsets * 3600
}

brim_rtw_nomads_url <- function(cycle_time, forecast_hour) {
  cycle_time <- brim_rtw_as_utc_posix(cycle_time)
  ymd <- format(cycle_time, "%Y%m%d", tz = "UTC")
  hh <- format(cycle_time, "%H", tz = "UTC")
  fff <- sprintf("%03d", as.integer(forecast_hour))

  query <- c(
    file = sprintf("gfs.t%sz.pgrb2.0p25.f%s", hh, fff),
    lev_10_m_above_ground = "on",
    var_UGRD = "on",
    var_VGRD = "on",
    subregion = "",
    leftlon = brim_rtw_domain$leftlon_360,
    rightlon = brim_rtw_domain$rightlon_360,
    toplat = brim_rtw_domain$toplat,
    bottomlat = brim_rtw_domain$bottomlat,
    dir = sprintf("/gfs.%s/%s/atmos", ymd, hh)
  )

  paste0(
    "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?",
    paste0(utils::URLencode(names(query), reserved = TRUE), "=",
           utils::URLencode(unname(query), reserved = TRUE), collapse = "&")
  )
}

brim_rtw_product_filename <- function(cycle_time, forecast_hour) {
  cycle_time <- brim_rtw_as_utc_posix(cycle_time)
  paste0(
    "gfs_surface_wind_",
    format(cycle_time, "%Y%m%dT%HZ", tz = "UTC"),
    "_f", sprintf("%03d", as.integer(forecast_hour)),
    ".json"
  )
}

brim_rtw_rel_url <- function(path) {
  # Relative to docs/data/wind/gfs_surface_wind_feed_manifest.json location.
  sub(paste0("^", normalizePath(brim_rtw_dirs$publish, winslash = "/", mustWork = FALSE), "/?"),
      "", normalizePath(path, winslash = "/", mustWork = FALSE))
}

brim_rtw_identify_uv_layers <- function(r) {
  labels <- paste(names(r), terra::longnames(r), sep = " | ")
  u_idx <- grep("(^|[^A-Z])UGRD|U-component|u-component|10 metre U", labels, ignore.case = TRUE)
  v_idx <- grep("(^|[^A-Z])VGRD|V-component|v-component|10 metre V", labels, ignore.case = TRUE)
  if (!length(u_idx) || !length(v_idx)) {
    stop("Could not identify U/V layers in GRIB2. Layers found:\n",
         paste(seq_along(labels), labels, sep = ": ", collapse = "\n"))
  }
  c(u = u_idx[1], v = v_idx[1])
}

brim_rtw_normalize_longitude <- function(r) {
  ex <- terra::ext(r)
  if (terra::xmax(ex) > 180) {
    terra::ext(r) <- terra::ext(
      terra::xmin(ex) - 360,
      terra::xmax(ex) - 360,
      terra::ymin(ex),
      terra::ymax(ex)
    )
  }
  r
}

brim_rtw_flatten_north_to_south <- function(layer) {
  m <- terra::as.matrix(layer, wide = TRUE)
  ys <- terra::yFromRow(layer, seq_len(terra::nrow(layer)))
  if (length(ys) > 1L && ys[1] < ys[length(ys)]) {
    m <- m[nrow(m):1, , drop = FALSE]
  }
  as.numeric(t(m))
}

brim_rtw_velocity_record <- function(layer, component, cycle_time, forecast_hour) {
  stopifnot(component %in% c("u", "v"))
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
      refTime = brim_rtw_fmt_iso_utc(cycle_time),
      forecastTime = as.integer(forecast_hour)
    ),
    data = brim_rtw_flatten_north_to_south(layer)
  )
}

brim_rtw_download_grib <- function(cycle_time, forecast_hour) {
  url <- brim_rtw_nomads_url(cycle_time, forecast_hour)
  stamp <- format(brim_rtw_as_utc_posix(cycle_time), "%Y%m%d_%Hz", tz = "UTC")
  dest <- file.path(
    brim_rtw_dirs$cache_grib,
    sprintf("gfs_%s_%s_f%03d_10m_uv.grib2", stamp, brim_rtw_domain$id, as.integer(forecast_hour))
  )

  req <- httr2::request(url) |>
    httr2::req_user_agent("BRIM-realtimewind/0.2 (NOAA GFS time-set feed)") |>
    httr2::req_timeout(120)

  resp <- httr2::req_perform(req, path = dest)
  status <- httr2::resp_status(resp)
  size <- file.info(dest)$size
  if (is.na(size)) size <- 0
  if (status != 200L || size < brim_rtw_min_grib_bytes) {
    if (file.exists(dest)) unlink(dest)
    stop("HTTP ", status, "; downloaded bytes: ", size)
  }
  list(path = dest, url = url, bytes = unname(size))
}

brim_rtw_convert_grib_to_velocity_json <- function(grib_path, out_json, cycle_time, forecast_hour) {
  r <- tryCatch(
    terra::rast(grib_path),
    error = function(e) stop("terra/GDAL could not read the downloaded GRIB2 file. Original error: ", conditionMessage(e))
  )
  idx <- brim_rtw_identify_uv_layers(r)
  u <- brim_rtw_normalize_longitude(r[[idx[["u"]]]])
  v <- brim_rtw_normalize_longitude(r[[idx[["v"]]]])
  if (!terra::compareGeom(u, v, stopOnError = FALSE)) {
    stop("U and V grids do not have matching geometry.")
  }
  velocity <- list(
    brim_rtw_velocity_record(u, "u", cycle_time, forecast_hour),
    brim_rtw_velocity_record(v, "v", cycle_time, forecast_hour)
  )
  jsonlite::write_json(
    velocity,
    out_json,
    auto_unbox = TRUE,
    digits = 5,
    pretty = FALSE,
    na = "null"
  )
  if (file.info(out_json)$size < brim_rtw_min_json_bytes) {
    stop("Converted wind JSON was unexpectedly small: ", file.info(out_json)$size, " bytes")
  }
  list(
    grid = list(nx = terra::ncol(u), ny = terra::nrow(u), dx = terra::res(u)[1], dy = terra::res(u)[2]),
    domain = list(west = terra::xmin(u), east = terra::xmax(u), south = terra::ymin(u), north = terra::ymax(u))
  )
}

brim_rtw_speed_stats_from_velocity_json <- function(path) {
  obj <- jsonlite::read_json(path, simplifyVector = FALSE)
  if (length(obj) < 2L) return(NULL)
  u <- unlist(obj[[1]]$data, use.names = FALSE)
  v <- unlist(obj[[2]]$data, use.names = FALSE)
  n <- min(length(u), length(v))
  if (!n) return(NULL)

  speed <- sqrt(u[seq_len(n)]^2 + v[seq_len(n)]^2)
  speed <- speed[is.finite(speed)]
  if (!length(speed)) return(NULL)

  probs <- c(min = 0, p50 = 0.50, p75 = 0.75, p90 = 0.90, p95 = 0.95, max = 1)
  q_ms <- unname(stats::quantile(speed, probs = probs, na.rm = TRUE, names = FALSE, type = 7))
  names(q_ms) <- names(probs)
  q_mph <- q_ms * 2.23694
  names(q_mph) <- names(probs)

  scale_ref_ms <- unname(q_ms[["p95"]] * brim_rtw_scale_multiplier)
  scale_ms <- min(max(scale_ref_ms, brim_rtw_scale_floor_ms), brim_rtw_scale_ceiling_ms)
  scale_mph <- scale_ms * 2.23694

  speed_ms <- as.list(unname(q_ms))
  names(speed_ms) <- names(q_ms)
  speed_ms$median <- speed_ms$p50

  speed_mph <- as.list(unname(q_mph))
  names(speed_mph) <- names(q_mph)
  speed_mph$median <- speed_mph$p50

  flat <- list(
    speed_ms_min = unname(q_ms[["min"]]),
    speed_ms_p50 = unname(q_ms[["p50"]]),
    speed_ms_p75 = unname(q_ms[["p75"]]),
    speed_ms_p90 = unname(q_ms[["p90"]]),
    speed_ms_p95 = unname(q_ms[["p95"]]),
    speed_ms_max = unname(q_ms[["max"]]),
    speed_mph_min = unname(q_mph[["min"]]),
    speed_mph_p50 = unname(q_mph[["p50"]]),
    speed_mph_p75 = unname(q_mph[["p75"]]),
    speed_mph_p90 = unname(q_mph[["p90"]]),
    speed_mph_p95 = unname(q_mph[["p95"]]),
    speed_mph_max = unname(q_mph[["max"]]),
    recommended_velocity_scale_ms = unname(scale_ms),
    recommended_velocity_scale_mph = unname(scale_mph),
    velocity_scale_reference = sprintf(
      "p95 * %.2f, clamped to %.1f-%.1f m/s",
      brim_rtw_scale_multiplier,
      brim_rtw_scale_floor_ms,
      brim_rtw_scale_ceiling_ms
    )
  )

  c(
    list(
      speed_ms = speed_ms,
      speed_mph = speed_mph,
      recommended_velocity_scale_ms = unname(scale_ms),
      recommended_velocity_scale_mph = unname(scale_mph),
      velocity_scale_reference = flat$velocity_scale_reference
    ),
    flat
  )
}

brim_rtw_candidate_products_for_valid <- function(valid_time, cycles) {
  valid_time <- brim_rtw_as_utc_posix(valid_time)
  rows <- list()
  for (cycle in cycles) {
    cycle <- brim_rtw_as_utc_posix(cycle)
    fh <- as.numeric(difftime(valid_time, cycle, units = "hours"))
    if (abs(fh - round(fh)) < 1e-6) {
      fh <- as.integer(round(fh))
      if (fh >= 0L && fh <= brim_rtw_max_forecast_hour) {
        rows[[length(rows) + 1L]] <- list(cycle_time = cycle, forecast_hour = fh, valid_time = valid_time)
      }
    }
  }
  rows
}

brim_rtw_build_or_reuse_product <- function(valid_time, cycles, now_utc) {
  products <- brim_rtw_candidate_products_for_valid(valid_time, cycles)
  if (!length(products)) return(NULL)

  # Newest cycle first. If the newest cycle/hour is unavailable, fall back to an
  # older cycle that has the same valid hour.
  products <- products[order(vapply(products, function(x) as.numeric(x$cycle_time), numeric(1)), decreasing = TRUE)]
  errors <- character()

  for (prod in products) {
    cycle_time <- brim_rtw_as_utc_posix(prod$cycle_time)
    forecast_hour <- as.integer(prod$forecast_hour)
    out_json <- file.path(brim_rtw_dirs$timeset, brim_rtw_product_filename(cycle_time, forecast_hour))
    source_url <- brim_rtw_nomads_url(cycle_time, forecast_hour)
    source_bytes <- NA_real_
    grid <- NULL
    domain <- NULL
    status <- "reused_existing"

    if (!brim_rtw_force_refresh && file.exists(out_json) && file.info(out_json)$size >= brim_rtw_min_json_bytes) {
      brim_rtw_log("Reusing GFS wind file for valid ", brim_rtw_fmt_iso_utc(valid_time),
                   ": cycle ", brim_rtw_fmt_iso_utc(cycle_time),
                   " f", sprintf("%03d", forecast_hour))
    } else {
      brim_rtw_log("Trying GFS valid ", brim_rtw_fmt_iso_utc(valid_time),
                   " from cycle ", brim_rtw_fmt_iso_utc(cycle_time),
                   " f", sprintf("%03d", forecast_hour), " ...")
      result <- tryCatch({
        dl <- brim_rtw_download_grib(cycle_time, forecast_hour)
        conv <- brim_rtw_convert_grib_to_velocity_json(dl$path, out_json, cycle_time, forecast_hour)
        source_url <- dl$url
        source_bytes <- dl$bytes
        grid <- conv$grid
        domain <- conv$domain
        status <- "downloaded"
        TRUE
      }, error = function(e) e)
      if (inherits(result, "error")) {
        errors <- c(errors, sprintf("%s f%03d: %s", brim_rtw_fmt_iso_utc(cycle_time), forecast_hour, conditionMessage(result)))
        if (file.exists(out_json) && file.info(out_json)$size < brim_rtw_min_json_bytes) unlink(out_json)
        next
      }
    }

    speed_stats <- brim_rtw_speed_stats_from_velocity_json(out_json)

    entry <- list(
      product_id = "gfs_surface_wind",
      model = "NOAA GFS 0.25-degree",
      level = "10 m above ground",
      variables = c("UGRD", "VGRD"),
      domain_id = brim_rtw_domain$id,
      domain_label = brim_rtw_domain$label,
      model_cycle_utc = brim_rtw_fmt_iso_utc(cycle_time),
      model_cycle_local = brim_rtw_fmt_local(cycle_time),
      forecast_hour = forecast_hour,
      forecast_hour_label = paste0("f", sprintf("%03d", forecast_hour)),
      valid_time_utc = brim_rtw_fmt_iso_utc(valid_time),
      valid_time_local = brim_rtw_fmt_local(valid_time),
      valid_lag_minutes_at_build = unname(as.numeric(difftime(now_utc, valid_time, units = "mins"))),
      relative_url = brim_rtw_rel_url(out_json),
      filename = basename(out_json),
      file_bytes = unname(file.info(out_json)$size),
      status = status,
      source_request_url = source_url,
      source_grib_bytes = source_bytes
    )
    if (!is.null(speed_stats)) {
      entry$speed_ms <- speed_stats$speed_ms
      entry$speed_mph <- speed_stats$speed_mph
      entry$recommended_velocity_scale_ms <- speed_stats$recommended_velocity_scale_ms
      entry$recommended_velocity_scale_mph <- speed_stats$recommended_velocity_scale_mph
      entry$velocity_scale_reference <- speed_stats$velocity_scale_reference
      for (nm in names(speed_stats)) {
        if (grepl("^speed_(ms|mph)_", nm) || nm %in% c("recommended_velocity_scale_ms", "recommended_velocity_scale_mph", "velocity_scale_reference")) {
          entry[[nm]] <- speed_stats[[nm]]
        }
      }
    }
    if (!is.null(grid)) entry$grid <- grid
    if (!is.null(domain)) entry$domain <- domain
    return(entry)
  }

  brim_rtw_log("No usable GFS product found for valid ", brim_rtw_fmt_iso_utc(valid_time),
               ". Attempts: ", paste(errors, collapse = " | "))
  NULL
}

brim_rtw_select_best_entry <- function(entries, now_utc) {
  if (!length(entries)) return(NULL)
  valid_times <- brim_rtw_parse_iso_utc(vapply(entries, function(x) x$valid_time_utc, character(1)))
  cycles <- brim_rtw_parse_iso_utc(vapply(entries, function(x) x$model_cycle_utc, character(1)))
  good <- is.finite(as.numeric(valid_times)) & is.finite(as.numeric(cycles))
  if (!any(good)) return(entries[[1]])
  entries <- entries[good]
  valid_times <- valid_times[good]
  cycles <- cycles[good]
  diffs <- abs(as.numeric(difftime(valid_times, now_utc, units = "mins")))
  future <- valid_times > now_utc
  fhs <- vapply(entries, function(x) as.integer(x$forecast_hour), integer(1))
  ord <- order(diffs, future, -as.numeric(cycles), fhs)
  entries[[ord[1]]]
}

brim_rtw_cleanup_timeset <- function(keep_relative_urls) {
  files <- list.files(brim_rtw_dirs$timeset, pattern = "^gfs_surface_wind_.*_f[0-9]{3}\\.json$", full.names = TRUE)
  if (!length(files)) return(invisible(character()))
  keep_abs <- file.path(brim_rtw_dirs$publish, keep_relative_urls)
  keep_abs <- normalizePath(keep_abs, winslash = "/", mustWork = FALSE)
  files_norm <- normalizePath(files, winslash = "/", mustWork = FALSE)
  drop <- files[!files_norm %in% keep_abs]
  if (length(drop)) {
    brim_rtw_log("Removing old GFS time-set files: ", length(drop))
    unlink(drop)
  }
  invisible(drop)
}

brim_rtw_sandbox_html <- function(wind_json_text, summary, manifest) {
  stamp <- format(Sys.time(), "%Y%m%d_%H%M%S")
  out <- file.path(brim_rtw_dirs$sandbox, paste0("brim_gfs_wind_sandbox_", stamp, ".html"))
  meta_json <- jsonlite::toJSON(summary, auto_unbox = TRUE, digits = 6)
  manifest_json <- jsonlite::toJSON(manifest, auto_unbox = TRUE, digits = 6)

  html <- paste0(
'<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BRIM GFS wind sandbox</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
html, body, #map { width:100%; height:100%; margin:0; background:#111; }
.info-panel { background:rgba(18,22,27,.90); color:#f4f6f8; padding:10px 12px; border-radius:5px;
  font:13px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; box-shadow:0 1px 8px rgba(0,0,0,.45); max-width:340px; }
.info-panel strong { font-size:14px; }
.info-panel .muted { color:#b8c0c8; }
.leaflet-control-velocity { background:rgba(18,22,27,.90)!important; color:#fff!important; }
</style>
</head>
<body>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet-velocity@2.1.4/dist/leaflet-velocity.min.js"></script>
<script>
const windData = ', wind_json_text, ';
const meta = ', meta_json, ';
const manifest = ', manifest_json, ';
const map = L.map("map", { preferCanvas:true, zoomControl:true }).setView([40,-120], 3);
const dark = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors &copy; CARTO | Wind: NOAA/NCEP GFS"
}).addTo(map);
const light = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors | Wind: NOAA/NCEP GFS"
});
const scaleMs = Number(meta.recommended_velocity_scale_ms || (meta.speed_ms && meta.speed_ms.p95) || 25);
const scaleMph = Number(meta.recommended_velocity_scale_mph || scaleMs * 2.23694);
const p95Mph = Number(meta.speed_mph_p95 || (meta.speed_mph && meta.speed_mph.p95) || 0);
const maxMph = Number(meta.speed_mph_max || (meta.speed_mph && meta.speed_mph.max) || 0);
const wind = L.velocityLayer({
  displayValues: true,
  displayOptions: {
    velocityType: "GFS 10-m wind",
    position: "bottomleft",
    emptyString: "No wind data",
    angleConvention: "bearingCW",
    speedUnit: "mph",
    showCardinal: true
  },
  data: windData,
  minVelocity: 0,
  maxVelocity: scaleMs,
  velocityScale: 0.006,
  particleAge: 90,
  particleMultiplier: 1 / 280,
  lineWidth: 1.2,
  colorScale: ["#b6d7ff", "#d7f0ff", "#fff7bd", "#ffd47d", "#ff9a65", "#ef5b5b", "#c73b66"]
}).addTo(map);
const info = L.control({position:"topright"});
info.onAdd = function() {
  const div = L.DomUtil.create("div", "info-panel");
  const valid = new Date(meta.valid_time_utc).toLocaleString(undefined, {timeZone:"America/Los_Angeles", timeZoneName:"short"});
  const cycle = new Date(meta.model_cycle_utc).toLocaleString(undefined, {timeZone:"America/Los_Angeles", timeZoneName:"short"});
  const stale = meta.is_stale ? `<br><span style="color:#ffd47d">stale feed</span>` : "";
  const lag = Math.round(meta.valid_lag_minutes_at_build || 0);
  const available = manifest.entries ? manifest.entries.length : 0;
  div.innerHTML = `<strong>NOAA GFS 10-m wind</strong><br>${meta.domain_label}<br>Selected closest available valid hour<br>Valid: ${valid}<br><span class="muted">GFS run: ${cycle} · ${meta.forecast_hour_label}<br>${meta.grid.nx} × ${meta.grid.ny} grid · ${available} time files<br>valid lag at build: ${lag} min<br>color scale: p95 ${Math.round(p95Mph)} mph; domain max ${Math.round(maxMph)} mph; maxVelocity ${Math.round(scaleMph)} mph<br>Particle color scaled to current domain winds.</span>${stale}`;
  return div;
};
info.addTo(map);
L.control.layers({"Dark":dark,"Light":light},{"Animated GFS wind":wind},{collapsed:false}).addTo(map);
map.on("movestart zoomstart", () => { if (map.hasLayer(wind) && wind._windy) wind._windy.stop(); });
map.on("moveend zoomend", () => { if (map.hasLayer(wind) && wind._clearAndRestart) wind._clearAndRestart(); });
</script>
</body>
</html>')

  writeLines(html, out, useBytes = TRUE)
  out
}

brim_rtw_package_qa <- function(summary, manifest, log_file) {
  manifest_txt <- file.path(brim_rtw_dirs$qa, "RTW_QA_manifest.txt")
  lines <- c(
    "BRIM realtime wind QA manifest",
    paste0("Generated: ", format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z")),
    paste0("Version: ", brim_rtw_version),
    paste0("Project root: ", brim_rtw_project_root),
    paste0("Log: ", normalizePath(log_file, winslash = "/", mustWork = FALSE)),
    paste0("Latest wind JSON: ", normalizePath(brim_rtw_files$wind_json, winslash = "/", mustWork = FALSE)),
    paste0("Summary JSON: ", normalizePath(brim_rtw_files$summary_json, winslash = "/", mustWork = FALSE)),
    paste0("Manifest JSON: ", normalizePath(brim_rtw_files$manifest_json, winslash = "/", mustWork = FALSE)),
    "",
    paste0("Status: ", summary$status),
    paste0("Mode: ", summary$feed_mode),
    paste0("Available time entries: ", length(manifest$entries)),
    paste0("Selected model cycle UTC: ", summary$model_cycle_utc),
    paste0("Selected forecast hour: ", summary$forecast_hour_label),
    paste0("Selected valid UTC: ", summary$valid_time_utc),
    paste0("Selected valid lag minutes at build: ", round(summary$valid_lag_minutes_at_build, 1)),
    paste0("Speed p95 mph: ", round(summary$speed_mph_p95, 1)),
    paste0("Speed max mph: ", round(summary$speed_mph_max, 1)),
    paste0("Recommended velocity scale mph: ", round(summary$recommended_velocity_scale_mph, 1)),
    paste0("Velocity scale reference: ", summary$velocity_scale_reference),
    paste0("Domain: ", summary$domain_label),
    paste0("Grid: ", summary$grid$nx, " x ", summary$grid$ny, " @ ", summary$grid$dx, " deg"),
    paste0("Latest wind JSON bytes: ", file.info(brim_rtw_files$wind_json)$size),
    paste0("Summary JSON bytes: ", file.info(brim_rtw_files$summary_json)$size),
    paste0("Manifest bytes: ", file.info(brim_rtw_files$manifest_json)$size)
  )
  writeLines(lines, manifest_txt, useBytes = TRUE)

  qa_zip <- file.path(brim_rtw_dirs$qa, "RTW_latest_QA_upload.zip")
  if (file.exists(qa_zip)) unlink(qa_zip)
  old_wd <- getwd()
  on.exit(setwd(old_wd), add = TRUE)
  setwd(brim_rtw_project_root)
  files <- c(manifest_txt, log_file, brim_rtw_files$summary_json, brim_rtw_files$manifest_json)
  files <- files[file.exists(files)]
  utils::zip(zipfile = qa_zip, files = files, flags = "-j -X")
  qa_zip
}

brim_rtw_log("BRIM GFS wind live-feed build started: ", format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z"))
brim_rtw_log("Project root: ", brim_rtw_project_root)
brim_rtw_log("Version: ", brim_rtw_version)
brim_rtw_log("Build sandbox HTML: ", brim_rtw_build_sandbox)
brim_rtw_log("Time-set target window: now -", brim_rtw_target_past_hours,
             "h to now +", brim_rtw_target_future_hours, "h; max forecast hour f",
             sprintf("%03d", brim_rtw_max_forecast_hour))

run_error <- NULL
run_summary <- NULL
run_manifest <- NULL
sandbox_html <- NULL
qa_zip <- NULL

tryCatch({
  now_utc <- brim_rtw_as_utc_posix(Sys.time())
  cycles <- brim_rtw_cycle_candidates(now_utc, brim_rtw_cycle_lookback_hours)
  valid_targets <- brim_rtw_target_valid_times(now_utc)
  entries <- list()

  brim_rtw_log("Target valid hours: ", paste(brim_rtw_fmt_iso_utc(valid_targets), collapse = ", "))
  brim_rtw_log("Candidate cycles: ", paste(brim_rtw_fmt_iso_utc(cycles), collapse = ", "))

  for (valid_time in valid_targets) {
    entry <- brim_rtw_build_or_reuse_product(valid_time, cycles, now_utc)
    if (!is.null(entry)) entries[[length(entries) + 1L]] <- entry
  }

  if (!length(entries)) stop("No usable GFS wind products were available for the target valid-time window.")

  # Sort entries by valid time, then cycle time.
  entries <- entries[order(
    vapply(entries, function(x) x$valid_time_utc, character(1)),
    vapply(entries, function(x) x$model_cycle_utc, character(1))
  )]

  selected <- brim_rtw_select_best_entry(entries, now_utc)
  if (is.null(selected)) stop("Could not select a closest GFS wind product from available entries.")

  selected_abs <- file.path(brim_rtw_dirs$publish, selected$relative_url)
  if (!file.exists(selected_abs)) stop("Selected wind JSON is missing: ", selected_abs)

  file.copy(selected_abs, brim_rtw_files$wind_json, overwrite = TRUE)
  speed_stats <- brim_rtw_speed_stats_from_velocity_json(brim_rtw_files$wind_json)
  if (is.null(speed_stats)) stop("Could not calculate speed distribution from selected wind JSON.")

  # Pull grid/domain from selected file if the selected entry reused an older file
  # and did not have conversion metadata in this run.
  selected_obj <- jsonlite::read_json(brim_rtw_files$wind_json, simplifyVector = FALSE)
  hdr <- selected_obj[[1]]$header
  selected_grid <- list(nx = hdr$nx, ny = hdr$ny, dx = hdr$dx, dy = hdr$dy)
  selected_domain <- list(west = hdr$lo1 - hdr$dx / 2, east = hdr$lo2 + hdr$dx / 2, south = hdr$la2 - hdr$dy / 2, north = hdr$la1 + hdr$dy / 2)

  build_time <- Sys.time()
  valid_time <- brim_rtw_parse_iso_utc(selected$valid_time_utc)
  cycle_time <- brim_rtw_parse_iso_utc(selected$model_cycle_utc)
  valid_age_hours <- as.numeric(difftime(build_time, valid_time, units = "hours"))

  run_summary <- list(
    product_id = "gfs_surface_wind",
    product = "NOAA GFS 0.25-degree 10-m wind",
    feed_mode = "time_set_with_legacy_latest",
    version = brim_rtw_version,
    status = "success",
    selection_strategy = "closest valid_time_utc to build time; BRIM should prefer browser-time selection from manifest entries",
    domain_id = brim_rtw_domain$id,
    domain_label = brim_rtw_domain$label,
    source = "NOAA/NCEP NOMADS",
    model_cycle_utc = selected$model_cycle_utc,
    model_cycle_local = selected$model_cycle_local,
    forecast_hour = selected$forecast_hour,
    forecast_hour_label = selected$forecast_hour_label,
    valid_time_utc = selected$valid_time_utc,
    valid_time_local = selected$valid_time_local,
    build_time_utc = brim_rtw_fmt_iso_utc(build_time),
    build_time_local = brim_rtw_fmt_local(build_time),
    local_time_zone = brim_rtw_local_tz,
    valid_lag_minutes_at_build = unname(as.numeric(difftime(build_time, valid_time, units = "mins"))),
    valid_time_age_hours = unname(valid_age_hours),
    model_cycle_age_hours = unname(as.numeric(difftime(build_time, cycle_time, units = "hours"))),
    stale_after_hours = brim_rtw_stale_after_hours,
    is_stale = isTRUE(valid_age_hours > brim_rtw_stale_after_hours),
    domain = selected_domain,
    grid = selected_grid,
    speed_ms = speed_stats$speed_ms,
    speed_mph = speed_stats$speed_mph,
    recommended_velocity_scale_ms = speed_stats$recommended_velocity_scale_ms,
    recommended_velocity_scale_mph = speed_stats$recommended_velocity_scale_mph,
    velocity_scale_reference = speed_stats$velocity_scale_reference,
    selected_entry = selected,
    available_entry_count = length(entries),
    target_past_hours = brim_rtw_target_past_hours,
    target_future_hours = brim_rtw_target_future_hours,
    max_forecast_hour = brim_rtw_max_forecast_hour,
    output_json_bytes = unname(file.info(brim_rtw_files$wind_json)$size),
    output_files = list(
      wind_json = file.path("docs/data/wind", basename(brim_rtw_files$wind_json)),
      summary_json = file.path("docs/data/wind", basename(brim_rtw_files$summary_json)),
      manifest_json = file.path("docs/data/wind", basename(brim_rtw_files$manifest_json)),
      time_set_directory = "docs/data/wind/gfs/surface"
    )
  )

  for (nm in names(speed_stats)) {
    if (grepl("^speed_(ms|mph)_", nm) || nm %in% c("recommended_velocity_scale_ms", "recommended_velocity_scale_mph", "velocity_scale_reference")) {
      run_summary[[nm]] <- speed_stats[[nm]]
    }
  }

  run_manifest <- list(
    product_id = "gfs_surface_wind",
    product = "NOAA GFS 0.25-degree 10-m wind time set",
    feed_mode = "time_set",
    version = brim_rtw_version,
    description = "NOAA GFS 0.25-degree 10-m U/V wind converted for Leaflet particle-flow rendering. BRIM should select the entry with valid_time_utc closest to browser time and fetch only that entry.",
    source = "NOAA/NCEP NOMADS",
    domain_id = brim_rtw_domain$id,
    domain_label = brim_rtw_domain$label,
    local_time_zone = brim_rtw_local_tz,
    generated_utc = brim_rtw_fmt_iso_utc(build_time),
    generated_local = brim_rtw_fmt_local(build_time),
    browser_selection = list(
      recommended = TRUE,
      strategy = "fetch this manifest first; choose entry with valid_time_utc closest to Date.now(); prefer recent past over farther future for ties; then fetch entry.relative_url relative to this manifest's directory",
      utc_selection_note = "Use UTC ISO strings for selection. Format labels in America/Los_Angeles only after selection."
    ),
    dynamic_particle_scaling = list(
      recommended = TRUE,
      note = "Use the selected manifest entry's recommended_velocity_scale_ms, or speed_ms$p95, for leaflet-velocity maxVelocity. This avoids hard-coding a wind-color scale and avoids scanning the full U/V JSON in the browser.",
      reference = sprintf(
        "p95 * %.2f, clamped to %.1f-%.1f m/s",
        brim_rtw_scale_multiplier,
        brim_rtw_scale_floor_ms,
        brim_rtw_scale_ceiling_ms
      ),
      legend_suggestion = "Particle color scaled to current domain winds. Show selected p95 and domain max for context."
    ),
    legacy_latest = list(
      note = "Legacy latest files are retained for the older one-file BRIM integration, but the preferred integration uses manifest entries.",
      wind_json = file.path("docs/data/wind", basename(brim_rtw_files$wind_json)),
      summary_json = file.path("docs/data/wind", basename(brim_rtw_files$summary_json)),
      selected_entry = selected
    ),
    stale_after_hours = brim_rtw_stale_after_hours,
    target_past_hours = brim_rtw_target_past_hours,
    target_future_hours = brim_rtw_target_future_hours,
    max_forecast_hour = brim_rtw_max_forecast_hour,
    entry_count = length(entries),
    entries = entries
  )

  jsonlite::write_json(run_summary, brim_rtw_files$summary_json, auto_unbox = TRUE, pretty = TRUE, digits = 6)
  jsonlite::write_json(run_manifest, brim_rtw_files$manifest_json, auto_unbox = TRUE, pretty = TRUE, digits = 6)

  brim_rtw_cleanup_timeset(vapply(entries, function(x) x$relative_url, character(1)))

  if (isTRUE(brim_rtw_build_sandbox)) {
    wind_json_text <- paste(readLines(brim_rtw_files$wind_json, warn = FALSE), collapse = "")
    sandbox_html <- brim_rtw_sandbox_html(wind_json_text, run_summary, run_manifest)
    brim_rtw_log("Wrote sandbox HTML: ", sandbox_html)
  }

  brim_rtw_log("Wrote legacy latest wind JSON: ", brim_rtw_files$wind_json)
  brim_rtw_log("Wrote latest summary JSON: ", brim_rtw_files$summary_json)
  brim_rtw_log("Wrote time-set manifest JSON: ", brim_rtw_files$manifest_json)
  brim_rtw_log("Available time entries: ", length(entries))
  brim_rtw_log("Selected: valid ", run_summary$valid_time_utc, " | cycle ", run_summary$model_cycle_utc,
               " | ", run_summary$forecast_hour_label,
               " | valid lag ", round(run_summary$valid_lag_minutes_at_build, 1), " minutes")
}, error = function(e) {
  run_error <<- e
  brim_rtw_log("ERROR: ", conditionMessage(e))
})

brim_rtw_log("BRIM GFS wind live-feed build finished: ", format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z"))
brim_rtw_log("Status: ", if (!is.null(run_error)) "error" else if (!is.null(run_summary$status)) run_summary$status else "success")
brim_rtw_log("Log: ", normalizePath(brim_rtw_log_file, winslash = "/", mustWork = FALSE))

if (isTRUE(brim_rtw_build_qa_zip) && !is.null(run_summary) && !is.null(run_manifest)) {
  qa_zip <- brim_rtw_package_qa(run_summary, run_manifest, brim_rtw_log_file)
  message("QA ZIP: ", normalizePath(qa_zip, winslash = "/", mustWork = FALSE))
}

if (!is.null(run_error)) stop(run_error)

message("\nMost useful outputs:")
message("  Legacy latest wind feed: ", normalizePath(brim_rtw_files$wind_json, winslash = "/", mustWork = FALSE))
message("  Latest summary:          ", normalizePath(brim_rtw_files$summary_json, winslash = "/", mustWork = FALSE))
message("  Time-set manifest:       ", normalizePath(brim_rtw_files$manifest_json, winslash = "/", mustWork = FALSE))
message("  Time-set dir:            ", normalizePath(brim_rtw_dirs$timeset, winslash = "/", mustWork = FALSE))
if (!is.null(sandbox_html)) message("  Sandbox:                 ", normalizePath(sandbox_html, winslash = "/", mustWork = FALSE))
if (!is.null(qa_zip)) message("  QA ZIP:                  ", normalizePath(qa_zip, winslash = "/", mustWork = FALSE))
