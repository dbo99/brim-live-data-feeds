"""Portable state and mixed retention using real R, with offline clock/transport."""
import json,shutil,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from integration_fixture import Fixture,dc
import dendra_state as ds

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name);self.f=Fixture(self.p/'fixture');self.f.native(backfill=True)
    def tearDown(self):self.tmp.cleanup()
    def test_prepared_is_not_published_and_failures_keep_pointer(self):
        p=self.f.invoke();state=self.f.root/'state';before=(state/'current.json').read_bytes()
        self.f.native();r=self.f.invoke('update',expect=2);self.assertIn('Prepared state is not published',r.stderr)
        self.assertFalse((state/'published.json').exists());self.assertEqual(before,(state/'current.json').read_bytes())
        # Frozen recovery tests use current, never purport to acknowledge a publication.
        for failure in ('build','validation','activation'):
            self.f.invoke('replay-update',failure=failure,expect=2)
            self.assertEqual(before,(state/'current.json').read_bytes())
        manifest=dc.load(self.f.root/'native.json');manifest['complete']=False;manifest['failures']=['fixture source failure'];dc.write(self.f.root/'native.json',manifest)
        self.f.invoke('replay-update',expect=2);self.assertEqual(before,(state/'current.json').read_bytes())
        self.f.native();retry=self.f.invoke('replay-update');self.assertEqual(retry['state_role'],'prepared');self.assertFalse((state/'published.json').exists())
    def test_restore_hash_closure_and_lock_fail_closed(self):
        p=self.f.invoke();state=self.f.root/'state';snapshot=self.p/'snapshot';ds.export_snapshot(state,snapshot)
        ds.restore_snapshot(snapshot,self.p/'cold');cp=dc.load(self.p/'cold/current.json')
        idx=dc.load(ds.candidate_at(self.p/'cold',cp)/dc.FIXED[0]);self.assertEqual(idx['stations'][0]['selected_stream_count'],3);self.assertEqual(idx['stations'][0]['selected_stream_ids'],[s['datastream_id'] for s in idx['streams']])
        self.assertNotIn('candidate_root',cp);self.assertEqual(ds.inventory(ds.candidate_at(self.p/'cold',cp)),ds.inventory(ds.candidate_at(state,p)))
        (self.p/'cold/writer.lock').mkdir();r=self.f.invoke('replay-update',state=self.p/'cold',expect=2);self.assertIn('writer lock',r.stderr)
        (snapshot/'candidate/docs/data/dendra/extra.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'integrity'):ds.restore_snapshot(snapshot,self.p/'tampered')
        self.assertFalse((self.p/'tampered').exists())
    def test_generation_traversal_and_artifact_symlink_rejected_before_write(self):
        p=self.f.invoke();state=self.f.root/'state';candidate=ds.candidate_at(state,p)
        snapshot=self.p/'snapshot';ds.export_snapshot(state,snapshot)
        bad=snapshot/'candidate';index=dc.load(bad/dc.FIXED[0]);source=dc.load(bad/dc.STATE_INDEX)
        index['generation']=source['generation']='../../outside'
        dc.write(bad/dc.STATE_INDEX,source)
        for f in index['files']:
            if f['path']==dc.STATE_INDEX:f.update(sha256=dc.sha(bad/dc.STATE_INDEX),bytes=(bad/dc.STATE_INDEX).stat().st_size)
        dc.write(bad/dc.FIXED[0],index)
        manifest=dc.load(snapshot/'manifest.json');manifest.update(generation=index['generation'],files=ds.inventory(bad));dc.write(snapshot/'manifest.json',manifest)
        with self.assertRaisesRegex(ValueError,'Unsafe generation'):ds.restore_snapshot(snapshot,self.p/'restored')
        self.assertFalse((self.p/'outside').exists());self.assertFalse((self.p/'restored').exists())
        target=self.p/'linked-state';target.mkdir();outside=self.p/'outside-existing';outside.mkdir();(target/'artifacts').symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'escapes|symlink'):ds.stage(target,candidate)
        self.assertEqual(list(outside.iterdir()),[])

    def test_mixed_retention_advances_water_year_without_loss(self):
        self.f.now=datetime(2026,9,30,12,tzinfo=timezone.utc)
        self.f.native(start='2016-10-01',end='2026-09-30')
        p=self.f.invoke('replay');state=self.f.root/'state';gen=state/'generations'/p['generation']
        before={s['datastream_id']:dc.load(gen/'daily'/f"{s['datastream_id']}.json") for s in self.f.catalog['streams']}
        self.f.now=datetime(2026,10,2,12,tzinfo=timezone.utc);self.f.native()
        p=self.f.invoke('replay-update');gen=state/'generations'/p['generation']
        for s in self.f.catalog['streams']:
            after=dc.load(gen/'daily'/f"{s['datastream_id']}.json");old={r['date']:r for r in before[s['datastream_id']]['rows']}
            for r in after['rows']:
                if r['date'] in old:self.assertEqual(r,old[r['date']])
            self.assertEqual(after['cadence_context'],before[s['datastream_id']]['cadence_context'])
            self.assertEqual(after['rows'][0]['date'],'2026-07-04' if s['parameter']=='soil_temperature' else '2017-10-01')
            self.assertEqual(after['rows'][-1]['date'],'2026-10-01')
        assessment=dc.load(sorted((state/'runs').glob('*/loss_assessment.json'))[-1]);self.assertFalse(assessment['hold'])
    def test_old_reconciliation_preserves_latest_and_recent_freshness(self):
        self.f.now=datetime(2026,9,20,12,tzinfo=timezone.utc);self.f.native(backfill=True)
        p=self.f.invoke();state=self.f.root/'state';old=dc.load(ds.candidate_at(state,p)/dc.FIXED[0])
        # Test published pointer semantics separately from Git acknowledgement (covered by local transactions).
        pointer={**p,'state_role':'published','publication_commit':'f'*40};dc.write(state/'published.json',pointer)
        self.f.now+=timedelta(hours=1);end=(self.f.now-timedelta(hours=8)).date();start=(end-timedelta(days=20)).isoformat();stop=(end-timedelta(days=19)).isoformat();self.f.native(start=start,end=stop)
        p=self.f.invoke('reconcile',extra={'start':start,'end':stop,'request-generation':'synthetic-old-interval'})
        new=dc.load(ds.candidate_at(state,p)/dc.FIXED[0])
        for a,b in zip(old['streams'],new['streams']):
            self.assertEqual(a['latest_observation_utc'],b['latest_observation_utc']);self.assertEqual(a['current_interval_retrieved_at_utc'],b['current_interval_retrieved_at_utc']);self.assertEqual(a['expires_at_utc'],b['expires_at_utc']);self.assertGreater(b['last_retrieved_at_utc'],a['last_retrieved_at_utc'])
        self.assertEqual(dc.load(state/'published.json')['generation'],old['generation'])
if __name__=='__main__':unittest.main()
