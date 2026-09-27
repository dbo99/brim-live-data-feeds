"""Synthetic first-witness dispatch and recovery through the production path.

Explicit frozen local inventory/audit inputs; no live authorization or network.
Test journals remain in the caller's task-owned test root for inspection.
"""
import copy
from datetime import timedelta
import io
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from dendra.history_acquisition import authority_witness as w
from dendra.history_acquisition import presentation as p
from dendra.history_acquisition import dimensionless_probe as probe, provider_metadata
from dendra.history_acquisition.d3_plan import validate_request, RequestSpec as HistorySpec, START as D3_START
from dendra.history_acquisition.journal import Journal
from dendra.history_acquisition.model import Inventory, INVENTORY_SHA256, source_binding
from dendra.history_acquisition.provider_adapter import WitnessAdapter, Deadline
from dendra.history_acquisition.safety import Hold, decode, digest, encode, sha
from dendra.transport import parse_utc, format_utc

NOW = "2026-09-26T14:00:00.000Z"
SID = "63531a67a9b61453fa1ca4ed"
OTHER = "5d8e42e72da5c3cc53f6531d"
FIRST = "2024-02-29T12:34:00.000Z"


def setUpModule():
    global INV, AUDIT
    INV = Inventory.load(os.environ["DENDRA_INVENTORY"], INVENTORY_SHA256)
    AUDIT = Path(os.environ["DENDRA_RECORD_AGE_AUDIT"]).read_bytes()
    if sha(AUDIT) != p.AUDIT_SHA256:
        raise ValueError("Exact accepted audit required")


def packet(sid):
    identity = INV.identity(sid)
    attrs = {}
    if identity["depth_cm"] is not None:
        attrs["depth"] = dict(value=identity["depth_cm"], unit="Centimeter")
    if identity["orientation"] is not None:
        attrs["orientation"] = identity["orientation"]
    station = dict(_id=identity["station_id"], public_level=3, is_hidden=False, is_geo_protected=False)
    admitted = provider_metadata._parse_station(encode(station), identity["station_id"], checked_at=NOW, now=NOW)
    stream = dict(_id=sid, station_id=identity["station_id"], public_level=3, is_hidden=False,
        is_geo_protected=False, attributes=attrs, terms=dict(dt=dict(Unit=identity["native_unit"]),
        ds=dict(Medium="Soil", Variable="VolumetricWaterContent")), datapoints_config=[dict(begins_at=FIRST)])
    return probe.review_packet(encode(dict(data=[stream], limit=500, total=1, skip=0)), admitted,
        INV, stream_id=sid, metadata_profile=probe.CAMPAIGN_TEMPORAL_PROFILE, checked_at=NOW, now=NOW)


def body(sid=SID, *, empty=False, **row):
    return encode(dict(data=[] if empty else [dict(t=FIRST, v=0, datastream_id=sid, **row)],
                       limit=1, total=0 if empty else 234, skip=0))


class Clock:
    seconds = 0
    def now(self):
        return format_utc(parse_utc(NOW)+timedelta(seconds=self.seconds))
    def monotonic(self):
        return self.seconds
    def advance(self, seconds):
        self.seconds += seconds


class Reply(io.BytesIO):
    status = 200
    headers = {}
    def read(self, size=-1):
        if not 0 < signal.getitimer(signal.ITIMER_REAL)[0] <= 25 or not 0 <= size <= 65536:
            raise AssertionError("Shared deadline/bounded read missing")
        return super().read(size)


class WitnessTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.calls = []

    def setup_journal(self, ids=(SID,)):
        self.binding, self.tasks = w.prepare(INV, campaign_id="synthetic-witness",
            packets={s:packet(s) for s in ids}, as_of=NOW)
        self.root = Path(tempfile.mkdtemp(prefix="witness-", dir=os.environ["DENDRA_TEST_ROOT"]))
        self.j = Journal(self.root, self.binding, self.tasks, create=True, inventory=INV,
            now=self.clock.now, monotonic=self.clock.monotonic)
        self.addCleanup(self.j.close)
        self.adapter = WitnessAdapter(self.j)
        return self.j

    def authorization(self):
        return dict(binding_sha256=digest(self.binding), approval_reference="synthetic-only-no-provider-permission",
                    window_start=NOW, window_end=format_utc(parse_utc(NOW)+timedelta(seconds=60)))

    def run_witness(self, response=None, *, executor=None):
        def execute(request, timeout):
            self.assertLessEqual(timeout,25)
            state = self.j.snapshot()
            self.assertEqual(list(state["attempts"].values())[-1]["state"],"started")
            self.assertEqual([e["kind"] for e in self.j.events][-2:],["reserved","started"])
            for event in self.j.events[-2:]:
                self.assertEqual(self.j.fs.read(self.j._event_path(event["sequence"]),65536),encode(event))
            sid = parse_qs(urlsplit(request.full_url).query)["datastream_id"][0]
            self.calls.append((sid,self.clock.seconds))
            return Reply(body(sid) if response is None else response)
        return self.adapter.run(executor=executor or execute, wait=self.clock.advance, authorization=self.authorization())

    def review(self, sid=SID):
        e = w.evidence(self.j,sid)
        return dict(rule=w.REVIEW, disposition="ACCEPT_SOURCE_START", evidence_sha256=e["evidence_sha256"],
                    reviewer_ref="synthetic-review", reviewed_at=self.clock.now())

    def test_request_exact_deterministic_no_interval(self):
        self.setup_journal()
        query = parse_qs(urlsplit(w.RequestSpec(INV.identity(SID)["station_id"],SID).url()).query)
        self.assertEqual(query,{"datastream_id":[SID],"$sort[time]":["1"],"$limit":["1"]})
        self.assertEqual(self.tasks,{})
        self.assertEqual(w.prepare(INV,campaign_id="synthetic-witness",packets={SID:packet(SID)},as_of=NOW),
                         (self.binding,self.tasks))

    def test_wrong_station_or_stream_refused(self):
        for station,sid in [(INV.identity(OTHER)["station_id"],SID),(INV.identity(SID)["station_id"],"0"*24)]:
            with self.subTest(station=station,sid=sid), self.assertRaises(Hold):
                w.RequestSpec(station,sid).url()

    def test_sort_limit_bounds_duplicate_and_credentials_refused(self):
        self.setup_journal()
        spec = w.RequestSpec(INV.identity(SID)["station_id"],SID)
        for query in ["datastream_id="+OTHER+"&$sort[time]=1&$limit=1",
                      "datastream_id="+SID+"&$sort[time]=-1&$limit=1",
                      "datastream_id="+SID+"&$sort[time]=1&$limit=2",
                      urlsplit(spec.url()).query+"&time[$gte]=2020-01-01",
                      urlsplit(spec.url()).query+"&$limit=1"]:
            req = self.adapter._request(spec); req.full_url = "https://api.dendra.science/v2/datapoints?"+query
            with self.subTest(query=query), self.assertRaises(Hold): validate_request(req,spec)
        req=self.adapter._request(spec); req.add_header("Cookie","forbidden")
        with self.assertRaises(Hold): validate_request(req,spec)
        self.assertEqual(self.j.snapshot()["counters"]["attempts"],0)

    def test_history_spec_cannot_substitute(self):
        self.setup_journal()
        spec=HistorySpec("observations",SID,D3_START)
        with self.assertRaises(Hold): self.adapter._validate_dispatch(self.adapter._request(spec),spec,None)

    def test_binding_tamper_and_source_mismatch_refused(self):
        self.setup_journal()
        for field,value in [("tasks", {"history":{}}),("request_policy",{}),("collector_sources",{})]:
            b=copy.deepcopy(self.binding); tasks=self.tasks
            if field=="tasks": tasks=value
            else: b[field]=value
            with self.subTest(field=field), self.assertRaises(Hold): w.validate_binding(b,tasks,inventory=INV)

    def test_metadata_identity_source_configuration_freshness_unchanged(self):
        for name in ["stream", "source", "config", "stale"]:
            value=packet(SID); now=NOW
            if name=="stream": value["station_id"]="0"*24
            if name=="source": value["metadata_binding"]["collector_fingerprint"]="0"*64
            if name=="config": value["configuration_evidence"]["evidence_complete"]=False
            if name=="stale": now="2026-09-28T14:00:00.000Z"
            with self.subTest(name=name), self.assertRaises(Hold):
                w.prepare(INV,campaign_id="bad",packets={SID:value},as_of=now)

    def test_dispatch_rechecks_freshness_before_reservation(self):
        self.setup_journal(); self.clock.advance(86401)
        auth=self.authorization(); auth.update(window_start=self.clock.now(),window_end=format_utc(parse_utc(self.clock.now())+timedelta(seconds=60)))
        with self.assertRaises(Hold): self.adapter.run(executor=lambda *a,**k:self.fail("dispatch"),wait=self.clock.advance,authorization=auth)
        self.assertEqual(self.j.snapshot()["counters"]["attempts"],0)

    def test_receipt_object_review_zero_and_leap_timestamp(self):
        self.setup_journal(); result=self.run_witness()[SID]
        self.assertEqual(result["result"]["timestamp"],FIRST)
        self.assertEqual(self.j.read_object(result["response_object"]),body())
        self.assertEqual(result["response_object"]["sha256"],sha(body()))
        self.assertEqual(set(result["record_identities"]),{"reserved","started","received"})
        checked=p.review_journal_first(self.j,SID,review=self.review(),as_of=self.clock.now())
        self.assertEqual(checked["state"],p.REVIEWED)
        self.assertFalse(checked["dispatch_ready"])
        self.assertFalse(result["history_coverage"])
        self.assertEqual(self.j.snapshot()["intervals"],{})

    def test_two_streams_serial_spacing_and_budgets(self):
        self.setup_journal((SID,OTHER)); results=self.run_witness()
        self.assertEqual(set(results),{SID,OTHER})
        self.assertGreaterEqual(self.calls[1][1]-self.calls[0][1],1)
        c=self.j.snapshot()["counters"]
        self.assertEqual((c["attempts"],c["logical_requests"],c["source_rows"],c["intervals"]),(2,2,2,0))
        self.assertEqual(self.binding["budgets"]["attempts"],2)

    def test_empty_no_authority_no_history_covered_empty(self):
        self.setup_journal(); e=self.run_witness(body(empty=True))[SID]
        self.assertEqual(e["result"]["state"],"COMPLETE_EMPTY")
        self.assertEqual(p.review_journal_first(self.j,SID,review=self.review(),as_of=NOW)["state"],p.UNKNOWN)
        self.assertEqual(self.j.snapshot()["intervals"],{})

    def test_malformed_empty_missing_null_foreign_private_refused(self):
        for payload in [{"data":[],"limit":1,"total":2}, {"data":[],"limit":1},
                        {"data":None,"limit":1,"total":0},
                        {"data":[{"t":FIRST,"v":None}],"limit":1,"total":1},
                        {"data":[{"t":FIRST,"v":0,"datastream_id":OTHER}],"limit":1,"total":1},
                        {"data":[{"t":FIRST,"v":0,"coordinates":[0,0]}],"limit":1,"total":1}]:
            with self.subTest(payload=payload), self.assertRaises(Hold): w.response_shape(encode(payload),SID,retrieved_at=NOW)

    def test_rejected_response_counted_not_retained(self):
        self.setup_journal(); raw=body(coordinates=[0,0])
        with self.assertRaises(Hold): self.run_witness(raw)
        a=next(iter(self.j.snapshot()["attempts"].values()))
        self.assertEqual(a["response_bytes"],len(raw)); self.assertFalse(a["body_retained"])
        with self.assertRaises(Hold): w.evidence(self.j,SID)
        with self.assertRaises(Hold): self.run_witness()
        self.assertEqual(len(self.calls),1)

    def test_missing_attempt_fails_closed(self):
        self.setup_journal()
        with self.assertRaises(Hold): w.evidence(self.j,SID)

    def test_reserved_ambiguous_spent_no_refund_restart(self):
        self.setup_journal(); key="witness-"+SID; cursor=self.binding["witness_requests"][key]["request_id"]
        self.j.reserve(key,cursor); self.j.close()
        with Journal(self.root,self.binding,self.tasks,inventory=INV,now=self.clock.now,monotonic=self.clock.monotonic) as j:
            self.assertEqual(j.snapshot()["counters"]["attempts"],1)
            with self.assertRaises(Hold): j.reserve(key,cursor)
            with self.assertRaises(Hold): WitnessAdapter(j).run(executor=lambda *a,**k:self.fail("retry"),wait=self.clock.advance,authorization=self.authorization())
            with self.assertRaises(Hold): w.evidence(j,SID)

    def test_started_ambiguous_spent_no_refund(self):
        self.setup_journal(); key="witness-"+SID
        a=self.j.reserve(key,self.binding["witness_requests"][key]["request_id"]); self.j.started(a)
        with self.assertRaises(Hold): self.run_witness()
        self.assertEqual(self.j.snapshot()["counters"]["attempts"],1)

    def test_transport_failure_no_retry(self):
        self.setup_journal()
        def fail(request,timeout): self.calls.append(request); raise urllib.error.URLError("synthetic")
        with self.assertRaises(urllib.error.URLError): self.run_witness(executor=fail)
        a=next(iter(self.j.snapshot()["attempts"].values()))
        self.assertFalse(a["details"]["retryable"])
        with self.assertRaises(Hold): self.run_witness()
        self.assertEqual(len(self.calls),1)

    def test_http_redirect_and_throttle_no_retry(self):
        for status in (302,429):
            self.setup_journal()
            def execute(request,timeout):
                self.calls.append(status); r=Reply(b""); r.status=status; return r
            with self.subTest(status=status), self.assertRaises((Hold,urllib.error.HTTPError)):
                self.run_witness(executor=execute)
            a=next(iter(self.j.snapshot()["attempts"].values()))
            self.assertEqual(a["status"],status); self.assertFalse(a["details"]["retryable"])
            self.j.close()
        self.assertEqual(self.calls,[302,429])

    def test_body_ceiling_spent_not_stored(self):
        self.setup_journal()
        with self.assertRaises(Hold): self.run_witness(b" "*(8*1024**2+1))
        a=next(iter(self.j.snapshot()["attempts"].values()))
        self.assertEqual(a["response_bytes"],8*1024**2+1); self.assertFalse(a["body_retained"])

    def test_deadline_failure_spent(self):
        self.setup_journal()
        def expire(request,timeout): raise Deadline("synthetic deadline")
        with self.assertRaises(Deadline): self.run_witness(executor=expire)
        a=next(iter(self.j.snapshot()["attempts"].values()))
        self.assertEqual(a["details"]["error_code"],"deadline"); self.assertFalse(a["details"]["retryable"])

    def test_readonly_reopen_review_preserved_no_request(self):
        self.setup_journal(); expected=self.run_witness()[SID]; review=self.review(); count=len(self.j.events); self.j.close()
        with Journal(self.root,self.binding,self.tasks,inspect_only=True,inventory=INV,now=self.clock.now,monotonic=self.clock.monotonic) as j:
            self.assertEqual(w.evidence(j,SID),expected)
            self.assertEqual(p.review_journal_first(j,SID,review=review,as_of=NOW)["state"],p.REVIEWED)
            self.assertEqual(len(j.events),count)
            with self.assertRaises(Hold): WitnessAdapter(j)
        self.assertEqual(len(self.calls),1)

    def test_receipt_memory_tamper_rejected(self):
        self.setup_journal(); self.run_witness(); self.j.events[-1]["data"]["response_sha256"]="0"*64
        with self.assertRaises(Hold): w.evidence(self.j,SID)

    def test_object_tamper_rejected(self):
        self.setup_journal(); e=self.run_witness()[SID]
        path=self.root/self.j.prefix/e["response_object"]["path"]; path.write_bytes(b"synthetic-corruption")
        with self.assertRaises(Hold): w.evidence(self.j,SID)

    def test_wrong_review_hash_policy_or_stream_rejected(self):
        self.setup_journal(); self.run_witness()
        for key,value in [("rule","unreviewed"),("evidence_sha256","0"*64),("disposition","PENDING")]:
            r=self.review(); r[key]=value
            with self.subTest(key=key),self.assertRaises(Hold): p.review_journal_first(self.j,SID,review=r,as_of=NOW)
        with self.assertRaises(Hold): w.evidence(self.j,OTHER)

    def test_post_receipt_source_change_rejected(self):
        self.setup_journal(); self.run_witness()
        with patch.object(w,"source_binding",return_value={}):
            with self.assertRaises(Hold): w.evidence(self.j,SID)

    def test_audit_alone_cannot_promote_and_journal_enters_existing_reviewer(self):
        audit_only=p.classify_starts(INV,AUDIT,as_of=NOW)
        self.assertEqual(audit_only["classes"][p.REVIEWED],[])
        stamp=next(x["source_start_timestamp"] for x in audit_only["streams"] if x["stream_id"]==SID)
        self.setup_journal(); raw=decode(body()); raw["data"][0]["t"]=stamp
        self.run_witness(encode(raw))
        result=p.classify_starts(INV,AUDIT,as_of=NOW,evidence={SID:dict(journal=self.j,review=self.review())})
        self.assertEqual(result["classes"][p.REVIEWED],[SID])
        checked=next(x for x in result["streams"] if x["stream_id"]==SID)
        self.assertFalse(checked["source_start_evidence"]["dispatch_ready"])
        self.assertFalse(self.binding["metadata_packets"][SID]["observation_acquisition_authorized"])

    def test_reentrant_concurrency_refused(self):
        self.setup_journal()
        def execute(request,timeout):
            with self.assertRaises(Hold): self.run_witness()
            return Reply(body())
        self.run_witness(executor=execute)
        self.assertEqual(self.j.snapshot()["counters"]["attempts"],1)

    def test_unbound_journal_cannot_promote_from_audit(self):
        for value in (None, {}, "not-a-journal"):
            result=p.classify_starts(INV,AUDIT,as_of=NOW,evidence={SID:dict(journal=value,review={})})
            self.assertEqual(result["classes"][p.REVIEWED],[])
            item=next(x for x in result["streams"] if x["stream_id"]==SID)
            self.assertEqual(item["source_start_review_reason"],"underlying_first_response_invalid_or_ambiguous")

    def test_anchor_corruption_prevents_review(self):
        self.setup_journal(); self.run_witness()
        path=self.root/"anchors"/self.binding["campaign_id"]/"00000003.json"
        path.write_bytes(b"synthetic-corruption")
        with self.assertRaises(Hold): w.evidence(self.j,SID)

    def test_interval_or_wrong_cursor_reservation_refused(self):
        self.setup_journal(); key="witness-"+SID; cursor=self.binding["witness_requests"][key]["request_id"]
        for kwargs in [dict(cursor="first"),dict(cursor=cursor,interval_key="history",run=1)]:
            with self.subTest(kwargs=kwargs),self.assertRaises(Hold): self.j.reserve(key,**kwargs)
        self.assertEqual(self.j.snapshot()["counters"]["attempts"],0)

    def test_receipt_persistence_failure_cannot_replay(self):
        self.setup_journal()
        with patch.object(self.j,"received",side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(Hold): self.run_witness()
        self.assertEqual(next(iter(self.j.snapshot()["attempts"].values()))["state"],"started")
        with self.assertRaises(Hold): self.run_witness()
        self.assertEqual(len(self.calls),1)

    def test_wrong_approval_prevents_session_and_attempt(self):
        self.setup_journal(); auth=self.authorization(); auth["binding_sha256"]="0"*64
        with self.assertRaises(Hold):
            self.adapter.run(executor=lambda *a,**k:self.fail("dispatch"),wait=self.clock.advance,authorization=auth)
        self.assertEqual(self.j.events,[])

    def test_no_fake_wait_bypass(self):
        self.setup_journal((SID,OTHER))
        with self.assertRaises(Hold):
            self.adapter.run(executor=lambda request,timeout:Reply(body(parse_qs(urlsplit(request.full_url).query)["datastream_id"][0])),
                             wait=lambda seconds:None,authorization=self.authorization())
        self.assertEqual(self.j.snapshot()["counters"]["attempts"],1)


if __name__ == "__main__":
    unittest.main()
