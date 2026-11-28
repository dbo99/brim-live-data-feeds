"""Finite successor admission against local Git, synthetic products and saved run metadata."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_snotel_snapshot import build, fixture
import soil_network_workflow as w
import snotel_first_run as f


class SuccessorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name).resolve()
        self.git('init','-q')
        self.git('config','user.name','Fixture')
        self.git('config','user.email','fixture@example.invalid')
        self.git('remote','add','origin','https://github.com/'+f.REPO+'.git')
        groups, caps = fixture(end='2026-10-01')
        self.files = build(groups, caps)
        self.parent = self.commit_product(self.files)
        self.source = self.parent
        self.gen = json.loads(self.files[w.ss.PATHS[1]])['generation']
        self.policy = dict(version='snotel-successor-1', reviewed_head=self.source, event='workflow_dispatch',
            predecessor_commit=self.parent, predecessor_generation=self.gen, predecessor_run_id=f.ACCOUNTED_R9,
            first_run=3, first_start_utc='2026-10-03T18:11:00Z', first_cutoff='2026-10-02', slots=1,
            catalog_sha256='c'*64, roster_sha256='d'*64, limits=dict(f.LIMITS), python_version='3.12.x')
        self.env = dict(GITHUB_ACTIONS='true',GITHUB_REPOSITORY=f.REPO,GITHUB_REPOSITORY_ID='1247282299',
            GITHUB_REF='refs/heads/main',GITHUB_REF_TYPE='branch',GITHUB_WORKFLOW_REF=f.REPO+'/'+f.WORKFLOW+'@refs/heads/main',
            GITHUB_SHA=self.source,GITHUB_WORKFLOW_SHA=self.source,GITHUB_EVENT_NAME='workflow_dispatch',
            GITHUB_RUN_ATTEMPT='1',GITHUB_RUN_ID='900003',GITHUB_RUN_NUMBER='3',BOOTSTRAP='false',
            ACQUIRE='true',PUBLISH='true',RELEASE_APPROVED='true',PROVIDER_HOLD='false',
            CATALOG_PIN='c'*64,ROSTER_PIN='d'*64,WINDOW_END='2026-10-03T21:11:00Z',SCHEDULE=f.DAILY_CRON)
        self.clock=f.utc('2026-10-03T18:12:00Z')
        self.anchor=self.run_row(f.ACCOUNTED_R9,2,'workflow_dispatch','2026-10-02T18:12:00Z','completed','failure')
        self.runs=[self.anchor,self.run_row(900003,3,'workflow_dispatch','2026-10-03T18:11:30Z','in_progress',None)]
        self.active=[];self.ack=True;self.missing=False
        self.bind(self.gen, '2026-10-02')

    def git(self,*args):
        return subprocess.check_output(['git','--no-optional-locks',*args],cwd=self.repo,text=True,stderr=subprocess.DEVNULL).strip()

    def commit_product(self, files):
        for name, data in files.items():
            p=self.repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        self.git('add','docs');self.git('commit','-qm','Fixture product')
        return self.git('rev-parse','HEAD')

    def run_row(self,identity,number,event,created,status,conclusion):
        return dict(id=identity,run_number=number,run_attempt=1,workflow_id=f.OPERATING_WORKFLOW_ID,
                    head_branch='main',head_sha=self.source,event=event,created_at=created,status=status,conclusion=conclusion)

    def bind(self, generation, cutoff):
        raw=json.dumps(self.policy,sort_keys=True,separators=(',',':'))
        digest=hashlib.sha256(raw.encode()).hexdigest()
        self.env['NRCS_CLEARANCE']='/'.join([self.policy['event'],digest,self.env['GITHUB_RUN_NUMBER'],cutoff,generation,
            self.clock.strftime('%Y-%m-%dT%H:%M:%SZ'),self.clock.strftime('%Y-%m-%dT21:11:00Z')])
        self.env['SNOTEL_JOB_STARTED']=str(int(self.clock.timestamp())-60)
        return raw

    def get(self, path):
        if path=='git/ref/heads/main':return {'object':{'sha':self.source}}
        if path==f"actions/runs/{self.anchor['id']}":return self.anchor
        if path.startswith(f'actions/workflows/{f.OPERATING_WORKFLOW_ID}/runs?'):
            return {'total_count':len(self.runs)+int(self.missing),'workflow_runs':self.runs}
        if path.startswith('actions/runs?status='):
            return {'total_count':len(self.active),'workflow_runs':self.active}
        if '/jobs?' in path:
            return {'total_count':1,'jobs':[dict(id=812,name='publish-to-main',run_attempt=1,conclusion='success',
                steps=[dict(name='Authenticate committed delivery independently',conclusion='success' if self.ack else 'skipped')])]}
        self.fail('Unexpected GitHub read')

    def admit(self, phase='prepare', raw=None):
        with (patch.object(f.platform,'system',return_value='Linux'),
              patch.object(f.platform,'freedesktop_os_release',return_value={'ID':'ubuntu','VERSION_ID':'24.04'}),
              patch.object(f.platform,'python_version',return_value='3.12.3'),
              patch.object(sys,'version_info',(3,12,3))):
            return f.admit_successor(self.env,phase,json.dumps(self.policy,sort_keys=True,separators=(',',':')) if raw is None else raw,
                                     w.SnotelGitReader(self.repo),self.get,lambda:self.clock)

    def term_fixture(self):
        # The prior generation is an actual local Git publication descended from
        # a distinct source event. Give it a reproducible publication clock.
        import os
        self.git('checkout','-q','--orphan','term-fixture')
        self.git('rm','-qr','--cached','.')
        wf=self.repo/f.WORKFLOW
        wf.parent.mkdir(parents=True,exist_ok=True)
        wf.write_text('reviewed fixture workflow')
        self.git('add',f.WORKFLOW)
        self.git('commit','-qm','Source event')
        event=self.git('rev-parse','HEAD')
        with patch.dict(os.environ,GIT_COMMITTER_DATE='2026-10-02T20:05:00Z'):
            self.parent=self.commit_product(self.files)
        self.source=self.parent
        self.policy.update(version=f.TERM_VERSION,event='schedule',reviewed_head=self.source,
            predecessor_commit=self.parent,predecessor_run_id=900002,slots=4,python_version='3.12.3',
            daily_release_utc='06:00')
        self.policy.pop('first_run')
        self.clock=f.utc('2026-10-06T01:42:28Z')
        self.env.update(GITHUB_SHA=self.source,GITHUB_WORKFLOW_SHA=self.source,GITHUB_EVENT_NAME='schedule',
                        GITHUB_RUN_NUMBER='5',GITHUB_RUN_ID='900005',SNOTEL_JOB_STARTED=str(int(self.clock.timestamp())-3))
        self.anchor=self.run_row(900002,2,'workflow_dispatch','2026-10-02T18:12:00Z','completed','success')
        self.anchor['head_sha']=event
        self.runs=[self.anchor,self.run_row(900003,3,'schedule','2026-10-04T01:42:28Z','completed','skipped'),
                   self.run_row(900004,4,'schedule','2026-10-05T01:26:20Z','completed','skipped'),
                   self.run_row(900005,5,'schedule','2026-10-06T01:42:27Z','in_progress',None)]
        self.raw=json.dumps(self.policy,sort_keys=True,separators=(',',':'))
        self.env['NRCS_CLEARANCE']='schedule/term/'+hashlib.sha256(self.raw.encode()).hexdigest()
        prep=['Set up job','Validate explicit modes','Check out exact source event',
              'Qualify this runner offline before provider admission',f.TERM_STEP,f.BUILD_STEP,f.UPLOAD_STEP,
              'Post Check out exact source event','Complete job']
        pub=['Set up job','Record publisher job start','Check out candidate source event','Download closed candidate',
             'Check finite publication admission','Publish through the unchanged shared owner',
             'Authenticate committed delivery independently','Post Check out candidate source event','Complete job']
        def steps(names):
            return [dict(name=name,number=i+1,status='completed',conclusion='success',
                         started_at='2026-10-02T20:04:59Z',completed_at='2026-10-02T20:05:01Z')
                    for i,name in enumerate(names)]
        self.proofs={}
        for row in self.runs[:-1]:
            ok=row is self.anchor
            js=[dict(id=row['id']*10+i,name=name,run_id=row['id'],run_attempt=1,status='completed',
                     head_sha=row['head_sha'],head_branch='main',conclusion=row['conclusion'],
                     steps=steps(names) if ok else [])
                for i,(name,names) in enumerate([('prepare-candidate',prep),('publish-to-main',pub)])]
            if ok:js[0]['steps'][4]['conclusion']='skipped'
            self.proofs[row['id']]=dict(total_count=2,jobs=js)
        self.refusal_steps=steps(prep)
        for step,result in zip(self.refusal_steps[4:],['failure','skipped','skipped','success','success']):
            step['conclusion']=result
        initial_get=self.get
        def get(path):
            if '/jobs?' in path:return self.proofs[int(path.split('/')[2])]
            if path == 'actions/runs/900003':return self.runs[1]
            return initial_get(path)
        self.get=get

    def test_term_late_arrival_and_no_spend_bridge(self):
        from datetime import timedelta
        self.term_fixture()
        grant=self.admit()
        self.assertEqual((grant['cutoff'],grant['window_end'],grant['prior']['generation']),
                         ('2026-10-04','2026-10-06T06:00:00Z',self.gen))
        self.assertEqual(self.admit('publish'),grant)
        record=self.repo/'term.json'
        f.write(record,dict(grant=grant,run=self.env['GITHUB_RUN_ID'],admitted_at=self.clock.timestamp()))
        self.assertEqual(f.consume_term_admission(self.env,self.raw,record,self.clock),grant)
        for delta in (61,-1):
            with self.assertRaises(ValueError):f.consume_term_admission(self.env,self.raw,record,self.clock+timedelta(seconds=delta))
        for changes in ({'GITHUB_RUN_ID':'1'},{'NRCS_CLEARANCE':''},{'PROVIDER_HOLD':'true'}):
            with patch.dict(self.env,changes),self.assertRaises(ValueError):
                f.consume_term_admission(self.env,self.raw,record,self.clock)
        row=self.runs[2];prep=self.proofs[row['id']]['jobs'][0]
        row['conclusion']=prep['conclusion']='failure';prep['steps']=self.refusal_steps
        self.assertEqual(self.admit(),grant)
        for changes in ({'conclusion':'success'},{'conclusion':'cancelled'},{'status':'in_progress'}):
            with patch.dict(prep['steps'][5],changes),self.assertRaises(ValueError):self.admit()
        self.proofs[row['id']]['jobs'].pop()
        with self.assertRaises(ValueError):self.admit()

    def test_term_closed_ambiguous_or_duplicate(self):
        self.term_fixture()
        for changes in ({'NRCS_CLEARANCE':''},{'PROVIDER_HOLD':'true'},{'GITHUB_RUN_ATTEMPT':'2'},
                        {'GITHUB_RUN_NUMBER':'6'}):
            with patch.dict(self.env,changes),self.assertRaises(ValueError):self.admit()
        for when in ('2026-10-06T04:31:00Z','2026-10-06T07:00:00Z','2026-10-09T01:00:00Z'):
            with patch.object(self,'clock',f.utc(when)),self.assertRaises(ValueError):self.admit()
        for changes in ({'run_attempt':2},{'status':'in_progress'},{'conclusion':'failure'},
                        {'event':'workflow_dispatch'},{'run_number':3}):
            with patch.dict(self.runs[2],changes),self.assertRaises(ValueError):self.admit()
        for changes in ({'created_at':'2026-10-05T01:42:28Z'},{'head_sha':'f'*40}):
            with patch.dict(self.runs[-1],changes),self.assertRaises(ValueError):self.admit()
        self.missing=True
        with self.assertRaises(ValueError):self.admit()
        self.missing=False
        for key,val in [('predecessor_generation','f'*64),('predecessor_run_id',900003),('reviewed_head','f'*40),
                        ('predecessor_commit',self.anchor['head_sha']),('daily_release_utc','09:00')]:
            with patch.dict(self.policy,{key:val}):
                raw=json.dumps(self.policy,sort_keys=True,separators=(',',':'))
                with patch.dict(self.env,NRCS_CLEARANCE='schedule/term/'+hashlib.sha256(raw.encode()).hexdigest()):
                    with self.assertRaises(ValueError):self.admit()
        self.active=[dict(id=1,workflow_id=288862661)]
        with self.assertRaisesRegex(ValueError,'Shared NRCS'):self.admit()
        self.active=[]
        self.proofs[self.anchor['id']]['jobs'][1]['steps'].pop()
        with self.assertRaisesRegex(ValueError,'Incomplete acknowledged'):self.admit()

    def test_term_delivery_after_gap_and_source_drift(self):
        import os
        self.term_fixture()
        grant=self.admit()
        groups,caps=fixture(end=grant['cutoff'])
        child=build(groups,caps,end=grant['cutoff'],prior=self.files)
        prior=self.runs[-1];prior.update(status='completed',conclusion='success')
        with patch.dict(os.environ,GIT_COMMITTER_DATE='2026-10-06T01:55:00Z'):
            self.source=self.commit_product(child)
        import copy
        js=copy.deepcopy(self.proofs[self.anchor['id']])
        for j in js['jobs']:
            j.update(run_id=prior['id'],head_sha=prior['head_sha'])
            for s in j['steps']:
                s.update(conclusion='success',started_at='2026-10-06T01:54:59Z',completed_at='2026-10-06T01:55:01Z')
        self.proofs[prior['id']]=js
        self.clock=f.utc('2026-10-07T01:40:00Z')
        cur=self.run_row(900006,6,'schedule','2026-10-07T01:39:00Z','in_progress',None)
        self.runs.append(cur);self.env.update(GITHUB_SHA=self.source,GITHUB_WORKFLOW_SHA=self.source,
            GITHUB_RUN_NUMBER='6',GITHUB_RUN_ID='900006',SNOTEL_JOB_STARTED=str(int(self.clock.timestamp())-2))
        self.assertEqual(self.admit()['prior']['generation'],json.loads(child[w.ss.PATHS[1]])['generation'])
        publish=js['jobs'][1]['steps'][5]
        with patch.dict(publish,started_at='2026-10-06T01:55:01Z'),self.assertRaises(ValueError):self.admit()
        with patch.dict(prior,created_at=cur['created_at']),self.assertRaises(ValueError):self.admit()
        (self.repo/f.WORKFLOW).write_text('changed workflow')
        self.git('add',f.WORKFLOW);self.git('commit','-qm','Changed admission source')
        self.source=self.git('rev-parse','HEAD');cur['head_sha']=self.source
        self.env.update(GITHUB_SHA=self.source,GITHUB_WORKFLOW_SHA=self.source)
        with self.assertRaises(ValueError):self.admit()

    def test_operating_imports_are_independent(self):
        # Use the same two entry modules as the hosted job in a fresh interpreter.
        code = ("import sys; import snotel_first_run, soil_network_workflow; "
                "snotel_first_run.operating_paths(); "
                "assert not {'dendra_candidate', 'scan_soil_moisture_publisher'}.intersection(sys.modules)")
        subprocess.run([sys.executable, '-B', '-c', code], cwd=Path(f.__file__).parent, check=True)

    def test_dependency_changes_across_reviewed_event_and_fresh_main(self):
        unrelated = ('scripts/scan_soil_moisture_publisher.py', 'scripts/build_snow_pillow_latest.R',
                     'tests/soil_moisture/test_scan_failure_recording.py', 'scripts/dendra_candidate.py')
        relevant = (f.WORKFLOW, w.SNOTEL_CATALOG, 'scripts/soil_moisture/snotel_transport.py',
                    'scripts/main_publisher.py', 'scripts/soil_moisture/__init__.py',
                    'tests/soil_moisture/test_snotel_origin.py', 'tests/soil_moisture/test_snotel_snapshot.py')
        reader = w.SnotelGitReader(self.repo)
        original_get = self.get
        cases = [unrelated] + [(path,) for path in relevant] + [('docs/data/snotel-sms/extra.json',)]
        for paths in cases:
            with self.subTest(paths=paths):
                for name in paths:
                    path = self.repo/name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('fixture change')
                self.git('add', '--', *paths)
                self.git('commit', '-qm', 'Fixture change')
                child = self.git('rev-parse', 'HEAD')
                self.git('checkout', '-q', '--detach', self.parent)
                def get(path):
                    return {'object': {'sha': child}} if path == 'git/ref/heads/main' else original_get(path)
                with patch.object(self, 'get', get):
                    if paths == unrelated:
                        self.assertEqual(self.admit()['prior']['generation'], self.gen)
                        prior = f.check_product_lineage(reader, self.policy, child, 0, '2026-10-02')
                        self.assertEqual(prior['generation'], self.gen)
                    else:
                        with self.assertRaisesRegex(ValueError, 'Read-only Git object lookup failed'):
                            self.admit()
                        if paths[0] in relevant:
                            with self.assertRaisesRegex(ValueError, 'Read-only Git object lookup failed'):
                                f.check_product_lineage(reader, self.policy, child, 0, '2026-10-02')

    def test_manual_two_phase_and_refusals(self):
        grant=self.admit()
        self.assertEqual(grant,self.admit('publish'))
        self.assertEqual((grant['prior']['generation'],grant['source_sha'],grant['cutoff']),(self.gen,self.source,'2026-10-02'))
        for name,bad in [('GITHUB_SHA','f'*40),('ROSTER_PIN','0'*64),('GITHUB_RUN_NUMBER','4'),
                         ('GITHUB_RUN_ATTEMPT','2'),('NRCS_CLEARANCE',''),('PROVIDER_HOLD','true'),
                         ('RELEASE_APPROVED','false'),('BOOTSTRAP','true'),('WINDOW_END',''),
                         ('GITHUB_REF','refs/heads/test'),('SNOTEL_JOB_STARTED','1')]:
            with self.subTest(name=name),patch.dict(self.env,{name:bad}),self.assertRaises(ValueError):self.admit()
        for raw in ('','{}','{"x":1,"x":2}'):
            with self.assertRaises(ValueError):self.admit(raw=raw)
        for key,val in [('first_cutoff','2026-10-01'),('reviewed_head','0'*40),('predecessor_generation','0'*64),
                        ('slots',2),('limits',dict(f.LIMITS,attempts=33))]:
            with self.subTest(key=key),patch.dict(self.policy,{key:val}),self.assertRaises(ValueError):self.admit()
        with patch.object(self,'clock',f.utc('2026-10-03T21:12:00Z')),self.assertRaises(ValueError):self.admit()
        self.active=[dict(id=123,workflow_id=287560809)]
        with self.assertRaisesRegex(ValueError,'Shared NRCS'):self.admit()
        self.active=[];self.missing=True
        with self.assertRaisesRegex(ValueError,'Incomplete'):self.admit()
        self.missing=False;self.runs.append(dict(self.runs[-1],id=900099))
        with self.assertRaises(ValueError):self.admit()

    def test_prior_proof_cannot_be_replaced_by_absence(self):
        grant=self.admit()
        self.env['RUNNER_TEMP']=str(self.repo)
        proof=dict(source_sha=self.source,run_id=self.env['GITHUB_RUN_ID'],attempt='1',test_stub=False,
                   catalog_sha256=self.policy['catalog_sha256'],roster_sha256=self.policy['roster_sha256'],
                   stations=32,sensors=98,checked_at_utc=self.clock.strftime('%Y-%m-%dT%H:%M:%SZ'),
                   product_absent=False,prior=grant['prior'])
        path=self.repo/'snotel-real-reader-check.json'
        path.write_text(json.dumps(proof))
        self.assertEqual(f.require_source_proof(self.env,grant,self.clock),proof)
        for changes in ({'product_absent':True},{'prior':None},{'prior':dict(grant['prior'],commit='a'*40)}):
            path.write_text(json.dumps(dict(proof,**changes)))
            with self.assertRaises(ValueError):f.require_source_proof(self.env,grant,self.clock)

    def test_two_consecutive_daily_slots_require_verified_prior(self):
        self.policy.update(event='schedule',slots=2,python_version='3.12.3',predecessor_run_id=900002)
        self.anchor.update(id=900002,conclusion='success')
        self.runs[-1]['event']='schedule';self.env['GITHUB_EVENT_NAME']='schedule'
        self.bind(self.gen,'2026-10-02');self.admit()
        groups,caps=fixture(end='2026-10-02',mode='mixed')
        child=build(groups,caps,end='2026-10-02',prior=self.files)
        self.source=self.commit_product(child)
        self.env.update(GITHUB_SHA=self.source,GITHUB_WORKFLOW_SHA=self.source,GITHUB_RUN_NUMBER='4',GITHUB_RUN_ID='900004')
        self.clock=f.utc('2026-10-04T18:12:00Z')
        self.runs[-1].update(status='completed',conclusion='success')
        self.runs.append(self.run_row(900004,4,'schedule','2026-10-04T18:11:30Z','in_progress',None))
        with self.assertRaisesRegex(ValueError,'clearance'):self.admit()
        self.bind(json.loads(child[w.ss.PATHS[1]])['generation'],'2026-10-03')
        self.assertEqual(self.admit()['cutoff'],'2026-10-03')
        self.ack=False
        with self.assertRaisesRegex(ValueError,'acknowledgment'):self.admit()
        self.ack=True;self.runs[1]['conclusion']='failure'
        with self.assertRaisesRegex(ValueError,'Ambiguous'):self.admit()
        self.runs.pop(1)
        with self.assertRaisesRegex(ValueError,'history'):self.admit()

    def test_manual_restore_after_proved_unspent_scheduled_slot(self):
        skipped=self.run_row(800003,3,'schedule','2026-10-02T18:15:00Z','completed','skipped')
        self.runs.insert(1,skipped)
        self.runs[-1].update(id=900004,run_number=4)
        self.env.update(GITHUB_RUN_NUMBER='4',GITHUB_RUN_ID='900004')
        self.policy['first_run']=4;self.bind(self.gen,'2026-10-02')
        with self.assertRaisesRegex(ValueError,'No-spend'):self.admit()
        original=self.get
        def with_jobs(path):
            if path.startswith('actions/runs/800003/jobs?'):
                jobs=[dict(id=i,name=name,run_attempt=1,conclusion='skipped',steps=[])
                      for i,name in enumerate(('prepare-candidate','publish-to-main'))]
                return dict(total_count=2,jobs=jobs)
            return original(path)
        with patch.object(self,'get',with_jobs):
            self.assertEqual(self.admit()['cutoff'],'2026-10-02')
            skipped['conclusion']='failure'
            with self.assertRaisesRegex(ValueError,'Intervening'):self.admit()

    def test_stale_or_short_external_clearance(self):
        original=self.env['NRCS_CLEARANCE']
        for change in (original.replace('18:12:00Z','17:31:00Z'),original.replace('21:11:00Z','20:11:00Z')):
            with patch.dict(self.env,NRCS_CLEARANCE=change),self.assertRaisesRegex(ValueError,'Clearance validity'):
                self.admit()

    def test_existing_cutoff_and_ambiguous_predecessor_refuse(self):
        with self.assertRaisesRegex(ValueError,'already queried'):
            f.check_product_lineage(w.SnotelGitReader(self.repo),self.policy,self.source,0,'2026-10-01')
        self.anchor.update(id=900002);self.policy['predecessor_run_id']=900002
        self.bind(self.gen,'2026-10-02')
        with self.assertRaisesRegex(ValueError,'Unresolved prior spend'):self.admit()


if __name__=='__main__':
    unittest.main(verbosity=2)
