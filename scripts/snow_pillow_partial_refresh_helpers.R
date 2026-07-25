# Snow-pillow provider refresh helpers.
#
# This file intentionally contains no network calls and no top-level build
# execution.  The production builder sources it, and deterministic tests source
# it directly.

snow_empty_rows <- function() {
  tibble::tibble()
}

snow_utc_now <- function() {
  format(Sys.time(), tz = "UTC", format = "%Y-%m-%dT%H:%M:%SZ")
}

`%||%` <- function(x, y) {
  if (is.null(x) || length(x) == 0L) y else x
}

snow_provider_completeness_defaults <- function() {
  list(
    nrcs_snotel = list(
      station_fraction = 0.90,
      row_fraction = 0.90
    ),
    cdec_snow_sensor = list(
      station_fraction = 0.92,
      row_fraction = 0.85
    )
  )
}

snow_env_fraction <- function(name,
                              default,
                              minimum_allowed = default,
                              raw_value = Sys.getenv(name, unset = "")) {
  default <- suppressWarnings(as.numeric(default))
  minimum_allowed <- suppressWarnings(as.numeric(minimum_allowed))
  if (!is.finite(default) || default <= 0 || default > 1 ||
      !is.finite(minimum_allowed) || minimum_allowed <= 0 ||
      minimum_allowed > 1 || default < minimum_allowed) {
    stop("Invalid internal fraction bounds for ", name, ".", call. = FALSE)
  }

  raw_value <- trimws(as.character(raw_value))
  if (length(raw_value) != 1L || is.na(raw_value) || raw_value == "") {
    return(default)
  }

  value <- suppressWarnings(as.numeric(raw_value))
  if (!is.finite(value) || value <= 0 || value > 1) {
    stop(
      name,
      " must be a finite fraction greater than 0 and at most 1; got '",
      raw_value,
      "'.",
      call. = FALSE
    )
  }
  if (value < minimum_allowed) {
    stop(
      name,
      " may not weaken completeness protection below ",
      minimum_allowed,
      "; got ",
      value,
      ".",
      call. = FALSE
    )
  }

  value
}

snow_env_integer <- function(name,
                             default,
                             minimum_allowed = default,
                             maximum_allowed = Inf,
                             raw_value = Sys.getenv(name, unset = "")) {
  default <- suppressWarnings(as.numeric(default))
  minimum_allowed <- suppressWarnings(as.numeric(minimum_allowed))
  maximum_allowed <- suppressWarnings(as.numeric(maximum_allowed))
  internal_values <- c(default, minimum_allowed, maximum_allowed)
  finite_values <- internal_values[is.finite(internal_values)]
  if (any(finite_values <= 0) ||
      any(finite_values != floor(finite_values)) ||
      default < minimum_allowed ||
      default > maximum_allowed ||
      minimum_allowed > maximum_allowed) {
    stop("Invalid internal integer bounds for ", name, ".", call. = FALSE)
  }

  raw_value <- trimws(as.character(raw_value))
  if (length(raw_value) != 1L || is.na(raw_value) || raw_value == "") {
    return(as.integer(default))
  }

  value <- suppressWarnings(as.numeric(raw_value))
  if (!is.finite(value) || value <= 0 || value != floor(value)) {
    stop(
      name,
      " must be a positive whole number; got '",
      raw_value,
      "'.",
      call. = FALSE
    )
  }
  if (value < minimum_allowed) {
    stop(
      name,
      " may not weaken completeness protection below ",
      minimum_allowed,
      "; got ",
      value,
      ".",
      call. = FALSE
    )
  }
  if (value > maximum_allowed) {
    stop(
      name,
      " is impossible for the indexed stations and fetch window; maximum is ",
      maximum_allowed,
      ", got ",
      value,
      ".",
      call. = FALSE
    )
  }

  as.integer(value)
}

snow_provider_completeness_minimums <- function(provider_id,
                                                indexed_station_count,
                                                expected_fetch_days,
                                                station_fraction,
                                                row_fraction) {
  defaults <- snow_provider_completeness_defaults()
  if (!provider_id %in% names(defaults)) {
    stop("Unknown snow provider completeness policy: ", provider_id, ".", call. = FALSE)
  }

  indexed_station_count <- suppressWarnings(as.numeric(indexed_station_count))
  expected_fetch_days <- suppressWarnings(as.numeric(expected_fetch_days))
  if (!is.finite(indexed_station_count) || indexed_station_count <= 0 ||
      indexed_station_count != floor(indexed_station_count)) {
    stop("Indexed station count must be a positive whole number.", call. = FALSE)
  }
  if (!is.finite(expected_fetch_days) || expected_fetch_days <= 0 ||
      expected_fetch_days != floor(expected_fetch_days)) {
    stop("Expected fetch days must be a positive whole number.", call. = FALSE)
  }

  station_fraction <- snow_env_fraction(
    paste0(provider_id, " station fraction"),
    default = station_fraction,
    minimum_allowed = station_fraction,
    raw_value = ""
  )
  row_fraction <- snow_env_fraction(
    paste0(provider_id, " row fraction"),
    default = row_fraction,
    minimum_allowed = row_fraction,
    raw_value = ""
  )
  expected_station_days <- indexed_station_count * expected_fetch_days

  list(
    provider_id = provider_id,
    indexed_station_count = as.integer(indexed_station_count),
    expected_fetch_days = as.integer(expected_fetch_days),
    expected_station_days = as.integer(expected_station_days),
    station_fraction = station_fraction,
    row_fraction = row_fraction,
    min_sites = as.integer(ceiling(indexed_station_count * station_fraction)),
    min_rows = as.integer(ceiling(expected_station_days * row_fraction))
  )
}

snow_safe_failure_reason <- function(x, max_chars = 300L) {
  x <- as.character(x %||% NA_character_)
  x <- x[!is.na(x) & nzchar(trimws(x))]
  if (length(x) == 0L) {
    return(NA_character_)
  }

  out <- paste(x, collapse = "; ")
  out <- gsub("[\r\n\t]+", " ", out)
  out <- gsub("https?://[^ ]+", "[provider URL]", out)
  out <- gsub("\\s+", " ", trimws(out))
  substr(out, 1L, max_chars)
}

snow_provider_result <- function(provider_id,
                                 success = FALSE,
                                 fetch_status = if (isTRUE(success)) "success" else "failed",
                                 qa_status = if (isTRUE(success)) "passed" else "not_passed",
                                 observations = snow_empty_rows(),
                                 depth_observations = snow_empty_rows(),
                                 latest_rows = snow_empty_rows(),
                                 trace_rows = snow_empty_rows(),
                                 metrics = list(),
                                 qa = list(),
                                 failure_reason = NA_character_,
                                 fetch_started_at_utc = snow_utc_now(),
                                 fetch_completed_at_utc = snow_utc_now(),
                                 result_built_at_utc = snow_utc_now()) {
  list(
    provider_id = as.character(provider_id),
    success = isTRUE(success),
    fetch_status = as.character(fetch_status),
    qa_status = as.character(qa_status),
    observations = observations,
    depth_observations = depth_observations,
    latest_rows = latest_rows,
    trace_rows = trace_rows,
    metrics = metrics,
    qa = qa,
    fresh_row_count = if (is.data.frame(observations)) nrow(observations) else 0L,
    fresh_site_count = if (is.data.frame(observations) &&
        "station_uid" %in% names(observations)) {
      dplyr::n_distinct(observations$station_uid)
    } else {
      0L
    },
    failure_reason = snow_safe_failure_reason(failure_reason),
    carry_forward_required = !isTRUE(success),
    fetch_started_at_utc = as.character(fetch_started_at_utc),
    fetch_completed_at_utc = as.character(fetch_completed_at_utc),
    result_built_at_utc = as.character(result_built_at_utc)
  )
}

snow_validate_provider_observations <- function(observations,
                                                provider_id,
                                                expected_station_uids,
                                                min_rows,
                                                min_sites,
                                                fetch_start_date,
                                                fetch_end_date,
                                                max_valid_swe_in) {
  problems <- character()
  required <- c(
    "station_uid",
    "provider",
    "provider_station_id",
    "obs_date",
    "swe_in",
    "source_element",
    "swe_source_class",
    "swe_source_label",
    "swe_source_note"
  )

  if (!is.data.frame(observations)) {
    observations <- snow_empty_rows()
    problems <- c(problems, "provider observations are not a data frame")
  }

  missing_cols <- setdiff(required, names(observations))
  if (length(missing_cols) > 0L) {
    problems <- c(
      problems,
      paste0("missing required observation fields: ", paste(missing_cols, collapse = ", "))
    )
  }

  if (length(missing_cols) == 0L) {
    station_uid <- trimws(as.character(observations$station_uid))
    provider_station_id <- trimws(as.character(observations$provider_station_id))
    obs_date <- suppressWarnings(as.Date(observations$obs_date))
    swe_in <- suppressWarnings(as.numeric(observations$swe_in))

    if (any(is.na(station_uid) | station_uid == "")) {
      problems <- c(problems, "missing station_uid values")
    }
    if (any(is.na(provider_station_id) | provider_station_id == "")) {
      problems <- c(problems, "missing provider_station_id values")
    }
    required_text <- c(
      "provider",
      "source_element",
      "swe_source_class",
      "swe_source_label",
      "swe_source_note"
    )
    for (field in required_text) {
      value <- trimws(as.character(observations[[field]]))
      if (any(is.na(value) | value == "")) {
        problems <- c(problems, paste0("missing ", field, " values"))
      }
    }
    if (any(!station_uid %in% expected_station_uids)) {
      problems <- c(problems, "observation station identifiers are not in the provider station index")
    }
    if (any(is.na(obs_date))) {
      problems <- c(problems, "invalid observation dates")
    }
    if (any(!is.na(obs_date) & (obs_date < as.Date(fetch_start_date) | obs_date > as.Date(fetch_end_date)))) {
      problems <- c(problems, "observation dates fall outside the requested fetch window")
    }
    if (any(!is.finite(swe_in[!is.na(swe_in)]))) {
      problems <- c(problems, "non-finite SWE values")
    }
    if (any(!is.na(swe_in) & (swe_in < 0 | swe_in > max_valid_swe_in))) {
      problems <- c(problems, "SWE values fall outside accepted bounds")
    }

    duplicate_keys <- duplicated(data.frame(station_uid, obs_date))
    if (any(duplicate_keys)) {
      problems <- c(problems, "duplicate station/date observation keys")
    }
  }

  row_count <- if (is.data.frame(observations)) nrow(observations) else 0L
  site_count <- if (is.data.frame(observations) && "station_uid" %in% names(observations)) {
    dplyr::n_distinct(observations$station_uid[!is.na(observations$station_uid)])
  } else {
    0L
  }
  indexed_station_count <- length(unique(expected_station_uids))
  expected_fetch_days <- max(
    0L,
    as.integer(as.Date(fetch_end_date) - as.Date(fetch_start_date) + 1L)
  )
  expected_station_days <- indexed_station_count * expected_fetch_days
  station_coverage_fraction <- if (indexed_station_count > 0L) {
    site_count / indexed_station_count
  } else {
    0
  }
  row_coverage_fraction <- if (expected_station_days > 0L) {
    row_count / expected_station_days
  } else {
    0
  }

  if (row_count < min_rows) {
    problems <- c(problems, paste0("rows too low: ", row_count, " < ", min_rows))
  }
  if (site_count < min_sites) {
    problems <- c(problems, paste0("sites too low: ", site_count, " < ", min_sites))
  }

  list(
    provider_id = provider_id,
    passed = length(problems) == 0L,
    problems = unique(problems),
    row_count = as.integer(row_count),
    site_count = as.integer(site_count),
    indexed_station_count = as.integer(indexed_station_count),
    expected_fetch_days = as.integer(expected_fetch_days),
    expected_station_days = as.integer(expected_station_days),
    station_coverage_fraction = station_coverage_fraction,
    row_coverage_fraction = row_coverage_fraction,
    min_rows = as.integer(min_rows),
    min_sites = as.integer(min_sites)
  )
}

snow_decide_refresh <- function(provider_results) {
  provider_ids <- names(provider_results)
  if (length(provider_ids) != 2L || any(provider_ids == "")) {
    stop("Snow refresh requires exactly two named provider results.")
  }

  success <- vapply(provider_results, function(x) isTRUE(x$success), logical(1))
  actions <- stats::setNames(
    ifelse(success, "refreshed", "not_published"),
    provider_ids
  )

  if (all(success)) {
    return(list(
      publish = TRUE,
      mode = "full",
      actions = actions,
      successful_provider_ids = provider_ids,
      failed_provider_ids = character(),
      refusal_reason = NA_character_
    ))
  }

  if (sum(success) == 1L) {
    actions[!success] <- "carried_forward"
    return(list(
      publish = TRUE,
      mode = "partial",
      actions = actions,
      successful_provider_ids = provider_ids[success],
      failed_provider_ids = provider_ids[!success],
      refusal_reason = NA_character_
    ))
  }

  reasons <- vapply(
    provider_results,
    function(x) {
      reason <- x$failure_reason %||% NA_character_
      if (length(reason) == 0L || is.na(reason[[1]]) || !nzchar(reason[[1]])) {
        reason <- "provider failed"
      }
      paste0(x$provider_id, ": ", reason[[1]])
    },
    character(1)
  )

  list(
    publish = FALSE,
    mode = "failed",
    actions = actions,
    successful_provider_ids = character(),
    failed_provider_ids = provider_ids,
    refusal_reason = snow_safe_failure_reason(c(
      "both snow providers failed",
      reasons
    ))
  )
}

snow_read_geojson_properties <- function(path) {
  obj <- jsonlite::fromJSON(path, simplifyVector = FALSE)
  if (!is.list(obj) || !identical(obj$type, "FeatureCollection") || !is.list(obj$features)) {
    stop("Latest GeoJSON is not a FeatureCollection: ", path)
  }

  if (length(obj$features) == 0L) {
    stop("Latest GeoJSON has no features: ", path)
  }

  rows <- lapply(obj$features, function(feature) {
    if (!is.list(feature) ||
        !identical(feature$type, "Feature") ||
        !is.list(feature$properties) ||
        !is.list(feature$geometry) ||
        !identical(feature$geometry$type, "Point") ||
        length(feature$geometry$coordinates) != 2L) {
      stop("Latest GeoJSON contains an invalid point feature: ", path)
    }

    props <- feature$properties
    props$.geometry_longitude <- suppressWarnings(as.numeric(feature$geometry$coordinates[[1]]))
    props$.geometry_latitude <- suppressWarnings(as.numeric(feature$geometry$coordinates[[2]]))
    props
  })

  dplyr::bind_rows(rows)
}

snow_read_json_object <- function(path, label) {
  obj <- jsonlite::fromJSON(path, simplifyVector = TRUE)
  if (!is.list(obj) || is.data.frame(obj)) {
    stop(label, " is not a JSON object: ", path)
  }
  obj
}

snow_validate_prior_outputs <- function(paths,
                                        stations,
                                        current_water_year,
                                        max_valid_swe_in,
                                        required_latest_columns,
                                        required_trace_columns) {
  required_path_names <- c("latest_geojson", "latest_summary", "trace_csv", "trace_summary")
  problems <- character()

  if (!all(required_path_names %in% names(paths))) {
    return(list(
      valid = FALSE,
      problems = paste0(
        "prior output path map is missing: ",
        paste(setdiff(required_path_names, names(paths)), collapse = ", ")
      )
    ))
  }

  missing_files <- unname(paths[!file.exists(unname(paths))])
  if (length(missing_files) > 0L) {
    return(list(
      valid = FALSE,
      problems = paste0("required prior output is missing: ", paste(missing_files, collapse = ", "))
    ))
  }

  parsed <- tryCatch(
    list(
      latest = snow_read_geojson_properties(paths[["latest_geojson"]]),
      latest_summary = snow_read_json_object(paths[["latest_summary"]], "Latest summary"),
      trace = readr::read_csv(paths[["trace_csv"]], show_col_types = FALSE),
      trace_summary = snow_read_json_object(paths[["trace_summary"]], "Trace summary")
    ),
    error = function(e) e
  )

  if (inherits(parsed, "error")) {
    return(list(
      valid = FALSE,
      problems = snow_safe_failure_reason(paste0("prior output parsing failed: ", conditionMessage(parsed)))
    ))
  }

  latest <- parsed$latest
  trace <- parsed$trace

  missing_latest <- setdiff(required_latest_columns, names(latest))
  missing_trace <- setdiff(required_trace_columns, names(trace))
  if (length(missing_latest) > 0L) {
    problems <- c(
      problems,
      paste0("prior latest rows are missing fields: ", paste(missing_latest, collapse = ", "))
    )
  }
  if (length(missing_trace) > 0L) {
    problems <- c(
      problems,
      paste0("prior trace rows are missing fields: ", paste(missing_trace, collapse = ", "))
    )
  }

  if (length(missing_latest) == 0L) {
    latest_station_uid <- as.character(latest$station_uid)
    latest_provider_key <- as.character(latest$live_provider_key)
    latest_provider_station_id <- as.character(latest$provider_station_id)
    station_match <- match(latest_station_uid, as.character(stations$station_uid))

    if (nrow(latest) != nrow(stations) ||
        anyDuplicated(latest_station_uid) ||
        !setequal(latest_station_uid, as.character(stations$station_uid))) {
      problems <- c(problems, "prior latest rows do not exactly match the current station index")
    }
    if (any(is.na(station_match))) {
      problems <- c(problems, "prior latest rows contain unknown station_uid values")
    } else {
      if (any(latest_provider_key != as.character(stations$live_provider_key[station_match]))) {
        problems <- c(problems, "prior latest provider identity does not match the station index")
      }
      if (any(as.character(latest$provider) != as.character(stations$provider[station_match]))) {
        problems <- c(problems, "prior latest provider labels do not match the station index")
      }
      if (any(latest_provider_station_id != as.character(stations$provider_station_id[station_match]))) {
        problems <- c(problems, "prior latest provider station IDs do not match the station index")
      }
    }

    lon <- suppressWarnings(as.numeric(latest$longitude))
    lat <- suppressWarnings(as.numeric(latest$latitude))
    geom_lon <- suppressWarnings(as.numeric(latest$.geometry_longitude))
    geom_lat <- suppressWarnings(as.numeric(latest$.geometry_latitude))
    if (any(!is.finite(lon)) || any(!is.finite(lat)) ||
        any(lon < -180 | lon > 180) || any(lat < -90 | lat > 90)) {
      problems <- c(problems, "prior latest coordinates are missing, non-finite, or out of bounds")
    }
    if (any(abs(lon - geom_lon) > 1e-7) || any(abs(lat - geom_lat) > 1e-7)) {
      problems <- c(problems, "prior latest geometry does not match coordinate properties")
    }

    swe <- suppressWarnings(as.numeric(latest$latest_swe_in))
    if (any(!is.finite(swe[!is.na(swe)])) ||
        any(!is.na(swe) & (swe < 0 | swe > max_valid_swe_in))) {
      problems <- c(problems, "prior latest SWE values are invalid")
    }

    latest_dates <- as.character(latest$latest_swe_date_local)
    latest_dates <- latest_dates[!is.na(latest_dates) & latest_dates != ""]
    if (length(latest_dates) > 0L && any(is.na(suppressWarnings(as.Date(latest_dates))))) {
      problems <- c(problems, "prior latest SWE dates are invalid")
    }
    if (any(suppressWarnings(as.integer(latest$current_water_year)) != current_water_year)) {
      problems <- c(problems, "prior latest rows are not for the current water year")
    }
  }

  if (length(missing_trace) == 0L) {
    trace_station_uid <- as.character(trace$station_uid)
    trace_provider_key <- as.character(trace$live_provider_key)
    trace_provider_station_id <- as.character(trace$provider_station_id)
    trace_match <- match(trace_station_uid, as.character(stations$station_uid))
    trace_date <- suppressWarnings(as.Date(trace$obs_date_local))
    trace_wy <- suppressWarnings(as.integer(trace$water_year))
    trace_water_day <- suppressWarnings(as.integer(trace$water_day))
    trace_swe <- suppressWarnings(as.numeric(trace$swe_in))
    expected_wy_start <- as.Date(sprintf("%d-10-01", current_water_year - 1L))
    expected_water_day <- as.integer(trace_date - expected_wy_start + 1L)

    if (nrow(trace) == 0L) {
      problems <- c(problems, "prior trace has no rows")
    }
    if (any(is.na(trace_match))) {
      problems <- c(problems, "prior trace contains unknown station_uid values")
    } else {
      if (any(trace_provider_key != as.character(stations$live_provider_key[trace_match]))) {
        problems <- c(problems, "prior trace provider identity does not match the station index")
      }
      if (any(as.character(trace$provider) != as.character(stations$provider[trace_match]))) {
        problems <- c(problems, "prior trace provider labels do not match the station index")
      }
      if (any(trace_provider_station_id != as.character(stations$provider_station_id[trace_match]))) {
        problems <- c(problems, "prior trace provider station IDs do not match the station index")
      }
    }
    if (any(is.na(trace_date)) ||
        any(is.na(trace_wy)) ||
        any(trace_wy != current_water_year) ||
        any(is.na(trace_water_day)) ||
        any(trace_water_day != expected_water_day)) {
      problems <- c(problems, "prior trace dates, water year, or water day are invalid")
    }
    if (any(!is.finite(trace_swe[!is.na(trace_swe)])) ||
        any(is.na(trace_swe)) ||
        any(trace_swe < 0 | trace_swe > max_valid_swe_in)) {
      problems <- c(problems, "prior trace SWE values are invalid")
    }
    required_trace_text <- c(
      "station_uid",
      "live_provider_key",
      "provider",
      "provider_station_id",
      "station_name",
      "obs_date_local",
      "source_element",
      "swe_source_class",
      "swe_source_label",
      "swe_source_note"
    )
    for (field in required_trace_text) {
      value <- trimws(as.character(trace[[field]]))
      if (any(is.na(value) | value == "")) {
        problems <- c(problems, paste0("prior trace has missing ", field, " values"))
      }
    }

    trace_key <- data.frame(
      trace_provider_key,
      trace_station_uid,
      trace_wy,
      trace_water_day,
      stringsAsFactors = FALSE
    )
    if (anyDuplicated(trace_key)) {
      problems <- c(problems, "prior trace has duplicate provider/station/water-day keys")
    }
  }

  latest_summary_required <- c(
    "layer",
    "build_time_local",
    "build_date_local",
    "current_water_year",
    "latest_geojson_rows",
    "current_wy_trace_rows"
  )
  trace_summary_required <- c(
    "layer",
    "build_time_local",
    "build_date_local",
    "current_water_year",
    "current_wy_trace_rows",
    "stations_with_trace_rows",
    "providers"
  )

  if (!all(latest_summary_required %in% names(parsed$latest_summary))) {
    problems <- c(problems, "prior latest summary is missing required fields")
  } else {
    if (as.integer(parsed$latest_summary$current_water_year) != current_water_year ||
        as.integer(parsed$latest_summary$latest_geojson_rows) != nrow(latest) ||
        as.integer(parsed$latest_summary$current_wy_trace_rows) != nrow(trace)) {
      problems <- c(problems, "prior latest summary counts or water year do not match prior row products")
    }
  }

  if (!all(trace_summary_required %in% names(parsed$trace_summary))) {
    problems <- c(problems, "prior trace summary is missing required fields")
  } else {
    if (as.integer(parsed$trace_summary$current_water_year) != current_water_year ||
        as.integer(parsed$trace_summary$current_wy_trace_rows) != nrow(trace) ||
        as.integer(parsed$trace_summary$stations_with_trace_rows) !=
          dplyr::n_distinct(trace$station_uid)) {
      problems <- c(problems, "prior trace summary counts or water year do not match prior trace")
    }
  }

  latest <- latest |>
    dplyr::select(-dplyr::any_of(c(".geometry_longitude", ".geometry_latitude")))

  list(
    valid = length(problems) == 0L,
    problems = unique(problems),
    latest = latest,
    latest_summary = parsed$latest_summary,
    trace = trace,
    trace_summary = parsed$trace_summary
  )
}

snow_validate_combined_outputs <- function(latest,
                                           trace,
                                           stations,
                                           current_water_year,
                                           max_valid_swe_in,
                                           min_trace_rows,
                                           min_latest_swe_rows,
                                           required_latest_columns,
                                           required_trace_columns) {
  problems <- character()
  missing_latest <- setdiff(required_latest_columns, names(latest))
  missing_trace <- setdiff(required_trace_columns, names(trace))

  if (length(missing_latest) > 0L) {
    problems <- c(
      problems,
      paste0("combined latest is missing fields: ", paste(missing_latest, collapse = ", "))
    )
  }
  if (length(missing_trace) > 0L) {
    problems <- c(
      problems,
      paste0("combined trace is missing fields: ", paste(missing_trace, collapse = ", "))
    )
  }

  if (length(missing_latest) == 0L) {
    latest_uid <- as.character(latest$station_uid)
    latest_key <- data.frame(
      as.character(latest$live_provider_key),
      latest_uid,
      stringsAsFactors = FALSE
    )
    latest_match <- match(latest_uid, as.character(stations$station_uid))

    if (nrow(latest) != nrow(stations) ||
        anyDuplicated(latest_key) ||
        !setequal(latest_uid, as.character(stations$station_uid))) {
      problems <- c(problems, "combined latest does not contain exactly one row per indexed station")
    }
    if (any(is.na(latest_match)) ||
        any(as.character(latest$live_provider_key) !=
              as.character(stations$live_provider_key[latest_match])) ||
        any(as.character(latest$provider) !=
              as.character(stations$provider[latest_match])) ||
        any(as.character(latest$provider_station_id) !=
              as.character(stations$provider_station_id[latest_match]))) {
      problems <- c(problems, "combined latest provider identity is invalid")
    }

    lon <- suppressWarnings(as.numeric(latest$longitude))
    lat <- suppressWarnings(as.numeric(latest$latitude))
    if (any(!is.finite(lon)) || any(!is.finite(lat)) ||
        any(lon < -180 | lon > 180) || any(lat < -90 | lat > 90)) {
      problems <- c(problems, "combined latest coordinates are invalid")
    }

    latest_swe <- suppressWarnings(as.numeric(latest$latest_swe_in))
    if (any(!is.finite(latest_swe[!is.na(latest_swe)])) ||
        any(!is.na(latest_swe) & (latest_swe < 0 | latest_swe > max_valid_swe_in))) {
      problems <- c(problems, "combined latest SWE values are invalid")
    }
    if (sum(!is.na(latest_swe)) < min_latest_swe_rows) {
      problems <- c(
        problems,
        paste0(
          "combined rows with latest SWE too low: ",
          sum(!is.na(latest_swe)),
          " < ",
          min_latest_swe_rows
        )
      )
    }
    latest_dates <- as.character(latest$latest_swe_date_local)
    latest_dates <- latest_dates[!is.na(latest_dates) & latest_dates != ""]
    if (length(latest_dates) > 0L && any(is.na(suppressWarnings(as.Date(latest_dates))))) {
      problems <- c(problems, "combined latest SWE dates are invalid")
    }
    if (any(suppressWarnings(as.integer(latest$current_water_year)) != current_water_year)) {
      problems <- c(problems, "combined latest rows are not for the current water year")
    }
  }

  if (length(missing_trace) == 0L) {
    trace_uid <- as.character(trace$station_uid)
    trace_provider <- as.character(trace$live_provider_key)
    trace_wy <- suppressWarnings(as.integer(trace$water_year))
    trace_day <- suppressWarnings(as.integer(trace$water_day))
    trace_date <- suppressWarnings(as.Date(trace$obs_date_local))
    trace_swe <- suppressWarnings(as.numeric(trace$swe_in))
    trace_match <- match(trace_uid, as.character(stations$station_uid))
    trace_key <- data.frame(trace_provider, trace_uid, trace_wy, trace_day)
    wy_start <- as.Date(sprintf("%d-10-01", current_water_year - 1L))

    if (nrow(trace) < min_trace_rows) {
      problems <- c(
        problems,
        paste0("combined trace rows too low: ", nrow(trace), " < ", min_trace_rows)
      )
    }
    if (anyDuplicated(trace_key)) {
      problems <- c(problems, "combined trace has duplicate provider/station/water-day keys")
    }
    if (any(is.na(trace_match)) ||
        any(trace_provider != as.character(stations$live_provider_key[trace_match])) ||
        any(as.character(trace$provider) != as.character(stations$provider[trace_match])) ||
        any(as.character(trace$provider_station_id) !=
              as.character(stations$provider_station_id[trace_match]))) {
      problems <- c(problems, "combined trace provider identity is invalid")
    }
    if (any(is.na(trace_wy)) ||
        any(trace_wy != current_water_year) ||
        any(is.na(trace_date)) ||
        any(is.na(trace_day)) ||
        any(trace_day != as.integer(trace_date - wy_start + 1L))) {
      problems <- c(problems, "combined trace dates, water year, or water day are invalid")
    }
    if (any(is.na(trace_swe)) ||
        any(!is.finite(trace_swe)) ||
        any(trace_swe < 0 | trace_swe > max_valid_swe_in)) {
      problems <- c(problems, "combined trace SWE values are invalid")
    }
    if (!setequal(
      sort(unique(trace_provider)),
      sort(unique(as.character(stations$live_provider_key)))
    )) {
      problems <- c(problems, "combined trace does not contain both provider families")
    }
    required_trace_text <- c(
      "station_uid",
      "live_provider_key",
      "provider",
      "provider_station_id",
      "station_name",
      "obs_date_local",
      "source_element",
      "swe_source_class",
      "swe_source_label",
      "swe_source_note"
    )
    for (field in required_trace_text) {
      value <- trimws(as.character(trace[[field]]))
      if (any(is.na(value) | value == "")) {
        problems <- c(problems, paste0("combined trace has missing ", field, " values"))
      }
    }
  }

  list(
    passed = length(problems) == 0L,
    problems = unique(problems),
    latest_rows = nrow(latest),
    trace_rows = nrow(trace),
    latest_sites = if ("station_uid" %in% names(latest)) dplyr::n_distinct(latest$station_uid) else 0L,
    trace_sites = if ("station_uid" %in% names(trace)) dplyr::n_distinct(trace$station_uid) else 0L
  )
}

snow_resolve_publication <- function(provider_results,
                                     prior_outputs,
                                     stations,
                                     current_water_year,
                                     max_valid_swe_in,
                                     min_trace_rows,
                                     min_latest_swe_rows,
                                     required_latest_columns,
                                     required_trace_columns) {
  decision <- snow_decide_refresh(provider_results)
  if (!isTRUE(decision$publish)) {
    return(list(
      publish = FALSE,
      decision = decision,
      refusal_reason = decision$refusal_reason
    ))
  }

  if (identical(decision$mode, "partial")) {
    if (is.null(prior_outputs) || !isTRUE(prior_outputs$valid)) {
      prior_reason <- if (is.null(prior_outputs)) {
        "prior output set was not loaded"
      } else {
        paste(prior_outputs$problems, collapse = "; ")
      }
      return(list(
        publish = FALSE,
        decision = decision,
        refusal_reason = snow_safe_failure_reason(paste0(
          "partial refresh refused because prior outputs are invalid: ",
          prior_reason
        ))
      ))
    }
  }

  latest_pieces <- list()
  trace_pieces <- list()
  carry_counts <- list()

  for (provider_id in names(provider_results)) {
    result <- provider_results[[provider_id]]
    action <- decision$actions[[provider_id]]

    if (identical(action, "refreshed")) {
      latest_piece <- result$latest_rows
      trace_piece <- result$trace_rows
    } else {
      latest_piece <- prior_outputs$latest |>
        dplyr::filter(.data$live_provider_key == provider_id)
      trace_piece <- prior_outputs$trace |>
        dplyr::filter(.data$live_provider_key == provider_id)

      expected_uids <- stations |>
        dplyr::filter(.data$live_provider_key == provider_id) |>
        dplyr::pull(.data$station_uid)

      if (nrow(latest_piece) == 0L ||
          nrow(trace_piece) == 0L ||
          !setequal(as.character(latest_piece$station_uid), as.character(expected_uids))) {
        return(list(
          publish = FALSE,
          decision = decision,
          refusal_reason = paste0(
            "partial refresh refused because valid prior rows are unavailable for ",
            provider_id
          )
        ))
      }

      min_prior_sites <- as.integer(result$qa$min_sites %||% 1L)
      min_prior_rows <- as.integer(result$qa$min_rows %||% 1L)
      prior_sites <- dplyr::n_distinct(trace_piece$station_uid)
      if (nrow(trace_piece) < min_prior_rows || prior_sites < min_prior_sites) {
        return(list(
          publish = FALSE,
          decision = decision,
          refusal_reason = paste0(
            "partial refresh refused because prior trace coverage is too low for ",
            provider_id,
            ": rows ",
            nrow(trace_piece),
            " < ",
            min_prior_rows,
            " or sites ",
            prior_sites,
            " < ",
            min_prior_sites
          )
        ))
      }
    }

    latest_pieces[[provider_id]] <- latest_piece
    trace_pieces[[provider_id]] <- trace_piece
    carry_counts[[provider_id]] <- list(
      latest_rows = if (identical(action, "carried_forward")) nrow(latest_piece) else 0L,
      trace_rows = if (identical(action, "carried_forward")) nrow(trace_piece) else 0L,
      sites = if (identical(action, "carried_forward")) {
        dplyr::n_distinct(trace_piece$station_uid)
      } else {
        0L
      }
    )
  }

  latest <- dplyr::bind_rows(latest_pieces) |>
    dplyr::arrange(
      .data$live_provider_key,
      .data$station_name,
      .data$provider_station_id
    )
  trace <- dplyr::bind_rows(trace_pieces) |>
    dplyr::arrange(.data$station_uid, .data$obs_date_local)

  combined_qa <- snow_validate_combined_outputs(
    latest = latest,
    trace = trace,
    stations = stations,
    current_water_year = current_water_year,
    max_valid_swe_in = max_valid_swe_in,
    min_trace_rows = min_trace_rows,
    min_latest_swe_rows = min_latest_swe_rows,
    required_latest_columns = required_latest_columns,
    required_trace_columns = required_trace_columns
  )

  if (!isTRUE(combined_qa$passed)) {
    return(list(
      publish = FALSE,
      decision = decision,
      combined_qa = combined_qa,
      refusal_reason = snow_safe_failure_reason(paste0(
        "combined output QA failed: ",
        paste(combined_qa$problems, collapse = "; ")
      ))
    ))
  }

  list(
    publish = TRUE,
    decision = decision,
    latest = latest,
    trace = trace,
    carry_counts = carry_counts,
    combined_qa = combined_qa,
    refusal_reason = NA_character_
  )
}

snow_build_refresh_metadata <- function(provider_results, resolved, build_time_utc) {
  providers <- list()

  for (provider_id in names(provider_results)) {
    result <- provider_results[[provider_id]]
    action <- resolved$decision$actions[[provider_id]]
    carried <- resolved$carry_counts[[provider_id]] %||% list(
      latest_rows = 0L,
      trace_rows = 0L,
      sites = 0L
    )

    providers[[provider_id]] <- list(
      fetch_status = result$fetch_status,
      qa_status = result$qa_status,
      publication_action = action,
      fetch_started_at_utc = result$fetch_started_at_utc,
      fetch_completed_at_utc = result$fetch_completed_at_utc,
      result_built_at_utc = result$result_built_at_utc,
      fresh_latest_rows = nrow(result$latest_rows),
      fresh_trace_rows = nrow(result$trace_rows),
      fresh_site_count = if ("station_uid" %in% names(result$trace_rows)) {
        dplyr::n_distinct(result$trace_rows$station_uid)
      } else {
        0L
      },
      carried_forward_latest_rows = as.integer(carried$latest_rows),
      carried_forward_trace_rows = as.integer(carried$trace_rows),
      carried_forward_site_count = as.integer(carried$sites),
      last_successful_data_preserved = identical(action, "carried_forward"),
      failure_reason = if (isTRUE(result$success)) NULL else result$failure_reason
    )
  }

  list(
    mode = resolved$decision$mode,
    build_time_utc = as.character(build_time_utc),
    last_known_good_provider_data_preserved = identical(resolved$decision$mode, "partial"),
    providers = providers
  )
}

snow_stage_and_promote_outputs <- function(final_paths,
                                           write_staged,
                                           validate_staged) {
  required_names <- c("latest_geojson", "latest_summary", "trace_csv", "trace_summary")
  if (!all(required_names %in% names(final_paths))) {
    stop("Final snow output path map is incomplete.")
  }

  output_dirs <- unique(dirname(unname(final_paths[required_names])))
  if (length(output_dirs) != 1L) {
    stop("All snow outputs must share one output directory.")
  }

  staging_dir <- tempfile(pattern = ".snow-pillow-staging-", tmpdir = output_dirs[[1]])
  if (!dir.create(staging_dir, recursive = FALSE, showWarnings = FALSE)) {
    stop("Could not create snow output staging directory.")
  }
  on.exit(unlink(staging_dir, recursive = TRUE, force = TRUE), add = TRUE)

  staged_paths <- stats::setNames(
    file.path(staging_dir, basename(unname(final_paths[required_names]))),
    required_names
  )

  write_staged(staged_paths)

  missing_staged <- unname(staged_paths[!file.exists(unname(staged_paths))])
  if (length(missing_staged) > 0L) {
    stop("Not all staged snow outputs were constructed.")
  }

  validation <- validate_staged(staged_paths)
  if (!isTRUE(validation$valid)) {
    stop(
      "Staged snow output validation failed: ",
      paste(validation$problems, collapse = "; ")
    )
  }

  backup_dir <- file.path(staging_dir, "backups")
  if (!dir.create(backup_dir, recursive = FALSE, showWarnings = FALSE)) {
    stop("Could not create snow output backup directory.")
  }
  backup_paths <- stats::setNames(
    file.path(backup_dir, basename(unname(final_paths[required_names]))),
    required_names
  )

  for (name in required_names) {
    final_path <- final_paths[[name]]
    if (!file.exists(final_path) ||
        !file.copy(final_path, backup_paths[[name]], overwrite = FALSE, copy.mode = TRUE)) {
      stop("Could not preserve prior snow output before replacement: ", final_path)
    }
  }

  promoted <- character()
  promotion_error <- NULL

  for (name in required_names) {
    ok <- tryCatch(
      file.rename(staged_paths[[name]], final_paths[[name]]),
      warning = function(w) FALSE,
      error = function(e) FALSE
    )
    if (!isTRUE(ok)) {
      promotion_error <- paste0("Could not replace snow output: ", final_paths[[name]])
      break
    }
    promoted <- c(promoted, name)
  }

  if (!is.null(promotion_error)) {
    rollback_failures <- character()
    for (name in promoted) {
      if (!file.copy(
        backup_paths[[name]],
        final_paths[[name]],
        overwrite = TRUE,
        copy.mode = TRUE
      )) {
        rollback_failures <- c(rollback_failures, final_paths[[name]])
      }
    }

    if (length(rollback_failures) > 0L) {
      stop(
        promotion_error,
        "; rollback also failed for: ",
        paste(rollback_failures, collapse = ", ")
      )
    }
    stop(promotion_error, "; prior outputs restored.")
  }

  invisible(list(
    paths = final_paths[required_names],
    validation = validation
  ))
}
