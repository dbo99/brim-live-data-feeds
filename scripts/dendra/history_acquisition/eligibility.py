"""Offline reviewed native eligibility; metadata packets never grant authority.

The caller supplies a trusted, explicitly reviewed artifact. Hashes bind bytes,
not a reviewer's authority. This module has no provider or journal entrypoint.
"""
from datetime import timedelta

from ..transport import parse_utc, format_utc
from .dimensionless_probe import (IDENTITY, TEMPORAL_PROFILE, TEMPORAL_PACKET, TEMPORAL_EVIDENCE,
    CAMPAIGN_TEMPORAL_PROFILE, CAMPAIGN_TEMPORAL_PACKET, CAMPAIGN_TEMPORAL_EVIDENCE,
    _attribute_identity, _claim_values)
from .model import Inventory, INVENTORY_SHA256, HASH
from .safety import decode, digest, encode, require, sha
from .scale_resolution import resolve

REVIEW = "dendra-native-acquisition-review-1"
DECISION = "dendra-native-eligibility-2"
POLICY = "dendra-native-acquisition-policy-1"
NATIVE_ELIGIBLE = "NATIVE_ACQUISITION_ELIGIBLE"
NATIVE_HOLD = "NATIVE_ACQUISITION_HOLD"
NORMALIZED_ELIGIBLE = "NORMALIZED_PERCENT_ELIGIBLE"
NORMALIZED_HOLD = "NORMALIZED_PERCENT_HOLD"
PROFILES = {TEMPORAL_PROFILE: (TEMPORAL_PACKET, TEMPORAL_EVIDENCE),
            CAMPAIGN_TEMPORAL_PROFILE: (CAMPAIGN_TEMPORAL_PACKET, CAMPAIGN_TEMPORAL_EVIDENCE)}
ACKNOWLEDGEMENTS = ["backend_not_interpreted", "history_not_science",
                    "native_only_no_scale_inference", "unknown_identity_stays_unknown"]


def _hash(value):
    require(isinstance(value, str) and HASH.fullmatch(value), "Explicit SHA-256 required")
    return value


def _packet(inventory, body, expected_sha256, packet_source):
    require(type(inventory) is Inventory and isinstance(body, bytes) and len(body) <= 65536,
            "Bounded packet and frozen inventory required")
    require(sha(body) == _hash(expected_sha256), "Reviewed packet bytes changed")
    p = decode(body)
    require(isinstance(p, dict) and p.get("metadata_profile") in PROFILES,
            "Unaccepted metadata profile")
    profile = p["metadata_profile"]
    packet_version, evidence_version = PROFILES[profile]
    require(p.get("schema_version") == packet_version, "Unaccepted metadata profile")
    identity = inventory.identity(p["stream_id"])
    # Historical profile remains exact-target. The additive campaign profile
    # uses the same parsers with explicit known-depth/orientation matching.
    require((profile != TEMPORAL_PROFILE or identity == IDENTITY) and p["frozen_identity"] == identity and
            p["station_id"] == identity["station_id"] and
            p["frozen_identity_sha256"] == digest(identity) and
            p["inventory_sha256"] == INVENTORY_SHA256 and
            p["native_unit"] == identity["native_unit"] and p["unit_status"] == identity["unit_status"],
            "Frozen identity binding changed")
    b, e = p["metadata_binding"], p["configuration_evidence"]
    require(p["metadata_binding_sha256"] == digest(b) and
            b["collector_fingerprint"] == _hash(packet_source) and
            b["profile"] == profile and b["packet_version"] == packet_version and
            b["inventory_sha256"] == INVENTORY_SHA256 and
            b["frozen_identity_sha256"] == digest(identity) and
            b["configuration_evidence_sha256"] == digest(e) and
            b["configuration_sha256"] == p["configuration_sha256"] == e["configuration"]["sha256"] and
            b["scientific_sha256"] == p["scientific_sha256"], "Packet binding mismatch")
    require(e["schema_version"] == evidence_version and e["evidence_complete"] is True and
            e["metadata_profile"] == profile and e["packet_version"] == packet_version and
            e["inventory_sha256"] == INVENTORY_SHA256 and e["frozen_identity_sha256"] == digest(identity) and
            not e["hold_reasons"] and e["omitted_configurations"] == 0 and
            e["collector_fingerprint"] == packet_source and
            e["station_id"] == p["station_id"] and e["stream_id"] == p["stream_id"] and
            e["original_response_sha256"] == p["original_response_sha256"] and
            e["original_response_bytes"] == p["original_response_bytes"] and
            e["selected_record_sha256"] == p["selected_record_sha256"], "Incomplete temporal evidence")
    require(all(p[k] is False and e[k] is False for k in (
        "raw_eligible", "observation_acquisition_authorized", "daily_science_accepted",
        "browser_publication_eligible")) and p["scale_assertions"] == e["scale_assertions"] == [] and
        p["historical_applicability"] == e["historical_applicability"] == {"kind": "unknown_history"},
        "Metadata packet authority changed")
    for value in (p["scientific_sha256"], p["configuration_sha256"], p["selected_record_sha256"],
                  p["original_response_sha256"], p["access_evidence"]["station_metadata_sha256"]):
        _hash(value)
    claims = p["scientific_claims"]
    terms = claims["terms"]
    attributes = claims.get("attributes")
    if attributes is not None:
        _claim_values(attributes)
    identity_check = _attribute_identity(attributes, identity)
    require(terms["ds"]["Medium"] == "Soil" and terms["ds"]["Variable"] == "VolumetricWaterContent" and
            terms["dt"]["Unit"] == identity["native_unit"] and
            terms["ds"].get("Aggregate", "Instantaneous") in ("Average", "Instantaneous") and
            p["identity_check"] == identity_check,
            "Scientific identity changed")
    require(p["attributes_state"] in ("ABSENT", "PRESENT_EMPTY", "PRESENT_POPULATED") and
            (p["attributes_state"] == "ABSENT") == ("attributes" not in claims) and
            (p["attributes_state"] != "PRESENT_EMPTY" or attributes == {}), "Scientific identity changed")
    if profile == CAMPAIGN_TEMPORAL_PROFILE:
        require(not any(p["omitted_scientific_fields"].values()), "Unreviewed scientific fields")
    a = p["access_evidence"]
    require(a["station_public_level"] == a["stream_public_level"] == 3 and
            a["station_is_hidden"] is False and a["stream_is_hidden"] is False and
            type(a["geo_protected"]) is bool, "Access HOLD")
    windows = []
    for c in e["configurations"]:
        require(type(c["ordinal"]) is int and 0 <= c["ordinal"] < 8, "Configuration ordinal bound")
        _hash(c["object_sha256"])
        f = c["fields"]
        require(c["window_validation"] == "VALID_HALF_OPEN" and c["unknown_field_count"] == 0 and
                f["actions"]["validation"] == "ABSENT" and
                f["begins_at"]["validation"] == "VALID_R2" and
                f["ends_before"]["validation"] in ("VALID_R2", "ABSENT_OPEN_END") and
                f["interval"]["validation"] in ("ABSENT_UNSPECIFIED", "VALID_LOCAL_CADENCE_CLAIM") and
                all(f[k]["validation"] in ("ABSENT", "WITHHELD_UNREVIEWED")
                    for k in ("connection", "params", "path")), "Temporal HOLD")
        start = f["begins_at"]["value"]
        end = f["ends_before"].get("value")
        require(end is None or parse_utc(start) < parse_utc(end), "Temporal window order")
        windows.append(dict(ordinal=c["ordinal"], start=start, end=end, object_sha256=c["object_sha256"]))
    windows.sort(key=lambda x: parse_utc(x["start"]))
    require(0 < len(windows) <= 8 and len(windows) == e["configuration"]["item_count"] and
            sorted(w["ordinal"] for w in windows) == list(range(len(windows))), "Configuration closure")
    for a, b in zip(windows, windows[1:]):
        require(a["end"] is not None and parse_utc(a["end"]) <= parse_utc(b["start"]), "Temporal overlap")
    return p, windows


def propose(inventory, packet_bytes, *, packet_sha256, packet_source_fingerprint,
            executor_fingerprint, start, end):
    """Return PENDING only. A separate human-reviewed artifact is required."""
    p, _ = _packet(inventory, packet_bytes, packet_sha256, packet_source_fingerprint)
    require(parse_utc(start) < parse_utc(end), "Invalid review interval")
    return dict(schema_version=REVIEW, packet_sha256=packet_sha256,
                packet_source_fingerprint=packet_source_fingerprint,
                executor_fingerprint=_hash(executor_fingerprint),
                inventory_sha256=INVENTORY_SHA256, identity_sha256=p["frozen_identity_sha256"],
                configuration_evidence_sha256=digest(p["configuration_evidence"]),
                scientific_sha256=p["scientific_sha256"], station_metadata_sha256=p["access_evidence"]["station_metadata_sha256"],
                selected_record_sha256=p["selected_record_sha256"],
                scope=dict(start=start, end=end), disposition="PENDING",
                reviewer_ref=None, reviewed_at=None, expires_at=None, acknowledgements=[])


def decide(inventory, packet_bytes, review, *, executor_fingerprint, now, scale_evidence=()):
    """Evaluate explicit acceptance; never mutate the packet or authorize I/O."""
    expected = propose(inventory, packet_bytes, packet_sha256=review["packet_sha256"],
                       packet_source_fingerprint=review["packet_source_fingerprint"],
                       executor_fingerprint=executor_fingerprint, **review["scope"])
    mutable = {"disposition", "reviewer_ref", "reviewed_at", "expires_at", "acknowledgements"}
    require(set(review) == set(expected) and all(review[k] == expected[k] for k in expected if k not in mutable),
            "Review/source/configuration binding changed")
    require(review["disposition"] in ("PENDING", "ACCEPT_NATIVE", "HOLD"), "Unknown review disposition")
    p, windows = _packet(inventory, packet_bytes, review["packet_sha256"], review["packet_source_fingerprint"])
    reasons = []
    if review["disposition"] != "ACCEPT_NATIVE":
        reasons.append("review_required" if review["disposition"] == "PENDING" else "review_hold")
    else:
        require(isinstance(review["reviewer_ref"], str) and 0 < len(review["reviewer_ref"]) <= 128 and
                review["acknowledgements"] == ACKNOWLEDGEMENTS, "Explicit reviewed acknowledgement required")
        reviewed, expires, current = map(parse_utc, (review["reviewed_at"], review["expires_at"], now))
        require(reviewed < expires <= reviewed + timedelta(hours=24), "Review expiry bound")
        if not reviewed <= current <= expires:
            reasons.append("review_stale_or_future")
    current = parse_utc(now)
    metadata_expiry = min(parse_utc(t) + timedelta(hours=24) for t in
                          (p["checked_at"], p["access_evidence"]["station_checked_at"],
                           p["access_evidence"]["stream_checked_at"]))
    if any(not timedelta(0) <= current - parse_utc(t) <= timedelta(hours=24)
           for t in (p["checked_at"], p["access_evidence"]["station_checked_at"], p["access_evidence"]["stream_checked_at"])):
        reasons.append("access_metadata_stale_or_future")
    scale = resolve(inventory, p["stream_id"], scale_evidence,
                    scope=dict(kind="interval", **review["scope"]))
    native = not reasons
    normalized = native and scale["normalized_percent_eligible"]
    normalized_reasons = list(reasons)
    if not scale["normalized_percent_eligible"]:
        normalized_reasons.append("scale_" + scale["unresolved_reason"])
    value = dict(schema_version=DECISION, policy_version=POLICY,
                 reviewer_policy_version=REVIEW, reviewer_ref=review["reviewer_ref"],
                 stream_id=p["stream_id"], station_id=p["station_id"],
                 inventory_sha256=INVENTORY_SHA256, identity=p["frozen_identity"],
                 frozen_identity_sha256=p["frozen_identity_sha256"],
                 native_unit=p["native_unit"], native_unit_status=p["unit_status"],
                 packet_sha256=review["packet_sha256"], metadata_profile=p["metadata_profile"],
                 metadata_packet_version=p["schema_version"],
                 metadata_binding_sha256=p["metadata_binding_sha256"],
                 packet_source_fingerprint=review["packet_source_fingerprint"],
                 executor_fingerprint=executor_fingerprint, review_sha256=digest(review),
                 configuration_evidence_sha256=review["configuration_evidence_sha256"],
                 configuration_profile_sha256=digest(dict(profile=p["metadata_profile"],
                     packet_version=p["schema_version"], evidence_version=p["configuration_evidence"]["schema_version"])),
                 configuration_sha256=p["configuration_sha256"],
                 configuration_windows=windows, scope=review["scope"], evaluated_at=now,
                 valid_until=format_utc(min(metadata_expiry, parse_utc(review["expires_at"])))
                 if review["disposition"] == "ACCEPT_NATIVE" else None,
                 access_status="fresh_public" if "access_metadata_stale_or_future" not in reasons else "stale",
                 access_evidence=p["access_evidence"], access_evidence_sha256=digest(p["access_evidence"]),
                 metadata_checked_at=p["checked_at"],
                 metadata_review_status="admitted", scientific_identity_status="matched_unknowns_preserved",
                 scientific_sha256=p["scientific_sha256"], identity_check=p["identity_check"],
                 temporal_config_status="reviewed_valid_half_open_windows",
                 historical_applicability="unknown_history", native_acquisition_eligible=native,
                 native_acquisition_status=NATIVE_ELIGIBLE if native else NATIVE_HOLD,
                 normalized_percent_status=NORMALIZED_ELIGIBLE if normalized else NORMALIZED_HOLD,
                 normalized_conversion_eligible=normalized,
                 normalized_percent_product_eligible=False, daily_product_eligible=False,
                 publication_eligible=False, live_execution_authorized=False,
                 scale=scale, scale_state_sha256=digest(scale), hold_reasons=reasons,
                 normalized_hold_reasons=normalized_reasons)
    # Scale decisions remain a sidecar; a scale-only update cannot rewrite native
    # task/receipt identity. Execution still needs explicit campaign authorization.
    value["native_authority_sha256"] = digest({k: value[k] for k in (
        "policy_version", "stream_id", "station_id", "inventory_sha256", "identity", "packet_sha256", "metadata_profile",
        "packet_source_fingerprint", "executor_fingerprint", "review_sha256", "configuration_evidence_sha256", "scope")})
    return dict(value, decision_sha256=digest(value))


def validate_decision(inventory, packet_bytes, review, decision, *, executor_fingerprint, now,
                      scale_evidence=()):
    """Restore from original reviewed inputs, then recheck at dispatch time.

    A recomputed outer decision hash is never authority. The caller supplies the
    original trusted review and packet; a scale sidecar additionally needs its
    original bound Evidence objects. Historical decisions/archives are never
    edited. Passing this guard grants no network budget or execution permission.
    """
    require(isinstance(decision, dict) and decision.get("schema_version") == DECISION,
            "Unsupported eligibility decision version")
    expected = decide(inventory, packet_bytes, review, executor_fingerprint=executor_fingerprint,
                      now=decision["evaluated_at"], scale_evidence=scale_evidence)
    require(encode(decision) == encode(expected), "Eligibility decision differs from original review/evidence")
    require(expected["native_acquisition_status"] == NATIVE_ELIGIBLE and
            expected["native_acquisition_eligible"] is True, "Reviewed native eligibility HOLD")
    require(parse_utc(expected["evaluated_at"]) <= parse_utc(now) <= parse_utc(expected["valid_until"]),
            "Eligibility decision stale or future")
    current = decide(inventory, packet_bytes, review, executor_fingerprint=executor_fingerprint,
                     now=now, scale_evidence=scale_evidence)
    require(current["native_acquisition_status"] == NATIVE_ELIGIBLE,
            "Current reviewed native eligibility HOLD")
    return expected
