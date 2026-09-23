"""No Git commits, no publisher publish, no real network calls."""
import copy,io,json,os,sys,tarfile,tempfile,unittest,urllib.error
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/dendra'))
import dendra_candidate as adapter
from bridge import BudgetClient,FetchError,classify,public,collect,dump
from dendra_workflow import restore
from datetime import datetime,timezone

class PureAdapterTests(unittest.TestCase):
    def index(self,**overrides):
        d={'generation':'g2','parent_generation':'g1','as_of_utc':'2026-09-20T12:00:00Z','complete_through_date':'2026-09-19','policy_version':'v1','streams':[{'datastream_id':'a'}]};d.update(overrides);return d
    def test_first_candidate(self):self.assertEqual(adapter.reconcile_decision(self.index(),None)['decision'],'publish')
    def test_same_generation(self):
        x=self.index();self.assertEqual(adapter.reconcile_decision(x,copy.deepcopy(x))['candidate_state'],'same')
    def test_same_generation_conflict(self):
        with self.assertRaises(ValueError):adapter.reconcile_decision(self.index(),self.index(policy_version='different'))
    def test_stale_candidate(self):self.assertEqual(adapter.reconcile_decision(self.index(as_of_utc='2026-09-19T12:00:00Z'),self.index(generation='newer'))['candidate_state'],'stale')
    def test_new_descendant(self):self.assertEqual(adapter.reconcile_decision(self.index(),self.index(generation='g1'))['decision'],'publish')
    def test_race_advances_current_same_cutoff(self):
        candidate=self.index();current=self.index(generation='g1')
        self.assertEqual(adapter.reconcile_decision(candidate,current)['decision'],'publish')
        with self.assertRaisesRegex(ValueError,'Stale base'):adapter.reconcile_decision(candidate,self.index(generation='racer'))
    def test_selection_deletion_requires_migration(self):
        with self.assertRaises(ValueError):adapter.reconcile_decision(self.index(streams=[]),self.index(generation='g1'))
    def test_old_index_stalls_at_read_time(self):
        x={'mode':'live','expires_at_utc':'2026-09-20T00:00:00Z'}
        self.assertEqual(adapter.freshness(x,datetime(2026,9,24,tzinfo=timezone.utc)),'stalled-feed')
    def test_replay_never_current(self):self.assertEqual(adapter.freshness({'mode':'replay'}),'frozen-replay')
    def test_shared_ownership_rejects_broad_root(self):
        with self.assertRaises(adapter.shared.PublisherError):adapter.shared._normalize_owned_roots(['docs/data/dendra'],fixed_paths=adapter.FIXED)
    def test_shared_prepare_and_hash_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'candidate';p=root/adapter.FIXED[0];adapter.write(p,{'fixture':True})
            args=adapter.SimpleNamespace(candidate_root=str(root),output=str(root.parent/'candidate-metadata.json'),product_id=adapter.PRODUCT,semantic_key_type='dendra_state_generation',semantic_key='g1',source_event_sha='a'*40,allowlist=adapter.FIXED,owned_root=adapter.OWNED)
            adapter.shared.prepare_metadata(args)
            kwargs=dict(root=root,metadata_path=Path(args.output),product_id=adapter.PRODUCT,allowlist=adapter.FIXED,owned_roots=adapter.OWNED,expected_source_sha='a'*40)
            adapter.shared.validate_candidate_metadata(**kwargs);p.write_text('tampered')
            with self.assertRaises(adapter.shared.PublisherError):adapter.shared.validate_candidate_metadata(**kwargs)
    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'candidate';root.mkdir();(root/'bad').symlink_to('/tmp')
            with self.assertRaises(adapter.shared.PublisherError):adapter.shared._candidate_inventory(root)
    def test_undeclared_path_rejected(self):
        with self.assertRaises(adapter.shared.PublisherError):adapter.shared._validate_desired_inventory(adapter.FIXED+['docs/data/scan.csv'],fixed_paths=adapter.FIXED,owned_roots=adapter.OWNED)

class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.station={'_id':'1'*24,'is_hidden':False,'access_levels_resolved':{'public_level':3}}
        self.meta={'_id':'2'*24,'station_id':'1'*24,'is_hidden':False,'access_levels_resolved':{'public_level':3},'terms':{'ds':{'Variable':'Temperature','Medium':'Soil','Aggregate':'Average'},'dt':{'Unit':'DegreeCelsius'}},'attributes':{'depth':{'value':200,'unit_tag':'dt_Unit_Millimeter'},'orientation':'horizontal'}}
        self.units={'DegreeCelsius':{'label':'DegreeCelsius','abbreviation':'degC'}}
    def test_exact_depth_units_and_identity(self):
        r=classify(self.meta,self.station,self.units);self.assertEqual(r['depth_cm'],20);self.assertEqual(r['datastream_id'],'2'*24);self.assertTrue(r['processing_eligible'])
    def test_orientation_cannot_invent_depth(self):
        self.meta['attributes']={'orientation':'vertical'};self.assertIsNone(classify(self.meta,self.station,self.units)['depth_cm'])
    def test_same_depth_sensors_remain_distinct(self):
        r1=classify(self.meta,self.station,self.units);self.meta['_id']='3'*24;r2=classify(self.meta,self.station,self.units);self.assertEqual(r1['depth_cm'],r2['depth_cm']);self.assertNotEqual(r1['datastream_id'],r2['datastream_id'])
    def test_unknown_unit(self):
        self.meta['terms']['dt']['Unit']='Dimensionless';self.assertFalse(classify(self.meta,self.station,self.units)['processing_eligible'])
    def test_hidden_and_low_access(self):
        self.meta['is_hidden']=True;self.assertFalse(classify(self.meta,self.station,self.units)['processing_eligible']);self.assertFalse(public({'public_level':1,'is_hidden':False}))
    def test_cumulative_precip_is_discovery_only(self):
        self.meta['terms']={'ds':{'Variable':'Precipitation','Medium':'Water','Aggregate':'Cumulative'},'dt':{'Unit':'Millimeter'}};r=classify(self.meta,self.station,self.units);self.assertEqual(r['precipitation_semantics'],'cumulative_counter');self.assertFalse(r['processing_eligible'])

class BudgetTests(unittest.TestCase):
    def test_budget_counts_before_request_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Path(tmp)/'ledger.json'
            def fail(*args,**kwargs):raise urllib.error.URLError('fixture outage')
            client=BudgetClient(ledger,1,opener=fail)
            with self.assertRaises(urllib.error.URLError):client.open(__import__('urllib.request',fromlist=['Request']).Request('https://api.dendra.science/v2/datapoints'),1)
            self.assertEqual(len(json.loads(ledger.read_text())),1)
            client=BudgetClient(ledger,1,opener=fail)
            with self.assertRaisesRegex(FetchError,'budget'):client.open(__import__('urllib.request',fromlist=['Request']).Request('https://api.dendra.science/v2/datapoints'),1)
    def test_circuit_stops_repeated_service_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Path(tmp)/'ledger.json';dump(ledger,[{'service_failure':True},{'service_failure':True}]);client=BudgetClient(ledger,opener=lambda *a: self.fail('must not request'))
            with self.assertRaisesRegex(FetchError,'Circuit'):client.metadata('stations/x')
    def test_partial_one_stream_failure_not_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sid='a'*24;station={'station_id':'b'*24,'public_level':3,'source_is_hidden':False};s={'datastream_id':sid,'station_id':'b'*24,'public_level':3,'source_is_hidden':False};second=dict(s,datastream_id='c'*24)
            c={'stations':[station],'streams':[s,second]};tasks=[{'stream':x,'start':'2026-09-01','end':'2026-09-02'} for x in (s,second)];dump(root/'plan.json',{'catalog':c,'intervals':tasks,'request_generation':'test'})
            env={'rows':[],'cache_hit':False,'content_sha256':'e'*64,'requested_interval':{'start_inclusive':'2026-09-01T08:00:00Z','end_exclusive':'2026-09-02T08:00:00Z'},'retrieval_last_utc':'2026-09-02T10:00:00Z','latest_observation_utc':None}
            args=adapter.SimpleNamespace(plan=root/'plan.json',ledger=root/'ledger.json',budget=80,output=root/'out',state=root/'state')
            with mock.patch('bridge.DendraFetcher.fetch_chunk',side_effect=[env,FetchError('fixture failed')]):self.assertEqual(collect(args),2)
            r=json.loads((root/'out/native_manifest.json').read_text());self.assertFalse(r['complete']);self.assertEqual(len(r['streams']),1);self.assertEqual(len(r['failures']),1)

class RestoreTests(unittest.TestCase):
    def test_restore_safe_state_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);archive=p/'state.tar.gz'
            with tarfile.open(archive,'w:gz') as t:
                data=b'{}';i=tarfile.TarInfo('state/current.json');i.size=len(data);t.addfile(i,io.BytesIO(data))
            restore(archive,adapter.sha(archive),p/'restored');self.assertTrue((p/'restored/current.json').is_file())
            with self.assertRaises(ValueError):restore(archive,'0'*64,p/'bad')
    def test_archive_traversal_and_link(self):
        for name,kind in [('state/../../escape',tarfile.REGTYPE),('state/link',tarfile.SYMTYPE)]:
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp);archive=p/'state.tar.gz'
                with tarfile.open(archive,'w:gz') as t:
                    i=tarfile.TarInfo(name);i.type=kind;i.linkname='/tmp';t.addfile(i)
                with self.assertRaises(ValueError):restore(archive,adapter.sha(archive),p/'bad')
if __name__=='__main__':unittest.main()
