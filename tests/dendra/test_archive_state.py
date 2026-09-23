#!/usr/bin/env python3
"""SM2A saved-row parity and explicitly synthetic state/failure/selection regressions."""
import argparse,copy,json,shutil,sys,time
from pathlib import Path
from datetime import datetime,timedelta,timezone
from archive_fixture import ArchiveFixture,ROOT,dc
import dendra_archive as da
import dendra_state as ds

def run(workspace,root):
    w=Path(workspace).resolve();root=Path(root).resolve();assert not root.exists();root.mkdir(parents=True);checks=[]
    def passed(name,**details):checks.append({'scenario':name,'passed':True,**details});print(name,flush=True)
    saved=w/'work/sm2a/state';sp=dc.load(saved/'seed.json');candidate=ds.candidate_at(saved,sp);i=da.open_index(candidate);seed=dc.load(w/'work/sm2a/saved-seed/seed.json')
    for s in i['streams']:
        m,p=da.stream_product(candidate,s);original=dc.load(next(Path(x['path']) for x in seed['streams'] if x['datastream_id']==s['datastream_id']))
        assert p['rows']==original['rows'];assert p['cadence_context']==original['cadence_context'];assert p['source_snapshot']['query_by_date']==original['source_snapshot']['query_by_date']
        assert s['acquired_through_date']==original['rows'][-1]['date']
        if s['acquired_through_date']=='2026-09-18':assert '2026-09-19' not in p['source_snapshot']['query_by_date']
    passed('every-saved-row-flags-calendar-diagnostics-cadence-and-retrieval-exact',rows=10623)
    snapshot=root/'seed-snapshot';ds.export_snapshot(saved,snapshot,'seed');restore=root/'different-directory';ds.restore_snapshot(snapshot,restore);assert dc.load(restore/'seed.json')==sp and not (restore/'published.json').exists()
    passed('portable-seed-is-not-published-and-fresh-directory-restore')
    # Saved archive plus injected synthetic completed observations: not a real new product.
    f=ArchiveFixture(root/'SYNTHETIC_saved_archive_update',datetime(2026,9,21,20,tzinfo=timezone.utc));f.catalog=dc.load(w/'work/sm2a/saved-seed/catalog.json');f.catalog['live_identity_status']='verified'
    f.native(start='2026-09-20',end='2026-09-21');before=ds.inventory(candidate);oldseed=(restore/'seed.json').read_bytes();p=f.invoke(state=restore,extra={'days':'1'});new=ds.candidate_at(restore,p);ni=da.open_index(new)
    assert (restore/'seed.json').read_bytes()==oldseed and not (restore/'published.json').exists();assert ni['parent_generation'] is None and ni['lineage']['state_parent_generation']==i['generation']
    changed=[];unchanged=[]
    for s in i['streams']:
        m,old=da.stream_product(candidate,s);n,nextp=da.stream_product(new,next(v for v in ni['streams'] if v['datastream_id']==s['datastream_id']));lookup={r['date']:r for r in nextp['rows']}
        if s['parameter']=='soil_moisture':
            assert all(lookup[r['date']]==r for r in old['rows']);assert all(nextp['source_snapshot']['query_by_date'][d]==q for d,q in old['source_snapshot']['query_by_date'].items());assert lookup['2026-09-20']['plot_eligible']
            if s['acquired_through_date']=='2026-09-18':assert '2026-09-19' not in lookup
        prior={p['water_year']:p for p in m['partitions']}
        for part in n['partitions']:
            if part['water_year']<2026:assert part==prior[part['water_year']];unchanged.extend(part[k]['path'] for k in ['history','diagnostics','csv'])
    oldfiles={x['path']:x for x in before};newfiles={x['path']:x for x in ds.inventory(new)};changed=[v for k,v in newfiles.items() if oldfiles.get(k)!=v]
    passed('later-synthetic-update-preserves-full-real-seed-unqueried-gap-and-old-retrievals',closed_year_files_unchanged=len(unchanged),changed_files=len(changed),changed_file_bytes=sum(v['bytes'] for v in changed))
    beforecurrent=(restore/'current.json').read_bytes()
    f.invoke(state=restore,failure='source',expect=2,extra={'days':'1'});assert (restore/'current.json').read_bytes()==beforecurrent and (restore/'seed.json').read_bytes()==oldseed
    f.invoke(state=restore,failure='build',expect=2,extra={'days':'1'});assert (restore/'current.json').read_bytes()==beforecurrent
    passed('failed-fetch-or-build-cannot-advance-state')
    # Real runner must choose seed, not previously prepared child.
    retry=f.invoke(state=restore,extra={'days':'1'});assert dc.load(ds.candidate_at(restore,retry)/dc.FIXED[0])['lineage']['state_parent_generation']==sp['generation']
    passed('unacknowledged-prepared-child-never-becomes-next-parent')
    original=copy.deepcopy(f.catalog);f.catalog['streams']=f.catalog['streams'][:-1];f.native(start='2026-09-20',end='2026-09-21');f.invoke(state=restore,expect=2,extra={'days':'1'});f.catalog=original
    passed('unauthorized-selection-removal-held')
    # Independent small synthetic seed for new stream, known empty, rollover and correction cases.
    g=ArchiveFixture(root/'SYNTHETIC_small',datetime(2024,9,30,20,tzinfo=timezone.utc));g.bootstrap();g.now=datetime(2024,10,2,20,tzinfo=timezone.utc);g.native(start='2024-10-01',end='2024-10-02');p=g.invoke(extra={'days':'1'});ii=da.open_index(ds.candidate_at(g.root/'state',p));assert ii['complete_through_date']=='2024-10-01'
    for s in ii['streams']:
        _,v=da.stream_product(ds.candidate_at(g.root/'state',p),s);assert v['rows'][-1]['water_year']==2025 and v['rows'][-1]['dowy']==1
    passed('October-1-rollover-retains-prior-WY')
    newstream=copy.deepcopy(g.catalog['streams'][0]);newstream.update(datastream_id='4'*24,depth_cm=80,sensor_label='SYNTHETIC 80 cm');newstream['source_attributes']['depth']['value']=80;g.catalog['streams'].append(newstream);g.native(start='2024-10-01',end='2024-10-02');g.invoke(expect=2,extra={'days':'1'})
    oldids=['1'*24,'2'*24,'3'*24];import hashlib
    g.catalog['selection_change']={'version':'dendra-selection-add-1','prior_selection_sha256':hashlib.sha256('\n'.join(oldids).encode()).hexdigest(),'additions':['4'*24],'review_id':'SYNTHETIC-review','reason':'Synthetic exact additive selection test'}
    g.invoke(failure='source',expect=2,extra={'days':'1'});p=g.invoke(extra={'days':'1'});assert da.open_index(ds.candidate_at(g.root/'state',p))['stream_count']==4
    passed('reviewed-addition-preserves-old-history-and-failed-addition-holds-whole-candidate')
    # Wrong/future cutoffs fail before collection. No HTTP boundary is used.
    g.invoke(asof='2024-10-04T20:00:00Z',expect=2,extra={'days':'1'});passed('future-cutoff-held')
    # Duplicate imported seed selection fails at bootstrap, before any state activation.
    dup=dc.load(g.root/'seed.json');dup['streams'].append(dup['streams'][0]);dc.write(root/'duplicate-seed.json',dup);g.catalog['streams']=g.catalog['streams'][:3];g.invoke('bootstrap',state=root/'duplicate-state',extra={'seed-manifest':str(root/'duplicate-seed.json')},expect=2);passed('duplicate-bootstrap-rejected')
    report={'status':'passed','provider_requests':0,'scope':'saved parity plus explicitly synthetic clock/transport tests','checks':checks};dc.write(root/'report.json',report);return report
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--workspace',required=True);p.add_argument('--root',required=True);a=p.parse_args();run(a.workspace,a.root)
