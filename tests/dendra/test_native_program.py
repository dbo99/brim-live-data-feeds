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


if __name__=='__main__':unittest.main()
