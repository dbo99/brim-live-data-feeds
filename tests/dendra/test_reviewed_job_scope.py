"""Exact reviewed scopes through ordinary R, with synthetic responses only.

Run with the existing checksum-bound inventory and a private DENDRA_TEST_ROOT.
The OS network-denying test runner must also cover fresh R/Python processes.
"""
import copy
from datetime import timedelta
import os
from pathlib import Path
import subprocess
import unittest
from urllib.request import Request
from urllib.parse import urlencode

import test_local_job as helpers
from dendra.history_acquisition import local_job as l, eligibility, authority_witness as aw
from dendra.history_acquisition.safety import encode, digest, sha, Hold
from dendra.history_acquisition.model import source_binding
from dendra.transport import parse_utc, format_utc

CURRENT=dict(start='2026-10-01T08:00:00.000Z',end='2026-10-03T08:00:00.000Z')
NOW='2026-10-03T12:00:00.000Z'


class ReviewedScopeTests(unittest.TestCase):
    synthetic=helpers.RJobTests.synthetic
    write=helpers.RJobTests.write
    save=helpers.RJobTests.save
    r=helpers.RJobTests.r

    def setUp(self):
        helpers.RJobTests.setUp(self)
        self.c.update(version=l.SCOPED_VERSION,scope=copy.deepcopy(CURRENT),streams=[helpers.SID,helpers.SECOND])
        self.c['attribution']={s:[] for s in self.c['streams']}
        self.fixture=self.synthetic(self.c['streams']);self.fixture['now']=NOW

    def prepare_review(self):
        self.fixture['history'][self.c['scope']['start']]=dict(
            data=[dict(t=self.c['scope']['start'],v=0,datastream_id=helpers.SID)],limit=2016)
        self.save();self.r('prepare');self.r('metadata')
        self.review_time=format_utc(parse_utc(NOW)+timedelta(seconds=10))
        streams={};fp=digest(source_binding())
        for sid in self.c['streams']:
            scope=l.stream_scope(self.c,sid)
            result=l.read(Path(self.c['root'])/'metadata.json')[sid];packet=result['metadata']['packet']
            nr=eligibility.propose(self.inv,encode(packet),packet_sha256=digest(packet),
                packet_source_fingerprint=fp,executor_fingerprint=fp,**scope)
            nr.update(disposition='ACCEPT_NATIVE',reviewer_ref='SYNTHETIC_ONLY',reviewed_at=self.review_time,
                expires_at=format_utc(parse_utc(NOW)+timedelta(hours=1)),acknowledgements=eligibility.ACKNOWLEDGEMENTS)
            sr=dict(rule=aw.REVIEW,disposition='ACCEPT_SOURCE_START',reviewer_ref='SYNTHETIC_ONLY',
                reviewed_at=self.review_time,evidence_sha256=result['witness']['evidence_sha256'])
            place=dict(disposition='ACCEPT_PLACEMENT',reviewer_ref='SYNTHETIC_ONLY',
                station_metadata_sha256=packet['access_evidence']['station_metadata_sha256'],
                configuration_evidence_sha256=digest(packet['configuration_evidence']),
                depth_cm=self.inv.identity(sid)['depth_cm'],crs='EPSG:4326',
                timestamp_meaning='UTC t; preserve native timestamps',scope=scope,
                evidence=[dict(path=str(self.dir/'fixture.json'),sha256=self.c['fixture']['sha256'])])
            streams[sid]=dict(scope=scope,native_review=nr,source_review=sr,placement_review=place)
        self.authority=dict(job_id=digest(self.c),streams=streams)
        self.write(self.dir/'review.json',self.authority)

    def ready(self):
        self.prepare_review();self.r('review','--review',self.dir/'review.json')

    def singleton(self):
        self.c['streams']=[helpers.SID];self.c['attribution']={helpers.SID:[]}
        self.fixture=self.synthetic(self.c['streams']);self.fixture['now']=NOW

    def task_request(self,*,start=None,end=None,sid=helpers.SID):
        q=urlencode({'datastream_id':sid,'$limit':2016,'time[$gte]':start or CURRENT['start'],
                     'time[$lt]':end or CURRENT['end']})
        return Request('https://dendra.example.invalid/v2/datapoints?'+q)

    def test_exact_legacy_wy2026_and_general_scope_pass(self):
        for version in (l.VERSION,l.SCOPED_VERSION):
            with self.subTest(version=version):
                self.setUp();self.c.update(version=version,scope=copy.deepcopy(l.SCOPE))
                self.prepare_review();out=self.r('review','--review',self.dir/'review.json')
                self.assertEqual(out['planned_tasks'],26)
                if version==l.SCOPED_VERSION:
                    self.assertEqual(self.r('validate-scope')['scope'],l.SCOPE)

    def test_current_exact_scope_and_fresh_r_readback(self):
        self.ready();out=self.r('validate-scope')
        self.assertEqual(out['scope'],CURRENT);self.assertEqual(out['streams'],self.c['streams'])
        self.assertEqual(out['planned_tasks'],2)
        binding=l.read(Path(self.c['root'])/'scope-binding.json')
        self.assertEqual(binding['identities'],[self.inv.identity(s) for s in self.c['streams']])
        self.assertEqual(binding['configuration_sha256'],sha((self.dir/'config.json').read_bytes()))
        before=self.r('status')['accounting']
        self.assertEqual(before['attempts'],5)
        self.assertEqual(self.r('inspect')['configuration']['scope'],CURRENT)
        run=subprocess.run(['Rscript','--vanilla',str(l.ENTRY),'--help'],capture_output=True,text=True,timeout=30)
        self.assertEqual(run.returncode,0);self.assertIn('validate-scope',run.stdout)
        self.assertEqual(self.r('status')['accounting'],before)

    def test_review_scope_roster_and_mandatory_acceptance_negatives(self):
        self.prepare_review();before=self.r('status')['accounting']
        cases=[]
        for label,scope in (
            ('earlier_start',dict(CURRENT,start='2026-09-30T08:00:00.000Z')),
            ('later_end',dict(CURRENT,end='2026-10-04T08:00:00.000Z')),
            ('shorter_end',dict(CURRENT,end='2026-10-02T08:00:00.000Z'))):
            r=copy.deepcopy(self.authority)
            for item in r['streams'].values():
                item['scope']=scope;item['native_review']['scope']=scope;item['placement_review']['scope']=scope
            cases.append((label,r))
        r=copy.deepcopy(self.authority);r['streams']['63531a684b24f740d53623e6']=copy.deepcopy(r['streams'][helpers.SID])
        cases.append(('added_stream',r))
        r=copy.deepcopy(self.authority);del r['streams'][helpers.SECOND];cases.append(('removed_stream',r))
        r=copy.deepcopy(self.authority);r['streams'][helpers.SID]['native_review']['disposition']='HOLD'
        cases.append(('held_native',r))
        r=copy.deepcopy(self.authority);r['streams'][helpers.SID]={'disposition':'EXCLUDE'};cases.append(('excluded_stream',r))
        for key in ('native_review','placement_review','source_review'):
            r=copy.deepcopy(self.authority);del r['streams'][helpers.SID][key];cases.append(('absent_'+key,r))
        r=copy.deepcopy(self.authority);r['job_id']='0'*64;cases.append(('wrong_job',r))
        r=copy.deepcopy(self.authority);r['streams'][helpers.SID]['native_review']['packet_sha256']='0'*64
        cases.append(('wrong_metadata_hash',r))
        for label,r in cases:
            with self.subTest(label=label):
                self.write(self.dir/'review.json',r);self.r('review','--review',self.dir/'review.json',code=2)
                self.assertFalse((Path(self.c['root'])/'review.json').exists())
                self.assertEqual(self.r('status')['accounting'],before)
        self.write(self.dir/'review.json',self.authority)
        self.r('acquire',code=2)  # Dates without acceptance grant no permission.
        self.assertFalse((Path(self.c['root'])/'history').exists())

    def test_serialized_date_or_roster_change_fails_acquire_and_resume(self):
        self.ready();before=self.r('status')['accounting'];original=copy.deepcopy(self.c)
        variants=[]
        for scope in (dict(CURRENT,start='2026-09-30T08:00:00.000Z'),
                      dict(CURRENT,end='2026-10-04T08:00:00.000Z'),dict(CURRENT,end='2026-10-02T08:00:00.000Z')):
            c=copy.deepcopy(original);c['scope']=scope;variants.append(c)
        c=copy.deepcopy(original);c['streams']=[helpers.SID];del c['attribution'][helpers.SECOND];variants.append(c)
        c=copy.deepcopy(original);sid='63531a684b24f740d53623e6';c['streams'].append(sid);c['attribution'][sid]=[];variants.append(c)
        for c in variants:
            with self.subTest(scope=c['scope'],streams=c['streams']):
                self.write(self.dir/'config.json',c)
                for mode in ('acquire','resume'):self.r(mode,code=2)
                self.write(self.dir/'config.json',original)
                self.assertEqual(self.r('status')['accounting'],before)
        self.assertFalse((Path(self.c['root'])/'history').exists())

    def test_original_review_hash_and_coherent_plan_tampering_fail(self):
        self.ready();root=Path(self.c['root']);before=self.r('status')['accounting']
        # Equivalent JSON with different original bytes is not the accepted input.
        original=(self.dir/'review.json').read_bytes();(self.dir/'review.json').write_bytes(original+b' ')
        self.assertIn('hash changed',self.r('acquire',code=2)['reason'])
        (self.dir/'review.json').write_bytes(original)
        originals={n:(root/n).read_bytes() for n in ('plan.json','plan-binding.json','job.json')}
        for mutate in ('earlier','later','shorter','added','removed','review_hash','job_identity'):
            with self.subTest(mutate=mutate):
                plan=l.read(root/'plan.json')
                if mutate=='earlier':plan['tasks'][0]['start']='2026-09-30T08:00:00.000Z'
                elif mutate=='later':plan['tasks'][0]['end']='2026-10-04T08:00:00.000Z'
                elif mutate=='shorter':plan['tasks'][0]['end']='2026-10-02T08:00:00.000Z'
                elif mutate=='added':plan['tasks'][0]['stream_id']='63531a684b24f740d53623e6'
                elif mutate=='removed':plan['tasks'].pop()
                elif mutate=='review_hash':plan['review_sha256']='0'*64
                else:
                    job=l.read(root/'job.json');job['job_id']='0'*64;self.write(root/'job.json',job)
                self.write(root/'plan.json',plan);self.write(root/'plan-binding.json',dict(sha256=digest(plan)))
                for mode in ('acquire','resume'):self.r(mode,code=2)
                for n,body in originals.items():(root/n).write_bytes(body)
                self.assertEqual(self.r('status')['accounting'],before)
        self.assertFalse((root/'history').exists())

    def test_expired_authority_and_runtime_dispatch_mismatches(self):
        self.ready();before=self.r('status')['accounting']
        late=format_utc(parse_utc(NOW)+timedelta(hours=1,milliseconds=1))
        for mode in ('acquire','resume','validate-scope'):
            self.r(mode,'--offline-now',late,code=2)
        job=l.Job(self.c,self.inv,fixture=self.fixture,clock=l.Clock(self.fixture,self.review_time))
        job.config_path=self.dir/'config.json'
        with job.open():
            for request in (self.task_request(start='2026-09-30T08:00:00.000Z'),
                            self.task_request(end='2026-10-04T08:00:00.000Z'),
                            self.task_request(end='2026-10-02T08:00:00.000Z'),
                            self.task_request(sid='63531a684b24f740d53623e6')):
                with self.assertRaises(Hold):job.dispatch(request,25)
            changed=copy.deepcopy(self.c);changed['scope']['end']='2026-10-04T08:00:00.000Z'
            self.write(self.dir/'config.json',changed)
            with self.assertRaisesRegex(Hold,'configuration changed'):job.dispatch(self.task_request(),25)
            self.write(self.dir/'config.json',self.c)
            job.clock.value=parse_utc(late)
            with self.assertRaises(Hold):job.dispatch(self.task_request(),25)
        self.assertEqual(self.r('status')['accounting'],before)

    def test_current_acquire_and_resume_preserve_scope_window_and_accounting(self):
        self.singleton();self.ready();first=self.r('acquire')
        self.assertEqual(first['outcome'],'COMPLETE_FOR_DECLARED_SCOPE')
        self.assertEqual(first['accounting']['sealed'],1);self.assertEqual(first['accounting']['attempts'],5)
        resumed=self.r('resume');self.assertEqual(resumed['accounting'],first['accounting'])
        root=Path(self.c['root']);prepared=l.read(root/'history/0/prepared.json')
        task=next(iter(prepared['tasks'].values()))
        self.assertEqual({k:task[k] for k in ('start','end')},CURRENT)
        self.assertEqual(self.r('validate-scope')['scope'],CURRENT)


class PerStreamScopeTests(unittest.TestCase):
    synthetic=helpers.RJobTests.synthetic
    write=helpers.RJobTests.write
    save=helpers.RJobTests.save
    r=helpers.RJobTests.r
    prepare_review=ReviewedScopeTests.prepare_review
    ready=ReviewedScopeTests.ready
    task_request=ReviewedScopeTests.task_request

    def setUp(self):
        helpers.RJobTests.setUp(self)
        self.ids=[helpers.SID,helpers.SECOND,'63531a684b24f740d53623e6']
        starts=['2021-07-09T20:20:00.000Z','2021-10-28T18:40:00.000Z','2023-06-09T20:10:00.000Z']
        scopes={s:dict(start=t,end='2025-10-01T08:00:00.000Z') for s,t in zip(self.ids,starts)}
        self.c.update(version=l.STREAM_SCOPED_VERSION,streams=self.ids,stream_scopes=scopes,
                      scope=dict(start=starts[0],end=scopes[self.ids[0]]['end']))
        self.c['attribution']={s:[] for s in self.ids}
        self.fixture=self.synthetic(self.ids);self.fixture['now']=NOW
        for rows in self.fixture['datastreams'].values():
            for row in rows['data']:row['datapoints_config']=[dict(begins_at=starts[0])]
        for sid in self.ids:self.fixture['witnesses'][sid]['data'][0]['t']=scopes[sid]['start']

    def test_exact_map_planner_and_fresh_r_readback(self):
        self.ready();out=self.r('validate-scope');root=Path(self.c['root'])
        self.assertEqual(out['stream_scopes'],self.c['stream_scopes'])
        self.assertEqual(self.r('inspect')['configuration']['stream_scopes'],self.c['stream_scopes'])
        self.assertEqual(l.read(root/'scope-binding.json')['stream_scopes'],self.c['stream_scopes'])
        tasks=l.read(root/'plan.json')['tasks'];self.assertEqual(len(tasks),129)
        for sid,scope in self.c['stream_scopes'].items():
            selected=[t for t in tasks if t['stream_id']==sid]
            self.assertEqual(selected[0]['start'],scope['start']);self.assertEqual(selected[-1]['end'],scope['end'])
            self.assertTrue(all(scope['start']<=t['start']<t['end']<=scope['end'] for t in selected))
            self.assertTrue(all(a['end']==b['start'] for a,b in zip(selected,selected[1:])))
            self.assertEqual(l.read(root/'catalog.json')['streams'][sid]['scope'],scope)

    def test_unmatched_review_maps_rosters_held_and_hashes(self):
        self.prepare_review();before=self.r('status')['accounting'];cases=[]
        for start in ('2021-07-08T20:20:00.000Z','2021-07-10T20:20:00.000Z'):
            r=copy.deepcopy(self.authority);r['streams'][self.ids[0]]['scope']['start']=start;cases.append(r)
        r=copy.deepcopy(self.authority)
        a,b=self.ids[:2];r['streams'][a]['scope'],r['streams'][b]['scope']=r['streams'][b]['scope'],r['streams'][a]['scope'];cases.append(r)
        r=copy.deepcopy(self.authority);del r['streams'][a];cases.append(r)
        r=copy.deepcopy(self.authority);r['streams']['63531a688f3bc3f3df655be6']=r['streams'][a];cases.append(r)
        for field in ('native_review','placement_review','source_review'):
            r=copy.deepcopy(self.authority);r['streams'][a][field]['disposition']='HOLD';cases.append(r)
        r=copy.deepcopy(self.authority);r['streams'][a]={'disposition':'EXCLUDE'};cases.append(r)
        for field,key in (('native_review','packet_sha256'),('source_review','evidence_sha256'),
                          ('placement_review','configuration_evidence_sha256')):
            r=copy.deepcopy(self.authority);r['streams'][a][field][key]='0'*64;cases.append(r)
        for n,r in enumerate(cases):
            with self.subTest(case=n):
                self.write(self.dir/'review.json',r);self.r('review','--review',self.dir/'review.json',code=2)
                self.assertFalse((Path(self.c['root'])/'review.json').exists())
        self.r('acquire',code=2);self.assertEqual(self.r('status')['accounting'],before)
        self.assertFalse((Path(self.c['root'])/'history').exists())

    def test_config_only_change_and_resume_single_stream_widening(self):
        self.ready();original=copy.deepcopy(self.c);before=self.r('status')['accounting'];cases=[]
        for start in ('2021-10-27T18:40:00.000Z','2021-10-29T18:40:00.000Z'):
            c=copy.deepcopy(original);c['stream_scopes'][self.ids[1]]['start']=start;cases.append(c)
        c=copy.deepcopy(original);c['scope']['end']='2025-10-02T08:00:00.000Z';cases.append(c)
        c=copy.deepcopy(original);a,b=self.ids[:2];c['stream_scopes'][a],c['stream_scopes'][b]=c['stream_scopes'][b],c['stream_scopes'][a];cases.append(c)
        c=copy.deepcopy(original);c['streams']=c['streams'][:-1];cases.append(c)
        c=copy.deepcopy(original);c['streams'].append('63531a688f3bc3f3df655be6');cases.append(c)
        c=copy.deepcopy(original);del c['stream_scopes'][self.ids[1]];cases.append(c)
        for n,c in enumerate(cases):
            with self.subTest(case=n):
                self.write(self.dir/'config.json',c)
                for mode in ('validate-scope','acquire','resume'):self.r(mode,code=2)
        self.write(self.dir/'config.json',original)
        self.assertEqual(self.r('status')['accounting'],before)
        self.assertFalse((Path(self.c['root'])/'history').exists())

    def test_plan_bounds_dispatch_expiry_and_authority_binding(self):
        self.ready();root=Path(self.c['root']);before=self.r('status')['accounting']
        originals={n:(root/n).read_bytes() for n in ('plan.json','plan-binding.json','scope-binding.json')}
        for case in ('before_stream_start','after_stream_end','held_stream','authority_map'):
            with self.subTest(case=case):
                plan=l.read(root/'plan.json');binding=l.read(root/'scope-binding.json')
                task=next(t for t in plan['tasks'] if t['stream_id']==self.ids[1])
                if case=='before_stream_start':task['start']=self.c['scope']['start']
                elif case=='after_stream_end':task['end']='2025-10-02T08:00:00.000Z'
                elif case=='held_stream':task['stream_id']='63531a688f3bc3f3df655be6'
                else:binding['stream_scopes'][self.ids[1]]['start']=self.c['scope']['start']
                self.write(root/'plan.json',plan);self.write(root/'plan-binding.json',dict(sha256=digest(plan)))
                # Also recompute outer plan checksum to exercise the explicit
                # per-stream bounds rather than only checksum mismatch.
                binding['plan_sha256']=digest(plan);self.write(root/'scope-binding.json',binding)
                for mode in ('acquire','resume'):self.r(mode,code=2)
                for n,body in originals.items():(root/n).write_bytes(body)
        late=format_utc(parse_utc(NOW)+timedelta(hours=1,milliseconds=1))
        for mode in ('acquire','resume','validate-scope'):self.r(mode,'--offline-now',late,code=2)
        job=l.Job(self.c,self.inv,fixture=self.fixture,clock=l.Clock(self.fixture,self.review_time))
        job.config_path=self.dir/'config.json'
        with job.open():
            sid=self.ids[1];scope=self.c['stream_scopes'][sid]
            for start,end in ((self.c['scope']['start'],scope['end']),
                              (scope['start'],'2025-10-02T08:00:00.000Z')):
                with self.assertRaises(Hold):job.dispatch(self.task_request(sid=sid,start=start,end=end),25)
            job.clock.value=parse_utc(late)
            with self.assertRaises(Hold):job.dispatch(self.task_request(sid=sid,**scope),25)
        self.assertEqual(self.r('status')['accounting'],before)
        self.assertFalse((root/'history').exists())

    def test_three_stream_acquire_resume_no_repeat(self):
        # Small complete execution complements the 129-task planning fixture.
        for sid,scope in self.c['stream_scopes'].items():
            scope['end']=format_utc(parse_utc(scope['start'])+timedelta(days=1))
            self.fixture['history'][scope['start']]=dict(data=[],limit=2016)
        self.c['scope']['end']=max(s['end'] for s in self.c['stream_scopes'].values())
        self.ready();out=self.r('acquire');self.assertEqual(out['outcome'],'COMPLETE_FOR_DECLARED_SCOPE')
        self.assertEqual(out['accounting']['sealed'],3);self.assertEqual(out['accounting']['attempts'],9)
        self.assertEqual(self.r('resume')['accounting'],out['accounting'])
        self.assertEqual(self.r('validate-scope')['stream_scopes'],self.c['stream_scopes'])


if __name__=='__main__':unittest.main()
