# Diagnostic only. This policy does not replace any agency Daily field.
sm1_pilot_comparison <- function(hourly,daily,cutoff) {
 stopifnot(!anyDuplicated(vapply(hourly,function(r)r$date,'')),!anyDuplicated(vapply(daily,function(r)r$date,'')))
 first<-function(xs,k)if(length(xs))xs[[1]][[k]]else NULL
 dates<-sort(unique(vapply(hourly,function(r)substr(r$date,1,10),'')));dates<-dates[dates<=cutoff]
 lapply(dates,function(date){
  rr<-Filter(function(r)substr(r$date,1,10)==date,hourly)
  good<-Filter(function(r)is.numeric(r$value)&&is.finite(r$value)&&r$value>=0&&r$value<=100&&identical(r$qcFlag,'V'),rr)
  agency<-Filter(function(r)r$date==date,daily)
  midnight<-Filter(function(r)identical(r$date,paste(date,'00:00')),rr)
  nextmidnight<-Filter(function(r)identical(r$date,paste(as.Date(date)+1,'00:00')),hourly)
  list(date=date,agency_daily=first(agency,'value'),hourly_mean=if(length(good))mean(vapply(good,function(r)r$value,0.0))else NULL,valid_hourly=length(good),expected_hours=24,source_midnight=first(midnight,'value'),next_source_midnight=first(nextmidnight,'value'),agency_qc=first(agency,'qcFlag'),agency_qa=first(agency,'qaFlag'),missing_reason=if(!length(agency))'agency daily unavailable'else if(!length(good))'no valid hourly samples'else NULL)
 })
}
