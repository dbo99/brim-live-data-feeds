# BRIM GFS wind live-feed builder -------------------------------------------
# Converts NOAA/NCEP GFS 0.25-degree 10-m U/V wind into a Leaflet-velocity
# compatible JSON feed under docs/data/wind/.

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

brim_rtw_version <- "RTW011"
brim_rtw_local_tz <- brim_rtw_env("BRIM_RTW_LOCAL_TZ", "America/Los_Angeles")
brim_rtw_build_sandbox <- brim_rtw_bool(brim_rtw_env("BRIM_RTW_BUILD_SANDBOX", "true"), TRUE)
brim_rtw_build_qa_zip <- brim_rtw_bool(brim_rtw_env("BRIM_RTW_BUILD_QA_ZIP", "true"), TRUE)
brim_rtw_write_archive <- brim_rtw_bool(brim_rtw_env("BRIM_RTW_WRITE_ARCHIVE", "false"), FALSE)
brim_rtw_skip_if_same_product <- brim_rtw_bool(brim_rtw_env("BRIM_RTW_SKIP_IF_SAME_PRODUCT", "true"), TRUE)
brim_rtw_force_refresh <- brim_rtw_bool(brim_rtw_env("BRIM_RTW_FORCE_REFRESH", "false"), FALSE)

# GFS is produced on 6-hour cycles; f003 from the newest complete cycle gives
# a recent near-surface wind field without chasing a partially available cycle.
brim_rtw_forecast_hour <- as.integer(brim_rtw_env("BRIM_RTW_GFS_FHOUR", "3"))
brim_rtw_max_cycle_age_hours <- as.integer(brim_rtw_env("BRIM_RTW_MAX_CYCLE_AGE_HOURS", "36"))
brim_rtw_stale_after_hours <- as.numeric(brim_rtw_env("BRIM_RTW_STALE_AFTER_HOURS", "12"))

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
  # Keep live-feed repo scratch outputs contained in existing-style folders.
  # Public feed files are written only under docs/data/wind/.
  cache_grib = file.path(brim_rtw_project_root, "data", "cache", "wind", "grib"),
  logs = file.path(brim_rtw_project_root, "debug", "wind"),
  qa = file.path(brim_rtw_project_root, "qa", "wind"),
  sandbox = file.path(brim_rtw_project_root, "debug", "wind", "html"),
  archive = file.path(brim_rtw_project_root, "data", "cache", "wind", "archive"),
  publish = file.path(brim_rtw_project_root, "docs", "data", "wind")
)
for (d in brim_rtw_dirs) dir.create(d, recursive = TRUE, showWarnings = FALSE)

brim_rtw_files <- list(
  wind_json = file.path(brim_rtw_dirs$publish, "gfs_surface_wind_latest.json"),
  summary_json = file.path(brim_rtw_dirs$publish, "gfs_surface_wind_latest_summary.json"),
  manifest_json = file.path(brim_rtw_dirs$publish, "gfs_surface_wind_feed_manifest.json")
)

brim_rtw_install_missing <- function(pkgs) {
  missing <- pkgs[!vapply(pkgs, requireNamespace, quietly = TRUE, FUN.VALUE = logical(1))]
  if (length(missing)) {
    install.packages(missing, repos = "https://cloud.r-project.org")
  }
}
brim_rtw_install_missing(c("httr2", "jsonlite", "terra"))

brim_rtw_log_file <- file.path(
  brim_rtw_dirs$logs,
  paste0("gfs_wind_run_", format(Sys.time(), "%Y%m%d_%H%M%S"), ".log")
)

# Use path-based append logging rather than a long-lived connection.  This is
# more robust when the script is sourced interactively in an existing R session
# and avoids invalid/closed connection state leaking between patch runs.
brim_rtw_log <- function(...) {
  txt <- paste0(...)
  message(txt)
  try(
    cat(txt, "\n", file = brim_rtw_log_file, append = TRUE, sep = ""),
    silent = TRUE
  )
}

brim_rtw_fmt_iso_utc <- function(x) {
  format(as.POSIXct(x, tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
}

brim_rtw_fmt_local <- function(x) {
  format(as.POSIXct(x, tz = "UTC"), "%Y-%m-%d %I:%M:%S %p %Z", tz = brim_rtw_local_tz)
}

brim_rtw_as_utc_posix <- function(x) {
  if (inherits(x, "POSIXt")) return(as.POSIXct(x, tz = "UTC"))
  as.POSIXct(as.numeric(x), origin = "1970-01-01", tz = "UTC")
}

brim_rtw_read_existing_summary <- function() {
  if (!file.exists(brim_rtw_files$summary_json)) return(NULL)
  tryCatch(
    jsonlite::read_json(brim_rtw_files$summary_json, simplifyVector = FALSE),
    error = function(e) {
      brim_rtw_log("Could not read existing wind summary JSON: ", conditionMessage(e))
      NULL
    }
  )
}

brim_rtw_same_product <- function(existing_summary, cycle_time, forecast_hour) {
  if (is.null(existing_summary)) return(FALSE)
  existing_cycle <- as.character(brim_rtw_null(existing_summary$model_cycle_utc, ""))
  existing_fhour <- suppressWarnings(as.integer(brim_rtw_null(existing_summary$forecast_hour, NA_integer_)))
  existing_domain <- as.character(brim_rtw_null(existing_summary$domain_id, ""))
  identical(existing_cycle, brim_rtw_fmt_iso_utc(cycle_time)) &&
    identical(existing_fhour, as.integer(forecast_hour)) &&
    identical(existing_domain, brim_rtw_domain$id)
}

brim_rtw_skip_summary <- function(existing_summary, cycle_time, forecast_hour, reason) {
  if (is.null(existing_summary)) existing_summary <- list()
  build_time <- Sys.time()
  valid_time <- brim_rtw_as_utc_posix(cycle_time) + as.integer(forecast_hour) * 3600
  existing_summary$status <- "skipped_same_product"
  existing_summary$skip_reason <- reason
  existing_summary$version <- brim_rtw_version
  existing_summary$build_time_utc <- brim_rtw_fmt_iso_utc(build_time)
  existing_summary$build_time_local <- brim_rtw_fmt_local(build_time)
  existing_summary$model_cycle_utc <- brim_rtw_fmt_iso_utc(cycle_time)
  existing_summary$model_cycle_local <- brim_rtw_fmt_local(cycle_time)
  existing_summary$forecast_hour <- as.integer(forecast_hour)
  existing_summary$valid_time_utc <- brim_rtw_fmt_iso_utc(valid_time)
  existing_summary$valid_time_local <- brim_rtw_fmt_local(valid_time)
  existing_summary$valid_time_age_hours <- unname(as.numeric(difftime(build_time, valid_time, units = "hours")))
  existing_summary$model_cycle_age_hours <- unname(as.numeric(difftime(build_time, cycle_time, units = "hours")))
  existing_summary$stale_after_hours <- brim_rtw_stale_after_hours
  existing_summary$is_stale <- isTRUE(existing_summary$valid_time_age_hours > brim_rtw_stale_after_hours)
  if (is.null(existing_summary$product_id)) existing_summary$product_id <- "gfs_surface_wind"
  if (is.null(existing_summary$product)) existing_summary$product <- "NOAA GFS 0.25-degree 10-m wind"
  if (is.null(existing_summary$domain_id)) existing_summary$domain_id <- brim_rtw_domain$id
  if (is.null(existing_summary$domain_label)) existing_summary$domain_label <- brim_rtw_domain$label
  if (is.null(existing_summary$output_files)) {
    existing_summary$output_files <- list(
      wind_json = file.path("docs/data/wind", basename(brim_rtw_files$wind_json)),
      summary_json = file.path("docs/data/wind", basename(brim_rtw_files$summary_json)),
      manifest_json = file.path("docs/data/wind", basename(brim_rtw_files$manifest_json))
    )
  }
  existing_summary$output_json_bytes <- unname(file.info(brim_rtw_files$wind_json)$size)
  existing_summary
}

brim_rtw_cycle_candidates <- function(now_utc = Sys.time(), max_age_hours = 36L) {
  now_utc <- as.POSIXct(now_utc, tz = "UTC")
  start_hour <- as.integer(format(now_utc, "%H", tz = "UTC"))
  cycle_hour <- max(c(0L, 6L, 12L, 18L)[c(0L, 6L, 12L, 18L) <= start_hour])
  anchor <- as.POSIXct(
    sprintf("%s %02d:00:00", format(now_utc, "%Y-%m-%d", tz = "UTC"), cycle_hour),
    tz = "UTC"
  )
  n <- ceiling(max_age_hours / 6) + 1L
  anchor - seq.int(0, by = 6 * 3600, length.out = n)
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

brim_rtw_find_and_download <- function() {
  candidates <- brim_rtw_cycle_candidates(max_age_hours = brim_rtw_max_cycle_age_hours)
  errors <- character()

  for (i in seq_along(candidates)) {
    cycle_time <- brim_rtw_as_utc_posix(candidates[i])
    url <- brim_rtw_nomads_url(cycle_time, brim_rtw_forecast_hour)
    stamp <- format(cycle_time, "%Y%m%d_%Hz", tz = "UTC")
    dest <- file.path(
      brim_rtw_dirs$cache_grib,
      sprintf("gfs_%s_%s_f%03d_10m_uv.grib2", stamp, brim_rtw_domain$id, brim_rtw_forecast_hour)
    )

    brim_rtw_log("Trying GFS cycle ", format(cycle_time, "%Y-%m-%d %H:%M UTC", tz = "UTC"), " ...")
    req <- httr2::request(url) |>
      httr2::req_user_agent("BRIM-realtimewind/0.1 (NOAA GFS live-feed pilot)") |>
      httr2::req_timeout(120)

    result <- tryCatch({
      resp <- httr2::req_perform(req, path = dest)
      status <- httr2::resp_status(resp)
      size <- file.info(dest)$size
      if (is.na(size)) size <- 0
      if (status != 200L || size < 10000) {
        stop("HTTP ", status, "; downloaded bytes: ", size)
      }
      list(cycle_time = cycle_time, url = url, path = dest, bytes = size)
    }, error = function(e) e)

    if (!inherits(result, "error")) return(result)
    errors <- c(errors, sprintf("%s: %s", stamp, conditionMessage(result)))
    if (file.exists(dest)) unlink(dest)
  }

  stop("No usable GFS cycle found. Attempts:\n", paste(errors, collapse = "\n"))
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

brim_rtw_velocity_record <- function(layer, component, cycle_time) {
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
      forecastTime = as.integer(brim_rtw_forecast_hour)
    ),
    data = brim_rtw_flatten_north_to_south(layer)
  )
}

brim_rtw_sandbox_html <- function(wind_json_text, summary) {
  stamp <- format(Sys.time(), "%Y%m%d_%H%M%S")
  out <- file.path(brim_rtw_dirs$sandbox, paste0("brim_gfs_wind_sandbox_", stamp, ".html"))
  meta_json <- jsonlite::toJSON(summary, auto_unbox = TRUE, digits = 6)

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
  font:13px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; box-shadow:0 1px 8px rgba(0,0,0,.45); }
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
const map = L.map("map", { preferCanvas:true, zoomControl:true }).setView([40,-120], 3);
const dark = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors &copy; CARTO | Wind: NOAA/NCEP GFS"
}).addTo(map);
const light = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors | Wind: NOAA/NCEP GFS"
});
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
  maxVelocity: 45,
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
  const domainLabel = meta.domain_label || "";
  const stale = meta.is_stale ? `<br><span style="color:#ffd47d">stale feed</span>` : "";
  div.innerHTML = `<strong>NOAA GFS 10-m wind</strong><br>${domainLabel}<br>Valid: ${valid}<br><span class="muted">Cycle: ${cycle} · f${String(meta.forecast_hour).padStart(3,"0")}<br>${meta.grid.nx} × ${meta.grid.ny} grid</span>${stale}`;
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

brim_rtw_package_qa <- function(summary, log_file) {
  manifest <- file.path(brim_rtw_dirs$qa, "RTW_QA_manifest.txt")
  lines <- c(
    "BRIM realtime wind QA manifest",
    paste0("Generated: ", format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z")),
    paste0("Version: ", brim_rtw_version),
    paste0("Project root: ", brim_rtw_project_root),
    paste0("Log: ", normalizePath(log_file, winslash = "/", mustWork = FALSE)),
    paste0("Wind JSON: ", normalizePath(brim_rtw_files$wind_json, winslash = "/", mustWork = FALSE)),
    paste0("Summary JSON: ", normalizePath(brim_rtw_files$summary_json, winslash = "/", mustWork = FALSE)),
    paste0("Manifest JSON: ", normalizePath(brim_rtw_files$manifest_json, winslash = "/", mustWork = FALSE)),
    "",
    paste0("Status: ", summary$status),
    paste0("Model cycle UTC: ", summary$model_cycle_utc),
    paste0("Forecast hour: f", sprintf("%03d", summary$forecast_hour)),
    paste0("Valid UTC: ", summary$valid_time_utc),
    paste0("Domain: ", summary$domain_label),
    paste0("Grid: ", summary$grid$nx, " x ", summary$grid$ny, " @ ", summary$grid$dx, " deg"),
    paste0("Wind JSON bytes: ", file.info(brim_rtw_files$wind_json)$size),
    paste0("Summary JSON bytes: ", file.info(brim_rtw_files$summary_json)$size),
    paste0("Manifest bytes: ", file.info(brim_rtw_files$manifest_json)$size)
  )
  writeLines(lines, manifest, useBytes = TRUE)

  qa_zip <- file.path(brim_rtw_dirs$qa, "RTW_latest_QA_upload.zip")
  if (file.exists(qa_zip)) unlink(qa_zip)
  old_wd <- getwd()
  on.exit(setwd(old_wd), add = TRUE)
  setwd(brim_rtw_project_root)
  files <- c(manifest, log_file, brim_rtw_files$summary_json, brim_rtw_files$manifest_json)
  files <- files[file.exists(files)]
  utils::zip(zipfile = qa_zip, files = files, flags = "-j -X")
  qa_zip
}

brim_rtw_log("BRIM GFS wind live-feed build started: ", format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z"))
brim_rtw_log("Project root: ", brim_rtw_project_root)
brim_rtw_log("Version: ", brim_rtw_version)
brim_rtw_log("Build sandbox HTML: ", brim_rtw_build_sandbox)
brim_rtw_log("Skip same product: ", brim_rtw_skip_if_same_product, " | force refresh: ", brim_rtw_force_refresh)

run_error <- NULL
run_summary <- NULL
sandbox_html <- NULL

tryCatch({
  existing_summary <- brim_rtw_read_existing_summary()
  expected_cycle <- brim_rtw_as_utc_posix(brim_rtw_cycle_candidates(max_age_hours = brim_rtw_max_cycle_age_hours)[1])

  if (brim_rtw_skip_if_same_product && !brim_rtw_force_refresh &&
      brim_rtw_same_product(existing_summary, expected_cycle, brim_rtw_forecast_hour)) {
    run_summary <- brim_rtw_skip_summary(
      existing_summary,
      expected_cycle,
      brim_rtw_forecast_hour,
      "existing published product already matches the newest expected GFS cycle"
    )
    brim_rtw_log(
      "Existing GFS wind product already matches newest expected cycle ",
      run_summary$model_cycle_utc, " f", sprintf("%03d", run_summary$forecast_hour),
      "; skipping NOAA download and publish writes."
    )
  } else {
    download <- brim_rtw_find_and_download()
    cycle_time <- download$cycle_time
    valid_time <- cycle_time + brim_rtw_forecast_hour * 3600

    if (brim_rtw_skip_if_same_product && !brim_rtw_force_refresh &&
        brim_rtw_same_product(existing_summary, cycle_time, brim_rtw_forecast_hour)) {
      run_summary <- brim_rtw_skip_summary(
        existing_summary,
        cycle_time,
        brim_rtw_forecast_hour,
        "existing published product already matches downloaded GFS cycle"
      )
      brim_rtw_log(
        "Existing GFS wind product already matches downloaded cycle ",
        run_summary$model_cycle_utc, " f", sprintf("%03d", run_summary$forecast_hour),
        "; skipping publish writes."
      )
    } else {
      brim_rtw_log("Reading cropped GRIB2: ", download$path)
  r <- tryCatch(
    terra::rast(download$path),
    error = function(e) stop("terra/GDAL could not read the downloaded GRIB2 file. Original error: ", conditionMessage(e))
  )

  idx <- brim_rtw_identify_uv_layers(r)
  u <- brim_rtw_normalize_longitude(r[[idx[["u"]]]])
  v <- brim_rtw_normalize_longitude(r[[idx[["v"]]]])
  if (!terra::compareGeom(u, v, stopOnError = FALSE)) {
    stop("U and V grids do not have matching geometry.")
  }

      velocity <- list(
    brim_rtw_velocity_record(u, "u", cycle_time),
    brim_rtw_velocity_record(v, "v", cycle_time)
  )

  jsonlite::write_json(
    velocity,
    brim_rtw_files$wind_json,
    auto_unbox = TRUE,
    digits = 5,
    pretty = FALSE,
    na = "null"
  )

  uvals <- terra::values(u, mat = FALSE)
  vvals <- terra::values(v, mat = FALSE)
  speed <- sqrt(uvals^2 + vvals^2)
  build_time <- Sys.time()
  valid_time_age_hours <- as.numeric(difftime(build_time, valid_time, units = "hours"))
  cycle_age_hours <- as.numeric(difftime(build_time, cycle_time, units = "hours"))

  run_summary <- list(
    product_id = "gfs_surface_wind",
    product = "NOAA GFS 0.25-degree 10-m wind",
    version = brim_rtw_version,
    status = "success",
    domain_id = brim_rtw_domain$id,
    domain_label = brim_rtw_domain$label,
    source = "NOAA/NCEP NOMADS",
    model_cycle_utc = brim_rtw_fmt_iso_utc(cycle_time),
    model_cycle_local = brim_rtw_fmt_local(cycle_time),
    forecast_hour = brim_rtw_forecast_hour,
    valid_time_utc = brim_rtw_fmt_iso_utc(valid_time),
    valid_time_local = brim_rtw_fmt_local(valid_time),
    build_time_utc = brim_rtw_fmt_iso_utc(build_time),
    build_time_local = brim_rtw_fmt_local(build_time),
    local_time_zone = brim_rtw_local_tz,
    valid_time_age_hours = unname(valid_time_age_hours),
    model_cycle_age_hours = unname(cycle_age_hours),
    stale_after_hours = brim_rtw_stale_after_hours,
    is_stale = isTRUE(valid_time_age_hours > brim_rtw_stale_after_hours),
    domain = list(west = terra::xmin(u), east = terra::xmax(u), south = terra::ymin(u), north = terra::ymax(u)),
    grid = list(nx = terra::ncol(u), ny = terra::nrow(u), dx = terra::res(u)[1], dy = terra::res(u)[2]),
    speed_ms = list(
      min = unname(min(speed, na.rm = TRUE)),
      median = unname(stats::median(speed, na.rm = TRUE)),
      max = unname(max(speed, na.rm = TRUE))
    ),
    source_request_url = download$url,
    source_grib_bytes = unname(download$bytes),
    output_json_bytes = unname(file.info(brim_rtw_files$wind_json)$size),
    output_files = list(
      wind_json = file.path("docs/data/wind", basename(brim_rtw_files$wind_json)),
      summary_json = file.path("docs/data/wind", basename(brim_rtw_files$summary_json)),
      manifest_json = file.path("docs/data/wind", basename(brim_rtw_files$manifest_json))
    )
  )

  jsonlite::write_json(run_summary, brim_rtw_files$summary_json, auto_unbox = TRUE, pretty = TRUE, digits = 6)

  manifest <- list(
    product_id = "gfs_surface_wind",
    version = brim_rtw_version,
    description = "NOAA GFS 0.25-degree 10-m U/V wind converted for Leaflet particle-flow rendering.",
    public_files = run_summary$output_files,
    source = run_summary$source,
    model_cycle_utc = run_summary$model_cycle_utc,
    forecast_hour = run_summary$forecast_hour,
    valid_time_utc = run_summary$valid_time_utc,
    build_time_utc = run_summary$build_time_utc,
    stale_after_hours = brim_rtw_stale_after_hours,
    valid_time_age_hours = run_summary$valid_time_age_hours,
    is_stale = run_summary$is_stale,
    domain_id = run_summary$domain_id,
    domain_label = run_summary$domain_label,
    grid = run_summary$grid,
    file_bytes = list(
      wind_json = unname(file.info(brim_rtw_files$wind_json)$size),
      summary_json = unname(file.info(brim_rtw_files$summary_json)$size)
    ),
    generated_utc = brim_rtw_fmt_iso_utc(Sys.time())
  )
  jsonlite::write_json(manifest, brim_rtw_files$manifest_json, auto_unbox = TRUE, pretty = TRUE, digits = 6)

  if (isTRUE(brim_rtw_write_archive)) {
    stamp <- gsub("[^0-9]", "", run_summary$valid_time_utc)
    stamp <- substr(stamp, 1, 10)
    file.copy(brim_rtw_files$wind_json, file.path(brim_rtw_dirs$archive, paste0("gfs_surface_wind_", stamp, ".json")), overwrite = TRUE)
    file.copy(brim_rtw_files$summary_json, file.path(brim_rtw_dirs$archive, paste0("gfs_surface_wind_", stamp, "_summary.json")), overwrite = TRUE)
  }

  if (isTRUE(brim_rtw_build_sandbox)) {
    wind_json_text <- paste(readLines(brim_rtw_files$wind_json, warn = FALSE), collapse = "")
    sandbox_html <- brim_rtw_sandbox_html(wind_json_text, run_summary)
    brim_rtw_log("Wrote sandbox HTML: ", sandbox_html)
  }

  brim_rtw_log("Wrote wind JSON: ", brim_rtw_files$wind_json)
  brim_rtw_log("Wrote summary JSON: ", brim_rtw_files$summary_json)
  brim_rtw_log("Wrote manifest JSON: ", brim_rtw_files$manifest_json)
  brim_rtw_log("Grid: ", run_summary$grid$nx, " x ", run_summary$grid$ny,
               " | wind JSON: ", format(run_summary$output_json_bytes, big.mark = ","), " bytes")
    }
  }
}, error = function(e) {
  run_error <<- e
  brim_rtw_log("ERROR: ", conditionMessage(e))
})

brim_rtw_log("BRIM GFS wind live-feed build finished: ", format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z"))
brim_rtw_log("Status: ", if (!is.null(run_error)) "error" else if (!is.null(run_summary$status)) run_summary$status else "success")
brim_rtw_log("Log: ", normalizePath(brim_rtw_log_file, winslash = "/", mustWork = FALSE))

qa_zip <- NULL
if (isTRUE(brim_rtw_build_qa_zip) && !is.null(run_summary)) {
  qa_zip <- brim_rtw_package_qa(run_summary, brim_rtw_log_file)
  message("QA ZIP: ", normalizePath(qa_zip, winslash = "/", mustWork = FALSE))
}

if (!is.null(run_error)) stop(run_error)

message("\nMost useful outputs:")
message("  Wind feed: ", normalizePath(brim_rtw_files$wind_json, winslash = "/", mustWork = FALSE))
message("  Summary:   ", normalizePath(brim_rtw_files$summary_json, winslash = "/", mustWork = FALSE))
message("  Manifest:  ", normalizePath(brim_rtw_files$manifest_json, winslash = "/", mustWork = FALSE))
if (!is.null(sandbox_html)) message("  Sandbox:   ", normalizePath(sandbox_html, winslash = "/", mustWork = FALSE))
if (!is.null(qa_zip)) message("  QA ZIP:    ", normalizePath(qa_zip, winslash = "/", mustWork = FALSE))
