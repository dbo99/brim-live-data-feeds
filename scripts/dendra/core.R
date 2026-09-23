# Dendra numerical authority. All daily and window arithmetic lives here.
DENDRA_POLICY <- "dendra-daily-1.0.0-frozen-cadence"
DENDRA_SCHEMA <- "dendra-daily-1.0.0"
`%||%` <- function(x,y) if(is.null(x)) y else x
finite_number <- function(x) is.numeric(x) && length(x)==1L && is.finite(x)
json_read <- function(p) jsonlite::fromJSON(p, simplifyVector=FALSE)
json_write <- function(x,p) {
  dir.create(dirname(p), recursive=TRUE, showWarnings=FALSE)
  jsonlite::write_json(x,p,auto_unbox=TRUE,null="null",na="null",digits=NA,pretty=FALSE)
}
sha_file <- function(p) digest::digest(file=p,algo="sha256",serialize=FALSE)
parse_utc <- function(x) {
  if(any(!grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?Z$",x))) stop("Explicit UTC timestamp required")
  y <- as.POSIXct(x,format="%Y-%m-%dT%H:%M:%OSZ",tz="UTC")
  if(anyNA(y)) stop("Invalid UTC time")
  y
}
completed_cutoff <- function(asof) as.character(as.Date(parse_utc(asof)-8*3600,tz="UTC")-1)
water_year <- function(d) as.integer(format(as.Date(d),"%Y")) + as.integer(format(as.Date(d),"%m")>="10")
mode_interval <- function(x) {
  x <- round(x[x>0 & x<=86400],3)
  if(!length(x)) return(NULL)
  tab <- table(x); m <- max(tab)
  if(m<3 || sum(tab==m)!=1 || m/sum(tab)<0.5) return(NULL)
  list(seconds=as.numeric(names(tab)[which.max(tab)]),occurrences=as.integer(m),interval_share=m/sum(tab))
}
read_native <- function(path) {
  x <- read.csv(path,stringsAsFactors=FALSE,na.strings="",colClasses=c("character","character","numeric","character","logical","logical"))
  if(nrow(x)) { x$time <- as.numeric(parse_utc(x$t)); x$date <- as.character(as.Date(as.POSIXct(x$time-28800,origin="1970-01-01",tz="UTC"),tz="UTC")) }
  else {x$time<-numeric();x$date<-character()}
  x
}
normalize_native <- function(x,stream) {
  if(!nrow(x)) {x$duplicate_rows<-integer(); return(x)}
  if(any(x$datastream_id != stream$datastream_id)) stop("Mixed stream identities")
  if(any(!x$value_status %in% c("number","null","missing","invalid"))) stop("Invalid normalized value status")
  x$value_status[x$value_status=="number" & !is.finite(x$v)] <- "invalid"
  x<-x[order(x$time),,drop=FALSE]; if(!"duplicate_rows" %in% names(x)) x$duplicate_rows<-0L
  if(anyDuplicated(x$time)) {
    groups<-split(seq_len(nrow(x)),x$time)
    for(g in groups[lengths(groups)>1]) {
      signature<-paste(x$value_status[g],ifelse(x$value_status[g]=="number",sprintf("%.17g",x$v[g]),""))
      x$duplicate_conflict[g[1]]<-any(x$duplicate_conflict[g]) || length(unique(signature))>1
      x$alternative_out_of_range[g[1]]<-any(x$alternative_out_of_range[g])
      conv<-stream$unit_normalization
      if(identical(conv$status,"verified_percent_conversion")) {
        vals<-x$v[g]*conv$multiplier+(conv$offset %||% 0)
        x$alternative_out_of_range[g[1]]<-x$alternative_out_of_range[g[1]] || any(is.finite(vals) & (vals<0|vals>100))
      }
      x$duplicate_rows[g[1]]<-sum(x$duplicate_rows[g])+length(g)-1L
    }
    x<-x[!duplicated(x$time),,drop=FALSE]
  }
  x
}
cadence_context <- function(x,stream,evidence_sha) {
  intervals<-unlist(lapply(split(x$time,x$date),diff),use.names=FALSE)
  m<-mode_interval(intervals)
  configured<-stream$cadence_seconds %||% ((stream$cadence_evidence$configured_interval_ms_from_seed %||% NA_real_)/1000)
  if(!finite_number(configured) || configured<=0 || configured>86400) configured<-NULL
  list(version="frozen-cadence-context-1",seconds=if(!is.null(m)) m$seconds else configured,
       source=if(!is.null(m)) "observed_stream_mode" else if(!is.null(configured)) "configured_stream_interval" else NULL,
       observed_stream_mode=m,configured_seconds=configured,evidence_sha256=evidence_sha,
       note="Pinned at explicit initialization; never re-estimated from an update or retained history window.")
}
aggregate_daily <- function(x,stream,start,end,context,previous_cadence=NULL) {
  start<-as.Date(start);end<-as.Date(end)
  if(end<start) stop("Invalid date interval")
  if(is.null(context) || !identical(context$version,"frozen-cadence-context-1")) stop("A versioned cadence context is required")
  x<-normalize_native(x,stream);x<-x[x$date>=as.character(start) & x$date<as.character(end),,drop=FALSE]
  groups<-split(seq_len(nrow(x)),x$date)
  conv<-stream$unit_normalization %||% list();parameter<-stream$parameter %||% "soil_moisture"
  verified<-identical(conv$status,if(parameter=="soil_temperature") "verified_temperature_conversion" else "verified_percent_conversion")
  if(verified && (!finite_number(conv$multiplier)||conv$multiplier<=0||!finite_number(conv$offset %||% 0))) stop("Invalid unit conversion")
  if(!parameter %in% c("soil_moisture","soil_temperature")) stop("Parameter not implemented")
  if(start==end) return(list(rows=list(),previous_cadence=previous_cadence))
  dates<-seq(start,end-1,by="day");out<-vector("list",length(dates))
  for(k in seq_along(dates)) {
    day<-dates[k];date<-as.character(day);idx<-groups[[date]] %||% integer();obs<-x[idx,,drop=FALSE]
    valid<-obs[!obs$duplicate_conflict & obs$value_status=="number",,drop=FALSE]
    native<-if(nrow(valid)) mean(valid$v) else NULL
    converted<-if(!is.null(native)&&verified) native*conv$multiplier+(conv$offset %||% 0) else NULL
    if(!is.null(converted)&&!is.finite(converted)) converted<-NULL
    value<-if(verified) converted else native
    intervals<-round(diff(obs$time),3);dm<-mode_interval(intervals)
    cadence<-if(!is.null(dm)) dm$seconds else context$seconds
    cadence_source<-if(!is.null(dm)) "observed_day_mode" else context$source
    expected<-if(!is.null(cadence)) 86400/cadence else NULL
    coverage<-if(!is.null(expected)) min(1,nrow(valid)/expected) else NULL
    span<-if(nrow(valid)>1) (tail(valid$time,1)-valid$time[1])/86400 else 0
    irregular<-FALSE;change<-FALSE
    if(length(intervals)&&!is.null(cadence)) {
      mult<-round(intervals/cadence);grid<-mult>=1 & abs(intervals-mult*cadence)<=1
      irregular<-any(!grid);slower<-ifelse(grid & mult>1,mult,0);rr<-rle(slower)
      change<-any(rr$values>1 & rr$lengths>=3)
    }
    counts<-vapply(c("null","invalid","missing"),function(s)sum(!obs$duplicate_conflict & obs$value_status==s),integer(1))
    conflicts<-sum(obs$duplicate_conflict);duplicates<-sum(obs$duplicate_rows)
    oor<-rep(FALSE,nrow(obs))
    if(verified && parameter=="soil_moisture") {
      z<-obs$v*conv$multiplier+(conv$offset %||% 0)
      oor<-(obs$value_status=="number" & (!is.finite(z)|z<0|z>100)) | obs$alternative_out_of_range
    }
    flags<-character()
    if(!nrow(obs)) flags<-c(flags,"no_observations") else if(!nrow(valid)) flags<-c(flags,"no_valid_numeric_values")
    for(s in names(counts)) if(counts[s]>0) flags<-c(flags,paste0(s,"_values_present"))
    if(duplicates) flags<-c(flags,"duplicate_rows_removed")
    if(conflicts) flags<-c(flags,"duplicate_timestamp_conflicts")
    if(any(oor)) flags<-c(flags,"out_of_range_values")
    if(!verified) flags<-c(flags,if(parameter=="soil_moisture") "percent_scale_unresolved" else "temperature_scale_unresolved")
    if(is.null(cadence)) flags<-c(flags,"cadence_unresolved")
    if(irregular) flags<-c(flags,"irregular_cadence")
    if(change) flags<-c(flags,"possible_within_day_cadence_change")
    if(!is.null(coverage)&&coverage+1e-12<.75) flags<-c(flags,"insufficient_sample_coverage")
    if(span+1e-12<.75) flags<-c(flags,"insufficient_temporal_span")
    if(!is.null(native)&&is.null(value)) flags<-c(flags,"nonfinite_converted_mean")
    if(nrow(obs)&&!is.null(cadence)) {
      if(!is.null(previous_cadence)&&abs(cadence-previous_cadence)>1) flags<-c(flags,"cadence_changed_from_previous_observed_day")
      previous_cadence<-cadence
    }
    eligible<-daily_eligible(list(n_valid=nrow(valid),mean_value=value,cadence_seconds=cadence,coverage_fraction=coverage,temporal_span_fraction=span,flags=as.list(flags),n_duplicate_conflicts=conflicts,n_out_of_range=sum(oor)),verified)
    wy<-water_year(day);ws<-as.Date(sprintf("%d-10-01",wy-1));aligned<-as.Date(paste0(if(format(day,"%m")>="10") "1999" else "2000",format(day,"-%m-%d")))
    dowy<-as.integer(day-ws)+1L
    out[[k]]<-list(date=date,water_year=wy,dowy=dowy,water_day=dowy,water_year_days=as.integer(as.Date(sprintf("%d-10-01",wy))-ws),water_day_aligned=as.integer(aligned-as.Date("1999-10-01"))+1L,
      mean_native=native,mean_percent=if(parameter=="soil_moisture") converted else NULL,mean_value=value,
      n_valid=nrow(valid),n_total=nrow(obs),n_null=unname(counts["null"]),n_invalid=unname(counts["invalid"]),n_missing=unname(counts["missing"]),n_duplicate_conflicts=conflicts,n_duplicate_rows=duplicates,n_out_of_range=sum(oor),
      expected_samples=expected,cadence_seconds=cadence,cadence_source=cadence_source,coverage_fraction=coverage,temporal_span_fraction=span,plot_eligible=eligible,flags=as.list(flags))
  }
  list(rows=out,previous_cadence=previous_cadence)
}
daily_eligible <- function(r,verified) {
  verified && r$n_valid>0 && finite_number(r$mean_value) && !is.null(r$cadence_seconds) &&
    r$coverage_fraction+1e-12>=.75 && r$temporal_span_fraction+1e-12>=.75 &&
    !any(c("irregular_cadence","possible_within_day_cadence_change") %in% r$flags) &&
    r$n_duplicate_conflicts==0 && r$n_out_of_range==0
}
daily_summary <- function(rows) {
  numeric<-Filter(function(r)!is.null(r$mean_native),rows);good<-Filter(function(r)isTRUE(r$plot_eligible),rows)
  list(calendar_row_count=length(rows),eligible_row_count=length(good),mean_row_count=length(numeric),
    first_numeric_date=if(length(numeric))numeric[[1]]$date else NULL,last_numeric_date=if(length(numeric))tail(numeric,1)[[1]]$date else NULL,
    last_plottable_date=if(length(good))tail(good,1)[[1]]$date else NULL)
}
project_rows <- function(rows) lapply(rows,function(r) list(date=r$date,v=r$mean_percent,ok=r$plot_eligible,wy=r$water_year,dowy=r$dowy,x=r$water_day_aligned,n=r$n_valid,expected=r$expected_samples,coverage=r$coverage_fraction,span=r$temporal_span_fraction,flags=r$flags))
window_stats <- function(rows,start,end) {
  dates<-as.character(seq(as.Date(start),as.Date(end),by="day"))
  good<-Filter(function(r)isTRUE(r$plot_eligible)&&finite_number(r$mean_percent)&&r$mean_percent>=0&&r$mean_percent<=100&&r$date>=start&&r$date<=end,rows)
  missing<-!dates %in% vapply(good,`[[`,character(1),"date");rr<-rle(missing)
  list(start=start,end=end,n=length(good),expected=length(dates),mean=if(length(good)) mean(vapply(good,`[[`,numeric(1),"mean_percent")) else NULL,maxGap=if(any(rr$values)) max(rr$lengths[rr$values]) else 0L)
}
recent_change <- function(rows,cutoff,n) {
  if(!n %in% c(7,14,30)) stop("Unsupported window")
  good<-Filter(function(r)isTRUE(r$plot_eligible)&&finite_number(r$mean_percent)&&r$date<=cutoff,rows)
  last<-if(length(good)) tail(good,1)[[1]] else NULL;lag<-if(!is.null(last)) as.integer(as.Date(cutoff)-as.Date(last$date)) else NULL
  recent<-window_stats(rows,as.character(as.Date(cutoff)-n+1),cutoff);prior<-window_stats(rows,as.character(as.Date(cutoff)-2*n+1),as.character(as.Date(cutoff)-n))
  state<-if(is.null(last)) "no-data" else if(lag>3) "stale" else if(recent$n<ceiling(.8*n)||prior$n<ceiling(.8*n)||recent$maxGap>2||prior$maxGap>2) "insufficient" else "qualified"
  delta<-NULL
  if(state=="qualified") {delta<-recent$mean-prior$mean;state<-if(delta>.5) "wetting" else if(delta< -.5) "drying" else "little"}
  list(last=if(!is.null(last)) project_rows(list(last))[[1]] else NULL,lag=lag,recent=recent,prior=prior,span=n,deadband=.5,delta=delta,required=ceiling(.8*n),state=state)
}
feed_freshness <- function(mode,asof,generated,retrieved,now=format(Sys.time(),"%Y-%m-%dT%H:%M:%SZ",tz="UTC")) {
  if(mode=="replay") return(list(state="frozen-replay",current=FALSE,expires_at_utc=NULL))
  dates<-c(asof,generated,retrieved)
  if(length(dates)!=3 || anyNA(dates)) return(list(state="unavailable",current=FALSE,expires_at_utc=NULL))
  expiry<-min(parse_utc(dates))+3*86400
  list(state=if(parse_utc(now)<=expiry) "current" else "stalled-feed",current=parse_utc(now)<=expiry,expires_at_utc=format(expiry,"%Y-%m-%dT%H:%M:%SZ",tz="UTC"),evaluation_utc=now)
}
# Safeguard version is separate from the unchanged daily numerical policy.
DENDRA_SAFEGUARDS <- "dendra-safeguards-1.1.0"
validate_live_time <- function(asof,run_start) {
  age<-as.numeric(difftime(parse_utc(run_start),parse_utc(asof),units="secs"))
  if(age<0 || age>86400 || completed_cutoff(asof)>completed_cutoff(run_start))
    stop("Live as-of must be at or before run start and within 24 hours; unfinished days cannot be completed")
}
# Compare only dates retained in prior state. New missing dates never count as deletion.
replacement_assessment <- function(prior,replacement,start,end,sid="unspecified") {
  inside<-Filter(function(r)r$date>=start&&r$date<end,prior)
  lookup<-setNames(replacement,vapply(replacement,`[[`,character(1),"date"))
  counts<-lapply(inside,function(r) {
    z<-lookup[[r$date]] %||% list(n_total=0,plot_eligible=FALSE)
    list(date=r$date,prior_observations=r$n_total,replacement_observations=z$n_total,
      prior_eligible=as.integer(isTRUE(r$plot_eligible)),replacement_eligible=as.integer(isTRUE(z$plot_eligible)),
      removed_observations=max(0,r$n_total-z$n_total),removed_eligible=as.integer(isTRUE(r$plot_eligible)&&!isTRUE(z$plot_eligible)))
  })
  sumfield<-function(k)sum(vapply(counts,function(r)as.numeric(r[[k]]),numeric(1)))
  total<-list(prior_observations=sumfield("prior_observations"),replacement_observations=sumfield("replacement_observations"),
    prior_eligible=sumfield("prior_eligible"),replacement_eligible=sumfield("replacement_eligible"),
    removed_observations=sumfield("removed_observations"),removed_eligible=sumfield("removed_eligible"),
    supported_dates=sum(vapply(counts,function(r)r$prior_observations>0,logical(1))),
    affected_dates=sum(vapply(counts,function(r)r$removed_observations>0||r$removed_eligible>0,logical(1))))
  assessment<-c(list(datastream_id=sid,start=start,end_exclusive=end,retained_dates=length(inside),
    newly_appended_dates=sum(!vapply(replacement,`[[`,character(1),"date") %in% vapply(inside,`[[`,character(1),"date")),
    affected=lapply(Filter(function(r)r$removed_observations>0||r$removed_eligible>0,counts),identity)),total)
  list(assessment=assessment,rows=c(Filter(function(r)r$date<start,prior),replacement,Filter(function(r)r$date>=end,prior)))
}
loss_decision <- function(assessments) {
  fractions<-function(a) list(eligible_fraction=if(a$prior_eligible) a$removed_eligible/a$prior_eligible else 0,
                            observation_fraction=if(a$prior_observations) a$removed_observations/a$prior_observations else 0)
  streams<-lapply(assessments,function(a) {
    a<-c(a,fractions(a));a$hold_reasons<-list()
    if(a$removed_eligible>=2&&a$eligible_fraction>.5) a$hold_reasons<-c(a$hold_reasons,"more_than_half_prior_eligible_days_removed_minimum_two")
    if(a$supported_dates>=2&&a$affected_dates>=2&&a$removed_observations>=8&&a$observation_fraction>.5)
      a$hold_reasons<-c(a$hold_reasons,"more_than_half_prior_observations_removed_minimum_two_dates_eight_samples")
    a
  })
  total<-setNames(lapply(c("prior_observations","replacement_observations","prior_eligible","replacement_eligible","removed_observations","removed_eligible","supported_dates","affected_dates"),function(k)sum(vapply(streams,function(a)as.numeric(a[[k]]),numeric(1)))),c("prior_observations","replacement_observations","prior_eligible","replacement_eligible","removed_observations","removed_eligible","supported_dates","affected_dates"))
  total<-c(total,fractions(total));affected<-Filter(function(a)a$affected_dates>0,streams)
  total$affected_streams<-length(affected)
  selection_hold<-length(affected)>=2 && ((total$removed_eligible>=2&&total$eligible_fraction>.5)||
    (total$affected_dates>=2&&total$removed_observations>=16&&total$observation_fraction>.5))
  list(version=DENDRA_SAFEGUARDS,hold=selection_hold||any(vapply(streams,function(a)length(a$hold_reasons)>0,logical(1))),
       reason="Removal from retained dates; new missing dates excluded. Complete-empty is a source result, not an HTTP failure.",
       selection_hold=selection_hold,selection=total,streams=streams,
       affected_stream_ids=as.list(vapply(affected,`[[`,character(1),"datastream_id")))
}
replace_daily_interval <- function(prior,replacement,start,end) {
  a<-replacement_assessment(prior,replacement,start,end)
  if(loss_decision(list(a$assessment))$hold) stop("Suspicious mass loss: prior interval retained; explicit review required")
  a$rows
}
