# Experimental independent arithmetic means, never production observations.
a<-commandArgs(TRUE);stopifnot(length(a)==2);root<-a[1];out<-a[2]
meta<-lapply(list.files(root,pattern='meta.json$',full.names=TRUE),jsonlite::read_json)
hour<-Filter(function(x)identical(x$spec$params$duration,'HOURLY'),meta)[[1]]
day<-Filter(function(x)identical(x$spec$params$duration,'DAILY')&&identical(x$spec$params$stationTriplets,'356:CA:SNTL'),meta)[[1]]
body<-function(m)jsonlite::read_json(file.path(root,paste0(digest::digest(m$spec,algo='sha256'),'.json')))
# Locate by response hash; JSON parsing should not change the cache identity.
body<-function(m){files<-list.files(root,pattern='^[a-f0-9]{64}.json$',full.names=TRUE);p<-files[vapply(files,function(f)identical(digest::digest(file=f,algo='sha256'),m$sha256),TRUE)];stopifnot(length(p)==1);jsonlite::read_json(p)}
select<-function(m){st<-Filter(function(x)x$stationTriplet=='356:CA:SNTL',body(m));stopifnot(length(st)==1);b<-Filter(function(x){e<-x$stationElement; e$elementCode=='SMS'&&e$heightDepth== -8&&e$ordinal==1},st[[1]]$data);stopifnot(length(b)==1);v<-b[[1]]$values;t<-vapply(v,function(r)r$date,'');stopifnot(!anyDuplicated(t));v}
h<-select(hour);d<-select(day)
script<-sub('^--file=','',grep('^--file=',commandArgs(FALSE),value=TRUE)[1]);source(file.path(dirname(script),'pilot_analysis.R'))
rows<-sm1_pilot_comparison(h,d,'2026-09-20')

result<-list(policy='EXPERIMENTAL SMS hourly arithmetic mean on unshifted source label dates; fixed offset -08 from metadata; 00:00 through 23:00, 0..100 QC=V; support retained; not substituted into any source daily field',cutoff='2026-09-20',rows=rows,conclusion='Compare observed numbers; agreement alone does not prove derivation. DAILY reduction unresolved, common recent-change/reference disabled.',winter_diagnostic='not requested; freeze response not assessed')
jsonlite::write_json(result,out,auto_unbox=TRUE,pretty=TRUE,null='null',digits=NA)
cat('Hourly rows',length(h),'comparison days',length(rows),'\n');print(rows)
