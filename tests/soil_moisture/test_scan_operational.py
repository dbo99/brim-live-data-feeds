"""Finite C13R decision, actual workflow integration, and disclosure tests."""
import copy,datetime as dt,importlib.util,json,pathlib,subprocess,tempfile,unittest,sys,hashlib,shlex,os
ROOT=pathlib.Path(__file__).resolve().parents[2]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
g=module('guard','scripts/scan_operational_guard.py');runner=module('runner','scripts/run_scan_operational.py')

class FiniteTests(unittest.TestCase):
    def test_exact_history_endpoint_including_literal_date_range(self):
        self.assertTrue(g.valid_history_endpoint('actions/workflows/287560809/runs?created=2026-11-11..2026-11-20&per_page=100'))
        self.assertTrue(g.valid_history_endpoint('actions/runs/1234'))
        for bad in ('actions/runs/1234/attempts/2','actions/../secrets','actions/runs/0','https://example.invalid','actions/workflows/1/runs?per_page=100'):
            self.assertFalse(g.valid_history_endpoint(bad))
    def fixture(self,scheduled=False,day=13):
        now=g.stamp(f'2026-11-{day if scheduled else 11:02d}T14:35:00Z')
        sha='a'*40;pins={n:'b'*64 for n in g.CODE_PATHS}
        env=dict(GITHUB_ACTIONS='true',GITHUB_REPOSITORY=g.REPOSITORY,GITHUB_REF='refs/heads/main',GITHUB_REF_TYPE='branch',GITHUB_RUN_ATTEMPT='1',GITHUB_WORKFLOW_REF=g.REPOSITORY+'/'+g.WORKFLOW+'@refs/heads/main',GITHUB_RUN_ID='900',GITHUB_RUN_NUMBER='147',GITHUB_SHA=sha,GITHUB_EVENT_NAME='schedule' if scheduled else 'workflow_dispatch',SCAN_PROVIDER_HOLD='false',SCAN_ACQUIRE_ONLY='false',SCAN_SOURCE_CUTOFF_DATE='2026-11-10')
        current=dict(id=900,run_number=147,run_attempt=1,workflow_id=g.WORKFLOW_ID,repository={'id':g.REPOSITORY_ID},head_sha=sha,head_branch='main',event=env['GITHUB_EVENT_NAME'],created_at=now.strftime('%Y-%m-%dT%H:%M:%SZ'),status='in_progress',conclusion=None)
        policy=dict(approved=True,schema='scan-c13r-finite-1',kind='pilot' if scheduled else 'manual',code_sha256=pins,budgets=dict(g.BUDGETS),acquire_only=False)
        history=[current]
        if scheduled:
            policy.update(dates=[d.isoformat() for d in g.PILOT_DATES],expires_utc='2026-11-20T08:00:00Z',manual_run_id=800,manual_source_sha='c'*40,runtime_signature='d'*64)
            history.append(dict(current,id=800,event='workflow_dispatch',status='completed',conclusion='success',created_at='2026-11-11T17:00:00Z',head_sha='c'*40))
            for prev in range(13,day):history.append(dict(current,id=700+prev,created_at=f'2026-11-{prev:02d}T14:30:00Z',status='completed',conclusion='success'))
        else:policy.update(source_sha=sha,run_number=147,not_before_utc='2026-11-11T14:30:00Z',not_after_utc='2026-11-11T18:00:00Z',release_not_after_utc='2026-11-11T18:30:00Z',source_cutoff_date='2026-11-10')
        return [policy,env,now,pins,current,history]

    def test_manual_and_each_seven_dates(self):
        self.assertEqual(g.admit(*self.fixture())['status'],'ADMITTED')
        for d in range(13,20):self.assertEqual(g.admit(*self.fixture(True,d))['status'],'ADMITTED')

    def test_fail_closed_boundaries(self):
        mutations=[lambda a:a[0].update(approved=False),lambda a:a[0].update(budgets={}),lambda a:a[0].update(acquire_only='false'),lambda a:a[0].update(extra='private material'),lambda a:a[1].update(GITHUB_RUN_ATTEMPT='2'),lambda a:a[1].update(GITHUB_REF='refs/heads/other'),lambda a:a[1].update(GITHUB_ACTIONS='false'),lambda a:a[1].update(GITHUB_WORKFLOW_REF='wrong'),lambda a:a[1].update(SCAN_PROVIDER_HOLD=''),lambda a:a[4].update(head_sha='f'*40),lambda a:a[4].update(repository={'id':0}),lambda a:a[5].clear(),lambda a:a[5].append(dict(a[4]))]
        for scheduled in (False,True):
            for mutation in mutations:
                a=self.fixture(scheduled);mutation(a)
                with self.assertRaises(g.Refused):g.admit(*a)
        for key,value in [('source_sha','e'*40),('run_number',148),('source_cutoff_date','2026-11-11'),('not_after_utc','2026-11-11T14:34:00Z')]:
            a=self.fixture();a[0][key]=value
            with self.assertRaises(g.Refused):g.admit(*a)
        a=self.fixture();a[5].append(dict(a[4],id=901))
        with self.assertRaises(g.Refused):g.admit(*a)

    def test_pilot_failed_unknown_missing_duplicate_and_expired(self):
        for state in ('failure','cancelled',None):
            a=self.fixture(True,14);a[5][-1]['conclusion']=state
            with self.assertRaises(g.Refused):g.admit(*a)
        a=self.fixture(True,14);a[5].pop()
        with self.assertRaises(g.Refused):g.admit(*a)
        for day in (12,20):
            a=self.fixture(True);a[2]=g.stamp(f'2026-11-{day}T14:35:00Z');a[4]['created_at']=f'2026-11-{day}T14:30:00Z'
            with self.assertRaises(g.Refused):g.admit(*a)
        a=self.fixture(True);a[5].append(dict(a[4],id=901))
        with self.assertRaises(g.Refused):g.admit(*a)
        a=self.fixture(True);a[0]['runtime_signature']=''
        with self.assertRaises(g.Refused):g.admit(*a)

    def test_actual_code_pins_ignore_only_data_changes(self):
        expected=g.code_pins(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp).resolve()
            for name in g.CODE_PATHS:(root/name).parent.mkdir(parents=True,exist_ok=True);(root/name).write_bytes((ROOT/name).read_bytes())
            self.assertEqual(g.code_pins(root),expected)
            (root/'docs/data').mkdir(parents=True)
            def git(*args):
                return subprocess.check_output(['git','-c','user.name=Synthetic','-c','user.email=fixture@example.invalid','-C',str(root),*args],stderr=subprocess.DEVNULL,text=True).strip()
            git('init','-q');git('add','--','.');git('commit','-qm','Synthetic code baseline')
            for value in ('first data-only commit','second data-only commit'):
                (root/'docs/data/current.json').write_text(value);self.assertEqual(g.code_pins(root),expected)
                git('add','--','docs/data/current.json');git('commit','-qm',value)
                a=self.fixture(True);a[1]['GITHUB_SHA']=a[4]['head_sha']=git('rev-parse','HEAD');a[0]['code_sha256']=expected;a[3]=g.code_pins(root)
                self.assertEqual(g.admit(*a)['status'],'ADMITTED')
            (root/'scripts/run_scan_operational.R').write_text('changed')
            changed=g.code_pins(root);self.assertNotEqual(changed,expected)
            a=self.fixture(True);a[0]['code_sha256']=expected;a[3]=changed
            with self.assertRaises(g.Refused):g.admit(*a)
            (root/'scripts/run_scan_operational.R').unlink()
            (root/'scripts/run_scan_operational.R').symlink_to(ROOT/'scripts/run_scan_operational.R')
            with self.assertRaises(g.Refused):g.code_pins(root)

    def test_public_artifact_exact_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)/'artifact';(root/'candidate/docs/data').mkdir(parents=True)
            for name in runner.PRODUCTS:(root/'candidate/docs/data'/name).write_text('{}')
            (root/'candidate-metadata.json').write_text('{}');account=pathlib.Path(tmp)/'account.json'
            account.write_text(json.dumps({'status':'BUILT_ACCOUNT_COMPLETE','account_refusal':'NONE','account_predicate':True,'account':{'requests':29},'raw_provider_errors_disclosed':False}))
            runner.audit_candidate(root,account)
            for name in ('raw-provider-error.txt','private-c9-receipt.json','config.json'):
                bad=root/name;bad.write_text('SYNTHETIC_PRIVATE_SENTINEL')
                with self.assertRaises(AssertionError):runner.audit_candidate(root,account)
                bad.unlink()

    def test_workflow_actual_order_and_guards(self):
        raw=(ROOT/g.WORKFLOW).read_text();w=json.loads(subprocess.check_output(['ruby','-ryaml','-rjson','-e','puts JSON.generate(YAML.load_file(ARGV[0]))',str(ROOT/g.WORKFLOW)],text=True))
        prepare=w['jobs']['prepare-candidate'];pub=w['jobs']['publish-to-main'];steps=prepare['steps'];bodies=[x.get('run','') for x in steps]
        initial=next(i for i,b in enumerate(bodies) if 'guard.py initial' in b);qual=next(i for i,b in enumerate(bodies) if 'qualification.sh' in b);pre=next(i for i,b in enumerate(bodies) if 'guard.py pre-acquisition' in b);build=next(i for i,b in enumerate(bodies) if 'run_scan_operational.R' in b)
        self.assertLess(initial,qual);self.assertLess(qual,pre);self.assertLess(pre,build)
        self.assertEqual(prepare['permissions'],{'contents':'read','actions':'read'})
        self.assertEqual(pub['permissions'],{'contents':'write'})
        self.assertEqual(pub['concurrency'],{'group':'brim-live-main-publish','cancel-in-progress':False,'queue':'max'})
        self.assertNotIn('concurrency',w)
        self.assertEqual(prepare['concurrency']['group'],'scan-nrcs-finite-campaign')
        self.assertIn('--seconds 1200',bodies[build]);self.assertIn('SCAN_FETCH_TIMEOUT_SEC',g.BUDGETS)
        import re
        expected=['docs/data/'+p for p in runner.PRODUCTS]
        for job in (prepare,pub):
            body='\n'.join(s.get('run','') for s in job['steps']);self.assertEqual(re.findall(r'--allowlist (\S+)',body),expected)
        uploads=[x for x in steps if x.get('uses','').startswith('actions/upload-artifact')]
        self.assertEqual(len(uploads),2)
        self.assertNotIn('*',uploads[1]['with']['path']);self.assertNotIn('bounded',uploads[1]['with']['path'])
        for x in steps:
            if 'GH_TOKEN' in x.get('env',{}):self.assertIn('scan_operational_guard.py',x['run'])

    def test_actual_publisher_cli_metadata_order(self):
        publisher=module('publisher','scripts/main_publisher.py')
        w=json.loads(subprocess.check_output(['ruby','-ryaml','-rjson','-e','puts JSON.generate(YAML.load_file(ARGV[0]))',str(ROOT/g.WORKFLOW)],text=True))
        scripts={key:next(x['run'] for x in job['steps'] if 'scripts/main_publisher.py '+mode in x.get('run','')) for key,job,mode in [('prepare',w['jobs']['prepare-candidate'],'prepare'),('publish',w['jobs']['publish-to-main'],'publish')]}
        args={}
        for mode,body in scripts.items():
            cli=body.split('python3 scripts/main_publisher.py '+mode,1)[1].split('\n\n',1)[0]
            args[mode]=publisher.build_parser().parse_args([mode]+shlex.split(cli.replace('\\\n','')))
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp).resolve();candidate=root/'candidate';candidate.mkdir()
            for name in args['prepare'].allowlist:(candidate/name).parent.mkdir(parents=True,exist_ok=True);(candidate/name).write_text('SYNTHETIC')
            a=args['prepare'];a.candidate_root=str(candidate);a.output=str(root/'candidate-metadata.json');a.source_event_sha='a'*40;a.semantic_key='2026-11-11T00:00:00Z'
            publisher.prepare_metadata(a)
            values=dict(root=candidate,metadata_path=pathlib.Path(a.output),product_id=args['publish'].product_id,allowlist=args['publish'].allowlist,expected_source_sha='a'*40)
            publisher.validate_candidate_metadata(**values)
            with self.assertRaises(publisher.PublisherError):publisher.validate_candidate_metadata(**dict(values,expected_source_sha='b'*40))
            data=json.loads(pathlib.Path(a.output).read_text());data['files']=sorted(data['files'],key=lambda x:x['path']);pathlib.Path(a.output).write_text(json.dumps(data))
            with self.assertRaises(publisher.PublisherError):publisher.validate_candidate_metadata(**values)

    def test_bounded_child_failure_and_raw_log_nondisclosure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp).resolve();out=root/'bounded'
            self.assertEqual(runner.bounded([sys.executable,'-c','print("SYNTHETIC_PRIVATE_PROVIDER_ERROR");raise SystemExit(7)'],out,[root],5),7)
            self.assertIn('SYNTHETIC_PRIVATE_PROVIDER_ERROR',(out/'child.stdout').read_text())
            receipt=json.loads((out/'bounded.json').read_text());self.assertFalse(receipt['raw_logs_public']);self.assertEqual(receipt['remaining_owned_processes'],[])
            self.assertNotIn('SYNTHETIC_PRIVATE_PROVIDER_ERROR',json.dumps(receipt))

if __name__=='__main__':unittest.main()
