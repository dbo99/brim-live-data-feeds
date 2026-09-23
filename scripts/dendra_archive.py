"""Integration2 archive layout. R owns all daily/statistical calculations.

Public partitions contain stable values/query provenance, not generation clocks.
Strict version dispatch leaves accepted integration1 artifacts replayable.
"""
from __future__ import annotations
import argparse,copy,csv,hashlib,io,json,math,os,re,shutil,sys,tempfile
from pathlib import Path
from datetime import date,datetime,timedelta,timezone
import dendra_candidate as dc

SCHEMA='dendra-daily-2.0.0'; INTEGRATION='dendra-integration-2'; PART='dendra-archive-partition-2'; MANIFEST='dendra-stream-manifest-2'; CATALOG='dendra-map-catalog-2'
LIMITS={'index_bytes':256000,'shard_bytes':262144,'partition_bytes':1000000,'csv_bytes':512000,'files':20000,'inventory_page_files':500,'catalog_shards':128,'streams':1024,'stations':1500,'years_per_stream':256,'cache_entries':24,'cache_bytes':12000000,'selected_rows':93696,'csv_export_bytes':32000000}
PREFIX='docs/data/dendra/'
IDENTITY=('datastream_id','station_id','parameter','depth_cm','orientation','native_unit_name','unit_normalization','source_terms','source_attributes','public_level','source_is_hidden','source_is_geo_protected')

def windows(temperature_days=90):
    dc.require(type(temperature_days) is int and 1<=temperature_days<=3660,'Invalid explicit temperature window')
    return {'version':'dendra-windows-2','soil_moisture':{'archive':'all_acquired_daily_no_automatic_deletion','display_water_years':10,'reference':'not_computed'},'soil_temperature':{'archive_completed_days':temperature_days,'display_completed_days':temperature_days,'reference':'not_computed'}}

def encoded(value):return (json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n').encode()
def safe_path(path):
    dc.require(isinstance(path,str) and re.fullmatch(r'docs/data/dendra/(history|companion|diagnostics|state)/[a-zA-Z0-9_./-]+',path) and '..' not in Path(path).parts,'Unsafe archive path')
    return path

def read_bounded(path,limit):
    path=Path(path);dc.require(not path.is_symlink(),'Archive symlink')
    with path.open('rb') as handle:data=handle.read(limit+1)
    dc.require(0<len(data)<=limit,'Archive byte bound: '+path.name);return data

def descriptor(root,path,limit):
    data=read_bounded(Path(root)/path,limit);return {'path':path,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}

def checked(root,d,limit):
    safe_path(d['path']);dc.require(type(d['bytes']) is int and 0<d['bytes']<=limit and re.fullmatch('[a-f0-9]{64}',d['sha256']),'Archive descriptor bound/hash')
    data=read_bounded(Path(root)/d['path'],limit);dc.require(len(data)==d['bytes'] and hashlib.sha256(data).hexdigest()==d['sha256'],'Archive integrity: '+d['path']);return data

def put(root,prefix,value,limit,suffix='.json'):
    data=encoded(value) if suffix=='.json' else value.encode();dc.require(len(data)<=limit,'Archive writer bound: '+prefix)
    digest=hashlib.sha256(data).hexdigest();path=prefix+'-'+digest+suffix;p=Path(root)/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    return {'path':path,'bytes':len(data),'sha256':digest}

def csv_bytes(sid,rows):
    out=io.StringIO(newline='');w=csv.DictWriter(out,fieldnames=dc.CSV_FIELDS);w.writeheader()
    for r in rows:w.writerow(dc.csv_record(sid,r))
    return out.getvalue()

def expected_limit(path):
    if path.endswith('.csv'):return LIMITS['csv_bytes']
    if '/state/' in path:return LIMITS['shard_bytes']
    return LIMITS['partition_bytes']

def write_layout(generation,root):
    """Pure serialization; caller MUST validate before staging or publication."""
    generation=Path(generation);root=Path(root);index=dc.load(generation/'index.json');dc.generation_id(index['generation'])
    dc.require(index['schema_version']==SCHEMA and index['integration_version']==INTEGRATION,'Archive layout version')
    records=[];manifest_records=[];station_map={s['station_id']:s for s in index['stations']}
    for s in index['streams']:
        sid=s['datastream_id'];dc.require(dc.ID.fullmatch(sid),'Archive stream id');p=dc.load(generation/'daily'/f'{sid}.json');rows=p['rows'];query=p['source_snapshot']['query_by_date'];chunks=p['source_snapshot']['chunks'];parts=[]
        for wy in sorted({r['water_year'] for r in rows}):
            yr=[r for r in rows if r['water_year']==wy];q={r['date']:query[r['date']] for r in yr};used={v.get('content_sha256') for v in q.values()}
            source=[c for c in chunks if c['content_sha256'] in used]
            body={'schema_version':PART,'policy_version':index['policy_version'],'safeguard_version':dc.VALIDATOR_VERSION,'datastream_id':sid,'station_id':s['station_id'],'parameter':s['parameter'],'water_year':wy,'rows':yr,'query_by_date':q,'chunks':source}
            d=put(root,f'{PREFIX}diagnostics/{sid}/{wy}',body,LIMITS['partition_bytes'])
            c=put(root,f'{PREFIX}diagnostics/{sid}/{wy}',csv_bytes(sid,yr),LIMITS['csv_bytes'],'.csv')
            history={'schema_version':dc.SCHEMA,'policy_version':index['policy_version'],'safeguard_version':dc.VALIDATOR_VERSION,**{k:s[k] for k in ('datastream_id','station_id','parameter','depth_cm','orientation','native_unit_name','unit_normalization')},'water_year':wy,'unit':s['unit_normalization']['target_unit'],'rows':[dict(dc.projected(r),v=r['mean_value']) if s['parameter']=='soil_temperature' else dc.projected(r) for r in yr]}
            h=put(root,f"{PREFIX}{'companion' if s['parameter']=='soil_temperature' else 'history'}/{sid}/{wy}",history,LIMITS['partition_bytes'])
            parts.append({'water_year':wy,'rows':len(yr),'start_date':yr[0]['date'],'end_date':yr[-1]['date'],'history':h,'diagnostics':d,'csv':c})
        header=copy.deepcopy(p);header.pop('rows');header['source_snapshot'].pop('query_by_date');header['source_snapshot'].pop('chunks')
        body={'schema_version':MANIFEST,'stream':s,'product_header':header,'windows':index['windows'][s['parameter']],'partitions':parts}
        m=put(root,f'{PREFIX}state/streams/{sid}',body,LIMITS['shard_bytes']);manifest_records.append(m)
        # Map summaries omit heavy diagnostics, query evidence and history inventories.
        records.append({**s,'manifest':m})
    groups=[];group=[]
    def catalog(group):return {'schema_version':CATALOG,'streams':group,'stations':[station_map[k] for k in sorted({s['station_id'] for s in group})]}
    for s in records:
        if group and len(encoded(catalog(group+[s])))>240000:groups.append(group);group=[]
        group.append(s)
    if group:groups.append(group)
    root_index={k:copy.deepcopy(v) for k,v in index.items() if k not in ('streams','stations')}
    root_index.update(selection_ids=sorted(s['datastream_id'] for s in records),limits=LIMITS,stream_count=len(records),station_count=len(station_map),catalogs=[put(root,f'{PREFIX}state/catalog/{n:03d}',catalog(g),LIMITS['shard_bytes']) for n,g in enumerate(groups)])
    # Root lists bounded pages, each owning bounded descriptors, never one giant file list.
    files=[descriptor(root,p.relative_to(root).as_posix(),expected_limit(p.as_posix())) for p in sorted(root.rglob('*')) if p.is_file()]
    root_index['file_pages']=[put(root,f'{PREFIX}state/files/{n:03d}',{'schema_version':'dendra-file-page-2','files':files[lo:lo+LIMITS['inventory_page_files']]},LIMITS['shard_bytes']) for n,lo in enumerate(range(0,len(files),LIMITS['inventory_page_files']))]
    root_index['file_count']=len(files)+len(root_index['file_pages'])+1
    dc.write(root/dc.FIXED[0],root_index)
    return root_index

def open_index(root):
    root=Path(root);i=json.loads(read_bounded(root/dc.FIXED[0],LIMITS['index_bytes']))
    dc.require(i.get('product_id')==dc.PRODUCT and i.get('schema_version')==SCHEMA and i.get('integration_version')==INTEGRATION and i.get('limits')==LIMITS,'Archive version/bounds')
    dc.require(i.get('windows')==windows(i.get('windows',{}).get('soil_temperature',{}).get('archive_completed_days')),'Archive window policy')
    dc.require(i.get('policy_version')=='dendra-daily-1.0.0-frozen-cadence' and i.get('safeguard_version')==dc.VALIDATOR_VERSION,'Archive scientific policy')
    dc.generation_id(i['generation']);dc.require(0<i['stream_count']<=LIMITS['streams'] and 0<i['station_count']<=LIMITS['stations'],'Archive catalog count bound')
    dc.require(0<len(i['catalogs'])<=LIMITS['catalog_shards'] and 0<len(i['file_pages'])<=40 and i['file_count']<=LIMITS['files'],'Archive file/page bounds')
    streams=[];stations={};catalog_paths=set()
    for d in i['catalogs']:
        dc.require(re.fullmatch(r'docs/data/dendra/state/catalog/\d{3}-'+d['sha256']+r'\.json',d['path']) and d['path'] not in catalog_paths,'Catalog path/duplicate');catalog_paths.add(d['path']);b=json.loads(checked(root,d,LIMITS['shard_bytes']));dc.require(b['schema_version']==CATALOG,'Catalog schema');streams+=b['streams']
        for st in b['stations']:
            sid=st['station_id'];dc.require(sid not in stations or stations[sid]==st,'Mixed station catalog');stations[sid]=st
    dc.require(len(streams)==i['stream_count'] and len({s['datastream_id'] for s in streams})==len(streams) and len(stations)==i['station_count'],'Partial/duplicate catalog')
    dc.require(i['selection_ids']==sorted(s['datastream_id'] for s in streams),'Selection/catalog binding')
    return {**i,'streams':streams,'stations':list(stations.values())}

def stream_product(root,s):
    d=s['manifest'];dc.require(d['path']==f"{PREFIX}state/streams/{s['datastream_id']}-{d['sha256']}.json",'Stream manifest path')
    m=json.loads(checked(root,d,LIMITS['shard_bytes']));dc.require(m['schema_version']==MANIFEST and m['stream']=={k:v for k,v in s.items() if k!='manifest'},'Stream manifest identity')
    parts=m['partitions'];dc.require(0<len(parts)<=LIMITS['years_per_stream'] and [p['water_year'] for p in parts]==sorted({p['water_year'] for p in parts}),'Archive year bound/order')
    p=copy.deepcopy(m['product_header']);rows=[];query={};chunks={}
    for part in parts:
        b=json.loads(checked(root,part['diagnostics'],LIMITS['partition_bytes']));dc.require(b['schema_version']==PART and b['policy_version']=='dendra-daily-1.0.0-frozen-cadence' and b['safeguard_version']==dc.VALIDATOR_VERSION and all(b[k]==s[k] for k in ('datastream_id','station_id','parameter')) and b['water_year']==part['water_year'],'Diagnostic partition identity')
        dc.require(0<len(b['rows'])<=366 and len(b['rows'])==part['rows'],'Daily partition row bound')
        dc.require(list(b['query_by_date'])==[r['date'] for r in b['rows']],'Partition query closure')
        rows+=b['rows'];query.update(b['query_by_date'])
        for c in b['chunks']:chunks[encoded(c)]=c
    p['rows']=rows;p['source_snapshot'].update(query_by_date=query,chunks=list(chunks.values()))
    return m,p

def materialize(root,destination):
    """Temporary R sufficient-statistic view; portable state keeps only partitions."""
    from dendra_validation import content_binding
    root=Path(root);destination=Path(destination);dc.require(not destination.exists(),'Materialized view must be new')
    before=content_binding(root)
    try:
        validate(root,_materialize_to=destination)
        dc.require(content_binding(root)==before,'Archive changed during materialization')
    except BaseException:
        if destination.exists():shutil.rmtree(destination)
        raise
    return dc.load(destination/'index.json')

def validate(root,now=None,semantic=True,_materialize_to=None):
    from dendra_validation import content_binding
    dc.require(_materialize_to is None or semantic,'Materialization requires full science')
    before=content_binding(root)
    root=Path(root);inventory=dc.shared._candidate_inventory(root);dc.shared._validate_desired_inventory(inventory,fixed_paths=dc.FIXED,owned_roots=dc.OWNED)
    i=open_index(root);declared={};page_paths=set()
    for d in i['file_pages']:
        dc.require(re.fullmatch(r'docs/data/dendra/state/files/\d{3}-'+d['sha256']+r'\.json',d['path']) and d['path'] not in page_paths,'Inventory page path/duplicate');page_paths.add(d['path']);b=json.loads(checked(root,d,LIMITS['shard_bytes']));dc.require(b['schema_version']=='dendra-file-page-2' and 0<len(b['files'])<=LIMITS['inventory_page_files'],'Inventory page schema/bound')
        for f in b['files']:
            dc.require(f['path'] not in declared,'Duplicate inventory descriptor');checked(root,f,expected_limit(f['path']));declared[f['path']]=f
    dc.require(set(inventory)==set(declared)|page_paths|set(dc.FIXED) and len(inventory)==i['file_count'],'Archive inventory closure')
    used={d['path'] for d in i['catalogs']};stations={s['station_id']:s for s in i['stations']};cutoff=date.fromisoformat(i['complete_through_date']);asof=datetime.fromisoformat(i['as_of_utc'].replace('Z','+00:00'));dc.require(cutoff==(asof-timedelta(hours=8)).date()-timedelta(days=1),'Archive global cutoff clock')
    dc.require(i['mode'] in ('live','replay') and i['completeness']=='complete_selected_catalog','Archive source completeness');dc.require(i['lineage']['kind'] in ('imported_seed','descendant'),'Archive lineage')
    lineage=i['lineage'];dc.require(re.fullmatch('[0-9a-f]{64}',lineage.get('seed_sha256') or ''),'Archive seed checksum');dc.generation_id(lineage['seed_generation'])
    if lineage['kind']=='imported_seed':
        dc.require(i['mode']=='replay' and i['parent_generation'] is None and lineage['state_parent_generation'] is None and lineage['seed_generation']==i['generation'] and i['publication_time_utc'] is None,'Imported seed is never live/acknowledged')
    else:
        dc.generation_id(lineage['state_parent_generation']);dc.require(lineage['state_parent_generation']!=i['generation'] and lineage['seed_generation']!=i['generation'],'Archive descendant ancestry')
        dc.require(i['parent_generation']==lineage['state_parent_generation'] if i['parent_generation'] else lineage['state_parent_generation']==lineage['seed_generation'],'Archive public/state parent binding')
    if i['parent_generation'] is not None:dc.generation_id(i['parent_generation'])
    for st in stations.values():
        dc.require(dc.ID.fullmatch(st['station_id']) and st['public_level']==3 and st['source_is_hidden'] is False and type(st['source_is_geo_protected']) is bool,'Archive station policy/identity')
        if st.get('geometry'):
            g=st['geometry'];c=g.get('coordinates');dc.require(not st['source_is_geo_protected'] and g.get('type')=='Point' and isinstance(c,list) and len(c)==2 and all(type(v) in (int,float) and math.isfinite(v) for v in c) and abs(c[0])<=180 and abs(c[1])<=90,'Archive public coordinates')
    rowcount=eligible=0
    with tempfile.TemporaryDirectory(prefix='dendra-archive-validation-') as temp:
        view=Path(temp);view_index=copy.deepcopy(i)
        for s in view_index['streams']:
            sid=s['datastream_id'];dc.require(dc.ID.fullmatch(sid) and s['station_id'] in stations,'Archive sensor/station identity');st=stations[s['station_id']]
            dc.require(st['public_level']==s['public_level']==3 and st['source_is_hidden'] is False and s['source_is_hidden'] is False and type(s['source_is_geo_protected']) is bool,'Archive public source exclusion');dc.require(not st.get('source_is_geo_protected') or not st.get('geometry'),'Archive protected coordinates')
            dc.require(s['parameter'] in ('soil_moisture','soil_temperature') and all(k in s for k in IDENTITY),'Archive parameter/metadata')
            dc.require(s['depth_cm'] is None or type(s['depth_cm']) in (int,float) and math.isfinite(s['depth_cm']) and s['depth_cm']>=0,'Archive numeric depth')
            m,p=stream_product(root,s);rows=p['rows'];dates=[r['date'] for r in rows];dc.require(dates==sorted(set(dates)) and dates[-1]==s['acquired_through_date']<=i['complete_through_date'],'Acquired per-stream coverage')
            dc.require(len(rows)<=LIMITS['selected_rows'] and m['windows']==i['windows'][s['parameter']],'Archive stream/window bound')
            if s['parameter']=='soil_temperature':dc.require(dates[0]>=(cutoff-timedelta(days=i['windows']['soil_temperature']['archive_completed_days']-1)).isoformat(),'Temperature context retention')
            for k in IDENTITY:dc.require(p['stream'].get(k)==s[k],'Archive scientific identity: '+k)
            dc.require(s['summary']['calendar_row_count']==len(rows),'Archive summary count')
            for part in m['partitions']:
                yr=[r for r in rows if r['water_year']==part['water_year']];dc.require(part['start_date']==yr[0]['date'] and part['end_date']==yr[-1]['date'],'Partition date coverage')
                for kind,d in [(k,part[k]) for k in ('history','diagnostics','csv')]:
                    base='companion' if kind=='history' and s['parameter']=='soil_temperature' else 'history' if kind=='history' else 'diagnostics';suffix='.csv' if kind=='csv' else '.json';dc.require(d['path']==f"{PREFIX}{base}/{sid}/{part['water_year']}-{d['sha256']}"+suffix and declared.get(d['path'])==d,'Partition membership/path');used.add(d['path'])
                h=json.loads(checked(root,part['history'],LIMITS['partition_bytes']));expected=[dict(dc.projected(r),v=r['mean_value']) if s['parameter']=='soil_temperature' else dc.projected(r) for r in yr]
                dc.require(h['schema_version']==dc.SCHEMA and h['policy_version']==i['policy_version'] and h['safeguard_version']==dc.VALIDATOR_VERSION and all(h[k]==s[k] for k in ('datastream_id','station_id','parameter','depth_cm','orientation','native_unit_name','unit_normalization')) and h['water_year']==part['water_year'] and h['rows']==expected and h['unit']==s['unit_normalization']['target_unit'],'Archive history projection/identity')
                dc.require(checked(root,part['csv'],LIMITS['csv_bytes']).decode()==csv_bytes(sid,yr),'Partition CSV exact identity')
            for r in rows:
                d=date.fromisoformat(r['date']);wy=d.year+(d.month>=10);origin=date(wy-1,10,1);aligned=date(1999 if d.month>=10 else 2000,d.month,d.day)
                dc.require((r['water_year'],r['dowy'],r['water_day'],r['water_day_aligned'])==(wy,(d-origin).days+1,(d-origin).days+1,(aligned-date(1999,10,1)).days+1),'Archive calendar alignment')
            dc.require(s['recent_rows']==[dict(dc.projected(r),v=r['mean_value']) if s['parameter']=='soil_temperature' else dc.projected(r) for r in rows if r['date']>=(cutoff-timedelta(days=59)).isoformat()],'Archive recent summaries')
            last=next((r for r in reversed(rows) if r['plot_eligible']),None);wanted=dict(dc.projected(last),v=last['mean_value']) if last else None;dc.require(s['latest_accepted']==wanted,'Archive latest accepted summary')
            dc.require(s['manifest']['path'] in declared and declared[s['manifest']['path']]==s['manifest'],'Manifest inventory membership');used.add(s['manifest']['path'])
            rowcount+=len(rows);eligible+=sum(r['plot_eligible'] for r in rows)
            if _materialize_to is not None:
                s['diagnostics_path']=f"docs/data/dendra/diagnostics/{sid}.json";dc.write(view/s['diagnostics_path'],p)
        dc.require(used==set(declared),'Unreferenced archive file')
        dc.write(view/dc.FIXED[0],view_index)
        result=dc.semantic_validate(view,now=now,archive_root=root if _materialize_to is None else None) if semantic else {'status':'not_run','scope':'explicit synthetic capacity shape test, not scientific validation'}
        if semantic:dc.require(result['rows_checked']==rowcount and result['streams_checked']==len(i['streams']),'Full archive semantic coverage mismatch')
        dc.require(content_binding(root)==before,'Archive changed during validation')
        if _materialize_to is not None:
            destination=Path(_materialize_to);dc.require(not destination.exists(),'Materialized view must be new')
            daily=view/'daily';daily.mkdir()
            for s in view_index['streams']:
                os.replace(view/s.pop('diagnostics_path'),daily/f"{s['datastream_id']}.json")
                s.pop('manifest')
            dc.write(view/'index.json',view_index)
            shutil.rmtree(view/'docs')
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.move(str(view),str(destination))
    return {'status':'passed','streams':len(i['streams']),'calendar_rows':rowcount,'eligible_rows':eligible,'files':len(inventory),'semantic':result,'freshness':dc.freshness(i),'schema_version':SCHEMA}

def build(generation,output,now=None,_operation=None):
    from dendra_validation import operation
    op=operation(_operation)
    generation=Path(generation);i=dc.load(generation/'index.json');dest=Path(output)/dc.generation_id(i['generation']);dc.require(not dest.exists(),'Archive output generation exists');dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='archive-candidate-',dir=dest.parent) as td:
        stage=Path(td)/'candidate';stage.mkdir();write_layout(generation,stage);report=op.validate(stage,now=now);os.replace(Path(td),dest)
    dc.write(Path(output)/'validation'/f"{i['generation']}.json",report);print(json.dumps(report));return dest/'candidate'

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['materialize','validate']);p.add_argument('--root',required=True);p.add_argument('--output');a=p.parse_args()
    if a.command=='materialize':materialize(a.root,a.output)
    else:print(json.dumps(validate(a.root)))
