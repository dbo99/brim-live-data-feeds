source('scripts/soil_moisture/pilot_analysis.R')
r<-function(d,v,q='V')list(date=d,value=v,qcFlag=q,qaFlag='P')
h<-list(r('2026-09-20 01:00',0),r('2026-09-20 02:00',-99.9),r('2026-09-20 03:00',10,'S'),r('2026-09-21 00:00',90))
x<-sm1_pilot_comparison(h,list(),'2026-09-20')
stopifnot(length(x)==1,x[[1]]$valid_hourly==1,x[[1]]$hourly_mean==0,is.null(x[[1]]$source_midnight),is.null(x[[1]]$agency_daily),x[[1]]$next_source_midnight==90)
stopifnot(inherits(try(sm1_pilot_comparison(c(h,h[1]),list(),'2026-09-20'),silent=TRUE),'try-error'))
# Fixed source dates across leap and DST boundaries stay unshifted.
for(d in c('2024-02-29','2026-03-08','2026-11-01'))stopifnot(sm1_pilot_comparison(list(r(paste(d,'00:00'),4)),list(r(d,4)),d)[[1]]$date==d)
cat('PASS diagnostic zero/sentinel/suspect/gaps/duplicates, incomplete cutoff, DST/leap labels. No winter temperature requested; no inferred freezing screen.\n')
