"""Frozen resolved-roster packages over existing metadata and witness Journals.

No HTTP client, parser, new ledger or automatic review acceptance. A package is
an immutable selection/ceiling manifest. Each station retains its own bounded
metadata Journal; every witness has its own Journal. Their counters are the sole
attempt accounting. Reopening never repeats a spent child or renews its window.
"""
from datetime import timedelta
import fcntl
import os
from pathlib import Path
import subprocess
import threading
import urllib.error

from ..transport import parse_utc, format_utc
from .model import Inventory, INVENTORY_SHA256, PAGE_BYTES, source_binding
from .safety import Root, Hold, decode, digest, encode, require

VERSION = "dendra-roster-authority-package-1"
MAX_STREAMS = 32
MAX_STATIONS = 16
MAX_STATION_STREAMS = 16
POLICY = dict(concurrency=1, retries=0, redirects=0, minimum_spacing_seconds=1,
              response_bytes=8*1024**2, request_deadline_seconds=25,
              datastream_pages_per_station=1, list_limit=500,
              metadata_campaign_wall_seconds=150, witness_campaign_wall_seconds=60)


def current_checkpoint():
    """Local objects only; no refresh, optional locks or network subprocess."""
    root = Path(__file__).resolve().parents[3]
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1")
    values = subprocess.check_output(["git","rev-parse","HEAD","HEAD^{tree}"],
                                     cwd=root,env=env,text=True).splitlines()
    return dict(head=values[0],tree=values[1])


def station_groups(package):
    groups = {}
    for entry in package["selection"]:
        i = entry["identity"]
        groups.setdefault(i["station_id"],[]).append(i["stream_id"])
    return groups


def make(inventory, *, selected_ids, checkpoint):
    require(type(inventory) is Inventory and isinstance(selected_ids,(list,tuple)) and
            1 <= len(selected_ids) <= MAX_STREAMS and len(set(selected_ids)) == len(selected_ids),
            "Bounded explicit unique frozen selection required")
    require(checkpoint == current_checkpoint(), "Current local checkpoint required")
    roster = inventory.roster()
    require(set(selected_ids) <= set(roster), "Foreign stream outside frozen roster")
    selection = []
    for sid in sorted(selected_ids,key=lambda s:(roster[s]["station_id"],s)):
        i = roster[sid]
        require(i["native_unit"] in ("Percent","VolumetricWaterContent") and
                i["unit_status"] == "verified_percent_conversion", "Resolved frozen scale required")
        selection.append(dict(identity=i,measurement=dict(Medium="Soil",Variable="VolumetricWaterContent"),
            configuration_expectation=dict(depth_cm=i["depth_cm"],orientation=i["orientation"],
                temporal_review_required=True,historical_applicability="unknown_history"),
            scale_multiplier=1 if i["native_unit"] == "Percent" else 100,
            access_expectation=dict(public_level=3,is_hidden=False,protected_geometry_retained=False)))
    value = dict(schema_version=VERSION,checkpoint=checkpoint,collector_fingerprint=digest(source_binding()),
        inventory_sha256=INVENTORY_SHA256,selection=selection,selection_sha256=digest(selection),policy=POLICY)
    groups = station_groups(value)
    require(len(groups) <= MAX_STATIONS and max(map(len,groups.values())) <= MAX_STATION_STREAMS,
            "Station package capacity exceeded")
    s,n = len(groups),len(selection)
    value["ceilings"] = dict(stations=s,streams=n,vocabulary_requests=s,station_requests=s,
        datastream_requests=s,witness_requests=n,http_attempts=3*s+n,provider_bytes=(3*s+n)*8*1024**2,
        wall_seconds=s*150+n*60,metadata_campaigns=s,witness_campaigns=n)
    require(len(encode(value))+4096 <= PAGE_BYTES, "Package byte capacity")
    return decode(encode(dict(value,package_id=digest(value))))


def validate(package, inventory):
    require(isinstance(package,dict) and package.get("schema_version") == VERSION,
            "Frozen authority package version")
    expected = make(inventory,selected_ids=[e["identity"]["stream_id"] for e in package["selection"]],
                    checkpoint=package["checkpoint"])
    require(package == expected, "Package source/selection/order/policy hash mismatch")


def partition(inventory, *, selected_ids, checkpoint):
    """Deterministic bounded packages; never silently omit an input stream."""
    require(len(set(selected_ids)) == len(selected_ids) and selected_ids, "Unique explicit roster required")
    roster = inventory.roster()
    require(set(selected_ids) <= set(roster), "Foreign stream outside frozen roster")
    groups = {}
    for sid in sorted(selected_ids): groups.setdefault(roster[sid]["station_id"],[]).append(sid)
    packages, pending, stations = [],[],0
    for station,ids in sorted(groups.items()):
        # Keep stations together when they fit, avoiding duplicate metadata at
        # ordinary package boundaries. Oversize stations split explicitly.
        for offset in range(0,len(ids),MAX_STATION_STREAMS):
            chunk = ids[offset:offset+MAX_STATION_STREAMS]
            if pending and (len(pending)+len(chunk) > MAX_STREAMS or stations == MAX_STATIONS or offset):
                packages.append(make(inventory,selected_ids=pending,checkpoint=checkpoint))
                pending,stations = [],0
            pending.extend(chunk); stations += 1
    if pending: packages.append(make(inventory,selected_ids=pending,checkpoint=checkpoint))
    return packages


def metadata_campaign_id(package, station):
    require(station in station_groups(package), "Station outside package")
    return "metadata-"+digest(dict(package=package["package_id"],station=station))[:48]


def witness_campaign_id(package, sid):
    require(any(e["identity"]["stream_id"] == sid for e in package["selection"]), "Witness outside package")
    return "first-"+digest(dict(package=package["package_id"],stream=sid))[:48]


def hold_status(reason, *, witness=False):
    if witness: return "SOURCE_START_HOLD"
    if reason.startswith("privacy.") or reason.startswith("access."): return "PRIVACY_ACCESS_HOLD"
    if "cadence" in reason or "temporal" in reason or "config" in reason: return "CONFIGURATION_HOLD"
    if "unit" in reason: return "SCALE_HOLD"
    if any(x in reason for x in ("identity","association","station.id","depth_evidence","orientation_evidence")):
        return "SOURCE_ANOMALY_HOLD"
    return "CURRENT_METADATA_HOLD"


def reviewed_status(inventory, sid, *, metadata_journal, witness_journal,
                    source_review, native_bundle, now):
    """Validate caller-supplied accepted reviews; never issue/accept a review.

    No string claiming REVIEWED_SOURCE_START substitutes for Journal evidence.
    Both Journals must belong to the same explicit package and current source.
    """
    from . import metadata_acquisition as m, authority_witness as w, presentation as p, eligibility as e
    meta = m.packet_evidence(metadata_journal,sid,now=now)
    package = metadata_journal.binding["authority_package"]
    validate(package,inventory)
    require(witness_journal.binding["campaign_id"] == witness_campaign_id(package,sid), "Witness package mismatch")
    bound = w.evidence(witness_journal,sid)
    require(bound["collector_fingerprint"] == package["collector_fingerprint"] and
            witness_journal.binding["metadata_packets"][sid] == meta["packet"] and
            native_bundle["packet"] == meta["packet"], "Review packet/source mismatch")
    reviewed = p.review_journal_first(witness_journal,sid,review=source_review,as_of=now)
    readiness = e.dispatch_readiness(inventory,sid,source_start_authority=reviewed["state"],
        bundle=native_bundle,executor_fingerprint=package["collector_fingerprint"],now=now,
        horizon=native_bundle["review"]["scope"])
    ready = readiness["dispatch_readiness"] == "DISPATCH_READY" and reviewed["state"] == p.REVIEWED
    return dict(status="AUTHORITY_READY" if ready else "SOURCE_START_HOLD" if reviewed["state"] != p.REVIEWED else "OTHER_HOLD",
        identity=inventory.identity(sid),package_id=package["package_id"],metadata_evidence_sha256=meta["evidence_sha256"],
        source_start=reviewed,native_review_sha256=digest(native_bundle["review"]),
        native_decision_sha256=digest(native_bundle["decision"]),readiness=readiness,
        expires_at=native_bundle["decision"].get("valid_until"))


def dispositions(package, inventory, *, metadata_journals, witness_journals, reviews, now):
    """Read-only per-stream closure, including unstarted work after a HOLD.

    Inputs are open, verified Journals and explicitly supplied reviews, never
    claimed ready status strings. Missing evidence stays visible. Integrity and
    binding failures propagate; a caller must not convert corruption to absence.
    """
    from . import metadata_acquisition as m, authority_witness as w
    validate(package,inventory)
    groups = station_groups(package)
    ids = {e["identity"]["stream_id"] for e in package["selection"]}
    require(set(metadata_journals) <= set(groups) and set(witness_journals) <= ids and set(reviews) <= ids,
            "Disposition inputs outside package")
    results = {}
    for station,selected in groups.items():
        journal = metadata_journals.get(station)
        outcomes = {}
        if journal is not None:
            require(journal.binding.get("authority_package") == package and
                    journal.binding["campaign_id"] == metadata_campaign_id(package,station), "Metadata package mismatch")
            journal.verify_records()
            attempts = journal.snapshot()["attempts"].values()
            if any(a["cursor"] == "datastream-list" and a["state"] == "received" and
                   a.get("details",{}).get("error_code") is None for a in attempts):
                outcomes = m.station_results(journal,now=now)
        for sid in selected:
            item = outcomes.get(sid)
            result = dict(identity=inventory.identity(sid),status="CURRENT_METADATA_HOLD",
                needs_fresh_metadata=True,needs_source_start_witness=True,
                source_start_authority="UNKNOWN_SOURCE_START",dispatch_readiness="NOT_READY")
            if item is not None and "packet" not in item:
                result.update(status=hold_status(item["reason"]),reason=item["reason"])
            if item is not None and "packet" in item:
                result.update(status="SOURCE_START_HOLD",needs_fresh_metadata=False)
                witness = witness_journals.get(sid)
                if witness is not None:
                    require(witness.binding["campaign_id"] == witness_campaign_id(package,sid) and
                            witness.binding["metadata_packets"] == {sid:item["packet"]}, "Witness package/packet mismatch")
                    witness.verify_records()
                    attempts = witness.snapshot()["attempts"].values()
                    if any(a["state"] == "received" and a.get("details",{}).get("error_code") is None for a in attempts):
                        proof = w.evidence(witness,sid)
                        require(proof["collector_fingerprint"] == package["collector_fingerprint"], "Witness source mismatch")
                        result["needs_source_start_witness"] = proof["result"]["state"] != "FIRST_OBSERVATION"
                        if sid in reviews:
                            review = reviews[sid]
                            checked = reviewed_status(inventory,sid,metadata_journal=journal,witness_journal=witness,
                                source_review=review["source_review"],native_bundle=review["native_bundle"],now=now)
                            result.update(status=checked["status"],review=checked,
                                source_start_authority=checked["source_start"]["state"],
                                dispatch_readiness=checked["readiness"]["dispatch_readiness"])
            results[sid] = result
    return results


def run(root, package, inventory, authority, *, authorization, executor, wait, now, monotonic):
    """Explicit future live caller or synthetic tests; no implicit network.

    Restart reuses completed children and leaves any partially spent child HOLD.
    Unstarted children retain their deterministic IDs and original package window.
    Service/transport/unknown-accounting/integrity failures stop the package.
    Local accounted admission failures leave unrelated children available.
    """
    validate(package,inventory)
    require(threading.current_thread() is threading.main_thread() and callable(executor) and callable(wait),
            "Serial explicit package executor required")
    require(isinstance(authorization,dict) and set(authorization) == {
        "package_id","approval_reference","window_start","window_end"} and
        authorization["package_id"] == package["package_id"] and
        isinstance(authorization["approval_reference"],str) and 0 < len(authorization["approval_reference"]) <= 160,
        "Explicit exact package approval required")
    start,end = map(parse_utc,(authorization["window_start"],authorization["window_end"]))
    require(start <= parse_utc(now()) < end and (end-start).total_seconds() <= package["ceilings"]["wall_seconds"],
            "Original bounded package window required")
    with Root(root) as fs:
        # Exclusive creation/verification, never replace an earlier manifest or
        # authorization. An interrupted initialization is a HOLD, not a reset.
        if "package.json" not in fs.list():
            require(not fs.list(), "Fresh package root must be empty")
            fs.write_new("package.json",encode(package),PAGE_BYTES)
            fs.write_new("authorization.json",encode(authorization),4096)
            fs.write_new("package.lock",b"",0)
        require(fs.read("package.json",PAGE_BYTES) == encode(package) and
                fs.read("authorization.json",4096) == encode(authorization), "Package restart binding changed")
        fd = fs.lock_fd("package.lock")
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return _run_locked(root,fs,package,inventory,authority,authorization,executor,wait,now,monotonic)
        finally:
            os.close(fd)


def _run_locked(root, fs, package, inventory, authority, approval, executor, wait, now, monotonic):
    from . import metadata_acquisition as m, authority_witness as w
    from .journal import Journal
    from .provider_adapter import WitnessAdapter, MetadataAdmissionHold, WitnessAdmissionHold
    end = parse_utc(approval["window_end"])
    existing = set(fs.list("registry")) if "registry" in fs.list() else set()
    allowed = {metadata_campaign_id(package,s)+".json" for s in station_groups(package)} | {
        witness_campaign_id(package,e["identity"]["stream_id"])+".json" for e in package["selection"]}
    require(existing <= allowed, "Foreign Journal in package root")
    last_start = None
    counts = dict(attempts=0,response_bytes=0)
    # The preserved Journals, not a second counter store, establish aggregate
    # budget and spacing across process/campaign boundaries.
    for name in sorted(existing):
        header = decode(fs.read("campaigns/"+name[:-5]+"/manifest.json",PAGE_BYTES))
        with Journal(root,header["binding"],{},inspect_only=True,inventory=inventory,now=now,monotonic=monotonic) as j:
            j.verify_records()
            require(j.binding["collector_sources"] == source_binding(), "Package child source changed")
            c = j.snapshot()["counters"]
            require(c["unknown_row_responses"] == 0, "Package unknown accounting requires review")
            for k in counts: counts[k] += c[k]
            for ev in j.events:
                if ev["kind"] == "started":
                    at = parse_utc(ev["at"])
                    require(parse_utc(approval["window_start"]) <= at < end, "Child outside original window")
                    last_start = max(last_start,at) if last_start else at
                elif ev["kind"] == "received":
                    # A durable start precedes dispatch validation. On restart,
                    # receipt time is a conservative floor for actual dispatch.
                    at = parse_utc(ev["at"])
                    last_start = max(last_start,at) if last_start else at
            _global_guard(j)

    def pace():
        if last_start is not None:
            delay = max(0,1-(parse_utc(now())-last_start).total_seconds())
            if delay: wait(delay)
        require(parse_utc(now()) < end, "Package window exhausted")

    def dispatch(request,timeout):
        nonlocal last_start
        began = monotonic()
        deadline = min(end,parse_utc(now())+timedelta(seconds=timeout))
        validate(package,inventory)
        at = parse_utc(now())
        # Each child request must pace against the same post-validation marker
        # as this guard. Validation and waiting spend the incoming time budget.
        delay = max(0,1-(at-last_start).total_seconds()) if last_start is not None else 0
        remaining = min(timeout-(monotonic()-began),(deadline-at).total_seconds())
        require(delay < remaining, "Package dispatch deadline cannot fit spacing")
        if delay: wait(delay)
        at = parse_utc(now())
        require(at < end and (last_start is None or (at-last_start).total_seconds() >= 1), "Package request-start spacing/window")
        remaining = min(timeout-(monotonic()-began),(deadline-at).total_seconds())
        require(remaining > 0, "Package dispatch deadline exhausted")
        require(counts["attempts"] < package["ceilings"]["http_attempts"], "Package attempt ceiling")
        counts["attempts"] += 1
        last_start = at
        return executor(request,timeout=remaining)

    def child_approval(j,seconds):
        start = parse_utc(now())
        return dict(binding_sha256=j.binding_sha,approval_reference=approval["approval_reference"],
            window_start=format_utc(start),window_end=format_utc(min(end,start+timedelta(seconds=seconds))))

    results = {}
    for station,ids in station_groups(package).items():
        cid = metadata_campaign_id(package,station)
        binding,tasks = m.prepare(inventory,authority,selected_ids=ids,campaign_id=cid,package=package)
        with Journal(root,binding,tasks,create=cid+".json" not in existing,inventory=inventory,now=now,monotonic=monotonic) as j:
            if j.snapshot()["attempts"]:
                # No retry/replayed metadata; read only complete receipts.
                try: outcomes = m.station_results(j,now=now())
                except Hold: outcomes = {s:dict(outcome="HOLD",reason="spent_metadata_review_required") for s in ids}
            else:
                pace()
                try: outcomes = m.MetadataAdapter(j).run(executor=dispatch,wait=wait,authorization=child_approval(j,150))
                except (MetadataAdmissionHold,urllib.error.HTTPError) as exc:
                    _global_guard(j)
                    reason = exc.diagnostic["reason"]["code"] if isinstance(exc,MetadataAdmissionHold) else "http_status_"+str(exc.code)
                    outcomes = {s:dict(outcome="HOLD",reason=reason) for s in ids}
            for sid,item in outcomes.items():
                results[sid] = dict(status="SOURCE_START_HOLD" if "packet" in item else hold_status(item["reason"]),
                    metadata=item,source_start_authority="UNKNOWN_SOURCE_START",dispatch_readiness="NOT_READY")
        for sid in ids:
            if "packet" not in outcomes[sid]: continue
            cid = witness_campaign_id(package,sid)
            if cid+".json" in existing:
                binding = decode(fs.read("campaigns/"+cid+"/manifest.json",PAGE_BYTES))["binding"]
                require(binding["metadata_packets"] == {sid:outcomes[sid]["packet"]}, "Restart witness packet changed")
                tasks = {}
            else:
                binding,tasks = w.prepare(inventory,campaign_id=cid,packets={sid:outcomes[sid]["packet"]},as_of=now())
            with Journal(root,binding,tasks,create=cid+".json" not in existing,inventory=inventory,now=now,monotonic=monotonic) as j:
                if j.snapshot()["attempts"]:
                    try: results[sid]["witness"] = w.evidence(j,sid)
                    except Hold: results[sid]["witness_hold"] = "spent_witness_review_required"
                else:
                    pace()
                    try: results[sid]["witness"] = WitnessAdapter(j).run(executor=dispatch,wait=wait,authorization=child_approval(j,60))[sid]
                    except (WitnessAdmissionHold,urllib.error.HTTPError):
                        _global_guard(j)
                        results[sid]["witness_hold"] = "witness_admission_hold"
                # Acquisition never silently accepts source-start/native review.
                results[sid]["review_required"] = True
    require(set(results) == {e["identity"]["stream_id"] for e in package["selection"]}, "Result selection closure")
    return results


def _global_guard(journal):
    """Local admission failures may isolate; ambiguous transport does not."""
    journal.verify_records()
    require(not journal.damage and journal.snapshot()["counters"]["unknown_row_responses"] == 0,
            "Package integrity/unknown accounting HOLD")
    for key in ("attempts","logical_requests","response_bytes","source_rows"):
        require(journal.snapshot()["counters"][key] <= journal.binding["budgets"][key], "Package child budget exceeded")
    for a in journal.snapshot()["attempts"].values():
        d = a.get("details",{})
        require(a["state"] not in ("reserved","started") and a.get("status") not in (None,408,429) and
                not a.get("service_failure") and d.get("error_code") not in ("transport","deadline","body_limit","budget","redirect"),
                "Package spent/ambiguous service failure requires review")
