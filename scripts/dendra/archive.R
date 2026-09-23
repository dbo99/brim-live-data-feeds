# Integration2 orchestration; all means, flags and recent changes reuse core.R.
DENDRA_ARCHIVE_INTEGRATION <- "dendra-integration-2"
archive_windows <- function(days=90L) {
  if(!finite_number(days)||days!=floor(days)||days<1||days>3660)stop("Invalid explicit temperature window")
  list(version="dendra-windows-2",soil_moisture=list(archive="all_acquired_daily_no_automatic_deletion",display_water_years=10L,reference="not_computed"),soil_temperature=list(archive_completed_days=days,display_completed_days=days,reference="not_computed"))
}
archive_identity <- function(s) s[c("datastream_id","station_id","parameter","depth_cm","orientation","native_unit_name","unit_normalization","source_terms","source_attributes","public_level","source_is_hidden","source_is_geo_protected")]
# Serialize the same scalar daily columns in a data frame. jsonlite's vector
# encoder avoids an S4 dispatch for every cell; flags remain JSON arrays and
# missing scalars remain explicit nulls. No statistics are calculated here.
archive_write_product <- function(p,path) {
  rows<-p$rows;keys<-names(rows[[1]])
  if(!length(rows)||!all(vapply(rows,function(r)!anyDuplicated(names(r))&&setequal(names(r),keys),logical(1))))stop("Archive row serialization shape")
  columns<-setNames(lapply(keys,function(k) {
    values<-lapply(rows,`[[`,k)
    if(k=="flags")return(I(values))
    present<-Filter(Negate(is.null),values)
    if(!length(present))return(rep(NA_real_,length(values)))
    proto<-present[[1]]
    family<-if(is.numeric(proto))is.numeric else if(is.logical(proto))is.logical else if(is.character(proto))is.character else function(x)FALSE
    if(!all(vapply(present,function(v)length(v)==1&&family(v),logical(1))))stop("Archive scalar serialization type")
    if(is.numeric(proto))proto<-as.numeric(proto)
    if(length(proto)!=1||!is.atomic(proto))stop("Archive scalar serialization field")
    missing<-if(is.logical(proto))NA else if(is.numeric(proto))NA_real_ else NA_character_
    vapply(values,function(v)if(is.null(v))missing else v,proto)
  }),keys)
  p$rows<-structure(columns,class="data.frame",row.names=seq_along(rows))
  json_write(p,path)
}
archive_run <- function(a,clock=Sys.time) {
  mode<-a$mode %||% "plan";if(!mode %in% c("bootstrap","plan","update","replay-update","reconcile","replay-reconcile"))stop("Archive2 requires explicit bootstrap/update/reconcile")
  for(k in c("catalog","state","output","as-of"))if(is.null(a[[k]]))stop("Archive2 required --",k)
  catalog<-json_read(a$catalog);windows<-archive_windows(catalog$integration$temperature_days %||% 90L)
  if(!identical(catalog$integration$version,DENDRA_ARCHIVE_INTEGRATION))stop("Archive2 version mismatch")
  state<-normalizePath(a$state,mustWork=FALSE);output<-normalizePath(a$output,mustWork=FALSE)
  if(!grepl("^/",state)||!grepl("^/",output))stop("Absolute state/output required")
  dir.create(state,recursive=TRUE,showWarnings=FALSE);lock<-file.path(state,"writer.lock")
  if(!dir.create(lock,showWarnings=FALSE))stop("State writer lock exists")
  on.exit(unlink(lock,recursive=TRUE),add=TRUE)
  started<-format(clock(),"%Y-%m-%dT%H:%M:%SZ",tz="UTC");asof<-a[["as-of"]];cutoff<-completed_cutoff(asof);end<-as.character(as.Date(cutoff)+1)
  frozen<-mode %in% c("bootstrap","replay-update","replay-reconcile","plan");reconciling<-mode %in% c("reconcile","replay-reconcile")
  if(!frozen)validate_live_time(asof,started)
  if(!is.null(a[["loss-review"]])&&!reconciling)stop("Loss review only in explicit reconciliation")
  runid<-paste0(format(parse_utc(started),"%Y%m%dT%H%M%S",tz="UTC"),"-",Sys.getpid());runpath<-file.path(state,"runs",runid)
  if(dir.exists(runpath))stop("Generation collision")
  dir.create(runpath,recursive=TRUE)
  prior_path<-if(file.exists(file.path(state,"published.json")))file.path(state,"published.json") else file.path(state,"seed.json")
  prior<-if(file.exists(prior_path))json_read(prior_path) else NULL
  if(mode=="bootstrap"&&any(file.exists(file.path(state,c("seed.json","published.json","current.json")))))stop("Bootstrap requires fresh state")
  if(mode %in% c("update","replay-update","reconcile","replay-reconcile")&&is.null(prior))stop("No acknowledged parent or explicitly imported seed")
  # Daily objects live on disk and are loaded one stream at a time. No semantic
  # trust cache: the full parent is validated by materialize before any use.
  products<-list();old_index<-NULL
  working_daily<-file.path(runpath,"generation/daily");dir.create(working_daily,recursive=TRUE)
  read_product<-function(sid)if(is.null(products[[sid]]))NULL else json_read(products[[sid]])
  save_product<-function(sid,p) {
    path<-file.path(working_daily,paste0(sid,".json"));archive_write_product(p,path);products[[sid]]<<-path
  }
  if(!is.null(prior)) {
    # Validate/restore from partitioned portable candidate, never current unacknowledged output.
    candidate<-file.path(state,prior$candidate_relpath)
    if(!identical(prior$candidate_relpath,paste0("artifacts/",prior$generation,"/candidate")))stop("Unsafe archive parent path")
    args<-c(file.path(script_dir,"dendra_archive.py"),"materialize","--root",candidate,"--output",file.path(runpath,"prior"))
    if(system2(Sys.getenv("PYTHON",unset="python3"),shQuote(args))!=0)stop("Archive parent failed validation/materialization")
    old_index<-json_read(file.path(runpath,"prior/index.json"))
    if(!identical(prior$generation,old_index$generation)||!identical(prior$index_sha256,sha_file(file.path(candidate,"docs/data/dendra/index.json"))))stop("Archive parent binding mismatch")
    for(s in old_index$streams)products[[s$datastream_id]]<-file.path(runpath,"prior/daily",paste0(s$datastream_id,".json"))
  }
  selected<-vapply(catalog$streams,`[[`,character(1),"datastream_id")
  if(anyDuplicated(selected)||!length(selected)||length(selected)>1024||any(!grepl("^[a-f0-9]{24}$",selected)))stop("Archive selected identity bound")
  previous_ids<-names(products) %||% character();added<-setdiff(selected,previous_ids)
  if(length(setdiff(previous_ids,selected)))stop("Archive selection removal is not authorized")
  if(length(added)&&!is.null(prior)) {
    review<-catalog$selection_change
    signature<-digest::digest(paste(sort(previous_ids),collapse="\n"),algo="sha256",serialize=FALSE)
    if(is.null(review)||review$version!="dendra-selection-add-1"||!identical(review$prior_selection_sha256,signature)||!setequal(unlist(review$additions),added)||!nzchar(review$review_id %||% "")||!nzchar(review$reason %||% ""))stop("Selection additions require exact reviewed lineage")
  }
  for(s in catalog$streams) {
    st<-Filter(function(t)t$station_id==s$station_id,catalog$stations)
    if(length(st)!=1||s$public_level!=3||!isFALSE(s$source_is_hidden)||st[[1]]$public_level!=3||!isFALSE(st[[1]]$source_is_hidden))stop("Archive public/hidden source restriction")
    if(!s$parameter %in% c("soil_moisture","soil_temperature"))stop("Unsupported archive parameter")
    previous<-read_product(s$datastream_id)
    if(!is.null(previous)&&!isTRUE(all.equal(archive_identity(previous$stream),archive_identity(s),check.attributes=FALSE)))stop("Archive scientific identity changed")
  }
  assessments<-list();seed_binding<-NULL
  if(mode=="bootstrap") {
    if(is.null(a[["seed-manifest"]]))stop("Prepare-only bootstrap requires checksum-bound daily seed manifest")
    seed<-json_read(a[["seed-manifest"]]);if(seed$version!="dendra-saved-seed-2"||!identical(seed$mode,"saved")||!setequal(selected,vapply(seed$streams,`[[`,character(1),"datastream_id")))stop("Seed selection/mode mismatch")
    if(length(seed$streams)!=length(selected)||anyDuplicated(vapply(seed$streams,`[[`,character(1),"datastream_id")))stop("Duplicate bootstrap stream")
    seed_binding<-sha_file(a[["seed-manifest"]])
    for(item in seed$streams) {
      if(sha_file(item$path)!=item$sha256)stop("Daily seed hash mismatch")
      p<-json_read(item$path);s<-catalog$streams[[match(item$datastream_id,selected)]]
      if(!isTRUE(all.equal(archive_identity(p$stream),archive_identity(s),check.attributes=FALSE)))stop("Seed identity mismatch")
      if(is.null(p$source_snapshot$query_by_date)||p$aggregation$complete_through_date>cutoff)stop("Seed query coverage/future cutoff")
      save_product(item$datastream_id,p)
    }
  } else {
    coverage_driven<-mode %in% c("update","plan")
    if(coverage_driven) {
      if(!is.null(a$start)||!is.null(a$end)||(!is.null(a$days)&&a$days!="7"))stop("Routine update uses seven-day overlap plus coverage gaps; use explicit reconciliation for a different interval")
      planner_args<-c(file.path(script_dir,"dendra_coverage.py"),"--catalog",a$catalog,"--as-of",asof,"--output",file.path(runpath,"plan.json"),"--budget",a$budget %||% "0","--checkpoint-state",file.path(state,"intervals"),"--checked-at",format(clock(),"%Y-%m-%dT%H:%M:%OS3Z",tz="UTC"))
      if(!is.null(prior))planner_args<-c(planner_args,"--prior-view",file.path(runpath,"prior"),"--parent",prior_path)
      if(!is.null(a[["request-generation"]]))planner_args<-c(planner_args,"--request-generation",a[["request-generation"]])
      if(!is.null(a[["collection-limits"]]))planner_args<-c(planner_args,"--limits",a[["collection-limits"]])
      if(system2(Sys.getenv("PYTHON",unset="python3"),shQuote(planner_args))!=0)stop("Coverage planning failed; acknowledged state unchanged")
      plan<-json_read(file.path(runpath,"plan.json"));intervals<-plan$intervals
      if(mode=="plan"){json_write(plan,output);return(invisible(NULL))}
      if(!isTRUE(plan$collection_authorized)||length(plan$holds))stop("Coverage initialization horizon requires explicit approval")
    } else {
    # Frozen replay and explicit reconciliation retain their accepted interval policy.
    intervals<-lapply(catalog$streams,function(s){
      days<-as.integer(a$days %||% catalog$integration$update_days[[s$parameter]] %||% 7L)
      maxdays<-if(s$parameter=="soil_temperature")90L else 30L
      start<-a$start %||% as.character(as.Date(end)-days);stopdate<-a$end %||% end
      if(is.na(days)||days<1||days>maxdays||start>=stopdate||stopdate>end||as.integer(as.Date(stopdate)-as.Date(start))>maxdays)stop("Archive bounded source interval")
      if(!reconciling&&stopdate!=end)stop("Archive update must query through completed cutoff")
      list(stream=s,start=start,end=stopdate)
    })
    plan<-list(schema_version=DENDRA_SCHEMA,mode=mode,as_of_utc=asof,cutoff=cutoff,catalog=catalog,intervals=intervals,request_generation=a[["request-generation"]] %||% gsub("-","",cutoff),budget=as.integer(a$budget %||% "0"))
    json_write(plan,file.path(runpath,"plan.json"))
    }
    native_path<-a[["native-manifest"]]
    if(!frozen) {
      if(!is.null(native_path))stop("Live archive updates require transport; fixtures inject at transport boundary")
      if(!identical(catalog$live_identity_status,"verified")||is.null(catalog$verified_at_utc)||parse_utc(catalog$verified_at_utc)>parse_utc(started)||as.numeric(difftime(parse_utc(started),parse_utc(catalog$verified_at_utc),units="hours"))>24)stop("Archive live metadata verification required")
      if(is.null(a$ledger)||is.null(a$budget)||as.integer(a$budget)<=0)stop("Explicit positive source request budget required for live transport")
      if(reconciling&&is.null(a[["request-generation"]]))stop("Reconcile requires request generation")
      args<-c(file.path(script_dir,"dendra/bridge.py"),"collect","--plan",file.path(runpath,"plan.json"),"--state",file.path(state,"intervals"),"--output",file.path(runpath,"native"),"--ledger",a$ledger,"--budget",a$budget)
      if(system2(Sys.getenv("PYTHON",unset="python3"),shQuote(args))!=0)stop("Whole archive candidate held: source failure")
      native_path<-file.path(runpath,"native/native_manifest.json")
    }
    if(is.null(native_path))stop("Saved update needs native manifest")
    built<-format(clock(),"%Y-%m-%dT%H:%M:%OS3Z",tz="UTC")
    if(parse_utc(built)<parse_utc(started))stop("Build clock moved backwards")
    native<-json_read(native_path)
    if(!isTRUE(native$complete)||length(native$failures)||!setequal(selected,vapply(native$streams,function(x)x$stream$datastream_id,character(1))))stop("Whole archive candidate held: failed/partial source selection")
    if(coverage_driven) {
      expected<-vapply(intervals,`[[`,character(1),"task_id");actual<-vapply(native$streams,function(x)x$task_id %||% "",character(1))
      if(!identical(native$version,"dendra-coverage-collection-1")||!identical(native$plan_sha256,plan$plan_sha256)||anyDuplicated(actual)||!setequal(actual,expected)||length(actual)!=length(expected))stop("Whole archive held: incomplete/mismatched coverage tasks")
      native$streams<-native$streams[match(expected,actual)]
    } else if(length(native$streams)!=length(selected))stop("Whole archive candidate held: duplicate source selection")
    for(item in native$streams) {
      sid<-item$stream$datastream_id;s<-catalog$streams[[match(sid,selected)]]
      interval<-if(coverage_driven)intervals[[match(item$task_id,expected)]] else intervals[[match(sid,selected)]]
      if(!isTRUE(all.equal(archive_identity(item$stream),archive_identity(s),check.attributes=FALSE)))stop("Collected stream scientific identity mismatch")
      start<-interval$start;stopdate<-interval$end
      if(!identical(item$start,start)||!identical(item$end,stopdate)||sha_file(item$native_csv)!=item$native_sha256)stop("Archive source interval/hash mismatch")
      previous<-read_product(sid);x<-normalize_native(read_native(item$native_csv),s)
      if(any(x$date<start|x$date>=stopdate))stop("Archive native source over-return")
      ctx<-previous$cadence_context %||% cadence_context(x,s,item$native_sha256)
      prevcad<-preceding_cadence(previous$rows %||% list(),start,previous$aggregation$prior_observed_cadence_seconds %||% NULL)
      rows<-aggregate_daily(x,s,start,stopdate,ctx,prevcad)$rows
      if(!is.null(previous)) {
        replace<-replacement_assessment(previous$rows,rows,start,stopdate,sid)
        replace$assessment$replacement_sha256<-digest::digest(jsonlite::toJSON(rows,auto_unbox=TRUE,null="null",digits=NA),algo="sha256",serialize=FALSE);replace$assessment$native_sha256<-item$native_sha256;assessments[[length(assessments)+1L]]<-replace$assessment
        rows<-replace$rows
        lastcad<-previous$aggregation$prior_observed_cadence_seconds %||% NULL
        for(k in seq_along(rows)) {
          r<-rows[[k]];r$flags<-Filter(function(f)f!="cadence_changed_from_previous_observed_day",r$flags)
          if(r$n_total>0&&!is.null(r$cadence_seconds)){if(!is.null(lastcad)&&abs(r$cadence_seconds-lastcad)>1)r$flags<-c(r$flags,list("cadence_changed_from_previous_observed_day"));lastcad<-r$cadence_seconds}
          rows[[k]]<-r
        }
      }
      query<-update_query_evidence(previous$source_snapshot$query_by_date,x,start,stopdate,item$chunks)
      chunks<-c(previous$source_snapshot$chunks %||% list(),item$chunks)
      # Moisture has no rolling storage deletion. Temperature is explicitly recent context.
      carry<-previous$aggregation$prior_observed_cadence_seconds %||% prevcad
      if(s$parameter=="soil_temperature") {
        keep_start<-as.character(as.Date(cutoff)-windows$soil_temperature$archive_completed_days+1)
        carry<-preceding_cadence(rows,keep_start,carry);rows<-Filter(function(r)r$date>=keep_start,rows)
      }
      dates<-vapply(rows,`[[`,character(1),"date");query<-query[dates];used<-unique(unlist(lapply(query,`[[`,"content_sha256"),use.names=FALSE));chunks<-Filter(function(c)c$content_sha256 %in% used,chunks)
      keys<-vapply(chunks,function(c)jsonlite::toJSON(c,auto_unbox=TRUE,null="null",digits=NA),character(1));chunks<-chunks[!duplicated(keys)]
      complete<-tail(dates,1)
      aggregation<-list(method="arithmetic_mean_of_reported_values",day_timezone="UTC-08:00",day_bounds_utc="08:00Z inclusive through next 08:00Z exclusive",complete_through_date=complete,start_date=dates[1],end_date_exclusive=as.character(as.Date(complete)+1),percent_conversion_verified=identical(s$unit_normalization$status,"verified_percent_conversion"),coverage_threshold=.75,cadence_context_version=ctx$version,policy_version=DENDRA_POLICY,prior_observed_cadence_seconds=carry)
      save_product(sid,list(schema_version=DENDRA_SCHEMA,stream=s,aggregation=aggregation,summary=daily_summary(rows),cadence_context=ctx,source_snapshot=list(chunks=chunks,native_row_count=item$native_row_count,query_by_date=query),generated_at_utc=built,rows=rows))
    }
  }
  if(mode=="bootstrap")built<-started
  loss<-loss_decision(assessments);loss$prior_index_sha256<-prior$index_sha256 %||% NULL;loss$request_generation<-a[["request-generation"]] %||% gsub("-","",cutoff)
  hash<-digest::digest(jsonlite::toJSON(loss,auto_unbox=TRUE,null="null",digits=NA),algo="sha256",serialize=FALSE);loss$assessment_sha256<-hash;json_write(loss,file.path(runpath,"loss_assessment.json"))
  if(loss$hold) {
    review<-if(!is.null(a[["loss-review"]]))json_read(a[["loss-review"]]) else NULL
    if(!reconciling||is.null(review)||review$decision!="approve_exact_removal"||review$version!=DENDRA_SAFEGUARDS||!identical(review$assessment_sha256,hash)||!identical(review$prior_index_sha256,prior$index_sha256)||!identical(review$request_generation,loss$request_generation)||any(!vapply(c("review_id","reviewed_by","reason"),function(k)nzchar(review[[k]] %||% ""),logical(1))))stop("Suspicious source-data loss held for exact reviewed reconciliation")
    json_write(review,file.path(runpath,"applied_loss_review.json"))
  }
  generation<-file.path(runpath,"generation");dir.create(generation,showWarnings=FALSE)
  streams<-lapply(selected,function(sid){
    p<-read_product(sid);s<-p$stream;rows<-p$rows;dates<-vapply(rows,`[[`,character(1),"date")
    if(anyDuplicated(dates)||!identical(dates,sort(dates))||!identical(names(p$source_snapshot$query_by_date),dates))stop("Archive daily/query identity closure")
    recent<-Filter(function(r)r$date>=as.character(as.Date(cutoff)-59),rows);good<-Filter(function(r)isTRUE(r$plot_eligible),rows)
    # save_product already wrote this exact generation file; no second write.
    summary<-s;summary$cadence_context<-p$cadence_context;summary$summary<-daily_summary(rows);summary$acquired_through_date<-tail(dates,1);summary$processed_at_utc<-p$generated_at_utc
    retrieval_times<-vapply(p$source_snapshot$chunks,`[[`,character(1),"retrieval_last_utc");summary$last_retrieved_at_utc<-retrieval_times[which.max(parse_utc(retrieval_times))];summary["latest_observation_utc"]<-list(retained_observation(p$source_snapshot$query_by_date))
    summary["current_interval_retrieved_at_utc"]<-list(p$source_snapshot$query_by_date[[cutoff]]$retrieved_at_utc %||% NULL)
    summary["change"]<-list(if(s$parameter=="soil_moisture")setNames(lapply(c(7,14,30),function(n)recent_change(rows,cutoff,n)),c("7","14","30")) else NULL)
    # Keep projection ownership in the candidate serializer; use R core's established row shape.
    project<-function(r)list(date=r$date,v=r$mean_value,ok=r$plot_eligible,wy=r$water_year,dowy=r$dowy,x=r$water_day_aligned,n=r$n_valid,expected=r$expected_samples,coverage=r$coverage_fraction,span=r$temporal_span_fraction,flags=r$flags)
    summary$recent_rows<-lapply(recent,project);summary["latest_accepted"]<-list(if(length(good))project(tail(good,1)[[1]]) else NULL)
    summary["expires_at_utc"]<-list(if(!frozen&&!is.null(summary$current_interval_retrieved_at_utc))format(min(parse_utc(c(asof,started,summary$current_interval_retrieved_at_utc)))+3*86400,"%Y-%m-%dT%H:%M:%SZ",tz="UTC") else NULL)
    summary
  })
  stations<-lapply(catalog$stations,function(st){st$selected_stream_ids<-as.list(selected[vapply(streams,function(s)s$station_id==st$station_id,logical(1))]);st$selected_stream_count<-length(st$selected_stream_ids);if(isTRUE(st$source_is_geo_protected))st$geometry<-NULL;st})
  parent<-if(!is.null(prior)&&identical(prior$state_role,"published"))prior$generation else NULL
  lineage<-list(kind=if(mode=="bootstrap")"imported_seed" else "descendant",state_parent_generation=prior$generation %||% NULL,seed_sha256=seed_binding %||% old_index$lineage$seed_sha256,seed_generation=if(mode=="bootstrap")runid else old_index$lineage$seed_generation,selection_change=catalog$selection_change %||% NULL)
  expiry<-if(!frozen) {times<-vapply(streams,`[[`,character(1),"expires_at_utc");format(min(parse_utc(times)),"%Y-%m-%dT%H:%M:%SZ",tz="UTC")} else NULL
  index<-list(schema_version="dendra-daily-2.0.0",integration_version=DENDRA_ARCHIVE_INTEGRATION,policy_version=DENDRA_POLICY,safeguard_version=DENDRA_SAFEGUARDS,product_id="dendra-daily",generation=runid,parent_generation=parent,mode=if(frozen)"replay" else "live",as_of_utc=asof,run_started_at_utc=started,generated_at_utc=built,publication_time_utc=NULL,metadata_verified_at_utc=catalog$verified_at_utc %||% NULL,complete_through_date=cutoff,expires_at_utc=expiry,completeness="complete_selected_catalog",scope="saved mixed stream vintages; not statewide collected coverage",lineage=lineage,windows=windows,streams=streams,stations=stations)
  json_write(index,file.path(generation,"index.json"))
  # This run's temporary parent view has been consumed; acknowledged partitions
  # are retained untouched. Avoid keeping two full materialized daily archives.
  unlink(file.path(runpath,"prior"),recursive=TRUE)
  if(system2(Sys.getenv("PYTHON",unset="python3"),shQuote(c(file.path(script_dir,"dendra_candidate.py"),"build","--generation",generation,"--output",output,"--state",state,"--role",if(mode=="bootstrap")"seed" else "prepared")))!=0)stop("Archive candidate validation/state preparation failed; acknowledged state unchanged")
  cat("Archive2 prepared",mode,"generation",runid,"streams",length(streams),"; no publication acknowledgement\n")
}
