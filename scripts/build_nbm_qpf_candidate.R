#!/usr/bin/env Rscript

# Build one complete, offline NBM QPF candidate cycle.
#
# This entry point has no publication mode and refuses canonical docs/data.
# Use BRIM_NBM_QPF_OUTPUT_ROOT to select a new, absent delivery directory.

qpf_env <- function(name, default = "") {
  value <- Sys.getenv(name, unset = default)
  if (!nzchar(value)) default else value
}

qpf_project_root <- normalizePath(
  qpf_env("BRIM_NBM_QPF_PROJECT_ROOT", getwd()),
  winslash = "/",
  mustWork = TRUE
)
setwd(qpf_project_root)
source(file.path("scripts", "nbm_qpf_helpers.R"))

required_packages <- c("digest", "httr2", "jsonlite", "png", "terra")
missing_packages <- required_packages[
  !vapply(required_packages, requireNamespace, quietly = TRUE, FUN.VALUE = logical(1))
]
if (length(missing_packages)) {
  stop("Missing required R packages: ", paste(missing_packages, collapse = ", "))
}

palette_path <- normalizePath(
  qpf_env(
    "BRIM_NBM_QPF_PALETTE",
    file.path(qpf_project_root, "data", "input", "nbm_qpf_palette.csv")
  ),
  winslash = "/",
  mustWork = TRUE
)
output_root <- qpf_env(
  "BRIM_NBM_QPF_OUTPUT_ROOT",
  tempfile("nbm-qpf-candidate-delivery-")
)
invisible(qpf_assert_offline_output_root(output_root, qpf_project_root))

explicit_cycle <- qpf_env("BRIM_NBM_QPF_CYCLE_UTC", "")
now_text <- qpf_env("BRIM_NBM_QPF_NOW_UTC", "")
now <- if (nzchar(now_text)) qpf_as_utc(now_text) else qpf_as_utc(Sys.time())
if (is.na(now)) stop("BRIM_NBM_QPF_NOW_UTC is invalid.")
lookback_hours <- suppressWarnings(as.numeric(qpf_env("BRIM_NBM_QPF_LOOKBACK_HOURS", "36")))
if (!is.finite(lookback_hours) || lookback_hours < 0 || lookback_hours > 168) {
  stop("BRIM_NBM_QPF_LOOKBACK_HOURS must be between 0 and 168.")
}
repeat_build <- identical(
  tolower(qpf_env("BRIM_NBM_QPF_REPEAT_BUILD", "false")), "true"
)

working_root <- tempfile("nbm-qpf-producer-")
dir.create(working_root, recursive = TRUE, showWarnings = FALSE)
on.exit(unlink(working_root, recursive = TRUE, force = TRUE), add = TRUE)

message("Discovering a complete deterministic NBM Core CONUS APCP cycle.")
discovery <- qpf_discover_cycle(
  now = now,
  lookback_hours = lookback_hours,
  explicit_cycle = if (nzchar(explicit_cycle)) explicit_cycle else NULL
)
message("Selected complete QPF cycle: ", qpf_iso_utc(discovery$cycle))
message("Inventory preflight passed for all ten required native six-hour records.")

source_root <- file.path(working_root, "sources")
sources <- qpf_acquire_sources(discovery$records, source_root)
first_root <- file.path(working_root, "candidate-first")
first <- qpf_build_candidate(discovery$records, sources, first_root, palette_path)

if (repeat_build) {
  message("Repeating production from the exact same downloaded source inputs.")
  second_root <- file.path(working_root, "candidate-second")
  qpf_build_candidate(discovery$records, sources, second_root, palette_path)
  proof <- qpf_compare_candidate_builds(first_root, second_root, palette_path)
  qpf_record_determinism(first_root, proof, palette_path)
  message("Determinism passed: 10/10 WebP byte and SHA identities.")
}

delivered <- qpf_deliver_candidate(
  first_root, output_root, palette_path, qpf_project_root
)
validated <- qpf_validate_candidate(delivered, palette_path)
targets <- validated$manifest$cycle$targets
message("NBM QPF candidate validation: passed")
message("Candidate root: ", delivered)
message("Cycle maximum (in): ", validated$manifest$cycle$cycle_max_qpf_in)
message("Legend cap (in): ", validated$manifest$cycle$legend_cap_in)
message("Legend overflow: ", validated$manifest$cycle$legend_overflow)
message("Total WebP bytes: ", sum(vapply(targets, function(target) {
  as.numeric(target$bytes)
}, numeric(1))))
for (target in targets) {
  message(
    sprintf("f%03d bytes=%d sha256=%s", target$lead_hours, target$bytes, target$sha256)
  )
}
