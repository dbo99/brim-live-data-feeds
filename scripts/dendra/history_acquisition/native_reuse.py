"""Read-only exact native coverage proof over original Journals and receipts.

Historical source fingerprints are verified against their recorded bindings,
never replaced with the current implementation. This reader grants no scientific
admission, publication authority, retry, or writable historical state.
"""
from collections import defaultdict
from pathlib import Path

from . import native_snapshot as ns, recovery
from .model import NAME
from .provider_adapter import observation_shape
from .safety import decode, digest, encode, require, sha
from ..transport import parse_utc, normalize_rows, _content_hash

MODES = {"campaign_reviewed_adapter", "task37_recovery_adapter",
         "program_native_archive_adapter"}
REQUIRED = {"root", "campaign_id", "task_id", "station_id", "stream_id",
            "start", "end", "state", "header_sha256", "source_fingerprint",
            "seal_sha256", "seal_file", "objects"}


def _descriptor(value):
    require(isinstance(value, dict) and {"path", "sha256", "bytes"} <= set(value) and
            isinstance(value["sha256"], str) and ns.HASH.fullmatch(value["sha256"]) and
            value["path"] == "objects/" + value["sha256"] + ".bin" and
            type(value["bytes"]) is int and value["bytes"] >= 0,
            "Exact retained object descriptor")
    return {k: value[k] for k in ("path", "sha256", "bytes")}


def _prefix(journal, task):
    """Verify the separately recorded original prefix without reopening it."""
    if journal.binding.get("version") != recovery.PREFIX_VERSION:
        return None
    original = journal.binding["predecessor"]
    ref = journal.binding["predecessor_ref"]
    require(original["task"]["identity"] == task["identity"] and
            all(original["task"][k] == task[k] for k in ("start", "end")),
            "Retained prefix belongs to another exact stream/interval")
    with recovery.open_evidence(ref["root"], original["campaign_id"], None) as old:
        old.verify_records()
        require(old.header_sha == original["header_sha256"] and
                old.binding_sha == original["binding_sha256"] and
                digest(old.binding["collector_sources"]) == original["source_fingerprint"] and
                old.tasks[original["task_id"]] == original["task"] and
                old.events[-1]["record_sha256"] == original["last_anchor_sha256"],
                "Retained prefix original header/task/chain changed")
        receipt = original["prefix_receipt"]
        require(old.snapshot()["attempts"][receipt["attempt_key"]] == receipt and
                receipt["state"] == "received" and receipt["status"] == 200 and
                len(receipt["objects"]) == 1,
                "Retained prefix original receipt changed")
        body = old.read_object(receipt["objects"][0])
        require(journal.read_object(receipt["objects"][0]) == body,
                "Retained prefix copy differs from original bytes")
        return receipt


def _completed(journal, key, state):
    """Reconstruct complete coverage from original retained request pages."""
    task, item = journal.tasks[key], state["intervals"][key]
    seal = item["complete"]
    require(seal is not None and item["state"] in ("complete_empty", "complete_nonempty") and
            seal["interval_key"] == key and seal["run"] == item["runs"] and
            len(seal["objects"]) == 1,
            "Retained interval is unsealed or has inconsistent state")
    keys = seal["attempt_keys"]
    require(isinstance(keys, list) and keys and len(keys) == len(set(keys)) and
            set(keys) == {k for k, a in state["attempts"].items() if a["interval_key"] == key},
            "Retained seal omits or duplicates charged receipts")
    receipts = [state["attempts"][k] for k in keys]
    envelope = journal.completed(key)
    if journal.binding["mode"] == "program_native_archive_adapter":
        from .native_program import raw_envelope
        require(envelope == raw_envelope(journal, key, seal["run"], keys) and
                seal.get("product_eligible") is False and
                seal.get("acquisition_status") == envelope["acquisition_status"],
                "Retained native-only envelope/eligibility changed")
        require(seal["state"] == ("complete_nonempty" if envelope["rows"] else "complete_empty") and
                seal["checked_at"] == envelope["pages"][-1]["retrieved_at"] and
                seal["latest_source_observation"] == (envelope["rows"][-1]["t"] if envelope["rows"] else None),
                "Retained native-only seal clocks/state changed")
        return envelope, seal, receipts

    prefix = _prefix(journal, task)
    pages = ([prefix] if prefix is not None else []) + receipts
    require(0 < len(pages) <= 3 and envelope["query_complete"] is True and
            envelope["datastream_id"] == task["identity"]["stream_id"] and
            envelope["requested_interval"] == {"start_inclusive": task["start"], "end_exclusive": task["end"]} and
            envelope["page_count"] == len(pages) == len(envelope["pages"]),
            "Retained complete envelope identity/interval/page closure")
    cursor, end = parse_utc(task["start"]), parse_utc(task["end"])
    source_rows = []
    for n, (page, receipt) in enumerate(zip(envelope["pages"], pages)):
        require(receipt["state"] == "received" and receipt["status"] == 200 and
                len(receipt["objects"]) == 1 and
                (n == 0 and prefix is not None or
                 receipt["interval_key"] == key and receipt["run"] == seal["run"]),
                "Retained seal includes a failed or foreign receipt")
        obj = _descriptor(receipt["objects"][0])
        require(page["response_sha256"] == receipt["response_sha256"] == obj["sha256"] and
                page["response_bytes"] == receipt["response_bytes"] == obj["bytes"] and
                parse_utc(page["requested_at_utc"]) <= parse_utc(receipt["reserved_at"]) <=
                parse_utc(receipt["at"]) <= parse_utc(page["retrieved_at_utc"]) <= parse_utc(seal["checked_at"]),
                "Retained page receipt/hash/clock mismatch")
        raw = observation_shape(journal.read_object(obj), task["identity"]["stream_id"],
                                quality_policy=journal.binding.get("quality_policy"))
        require(receipt["source_rows"] == len(raw["data"]) and parse_utc(receipt["cursor"]) == cursor,
                "Retained page row accounting/cursor mismatch")
        previous = cursor
        for row in raw["data"]:
            at = parse_utc(row["t"])
            require(previous <= at < end, "Retained rows outside exact ascending request interval")
            previous = at
        if n < len(pages) - 1:
            require(len(raw["data"]) == raw["limit"] and previous > cursor,
                    "Retained pagination is incomplete or nonadvancing")
            cursor = previous
        else:
            require(len(raw["data"]) < raw["limit"], "Retained full final page cannot establish coverage")
        source_rows.extend(raw["data"])
    rows, diagnostics = normalize_rows(source_rows, task["start"], task["end"],
                                       quality_policy=journal.binding.get("quality_policy"))
    require(envelope["diagnostics"]["completion_reason"] in
            ("empty_page", "short_page_with_effective_limit") and
            rows == envelope["rows"] and envelope["content_sha256"] == _content_hash(envelope) and
            all(envelope["diagnostics"].get(k) == v for k, v in diagnostics.items()),
            "Retained completed object differs from original raw pages")
    if journal.binding.get("quality_policy") is not None:
        from .observation_quality import summary
        require(envelope.get("quality_disposition") == summary(rows) and
                envelope.get("query_state") == "QUERY_COMPLETE", "Retained native quality lineage changed")
    require(seal["state"] == ("complete_nonempty" if rows else "complete_empty") and
            envelope["latest_observation_utc"] == seal["latest_source_observation"] ==
            (rows[-1]["t"] if rows else None) and
            envelope["retrieval_first_utc"] == envelope["pages"][0]["retrieved_at_utc"] and
            envelope["retrieval_last_utc"] == envelope["pages"][-1]["retrieved_at_utc"] == seal["checked_at"],
            "Retained completion state or retrieval clocks changed")
    return envelope, seal, receipts


def observed_density(row, ref):
    """A minimum positive observed spacing from exact immutable native receipts.

    Failed intervals can supply actual timestamp evidence, never reusable
    coverage. A current sample_interval field is deliberately not consulted.
    """
    require(set(ref)=={'root','campaign_id','task_id','header_sha256','source_fingerprint','last_anchor_sha256'},
            'Exact observed-density Journal reference required')
    with recovery.open_evidence(ref['root'],ref['campaign_id'],None) as j:
        j.verify_records();s=j.snapshot()
        require(not s['damage'] and j.header_sha==ref['header_sha256'] and
                digest(j.binding['collector_sources'])==ref['source_fingerprint'] and
                j.events[-1]['record_sha256']==ref['last_anchor_sha256'] and ref['task_id'] in j.tasks,
                'Observed-density source/header/anchor changed')
        task=j.tasks[ref['task_id']];identity=task['identity']
        require(identity['stream_id']==row['stream_id'] and identity['station_id']==row['station_id'] and
                identity['native_unit']==row['native_unit'], 'Observed-density exact-stream identity mismatch')
        spacing=None;count=0
        for a in s['attempts'].values():
            if a['interval_key']!=ref['task_id'] or a['state']!='received' or a['status']!=200:continue
            require(len(a['objects'])==1,'Observed-density receipt body required')
            body=j.read_object(a['objects'][0])
            require(sha(body)==a['response_sha256'] and len(body)==a['response_bytes'],
                    'Observed-density receipt hash/bytes changed')
            raw=observation_shape(body,row['stream_id'],quality_policy=j.binding.get('quality_policy'))
            require(a['source_rows']==len(raw['data']),'Observed-density row accounting changed')
            previous=None
            for item in raw['data']:
                stamp=parse_utc(item['t'])
                require(parse_utc(a['cursor'])<=stamp<parse_utc(task['end']) and
                        (previous is None or previous<=stamp),'Observed-density time order/bounds')
                if previous is not None and stamp>previous:
                    seconds=(stamp-previous).total_seconds();count+=1
                    spacing=min(spacing,seconds) if spacing is not None else seconds
                previous=stamp
        return dict(seconds=spacing,positive_intervals=count)


def historical_density(row, evidence, cutoff):
    """Historical epochs, retained spacing, exact historical record, or unknown.

    Source records must explicitly bind this stream, interval and milliseconds.
    Locators are JSON pointers into preserved original documents; no edited
    metadata or nominal current cadence can masquerade as historical evidence.
    Denser observed evidence always tightens a source assertion.
    """
    require(set(evidence)=={'epochs','observations','historical_records'} and
            all(isinstance(v,list) and len(v)<=10000 for v in evidence.values()),
            'Bounded historical-density evidence required')
    begin,end=parse_utc(row['query_start']),parse_utc(cutoff)
    def records(items):
        spans=[]
        for ref in items:
            require(set(ref)=={'document','stream_pointer','start_pointer','end_pointer','milliseconds_pointer'},
                    'Explicit historical source field locators required')
            doc=ns.reference(ref['document'])
            def pointer(p):
                require(isinstance(p,str) and p.startswith('/'),'Historical source JSON pointer')
                value=doc
                for component in p[1:].split('/'):
                    component=component.replace('~1','/').replace('~0','~')
                    value=value[int(component)] if isinstance(value,list) else value[component]
                return value
            require(pointer(ref['stream_pointer'])==row['stream_id'],'Historical source belongs to another stream')
            a,b=parse_utc(pointer(ref['start_pointer'])),parse_utc(pointer(ref['end_pointer']))
            ms=pointer(ref['milliseconds_pointer'])
            require(a<b and type(ms) in (int,float) and 0<ms<float('inf'),'Invalid historical source epoch')
            spans.append((a,b,ms/1000))
        cursor=begin;values=[]
        for a,b,seconds in sorted(spans):
            if b<=begin or a>=end:continue
            if a>cursor:return None
            cursor=max(cursor,b);values.append(seconds)
        return min(values) if values and cursor>=end else None
    epoch=records(evidence['epochs'])
    observed=[observed_density(row,r) for r in evidence['observations']]
    observed=[r['seconds'] for r in observed if r['positive_intervals']>=2 and r['seconds'] is not None]
    other=records(evidence['historical_records'])
    choices=[v for v in [epoch, min(observed) if observed else None, other] if v is not None]
    if not choices:return None
    return dict(seconds=min(choices),basis='HISTORICAL_CONFIGURATION_EPOCHS' if epoch is not None else
                'VERIFIED_EXACT_STREAM_OBSERVATIONS' if observed else 'EXACT_HISTORICAL_RECORD',
                evidence_sha256=digest(evidence),maximum_unseen_density_proven=False)


def validate(references, snapshot):
    """Verify exact selected references without writing any historical file."""
    require(isinstance(references, list) and len(references) <= 10000,
            "Bounded retained native reference set")
    groups, seen = defaultdict(list), set()
    for ref in references:
        require(isinstance(ref, dict) and REQUIRED <= set(ref) and
                isinstance(ref["campaign_id"], str) and NAME.fullmatch(ref["campaign_id"]) and
                all(isinstance(ref[k], str) and ns.HASH.fullmatch(ref[k]) for k in
                    ("task_id", "header_sha256", "source_fingerprint", "seal_sha256")),
                "Exact original native reference fields")
        root = Path(ref["root"])
        require(root.is_absolute() and root == root.resolve(), "Canonical original native Journal root")
        sid = ref["stream_id"]
        require(sid in snapshot.rows and ref["station_id"] == snapshot.rows[sid]["station_id"] and
                ref["state"] in ("complete_nonempty", "complete_empty"), "Retained reference exact snapshot identity")
        ns.interval({k: ref[k] for k in ("start", "end")})
        key = (str(root), ref["campaign_id"], ref["task_id"])
        require(key not in seen, "Duplicate retained Journal/task reference")
        seen.add(key)
        groups[key[:2]].append(ref)
    for (root, cid), refs in groups.items():
        with recovery.open_evidence(root, cid, None) as journal:
            journal.verify_records()
            require(journal.binding["mode"] in MODES and journal.binding["campaign_id"] == cid,
                    "Unsupported original native archive Journal")
            state = journal.snapshot()
            for ref in refs:
                key, sid = ref["task_id"], ref["stream_id"]
                require(journal.header_sha == ref["header_sha256"] and
                        digest(journal.binding["collector_sources"]) == ref["source_fingerprint"] and
                        key in journal.tasks, "Original native source/header/task changed")
                task, row = journal.tasks[key], snapshot.rows[sid]
                identity = task["identity"]
                require(identity["stream_id"] == sid and identity["station_id"] == ref["station_id"] and
                        identity["native_unit"] == row["native_unit"] and
                        identity == journal.binding["roster"][sid] and sid in journal.binding["selected_ids"] and
                        all(task[k] == ref[k] for k in ("start", "end")) and
                        ("identity" not in ref or ref["identity"] == identity),
                        "Original native stream/station/unit/interval differs from claimed coverage")
                envelope, seal, receipts = _completed(journal, key, state)
                events = [e for e in journal.events if e["kind"] == "sealed" and e["data"] == seal]
                require(len(events) == 1 and events[0]["record_sha256"] == ref["seal_sha256"] and
                        seal["state"] == ref["state"], "Original native seal/state differs")
                event = events[0]
                seal_ref = ref["seal_file"]
                expected_path = str(Path(root) / journal._event_path(event["sequence"]))
                require(seal_ref["path"] == expected_path and ns.reference(seal_ref) == event and
                        ("bytes" not in seal_ref or seal_ref["bytes"] == len(encode(event))),
                        "Original native seal locator/body changed")
                expected = {("normalized_archive", d["path"], d["sha256"], d["bytes"])
                            for d in seal["objects"]}
                expected |= {("provider_response", d["path"], d["sha256"], d["bytes"])
                             for a in receipts for d in a["objects"]}
                require(isinstance(ref["objects"], list) and ref["objects"], "Original native object index required")
                actual = set()
                for obj in ref["objects"]:
                    descriptor = _descriptor(obj)
                    require(obj["absolute_path"] == str(Path(root) / journal.prefix / descriptor["path"]),
                            "Original native object locator changed")
                    journal.read_object(descriptor)
                    actual.add((obj["role"], descriptor["path"], descriptor["sha256"], descriptor["bytes"]))
                require(actual == expected, "Original native archive/receipt object set changed")
                if "received_pages" in ref:
                    require(ref["received_pages"] == len(receipts), "Original received page count changed")
                if "provider_attempts" in ref:
                    require(ref["provider_attempts"] == len(receipts), "Original task attempt count changed")
                if "provider_bytes" in ref:
                    require(ref["provider_bytes"] == sum(a["response_bytes"] for a in receipts),
                            "Original task response-byte accounting changed")
    return dict(references_verified=len(references), journals_verified=len(groups), read_only=True)
