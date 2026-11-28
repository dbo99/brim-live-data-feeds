"""Actual supervised failures, validation and fixed public diagnostic inventory."""
import contextlib, importlib.util, io, json, os, pathlib, subprocess, sys, tempfile, unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[2]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
runner=load('runner','scripts/run_scan_operational.py')
fixture=load('fixture','tests/test_phase2_product_publishers.py')
SENTINEL='SYNTHETIC_PRIVATE_FAILURE_SENTINEL'

class ProvisioningFailureTests(unittest.TestCase):
    provisioner = load('provisioner', 'tests/soil_moisture/run_sms_hosted_qualification.py')

    def test_workflow_provision_failure_reaches_log_without_private_stderr(self):
        workflow = json.loads(subprocess.check_output([
            'ruby', '-ryaml', '-rjson', '-e', 'puts JSON.generate(YAML.load_file(ARGV[0]))',
            str(ROOT/'.github/workflows/build-scan-soil-moisture-latest.yml')], text=True))
        step = next(s for s in workflow['jobs']['prepare-candidate']['steps']
                    if s['name'] == 'Provision the supported bounded SCAN runtime')
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            sudo = root/'sudo'
            sudo.write_text(f'#!/bin/sh\necho {SENTINEL} >&2\nexit 47\n')
            sudo.chmod(0o700)
            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ['PATH'], RUNNER_TEMP=tmp)
            result = subprocess.run(['bash', '-c', step['run']], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1)
            self.assertIn('Provisioning CHILD_FAILURE stage=APT_PLAN '
                          'exception_class=CalledProcessError status=47', result.stdout)
            self.assertNotIn(SENTINEL, result.stdout + result.stderr)
            self.assertNotIn(tmp, result.stdout + result.stderr)
            self.assertIn(SENTINEL, (root/'scan-provision-bounded/child.stderr').read_text())
            self.assertFalse((root/'scan-control/runtime-lock.json').exists())

    def test_distinct_stage_refuses_before_any_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)/'runtime'
            plan = f"'https://example.invalid/{SENTINEL}' test.deb 4 MD5Sum:1234\n"
            with mock.patch.object(subprocess, 'check_output', return_value=plan), \
                 mock.patch.object(self.provisioner.urllib.request.OpenerDirector, 'open',
                                   side_effect=AssertionError('unexpected download')) as opener:
                with self.assertRaises(AssertionError):
                    self.provisioner.provision(out)
                opener.assert_not_called()
            report = self.provisioner.provision_failure_path(out).read_text()
            self.assertEqual(json.loads(report),
                             {'stage':'APT_ROUTE', 'exception_class':'AssertionError', 'status':None})
            self.assertNotIn(SENTINEL, report)

    def test_scratch_failure_preserves_original_exception_and_existing_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)/'already-present'
            out.mkdir()
            report = self.provisioner.provision_failure_path(out)
            with self.assertRaises(FileExistsError):
                self.provisioner.provision(out)
            self.assertEqual(json.loads(report.read_bytes()), {
                'stage':'SCRATCH', 'exception_class':'FileExistsError', 'status':None})
            report.write_text(SENTINEL)
            with self.assertRaises(FileExistsError):
                self.provisioner.provision(out)
            self.assertEqual(report.read_text(), SENTINEL)

    def test_untrusted_or_missing_diagnostic_emits_only_fixed_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp)/'report'
            for data in (None, b'{' + SENTINEL.encode(), b'x'*513,
                         json.dumps({'stage':SENTINEL, 'exception_class':[SENTINEL],
                                     'status':True, 'stderr':SENTINEL}).encode()):
                if data is not None:
                    path.write_bytes(data)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.provisioner.report_provision_failure(path, 'SUPERVISOR_FAILURE')
                self.assertEqual(output.getvalue().strip(),
                                 'Provisioning SUPERVISOR_FAILURE stage=UNAVAILABLE '
                                 'exception_class=UNAVAILABLE status=unavailable')


class FailureTests(unittest.TestCase):
    def child(self,root,name,code,**limits):
        return runner.bounded([sys.executable,'-c',code],root/name,[root],limits.pop('seconds',5),**limits)
    def receipt(self,root,status='BUILT_ACCOUNT_COMPLETE',**values):
        control=root/'scan-control';control.mkdir(exist_ok=True)
        (control/'runtime-account.json').write_text(json.dumps(dict(dict(status=status,phase='COMPLETE',exception_class='NONE',account_refusal='NONE',account_predicate=True),**values)))
    def final(self,root,build='success',validation='success',metadata='success'):
        p=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/run_scan_operational.py'),'finalize',str(root),build,validation,metadata],capture_output=True,timeout=5)
        body=(root/'scan-control/failure-summary.json').read_bytes()
        self.assertNotIn(SENTINEL.encode(),body+p.stdout+p.stderr)
        self.assertEqual(p.stderr,b'')
        result=json.loads(body);self.assertEqual(set(result),{'schema','outcome','runtime_status','phase','exception_class','account_refusal','build_step','validation_step','metadata_step','acquisition_supervision','raw_logs_public'})
        self.assertEqual(p.returncode,0 if result['outcome']=='VALIDATED_CANDIDATE' else 1)
        return result
    def test_actual_builder_and_account_failure_and_missing_receipt(self):
        for status in ['BUILDER_EXCEPTION','ACCOUNT_REJECTED','RUNTIME_RECEIPT_FAILURE',None]:
            with self.subTest(status=status),tempfile.TemporaryDirectory() as t:
                root=pathlib.Path(t);self.child(root,'scan-acquisition-bounded',f'print({SENTINEL!r});raise SystemExit(1)')
                if status:self.receipt(root,status,private=SENTINEL)
                self.assertEqual(self.final(root,'failure','skipped','skipped')['outcome'],status or 'RUNTIME_RECEIPT_FAILURE')
    def test_actual_supervisor_limits_and_launch_failure(self):
        cases=[('time','import time;time.sleep(5)',{'seconds':.1}),
               ('footprint','import time;time.sleep(5)',{'trigger':1}),
               ('file','import os;os.write(1,b"x"*8192);os.write(1,b"x")',{'file_limit':1024})]
        for name,code,limits in cases:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as t:
                root=pathlib.Path(t);rc=self.child(root,'scan-acquisition-bounded',code,**limits);self.assertNotEqual(rc,0)
                self.assertEqual(json.loads((root/'scan-acquisition-bounded/bounded.json').read_text())['remaining_owned_processes'],[])
                if name=='file':self.assertLessEqual((root/'scan-acquisition-bounded/child.stdout').stat().st_size,1024)
                result=self.final(root,'failure','skipped','skipped')
                self.assertIn(result['outcome'],{'SUPERVISOR_FAILURE','RUNTIME_RECEIPT_FAILURE'})
        with tempfile.TemporaryDirectory() as t:
            root=pathlib.Path(t)
            with self.assertRaises(OSError):runner.bounded([str(root/'absent')],root/'scan-acquisition-bounded',[root],5)
            self.assertEqual(self.final(root,'failure','skipped','skipped')['outcome'],'SUPERVISOR_FAILURE')
    def test_cli_launch_failure_does_not_print_private_path(self):
        with tempfile.TemporaryDirectory() as t:
            root=pathlib.Path(t)
            p=subprocess.run([sys.executable,str(ROOT/'scripts/run_scan_operational.py'),'--output',str(root/'bounded'),'--root',str(root),'--seconds','5','--',str(root/SENTINEL)],capture_output=True,timeout=10)
            self.assertNotEqual(p.returncode,0)
            self.assertEqual(p.stderr,b'')
            self.assertNotIn(SENTINEL.encode(),p.stdout)
            self.assertNotIn(str(root).encode(),p.stdout)
    def test_actual_validator_positive_empty_quality_and_last_good(self):
        for defect in ['none','empty','quality']:
            with self.subTest(defect=defect),tempfile.TemporaryDirectory() as t:
                root=pathlib.Path(t);candidate=root/'candidate';last=root/'last-good'
                fixture.write_scan_product(candidate,'2026-11-11T00:00:00Z',station_name='SYNTHETIC')
                fixture.write_scan_product(last,'2026-11-10T00:00:00Z',station_name='SYNTHETIC')
                before={str(p.relative_to(last)):p.read_bytes() for p in last.rglob('*') if p.is_file()}
                summary=candidate/'docs/data/scan_soil_moisture_latest_summary.json'
                if defect!='none':
                    value=json.loads(summary.read_text());value['stations_with_any_current_wy_sms']=0 if defect=='empty' else SENTINEL;summary.write_text(json.dumps(value))
                self.receipt(root);self.child(root,'scan-acquisition-bounded','pass')
                rc=runner.bounded([sys.executable,str(ROOT/'scripts/scan_soil_moisture_publisher.py'),'validate','--root',str(candidate)],root/'scan-validation-bounded',[root],5)
                self.assertEqual(rc==0,defect=='none')
                if rc==0:self.child(root,'scan-metadata-bounded','pass')
                result=self.final(root,validation='success' if rc==0 else 'failure',metadata='success' if rc==0 else 'skipped')
                self.assertEqual(result['outcome'],'VALIDATED_CANDIDATE' if rc==0 else 'VALIDATION_FAILURE')
                self.assertEqual(before,{str(p.relative_to(last)):p.read_bytes() for p in last.rglob('*') if p.is_file()})
    def test_unreviewed_values_never_pass_through(self):
        with tempfile.TemporaryDirectory() as t:
            root=pathlib.Path(t);self.receipt(root,SENTINEL)
            p=root/'scan-control/runtime-account.json';value=json.loads(p.read_text());value.update(phase=SENTINEL,exception_class=[SENTINEL]);p.write_text(json.dumps(value))
            self.child(root,'scan-acquisition-bounded','raise SystemExit(1)')
            self.assertEqual(self.final(root,'failure','skipped','skipped')['outcome'],'RUNTIME_RECEIPT_FAILURE')
    def test_refusal_missing_unknown_or_conflicting_never_passes(self):
        for code in [None,SENTINEL,['NONE'],'TIMEOUT_LIMIT']:
            with self.subTest(code=code),tempfile.TemporaryDirectory() as t:
                root=pathlib.Path(t);self.receipt(root,account_refusal=code)
                self.child(root,'scan-acquisition-bounded','pass')
                result=self.final(root)
                self.assertEqual(result['outcome'],'RUNTIME_RECEIPT_FAILURE')
                self.assertEqual(result['account_refusal'],'TIMEOUT_LIMIT' if code=='TIMEOUT_LIMIT' else 'UNVERIFIED')
    def test_actual_R_serialized_refusals_reach_independent_finalizer(self):
        directory=os.environ.get('SCAN_R_RECEIPT_TEST_DIR')
        if not directory:self.skipTest('Run after the confined R receipt suite for cross-process evidence')
        paths=list(pathlib.Path(directory).glob('clause-*.json'))
        self.assertEqual(len(paths),15)
        paths += [pathlib.Path(directory)/(n+'.json') for n in ['snapshot','predicate-exception','corrected-meter']]
        for path in paths:
            with self.subTest(name=path.name),tempfile.TemporaryDirectory() as t:
                root=pathlib.Path(t);control=root/'scan-control';control.mkdir()
                body=path.read_bytes();(control/'runtime-account.json').write_bytes(body);runtime=json.loads(body)
                good=runtime['status']=='BUILT_ACCOUNT_COMPLETE'
                self.child(root,'scan-acquisition-bounded','pass' if good else 'raise SystemExit(1)')
                if good:
                    self.child(root,'scan-validation-bounded','pass');self.child(root,'scan-metadata-bounded','pass')
                result=self.final(root,'success' if good else 'failure','success' if good else 'skipped','success' if good else 'skipped')
                self.assertEqual(result['account_refusal'],runtime['account_refusal'])
                self.assertEqual(result['outcome'],'VALIDATED_CANDIDATE' if good else runtime['status'])
    def test_metadata_failure_cannot_publish(self):
        with tempfile.TemporaryDirectory() as t:
            root=pathlib.Path(t);self.receipt(root)
            self.child(root,'scan-acquisition-bounded','pass');self.child(root,'scan-validation-bounded','pass')
            self.child(root,'scan-metadata-bounded','raise SystemExit(1)')
            self.assertEqual(self.final(root,metadata='failure')['outcome'],'RUNTIME_RECEIPT_FAILURE')
    def test_absent_supervisor_cannot_be_success(self):
        with tempfile.TemporaryDirectory() as t:
            root=pathlib.Path(t);self.receipt(root)
            self.assertEqual(self.final(root)['outcome'],'SUPERVISOR_FAILURE')
    def test_actual_workflow_validation_metadata_and_finalization(self):
        workflow=json.loads(subprocess.check_output(['ruby','-ryaml','-rjson','-e','puts JSON.generate(YAML.load_file(ARGV[0]))',str(ROOT/'.github/workflows/build-scan-soil-moisture-latest.yml')],text=True))
        steps=workflow['jobs']['prepare-candidate']['steps']
        bodies=[next(s['run'] for s in steps if s.get('id')==ident) for ident in ['validation','candidate_metadata']]
        bodies.append(next(s['run'] for s in steps if s['name']=='Record fixed-label SCAN outcome'))
        with tempfile.TemporaryDirectory() as t:
            root=pathlib.Path(t);candidate=root/'scan-soil-moisture-candidate/candidate'
            fixture.write_scan_product(candidate,'2026-11-11T00:00:00Z',station_name='SYNTHETIC')
            control = root/'scan-control'
            control.mkdir()
            fixtures = load('scan_plan_fixture','tests/soil_moisture/test_scan_operational.py')
            fixtures.write_account_fixture(control, '2026-11-10')
            self.child(root,'scan-acquisition-bounded','pass')
            env=dict(os.environ,RUNNER_TEMP=str(root),GITHUB_SHA='a'*40,SCAN_SOURCE_CUTOFF_DATE='2026-11-10',SCAN_BUILD_OUTCOME='success',SCAN_VALIDATION_OUTCOME='success',SCAN_METADATA_OUTCOME='success')
            for body in bodies:
                p=subprocess.run(['bash','--noprofile','--norc','-e','-c',body],cwd=ROOT,env=env,capture_output=True,timeout=15)
                self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                self.assertNotIn(SENTINEL.encode(),p.stdout+p.stderr)
            metadata=json.loads((root/'scan-soil-moisture-candidate/candidate-metadata.json').read_text())
            self.assertEqual([x['path'] for x in metadata['files']],['docs/data/'+n for n in runner.PRODUCTS])
            self.assertEqual(json.loads((root/'scan-control/failure-summary.json').read_text())['outcome'],'VALIDATED_CANDIDATE')

if __name__=='__main__':unittest.main()
