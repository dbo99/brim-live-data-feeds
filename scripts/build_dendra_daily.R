#!/usr/bin/env Rscript
# Explicit durable state/output paths; no implicit writes to docs/data or cwd.
args_all<-commandArgs(FALSE);script<-if(sys.nframe()>0) sys.frame(1)$ofile else sub("^--file=","",args_all[grepl("^--file=",args_all)][1]);script_dir<-dirname(normalizePath(script))
source(file.path(script_dir,"dendra/core.R"))
source(file.path(script_dir,"dendra/integrated.R"))
source(file.path(script_dir,"dendra/archive.R"))
for(p in c("jsonlite","digest")) if(!requireNamespace(p,quietly=TRUE)) stop("Missing project dependency: ",p)
parse_args<-function(x) {
  allowed<-c("mode","catalog","state","output","native-manifest","as-of","days","start","end","request-generation","ledger","budget","loss-review","seed-manifest","collection-limits")
  if(length(x)%%2) stop("Arguments require --name value pairs")
  a<-list();for(i in seq(1,length(x),by=2)) {key<-sub("^--","",x[i]);if(!startsWith(x[i],"--")||!key %in% allowed||!is.null(a[[key]])) stop("Unknown/duplicate argument: ",x[i]);a[[key]]<-x[i+1]};a
}
run<-function(a,clock=Sys.time) {
  if(!is.null(a$catalog)&&identical(json_read(a$catalog)$integration$version,DENDRA_ARCHIVE_INTEGRATION))return(archive_run(a,clock))
  run_start<-format(clock(),"%Y-%m-%dT%H:%M:%SZ",tz="UTC")
  mode<-a$mode %||% "plan"
  if(!mode %in% c("replay","replay-update","plan","backfill","update","reconcile")) stop("Invalid mode")
  for(k in c("catalog","state","output")) if(is.null(a[[k]])) stop("Required --",k)
  catalog<-json_read(a$catalog);integrated<-integrated_catalog(catalog)
  frozen<-mode %in% c("replay","replay-update")
  if(mode=="replay-update"&&!integrated)stop("Replay-update requires explicit integrated frozen state")
  if(sum(vapply(catalog$streams,function(s)identical(s$parameter,"soil_temperature"),logical(1)))>1) stop("D1 permits only one temperature stream")
  state<-normalizePath(a$state,mustWork=FALSE);output<-normalizePath(a$output,mustWork=FALSE)
  if(!grepl("^/",state)||!grepl("^/",output)) stop("Absolute durable paths required")
  dir.create(state,recursive=TRUE,showWarnings=FALSE)
  lock<-file.path(state,"writer.lock");if(!dir.create(lock,showWarnings=FALSE)) stop("State writer lock exists; inspect interrupted run before removing")
  on.exit(unlink(lock,recursive=TRUE),add=TRUE)
  asof<-a[['as-of']] %||% run_start
  if(frozen&&is.null(a[['as-of']])) stop("Replay needs an explicit frozen --as-of")
  if(mode %in% c("update","backfill","reconcile")) validate_live_time(asof,run_start)
  if(!is.null(a[["loss-review"]])&&mode!="reconcile") stop("Loss review is only valid for explicit reconciliation")
  cutoff<-completed_cutoff(asof);end<-as.character(as.Date(cutoff)+1);runid<-paste0(format(parse_utc(run_start),"%Y%m%dT%H%M%S",tz="UTC"),"-",Sys.getpid())
  runpath<-file.path(state,"runs",runid);dir.create(runpath,recursive=TRUE)
  current_file<-file.path(state,"current.json")
  prior_file<-if(integrated&&!frozen)file.path(state,"published.json") else current_file
  prior<-if(file.exists(prior_file))json_read(prior_file) else NULL
  if(integrated&&!frozen&&is.null(prior)&&file.exists(current_file))stop("Prepared state is not published; restore acknowledged public state or use a fresh explicit bootstrap")
  prior_gen<-if(!is.null(prior)) file.path(state,"generations",prior$generation) else NULL
  if(!is.null(prior)) {
    if(!grepl("^[0-9]{8}T[0-9]{6}-[0-9]+$",prior$generation)||!identical(prior$policy_version,DENDRA_POLICY)) stop("Invalid restored generation/policy")
    if(sha_file(file.path(prior_gen,"index.json"))!=prior$index_sha256) stop("Restored index integrity mismatch")
    if(is.null(prior$files)) stop("Restored state lacks a complete file inventory")
    expected_files<-sort(c("index.json",paste0("daily/",unlist(prior$stream_ids),".json")))
    actual_files<-vapply(prior$files,`[[`,character(1),"path")
    if(anyDuplicated(actual_files)||!identical(sort(actual_files),expected_files)||!identical(sort(list.files(prior_gen,recursive=TRUE)),expected_files)) stop("Restored state inventory closure mismatch")
    for(f in prior$files) {
      if(!grepl("^(index[.]json|daily/[0-9a-f]{24}[.]json)$",f$path)||sha_file(file.path(prior_gen,f$path))!=f$sha256) stop("Restored daily integrity mismatch")
    }
  }
  if(mode %in% c("update","reconcile","replay-update")&&is.null(prior)) stop("No durable prior state; restore state or choose explicit bounded backfill")
  if(!is.null(prior)&&mode=="replay") stop("Replay requires a new state directory")
  if(!is.null(prior)&&!identical(sort(vapply(catalog$streams,`[[`,character(1),"datastream_id")),sort(unlist(prior$stream_ids)))) stop("Selection changes require explicit migration, not implicit deletion")
  days<-as.integer(a$days %||% "7");if(is.na(days)||days<1) stop("Positive days required")
  intervals<-lapply(catalog$streams,function(s) {
    parameter<-s$parameter %||% "soil_moisture";maxdays<-if(parameter=="soil_temperature") 90 else 30
    interval_days<-if(integrated&&is.null(a$days))catalog$integration[[if(mode=="backfill")"initialization_days" else "update_days"]][[parameter]] else days
    if(!finite_number(interval_days)||interval_days<1||interval_days!=floor(interval_days))stop("Invalid parameter interval configuration")
    if(interval_days>maxdays && !frozen) stop("D1 live interval exceeds parameter budget")
    start<-a$start %||% as.character(as.Date(end)-interval_days);stopdate<-a$end %||% end
    if(as.Date(start)>=as.Date(stopdate)||stopdate>end||as.integer(as.Date(stopdate)-as.Date(start))>maxdays) stop("Invalid bounded interval")
    if(mode!="reconcile"&&(start<as.character(as.Date(end)-maxdays)||stopdate!=end)) stop("Live backfill/update must use recent completed days")
    list(stream=s,start=start,end=stopdate)
  })
  plan<-list(schema_version=DENDRA_SCHEMA,mode=mode,as_of_utc=asof,cutoff=cutoff,catalog=catalog,intervals=intervals,request_generation=a[['request-generation']] %||% gsub("-","",cutoff),budget=as.integer(a$budget %||% "80"))
  json_write(plan,file.path(runpath,"plan.json"))
  if(mode=="plan") {json_write(plan,output);cat("Wrote bounded plan\n");return(invisible(NULL))}
  if(mode=="reconcile"&&is.null(a[['request-generation']])) stop("Reconcile needs explicit --request-generation for resumable new source evidence")
  native_manifest<-a[['native-manifest']]
  if(!frozen) {
    if(!is.null(native_manifest)) stop("Live modes require collection; fixture replay uses replay mode")
    if(is.null(a$ledger)) stop("Live mode requires shared --ledger")
    # Fresh discovery must bind the selected IDs, units, public status and depths.
    if(!identical(catalog$live_identity_status,"verified")||is.null(catalog$verified_at_utc)||parse_utc(catalog$verified_at_utc)>parse_utc(run_start)||as.numeric(difftime(parse_utc(run_start),parse_utc(catalog$verified_at_utc),units="hours"))>24) stop("Live catalog needs current verified public metadata")
    result<-system2(Sys.getenv("PYTHON",unset="python3"),shQuote(c(file.path(script_dir,"dendra/bridge.py"),"collect","--plan",file.path(runpath,"plan.json"),"--state",file.path(state,"intervals"),"--output",file.path(runpath,"native"),"--ledger",a$ledger,"--budget",a$budget %||% "80")))
    if(result!=0) stop("Incomplete source retrieval; prior state/candidate preserved; see run/native and interval failures")
    native_manifest<-file.path(runpath,"native/native_manifest.json")
  }
  if(is.null(native_manifest)) stop("Replay requires --native-manifest")
  native<-json_read(native_manifest)
  if(length(native$failures)||identical(native$complete,FALSE)) stop("Incomplete native manifest")
  selected<-vapply(catalog$streams,`[[`,character(1),"datastream_id")
  if(anyDuplicated(selected)||!identical(sort(selected),sort(vapply(native$streams,function(s)s$stream$datastream_id,character(1))))) stop("Native selection mismatch")
  generation<-file.path(state,"generations",runid);dir.create(generation,recursive=TRUE)
  index<-list(schema_version=if(integrated)DENDRA_INDEX_SCHEMA else DENDRA_SCHEMA,integration_version=if(integrated)DENDRA_INTEGRATION else NULL,retention=if(integrated)retention_contract() else NULL,policy_version=DENDRA_POLICY,product_id="dendra-daily",generation=runid,parent_generation=prior$generation %||% NULL,
    mode=if(frozen) "replay" else "live",as_of_utc=asof,run_started_at_utc=run_start,safeguard_version=DENDRA_SAFEGUARDS,metadata_verified_at_utc=catalog$verified_at_utc %||% NULL,complete_through_date=cutoff,generated_at_utc=format(Sys.time(),"%Y-%m-%dT%H:%M:%SZ",tz="UTC"),publication_time_utc=NULL,
    completeness="complete_selected_catalog",scope="limited configured selection; not statewide completeness",streams=list(),stations=catalog$stations)
  assessments<-list()
  for(j in seq_along(native$streams)) {
    item<-native$streams[[j]];s<-catalog$streams[[match(item$stream$datastream_id,selected)]];sid<-s$datastream_id
    if(!grepl("^[0-9a-f]{24}$",sid)) stop("Unsafe stream ID")
    station<-Filter(function(x)x$station_id==s$station_id,catalog$stations)
    if(length(station)!=1||!isFALSE(s$source_is_hidden)||!isFALSE(station[[1]]$source_is_hidden)||s$public_level!=3||station[[1]]$public_level!=3) stop("Public/hidden restriction prevents processing")
    if(sha_file(item$native_csv)!=item$native_sha256) stop("Native CSV integrity failure")
    x<-read_native(item$native_csv);x<-normalize_native(x,s)
    previous<-if(!is.null(prior_gen)) json_read(file.path(prior_gen,"daily",paste0(sid,".json"))) else NULL
    if(!is.null(previous)) {
      identity<-function(z) list(datastream_id=z$datastream_id,station_id=z$station_id,parameter=z$parameter %||% "soil_moisture",depth_cm=z$depth_cm,orientation=z$orientation,source_attributes=z$source_attributes,native_unit_name=z$native_unit_name,unit_status=z$unit_normalization$status,multiplier=z$unit_normalization$multiplier,offset=z$unit_normalization$offset %||% 0,terms=z$source_terms)
      if(!isTRUE(all.equal(identity(previous$stream),identity(s),check.attributes=FALSE))) stop("Numerical stream identity changed; explicit reviewed migration required")
    }
    ctx<-if(!is.null(previous)) previous$cadence_context else cadence_context(x,s,item$native_sha256)
    if(mode=="replay") {
      cap<-sprintf("%d-10-01",water_year(cutoff)-10)
      if(!is.null(a$start)) start<-a$start else {
        observed<-as.Date(parse_utc(s$observed_start_utc)-28800,tz="UTC")
        start<-max(cap,sprintf("%d-10-01",water_year(observed)-1))
      }
      if(integrated)start<-max(start,retention_start(s$parameter,cutoff))
      stopdate<-end
      if(tail(item$chunks,1)[[1]]$requested_interval$end_exclusive!=paste0(end,"T08:00:00.000Z")) stop("Replay cutoff does not match completed checkpoint")
    } else {start<-item$start;stopdate<-item$end}
    before<-if(!is.null(previous)) Filter(function(r)r$date<start,previous$rows) else list()
    observed_before<-Filter(function(r)r$n_total>0&&!is.null(r$cadence_seconds),before)
    prevcad<-if(length(observed_before)) tail(observed_before,1)[[1]]$cadence_seconds else previous$aggregation$prior_observed_cadence_seconds %||% NULL
    if(integrated&&is.null(previous)&&any(x$date<start)) {
      d<-tail(x$date[x$date<start],1)
      prevcad<-aggregate_daily(x,s,d,as.character(as.Date(d)+1),ctx)$previous_cadence
    }
    rows<-aggregate_daily(x,s,start,stopdate,ctx,prevcad)$rows
    if(!is.null(previous)) {
      replacement<-replacement_assessment(previous$rows,rows,start,stopdate,sid)
      replacement$assessment$replacement_sha256<-digest::digest(jsonlite::toJSON(rows,auto_unbox=TRUE,null="null",digits=NA),algo="sha256",serialize=FALSE)
      replacement$assessment$native_sha256<-item$native_sha256
      assessments[[length(assessments)+1L]]<-replacement$assessment
      # Diagnostic cadence-change flags depend on the immediately preceding observed day.
      # Reconcile the carried suffix's transition flags without recalculating its means.
      allrows<-replacement$rows;lastcad<-previous$aggregation$prior_observed_cadence_seconds %||% NULL
      for(k in seq_along(allrows)) {
        r<-allrows[[k]];r$flags<-Filter(function(f)f!="cadence_changed_from_previous_observed_day",r$flags)
        if(r$n_total>0&&!is.null(r$cadence_seconds)) {if(!is.null(lastcad)&&abs(r$cadence_seconds-lastcad)>1)r$flags<-c(r$flags,list("cadence_changed_from_previous_observed_day"));lastcad<-r$cadence_seconds}
        allrows[[k]]<-r
      }
      rows<-allrows
      # A reconciliation cannot pretend to advance an unqueried present-day cutoff.
      cutoff_stream<-max(previous$aggregation$complete_through_date,as.character(as.Date(stopdate)-1))
      if(cutoff_stream!=cutoff) stop("History has an unqueried gap to this run cutoff; update recent state first")
    }
    source_chunks<-c(previous$source_snapshot$chunks %||% list(),item$chunks)
    query<-NULL;carry_cadence<-prevcad
    if(integrated) {
      query<-update_query_evidence(previous$source_snapshot$query_by_date,x,start,stopdate,item$chunks)
      trimmed<-trim_product(rows,query,source_chunks,s$parameter,cutoff,previous$aggregation$prior_observed_cadence_seconds %||% prevcad)
      rows<-trimmed$rows;query<-trimmed$query;source_chunks<-trimmed$chunks;carry_cadence<-trimmed$prior_cadence
    }
    dates<-vapply(rows,`[[`,character(1),"date")
    if(anyDuplicated(dates)||!identical(dates,sort(dates))||!identical(dates,as.character(seq(as.Date(dates[1]),as.Date(tail(dates,1)),by="day")))) stop("Daily calendar is not contiguous/unique")
    numeric<-Filter(function(r)!is.null(r$mean_native),rows);good<-Filter(function(r)isTRUE(r$plot_eligible),rows)
    aggregation<-list(method="arithmetic_mean_of_reported_values",day_timezone="UTC-08:00",day_bounds_utc="08:00Z inclusive through next 08:00Z exclusive",complete_through_date=cutoff,start_date=dates[1],end_date_exclusive=end,percent_conversion_verified=identical(s$unit_normalization$status,"verified_percent_conversion"),coverage_threshold=.75,cadence_context_version=ctx$version,policy_version=DENDRA_POLICY)
    summary<-daily_summary(rows)
    if(integrated)aggregation$prior_observed_cadence_seconds<-carry_cadence
    product<-list(schema_version=DENDRA_SCHEMA,stream=s,aggregation=aggregation,summary=summary,cadence_context=ctx,source_snapshot=list(chunks=source_chunks,native_row_count=item$native_row_count,query_by_date=query),generated_at_utc=index$generated_at_utc,rows=rows)
    json_write(product,file.path(generation,"daily",paste0(sid,".json")))
    latest_retrieval<-max(vapply(source_chunks,`[[`,character(1),"retrieval_last_utc"))
    index$streams[[j]]<-list(datastream_id=sid,station_id=s$station_id,parameter=s$parameter %||% "soil_moisture",source_name=s$source_name,sensor_label=s$sensor_label,depth_cm=s$depth_cm,orientation=s$orientation,native_unit_name=s$native_unit_name,unit_normalization=s$unit_normalization,source_terms=s$source_terms,source_attributes=s$source_attributes,public_level=s$public_level,source_is_hidden=s$source_is_hidden,source_is_geo_protected=s$source_is_geo_protected,cadence_context=ctx,summary=summary,last_retrieved_at_utc=latest_retrieval,latest_observation_utc=if(integrated)retained_observation(query) else tail(item$chunks,1)[[1]]$latest_observation_utc,current_interval_retrieved_at_utc=if(integrated)query[[cutoff]]$retrieved_at_utc else NULL,
      change=if((s$parameter %||% "soil_moisture")=="soil_moisture") setNames(lapply(c(7,14,30),function(n)recent_change(rows,cutoff,n)),c("7","14","30")) else NULL)
  }
  loss<-loss_decision(assessments)
  loss$prior_index_sha256<-prior$index_sha256 %||% NULL
  loss$request_generation<-plan$request_generation
  evidence_hash<-digest::digest(jsonlite::toJSON(loss,auto_unbox=TRUE,null="null",digits=NA),algo="sha256",serialize=FALSE)
  loss$assessment_sha256<-evidence_hash
  json_write(loss,file.path(runpath,"loss_assessment.json"))
  if(loss$hold) {
    review<-if(!is.null(a[["loss-review"]])) json_read(a[["loss-review"]]) else NULL
    valid_review<-mode=="reconcile"&&!is.null(review)&&identical(review$decision,"approve_exact_removal")&&
      identical(review$assessment_sha256,evidence_hash)&&identical(review$prior_index_sha256,prior$index_sha256)&&
      identical(review$request_generation,plan$request_generation)&&identical(review$version,DENDRA_SAFEGUARDS)&&
      all(vapply(c("review_id","reviewed_by","reason"),function(k)is.character(review[[k]])&&length(review[[k]])==1&&nzchar(trimws(review[[k]])),logical(1)))
    if(!valid_review) stop("Suspicious mass loss held; prior current pointer/data preserved. Review ",file.path(runpath,"loss_assessment.json"))
    json_write(review,file.path(runpath,"applied_loss_review.json"))
  }
  # Protected station coordinates are omitted, never reconstructed.
  index$stations<-lapply(index$stations,function(s){
    if(integrated) {
      members<-Filter(function(x)x$station_id==s$station_id,index$streams)
      s$selected_stream_ids<-lapply(members,function(x)x$datastream_id)
      s$selected_stream_count<-length(members)
    }
    if(isTRUE(s$source_is_geo_protected))s$geometry<-NULL
    s
  })
  json_write(index,file.path(generation,"index.json"))
  # Adapter validates the complete set before the sole current pointer is activated.
  result<-system2(Sys.getenv("PYTHON",unset="python3"),shQuote(c(file.path(script_dir,"dendra_candidate.py"),"build","--generation",generation,"--output",output)))
  if(result!=0) stop("Candidate validation failed; prior state preserved")
  pointer<-list(generation=runid,stream_ids=as.list(selected),policy_version=DENDRA_POLICY,candidate_root=file.path(output,runid,"candidate"),index_sha256=sha_file(file.path(generation,"index.json")),files=lapply(list.files(generation,recursive=TRUE),function(p)list(path=p,sha256=sha_file(file.path(generation,p)))))
  if(integrated) {
    # Publish acknowledgement is separate. This portable pointer is prepared only.
    result<-system2(Sys.getenv("PYTHON",unset="python3"),shQuote(c(file.path(script_dir,"dendra_state.py"),"stage","--state",state,"--candidate",pointer$candidate_root)))
    if(result!=0)stop("Portable candidate staging failed; prior state preserved")
    pointer$candidate_root<-NULL;pointer$candidate_relpath<-paste0("artifacts/",runid,"/candidate")
    pointer$state_role<-"prepared";pointer$integration_version<-DENDRA_INTEGRATION
  }
  json_write(pointer,file.path(state,"current.next.json"));if(!file.rename(file.path(state,"current.next.json"),current_file)) stop("Cannot activate state")
  cat("Completed",mode,"with",length(index$streams),"streams; generation",runid,"\n")
}
if(sys.nframe()==0) tryCatch(run(parse_args(commandArgs(TRUE))),error=function(e){message(conditionMessage(e));quit(status=2)})
