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
