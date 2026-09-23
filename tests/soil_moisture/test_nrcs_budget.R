source('scripts/soil_moisture/nrcs_pilot.R')
r<-tempfile('sm1-budget-');dir.create(r);on.exit<-NULL
calls<-0L; fake<-function(url){calls<<-calls+1L;list(status_code=200L,content=charToRaw('[]'),headers=raw())}
s<-sm1_metadata_spec();sm1_request(s,r,TRUE,fake);sm1_request(s,r,FALSE,function(u)stop('unexpected network'));stopifnot(calls==1,length(jsonlite::read_json(file.path(r,'ledger.json'))$attempts)==1)
bad<-sm1_data_spec('356:CA:SNTL','SMS:-8:1','2026-01-01','2026-09-20');stopifnot(inherits(try(sm1_request(bad,r,TRUE,fake),silent=TRUE),'try-error'),calls==1)
bad<-sm1_data_spec('356:CA:SNTL','SMS:-8:1','2026-09-12','2026-09-20','HOURLY');stopifnot(inherits(try(sm1_request(bad,r,TRUE,fake),silent=TRUE),'try-error'),calls==1)
l<-jsonlite::read_json(file.path(r,'ledger.json'));l$attempts<-rep(l$attempts,80);sm1_json(l,file.path(r,'ledger.json'))
s$params$activeOnly<-'true';stopifnot(inherits(try(sm1_request(s,r,TRUE,fake),silent=TRUE),'try-error'),calls==1)
cat('PASS pre-attempt ledger, zero-call cache, daily/hourly scope and 80-attempt cap\n')
unlink(r,recursive=TRUE)
