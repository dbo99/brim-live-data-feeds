# Pure helpers for the BRIM Winter Storm Levels NBM publisher.

WSL_METERS_TO_FEET <- 3.28083989501312
WSL_TRANSIENT_HTTP_STATUS <- c(408L, 429L, 500L, 502L, 503L, 504L)

wsl_stop <- function(class, ..., call. = FALSE) {
  condition <- structure(
    list(message = paste0(...), call = NULL),
    class = c(class, "winter_storm_levels_error", "error", "condition")
  )
  stop(condition)
}

wsl_as_utc <- function(x) {
  if (inherits(x, "POSIXt")) {
    return(as.POSIXct(as.numeric(x), origin = "1970-01-01", tz = "UTC"))
  }
  if (is.numeric(x)) return(as.POSIXct(x, origin = "1970-01-01", tz = "UTC"))
  parsed <- as.POSIXct(x, tz = "UTC", format = "%Y-%m-%dT%H:%M:%SZ")
  if (is.na(parsed)) parsed <- as.POSIXct(x, tz = "UTC")
  parsed
}

wsl_iso_utc <- function(x) {
  format(wsl_as_utc(x), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
}

wsl_parse_integer_csv <- function(value, field) {
  values <- suppressWarnings(as.integer(trimws(strsplit(as.character(value), ",", fixed = TRUE)[[1]])))
  if (!length(values) || anyNA(values)) stop("Invalid integer list in ", field, ".")
  sort(unique(values))
}

wsl_read_config <- function(path) {
  config <- utils::read.csv(path, stringsAsFactors = FALSE, check.names = FALSE)
  if (nrow(config) != 1L) stop("Winter Storm Levels config must contain exactly one data row.")
  row <- as.list(config[1L, , drop = FALSE])
  row$source_cycle_hours_utc <- wsl_parse_integer_csv(row$source_cycle_hours_utc, "source_cycle_hours_utc")
  row$forecast_lead_hours <- wsl_parse_integer_csv(row$forecast_lead_hours, "forecast_lead_hours")
  numeric_fields <- c(
    "west", "east", "south", "north", "source_west", "source_east",
    "source_south", "source_north", "contour_min_ft", "contour_max_ft",
    "contour_interval_ft", "simplify_tolerance_m", "coordinate_digits",
    "current_after_hours", "delayed_after_hours", "expire_after_hours",
    "valid_tolerance_hours", "retain_cycle_count", "cycle_lookback_hours",
    "http_max_tries", "http_timeout_seconds"
  )
  for (field in numeric_fields) row[[field]] <- as.numeric(row[[field]])

  if (any(!is.finite(unlist(row[numeric_fields])))) stop("Config contains non-finite numeric values.")
  if (!(row$west < row$east && row$south < row$north)) stop("Invalid display bounds.")
  if (!(row$source_west <= row$west && row$source_east >= row$east &&
        row$source_south <= row$south && row$source_north >= row$north)) {
    stop("Source crop must fully buffer the display domain.")
  }
  if (!(row$contour_min_ft >= 0 && row$contour_max_ft > row$contour_min_ft &&
        row$contour_interval_ft > 0)) stop("Invalid contour range.")
  if (!(row$current_after_hours < row$delayed_after_hours &&
        row$delayed_after_hours < row$expire_after_hours)) stop("Invalid freshness thresholds.")
  if (any(!row$source_cycle_hours_utc %in% 0:23)) stop("Invalid source cycle hour.")
  if (any(row$forecast_lead_hours < 0)) stop("Forecast lead hours cannot be negative.")
  row
}

wsl_floor_cycle <- function(now, cycle_hours = c(0L, 6L, 12L, 18L)) {
  now <- wsl_as_utc(now)
  day <- as.POSIXct(format(now, "%Y-%m-%d 00:00:00", tz = "UTC"), tz = "UTC")
  candidates <- day + as.integer(cycle_hours) * 3600
  candidates <- candidates[candidates <= now]
  if (!length(candidates)) return(day - (24L - max(cycle_hours)) * 3600)
  max(candidates)
}

wsl_candidate_cycles <- function(now, config) {
  newest <- wsl_floor_cycle(now, config$source_cycle_hours_utc)
  count <- floor(config$cycle_lookback_hours / 6) + 1L
  newest - seq.int(0L, count - 1L) * 6L * 3600
}

wsl_cycle_token <- function(cycle_time) {
  format(wsl_as_utc(cycle_time), "%Y%m%d%H", tz = "UTC")
}

wsl_nbm_urls <- function(cycle_time, lead_hour) {
  date <- format(wsl_as_utc(cycle_time), "%Y%m%d", tz = "UTC")
  hour <- format(wsl_as_utc(cycle_time), "%H", tz = "UTC")
  filename <- sprintf("blend.t%sz.core.f%03d.co.grib2", hour, as.integer(lead_hour))
  base <- sprintf(
    "https://noaa-nbm-grib2-pds.s3.amazonaws.com/blend.%s/%s/core/%s",
    date, hour, filename
  )
  list(grib = base, index = paste0(base, ".idx"))
}

wsl_classify_http_status <- function(status) {
  status <- as.integer(status)
  if (status >= 200L && status < 300L) return("ok")
  if (status %in% c(403L, 404L)) return("source_unavailable")
  if (status %in% WSL_TRANSIENT_HTTP_STATUS) return("fetch_failed_transient")
  "fetch_failed_permanent"
}

wsl_retry_delay <- function(response, attempt, jitterer = function(max_seconds) {
  stats::runif(1L, min = 0, max = max_seconds)
}) {
  if (!is.null(response) && !inherits(response, "error")) {
    retry_after <- httr2::resp_header(response, "retry-after")
    if (!is.null(retry_after) && grepl("^[0-9]+(?:\\.[0-9]+)?$", retry_after)) {
      return(min(as.numeric(retry_after), 30))
    }
  }
  base <- min(2 ^ (as.integer(attempt) - 1L), 4)
  min(base + max(0, as.numeric(jitterer(0.25))), 5)
}

wsl_http_request <- function(
    request,
    max_tries = 3L,
    performer = httr2::req_perform,
    sleeper = Sys.sleep,
    jitterer = function(max_seconds) stats::runif(1L, min = 0, max = max_seconds)
) {
  max_tries <- max(1L, as.integer(max_tries))
  last_error <- NULL
  for (attempt in seq_len(max_tries)) {
    response <- tryCatch(performer(request), error = function(error) error)
    if (!inherits(response, "error")) {
      status <- httr2::resp_status(response)
      classification <- wsl_classify_http_status(status)
      if (identical(classification, "ok")) return(response)
      if (!identical(classification, "fetch_failed_transient") || attempt == max_tries) {
        wsl_stop(classification, "HTTP ", status, " after ", attempt, " attempt(s).")
      }
      last_error <- paste0("HTTP ", status)
    } else {
      last_error <- conditionMessage(response)
      if (attempt == max_tries) {
        wsl_stop("fetch_failed_transient", "Transport failure after ", attempt,
                 " attempt(s): ", last_error)
      }
    }
    sleeper(wsl_retry_delay(response, attempt, jitterer))
  }
  wsl_stop("fetch_failed_transient", "Request failed: ", last_error)
}

wsl_fetch_text <- function(url, config) {
  request <- httr2::request(url) |>
    httr2::req_user_agent("BRIM-Winter-Storm-Levels/1.0") |>
    httr2::req_timeout(config$http_timeout_seconds) |>
    httr2::req_error(is_error = function(response) FALSE)
  response <- wsl_http_request(request, config$http_max_tries)
  httr2::resp_body_string(response)
}

wsl_validate_range_response <- function(response, start_byte, end_byte) {
  expected_length <- as.integer(end_byte - start_byte + 1)
  status <- httr2::resp_status(response)
  if (status != 206L) wsl_stop("validation_failed", "Range request returned HTTP ", status, ".")
  content_range <- httr2::resp_header(response, "content-range")
  expected_range <- sprintf("bytes %d-%d/", start_byte, end_byte)
  if (is.null(content_range) || !startsWith(content_range, expected_range)) {
    wsl_stop("validation_failed", "Unexpected Content-Range response: ", content_range %||% "<missing>")
  }
  body <- httr2::resp_body_raw(response)
  if (length(body) != expected_length) {
    wsl_stop("validation_failed", "Range response length mismatch: expected ",
             expected_length, ", received ", length(body), ".")
  }
  body
}

wsl_fetch_range <- function(url, start_byte, end_byte, path, config) {
  request <- httr2::request(url) |>
    httr2::req_user_agent("BRIM-Winter-Storm-Levels/1.0") |>
    httr2::req_headers(Range = sprintf("bytes=%d-%d", start_byte, end_byte)) |>
    httr2::req_timeout(config$http_timeout_seconds) |>
    httr2::req_error(is_error = function(response) FALSE)
  response <- wsl_http_request(request, config$http_max_tries)
  body <- wsl_validate_range_response(response, start_byte, end_byte)
  writeBin(body, path)
  invisible(path)
}

`%||%` <- function(x, y) {
  if (is.null(x) || length(x) == 0L || all(is.na(x))) y else x
}

wsl_parse_index <- function(text, cycle_time, lead_hour) {
  lines <- strsplit(text, "\n", fixed = TRUE)[[1]]
  lines <- lines[nzchar(lines)]
  parts <- strsplit(lines, ":", fixed = TRUE)
  offsets <- suppressWarnings(vapply(parts, function(x) as.numeric(x[2L]), numeric(1)))
  expected_cycle <- paste0("d=", wsl_cycle_token(cycle_time))
  expected_forecast <- sprintf("%d hour fcst", as.integer(lead_hour))
  matches <- vapply(parts, function(fields) {
    length(fields) >= 6L &&
      identical(fields[3L], expected_cycle) &&
      identical(fields[4L], "SNOWLVL") &&
      identical(fields[5L], "0 m above mean sea level") &&
      identical(fields[6L], expected_forecast) &&
      !any(grepl("percentileValue=", fields, fixed = TRUE))
  }, logical(1))
  positions <- which(matches)
  variable_present <- any(vapply(parts, function(fields) {
    length(fields) >= 4L && identical(fields[4L], "SNOWLVL")
  }, logical(1)))
  if (!variable_present) {
    wsl_stop("variable_missing", "The source inventory does not contain SNOWLVL.")
  }
  if (length(positions) != 1L) {
    wsl_stop("validation_failed", "Expected one deterministic SNOWLVL record for ",
             expected_cycle, " f", sprintf("%03d", lead_hour), "; found ", length(positions), ".")
  }
  position <- positions[[1L]]
  if (position >= length(lines) || !is.finite(offsets[position + 1L])) {
    wsl_stop("validation_failed", "SNOWLVL record has no following byte offset.")
  }
  list(
    record_number = as.integer(parts[[position]][1L]),
    start_byte = offsets[position],
    end_byte = offsets[position + 1L] - 1,
    cycle_time_utc = wsl_iso_utc(cycle_time),
    valid_time_utc = wsl_iso_utc(wsl_as_utc(cycle_time) + as.integer(lead_hour) * 3600),
    lead_hours = as.integer(lead_hour),
    inventory_line = lines[position]
  )
}

wsl_discover_cycle <- function(now, config, fetch_text = wsl_fetch_text) {
  attempts <- list()
  for (cycle in wsl_candidate_cycles(now, config)) {
    records <- list()
    cycle_ok <- TRUE
    reason <- NULL
    for (lead in config$forecast_lead_hours) {
      urls <- wsl_nbm_urls(cycle, lead)
      result <- tryCatch(
        wsl_parse_index(fetch_text(urls$index, config), cycle, lead),
        error = function(error) error
      )
      if (inherits(result, "error")) {
        cycle_ok <- FALSE
        reason <- conditionMessage(result)
        outcome <- if ("variable_missing" %in% class(result)) {
          "variable_missing"
        } else if ("validation_failed" %in% class(result)) {
          "validation_failed"
        } else if (any(c("fetch_failed_transient", "fetch_failed_permanent") %in% class(result))) {
          "fetch_failed"
        } else {
          "source_unavailable"
        }
        break
      }
      result$grib_url <- urls$grib
      result$index_url <- urls$index
      records[[as.character(lead)]] <- result
    }
    attempts[[length(attempts) + 1L]] <- list(
      cycle_time_utc = wsl_iso_utc(cycle), complete = cycle_ok,
      outcome = if (cycle_ok) "success" else outcome, reason = reason
    )
    if (cycle_ok) return(list(cycle_time = wsl_as_utc(cycle), records = records, attempts = attempts))
  }
  outcomes <- vapply(attempts, `[[`, character(1), "outcome")
  final_class <- if ("validation_failed" %in% outcomes) {
    "validation_failed"
  } else if ("variable_missing" %in% outcomes) {
    "variable_missing"
  } else if ("fetch_failed" %in% outcomes) {
    "fetch_failed_transient"
  } else {
    "source_unavailable"
  }
  condition <- structure(
    list(
      message = "No complete NBM cycle was found within the configured lookback.",
      call = NULL,
      attempts = attempts
    ),
    class = c(final_class, "winter_storm_levels_error", "error", "condition")
  )
  stop(condition)
}

wsl_decode_grib <- function(path, reader = terra::rast) {
  tryCatch(
    reader(path),
    error = function(error) wsl_stop("decode_failed", "GRIB decode failed: ", conditionMessage(error))
  )
}

wsl_validate_raster <- function(raster, record, config) {
  if (!inherits(raster, "SpatRaster")) stop("Decoded source is not a SpatRaster.")
  if (terra::nlyr(raster) != 1L) stop("SNOWLVL message must decode to one raster layer.")
  if (!nzchar(terra::crs(raster))) stop("SNOWLVL raster has no coordinate reference system.")
  units <- tolower(trimws(terra::units(raster)[1L] %||% ""))
  if (!units %in% c("m", "meter", "metre", "meters", "metres")) {
    stop("SNOWLVL raster units must be metres; received: ", units)
  }
  valid_time <- terra::time(raster)[1L]
  if (is.na(valid_time) || !identical(wsl_iso_utc(valid_time), record$valid_time_utc)) {
    stop("Decoded GRIB valid time does not match the selected inventory record.")
  }
  values <- terra::values(raster, mat = FALSE)
  finite <- is.finite(values) & values < 9999
  coverage <- mean(finite)
  if (!is.finite(coverage) || coverage < 0.95) stop("SNOWLVL finite coverage is below 95%.")
  range_m <- range(values[finite])
  if (range_m[1L] < -500 || range_m[2L] > 12000) {
    stop("SNOWLVL values are outside the physical validation range (-500..12000 m).")
  }
  list(
    finite_coverage = unname(coverage),
    min_m = unname(range_m[1L]),
    max_m = unname(range_m[2L]),
    rows = terra::nrow(raster), columns = terra::ncol(raster),
    resolution_m = unname(mean(terra::res(raster)))
  )
}

wsl_bbox_polygon <- function(west, east, south, north, crs = "EPSG:4326") {
  sf::st_as_sfc(sf::st_bbox(c(xmin = west, ymin = south, xmax = east, ymax = north), crs = crs))
}

wsl_normalize_line <- function(coordinates, digits) {
  coordinates <- round(unclass(coordinates)[, 1:2, drop = FALSE], digits)
  if (nrow(coordinates) < 2L) return(NULL)
  first <- coordinates[1L, ]
  last <- coordinates[nrow(coordinates), ]
  reverse <- first[1L] > last[1L] || (first[1L] == last[1L] && first[2L] > last[2L])
  if (reverse) coordinates <- coordinates[nrow(coordinates):1L, , drop = FALSE]
  coordinates
}

wsl_make_contours <- function(raster, record, config) {
  source_crs <- terra::crs(raster)
  source_box <- terra::vect(wsl_bbox_polygon(
    config$source_west, config$source_east, config$source_south, config$source_north
  ))
  source_box <- terra::project(source_box, source_crs)
  cropped <- terra::crop(raster, source_box, snap = "out")
  cropped[cropped >= 9999] <- NA
  feet <- cropped * WSL_METERS_TO_FEET
  levels <- seq(config$contour_min_ft, config$contour_max_ft, by = config$contour_interval_ft)
  contours <- tryCatch(
    terra::as.contour(feet, levels = levels),
    error = function(error) NULL
  )
  contour_count <- tryCatch(nrow(contours), error = function(error) NULL)
  if (is.null(contour_count) || !length(contour_count) || contour_count == 0L) {
    stop("No configured snow-level contours intersect the source crop.")
  }
  contours_sf <- sf::st_as_sf(contours)
  level_field <- intersect(c("level", "elevation", "value"), names(contours_sf))[1L]
  if (is.na(level_field)) stop("Contour conversion did not expose an elevation field.")
  names(contours_sf)[names(contours_sf) == level_field] <- "level_ft_msl"

  processing_crs <- source_crs
  if (isTRUE(sf::st_is_longlat(contours_sf))) {
    processing_crs <- "EPSG:5070"
    contours_sf <- sf::st_transform(contours_sf, processing_crs)
  }

  display <- sf::st_transform(wsl_bbox_polygon(
    config$west, config$east, config$south, config$north
  ), processing_crs)
  previous_s2 <- suppressMessages(sf::sf_use_s2(FALSE))
  on.exit(suppressMessages(sf::sf_use_s2(previous_s2)), add = TRUE)
  clipped <- suppressMessages(suppressWarnings(sf::st_intersection(contours_sf, display)))
  clipped <- suppressWarnings(sf::st_collection_extract(clipped, "LINESTRING"))
  clipped <- suppressWarnings(sf::st_cast(clipped, "LINESTRING"))
  clipped <- clipped[!sf::st_is_empty(clipped), , drop = FALSE]
  if (!nrow(clipped)) stop("No snow-level contours intersect the display domain.")
  simplified <- sf::st_simplify(clipped, dTolerance = config$simplify_tolerance_m, preserveTopology = TRUE)
  simplified <- simplified[!sf::st_is_empty(simplified), , drop = FALSE]
  simplified <- sf::st_transform(simplified, 4326)
  # The projected clip prevents curved-edge artifacts during simplification.
  # A final planar lon/lat clip makes the serialized API bounds exact.
  simplified <- suppressMessages(suppressWarnings(sf::st_intersection(
    simplified,
    wsl_bbox_polygon(config$west, config$east, config$south, config$north)
  )))
  simplified <- suppressWarnings(sf::st_collection_extract(simplified, "LINESTRING"))
  simplified <- suppressWarnings(sf::st_cast(simplified, "LINESTRING"))
  simplified <- simplified[!sf::st_is_empty(simplified), , drop = FALSE]
  simplified$length_m <- as.numeric(sf::st_length(sf::st_transform(simplified, 5070)))

  rows <- lapply(seq_len(nrow(simplified)), function(index) {
    coordinates <- wsl_normalize_line(sf::st_coordinates(sf::st_geometry(simplified)[[index]]), config$coordinate_digits)
    if (is.null(coordinates)) return(NULL)
    level <- as.integer(round(simplified$level_ft_msl[index]))
    list(
      level = level,
      length_m = round(simplified$length_m[index], 1),
      coordinates = coordinates,
      key = sprintf("%05d|%s", level, paste(format(coordinates, scientific = FALSE, trim = TRUE), collapse = ","))
    )
  })
  rows <- Filter(Negate(is.null), rows)
  rows <- rows[order(vapply(rows, `[[`, character(1), "key"))]
  if (!length(rows)) stop("Contour simplification removed every line.")

  level_counts <- integer()
  features <- lapply(rows, function(row) {
    key <- as.character(row$level)
    level_counts[key] <<- (level_counts[key] %||% 0L) + 1L
    list(
      type = "Feature",
      id = sprintf("%s_f%03d_%05d_%03d", wsl_cycle_token(record$cycle_time_utc),
                   record$lead_hours, row$level, level_counts[key]),
      properties = list(
        product_id = config$product_id,
        source_id = config$source_id,
        parameter = "snow_level",
        definition = "height of the wet-bulb 0.5 degree C surface",
        level_ft_msl = row$level,
        label = sprintf("%s ft MSL", format(row$level, big.mark = ",", scientific = FALSE)),
        unit = "ft_msl",
        cycle_time_utc = record$cycle_time_utc,
        valid_time_utc = record$valid_time_utc,
        lead_hours = record$lead_hours,
        segment = level_counts[key],
        length_m = row$length_m
      ),
      geometry = list(type = "LineString", coordinates = unname(split(row$coordinates, row(row$coordinates))))
    )
  })
  all_coordinates <- do.call(rbind, lapply(rows, `[[`, "coordinates"))
  bbox <- c(min(all_coordinates[, 1L]), min(all_coordinates[, 2L]),
            max(all_coordinates[, 1L]), max(all_coordinates[, 2L]))
  list(
    geojson = list(
      type = "FeatureCollection",
      contract_version = config$contract_version,
      bbox = unname(bbox),
      features = features
    ),
    feature_count = length(features),
    contour_levels = sort(unique(vapply(rows, `[[`, integer(1), "level"))),
    bbox = unname(bbox)
  )
}

wsl_write_json <- function(value, path, pretty = TRUE) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  text <- jsonlite::toJSON(value, auto_unbox = TRUE, null = "null", na = "null",
                           digits = NA, pretty = pretty)
  writeLines(c(text, ""), path, useBytes = TRUE)
  invisible(path)
}

wsl_sha256 <- function(path) digest::digest(file = path, algo = "sha256")

wsl_safe_target_path <- function(path) {
  expected <- "^nbm/snow-level/winter_storm_levels_nbm_snow_level_[0-9]{10}_f[0-9]{3}_[0-9a-f]{12}\\.geojson$"
  if (length(path) != 1L || is.na(path) || !nzchar(path) || startsWith(path, "/") ||
      grepl("\\\\", path) || any(strsplit(path, "/", fixed = TRUE)[[1L]] %in% c("", ".", "..")) ||
      !grepl(expected, path)) {
    stop("Unsafe Winter Storm Levels target path: ", path)
  }
  path
}

wsl_target_relative_path <- function(record, content_sha256 = NULL) {
  if (is.null(content_sha256) || !grepl("^[0-9a-f]{64}$", content_sha256)) {
    stop("A full lowercase SHA-256 is required for a Winter Storm Levels target path.")
  }
  content_suffix <- paste0("_", substr(content_sha256, 1L, 12L))
  sprintf(
    "nbm/snow-level/winter_storm_levels_nbm_snow_level_%s_f%03d%s.geojson",
    wsl_cycle_token(record$cycle_time_utc), record$lead_hours, content_suffix
  )
}

wsl_freshness_status <- function(cycle_time, now, config, has_active_target = TRUE) {
  age_hours <- as.numeric(difftime(wsl_as_utc(now), wsl_as_utc(cycle_time), units = "hours"))
  if (!has_active_target || age_hours > config$expire_after_hours) return("expired")
  if (age_hours > config$delayed_after_hours) return("stale_last_known_good")
  if (age_hours > config$current_after_hours) return("delayed_but_usable")
  "current"
}

wsl_target_entry <- function(record, relative_path, stats, contour, file_path, config) {
  valid <- wsl_as_utc(record$valid_time_utc)
  list(
    source_id = config$source_id,
    cycle_time_utc = record$cycle_time_utc,
    valid_time_utc = record$valid_time_utc,
    valid_from_utc = wsl_iso_utc(valid - config$valid_tolerance_hours * 3600),
    valid_through_utc = wsl_iso_utc(valid + config$valid_tolerance_hours * 3600),
    lead_hours = record$lead_hours,
    retrieval_url = record$grib_url,
    inventory_url = record$index_url,
    inventory_record = record$inventory_line,
    path = relative_path,
    media_type = "application/geo+json",
    sha256 = wsl_sha256(file_path),
    bytes = unname(file.info(file_path)$size),
    feature_count = contour$feature_count,
    contour_levels_ft_msl = contour$contour_levels,
    source_grid = list(
      rows = stats$rows, columns = stats$columns,
      resolution_m = round(stats$resolution_m, 3),
      finite_coverage = round(stats$finite_coverage, 6),
      min_m = round(stats$min_m, 3), max_m = round(stats$max_m, 3)
    ),
    output_bbox_wgs84 = contour$bbox
  )
}

wsl_manifest <- function(cycle_time, entries, now, config, publication_time = now) {
  has_active <- any(vapply(entries, function(entry) {
    wsl_as_utc(now) >= wsl_as_utc(entry$valid_from_utc) &&
      wsl_as_utc(now) <= wsl_as_utc(entry$valid_through_utc)
  }, logical(1)))
  selected_cycle <- wsl_iso_utc(cycle_time)
  current_cycle_count <- sum(vapply(entries, function(entry) {
    identical(entry$cycle_time_utc, selected_cycle)
  }, logical(1)))
  list(
    product_id = config$product_id,
    schema_version = config$contract_version,
    contract_version = config$contract_version,
    status = wsl_freshness_status(cycle_time, now, config, has_active),
    source = list(
      id = config$source_id,
      name = "NOAA/NWS National Blend of Models deterministic snow level",
      agency = "NOAA/NWS/NCEP Meteorological Development Laboratory",
      parameter = "SNOWLVL",
      definition = "Elevation where wet-bulb temperature reaches 0.5 degrees C",
      source_unit = "m above mean sea level",
      output_unit = "ft above mean sea level",
      product_url = "https://www.nco.ncep.noaa.gov/pmb/products/blend/",
      retrieval_base_url = "https://noaa-nbm-grib2-pds.s3.amazonaws.com/",
      alternate_retrieval_base_url = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/"
    ),
    domain = list(
      id = config$domain_id, label = config$domain_label,
      bbox_wgs84 = c(config$west, config$south, config$east, config$north)
    ),
    contour = list(
      geometry = "LineString", datum = "mean_sea_level", unit = "ft_msl",
      minimum_ft = config$contour_min_ft, maximum_ft = config$contour_max_ft,
      interval_ft = config$contour_interval_ft,
      simplify_tolerance_m = config$simplify_tolerance_m
    ),
    freshness = list(
      current_after_hours = config$current_after_hours,
      delayed_after_hours = config$delayed_after_hours,
      expire_after_hours = config$expire_after_hours,
      valid_tolerance_hours = config$valid_tolerance_hours
    ),
    cycle_time_utc = wsl_iso_utc(cycle_time),
    retrieval_time_utc = wsl_iso_utc(now),
    publication_time_utc = if (is.null(publication_time)) NULL else wsl_iso_utc(publication_time),
    target_count = length(entries),
    diagnostics = list(
      expected_current_cycle_target_count = length(config$forecast_lead_hours),
      actual_current_cycle_target_count = current_cycle_count,
      retained_cycle_count = length(unique(vapply(entries, `[[`, character(1), "cycle_time_utc"))),
      complete_bundle_validated = TRUE
    ),
    targets = entries
  )
}

wsl_validate_geojson <- function(path, record, config) {
  payload <- jsonlite::fromJSON(path, simplifyVector = FALSE)
  if (!all(c("type", "contract_version", "bbox", "features") %in% names(payload))) {
    stop("GeoJSON root fields are incomplete.")
  }
  if (!identical(payload$type, "FeatureCollection")) stop("GeoJSON root is not a FeatureCollection.")
  if (!identical(payload$contract_version, config$contract_version)) {
    stop("GeoJSON contract version mismatch.")
  }
  if (!length(payload$features)) stop("GeoJSON has no contour features.")
  required <- c("product_id", "source_id", "parameter", "definition", "level_ft_msl",
                "label", "unit", "cycle_time_utc", "valid_time_utc", "lead_hours",
                "segment", "length_m")
  ids <- character()
  all_coordinates <- list()
  for (feature in payload$features) {
    if (!all(c("type", "id", "properties", "geometry") %in% names(feature)) ||
        !identical(feature$type, "Feature") || !is.character(feature$id) ||
        length(feature$id) != 1L || !nzchar(feature$id)) {
      stop("GeoJSON feature identity is incomplete.")
    }
    if (!identical(feature$geometry$type, "LineString")) stop("Unexpected GeoJSON geometry type.")
    if (!all(required %in% names(feature$properties))) stop("GeoJSON feature properties are incomplete.")
    properties <- feature$properties
    if (!identical(properties$product_id, config$product_id) ||
        !identical(properties$source_id, config$source_id) ||
        !identical(properties$parameter, "snow_level") ||
        !identical(properties$definition, "height of the wet-bulb 0.5 degree C surface") ||
        !identical(properties$unit, "ft_msl")) {
      stop("GeoJSON feature product/source/parameter/unit semantics are invalid.")
    }
    if (!identical(feature$properties$cycle_time_utc, record$cycle_time_utc) ||
        !identical(feature$properties$valid_time_utc, record$valid_time_utc) ||
        as.numeric(feature$properties$lead_hours) != as.numeric(record$lead_hours)) {
      stop("GeoJSON feature time metadata does not match its target.")
    }
    level <- as.numeric(properties$level_ft_msl)
    segment <- as.numeric(properties$segment)
    length_m <- as.numeric(properties$length_m)
    if (!is.finite(level) || level < config$contour_min_ft || level > config$contour_max_ft ||
        level %% config$contour_interval_ft != 0 || !is.character(properties$label) ||
        length(properties$label) != 1L || !nzchar(properties$label) ||
        !is.finite(segment) || segment < 1 || segment %% 1 != 0 ||
        !is.finite(length_m) || length_m <= 0) {
      stop("GeoJSON contour level, label, segment, or length is invalid.")
    }
    flat_coordinates <- as.numeric(unlist(feature$geometry$coordinates))
    if (length(flat_coordinates) < 4L || length(flat_coordinates) %% 2L != 0L) {
      stop("GeoJSON LineString coordinates are incomplete.")
    }
    coordinates <- matrix(
      flat_coordinates, ncol = 2L, byrow = TRUE
    )
    if (any(!is.finite(coordinates))) stop("GeoJSON coordinates contain non-finite values.")
    if (any(coordinates[, 1L] < config$west - 1e-6 | coordinates[, 1L] > config$east + 1e-6 |
            coordinates[, 2L] < config$south - 1e-6 | coordinates[, 2L] > config$north + 1e-6)) {
      stop("GeoJSON coordinates exceed the configured output bounds.")
    }
    ids <- c(ids, feature$id)
    all_coordinates[[length(all_coordinates) + 1L]] <- coordinates
  }
  if (anyDuplicated(ids)) stop("GeoJSON feature IDs are not unique.")
  coordinates <- do.call(rbind, all_coordinates)
  actual_bbox <- c(min(coordinates[, 1L]), min(coordinates[, 2L]),
                   max(coordinates[, 1L]), max(coordinates[, 2L]))
  declared_bbox <- as.numeric(unlist(payload$bbox))
  if (length(declared_bbox) != 4L || any(!is.finite(declared_bbox)) ||
      any(abs(declared_bbox - actual_bbox) > 10 ^ (-config$coordinate_digits))) {
    stop("GeoJSON bbox does not match its serialized coordinates.")
  }
  invisible(payload)
}

wsl_validate_manifest <- function(path, root, config, now = Sys.time()) {
  manifest <- jsonlite::fromJSON(path, simplifyVector = FALSE)
  scalar_number <- function(value) {
    length(value) == 1L && is.numeric(value) && is.finite(value)
  }
  root_fields <- c("product_id", "schema_version", "contract_version", "status", "source", "domain",
                   "contour", "freshness", "cycle_time_utc", "retrieval_time_utc",
                   "publication_time_utc", "target_count", "diagnostics", "targets")
  if (!all(root_fields %in% names(manifest))) stop("Manifest root fields are incomplete.")
  if (!identical(manifest$product_id, config$product_id)) stop("Manifest product_id mismatch.")
  if (!identical(manifest$schema_version, config$contract_version)) stop("Manifest schema version mismatch.")
  if (!identical(manifest$contract_version, config$contract_version)) stop("Manifest contract version mismatch.")
  if (!identical(manifest$source$id, config$source_id) ||
      !identical(manifest$source$parameter, "SNOWLVL") ||
      !identical(manifest$source$source_unit, "m above mean sea level") ||
      !identical(manifest$source$output_unit, "ft above mean sea level")) {
    stop("Manifest source semantics mismatch.")
  }
  if (!identical(as.numeric(unlist(manifest$domain$bbox_wgs84)),
                 c(config$west, config$south, config$east, config$north))) {
    stop("Manifest domain bounds mismatch.")
  }
  if (!identical(manifest$contour$geometry, "LineString") ||
      !identical(manifest$contour$datum, "mean_sea_level") ||
      !identical(manifest$contour$unit, "ft_msl") ||
      as.numeric(manifest$contour$minimum_ft) != config$contour_min_ft ||
      as.numeric(manifest$contour$maximum_ft) != config$contour_max_ft ||
      as.numeric(manifest$contour$interval_ft) != config$contour_interval_ft) {
    stop("Manifest contour contract mismatch.")
  }
  expected_freshness <- c(config$current_after_hours, config$delayed_after_hours,
                          config$expire_after_hours, config$valid_tolerance_hours)
  actual_freshness <- as.numeric(unlist(manifest$freshness[c(
    "current_after_hours", "delayed_after_hours", "expire_after_hours", "valid_tolerance_hours"
  )]))
  if (length(actual_freshness) != 4L || !identical(actual_freshness, expected_freshness)) {
    stop("Manifest freshness contract mismatch.")
  }
  if (!length(manifest$targets)) stop("Manifest has no targets.")
  if (!scalar_number(manifest$target_count) || manifest$target_count %% 1 != 0 ||
      length(manifest$targets) != manifest$target_count) stop("Manifest target count mismatch.")
  if (!scalar_number(manifest$diagnostics$expected_current_cycle_target_count) ||
      as.numeric(manifest$diagnostics$expected_current_cycle_target_count) !=
        length(config$forecast_lead_hours) ||
      !scalar_number(manifest$diagnostics$actual_current_cycle_target_count) ||
      as.numeric(manifest$diagnostics$actual_current_cycle_target_count) !=
        sum(vapply(manifest$targets, function(entry) {
          identical(entry$cycle_time_utc, manifest$cycle_time_utc)
        }, logical(1))) ||
      !scalar_number(manifest$diagnostics$retained_cycle_count) ||
      as.numeric(manifest$diagnostics$retained_cycle_count) !=
        length(unique(vapply(manifest$targets, `[[`, character(1), "cycle_time_utc"))) ||
      !identical(manifest$diagnostics$complete_bundle_validated, TRUE)) {
    stop("Manifest diagnostics summary mismatch.")
  }
  if (!is.character(manifest$status) || length(manifest$status) != 1L ||
      !manifest$status %in% c("current", "delayed_but_usable", "stale_last_known_good", "expired")) {
    stop("Manifest status is invalid.")
  }
  keys <- character()
  for (entry in manifest$targets) {
    entry_fields <- c(
      "source_id", "cycle_time_utc", "valid_time_utc", "valid_from_utc",
      "valid_through_utc", "lead_hours", "retrieval_url", "inventory_url",
      "inventory_record", "path", "media_type", "sha256", "bytes",
      "feature_count", "contour_levels_ft_msl", "source_grid", "output_bbox_wgs84"
    )
    if (!all(entry_fields %in% names(entry))) stop("Manifest target fields are incomplete.")
    relative_path <- wsl_safe_target_path(entry$path)
    target <- file.path(root, relative_path)
    if (!file.exists(target)) stop("Manifest target is missing: ", entry$path)
    if (!identical(entry$source_id, config$source_id)) stop("Manifest target source mismatch.")
    if (!is.character(entry$sha256) || length(entry$sha256) != 1L ||
        !grepl("^[0-9a-f]{64}$", entry$sha256)) stop("Manifest target SHA-256 is invalid.")
    if (!scalar_number(entry$bytes) || entry$bytes < 1 || entry$bytes %% 1 != 0) {
      stop("Manifest target byte count is invalid.")
    }
    expected_path <- wsl_target_relative_path(
      list(cycle_time_utc = entry$cycle_time_utc, lead_hours = entry$lead_hours), entry$sha256
    )
    if (!identical(relative_path, expected_path)) stop("Manifest target path identity mismatch.")
    cycle <- wsl_as_utc(entry$cycle_time_utc)
    valid <- wsl_as_utc(entry$valid_time_utc)
    if (is.na(cycle) || is.na(valid) ||
        !identical(wsl_iso_utc(cycle + as.numeric(entry$lead_hours) * 3600), entry$valid_time_utc)) {
      stop("Manifest target cycle/lead/valid time mismatch.")
    }
    if (!identical(wsl_iso_utc(valid - config$valid_tolerance_hours * 3600), entry$valid_from_utc) ||
        !identical(wsl_iso_utc(valid + config$valid_tolerance_hours * 3600), entry$valid_through_utc)) {
      stop("Manifest target validity window mismatch.")
    }
    if (!identical(entry$media_type, "application/geo+json") ||
        !scalar_number(entry$feature_count) || entry$feature_count < 1 ||
        entry$feature_count %% 1 != 0) {
      stop("Manifest target media type or feature count is invalid.")
    }
    if (!is.character(entry$retrieval_url) || length(entry$retrieval_url) != 1L ||
        !startsWith(entry$retrieval_url, "https://") ||
        !is.character(entry$inventory_url) || length(entry$inventory_url) != 1L ||
        !startsWith(entry$inventory_url, "https://") ||
        !endsWith(entry$inventory_url, ".idx") ||
        !is.character(entry$inventory_record) || length(entry$inventory_record) != 1L ||
        !grepl(":SNOWLVL:0 m above mean sea level:", entry$inventory_record, fixed = TRUE)) {
      stop("Manifest target source provenance is invalid.")
    }
    if (!identical(wsl_sha256(target), entry$sha256)) stop("Manifest target checksum mismatch: ", entry$path)
    if (!identical(unname(file.info(target)$size), as.numeric(entry$bytes))) stop("Manifest target size mismatch.")
    payload <- wsl_validate_geojson(
      target,
      list(cycle_time_utc = entry$cycle_time_utc,
           valid_time_utc = entry$valid_time_utc,
           lead_hours = entry$lead_hours),
      config
    )
    levels <- sort(unique(vapply(payload$features, function(feature) {
      as.numeric(feature$properties$level_ft_msl)
    }, numeric(1))))
    if (length(payload$features) != as.integer(entry$feature_count) ||
        !identical(levels, sort(as.numeric(unlist(entry$contour_levels_ft_msl)))) ||
        any(abs(as.numeric(unlist(payload$bbox)) -
                as.numeric(unlist(entry$output_bbox_wgs84))) > 10 ^ (-config$coordinate_digits))) {
      stop("Manifest target feature count, contour levels, or bounds mismatch.")
    }
    keys <- c(keys, paste(entry$cycle_time_utc, entry$valid_time_utc, sep = "|"))
  }
  if (anyDuplicated(keys)) stop("Manifest contains duplicate cycle/valid-time entries.")
  cycles <- unique(vapply(manifest$targets, `[[`, character(1), "cycle_time_utc"))
  for (cycle in cycles) {
    cycle_leads <- sort(vapply(Filter(function(entry) entry$cycle_time_utc == cycle,
                                      manifest$targets),
                               function(entry) as.integer(entry$lead_hours), integer(1)))
    if (!identical(cycle_leads, as.integer(config$forecast_lead_hours))) {
      stop("Manifest cycle forecast-hour set is incomplete or unexpected: ", cycle)
    }
  }
  target_cycles <- vapply(manifest$targets, function(entry) wsl_as_utc(entry$cycle_time_utc), as.POSIXct(NA))
  if (!identical(wsl_iso_utc(max(target_cycles)), manifest$cycle_time_utc)) {
    stop("Manifest root cycle is not the newest target cycle.")
  }
  invisible(manifest)
}

wsl_semantic_manifest <- function(manifest) {
  manifest$retrieval_time_utc <- NULL
  manifest$publication_time_utc <- NULL
  manifest
}

wsl_is_semantic_noop <- function(candidate_path, canonical_path) {
  if (!file.exists(canonical_path)) return(FALSE)
  candidate <- jsonlite::fromJSON(candidate_path, simplifyVector = FALSE)
  canonical <- jsonlite::fromJSON(canonical_path, simplifyVector = FALSE)
  identical(wsl_semantic_manifest(candidate), wsl_semantic_manifest(canonical))
}

wsl_promote_bundle <- function(candidate_root, canonical_root, manifest_name, config,
                               fail_before_manifest = FALSE) {
  candidate_manifest_path <- file.path(candidate_root, manifest_name)
  wsl_validate_manifest(candidate_manifest_path, candidate_root, config)
  candidate <- jsonlite::fromJSON(candidate_manifest_path, simplifyVector = FALSE)
  dir.create(canonical_root, recursive = TRUE, showWarnings = FALSE)
  canonical_manifest_path <- file.path(canonical_root, manifest_name)
  if (wsl_is_semantic_noop(candidate_manifest_path, canonical_manifest_path)) {
    return(list(changed = FALSE, promoted = character(), removed = character()))
  }

  promoted <- character()
  created <- character()
  backup_root <- tempfile("winter-storm-level-target-backup-", tmpdir = canonical_root)
  dir.create(backup_root, recursive = TRUE, showWarnings = FALSE)
  on.exit(unlink(backup_root, recursive = TRUE, force = TRUE), add = TRUE)
  backups <- list()
  rollback_targets <- function() {
    if (length(created)) unlink(created, force = TRUE)
    if (length(backups)) {
      for (destination in names(backups)) {
        dir.create(dirname(destination), recursive = TRUE, showWarnings = FALSE)
        file.copy(backups[[destination]], destination, overwrite = TRUE, copy.mode = TRUE)
      }
    }
  }
  for (entry in candidate$targets) {
    relative_path <- wsl_safe_target_path(entry$path)
    source <- file.path(candidate_root, relative_path)
    destination <- file.path(canonical_root, relative_path)
    if (!file.exists(source) || !identical(wsl_sha256(source), entry$sha256)) {
      rollback_targets()
      stop("Candidate target is missing or does not match its checksum: ", relative_path)
    }
    dir.create(dirname(destination), recursive = TRUE, showWarnings = FALSE)
    if (file.exists(destination) && identical(wsl_sha256(destination), entry$sha256)) next
    if (file.exists(destination)) {
      backup <- file.path(backup_root, entry$path)
      dir.create(dirname(backup), recursive = TRUE, showWarnings = FALSE)
      if (!file.copy(destination, backup, overwrite = TRUE, copy.mode = TRUE)) {
        rollback_targets()
        wsl_stop("publication_failed", "Could not back up existing target: ", entry$path)
      }
      backups[[destination]] <- backup
    } else {
      created <- c(created, destination)
    }
    if (!file.copy(source, destination, overwrite = TRUE, copy.mode = TRUE)) {
      rollback_targets()
      wsl_stop("publication_failed", "Could not promote target: ", entry$path)
    }
    promoted <- c(promoted, destination)
  }
  if (isTRUE(fail_before_manifest)) {
    rollback_targets()
    wsl_stop("publication_failed", "Injected failure before manifest promotion.")
  }
  staged_manifest <- paste0(canonical_manifest_path, ".candidate")
  if (!file.copy(candidate_manifest_path, staged_manifest, overwrite = TRUE, copy.mode = TRUE)) {
    rollback_targets()
    wsl_stop("publication_failed", "Could not stage manifest.")
  }
  if (!file.rename(staged_manifest, canonical_manifest_path)) {
    rollback_targets()
    unlink(staged_manifest)
    wsl_stop("publication_failed", "Could not atomically promote manifest.")
  }

  previous_targets <- list.files(canonical_root, pattern = "^winter_storm_levels_.*\\.geojson$",
                                 recursive = TRUE, full.names = TRUE)
  retained_cycles <- unique(vapply(candidate$targets, function(entry) {
    wsl_cycle_token(entry$cycle_time_utc)
  }, character(1)))
  target_cycles <- sub(
    ".*_([0-9]{10})_f[0-9]{3}(?:_[0-9a-f]{12})?\\.geojson$", "\\1",
    basename(previous_targets), perl = TRUE
  )
  recognized <- grepl("^[0-9]{10}$", target_cycles)
  removed <- previous_targets[recognized & !target_cycles %in% retained_cycles]
  removed <- normalizePath(removed, winslash = "/", mustWork = FALSE)
  if (length(removed)) unlink(removed)
  list(changed = TRUE, promoted = promoted, removed = removed)
}
