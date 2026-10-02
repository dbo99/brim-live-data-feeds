#!/usr/bin/env Rscript
# Ordinary local supervisor. Python owns collection, Journals and admission.
argv <- commandArgs(trailingOnly = FALSE)
file_arg <- grep("^--file=", argv, value = TRUE)
if (length(file_arg) != 1L) stop("Invoke with Rscript --vanilla acquire_native.R", call. = FALSE)
self <- normalizePath(sub("^--file=", "", file_arg[[1]]), mustWork = TRUE)
scripts <- dirname(dirname(self))
python <- Sys.which("python3")
if (!nzchar(python)) stop("Installed python3 required; no installation performed", call. = FALSE)
Sys.setenv(PYTHONPATH = scripts, PYTHONDONTWRITEBYTECODE = "1",
           GIT_OPTIONAL_LOCKS = "0", GIT_NO_LAZY_FETCH = "1")
args <- c("-B", "-m", "dendra.history_acquisition.local_job", commandArgs(trailingOnly = TRUE))
status <- system2(python, args = vapply(args, shQuote, character(1)))
quit(status = status)
