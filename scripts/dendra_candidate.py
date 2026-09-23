#!/usr/bin/env python3
"""Dendra-only candidate build, validation, preparation, and pure reconciliation.

Never invokes the shared publisher's publish command or a Git transaction.
"""
from __future__ import annotations
import argparse,csv,hashlib,json,math,os,re,shutil,sys,tempfile,subprocess
from pathlib import Path
from datetime import date,datetime,timedelta,timezone
from types import SimpleNamespace
import main_publisher as shared

PRODUCT='dendra-daily';SCHEMA='dendra-daily-1.0.0'
INDEX_SCHEMA='dendra-daily-1.1.0'
INTEGRATION='dendra-integration-1'
STATE_INDEX='docs/data/dendra/state/source-index.json'
VALIDATOR_VERSION='dendra-safeguards-1.1.0'
CSV_FIELDS=['datastream_id','date','accepted_mean','diagnostic_mean','native_mean','water_year','dowy','water_day_aligned','plot_eligible','n_total','n_valid','expected_samples','coverage_fraction','temporal_span_fraction','flags']
def csv_record(sid,r):
    return dict(datastream_id=sid,date=r['date'],accepted_mean=r['mean_value'] if r['plot_eligible'] else None,diagnostic_mean=r['mean_value'],native_mean=r['mean_native'],**{k:r[k] for k in CSV_FIELDS if k in r and k not in ('date','flags')},flags=';'.join(r['flags']))
def csv_text(value):return '' if value is None else str(value)
# Bounds apply to each invocation, including publisher callbacks. There is no
# validation cache: every stream is checked on every call.
SEMANTIC_WORKERS=4
SEMANTIC_BATCH_STREAMS=16
SEMANTIC_BATCH_SECONDS=120
SEMANTIC_TOTAL_SECONDS=900

def semantic_validate(root,now=None,archive_root=None):
    from concurrent.futures import ThreadPoolExecutor,as_completed
    import time
    now=now or datetime.now(timezone.utc);root=Path(root).resolve()
    index=load(root/FIXED[0]);count=len(index['streams'])
    require(0<count<=1024,'Semantic stream bound')
    started=time.monotonic();deadline=started+SEMANTIC_TOTAL_SECONDS
    def batch(lo):
        hi=min(lo+SEMANTIC_BATCH_STREAMS,count)
        command=[os.environ.get('RSCRIPT','Rscript'),'--vanilla',str(Path(__file__).parent/'dendra/semantic.R'),str(root),now.isoformat().replace('+00:00','Z'),str(lo+1),str(hi)]
        if archive_root is not None:command.append(str(Path(archive_root).resolve()))
        remaining=min(SEMANTIC_BATCH_SECONDS,deadline-time.monotonic())
        require(remaining>0,'R semantic total deadline exhausted')
        try:result=subprocess.run(command,capture_output=True,text=True,timeout=remaining)
        except subprocess.TimeoutExpired as exc:raise ValueError('R semantic validation timed out; candidate held') from exc
        require(result.returncode==0,'R semantic validation: '+(result.stderr or result.stdout).strip())
        value=json.loads(result.stdout)
        require(value['status']=='passed' and value['streams_checked']==hi-lo,'R semantic batch coverage')
        return value
    results=[]
    with ThreadPoolExecutor(max_workers=SEMANTIC_WORKERS) as pool:
        pending=[pool.submit(batch,lo) for lo in range(0,count,SEMANTIC_BATCH_STREAMS)]
        try:
            for future in as_completed(pending):results.append(future.result())
        except BaseException:
            for future in pending:future.cancel()
            raise
    require(time.monotonic()<=deadline,'R semantic total deadline exhausted')
    first=results[0]
    require(all(all(r[k]==first[k] for k in ('tolerance','numerical_policy','safeguard_version')) for r in results),'Mixed scientific validators')
    report={**{k:first[k] for k in ('status','tolerance','numerical_policy','safeguard_version')},'rows_checked':sum(r['rows_checked'] for r in results),'streams_checked':sum(r['streams_checked'] for r in results),'batches':len(results),'workers':SEMANTIC_WORKERS,'batch_seconds':SEMANTIC_BATCH_SECONDS,'total_seconds':SEMANTIC_TOTAL_SECONDS,'elapsed_seconds':time.monotonic()-started}
    # Opt-in local audit only; never used as authorization or as a validation cache.
    if os.environ.get('DENDRA_VALIDATION_TRACE'):
        audit={**report,'index_sha256':sha(root/FIXED[0]),'semantic_sha256':sha(Path(__file__).parent/'dendra/semantic.R'),'core_sha256':sha(Path(__file__).parent/'dendra/core.R'),'input_layout':'direct_archive_partitions' if archive_root is not None else 'materialized_daily'}
        with open(os.environ['DENDRA_VALIDATION_TRACE'],'a') as handle:handle.write(json.dumps(audit,sort_keys=True)+'\n')
    return report

FIXED=['docs/data/dendra/index.json']
OWNED=['docs/data/dendra/history','docs/data/dendra/diagnostics','docs/data/dendra/companion','docs/data/dendra/state']
ID=re.compile(r'^[0-9a-f]{24}$')
GENERATION=re.compile(r'^[0-9]{8}T[0-9]{6}-[0-9]+$')
def generation_id(value):
    require(isinstance(value,str) and GENERATION.fullmatch(value),'Unsafe generation identifier')
    return value
def load(p):return json.loads(Path(p).read_text())
def write(p,x):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def finite(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
def require(x,msg):
    if not x:raise ValueError(msg)
def projected(r):return dict(date=r['date'],v=r['mean_percent'],ok=r['plot_eligible'],wy=r['water_year'],dowy=r['dowy'],x=r['water_day_aligned'],n=r['n_valid'],expected=r['expected_samples'],coverage=r['coverage_fraction'],span=r['temporal_span_fraction'],flags=r['flags'])
def freshness(index,now=None):
    now=now or datetime.now(timezone.utc)
    if index['mode']=='replay':return 'frozen-replay'
    parse=lambda s:datetime.fromisoformat(s.replace('Z','+00:00'))
    if not index.get('expires_at_utc') or now>parse(index['expires_at_utc']):return 'stalled-feed'
    return 'current'

def build(generation,output,now=None,state=None,role=None):
    generation=Path(generation);index=load(generation/'index.json');output=Path(output)
    if index.get('integration_version')=='dendra-integration-2':
        import dendra_archive,dendra_state
        from dendra_validation import ValidationOperation
        require((state is None and role is None) or (state is not None and role in ('seed','prepared')),'Archive build state/role')
        op=ValidationOperation();candidate=dendra_archive.build(generation,output,now=now,_operation=op)
        if state is not None:
            pointer=dendra_state.hydrate(state,candidate,role,_operation=op,_adopt=True)
            candidate=dendra_state.candidate_at(state,pointer)
        return candidate
    require(state is None and role is None,'Combined state preparation requires archive2')
    if index.get('integration_version')==INTEGRATION:generation_id(index['generation'])
    dest=output/index['generation'];require(not dest.exists(),'Candidate generation already exists')
    candidate=dest/'candidate';candidate.mkdir(parents=True);files=[]
    modern=index.get('integration_version')==INTEGRATION
    if modern:
        target=candidate/STATE_INDEX;target.parent.mkdir(parents=True);shutil.copyfile(generation/'index.json',target)
        index['state_index_path']=STATE_INDEX
    for s in index['streams']:
        sid=s['datastream_id'];require(ID.fullmatch(sid),'Unsafe stream ID')
        p=load(generation/'daily'/f'{sid}.json');s['histories']=[]
        parameter=s['parameter'];kind='companion' if parameter=='soil_temperature' else 'history'
        for wy in sorted({r['water_year'] for r in p['rows']}):
            rows=[r for r in p['rows'] if r['water_year']==wy]
            path=f'docs/data/dendra/{kind}/{sid}/{wy}.json'
            payload={'schema_version':SCHEMA,'policy_version':index['policy_version'],'safeguard_version':VALIDATOR_VERSION,'depth_cm':s['depth_cm'],'orientation':s['orientation'],'native_unit_name':s['native_unit_name'],'unit_normalization':s['unit_normalization'],'datastream_id':sid,'station_id':s['station_id'],'parameter':parameter,'water_year':wy,'unit':'degree Celsius' if parameter=='soil_temperature' else '% volumetric water content','rows':[dict(projected(r),v=r['mean_value']) if parameter=='soil_temperature' else projected(r) for r in rows]}
            if modern:
                encoded=(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
                path=path[:-5]+'-'+hashlib.sha256(encoded).hexdigest()+'.json'
            write(candidate/path,payload);s['histories'].append({'path':path,'water_year':wy,'sha256':sha(candidate/path),'rows':len(rows)})
        diagnostic=f'docs/data/dendra/diagnostics/{sid}.json'
        write(candidate/diagnostic,p);s['diagnostics_path']=diagnostic
        csvpath=f'docs/data/dendra/diagnostics/{sid}.csv'
        with (candidate/csvpath).open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=CSV_FIELDS);writer.writeheader()
            for r in p['rows']:writer.writerow(csv_record(sid,r))
        s['csv_path']=csvpath
        if modern:
            s['recent_rows']=[projected(r) for r in p['rows'][-60:]] if parameter=='soil_moisture' else []
            fresh_time=s['current_interval_retrieved_at_utc']
            s['expires_at_utc']=(min(datetime.fromisoformat(t.replace('Z','+00:00')) for t in (index['as_of_utc'],index['generated_at_utc'],fresh_time))+timedelta(days=3)).isoformat().replace('+00:00','Z') if index['mode']=='live' and fresh_time else None
    times=[index['as_of_utc'],index['generated_at_utc']]+[s['current_interval_retrieved_at_utc'] if modern else s['last_retrieved_at_utc'] for s in index['streams']]
    index['expires_at_utc']=(min(datetime.fromisoformat(t.replace('Z','+00:00')) for t in times)+timedelta(days=3)).isoformat().replace('+00:00','Z') if index['mode']=='live' else None
    index['freshness_contract']='Evaluate wall clock against expires_at_utc at every read; also retain per-stream observation lag and recent-change state. Replay is always frozen.'
    for p in sorted(candidate.rglob('*')):
        if p.is_file():files.append({'path':str(p.relative_to(candidate)),'bytes':p.stat().st_size,'sha256':sha(p)})
    index['files']=files;write(candidate/FIXED[0],index);report=validate(candidate,now=now)
    write(output/'validation'/f"{index['generation']}.json",report)
    # A validated artifact is staged only. R alone activates state/current.json,
    # which also names this candidate. There is no competing candidate pointer.
    print(json.dumps(report))
    return candidate

def validate(root,now=None):
    root=Path(root)
    with (root/FIXED[0]).open('rb') as handle:index_bytes=handle.read(256001)
    require(0<len(index_bytes)<=256000,'Index byte bound')
    if json.loads(index_bytes).get('schema_version')=='dendra-daily-2.0.0':
        import dendra_archive
        return dendra_archive.validate(root,now=now)
    inventory=shared._candidate_inventory(root)
    shared._validate_desired_inventory(inventory,fixed_paths=FIXED,owned_roots=OWNED)
    index=load(root/FIXED[0]);require(index.get('schema_version') in (SCHEMA,INDEX_SCHEMA) and index.get('product_id')==PRODUCT,'Schema/product mismatch')
    modern=index.get('schema_version')==INDEX_SCHEMA
    require((index.get('integration_version')==INTEGRATION)==modern,'Integration/schema version mismatch')
    if modern:
        generation_id(index.get('generation'))
        if index.get('parent_generation') is not None:generation_id(index['parent_generation'])
        require(index.get('safeguard_version')==VALIDATOR_VERSION,'Modern safeguards required')
        require(index.get('retention')=={'soil_moisture':{'water_years':10},'soil_temperature':{'completed_days':90}},'Retention contract mismatch')
        require(index.get('state_index_path')==STATE_INDEX,'State index path mismatch')
        source=load(root/STATE_INDEX)
        for key,value in source.items():
            if key not in ('streams','publication_time_utc'):require(index[key]==value,'State index identity mismatch: '+key)
        require([s['datastream_id'] for s in source['streams']]==[s['datastream_id'] for s in index['streams']],'State selection mismatch')
        for before,after in zip(source['streams'],index['streams']):
            for key,value in before.items():require(after[key]==value,'State stream mismatch: '+key)
    require(index.get('completeness')=='complete_selected_catalog','Incomplete selection')
    require(index.get('mode') in ('live','replay'),'Explicit replay/live mode required')
    cutoff=date.fromisoformat(index['complete_through_date']);asof=datetime.fromisoformat(index['as_of_utc'].replace('Z','+00:00'))
    require(cutoff==(asof-timedelta(hours=8)).date()-timedelta(days=1),'Cutoff mismatch')
    declared=index['files'];require(len({f['path'] for f in declared})==len(declared),'Duplicate file entry')
    require(sorted([f['path'] for f in declared]+FIXED)==inventory,'Index inventory closure mismatch')
    for f in declared:
        p=root/f['path'];require(p.stat().st_size==f['bytes'] and sha(p)==f['sha256'],'File integrity mismatch')
    require(index['streams'],'Empty stream set');ids=set();owned=set();count=0;eligible=0
    stations={s['station_id']:s for s in index['stations']}
    if modern:
        owned.add(STATE_INDEX)
        require(len(stations)==len(index['stations']),'Duplicate station')
        for station in stations.values():
            selected=[s['datastream_id'] for s in index['streams'] if s['station_id']==station['station_id']]
            require(station.get('selected_stream_ids')==selected and station.get('selected_stream_count')==len(selected),'Station selection metadata mismatch')
    for s in index['streams']:
        sid=s['datastream_id'];require(ID.fullmatch(sid) and sid not in ids,'Duplicate/unsafe ID');ids.add(sid)
        require(s['station_id'] in stations,'Missing station');station=stations[s['station_id']]
        require(s['public_level']==3 and s['source_is_hidden'] is False and station['public_level']==3 and station['source_is_hidden'] is False,'Public access restriction')
        require(not station.get('source_is_geo_protected') or not station.get('geometry'),'Protected coordinate leaked')
        require(s['depth_cm'] is None or (finite(s['depth_cm']) and s['depth_cm']>=0),'Invalid depth')
        parameter=s['parameter'];require(parameter in ('soil_moisture','soil_temperature'),'Unsupported parameter')
        p=load(root/s['diagnostics_path']);require(p['stream']['datastream_id']==sid and p['stream']['station_id']==s['station_id'],'Diagnostic identity mismatch')
        require(p['aggregation']['complete_through_date']==index['complete_through_date'],'Mixed generation cutoff')
        rows=p['rows'];dates=[r['date'] for r in rows];require(dates==sorted(set(dates)) and dates,'Invalid daily keys')
        require(dates[-1]==index['complete_through_date'],'Daily series stops before cutoff')
        require(len(dates)==(date.fromisoformat(dates[-1])-date.fromisoformat(dates[0])).days+1,'Missing explicit calendar gaps')
        for r in rows:
            d=date.fromisoformat(r['date']);wy=d.year+(d.month>=10);start=date(wy-1,10,1);aligned=date(1999 if d.month>=10 else 2000,d.month,d.day)
            require((r['water_year'],r['dowy'],r['water_day'],r['water_day_aligned'])==(wy,(d-start).days+1,(d-start).days+1,(aligned-date(1999,10,1)).days+1),'Calendar mismatch')
            require(type(r['plot_eligible']) is bool,'Eligibility must be boolean')
            require(sum(r[k] for k in ('n_valid','n_null','n_missing','n_invalid','n_duplicate_conflicts'))==r['n_total'],'Diagnostic sample count mismatch')
            if r['plot_eligible']:
                require(finite(r['mean_value']) and r['coverage_fraction']>=.75-1e-12 and r['temporal_span_fraction']>=.75-1e-12,'Invalid eligible mean/coverage')
                require(s['unit_normalization']['status']==('verified_percent_conversion' if parameter=='soil_moisture' else 'verified_temperature_conversion'),'Unresolved accepted scale')
                require(parameter!='soil_moisture' or finite(r['mean_percent']) and 0<=r['mean_percent']<=100,'Invalid accepted percent')
        expected_rows=[]
        require(len({h['water_year'] for h in s['histories']})==len(s['histories']),'Duplicate history partition')
        for h in s['histories']:
            prefix='companion' if parameter=='soil_temperature' else 'history'
            expected_path=f"docs/data/dendra/{prefix}/{sid}/{h['water_year']}"+('-'+h['sha256'] if modern else '')+'.json'
            require(h['path']==expected_path,'Unbounded history path')
            hp=load(root/h['path']);require(hp['datastream_id']==sid and hp['parameter']==parameter and hp['station_id']==s['station_id'],f'{sid}: History identity mismatch')
            require(hp.get('schema_version')==SCHEMA and hp.get('water_year')==h['water_year'],f'{sid}: History schema/WY mismatch')
            require(hp.get('unit')==('degree Celsius' if parameter=='soil_temperature' else '% volumetric water content'),f'{sid}: History target unit mismatch')
            # D1 snapshots predate additive partition identity fields; validate every field present.
            for key in ('policy_version','depth_cm','orientation','native_unit_name','unit_normalization'):
                if key in hp or index.get('safeguard_version')==VALIDATOR_VERSION:
                    require(key in hp and hp[key]==(index[key] if key=='policy_version' else s[key]),f'{sid}: History {key} mismatch')
            if 'safeguard_version' in hp:require(hp['safeguard_version']==VALIDATOR_VERSION,f'{sid}: History safeguard version mismatch')
            require(sha(root/h['path'])==h['sha256'] and len(hp['rows'])==h['rows'],'History hash/count mismatch')
            wanted=[dict(projected(r),v=r['mean_value']) if parameter=='soil_temperature' else projected(r) for r in rows if r['water_year']==h['water_year']]
            require(hp['rows']==wanted,'Projection numerical mismatch');expected_rows.extend(hp['rows']);owned.add(h['path'])
        require(len(expected_rows)==len(rows),'History partition coverage mismatch')
        for key,suffix in [('diagnostics_path','json'),('csv_path','csv')]:require(s[key]==f'docs/data/dendra/diagnostics/{sid}.{suffix}','Unbounded diagnostic path');owned.add(s[key])
        with (root/s['csv_path']).open(newline='') as f:
            reader=csv.DictReader(f);require(reader.fieldnames==CSV_FIELDS,f'{sid}: CSV columns mismatch');csvrows=list(reader)
        require(len(csvrows)==len(rows),f'{sid}: CSV row count mismatch')
        for c,r in zip(csvrows,rows):
            for k,value in csv_record(sid,r).items():
                require(c[k]==csv_text(value),f'{sid} {r["date"]}: CSV {k} mismatch')
        if modern:
            start=(cutoff-timedelta(days=89)) if parameter=='soil_temperature' else date(cutoff.year+(cutoff.month>=10)-10,10,1)
            require(date.fromisoformat(dates[0])>=start,'Retention bound exceeded')
            if parameter=='soil_temperature':require(len(rows)==90,'Temperature must cover exactly 90 completed calendar days')
            require(s['recent_rows']==([projected(r) for r in rows[-60:]] if parameter=='soil_moisture' else []),'Recent row projection mismatch')
        count+=len(rows);eligible+=sum(r['plot_eligible'] for r in rows)
    require(owned==set(inventory)-set(FIXED),'Unreferenced product files')
    semantic=semantic_validate(root,now=now)
    return {'status':'passed','semantic':semantic,'streams':len(ids),'calendar_rows':count,'eligible_rows':eligible,'files':len(inventory),'freshness':freshness(index),'origin':'local candidate; no publication'}

def prepare(root,source_sha,_operation=None):
    from dendra_validation import operation
    op=operation(_operation);proof=op.validate(root);root=Path(root);index=load(root/FIXED[0]);metadata=root.parent/'candidate-metadata.json'
    shared.prepare_metadata(SimpleNamespace(candidate_root=str(root),output=str(metadata),product_id=PRODUCT,semantic_key_type='dendra_state_generation',semantic_key=index['generation'],source_event_sha=source_sha,allowlist=FIXED,owned_root=OWNED))
    shared.validate_candidate_metadata(root=root,metadata_path=metadata,product_id=PRODUCT,allowlist=FIXED,expected_source_sha=source_sha,owned_roots=OWNED)
    require(op.validate(root)['content_sha256']==proof['content_sha256'],'Candidate changed during preparation')
    return metadata

def reconcile_decision(candidate,current):
    """Pure no-network decision, reevaluated after each fresh-main race."""
    if current is None:return {'decision':'publish','candidate_state':'new','reason':'explicit first selected catalog'}
    if candidate['generation']==current['generation']:
        require({k:v for k,v in candidate.items() if k!='publication_time_utc'}=={k:v for k,v in current.items() if k!='publication_time_utc'},'Same generation differs');return {'decision':'no-op','candidate_state':'same','reason':'identical generation'}
    if candidate['as_of_utc']<current['as_of_utc'] or candidate['complete_through_date']<current['complete_through_date']:
        return {'decision':'no-op','candidate_state':'stale','reason':'older fixed cutoff/source run'}
    require(candidate['parent_generation']==current['generation'],'Stale base: rebuild from current durable state before retry')
    if candidate.get('integration_version')=='dendra-integration-2':
        import hashlib
        require(current.get('integration_version')=='dendra-integration-2','Archive publication requires explicit seed migration')
        old=set(current['selection_ids']);new=set(candidate['selection_ids'])
        require(old<=new,'Archive selection deletion forbidden')
        require(candidate['lineage']['state_parent_generation']==current['generation'],'Archive state/public parent mismatch')
        require(candidate['lineage']['seed_sha256']==current['lineage']['seed_sha256'] and candidate['lineage']['seed_generation']==current['lineage']['seed_generation'],'Archive seed lineage changed')
        if new-old:
            review=candidate['lineage'].get('selection_change') or {}
            require(review.get('version')=='dendra-selection-add-1' and review.get('prior_selection_sha256')==hashlib.sha256('\n'.join(sorted(old)).encode()).hexdigest() and set(review.get('additions',[]))==new-old and review.get('review_id') and review.get('reason'),'Unreviewed archive selection addition')
    else:
        require(sorted(s['datastream_id'] for s in candidate['streams'])==sorted(s['datastream_id'] for s in current['streams']),'Selection deletion/addition requires migration')
    require(candidate['policy_version']==current['policy_version'],'Policy change requires review')
    return {'decision':'publish','candidate_state':'new','reason':'complete descendant of current state'}

def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    b=sub.add_parser('build');b.add_argument('--generation',required=True);b.add_argument('--output',required=True);b.add_argument('--state');b.add_argument('--role',choices=['seed','prepared'])
    b=sub.add_parser('validate');b.add_argument('--root',required=True)
    b=sub.add_parser('prepare');b.add_argument('--root',required=True);b.add_argument('--source-sha',required=True)
    a=p.parse_args()
    if a.command=='build':build(a.generation,a.output,state=a.state,role=a.role)
    elif a.command=='validate':print(json.dumps(validate(a.root)))
    else:prepare(a.root,a.source_sha)
if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,OSError,shared.PublisherError) as exc:print(str(exc),file=sys.stderr);sys.exit(2)
