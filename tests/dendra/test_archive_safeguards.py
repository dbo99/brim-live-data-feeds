#!/usr/bin/env python3
"""Version2 correction/failure tests through the actual R orchestration, no HTTP."""
import argparse,copy,csv,json,sys
from pathlib import Path
from datetime import datetime,timedelta,timezone
from archive_fixture import ArchiveFixture,dc
import dendra_archive as da
import dendra_state as ds

def run(root):
    root=Path(root).resolve();assert not root.exists();f=ArchiveFixture(root,datetime(2024,3,2,20,tzinfo=timezone.utc));f.bootstrap();state=f.root/'state';seed=(state/'seed.json').read_bytes();current=(state/'current.json').read_bytes();checks=[]
    def ok(name):checks.append(name);print(name,flush=True)
    i=da.open_index(ds.candidate_at(state,dc.load(state/'seed.json')))
    for s in i['streams']:
        _,p=da.stream_product(ds.candidate_at(state,dc.load(state/'seed.json')),s);row=next(r for r in p['rows'] if r['date']=='2024-02-29');assert row['water_day_aligned']==152 and row['water_year_days']==366
    ok('leap-day-aligned-calendar')
    def empty_first(days):
        end='2024-03-02';start=(datetime.fromisoformat(end)-timedelta(days=days)).date().isoformat();path=f.native(start=start,end=end);m=dc.load(path);x=m['streams'][0];csvpath=Path(x['native_csv']);csvpath.write_text('t,datastream_id,v,value_status,duplicate_conflict,alternative_out_of_range\n');x.update(native_sha256=dc.sha(csvpath),native_row_count=0)
        for c in x['chunks']:c['content_sha256']=x['native_sha256'];c['latest_observation_utc']=None
        dc.write(path,m);return path
    for days in (1,7,14,30):
        path=empty_first(days)
        if days==1:
            m=dc.load(path);x=m['streams'][1];cp=Path(x['native_csv']);cp.write_text('t,datastream_id,v,value_status,duplicate_conflict,alternative_out_of_range\n');x.update(native_sha256=dc.sha(cp),native_row_count=0);x['chunks'][0].update(content_sha256=x['native_sha256'],latest_observation_utc=None);dc.write(path,m)
        f.invoke(extra={'days':str(days)},expect=2);assert (state/'current.json').read_bytes()==current and (state/'seed.json').read_bytes()==seed;ok(f'{days}-day-'+('selection-wide' if days==1 else 'one-stream')+'-mass-loss-held')
    empty_first(7);before=set((state/'runs').iterdir());f.invoke('reconcile',extra={'days':'7','request-generation':'SYNTHETIC-reviewed'},expect=2);new=(set((state/'runs').iterdir())-before).pop();loss=dc.load(new/'loss_assessment.json');review={'version':dc.VALIDATOR_VERSION,'decision':'approve_exact_removal','assessment_sha256':loss['assessment_sha256'],'prior_index_sha256':loss['prior_index_sha256'],'request_generation':loss['request_generation'],'review_id':'SYNTHETIC-review','reviewed_by':'SYNTHETIC scientist','reason':'Synthetic exact source removal regression'};dc.write(root/'loss-review.json',review)
    p=f.invoke('reconcile',extra={'days':'7','request-generation':'SYNTHETIC-reviewed','loss-review':str(root/'loss-review.json')});assert dc.load(ds.candidate_at(state,p)/dc.FIXED[0])['lineage']['state_parent_generation']==i['generation'];ok('exact-reviewed-removal-evidence-path')
    current=(state/'current.json').read_bytes();review['assessment_sha256']='0'*64;dc.write(root/'wrong-review.json',review);f.invoke('reconcile',extra={'days':'7','request-generation':'SYNTHETIC-reviewed','loss-review':str(root/'wrong-review.json')},expect=2);assert (state/'current.json').read_bytes()==current;ok('wrong-review-held')
    f.now=datetime(2024,6,2,20,tzinfo=timezone.utc);empty_first(1) # replace below with a completed new empty date, not a historical loss
    path=f.native(start='2024-06-01',end='2024-06-02');m=dc.load(path);x=m['streams'][0];csvpath=Path(x['native_csv']);csvpath.write_text('t,datastream_id,v,value_status,duplicate_conflict,alternative_out_of_range\n');x.update(native_sha256=dc.sha(csvpath),native_row_count=0);x['chunks'][0].update(content_sha256=x['native_sha256'],latest_observation_utc=None);dc.write(path,m)
    p=f.invoke(extra={'days':'1'});ii=da.open_index(ds.candidate_at(state,p));_,prod=da.stream_product(ds.candidate_at(state,p),ii['streams'][0]);q=prod['source_snapshot']['query_by_date'];assert q['2024-06-01']['status']=='queried' and prod['rows'][-1]['n_total']==0 and '2024-04-01' not in q;ok('known-empty-new-date-distinct-from-long-unqueried-gap')
    current=(state/'current.json').read_bytes();m['complete']=False;m['failures']=['SYNTHETIC one-stream failure'];dc.write(path,m);f.invoke(extra={'days':'1'},expect=2);assert (state/'current.json').read_bytes()==current;ok('partial-fetch-whole-candidate-hold')
    f.catalog['streams'][0]['source_is_hidden']=True;f.invoke(extra={'days':'1'},expect=2);assert (state/'current.json').read_bytes()==current;ok('source-exclusion-held-no-silent-deletion')
    dc.write(root/'report.json',{'status':'passed','scope':'SYNTHETIC transport/clock only','provider_requests':0,'checks':checks})
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);a=p.parse_args();run(a.root)
