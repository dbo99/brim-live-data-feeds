#!/usr/bin/env python3
"""Offline R2 daily adapter over immutable R1 Parquet and retained API evidence.

No provider client, acquisition command, or publisher. Missing CSV quality flags
are NOT treated as proof of absent API flags. Accepted daily values require a
complete matched retained API day, the existing quality policy, and core.R's
screen. Other dates/families remain explicit local holds. No native rewriting.
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

VERSION = "dendra-bulk-daily-2"
TARGETS = set(bulk.CLASSES[:2])
MAX_SERIES_ROWS = 1500000
PST = timezone(timedelta(hours=-8))


def read_json(path, expected=None, limit=64*1024*1024):
    path = Path(path)
    bulk.require(path.stat().st_size <= limit, "Bounded evidence object required")
    body = path.read_bytes()
    if expected: bulk.require(hashlib.sha256(body).hexdigest() == expected, "Evidence checksum differs: " + str(path))
    return json.loads(body)


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


def calendar_count(row, end):
    if not row["first_observed_timestamp_raw"]:return 0
    a=date.fromisoformat(row["first_observed_timestamp_raw"][:10]);b=min(date.fromisoformat(row["last_observed_timestamp_raw"][:10])+timedelta(days=1),end)
    return max(0,(b-a).days)


def build_series(helper,library,out,row,refs,as_of):
    end=min(date.fromisoformat(row["last_observed_timestamp_raw"][:10])+timedelta(days=1),utc(as_of).astimezone(PST).date())
    start=date.fromisoformat(row["first_observed_timestamp_raw"][:10])
    bulk.require(int(row["observation_count"])<=MAX_SERIES_ROWS,"Explicit per-series R row bound exceeded")
    with tempfile.TemporaryDirectory(prefix="series-",dir=out/"scratch") as tmp:
        scratch=Path(tmp);api,proof=api_day_index(row,refs,scratch);intervals=interval_union(refs)
        hashes={};counts=collections.Counter();n=0
        csvpath=scratch/"science.csv"
        with csvpath.open("w",newline="") as f:
            w=csv.writer(f);w.writerow(["datastream_id","t","v","value_status","duplicate_conflict","alternative_out_of_range"])
            for x in native_rows(helper,library/row["native_asset"]):
                stamp=x["source_timestamp_naive"];day=stamp[:10];v=float(x["exported_value"]);n+=1
                hashes.setdefault(day,hashlib.sha256()).update(canonical_pair(stamp,v));counts[day]+=1
                t=(datetime.fromisoformat(stamp)+timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
                w.writerow([row["proposed_stream_id"],t,x["exported_value"],"number",False,False])
        bulk.require(n==int(row["observation_count"]),"R1 native row count changed")
        decisions={};day=start
        while day<end:
            d=day.isoformat();native=dict(rows=counts[d],sha256=hashes[d].hexdigest()) if d in hashes else None
            decisions[d]=quality_decision(d,native,api.get(d),intervals);day+=timedelta(days=1)
        cfg=out/"evidence"/(row["asset_id"]+".input.json")
        bulk.write_json(cfg,dict(version="dendra-bulk-daily-input-2",core_sha256=bulk.sha(SCRIPTS/"dendra/core.R"),
            csv=str(csvpath),csv_sha256=bulk.sha(csvpath),stream_id=row["proposed_stream_id"],native_unit=row["native_unit"],
            multiplier=float(row["conversion_multiplier"]),start=start.isoformat(),end=end.isoformat(),as_of=as_of,quality_days=decisions))
        science_path=out/"science"/(row["asset_id"]+".json")
        bulk.run(["Rscript","--vanilla",SCRIPTS/"dendra/bulk_daily.R",cfg,science_path])
        science=read_json(science_path)
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
        if accepted:bulk.require(r["n_valid"]>0 and r["plot_eligible"] and r["source_quality_status"]=="RESOLVED_CLEAR","Invalid daily acceptance")


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


def consumer_fixture(products,out):
    choices={}
    for kind in ("known_percent","known_fraction","unknown_depth"):
        for row,daily in products:
            good=[r for r in daily if r["daily_status"]=="ACCEPTED"]
            depth=row["depth_cm"]
            ok=(kind=="known_percent" and depth and row["product_class"]==bulk.CLASSES[0]) or (kind=="known_fraction" and depth and row["product_class"]==bulk.CLASSES[1]) or (kind=="unknown_depth" and not depth)
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
    bulk.write_json(out/"00G_CANDIDATE_FIXTURE.json",schema)
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


def build(library,coverage,output,as_of,prior_daily):
    tick=time.monotonic();bulk.require(not output.exists() and output.parent==library,"Fresh versioned daily subroot required")
    with (library/"SERIES_CATALOG.csv").open() as f:catalog=list(csv.DictReader(f))
    manifest=read_json(library/"NATIVE_VWC_ASSET_MANIFEST.json");native_assets={a["export_local_series_key"]:a for a in manifest["assets"]}
    pins={n:bulk.sha(library/n) for n in ("SERIES_CATALOG.csv","SERIES_CATALOG.parquet","NATIVE_VWC_ASSET_MANIFEST.json")}
    for a in native_assets.values():bulk.require(bulk.sha(library/a["path"])==a["sha256"],"R1 native asset hash differs")
    target=[r for r in catalog if r["product_class"] in TARGETS and r["materialized"]=="True"]
    bulk.require(len(catalog)==470 and len(target)==335,"R1 exact target closure differs")
    refs=load_references(coverage,{r["proposed_stream_id"] for r in target})
    output.mkdir()
    for name in ("series","science","evidence","scratch"): (output/name).mkdir()
    try:
        helper=bulk.compile_helper(output/"bulk-csv-arrow")
        times=timestamp_gate(helper,library,catalog,refs);bulk.write_json(output/"TIMESTAMP_EVIDENCE.json",times)
        print("TIMESTAMP_GATE "+json.dumps({k:v["status"] for k,v in times["families"].items()}),flush=True)
        daily_catalog=[];assets=[];qa=[];products=[];end=utc(as_of).astimezone(PST).date()
        for row in catalog:
            entry=dict(row);entry.update(depth_cm=float(row["depth_cm"]) if row["depth_cm"] else None,
                depth_status=row["depth_status"] if row["depth_cm"] else "UNKNOWN",
                timestamp_status=times["files"][row["relative_asset_path"]]["status"],daily_asset=None,
                accepted_daily_rows=0,withheld_daily_rows=0,missing_daily_rows=0,unresolved_daily_candidates=0,
                eligibility="EXCLUDED_NON_TARGET",timestamp_evidence="TIMESTAMP_EVIDENCE.json")
            if row["product_class"] not in TARGETS:daily_catalog.append(entry);continue
            if row["duplicate_of"]:entry["eligibility"]="DUPLICATE_EXPORT_ALIAS";daily_catalog.append(entry);continue
            if not int(row["observation_count"]):entry["eligibility"]="ALL_NULL_TARGET_NO_OBSERVATIONS";daily_catalog.append(entry);continue
            if not calendar_count(row,end):entry["eligibility"]="NO_COMPLETED_NATIVE_DAYS";daily_catalog.append(entry);continue
            family=times["families"][row["proposed_organization"]]["status"]
            group=refs.get(row["proposed_stream_id"],[])
            if family!="ACCEPTED_FIXED_UTC_MINUS_08" or not group or int(row["observation_count"])>MAX_SERIES_ROWS:
                entry["eligibility"]="UNRESOLVED_SEMANTICS"
                entry["hold_reason"]="TIMESTAMP_FAMILY_UNRESOLVED" if family!="ACCEPTED_FIXED_UTC_MINUS_08" else "NO_RETAINED_API_QUALITY_EVIDENCE" if not group else "PER_SERIES_ROW_BOUND"
                entry["unresolved_daily_candidates"]=calendar_count(row,end)
                daily_catalog.append(entry);continue
            science=build_series(helper,library,output,row,group,as_of)
            daily=[]
            for r in science["rows"]:
                r.update(stream_id=row["proposed_stream_id"],accepted_stream_id=row["accepted_stream_id"] or None,
                    export_local_series_key=row["export_local_series_key"],station_id=row["proposed_station_id"],station_name=row["proposed_station"],
                    depth_cm=entry["depth_cm"],depth_status=entry["depth_status"],native_unit=row["native_unit"],
                    conversion_multiplier=float(row["conversion_multiplier"]),processing_version=VERSION)
                daily.append(contract_columns(r))
            validate_daily(daily,row["proposed_stream_id"])
            path=output/"series"/(row["asset_id"]+".csv");daily_table(helper,path,daily)
            counts=collections.Counter(r["daily_status"] for r in daily)
            entry.update(daily_asset=path.with_suffix(".parquet").relative_to(output).as_posix(),eligibility="LOCAL_DAILY_CANDIDATE",
                accepted_daily_rows=counts["ACCEPTED"],withheld_daily_rows=counts["WITHHELD_BY_EXISTING_SCREEN"],
                missing_daily_rows=counts["MISSING"],unresolved_daily_candidates=counts["UNRESOLVED_SEMANTICS"])
            source=native_assets[row["export_local_series_key"]]
            assets.append(dict(path=entry["daily_asset"],sha256=bulk.sha(path.with_suffix(".parquet")),bytes=path.with_suffix(".parquet").stat().st_size,
                csv=dict(path=path.relative_to(output).as_posix(),sha256=bulk.sha(path),bytes=path.stat().st_size),rows=len(daily),states=dict(counts),
                source_native=source,original_csv_sha256=row["file_sha256"],export_local_series_key=row["export_local_series_key"],
                stream_id=row["proposed_stream_id"],science_sha256=bulk.sha(output/"science"/(row["asset_id"]+".json")),
                input_sha256=bulk.sha(output/"evidence"/(row["asset_id"]+".input.json")),quality_proof_sha256=bulk.sha(output/"evidence"/(row["asset_id"]+".proof.json"))))
            qa.append(dict(export_local_series_key=row["export_local_series_key"],stream_id=row["proposed_stream_id"],
                observations_considered=sum(r["observation_count"] for r in daily),nominal_range_flagged=sum(r["nominal_range_observations"] for r in daily),
                provider_quality_withheld_observations=sum(r["observation_count"] for r in daily if r["source_quality_status"]=="QUARANTINED"),
                unresolved_observations=sum(r["observation_count"] for r in daily if r["daily_status"]=="UNRESOLVED_SEMANTICS"),
                observation_level_deletions=0,**dict(counts)))
            # Retain only bounded examples in memory; full daily rows are durable files.
            products.append((row,[r for r in daily if r["daily_status"]=="ACCEPTED"][:3]))
            daily_catalog.append(entry)
            print("DAILY "+row["proposed_stream_id"]+" "+json.dumps(dict(counts)),flush=True)
        bykey={r["export_local_series_key"]:r for r in daily_catalog}
        for r in daily_catalog:
            if r["duplicate_of"]:r["daily_asset"]=bykey[r["duplicate_of"]]["daily_asset"]
        bulk.table_parquet(helper,output/"DAILY_SERIES_CATALOG.csv",daily_catalog)
        bulk.write_csv(output/"DAILY_QA_SUMMARY.csv",qa)
        fixture=consumer_fixture(products,output)
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
            timestamp_evidence_sha256=bulk.sha(output/"TIMESTAMP_EVIDENCE.json"),
            coverage_summary=dict(path=str(coverage),sha256=bulk.sha(coverage)),
            meaning="Local candidate; accepted means exact-day API quality binding plus unchanged daily screen. No public acceptance or whole-POR claim.",
            csv_layout="One daily CSV per exact export-local series under series/, same stem as Parquet; never concatenate by station/depth"))
        for name,pin in pins.items():bulk.require(bulk.sha(library/name)==pin,"Native catalog/manifest modified")
        for a in native_assets.values():bulk.require(bulk.sha(library/a["path"])==a["sha256"],"Native Parquet changed")
        inputs=read_json(library/"IMPORT_INPUT_BINDING.json")
        for a in inputs["originals"]:bulk.require(bulk.sha(Path(inputs["input_directory"])/a["basename"])==a["sha256"],"Original CSV changed")
        result=dict(result="READY_WITH_FAMILY_TIMESTAMP_HOLDS",version=VERSION,as_of=as_of,output_root=str(output),
            importer_head=bulk.run(["git","rev-parse","HEAD"]).stdout.strip(),catalog_rows=len(daily_catalog),
            daily_asset_series=len(assets),series_with_accepted_days=sum(a["states"].get("ACCEPTED",0)>0 for a in assets),
            daily_rows=sum(a["rows"] for a in assets),parquet_bytes=sum(a["bytes"] for a in assets),csv_bytes=sum(a["csv"]["bytes"] for a in assets),
            daily_states={k:sum(a["states"].get(k,0) for a in assets) for k in ["ACCEPTED","WITHHELD_BY_EXISTING_SCREEN","MISSING","UNRESOLVED_SEMANTICS"]},
            unresolved_daily_candidates=sum(r["unresolved_daily_candidates"] for r in daily_catalog),
            unresolved_count_basis="Within each native first/last observed extent, clipped to completed cutoff; held families counted as naive-date candidates, not UTC coverage",
            timestamp_families=times["families"],unit_unresolved_held=sum(r["product_class"]==bulk.CLASSES[2] for r in catalog),
            target_observations=sum(a["rows"] for a in native_assets.values()),qa_totals={k:sum(x[k] for x in qa) for k in ['observations_considered','nominal_range_flagged','provider_quality_withheld_observations','unresolved_observations','observation_level_deletions']},
            camp_cady=camp,fixture_kinds=list(fixture['examples']),original_csv_writes=0,native_parquet_writes=0,provider_requests=0,
            performance=dict(wall_seconds=time.monotonic()-tick,parent_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                             children_peak_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,memory_note="macOS bytes; one series in R, disk-bounded API union"))
        bulk.write_json(output/"DAILY_PRODUCT_RESULT.json",result)
        with zipfile.ZipFile(output/"L03_DAILY_R2_REVIEW.zip","w",zipfile.ZIP_DEFLATED) as z:
            for n in ['DAILY_PRODUCT_RESULT.json','DAILY_SERIES_CATALOG.csv','DAILY_QA_SUMMARY.csv','TIMESTAMP_EVIDENCE.json','00G_CANDIDATE_FIXTURE.json','00G_CANDIDATE_SCHEMA.json','DAILY_COMPARISONS.json']:
                z.write(output/n,n)
        bulk.require((output/"L03_DAILY_R2_REVIEW.zip").stat().st_size<=5*1024*1024,"Review package exceeds 5 MiB")
        return result
    except Exception as exc:
        bulk.write_json(output/"DAILY_HOLD.json",dict(error=str(exc),accepted=False,version=VERSION))
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('library','coverage-summary','output','as-of'):p.add_argument('--'+name,required=True)
    p.add_argument('--prior-daily',action='append',required=True)
    a=p.parse_args();r=build(Path(a.library),Path(a.coverage_summary),Path(a.output),a.as_of,[Path(x) for x in a.prior_daily])
    print(json.dumps({k:r[k] for k in ['result','daily_asset_series','series_with_accepted_days','daily_rows','daily_states']}))


if __name__=='__main__':main()
