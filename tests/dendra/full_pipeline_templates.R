# SYNTHETIC fixture preparation. Actual core.R owns template aggregation and summaries.
a<-commandArgs(TRUE);stopifnot(length(a)==2)
args<-commandArgs(FALSE);here<-dirname(normalizePath(sub('^--file=','',args[grepl('^--file=',args)][1])))
source(file.path(here,'../../scripts/dendra/core.R'));source(file.path(here,'../../scripts/dendra/integrated.R'))
input<-a[1];output<-a[2];dir.create(output,recursive=TRUE);now<-'2026-09-21T12:00:00Z';cutoff<-'2026-09-19'
for(v in 0:3) {
 p<-json_read(file.path(input,if(v%%2==0)'deep.json' else 'granites.json'));s<-p$stream;ctx<-p$cadence_context
 make<-function(day,kind) {
  t<-seq(as.numeric(parse_utc(paste0(day,'T08:00:00Z'))),length.out=144,by=600)
  x<-data.frame(t=format(as.POSIXct(t,origin='1970-01-01',tz='UTC'),'%Y-%m-%dT%H:%M:%SZ',tz='UTC'),datastream_id=s$datastream_id,v=rep(if(kind=='zero')0 else if(kind=='range')1.01 else .2,length(t)),value_status='number',duplicate_conflict=FALSE,alternative_out_of_range=FALSE,time=t,date=day)
  if(kind=='empty')x<-x[FALSE,] else if(kind=='sparse')x<-x[1:10,] else if(kind=='null'){x$v[1]<-NA;x$value_status[1]<-'null'}
  aggregate_daily(x,s,day,as.character(as.Date(day)+1),ctx,600)$rows[[1]]
 }
 p$rows<-c(p$rows,list(make(cutoff,if(v%%2==0)'good' else 'empty')))
 if(v>=2) {
  for(wy in 2017:2026)for(pair in list(c('02-01','zero'),c('02-02','sparse'),c('02-03','range'),c('02-04','empty'),c('02-05','null'))) {
   day<-paste0(wy,'-',pair[1]);pos<-match(day,vapply(p$rows,`[[`,character(1),'date'));p$rows[[pos]]<-make(day,pair[2])
  }
 }
 if(v==3)p$rows<-Filter(function(r)r$date!='2018-06-30',p$rows) # explicit synthetic unqueried day, not padded
 prior<-p$aggregation$prior_observed_cadence_seconds
 for(j in seq_along(p$rows)) {
  r<-p$rows[[j]];r$flags<-Filter(function(f)f!='cadence_changed_from_previous_observed_day',r$flags)
  if(r$n_total>0&&!is.null(r$cadence_seconds)){if(!is.null(prior)&&abs(prior-r$cadence_seconds)>1)r$flags<-c(r$flags,list('cadence_changed_from_previous_observed_day'));prior<-r$cadence_seconds};p$rows[[j]]<-r
 }
 p$generated_at_utc<-now;p$aggregation$complete_through_date<-cutoff;p$aggregation$end_date_exclusive<-'2026-09-20';p$summary<-daily_summary(p$rows)
 p$source_snapshot<-list(chunks=list(),native_row_count=sum(vapply(p$rows,function(r)as.numeric(r$n_total),numeric(1))),query_by_date=list())
 intervals<-if(v==3)list(c('2016-10-01','2018-06-30'),c('2018-07-01','2026-09-20')) else list(c('2016-10-01','2026-09-20'))
 for(b in intervals) {
  hash<-digest::digest(paste(v,b,collapse=':'),algo='sha256',serialize=FALSE);covered<-Filter(function(r)r$date>=b[1]&&r$date<b[2],p$rows);observed<-Filter(function(r)r$n_total>0,covered)
  latest<-if(length(observed))paste0(as.Date(tail(observed,1)[[1]]$date)+1,'T07:50:00Z') else NULL
  p$source_snapshot$chunks<-c(p$source_snapshot$chunks,list(list(requested_interval=list(start_inclusive=paste0(b[1],'T08:00:00Z'),end_exclusive=paste0(b[2],'T08:00:00Z')),content_sha256=hash,retrieval_last_utc=now,latest_observation_utc=latest)))
  for(r in covered)p$source_snapshot$query_by_date[r$date]<-list(list(status='queried',retrieved_at_utc=now,content_sha256=hash,latest_observation_utc=if(r$n_total>0)paste0(as.Date(r$date)+1,'T07:50:00Z') else NULL))
 }
 project<-function(r)list(date=r$date,v=r$mean_value,ok=r$plot_eligible,wy=r$water_year,dowy=r$dowy,x=r$water_day_aligned,n=r$n_valid,expected=r$expected_samples,coverage=r$coverage_fraction,span=r$temporal_span_fraction,flags=r$flags)
 good<-Filter(function(r)isTRUE(r$plot_eligible),p$rows);summary<-list(cadence_context=p$cadence_context,summary=p$summary,acquired_through_date=cutoff,processed_at_utc=now,last_retrieved_at_utc=now,latest_observation_utc=retained_observation(p$source_snapshot$query_by_date),current_interval_retrieved_at_utc=now,expires_at_utc=NULL,change=setNames(lapply(c(7,14,30),function(n)recent_change(p$rows,cutoff,n)),c('7','14','30')),recent_rows=lapply(Filter(function(r)r$date>=as.character(as.Date(cutoff)-59),p$rows),project),latest_accepted=if(length(good))project(tail(good,1)[[1]]) else NULL)
 json_write(p,file.path(output,paste0(v,'.json')));json_write(summary,file.path(output,paste0(v,'-summary.json')))
}
cat('Four synthetic templates from accepted daily records + bounded core.R examples; no provider requests\n')
