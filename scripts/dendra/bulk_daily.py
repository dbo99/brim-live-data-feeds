#!/usr/bin/env python3
"""Offline historical website-bulk adapter over immutable R1 Parquet.

ReadytoUse admits an otherwise resolved historical bulk source to unchanged R
daily screens. Unavailable API q stays unknown. Actual API quality decisions,
known vetoes and protected R2 days retain their stronger evidence semantics.
No provider client, acquisition command, publisher or native rewriting.
"""
import argparse
import collections
import csv
from datetime import datetime, timedelta, timezone, date
import hashlib
import json
from pathlib import Path
import resource
import sqlite3
import subprocess
import sys
import tempfile
import time
from zoneinfo import ZoneInfo
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from dendra import bulk_csv_import as bulk
from dendra.history_acquisition import observation_quality as quality
from dendra.history_acquisition.safety import digest
from dendra.history_acquisition.recovery import open_evidence

VERSION = "dendra-bulk-daily-3"
BULK_POLICY = "dendra-historical-bulk-readytouse-1"
BULK_ROUTE = "DENDRA_WEBSITE_HISTORICAL_BULK_EXPORT"
BULK_BASIS = "PROVIDER_READY_TO_USE"
TIME_STATES = {"ACCEPTED_FIXED_UTC_MINUS_08", "CORROBORATED_FIXED_UTC_MINUS_08"}
QUALITY_ONLY_HOLDS = {"NO_COMPLETE_RETAINED_API_DAY", "NO_RETAINED_API_QUALITY_EVIDENCE",
                      "NO_MATCHED_API_QUALITY_EVIDENCE"}
TARGETS = set(bulk.CLASSES[:2])
MAX_SERIES_ROWS = 1500000
# Capacity guards, not source/scientific eligibility. Larger streams use bounded
# complete-day passes in R; a breached guard fails the candidate without clipping.
MAX_NATIVE_ROWS = 50000000
ADAPTER_BOUNDS = dict(whole_rows=MAX_SERIES_ROWS, batch_rows=65536,
                      day_rows=MAX_SERIES_ROWS, interval_bins=65536)
PST = timezone(timedelta(hours=-8))


def read_json(path, expected=None, limit=64*1024*1024):
    path = Path(path)
    bulk.require(path.stat().st_size <= limit, "Bounded evidence object required")
    body = path.read_bytes()
    if expected: bulk.require(hashlib.sha256(body).hexdigest() == expected, "Evidence checksum differs: " + str(path))
    return json.loads(body)


def load_bulk_metadata(path, checksum, catalog):
    """Resolve exact retained provider fields; names and values never infer purpose."""
    mapping = read_json(path, checksum)
    registry = mapping["evidence_registry_private"]
    documents = {key: read_json(v["internal_private_path"], v["sha256"])
                 for key, v in registry.items()}
    def resolve(locator):
        obj = documents[locator["evidence_key"]]
        for part in locator["json_pointer"].strip("/").split("/"):
            obj = obj[int(part)] if isinstance(obj, list) else obj[part]
        return obj
    annotations = {}
    for key, doc in documents.items():
        for i, obj in enumerate(doc.get("data", []) if isinstance(doc, dict) else []):
            if isinstance(obj, dict) and "title" in obj:
                annotations[obj["_id"]] = (obj, dict(evidence_key=key, json_pointer=f"/data/{i}"))
    slots = {(r["basename"], int(r["column_ordinal_1_based"])): r for r in mapping["rows"]}
    result = {}
    for row in catalog:
        slot = slots[(Path(row["relative_asset_path"]).name, int(row["column_ordinal_1_based"]))]
        bulk.require(slot["original_header"] == row["original_header"], "Metadata/export header differs")
        candidates = slot["candidate_metadata"]
        claims = []; suitable = True; vetoes = []; annotation_refs = []; locators = []
        for candidate in candidates:
            raw = resolve(candidate["metadata_locator"])
            bulk.require(raw["_id"] == candidate["stream_id"] and
                         raw["terms"] == candidate["source_parameter_terms"], "Provider metadata differs")
            claims.append(raw.get("terms", {}).get("dq", {}).get("Purpose"))
            locators.append(candidate["metadata_locator"])
            suitable &= (raw.get("is_enabled") is True and raw.get("state") == "ready" and
                         raw.get("source_type") == "sensor" and not candidate["configuration_action_evidence"])
            if len(candidates) == 1:
                bulk.require(raw["_id"] == row["proposed_stream_id"] and
                             raw["station_id"] == row["proposed_station_id"] and
                             raw["terms"].get("dt", {}).get("Unit") == row["native_unit"] and
                             raw["terms"] == json.loads(row["source_parameter_terms"]),
                             "Exact proposed station/stream/unit metadata differs")
                for aid in candidate["source_annotation_ids"]:
                    annotation, locator = annotations[aid]
                    actions = annotation.get("actions") or []
                    # Explicit exclusion or a quality claim is a veto, not calibration.
                    veto = any(a.get("exclude") is True or bool(a.get("flag")) or bool(a.get("attrib"))
                               for a in actions)
                    active = annotation.get("is_enabled") is True and annotation.get("state") == "approved"
                    intervals = annotation.get("intervals") or []
                    annotation_refs.append(dict(id=aid, locator=locator, state=annotation.get("state"),
                                                active_quality_veto=active and veto, intervals=intervals))
                    if active and veto:
                        for interval in intervals:
                            vetoes.append((utc(interval["begins_at"]) if interval.get("begins_at") else
                                           datetime.min.replace(tzinfo=timezone.utc),
                                           utc(interval["ends_before"]) if interval.get("ends_before") else
                                           datetime.max.replace(tzinfo=timezone.utc)))
        result[row["export_local_series_key"]] = dict(
            provider_purpose=claims[0] if claims and len(set(claims)) == 1 else "CONFLICTING",
            identity_ready=len(candidates) == 1 and slot["proposed_stream_id"] == row["proposed_stream_id"],
            source_suitable=suitable, metadata_locators=locators, annotation_references=annotation_refs,
            veto_intervals=vetoes)
    bulk.require(len(result) == len(catalog), "Incomplete bulk metadata closure")
    return result


def historical_bulk_admission(row, timestamp_status, source, route=BULK_ROUTE):
    """Source admission only; no depth predicate and no claim about observation q."""
    holds = []
    if route != BULK_ROUTE: holds.append("NOT_HISTORICAL_WEBSITE_BULK_ROUTE")
    recipe="csv-sha256:"+row["file_sha256"]+":column:"+str(row["column_ordinal_1_based"])
    if row["export_local_series_key"] != recipe: holds.append("EXPORT_LOCAL_IDENTITY_UNRESOLVED")
    if (not source["identity_ready"] or row["mapping_state"] not in
            {"CORROBORATED_PROPOSAL", "EXACT_REVIEWED_CAMP_FIXTURE"}): holds.append("IDENTITY_UNRESOLVED")
    if source["provider_purpose"] != "ReadytoUse": holds.append("PROVIDER_PURPOSE_NOT_READYTOUSE")
    resolved_unit=(row["product_class"]==bulk.CLASSES[0] and row["native_unit"]=="Percent" and
                   float(row["conversion_multiplier"] or 0)==1) or (
                   row["product_class"]==bulk.CLASSES[1] and row["native_unit"]=="VolumetricWaterContent" and
                   float(row["conversion_multiplier"] or 0)==100)
    if not resolved_unit: holds.append("UNIT_OR_TARGET_UNRESOLVED")
    if timestamp_status not in TIME_STATES: holds.append("TIMESTAMP_UNRESOLVED")
    if not source["source_suitable"]: holds.append("SOURCE_CONFIGURATION_UNSUITABLE")
    if row.get("duplicate_of"): holds.append("DUPLICATE_EXPORT_ALIAS")
    if not int(row["observation_count"]): holds.append("ALL_NULL_TARGET_NO_OBSERVATIONS")
    return dict(admitted=not holds, holds=holds, historical_source_route=route,
                provider_purpose=source["provider_purpose"], quality_admission_basis=BULK_BASIS,
                observation_api_q="UNAVAILABLE", policy=BULK_POLICY)


def apply_reviewed_bulk_time(times, review_path, checksum, catalog):
    """Apply the closed Pepperwood finding only to its exact original export hash."""
    review = read_json(review_path, checksum)
    bulk.require(review["result"] == "PEPPERWOOD_TIMESTAMP_CORROBORATED" and
                 review["timestamp_decision"]["classification"] == "CORROBORATED_FIXED_UTC_MINUS_08",
                 "Unreviewed bulk timestamp finding")
    for pin in review["checked_source_pins"]:
        bulk.require(bulk.sha(Path(pin["path"])) == pin["sha256"], "Timestamp review input changed")
    original = review["pepperwood_export"]
    selected = [r for r in catalog if r["file_sha256"] == original["sha256"]]
    bulk.require(len(selected) == original["unique_resolved_unit_vwc_traces"] == 61 and
                 all(Path(r["relative_asset_path"]).name == original["basename"] for r in selected),
                 "Timestamp review/export identity differs")
    for row in selected:
        times["files"][row["relative_asset_path"]].update(
            status="CORROBORATED_FIXED_UTC_MINUS_08", accepted_family=False,
            basis="Exact hash-bound reviewed Pepperwood shared-file clock; corroborated, not accepted")
    family = selected[0]["proposed_organization"]
    times["families"][family] = dict(status="CORROBORATED_FIXED_UTC_MINUS_08",
        sampled_streams=[], basis="Separate reviewed exact-export finding; no other export inherits")
    times["bulk_timestamp_review"] = dict(path=str(review_path), sha256=checksum,
        export_sha256=original["sha256"], qualification=review["timestamp_decision"])
    return times


def utc(value):
    x = datetime.fromisoformat(value.replace("Z", "+00:00"))
    bulk.require(x.tzinfo is not None, "Explicit API UTC time required")
    return x.astimezone(timezone.utc)


def naive(value):
    return utc(value).astimezone(PST).strftime("%Y-%m-%d %H:%M:%S")


def canonical_pair(stamp, value):
    return (stamp + "\t" + float(value).hex() + "\n").encode()


def exact_api_local(value):
    """Never round API subsecond identity into a second-resolution export match."""
    at=utc(value)
    return naive(value)+(f".{at.microsecond:06d}" if at.microsecond else "")


def native_rows(helper, path, start="", end=""):
    with subprocess.Popen([str(helper), "extract", str(path), start, end], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True) as proc:
        try:
            yield from csv.DictReader(proc.stdout)
            error = proc.stderr.read()
            bulk.require(proc.wait() == 0, "Native Parquet extraction failed: " + error)
        finally:
            if proc.poll() is None:
                proc.terminate(); proc.wait()


def production_references(status_path, checksum):
    status = read_json(status_path, checksum)
    result = []
    for asset in status["accounting"]["assets"]:
        with open_evidence(asset["root"], asset["campaign_id"], None) as journal:
            journal.verify_records(); state = journal.snapshot(); key = asset["task_id"]
            task = journal.tasks[key]; seal = state["intervals"][key]["complete"]
            bulk.require(seal == asset["seal"], "Persisted production seal changed")
            event = next(e for e in journal.events if e["kind"] == "sealed" and e["data"] == seal)
            folder = Path(asset["root"]) / journal.prefix
            objects = [dict(o, absolute_path=str(folder/o["path"]), role="normalized_archive") for o in seal["objects"]]
            for key2 in seal["attempt_keys"]:
                a = state["attempts"][key2]
                bulk.require(a["state"] == "received" and a["status"] == 200, "Failed attempt cannot supply coverage")
                objects.extend(dict(o, absolute_path=str(folder/o["path"]), role="provider_response") for o in a["objects"])
            seal_path = Path(asset["root"]) / journal._event_path(event["sequence"])
            result.append(dict(root=asset["root"], campaign_id=asset["campaign_id"], task_id=asset["task_id"],
                               stream_id=task["identity"]["stream_id"], station_id=task["identity"]["station_id"],
                               identity=task["identity"], start=task["start"], end=task["end"], state=seal["state"],
                               header_sha256=journal.header_sha, source_fingerprint=digest(journal.binding["collector_sources"]),
                               seal_sha256=event["record_sha256"], seal_file=dict(path=str(seal_path), sha256=bulk.sha(seal_path)), objects=objects))
    return result


def load_references(coverage_path, target_ids):
    summary = read_json(coverage_path)
    ref = summary["prior_reuse_index"]
    refs = read_json(ref["private_path"], ref["sha256"])["references"]
    for s in summary["production_status_sources"]:
        refs.extend(production_references(s["private_path"], s["sha256"]))
    out = collections.defaultdict(list); seen = set()
    for r in refs:
        key = (r["root"], r["campaign_id"], r["task_id"])
        bulk.require(key not in seen, "Duplicate retained task reference")
        seen.add(key)
        if r["stream_id"] in target_ids: out[r["stream_id"]].append(r)
    for group in out.values(): group.sort(key=lambda x: (x["start"], x["end"], x["task_id"]))
    return dict(out)


def verify_reference(ref, row):
    folder = Path(ref["root"])/"campaigns"/ref["campaign_id"]
    header = read_json(folder/"manifest.json", ref["header_sha256"])
    bulk.require(digest(header["binding"]["collector_sources"]) == ref["source_fingerprint"], "Historical source binding changed")
    identity = header["binding"]["roster"][ref["stream_id"]]
    bulk.require(identity["station_id"] == row["proposed_station_id"] and identity["stream_id"] == row["proposed_stream_id"]
                 and identity["native_unit"] == row["native_unit"], "Retained station/stream/unit differs")
    event = read_json(ref["seal_file"]["path"], ref["seal_file"]["sha256"])
    unsigned = {k:v for k,v in event.items() if k != "record_sha256"}
    bulk.require(digest(unsigned) == ref["seal_sha256"] == event["record_sha256"] and
                 event["header_sha256"] == ref["header_sha256"] and event["kind"] == "sealed" and
                 event["data"]["interval_key"] == ref["task_id"] and event["data"]["state"] == ref["state"], "Seal identity/state differs")
    norm = next(o for o in ref["objects"] if o["role"] == "normalized_archive")
    env = read_json(norm["absolute_path"], norm["sha256"])
    bulk.require(env["query_complete"] is True, "Incomplete archive cannot resolve full-day quality")
    interval = env["requested_interval"]
    bulk.require(interval.get("start", interval.get("start_inclusive")) == ref["start"] and
                 interval.get("end", interval.get("end_exclusive")) == ref["end"], "Archive interval differs")
    expected = {p.get("response_sha256", p.get("object", {}).get("sha256")) for p in env["pages"]}
    actual = {o["sha256"] for o in ref["objects"] if o["role"] == "provider_response"}
    # Prefix recoveries may retain an additional original page in the same CAS.
    missing = expected - actual
    raw = [o for o in ref["objects"] if o["role"] == "provider_response"]
    for h in missing:
        p = folder/"objects"/(h+".bin")
        bulk.require(p.is_file(), "Preserved prefix response unavailable")
        raw.append(dict(absolute_path=str(p), sha256=h, bytes=p.stat().st_size))
    bulk.require(actual <= expected, "Unbound response object")
    bulk.require(env.get("datastream_id", env.get("identity", {}).get("stream_id")) == ref["stream_id"], "Normalized archive stream differs")
    return raw


def select_timestamp_samples(rows, refs):
    by_family = collections.defaultdict(list)
    for r in rows:
        if r["proposed_stream_id"] in refs and r["materialized"] == "True": by_family[r["proposed_organization"]].append(r)
    selected = []
    for candidates in by_family.values():
        candidates.sort(key=lambda r: (r["minimum"] == r["maximum"], r["proposed_stream_id"] != bulk.CAMP,
                                       not bool(r["depth_cm"]), r["proposed_stream_id"]))
        picked=[]; files=set()
        for r in candidates:
            if r["relative_asset_path"] not in files and len(picked)<3:
                picked.append(r);files.add(r["relative_asset_path"])
        for r in candidates:
            if r not in picked and len(picked)<3:picked.append(r)
        selected.extend(picked)
    return selected


def timestamp_gate(helper, library, rows, refs):
    evidence=[]
    for row in select_timestamp_samples(rows, refs):
        group=refs[row["proposed_stream_id"]]; probes=[]
        # Three retained intervals per selected stream, at most nine observations.
        for i in sorted({0,len(group)//2,len(group)-1}):
            ref=group[i]; objects=verify_reference(ref,row)
            if not objects:continue
            payload=read_json(objects[0]["absolute_path"],objects[0]["sha256"])
            data=payload if isinstance(payload,list) else payload["data"]
            data=[(index,x) for index,x in enumerate(data) if type(x.get("v")) in (int,float)]
            for j in sorted({0,len(data)//2,len(data)-1}) if data else []:
                original_index,obs=data[j]; t=utc(obs["t"])
                probes.append(dict(t=obs["t"],v=obs["v"],fixed=naive(obs["t"]),
                    utc=t.strftime("%Y-%m-%d %H:%M:%S"),dst=t.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d %H:%M:%S"),
                    source=dict(path=objects[0]["absolute_path"],sha256=objects[0]["sha256"],row=original_index)))
        wanted={p[k] for p in probes for k in ("fixed","utc","dst")};found={}
        if wanted:
            for x in native_rows(helper,library/row["native_asset"],min(wanted),(datetime.fromisoformat(max(wanted))+timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")):
                if x["source_timestamp_naive"] in wanted:found[x["source_timestamp_naive"]]=float(x["exported_value"])
        counts={k:sum(found.get(p[k])==p["v"] for p in probes) for k in ("fixed","utc","dst")}
        strong=bool(probes) and len({p["v"] for p in probes})>=2 and counts["fixed"]==len(probes) and counts["utc"]<len(probes) and counts["dst"]<len(probes) and len({p["fixed"][:10] for p in probes})>=2
        state="ACCEPTED_FIXED_UTC_MINUS_08" if strong else "CORROBORATED_FIXED_UTC_MINUS_08" if probes and counts["fixed"]==len(probes) else "UNRESOLVED"
        evidence.append(dict(family=row["proposed_organization"],file=row["relative_asset_path"],stream_id=row["proposed_stream_id"],
                             status=state,samples=len(probes),matches=counts,probes=probes))
    families={}
    for family in sorted({r["proposed_organization"] for r in rows}):
        e=[x for x in evidence if x["family"]==family]
        state="ACCEPTED_FIXED_UTC_MINUS_08" if any(x["status"].startswith("ACCEPTED") for x in e) else "CORROBORATED_FIXED_UTC_MINUS_08" if any(x["status"].startswith("CORROBORATED") for x in e) else "UNRESOLVED"
        families[family]=dict(status=state,sampled_streams=[x["stream_id"] for x in e],
            basis="Direct retained API/native comparisons; no transfer to families without evidence")
    files={}
    for filename in sorted({r["relative_asset_path"] for r in rows}):
        e=[x for x in evidence if x["file"]==filename]
        family=next(r["proposed_organization"] for r in rows if r["relative_asset_path"]==filename)
        state="ACCEPTED_FIXED_UTC_MINUS_08" if any(x["status"].startswith("ACCEPTED") for x in e) else "CORROBORATED_FIXED_UTC_MINUS_08" if families[family]["status"]!="UNRESOLVED" else "UNRESOLVED"
        files[filename]=dict(status=state,family=family,accepted_family=families[family]["status"].startswith("ACCEPTED"),
            basis="Direct file comparison" if e else "Same retained export family/layout; source quality still requires exact-day match")
    return dict(version=VERSION,families=families,files=files,evidence=evidence)


def interval_union(refs):
    merged=[]
    for r in sorted(refs,key=lambda x:x["start"]):
        lo,hi=utc(r["start"]),utc(r["end"])
        if merged and lo<=merged[-1][1]:merged[-1]=(merged[-1][0],max(merged[-1][1],hi))
        else:merged.append((lo,hi))
    return merged


def complete_day(day, intervals):
    lo=datetime.combine(date.fromisoformat(day),datetime.min.time(),PST).astimezone(timezone.utc)
    return any(a<=lo and b>=lo+timedelta(days=1) for a,b in intervals)


def api_day_index(row, refs, scratch):
    """Disk-bounded union of exact retained original observations and q vetoes."""
    db=sqlite3.connect(scratch/"quality.sqlite")
    db.execute("PRAGMA cache_size=-8192")
    db.execute("CREATE TABLE obs(t TEXT PRIMARY KEY,v TEXT,q INTEGER,bad INTEGER,conflict INTEGER) WITHOUT ROWID")
    inserted=0; signatures={}; checked=[]
    sql=("INSERT INTO obs VALUES(?,?,?,?,0) ON CONFLICT(t) DO UPDATE SET "
         "q=max(q,excluded.q),bad=max(bad,excluded.bad),conflict=max(conflict,v!=excluded.v)")
    for ref in refs:
        objects=verify_reference(ref,row)
        checked.append(dict(task_id=ref["task_id"],seal_sha256=ref["seal_sha256"],objects=[o["sha256"] for o in objects]))
        for obj in objects:
            payload=read_json(obj["absolute_path"],obj["sha256"])
            data=payload if isinstance(payload,list) else payload["data"]
            batch=[]
            for obs in data:
                at=utc(obs["t"])
                bulk.require(utc(ref["start"])<=at<utc(ref["end"]),"Retained observation outside bound interval")
                sig=json.dumps(["q" in obs,obs.get("q")],sort_keys=True,separators=(",",":"))
                if sig not in signatures:
                    if len(signatures)>1024:signatures.clear()
                    signatures[sig]=quality.classify(obs)["quarantined"]
                v=obs.get("v");numeric=type(v) in (int,float)
                batch.append((exact_api_local(obs["t"]),float(v).hex() if numeric else "NON_NUMERIC",int(signatures[sig]),int(not numeric)))
            db.executemany(sql,batch);inserted+=len(batch)
        db.commit()
    days={};current=None;h=None
    for stamp,v,q,bad,conflict in db.execute("SELECT t,v,q,bad,conflict FROM obs ORDER BY t"):
        day=stamp[:10]
        if day!=current:
            if current:days[current]["sha256"]=h.hexdigest()
            current=day;h=hashlib.sha256();days[day]=dict(rows=0,quarantined=0,nonnumeric=0,conflicts=0)
        x=days[day];x["rows"]+=1;x["quarantined"]+=q;x["nonnumeric"]+=bad;x["conflicts"]+=conflict
        if not bad:h.update((stamp+"\t"+v+"\n").encode())
    if current:days[current]["sha256"]=h.hexdigest()
    db.close()
    return days,dict(refs=len(refs),original_occurrences=inserted,verified_references=checked,policy=quality.binding())


def quality_decision(day, native, api, intervals):
    complete=complete_day(day,intervals)
    if api and api["quarantined"]:
        return dict(state="QUARANTINED",reason="PROVIDER_QUALITY_UNREVIEWED",query_complete=complete)
    if not complete:return dict(state="UNRESOLVED",reason="NO_COMPLETE_RETAINED_API_DAY",query_complete=False)
    native=native or dict(rows=0,sha256=hashlib.sha256().hexdigest())
    api=api or dict(rows=0,sha256=hashlib.sha256().hexdigest(),nonnumeric=0,conflicts=0)
    if api["nonnumeric"] or api["conflicts"]:
        return dict(state="UNRESOLVED",reason="API_NULL_OR_CONFLICT_NOT_REPRESENTED_IN_NUMERIC_EXPORT",query_complete=True)
    if (native["rows"],native["sha256"])!=(api["rows"],api["sha256"]):
        return dict(state="UNRESOLVED",reason="CSV_API_OBSERVATION_SET_DIFFERS",query_complete=True)
    return dict(state="RESOLVED_CLEAR",reason="EXACT_DAY_MATCH_NO_PROVIDER_QUALITY_VETO",query_complete=True)


def bulk_quality_decision(day, native, api, intervals, source_veto=False, prior=None):
    """Separate bulk admission from unchanged actual API decisions and known vetoes."""
    decision = quality_decision(day, native, api, intervals)
    if prior is not None:
        if prior["daily_status"] != "UNRESOLVED_SEMANTICS":
            bulk.require((decision["state"], decision["reason"], decision["query_complete"]) ==
                         (prior["source_quality_status"], prior["source_quality_reason"], prior["query_complete"]),
                         "Protected R2 source-quality decision changed")
            return decision
        if prior["source_quality_reason"] not in QUALITY_ONLY_HOLDS:
            return decision  # Observation-set differences and other blockers are not released.
    if decision["state"] == "QUARANTINED": return decision
    if api and (api["nonnumeric"] or api["conflicts"]): return decision
    if decision["state"] == "RESOLVED_CLEAR": return decision
    if decision["reason"] not in QUALITY_ONLY_HOLDS: return decision
    if source_veto:
        return dict(state="QUARANTINED", reason="RETAINED_SOURCE_QUALITY_VETO",
                    query_complete=decision["query_complete"])
    return dict(state=BULK_BASIS, reason="API_Q_PARTIALLY_KNOWN_BULK_READYTOUSE" if api else
                "API_Q_UNAVAILABLE_BULK_READYTOUSE", query_complete=False)


def compare_r2_science(new, prior):
    """Exact protected-row regression; only quality-only unresolved days may transition."""
    old = {r["date"]: r for r in prior}; current = {r["date"]: r for r in new}
    bulk.require(old.keys() == current.keys(), "R2 daily date extent changed")
    protected = unchanged = 0; transitions = collections.Counter()
    for day, before in old.items():
        after = current[day]
        if before == after:
            unchanged += 1
        else:
            bulk.require(before["daily_status"] == "UNRESOLVED_SEMANTICS" and
                         before["source_quality_reason"] in QUALITY_ONLY_HOLDS and
                         after["daily_status"] in {"ACCEPTED", "WITHHELD_BY_EXISTING_SCREEN"},
                         "R2 protected value/status/diagnostics changed: " + day)
            transitions[before["daily_status"] + "->" + after["daily_status"]] += 1
        protected += before["daily_status"] != "UNRESOLVED_SEMANTICS"
    return dict(rows=len(prior), protected_rows=protected, protected_rows_unchanged=protected,
                unchanged_rows=unchanged, intended_transitions=dict(transitions), regressions=0)


def calendar_count(row, end):
    if not row["first_observed_timestamp_raw"]:return 0
    a=date.fromisoformat(row["first_observed_timestamp_raw"][:10]);b=min(date.fromisoformat(row["last_observed_timestamp_raw"][:10])+timedelta(days=1),end)
    return max(0,(b-a).days)


def build_series(helper,library,out,row,refs,as_of,source=None,prior_rows=None):
    tick=time.monotonic()
    end=min(date.fromisoformat(row["last_observed_timestamp_raw"][:10])+timedelta(days=1),utc(as_of).astimezone(PST).date())
    start=date.fromisoformat(row["first_observed_timestamp_raw"][:10])
    bulk.require(int(row["observation_count"])<=MAX_NATIVE_ROWS,"Explicit native input capacity exceeded")
    bulk.require(source is not None or int(row["observation_count"])<=MAX_SERIES_ROWS,
                 "Large-series path requires historical bulk admission")
    with tempfile.TemporaryDirectory(prefix="series-",dir=out/"scratch") as tmp:
        scratch=Path(tmp);api,proof=api_day_index(row,refs,scratch);intervals=interval_union(refs)
        hashes={};counts=collections.Counter();n=0;source_veto_days=set()
        ordered=hashlib.sha256();previous_stamp=None;previous_record=0
        first_stamp=last_stamp=None
        csvpath=scratch/"science.csv"
        with csvpath.open("w",newline="") as f:
            w=csv.writer(f);w.writerow(["datastream_id","t","v","value_status","duplicate_conflict","alternative_out_of_range"])
            for x in native_rows(helper,library/row["native_asset"]):
                stamp=x["source_timestamp_naive"];day=stamp[:10];v=float(x["exported_value"]);n+=1
                record=int(x["source_csv_record_1_based"])
                bulk.require((previous_stamp is None or stamp>=previous_stamp) and record>previous_record,
                             "Native source order differs")
                previous_stamp,previous_record=stamp,record
                first_stamp=first_stamp or stamp;last_stamp=stamp
                ordered.update(canonical_pair(stamp,v))
                hashes.setdefault(day,hashlib.sha256()).update(canonical_pair(stamp,v));counts[day]+=1
                t=(datetime.fromisoformat(stamp)+timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
                if source and source["veto_intervals"]:
                    at=utc(t)
                    if any(lo<=at<hi for lo,hi in source["veto_intervals"]):source_veto_days.add(day)
                w.writerow([row["proposed_stream_id"],t,x["exported_value"],"number",False,False])
        bulk.require(n==int(row["observation_count"]),"R1 native row count changed")
        decisions={};day=start;prior={r["date"]:r for r in prior_rows or []}
        while day<end:
            d=day.isoformat();native=dict(rows=counts[d],sha256=hashes[d].hexdigest()) if d in hashes else None
            decisions[d]=(bulk_quality_decision(d,native,api.get(d),intervals,d in source_veto_days,prior.get(d))
                          if source else quality_decision(d,native,api.get(d),intervals))
            day+=timedelta(days=1)
        cfg=out/"evidence"/(row["asset_id"]+".input.json")
        config=dict(version="dendra-bulk-daily-input-3" if source else "dendra-bulk-daily-input-2",core_sha256=bulk.sha(SCRIPTS/"dendra/core.R"),
            csv=str(csvpath),csv_sha256=bulk.sha(csvpath),stream_id=row["proposed_stream_id"],native_unit=row["native_unit"],
            multiplier=float(row["conversion_multiplier"]),start=start.isoformat(),end=end.isoformat(),as_of=as_of,quality_days=decisions)
        if source:
            config.update(historical_bulk_policy=BULK_POLICY,historical_source_route=BULK_ROUTE,
                          quality_admission_basis=BULK_BASIS)
            config.update(native_rows=n,adapter_bounds=ADAPTER_BOUNDS)
            proof.update(historical_bulk_policy=BULK_POLICY,historical_source_route=BULK_ROUTE,
                provider_purpose=source["provider_purpose"],quality_admission_basis=BULK_BASIS,
                provider_metadata_locators=source["metadata_locators"],
                source_annotation_references=source["annotation_references"],
                source_veto_observation_days=sorted(source_veto_days),
                observation_api_q="PARTIALLY_KNOWN" if refs else "UNAVAILABLE",
                day_quality_basis_counts=dict(collections.Counter(d["state"] for d in decisions.values())),
                known_API_quality_policy=quality.binding())
        bulk.write_json(cfg,config)
        science_path=out/"science"/(row["asset_id"]+".json")
        bulk.run(["Rscript","--vanilla",SCRIPTS/"dendra/bulk_daily.R",cfg,science_path])
        science=read_json(science_path)
        proof["resource_accounting"]=dict(native_rows=n,ordered_timestamp_value_sha256=ordered.hexdigest(),
            first_source_timestamp_naive=first_stamp,last_source_timestamp_naive=last_stamp,
            native_day_rows=dict(counts),adapter_bounds=ADAPTER_BOUNDS,maximum_native_rows=MAX_NATIVE_ROWS,
            R=science.get("resource_accounting"),wall_seconds=time.monotonic()-tick,
            parent_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            children_peak_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
        if prior_rows is not None:
            proof["r2_regression"]=compare_r2_science(science["rows"],prior_rows)
        bulk.write_json(out/"evidence"/(row["asset_id"]+".proof.json"),proof)
    return science


def validate_daily(rows,sid):
    dates=set()
    for r in rows:
        d=date.fromisoformat(r["date"]);wy=d.year+(d.month>=10)
        aligned=date(1999 if d.month>=10 else 2000,d.month,d.day)
        bulk.require(r["date"] not in dates and r["stream_id"]==sid,"Mixed or duplicate daily identity/date")
        dates.add(r["date"])
        bulk.require(r["water_year"]==wy and r["dowy"]==(d-date(wy-1,10,1)).days+1 and
                     r["water_day_aligned"]==(aligned-date(1999,10,1)).days+1,"Daily calendar mismatch")
        accepted=r["daily_status"]=="ACCEPTED"
        bulk.require((r["daily_mean_vwc_percent"] is not None)==accepted,"Held/missing day has accepted value")
        if accepted:bulk.require(r["n_valid"]>0 and r["plot_eligible"] and
                                r["source_quality_status"] in {"RESOLVED_CLEAR",BULK_BASIS},"Invalid daily acceptance")


def contract_columns(row):
    """Add storage-contract names while retaining the frozen science diagnostics."""
    return dict(row,date_pst_fixed=row["date"],plot_day_aligned=row["water_day_aligned"],
                valid_sample_count=row["n_valid"],expected_sample_count=row["expected_samples"],
                sample_count_fraction=row["coverage_fraction"],first_last_span_fraction=row["temporal_span_fraction"])


def daily_table(helper,path,rows):
    """Explicit nullable numerical schema, including entirely withheld series."""
    numeric={'mean_native','mean_percent','mean_value','daily_mean_vwc_percent','depth_cm','conversion_multiplier',
             'expected_samples','cadence_seconds','coverage_fraction','temporal_span_fraction',
             'expected_sample_count','sample_count_fraction','first_last_span_fraction'}
    integers={'water_year','dowy','water_day','water_year_days','water_day_aligned','observation_count',
              'nominal_range_observations','plot_day_aligned','valid_sample_count'}
    booleans={'plot_eligible','query_complete','source_empty'}
    fields=list(dict.fromkeys(k for r in rows for k in r))
    bulk.write_csv(path,rows,fields)
    types=path.with_suffix('.types.csv')
    with types.open('w',newline='') as f:
        w=csv.writer(f)
        for k in fields:
            typ='double' if k in numeric else 'int64' if k in integers or k.startswith('n_') else 'bool' if k in booleans else 'string'
            w.writerow([k,typ])
    bulk.run([helper,'table',path,path.with_suffix('.parquet'),types])
    bulk.require(json.loads(bulk.run([helper,'readback',path.with_suffix('.parquet')]).stdout)['rows']==len(rows),'Daily Parquet readback differs')


def consumer_fixture(products,out,unknown_key=None,fixture_name="00G_CANDIDATE_FIXTURE.json"):
    choices={}
    for kind in ("known_percent","known_fraction","unknown_depth"):
        for row,daily in products:
            good=[r for r in daily if r["daily_status"]=="ACCEPTED"]
            depth=row["depth_cm"]
            ok=(kind=="known_percent" and depth and row["product_class"]==bulk.CLASSES[0]) or (kind=="known_fraction" and depth and row["product_class"]==bulk.CLASSES[1]) or (kind=="unknown_depth" and not depth)
            if kind=="unknown_depth" and unknown_key is not None:
                ok=ok and row["export_local_series_key"]==unknown_key
            if ok and good:
                choices[kind]=dict(station=row["proposed_station"],station_id=row["proposed_station_id"],
                    stream_id=row["proposed_stream_id"],export_local_series_key=row["export_local_series_key"],
                    depth_cm=float(depth) if depth else None,depth_status=row["depth_status"] if depth else "UNKNOWN",
                    orientation=row["orientation"] or None,equipment_type_id=row["equipment_type_id"] or None,
                    native_unit=row["native_unit"],conversion_multiplier=float(row["conversion_multiplier"]),
                    provider="Dendra",subprovider=row["proposed_organization"],source_file_sha256=row["file_sha256"],
                    native_asset=row["native_asset"],daily_asset="series/"+row["asset_id"]+".parquet",
                    timestamp_semantics="fixed UTC-08 completed days",records=good[:3])
                break
    schema=dict(version="dendra-00g-local-candidate-2",installed=False,publication_eligible=False,
                identity_key="export_local_series_key + exact stream_id; NULL depths never merge",
                date_field="date",value_field="daily_mean_vwc_percent",depth_nullable=True,
                daily_states=["ACCEPTED","WITHHELD_BY_EXISTING_SCREEN","MISSING","UNRESOLVED_SEMANTICS"],
                policy="dendra-daily-1.0.0-frozen-cadence",examples=choices)
    bulk.write_json(out/fixture_name,schema)
    # An exact, closed schema for the small producer proposal; no app installation.
    record={k:dict(type="integer") for k in (
        "water_year dowy water_day water_year_days water_day_aligned n_valid n_total n_null n_invalid n_missing "
        "n_duplicate_conflicts n_duplicate_rows n_out_of_range observation_count nominal_range_observations "
        "plot_day_aligned valid_sample_count").split()}
    record.update({k:dict(type=["number","null"]) for k in (
        "mean_native mean_percent mean_value daily_mean_vwc_percent expected_samples cadence_seconds "
        "coverage_fraction temporal_span_fraction depth_cm expected_sample_count sample_count_fraction "
        "first_last_span_fraction").split()})
    record.update({k:dict(type="boolean") for k in "plot_eligible query_complete source_empty".split()})
    record.update({k:dict(type="string") for k in (
        "date cadence_source daily_status source_quality_status source_quality_reason stream_id "
        "export_local_series_key station_id station_name depth_status native_unit processing_version date_pst_fixed").split()})
    record.update({k:dict(type=["string","null"]) for k in "accepted_stream_id latest_source_timestamp_utc".split()})
    record.update(flags=dict(type="array",items=dict(type="string")),conversion_multiplier=dict(type="number"))
    record["date"]["pattern"]=r"^\d{4}-\d{2}-\d{2}$"
    record["daily_status"]["enum"]=schema["daily_states"]
    series={k:dict(type="string") for k in (
        "station station_id stream_id export_local_series_key depth_status native_unit provider subprovider "
        "source_file_sha256 native_asset daily_asset timestamp_semantics").split()}
    series.update(depth_cm=dict(type=["number","null"]),conversion_multiplier=dict(type="number"),
        orientation=dict(type=["string","null"]),equipment_type_id=dict(type=["string","null"]),
        records=dict(type="array",minItems=1,maxItems=3,items=dict(type="object",additionalProperties=False,
            required=list(record),properties=record)))
    properties={k:dict(const=v) for k,v in schema.items() if k!="examples"}
    properties["examples"]=dict(type="object",additionalProperties=False,required=["known_percent","unknown_depth"],
        properties={k:dict(type="object",additionalProperties=False,required=list(series),properties=series)
                    for k in ("known_percent","known_fraction","unknown_depth")})
    bulk.write_json(out/"00G_CANDIDATE_SCHEMA.json",dict({"$schema":"https://json-schema.org/draft/2020-12/schema"},
        title="Local Dendra daily producer candidate 2",type="object",additionalProperties=False,
        required=list(properties),properties=properties))
    return schema


def read_daily_csv(path, schema):
    """Typed readback under the preserved candidate-2 record contract."""
    properties=schema["properties"]["examples"]["properties"]["unknown_depth"]["properties"]["records"]["items"]["properties"]
    with Path(path).open(newline="") as f:rows=list(csv.DictReader(f))
    for row in rows:
        bulk.require(set(row)==set(properties),"R2 daily field contract differs")
        for key,value in row.items():
            types=properties[key]["type"];types=[types] if isinstance(types,str) else types
            if value=="" and "null" in types:row[key]=None
            elif "boolean" in types:
                bulk.require(value in {"True","False"},"Invalid daily boolean");row[key]=value=="True"
            elif "integer" in types:row[key]=int(value)
            elif "number" in types:
                # Retain R2's integer spelling in number fields (e.g. zero/144).
                row[key]=int(value) if value.lstrip("-").isdigit() else float(value)
            elif "array" in types:row[key]=json.loads(value)
    return rows


def build(library,coverage,output,as_of,prior_daily,r2_baseline,mapping,mapping_sha256,
          timestamp_review,timestamp_review_sha256,impact_review,impact_review_sha256):
    tick=time.monotonic();bulk.require(not output.exists() and output.parent==library,"Fresh versioned daily subroot required")
    with (library/"SERIES_CATALOG.csv").open() as f:catalog=list(csv.DictReader(f))
    manifest=read_json(library/"NATIVE_VWC_ASSET_MANIFEST.json");native_assets={a["export_local_series_key"]:a for a in manifest["assets"]}
    pins={n:bulk.sha(library/n) for n in ("SERIES_CATALOG.csv","SERIES_CATALOG.parquet","NATIVE_VWC_ASSET_MANIFEST.json")}
    for a in native_assets.values():bulk.require(bulk.sha(library/a["path"])==a["sha256"],"R1 native asset hash differs")
    target=[r for r in catalog if r["product_class"] in TARGETS and r["materialized"]=="True"]
    bulk.require(len(catalog)==470 and len(target)==335,"R1 exact target closure differs")
    refs=load_references(coverage,{r["proposed_stream_id"] for r in target})
    baseline_manifest=read_json(r2_baseline/"DAILY_ASSET_MANIFEST.json")
    bulk.require(baseline_manifest["version"]=="dendra-bulk-daily-2" and
                 baseline_manifest["native_catalog_pins"]==pins and
                 baseline_manifest["coverage_summary"]["sha256"]==bulk.sha(coverage),"R2 baseline/input binding differs")
    baseline_result=read_json(r2_baseline/"DAILY_PRODUCT_RESULT.json")
    bulk.require(baseline_result["as_of"]==as_of,"Use the R2 cutoff for exact regression")
    baseline_assets={a["export_local_series_key"]:a for a in baseline_manifest["assets"]}
    for asset in baseline_assets.values():
        bulk.require(bulk.sha(r2_baseline/asset["path"])==asset["sha256"] and
                     bulk.sha(r2_baseline/asset["csv"]["path"])==asset["csv"]["sha256"],"R2 baseline asset changed")
    baseline_schema=read_json(r2_baseline/"00G_CANDIDATE_SCHEMA.json")
    baseline_catalog={r["export_local_series_key"]:r for r in csv.DictReader((r2_baseline/"DAILY_SERIES_CATALOG.csv").open())}
    sources=load_bulk_metadata(mapping,mapping_sha256,catalog)
    impact=read_json(impact_review,impact_review_sha256)
    bulk.require(impact["result"]=="BULK_READYTOUSE_POLICY_REVIEW_READY","Unreviewed bulk impact evidence")
    conflicts={impact["unknown_depth_multiplicity"]["conflict_excluded"]["id"]}
    output.mkdir()
    for name in ("series","science","evidence","scratch"): (output/name).mkdir()
    try:
        helper=bulk.compile_helper(output/"bulk-csv-arrow")
        times=apply_reviewed_bulk_time(read_json(r2_baseline/"TIMESTAMP_EVIDENCE.json"),
                                      timestamp_review,timestamp_review_sha256,catalog)
        bulk.write_json(output/"TIMESTAMP_EVIDENCE.json",times)
        print("TIMESTAMP_GATE "+json.dumps({k:v["status"] for k,v in times["families"].items()}),flush=True)
        daily_catalog=[];assets=[];qa=[];products=[];regressions=[];end=utc(as_of).astimezone(PST).date()
        for row in catalog:
            source=sources[row["export_local_series_key"]]
            status=times["files"][row["relative_asset_path"]]["status"]
            admission=historical_bulk_admission(row,status,source)
            group=refs.get(row["proposed_stream_id"],[])
            entry=dict(row);entry.update(depth_cm=float(row["depth_cm"]) if row["depth_cm"] else None,
                depth_status=row["depth_status"] if row["depth_cm"] else "CONFLICTING" if row["proposed_stream_id"] in conflicts else "UNKNOWN",
                timestamp_status=status,daily_asset=None,
                accepted_daily_rows=0,withheld_daily_rows=0,missing_daily_rows=0,unresolved_daily_candidates=0,
                eligibility="EXCLUDED_NON_TARGET",timestamp_evidence="TIMESTAMP_EVIDENCE.json",
                historical_source_route=BULK_ROUTE,provider_purpose=source["provider_purpose"],
                quality_admission_basis=BULK_BASIS if admission["admitted"] else None,
                observation_api_q="PARTIALLY_KNOWN" if group else "UNAVAILABLE",
                source_annotation_evidence="KNOWN_METADATA" if source["annotation_references"] else "NO_RETAINED_ANNOTATION_CLAIM",
                bulk_prescreen_holds=admission["holds"],historical_bulk_policy=BULK_POLICY)
            if row["product_class"] not in TARGETS:daily_catalog.append(entry);continue
            if row["duplicate_of"]:entry["eligibility"]="DUPLICATE_EXPORT_ALIAS";daily_catalog.append(entry);continue
            if not int(row["observation_count"]):entry["eligibility"]="ALL_NULL_TARGET_NO_OBSERVATIONS";daily_catalog.append(entry);continue
            if not calendar_count(row,end):entry["eligibility"]="NO_COMPLETED_NATIVE_DAYS";daily_catalog.append(entry);continue
            if not admission["admitted"]:
                entry["eligibility"]="UNRESOLVED_SEMANTICS"
                entry["hold_reason"]=admission["holds"][0]
                entry["unresolved_daily_candidates"]=calendar_count(row,end)
                daily_catalog.append(entry);continue
            old_asset=baseline_assets.get(row["export_local_series_key"])
            old_science=None;old_daily={}
            if old_asset:
                bulk.require(bulk.sha(r2_baseline/"science"/(row["asset_id"]+".json"))==old_asset["science_sha256"],"R2 science changed")
                old_science=read_json(r2_baseline/"science"/(row["asset_id"]+".json"))["rows"]
                old_daily={r["date"]:r for r in read_daily_csv(r2_baseline/old_asset["csv"]["path"],baseline_schema)}
            science=build_series(helper,library,output,row,group,as_of,source,old_science)
            old_by_date={r["date"]:r for r in old_science or []}
            daily=[]
            for r in science["rows"]:
                if r==old_by_date.get(r["date"]):
                    daily.append(old_daily[r["date"]]);continue
                r.update(stream_id=row["proposed_stream_id"],accepted_stream_id=row["accepted_stream_id"] or None,
                    export_local_series_key=row["export_local_series_key"],station_id=row["proposed_station_id"],station_name=row["proposed_station"],
                    depth_cm=entry["depth_cm"],depth_status=entry["depth_status"],native_unit=row["native_unit"],
                    conversion_multiplier=float(row["conversion_multiplier"]),processing_version=VERSION)
                daily.append(contract_columns(r))
            if old_asset:
                proof=read_json(output/"evidence"/(row["asset_id"]+".proof.json"))
                regression=dict(stream_id=row["proposed_stream_id"],export_local_series_key=row["export_local_series_key"],
                                **proof["r2_regression"])
                for r in daily:
                    before=old_daily[r["date"]]
                    if before["daily_status"]!="UNRESOLVED_SEMANTICS":
                        bulk.require(r==before,"Protected R2 consumer row changed")
                regressions.append(regression)
            validate_daily(daily,row["proposed_stream_id"])
            path=output/"series"/(row["asset_id"]+".csv");daily_table(helper,path,daily)
            counts=collections.Counter(r["daily_status"] for r in daily)
            entry.update(daily_asset=path.with_suffix(".parquet").relative_to(output).as_posix(),eligibility="LOCAL_DAILY_CANDIDATE",
                accepted_daily_rows=counts["ACCEPTED"],withheld_daily_rows=counts["WITHHELD_BY_EXISTING_SCREEN"],
                missing_daily_rows=counts["MISSING"],unresolved_daily_candidates=counts["UNRESOLVED_SEMANTICS"])
            native_source=native_assets[row["export_local_series_key"]]
            assets.append(dict(path=entry["daily_asset"],sha256=bulk.sha(path.with_suffix(".parquet")),bytes=path.with_suffix(".parquet").stat().st_size,
                csv=dict(path=path.relative_to(output).as_posix(),sha256=bulk.sha(path),bytes=path.stat().st_size),rows=len(daily),states=dict(counts),
                source_native=native_source,original_csv_sha256=row["file_sha256"],export_local_series_key=row["export_local_series_key"],
                stream_id=row["proposed_stream_id"],science_sha256=bulk.sha(output/"science"/(row["asset_id"]+".json")),
                input_sha256=bulk.sha(output/"evidence"/(row["asset_id"]+".input.json")),quality_proof_sha256=bulk.sha(output/"evidence"/(row["asset_id"]+".proof.json")),
                historical_source_route=BULK_ROUTE,quality_admission_basis=BULK_BASIS,
                provider_purpose=source["provider_purpose"],observation_api_q=entry["observation_api_q"]))
            qa.append(dict(export_local_series_key=row["export_local_series_key"],stream_id=row["proposed_stream_id"],
                observations_considered=sum(r["observation_count"] for r in daily),nominal_range_flagged=sum(r["nominal_range_observations"] for r in daily),
                provider_quality_withheld_observations=sum(r["observation_count"] for r in daily if r["source_quality_status"]=="QUARANTINED"),
                unresolved_observations=sum(r["observation_count"] for r in daily if r["daily_status"]=="UNRESOLVED_SEMANTICS"),
                observation_level_deletions=0,historical_source_route=BULK_ROUTE,provider_purpose=source["provider_purpose"],
                quality_admission_basis=BULK_BASIS,observation_api_q=entry["observation_api_q"],
                daily_quality_basis_counts=dict(collections.Counter(r["source_quality_status"] for r in daily)),**dict(counts)))
            # Retain only bounded examples in memory; full daily rows are durable files.
            fixture_row=dict(row,depth_status=entry["depth_status"])
            products.append((fixture_row,[r for r in daily if r["daily_status"]=="ACCEPTED"][:3]))
            daily_catalog.append(entry)
            print("DAILY "+row["proposed_stream_id"]+" "+json.dumps(dict(counts)),flush=True)
        bykey={r["export_local_series_key"]:r for r in daily_catalog}
        for r in daily_catalog:
            if r["duplicate_of"]:r["daily_asset"]=bykey[r["duplicate_of"]]["daily_asset"]
        bulk.table_parquet(helper,output/"DAILY_SERIES_CATALOG.csv",daily_catalog)
        bulk.write_csv(output/"DAILY_QA_SUMMARY.csv",qa)
        bulk.require(len(assets)==impact["model_b"]["enter_nonempty_daily_screen"],"Reviewed screen-entry ceiling differs; stop for review")
        unknown_counts=collections.Counter(r["proposed_station_id"] for r in catalog if r["product_class"] in TARGETS and
                                          not r["duplicate_of"] and not r["depth_cm"] and r["proposed_stream_id"] not in conflicts)
        unknown_products=[(r,d) for r,d in products if not r["depth_cm"] and r["proposed_stream_id"] not in conflicts and d]
        single=next((r["export_local_series_key"] for r,d in unknown_products if unknown_counts[r["proposed_station_id"]]==1),None)
        fixture=consumer_fixture(products,output,single)
        fixture_representatives=[]
        by_station=collections.defaultdict(list)
        for r,d in unknown_products:by_station[r["proposed_station_id"]].append((r,d))
        multiple=next((rs for rs in by_station.values() if len(rs)>=2),[])
        for r,d in multiple[:2]:
            name="00G_MULTI_UNKNOWN_"+r["asset_id"]+".json"
            consumer_fixture(products,output,r["export_local_series_key"],name)
            fixture_representatives.append(dict(path=name,station_id=r["proposed_station_id"],stream_id=r["proposed_stream_id"],
                                               export_local_series_key=r["export_local_series_key"],sha256=bulk.sha(output/name)))
        bulk.require(bulk.sha(output/"00G_CANDIDATE_SCHEMA.json")==bulk.sha(r2_baseline/"00G_CANDIDATE_SCHEMA.json"),
                     "Consumer schema changed; stop for 00G review")
        bulk.require(len(regressions)==len(baseline_assets),"Incomplete prior R2 regression closure")
        bulk.write_json(output/"R2_REGRESSION.json",dict(streams=regressions,regressions=0,
            intended_transitions=dict(sum((collections.Counter(r["intended_transitions"]) for r in regressions),collections.Counter())),
            protected_rows=sum(r["protected_rows"] for r in regressions),unchanged_rows=sum(r["unchanged_rows"] for r in regressions)))
        comparisons=[]
        for prior_path in prior_daily:
            old=read_json(prior_path);bulk.require(old["science_binding"]["core_sha256"]==bulk.sha(SCRIPTS/"dendra/core.R"),"Prior daily science differs")
            for sid,prior in old["rows"].items():
                a=next((a for a in assets if a["stream_id"]==sid),None)
                if not a:continue
                r=bykey[a["export_local_series_key"]];new=read_json(output/"science"/(r["asset_id"]+".json"))["rows"]
                # Compare numerical rows only where the current exact source/quality binding resolved.
                new=[x for x in new if x["source_quality_status"]=="RESOLVED_CLEAR"]
                c=bulk.compare_daily(new,prior)
                if c["overlap_days"]:comparisons.append(dict(stream_id=sid,prior_path=str(prior_path),prior_sha256=bulk.sha(prior_path),comparison=c))
        bulk.write_json(output/"DAILY_COMPARISONS.json",comparisons)
        camp=[c for c in comparisons if c["stream_id"]==bulk.CAMP and c["comparison"]["overlap_days"]==15]
        bulk.require(any(c["comparison"]["exact_matches"]==15 and c["comparison"]["differences"]==0 for c in camp),"Camp Cady 15/15 regression")
        source={p.name:bulk.sha(p) for p in [Path(__file__),Path(__file__).with_suffix('.R'),SCRIPTS/"dendra/core.R",SCRIPTS/"dendra/bulk_csv_arrow.cpp"]}
        bulk.write_json(output/"DAILY_ASSET_MANIFEST.json",dict(version=VERSION,as_of=as_of,completed_end_exclusive=end.isoformat(),
            native_library_root=str(library),native_catalog_pins=pins,source_sha256=source,assets=assets,
            policy=quality.binding(),daily_policy="dendra-daily-1.0.0-frozen-cadence",publication_eligible=False,
            historical_bulk_policy=BULK_POLICY,historical_source_route=BULK_ROUTE,
            candidate_version="dendra-00g-local-candidate-3",consumer_format_version=fixture["version"],
            fixture_representatives=fixture_representatives,
            bulk_metadata_binding=dict(path=str(mapping),sha256=mapping_sha256),
            impact_review_binding=dict(path=str(impact_review),sha256=impact_review_sha256),
            r2_regression_binding=dict(root=str(r2_baseline),manifest_sha256=bulk.sha(r2_baseline/"DAILY_ASSET_MANIFEST.json")),
            timestamp_evidence_sha256=bulk.sha(output/"TIMESTAMP_EVIDENCE.json"),
            coverage_summary=dict(path=str(coverage),sha256=bulk.sha(coverage)),
            meaning="Local historical website-bulk candidate; ReadytoUse source admission plus unchanged daily science. API q unknown stays unavailable; actual API decisions and known vetoes remain distinct. No publication or whole-POR claim.",
            csv_layout="One daily CSV per exact export-local series under series/, same stem as Parquet; never concatenate by station/depth"))
        for name,pin in pins.items():bulk.require(bulk.sha(library/name)==pin,"Native catalog/manifest modified")
        for a in native_assets.values():bulk.require(bulk.sha(library/a["path"])==a["sha256"],"Native Parquet changed")
        inputs=read_json(library/"IMPORT_INPUT_BINDING.json")
        for a in inputs["originals"]:bulk.require(bulk.sha(Path(inputs["input_directory"])/a["basename"])==a["sha256"],"Original CSV changed")
        result=dict(result="READY_WITH_HOLDS",version=VERSION,as_of=as_of,output_root=str(output),
            importer_head=bulk.run(["git","rev-parse","HEAD"]).stdout.strip(),catalog_rows=len(daily_catalog),
            daily_asset_series=len(assets),series_with_accepted_days=sum(a["states"].get("ACCEPTED",0)>0 for a in assets),
            daily_rows=sum(a["rows"] for a in assets),parquet_bytes=sum(a["bytes"] for a in assets),csv_bytes=sum(a["csv"]["bytes"] for a in assets),
            daily_states={k:sum(a["states"].get(k,0) for a in assets) for k in ["ACCEPTED","WITHHELD_BY_EXISTING_SCREEN","MISSING","UNRESOLVED_SEMANTICS"]},
            unresolved_daily_candidates=sum(r["unresolved_daily_candidates"] for r in daily_catalog),
            unresolved_count_basis="Within each native first/last observed extent, clipped to completed cutoff; held families counted as naive-date candidates, not UTC coverage",
            timestamp_families=times["families"],unit_unresolved_held=sum(r["product_class"]==bulk.CLASSES[2] for r in catalog),
            target_observations=sum(a["rows"] for a in native_assets.values()),qa_totals={k:sum(x[k] for x in qa) for k in ['observations_considered','nominal_range_flagged','provider_quality_withheld_observations','unresolved_observations','observation_level_deletions']},
            camp_cady=camp,fixture_kinds=list(fixture['examples']),original_csv_writes=0,native_parquet_writes=0,provider_requests=0,
            historical_bulk_policy=BULK_POLICY,historical_source_route=BULK_ROUTE,
            admission=dict(resolved_unique_targets=sum(r["product_class"] in TARGETS and not r["duplicate_of"] for r in catalog),
                ready_to_use_unique_targets=sum(r["product_class"] in TARGETS and not r["duplicate_of"] and
                                                sources[r["export_local_series_key"]]["provider_purpose"]=="ReadytoUse" for r in catalog),
                screen_entry_traces=len(assets),new_screen_entry_traces=len(assets)-len(baseline_assets),
                all_null_targets=sum(r["product_class"] in TARGETS and not r["duplicate_of"] and not int(r["observation_count"]) for r in catalog),
                overlapping_prescreen_holds=dict(collections.Counter(h for r in daily_catalog if r["product_class"] in TARGETS and
                    not r["duplicate_of"] and not r["daily_asset"] for h in r["bulk_prescreen_holds"]))),
            quality_provenance=dict(provider_ready_to_use_admission_traces=len(assets),
                API_q_entirely_unavailable_traces=sum(a["observation_api_q"]=="UNAVAILABLE" for a in assets),
                API_q_partially_known_traces=sum(a["observation_api_q"]=="PARTIALLY_KNOWN" for a in assets),
                retained_API_day_quality_basis_traces=sum(x["daily_quality_basis_counts"].get("RESOLVED_CLEAR",0)>0 for x in qa),
                daily_quality_basis_counts=dict(sum((collections.Counter(x["daily_quality_basis_counts"]) for x in qa),collections.Counter())),
                source_annotation_metadata_traces=sum(bool(sources[a["export_local_series_key"]]["annotation_references"]) for a in assets)),
            r2_regression=dict(prior_streams_compared=len(regressions),regressions=0,
                unchanged_streams=sum(r["unchanged_rows"]==r["rows"] for r in regressions),
                protected_rows=sum(r["protected_rows"] for r in regressions),
                intended_transitions=dict(sum((collections.Counter(r["intended_transitions"]) for r in regressions),collections.Counter()))),
            consumer_schema_unchanged=True,fixture_representatives=fixture_representatives,
            performance=dict(wall_seconds=time.monotonic()-tick,parent_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                             children_peak_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,memory_note="macOS bytes; ordinary whole series or bounded complete-day R passes, disk-bounded API union"))
        bulk.write_json(output/"DAILY_PRODUCT_RESULT.json",result)
        with zipfile.ZipFile(output/"L03_DAILY_R3_REVIEW.zip","w",zipfile.ZIP_DEFLATED) as z:
            for n in ['DAILY_PRODUCT_RESULT.json','DAILY_SERIES_CATALOG.csv','DAILY_QA_SUMMARY.csv','TIMESTAMP_EVIDENCE.json','00G_CANDIDATE_FIXTURE.json','00G_CANDIDATE_SCHEMA.json','DAILY_COMPARISONS.json','DAILY_ASSET_MANIFEST.json','R2_REGRESSION.json']+[r['path'] for r in fixture_representatives]:
                z.write(output/n,n)
        bulk.require((output/"L03_DAILY_R3_REVIEW.zip").stat().st_size<=5*1024*1024,"Review package exceeds 5 MiB")
        return result
    except Exception as exc:
        bulk.write_json(output/"DAILY_HOLD.json",dict(error=str(exc),accepted=False,version=VERSION))
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('library','coverage-summary','output','as-of'):p.add_argument('--'+name,required=True)
    p.add_argument('--prior-daily',action='append',required=True)
    for name in ('r2-baseline','bulk-mapping','bulk-mapping-sha256','timestamp-review','timestamp-review-sha256',
                 'impact-review','impact-review-sha256'):p.add_argument('--'+name,required=True)
    a=p.parse_args();r=build(Path(a.library),Path(a.coverage_summary),Path(a.output),a.as_of,[Path(x) for x in a.prior_daily],
        Path(a.r2_baseline),Path(a.bulk_mapping),a.bulk_mapping_sha256,Path(a.timestamp_review),a.timestamp_review_sha256,
        Path(a.impact_review),a.impact_review_sha256)
    print(json.dumps({k:r[k] for k in ['result','daily_asset_series','series_with_accepted_days','daily_rows','daily_states']}))


if __name__=='__main__':main()
