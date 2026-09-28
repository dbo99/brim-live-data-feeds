"""One incident-linked history-page shape diagnostic, never an acquisition retry.

Preparation and inspection are offline. Live authorization is a separate trusted
caller input. The caller must preserve the unique diagnostic namespace/budget;
copying state or changing roots never grants another request.
"""
import re
import threading

from ..transport import parse_utc
from .model import source_binding
from .safety import Hold, decode, digest, encode, require, sha
from .provider_adapter import Adapter, CampaignRequestSpec, observation_shape
from .d3_plan import IDENTITIES, validate_request

MODE = "history_diagnostic_adapter"
VERSION = "dendra-history-page-diagnostic-1"
MAX_BYTES = 12288
BODY_BYTES = 8 * 1024**2
SLOTS = ("_id", "datastream_id", "t", "v", "lt", "q")
TOP = ("data", "limit", "total", "skip")
Q_SLOTS = ("attrib", "flag", "annotation_ids", "annotationIds")
Q_COUNT_CAP = 256
JSON_TYPES = ("null", "boolean", "integer", "number", "string", "array", "object")


def incident(journal, task_id):
    """Extract a bounded link from an intact read-only original failed journal."""
    require(journal.inspect_only and journal.binding["mode"] == "campaign_reviewed_adapter",
            "Read-only original campaign required")
    journal.verify_records()
    require(task_id in journal.tasks, "Incident task outside original plan")
    task = journal.tasks[task_id]
    require(digest(task["native_task"]["identity"]) == task_id and
            task["native_task"]["task_id"] == task_id, "Incident logical identity")
    require(task["identity"] == task["native_task"]["identity"]["frozen_identity"], "Incident frozen identity")
    sid = task["identity"]["stream_id"]
    spec = CampaignRequestSpec(sid, task["start"], task["end"], task["start"])
    require(task["native_task"]["request"] == dict(method="GET", url=spec.url()), "Incident first request")
    state = journal.snapshot()
    attempts = [a for a in state["attempts"].values() if a["interval_key"] == task_id]
    require(len(attempts) == 1 and attempts[0]["state"] == "failure" and
            state["intervals"][task_id]["state"] == "held" and
            journal.completed(task_id) is None, "One spent unsealed incident required")
    a = attempts[0]
    require(a["cursor"] == task["start"] and a["status"] == 200 and
            a["representation"] == "omitted" and not a["objects"] and
            a["details"]["error_code"] == "parse_or_privacy", "Exact omitted first-page incident")
    records = {}
    for kind in ("reserved", "started", "received", "failure"):
        found = [e for e in journal.events if e["kind"] == kind and e["data"].get("attempt_key") == a["attempt_key"]]
        require(len(found) == 1, "Incident receipt closure")
        records[kind] = found[0]["record_sha256"]
    return dict(task_id=task_id, campaign_id=journal.binding["campaign_id"],
        header_sha256=journal.header_sha, original_source=digest(journal.binding["collector_sources"]),
        identity=task["identity"], start=task["start"], end=task["end"], request=task["native_task"]["request"],
        attempt_key=a["attempt_key"], records=records, response_sha256=a["response_sha256"],
        response_bytes=a["response_bytes"])


def prepare(original, task_id, *, checkpoint):
    """No writes/dispatch. Checkpoint is caller-verified; source bytes are checked here."""
    return _binding(incident(original, task_id), checkpoint), {}


def _binding(link, checkpoint):
    require(type(checkpoint) is str and re.fullmatch(r"[0-9a-f]{40}", checkpoint), "Explicit source checkpoint")
    require(type(link) is dict and set(link) == {"task_id", "campaign_id", "header_sha256", "original_source",
        "identity", "start", "end", "request", "attempt_key", "records", "response_sha256", "response_bytes"},
        "Incident fields")
    require(set(link["records"]) == {"reserved", "started", "received", "failure"}, "Incident records")
    for value in [link[k] for k in ("task_id", "header_sha256", "original_source", "attempt_key", "response_sha256")] + list(link["records"].values()):
        require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "Incident hash")
    require(type(link["response_bytes"]) is int and 0 < link["response_bytes"] <= BODY_BYTES,
            "Incident body bound")
    from .model import NAME
    require(type(link["campaign_id"]) is str and NAME.fullmatch(link["campaign_id"]), "Incident campaign")
    sid = link["identity"].get("stream_id")
    require(sid in IDENTITIES and link["identity"] == IDENTITIES[sid], "Frozen diagnostic target")
    spec = CampaignRequestSpec(sid, link["start"], link["end"], link["start"])
    require(link["request"] == dict(method="GET", url=spec.url()), "Exact saved request required")
    sources = source_binding()
    request_id = digest(dict(incident=link, checkpoint=checkpoint, collector_sources=sources))
    return dict(version=VERSION, mode=MODE, campaign_id="history-diag-"+request_id,
        collector_sources=sources, checkpoint=checkpoint, incident=link, request_id=request_id,
        selected_ids=[sid], roster={sid:link["identity"]},
        budgets=dict(logical_requests=1, attempts=1, response_bytes=BODY_BYTES,
                     source_rows=2016, intervals=0, elapsed_ms=60000, sessions=1))


def validate_binding(binding, tasks, *, inventory=None):
    require(tasks == {} and encode(binding) == encode(_binding(binding["incident"], binding["checkpoint"])),
            "Diagnostic source/request/budget binding changed")


def _type(value):
    return {type(None):"null", bool:"boolean", int:"integer", float:"number",
            str:"string", list:"array", dict:"object"}.get(type(value), "invalid")


def _q_structure(value):
    """Immediate types only: never copy keys/values or descend into children."""
    slots = {}
    for name in Q_SLOTS:
        item = value.get(name)
        shape = dict(present=name in value, type=_type(item) if name in value else "missing")
        if type(item) in (list, dict):
            counts = dict.fromkeys(JSON_TYPES, 0)
            for child in item.values() if type(item) is dict else item:
                kind = _type(child)
                counts[kind] = min(Q_COUNT_CAP, counts[kind] + 1)
            shape.update(count=min(len(item), Q_COUNT_CAP), count_truncated=len(item) > Q_COUNT_CAP,
                         child_type_counts=counts)
        slots[name] = shape
    unknown = sum(name not in Q_SLOTS for name in value)
    return dict(slots=slots, total_q_member_count=min(len(value), Q_COUNT_CAP),
                total_q_member_count_truncated=len(value) > Q_COUNT_CAP,
                unknown_q_key_count=min(unknown, Q_COUNT_CAP),
                unknown_q_key_count_truncated=unknown > Q_COUNT_CAP,
                both_annotation_aliases_present=all(name in value for name in Q_SLOTS[2:]))


def _validate_q_structure(value):
    require(type(value) is dict and set(value) == {
        "slots", "total_q_member_count", "total_q_member_count_truncated", "unknown_q_key_count",
        "unknown_q_key_count_truncated", "both_annotation_aliases_present"}, "Quality structure fields")
    for name in ("total_q_member_count", "unknown_q_key_count"):
        require(type(value[name]) is int and 0 <= value[name] <= Q_COUNT_CAP and
                type(value[name + "_truncated"]) is bool and
                (not value[name + "_truncated"] or value[name] == Q_COUNT_CAP), "Quality count bound")
    require(type(value["slots"]) is dict and set(value["slots"]) == set(Q_SLOTS), "Quality slots")
    for shape in value["slots"].values():
        require(type(shape) is dict and type(shape.get("present")) is bool and
                shape.get("type") in ("missing", *JSON_TYPES) and
                shape["present"] == (shape["type"] != "missing"), "Quality slot presence/type")
        container = shape["type"] in ("array", "object")
        require(set(shape) == ({"present", "type", "count", "count_truncated", "child_type_counts"}
                              if container else {"present", "type"}), "Quality slot fields")
        if container:
            count, truncated, types = shape["count"], shape["count_truncated"], shape["child_type_counts"]
            require(type(count) is int and 0 <= count <= Q_COUNT_CAP and type(truncated) is bool and
                    (not truncated or count == Q_COUNT_CAP) and type(types) is dict and
                    set(types) == set(JSON_TYPES) and
                    all(type(n) is int and 0 <= n <= count for n in types.values()), "Quality child counts")
            require(sum(types.values()) >= count if truncated else sum(types.values()) == count,
                    "Quality child count closure")
    known = sum(shape["present"] for shape in value["slots"].values())
    total, unknown = value["total_q_member_count"], value["unknown_q_key_count"]
    require(total == min(Q_COUNT_CAP, known + unknown) and
            value["total_q_member_count_truncated"] ==
            (value["unknown_q_key_count_truncated"] or known + unknown > Q_COUNT_CAP),
            "Quality member count closure")
    require(type(value["both_annotation_aliases_present"]) is bool and
            value["both_annotation_aliases_present"] ==
            all(value["slots"][name]["present"] for name in Q_SLOTS[2:]), "Quality alias presence")


def projection(body, binding, status):
    """Fixed structural schema. Invoke the unchanged parser; never retain its text."""
    validate_binding(binding, {})
    require(type(body) is bytes and len(body) <= BODY_BYTES and type(status) is int and status == 200,
            "Complete bounded HTTP 200 body required")
    sid = binding["selected_ids"][0]
    accepted = False
    try:
        observation_shape(body, sid)
        accepted = True
    except (Hold, ValueError, TypeError, KeyError, RecursionError):
        pass
    parsed = True
    try:
        value = decode(body)
    except (Hold, ValueError, TypeError, RecursionError):
        value = None
        parsed = False
    obj = value if type(value) is dict else {}
    rows = obj.get("data") if type(obj.get("data")) is list else None
    code, slot, offending = "NONE", None, None
    site = None
    if not accepted:
        site = "observation_shape"
        if not parsed:
            code, site = "json.invalid", "decode"
        elif type(value) is not dict:
            code = "envelope.nonobject"
        elif set(obj) - set(TOP):
            code = "envelope.unknown_field"
        elif rows is None or type(obj.get("limit")) is not int or not 0 < obj["limit"] <= 2016 or len(rows) > obj["limit"]:
            code = "envelope.limit"
        elif any(k in obj and (type(obj[k]) is not int or obj[k] < 0) for k in ("total", "skip")):
            code = "envelope.pagination"
        else:
            # Follow admission order, including JSON field iteration order.
            for i, row in enumerate(rows):
                if type(row) is not dict:
                    code = "row.nonobject"
                elif set(row) - set(SLOTS):
                    code = "row.unknown_field"
                elif row.get("datastream_id", sid) != sid:
                    code = "row.stream_mismatch"
                else:
                    try:
                        parse_utc(row.get("t"))
                    except (ValueError, TypeError, OverflowError):
                        code, slot, site = "row.timestamp", "t", "parse_utc"
                    if code == "NONE":
                        for k, v in row.items():
                            if v is not None and type(v) not in (str, int, float, bool):
                                code, slot = "row.nested", k
                                break
                            if type(v) is str and len(v) > 256:
                                code, slot = "row.scalar_bound", k
                                break
                if code != "NONE":
                    offending = i
                    break
        require(code != "NONE", "Unclassified parser rejection; diagnostic omitted")
    row = rows[offending] if offending is not None else None
    rowobj = row if type(row) is dict else {}
    result = dict(version=VERSION, request_id=binding["request_id"], binding_sha256=digest(binding),
        incident_task_id=binding["incident"]["task_id"], source_fingerprint=digest(binding["collector_sources"]),
        http_status=status, response_bytes=len(body), response_sha256=sha(body),
        admission="PASSED_SHAPE_ONLY" if accepted else "REJECTED", guard_code=code, parser_site=site,
        root_type=_type(value) if parsed else "unparsed", unknown_envelope_keys=len(set(obj)-set(TOP)),
        row_count=len(rows) if rows is not None else None, first_offending_row=offending,
        row_is_object=type(row) is dict, unknown_row_keys=len(set(rowobj)-set(SLOTS)),
        explicit_stream_present="datastream_id" in rowobj,
        selected_stream_match=(rowobj.get("datastream_id", sid) == sid) if type(row) is dict else None,
        fields={k:dict(present=k in rowobj, type=_type(rowobj[k]) if k in rowobj else "missing") for k in SLOTS},
        offending_slot=slot, nested_value=code == "row.nested", scalar_bound_exceeded=code == "row.scalar_bound",
        acquisition_eligible=False, source_start_reviewed=False, dispatch_ready=False, sealed=False)
    if code == "row.nested" and slot == "q" and type(rowobj.get("q")) is dict:
        result["q_structure"] = _q_structure(rowobj["q"])
    validate_schema(result)
    require(len(encode(result)) <= MAX_BYTES, "History diagnostic byte ceiling")
    return result


def validate_schema(value):
    """Independent fixed wire allowlist, including exact JSON scalar types."""
    hashes = {"request_id", "binding_sha256", "incident_task_id", "source_fingerprint", "response_sha256"}
    flags = {"row_is_object", "explicit_stream_present", "nested_value", "scalar_bound_exceeded",
             "acquisition_eligible", "source_start_reviewed", "dispatch_ready", "sealed"}
    counts = {"response_bytes", "unknown_envelope_keys", "unknown_row_keys"}
    optional_counts = {"row_count", "first_offending_row"}
    other = {"version", "http_status", "admission", "guard_code", "parser_site", "root_type",
             "selected_stream_match", "fields", "offending_slot"}
    require(type(value) is dict and set(value) - {"q_structure"} == hashes | flags | counts | optional_counts | other,
            "History diagnostic fields")
    require(all(type(value[k]) is str and re.fullmatch(r"[0-9a-f]{64}",value[k]) for k in hashes) and
            all(type(value[k]) is bool for k in flags) and
            all(type(value[k]) is int and 0 <= value[k] <= BODY_BYTES for k in counts) and
            all(value[k] is None or (type(value[k]) is int and 0 <= value[k] <= BODY_BYTES) for k in optional_counts),
            "History diagnostic scalar types")
    require(value["version"] == VERSION and type(value["http_status"]) is int and value["http_status"] == 200 and
            value["admission"] in ("PASSED_SHAPE_ONLY", "REJECTED") and
            value["guard_code"] in ("NONE", "json.invalid", "envelope.nonobject", "envelope.unknown_field",
                "envelope.limit", "envelope.pagination", "row.nonobject", "row.unknown_field",
                "row.stream_mismatch", "row.timestamp", "row.nested", "row.scalar_bound") and
            value["parser_site"] in (None, "decode", "observation_shape", "parse_utc") and
            value["root_type"] in ("null","boolean","integer","number","string","array","object","unparsed") and
            (value["selected_stream_match"] is None or type(value["selected_stream_match"]) is bool) and
            value["offending_slot"] in (None, *SLOTS) and
            all(value[k] is False for k in ("acquisition_eligible","source_start_reviewed","dispatch_ready","sealed")),
            "History diagnostic enum/authority bounds")
    require(type(value["fields"]) is dict and set(value["fields"]) == set(SLOTS), "History diagnostic slots")
    for field in value["fields"].values():
        require(type(field) is dict and set(field) == {"present","type"} and type(field["present"]) is bool and
                field["type"] in ("missing","null","boolean","integer","number","string","array","object"),
                "History diagnostic slot type")
    if "q_structure" in value:
        require(value["admission"] == "REJECTED" and value["guard_code"] == "row.nested" and
                value["offending_slot"] == "q" and value["nested_value"] is True and
                value["first_offending_row"] is not None and value["row_is_object"] is True and
                value["fields"]["q"] == dict(present=True, type="object"), "Quality structure gate")
        _validate_q_structure(value["q_structure"])


def validate(body, sanitized, binding, status):
    require(type(sanitized) is bytes and len(sanitized) <= MAX_BYTES, "History diagnostic byte ceiling")
    validate_schema(decode(sanitized))
    # Canonical byte comparison rejects unknown fields and bool/int substitution.
    require(encode(decode(sanitized)) == sanitized and
            encode(projection(body, binding, status)) == sanitized, "History diagnostic projection binding")


class HistoryDiagnosticAdapter(Adapter):
    """Single separately charged diagnostic request; no acquisition task namespace."""
    attempts_per_page = 1

    def __init__(self, journal, original):
        validate_binding(journal.binding, journal.tasks)
        require(not journal.inspect_only and not journal.damage and journal.lock is not None,
                "Writable diagnostic journal required")
        self.original = original
        self._initialize(journal, None)
        self.used = False

    def _check_incident(self):
        validate_binding(self.journal.binding, self.journal.tasks)
        link = self.journal.binding["incident"]
        require(encode(incident(self.original, link["task_id"])) == encode(link), "Original incident binding changed")

    def _validate_dispatch(self, request, spec, interval_key):
        self._check_incident()
        link = self.journal.binding["incident"]
        require(type(spec) is CampaignRequestSpec and spec is self.current_spec and interval_key is None and
                spec.cursor == link["start"] and request.full_url == link["request"]["url"],
                "Diagnostic first page only")
        validate_request(request, spec)

    def _execute(self, request, *, timeout, interval_key):
        self._check_incident()
        self.remaining()
        return self.executor(request, timeout=timeout)

    def run(self, *, executor, wait, authorization):
        require(callable(executor) and callable(wait) and not self.active and not self.used and
                threading.current_thread() is threading.main_thread(), "One-shot serial diagnostic required")
        self._check_incident()
        require(type(authorization) is dict and set(authorization) == {
            "binding_sha256", "checkpoint", "incident_sha256", "approval_reference", "window_start", "window_end"} and
            authorization["binding_sha256"] == self.binding_hash and
            authorization["checkpoint"] == self.journal.binding["checkpoint"] and
            authorization["incident_sha256"] == digest(self.journal.binding["incident"]) and
            type(authorization["approval_reference"]) is str and
            0 < len(authorization["approval_reference"]) <= 160, "Separate diagnostic approval required")
        start, end, now = map(parse_utc, (authorization["window_start"], authorization["window_end"], self.journal.now()))
        require(start <= now < end and (end-start).total_seconds() <= 60, "Diagnostic execution window")
        require(not self.journal.snapshot()["attempts"], "Spent diagnostic cannot replay")
        # A conservative initial wait also separates this sole start from the
        # caller's preceding serial request, including a campaign boundary.
        self.window_end = authorization["window_end"]
        self.used = True
        self.journal.session()
        self.executor, self.wait, self.active = executor, wait, True
        link = self.journal.binding["incident"]
        self.current_spec = CampaignRequestSpec(link["identity"]["stream_id"], link["start"], link["end"], link["start"])
        try:
            before = self.journal.monotonic()
            self.pause(1)
            require(self.journal.monotonic()-before >= 1, "Diagnostic request-start spacing")
            _, result, _ = self.exchange(self._request(self.current_spec), self.current_spec)
            return result
        finally:
            self.current_spec = self.executor = self.wait = None
            self.active = False
