# Local CSV evidence adapter. Numerical policy stays exclusively in core.R.
args <- commandArgs(TRUE)
if(length(args)!=2L || file.exists(args[2])) stop("Explicit input and fresh output required")
script <- sub("^--file=", "", grep("^--file=",commandArgs(),value=TRUE)[1])
core <- file.path(dirname(normalizePath(script)),"core.R")
source(core)
h <- json_read(args[1])
if(!h$version %in% c("dendra-bulk-daily-input-2","dendra-bulk-daily-input-3") ||
   !identical(h$core_sha256,sha_file(core)) ||
   !identical(h$csv_sha256,sha_file(h$csv))) stop("Evidence/science binding differs")
bulk_ready <- identical(h$version,"dendra-bulk-daily-input-3") &&
  identical(h$historical_bulk_policy,"dendra-historical-bulk-readytouse-1") &&
  identical(h$historical_source_route,"DENDRA_WEBSITE_HISTORICAL_BULK_EXPORT") &&
  identical(h$quality_admission_basis,"PROVIDER_READY_TO_USE")
if(identical(h$version,"dendra-bulk-daily-input-3") && !bulk_ready) stop("Historical bulk policy binding differs")
factor <- switch(h$native_unit,Percent=1,VolumetricWaterContent=100,NULL)
if(is.null(factor)||factor!=h$multiplier||!grepl("^[a-f0-9]{24}$",h$stream_id)) stop("Unresolved unit/identity")
stream <- list(datastream_id=h$stream_id,parameter="soil_moisture",
               unit_normalization=list(status="verified_percent_conversion",multiplier=factor,offset=0))
limits <- list(whole_rows=1500000,batch_rows=65536,day_rows=1500000,interval_bins=65536)
# Tests can lower these bounds; callers cannot increase production limits.
bounds <- h$adapter_bounds %||% limits
if(!identical(sort(names(bounds)),sort(names(limits))) || any(vapply(names(limits),function(k)
  !finite_number(bounds[[k]]) || bounds[[k]]<1 || bounds[[k]]!=floor(bounds[[k]]) || bounds[[k]]>limits[[k]],logical(1))))
  stop("Explicit adapter bounds invalid")
partitioned <- !is.null(h$native_rows) && h$native_rows>bounds$whole_rows
if(!is.null(h$native_rows) && (!finite_number(h$native_rows) || h$native_rows<0 ||
   h$native_rows!=floor(h$native_rows) || h$native_rows>50000000)) stop("Native input capacity exceeded")
if(partitioned && !bulk_ready) stop("Large-series path requires historical bulk admission")

# Transport batches are not science partitions. Keep each complete fixed UTC-08
# day (including timestamp duplicates across batches) together for the core.
day_reader <- function() {
  con <- file(h$csv,"r"); header <- readLines(con,n=1,warn=FALSE)
  if(!identical(header,"datastream_id,t,v,value_status,duplicate_conflict,alternative_out_of_range")) {
    close(con);stop("Native adapter CSV header differs")
  }
  block <- NULL; at <- 1L; last_time <- NULL
  stats <- list(raw_rows=0,normalized_rows=0,partitions=0,batches=0,
                largest_batch_rows=0,largest_day_raw_rows=0,largest_day_normalized_rows=0)
  blank <- function() {
    tc <- textConnection(header);on.exit(close(tc));normalize_native(read_native(tc),stream)
  }
  next_day <- function() {
    pieces <- list(); count <- 0; day <- NULL
    repeat {
      if(is.null(block) || at>nrow(block)) {
        lines <- readLines(con,n=bounds$batch_rows,warn=FALSE)
        if(!length(lines)) break
        if(any(nchar(lines,type="bytes")>1024)) stop("Native adapter line capacity exceeded")
        tc <- textConnection(c(header,lines)); block <<- tryCatch(read_native(tc),finally=close(tc))
        if(nrow(block)!=length(lines) || any(diff(block$time)<0) ||
           (!is.null(last_time) && block$time[1]<last_time)) stop("Native source order/count differs")
        last_time <<- tail(block$time,1);at <<- 1L
        stats$raw_rows <<- stats$raw_rows+nrow(block);stats$batches <<- stats$batches+1
        stats$largest_batch_rows <<- max(stats$largest_batch_rows,nrow(block))
      }
      if(is.null(day)) day <- block$date[at]
      if(block$date[at]!=day) break
      different <- which(block$date[seq.int(at,nrow(block))]!=day)
      stop_at <- if(length(different)) at+different[1]-2L else nrow(block)
      count <- count+stop_at-at+1L
      if(count>bounds$day_rows) stop("Complete-day adapter row capacity exceeded")
      pieces[[length(pieces)+1L]] <- block[at:stop_at,,drop=FALSE];at <<- stop_at+1L
    }
    if(is.null(day)) return(NULL)
    obs <- normalize_native(do.call(rbind,pieces),stream)
    stats$normalized_rows <<- stats$normalized_rows+nrow(obs);stats$partitions <<- stats$partitions+1
    stats$largest_day_raw_rows <<- max(stats$largest_day_raw_rows,count)
    stats$largest_day_normalized_rows <<- max(stats$largest_day_normalized_rows,nrow(obs))
    list(day=day,obs=obs)
  }
  list(next_day=next_day,blank=blank,stats=function() stats,close=function() close(con))
}

if(!partitioned) {
  x <- normalize_native(read_native(h$csv),stream)
  if(nrow(x)>bounds$whole_rows) stop("Per-series adapter row bound exceeded")
  context <- cadence_context(x,stream,h$csv_sha256)
  groups <- split(seq_len(nrow(x)),x$date)
} else {
  # cadence_context uses only within-day differences after normalization. Summing
  # their bounded frequency tables is exactly its mode_interval sufficient state;
  # cross-midnight gaps must never enter this summary.
  first <- day_reader(); frequencies <- numeric();context <- NULL
  tryCatch({
    repeat {
      part <- first$next_day();if(is.null(part)) break
      if(is.null(context) && nrow(part$obs)>1) context <- cadence_context(part$obs,stream,h$csv_sha256)
      intervals <- diff(part$obs$time)
      intervals <- round(intervals[intervals>0 & intervals<=86400],3)
      tab <- table(intervals); keys <- union(names(frequencies),names(tab))
      if(length(keys)>bounds$interval_bins) stop("Cadence summary capacity exceeded")
      combined <- setNames(numeric(length(keys)),keys)
      combined[names(frequencies)] <- frequencies
      combined[names(tab)] <- combined[names(tab)]+as.numeric(tab)
      frequencies <- combined
    }
  },finally=first$close())
  first_stats <- first$stats()
  if(first_stats$raw_rows!=h$native_rows) stop("First-pass native row count differs")
  if(is.null(context)) stop("Core cadence context requires within-day intervals")
  m <- if(length(frequencies)) max(frequencies) else 0
  mode <- if(m>=3 && sum(frequencies==m)==1 && m/sum(frequencies)>=0.5)
    list(seconds=as.numeric(names(frequencies)[which.max(frequencies)]),occurrences=as.integer(m),interval_share=m/sum(frequencies)) else NULL
  reader <- day_reader()
  context["seconds"] <- list(if(!is.null(mode)) mode$seconds else context$configured_seconds)
  context["source"] <- list(if(!is.null(mode)) "observed_stream_mode" else if(!is.null(context$configured_seconds)) "configured_stream_interval" else NULL)
  context["observed_stream_mode"] <- list(mode)
  current <- reader$next_day();empty <- reader$blank()
}
start <- as.Date(h$start); end <- as.Date(h$end)
if(end>as.Date(completed_cutoff(h$as_of))+1 || end<start) stop("Uncompleted date interval")
dates <- if(start<end) as.character(seq(start,end-1,by="day")) else character()
rows <- vector("list",length(dates)); previous <- NULL
for(k in seq_along(dates)) {
  day <- dates[k]
  if(!partitioned) obs <- x[groups[[day]] %||% integer(),,drop=FALSE] else {
    while(!is.null(current) && current$day<day) current <- reader$next_day()
    obs <- if(!is.null(current) && current$day==day) current$obs else empty
  }
  evidence <- h$quality_days[[day]] %||% list(state="UNRESOLVED",reason="NO_MATCHED_API_QUALITY_EVIDENCE",query_complete=FALSE)
  if(!evidence$state %in% c("RESOLVED_CLEAR","QUARANTINED","UNRESOLVED","PROVIDER_READY_TO_USE")) stop("Unknown quality basis")
  if(identical(evidence$state,"PROVIDER_READY_TO_USE") && !bulk_ready) stop("Bulk quality requires historical bulk admission")
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
  if(partitioned && !is.null(current) && current$day==day) current <- reader$next_day()
}
result <- list(version="dendra-bulk-daily-science-2",policy=DENDRA_POLICY,core_sha256=sha_file(core),context=context,rows=rows)
if(partitioned) {
  while(!is.null(current)) current <- reader$next_day()
  second_stats <- reader$stats();reader$close()
  if(!identical(first_stats,second_stats)) stop("Bounded-pass accounting differs")
  result$resource_accounting <- list(design="two-pass-complete-fixed-UTC-08-days-1",bounds=bounds,
    first_pass=first_stats,second_pass=second_stats,interval_bins=length(frequencies),native_rows=h$native_rows)
}
json_write(result,args[2])
