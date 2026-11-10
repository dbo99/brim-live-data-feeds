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
        lines = cls.raw.splitlines()
        start = next(i for i,x in enumerate(lines) if '# BEGIN SCAN_APPROVAL_GUARD' in x)
        end = next(i for i,x in enumerate(lines) if '# END SCAN_APPROVAL_GUARD' in x)
        cls.guard = textwrap.dedent('\n'.join(lines[start:end+1]))

    def test_actual_guard_modes_cutoffs_and_binding(self):
        now = dt.datetime.now(dt.timezone.utc)
        from zoneinfo import ZoneInfo
        day = now.astimezone(ZoneInfo('America/Los_Angeles')).date().isoformat()
        names = ('SCAN_HTTP_MAX_REQUESTS','SCAN_HTTP_RESPONSE_BYTES','SCAN_HTTP_TOTAL_BYTES','SCAN_HTTP_CAMPAIGN_SECONDS','SCAN_FETCH_TIMEOUT_SEC')
        budgets = dict(zip(names,[10,4096,16384,10,2]))
        grant = dict(approved=True,approval_id='SYNTHETIC',source_sha='0'*40,run_number=1,
                     not_before_utc=(now-dt.timedelta(seconds=60)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                     not_after_utc=(now+dt.timedelta(seconds=600)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                     budgets=budgets,acquire_only=True,source_cutoff_date=day)
        base = dict(PATH=os.environ['PATH'],GITHUB_SHA='0'*40,GITHUB_RUN_ID='SYNTHETIC',GITHUB_RUN_NUMBER='1',
                    GITHUB_RUN_ATTEMPT='1',GITHUB_REF='refs/heads/main',GITHUB_EVENT_NAME='workflow_dispatch',
                    SCAN_ACQUIRE_ONLY='true',SCAN_SOURCE_CUTOFF_DATE=day,**{k:str(v) for k,v in budgets.items()})
        cases = [('valid',{}, {},True),('mode-mismatch',{'acquire_only':False},{},False),
                 ('string-mode',{'acquire_only':'true'},{},False),('schedule',{}, {'GITHUB_EVENT_NAME':'schedule'},False),
                 ('rerun',{}, {'GITHUB_RUN_ATTEMPT':'2'},False),('wrong-source',{'source_sha':'1'*40},{},False),
                 ('wrong-run',{'run_number':2},{},False),('wrong-cutoff',{'source_cutoff_date':'2000-01-01'},{},False),
                 ('invalid-cutoff',{'source_cutoff_date':'2026-02-30'},{'SCAN_SOURCE_CUTOFF_DATE':'2026-02-30'},False),
                 ('missing-cutoff',{'source_cutoff_date':''},{'SCAN_SOURCE_CUTOFF_DATE':''},False),
                 ('wrong-budget',{}, {'SCAN_HTTP_TOTAL_BYTES':'16385'},False),
                 ('expired',{'not_after_utc':'2000-01-01T00:00:00Z'},{},False),
                 ('unapproved',{'approved':False},{},False)]
        for name,change,envchange,passed in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                value=copy.deepcopy(grant);value.update(change)
                env=dict(base,RUNNER_TEMP=tmp,SCAN_LIVE_APPROVAL_JSON=json.dumps(value));env.update(envchange)
                p=subprocess.run([sys.executable,'-B','-c',self.guard],env=env,capture_output=True,text=True,timeout=5)
                self.assertEqual(p.returncode==0,passed,p.stderr)
                receipt=json.loads((Path(tmp)/'scan-control/approval-initial.json').read_text())
                self.assertFalse(receipt['provider_invoked_by_guard'])
        # The old missing-mode grant remains false only; it cannot select acquisition-only.
        with tempfile.TemporaryDirectory() as tmp:
            value={k:v for k,v in grant.items() if k not in ('acquire_only','source_cutoff_date')}
            env=dict(base,RUNNER_TEMP=tmp,SCAN_LIVE_APPROVAL_JSON=json.dumps(value),SCAN_ACQUIRE_ONLY='false',SCAN_SOURCE_CUTOFF_DATE='')
            self.assertEqual(subprocess.run([sys.executable,'-B','-c',self.guard],env=env,capture_output=True,timeout=5).returncode,0)

    def test_actual_publisher_condition_and_permissions(self):
        jobs=self.workflow['jobs'];publisher=jobs['publish-to-main'];prepare=jobs['prepare-candidate']
        expression=publisher['if'].removeprefix('${{').removesuffix('}}').strip()
        for success,ref,mode in itertools.product([True,False],['refs/heads/main','refs/heads/feature'],[True,False,None]):
            code=expression.replace('success()',repr(success)).replace('github.ref',repr(ref)).replace('inputs.acquire_only',repr(mode)).replace('true','True').replace('&&',' and ')
            self.assertEqual(eval(code,{'__builtins__':{}},{}),success and ref=='refs/heads/main' and mode is not True)
        self.assertEqual(prepare['permissions'],{'contents':'read'})
        self.assertEqual(publisher['permissions'],{'contents':'write'})
        self.assertEqual(publisher['needs'],'prepare-candidate')
        self.assertEqual(publisher['concurrency'],{'group':'brim-live-main-publish','cancel-in-progress':False,'queue':'max'})
        self.assertNotIn('main_publisher.py publish',json.dumps(prepare))
        self.assertEqual(prepare['runs-on'],'ubuntu-24.04')
        self.assertIn('provision',json.dumps(prepare))
        self.assertNotIn('any::soilDB',self.raw)
        self.assertIn('soilDB="2.9.2", httr="1.4.9", curl="8.1.0"',self.raw)
        trigger=self.workflow.get('on',self.workflow.get('true'))
        self.assertFalse(trigger['workflow_dispatch']['inputs']['acquire_only']['default'])
        self.assertEqual(trigger['schedule'],[{'cron':'30 14 * * *'}])
        for job in (prepare,publisher):
            paths=set(__import__('re').findall(r'--allowlist (docs/data/[^\s\\]+)',json.dumps(job).replace('\\n','\n')))
            self.assertEqual(len(paths),5)

if __name__=='__main__':unittest.main()
