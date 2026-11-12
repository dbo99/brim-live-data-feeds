"""Focused first-run grant, actual supervision and workflow integration tests."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
import snotel_first_run as f
from test_snotel_workflow import WorkflowTests, StrictGit, SOURCE, PRIOR
from test_snotel_snapshot import fixture


class GrantTests(unittest.TestCase):
    def setUp(self):
        self.grant = dict(version='snotel-first-run-1',
            approval_id='L11-SNOTEL-SMS-R8-FIRST-REAL-CURRENT-WY-20261112', source_sha='a'*40,
            run_number=1, window_end='2026-11-12T21:30:00Z', catalog_sha256='b'*64,
            roster_sha256='c'*64, limits=dict(f.LIMITS))
        self.env = dict(GITHUB_REPOSITORY=f.REPO, GITHUB_EVENT_NAME='workflow_dispatch',
            GITHUB_REF='refs/heads/main', GITHUB_WORKFLOW_REF=f.REPO+'/'+f.WORKFLOW+'@refs/heads/main',
            GITHUB_RUN_ATTEMPT='1', GITHUB_RUN_NUMBER='1', GITHUB_RUN_ID='123', GITHUB_SHA='a'*40,
            CATALOG_PIN='b'*64, ROSTER_PIN='c'*64, RELEASE_APPROVED='true',
            ACQUIRE='true', PUBLISH='true', BOOTSTRAP='true', WINDOW_END=self.grant['window_end'])
        self.clock = f.utc('2026-11-12T19:30:00Z')

    def auth(self, grant=None, env=None, phase='prepare'):
        e=dict(self.env, SNOTEL_SMS_FIRST_RUN_JSON=json.dumps(grant or self.grant));e.update(env or {})
        return f.authorize(e, phase, self.clock)

    def test_valid_prepare_and_publish(self):
        self.assertEqual(self.auth(), self.grant)
        self.assertEqual(self.auth(phase='publish'), self.grant)

    def test_environment_refusals(self):
        for key, value in dict(GITHUB_REPOSITORY='other/repo',GITHUB_EVENT_NAME='schedule',
                GITHUB_REF='refs/heads/test', GITHUB_WORKFLOW_REF='other', GITHUB_RUN_ATTEMPT='2',
                GITHUB_RUN_ID='', GITHUB_RUN_NUMBER='2', GITHUB_SHA='d'*40, CATALOG_PIN='d'*64,
                ROSTER_PIN='d'*64, RELEASE_APPROVED='false', ACQUIRE='false', PUBLISH='false',
                BOOTSTRAP='false', WINDOW_END='2026-11-12T22:30:00Z').items():
            with self.subTest(key=key), self.assertRaises(ValueError):self.auth(env={key:value})

    def test_grant_schema_types_deadline_and_budgets(self):
        cases=[]
        for key in self.grant:
            bad=copy.deepcopy(self.grant);del bad[key];cases.append(bad)
        for key,value in [('run_number',True),('source_sha','main'),('window_end',f.EXPIRY.replace('15','16',1)),
                          ('window_end','2026-11-12T20:00:00Z'),('approval_id','other')]:
            bad=copy.deepcopy(self.grant);bad[key]=value;cases.append(bad)
        for key in f.LIMITS:
            bad=copy.deepcopy(self.grant);bad['limits'][key]+=1;cases.append(bad)
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises((ValueError,TypeError)):self.auth(bad)
        with self.assertRaises(ValueError):self.auth(env={'SNOTEL_SMS_FIRST_RUN_JSON':'{"version":1,"version":2}'})

    def test_actual_owned_child_deadlines_and_closure(self):
        with tempfile.TemporaryDirectory() as tmp:
            for phase in ('preflight','acquisition','finalization','complete'):
                parent=Path(tmp)/phase;parent.mkdir();root=parent/'run'
                code='from pathlib import Path;import sys,time;p=Path(sys.argv[1]);p.mkdir();'
                if phase!='preflight':code+="(p/'acquisition-started.json').write_text('{}');"
                if phase in ('finalization','complete'):code+="(p/'acquisition-completed.json').write_text('{}');"
                code+='time.sleep(2)' if phase!='complete' else 'pass'
                r=f.supervise([sys.executable,'-B','-c',code,str(root)],root,
                    preflight_seconds=.25,acquisition_seconds=.25,finalization_seconds=.25,poll=.01)
                if phase=='complete':self.assertEqual((r['child_exit'],r['reason']),(0,None))
                else:self.assertEqual((r['child_exit'],r['reason']),(-9,phase+'_deadline'))
                self.assertFalse(r['restarted']);self.assertTrue((parent/'supervision.json').is_file())

    def test_safe_failure_output_fresh_cli(self):
        p=subprocess.run([sys.executable,'-B',str(Path(f.__file__)),'prepare'],
            env=dict(os.environ,SNOTEL_SMS_FIRST_RUN_JSON='SECRET_CANARY_INVALID_JSON'),capture_output=True,timeout=10)
        self.assertEqual(p.returncode,2);self.assertNotIn(b'SECRET_CANARY',p.stdout+p.stderr)
        self.assertEqual(p.stderr,b'');self.assertEqual(json.loads(p.stdout)['status'],'HELD')

    def test_actual_workflow_contract(self):
        path=Path(__file__).resolve().parents[2]/f.WORKFLOW
        try:
            import yaml
            obj=yaml.load(path.read_text(),Loader=yaml.BaseLoader)
        except ModuleNotFoundError:
            parsed=subprocess.run(['ruby','-ryaml','-rjson','-e',
                'puts JSON.generate(YAML.load_file(ARGV[0]))',str(path)],
                capture_output=True,text=True,check=True,timeout=10)
            def strings(v):
                if isinstance(v,dict):return {k:strings(x) for k,x in v.items()}
                if isinstance(v,list):return [strings(x) for x in v]
                if isinstance(v,bool):return str(v).lower()
                if isinstance(v,int):return str(v)
                return v
            obj=strings(json.loads(parsed.stdout));obj['on']=obj.pop('true',obj.get('on'))
        self.assertEqual(set(obj['on']),{'workflow_dispatch'})
        inputs=obj['on']['workflow_dispatch']['inputs']
        self.assertTrue(all(inputs[k]['default']=='false' for k in ('acquire','publish','bootstrap')))
        prep=obj['jobs']['prepare-candidate'];pub=obj['jobs']['publish-to-main']
        self.assertEqual(prep['permissions'],{'contents':'read'});self.assertEqual(pub['permissions'],{'contents':'write'})
        self.assertEqual(pub['concurrency'],{'group':'brim-live-main-publish','cancel-in-progress':'false','queue':'max'})
        self.assertEqual(prep['runs-on'],pub['runs-on']);self.assertEqual(prep['runs-on'],'ubuntu-24.04')
        step=next(s for s in prep['steps'] if s.get('id')=='prepare')
        self.assertIn('python3 -B scripts/snotel_first_run.py prepare',step['run'])
        upload=next(s for s in prep['steps'] if s.get('uses','').startswith('actions/upload-artifact@'))
        self.assertEqual(upload['if'],"${{ steps.prepare.outputs.candidate_ready == 'true' }}")
        self.assertEqual(upload['with']['path'].splitlines(),[
            '${{ runner.temp }}/snotel-private/run/transfer/candidate/docs/data/snotel-sms/index.json',
            '${{ runner.temp }}/snotel-private/run/transfer/candidate/docs/data/snotel-sms/current-wy.json',
            '${{ runner.temp }}/snotel-private/run/transfer/candidate-metadata.json'])
        command=next(s['run'] for s in pub['steps'] if 'snotel-publish' in s.get('run',''))
        self.assertLess(command.index('snotel_first_run.py publish'),command.index('soil_network_workflow.py snotel-publish'))

    def test_native_network_denial(self):
        import socket
        with socket.socket() as s:
            s.settimeout(.2)
            with self.assertRaises(OSError):s.connect(('192.0.2.1',9))


class FirstPrefixTests(WorkflowTests):
    # Only this new integration method is selected, not inherited science tests.
    def test_full_completed_prefix_bootstrap(self):
        self.groups,self.captures=fixture(end='2026-11-11')
        self.git=StrictGit(self.root/'prefix-repo',self.groups)
        self.queue=iter(c['body'] for c in self.captures)
        r=self.run_candidate(bootstrap=True,clock=lambda:'2026-11-12T19:00:00Z',
            transport_wall=lambda:__import__('soil_network_workflow').ops.time('2026-11-12T19:00:00Z').timestamp(),
            window_end='2026-11-12T22:00:00Z')
        self.assertEqual(r['status'],'CANDIDATE_READY')
        from test_snotel_snapshot import doc
        from soil_network_workflow import ss
        d=doc(ss.read_product(self.runroot/'transfer/candidate'))
        self.assertIsNone(d['parent']);self.assertEqual((d['start'],d['end']),('2026-10-01','2026-11-11'))
        self.assertEqual(r['completed_stations'],32)
        self.assertTrue((self.runroot/'acquisition-started.json').is_file())
        self.assertTrue((self.runroot/'acquisition-completed.json').is_file())

    def test_finalization_alarm_holds_without_candidate(self):
        import time
        import soil_network_workflow as w
        self.git.trees[PRIOR]={}
        limits=dict(w.SNOTEL_LIMITS,finalization_seconds=.02)
        with patch.object(w,'SNOTEL_LIMITS',limits), patch.object(w,'_snotel_finish',side_effect=lambda *a:time.sleep(1)):
            result=self.run_candidate(bootstrap=True,budget=limits)
        self.assertEqual((result['status'],result['attempts'],result['completed_stations']),('HELD',32,32))
        self.assertFalse((self.runroot/'transfer').exists())
        self.assertTrue((self.runroot/'captures.json').is_file())


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(GrantTests)
    suite.addTest(FirstPrefixTests('test_full_completed_prefix_bootstrap'))
    suite.addTest(FirstPrefixTests('test_finalization_alarm_holds_without_candidate'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
