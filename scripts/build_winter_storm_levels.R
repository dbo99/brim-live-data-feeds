# Build the BRIM Winter Storm Levels rolling NBM snow-level contour bundle.
#
# Dry run is the default. Set BRIM_WSL_PUBLISH=true only from the protected
# main-branch workflow after the complete candidate bundle validates.

wsl_env <- function(name, default = "") {
  value <- Sys.getenv(name, unset = default)
  if (!nzchar(value)) default else value
}

wsl_project_root <- normalizePath(
  wsl_env("BRIM_WSL_PROJECT_ROOT", getwd()), winslash = "/", mustWork = TRUE
)
setwd(wsl_project_root)
source(file.path("scripts", "winter_storm_levels_helpers.R"))

required_packages <- c("digest", "httr2", "jsonlite", "sf", "terra")
missing_packages <- required_packages[
  !vapply(required_packages, requireNamespace, quietly = TRUE, FUN.VALUE = logical(1))
]
if (length(missing_packages)) {
  stop("Missing required R packages: ", paste(missing_packages, collapse = ", "))
}

config <- wsl_read_config(file.path("data", "input", "winter_storm_levels_config.csv"))
publish <- identical(tolower(wsl_env("BRIM_WSL_PUBLISH", "false")), "true")
canonical_root <- normalizePath(
  wsl_env("BRIM_WSL_CANONICAL_ROOT", file.path(wsl_project_root, "docs", "data", "winter-storm-levels")),
  winslash = "/", mustWork = FALSE
)
output_root <- normalizePath(
  wsl_env("BRIM_WSL_OUTPUT_ROOT", file.path(tempdir(), "winter-storm-levels-dry-run")),
  winslash = "/", mustWork = FALSE
)
qa_path <- normalizePath(
  wsl_env("BRIM_WSL_QA_JSON", file.path(tempdir(), "winter-storm-levels-attempt.json")),
  winslash = "/", mustWork = FALSE
)
manifest_name <- "winter_storm_levels_manifest.json"

now_text <- wsl_env("BRIM_WSL_NOW_UTC", "")
now <- if (nzchar(now_text)) wsl_as_utc(now_text) else wsl_as_utc(Sys.time())
if (is.na(now)) stop("BRIM_WSL_NOW_UTC is invalid.")

attempt <- list(
  product_id = config$product_id,
  started_at_utc = wsl_iso_utc(Sys.time()),
  observation_time_utc = wsl_iso_utc(now),
  mode = if (publish) "publish_candidate" else "dry_run",
  status = "fetch_failed",
  source_attempts = list(),
  selected_cycle_time_utc = NULL,
  output_root = if (publish) "docs/data/winter-storm-levels" else output_root,
  promoted = FALSE,
  semantic_change = FALSE
)

wsl_copy_retained_cycles <- function(candidate_root, entries, selected_cycle, config) {
  prior_manifest_path <- file.path(canonical_root, manifest_name)
  if (!file.exists(prior_manifest_path) || config$retain_cycle_count <= 1L) return(entries)
  prior <- tryCatch(jsonlite::fromJSON(prior_manifest_path, simplifyVector = FALSE), error = function(error) NULL)
  if (is.null(prior) || !length(prior$targets)) return(entries)

  prior_cycles <- unique(vapply(prior$targets, `[[`, character(1), "cycle_time_utc"))
  prior_cycles <- setdiff(prior_cycles, wsl_iso_utc(selected_cycle))
  prior_cycles <- sort(prior_cycles, decreasing = TRUE)
  keep_cycles <- head(prior_cycles, max(0L, as.integer(config$retain_cycle_count) - 1L))
  retained <- Filter(function(entry) entry$cycle_time_utc %in% keep_cycles, prior$targets)
  copied <- list()
  for (entry in retained) {
    relative_path <- tryCatch(wsl_safe_target_path(entry$path), error = function(error) NULL)
    if (is.null(relative_path)) next
    source <- file.path(canonical_root, relative_path)
    destination <- file.path(candidate_root, relative_path)
    if (!file.exists(source) || !identical(wsl_sha256(source), entry$sha256)) next
    dir.create(dirname(destination), recursive = TRUE, showWarnings = FALSE)
    if (!file.copy(source, destination, overwrite = TRUE, copy.mode = TRUE)) next
    copied[[length(copied) + 1L]] <- entry
  }
  c(entries, copied)
}

wsl_build <- function() {
  stage_root <- tempfile("winter-storm-levels-candidate-")
  dir.create(stage_root, recursive = TRUE, showWarnings = FALSE)
  on.exit(unlink(stage_root, recursive = TRUE, force = TRUE), add = TRUE)

  discovery <- wsl_discover_cycle(now, config)
  attempt$source_attempts <<- discovery$attempts
  attempt$selected_cycle_time_utc <<- wsl_iso_utc(discovery$cycle_time)
  entries <- list()
  source_stats <- list()
  started <- proc.time()[["elapsed"]]

  for (record in discovery$records) {
    message("Building NBM SNOWLVL ", record$cycle_time_utc, " f", sprintf("%03d", record$lead_hours))
    grib_path <- tempfile(sprintf("wsl-f%03d-", record$lead_hours), fileext = ".grib2")
    on.exit(unlink(grib_path, force = TRUE), add = TRUE)
    wsl_fetch_range(record$grib_url, record$start_byte, record$end_byte, grib_path, config)
    raster <- wsl_decode_grib(grib_path)
    stats <- wsl_validate_raster(raster, record, config)
    contour <- wsl_make_contours(raster, record, config)
    working_path <- file.path(stage_root, ".working", sprintf("f%03d.geojson", record$lead_hours))
    wsl_write_json(contour$geojson, working_path)
    wsl_validate_geojson(working_path, record, config)
    relative_path <- wsl_target_relative_path(record, wsl_sha256(working_path))
    target_path <- file.path(stage_root, relative_path)
    dir.create(dirname(target_path), recursive = TRUE, showWarnings = FALSE)
    if (!file.rename(working_path, target_path)) stop("Could not finalize content-addressed target path.")
    entries[[length(entries) + 1L]] <- wsl_target_entry(
      record, relative_path, stats, contour, target_path, config
    )
    source_stats[[length(source_stats) + 1L]] <- list(
      lead_hours = record$lead_hours,
      source_bytes = unname(file.info(grib_path)$size),
      output_bytes = unname(file.info(target_path)$size),
      feature_count = contour$feature_count,
      finite_coverage = stats$finite_coverage
    )
  }

  entries <- wsl_copy_retained_cycles(stage_root, entries, discovery$cycle_time, config)
  manifest <- wsl_manifest(
    discovery$cycle_time, entries, now, config,
    publication_time = if (publish) now else NULL
  )
  manifest_path <- file.path(stage_root, manifest_name)
  wsl_write_json(manifest, manifest_path)
  wsl_validate_manifest(manifest_path, stage_root, config, now)

  destination_root <- if (publish) canonical_root else output_root
  promotion <- wsl_promote_bundle(stage_root, destination_root, manifest_name, config)
  wsl_validate_manifest(file.path(destination_root, manifest_name), destination_root, config, now)

  attempt$status <<- manifest$status
  attempt$promoted <<- isTRUE(publish && promotion$changed)
  attempt$semantic_change <<- isTRUE(promotion$changed)
  attempt$manifest_path <<- file.path(destination_root, manifest_name)
  attempt$target_count <<- length(entries)
  attempt$expected_current_cycle_target_count <<- length(config$forecast_lead_hours)
  attempt$current_cycle_target_count <<- length(discovery$records)
  valid_times <- vapply(discovery$records, `[[`, character(1), "valid_time_utc")
  attempt$valid_time_start_utc <<- min(valid_times)
  attempt$valid_time_end_utc <<- max(valid_times)
  attempt$source_stats <<- source_stats
  attempt$total_source_bytes <<- sum(vapply(source_stats, `[[`, numeric(1), "source_bytes"))
  attempt$total_output_bytes <<- sum(vapply(source_stats, `[[`, numeric(1), "output_bytes"))
  attempt$qa <<- "passed"
  attempt$publication_outcome <<- if (!publish) {
    "dry_run_only"
  } else if (promotion$changed) {
    "promoted"
  } else {
    "semantic_noop"
  }
  attempt$elapsed_seconds <<- round(proc.time()[["elapsed"]] - started, 3)
  attempt$removed_paths <<- if (length(promotion$removed)) {
    sub(paste0("^", destination_root, "/?"), "", promotion$removed)
  } else character()
  invisible(attempt)
}

result <- tryCatch(
  {
    wsl_build()
    NULL
  },
  error = function(error) error
)

if (inherits(result, "error")) {
  if (!is.null(result$attempts)) attempt$source_attempts <- result$attempts
  classes <- class(result)
  if ("source_unavailable" %in% classes) {
    attempt$status <- "source_unavailable"
  } else if ("variable_missing" %in% classes) {
    attempt$status <- "variable_missing"
  } else if ("decode_failed" %in% classes) {
    attempt$status <- "decode_failed"
  } else if ("publication_failed" %in% classes) {
    attempt$status <- "publication_failed"
  } else if (any(c("fetch_failed_transient", "fetch_failed_permanent") %in% classes)) {
    attempt$status <- "fetch_failed"
  } else {
    attempt$status <- "validation_failed"
  }
  attempt$qa <- "failed"
  attempt$publication_outcome <- "not_published"
  attempt$error <- conditionMessage(result)
}
attempt$completed_at_utc <- wsl_iso_utc(Sys.time())
wsl_write_json(attempt, qa_path)

if (inherits(result, "error")) stop(conditionMessage(result), call. = FALSE)
message("Winter Storm Levels candidate status: ", attempt$status)
message("Bundle: ", attempt$manifest_path)
