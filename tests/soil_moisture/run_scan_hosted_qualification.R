# Actual installed soilDB and complete unchanged builder over native loopback HTTP.
args <- commandArgs(trailingOnly=TRUE); stopifnot(length(args)==4L)
D <- normalizePath(args[1]); run <- normalizePath(args[2]); mode <- args[3]; port <- args[4]
stopifnot(grepl("^[0-9]{4,5}$",port)); setwd(run)
source(file.path(D,"scripts/soil_moisture/scan_metered_http.R"))
# Packaged public cache IDs authenticate the installed formatter; no real roster,
# station name, coordinate or observation file is used or exported as a fixture.
meta <- soilDB::SCAN_site_metadata()
ids <- as.integer(head(meta$Site,if(mode=="good_peer") 11 else 10))
stopifnot(length(ids)>=10,!anyNA(ids),!anyDuplicated(ids))
if(mode=="source_scope")ids[10]<-999999L
roster<-data.frame(station_uid=paste0("synthetic-",seq_along(ids)),station_name=paste("SYNTHETIC",seq_along(ids)),site_code=ids,state="CA",county="SYNTHETIC",elevation_ft=100,latitude=36+seq_along(ids)/100,longitude=-120-seq_along(ids)/100,site_page_url="https://example.invalid/synthetic",source_url="https://example.invalid/synthetic")
input<-file.path(run,"inputs");dir.create(input)
write.csv(roster,file.path(input,"stations.csv"),row.names=FALSE)
Sys.setenv(GITHUB_WORKSPACE=D,SCAN_STATION_INDEX_CSV=file.path(input,"stations.csv"),SCAN_SMS_PERCENTILES_CSV=file.path(input,"absent-percentiles.csv"),SCAN_DEPTH_STYLE_CSV=file.path(input,"absent-style.csv"),SCAN_HTTP_MAX_REQUESTS="100",SCAN_HTTP_RESPONSE_BYTES="2097152",SCAN_HTTP_TOTAL_BYTES="8388608",SCAN_HTTP_CAMPAIGN_SECONDS="60",SCAN_FETCH_TIMEOUT_SEC="5",SCAN_FETCH_RETRIES="3",SCAN_FETCH_RETRY_PAUSE_SEC="0",SCAN_SMS_REQUEST_PAUSE_SEC="0",SCAN_NWCC_PREFLIGHT_RETRIES="3",SCAN_NWCC_PREFLIGHT_PAUSE_SEC="0",SCAN_NWCC_PREFLIGHT_TIMEOUT_SEC="5",SCAN_CANARY_SITE_CODES=as.character(ids[1]),SCAN_CANARY_MAX_REQUESTS="1",SCAN_CANARY_MIN_SUCCESSES="1",SCAN_INITIAL_EMPTY_ABORT_REQUESTS="2")
if(mode=="oversize")Sys.setenv(SCAN_HTTP_RESPONSE_BYTES="20")
if(mode=="budget")Sys.setenv(SCAN_HTTP_MAX_REQUESTS="2")
# URL routing is the only transport substitution. The accepted native backend
# constructs the form, opens the socket and performs every bounded body read.
# Last-site identity is encoded in the local path's query, not a production header.
options(brim.scan.http.backend=function(method,url,form,timeout,read_limit,consume,check){
 route<-if(method=="GET") "preflight" else "report"
 local<-paste0("http://127.0.0.1:",port,"/builder/",mode,"/",route,"?last=",tail(ids,1))
 pt_scan_curl_backend(method,local,form,timeout,read_limit,consume,check)
})
e<-new.env(parent=globalenv());e$Sys.time<-function()as.POSIXct("2026-10-26 19:00:00",tz="UTC")
failed<-tryCatch({source(file.path(D,"scripts/build_scan_soil_moisture_latest.R"),local=e);NULL},error=identity)
receipt<-list(mode=mode,success=is.null(failed),error=if(is.null(failed))NULL else list(class=class(failed),message=conditionMessage(failed)),account=if(exists("pt_scan_http",e,inherits=FALSE))e$pt_scan_http$snapshot() else NULL,synthetic_only=TRUE,provider_requests=0)
jsonlite::write_json(receipt,file.path(run,"BUILDER_RECEIPT.json"),auto_unbox=TRUE,pretty=TRUE,null="null")
if(!is.null(failed)){message(conditionMessage(failed));quit(status=1)}
