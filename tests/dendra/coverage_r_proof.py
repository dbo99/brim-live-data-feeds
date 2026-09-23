#!/usr/bin/env python3
"""Focused three-stream actual R, transport, validator and state proof; no HTTP."""
import argparse,json
from pathlib import Path
from datetime import datetime,timedelta,timezone
from coverage_fixture import CoverageFixture,dc
import dendra_archive as da
import dendra_state as ds

def run(root):
    root=Path(root).resolve();assert not root.exists();root.mkdir(parents=True);checks=[]
    now=datetime.now(timezone.utc).replace(microsecond=0)-timedelta(minutes=5)
    def record(name,**kw):checks.append(dict(scenario=name,passed=True,**kw));print(name,flush=True)
    for gap in (12,45):
        initial=now-timedelta(days=1 if gap==45 else 0)
        f=CoverageFixture(root/f'missed-{gap}',initial-timedelta(days=gap));f.bootstrap();state=f.root/'state';seed=(state/'seed.json').read_bytes();current=(state/'current.json').read_bytes();prior=ds.candidate_at(state,dc.load(state/'seed.json'));f.now=initial
        if gap==45:
            f.coverage_update(expect=2,limits={'new_intervals':1});partial=dc.load(f.last_run/'native/collection-result.json');assert partial['completed_intervals']==1 and partial['backlog_intervals']==4;assert (state/'seed.json').read_bytes()==seed and (state/'current.json').read_bytes()==current
            record('over-30-day-gap-bounded-run-holds-whole-candidate',collection=partial)
        f.now=now
        p=f.coverage_update();candidate=ds.candidate_at(state,p);idx=da.open_index(candidate);plan=dc.load(f.last_run/'plan.json');collection=dc.load(f.last_run/'native/collection-result.json')
        assert (state/'seed.json').read_bytes()==seed and not (state/'published.json').exists();assert idx['lineage']['state_parent_generation']==dc.load(state/'seed.json')['generation']
        rows=0
        for stream in idx['streams']:
            _,product=da.stream_product(candidate,stream);coverage=next(x for x in plan['stream_coverage'] if x['datastream_id']==stream['datastream_id']);q=product['source_snapshot']['query_by_date'];assert all(q[str(datetime.fromisoformat(coverage['window_start']).date()+timedelta(days=i))]['status']=='queried' for i in range(coverage['required_days']))
            oldstream=next(s for s in da.open_index(prior)['streams'] if s['datastream_id']==stream['datastream_id']);_,old=da.stream_product(prior,oldstream);lookup={r['date']:r for r in product['rows']}
            for r in old['rows']:
                if r['date'] in lookup:assert r==lookup[r['date']];rows+=1
        record(f'missed-{gap}-all-required-dates-queried-real-R',intervals=[{k:t[k] for k in ('start','end','task_id')} for t in plan['intervals']],collection=collection,unchanged_scientific_rows=rows,candidate_generation=p['generation'],parent_generation=idx['lineage']['state_parent_generation'])
        if gap==45:
            assert any(x['cache_hit'] for x in collection['completed'])
            old=partial['completed'][0];new=next(x for x in collection['completed'] if x['task_id']==old['task_id']);assert new['cache_hit'] and new['chunks']==old['chunks']
            record('next-day-resume-original-retrieval-retained',prior_completed=old,reused=new)
        assert idx['generated_at_utc']>idx['run_started_at_utc']
        record('moving-source-clock-post-collection-build-time',start=idx['run_started_at_utc'],built=idx['generated_at_utc'])
        # A failed optional temperature interval still holds the entire Dendra candidate.
        before=(state/'current.json').read_bytes();f.coverage_update(expect=2,settings={'fail_streams':['3'*24]},request_generation='optional-failure');assert (state/'current.json').read_bytes()==before and (state/'seed.json').read_bytes()==seed
        record(f'optional-temperature-failure-whole-network-hold-{gap}',collection=dc.load(f.last_run/'native/collection-result.json'))
    gap=CoverageFixture(root/'internal-gap-source',now-timedelta(days=12));gap.bootstrap();gap.now=now;end=(now-timedelta(hours=8)).date();start=end-timedelta(days=1);gap.native(start=str(start),end=str(end));ptr=gap.invoke('reconcile',extra={'days':'1','request-generation':'SYNTHETIC-internal-gap'})
    sparse=ds.candidate_at(gap.root/'state',ptr);idx=da.open_index(sparse);f=CoverageFixture(root/'internal-gap-recovery',now);manifest={'version':'dendra-saved-seed-2','mode':'saved','streams':[]}
    for stream in idx['streams']:
        _,prod=da.stream_product(sparse,stream);path=f.root/(stream['datastream_id']+'.seed.json');dc.write(path,prod);manifest['streams'].append(dict(datastream_id=stream['datastream_id'],path=str(path),sha256=dc.sha(path)))
        assert str(end-timedelta(days=8)) not in prod['source_snapshot']['query_by_date']
    dc.write(f.root/'seed.json',manifest);f.invoke('bootstrap',extra={'seed-manifest':str(f.root/'seed.json')});p=f.coverage_update();candidate=ds.candidate_at(f.root/'state',p)
    for stream in da.open_index(candidate)['streams']:
        _,prod=da.stream_product(candidate,stream);assert prod['source_snapshot']['query_by_date'][str(end-timedelta(days=8))]['status']=='queried'
    record('legitimate-internal-absent-day-before-later-data-through-full-R-validation',plan=dc.load(f.last_run/'plan.json')['stream_coverage'])
    # Complete empty recent correction reaches the existing R loss guard, not transport failure.
    f=CoverageFixture(root/'empty-correction',now);f.bootstrap();current=(f.root/'state/current.json').read_bytes();f.coverage_update(expect=2,settings={'empty_streams':['1'*24]});c=dc.load(f.last_run/'native/collection-result.json');loss=dc.load(f.last_run/'loss_assessment.json');assert c['complete'] and loss['hold'];assert (f.root/'state/current.json').read_bytes()==current
    record('complete-empty-success-then-actual-R-loss-hold',collection=c,loss=loss)
    report=dict(status='passed',scope='SYNTHETIC three-stream fixtures; actual R aggregation, coverage/transport, full scientific candidate validation and prepared state; no publication',external_requests=0,checks=checks)
    dc.write(root/'report.json',report);return report
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);a=p.parse_args();run(a.root)
