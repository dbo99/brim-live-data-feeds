"""Explicit offline authority transfer; original Journals remain the ledger.

No HTTP, implicit acceptance, renewed metadata/review times or donated counters.
The config pins the original review. A derived record pins the serialized new
job identity, avoiding any config/derived-record hash cycle.
"""
from copy import deepcopy
from pathlib import Path

from . import local_job as l, preparation_recovery as pr, authority_package as ap
from . import authority_witness as aw, metadata_acquisition as ma, eligibility, presentation
from . import provider_metadata, recovery
from .model import source_binding
from .safety import Root, require, sha, digest, encode
from ..transport import parse_utc

VERSION = "dendra-offline-authority-import-1"
CONFIG_VERSION = "dendra-local-native-job-2"
REVIEW_VERSION = "l03-explicit-offline-review-result-1"
HELD = {"65355b8c5c0d5f806969a8ea", "65355b8d8081876e27c95d31",
        "65355b8dd07087215fd59748", "65355b8dd070878668d5974a", "65355b8e2148dbec414875a4"}
# These modules assemble/interpret accounting, reference or supervisor bindings.
# All other captured parser/science/Journal/policy source bytes must match.
ASSEMBLY = {"local_job.py", "preparation_recovery.py", "evidence_import.py",
            "metadata_acquisition.py", "provider_adapter.py", "recovery.py",
            "witness_diagnostic.py", "sealed_history.py"}


def reference(path):
    path = Path(path).absolute()
    return dict(path=str(path), sha256=sha(path.read_bytes()))


def pinned(ref, *, parse=True):
    require(type(ref) is dict and set(ref) == {"path", "sha256"}, "Exact import reference required")
    path = Path(ref["path"])
    with Root(path.parent) as fs:
        body = fs.read(path.name, 8*l.BODY)
    require(sha(body) == ref["sha256"], "Imported evidence/review hash changed")
    return l.decode(body) if parse else body


def pins(c):
    spec = c["authority_import"]
    require(set(spec) == {"version", "donor_config", "review_request", "final_review", "recovery"} and
            spec["version"] == VERSION, "Exact authority import fields/version")
    return {k:pinned(spec[k]) for k in ("donor_config", "review_request", "final_review", "recovery")}


def compatible(old):
    current = source_binding()
    require(set(old) <= set(current), "Unsupported historical collector file closure")
    changed = sorted(k for k in set(current)|set(old) if current.get(k) != old.get(k))
    require(all(k.startswith("history_acquisition/") and Path(k).name in ASSEMBLY for k in changed),
            "Captured parser/science/Journal/policy source is incompatible")
    return dict(rule=VERSION, capture_sources=old, execution_sources=current, changed_assembly_modules=changed,
                policy_validation="Current metadata, witness, source-start, native and placement validators applied to original bytes")


def placement(inv, sid, packet, station, place, scope):
    require(set(place) == {"disposition", "reviewer_ref", "station_metadata_sha256", "configuration_evidence_sha256",
            "depth_cm", "crs", "timestamp_meaning", "evidence", "scope"} and
        place["disposition"] == "ACCEPT_PLACEMENT" and isinstance(place["reviewer_ref"],str) and
        bool(place["reviewer_ref"]) and place["scope"] == scope and place["crs"] == "EPSG:4326" and
        place["timestamp_meaning"] == "UTC t; preserve native timestamps" and
        type(place["depth_cm"]) in (int,float) and place["depth_cm"] == inv.identity(sid)["depth_cm"] and
        place["station_metadata_sha256"] == packet["access_evidence"]["station_metadata_sha256"] and
        place["configuration_evidence_sha256"] == digest(packet["configuration_evidence"]),
        "Imported depth/location/deployment review changed")
    require(station["geometry"] is not None and station["geo_protected"] is False,
            "Imported public station geometry required")
    require(type(place["evidence"]) is list and 0 < len(place["evidence"]) <= 8, "Placement evidence required")
    for ref in place["evidence"]:
        pinned(ref,parse=False)


def stream(c, inv, request, review, sid, *, now):
    require(sid in c["streams"] and sid not in HELD and review["streams"][sid]["decision"] == "ADMIT",
            "Held/unselected stream cannot dispatch")
    original = request["streams"][sid]; row = review["streams"][sid]
    require(original["review_status"] == "REVIEW_REQUIRED" and original["witness"] is not None,
            "Imported successful witness required")
    root = Path(request["job_root"])/"authority"; package = l.read(root/"package.json")
    station = inv.identity(sid)["station_id"]
    with recovery.open_evidence(str(root), ap.metadata_campaign_id(package,station), inv) as mj, \
            recovery.open_evidence(str(root), ap.witness_campaign_id(package,sid), inv) as wj:
        old = mj.binding["collector_sources"]; compatible(old)
        authority = provider_metadata.load_authority(inv,c["catalog"])
        ids = ap.station_groups(package)[station]
        meta = pr._metadata(mj,inv,authority,package,station,ids,old,now)[sid]
        require(meta == original["metadata"] and aw.evidence(wj,sid) == original["witness"] and
                wj.binding["collector_sources"] == old and wj.binding["metadata_packets"] == {sid:meta["packet"]},
                "Imported metadata/witness binding changed")
        selection = review["original_job_review_selection"]["streams"][sid]
        require(selection == dict(scope=row["native_review"]["scope"],
                    **{k:row[k] for k in ("source_review", "native_review", "placement_review")}),
                "Original accepted review fields changed")
        scope = selection["scope"]; lo,hi = map(parse_utc,(scope["start"],scope["end"]))
        require(parse_utc(c["scope"]["start"]) <= lo < hi <= parse_utc(c["scope"]["end"]), "Imported review scope changed")
        source = presentation.review_journal_first(wj,sid,review=selection["source_review"],as_of=now)
        require(source["state"] == presentation.REVIEWED and parse_utc(source["start"]) <= lo and
                source["start"] == row["source_start"], "Imported source-start review HOLD")
        original_native = selection["native_review"]
        require(original_native["disposition"] == "ACCEPT_NATIVE", "Explicit native review required")
        eligibility.validate_decision(inv,encode(meta["packet"]),original_native,row["native_decision"],
            executor_fingerprint=review["repair_sources"]["collector"],now=now)
        native = dict(original_native, executor_fingerprint=c["sources"]["collector"])
        # Review/capture timestamps and expiry stay original. This is a derived
        # assessment at the original review instant, explicitly linked below.
        decision = eligibility.decide(inv,encode(meta["packet"]),native,
            executor_fingerprint=c["sources"]["collector"],now=row["native_decision"]["evaluated_at"])
        eligibility.validate_decision(inv,encode(meta["packet"]),native,decision,
            executor_fingerprint=c["sources"]["collector"],now=now)
        station_record,_ = ma._receipt(mj,ma._spec(mj.binding,"station",sid))
        placement(inv,sid,meta["packet"],station_record,selection["placement_review"],scope)
        require(row["identity_review"]["frozen_identity"] == inv.identity(sid) and
                row["identity_review"]["source_organization"] == original["source_organization"] ==
                l.attribution(c,inv.identity(sid)), "Imported identity/subprovider changed")
        accepted = dict(selection,native_review=native)
        return dict(packet=meta["packet"],review=native,decision=decision), scope, accepted


def validate(c, inv, *, now):
    """Read-only full donor closure and original review; no new provider window."""
    require(c["version"] == CONFIG_VERSION and c["sources"] == l.sources(), "Import execution source changed")
    inputs = pins(c); donor,request,review = (inputs[k] for k in ("donor_config","review_request","final_review"))
    require(review["schema_version"] == REVIEW_VERSION and request["schema_version"] == pr.VERSION and
            request["review_request_sha256"] == digest({k:v for k,v in request.items() if k != "review_request_sha256"}),
            "Imported review/request version or digest changed")
    require(c["authority_import"]["review_request"] == review["consolidated_request"] and
            c["authority_import"]["recovery"] == review["recovery_interpretation"] and
            review["original_selection"] == donor["streams"] and
            review["original_selection_sha256"] == digest(donor["streams"]) and
            review["original_job_id"] == request["job_id"] == digest(donor) and
            review["original_sources"] == request["original_sources"] == donor["sources"] and
            request["job_root"] == donor["root"], "Imported original job/selection binding changed")
    rows = review["streams"]; admitted = [s for s in donor["streams"] if rows[s]["decision"] == "ADMIT"]
    held = {s for s in rows if rows[s]["decision"] == "HOLD"}
    require(set(rows) == set(request["streams"]) == set(donor["streams"]) and
            admitted == review["admitted_streams"] == c["streams"] and held == HELD and
            set(admitted).isdisjoint(HELD) and
            all(r["decision"] in ("ADMIT","HOLD") for r in rows.values()), "Imported admitted/held roster closure changed")
    synthetic = c["fixture"] is not None
    require(synthetic == (donor["fixture"] is not None), "Synthetic evidence cannot authorize a real job")
    require(synthetic or (len(rows) == 28 and len(admitted) == 23), "Exact real 28/23 CDFW selection required")
    require(review["counts"] == dict(original=len(rows),admitted=len(admitted),excluded=0,held=len(held)),
            "Imported review counts changed")
    pr.validate_review_selection(request,review["original_job_review_selection"])
    prior_config = pinned(review['config'])
    require(prior_config['sources'] == review['repair_sources'] and prior_config['streams'] == admitted and
            prior_config['scope'] == c['scope'] and
            review['bulk_review_input'] == dict(job_id=digest(prior_config),
                streams={s:review['original_job_review_selection']['streams'][s] for s in admitted}),
            'Original disabled-config review identity changed')
    require(c["reuse"] == donor["reuse"] == request["original_archive_references"] and
            digest(c["reuse"]) == request["original_archive_references_sha256"] and
            c["attribution"] == {s:donor["attribution"][s] for s in admitted} and
            c["organization_labels"] == donor["organization_labels"], "Imported archive/organization references changed")
    # Preserve and verify the entire original preparation, including the spent
    # rejection. Unsupported unknown accounting remains a HOLD in _collect.
    recovered,checked,interpretations = pr._collect(Path(c["authority_import"]["donor_config"]["path"]),now=now)
    require(recovered["streams"] == request["streams"] and recovered["operations"] == request["operations"] and
            recovered["accounting"] == request["accounting"] and checked["identical"] and
            interpretations == inputs["recovery"] == request["recovery_interpretations"],
            "Imported original receipts/accounting/recovery changed")
    first = next(iter(ap.station_groups(l.read(Path(donor["root"])/"authority/package.json"))))
    package = l.read(Path(donor["root"])/"authority/package.json")
    old = l.read(Path(donor["root"])/"authority/campaigns"/ap.metadata_campaign_id(package,first)/"manifest.json")["binding"]["collector_sources"]
    compatibility = compatible(old)
    bundles,scopes,accepted = {},{},{}
    for sid in admitted:
        bundles[sid],scopes[sid],accepted[sid] = stream(c,inv,request,review,sid,now=now)
    record = dict(schema_version=VERSION,job_id=digest(c),serialized_config_sha256=digest(c),
        execution_sources=c["sources"],original_review=c["authority_import"]["final_review"],
        import_references=c["authority_import"],original_job_id=request["job_id"],original_sources=request["original_sources"],
        historical_preparation_accounting=request["accounting"],historical_package_authorization=request["original_package_authorization"],
        original_selection_sha256=digest(donor["streams"]),admitted_streams=admitted,held_streams=sorted(held),
        scope=c["scope"],compatibility=compatibility,review=dict(job_id=digest(c),streams=accepted),
        original_native_review_hashes={s:digest(rows[s]["native_review"]) for s in admitted},
        original_native_decision_hashes={s:digest(rows[s]["native_decision"]) for s in admitted},
        metadata_witness_requests=0,counters_transferred=False,capture_times_renewed=False,
        next_expiry=min((b["decision"]["valid_until"] for b in bundles.values()),key=parse_utc))
    return record,bundles,scopes,request,review


def plan(c, inv, validated):
    """Use the actual job planner in memory; do not initialize any output root."""
    record,bundles,scopes,_,_ = validated
    job = l.Job(c,inv,clock=l.Clock()); buffers = {"review.json":record["review"]}
    job.put = lambda name,value:buffers.__setitem__(name,value)
    job.get = lambda name:buffers[name]
    summary = job.make_plan(bundles,scopes)
    return summary,buffers


def write_new(path, value):
    path = Path(path).absolute()
    require((l.REPO/".l01-soil-integration") in path.parents, "Private task output required")
    with Root(path.parent) as fs:
        fs.write_new(path.name,encode(value),8*l.BODY)


def bind(template, *, donor_config, request, review, recovery_ref, output, job_root, now):
    c = l.read(template);l._config(c,c["sources"])
    require(c["enabled"] is False and not Path(job_root).exists(), "Disabled uninitialized import destination required")
    c = deepcopy(c);c.update(version=CONFIG_VERSION,sources=l.sources(),enabled=False,root=str(Path(job_root).absolute()))
    c["authority_import"] = dict(version=VERSION,donor_config=reference(donor_config),review_request=reference(request),
        final_review=reference(review),recovery=reference(recovery_ref))
    c,inv = l._config(c,l.sources());validated = validate(c,inv,now=now);summary,buffers = plan(c,inv,validated)
    write_new(output,c);write_new(str(output)+".import.json",dict(binding=validated[0],planning=summary,
        plan=buffers["plan.json"],plan_binding=buffers["plan-binding.json"],asset_map=buffers["asset-map.json"]))
    return dict(outcome="BOUND_DISABLED_IMPORT",config_path=str(output),config_sha256=digest(c),
        binding_path=str(output)+".import.json",**summary,next_expiry=validated[0]["next_expiry"])


def finalize(config_path, output, *, now):
    c,inv = l.config(config_path)
    require(c["version"] == CONFIG_VERSION and not c["enabled"] and not Path(c["root"]).exists(),
            "Finalize only a disabled uninitialized imported job")
    validate(c,inv,now=now)
    enabled = dict(c,enabled=True);validated = validate(enabled,inv,now=now)
    summary,buffers = plan(enabled,inv,validated)
    write_new(output,enabled);write_new(str(output)+".import.json",dict(binding=validated[0],planning=summary,
        plan=buffers["plan.json"],plan_binding=buffers["plan-binding.json"],asset_map=buffers["asset-map.json"]))
    return dict(outcome="FINALIZED_ENABLED_NOT_INITIALIZED",config_path=str(output),config_sha256=digest(enabled),
        job_id=digest(enabled),binding_path=str(output)+".import.json",**summary)
