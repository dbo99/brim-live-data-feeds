# Production wrapper: no new transport or science; errors never echo raw bodies.
scan_account_refusal <- function(account) {
  number <- function(x) is.numeric(x)&&!is.object(x)&&length(x)==1L&&is.finite(x)
  if(!is.list(account)||is.object(account))return("ACCOUNT_SHAPE")
  if(!is.null(account$fatal))return("FATAL_PRESENT")
  if(!identical(account$requests,29L))return("REQUEST_COUNT_TYPE")
  if(!is.list(account$receipts)||is.object(account$receipts)||length(account$receipts)!=29L)return("RECEIPT_COUNT_TYPE")
  for(i in seq_len(29)) {
    x<-account$receipts[[i]]
    if(!is.list(x)||is.object(x))return("RECEIPT_SHAPE")
    if(!identical(x$state,"COMPLETE"))return("RECEIPT_STATE")
    if(!number(x$status)||x$status!=200)return("RECEIPT_STATUS")
    if(!number(x$attempt)||x$attempt!=i)return("RECEIPT_ATTEMPT")
    if(!identical(x$method,if(i==1)"GET" else "POST"))return("RECEIPT_METHOD")
    if(!number(x$body_bytes)||x$body_bytes<0||x$body_bytes>2097152)return("RECEIPT_BYTES")
    if(!number(x$timeout_seconds))return("TIMEOUT_NONFINITE_TYPE")
    if(x$timeout_seconds<=0||x$timeout_seconds>20)return("TIMEOUT_LIMIT")
  }
  if(!number(account$body_bytes)||account$body_bytes<0||account$body_bytes>182452224)return("TOTAL_BYTES")
  if(sum(vapply(account$receipts,function(x)x$body_bytes,numeric(1)))!=account$body_bytes)return("BYTE_SUM")
  "NONE"
}
scan_account_complete <- function(account) identical(scan_account_refusal(account),"NONE")
# Only fixed labels and numeric accounting cross the public receipt boundary.
# Neither conditionMessage() nor an arbitrary condition class is exported.
scan_error_label <- function(e) {
  if(is.null(e))return("NONE")
  if(inherits(e,"scan_fatal"))return("SCAN_FATAL")
  if(inherits(e,"simpleError"))return("SIMPLE_ERROR")
  "OTHER_ERROR"
}
scan_public_account <- function(a) {
  if(!is.list(a)||is.object(a))return(NULL)
  number <- function(x) if(is.numeric(x)&&!is.object(x)&&length(x)==1L&&is.finite(x))unname(as.numeric(x)) else NULL
  label <- function(x,allowed) if(is.character(x)&&!is.object(x)&&length(x)==1L&&!is.na(x)&&x %in% allowed)unname(as.character(x)) else "UNRECOGNIZED"
  if(!is.list(a$receipts)||is.object(a$receipts)||length(a$receipts)>87L)return(NULL)
  list(requests=number(a$requests),body_bytes=number(a$body_bytes),
       fatal=if(is.null(a$fatal))NULL else "PRESENT",
       receipts=unname(lapply(a$receipts,function(x){if(!is.list(x)||is.object(x))return(NULL);list(
         attempt=number(x$attempt),method=label(x$method,c("GET","POST")),
         state=label(x$state,c("COMPLETE","UNKNOWN")),status=number(x$status),
         body_bytes=number(x$body_bytes),body_allowance_reserved=number(x$body_allowance_reserved),
         timeout_seconds=number(x$timeout_seconds))})))
}
scan_write_receipt <- function(value,path) {
  # JSON is not a bit/type-preserving record. The fixed refusal code is
  # computed on the original R operands before this serialization boundary.
  pending <- tempfile("scan-receipt-",tmpdir=dirname(path))
  jsonlite::write_json(value,pending,auto_unbox=TRUE,null="null",digits=NA)
  stopifnot(file.rename(pending,path))
}
scan_run <- function(builder,admit,path) {
  scope <- new.env(parent=globalenv())
  phase <- "RUNTIME_ADMISSION";builder_state <- "NOT_RUN";predicate <- NULL;refusal <- "NOT_EVALUATED"
  outcome <- "RUNTIME_RECEIPT_FAILURE";failure <- NULL;account <- NULL
  failure <- tryCatch({
    admit()
    phase <- "BUILDER"
    builder_failure <- tryCatch({builder(scope);NULL},error=identity)
    builder_state <- if(is.null(builder_failure))"RETURNED" else "EXCEPTION"
    phase <- "ACCOUNT_SNAPSHOT"
    account <- tryCatch(if(exists("pt_scan_http",scope,inherits=FALSE))scope$pt_scan_http$snapshot() else NULL,
      error=function(e){refusal<<-"SNAPSHOT_EXCEPTION";stop(e)})
    phase <- "ACCOUNT_PREDICATE"
    predicate_failure <- NULL
    predicate <- tryCatch({refusal<-scan_account_refusal(account);identical(refusal,"NONE")},
      error=function(e){predicate_failure<<-e;refusal<<-"PREDICATE_EXCEPTION";FALSE})
    if(!is.null(builder_failure)) {
      outcome <- "BUILDER_EXCEPTION";phase <- "BUILDER";builder_failure
    } else if(!predicate) {
      outcome <- "ACCOUNT_REJECTED";predicate_failure
    } else {outcome <- "BUILT_ACCOUNT_COMPLETE";phase <- "COMPLETE";NULL}
  },error=identity)
  # A snapshot/serialization failure never hides a completed builder exception
  # marker or creates a successful account. Missing receipt is also diagnosed
  # independently by the workflow finalizer.
  receipt_error <- tryCatch({
    public_id <- function(name,pattern){x<-Sys.getenv(name);if(grepl(pattern,x))x else NULL}
    receipt <- list(status=outcome,phase=phase,builder_state=builder_state,
      source_sha=public_id("GITHUB_SHA","^[0-9a-f]{40}$"),
      run_id=public_id("GITHUB_RUN_ID","^[1-9][0-9]{0,19}$"),
      run_attempt=public_id("GITHUB_RUN_ATTEMPT","^[1-9][0-9]{0,3}$"),
      exception_class=scan_error_label(failure),account_predicate=predicate,account_refusal=refusal,
      account=scan_public_account(account),raw_provider_errors_disclosed=FALSE)
    scan_write_receipt(receipt,path);NULL
  },error=identity)
  if(!is.null(receipt_error)) {
    outcome <- "RUNTIME_RECEIPT_FAILURE"
    # A filesystem refusal may also prevent this minimal fallback. Emit only
    # the fixed failure label; the independent finalizer must not assume write.
    tryCatch(scan_write_receipt(list(status=outcome,phase="RECEIPT_WRITE",
      builder_state=builder_state,exception_class=scan_error_label(receipt_error),
      account_predicate=NULL,account_refusal="RECEIPT_EXCEPTION",account=NULL,raw_provider_errors_disclosed=FALSE),path),error=function(e)NULL)
  }
  cat("SCAN wrapper ",outcome,"\n",sep="")
  if(identical(outcome,"BUILT_ACCOUNT_COMPLETE"))0L else 1L
}
scan_main <- function() {
  control <- file.path(Sys.getenv("RUNNER_TEMP"),"scan-control")
  admit <- function() {
    required <- c(soilDB="2.9.2", httr="1.4.9", curl="8.1.0")
    stopifnot(getRversion()=="4.6.1",all(vapply(names(required),function(n)as.character(packageVersion(n)),character(1))==required))
    reservation <- jsonlite::fromJSON(file.path(control,"campaign-reservation.json"))
    stopifnot(reservation$state=="UNKNOWN_RESERVED_BEFORE_LAUNCH",as.character(reservation$run_id)==Sys.getenv("GITHUB_RUN_ID"),reservation$attempt==1)
    deadline <- as.POSIXct(Sys.getenv("SCAN_RUN_DEADLINE_UTC"),format="%Y-%m-%dT%H:%M:%SZ",tz="UTC")
    stopifnot(is.finite(as.numeric(deadline)),as.numeric(difftime(deadline,Sys.time(),units="secs"))>1230)
  }
  scan_run(function(scope)source(file.path(Sys.getenv("GITHUB_WORKSPACE"),"scripts/build_scan_soil_moisture_latest.R"),local=scope),
           admit,file.path(control,"runtime-account.json"))
}
if(sys.nframe()==0L)quit(status=scan_main())
