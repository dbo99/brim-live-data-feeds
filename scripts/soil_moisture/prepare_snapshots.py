"""Offline lossless common views; no HTTP, mean calculation, or publication."""
import argparse,csv,hashlib,json,shutil,tempfile,subprocess,sys
from common_index import emit as emit_index
from scan_transport import encode_scan, preflight
from collections import Counter
from pathlib import Path

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def write(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
def num(x):return x if isinstance(x,(int,float)) else None

def validate_response(spec,records):
    params=spec['params'];requested=params['stationTriplets'].split(',')
    assert len({r['stationTriplet'] for r in records})==len(records),'duplicate station'
    assert all(r['stationTriplet'] in requested for r in records),'unexpected station'
    if spec['kind']=='stations':return
    selectors=params['elements'].split(',')
    for record in records:
        seen=set()
        for block in record['data']:
            e=block['stationElement'];sid=f"{e['elementCode']}:{e['heightDepth']}:{e['ordinal']}"
            assert sid in selectors and sid not in seen,'unexpected/duplicate sensor';seen.add(sid)
            assert e['durationName']==params['duration'] and e['storedUnitCode']=='pct' and e['originalUnitCode']=='pct','unverified unit/duration'
            dates=[v['date'] for v in block['values']]
            assert dates==sorted(set(dates)),'duplicate/out-of-order dates'
            assert all(params['beginDate']<=d<=params['endDate'] for d in dates),'date over-return'

def _prepare(scan,dendra,inventory,nrcs,out):
    out.mkdir(parents=True,exist_ok=True);manifest=[]
    def inp(p):manifest.append({'path':p.name,'bytes':p.stat().st_size,'sha256':sha(p)});return p
    def blm(xy):return {'on_blm_ca':None,'dist_to_blm_mi':None,'dist_to_blm_ft':None,'blm_geometry_scope':'BLM-California managed lands','geometry_sha256':None,'coordinate_sha256':hashlib.sha256(json.dumps(xy).encode()).hexdigest(),'method':'EPSG:3310 st_intersects/st_distance; USGS 48/49','calculated_at':None,'null_reason':'Permitted public BLM-CA geometry unavailable'}
    def station(source,id,name,xy,provider,aliases=[]):return dict(key=source+':'+id,source=source,id=id,name=name,coordinates=xy,provider=provider,aliases=aliases,coordinate_provenance='Public source metadata',sensors=[],blm=blm(xy),primary_sensor=None)
    def sensor(id,depth,unit='in',native=None):return dict(id=id,parameter='soil_moisture',depth_native=depth if native is None else native,depth_unit=unit,depth_mm=None if depth is None else round(depth*(25.4 if unit=='in' else 10),10),orientation='below_ground' if depth is not None else 'unspecified',primary_rank=1,public_access=True,unit='percent VWC',capabilities=dict(latest_vwc=False,recent_change=False,source_context=False,common_reference=False,companion=False),observation=None,history=None)
    def descriptor(p):return dict(path=p.name,sha256=sha(p),bytes=p.stat().st_size)
    def emit(source,stations,generation,note):
        counts=dict(stations=len(stations),sensors=sum(len(s['sensors']) for s in stations),imported_histories=sum(bool(v['history'] and v['history'].get('available')) for s in stations for v in s['sensors']))
        shared={}
        all_sensors=[z for s in stations for z in s['sensors']]
        for k in list(all_sensors[0]) if all_sensors else []:
            if k in ['id','depth_native','depth_mm','observation','history']:continue
            common,n=Counter(json.dumps(z.get(k),sort_keys=True) for z in all_sensors).most_common(1)[0]
            if n>=len(all_sensors)/2:
                shared[k]=json.loads(common)
                for z in all_sensors:
                    if z.get(k)==shared[k]:z.pop(k,None)
        emit_index(out,source,dict(schema='brim-soil-moisture-1',source=source,generation=generation,note=note,sensor_defaults=shared,stations=stations,counts=counts))

    geo=read(inp(scan/'scan_soil_moisture_latest.geojson'));summary=read(inp(scan/'scan_soil_moisture_latest_summary.json'))
    csvs,columns={},{}
    for p in sorted(scan.glob('scan_*.csv')):
        with inp(p).open(newline='') as handle:
            reader=csv.DictReader(handle)
            csvs[p.name]=list(reader)
            columns[p.name]=reader.fieldnames
    stations=[]
    for f in geo['features']:
        p=f['properties'];id=str(p['station_triplet']);s=station('scan',id,p['station_name'],f['geometry']['coordinates'],p['provider'],[str(p['station_id']),p['station_uid'],p['shef_id']]);s['source_url']=p['site_page_url'];s['network_code']='SCAN';s['source_generation']=p['feed_build_time_utc']
        for v in json.loads(p['depth_values_json']):
            z=sensor(v['sensor_id'],v['depth_in']);z['sensor_count']=v['sensor_count'];z['primary_policy']='published same-depth composite; no individual substitution';z['unit_evidence']='Existing SCAN published sms_pct';z['precision']='published source precision retained';z['source_era']='instrument era unresolved in published composite'
            value=num(v['sms_pct']);z['observation']=dict(value=value,date=v['obs_date'],day_offset='-08:00',statistic='NRCS Daily SMS.I; temporal statistic not verified; same-depth arithmetic composite if sensor_count>1',eligible=value is not None and 0<=value<=100,missing_reason=None if value is not None else 'published missing',source_time=None,timestamp_role='Daily date; soilDB noon is a generated label, not observed time',source_timezone='soilDB UTC request; date labels retained; display America/Los_Angeles',qc='published flags not supplied',sample_count=None,coverage=None,retrieved_at=None,build_time=p['feed_build_time_utc'])
            z['capabilities'].update(latest_vwc=True,source_context=True);z['windows']=dict(display='current WY + published monthly/prior-WY context',archive='published snapshot only; no retention change',reference='existing SCAN source context; not common daily mean reference');s['sensors'].append(z)
            if v['depth_in']==p['display_depth_in']:s['primary_sensor']=z['id']
        tables={k:[r for r in rows if 'site_code' not in r or str(r['site_code'])==str(p['site_code'])] for k,rows in csvs.items()}
        bundle=encode_scan(f,tables,columns)
        path=out/('scan-'+str(p['site_code'])+'.json');write(path,bundle);s['bundle']=descriptor(path)
        for z in s['sensors']:z['history']=dict(available=True,bundle=s['bundle']['path'])
        stations.append(s)
    emit('scan',stations,sha(scan/'scan_soil_moisture_latest.geojson'),'Frozen prepared snapshot; source daily statistic/composites disclosed; legacy reference plots retained')
    idx=read(inp(dendra/'index.json'));cat=read(inp(inventory));ds=[]
    if idx['schema_version']=='dendra-daily-2.0.0':
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]));import dendra_archive
        # Validated native tree is mounted at the expected scoped root for producer preflight.
        with tempfile.TemporaryDirectory(prefix='sm2a-native-') as temp:
            root=Path(temp);shutil.copytree(dendra,root/'docs/data/dendra');dendra_archive.validate(root);idx=dendra_archive.open_index(root)
    for st in cat['stations']:
        native_station=next((x for x in idx['stations'] if x['station_id']==st['id']),None)
        # Current selected public metadata overrides stale inventory coordinates.
        # Missing/withheld current geometry is never recovered from an older catalog.
        protected=bool(st.get('source_is_geo_protected') or st.get('is_geo_protected') or (native_station or {}).get('source_is_geo_protected') or any(x.get('source_is_geo_protected') for x in idx['streams'] if x['station_id']==st['id']))
        xy=None if protected else (native_station.get('geometry') or {}).get('coordinates') if native_station is not None else st['xy']
        if native_station is not None and (native_station.get('public_level')!=3 or native_station.get('source_is_hidden') is not False):continue
        s=station('dendra',st['id'],st['name'],xy,st['organization']);s['source_url']=st['url'];s['source_generation']=idx['generation'];s['inventory_date']=cat['inventory_generated']
        for v in st['catalog']:
            z=sensor(v['id'],v['depth'],'cm');z['orientation']=v['orientation'];z['primary_policy']='shallowest reported imported sensor; then catalog depth/id; never by value';z['unit_evidence']=v['unit_status'];z['unit']='percent VWC' if v['unit_status']=='verified_percent_conversion' else None;z['native_unit']=v['unit'];z['unit_missing_reason']=None if z['unit'] else 'Native scale unresolved; no percent conversion';z['source_era']='exact Dendra datastream identity';z['windows']=dict(display='accepted imported water years',archive='accepted D2A only',reference=None)
            real=next((x for x in idx['streams'] if x['datastream_id']==v['id'] and x['parameter']=='soil_moisture'),None)
            if real:
                last=real.get('latest_accepted') if 'latest_accepted' in real else next((r for r in reversed(real['recent_rows']) if r['ok']),None)
                if 'windows' in idx:z['windows']=idx['windows']['soil_moisture']
                z['observation']=dict(value=last['v'] if last else None,date=last['date'] if last else None,day_offset='-08:00',statistic='accepted completed fixed-PST arithmetic daily mean',eligible=bool(last),missing_reason=None if last else 'No eligible acquired rows',sample_count=last['n'] if last else None,coverage=last['coverage'] if last else None,qc=last['flags'] if last else None,build_time=idx['generated_at_utc'],last_retrieved_at=real['last_retrieved_at_utc'],current_interval_retrieved_at=real['current_interval_retrieved_at_utc'],source_time=real['latest_observation_utc'])
                z['history']=dict(available=True,reader='exact native generation');z['change']=real['change'];z['capabilities'].update(latest_vwc=True,recent_change=True,companion=v['depth'] is not None and any(x['station_id']==st['id'] and x['parameter']=='soil_temperature' and x['depth_cm']==v['depth'] and x['orientation']==real['orientation'] for x in idx['streams']))
            s['sensors'].append(z)
        ordered=sorted(s['sensors'],key=lambda z:(not bool(z['history']),z['depth_mm'] is None,z['depth_mm'] or 0,z['id']))
        s['primary_sensor']=ordered[0]['id'] if ordered else None;ds.append(s)
    emit('dendra',ds,idx['generation'],'Dated 122-station / 434-stream catalog; selected saved histories imported. Metadata-only values are null. Frozen observations, NOT LIVE.')
    shutil.copytree(dendra,out/'dendra',dirs_exist_ok=True)
    meta=[];daily=[];hourly=[];raw_manifest=[]
    for p in sorted(nrcs.glob('*.meta.json')):
        m=read(p);raw=p.with_name(p.name.replace('.meta',''));assert sha(raw)==m['sha256'];raw_manifest.append({'file':raw.name,**m});inp(raw);r=read(raw);validate_response(m['spec'],r)
        if m['spec']['kind']=='stations':meta=r
        elif m['spec']['params']['duration']=='DAILY':daily.extend(r)
        else:hourly.extend(r)
    ns=[]
    for st in meta:
        id=st['stationTriplet'];s=station('snotel',id,st['name'],[st['longitude'],st['latitude']],st['operator'],[st['stationId'],st['shefId']]);s['network_code']=st['networkCode'];s['source_url']='https://wcc.sc.egov.usda.gov/nwcc/site?sitenum='+st['stationId'];s['source_generation']=hashlib.sha256(json.dumps(raw_manifest,sort_keys=True).encode()).hexdigest()
        record=next((x for x in daily if x['stationTriplet']==id),None)
        for block in (record or {}).get('data',[]):
            e=block['stationElement'];sid=f"{e['elementCode']}:{e['heightDepth']}:{e['ordinal']}";z=sensor(sid,abs(e['heightDepth']),native=e['heightDepth']);z['ordinal']=e['ordinal'];z['source_element']=e;z['precision']=e['dataPrecision'];z['unit_evidence']={'stored':e['storedUnitCode'],'original':e['originalUnitCode']};z['primary_policy']='ordinal 1, 8-inch default; no best-value sensor selection';z['source_era']={'begin':e['beginDate'],'end':e['endDate']}
            vals=[v for v in block['values'] if v['date']<='2026-09-20'];valid=[v for v in vals if isinstance(v.get('value'),(int,float)) and 0<=v['value']<=100 and v.get('qcFlag')=='V'];last=valid[-1] if valid else None
            z['observation']=dict(value=last['value'] if last else None,date=last['date'] if last else None,day_offset='-08:00',statistic='AWDB derived DAILY SMS; precise reduction unresolved (not assumed mean)',eligible=bool(last),missing_reason=None if last else 'No valid source values',sample_count=None,coverage=None,qc={k:last.get(k) for k in ['qcFlag','qaFlag','origQcFlag']} if last else None,source_timezone=st['dataTimeZone'],interval_reference='END; date unshifted',retrieved_at=next(m['received_utc'] for m in raw_manifest if m['spec']['kind']=='data' and m['spec']['params']['stationTriplets']==id and m['spec']['params']['duration']=='DAILY'))
            z['capabilities']['latest_vwc']=True;z['windows']=dict(display=['2026-06-23','2026-09-20'],archive=['2026-06-23','2026-09-20'],reference=None);z['history']=dict(available=bool(valid),rows=len(vals));s['sensors'].append(z)
        s['primary_sensor']=next((z['id'] for z in s['sensors'] if z['depth_mm']==203.2),None)
        path=out/('snotel-'+st['stationId']+'.json');write(path,dict(schema='sm1-snotel-popup-1',metadata=st,data=record));s['bundle']=descriptor(path);ns.append(s)
    emit('snotel',ns,ns[0]['source_generation'] if ns else 'unavailable',f'SNOTEL PILOT: {sum(any(z["history"]["available"] for z in s["sensors"]) for s in ns)} imported stations. DAILY reduction unresolved; no recent-change/reference capability.')
    write(out/'input-manifest.json',manifest)
    write(out/'nrcs-response-manifest.json',raw_manifest)
    return {k:read(out/(k+'.json'))['counts'] for k in ['scan','dendra','snotel']}


def prepare(scan,dendra,inventory,nrcs,out,consumer_check):
    """Build a fresh candidate; only rename into place after both sides preflight.

    consumer_check is the explicitly supplied offline validator from the consuming
    checkout. No private consumer source/path is embedded in this public adapter.
    Existing outputs are never rewritten on a failed or successful rerun.
    """
    out=Path(out).resolve()
    consumer_check=Path(consumer_check).resolve()
    if out.exists():raise ValueError('Output must be a new directory; existing prepared products are immutable')
    if not consumer_check.is_file():raise ValueError('Offline consumer preflight required')
    out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='sm1r1-prepare-',dir=out.parent) as temp:
        stage=Path(temp)/'candidate'
        counts=_prepare(scan,dendra,inventory,nrcs,stage)
        producer=preflight(stage)
        result=subprocess.run(['node',str(consumer_check),str(stage)],capture_output=True,text=True)
        if result.returncode:raise ValueError('Consumer preflight failed: '+result.stdout+result.stderr)
        consumer=json.loads(result.stdout)
        if not consumer.get('passed'):raise ValueError('Consumer preflight did not pass')
        write(stage/'preflight.json',{'producer':producer,'consumer':consumer,'complete':True})
        stage.rename(out)
    print(json.dumps(counts))


if __name__=='__main__':
    a=argparse.ArgumentParser()
    for k in ['scan','dendra','inventory','nrcs','out','consumer-check']:
        a.add_argument('--'+k,type=Path,required=True)
    prepare(**vars(a.parse_args()))
