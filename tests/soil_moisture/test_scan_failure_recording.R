# Run with non-loopback networking denied; no provider or science execution.
args <- commandArgs(trailingOnly=TRUE)
source("scripts/run_scan_operational.R")
out <- args[[1]];dir.create(out,recursive=TRUE,showWarnings=FALSE)
sentinel <- "SYNTHETIC_PRIVATE_FAILURE_SENTINEL"
account <- list(requests=29L,body_bytes=29,fatal=NULL,receipts=lapply(seq_len(29),function(i)
  list(attempt=i,method=if(i==1)"GET" else "POST",state="COMPLETE",status=200L,
       body_bytes=1,body_allowance_reserved=2097151,timeout_seconds=20)))
results <- list()
run <- function(name,a=account,fail=FALSE,admit=function()NULL,snapshot_fail=FALSE,path=file.path(out,paste0(name,".json"))) {
  # Exercise the same source(scope) -> snapshot -> predicate -> receipt route
  # used by production, with explicitly synthetic builder source only.
  fixture <- file.path(out,paste0(name,"-builder.R"))
  writeLines(c("pt_scan_http <- list(snapshot=function(){if(snapshot_fail)stop(sentinel);a})",
               "if(fail)stop(structure(list(message=sentinel,call=NULL),class=c(sentinel,'error','condition')))"),fixture)
  builder <- function(scope){scope$a<-a;scope$fail<-fail;scope$snapshot_fail<-snapshot_fail;scope$sentinel<-sentinel;source(fixture,local=scope)}
  exit <- scan_run(builder,admit,path)
  value <- if(file.exists(path)&&!dir.exists(path))jsonlite::fromJSON(path,simplifyVector=FALSE) else NULL
  if(!is.null(value))stopifnot(!grepl(sentinel,paste(readLines(path),collapse=""),fixed=TRUE))
  results[[name]] <<- list(exit=exit,status=if(is.null(value))"MISSING" else value$status)
  list(exit=exit,value=value)
}
x<-run("normal");stopifnot(x$exit==0L,x$value$status=="BUILT_ACCOUNT_COMPLETE",isTRUE(x$value$account_predicate))
x<-run("builder",fail=TRUE);stopifnot(x$exit==1L,x$value$status=="BUILDER_EXCEPTION",x$value$exception_class=="OTHER_ERROR",isTRUE(x$value$account_predicate),x$value$account$requests==29)
b<-account;b$body_bytes<-28
x<-run("account",b);stopifnot(x$exit==1L,x$value$status=="ACCOUNT_REJECTED",x$value$builder_state=="RETURNED")
b<-account;b$receipts[[29]]$state<-"UNKNOWN"
x<-run("unknown",b);stopifnot(x$value$status=="ACCOUNT_REJECTED",x$value$account$receipts[[29]]$state=="UNKNOWN")
x<-run("unknown-builder",b,fail=TRUE);stopifnot(x$value$status=="BUILDER_EXCEPTION",x$value$account$receipts[[29]]$state=="UNKNOWN",!x$value$account_predicate)
x<-run("snapshot",snapshot_fail=TRUE);stopifnot(x$value$status=="RUNTIME_RECEIPT_FAILURE",x$value$phase=="ACCOUNT_SNAPSHOT")
x<-run("builder-snapshot",fail=TRUE,snapshot_fail=TRUE);stopifnot(x$value$status=="RUNTIME_RECEIPT_FAILURE",x$value$builder_state=="EXCEPTION")
x<-run("admission",admit=function()stop(sentinel));stopifnot(x$value$status=="RUNTIME_RECEIPT_FAILURE",x$value$builder_state=="NOT_RUN")
x<-run("filesystem",path=file.path(out,"missing-parent","receipt.json"));stopifnot(x$exit==1L,is.null(x$value))
b<-account;b$private<-sentinel;b$receipts[[1]]$private<-sentinel
x<-run("projection",b);stopifnot(x$exit==0L,is.null(x$value$account$private))
b<-account;b$receipts[[1]]$timeout_seconds<-20+1e-10
x<-run("precision",b);stopifnot(x$value$status=="ACCOUNT_REJECTED",x$value$account$receipts[[1]]$timeout_seconds>20)
# Every first-clause refusal is evaluated in the actual R route and survives
# real JSON serialization, even where JSON cannot retain the failing R type.
cases<-list(ACCOUNT_SHAPE=NULL,FATAL_PRESENT=within(account,{fatal<-"SYNTHETIC"}),
 REQUEST_COUNT_TYPE=within(account,{requests<-29}),RECEIPT_COUNT_TYPE=within(account,{receipts<-receipts[-1]}),
 TOTAL_BYTES=within(account,{body_bytes<-Inf}),BYTE_SUM=within(account,{body_bytes<-28}))
for(code in names(cases)){
 x<-run(paste0("clause-",code),cases[[code]])
 stopifnot(x$exit==1L,x$value$status=="ACCOUNT_REJECTED",identical(x$value$account_refusal,code))
}
bad_fields<-list(RECEIPT_STATE=list("state","UNKNOWN"),RECEIPT_STATUS=list("status",503),
 RECEIPT_ATTEMPT=list("attempt",2),RECEIPT_METHOD=list("method","POST"),RECEIPT_BYTES=list("body_bytes",2097153),
 TIMEOUT_NONFINITE_TYPE=list("timeout_seconds",NaN),TIMEOUT_LIMIT=list("timeout_seconds",20+.Machine$double.eps*16))
for(code in names(bad_fields)){
 b<-account;b$receipts[[1]][[bad_fields[[code]][[1]]]]<-bad_fields[[code]][[2]]
 x<-run(paste0("clause-",code),b);stopifnot(x$exit==1L,identical(x$value$account_refusal,code))
}
b<-account;b$receipts[[1]]<-"SYNTHETIC"
x<-run("clause-RECEIPT_SHAPE",b);stopifnot(x$value$account_refusal=="RECEIPT_SHAPE")
b<-account;attr(b$receipts[[1]],"private")<-sentinel
attr(b$receipts[[1]]$timeout_seconds,"private")<-sentinel
names(b$receipts[[1]]$body_bytes)<-sentinel
names(b$receipts)<-rep(sentinel,29)
x<-run("attributes",b);stopifnot(x$exit==0L,x$value$account_refusal=="NONE")
# R numeric classes cannot dispatch custom comparison/serialization behavior.
b<-account;class(b$receipts[[1]]$timeout_seconds)<-sentinel
x<-run("classified-number",b);stopifnot(x$value$account_refusal=="TIMEOUT_NONFINITE_TYPE")
original_refusal<-scan_account_refusal
scan_account_refusal<-function(account)stop(sentinel)
x<-run("predicate-exception");stopifnot(x$value$account_refusal=="PREDICATE_EXCEPTION",x$exit==1L)
scan_account_refusal<-original_refusal
stopifnot(jsonlite::fromJSON(file.path(out,"snapshot.json"))$account_refusal=="SNAPSHOT_EXCEPTION")
# The actual corrected meter, controlled clock and backend must produce an
# accepted 29-receipt account without changing the wrapper cap or byte totals.
source("scripts/soil_moisture/scan_metered_http.R")
clock_value<-12.078;stopifnot((clock_value+20)-clock_value>20)
backend_timeouts<-numeric()
backend<-function(method,url,form,timeout,read_limit,consume,check){
 backend_timeouts<<-c(backend_timeouts,timeout);consume(as.raw(1));list(status=200L,complete=TRUE)
}
h<-pt_scan_make_http(87,2097151,182452223,1200,20,backend=backend,clock=function()clock_value)
form<-setNames(as.list(rep("SYNTHETIC",8)),c("intervalType","report","timeseries","format","sitenum","interval","year","month"))
h$request("GET","https://wcc.sc.egov.usda.gov/")
for(i in 2:29)h$request("POST","https://wcc.sc.egov.usda.gov/nwcc/view",form)
x<-run("corrected-meter",h$snapshot());stopifnot(x$exit==0L,all(backend_timeouts==20),h$snapshot()$body_bytes==29)
for(value in list(20.01,0,-1,Inf,NA_real_,"20",c(1,20))){
 b<-account;b$receipts[[1]]$timeout_seconds<-value;stopifnot(!scan_account_complete(b))
}
for(value in list(200L,200)){b<-account;b$receipts[[1]]$status<-value;stopifnot(scan_account_complete(b))}
# Full retained JSON evaluation is diagnostic only: serialized receipt types and
# rounded numbers do not reconstruct the original in-memory failing operand.
if(length(args)>1L){
  retained<-jsonlite::fromJSON(args[[2]],simplifyVector=FALSE)
  results$retained<-list(predicate_on_serialized_account=scan_account_complete(retained$account),
    historical_status=retained$status,requests=retained$account$requests,body_bytes=retained$account$body_bytes,
    original_cause="UNESTABLISHED")
}
jsonlite::write_json(results,file.path(out,"TEST_RESULTS.json"),auto_unbox=TRUE,pretty=TRUE)
cat("PASS actual SCAN R failure recording\n")
