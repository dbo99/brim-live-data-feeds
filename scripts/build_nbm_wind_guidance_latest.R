# BRIM NBM wind-guidance production feed builder -------------------------------
#
# PURPOSE
#   Publish compact NOAA/NWS National Blend of Models (NBM) wind guidance for
#   Hydrologic California + adjacent basins at +6, +12, +24, and +48 hours.
#
# PRODUCT DESIGN
#   - Central direction: NBM core 10-m WDIR.
#   - Sustained wind uncertainty: NBM QMD 10th/50th/90th percentiles.
#   - Gust uncertainty: NBM QMD 10th/50th/90th percentiles.
#   - Browser output: sparse regular lon/lat GeoJSON point grid.
#
# IMPORTANT
#   NBM QMD percentile files are produced for 00/06/12/18 UTC cycles. This
#   builder searches recent QMD cycles, selects the newest cycle with all four
#   target forecast hours available, and uses matching core direction fields.
#
# REQUIREMENT
#   wgrib2 must be on PATH. The companion GitHub workflow installs it through
#   micromamba/conda-forge.

nbm_env <- function(name, default = "") {
  value <- Sys.getenv(name, unset = default)
  if (!nzchar(value)) default else value
}

`%||%` <- function(x, y) {
  if (is.null(x) || length(x) == 0L || all(is.na(x))) y else x
}

nbm_parse_integer_csv <- function(x, default) {
  x <- trimws(as.character(x %||% ""))
  if (!nzchar(x)) return(as.integer(default))
  values <- suppressWarnings(as.integer(trimws(strsplit(x, ",", fixed = TRUE)[[1]])))
  values <- values[is.finite(values) & values >= 0L]
  if (!length(values)) as.integer(default) else sort(unique(values))
}

nbm_as_utc <- function(x) {
  if (inherits(x, "POSIXt")) {
    return(as.POSIXct(as.numeric(x), origin = "1970-01-01", tz = "UTC"))
  }
  if (is.numeric(x)) return(as.POSIXct(x, origin = "1970-01-01", tz = "UTC"))
  as.POSIXct(x, tz = "UTC")
}

nbm_floor_hour <- function(x) {
  x <- nbm_as_utc(x)
  as.POSIXct(format(x, "%Y-%m-%d %H:00:00", tz = "UTC"), tz = "UTC")
}

nbm_floor_six_hour_cycle <- function(x) {
  x <- nbm_floor_hour(x)
  hour <- as.integer(format(x, "%H", tz = "UTC"))
  x - (hour %% 6L) * 3600
}

nbm_fmt_iso_utc <- function(x) {
  format(nbm_as_utc(x), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
}

nbm_fmt_local <- function(x, tz = "America/Los_Angeles") {
  format(nbm_as_utc(x), "%Y-%m-%d %I:%M:%S %p %Z", tz = tz)
}

nbm_project_root <- normalizePath(
  nbm_env("BRIM_NBM_PROJECT_ROOT", getwd()),
  winslash = "/",
  mustWork = FALSE
)
if (!dir.exists(nbm_project_root)) stop("Project root does not exist: ", nbm_project_root)
setwd(nbm_project_root)

nbm_version <- "RTW-NBM002"
nbm_local_tz <- nbm_env("BRIM_NBM_LOCAL_TZ", "America/Los_Angeles")
nbm_target_lead_hours <- nbm_parse_integer_csv(
  nbm_env("BRIM_NBM_TARGET_LEAD_HOURS", "6,12,24,48"),
  c(6L, 12L, 24L, 48L)
)

# The QMD feed updates four times daily. Publish a second support field six
# hours beyond each user-facing target so the browser can still select a field
# within about three hours of now + target lead throughout the update window.
# This keeps the visible controls honest without running another workflow.
nbm_support_window_hours <- as.integer(nbm_env("BRIM_NBM_SUPPORT_WINDOW_HOURS", "6"))
if (!is.finite(nbm_support_window_hours) || nbm_support_window_hours < 0L) {
  nbm_support_window_hours <- 6L
}
nbm_publish_lead_hours <- sort(unique(c(
  nbm_target_lead_hours,
  nbm_target_lead_hours + nbm_support_window_hours
)))

nbm_cycle_lookback_hours <- as.integer(nbm_env("BRIM_NBM_CYCLE_LOOKBACK_HOURS", "30"))
nbm_request_timeout_seconds <- as.numeric(nbm_env("BRIM_NBM_REQUEST_TIMEOUT_SECONDS", "120"))
nbm_retain_hours <- as.numeric(nbm_env("BRIM_NBM_RETAIN_HOURS", "18"))

nbm_domain <- list(
  id = "hydrologic_ca_adjacent",
  label = "Hydrologic California + adjacent basins",
  west = as.numeric(nbm_env("BRIM_NBM_WEST", "-125.5")),
  east = as.numeric(nbm_env("BRIM_NBM_EAST", "-112.0")),
  south = as.numeric(nbm_env("BRIM_NBM_SOUTH", "31.0")),
  north = as.numeric(nbm_env("BRIM_NBM_NORTH", "43.5")),
  resolution_degrees = as.numeric(nbm_env("BRIM_NBM_RESOLUTION_DEGREES", "0.25"))
)

nbm_domain$nx <- as.integer(round((nbm_domain$east - nbm_domain$west) / nbm_domain$resolution_degrees)) + 1L
nbm_domain$ny <- as.integer(round((nbm_domain$north - nbm_domain$south) / nbm_domain$resolution_degrees)) + 1L
if (nbm_domain$nx < 2L || nbm_domain$ny < 2L) stop("Invalid NBM output grid dimensions.")

nbm_dirs <- list(
  cache = file.path(nbm_project_root, "data", "cache", "wind", "nbm"),
  debug = file.path(nbm_project_root, "debug", "wind", "nbm"),
  qa = file.path(nbm_project_root, "qa", "wind", "nbm"),
  publish = file.path(nbm_project_root, "docs", "data", "wind"),
  timeset = file.path(nbm_project_root, "docs", "data", "wind", "nbm", "guidance")
)
for (d in nbm_dirs) dir.create(d, recursive = TRUE, showWarnings = FALSE)

nbm_files <- list(
  manifest = file.path(nbm_dirs$publish, "nbm_wind_guidance_feed_manifest.json"),
  summary = file.path(nbm_dirs$publish, "nbm_wind_guidance_latest_summary.json")
)

nbm_install_missing <- function(pkgs) {
  missing <- pkgs[!vapply(pkgs, requireNamespace, quietly = TRUE, FUN.VALUE = logical(1))]
  if (length(missing)) install.packages(missing, repos = "https://cloud.r-project.org")
}
nbm_install_missing(c("httr2", "jsonlite"))

nbm_wgrib2 <- Sys.which("wgrib2")
if (!nzchar(nbm_wgrib2)) stop("wgrib2 was not found on PATH.")

nbm_stamp <- format(Sys.time(), "%Y%m%d_%H%M%S")
nbm_log_file <- file.path(nbm_dirs$debug, paste0("nbm_wind_run_", nbm_stamp, ".log"))

nbm_log <- function(...) {
  text <- paste0(...)
  message(text)
  try(cat(text, "\n", file = nbm_log_file, append = TRUE), silent = TRUE)
}

nbm_run <- function(command, args, label = basename(command)) {
  output <- system2(command, args = args, stdout = TRUE, stderr = TRUE)
  status <- attr(output, "status") %||% 0L
  if (!identical(as.integer(status), 0L)) {
    stop(label, " failed with status ", status, ":\n", paste(output, collapse = "\n"))
  }
  output
}

nbm_log("Starting ", nbm_version)
nbm_log("Project root: ", nbm_project_root)
nbm_log("wgrib2: ", nbm_wgrib2)
version_text <- suppressWarnings(system2(nbm_wgrib2, "-version", stdout = TRUE, stderr = TRUE))
if (length(version_text)) nbm_log("wgrib2 version probe: ", paste(version_text, collapse = " | "))

nbm_http_text <- function(url, quiet = FALSE) {
  req <- httr2::request(url) |>
    httr2::req_user_agent("BRIM-NBM-wind-guidance/1.0") |>
    httr2::req_timeout(nbm_request_timeout_seconds) |>
    httr2::req_retry(max_tries = 3)
  resp <- tryCatch(httr2::req_perform(req), error = function(e) e)
  if (inherits(resp, "error")) {
    if (quiet) return(NULL)
    stop("HTTP request failed for ", url, ": ", conditionMessage(resp))
  }
  if (httr2::resp_status(resp) >= 400L) {
    if (quiet) return(NULL)
    stop("HTTP ", httr2::resp_status(resp), " for ", url)
  }
  httr2::resp_body_string(resp)
}

nbm_http_range <- function(url, start_byte, end_byte, path) {
  req <- httr2::request(url) |>
    httr2::req_user_agent("BRIM-NBM-wind-guidance/1.0") |>
    httr2::req_headers(Range = sprintf("bytes=%d-%d", start_byte, end_byte)) |>
    httr2::req_timeout(nbm_request_timeout_seconds) |>
    httr2::req_retry(max_tries = 3)
  resp <- httr2::req_perform(req)
  status <- httr2::resp_status(resp)
  if (!status %in% c(200L, 206L)) stop("Unexpected HTTP status ", status, " for byte-range request.")
  raw <- httr2::resp_body_raw(resp)
  expected <- as.numeric(end_byte - start_byte + 1)
  if (length(raw) != expected) {
    stop("Byte-range response length mismatch: expected ", expected, ", received ", length(raw), ".")
  }
  writeBin(raw, path)
  invisible(path)
}

nbm_aws_urls <- function(cycle_time, forecast_hour, product = c("core", "qmd")) {
  product <- match.arg(product)
  cycle_time <- nbm_as_utc(cycle_time)
  ymd <- format(cycle_time, "%Y%m%d", tz = "UTC")
  hh <- format(cycle_time, "%H", tz = "UTC")
  ff <- sprintf("%03d", as.integer(forecast_hour))
  key <- sprintf(
    "blend.%s/%s/%s/blend.t%sz.%s.f%s.co.grib2",
    ymd, hh, product, hh, product, ff
  )
  base <- paste0("https://noaa-nbm-grib2-pds.s3.amazonaws.com/", key)
  list(grib = base, idx = paste0(base, ".idx"), key = key)
}

nbm_parse_idx <- function(text) {
  lines <- strsplit(text, "\n", fixed = TRUE)[[1]]
  lines <- trimws(lines)
  lines <- lines[nzchar(lines)]
  starts <- suppressWarnings(as.numeric(sub("^[0-9]+:([0-9]+):.*$", "\\1", lines)))
  desc <- sub("^[0-9]+:[0-9]+:", "", lines)
  if (any(!is.finite(starts))) stop("Could not parse one or more NBM index byte offsets.")
  data.frame(
    line = lines,
    start = starts,
    end = c(starts[-1] - 1, NA_real_),
    desc = desc,
    stringsAsFactors = FALSE
  )
}

nbm_find_record <- function(idx, pattern, label) {
  hit <- which(grepl(pattern, idx$desc, perl = TRUE))
  if (length(hit) != 1L) {
    stop("Expected exactly one ", label, " record; found ", length(hit), ".")
  }
  rec <- idx[hit, , drop = FALSE]
  if (!is.finite(rec$end)) stop("Selected ", label, " record was the final index record; end byte unavailable.")
  rec
}

nbm_available_forecast_hours <- c(1:48, seq(51, 192, 3), seq(198, 264, 6))

nbm_snap_forecast_hour <- function(cycle_time, target_time) {
  desired <- as.numeric(difftime(target_time, cycle_time, units = "hours"))
  if (!is.finite(desired)) stop("Invalid NBM target time.")
  nbm_available_forecast_hours[which.min(abs(nbm_available_forecast_hours - desired))]
}

nbm_candidate_cycles <- function(now_utc) {
  newest <- nbm_floor_six_hour_cycle(now_utc)
  count <- floor(nbm_cycle_lookback_hours / 6)
  newest - seq.int(0L, count) * 6 * 3600
}

nbm_cycle_plan <- function(cycle_time, now_utc) {
  rows <- lapply(nbm_publish_lead_hours, function(lead) {
    target <- nbm_as_utc(now_utc) + lead * 3600
    fhr <- nbm_snap_forecast_hour(cycle_time, target)
    data.frame(
      lead_hours = as.integer(lead),
      target_time = target,
      forecast_hour = as.integer(fhr),
      valid_time = nbm_as_utc(cycle_time) + fhr * 3600,
      stringsAsFactors = FALSE
    )
  })
  do.call(rbind, rows)
}

nbm_choose_cycle <- function(now_utc) {
  for (cycle in nbm_candidate_cycles(now_utc)) {
    cycle <- nbm_as_utc(cycle)
    plan <- nbm_cycle_plan(cycle, now_utc)
    ok <- TRUE
    for (fhr in unique(plan$forecast_hour)) {
      core <- nbm_aws_urls(cycle, fhr, "core")
      qmd <- nbm_aws_urls(cycle, fhr, "qmd")
      if (is.null(nbm_http_text(core$idx, quiet = TRUE)) || is.null(nbm_http_text(qmd$idx, quiet = TRUE))) {
        ok <- FALSE
        break
      }
    }
    if (ok) return(list(cycle = cycle, plan = plan))
    nbm_log("NBM QMD/core set incomplete for cycle ", nbm_fmt_iso_utc(cycle), "; trying older cycle.")
  }
  stop("Could not find a recent NBM QMD cycle with all requested forecast hours available.")
}

nbm_extract_message <- function(urls, idx, pattern, label, output_path) {
  rec <- nbm_find_record(idx, pattern, label)
  nbm_http_range(urls$grib, rec$start, rec$end, output_path)
  output_path
}

nbm_prepare_regrid_input <- function(input_grib, output_stub) {
  grid_text <- nbm_run(
    nbm_wgrib2,
    c(input_grib, "-grid"),
    "wgrib2 NBM input-grid inspection"
  )

  grid_log <- paste0(output_stub, "_input_grid.txt")
  writeLines(grid_text, grid_log, useBytes = TRUE)

  # NBM CONUS grids use the NDFD/Glahn alternating-row scan convention
  # (WE|EW:SN; GRIB scan mode 80). IPOLATES/new_grid cannot consume that
  # orientation directly. Convert each extracted one-message GRIB to ordinary
  # WE:SN before interpolation, using NOAA's documented workaround.
  alternating_scan <- any(grepl(
    "WE\\|EW:SN|scan mode 80|scan=80",
    grid_text,
    ignore.case = TRUE,
    perl = TRUE
  ))

  if (!alternating_scan) return(input_grib)

  normalized_grib <- paste0(output_stub, "_wesn.grib2")
  if (file.exists(normalized_grib)) unlink(normalized_grib)

  nbm_log("Normalizing NBM alternating-row scan order to WE:SN.")
  nbm_run(
    nbm_wgrib2,
    c(
      input_grib,
      "-rpn", "alt_x_scan",
      "-set", "table_3.4", "64",
      "-set_grib_type", "same",
      "-grib_out", normalized_grib
    ),
    "wgrib2 NBM scan-order normalization"
  )

  if (!file.exists(normalized_grib) || file.info(normalized_grib)$size < 100) {
    stop("NBM scan-order normalization did not create a usable GRIB.")
  }

  normalized_grid <- nbm_run(
    nbm_wgrib2,
    c(normalized_grib, "-grid"),
    "wgrib2 normalized-grid inspection"
  )
  writeLines(
    normalized_grid,
    paste0(output_stub, "_normalized_grid.txt"),
    useBytes = TRUE
  )

  if (any(grepl("WE\\|EW:SN|scan mode 80|scan=80", normalized_grid, ignore.case = TRUE, perl = TRUE))) {
    stop("NBM scan-order normalization still reports alternating-row scan order.")
  }

  normalized_grib
}

nbm_regrid_to_csv <- function(input_grib, output_stub, interpolation = c("bilinear", "neighbor")) {
  interpolation <- match.arg(interpolation)
  regrid_input <- nbm_prepare_regrid_input(input_grib, output_stub)

  out_grib <- paste0(output_stub, "_grid.grib2")
  out_csv <- paste0(output_stub, ".csv")
  lon_spec <- sprintf("%.8f:%d:%.8f", nbm_domain$west, nbm_domain$nx, nbm_domain$resolution_degrees)
  lat_spec <- sprintf("%.8f:%d:%.8f", nbm_domain$south, nbm_domain$ny, nbm_domain$resolution_degrees)

  if (file.exists(out_grib)) unlink(out_grib)
  if (file.exists(out_csv)) unlink(out_csv)

  nbm_run(
    nbm_wgrib2,
    c(
      regrid_input,
      "-set_grib_type", "same",
      "-new_grid_interpolation", interpolation,
      "-new_grid", "latlon", lon_spec, lat_spec, out_grib
    ),
    paste0("wgrib2 ", interpolation, " regrid")
  )

  if (!file.exists(out_grib) || file.info(out_grib)$size < 100) {
    stop("NBM regridded GRIB was missing or too small.")
  }

  output_inventory <- nbm_run(
    nbm_wgrib2,
    c(out_grib, "-s"),
    "wgrib2 regridded inventory"
  )
  writeLines(
    output_inventory,
    paste0(output_stub, "_regridded_inventory.txt"),
    useBytes = TRUE
  )

  nbm_run(nbm_wgrib2, c(out_grib, "-csv", out_csv), "wgrib2 CSV export")
  if (!file.exists(out_csv) || file.info(out_csv)$size < 1000) {
    stop("NBM regridded CSV was missing or too small.")
  }
  out_csv
}

nbm_read_field_csv <- function(path, value_name) {
  x <- utils::read.csv(
    path,
    header = FALSE,
    stringsAsFactors = FALSE,
    check.names = FALSE
  )

  # Current wgrib2 CSV output has reference time and valid time as separate
  # columns. Older builds may emit the legacy six-column form.
  if (ncol(x) == 7L) {
    names(x) <- c("reference_time", "valid", "variable", "level", "lon", "lat", "value")
  } else if (ncol(x) == 6L) {
    names(x) <- c("valid", "variable", "level", "lon", "lat", "value")
  } else {
    stop(
      "Unexpected wgrib2 CSV column count for ", value_name,
      ": ", ncol(x), "."
    )
  }

  x$lon <- suppressWarnings(as.numeric(x$lon))
  x$lat <- suppressWarnings(as.numeric(x$lat))
  x$value <- suppressWarnings(as.numeric(x$value))
  x$lon[x$lon > 180] <- x$lon[x$lon > 180] - 360
  x <- x[
    is.finite(x$lon) & is.finite(x$lat) & is.finite(x$value),
    c("lon", "lat", "value")
  ]

  expected <- nbm_domain$nx * nbm_domain$ny
  if (nrow(x) < expected * 0.95) {
    stop(
      "NBM field ", value_name, " retained only ", nrow(x),
      " finite points of ", expected, " expected."
    )
  }

  x$key <- sprintf("%.5f|%.5f", x$lon, x$lat)
  if (anyDuplicated(x$key)) x <- x[!duplicated(x$key), ]
  names(x)[names(x) == "value"] <- value_name
  x[, c("key", "lon", "lat", value_name)]
}

nbm_percentile_stats <- function(x) {
  x <- x[is.finite(x)]
  if (!length(x)) return(list())
  p <- stats::quantile(x, c(0, .5, .75, .9, .95, 1), names = FALSE, na.rm = TRUE)
  names(p) <- c("min", "p50", "p75", "p90", "p95", "max")
  as.list(p)
}

nbm_cardinal <- function(degrees) {
  dirs <- c("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
  degrees <- ((degrees %% 360) + 360) %% 360
  dirs[(floor((degrees + 11.25) / 22.5) %% 16) + 1]
}

nbm_build_entry <- function(cycle_time, row) {
  fhr <- as.integer(row$forecast_hour)
  ff <- sprintf("%03d", fhr)
  cycle_tag <- format(cycle_time, "%Y%m%dT%HZ", tz = "UTC")
  work_dir <- file.path(nbm_dirs$cache, paste0(cycle_tag, "_f", ff))
  dir.create(work_dir, recursive = TRUE, showWarnings = FALSE)

  core_urls <- nbm_aws_urls(cycle_time, fhr, "core")
  qmd_urls <- nbm_aws_urls(cycle_time, fhr, "qmd")
  core_idx <- nbm_parse_idx(nbm_http_text(core_urls$idx))
  qmd_idx <- nbm_parse_idx(nbm_http_text(qmd_urls$idx))

  specs <- list(
    wind_dir_deg = list(product = "core", pattern = ":WDIR:10 m above ground:.*fcst:$", interpolation = "neighbor"),
    wind_p10_ms = list(product = "qmd", pattern = ":WIND:10 m above ground:.*:10% level$", interpolation = "bilinear"),
    wind_p50_ms = list(product = "qmd", pattern = ":WIND:10 m above ground:.*:50% level$", interpolation = "bilinear"),
    wind_p90_ms = list(product = "qmd", pattern = ":WIND:10 m above ground:.*:90% level$", interpolation = "bilinear"),
    gust_p10_ms = list(product = "qmd", pattern = ":GUST:10 m above ground:.*:10% level$", interpolation = "bilinear"),
    gust_p50_ms = list(product = "qmd", pattern = ":GUST:10 m above ground:.*:50% level$", interpolation = "bilinear"),
    gust_p90_ms = list(product = "qmd", pattern = ":GUST:10 m above ground:.*:90% level$", interpolation = "bilinear")
  )

  fields <- list()
  for (name in names(specs)) {
    spec <- specs[[name]]
    urls <- if (spec$product == "core") core_urls else qmd_urls
    idx <- if (spec$product == "core") core_idx else qmd_idx
    raw_path <- file.path(work_dir, paste0(name, ".grib2"))
    stub <- file.path(work_dir, name)
    nbm_log("Extracting ", name, " for f", ff, ".")
    nbm_extract_message(urls, idx, spec$pattern, name, raw_path)
    csv_path <- nbm_regrid_to_csv(raw_path, stub, spec$interpolation)
    fields[[name]] <- nbm_read_field_csv(csv_path, name)
  }

  merged <- fields[[1]]
  for (name in names(fields)[-1]) {
    merged <- merge(merged, fields[[name]][, c("key", name)], by = "key", all = FALSE, sort = FALSE)
  }
  expected <- nbm_domain$nx * nbm_domain$ny
  if (nrow(merged) < expected * 0.95) {
    stop("NBM merged grid retained only ", nrow(merged), " of ", expected, " expected points.")
  }

  merged$wind_dir_deg <- ((merged$wind_dir_deg %% 360) + 360) %% 360
  ms_names <- c("wind_p10_ms", "wind_p50_ms", "wind_p90_ms", "gust_p10_ms", "gust_p50_ms", "gust_p90_ms")
  for (name in ms_names) merged[[sub("_ms$", "_mph", name)]] <- merged[[name]] * 2.2369362921
  merged$grid_i <- as.integer(round((merged$lon - nbm_domain$west) / nbm_domain$resolution_degrees))
  merged$grid_j <- as.integer(round((merged$lat - nbm_domain$south) / nbm_domain$resolution_degrees))
  merged$wind_dir_cardinal <- nbm_cardinal(merged$wind_dir_deg)

  valid_time <- nbm_as_utc(row$valid_time)
  file_name <- sprintf("nbm_wind_guidance_%s_f%s.geojson", cycle_tag, ff)
  output_path <- file.path(nbm_dirs$timeset, file_name)

  features <- lapply(seq_len(nrow(merged)), function(i) {
    p <- merged[i, ]
    list(
      type = "Feature",
      geometry = list(type = "Point", coordinates = c(round(p$lon, 5), round(p$lat, 5))),
      properties = list(
        grid_i = p$grid_i,
        grid_j = p$grid_j,
        wind_dir_degrees = round(p$wind_dir_deg, 1),
        wind_dir_cardinal = p$wind_dir_cardinal,
        wind_p10_mph = round(p$wind_p10_mph, 1),
        wind_p50_mph = round(p$wind_p50_mph, 1),
        wind_p90_mph = round(p$wind_p90_mph, 1),
        gust_p10_mph = round(p$gust_p10_mph, 1),
        gust_p50_mph = round(p$gust_p50_mph, 1),
        gust_p90_mph = round(p$gust_p90_mph, 1)
      )
    )
  })

  collection <- list(
    type = "FeatureCollection",
    name = "NOAA NBM wind guidance",
    metadata = list(
      feed_version = nbm_version,
      model = "NOAA/NWS National Blend of Models",
      model_cycle_utc = nbm_fmt_iso_utc(cycle_time),
      valid_time_utc = nbm_fmt_iso_utc(valid_time),
      forecast_hour = fhr,
      target_lead_hours = as.integer(row$lead_hours),
      domain = nbm_domain,
      guidance = list(
        direction = "NBM core 10-m WDIR central guidance",
        sustained = "NBM QMD 10th/50th/90th percentile 10-m WIND",
        gust = "NBM QMD 10th/50th/90th percentile 10-m GUST"
      )
    ),
    features = features
  )
  jsonlite::write_json(collection, output_path, auto_unbox = TRUE, digits = 8, null = "null", pretty = FALSE)

  wind50_stats <- nbm_percentile_stats(merged$wind_p50_mph)
  gust50_stats <- nbm_percentile_stats(merged$gust_p50_mph)
  gust_scale <- unname(as.numeric(gust50_stats$p95 %||% 40))
  gust_scale <- min(max(gust_scale, 15), 70)

  list(
    feed_version = nbm_version,
    model = "NOAA/NWS National Blend of Models",
    product = "NBM wind central guidance + p10-p90 uncertainty",
    target_lead_hours = as.integer(row$lead_hours),
    target_label = paste0("+", as.integer(row$lead_hours), " hr"),
    model_cycle_utc = nbm_fmt_iso_utc(cycle_time),
    model_cycle_local = nbm_fmt_local(cycle_time, nbm_local_tz),
    forecast_hour = fhr,
    forecast_hour_label = paste0("f", ff),
    valid_time_utc = nbm_fmt_iso_utc(valid_time),
    valid_time_local = nbm_fmt_local(valid_time, nbm_local_tz),
    relative_url = file.path("nbm", "guidance", file_name),
    feature_count = nrow(merged),
    grid = list(nx = nbm_domain$nx, ny = nbm_domain$ny, resolution_degrees = nbm_domain$resolution_degrees),
    wind_p50_mph = wind50_stats,
    gust_p50_mph = gust50_stats,
    recommended_gust_scale_mph = gust_scale,
    color_scale_reference = "95th percentile of NBM median gust, clamped to 15-70 mph",
    source_core = core_urls$grib,
    source_qmd = qmd_urls$grib
  )
}

now_utc <- nbm_as_utc(Sys.time())
selection <- nbm_choose_cycle(now_utc)
cycle_time <- selection$cycle
plan <- selection$plan
nbm_log("Selected NBM QMD cycle ", nbm_fmt_iso_utc(cycle_time), ".")

entries <- lapply(seq_len(nrow(plan)), function(i) nbm_build_entry(cycle_time, plan[i, ]))
entries <- entries[order(vapply(entries, function(x) x$target_lead_hours, numeric(1)))]

manifest <- list(
  feed_version = nbm_version,
  generated_at_utc = nbm_fmt_iso_utc(now_utc),
  generated_at_local = nbm_fmt_local(now_utc, nbm_local_tz),
  model = "NOAA/NWS National Blend of Models",
  product = "Wind central guidance and uncertainty",
  description = "NBM median sustained wind/direction and median gust with 10th-90th percentile ranges.",
  update_note = "QMD percentile guidance uses 00/06/12/18 UTC NBM cycles.",
  domain = nbm_domain,
  target_lead_hours = as.list(nbm_target_lead_hours),
  published_support_lead_hours = as.list(nbm_publish_lead_hours),
  support_window_hours = nbm_support_window_hours,
  entries = entries
)
jsonlite::write_json(manifest, nbm_files$manifest, auto_unbox = TRUE, digits = 8, null = "null", pretty = TRUE)

summary <- list(
  feed_version = nbm_version,
  generated_at_utc = nbm_fmt_iso_utc(now_utc),
  selected_cycle_utc = nbm_fmt_iso_utc(cycle_time),
  selected_cycle_local = nbm_fmt_local(cycle_time, nbm_local_tz),
  entry_count = length(entries),
  feature_count_per_entry = vapply(entries, function(x) x$feature_count, numeric(1)),
  valid_times_utc = vapply(entries, function(x) x$valid_time_utc, character(1)),
  target_lead_hours = as.list(nbm_target_lead_hours),
  published_support_lead_hours = vapply(entries, function(x) x$target_lead_hours, numeric(1)),
  support_window_hours = nbm_support_window_hours,
  domain = nbm_domain
)
jsonlite::write_json(summary, nbm_files$summary, auto_unbox = TRUE, digits = 8, null = "null", pretty = TRUE)

inventory <- data.frame(
  target_lead_hours = vapply(entries, function(x) x$target_lead_hours, numeric(1)),
  forecast_hour = vapply(entries, function(x) x$forecast_hour, numeric(1)),
  valid_time_utc = vapply(entries, function(x) x$valid_time_utc, character(1)),
  relative_url = vapply(entries, function(x) x$relative_url, character(1)),
  feature_count = vapply(entries, function(x) x$feature_count, numeric(1)),
  stringsAsFactors = FALSE
)
utils::write.csv(inventory, file.path(nbm_dirs$qa, "nbm_wind_guidance_inventory.csv"), row.names = FALSE)

# Prune old dated GeoJSON files by the cycle encoded in the filename. GitHub
# checkout mtimes are fresh on every runner, so filesystem age is not reliable.
current_names <- basename(vapply(entries, function(x) x$relative_url, character(1)))
old_files <- list.files(nbm_dirs$timeset, pattern = "^nbm_wind_guidance_.*\\.geojson$", full.names = TRUE)
if (length(old_files)) {
  cycle_text <- sub(
    "^nbm_wind_guidance_([0-9]{8}T[0-9]{2}Z)_f[0-9]{3}\\.geojson$",
    "\\1",
    basename(old_files)
  )
  file_cycles <- as.POSIXct(cycle_text, format = "%Y%m%dT%HZ", tz = "UTC")
  cutoff <- nbm_as_utc(cycle_time) - nbm_retain_hours * 3600
  drop <- !basename(old_files) %in% current_names & is.finite(as.numeric(file_cycles)) & file_cycles < cutoff
  if (any(drop)) unlink(old_files[drop], force = TRUE)
}

qa_zip <- file.path(nbm_dirs$qa, "NBM_wind_guidance_QA_upload.zip")
try(unlink(qa_zip, force = TRUE), silent = TRUE)
qa_files <- c(
  nbm_files$manifest,
  nbm_files$summary,
  file.path(nbm_dirs$qa, "nbm_wind_guidance_inventory.csv"),
  nbm_log_file
)
qa_files <- qa_files[file.exists(qa_files)]
if (length(qa_files)) {
  root_norm <- normalizePath(nbm_project_root, winslash = "/", mustWork = TRUE)
  qa_norm <- normalizePath(qa_files, winslash = "/", mustWork = TRUE)
  prefix <- paste0(root_norm, "/")
  relative_files <- ifelse(
    startsWith(qa_norm, prefix),
    substring(qa_norm, nchar(prefix) + 1L),
    qa_norm
  )
  utils::zip(qa_zip, files = relative_files)
}

nbm_log("Published NBM wind guidance manifest with ", length(entries), " entries.")
nbm_log("Manifest: ", nbm_files$manifest)
nbm_log("Summary: ", nbm_files$summary)
