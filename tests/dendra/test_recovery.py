"""Synthetic offline recovery through the existing Journal/CampaignAdapter.

Only the fixed incident selector is substituted for synthetic predecessor cases.
Source, metadata, witness, eligibility, transport and Journal checks stay real.
The named real predecessor is also checked read-only; it is never executed.
"""
import copy
from datetime import timedelta
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_campaign_integration as c
from dendra.history_acquisition import recovery as r, authority_witness as w, daily_handoff as h
from dendra.history_acquisition.safety import Hold, encode, decode, sha, digest
from dendra.transport import parse_utc, format_utc


def setUpModule():
    c.setUpModule()


def hashes(root):
    return {str(p.relative_to(root)):sha(p.read_bytes()) for p in root.rglob('*') if p.is_file()}


class RealPredecessorTests(unittest.TestCase):
    def test_named_original_is_read_only_and_charged(self):
        root=Path(os.environ['DENDRA_TASK37_ROOT'])
        auth='campaign-37-authorization.json'
        ref=dict(root=str(root),authorization_path=auth,authorization_sha256=sha((root/auth).read_bytes()))
        value=r.predecessor(ref,c.INVENTORY)
        self.assertEqual(value['task_id'],r.TASK_ID)
        self.assertEqual(value['charges'],r.CHARGES)
        self.assertEqual(value['historical_attempt_limit'],3)
        self.assertIsNone(value['seal']);self.assertIsNone(value['cursor'])
        self.assertFalse(value['body_retained'])


class RecoveryTests(unittest.TestCase):
    fixture=c.IntegrationTests.fixture

    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='recovery-',dir=c.TEST_ROOT))
        self.clock=c.Clock();self.journals=[]
        self.old=self.root/'predecessor';self.old.mkdir()
        self.bundle=c.bundle(c.VWC)
        manifest=c.campaign.make_campaign(c.INVENTORY,campaign_id='synthetic-recovery-predecessor',
            executor_fingerprint=c.FINGERPRINT,horizons={c.VWC:dict(start=c.START,end=c.END)},
            chunk_days=2,decisions={c.VWC:self.bundle['decision']},
            budgets=c.campaign.policy(logical_requests=3,attempts=3,total_bytes=25165824,wall_seconds=300))
        binding,tasks=c.campaign_execution.prepare(manifest,c.INVENTORY,{c.VWC:self.bundle},now=c.NOW)
        oldkey=next(iter(tasks))
        with c.Journal(self.old,binding,tasks,create=True,inventory=c.INVENTORY,
                       now=self.clock.now,monotonic=self.clock.monotonic) as j:
            body=c.page([c.row(c.START,q={'unknown':1}) for _ in range(2016)])
            body += b' '*(177290-len(body))
            self.assertEqual(len(body),177290)
            fake=c.FiniteExecutor(j,self.clock,[body])
            with self.assertRaises(c.campaign_execution.Stop):
                c.provider_adapter.CampaignAdapter(j).run(executor=fake,wait=fake.wait)
            self.assertEqual(len(fake.calls),1)
            auth=dict(campaign_id=binding['campaign_id'],binding_sha256=digest(binding),
                ordered_task_ids=[oldkey],window_start=c.NOW,
                window_end=format_utc(parse_utc(c.NOW)+timedelta(seconds=300)),max_wall_seconds=300)
            self.header=j.header_sha
            self.anchor=j.events[-1]['record_sha256']
        (self.old/'authorization.json').write_bytes(encode(auth))
        self.oldhashes=hashes(self.old)
        self.patch=patch.multiple(r,TASK_ID=oldkey,CAMPAIGN_ID=binding['campaign_id'],HEADER_SHA=self.header,LAST_ANCHOR_SHA=self.anchor)
        self.patch.start();self.addCleanup(self.patch.stop)
        self.ref=dict(root=str(self.old),authorization_path='authorization.json',authorization_sha256=digest(auth))
        self.clock.advance(301)
        witness=self.root/'witness';witness.mkdir()
        wb,wt=w.prepare(c.INVENTORY,campaign_id='synthetic-recovery-witness',packets={c.VWC:self.bundle['packet']},as_of=self.clock.now())
        with c.Journal(witness,wb,wt,create=True,inventory=c.INVENTORY,
                       now=self.clock.now,monotonic=self.clock.monotonic) as j:
            fake=c.FiniteExecutor(j,self.clock,[c.page([c.row(c.START,datastream_id=c.VWC)],1)])
            c.provider_adapter.WitnessAdapter(j).run(executor=fake,wait=fake.wait,
                authorization=dict(binding_sha256=digest(wb),approval_reference='synthetic only',
                    window_start=self.clock.now(),window_end=format_utc(parse_utc(self.clock.now())+timedelta(seconds=60))))
            evidence=w.evidence(j,c.VWC)
        self.wref=dict(root=str(witness),campaign_id=wb['campaign_id'],binding_sha256=digest(wb),
            review=dict(rule=w.REVIEW,disposition='ACCEPT_SOURCE_START',evidence_sha256=evidence['evidence_sha256'],
                reviewer_ref='synthetic review',reviewed_at=self.clock.now()))
        self.native=self.root/'recovery';self.native.mkdir()
        self.auth=dict(checkpoint=r.checkpoint(),root=str(self.native),approval_reference='synthetic only',
            window_start=self.clock.now(),window_end=format_utc(parse_utc(self.clock.now())+timedelta(seconds=600)))
        self.b,self.tasks=self.prepare();self.key=next(iter(self.tasks))

    def tearDown(self):
        for j in reversed(self.journals):j.close()
        self.assertEqual(hashes(self.old),self.oldhashes)

    def prepare(self,**kwargs):
        return r.prepare(c.INVENTORY,**dict(dict(predecessor_ref=self.ref,bundle=self.bundle,
            source_start_ref=self.wref,authorization=self.auth,now=self.clock.now()),**kwargs))

    def open(self,create=True,binding=None,tasks=None,root=None):
        j=c.Journal(root or self.native,binding or self.b,tasks or self.tasks,create=create,inventory=c.INVENTORY,
                    now=self.clock.now,monotonic=self.clock.monotonic)
        self.journals.append(j);return j

    def close(self,j):
        j.close();self.journals.remove(j)

    def run_pages(self,pages,j=None):
        j=j or self.open();fake=c.FiniteExecutor(j,self.clock,pages)
        result=c.provider_adapter.CampaignAdapter(j).run(executor=fake,wait=fake.wait)
        return j,result,fake

    def pages(self,n,full_last=False,quality=False):
        result=[]
        for i in range(n):
            rows=[c.row(format_utc(parse_utc(c.START)+timedelta(hours=i)),q='flag' if quality else None)]
            if i<n-1 or full_last:
                rows.append(c.row(format_utc(parse_utc(c.START)+timedelta(hours=i+1)),q=None))
            result.append(c.page(rows,2))
        return result

    def export(self,j):
        (self.native/'execution-binding.json').write_bytes(encode(dict(binding=j.binding,tasks=j.tasks)))
        (self.native/'source-binding.json').write_bytes(encode(dict(collector_fingerprint=c.FINGERPRINT,sources=j.binding['collector_sources'])))
        entries=[dict(path=str(p.relative_to(self.native)),bytes=p.stat().st_size,sha256=sha(p.read_bytes()))
                 for p in sorted(self.native.rglob('*')) if p.is_file()]
        body=encode(dict(files=entries));(self.native/'evidence-manifest.json').write_bytes(body)
        return sha(body)

    def test_distinct_identity_and_exact_link(self):
        self.assertNotEqual(self.key,r.TASK_ID)
        self.assertEqual(self.tasks[self.key]['predecessor_task_id'],r.TASK_ID)
        self.assertNotEqual(self.b['campaign_id'],r.CAMPAIGN_ID)
        self.assertEqual(self.tasks[self.key]['native_task']['task_id'],r.TASK_ID)
        self.assertEqual(self.b['collector_sources'],c.source_binding())
        self.assertEqual(self.b['quality_policy']['policy']['version'],'dendra-observation-quality-1')
        self.assertEqual(len(self.tasks),1)

    def test_predecessor_pins_required(self):
        with patch.object(r,'HEADER_SHA','0'*64),self.assertRaises(Hold):self.prepare()

    def test_predecessor_terminal_anchor_required(self):
        with patch.object(r,'LAST_ANCHOR_SHA','0'*64),self.assertRaises(Hold):self.prepare()

    def test_predecessor_authorization_hash_mismatch(self):
        with self.assertRaises(Hold):self.prepare(predecessor_ref=dict(self.ref,authorization_sha256='0'*64))

    def test_binding_predecessor_hash_tamper(self):
        b=copy.deepcopy(self.b);b['predecessor']['receipt_sha256']='0'*64
        with self.assertRaises(Hold):self.open(binding=b)

    def test_initial_cumulative_charges(self):
        j=self.open();counts=j.snapshot()['counters']
        for k,v in r.CHARGES.items():self.assertEqual(counts[k],v)
        self.assertFalse(j.snapshot()['attempts'])
        self.assertIsNone(j.completed(self.key))

    def test_all_cumulative_limits_cannot_be_raised(self):
        for field in ('attempts','source_rows','response_bytes','elapsed_ms'):
            b=copy.deepcopy(self.b);b['budgets'][field]+=1
            with self.subTest(field=field),self.assertRaises(Hold):r.validate_binding(b,self.tasks,inventory=c.INVENTORY)

    def test_predecessor_expired_window_preserved(self):
        self.assertLess(parse_utc(self.b['predecessor']['authorization']['window_end']),parse_utc(self.auth['window_start']))
        self.assertEqual(self.b['authorization'],self.auth)

    def test_expired_recovery_window(self):
        j=self.open();self.clock.advance(601)
        with self.assertRaises(Hold):j.check_budget()
        self.close(j);j=self.open(create=False)
        with self.assertRaises(Hold):j.check_budget()

    def test_restart_cannot_replace_deadline(self):
        j=self.open();self.close(j)
        b=copy.deepcopy(self.b);b['authorization']['window_end']=format_utc(parse_utc(self.auth['window_end'])-timedelta(seconds=1))
        with self.assertRaises(Hold):self.open(create=False,binding=b)

    def test_different_storage_cannot_replay_binding(self):
        other=self.root/'other';other.mkdir()
        with self.assertRaises(Hold):self.open(root=other)
        self.assertFalse((other/'registry').exists())

    def test_checkpoint_mismatch(self):
        with self.assertRaises(Hold):self.prepare(authorization=dict(self.auth,checkpoint='0'*40))

    def test_window_cannot_exceed_600_seconds(self):
        auth=dict(self.auth,window_end=format_utc(parse_utc(self.auth['window_start'])+timedelta(seconds=601)))
        with self.assertRaises(Hold):self.prepare(authorization=auth)

    def test_source_mismatch(self):
        b=copy.deepcopy(self.b);b['collector_sources']['core.R']='0'*64
        with self.assertRaises(Hold):self.open(binding=b)

    def test_quality_policy_mismatch(self):
        b=copy.deepcopy(self.b);b['quality_policy']['sha256']='0'*64
        with self.assertRaises(Hold):self.open(binding=b)

    def test_pending_native_review_refused(self):
        bundle=copy.deepcopy(self.bundle);bundle['review']['disposition']='PENDING'
        with self.assertRaises(Hold):self.prepare(bundle=bundle)

    def test_pending_source_start_refused(self):
        ref=copy.deepcopy(self.wref);ref['review']['disposition']='PENDING'
        with self.assertRaises(Hold):self.prepare(source_start_ref=ref)

    def test_freshness_rechecked_per_dispatch(self):
        j=self.open();self.clock.advance(86401)
        fake=c.FiniteExecutor(j,self.clock,[])
        with self.assertRaises(Hold):c.provider_adapter.CampaignAdapter(j).run(executor=fake,wait=fake.wait)
        self.assertFalse(fake.calls)

    def test_authority_expiry_between_pages_stops_dispatch(self):
        bundle=copy.deepcopy(self.bundle)
        bundle['review']['expires_at']=format_utc(parse_utc(self.clock.now())+timedelta(seconds=.5))
        bundle['decision']=c.eligibility.decide(c.INVENTORY,encode(bundle['packet']),bundle['review'],
            executor_fingerprint=c.FINGERPRINT,now=self.clock.now())
        self.b,self.tasks=self.prepare(bundle=bundle);self.key=next(iter(self.tasks))
        j,result,f=self.run_pages(self.pages(2))
        self.assertEqual(len(f.calls),1);self.assertTrue(result[self.key]['held'])
        self.assertEqual(j.snapshot()['counters']['attempts'],2)

    def test_new_page_one_cursor_required(self):
        j=self.open();a=c.provider_adapter.CampaignAdapter(j)
        task=self.tasks[self.key]
        spec=c.provider_adapter.CampaignRequestSpec(c.VWC,task['start'],task['end'],c.MIDDLE)
        with self.assertRaises(Hold):a._validate_dispatch(a._request(spec),spec,self.key)
        self.assertEqual(j.snapshot()['counters']['attempts'],1)

    def test_short_page_one_seals(self):
        j,_,f=self.run_pages(self.pages(1));self.assertEqual(len(f.calls),1)
        self.assertEqual(j.completed(self.key)['page_count'],1)
        self.assertEqual(j.snapshot()['counters']['attempts'],2)

    def test_short_page_two_seals(self):
        j,_,f=self.run_pages(self.pages(2));self.assertEqual(len(f.calls),2)
        self.assertEqual(j.completed(self.key)['page_count'],2)

    def test_short_page_three_seals_four_cumulative_attempts(self):
        j,_,f=self.run_pages(self.pages(3));self.assertEqual(len(f.calls),3)
        self.assertEqual(j.completed(self.key)['page_count'],3)
        self.assertEqual(j.snapshot()['counters']['attempts'],4)
        with self.assertRaises(Hold):j.check_budget({'attempts':1})

    def test_full_third_page_holds_no_fourth_or_restart_refund(self):
        j,result,f=self.run_pages(self.pages(3,full_last=True))
        self.assertTrue(result[self.key]['held']);self.assertEqual(len(f.calls),3)
        self.assertIsNone(j.completed(self.key));before=j.snapshot()['counters'];self.close(j)
        j=self.open(create=False);self.assertEqual(j.snapshot()['counters'],before)
        _,result,f=self.run_pages([],j)
        self.assertTrue(result[self.key]['held']);self.assertFalse(f.calls)
        with self.assertRaises(Hold):j.reserve(self.key,c.MIDDLE,interval_key=self.key,run=1)

    def test_full_sized_pages_include_historical_rows_at_ceiling(self):
        pages=[]
        for i in range(3):
            pages.append(c.page([c.row(format_utc(parse_utc(c.START)+timedelta(seconds=i*2015+k)),q='flag')
                                 for k in range(2016)]))
        j,result,f=self.run_pages(pages)
        self.assertTrue(result[self.key]['held']);self.assertEqual(len(f.calls),3)
        self.assertEqual(j.snapshot()['counters']['source_rows'],8064)
        self.assertEqual(j.snapshot()['counters']['attempts'],4)
        self.assertIsNone(j.completed(self.key))

    def test_crash_after_full_page_preserves_spent_suffix_attempt(self):
        j=self.open();fake=c.FiniteExecutor(j,self.clock,[self.pages(2)[0],c.Crash()])
        with self.assertRaises(c.Crash):c.provider_adapter.CampaignAdapter(j).run(executor=fake,wait=fake.wait)
        self.assertEqual(j.snapshot()['counters']['attempts'],3)
        self.close(j);j=self.open(create=False)
        with self.assertRaises(Hold):self.run_pages([],j)

    def test_nonadvancing_page_holds(self):
        j,result,f=self.run_pages([c.page([c.row(c.START),c.row(c.START)],2)])
        self.assertTrue(result[self.key]['held']);self.assertEqual(len(f.calls),1);self.assertIsNone(j.completed(self.key))

    def test_ambiguous_attempt_remains_spent_on_reopen(self):
        j=self.open();run=j.start_run(self.key)
        key=j.reserve(self.key,c.START,interval_key=self.key,run=run);j.started(key)
        self.close(j);j=self.open(create=False);self.assertEqual(j.snapshot()['counters']['attempts'],2)
        fake=c.FiniteExecutor(j,self.clock,[])
        with self.assertRaises(Hold):c.provider_adapter.CampaignAdapter(j).run(executor=fake,wait=fake.wait)
        self.assertFalse(fake.calls)

    def test_bytes_and_rows_cumulative_ceiling(self):
        j=self.open()
        j.check_budget({'response_bytes':33554432-177290,'source_rows':8064-2016})
        for extra in ({'response_bytes':33554432-177290+1},{'source_rows':8064-2016+1}):
            with self.subTest(extra=extra),self.assertRaises(Hold):j.check_budget(extra)

    def test_quality_rows_count_for_pagination_and_lineage(self):
        j,_,f=self.run_pages(self.pages(2,quality=True));state=j.snapshot()
        self.assertEqual(len(f.calls),2);self.assertEqual(state['counters']['source_rows'],2016+3)
        seal=state['intervals'][self.key]['complete'];lineage=seal['recovery_lineage']
        self.assertEqual(lineage['predecessor_charges'],r.CHARGES)
        self.assertEqual(len(lineage['recovery_attempt_keys']),2)
        self.assertNotIn(self.b['predecessor']['attempt_key'],lineage['recovery_attempt_keys'])
        self.assertEqual(lineage['query_state'],'QUERY_COMPLETE')
        self.assertEqual(j.completed(self.key)['quality_disposition']['state'],'OBSERVATIONS_QUARANTINED')

    def test_all_quarantined_nonempty_not_covered_empty(self):
        j,_,_=self.run_pages([c.page([c.row(c.START,q={'flag':['private'],'annotation_ids':['private']})])])
        self.assertEqual(j.snapshot()['intervals'][self.key]['state'],'complete_nonempty')

    def test_unsupported_quality_stops_without_seal(self):
        j=self.open();fake=c.FiniteExecutor(j,self.clock,[c.page([c.row(c.START,q={'unsupported':1})])])
        with self.assertRaises(c.campaign_execution.Stop):c.provider_adapter.CampaignAdapter(j).run(executor=fake,wait=fake.wait)
        self.assertEqual(len(fake.calls),1);self.assertIsNone(j.completed(self.key))

    def test_seal_reuse_no_request(self):
        j,_,_=self.run_pages(self.pages(1));env=j.completed(self.key);self.close(j)
        j=self.open(create=False);_,result,f=self.run_pages([],j)
        self.assertEqual(result[self.key]['envelope'],env);self.assertFalse(f.calls)

    def test_handoff_only_new_pages_and_keeps_old_charges(self):
        j,_,_=self.run_pages(self.pages(1,quality=True));pin=self.export(j);self.close(j)
        verified=h.verify_sealed(self.native,manifest_sha256=pin,inventory=c.INVENTORY,acquisition_fingerprint=c.FINGERPRINT)
        record=verified['records'][0]
        self.assertEqual(len(record['receipts']),1);self.assertEqual(len(record['science_rows']),1)
        self.assertEqual(record['seal']['recovery_lineage']['predecessor_charges'],r.CHARGES)
        self.assertTrue(record['science_rows'][0]['quality']['quarantined'])

    def test_recovered_daily_day_withheld_and_lineage_preserved(self):
        rows=[c.row(format_utc(parse_utc(c.START)+timedelta(hours=i)),0) for i in range(48)]
        rows[12]['q']={'flag':['PRIVATE_RECOVERY_FLAG']}
        j,_,_=self.run_pages([c.page(rows)]);pin=self.export(j);self.close(j)
        out=self.root/'daily'
        h.prepare_product(self.native,manifest_sha256=pin,inventory=c.INVENTORY,
            acquisition_fingerprint=c.FINGERPRINT,output_root=out,as_of=self.clock.now(),cadence_mode='initialize')
        daily=decode((out/'daily-output.json').read_bytes())
        day=next(x for x in daily['rows'][c.VWC] if x['date']=='2024-02-29')
        self.assertTrue(day['query_complete']);self.assertIsNone(day['mean_percent'])
        self.assertFalse(day['presentation_eligible'])
        self.assertEqual(daily['quality_disposition'][c.VWC][0]['state'],'DAILY_VALUE_WITHHELD')
        lineage=(out/('lineage/'+c.VWC+'.json')).read_bytes()
        self.assertNotIn(b'PRIVATE_RECOVERY_FLAG',lineage)
        self.assertIn(b'PREDECESSOR_FAILED_ATTEMPT',lineage)
        self.assertEqual(decode(lineage)['records'][0]['seal']['recovery_lineage']['predecessor_charges'],r.CHARGES)

    def test_handoff_rejects_lineage_tamper(self):
        j,_,_=self.run_pages(self.pages(1));state=j.snapshot()
        state['intervals'][self.key]['complete']['recovery_lineage']['predecessor_charges']['attempts']=0
        with self.assertRaises(Hold):h._seal(j,self.key,state,c.INVENTORY,c.FINGERPRINT)

    def test_unrelated_task_dispatch_refused(self):
        j=self.open();fake=c.FiniteExecutor(j,self.clock,[])
        with self.assertRaises(Hold):c.provider_adapter.CampaignAdapter(j).run(executor=fake,wait=fake.wait,task_keys=['task38'])
        self.assertFalse(fake.calls);self.assertEqual(len(j.tasks),1)
