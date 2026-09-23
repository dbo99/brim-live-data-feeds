`%||%`<-function(a,b)if(is.null(a))b else a
a<-commandArgs(FALSE);script<-sub('^--file=','',a[grepl('^--file=',a)][1]);root<-normalizePath(file.path(dirname(script),'../..'))
for(network in c('scan','dendra','snotel')) {
 p<-file.path(root,'templates/soil-moisture',paste0(network,'.template.yml'));x<-yaml::read_yaml(p)
 stopifnot(!'schedule' %in% names(x$on),identical(names(x$jobs),c('prepare','publish')),x$permissions$contents=='read',x$jobs$prepare$permissions$contents=='read',x$jobs$publish$permissions$contents=='write',x$jobs$publish$needs=='prepare',x$jobs$prepare$`timeout-minutes`<=30,x$jobs$publish$`timeout-minutes`<=15)
 stopifnot(is.null(x$concurrency),is.null(x$jobs$prepare$concurrency),identical(x$jobs$publish$concurrency,list(group='brim-live-main-publish',`cancel-in-progress`=FALSE,queue='max')))
 lines<-readLines(p);stopifnot(any(grepl('soil_network_workflow.py prepare',lines,fixed=TRUE)),any(grepl('fallback-result',lines,fixed=TRUE)),any(grepl('soil_moisture_operations.py restore',lines,fixed=TRUE)),any(grepl('publication-result',lines,fixed=TRUE)),!any(grepl('continue-on-error:',lines,fixed=TRUE)))
 if(network=='snotel')stopifnot(grepl('false',x$jobs$publish$`if`,fixed=TRUE),!any(grepl('--enable-acquisition',lines,fixed=TRUE))) else stopifnot(grepl("needs.prepare.result == 'success'",x$jobs$publish$`if`,fixed=TRUE))
 for(job in x$jobs)for(step in job$steps)if(!is.null(step$run)) {
  f<-tempfile();writeLines(step$run,f);stopifnot(system2('bash',c('-n',shQuote(f)))==0);unlink(f)
 }
 if(network=='dendra')stopifnot(sum(grepl('--ledger "$RUNNER_TEMP/dendra-http.json"',lines,fixed=TRUE))==2L,any(grepl('restore-public',lines,fixed=TRUE)))
 if(network %in% c('dendra','scan')) {
  steps<-x$jobs$publish$steps
  receipt<-Filter(function(s)identical(s$name,'Publication outcome does not overwrite query clocks'),steps)[[1]]
  export<-Filter(function(s)identical(s$name,'Export publication health even when receipt finalization fails'),steps)[[1]]
  stopifnot(grepl('always()',export$`if`,fixed=TRUE),!grepl('operations.py export',receipt$run,fixed=TRUE))
 }
 if(network=='dendra') {
  steps<-x$jobs$prepare$steps
  names<-vapply(steps,function(s)s$name %||% '',character(1))
  stopifnot(match('Exact acknowledged parent and explicit fresh metadata selection',names)<match('Restore complete pending queries after public parent and before planning',names),match('Restore complete pending queries after public parent and before planning',names)<match('Source-specific preparation; failure cannot produce a publication artifact',names))
  pending<-Filter(function(s)identical(s$name,'Export complete pending queries even without a prepared candidate'),steps)[[1]]
  stopifnot(grepl('always()',pending$`if`,fixed=TRUE),any(grepl('pending_manifest_sha256',lines,fixed=TRUE)))
 }
 cat(network,': independent inactive graph, permissions, source gate, transfer/restore paths, bounded timeouts, shell syntax passed\n')
}
