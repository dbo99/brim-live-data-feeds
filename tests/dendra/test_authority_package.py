"""Offline frozen-roster promotion on the actual Journal/adapter path."""
import copy
from datetime import timedelta
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"scripts"))
from dendra.history_acquisition import authority_package as p, metadata_acquisition as m
from dendra.history_acquisition import authority_witness as w, eligibility, presentation, campaign, campaign_execution, routine_update
from dendra.history_acquisition.model import Inventory,INVENTORY_SHA256,source_binding
from dendra.history_acquisition.provider_metadata import load_authority
from dendra.history_acquisition.journal import Journal
from dendra.history_acquisition.safety import Hold,decode,encode,digest,sha
from dendra.transport import parse_utc,format_utc
from test_authority_witness import Clock,Reply,NOW,SID,OTHER

FIRST = "2026-07-01T08:00:00.000Z"
END = "2026-09-26T08:00:00.000Z"


def setUpModule():
    global INV,AUTHORITY,CHECKPOINT,ROSTER,RESOLVED,GROUPS,IDS
    INV=Inventory.load(os.environ["DENDRA_INVENTORY"],INVENTORY_SHA256)
    AUTHORITY=load_authority(INV,Path(__file__).resolve().parents[2]/"data/input/dendra/pilot_catalog.json")
    CHECKPOINT=p.current_checkpoint();ROSTER=INV.roster()
    RESOLVED=[s for s,i in ROSTER.items() if i['native_unit'] in ('Percent','VolumetricWaterContent')]
    GROUPS={}
    for s in sorted(RESOLVED):GROUPS.setdefault(ROSTER[s]['station_id'],[]).append(s)
    station=next(s for s,ids in sorted(GROUPS.items()) if len(ids)>=3 and SID not in ids and OTHER not in ids)
    IDS=GROUPS[station][:3]


def row(sid):
    i=ROSTER[sid];attributes={}
    if i['depth_cm'] is not None:attributes['depth']=dict(value=i['depth_cm'],unit='Centimeter')
    if i['orientation'] is not None:attributes['orientation']=i['orientation']
    return dict(_id=sid,station_id=i['station_id'],public_level=3,is_hidden=False,is_geo_protected=False,
        attributes=attributes,terms=dict(dt=dict(Unit=i['native_unit']),ds=dict(Medium='Soil',Variable='VolumetricWaterContent')),
        datapoints_config=[dict(begins_at=FIRST,ends_before='2026-08-01T08:00:00.000Z'),dict(begins_at='2026-08-01T08:00:00.000Z')])


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.calls=[];self.mutate=None
        self.root=Path(tempfile.mkdtemp(prefix='authority-package-',dir=os.environ['DENDRA_TEST_ROOT']))
        self.configure(IDS+[SID,OTHER])

    def configure(self,ids):
        self.package=p.make(INV,selected_ids=ids,checkpoint=CHECKPOINT)
        self.approval=dict(package_id=self.package['package_id'],approval_reference='SYNTHETIC_OFFLINE_ONLY',
            window_start=NOW,window_end=format_utc(parse_utc(NOW)+timedelta(seconds=self.package['ceilings']['wall_seconds'])))

    def execute(self,request,timeout):
        q=parse_qs(urlsplit(request.full_url).query);path=urlsplit(request.full_url).path
        self.assertLessEqual(timeout,25)
        if path.endswith('/dt-unit'):
            kind='vocabulary';identity=None;value=dict(_id='dt-unit',terms=list(AUTHORITY['dictionary_terms'].values()))
        elif '/stations/' in path:
            kind='station';identity=path.rsplit('/',1)[-1]
            value=dict(_id=identity,public_level=3,is_hidden=False,is_geo_protected=False)
        elif path.endswith('/datastreams'):
            kind='list';identity=q['station_id'][0]
            rows=[row(s) for s in p.station_groups(self.package)[identity]]
            value=dict(data=rows,total=len(rows),limit=500,skip=0)
        else:
            kind='witness';identity=q['datastream_id'][0]
            self.assertEqual(q,{'datastream_id':[identity],'$sort[time]':['1'],'$limit':['1']})
            value=dict(data=[dict(t=FIRST,v=0,datastream_id=identity)],limit=1)
        # Every fake dispatch sees already durable reserve/start events.
        started=[]
        for f in (self.root/'campaigns').glob('*/events/*/*.json'):
            event=decode(f.read_bytes())
            if event['kind']=='started' and parse_utc(event['at'])<=parse_utc(self.clock.now()):started.append(event)
        self.assertTrue(started)
        self.calls.append((kind,identity,self.clock.seconds))
        if self.mutate:value=self.mutate(kind,identity,value)
        return value if isinstance(value,Reply) else Reply(encode(value))

    def run_package(self):
        return p.run(self.root,self.package,INV,AUTHORITY,authorization=self.approval,
            executor=self.execute,wait=self.clock.advance,now=self.clock.now,monotonic=self.clock.monotonic)

    def journal(self,cid):
        binding=decode((self.root/'campaigns'/cid/'manifest.json').read_bytes())['binding']
        return Journal(self.root,binding,{},inspect_only=True,inventory=INV,now=self.clock.now,monotonic=self.clock.monotonic)

    def bundle(self,result,sid,accepted=True):
        packet=result[sid]['metadata']['packet'];fp=digest(source_binding())
        review=eligibility.propose(INV,encode(packet),packet_sha256=digest(packet),packet_source_fingerprint=fp,
            executor_fingerprint=fp,start=FIRST,end=END)
        if accepted:review.update(disposition='ACCEPT_NATIVE',reviewer_ref='synthetic-offline',reviewed_at=self.clock.now(),
            expires_at=format_utc(parse_utc(self.clock.now())+timedelta(hours=1)),acknowledgements=eligibility.ACKNOWLEDGEMENTS)
        return dict(packet=packet,review=review,decision=eligibility.decide(INV,encode(packet),review,executor_fingerprint=fp,now=self.clock.now()))

    def review(self,result,sid,bundle=None,pending=False):
        source=dict(rule=w.REVIEW,disposition='PENDING' if pending else 'ACCEPT_SOURCE_START',
            evidence_sha256=result[sid]['witness']['evidence_sha256'],reviewer_ref='synthetic-offline',reviewed_at=self.clock.now())
        with self.journal(p.metadata_campaign_id(self.package,ROSTER[sid]['station_id'])) as mj, \
             self.journal(p.witness_campaign_id(self.package,sid)) as wj:
            return p.reviewed_status(INV,sid,metadata_journal=mj,witness_journal=wj,source_review=source,
                native_bundle=bundle or self.bundle(result,sid),now=self.clock.now())

    def test_shared_station_list_separate_packets_and_witnesses(self):
        result=self.run_package();self.assertEqual(len(self.calls),14)
        for kind in ('station','list','vocabulary'):self.assertEqual(sum(k==kind for k,_,_ in self.calls),3)
        self.assertEqual(sum(k=='witness' for k,_,_ in self.calls),5)
        receipts=[result[s]['metadata']['provenance']['datastream_list'] for s in IDS]
        self.assertTrue(all(x==receipts[0] for x in receipts))
        self.assertEqual(len({result[s]['metadata']['packet_sha256'] for s in IDS}),3)
        self.assertEqual(len({result[s]['witness']['journal_header_sha256'] for s in IDS}),3)
        self.assertTrue(all(x['status']=='SOURCE_START_HOLD' and x['dispatch_readiness']=='NOT_READY' for x in result.values()))

    def test_deep_and_camp_generalized_single_stream(self):
        self.configure([OTHER]);result=self.run_package()
        self.assertEqual(self.review(result,OTHER)['status'],'AUTHORITY_READY')
        self.assertEqual(len(self.calls),4)

    def test_camp_generalized_without_exception(self):
        self.configure([SID]);result=self.run_package()
        self.assertEqual(self.review(result,SID)['status'],'AUTHORITY_READY')

    def test_foreign_nonpilot_stream_ready_through_same_rules(self):
        result=self.run_package();r=self.review(result,IDS[0])
        self.assertEqual(r['status'],'AUTHORITY_READY');self.assertEqual(r['identity'],ROSTER[IDS[0]])
        self.assertEqual(r['readiness']['dispatch_readiness'],'DISPATCH_READY')

    def test_global_spacing_across_journals(self):
        self.run_package()
        self.assertTrue(all(b[2]-a[2]>=1 for a,b in zip(self.calls,self.calls[1:])))

    def test_advancing_clock_child_windows_capture_once(self):
        from dendra.history_acquisition import provider_adapter
        class AdvancingClock(Clock):
            def __init__(self):self.reads=[]
            def now(self):
                stamp=super().now();self.reads.append(stamp)
                self.advance(0.001)
                return stamp
        self.clock=AdvancingClock();self.configure([OTHER]);windows=[]
        def observe(adapter_type,seconds):
            def construct(journal):
                adapter=adapter_type(journal);run=adapter.run;before=len(self.clock.reads)
                def checked(**kwargs):
                    approval=kwargs['authorization']
                    windows.append((seconds,copy.deepcopy(approval),self.clock.reads[before:]))
                    # Exercise the unchanged production validator and adapters.
                    return run(**kwargs)
                adapter.run=checked
                return adapter
            return construct
        with patch.object(m,'MetadataAdapter',side_effect=observe(m.MetadataAdapter,150)), \
             patch.object(provider_adapter,'WitnessAdapter',side_effect=observe(provider_adapter.WitnessAdapter,60)):
            result=self.run_package()
        self.assertEqual([seconds for seconds,_,_ in windows],[150,60])
        for seconds,approval,reads in windows:
            self.assertEqual(reads,[approval['window_start']])
            self.assertEqual((parse_utc(approval['window_end'])-parse_utc(approval['window_start'])).total_seconds(),seconds)
        self.assertTrue(all(parse_utc(b)>parse_utc(a) for a,b in zip(self.clock.reads,self.clock.reads[1:])))
        self.assertEqual(len(self.calls),4)
        self.assertIn('packet',result[OTHER]['metadata']);self.assertIn('witness',result[OTHER])

    def test_child_window_clipped_to_parent_deadline(self):
        self.configure([OTHER]);deadline=format_utc(parse_utc(NOW)+timedelta(seconds=10))
        self.approval['window_end']=deadline
        run=m.MetadataAdapter.run;windows=[]
        def checked(adapter,**kwargs):
            windows.append(copy.deepcopy(kwargs['authorization']))
            return run(adapter,**kwargs)
        with patch.object(m.MetadataAdapter,'run',checked):result=self.run_package()
        self.assertEqual(len(windows),1)
        self.assertEqual(windows[0]['window_end'],deadline)
        self.assertEqual((parse_utc(windows[0]['window_end'])-parse_utc(windows[0]['window_start'])).total_seconds(),10)
        self.assertIn('witness',result[OTHER])

    def test_malformed_or_oversized_child_window_still_refused(self):
        self.configure([OTHER]);run=m.MetadataAdapter.run
        for seconds in (-1,0,150.001):
            with self.subTest(seconds=seconds):
                def altered(adapter,**kwargs):
                    approval=copy.deepcopy(kwargs['authorization'])
                    approval['window_end']=format_utc(parse_utc(approval['window_start'])+timedelta(seconds=seconds))
                    return run(adapter,**dict(kwargs,authorization=approval))
                with patch.object(m.MetadataAdapter,'run',altered),self.assertRaisesRegex(Hold,'Metadata runner window'):
                    self.run_package()
                self.assertEqual(self.calls,[])
                with self.journal(p.metadata_campaign_id(self.package,ROSTER[OTHER]['station_id'])) as j:
                    self.assertEqual(j.events,[])
                    self.assertEqual(j.snapshot()['counters']['attempts'],0)

    def test_station_privacy_failure_isolated(self):
        failed=ROSTER[IDS[0]]['station_id']
        def mutate(k,i,v):
            if k=='station' and i==failed:v['is_hidden']=True
            return v
        self.mutate=mutate;r=self.run_package()
        self.assertTrue(all(r[s]['status']=='PRIVACY_ACCESS_HOLD' for s in IDS))
        self.assertIn('witness',r[SID]);self.assertIn('witness',r[OTHER])

    def test_missing_stream_holds_only_that_stream(self):
        def mutate(k,i,v):
            if k=='list' and i==ROSTER[IDS[0]]['station_id']:
                v['data']=[x for x in v['data'] if x['_id']!=IDS[0]];v['total']=len(v['data'])
            return v
        self.mutate=mutate;r=self.run_package()
        self.assertNotIn('witness',r[IDS[0]])
        self.assertIn('witness',r[IDS[1]])
        self.assertIn('witness',r[SID])

    def test_incomplete_listing_holds_all_dependent_streams_no_pagination(self):
        def mutate(k,i,v):
            if k=='list' and i==ROSTER[IDS[0]]['station_id']:v['total']+=1
            return v
        self.mutate=mutate;r=self.run_package()
        self.assertTrue(all('witness' not in r[s] for s in IDS));self.assertIn('witness',r[SID])
        self.assertEqual(sum(k=='list' and i==ROSTER[IDS[0]]['station_id'] for k,i,_ in self.calls),1)
        with self.journal(p.metadata_campaign_id(self.package,ROSTER[IDS[0]]['station_id'])) as j:
            receipt=next(a for a in j.snapshot()['attempts'].values() if a['cursor']=='datastream-list')
            self.assertIsNot(receipt['details']['page_complete'],True)
            self.assertEqual(receipt['details']['error_code'],'parse_or_privacy')

    def test_empty_incomplete_listing_accounted_without_promoting(self):
        def mutate(k,i,v):
            if k=='list' and i==ROSTER[IDS[0]]['station_id']:v.update(data=[],total=1)
            return v
        self.mutate=mutate;r=self.run_package()
        self.assertTrue(all('witness' not in r[s] for s in IDS));self.assertIn('witness',r[SID])
        with self.journal(p.metadata_campaign_id(self.package,ROSTER[IDS[0]]['station_id'])) as j:
            self.assertEqual(j.snapshot()['counters']['unknown_row_responses'],0)

    def test_target_configuration_failure_localized(self):
        def mutate(k,i,v):
            if k=='list':
                for x in v['data']:
                    if x['_id']==IDS[0]:x['datapoints_config']=[]
            return v
        self.mutate=mutate;r=self.run_package()
        self.assertNotIn('witness',r[IDS[0]]);self.assertIn('witness',r[IDS[1]])

    def test_temporal_split_preserved(self):
        r=self.run_package()
        for sid in r:self.assertEqual(len(r[sid]['metadata']['packet']['configuration_evidence']['configurations']),2)

    def test_scale_mismatch_localized(self):
        def mutate(k,i,v):
            if k=='list':
                for x in v['data']:
                    if x['_id']==IDS[0]:x['terms']['dt']['Unit']='Dimensionless'
            return v
        self.mutate=mutate;r=self.run_package();self.assertNotIn('witness',r[IDS[0]]);self.assertIn('witness',r[IDS[1]])

    def test_stream_privacy_failure_localized(self):
        def mutate(k,i,v):
            if k=='list':
                for x in v['data']:
                    if x['_id']==IDS[0]:x['is_hidden']=True
            return v
        self.mutate=mutate;r=self.run_package();self.assertNotIn('witness',r[IDS[0]]);self.assertIn('witness',r[IDS[1]])

    def test_wrong_station_association_refuses_listing(self):
        def mutate(k,i,v):
            if k=='list' and i==ROSTER[IDS[0]]['station_id']:v['data'][0]['station_id']='0'*24
            return v
        self.mutate=mutate;r=self.run_package();self.assertTrue(all('witness' not in r[s] for s in IDS))

    def test_witness_wrong_stream_isolated_no_retry(self):
        def mutate(k,i,v):
            if k=='witness' and i==IDS[0]:v['data'][0]['datastream_id']=SID
            return v
        self.mutate=mutate;r=self.run_package()
        self.assertNotIn('witness',r[IDS[0]]);self.assertIn('witness',r[IDS[1]])
        self.assertEqual(sum(k=='witness' and i==IDS[0] for k,i,_ in self.calls),1)

    def test_independent_first_timestamps(self):
        def mutate(k,i,v):
            if k=='witness' and i==IDS[0]:v['data'][0]['t']='2026-07-02T08:00:00.000Z'
            return v
        self.mutate=mutate;r=self.run_package()
        self.assertNotEqual(r[IDS[0]]['witness']['result']['timestamp'],r[IDS[1]]['witness']['result']['timestamp'])

    def test_empty_witness_remains_unknown(self):
        def mutate(k,i,v):
            if k=='witness' and i==IDS[0]:v.update(data=[],total=0)
            return v
        self.mutate=mutate;r=self.run_package();review=self.review(r,IDS[0])
        self.assertEqual(review['source_start']['state'],presentation.UNKNOWN)
        self.assertEqual(review['readiness']['dispatch_readiness'],'NOT_READY')

    def test_source_start_review_cannot_accept_native_review(self):
        r=self.run_package();status=self.review(r,IDS[0],self.bundle(r,IDS[0],False))
        self.assertNotEqual(status['status'],'AUTHORITY_READY')

    def test_pending_source_review_refused(self):
        r=self.run_package()
        with self.assertRaises(Hold):self.review(r,IDS[0],pending=True)

    def test_source_mismatch_refuses_before_request(self):
        with patch.object(p,'source_binding',return_value={}):
            with self.assertRaises(Hold):self.run_package()
        self.assertEqual(self.calls,[])

    def test_checkpoint_mismatch_refused(self):
        with self.assertRaises(Hold):p.make(INV,selected_ids=IDS,checkpoint=dict(head='0'*40,tree='0'*40))

    def test_foreign_dimensionless_and_duplicate_selections_refused(self):
        dim=next(s for s,i in ROSTER.items() if i['native_unit']=='Dimensionless')
        for ids in ([dim],['0'*24],[SID,SID]):
            with self.subTest(ids=ids),self.assertRaises(Hold):p.make(INV,selected_ids=ids,checkpoint=CHECKPOINT)

    def test_all_337_partition_deterministic_and_bounded(self):
        packs=p.partition(INV,selected_ids=RESOLVED,checkpoint=CHECKPOINT)
        self.assertEqual(packs,p.partition(INV,selected_ids=list(reversed(RESOLVED)),checkpoint=CHECKPOINT))
        self.assertEqual({e['identity']['stream_id'] for v in packs for e in v['selection']},set(RESOLVED))
        self.assertEqual(sum(len(v['selection']) for v in packs),337)
        self.assertEqual(sum(len(p.station_groups(v)) for v in packs),107)
        for v in packs:
            self.assertLessEqual(len(v['selection']),32);self.assertLessEqual(len(p.station_groups(v)),16)
            for station,ids in p.station_groups(v).items():
                b,_=m.prepare(INV,AUTHORITY,selected_ids=ids,campaign_id=p.metadata_campaign_id(v,station),package=v)
                self.assertEqual(len(b['requests']),3);self.assertLessEqual(len(encode(b))+4096,262144)

    def test_capacity_no_silent_truncation(self):
        with self.assertRaises(Hold):p.make(INV,selected_ids=RESOLVED[:33],checkpoint=CHECKPOINT)
        ids=[ss[0] for ss in list(GROUPS.values())[:17]]
        with self.assertRaises(Hold):p.make(INV,selected_ids=ids,checkpoint=CHECKPOINT)

    def test_rehashed_package_tampering_still_refused(self):
        for field,value in [('scale_multiplier',42),('identity',ROSTER[SID])]:
            v=copy.deepcopy(self.package);v['selection'][0][field]=value
            v['package_id']=digest({k:x for k,x in v.items() if k!='package_id'})
            with self.assertRaises(Hold):p.validate(v,INV)

    def test_loose_selection_cannot_expand_station_preparation(self):
        station=ROSTER[IDS[0]]['station_id'];ids=IDS+[SID]
        with self.assertRaises(Hold):m.prepare(INV,AUTHORITY,selected_ids=ids,
            campaign_id=p.metadata_campaign_id(self.package,station),package=self.package)

    def test_restart_completed_evidence_no_requests_or_appends(self):
        r=self.run_package();before={str(f):sha(f.read_bytes()) for f in self.root.rglob('*.json')}
        calls=len(self.calls);self.assertEqual(self.run_package(),r);self.assertEqual(len(self.calls),calls)
        self.assertEqual(before,{str(f):sha(f.read_bytes()) for f in self.root.rglob('*.json')})

    def test_restart_keeps_failed_witness_spent(self):
        def mutate(k,i,v):
            if k=='witness' and i==IDS[0]:v['data'][0]['v']=None
            return v
        self.mutate=mutate;self.run_package();n=len(self.calls)
        r=self.run_package();self.assertEqual(len(self.calls),n);self.assertNotIn('witness',r[IDS[0]])

    def test_restart_no_new_authorization_window(self):
        self.run_package();self.approval['window_end']=format_utc(parse_utc(self.approval['window_end'])-timedelta(seconds=1))
        with self.assertRaises(Hold):self.run_package()

    def test_restart_ambiguous_attempt_not_refunded(self):
        with patch.object(Journal,'received',side_effect=OSError('synthetic storage failure')):
            with self.assertRaises(Hold):self.run_package()
        n=len(self.calls)
        with self.assertRaises(Hold):self.run_package()
        self.assertEqual(len(self.calls),n)

    def test_throttle_stops_package_without_retry(self):
        def mutate(k,i,v):r=Reply(b'');r.status=429;return r
        self.mutate=mutate
        with self.assertRaises(Hold):self.run_package()
        self.assertEqual(len(self.calls),1)

    def test_foreign_receipt_corruption_not_treated_as_absence(self):
        self.run_package()
        header=next((self.root/'campaigns').glob('*/manifest.json'));header.write_bytes(b'corrupt synthetic header')
        with self.assertRaises(Hold):self.run_package()

    def test_temporal_configuration_gap_not_ready(self):
        def mutate(k,i,v):
            if k=='list':
                for x in v['data']:x['datapoints_config'][1]['begins_at']='2026-08-02T08:00:00.000Z'
            return v
        self.mutate=mutate;r=self.run_package();review=self.review(r,IDS[0])
        self.assertNotEqual(review['status'],'AUTHORITY_READY')
        self.assertIn('temporal_configuration_incomplete',review['readiness']['dispatch_blockers'])

    def test_generalized_authority_feeds_existing_shards_executor_and_routine(self):
        r=self.run_package();sid=IDS[0];bundle=self.bundle(r,sid);review=self.review(r,sid,bundle)
        audit=Path(os.environ['DENDRA_RECORD_AGE_AUDIT']).read_bytes()
        a=presentation.classify_starts(INV,audit,as_of=self.clock.now())
        entry=next(x for x in a['streams'] if x['stream_id']==sid)
        entry.update(source_start_authority=presentation.REVIEWED,source_start_timestamp=FIRST,
            source_start_evidence=review['source_start'],source_start_review_reason=presentation.FIRST_RULE)
        a['classification_sha256']=digest({k:v for k,v in a.items() if k!='classification_sha256'})
        shards=campaign.plan_shards(INV,a,{sid:bundle},as_of=self.clock.now())
        self.assertTrue(shards['shards']);self.assertFalse(shards['planning_holds'])
        for s in shards['shards']:
            binding,tasks=campaign_execution.prepare(s['campaign'],INV,{sid:bundle},now=self.clock.now())
            self.assertEqual(set(tasks),set(s['identity']['ordered_task_ids']))
            self.assertEqual([key for key,t in sorted(tasks.items(),key=lambda x:x[1]['start'])],s['identity']['ordered_task_ids'])
        # Routine preparation uses the same accepted bundle/executor. Only its
        # already-verified cycle input is substituted here; routine's actual
        # state/overlap behavior is covered by the unmodified regression suite.
        interval=dict(start='2026-09-20T08:00:00.000Z',end=END)
        cycle=dict(cycle_id='a'*64,source_fingerprint=digest(source_binding()),work={sid:dict(status='PLANNED',intervals=[interval])})
        with patch.object(routine_update,'resume',return_value=(cycle,None,None)):
            plans=routine_update.prepare_campaigns({}, {sid:bundle},inventory=INV,now=self.clock.now())
        self.assertEqual(len(plans),1);self.assertEqual(len(plans[0]['tasks']),1)

    def test_offline_missing_evidence_has_complete_disposition_closure(self):
        result=p.dispositions(self.package,INV,metadata_journals={},witness_journals={},reviews={},now=self.clock.now())
        self.assertEqual(set(result),set(IDS+[SID,OTHER]))
        self.assertTrue(all(x['needs_fresh_metadata'] and x['needs_source_start_witness'] for x in result.values()))
        self.assertTrue(all(x['dispatch_readiness']=='NOT_READY' for x in result.values()))

    def test_dispositions_from_preserved_journals_do_not_accept_reviews(self):
        from contextlib import ExitStack
        result=self.run_package()
        with ExitStack() as stack:
            metadata={s:stack.enter_context(self.journal(p.metadata_campaign_id(self.package,s))) for s in p.station_groups(self.package)}
            witnesses={s:stack.enter_context(self.journal(p.witness_campaign_id(self.package,s))) for s in result}
            view=p.dispositions(self.package,INV,metadata_journals=metadata,witness_journals=witnesses,reviews={},now=self.clock.now())
            self.assertTrue(all(not x['needs_fresh_metadata'] and not x['needs_source_start_witness'] for x in view.values()))
            self.assertTrue(all(x['status']=='SOURCE_START_HOLD' for x in view.values()))
            self.assertTrue(all(x['dispatch_readiness']=='NOT_READY' for x in view.values()))

    def test_partial_accounted_package_restart_continues_unstarted_children(self):
        # Crash between children, after durable completed first station/witness.
        original=p.witness_campaign_id
        validate=p.validate
        stops=0
        def delayed_validation(package,inventory):
            validate(package,inventory)
            # Validation can take time after the durable started event and
            # before actual dispatch. A new process must preserve spacing too.
            self.clock.advance(0.25)
        def stopped(package,sid):
            nonlocal stops
            stops+=1
            # Calls first enumerate all allowed IDs, then create actual children.
            if stops==len(package['selection'])+2:raise RuntimeError('synthetic caller crash between children')
            return original(package,sid)
        with patch.object(p,'witness_campaign_id',side_effect=stopped), \
             patch.object(p,'validate',side_effect=delayed_validation):
            with self.assertRaises(RuntimeError):self.run_package()
        before=list(self.calls);r=self.run_package()
        self.assertEqual(len(self.calls),14)
        self.assertEqual(self.calls[:len(before)],before)
        self.assertTrue(all(b[2]-a[2]>=1 for a,b in zip(self.calls,self.calls[1:])))
        self.assertTrue(all('witness' in x for x in r.values()))

    def test_preserved_admitted_old_packet_cannot_become_current_authority(self):
        root=Path.cwd()/'.l01-soil-integration/l02-practical-latest-proof-20260929T070637Z'
        packet=decode((root/'metadata-packet.json').read_bytes())
        with self.assertRaisesRegex(Hold,'Packet binding mismatch'):
            w.check_metadata(INV,packet['stream_id'],packet,now=self.clock.now())

    def test_no_spacing_wait_means_no_second_dispatch(self):
        with self.assertRaises(Hold):
            p.run(self.root,self.package,INV,AUTHORITY,authorization=self.approval,
                executor=self.execute,wait=lambda _:None,now=self.clock.now,monotonic=self.clock.monotonic)
        self.assertEqual(len(self.calls),1)


if __name__=='__main__':unittest.main()
