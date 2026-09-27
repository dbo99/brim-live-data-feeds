"""Offline sealed native archive -> unchanged R daily science.

Explicit trusted manifest/source pins are inputs, not authenticity claims. Old
journals are inspected under their acquisition identity, never resumed. Output
is a fresh local preparation with no publication or latest-observation authority.
"""
import csv
from datetime import datetime, timedelta, timezone
import io
import math
from pathlib import Path
import subprocess
import time
from urllib.parse import urlencode

from ..transport import parse_utc, format_utc, normalize_rows, _content_hash
from . import campaign
from .eligibility import validate_decision
from .journal import Journal, BODY_BYTES
from .model import Inventory, INVENTORY_SHA256, HASH, source_binding
from .safety import Root, decode, digest, encode, require, sha

VERSION = "dendra-sealed-daily-handoff-1"
POINT_VERSION = "dendra-soil-point-semantics-1"
PST = timezone(timedelta(hours=-8))
CSV_FIELDS = ("t", "datastream_id", "v", "value_status", "duplicate_conflict", "alternative_out_of_range")
MAX_MANIFEST_BYTES = 4 * 1024**2


def _pin(value):
    require(isinstance(value, str) and HASH.fullmatch(value), "Explicit SHA-256 pin required")
    return value


def _manifest(root, expected):
    body = root.read("evidence-manifest.json", MAX_MANIFEST_BYTES)
    require(sha(body) == _pin(expected), "Sealed evidence manifest hash differs")
    value = decode(body)
    require(isinstance(value.get("files"), list) and 0 < len(value["files"]) <= 4096,
            "Bounded evidence manifest required")
    entries = {}
    for item in value["files"]:
        name = item["path"]
        Root.parts(name)
        require(name not in entries and type(item["bytes"]) is int and 0 <= item["bytes"] <= BODY_BYTES,
                "Manifest path/size bound")
        blob = root.read(name, BODY_BYTES)
        require(len(blob) == item["bytes"] and sha(blob) == _pin(item["sha256"]),
                "Sealed evidence file missing or changed: " + name)
        entries[name] = item
    return entries


def _binding(binding, tasks, inventory, acquisition_fingerprint):
    require(type(inventory) is Inventory and binding["mode"] == "campaign_reviewed_adapter" and
            binding["version"] == "dendra-campaign-execution-1" and
            digest(binding["collector_sources"]) == _pin(acquisition_fingerprint) and
            binding["inventory_sha256"] == INVENTORY_SHA256 and binding["roster"] == inventory.roster(),
            "Acquisition source/inventory mismatch")
    manifest = dict(binding["campaign_manifest"], roster=binding["roster"])
    campaign.verify(manifest, inventory, executor_fingerprint=acquisition_fingerprint)
    require(binding["campaign_id"] == manifest["core"]["campaign_id"] and
            binding["request_policy"] == manifest["budgets"], "Campaign binding mismatch")
    selected = binding["selected_ids"]
    require(selected == sorted(set(selected)) and set(selected) == set(binding["reviewed_bundles"]),
            "Reviewed selection closure")
    expected = {}
    for sid in selected:
        bundle = binding["reviewed_bundles"][sid]
        require(set(bundle) == {"packet", "review", "decision"} and
                bundle["decision"] == manifest["decisions"][sid], "Reviewed decision mismatch")
        d = validate_decision(inventory, encode(bundle["packet"]), bundle["review"], bundle["decision"],
                              executor_fingerprint=acquisition_fingerprint, now=binding["planned_at"])
        h = manifest["core"]["horizons"][sid]
        lo, hi = parse_utc(h["start"]), parse_utc(h["end"])
        require(parse_utc(d["scope"]["start"]) <= lo < hi <= parse_utc(d["scope"]["end"]),
                "Task horizon exceeds review")
        cuts, cursor = {lo, hi}, lo
        while cursor < hi:
            cursor = min(hi, cursor + timedelta(days=manifest["core"]["chunk_days"]))
            cuts.add(cursor)
            require(len(cuts) <= 4097, "Task bound")
        for window in d["configuration_windows"]:
            for point in (window["start"], window["end"]):
                if point is not None and lo < parse_utc(point) < hi:
                    cuts.add(parse_utc(point))
        points = sorted(cuts)
        for lo_part, hi_part in zip(points, points[1:]):
            owners = [w for w in d["configuration_windows"] if parse_utc(w["start"]) <= lo_part and
                      (w["end"] is None or hi_part <= parse_utc(w["end"]))]
            require(len(owners) == 1, "Incomplete or overlapping configuration coverage")
            start, end = format_utc(lo_part), format_utc(hi_part)
            identity = dict(schema_version=campaign.TASK, campaign_identity=manifest["campaign_identity"],
                campaign_version=campaign.VERSION, collector_fingerprint=acquisition_fingerprint,
                inventory_sha256=INVENTORY_SHA256, frozen_identity=inventory.identity(sid),
                native_authority_sha256=d["native_authority_sha256"],
                configuration_evidence_sha256=d["configuration_evidence_sha256"],
                configuration_ordinal=owners[0]["ordinal"], start=start, end=end,
                request_policy=campaign.POLICY)
            key = digest(identity)
            query = [("datastream_id", sid), ("time[$gte]", start), ("time[$lt]", end),
                     ("$sort[time]", "1"), ("$limit", "2016")]
            native = dict(task_id=key, identity=identity, scale_decision_sha256=d["scale"]["decision_sha256"],
                request=dict(method="GET", url="https://api.dendra.science/v2/datapoints?"+urlencode(query)),
                state="PLANNED", executable=False)
            expected[key] = dict(identity=inventory.identity(sid), start=start, end=end, native_task=native)
    require(tasks == expected and 0 < len(tasks) <= 128, "Task/source/stream/configuration identity mismatch")
    return manifest


def _seal(journal, key, state, inventory, fingerprint):
    """Recheck seal semantics without calling any journal mutation or provider."""
    task = journal.tasks[key]
    item = state["intervals"][key]
    require(item["state"] in ("complete_empty", "complete_nonempty") and item["complete"],
            "Unsealed or held interval")
    seal = item["complete"]
    require(len(seal["objects"]) == 1 and seal["interval_key"] == key and seal["run"] == item["runs"],
            "Seal identity/run mismatch")
    envelope = journal.completed(key)
    keys = seal["attempt_keys"]
    attempts = [state["attempts"][k] for k in keys]
    require(keys and len(keys) == len(set(keys)) and
            set(keys) == {k for k, a in state["attempts"].items() if a["interval_key"] == key},
            "Seal receipt closure")
    require(envelope["query_complete"] is True and envelope["datastream_id"] == task["identity"]["stream_id"] and
            envelope["requested_interval"] == dict(start_inclusive=task["start"], end_exclusive=task["end"]) and
            envelope["page_count"] == len(attempts) == len(envelope["pages"]), "Incomplete/mismatched envelope")
    cursor, end = parse_utc(task["start"]), parse_utc(task["end"])
    source_rows = []
    bundle = journal.binding["reviewed_bundles"][task["identity"]["stream_id"]]
    for page, attempt in zip(envelope["pages"], attempts):
        require(attempt["interval_key"] == key and attempt["run"] == seal["run"] and
                attempt["state"] == "received" and attempt["status"] == 200 and len(attempt["objects"]) == 1,
                "Held/incomplete receipt")
        validate_decision(inventory, encode(bundle["packet"]), bundle["review"], bundle["decision"],
                          executor_fingerprint=fingerprint, now=attempt["reserved_at"])
        descriptor = attempt["objects"][0]
        require(page["response_sha256"] == descriptor["sha256"] == attempt["response_sha256"] and
                page["response_bytes"] == descriptor["bytes"] == attempt["response_bytes"], "Page/receipt hash")
        require(parse_utc(page["requested_at_utc"]) <= parse_utc(attempt["reserved_at"]) <=
                parse_utc(attempt["at"]) <= parse_utc(page["retrieved_at_utc"]) <= parse_utc(seal["checked_at"]),
                "Receipt retrieval clock")
        raw = decode(journal.read_object(descriptor))
        require(type(raw.get("limit")) is int and 0 < raw["limit"] <= 2016 and
                isinstance(raw.get("data"), list) and len(raw["data"]) <= raw["limit"] and
                attempt["source_rows"] == len(raw["data"]), "Response row/limit integrity")
        require(parse_utc(attempt["cursor"]) == cursor, "Page cursor mismatch")
        previous = cursor
        for row in raw["data"]:
            stamp = parse_utc(row["t"])
            require(previous <= stamp < end and
                    row.get("datastream_id", task["identity"]["stream_id"]) == task["identity"]["stream_id"],
                    "Out-of-bounds source time or stream")
            previous = stamp
        source_rows.extend(raw["data"])
        if page is not envelope["pages"][-1]:
            require(len(raw["data"]) == raw["limit"] and previous > cursor, "Incomplete pagination")
            cursor = previous
        else:
            require(len(raw["data"]) < raw["limit"], "Full final page cannot seal")
    require(envelope["diagnostics"]["completion_reason"] in ("empty_page", "short_page_with_effective_limit"),
            "No pagination completion proof")
    rows, diagnostics = normalize_rows(source_rows, task["start"], task["end"])
    require(rows == envelope["rows"] and envelope["content_sha256"] == _content_hash(envelope) and
            all(envelope["diagnostics"].get(k) == v for k, v in diagnostics.items()), "Parsed/raw semantics mismatch")
    require(envelope["latest_observation_utc"] == (rows[-1]["t"] if rows else None) == seal["latest_source_observation"] and
            envelope["retrieval_first_utc"] == envelope["pages"][0]["retrieved_at_utc"] and
            envelope["retrieval_last_utc"] == envelope["pages"][-1]["retrieved_at_utc"] == seal["checked_at"] and
            seal["state"] == ("complete_nonempty" if rows else "complete_empty"), "Seal clock/state mismatch")
    return envelope, seal, attempts


def csv_bytes(rows, identity, scale):
    """Exactly core.R's six-column input; full rows remain in lineage JSON."""
    text = io.StringIO(newline="")
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(CSV_FIELDS)
    multiplier = scale.get("conversion_factor") if scale.get("normalized_percent_eligible") else None
    for row in rows:
        parse_utc(row["t"])
        require(row.get("datastream_id", identity["stream_id"]) == identity["stream_id"], "Mixed stream identity")
        require(row["value_status"] in ("number", "null", "missing", "invalid"), "Unknown native value status")
        value = row.get("v")
        if row["value_status"] == "number":
            require(type(value) in (int, float) and math.isfinite(value) and
                    (type(value) is not int or abs(value) <= 2**53), "Unsupported R numeric precision")
        alternative = any(a["value_status"] == "number" and multiplier is not None and
                          not 0 <= a["v"] * multiplier <= 100 for a in row.get("conflicting_values", []))
        writer.writerow((row["t"], identity["stream_id"], repr(value) if row["value_status"] == "number" else "",
                         row["value_status"], bool(row.get("duplicate_conflict")), alternative))
    return text.getvalue().encode("utf-8")


def historical_terminal(identity, rows, scale, *, as_of):
    """Historical interval termination is never a latest-observation witness."""
    now = parse_utc(as_of)
    if not rows:
        return None
    row = rows[-1]
    at = parse_utc(row["t"])
    require(at <= now, "Observation after as-of")
    numeric = row["value_status"] == "number" and not row.get("duplicate_conflict", False)
    native = row.get("v") if numeric else None
    percent = native * scale["conversion_factor"] if native is not None and scale["normalized_percent_eligible"] else None
    normalized_status = "resolved" if percent is not None else "withheld"
    if percent is not None and not math.isfinite(percent):
        percent, normalized_status = None, "nonfinite_conversion_withheld"
    return dict(schema_version=POINT_VERSION, representation="historical_terminal", identity=identity,
                source_timestamp=row["t"], source_fixed_pst_date=at.astimezone(PST).date().isoformat(),
                age_seconds=(now-at).total_seconds(), as_of=format_utc(now), native_value=native,
                normalized_percent=percent, normalized_status=normalized_status, value_status=row["value_status"],
                latest_witness=False, current_state_claim=False, publication_eligible=False)


def verify_sealed(sealed_root, *, manifest_sha256, inventory, acquisition_fingerprint):
    """Return inspected native records; no output and no acquisition mutation."""
    with Root(sealed_root) as root:
        entries = _manifest(root, manifest_sha256)
        require({"execution-binding.json", "source-binding.json"} <= set(entries), "Missing named source bindings")
        execution = decode(root.read("execution-binding.json", BODY_BYTES))
        source = decode(root.read("source-binding.json", BODY_BYTES))
    binding, tasks = execution["binding"], execution["tasks"]
    require(source["collector_fingerprint"] == acquisition_fingerprint and
            source["sources"] == binding["collector_sources"], "Acquisition checkpoint/source mismatch")
    _binding(binding, tasks, inventory, acquisition_fingerprint)
    records = []
    with Journal(sealed_root, binding, tasks, inspect_only=True) as journal:
        state = journal.snapshot()
        # Every byte read by the Journal must also be in the caller-pinned manifest.
        needed = {"registry/"+binding["campaign_id"]+".json", journal.prefix+"/manifest.json", journal.prefix+"/writer.lock"}
        needed.update(journal.prefix+"/"+p["path"] for p in decode(journal.fs.read(journal.prefix+"/manifest.json", BODY_BYTES))["plan"])
        for event in journal.events:
            needed.add(journal._event_path(event["sequence"]))
            needed.add("anchors/"+binding["campaign_id"]+f"/{event['sequence']:08d}.json")
            needed.update(journal.prefix+"/"+d["path"] for d in event["data"].get("objects", []))
        require(needed <= set(entries), "Journal object not in pinned evidence manifest")
        for key, task in sorted(tasks.items()):
            envelope, seal, attempts = _seal(journal, key, state, inventory, acquisition_fingerprint)
            event = [e for e in journal.events if e["kind"] == "sealed" and e["data"] == seal]
            require(len(event) == 1, "Ambiguous seal")
            bundle = binding["reviewed_bundles"][task["identity"]["stream_id"]]
            ordinal = task["native_task"]["identity"]["configuration_ordinal"]
            configuration = next(c for c in bundle["packet"]["configuration_evidence"]["configurations"] if c["ordinal"] == ordinal)
            records.append(dict(task_id=key, task=task, seal=seal, seal_record_sha256=event[0]["record_sha256"],
                envelope=envelope, receipts=attempts,
                configured_cadence_claim=configuration["fields"]["interval"], decision=bundle["decision"]))
    return dict(schema_version=VERSION, evidence_manifest_sha256=manifest_sha256,
        acquisition_fingerprint=acquisition_fingerprint, acquisition_checkpoint=source,
        execution_binding_sha256=digest(binding), task_binding_sha256=digest(tasks), records=records)


def configured_cadence(records):
    """A matching reviewed claim is a fallback, never an observation inference."""
    claims = [r["configured_cadence_claim"] for r in records]
    if not claims or any(c.get("validation") != "VALID_LOCAL_CADENCE_CLAIM" or
                         c.get("unit") != "millisecond" for c in claims):
        return None
    values = [c["value"] for c in claims]
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= 86400000 for v in values):
        return None
    return values[0]/1000 if all(v == values[0] for v in values) else None


def prepare_product(sealed_root, *, manifest_sha256, inventory, acquisition_fingerprint,
                    output_root, as_of, cadence_mode, rscript="Rscript"):
    """Verify all input before creating a fresh, local-only R preparation."""
    now = parse_utc(as_of)
    require(cadence_mode == "initialize", "Explicit fresh cadence initialization required; update/resume unsupported")
    verified = verify_sealed(sealed_root, manifest_sha256=manifest_sha256, inventory=inventory,
                             acquisition_fingerprint=acquisition_fingerprint)
    streams = {}
    for record in verified["records"]:
        sid = record["task"]["identity"]["stream_id"]
        streams.setdefault(sid, []).append(record)
    specifications, files = [], {}
    for sid, records in sorted(streams.items()):
        records.sort(key=lambda r: parse_utc(r["task"]["start"]))
        for a, b in zip(records, records[1:]):
            require(parse_utc(a["task"]["end"]) <= parse_utc(b["task"]["start"]), "Overlapping input seals")
        identity, scale = records[0]["task"]["identity"], records[0]["decision"]["scale"]
        require(all(r["task"]["identity"] == identity and r["decision"]["scale"] == scale for r in records),
                "Scientific identity or scale conflict")
        rows = [row for r in records for row in r["envelope"]["rows"]]
        csv = csv_bytes(rows, identity, scale)
        lineage = encode(dict(identity=identity, scale=scale, records=records))
        csv_path, lineage_path = "native/"+sid+".csv", "lineage/"+sid+".json"
        files[csv_path], files[lineage_path] = csv, lineage
        resolved = scale["normalized_percent_eligible"]
        require(not resolved or (identity["native_unit"] in ("Percent", "VolumetricWaterContent") and
                scale["conversion_factor"] == (1 if identity["native_unit"] == "Percent" else 100)),
                "Unsupported percent route; separately reviewed science required")
        specifications.append(dict(identity=identity, csv=dict(path=csv_path, sha256=sha(csv), bytes=len(csv)),
            lineage=dict(path=lineage_path, sha256=sha(lineage), bytes=len(lineage)),
            unit_normalization=dict(status="verified_percent_conversion" if resolved else "native_only_scale_unresolved",
                                    multiplier=scale["conversion_factor"] if resolved else None, offset=0 if resolved else None),
            daily_route="completed_daily" if resolved else "native_only", historical_terminal=historical_terminal(identity, rows, scale, as_of=as_of),
            configured_cadence_seconds=configured_cadence(records),
            intervals=[dict(task_id=r["task_id"], start=r["task"]["start"], end=r["task"]["end"],
                query_state="COVERED_EMPTY" if not r["envelope"]["rows"] else "COMPLETE_NONEMPTY",
                seal_record_sha256=r["seal_record_sha256"], content_sha256=r["envelope"]["content_sha256"],
                parsed_sha256=r["seal"]["objects"][0]["sha256"], row_count=len(r["envelope"]["rows"])) for r in records]))
    package = Path(__file__).resolve().parent
    science = dict(core_sha256=sha((package.parent/"core.R").read_bytes()),
                   wrapper_sha256=sha((package/"daily_prepare.R").read_bytes()),
                   collector_fingerprint=digest(source_binding()))
    require(science["core_sha256"] == verified["acquisition_checkpoint"]["sources"]["core.R"],
            "Daily numerical authority changed since acquisition; separate review required")
    handoff = dict(schema_version=VERSION, as_of=format_utc(now), source_scope="historical_sealed_intervals",
        cadence_mode=cadence_mode, cadence_policy="fresh_frozen_initialization_only_no_update_or_resume",
        science_binding=science, evidence_binding={k:v for k,v in verified.items() if k != "records"},
        streams=specifications, latest_instantaneous=[], publication_eligible=False, reference_band="not_computed")
    files["handoff.json"] = encode(handoff)
    out = Path(output_root)
    require(out.is_absolute() and out.name not in ("", ".", "..") and ".." not in out.parts,
            "Explicit fresh absolute output required")
    require(out != Path(sealed_root) and Path(sealed_root) not in out.parents,
            "Output cannot modify sealed evidence root")
    with Root(out.parent) as parent:
        # Atomic no-reuse directory creation; symlinks and existing outputs refuse.
        import os
        os.mkdir(out.name, 0o700, dir_fd=parent.fd)
    with Root(out) as destination:
        for name, body in files.items():
            destination.write_new(name, body, max(BODY_BYTES, len(body)))
    command = [rscript, "--vanilla", str(package/"daily_prepare.R"), str(out/"handoff.json"), str(out/"daily-output.json")]
    began = time.monotonic()
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        with Root(out) as destination:
            destination.write_new("r-receipt.json", encode(dict(command=command, exit_code=None,
                elapsed_seconds=time.monotonic()-began, outcome="R_TIMEOUT" if isinstance(exc, subprocess.TimeoutExpired)
                else "R_EXECUTION_UNAVAILABLE")), 32768)
        require(False, "Offline R execution unavailable or timed out; preserve preparation for review")
    with Root(out) as destination:
        destination.write_new("r-receipt.json", encode(dict(command=command, exit_code=completed.returncode,
            elapsed_seconds=time.monotonic()-began,
            stdout=completed.stdout[:8192], stderr=completed.stderr[:8192])), 32768)
        require(completed.returncode == 0, "Accepted R daily preparation failed; preserve output for review")
        daily = decode(destination.read("daily-output.json", BODY_BYTES))
        require(daily["schema_version"] == VERSION and daily["science_binding"] == science,
                "R science/source binding mismatch")
        result = dict(schema_version=VERSION, outcome="OFFLINE_DAILY_PREPARED", output_root=str(out),
            source_scope="historical_sealed_intervals", science_binding=science,
            evidence_manifest_sha256=manifest_sha256, stream_count=len(specifications),
            daily_summaries=daily["summaries"], publication_eligible=False, provider_requests=0)
        destination.write_new("result.json", encode(result), BODY_BYTES)
    return result
