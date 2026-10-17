"""Phase B integration: fictional metadata, strict Git stub, mocked body opener only."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
import soil_network_workflow as w
from test_snotel_snapshot import fixture, build, doc
from test_snotel_transport import Response

s = w.ss
SOURCE = 'a'*40
PRIOR = 'b'*40
DELIVERED = 'c'*40
CLOCK = '2026-10-02T20:00:00Z'


class StrictGit:
    """Allow only enumerated read-only commands against explicit fixture objects."""
    def __init__(self, root, groups, prior=None):
        self.root = Path(root); self.root.mkdir()
        self.trees = {SOURCE:{}, PRIOR:{}}; self.blobs = {}; self.calls = []
        for name in w.SNOTEL_SOURCES:
            raw = (Path(w.__file__).resolve().parent.parent/name).read_bytes()
            self.add(SOURCE, name, raw)
            p = self.root/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(raw)
        roster = s.h.sha256(s.h.canonical(s.roster_binding(groups)))
        catalog = dict(version='snotel-sms-catalog-1', disclosure='SYNTHETIC_ONLY',
            roster_sha256=roster, groups=[dict(metadata_base64=base64.b64encode(g['metadata_body']).decode(),
                metadata_sha256=g['metadata_sha256'], sensor_identities=g['sensor_identities']) for g in groups])
        self.catalog = s.h.canonical(catalog); self.roster = roster
        self.add(SOURCE, w.SNOTEL_CATALOG, self.catalog)
        if prior:
            for name, raw in prior.items(): self.add(PRIOR, name, raw)

    def add(self, commit, name, raw, mode='100644'):
        oid = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        self.blobs[oid] = raw; self.trees.setdefault(commit, {})[name] = (mode, oid)

    def __call__(self, args):
        self.calls.append(args)
        if args == ['remote','get-url','origin']: return b'https://github.com/dbo99/brim-live-data-feeds.git\n'
        if args == ['rev-parse','HEAD']: return (SOURCE+'\n').encode()
        if args[:2] == ['cat-file','-t']:
            if args[2] not in self.trees: raise ValueError('Unknown commit')
            return b'commit\n'
        if args[:2] == ['rev-parse','--verify']:
            c=args[2][:-9]
            if not args[2].endswith('^{commit}') or c not in self.trees: raise ValueError('Unknown commit')
            return (c+'\n').encode()
        if args[:3] == ['ls-tree','-rz','--full-tree'] and len(args)==6 and args[4]=='--':
            c, path=args[3],args[5]
            return b''.join(f'{mode} blob {oid}\t{name}\0'.encode()
                for name,(mode,oid) in sorted(self.trees[c].items()) if name==path or name.startswith(path+'/'))
        if args[:2] == ['cat-file','-s']: return str(len(self.blobs[args[2]])).encode()
        if args[:2] == ['cat-file','blob']: return self.blobs[args[2]]
        raise AssertionError('Non-read-only/unexpected Git command: '+repr(args))

    def reader(self): return w.SnotelGitReader(self.root, git=self)

    def save(self, path):
        Path(path).write_text(json.dumps(dict(root=str(self.root), trees=self.trees,
            blobs={k:base64.b64encode(v).decode() for k,v in self.blobs.items()})))

    @classmethod
    def restore(cls, path):
        d=json.loads(Path(path).read_bytes()); obj=object.__new__(cls)
        obj.root=Path(d['root']);obj.trees=d['trees'];obj.calls=[]
        obj.blobs={k:base64.b64decode(v) for k,v in d['blobs'].items()}
        return obj


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='snotel-phaseb-test-',dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup); self.root=Path(self.temp.name)
        self.groups,self.captures=fixture(); self.prior=build(self.groups,self.captures)
        self.git=StrictGit(self.root/'repo',self.groups,self.prior);self.calls=[]
        self.queue=iter([c['body'] for c in self.captures])

    def opener(self, request, timeout):
        lane=self.runroot/f'station-{len(self.calls)+1:02d}'
        ledger=json.loads((lane/'ledger.json').read_bytes())
        self.assertEqual(ledger['attempts'][-1]['state'],'RESERVED')
        self.assertLessEqual(timeout,30)
        self.calls.append(request.full_url)
        value=next(self.queue)
        if isinstance(value,BaseException):raise value
        return value if isinstance(value,Response) else Response(value)

    def run_candidate(self, **changes):
        self.runroot=self.root/'run'
        args=dict(source_sha=SOURCE,prior_commit=PRIOR,
            catalog_sha256=s.h.sha256(self.git.catalog),roster_sha256=self.git.roster,
            acquire=True,budget=dict(w.SNOTEL_LIMITS),window_end='2026-10-02T23:00:00Z',
            opener=self.opener,clock=lambda:CLOCK,
            transport_wall=lambda:w.ops.time(CLOCK).timestamp())
        args.update(changes)
        return w.snotel_prepare(self.git.reader(),self.runroot,**args)

    def test_complete_32_98_and_closed_candidate(self):
        r=self.run_candidate()
        self.assertEqual(r['status'],'CANDIDATE_READY', (self.runroot/'failure.txt').read_text() if (self.runroot/'failure.txt').exists() else r)
        self.assertEqual((r['attempts'],len(self.calls),r['coverage']['positions']),(32,32,98))
        self.assertEqual(r['consumed_body_bytes'],sum(len(c['body']) for c in self.captures))
        self.assertEqual(r['coverage']['genuine_zeros'],98)
        self.assertFalse(r['science_changed']);self.assertIsNone(r['publication_acknowledgment'])
        files=s.read_product(self.runroot/'transfer/candidate')
        self.assertEqual(doc(files)['parent'],s._binding(self.prior[s.PATHS[0]],self.prior[s.PATHS[1]]))
        self.assertEqual(w.sp.classify(files,self.prior)['decision'],'publish')
        metadata=w.mp.validate_candidate_metadata(root=self.runroot/'transfer/candidate',
            metadata_path=self.runroot/'transfer/candidate-metadata.json', product_id=s.PRODUCT,
            allowlist=s.PATHS,expected_source_sha=SOURCE)
        self.assertEqual(metadata['semantic_key']['value'],r['candidate_generation'])
        self.assertTrue(all('beginDate=2026-10-01' in u and 'endDate=2026-10-01' in u for u in self.calls))
        self.assertEqual(sum(len(g['sensor_identities']) for g in self.groups),98)
        self.assertEqual(self.git.reader().product(PRIOR)[0],self.prior)

    def test_disabled_and_contradictory_modes(self):
        r=self.run_candidate(acquire=False)
        self.assertEqual((r['status'],r['attempts'],self.calls),('DISABLED',0,[]))
        self.assertFalse(list(self.runroot.glob('station-*')))
        with self.assertRaisesRegex(ValueError,'Publication requires'):
            w.snotel_prepare(self.git.reader(),self.root/'contradiction',source_sha=SOURCE,prior_commit=PRIOR,
                catalog_sha256='',roster_sha256='',publish=True)

    def test_no_completed_current_day(self):
        r=self.run_candidate(clock=lambda:'2026-10-01T08:00:00Z')
        self.assertEqual((r['status'],r['attempts'],r['source_check']),('NO_ELIGIBLE_INTERVAL',0,None))
        self.assertFalse(list(self.runroot.glob('station-*')))

    def test_missing_release_approval_and_synthetic_publish(self):
        r=self.run_candidate(opener=None)
        self.assertEqual((r['status'],r['attempts']),('HELD',0))
        self.runroot.rename(self.root/'held')
        r=self.run_candidate(publish=True)
        self.assertEqual((r['status'],r['attempts']),('HELD',0))

    def test_hosted_rerun_never_replaces_missing_lanes(self):
        with patch.dict(os.environ, GITHUB_RUN_ATTEMPT='2'):
            r=self.run_candidate()
        self.assertEqual((r['status'],r['attempts'],self.calls),('HELD',0,[]))
        self.assertFalse(list(self.runroot.glob('station-*')))

    def test_budget_and_headroom(self):
        for k in ('attempts','total_bytes','total_seconds','station_seconds'):
            with self.subTest(k=k):
                budget=dict(w.SNOTEL_LIMITS);budget[k]-=1
                r=self.run_candidate(budget=budget)
                self.assertEqual((r['status'],r['attempts']),('HELD',0))
                self.runroot.rename(self.root/('held-'+k))
        r=self.run_candidate(window_end='2026-10-02T20:33:59Z')
        self.assertEqual((r['status'],r['attempts']),('HELD',0))

    def test_source_catalog_and_partial_product_hold(self):
        self.git.add(PRIOR,s.PATHS[0],b'{}')
        r=self.run_candidate(bootstrap=True)
        self.assertEqual((r['status'],r['attempts']),('HELD',0))
        self.assertFalse(self.calls)

    def test_explicit_bootstrap_only(self):
        self.git.trees[PRIOR]={}
        r=self.run_candidate()
        self.assertEqual((r['status'],r['attempts']),('HELD',0))
        self.runroot.rename(self.root/'no-bootstrap')
        r=self.run_candidate(bootstrap=True)
        self.assertEqual(r['status'],'CANDIDATE_READY')
        self.assertIsNone(doc(s.read_product(self.runroot/'transfer/candidate'))['parent'])

    def test_failure_stops_before_next_and_no_resume(self):
        self.queue=iter([self.captures[0]['body'],urllib.error.URLError('synthetic failure')])
        r=self.run_candidate()
        self.assertEqual((r['status'],r['attempts'],len(self.calls)),('HELD',2,2))
        self.assertFalse((self.runroot/'transfer').exists())
        self.assertEqual(self.git.reader().product(PRIOR)[0],self.prior)
        with self.assertRaisesRegex(ValueError,'never reset'):self.run_candidate()
        with self.assertRaisesRegex(ValueError,'Missing/ambiguous'):
            w.snotel_rebuild(self.git.reader(),self.runroot,self.root/'rebuilt',
                run_sha256=r['run_sha256'],capture_manifest_sha256='0'*64,clock=lambda:CLOCK)

    def test_redirect_retry_after_and_schema_failure(self):
        for label,response in [
            ('redirect',Response(b'',302,{'Location':'https://invalid.example/never'})),
            ('backoff',Response(self.captures[0]['body'],200,{'Retry-After':'60'})),
            ('schema',Response(b'{}')),
            ('overflow',Response(b'X'*(1048576+1)))]:
            with self.subTest(label=label):
                self.calls=[];self.queue=iter([response])
                r=self.run_candidate()
                self.assertEqual((r['status'],r['attempts'],len(self.calls)),('HELD',1,1))
                self.assertFalse((self.runroot/'transfer').exists())
                self.runroot.rename(self.root/label)

    def test_year_boundary_between_stations(self):
        times=iter([CLOCK,CLOCK,'2027-10-01T08:00:00Z','2027-10-01T08:00:00Z'])
        r=self.run_candidate(clock=lambda:next(times))
        self.assertEqual((r['status'],r['attempts']),('HELD',1))

    def test_nonnumeric_retains_real_last_good_and_qa_revision(self):
        for mode in ('withheld','missing','empty','numeric'):
            with self.subTest(mode=mode):
                _,caps=fixture(mode=mode,revision=1)
                self.calls=[];self.queue=iter([c['body'] for c in caps])
                r=self.run_candidate()
                self.assertEqual(r['status'],'CANDIDATE_READY')
                if mode!='numeric':
                    self.assertEqual(r['coverage']['retained_after_nonnumeric'],98)
                    self.assertEqual(r['coverage']['genuine_zeros'],98)
                    self.assertFalse(r['science_changed'])
                self.runroot.rename(self.root/mode)

    def test_fresh_process_rebuild_and_tamper(self):
        r=self.run_candidate();self.assertEqual(r['status'],'CANDIDATE_READY')
        stub=self.root/'stub.json';self.git.save(stub)
        code="from test_snotel_workflow import *;g=StrictGit.restore(sys.argv[1]);w.snotel_rebuild(g.reader(),sys.argv[2],sys.argv[3],run_sha256=sys.argv[4],capture_manifest_sha256=sys.argv[5],clock=lambda:CLOCK)"
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
            str(Path(__file__).parent),str(Path(w.__file__).parent)]))
        result=subprocess.run([sys.executable,'-B','-c',code,str(stub),str(self.runroot),
            str(self.root/'restored'),r['run_sha256'],r['capture_manifest_sha256']],env=env,capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(s.read_product(self.root/'restored/candidate'),s.read_product(self.runroot/'transfer/candidate'))
        (self.runroot/'completed-01.json').write_text('{}')
        with self.assertRaises((ValueError,KeyError)):
            w.snotel_rebuild(self.git.reader(),self.runroot,self.root/'bad-restore',
                run_sha256=r['run_sha256'],capture_manifest_sha256=r['capture_manifest_sha256'],clock=lambda:CLOCK)
        self.assertEqual(len(self.calls),32)

    def test_committed_acknowledgment_and_mismatch(self):
        r=self.run_candidate();self.assertEqual(r['status'],'CANDIDATE_READY')
        files=s.read_product(self.runroot/'transfer/candidate')
        for name,raw in files.items():self.git.add(DELIVERED,name,raw)
        ack=w.snotel_acknowledge(self.git.reader(),DELIVERED,self.runroot/'transfer/candidate',clock=lambda:CLOCK)
        self.assertEqual(ack['commit'],DELIVERED)
        with self.assertRaisesRegex(ValueError,'differs'):
            w.snotel_acknowledge(self.git.reader(),PRIOR,self.runroot/'transfer/candidate',clock=lambda:CLOCK)
        for c in (DELIVERED,PRIOR):
            self.assertIn(['cat-file','-t',c],self.git.calls)

    def test_untrusted_commit_source_and_blob(self):
        reader=self.git.reader()
        with self.assertRaises(ValueError):reader.product('0'*40)
        with self.assertRaises(ValueError):reader.product('main')
        (self.git.root/w.SNOTEL_SOURCES[0]).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'Effective source'):reader.source(SOURCE)
        entry=self.git.trees[PRIOR][s.PATHS[0]]
        self.git.blobs[entry[1]]=b'{}'
        with self.assertRaisesRegex(ValueError,'identity'):reader.product(PRIOR)

    def test_committed_tree_closure_and_bounds(self):
        reader=self.git.reader()
        for name,mode in [('docs/data/snotel-sms/extra.json','100644'),(s.PATHS[0],'120000')]:
            before=copy.deepcopy(self.git.trees[PRIOR])
            self.git.add(PRIOR,name,b'{}',mode)
            with self.assertRaises(ValueError):reader.product(PRIOR)
            self.git.trees[PRIOR]=before
        oid=self.git.trees[PRIOR][s.PATHS[1]][1]
        real=self.git
        def huge(args):
            if args==['cat-file','-s',oid]:return str(s.MAX_PRODUCT+1).encode()
            return real(args)
        with self.assertRaisesRegex(ValueError,'size limit'):
            w.SnotelGitReader(self.git.root,git=huge).product(PRIOR)
        self.assertNotIn(['cat-file','blob',oid],self.git.calls)

    def test_existing_network_publisher_boundaries(self):
        for network in ('scan','dendra'):
            args=w.publisher_args(network,self.git.root,self.root/'transfer')
            self.assertIn('scripts/main_publisher.py',' '.join(args))
        with self.assertRaises(ValueError):w.publisher_args('snotel',self.git.root,self.root/'transfer')

    def test_prior_incompatibility_is_zero_attempt(self):
        g,c=fixture(year=2025)
        old=build(g,c,year=2025)
        for name,raw in old.items():self.git.add(PRIOR,name,raw)
        r=self.run_candidate()
        self.assertEqual((r['status'],r['attempts'],self.calls),('HELD',0,[]))
        self.runroot.rename(self.root/'old-wy')
        g,c=fixture(end='2026-10-02')
        newer=build(g,c,end='2026-10-02')
        for name,raw in newer.items():self.git.add(PRIOR,name,raw)
        r=self.run_candidate()
        self.assertEqual((r['status'],r['attempts'],self.calls),('HELD',0,[]))

    def test_oversized_native_field_stops_first_station(self):
        b=s.h._json(self.captures[0]['body']);b[0]['data'][0]['values'][0]['origValue']=10**33
        self.queue=iter([s.h.canonical(b)])
        r=self.run_candidate()
        self.assertEqual((r['status'],r['attempts'],len(self.calls)),('HELD',1,1))
        self.assertIn('numeric representation',(self.runroot/'failure.txt').read_text())

    def test_preopener_boundary_after_reservation(self):
        count=[0]
        epoch=w.ops.time(CLOCK).timestamp()
        def wall():
            count[0]+=1
            return epoch if count[0] <= 2 else w.ops.time('2027-10-01T08:00:00Z').timestamp()
        r=self.run_candidate(transport_wall=wall)
        self.assertEqual(r['status'],'HELD')
        self.assertEqual(self.calls,[])
        self.assertLessEqual(r['attempts'],1)

    def test_unknown_consumption_and_interrupted_reservation(self):
        def boom(response,count):raise ConnectionError('synthetic interrupted read')
        self.queue=iter([Response(self.captures[0]['body'],read_hook=boom)])
        r=self.run_candidate()
        self.assertEqual((r['status'],r['attempts'],len(self.calls)),('HELD',1,1))
        self.assertIsNone(r['consumed_body_bytes'])
        self.assertFalse((self.runroot/'transfer').exists())

    def test_later_saved_rebuild_is_new_provenance_without_acquisition(self):
        r=self.run_candidate()
        d=w.snotel_rebuild(self.git.reader(),self.runroot,self.root/'later',
            run_sha256=r['run_sha256'],capture_manifest_sha256=r['capture_manifest_sha256'],
            clock=lambda:'2026-10-02T21:00:00Z')
        original=doc(s.read_product(self.runroot/'transfer/candidate'))
        self.assertNotEqual(d['generation'],original['generation'])
        self.assertEqual(d['sensors'],original['sensors'])
        self.assertEqual(d['evidence'],original['evidence'])
        self.assertEqual(len(self.calls),32)
        with self.assertRaisesRegex(ValueError,'outside source'):
            w.snotel_rebuild(self.git.reader(),self.runroot,self.git.root/'new',
                run_sha256=r['run_sha256'],capture_manifest_sha256=r['capture_manifest_sha256'])

    def test_callbacks_preserve_unrelated_and_stale_ack(self):
        r=self.run_candidate();self.assertEqual(r['status'],'CANDIDATE_READY')
        transfer=self.runroot/'transfer';tree=self.root/'main-context';tree.mkdir()
        for name,raw in self.prior.items():
            p=tree/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        other=tree/'docs/data/other.json';other.write_bytes(b'UNRELATED_NEWER')
        env=dict(BRIM_PUBLISH_PRODUCT_ID=s.PRODUCT,BRIM_PUBLISH_FIXED_PATHS=json.dumps(list(s.PATHS)),
            BRIM_PUBLISH_OWNED_ROOTS='[]',BRIM_PUBLISH_CANDIDATE_ROOT=str(transfer/'candidate'),
            BRIM_PUBLISH_WORKTREE=str(tree),BRIM_PUBLISH_METADATA=str(transfer/'candidate-metadata.json'),
            BRIM_PUBLISH_RESULT=str(self.root/'callback.json'))
        for phase in ('validate-candidate','reconcile','validate-staged'):
            self.assertEqual(w.snotel_callback(dict(env,BRIM_PUBLISH_PHASE=phase),clock=lambda:CLOCK),0)
        self.assertEqual(other.read_bytes(),b'UNRELATED_NEWER')
        with self.assertRaisesRegex(ValueError,'boundary'):
            w.snotel_callback(dict(env,BRIM_PUBLISH_PHASE='validate-staged'),clock=lambda:'2027-10-01T08:00:00Z')
        files=s.read_product(transfer/'candidate')
        _,caps=fixture(revision=1)
        for cap in caps:
            cap['receipt']['retrieved_at_utc']=CLOCK
            cap['receipt_sha256']=s.h.sha256(s.h.canonical(cap['receipt']))
        descendant=build(self.groups,caps,prior=files)
        for name,raw in descendant.items():self.git.add(DELIVERED,name,raw)
        ack=w.snotel_acknowledge(self.git.reader(),DELIVERED,transfer/'candidate',clock=lambda:CLOCK)
        self.assertEqual(ack['status'],'STALE_NO_OP_NEWER_PRODUCT_PRESERVED')
        self.assertFalse(ack['acknowledged'])

    def test_synthetic_catalog_cannot_be_relabeled_for_real(self):
        c=s.h._json(self.git.catalog);c['disclosure']='RELEASE_APPROVED_PUBLIC'
        raw=s.h.canonical(c);self.git.add(SOURCE,w.SNOTEL_CATALOG,raw)
        with self.assertRaisesRegex(ValueError,'Synthetic identities'):
            w.snotel_catalog(self.git.reader(),SOURCE,s.h.sha256(raw),self.git.roster,synthetic=False)

    def test_selected_execution_checkout_bound(self):
        reader=self.git.reader();reader.test_stub=False
        with self.assertRaisesRegex(ValueError,'Executing source checkout'):
            reader.source(SOURCE)

    def test_synthetic_native_revision_is_not_query_only(self):
        b=s.h._json(self.captures[0]['body'])
        b[0]['data'][0]['values'][0]['qaFlag']='SYNTHETIC_REVISION'
        self.queue=iter([s.h.canonical(b)]+[c['body'] for c in self.captures[1:]])
        r=self.run_candidate()
        self.assertEqual(r['status'],'CANDIDATE_READY')
        self.assertEqual(r['coverage']['admission_counts']['ACCEPTED_REVISION'],1)
        self.assertTrue(r['science_changed'])

    def test_os_network_denial_fresh_process(self):
        code="""import socket,errno
try:
 s=socket.socket();s.connect(('127.0.0.1',9))
except OSError as e:
 assert e.errno in (errno.EPERM,errno.EACCES),e
 print('OS_NETWORK_DENIED')
else:raise AssertionError('OS network denial missing')
"""
        result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'OS_NETWORK_DENIED')

    def test_workflow_structure_and_shell_syntax(self):
        path=Path(w.__file__).parent.parent/'.github/workflows/build-snotel-sms-current-wy.yml'
        raw=path.read_text()
        try:
            import yaml
            d=yaml.load(raw,Loader=yaml.BaseLoader)
        except ModuleNotFoundError:
            parsed=subprocess.run(['ruby','-ryaml','-rjson','-e',
                'puts JSON.generate(YAML.load_file(ARGV[0]))',str(path)],capture_output=True,text=True,check=True)
            def strings(v):
                if isinstance(v,dict):return {k:strings(x) for k,x in v.items()}
                if isinstance(v,list):return [strings(x) for x in v]
                if isinstance(v,bool):return str(v).lower()
                if isinstance(v,int):return str(v)
                return v
            d=strings(json.loads(parsed.stdout));d['on']=d.pop('true',d.get('on'))
        self.assertEqual(set(d['on']),{'workflow_dispatch'})
        inputs=d['on']['workflow_dispatch']['inputs']
        self.assertEqual([inputs[n]['default'] for n in ('acquire','publish','bootstrap')],['false']*3)
        self.assertEqual(d['permissions'],{});self.assertNotIn('concurrency',d)
        jobs=d['jobs'];self.assertEqual(set(jobs),{'prepare-candidate','publish-to-main'})
        prepare,publish=jobs.values()
        self.assertEqual(prepare['permissions'],{'contents':'read'})
        self.assertEqual(publish['permissions'],{'contents':'write'})
        self.assertEqual(publish['concurrency'],{'group':'brim-live-main-publish','cancel-in-progress':'false','queue':'max'})
        self.assertIn("github.ref == 'refs/heads/main'",publish['if'])
        self.assertIn("candidate_ready == 'true'",publish['if'])
        for job in jobs.values():
            self.assertLessEqual(int(job['timeout-minutes']),60)
            for step in job['steps']:
                if step.get('uses')=='actions/checkout@v6':self.assertEqual(step['with']['ref'],'${{ github.sha }}')
                if 'run' in step:
                    result=subprocess.run(['/bin/bash','-n'],input=step['run'],text=True,capture_output=True)
                    self.assertEqual(result.returncode,0,result.stderr)
        upload=next(x for x in prepare['steps'] if x.get('uses')=='actions/upload-artifact@v7')
        paths=upload['with']['path'].splitlines()
        self.assertEqual(len(paths),3)
        self.assertEqual([p.split('/transfer/')[1] for p in paths],
                         ['candidate/'+n for n in s.PATHS]+['candidate-metadata.json'])
        self.assertNotIn('pip install',raw);self.assertNotIn('schedule:',raw)
        self.assertIn('$GITHUB_RUN_ATTEMPT',raw)
