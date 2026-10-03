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
            result=l.read(Path(self.c['root'])/'metadata.json')[sid];packet=result['metadata']['packet']
            nr=eligibility.propose(self.inv,encode(packet),packet_sha256=digest(packet),
                packet_source_fingerprint=fp,executor_fingerprint=fp,**self.c['scope'])
            nr.update(disposition='ACCEPT_NATIVE',reviewer_ref='SYNTHETIC_ONLY',reviewed_at=self.review_time,
                expires_at=format_utc(parse_utc(NOW)+timedelta(hours=1)),acknowledgements=eligibility.ACKNOWLEDGEMENTS)
            sr=dict(rule=aw.REVIEW,disposition='ACCEPT_SOURCE_START',reviewer_ref='SYNTHETIC_ONLY',
                reviewed_at=self.review_time,evidence_sha256=result['witness']['evidence_sha256'])
            place=dict(disposition='ACCEPT_PLACEMENT',reviewer_ref='SYNTHETIC_ONLY',
                station_metadata_sha256=packet['access_evidence']['station_metadata_sha256'],
                configuration_evidence_sha256=digest(packet['configuration_evidence']),
                depth_cm=self.inv.identity(sid)['depth_cm'],crs='EPSG:4326',
                timestamp_meaning='UTC t; preserve native timestamps',scope=self.c['scope'],
                evidence=[dict(path=str(self.dir/'fixture.json'),sha256=self.c['fixture']['sha256'])])
            streams[sid]=dict(scope=self.c['scope'],native_review=nr,source_review=sr,placement_review=place)
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


if __name__=='__main__':unittest.main()
