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
required <- c(soilDB="2.9.2", httr="1.4.9", curl="8.1.0")
stopifnot(getRversion()=="4.6.1",all(vapply(names(required),function(n)as.character(packageVersion(n)),character(1))==required))
control <- file.path(Sys.getenv("RUNNER_TEMP"),"scan-control")
reservation <- jsonlite::fromJSON(file.path(control,"campaign-reservation.json"))
stopifnot(reservation$state=="UNKNOWN_RESERVED_BEFORE_LAUNCH",as.character(reservation$run_id)==Sys.getenv("GITHUB_RUN_ID"),reservation$attempt==1)
deadline <- as.POSIXct(Sys.getenv("SCAN_RUN_DEADLINE_UTC"),format="%Y-%m-%dT%H:%M:%SZ",tz="UTC")
stopifnot(is.finite(as.numeric(deadline)),as.numeric(difftime(deadline,Sys.time(),units="secs"))>1230)
scope <- new.env(parent=globalenv())
failure <- tryCatch({source(file.path(Sys.getenv("GITHUB_WORKSPACE"),"scripts/build_scan_soil_moisture_latest.R"),local=scope);NULL},error=identity)
account <- if(exists("pt_scan_http",scope,inherits=FALSE))scope$pt_scan_http$snapshot() else NULL
complete <- is.null(failure) && isTRUE(tryCatch(scan_account_complete(account),error=function(e)FALSE))
receipt <- list(source_sha=Sys.getenv("GITHUB_SHA"),run_id=Sys.getenv("GITHUB_RUN_ID"),run_attempt=Sys.getenv("GITHUB_RUN_ATTEMPT"),
  status=if(complete)"BUILT_ACCOUNT_COMPLETE" else "FAILED_OR_UNKNOWN",account=account,
  raw_provider_errors_disclosed=FALSE)
jsonlite::write_json(receipt,file.path(control,"runtime-account.json"),auto_unbox=TRUE,null="null")
if(!complete)quit(status=1L)
