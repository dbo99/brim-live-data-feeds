"""Offline, fail-closed metadata parsing for the fixed two-stream D3 boundary.

Only normalized, allowlisted fields escape these functions. Original response
hashes/byte counts belong to the caller's existing journal receipt; these returned
objects are sanitized projections, never an assertion of original body bytes.
"""
from datetime import timedelta
import math
from pathlib import Path

from ..transport import parse_utc
from .model import ID, INVENTORY_SHA256, Inventory
from .safety import Hold, Root, decode, digest, encode, require, sha

CATALOG_SHA256 = "44e5b22ab113c4f57177fb2ab1c894b4e327ff98e9e93be26050336fd8196e2b"
SCIENTIFIC_SHA256 = "0d972d48b592a069fa884b0e3fcae1d69422bb27e3a657d3fbe11b1cd7bc416f"
DICTIONARY_SHA256 = "8fffc7413c21a1ce82134490f767b1123b48dcf64be0aa59235f61c3453334da"
SELECTED = {
    "63531a67a9b61453fa1ca4ed": "635319fcb055ac5348842453",
    "5d8e42e72da5c3cc53f6531d": "58e68cabdf5ce600012602bd",
}
BODY_BYTES = 8 * 1024**2


def _copy(value):
    return decode(encode(value))


def validate_authority(authority):
    """Validate the complete pinned projection without reading files or sockets."""
    require(isinstance(authority, dict) and set(authority) == {
                "catalog_sha256", "inventory_sha256", "scientific", "dictionary_terms",
                "dictionary_sha256", "metadata_bindings"} and
            authority.get("catalog_sha256") == CATALOG_SHA256 and
            authority.get("inventory_sha256") == INVENTORY_SHA256 and
            isinstance(authority.get("scientific"), dict) and
            set(authority["scientific"]) == set(SELECTED) and
            isinstance(authority.get("dictionary_terms"), dict) and
            set(authority["dictionary_terms"]) == {"Percent", "VolumetricWaterContent"} and
            digest(authority.get("scientific")) == SCIENTIFIC_SHA256 and
            digest(authority.get("dictionary_terms")) == DICTIONARY_SHA256 and
            authority.get("dictionary_sha256") == DICTIONARY_SHA256,
            "Accepted metadata authority changed")
    require(authority.get("metadata_bindings") == {
        sid: digest(value) for sid, value in authority["scientific"].items()},
        "Scientific binding changed")


def load_authority(inventory, catalog_path):
    """Read only the explicit, hash-pinned accepted catalog; no provider access."""
    require(type(inventory) is Inventory, "Bound inventory required")
    path = Path(catalog_path)
    require(path.is_absolute(), "Explicit absolute catalog path required")
    with Root(path.parent) as root:
        raw = root.read(path.name, 65536)
    require(len(raw) == 40235 and sha(raw) == CATALOG_SHA256, "Accepted catalog bytes changed")
    catalog = decode(raw)
    scientific = {}
    for stream in catalog["streams"]:
        sid = stream["datastream_id"]
        if sid not in SELECTED:
            continue
        identity = inventory.identity(sid)
        require(stream["station_id"] == SELECTED[sid] == identity["station_id"] and
                stream["depth_cm"] == identity["depth_cm"] and
                stream["orientation"] == identity["orientation"] and
                stream["native_unit_name"] == identity["native_unit"] and
                stream["unit_normalization"]["status"] == identity["unit_status"],
                "Accepted catalog/inventory disagreement")
        scientific[sid] = dict(parameter="soil_moisture", source_terms=stream["source_terms"],
                               source_attributes=stream["source_attributes"],
                               cadence_seconds=stream["cadence_evidence"]["configured_interval_ms_from_seed"] / 1000,
                               time_semantics="canonical_t_unshifted")
    dictionary = catalog["provenance"]["validated_unit_dictionary"]
    require(dictionary["vocabulary_id"] == "dt-unit", "Accepted dictionary identity")
    terms = {term["label"]: term for term in dictionary["source_unit_terms"]
             if term["label"] in ("Percent", "VolumetricWaterContent")}
    result = dict(catalog_sha256=sha(raw), inventory_sha256=INVENTORY_SHA256,
                  scientific=scientific, dictionary_terms=terms,
                  dictionary_sha256=digest(terms),
                  metadata_bindings={sid: digest(fields) for sid, fields in scientific.items()})
    validate_authority(result)
    return result


def _payload(body):
    require(isinstance(body, bytes) and len(body) <= BODY_BYTES, "Metadata body bound")
    try:
        value = decode(body)
    except RecursionError as exc:
        raise Hold("Metadata nesting bound") from exc
    require(isinstance(value, dict), "Metadata object required")
    count = 0
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        count += 1
        require(depth <= 16 and count <= 20000, "Metadata traversal bound")
        if isinstance(item, dict):
            require(len(item) <= 512, "Metadata object field bound")
            for key, child in item.items():
                require(len(key) <= 256, "Metadata key bound")
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            require(len(item) <= 10000, "Metadata array bound")
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            require(len(item) <= 65536, "Metadata string bound")
    return value


def parse_vocabulary(body, authority):
    """Find exact selected unit terms within bounded bridge-compatible nesting.

    The old bridge scans nested dictionaries/lists for labels. This accepts that
    shape conservatively while requiring one exact occurrence per selected unit;
    it does not assert an unobserved complete outer dictionary schema.
    """
    validate_authority(authority)
    value = _payload(body)
    require(value.get("_id") == "dt-unit", "Unit vocabulary identity")
    found = {}
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            label = item.get("label")
            if isinstance(label, str) and label in authority["dictionary_terms"]:
                require(label not in found, "Duplicate selected unit definition")
                require(item == authority["dictionary_terms"][label], "Unit vocabulary mismatch")
                found[label] = _copy(item)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    require(found == authority["dictionary_terms"], "Missing selected unit definition")
    return dict(dictionary_sha256=digest(found), terms=found)


def _fresh(checked_at, now):
    try:
        elapsed = parse_utc(now) - parse_utc(checked_at)
    except (ValueError, TypeError) as exc:
        raise Hold("Metadata check timestamp") from exc
    require(timedelta(0) <= elapsed <= timedelta(hours=24), "Metadata stale or future dated")


def _timestamp(value, label):
    if value is not None:
        require(isinstance(value, str) and len(value) <= 64, label)
        try:
            parse_utc(value)
        except (ValueError, TypeError) as exc:
            raise Hold(label) from exc
    return value


def _text(value, label, limit=256):
    require(value is None or (isinstance(value, str) and len(value) <= limit and
                             not any(ord(c) < 32 for c in value)), label)
    return value


def _public(value):
    nested = value.get("access_levels_resolved", {})
    require(isinstance(nested, dict), "Access-level shape")
    level = value.get("public_level", nested.get("public_level"))
    if "public_level" in value and "public_level" in nested:
        require(value["public_level"] == nested["public_level"], "Conflicting public levels")
    require(type(level) is int and level == 3 and value.get("is_hidden") is False,
            "Private, hidden or unknown public metadata")
    require(value.get("is_deleted", False) is False and value.get("deleted", False) is False and
            value.get("deleted_at") is None and value.get("state") not in ("deleted", "missing"),
            "Missing or deleted metadata")
    protected = value.get("is_geo_protected")
    require(protected is None or type(protected) is bool, "Protection flag type")
    return level, protected is not False


def _descriptive(value, now):
    activity = "unknown"
    claims = []
    for key in ("is_active", "is_enabled"):
        if key in value:
            require(type(value[key]) is bool, "Activity flag type")
            claims.append(value[key])
    if claims:
        activity = "active" if all(claims) else "inactive/ended"
    state = value.get("state")
    require(state is None or (isinstance(state, str) and len(state) <= 64), "Activity state type")
    if state in ("ended", "inactive", "disabled"):
        activity = "inactive/ended"
    ended = _timestamp(value.get("ends_before", value.get("ended_at")), "Ended timestamp")
    if ended is not None and parse_utc(ended) <= parse_utc(now):
        activity = "inactive/ended"
    revision = value.get("revision", value.get("_rev"))
    require(revision is None or type(revision) in (str, int), "Metadata revision type")
    revision = _text(str(revision) if revision is not None else None, "Metadata revision", 128)
    return dict(display_name=_text(value.get("name"), "Metadata display name"),
                revision=revision, updated_at=_timestamp(value.get("updated_at"), "Updated timestamp"),
                activity=activity, ended_at=ended)


def _geometry(value, protected):
    if protected:
        return None
    geometry = value.get("geo", value.get("geometry"))
    if geometry is None:
        return None
    require(isinstance(geometry, dict) and geometry.get("type") == "Point", "Public Point geometry required")
    coordinates = geometry.get("coordinates")
    require(isinstance(coordinates, list) and len(coordinates) == 2 and
            all(type(n) in (int, float) and math.isfinite(n) for n in coordinates) and
            -180 <= coordinates[0] <= 180 and -90 <= coordinates[1] <= 90,
            "Public coordinate bounds")
    if "geo" in value and "geometry" in value:
        require(value["geo"] == value["geometry"], "Conflicting geometry")
    return list(coordinates)


def parse_station(body, station_id, *, checked_at, now):
    require(station_id in SELECTED.values(), "Station outside D3 selection")
    _fresh(checked_at, now)
    value = _payload(body)
    require(value.get("_id") == station_id, "Station identity mismatch or missing")
    level, protected = _public(value)
    return dict(exact_id=station_id, public_level=level, is_hidden=False,
                geo_protected=protected, geometry=_geometry(value, protected),
                checked_at=checked_at, **_descriptive(value, now))


def _scientific(value):
    terms, attributes = value.get("terms"), value.get("attributes")
    require(isinstance(terms, dict) and isinstance(attributes, dict), "Scientific metadata shape")
    configs = value.get("datapoints_config")
    require(isinstance(configs, list) and len(configs) == 1 and isinstance(configs[0], dict),
            "Missing or ambiguous configured cadence")
    interval = configs[0].get("interval")
    require(type(interval) in (int, float) and math.isfinite(interval) and interval > 0,
            "Configured cadence value")
    return dict(parameter="soil_moisture", source_terms=terms, source_attributes=attributes,
                cadence_seconds=interval / 1000, time_semantics="canonical_t_unshifted")


def parse_datastreams(body, identity, station, vocabulary, authority, *, checked_at, now):
    """Return sanitized claims for the accepted D1+D2 metadata_view contract."""
    validate_authority(authority)
    _fresh(checked_at, now)
    require(isinstance(identity, dict) and identity.get("stream_id") in SELECTED and
            identity.get("station_id") == SELECTED[identity["stream_id"]], "Selected stream association")
    sid = identity["stream_id"]
    require(isinstance(station, dict) and station.get("exact_id") == identity["station_id"] and
            station.get("public_level") == 3 and station.get("is_hidden") is False,
            "Fresh selected public station required")
    _fresh(station.get("checked_at"), now)
    require(isinstance(vocabulary, dict) and vocabulary.get("terms") == authority["dictionary_terms"] and
            vocabulary.get("dictionary_sha256") == authority["dictionary_sha256"],
            "Verified selected dictionary required")
    value = _payload(body)
    rows, limit = value.get("data"), value.get("limit")
    require(isinstance(rows, list) and type(limit) is int and 1 <= limit <= 500 and len(rows) < limit,
            "Incomplete, full or unknown-limit datastream list")
    require(type(value.get("skip", 0)) is int and value.get("skip", 0) == 0,
            "Metadata list offset is not the complete first page")
    if "total" in value:
        require(type(value["total"]) is int and value["total"] == len(rows), "Metadata total incomplete")
    by_id = {}
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get("_id"), str) and
                ID.fullmatch(row["_id"]) and row["_id"] not in by_id and
                row.get("station_id") == identity["station_id"], "Metadata list identity or association")
        by_id[row["_id"]] = row
    require(sid in by_id, "Selected stream unavailable; deletion is not proved")
    selected = by_id[sid]
    level, protected = _public(selected)
    scientific = _scientific(selected)
    require(digest(scientific) == authority["metadata_bindings"][sid], "Scientific metadata mismatch")
    # Check caller identity against pinned scientific authority without deriving a
    # replacement identity from the refreshed provider claims.
    expected = authority["scientific"][sid]
    attributes = expected["source_attributes"]
    expected_depth = attributes.get("depth", {}).get("value")
    expected_depth = expected_depth / 10 if expected_depth is not None else None
    expected_orientation = attributes.get("orientation", attributes.get("Orientation"))
    require(set(identity) == {"station_id", "stream_id", "depth_cm", "orientation", "native_unit", "unit_status"} and
            identity["depth_cm"] == expected_depth and identity["orientation"] == expected_orientation and
            identity["native_unit"] == expected["source_terms"]["dt"]["Unit"] and
            identity["unit_status"] == "verified_percent_conversion", "Frozen selected identity mismatch")
    protected = protected or station.get("geo_protected") is not False
    geometry = None
    if not protected and station.get("geometry") is not None:
        geometry = _geometry(dict(geo=dict(type="Point", coordinates=station["geometry"])), False)
    description = _descriptive(selected, now)
    config_end = _timestamp(selected["datapoints_config"][0].get("ends_before"), "Configured end timestamp")
    if config_end is not None:
        if description["ended_at"] is not None:
            require(description["ended_at"] == config_end, "Conflicting end timestamps")
        description["ended_at"] = config_end
        if parse_utc(config_end) <= parse_utc(now):
            description["activity"] = "inactive/ended"
    result = dict(**_copy(identity), complete=True, access_state="accessible", public_level=level,
                  is_hidden=False, station_public_level=3, station_is_hidden=False,
                  geo_protected=protected, geometry=geometry,
                  display_name=description["display_name"], revision=description["revision"],
                  metadata_updated_at=description["updated_at"], activity=description["activity"],
                  ended_at=description["ended_at"], scientific_fields=_copy(scientific),
                  scientific_sha256=digest(scientific), dictionary_sha256=authority["dictionary_sha256"],
                  pagination=dict(effective_limit=limit, row_count=len(rows)),
                  unexpected_ids=sorted(set(by_id) - {sid}))
    require(len(encode(result)) <= 262144, "Sanitized metadata bound")
    return result
