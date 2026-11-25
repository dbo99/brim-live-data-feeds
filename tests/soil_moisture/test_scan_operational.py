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

class RenewableTests(unittest.TestCase):
    def case(self, daily=False, ordinal=0, renew=False):
        # Straddle a calendar month with one slot arriving after UTC midnight.
        first = dt.date(2026,11,30)
        event = "schedule" if daily else "workflow_dispatch"
        manual_day = first - g.DAY
        day = first + ordinal*g.DAY if daily else manual_day
        clock = g.stamp(day.isoformat()+"T23:40:00Z")
        sha = "a"*40
        def row(n, kind, at, success=True):
            return dict(id=1000+n,run_number=n,run_attempt=1,workflow_id=g.WORKFLOW_ID,
                        repository={"id":g.REPOSITORY_ID},event=kind,head_branch="main",head_sha=sha,
                        created_at=at.isoformat().replace("+00:00","Z"),
                        status="completed" if success else "in_progress",conclusion="success" if success else None)
        def at(day):
            return g.stamp(day.isoformat()+"T23:35:00Z")
        manual = row(10,"workflow_dispatch",at(manual_day))
        anchor = row(11,"schedule",at(manual_day)) if renew else manual
        if renew:
            manual["created_at"] = "2026-11-10T22:30:00Z"
        if daily:
            prior = [row(anchor["run_number"]+i+1,"schedule",at(first+i*g.DAY)) for i in range(ordinal)]
            current = row(anchor["run_number"]+ordinal+1,event,at(day),False)
            history = [current,*prior,anchor]
        else:
            anchor = row(9,"schedule",at(manual_day-g.DAY))
            current = row(10,event,at(day),False)
            history = [current,anchor]
        env = FiniteTests().fixture()[1]
        env.update(GITHUB_EVENT_NAME=event,GITHUB_RUN_ID=str(current["id"]),
                   GITHUB_RUN_NUMBER=str(current["run_number"]),SCAN_SOURCE_CUTOFF_DATE=(day-g.DAY).isoformat())
        pins = {n:"b"*64 for n in g.CODE_PATHS}
        policy = dict(schema=g.RENEWABLE,approved=True,acquire_only=False,code_sha256=pins.copy(),
                      budgets=dict(g.BUDGETS),predecessor_run_id=anchor["id"])
        if daily:
            dates = [first+i*g.DAY for i in range(7)]
            policy.update(kind="daily",dates=[d.isoformat() for d in dates],window_minutes=840,
                          expires_utc=g.scheduled_window(dates[-1],840)[2].isoformat().replace("+00:00","Z"),
                          manual_run_id=manual["id"],manual_source_sha=sha,runtime_signature="d"*64)
        else:
            policy.update(kind="manual",source_sha=sha,run_number=current["run_number"],
                          not_before_utc="2026-11-29T23:20:00Z",not_after_utc="2026-11-30T02:20:00Z",
                          release_not_after_utc="2026-11-30T03:10:00Z",source_cutoff_date=(day-g.DAY).isoformat())
        return dict(policy=policy,env=env,now=clock,pins=pins,current=current,history=history,manual=manual,root=ROOT)

    def test_real_calendar_semantics_late_arrival_and_renewal(self):
        self.assertEqual(g.admit(**self.case())["source_cutoff_date"],"2026-11-28")
        for renew in (False,True):
            for slot in range(7):
                a = self.case(True,slot,renew)
                a["now"] += dt.timedelta(hours=2)
                a["current"]["created_at"] = (a["now"]-dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
                r = g.admit(**a)
                self.assertEqual(r["source_cutoff_date"],(dt.date(2026,11,29)+slot*g.DAY).isoformat())
        a = self.case(True)
        a["current"]["head_sha"] = a["env"]["GITHUB_SHA"] = "f"*40
        self.assertEqual(g.admit(**a)["status"],"ADMITTED")  # same pinned closure, advancing data

    def test_calendar_year_and_water_year_limits_follow_actual_accounting(self):
        self.assertEqual(g.supported_cutoff(dt.date(2028,1,1)),dt.date(2027,12,31))
        self.assertEqual(g.supported_cutoff(dt.date(2027,10,2)),dt.date(2027,10,1))
        for day,reason in ((dt.date(2027,10,1),"PRECEDES_CURRENT_WY"),(dt.date(2027,1,2),"TWO_YEAR_ACCOUNT"),
                           (dt.date(2028,2,29),"TWO_YEAR_ACCOUNT")):
            with self.assertRaisesRegex(g.Refused,reason):g.supported_cutoff(day)
        a=self.case(True)
        a["policy"]["dates"]=["2027-01-01","2027-01-02"]
        with self.assertRaisesRegex(g.Refused,"TWO_YEAR_ACCOUNT"):g.calendar(a["policy"],"schedule")

    def test_renewable_refusals(self):
        for field,updates in (
            ("policy",{"approved":False}),("policy",{"schema":"unknown"}),("policy",{"acquire_only":True}),
            ("policy",{"budgets":{}}),("policy",{"code_sha256":{}}),("policy",{"source_sha":"f"*40}),
            ("policy",{"run_number":11}),("policy",{"source_cutoff_date":"2026-11-29"}),
            ("policy",{"release_not_after_utc":"2026-11-30T03:20:01Z"}),
            ("env",{"GITHUB_RUN_ATTEMPT":"2"}),("env",{"GITHUB_REF":"refs/heads/other"}),
            ("env",{"SCAN_PROVIDER_HOLD":"true"}),("current",{"status":"completed"}),
            ("current",{"head_branch":"other"}),("policy",{"predecessor_run_id":True})):
            with self.subTest(field=field,updates=updates):
                a=self.case();a[field].update(updates)
                with self.assertRaises(g.Refused):g.admit(**a)
        for updates in ({"dates":[]},{"dates":["2026-11-30"]*8},{"dates":["2026-11-30","2026-12-02"]},
                        {"window_minutes":841},{"expires_utc":"2027-02-01T00:00:00Z"},
                        {"manual_source_sha":"f"*40},{"runtime_signature":""}):
            with self.subTest(updates=updates):
                a=self.case(True);a["policy"].update(updates)
                with self.assertRaises(g.Refused):g.admit(**a)
        a=self.case(True);a["now"]=g.stamp("2026-12-09T00:00:00Z")
        with self.assertRaises(g.Refused):g.admit(**a)
        a=self.case(True,1);a["history"][1]["conclusion"]="failure"
        with self.assertRaisesRegex(g.Refused,"FAILED_OR_UNKNOWN"):g.admit(**a)
        for conclusion in ("cancelled","skipped",None):
            a=self.case(True,0,True);a["history"][-1]["conclusion"]=conclusion
            with self.assertRaises(g.Refused):g.admit(**a)
        a=self.case(True,1);a["history"].pop(1)
        with self.assertRaisesRegex(g.Refused,"INCOMPLETE_SUCCESSION"):g.admit(**a)
        a=self.case(True,1);a["history"][1]["created_at"]=a["current"]["created_at"]
        with self.assertRaisesRegex(g.Refused,"DUPLICATE"):g.admit(**a)
        a=self.case(True,0,True);a["history"].append(dict(a["history"][-1],id=805,run_number=8))
        with self.assertRaisesRegex(g.Refused,"DUPLICATE_PREDECESSOR"):g.admit(**a)
        a=self.case(True);a["history"][0]=dict(a["current"],head_sha="f"*40)
        with self.assertRaisesRegex(g.Refused,"INCONSISTENT_RUN_HISTORY"):g.admit(**a)

    def test_fixed_github_routes_and_complete_history(self):
        days,_,_=g.calendar(self.case(True)["policy"],"schedule")
        self.assertTrue(g.valid_history_endpoint(g.history_path(days)))
        for url in ("pulls/1/files","pulls/0","actions/runs/1/jobs?per_page=100&filter=all",
                    g.history_path(days).replace("2026-11-29","2026-11-01"),
                    g.history_path(days).replace("2026-11-29","2026-13-30"),
                    g.history_path(days)+"&page=2"):
            self.assertFalse(g.valid_history_endpoint(url),url)
        for count,rows in [(2,[{"id":1}]),(2,[{"id":1},{"id":1}]),(101,[{"id":i} for i in range(101)])]:
            with self.assertRaises(g.Refused):g.complete_history({"total_count":count,"workflow_runs":rows},"workflow_runs")

    def test_changed_commissioned_or_predecessor_source_refuses(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        a=self.case(True,1,True)
        anchor=a["history"][-1];anchor["head_sha"]="e"*40
        def compare(args, **kwargs):
            return SimpleNamespace(returncode=int(args[3] == anchor["head_sha"]))
        with patch.object(g.subprocess,"run",side_effect=compare):
            with self.assertRaisesRegex(g.Refused,"COMMISSIONED_EXECUTABLE_CHANGED"):
                g.check_daily_sources(ROOT,a["env"]["GITHUB_SHA"],a["policy"],a["manual"],a["history"])

    def test_reviewed_merge_exactness(self):
        from unittest.mock import patch
        a=self.case();p=a["policy"];commit=p.pop("source_sha");candidate="b"*40
        p.update(source_pr=12,reviewed_head_sha=candidate)
        pr=dict(number=12,merged=True,merge_commit_sha=commit,head={"sha":candidate,"repo":{"id":g.REPOSITORY_ID}},
                base={"ref":"main","repo":{"id":g.REPOSITORY_ID}})
        for field,value in ((None,None),("merged",False),("merge_commit_sha","f"*40),("number",13),
                            ("base",{"ref":"other","repo":{"id":g.REPOSITORY_ID}}),
                            ("head",{"sha":candidate,"repo":{"id":1}}),("head",{"sha":"f"*40,"repo":{"id":g.REPOSITORY_ID}})):
            obj=pr if not field else dict(pr,**{field:value})
            with patch.object(g,"github",return_value=obj),patch.object(g.subprocess,"check_output",return_value=" ".join([commit,"c"*40,candidate])):
                if field:
                    with self.assertRaises(g.Refused):g.admit(**a)
                else:self.assertEqual(g.admit(**a)["status"],"ADMITTED")
        with patch.object(g,"github",return_value=pr),patch.object(g.subprocess,"check_output",return_value=commit+" "+"c"*40):
            with self.assertRaisesRegex(g.Refused,"MERGE_PARENTS"):g.admit(**a)

    def test_initial_refusal_never_forgives_a_spent_or_missing_receipt(self):
        from unittest.mock import patch
        a=self.case();current=a["current"]
        unused=dict(current,id=10095,run_number=10,event="schedule",status="completed",conclusion="failure",
                    created_at="2026-11-29T19:55:00Z")
        current["run_number"]=a["policy"]["run_number"]=11;a["env"]["GITHUB_RUN_NUMBER"]="11";a["history"].append(unused)
        steps={"Require finite manual or scheduled admission":"failure",
               "Recheck admission and reserve this campaign once":"skipped","Build SCAN soil moisture latest feed":"skipped"}
        for name,val in ((None,None),("Recheck admission and reserve this campaign once","success"),
                         ("Build SCAN soil moisture latest feed","failure"),("Require finite manual or scheduled admission","success")):
            modified=steps if name is None else dict(steps,**{name:val})
            job=dict(name="prepare-candidate",id=900,run_id=unused["id"],run_attempt=1,conclusion="failure",
                     steps=[dict(name=k,conclusion=v) for k,v in modified.items()])
            with patch.object(g,"github",return_value={"total_count":1,"jobs":[job]}),patch.object(g.subprocess,"check_output",return_value="b"*40):
                if name:
                    with self.assertRaisesRegex(g.Refused,"PRIOR_SPEND"):g.admit(**a)
                else:self.assertEqual(g.admit(**a)["status"],"ADMITTED")
        a=self.case(True,1);a["history"][1]["conclusion"]="failure"
        with patch.object(g,"initial_refusal",side_effect=AssertionError("A failed approved date must not be excused")):
            with self.assertRaises(g.Refused):g.admit(**a)


    def test_normal_entrypoint_both_phases_and_fail_closed_receipts(self):
        from contextlib import ExitStack
        from unittest.mock import patch
        from types import SimpleNamespace
        head = subprocess.check_output(["git","-C",str(ROOT),"rev-parse","HEAD"],text=True).strip()
        for scenario in ("manual","daily","absent","disabled","old_expired","short_fit","disk","memory",
                         "runtime","qualification","expired_during_lookup","incomplete_history"):
            daily = scenario in ("daily","runtime","old_expired")
            a = self.case(daily)
            p,env,current = a["policy"],a["env"],a["current"]
            env["GITHUB_SHA"] = current["head_sha"] = head
            p["code_sha256"] = g.code_pins(ROOT)
            runtime = dict(R="4.6.1",selected={k:{"version":v} for k,v in
                           [("soilDB","2.9.2"),("httr","1.4.9"),("curl","8.1.0")]},reused_platform_packages={})
            if daily:
                a["manual"]["head_sha"]=p["manual_source_sha"]=head
                p["runtime_signature"] = "f"*64 if scenario=="runtime" else g.runtime_signature(runtime)
            else:
                p["source_sha"]=head
            if scenario=="disabled":p["approved"]=False
            if scenario=="old_expired":p=self.case()["policy"] | {"schema":"scan-c13r-finite-1"}
            if scenario=="short_fit":
                p["not_after_utc"]="2026-11-30T00:55:00Z";p["release_not_after_utc"]="2026-11-30T01:05:00Z"
            expected=g.history_path(g.calendar(a["policy"],env["GITHUB_EVENT_NAME"])[0]) if scenario!="disabled" else None
            calls=[]
            def hosted(path):
                calls.append(path)
                if path==f"actions/runs/{current['id']}":return current
                if daily and path==f"actions/runs/{a['manual']['id']}":return a["manual"]
                if path==expected:return dict(total_count=len(a["history"])+int(scenario=="incomplete_history"),workflow_runs=a["history"])
                raise AssertionError(path)
            class Clock(dt.datetime):
                @classmethod
                def now(cls,tz=None):
                    if scenario=="expired_during_lookup" and calls:return a["now"]+dt.timedelta(days=1)
                    return a["now"]
            read_text=pathlib.Path.read_text
            def system_text(path,*args,**kwargs):
                if str(path)=="/proc/meminfo":return f"MemAvailable: {0 if scenario=='memory' else 8000000} kB"
                return read_text(path,*args,**kwargs)
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as tmp,ExitStack() as ctx:
                root=pathlib.Path(tmp)
                env.update(RUNNER_TEMP=tmp,GITHUB_ENV=str(root/"env"),GITHUB_OUTPUT=str(root/"output"))
                env["SCAN_DAILY_PILOT_JSON" if daily else "SCAN_LIVE_APPROVAL_JSON"]="" if scenario=="absent" else json.dumps(p)
                ctx.enter_context(patch.dict(os.environ,env,clear=True))
                ctx.enter_context(patch.object(pathlib.Path,"cwd",return_value=ROOT))
                ctx.enter_context(patch.object(pathlib.Path,"read_text",system_text))
                ctx.enter_context(patch.object(g.dt,"datetime",Clock))
                ctx.enter_context(patch.object(g,"github",side_effect=hosted))
                ctx.enter_context(patch.object(g.shutil,"disk_usage",return_value=SimpleNamespace(free=0 if scenario=="disk" else 20*1024**3)))
                control=root/"scan-control"
                for phase in ("initial","pre-acquisition"):
                    if phase=="pre-acquisition":
                        (control/"hosted-qualification.json").write_text(json.dumps(dict(status="FAIL" if scenario=="qualification" else "PASS",platform="linux",source_sha=head)))
                        (control/"runtime-lock.json").write_text(json.dumps(runtime))
                    should_fail=(scenario not in ("manual","daily","runtime","qualification") or
                                 phase=="pre-acquisition" and scenario in ("runtime","qualification"))
                    with patch.object(sys,"argv",["guard",phase]):
                        if should_fail:
                            with self.assertRaises(SystemExit):g.main()
                        else:g.main()
                    receipt=json.loads((control/f"approval-{phase}.json").read_bytes())
                    if should_fail:
                        self.assertNotEqual(receipt["status"],"ADMITTED")
                        self.assertFalse((control/"campaign-reservation.json").exists())
                        if scenario in ("absent","disabled","old_expired"):self.assertFalse(calls)
                        break
                    self.assertEqual(receipt["status"],"ADMITTED")
                    self.assertEqual(receipt["source_sha"],head)
                    if phase=="pre-acquisition":
                        self.assertEqual(receipt["runtime_signature"],g.runtime_signature(runtime))
                        self.assertEqual(json.loads((control/"campaign-reservation.json").read_bytes())["state"],"UNKNOWN_RESERVED_BEFORE_LAUNCH")
                        self.assertEqual(calls.count(expected),2)


if __name__=='__main__':unittest.main()
