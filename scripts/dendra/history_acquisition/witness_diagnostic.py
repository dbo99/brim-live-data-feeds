"""Bounded rejection evidence only; never parses a witness into authority.

Fixed field names/types and pagination integers explain the unchanged guards.
No observation values, timestamps, returned identifiers or unknown keys escape.
"""
from ..transport import parse_utc
from .safety import Hold, require, encode, decode, digest, sha

VERSION = "dendra-witness-diagnostic-1"
MAX_BYTES = 12288
MAX_ROWS = 2
MAX_CONTROL = 2**53 - 1
TOP = ("data", "limit", "skip", "total", "offset", "count")
ROW = ("_id", "datastream_id", "station_id", "t", "v", "lt", "q")
REASONS = {
    "Unexpected observation envelope fields": "envelope.fields",
    "Effective observation limit": "envelope.data_limit",
    "Restricted observation pagination metadata": "envelope.pagination",
    "Restricted or unselected observation metadata": "row.structure_or_identity",
    "Nested observation metadata": "row.nested",
    "Observation scalar bound": "row.scalar_bound",
    "First witness completeness/selection": "witness.completeness_selection",
    "Ambiguous empty first witness": "witness.ambiguous_empty",
    "Invalid first witness observation": "witness.observation",
    "Invalid JSON": "json.invalid",
    "Duplicate JSON key": "json.duplicate_key",
    "Nonfinite JSON number": "json.nonfinite",
    "Nonstandard JSON constant": "json.nonstandard",
}
SITES = {"provider_adapter": {"observation_shape", "exchange"},
         "authority_witness": {"response_shape"}, "transport": {"parse_utc"},
         "safety": {"decode", "pairs", "number", "constant"}}


def _type(value):
    return {dict: "object", list: "array", str: "string", int: "integer",
            float: "number", bool: "boolean", type(None): "null"}.get(type(value), "unparsed")


def _fields(value, names, *, controls=False):
    result = {}
    for name in names:
        present = type(value) is dict and name in value
        item = value.get(name) if present else None
        field = dict(present=present, json_type=_type(item) if present else "missing")
        if controls and name != "data" and type(item) is int:
            field["value_withheld"] = abs(item) > MAX_CONTROL
            if not field["value_withheld"]:
                field["value"] = item
        result[name] = field
    return result


def projection(body, request, status, reason):
    """Deterministic bounded shape projection, not an alternate admission rule."""
    from .authority_witness import RequestSpec
    identity = request["request"]["identity"]
    require(request["request"] == RequestSpec(identity["station_id"], identity["stream_id"]).descriptor()
            and request["request_id"] == digest({k:v for k,v in request.items() if k != "request_id"}),
            "Diagnostic exact witness binding")
    require(type(status) is int and status == 200 and isinstance(body, bytes) and len(body) <= 8*1024**2,
            "Diagnostic complete parser response required")
    require(set(reason) == {"code", "parser_site"} and reason["code"] in
            set(REASONS.values()) | {"parser.condition", "row.timestamp"}, "Diagnostic reason")
    site = reason["parser_site"]
    require(site is None or (type(site) is dict and set(site) == {"module", "function", "line"}
            and site["module"] in SITES and site["function"] in SITES[site["module"]]
            and type(site["line"]) is int and 0 < site["line"] <= 100000), "Diagnostic location")
    parsed = True
    try:
        value = decode(body)
    except (Hold, ValueError, TypeError, RecursionError):
        value = None; parsed = False
    obj = value if type(value) is dict else {}
    data = obj.get("data"); rows = data if type(data) is list else None
    count = len(rows) if rows is not None else None
    limit, skip, total = obj.get("limit"), obj.get("skip", 0), obj.get("total")
    checks = dict(root_object=parsed and type(value) is dict,
        envelope_fields=type(value) is dict and set(value) <= {"data", "limit", "skip", "total"},
        data_present="data" in obj, data_array=rows is not None,
        limit_integer=type(limit) is int, limit_one=type(limit) is int and limit == 1,
        rows_at_most_one=count is not None and count <= 1,
        skip_zero=type(skip) is int and skip == 0,
        total_present="total" in obj, total_integer=type(total) is int,
        total_covers_rows=type(total) is int and count is not None and total >= count,
        empty_unambiguous=count != 0 or (type(total) is int and total == 0))
    selected = []
    for row in (rows or [])[:MAX_ROWS]:
        is_object = type(row) is dict
        t = row.get("t") if is_object else None
        valid_time = False
        if type(t) is str and len(t) <= 256:
            try:
                parse_utc(t); valid_time = True
            except (ValueError, TypeError, OverflowError):
                pass
        selected.append(dict(json_type=_type(row), fields=_fields(row, ROW),
            omitted_field_count=len(row)-sum(k in row for k in ROW) if is_object else 0,
            allowed_fields=is_object and set(row) <= {"_id", "datastream_id", "t", "v", "lt", "q"},
            stream_matches=is_object and row.get("datastream_id", identity["stream_id"]) == identity["stream_id"],
            timestamp_parseable=valid_time))
    completeness = all(checks.values())
    selection = checks["rows_at_most_one"] and checks["skip_zero"] and all(
        row["allowed_fields"] and row["stream_matches"] for row in selected)
    result = dict(version=VERSION, kind="authority-witness", request=request["request"],
        request_id=request["request_id"], bound_request_sha256=digest(request), identity=identity,
        http_status=status, body_bytes=len(body), body_sha256=sha(body), complete_body=True,
        root_json_type=_type(value) if parsed else "unparsed", fields=_fields(obj, TOP, controls=True),
        omitted_top_level_field_count=len(obj)-sum(k in obj for k in TOP),
        returned_row_count=count, requested_limit=1, rows=selected,
        rows_truncated=count is not None and count > MAX_ROWS, checks=checks,
        completeness_check="PASS" if completeness else "FAIL",
        selection_check="PASS" if selection else "FAIL",
        ordering_check="NOT_EVALUATED_SINGLE_ROW_REQUIRED", reason=reason,
        admitted=False, source_start_reviewed=False, dispatch_ready=False)
    result["diagnostic_sha256"] = digest(result)
    require(len(encode(result)) <= MAX_BYTES, "Witness diagnostic byte ceiling")
    return result


class WitnessAdmissionHold(Hold):
    def __init__(self, body, request, status, cause):
        site = None; tb = cause.__traceback__
        while tb:
            module = tb.tb_frame.f_globals.get("__name__", "").split(".")[-1]
            function = tb.tb_frame.f_code.co_name
            if function in SITES.get(module, ()):
                site = dict(module=module, function=function, line=tb.tb_lineno)
            tb = tb.tb_next
        code = REASONS.get(str(cause), "parser.condition")
        if site is not None and site["function"] == "parse_utc": code = "row.timestamp"
        self.diagnostic = projection(body, request, status, dict(code=code, parser_site=site))
        super().__init__("Witness admission HOLD: " + code)


def validate(body, sanitized, request, status):
    require(isinstance(sanitized, bytes) and len(sanitized) <= MAX_BYTES, "Witness diagnostic bound")
    value = decode(sanitized)
    require(type(value) is dict and "reason" in value and
            encode(projection(body, request, status, value["reason"])) == sanitized,
            "Witness diagnostic body/request binding")
