"""Offline latest requests use real preparation, Journal, Adapter and review guards."""
import copy
from datetime import timedelta
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import test_authority_witness as fixture
from test_browser_projection import synthetic_latest
from dendra.history_acquisition import latest_observation as latest, eligibility, authority_witness as witness
from dendra.history_acquisition.browser_projection import validate_latest
from dendra.history_acquisition.d3_plan import validate_request
from dendra.history_acquisition.journal import Journal
from dendra.history_acquisition.model import source_binding
from dendra.history_acquisition.provider_adapter import WitnessAdapter
from dendra.history_acquisition.recovery import checkpoint, open_evidence
from dendra.history_acquisition.safety import Hold, decode, digest, encode
from dendra.transport import parse_utc, format_utc

NOW, FIRST = fixture.NOW, fixture.FIRST
DEEP, CAMP = fixture.OTHER, fixture.SID
LATEST = "2026-09-26T13:59:00.123Z"


def root(name):
    return Path(tempfile.mkdtemp(prefix=name, dir=os.environ["DENDRA_TEST_ROOT"]))


def response(rows=None, **envelope):
    return encode(dict(data=rows if rows is not None else [dict(t=LATEST, v=.25, datastream_id=DEEP)],
                       limit=2, **envelope))


class LatestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.setUpModule()
        cls.inv = fixture.INV
        cls.head, cls.fp = checkpoint(), digest(source_binding())
        cls.authorities = {}
        for sid in (DEEP, CAMP):
            packet = fixture.packet(sid)
            clock = fixture.Clock()
            location = root("latest-first-")
            binding, tasks = witness.prepare(cls.inv, campaign_id="accepted-first", packets={sid:packet}, as_of=NOW)
            with Journal(location, binding, tasks, create=True, inventory=cls.inv,
                         now=clock.now, monotonic=clock.monotonic) as j:
                WitnessAdapter(j).run(executor=lambda request, timeout:fixture.Reply(fixture.body(sid)),
                    wait=clock.advance, authorization=dict(binding_sha256=digest(binding),
                    approval_reference="synthetic-only", window_start=NOW,
                    window_end=format_utc(parse_utc(NOW)+timedelta(seconds=60))))
                e = witness.evidence(j, sid)
            first = dict(root=str(location), campaign_id="accepted-first", review=dict(rule=witness.REVIEW,
                disposition="ACCEPT_SOURCE_START", evidence_sha256=e["evidence_sha256"],
                reviewer_ref="synthetic-review", reviewed_at=NOW))
            review = eligibility.propose(cls.inv, encode(packet), packet_sha256=digest(packet),
                packet_source_fingerprint=cls.fp, executor_fingerprint=cls.fp,
                start=FIRST, end="2026-09-27T14:00:00.000Z")
            review.update(disposition="ACCEPT_NATIVE", reviewer_ref="synthetic-review", reviewed_at=NOW,
                expires_at="2026-09-27T14:00:00.000Z", acknowledgements=list(eligibility.ACKNOWLEDGEMENTS))
            decision = eligibility.decide(cls.inv, encode(packet), review, executor_fingerprint=cls.fp, now=NOW)
            cls.authorities[sid] = dict(packet=packet, review=review, decision=decision), first

    def setUp(self):
        self.clock, self.calls, self.j = fixture.Clock(), [], None
        self.clock.advance(2)
        self.root = root("latest-")
        self.bundle, self.first = copy.deepcopy(self.authorities[DEEP])
        self.authorization = dict(checkpoint=self.head, root=str(self.root), approval_reference="synthetic-only",
            window_start=NOW, window_end=format_utc(parse_utc(NOW)+timedelta(seconds=300)),
            previous_request_started_at=NOW)

    def prepare(self, sid=DEEP):
        return latest.prepare(self.inv, campaign_id="latest-proof", sid=sid, bundle=self.bundle,
            first_ref=self.first, authorization=self.authorization, now=self.clock.now())

    def setup_journal(self, sid=DEEP):
        self.binding, self.tasks = self.prepare(sid)
        self.j = Journal(self.root, self.binding, self.tasks, create=True, inventory=self.inv,
                         now=self.clock.now, monotonic=self.clock.monotonic)
        self.addCleanup(self.j.close)
        self.adapter = latest.LatestAdapter(self.j)

    def run_latest(self, body=None, executor=None, sid=DEEP):
        if self.j is None:
            self.setup_journal(sid)
        def execute(request, timeout):
            self.assertLessEqual(timeout,25)
            self.assertEqual([e["kind"] for e in self.j.events][-2:],["reserved","started"])
            self.j.verify_records()
            self.assertEqual(self.j.snapshot()["counters"]["attempts"],1)
            self.calls.append(request.full_url)
            self.clock.advance(.25)
            return fixture.Reply(response() if body is None else body)
        return self.adapter.run(executor=executor or execute, wait=self.clock.advance, authorization=self.authorization)

    def project(self, **kwargs):
        return latest.project(self.j, evaluated_at=self.clock.now(), **kwargs)

    def row(self, **changes):
        return dict(t=LATEST, v=.25, datastream_id=DEEP, **changes)

    def test_exact_request_and_deterministic_preparation(self):
        a, tasks = self.prepare()
        self.assertEqual((a,tasks),self.prepare())
        self.assertEqual(tasks,{})
        self.assertEqual(parse_qs(urlsplit(a["request"]["url"]).query),
            {"datastream_id":[DEEP],"$sort[time]":["-1"],"$limit":["2"]})
        self.assertEqual(a["budgets"]["attempts"],1)
        self.setup_journal()
        self.assertEqual(self.adapter.plan(),[a["request"]])

    def test_request_substitutions_refused(self):
        spec = latest.RequestSpec(self.inv.identity(DEEP)["station_id"],DEEP)
        for url in (spec.url().replace("%24limit=2","%24limit=1"), spec.url()+"&skip=1",
                    spec.url()+"&time[$gte]=2024-01-01",spec.url().replace("=-1","=1"),
                    spec.url().replace(DEEP,CAMP)):
            with self.subTest(url=url), self.assertRaises(Hold):
                validate_request(urllib.request.Request(url),spec)

    def test_wrong_station_stream(self):
        for station,sid in (("0"*24,DEEP),(self.inv.identity(DEEP)["station_id"],CAMP)):
            with self.subTest(sid=sid), self.assertRaises(Hold): latest.RequestSpec(station,sid).url()

    def test_empty_is_unavailable_without_total(self):
        e = self.run_latest(response([]))
        self.assertEqual(e["status"],"UNAVAILABLE")
        self.assertIsNone(self.project()["record"])
        self.assertIsNone(e["state"]["source_age_seconds"])
        self.assertEqual(e["state"]["last_successful_source_check"], e["retrieved_at"])

    def test_one_row_available_exact_time_and_scale(self):
        e = self.run_latest()
        point = self.project()["record"]
        self.assertEqual(point["source_timestamp"],LATEST)
        self.assertEqual(point["native_value"],.25)
        self.assertEqual(point["normalized_percent"],25)
        self.assertEqual(e["scale"],self.bundle["decision"]["scale"])
        self.assertEqual(point["identity"],self.inv.identity(DEEP))
        self.assertEqual(e["qa"]["identity_inherited_count"],0)

    def test_two_rows_newest_selected(self):
        self.run_latest(response([self.row(),dict(t=FIRST,v=.9,datastream_id=DEEP)]))
        self.assertEqual(self.project()["record"]["normalized_percent"],25)

    def test_equal_tied_newest_accepts_one_with_private_qa(self):
        rows=[self.row(),self.row()]
        body=response(rows)
        e=self.run_latest(body)
        self.assertEqual(e["status"],"AVAILABLE")
        self.assertEqual(self.project()["record"]["native_value"],.25)
        self.assertEqual(e["source_rows"],2)
        self.assertEqual(e["qa"]["newest_group_occurrence_count"],2)
        self.assertEqual(e["qa"]["newest_group_duplicate_count"],1)
        self.assertEqual(e["qa"]["newest_group_row_sha256"],[digest(r) for r in rows])
        self.assertFalse(e["qa"]["newest_group_value_conflict"])
        self.assertFalse(e["qa"]["newest_group_exhaustive"])
        self.assertTrue(e["qa"]["newest_group_at_response_limit"])
        self.assertEqual(self.j.read_object(e["response_object"]),body)
        self.assertNotIn("qa",self.project())
        self.assertEqual(set(self.project()["record"]),set(synthetic_latest()["record"]))
        self.assertEqual(self.j.snapshot()["counters"]["attempts"],1)

    def test_equal_zero_int_float_tie_preserved(self):
        e=self.run_latest(response([dict(t=LATEST,v=0,datastream_id=DEEP),dict(t=LATEST,v=0.0)]))
        self.assertEqual(e["status"],"AVAILABLE")
        self.assertEqual(e["native_value"],0)
        self.assertEqual(e["qa"]["identity_inherited_row_indices"],[1])
        self.assertFalse(e["qa"]["newest_group_value_conflict"])

    def test_tied_quality_veto_on_second_occurrence(self):
        e=self.run_latest(response([self.row(),self.row(q={"flag":["private-veto"]})]))
        self.assertEqual(e["status"],"UNAVAILABLE")
        self.assertEqual(e["reason"],"provider_quality_quarantined")
        self.assertTrue(e["quality"]["quarantined"])
        self.assertEqual(e["state"]["latest_quality_disposition"],e["quality"])
        self.assertEqual(e["quality"]["alternatives"][1]["source_occurrences"],[1])
        self.assertIsNone(self.project()["record"])
        self.assertNotIn(b"private-veto",encode(e))

    def test_tied_quality_veto_on_first_occurrence(self):
        e=self.run_latest(response([self.row(q={"flag":["private-veto"]}),self.row()]))
        self.assertEqual(e["status"],"UNAVAILABLE")
        self.assertTrue(e["quality"]["quarantined"])
        self.assertIsNone(e["native_value"])

    def test_tied_absent_and_null_quality_no_veto(self):
        e=self.run_latest(response([self.row(),self.row(q=None)]))
        self.assertEqual(e["status"],"AVAILABLE")
        self.assertFalse(e["quality"]["quarantined"])
        self.assertEqual(len(e["quality"]["alternatives"]),2)

    def test_conflicting_newest_no_winner_or_older_fallback(self):
        rows=[self.row(),dict(t=LATEST,v=.9,datastream_id=DEEP)]
        body=response(rows)
        e=self.run_latest(body)
        self.assertEqual((e["status"],e["reason"]),("UNAVAILABLE","conflicting_latest_values"))
        self.assertIsNone(e["selected_row_sha256"])
        self.assertIsNone(e["native_value"])
        self.assertIsNone(e["normalized_percent"])
        self.assertIsNone(e["state"]["latest_eligible_source_timestamp"])
        self.assertEqual(e["source_timestamp"],LATEST)
        self.assertTrue(e["qa"]["newest_group_value_conflict"])
        self.assertEqual(e["source_rows"],2)
        self.assertEqual(self.j.read_object(e["response_object"]),body)
        self.assertIsNone(self.project()["record"])
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.j.snapshot()["intervals"],{})

    def test_conflict_disposition_independent_of_provider_tie_order(self):
        e=self.run_latest(response([dict(t=LATEST,v=.9,datastream_id=DEEP),self.row()]))
        self.assertEqual(e["reason"],"conflicting_latest_values")
        self.assertIsNone(e["selected_row_sha256"])
        self.assertIsNone(self.project()["record"])

    def test_tied_null_cannot_promote_other_value(self):
        e=self.run_latest(response([dict(t=LATEST,v=None,datastream_id=DEEP),self.row()]))
        self.assertEqual(e["reason"],"conflicting_latest_values")
        self.assertIsNone(self.project()["record"])

    def test_tied_missing_cannot_promote_other_value(self):
        e=self.run_latest(response([self.row(),dict(t=LATEST,datastream_id=DEEP)]))
        self.assertEqual(e["reason"],"conflicting_latest_values")
        self.assertIsNone(self.project()["record"])

    def test_tied_null_missing_stays_unavailable(self):
        e=self.run_latest(response([dict(t=LATEST,v=None),dict(t=LATEST)]))
        self.assertEqual(e["reason"],"missing_latest_value")
        self.assertIsNone(self.project()["record"])

    def test_tied_wrong_stream_still_hard_hold(self):
        with self.assertRaises(Hold):self.run_latest(response([self.row(),dict(t=LATEST,v=.25,datastream_id=CAMP)]))
        self.assertFalse(next(iter(self.j.snapshot()["attempts"].values()))["body_retained"])

    def test_tied_unsafe_quality_still_hard_hold(self):
        with self.assertRaises(Hold):self.run_latest(response([self.row(),self.row(q={"unsafe":[]})]))
        self.assertFalse(next(iter(self.j.snapshot()["attempts"].values()))["body_retained"])

    def test_ascending_response_hold(self):
        with self.assertRaises(Hold): self.run_latest(response([dict(t=FIRST,v=.1,datastream_id=DEEP),self.row()]))

    def test_third_row_hold(self):
        with self.assertRaises(Hold): self.run_latest(response([self.row()]*3))

    def test_wrong_returned_stream_hold(self):
        with self.assertRaises(Hold): self.run_latest(response([dict(t=LATEST,v=.25,datastream_id=CAMP)]))

    def test_missing_returned_stream_inherits_bound_request(self):
        rows=[dict(t=LATEST,v=.25),dict(t=FIRST,v=.9)]
        body=response(rows)
        e=self.run_latest(body)
        self.assertEqual(e["status"],"AVAILABLE")
        self.assertEqual(e["qa"]["identity_inherited_row_indices"],[0,1])
        self.assertEqual(e["qa"]["identity_inherited_count"],2)
        self.assertEqual(e["qa"]["newest_group_occurrence_count"],1)
        self.assertEqual(e["identity"],self.inv.identity(DEEP))
        self.assertEqual(self.j.read_object(e["response_object"]),body)
        self.assertEqual(latest.evidence(self.j,evaluated_at=self.clock.now()),e)

    def test_present_null_stream_is_not_absence(self):
        with self.assertRaises(Hold):self.run_latest(response([dict(t=LATEST,v=.25,datastream_id=None)]))

    def test_unsupported_stream_types_are_not_absence(self):
        self.setup_journal()
        for sid in (True,42,[],{},""):
            with self.subTest(sid=sid),self.assertRaises(Hold):
                latest.response_shape(response([dict(t=LATEST,v=.25,datastream_id=sid)]),
                                      self.binding,retrieved_at=self.clock.now())

    def test_inheritance_requires_exact_single_stream_descriptor(self):
        b,_=self.prepare()
        for ids in ([],[DEEP,CAMP],[DEEP,DEEP]):
            bad=copy.deepcopy(b);bad["selected_ids"]=ids
            with self.subTest(ids=ids),self.assertRaises(Hold):
                latest.response_shape(response([dict(t=LATEST,v=.25)]),bad,retrieved_at=self.clock.now())
        for url in (latest.BASE+"datapoints?%24limit=2&%24sort%5Btime%5D=-1",
                    b["request"]["url"]+"&datastream_id="+CAMP,b["request"]["url"].replace(DEEP,CAMP)):
            bad=copy.deepcopy(b);bad["request"]["url"]=url
            with self.subTest(url=url),self.assertRaises(Hold):
                latest.response_shape(response([dict(t=LATEST,v=.25)]),bad,retrieved_at=self.clock.now())

    def test_inheritance_cannot_bypass_changed_request(self):
        self.setup_journal();self.j.binding["request"]["url"]+="&datastream_id="+CAMP
        with self.assertRaises(Hold):self.run_latest(response([dict(t=LATEST,v=.25)]))
        self.assertEqual(self.calls,[])

    def test_inheritance_cannot_bypass_changed_source(self):
        self.setup_journal()
        changed=dict(source_binding());changed["history_acquisition/latest_observation.py"]="0"*64
        with patch.object(latest,"source_binding",return_value=changed),self.assertRaises(Hold):
            self.run_latest(response([dict(t=LATEST,v=.25)]))
        self.assertEqual(self.calls,[])

    def test_inherited_row_evidence_still_checks_journal_provenance(self):
        self.run_latest(response([dict(t=LATEST,v=.25)]))
        self.j.events[-1]["data"]["response_sha256"]="0"*64
        with self.assertRaises(Hold):self.project()

    def test_inherited_row_evidence_still_checks_original_object(self):
        e=self.run_latest(response([dict(t=LATEST,v=.25)]))
        path=self.root/self.j.prefix/e["response_object"]["path"];path.write_bytes(b"{}")
        with self.assertRaises(Hold):self.project()

    def test_malformed_time_hold(self):
        with self.assertRaises((Hold,ValueError)): self.run_latest(response([dict(t="yesterday",v=.25,datastream_id=DEEP)]))

    def test_future_timestamp_hold(self):
        with self.assertRaises(Hold): self.run_latest(response([dict(t="2026-09-26T15:00:00Z",v=.25,datastream_id=DEEP)]))

    def test_outside_reviewed_scope_hold(self):
        with self.assertRaises(Hold): self.run_latest(response([dict(t="2020-01-01T00:00:00Z",v=.25,datastream_id=DEEP)]))

    def test_q_absent_eligible(self):
        e=self.run_latest()
        self.assertEqual(e["quality"]["presence"],"NO_PROVIDER_QUALITY_CLAIM")
        self.assertEqual(e["status"],"AVAILABLE")

    def test_q_null_eligible(self):
        e=self.run_latest(response([self.row(q=None)]))
        self.assertEqual(e["quality"]["presence"],"EXPLICIT_NULL")
        self.assertEqual(e["status"],"AVAILABLE")

    def test_quarantine_no_fallback_or_leak(self):
        private=dict(flag=["private-quality"],annotation_ids=["private-annotation"])
        body=response([self.row(q=private),dict(t=FIRST,v=.8,datastream_id=DEEP)])
        e=self.run_latest(body)
        self.assertEqual(e["status"],"UNAVAILABLE")
        self.assertIsNone(e["native_value"])
        self.assertIsNone(e["state"]["latest_eligible_source_timestamp"])
        point=self.project()
        self.assertIsNone(point["record"])
        self.assertNotIn(b"private-",encode(point))
        self.assertEqual(self.j.read_object(e["response_object"]),body)

    def test_scalar_false_quality_is_not_empty(self):
        self.assertEqual(self.run_latest(response([self.row(q=False)]))["status"],"UNAVAILABLE")

    def test_existing_empty_quality_policy_preserved(self):
        self.assertEqual(self.run_latest(response([self.row(q={})]))["status"],"AVAILABLE")

    def test_unsupported_quality_hold_omits_body(self):
        with self.assertRaises(Hold): self.run_latest(response([self.row(q={"unknown-private-key":"secret"})]))
        self.assertFalse(next(iter(self.j.snapshot()["attempts"].values()))["body_retained"])

    def test_missing_value_unavailable_no_fallback(self):
        self.run_latest(response([dict(t=LATEST,datastream_id=DEEP),dict(t=FIRST,v=.8,datastream_id=DEEP)]))
        self.assertIsNone(self.project()["record"])

    def test_null_value_unavailable(self):
        self.run_latest(response([dict(t=LATEST,v=None,datastream_id=DEEP)]))
        self.assertIsNone(self.project()["record"])

    def test_malformed_value_hold(self):
        with self.assertRaises(Hold): self.run_latest(response([dict(t=LATEST,v=True,datastream_id=DEEP)]))

    def test_zero_value_preserved(self):
        self.run_latest(response([dict(t=LATEST,v=0,datastream_id=DEEP)]))
        self.assertEqual(self.project()["record"]["normalized_percent"],0)

    def test_percent_uses_reviewed_factor(self):
        self.bundle,self.first=copy.deepcopy(self.authorities[CAMP])
        self.run_latest(response([dict(t=LATEST,v=25,datastream_id=CAMP)]),sid=CAMP)
        self.assertEqual(self.project()["record"]["normalized_percent"],25)

    def test_stale_source_no_day_cutoff(self):
        self.run_latest(response([dict(t=FIRST,v=.25,datastream_id=DEEP)]))
        point=self.project()["record"]
        self.assertEqual(point["source_timestamp"],FIRST)
        self.assertGreater(point["observation_age_seconds"],86400)
        self.assertEqual(point["observation_age_seconds"],(parse_utc(self.clock.now())-parse_utc(FIRST)).total_seconds())

    def test_receipt_age_exact(self):
        self.run_latest();self.clock.advance(10.125)
        self.assertEqual(self.project()["record"]["retrieval_age_seconds"],10.125)

    def test_receipt_300_boundary_and_historical_read(self):
        self.run_latest();self.clock.advance(300)
        self.assertEqual(self.project(live_proof=True)["status"],"AVAILABLE")
        self.clock.advance(.001)
        with self.assertRaises(Hold): self.project(live_proof=True)
        self.assertEqual(self.project()["status"],"AVAILABLE")

    def test_freshness_not_authority_override(self):
        self.clock.advance(86401)
        self.authorization["window_start"]=self.clock.now()
        self.authorization["window_end"]=format_utc(parse_utc(self.clock.now())+timedelta(seconds=60))
        with self.assertRaises(Hold): self.prepare()

    def test_explicit_reviews_required(self):
        self.bundle["review"]["disposition"]="PENDING"
        with self.assertRaises(Hold): self.prepare()

    def test_first_review_cannot_be_audit_timestamp(self):
        self.first["review"]={"start":FIRST}
        with self.assertRaises(Hold): self.prepare()

    def test_source_binding_mismatch_refused(self):
        self.bundle["decision"]["executor_fingerprint"]="0"*64
        with self.assertRaises(Hold): self.prepare()

    def test_checkpoint_mismatch_refused(self):
        self.authorization["checkpoint"]="0"*40
        with self.assertRaises(Hold): self.prepare()

    def test_configuration_mutation_refused(self):
        self.bundle["decision"]["configuration_windows"][0]["start"]="1900-01-01T00:00:00Z"
        with self.assertRaises(Hold): self.prepare()

    def test_one_attempt_restart_and_reopen(self):
        e=self.run_latest()
        self.j.close()
        with Journal(self.root,self.binding,{},inventory=self.inv,now=self.clock.now,
                     monotonic=self.clock.monotonic) as j:
            with self.assertRaises(Hold): latest.LatestAdapter(j).run(executor=lambda *a,**k:self.fail("repeat"),
                wait=self.clock.advance,authorization=self.authorization)
            self.assertEqual(j.snapshot()["counters"]["attempts"],1)
        with open_evidence(str(self.root),"latest-proof",self.inv) as j:
            self.assertEqual(latest.evidence(j,evaluated_at=self.clock.now()),e)
            with self.assertRaises(Hold): latest.LatestAdapter(j)

    def test_ambiguous_reserved_attempt_stays_spent(self):
        self.setup_journal()
        self.j.reserve("latest-witness",self.binding["request_id"])
        self.j.close()
        with Journal(self.root,self.binding,{},inventory=self.inv,now=self.clock.now,
                     monotonic=self.clock.monotonic) as j:
            with self.assertRaises(Hold): latest.LatestAdapter(j).run(executor=lambda *a,**k:self.fail("repeat"),
                wait=self.clock.advance,authorization=self.authorization)
            self.assertEqual(j.snapshot()["counters"]["attempts"],1)

    def test_authorization_cannot_move_storage(self):
        binding,_=self.prepare()
        with self.assertRaises(Hold): Journal(root("moved-"),binding,{},create=True,inventory=self.inv)

    def test_campaign_rename_cannot_reset_approved_root(self):
        self.run_latest()
        renamed,tasks=latest.prepare(self.inv,campaign_id="another-name",sid=DEEP,bundle=self.bundle,
            first_ref=self.first,authorization=self.authorization,now=self.clock.now())
        with self.assertRaises(Hold):Journal(self.root,renamed,tasks,create=True,inventory=self.inv)
        self.assertEqual(self.j.snapshot()["counters"]["attempts"],1)

    def test_oversized_integer_is_bounded_hold(self):
        with self.assertRaises(Hold):self.run_latest(response([dict(t=LATEST,v=10**400,datastream_id=DEEP)]))
        self.assertFalse(next(iter(self.j.snapshot()["attempts"].values()))["body_retained"])

    def test_failed_response_no_retry(self):
        def fail(request,timeout):
            self.calls.append(request.full_url)
            raise urllib.error.URLError("test-only")
        with self.assertRaises(urllib.error.URLError):self.run_latest(executor=fail)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.j.snapshot()["counters"]["attempts"],1)
        with self.assertRaises(Hold):self.run_latest()

    def test_redirect_and_http_failure_no_retry(self):
        reply=fixture.Reply(b"");reply.status=302
        with self.assertRaises(Hold):self.run_latest(executor=lambda request,timeout:reply)
        self.assertFalse(next(iter(self.j.snapshot()["attempts"].values()))["details"]["retryable"])

    def test_receipt_tamper_refused(self):
        self.run_latest()
        self.j.events[-1]["data"]["response_bytes"]+=1
        with self.assertRaises(Hold):self.project()

    def test_object_tamper_refused(self):
        e=self.run_latest()
        path=self.root/self.j.prefix/e["response_object"]["path"]
        path.write_bytes(b"{}")
        with self.assertRaises(Hold):self.project()

    def test_no_history_or_daily_mutation(self):
        self.run_latest()
        self.assertEqual(self.j.snapshot()["intervals"],{})
        self.assertFalse(any(e["kind"] in ("run","sealed","daily_evidence") for e in self.j.events))
        with self.assertRaises((Hold,KeyError)):self.j.start_run("history-task")
        with self.assertRaises((Hold,KeyError)):self.j.seal("history-task",0,{},[])
        with self.assertRaises((Hold,KeyError)):self.j.daily_evidence("history-task","2026-09-25","0"*64)

    def test_private_projection_fields_match_existing_available(self):
        self.run_latest()
        point=self.project()
        self.assertEqual(set(point),{"schema_version","status","reason","record"})
        self.assertEqual(set(point["record"]),set(synthetic_latest()["record"]))
        self.assertEqual(point["schema_version"],synthetic_latest()["schema_version"])
        self.assertFalse(any(k in encode(point).decode() for k in ("annotation_ids","approval_reference","configuration_windows","quality")))
        self.assertTrue(validate_latest(synthetic_latest()))

    def test_real_unavailable_uses_existing_four_field_marker(self):
        self.run_latest(response([]))
        point=self.project()
        self.assertEqual(set(point),{"schema_version","status","reason","record"})
        self.assertEqual(point["status"],"UNAVAILABLE")
        self.assertIsNone(point["record"])

    def test_receipt_state_and_saved_artifact_are_bound(self):
        e=self.run_latest()
        saved=decode(self.j.fs.read(self.j.prefix+"/latest-evidence.json",262144))
        self.assertEqual(e,saved)
        self.assertEqual(set(e["record_identities"]),{"reserved","started","received"})
        self.assertFalse(e["history_coverage"])
        self.assertFalse(e["publication_allowed"])
        self.assertFalse(e["current_state_claim"])

    def test_spacing_across_prior_witness(self):
        self.clock.seconds=0
        self.run_latest()
        self.assertGreaterEqual((parse_utc(self.j.events[-1]["data"]["details"]["requested_at"])-parse_utc(NOW)).total_seconds(),1)

    def test_total_and_skip_are_checked(self):
        self.setup_journal()
        for body in (response(total=False),response(total=0),response(skip=1),
                     encode(dict(data=[self.row()],limit=1)),response(unknown="private")):
            with self.subTest(body=body),self.assertRaises(Hold):latest.response_shape(body,self.binding,retrieved_at=self.clock.now())

    def test_no_interval_reservation_substitution(self):
        self.setup_journal()
        with self.assertRaises(Hold):self.j.reserve("latest-witness",self.binding["request_id"],interval_key="history-task",run=1)
        self.assertEqual(self.j.snapshot()["counters"]["attempts"],0)

    def test_native_scale_tamper_refused(self):
        self.bundle["decision"]["scale"]["conversion_factor"]=1
        with self.assertRaises(Hold):self.prepare()


class LatestDiagnosticTests(unittest.TestCase):
    # Reuse the accepted authority and real Journal/Adapter fixture, not another
    # fake transport or admission implementation. No inherited test duplication.
    setUpClass = classmethod(LatestTests.setUpClass.__func__)
    setUp = LatestTests.setUp
    setup_journal = LatestTests.setup_journal
    run_latest = LatestTests.run_latest
    row = LatestTests.row

    def prepare(self, sid=DEEP):
        return latest.prepare_diagnostic(self.inv, campaign_id="latest-proof", sid=sid,
            bundle=self.bundle, first_ref=self.first, authorization=self.authorization, now=self.clock.now())

    def diagnostic(self, rows=None, **envelope):
        return self.run_latest(response(rows, **envelope))["diagnostic"]

    def projection(self, body):
        if self.j is None: self.setup_journal()
        return latest.diagnostic_projection(body,self.binding,retrieved_at=self.clock.now())

    def test_valid_distinct_descending_structure_only(self):
        d=self.diagnostic([self.row(),dict(t=FIRST,v=0,datastream_id=DEEP)])
        self.assertEqual(d["diagnostic_classification"],"PASSED_STRUCTURE_ONLY")
        self.assertTrue(d["request_identity_valid"])
        self.assertTrue(d["descending_order_valid"])
        self.assertFalse(d["newest_timestamp_tied"])
        self.assertTrue(all(r["stream_matches_selected"] for r in d["rows"]))
        for k in ("latest_available_allowed","latest_unavailable_allowed","publication_allowed"):
            self.assertIs(d[k],False)

    def test_stream_mismatch(self):
        d=self.diagnostic([dict(t=LATEST,v=.2,datastream_id=CAMP)])
        self.assertEqual(d["diagnostic_classification"],"REJECTED_PREDICATES")
        self.assertFalse(d["rows"][0]["stream_matches_selected"])
        self.assertTrue(d["rows"][0]["timestamp_not_after_retrieval"])

    def test_stream_missing_and_unsupported_types(self):
        for fields in ({}, {"datastream_id":None},{"datastream_id":[]},{"datastream_id":4}):
            with self.subTest(fields=fields):
                d=self.projection(response([dict(t=LATEST,v=.2,**fields)]))
                r=d["rows"][0]
                self.assertEqual(r["stream_field_present"],bool(fields))
                self.assertFalse(r["stream_field_type_supported"])
                self.assertEqual(d["diagnostic_classification"],"REJECTED_SCHEMA")

    def test_pre1900_timestamp_distinguished(self):
        d=self.diagnostic([dict(t="1899-01-01T00:00:00.000Z",v=.2,datastream_id=DEEP)])
        r=d["rows"][0]
        self.assertTrue(r["timestamp_parseable"])
        self.assertFalse(r["timestamp_year_at_least_1900"])
        self.assertTrue(r["timestamp_not_after_retrieval"])
        self.assertEqual(d["diagnostic_classification"],"REJECTED_PREDICATES")

    def test_future_timestamp_distinguished(self):
        d=self.diagnostic([dict(t="2099-01-01T00:00:00.000Z",v=.2,datastream_id=DEEP)])
        self.assertTrue(d["rows"][0]["timestamp_year_at_least_1900"])
        self.assertFalse(d["rows"][0]["timestamp_not_after_retrieval"])

    def test_combined_failures_remain_explicit(self):
        d=self.diagnostic([dict(t="2099-01-01T00:00:00.000Z",v=.2,datastream_id=CAMP),
                           dict(t="1899-01-01T00:00:00.000Z",v=.2,datastream_id=CAMP)])
        self.assertFalse(d["rows"][0]["stream_matches_selected"])
        self.assertFalse(d["rows"][0]["timestamp_not_after_retrieval"])
        self.assertFalse(d["rows"][1]["timestamp_year_at_least_1900"])
        self.assertTrue(d["descending_order_valid"])

    def test_malformed_timestamp_and_type(self):
        for fields in ({},{"t":None},{"t":[]},{"t":True},{"t":"SECRET_TIMESTAMP"},
                       {"t":"2026-02-30T00:00:00Z"},{"t":"2026-01-01T00:00:00.1234567Z"}):
            with self.subTest(fields=fields):
                d=self.projection(response([dict(v=.2,datastream_id=DEEP,**fields)]))
                self.assertFalse(d["rows"][0]["timestamp_parseable"])
                self.assertIsNone(d["rows"][0]["timestamp_year_at_least_1900"])
                self.assertIsNone(d["descending_order_valid"])
                self.assertEqual(d["diagnostic_classification"],"REJECTED_SCHEMA")
                self.assertNotIn(b"SECRET_TIMESTAMP",encode(d))

    def test_tie_and_ascending_order_facts(self):
        for rows,tied in (([self.row(),self.row()],True),
                         ([dict(t=FIRST,v=.2,datastream_id=DEEP),self.row()],False)):
            with self.subTest(tied=tied):
                d=self.projection(response(rows))
                self.assertEqual(d["newest_timestamp_tied"],tied)
                self.assertFalse(d["descending_order_valid"])
                self.assertFalse(d["rows"][1]["relative_order_valid"])

    def test_zero_rows_never_unavailable(self):
        d=self.diagnostic([])
        self.assertEqual(d["row_count"],0)
        self.assertEqual(d["diagnostic_classification"],"PASSED_STRUCTURE_ONLY")
        self.assertFalse(d["latest_unavailable_allowed"])

    def test_unknown_schema_values_quality_fail_closed_without_leaks(self):
        bad=[response([dict(t=LATEST,v=["SECRET_VALUE"],datastream_id=DEEP)]),
             response([dict(t=LATEST,v=.1,datastream_id=DEEP,SECRET_KEY="SECRET_COORDINATE")]),
             response([dict(t=LATEST,v=.1,datastream_id=DEEP,q={"SECRET_Q":"SECRET_ANNOTATION"})]),
             response([None]),response(SECRET_ENVELOPE="SECRET_VALUE"),
             response([self.row()]*3),response(total=False),response(skip=1)]
        for raw in bad:
            with self.subTest(raw=raw):
                d=self.projection(raw)
                self.assertEqual(d["diagnostic_classification"],"REJECTED_SCHEMA")
                self.assertNotIn(b"SECRET",encode(d))

    def test_sanitized_object_no_private_content(self):
        raw=response([dict(t=LATEST,v=.31415926,datastream_id=DEEP,
                           q={"flag":["SECRET_FLAG"],"annotation_ids":["SECRET_ANNOTATION"]})])
        result=self.run_latest(raw)
        a=next(iter(self.j.snapshot()["attempts"].values()))
        self.assertEqual(a["representation"],"sanitized")
        self.assertEqual((a["response_bytes"],a["response_sha256"]),(len(raw),latest.sha(raw)))
        self.assertEqual(len(a["objects"]),1)
        obj=self.j.read_object(a["objects"][0])
        for token in (DEEP,CAMP,LATEST,".31415926","SECRET_FLAG","SECRET_ANNOTATION",'"q":'):
            self.assertNotIn(token,encode(result).decode())
            self.assertNotIn(token,obj.decode())
        self.assertFalse(result["raw_body_retained"])
        self.assertNotEqual(obj,raw)

    def test_deterministic_bound_without_truncating_rows(self):
        raw=response([self.row(),self.row()])
        a=self.projection(raw);b=self.projection(raw)
        self.assertEqual(encode(a),encode(b))
        self.assertLessEqual(len(encode(a)),latest.DIAGNOSTIC_BYTES)
        many=self.projection(response([self.row()]*3))
        self.assertEqual(many["row_count"],3)
        self.assertEqual(many["rows"],[])
        with self.assertRaises(Hold):self.projection(b" "*(8*1024**2+1))

    def test_production_timestamp_rejections_unchanged(self):
        ordinary,_=latest.prepare(self.inv,campaign_id="latest-proof",sid=DEEP,bundle=self.bundle,
            first_ref=self.first,authorization=self.authorization,now=self.clock.now())
        for raw in (response([dict(t="1899-01-01T00:00:00.000Z",v=.2)]),
                    response([dict(t="2099-01-01T00:00:00.000Z",v=.2,datastream_id=DEEP)])):
            with self.subTest(raw=raw),self.assertRaisesRegex(Hold,"Latest source timestamp invalid"):
                latest.response_shape(raw,ordinary,retrieved_at=self.clock.now())

    def test_diagnostic_cannot_enter_production_or_history(self):
        self.diagnostic()
        for action in (lambda:latest.evidence(self.j,evaluated_at=self.clock.now()),
                       lambda:latest.project(self.j,evaluated_at=self.clock.now()),
                       lambda:latest.response_shape(response(),self.binding,retrieved_at=self.clock.now()),
                       lambda:self.j.start_run("history"),lambda:self.j.seal("history",0,{},[]),
                       lambda:self.j.daily_evidence("history","2026-09-25","0"*64)):
            with self.assertRaises((Hold,KeyError)):action()
        self.assertFalse((self.root/self.j.prefix/"latest-evidence.json").exists())
        self.assertFalse(any(e["kind"] in ("run","sealed","daily_evidence") for e in self.j.events))

    def test_ordinary_latest_forbids_sanitized_substitution(self):
        b,_=latest.prepare(self.inv,campaign_id="ordinary",sid=DEEP,bundle=self.bundle,
            first_ref=self.first,authorization=self.authorization,now=self.clock.now())
        with Journal(self.root,b,{},create=True,inventory=self.inv,now=self.clock.now,monotonic=self.clock.monotonic) as j:
            adapter=latest.LatestAdapter(j);key=j.reserve("latest-witness",b["request_id"]);j.started(key)
            details=adapter._details(latest.RequestSpec(self.inv.identity(DEEP)["station_id"],DEEP),self.clock.now(),self.clock.monotonic())
            with self.assertRaisesRegex(Hold,"cannot retry or substitute"):
                j.received(key,response(),source_rows=1,status=200,retain=False,sanitized_body=b"{}",details=details)

    def test_projection_mutation_refused_at_receipt(self):
        self.setup_journal();raw=response();d=self.projection(raw);d["latest_available_allowed"]=True
        key=self.j.reserve("latest-witness",self.binding["request_id"]);self.j.started(key)
        details=self.adapter._details(latest.RequestSpec(self.inv.identity(DEEP)["station_id"],DEEP),self.clock.now(),self.clock.monotonic())
        with self.assertRaisesRegex(Hold,"differs from original"):
            self.j.received(key,raw,source_rows=1,status=200,retain=False,sanitized_body=encode(d),details=details)

    def test_receipt_and_object_integrity(self):
        e=self.run_latest();a=next(iter(self.j.snapshot()["attempts"].values()))
        self.assertEqual(e["records"]["received"],self.j.events[-1]["record_sha256"])
        path=self.root/self.j.prefix/a["objects"][0]["path"];path.write_bytes(b"{}")
        with self.assertRaises(Hold):latest.diagnostic_evidence(self.j,evaluated_at=self.clock.now())

    def test_purpose_changes_request_and_cannot_switch_after_reservation(self):
        self.setup_journal()
        ordinary,_=latest.prepare(self.inv,campaign_id="latest-proof",sid=DEEP,bundle=self.bundle,
            first_ref=self.first,authorization=self.authorization,now=self.clock.now())
        self.assertNotEqual(ordinary["request_id"],self.binding["request_id"])
        self.j.reserve("latest-witness",self.binding["request_id"])
        self.j.binding=ordinary
        with self.assertRaises(Hold):self.j.started(next(iter(self.j.snapshot()["attempts"])))
        with self.assertRaises(Hold):self.adapter._permission()

    def test_ambiguous_restart_rename_and_relocation_cannot_reset(self):
        self.setup_journal();self.j.reserve("latest-witness",self.binding["request_id"]);self.j.close()
        with Journal(self.root,self.binding,{},inventory=self.inv,now=self.clock.now,monotonic=self.clock.monotonic) as j:
            with self.assertRaises(Hold):latest.LatestAdapter(j).run(executor=lambda *a,**k:self.fail("repeat"),wait=self.clock.advance,authorization=self.authorization)
            self.assertEqual(j.snapshot()["counters"]["attempts"],1)
        renamed,_=latest.prepare_diagnostic(self.inv,campaign_id="renamed",sid=DEEP,bundle=self.bundle,
            first_ref=self.first,authorization=self.authorization,now=self.clock.now())
        with self.assertRaises(Hold):Journal(self.root,renamed,{},create=True,inventory=self.inv)
        with self.assertRaises(Hold):Journal(root("relocated-"),self.binding,{},create=True,inventory=self.inv)

    def test_completed_restart_is_read_only_without_second_request(self):
        result=self.run_latest();self.j.close()
        with open_evidence(str(self.root),"latest-proof",self.inv) as j:
            self.assertEqual(latest.diagnostic_evidence(j,evaluated_at=self.clock.now()),result)
            with self.assertRaises(Hold):latest.LatestAdapter(j)
        with Journal(self.root,self.binding,{},inventory=self.inv,now=self.clock.now,monotonic=self.clock.monotonic) as j:
            with self.assertRaises(Hold):latest.LatestAdapter(j).run(executor=lambda *a,**k:self.fail("repeat"),wait=self.clock.advance,authorization=self.authorization)

    def test_source_request_and_unknown_purpose_refused(self):
        b,_=self.prepare()
        for name,value in (("purpose","latest"),("version","unknown"),("request_id","0"*64)):
            changed=copy.deepcopy(b);changed[name]=value
            with self.subTest(name=name),self.assertRaises(Hold):latest.validate_binding(changed,{},inventory=self.inv)
        b["collector_sources"]["core.R"]="0"*64
        with self.assertRaises(Hold):latest.validate_binding(b,{},inventory=self.inv)

    def test_http_transport_failures_remain_spent_and_omitted(self):
        reply=fixture.Reply(b"SECRET_BODY");reply.status=503
        with self.assertRaises(urllib.error.HTTPError):self.run_latest(executor=lambda request,timeout:reply)
        a=next(iter(self.j.snapshot()["attempts"].values()))
        self.assertFalse(a["body_retained"])
        self.assertEqual(a["objects"],[])
        with self.assertRaises(Hold):self.run_latest()

    def test_unknown_row_schema_through_shared_adapter(self):
        d=self.diagnostic([dict(t=LATEST,v=.1,datastream_id=DEEP,SECRET_KEY="SECRET_VALUE")])
        self.assertEqual(d["diagnostic_classification"],"REJECTED_SCHEMA")
        a=next(iter(self.j.snapshot()["attempts"].values()))
        self.assertEqual(a["representation"],"sanitized")
        self.assertIsNone(a["details"]["page_complete"])
        self.assertEqual(a["details"]["privacy"],"not_evaluated")

    def test_original_body_retention_denied_for_diagnostic(self):
        self.setup_journal();key=self.j.reserve("latest-witness",self.binding["request_id"]);self.j.started(key)
        details=self.adapter._details(latest.RequestSpec(self.inv.identity(DEEP)["station_id"],DEEP),self.clock.now(),self.clock.monotonic())
        with self.assertRaisesRegex(Hold,"cannot retain originals"):
            self.j.received(key,response(),source_rows=1,status=200,retain=True,details=details)

    def test_valid_facts_cannot_claim_admission_in_receipt(self):
        self.setup_journal();raw=response();d=self.projection(raw)
        key=self.j.reserve("latest-witness",self.binding["request_id"]);self.j.started(key)
        details=self.adapter._details(latest.RequestSpec(self.inv.identity(DEEP)["station_id"],DEEP),self.clock.now(),self.clock.monotonic())
        details.update(privacy="public",identity="match",effective_limit=2,page_complete=True)
        with self.assertRaisesRegex(Hold,"cannot claim admission"):
            self.j.received(key,raw,source_rows=1,status=200,retain=False,sanitized_body=encode(d),details=details)

    def test_receipt_tamper_is_not_diagnostic_evidence(self):
        self.diagnostic();self.j.events[-1]["data"]["response_bytes"]+=1
        with self.assertRaises(Hold):latest.diagnostic_evidence(self.j,evaluated_at=self.clock.now())

    def test_ordinary_spent_root_cannot_be_reopened_as_diagnostic(self):
        ordinary,_=latest.prepare(self.inv,campaign_id="latest-proof",sid=DEEP,bundle=self.bundle,
            first_ref=self.first,authorization=self.authorization,now=self.clock.now())
        with Journal(self.root,ordinary,{},create=True,inventory=self.inv,now=self.clock.now,monotonic=self.clock.monotonic) as j:
            j.reserve("latest-witness",ordinary["request_id"])
        diagnostic,_=self.prepare()
        with self.assertRaises(Hold):Journal(self.root,diagnostic,{},inventory=self.inv)

    def test_timestamp_boundary_and_source_precision(self):
        for t,expected in (("1900-01-01T00:00:00.000Z",True),
                           (self.clock.now(),True),(format_utc(parse_utc(self.clock.now())+timedelta(microseconds=1)),False)):
            with self.subTest(t=t):
                d=self.projection(response([dict(t=t,v=.1,datastream_id=DEEP)]))
                self.assertEqual(d["rows"][0]["timestamp_not_after_retrieval"],expected)
                self.assertTrue(d["rows"][0]["timestamp_year_at_least_1900"])

    def test_window_not_extendible_at_run(self):
        self.setup_journal()
        auth=dict(self.authorization,window_end="2026-09-27T14:00:00Z")
        with self.assertRaises(Hold):self.adapter.run(executor=lambda *a,**k:self.fail("dispatch"),
            wait=self.clock.advance,authorization=auth)
        self.assertEqual(self.j.snapshot()["counters"]["attempts"],0)
