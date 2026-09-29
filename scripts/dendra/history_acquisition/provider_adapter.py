"""Explicit synchronous provider boundary; import/planning never sends HTTP.

The D3 and reviewed campaign adapters share dispatch, receipts and transport.
Live callers separately verify approval; offline callers inject finite response
callables and waits into the same run methods.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import io
import math
from pathlib import Path
import re
import signal
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, urlencode, urlsplit

from ..transport import DendraFetcher, FetchError, parse_utc, format_utc
from .d3_plan import (RequestSpec, SELECTED, START, END, validate_binding,
                      validate_request, seven_calls)
from .provider_metadata import parse_vocabulary, parse_station, parse_datastreams
from .journal import UnknownSourceRowCount
from .safety import Hold, require, encode, decode, digest, sha
from .witness_diagnostic import WitnessAdmissionHold

RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})
BODY_LIMIT = 8 * 1024**2
OBSERVATION_ENVELOPE = frozenset({"data", "limit", "total", "skip"})
OBSERVATION_FIELDS = frozenset({"_id", "datastream_id", "t", "v", "lt", "q"})

# These are diagnostic names only. Admission remains in provider_metadata.py.
METADATA_REASONS = {
    "Invalid JSON": ("json.invalid", "unsupported_shape"),
    "Duplicate JSON key": ("json.duplicate_key", "unsupported_shape"),
    "Nonfinite JSON number": ("json.nonfinite_number", "unsupported_shape"),
    "Nonstandard JSON constant": ("json.nonstandard_constant", "unsupported_shape"),
    "Metadata object required": ("metadata.object_required", "unsupported_shape"),
    "Metadata body bound": ("metadata.body_bound", "bounds"),
    "Metadata nesting bound": ("metadata.nesting_bound", "bounds"),
    "Metadata traversal bound": ("metadata.traversal_bound", "bounds"),
    "Metadata object field bound": ("metadata.field_bound", "bounds"),
    "Metadata key bound": ("metadata.key_bound", "bounds"),
    "Metadata array bound": ("metadata.array_bound", "bounds"),
    "Metadata string bound": ("metadata.string_bound", "bounds"),
    "Unit vocabulary identity": ("vocabulary.identity", "identity_mismatch"),
    "Duplicate selected unit definition": ("vocabulary.duplicate_term", "scientific_mismatch"),
    "Unit vocabulary mismatch": ("vocabulary.term_mismatch", "scientific_mismatch"),
    "Missing selected unit definition": ("vocabulary.missing_term", "missing_field"),
    "Metadata check timestamp": ("metadata.check_timestamp", "timestamp"),
    "Metadata stale or future dated": ("metadata.freshness", "timestamp"),
    "Access-level shape": ("access.level_shape", "unexpected_type"),
    "Conflicting public levels": ("access.conflicting_levels", "privacy_access"),
    "Private, hidden or unknown public metadata": ("access.public_nonhidden_required", "privacy_access"),
    "Missing or deleted metadata": ("access.missing_or_deleted", "privacy_access"),
    "Protection flag type": ("access.protection_type", "unexpected_type"),
    "Activity flag type": ("description.activity_flag_type", "unexpected_type"),
    "Activity state type": ("description.activity_state_type", "unexpected_type"),
    "Ended timestamp": ("description.ended_timestamp", "timestamp"),
    "Metadata revision type": ("description.revision_type", "unexpected_type"),
    "Metadata revision": ("description.revision_format", "unsupported_shape"),
    "Metadata display name": ("description.name_format", "unsupported_shape"),
    "Updated timestamp": ("description.updated_timestamp", "timestamp"),
    "Public Point geometry required": ("geometry.point_required", "unsupported_shape"),
    "Public coordinate bounds": ("geometry.coordinate_bounds", "bounds"),
    "Conflicting geometry": ("geometry.conflicting_claims", "unsupported_shape"),
    "Station identity mismatch or missing": ("station.id_mismatch", "identity_mismatch"),
    "Scientific metadata shape": ("science.terms_attributes_shape", "unsupported_shape"),
    "Missing or ambiguous configured cadence": ("science.cadence_shape", "completeness"),
    "Configured cadence value": ("science.cadence_value", "scientific_mismatch"),
    "Selected stream association": ("stream.station_association", "identity_mismatch"),
    "Fresh selected public station required": ("stream.station_admission", "privacy_access"),
    "Verified selected dictionary required": ("stream.dictionary_admission", "scientific_mismatch"),
    "Incomplete, full or unknown-limit datastream list": ("list.incomplete", "completeness"),
    "Metadata list offset is not the complete first page": ("list.offset", "completeness"),
    "Metadata total incomplete": ("list.total", "completeness"),
    "Metadata list identity or association": ("list.identity_association", "identity_mismatch"),
    "Selected stream unavailable; deletion is not proved": ("list.selected_stream_absent", "completeness"),
    "Scientific metadata mismatch": ("science.identity_mismatch", "scientific_mismatch"),
    "Frozen selected identity mismatch": ("science.frozen_identity_mismatch", "scientific_mismatch"),
    "Configured end timestamp": ("science.end_timestamp", "timestamp"),
    "Conflicting end timestamps": ("science.conflicting_end_timestamps", "scientific_mismatch"),
    "Sanitized metadata bound": ("metadata.projection_bound", "bounds"),
}
SHAPE_KEYS = frozenset({"_id", "data", "limit", "skip", "total", "station_id", "terms",
    "attributes", "datapoints_config", "interval", "public_level", "access_levels_resolved",
    "is_hidden", "is_deleted", "deleted", "deleted_at", "state", "is_geo_protected",
    "geo", "geometry", "type", "coordinates", "name", "revision", "_rev", "updated_at",
    "ends_before", "ended_at", "is_active", "is_enabled", "depth", "orientation",
    "Orientation", "value", "unit", "ds", "dt", "dq", "Unit", "label", "abbreviation"})
SHAPE_FIELDS, SHAPE_KEYS_PER_OBJECT, SHAPE_DEPTH, SHAPE_LIST_ITEMS = 64, 16, 4, 2
DIAGNOSTIC_BYTES = 12288
UNPARSED = object()


def metadata_shape(value, kind):
    """Types/counts and fixed schema names only; no provider values or unknown keys."""
    def type_name(item):
        return {dict: "object", list: "array", str: "string", int: "integer", float: "number",
                bool: "boolean", type(None): "null"}.get(type(item), "unparsed")
    fields, pending, truncated = [], [("$", value, 0)], False
    while pending and len(fields) < SHAPE_FIELDS:
        path, item, depth = pending.pop(0)
        entry = dict(path=path, type=type_name(item))
        if isinstance(item, (dict, list)):
            entry["count"] = len(item)
        children = []
        if isinstance(item, dict):
            keys = sorted(k for k in SHAPE_KEYS if k in item)[:SHAPE_KEYS_PER_OBJECT]
            entry["keys"] = keys
            entry["omitted_keys"] = len(item) - len(keys)
            truncated |= entry["omitted_keys"] > 0
            children = [(path + "." + k, item[k], depth + 1) for k in keys]
        elif isinstance(item, list):
            children = [(path + f"[{i}]", item[i], depth + 1)
                        for i in range(min(len(item), SHAPE_LIST_ITEMS))]
            truncated |= len(item) > SHAPE_LIST_ITEMS
        fields.append(entry)
        if depth < SHAPE_DEPTH:
            pending.extend(c for c in children if len(c[0]) <= 96)
            truncated |= any(len(c[0]) > 96 for c in children)
        else:
            truncated |= bool(children)
    required = {"station": ("_id", "is_hidden"), "unit-vocabulary": ("_id",),
                "datastream-list": ("data", "limit")}[kind]
    return dict(json_type=type_name(value), fields=fields, truncated=truncated or bool(pending),
                required_key_presence={k: isinstance(value, dict) and k in value for k in required})


class MetadataAdmissionHold(Hold):
    """Safe specific reason also saved in the existing sanitized-object receipt."""
    def __init__(self, spec, body, payload, cause, *, target_scientific_shape=None):
        message = str(cause)
        code, category = METADATA_REASONS.get(message, ("parser.condition", "parser_condition"))
        if message == "Station identity mismatch or missing" and isinstance(payload, dict):
            if "_id" not in payload:
                code, category = "station.id_missing", "missing_field"
            elif type(payload["_id"]) is not str:
                code, category = "station.id_type", "unexpected_type"
        if message == "Private, hidden or unknown public metadata" and isinstance(payload, dict):
            nested = payload.get("access_levels_resolved", {})
            level = payload.get("public_level", nested.get("public_level"))
            if "public_level" not in payload and "public_level" not in nested:
                code, category = "access.level_missing", "missing_field"
            elif type(level) is not int:
                code, category = "access.level_type", "unexpected_type"
            elif level == 3 and "is_hidden" not in payload:
                code, category = "access.hidden_missing", "missing_field"
            elif level == 3 and type(payload["is_hidden"]) is not bool:
                code, category = "access.hidden_type", "unexpected_type"
        site = None
        tb = cause.__traceback__
        while tb is not None:
            frame = tb.tb_frame
            if frame.f_globals.get("__name__") == parse_station.__module__:
                site = dict(module="provider_metadata", function=frame.f_code.co_name, line=tb.tb_lineno)
            tb = tb.tb_next
        exception_class = type(cause).__name__ if type(cause) in (
            Hold, ValueError, TypeError, KeyError, RecursionError) else "Exception"
        reason = dict(code=code, category=category, exception_class=exception_class, parser_site=site)
        self.diagnostic = dict(version="dendra-metadata-diagnostic-1", kind=spec.kind, reason=reason,
                               shape=metadata_shape(payload, spec.kind), body_bytes=len(body), body_sha256=sha(body))
        if target_scientific_shape is not None:
            require(code == "science.terms_attributes_shape" and spec.kind == "datastream-list" and
                    type(target_scientific_shape) is dict and
                    set(target_scientific_shape) == {"terms", "attributes"}, "Target diagnostic context")
            context = {}
            for name in ("terms", "attributes"):
                fact = target_scientific_shape[name]
                require(type(fact) is dict and set(fact) == {"present", "json_type"} and
                        type(fact["present"]) is bool and type(fact["json_type"]) is str and
                        fact["json_type"] in {"missing", "null", "object", "array", "string",
                                              "integer", "number", "boolean"} and
                        fact["present"] == (fact["json_type"] != "missing"), "Target diagnostic field type")
                context[name] = dict(fact)
            require(any(fact["json_type"] != "object" for fact in context.values()),
                    "Target diagnostic requires scientific shape HOLD")
            self.diagnostic["target_scientific_shape"] = context
        # Many permitted key names at every sampled level can reach the byte
        # bound before the field-count bound. Trim the deterministic tail while
        # preserving the reason, response binding and optional target facts.
        while len(encode(self.diagnostic)) > DIAGNOSTIC_BYTES and self.diagnostic["shape"]["fields"]:
            self.diagnostic["shape"]["fields"].pop()
            self.diagnostic["shape"]["truncated"] = True
        require(len(encode(self.diagnostic)) <= DIAGNOSTIC_BYTES, "Metadata diagnostic size bound")
        location = "" if site is None else f" at {site['module']}.{site['function']}:{site['line']}"
        super().__init__(f"Metadata admission HOLD: {code} ({exception_class}{location})")


def retry_delay(value, *, ordinal, now, remaining):
    """Strict admission before the existing fetcher sees a Retry-After value."""
    require(ordinal in (1, 2) and math.isfinite(remaining) and remaining > 0, "Retry budget")
    if value is None:
        delay = float(2 ** (ordinal - 1))
    else:
        require(isinstance(value, str) and 0 < len(value) <= 128, "Invalid Retry-After")
        if re.fullmatch(r"[0-9]+", value):
            delay = float(value)
        else:
            try:
                until = parsedate_to_datetime(value)
                require(until.tzinfo is not None and until.utcoffset().total_seconds() == 0,
                        "Retry-After date must be UTC")
                delay = max(0.0, (until - parse_utc(now)).total_seconds())
            except (ValueError, TypeError, OverflowError) as exc:
                raise Hold("Invalid Retry-After") from exc
    require(math.isfinite(delay) and 0 <= delay <= 15 and delay < remaining,
            "Retry-After exceeds remaining probe budget")
    return delay


class Deadline(Hold):
    pass


@contextmanager
def total_deadline(seconds):
    """Serial POSIX total deadline, including slow-drip reads (as in bridge)."""
    require(threading.current_thread() is threading.main_thread(), "Serial main thread required")
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "Existing process timer conflicts")
    require(0 < seconds <= 25, "Per-request deadline bound")
    previous = signal.getsignal(signal.SIGALRM)
    def expire(*_):
        raise Deadline("Per-request total deadline exhausted")
    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class MemoryResponse(io.BytesIO):
    status = 200
    headers = {}


def observation_shape(body, sid, *, quality_policy=None):
    payload = decode(body)
    require(isinstance(payload, dict) and set(payload) <= OBSERVATION_ENVELOPE,
            "Unexpected observation envelope fields")
    rows, limit = payload.get("data"), payload.get("limit")
    require(isinstance(rows, list) and type(limit) is int and 0 < limit <= 2016 and len(rows) <= limit,
            "Effective observation limit")
    for name in ("total", "skip"):
        require(name not in payload or (type(payload[name]) is int and payload[name] >= 0),
                "Restricted observation pagination metadata")
    for row in rows:
        require(isinstance(row, dict) and set(row) <= OBSERVATION_FIELDS and
                row.get("datastream_id", sid) == sid, "Restricted or unselected observation metadata")
        parse_utc(row.get("t"))
        for name, value in row.items():
            if name == "q" and quality_policy is not None:
                from . import observation_quality as quality
                quality.validate_policy(quality_policy)
                quality.classify(row)
                continue
            require(value is None or type(value) in (str, int, float, bool), "Nested observation metadata")
            require(not isinstance(value, str) or len(value) <= 256, "Observation scalar bound")
    return payload


class Adapter:
    attempts_per_page = 2

    def __init__(self, journal):
        validate_binding(journal.binding, journal.tasks)
        require(not journal.damage, "Recovery journal cannot dispatch")
        self._initialize(journal, journal.binding["d3"]["authority"])

    def _initialize(self, journal, authority):
        self.journal = journal
        self.binding_hash = digest(journal.binding)
        self.authority = authority
        self.executor = self.wait = None
        self.active = False
        self.halted = False
        self.window_end = None
        self.permissions = {}
        self.vocabulary, self.stations = None, {}
        self.current_spec = self.current_interval = self.current_run = None
        self.successful = []

    def plan(self):
        return [s.descriptor() for s in seven_calls()]

    def remaining(self):
        require(not self.halted and not self.journal.damage, "Receipt failure requires review")
        require(digest(self.journal.binding) == self.binding_hash, "Adapter binding changed")
        elapsed = self.journal.check_budget()
        value = (self.journal.binding["budgets"]["elapsed_ms"] - elapsed) / 1000
        if self.window_end is not None:
            value = min(value, (parse_utc(self.window_end) - parse_utc(self.journal.now())).total_seconds())
        require(value > 0, "D3 wall ceiling exhausted")
        return value

    def pause(self, seconds):
        require(self.active and self.wait is not None and 0 <= seconds <= 15 and seconds < self.remaining(),
                "Retry delay cannot fit probe")
        counts = self.journal.snapshot()["counters"]
        require(counts["attempts"] < self.journal.binding["budgets"]["attempts"], "No retry attempts remain")
        self.wait(seconds)
        self.remaining()

    def _request(self, spec):
        return urllib.request.Request(spec.url(), method="GET", headers={
            "Accept": "application/json", "User-Agent": "BRIM-Dendra-D3/1.0"})

    def _details(self, spec, began, mono):
        return dict(kind=spec.kind, outcome="received", requested_at=began,
            retrieved_at=format_utc(self.journal.now()),
            duration_ms=max(0, int((self.journal.monotonic() - mono) * 1000)), retryable=False,
            retry_after_seconds=None, effective_limit=None, page_complete=None,
            privacy="not_evaluated", identity="not_evaluated", error_code=None)

    def _persist(self, operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except OSError as exc:
            self.halted = True
            raise Hold("Local receipt persistence failed; no HTTP retry") from exc

    def _parse_metadata(self, spec, body):
        if spec.kind == "unit-vocabulary":
            return parse_vocabulary(body, self.authority)
        if spec.kind == "station":
            station_id = self.journal.binding["roster"][spec.selected_stream]["station_id"]
            return parse_station(body, station_id, checked_at=self.journal.now(), now=self.journal.now())
        require(spec.kind == "datastream-list" and self.vocabulary is not None and
                spec.selected_stream in self.stations, "Metadata order")
        return parse_datastreams(body, self.journal.binding["roster"][spec.selected_stream],
            self.stations[spec.selected_stream], self.vocabulary, self.authority,
            checked_at=self.journal.now(), now=self.journal.now())

    def _validate_dispatch(self, request, spec, interval_key):
        validate_request(request, spec)

    def _page_permission(self, spec, interval_key):
        self._permission(spec.selected_stream)

    def _execute(self, request, *, timeout, interval_key):
        return self.executor(request, timeout=timeout)

    def _classify_error(self, error, details):
        return details

    def exchange(self, request, spec, *, interval_key=None, run=0):
        require(self.active and self.executor is not None, "Explicit runner required")
        diagnostic = self.journal.binding["mode"] == "history_diagnostic_adapter"
        require((diagnostic and spec.kind == "observations" and interval_key is None and run == 0) or
                (not diagnostic and (spec.kind == "observations") == (interval_key is not None)),
                "Receipt task kind mismatch")
        if interval_key is not None:
            require(interval_key in self.journal.tasks and self.journal.tasks[interval_key]["identity"]["stream_id"]
                    == spec.selected_stream, "Receipt selected-stream mismatch")
        self._validate_dispatch(request, spec, interval_key)
        remaining = self.remaining()
        counts = self.journal.snapshot()["counters"]
        budgets = self.journal.binding["budgets"]
        require(counts["response_bytes"] < budgets["response_bytes"] and
                counts["source_rows"] < budgets["source_rows"], "No response capacity remains")
        read_limit = min(BODY_LIMIT, budgets["response_bytes"] - counts["response_bytes"])
        if interval_key is not None:
            self._page_permission(spec, interval_key)
        witness = spec.kind == "authority-witness"
        require(not witness or self.journal.binding["mode"] == "authority_witness_adapter", "Witness journal required")
        task = ("witness-" + spec.selected_stream) if witness else interval_key or ("unit-vocabulary" if spec.kind == "unit-vocabulary"
                                else "metadata-" + spec.selected_stream)
        cursor = self.journal.binding["witness_requests"][task]["request_id"] if witness else spec.cursor if interval_key else spec.kind
        latest = spec.kind == "latest-witness"
        latest_diagnostic = False
        if latest:
            from .latest_observation import DIAGNOSTIC
            latest_diagnostic = self.journal.binding["version"] == DIAGNOSTIC
            require(self.journal.binding["mode"] == "latest_evidence_adapter", "Latest journal required")
            task, cursor = "latest-witness", self.journal.binding["request_id"]
        if diagnostic:
            task, cursor = "history-diagnostic", self.journal.binding["request_id"]
        key = self._persist(self.journal.reserve, task, cursor, interval_key=interval_key, run=run)
        self._persist(self.journal.started, key)
        began, mono = format_utc(self.journal.now()), self.journal.monotonic()
        body, status, row_count, value, headers, caught = b"", None, 0, None, {}, None
        payload = UNPARSED
        details = self._details(spec, began, mono)
        retain, sanitized = False, None
        try:
            remaining = self.remaining()
            with total_deadline(min(25, remaining)):
                try:
                    response = self._execute(request, timeout=min(25, remaining), interval_key=interval_key)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    status = response.status if getattr(response, "status", None) is not None else response.code
                    require(type(status) is int and 100 <= status <= 599, "Invalid response status")
                    headers = response.headers
                    require(headers.get("Content-Encoding", "identity").lower() == "identity",
                            "Encoded response body not supported")
                    # A hard total alarm bounds slow reads. Read one sentinel byte
                    # beyond the body ceiling; persist/count the bytes actually read.
                    while len(body) <= read_limit:
                        chunk = response.read(min(65536, read_limit + 1 - len(body)))
                        require(isinstance(chunk, bytes), "Response must be bytes")
                        if not chunk:
                            break
                        body += chunk
                        require(self.journal.monotonic() - mono <= 25, "Per-request elapsed ceiling")
                        self.remaining()
                details = self._details(spec, began, mono)
                require(len(body) <= BODY_LIMIT, "Response exceeds 8 MiB")
                require(len(body) <= read_limit, "Cumulative response-byte ceiling")
                if status != 200:
                    details.update(outcome="failure", error_code="redirect" if 300 <= status <= 399 else "http")
                    if status in RETRYABLE:
                        ordinal = self.journal.snapshot()["attempts"][key]["ordinal"]
                        if ordinal < self.attempts_per_page:
                            details["retryable"] = True
                            try:
                                details["retry_after_seconds"] = retry_delay(headers.get("Retry-After"),
                                    ordinal=ordinal, now=self.journal.now(), remaining=self.remaining())
                            except Hold:
                                details.update(outcome="hold", retryable=False, error_code="retry_after")
                                raise
                            details["outcome"] = "retry"
                    raise urllib.error.HTTPError(spec.url(), status, "D3 HTTP status", {}, None)
                row_count = None
                try:
                    payload = decode(body)
                    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
                        row_count = len(payload["data"])
                    elif spec.kind in ("station", "unit-vocabulary"):
                        row_count = 0
                    if diagnostic:
                        from .history_diagnostic import projection
                        value = projection(body, self.journal.binding, status)
                        sanitized = encode(value)
                        if value["admission"] == "REJECTED":
                            details.update(outcome="failure", error_code="parse_or_privacy",
                                           privacy="hold", identity="hold")
                        else:
                            details.update(privacy="public", identity="match")
                    elif witness:
                        from .authority_witness import response_shape
                        value = response_shape(body, spec.selected_stream, retrieved_at=self.journal.now())
                        details.update(effective_limit=1, page_complete=True)
                        retain = True
                    elif latest_diagnostic:
                        from .latest_observation import diagnostic_projection
                        value = diagnostic_projection(body, self.journal.binding,
                                                      retrieved_at=details["retrieved_at"])
                        sanitized = encode(value)
                        # Receipt success describes diagnostic persistence only.
                        # No admission/privacy/identity success or raw retention.
                    elif latest:
                        from .latest_observation import response_shape
                        value = response_shape(body, self.journal.binding, retrieved_at=self.journal.now())
                        # Complete bounded top-two selection, never interval coverage.
                        details.update(effective_limit=2, page_complete=True)
                        retain = True
                    elif spec.kind == "observations":
                        value = observation_shape(body, spec.selected_stream,
                            quality_policy=self.journal.binding.get("quality_policy"))
                        details.update(effective_limit=value["limit"], page_complete=len(value["data"]) < value["limit"])
                        retain = True
                    else:
                        value = self._parse_metadata(spec, body)
                        sanitized = encode(value)
                        if spec.kind == "datastream-list":
                            details.update(effective_limit=payload["limit"], page_complete=True)
                except (Hold, ValueError, TypeError, KeyError, RecursionError) as exc:
                    if isinstance(exc, Deadline) or spec.kind == "observations" or latest:
                        raise
                    if witness:
                        raise WitnessAdmissionHold(body, self.journal.binding["witness_requests"][task], status, exc) from None
                    raise MetadataAdmissionHold(spec, body, payload, exc) from None
                if not diagnostic and not latest_diagnostic:
                    details.update(privacy="public", identity="match")
                # Validate capacity before writing raw/sanitized objects, while
                # still charging rejected bytes/rows through the same receipt.
                self.journal.check_budget({"response_bytes": len(body), "source_rows": row_count or 0})
                require(self.journal.monotonic() - mono <= 25, "Per-request elapsed ceiling")
        except BaseException as exc:
            caught = exc
            retain = False
            sanitized = encode(exc.diagnostic) if isinstance(exc, (MetadataAdmissionHold, WitnessAdmissionHold)) else None
            # Recognized station objects contain zero observation/list rows;
            # parsed list arrays have an exact count even on admission failure.
            # Only the generalized metadata profile uses this structural count.
            counted_metadata = (status == 200 and self.journal.binding.get("version") == "dendra-roster-metadata-acquisition-1" and
                isinstance(payload, dict) and ((spec.kind == "station" and "data" not in payload) or
                (spec.kind == "datastream-list" and isinstance(payload.get("data"), list))))
            if body and value is None and status == 200 and row_count == 0 and not counted_metadata:
                row_count = None
            if details["error_code"] is None:
                transport = isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError))
                ordinal = self.journal.snapshot()["attempts"][key]["ordinal"]
                can_retry = transport and ordinal < self.attempts_per_page and row_count is not None
                details.update(outcome="retry" if can_retry else "failure" if transport else "hold",
                    retryable=can_retry, error_code="transport" if transport else
                    "deadline" if isinstance(exc, Deadline) else "body_limit" if len(body) > BODY_LIMIT
                    else "parse_or_privacy", privacy="hold", identity="hold")
        if caught is not None:
            details = self._classify_error(caught, details)
        details.update(retrieved_at=details["retrieved_at"] if latest_diagnostic and sanitized is not None
                       else format_utc(self.journal.now()),
                       duration_ms=max(0, int((self.journal.monotonic() - mono) * 1000)))
        # Reserve/start have already been durable even if this receipt cannot be
        # written (crash/storage failure). Never issue an unreserved retry.
        try:
            self._persist(self.journal.received, key, body, source_rows=row_count, status=status, retain=retain,
                          sanitized_body=sanitized, details=details)
        except UnknownSourceRowCount:
            if not isinstance(caught, (MetadataAdmissionHold, WitnessAdmissionHold)):
                raise
            # received() has already durably charged the response and saved its
            # diagnostic. End in the originating HOLD; never bypass the guard
            # for subsequent requests or suppress a storage/integrity failure.
        if diagnostic and caught is None and details["error_code"] is not None:
            self._persist(self.journal.failed, key, status)
        if caught is not None:
            self._persist(self.journal.failed, key, status)
            if isinstance(caught, urllib.error.HTTPError):
                if 300 <= status <= 399:
                    raise Hold("Redirect refused") from caught
                safe_headers = {}
                if details["retry_after_seconds"] is not None:
                    safe_headers["Retry-After"] = str(details["retry_after_seconds"])
                raise urllib.error.HTTPError(spec.url(), status, "D3 HTTP status", safe_headers, None) from None
            raise caught
        return body, value, key

    def metadata(self, spec):
        for attempt in (1, 2):
            try:
                return self.exchange(self._request(spec), spec)[1]
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRYABLE or attempt == 2:
                    raise Hold("Metadata HTTP failure") from None
                self.pause(float(exc.headers.get("Retry-After", "1")))
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
                if attempt == 2:
                    raise Hold("Metadata transport failure") from None
                self.pause(1)
        raise AssertionError("unreachable")

    def _permission(self, sid):
        require(sid in self.permissions, "No permission from this explicit metadata wave")
        view = self.journal.snapshot()["metadata"].get(sid)
        require(view and view["raw_eligible"] and view["checked_at"] == self.permissions[sid] and
                0 <= (parse_utc(self.journal.now()) - parse_utc(view["checked_at"])).total_seconds() <= 86400,
                "Fresh public metadata required; no cached-permission fallback")

    def observations(self, sid, *, recheck=False):
        self._permission(sid)
        key = next(k for k, v in self.journal.tasks.items() if v["identity"]["stream_id"] == sid)
        saved = self.journal.completed(key)
        if saved is not None and not recheck:
            return dict(cache_hit=True, envelope=saved)
        run = self.journal.start_run(key, recheck=recheck)
        successful = []
        def open_page(request, timeout):
            from urllib.parse import urlsplit, parse_qs
            params = parse_qs(urlsplit(request.full_url).query)
            cursor = params.get("time[$gte]", [None])[0]
            spec = RequestSpec("observations", sid, cursor)
            body, _, receipt = self.exchange(request, spec, interval_key=key, run=run)
            successful.append(receipt)
            return MemoryResponse(body)
        fetcher = DendraFetcher(opener=open_page, timeout=25, max_attempts=2, max_pages=20,
            page_size=2016, max_retry_delay=15, now_fn=lambda: parse_utc(self.journal.now()), sleep_fn=self.pause)
        try:
            result = fetcher.fetch_interval(sid, START, END)
            self.journal.seal(key, run, result, successful)
            return dict(cache_hit=False, envelope=result)
        except Exception:
            self.journal.hold(key, "transport_or_parse")
            raise

    def run(self, *, executor, wait, window_end=None):
        """Explicit injection boundary. Tests supply finite bytes and a fake clock."""
        require(callable(executor) and callable(wait) and not self.active, "Explicit serial runner required")
        require(threading.current_thread() is threading.main_thread(), "One local acquisition worker")
        if window_end is not None:
            require(0 < (parse_utc(window_end) - parse_utc(self.journal.now())).total_seconds() <= 300,
                    "Explicit runner window bound")
        self.window_end = window_end
        self.journal.session()
        self.executor, self.wait, self.active = executor, wait, True
        self.permissions = {}
        try:
            self.vocabulary = self.metadata(RequestSpec("unit-vocabulary"))
            self.stations = {}
            for sid in SELECTED:
                self.stations[sid] = self.metadata(RequestSpec("station", sid))
            for sid in SELECTED:
                claims = self.metadata(RequestSpec("datastream-list", sid))
                checked = format_utc(self.journal.now())
                view = self.journal.metadata(sid, claims, checked)
                require(view["raw_eligible"], "Metadata scientific/privacy HOLD")
                self.permissions[sid] = checked
            return {sid: self.observations(sid) for sid in SELECTED}
        except Exception:
            if len(self.permissions) < 2:
                # Clear any earlier permissive overlay even when a failed/404/
                # private response cannot yield safe complete metadata claims.
                for sid in SELECTED:
                    fields = self.authority["scientific"][sid]
                    denied = dict(self.journal.binding["roster"][sid], complete=False,
                        access_state="metadata-only", scientific_fields=fields,
                        scientific_sha256=self.authority["metadata_bindings"][sid],
                        dictionary_sha256=self.authority["dictionary_sha256"])
                    self.journal.metadata(sid, denied, self.journal.now())
            for key in self.journal.tasks:
                self.journal.hold(key, "metadata" if len(self.permissions) < 2 else "transport_or_parse")
            raise
        finally:
            self.executor = self.wait = None
            self.active = False
            self.permissions = {}


@dataclass(frozen=True)
class CampaignRequestSpec:
    """One exact campaign interval and its bounded advancing page cursor."""
    selected_stream: str
    start: str
    end: str
    cursor: str
    kind = "observations"

    def url(self):
        require(all(type(value) is str for value in (self.start, self.end, self.cursor)),
                "Campaign page requires exact UTC strings")
        require(all(format_utc(value) == value for value in (self.start, self.end, self.cursor)) and
                parse_utc(self.start) <= parse_utc(self.cursor) < parse_utc(self.end),
                "Campaign page cursor outside exact interval")
        return "https://api.dendra.science/v2/datapoints?" + urlencode({
            "datastream_id": self.selected_stream, "time[$gte]": self.cursor,
            "time[$lt]": self.end, "$sort[time]": 1, "$limit": 2016})


class CampaignAdapter(Adapter):
    """Reviewed native tasks using the accepted single journal/HTTP stack.

    A successful receipt without a seal never authorizes another run. Durable
    reservation/started ambiguity and provider throttling stop campaign traffic;
    a fully accounted malformed task can leave unrelated tasks available.
    """
    attempts_per_page = 1

    def _classify_error(self, error, details):
        from .observation_quality import UnsupportedQuality
        if isinstance(error, UnsupportedQuality) or str(error) in {"Unexpected observation envelope fields",
                          "Restricted or unselected observation metadata",
                          "Nested observation metadata", "Observation scalar bound"}:
            # Existing receipt schema: failure + parse/privacy identifies the
            # conservative global schema/identity stop; task malformed is HOLD.
            details.update(outcome="failure", error_code="parse_or_privacy", retryable=False)
        return details

    def __init__(self, journal):
        require(journal.binding.get("mode") in ("campaign_reviewed_adapter", "task37_recovery_adapter") and
                not journal.damage and not journal.inspect_only and journal.lock is not None,
                "Writable reviewed campaign journal required")
        from .campaign import policy
        limits = journal.binding["request_policy"]
        require(limits == policy(logical_requests=limits["logical_requests"],
                attempts=limits["http_attempts"], total_bytes=limits["total_bytes"],
                wall_seconds=limits["wall_seconds"]), "Exact campaign request policy required")
        self._initialize(journal, None)
        self.last_dispatch_mono = None

    def plan(self):
        return {key: task["native_task"]["request"] for key, task in self.journal.tasks.items()}

    def _traffic_guard(self):
        """Receipt state, rather than process memory, carries campaign pauses."""
        from .campaign_execution import Stop
        require(not self.journal.inspect_only and self.journal.lock is not None and
                self.journal.fs.fd is not None and not self.journal.damage and not self.halted,
                "Campaign writer unavailable or requires recovery")
        if (digest(self.journal.binding) != self.binding_hash or
                digest(self.journal.tasks) != self.journal.tasks_sha):
            raise Stop("Campaign adapter binding changed")
        state = self.journal.snapshot()
        require(state["counters"]["unknown_row_responses"] == 0,
                "Campaign paused: unknown response accounting")
        for attempt in state["attempts"].values():
            details = attempt.get("details", {})
            if details.get("outcome") == "failure" and details.get("error_code") == "parse_or_privacy":
                from .campaign_execution import Stop
                raise Stop("Campaign stopped: observation schema/identity changed")
            status = attempt.get("status")
            require(attempt["state"] not in {"reserved", "started"},
                    "Campaign paused: ambiguous spent provider attempt")
            require(status not in (None, 408, 429) and not (type(status) is int and status >= 500) and
                    details.get("error_code") not in {"transport", "deadline", "body_limit", "budget"},
                    "Campaign paused: provider failure or response budget")
            # A previous process cannot silently discard a successful page and
            # continue elsewhere after crashing before the task's seal.
            if attempt["state"] == "received" and attempt.get("interval_key") != self.current_interval:
                interval = state["intervals"][attempt["interval_key"]]
                require(interval["complete"] is not None or interval["state"] == "held",
                        "Campaign paused: unsealed receipt requires review")
        self.remaining()

    def _spacing(self):
        starts = [event for event in self.journal.events if event["kind"] == "started"]
        if not starts:
            return
        elapsed = (parse_utc(self.journal.now()) - parse_utc(starts[-1]["at"])).total_seconds()
        if self.last_dispatch_mono is not None:
            elapsed = min(elapsed, self.journal.monotonic() - self.last_dispatch_mono)
        delay = max(0.0, 1.0 - elapsed)
        if delay:
            self.pause(delay)
        require((parse_utc(self.journal.now()) - parse_utc(starts[-1]["at"])).total_seconds() >= 1 and
                (self.last_dispatch_mono is None or
                 self.journal.monotonic() - self.last_dispatch_mono >= 1),
                "Campaign dispatch spacing not satisfied")

    def _validate_dispatch(self, request, spec, interval_key):
        require(type(spec) is CampaignRequestSpec and interval_key in self.journal.tasks,
                "Exact campaign observation specification required")
        task = self.journal.tasks[interval_key]
        require(spec.selected_stream == task["identity"]["stream_id"] and
                (spec.start, spec.end) == (task["start"], task["end"]),
                "Campaign request interval/stream mismatch")
        initial = CampaignRequestSpec(spec.selected_stream, task["start"], task["end"], task["start"])
        require(task["native_task"]["request"] == {"method": "GET", "url": initial.url()},
                "Campaign initial request binding changed")
        validate_request(request, spec)
        attempts = [a for a in self.journal.snapshot()["attempts"].values()
                    if a.get("interval_key") == interval_key]
        require(len(attempts) < 3 and all(a["cursor"] != spec.cursor for a in attempts),
                "Campaign page ceiling or replay refused")
        expected_cursor = task["start"]
        if attempts:
            previous = attempts[-1]
            require(previous["state"] == "received" and previous.get("status") == 200 and
                    len(previous.get("objects", [])) == 1, "Campaign previous page is not admissible")
            raw = decode(self.journal.read_object(previous["objects"][0]))
            require(raw["data"] and len(raw["data"]) == raw["limit"],
                    "Campaign continuation requires a full previous page")
            expected_cursor = format_utc(raw["data"][-1]["t"])
            require(parse_utc(expected_cursor) > parse_utc(previous["cursor"]),
                    "Campaign continuation must advance")
        require(spec.cursor == expected_cursor, "Campaign page cursor differs from received source boundary")
        self._traffic_guard()
        self._spacing()

    def _page_permission(self, spec, interval_key):
        from .campaign_execution import authorize_task
        authorize_task(self.journal, interval_key, now=self.journal.now())
        sid = spec.selected_stream
        require(not any(a.get("status") in (401, 403, 404, 410) and
                        self.journal.tasks[a["interval_key"]]["identity"]["stream_id"] == sid
                        for a in self.journal.snapshot()["attempts"].values()),
                "Campaign stream provider-access HOLD")

    def _execute(self, request, *, timeout, interval_key):
        # Recheck local authority after the durable reservation, immediately
        # before the only injected/live dispatch boundary.
        from .campaign_execution import authorize_task
        authorize_task(self.journal, interval_key, now=self.journal.now())
        self.last_dispatch_mono = self.journal.monotonic()
        return self.executor(request, timeout=timeout)

    def observations(self, key, *, recheck=False):
        require(key in self.journal.tasks and not recheck, "Exact task key; campaign recheck forbidden")
        saved = self.journal.completed(key)
        if saved is not None:
            return dict(cache_hit=True, envelope=saved)
        require(self.active, "Explicit serial campaign runner required")
        state = self.journal.snapshot()
        require(not any(a.get("interval_key") == key for a in state["attempts"].values()),
                "Spent unsealed task requires operator review; no automatic replay")
        self._traffic_guard()
        task = self.journal.tasks[key]
        sid = task["identity"]["stream_id"]
        first = CampaignRequestSpec(sid, task["start"], task["end"], task["start"])
        self._page_permission(first, key)
        # A crash before reservation spent no provider attempt. Reuse its run;
        # an existing reservation can never reach this branch.
        run = state["intervals"][key]["runs"] or self.journal.start_run(key)
        successful = []
        self.current_interval = key
        def open_page(request, timeout):
            pairs = parse_qsl(urlsplit(request.full_url).query, keep_blank_values=True, strict_parsing=True)
            cursor = dict(pairs).get("time[$gte]")
            spec = CampaignRequestSpec(sid, task["start"], task["end"], cursor)
            body, _, receipt = self.exchange(request, spec, interval_key=key, run=run)
            successful.append(receipt)
            return MemoryResponse(body)
        fetcher = DendraFetcher(opener=open_page, timeout=25, max_attempts=1, max_pages=3,
            page_size=2016, max_retry_delay=0, now_fn=lambda: parse_utc(self.journal.now()),
            sleep_fn=self.pause)
        try:
            envelope = fetcher.fetch_interval(sid, task["start"], task["end"])
            from . import observation_quality as quality
            from ..transport import normalize_rows, _content_hash
            policy = self.journal.binding["quality_policy"]
            quality.validate_policy(policy)
            state = self.journal.snapshot()
            source_rows = [row for key in successful for row in decode(self.journal.read_object(
                state["attempts"][key]["objects"][0]))["data"]]
            rows, diagnostics = normalize_rows(source_rows, task["start"], task["end"], quality_policy=policy)
            envelope["rows"] = rows
            envelope["diagnostics"].update(diagnostics)
            envelope.update(quality_disposition=quality.summary(rows), query_state="QUERY_COMPLETE")
            envelope["content_sha256"] = _content_hash(envelope)
            try:
                self._persist(self.journal.seal, key, run, envelope, successful)
            except Hold as exc:
                from .campaign_execution import Stop
                raise Stop("Campaign archive seal validation/persistence failed") from exc
            return dict(cache_hit=False, envelope=envelope)
        except Exception as exc:
            if not self.journal.damage and not self.halted:
                self._persist(self.journal.hold, key, "transport_or_parse")
            from .campaign_execution import Stop
            if isinstance(exc, (ValueError, TypeError, KeyError, RecursionError)) and not isinstance(exc, (Hold, Stop)):
                raise Hold("Campaign observation schema HOLD") from exc
            raise
        finally:
            self.current_interval = None

    def run(self, *, executor, wait, window_end=None, task_keys=None):
        """Single execution path for reviewed live calls and injected offline IO."""
        require(callable(executor) and callable(wait) and not self.active and
                threading.current_thread() is threading.main_thread(),
                "Explicit serial main-thread campaign runner required")
        keys = list(self.journal.tasks) if task_keys is None else list(task_keys)
        require(len(set(keys)) == len(keys) and all(k in self.journal.tasks for k in keys),
                "Exact unique planned task keys required")
        if self.journal.binding["mode"] == "task37_recovery_adapter":
            fixed = self.journal.binding["authorization"]["window_end"]
            require(window_end is None or window_end == fixed, "Recovery deadline cannot be reset")
            window_end = fixed
        if window_end is not None:
            require(0 < (parse_utc(window_end) - parse_utc(self.journal.now())).total_seconds() <=
                    self.journal.binding["request_policy"]["wall_seconds"],
                    "Explicit campaign runner window bound")
        self.window_end = window_end
        results = {}
        for key in keys:
            saved = self.journal.completed(key)
            if saved is not None:
                results[key] = dict(cache_hit=True, envelope=saved)
        pending = [key for key in keys if key not in results]
        if not pending:
            return results
        self._traffic_guard()
        self.journal.session()
        self.executor, self.wait, self.active = executor, wait, True
        try:
            for key in pending:
                try:
                    results[key] = self.observations(key)
                except (Hold, FetchError) as exc:
                    # campaign_execution.Stop is deliberately separate from
                    # Hold and escapes this task-local catch.
                    results[key] = dict(held=True, reason=str(exc))
                    self._traffic_guard()
            return results
        finally:
            self.executor = self.wait = None
            self.active = False


class WitnessAdapter(Adapter):
    """One ascending-first request per exact target, using the shared exchange.

    Approval is an explicit trusted caller input, not inferred from a packet.
    There is no retry/resume dispatcher: preserved receipts are reviewed offline.
    """
    attempts_per_page = 1

    def __init__(self, journal):
        from .authority_witness import validate_binding
        validate_binding(journal.binding, journal.tasks, inventory=journal.inventory)
        require(not journal.damage and not journal.inspect_only and journal.lock is not None,
                "Writable witness journal required")
        self._initialize(journal, None)
        self.last_dispatch_mono = None
        self.used = False

    def plan(self):
        return self.journal.binding["witness_requests"]

    def _validate_dispatch(self, request, spec, interval_key):
        from .authority_witness import RequestSpec as WitnessSpec, check_metadata
        from .model import source_binding
        require(type(spec) is WitnessSpec and interval_key is None and spec is self.current_spec,
                "Exact serial witness specification required")
        key = "witness-" + spec.selected_stream
        require(key in self.plan() and spec.descriptor() == self.plan()[key]["request"],
                "Witness differs from frozen request")
        require(self.journal.binding["collector_sources"] == source_binding(), "Witness source changed")
        validate_request(request, spec)
        check_metadata(self.journal.inventory, spec.selected_stream,
                       self.journal.binding["metadata_packets"][spec.selected_stream], now=self.journal.now())
        CampaignAdapter._spacing(self)

    def _execute(self, request, *, timeout, interval_key):
        from .authority_witness import check_metadata
        from .model import source_binding
        require(self.journal.binding["collector_sources"] == source_binding(), "Witness source changed")
        check_metadata(self.journal.inventory, self.current_spec.selected_stream,
                       self.journal.binding["metadata_packets"][self.current_spec.selected_stream], now=self.journal.now())
        self.remaining()
        self.last_dispatch_mono = self.journal.monotonic()
        return self.executor(request, timeout=timeout)

    def run(self, *, executor, wait, authorization):
        from .authority_witness import RequestSpec as WitnessSpec, evidence
        require(callable(executor) and callable(wait) and not self.active and not self.used and
                threading.current_thread() is threading.main_thread(), "Explicit one-shot serial witness runner required")
        require(isinstance(authorization, dict) and set(authorization) == {
            "binding_sha256", "approval_reference", "window_start", "window_end"} and
            authorization["binding_sha256"] == self.binding_hash and
            isinstance(authorization["approval_reference"], str) and
            0 < len(authorization["approval_reference"]) <= 160, "Exact witness execution approval required")
        start, end, now = map(parse_utc, (authorization["window_start"], authorization["window_end"], self.journal.now()))
        require(start <= now < end and (end-start).total_seconds() <= 60, "Witness execution window")
        require(not self.journal.snapshot()["attempts"], "Spent witness state requires review; no replay")
        self.window_end = authorization["window_end"]
        self.journal.session()
        self.used = True
        self.executor, self.wait, self.active = executor, wait, True
        results = {}
        try:
            for sid in self.journal.binding["selected_ids"]:
                identity = self.journal.binding["roster"][sid]
                self.current_spec = WitnessSpec(identity["station_id"], sid, identity)
                self.exchange(self._request(self.current_spec), self.current_spec)
                results[sid] = evidence(self.journal, sid)
            return results
        finally:
            self.current_spec = self.executor = self.wait = None
            self.active = False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Redirect refused", headers, fp)


def anonymous_executor(window_end):
    """Shared anonymous live transport; caller must verify separate approval."""
    from .journal import utc_now
    end = parse_utc(window_end)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    def execute(request, timeout):
        remaining = (end - parse_utc(utc_now())).total_seconds()
        require(remaining > 0, "Approved resource window ended")
        return opener.open(request, timeout=min(timeout, remaining))
    return execute


def anonymous_wait(window_end):
    """Only future explicitly authorized live callers use real sleeps."""
    from .journal import utc_now
    end = parse_utc(window_end)
    def wait(seconds):
        require(0 <= seconds <= 15 and (end - parse_utc(utc_now())).total_seconds() > seconds,
                "Wait exceeds authorized window")
        time.sleep(seconds)
    return wait


def run_authorized_probe(journal, *, authorization):
    """Live entry only for a future explicit task; never invoked by this gate.

    This declaration records the caller's reviewed authority; it is not a way
    to create permission. No default arguments, environment flag, CLI or daemon.
    """
    require(isinstance(authorization, dict) and set(authorization) == {
        "approval_reference", "binding_sha256", "task_root", "window_start", "window_end"},
        "Separate exact D3 authorization required")
    require(isinstance(authorization["approval_reference"], str) and
            1 <= len(authorization["approval_reference"]) <= 160, "Approval reference required")
    from .journal import Journal, utc_now
    require(type(journal) is Journal and journal.now is utc_now and journal.monotonic is time.monotonic,
            "Live entry requires real journal clocks; injected clocks are offline only")
    validate_binding(journal.binding)
    require(authorization["binding_sha256"] == digest(journal.binding), "Approved binding differs")
    # Compare an already open directory identity; do not create production roots.
    import os
    root = Path(authorization["task_root"])
    require(root.is_absolute() and not root.is_symlink() and root.is_dir(), "Explicit existing task root")
    require(os.stat(root).st_ino == os.fstat(journal.fs.fd).st_ino and
            os.stat(root).st_dev == os.fstat(journal.fs.fd).st_dev, "Approved root differs")
    start, end, now = map(parse_utc, (authorization["window_start"], authorization["window_end"], journal.now()))
    require(start <= now < end and (end - start).total_seconds() <= 300, "Approved probe window")
    return Adapter(journal).run(executor=anonymous_executor(authorization["window_end"]),
        wait=anonymous_wait(authorization["window_end"]), window_end=authorization["window_end"])
