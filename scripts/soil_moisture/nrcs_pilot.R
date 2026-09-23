# SM1 bounded adaptation of build_snow_pillow_latest.R pt_fetch_text and
# build_awdb_data_url. No implicit stations, years, redirects or fallback fetches.
sm1_triplets <- c('356:CA:SNTL','574:CA:SNTL','1051:CA:SNTL')
sm1_json <- function(x,p) { tmp<-paste0(p,'.tmp'); jsonlite::write_json(x,tmp,auto_unbox=TRUE,pretty=TRUE,null='null',digits=NA); if(!file.rename(tmp,p))stop('atomic write failed') }
sm1_request <- function(spec, root, live=FALSE, transport=NULL) {
  stopifnot(is.list(spec),spec$kind %in% c('stations','data'))
  ids<-strsplit(spec$params$stationTriplets,',',fixed=TRUE)[[1]]
  if(!length(ids)||any(!ids %in% sm1_triplets)||anyDuplicated(ids))stop('station scope')
  if(spec$kind=='stations') {
    if(!identical(spec$params$elements,'SMS:*:*'))stop('metadata scope')
  } else {
    els<-strsplit(spec$params$elements,',',fixed=TRUE)[[1]]
    if(length(els)>6||any(!grepl('^SMS:-?[0-9.]+:[0-9]+$',els)))stop('sensor scope')
    a<-as.Date(spec$params$beginDate); b<-as.Date(spec$params$endDate)
    cutoff<-as.Date('2026-09-20')
    limit<-if(identical(spec$params$duration,'HOURLY'))7L else if(identical(spec$params$duration,'DAILY'))90L else stop('duration')
    if(is.na(a)||is.na(b)||a>b||b>cutoff||as.integer(b-a)+1L>limit)stop('date scope')
    if(spec$params$duration=='HOURLY'&&(length(ids)!=1||length(els)!=1))stop('hourly scope')
    if(!identical(spec$params$periodRef,'END'))stop('period reference')
  }
  dir.create(root,recursive=TRUE,showWarnings=FALSE)
  key<-digest::digest(spec,algo='sha256'); bodyfile<-file.path(root,paste0(key,'.json')); infofile<-file.path(root,paste0(key,'.meta.json'))
  if(file.exists(bodyfile)&&file.exists(infofile)) {
    info<-jsonlite::read_json(infofile)
    if(!identical(info$sha256,digest::digest(file=bodyfile,algo='sha256')))stop('cache integrity')
    return(jsonlite::read_json(bodyfile,simplifyVector=FALSE))
  }
  if(!live)stop('offline cache miss')
  lock<-file.path(root,'request.lock'); if(!dir.create(lock,showWarnings=FALSE))stop('another request / stale lock; inspect, do not bypass')
  on.exit(unlink(lock,recursive=TRUE),add=TRUE)
  ledgerfile<-file.path(root,'ledger.json')
  ledger<-if(file.exists(ledgerfile))jsonlite::read_json(ledgerfile) else list(version=1,limit=80,attempts=list(),sensors=list(),hourly=list())
  if(spec$kind=='data') {
    keys<-unlist(lapply(ids,function(id)paste(id,els,sep='/')))
    unionkeys<-union(unlist(ledger$sensors),keys); if(length(unionkeys)>6)stop('global sensor limit')
    # No changed/expanded date plans on a later invocation: cache-only replay.
    for(old in ledger$attempts) if(old$kind=='data'&&length(intersect(unlist(old$sensors),keys))&&old$duration==spec$params$duration&&old$key!=key)stop('changed interval requires new authorization')
    ledger$sensors<-as.list(unionkeys)
    if(spec$params$duration=='HOURLY') { h<-union(unlist(ledger$hourly),keys);if(length(h)>1)stop('pilot allows one SNOTEL hourly sensor');ledger$hourly<-as.list(h) }
  } else keys<-character()
  url<-paste0('https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/',spec$kind,'?',paste(vapply(names(spec$params),function(n)paste0(n,'=',utils::URLencode(as.character(spec$params[[n]]),reserved=TRUE)),''),collapse='&'))
  if(is.null(transport))transport<-function(url)curl::curl_fetch_memory(url,handle=curl::new_handle(followlocation=FALSE,timeout=45,connecttimeout=15,useragent='BRIM-SM1-bounded-compatibility-pilot/1.0',maxfilesize=20000000))
  # Finite retries only. Redirects counted as received attempts, never followed.
  for(attempt in 1:2) {
    if(length(ledger$attempts)>=80)stop('NRCS budget exhausted')
    recent<-tail(ledger$attempts,2)
    if(length(recent)==2&&all(vapply(recent,function(x)isTRUE(x$service_error),TRUE)))stop('consecutive service errors; pilot stopped')
    entry<-list(key=key,kind=spec$kind,url=url,started_utc=format(Sys.time(),tz='UTC',usetz=TRUE),status='reserved',sensors=as.list(keys),duration=spec$params$duration,service_error=FALSE)
    ledger$attempts[[length(ledger$attempts)+1L]]<-entry;sm1_json(ledger,ledgerfile) # reserve before transport
    response<-tryCatch(transport(url),error=function(e)e)
    err<-inherits(response,'error'); status<-if(err)0L else response$status_code
    entry$status<-status;entry$error<-if(err)conditionMessage(response)else NULL
    entry$service_error<-err||status==429||status>=500
    entry$finished_utc<-format(Sys.time(),tz='UTC',usetz=TRUE)
    ledger$attempts[[length(ledger$attempts)]]<-entry;sm1_json(ledger,ledgerfile)
    if(!err&&status>=200&&status<300) {
      if(length(response$content)>20000000)stop('response size limit')
      value<-jsonlite::fromJSON(rawToChar(response$content),simplifyVector=FALSE)
      writeBin(response$content,bodyfile)
      sm1_json(list(spec=spec,url=url,status=status,received_utc=entry$finished_utc,bytes=length(response$content),sha256=digest::digest(file=bodyfile,algo='sha256')),infofile)
      return(value)
    }
    if(!entry$service_error||attempt==2)stop(paste('request failed',status,entry$error))
    delay<-2L*attempt
    if(!err) { headers<-rawToChar(response$headers); line<-regmatches(headers,regexpr('(?i)retry-after:[^\r\n]+',headers,perl=TRUE)); if(length(line)&&nzchar(line)){v<-trimws(sub('^[^:]+:','',line));n<-suppressWarnings(as.numeric(v));if(!is.finite(n)) n<-as.numeric(as.POSIXct(v,format='%a, %d %b %Y %H:%M:%S',tz='GMT')-Sys.time(),units='secs');if(!is.finite(n)||n>30)stop('Retry-After exceeds bounded wait; stop');delay<-max(delay,n)} }
    Sys.sleep(delay)
  }
}
sm1_metadata_spec <- function()list(kind='stations',params=list(stationTriplets=paste(sm1_triplets,collapse=','),elements='SMS:*:*',returnStationElements='true',activeOnly='false'))
sm1_data_spec <- function(triplet,elements,begin,end,duration='DAILY')list(kind='data',params=list(stationTriplets=triplet,elements=paste(elements,collapse=','),duration=duration,beginDate=begin,endDate=end,periodRef='END',returnFlags='true',returnOriginalValues='true',returnSuspectData='true'))
