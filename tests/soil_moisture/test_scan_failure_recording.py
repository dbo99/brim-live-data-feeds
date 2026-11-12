"""Actual supervised failures, validation and fixed public diagnostic inventory."""
import importlib.util,json,os,pathlib,subprocess,sys,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[2]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
runner=load('runner','scripts/run_scan_operational.py')
fixture=load('fixture','tests/test_phase2_product_publishers.py')
SENTINEL='SYNTHETIC_PRIVATE_FAILURE_SENTINEL'

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
        self.assertEqual(len(paths),14)
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
            self.receipt(root,account={'requests':29},raw_provider_errors_disclosed=False)
            self.child(root,'scan-acquisition-bounded','pass')
            env=dict(os.environ,RUNNER_TEMP=str(root),GITHUB_SHA='a'*40,SCAN_BUILD_OUTCOME='success',SCAN_VALIDATION_OUTCOME='success',SCAN_METADATA_OUTCOME='success')
            for body in bodies:
                p=subprocess.run(['bash','--noprofile','--norc','-e','-c',body],cwd=ROOT,env=env,capture_output=True,timeout=15)
                self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                self.assertNotIn(SENTINEL.encode(),p.stdout+p.stderr)
            metadata=json.loads((root/'scan-soil-moisture-candidate/candidate-metadata.json').read_text())
            self.assertEqual([x['path'] for x in metadata['files']],['docs/data/'+n for n in runner.PRODUCTS])
            self.assertEqual(json.loads((root/'scan-control/failure-summary.json').read_text())['outcome'],'VALIDATED_CANDIDATE')

if __name__=='__main__':unittest.main()
