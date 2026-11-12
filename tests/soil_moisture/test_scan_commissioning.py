"""Offline tests of actual SCAN workflow guard and publisher expression."""
import copy
import datetime as dt
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/build-scan-soil-moisture-latest.yml'

class ScanCommissioningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = WORKFLOW.read_text()
        result = subprocess.run(['ruby', '-ryaml', '-rjson', '-e',
            'puts JSON.generate(YAML.load_file(ARGV[0]))', str(WORKFLOW)],
            capture_output=True, text=True, check=True)
        cls.workflow = json.loads(result.stdout)
    def test_actual_guard_integration(self):
        steps=self.workflow['jobs']['prepare-candidate']['steps']
        guards=[s for s in steps if 'scan_operational_guard.py' in s.get('run','')]
        self.assertEqual([s['run'] for s in guards], [
            'python3 -B scripts/scan_operational_guard.py initial',
            'python3 -B scripts/scan_operational_guard.py pre-acquisition'])
        # Full finite decision cases exercise the same imported guard in
        # test_scan_operational.py; private acquire-only remains job-excluded.

    def test_actual_publisher_condition_and_permissions(self):
        jobs=self.workflow['jobs'];publisher=jobs['publish-to-main'];prepare=jobs['prepare-candidate']
        expression=publisher['if'].removeprefix('${{').removesuffix('}}').strip()
        for success,ref,mode in itertools.product([True,False],['refs/heads/main','refs/heads/feature'],[True,False,None]):
            code=expression.replace('success()',repr(success)).replace('github.ref',repr(ref)).replace('inputs.acquire_only',repr(mode)).replace('true','True').replace('&&',' and ')
            self.assertEqual(eval(code,{'__builtins__':{}},{}),success and ref=='refs/heads/main' and mode is not True)
        self.assertEqual(prepare['permissions'],{'contents':'read','actions':'read'})
        self.assertEqual(publisher['permissions'],{'contents':'write'})
        self.assertEqual(publisher['needs'],'prepare-candidate')
        self.assertEqual(publisher['concurrency'],{'group':'brim-live-main-publish','cancel-in-progress':False,'queue':'max'})
        self.assertNotIn('main_publisher.py publish',json.dumps(prepare))
        self.assertEqual(prepare['runs-on'],'ubuntu-24.04')
        self.assertIn('provision',json.dumps(prepare))
        self.assertNotIn('any::soilDB',self.raw)
        self.assertIn('soilDB="2.9.2", httr="1.4.9", curl="8.1.0"',(ROOT/'scripts/run_scan_operational.R').read_text())
        trigger=self.workflow.get('on',self.workflow.get('true'))
        self.assertFalse(trigger['workflow_dispatch']['inputs']['acquire_only']['default'])
        self.assertEqual(trigger['schedule'],[{'cron':'30 14 * * *'}])
        for job in (prepare,publisher):
            paths=set(__import__('re').findall(r'--allowlist (docs/data/[^\s\\]+)',json.dumps(job).replace('\\n','\n')))
            self.assertEqual(len(paths),5)

    @staticmethod
    def condition(expression, mode, success=True):
        # Only the actual boolean workflow expressions under test; no service or
        # runner emulation. A step without a status function implicitly requires
        # success, as do the unconditioned production steps.
        if expression is None:
            return success
        code = expression.removeprefix('${{').removesuffix('}}').strip()
        explicit_status = 'always()' in code or 'success()' in code
        code = code.replace('always()', 'True').replace('success()', repr(success))
        code = code.replace('inputs.acquire_only', repr(mode)).replace('true', 'True').replace('&&', ' and ')
        value = eval(code, {'__builtins__': {}}, {})
        return bool(value) and (explicit_status or success)

    def publication_uploads(self, workflow):
        steps = workflow['jobs']['prepare-candidate']['steps']
        uploads = [s for s in steps if s.get('uses', '').startswith('actions/upload-artifact@')]
        self.assertEqual(len(uploads), 2)
        expected = [
            ('scan-soil-moisture-candidate-${{ github.run_id }}-${{ github.run_attempt }}',
             '${{ runner.temp }}/scan-soil-moisture-candidate', 'error', 2),
            ('scan-control-${{ github.run_id }}-${{ github.run_attempt }}',
             '\n'.join('${{ runner.temp }}/scan-control/'+n+'.json' for n in ('approval-initial','approval-pre-acquisition','campaign-reservation','hosted-qualification','runtime-lock','runtime-account','failure-summary'))+'\n', 'warn', 14),
        ]
        for step, (name, path, missing, retention) in zip(uploads, expected):
            self.assertEqual(step['uses'], 'actions/upload-artifact@v7')
            self.assertEqual(step['with'], dict(name=name, path=path,
                **{'if-no-files-found': missing, 'retention-days': retention}))
        return uploads

    def test_actual_job_admission_isolates_grant_environment(self):
        jobs = self.workflow['jobs']
        self.assertEqual(set(jobs), {'refuse-private-acquisition', 'prepare-candidate', 'publish-to-main'})
        refusal = jobs['refuse-private-acquisition']
        prepare = jobs['prepare-candidate']
        self.assertNotIn('env', self.workflow)
        self.assertNotIn('env', refusal)
        self.assertNotIn('continue-on-error', refusal)
        self.assertEqual(refusal['permissions'], {})
        self.assertEqual(refusal['timeout-minutes'], 1)
        self.assertIn('SCAN_LIVE_APPROVAL_JSON', prepare['env'])
        for mode in (True, False, None):
            self.assertEqual(self.condition(refusal['if'], mode), mode is True)
            self.assertEqual(self.condition(prepare['if'], mode), mode is not True)
        # No checkout, grant evaluation, source build or artifact action can run
        # in the sole admitted acquire-only job.
        self.assertEqual(len(refusal['steps']), 1)
        step = refusal['steps'][0]
        self.assertEqual(set(step), {'name', 'shell', 'run'})
        self.assertEqual(step['shell'], 'bash')
        for step in prepare['steps']:
            if not step.get('uses', '').startswith('actions/upload-artifact@'):
                if step['name']=='Record fixed-label SCAN outcome':
                    self.assertEqual(step['if'],'${{ always() && inputs.acquire_only != true }}')
                    self.assertIn('run_scan_operational.py finalize',step['run'])
                else:self.assertNotIn('if', step)
                self.assertFalse(step.get('continue-on-error', False))
        # A failed or refused preparation cannot make the dependent publisher run.
        self.assertEqual(jobs['publish-to-main']['needs'], 'prepare-candidate')
        self.assertNotIn('continue-on-error', prepare)

    def test_actual_refusal_command_has_fixed_output_and_no_evidence_claim(self):
        body = self.workflow['jobs']['refuse-private-acquisition']['steps'][0]['run']
        expected = b'SCAN acquire-only held: no approved durable private candidate and receipt custody route.\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sentinels = ['SYNTHETIC_PRIVATE_CANDIDATE', 'SYNTHETIC_RAW_RESPONSE',
                         'SYNTHETIC_PARTIAL_UNKNOWN_RECEIPT', 'SYNTHETIC_PRIVATE_ID',
                         'SYNTHETIC_PRIVATE_COORDINATE', 'SYNTHETIC_PRIVATE_ENV_VALUE']
            for i, value in enumerate(sentinels):
                (root / str(i)).write_text(value)
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            env = dict(PATH=os.environ['PATH'], SCAN_LIVE_APPROVAL_JSON=sentinels[2],
                       SYNTHETIC_PRIVATE_ENV=sentinels[-1], RUNNER_TEMP=tmp)
            for phase in ('success', 'failed-build', 'validation-failure', 'partial-unknown', 'always-cleanup'):
                with self.subTest(phase=phase):
                    proc = subprocess.run(['bash', '--noprofile', '--norc', '-e', '-c', body],
                        cwd=tmp, env=env, capture_output=True, timeout=5)
                    self.assertEqual(proc.returncode, 1)  # Expected fail-closed refusal.
                    self.assertEqual(proc.stdout, b'')
                    self.assertEqual(proc.stderr, expected)
                    self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)
                    for marker in sentinels:
                        self.assertNotIn(marker.encode(), proc.stdout + proc.stderr)
        self.assertNotIn('GITHUB_STEP_SUMMARY', body)

    def test_actual_upload_matrix_including_always_failure_and_partial_state(self):
        candidate, receipts = self.publication_uploads(self.workflow)
        phases = {'success': True, 'failed-build': False, 'validation-failure': False,
                  'partial-unknown': False, 'always-cleanup': False}
        for mode, (phase, success) in itertools.product((True, False, None), phases.items()):
            with self.subTest(mode=mode, phase=phase):
                # Test upload guards independently, even if future preparation
                # accidentally creates partial files before failure.
                self.assertEqual(self.condition(candidate['if'], mode, success),
                                 mode is not True and success)
                self.assertEqual(self.condition(receipts['if'], mode, success), mode is not True)
        # Paths are literal workflow-owned constants, not a user-supplied escape.
        # The offline contract refuses a synthetic broad-path regression.
        altered = copy.deepcopy(self.workflow)
        for step in altered['jobs']['prepare-candidate']['steps']:
            if step.get('uses', '').startswith('actions/upload-artifact@'):
                step['with']['path'] = '${{ runner.temp }}'
                break
        with self.assertRaises(AssertionError):
            self.publication_uploads(altered)

if __name__=='__main__':unittest.main()
