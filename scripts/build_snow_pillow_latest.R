# ==== build_snow_pillow_latest.R ============================================
## SWE018
##   - Harden provider fetches with curl-based retries, AWDB chunk fallback,
##     CDEC direct-JSON fallback, and pre-write QA guardrails so partial
##     provider outages fail before publishing degraded feed files.
##
## SWE008a
##   - Trim unused top-level dependency tidyr from script; keep CDEC querying
##     through sharpshootR because the GitHub Action runtime is acceptable.
##
## SWE008
##   - Add 7-day SWE delta for future recent-change map mode.
##   - Ensure both 3-day and 7-day deltas are carried into the latest GeoJSON.
##   - Retain SWE004b negative-SWE cleanup.
##
## SWE007
##   - Add 3-day/72-hour SWE delta field for future recent-change map mode.
##   - Retain SWE004b negative-SWE cleanup.
##
## SWE004b
##   - Treat negative provider SWE values as invalid/missing so summer or
##     sensor-artifact values do not become "unknown" latest statuses.
##   - Add summary QA counts for invalid negative SWE rows that were excluded
##     from the latest/trace products.
##   - Retain SWE004a join-name and tidyselect deprecation fixes.
##
## PURPOSE:
##   Build the first browser-facing BRIM Ops Live snow-pillow / SWE feed files.
##
## DESIGN:
##   - Run from the local `brim-live-data-feeds` repo.
##   - Read the clean station index exported by BRIM:
##       data/input/snow_pillow_station_index.csv
##   - Fetch current-water-year daily SWE values for:
##       * USDA NRCS / SNOTEL via AWDB REST element WTEQ
##       * CA DWR / CDEC snow sensors via sharpshootR::CDECquery(sensor = 3)
##   - Write small browser-facing files under:
##       docs/data/
##
## IMPORTANT DATA RULES:
##   - Do not assume missing SWE means zero.
##   - A true zero is reported only when the provider returns a numeric 0.
##   - Missing/stale/no-valid-current-WY values are carried as explicit status
##     classes so summer CDEC/SNOTEL behavior is honest rather than implied.
##   - Dates are daily observation dates and are displayed as friendly local
##     dates. No raw UTC/Z timestamps are shown in browser-facing fields.
##
## OUTPUTS:
##   docs/data/snow_pillow_latest.geojson
##   docs/data/snow_pillow_latest_summary.json
##   docs/data/snow_pillow_current_wy_trace.csv
##   docs/data/snow_pillow_current_wy_trace_summary.json
## ============================================================================

# ==== 1. Repo sanity check ===================================================

if (!dir.exists("data/input") || !dir.exists("docs")) {
  stop(
    "Run this script from the brim-live-data-feeds repository root.\n",
    "Expected folders data/input/ and docs/ were not found."
  )
}

# ==== 2. Packages ============================================================

required_pkgs <- c(
  "dplyr",
  "purrr",
  "readr",
  "tibble",
  "stringr",
  "lubridate",
  "jsonlite",
  "curl",
  "sharpshootR"
)

missing_pkgs <- required_pkgs[!vapply(required_pkgs, requireNamespace, logical(1), quietly = TRUE)]

if (length(missing_pkgs) > 0) {
  stop(
    "Missing required R packages: ", paste(missing_pkgs, collapse = ", "),
    "\nInstall them before running build_snow_pillow_latest.R."
  )
}

suppressPackageStartupMessages({
  library(dplyr)
  library(purrr)
  library(readr)
  library(tibble)
  library(stringr)
  library(lubridate)
  library(jsonlite)
  library(curl)
  library(sharpshootR)
})

# ==== 3. Paths and user-facing switches =====================================

STATION_INDEX_CSV <- file.path("data", "input", "snow_pillow_station_index.csv")

OUT_DIR <- file.path("docs", "data")
dir.create(OUT_DIR, showWarnings = FALSE, recursive = TRUE)

OUT_LATEST_GEOJSON <- file.path(OUT_DIR, "snow_pillow_latest.geojson")
OUT_LATEST_SUMMARY <- file.path(OUT_DIR, "snow_pillow_latest_summary.json")
OUT_WY_TRACE_CSV   <- file.path(OUT_DIR, "snow_pillow_current_wy_trace.csv")
OUT_WY_TRACE_SUMRY <- file.path(OUT_DIR, "snow_pillow_current_wy_trace_summary.json")

## Local browser-display time zone. Observation values are daily, but feed
## build-time labels should still be user-friendly for BRIM users.
LOCAL_TZ <- "America/Los_Angeles"

## Current-water-year fetch window. This supports the popup trace and lets the
## latest-status logic distinguish true reported zeros from missing summer data.
TODAY_LOCAL <- as.Date(lubridate::with_tz(Sys.time(), LOCAL_TZ))
CURRENT_WY <- {
  yr <- as.integer(format(TODAY_LOCAL, "%Y"))
  mo <- as.integer(format(TODAY_LOCAL, "%m"))
  if (mo >= 10L) yr + 1L else yr
}
DEFAULT_WY_START <- as.Date(sprintf("%d-10-01", CURRENT_WY - 1L))

FETCH_START_DATE <- as.Date(Sys.getenv("SNOW_PILLOW_FETCH_START_DATE", unset = as.character(DEFAULT_WY_START)))
FETCH_END_DATE   <- as.Date(Sys.getenv("SNOW_PILLOW_FETCH_END_DATE", unset = as.character(TODAY_LOCAL)))

## Optional development limit. Keep unset for the real feed.
STATION_LIMIT <- suppressWarnings(as.integer(Sys.getenv("SNOW_PILLOW_STATION_LIMIT", unset = NA_character_)))

## Provider request controls.
REQUEST_PAUSE_SEC <- suppressWarnings(as.numeric(Sys.getenv("SNOW_PILLOW_REQUEST_PAUSE_SEC", unset = "0.08")))
if (is.na(REQUEST_PAUSE_SEC) || REQUEST_PAUSE_SEC < 0) REQUEST_PAUSE_SEC <- 0.08

AWDB_CHUNK_SIZE <- suppressWarnings(as.integer(Sys.getenv("SNOW_PILLOW_AWDB_CHUNK_SIZE", unset = "40")))
if (is.na(AWDB_CHUNK_SIZE) || AWDB_CHUNK_SIZE < 1) AWDB_CHUNK_SIZE <- 40L

## Daily snow observations that are older than this threshold remain visible but
## are flagged as stale. Missing is never converted to zero.
STALE_DAYS <- suppressWarnings(as.integer(Sys.getenv("SNOW_PILLOW_STALE_DAYS", unset = "7")))
if (is.na(STALE_DAYS) || STALE_DAYS < 1) STALE_DAYS <- 7L

VERY_STALE_DAYS <- suppressWarnings(as.integer(Sys.getenv("SNOW_PILLOW_VERY_STALE_DAYS", unset = "21")))
if (is.na(VERY_STALE_DAYS) || VERY_STALE_DAYS < STALE_DAYS) VERY_STALE_DAYS <- max(21L, STALE_DAYS)

## CDEC sensor 3 is snow water content / SWE in inches in the existing BRIM
## prototype workflow.
CDEC_SWE_SENSOR <- 3L
CDEC_DAILY_INTERVAL <- "D"

# ==== 4. Small helpers =======================================================

pt_chr <- function(x) {
  x <- as.character(x)
  x <- trimws(x)
  x[x == "" | is.na(x) | toupper(x) %in% c("NA", "NULL", "NAN")] <- NA_character_
  x
}

pt_num <- function(x) {
  suppressWarnings(as.numeric(gsub(",", "", as.character(x))))
}

pt_swe_num <- function(x) {
  ## SWE should not be negative.  Some provider feeds can emit negative daily
  ## values during melt-out, maintenance, or sensor/QC edge cases.  Keep those
  ## values out of the browser-facing latest/trace products rather than letting
  ## them create ambiguous "unknown" status classes.
  v <- pt_num(x)
  v[!is.na(v) & v < 0] <- NA_real_
  v
}

pt_negative_swe_count <- function(x) {
  v <- pt_num(x)
  sum(!is.na(v) & v < 0, na.rm = TRUE)
}

pt_date <- function(x) {
  if (inherits(x, "Date")) return(x)
  if (inherits(x, "POSIXt")) return(as.Date(x, tz = LOCAL_TZ))

  x_chr <- pt_chr(x)
  out <- suppressWarnings(as.Date(x_chr))

  if (all(is.na(out))) {
    out <- suppressWarnings(as.Date(lubridate::ymd_hms(x_chr, quiet = TRUE, tz = "UTC"), tz = LOCAL_TZ))
  }

  if (all(is.na(out))) {
    out <- suppressWarnings(as.Date(lubridate::ymd(x_chr, quiet = TRUE)))
  }

  out
}

pt_fmt_num <- function(x, digits = 1, suffix = "") {
  x <- suppressWarnings(as.numeric(x))
  out <- rep(NA_character_, length(x))
  ok <- !is.na(x)
  out[ok] <- paste0(formatC(x[ok], format = "f", digits = digits, big.mark = ","), suffix)
  out <- sub("(\\.\\d*?)0+([^0-9]|$)", "\\1\\2", out)
  out <- sub("\\.([^0-9]|$)", "\\1", out)
  out
}

pt_fmt_date_friendly <- function(x) {
  x <- pt_date(x)
  out <- rep(NA_character_, length(x))
  ok <- !is.na(x)
  out[ok] <- format(x[ok], "%b %d, %Y")
  out
}

pt_water_year <- function(date) {
  date <- as.Date(date)
  yr <- as.integer(format(date, "%Y"))
  mo <- as.integer(format(date, "%m"))
  ifelse(mo >= 10L, yr + 1L, yr)
}

pt_water_day <- function(date) {
  date <- as.Date(date)
  wy <- pt_water_year(date)
  wy_start <- as.Date(sprintf("%d-10-01", wy - 1L))
  as.integer(date - wy_start + 1L)
}

pt_empty_awdb <- function() {
  tibble::tibble(
    provider_station_id = character(),
    nrcs_station_triplet = character(),
    obs_date = as.Date(character()),
    value = numeric(),
    element_code = character()
  )
}

pt_empty_cdec <- function() {
  tibble::tibble(
    provider_station_id = character(),
    obs_date = as.Date(character()),
    value = numeric()
  )
}

first_existing_col <- function(x, choices) {
  hit <- choices[choices %in% names(x)]
  if (length(hit) == 0) NA_character_ else hit[[1]]
}

# ---- 4.1 Robust provider fetch helpers -------------------------------------

PT_FETCH_ATTEMPTS <- suppressWarnings(as.integer(Sys.getenv(
  "SNOW_PILLOW_FETCH_ATTEMPTS",
  unset = "3"
)))
if (is.na(PT_FETCH_ATTEMPTS) || PT_FETCH_ATTEMPTS < 1) PT_FETCH_ATTEMPTS <- 3L

PT_FETCH_TIMEOUT_SEC <- suppressWarnings(as.integer(Sys.getenv(
  "SNOW_PILLOW_FETCH_TIMEOUT_SEC",
  unset = "90"
)))
if (is.na(PT_FETCH_TIMEOUT_SEC) || PT_FETCH_TIMEOUT_SEC < 10) PT_FETCH_TIMEOUT_SEC <- 90L

pt_fetch_text <- function(url, label, attempts = PT_FETCH_ATTEMPTS, timeout_sec = PT_FETCH_TIMEOUT_SEC) {

  label <- pt_chr(label)
  if (is.na(label)) label <- "provider request"

  for (attempt in seq_len(attempts)) {

    handle <- curl::new_handle()
    curl::handle_setopt(
      handle,
      useragent = "BRIM-snow-pillow-latest/1.0 (+https://github.com/dbo99/brim-live-data-feeds)",
      followlocation = TRUE,
      timeout = timeout_sec,
      connecttimeout = min(30L, timeout_sec)
    )

    resp <- tryCatch(
      curl::curl_fetch_memory(url, handle = handle),
      error = function(e) e
    )

    if (!inherits(resp, "error")) {
      status <- as.integer(resp$status_code)
      txt <- rawToChar(resp$content)

      if (!is.na(status) && status >= 200L && status < 300L && nzchar(txt)) {
        return(txt)
      }

      msg <- paste0(
        label, " returned HTTP ", status,
        " on attempt ", attempt, "/", attempts,
        if (nzchar(txt)) paste0("; first response chars: ", substr(gsub("\\s+", " ", txt), 1, 160)) else ""
      )
      message(msg)
    } else {
      message(
        label, " failed on attempt ", attempt, "/", attempts,
        "; error: ", conditionMessage(resp)
      )
    }

    if (attempt < attempts) Sys.sleep(pmin(10, attempt * 2))
  }

  NULL
}

pt_json_from_url <- function(url, label) {

  txt <- pt_fetch_text(url, label = label)

  if (is.null(txt) || !nzchar(txt)) {
    return(NULL)
  }

  tryCatch(
    jsonlite::fromJSON(txt, flatten = TRUE),
    error = function(e) {
      message(label, " returned text that could not be parsed as JSON; error: ", conditionMessage(e))
      NULL
    }
  )
}

# ==== 5. AWDB / SNOTEL fetch helpers ========================================

parse_awdb_data_response <- function(resp, element_code) {

  empty_out <- pt_empty_awdb()

  if (!is.data.frame(resp) || !"stationTriplet" %in% names(resp) || !"data" %in% names(resp)) {
    return(empty_out)
  }

  pieces <- vector("list", nrow(resp))

  for (i in seq_len(nrow(resp))) {

    trip <- as.character(resp$stationTriplet[i])
    provider_station_id <- stringr::str_extract(trip, "^\\d+")
    data_i <- resp$data[[i]]

    if (!is.data.frame(data_i) || nrow(data_i) == 0 || !"values" %in% names(data_i)) {
      pieces[[i]] <- empty_out
      next
    }

    value_pieces <- vector("list", nrow(data_i))

    for (j in seq_len(nrow(data_i))) {

      vals <- data_i$values[[j]]

      if (!is.data.frame(vals) || nrow(vals) == 0) {
        value_pieces[[j]] <- empty_out
        next
      }

      vals <- tibble::as_tibble(vals)

      date_col <- first_existing_col(vals, c("date", "Date", "datetime", "obsDate"))
      value_col <- first_existing_col(vals, c("value", "Value"))

      if (is.na(date_col) && ncol(vals) >= 1) date_col <- names(vals)[1]
      if (is.na(value_col) && ncol(vals) >= 2) value_col <- names(vals)[2]

      if (is.na(date_col) || is.na(value_col)) {
        value_pieces[[j]] <- empty_out
        next
      }

      value_pieces[[j]] <- tibble::tibble(
        provider_station_id = provider_station_id,
        nrcs_station_triplet = trip,
        obs_date = pt_date(vals[[date_col]]),
        value = pt_num(vals[[value_col]]),
        element_code = element_code
      ) |>
        dplyr::filter(!is.na(obs_date))
    }

    pieces[[i]] <- dplyr::bind_rows(value_pieces)
  }

  dplyr::bind_rows(pieces)
}

fetch_awdb_element <- function(station_triplets,
                               element_code,
                               begin_date,
                               end_date,
                               chunk_size = AWDB_CHUNK_SIZE,
                               pause_sec = REQUEST_PAUSE_SEC) {

  station_triplets <- pt_chr(station_triplets)
  station_triplets <- unique(station_triplets[!is.na(station_triplets)])

  empty_out <- pt_empty_awdb()

  if (length(station_triplets) == 0) {
    return(empty_out)
  }

  chunks <- split(
    station_triplets,
    ceiling(seq_along(station_triplets) / chunk_size)
  )

  message("AWDB ", element_code, " chunks: ", length(chunks), " (", length(station_triplets), " stations)")

  fetch_one_awdb_chunk <- function(trips, chunk_label) {

    Sys.sleep(pause_sec)

    awdb_url <- paste0(
      "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data?",
      "stationTriplets=", utils::URLencode(paste(trips, collapse = ","), reserved = TRUE),
      "&elements=", utils::URLencode(element_code, reserved = TRUE),
      "&duration=DAILY",
      "&beginDate=", utils::URLencode(as.character(begin_date), reserved = TRUE),
      "&endDate=", utils::URLencode(as.character(end_date), reserved = TRUE),
      "&periodRef=END"
    )

    resp <- pt_json_from_url(
      awdb_url,
      label = paste0("AWDB ", element_code, " ", chunk_label, " (", length(trips), " station(s))")
    )

    if (is.null(resp) || !is.data.frame(resp) || nrow(resp) == 0) {
      return(empty_out)
    }

    parse_awdb_data_response(resp, element_code = element_code)
  }

  out <- purrr::imap(chunks, function(trips, idx) {

    chunk_rows <- fetch_one_awdb_chunk(trips, paste0("chunk ", idx, "/", length(chunks)))

    ## If a multi-station chunk fails or parses empty, retry station-by-station.
    ## This is slower but much safer for GitHub Actions and avoids publishing a
    ## zero-SNOTEL feed because one large chunk request failed.
    if (nrow(chunk_rows) == 0 && length(trips) > 1) {
      message(
        "AWDB ", element_code, " chunk ", idx, " returned 0 rows; ",
        "retrying ", length(trips), " station(s) individually."
      )

      indiv <- purrr::map(trips, function(trip) {
        fetch_one_awdb_chunk(trip, paste0("station ", trip))
      })

      return(dplyr::bind_rows(indiv))
    }

    chunk_rows
  })

  dplyr::bind_rows(out)
}


# ==== 6. CDEC fetch helper ===================================================

pt_parse_cdec_json_response <- function(resp, id) {

  empty_out <- pt_empty_cdec()

  if (is.null(resp) || !is.data.frame(resp) || nrow(resp) == 0) {
    return(empty_out)
  }

  raw <- tibble::as_tibble(resp)
  names(raw) <- make.names(tolower(names(raw)))

  station_col <- first_existing_col(raw, c("stationid", "station.id", "station_id", "id", "station"))
  date_col    <- first_existing_col(raw, c("date", "datetime", "obsdate", "obs.date", "eventdate", "event.date"))
  value_col   <- first_existing_col(raw, c("value", "sensorvalue", "sensor.value", "obsvalue", "obs.value"))

  if (is.na(date_col) || is.na(value_col)) {
    message(
      "CDEC direct JSON for ", id,
      " could not be standardized. Columns: ", paste(names(raw), collapse = ", ")
    )
    return(empty_out)
  }

  provider_station_id <- if (!is.na(station_col)) {
    pt_chr(raw[[station_col]])
  } else {
    rep(id, nrow(raw))
  }

  tibble::tibble(
    provider_station_id = provider_station_id,
    obs_date = pt_date(raw[[date_col]]),
    value = pt_num(raw[[value_col]])
  ) |>
    dplyr::filter(!is.na(.data$provider_station_id), !is.na(.data$obs_date))
}

fetch_cdec_swe <- function(cdec_ids,
                           begin_date,
                           end_date,
                           pause_sec = REQUEST_PAUSE_SEC) {

  cdec_ids <- pt_chr(cdec_ids)
  cdec_ids <- unique(cdec_ids[!is.na(cdec_ids)])

  empty_out <- pt_empty_cdec()

  if (length(cdec_ids) == 0) {
    return(empty_out)
  }

  message("CDEC SWE stations to query: ", length(cdec_ids))

  pieces <- purrr::map(cdec_ids, function(id) {

    Sys.sleep(pause_sec)

    raw <- NULL

    for (attempt in seq_len(PT_FETCH_ATTEMPTS)) {

      raw_try <- withCallingHandlers(
        tryCatch(
          sharpshootR::CDECquery(
            id = id,
            sensor = CDEC_SWE_SENSOR,
            interval = CDEC_DAILY_INTERVAL,
            start = as.character(begin_date),
            end = as.character(end_date)
          ),
          error = function(e) {
            message(
              "CDEC sharpshootR fetch failed for ", id,
              " attempt ", attempt, "/", PT_FETCH_ATTEMPTS,
              "; error: ", conditionMessage(e)
            )
            NULL
          }
        ),
        warning = function(w) {
          message(
            "CDEC sharpshootR warning for ", id,
            " attempt ", attempt, "/", PT_FETCH_ATTEMPTS,
            "; warning: ", conditionMessage(w)
          )
          invokeRestart("muffleWarning")
        }
      )

      if (is.data.frame(raw_try) && nrow(raw_try) > 0) {
        raw <- raw_try
        break
      }

      if (attempt < PT_FETCH_ATTEMPTS) Sys.sleep(pmin(8, attempt * 2))
    }

    if (is.null(raw) || !is.data.frame(raw) || nrow(raw) == 0) {

      ## Direct JSON fallback uses the same public CDEC endpoint that users can
      ## open in a browser.  It avoids losing a station solely because the
      ## sharpshootR wrapper or connection layer failed in GitHub Actions.
      cdec_url <- paste0(
        "https://cdec.water.ca.gov/dynamicapp/req/JSONDataServlet?",
        "Stations=", utils::URLencode(id, reserved = TRUE),
        "&SensorNums=", CDEC_SWE_SENSOR,
        "&dur_code=", CDEC_DAILY_INTERVAL,
        "&Start=", utils::URLencode(as.character(begin_date), reserved = TRUE),
        "&End=", utils::URLencode(as.character(end_date), reserved = TRUE)
      )

      resp <- pt_json_from_url(cdec_url, label = paste0("CDEC direct JSON ", id))
      return(pt_parse_cdec_json_response(resp, id = id))
    }

    raw <- tibble::as_tibble(raw)

    station_col <- first_existing_col(raw, c("station_id", "id", "station", "Station", "STATION_ID"))
    date_col    <- first_existing_col(raw, c("datetime", "date", "obs_date", "Date", "DATE"))
    value_col   <- first_existing_col(raw, c("value", "VALUE", "snow_water_content", "SWE"))

    if (is.na(date_col) || is.na(value_col)) {
      return(empty_out)
    }

    provider_station_id <- if (!is.na(station_col)) {
      pt_chr(raw[[station_col]])
    } else {
      rep(id, nrow(raw))
    }

    tibble::tibble(
      provider_station_id = provider_station_id,
      obs_date = pt_date(raw[[date_col]]),
      value = pt_num(raw[[value_col]])
    ) |>
      dplyr::filter(!is.na(provider_station_id), !is.na(obs_date))
  })

  dplyr::bind_rows(pieces)
}


# ==== 7. Observation summarizers ============================================

latest_and_delta <- function(obs,
                             id_col = "station_uid",
                             value_col = "swe_in",
                             date_col = "obs_date") {

  empty_out <- tibble::tibble(
    station_uid = character(),
    latest_swe_in = numeric(),
    latest_swe_date_local = character(),
    latest_swe_age_days = integer(),
    swe_delta_1day_in = numeric(),
    swe_delta_2day_in = numeric(),
    swe_delta_3day_in = numeric(),
    swe_delta_7day_in = numeric(),
    n_current_wy_swe_obs = integer(),
    n_current_wy_swe_zero_obs = integer(),
    n_current_wy_swe_positive_obs = integer()
  )

  if (!is.data.frame(obs) || nrow(obs) == 0) {
    return(empty_out)
  }

  obs2 <- obs |>
    dplyr::transmute(
      station_uid = as.character(.data[[id_col]]),
      obs_date = as.Date(.data[[date_col]]),
      swe_in = pt_num(.data[[value_col]])
    ) |>
    dplyr::filter(!is.na(.data$station_uid), !is.na(.data$obs_date), !is.na(.data$swe_in))

  if (nrow(obs2) == 0) {
    return(empty_out)
  }

  stats <- obs2 |>
    dplyr::group_by(.data$station_uid) |>
    dplyr::summarise(
      n_current_wy_swe_obs = dplyr::n(),
      n_current_wy_swe_zero_obs = sum(.data$swe_in == 0, na.rm = TRUE),
      n_current_wy_swe_positive_obs = sum(.data$swe_in > 0, na.rm = TRUE),
      .groups = "drop"
    )

  latest <- obs2 |>
    dplyr::arrange(.data$station_uid, dplyr::desc(.data$obs_date)) |>
    dplyr::group_by(.data$station_uid) |>
    dplyr::slice(1) |>
    dplyr::ungroup() |>
    dplyr::rename(
      latest_swe_date = obs_date,
      latest_swe_in = swe_in
    )

  delta_ref <- function(days_back) {
    latest |>
      dplyr::select("station_uid", "latest_swe_date", "latest_swe_in") |>
      dplyr::left_join(obs2, by = "station_uid", relationship = "many-to-many") |>
      dplyr::filter(.data$obs_date <= .data$latest_swe_date - days_back) |>
      dplyr::arrange(.data$station_uid, dplyr::desc(.data$obs_date)) |>
      dplyr::group_by(.data$station_uid) |>
      dplyr::slice(1) |>
      dplyr::ungroup() |>
      dplyr::transmute(
        station_uid = .data$station_uid,
        ref_value = .data$swe_in,
        delta = .data$latest_swe_in - .data$swe_in
      )
  }

  d1 <- delta_ref(1L) |>
    dplyr::transmute(station_uid = .data$station_uid, swe_delta_1day_in = .data$delta)

  d2 <- delta_ref(2L) |>
    dplyr::transmute(station_uid = .data$station_uid, swe_delta_2day_in = .data$delta)

  d3 <- delta_ref(3L) |>
    dplyr::transmute(station_uid = .data$station_uid, swe_delta_3day_in = .data$delta)

  d7 <- delta_ref(7L) |>
    dplyr::transmute(station_uid = .data$station_uid, swe_delta_7day_in = .data$delta)

  latest |>
    dplyr::left_join(d1, by = "station_uid") |>
    dplyr::left_join(d2, by = "station_uid") |>
    dplyr::left_join(d3, by = "station_uid") |>
    dplyr::left_join(d7, by = "station_uid") |>
    dplyr::left_join(stats, by = "station_uid") |>
    dplyr::mutate(
      latest_swe_age_days = as.integer(TODAY_LOCAL - .data$latest_swe_date),
      latest_swe_date_local = as.character(.data$latest_swe_date)
    ) |>
    dplyr::select(
      "station_uid",
      "latest_swe_in",
      "latest_swe_date_local",
      "latest_swe_age_days",
      "swe_delta_1day_in",
      "swe_delta_2day_in",
      "swe_delta_3day_in",
      "swe_delta_7day_in",
      "n_current_wy_swe_obs",
      "n_current_wy_swe_zero_obs",
      "n_current_wy_swe_positive_obs"
    )
}

latest_depth <- function(obs) {

  if (!is.data.frame(obs) || nrow(obs) == 0) {
    return(tibble::tibble(
      station_uid = character(),
      latest_snow_depth_in = numeric(),
      latest_snow_depth_date_local = character()
    ))
  }

  obs |>
    dplyr::transmute(
      station_uid = as.character(.data$station_uid),
      obs_date = as.Date(.data$obs_date),
      snow_depth_in = pt_num(.data$snow_depth_in)
    ) |>
    dplyr::filter(!is.na(.data$station_uid), !is.na(.data$obs_date), !is.na(.data$snow_depth_in)) |>
    dplyr::arrange(.data$station_uid, dplyr::desc(.data$obs_date)) |>
    dplyr::group_by(.data$station_uid) |>
    dplyr::slice(1) |>
    dplyr::ungroup() |>
    dplyr::transmute(
      station_uid = .data$station_uid,
      latest_snow_depth_in = .data$snow_depth_in,
      latest_snow_depth_date_local = as.character(.data$obs_date)
    )
}

# ==== 8. GeoJSON writer ======================================================

write_point_geojson <- function(x, path) {

  x <- x |>
    dplyr::filter(!is.na(.data$latitude), !is.na(.data$longitude))

  props <- x |>
    dplyr::mutate(
      dplyr::across(where(is.Date), as.character),
      dplyr::across(where(is.POSIXt), as.character)
    )

  features <- purrr::pmap(
    props,
    function(...) {
      row <- list(...)
      lat <- suppressWarnings(as.numeric(row$latitude))
      lon <- suppressWarnings(as.numeric(row$longitude))

      row$latitude <- lat
      row$longitude <- lon

      list(
        type = "Feature",
        geometry = list(
          type = "Point",
          coordinates = c(lon, lat)
        ),
        properties = row
      )
    }
  )

  fc <- list(
    type = "FeatureCollection",
    features = features
  )

  jsonlite::write_json(
    fc,
    path = path,
    auto_unbox = TRUE,
    na = "null",
    null = "null",
    pretty = FALSE,
    digits = 8
  )

  invisible(path)
}

# ==== 9. Read station index ==================================================

if (!file.exists(STATION_INDEX_CSV)) {
  stop(
    "Missing snow-pillow station index: ", STATION_INDEX_CSV,
    "\nRun export_snow_pillow_live_inputs() from the BRIM root first."
  )
}

message("Reading snow-pillow station index: ", STATION_INDEX_CSV)

stations <- readr::read_csv(STATION_INDEX_CSV, show_col_types = FALSE) |>
  dplyr::mutate(
    station_uid = pt_chr(.data$station_uid),
    live_provider_key = pt_chr(.data$live_provider_key),
    provider_station_id = pt_chr(.data$provider_station_id),
    nrcs_station_triplet = pt_chr(.data$nrcs_station_triplet),
    cdec_id = pt_chr(.data$cdec_id),
    latitude = pt_num(.data$latitude),
    longitude = pt_num(.data$longitude),
    elevation_ft = pt_num(.data$elevation_ft)
  ) |>
  dplyr::filter(!is.na(.data$station_uid), !is.na(.data$provider_station_id))

if (!is.na(STATION_LIMIT) && STATION_LIMIT > 0) {
  message("Development station limit active: ", STATION_LIMIT)
  stations <- stations |>
    dplyr::slice_head(n = STATION_LIMIT)
}

message("Stations in latest-feed build: ", nrow(stations))
message("Fetch window: ", as.character(FETCH_START_DATE), " to ", as.character(FETCH_END_DATE), " (WY", CURRENT_WY, ")")

# ==== 10. Fetch current-water-year observations =============================

snotel_stations <- stations |>
  dplyr::filter(.data$live_provider_key == "nrcs_snotel")

cdec_stations <- stations |>
  dplyr::filter(.data$live_provider_key == "cdec_snow_sensor")

message("NRCS/SNOTEL rows in station index: ", nrow(snotel_stations))
message("CDEC rows in station index: ", nrow(cdec_stations))

snotel_wteq <- fetch_awdb_element(
  station_triplets = snotel_stations$nrcs_station_triplet,
  element_code = "WTEQ",
  begin_date = FETCH_START_DATE,
  end_date = FETCH_END_DATE
)

snotel_snwd <- fetch_awdb_element(
  station_triplets = snotel_stations$nrcs_station_triplet,
  element_code = "SNWD",
  begin_date = FETCH_START_DATE,
  end_date = FETCH_END_DATE
)

cdec_swe <- fetch_cdec_swe(
  cdec_ids = cdec_stations$cdec_id,
  begin_date = FETCH_START_DATE,
  end_date = FETCH_END_DATE
)

message("Rows returned: SNOTEL WTEQ = ", nrow(snotel_wteq), "; SNOTEL SNWD = ", nrow(snotel_snwd), "; CDEC SWE = ", nrow(cdec_swe))

# ==== 11. Standardize observations ==========================================

snotel_key <- snotel_stations |>
  dplyr::select(
    "station_uid",
    "nrcs_station_triplet",
    "provider_station_id",
    "provider",
    "station_name"
  ) |>
  dplyr::rename(
    station_provider_station_id = provider_station_id,
    station_provider = provider,
    station_name_index = station_name
  )

cdec_key <- cdec_stations |>
  dplyr::select(
    "station_uid",
    "cdec_id",
    "provider_station_id",
    "provider",
    "station_name"
  ) |>
  dplyr::rename(
    station_provider_station_id = provider_station_id,
    station_provider = provider,
    station_name_index = station_name
  )

snotel_swe_obs <- snotel_wteq |>
  dplyr::left_join(snotel_key, by = "nrcs_station_triplet") |>
  dplyr::transmute(
    station_uid = .data$station_uid,
    provider = .data$station_provider,
    provider_station_id = dplyr::coalesce(.data$station_provider_station_id, .data$provider_station_id),
    station_name = .data$station_name_index,
    obs_date = as.Date(.data$obs_date),
    raw_swe_in = pt_num(.data$value),
    swe_in = pt_swe_num(.data$value),
    source_element = "WTEQ"
  ) |>
  dplyr::filter(!is.na(.data$station_uid), !is.na(.data$obs_date))

snotel_depth_obs <- snotel_snwd |>
  dplyr::left_join(snotel_key, by = "nrcs_station_triplet") |>
  dplyr::transmute(
    station_uid = .data$station_uid,
    obs_date = as.Date(.data$obs_date),
    snow_depth_in = pt_num(.data$value)
  ) |>
  dplyr::filter(!is.na(.data$station_uid), !is.na(.data$obs_date))

cdec_swe_obs <- cdec_swe |>
  dplyr::left_join(cdec_key, by = c("provider_station_id" = "cdec_id")) |>
  dplyr::transmute(
    station_uid = .data$station_uid,
    provider = .data$station_provider,
    provider_station_id = dplyr::coalesce(.data$station_provider_station_id, .data$provider_station_id),
    station_name = .data$station_name_index,
    obs_date = as.Date(.data$obs_date),
    raw_swe_in = pt_num(.data$value),
    swe_in = pt_swe_num(.data$value),
    source_element = "CDEC_SENSOR_3"
  ) |>
  dplyr::filter(!is.na(.data$station_uid), !is.na(.data$obs_date))

invalid_negative_snotel_swe_rows <- pt_negative_swe_count(snotel_swe_obs$raw_swe_in)
invalid_negative_cdec_swe_rows <- pt_negative_swe_count(cdec_swe_obs$raw_swe_in)
invalid_negative_swe_rows <- invalid_negative_snotel_swe_rows + invalid_negative_cdec_swe_rows

swe_obs <- dplyr::bind_rows(
  snotel_swe_obs,
  cdec_swe_obs
) |>
  dplyr::filter(!is.na(.data$swe_in)) |>
  dplyr::mutate(
    water_year = pt_water_year(.data$obs_date),
    water_day = pt_water_day(.data$obs_date),
    obs_date_local = as.character(.data$obs_date)
  ) |>
  dplyr::arrange(.data$station_uid, .data$obs_date)

# ==== 12. Build latest point feed ===========================================

latest_swe <- latest_and_delta(swe_obs)
latest_snwd <- latest_depth(snotel_depth_obs)

in_low_snow_reporting_season <- as.integer(format(TODAY_LOCAL, "%m")) %in% c(7L, 8L, 9L)

latest <- stations |>
  dplyr::left_join(latest_swe, by = "station_uid") |>
  dplyr::left_join(latest_snwd, by = "station_uid") |>
  dplyr::mutate(
    latest_swe_value_class = dplyr::case_when(
      is.na(.data$latest_swe_in) ~ "no_valid_current_wy_swe",
      .data$latest_swe_in < 0 ~ "invalid_negative_swe",
      .data$latest_swe_in == 0 ~ "zero",
      .data$latest_swe_in > 0 ~ "positive",
      TRUE ~ "unknown"
    ),
    latest_swe_staleness_class = dplyr::case_when(
      is.na(.data$latest_swe_age_days) ~ "no_valid_current_wy_swe",
      .data$latest_swe_age_days <= 2 ~ "fresh_0_2_days",
      .data$latest_swe_age_days <= STALE_DAYS ~ paste0("recent_3_", STALE_DAYS, "_days"),
      .data$latest_swe_age_days <= VERY_STALE_DAYS ~ paste0("stale_", STALE_DAYS + 1L, "_", VERY_STALE_DAYS, "_days"),
      TRUE ~ paste0("very_stale_gt_", VERY_STALE_DAYS, "_days")
    ),
    latest_swe_report_status = dplyr::case_when(
      is.na(.data$latest_swe_in) & in_low_snow_reporting_season ~ "outside_snow_season_no_recent_report",
      is.na(.data$latest_swe_in) ~ "missing_recent_value",
      .data$latest_swe_in < 0 ~ "invalid_negative_swe",
      !is.na(.data$latest_swe_age_days) & .data$latest_swe_age_days > VERY_STALE_DAYS ~ "stale_last_value",
      .data$latest_swe_in == 0 ~ "reported_zero",
      .data$latest_swe_in > 0 ~ "reported_positive",
      TRUE ~ "unknown"
    ),
    latest_swe_display = dplyr::case_when(
      is.na(.data$latest_swe_in) ~ "No valid current-WY SWE reported; do not assume zero.",
      TRUE ~ paste0(
        pt_fmt_num(.data$latest_swe_in, 1, " in"),
        " on ",
        pt_fmt_date_friendly(.data$latest_swe_date_local)
      )
    ),
    latest_snow_depth_display = dplyr::case_when(
      is.na(.data$latest_snow_depth_in) ~ NA_character_,
      TRUE ~ paste0(
        pt_fmt_num(.data$latest_snow_depth_in, 1, " in"),
        " on ",
        pt_fmt_date_friendly(.data$latest_snow_depth_date_local)
      )
    ),
    feed_build_date_local = as.character(TODAY_LOCAL),
    feed_build_time_local = format(lubridate::with_tz(Sys.time(), LOCAL_TZ), "%Y-%m-%d %I:%M %p %Z"),
    current_water_year = CURRENT_WY,
    fetch_start_date = as.character(FETCH_START_DATE),
    fetch_end_date = as.character(FETCH_END_DATE)
  ) |>
  dplyr::transmute(
    station_uid,
    live_provider_key,
    provider,
    source_system,
    station_type,
    station_type_label,
    provider_station_id,
    station_name,
    state,
    county,
    river_basin,
    huc,
    elevation_ft,
    latitude,
    longitude,
    period_of_record,
    station_status,
    official_station_url,
    official_data_url,
    latest_swe_in,
    latest_swe_date_local,
    latest_swe_age_days,
    latest_swe_value_class,
    latest_swe_staleness_class,
    latest_swe_report_status,
    latest_swe_display,
    swe_delta_1day_in,
    swe_delta_2day_in,
    swe_delta_3day_in,
    swe_delta_7day_in,
    latest_snow_depth_in,
    latest_snow_depth_date_local,
    latest_snow_depth_display,
    n_current_wy_swe_obs,
    n_current_wy_swe_zero_obs,
    n_current_wy_swe_positive_obs,
    current_water_year,
    fetch_start_date,
    fetch_end_date,
    feed_build_date_local,
    feed_build_time_local
  ) |>
  dplyr::arrange(.data$live_provider_key, .data$station_name, .data$provider_station_id)

# ==== 13. Build current-water-year trace ====================================

wy_trace <- swe_obs |>
  dplyr::left_join(
    stations |>
      dplyr::select(
        "station_uid",
        "live_provider_key",
        "provider",
        "provider_station_id",
        "station_name"
      ) |>
      dplyr::rename(
        provider_index = provider,
        provider_station_id_index = provider_station_id,
        station_name_index = station_name
      ),
    by = "station_uid"
  ) |>
  dplyr::transmute(
    station_uid,
    live_provider_key,
    provider = dplyr::coalesce(.data$provider, .data$provider_index),
    provider_station_id = dplyr::coalesce(.data$provider_station_id, .data$provider_station_id_index),
    station_name = dplyr::coalesce(.data$station_name, .data$station_name_index),
    water_year,
    water_day,
    obs_date_local,
    swe_in,
    source_element
  ) |>
  dplyr::arrange(.data$station_uid, .data$obs_date_local)

# ==== 14. Summary objects ====================================================

status_counts <- latest |>
  dplyr::count(.data$latest_swe_report_status, name = "n") |>
  dplyr::arrange(.data$latest_swe_report_status)

provider_counts <- latest |>
  dplyr::count(.data$live_provider_key, .data$latest_swe_report_status, name = "n") |>
  dplyr::arrange(.data$live_provider_key, .data$latest_swe_report_status)

summary_obj <- list(
  layer = "Snow pillows / SWE latest",
  build_time_local = format(lubridate::with_tz(Sys.time(), LOCAL_TZ), "%Y-%m-%d %I:%M %p %Z"),
  build_date_local = as.character(TODAY_LOCAL),
  local_time_zone = LOCAL_TZ,
  current_water_year = CURRENT_WY,
  fetch_start_date = as.character(FETCH_START_DATE),
  fetch_end_date = as.character(FETCH_END_DATE),
  station_rows = nrow(stations),
  latest_geojson_rows = nrow(latest),
  current_wy_trace_rows = nrow(wy_trace),
  snotel_wteq_rows_returned = nrow(snotel_wteq),
  snotel_snwd_rows_returned = nrow(snotel_snwd),
  cdec_swe_rows_returned = nrow(cdec_swe),
  invalid_negative_snotel_swe_rows_excluded = invalid_negative_snotel_swe_rows,
  invalid_negative_cdec_swe_rows_excluded = invalid_negative_cdec_swe_rows,
  invalid_negative_swe_rows_excluded = invalid_negative_swe_rows,
  rows_with_latest_swe = sum(!is.na(latest$latest_swe_in)),
  rows_without_latest_swe = sum(is.na(latest$latest_swe_in)),
  rows_reported_zero = sum(latest$latest_swe_report_status == "reported_zero", na.rm = TRUE),
  rows_reported_positive = sum(latest$latest_swe_report_status == "reported_positive", na.rm = TRUE),
  rows_missing_recent_value = sum(latest$latest_swe_report_status == "missing_recent_value", na.rm = TRUE),
  rows_outside_snow_season_no_recent_report = sum(latest$latest_swe_report_status == "outside_snow_season_no_recent_report", na.rm = TRUE),
  rows_stale_last_value = sum(latest$latest_swe_report_status == "stale_last_value", na.rm = TRUE),
  rows_with_1day_delta = sum(!is.na(latest$swe_delta_1day_in)),
  rows_with_3day_delta = sum(!is.na(latest$swe_delta_3day_in)),
  rows_with_7day_delta = sum(!is.na(latest$swe_delta_7day_in)),
  status_counts = status_counts,
  provider_status_counts = provider_counts,
  data_notes = c(
    "Missing SWE is not interpreted as zero.",
    "Zero SWE is shown only when the provider returned a numeric 0.",
    "Negative provider SWE values are treated as invalid/missing and excluded from latest/trace products.",
    "Observation values are daily; browser-facing observation fields use local date labels rather than UTC/Z timestamps."
  )
)

trace_summary_obj <- list(
  layer = "Snow pillows / SWE current water-year trace",
  build_time_local = summary_obj$build_time_local,
  build_date_local = summary_obj$build_date_local,
  local_time_zone = LOCAL_TZ,
  current_water_year = CURRENT_WY,
  fetch_start_date = as.character(FETCH_START_DATE),
  fetch_end_date = as.character(FETCH_END_DATE),
  current_wy_trace_rows = nrow(wy_trace),
  stations_with_trace_rows = dplyr::n_distinct(wy_trace$station_uid),
  providers = sort(unique(wy_trace$live_provider_key))
)

# ==== 14.5. Pre-write QA guardrails =========================================

## The live map should not publish a mostly-empty feed when a provider/API or
## GitHub runner fetch fails.  These checks intentionally run after all fetches
## and summaries are built, but before any browser-facing files are written.
DISABLE_QA_GUARDRAILS <- tolower(Sys.getenv(
  "SNOW_PILLOW_DISABLE_QA_GUARDRAILS",
  unset = "false"
)) %in% c("true", "t", "1", "yes", "y")

expected_fetch_days <- max(1L, as.integer(FETCH_END_DATE - FETCH_START_DATE + 1L))

qa_min_snotel_wteq_rows <- suppressWarnings(as.integer(Sys.getenv(
  "SNOW_PILLOW_QA_MIN_SNOTEL_WTEQ_ROWS",
  unset = as.character(max(10L, floor(nrow(snotel_stations) * expected_fetch_days * 0.20)))
)))
qa_min_snotel_snwd_rows <- suppressWarnings(as.integer(Sys.getenv(
  "SNOW_PILLOW_QA_MIN_SNOTEL_SNWD_ROWS",
  unset = as.character(max(10L, floor(nrow(snotel_stations) * expected_fetch_days * 0.20)))
)))
qa_min_cdec_swe_rows <- suppressWarnings(as.integer(Sys.getenv(
  "SNOW_PILLOW_QA_MIN_CDEC_SWE_ROWS",
  unset = as.character(max(10L, floor(nrow(cdec_stations) * expected_fetch_days * 0.20)))
)))
qa_min_trace_rows <- suppressWarnings(as.integer(Sys.getenv(
  "SNOW_PILLOW_QA_MIN_CURRENT_WY_TRACE_ROWS",
  unset = as.character(max(10L, floor(nrow(stations) * expected_fetch_days * 0.20)))
)))
qa_min_latest_swe_rows <- suppressWarnings(as.integer(Sys.getenv(
  "SNOW_PILLOW_QA_MIN_LATEST_SWE_ROWS",
  unset = as.character(if (in_low_snow_reporting_season) 10L else max(25L, floor(nrow(stations) * 0.40)))
)))

qa_problems <- character()

if (nrow(snotel_stations) > 0 && nrow(snotel_wteq) < qa_min_snotel_wteq_rows) {
  qa_problems <- c(
    qa_problems,
    paste0("SNOTEL WTEQ rows too low: ", nrow(snotel_wteq), " < ", qa_min_snotel_wteq_rows)
  )
}

if (nrow(snotel_stations) > 0 && nrow(snotel_snwd) < qa_min_snotel_snwd_rows) {
  qa_problems <- c(
    qa_problems,
    paste0("SNOTEL SNWD rows too low: ", nrow(snotel_snwd), " < ", qa_min_snotel_snwd_rows)
  )
}

if (nrow(cdec_stations) > 0 && nrow(cdec_swe) < qa_min_cdec_swe_rows) {
  qa_problems <- c(
    qa_problems,
    paste0("CDEC SWE rows too low: ", nrow(cdec_swe), " < ", qa_min_cdec_swe_rows)
  )
}

if (nrow(wy_trace) < qa_min_trace_rows) {
  qa_problems <- c(
    qa_problems,
    paste0("Current-WY trace rows too low: ", nrow(wy_trace), " < ", qa_min_trace_rows)
  )
}

latest_swe_rows <- sum(!is.na(latest$latest_swe_in))
if (latest_swe_rows < qa_min_latest_swe_rows) {
  qa_problems <- c(
    qa_problems,
    paste0("Rows with latest SWE too low: ", latest_swe_rows, " < ", qa_min_latest_swe_rows)
  )
}

if (length(qa_problems) > 0) {

  qa_msg <- paste0(
    "Snow pillow latest-feed QA guardrail failed; refusing to write/publish degraded feed files.\n",
    "Problems:\n  - ", paste(qa_problems, collapse = "\n  - "), "\n\n",
    "Observed fetch summary:\n",
    "  SNOTEL WTEQ rows: ", nrow(snotel_wteq), "\n",
    "  SNOTEL SNWD rows: ", nrow(snotel_snwd), "\n",
    "  CDEC SWE rows: ", nrow(cdec_swe), "\n",
    "  Current-WY trace rows: ", nrow(wy_trace), "\n",
    "  Rows with latest SWE: ", latest_swe_rows, "\n",
    "  Stations: ", nrow(stations), "\n",
    "  Expected fetch days: ", expected_fetch_days, "\n\n",
    "If this is an intentional emergency override, set ",
    "SNOW_PILLOW_DISABLE_QA_GUARDRAILS=true for a one-off run."
  )

  if (DISABLE_QA_GUARDRAILS) {
    warning(qa_msg, call. = FALSE)
  } else {
    stop(qa_msg, call. = FALSE)
  }
}

# ==== 15. Write outputs ======================================================

write_point_geojson(latest, OUT_LATEST_GEOJSON)

jsonlite::write_json(
  summary_obj,
  path = OUT_LATEST_SUMMARY,
  auto_unbox = TRUE,
  na = "null",
  null = "null",
  pretty = TRUE,
  digits = 8
)

readr::write_csv(wy_trace, OUT_WY_TRACE_CSV)

jsonlite::write_json(
  trace_summary_obj,
  path = OUT_WY_TRACE_SUMRY,
  auto_unbox = TRUE,
  na = "null",
  null = "null",
  pretty = TRUE,
  digits = 8
)

# ==== 16. Console QA =========================================================

message("\nSnow pillow latest-feed summary:")
message("  Stations:                 ", nrow(stations))
message("  Latest GeoJSON rows:      ", nrow(latest))
message("  Current-WY trace rows:    ", nrow(wy_trace))
message("  Rows with latest SWE:     ", sum(!is.na(latest$latest_swe_in)))
message("  Rows without latest SWE:  ", sum(is.na(latest$latest_swe_in)))
message("  Reported zero rows:       ", sum(latest$latest_swe_report_status == "reported_zero", na.rm = TRUE))
message("  Reported positive rows:   ", sum(latest$latest_swe_report_status == "reported_positive", na.rm = TRUE))
message("  Invalid negative obs rows excluded: ", invalid_negative_swe_rows)
message("  Rows with 1-day SWE delta: ", sum(!is.na(latest$swe_delta_1day_in)))
message("  Rows with 3-day SWE delta: ", sum(!is.na(latest$swe_delta_3day_in)))
message("  Rows with 7-day SWE delta: ", sum(!is.na(latest$swe_delta_7day_in)))
message("    SNOTEL invalid negative obs:      ", invalid_negative_snotel_swe_rows)
message("    CDEC invalid negative obs:        ", invalid_negative_cdec_swe_rows)
message("  Missing recent rows:      ", sum(latest$latest_swe_report_status == "missing_recent_value", na.rm = TRUE))
message("  Stale last-value rows:    ", sum(latest$latest_swe_report_status == "stale_last_value", na.rm = TRUE))
message("\nLatest SWE report-status counts:")
print(status_counts, n = Inf)
message("\nLatest SWE provider/status counts:")
print(provider_counts, n = Inf)
message("\nWrote:")
message("  ", OUT_LATEST_GEOJSON)
message("  ", OUT_LATEST_SUMMARY)
message("  ", OUT_WY_TRACE_CSV)
message("  ", OUT_WY_TRACE_SUMRY)

message("\nDone: snow pillow / SWE latest feed build complete.")

