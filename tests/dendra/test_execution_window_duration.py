"""Bound durations through fresh R processes; synthetic, network denied."""
from datetime import timedelta
from pathlib import Path
import unittest

import test_local_job as helpers
import test_reviewed_job_scope as scoped
import test_continuation_windows as continuation
from dendra.history_acquisition import local_job as l
from dendra.history_acquisition.safety import digest
from dendra.transport import parse_utc, format_utc


class ExecutionWindowDurationTests(unittest.TestCase):
    synthetic=helpers.RJobTests.synthetic
    write=helpers.RJobTests.write
    save=helpers.RJobTests.save
    r=helpers.RJobTests.r
    setUp=continuation.ContinuationWindowTests.setUp
    prepare_review=scoped.ReviewedScopeTests.prepare_review
    ready=continuation.ContinuationWindowTests.ready
    preserved=continuation.ContinuationWindowTests.preserved

    def duration(self, window, start='first_attempt_at'):
        return (parse_utc(window['deadline'])-parse_utc(window[start])).total_seconds()

    def test_omitted_field_preserves_default_and_stricter_old_limits(self):
        for seconds in (7200,60):
            with self.subTest(seconds=seconds):
                self.setUp();self.c['limits']=dict(l.LIMITS,seconds=seconds)
                self.ready();self.assertNotIn('execution_window_seconds',self.c)
                self.assertEqual(self.duration(self.before['window']),seconds)
                self.assertEqual(self.r('status')['accounting'],self.before)

    def test_extended_first_and_continuation_windows_fresh_r(self):
        self.c['execution_window_seconds']=12600;self.ready()
        self.assertEqual(self.duration(self.before['window']),12600)
        self.assertEqual(self.r('inspect')['configuration']['execution_window_seconds'],12600)
        self.review_time=format_utc(parse_utc(scoped.NOW)+timedelta(hours=3))
        self.assertIn('still active',self.r('continue-window',code=2)['reason'])
        self.assertEqual(self.r('status')['accounting'],self.before)
        self.review_time=format_utc(parse_utc(scoped.NOW)+timedelta(hours=4))
        self.r('resume',code=2)
        self.assertEqual(self.r('status')['accounting'],self.before)
        before=self.preserved();out=self.r('continue-window')
        self.assertEqual(self.duration(out['window'],'opened_at'),12600)
        self.assertEqual(out['window']['accounting_before'],self.before)
        self.assertEqual(self.preserved(),before)
        self.assertEqual(self.r('status')['accounting'],out['accounting'])
        self.r('validate-scope');done=self.r('resume')
        self.assertEqual(done['outcome'],'COMPLETE_FOR_DECLARED_SCOPE')
        self.assertEqual(done['accounting']['attempts'],self.before['attempts']+1)
        self.assertEqual(done['accounting']['window'],self.before['window'])
        self.assertEqual(self.preserved(),before)
        self.assertEqual(self.r('resume')['accounting'],done['accounting'])

    def test_invalid_and_unsupported_durations_refused_before_initialization(self):
        for value in (None,True,False,0,-1,7199,12601,999999,12600.0,'12600',[],{}):
            with self.subTest(value=value):
                self.c['execution_window_seconds']=value;self.save()
                with self.assertRaises(l.Hold):l.config(self.dir/'config.json')
                self.assertFalse(Path(self.c['root']).exists())
        self.c['execution_window_seconds']=12600
        self.c['limits']=dict(l.LIMITS,seconds=60);self.save()
        with self.assertRaises(l.Hold):l.config(self.dir/'config.json')
        self.c['limits']=dict(l.LIMITS)
        for version in (l.VERSION,l.IMPORT_VERSION):
            with self.subTest(version=version):
                self.c['version']=version;self.c.pop('stream_scopes',None)
                if version==l.IMPORT_VERSION:self.c['authority_import']={}
                self.save()
                with self.assertRaises(l.Hold):l.config(self.dir/'config.json')

    def test_duration_change_after_prepare_or_review_cannot_rebind_job(self):
        self.c['execution_window_seconds']=12600;self.save();self.r('prepare')
        path=self.dir/'config.json';original=path.read_bytes()
        changed=dict(self.c,execution_window_seconds=7200);self.write(path,changed)
        self.assertIn('Same-job',self.r('status',code=2)['reason'])
        path.write_bytes(original)
        # A separate ready fixture exercises the immutable post-review binding.
        self.setUp();self.c['execution_window_seconds']=12600;self.ready()
        path=self.dir/'config.json';original=path.read_bytes();before=self.preserved()
        changed=dict(self.c,execution_window_seconds=7200);self.write(path,changed)
        self.r('validate-scope',code=2);self.r('resume',code=2)
        saved=l.read(self.root/'job.json');body=(self.root/'job.json').read_bytes()
        saved.update(configuration=changed,job_id=digest(changed));self.write(self.root/'job.json',saved)
        self.r('validate-scope',code=2)  # Review and scope still bind the old config.
        (self.root/'job.json').write_bytes(body);path.write_bytes(original)
        self.assertEqual(self.preserved(),before)
        self.assertEqual(self.r('status')['accounting'],self.before)

    def test_serialized_window_cannot_disagree_with_bound_duration(self):
        self.c['execution_window_seconds']=12600;self.ready()
        path=self.root/'window.json';body=path.read_bytes();window=l.read(path)
        window['deadline']=format_utc(parse_utc(window['first_attempt_at'])+timedelta(seconds=7200))
        self.write(path,window);self.r('status',code=2);self.r('resume',code=2)
        path.write_bytes(body);self.assertEqual(self.r('status')['accounting'],self.before)


if __name__=='__main__':unittest.main()
