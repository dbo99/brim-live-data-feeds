# Actual installed curl setopt and existing environment-to-transport handoff.
# Only an externally confined loopback fixture may be used by this test.
source("scripts/soil_moisture/scan_metered_http.R")
port <- Sys.getenv("SCAN_TEST_LOOPBACK_PORT")
stopifnot(grepl("^[0-9]{4,5}$", port))
seen <- list()
trace("handle_setopt", where=asNamespace("curl"), print=FALSE,
      tracer=quote({ args <- list(...); if (!is.null(args$timeout_ms))
        .GlobalEnv$seen[[length(.GlobalEnv$seen)+1L]] <- args[c("timeout_ms", "connecttimeout_ms")] }))
form <- getFromNamespace(".make_SCAN_req", "soilDB")(2218,2026,"SMS","Daily")
options(brim.scan.http.backend=function(method,url,form,timeout,read_limit,consume,check)
  pt_scan_curl_backend(method,paste0("http://127.0.0.1:",port,"/report"),form,timeout,read_limit,consume,check))
Sys.setenv(SCAN_HTTP_MAX_REQUESTS="87",SCAN_HTTP_RESPONSE_BYTES="2097151",
           SCAN_HTTP_TOTAL_BYTES="182452223",SCAN_HTTP_CAMPAIGN_SECONDS="1200",SCAN_FETCH_TIMEOUT_SEC="20")
h <- pt_scan_http_from_env(); h$request("POST","https://wcc.sc.egov.usda.gov/nwcc/view",form)
stopifnot(length(seen)==1L,seen[[1]]$timeout_ms<=20000L,seen[[1]]$timeout_ms>19000L,
          identical(seen[[1]]$timeout_ms,seen[[1]]$connecttimeout_ms),h$snapshot()$requests==1)
Sys.setenv(SCAN_HTTP_CAMPAIGN_SECONDS="0.9")
h <- pt_scan_http_from_env(); Sys.sleep(.15)
h$request("POST","https://wcc.sc.egov.usda.gov/nwcc/view",form)
stopifnot(length(seen)==2L,seen[[2]]$timeout_ms<=750L,seen[[2]]$timeout_ms>0L)
untrace("handle_setopt",where=asNamespace("curl"))
# Same environment factory, 87 admitted in-process physical-backend invocations;
# request 88 refuses before the backend. No external or loopback traffic here.
calls <- 0L
options(brim.scan.http.backend=function(method,url,form,timeout,read_limit,consume,check){calls <<- calls+1L;list(status=200L,complete=TRUE)})
Sys.setenv(SCAN_HTTP_CAMPAIGN_SECONDS="1200")
h <- pt_scan_http_from_env()
for(i in seq_len(87))h$request("GET","https://wcc.sc.egov.usda.gov/")
e <- tryCatch(h$request("GET","https://wcc.sc.egov.usda.gov/"),error=identity)
stopifnot(inherits(e,"scan_budget_exhausted"),calls==87L,h$snapshot()$requests==87L)
# Evaluate only the actual pure accounting predicate; never source the real
# entry point or substitute synthetic provenance for a production campaign.
expressions <- parse("scripts/run_scan_operational.R")
for(expr in expressions)if(is.call(expr) && identical(expr[[1]],as.name("<-")) && identical(expr[[2]],as.name("scan_account_complete")))eval(expr)
a<-list(requests=29L,body_bytes=29,fatal=NULL,receipts=lapply(seq_len(29),function(i)list(attempt=i,method=if(i==1)"GET" else "POST",state="COMPLETE",body_bytes=1,timeout_seconds=20,status=200)))
stopifnot(scan_account_complete(a),!scan_account_complete(NULL))
for(field in c("state","body_bytes","status","method","timeout_seconds")){
  b<-a;b$receipts[[3]][[field]]<-switch(field,state="UNKNOWN",body_bytes=2097153,status=503,method="GET",timeout_seconds=20.001)
  stopifnot(!scan_account_complete(b))
}
b<-a;b$body_bytes<-28;stopifnot(!scan_account_complete(b))
jsonlite::write_json(list(status="PASS",native_setopts=seen,request_ceiling=87,
  configured_seconds=20,deadline_clipped=TRUE,provider_requests=0),
  Sys.getenv("SCAN_NATIVE_TIMEOUT_RECEIPT"),auto_unbox=TRUE)
cat("PASS actual native timeout and post-setup deadline clipping\n")
