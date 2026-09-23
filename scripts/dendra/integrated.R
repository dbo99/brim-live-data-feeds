# Integration/state policy. Numerical aggregation remains exclusively in core.R.
DENDRA_INTEGRATION <- "dendra-integration-1"
DENDRA_INDEX_SCHEMA <- "dendra-daily-1.1.0"
retention_contract <- function() list(soil_moisture=list(water_years=10),soil_temperature=list(completed_days=90))
integrated_catalog <- function(catalog) {
  if(is.null(catalog$integration))return(FALSE)
  if(!identical(catalog$integration$version,DENDRA_INTEGRATION)||!isTRUE(all.equal(catalog$integration$retention,retention_contract(),check.attributes=FALSE)))stop("Unknown integration/retention contract")
  TRUE
}
retention_start <- function(parameter,cutoff) {
  if(parameter=="soil_temperature")as.character(as.Date(cutoff)-89) else sprintf("%d-10-01",water_year(cutoff)-10)
}
preceding_cadence <- function(rows,start,initial=NULL) {
  before<-Filter(function(r)r$date<start&&r$n_total>0&&!is.null(r$cadence_seconds),rows)
  if(length(before))tail(before,1)[[1]]$cadence_seconds else initial
}
# The per-date sufficient source evidence survives trimming and reconciliation.
# No native observations are duplicated in durable state.
update_query_evidence <- function(previous,x,start,end,chunks) {
  out<-previous %||% list()
  for(day in as.character(seq(as.Date(start),as.Date(end)-1,by="day"))) {
    matching<-Filter(function(c)substr(c$requested_interval$start_inclusive,1,10)<=day&&substr(c$requested_interval$end_exclusive,1,10)>day,chunks)
    if(!length(matching)) {
      if(nrow(x)&&day<min(x$date)) {out[[day]]<-list(status="before_saved_source_start",latest_observation_utc=NULL,retrieved_at_utc=NULL,content_sha256=NULL);next}
      stop("No completed source query evidence for ",day)
    }
    c<-tail(matching,1)[[1]];obs<-x[x$date==day,,drop=FALSE]
    out[[day]]<-list(status="queried",latest_observation_utc=if(nrow(obs))tail(obs$t,1) else NULL,
      retrieved_at_utc=c$retrieval_last_utc,content_sha256=c$content_sha256)
  }
  out[order(names(out))]
}
trim_product <- function(rows,query,chunks,parameter,cutoff,initial_cadence=NULL) {
  start<-retention_start(parameter,cutoff)
  prior_cadence<-preceding_cadence(rows,start,initial_cadence)
  rows<-Filter(function(r)r$date>=start,rows)
  dates<-vapply(rows,`[[`,character(1),"date");query<-query[intersect(dates,names(query))]
  used<-unique(unlist(lapply(query,`[[`,"content_sha256"),use.names=FALSE))
  chunks<-Filter(function(c)c$content_sha256 %in% used,chunks)
  keys<-vapply(chunks,function(c)paste(c$content_sha256,c$requested_interval$start_inclusive,c$requested_interval$end_exclusive,c$retrieval_last_utc),character(1))
  chunks<-chunks[!duplicated(keys,fromLast=TRUE)]
  list(rows=rows,query=query,chunks=chunks,prior_cadence=prior_cadence)
}
retained_observation <- function(query) {
  times<-unlist(lapply(query,`[[`,"latest_observation_utc"),use.names=FALSE)
  if(length(times)) times[which.max(as.numeric(parse_utc(times)))] else NULL
}
