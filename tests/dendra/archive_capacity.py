#!/usr/bin/env python3
"""Deterministic SYNTHETIC 434-stream/ten-WY layout, never observed coverage.
Only capacity/shape validation; R scientific regression is a separate gate.
"""
import argparse,copy,json,resource,shutil,sys,time,platform
from pathlib import Path
from datetime import date,timedelta
from archive_fixture import dc
import dendra_archive as da

def run(workspace,root):
    w=Path(workspace).resolve();root=Path(root).resolve();assert not root.exists();root.mkdir(parents=True);start=time.monotonic()
    seed=dc.load(w/'work/sm2a/state/seed.json');candidate=w/'work/sm2a/state'/seed['candidate_relpath'];idx=da.open_index(candidate);s=next(s for s in idx['streams'] if s['datastream_id']=='63531a67a9b61453fa1ca4ed');_,p=da.stream_product(candidate,s);template=copy.deepcopy(next(r for r in p['rows'] if r['plot_eligible'] and not r['flags']))
    begin=date(2016,10,1);end=date(2026,9,20);rows=[];query={};now='2026-09-21T12:00:00Z';sha='0'*64
    for offset in range((end-begin).days):
        day=begin+timedelta(days=offset);wy=day.year+(day.month>=10);r=copy.deepcopy(template);r.update(date=day.isoformat(),water_year=wy,dowy=(day-date(wy-1,10,1)).days+1,water_day=(day-date(wy-1,10,1)).days+1,water_day_aligned=(date(1999 if day.month>=10 else 2000,day.month,day.day)-date(1999,10,1)).days+1,water_year_days=(date(wy,10,1)-date(wy-1,10,1)).days);rows.append(r);query[r['date']]={'status':'queried','retrieved_at_utc':now,'content_sha256':sha,'latest_observation_utc':(day+timedelta(days=1)).isoformat()+'T07:00:00Z'}
    generation=root/'generation';generation.mkdir();index={k:v for k,v in idx.items() if k not in ('limits','selection_ids','catalogs','file_pages','file_count','stream_count','station_count','streams','stations')};index.update(generation='20260921T120000-434',generated_at_utc=now,run_started_at_utc=now,mode='replay',parent_generation=None,publication_time_utc=None,scope='SYNTHETIC CAPACITY ONLY, not observed station coverage',lineage={'kind':'imported_seed','state_parent_generation':None,'seed_sha256':sha,'seed_generation':'20260921T120000-434','selection_change':None},streams=[],stations=[])
    for n in range(122):
        st=copy.deepcopy(idx['stations'][0]);st.update(station_id=f'{0xf000+n:024x}',name=f'SYNTHETIC capacity station {n}',source_name=f'SYNTHETIC capacity station {n}',selected_stream_ids=[],selected_stream_count=0);index['stations'].append(st)
    for n in range(434):
        sid=f'{0x1000+n:024x}';st=index['stations'][n%122];st['selected_stream_ids'].append(sid);st['selected_stream_count']+=1;stream=copy.deepcopy(p['stream']);depth=[5,20,60,100][n%4];stream.update(datastream_id=sid,station_id=st['station_id'],source_name=f'SYNTHETIC capacity stream {n}',depth_cm=depth,orientation='horizontal',sensor_label=f'SYNTHETIC {depth} cm');stream['source_attributes']={'depth':{'unit_tag':'dt_Unit_Centimeter','value':depth},'orientation':'horizontal'}
        product=copy.deepcopy({k:v for k,v in p.items() if k not in ('rows','source_snapshot')});product.update(stream=stream,rows=rows,generated_at_utc=now,source_snapshot={'native_row_count':len(rows)*24,'chunks':[{'requested_interval':{'start_inclusive':begin.isoformat()+'T08:00:00Z','end_exclusive':end.isoformat()+'T08:00:00Z'},'content_sha256':sha,'retrieval_last_utc':now,'latest_observation_utc':'2026-09-20T07:00:00Z'}],'query_by_date':query});product['aggregation'].update(start_date=begin.isoformat(),complete_through_date='2026-09-19',end_date_exclusive=end.isoformat())
        summary=copy.deepcopy(s);summary.pop('manifest');summary.update(stream);summary.update(processed_at_utc=now,last_retrieved_at_utc=now,current_interval_retrieved_at_utc=now,latest_observation_utc='2026-09-20T07:00:00Z',recent_rows=[dc.projected(r) for r in rows[-60:]],latest_accepted=dc.projected(rows[-1]));summary['summary']['calendar_row_count']=len(rows);summary['summary']['eligible_row_count']=len(rows)
        index['streams'].append(summary);dc.write(generation/'daily'/f'{sid}.json',product)
    dc.write(generation/'index.json',index);out=root/'candidate';out.mkdir();da.write_layout(generation,out);layout_seconds=time.monotonic()-start
    validation=da.validate(out,semantic=False);raw=dc.load(out/dc.FIXED[0]);files=ds_inventory(out)
    for field,value in [('file_count',20001),('stream_count',1025)]:
        bad=copy.deepcopy(raw);bad[field]=value;dc.write(out/dc.FIXED[0],bad)
        try:da.open_index(out)
        except ValueError:pass
        else:raise AssertionError('Capacity bound accepted '+field)
    dc.write(out/dc.FIXED[0],raw)
    # Common-source sharding uses the actual writer and full catalog semantics;
    # observations remain explicitly synthetic and separate from the real preview.
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/soil_moisture'));from common_index import emit,expand
    common=dc.load(w/'work/sm2a/data/dendra.json');base=copy.deepcopy(common['stations'][0]);sensor={**common['sensor_defaults'],**base['sensors'][0]};common.update(generation=index['generation'],note='SYNTHETIC capacity only',sensor_defaults={},stations=[],counts={'stations':122,'sensors':434,'imported_histories':434})
    for station in index['stations']:
        st=copy.deepcopy(base);st.update(id=station['station_id'],key='dendra:'+station['station_id'],name=station['name'],source_generation=index['generation'],coordinates=station['geometry']['coordinates'],sensors=[],primary_sensor=station['selected_stream_ids'][0])
        for native in index['streams']:
            if native['station_id']!=station['station_id']:continue
            z=copy.deepcopy(sensor);z.update(id=native['datastream_id'],depth_native=native['depth_cm'],depth_unit='cm',depth_mm=native['depth_cm']*10,orientation=native['orientation'],history={'available':True,'reader':'SYNTHETIC'},observation={'value':20,'date':'2026-09-19','eligible':True,'statistic':'SYNTHETIC shape only','sample_count':144,'coverage':1,'qc':[]},change=copy.deepcopy(native['change']),capabilities={'latest_vwc':True,'recent_change':True,'source_context':False,'common_reference':False,'companion':False},unit='percent VWC',unit_evidence='SYNTHETIC capacity only');st['sensors'].append(z)
        common['stations'].append(st)
    commonout=root/'common';commonout.mkdir();emit(commonout,'dendra',common);ci=dc.load(commonout/'dendra.json');assert ci['schema']=='brim-soil-moisture-2';expanded=expand(ci,lambda d:(commonout/d['path']).read_bytes());assert expanded['stations']==common['stations']
    report={'status':'passed','scope':'SYNTHETIC 434 moisture streams x 10 WY, 122 synthetic stations; shape/capacity, NOT observed coverage or R reaggregation','calendar_rows':434*len(rows),'water_years':10,'layout_seconds':layout_seconds,'total_seconds':time.monotonic()-start,'peak_process_memory_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if platform.system()=='Darwin' else 1024),'platform':platform.platform(),'files':len(files),'total_file_bytes':sum(f['bytes'] for f in files),'root_index_bytes':(out/dc.FIXED[0]).stat().st_size,'catalog_shards':len(raw['catalogs']),'max_catalog_bytes':max(d['bytes'] for d in raw['catalogs']),'file_pages':len(raw['file_pages']),'max_partition_bytes':max(f['bytes'] for f in files if '/diagnostics/' in f['path'] and f['path'].endswith('.json')),'validation':validation,'common_shards':len(ci['shards']),'common_index_bytes':(commonout/'dendra.json').stat().st_size,'rejected_boundaries':['20001 files','1025 streams'],'provider_requests':0}
    dc.write(root/'report.json',report);print(json.dumps(report));return report

def ds_inventory(root):return [{'path':p.relative_to(root).as_posix(),'bytes':p.stat().st_size} for p in root.rglob('*') if p.is_file()]
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--workspace',required=True);p.add_argument('--root',required=True);a=p.parse_args();run(a.workspace,a.root)
