"""Focused provider-free tests for the one-shot retained SNOTEL bridge."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import snotel_retained_candidate as b


class RetainedTests(unittest.TestCase):
    def env(self):
        return dict(GITHUB_ACTIONS='true', GITHUB_REPOSITORY=b.REPOSITORY,
            GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/main',
            GITHUB_REF_TYPE='branch', GITHUB_RUN_NUMBER='1', GITHUB_RUN_ATTEMPT='1',
            GITHUB_WORKFLOW_REF=b.REPOSITORY+'/'+b.WORKFLOW+'@refs/heads/main',
            GITHUB_SHA='a'*40, RETAINED_EVENT_SHA='a'*40,
            RETAINED_STAGING_SHA=b.STAGING_SHA, GITHUB_RUN_ID='123')

    def test_finite_identity_and_complete_time_reserve(self):
        now = b.EXPIRES-dt.timedelta(hours=1)
        self.assertEqual(b.admit(self.env(), 'a'*40, now), 'a'*40)
        for key in self.env():
            env=self.env();env[key]='wrong'
            with self.subTest(key=key),self.assertRaises(ValueError):b.admit(env,'a'*40,now)
        for key,value in [('GITHUB_RUN_NUMBER','2'),('GITHUB_RUN_ATTEMPT','2'),
                          ('GITHUB_REF','refs/heads/staging'),('GITHUB_EVENT_NAME','schedule')]:
            env=self.env();env[key]=value
            with self.assertRaises(ValueError):b.admit(env,'a'*40,now)
        with self.assertRaises(ValueError):b.admit(self.env(),'b'*40,now)
        with self.assertRaises(ValueError):b.admit(self.env(),'a'*40,b.EXPIRES-dt.timedelta(seconds=1499))

    def test_tree_closure_and_modes(self):
        rows=[b'100644 blob '+b'a'*40+b'\t'+n.encode() for n in b.EXPECTED]
        self.assertEqual(set(b.tree_entries(b'\0'.join(rows),b.EXPECTED)),set(b.EXPECTED))
        cases=[rows[:1],rows+[rows[0]],rows+[b'100644 blob '+b'a'*40+b'\t.gitattributes']]
        cases += [[rows[0].replace(b'100644',m)]+rows[1:] for m in (b'120000',b'100755',b'160000')]
        for bad in cases:
            with self.assertRaises(ValueError):b.tree_entries(b'\0'.join(bad),b.EXPECTED)

    def test_actual_git_objects_root_parent_and_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();repo=root/'repo';repo.mkdir()
            env=dict(os.environ,GIT_AUTHOR_NAME='Synthetic',GIT_AUTHOR_EMAIL='fixture@example.invalid',
                     GIT_COMMITTER_NAME='Synthetic',GIT_COMMITTER_EMAIL='fixture@example.invalid')
            def git(*args,data=None):
                return subprocess.check_output(['git','-C',str(repo),*args],input=data,env=env,stderr=subprocess.DEVNULL).decode().strip()
            git('init','-q')
            bodies={n:('SYNTHETIC '+n).encode() for n in b.EXPECTED}
            pins={n:(len(v),hashlib.sha256(v).hexdigest()) for n,v in bodies.items()}
            rows=[]
            for n,v in sorted(bodies.items()):rows.append('100644 blob '+git('hash-object','-w','--stdin',data=v)+'\t'+Path(n).name+'\n')
            tree=git('mktree',data=''.join(rows).encode())
            for name in ('snotel-sms','data','docs'):tree=git('mktree',data=f'040000 tree {tree}\t{name}\n'.encode())
            commit=git('commit-tree',tree,data=b'Synthetic root\n')
            with patch.object(b,'STAGING_SHA',commit),patch.object(b,'EXPECTED',pins):
                b.export_blobs(repo,commit,root/'candidate')
                self.assertEqual({n:(root/'candidate'/n).read_bytes() for n in pins},bodies)
                with self.assertRaises(ValueError):b.export_blobs(repo,'0'*40,root/'wrong')
                with self.assertRaises(ValueError):b.object_body(repo,'commit',git('rev-parse',commit+'^{tree}'))
                pins[next(iter(pins))]=(1,'0'*64)
                with self.assertRaises(ValueError):b.export_blobs(repo,commit,root/'bad')
                self.assertFalse((root/'bad').exists())
            child=git('commit-tree',tree,'-p',commit,data=b'Synthetic parent\n')
            with patch.object(b,'STAGING_SHA',child):
                with self.assertRaises(ValueError):b.export_blobs(repo,child,root/'child')
            with self.assertRaises(ValueError):b.absent(repo,commit)

    def test_workflow_order_and_security(self):
        w=json.loads(subprocess.check_output(['ruby','-ryaml','-rjson','-e',
            'puts JSON.generate(YAML.load_file(ARGV[0]))',str(ROOT/b.WORKFLOW)],text=True))
        self.assertEqual(set(w['on']),{'workflow_dispatch'});self.assertEqual(w['permissions'],{})
        jobs=w['jobs'];self.assertEqual(set(jobs),{'prepare-candidate','publish-to-main'})
        self.assertEqual(jobs['prepare-candidate']['permissions'],{'contents':'read'})
        self.assertEqual(jobs['publish-to-main']['permissions'],{'contents':'write'})
        self.assertEqual(jobs['publish-to-main']['concurrency'],dict(group='brim-live-main-publish',**{'cancel-in-progress':False,'queue':'max'}))
        self.assertEqual([jobs[x]['timeout-minutes'] for x in jobs],[10,15])
        for job in jobs.values():
            for text in ("github.event_name == 'workflow_dispatch'","github.ref == 'refs/heads/main'",'github.run_number == 1','github.run_attempt == 1'):
                self.assertIn(text,job['if'])
            self.assertEqual(job['steps'][0]['with']['ref'],'${{ github.sha }}')
            self.assertIn('snotel_retained_candidate.py admit',job['steps'][1]['run'])
        upload=jobs['prepare-candidate']['steps'][-1]['with']
        self.assertEqual(upload['retention-days'],2)
        self.assertEqual(upload['path'].splitlines(),['${{ runner.temp }}/snotel-retained-r11-candidate/candidate/'+n for n in b.ORDERED_ALLOWLIST]+['${{ runner.temp }}/snotel-retained-r11-candidate/candidate-metadata.json'])
        script=jobs['publish-to-main']['steps'][-1]['run']
        argv=['publish']+shlex.split(script.split('python3 scripts/main_publisher.py publish',1)[1].replace('\\\n',''))
        args=b.main_publisher.build_parser().parse_args(argv)
        self.assertEqual(args.allowlist,list(b.ORDERED_ALLOWLIST));self.assertEqual(args.product_id,b.s.PRODUCT)
        for k in ('candidate_validator','reconcile_callback','staged_validator'):
            self.assertEqual(getattr(args,k),'scripts/snotel_sms_publisher.py')
        raw=(ROOT/b.WORKFLOW).read_text()+(ROOT/'scripts/snotel_retained_candidate.py').read_text()
        for forbidden in ('urlopen(', 'requests.get(', 'snotel-prepare', 'snotel_first_run', 'Rscript', 'schedule:', 'curl ', 'wget '):
            self.assertNotIn(forbidden,raw)

    def test_candidate_refusals_before_science(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            for name in b.EXPECTED:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'wrong')
            with self.assertRaises(ValueError):b.verify_candidate(root)
            (root/'private-receipt').write_text('SYNTHETIC')
            with self.assertRaises(ValueError):b.verify_candidate(root)
            with self.assertRaises(ValueError):b.new_directory(root)
            link=root/'linked';link.symlink_to(root,target_is_directory=True)
            with self.assertRaises(ValueError):b.new_directory(link/'new')

    @unittest.skipUnless(os.environ.get('R11_ACCEPTED_CANDIDATE'), 'Private accepted input supplied only in commissioning')
    def test_original_candidate_ordered_metadata_and_unchanged_callbacks(self):
        # Original accepted bytes are provided externally, never tracked as a fixture.
        original=Path(os.environ['R11_ACCEPTED_CANDIDATE']);b.verify_candidate(original)
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            artifact=Path(tmp).resolve()/'artifact';artifact.mkdir();candidate=artifact/'candidate';shutil.copytree(original,candidate)
            meta=artifact/'candidate-metadata.json'
            b.main_publisher.prepare_metadata(argparse.Namespace(candidate_root=str(candidate),output=str(meta),
                product_id=b.s.PRODUCT,semantic_key_type='generation',semantic_key=b.GENERATION,
                source_event_sha='a'*40,allowlist=b.ORDERED_ALLOWLIST,owned_root=None))
            b.verify_artifact(artifact,'a'*40)
            self.assertEqual([x['path'] for x in json.loads(meta.read_text())['files']],list(b.ORDERED_ALLOWLIST))
            tree=artifact.parent/'transaction';tree.mkdir()
            result=b.publisher.reconcile(candidate,tree);self.assertEqual(result['decision'],'publish')
            b.publisher.validate_staged(candidate,tree)
            self.assertEqual(b.publisher.reconcile(candidate,tree)['decision'],'no-op')
            with self.assertRaises(b.main_publisher.PublisherError):b.verify_artifact(artifact,'b'*40)
            (tree/b.s.PATHS[0]).write_bytes(b'corrupt')
            with self.assertRaises(ValueError):b.publisher.validate_staged(candidate,tree)
            value=json.loads(meta.read_text());value['files'].reverse();meta.write_text(json.dumps(value))
            with self.assertRaises(b.main_publisher.PublisherError):b.verify_artifact(artifact,'a'*40)

if __name__=='__main__':unittest.main()
