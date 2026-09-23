#!/usr/bin/env python3
"""Transport, metadata and state I/O only. No daily/window aggregation here."""
from __future__ import annotations
import argparse,csv,hashlib,io,json,math,os,re,sys,time,urllib.request,urllib.error,urllib.parse
from pathlib import Path
from datetime import datetime,timezone
from contextlib import contextmanager
import fcntl,signal,threading
from transport import DendraFetcher,FetchError,_content_hash,_atomic_json,format_utc,utc_now,parse_utc,ChunkStore

ID=re.compile(r'^[0-9a-f]{24}$')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load(p): return json.loads(Path(p).read_text())
def dump(p,x): _atomic_json(Path(p),x)
def check_id(s):
    if not isinstance(s,str) or not ID.fullmatch(s): raise ValueError('Invalid Dendra identity')
    return s

def public(meta):
    return (meta.get('public_level',meta.get('access_levels_resolved',{}).get('public_level')) == 3 and
            meta.get('is_hidden',meta.get('source_is_hidden')) is False)

def validate_catalog(catalog):
    stations={check_id(s['station_id']):s for s in catalog['stations']}
    seen=set()
    for s in catalog['streams']:
        sid=check_id(s['datastream_id']);check_id(s['station_id'])
        if sid in seen: raise ValueError('Duplicate stream ID')
        seen.add(sid)
        if s['station_id'] not in stations or not public(s) or not public(stations[s['station_id']]): raise ValueError('Hidden/nonpublic or missing station')
        if s.get('parameter','soil_moisture') not in ('soil_moisture','soil_temperature'): raise ValueError('Discovery-only parameter selected for processing')
    if not seen: raise ValueError('Empty processing selection')
    return catalog

def write_csv(path,rows,s):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    with Path(path).open('w',newline='') as f:
        writer=csv.writer(f);writer.writerow(['t','datastream_id','v','value_status','duplicate_conflict','alternative_out_of_range'])
        for row in rows:
            v=row.get('v');numeric=isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
            conv=s.get('unit_normalization',{});oor=False
            if conv.get('status')=='verified_percent_conversion':
                for alt in row.get('conflicting_values',[]):
                    av=alt.get('v')
                    if alt.get('value_status')=='number' and isinstance(av,(int,float)) and not isinstance(av,bool) and math.isfinite(av):
                        av=av*conv['multiplier']+conv.get('offset',0);oor|=not math.isfinite(av) or not 0<=av<=100
            writer.writerow([row['t'],row.get('datastream_id',s['datastream_id']),repr(v) if numeric else '',row['value_status'],str(bool(row.get('duplicate_conflict',False))).upper(),str(oor).upper()])

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a,**k): raise FetchError('Redirect refused for bounded public API')

class CollectionBudgetExceeded(FetchError):
    """A bounded run stopped; this is not a completed empty source interval."""

class BudgetClient:
    """One process/one request at a time; persistent ledger counts before HTTP."""
    def __init__(self,ledger,max_attempts=80,opener=None,*,max_elapsed_seconds=300,
                 max_response_bytes=64*1024**2,max_source_rows=1_000_000,
                 monotonic=time.monotonic,wall=time.time,now=utc_now):
        for value,ceiling in ((max_attempts,80),(max_elapsed_seconds,300),
                              (max_response_bytes,64*1024**2),(max_source_rows,1_000_000)):
            if isinstance(value,bool) or not isinstance(value,int) or not 0<=value<=ceiling:
                raise ValueError('Invalid collection budget')
        self.ledger=Path(ledger);self.limit=max_attempts
        self.seconds=max_elapsed_seconds;self.bytes=max_response_bytes;self.rows=max_source_rows
        self.monotonic=monotonic;self.wall=wall;self.now=now
        self.started_mono=monotonic();self.started_wall=wall();self.last_wall=self.started_wall
        self.opener=opener or urllib.request.build_opener(NoRedirect()).open
        self.fetcher=DendraFetcher(opener=self.open,timeout=25,max_attempts=2,max_pages=20,max_retry_delay=15,now_fn=now,sleep_fn=self.pause)
    def remaining(self,items):
        current=self.wall();start=items[0].get('budget_started_wall',self.started_wall) if items else self.started_wall
        if current<self.last_wall or current<start:raise CollectionBudgetExceeded('Collection wall clock moved backwards')
        self.last_wall=current
        remaining=self.seconds-max(self.monotonic()-self.started_mono,current-start)
        if remaining<=0:raise CollectionBudgetExceeded('Cumulative collection deadline exhausted')
        return remaining
    def pause(self,delay):
        items=load(self.ledger) if self.ledger.exists() else []
        remaining=self.remaining(items)
        if delay>=remaining:raise CollectionBudgetExceeded('Retry backoff exceeds remaining collection deadline')
        with self.deadline(remaining):time.sleep(delay)
        self.remaining(items)
    @contextmanager
    def deadline(self,seconds):
        # Socket timeout measures inactivity, not total transfer time. A process
        # alarm also interrupts a slow-drip read. This collector is serial/POSIX.
        if threading.current_thread() is not threading.main_thread():raise CollectionBudgetExceeded('Deadline requires serial main-thread collector')
        if signal.getitimer(signal.ITIMER_REAL)!=(0.0,0.0):raise CollectionBudgetExceeded('Existing process deadline conflicts with collection deadline')
        previous=signal.getsignal(signal.SIGALRM)
        def expired(signum,frame):raise CollectionBudgetExceeded('Cumulative collection deadline exhausted during response')
        signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,seconds)
        try:yield
        finally:signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)
    def open(self,request,timeout):
        url=request.full_url
        if not url.startswith('https://api.dendra.science/v2/'): raise FetchError('Only public Dendra v2 endpoint is permitted')
        self.ledger.parent.mkdir(parents=True,exist_ok=True)
        with self.ledger.with_suffix('.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            items=load(self.ledger) if self.ledger.exists() else []
            remaining=self.remaining(items)
            if len(items)>=self.limit: raise CollectionBudgetExceeded('Global HTTP attempt budget exhausted')
            if sum(i.get('response_bytes') or 0 for i in items)>=self.bytes:raise CollectionBudgetExceeded('Cumulative response-byte budget exhausted')
            if sum(i.get('source_rows') or 0 for i in items)>=self.rows:raise CollectionBudgetExceeded('Cumulative source-row budget exhausted')
            if len(items)>=2 and all(i.get('service_failure') for i in items[-2:]): raise FetchError('Circuit open after repeated source failures')
            record={'attempt':len(items)+1,'url':url,'requested_at_utc':format_utc(self.now()),'status':None,'response_bytes':None,'source_rows':None,'service_failure':True,'outcome':'started','budget_started_wall':items[0].get('budget_started_wall',self.started_wall) if items else self.started_wall}
            items.append(record);dump(self.ledger,items)
            begin=time.monotonic()
            try:
                with self.deadline(remaining), self.opener(request,timeout=min(timeout,remaining)) as response:
                    status=response.status;body=response.read(8*1024*1024+1)
                    record.update(status=status,response_bytes=len(body))
                    if len(body)>8*1024*1024: raise FetchError('Response exceeded 8 MiB limit')
                    if sum(i.get('response_bytes') or 0 for i in items)>self.bytes:raise CollectionBudgetExceeded('Cumulative response-byte budget exhausted')
                    payload=json.loads(body)
                    record['source_rows']=len(payload['data']) if isinstance(payload,dict) and isinstance(payload.get('data'),list) else 0
                    if sum(i.get('source_rows') or 0 for i in items)>self.rows:raise CollectionBudgetExceeded('Cumulative source-row budget exhausted')
                    self.remaining(items)
                    record.update(status=status,response_bytes=len(body),response_sha256=hashlib.sha256(body).hexdigest(),service_failure=status==429 or status>=500,outcome='received')
                    result=io.BytesIO(body);result.status=status;result.headers=response.headers
                return result
            except urllib.error.HTTPError as exc:
                record.update(status=exc.code,error_type=type(exc).__name__,service_failure=exc.code==429 or exc.code>=500,outcome='http-error');raise
            except Exception as exc:
                record.update(error_type=type(exc).__name__,outcome='failed');raise
            finally:
                record.update(retrieved_at_utc=format_utc(self.now()),elapsed_seconds=time.monotonic()-begin)
                dump(self.ledger,items)
    def metadata(self,path,params=None):
        url='https://api.dendra.science/v2/'+path
        if params: url+='?'+urllib.parse.urlencode(params)
        for attempt in (1,2):
            try:
                with self.open(urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'BRIM-Dendra-D1/1.0'}),25) as r:
                    value=json.loads(r.read())
                return value
            except urllib.error.HTTPError as exc:
                if exc.code not in (408,429,500,502,503,504) or attempt==2: raise
                self.pause(self.fetcher._retry_delay(attempt,exc.headers))
            except (urllib.error.URLError,TimeoutError,ConnectionError,OSError):
                if attempt==2: raise
                self.pause(1)
        raise AssertionError('unreachable')

def export_checkpoint(args):
    c=validate_catalog(load(args.catalog));root=Path(args.checkpoint);out=Path(args.output);out.mkdir(parents=True,exist_ok=True);spec=[]
    for s in c['streams']:
        sid=s['datastream_id'];chunks=[];rows=[]
        files=sorted((root/'chunks'/sid).glob('*.json'))
        if not files: raise ValueError('No checkpoint for '+sid)
        for f in files:
            e=load(f)
            if e.get('datastream_id')!=sid or e.get('query_complete') is not True or e.get('content_sha256')!=_content_hash(e): raise ValueError('Corrupt/incomplete native checkpoint')
            if chunks and chunks[-1]['requested_interval']['end_exclusive']!=e['requested_interval']['start_inclusive']: raise ValueError('Noncontiguous checkpoint intervals')
            rows.extend(e['rows']);chunks.append({k:e[k] for k in ('content_sha256','requested_interval','retrieval_last_utc','latest_observation_utc')})
        write_csv(out/(sid+'.csv'),rows,s)
        spec.append({'stream':s,'native_csv':str((out/(sid+'.csv')).resolve()),'native_sha256':sha(out/(sid+'.csv')),'chunks':chunks,'native_row_count':len(rows)})
    dump(out/'native_manifest.json',{'streams':spec,'catalog':c,'checkpoint_origin':'verified supplied native checkpoint'})

def classify(meta,station,unit_terms):
    terms=meta.get('terms',{});ds=terms.get('ds',{});dt=terms.get('dt',{});var=ds.get('Variable');medium=ds.get('Medium');agg=ds.get('Aggregate')
    parameter='soil_temperature' if var=='Temperature' and medium=='Soil' else 'soil_moisture' if var=='VolumetricWaterContent' and medium=='Soil' else 'air_temperature' if var=='Temperature' and medium=='Air' else 'precipitation' if var in ('Precipitation','Rainfall') else 'unclassified'
    attr=meta.get('attributes',{});unit=dt.get('Unit');depth=None;reason=[]
    # Dendra attributes are terms such as {Depth:200, LengthUnits:'Millimeter'}.
    rawdepth=attr.get('depth',{}).get('value') if isinstance(attr.get('depth'),dict) else attr.get('Depth');depthunit=attr.get('depth',{}).get('unit_tag','').removeprefix('dt_Unit_') if isinstance(attr.get('depth'),dict) else attr.get('LengthUnits')
    if isinstance(rawdepth,(int,float)) and not isinstance(rawdepth,bool) and math.isfinite(rawdepth):
        if depthunit=='Millimeter': depth=rawdepth/10
        elif depthunit=='Centimeter': depth=rawdepth
        else: reason.append('depth unit unresolved')
    normalization={'status':'unresolved','multiplier':None,'offset':None}
    term=unit_terms.get(unit,{})
    if parameter=='soil_temperature' and unit in ('DegreeCelsius','Celsius','DegreeFahrenheit','Fahrenheit','Kelvin') and term:
        mult,offset=(1,0) if unit in ('DegreeCelsius','Celsius') else ((5/9,-32*5/9) if unit in ('DegreeFahrenheit','Fahrenheit') else (1,-273.15))
        normalization={'status':'verified_temperature_conversion','multiplier':mult,'offset':offset,'target_unit':'degree Celsius','unit_definition':term}
    if parameter=='soil_moisture' and unit in ('VolumetricWaterContent','Percent') and term:
        normalization={'status':'verified_percent_conversion','multiplier':100 if unit=='VolumetricWaterContent' else 1,'offset':0,'target_unit':'% volumetric water content','unit_definition':term}
    if normalization['status']=='unresolved': reason.append('unit/parameter conversion unresolved')
    if not public(meta) or not public(station): reason.append('hidden/nonpublic metadata')
    if agg not in ('Average','Instantaneous'): reason.append('report timestamp/aggregation semantics unresolved')
    sid=meta.get('_id');station_id=meta.get('station_id')
    if not ID.fullmatch(str(sid)) or station_id!=station['_id']: reason.append('stream identity unresolved')
    r={'datastream_id':sid,'station_id':station_id,'source_name':meta.get('name'),'source_terms':terms,'source_attributes':attr,'parameter':parameter,'medium':medium,'variable':var,'aggregation_type':agg,'time_semantics':'canonical t unshifted; interval support not established',
       'native_unit_name':unit,'unit_normalization':normalization,'depth_cm':depth,'orientation':attr.get('orientation',attr.get('Orientation')),'public_level':meta.get('public_level',meta.get('access_levels_resolved',{}).get('public_level')),'source_is_hidden':meta.get('is_hidden'),'source_is_geo_protected':meta.get('is_geo_protected',False),
       'cadence_seconds':None,'unresolved_reasons':reason,'processing_eligible':not reason and parameter in ('soil_moisture','soil_temperature'),
       'precipitation_semantics':('reported_sum_interval_unverified' if agg=='Sum' else 'rate' if agg=='Average' and unit in ('MillimeterPerHour','InchPerHour') else 'cumulative_counter' if agg=='Cumulative' else 'unknown') if parameter=='precipitation' else None,
       'sensor_label':f'{depth:g} cm' if depth is not None else 'Depth unspecified','station_name':station.get('name')}
    # Configuration only when explicitly present in source config; no orientation inference.
    for cfg in meta.get('datapoints_config',[]) if isinstance(meta.get('datapoints_config'),list) else []:
        if isinstance(cfg,dict) and isinstance(cfg.get('interval'),(int,float)):r['cadence_seconds']=cfg['interval']/1000
    return r

def vocabulary_terms(value):
    found={}
    def visit(x):
        if isinstance(x,dict):
            if isinstance(x.get('label'),str):found[x['label']]=x
            for v in x.values():visit(v)
        elif isinstance(x,list):
            for v in x:visit(v)
    visit(value);return found

def discover(args):
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True);c=load(args.catalog);client=BudgetClient(args.ledger,args.budget);records=[];stations=[];errors=[]
    try:
        vocab=client.metadata('vocabularies/dt-unit');dump(out/'unit_vocabulary.json',vocab);units=vocabulary_terms(vocab)
        selected=c['stations'];primary=next(s for s in selected if s['station_id']==args.station)
        ordered=[primary]+[s for s in selected if s is not primary][:3]
        for saved in ordered:
            ident=check_id(saved['station_id']);station=client.metadata('stations/'+ident)
            if station.get('_id')!=ident or not public(station): raise ValueError('Station public identity mismatch')
            # Retain protected restriction but never persist a protected coordinate.
            if station.get('is_geo_protected'):station.pop('geo',None);station.pop('geometry',None)
            dump(out/(ident+'-station.json'),station);stations.append(station)
            payload=client.metadata('datastreams',{'station_id':ident,'$limit':500,'$sort[_id]':1})
            if not isinstance(payload.get('data'),list) or len(payload['data'])>=payload.get('limit',0) or payload.get('total',len(payload['data']))>len(payload['data']):raise ValueError('Incomplete companion inventory; no hidden pagination assumptions')
            # Hidden records are classified in counts only, never copied into exported catalog.
            visible=[m for m in payload['data'] if public(m)]
            dump(out/(ident+'-streams.json'),{'data':visible,'hidden_or_nonpublic_omitted':len(payload['data'])-len(visible)})
            records.extend(classify(m,station,units) for m in visible)
            if any(r['parameter']=='soil_temperature' and r['processing_eligible'] for r in records):break
    except Exception as exc: errors.append({'type':type(exc).__name__,'error':str(exc)})
    result={'status':'verified' if not errors else 'unavailable','checked_at_utc':format_utc(utc_now()),'records':records,'stations':[{'station_id':s['_id'],'name':s.get('name'),'public_level':s.get('public_level',s.get('access_levels_resolved',{}).get('public_level')),'source_is_hidden':s.get('is_hidden'),'source_is_geo_protected':s.get('is_geo_protected')} for s in stations],'errors':errors}
    dump(out/'companion_catalog.json',result)
    print(json.dumps({'status':result['status'],'records':len(records),'temperature_eligible':[r['datastream_id'] for r in records if r['parameter']=='soil_temperature' and r['processing_eligible']],'errors':errors}))
    return 0 if not errors else 2

def collect(args):
    plan=load(args.plan);validate_catalog(plan['catalog'])
    if plan.get('version')=='dendra-coverage-plan-1':
        from coverage_collect import collect_coverage
        sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
        from dendra_coverage import validate_plan
        validate_plan(plan);limits=plan['limits']
        if args.budget!=limits['attempts']:raise ValueError('Plan/transport attempt budget mismatch')
        client=BudgetClient(args.ledger,args.budget,max_elapsed_seconds=limits['elapsed_seconds'],max_response_bytes=limits['response_bytes'],max_source_rows=limits['source_rows'])
        result=collect_coverage(plan,client,args.state,args.output)
        return 0 if result['complete'] else 2
    client=BudgetClient(args.ledger,args.budget);out=Path(args.output);out.mkdir(parents=True,exist_ok=True);store=ChunkStore(args.state);results=[];failures=[]
    for task in plan['intervals']:
        s=task['stream'];sid=check_id(s['datastream_id']);key=task['start']+'_'+task['end']
        # Each run cutoff/interval is frozen; a new reconcile generation is explicit.
        key+='_'+plan['request_generation']
        try:
            e=client.fetcher.fetch_chunk(store,sid,task['start']+'T08:00:00Z',task['end']+'T08:00:00Z',chunk_key=key)
            csvpath=out/(sid+'.csv');write_csv(csvpath,e['rows'],s)
            results.append({**task,'native_csv':str(csvpath.resolve()),'native_sha256':sha(csvpath),'chunks':[{k:e[k] for k in ('content_sha256','requested_interval','retrieval_last_utc','latest_observation_utc')}],'native_row_count':len(e['rows']),'cache_hit':e['cache_hit']})
        except Exception as exc: failures.append({'datastream_id':sid,'error':str(exc),'details':getattr(exc,'details',{})})
    dump(out/'native_manifest.json',{'streams':results,'catalog':plan['catalog'],'failures':failures,'complete':not failures})
    return 2 if failures else 0

def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='mode',required=True)
    q=sub.add_parser('export-checkpoint');q.add_argument('--catalog',required=True);q.add_argument('--checkpoint',required=True);q.add_argument('--output',required=True)
    q=sub.add_parser('discover');q.add_argument('--catalog',required=True);q.add_argument('--station',required=True);q.add_argument('--output',required=True);q.add_argument('--ledger',required=True);q.add_argument('--budget',type=int,default=80)
    q=sub.add_parser('collect');q.add_argument('--plan',required=True);q.add_argument('--state',required=True);q.add_argument('--output',required=True);q.add_argument('--ledger',required=True);q.add_argument('--budget',type=int,default=80)
    args=p.parse_args();return {'export-checkpoint':export_checkpoint,'discover':discover,'collect':collect}[args.mode](args) or 0
if __name__=='__main__':sys.exit(main())
