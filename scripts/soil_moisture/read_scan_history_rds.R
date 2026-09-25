#!/usr/bin/env Rscript
# Offline adapter only. Never sources a builder or loads provider packages.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2L) stop("usage: read_scan_history_rds.R INPUT.rds NEW_OUTPUT.csv")
if (file.exists(args[[2L]])) stop("output must not exist")
x <- readRDS(args[[1L]])
columns <- c("station_uid", "site_code", "date", "water_year", "water_day",
             "depth_in", "sms_pct", "sensor_count", "sensor_ids")
if (!is.data.frame(x) || !all(columns %in% names(x)) ||
    !inherits(x$date, "Date") || nrow(x) < 1L || nrow(x) > 2000000L) {
  stop("unexpected saved SCAN daily-history shape")
}
if (anyNA(x[columns]) || !all(is.finite(x$sms_pct))) stop("missing/nonfinite saved data")
# These are already daily same-depth composites. Do not average or round them.
options(digits = 17)
x$sms_pct <- sprintf("%.17g", x$sms_pct)
write.table(as.data.frame(x[columns]), args[[2L]], sep = ",", quote = TRUE,
            row.names = FALSE, na = "", fileEncoding = "UTF-8")
