"""Network-denied version-5 raw archives and incompatible product boundaries."""
import copy
from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
from dendra.history_acquisition import native_program as n, native_snapshot as ns, local_job as l
from dendra.history_acquisition import daily_handoff, sealed_history, latest_observation, browser_projection
from dendra.history_acquisition.journal import Journal
from dendra.history_acquisition.safety import encode, digest, sha, Hold
from dendra.transport import format_utc, parse_utc

ORG='a'*24
STATION='b'*24
SIDS=['1'*24,'2'*24]
START='2026-10-01T08:00:00.000Z'
MID='2026-10-02T08:00:00.000Z'
END='2026-10-03T08:00:00.000Z'
NOW='2026-10-04T16:20:20.000Z'


class NativeProgramTests(unittest.TestCase):
    def setUp(self):
        root=Path(os.environ['DENDRA_TEST_ROOT']);root.mkdir(parents=True,exist_ok=True)
        self.root=Path(tempfile.mkdtemp(prefix='native-v5-',dir=root))
        self.family=dict(provider='Dendra',organization_id=ORG,organization_name='Synthetic family',subprovider_label='Dendra-Synthetic family')
        stream_rows=[]
        for sid in SIDS:
            stream_rows.append(dict(_id=sid,station_id=STATION,organization_id=ORG,public_level=3,is_hidden=False,
                terms=dict(ds=dict(Medium='Soil',Variable='VolumetricWaterContent'),dt=dict(Unit='Dimensionless')),
                attributes=dict(orientation='vertical',depth=dict(value=5,unit_tag='dt_Unit_Centimeter')) if sid==SIDS[0]
                    else dict(orientation='vertical'),general_config_resolved=dict(sample_interval=600000),
                datapoints_config=[dict(begins_at=START,path='/synthetic/provider')]))
        raw=self.write('raw.json',dict(data=stream_rows))
        station=self.write('station.json',dict(_id=STATION,organization_id=ORG,name='Synthetic station',public_level=3,is_hidden=False))
        org=self.write('organization.json',dict(_id=ORG,name='Synthetic family'))
        review=self.write('prior-review.json',dict(provider='Dendra',review_acceptance='PRIOR_BASELINE_PRESERVED',
            station_id=STATION,stream_id=SIDS[0],native_unit='Dimensionless',subprovider_key=ORG,
            depth_geometry=dict(state='ALREADY_ACCEPTED_UNCHANGED',accepted_depth_cm=5),frozen_depth_cm_unchanged=5))
        rows=[]
        for i,sid in enumerate(SIDS):
            rows.append(dict(provider='Dendra',organization_id=ORG,organization_name='Synthetic family',
                station_id=STATION,station_name='Synthetic station',stream_id=sid,target_soil_moisture=True,
                acquisition_eligible=True,science_status='SCIENCE_READY' if i==0 else 'NATIVE_ONLY_MULTIPLE_UNRESOLVED',
                depth_cm=5 if i==0 else None,depth_status='ACCEPTED' if i==0 else 'UNRESOLVED',native_unit='Dimensionless',
                orientation='vertical',science_review_reference=review if i==0 else None,
                metadata_reference=dict(raw,json_pointer=f'/data/{i}'),station_metadata_reference=station,
                organization_reference=org,subprovider_key=ORG,subprovider_name='Synthetic family',subprovider_label='Dendra-Synthetic family',
                cadence_seconds=600,cadence_ms=600000,cadence_reference=dict(raw,json_pointer=f'/data/{i}/general_config_resolved/sample_interval'),
                query_start=START,query_start_basis='PROVIDER_CONFIGURATION_BOUNDED_QUERY_START_NOT_OBSERVATION_POR_PROOF',
                query_start_reference=dict(raw,json_pointer=f'/data/{i}/datapoints_config')))
        self.snapshot=self.write('snapshot.json',dict(schema_version=ns.VERSION,fixed_cutoff=NOW,records=rows))
        self.fixture=self.write('fixture.json',dict(now=NOW,history={START:dict(data=[dict(t=START,v=0,datastream_id=SIDS[0])],limit=2016),
                            MID:dict(data=[],limit=2016)}))
        self.c=dict(version=n.VERSION,family=self.family,snapshot=self.snapshot,streams=SIDS,station_ids=[STATION],
                    stream_scopes={SIDS[0]:[dict(start=START,end=MID)],SIDS[1]:[dict(start=MID,end=END)]},
                    science_states={r['stream_id']:r['science_status'] for r in rows},
                    reuse=self.write('reuse.json',dict(references=[])),sources=l.sources(),limits=dict(attempts=6,bytes=64*1024**2),
                    execution_window_seconds=12600,reserve_bytes=l.BODY,root=str(self.root/'job'),enabled=False,fixture=self.fixture)
        self.path=self.root/'config.json';self.save_config()

    def write(self,name,value):
        p=self.root/name;p.write_bytes(encode(value));return dict(path=str(p),sha256=sha(p.read_bytes()))

    def save_config(self):self.path.write_bytes(encode(self.c))

    def job(self):
        c,s,t=n.config(self.path);f=ns.reference(c['fixture']) if c['fixture'] else None
        return n.ProgramJob(c,s,t,clock=l.Clock(f),fixture=f,config_path=self.path)

    def no_network(self):
        return patch('socket.socket',side_effect=AssertionError('Network prohibited'))

    def test_mixed_raw_empty_resume_and_real_product_guards(self):
        with self.no_network(),self.job().open(create=True) as j:
            self.assertIsNone(j.execution_window())
            result=j.acquire();self.assertEqual(result['accounting']['sealed'],2)
            self.assertEqual(result['accounting']['covered_empty'],1)
            self.assertEqual(result['accounting']['attempts'],2)
            before=copy.deepcopy(result['accounting']);j.acquire();self.assertEqual(j.accounting(),before)
            for a in before['assets']:
                with j.child(a['root'],a['campaign_id']) as child:
                    key=a['task_id'];env=child.completed(key)
                    self.assertFalse(env['product_eligible'])
                    if child.tasks[key]['identity']['stream_id']==SIDS[1]:self.assertIsNone(env['identity']['depth_cm'])
                    with self.assertRaises(Hold):child.daily_evidence(key,'2026-10-01','0'*64)
                    with self.assertRaises(Hold):daily_handoff._binding(child.binding,child.tasks,None,digest(child.binding['collector_sources']))
                    entry={k:None for k in sealed_history.ENTRY_FIELDS}
                    entry.update(header_sha256=child.header_sha,source_fingerprint=digest(child.binding['collector_sources']))
                    with self.assertRaises(Hold):sealed_history.referenced_archive(child,entry,None)
                    with self.assertRaises((Hold,KeyError,TypeError)):
                        latest_observation.validate_binding(child.binding,child.tasks,inventory=None)
                    with self.assertRaises(Hold):browser_projection.render(env,env['rows'])
                    # Even re-labelling an envelope as a daily file fails the actual exporter schema gate.
                    pins=[]
                    for name in ('handoff.json','daily-output.json','result.json','r-receipt.json'):
                        path=self.root/name;path.write_bytes(encode(env))
                        pins.append(dict(path=name,bytes=path.stat().st_size,sha256=sha(path.read_bytes())))
                    with self.assertRaisesRegex(Hold,'Preparation source mismatch'):
                        browser_projection.load_prepared(self.root,pins=pins,prepared_fingerprint='0'*64)
        raw=json.loads((self.root/'raw.json').read_text())
        self.assertEqual(raw['data'][1]['attributes'],{'orientation':'vertical'})

    def test_exact_roster_family_scope_hash_null_enforcement(self):
        changes=[lambda c:c['family'].update(organization_id='c'*24),
                 lambda c:c['streams'].append('3'*24),lambda c:c.update(station_ids=['c'*24]),
                 lambda c:c['stream_scopes'][SIDS[0]][0].update(start='2026-09-01T08:00:00.000Z'),
                 lambda c:c['science_states'].update({SIDS[1]:'SCIENCE_READY'}),
                 lambda c:c['snapshot'].update(sha256='0'*64)]
        original=copy.deepcopy(self.c)
        for change in changes:
            self.c=copy.deepcopy(original);change(self.c);self.save_config()
            with self.assertRaises((Hold,KeyError)):n.config(self.path)
        self.c=original;self.save_config()
        data=ns.reference(self.snapshot);data['records'][1]['depth_cm']=30
        self.c['snapshot']=self.write('snapshot.json',data);self.save_config()
        with self.assertRaisesRegex(Hold,'NULL'):n.config(self.path)

    def test_raw_timestamps_nulls_duplicates_and_flags_preserved(self):
        f=ns.reference(self.fixture)
        rows=[dict(t='2026-10-01T08:00:00.000000Z',v=None,datastream_id=SIDS[0]),
              dict(t='2026-10-01T08:00:00.000000Z',v=None,datastream_id=SIDS[0]),
              dict(t='2026-10-01T08:10:00.000Z',v=0,datastream_id=SIDS[0],q={'flag':['M']})]
        f['history'][START]['data']=rows
        self.c['fixture']=self.write('fixture.json',f);self.save_config()
        with self.no_network(),self.job().open(create=True) as j:
            result=j.acquire();a=result['accounting']['assets'][0]
            with j.child(a['root'],a['campaign_id']) as child:self.assertEqual(child.completed(a['task_id'])['rows'],rows)

    def test_failure_spending_preserved_and_no_retry(self):
        f=ns.reference(self.fixture);f['history'][START]=dict(status=500,body={})
        self.c['fixture']=self.write('fixture.json',f);self.save_config()
        with self.no_network(),self.job().open(create=True) as j:
            with self.assertRaises(Exception):j.acquire()
            first=j.accounting();self.assertEqual(first['attempts'],1);self.assertTrue(first['spent_unsealed'])
            with self.assertRaises(Hold):j.acquire()
            self.assertEqual(j.accounting()['attempts'],1)

    def test_stream_access_hold_does_not_block_other_stream(self):
        f=ns.reference(self.fixture);f['history'][START]=dict(status=403,body={})
        self.c['fixture']=self.write('fixture.json',f);self.save_config()
        with self.no_network(),self.job().open(create=True) as j:
            result=j.acquire();self.assertEqual(result['accounting']['sealed'],1)
            self.assertEqual(result['accounting']['held_streams'],[SIDS[0]])
            self.assertEqual(result['accounting']['attempts'],2)
            j.acquire();self.assertEqual(j.accounting()['attempts'],2)

    def test_window_continuation_preserves_first_window_and_accounting(self):
        with self.no_network(),self.job().open(create=True) as j:
            with patch.object(j,'stop_requested',side_effect=[False,True]):j.acquire()
            first=j.get('window.json');a=j.accounting();self.assertEqual(a['sealed'],1)
            j.clock.value=parse_utc(first['deadline'])+timedelta(seconds=1)
            opened=j.continue_window();self.assertEqual(opened['window']['accounting_before']['attempts'],1)
            result=j.acquire();self.assertEqual(result['accounting']['sealed'],2)
            self.assertEqual(j.get('window.json'),first);self.assertEqual(result['accounting']['attempts'],2)
            j.clock.value=parse_utc(opened['window']['deadline'])+timedelta(seconds=1)
            with self.assertRaisesRegex(Hold,'executable'):j.continue_window()
            p=j.root/'continuation-windows/0001.json';bad=json.loads(p.read_bytes());bad['deadline']=END;p.write_bytes(encode(bad))
            with self.assertRaisesRegex(Hold,'continuation changed'):j.window_records()

    def test_zero_attempt_existing_child_resumes_and_stop_works_during_writer(self):
        with self.no_network(),self.job().open(create=True) as j:
            binding,tasks=n.task_binding(self.path,j.c,j.inventory,j.tasks[0])
            child=j.root/'history/0';child.mkdir(parents=True)
            (child/'prepared.json').write_bytes(encode(dict(binding=binding,tasks=tasks)))
            with Journal(child,binding,tasks,create=True,inventory=j.inventory):pass
            args=SimpleNamespace(mode='stop',config=str(self.path),offline_now=None,allow_provider=False)
            self.assertEqual(n.main(args)['outcome'],'STOP_REQUESTED_NO_DISPATCH')
            self.assertEqual(j.acquire()['accounting']['attempts'],0)
            j.acknowledge_stop();self.assertEqual(j.acquire()['accounting']['sealed'],2)

    def test_asset_map_mutation_and_false_science_proofs_rejected(self):
        with self.job().open(create=True) as j:
            p=j.root/'asset-map.json';v=json.loads(p.read_bytes());v['product_eligible']=True;p.write_bytes(encode(v))
            with self.assertRaisesRegex(Hold,'mutation'):j.verify_scope()
        self.c['root']=str(self.root/'other-job')
        d=ns.reference(self.snapshot);d['records'][0]['science_review_reference']=self.write('bad-review.json',dict(unrelated=True))
        self.c['snapshot']=self.write('bad-snapshot.json',d);self.save_config()
        with self.assertRaises(Hold):n.config(self.path)

    def test_foreign_cadence_and_first_witness_rejected(self):
        data=ns.reference(self.snapshot)
        data['records'][1]['cadence_reference']=data['records'][0]['cadence_reference']
        self.c['snapshot']=self.write('foreign-cadence.json',data);self.save_config()
        with self.assertRaisesRegex(Hold,'Cadence'):n.config(self.path)
        data=ns.reference(self.snapshot);r=data['records'][1]
        r['query_start_basis']='RETAINED_REVIEWED_FIRST_OBSERVATION'
        r['query_start_reference']=self.write('foreign-witness.json',dict(state='REVIEWED_SOURCE_START',
            start=START,journal_evidence=dict(schema_version='dendra-journal-first-evidence-1',
                identity=dict(stream_id=SIDS[0],station_id=STATION,native_unit='Dimensionless'),
                result=dict(state='FIRST_OBSERVATION',timestamp=START))))
        self.c['snapshot']=self.write('foreign-first.json',data);self.save_config()
        with self.assertRaisesRegex(Hold,'exact identity'):n.config(self.path)

    def test_complete_reuse_joins_original_journal_and_rejects_forged_coverage(self):
        from dendra.history_acquisition import native_reuse
        with self.no_network(),self.job().open(create=True) as j:
            a=j.acquire()['accounting']['assets'][0]
            with j.child(a['root'],a['campaign_id']) as child:
                task=child.tasks[a['task_id']];state=child.snapshot();seal=state['intervals'][a['task_id']]['complete']
                event=next(e for e in child.events if e['kind']=='sealed');objects=[]
                for role,descriptors in [('normalized_archive',seal['objects'])]+[
                    ('provider_response',state['attempts'][k]['objects']) for k in seal['attempt_keys']]:
                    for d in descriptors:objects.append(dict(d,role=role,absolute_path=str(Path(a['root'])/child.prefix/d['path'])))
                p=Path(a['root'])/child._event_path(event['sequence'])
                ref=dict(root=a['root'],campaign_id=a['campaign_id'],task_id=a['task_id'],
                    station_id=STATION,stream_id=task['identity']['stream_id'],start=task['start'],end=task['end'],
                    state=seal['state'],header_sha256=child.header_sha,source_fingerprint=digest(child.binding['collector_sources']),
                    seal_sha256=event['record_sha256'],seal_file=dict(path=str(p),sha256=sha(p.read_bytes())),objects=objects)
            self.assertEqual(native_reuse.validate([ref],j.inventory)['references_verified'],1)
            for field,value in [('stream_id',SIDS[1]),('end',END),('state','complete_empty')]:
                bad=dict(ref);bad[field]=value
                with self.assertRaises(Hold):native_reuse.validate([bad],j.inventory)

    def test_expiring_window_defers_next_task_without_spending(self):
        self.c['execution_window_seconds']=300;self.save_config()
        with self.no_network(),self.job().open(create=True) as j:
            result=j.acquire();self.assertEqual(result['accounting']['sealed'],1)
            self.assertEqual(result['accounting']['attempts'],1)
            self.assertFalse(result['accounting']['spent_unsealed'])
            self.assertFalse((j.root/'history/1').exists())
        d=ns.reference(self.snapshot);d['records'][1]['science_status']='EXCLUDED_NOT_TARGET_SOIL_MOISTURE'
        self.c['science_states'][SIDS[1]]=d['records'][1]['science_status']
        self.c['snapshot']=self.write('excluded-snapshot.json',d);self.save_config()
        with self.assertRaises(Hold):n.config(self.path)

    def test_fresh_r_prepare_status_validation_and_fixture_network_denial(self):
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
        for command in ['inspect','prepare','validate-scope','acquire','status']:
            run=subprocess.run(['Rscript','--vanilla',str(l.ENTRY),command,str(self.path)],env=env,capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            result=json.loads(run.stdout)
        self.assertEqual(result['accounting']['sealed'],2)
        self.assertEqual(result['accounting']['attempts'],2)

    def test_native_snapshot_mutation_holds_existing_job(self):
        with self.job().open(create=True):pass
        p=Path(self.snapshot['path']);p.write_bytes(p.read_bytes()+b' ')
        with self.assertRaisesRegex(Hold,'hash changed'):self.job()

    def cadence_config(self, milliseconds, *, sid=SIDS[1]):
        raw=ns.reference(self.snapshot)['records'][0]['metadata_reference']
        body=ns.reference(raw)
        for row in body['data']:
            if milliseconds is None:row['general_config_resolved'].pop('sample_interval')
            else:row['general_config_resolved']['sample_interval']=milliseconds
        new=self.write('cadence-raw.json',body)
        d=ns.reference(self.snapshot);d['fixed_cutoff']='2028-10-01T08:00:00.000Z'
        for row in d['records']:
            row['cadence_ms']=milliseconds
            row['cadence_seconds']=None if milliseconds is None else milliseconds/1000
            for field in ('metadata_reference','query_start_reference','cadence_reference'):
                row[field]=dict(new,json_pointer=row[field]['json_pointer'])
        self.c['snapshot']=self.write('cadence-snapshot.json',d)
        self.c['streams']=[sid];self.c['science_states']={sid:next(r['science_status'] for r in d['records'] if r['stream_id']==sid)}
        self.c['stream_scopes']={sid:[dict(start=MID,end=END)]};self.save_config()
        return d

    def page_fixture(self, count):
        rows=[dict(t=format_utc(parse_utc(MID)+timedelta(seconds=i)),v=None if i==0 else 0,
                   datastream_id=SIDS[1]) for i in range(count)]
        history={}
        for offset in range(0,count,2015):
            page=rows[offset:offset+2016]
            history[rows[offset]['t']]=dict(data=page,limit=2016)
            if len(page)<2016:break
        self.c['fixture']=self.write('paged.json',dict(now=NOW,history=history));self.save_config()
        return rows

    def test_365_day_and_nominal_sample_bound_interaction(self):
        self.cadence_config(86400000)
        stop=format_utc(parse_utc(MID)+timedelta(days=365))
        self.c['stream_scopes'][SIDS[1]]=[dict(start=MID,end=stop)];self.save_config()
        self.assertEqual(len(n.config(self.path)[2]),1)
        self.c['stream_scopes'][SIDS[1]][0]['end']=format_utc(parse_utc(stop)+timedelta(seconds=1));self.save_config()
        with self.assertRaises(Hold):n.config(self.path)
        self.cadence_config(3600000)
        stop=format_utc(parse_utc(MID)+timedelta(hours=4030))
        self.c['stream_scopes'][SIDS[1]]=[dict(start=MID,end=stop)];self.save_config()
        n.config(self.path)
        self.c['stream_scopes'][SIDS[1]][0]['end']=format_utc(parse_utc(stop)+timedelta(seconds=1));self.save_config()
        with self.assertRaises(Hold):n.config(self.path)

    def test_planner_chooses_tighter_bound_and_preserves_gaps(self):
        row=dict(query_start=START,cadence_seconds=3600,science_status='NATIVE_ONLY_UNRESOLVED_DEPTH')
        end=format_utc(parse_utc(START)+timedelta(days=366))
        plan=n.plan_intervals(row,[dict(start=START,end=end)],dict(seconds=3600))
        self.assertEqual(len(plan),3)
        self.assertEqual((parse_utc(plan[0]['end'])-parse_utc(START)).total_seconds(),4030*3600)
        row['cadence_seconds']=86400
        plan=n.plan_intervals(row,[dict(start=START,end=end)],dict(seconds=86400))
        self.assertEqual(len(plan),2)
        scopes=[dict(start=START,end=MID),dict(start=END,end=format_utc(parse_utc(END)+timedelta(days=2)))]
        self.assertEqual(n.plan_intervals(row,scopes),scopes)
        with self.assertRaises(Hold):n.plan_intervals(row,[dict(start=START,end=END),dict(start=MID,end=END)])

    def test_cadence_free_exact_quarantine_and_thirty_day_cap(self):
        self.cadence_config(None)
        c,s,t=n.config(self.path);self.assertIsNone(s.rows[SIDS[1]]['cadence_seconds'])
        self.assertEqual(n.chunk_seconds(s.rows[SIDS[1]]),30*86400)
        self.c['stream_scopes'][SIDS[1]][0]['end']=format_utc(parse_utc(MID)+timedelta(days=30,seconds=1));self.save_config()
        with self.assertRaises(Hold):n.config(self.path)
        self.assertEqual(n.chunk_seconds(dict(science_status='SCIENCE_READY',cadence_seconds=None)),30*86400)

    def test_cadence_absence_cannot_hide_reported_value_or_science_promotion(self):
        self.cadence_config(None,sid=SIDS[0])
        with self.assertRaisesRegex(Hold,'quarantine'):n.config(self.path)
        self.cadence_config(600000)
        d=ns.reference(self.c['snapshot']);d['records'][1]['cadence_seconds']=None;d['records'][1]['cadence_ms']=None
        self.c['snapshot']=self.write('false-absence.json',d);self.save_config()
        with self.assertRaises(Hold):n.config(self.path)
        for bad in (0,-1,float('inf'),True):
            with self.assertRaises(Hold):n.chunk_seconds(dict(cadence_seconds=bad))

    def test_cadence_free_complete_pages_preserve_native_rows_and_resume(self):
        self.cadence_config(None);rows=self.page_fixture(4030)
        with self.no_network(),self.job().open(create=True) as j:
            result=j.acquire();a=result['accounting']
            self.assertEqual((a['attempts'],a['sealed'],a['spent_unsealed']),(2,1,[]))
            asset=a['assets'][0]
            with j.child(asset['root'],asset['campaign_id']) as child:
                env=child.completed(asset['task_id'])
                self.assertEqual(env['rows'],rows[:2016]+rows[2015:])
                self.assertIsNone(env['identity']['depth_cm']);self.assertEqual(env['identity']['native_unit'],'Dimensionless')
                self.assertFalse(env['product_eligible'])
                with self.assertRaises(Hold):child.daily_evidence(asset['task_id'],'2026-10-02','0'*64)
            self.assertEqual(j.acquire()['accounting'],a)

    def test_cadence_free_full_third_page_preserves_spending_and_holds(self):
        self.cadence_config(None);self.page_fixture(6046)
        with self.no_network(),self.job().open(create=True) as j:
            with self.assertRaises(Exception):j.acquire()
            a=j.accounting();self.assertEqual(a['attempts'],3);self.assertEqual(a['sealed'],0)
            self.assertTrue(a['spent_unsealed']);self.assertGreater(a['bytes'],0)
            with self.assertRaises(Hold):j.acquire()
            self.assertEqual(j.accounting(),a)

    def test_cadence_free_empty_and_attempt_reserve(self):
        self.cadence_config(None)
        self.c['limits']['attempts']=2;self.save_config()
        with self.assertRaises(Hold):n.config(self.path)
        self.c['limits']['attempts']=3;self.save_config()
        with self.no_network(),self.job().open(create=True) as j:
            a=j.acquire()['accounting'];self.assertEqual((a['sealed'],a['covered_empty'],a['attempts']),(1,1,1))

    def test_native_byte_reserve_holds_before_spending_or_window(self):
        self.cadence_config(None)
        self.c['limits']['bytes']=3*l.BODY-1;self.save_config()
        with self.no_network(),self.job().open(create=True) as j:
            with self.assertRaisesRegex(Hold,'budget'):j.acquire()
            a=j.accounting();self.assertEqual((a['attempts'],a['bytes']),(0,0))
            self.assertIsNone(j.execution_window());self.assertFalse((j.root/'history').exists())

    def test_cached_accounting_equals_full_and_fresh_readback(self):
        with self.no_network(),self.job().open(create=True) as j:
            a=j.acquire()['accounting']
            with patch.object(n,'MAX_TASKS',2), patch.object(n,'raw_envelope',wraps=n.raw_envelope) as replay:
                self.assertEqual(j.accounting(),a);self.assertEqual(replay.call_count,0)
                self.assertEqual(j.accounting(full=True),a);self.assertEqual(replay.call_count,2)
                self.assertEqual(j.accounting(),a);self.assertEqual(replay.call_count,2)
        with self.job().open() as fresh:
            with patch.object(n,'raw_envelope',wraps=n.raw_envelope) as replay:
                self.assertEqual(fresh.accounting(),a);self.assertEqual(replay.call_count,2)

    def test_cached_accounting_still_rehashes_every_body_and_seal(self):
        with self.no_network(),self.job().open(create=True) as j:
            a=j.acquire()['accounting'];asset=a['assets'][0]
            files=list((Path(asset['root'])/'campaigns'/asset['campaign_id']/'objects').glob('*.bin'))
            for p in files:
                old=p.read_bytes();p.write_bytes(old+b' ')
                try:
                    with self.assertRaises(Hold):j.accounting()
                finally:p.write_bytes(old)
            self.assertEqual(j.accounting(),a)

    def test_cached_accounting_still_checks_anchor_config_source_and_totals(self):
        with self.no_network(),self.job().open(create=True) as j:
            a=j.acquire()['accounting'];asset=a['assets'][0]
            p=next((Path(asset['root'])/'anchors'/asset['campaign_id']).glob('*.json'));old=p.read_bytes();p.write_bytes(old+b' ')
            try:
                with self.assertRaises(Hold):j.accounting()
            finally:p.write_bytes(old)
            with patch.object(n,'source_binding',return_value={}):
                with self.assertRaises(Hold):j.accounting()
            original=self.path.read_bytes();self.path.write_bytes(original+b' ')
            try:
                with self.assertRaises(Hold):j.accounting()
            finally:self.path.write_bytes(original)
            old=j.get('status.json');bad=copy.deepcopy(old);bad['accounting']['attempts']+=1;j.summary(bad)
            try:
                with self.assertRaises(Hold):j.accounting()
            finally:j.summary(old)

    def test_verified_json_cache_has_no_mutable_alias_and_rechecks_hash(self):
        first=ns.reference(self.snapshot);first['records'].clear()
        self.assertEqual(len(ns.reference(self.snapshot)['records']),2)
        p=Path(self.snapshot['path']);p.write_bytes(p.read_bytes()+b' ')
        with self.assertRaises(Hold):ns.reference(self.snapshot)
        with self.assertRaises(Hold):ns.checked_decode(b'{"x":1,"x":2}',sha(b'{"x":1,"x":2}'))

    def test_throughput_metrics_preserved_by_fresh_status(self):
        with self.no_network(),self.job().open(create=True) as j:
            result=j.acquire();metrics=result['throughput']
            self.assertEqual(metrics['executor_calls'],2)
            self.assertEqual(len(metrics['provider_request_seconds']),2)
            for key in ('provider_io_seconds','parse_seconds','journal_and_seal_seconds','validation_seconds','accounting_seconds'):
                self.assertGreaterEqual(metrics[key],0)
            self.assertGreater(metrics['seal_semantic_cache_hits'],0)
        with self.job().open() as j:self.assertEqual(j.status()['throughput'],metrics)


class PaginationRecoveryTests(NativeProgramTests):
    # Inherit the fixture helpers, not a second copy of the compatibility suite.
    def make_failed(self, count=14000, recovery_attempts=6, byte_limit=512*1024**2):
        self.cadence_config(3600000)
        rows=self.page_fixture(count)
        f=ns.reference(self.c['fixture'])
        f['history'][START]=dict(data=[dict(t=START,v=1,datastream_id=SIDS[0])],limit=2016)
        self.c['fixture']=self.write('complete-fixture.json',f)
        self.c['streams']=SIDS
        self.c['science_states']={r['stream_id']:r['science_status'] for r in ns.reference(self.c['snapshot'])['records']}
        self.c['stream_scopes'][SIDS[0]]=[dict(start=START,end=MID)]
        self.c['limits']=dict(attempts=32,bytes=byte_limit);self.save_config()
        with self.no_network(),self.job().open(create=True) as j:
            with self.assertRaisesRegex(Hold,n.RECOVERY_REASON):j.acquire()
            self.before=j.accounting()
        self.frozen=n.original_files(Path(self.c['root']))
        self.config_bytes=self.path.read_bytes()
        self.request=n.make_recovery_request(self.path,max_attempts=recovery_attempts)
        self.request_path=Path(self.write('recovery-request.json',self.request)['path'])
        return rows

    def runner(self, now=None):
        clock=l.Clock(ns.reference(self.c['fixture']),now)
        return n.PaginationRecovery(self.path,self.request_path,clock=clock)

    def test_recovery_continues_exact_cursor_preserves_charges_seals_and_source_bytes(self):
        rows=self.make_failed();r=self.runner();calls=[]
        original=r.dispatch
        def record(request,timeout):
            calls.append(dict(n.parse_qsl(n.urlsplit(request.full_url).query))['time[$gte]'])
            return original(request,timeout)
        r.dispatch=record
        with self.no_network(),r.locked():
            r.prepare();result=r.acquire()
            self.assertEqual(result['accounting']['attempts'],self.before['attempts']+4)
            self.assertEqual(result['accounting']['sealed'],2)
            self.assertGreater(result['accounting']['bytes'],self.before['bytes'])
            self.assertFalse(result['original_scope_complete'])
            self.assertEqual(calls[0],rows[6045]['t'])
            self.assertEqual(calls,[rows[i]['t'] for i in (6045,8060,10075,12090)])
            self.assertEqual(r.acquire(),result);self.assertEqual(len(calls),4)
        self.assertEqual(n.original_files(Path(self.c['root'])),self.frozen)
        self.assertEqual(self.path.read_bytes(),self.config_bytes)
        b,t=r.binding()
        with Journal(r.output/'history',b,t,inspect_only=True) as j:
            key=next(iter(t));env=j.completed(key)
            self.assertEqual(env['rows'],rows)
            self.assertEqual(len(env['duplicate_copies']),6)
            self.assertEqual(env['raw_source_row_count'],14006)
            self.assertFalse(env['product_eligible'])
            self.assertEqual(env['prefix']['task_id'],self.request['prefix']['task_id'])
            from dendra.history_acquisition.native_reuse import _completed
            self.assertEqual(_completed(j,key,j.snapshot())[0],env)
        with self.job().open() as old:
            with self.assertRaisesRegex(Hold,'superseded'):old.acquire()
        run=subprocess.run(['Rscript','--vanilla',str(l.ENTRY),'status',str(self.path),'--recovery',str(self.request_path)],
                           capture_output=True,text=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        self.assertEqual(json.loads(run.stdout),result)

    def test_recovery_prefix_tamper_rejected_without_writes(self):
        self.make_failed()
        prefix=self.request['prefix'];obj=prefix['pages'][1]['object']
        p=Path(prefix['root'])/'campaigns'/prefix['campaign_id']/obj['path']
        p.write_bytes(p.read_bytes()+b' ')
        with self.assertRaises(Hold):self.runner()
        self.assertFalse((Path(self.c['root'])/n.RECOVERY_DIRECTORY).exists())

    def test_recovery_exhaustion_preserves_all_charges_and_forbids_replay(self):
        self.make_failed(recovery_attempts=2);r=self.runner()
        with self.no_network(),r.locked():
            r.prepare()
            with self.assertRaises(Hold):r.acquire()
            state=r.status();self.assertEqual(state['recovery_attempts'],2)
            self.assertEqual(state['accounting']['attempts'],6)
            self.assertIsNone(state['recovered_task'])
            with self.assertRaisesRegex(Hold,'cannot replay'):r.acquire()
            self.assertEqual(r.status(),state)

    def test_recovery_window_and_append_only_transition(self):
        self.make_failed();r=self.runner('2026-10-05T16:20:20.000Z')
        old=(Path(self.c['root'])/'window.json').read_bytes()
        with self.no_network(),r.locked():
            result=r.prepare();self.assertEqual(result['window']['kind'],'BOUNDED_RECOVERY_WINDOW')
            self.assertEqual((parse_utc(result['window']['deadline'])-parse_utc(result['window']['opened_at'])).total_seconds(),12600)
            with self.assertRaisesRegex(Hold,'Never recreate'):r.prepare()
            r.clock.value=parse_utc(result['window']['deadline'])
            with self.assertRaisesRegex(Hold,'window'):r.acquire()
            self.assertEqual(r.status()['recovery_attempts'],0)
        self.assertEqual((Path(self.c['root'])/'window.json').read_bytes(),old)

    def test_recovery_cannot_precede_original_receipts(self):
        self.make_failed();r=self.runner()
        self.assertGreaterEqual(parse_utc(r.clock.now()),parse_utc(r.old['last_original_event_at']))
        r.clock.now=lambda:format_utc(parse_utc(r.old['last_original_event_at'])-timedelta(seconds=1))
        with r.locked():
            with self.assertRaisesRegex(Hold,'precedes'):r.prepare()
        self.assertFalse(r.output.exists())

    def test_recovery_request_whole_job_limits_and_forged_binding(self):
        self.make_failed()
        for mutate in (lambda x:x['limits'].update(attempts=33,bytes=33*l.BODY),
                       lambda x:x['accounting_before'].update(attempts=0),
                       lambda x:x['prefix'].update(cursor=MID),
                       lambda x:x['execution_sources'].update(collector='0'*64)):
            request=copy.deepcopy(self.request);mutate(request);self.request_path.write_bytes(encode(request))
            with self.assertRaises(Hold):self.runner()
        self.request_path.write_bytes(encode(self.request))
        with self.assertRaisesRegex(Hold,'capacity'):n.make_recovery_request(self.path,max_attempts=32)

    def test_recovery_stop_no_dispatch(self):
        self.make_failed();r=self.runner()
        with self.no_network(),r.locked():
            r.prepare();r.stop()
            with self.assertRaisesRegex(Hold,'stopped'):r.acquire()
            self.assertEqual(r.status()['recovery_attempts'],0)

    def test_recovery_byte_capacity_and_transition_tamper(self):
        self.make_failed(byte_limit=64*1024**2)
        with self.assertRaisesRegex(Hold,'capacity'):n.make_recovery_request(self.path,max_attempts=8)
        r=self.runner()
        with r.locked():r.prepare()
        p=r.output/'transition.json';transition=l.read(p)
        transition['window']['deadline']='2027-01-01T00:00:00.000Z';p.write_bytes(encode(transition))
        with self.assertRaisesRegex(Hold,'transition changed'):r.status()

    def test_explicit_source_transition_preserves_original_config_and_accounting(self):
        self.make_failed()
        captured=dict(files=n.source_binding(),sources=copy.deepcopy(self.c['sources']))
        repaired=copy.deepcopy(self.c['sources'])
        repaired['checkpoint']=dict(head='f'*40,tree='e'*40)
        with patch.object(l,'sources',return_value=repaired),patch.object(n,'committed_source_files',return_value=encode(captured)):
            request=n.make_recovery_request(self.path,max_attempts=6)
            self.request_path.write_bytes(encode(request));r=self.runner()
            with r.locked():r.prepare()
            transition=r.transition()
            self.assertNotEqual(transition['original_sources'],transition['execution_sources'])
            self.assertEqual(transition['original_sources'],captured['sources'])
            self.assertEqual(transition['execution_sources'],repaired)
            self.assertEqual(transition['accounting_before']['attempts'],4)
            self.assertEqual(self.path.read_bytes(),self.config_bytes)
            self.assertEqual(n.original_files(Path(self.c['root'])),self.frozen)

    def test_explicit_historical_epochs_and_unknown_fallback(self):
        from dendra.history_acquisition.native_reuse import historical_density
        row=dict(stream_id=SIDS[0],query_start=START,cadence_seconds=3600)
        document=self.write('epochs.json',dict(stream=SIDS[0],start=START,end=END,milliseconds=600000))
        ref=dict(document=document,stream_pointer='/stream',start_pointer='/start',end_pointer='/end',milliseconds_pointer='/milliseconds')
        ev=dict(epochs=[ref],observations=[],historical_records=[])
        self.assertEqual(historical_density(row,ev,END)['seconds'],600)
        self.assertIsNone(historical_density(row,ev,'2026-10-04T08:00:00.000Z'))
        ev=dict(epochs=[],observations=[],historical_records=[ref])
        self.assertEqual(historical_density(row,ev,END)['basis'],'EXACT_HISTORICAL_RECORD')
        self.assertEqual(n.chunk_seconds(row),30*86400)

    def test_replacement_plan_cannot_initialize_before_conditional_recovery(self):
        self.make_failed()
        empty=dict(epochs=[],observations=[],historical_records=[])
        planning=self.write('planning.json',dict(version='dendra-historical-density-1',
            streams={sid:empty for sid in SIDS},decisions={sid:None for sid in SIDS},
            conditional_recoveries=[dict(config=self.request['config'],request=dict(
                path=str(self.request_path),sha256=sha(self.request_path.read_bytes())))]))
        c=copy.deepcopy(self.c);c.update(planning=planning,root=str(self.root/'replacement-unstarted'))
        p=Path(self.write('replacement.json',c)['path'])
        n.config(p)
        with self.assertRaises((Hold,OSError)):n.config(p,verify_reuse=True)
        self.assertFalse(Path(c['root']).exists())
        r=self.runner()
        with self.no_network(),r.locked():r.prepare();r.acquire()
        n.config(p,verify_reuse=True)
        self.assertFalse(Path(c['root']).exists())

    @unittest.skipUnless(os.environ.get('DENDRA_OLD_COMPLETED_CONFIG'),'Optional preserved completed v5 job')
    def test_original_completed_v5_readback_across_source_transition(self):
        path=Path(os.environ['DENDRA_OLD_COMPLETED_CONFIG']);root=Path(l.read(path)['root'])
        before=n.original_files(root)
        state=n.original_state(path,require_prefix=False)
        self.assertEqual(state['counts']['sealed'],len(state['tasks']))
        self.assertIsNone(state['prefix'])
        self.assertEqual(n.original_files(root),before)

    def test_historical_density_observed_spacing_overrides_current_nominal(self):
        self.make_failed()
        from dendra.history_acquisition.native_reuse import historical_density
        row=ns.Snapshot(self.c['snapshot']).rows[SIDS[1]]
        p=self.request['prefix'];ref={k:p[k] for k in ('root','campaign_id','task_id','header_sha256','source_fingerprint','last_anchor_sha256')}
        ev=dict(epochs=[],observations=[ref],historical_records=[])
        density=historical_density(row,ev,'2028-10-01T08:00:00.000Z')
        self.assertEqual(density['seconds'],1)
        self.assertEqual(n.chunk_seconds(row,density),4030)
        self.assertEqual(n.chunk_seconds(row),30*86400)
        self.assertIsNone(historical_density(row,dict(epochs=[],observations=[],historical_records=[]),END))
        plan=n.plan_intervals(row,[dict(start=MID,end=END)],density)
        self.assertTrue(all((parse_utc(t['end'])-parse_utc(t['start'])).total_seconds()<=4030 for t in plan))
        bad=copy.deepcopy(ev);bad['observations'][0]['task_id']='0'*64
        with self.assertRaises(Hold):historical_density(row,bad,END)

    @unittest.skipUnless(os.environ.get('DENDRA_FAILURE_EVIDENCE'),'Optional preserved production evidence')
    def test_exact_saved_carrizo_failures_and_600_second_planning(self):
        from dendra.history_acquisition.native_reuse import historical_density
        base=Path(os.environ['DENDRA_FAILURE_EVIDENCE']).resolve()
        for lane in 'AB':
            child=base/f'lane-{lane}-wave-001/history/3';saved=l.read(child/'prepared.json')
            with Journal(child,saved['binding'],saved['tasks'],inspect_only=True) as j:
                key=next(iter(j.tasks));p=n.prefix_evidence(j,key)
                self.assertEqual(p['attempts'],3);self.assertEqual(len(p['rows']),6048)
                self.assertEqual(n.pagination_failure(j,key)['code'],n.RECOVERY_REASON)
                p['root']=str(child)
                ref={k:p[k] for k in ('root','campaign_id','task_id','header_sha256','source_fingerprint','last_anchor_sha256')}
                row=dict(j.tasks[key]['identity'],query_start=j.tasks[key]['start'],cadence_seconds=3600)
            density=historical_density(row,dict(epochs=[],observations=[ref],historical_records=[]),p['task']['end'])
            self.assertEqual(density['seconds'],600)
            self.assertEqual(n.chunk_seconds(row,density),4030*600)


# unittest would otherwise run inherited tests twice under the helper subclass.
for _name in list(NativeProgramTests.__dict__):
    if _name.startswith('test_') and _name not in PaginationRecoveryTests.__dict__:
        setattr(PaginationRecoveryTests,_name,None)


if __name__=='__main__':unittest.main()
