"""Offline multi-Journal integration; only fresh test scratch is writable."""
import copy
from datetime import timedelta
import os
from pathlib import Path
import tempfile
import unittest
import shutil

import test_campaign_integration as c
from dendra.history_acquisition import sealed_history as m, browser_projection as b, recovery
from dendra.history_acquisition.safety import Hold, encode, decode, digest, sha
from dendra.transport import parse_utc, format_utc


def setUpModule():
    c.setUpModule()


def pins(root):
    h=decode((root/'handoff.json').read_bytes())
    names=['handoff.json','daily-output.json','result.json','r-receipt.json']+[s['lineage']['path'] for s in h['streams']]
    return [dict(path=n,bytes=(root/n).stat().st_size,sha256=sha((root/n).read_bytes())) for n in names]


class MultiJournalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(tempfile.mkdtemp(prefix='multi-journal-',dir=c.TEST_ROOT))
        cls.start='2024-02-28T08:00:00.000Z';cls.end='2024-03-03T08:00:00.000Z'
        base=c.bundle(c.VWC,start=cls.start,end=cls.end);cls.entries=[];cls.records=[]
        for n in range(4):
            lo=format_utc(parse_utc(cls.start)+timedelta(days=n));hi=format_utc(parse_utc(lo)+timedelta(days=1))
            bundle=copy.deepcopy(base);bundle['review']['scope']=dict(start=lo,end=hi)
            bundle['decision']=c.eligibility.decide(c.INVENTORY,encode(bundle['packet']),bundle['review'],executor_fingerprint=c.FINGERPRINT,now=c.NOW)
            plan=c.campaign.make_campaign(c.INVENTORY,campaign_id='synthetic-multi-'+str(n),executor_fingerprint=c.FINGERPRINT,
                horizons={c.VWC:dict(start=lo,end=hi)},chunk_days=1,decisions={c.VWC:bundle['decision']},
                budgets=c.campaign.policy(logical_requests=3,attempts=3,total_bytes=25165824,wall_seconds=300))
            binding,tasks=c.campaign_execution.prepare(plan,c.INVENTORY,{c.VWC:bundle},now=c.NOW)
            native=cls.root/('native-'+str(n));native.mkdir();clock=c.Clock()
            rows=[] if n==2 else [c.row(format_utc(parse_utc(lo)+timedelta(hours=i)),0 if n==0 else .25) for i in range(24)]
            if n==1: rows[0]['q']={'flag':['PRIVATE_TEST_FLAG'],'annotation_ids':['PRIVATE_TEST_ID']}
            with c.Journal(native,binding,tasks,create=True,inventory=c.INVENTORY,now=clock.now,monotonic=clock.monotonic) as j:
                fake=c.FiniteExecutor(j,clock,[c.page(rows)])
                c.provider_adapter.CampaignAdapter(j).run(executor=fake,wait=fake.wait)
                key=next(iter(tasks));seal=j.snapshot()['intervals'][key]['complete']
                event=next(e for e in j.events if e['kind']=='sealed')
                cls.entries.append(dict(root=str(native),campaign_id=binding['campaign_id'],task_id=key,
                    source_fingerprint=c.FINGERPRINT,header_sha256=j.header_sha,seal_sha256=event['record_sha256'],
                    archive_sha256=seal['objects'][0]['sha256'],identity=c.INVENTORY.identity(c.VWC),start=lo,end=hi))
                cls.records.append((tasks[key],bundle['decision']))
        cls.manifest=dict(schema_version=m.VERSION,inventory_sha256=c.INVENTORY_SHA256,
            streams=[dict(identity=c.INVENTORY.identity(c.VWC),start=cls.start,end=cls.end)],seals=cls.entries)
        inputs=cls.root/'inputs';inputs.mkdir();cls.path=inputs/'seals.json';cls.path.write_bytes(encode(cls.manifest));cls.pin=sha(cls.path.read_bytes())
        cls.verified=m.verify(cls.path,manifest_sha256=cls.pin,inventory=c.INVENTORY)
        cls.prepared=cls.root/'prepared'
        m.prepare_product(cls.path,manifest_sha256=cls.pin,inventory=c.INVENTORY,output_root=cls.prepared,as_of=c.NOW)
        cls.handoff=decode((cls.prepared/'handoff.json').read_bytes());cls.daily=decode((cls.prepared/'daily-output.json').read_bytes())
        cls.lineage=decode((cls.prepared/cls.handoff['streams'][0]['lineage']['path']).read_bytes())

    def altered(self,mutate,verify=True):
        value=copy.deepcopy(self.manifest);mutate(value)
        p=self.root/('case-'+self._testMethodName+'.json');p.write_bytes(encode(value))
        return (m.verify if verify else m.read_set)(p,manifest_sha256=sha(p.read_bytes()),inventory=c.INVENTORY) if verify else m.read_set(p,sha(p.read_bytes()),c.INVENTORY)

    def test_many_journals_different_scopes_same_science(self):
        self.assertEqual(len(self.verified['records']),4)
        self.assertEqual(len({digest(d['scale']) for _,d in self.records}),4)

    def test_one_seal(self):
        v=self.altered(lambda x:(x['seals'].__delitem__(slice(1,None)),x['streams'][0].update(end=self.entries[0]['end'])))
        self.assertEqual(len(v['records']),1)

    def test_complete_empty_remains_empty(self):
        r=self.lineage['seals'][2];self.assertEqual(r['query_state'],'COVERED_EMPTY');self.assertEqual(r['row_count'],0)
        row=self.daily['rows'][c.VWC][2];self.assertIsNone(row['mean_value']);self.assertFalse(row['presentation_eligible'])

    def test_order_refused(self):
        with self.assertRaises(Hold):self.altered(lambda x:x['seals'].reverse())

    def test_gap_refused(self):
        with self.assertRaises(Hold):self.altered(lambda x:x['seals'].pop(1))

    def test_overlap_refused(self):
        with self.assertRaises(Hold):self.altered(lambda x:x['seals'][1].update(start=self.start))

    def test_horizon_mismatch_refused(self):
        with self.assertRaises(Hold):self.altered(lambda x:x['streams'][0].update(end=self.entries[2]['end']))

    def test_wrong_stream_refused(self):
        with self.assertRaises(Hold):self.altered(lambda x:x['seals'][0].update(identity=c.INVENTORY.identity(c.PERCENT)))

    def test_missing_seal_refused(self):
        with self.assertRaises(Hold):self.altered(lambda x:x['seals'][0].update(task_id='0'*64))

    def test_corrupt_seal_refused(self):
        with self.assertRaises(Hold):self.altered(lambda x:x['seals'][0].update(seal_sha256='0'*64))

    def test_wrong_archive_refused(self):
        with self.assertRaises(Hold):self.altered(lambda x:x['seals'][0].update(archive_sha256='0'*64))

    def test_wrong_source_refused(self):
        with self.assertRaises(Hold):self.altered(lambda x:x['seals'][0].update(source_fingerprint='0'*64))

    def test_input_pin_refused(self):
        with self.assertRaises(Hold):m.verify(self.path,manifest_sha256='0'*64,inventory=c.INVENTORY)

    def test_review_inside_scope(self):
        task,d=self.records[0];self.assertEqual(m.review_interval(task,d)['ordinal'],0)

    def test_review_refusals(self):
        mutations=[lambda d:d['scope'].update(start=self.entries[0]['end']),lambda d:d.update(native_unit='Percent'),
            lambda d:d['scale'].update(conversion_factor=1),lambda d:d['identity'].update(depth_cm=20),
            lambda d:d['identity'].update(orientation='other'),lambda d:d['configuration_windows'][0].update(start=self.end),
            lambda d:d.update(native_acquisition_eligible=False),lambda d:d.update(access_status='stale'),
            lambda d:d['scale']['segments'][0].update(start=self.end)]
        for f in mutations:
            task,d=copy.deepcopy(self.records[0]);f(d)
            with self.subTest(mutation=mutations.index(f)),self.assertRaises(Hold):m.review_interval(task,d)

    def test_compact_deterministic(self):
        again=m.compact(self.verified['records'],seal_set_sha256=self.pin,native_sha256=self.handoff['streams'][0]['csv']['sha256'])
        self.assertEqual(again,self.lineage);self.assertLess(len(encode(again)),m.MAX_BYTES)

    def test_compact_no_quality_receipts_or_values(self):
        text=encode(self.lineage).decode()
        for forbidden in ['PRIVATE_TEST_FLAG','PRIVATE_TEST_ID','"q":','"receipts":','"rows":','"root":','"v":']:
            self.assertNotIn(forbidden,text)

    def test_compact_tampering_refused(self):
        v=copy.deepcopy(self.lineage);v['native_rows']+=1
        with self.assertRaises(Hold):m.validate_compact(v,self.handoff['streams'][0],self.handoff)

    def test_compact_rehashed_omission_refused(self):
        v=copy.deepcopy(self.lineage);v['seals'].pop();v['lineage_sha256']=digest({k:x for k,x in v.items() if k!='lineage_sha256'})
        with self.assertRaises(Hold):m.validate_compact(v,self.handoff['streams'][0],self.handoff)

    def test_compact_unknown_private_content_refused(self):
        v=copy.deepcopy(self.lineage);v['seals'][0]['q']={'flag':['PRIVATE_TEST_FLAG']}
        v['lineage_sha256']=digest({k:x for k,x in v.items() if k!='lineage_sha256'})
        with self.assertRaises(Hold):m.validate_compact(v,self.handoff['streams'][0],self.handoff)

    def test_independent_original_provenance(self):
        self.assertTrue(m.verify_preparation_sources(self.path,manifest_sha256=self.pin,inventory=c.INVENTORY,prepared_root=self.prepared))

    def test_independent_audit_refuses_omitted_stream(self):
        p=self.root/'omitted-stream';shutil.copytree(self.prepared,p)
        v=decode((p/'handoff.json').read_bytes());v['streams']=[];(p/'handoff.json').write_bytes(encode(v))
        with self.assertRaises(Hold):m.verify_preparation_sources(self.path,manifest_sha256=self.pin,inventory=c.INVENTORY,prepared_root=p)

    def test_independent_audit_refuses_corrupt_csv(self):
        p=self.root/'corrupt-csv';shutil.copytree(self.prepared,p)
        (p/'native'/f'{c.VWC}.csv').write_bytes(b'corrupt\n')
        with self.assertRaises(Hold):m.verify_preparation_sources(self.path,manifest_sha256=self.pin,inventory=c.INVENTORY,prepared_root=p)

    def test_leap_withheld_and_neighbors(self):
        rows=self.daily['rows'][c.VWC]
        self.assertEqual(rows[1]['date'],'2024-02-29');self.assertIsNone(rows[1]['mean_value'])
        self.assertFalse(rows[1]['presentation_eligible']);self.assertEqual(rows[1]['water_year'],2024)
        self.assertEqual(rows[0]['mean_percent'],0);self.assertTrue(rows[0]['presentation_eligible'])
        self.assertEqual(rows[3]['mean_percent'],25);self.assertTrue(rows[3]['presentation_eligible'])

    def test_browser_public_contract_and_no_leak(self):
        dest=self.root/'delivery';v=b.export(self.prepared,pins=pins(self.prepared),prepared_fingerprint=c.FINGERPRINT,output_root=dest)
        self.assertEqual(b.SCHEMA,'brim-soil-history-1');self.assertEqual(b.PROFILE,'dendra-history-profile-1');self.assertEqual(b.LIMITS['input_bytes'],8388608)
        for p in dest.rglob('*.json'):
            text=p.read_text();self.assertNotIn('PRIVATE_TEST',text);self.assertNotIn('"q":',text)
        self.assertGreater(v['validation']['files'] if 'validation' in v else v['files'],0)

    def test_no_write_into_original_root(self):
        with self.assertRaises(Hold):m.prepare_product(self.path,manifest_sha256=self.pin,inventory=c.INVENTORY,output_root=Path(self.entries[0]['root'])/'bad',as_of=c.NOW)


class FullHistoryTests(unittest.TestCase):
    """The task supplies its fresh verified preparation; never regenerate it here."""
    @classmethod
    def setUpClass(cls):
        cls.root=Path(os.environ['DENDRA_FULL_HISTORY_PREPARED'])
        cls.handoff=decode((cls.root/'handoff.json').read_bytes());cls.daily=decode((cls.root/'daily-output.json').read_bytes())
        cls.inputs,cls.rows=b.load_prepared(cls.root,pins=pins(cls.root),prepared_fingerprint=cls.handoff['science_binding']['collector_fingerprint'])

    def test_complete_172_closure(self):
        self.assertEqual(sum(len(s['intervals']) for s in self.handoff['streams']),172)
        self.assertEqual({s['identity']['stream_id']:len(s['intervals']) for s in self.handoff['streams']},{c.VWC:123,c.PERCENT:49})

    def test_exact_task37_dates_and_neighbors(self):
        r={x['date']:x for x in self.daily['rows'][c.VWC]}
        days=['2022-04-11','2022-04-12','2022-04-13','2022-04-14']
        self.assertEqual([x['date'] for x in self.daily['quality_disposition'][c.VWC]],days)
        for d in days:
            self.assertFalse(r[d]['presentation_eligible']);self.assertTrue(r[d]['query_complete'])
            for k in ('mean_native','mean_percent','mean_value'):self.assertIsNone(r[d][k])
        for d in ('2022-04-10','2022-04-15'):self.assertTrue(r[d]['presentation_eligible'])

    def test_partial_boundary_and_zero_empty(self):
        first=self.daily['rows'][c.PERCENT][0];self.assertEqual(first['date'],'2022-10-11');self.assertFalse(first['query_complete']);self.assertFalse(first['presentation_eligible'])
        for sid,rows in self.rows.items():
            for row in rows:self.assertTrue(row['query_complete']);self.assertGreater(row['n_valid'],0)

    def test_calendar_and_scaling(self):
        from datetime import date
        for sid,rows in self.daily['rows'].items():
            self.assertEqual(len(rows),3648 if sid==c.VWC else 1447)
            self.assertEqual(len({r['date'] for r in rows}),len(rows))
            for r in rows:
                d=date.fromisoformat(r['date']);wy=d.year+(d.month>=10)
                self.assertEqual(r['water_year'],wy);self.assertEqual(r['dowy'],(d-date(wy-1,10,1)).days+1)
                if r['mean_percent'] is not None:self.assertAlmostEqual(r['mean_percent'],r['mean_native']*(100 if sid==c.VWC else 1))
            self.assertTrue(any(r['date'].endswith('-02-29') for r in rows))

    def test_compact_bounds_recovery_and_sources(self):
        refs=[]
        for s in self.handoff['streams']:
            p=self.root/s['lineage']['path'];self.assertLessEqual(p.stat().st_size,8388608)
            v=decode(p.read_bytes());refs.extend(m.validate_compact(v,s,self.handoff))
            self.assertNotIn('"q":',p.read_text())
        self.assertEqual(len({r['source_fingerprint'] for r in refs}),2)
        self.assertEqual(sum(r['recovery_lineage_sha256'] is not None for r in refs),1)
        self.assertEqual(sum(r['quarantined_groups'] for r in refs),407)

    def test_browser_graph(self):
        dest=Path(tempfile.mkdtemp(prefix='full-browser-parent-',dir=c.TEST_ROOT))/'delivery'
        b.export(self.root,pins=pins(self.root),prepared_fingerprint=self.handoff['science_binding']['collector_fingerprint'],output_root=dest)
        self.assertFalse(any(x['date'] in ('2022-04-11','2022-04-12','2022-04-13','2022-04-14') for x in self.rows[c.VWC]))

    def test_historical_recovery_read_is_not_current_resume(self):
        manifest=decode(Path(os.environ['DENDRA_FULL_SEAL_SET']).read_bytes())
        e=next(e for e in manifest['seals'] if e['campaign_id'].startswith('recovery-'))
        with recovery.open_evidence(e['root'],e['campaign_id'],c.INVENTORY) as j:
            self.assertNotEqual(digest(j.binding['collector_sources']),c.FINGERPRINT)
            recovery.validate_historical_binding(j.binding,j.tasks,inventory=c.INVENTORY)
            with self.assertRaises(Hold):recovery.validate_binding(j.binding,j.tasks,inventory=c.INVENTORY)
            with self.assertRaises(Hold):j.put_object(b'not permitted')
