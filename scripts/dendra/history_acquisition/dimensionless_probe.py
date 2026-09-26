"""Exact-target metadata readiness: no default executor, live entry or campaign.

The injected one-shot runner is exercised with synthetic responses offline.
A future live driver requires separate authorization and fresh durable attempt/
receipt callbacks. Plan construction is not permission to contact the provider.
No scale decision is made here, and no acquisition journal is opened or resumed.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
import math
import re
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

from .d3_plan import BASE, validate_request
from .model import Inventory, INVENTORY_SHA256, source_binding
from .provider_adapter import total_deadline, MetadataAdmissionHold, UNPARSED, DIAGNOSTIC_BYTES
from .provider_metadata import (_parse_station, _payload, _fresh, _public,
                                _datastream_page, _stream_description, _descriptive, _timestamp)
from ..transport import parse_utc
from .safety import Hold, require, encode, decode, digest, sha

VERSION = "dendra-dimensionless-probe-1"
PACKET_VERSION = "dendra-soil-metadata-review-1"
REVIEW_PROFILE = "dendra-soil-conditional-attributes-1"
TEMPORAL_PROFILE = "dendra-soil-temporal-config-review-1"
TEMPORAL_PACKET = "dendra-soil-temporal-metadata-review-1"
TEMPORAL_EVIDENCE = "dendra-target-temporal-evidence-1"
CAMPAIGN_TEMPORAL_PROFILE = "dendra-soil-campaign-temporal-config-review-1"
CAMPAIGN_TEMPORAL_PACKET = "dendra-soil-campaign-temporal-metadata-review-1"
CAMPAIGN_TEMPORAL_EVIDENCE = "dendra-campaign-temporal-evidence-1"
STATION = "5d8f7f052da5c3a1bdf65382"
STREAM = "5d9272a12da5c3cff0f655ed"
IDENTITY = dict(station_id=STATION, stream_id=STREAM, depth_cm=None,
                orientation=None, native_unit="Dimensionless",
                unit_status="native_only_scale_unresolved")
BODY_BYTES = 8 * 1024**2
ENVELOPE = dict(logical_requests=2, http_attempts=2, retries=0, redirects=0,
                concurrency=1, per_request_seconds=25, total_seconds=50,
                per_request_bytes=BODY_BYTES, total_bytes=2*BODY_BYTES,
                pagination=False, observations=False)


@dataclass(frozen=True)
class RequestSpec:
    kind: str
    station_id: str = STATION
    selected_stream: str = STREAM

    def url(self):
        require(self.station_id == STATION and self.selected_stream == STREAM,
                "Exact Dimensionless target required")
        require(self.kind in ("station", "datastream-list"), "Metadata-only probe endpoints")
        return (BASE + "stations/" + STATION if self.kind == "station" else
                BASE + "datastreams?" + urlencode({"station_id": STATION, "$limit": 500, "$sort[_id]": 1}))

    def descriptor(self):
        return dict(kind=self.kind, station_id=self.station_id,
                    selected_stream=self.selected_stream, url=self.url())


def make_plan(inventory, *, station_id, stream_id, metadata_profile):
    require(metadata_profile in (REVIEW_PROFILE, TEMPORAL_PROFILE),
            "Exact-target probe profiles only")
    packet_version = _packet_version(metadata_profile)
    require(type(inventory) is Inventory and station_id == STATION and stream_id == STREAM,
            "Exact bound Dimensionless inventory target required")
    require(inventory.identity(STREAM) == IDENTITY, "Frozen Dimensionless identity changed")
    return dict(version=VERSION, metadata_profile=metadata_profile,
                metadata_profile_sha256=digest(dict(profile=metadata_profile, packet_version=packet_version)),
                inventory_sha256=INVENTORY_SHA256,
                frozen_identity=inventory.identity(STREAM), collector_sources=source_binding(),
                envelope=dict(ENVELOPE),
                requests=[RequestSpec(kind).descriptor() for kind in ("station", "datastream-list")])


def _packet_version(profile):
    if profile == CAMPAIGN_TEMPORAL_PROFILE:
        return CAMPAIGN_TEMPORAL_PACKET
    require(profile in (REVIEW_PROFILE, TEMPORAL_PROFILE), "Explicit metadata-review profile required")
    return TEMPORAL_PACKET if profile == TEMPORAL_PROFILE else PACKET_VERSION


# Only these scientific paths can escape into the review packet. Unknown keys,
# arbitrary nested objects, other streams and coordinates are never retained.
# These names are projection slots, not assertions about an observed API schema.
SCALAR = None
MEASUREMENT = {key: SCALAR for key in ("value", "unit", "units", "description")}
CALIBRATION = {key: SCALAR for key in ("scale", "multiplier", "offset", "unit", "units",
                                      "output_unit", "description", "valid_from", "valid_to")}
TERMS = {"dt": {"Unit": SCALAR},
         "ds": {key: SCALAR for key in ("Medium", "Variable", "Aggregate")},
         "dq": {key: SCALAR for key in ("Measurement", "Purpose")}}
ATTRIBUTES = {"depth": dict(MEASUREMENT, unit_tag=SCALAR), "Depth": SCALAR,
              "LengthUnits": SCALAR, "orientation": SCALAR, "Orientation": SCALAR,
              "scale": MEASUREMENT, "output_scale": MEASUREMENT, "output_unit": SCALAR,
              "output_units": SCALAR, "calibration": CALIBRATION}
CONFIG = {key: SCALAR for key in ("interval", "scale", "multiplier", "offset", "unit", "units",
                                 "output_unit", "output_units", "scale_description", "starts_at",
                                 "ends_before", "valid_from", "valid_to")}
CONFIG["calibration"] = CALIBRATION
TARGET_REASONS = {
    "Scientific metadata shape": ("science.terms_attributes_shape", "unsupported_shape"),
    "Missing or ambiguous configured cadence": ("science.cadence_shape", "completeness"),
    "Configured cadence value": ("science.cadence_value", "scientific_mismatch"),
    "Frozen target native unit changed": ("target.native_unit_changed", "identity_mismatch"),
    "Unsupported scientific projection leaf": ("target.unsupported_scientific_leaf", "unsupported_shape"),
    "Target scale packet byte bound": ("target.packet_bound", "bounds"),
}
for _name in ("core_terms", "aggregate", "term_claim", "attribute_claim", "depth_evidence",
              "orientation_evidence", "date_bounds", "projection_shape"):
    TARGET_REASONS["Review " + _name] = ("review." + _name, "scientific_mismatch")


def _projection(value, schema):
    """Bounded exact provider claims plus omission count, without interpretation."""
    if schema is None:
        allowed = value is None or type(value) is bool or type(value) is int or (
            type(value) is float and math.isfinite(value)) or (
            type(value) is str and len(value) <= 256 and all(ord(c) >= 32 for c in value))
        require(allowed, "Unsupported scientific projection leaf")
        return value, 0
    require(isinstance(value, dict), "Review projection_shape")
    output, omitted = {}, len(set(value) - set(schema))
    for key in sorted(set(value) & set(schema)):
        output[key], count = _projection(value[key], schema[key])
        omitted += count
    return output, omitted


def attributes_state(value):
    """Presence is provenance: missing and explicitly empty never collapse."""
    if "attributes" not in value:
        return "ABSENT"
    attributes = value["attributes"]
    if attributes is None:
        return "NULL"
    if not isinstance(attributes, dict):
        return "MALFORMED_NON_OBJECT"
    return "PRESENT_POPULATED" if attributes else "PRESENT_EMPTY"


def _dates(value):
    for key in ("starts_at", "ends_before", "valid_from", "valid_to"):
        if key in value:
            _timestamp(value[key], "Review date_bounds")
    for start, end in (("starts_at", "ends_before"), ("valid_from", "valid_to")):
        if value.get(start) is not None and value.get(end) is not None:
            require(parse_utc(value[start]) < parse_utc(value[end]), "Review date_bounds")


def _numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


def _claim_values(value):
    """Validate supported scalar families, without interpreting scale claims."""
    _dates(value)
    for key, item in value.items():
        if isinstance(item, dict):
            _claim_values(item)
        elif key in ("value", "Depth", "multiplier", "offset", "scale"):
            require(_numeric(item), "Review attribute_claim")
        elif key not in ("interval", "starts_at", "ends_before", "valid_from", "valid_to"):
            require(isinstance(item, str) and 0 < len(item) <= 256,
                    "Review attribute_claim")


def _attribute_identity(attributes, identity):
    """Fresh evidence must establish known identity; unknown identity stays unknown."""
    depths, orientations = [], []
    factors = {"dt_Unit_Millimeter": 0.1, "Millimeter": 0.1,
               "dt_Unit_Centimeter": 1, "Centimeter": 1}
    if attributes is not None and "depth" in attributes:
        depth = attributes["depth"]
        require(isinstance(depth, dict) and _numeric(depth.get("value")), "Review depth_evidence")
        units = [depth[k] for k in ("unit_tag", "unit", "units") if k in depth]
        require(units and all(isinstance(u, str) and u in factors for u in units) and
                len({factors[u] for u in units}) == 1, "Review depth_evidence")
        depths.append(depth["value"] * factors[units[0]])
    if attributes is not None and ("Depth" in attributes or "LengthUnits" in attributes):
        unit = attributes.get("LengthUnits")
        require(_numeric(attributes.get("Depth")) and isinstance(unit, str) and unit in factors,
                "Review depth_evidence")
        depths.append(attributes["Depth"] * factors[unit])
    if attributes is not None:
        orientations = [attributes[k] for k in ("orientation", "Orientation") if k in attributes]
    require(all(_numeric(d) for d in depths) and len(set(depths)) <= 1, "Review depth_evidence")
    require(all(isinstance(o, str) and 0 < len(o) <= 256 for o in orientations) and
            len(set(orientations)) <= 1, "Review orientation_evidence")
    for field, values, reason in (("depth_cm", depths, "depth_evidence"),
                                  ("orientation", orientations, "orientation_evidence")):
        if identity[field] is not None:
            require(values and values[0] == identity[field], "Review " + reason)
    return {field: "frozen_unknown" if identity[field] is None else "matched_fresh_evidence"
            for field in ("depth_cm", "orientation")}


# R2 schema/source mapping and deliberate gaps are documented in DENDRA_D3_ADAPTER.
# Values may escape ONLY for valid temporal strings and positive numeric interval.
# Backend dispatch/action content is never retained or executed.
TEMPORAL_CONFIG_LIMIT = 8
TEMPORAL_EVIDENCE_BYTES = 24576
TEMPORAL_FIELDS = ("begins_at", "ends_before", "interval", "connection", "params", "path", "actions")
JSON_TYPES = {dict: "object", list: "array", str: "string", int: "integer",
              float: "number", bool: "boolean", type(None): "null"}
ISO_BOUND = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.([0-9]{1,18}))?(Z|[+-][0-9]{2}:[0-9]{2})")
R2_BOUND = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{1,3}Z")
for _name in ("shape", "bound", "date", "unknown_bound", "interval", "backend_shape",
              "unreviewed_fields", "actions", "overlap", "duplicate", "window"):
    TARGET_REASONS["Review temporal_" + _name] = ("review.temporal_" + _name, "scientific_mismatch")


def _non_authorizing():
    return dict(raw_eligible=False, observation_acquisition_authorized=False,
                daily_science_accepted=False, browser_publication_eligible=False,
                scale_assertions=[], historical_applicability=dict(kind="unknown_history"))


def _field_fact(value, key):
    present = key in value
    return dict(present=present, json_type=JSON_TYPES[type(value[key])] if present else "missing",
                sha256=digest(value[key]) if present else None)


def _temporal_bound(value, key):
    fact = _field_fact(value, key)
    if key not in value:
        # R2 permits omission. Only the end has the guide's explicit ongoing
        # meaning; never manufacture a start date from a backend sentinel.
        fact["validation"] = "ABSENT_OPEN_END" if key == "ends_before" else "ABSENT_UNKNOWN"
        return fact, None
    raw = value[key]
    fact["validation"] = "NULL_UNSUPPORTED" if raw is None else "INVALID"
    match = ISO_BOUND.fullmatch(raw) if isinstance(raw, str) and len(raw) <= 64 else None
    if match is None:
        return fact, None
    base, fraction, zone = match.groups()
    try:
        if zone != "Z" and (int(zone[1:3]) > 23 or int(zone[4:6]) > 59):
            return fact, None
        # Fraction is kept separately: datetime must not truncate source precision.
        stamp = datetime.fromisoformat(base + ("+00:00" if zone == "Z" else zone))
        delta = stamp.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        exact = Fraction(delta.days * 86400 + delta.seconds) + Fraction(int(fraction or "0"), 10**len(fraction or ""))
    except (ValueError, OverflowError):
        return fact, None
    fact["value"] = raw  # Date-only grammar; never an arbitrary provider string.
    fact["validation"] = "VALID_R2" if R2_BOUND.fullmatch(raw) else "UNSUPPORTED_R2_FORMAT"
    # Preserve offset/precision evidence on HOLD; do not extend R2's schema.
    return fact, exact if fact["validation"] == "VALID_R2" else None


def _temporal_evidence(target, body, identity, *, profile=TEMPORAL_PROFILE):
    """Exact public target only; bounded safe facts independent of later admission."""
    configs = target.get("datapoints_config")
    fact = _field_fact(target, "datapoints_config")
    evidence = dict(schema_version=CAMPAIGN_TEMPORAL_EVIDENCE if profile == CAMPAIGN_TEMPORAL_PROFILE else TEMPORAL_EVIDENCE,
        metadata_profile=profile, packet_version=_packet_version(profile),
        station_id=identity["station_id"], stream_id=identity["stream_id"],
        inventory_sha256=INVENTORY_SHA256, frozen_identity_sha256=digest(identity),
        collector_fingerprint=digest(source_binding()), original_response_bytes=len(body),
        original_response_sha256=sha(body), selected_record_sha256=digest(target),
        configuration=fact, configurations=[], temporal_order=[], relations=[], hold_reasons=[],
        relation_scope="pairwise_configuration_windows_not_observation_coverage",
        evidence_complete=True, metadata_admitted=False, cadence_role="provider_metadata_only",
        interval_authority="legacy_local_ms_claim_not_defined_by_captured_r2_schema",
        backend_semantics="not_evaluated", **_non_authorizing())
    reasons = evidence["hold_reasons"]
    def hold(reason):
        if reason not in reasons:
            reasons.append(reason)
    if not isinstance(configs, list) or not configs:
        hold("Review temporal_shape")
        return evidence
    fact["item_count"] = len(configs)
    evidence["omitted_configurations"] = max(0, len(configs) - TEMPORAL_CONFIG_LIMIT)
    if len(configs) > TEMPORAL_CONFIG_LIMIT:
        evidence["evidence_complete"] = False
        hold("Review temporal_bound")
    windows, hashes = [], {}
    for ordinal, config in enumerate(configs[:TEMPORAL_CONFIG_LIMIT]):
        item = dict(ordinal=ordinal, json_type=JSON_TYPES[type(config)], object_sha256=digest(config))
        evidence["configurations"].append(item)
        if not isinstance(config, dict):
            hold("Review temporal_shape")
            continue
        item["fields"] = {key: _field_fact(config, key) for key in TEMPORAL_FIELDS}
        unknown = {k: v for k, v in config.items() if k not in TEMPORAL_FIELDS}
        item.update(unknown_field_count=len(unknown), unknown_fields_sha256=digest(unknown),
                    omitted_backend_field_count=sum(k in config for k in ("connection", "params", "path", "actions")))
        if unknown:
            hold("Review temporal_unreviewed_fields")
        if item["object_sha256"] in hashes:
            item["duplicate_of_ordinal"] = hashes[item["object_sha256"]]
            hold("Review temporal_duplicate")
        else:
            hashes[item["object_sha256"]] = ordinal
        for key, expected in (("connection", str), ("params", dict), ("path", str), ("actions", dict)):
            field = item["fields"][key]
            field["validation"] = ("ABSENT" if key not in config else
                                   "WITHHELD_UNREVIEWED" if type(config[key]) is expected else "INVALID_TYPE")
            if field["validation"] == "INVALID_TYPE":
                hold("Review temporal_backend_shape")
        if "actions" in config:
            hold("Review temporal_actions")  # No transform/exclusion interpretation.
        bounds = []
        for key in ("begins_at", "ends_before"):
            field, instant = _temporal_bound(config, key)
            item["fields"][key] = field
            bounds.append(instant)
            if field["validation"] == "ABSENT_UNKNOWN":
                hold("Review temporal_unknown_bound")
            elif field["validation"] not in ("VALID_R2", "ABSENT_OPEN_END"):
                hold("Review temporal_date")
        interval = item["fields"]["interval"]
        if "interval" not in config:
            interval["validation"] = "ABSENT_UNSPECIFIED"
        elif type(config["interval"]) in (int, float) and 0 < config["interval"] <= 2**53 - 1:
            interval.update(validation="VALID_LOCAL_CADENCE_CLAIM", value=config["interval"], unit="millisecond")
        else:
            interval["validation"] = "NULL_UNSUPPORTED" if config["interval"] is None else "INVALID"
            hold("Review temporal_interval")
        start, end = bounds
        valid = (item["fields"]["begins_at"]["validation"] == "VALID_R2" and
                 item["fields"]["ends_before"]["validation"] in ("VALID_R2", "ABSENT_OPEN_END"))
        if valid and end is not None and start >= end:
            hold("Review temporal_window")
            valid = False
        item["window_validation"] = "VALID_HALF_OPEN" if valid else "UNKNOWN_OR_INVALID"
        if valid:
            windows.append((start, ordinal, end))
    windows.sort(key=lambda window: (window[0], window[1]))
    evidence["temporal_order"] = [ordinal for _, ordinal, _ in windows]
    evidence["temporal_order_complete"] = len(windows) == len(configs)
    # Every pair is checked, so a nested interval cannot hide a later overlap.
    # Source order is always retained separately; ordering selects no winner.
    for pos, (start, ordinal, end) in enumerate(windows):
        for next_start, next_ordinal, _ in windows[pos+1:]:
            relation = "OVERLAP" if end is None or next_start < end else "ADJACENT" if next_start == end else "GAP"
            evidence["relations"].append(dict(left_ordinal=ordinal, right_ordinal=next_ordinal, relation=relation))
            if relation == "OVERLAP":
                hold("Review temporal_overlap")
    require(len(encode(evidence)) <= TEMPORAL_EVIDENCE_BYTES, "Review temporal_bound")
    return evidence


def _review_science(target, identity, *, temporal=False, profile=None):
    terms, state = target.get("terms"), attributes_state(target)
    require(isinstance(terms, dict) and state not in ("NULL", "MALFORMED_NON_OBJECT"),
            "Scientific metadata shape")
    dt, ds = terms.get("dt"), terms.get("ds")
    require(isinstance(dt, dict) and dt.get("Unit") == identity["native_unit"],
            "Frozen target native unit changed")
    require(isinstance(ds, dict) and ds.get("Medium") == "Soil" and
            ds.get("Variable") == "VolumetricWaterContent", "Review core_terms")
    if "Aggregate" in ds:
        require(ds["Aggregate"] in ("Average", "Instantaneous"), "Review aggregate")
    if "dq" in terms:
        require(isinstance(terms["dq"], dict), "Review term_claim")
        for key in ("Measurement", "Purpose"):
            if key in terms["dq"]:
                item = terms["dq"][key]
                require(isinstance(item, str) and 0 < len(item) <= 256 and
                        all(ord(c) >= 32 for c in item), "Review term_claim")
    configs = target.get("datapoints_config")
    if not temporal:
        require(isinstance(configs, list) and len(configs) == 1 and isinstance(configs[0], dict),
                "Missing or ambiguous configured cadence")
        interval = configs[0].get("interval")
        require(_numeric(interval) and interval > 0, "Configured cadence value")
    claims, omissions = {}, {}
    fields = [("terms", terms, TERMS)]
    if not temporal:
        fields.append(("datapoints_config", configs[0], CONFIG))
    if state != "ABSENT":
        fields.append(("attributes", target["attributes"], ATTRIBUTES))
    for name, value, schema in fields:
        claims[name], omissions[name] = _projection(value, schema)
    for name in ("attributes", "datapoints_config"):
        if name in claims:
            _claim_values(claims[name])
    identity_check = _attribute_identity(claims.get("attributes"), identity)
    # Bind full original science, not just the allowlisted projection. Absence
    # has no manufactured attributes object and a distinct explicit state.
    science = dict(profile=profile or (TEMPORAL_PROFILE if temporal else REVIEW_PROFILE), terms=terms, attributes_state=state,
                   datapoints_config=configs)
    if state != "ABSENT":
        science["attributes"] = target["attributes"]
    presence = {section: dict(present=section in terms,
                              fields={key: key in terms.get(section, {}) for key in schema})
                for section, schema in TERMS.items()}
    return dict(science=science, claims=claims, omissions=omissions, state=state,
                presence=presence, identity_check=identity_check,
                aggregate_state="PRESENT" if "Aggregate" in ds else "ABSENT_UNSPECIFIED")


def review_packet(body, station, inventory, *, stream_id, metadata_profile, checked_at, now):
    """Offline review for one frozen roster stream, never acquisition authority.

    No provider I/O, journal, dictionary assertion or daily/scale decision. The
    caller supplies freshly admitted station metadata; original bodies are not
    returned. Network selection remains separately pinned by RequestSpec.
    """
    packet_version = _packet_version(metadata_profile)
    require(type(inventory) is Inventory, "Explicit metadata-review profile required")
    identity = inventory.identity(stream_id)
    temporal = metadata_profile in (TEMPORAL_PROFILE, CAMPAIGN_TEMPORAL_PROFILE)
    require(metadata_profile != TEMPORAL_PROFILE or identity == IDENTITY,
            "Exact bound Dimensionless inventory target required")
    _fresh(checked_at, now)
    require(station.get("exact_id") == identity["station_id"] and station.get("public_level") == 3 and
            station.get("is_hidden") is False, "Fresh selected public station required")
    _fresh(station.get("checked_at"), now)
    rows, limit = _datastream_page(_payload(body), identity["station_id"], stream_id)
    target = rows[stream_id]
    level, protected = _public(target)
    evidence = _temporal_evidence(target, body, identity, profile=metadata_profile) if temporal else None
    try:
        review = _review_science(target, identity, temporal=temporal, profile=metadata_profile)
        if metadata_profile == CAMPAIGN_TEMPORAL_PROFILE:
            require(not any(review["omissions"].values()), "Review projection_shape")
        if temporal:
            require(not evidence["hold_reasons"], evidence["hold_reasons"][0] if evidence["hold_reasons"] else "Review temporal_shape")
            # Stream-level descriptions still validate; no configuration is
            # chosen to stand for current activity or a universal end/cadence.
            _descriptive(target, now)
        else:
            _stream_description(target, now)
    except Hold as exc:
        if evidence is not None:
            exc.temporal_configuration_evidence = evidence
        if str(exc) == "Scientific metadata shape":
            # Lookup/association/public admission already passed. Retain only
            # fixed field presence/types, never the selected record or values.
            types = {dict: "object", list: "array", str: "string", int: "integer",
                     float: "number", bool: "boolean", type(None): "null"}
            exc.target_scientific_shape = {
                name: dict(present=name in target,
                           json_type=types[type(target[name])] if name in target else "missing")
                for name in ("terms", "attributes")}
        elif str(exc) == "Missing or ambiguous configured cadence" and identity == IDENTITY:
            # Exact lookup/access and preceding science checks already passed.
            # Enrich this HOLD only; the configuration admission rule is unchanged.
            exc.target_configuration_shape = _target_configuration_shape(target)
        raise
    binding = dict(profile=metadata_profile, packet_version=packet_version,
                   inventory_sha256=INVENTORY_SHA256, frozen_identity_sha256=digest(identity),
                   scientific_sha256=digest(review["science"]),
                   configuration_sha256=digest(target["datapoints_config"]),
                   collector_fingerprint=digest(source_binding()))
    if temporal:
        binding["configuration_evidence_sha256"] = digest(evidence)
    packet = dict(schema_version=packet_version, metadata_profile=metadata_profile,
        metadata_binding=binding, metadata_binding_sha256=digest(binding),
        station_id=identity["station_id"], stream_id=stream_id,
        frozen_identity=identity, frozen_identity_sha256=digest(identity),
        native_unit=identity["native_unit"], unit_status=identity["unit_status"],
        inventory_sha256=INVENTORY_SHA256,
        checked_at=checked_at, original_response_bytes=len(body), original_response_sha256=sha(body),
        selected_record_sha256=digest(target), scientific_sha256=binding["scientific_sha256"],
        configuration_sha256=binding["configuration_sha256"], configuration_state="PRESENT_SINGLE",
        cadence_role="provider_metadata_only", scientific_claims=review["claims"],
        scientific_field_sha256={name: digest(target[name]) for name in
                                 ("terms", "attributes", "datapoints_config") if name in target},
        terms_presence=review["presence"], aggregate_state=review["aggregate_state"],
        attributes_state=review["state"], identity_check=review["identity_check"],
        omitted_scientific_fields=review["omissions"], claim_review="unreviewed_provider_metadata",
        access_evidence=dict(station_public_level=3, station_is_hidden=False,
            stream_public_level=level, stream_is_hidden=False, station_checked_at=station["checked_at"],
            stream_checked_at=checked_at, station_metadata_sha256=digest(station),
            geo_protected=protected or station.get("geo_protected") is not False),
        raw_eligible=False, observation_acquisition_authorized=False,
        daily_science_accepted=False, browser_publication_eligible=False,
        historical_applicability=dict(kind="unknown_history"), scale_assertions=[],
        pagination=dict(effective_limit=limit, row_count=len(rows)),
        unexpected_ids=sorted(set(rows)-{stream_id}), returned_ids_sha256=digest(sorted(rows)))
    if temporal:
        packet.update(configuration_state="PRESENT_TEMPORAL_ARRAY", configuration_evidence=evidence)
    else:
        packet["configured_cadence_seconds"] = target["datapoints_config"][0]["interval"] / 1000
    require(len(encode(packet)) <= 65536, "Target scale packet byte bound")
    return packet


CONFIG_ITEM_TYPE_LIMIT = 8


def _target_configuration_shape(target):
    """Closed type facts only: no values, keys, nested traversal or identifiers."""
    types = {dict: "object", list: "array", str: "string", int: "integer",
             float: "number", bool: "boolean", type(None): "null"}
    present = "datapoints_config" in target
    value = target.get("datapoints_config")
    facts = dict(present=present, json_type=types[type(value)] if present else "missing")
    if isinstance(value, list):
        facts.update(item_count=len(value),
                     item_types=[types[type(item)] for item in value[:CONFIG_ITEM_TYPE_LIMIT]],
                     item_types_truncated=len(value) > CONFIG_ITEM_TYPE_LIMIT)
    return dict(datapoints_config=facts)


class Probe:
    """One-shot injection boundary, never a restartable provider campaign.

    reserve(spec, plan_sha256) must durably consume that exact attempt BEFORE
    dispatch. record(receipt, sanitized_bytes) must persist before continuation.
    A live driver must supply fresh exclusive state, a fixed authorization window,
    and the existing anonymous NoRedirect opener. No defaults enable live access.
    """
    def __init__(self, inventory, plan):
        require(plan == make_plan(inventory, station_id=STATION, stream_id=STREAM,
                                  metadata_profile=plan.get("metadata_profile")),
                "Probe plan/source binding changed")
        self._inventory = inventory
        self._plan = encode(plan)
        self._used = False
        self._counts = dict(logical_requests=0, http_attempts=0, response_bytes=0, retries=0)

    @property
    def counters(self):
        return dict(self._counts)

    def run(self, *, executor, reserve, record, now, monotonic=time.monotonic):
        require(not self._used and threading.current_thread() is threading.main_thread() and
                all(callable(f) for f in (executor, reserve, record, now, monotonic)),
                "Fresh serial explicitly injected probe required")
        self._used = True  # A failure, interrupted call or reentrant call cannot reset this budget.
        began = monotonic()
        require(type(began) in (int, float) and math.isfinite(began), "Probe clock required")
        plan = decode(self._plan)
        station, packet = None, None
        for kind in ("station", "datastream-list"):
            require(source_binding() == plan["collector_sources"], "Probe source fingerprint changed")
            require(kind == "station" or station is not None, "Station admission required before list")
            remaining = 50 - (monotonic() - began)
            require(0 < remaining <= 50 and self._counts["http_attempts"] < 2,
                    "Probe total time or request budget exhausted")
            spec = RequestSpec(kind)
            request = urllib.request.Request(spec.url(), method="GET", headers={
                "Accept": "application/json", "User-Agent": "BRIM-Dendra-D3/1.0"})
            validate_request(request, spec)
            # No executor call is possible if durable reservation fails.
            reserve(spec.descriptor(), sha(self._plan))
            self._counts["logical_requests"] += 1
            self._counts["http_attempts"] += 1
            start = monotonic()
            body, status, parsed, diagnostic = bytearray(), None, None, None
            complete = False
            outcome, reason, caught = "STOP", "transport_or_resource", None
            try:
                remaining = 50 - (start - began)
                require(0 < remaining <= 50, "Probe total time budget exhausted")
                with total_deadline(min(25, remaining)):
                    try:
                        response = executor(request, timeout=min(25, remaining))
                    except urllib.error.HTTPError as exc:
                        status = exc.code
                        exc.close()
                        raise Hold("Probe HTTP failure; no retry") from None
                    try:
                        status = response.status
                        require(type(status) is int and status == 200, "Probe HTTP failure; no retry")
                        require(response.headers.get("Content-Encoding", "identity").lower() == "identity",
                                "Encoded metadata body forbidden")
                        while True:
                            chunk = response.read(min(65536, BODY_BYTES + 1 - len(body)))
                            require(isinstance(chunk, bytes), "Metadata read must return bytes")
                            if not chunk:
                                complete = True
                                break
                            body.extend(chunk)
                            if len(body) > BODY_BYTES:
                                break
                        require(len(body) <= BODY_BYTES, "Metadata body bound")
                    finally:
                        response.close()
                    payload = UNPARSED
                    try:
                        body = bytes(body)
                        checked = now()
                        payload = _payload(body)
                        parsed = (_parse_station(body, STATION, checked_at=checked, now=checked)
                                  if kind == "station" else
                                  review_packet(body, station, self._inventory, stream_id=STREAM,
                                                metadata_profile=plan["metadata_profile"],
                                                checked_at=checked, now=checked))
                    except (Hold, ValueError, TypeError, KeyError, RecursionError, AttributeError) as exc:
                        failure = MetadataAdmissionHold(spec, body, payload, exc,
                            target_scientific_shape=getattr(exc, "target_scientific_shape", None))
                        diagnostic = failure.diagnostic
                        if str(exc) in TARGET_REASONS:
                            code, category = TARGET_REASONS[str(exc)]
                            diagnostic["reason"].update(code=code, category=category)
                            if code == "science.cadence_shape" and hasattr(exc, "target_configuration_shape"):
                                diagnostic["target_configuration_shape"] = exc.target_configuration_shape
                            tb = exc.__traceback__
                            while tb is not None:
                                if tb.tb_frame.f_globals.get("__name__") == __name__:
                                    diagnostic["reason"]["parser_site"] = dict(module="dimensionless_probe",
                                        function=tb.tb_frame.f_code.co_name, line=tb.tb_lineno)
                                tb = tb.tb_next
                            failure.args = ("Metadata admission HOLD: " + code,)
                            while len(encode(diagnostic)) > DIAGNOSTIC_BYTES and diagnostic["shape"]["fields"]:
                                diagnostic["shape"]["fields"].pop()
                                diagnostic["shape"]["truncated"] = True
                            require(len(encode(diagnostic)) <= DIAGNOSTIC_BYTES, "Metadata diagnostic size bound")
                        evidence = getattr(exc, "temporal_configuration_evidence", None)
                        if evidence is not None:
                            # Keep the existing diagnostic/byte ceiling intact.
                            # This separately versioned partial record is not an
                            # admitted packet, even when its timeline is valid.
                            failure.temporal_configuration_evidence = evidence
                            diagnostic = dict(schema_version=TEMPORAL_EVIDENCE, outcome="HOLD",
                                metadata_admitted=False, diagnostic=failure.diagnostic,
                                configuration_evidence=evidence, **_non_authorizing())
                            require(len(encode(diagnostic)) <= 65536, "Review temporal_bound")
                        outcome, reason = "HOLD", failure.diagnostic["reason"]["code"]
                        raise failure from None
                    elapsed = monotonic() - start
                    total = monotonic() - began
                    require(0 <= elapsed <= 25 and 0 <= total <= 50, "Probe elapsed ceiling")
                    outcome, reason = "ADMITTED", None
            except BaseException as exc:
                caught = exc
            self._counts["response_bytes"] += len(body)
            if self._counts["response_bytes"] > 2*BODY_BYTES:
                caught = Hold("Probe cumulative byte ceiling")
                outcome, reason = "STOP", "body_limit"
            if caught is not None:
                parsed = None
            receipt = dict(version=VERSION, plan_sha256=sha(self._plan), request=spec.descriptor(),
                ordinal=self._counts["http_attempts"], http_status=status,
                response_bytes=len(body), response_sha256=sha(body),
                response_complete=complete,
                elapsed_seconds=max(0, monotonic()-start), outcome=outcome, reason=reason,
                retries=0, original_body_retained=False)
            sanitized = encode(parsed if parsed is not None else diagnostic) if (
                parsed is not None or diagnostic is not None) else None
            receipt.update(sanitized_bytes=len(sanitized) if sanitized is not None else 0,
                           sanitized_sha256=sha(sanitized) if sanitized is not None else None)
            record(receipt, sanitized)  # Persistence failure is terminal; never retries.
            if caught is not None:
                raise caught
            require(0 <= monotonic()-began <= 50, "Probe total time budget exhausted")
            if kind == "station":
                station = parsed
            else:
                packet = parsed
        return packet
