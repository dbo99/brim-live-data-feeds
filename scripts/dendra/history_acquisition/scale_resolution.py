"""Offline, reviewed scale assertions; no acquisition, conversion or receipt writes.

Hash binding establishes which bytes were reviewed, not their authority or truth.
Callers must review exact-stream semantics and historical scope before binding
primary assertions. This module never infers scale from source values or siblings.
"""
from dataclasses import dataclass, field

from ..transport import parse_utc, format_utc
from .model import Inventory, INVENTORY_SHA256, ID, HASH, NAME
from .safety import Hold, require, encode, decode, digest, sha

POLICY_VERSION = "dendra-scale-policy-1"
EVIDENCE_VERSION = "dendra-scale-evidence-1"
DECISION_VERSION = "dendra-scale-decision-1"
STATE_VERSION = "dendra-scale-state-1"
MAX_EVIDENCE = 64
MAX_STATE_EVIDENCE = 1024
STATE_BYTES = 2 * 1024**2
PRIMARY = frozenset({"datastream_metadata", "unit_dictionary",
                     "sensor_output_configuration", "provider_scale_statement",
                     "authoritative_equivalent"})
SUPPORTING = frozenset({"range_distribution", "sister_stream", "sensor_family"})


def _scope(value, *, request=False):
    require(isinstance(value, dict), "Explicit temporal applicability required")
    kind = value.get("kind")
    allowed = {"whole_history", "interval"} if request else {"whole_history", "interval", "unknown_history"}
    require(isinstance(kind, str) and kind in allowed, "Unsupported temporal applicability")
    if kind == "interval":
        require(set(value) == {"kind", "start", "end"}, "Interval fields")
        try:
            lo, hi = parse_utc(value["start"]), parse_utc(value["end"])
        except (ValueError, TypeError) as exc:
            raise Hold("Explicit UTC interval required") from exc
        require(lo < hi, "Empty or reversed scale interval")
        return dict(kind=kind, start=format_utc(lo), end=format_utc(hi))
    require(set(value) == {"kind"}, "Scope fields")
    return dict(kind=kind)


def _claim(value):
    require(isinstance(value, dict) and set(value) == {
        "schema_version", "station_id", "stream_id", "role", "kind", "scale",
        "applicability", "source"}, "Scale evidence fields")
    require(value["schema_version"] == EVIDENCE_VERSION, "Scale evidence version")
    require(all(isinstance(value[k], str) and ID.fullmatch(value[k])
                for k in ("station_id", "stream_id")), "Evidence exact identity required")
    role = value["role"]
    require(role in ("primary", "supporting") and isinstance(value["kind"], str) and
            value["kind"] in (PRIMARY if role == "primary" else SUPPORTING), "Evidence hierarchy")
    require(value["scale"] in ("percent", "fraction", "ambiguous", None), "Scale claim category")
    source = value["source"]
    require(isinstance(source, dict) and set(source) == {"ref", "sha256", "version"}, "Evidence source binding")
    require(isinstance(source["sha256"], str) and HASH.fullmatch(source["sha256"]), "Evidence source hash")
    require(isinstance(source["version"], str) and NAME.fullmatch(source["version"]), "Evidence source version")
    ref = source["ref"]
    require(isinstance(ref, str) and 0 < len(ref) <= 256 and not ref.startswith("/") and
            all(c.isascii() and (c.isalnum() or c in "_./#-") for c in ref) and
            all(p not in ("", ".", "..") for p in ref.split("#", 1)[0].split("/")),
            "Safe relative evidence reference required")
    return dict(value, applicability=_scope(value["applicability"]))


@dataclass(frozen=True)
class Evidence:
    """A normalized, explicitly reviewed assertion bound to actual saved bytes.

    Source bytes stay in memory only here; decisions retain references/hashes.
    An authority-kind label is not provider authentication or live permission.
    """
    claim_bytes: bytes = field(repr=False)
    source_bytes: bytes = field(repr=False)

    def __post_init__(self):
        require(isinstance(self.claim_bytes, bytes) and len(self.claim_bytes) <= 8192,
                "Scale evidence claim bound")
        require(isinstance(self.source_bytes, bytes) and 0 < len(self.source_bytes) <= 8 * 1024**2,
                "Scale evidence source bound")
        claim = _claim(decode(self.claim_bytes))
        require(encode(claim) == self.claim_bytes, "Noncanonical scale evidence")
        require(sha(self.source_bytes) == claim["source"]["sha256"], "Evidence source bytes differ")

    @classmethod
    def bind(cls, claim, source_bytes):
        return cls(encode(_claim(claim)), source_bytes)

    def reference(self):
        return dict(evidence_sha256=sha(self.claim_bytes), claim=decode(self.claim_bytes))


def _evidence(evidence, identity):
    require(isinstance(evidence, (list, tuple)) and len(evidence) <= MAX_EVIDENCE, "Per-stream evidence bound")
    result = {}
    for item in evidence:
        require(type(item) is Evidence, "Hash-bound reviewed Evidence required")
        ref = item.reference()
        claim = ref["claim"]
        require(claim["station_id"] == identity["station_id"] and
                claim["stream_id"] == identity["stream_id"], "Evidence stream/station mismatch")
        result[ref["evidence_sha256"]] = ref
    return [result[k] for k in sorted(result)]


def _resolution(status, evidence_status, reason=None):
    factor = 1 if status.endswith("_percent") else 100 if status.endswith("_fraction") else None
    return dict(resolution_status=status, conversion_factor=factor,
                normalized_unit="% volumetric water content" if factor is not None else None,
                normalized_percent_eligible=factor is not None,
                absolute_percent_product_eligible=factor is not None,
                evidence_status=evidence_status, unresolved_reason=reason)


def _covers(scope, lo, hi):
    if scope["kind"] == "whole_history":
        return True
    return (scope["kind"] == "interval" and lo is not None and hi is not None and
            parse_utc(scope["start"]) <= lo and hi <= parse_utc(scope["end"]))


def _segments(scope, primary):
    lo = parse_utc(scope["start"]) if scope["kind"] == "interval" else None
    hi = parse_utc(scope["end"]) if scope["kind"] == "interval" else None
    cuts = set()
    for ref in primary:
        interval = ref["claim"]["applicability"]
        if interval["kind"] == "interval":
            for t in (parse_utc(interval["start"]), parse_utc(interval["end"])):
                if (lo is None or lo < t) and (hi is None or t < hi):
                    cuts.add(t)
    points = [lo, *sorted(cuts), hi]
    unknown = [r for r in primary if r["claim"]["applicability"]["kind"] == "unknown_history"]
    segments = []
    for a, b in zip(points, points[1:]):
        applied = [r for r in primary if _covers(r["claim"]["applicability"], a, b)]
        scales = {r["claim"]["scale"] for r in applied}
        if unknown:
            result = _resolution("unresolved", "temporal_scope_unresolved", "primary_historical_scope_unknown")
        elif "ambiguous" in scales or None in scales:
            result = _resolution("unresolved", "primary_evidence_conflict", "ambiguous_primary_evidence")
        elif len(scales) > 1:
            result = _resolution("unresolved", "primary_evidence_conflict", "contradictory_primary_evidence")
        elif not scales:
            result = _resolution("unresolved", "temporal_scope_unresolved" if primary else "insufficient_primary_evidence",
                                 "no_primary_for_interval" if primary else "no_qualifying_primary_evidence")
        else:
            result = _resolution("evidence_resolved_" + next(iter(scales)), "primary_evidence_resolved")
        segments.append(dict(start=format_utc(a) if a is not None else None,
                             end=format_utc(b) if b is not None else None,
                             primary_evidence_ids=sorted(r["evidence_sha256"] for r in applied + unknown), **result))
    return segments


def _decision(identity, evidence, scope):
    refs = _evidence(evidence, identity)
    primary = [r for r in refs if r["claim"]["role"] == "primary"]
    supporting = [r for r in refs if r["claim"]["role"] == "supporting"]
    if identity["native_unit"] != "Dimensionless":
        # This gate cannot replace or contest the already accepted 337 routes.
        require(not primary, "Accepted baseline primary evidence requires separate review")
        result = _resolution("accepted_resolved_" + ("percent" if identity["native_unit"] == "Percent" else "fraction"),
                             "accepted_baseline")
        segments = [dict(start=scope.get("start"), end=scope.get("end"), primary_evidence_ids=[], **result)]
    else:
        segments = _segments(scope, primary)
        statuses = {s["resolution_status"] for s in segments}
        if len(statuses) == 1 and "unresolved" not in statuses:
            # A whole-history claim is essential for whole-history eligibility,
            # even when all supplied finite interval pieces happen to agree.
            result = _resolution(next(iter(statuses)), "primary_evidence_resolved")
        elif all(s["evidence_status"] == "insufficient_primary_evidence" for s in segments):
            result = _resolution("unresolved", "insufficient_primary_evidence", "no_qualifying_primary_evidence")
        elif any(s["evidence_status"] == "primary_evidence_conflict" for s in segments):
            reason = "contradictory_primary_evidence" if any(s["unresolved_reason"] == "contradictory_primary_evidence" for s in segments) else "ambiguous_primary_evidence"
            result = _resolution("unresolved", "primary_evidence_conflict", reason)
        else:
            result = _resolution("unresolved", "temporal_scope_unresolved", "mixed_or_unsupported_historical_scope")
    value = dict(schema_version=DECISION_VERSION, policy_version=POLICY_VERSION,
                 station_id=identity["station_id"], stream_id=identity["stream_id"],
                 frozen_inventory_sha256=INVENTORY_SHA256, frozen_identity=identity,
                 frozen_identity_sha256=digest(identity), native_unit=identity["native_unit"],
                 native_unit_status=identity["unit_status"], acquisition_eligible=True,
                 native_archive_eligible=True, eligibility_scope="scale_only_subject_to_access_and_daily_acceptance",
                 primary_evidence=primary, supporting_evidence=supporting,
                 temporal_applicability=scope, segments=segments, **result)
    require(len(encode(value)) <= 262144, "Scale decision byte bound")
    return dict(value, decision_sha256=digest(value))


def resolve(inventory, stream_id, evidence=(), *, scope=None):
    """Derive one scale decision; no native identity, value or receipt is changed."""
    require(type(inventory) is Inventory, "Accepted frozen inventory required")
    return _decision(inventory.identity(stream_id), evidence,
                     _scope(scope if scope is not None else {"kind": "whole_history"}, request=True))


def build_state(inventory, evidence=(), *, scope=None):
    """Complete 122/434 derived state, independently serializable from a campaign."""
    require(type(inventory) is Inventory, "Accepted frozen inventory required")
    require(isinstance(evidence, (list, tuple)) and len(evidence) <= MAX_STATE_EVIDENCE, "State evidence bound")
    scope = _scope(scope if scope is not None else {"kind": "whole_history"}, request=True)
    roster = inventory.roster()
    grouped = {sid: [] for sid in roster}
    for item in evidence:
        require(type(item) is Evidence, "Hash-bound reviewed Evidence required")
        sid = item.reference()["claim"]["stream_id"]
        require(sid in roster, "Evidence outside frozen roster")
        grouped[sid].append(item)
    value = dict(schema_version=STATE_VERSION, policy_version=POLICY_VERSION,
                 frozen_inventory_sha256=INVENTORY_SHA256, station_count=122, stream_count=434,
                 temporal_applicability=scope,
                 decisions=[_decision(roster[sid], grouped[sid], scope) for sid in sorted(roster)])
    state = dict(value, state_sha256=digest(value))
    require(len(encode(state)) <= STATE_BYTES, "Scale state byte bound")
    return state


def restore_state(body, inventory, source_blobs):
    """Recompute from referenced saved bytes, rejecting altered/rehash-forged state.

    source_blobs maps original source SHA-256 to explicitly supplied bytes; this
    function never loads a URL/path or infers that a missing source is absent.
    """
    require(isinstance(body, bytes) and len(body) <= STATE_BYTES, "Scale state byte bound")
    value = decode(body)
    require(isinstance(value, dict) and value.get("schema_version") == STATE_VERSION and
            value.get("policy_version") == POLICY_VERSION, "Unsupported scale state/policy")
    decisions = value.get("decisions")
    require(isinstance(decisions, list) and len(decisions) == 434 and isinstance(source_blobs, dict),
            "Scale state closure/source mapping")
    unique = {}
    for decision in decisions:
        require(isinstance(decision, dict), "Scale decision object")
        for field_name in ("primary_evidence", "supporting_evidence"):
            refs = decision.get(field_name)
            require(isinstance(refs, list) and len(refs) <= MAX_EVIDENCE, "Stored evidence bound")
            for ref in refs:
                require(isinstance(ref, dict) and set(ref) == {"evidence_sha256", "claim"}, "Stored evidence fields")
                claim = _claim(ref["claim"])
                require(ref["evidence_sha256"] == digest(claim), "Stored evidence hash mismatch")
                source_hash = claim["source"]["sha256"]
                require(source_hash in source_blobs, "Referenced source bytes unavailable")
                unique[ref["evidence_sha256"]] = Evidence.bind(claim, source_blobs[source_hash])
                require(len(unique) <= MAX_STATE_EVIDENCE, "State evidence bound")
    expected = build_state(inventory, list(unique.values()), scope=value.get("temporal_applicability"))
    require(body == encode(expected), "Scale state differs from bound policy/evidence")
    return expected
