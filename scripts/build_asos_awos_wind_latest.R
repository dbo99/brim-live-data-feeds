# BRIM observed wind live-feed builder ---------------------------------------
# Builds a GeoJSON point feed of recent METAR/ASOS/AWOS wind observations from
# the NOAA/NWS Aviation Weather Center METAR cache.

brim_obs_env <- function(name, default = "") {
  value <- Sys.getenv(name, unset = default)
  if (!nzchar(value)) default else value
}

brim_obs_bool <- function(x, default = FALSE) {
  if (is.null(x) || !nzchar(x)) return(default)
  tolower(x) %in% c("1", "true", "yes", "y", "on")
}

brim_obs_null <- function(x, y) {
  if (is.null(x) || length(x) == 0L || all(is.na(x))) y else x
}

brim_obs_project_root <- normalizePath(
  brim_obs_env("BRIM_OBS_PROJECT_ROOT", getwd()),
  winslash = "/",
  mustWork = FALSE
)
if (!dir.exists(brim_obs_project_root)) {
  stop("Project root does not exist: ", brim_obs_project_root)
}
setwd(brim_obs_project_root)

brim_obs_version <- "RTW019"
brim_obs_local_tz <- brim_obs_env("BRIM_OBS_LOCAL_TZ", "America/Los_Angeles")
brim_obs_build_sandbox <- brim_obs_bool(brim_obs_env("BRIM_OBS_BUILD_SANDBOX", "true"), TRUE)
brim_obs_build_qa_zip <- brim_obs_bool(brim_obs_env("BRIM_OBS_BUILD_QA_ZIP", "true"), TRUE)
brim_obs_max_age_hours <- as.numeric(brim_obs_env("BRIM_OBS_MAX_AGE_HOURS", "3.0"))
brim_obs_stale_after_hours <- as.numeric(brim_obs_env("BRIM_OBS_STALE_AFTER_HOURS", "2.0"))
brim_obs_min_features_to_publish <- as.integer(brim_obs_env("BRIM_OBS_MIN_FEATURES_TO_PUBLISH", "25"))

# Hydrologic-California observation domain. This intentionally includes all
# California plus adjacent hydrologic/field-context areas: southern Oregon,
# western/central Nevada including Walker/Carson and Beatty/Amargosa context,
# western Arizona/lower Colorado River context, nearby Pacific/offshore stations,
# and the immediate border region. The GFS particle field remains broad; this
# observed layer is deliberately tighter so labels/barbs stay readable.
brim_obs_domain <- list(
  id = "hydrologic_ca_adjacent",
  label = "Hydrologic California + adjacent basins",
  west = -125.5,
  east = -112.0,
  south = 31.0,
  north = 43.5
)

brim_obs_source_url <- brim_obs_env(
  "BRIM_OBS_METAR_CACHE_URL",
  "https://aviationweather.gov/data/cache/metars.cache.csv.gz"
)

brim_obs_dirs <- list(
  cache = file.path(brim_obs_project_root, "data", "cache", "wind", "asos_awos"),
  logs = file.path(brim_obs_project_root, "debug", "wind", "asos_awos"),
  qa = file.path(brim_obs_project_root, "qa", "wind", "asos_awos"),
  sandbox = file.path(brim_obs_project_root, "debug", "wind", "asos_awos", "html"),
  publish = file.path(brim_obs_project_root, "docs", "data", "wind")
)
for (d in brim_obs_dirs) dir.create(d, recursive = TRUE, showWarnings = FALSE)

brim_obs_files <- list(
  geojson = file.path(brim_obs_dirs$publish, "asos_awos_wind_latest.geojson"),
  summary_json = file.path(brim_obs_dirs$publish, "asos_awos_wind_latest_summary.json"),
  manifest_json = file.path(brim_obs_dirs$publish, "asos_awos_wind_feed_manifest.json")
)

brim_obs_install_missing <- function(pkgs) {
  missing <- pkgs[!vapply(pkgs, requireNamespace, quietly = TRUE, FUN.VALUE = logical(1))]
  if (length(missing)) {
    install.packages(missing, repos = "https://cloud.r-project.org")
  }
}
brim_obs_install_missing(c("httr2", "jsonlite", "readr"))

brim_obs_log_file <- file.path(
  brim_obs_dirs$logs,
  paste0("asos_awos_wind_run_", format(Sys.time(), "%Y%m%d_%H%M%S"), ".log")
)

brim_obs_log <- function(...) {
  txt <- paste0(...)
  message(txt)
  try(cat(txt, "\n", file = brim_obs_log_file, append = TRUE, sep = ""), silent = TRUE)
}

brim_obs_fmt_iso_utc <- function(x) {
  format(as.POSIXct(x, tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
}

brim_obs_fmt_local <- function(x) {
  if (is.na(as.POSIXct(x, tz = "UTC"))) return(NA_character_)
  format(as.POSIXct(x, tz = "UTC"), "%Y-%m-%d %I:%M:%S %p %Z", tz = brim_obs_local_tz)
}

brim_obs_norm_name <- function(x) {
  tolower(gsub("[^a-z0-9]+", "", x))
}

brim_obs_pick_col <- function(df, candidates, required = TRUE) {
  nms <- names(df)
  norm <- brim_obs_norm_name(nms)
  cand_norm <- brim_obs_norm_name(candidates)
  idx <- match(cand_norm, norm, nomatch = 0)
  idx <- idx[idx > 0]
  if (length(idx)) return(nms[idx[1]])
  if (isTRUE(required)) {
    stop(
      "Could not find required column. Candidates: ", paste(candidates, collapse = ", "),
      "\nAvailable columns: ", paste(nms, collapse = ", ")
    )
  }
  NA_character_
}

brim_obs_as_num <- function(x) {
  suppressWarnings(as.numeric(as.character(x)))
}

brim_obs_parse_time <- function(x) {
  x <- as.character(x)
  x[!nzchar(x)] <- NA_character_
  out <- rep(as.POSIXct(NA_real_, origin = "1970-01-01", tz = "UTC"), length(x))

  fmts <- c(
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S"
  )

  for (fmt in fmts) {
    need <- is.na(out) & !is.na(x)
    if (!any(need)) break
    parsed <- as.POSIXct(strptime(x[need], format = fmt, tz = "UTC"), tz = "UTC")
    out[which(need)] <- parsed
  }

  # Numeric epoch fallback, accepting seconds or milliseconds.
  need <- is.na(out) & !is.na(x)
  if (any(need)) {
    num <- suppressWarnings(as.numeric(x[need]))
    ok <- !is.na(num)
    if (any(ok)) {
      # Treat very large values as milliseconds.
      num2 <- num
      num2[num2 > 1e12] <- num2[num2 > 1e12] / 1000
      tmp <- rep(as.POSIXct(NA_real_, origin = "1970-01-01", tz = "UTC"), length(num))
      tmp[ok] <- as.POSIXct(num2[ok], origin = "1970-01-01", tz = "UTC")
      out[which(need)] <- tmp
    }
  }

  out
}

brim_obs_parse_metar_wind <- function(raw_text) {
  raw_text <- as.character(raw_text)
  dir <- rep(NA_real_, length(raw_text))
  spd <- rep(NA_real_, length(raw_text))
  gst <- rep(NA_real_, length(raw_text))

  for (i in seq_along(raw_text)) {
    txt <- raw_text[i]
    if (is.na(txt) || !nzchar(txt)) next
    m <- regexec("(^|\\s)([0-9]{3}|VRB)([0-9]{2,3})(G([0-9]{2,3}))?KT", txt, perl = TRUE)
    parts <- regmatches(txt, m)[[1]]
    if (length(parts) >= 4) {
      if (!identical(parts[3], "VRB")) dir[i] <- suppressWarnings(as.numeric(parts[3]))
      spd[i] <- suppressWarnings(as.numeric(parts[4]))
      if (length(parts) >= 6 && nzchar(parts[6])) gst[i] <- suppressWarnings(as.numeric(parts[6]))
    }
  }

  data.frame(wind_dir_degrees = dir, wind_speed_kt = spd, wind_gust_kt = gst)
}

brim_obs_compass16 <- function(deg) {
  dirs <- c("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
  ifelse(
    is.na(deg),
    NA_character_,
    dirs[((round((deg %% 360) / 22.5) %% 16) + 1)]
  )
}

brim_obs_style_class <- function(speed_mph, gust_mph) {
  maxv <- pmax(speed_mph, gust_mph, na.rm = TRUE)
  maxv[is.infinite(maxv)] <- NA_real_
  ifelse(
    is.na(maxv), "no_wind",
    ifelse(maxv >= 50, "wind_50plus",
      ifelse(maxv >= 40, "wind_40plus",
        ifelse(maxv >= 30, "wind_30plus",
          ifelse(maxv >= 20, "wind_20plus",
            ifelse(maxv >= 10, "wind_10plus", "wind_light")
          )
        )
      )
    )
  )
}

brim_obs_display_label <- function(station, speed_mph, gust_mph, include_units = TRUE) {
  speed <- ifelse(is.na(speed_mph), "NA", as.character(round(speed_mph)))
  gust <- ifelse(is.na(gust_mph), "", paste0("G", round(gust_mph)))
  units <- if (isTRUE(include_units)) " mph" else ""
  paste0(station, " ", speed, gust, units)
}

brim_obs_download_cache <- function() {
  stamp <- format(Sys.time(), "%Y%m%d_%H%M%S")
  dest <- file.path(brim_obs_dirs$cache, paste0("metars_cache_", stamp, ".csv.gz"))
  brim_obs_log("Downloading METAR cache: ", brim_obs_source_url)
  req <- httr2::request(brim_obs_source_url) |>
    httr2::req_user_agent("BRIM-asos-awos-wind/0.1 (observed wind live feed)") |>
    httr2::req_timeout(120)
  resp <- httr2::req_perform(req, path = dest)
  status <- httr2::resp_status(resp)
  bytes <- file.info(dest)$size
  if (is.na(bytes)) bytes <- 0
  if (status != 200L || bytes < 10000) {
    stop("METAR cache download failed. HTTP ", status, "; bytes: ", bytes)
  }
  list(path = dest, bytes = bytes, url = brim_obs_source_url)
}

brim_obs_read_cache <- function(path) {
  brim_obs_log("Reading METAR cache CSV: ", path)
  # readr handles gzipped CSVs directly and is more forgiving about column types.
  df <- readr::read_csv(path, show_col_types = FALSE, progress = FALSE)
  df <- as.data.frame(df, stringsAsFactors = FALSE)
  if (!nrow(df)) stop("METAR cache CSV contained zero rows.")
  df
}

brim_obs_build_features <- function(df) {
  col_station <- brim_obs_pick_col(df, c("station_id", "icao_id", "id", "station", "stationId"))
  col_lat <- brim_obs_pick_col(df, c("latitude", "lat"))
  col_lon <- brim_obs_pick_col(df, c("longitude", "lon", "lng"))
  col_time <- brim_obs_pick_col(df, c("observation_time", "obs_time", "obsTime", "reportTime", "report_time", "receiptTime"))
  col_wdir <- brim_obs_pick_col(df, c("wind_dir_degrees", "wind_direction_degrees", "wind_direction", "wdir", "windDir"), required = FALSE)
  col_wspd <- brim_obs_pick_col(df, c("wind_speed_kt", "wind_speed_kts", "wind_speed", "wspd", "windSpeed"), required = FALSE)
  col_wgst <- brim_obs_pick_col(df, c("wind_gust_kt", "wind_gust_kts", "wind_gust", "wgst", "windGust", "gust"), required = FALSE)
  col_raw <- brim_obs_pick_col(df, c("raw_text", "rawOb", "raw", "raw_metar", "metar"), required = FALSE)
  col_type <- brim_obs_pick_col(df, c("metar_type", "type"), required = FALSE)
  col_cat <- brim_obs_pick_col(df, c("flight_category", "flightCategory"), required = FALSE)
  col_elev <- brim_obs_pick_col(df, c("elevation_m", "elev_m", "elevation", "elev"), required = FALSE)

  station <- as.character(df[[col_station]])
  lat <- brim_obs_as_num(df[[col_lat]])
  lon <- brim_obs_as_num(df[[col_lon]])
  obs_time <- brim_obs_parse_time(df[[col_time]])
  raw <- if (!is.na(col_raw)) as.character(df[[col_raw]]) else rep(NA_character_, nrow(df))

  parsed <- brim_obs_parse_metar_wind(raw)

  wind_dir <- if (!is.na(col_wdir)) brim_obs_as_num(df[[col_wdir]]) else parsed$wind_dir_degrees
  wind_spd <- if (!is.na(col_wspd)) brim_obs_as_num(df[[col_wspd]]) else parsed$wind_speed_kt
  wind_gst <- if (!is.na(col_wgst)) brim_obs_as_num(df[[col_wgst]]) else parsed$wind_gust_kt

  # Fill missing parsed values from raw METAR if columns are present but sparse.
  wind_dir[is.na(wind_dir)] <- parsed$wind_dir_degrees[is.na(wind_dir)]
  wind_spd[is.na(wind_spd)] <- parsed$wind_speed_kt[is.na(wind_spd)]
  wind_gst[is.na(wind_gst)] <- parsed$wind_gust_kt[is.na(wind_gst)]

  metar_type <- if (!is.na(col_type)) as.character(df[[col_type]]) else rep(NA_character_, nrow(df))
  flight_category <- if (!is.na(col_cat)) as.character(df[[col_cat]]) else rep(NA_character_, nrow(df))
  elev_m <- if (!is.na(col_elev)) brim_obs_as_num(df[[col_elev]]) else rep(NA_real_, nrow(df))

  build_time <- Sys.time()
  age_hours <- as.numeric(difftime(build_time, obs_time, units = "hours"))
  age_minutes <- age_hours * 60

  # Keep the hydrologic-California adjacent-area domain, and only current-ish observations.
  keep <- !is.na(lat) & !is.na(lon) & !is.na(obs_time) &
    lon >= brim_obs_domain$west & lon <= brim_obs_domain$east &
    lat >= brim_obs_domain$south & lat <= brim_obs_domain$north &
    age_hours >= -0.25 & age_hours <= brim_obs_max_age_hours &
    !is.na(wind_spd)

  station <- station[keep]
  lat <- lat[keep]
  lon <- lon[keep]
  obs_time <- obs_time[keep]
  age_hours <- age_hours[keep]
  age_minutes <- age_minutes[keep]
  raw <- raw[keep]
  wind_dir <- wind_dir[keep]
  wind_spd <- wind_spd[keep]
  wind_gst <- wind_gst[keep]
  metar_type <- metar_type[keep]
  flight_category <- flight_category[keep]
  elev_m <- elev_m[keep]

  speed_mph <- wind_spd * 1.150779448
  gust_mph <- wind_gst * 1.150779448
  speed_ms <- wind_spd * 0.514444
  gust_ms <- wind_gst * 0.514444
  dir_cardinal <- brim_obs_compass16(wind_dir)
  wind_from_degrees <- wind_dir
  wind_to_degrees <- ifelse(is.na(wind_dir), NA_real_, (wind_dir + 180) %% 360)
  has_gust <- !is.na(wind_gst)
  calm <- !is.na(wind_spd) & wind_spd == 0
  style_class <- brim_obs_style_class(speed_mph, gust_mph)
  gust_display <- ifelse(has_gust, paste0(round(gust_mph), " mph"), "not reported")
  display_label <- ifelse(
    has_gust,
    paste0(station, " ", round(speed_mph), " mph · gust ", round(gust_mph), " mph"),
    paste0(station, " ", round(speed_mph), " mph · gust not reported")
  )
  # Compact label intentionally keeps gust explicit. G– means no gust was reported
  # in the current METAR, not that gust = 0.
  label_short <- ifelse(
    has_gust,
    paste0(station, " ", round(speed_mph), "G", round(gust_mph)),
    paste0(station, " ", round(speed_mph), " G–")
  )

  if (length(station) < brim_obs_min_features_to_publish) {
    stop("Only ", length(station), " wind observation features after filtering; minimum is ", brim_obs_min_features_to_publish,
         ". This likely indicates an upstream/API/schema issue.")
  }

  features <- vector("list", length(station))
  for (i in seq_along(station)) {
    features[[i]] <- list(
      type = "Feature",
      geometry = list(type = "Point", coordinates = list(unname(lon[i]), unname(lat[i]))),
      properties = list(
        product_id = "asos_awos_wind_obs",
        station_id = station[i],
        observation_time_utc = brim_obs_fmt_iso_utc(obs_time[i]),
        observation_time_local = brim_obs_fmt_local(obs_time[i]),
        age_hours = unname(round(age_hours[i], 3)),
        age_minutes = unname(round(age_minutes[i])),
        age_display = paste0(unname(round(age_minutes[i])), " min"),
        wind_dir_degrees = if (is.na(wind_dir[i])) NULL else unname(wind_dir[i]),
        wind_from_degrees = if (is.na(wind_from_degrees[i])) NULL else unname(wind_from_degrees[i]),
        wind_to_degrees = if (is.na(wind_to_degrees[i])) NULL else unname(wind_to_degrees[i]),
        wind_dir_cardinal = if (is.na(dir_cardinal[i])) NULL else dir_cardinal[i],
        wind_speed_kt = unname(round(wind_spd[i], 1)),
        wind_barb_speed_kt = unname(round(wind_spd[i], 1)),
        wind_speed_mph = unname(round(speed_mph[i], 1)),
        wind_speed_ms = unname(round(speed_ms[i], 2)),
        wind_gust_kt = if (is.na(wind_gst[i])) NA_real_ else unname(round(wind_gst[i], 1)),
        wind_gust_mph = if (is.na(gust_mph[i])) NA_real_ else unname(round(gust_mph[i], 1)),
        wind_gust_ms = if (is.na(gust_ms[i])) NA_real_ else unname(round(gust_ms[i], 2)),
        wind_gust_display = gust_display[i],
        has_gust = isTRUE(has_gust[i]),
        calm = isTRUE(calm[i]),
        display_label = display_label[i],
        speed_gust_label = display_label[i],
        label_short = label_short[i],
        style_class = style_class[i],
        metar_type = if (is.na(metar_type[i])) NULL else metar_type[i],
        flight_category = if (is.na(flight_category[i])) NULL else flight_category[i],
        elevation_m = if (is.na(elev_m[i])) NULL else unname(round(elev_m[i], 1)),
        raw_text = if (is.na(raw[i])) NULL else raw[i],
        source = "NOAA/NWS Aviation Weather Center METAR cache"
      )
    )
  }

  list(
    features = features,
    n_features = length(features),
    station_count = length(unique(station)),
    max_age_hours = max(age_hours, na.rm = TRUE),
    median_age_hours = stats::median(age_hours, na.rm = TRUE),
    max_age_minutes = max(age_minutes, na.rm = TRUE),
    median_age_minutes = stats::median(age_minutes, na.rm = TRUE),
    min_obs_time = min(obs_time, na.rm = TRUE),
    max_obs_time = max(obs_time, na.rm = TRUE),
    speed_mph = speed_mph,
    gust_mph = gust_mph,
    has_gust = has_gust,
    columns_used = list(
      station = col_station,
      latitude = col_lat,
      longitude = col_lon,
      observation_time = col_time,
      wind_dir_degrees = ifelse(is.na(col_wdir), NA_character_, col_wdir),
      wind_speed_kt = ifelse(is.na(col_wspd), NA_character_, col_wspd),
      wind_gust_kt = ifelse(is.na(col_wgst), NA_character_, col_wgst),
      raw_text = ifelse(is.na(col_raw), NA_character_, col_raw),
      metar_type = ifelse(is.na(col_type), NA_character_, col_type),
      flight_category = ifelse(is.na(col_cat), NA_character_, col_cat),
      elevation_m = ifelse(is.na(col_elev), NA_character_, col_elev)
    )
  )
}

brim_obs_sandbox_html <- function(geojson_text, summary) {
  stamp <- format(Sys.time(), "%Y%m%d_%H%M%S")
  out <- file.path(brim_obs_dirs$sandbox, paste0("brim_asos_awos_wind_sandbox_", stamp, ".html"))
  meta_json <- jsonlite::toJSON(summary, auto_unbox = TRUE, digits = 6)

  html <- paste0(
'<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BRIM observed wind sandbox</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
html, body, #map { width:100%; height:100%; margin:0; background:#111; }
.info-panel { background:rgba(18,22,27,.90); color:#f4f6f8; padding:10px 12px; border-radius:5px;
  font:13px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; box-shadow:0 1px 8px rgba(0,0,0,.45); }
.info-panel strong { font-size:14px; }
.info-panel .muted { color:#b8c0c8; }
.wind-label-icon { background:transparent; border:none; }
.wind-label { display:inline-block; font: 10.5px/1.1 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:#fff; background:rgba(0,0,0,.44); padding:2px 4px; border-radius:3px; text-shadow:0 1px 2px #000; white-space:nowrap; pointer-events:none; }
</style>
</head>
<body>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const obsData = ', geojson_text, ';
const meta = ', meta_json, ';
const map = L.map("map", { preferCanvas:true, zoomControl:true }).setView([39,-121], 5);
const dark = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors &copy; CARTO | Wind obs: NOAA/NWS AWC"
}).addTo(map);
const light = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors | Wind obs: NOAA/NWS AWC"
});
function styleBy(p) {
  const v = Math.max(p.wind_speed_mph || 0, p.wind_gust_mph || 0);
  let fill = "#9ed7ff";
  if (v >= 50) fill = "#c73b66";
  else if (v >= 40) fill = "#ef5b5b";
  else if (v >= 30) fill = "#ff9a65";
  else if (v >= 20) fill = "#ffd47d";
  else if (v >= 10) fill = "#d7f0ff";
  let radius = 2.9;
  if (v >= 30) radius = 3.4;
  if (v >= 50) radius = 4.0;
  return {radius: radius, weight: 1, color: "#111", opacity: .9, fillColor: fill, fillOpacity: .74};
}
function popupHtml(p) {
  const gust = p.has_gust ? `<br>Gust: <b>${p.wind_gust_mph} mph</b>` : `<br>Gust: <span style="color:#777">not reported</span>`;
  const dir = p.wind_dir_degrees == null ? "variable/unknown" : `${p.wind_dir_degrees}° ${p.wind_dir_cardinal || ""}`;
  const age = p.age_minutes == null ? `${p.age_hours} hr` : `${p.age_minutes} min`;
  return `<b>${p.station_id}</b><br>Wind: <b>${p.wind_speed_mph} mph</b>${gust}<br>Direction: ${dir}<br>Observed: ${p.observation_time_local}<br>Age: ${age}<br><span style="color:#777">${p.raw_text || ""}</span>`;
}
const obs = L.geoJSON(obsData, {
  pointToLayer: (feature, latlng) => L.circleMarker(latlng, styleBy(feature.properties)),
  onEachFeature: (feature, layer) => {
    layer.bindPopup(popupHtml(feature.properties));
    layer.bindTooltip(feature.properties.display_label, {sticky:true, direction:"top", className:"wind-label"});
  }
}).addTo(map);
const labelMinZoom = 7;
let labelsRequested = false;
const labelLayer = L.layerGroup();
const labelMarkers = (obsData.features || []).map(f => {
  const coords = f.geometry && f.geometry.coordinates;
  const p = f.properties || {};
  if (!coords || coords.length < 2) return null;
  return L.marker([coords[1], coords[0]], {
    interactive:false,
    icon:L.divIcon({
      className:"wind-label-icon",
      html:`<span class="wind-label">${p.label_short || p.display_label || p.station_id || ""}</span>`,
      iconAnchor:[0,0]
    })
  });
}).filter(Boolean);
function refreshLabels() {
  labelLayer.clearLayers();
  if (!labelsRequested || map.getZoom() < labelMinZoom) return;
  labelMarkers.forEach(m => labelLayer.addLayer(m));
}
map.on("zoomend", refreshLabels);
map.on("overlayadd", function(e) {
  if (e.layer === labelLayer) {
    labelsRequested = true;
    refreshLabels();
  }
});
map.on("overlayremove", function(e) {
  if (e.layer === labelLayer) {
    labelsRequested = false;
    labelLayer.clearLayers();
  }
});
const info = L.control({position:"topright"});
info.onAdd = function() {
  const div = L.DomUtil.create("div", "info-panel");
  const newest = new Date(meta.newest_observation_utc).toLocaleString(undefined, {timeZone:"America/Los_Angeles", timeZoneName:"short"});
  const build = new Date(meta.build_time_utc).toLocaleString(undefined, {timeZone:"America/Los_Angeles", timeZoneName:"short"});
  const stale = meta.is_stale ? `<br><span style="color:#ffd47d">stale feed</span>` : "";
  const ageTxt = meta.newest_observation_age_minutes == null ? "" : `<br><span class="muted">Newest age: ${meta.newest_observation_age_minutes} min</span>`;
  const gustTxt = meta.gust_feature_count == null ? "" : `<br><span class="muted">Gusts reported: ${meta.gust_feature_count}/${meta.feature_count}</span>`;
  div.innerHTML = `<strong>Observed wind | METAR/ASOS speed + gusts</strong><br>${meta.domain_label}<br>${meta.feature_count} stations${gustTxt}<br>Newest obs: ${newest}${ageTxt}<br><span class="muted">Built: ${build}</span><br><span class="muted">Optional lbl layer appears at zoom ${labelMinZoom}+; G– = no gust reported</span>${stale}`;
  return div;
};
info.addTo(map);
L.control.layers(
  {"Dark":dark,"Light":light},
  {"Observed wind | METAR/ASOS speed + gusts":obs, "lbl | speed + gust labels (z≥7)":labelLayer},
  {collapsed:false}
).addTo(map);
</script>
</body>
</html>')

  writeLines(html, out, useBytes = TRUE)
  out
}

brim_obs_package_qa <- function(summary, log_file) {
  manifest <- file.path(brim_obs_dirs$qa, "RTW_ASOS_QA_manifest.txt")
  lines <- c(
    "BRIM observed wind QA manifest",
    paste0("Generated: ", format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z")),
    paste0("Version: ", brim_obs_version),
    paste0("Project root: ", brim_obs_project_root),
    paste0("Log: ", normalizePath(log_file, winslash = "/", mustWork = FALSE)),
    paste0("GeoJSON: ", normalizePath(brim_obs_files$geojson, winslash = "/", mustWork = FALSE)),
    paste0("Summary JSON: ", normalizePath(brim_obs_files$summary_json, winslash = "/", mustWork = FALSE)),
    paste0("Manifest JSON: ", normalizePath(brim_obs_files$manifest_json, winslash = "/", mustWork = FALSE)),
    "",
    paste0("Status: ", summary$status),
    paste0("Feature count: ", summary$feature_count),
    paste0("Station count: ", summary$station_count),
    paste0("Gust feature count: ", summary$gust_feature_count),
    paste0("Gust feature percent: ", summary$gust_feature_percent),
    paste0("Newest observation UTC: ", summary$newest_observation_utc),
    paste0("Newest observation age minutes: ", summary$newest_observation_age_minutes),
    paste0("Median age minutes: ", summary$observation_age_minutes$median),
    paste0("Max gust mph: ", summary$gust_mph$max_gust),
    paste0("Domain: ", summary$domain_label),
    paste0("GeoJSON bytes: ", file.info(brim_obs_files$geojson)$size),
    paste0("Summary JSON bytes: ", file.info(brim_obs_files$summary_json)$size),
    paste0("Manifest bytes: ", file.info(brim_obs_files$manifest_json)$size)
  )
  writeLines(lines, manifest, useBytes = TRUE)

  qa_zip <- file.path(brim_obs_dirs$qa, "RTW_ASOS_latest_QA_upload.zip")
  if (file.exists(qa_zip)) unlink(qa_zip)
  old_wd <- getwd()
  on.exit(setwd(old_wd), add = TRUE)
  setwd(brim_obs_project_root)
  files <- c(manifest, log_file, brim_obs_files$summary_json, brim_obs_files$manifest_json)
  files <- files[file.exists(files)]
  utils::zip(zipfile = qa_zip, files = files, flags = "-j -X")
  qa_zip
}

brim_obs_log("BRIM observed wind live-feed build started: ", format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z"))
brim_obs_log("Project root: ", brim_obs_project_root)
brim_obs_log("Version: ", brim_obs_version)
brim_obs_log("Source: ", brim_obs_source_url)
brim_obs_log("Max observation age hours: ", brim_obs_max_age_hours)

run_error <- NULL
run_summary <- NULL
sandbox_html <- NULL

tryCatch({
  download <- brim_obs_download_cache()
  df <- brim_obs_read_cache(download$path)
  built <- brim_obs_build_features(df)
  build_time <- Sys.time()

  geojson <- list(
    type = "FeatureCollection",
    name = "asos_awos_wind_latest",
    product_id = "asos_awos_wind_obs",
    generated_utc = brim_obs_fmt_iso_utc(build_time),
    source = "NOAA/NWS Aviation Weather Center METAR cache",
    bbox = list(brim_obs_domain$west, brim_obs_domain$south, brim_obs_domain$east, brim_obs_domain$north),
    features = built$features
  )

  jsonlite::write_json(
    geojson,
    brim_obs_files$geojson,
    auto_unbox = TRUE,
    pretty = FALSE,
    digits = 6,
    na = "null",
    null = "null"
  )

  max_gust_or_speed <- max(pmax(built$speed_mph, built$gust_mph, na.rm = TRUE), na.rm = TRUE)
  gust_count <- sum(built$has_gust, na.rm = TRUE)
  newest_age <- as.numeric(difftime(build_time, built$max_obs_time, units = "hours"))
  newest_age_minutes <- newest_age * 60
  gust_values <- built$gust_mph[!is.na(built$gust_mph)]

  run_summary <- list(
    product_id = "asos_awos_wind_obs",
    product = "Observed wind speed + gusts from METAR/ASOS stations",
    version = brim_obs_version,
    status = "success",
    domain_id = brim_obs_domain$id,
    domain_label = brim_obs_domain$label,
    layer_title_recommended = "Observed wind | METAR/ASOS speed + gusts",
    label_min_zoom_recommended = 7,
    barb_symbol_note = "METAR wind_dir_degrees is wind-from direction. Standard wind barbs should use wind_barb_speed_kt for sustained wind; gusts are separate label/popup values.",
    source = "NOAA/NWS Aviation Weather Center METAR cache",
    source_url = brim_obs_source_url,
    build_time_utc = brim_obs_fmt_iso_utc(build_time),
    build_time_local = brim_obs_fmt_local(build_time),
    local_time_zone = brim_obs_local_tz,
    oldest_observation_utc = brim_obs_fmt_iso_utc(built$min_obs_time),
    oldest_observation_local = brim_obs_fmt_local(built$min_obs_time),
    newest_observation_utc = brim_obs_fmt_iso_utc(built$max_obs_time),
    newest_observation_local = brim_obs_fmt_local(built$max_obs_time),
    newest_observation_age_hours = unname(round(newest_age, 3)),
    newest_observation_age_minutes = unname(round(newest_age_minutes)),
    max_age_filter_hours = brim_obs_max_age_hours,
    max_age_filter_minutes = unname(round(brim_obs_max_age_hours * 60)),
    stale_after_hours = brim_obs_stale_after_hours,
    is_stale = isTRUE(newest_age > brim_obs_stale_after_hours),
    feature_count = built$n_features,
    station_count = built$station_count,
    gust_feature_count = unname(gust_count),
    gust_feature_percent = unname(round(100 * gust_count / built$n_features, 1)),
    observation_age_hours = list(
      median = unname(round(built$median_age_hours, 3)),
      max = unname(round(built$max_age_hours, 3))
    ),
    observation_age_minutes = list(
      median = unname(round(built$median_age_minutes)),
      max = unname(round(built$max_age_minutes))
    ),
    speed_mph = list(
      min_speed = unname(round(min(built$speed_mph, na.rm = TRUE), 1)),
      median_speed = unname(round(stats::median(built$speed_mph, na.rm = TRUE), 1)),
      max_speed = unname(round(max(built$speed_mph, na.rm = TRUE), 1)),
      max_gust_or_speed = unname(round(max_gust_or_speed, 1))
    ),
    gust_mph = list(
      feature_count = unname(gust_count),
      min_gust = if (length(gust_values)) unname(round(min(gust_values, na.rm = TRUE), 1)) else NA_real_,
      median_gust = if (length(gust_values)) unname(round(stats::median(gust_values, na.rm = TRUE), 1)) else NA_real_,
      max_gust = if (length(gust_values)) unname(round(max(gust_values, na.rm = TRUE), 1)) else NA_real_
    ),
    domain = brim_obs_domain[c("west", "east", "south", "north")],
    source_cache_bytes = unname(download$bytes),
    output_geojson_bytes = unname(file.info(brim_obs_files$geojson)$size),
    columns_used = built$columns_used,
    output_files = list(
      geojson = file.path("docs/data/wind", basename(brim_obs_files$geojson)),
      summary_json = file.path("docs/data/wind", basename(brim_obs_files$summary_json)),
      manifest_json = file.path("docs/data/wind", basename(brim_obs_files$manifest_json))
    )
  )

  jsonlite::write_json(run_summary, brim_obs_files$summary_json, auto_unbox = TRUE, pretty = TRUE, digits = 6, null = "null")

  manifest <- list(
    product_id = "asos_awos_wind_obs",
    version = brim_obs_version,
    description = "Recent METAR/ASOS observed wind speed and gust points for BRIM Ops Live.",
    public_files = run_summary$output_files,
    source = run_summary$source,
    source_url = run_summary$source_url,
    build_time_utc = run_summary$build_time_utc,
    newest_observation_utc = run_summary$newest_observation_utc,
    newest_observation_age_hours = run_summary$newest_observation_age_hours,
    newest_observation_age_minutes = run_summary$newest_observation_age_minutes,
    stale_after_hours = brim_obs_stale_after_hours,
    is_stale = run_summary$is_stale,
    domain_id = run_summary$domain_id,
    domain_label = run_summary$domain_label,
    feature_count = run_summary$feature_count,
    station_count = run_summary$station_count,
    gust_feature_count = run_summary$gust_feature_count,
    gust_feature_percent = run_summary$gust_feature_percent,
    file_bytes = list(
      geojson = unname(file.info(brim_obs_files$geojson)$size),
      summary_json = unname(file.info(brim_obs_files$summary_json)$size)
    ),
    generated_utc = brim_obs_fmt_iso_utc(Sys.time())
  )
  jsonlite::write_json(manifest, brim_obs_files$manifest_json, auto_unbox = TRUE, pretty = TRUE, digits = 6, null = "null")

  if (isTRUE(brim_obs_build_sandbox)) {
    geojson_text <- paste(readLines(brim_obs_files$geojson, warn = FALSE), collapse = "")
    sandbox_html <- brim_obs_sandbox_html(geojson_text, run_summary)
    brim_obs_log("Wrote sandbox HTML: ", sandbox_html)
  }

  brim_obs_log("Wrote observed wind GeoJSON: ", brim_obs_files$geojson)
  brim_obs_log("Wrote summary JSON: ", brim_obs_files$summary_json)
  brim_obs_log("Wrote manifest JSON: ", brim_obs_files$manifest_json)
  brim_obs_log("Features: ", run_summary$feature_count, " | stations: ", run_summary$station_count,
               " | GeoJSON: ", format(run_summary$output_geojson_bytes, big.mark = ","), " bytes")
}, error = function(e) {
  run_error <<- e
  brim_obs_log("ERROR: ", conditionMessage(e))
})

brim_obs_log("BRIM observed wind live-feed build finished: ", format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z"))
brim_obs_log("Status: ", if (!is.null(run_error)) "error" else if (!is.null(run_summary$status)) run_summary$status else "success")
brim_obs_log("Log: ", normalizePath(brim_obs_log_file, winslash = "/", mustWork = FALSE))

qa_zip <- NULL
if (isTRUE(brim_obs_build_qa_zip) && !is.null(run_summary)) {
  qa_zip <- brim_obs_package_qa(run_summary, brim_obs_log_file)
  message("QA ZIP: ", normalizePath(qa_zip, winslash = "/", mustWork = FALSE))
}

if (!is.null(run_error)) stop(run_error)

message("\nMost useful outputs:")
message("  Observed wind GeoJSON: ", normalizePath(brim_obs_files$geojson, winslash = "/", mustWork = FALSE))
message("  Summary:               ", normalizePath(brim_obs_files$summary_json, winslash = "/", mustWork = FALSE))
message("  Manifest:              ", normalizePath(brim_obs_files$manifest_json, winslash = "/", mustWork = FALSE))
if (!is.null(sandbox_html)) message("  Sandbox:               ", normalizePath(sandbox_html, winslash = "/", mustWork = FALSE))
if (!is.null(qa_zip)) message("  QA ZIP:                ", normalizePath(qa_zip, winslash = "/", mustWork = FALSE))
