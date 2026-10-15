"""Offline routine candidates over immutable, independently verified Journal seals.

There is no acquisition ledger here: pending work stays in the existing campaign
Journal. A pinned cycle is a durable intent, and generations are interval views,
never edits to the source seals. All files are private, caller-pinned evidence.
"""
import copy
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import daily_handoff as h, observation_quality as quality, sealed_history as sealed
from .model import source_binding
from .safety import Hold, Root, decode, digest, encode, require, sha
from ..transport import format_utc, parse_utc

VERSION = "dendra-routine-generation-1"
CYCLE = "dendra-routine-cycle-1"
POLICY = "dendra-routine-overlap-1"
HANDOFF = "dendra-sealed-daily-handoff-4"
LINEAGE = "dendra-routine-view-lineage-1"
MAX_BYTES = 32 * 1024**2
MAX_VIEWS = 4096
PST = timezone(timedelta(hours=-8))


def policy(*, overlap_days=7, max_catchup_days=366):
    require(type(overlap_days) is int and 1 <= overlap_days <= 30 and
            type(max_catchup_days) is int and overlap_days <= max_catchup_days <= 366,
            "Bounded overlap/catch-up policy required")
    return dict(version=POLICY, overlap_days=overlap_days, max_catchup_days=max_catchup_days)


def reference(path):
    path = Path(path)
    with Root(path.parent) as fs:
        body = fs.read(path.name, MAX_BYTES)
    return dict(path=str(path), sha256=sha(body))


def read(ref):
    sealed.exact(ref, ("path", "sha256")); sealed.hash_value(ref["sha256"])
    path = Path(ref["path"])
    with Root(path.parent) as fs:
        body = fs.read(path.name, MAX_BYTES)
    require(sha(body) == ref["sha256"], "Pinned routine input changed")
    return decode(body)


def fresh(root, files):
    root = Path(root)
    with Root(root.parent) as parent:
        os.mkdir(root.name, 0o700, dir_fd=parent.fd)
    with Root(root) as fs:
        for name, value in files.items():
            fs.write_new(name, value if isinstance(value, bytes) else encode(value), MAX_BYTES)


def protect_output(output_root, refs, inventory):
    """No new output may be nested inside an original Journal/evidence input."""
    out = Path(output_root)
    for ref in refs.values():
        m = sealed.read_set(ref["path"], ref["sha256"], inventory)
        roots = [Path(ref["path"]).parent] + [Path(e["root"]) for e in m["seals"]]
        require(all(out != p and p not in out.parents for p in roots), "Output would alter preserved source evidence")


def protect_parent(output_root, parent_ref):
    source = Path(parent_ref["path"]).parent
    out = Path(output_root)
    require(out != source and source not in out.parents, "Fresh output outside preserved parent required")


def boundary(value):
    t = parse_utc(value)
    require(t.hour == 8 and t.minute == t.second == t.microsecond == 0,
            "Completed fixed-PST midnight boundary required")
    return t


def completed_boundary(as_of):
    return parse_utc(as_of).astimezone(PST).replace(hour=0, minute=0, second=0, microsecond=0)


def coverage(views, start, end):
    """One ordered disjoint interval implementation for coverage and planning."""
    cursor, hi = parse_utc(start), parse_utc(end)
    frontier, gaps, empty = cursor, [], []
    for v in views:
        a, b = map(parse_utc, (v["start"], v["end"]))
        require(cursor <= a < b <= hi, "Overlapping/out-of-scope routine views")
        if a > cursor:
            gaps.append(dict(start=format_utc(cursor), end=format_utc(a)))
        if not gaps:
            frontier = b
        if v["row_count"] == 0:
            empty.append(dict(start=v["start"], end=v["end"]))
        cursor = b
    if cursor < hi:
        gaps.append(dict(start=format_utc(cursor), end=format_utc(hi)))
    return dict(contiguous_complete_query_frontier=format_utc(frontier), gaps=gaps,
                queried_empty=empty)


def sources(refs, inventory):
    result = {}
    for key, ref in refs.items():
        require(key == ref["sha256"], "Seal-source key binding")
        verified = sealed.verify(ref["path"], manifest_sha256=key, inventory=inventory)
        result[key] = {r["task_id"]: r for r in verified["records"]}
    return result


def view(source, record, start=None, end=None):
    start, end = start or record["task"]["start"], end or record["task"]["end"]
    lo, hi = map(parse_utc, (start, end))
    require(parse_utc(record["task"]["start"]) <= lo < hi <= parse_utc(record["task"]["end"]),
            "View exceeds original complete seal")
    rows = [r for r in record["science_rows"] if lo <= parse_utc(r["t"]) < hi]
    return dict(source=source, task_id=record["task_id"], start=start, end=end,
                row_count=len(rows), rows_sha256=digest(rows), quarantine=h.disposition(rows),
                original=record["compact_ref"])


def materialize(stream, original):
    rows = []
    for v in stream["views"]:
        r = original[v["source"]][v["task_id"]]
        require(r["task"]["identity"] == stream["identity"] and
                view(v["source"], r, v["start"], v["end"]) == v, "Routine view/provenance changed")
        rows.extend(x for x in r["science_rows"] if
                    parse_utc(v["start"]) <= parse_utc(x["t"]) < parse_utc(v["end"]))
    require(len(rows) <= sealed.MAX_ROWS, "Per-stream routine row bound")
    return rows


def load(ref, inventory):
    g = read(ref)
    require(g["schema_version"] == VERSION and g["quality_policy"] == quality.binding() and
            g["generation_id"] == digest({k:v for k,v in g.items() if k != "generation_id"}) and
            0 < len(g["streams"]) <= 1024 and
            sum(len(s["views"]) for s in g["streams"].values()) <= MAX_VIEWS,
            "Routine generation integrity/bounds")
    original = sources(g["sources"], inventory)
    for sid, s in g["streams"].items():
        require(s["identity"] == inventory.identity(sid), "Frozen routine identity")
        materialize(s, original)
        require(s["coverage"] == coverage(s["views"], s["start"], s["end"]), "Routine coverage changed")
    return g, original


def save(g, root):
    require(sum(len(s["views"]) for s in g["streams"].values()) <= MAX_VIEWS,
            "Routine view capacity; separate reviewed compaction required")
    g["generation_id"] = digest(g)
    fresh(root, {"generation.json": g})
    return reference(Path(root)/"generation.json")


def prepared(ref, inventory):
    from .browser_projection import load_prepared
    load_prepared(ref["root"], pins=ref["pins"],
                  prepared_fingerprint=ref["fingerprint"])
    with Root(ref["root"]) as fs:
        handoff=decode(fs.read("handoff.json", MAX_BYTES))
        require(all(s["identity"] == inventory.identity(s["identity"]["stream_id"]) for s in handoff["streams"]),
                "Prepared frozen identity changed")
        return handoff, decode(fs.read("daily-output.json", MAX_BYTES))


def initialize(seal_ref, daily_ref, *, inventory, output_root):
    """Explicit verified seed; never acknowledges publication or imports data."""
    protect_output(output_root,{seal_ref["sha256"]:seal_ref},inventory)
    original = sources({seal_ref["sha256"]: seal_ref}, inventory)
    handoff, daily = prepared(daily_ref, inventory)
    require(handoff["evidence_binding"]["evidence_manifest_sha256"] == seal_ref["sha256"],
            "Daily preparation and source seal set differ")
    streams = {}
    for s in handoff["streams"]:
        sid = s["identity"]["stream_id"]
        rr = [r for r in original[seal_ref["sha256"]].values() if r["task"]["identity"] == s["identity"]]
        require(rr and sha(h.csv_bytes([x for r in rr for x in r["science_rows"]], s["identity"],
                                      rr[0]["decision"]["scale"])) == s["csv"]["sha256"],
                "Daily native source binding differs")
        vv = [view(seal_ref["sha256"], r) for r in rr]
        streams[sid] = dict(identity=s["identity"], start=vv[0]["start"], end=vv[-1]["end"], views=vv,
            coverage=coverage(vv, vv[0]["start"], vv[-1]["end"]), outcome="VERIFIED_SEED",
            last_attempted_source_check=None, last_successful_complete_query=vv[-1]["original"],
            latest_eligible_instantaneous_timestamp=None, last_acknowledged_publication_receipt=None,
            last_prepared_candidate=daily_ref, last_routine_generation=None)
    return save(dict(schema_version=VERSION, parent=None, cycle=None, sources={seal_ref["sha256"]:seal_ref},
        quality_policy=quality.binding(), streams=streams, daily=daily_ref, changes={},
        publication_eligible=False), output_root)


def plan(parent_ref, *, inventory, target, as_of, selected_ids, output_root, overlap_policy=None):
    g, _ = load(parent_ref, inventory)
    protect_parent(output_root,parent_ref)
    protect_output(output_root,g["sources"],inventory)
    require(all(s["last_prepared_candidate"] is not None for s in g["streams"].values()),
            "Prepare the parent candidate before planning another cycle")
    p = overlap_policy or policy()
    require(p == policy(overlap_days=p["overlap_days"], max_catchup_days=p["max_catchup_days"]), "Overlap policy")
    end = boundary(target)
    require(end <= completed_boundary(as_of), "Current incomplete day excluded")
    require(selected_ids and len(set(selected_ids)) == len(selected_ids), "Exact distinct routine selection")
    work = {}
    for sid in selected_ids:
        inventory.identity(sid)
        if sid not in g["streams"]:
            work[sid] = dict(status="EXPLICIT_BOOTSTRAP_REQUIRED", intervals=[])
            continue
        s = g["streams"][sid]
        require(parse_utc(s["end"]) <= end, "Routine cannot shrink authorized scope")
        lo = max(parse_utc(s["start"]), parse_utc(s["coverage"]["contiguous_complete_query_frontier"]) -
                 timedelta(days=p["overlap_days"]))
        if end-lo > timedelta(days=p["max_catchup_days"]):
            work[sid] = dict(status="EXPLICIT_CATCHUP_APPROVAL_REQUIRED", intervals=[])
            continue
        cuts = []
        cursor = lo
        while cursor < end:
            stop = min(end, cursor+timedelta(days=30))
            cuts.append(dict(start=format_utc(cursor), end=format_utc(stop))); cursor=stop
        work[sid] = dict(status="PLANNED", intervals=cuts, identity=s["identity"],
                         start=format_utc(lo), end=format_utc(end))
    require(sum(len(w["intervals"]) for w in work.values()) <= 128, "Prepared-task ceiling; select a bounded tranche")
    c = dict(schema_version=CYCLE, parent=parent_ref, parent_generation=g["generation_id"],
             source_fingerprint=digest(source_binding()), policy=p, target=format_utc(end),
             planned_at=format_utc(parse_utc(as_of)), work=work, execution_authorized=False)
    c["cycle_id"] = digest(c)
    fresh(output_root, {"cycle.json":c})
    return reference(Path(output_root)/"cycle.json")


def resume(cycle_ref, inventory):
    """Reopen exactly the saved intent, not a newly calculated seven-day window."""
    c = read(cycle_ref)
    require(c["schema_version"] == CYCLE and c["source_fingerprint"] == digest(source_binding()) and
            c["cycle_id"] == digest({k:v for k,v in c.items() if k != "cycle_id"}), "Cycle/source binding changed")
    g, original = load(c["parent"], inventory)
    require(g["generation_id"] == c["parent_generation"], "Routine parent changed")
    return c, g, original


def assemble(cycle_ref, replacements, *, inventory, output_root, attempt_refs=None):
    """One all-or-nothing replacement per stream; failed streams retain parent."""
    c, parent, original = resume(cycle_ref, inventory)
    protect_parent(output_root,cycle_ref);protect_parent(output_root,c["parent"])
    protect_output(output_root,parent["sources"],inventory)
    require(set(replacements) <= set(c["work"]), "Unplanned replacement stream")
    checks = inspect_attempts(c, attempt_refs or {}, inventory)
    g = copy.deepcopy(parent); g.pop("generation_id")
    g.update(parent=c["parent"], cycle=cycle_ref, changes={})
    for sid, w in c["work"].items():
        if sid not in g["streams"]:
            continue
        s = g["streams"][sid]
        if sid in checks:
            s["last_attempted_source_check"] = checks[sid]["last_attempted_at"]
            s["attempt_evidence"] = checks[sid]
        s["last_routine_generation"] = parent["generation_id"]
        if w["status"] != "PLANNED" or sid not in replacements:
            s["outcome"] = "KEEP_PRIOR_ACKNOWLEDGED_HISTORY"
            if w["status"] == "PLANNED":
                s.update(end=w["end"], coverage=coverage(s["views"],s["start"],w["end"]))
            continue
        ref = replacements[sid]
        try:
            protect_output(output_root,{ref["sha256"]:ref},inventory)
            new = sources({ref["sha256"]:ref}, inventory)[ref["sha256"]]
            rr = list(new.values())
            require(rr and all(r["task"]["identity"] == s["identity"] for r in rr), "Replacement identity")
            require([(r["task"]["start"], r["task"]["end"]) for r in rr] ==
                    [(i["start"], i["end"]) for i in w["intervals"]], "Exact complete replacement interval set")
            old_record = original[s["views"][0]["source"]][s["views"][0]["task_id"]]
            require(all(sealed.scale_semantics(r["decision"]["scale"]) == sealed.scale_semantics(old_record["decision"]["scale"]) and
                        r["decision"]["configuration_sha256"] == old_record["decision"]["configuration_sha256"] and
                        r["decision"]["scientific_sha256"] == old_record["decision"]["scientific_sha256"] for r in rr),
                    "Replacement configuration/science changed; explicit review required")
            require(all(r["compact_ref"]["source_fingerprint"] == c["source_fingerprint"] for r in rr),
                    "Replacement not acquired under current cycle source")
        except (Hold, OSError, KeyError, TypeError, ValueError):
            # Do not expose provider text or partially incorporate a verified prefix.
            s["outcome"] = "KEEP_PRIOR_ACKNOWLEDGED_HISTORY"
            s.update(end=w["end"], coverage=coverage(s["views"],s["start"],w["end"]))
            g["changes"][sid] = dict(status="REPLACEMENT_NOT_ADMITTED")
            continue
        retained = []
        lo, hi = map(parse_utc, (w["start"], w["end"]))
        for v in s["views"]:
            a,b = map(parse_utc, (v["start"], v["end"]))
            r = original[v["source"]][v["task_id"]]
            if b <= lo or a >= hi:
                retained.append(v)
            else:
                if a < lo: retained.append(view(v["source"], r, v["start"], w["start"]))
                if b > hi: retained.append(view(v["source"], r, w["end"], v["end"]))
        vv = sorted(retained + [view(ref["sha256"], r) for r in rr], key=lambda v:v["start"])
        g["sources"][ref["sha256"]] = ref
        g["changes"][sid] = dict(status="REPLACED", start=w["start"], end=w["end"],
            parent_views_sha256=digest(s["views"]), replacement=ref, retained_views_sha256=digest(retained))
        s.update(views=vv, end=w["end"], coverage=coverage(vv, s["start"], w["end"]),
                 outcome="STREAM_UPDATE_SUCCESS", last_successful_complete_query=vv[-1]["original"],
                 last_attempted_source_check=max(r["compact_ref"]["retrieval_last_utc"] for r in rr),
                 last_prepared_candidate=None)
    return save(g, output_root)


def inspect_attempts(cycle, refs, inventory):
    """Optional failed/incomplete attempt state comes only from the real Journal."""
    from .recovery import open_evidence
    require(set(refs) <= set(cycle["work"]), "Unplanned attempt evidence")
    result={}
    for sid,entries in refs.items():
        require(type(entries) is list and 0 < len(entries) <= 128, "Bounded Journal references")
        records=[];times=[]
        for e in entries:
            sealed.exact(e, ("root","campaign_id","header_sha256","task_id"))
            with open_evidence(e["root"],e["campaign_id"],inventory) as j:
                require(j.header_sha == e["header_sha256"] and
                        digest(j.binding["collector_sources"]) == cycle["source_fingerprint"], "Attempt source binding")
                h._binding(j.binding,j.tasks,inventory,cycle["source_fingerprint"]);j.verify_records()
                t=j.tasks[e["task_id"]]
                require(t["identity"] == cycle["work"][sid]["identity"] and
                        dict(start=t["start"],end=t["end"]) in cycle["work"][sid]["intervals"], "Attempt scope mismatch")
                attempts=[a for a in j.snapshot()["attempts"].values() if a["interval_key"] == e["task_id"]]
                times.extend(a["reserved_at"] for a in attempts)
                records.append(dict(reference=e,attempts_sha256=digest(attempts),spent_attempts=len(attempts)))
        result[sid]=dict(last_attempted_at=max(times) if times else None, journals=records)
    return result


def continue_assembly(cycle_ref, previous_ref, replacements, *, inventory, output_root):
    """Reuse already validated successful streams, without resetting any Journal."""
    previous, _ = load(previous_ref, inventory)
    require(previous["cycle"] == cycle_ref, "Different interrupted cycle")
    accepted = {sid:c["replacement"] for sid,c in previous["changes"].items() if c["status"] == "REPLACED"}
    require(not (set(accepted) & set(replacements)), "Completed stream replacement already owned")
    return assemble(cycle_ref, dict(accepted, **replacements), inventory=inventory, output_root=output_root)


def prepare_campaigns(cycle_ref, bundles, *, inventory, now):
    """Normal committed executor preparation; no reservation, Journal or dispatch."""
    from . import campaign, campaign_execution
    c, _, _ = resume(cycle_ref, inventory)
    result = []
    for sid,w in c["work"].items():
        if w["status"] != "PLANNED": continue
        require(sid in bundles, "Fresh reviewed authority required before executor preparation")
        for n,i in enumerate(w["intervals"]):
            manifest = campaign.make_campaign(inventory, campaign_id="routine-"+c["cycle_id"][:24]+"-"+sid+"-"+str(n),
                executor_fingerprint=c["source_fingerprint"], horizons={sid:i}, chunk_days=30,
                decisions={sid:bundles[sid]["decision"]}, budgets=campaign.policy(logical_requests=3,
                attempts=3,total_bytes=25165824,wall_seconds=600))
            binding,tasks = campaign_execution.prepare(manifest,inventory,{sid:bundles[sid]},now=now)
            require([(t["start"],t["end"]) for t in tasks.values()] == [(i["start"],i["end"])],
                    "Routine interval needs separate configuration-aware planning")
            result.append(dict(manifest=manifest,binding=binding,tasks=tasks))
    return result


def interval(v):
    return dict(task_id=v["task_id"], start=v["start"], end=v["end"],
        query_state="COMPLETE_NONEMPTY" if v["row_count"] else "COVERED_EMPTY",
        row_count=v["row_count"], **{k:v["original"][k] for k in
        ("seal_record_sha256", "content_sha256", "parsed_sha256")})


def validate_lineage(value, specification, handoff):
    """Validate the private view envelope before the unchanged public projection."""
    require(value["schema_version"] == LINEAGE and value["identity"] == specification["identity"] and
            value["lineage_sha256"] == digest({k:v for k,v in value.items() if k != "lineage_sha256"}) and
            value["generation_sha256"] == handoff["evidence_binding"]["evidence_manifest_sha256"] and
            value["native_sha256"] == specification["csv"]["sha256"] and
            value["quarantine"] == specification["quarantine"], "Routine private lineage binding")
    vv = value["views"]
    require(vv and len(vv) <= MAX_VIEWS and specification["intervals"] == [interval(v) for v in vv],
            "Routine interval views differ")
    coverage(vv, vv[0]["start"], vv[-1]["end"])
    for v in vv:
        r = v["original"]
        require(parse_utc(r["start"]) <= parse_utc(v["start"]) < parse_utc(v["end"]) <= parse_utc(r["end"]),
                "View exceeds source seal")
        for key in ("seal_record_sha256", "content_sha256", "parsed_sha256", "source_fingerprint", "receipt_set_sha256"):
            sealed.hash_value(r[key])
        require(type(v["row_count"]) is int and 0 <= v["row_count"] <= r["row_count"], "View row count")
    refs = [v["original"] for v in vv]
    require(value["review_set_sha256"] == digest([r["review_sha256"] for r in refs]) and
            value["scale_set_sha256"] == digest([r["scale_sha256"] for r in refs]), "View review/scale binding")
    return refs


def prepare(generation_ref, *, inventory, output_root, as_of, rscript="Rscript"):
    """Only affected completed days; use pinned parent cadence and original seals."""
    g, original = load(generation_ref, inventory)
    protect_parent(output_root,generation_ref)
    protect_output(output_root,g["sources"],inventory)
    require(all(parse_utc(c["end"]) <= completed_boundary(as_of) for c in g["changes"].values()
                if c["status"] == "REPLACED"), "Routine target has not completed at preparation time")
    old_h, old_daily = prepared(g["daily"], inventory)
    require(parse_utc(as_of) >= parse_utc(old_h["as_of"]), "Routine time cannot roll back")
    specs, files = [], {"prior-daily.json":encode(old_daily)}
    for sid, s in sorted(g["streams"].items()):
        rows = materialize(s, original)
        rr = [original[v["source"]][v["task_id"]] for v in s["views"]]
        refs = [v["original"] for v in s["views"]]
        lineage = dict(schema_version=LINEAGE, identity=s["identity"], views=s["views"],
            generation_sha256=generation_ref["sha256"], quarantine=h.disposition(rows),
            review_set_sha256=digest([r["review_sha256"] for r in refs]),
            scale_set_sha256=digest([r["scale_sha256"] for r in refs]))
        old = next(x for x in old_h["streams"] if x["identity"] == s["identity"])
        spec = copy.deepcopy(old)
        spec.update(intervals=[interval(v) for v in s["views"]],
            quarantine=lineage["quarantine"], historical_terminal=h.historical_terminal(s["identity"], rows,
            rr[0]["decision"]["scale"], as_of=as_of))
        change = g["changes"].get(sid,{})
        dates = set()
        if change.get("status") == "REPLACED":
            day = parse_utc(change["start"]).astimezone(PST).date()
            stop = parse_utc(change["end"]).astimezone(PST)
            while datetime.combine(day, datetime.min.time(), PST) < stop:
                if datetime.combine(day+timedelta(days=1), datetime.min.time(), PST) <= completed_boundary(as_of):
                    dates.add(day.isoformat())
                day += timedelta(days=1)
            # Previous observed cadence is a dependency of the next observed day.
            following = sorted({parse_utc(r["t"]).astimezone(PST).date().isoformat() for r in rows}
                               - dates)
            after = [d for d in following if dates and d > max(dates) and
                     d < completed_boundary(as_of).date().isoformat()]
            if after: dates.add(after[0])
        # Transfer only changed days and their cadence dependency, never copy the
        # entire historical CSV merely to prepare a short routine overlap.
        observed_days = sorted({parse_utc(row["t"]).astimezone(PST).date().isoformat() for row in rows})
        native_days = set(dates)
        for day in dates:
            earlier = [d for d in observed_days if d < day]
            if earlier: native_days.add(earlier[-1])
        native = [row for row in rows if parse_utc(row["t"]).astimezone(PST).date().isoformat() in native_days]
        csv = h.csv_bytes(native,s["identity"],rr[0]["decision"]["scale"])
        lineage.update(native_sha256=sha(csv), native_input_days=sorted(native_days),
                       native_input_scope="affected_days_plus_preceding_observed_day")
        lineage["lineage_sha256"] = digest(lineage)
        lc=encode(lineage);cp,lp="native/"+sid+".csv","lineage/"+sid+".json"
        files[cp],files[lp]=csv,lc
        spec.update(csv=dict(path=cp,bytes=len(csv),sha256=sha(csv)),
                    lineage=dict(path=lp,bytes=len(lc),sha256=sha(lc)),native_view_rows=len(rows))
        spec["recompute_dates"] = sorted(dates)
        specs.append(spec)
    package = Path(__file__).parent
    science = dict(core_sha256=sha((package.parent/"core.R").read_bytes()),
        wrapper_sha256=sha((package/"daily_prepare.R").read_bytes()), collector_fingerprint=digest(source_binding()))
    ev = dict(evidence_manifest_sha256=generation_ref["sha256"],
        acquisition_fingerprint=digest([r["source_fingerprint"] for s in specs for r in [v["original"] for v in g["streams"][s["identity"]["stream_id"]]["views"]]]),
        execution_binding_sha256=digest(g["sources"]), task_binding_sha256=digest([s["intervals"] for s in specs]))
    handoff = dict(schema_version=HANDOFF, as_of=format_utc(parse_utc(as_of)), source_scope="historical_sealed_intervals",
        cadence_mode="routine", cadence_policy="reuse_pinned_parent_context", science_binding=science,
        evidence_binding=ev, streams=specs, latest_instantaneous=[], publication_eligible=False, reference_band="not_computed",
        prior_daily=dict(path="prior-daily.json",sha256=sha(files["prior-daily.json"])))
    files["handoff.json"] = handoff
    fresh(output_root,files)
    command=[rscript,"--vanilla",str(package/"daily_prepare.R"),str(Path(output_root)/"handoff.json"),str(Path(output_root)/"daily-output.json")]
    began=time.monotonic()
    run=subprocess.run(command,capture_output=True,text=True,timeout=120,check=False)
    with Root(output_root) as fs:
        fs.write_new("r-receipt.json",encode(dict(exit_code=run.returncode,elapsed_seconds=time.monotonic()-began,
                     stdout=run.stdout[:8192],stderr=run.stderr[:8192])),32768)
        require(run.returncode == 0,"Routine R preparation failed; preserve prior")
        daily=decode(fs.read("daily-output.json",MAX_BYTES))
        fs.write_new("result.json",encode(dict(outcome="OFFLINE_DAILY_PREPARED",science_binding=science,
                     daily_output_sha256=digest(daily),publication_eligible=False)),MAX_BYTES)
    paths=["handoff.json","daily-output.json","result.json","r-receipt.json"]+[s["lineage"]["path"] for s in specs]
    pins=[dict(path=p,bytes=(Path(output_root)/p).stat().st_size,sha256=sha((Path(output_root)/p).read_bytes())) for p in paths]
    daily_ref=dict(root=str(output_root),pins=pins,fingerprint=science["collector_fingerprint"])
    prepared(daily_ref,inventory)
    return daily_ref


def checkpoint_preparation(generation_ref, daily_ref, *, inventory, output_root):
    """A prepared child is not a publication acknowledgement."""
    g,_=load(generation_ref,inventory); handoff,_=prepared(daily_ref,inventory)
    protect_parent(output_root,generation_ref)
    protect_output(output_root,g["sources"],inventory)
    require(handoff["evidence_binding"]["evidence_manifest_sha256"] == generation_ref["sha256"], "Wrong prepared generation")
    g.pop("generation_id"); g.update(parent=generation_ref,daily=daily_ref,changes={})
    for s in g["streams"].values():
        s["last_prepared_candidate"]=daily_ref
        s["last_routine_generation"]=generation_ref
    return save(g,output_root)


def latest_state(generation_ref, journal_ref, *, inventory, evaluated_at, output_root):
    """Separate private state snapshot; latest cannot alter a history generation."""
    from . import latest_observation, recovery
    g,_=load(generation_ref,inventory)
    sealed.exact(journal_ref,("root","campaign_id","header_sha256"))
    protect_parent(output_root,generation_ref)
    out,source=Path(output_root),Path(journal_ref["root"])
    require(out != source and source not in out.parents,"Latest evidence remains immutable")
    with recovery.open_evidence(source,journal_ref["campaign_id"],inventory) as j:
        require(j.header_sha == journal_ref["header_sha256"],"Latest Journal header changed")
        evidence=latest_observation.evidence(j,evaluated_at=evaluated_at)
        marker=latest_observation.project(j,evaluated_at=evaluated_at)
    sid=evidence["identity"]["stream_id"]
    require(sid in g["streams"] and evidence["identity"] == g["streams"][sid]["identity"] and
            evidence["history_coverage"] is False,"Separate exact latest identity required")
    snapshot=dict(schema_version="dendra-routine-state-snapshot-1",history=generation_ref,
        stream_id=sid,source_check=evidence["state"],latest_eligible_instantaneous_timestamp=
        evidence["state"]["latest_eligible_source_timestamp"],journal=journal_ref,
        latest_evidence_sha256=evidence["evidence_sha256"],latest_marker=marker,
        historical_coverage_changed=False,publication_acknowledgement=None,publication_allowed=False)
    fresh(output_root,{"state.json":snapshot})
    return reference(out/"state.json")
