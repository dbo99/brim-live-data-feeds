"""Offline successful-prefix recovery through the existing Journal and adapter."""
import copy
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import test_campaign_integration as c
import test_recovery as old_tests
from dendra.history_acquisition import recovery as r, authority_witness as w, daily_handoff as h
from dendra.history_acquisition.safety import Hold, encode, digest
from dendra.transport import parse_utc, format_utc


def setUpModule():
    c.setUpModule()


class ContinuationTests(unittest.TestCase):
    prepare = old_tests.RecoveryTests.prepare
    open = old_tests.RecoveryTests.open
    close = old_tests.RecoveryTests.close
    run_pages = old_tests.RecoveryTests.run_pages
    export = old_tests.RecoveryTests.export
    tearDown = old_tests.RecoveryTests.tearDown

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='prefix-recovery-', dir=c.TEST_ROOT))
        self.clock = c.Clock(); self.journals = []
        self.old = self.root/'predecessor'; self.old.mkdir()
        self.bundle = c.bundle(c.VWC)
        manifest = c.campaign.make_campaign(c.INVENTORY, campaign_id='synthetic-prefix-predecessor',
            executor_fingerprint=c.FINGERPRINT, horizons={c.VWC:dict(start=c.START,end=c.END)},
            chunk_days=2, decisions={c.VWC:self.bundle['decision']},
            budgets=c.campaign.policy(logical_requests=3,attempts=3,total_bytes=25165824,wall_seconds=600))
        binding, tasks = c.campaign_execution.prepare(manifest,c.INVENTORY,{c.VWC:self.bundle},now=c.NOW)
        oldkey = next(iter(tasks))
        self.prefix_rows = [c.row(format_utc(parse_utc(c.START)+timedelta(seconds=i))) for i in range(2016)]
        self.cursor = self.prefix_rows[-1]['t']
        body = c.page(self.prefix_rows)
        body += b' '*(149207-len(body))
        with c.Journal(self.old,binding,tasks,create=True,inventory=c.INVENTORY,
                       now=self.clock.now,monotonic=self.clock.monotonic) as j:
            fake = c.FiniteExecutor(j,self.clock,[body,c.provider_adapter.Deadline('synthetic deadline')])
            with self.assertRaises(Hold):
                c.provider_adapter.CampaignAdapter(j).run(executor=fake,wait=fake.wait)
            self.assertEqual(len(fake.calls),2)
            auth = dict(schema_version='dendra-campaign-dispatch-1',task_root=str(self.old),
                binding_sha256=digest(binding),approval_reference='synthetic only',window_start=c.NOW,
                window_end=format_utc(parse_utc(c.NOW)+timedelta(seconds=600)))
            pin = dict(task_id=oldkey,campaign_id=binding['campaign_id'],header_sha256=j.header_sha,
                anchor_sha256=j.events[-1]['record_sha256'],object_sha256=c.sha(body),
                stream_id=c.VWC,station_id=c.INVENTORY.identity(c.VWC)['station_id'],
                start=c.START,end=c.END,cursor=self.cursor)
        auth_path = self.root/'predecessor-authorization.json'; auth_path.write_bytes(encode(auth))
        self.oldhashes = old_tests.hashes(self.old)
        incident_patch = patch.object(r,'PREFIX_INCIDENT',pin)
        incident_patch.start(); self.addCleanup(incident_patch.stop)
        self.ref = dict(incident=r.PREFIX_VERSION,root=str(self.old),authorization_path=str(auth_path),
                        authorization_sha256=digest(auth))
        self.clock.advance(601)
        witness = self.root/'witness'; witness.mkdir()
        wb, wt = w.prepare(c.INVENTORY,campaign_id='synthetic-prefix-witness',
            packets={c.VWC:self.bundle['packet']},as_of=self.clock.now())
        with c.Journal(witness,wb,wt,create=True,inventory=c.INVENTORY,
                       now=self.clock.now,monotonic=self.clock.monotonic) as j:
            fake = c.FiniteExecutor(j,self.clock,[c.page([c.row(c.START,datastream_id=c.VWC)],1)])
            c.provider_adapter.WitnessAdapter(j).run(executor=fake,wait=fake.wait,
                authorization=dict(binding_sha256=digest(wb),approval_reference='synthetic only',
                    window_start=self.clock.now(),window_end=format_utc(parse_utc(self.clock.now())+timedelta(seconds=60))))
            evidence = w.evidence(j,c.VWC)
        self.wref = dict(root=str(witness),campaign_id=wb['campaign_id'],binding_sha256=digest(wb),
            review=dict(rule=w.REVIEW,disposition='ACCEPT_SOURCE_START',evidence_sha256=evidence['evidence_sha256'],
                        reviewer_ref='synthetic review',reviewed_at=self.clock.now()))
        self.native = self.root/'recovery'; self.native.mkdir()
        self.auth = dict(checkpoint=r.checkpoint(),root=str(self.native),approval_reference='synthetic only',
            window_start=self.clock.now(),window_end=format_utc(parse_utc(self.clock.now())+timedelta(seconds=600)))
        self.b, self.tasks = self.prepare(); self.key = next(iter(self.tasks))

    def short(self, **extra):
        return c.page([c.row(self.cursor, **extra)])

    def full(self, start=None):
        start = parse_utc(start or self.cursor)
        return c.page([c.row(format_utc(start+timedelta(seconds=i))) for i in range(2016)])

    def test_exact_immutable_predecessor_charges_and_distinct_identity(self):
        j = self.open()
        self.assertEqual({k:j.snapshot()['counters'][k] for k in r.PREFIX_CHARGES},r.PREFIX_CHARGES)
        self.assertNotEqual(self.key,r.PREFIX_INCIDENT['task_id'])
        self.assertEqual(self.tasks[self.key]['native_task']['task_id'],r.PREFIX_INCIDENT['task_id'])
        self.assertEqual(self.b['predecessor']['prefix_receipt']['source_rows'],2016)
        self.assertEqual(self.b['request_policy']['http_attempts'],2)
        self.assertEqual(self.b['budgets']['attempts'],4)

    def test_original_window_expired_new_window_exact_600(self):
        self.assertLess(parse_utc(self.b['predecessor']['authorization']['window_end']),parse_utc(self.auth['window_start']))
        self.assertEqual((parse_utc(self.auth['window_end'])-parse_utc(self.auth['window_start'])).total_seconds(),600)

    def test_cursor_and_prefix_once_equal_boundary_collapsed(self):
        j,_,fake = self.run_pages([self.short()])
        self.assertEqual(len(fake.calls),1)
        self.assertEqual(parse_qs(urlsplit(fake.calls[0][0]).query)['time[$gte]'],[self.cursor])
        env = j.completed(self.key)
        self.assertEqual(env['page_count'],2)
        self.assertEqual(len(env['rows']),2016)
        self.assertEqual(j.snapshot()['counters']['source_rows'],2017)
        self.assertEqual(env['pages'][0]['response_sha256'],r.PREFIX_INCIDENT['object_sha256'])
        self.assertEqual(env['retrieval_first_utc'],self.b['predecessor']['prefix_receipt']['at'])
        self.assertEqual(env['diagnostics']['duplicate_rows'],1)

    def test_conflicting_boundary_localizes(self):
        j,_,_ = self.run_pages([c.page([c.row(self.cursor,1)])])
        env = j.completed(self.key)
        self.assertEqual(len(env['rows']),2016)
        self.assertTrue(env['rows'][-1]['duplicate_conflict'])
        self.assertEqual(env['diagnostics']['conflicting_timestamps'],1)
        self.assertEqual(env['quality_disposition']['quarantined_groups'],0)

    def test_quality_quarantined_boundary_and_strict_pagination(self):
        j,_,_ = self.run_pages([self.short(q={'flag':['synthetic']})])
        self.assertEqual(j.completed(self.key)['quality_disposition']['quarantined_groups'],1)
        self.assertEqual(j.snapshot()['counters']['source_rows'],2017)

    def test_empty_continuation_completes_nonempty_prefix(self):
        j,_,f = self.run_pages([c.page()])
        self.assertEqual(len(f.calls),1)
        self.assertEqual(j.snapshot()['intervals'][self.key]['state'],'complete_nonempty')
        self.assertEqual(len(j.completed(self.key)['rows']),2016)

    def test_two_new_requests_four_cumulative(self):
        last = format_utc(parse_utc(self.cursor)+timedelta(seconds=2015))
        j,_,f = self.run_pages([self.full(),c.page([c.row(last)])])
        self.assertEqual(len(f.calls),2)
        self.assertEqual(j.snapshot()['counters']['attempts'],4)
        self.assertEqual(j.completed(self.key)['page_count'],3)
        self.assertEqual(len(j.completed(self.key)['rows']),4031)
        with self.assertRaises(Hold): j.check_budget({'attempts':1})
        self.assertTrue(all(wait>=0 for wait in f.waits))
        starts = [parse_utc(e['at']) for e in j.events if e['kind']=='started']
        self.assertGreaterEqual((starts[1]-starts[0]).total_seconds(),1)

    def test_full_logical_third_holds_without_fourth(self):
        last = format_utc(parse_utc(self.cursor)+timedelta(seconds=2015))
        j,result,f = self.run_pages([self.full(),self.full(last)])
        self.assertTrue(result[self.key]['held']); self.assertEqual(len(f.calls),2)
        self.assertIsNone(j.completed(self.key))
        self.assertEqual(j.snapshot()['counters']['source_rows'],6048)

    def test_nonadvancing_continuation_holds(self):
        j,result,f = self.run_pages([c.page([c.row(self.cursor)]*2016)])
        self.assertTrue(result[self.key]['held']); self.assertEqual(len(f.calls),1)
        self.assertIsNone(j.completed(self.key))

    def test_timeout_spent_no_replay_or_refund(self):
        j = self.open(); f = c.FiniteExecutor(j,self.clock,[TimeoutError()])
        with self.assertRaises(Hold): c.provider_adapter.CampaignAdapter(j).run(executor=f,wait=f.wait)
        self.assertEqual(j.snapshot()['counters']['attempts'],3); self.close(j)
        j = self.open(create=False)
        f = c.FiniteExecutor(j,self.clock,[])
        with self.assertRaises(Hold): c.provider_adapter.CampaignAdapter(j).run(executor=f,wait=f.wait)
        self.assertFalse(f.calls); self.assertEqual(j.snapshot()['counters']['attempts'],3)

    def test_new_byte_ceiling_exact_and_old_bytes_charged(self):
        j = self.open(); j.check_budget({'response_bytes':16777216})
        with self.assertRaises(Hold): j.check_budget({'response_bytes':16777217})
        self.assertEqual(j.snapshot()['counters']['response_bytes'],149207)
        self.assertEqual(self.b['request_policy']['total_bytes'],16777216)

    def test_window_cannot_extend(self):
        with self.assertRaises(Hold):
            self.prepare(authorization=dict(self.auth,window_end=format_utc(parse_utc(self.auth['window_end'])+timedelta(microseconds=1))))

    def test_expired_window_no_dispatch(self):
        j = self.open(); self.clock.advance(601); f = c.FiniteExecutor(j,self.clock,[])
        with self.assertRaises(Hold): c.provider_adapter.CampaignAdapter(j).run(executor=f,wait=f.wait)
        self.assertFalse(f.calls)

    def test_no_retry_redirect_or_limit_widening(self):
        p = self.b['request_policy']
        self.assertEqual(p['retries'],0); self.assertEqual(p['redirects'],0)
        self.assertEqual(p['concurrency'],1)
        for name in ('attempts','response_bytes','source_rows','elapsed_ms'):
            b = copy.deepcopy(self.b); b['budgets'][name]+=1
            with self.subTest(name=name),self.assertRaises(Hold): r.validate_binding(b,self.tasks,inventory=c.INVENTORY)

    def test_incident_identity_pins_refuse_substitution(self):
        for name in ('task_id','stream_id','station_id','start','end','cursor','header_sha256','anchor_sha256','object_sha256'):
            pin = dict(r.PREFIX_INCIDENT); pin[name] = c.PERCENT if name.endswith('_id') else '0'*64
            with self.subTest(name=name),patch.object(r,'PREFIX_INCIDENT',pin),self.assertRaises((Hold,KeyError,ValueError)):
                self.prepare()

    def test_prefix_receipt_tamper_refused(self):
        b = copy.deepcopy(self.b); b['predecessor']['prefix_receipt']['source_rows']=1
        with self.assertRaises(Hold): self.open(binding=b)

    def test_wrong_cursor_refused_before_reservation(self):
        j = self.open(); a = c.provider_adapter.CampaignAdapter(j)
        spec = c.provider_adapter.CampaignRequestSpec(c.VWC,c.START,c.END,c.START)
        with self.assertRaises(Hold): a._validate_dispatch(a._request(spec),spec,self.key)
        self.assertFalse(j.snapshot()['attempts'])

    def test_source_and_current_authority_required(self):
        b = copy.deepcopy(self.b); b['collector_sources']['core.R']='0'*64
        with self.assertRaises(Hold): self.open(binding=b)
        bundle = copy.deepcopy(self.bundle); bundle['review']['disposition']='PENDING'
        with self.assertRaises(Hold): self.prepare(bundle=bundle)

    def test_only_held_task_prepared_suffix_refused(self):
        self.assertEqual(len(self.tasks),1)
        j = self.open(); f = c.FiniteExecutor(j,self.clock,[])
        with self.assertRaises(Hold): c.provider_adapter.CampaignAdapter(j).run(executor=f,wait=f.wait,task_keys=['suffix'])
        self.assertFalse(f.calls)

    def test_seal_provenance_and_offline_verification(self):
        j,_,_ = self.run_pages([self.short()]); state = j.snapshot()
        env,seal,attempts = h._seal(j,self.key,state,c.INVENTORY,c.FINGERPRINT)
        self.assertEqual(len(attempts),2)
        self.assertEqual(attempts[0],self.b['predecessor']['prefix_receipt'])
        lineage = seal['recovery_lineage']
        self.assertEqual(lineage['predecessor_charges'],r.PREFIX_CHARGES)
        self.assertEqual(len(lineage['recovery_attempt_keys']),1)
        self.assertEqual(lineage['query_state'],'QUERY_COMPLETE')
        self.assertEqual(lineage['predecessor_state'],'PREDECESSOR_SUCCESSFUL_PREFIX_AND_FAILED_ATTEMPT')
        self.assertEqual(lineage['cumulative_charges']['attempts'],3)
        pin = self.export(j); self.close(j)
        verified = h.verify_sealed(self.native,manifest_sha256=pin,inventory=c.INVENTORY,acquisition_fingerprint=c.FINGERPRINT)
        self.assertEqual(len(verified['records'][0]['science_rows']),2016)
        self.assertEqual(len(verified['records'][0]['receipts']),2)

    def test_seal_tamper_refused(self):
        j,_,_ = self.run_pages([self.short()]); state = j.snapshot()
        state['intervals'][self.key]['complete']['recovery_lineage']['continuation_cursor']=c.START
        with self.assertRaises(Hold): h._seal(j,self.key,state,c.INVENTORY,c.FINGERPRINT)

    def test_completed_seal_reuse_zero_requests(self):
        j,_,_ = self.run_pages([self.short()]); self.close(j)
        j = self.open(create=False); _,result,f = self.run_pages([],j)
        self.assertTrue(result[self.key]['cache_hit']); self.assertFalse(f.calls)

    def test_missing_prefix_archive_refuses_readonly_seal(self):
        j,_,_ = self.run_pages([self.short()])
        descriptor = self.b['predecessor']['prefix_receipt']['objects'][0]
        p = self.native/j.prefix/descriptor['path']
        p.write_bytes(b'changed synthetic copy')
        with self.assertRaises(Hold): h._seal(j,self.key,j.snapshot(),c.INVENTORY,c.FINGERPRINT)

    def test_incomplete_successful_page_no_restart(self):
        j = self.open(); f = c.FiniteExecutor(j,self.clock,[self.full(),c.Crash()])
        with self.assertRaises(c.Crash): c.provider_adapter.CampaignAdapter(j).run(executor=f,wait=f.wait)
        self.assertIsNone(j.completed(self.key)); self.assertEqual(j.snapshot()['counters']['attempts'],4)
        self.close(j); j = self.open(create=False); f = c.FiniteExecutor(j,self.clock,[])
        with self.assertRaises(Hold): c.provider_adapter.CampaignAdapter(j).run(executor=f,wait=f.wait)
        self.assertFalse(f.calls)
