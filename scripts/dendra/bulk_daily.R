# Local CSV evidence adapter. Numerical policy stays exclusively in core.R.
args <- commandArgs(TRUE)
if(length(args)!=2L || file.exists(args[2])) stop("Explicit input and fresh output required")
script <- sub("^--file=", "", grep("^--file=",commandArgs(),value=TRUE)[1])
core <- file.path(dirname(normalizePath(script)),"core.R")
source(core)
h <- json_read(args[1])
if(!identical(h$version,"dendra-bulk-daily-input-2") ||
   !identical(h$core_sha256,sha_file(core)) ||
   !identical(h$csv_sha256,sha_file(h$csv))) stop("Evidence/science binding differs")
factor <- switch(h$native_unit,Percent=1,VolumetricWaterContent=100,NULL)
if(is.null(factor)||factor!=h$multiplier||!grepl("^[a-f0-9]{24}$",h$stream_id)) stop("Unresolved unit/identity")
stream <- list(datastream_id=h$stream_id,parameter="soil_moisture",
               unit_normalization=list(status="verified_percent_conversion",multiplier=factor,offset=0))
x <- normalize_native(read_native(h$csv),stream)
if(nrow(x)>1500000) stop("Per-series adapter row bound exceeded")
context <- cadence_context(x,stream,h$csv_sha256)
groups <- split(seq_len(nrow(x)),x$date)
start <- as.Date(h$start); end <- as.Date(h$end)
if(end>as.Date(completed_cutoff(h$as_of))+1 || end<start) stop("Uncompleted date interval")
dates <- if(start<end) as.character(seq(start,end-1,by="day")) else character()
rows <- vector("list",length(dates)); previous <- NULL
for(k in seq_along(dates)) {
  day <- dates[k]; obs <- x[groups[[day]] %||% integer(),,drop=FALSE]
  evidence <- h$quality_days[[day]] %||% list(state="UNRESOLVED",reason="NO_MATCHED_API_QUALITY_EVIDENCE",query_complete=FALSE)
  held <- evidence$state %in% c("QUARANTINED","UNRESOLVED")
  one <- aggregate_daily(if(held) obs[FALSE,,drop=FALSE] else obs,stream,day,
                         as.character(as.Date(day)+1),context,previous)
  row <- one$rows[[1]]
  if(held) {
    # Same cadence preservation used by daily_prepare.R for quarantined days.
    dm <- mode_interval(round(diff(obs$time),3))
    cadence <- if(!is.null(dm)) dm$seconds else context$seconds
    if(nrow(obs)&&!is.null(cadence)) previous <- cadence
    row["cadence_seconds"] <- list(cadence)
    row["cadence_source"] <- list(if(!is.null(dm)) "observed_day_mode" else context$source)
    row["expected_samples"] <- list(if(!is.null(cadence)) 86400/cadence else NULL)
    row$n_total <- nrow(obs); row$n_valid <- 0L
    row$flags <- as.list(if(identical(evidence$state,"QUARANTINED")) "provider_quality_unreviewed" else "source_quality_semantics_unresolved")
    row$plot_eligible <- FALSE
  } else previous <- one$previous_cadence
  state <- if(identical(evidence$state,"QUARANTINED")) "WITHHELD_BY_EXISTING_SCREEN" else
    if(!nrow(obs)) "MISSING" else if(held) "UNRESOLVED_SEMANTICS" else
    if(isTRUE(row$plot_eligible)) "ACCEPTED" else "WITHHELD_BY_EXISTING_SCREEN"
  row$daily_status <- state
  row["daily_mean_vwc_percent"] <- list(if(identical(state,"ACCEPTED")) row$mean_percent else NULL)
  row$source_quality_status <- evidence$state
  row$source_quality_reason <- evidence$reason
  row$query_complete <- isTRUE(evidence$query_complete)
  row$source_empty <- isTRUE(evidence$query_complete) && !nrow(obs) && identical(evidence$state,"RESOLVED_CLEAR")
  row$observation_count <- nrow(obs)
  row["latest_source_timestamp_utc"] <- list(if(nrow(obs)) tail(obs$t,1) else NULL)
  row$nominal_range_observations <- if(nrow(obs)) sum(obs$v*factor<0|obs$v*factor>100,na.rm=TRUE) else 0L
  rows[[k]] <- row
}
json_write(list(version="dendra-bulk-daily-science-2",policy=DENDRA_POLICY,core_sha256=sha_file(core),
                context=context,rows=rows),args[2])
