# Synthetic R-boundary checks; preserve every output under explicit test root.
source("scripts/dendra/core.R")
root <- Sys.getenv("DENDRA_TEST_ROOT")
if(!nzchar(root)||!dir.exists(root)) stop("Explicit task-owned test root required")
root <- tempfile("daily-prepare-r-",tmpdir=root)
dir.create(root)
wrapper <- normalizePath("scripts/dendra/history_acquisition/daily_prepare.R")
core <- normalizePath("scripts/dendra/core.R")
sid <- "63531a67a9b61453fa1ca4ed"
count <- 0L
check <- function(ok,label) {if(!isTRUE(ok)) stop(label);count<<-count+1L;cat("PASS",label,"\n")}
run_case <- function(name, rows, intervals, configured=NULL, asof="2024-07-04T08:00:00.000Z") {
  base<-file.path(root,name);dir.create(base);dir.create(file.path(base,"native"));dir.create(file.path(base,"lineage"))
  path<-file.path(base,"native",paste0(sid,".csv"));write.csv(rows,path,row.names=FALSE,na="")
  lineage<-file.path(base,"lineage",paste0(sid,".json"));writeLines("{}",lineage)
  s<-list(identity=list(stream_id=sid,station_id="635319fcb055ac5348842453",native_unit="Percent",depth_cm=20,orientation="horizontal"),
    csv=list(path=paste0("native/",sid,".csv"),sha256=sha_file(path),bytes=file.info(path)$size),
    lineage=list(path=paste0("lineage/",sid,".json"),sha256=sha_file(lineage),bytes=file.info(lineage)$size),
    unit_normalization=list(status="verified_percent_conversion",multiplier=1,offset=0),
    daily_route="completed_daily",configured_cadence_seconds=configured,intervals=intervals)
  h<-list(schema_version="dendra-sealed-daily-handoff-1",source_scope="historical_sealed_intervals",cadence_mode="initialize",
    science_binding=list(core_sha256=sha_file(core),wrapper_sha256=sha_file(wrapper),collector_fingerprint=paste(rep("0",64),collapse="")),
    streams=list(s),as_of=asof)
  input<-file.path(base,"handoff.json");output<-file.path(base,"daily-output.json");json_write(h,input)
  status<-system2(file.path(R.home("bin"),"Rscript"),c("--vanilla",shQuote(wrapper),shQuote(input),shQuote(output)),
                  stdout=file.path(base,"stdout.txt"),stderr=file.path(base,"stderr.txt"))
  if(status!=0L) stop(paste("Wrapper failed",name))
  json_read(output)
}
native <- function(times, values=rep(0,length(times))) data.frame(t=times,datastream_id=sid,v=values,
  value_status="number",duplicate_conflict=FALSE,alternative_out_of_range=FALSE,stringsAsFactors=FALSE)
interval <- function(start,end,state="COMPLETE_NONEMPTY") list(start=start,end=end,query_state=state,task_id="synthetic",
  seal_record_sha256=paste(rep("0",64),collapse=""),content_sha256=paste(rep("1",64),collapse=""),parsed_sha256=paste(rep("2",64),collapse=""))
times <- function(start,n,seconds=3600) format(parse_utc(start)+seq(0,by=seconds,length.out=n),"%Y-%m-%dT%H:%M:%OS3Z",tz="UTC")
x<-native(c(times("2024-07-01T08:00:00Z",24),times("2024-07-03T08:00:00Z",24)))
g<-run_case("summer-gap-zero",x,list(interval("2024-07-01T08:00:00Z","2024-07-02T08:00:00Z"),interval("2024-07-03T08:00:00Z","2024-07-04T08:00:00Z")))
r<-g$rows[[sid]]
check(length(r)==3L && r[[1]]$date=="2024-07-01" && r[[1]]$n_total==24L,"fixed PST summer day is 08Z, no DST")
check(r[[1]]$mean_native==0 && r[[1]]$mean_percent==0 && isTRUE(r[[1]]$plot_eligible),"numeric zero survives real R handoff")
check(r[[2]]$n_total==0L && is.null(r[[2]]$mean_native) && !r[[2]]$query_complete && !r[[2]]$plot_eligible,"unqueried gap has no synthetic observation")
check("no_observations" %in% r[[2]]$flags,"gap retains science reason")
check(r[[1]]$representation=="completed_daily" && length(g$latest_instantaneous)==0L,"daily and instantaneous products distinct")
p<-run_case("incomplete-current-day",native(times("2024-07-01T08:00:00Z",30)),
  list(interval("2024-07-01T08:00:00Z","2024-07-02T14:00:00Z")),asof="2024-07-02T14:00:00.000Z")
check(length(p$rows[[sid]])==1L && p$rows[[sid]][[1]]$date=="2024-07-01","current incomplete day excluded")
f<-run_case("configured-fallback",native("2024-07-01T08:00:00Z"),
  list(interval("2024-07-01T08:00:00Z","2024-07-02T08:00:00Z")),configured=3600)
check(f$cadence_contexts[[sid]]$seconds==3600 && f$cadence_contexts[[sid]]$source=="configured_stream_interval","reviewed configured fallback preserved")
check(!f$rows[[sid]][[1]]$plot_eligible && "insufficient_temporal_span" %in% f$rows[[sid]][[1]]$flags,"existing sparse QC remains unchanged")
o<-run_case("observed-precedence",native(times("2024-07-01T08:00:00Z",144,600)),
  list(interval("2024-07-01T08:00:00Z","2024-07-02T08:00:00Z")),configured=3600)
check(o$cadence_contexts[[sid]]$seconds==600 && o$rows[[sid]][[1]]$cadence_source=="observed_day_mode","observed cadence overrides configured fallback")
x<-native(times("2024-07-01T08:00:00Z",24));x$v[1:3]<-NA;x$value_status[1:3]<-c("missing","null","invalid");x$duplicate_conflict[4]<-TRUE
d<-run_case("distinctions",x,list(interval("2024-07-01T08:00:00Z","2024-07-02T08:00:00Z")))$rows[[sid]][[1]]
check(d$n_missing==1 && d$n_null==1 && d$n_invalid==1 && d$n_duplicate_conflicts==1,"missing null invalid conflict survive")
check(!d$plot_eligible && "duplicate_timestamp_conflicts" %in% d$flags,"conflict remains ineligible")
cat("DAILY_PREPARE_R_CHECKS_PASS",count,"\n")
