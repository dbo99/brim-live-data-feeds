# Offline adapter only. All numerical/date/cadence/QC functions come from core.R.
args <- commandArgs(trailingOnly=TRUE)
if(length(args)!=2L) stop("Expected explicit handoff and fresh output paths")
script <- sub("^--file=", "", grep("^--file=", commandArgs(), value=TRUE)[1])
core <- file.path(dirname(normalizePath(script)), "..", "core.R")
source(core)
input <- normalizePath(args[1], mustWork=TRUE)
output <- args[2]
if(file.exists(output) || dir.exists(output)) stop("Fresh output required")
h <- json_read(input)
if(!h$schema_version %in% c("dendra-sealed-daily-handoff-1","dendra-sealed-daily-handoff-2","dendra-sealed-daily-handoff-3") ||
   !identical(h$source_scope,"historical_sealed_intervals") ||
   !identical(h$cadence_mode,"initialize") ||
   !identical(h$science_binding$core_sha256,sha_file(core)) ||
   !identical(h$science_binding$wrapper_sha256,sha_file(normalizePath(script)))) stop("Handoff/science binding differs")
cutoff <- completed_cutoff(h$as_of)
base <- dirname(input)
rows_out <- list(); summaries <- list(); contexts <- list(); quality_out <- list()
for(s in h$streams) {
  sid <- s$identity$stream_id
  if(!grepl("^[0-9a-f]{24}$",sid) || !identical(s$csv$path,paste0("native/",sid,".csv")) ||
     !identical(s$lineage$path,paste0("lineage/",sid,".json"))) stop("Confined stream input required")
  path <- file.path(base,s$csv$path)
  if(!identical(sha_file(path),s$csv$sha256) || file.info(path)$size!=s$csv$bytes ||
     !identical(sha_file(file.path(base,s$lineage$path)),s$lineage$sha256)) stop("Handoff bytes differ")
  x <- read_native(path)
  if(!identical(names(x)[1:6],c("t","datastream_id","v","value_status","duplicate_conflict","alternative_out_of_range"))) stop("Native columns differ")
  if(identical(s$daily_route,"native_only")) {
    summaries[[sid]] <- list(route="native_only",native_rows=nrow(x),normalized_percent_status="NORMALIZED_PERCENT_HOLD",
                            calendar_row_count=0,eligible_row_count=0,covered_empty=sum(vapply(s$intervals,function(i)identical(i$query_state,"COVERED_EMPTY"),logical(1))))
    next
  }
  if(!identical(s$daily_route,"completed_daily") || !identical(s$unit_normalization$status,"verified_percent_conversion")) stop("Daily route differs")
  expected_factor <- switch(s$identity$native_unit,Percent=1,VolumetricWaterContent=100,NULL)
  if(is.null(expected_factor) || s$unit_normalization$multiplier!=expected_factor ||
     s$unit_normalization$offset!=0) stop("Unsupported baseline conversion")
  stream <- list(datastream_id=sid,parameter="soil_moisture",unit_normalization=s$unit_normalization,
                 cadence_seconds=s$configured_cadence_seconds)
  x <- normalize_native(x,stream)
  context <- cadence_context(x,stream,s$csv$sha256)
  withheld <- character()
  if(h$schema_version %in% c("dendra-sealed-daily-handoff-2","dendra-sealed-daily-handoff-3")) {
    if(!identical(s$quarantine$policy$policy$version,"dendra-observation-quality-1")) stop("Quality policy differs")
    withheld <- unlist(s$quarantine$withheld_days,use.names=FALSE)
    if(length(withheld) && (anyDuplicated(withheld) || any(!grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2}$",withheld)))) stop("Invalid withheld dates")
  }
  start <- min(vapply(s$intervals,function(i)as.character(as.Date(parse_utc(i$start)-28800,tz="UTC")),character(1)))
  last_end <- max(vapply(s$intervals,function(i)as.numeric(parse_utc(i$end)),numeric(1)))
  end_date <- as.Date(as.POSIXct(last_end-28800,origin="1970-01-01",tz="UTC"),tz="UTC")
  if(last_end > as.numeric(as.POSIXct(paste0(end_date," 08:00:00"),tz="UTC"))) end_date <- end_date+1
  end <- min(end_date,as.Date(cutoff)+1)
  if(as.Date(start)>end) start <- as.character(end)
  # Preserve the original timestamp-based cadence evidence. Quarantined days
  # supply no observations to numerical aggregation, not fabricated zero/null
  # samples. Carry cadence from their real timestamps across the day boundary.
  result <- list(rows=list()); previous <- NULL
  by_day <- split(seq_len(nrow(x)),x$date)
  if(as.Date(start)<end) for(day in as.character(seq(as.Date(start),end-1,by="day"))) {
    obs <- x[by_day[[day]] %||% integer(),,drop=FALSE]; held <- day %in% withheld
    one <- aggregate_daily(if(held) obs[FALSE,,drop=FALSE] else obs,stream,day,
                           as.character(as.Date(day)+1),context,previous)
    row <- one$rows[[1]]
    if(held) {
      dm <- mode_interval(round(diff(obs$time),3))
      cadence <- if(!is.null(dm)) dm$seconds else context$seconds
      if(nrow(obs)&&!is.null(cadence)) previous <- cadence
      row$cadence_seconds <- cadence
      row$cadence_source <- if(!is.null(dm)) "observed_day_mode" else context$source
      row$expected_samples <- if(!is.null(cadence)) 86400/cadence else NULL
      row$n_total <- nrow(obs); row$n_valid <- 0L
      for(status in c("null","missing","invalid")) row[[paste0("n_",status)]] <- sum(!obs$duplicate_conflict & obs$value_status==status)
      row$n_duplicate_conflicts <- sum(obs$duplicate_conflict); row$n_duplicate_rows <- sum(obs$duplicate_rows)
      row$flags <- as.list(c("provider_quality_unreviewed"))
      row$plot_eligible <- FALSE
    } else previous <- one$previous_cadence
    result$rows[[length(result$rows)+1L]] <- row
  }
  enriched <- lapply(result$rows,function(row) {
    lo <- as.numeric(as.POSIXct(paste0(row$date," 08:00:00"),tz="UTC")); hi <- lo+86400
    contributors <- Filter(function(i)as.numeric(parse_utc(i$start))<hi && as.numeric(parse_utc(i$end))>lo,s$intervals)
    cursor <- lo
    for(i in contributors) {
      a<-as.numeric(parse_utc(i$start));b<-as.numeric(parse_utc(i$end))
      if(a<=cursor && b>cursor) cursor<-b
    }
    c(row,list(representation="completed_daily",identity=s$identity,query_complete=cursor>=hi,
      presentation_eligible=isTRUE(row$plot_eligible)&&cursor>=hi,
      source_intervals=lapply(contributors,function(i)i[c("task_id","query_state","seal_record_sha256","content_sha256","parsed_sha256")])))
  })
  rows_out[[sid]] <- enriched
  quality_out[[sid]] <- lapply(Filter(function(r)r$date %in% withheld,enriched),function(r)
    list(date=r$date,state=if(isTRUE(r$query_complete)) "DAILY_VALUE_WITHHELD" else "QUERY_INCOMPLETE",
         reason="PROVIDER_QUALITY_UNREVIEWED"))
  summaries[[sid]] <- c(list(route="completed_daily",native_rows=nrow(x)),daily_summary(result$rows),
                       list(rejected_row_count=sum(!vapply(result$rows,function(r)isTRUE(r$plot_eligible),logical(1)))))
  contexts[[sid]] <- context
}
json_write(list(schema_version=h$schema_version,daily_schema=DENDRA_SCHEMA,numerical_policy=DENDRA_POLICY,
  science_binding=h$science_binding,as_of=h$as_of,completed_fixed_pst_cutoff=cutoff,
  rows=rows_out,summaries=summaries,cadence_contexts=contexts,quality_disposition=quality_out,latest_instantaneous=list(),
  publication_eligible=FALSE,reference_band="not_computed"),output)
