"""Offline authority/shard tests. All promotion and review inputs are synthetic.

The frozen inventory/audit are read from explicit checksum-bound local inputs.
No synthetic review object authorizes acquisition of real observations.
"""
import copy
import os
from pathlib import Path
import sys
import unittest
from datetime import timedelta
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from dendra.history_acquisition import campaign, campaign_execution, eligibility
from dendra.history_acquisition import presentation as p
from dendra.history_acquisition import dimensionless_probe as probe, provider_metadata
from dendra.history_acquisition.model import Inventory, INVENTORY_SHA256, source_binding
from dendra.history_acquisition.safety import Hold, decode, digest, encode, sha
from dendra.transport import parse_utc, format_utc

NOW = "2026-09-26T14:00:00.000Z"
SID = "63531a67a9b61453fa1ca4ed"
VWC = "5d8e42e72da5c3cc53f6531d"
START = "2026-07-01T12:34:00.000Z"
END = "2026-09-26T08:00:00.000Z"


def setUpModule():
    global INV, AUDIT, FP
    INV = Inventory.load(os.environ["DENDRA_INVENTORY"], INVENTORY_SHA256)
    AUDIT = Path(os.environ["DENDRA_RECORD_AGE_AUDIT"]).read_bytes()
    if sha(AUDIT) != p.AUDIT_SHA256:
        raise ValueError("Exact accepted audit required")
    FP = digest(source_binding())


def first(sid=SID, *, start=START, value=0, empty=False):
    response = encode(dict(data=[] if empty else [dict(t=start, v=value)], limit=1, total=0 if empty else 1, skip=0))
    receipt = dict(schema_version="dendra-first-response-receipt-1", inventory_sha256=INVENTORY_SHA256,
        identity=INV.identity(sid), method="GET", url="https://api.dendra.science/v2/datapoints?"+
        urlencode({"datastream_id":sid,"$sort[time]":1,"$limit":1}), status=200,
        response_bytes=len(response), response_sha256=sha(response), complete_body=True,
        requested_at=NOW, retrieved_at=NOW)
    review = dict(rule=p.FIRST_RULE, disposition="ACCEPT_SOURCE_START", receipt_sha256=digest(receipt),
        response_sha256=sha(response), evidence_identity="synthetic-original-first-response",
        reviewer_ref="synthetic-review-only", reviewed_at=NOW)
    return dict(response=response, receipt=receipt, review=review)


def rebind(f):
    f["receipt"].update(response_sha256=sha(f["response"]), response_bytes=len(f["response"]))
    f["review"].update(response_sha256=sha(f["response"]), receipt_sha256=digest(f["receipt"]))
    return f


def authority(starts=None):
    # Classification-specific promotion uses exact audit timestamps below.
    # Planner-only synthetic starts deliberately test arbitrary horizon cases.
    result = p.classify_starts(INV, AUDIT, as_of=NOW)
    for item in result["streams"]:
        if starts and item["stream_id"] in starts:
            proof = p.review_first(INV, item["stream_id"], as_of=NOW,
                **first(item["stream_id"], start=starts[item["stream_id"]]))
            item.update(source_start_authority=p.REVIEWED, source_start_timestamp=proof["start"],
                source_start_evidence=proof, source_start_review_reason=p.FIRST_RULE)
    result["classification_sha256"] = digest({k:v for k,v in result.items() if k!="classification_sha256"})
    return result


def bundle(sid=SID, *, start=START, end=END, configs=None, disposition="ACCEPT_NATIVE"):
    ident = INV.identity(sid); attributes = {}
    if ident["depth_cm"] is not None:
        attributes["depth"] = dict(value=ident["depth_cm"], unit="Centimeter")
    if ident["orientation"] is not None:
        attributes["orientation"] = ident["orientation"]
    station = dict(_id=ident["station_id"], public_level=3, is_hidden=False, is_geo_protected=False)
    admitted = provider_metadata._parse_station(encode(station), ident["station_id"], checked_at=NOW, now=NOW)
    stream = dict(_id=sid, station_id=ident["station_id"], public_level=3, is_hidden=False,
        is_geo_protected=False, attributes=attributes, terms=dict(dt=dict(Unit=ident["native_unit"]),
        ds=dict(Medium="Soil", Variable="VolumetricWaterContent")),
        datapoints_config=configs if configs is not None else [dict(begins_at=start)])
    packet = probe.review_packet(encode(dict(data=[stream], limit=500, total=1, skip=0)), admitted,
        INV, stream_id=sid, metadata_profile=probe.CAMPAIGN_TEMPORAL_PROFILE, checked_at=NOW, now=NOW)
    body = encode(packet)
    review = eligibility.propose(INV, body, packet_sha256=sha(body),
        packet_source_fingerprint=packet["metadata_binding"]["collector_fingerprint"],
        executor_fingerprint=FP, start=start, end=end)
    if disposition != "PENDING":
        review.update(disposition=disposition, reviewer_ref="synthetic-shard-review",
            reviewed_at=NOW, expires_at="2026-09-27T14:00:00.000Z",
            acknowledgements=list(eligibility.ACKNOWLEDGEMENTS))
    return dict(packet=packet, review=review,
        decision=eligibility.decide(INV, body, review, executor_fingerprint=FP, now=NOW))


class AuthorityTests(unittest.TestCase):
    def test_exact_337_closure_audit_cannot_promote_repeatable(self):
        a = p.classify_starts(INV,AUDIT,as_of=NOW)
        self.assertEqual(a,p.classify_starts(INV,AUDIT,as_of=NOW))
        self.assertEqual(len(a["streams"]),337)
        self.assertEqual(a["classes"][p.REVIEWED],[])
        self.assertEqual(sum(a["counts"].values()),337)
        self.assertEqual({r["stream_id"] for r in a["streams"]},
            {sid for sid,i in INV.roster().items() if i["native_unit"] != "Dimensionless"})
        self.assertTrue(all(v==sorted(v) for v in a["classes"].values()))
        for row in a["streams"]:
            self.assertEqual(row["station_id"],INV.identity(row["stream_id"])["station_id"])

    def test_exact_original_promotes_only_matching_stream(self):
        stamp = next(x["source_start_timestamp"] for x in authority()["streams"] if x["stream_id"]==SID)
        a = p.classify_starts(INV,AUDIT,as_of=NOW,evidence={SID:first(start=stamp)})
        self.assertEqual(a["classes"][p.REVIEWED],[SID])
        self.assertEqual(a["streams"],p.classify_starts(INV,AUDIT,as_of=NOW,evidence={SID:first(start=stamp)})["streams"])

    def test_missing_malformed_mismatched_ambiguous_fail_closed(self):
        fixtures = [{}, first(), first(), first(), first(), first(), first()]
        fixtures[1]["receipt"]["response_sha256"]="a"*64
        fixtures[2]["receipt"]["identity"]=INV.identity(VWC)
        fixtures[3]["receipt"]["url"] += "&time%5B%24gte%5D=2020-01-01"
        fixtures[4]["response"]=b"not json"
        fixtures[5]["receipt"]["complete_body"]=False
        fixtures[6]["review"]["disposition"]="PENDING"
        for f in fixtures:
            with self.subTest(fixture=fixtures.index(f)):
                a=p.classify_starts(INV,AUDIT,as_of=NOW,evidence={SID:f})
                self.assertEqual(a["classes"][p.REVIEWED],[])

    def test_conflicting_row_identity_and_wrong_query_are_rejected(self):
        for extra in (dict(datastream_id=VWC),dict(station_id="0"*24)):
            f=first(); body=decode(f["response"]);body["data"][0].update(extra);f["response"]=encode(body)
            with self.assertRaises(Hold): p.review_first(INV,SID,as_of=NOW,**rebind(f))
        f=first();f["receipt"]["url"]=f["receipt"]["url"].replace("%5D=1", "%5D=-1")
        with self.assertRaises(Hold): p.review_first(INV,SID,as_of=NOW,**rebind(f))

    def test_empty_is_unknown_zero_and_negative_are_actual_starts(self):
        checked=p.review_first(INV,SID,as_of=NOW,**first(empty=True))
        self.assertEqual((checked["state"],checked["start"]),(p.UNKNOWN,None))
        for value in (0,-0.031):
            self.assertEqual(p.review_first(INV,SID,as_of=NOW,**first(value=value))["start"],START)

    def test_empty_with_positive_total_and_null_value_hold(self):
        for f in (first(empty=True), first(value=None)):
            if not decode(f["response"])["data"]:
                f["response"]=encode(dict(data=[],limit=1,total=5))
            with self.assertRaises(Hold): p.review_first(INV,SID,as_of=NOW,**rebind(f))

    def test_audit_mismatch_not_silently_promoted(self):
        for f in (first(),first(empty=True)):
            a=p.classify_starts(INV,AUDIT,as_of=NOW,evidence={SID:f})
            self.assertNotIn(SID,a["classes"][p.REVIEWED])
            self.assertIn(SID,a["classes"][p.AUDIT])

    def test_audit_hash_drift_rejected(self):
        with self.assertRaises(Hold): p.classify_starts(INV,AUDIT+b" ",as_of=NOW)

    def test_valid_empty_for_unknown_stream_keeps_no_start(self):
        sid=authority()["classes"][p.UNKNOWN][0]
        a=p.classify_starts(INV,AUDIT,as_of=NOW,evidence={sid:first(sid,empty=True)})
        row=next(x for x in a["streams"] if x["stream_id"]==sid)
        self.assertEqual(row["source_start_review_reason"],"complete_empty_no_source_start")
        self.assertIsNone(row["source_start_timestamp"])


class ReadinessTests(unittest.TestCase):
    def ready(self,b=None,**kw):
        values=dict(source_start_authority=p.REVIEWED,bundle=b,executor_fingerprint=FP,now=NOW,
            horizon=dict(start=START,end=END));values.update(kw)
        return eligibility.dispatch_readiness(INV,SID,**values)

    def test_reviewed_start_without_metadata_is_not_ready(self):
        r=self.ready();self.assertEqual(r["dispatch_readiness"],"NOT_READY")
        self.assertIn("metadata_authority_missing",r["dispatch_blockers"])

    def test_ready_requires_source_and_existing_native_authority(self):
        b=bundle();self.assertEqual(self.ready(b)["dispatch_readiness"],"DISPATCH_READY")
        for state in (p.AUDIT,p.UNKNOWN):
            self.assertEqual(self.ready(b,source_start_authority=state)["dispatch_readiness"],"NOT_READY")

    def test_pending_review_and_incomplete_configuration_block(self):
        self.assertIn("review_required",self.ready(bundle(disposition="PENDING"))["dispatch_blockers"])
        b=bundle(configs=[dict(begins_at="2026-07-02T00:00:00.000Z")])
        self.assertIn("temporal_configuration_incomplete",self.ready(b)["dispatch_blockers"])

    def test_expired_or_changed_source_cannot_dispatch(self):
        r=self.ready(bundle(),now="2026-09-28T14:00:00.000Z")
        self.assertIn("access_metadata_stale_or_future",r["dispatch_blockers"])
        self.assertIn("executor_source_binding_changed",self.ready(bundle(),executor_fingerprint="b"*64)["dispatch_blockers"])

    def test_tampered_packet_never_becomes_ready(self):
        b=bundle();b["packet"]["stream_id"]=VWC
        self.assertIn("metadata_temporal_or_review_integrity_hold",self.ready(b)["dispatch_blockers"])


class ShardTests(unittest.TestCase):
    def test_deterministic_shards_existing_task_ids_capacity_and_independence(self):
        a=authority({SID:START,VWC:START});bundles={sid:bundle(sid) for sid in (SID,VWC)}
        s=campaign.plan_shards(INV,a,bundles,as_of=NOW)
        self.assertFalse(s["planning_holds"])
        self.assertEqual(len(s["shards"]),6)
        self.assertEqual(s,campaign.plan_shards(INV,a,dict(reversed(list(bundles.items()))),as_of=NOW))
        ids=[]
        for shard in s["shards"]:
            sid=shard["streams"][0];m=shard["campaign"]
            original=campaign.plan(m,INV,now=NOW)["tasks"]
            self.assertEqual(shard["tasks"],original)
            binding,tasks=campaign_execution.prepare(m,INV,{sid:bundles[sid]},now=NOW)
            self.assertEqual(set(tasks),set(shard["identity"]["ordered_task_ids"]))
            self.assertEqual({t["identity"]["schema_version"] for t in original},{campaign.TASK})
            c=shard["capacity"];self.assertEqual(c["executor_task_ceiling"],128)
            self.assertEqual((c["retries"],c["redirects"],c["concurrency"]),(0,0,1))
            self.assertEqual(c["maximum_http_attempts"],3*len(tasks))
            self.assertLessEqual(c["measured_journal_header_bytes"],262144)
            self.assertFalse(c["legacy_coverage_pending_limits_apply"])
            self.assertLessEqual(parse_utc(original[-1]["identity"]["end"])-parse_utc(original[0]["identity"]["start"]),timedelta(days=30))
            ids.extend(tasks)
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(s["first_real_acquisition_wave"],s["executable_shard_ids"][0])

    def test_stale_review_can_plan_but_never_enters_wave(self):
        a=authority({SID:START});later="2026-09-28T14:00:00.000Z"
        a["as_of"]=later;a["classification_sha256"]=digest({k:v for k,v in a.items() if k!="classification_sha256"})
        b=bundle(end="2026-09-28T08:00:00.000Z")
        s=campaign.plan_shards(INV,a,{SID:b},as_of=later)
        self.assertTrue(s["shards"]);self.assertEqual(s["executable_shard_ids"],[])
        self.assertTrue(all(x["not_ready_task_count"]==len(x["tasks"]) for x in s["shards"]))

    def test_no_authority_or_no_config_never_invents_tasks(self):
        s=campaign.plan_shards(INV,authority(),{},as_of=NOW)
        self.assertEqual((s["shards"],s["first_real_acquisition_wave"]),([],"NOT_READY"))
        s=campaign.plan_shards(INV,authority({SID:START}),{},as_of=NOW)
        self.assertEqual(s["shards"],[]);self.assertEqual(len(s["planning_holds"]),1)

    def test_config_boundaries_split_before_limits(self):
        lo="2026-09-18T08:00:00.000Z"
        cuts=[format_utc(parse_utc(lo)+timedelta(days=i)) for i in range(9)]
        configs=[dict(begins_at=cuts[i],ends_before=cuts[i+1]) for i in range(8)]
        s=campaign.plan_shards(INV,authority({SID:lo}),{SID:bundle(start=lo,configs=configs)},as_of=NOW)
        self.assertFalse(s["planning_holds"])
        self.assertEqual([len(x["tasks"]) for x in s["shards"]],[7,1])
        intervals=[(t["identity"]["start"],t["identity"]["end"]) for x in s["shards"] for t in x["tasks"]]
        self.assertEqual(intervals,list(zip(cuts,cuts[1:])))

    def test_configuration_gap_holds_without_executable_prefix(self):
        configs=[dict(begins_at=START,ends_before="2026-08-10T00:00:00.000Z"),
                 dict(begins_at="2026-08-11T00:00:00.000Z")]
        s=campaign.plan_shards(INV,authority({SID:START}),{SID:bundle(configs=configs)},as_of=NOW)
        self.assertTrue(s["planning_holds"]);self.assertEqual(s["executable_shard_ids"],[])

    def test_header_capacity_hold_without_increasing_limits(self):
        from unittest.mock import patch
        # Lower only the tested storage boundary to force an impossible task.
        with patch("dendra.history_acquisition.campaign_execution.PAGE_BYTES",1024):
            s=campaign.plan_shards(INV,authority({SID:START}),{SID:bundle()},as_of=NOW)
        self.assertEqual(s["shards"],[]);self.assertIn("header",s["planning_holds"][0]["detail"])

    def test_verified_completion_does_not_repack_later_shards(self):
        s=campaign.plan_shards(INV,authority({SID:START}),{SID:bundle()},as_of=NOW)
        first_shard,second=s["shards"][:2];t=first_shard["tasks"][0]
        seal=dict(task_id=t["task_id"],source_fingerprint=FP,campaign_identity=first_shard["campaign"]["campaign_identity"],
            status="COMPLETE_EMPTY",seal_sha256="a"*64,archive_sha256="b"*64)
        done=campaign.plan(first_shard["campaign"],INV,now=NOW,completed={t["task_id"]:seal})
        self.assertEqual(done["tasks"],[])
        self.assertEqual(campaign.plan(second["campaign"],INV,now=NOW)["tasks"],second["tasks"])
        with self.assertRaises(Hold):
            campaign.plan(second["campaign"],INV,now=NOW,completed={second["tasks"][0]["task_id"]:seal})

    def test_capacity_measurement_matches_real_journal_and_independent_open(self):
        import tempfile
        from dendra.history_acquisition.journal import Journal
        bundles={SID:bundle()}
        s=campaign.plan_shards(INV,authority({SID:START}),bundles,as_of=NOW)
        root=Path(tempfile.mkdtemp(prefix="shard-journals-",dir=os.environ["DENDRA_TEST_ROOT"]))
        for shard in s["shards"][:2]:
            binding,tasks=campaign_execution.prepare(shard["campaign"],INV,bundles,now=NOW)
            with Journal(root,binding,tasks,inventory=INV,create=True,now=lambda:NOW):
                body=(root/'campaigns'/binding['campaign_id']/'manifest.json').read_bytes()
                self.assertEqual(len(body),shard["capacity"]["measured_journal_header_bytes"])
            with Journal(root,binding,tasks,inventory=INV,now=lambda:NOW) as resumed:
                self.assertEqual(set(resumed.tasks),set(tasks))


class RefreshTests(unittest.TestCase):
    def source(self,start="2010-09-30T12:34:00.000Z"):
        item=next(x for x in authority({SID:start})["streams"] if x["stream_id"]==SID)
        return p.authority_start(INV,item)

    def test_fixed_pst_ten_wy_floor_and_intraday_clamp(self):
        x=p.make(INV,mode=p.INITIAL,as_of=NOW,source_starts={SID:self.source()})
        self.assertEqual(x["horizons"][SID],dict(start="2016-10-01T08:00:00.000Z",end=END))
        x=p.make(INV,mode=p.INITIAL,as_of=NOW,source_starts={SID:self.source(START)})
        self.assertEqual(x["horizons"][SID]["start"],START)
        self.assertFalse(x["current_incomplete_day"]["included"])

    def test_seven_days_or_full_missed_catchup_and_exact_replacement(self):
        for frontier,expected in (("2026-09-25T08:00:00.000Z","2026-09-19T08:00:00.000Z"),
                                  ("2026-08-01T08:00:00.000Z","2026-08-01T08:00:00.000Z")):
            r=p.refresh_plan(INV,SID,source_start=self.source(),as_of=NOW,mode="ROUTINE_REFRESH",last_complete_end=frontier)
            self.assertEqual(r["interval"],dict(start=expected,end=END))
            self.assertEqual(r["recompute_completed_days"]["last"],"2026-09-25")
            self.assertEqual(r["replacement"],"replace_retained_native_inside_exact_valid_complete_half_open_query_only")
            self.assertEqual(r["earlier_history"],"untouched")
            self.assertFalse(r["network_execution_authorized"])

    def test_no_guessed_coverage_frontier(self):
        for end in (None,"2030-01-01T00:00:00.000Z"):
            with self.assertRaises(Hold):
                p.refresh_plan(INV,SID,source_start=self.source(),as_of=NOW,mode="ROUTINE_REFRESH",last_complete_end=end)

    def test_current_wy_and_closed_wy_leap_are_distinct(self):
        r=p.refresh_plan(INV,SID,source_start=self.source(),as_of=NOW,mode="CURRENT_WY_RECONCILIATION")
        self.assertEqual(r["interval"]["start"],"2025-10-01T08:00:00.000Z")
        self.assertEqual(r["reconciliation_frequency"],"UNSET")
        r=p.refresh_plan(INV,SID,source_start=self.source(),as_of=NOW,mode="WY_CLOSE_RECONCILIATION",completed_water_year=2024)
        self.assertEqual((parse_utc(r["interval"]["end"])-parse_utc(r["interval"]["start"])).days,366)
        self.assertEqual(r["recompute_completed_days"],dict(first="2023-10-01",last="2024-09-30"))
        with self.assertRaises(Hold):
            p.refresh_plan(INV,SID,source_start=self.source(),as_of=NOW,mode="WY_CLOSE_RECONCILIATION",completed_water_year=2026)
