"""Offline synthetic quality/quarantine tests through the committed execution path."""
import copy
from datetime import timedelta
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_campaign_integration as c
from dendra.history_acquisition import observation_quality as q, daily_handoff as h, browser_projection as b
from dendra.history_acquisition.safety import Hold, encode, decode, sha, digest
from dendra.transport import normalize_rows, parse_utc, format_utc


def setUpModule():
    c.setUpModule()


def normalized(rows):
    return normalize_rows(rows, c.START, c.END, quality_policy=q.binding())[0]


class QualityTests(unittest.TestCase):
    def test_policy_bindings_are_detached_and_type_exact(self):
        original=q.binding();altered=q.binding()
        altered['policy']['q_bytes']=8192;altered['policy']['slots'].append('unknown')
        self.assertEqual(q.binding(),original)
        with self.assertRaises(Hold):q.validate_policy(altered)
        altered=copy.deepcopy(original);altered['policy']['q_bytes']=4096.0
        with self.assertRaises(Hold):q.validate_policy(altered)

    def test_absent_null_distinct_eligible(self):
        a, n = q.classify({}), q.classify({'q':None})
        self.assertEqual(a['presence'], 'NO_PROVIDER_QUALITY_CLAIM')
        self.assertEqual(n['presence'], 'EXPLICIT_NULL')
        self.assertNotEqual(a['sha256'],n['sha256'])
        self.assertFalse(a['quarantined'] or n['quarantined'])

    def test_supported_claims_quarantined(self):
        for value in ({'flag':['flag-sentinel']},{'annotation_ids':['annotation-sentinel']},
                      {'annotationIds':['camel-sentinel']},{'attrib':[0,False,None,'attribute']},
                      {'flag':['f'],'annotation_ids':['a'],'annotationIds':['b']},True,False,0,1,1.0,'flag'):
            with self.subTest(value_type=type(value).__name__):
                self.assertTrue(q.classify({'q':value})['quarantined'])

    def test_empty_claims_eligible_without_coercion(self):
        for value in ({},''):
            self.assertFalse(q.classify({'q':value})['quarantined'])
        self.assertTrue(q.classify({'q':{'flag':[]}})['quarantined'])

    def test_unknown_deep_unsafe_oversized_refuse(self):
        for value in ({'unknown':1},{'attrib':{'nested':1}},{'flag':[['nested']]},
                      {'flag':[1]}, {'flag':['a']*9}, 'x'*257, 'x\n', '\u200b',
                      {'flag':['é'*256]*8}, [], float('inf')):
            with self.subTest(kind=type(value).__name__), self.assertRaises((Hold,ValueError)):
                q.classify({'q':value})

    def test_compact_classification_has_no_values(self):
        claim=q.classify({'q':{'flag':['quality-secret'],'annotation_ids':['annotation-secret']}})
        self.assertNotIn(b'secret',encode(claim))

    def test_aliases_and_types_do_not_coalesce(self):
        values=[{'q':{'annotation_ids':['a']}},{'q':{'annotationIds':['a']}},{'q':True},{'q':1},{'q':1.0},{} ,{'q':None}]
        self.assertEqual(len({q.classify(v)['sha256'] for v in values}),len(values))

    def test_duplicate_alternatives_and_union(self):
        rows=normalized([c.row(c.START,0),c.row(c.START,0,q=None),c.row(c.START,0,q={'flag':['private']})])
        self.assertEqual(rows[0]['v'],0)
        self.assertNotIn('duplicate_conflict',rows[0])
        self.assertTrue(rows[0]['quality']['quarantined'])
        self.assertTrue(rows[0]['source_quality_conflict'])
        self.assertEqual([a['source_occurrences'] for a in rows[0]['quality']['alternatives']],[[0],[1],[2]])
        self.assertNotIn(b'private',encode(q.summary(rows)))

    def test_value_statuses_unchanged(self):
        rows=[c.row(c.START,0),c.row('2024-02-29T01:00:00Z',None),
              {'t':'2024-02-29T02:00:00Z'},c.row('2024-02-29T03:00:00Z','bad')]
        self.assertEqual([r['value_status'] for r in normalized(rows)],['number','null','missing','invalid'])

    def test_fixed_pst_leap_and_dst(self):
        for timestamp,day in [('2024-03-01T07:59:59Z','2024-02-29'),
                              ('2024-03-01T08:00:00Z','2024-03-01'),
                              ('2024-07-01T07:30:00Z','2024-06-30')]:
            rows,_=normalize_rows([c.row(timestamp,q='flag')],'2024-01-01T00:00:00Z','2025-01-01T00:00:00Z',quality_policy=q.binding())
            self.assertEqual(q.days(rows),[day])

    def test_historical_normalization_unchanged(self):
        raw=[c.row(c.START,0,q=None),c.row(c.START,0)]
        old,_=normalize_rows(raw,c.START,c.END)
        self.assertNotIn('quality',old[0]);self.assertNotIn('source_quality_conflict',old[0])

    def test_strict_witness_parser_unchanged(self):
        with self.assertRaises(Hold):c.provider_adapter.observation_shape(c.page([c.row(c.START,q={'flag':['a']})]),c.VWC)


class AcquisitionTests(unittest.TestCase):
    fixture=c.IntegrationTests.fixture
    open=c.IntegrationTests.open
    close=c.IntegrationTests.close
    run_fake=c.IntegrationTests.run_fake
    setUp=c.IntegrationTests.setUp
    tearDown=c.IntegrationTests.tearDown

    def acquire(self, pages):
        _,_,binding,tasks=self.fixture(selected=(c.VWC,))
        j=self.open(binding,tasks);key=next(iter(tasks))
        result,fake=self.run_fake(j,pages)
        return j,key,result,fake

    def test_native_retention_and_replay(self):
        body=c.page([c.row(c.START,0,q={'flag':['private-flag'],'annotation_ids':['private-id']})])
        j,key,_,fake=self.acquire([body]);env=j.completed(key)
        self.assertEqual(env['query_state'],'QUERY_COMPLETE')
        self.assertEqual(env['quality_disposition']['state'],'OBSERVATIONS_QUARANTINED')
        self.assertEqual(j.snapshot()['intervals'][key]['state'],'complete_nonempty')
        receipt=next(iter(j.snapshot()['attempts'].values()))
        self.assertEqual(j.read_object(receipt['objects'][0]),body)
        self.assertEqual(env['rows'][0]['q'],{'flag':['private-flag'],'annotation_ids':['private-id']})
        binding,tasks=j.binding,j.tasks;self.close(j)
        reopened=self.open(binding,tasks,create=False)
        saved,unused=self.run_fake(reopened,[])
        self.assertEqual(reopened.completed(key),env);self.assertEqual(unused.calls,[])

    def test_full_pages_keep_quarantined_boundary_rows(self):
        second='2024-02-29T01:00:00Z'
        j,key,_,fake=self.acquire([c.page([c.row(c.START,q='first'),c.row(second)],2),
                                  c.page([c.row(second,q={'flag':['boundary']})],2)])
        env=j.completed(key)
        self.assertEqual(len(fake.calls),2);self.assertEqual(env['page_count'],2)
        self.assertEqual(env['diagnostics']['raw_row_count'],3)
        self.assertEqual(env['quality_disposition']['quarantined_groups'],2)
        self.assertEqual(len(env['rows'][1]['quality']['alternatives']),2)
        self.assertIn('01%3A00%3A00',fake.calls[1][0])

    def test_all_quarantined_not_empty(self):
        j,key,_,_=self.acquire([c.page([c.row(c.START,q='flag')])])
        self.assertEqual(j.snapshot()['intervals'][key]['state'],'complete_nonempty')
        self.assertTrue(j.completed(key)['rows'])

    def test_empty_not_quarantined(self):
        j,key,_,_=self.acquire([c.page()])
        self.assertEqual(j.snapshot()['intervals'][key]['state'],'complete_empty')
        self.assertEqual(j.completed(key)['quality_disposition']['quarantined_groups'],0)

    def test_full_third_page_holds_without_fourth(self):
        _,_,binding,tasks=self.fixture(selected=(c.VWC,));j=self.open(binding,tasks);key=next(iter(tasks))
        pages=[c.page([c.row('2024-02-29T%02d:00:00Z'%i,q='flag'),c.row('2024-02-29T%02d:00:00Z'%(i+1))],2) for i in range(3)]
        fake=c.FiniteExecutor(j,self.clock,pages)
        result=c.provider_adapter.CampaignAdapter(j).run(executor=fake,wait=fake.wait)
        self.assertTrue(result[key]['held'])
        self.assertEqual(len(fake.calls),3);self.assertIsNone(j.completed(key))
        result,unused=self.run_fake(j,[])
        self.assertTrue(result[key]['held']);self.assertEqual(unused.calls,[])
        self.assertEqual(j.snapshot()['counters']['attempts'],3)

    def test_hard_failures_do_not_seal(self):
        for extra in ({'q':{'unknown':1}},{'t':'bad'},{'t':'2020-01-01T00:00:00Z'}, {'datastream_id':'wrong'}):
            with self.subTest(case=list(extra)):
                self.root=Path(tempfile.mkdtemp(dir=c.TEST_ROOT));_,_,binding,tasks=self.fixture(selected=(c.VWC,))
                j=self.open(binding,tasks);key=next(iter(tasks))
                try:
                    result,_=self.run_fake(j,[c.page([dict(c.row(c.START),**extra)])])
                    self.assertTrue(result[key]['held'])
                except c.campaign_execution.Stop:
                    pass
                self.assertIsNone(j.completed(key));self.assertEqual(j.snapshot()['counters']['attempts'],1)

    def test_policy_tamper_refused(self):
        _,_,binding,tasks=self.fixture(selected=(c.VWC,));binding['quality_policy']['policy']['q_bytes']=8192
        with self.assertRaises(c.campaign_execution.Stop):self.open(binding,tasks)

    def test_unsupported_quality_stops_suffix_and_restart(self):
        _,_,binding,tasks=self.fixture(selected=(c.VWC,),end=c.END)
        keys=sorted(tasks,key=lambda key:tasks[key]['start']);self.assertEqual(len(keys),2)
        j=self.open(binding,tasks)
        fake=c.FiniteExecutor(j,self.clock,[c.page([c.row(c.START,q={'unknown':1})])])
        with self.assertRaises(c.campaign_execution.Stop):
            c.provider_adapter.CampaignAdapter(j).run(executor=fake,wait=fake.wait,task_keys=keys)
        self.assertEqual(len(fake.calls),1)
        self.assertIsNone(j.completed(keys[0]));self.assertIsNone(j.completed(keys[1]))
        self.assertEqual(j.snapshot()['intervals'][keys[1]]['runs'],0)
        self.close(j);j=self.open(binding,tasks,create=False)
        unused=c.FiniteExecutor(j,self.clock,[])
        with self.assertRaises(c.campaign_execution.Stop):
            c.provider_adapter.CampaignAdapter(j).run(executor=unused,wait=unused.wait,task_keys=keys)
        self.assertEqual(unused.calls,[]);self.assertEqual(j.snapshot()['counters']['attempts'],1)

    def test_seal_disposition_tamper_refused(self):
        j,key,_,_=self.acquire([c.page([c.row(c.START,q='flag')])])
        env=j.completed(key);env['quality_disposition']['quarantined_groups']=0
        with self.assertRaises(Hold):j.seal(key,1,env,list(j.snapshot()['attempts']))

    def test_task_ids_still_planner_ids(self):
        manifest,_,binding,tasks=self.fixture(selected=(c.VWC,))
        self.assertEqual(set(tasks),{t['task_id'] for t in c.campaign.plan(manifest,c.INVENTORY,now=c.NOW)['tasks']})
        self.assertTrue(all('quality_policy' not in t['native_task']['identity'] for t in tasks.values()))


class DailyTests(unittest.TestCase):
    @classmethod
    def prepare_case(cls,start,end,flag_at):
        root=Path(tempfile.mkdtemp(prefix='quality-daily-',dir=c.TEST_ROOT))
        bundle=c.bundle(c.VWC,start=start,end=end)
        manifest=c.campaign.make_campaign(c.INVENTORY,campaign_id='synthetic-quality-daily',executor_fingerprint=c.FINGERPRINT,
            horizons={c.VWC:dict(start=start,end=end)},chunk_days=1,decisions={c.VWC:bundle['decision']},
            budgets=c.campaign.policy(logical_requests=9,attempts=9,total_bytes=32*1024**2,wall_seconds=300))
        binding,tasks=c.campaign_execution.prepare(manifest,c.INVENTORY,{c.VWC:bundle},now=c.NOW)
        native=root/'native';native.mkdir();clock=c.Clock()
        with c.Journal(native,binding,tasks,create=True,inventory=c.INVENTORY,now=clock.now,monotonic=clock.monotonic) as j:
            replies=[]
            for key in sorted(tasks,key=lambda key:tasks[key]['start']):
                rows=[c.row(format_utc(parse_utc(tasks[key]['start'])+timedelta(hours=i)),0) for i in range(24)]
                for row in rows:
                    if row['t']==format_utc(flag_at):
                        row['q']={'flag':['PRIVATE_QUALITY_SENTINEL'],'annotation_ids':['PRIVATE_ANNOTATION_SENTINEL']}
                    elif parse_utc(row['t']).hour % 2:
                        row['q']=None
                replies.append(c.page(rows))
            fake=c.FiniteExecutor(j,clock,replies)
            c.provider_adapter.CampaignAdapter(j).run(executor=fake,wait=fake.wait,
                task_keys=sorted(tasks,key=lambda key:tasks[key]['start']))
        (native/'execution-binding.json').write_bytes(encode(dict(binding=binding,tasks=tasks)))
        (native/'source-binding.json').write_bytes(encode(dict(collector_fingerprint=c.FINGERPRINT,sources=binding['collector_sources'])))
        files=[dict(path=str(p.relative_to(native)),bytes=p.stat().st_size,sha256=sha(p.read_bytes())) for p in sorted(native.rglob('*')) if p.is_file()]
        body=encode(dict(files=files));(native/'evidence-manifest.json').write_bytes(body)
        prepared=root/'prepared'
        h.prepare_product(native,manifest_sha256=sha(body),inventory=c.INVENTORY,acquisition_fingerprint=c.FINGERPRINT,
            output_root=prepared,as_of=c.NOW,cadence_mode='initialize')
        return root,prepared

    @classmethod
    def setUpClass(cls):
        cls.root,cls.prepared=cls.prepare_case('2024-02-28T08:00:00.000Z','2024-03-02T08:00:00.000Z','2024-02-29T08:00:00Z')
        cls.daily=decode((cls.prepared/'daily-output.json').read_bytes());cls.handoff=decode((cls.prepared/'handoff.json').read_bytes())
        names=['handoff.json','daily-output.json','result.json','r-receipt.json','lineage/'+c.VWC+'.json']
        cls.pins=[dict(path=n,bytes=(cls.prepared/n).stat().st_size,sha256=sha((cls.prepared/n).read_bytes())) for n in names]
        cls.inputs,cls.rows=b.load_prepared(cls.prepared,pins=cls.pins,prepared_fingerprint=c.FINGERPRINT)

    def test_whole_leap_day_withheld_and_neighbor_zero_survives(self):
        rows=self.daily['rows'][c.VWC]
        self.assertEqual([x['date'] for x in rows],['2024-02-28','2024-02-29','2024-03-01'])
        self.assertEqual([x['presentation_eligible'] for x in rows],[True,False,True])
        self.assertEqual([x['mean_percent'] for x in rows],[0,None,0])
        self.assertEqual(self.daily['quality_disposition'][c.VWC][0]['state'],'DAILY_VALUE_WITHHELD')

    def test_cadence_uses_original_timestamps(self):
        self.assertEqual(self.daily['cadence_contexts'][c.VWC]['seconds'],3600)
        self.assertEqual([r['cadence_seconds'] for r in self.daily['rows'][c.VWC]],[3600]*3)

    def test_no_quality_values_in_prepared_or_browser(self):
        for p in self.prepared.rglob('*'):
            if p.is_file():self.assertNotIn(b'PRIVATE_',p.read_bytes())
        for body in b.render(self.inputs,self.rows).values():self.assertNotIn(b'PRIVATE_',body)

    def test_no_withheld_day_in_browser_history(self):
        self.assertEqual([r['date'] for r in self.rows[c.VWC]],['2024-02-28','2024-03-01'])
        self.assertIn('2024-02-29',self.inputs['streams'][c.VWC]['rejected_dates'])

    def test_terminal_on_affected_day_withheld(self):
        rows=normalized([c.row('2024-02-29T08:00:00Z',0,q='flag'),c.row('2024-02-29T09:00:00Z',0)])
        point=h.historical_terminal(c.INVENTORY.identity(c.VWC),rows,c.bundle(c.VWC)['decision']['scale'],as_of=c.NOW)
        self.assertIsNone(point['native_value']);self.assertIsNone(point['normalized_percent'])

    def test_task_boundary_quarantine_union(self):
        rows=normalized([c.row(c.START,0,q='flag')])+normalized([c.row(c.START,0)])
        self.assertEqual(h.disposition(rows)['quarantined_groups'],1)
        self.assertEqual(h.disposition(rows)['withheld_days'],['2024-02-28'])

    def test_completed_day_crossing_two_task_seals(self):
        _,prepared=self.prepare_case('2024-02-28T20:00:00.000Z','2024-03-01T20:00:00.000Z','2024-02-29T20:00:00Z')
        daily=decode((prepared/'daily-output.json').read_bytes())
        row=daily['rows'][c.VWC][1]
        self.assertEqual(row['date'],'2024-02-29');self.assertTrue(row['query_complete'])
        self.assertEqual(len(row['source_intervals']),2);self.assertEqual(row['n_total'],24)
        self.assertIsNone(row['mean_percent']);self.assertFalse(row['presentation_eligible'])
        self.assertEqual(daily['quality_disposition'][c.VWC][0]['state'],'DAILY_VALUE_WITHHELD')

    def test_partial_day_and_summer_pst_do_not_claim_complete(self):
        _,prepared=self.prepare_case('2024-07-01T20:00:00.000Z','2024-07-03T20:00:00.000Z','2024-07-03T08:00:00Z')
        daily=decode((prepared/'daily-output.json').read_bytes());rows=daily['rows'][c.VWC]
        self.assertEqual([r['date'] for r in rows],['2024-07-01','2024-07-02','2024-07-03'])
        self.assertEqual([r['n_total'] for r in rows],[12,24,12])
        self.assertTrue(rows[1]['presentation_eligible']);self.assertEqual(rows[1]['mean_percent'],0)
        self.assertFalse(rows[2]['query_complete']);self.assertIsNone(rows[2]['mean_percent'])
        self.assertEqual(daily['quality_disposition'][c.VWC][0]['state'],'QUERY_INCOMPLETE')

    def test_forged_withheld_numeric_row_refuses_browser(self):
        path=self.prepared/'daily-output.json';modified=decode(path.read_bytes())
        row=modified['rows'][c.VWC][1];row['mean_percent']=0;row['presentation_eligible']=True
        values={n['path']:(self.prepared/n['path']).read_bytes() for n in self.pins};values['daily-output.json']=encode(modified)
        root=self.root/'forged';root.mkdir()
        for name,body in values.items():
            p=root/name;p.parent.mkdir(exist_ok=True);p.write_bytes(body)
        pins=[dict(path=name,sha256=sha(body),bytes=len(body)) for name,body in values.items()]
        with self.assertRaises(Hold):b.load_prepared(root,pins=pins,prepared_fingerprint=c.FINGERPRINT)
