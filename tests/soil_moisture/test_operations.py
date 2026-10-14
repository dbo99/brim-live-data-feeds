"""Fake-clock result/retry proof; no source collection or dispatch."""
import copy,json,sys,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
import soil_moisture_operations as o
import soil_network_workflow as w
T='2026-09-22T12:00:00Z';OLD='2026-09-21T12:00:00Z';END='2026-09-22T12:01:00Z';C='2026-09-22'
def event(n='dendra',rid='one',outcome='success_no_data_change',started=T,finished=END,**kw):
    return o.result(n,rid,C,started,finished,outcome,**kw)
def ack(g='new',clock=END):return dict(generation=g,commit=('a' if g=='new' else 'b')*40,index_sha256='c'*64,observed_at_utc=clock,evidence='verified_committed_product',commit_ancestry=['a'*40,'b'*40] if g=='new' else ['b'*40])
class OperationsTests(unittest.TestCase):
    def test_failed_query_publish_and_out_of_order_clocks(self):
        old=event(rid='old',started=OLD,finished=OLD,last_successful_source_check=OLD,latest_eligible_observation='2026-09-18',candidate_generation='old',acknowledgement=ack('old',OLD))
        success=event(last_successful_source_check=T,candidate_generation='prepared',latest_eligible_observation='2026-09-18')
        failed=event(outcome='publication_failure',phase='publication',last_successful_source_check=T,candidate_generation='prepared',latest_eligible_observation='2026-09-18')
        h=o.aggregate([failed,success,old],END)['networks']['dendra'];self.assertEqual(h['state'],'publication_failure');self.assertEqual(h['last_successful_source_check'],T);self.assertEqual(h['latest_eligible_observation'],'2026-09-18');self.assertEqual(h['acknowledgement'],old['acknowledgement'])
        newer=event(rid='newer',started='2026-09-22T12:02:00Z',finished='2026-09-22T12:03:00Z',acknowledgement=ack(clock='2026-09-22T12:03:00Z'))
        old['finished_at_utc']='2026-09-22T12:04:00Z';old['acknowledgement']['observed_at_utc']='2026-09-22T12:04:00Z'
        h=o.aggregate([newer,old,failed,success],'2026-09-22T12:05:00Z')['networks']['dendra'];self.assertEqual(h['last_attempt']['run_id'],'newer');self.assertEqual(h['acknowledgement']['generation'],'new')
    def test_source_independence_no_barrier_or_clock_substitution(self):
        for failed,healthy in [('dendra','scan'),('scan','dendra')]:
            h=o.aggregate([event(failed,'fail','provider_failure'),event(healthy,'good',acknowledgement=ack())],END)['networks'];self.assertEqual(h[failed]['state'],'provider_failure');self.assertIsNone(h[failed]['last_successful_source_check']);self.assertEqual(h[healthy]['acknowledgement']['generation'],'new');self.assertEqual(h['snotel']['state'],'not_enabled')
    def test_failure_durable_restore_without_candidate_and_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td).resolve();x=event(outcome='provider_failure');o.append(p/'health',x);o.append(p/'health',x)
            with self.assertRaises(ValueError):o.append(p/'health',event(outcome='semantic_hold'))
            sha=o.export(p/'health',p/'snapshot');o.restore(p/'snapshot',sha,p/'restored');self.assertEqual(o.records(p/'restored'),[x]);self.assertFalse(list(p.glob('**/candidate*')))
            (p/'snapshot/extra').write_text('unbound')
            with self.assertRaises(ValueError):o.restore(p/'snapshot',sha,p/'bad')
    def test_morning_boundaries(self):
        import morning_boundaries
        self.assertEqual(morning_boundaries.run()['status'],'passed')
    def test_fractional_query_clock_and_unrelated_ancestry_hold(self):
        a=event(rid='a',last_successful_source_check='2026-09-22T12:00:00Z')
        b=event(rid='b',last_successful_source_check='2026-09-22T12:00:00.900Z')
        self.assertEqual(o.aggregate([a,b],END)['networks']['dendra']['last_successful_source_check'],b['last_successful_source_check'])
        a['acknowledgement']=ack('old');b['acknowledgement']=ack();b['acknowledgement']['commit_ancestry']=['a'*40]
        a['null_reasons'].pop('acknowledgement');b['null_reasons'].pop('acknowledgement')
        with self.assertRaises(ValueError):o.aggregate([a,b],END)
    def test_prepare_failure_and_snotel_gate_always_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td).resolve()
            def never(*a,**kw):raise AssertionError('Must not acquire')
            x=w.prepare('snotel',p/'run',p/'health','gated',C,'a'*40,enabled=True,runner=never,clock=lambda:T);self.assertEqual(x['outcome'],'not_enabled');self.assertEqual(x['requests']['attempts'],0)
            def failure(*a,**kw):raise TimeoutError('SYNTHETIC NRCS common timeout')
            x=w.prepare('scan',p/'scan',p/'health','nrcs-failed',C,'a'*40,enabled=True,runner=failure,clock=lambda:T);self.assertEqual(x['outcome'],'provider_failure');self.assertIsNone(x['candidate_generation']);self.assertIsNone(x['requests']['attempts']);self.assertEqual(len(o.records(p/'health')),2)
    def test_reviewed_latest_removal_and_known_no_eligible_are_truthful(self):
        old=event(rid='old',started=OLD,finished=OLD,candidate_generation='old',latest_eligible_observation='2026-09-21')
        corrected=event(rid='new',candidate_generation='new',latest_eligible_observation='2026-09-20')
        self.assertEqual(o.aggregate([corrected,old],END)['networks']['dendra']['latest_eligible_observation'],'2026-09-20')
        corrected=event(rid='new',candidate_generation='new')
        h=o.aggregate([corrected,old],END)['networks']['dendra'];self.assertIsNone(h['latest_eligible_observation']);self.assertEqual(h['latest_eligible_reason'],'validated candidate has no eligible observation')
    def test_late_receipt_validation_failure_is_durable(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();prepared=root/'prepared.json';prepared.write_text(json.dumps(event('scan',candidate_generation='prepared')))
            for p in w.scan.ALLOWLIST:
                q=root/p;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(b'saved')
            with patch.object(w.scan,'validate_product',return_value=(T,28)),patch.object(w,'source_details',return_value=('prepared',None)),patch.object(w,'command',side_effect=['a'*40,'b'*40]),patch.object(w.subprocess,'check_output',return_value=b'saved'):
                r=w.record_publication(root/'health',prepared,root,True,clock=lambda:END)
            self.assertEqual(r['outcome'],'publication_failure');self.assertIsNone(r['acknowledgement']);self.assertEqual(o.records(root/'health'),[r])
    def test_publisher_arguments_are_source_owned(self):
        scan=w.publisher_args('scan',ROOT,'/private/tmp/SYNTHETIC');dendra=w.publisher_args('dendra',ROOT,'/private/tmp/SYNTHETIC')
        self.assertEqual(scan.count('--allowlist'),5);self.assertEqual(dendra.count('--allowlist'),1);self.assertEqual(dendra.count('--owned-root'),4)
        self.assertNotIn('docs/data/dendra/index.json',scan)
        with self.assertRaises(ValueError):w.publisher_args('snotel',ROOT,'/private/tmp/SYNTHETIC')
class OfflineWY2027Tests(unittest.TestCase):
    """Private fixture receipts, with source validators and all transports denied."""
    def setUp(self):
        import test_snotel_history as snf
        sys.path.insert(0, str(ROOT/'tests/dendra'))
        import test_candidate3_delivery as df
        self.snf, self.df = snf, df
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); (self.root/'inputs').mkdir()
        self.bindings = {n: dict(manifest_sha256='a'*64, generation='synthetic-'+n, policy='accepted-fixture-policy') for n in o.SOURCES}
        row = df.record(1, '2026-10-01')
        from dendra.history_acquisition import candidate3_delivery as c3
        identity = {k: row[k] for k in c3.IDENTITY_FIELDS}
        sources = dict(snotel=dict(station_triplet='999000001:ZZ:SNTL', signed_depth=-8, ordinal=1),
                       scan=dict(station_triplet='99001:ZZ:SCAN', site_code=99001, depth_in=2, dated_membership={'2026-10-01':['SMS.I_2']}),
                       dendra=dict(station_id=row['station_id'], stream_id=row['stream_id'], export_local_series_key=row['export_local_series_key'],
                                   depth_status='UNKNOWN', depth_cm=None, daily_identity=identity))
        ids = dict(snotel='999000001:ZZ:SNTL|SMS:-8:1', scan='99001:ZZ:SCAN|depth_in:2', dendra=row['station_id']+'|'+row['stream_id'])
        roster = [dict(network=n, identity=ids[n], source=sources[n], initial_outcome='NOT_QUERIED', evidence='SYNTHETIC_ACCEPTED_FIXTURE',
                       eligibility=dict(source='VERIFIED', current='VERIFIED', public='VERIFIED', reference='UNKNOWN'), holds=[]) for n in ('scan','snotel','dendra')]
        schema = df.schema_fixture()['properties']['examples']['properties']['known_percent']['properties']['records']['items']
        self.bundle = dict(version=o.OFFLINE_VERSION, bindings=self.bindings, roster=roster, events=[], dendra_row_schema=schema)
        cutoffs = {n:dict(as_of='2026-10-08T12:00:00Z', start='2026-10-01', end='2026-10-07') for n in o.SOURCES}
        cutoffs['dendra'].update(start='2026-10-01T08:00:00Z', end='2026-10-08T08:00:00Z')
        self.request = dict(version=o.OFFLINE_VERSION, offline_only=True, scratch_root=str(self.root), bindings=copy.deepcopy(self.bindings), expected_counts={n:1 for n in o.SOURCES},
                            selection={n:[ids[n]] for n in o.SOURCES}, cutoffs=cutoffs,
                            budgets=dict(snotel=dict(attempts=64,response_bytes=1048576),scan=dict(attempts=105,canaries=6,preflight=3),
                                         dendra=dict(attempts=80,seconds=300,response_bytes=67108864,new_intervals=16)))

    def event(self, network, kind='OBSERVATION', **kw):
        row = next(r for r in self.bundle['roster'] if r['network']==network)
        e = dict(network=network,identity=row['identity'],event_id=str(len(self.bundle['events'])), binding=copy.deepcopy(self.bindings[network]),
                 source_checked_at='2026-10-07T23:00:00Z',kind=kind,**kw)
        if network=='snotel' and kind=='OBSERVATION':e['source_checked_at']=kw['payload']['history']['query_ledger'][0]['retrieved_at_utc']
        self.bundle['events'].append(e); return e

    def pin(self, path, value):
        import hashlib
        path.write_text(json.dumps(value,sort_keys=True)+'\n')
        return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def run_fixture(self, name='result', **kw):
        self.request['bundle'] = self.pin(self.root/'inputs/bundle.json',self.bundle)
        result = o.offline_run(self.request,self.root/name,**kw)
        checkpoint = json.loads((self.root/name/'CHECKPOINT_RECONCILIATION_DRYRUN.json').read_text())
        return result, checkpoint

    def state(self, checkpoint, network):
        return next(v for k,v in checkpoint['reconciliation']['identities'].items() if k.startswith(network+':'))

    def scan_row(self, value=0):
        return dict(station_uid='NRCS_scan_99001',site_code=99001,depth_in=2,sensor_id='SMS.I_2',sensor_count=1,
                    obs_date='2026-10-01',sms_pct=value,water_year=2027,water_day=1)

    def snotel(self, rows=None, **outcome):
        f=self.snf; metadata=f.sh.canonical([f.station()])
        cap=f.capture(rows=rows if rows is not None else [f.record('2026-10-01',0)],begin='2026-10-01',end='2026-10-02',**outcome)
        return dict(date='2026-10-01',history=f.sh.build_history(metadata,f.sh.sha256(metadata),[cap]))

    def query(self, dates, returned=(), network='scan'):
        import base64,hashlib
        from datetime import datetime,timedelta
        proof=dict(complete=True,response_present=True,dates=dates,returned_dates=list(returned))
        if network=='scan':
            rows=[]
            for d in returned:
                row=self.scan_row();row['obs_date']=d;row['water_day']=(datetime.fromisoformat(d)-datetime(2026,10,1)).days+1;rows.append(row)
            raw=json.dumps(rows).encode()
            proof.update(authority='accepted_saved_query_receipt',http_status=200,response_base64=base64.b64encode(raw).decode(),response_sha256=hashlib.sha256(raw).hexdigest(),response_bytes=len(raw))
        else:
            from dendra import transport
            start=dates[0]+'T08:00:00.000Z';end=(datetime.fromisoformat(dates[-1])+timedelta(days=1)).strftime('%Y-%m-%dT08:00:00.000Z')
            native=[dict(t=d+'T12:00:00.000Z',v=0) for d in returned];raw=json.dumps(dict(data=native,limit=1000)).encode()
            rows,_=transport.normalize_rows(native,start,end)
            binding=dict(identity='synthetic-exact-source-binding',policy='synthetic-approved')
            self.bundle['roster'][2]['source']['collection_binding']=binding
            page=dict(requested_at_utc='2026-10-07T22:59:00Z',retrieved_at_utc='2026-10-07T23:00:00Z',status=200,
                      response_sha256=hashlib.sha256(raw).hexdigest(),response_bytes=len(raw),row_count=len(native),effective_limit=1000)
            env=dict(datastream_id=self.bundle['roster'][2]['source']['stream_id'],query_complete=True,requested_interval=dict(start_inclusive=start,end_exclusive=end),
                     rows=rows,pages=[page],page_count=1,retrieval_first_utc=page['retrieved_at_utc'],retrieval_last_utc=page['retrieved_at_utc'],
                     diagnostics=dict(completion_reason='short_page_with_effective_limit' if rows else 'empty_page'),collection_binding=binding)
            env['content_sha256']=transport._content_hash(env);env['collection_sha256']=o.digest(env)
            proof.update(envelope=env,responses_base64=[base64.b64encode(raw).decode()])
        return proof

    def test_science_zero_source_clocks_and_reference_independence(self):
        self.event('scan',payload=dict(row=self.scan_row()))
        self.event('snotel',payload=self.snotel())
        self.event('dendra',payload=dict(row=self.df.record(1,'2026-10-01')))
        _,cp=self.run_fixture()
        for n in o.SOURCES:self.assertEqual(self.state(cp,n)['outcome'],'OBSERVED_ACCEPTED')
        sn=self.state(cp,'snotel')['observations']['2026-10-01']
        self.assertEqual(sn['source_timestamp'],'2026-10-02T08:00:00Z');self.assertEqual(sn['rows'][0]['value_native'],0)
        d=self.state(cp,'dendra');self.assertIsNone(d['observations']['2026-10-01']['latest_instantaneous'])
        self.assertEqual(len(d['query_coverage']['gaps']),1) # A daily row does not certify a native query.

    def test_snotel_missing_suspect_empty_failed_and_original_flags(self):
        f=self.snf
        cases=[([f.record('2026-10-01',None)],{},'MISSING'),
               ([dict(date='2026-10-01',qcFlag='S',origValue=9)],{},'WITHHELD'),
               ([],{},'COMPLETE_VALID_EMPTY'),
               ([],dict(http_status=503,complete=False,error='fixture_failure'),'FAILED')]
        for i,(rows,kw,expected) in enumerate(cases):
            self.bundle['events']=[];self.event('snotel',payload=self.snotel(rows,**kw));_,cp=self.run_fixture(str(i))
            self.assertEqual(self.state(cp,'snotel')['outcome'],expected)
        self.bundle['roster'][1]['source']['signed_depth']=8
        with self.assertRaises(ValueError):self.run_fixture('baddepth')

    def test_revisions_noop_failure_does_not_erase_or_block_other_network(self):
        for value in [0,0,12]:self.event('scan',payload=dict(row=self.scan_row(value)))
        self.event('scan','FAILED',reason='saved HTTP failure')
        self.event('dendra',payload=dict(row=self.df.record(1,'2026-10-01')))
        _,cp=self.run_fixture();s=self.state(cp,'scan')
        self.assertEqual([e['outcome'] for e in s['attempts']],['OBSERVED_ACCEPTED','NO_DATA_CHANGE','ACCEPTED_REVISION','FAILED'])
        self.assertEqual(s['observations']['2026-10-01']['row'][1],12)
        self.assertEqual(self.state(cp,'dendra')['outcome'],'OBSERVED_ACCEPTED')

    def test_scan_composite_conflict_held_and_outlier_not_clamped(self):
        r=self.scan_row(130);self.event('scan',payload=dict(row=r));_,cp=self.run_fixture('finite')
        self.assertEqual(self.state(cp,'scan')['observations']['2026-10-01']['row'][1],130)
        self.bundle['roster'][0]['source']['dated_membership']['2026-10-01']=['SMS.I-1_2']
        _,cp=self.run_fixture('conflict');self.assertEqual(self.state(cp,'scan')['outcome'],'UNRESOLVED_COMPOSITE')

    def test_dendra_withheld_dimensionless_conflicting_depth_and_incomplete_day(self):
        from dendra.history_acquisition import candidate3_delivery as c3
        for i,status in enumerate(c3.STATES):
            self.bundle['events']=[];row=self.df.record(1,'2026-10-01',status=status)
            self.event('dendra',payload=dict(row=row));_,cp=self.run_fixture(str(i))
            self.assertIn(self.state(cp,'dendra')['outcome'],['OBSERVED_ACCEPTED','WITHHELD','MISSING','UNRESOLVED'])
        self.bundle['events']=[];row=self.df.record(1,'2026-10-01',unit='Dimensionless',status='UNRESOLVED_SEMANTICS')
        d=self.bundle['roster'][2];d['source']['daily_identity']['native_unit']='Dimensionless'
        self.event('dendra',payload=dict(row=row));_,cp=self.run_fixture('dimensionless');self.assertEqual(self.state(cp,'dendra')['outcome'],'UNRESOLVED')
        self.request['cutoffs']['dendra']['end']='2026-10-08T09:00:00Z'
        with self.assertRaises(ValueError):self.run_fixture('incomplete')

    def test_explicit_query_empty_missing_overlap_and_pending(self):
        self.event('dendra','COMPLETE_QUERY',query=self.query(['2026-10-01','2026-10-02'],['2026-10-01'],network='dendra'))
        self.event('dendra','COMPLETE_VALID_EMPTY',query=self.query(['2026-10-02','2026-10-03'],network='dendra'))
        self.event('scan','COMPLETE_QUERY',query=self.query(['2026-10-01','2026-10-02'],['2026-10-01']))
        self.event('snotel','INCOMPLETE_CONTINUATION',reason='saved cursor pending')
        _,cp=self.run_fixture();d=self.state(cp,'dendra')
        self.assertEqual(d['query_coverage']['contiguous_complete_query_frontier'],'2026-10-04T08:00:00.000Z')
        self.assertEqual(self.state(cp,'scan')['outcome'],'MISSING_WITHIN_COMPLETED_QUERY')
        self.assertEqual(self.state(cp,'snotel')['outcome'],'INCOMPLETE_CONTINUATION')
        self.bundle['events'][1]['query']['response_present']=False
        with self.assertRaises(ValueError):self.run_fixture('missingresponse')

    def test_resume_pinned_prefix_idempotency_and_prior_immutable(self):
        import hashlib
        for value in [0,12]:self.event('scan',payload=dict(row=self.scan_row(value)))
        _,first=self.run_fixture('prefix',max_events=1)
        path=self.root/'prefix/CHECKPOINT_RECONCILIATION_DRYRUN.json';raw=path.read_bytes()
        ref=dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest())
        a,cp=self.run_fixture('continued',resume=ref);b,_=self.run_fixture('replayed')
        self.assertEqual(a,b);self.assertEqual(path.read_bytes(),raw)
        self.assertEqual(first['reconciliation']['pending_events'],1);self.assertEqual(cp['reconciliation']['pending_events'],0)
        self.request['cutoffs']['scan']['end']='2026-10-06'
        with self.assertRaises(ValueError):self.run_fixture('badresume',resume=ref)

    def test_hash_generation_selection_budget_and_protected_outputs(self):
        self.request['bundle']=self.pin(self.root/'inputs/bundle.json',self.bundle)
        with self.assertRaises(ValueError):o.offline_run(self.request,self.root/'inputs/output')
        with self.assertRaises(ValueError):o.offline_run(self.request,ROOT/'forbidden-offline-output')
        for mutation in ['hash','generation','selection','budget']:
            r=copy.deepcopy(self.request)
            if mutation=='hash':r['bundle']['sha256']='0'*64
            elif mutation=='generation':r['bindings']['scan']['generation']='wrong'
            elif mutation=='selection':r['selection']['scan']=['wrong']
            else:r['budgets']['snotel']['attempts']=65
            with self.assertRaises(ValueError):o.offline_run(r,self.root/mutation)

    def test_offline_cli_never_calls_transport_legacy_or_git(self):
        import socket,subprocess
        self.request['bundle']=self.pin(self.root/'inputs/bundle.json',self.bundle)
        ref=self.pin(self.root/'inputs/request.json',self.request)
        argv=['soil_network_workflow.py','offline-wy2027','--offline-only','--request',ref['path'],'--request-sha256',ref['sha256'],'--output',str(self.root/'cli')]
        with patch.object(sys,'argv',argv),patch.object(w,'prepare',side_effect=AssertionError('legacy')),patch.object(w,'command',side_effect=AssertionError('command')),patch.object(subprocess,'Popen',side_effect=AssertionError('process')),patch.object(socket,'socket',side_effect=AssertionError('network')):
            self.assertEqual(w.main(),0)


    def test_public_holds_and_unselected_not_promoted(self):
        self.event('dendra',payload=dict(row=self.df.record(1,'2026-10-01')))
        self.bundle['roster'][2]['eligibility']['public']='HELD';self.bundle['roster'][2]['holds']=['PUBLIC_AUTHORITY_HELD']
        _,cp=self.run_fixture('held');self.assertEqual(self.state(cp,'dendra')['outcome'],'HELD_ELIGIBILITY')
        self.assertEqual(self.state(cp,'dendra')['observations'],{})
        self.request['selection'].pop('dendra');_,cp=self.run_fixture('unselected')
        self.assertEqual(self.state(cp,'dendra')['attempts'],[])
        self.assertEqual(self.state(cp,'scan')['outcome'],'NOT_QUERIED')

    def test_snotel_new_retrieval_is_noop_and_clock_mismatch_fails(self):
        self.event('snotel',payload=self.snotel())
        f=self.snf;metadata=f.sh.canonical([f.station()]);cap=f.capture([f.record('2026-10-01',0)],begin='2026-10-01',end='2026-10-02')
        cap['receipt']['retrieved_at_utc']='2026-10-07T21:15:09Z';f.repin(cap)
        self.event('snotel',payload=dict(date='2026-10-01',history=f.sh.build_history(metadata,f.sh.sha256(metadata),[cap])))
        _,cp=self.run_fixture();self.assertEqual(self.state(cp,'snotel')['outcome'],'NO_DATA_CHANGE')
        self.bundle['events'][-1]['source_checked_at']='2026-10-08T00:00:00Z'
        with self.assertRaises(ValueError):self.run_fixture('wrongclock')

    def test_dendra_interval_budget_and_scan_year_recalculation(self):
        self.request['cutoffs']['dendra'].update(end='2027-01-01T08:00:00Z',as_of='2027-01-02T12:00:00Z')
        self.request['budgets']['dendra']['new_intervals']=1
        self.request['cutoffs']['scan'].update(end='2027-01-01',as_of='2027-01-02T12:00:00Z')
        self.run_fixture();plan=json.loads((self.root/'result/ACQUISITION_PLAN_DRYRUN.json').read_text())
        d=plan['networks']['dendra']['detail'];self.assertEqual(len(d['intervals_this_epoch']),1);self.assertEqual(len(d['deferred']),3)
        self.assertEqual(plan['networks']['scan']['detail']['station_year_calls'],2)
        self.request['cutoffs']['dendra']['start']='2026-09-30T08:00:00Z'
        with self.assertRaises(ValueError):self.run_fixture('wrongwy')

    def test_saved_response_corruption_and_incomplete_checkpoint_rejected(self):
        e=self.event('dendra','COMPLETE_VALID_EMPTY',query=self.query(['2026-10-01'],network='dendra'))
        original=copy.deepcopy(e)
        for i,kind in enumerate(['bytes','complete','identity']):
            self.bundle['events']=[copy.deepcopy(original)];q=self.bundle['events'][0]['query']
            if kind=='bytes':q['responses_base64']=['e30=']
            elif kind=='complete':q['envelope']['query_complete']=False
            else:q['envelope']['datastream_id']='f'*24
            with self.assertRaises(ValueError):self.run_fixture(str(i))

    def test_daily_timestamp_and_exact_bridge_cannot_be_relabelled(self):
        self.event('dendra',payload=dict(row=self.df.record(1,'2026-10-01')))
        self.bundle['roster'][2]['source']['stream_id']='f'*24
        with self.assertRaises(ValueError):self.run_fixture('identity')
        self.bundle['roster'][2]['source']['stream_id']=self.bundle['events'][0]['payload']['row']['stream_id']
        self.bundle['events'][0]['payload']['row']['latest_source_timestamp_utc']='2026-10-02T12:00:00Z'
        with self.assertRaises(ValueError):self.run_fixture('future')

    def test_interrupted_receipt_is_preserved_and_restart_is_fresh(self):
        self.request['bundle']=self.pin(self.root/'inputs/bundle.json',self.bundle)
        with patch.object(o.os,'fsync',side_effect=InterruptedError('fixture interruption')):
            with self.assertRaises(InterruptedError):o.offline_run(self.request,self.root/'interrupted')
        preserved=(self.root/'interrupted/CURRENT_ROSTER_DECISIONS.json').read_bytes()
        with self.assertRaises(ValueError):o.offline_run(self.request,self.root/'interrupted')
        self.run_fixture('restarted');self.assertEqual((self.root/'interrupted/CURRENT_ROSTER_DECISIONS.json').read_bytes(),preserved)

    def test_fabricated_dendra_short_page_and_empty_misclassification_fail(self):
        import base64,hashlib
        q=self.query(['2026-10-01'],['2026-10-01'],network='dendra')
        value=json.loads(base64.b64decode(q['responses_base64'][0]));value['limit']=1
        raw=json.dumps(value).encode();q['responses_base64']=[base64.b64encode(raw).decode()]
        q['envelope']['pages'][0].update(response_sha256=hashlib.sha256(raw).hexdigest(),response_bytes=len(raw))
        q['envelope']['collection_sha256']=o.digest({k:v for k,v in q['envelope'].items() if k!='collection_sha256'})
        self.event('dendra','COMPLETE_QUERY',query=q)
        with self.assertRaises(ValueError):self.run_fixture('fabricated')
        self.bundle['events']=[];self.event('scan','COMPLETE_QUERY',query=self.query(['2026-10-01']))
        with self.assertRaises(ValueError):self.run_fixture('misclassified')


class SnotelCaptureBridgeTests(unittest.TestCase):
    setUp = OfflineWY2027Tests.setUp
    pin = OfflineWY2027Tests.pin
    run_fixture = OfflineWY2027Tests.run_fixture

    def retained(self, *, responses=None, max_attempts=64):
        from datetime import datetime, timezone
        from urllib.parse import urlencode
        import test_snotel_transport as tf
        self.tf = tf; self.sn = tf.history
        self.groups = [tf.spec(1), tf.spec(2)]
        rows = []
        for group, indexes in zip(self.groups, ([0, 1], [2])):
            station = json.loads(group['metadata_body'])[0]
            station['stationElements'] = [station['stationElements'][i] for i in indexes]
            if indexes == [2]: station['stationElements'][0]['ordinal'] = 2
            group['metadata_body'] = self.sn.canonical([station])
            group['metadata_sha256'] = self.sn.sha256(group['metadata_body'])
            params = group['request']['parameters']
            params['elements'] = ','.join('SMS:%s:%s' % (e['heightDepth'], e['ordinal']) for e in station['stationElements'])
            group['request']['url'] = self.sn.TARGET + '?' + urlencode(params)
            for element in station['stationElements']:
                row = copy.deepcopy(next(r for r in self.bundle['roster'] if r['network'] == 'snotel'))
                row['source'] = dict(station_triplet=station['stationTriplet'], signed_depth=element['heightDepth'], ordinal=element['ordinal'])
                row['identity'] = self.sn.sensor_identity(station['stationTriplet'], element['heightDepth'], element['ordinal'])
                rows.append(row)
        self.bundle['roster'] = rows + [r for r in self.bundle['roster'] if r['network'] != 'snotel']
        self.request['expected_counts']['snotel'] = 3
        self.request['selection']['snotel'] = [r['identity'] for r in rows]
        self.request['cutoffs']['snotel']['end'] = '2026-10-02'
        station = json.loads(self.groups[0]['metadata_body'])[0]
        self.valid = [dict(stationTriplet=station['stationTriplet'], data=[
            dict(stationElement=station['stationElements'][0], values=[
                dict(date='2026-10-01', value=0, origValue=0, qcFlag='V', qaFlag='A'),
                dict(date='2026-10-02', value=None, origValue=None, qcFlag='M')]),
            dict(stationElement=station['stationElements'][1], values=[
                dict(date='2026-10-01', origValue=17.5, qcFlag='S', origQcFlag='S')])])]
        if responses is None:
            responses = [tf.Response(b'busy', 503), tf.Response(self.sn.canonical(self.valid)), tf.Response()]
        queue = iter(responses); self.calls = []
        def mock(request, timeout):
            self.calls.append(request.full_url)
            return next(queue)
        clock = tf.Clock(); clock.value = datetime(2026, 10, 7, 12, tzinfo=timezone.utc).timestamp()
        self.client = tf.transport.StationTransport(self.root/'transport', self.groups, opener=mock,
            wall=clock.now, monotonic=clock.now, sleep=clock.sleep, retry_delay=0, max_attempts=max_attempts)
        try: self.captures = self.client.collect()
        except tf.transport.Held: self.captures = self.client.saved_captures()
        self.pins = dict(expected_groups_sha256=self.sn.sha256(self.sn.canonical([
            dict(request=g['request'], metadata_sha256=g['metadata_sha256']) for g in self.groups])),
            expected_plan_sha256=self.client.plan_sha256,
            expected_capture_set_sha256=self.sn.sha256(self.sn.canonical([c['receipt_sha256'] for c in self.captures])))
        return self.captures

    def bridge(self, groups=None, captures=None, pins=None):
        return o.offline_snotel_capture_bridge(self.bundle, self.request,
            self.groups if groups is None else groups, self.captures if captures is None else captures,
            **(self.pins if pins is None else pins))

    def repin(self, captures):
        for cap in captures:
            cap['receipt_sha256'] = self.sn.sha256(self.sn.canonical(cap['receipt']))
        return dict(self.pins, expected_capture_set_sha256=self.sn.sha256(self.sn.canonical([c['receipt_sha256'] for c in captures])))

    def test_retry_science_empty_and_physical_order(self):
        self.retained(); original = copy.deepcopy(self.captures)
        events = self.bridge(); self.assertEqual(len(events), 8)
        self.assertEqual([e['payload']['history']['captures'][0]['receipt']['physical_attempt'] for e in events], [1,1,2,2,2,2,3,3])
        self.bundle['events'] = events; _, cp = self.run_fixture()
        ids = self.request['selection']['snotel']; states = cp['reconciliation']['identities']
        zero = states['snotel:'+ids[0]]
        self.assertEqual(zero['attempts'][0]['outcome'], 'FAILED')
        self.assertEqual(zero['attempts'][0]['affected_dates'], [])
        row = zero['observations']['2026-10-01']['rows'][0]
        self.assertEqual(row['provider_record'], self.valid[0]['data'][0]['values'][0])
        self.assertEqual(row['source_timestamp_utc'], '2026-10-02T08:00:00Z')
        self.assertEqual(zero['attempts'][-1]['outcome'], 'MISSING')
        suspect = events[4]['payload']['history']['observations'][-1]
        self.assertEqual(suspect['original_value'],17.5)
        self.assertNotIn('value',suspect['provider_record'])
        self.assertEqual(states['snotel:'+ids[2]]['outcome'],'COMPLETE_VALID_EMPTY')
        self.assertEqual(self.captures,original)
        self.assertEqual(self.bridge(),events)

    def test_never_queried_and_no_transport_reopen(self):
        import test_snotel_transport as tf
        self.retained(responses=[tf.Response()], max_attempts=1)
        before = len(self.calls)
        with patch.object(tf.transport.StationTransport,'collect',side_effect=AssertionError('collection')), \
             patch.object(tf.transport.StationTransport,'saved_captures',side_effect=AssertionError('I/O')):
            self.bundle['events'] = self.bridge()
        _,cp = self.run_fixture()
        self.assertEqual(cp['reconciliation']['identities']['snotel:'+self.request['selection']['snotel'][2]]['outcome'],'NOT_QUERIED')
        self.assertEqual(len(self.calls),before)

    def test_original_bindings_and_order_fail_closed(self):
        self.retained()
        for field in self.pins:
            with self.subTest(pin=field), self.assertRaises(ValueError):
                self.bridge(pins=dict(self.pins,**{field:'0'*64}))
        for mutation in ('body','receipt','reverse','duplicate','missing'):
            caps=copy.deepcopy(self.captures)
            if mutation=='body':caps[1]['body']+=b' '
            elif mutation=='receipt':caps[1]['receipt']['request']['parameters']['elements']='SMS:-20:2'
            elif mutation=='reverse':caps.reverse()
            elif mutation=='duplicate':caps[1]=copy.deepcopy(caps[0])
            else:caps.pop(0)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.bridge(captures=caps)
        groups=copy.deepcopy(self.groups);groups[0]['metadata_body']+=b' '
        with self.assertRaises(ValueError):self.bridge(groups=groups)

    def test_accounting_and_missing_original_provenance_rejected(self):
        self.retained()
        for field,value in [('body_accounting_state','HELD'),('total_consumed_body_bytes',None),
                            ('captured_prefix_bytes',999),('physical_attempt',4),('complete',True),
                            ('plan_sha256','0'*64),('response_sha256','0'*64),('http_status',None)]:
            caps=copy.deepcopy(self.captures);caps[0]['receipt'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):self.bridge(captures=caps,pins=self.repin(caps))
        caps=copy.deepcopy(self.captures);del caps[0]['receipt']['physical_attempt']
        with self.assertRaises((ValueError,KeyError)):self.bridge(captures=caps,pins=self.repin(caps))

    def test_invalid_http_200_preserves_failure_without_checked_days(self):
        self.retained()
        invalid = [b'{', b'{"data":[],"data":[]}', b'[NaN]']
        for mutator in ('station','depth','ordinal','omitted','duplicate','date'):
            value=copy.deepcopy(self.valid)
            if mutator=='station':value[0]['stationTriplet']='99999:CA:SNTL'
            elif mutator=='depth':value[0]['data'][0]['stationElement']['heightDepth']=-99
            elif mutator=='ordinal':value[0]['data'][0]['stationElement']['ordinal']=2
            elif mutator=='omitted':value[0]['data'].pop()
            elif mutator=='duplicate':value[0]['data'].append(copy.deepcopy(value[0]['data'][0]))
            else:value[0]['data'][0]['values'][0]['date']='2026-10-03'
            invalid.append(self.sn.canonical(value))
        for body in invalid:
            caps=copy.deepcopy(self.captures);cap=caps[1];cap['body']=body
            cap['receipt'].update(response_bytes=len(body),response_sha256=self.sn.sha256(body),captured_prefix_bytes=len(body),total_consumed_body_bytes=len(body))
            events=self.bridge(captures=caps,pins=self.repin(caps))
            failed=[e for e in events if e['payload']['history']['captures'][0]['receipt']['physical_attempt']==2]
            self.assertTrue(all(e['kind']=='FAILED' and e['reason'].startswith('INVALID_RESPONSE:') for e in failed))
            self.bundle['events']=events;roster=o.offline_roster(self.bundle,self.request)
            states=o.offline_reconcile(self.bundle,self.request,roster)['identities']
            self.assertTrue(all(a['affected_dates']==[] for a in states['snotel:'+self.request['selection']['snotel'][0]]['attempts']))

    def test_exact_group_selection_window_and_no_offline_optin(self):
        self.retained()
        for mutation in ('missing','duplicate','window','optin'):
            request=copy.deepcopy(self.request)
            if mutation=='missing':self.request['selection']['snotel'].pop()
            elif mutation=='duplicate':self.request['selection']['snotel'].append(self.request['selection']['snotel'][0])
            elif mutation=='window':self.request['cutoffs']['snotel']['end']='2026-10-03'
            else:self.request['offline_only']=False
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.bridge()
            self.request=request

    def test_bridge_checkpoint_prefix_resume_and_immutability(self):
        self.retained();self.bundle['events']=self.bridge()
        _,first=self.run_fixture('prefix',max_events=3)
        path=self.root/'prefix/CHECKPOINT_RECONCILIATION_DRYRUN.json';raw=path.read_bytes()
        import hashlib
        pin=dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest())
        _,resumed=self.run_fixture('resumed',resume=pin);_,replayed=self.run_fixture('replayed')
        self.assertEqual(resumed,replayed);self.assertEqual(path.read_bytes(),raw)
        self.assertEqual(first['reconciliation']['pending_events'],5)

    def test_pinned_budget_is_not_replaced_by_hard_ceiling(self):
        self.retained()
        for field,value in [('attempts',0),('response_bytes',1)]:
            saved=self.request['budgets']['snotel'][field]
            self.request['budgets']['snotel'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):self.bridge()
            self.request['budgets']['snotel'][field]=saved

    def test_terminal_retry_and_cross_station_clock_rejected(self):
        self.retained()
        for kind in ('terminal200','terminal404','clock','too_many'):
            caps=copy.deepcopy(self.captures)
            if kind.startswith('terminal'):
                status=int(kind[-3:]);caps[0]['receipt'].update(http_status=status,complete=status==200)
            elif kind=='clock':caps[-1]['receipt']['retrieved_at_utc']='2026-10-06T12:00:00Z'
            else:
                caps=[copy.deepcopy(caps[0]) for _ in range(4)]
                for i,c in enumerate(caps,1):c['receipt']['physical_attempt']=i
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.bridge(captures=caps,pins=self.repin(caps))

    def test_no_capture_is_not_valid_empty_and_holds_survive(self):
        self.retained()
        events=self.bridge(captures=[],pins=self.repin([]));self.assertEqual(events,[])
        roster=o.offline_roster(self.bundle,self.request)
        states=o.offline_reconcile(dict(self.bundle,events=events),self.request,roster)['identities']
        self.assertTrue(all(states['snotel:'+i]['outcome']=='NOT_QUERIED' for i in self.request['selection']['snotel']))
        row=self.bundle['roster'][0];row['eligibility']['public']='HELD';row['holds']=['PUBLIC_AUTHORITY_HELD']
        events=self.bridge();roster=o.offline_roster(self.bundle,self.request)
        state=o.offline_reconcile(dict(self.bundle,events=events),self.request,roster)['identities']['snotel:'+row['identity']]
        self.assertEqual(state['observations'],{})
        self.assertIn('HELD_ELIGIBILITY',[a['outcome'] for a in state['attempts']])



class SnotelCompactReplayTests(unittest.TestCase):
    setUp = OfflineWY2027Tests.setUp
    retained = SnotelCaptureBridgeTests.retained

    def fixture(self):
        self.retained()
        legacy = o.offline_snotel_capture_bridge(self.bundle, self.request, self.groups, self.captures, **self.pins)
        compact = o.offline_snotel_capture_bridge(self.bundle, self.request, self.groups, self.captures, compact=True, **self.pins)
        return dict(self.bundle, events=legacy), o.offline_append_snotel_compact(self.bundle, compact), compact

    def replay(self, bundle, limit=None):
        return o.offline_reconcile(bundle, self.request, o.offline_roster(bundle, self.request), limit)

    def test_compact_legacy_equivalence_and_partial_replay(self):
        legacy, compact, bridge = self.fixture()
        self.assertEqual(len(bridge['snotel_history_pool']['members']), 3)
        for limit in (None, 0, 3, len(legacy['events'])):
            self.assertEqual(self.replay(legacy, limit), self.replay(compact, limit))
        self.assertLess(len(self.sn.canonical(compact)), len(self.sn.canonical(legacy)))
        states = self.replay(compact)['identities']
        self.assertIn('WITHHELD', {a['outcome'] for s in states.values() for a in s['attempts']})
        self.assertIn('MISSING', {a['outcome'] for s in states.values() for a in s['attempts']})

    def test_compact_reference_corruption_and_closure(self):
        _, base, _ = self.fixture()
        def cases(b, kind):
            es=b['events']; ms=b['snotel_history_pool']['members']
            if kind=='missing_pool':del b['snotel_history_pool']
            elif kind=='missing_member':ms.pop()
            elif kind=='duplicate_member':ms.append(copy.deepcopy(ms[0]))
            elif kind=='reordered_members':ms.reverse()
            elif kind=='duplicate_event':es.append(copy.deepcopy(es[-1]))
            elif kind=='missing_event':es.pop()
            elif kind=='reordered_events':es[2],es[3]=es[3],es[2]
            elif kind=='wrong_date':es[-1]['payload']['date']='2026-10-03'
            elif kind=='wrong_identity':es[-1]['identity']=es[0]['identity']
            elif kind=='mixed_payload':es[0]['payload']['history']={}
            elif kind=='tampered_bytes':ms[0]['body_base64']='e30='
            elif kind=='tampered_size':ms[0]['bytes']+=1
            elif kind=='failed_reason':es[0]['reason']='invented'
            elif kind=='wrong_ref_version':es[0]['payload']['history_ref']['version']='unknown'
            elif kind=='wrong_event_id':es[0]['event_id']='invented'
            elif kind=='wrong_clock':es[0]['source_checked_at']='2026-10-06T12:00:00Z'
            elif kind=='unused_pool':es.clear()
            elif kind=='interleaved_legacy':es.insert(3,copy.deepcopy(self.fixture_legacy[0]))
        self.fixture_legacy=o.offline_snotel_capture_bridge(self.bundle,self.request,self.groups,self.captures,**self.pins)
        for kind in ('missing_pool','missing_member','duplicate_member','reordered_members','duplicate_event','missing_event','reordered_events','wrong_date','wrong_identity','mixed_payload','tampered_bytes','tampered_size','failed_reason','wrong_ref_version','wrong_event_id','wrong_clock','unused_pool','interleaved_legacy'):
            with self.subTest(kind=kind):
                b=copy.deepcopy(base);cases(b,kind)
                with self.assertRaises((ValueError,KeyError)):self.replay(b,0)

    def test_compact_serialized_event_and_expansion_limits(self):
        _, base, bridge = self.fixture()
        oversized=copy.deepcopy(base);oversized['padding']='x'*(32*1024**2)
        with self.assertRaisesRegex(ValueError,'Combined compact replay byte bound'):self.replay(oversized)
        many=copy.deepcopy(base);many['events']=[many['events'][0]]*10001
        with self.assertRaisesRegex(ValueError,'Offline event bound'):self.replay(many)
        huge=copy.deepcopy(bridge);huge['events'][0]['padding']='x'*(64*1024**2)
        with self.assertRaisesRegex(ValueError,'Offline event expansion bound'):o.offline_append_snotel_compact(self.bundle,huge)
        extra=copy.deepcopy(bridge);extra['snotel_history_pool']['unknown']=True
        with self.assertRaisesRegex(ValueError,'Compact bridge pool fields'):o.offline_append_snotel_compact(self.bundle,extra)
        # Original history bytes cannot be changed and hidden behind a new hash.
        import base64
        broken=copy.deepcopy(base);m=broken['snotel_history_pool']['members'][0]
        doc=json.loads(base64.b64decode(m['body_base64']));doc['query_ledger'][0]['sensor_identity']='wrong'
        raw=self.sn.canonical(doc);m.update(sha256=self.sn.sha256(raw),bytes=len(raw),body_base64=base64.b64encode(raw).decode())
        with self.assertRaisesRegex(ValueError,'derived history mismatch'):self.replay(broken)

    def test_compact_transport_accounting_and_derived_expansion(self):
        import base64
        _, base, _ = self.fixture()
        for field,value in (('body_accounting_state','HELD'),('total_consumed_body_bytes',None),('captured_prefix_bytes',0),('physical_attempt',0),('plan_sha256','wrong')):
            broken=copy.deepcopy(base);member=broken['snotel_history_pool']['members'][0]
            doc=json.loads(base64.b64decode(member['body_base64']));cap=doc['captures'][0];cap['receipt'][field]=value
            capture=dict(body=base64.b64decode(cap['body_base64']),receipt=cap['receipt'],receipt_sha256=self.sn.sha256(self.sn.canonical(cap['receipt'])))
            rebuilt=self.sn.build_history(base64.b64decode(doc['metadata']['body_base64']),doc['metadata']['sha256'],[capture])
            raw=self.sn.canonical(rebuilt);member.update(sha256=self.sn.sha256(raw),bytes=len(raw),body_base64=base64.b64encode(raw).decode())
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'Compact original transport|Compact incomplete'):self.replay(broken)
        # Derivation itself is bounded even when the supplied ref list is tiny.
        with patch.object(o,'_snotel_events',return_value=iter([base['events'][0]]*10001)):
            with self.assertRaisesRegex(ValueError,'Compact reference expansion bound'):self.replay(base)



if __name__=='__main__':unittest.main()
