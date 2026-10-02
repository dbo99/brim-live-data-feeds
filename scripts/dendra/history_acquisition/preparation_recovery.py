"""Offline review of an existing preparation job; never opens a Journal writer.

Original source identities, counters and windows remain historical facts. A
verified rejection projection can explain an unknown row receipt, but cannot
make that witness admissible or bypass any dispatch/capacity/source guard.
"""
from contextlib import ExitStack
import fcntl
import os
from pathlib import Path
import subprocess
import sys

from . import authority_package as ap, authority_witness as aw, eligibility
from . import local_job as l, metadata_acquisition as ma, provider_metadata, recovery
from .journal import utc_now
from .model import source_binding, PAGE_BYTES
from .safety import Root, Hold, require, encode, decode, digest, sha
from ..transport import parse_utc

VERSION = "dendra-existing-preparation-review-1"


def deny_sockets(event, unused):
    if event.startswith("socket."):
        raise Hold("Offline preparation recovery refuses network access")


def _files(root):
    result = {}
    for p in sorted(root.rglob("*")):
        require(not p.is_symlink(), "Historical evidence symlink refused")
        if p.is_file():
            with Root(p.parent) as fs:
                body = fs.read(p.name, 8*l.BODY)
            result[str(p.relative_to(root))] = dict(bytes=len(body), sha256=sha(body))
    return result


def _sources(c, old):
    """Verify old source bytes against local Git objects, never rebind them."""
    require(c["sources"]["checkpoint"] == ap.current_checkpoint() and
        c["sources"]["collector"] == digest(old) and
        c["sources"]["r_entrypoint_sha256"] == sha(l.ENTRY.read_bytes()), "Historical job source identity changed")
    if old == source_binding():
        return
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1")
    head = c["sources"]["checkpoint"]["head"]
    files = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", head, "--", "scripts/dendra"],
        cwd=l.REPO, env=env, text=True).splitlines()
    expected = {p.removeprefix("scripts/dendra/") for p in files if
        p.startswith("scripts/dendra/history_acquisition/") and p.endswith(".py") or
        p in ("scripts/dendra/transport.py", "scripts/dendra/core.R")}
    require(set(old) == expected, "Historical collector file closure changed")
    for name, wanted in old.items():
        body = subprocess.check_output(["git", "show", head+":scripts/dendra/"+name], cwd=l.REPO, env=env)
        require(sha(body) == wanted, "Historical collector differs from local checkpoint")


def _metadata(j, inv, authority, package, station, ids, old, now):
    specs = [ma.RosterRequestSpec("unit-vocabulary")] + [
        ma.RosterRequestSpec(kind, ids[0], station_id=station) for kind in ("station", "datastream-list")]
    expected = ma._binding(inv, authority, ids, ap.metadata_campaign_id(package, station), specs, old, package)
    require(j.inspect_only and (j.binding, j.tasks) == expected, "Historical station metadata binding changed")
    j.verify_records()
    require(j.snapshot()["counters"]["unknown_row_responses"] == 0, "Historical metadata unknown accounting HOLD")
    return {sid: ma._packet_evidence(j, sid, now=now, fingerprint=digest(old)) for sid in ids}


def validate_review_selection(request, review):
    """Selection gate only; existing science/source/placement guards still apply."""
    require(request.get("schema_version") == VERSION and request.get("review_request_sha256") ==
        digest({k:v for k,v in request.items() if k != "review_request_sha256"}), "Recovery review request integrity")
    rows = request["streams"]
    require(set(review) == {"job_id", "streams"} and review["job_id"] == request["job_id"] and
        set(review["streams"]) == set(rows), "Recovered review roster closure")
    for sid, row in rows.items():
        item = review["streams"][sid]
        if row["review_status"] != "REVIEW_REQUIRED":
            require(item == {"disposition": "EXCLUDE"}, "Held witness cannot enter an acquisition plan")
        elif item != {"disposition": "EXCLUDE"}:
            require(set(item) == {"source_review", "native_review", "placement_review", "scope"} and
                item["source_review"].get("disposition") == "ACCEPT_SOURCE_START" and
                item["native_review"].get("disposition") == "ACCEPT_NATIVE" and
                item["placement_review"].get("disposition") == "ACCEPT_PLACEMENT",
                "Explicit source/native/placement review or exclusion required")
    return review


def _collect(config_path, *, now):
    c = l.read(config_path); root = Path(c["root"]); authority_root = root/"authority"
    with Root(root) as fs, ExitStack() as stack:
        fd = fs.lock_fd("writer.lock")
        stack.callback(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        before = _files(root)
        saved = decode(fs.read("job.json", 8*l.BODY))
        require(saved["configuration"] == c and saved["job_id"] == digest(c), "Original job/configuration changed")
        require(not (root/"history").exists() and not (root/"review.json").exists(), "Preparation-only recovery required")
        package = l.read(authority_root/"package.json")
        registry = {p.stem for p in (authority_root/"registry").iterdir()}
        require(registry == {p.name for p in (authority_root/"campaigns").iterdir()}, "Original Journal registry closure changed")
        groups = ap.station_groups(package)
        first = ap.metadata_campaign_id(package, next(iter(groups)))
        first_header = l.read(authority_root/"campaigns"/first/"manifest.json", PAGE_BYTES)
        old = first_header["binding"]["collector_sources"]
        _sources(c, old)
        c, inv = l._config(c, c["sources"])
        expected = ap.make(inv, selected_ids=c["streams"], checkpoint=c["sources"]["checkpoint"])
        expected["collector_fingerprint"] = digest(old)
        expected["package_id"] = digest({k:v for k,v in expected.items() if k != "package_id"})
        require(package == expected, "Original package source/selection/limits changed")
        allowed = {ap.metadata_campaign_id(package,s) for s in groups} | {
            ap.witness_campaign_id(package,sid) for sid in c["streams"]}
        require(registry <= allowed, "Foreign Journal in original preparation")
        job = l.Job(c, inv, clock=l.Clock())
        job.fs = fs
        require(decode(fs.read("catalog.json",8*l.BODY)) == job.series_catalog(), "Original series/organization catalog changed")
        accounting = job.accounting(evidence_sources=old)
        approval = l.read(authority_root/"authorization.json")
        require(approval["package_id"] == package["package_id"] and
            0 < (parse_utc(approval["window_end"])-parse_utc(approval["window_start"])).total_seconds() <=
            package["ceilings"]["wall_seconds"], "Original package window changed")
        authority = provider_metadata.load_authority(inv,c["catalog"])
        catalog = job.series_catalog()["streams"]
        names = {series["id"]: series for station in decode(inv.source)["stations"] for series in station["catalog"]}
        rows, proposed, interpretations, operations = {}, {}, [], []
        for station, ids in groups.items():
            cid = ap.metadata_campaign_id(package,station)
            with recovery.open_evidence(str(authority_root),cid,inv) as mj:
                metas = _metadata(mj, inv, authority, package, station, ids, old, now)
                station_record, _ = ma._receipt(mj,ma._spec(mj.binding,"station",ids[0]))
                for a in mj.snapshot()["attempts"].values():
                    operations.append(dict(campaign_id=cid, **a))
            for sid in ids:
                meta = metas[sid]; identity = inv.identity(sid)
                witness_id = ap.witness_campaign_id(package,sid)
                row = dict(identity=identity, source_network="Dendra", source_platform="dendra",
                    source_station_id=station, source_stream_id=sid,
                    station_name=station_record["display_name"], series_name=names[sid]["label"],
                    series_name_provenance=dict(inventory_sha256=ma.INVENTORY_SHA256, stream_id=sid),
                    scientific_claims=meta["packet"]["scientific_claims"], native_value_unit=identity["native_unit"],
                    native_time_meaning="UTC t; preserve native timestamps; explicit review required",
                    position=dict(geometry=station_record["geometry"], geo_protected=station_record["geo_protected"],
                        provenance=meta["provenance"]["station"], crs_review="PENDING_EPSG_4326_VERIFICATION"),
                    source_organization=catalog[sid]["source_organization"],
                    series_metadata_reference=dict(path="catalog.json",stream_id=sid),
                    metadata=meta, witness=None, dispatch_ready=False, source_start_reviewed=False)
                row.update({k:catalog[sid]["source_organization"][k] for k in
                    ("subprovider_key", "subprovider_name", "subprovider_label")})
                if witness_id not in registry:
                    row.update(review_status="HELD_UNATTEMPTED", reason="witness_never_attempted",
                        attempted=False, attempt_spent=False, witness_evidence_exists=False)
                    proposed[sid] = dict(disposition="EXCLUDE")
                else:
                    with recovery.open_evidence(str(authority_root),witness_id,inv) as wj:
                        require(wj.binding["collector_sources"] == old and
                            wj.binding["metadata_packets"] == {sid:meta["packet"]}, "Original witness metadata/source changed")
                        wj.verify_records()
                        for a in wj.snapshot()["attempts"].values():
                            operations.append(dict(campaign_id=witness_id, **a))
                        state = wj.snapshot()
                        if any(a["state"] != "received" for a in state["attempts"].values()):
                            interpreted = recovery.rejected_empty_witness(wj,sid)
                            interpretations.append(interpreted)
                            row.update(review_status="HELD_REJECTED_KNOWN_ZERO", reason="empty_witness_missing_total",
                                attempted=True, attempt_spent=True, http_status=200, returned_row_count=0,
                                witness_admissible=False, recovery_interpretation=interpreted)
                            proposed[sid] = dict(disposition="EXCLUDE")
                        else:
                            require(state["counters"]["unknown_row_responses"] == 0, "Original witness unknown accounting HOLD")
                            witness = aw.evidence(wj,sid)
                            row.update(review_status="REVIEW_REQUIRED", witness=witness, attempted=True, attempt_spent=True)
                            packet = meta["packet"]
                            native = eligibility.propose(inv,encode(packet),packet_sha256=meta["packet_sha256"],
                                packet_source_fingerprint=digest(old),executor_fingerprint=digest(old),**c["scope"])
                            proposed[sid] = dict(scope=c["scope"], native_review=native,
                                source_review=dict(rule=aw.REVIEW, disposition="PENDING", reviewer_ref=None,
                                    reviewed_at=None,evidence_sha256=witness["evidence_sha256"]),
                                placement_review=dict(disposition="PENDING", reviewer_ref=None,
                                    station_metadata_sha256=packet["access_evidence"]["station_metadata_sha256"],
                                    configuration_evidence_sha256=digest(packet["configuration_evidence"]),
                                    depth_cm=identity["depth_cm"],crs="EPSG:4326",
                                    timestamp_meaning="UTC t; preserve native timestamps",scope=c["scope"],evidence=[]))
                rows[sid] = row
        operations.sort(key=lambda a:parse_utc(a["reserved_at"]))
        for ordinal, a in enumerate(operations,1):
            a["job_attempt_ordinal"] = ordinal
            require(parse_utc(approval["window_start"]) <= parse_utc(a["reserved_at"]) <=
                parse_utc(a["at"]) <= parse_utc(approval["window_end"]), "Original attempt outside package window")
        require(len(operations) == accounting["attempts"] and set(rows) == set(c["streams"]), "Recovered attempt/roster closure")
        require(job.accounting(evidence_sources=old) == accounting, "Original accounting changed during interpretation")
        after = _files(root)
        require(before == after, "Historical evidence changed during offline recovery")
        counts = dict(reviewable=sum(r["review_status"] == "REVIEW_REQUIRED" for r in rows.values()),
            held=sum(r["review_status"] != "REVIEW_REQUIRED" for r in rows.values()),total=len(rows))
        result = dict(schema_version=VERSION, generated_at=now, job_root=str(root),job_id=digest(c),
            original_sources=c["sources"], interpreting_collector_fingerprint=digest(source_binding()),
            source_rebinding=False, accounting=accounting, original_package_authorization=approval,
            whole_job_deadline_expired=parse_utc(now) >= parse_utc(accounting["window"]["deadline"]),
            package_window_expired=parse_utc(now) >= parse_utc(approval["window_end"]),
            counts=counts, streams=rows, original_archive_references=c["reuse"],
            original_archive_references_sha256=digest(c["reuse"]), recovery_interpretations=interpretations,
            operations=operations, unattempted_witnesses=[sid for sid,r in rows.items() if r["review_status"] == "HELD_UNATTEMPTED"],
            review_input=dict(job_id=digest(c),streams=proposed),
            review_policy="Explicit admit/exclude decisions required; five held streams must remain excluded",
            source_migration_authorized=False, dispatch_ready=False, provider_retry=False,
            new_attempts_reserved=0, provider_requests=0, network_attempts=0)
        result["review_request_sha256"] = digest(result)
        return result, dict(files_before=before,files_after=after,identical=True,
            accounting_before=accounting,accounting_after=accounting), interpretations


def recover_review(config_path, output_root):
    """R-supervised export only. Existing job/config/Journal bytes stay untouched."""
    sys.addaudithook(deny_sockets)
    c = l.read(config_path)
    output_root = Path(output_root)
    require(output_root.is_absolute() and output_root == output_root.resolve() and output_root.is_dir() and
        (l.REPO/".l01-soil-integration") in output_root.parents and
        output_root != Path(c["root"]) and Path(c["root"]) not in output_root.parents,
        "Separate existing task evidence directory required")
    result, checked, interpretations = _collect(config_path,now=utc_now())
    with Root(output_root) as fs:
        for name in ("CONSOLIDATED_REVIEW_REQUEST.json","RECOVERY_INTERPRETATION.json","IMMUTABLE_RECOVERY_CHECK.json"):
            require(name not in fs.list(), "Recovery output already exists; preserve it")
        fs.write_new("CONSOLIDATED_REVIEW_REQUEST.json",encode(result),8*l.BODY)
        fs.write_new("RECOVERY_INTERPRETATION.json",encode(interpretations),l.BODY)
        fs.write_new("IMMUTABLE_RECOVERY_CHECK.json",encode(checked),8*l.BODY)
    return dict(outcome="READY_FOR_L03_REVIEW",counts=result["counts"],accounting=result["accounting"],
        review_request_path=str(output_root/"CONSOLIDATED_REVIEW_REQUEST.json"),
        review_request_sha256=sha(encode(result)),provider_requests=0,network_attempts=0,new_attempts_reserved=0)
