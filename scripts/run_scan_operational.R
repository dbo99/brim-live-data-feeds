# Production wrapper: no new transport or science; errors never echo raw bodies.
scan_account_complete <- function(account) {
  if(is.null(account) || !is.null(account$fatal) || !identical(account$requests,29L) || length(account$receipts)!=29L)return(FALSE)
  good <- vapply(seq_len(29),function(i){
    x<-account$receipts[[i]]
    identical(x$state,"COMPLETE") && x$status==200 && x$attempt==i &&
      identical(x$method,if(i==1)"GET" else "POST") &&
      is.finite(x$body_bytes) && x$body_bytes>=0 && x$body_bytes<=2097152 &&
      is.finite(x$timeout_seconds) && x$timeout_seconds>0 && x$timeout_seconds<=20
  },logical(1))
  all(good) && is.finite(account$body_bytes) && account$body_bytes<=182452224 &&
    sum(vapply(account$receipts,function(x)x$body_bytes,numeric(1)))==account$body_bytes
}
# Only fixed labels and numeric accounting cross the public receipt boundary.
# Neither conditionMessage() nor an arbitrary condition class is exported.
scan_error_label <- function(e) {
  if(is.null(e))return("NONE")
  if(inherits(e,"scan_fatal"))return("SCAN_FATAL")
  if(inherits(e,"simpleError"))return("SIMPLE_ERROR")
  "OTHER_ERROR"
}
scan_public_account <- function(a) {
  if(is.null(a))return(NULL)
  number <- function(x) if(is.numeric(x)&&length(x)==1L&&is.finite(x))x else NULL
  label <- function(x,allowed) if(is.character(x)&&length(x)==1L&&x %in% allowed)x else "UNRECOGNIZED"
  stopifnot(is.list(a),is.list(a$receipts),length(a$receipts)<=87L)
  list(requests=number(a$requests),body_bytes=number(a$body_bytes),
       fatal=if(is.null(a$fatal))NULL else "PRESENT",
       receipts=lapply(a$receipts,function(x)list(
         attempt=number(x$attempt),method=label(x$method,c("GET","POST")),
         state=label(x$state,c("COMPLETE","UNKNOWN")),status=number(x$status),
         body_bytes=number(x$body_bytes),body_allowance_reserved=number(x$body_allowance_reserved),
         timeout_seconds=number(x$timeout_seconds))))
}
scan_write_receipt <- function(value,path) {
  # Preserve full double precision, including a failing timeout operand.
  pending <- tempfile("scan-receipt-",tmpdir=dirname(path))
  jsonlite::write_json(value,pending,auto_unbox=TRUE,null="null",digits=NA)
  stopifnot(file.rename(pending,path))
}
scan_run <- function(builder,admit,path) {
  scope <- new.env(parent=globalenv())
  phase <- "RUNTIME_ADMISSION";builder_state <- "NOT_RUN";predicate <- NULL
  outcome <- "RUNTIME_RECEIPT_FAILURE";failure <- NULL;account <- NULL
  failure <- tryCatch({
    admit()
    phase <- "BUILDER"
    builder_failure <- tryCatch({builder(scope);NULL},error=identity)
    builder_state <- if(is.null(builder_failure))"RETURNED" else "EXCEPTION"
    phase <- "ACCOUNT_SNAPSHOT"
    account <- if(exists("pt_scan_http",scope,inherits=FALSE))scope$pt_scan_http$snapshot() else NULL
    phase <- "ACCOUNT_PREDICATE"
    predicate_failure <- NULL
    predicate <- tryCatch(isTRUE(scan_account_complete(account)),error=function(e){predicate_failure <<- e;FALSE})
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
      exception_class=scan_error_label(failure),account_predicate=predicate,
      account=scan_public_account(account),raw_provider_errors_disclosed=FALSE)
    scan_write_receipt(receipt,path);NULL
  },error=identity)
  if(!is.null(receipt_error)) {
    outcome <- "RUNTIME_RECEIPT_FAILURE"
    # A filesystem refusal may also prevent this minimal fallback. Emit only
    # the fixed failure label; the independent finalizer must not assume write.
    tryCatch(scan_write_receipt(list(status=outcome,phase="RECEIPT_WRITE",
      builder_state=builder_state,exception_class=scan_error_label(receipt_error),
      account_predicate=NULL,account=NULL,raw_provider_errors_disclosed=FALSE),path),error=function(e)NULL)
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
