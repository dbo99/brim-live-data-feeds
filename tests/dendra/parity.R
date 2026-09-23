a<-commandArgs(TRUE);if(length(a)!=4)stop('parity.R native_manifest generation reference_daily output_json')
allargs<-commandArgs(FALSE);here<-dirname(normalizePath(sub('^--file=','',allargs[grepl('^--file=',allargs)][1])))
source(file.path(here,'../../scripts/dendra/core.R'))
native<-json_read(a[1]);results<-list();maxerr<-0
for(item in native$streams) {
 sid<-item$stream$datastream_id;built<-json_read(file.path(a[2],'daily',paste0(sid,'.json')));ref<-json_read(file.path(a[3],paste0(sid,'.json')))
 stopifnot(identical(built$stream$datastream_id,ref$stream$datastream_id),length(built$rows)==length(ref$rows))
 errors<-list();nnum<-0L;err<-0
 for(k in seq_along(ref$rows)) {
  br<-built$rows[[k]];rr<-ref$rows[[k]]
  for(key in names(rr)) {
   if(!key %in% names(br))stop(sid," ",rr$date," missing ",key)
   b<-br[[key]];r<-rr[[key]]
   if(is.numeric(r)&&is.numeric(b)) {if(!finite_number(r)||!finite_number(b))stop("Nonfinite/nonscalar parity input");e<-abs(r-b);err<-max(err,e);nnum<-nnum+1L;if(e>1e-9)errors[[length(errors)+1L]]<-list(date=rr$date,key=key,reference=r,actual=b)}
   else if(!identical(r,b))errors[[length(errors)+1L]]<-list(date=rr$date,key=key,reference=r,actual=b)
  }
 }
 cat(sid,length(built$rows),'rows, max numeric error',format(err,digits=17),'differences',length(errors),'\n')
 # Partition and incremental runs receive the same persisted context and previous observed cadence.
 x<-normalize_native(read_native(item$native_csv),item$stream);begin<-built$aggregation$start_date;end<-built$aggregation$end_date_exclusive
 months<-sort(unique(substr(vapply(built$rows,`[[`,character(1),'date'),1,7)));monthly<-list();prev<-NULL
 for(m in months) {
  start<-max(begin,paste0(m,'-01'));nextmonth<-as.character(seq(as.Date(paste0(m,'-01')),by='month',length.out=2)[2]);stopdate<-min(end,nextmonth)
  r<-aggregate_daily(x[x$date>=start & x$date<stopdate,,drop=FALSE],item$stream,start,stopdate,built$cadence_context,prev);monthly<-c(monthly,r$rows);prev<-r$previous_cadence
 }
 monthok<-identical(built$rows,monthly)
 # JSON turns integral numeric values into integers; compare serialized values, not R storage type.
 equivalent<-function(u,v) isTRUE(all.equal(u,v,tolerance=1e-12,check.attributes=FALSE))
 monthok<-equivalent(built$rows,monthly)
 starts<-seq(as.Date(begin),as.Date(end)-1,by=7);parts<-list();prev<-NULL
 for(d in as.character(starts)) {
  stopdate<-min(end,as.character(as.Date(d)+7));r<-aggregate_daily(x[x$date>=d & x$date<stopdate,,drop=FALSE],item$stream,d,stopdate,built$cadence_context,prev);parts<-c(parts,r$rows);prev<-r$previous_cadence
 }
 incrementalok<-equivalent(built$rows,parts)
 results[[sid]]<-list(rows=length(ref$rows),eligible_rows=sum(vapply(built$rows,function(r)r$plot_eligible,logical(1))),numeric_fields_compared=nnum,max_absolute_error=err,differences=errors,monthly_invariant=monthok,weekly_incremental_invariant=incrementalok)
 maxerr<-max(maxerr,err)
}
out<-list(tolerance=1e-9,streams=results,max_absolute_error=maxerr,passed=all(vapply(results,function(r)!length(r$differences)&&r$monthly_invariant&&r$weekly_incremental_invariant,logical(1))))
json_write(out,a[4]);stopifnot(out$passed);cat('Every row/field and partition comparison passed\n')
