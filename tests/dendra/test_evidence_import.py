"""Real R offline import/finalization and runtime enforcement, finite fixtures.

The test runner's OS network denial also covers all fresh R/Python children.
Donor Journals are hashed before and after; synthetic acceptance grants no live
authority. No private inventory or real captured response is committed here.
"""
import copy
from datetime import timedelta
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"scripts"))
from dendra.history_acquisition import local_job as l, evidence_import as ei
from dendra.history_acquisition import preparation_recovery as pr, eligibility, authority_witness as aw
from dendra.history_acquisition import recovery, sealed_history
from dendra.history_acquisition.model import Inventory, INVENTORY_SHA256
from dendra.history_acquisition.safety import encode, decode, digest, sha, Hold
from dendra.transport import parse_utc, format_utc
import test_preparation_recovery as donor_helpers
import test_local_job as archive_helpers

SID=archive_helpers.SID
SECOND=archive_helpers.SECOND
INDIAN="65355b8c552645d72d3f0162"
NOW=donor_helpers.NOW
AT=format_utc(parse_utc(NOW)+timedelta(seconds=120))


def setUpModule():
    sys.addaudithook(pr.deny_sockets)


class EvidenceImportTests(unittest.TestCase):
    def setUp(self):
        self.dir=Path(tempfile.mkdtemp(prefix="authority-import-",dir=os.environ["DENDRA_TEST_ROOT"]))
        self.inv=Inventory.load(os.environ["DENDRA_INVENTORY"],INVENTORY_SHA256)
        self.ordinal=0

    def write(self,name,value):
        path=self.dir/name;path.write_bytes(encode(value));return path

    def r(self,mode,config,*args,code=0,now=AT):
        self.ordinal+=1
        cmd=["Rscript","--vanilla",str(l.ENTRY),mode,str(config),*map(str,args),"--offline-now",now]
        run=subprocess.run(cmd,capture_output=True,text=True,timeout=90,
            env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1"))
        self.write(f"command-{self.ordinal}.json",cmd)
        (self.dir/f"r-{self.ordinal}.stdout").write_text(run.stdout)
        (self.dir/f"r-{self.ordinal}.stderr").write_text(run.stderr)
        self.assertEqual(run.returncode,code,run.stdout+run.stderr)
        return decode(run.stdout.encode()) if run.stdout else None

    def donor(self,*,days=1,empty=False,stop=False,crash=False,reuse=(),unknown=False,limits=None):
        helper=donor_helpers.PreparationRecoveryTests()
        helper.inv=self.inv;helper.catalog=l.REPO/"data/input/dendra/pilot_catalog.json";helper.dir=self.dir
        ids=[SID,SECOND,INDIAN,donor_helpers.FAILED,*donor_helpers.UNSTARTED]
        fixture=helper.fixture(ids)
        fixture["witnesses"][donor_helpers.FAILED]=dict(data=None if unknown else [],limit=1)
        for n in range(0,days,30):
            t=format_utc(parse_utc(l.SCOPE["start"])+timedelta(days=n))
            fixture["history"][t]=dict(data=[] if empty else [dict(t=t,v=0)],limit=2016)
        if stop:fixture["stop_after_task"]=1
        if crash:fixture["crash_history"]=True
        fp=self.write("fixture.json",fixture)
        org=self.write("organizations.json",{s:dict(station_id=self.inv.identity(s)["station_id"],
            datastream_id=s,organization_id="6092b070492ae15e05876ed8",
            organization_name="CA Dept. of Fish and Wildlife") for s in ids})
        refs={s:[dict(path=str(org),sha256=sha(org.read_bytes()),record_pointer="/"+s)] for s in ids}
        c=dict(version=l.VERSION,inventory=os.environ["DENDRA_INVENTORY"],catalog=str(helper.catalog),
            catalog_sha256=sha(helper.catalog.read_bytes()),streams=ids,scope=l.SCOPE,limits=l.LIMITS,
            reserve_bytes=l.BODY,root=str(self.dir/"donor-job"),sources=l.sources(),enabled=False,
            fixture=dict(path=str(fp),sha256=sha(fp.read_bytes())),reuse=list(reuse),attribution=refs,
            organization_labels={"6092b070492ae15e05876ed8":"CDFW"})
        self.donor_path=self.write("donor.json",c)
        with l.Job(c,self.inv,clock=l.Clock(fixture),fixture=fixture).open(create=True) as j:
            with patch.object(donor_helpers.wd,"known_zero_rejection",return_value=False):
                with self.assertRaises(Hold):j.metadata()
        self.original=pr._files(Path(c["root"]))
        if unknown:return c
        q,checked,interpretations=pr._collect(self.donor_path,now=AT)
        self.assertTrue(checked["identical"])
        self.assertEqual(q["counts"],dict(reviewable=3,held=5,total=8))
        self.request_path=self.write("request.json",q);self.recovery_path=self.write("recovery.json",interpretations)
        admitted=[s for s in ids if s not in ei.HELD]
        template=dict(c,streams=admitted,root=str(self.dir/"template-job"),attribution={s:refs[s] for s in admitted})
        if limits:template["limits"]=dict(l.LIMITS,**limits)
        self.template_path=self.write("template.json",template)
        rows={s:dict(decision="HOLD") for s in ei.HELD};accepted={s:dict(disposition="EXCLUDE") for s in ei.HELD}
        scope=dict(start=l.SCOPE["start"],end=format_utc(parse_utc(l.SCOPE["start"])+timedelta(days=days)))
        for sid in admitted:
            original=q["streams"][sid];packet=original["metadata"]["packet"]
            native=eligibility.propose(self.inv,encode(packet),packet_sha256=digest(packet),
                packet_source_fingerprint=c["sources"]["collector"],executor_fingerprint=c["sources"]["collector"],**scope)
            native.update(disposition="ACCEPT_NATIVE",reviewer_ref="SYNTHETIC_ONLY",reviewed_at=AT,
                expires_at=format_utc(parse_utc(NOW)+timedelta(hours=1)),acknowledgements=eligibility.ACKNOWLEDGEMENTS)
            decision=eligibility.decide(self.inv,encode(packet),native,executor_fingerprint=c["sources"]["collector"],now=AT)
            source=dict(rule=aw.REVIEW,disposition="ACCEPT_SOURCE_START",reviewer_ref="SYNTHETIC_ONLY",reviewed_at=AT,
                evidence_sha256=original["witness"]["evidence_sha256"])
            place=dict(disposition="ACCEPT_PLACEMENT",reviewer_ref="SYNTHETIC_ONLY",
                station_metadata_sha256=packet["access_evidence"]["station_metadata_sha256"],
                configuration_evidence_sha256=digest(packet["configuration_evidence"]),depth_cm=self.inv.identity(sid)["depth_cm"],
                crs="EPSG:4326",timestamp_meaning="UTC t; preserve native timestamps",scope=scope,evidence=[ei.reference(fp)])
            accepted[sid]=dict(scope=scope,native_review=native,source_review=source,placement_review=place)
            rows[sid]=dict(decision="ADMIT",native_review=native,source_review=source,placement_review=place,
                native_decision=decision,source_start=l.SCOPE["start"],identity_review=dict(frozen_identity=self.inv.identity(sid),
                    source_organization=original["source_organization"]))
        final=dict(schema_version=ei.REVIEW_VERSION,original_selection=ids,original_selection_sha256=digest(ids),
            original_job_id=digest(c),original_sources=c["sources"],repair_sources=c["sources"],
            consolidated_request=ei.reference(self.request_path),recovery_interpretation=ei.reference(self.recovery_path),
            counts=dict(original=8,admitted=3,held=5,excluded=0),admitted_streams=admitted,streams=rows,
            original_job_review_selection=dict(job_id=digest(c),streams={s:accepted[s] for s in ids}),
            bulk_review_input=dict(job_id=digest(template),streams={s:accepted[s] for s in admitted}),config=ei.reference(self.template_path))
        self.review_path=self.write("final-review.json",final)
        self.bulk_root=self.dir/"bulk-job";self.disabled=self.dir/"disabled.json";self.enabled=self.dir/"enabled.json"
        return c

    def bind(self,**kwargs):
        self.donor(**kwargs)
        out=self.r("bind-import",self.template_path,"--donor-config",self.donor_path,"--request",self.request_path,
            "--final-review",self.review_path,"--recovery",self.recovery_path,"--output-config",self.disabled,
            "--job-root",self.bulk_root)
        self.assertEqual(out["outcome"],"BOUND_DISABLED_IMPORT");self.assertFalse(self.bulk_root.exists())
        return out

    def ready(self,**kwargs):
        planned=self.bind(**kwargs)
        self.assertEqual(self.r("inspect",self.disabled)["configuration"]["enabled"],False)
        self.r("acquire",self.disabled,code=2);self.r("prepare",self.disabled,code=2)
        self.assertFalse(self.bulk_root.exists())
        check=self.r("validate-import",self.disabled)
        self.assertEqual(check["planning"]["planned_tasks"],planned["planned_tasks"])
        self.r("finalize-import",self.disabled,"--output-config",self.enabled,"--enable")
        self.assertFalse(self.bulk_root.exists());self.assertNotEqual(digest(l.read(self.disabled)),digest(l.read(self.enabled)))
        self.r("prepare",self.enabled)
        accounting=self.r("import-authority",self.enabled)["accounting"]
        self.assertEqual(accounting["attempts"],0);self.assertIsNone(accounting["window"])
        self.r("metadata",self.enabled,code=2)
        self.assertFalse((self.bulk_root/"metadata.json").exists())
        return planned

    def preserved(self):
        self.assertEqual(pr._files(Path(l.read(self.donor_path)["root"])),self.original)

    def test_real_r_nonempty_empty_and_no_repeat(self):
        for empty in (False,True):
            with self.subTest(empty=empty):
                self.setUp();self.ready(empty=empty)
                first=self.r("acquire",self.enabled)["accounting"]
                self.assertEqual(first["attempts"],3);self.assertEqual(first["metadata_attempts"],0)
                self.assertGreater(parse_utc(first['window']['first_attempt_at']),
                    parse_utc(l.read(self.bulk_root/'import.json')['historical_preparation_accounting']['window']['first_attempt_at']))
                self.assertEqual(first["covered_empty"] if empty else first["sealed"],3)
                self.assertEqual(self.r("resume",self.enabled)["accounting"],first)
                self.assertEqual(l.read(self.bulk_root/"import.json")["job_id"],digest(l.read(self.enabled)))
                for asset in first["assets"]:
                    sid=asset["series_metadata_reference"]["stream_id"]
                    row=l.read(self.bulk_root/"catalog.json")["streams"][sid]
                    self.assertEqual(row["source_organization"]["subprovider_label"],"Dendra-CDFW")
                    self.assertEqual(row["source_organization"]["subprovider_name"],"CA Dept. of Fish and Wildlife")
                    self.assertIn(sid,[SID,SECOND,INDIAN])
                self.assertEqual(l.read(self.bulk_root/"import.json")["historical_preparation_accounting"]["attempts"],10)
                self.preserved()

    def test_interrupted_then_fresh_r_resume_keeps_window(self):
        self.ready(days=31,stop=True)
        first=self.r("acquire",self.enabled);self.assertEqual(first["outcome"],"STOPPED_AT_TASK_BOUNDARY")
        self.assertEqual(first["accounting"]["attempts"],1)
        later=self.r("resume",self.enabled)["accounting"]
        self.assertEqual(later["attempts"],6);self.assertEqual(later["metadata_attempts"],0)
        self.assertEqual(later["window"],first["accounting"]["window"]);self.preserved()

    def test_crash_reservation_is_spent_and_no_retry(self):
        self.ready(crash=True);self.r("acquire",self.enabled,code=77)
        first=self.r("status",self.enabled)["accounting"]
        self.assertEqual(first["attempts"],1);self.assertTrue(first["spent_unsealed"])
        self.r("resume",self.enabled,code=2)
        self.assertEqual(self.r("status",self.enabled)["accounting"],first);self.preserved()

    def test_runtime_missing_tampered_review_plan_and_config(self):
        self.ready();c=l.read(self.enabled)
        originals={n:(self.bulk_root/n).read_bytes() for n in ("review.json","import.json","plan.json")}
        for name in originals:
            with self.subTest(name=name):
                (self.bulk_root/name).write_bytes(encode({}))
                self.r("resume",self.enabled,code=2)
                (self.bulk_root/name).write_bytes(originals[name])
                self.assertEqual(self.r("status",self.enabled)["accounting"]["attempts"],0)
        (self.bulk_root/"review.json").rename(self.bulk_root/"missing-review.json")
        self.r("acquire",self.enabled,code=2)
        (self.bulk_root/"missing-review.json").rename(self.bulk_root/"review.json")
        bad=dict(c,enabled=False);self.enabled.write_bytes(encode(bad));self.r("resume",self.enabled,code=2)
        bad=dict(c,sources=dict(c["sources"],collector="0"*64));self.enabled.write_bytes(encode(bad));self.r("resume",self.enabled,code=2)
        self.enabled.write_bytes(encode(c)+b" ");self.r("resume",self.enabled,code=2)
        self.enabled.write_bytes(encode(c));self.assertEqual(self.r("status",self.enabled)["accounting"]["attempts"],0)
        self.preserved()

    def test_altered_original_pins_roster_interval_job_and_expiry(self):
        self.bind();c=l.read(self.disabled)
        for field,value in (("streams",[SID]),("scope",dict(l.SCOPE,end="2026-09-30T08:00:00Z"))):
            bad=copy.deepcopy(c);bad[field]=value;path=self.write(field+"-bad.json",bad)
            self.r("validate-import",path,code=2)
        old=self.review_path.read_bytes()
        self.review_path.write_bytes(old+b" ");self.r("validate-import",self.disabled,code=2);self.review_path.write_bytes(old)
        old_request=self.request_path.read_bytes();self.request_path.rename(self.dir/"missing-request.json")
        self.r("validate-import",self.disabled,code=2);self.request_path.write_bytes(old_request)
        for mutation in ("job","held","scope","native"):
            final=decode(old)
            if mutation=="job":final["original_job_id"]="0"*64
            if mutation=="held":final["streams"][donor_helpers.FAILED]["decision"]="ADMIT"
            if mutation=="scope":final["streams"][SID]["native_review"]["scope"]["start"]="2025-11-01T08:00:00.000Z"
            if mutation=="native":final["streams"][SID]["native_review"]["disposition"]="HOLD"
            path=self.write(mutation+"-review.json",final);bad=copy.deepcopy(c);bad["authority_import"]["final_review"]=ei.reference(path)
            self.r("validate-import",self.write(mutation+"-config.json",bad),code=2)
        self.r("validate-import",self.disabled,code=2,now="2026-10-02T18:01:00.000Z")
        self.assertFalse(self.bulk_root.exists());self.preserved()

    def test_runtime_held_plan_and_evidence_missing_or_changed(self):
        self.ready();plan=l.read(self.bulk_root/"plan.json");old=encode(plan)
        plan["tasks"][0]["stream_id"]=donor_helpers.FAILED;self.write("unused.json",plan)
        (self.bulk_root/"plan.json").write_bytes(encode(plan));self.r("acquire",self.enabled,code=2)
        (self.bulk_root/"plan.json").write_bytes(old)
        c=l.read(self.enabled);validated=ei.validate(c,self.inv,now=AT)
        original_root=Path(l.read(self.donor_path)["root"])
        candidate=next(original_root.rglob("*.bin"))
        data=candidate.read_bytes();candidate.rename(candidate.with_suffix(".missing"))
        self.r("acquire",self.enabled,code=2);candidate.write_bytes(data+b" ")
        self.r("resume",self.enabled,code=2);candidate.write_bytes(data)
        # The temporary synthetic tamper copy is outside the donor snapshot.
        candidate.with_suffix(".missing").rename(self.dir/"tamper-original-copy.json")
        self.assertEqual(self.r("status",self.enabled)["accounting"]["attempts"],0);self.preserved()

    def test_unknown_accounting_without_recovery_blocks(self):
        self.donor(unknown=True)
        with self.assertRaises(Hold):pr._collect(self.donor_path,now=AT)
        self.preserved()

    def test_attempt_byte_deadline_storage_and_writer_guards(self):
        for limits in (dict(attempts=3),dict(bytes=3*l.BODY-1),dict(seconds=10)):
            with self.subTest(limits=limits):
                self.setUp();self.ready(days=31,stop=True,limits=limits)
                if "bytes" in limits:
                    self.r("acquire",self.enabled,code=2);self.assertEqual(self.r("status",self.enabled)["accounting"]["attempts"],0)
                else:
                    first=self.r("acquire",self.enabled)["accounting"]
                    self.r("resume",self.enabled,code=2,now=format_utc(parse_utc(AT)+timedelta(seconds=20)) if "seconds" in limits else AT)
                    self.assertEqual(self.r("status",self.enabled)["accounting"],first)
                c=l.read(self.enabled)
                with patch.object(l.shutil,"disk_usage",return_value=type("Disk",(),dict(free=0))()):
                    with self.assertRaises(Hold):
                        l.Job(c,self.inv,clock=l.Clock(l.read(c["fixture"]["path"]))).storage(c['limits']['bytes'])
                with open(self.bulk_root/"writer.lock","rb") as lock:
                    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);self.r("status",self.enabled,code=2)
                self.preserved()

    def test_reused_nonempty_and_empty_have_no_duplicate_requests(self):
        for empty in (False,True):
            with self.subTest(empty=empty):
                self.setUp();helper=archive_helpers.RJobTests();helper.setUp();helper.ready(empty=empty)
                asset=helper.r("acquire")["accounting"]["assets"][0];old=pr._files(Path(helper.c["root"]))
                with recovery.open_evidence(asset["root"],asset["campaign_id"],self.inv) as j:
                    task=j.tasks[asset["task_id"]];seal=j.snapshot()["intervals"][asset["task_id"]]["complete"]
                    event=next(e for e in j.events if e["kind"]=="sealed")
                    entry=dict(root=asset["root"],campaign_id=asset["campaign_id"],task_id=asset["task_id"],
                        source_fingerprint=digest(j.binding["collector_sources"]),header_sha256=j.header_sha,
                        seal_sha256=event["record_sha256"],archive_sha256=seal["objects"][0]["sha256"],
                        **{k:task[k] for k in ("identity","start","end")})
                    with patch.object(sealed_history.h,"_seal",side_effect=AssertionError("Historical row replay")):
                        sealed_history.referenced_archive(j,entry,self.inv)
                self.assertEqual(self.ready(reuse=[entry])["planned_tasks"],2)
                first=self.r("acquire",self.enabled)["accounting"]
                self.assertEqual(first["attempts"],2);self.assertEqual(first["metadata_attempts"],0)
                self.assertEqual(self.r("resume",self.enabled)["accounting"],first)
                self.assertEqual(pr._files(Path(helper.c["root"])),old);self.preserved()


if __name__=="__main__":unittest.main()
