#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(digest)
  library(httr2)
  library(jsonlite)
  library(png)
  library(terra)
})
source(file.path("scripts", "nbm_qpf_helpers.R"))

research_root <- Sys.getenv(
  "BRIM_NBM_QPF_RESEARCH_ROOT",
  unset = file.path(path.expand("~"), "Documents", "BRIM_NBM_QPF_research")
)
if (!dir.exists(research_root)) {
  cat("NBM QPF controlled-sample test skipped: research workspace is unavailable.\n")
  quit(status = 0L)
}

palette <- qpf_read_palette(file.path("data", "input", "nbm_qpf_palette.csv"))
samples <- data.frame(
  sample_id = c("dry_current", "wet_stress", "heavier_stress"),
  cycle_utc = c(
    "2026-08-17T06:00:00Z", "2025-02-13T00:00:00Z", "2023-01-09T00:00:00Z"
  ),
  lead_hours = c(6L, 24L, 18L),
  inventory_file = c(
    "blend.t06z.core.f006.co.grib2.idx",
    "blend.20250213.t00z.core.f024.co.grib2.idx",
    "blend.20230109.t00z.core.f018.co.grib2.idx"
  ),
  grib_file = c(
    "f006_apcp_0-6.grib2",
    "storm_2025021300_f024_apcp_18-24.grib2",
    "stress_2023010900_f018_apcp_12-18.grib2"
  ),
  expected_maximum_in = c(0.09478375667662, 3.397636713944, 4.513705095907),
  expected_webp_bytes = c(780, 17918, 17052),
  expected_webp_sha256 = c(
    "c66a8bc2bfca1a7c7b4475b4600cfd1622c11e856a86cde8b842de11af30d88a",
    "6f70e7f9170b805601a2150e72d30f94ffe62fd36266b1a86394f087d2511cc6",
    "2f7b56f037c0cf540c1ef4ce9f682d52ce5e763e56f0a2fc4c90ab6191a9ce56"
  ),
  stringsAsFactors = FALSE
)

output_root <- tempfile("nbm-qpf-controlled-samples-")
dir.create(output_root, recursive = TRUE)
on.exit(unlink(output_root, recursive = TRUE, force = TRUE), add = TRUE)
checks <- 0L

for (index in seq_len(nrow(samples))) {
  sample <- samples[index, ]
  inventory_path <- file.path(
    research_root, "source_samples", "inventories", sample$inventory_file
  )
  grib_path <- file.path(research_root, "source_samples", "grib_ranges", sample$grib_file)
  if (!all(file.exists(c(inventory_path, grib_path)))) {
    stop("Controlled NBM QPF sample is incomplete: ", sample$sample_id)
  }
  inventory_raw <- readBin(inventory_path, "raw", n = file.info(inventory_path)$size)
  inventory_text <- rawToChar(inventory_raw)
  record <- qpf_parse_index(
    inventory_text, sample$cycle_utc, sample$lead_hours
  )
  urls <- qpf_nbm_urls(sample$cycle_utc, sample$lead_hours)
  record$inventory_bytes <- length(inventory_raw)
  record$inventory_sha256 <- qpf_sha256_raw(inventory_raw)
  record$index_url <- urls$index
  record$grib_url <- urls$grib
  source <- list(
    path = grib_path,
    bytes = unname(file.info(grib_path)$size),
    sha256 = qpf_sha256_file(grib_path)
  )
  target_root <- file.path(output_root, sample$sample_id)
  dir.create(target_root, recursive = TRUE)
  built <- qpf_build_target(record, source, target_root, palette)
  if (abs(built$maximum_in - sample$expected_maximum_in) > 1e-12) {
    stop("Controlled QPF maximum changed for ", sample$sample_id)
  }
  if (built$target$bytes != sample$expected_webp_bytes ||
      !identical(built$target$sha256, sample$expected_webp_sha256)) {
    stop("Controlled lossless WebP bytes/hash changed for ", sample$sample_id)
  }
  if (!isTRUE(built$validation$source_metadata$authoritative_metadata_accepted) ||
      built$validation$output_grid$rows != 733L ||
      built$validation$output_grid$columns != 720L ||
      built$validation$output_grid$finite_coverage < 0.99 ||
      !isTRUE(built$validation$webp$lossless_vp8l)) {
    stop("Controlled source/spatial/WebP validation failed for ", sample$sample_id)
  }
  checks <- checks + 1L
}

cat("NBM QPF controlled real-sample tests passed:", checks, "samples\n")
