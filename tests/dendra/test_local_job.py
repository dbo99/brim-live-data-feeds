"""Finite offline responses through real R -> Python -> existing collectors.

All artifacts remain under DENDRA_TEST_ROOT; no original evidence is mutated.
"""
import copy
from datetime import timedelta
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from dendra.history_acquisition import local_job as l, eligibility, authority_witness as aw
from dendra.history_acquisition.safety import encode, decode, digest, sha, Hold
from dendra.history_acquisition.model import Inventory, INVENTORY_SHA256, source_binding
from dendra.transport import parse_utc, format_utc

SID='63531a67a9b61453fa1ca4ed'
SECOND='63531a68a9b6141b4b1ca4ef'
NOW='2026-10-02T00:00:00.000Z'
START=l.SCOPE['start']


class RJobTests(unittest.TestCase):
    def setUp(self):
        if hasattr(self,'review_time'):del self.review_time
        self.dir=Path(tempfile.mkdtemp(prefix='r-job-',dir=os.environ['DENDRA_TEST_ROOT']))
        self.inv=Inventory.load(os.environ['DENDRA_INVENTORY'],INVENTORY_SHA256)
        self.catalog=l.REPO/'data/input/dendra/pilot_catalog.json'
        self.fixture=self.synthetic([SID])
        self.c=dict(version=l.VERSION,inventory=os.environ['DENDRA_INVENTORY'],catalog=str(self.catalog),
            catalog_sha256=sha(self.catalog.read_bytes()),streams=[SID],scope=l.SCOPE,limits=l.LIMITS,
            reserve_bytes=l.BODY,root=str(self.dir/'job'),sources=l.sources(),enabled=False,fixture=None,
            reuse=[],attribution={SID:[]},organization_labels={})
        self.ordinal=0

    def synthetic(self,ids):
        authority=l.provider_metadata.load_authority(self.inv,self.catalog)
        f=dict(now=NOW,vocabulary=dict(_id='dt-unit',terms=list(authority['dictionary_terms'].values())),
            stations={},datastreams={},witnesses={},history={})
        for sid in ids:
            i=self.inv.identity(sid);station=i['station_id'];attrs={}
            if i['depth_cm'] is not None:attrs['depth']=dict(value=i['depth_cm'],unit='Centimeter')
            if i['orientation'] is not None:attrs['orientation']=i['orientation']
            f['stations'][station]=dict(_id=station,public_level=3,is_hidden=False,is_geo_protected=False,
                geo=dict(type='Point',coordinates=[-115,33]))
            f['datastreams'].setdefault(station,dict(data=[],total=0,limit=500,skip=0))
            f['datastreams'][station]['data'].append(dict(_id=sid,station_id=station,public_level=3,
                is_hidden=False,is_geo_protected=False,attributes=attrs,
                terms=dict(dt=dict(Unit=i['native_unit']),ds=dict(Medium='Soil',Variable='VolumetricWaterContent')),
                datapoints_config=[dict(begins_at=START)]))
            f['datastreams'][station]['total']+=1
            f['witnesses'][sid]=dict(data=[dict(t=START,v=0,datastream_id=sid)],limit=1)
        return f

    def write(self,path,value):
        Path(path).write_bytes(encode(value));return path

    def save(self):
        self.write(self.dir/'fixture.json',self.fixture)
        self.c['fixture']=dict(path=str(self.dir/'fixture.json'),sha256=sha((self.dir/'fixture.json').read_bytes()))
        self.write(self.dir/'config.json',self.c)

    def ready(self,days=1,empty=False):
        for n in range(0,days,30):
            t=format_utc(parse_utc(START)+timedelta(days=n))
            self.fixture['history'][t]=dict(data=[] if empty else [dict(t=t,v=0,datastream_id=SID)],limit=2016)
        self.save();self.r('prepare');out=self.r('metadata')
        self.assertEqual(out['accounting']['attempts'],4)
        self.make_review(days)
        self.r('review','--review',self.dir/'review.json')

    def make_review(self,days=1):
        result=l.read(Path(self.c['root'])/'metadata.json')[SID]
        packet=result['metadata']['packet'];fp=digest(source_binding())
        scope=dict(start=START,end=format_utc(parse_utc(START)+timedelta(days=days)))
        at=format_utc(parse_utc(NOW)+timedelta(seconds=10))
        review=eligibility.propose(self.inv,encode(packet),packet_sha256=digest(packet),packet_source_fingerprint=fp,
            executor_fingerprint=fp,**scope)
        review.update(disposition='ACCEPT_NATIVE',reviewer_ref='SYNTHETIC_ONLY',reviewed_at=at,
            expires_at=format_utc(parse_utc(NOW)+timedelta(hours=1)),acknowledgements=eligibility.ACKNOWLEDGEMENTS)
        sr=dict(rule=aw.REVIEW,disposition='ACCEPT_SOURCE_START',reviewer_ref='SYNTHETIC_ONLY',reviewed_at=at,
            evidence_sha256=result['witness']['evidence_sha256'])
        place=dict(disposition='ACCEPT_PLACEMENT',reviewer_ref='SYNTHETIC_ONLY',
            station_metadata_sha256=packet['access_evidence']['station_metadata_sha256'],
            configuration_evidence_sha256=digest(packet['configuration_evidence']),
            depth_cm=self.inv.identity(SID)['depth_cm'],crs='EPSG:4326',timestamp_meaning='UTC t; preserve native timestamps',
            scope=scope,evidence=[dict(path=str(self.dir/'fixture.json'),sha256=self.c['fixture']['sha256'])])
        self.write(self.dir/'review.json',dict(job_id=digest(self.c),streams={SID:dict(scope=scope,native_review=review,
            source_review=sr,placement_review=place)}))
        # Fresh R processes use the same monotonically advancing synthetic time.
        self.review_time=at

    def r(self,mode,*args,code=0):
        self.ordinal+=1
        command=['Rscript','--vanilla',str(l.ENTRY),mode,str(self.dir/'config.json'),*map(str,args)]
        if hasattr(self,'review_time') and '--offline-now' not in args:command+=['--offline-now',self.review_time]
        run=subprocess.run(command,capture_output=True,text=True,timeout=60,
            env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
        (self.dir/f'run-{self.ordinal}.stdout').write_text(run.stdout)
        (self.dir/f'run-{self.ordinal}.stderr').write_text(run.stderr)
        self.assertEqual(run.returncode,code,run.stdout+'\n'+run.stderr)
        return decode(run.stdout.encode()) if run.stdout else None

    def test_real_r_nonempty_zero_and_readback_no_refetch(self):
        self.c['attribution'][SID]=[dict(path=str(self.catalog),sha256=sha(self.catalog.read_bytes()),record_pointer='/streams/1')]
        self.c['organization_labels']={'6092b070492ae15e05876ed8':'CDFW'}
        self.ready();first=self.r('acquire');self.assertEqual(first['accounting']['sealed'],1)
        self.assertEqual(first['accounting']['attempts'],5)
        again=self.r('resume');self.assertEqual(first['accounting'],again['accounting'])
        self.assertEqual(again['accounting']['assets'][0]['series_metadata_reference'],dict(path='catalog.json',stream_id=SID))
        self.assertEqual(l.read(Path(self.c['root'])/'catalog.json')['streams'][SID]['source_organization']['subprovider_label'],'Dendra-CDFW')

    def test_real_r_valid_empty_reused_without_refetch(self):
        self.ready(empty=True);first=self.r('acquire')
        self.assertEqual(first['accounting']['covered_empty'],1)
        self.assertEqual(self.r('resume')['accounting'],first['accounting'])

    def test_original_archive_reuse_empty_and_nonempty_preserves_org_and_times(self):
        for empty in (False,True):
            with self.subTest(empty=empty):
                self.setUp();self.ready(empty=empty);done=self.r('acquire')['accounting']['assets'][0]
                oldroot=Path(self.c['root'])
                original={str(p.relative_to(oldroot)):sha(p.read_bytes()) for p in oldroot.rglob('*') if p.is_file()}
                with l.recovery.open_evidence(done['root'],done['campaign_id'],self.inv) as j:
                    task=j.tasks[done['task_id']];seal=j.snapshot()['intervals'][done['task_id']]['complete']
                    record=next(e for e in j.events if e['kind']=='sealed')
                    entry=dict(root=done['root'],campaign_id=done['campaign_id'],task_id=done['task_id'],
                        source_fingerprint=digest(j.binding['collector_sources']),header_sha256=j.header_sha,
                        seal_sha256=record['record_sha256'],archive_sha256=seal['objects'][0]['sha256'],
                        **{k:task[k] for k in ('identity','start','end')})
                self.c['root']=str(self.dir/'reuse-job');self.c['reuse']=[entry]
                self.c['attribution'][SID]=[dict(path=str(self.catalog),sha256=sha(self.catalog.read_bytes()),record_pointer='/streams/1')]
                self.c['organization_labels']={'6092b070492ae15e05876ed8':'CDFW'}
                del self.review_time
                self.save();self.r('prepare');self.r('metadata');self.make_review()
                planned=self.r('review','--review',self.dir/'review.json')
                self.assertEqual(planned['planned_tasks'],0);self.assertEqual(planned['reused_seals'],1)
                acquired=self.r('acquire');self.assertEqual(acquired['accounting']['attempts'],4)
                self.assertEqual(self.r('resume')['accounting'],acquired['accounting'])
                linked=l.read(Path(self.c['root'])/'asset-map.json')['reused'][0]
                self.assertFalse(linked['historical_attribution_overwritten'])
                self.assertEqual(linked['series_metadata_reference']['stream_id'],SID)
                self.assertTrue(linked['original_retrieval_times'])
                self.assertEqual(original,{str(p.relative_to(oldroot)):sha(p.read_bytes()) for p in oldroot.rglob('*') if p.is_file()})

    def test_external_stop_command_no_dispatch_then_explicit_resume(self):
        self.ready();before=self.r('status')['accounting'];self.r('stop')
        self.assertEqual(self.r('acquire')['outcome'],'STOPPED_AT_TASK_BOUNDARY')
        self.assertEqual(self.r('status')['accounting'],before)
        self.assertEqual(self.r('resume')['accounting']['sealed'],1)

    def test_changed_source_pin_refused(self):
        self.save();self.c['sources']['collector']='0'*64;self.write(self.dir/'config.json',self.c)
        self.assertIn('source',self.r('prepare',code=2)['reason'])
        self.assertFalse(Path(self.c['root']).exists())

    def test_absent_or_foreign_organization_evidence_fails_closed(self):
        path=self.dir/'foreign.json';self.write(path,dict(station_id='foreign',organization_id='id',organization_name='CDFW'))
        self.c['attribution'][SID]=[dict(path=str(path),sha256=sha(path.read_bytes()),record_pointer='')]
        with self.assertRaisesRegex(Hold,'another'):l.attribution(self.c,self.inv.identity(SID))

    def test_metadata_attempt_cap_includes_witnesses(self):
        self.c['limits']=dict(l.LIMITS,metadata_attempts=3);self.save();self.r('prepare')
        self.r('metadata',code=2);self.assertEqual(self.r('status')['accounting']['attempts'],0)

    def test_stop_boundary_fresh_process_resume_deadline_budget_preserved(self):
        self.fixture['stop_after_task']=1;self.ready(days=31)
        first=self.r('acquire');self.assertEqual(first['outcome'],'STOPPED_AT_TASK_BOUNDARY')
        self.assertEqual(first['accounting']['sealed'],1)
        second=self.r('resume');self.assertEqual(second['accounting']['attempts'],6)
        self.assertEqual(second['accounting']['sealed'],2)
        self.assertEqual(first['accounting']['window'],second['accounting']['window'])

    def test_crash_after_reservation_stays_spent_fresh_r_refuses(self):
        self.fixture['crash_history']=True;self.ready()
        self.r('acquire',code=77)
        state=self.r('status')['accounting'];self.assertEqual(state['attempts'],5)
        self.assertTrue(state['spent_unsealed'])
        self.r('resume',code=2)
        self.assertEqual(self.r('status')['accounting'],state)

    def test_provider_error_stays_spent_no_hidden_retry(self):
        self.fixture['history'][START]=dict(status=503,body={})
        self.save();self.r('prepare');self.r('metadata');self.make_review();self.r('review','--review',self.dir/'review.json')
        self.r('acquire',code=2)
        first=self.r('status')['accounting'];self.assertEqual(first['attempts'],5)
        self.r('resume',code=2);self.assertEqual(self.r('status')['accounting'],first)

    def test_attempt_exhaustion_before_reservation(self):
        self.c['limits']=dict(l.LIMITS,attempts=4);self.ready()
        self.assertIn('capacity',self.r('acquire',code=2)['reason'])
        self.assertEqual(self.r('status')['accounting']['attempts'],4)

    def test_bytes_exhaustion_before_dispatch(self):
        self.c['limits']=dict(l.LIMITS,bytes=4*l.BODY-1);self.save();self.r('prepare')
        self.assertIn('capacity',self.r('metadata',code=2)['reason'])
        self.assertEqual(self.r('status')['accounting']['attempts'],0)

    def test_original_deadline_exhausted_without_renewal(self):
        self.ready();before=self.r('status')['accounting']
        self.assertIn('deadline',self.r('acquire','--offline-now','2026-10-02T02:01:00.000Z',code=2)['reason'])
        self.assertEqual(self.r('status')['accounting'],before)

    def test_review_absent_then_invalid_refused(self):
        self.save();self.r('prepare');self.r('acquire',code=2)
        self.write(self.dir/'bad-review.json',{});self.r('review','--review',self.dir/'bad-review.json',code=2)
        self.assertEqual(self.r('status')['accounting']['attempts'],0)

    def test_missing_state_refused(self):
        self.save();self.r('status',code=2)
        self.assertFalse(Path(self.c['root']).exists())

    def test_insufficient_storage(self):
        self.save();c,inv=l.config(self.dir/'config.json');job=l.Job(c,inv,clock=l.Clock(self.fixture),fixture=self.fixture)
        with patch.object(l.shutil,'disk_usage',return_value=type('Disk',(),dict(free=0))()):
            with self.assertRaisesRegex(Hold,'storage'):
                with job.open(create=True):pass
        self.assertFalse((job.root/'job.json').exists())

    def test_concurrent_r_refused(self):
        self.save();self.r('prepare')
        with open(Path(self.c['root'])/'writer.lock','rb') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertIn('writer',self.r('status',code=2)['reason'])

    def test_configuration_and_catalog_tamper_refused(self):
        self.save();self.r('prepare')
        self.c['organization_labels']={'x':'Y'};self.write(self.dir/'config.json',self.c);self.r('status',code=2)
        self.c['organization_labels']={};self.write(self.dir/'config.json',self.c)
        self.write(Path(self.c['root'])/'catalog.json',{});self.r('status',code=2)

    def test_two_organizations_labels_do_not_merge_series_identity(self):
        self.c['streams']=[SID,SECOND];self.c['attribution']={SID:[],SECOND:[]}
        for n,s in enumerate(self.c['streams']):
            record=dict(station_id=self.inv.identity(s)['station_id'],datastream_id=s,
                organization_id=f'synthetic-{n}',organization_name=f'Synthetic Organization {n}')
            path=self.dir/f'organization-{n}.json';self.write(path,record)
            self.c['attribution'][s]=[dict(path=str(path),sha256=sha(path.read_bytes()),record_pointer='')]
            self.c['organization_labels'][f'synthetic-{n}']='CDFW' if n==0 else 'Other'
        self.save();self.r('prepare');self.r('status')
        rows=l.read(Path(self.c['root'])/'catalog.json')['streams']
        self.assertEqual(rows[SID]['source_organization']['subprovider_label'],'Dendra-CDFW')
        self.assertNotEqual(rows[SID]['source_organization']['subprovider_key'],rows[SECOND]['source_organization']['subprovider_key'])
        self.assertNotEqual(rows[SID]['series_key'],rows[SECOND]['series_key'])
        c=copy.deepcopy(self.c);c['organization_labels']={k:'Same label' for k in c['organization_labels']}
        changed=l.Job(c,self.inv,clock=l.Clock(self.fixture)).series_catalog()['streams']
        self.assertEqual([r['series_key'] for r in rows.values()],[r['series_key'] for r in changed.values()])

    def test_missing_and_conflicting_organizations_never_filled(self):
        ident=self.inv.identity(SID);self.assertEqual(l.attribution(self.c,ident)['status'],'MISSING')
        for n in range(2):
            path=self.dir/f'org{n}.json';self.write(path,dict(station_id=ident['station_id'],datastream_id=SID,
                organization_id=f'synthetic-{n}',organization_name='Same name'))
            self.c['attribution'][SID].append(dict(path=str(path),sha256=sha(path.read_bytes()),record_pointer=''))
        out=l.attribution(self.c,ident);self.assertEqual(out['status'],'CONFLICT')
        self.assertIsNone(out['subprovider_key']);self.assertIsNone(out['subprovider_label'])

    def test_original_inventory_name_preserved_without_invented_id(self):
        path=self.dir/'name-only.json';self.write(path,dict(id=self.inv.identity(SID)['station_id'],organization='Exact source name'))
        self.c['attribution'][SID]=[dict(path=str(path),sha256=sha(path.read_bytes()),record_pointer='')]
        out=l.attribution(self.c,self.inv.identity(SID))
        self.assertEqual(out['status'],'MISSING');self.assertEqual(out['subprovider_name'],'Exact source name')
        self.assertIsNone(out['subprovider_key']);self.assertIsNone(out['subprovider_label'])

    def test_help_and_inspect_cannot_dispatch_or_create_state(self):
        self.save();self.r('inspect');self.assertFalse(Path(self.c['root']).exists())
        out=subprocess.run(['Rscript','--vanilla',str(l.ENTRY),'--help'],capture_output=True,text=True,timeout=30)
        self.assertEqual(out.returncode,0);self.assertIn('prepare',out.stdout)


if __name__=='__main__':unittest.main()
