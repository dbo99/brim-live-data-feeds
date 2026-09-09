#!/usr/bin/env Rscript
# Offline regression for percentile crossings introduced by GRIB repacking.
# Fixtures are synthetic 3x3 GUST percentile grids at 1-degree spacing,
# starting at (-120, 33), with a synthetic 2025-01-01 00Z reference time.
# In WE:SN order p10 = c(0,1,2,1,2,3,2,3,4); p50 differs only at the
# first cell (0.1). They use complex3 packing with scale -1,0, as in QMD.
# No NOAA forecast values or network retrieval are needed by this test.

main <- function() {
  builder <- "scripts/build_nbm_wind_guidance_latest.R"
  fixtures <- normalizePath("tests/fixtures/nbm_wind", mustWork = TRUE)
  work <- tempfile("nbm-wind-regrid-")
  dir.create(work)
  on.exit(unlink(work, recursive = TRUE), add = TRUE)

  # Load only the actual functions under test. Sourcing the builder would
  # initialize output paths, install packages and fetch live NOAA products.
  wanted <- c("%||%", "nbm_run", "nbm_prepare_regrid_input", "nbm_regrid_to_csv")
  scope <- new.env(parent = baseenv())
  loaded <- character()
  for (expr in parse(builder)) {
    if (is.call(expr) && identical(expr[[1]], as.name("<-")) &&
        is.symbol(expr[[2]]) &&
        as.character(expr[[2]]) %in% wanted) {
      eval(expr, envir = scope)
      loaded <- c(loaded, as.character(expr[[2]]))
    }
  }
  stopifnot(setequal(loaded, wanted))
  scope$nbm_wgrib2 <- Sys.which("wgrib2")
  if (!nzchar(scope$nbm_wgrib2)) stop("wgrib2 3.8.* must be on PATH")
  scope$nbm_log <- function(...) invisible(NULL)
  scope$nbm_domain <- list(
    west = -119.75, south = 33.25, nx = 4L, ny = 4L,
    resolution_degrees = 0.5
  )

  read_values <- function(path) {
    table <- utils::read.csv(path, header = FALSE)
    stopifnot(nrow(table) == 16L, ncol(table) == 7L)
    stopifnot(all(is.finite(table[[7]])))
    stopifnot(isTRUE(all.equal(table[[5]], rep(c(-119.75, -119.25, -118.75, -118.25), 4))))
    stopifnot(isTRUE(all.equal(table[[6]], rep(c(33.25, 33.75, 34.25, 34.75), each = 4))))
    table[[7]]
  }

  regrid <- function(percentile, method) {
    path <- scope$nbm_regrid_to_csv(
      file.path(fixtures, paste0("regrid_", percentile, ".grib2")),
      file.path(work, paste0(percentile, "_", method)), method
    )
    read_values(path)
  }

  # Independent bilinear values on the half-degree destination grid. The
  # original scaled-packing path corrupts even cells where p10 equals p50.
  expected10 <- as.vector(outer(c(0.25, 0.75, 1.25, 1.75),
                               c(0.25, 0.75, 1.25, 1.75), "+"))
  expected50 <- expected10
  expected50[c(1, 2, 5, 6)] <- expected50[c(1, 2, 5, 6)] +
    c(0.05625, 0.01875, 0.01875, 0.00625)
  p10 <- regrid("p10", "bilinear")
  p50 <- regrid("p50", "bilinear")
  stopifnot(max(abs(p10 - expected10)) < 0.00001)
  stopifnot(max(abs(p50 - expected50)) < 0.00001)
  stopifnot(all(p10 <= p50), all(p10 >= 0))
  stopifnot(all(round(p10 * 2.2369362921, 1) <=
                round(p50 * 2.2369362921, 1)))
  message("PASS: bilinear values and percentile ordering survive mph serialization")

  # Direction uses this same function with nearest-neighbor interpolation.
  expected_neighbor <- as.vector(outer(c(0, 1, 1, 2), c(0, 1, 1, 2), "+"))
  stopifnot(isTRUE(all.equal(regrid("p10", "neighbor"), expected_neighbor,
                             tolerance = 0)))
  message("PASS: nearest-neighbor values are preserved")
}

main()
