"""Exact-target metadata readiness: no default executor, live entry or campaign.

The injected one-shot runner is exercised with synthetic responses offline.
A future live driver requires separate authorization and fresh durable attempt/
receipt callbacks. Plan construction is not permission to contact the provider.
No scale decision is made here, and no acquisition journal is opened or resumed.
"""
from dataclasses import dataclass
import math
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

from .d3_plan import BASE, validate_request
from .model import Inventory, INVENTORY_SHA256, source_binding
from .provider_adapter import total_deadline, MetadataAdmissionHold, UNPARSED, DIAGNOSTIC_BYTES
from .provider_metadata import (_parse_station, _payload, _fresh, _public,
                                _scientific, _datastream_page, _stream_description)
from .safety import Hold, require, encode, decode, digest, sha

VERSION = "dendra-dimensionless-probe-1"
PACKET_VERSION = "dendra-target-scale-metadata-1"
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


def make_plan(inventory, *, station_id, stream_id):
    require(type(inventory) is Inventory and station_id == STATION and stream_id == STREAM,
            "Exact bound Dimensionless inventory target required")
    require(inventory.identity(STREAM) == IDENTITY, "Frozen Dimensionless identity changed")
    return dict(version=VERSION, inventory_sha256=INVENTORY_SHA256,
                frozen_identity=inventory.identity(STREAM), collector_sources=source_binding(),
                envelope=dict(ENVELOPE),
                requests=[RequestSpec(kind).descriptor() for kind in ("station", "datastream-list")])


# Only these scientific paths can escape into the review packet. Unknown keys,
# arbitrary nested objects, other streams and coordinates are never retained.
# These names are projection slots, not assertions about an observed API schema.
SCALAR = None
MEASUREMENT = {key: SCALAR for key in ("value", "unit", "units", "description")}
CALIBRATION = {key: SCALAR for key in ("scale", "multiplier", "offset", "unit", "units",
                                      "output_unit", "description", "valid_from", "valid_to")}
TERMS = {"dt": {key: SCALAR for key in ("Unit", "Variable", "Medium", "Method", "Aggregation")}}
ATTRIBUTES = {"depth": MEASUREMENT, "orientation": SCALAR, "Orientation": SCALAR,
              "scale": MEASUREMENT, "output_scale": MEASUREMENT, "output_unit": SCALAR,
              "output_units": SCALAR, "calibration": CALIBRATION}
CONFIG = {key: SCALAR for key in ("interval", "scale", "multiplier", "offset", "unit", "units",
                                 "output_unit", "output_units", "scale_description", "starts_at",
                                 "ends_before", "valid_from", "valid_to")}
CONFIG["calibration"] = CALIBRATION
TARGET_REASONS = {
    "Frozen target native unit changed": ("target.native_unit_changed", "identity_mismatch"),
    "Unsupported scientific projection leaf": ("target.unsupported_scientific_leaf", "unsupported_shape"),
    "Target scale packet byte bound": ("target.packet_bound", "bounds"),
}


def _projection(value, schema):
    """Bounded exact provider claims plus omission count, without interpretation."""
    if schema is None or not isinstance(value, dict):
        allowed = value is None or type(value) is bool or type(value) is int or (
            type(value) is float and math.isfinite(value)) or (
            type(value) is str and len(value) <= 256 and all(ord(c) >= 32 for c in value))
        require(allowed, "Unsupported scientific projection leaf")
        return value, 0
    output, omitted = {}, len(set(value) - set(schema))
    for key in sorted(set(value) & set(schema)):
        output[key], count = _projection(value[key], schema[key])
        omitted += count
    return output, omitted


def _target_packet(body, station, *, checked_at, now):
    _fresh(checked_at, now)
    require(station.get("exact_id") == STATION and station.get("public_level") == 3 and
            station.get("is_hidden") is False, "Fresh selected public station required")
    _fresh(station.get("checked_at"), now)
    rows, limit = _datastream_page(_payload(body), STATION, STREAM)
    target = rows[STREAM]
    _public(target)
    try:
        scientific = _scientific(target)
    except Hold as exc:
        if str(exc) == "Scientific metadata shape":
            # Lookup/association/public admission already passed. Retain only
            # fixed field presence/types, never the selected record or values.
            types = {dict: "object", list: "array", str: "string", int: "integer",
                     float: "number", bool: "boolean", type(None): "null"}
            exc.target_scientific_shape = {
                name: dict(present=name in target,
                           json_type=types[type(target[name])] if name in target else "missing")
                for name in ("terms", "attributes")}
        raise
    _stream_description(target, now)
    require(scientific["source_terms"].get("dt", {}).get("Unit") == "Dimensionless",
            "Frozen target native unit changed")
    claims, omissions = {}, {}
    for name, value, schema in (("terms", target["terms"], TERMS),
                                 ("attributes", target["attributes"], ATTRIBUTES),
                                 ("datapoints_config", target["datapoints_config"][0], CONFIG)):
        claims[name], omissions[name] = _projection(value, schema)
    packet = dict(schema_version=PACKET_VERSION, station_id=STATION, stream_id=STREAM,
        frozen_identity=dict(IDENTITY), inventory_sha256=INVENTORY_SHA256,
        checked_at=checked_at, original_response_bytes=len(body), original_response_sha256=sha(body),
        selected_record_sha256=digest(target), scientific_sha256=digest(scientific),
        configuration_sha256=digest(target["datapoints_config"]), scientific_claims=claims,
        omitted_scientific_fields=omissions, claim_review="unreviewed_provider_metadata",
        historical_applicability=dict(kind="unknown_history"), scale_assertions=[],
        pagination=dict(effective_limit=limit, row_count=len(rows)),
        unexpected_ids=sorted(set(rows)-{STREAM}), returned_ids_sha256=digest(sorted(rows)))
    require(len(encode(packet)) <= 65536, "Target scale packet byte bound")
    return packet


class Probe:
    """One-shot injection boundary, never a restartable provider campaign.

    reserve(spec, plan_sha256) must durably consume that exact attempt BEFORE
    dispatch. record(receipt, sanitized_bytes) must persist before continuation.
    A live driver must supply fresh exclusive state, a fixed authorization window,
    and the existing anonymous NoRedirect opener. No defaults enable live access.
    """
    def __init__(self, inventory, plan):
        require(plan == make_plan(inventory, station_id=STATION, stream_id=STREAM),
                "Probe plan/source binding changed")
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
                                  _target_packet(body, station, checked_at=checked, now=checked))
                    except (Hold, ValueError, TypeError, KeyError, RecursionError, AttributeError) as exc:
                        failure = MetadataAdmissionHold(spec, body, payload, exc,
                            target_scientific_shape=getattr(exc, "target_scientific_shape", None))
                        diagnostic = failure.diagnostic
                        if str(exc) in TARGET_REASONS:
                            code, category = TARGET_REASONS[str(exc)]
                            diagnostic["reason"].update(code=code, category=category)
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
                        outcome, reason = "HOLD", diagnostic["reason"]["code"]
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
