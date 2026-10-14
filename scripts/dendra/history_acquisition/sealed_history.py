"""Offline multi-Journal reader and compact private lineage, never acquisition.

The caller pins an ordered seal set. Each original Journal is independently
verified under its recorded source and receipt-time review. Hashes bind caller
trusted evidence; they are not signatures or substitutes for original objects.
"""
from datetime import date, timedelta
from pathlib import Path

from .safety import Root, decode, digest, encode, require, sha
from .model import HASH, INVENTORY_SHA256, source_binding
from . import daily_handoff as h, observation_quality as quality, recovery
from ..transport import normalize_rows, parse_utc

VERSION = "dendra-multi-journal-seal-set-1"
LINEAGE = "dendra-compact-sealed-lineage-1"
HANDOFF = "dendra-sealed-daily-handoff-3"
MAX_BYTES = 8388608
MAX_SEALS = 512
MAX_ROWS = 1000000
ENTRY_FIELDS = "root campaign_id task_id source_fingerprint header_sha256 seal_sha256 archive_sha256 identity start end".split()
SEAL_FIELDS = "task_id start end query_state seal_record_sha256 content_sha256 parsed_sha256 row_count campaign_id header_sha256 source_fingerprint entry_sha256 receipt_set_sha256 review_sha256 decision_sha256 configuration_sha256 configuration_window scale_sha256 raw_rows quality_sha256 quarantined_groups quarantined_occurrences withheld_days retrieval_first_utc retrieval_last_utc recovery_lineage_sha256".split()


def exact(value, fields):
    require(type(value) is dict and set(value) == set(fields), "Exact private lineage fields required")


def hash_value(value):
    require(type(value) is str and HASH.fullmatch(value), "Explicit SHA-256 required")


def scale_semantics(scale):
    # Only scope/derived decision hash may vary. Every scientific assertion stays.
    return {k:v for k,v in scale.items() if k not in ("decision_sha256", "temporal_applicability", "segments")}


def review_interval(task, decision):
    ident = task["identity"]; lo, hi = map(parse_utc, (task["start"], task["end"]))
    require(decision["identity"] == ident and decision["native_unit"] == ident["native_unit"] and
            decision["native_acquisition_eligible"] is True and
            decision["native_acquisition_status"] == "NATIVE_ACQUISITION_ELIGIBLE" and
            decision["access_status"] == "fresh_public" and not decision["hold_reasons"],
            "Unaccepted scientific/access review")
    require(parse_utc(decision["scope"]["start"]) <= lo < hi <= parse_utc(decision["scope"]["end"]),
            "Interval exceeds accepted review scope")
    ordinal = task["native_task"]["identity"]["configuration_ordinal"]
    windows = [w for w in decision["configuration_windows"] if w["ordinal"] == ordinal and
               parse_utc(w["start"]) <= lo and (w["end"] is None or hi <= parse_utc(w["end"]))]
    require(len(windows) == 1, "Configuration does not cover exact interval")
    scale = decision["scale"]; factor = {"Percent":1, "VolumetricWaterContent":100}.get(ident["native_unit"])
    require(factor is not None and scale["frozen_identity"] == ident and
            scale["normalized_percent_eligible"] is True and scale["conversion_factor"] == factor,
            "Unaccepted normalized scale")
    scope = scale["temporal_applicability"]
    require(scope["kind"] == "interval" and parse_utc(scope["start"]) <= lo < hi <= parse_utc(scope["end"]),
            "Scale scope gap")
    cursor = lo
    for segment in scale["segments"]:
        a,b = map(parse_utc, (segment["start"],segment["end"]))
        require(segment["normalized_percent_eligible"] is True and segment["conversion_factor"] == factor,
                "Conflicting scale segment")
        if a <= cursor < b: cursor = b
    require(cursor >= hi, "Scale segment gap")
    return windows[0]


def referenced_archive(journal, entry, inventory):
    """Verify an explicitly pinned, previously sealed reference without replay.

    Original header/receipts/anchors/object hashes and receipt-time authority
    remain mandatory. This reader checks the sealed envelope and page bindings;
    it does not normalize or reevaluate every historical observation.
    """
    exact(entry, ENTRY_FIELDS)
    require(journal.inspect_only and journal.header_sha == entry["header_sha256"] and
            digest(journal.binding["collector_sources"]) == entry["source_fingerprint"],
            "Referenced archive source/header changed")
    journal.verify_records()
    h._binding(journal.binding, journal.tasks, inventory, entry["source_fingerprint"])
    task = journal.tasks[entry["task_id"]]
    require(all(task[k] == entry[k] for k in ("identity", "start", "end")), "Referenced archive task changed")
    state = journal.snapshot(); item = state["intervals"][entry["task_id"]]; seal = item["complete"]
    require(seal and item["state"] in ("complete_empty", "complete_nonempty") and
            len(seal["objects"]) == 1 and seal["objects"][0]["sha256"] == entry["archive_sha256"],
            "Referenced archive seal/object changed")
    events = [e for e in journal.events if e["kind"] == "sealed" and e["data"] == seal]
    require(len(events) == 1 and events[0]["record_sha256"] == entry["seal_sha256"], "Referenced seal receipt changed")
    env = journal.completed(entry["task_id"])
    require(env["query_complete"] is True and env["datastream_id"] == entry["identity"]["stream_id"] and
            env["requested_interval"] == dict(start_inclusive=entry["start"], end_exclusive=entry["end"]) and
            env["page_count"] == len(env["pages"]) == len(seal["attempt_keys"]) and
            env["diagnostics"]["completion_reason"] in ("empty_page", "short_page_with_effective_limit"),
            "Referenced query closure changed")
    from .eligibility import validate_decision
    bundle = journal.binding["reviewed_bundles"][entry["identity"]["stream_id"]]
    for page, key in zip(env["pages"], seal["attempt_keys"]):
        a = state["attempts"][key]
        require(a["state"] == "received" and a["status"] == 200 and len(a["objects"]) == 1 and
                a["source_rows"] is not None and a["interval_key"] == entry["task_id"] and
                page["response_sha256"] == a["response_sha256"] == a["objects"][0]["sha256"] and
                page["response_bytes"] == a["response_bytes"] == a["objects"][0]["bytes"],
                "Referenced page/receipt changed")
        journal.read_object(a["objects"][0])  # Hash check only; no observation replay.
        validate_decision(inventory, encode(bundle["packet"]), bundle["review"], bundle["decision"],
                          executor_fingerprint=entry["source_fingerprint"], now=a["reserved_at"])
    return env, review_interval(task, bundle["decision"])


def read_set(path, checksum, inventory):
    """Bounded explicit manifest, exact stream horizons, no implicit gaps/sorting."""
    path = Path(path); hash_value(checksum)
    with Root(path.parent) as fs: body = fs.read(path.name, MAX_BYTES)
    require(sha(body) == checksum, "Seal-set manifest changed")
    m = decode(body)
    exact(m, ("schema_version", "inventory_sha256", "streams", "seals"))
    require(m["schema_version"] == VERSION and m["inventory_sha256"] == INVENTORY_SHA256 and
            type(m["streams"]) is list and 0 < len(m["streams"]) <= 32 and
            type(m["seals"]) is list and 0 < len(m["seals"]) <= MAX_SEALS, "Seal-set bounds/version")
    expected = {}; previous = None; seen = set(); counts = {}
    for s in m["streams"]:
        exact(s, ("identity", "start", "end")); sid = s["identity"]["stream_id"]
        require(sid not in expected and s["identity"] == inventory.identity(sid) and
                parse_utc(s["start"]) < parse_utc(s["end"]), "Manifest stream/horizon")
        expected[sid] = dict(s, cursor=s["start"])
    require(list(expected) == sorted(expected), "Manifest stream order")
    for e in m["seals"]:
        exact(e, ENTRY_FIELDS); sid=e["identity"]["stream_id"]
        require(sid in expected and e["identity"] == expected[sid]["identity"], "Seal-set identity")
        for k in ("task_id", "source_fingerprint", "header_sha256", "seal_sha256", "archive_sha256"): hash_value(e[k])
        order=(sid,parse_utc(e["start"]))
        require(previous is None or previous < order, "Exact seal ordering required")
        require(e["start"] == expected[sid]["cursor"] and parse_utc(e["start"]) < parse_utc(e["end"]),
                "Unexpected interval overlap/gap")
        require(e["seal_sha256"] not in seen, "Duplicate seal")
        p=Path(e["root"])
        require(p.is_absolute() and str(p.resolve()) == str(p), "Explicit canonical Journal root required")
        expected[sid]["cursor"]=e["end"]; previous=order;seen.add(e["seal_sha256"])
        counts[sid]=counts.get(sid,0)+1
    require(all(s["cursor"] == s["end"] and 0 < counts.get(sid,0) <= 128 for sid,s in expected.items()),
            "Exact horizon closure/per-stream bound")
    return m


def verify(path, *, manifest_sha256, inventory):
    """Read-only original bindings, then deterministic normalized sequence."""
    m=read_set(path,manifest_sha256,inventory); records=[]; total=0
    for e in m["seals"]:
        with recovery.open_evidence(e["root"], e["campaign_id"], inventory) as j:
            require(j.header_sha == e["header_sha256"] and digest(j.binding["collector_sources"]) == e["source_fingerprint"],
                    "Original header/source differs")
            require(j.binding["collector_sources"]["core.R"] == sha((Path(__file__).parent.parent/"core.R").read_bytes()),
                    "Original numerical authority differs")
            h._binding(j.binding,j.tasks,inventory,e["source_fingerprint"]);j.verify_records()
            require(e["task_id"] in j.tasks, "Missing bound task")
            task=j.tasks[e["task_id"]]
            require(all(task[k] == e[k] for k in ("identity","start","end")), "Seal task scope mismatch")
            env,seal,attempts=h._seal(j,e["task_id"],j.snapshot(),inventory,e["source_fingerprint"])
            events=[v for v in j.events if v["kind"] == "sealed" and v["data"] == seal]
            require(len(events)==1 and events[0]["record_sha256"] == e["seal_sha256"] and
                    seal["objects"][0]["sha256"] == e["archive_sha256"], "Original seal/archive differs")
            bundle=j.binding["reviewed_bundles"][e["identity"]["stream_id"]];decision=bundle["decision"]
            window=review_interval(task,decision)
            originals=[row for a in attempts for row in decode(j.read_object(a["objects"][0]))["data"]]
            rows,_=normalize_rows(originals,e["start"],e["end"],quality_policy=quality.binding())
            total+=len(rows);require(total<=MAX_ROWS,"Full-history normalized row bound")
            qs=quality.summary(rows)
            ref=dict(task_id=e["task_id"],start=e["start"],end=e["end"],query_state="COMPLETE_NONEMPTY" if rows else "COVERED_EMPTY",
                seal_record_sha256=e["seal_sha256"],content_sha256=env["content_sha256"],parsed_sha256=e["archive_sha256"],row_count=len(rows),
                campaign_id=e["campaign_id"],header_sha256=e["header_sha256"],source_fingerprint=e["source_fingerprint"],entry_sha256=digest(e),
                receipt_set_sha256=digest(attempts),review_sha256=decision["review_sha256"],decision_sha256=decision["decision_sha256"],
                configuration_sha256=decision["configuration_sha256"],configuration_window=window,scale_sha256=digest(decision["scale"]),
                raw_rows=len(originals),quality_sha256=digest(qs),quarantined_groups=qs["quarantined_groups"],
                quarantined_occurrences=qs["quarantined_occurrences"],withheld_days=quality.days(rows),
                retrieval_first_utc=env["retrieval_first_utc"],retrieval_last_utc=env["retrieval_last_utc"],
                recovery_lineage_sha256=digest(seal["recovery_lineage"]) if "recovery_lineage" in seal else None)
            configuration=next(c for c in bundle["packet"]["configuration_evidence"]["configurations"] if c["ordinal"] == window["ordinal"])
            records.append(dict(task_id=e["task_id"],task=task,seal=seal,seal_record_sha256=e["seal_sha256"],
                science_rows=rows,envelope=dict(rows=rows,content_sha256=env["content_sha256"]),
                configured_cadence_claim=configuration["fields"]["interval"],decision=decision,compact_ref=ref))
    # Full sequences cannot acquire a fresh source or review merely by combining.
    for sid in {r["task"]["identity"]["stream_id"] for r in records}:
        rr=[r for r in records if r["task"]["identity"]["stream_id"]==sid]
        require(all(scale_semantics(r["decision"]["scale"]) == scale_semantics(rr[0]["decision"]["scale"]) and
                    r["decision"]["configuration_sha256"] == rr[0]["decision"]["configuration_sha256"] and
                    r["decision"]["scientific_sha256"] == rr[0]["decision"]["scientific_sha256"] for r in rr),
                "Conflicting scientific/configuration assertions")
    return dict(records=records,schema_version=VERSION,evidence_manifest_sha256=manifest_sha256,
        acquisition_fingerprint=digest([e["source_fingerprint"] for e in m["seals"]]),
        execution_binding_sha256=digest([e["header_sha256"] for e in m["seals"]]),
        task_binding_sha256=digest([e["task_id"] for e in m["seals"]]))


def compact(records, *, seal_set_sha256, native_sha256):
    rows=[row for r in records for row in r["science_rows"]]
    refs=[r["compact_ref"] for r in records]
    value=dict(schema_version=LINEAGE,identity=records[0]["task"]["identity"],
        handoff_version=HANDOFF,seal_set_sha256=seal_set_sha256,
        preparation_fingerprint=digest(source_binding()),quality_policy=quality.binding(),
        scientific_scale={k:records[0]["decision"]["scale"][k] for k in
                          ("frozen_identity", "conversion_factor", "normalized_percent_eligible")},
        native_csv_sha256=native_sha256,normalized_rows_sha256=digest(rows),
        quarantine=h.disposition(rows),seals=refs,seal_count=len(refs),
        review_set_sha256=digest([r["review_sha256"] for r in refs]),
        scale_set_sha256=digest([r["scale_sha256"] for r in refs]),
        native_rows=sum(r["row_count"] for r in refs),raw_rows=sum(r["raw_rows"] for r in refs))
    require(len(encode(value)) <= MAX_BYTES,"Compact lineage input bound")
    return dict(value,lineage_sha256=digest(value))


def validate_compact(value, specification, handoff):
    """Independently validate fixed-schema private projection, never source q."""
    exact(value,"schema_version identity handoff_version seal_set_sha256 preparation_fingerprint quality_policy scientific_scale native_csv_sha256 normalized_rows_sha256 quarantine seals seal_count review_set_sha256 scale_set_sha256 native_rows raw_rows lineage_sha256".split())
    require(len(encode(value))<=MAX_BYTES and value["schema_version"]==LINEAGE and value["handoff_version"]==HANDOFF and
            value["lineage_sha256"]==digest({k:v for k,v in value.items() if k!='lineage_sha256'}),"Compact lineage hash/version")
    require(value["identity"]==specification["identity"] and value["preparation_fingerprint"]==handoff["science_binding"]["collector_fingerprint"] and
            value["seal_set_sha256"]==handoff["evidence_binding"]["evidence_manifest_sha256"] and
            value["native_csv_sha256"]==specification["csv"]["sha256"],"Compact preparation binding")
    quality.validate_policy(value["quality_policy"])
    require(type(value["seals"]) is list and 0<len(value["seals"])<=128 and value["seal_count"]==len(value["seals"]),"Compact seal count")
    refs=value["seals"];previous=None;seen=set()
    for k in ('seal_set_sha256','preparation_fingerprint','native_csv_sha256','normalized_rows_sha256',
              'review_set_sha256','scale_set_sha256'):hash_value(value[k])
    for r in refs:
        exact(r,SEAL_FIELDS)
        hash_value(r['task_id']);hash_value(r['source_fingerprint'])
        from .model import NAME
        require(type(r['campaign_id']) is str and NAME.fullmatch(r['campaign_id']),'Compact campaign identity')
        for k in SEAL_FIELDS:
            if k.endswith('sha256'):hash_value(r[k]) if r[k] is not None or k!='recovery_lineage_sha256' else None
        for k in ('row_count','raw_rows','quarantined_groups','quarantined_occurrences'):
            require(type(r[k]) is int and 0<=r[k]<=MAX_ROWS,'Compact count')
        require(r['row_count']<=r['raw_rows'] and r['quarantined_groups']<=r['row_count'] and
                r['quarantined_groups']<=r['quarantined_occurrences']<=r['raw_rows'],'Compact count consistency')
        require(r['query_state']==('COMPLETE_NONEMPTY' if r['row_count'] else 'COVERED_EMPTY') and
                (previous is None or previous==r['start']) and parse_utc(r['start'])<parse_utc(r['end']) and
                r['seal_record_sha256'] not in seen,'Compact interval/seal closure')
        w=r['configuration_window'];exact(w,('ordinal','start','end','object_sha256'));hash_value(w['object_sha256'])
        require(type(w['ordinal']) is int and w['ordinal']>=0 and parse_utc(w['start'])<=parse_utc(r['start']) and
                (w['end'] is None or parse_utc(r['end'])<=parse_utc(w['end'])),'Compact configuration scope')
        require(parse_utc(r['retrieval_first_utc'])<=parse_utc(r['retrieval_last_utc']),'Compact retrieval clock')
        require(r['withheld_days']==sorted(set(r['withheld_days'])) and bool(r['withheld_days'])==bool(r['quarantined_groups']),'Compact quality dates')
        for day in r['withheld_days']:
            date.fromisoformat(day)
            lo=parse_utc(day+'T08:00:00Z')
            require(lo<parse_utc(r['end']) and lo+timedelta(days=1)>parse_utc(r['start']),
                    'Compact quality date outside interval')
        previous=r['end'];seen.add(r['seal_record_sha256'])
    from .browser_projection import INTERVAL_FIELDS
    require([{k:r[k] for k in INTERVAL_FIELDS} for r in refs]==specification['intervals'],'Compact interval manifest mismatch')
    require(value['native_rows']==sum(r['row_count'] for r in refs) and value['raw_rows']==sum(r['raw_rows'] for r in refs) and
            value['review_set_sha256']==digest([r['review_sha256'] for r in refs]) and
            value['scale_set_sha256']==digest([r['scale_sha256'] for r in refs]),'Compact aggregate closure')
    require(value['quarantine']==specification['quarantine']==dict(policy=quality.binding(),
        withheld_days=sorted({d for r in refs for d in r['withheld_days']}),
        quarantined_groups=sum(r['quarantined_groups'] for r in refs)),'Compact quarantine closure')
    factor={'Percent':1,'VolumetricWaterContent':100}.get(value['identity']['native_unit'])
    exact(value['scientific_scale'],('frozen_identity','conversion_factor','normalized_percent_eligible'))
    require(factor is not None and value['scientific_scale']['frozen_identity']==value['identity'] and
            value['scientific_scale']['conversion_factor']==factor and value['scientific_scale']['normalized_percent_eligible'] is True and
            specification['unit_normalization']==dict(status='verified_percent_conversion',multiplier=factor,offset=0),'Compact science scale')
    return refs


def prepare_product(seal_manifest, *, manifest_sha256, inventory, output_root, as_of, rscript='Rscript'):
    m=read_set(seal_manifest,manifest_sha256,inventory)
    out=Path(output_root).resolve()
    require(all(out!=Path(e['root']) and Path(e['root']) not in out.parents for e in m['seals']),
            'Output cannot alter original Journal evidence')
    verified=verify(seal_manifest,manifest_sha256=manifest_sha256,inventory=inventory)
    return h._prepare_verified(verified, sealed_root=Path(seal_manifest).parent,
        manifest_sha256=manifest_sha256,output_root=output_root,as_of=as_of,rscript=rscript,compact_lineage=True)


def verify_preparation_sources(seal_manifest, *, manifest_sha256, inventory, prepared_root):
    """Optional full audit from pinned original evidence, not from compact claims."""
    verified=verify(seal_manifest,manifest_sha256=manifest_sha256,inventory=inventory)
    with Root(prepared_root) as fs:
        handoff=decode(fs.read('handoff.json',MAX_BYTES))
        expected={r['task']['identity']['stream_id'] for r in verified['records']}
        require(len(handoff['streams'])==len(expected) and
                {s['identity']['stream_id'] for s in handoff['streams']}==expected,
                'Preparation omitted or added a manifest stream')
        for s in handoff['streams']:
            rr=[r for r in verified['records'] if r['task']['identity']==s['identity']]
            body=h.csv_bytes([row for r in rr for row in r['science_rows']],s['identity'],rr[0]['decision']['scale'])
            require(sha(body)==s['csv']['sha256'] and len(body)==s['csv']['bytes'],'Original native sequence differs')
            require(s['csv']['path']=='native/'+s['identity']['stream_id']+'.csv' and
                    fs.read(s['csv']['path'],len(body))==body,'Stored native input differs')
            actual=decode(fs.read(s['lineage']['path'],MAX_BYTES))
            validate_compact(actual,s,handoff)
            require(actual==compact(rr,seal_set_sha256=manifest_sha256,native_sha256=sha(body)), 'Original compact lineage differs')
    return True
