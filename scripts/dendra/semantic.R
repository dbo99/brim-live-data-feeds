# Bounded semantic verification of retained daily sufficient statistics, never native reaggregation.
args<-commandArgs(FALSE);here<-dirname(normalizePath(sub("^--file=","",args[grepl("^--file=",args)][1])))
source(file.path(here,"core.R"))
source(file.path(here,"integrated.R"))
TOL<-1e-9
fail<-function(path,message) stop(paste0(path,": ",message),call.=FALSE)
need<-function(ok,path,message="inconsistent field") if(!isTRUE(ok)) fail(path,message)
# Presence is checked separately: NULL is an intentional value, absent is never equal.
equal<-function(actual,expected,path) {
  if(is.null(expected)) {need(is.null(actual),path);return(invisible(NULL))}
  if(is.numeric(expected)) {need(finite_number(actual)&&finite_number(expected)&&abs(actual-expected)<=TOL,path,"missing/nonfinite/nonnumeric or numerical disagreement");return(invisible(NULL))}
  if(is.list(expected)) {
    need(is.list(actual)&&length(actual)==length(expected),path,"list shape mismatch")
    if(!is.null(names(expected))) {
      need(setequal(names(actual),names(expected)),path,"missing/unexpected fields")
      for(k in names(expected)) equal(actual[[k]],expected[[k]],paste0(path,".",k))
    } else for(k in seq_along(expected)) equal(actual[[k]],expected[[k]],paste0(path,"[",k,"]"))
    return(invisible(NULL))
  }
  need(identical(actual,expected),path)
}
field<-function(x,k,expected,path) {need(k %in% names(x),paste0(path,".",k),"missing field");equal(x[[k]],expected,paste0(path,".",k))}
unit_semantics<-function(s,path) {
  c<-s$unit_normalization;parameter<-s$parameter %||% "soil_moisture";unit<-s$native_unit_name
  field(s$source_terms$dt,"Unit",unit,path)
  field(s$source_terms$ds,"Medium","Soil",path)
  field(s$source_terms$ds,"Variable",if(parameter=="soil_temperature") "Temperature" else "VolumetricWaterContent",path)
  if(!is.null(s$source_terms$ds$Aggregate))need(s$source_terms$ds$Aggregate %in% c("Average","Instantaneous"),path,"unsupported report semantics")
  supported<-if(parameter=="soil_moisture") unit %in% c("Percent","VolumetricWaterContent") else unit %in% c("DegreeCelsius","Celsius","DegreeFahrenheit","Fahrenheit","Kelvin")
  verified<-identical(c$status,if(parameter=="soil_temperature") "verified_temperature_conversion" else "verified_percent_conversion")
  if(!verified) {
    need(identical(c$status,"unresolved")&&is.null(c$multiplier)&&is.null(c$offset),path,"unresolved scale cannot assert conversion")
    return(FALSE)
  }
  need(supported,path,"unsupported unit cannot be verified by status or label")
  mult<-if(unit=="VolumetricWaterContent") 100 else if(unit %in% c("DegreeFahrenheit","Fahrenheit")) 5/9 else 1
  offset<-if(unit %in% c("DegreeFahrenheit","Fahrenheit")) -32*5/9 else if(unit=="Kelvin") -273.15 else 0
  field(c,"multiplier",mult,path);equal(c$offset %||% 0,offset,paste0(path,".offset"))
  field(c,"target_unit",if(parameter=="soil_temperature") "degree Celsius" else "% volumetric water content",path)
  if(!is.null(c$unit_definition)) field(c$unit_definition,"label",unit,paste0(path,".unit_definition")) else {
    need(identical(c$unit_definition_evidence,"pilot_catalog.json#/provenance/validated_unit_dictionary"),path,"unit definition evidence missing")
    dictionary<-json_read(file.path(here,"../../data/input/dendra/pilot_catalog.json"))$provenance$validated_unit_dictionary
    need(any(vapply(dictionary$source_unit_terms,function(t)identical(t$label,unit),logical(1))),path,"unsupported dictionary reference")
  }
  TRUE
}
archive_product<-function(root,s) {
  read_checked<-function(d) {
    need(is.character(d$path)&&grepl("^docs/data/dendra/(state|diagnostics)/[a-zA-Z0-9_./-]+$",d$path)&&!grepl("..",d$path,fixed=TRUE),"archive","unsafe partition path")
    path<-file.path(root,d$path)
    need(file.info(path)$size==d$bytes&&d$bytes>0&&d$bytes<=1000000&&sha_file(path)==d$sha256,"archive","partition integrity")
    json_read(path)
  }
  m<-read_checked(s$manifest);p<-m$product_header
  parts<-lapply(m$partitions,function(x)read_checked(x$diagnostics))
  p$rows<-unlist(lapply(parts,`[[`,"rows"),recursive=FALSE)
  p$source_snapshot$query_by_date<-do.call(c,lapply(parts,`[[`,"query_by_date"))
  chunks<-unlist(lapply(parts,`[[`,"chunks"),recursive=FALSE)
  keys<-vapply(chunks,function(x)jsonlite::toJSON(x,auto_unbox=TRUE,null="null",digits=NA),character(1))
  p$source_snapshot$chunks<-chunks[!duplicated(keys)]
  p
}
verify<-function(root,now,first=1L,last=NULL,archive_root=NULL) {
 index<-json_read(file.path(root,"docs/data/dendra/index.json"));path<-"index"
 archive<-identical(index$integration_version,"dendra-integration-2")
 modern<-archive||identical(index$integration_version,DENDRA_INTEGRATION)
 field(index,"policy_version",DENDRA_POLICY,path);field(index,"schema_version",if(archive)"dendra-daily-2.0.0" else if(modern)DENDRA_INDEX_SCHEMA else DENDRA_SCHEMA,path)
 if("safeguard_version" %in% names(index)) field(index,"safeguard_version",DENDRA_SAFEGUARDS,path)
 asof<-parse_utc(index$as_of_utc);generated<-parse_utc(index$generated_at_utc)
 need(generated<=parse_utc(now),path,"build timestamp is in the future")
 if(identical(index$safeguard_version,DENDRA_SAFEGUARDS)) {
   need(is.character(index$run_started_at_utc)&&length(index$run_started_at_utc)==1,path,"modern candidate requires captured run_started_at_utc")
   if(index$mode=="live")need(is.character(index$metadata_verified_at_utc)&&length(index$metadata_verified_at_utc)==1,path,"modern live candidate requires metadata_verified_at_utc")
 }
 start<-index$run_started_at_utc %||% index$generated_at_utc # Legacy D1 snapshots lack captured start.
 need(parse_utc(start)<=generated,path,"run start after build")
 if(index$mode=="live") {
   validate_live_time(index$as_of_utc,start)
   if(!is.null(index$metadata_verified_at_utc)) {
     age<-as.numeric(difftime(parse_utc(start),parse_utc(index$metadata_verified_at_utc),units="secs"))
     need(age>=0&&age<=86400,path,"metadata verification time outside run-start bounds")
   }
 }
 retrieved<-if(index$mode=="live")vapply(index$streams,`[[`,character(1),if(modern)"current_interval_retrieved_at_utc" else "last_retrieved_at_utc") else character()
 expiry<-if(index$mode=="live")format(min(parse_utc(c(index$as_of_utc,index$generated_at_utc,retrieved)))+3*86400,"%Y-%m-%dT%H:%M:%OS6Z",tz="UTC") else NULL
 if(is.null(expiry))field(index,"expires_at_utc",NULL,path) else {
   need("expires_at_utc" %in% names(index)&&!is.null(index$expires_at_utc),path,"missing expiry")
   need(abs(as.numeric(parse_utc(index$expires_at_utc)-parse_utc(expiry),units="secs"))<=1e-6,path,"expiry must equal earliest as-of/build/retrieval plus three days")
 }
 if(is.null(last))last<-length(index$streams)
 need(length(first)==1&&length(last)==1&&is.finite(first)&&is.finite(last)&&first==floor(first)&&last==floor(last)&&first>=1&&last>=first&&last<=length(index$streams),"index","invalid semantic batch")
 checked<-0L
 for(s in index$streams[seq.int(first,last)]) {
   sid<-s$datastream_id;path<-paste0("stream ",sid);p<-if(is.null(archive_root))json_read(file.path(root,s$diagnostics_path)) else archive_product(archive_root,s)
   # Bind scientific identity across index/diagnostics. Optional D1 fields normalize only explicit defaults.
   for(k in c("datastream_id","station_id","depth_cm","orientation","native_unit_name","unit_normalization","source_terms","source_attributes","public_level","source_is_hidden","source_is_geo_protected")) {
     need(k %in% names(s),paste0(path,".",k),"missing index identity field")
     equal(s[[k]],p$stream[[k]],paste0(path,".",k))
   }
   equal(s$parameter,p$stream$parameter %||% "soil_moisture",paste0(path,".parameter"))
   verified<-unit_semantics(s,path)
   attr<-s$source_attributes
   if(!is.null(attr$depth)) {
     units<-attr$depth$unit_tag;raw<-attr$depth$value
     depth<-if(identical(units,"dt_Unit_Millimeter")&&finite_number(raw))raw/10 else if(identical(units,"dt_Unit_Centimeter")&&finite_number(raw))raw else NULL
     equal(s$depth_cm,depth,paste0(path,".depth_cm source evidence"))
   }
   if(!is.null(attr$orientation))equal(s$orientation,attr$orientation,paste0(path,".orientation source evidence"))
   field(p,"schema_version",DENDRA_SCHEMA,path);field(p,"generated_at_utc",if(archive)s$processed_at_utc else index$generated_at_utc,path)
   if(archive)need(parse_utc(p$generated_at_utc)<=generated,path,"stream processed after build")
   stream_cutoff<-if(archive)s$acquired_through_date else index$complete_through_date
   a<-p$aggregation
   for(k in c("method","day_timezone","day_bounds_utc","coverage_threshold","policy_version","cadence_context_version","complete_through_date","start_date","end_date_exclusive","percent_conversion_verified")) {
     expected<-switch(k,method="arithmetic_mean_of_reported_values",day_timezone="UTC-08:00",day_bounds_utc="08:00Z inclusive through next 08:00Z exclusive",coverage_threshold=.75,policy_version=DENDRA_POLICY,cadence_context_version="frozen-cadence-context-1",complete_through_date=stream_cutoff,start_date=p$rows[[1]]$date,end_date_exclusive=as.character(as.Date(stream_cutoff)+1),percent_conversion_verified=verified&&s$parameter=="soil_moisture")
     field(a,k,expected,paste0(path,".aggregation"))
   }
   field(p$cadence_context,"version","frozen-cadence-context-1",path);equal(s$cadence_context,p$cadence_context,paste0(path,".cadence_context"))
   need(is.character(p$cadence_context$evidence_sha256)&&grepl("^[a-f0-9]{64}$",p$cadence_context$evidence_sha256),path,"invalid cadence evidence hash")
   ctx<-p$cadence_context
   cadence_number<-function(v)finite_number(v)&&v>0&&v<=86400
   for(k in c("seconds","configured_seconds"))need(k %in% names(ctx)&&(is.null(ctx[[k]])||cadence_number(ctx[[k]])),paste0(path,".cadence_context.",k),"invalid cadence seconds")
   m<-ctx$observed_stream_mode
   if(!is.null(m)) {
     need(cadence_number(m$seconds)&&finite_number(m$occurrences)&&m$occurrences>=3&&m$occurrences==floor(m$occurrences)&&finite_number(m$interval_share)&&m$interval_share>=.5&&m$interval_share<=1,path,"invalid observed cadence mode")
     field(ctx,"source","observed_stream_mode",path);field(ctx,"seconds",m$seconds,path)
   } else if(!is.null(ctx$configured_seconds)) {
     field(ctx,"source","configured_stream_interval",path);field(ctx,"seconds",ctx$configured_seconds,path)
   } else {field(ctx,"source",NULL,path);field(ctx,"seconds",NULL,path)}
   prevcad<-if(modern)a$prior_observed_cadence_seconds else NULL
   # Vectorize the same core calendar conversion, not the scientific decisions.
   years<-water_year(vapply(p$rows,`[[`,character(1),"date"))
   year_days<-as.integer(as.Date(sprintf("%d-10-01",years))-as.Date(sprintf("%d-10-01",years-1)))
   for(row_index in seq_along(p$rows)) {
     r<-p$rows[[row_index]];rp<-paste(path,r$date)
     field(r,"water_year_days",year_days[[row_index]],rp)
     for(k in c("n_valid","n_total","n_null","n_invalid","n_missing","n_duplicate_conflicts","n_duplicate_rows","n_out_of_range"))
       need(k %in% names(r)&&finite_number(r[[k]])&&r[[k]]>=0&&r[[k]]==floor(r[[k]]),paste0(rp,".",k),"nonnegative integer required")
     need(r$n_valid+r$n_null+r$n_invalid+r$n_missing+r$n_duplicate_conflicts==r$n_total,rp,"sample count partition mismatch")
     need(r$n_out_of_range<=r$n_total,rp,"out-of-range count exceeds observations")
     if(s$parameter=="soil_temperature")field(r,"n_out_of_range",0,rp)
     need(is.list(r$flags)&&!anyDuplicated(unlist(r$flags)),rp,"invalid/duplicate flags")
     flags<-unlist(r$flags);allowed<-c("no_observations","no_valid_numeric_values","null_values_present","invalid_values_present","missing_values_present","duplicate_rows_removed","duplicate_timestamp_conflicts","out_of_range_values","percent_scale_unresolved","temperature_scale_unresolved","cadence_unresolved","irregular_cadence","possible_within_day_cadence_change","insufficient_sample_coverage","insufficient_temporal_span","nonfinite_converted_mean","cadence_changed_from_previous_observed_day")
     need(all(flags %in% allowed),rp,"unknown diagnostic flag")
     flag<-function(name,want) need(identical(name %in% flags,want),paste0(rp,".flags.",name),"flag contradicts sufficient statistics")
     flag("no_observations",r$n_total==0);flag("no_valid_numeric_values",r$n_total>0&&r$n_valid==0)
     for(k in c("null","invalid","missing"))flag(paste0(k,"_values_present"),r[[paste0("n_",k)]]>0)
     flag("duplicate_rows_removed",r$n_duplicate_rows>0);flag("duplicate_timestamp_conflicts",r$n_duplicate_conflicts>0);flag("out_of_range_values",r$n_out_of_range>0)
     flag("percent_scale_unresolved",!verified&&s$parameter=="soil_moisture");flag("temperature_scale_unresolved",!verified&&s$parameter=="soil_temperature")
     need("cadence_seconds" %in% names(r),rp,"missing cadence")
     if(!is.null(r$cadence_seconds))need(finite_number(r$cadence_seconds)&&r$cadence_seconds>0&&r$cadence_seconds<=86400,rp,"invalid cadence")
     need("cadence_source" %in% names(r),rp,"missing cadence_source")
     if(identical(r$cadence_source,"observed_day_mode"))need(r$n_total>=4,rp,"observed day cadence requires at least four timestamps") else {
       equal(r$cadence_source,ctx$source,paste0(rp,".cadence_source"))
       equal(r$cadence_seconds,ctx$seconds,paste0(rp,".cadence_seconds fallback"))
     }
     expected<-if(is.null(r$cadence_seconds))NULL else 86400/r$cadence_seconds
     field(r,"expected_samples",expected,rp);field(r,"coverage_fraction",if(is.null(expected))NULL else min(1,r$n_valid/expected),rp)
     need(finite_number(r$temporal_span_fraction)&&r$temporal_span_fraction>=0&&r$temporal_span_fraction<1,rp,"invalid temporal span")
     if(r$n_valid<=1)field(r,"temporal_span_fraction",0,rp)
     flag("cadence_unresolved",is.null(r$cadence_seconds));flag("insufficient_sample_coverage",!is.null(r$coverage_fraction)&&r$coverage_fraction+1e-12<.75)
     flag("insufficient_temporal_span",r$temporal_span_fraction+1e-12<.75)
     if(r$n_total==0)need(!any(c("irregular_cadence","possible_within_day_cadence_change") %in% flags),rp,"cadence flags on empty day")
     flag("cadence_changed_from_previous_observed_day",r$n_total>0&&!is.null(r$cadence_seconds)&&!is.null(prevcad)&&abs(r$cadence_seconds-prevcad)>1)
     if(r$n_total>0&&!is.null(r$cadence_seconds))prevcad<-r$cadence_seconds
     if(r$n_valid==0)field(r,"mean_native",NULL,rp) else need(finite_number(r$mean_native),rp,"valid samples require finite native mean")
     converted<-if(verified&&!is.null(r$mean_native))r$mean_native*s$unit_normalization$multiplier+(s$unit_normalization$offset %||% 0) else NULL
     if(!is.null(converted)&&!is.finite(converted))converted<-NULL
     value<-if(verified)converted else r$mean_native
     field(r,"mean_value",value,rp);field(r,"mean_percent",if(s$parameter=="soil_moisture")converted else NULL,rp)
     flag("nonfinite_converted_mean",!is.null(r$mean_native)&&is.null(value))
     field(r,"plot_eligible",daily_eligible(r,verified),rp)
     if(verified&&s$parameter=="soil_moisture"&&finite_number(r$mean_percent)&&(r$mean_percent<0||r$mean_percent>100))need(r$n_out_of_range>0,rp,"out-of-range mean without out-of-range samples")
     if(r$plot_eligible&&s$parameter=="soil_moisture")need(r$mean_percent>=0&&r$mean_percent<=100,rp,"accepted moisture outside supported range")
     checked<-checked+1L
   }
   equal(p$summary,daily_summary(p$rows),paste0(path,".diagnostic_summary"));equal(s$summary,p$summary,paste0(path,".index_summary"))
   changes<-if(s$parameter=="soil_moisture")setNames(lapply(c(7,14,30),function(n)recent_change(p$rows,index$complete_through_date,n)),c("7","14","30")) else NULL
   field(s,"change",changes,path)
   chunks<-p$source_snapshot$chunks;need(length(chunks)>0,path,"no source evidence")
   for(c in chunks) {
     t<-parse_utc(c$retrieval_last_utc);need(t<=generated,path,"source retrieval after build")
     b<-c$requested_interval;lo<-parse_utc(b$start_inclusive);hi<-parse_utc(b$end_exclusive)
     need(lo<hi&&hi<=parse_utc(paste0(a$end_date_exclusive,"T08:00:00Z")),path,"source interval includes uncompleted date")
     if(!is.null(c$latest_observation_utc)) {
       obs<-parse_utc(c$latest_observation_utc);need(obs>=lo&&obs<hi&&obs<=t,path,"observation outside interval or after retrieval")
     }
   }
   if(modern) {
     query<-p$source_snapshot$query_by_date
     need(identical(names(query),vapply(p$rows,`[[`,character(1),"date")),path,"daily query evidence closure")
     # Parse each distinct timestamp once per stream through core.R's strict
     # parser. This is invocation-local work reuse, never a persisted trust cache.
     timestamps<-unique(unlist(lapply(query,function(q)c(q$latest_observation_utc,q$retrieved_at_utc)),use.names=FALSE))
     parsed<-setNames(as.numeric(parse_utc(timestamps)),timestamps)
     observed_days<-setNames(as.character(as.Date(as.POSIXct(unname(parsed)-28800,origin="1970-01-01",tz="UTC"),tz="UTC")),timestamps)
     for(row_index in seq_along(query)) {
       day<-names(query)[[row_index]];q<-query[[row_index]];r<-p$rows[[row_index]]
       if(identical(q$status,"before_saved_source_start")) {
         need(is.null(q$retrieved_at_utc)&&is.null(q$content_sha256)&&is.null(q$latest_observation_utc)&&r$n_total==0&&day<substr(p$stream$observed_start_utc,1,10),path,"invalid pre-source date")
       } else {
         field(q,"status","queried",path)
         matching<-Filter(function(c)identical(c$content_sha256,q$content_sha256)&&identical(c$retrieval_last_utc,q$retrieved_at_utc)&&substr(c$requested_interval$start_inclusive,1,10)<=day&&substr(c$requested_interval$end_exclusive,1,10)>day,chunks)
         need(length(matching)>0,path,"query evidence not bound to retained source interval")
         if(!is.null(q$latest_observation_utc)) {
           t<-parsed[[q$latest_observation_utc]]
           need(observed_days[[q$latest_observation_utc]]==day&&t<=parsed[[q$retrieved_at_utc]]&&r$n_total>0,path,"query observation mismatch")
         } else need(r$n_total==0,path,"missing retained latest observation")
       }
     }
     equal(s$latest_observation_utc,retained_observation(query),paste0(path,".latest_observation_utc"))
     equal(s$current_interval_retrieved_at_utc,query[[index$complete_through_date]]$retrieved_at_utc,paste0(path,".current_interval_retrieved_at_utc"))
     stream_expiry<-if(index$mode=="live")min(parse_utc(c(index$as_of_utc,index$generated_at_utc,s$current_interval_retrieved_at_utc)))+3*86400 else NULL
     if(is.null(stream_expiry))field(s,"expires_at_utc",NULL,path) else need(parse_utc(s$expires_at_utc)==stream_expiry,path,"parameter expiry mismatch")
   } else equal(s$latest_observation_utc,tail(chunks,1)[[1]]$latest_observation_utc,paste0(path,".latest_observation_utc"))
   need(parse_utc(s$last_retrieved_at_utc)==max(parse_utc(vapply(chunks,`[[`,character(1),"retrieval_last_utc"))),path,"latest retrieval does not match evidence")
 }
 cat(jsonlite::toJSON(list(status="passed",rows_checked=checked,streams_checked=last-first+1L,tolerance=TOL,numerical_policy=DENDRA_POLICY,safeguard_version=DENDRA_SAFEGUARDS),auto_unbox=TRUE),"\n")
}
tryCatch({a<-commandArgs(TRUE);stopifnot(length(a) %in% c(2,4,5));if(length(a)==2)verify(a[1],a[2]) else verify(a[1],a[2],as.integer(a[3]),as.integer(a[4]),if(length(a)==5)a[5] else NULL)},error=function(e){message(conditionMessage(e));quit(status=2)})
