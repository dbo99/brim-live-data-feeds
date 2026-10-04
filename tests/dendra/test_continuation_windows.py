"""Explicit same-job windows through R; permanently synthetic, socket denied."""
import copy
from contextlib import redirect_stdout
from datetime import timedelta
import fcntl
import io
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import test_local_job as helpers
import test_reviewed_job_scope as scoped
from dendra.history_acquisition import local_job as l
from dendra.history_acquisition.safety import digest, sha
from dendra.transport import parse_utc, format_utc


class ContinuationWindowTests(unittest.TestCase):
    synthetic=helpers.RJobTests.synthetic
    write=helpers.RJobTests.write
    save=helpers.RJobTests.save
    r=helpers.RJobTests.r
    prepare_review=scoped.ReviewedScopeTests.prepare_review

    def setUp(self):
        helpers.RJobTests.setUp(self)
        scope=dict(start=helpers.START,end='2025-11-01T08:00:00.000Z')
        self.c.update(version=l.STREAM_SCOPED_VERSION,scope=scope,stream_scopes={helpers.SID:scope})
        self.fixture['now']=scoped.NOW
        self.fixture['stop_after_task']=1
        second=format_utc(parse_utc(helpers.START)+timedelta(days=30))
        self.fixture['history'][second]=dict(data=[],limit=2016)

    def ready(self):
        self.prepare_review()
        for item in self.authority['streams'].values():
            item['native_review']['expires_at']=format_utc(parse_utc(scoped.NOW)+timedelta(hours=20))
        self.write(self.dir/'review.json',self.authority)
        self.r('review','--review',self.dir/'review.json')
        out=self.r('acquire');self.assertEqual(out['accounting']['sealed'],1)
        self.before=out['accounting'];self.root=Path(self.c['root'])

    def expired(self):
        self.review_time=format_utc(parse_utc(scoped.NOW)+timedelta(hours=3))

    def preserved(self):
        paths=[self.root/n for n in ('window.json','job.json','review.json','catalog.json','plan.json',
                                     'plan-binding.json','scope-binding.json','asset-map.json','metadata.json')]
        paths += [p for p in (self.root/'history/0').rglob('*') if p.is_file()]
        return {str(p):sha(p.read_bytes()) for p in paths}

    def test_expired_window_opens_without_accounting_reset_then_only_remaining_runs(self):
        self.ready();self.expired();before=self.preserved()
        out=self.r('continue-window');self.assertEqual(out['remaining_tasks'],1)
        window=out['window'];self.assertEqual(window['first_attempt_at'],None)
        self.assertEqual(window['remaining_tasks'],[1]);self.assertEqual(window['accounting_before'],self.before)
        self.assertEqual({k:out['accounting'][k] for k in self.before},self.before)
        self.assertEqual(self.preserved(),before)
        self.assertEqual(self.r('status')['accounting'],out['accounting'])
        self.assertEqual(self.r('validate-scope')['stream_scopes'],self.c['stream_scopes'])
        self.r('continue-window',code=2)  # An active continuation cannot be renewed.
        done=self.r('resume');self.assertEqual(done['outcome'],'COMPLETE_FOR_DECLARED_SCOPE')
        a=done['accounting'];self.assertEqual(a['sealed'],2);self.assertEqual(a['attempts'],self.before['attempts']+1)
        self.assertEqual(a['metadata_attempts'],4);self.assertEqual(a['window'],self.before['window'])
        self.assertIsNotNone(a['continuation_windows'][0]['first_attempt_at'])
        self.assertEqual(self.preserved(),before)
        self.assertEqual(self.r('resume')['accounting'],a)
        self.review_time=format_utc(parse_utc(scoped.NOW)+timedelta(hours=6))
        self.assertIn('No eligible',self.r('continue-window',code=2)['reason'])
        self.assertEqual(self.r('status')['accounting'],a)

    def test_refuses_active_original_writer_expired_authority_and_budget_exhaustion(self):
        self.ready();self.assertIn('still active',self.r('continue-window',code=2)['reason'])
        self.expired()
        with (self.root/'writer.lock').open('rb') as f:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertIn('writer',self.r('continue-window',code=2)['reason'])
        late=format_utc(parse_utc(scoped.NOW)+timedelta(hours=21))
        self.r('continue-window','--offline-now',late,code=2)
        # Exercise cumulative budget admission against independently supplied
        # accounting, without fabricating or changing any original Journal.
        job=l.Job(self.c,self.inv,fixture=self.fixture,clock=l.Clock(self.fixture,self.review_time))
        job.config_path=self.dir/'config.json';job.continuing=True
        with job.open():
            for field in ('attempts','bytes'):
                a=copy.deepcopy(self.before);a[field]=self.c['limits'][field]
                with self.subTest(field=field),patch.object(job,'accounting',return_value=a):
                    with self.assertRaisesRegex(l.Hold,'budget exhausted'):job.continue_window()
        self.assertFalse((self.root/'continuation-windows').exists())
        self.assertEqual(self.r('status')['accounting'],self.before)

    def test_scope_hash_and_original_window_mutations_refused(self):
        self.ready();self.expired()
        for name in ('config.json','review.json'):
            path=self.dir/name;body=path.read_bytes();v=l.read(path)
            if name=='config.json':v['stream_scopes'][helpers.SID]['start']='2025-10-02T08:00:00.000Z'
            else:v['streams'][helpers.SID]['native_review']['packet_sha256']='0'*64
            self.write(path,v);self.r('continue-window',code=2);path.write_bytes(body)
        p=self.root/'window.json';body=p.read_bytes();v=l.read(p)
        p.unlink()
        self.assertIn('Original window record required',self.r('continue-window',code=2)['reason'])
        self.assertFalse(p.exists());p.write_bytes(body)
        v['deadline']='2026-10-03T13:00:00.000Z';self.write(p,v)
        self.r('continue-window',code=2);p.write_bytes(body)
        self.r('continue-window')
        for p in (self.root/'window.json',self.root/'continuation-windows/0001/opened.json'):
            body=p.read_bytes();v=l.read(p);v['deadline']='2026-10-03T23:00:00.000Z';self.write(p,v)
            for mode in ('validate-scope','resume','continue-window'):self.r(mode,code=2)
            p.write_bytes(body)
        self.assertEqual({k:self.r('status')['accounting'][k] for k in self.before},self.before)

    def test_changed_collector_policy_cannot_use_supervisor_compatibility(self):
        self.ready()
        changed=l.source_binding();changed['history_acquisition/campaign_execution.py']='0'*64
        with patch.object(l,'source_binding',return_value=changed):
            with self.assertRaisesRegex(l.Hold,'identical parser/Journal/transport/science'):
                l.continuation_sources(self.c)
        self.assertFalse((self.root/'continuation-windows').exists())

    def test_spent_unsealed_never_retried_by_new_window(self):
        self.fixture.pop('stop_after_task');self.fixture['history'][helpers.START]=dict(status=503,body={})
        self.prepare_review()
        self.authority['streams'][helpers.SID]['native_review']['expires_at']=format_utc(parse_utc(scoped.NOW)+timedelta(hours=20))
        self.write(self.dir/'review.json',self.authority);self.r('review','--review',self.dir/'review.json')
        # prepare_review supplies the first successful response; use a dispatch
        # failure in the second task so the first seal is still preserved.
        job=l.Job(self.c,self.inv,fixture=self.fixture,clock=l.Clock(self.fixture,self.review_time))
        job.config_path=self.dir/'config.json'
        original=job.dispatch;calls=[]
        def failing(request,timeout):
            if 'time%5B%24gte%5D=2025-10-31' in request.full_url:
                calls.append(request.full_url);return l.Reply({},503)
            return original(request,timeout)
        with job.open(),patch.object(job,'dispatch',side_effect=failing):
            with self.assertRaises(l.Hold):job.acquire()
        self.assertEqual(len(calls),1)
        before=self.r('status')['accounting'];self.assertTrue(before['spent_unsealed']);self.expired()
        self.assertIn('Spent unsealed',self.r('continue-window',code=2)['reason'])
        self.assertEqual(self.r('status')['accounting'],before)
        self.assertFalse((Path(self.c['root'])/'continuation-windows').exists())

    def test_prior_committed_supervisor_same_job_fresh_r_continuation(self):
        # Build synthetic capture using the genuine predecessor fingerprint.
        # No checkout file is replaced. The positive historical-checkpoint
        # case exists only when every other collector file matches HEAD.
        path=l.REPO/'scripts/dendra/history_acquisition/local_job.py'
        old=subprocess.check_output(['git','show','HEAD:scripts/dendra/history_acquisition/local_job.py'],cwd=l.REPO)
        real_read=Path.read_bytes
        def capture_bytes(p):return old if p==path else real_read(p)
        def in_process(mode,*args,code=0):
            argv=[mode,str(self.dir/'config.json'),*map(str,args)]
            if hasattr(self,'review_time'):argv+=['--offline-now',self.review_time]
            output=io.StringIO()
            with redirect_stdout(output):rc=l.main(argv)
            self.assertEqual(rc,code,output.getvalue());return l.decode(output.getvalue().encode())
        with patch.object(Path,'read_bytes',capture_bytes):
            self.setUp();self.r=in_process;self.ready()
        del self.r
        if self.c['sources']['collector']==l.sources()['collector']:
            self.skipTest('No supervisor-only source change exists at this checkpoint')
        oldbytes=self.preserved();self.expired()
        committed_paths=subprocess.check_output(['git','ls-tree','-r','--name-only','HEAD','--',
            'scripts/dendra'],cwd=l.REPO,text=True).splitlines()
        committed={p.removeprefix('scripts/dendra/') for p in committed_paths if
            p.startswith('scripts/dendra/history_acquisition/') and p.endswith('.py') or
            p in ('scripts/dendra/transport.py','scripts/dendra/core.R')}
        current=l.source_binding()
        closure_matches=(set(current)==committed and all(
            k=='history_acquisition/local_job.py' or sha(subprocess.check_output(
                ['git','show','HEAD:scripts/dendra/'+k],cwd=l.REPO))==v for k,v in current.items()))
        if not closure_matches:
            out=self.r('continue-window',code=2)
            self.assertIn('Historical collector',out['reason'])
            self.assertFalse((self.root/'continuation-windows').exists())
            self.assertEqual(self.preserved(),oldbytes)
            return
        out=self.r('continue-window')
        self.assertEqual(out['window']['job_id'],digest(self.c))
        self.assertEqual(self.preserved(),oldbytes)
        self.r('validate-scope');done=self.r('resume')
        self.assertEqual(done['outcome'],'COMPLETE_FOR_DECLARED_SCOPE')
        self.assertEqual(done['accounting']['attempts'],self.before['attempts']+1)
        self.assertEqual(self.preserved(),oldbytes)
        self.assertEqual(l.read(self.root/'review.json'),self.authority)
        child=l.read(self.root/'history/1/prepared.json')
        self.assertEqual(digest(child['binding']['collector_sources']),l.sources()['collector'])


if __name__=='__main__':unittest.main()
