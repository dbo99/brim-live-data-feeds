# CoCoRaHS product-specific handling for retained observations whose upstream
# station name cannot satisfy the public feature contract.

COCORAHS_MISSING_STATION_NAME_THRESHOLD <- 10L

cocorahs_station_name_value <- function(value) {
  if (is.null(value) || length(value) != 1L || is.na(value)) {
    return(NA_character_)
  }

  value <- trimws(as.character(value))
  if (!nzchar(value) || toupper(value) %in% c("NA", "NULL", "NAN")) {
    return(NA_character_)
  }

  value
}

cocorahs_column_values <- function(df, candidates) {
  if (nrow(df) == 0L) return(character())

  column_index <- match(tolower(candidates), tolower(names(df)), nomatch = 0L)
  column_index <- column_index[column_index > 0L]
  if (length(column_index) == 0L) return(rep(NA_character_, nrow(df)))

  values <- df[[column_index[[1L]]]]
  vapply(seq_len(nrow(df)), function(index) {
    value <- if (is.list(values)) values[[index]] else values[index]
    cocorahs_station_name_value(value)
  }, character(1))
}

cocorahs_identifier_value <- function(value) {
  value <- cocorahs_station_name_value(value)
  if (is.na(value)) return(NA_character_)

  value <- gsub("[^[:alnum:].:_-]", "_", value)
  substr(value, 1L, 80L)
}

cocorahs_omitted_identifier <- function(df, index) {
  station_number <- cocorahs_column_values(
    df,
    c("stationNumber", "StationNumber", "station_number")
  )[index]
  station_number <- cocorahs_identifier_value(station_number)
  if (!is.na(station_number)) return(paste0("stationNumber=", station_number))

  observation_id <- cocorahs_column_values(
    df,
    c("id", "Id", "dailyPrecipReportID", "DailyPrecipReportID", "uid", "Uid")
  )[index]
  observation_id <- cocorahs_identifier_value(observation_id)
  if (!is.na(observation_id)) return(paste0("observationId=", observation_id))

  paste0("retainedRow=", index)
}

cocorahs_apply_station_name_policy <- function(
  df,
  threshold = COCORAHS_MISSING_STATION_NAME_THRESHOLD
) {
  if (length(threshold) != 1L || is.na(threshold) || threshold < 0L) {
    stop("CoCoRaHS missing-station-name threshold must be a non-negative scalar.")
  }
  threshold <- as.integer(threshold)

  retained_before <- nrow(df)
  station_names <- cocorahs_column_values(
    df,
    c("stationName", "StationName", "station_name")
  )
  omitted_rows <- which(is.na(station_names))
  omitted_count <- length(omitted_rows)
  retained_after <- retained_before - omitted_count
  identifier_rows <- head(omitted_rows, threshold)
  omitted_identifiers <- vapply(
    identifier_rows,
    function(index) cocorahs_omitted_identifier(df, index),
    character(1)
  )

  message(
    "CoCoRaHS station-name policy: retained observations before filtering=",
    retained_before,
    "; omitted_missing_station_name=", omitted_count,
    "; retained observations after filtering=", retained_after,
    "; threshold=", threshold
  )

  if (omitted_count > 0L) {
    message(
      "CoCoRaHS omitted missing-station-name identifiers (maximum ", threshold,
      "): ", paste(omitted_identifiers, collapse = ", ")
    )
  }

  if (omitted_count > threshold) {
    stop(
      "CoCoRaHS missing-station-name threshold exceeded: ",
      "omitted_missing_station_name=", omitted_count,
      "; threshold=", threshold,
      "; failing closed before candidate construction.",
      call. = FALSE
    )
  }

  retained_rows <- if (omitted_count == 0L) {
    df
  } else {
    df[-omitted_rows, , drop = FALSE]
  }

  list(
    rows = retained_rows,
    retained_before = as.integer(retained_before),
    omitted_count = as.integer(omitted_count),
    retained_after = as.integer(retained_after),
    threshold = threshold,
    omitted_identifiers = unname(omitted_identifiers)
  )
}
