"""Socket-denied focused recovery proofs; original live evidence is read-only."""
import copy
from datetime import timedelta
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"scripts"))
from dendra.history_acquisition import local_job as l, authority_package as ap
from dendra.history_acquisition import authority_witness as aw, metadata_acquisition as ma
from dendra.history_acquisition import preparation_recovery as pr, recovery, witness_diagnostic as wd, eligibility
from dendra.history_acquisition.journal import utc_now
from dendra.history_acquisition.model import Inventory, INVENTORY_SHA256, source_binding
from dendra.history_acquisition.safety import encode, decode, digest, sha, Hold, Root
from dendra.transport import parse_utc, format_utc

NOW = "2026-10-02T17:00:00.000Z"
FAILED = "65355b8c5c0d5f806969a8ea"
UNSTARTED = ["65355b8d8081876e27c95d31", "65355b8dd07087215fd59748",
             "65355b8dd070878668d5974a", "65355b8e2148dbec414875a4"]
SPENT = "7fcb6cfec20702d5d583c77dae14ff91880d7aee3a92cfc25cebcdc0998e09f9"


def setUpModule():
    sys.addaudithook(pr.deny_sockets)


class PreparationRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inv = Inventory.load(os.environ["DENDRA_INVENTORY"], INVENTORY_SHA256)
        cls.catalog = l.REPO/"data/input/dendra/pilot_catalog.json"

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="preparation-recovery-",dir=os.environ["DENDRA_TEST_ROOT"]))

    def fixture(self, ids):
        authority = l.provider_metadata.load_authority(self.inv,self.catalog)
        f = dict(now=NOW,vocabulary=dict(_id="dt-unit",terms=list(authority["dictionary_terms"].values())),
            stations={},datastreams={},witnesses={},history={})
        for sid in ids:
            i = self.inv.identity(sid); station = i["station_id"]
            f["stations"][station] = dict(_id=station,name="Synthetic station",public_level=3,is_hidden=False,
                is_geo_protected=False,geo=dict(type="Point",coordinates=[-115,33]))
            f["datastreams"].setdefault(station,dict(data=[],total=0,limit=500,skip=0))
            attrs = dict(depth=dict(value=i["depth_cm"],unit="Centimeter"),orientation=i["orientation"])
            f["datastreams"][station]["data"].append(dict(_id=sid,station_id=station,public_level=3,
                is_hidden=False,is_geo_protected=False,attributes=attrs,
                terms=dict(dt=dict(Unit=i["native_unit"]),ds=dict(Medium="Soil",Variable="VolumetricWaterContent")),
                datapoints_config=[dict(begins_at=l.SCOPE["start"])]))
            f["datastreams"][station]["total"] += 1
            f["witnesses"][sid] = dict(data=[dict(t=l.SCOPE["start"],v=0,datastream_id=sid)],limit=1)
        return f

    def make_job(self, ids, rejected, *, legacy=False):
        fixture = self.fixture(ids); fixture["witnesses"][FAILED] = rejected
        fp = self.dir/"fixture.json"; fp.write_bytes(encode(fixture))
        c = dict(version=l.VERSION,inventory=os.environ["DENDRA_INVENTORY"],catalog=str(self.catalog),
            catalog_sha256=sha(self.catalog.read_bytes()),streams=ids,scope=l.SCOPE,limits=l.LIMITS,
            reserve_bytes=l.BODY,root=str(self.dir/"synthetic-job"),sources=l.sources(),enabled=False,
            fixture=dict(path=str(fp),sha256=sha(fp.read_bytes())),reuse=[],
            attribution={s:[] for s in ids},organization_labels={})
        self.config = self.dir/"config.json"; self.config.write_bytes(encode(c)); self.c = c
        clock = l.Clock(fixture)
        with l.Job(c,self.inv,clock=clock,fixture=fixture).open(create=True) as j:
            with patch.object(wd,"known_zero_rejection",return_value=False) if legacy else patch.dict({},{}):
                if legacy or rejected.get("data") is None:
                    with self.assertRaises(Hold):j.metadata()
                else:
                    result = j.metadata(); self.assertIn("witness_hold",result[FAILED])
        self.package = l.read(Path(c["root"])/"authority/package.json")
        self.witness_id = ap.witness_campaign_id(self.package,FAILED)
        return c

    def witness(self):
        return recovery.open_evidence(str(Path(self.c["root"])/"authority"),self.witness_id,self.inv)

    def test_future_known_zero_remains_inadmissible_after_reopen(self):
        self.make_job([FAILED],dict(data=[],limit=1))
        with self.witness() as j:
            a = next(iter(j.snapshot()["attempts"].values()))
            self.assertEqual(a["source_rows"],0)
            self.assertEqual(j.snapshot()["counters"]["unknown_row_responses"],0)
            self.assertEqual(a["state"],"failure")
            with self.assertRaises(Hold):aw.evidence(j,FAILED)
            ap._global_guard(j)
            interpreted = recovery.rejected_empty_witness(j,FAILED)
            self.assertFalse(interpreted["witness_admissible"])
            self.assertEqual(interpreted["interpreted_returned_rows"],0)

    def test_truly_unknown_response_keeps_integrity_hold(self):
        self.make_job([FAILED],dict(data=None,limit=1))
        with self.witness() as j:
            self.assertEqual(j.snapshot()["counters"]["unknown_row_responses"],1)
            with self.assertRaisesRegex(Hold,"Package integrity/unknown accounting HOLD"):ap._global_guard(j)
            with self.assertRaises(Hold):recovery.rejected_empty_witness(j,FAILED)

    def test_legacy_interpretation_changes_no_journal_bytes_or_counters(self):
        self.make_job([FAILED],dict(data=[],limit=1),legacy=True)
        before = pr._files(Path(self.c["root"]))
        with self.witness() as j:
            old = j.snapshot()
            interpreted = recovery.rejected_empty_witness(j,FAILED)
            self.assertEqual(interpreted["original_receipt_source_rows"],None)
            self.assertEqual(interpreted["interpreted_returned_rows"],0)
            self.assertTrue(interpreted["attempt_spent"])
            self.assertFalse(interpreted["raw_body_retained"])
            self.assertEqual(j.snapshot(),old)
            with self.assertRaises(Hold):ap._global_guard(j)
            with self.assertRaises(Hold):aw.evidence(j,FAILED)
        self.assertEqual(pr._files(Path(self.c["root"])),before)

    def test_projection_recovery_requires_exact_supported_evidence(self):
        self.make_job([FAILED],dict(data=[],limit=1),legacy=True)
        with self.witness() as j:
            a = next(iter(j.snapshot()["attempts"].values()))
            d = decode(j.read_object(a["objects"][0])); req = j.binding["witness_requests"]["witness-"+FAILED]
            self.assertTrue(wd.known_zero_rejection(d,req))
            cases = [("complete_body",False),("returned_row_count",None),("admitted",True),
                ("completeness_check","PASS"),("selection_check","FAIL"),("rows",[{}]),
                ("diagnostic_sha256","0"*64),("fields",None),("reason",{})]
            for field,value in cases:
                with self.subTest(field=field):
                    bad = copy.deepcopy(d); bad[field] = value
                    if field != "diagnostic_sha256":bad["diagnostic_sha256"] = digest({k:v for k,v in bad.items() if k != "diagnostic_sha256"})
                    self.assertFalse(wd.known_zero_rejection(bad,req))

    def test_read_only_export_28_closure_and_r_fresh_process(self):
        ids = sorted(s for values in l.CDFW.values() for s in values)
        self.make_job(ids,dict(data=[],limit=1),legacy=True)
        before = pr._files(Path(self.c["root"])); output = self.dir/"review-output"; output.mkdir()
        command = ["Rscript","--vanilla",str(l.ENTRY),"recover-review",str(self.config),"--output-root",str(output)]
        run = subprocess.run(command,capture_output=True,text=True,timeout=60,env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1"))
        (self.dir/"r.stdout").write_text(run.stdout);(self.dir/"r.stderr").write_text(run.stderr)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        request = l.read(output/"CONSOLIDATED_REVIEW_REQUEST.json")
        self.assertEqual(request["counts"],dict(reviewable=23,held=5,total=28))
        self.assertEqual(request["accounting"]["attempts"],42)
        self.assertEqual(set(request["unattempted_witnesses"]),set(UNSTARTED))
        self.assertEqual(pr._files(Path(self.c["root"])),before)
        check = subprocess.run([sys.executable,"-B","-c",
            "from pathlib import Path;from dendra.history_acquisition.local_job import read;"
            "from dendra.history_acquisition.safety import digest;import sys;"
            "r=read(Path(sys.argv[1]));assert r['counts']==dict(reviewable=23,held=5,total=28);"
            "assert r['review_request_sha256']==digest({k:v for k,v in r.items() if k!='review_request_sha256'})",
            str(output/"CONSOLIDATED_REVIEW_REQUEST.json")],capture_output=True,text=True,timeout=30,
            env=dict(os.environ,PYTHONPATH=str(l.REPO/"scripts"),PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(check.returncode,0,check.stderr)
        self.assertEqual(pr._files(Path(self.c["root"])),before)
        for sid in [FAILED,*UNSTARTED]:
            bad = copy.deepcopy(request["review_input"])
            bad["streams"] = {s:dict(disposition="EXCLUDE") for s in ids}
            bad["streams"][sid] = dict(source_review={"disposition":"ACCEPT_SOURCE_START"},
                native_review={"disposition":"ACCEPT_NATIVE"},placement_review={"disposition":"ACCEPT_PLACEMENT"},scope=l.SCOPE)
            with self.subTest(sid=sid),self.assertRaisesRegex(Hold,"Held witness cannot enter an acquisition plan"):
                pr.validate_review_selection(request,bad)
        with self.assertRaisesRegex(Hold,"Explicit source/native/placement review"):
            pr.validate_review_selection(request,request["review_input"])
        excluded = copy.deepcopy(request["review_input"])
        excluded["streams"] = {sid:dict(disposition="EXCLUDE") for sid in ids}
        self.assertEqual(pr.validate_review_selection(request,excluded),excluded)
        with Root(Path(self.c["root"])) as fs:
            job = l.Job(self.c,self.inv,clock=l.Clock());job.fs = fs
            accounting = job.accounting(evidence_sources=source_binding())
            self.assertEqual(accounting["attempts"],42)
        # Old metadata entry remains closed; it cannot spend the four missing calls.
        with l.Job(self.c,self.inv,clock=l.Clock(),fixture=l.read(Path(self.c["fixture"]["path"]))).open() as job:
            with self.assertRaisesRegex(Hold,"Spent unsealed child requires review"):job.capacity(46,46*l.BODY,46)

    def test_existing_plan_guards_reject_each_held_stream_without_writes(self):
        ids = sorted(s for values in l.CDFW.values() for s in values)
        self.make_job(ids,dict(data=[],limit=1),legacy=True)
        request,_,_ = pr._collect(self.config,now=utc_now())
        before = pr._files(Path(self.c["root"]))
        for sid in [FAILED,*UNSTARTED]:
            item = copy.deepcopy(request["review_input"]["streams"][next(s for s in ids if request["streams"][s]["review_status"] == "REVIEW_REQUIRED")])
            meta = request["streams"][sid]["metadata"]
            native = eligibility.propose(self.inv,encode(meta["packet"]),packet_sha256=meta["packet_sha256"],
                packet_source_fingerprint=digest(source_binding()),executor_fingerprint=digest(source_binding()),**l.SCOPE)
            now = utc_now()
            native.update(disposition="ACCEPT_NATIVE",reviewer_ref="SYNTHETIC_ONLY",reviewed_at=now,
                expires_at=format_utc(parse_utc(now)+timedelta(hours=1)),acknowledgements=eligibility.ACKNOWLEDGEMENTS)
            item["native_review"] = native
            item["source_review"].update(disposition="ACCEPT_SOURCE_START",reviewer_ref="SYNTHETIC_ONLY",reviewed_at=now)
            review = dict(job_id=digest(self.c),streams={s:dict(disposition="EXCLUDE") for s in ids})
            review["streams"][sid] = item
            path = self.dir/(sid+"-invalid-review.json");path.write_bytes(encode(review))
            with self.subTest(sid=sid):
                with l.Job(self.c,self.inv,clock=l.Clock()).open() as job:
                    if sid == FAILED:
                        with self.assertRaisesRegex(Hold,"Witness is not a complete original response"):job.review(path)
                    else:
                        with self.assertRaises(FileNotFoundError):job.review(path)
                self.assertFalse((Path(self.c["root"])/"plan.json").exists())
                self.assertFalse((Path(self.c["root"])/"review.json").exists())
                self.assertEqual(pr._files(Path(self.c["root"])),before)

    @unittest.skipUnless(os.environ.get("DENDRA_CDFW_JOB_CONFIG"),"Explicit original read-only evidence required")
    def test_original_42_spent_23_reviewable_five_held_and_exact_windows(self):
        path = Path(os.environ["DENDRA_CDFW_JOB_CONFIG"])
        c = l.read(path); before = pr._files(Path(c["root"]))
        request, checked, interpretations = pr._collect(path,now=utc_now())
        self.assertEqual(request["counts"],dict(reviewable=23,held=5,total=28))
        self.assertEqual({k:request["accounting"][k] for k in ("attempts","metadata_attempts","bytes")},
            dict(attempts=42,metadata_attempts=42,bytes=434543))
        self.assertEqual(request["accounting"]["spent_unsealed"],[SPENT])
        self.assertEqual(request["accounting"]["window"],dict(first_attempt_at="2026-10-02T17:17:36.518536Z",deadline="2026-10-02T19:17:36.518536Z"))
        self.assertEqual(request["original_package_authorization"]["window_end"],"2026-10-02T18:00:36.396195Z")
        self.assertEqual(len(request["original_archive_references"]),50)
        self.assertEqual(request["original_archive_references"],c["reuse"])
        self.assertEqual(len(interpretations),1);self.assertEqual(interpretations[0]["attempt_key"],SPENT)
        self.assertEqual(request["operations"][-1]["task_key"],"witness-"+FAILED)
        self.assertEqual(set(request["unattempted_witnesses"]),set(UNSTARTED))
        self.assertTrue(checked["identical"]);self.assertEqual(pr._files(Path(c["root"])),before)
        for sid,row in request["streams"].items():
            self.assertEqual(row["subprovider_label"],"Dendra-CDFW")
            self.assertFalse(row["dispatch_ready"])
            if row["review_status"] == "REVIEW_REQUIRED":
                self.assertEqual(row["metadata"]["source_fingerprint"],c["sources"]["collector"])
                self.assertEqual(row["witness"]["collector_fingerprint"],c["sources"]["collector"])


if __name__ == "__main__":unittest.main()
