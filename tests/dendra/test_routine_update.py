"""Offline replacement proof through real Journal/adapter/seal and R science."""
import copy
from datetime import timedelta
import os
from pathlib import Path
import tempfile
import unittest

import test_campaign_integration as c
from test_sealed_history import pins
from dendra.history_acquisition import routine_update as r, sealed_history as s, browser_projection as b
from dendra.history_acquisition.safety import Hold, encode, decode, digest, sha
from dendra.transport import parse_utc, format_utc

START="2024-02-20T08:00:00.000Z"
END="2024-02-29T08:00:00.000Z"
TARGET="2024-03-01T08:00:00.000Z"
ASOF="2024-03-01T12:00:00.000Z"


def setUpModule():
    c.setUpModule()


def samples(sid,lo,hi,kind="good"):
    out=[]; cursor=parse_utc(lo)
    while cursor < parse_utc(hi):
        value=.25 if c.INVENTORY.identity(sid)["native_unit"]=="VolumetricWaterContent" else 25
        out.append(c.row(format_utc(cursor),value));cursor+=timedelta(hours=1)
    if kind=="empty":return []
    if kind=="quality":out[-24]["q"]={"flag":["PRIVATE_QUALITY"],"annotation_ids":["PRIVATE_ANNOTATION"]}
    if kind=="bad":
        for x in out[-24:-12]:x["v"]="invalid"
    if kind=="equal":out.append(copy.deepcopy(out[-20]));out.sort(key=lambda x:x["t"])
    if kind=="conflict":out.append(dict(out[-20],v=.5));out.sort(key=lambda x:x["t"])
    if kind=="zero":
        for x in out:x["v"]=0
    return out


def seal_set(root, windows, kind="good"):
    """Exact finite responses on committed preparation/Journal/adapter, no sockets."""
    root.mkdir(); entries=[]; streams=[]
    for sid,(lo,hi) in sorted(windows.items()):
        bundle=c.bundle(sid,start=START,end="2024-05-01T08:00:00.000Z")
        manifest=c.campaign.make_campaign(c.INVENTORY,campaign_id="routine-test-"+root.name+"-"+sid,
            executor_fingerprint=c.FINGERPRINT,horizons={sid:dict(start=lo,end=hi)},chunk_days=30,
            decisions={sid:bundle["decision"]},budgets=c.campaign.policy(logical_requests=3,attempts=3,total_bytes=25165824,wall_seconds=600))
        binding,tasks=c.campaign_execution.prepare(manifest,c.INVENTORY,{sid:bundle},now=c.NOW)
        native=root/sid;native.mkdir();clock=c.Clock()
        with c.Journal(native,binding,tasks,create=True,inventory=c.INVENTORY,now=clock.now,monotonic=clock.monotonic) as j:
            fake=c.FiniteExecutor(j,clock,[c.page(samples(sid,lo,hi,kind))])
            c.provider_adapter.CampaignAdapter(j).run(executor=fake,wait=fake.wait)
            key=next(iter(tasks));seal=j.snapshot()["intervals"][key]["complete"]
            event=next(e for e in j.events if e["kind"]=="sealed")
            entries.append(dict(root=str(native),campaign_id=binding["campaign_id"],task_id=key,
                source_fingerprint=c.FINGERPRINT,header_sha256=j.header_sha,seal_sha256=event["record_sha256"],
                archive_sha256=seal["objects"][0]["sha256"],identity=c.INVENTORY.identity(sid),start=lo,end=hi))
        streams.append(dict(identity=c.INVENTORY.identity(sid),start=lo,end=hi))
    path=root/"seals.json"
    path.write_bytes(encode(dict(schema_version=s.VERSION,inventory_sha256=c.INVENTORY_SHA256,streams=streams,seals=entries)))
    return r.reference(path)


def seed(root, windows):
    ref=seal_set(root/"source",windows)
    daily=root/"daily"
    s.prepare_product(ref["path"],manifest_sha256=ref["sha256"],inventory=c.INVENTORY,output_root=daily,as_of="2024-02-29T12:00:00.000Z")
    daily_ref=dict(root=str(daily),pins=pins(daily),fingerprint=c.FINGERPRINT)
    return r.initialize(ref,daily_ref,inventory=c.INVENTORY,output_root=root/"seed")


class RoutineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(tempfile.mkdtemp(prefix="routine-",dir=c.TEST_ROOT))
        cls.base=seed(cls.root,{c.VWC:(START,END),c.PERCENT:(START,END)})
        cls.cycle=r.plan(cls.base,inventory=c.INVENTORY,target=TARGET,as_of=ASOF,
            selected_ids=[c.VWC,c.PERCENT],output_root=cls.root/"cycle")
        cls.intent=r.read(cls.cycle)
        cls.replacements={}
        for kind in ("good","empty","quality","bad","equal","conflict","zero"):
            w=cls.intent["work"][c.VWC]
            ref=seal_set(cls.root/kind,{c.VWC:(w["start"],w["end"])},kind)
            gen=r.assemble(cls.cycle,{c.VWC:ref},inventory=c.INVENTORY,output_root=cls.root/(kind+"-generation"))
            daily=r.prepare(gen,inventory=c.INVENTORY,output_root=cls.root/(kind+"-daily"),as_of=ASOF)
            cls.replacements[kind]=(ref,gen,daily,decode((Path(daily["root"])/"daily-output.json").read_bytes()))

    def test_seven_days_versioned(self):
        self.assertEqual(self.intent["policy"],r.policy())
        self.assertEqual(self.intent["work"][c.VWC]["start"],"2024-02-22T08:00:00.000Z")

    def test_healthy_new_completed_day(self):
        d=self.replacements["good"][3]
        self.assertEqual(d["rows"][c.VWC][-1]["date"],"2024-02-29")
        self.assertTrue(d["rows"][c.VWC][-1]["presentation_eligible"])

    def test_fixed_pst_leap_and_scale(self):
        row=self.replacements["good"][3]["rows"][c.VWC][-1]
        self.assertEqual((row["mean_native"],row["mean_percent"],row["water_year"],row["dowy"],row["water_day_aligned"]),(.25,25,2024,152,152))

    def test_only_affected_days_recomputed(self):
        d=self.replacements["good"][3]
        self.assertEqual(len(d["routine_recomputed_dates"][c.VWC]),8)
        self.assertEqual(d["routine_recomputed_dates"][c.PERCENT],[])
        old=decode((self.root/"daily/daily-output.json").read_bytes())
        self.assertEqual(d["rows"][c.VWC][:2],old["rows"][c.VWC][:2])
        self.assertEqual(d["rows"][c.PERCENT],old["rows"][c.PERCENT])

    def test_frozen_cadence_not_reestimated(self):
        old=decode((self.root/"daily/daily-output.json").read_bytes())
        self.assertEqual(self.replacements["bad"][3]["cadence_contexts"],old["cadence_contexts"])

    def test_complete_empty_not_zero(self):
        d=self.replacements["empty"][3]
        self.assertTrue(all(x["query_complete"] and x["mean_native"] is None and x["n_total"]==0 for x in d["rows"][c.VWC][2:]))
        g=r.read(self.replacements["empty"][1]);self.assertEqual(len(g["streams"][c.VWC]["coverage"]["queried_empty"]),1)

    def test_loss_safeguards_remain_publication_hold(self):
        self.assertTrue(self.replacements["empty"][3]["routine_publication_loss_assessment"][c.VWC]["hold"])

    def test_quality_withholds_exact_day(self):
        rows=self.replacements["quality"][3]["rows"][c.VWC]
        self.assertIsNone(rows[-1]["mean_native"]);self.assertFalse(rows[-1]["presentation_eligible"])
        self.assertTrue(rows[-2]["presentation_eligible"])
        self.assertEqual(self.replacements["quality"][3]["routine_day_changes"][c.VWC][-1]["after"],"DAILY_VALUE_WITHHELD")

    def test_bad_hours_localized(self):
        rows=self.replacements["bad"][3]["rows"][c.VWC]
        self.assertFalse(rows[-1]["presentation_eligible"]);self.assertEqual(rows[-1]["n_invalid"],12)
        self.assertTrue(rows[-2]["presentation_eligible"])

    def test_equal_duplicates_not_double_counted(self):
        self.assertEqual(self.replacements["equal"][3]["rows"][c.VWC][-1]["n_total"],24)

    def test_conflict_no_arbitrary_winner(self):
        row=self.replacements["conflict"][3]["rows"][c.VWC][-1]
        self.assertEqual(row["n_duplicate_conflicts"],1);self.assertFalse(row["presentation_eligible"])

    def test_true_zero_preserved(self):
        row=self.replacements["zero"][3]["rows"][c.VWC][-1]
        self.assertEqual(row["mean_native"],0);self.assertTrue(row["presentation_eligible"])

    def test_stream_partial_success_and_explicit_gap(self):
        g=r.read(self.replacements["good"][1])
        self.assertEqual(g["streams"][c.VWC]["outcome"],"STREAM_UPDATE_SUCCESS")
        self.assertEqual(g["streams"][c.PERCENT]["outcome"],"KEEP_PRIOR_ACKNOWLEDGED_HISTORY")
        self.assertEqual(g["streams"][c.PERCENT]["coverage"]["gaps"],[dict(start=END,end=TARGET)])

    def test_missed_twelve_days_not_recent_seven(self):
        target=format_utc(parse_utc(END)+timedelta(days=12))
        pin=r.plan(self.base,inventory=c.INVENTORY,target=target,as_of=target,selected_ids=[c.VWC],output_root=self.root/"missed-cycle")
        w=r.read(pin)["work"][c.VWC]
        self.assertEqual((parse_utc(w["end"])-parse_utc(w["start"])).days,19)
        ref=seal_set(self.root/"missed-source",{c.VWC:(w["start"],w["end"])})
        gen=r.assemble(pin,{c.VWC:ref},inventory=c.INVENTORY,output_root=self.root/"missed-generation")
        self.assertEqual(r.read(gen)["streams"][c.VWC]["coverage"],dict(contiguous_complete_query_frontier=target,gaps=[],queried_empty=[]))

    def test_restart_keeps_durable_original_intent(self):
        interrupted=r.assemble(self.cycle,{},inventory=c.INVENTORY,output_root=self.root/"interrupted")
        c2,_,_=r.resume(self.cycle,c.INVENTORY)
        self.assertEqual(c2,self.intent)
        completed=r.continue_assembly(self.cycle,interrupted,{c.VWC:self.replacements["good"][0]},inventory=c.INVENTORY,output_root=self.root/"resumed")
        self.assertEqual(r.read(completed)["streams"][c.VWC]["coverage"]["gaps"],[])

    def test_completed_stream_reuse_no_reacquisition(self):
        done=r.continue_assembly(self.cycle,self.replacements["good"][1],{},inventory=c.INVENTORY,output_root=self.root/"reuse")
        self.assertEqual(r.read(done)["streams"][c.VWC]["views"],r.read(self.replacements["good"][1])["streams"][c.VWC]["views"])

    def test_incomplete_or_missing_seal_retains_parent(self):
        bad=dict(self.replacements["good"][0],sha256="0"*64)
        g=r.assemble(self.cycle,{c.VWC:bad},inventory=c.INVENTORY,output_root=self.root/"invalid-replacement")
        self.assertEqual(r.read(g)["streams"][c.VWC]["views"],r.read(self.base)["streams"][c.VWC]["views"])

    def test_wrong_stream_refuses_only_affected_replacement(self):
        g=r.assemble(self.cycle,{c.PERCENT:self.replacements["good"][0]},inventory=c.INVENTORY,output_root=self.root/"wrong-stream")
        self.assertEqual(r.read(g)["changes"][c.PERCENT]["status"],"REPLACEMENT_NOT_ADMITTED")

    def test_wrong_unit_depth_orientation_and_interval_refused(self):
        original=r.read(self.replacements["good"][0])
        for n,(key,value) in enumerate((("native_unit","Percent"),("depth_cm",987),("orientation","wrong"))):
            m=copy.deepcopy(original);m["seals"][0]["identity"][key]=value
            path=self.root/("identity-"+str(n)+".json");path.write_bytes(encode(m))
            g=r.assemble(self.cycle,{c.VWC:r.reference(path)},inventory=c.INVENTORY,output_root=self.root/("identity-held-"+str(n)))
            self.assertEqual(r.read(g)["streams"][c.VWC]["outcome"],"KEEP_PRIOR_ACKNOWLEDGED_HISTORY")

    def test_partial_replacement_prefix_not_promoted(self):
        w=self.intent["work"][c.VWC]
        ref=seal_set(self.root/"prefix",{c.VWC:(w["start"],END)})
        g=r.assemble(self.cycle,{c.VWC:ref},inventory=c.INVENTORY,output_root=self.root/"prefix-held")
        self.assertEqual(r.read(g)["streams"][c.VWC]["views"],r.read(self.base)["streams"][c.VWC]["views"])

    def test_attempt_state_from_existing_journal(self):
        e=r.read(self.replacements["good"][0])["seals"][0]
        ref={k:e[k] for k in ("root","campaign_id","header_sha256","task_id")}
        g=r.assemble(self.cycle,{},attempt_refs={c.VWC:[ref]},inventory=c.INVENTORY,output_root=self.root/"attempt-state")
        stream=r.read(g)["streams"][c.VWC]
        self.assertIsNotNone(stream["last_attempted_source_check"])
        self.assertEqual(stream["attempt_evidence"]["journals"][0]["spent_attempts"],1)
        self.assertEqual(stream["outcome"],"KEEP_PRIOR_ACKNOWLEDGED_HISTORY")

    def test_withheld_to_accepted_only_new_complete_evidence(self):
        _,gen,daily,_=self.replacements["quality"]
        base=r.checkpoint_preparation(gen,daily,inventory=c.INVENTORY,output_root=self.root/"quality-prepared")
        target="2024-03-02T08:00:00.000Z"
        cycle=r.plan(base,inventory=c.INVENTORY,target=target,as_of=target,selected_ids=[c.VWC],output_root=self.root/"clear-quality-cycle")
        w=r.read(cycle)["work"][c.VWC]
        replacement=seal_set(self.root/"clear-quality",{c.VWC:(w["start"],w["end"])})
        new=r.assemble(cycle,{c.VWC:replacement},inventory=c.INVENTORY,output_root=self.root/"clear-quality-gen")
        out=r.prepare(new,inventory=c.INVENTORY,output_root=self.root/"clear-quality-daily",as_of=target)
        changes=decode((Path(out["root"])/"daily-output.json").read_bytes())["routine_day_changes"][c.VWC]
        row=next(x for x in changes if x["date"]=="2024-02-29")
        self.assertEqual((row["before"],row["after"]),("DAILY_VALUE_WITHHELD","ACCEPTED"))

    def test_output_inside_original_journal_refused(self):
        source=r.read(self.replacements["good"][0])["seals"][0]["root"]
        with self.assertRaises(Hold):r.prepare(self.replacements["good"][1],inventory=c.INVENTORY,
            output_root=Path(source)/"forbidden",as_of=ASOF)

    def test_partial_current_day_denied(self):
        with self.assertRaises(Hold):r.plan(self.base,inventory=c.INVENTORY,target="2024-03-01T12:00:00.000Z",as_of=ASOF,selected_ids=[c.VWC],output_root=self.root/"partial-day")

    def test_future_day_denied(self):
        with self.assertRaises(Hold):r.plan(self.base,inventory=c.INVENTORY,target="2024-03-02T08:00:00.000Z",as_of=ASOF,selected_ids=[c.VWC],output_root=self.root/"future-day")

    def test_unprepared_parent_cannot_lose_prior_day_changes(self):
        with self.assertRaises(Hold):r.plan(self.replacements["good"][1],inventory=c.INVENTORY,target=TARGET,as_of=ASOF,
            selected_ids=[c.VWC],output_root=self.root/"skipped-preparation")

    def test_preparation_before_completed_target_refused(self):
        with self.assertRaises(Hold):r.prepare(self.replacements["good"][1],inventory=c.INVENTORY,
            output_root=self.root/"early-preparation",as_of="2024-03-01T07:59:59.000Z")

    def test_parent_evidence_root_cannot_receive_new_cycle(self):
        with self.assertRaises(Hold):r.plan(self.base,inventory=c.INVENTORY,target=TARGET,as_of=ASOF,
            selected_ids=[c.VWC],output_root=Path(self.base["path"]).parent/"forbidden-child")

    def test_unbackfilled_stream_requires_bootstrap(self):
        sid=next(s for s in c.INVENTORY.roster() if s not in (c.VWC,c.PERCENT))
        p=r.plan(self.base,inventory=c.INVENTORY,target=TARGET,as_of=ASOF,selected_ids=[sid],output_root=self.root/"unbackfilled")
        self.assertEqual(r.read(p)["work"][sid]["status"],"EXPLICIT_BOOTSTRAP_REQUIRED")

    def test_bounded_catchup_holds_without_shortening(self):
        p=r.plan(self.base,inventory=c.INVENTORY,target="2026-03-01T08:00:00.000Z",as_of=c.NOW,selected_ids=[c.VWC],output_root=self.root/"long-gap")
        self.assertEqual(r.read(p)["work"][c.VWC]["status"],"EXPLICIT_CATCHUP_APPROVAL_REQUIRED")

    def test_generation_corruption_global_hold(self):
        g=r.read(self.base);g["streams"][c.VWC]["identity"]["depth_cm"]=987
        p=self.root/"corrupt.json";p.write_bytes(encode(g))
        with self.assertRaises(Hold):r.load(r.reference(p),c.INVENTORY)

    def test_source_changed_cycle_cannot_resume(self):
        v=r.read(self.cycle);v["source_fingerprint"]="0"*64;v.pop("cycle_id");v["cycle_id"]=digest(v)
        p=self.root/"stale-cycle.json";p.write_bytes(encode(v))
        with self.assertRaises(Hold):r.resume(r.reference(p),c.INVENTORY)

    def test_private_q_never_enters_browser(self):
        daily=self.replacements["quality"][2];out=self.root/"browser"
        b.export(daily["root"],pins=daily["pins"],prepared_fingerprint=daily["fingerprint"],output_root=out)
        for p in out.rglob("*.json"):
            body=p.read_bytes()
            self.assertNotIn(b"PRIVATE_QUALITY",body);self.assertNotIn(b"PRIVATE_ANNOTATION",body)
        inputs,rows=b.load_prepared(daily["root"],pins=daily["pins"],prepared_fingerprint=daily["fingerprint"])
        self.assertEqual(len({x["date"] for x in rows[c.VWC]}),len(rows[c.VWC]))
        self.assertNotIn("2024-02-29",[x["date"] for x in rows[c.VWC]])

    def test_prepared_is_not_acknowledged_publication(self):
        _,gen,daily,_=self.replacements["good"]
        done=r.checkpoint_preparation(gen,daily,inventory=c.INVENTORY,output_root=self.root/"prepared-checkpoint")
        self.assertTrue(all(s["last_acknowledged_publication_receipt"] is None for s in r.read(done)["streams"].values()))
        self.assertFalse(r.read(done)["publication_eligible"])

    def test_normal_campaign_preparation(self):
        bundles={sid:c.bundle(sid,start=START,end="2024-05-01T08:00:00.000Z") for sid in (c.VWC,c.PERCENT)}
        plans=r.prepare_campaigns(self.cycle,bundles,inventory=c.INVENTORY,now=c.NOW)
        self.assertEqual(len(plans),2)
        self.assertTrue(all(len(p["tasks"])==1 for p in plans))

    def test_latest_never_creates_history(self):
        g=r.read(self.replacements["good"][1]);d=self.replacements["good"][3]
        self.assertEqual(d["latest_instantaneous"],[])
        self.assertTrue(all(s["latest_eligible_instantaneous_timestamp"] is None for s in g["streams"].values()))


class ManyStreamsTests(unittest.TestCase):
    def test_single_stream_generation(self):
        root=Path(tempfile.mkdtemp(prefix="routine-single-",dir=c.TEST_ROOT))
        base=seed(root,{c.VWC:(START,END)})
        cycle=r.plan(base,inventory=c.INVENTORY,target=TARGET,as_of=ASOF,selected_ids=[c.VWC],output_root=root/"cycle")
        w=r.read(cycle)["work"][c.VWC]
        replacement=seal_set(root/"replacement",{c.VWC:(w["start"],w["end"])})
        gen=r.assemble(cycle,{c.VWC:replacement},inventory=c.INVENTORY,output_root=root/"candidate")
        self.assertEqual(list(r.read(gen)["streams"]),[c.VWC])
        self.assertEqual(r.read(gen)["streams"][c.VWC]["coverage"]["contiguous_complete_query_frontier"],TARGET)

    def test_generic_many_streams_and_different_frontiers(self):
        root=Path(tempfile.mkdtemp(prefix="routine-many-",dir=c.TEST_ROOT))
        ids=[sid for sid,i in c.INVENTORY.roster().items() if i["native_unit"] in ("Percent","VolumetricWaterContent")][:6]
        base=seed(root,{sid:(START,format_utc(parse_utc(END)-timedelta(days=n%3))) for n,sid in enumerate(ids)})
        plan=r.plan(base,inventory=c.INVENTORY,target=TARGET,as_of=ASOF,selected_ids=ids,output_root=root/"cycle")
        work=r.read(plan)["work"]
        self.assertEqual(len(work),6);self.assertEqual(len({v["start"] for v in work.values()}),3)
        selected=ids[0];w=work[selected]
        replacement=seal_set(root/"empty",{selected:(w["start"],w["end"])},"empty")
        gen=r.assemble(plan,{selected:replacement},inventory=c.INVENTORY,output_root=root/"mixed")
        states=[s["outcome"] for s in r.read(gen)["streams"].values()]
        self.assertEqual(states.count("STREAM_UPDATE_SUCCESS"),1)
        self.assertEqual(states.count("KEEP_PRIOR_ACKNOWLEDGED_HISTORY"),5)


class SavedHistoryTests(unittest.TestCase):
    def test_saved_172_seals_and_quality_survive_private_generation(self):
        root=Path(tempfile.mkdtemp(prefix="routine-saved-",dir=c.TEST_ROOT))
        daily=Path(os.environ["DENDRA_FULL_HISTORY_PREPARED"])
        old_h=decode((daily/"handoff.json").read_bytes())
        ref=dict(root=str(daily),pins=pins(daily),fingerprint=old_h["science_binding"]["collector_fingerprint"])
        base=r.initialize(r.reference(Path(os.environ["DENDRA_FULL_SEAL_SET"])),ref,inventory=c.INVENTORY,output_root=root/"seed")
        g=r.read(base)
        self.assertEqual(sorted(len(x["views"]) for x in g["streams"].values()),[49,123])
        new=r.prepare(base,inventory=c.INVENTORY,output_root=root/"prepared",as_of=old_h["as_of"])
        result=decode((Path(new["root"])/"daily-output.json").read_bytes());old=decode((daily/"daily-output.json").read_bytes())
        self.assertEqual(result["rows"],old["rows"])
        held=[x for x in result["rows"][c.VWC] if "provider_quality_unreviewed" in x["flags"]]
        self.assertEqual(len(held),4)
        self.assertTrue(all(x["query_complete"] and x["mean_native"] is None for x in held))
        b.export(new["root"],pins=new["pins"],prepared_fingerprint=new["fingerprint"],output_root=root/"browser")
        # Actual saved latest stays separately dated; no new source check occurs.
        prior=Path(os.environ["DENDRA_REAL_LATEST_PROOF"])
        e=decode((prior/"latest-evidence.json").read_bytes())
        header=next((prior/"latest/campaigns").glob("*/manifest.json"))
        jref=dict(root=str(prior/"latest"),campaign_id=header.parent.name,
                  header_sha256=sha(header.read_bytes()))
        before=r.read(base)
        snapshot=r.latest_state(base,jref,inventory=c.INVENTORY,evaluated_at="2026-09-30T12:00:00.000Z",output_root=root/"latest-state")
        state=r.read(snapshot)
        self.assertEqual(state["latest_marker"]["record"]["source_timestamp"],e["source_timestamp"])
        self.assertGreater(state["latest_marker"]["record"]["observation_age_seconds"],86400)
        self.assertFalse(state["historical_coverage_changed"])
        self.assertEqual(r.read(base),before)
