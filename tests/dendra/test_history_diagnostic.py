"""Synthetic one-shot diagnostics through the existing transport and Journal."""
import copy
from datetime import timedelta
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_campaign_integration as c
from dendra.history_acquisition import history_diagnostic as d
from dendra.history_acquisition import provider_adapter as a
from dendra.history_acquisition.journal import Journal
from dendra.history_acquisition.safety import Hold, decode, digest, encode, sha
from dendra.transport import parse_utc, format_utc


def setUpModule():
    c.setUpModule()


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.clock = c.Clock()
        self.root = Path(tempfile.mkdtemp(prefix="history-diag-test-", dir=os.environ["DENDRA_TEST_ROOT"]))
        _, _, self.old_binding, self.old_tasks = c.IntegrationTests.fixture(None, selected=(c.VWC,), end=c.END)
        self.key = next(iter(self.old_tasks))
        self.source_root = self.root / "original"
        self.source_root.mkdir()
        old = Journal(self.source_root, self.old_binding, self.old_tasks, create=True,
            inventory=c.INVENTORY, now=self.clock.now, monotonic=self.clock.monotonic)
        fake = c.FiniteExecutor(old, self.clock, [c.page([c.row(c.START, secret_field="DO_NOT_RETAIN")])])
        with self.assertRaises(c.campaign_execution.Stop):
            a.CampaignAdapter(old).run(executor=fake, wait=fake.wait, task_keys=[self.key])
        old.close()
        self.original = Journal(self.source_root, self.old_binding, self.old_tasks, inspect_only=True,
            now=self.clock.now, monotonic=self.clock.monotonic)
        self.addCleanup(self.original.close)
        self.original_bytes = self.files(self.source_root)
        self.binding, self.tasks = d.prepare(self.original, self.key, checkpoint="a"*40)
        self.diagroot = self.root / "diagnostic"
        self.diagroot.mkdir()
        self.j = Journal(self.diagroot, self.binding, self.tasks, create=True,
            now=self.clock.now, monotonic=self.clock.monotonic)
        self.addCleanup(self.j.close)
        self.adapter = d.HistoryDiagnosticAdapter(self.j, self.original)

    def files(self, root):
        return {str(p.relative_to(root)):sha(p.read_bytes()) for p in root.rglob("*") if p.is_file()}

    def auth(self):
        return dict(binding_sha256=digest(self.binding), checkpoint="a"*40,
            incident_sha256=digest(self.binding["incident"]), approval_reference="synthetic-only",
            window_start=self.clock.now(), window_end=format_utc(parse_utc(self.clock.now())+timedelta(seconds=60)))

    def run_page(self, body, *, reply=None):
        self.fake = c.FiniteExecutor(self.j, self.clock, [body if reply is None else reply])
        return self.adapter.run(executor=self.fake, wait=self.fake.wait, authorization=self.auth())

    def project(self, value):
        return d.projection(encode(value), self.binding, 200)

    def test_six_atomic_alternatives_and_four_families(self):
        cases = [
            (dict(data=[], limit=2016, secret=1), "envelope.unknown_field"),
            (dict(data=[None], limit=2016), "row.nonobject"),
            (dict(data=[c.row(c.START, secret=1)], limit=2016), "row.unknown_field"),
            (dict(data=[c.row(c.START, datastream_id="wrong-private-id")], limit=2016), "row.stream_mismatch"),
            (dict(data=[c.row(c.START, q={"private":3})], limit=2016), "row.nested"),
            (dict(data=[c.row(c.START, lt="x"*257)], limit=2016), "row.scalar_bound")]
        messages = set()
        for value, code in cases:
            with self.subTest(code=code):
                body = encode(value)
                with self.assertRaises(Hold) as caught:
                    a.observation_shape(body, c.VWC)
                messages.add(str(caught.exception))
                result = self.project(value)
                self.assertEqual(result["guard_code"], code)
                self.assertEqual(result["admission"], "REJECTED")
        self.assertEqual(len(messages), 4)

    def test_late_row_in_full_page(self):
        rows = [c.row(c.START) for _ in range(2016)]
        rows[-1]["q"] = ["hidden-value"]
        result = self.run_page(c.page(rows))
        self.assertEqual((result["first_offending_row"], result["offending_slot"]), (2015, "q"))
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(self.fake.waits, [1])

    def test_valid_shape_unchanged_but_diagnostic_never_seals(self):
        body = c.page([c.row(c.START, 0), c.row(c.START, None)])
        before = a.observation_shape(body, c.VWC)
        result = self.run_page(body)
        self.assertEqual(result["admission"], "PASSED_SHAPE_ONLY")
        self.assertEqual(a.observation_shape(body, c.VWC), before)
        self.assertEqual(self.j.tasks, {})
        self.assertEqual(self.j.snapshot()["intervals"], {})
        for key in self.old_tasks:
            with self.assertRaises(Hold): self.j.start_run(key)
            with self.assertRaises(Hold): self.j.seal(key, 1, {}, [])
        self.assertFalse(any(result[k] for k in ("sealed", "dispatch_ready", "source_start_reviewed", "acquisition_eligible")))

    def test_generic_classification_and_original_unchanged(self):
        result = self.run_page(c.page([c.row(c.START, q=[])]))
        receipt = next(e["data"] for e in self.j.events if e["kind"] == "received")
        self.assertEqual({k:receipt["details"][k] for k in ("error_code", "privacy", "identity", "retryable")},
                         dict(error_code="parse_or_privacy", privacy="hold", identity="hold", retryable=False))
        self.assertEqual(receipt["representation"], "sanitized")
        self.assertEqual(decode(self.j.read_object(receipt["objects"][0])), result)
        self.assertEqual(self.files(self.source_root), self.original_bytes)
        self.assertIsNone(self.original.completed(self.key))
        self.assertEqual(len(self.original.snapshot()["attempts"]), 1)
        suffix = [k for k in self.old_tasks if k != self.key]
        self.assertTrue(suffix)
        self.assertTrue(all(self.original.snapshot()["intervals"][k]["state"] == "unqueried" for k in suffix))

    def test_projection_deterministic_and_bounded(self):
        body = c.page([c.row(c.START, q={str(i):"private" for i in range(1000)})])
        one = d.projection(body, self.binding, 200)
        self.assertEqual(one, d.projection(body, self.binding, 200))
        self.assertLessEqual(len(encode(one)), 12288)
        d.validate(body, encode(one), self.binding, 200)

    def test_unknown_fields_types_and_oversize_refused(self):
        body = c.page([c.row(c.START, q=[])])
        result = d.projection(body, self.binding, 200)
        for change in [dict(unknown="private"), dict(row_count=True), dict(row_is_object=1),
                       dict(fields={}), dict(guard_code="arbitrary"), dict(padding="x"*12289)]:
            bad = dict(result, **change)
            with self.subTest(change=list(change)), self.assertRaises(Hold):
                d.validate(body, encode(bad), self.binding, 200)

    def test_no_values_ids_keys_headers_or_exception_leak(self):
        secrets = ["PRIVATE-OBS", "PRIVATE-ID", "PRIVATE-UNKNOWN", "PRIVATE-NESTED", "PRIVATE-HEADER"]
        bodies = [c.page([dict(t="1999-01-01T00:00:00Z", v=secrets[0], datastream_id=secrets[1])]),
                  c.page([dict(t="1999-01-01T00:00:00Z", **{secrets[2]:secrets[3]})]),
                  c.page([dict(t="1999-01-01T00:00:00Z", q={secrets[3]:secrets[0]})]),
                  c.page([dict(t="PRIVATE-BAD-TIMESTAMP", v=123456.789)])]
        for body in bodies:
            out = encode(d.projection(body, self.binding, 200)).decode()
            for secret in secrets+["1999-01-01T00:00:00Z", "PRIVATE-BAD-TIMESTAMP", "123456.789"]:
                self.assertNotIn(secret, out)
        self.run_page(bodies[0], reply=c.Reply(bodies[0], headers={"Set-Cookie":secrets[4]}))
        for path in self.diagroot.rglob("*"):
            if path.is_file():
                out = path.read_bytes()
                for secret in secrets: self.assertNotIn(secret.encode(), out)

    def test_request_source_body_status_mismatch_refused(self):
        body = c.page([c.row(c.START, q=[])])
        projection = encode(d.projection(body, self.binding, 200))
        for field, value in [("request_id", "0"*64), ("collector_sources", {}), ("checkpoint", "b"*40)]:
            bad = dict(self.binding, **{field:value})
            with self.subTest(field=field), self.assertRaises(Hold): d.validate(body, projection, bad, 200)
        with self.assertRaises(Hold): d.validate(body+b" ", projection, self.binding, 200)
        with self.assertRaises(Hold): d.validate(body, projection, self.binding, 201)

    def test_wrong_incident_task_or_approval_refused(self):
        with self.assertRaises(Hold): d.prepare(self.original, "0"*64, checkpoint="a"*40)
        for field in ("binding_sha256", "incident_sha256", "checkpoint"):
            auth = self.auth(); auth[field] = "0"*len(auth[field])
            fake = c.FiniteExecutor(self.j, self.clock, [])
            with self.assertRaises(Hold): self.adapter.run(executor=fake, wait=fake.wait, authorization=auth)
            self.assertEqual(fake.calls, [])
        bad = copy.deepcopy(self.binding); bad["incident"]["task_id"] = "0"*64
        with self.assertRaises(Hold): d.validate_binding(bad, {})

    def test_binding_reconstruction_cannot_substitute_incident(self):
        altered = copy.deepcopy(self.binding["incident"]); altered["response_sha256"] = "0"*64
        bad = d._binding(altered, "a"*40)
        self.j.binding = bad
        with self.assertRaises(Hold): d.HistoryDiagnosticAdapter(self.j, self.original)._check_incident()
        self.j.binding = self.binding

    def test_one_attempt_no_reopen_replay_or_pagination(self):
        self.run_page(c.page([c.row(c.START, q=[])]))
        with self.assertRaises(Hold): self.run_page(c.page())
        self.j.close()
        with Journal(self.diagroot, self.binding, {}, now=self.clock.now, monotonic=self.clock.monotonic) as j:
            fake = c.FiniteExecutor(j, self.clock, [])
            with self.assertRaises(Hold):
                d.HistoryDiagnosticAdapter(j, self.original).run(executor=fake, wait=fake.wait, authorization=self.auth())
            self.assertEqual(fake.calls, [])
        with Journal(self.diagroot, self.binding, {}, inspect_only=True) as j:
            self.assertEqual(j.snapshot()["counters"]["attempts"], 1)

    def test_reserved_ambiguity_stays_spent(self):
        self.j.session()
        self.j.reserve("history-diagnostic", self.binding["request_id"])
        fake = c.FiniteExecutor(self.j, self.clock, [])
        with self.assertRaises(Hold): self.adapter.run(executor=fake, wait=fake.wait, authorization=self.auth())
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.j.snapshot()["counters"]["attempts"], 1)

    def test_raw_object_receipt_forbidden(self):
        key = self.j.reserve("history-diagnostic", self.binding["request_id"]); self.j.started(key)
        with self.assertRaises(Hold): self.j.received(key, c.page(), source_rows=0)
        self.assertFalse(list(self.diagroot.rglob("*.bin")))

    def test_receipt_projection_mismatch_refused(self):
        body = c.page([c.row(c.START, q=[])])
        key = self.j.reserve("history-diagnostic", self.binding["request_id"]); self.j.started(key)
        details = self.adapter._details(a.CampaignRequestSpec(c.VWC,c.START,c.MIDDLE,c.START),self.clock.now(),self.clock.monotonic())
        details.update(outcome="failure",error_code="parse_or_privacy",privacy="hold",identity="hold")
        with self.assertRaises(Hold):
            self.j.received(key, body, source_rows=2, retain=False,
                sanitized_body=encode(d.projection(body,self.binding,200)),details=details)
        self.assertFalse(list(self.diagroot.rglob("*.bin")))

    def test_wrong_endpoint_or_cursor_refused(self):
        link = self.binding["incident"]
        for end, cursor in [(format_utc(parse_utc(link["end"])+timedelta(seconds=1)),link["start"]),
                            (link["end"],format_utc(parse_utc(link["start"])+timedelta(seconds=1)))]:
            spec = a.CampaignRequestSpec(c.VWC,link["start"],end,cursor)
            self.adapter.current_spec = spec
            with self.assertRaises(Hold): self.adapter._validate_dispatch(self.adapter._request(spec),spec,None)

    def test_transport_exception_is_not_persisted_or_retried(self):
        fake = c.FiniteExecutor(self.j,self.clock,[ConnectionError("PRIVATE-EXCEPTION")])
        with self.assertRaises(ConnectionError): self.adapter.run(executor=fake,wait=fake.wait,authorization=self.auth())
        self.assertEqual(len(fake.calls),1)
        for path in self.diagroot.rglob("*"):
            if path.is_file(): self.assertNotIn(b"PRIVATE-EXCEPTION",path.read_bytes())

    def test_http_redirect_and_service_failure_do_not_retry(self):
        self.run_http_failure(302)

    def run_http_failure(self,status):
        import urllib.error
        fake=c.FiniteExecutor(self.j,self.clock,[c.Reply(b"",status=status)])
        with self.assertRaises((Hold,urllib.error.HTTPError)):
            self.adapter.run(executor=fake,wait=fake.wait,authorization=self.auth())
        self.assertEqual(len(fake.calls),1)

    def test_service_failure_does_not_retry(self):
        self.run_http_failure(503)

    def test_foreign_task_and_interval_reservations_refused(self):
        for key,interval in [(self.key,self.key),("metadata-"+c.VWC,None),("history-diagnostic",self.key)]:
            with self.assertRaises(Hold): self.j.reserve(key,self.binding["request_id"],interval_key=interval)

    def test_projection_persistence_mismatch_refused(self):
        body=c.page([c.row(c.START,q=[])])
        real=d.projection
        def changed(*args):
            result=real(*args);result["unexpected"]="PRIVATE";return result
        with patch.object(d,"projection",changed),self.assertRaises(Hold):
            # The fixed validator must not trust an arbitrary projection dict.
            d.validate(body,encode(dict(real(body,self.binding,200),unexpected="PRIVATE")),self.binding,200)

    def test_body_ceiling_omits_original_and_charges_sentinel(self):
        with self.assertRaises(Hold): self.run_page(b"x"*(d.BODY_BYTES+1))
        self.assertEqual(len(self.fake.calls),1)
        self.assertEqual(self.j.snapshot()["counters"]["response_bytes"],d.BODY_BYTES+1)
        self.assertFalse(list(self.diagroot.rglob("*.bin")))

    def test_deadline_no_retry_or_exception_leak(self):
        fake=c.FiniteExecutor(self.j,self.clock,[a.Deadline("PRIVATE-DEADLINE")])
        with self.assertRaises(a.Deadline):
            self.adapter.run(executor=fake,wait=fake.wait,authorization=self.auth())
        self.assertEqual(len(fake.calls),1)
        self.assertEqual(self.j.snapshot()["counters"]["attempts"],1)
        for path in self.diagroot.rglob("*"):
            if path.is_file(): self.assertNotIn(b"PRIVATE-DEADLINE",path.read_bytes())

    def test_started_ambiguity_and_no_spacing_bypass(self):
        fake=c.FiniteExecutor(self.j,self.clock,[])
        with self.assertRaises(Hold):
            self.adapter.run(executor=fake,wait=lambda seconds:None,authorization=self.auth())
        self.assertEqual(fake.calls,[])
        key=self.j.reserve("history-diagnostic",self.binding["request_id"])
        self.j.started(key)
        self.j.close()
        with Journal(self.diagroot,self.binding,{},now=self.clock.now,monotonic=self.clock.monotonic) as j:
            with self.assertRaises(Hold):
                d.HistoryDiagnosticAdapter(j,self.original).run(executor=fake,wait=fake.wait,authorization=self.auth())
            self.assertEqual(j.snapshot()["counters"]["attempts"],1)

    def test_other_rejections_fail_closed_without_provider_content(self):
        for value in [None,dict(data=[],limit=True),dict(data=[],limit=2016,total=-1),
                      dict(data=[dict(t="PRIVATE-TIME")],limit=2016)]:
            result=self.project(value)
            self.assertEqual(result["admission"],"REJECTED")
            self.assertNotIn("PRIVATE-TIME",encode(result).decode())
        result=d.projection(b'{"PRIVATE":',self.binding,200)
        self.assertEqual(result["guard_code"],"json.invalid")

    def test_q_slots_distinguish_aliases_and_scalar_types(self):
        q = dict(attrib=None, flag=True, annotation_ids=[], annotationIds={})
        result = self.project(dict(data=[c.row(c.START, q=q)], limit=2016))
        shape = result["q_structure"]
        self.assertEqual(shape["total_q_member_count"], 4)
        self.assertEqual(shape["unknown_q_key_count"], 0)
        self.assertTrue(shape["both_annotation_aliases_present"])
        self.assertEqual(shape["slots"]["attrib"], dict(present=True, type="null"))
        self.assertEqual(shape["slots"]["flag"], dict(present=True, type="boolean"))
        self.assertEqual(shape["slots"]["annotation_ids"]["type"], "array")
        self.assertEqual(shape["slots"]["annotationIds"]["type"], "object")
        for item, kind in [(None,"null"), (False,"boolean"), (1,"integer"), (1.5,"number"), ("private","string")]:
            with self.subTest(kind=kind):
                value = self.project(dict(data=[c.row(c.START, q=dict(flag=item))], limit=2016))
                self.assertEqual(value["q_structure"]["slots"]["flag"], dict(present=True,type=kind))
                self.assertEqual(value["q_structure"]["slots"]["attrib"], dict(present=False,type="missing"))
                self.assertFalse(value["q_structure"]["both_annotation_aliases_present"])

    def test_q_immediate_histogram_does_not_descend(self):
        children = [None,True,1,1.5,"PRIVATE",["PRIVATE-DEEP"],{"PRIVATE-KEY":{"deeper":"PRIVATE"}}]
        q = dict(attrib={"PRIVATE-"+str(i):v for i,v in enumerate(children)}, annotation_ids=children)
        shape = self.project(dict(data=[c.row(c.START,q=q)],limit=2016))["q_structure"]
        for name in ("attrib","annotation_ids"):
            self.assertEqual(shape["slots"][name]["child_type_counts"], dict.fromkeys(d.JSON_TYPES,1))
            self.assertEqual(shape["slots"][name]["count"],7)
            self.assertFalse(shape["slots"][name]["count_truncated"])
        self.assertNotIn("PRIVATE",encode(shape).decode())
        self.assertNotIn("deeper",encode(shape).decode())

    def test_q_count_boundaries_and_histogram_saturation(self):
        for n in (0,255,256,257,1000):
            with self.subTest(n=n):
                q = {"PRIVATE-"+str(i):"PRIVATE" for i in range(n)}
                q["attrib"] = ["PRIVATE"]*n + [False]*n
                shape = self.project(dict(data=[c.row(c.START,q=q)],limit=2016))["q_structure"]
                self.assertEqual(shape["unknown_q_key_count"],min(n,256))
                self.assertEqual(shape["unknown_q_key_count_truncated"],n>256)
                self.assertEqual(shape["total_q_member_count"],min(n+1,256))
                self.assertEqual(shape["total_q_member_count_truncated"],n+1>256)
                slot = shape["slots"]["attrib"]
                self.assertEqual(slot["count"],min(2*n,256))
                self.assertEqual(slot["count_truncated"],2*n>256)
                self.assertEqual(slot["child_type_counts"]["string"],min(n,256))
                self.assertEqual(slot["child_type_counts"]["boolean"],min(n,256))
                self.assertLessEqual(len(encode(shape)),12288)

    def test_q_extension_only_for_exact_first_offending_object(self):
        for q in (None,1,"scalar",[],[{}]):
            self.assertNotIn("q_structure",self.project(dict(data=[c.row(c.START,q=q)],limit=2016)))
        for row in (c.row(c.START,lt=[],q={}),c.row(c.START,datastream_id="foreign",q={}),
                    dict(t="invalid",q={}),c.row(c.START,q={},unknown="PRIVATE")):
            self.assertNotIn("q_structure",self.project(dict(data=[row],limit=2016)))
        result = self.project(dict(data=[c.row(c.START,q={})],limit=2016,unknown="PRIVATE"))
        self.assertNotIn("q_structure",result)
        empty = self.project(dict(data=[c.row(c.START,q={})],limit=2016))["q_structure"]
        self.assertEqual(empty["total_q_member_count"],0)
        self.assertTrue(all(not s["present"] for s in empty["slots"].values()))

    def test_q_projection_journal_late_row_roundtrip_no_leak(self):
        rows=[c.row(c.START) for _ in range(2016)]
        rows[1260]["q"] = dict(attrib={"PRIVATE-ATTRIBUTE":"PRIVATE-VALUE"},
                              annotation_ids=["PRIVATE-ID"],flag="PRIVATE-FLAG")
        body=c.page(rows)
        with self.assertRaises(Hold): a.observation_shape(body,c.VWC)
        result=self.run_page(body)
        self.assertEqual((result["guard_code"],result["first_offending_row"]),("row.nested",1260))
        self.assertEqual(result["q_structure"]["slots"]["attrib"]["child_type_counts"]["string"],1)
        receipt=next(e["data"] for e in self.j.events if e["kind"]=="received")
        self.assertEqual(receipt["representation"],"sanitized")
        self.assertEqual(decode(self.j.read_object(receipt["objects"][0])),result)
        d.validate(body,encode(result),self.binding,200)
        self.assertEqual(len(self.fake.calls),1)
        self.assertEqual(self.j.tasks,{})
        self.assertEqual(self.files(self.source_root),self.original_bytes)
        for path in self.diagroot.rglob("*"):
            if path.is_file(): self.assertNotIn(b"PRIVATE-",path.read_bytes())
        with self.assertRaises(Hold): self.run_page(body)

    def test_q_shape_order_independent_and_no_alias_coalescing(self):
        first=dict(attrib={"private1":1,"private2":False},annotation_ids=[1,False])
        second=dict(annotation_ids=[False,1],attrib={"private2":False,"private1":1})
        one=self.project(dict(data=[c.row(c.START,q=first)],limit=2016))
        two=self.project(dict(data=[c.row(c.START,q=second)],limit=2016))
        self.assertEqual(one["q_structure"],two["q_structure"])
        # Response hashes still bind exact bytes; the summary is deliberately lossy.
        self.assertNotEqual(one["response_sha256"],two["response_sha256"])
        self.assertFalse(one["q_structure"]["slots"]["annotationIds"]["present"])

    def test_q_validator_refuses_unknown_content_types_counts_and_gate(self):
        result=self.project(dict(data=[c.row(c.START,q=dict(attrib=[1]))],limit=2016))
        bads=[]
        for k,v in [("total_q_member_count",True),("unknown_q_key_count",257),
                    ("both_annotation_aliases_present",True),("private","SECRET")]:
            bad=copy.deepcopy(result);bad["q_structure"][k]=v;bads.append(bad)
        for k,v in [("value","SECRET"),("count",True),("count_truncated",True),
                    ("child_type_counts",{"private":1}),("type","invalid"),("present",False)]:
            bad=copy.deepcopy(result);bad["q_structure"]["slots"]["attrib"][k]=v;bads.append(bad)
        bad=copy.deepcopy(result);bad["offending_slot"]="v";bads.append(bad)
        bad=copy.deepcopy(result);bad["q_structure"]=None;bads.append(bad)
        bad=copy.deepcopy(result);bad["q_structure"]["slots"]["flag"]["count"]=0;bads.append(bad)
        for bad in bads:
            with self.subTest(bad=bad),self.assertRaises(Hold):d.validate_schema(bad)

    def test_q_projection_cannot_be_removed_or_forged_for_current_body(self):
        body=c.page([c.row(c.START,q=dict(flag=1))]);result=d.projection(body,self.binding,200)
        legacy=copy.deepcopy(result);legacy.pop("q_structure")
        d.validate_schema(legacy)  # Historical version-1 diagnostics remain readable.
        with self.assertRaises(Hold):d.validate(body,encode(legacy),self.binding,200)
        forged=copy.deepcopy(result);forged["q_structure"]["slots"]["flag"]["type"]="string"
        d.validate_schema(forged)
        with self.assertRaises(Hold):d.validate(body,encode(forged),self.binding,200)

    def test_q_all_slots_large_histograms_stay_bounded(self):
        children = [None,True,1,1.5,"PRIVATE",["PRIVATE"],{"PRIVATE":"PRIVATE"}]*300
        q = {name:children for name in d.Q_SLOTS}
        q.update({"PRIVATE-"+str(i):{"PRIVATE":"PRIVATE"} for i in range(1000)})
        body=c.page([c.row(c.START,q=q)]);result=d.projection(body,self.binding,200)
        self.assertTrue(result["q_structure"]["both_annotation_aliases_present"])
        for shape in result["q_structure"]["slots"].values():
            self.assertEqual(shape["child_type_counts"],dict.fromkeys(d.JSON_TYPES,256))
        self.assertLessEqual(len(encode(result)),d.MAX_BYTES)
        self.assertNotIn("PRIVATE",encode(result).decode())
        d.validate(body,encode(result),self.binding,200)


if __name__ == "__main__":
    unittest.main()
