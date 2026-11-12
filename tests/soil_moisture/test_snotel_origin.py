"""Real Git objects and fresh interpreters; all writes are inert fixtures only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_snotel_first_run import GrantTests
import snotel_first_run as f

SOURCE = Path(__file__).resolve().parents[2]
ORIGINS = ('https://github.com/dbo99/brim-live-data-feeds',
           'https://github.com/dbo99/brim-live-data-feeds.git',
           'git@github.com:dbo99/brim-live-data-feeds.git')


class RealReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve();self.repo=self.root/'repo';self.repo.mkdir()
        for p in (SOURCE/'scripts').rglob('*.py'):
            q=self.repo/p.relative_to(SOURCE);q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(p.read_bytes())
        catalog=self.repo/'data/input/snotel_sms_catalog.json';catalog.parent.mkdir(parents=True)
        catalog.write_bytes((SOURCE/'data/input/snotel_sms_catalog.json').read_bytes())
        self.git('init','-q');self.git('config','user.name','Synthetic fixture');self.git('config','user.email','fixture@example.invalid')
        self.git('add','scripts','data');self.git('commit','-qm','Synthetic reader fixture')
        self.git('remote','add','origin',ORIGINS[0]);self.head=self.git('rev-parse','HEAD')

    def git(self,*args):
        return subprocess.check_output(['git','--no-optional-locks',*args],cwd=self.repo,timeout=30,text=True,stderr=subprocess.DEVNULL).strip()

    def reader(self,body,ok=True):
        code="import sys;sys.path.insert(0,'scripts');import soil_network_workflow as w;r=w.SnotelGitReader('.');assert not r.test_stub;"+body
        p=subprocess.run([sys.executable,'-B','-c',code],cwd=self.repo,capture_output=True,timeout=30)
        self.assertEqual(p.returncode==0,ok,p.stderr.decode() if ok else 'Unexpected reader acceptance')
        return p

    def test_exact_origins_actual_objects_and_source(self):
        for origin in ORIGINS:
            with self.subTest(origin=origin):
                self.git('remote','set-url','origin',origin)
                self.reader(f"pins=r.source('{self.head}');assert len(pins)>=56;assert r.product('{self.head}',bootstrap=True)==(None,None)")
        self.git('remote','set-url','origin',ORIGINS[0])
        self.reader("import snotel_first_run as f;"+f"e=dict(GITHUB_SHA='{self.head}',GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1',RUNNER_TEMP={str(self.root)!r},CATALOG_PIN='d82973ad46b00df2737d6b902a2dbea3a450210d859bf10c82d80384e405beb8',ROSTER_PIN='c85422467f7b49d603cbce3d7ce92e8cd55f01fcdcbb42053373a11f01ab66da');p=f.check_source(e);assert p['test_stub'] is False and p['origin']=={ORIGINS[0]!r}")

    def test_origin_near_matches_refuse(self):
        for origin in ('https://github.com.invalid/dbo99/brim-live-data-feeds',
            'https://github.com/dbo99/other','https://blocked@github.com/dbo99/brim-live-data-feeds',
            ORIGINS[0]+'?x=1',ORIGINS[0]+'#main',ORIGINS[0]+'/',ORIGINS[0]+'/tree/main',
            'http://github.com/dbo99/brim-live-data-feeds','https://GITHUB.com/dbo99/brim-live-data-feeds'):
            with self.subTest(origin=origin):
                self.git('remote','set-url','origin',origin);self.reader('pass',ok=False)

    def test_source_content_commit_and_partial_bootstrap_refuse(self):
        p=self.repo/'scripts/soil_moisture/snotel_snapshot.py';original=p.read_bytes();p.write_bytes(original+b'\n# synthetic mismatch\n')
        self.reader(f"r.source('{self.head}')",ok=False);p.write_bytes(original)
        self.reader("r.source('main')",ok=False)
        self.reader(f"r.product('{self.head}',bootstrap=False)",ok=False)
        q=self.repo/'docs/data/snotel-sms/index.json';q.parent.mkdir(parents=True);q.write_text('{}')
        self.git('add','docs');self.git('commit','-qm','Synthetic partial product')
        partial=self.git('rev-parse','HEAD');self.reader(f"r.product('{partial}',bootstrap=True)",ok=False)
        self.reader(f"r.source('{self.head}')",ok=False)


class ProjectionTests(GrantTests):
    def test_source_proof_refusals(self):
        with tempfile.TemporaryDirectory() as directory:
            env=dict(self.env,RUNNER_TEMP=directory)
            p=Path(directory)/'snotel-real-reader-check.json'
            with self.assertRaises(FileNotFoundError):f.require_source_proof(env,self.grant,self.clock)
            proof=dict(source_sha=self.grant['source_sha'],run_id=env['GITHUB_RUN_ID'],attempt='1',
                test_stub=False,product_absent=True,catalog_sha256=self.grant['catalog_sha256'],
                roster_sha256=self.grant['roster_sha256'],stations=32,sensors=98,
                checked_at_utc=self.clock.strftime('%Y-%m-%dT%H:%M:%SZ'))
            p.write_text(json.dumps(proof));self.assertEqual(f.require_source_proof(env,self.grant,self.clock),proof)
            for changes in ({'source_sha':'f'*40},{'run_id':'999'},{'attempt':'2'},
                {'test_stub':True},{'product_absent':False},{'stations':31},{'sensors':97},
                {'catalog_sha256':'f'*64},{'roster_sha256':'f'*64},
                {'checked_at_utc':'2026-11-12T18:00:00Z'},{'checked_at_utc':'2026-11-13T18:00:00Z'}):
                p.write_text(json.dumps(dict(proof,**changes)))
                with self.assertRaises(ValueError):f.require_source_proof(env,self.grant,self.clock)

    def test_exact_private_grant_projection(self):
        env=dict(self.env,R9_GRANT_EXACT_MATCH='true',GRANT_ACTIVATED_AT=self.grant['activated_at_utc'])
        self.assertEqual(f.authorize(f.hosted_environment(env),'prepare',self.clock),self.grant)
        for changes in ({'R9_GRANT_EXACT_MATCH':'false'}, {'SNOTEL_SMS_FIRST_RUN_JSON':'private-canary'}):
            with self.assertRaises(ValueError):f.hosted_environment(dict(env,**changes))
        old=dict(self.grant,approval_id='L11-SNOTEL-SMS-R8-FIRST-REAL-CURRENT-WY-20261112',run_number=1)
        with self.assertRaises(ValueError):self.auth(old)

    def test_complete_workflow_grant_serialization(self):
        import re
        raw=(SOURCE/f.WORKFLOW).read_text()
        templates=re.findall(r"vars\.SNOTEL_SMS_FIRST_RUN_JSON == format\('([^']+)'",raw)
        self.assertEqual(len(templates),2);self.assertEqual(templates[0],templates[1])
        formed=templates[0].format(self.grant['activated_at_utc'],self.grant['catalog_sha256'],
            self.grant['roster_sha256'],self.grant['source_sha'],self.grant['window_end'])
        canonical=json.dumps(self.grant,sort_keys=True,separators=(',',':'));self.assertEqual(formed,canonical)
        for bad in (dict(self.grant,run_number=1),dict(self.grant,extra=True),dict(self.grant,approval_id='old')):
            self.assertNotEqual(formed,json.dumps(bad,sort_keys=True,separators=(',',':')))
        self.assertNotIn('SNOTEL_SMS_FIRST_RUN_JSON: ${{',raw)
        self.assertIn('python3 -B scripts/snotel_first_run.py check-source',raw)
        self.assertLess(raw.index('snotel_first_run.py check-source'),raw.index('snotel_first_run.py prepare'))
        self.assertIn('sudo unshare --net',raw)


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(RealReaderTests)
    for name in ('test_valid_prepare_and_publish','test_environment_refusals','test_grant_schema_types_deadline_and_budgets',
                 'test_actual_workflow_contract','test_safe_failure_output_fresh_cli','test_native_network_denial'):
        suite.addTest(GrantTests(name))
    for name in ('test_source_proof_refusals','test_exact_private_grant_projection','test_complete_workflow_grant_serialization'):
        suite.addTest(ProjectionTests(name))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
