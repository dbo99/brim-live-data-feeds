# Run from feeds root: Rscript scripts/soil_moisture/run_pilot.R CACHE [--live]
a<-commandArgs(TRUE);if(!length(a))stop('CACHE required');source('scripts/soil_moisture/nrcs_pilot.R')
root<-a[1];live<-'--live'%in%a
metadata<-sm1_request(sm1_metadata_spec(),root,live)
for(id in sm1_triplets){
 st<-Filter(function(x)identical(x$stationTriplet,id),metadata);if(length(st)!=1||!identical(as.numeric(st[[1]]$dataTimeZone),-8))stop('metadata station/timezone gate')
 for(depth in c(-8,-20))for(duration in c('DAILY',if(id==sm1_triplets[1]&&depth==-8)'HOURLY')){
  found<-Filter(function(e)identical(e$elementCode,'SMS')&&e$heightDepth==depth&&e$ordinal==1&&identical(e$durationName,duration)&&identical(e$storedUnitCode,'pct')&&identical(e$originalUnitCode,'pct'),st[[1]]$stationElements)
  if(length(found)!=1)stop('metadata sensor/unit/duration gate')
 }
}
plans<-lapply(sm1_triplets,function(id)sm1_data_spec(id,c('SMS:-8:1','SMS:-20:1'),'2026-06-23','2026-09-20'))
plans[[4]]<-sm1_data_spec('356:CA:SNTL','SMS:-8:1','2026-09-14 00:00','2026-09-20 23:00','HOURLY')
for(s in plans){x<-sm1_request(s,root,live);cat(s$params$stationTriplets,s$params$duration,'response entries',length(x),'\n')}
sm1_json(list(metadata_triplets=sm1_triplets,selection_reason='Three existing CA SWE identities; metadata confirms ordinal 1 at 8 and 20 inches below ground. Two depths per site, no substitution.',plans=plans,cutoff='2026-09-20',winter='Optional supplemental winter/temperature requests not used',source_timezone='Metadata dataTimeZone=-8; source labels retained; END period reference, statistic unresolved pending comparison'),file.path(root,'selection.json'))
