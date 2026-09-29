"""Pure, explicit two-stream probe plan; creating it never authorizes HTTP."""
from dataclasses import dataclass
import math
from urllib.parse import urlencode, urlsplit, parse_qsl

from ..transport import format_utc, parse_utc
from .model import campaign, plan, INVENTORY_SHA256, Inventory
from .safety import require, encode, decode, digest

BASE = "https://api.dendra.science/v2/"
START = "2024-02-29T08:00:00.000Z"
END = "2024-03-01T08:00:00.000Z"
CAMP = "63531a67a9b61453fa1ca4ed"
DEEP = "5d8e42e72da5c3cc53f6531d"
SELECTED = (CAMP, DEEP)
IDENTITIES = {
    CAMP: dict(station_id="635319fcb055ac5348842453", stream_id=CAMP, depth_cm=20,
               orientation="horizontal", native_unit="Percent", unit_status="verified_percent_conversion"),
    DEEP: dict(station_id="58e68cabdf5ce600012602bd", stream_id=DEEP, depth_cm=None,
               orientation="Vertical", native_unit="VolumetricWaterContent", unit_status="verified_percent_conversion"),
}
CATALOG_SHA256 = "44e5b22ab113c4f57177fb2ab1c894b4e327ff98e9e93be26050336fd8196e2b"
CEILINGS = dict(logical_requests=14, attempts=14, response_bytes=16*1024**2,
                source_rows=20000, intervals=2, elapsed_ms=300000, sessions=1)
VERSION = "dendra-d3-adapter-1"
ROSTER_SHA256 = "ffd7df8a343306c6d0b5a4d355b32c23c8045d5db321113c5a9c6c31ae1c8dd4"


def validate_budget(budget):
    require(isinstance(budget, dict) and set(budget) == set(CEILINGS), "Explicit D3 budget required")
    require(all(type(budget[k]) is int and 0 < budget[k] <= v for k, v in CEILINGS.items()),
            "D3 budget exceeds approved envelope")
    require(budget["logical_requests"] >= 7 and budget["attempts"] >= 7 and
            budget["intervals"] == 2, "Seven-call baseline budget required")


@dataclass(frozen=True)
class RequestSpec:
    kind: str
    selected_stream: str | None = None
    cursor: str | None = None

    def url(self):
        require(self.kind in {"unit-vocabulary", "station", "datastream-list", "observations"},
                "Unapproved endpoint kind")
        if self.kind == "unit-vocabulary":
            require(self.selected_stream is None and self.cursor is None, "Vocabulary arguments")
            return BASE + "vocabularies/dt-unit"
        require(self.selected_stream in SELECTED, "Unapproved selected identifier")
        station = IDENTITIES[self.selected_stream]["station_id"]
        if self.kind == "station":
            require(self.cursor is None, "Station cursor forbidden")
            return BASE + "stations/" + station
        if self.kind == "datastream-list":
            require(self.cursor is None, "Metadata pagination forbidden")
            return BASE + "datastreams?" + urlencode({"station_id": station, "$limit": 500, "$sort[_id]": 1})
        require(self.cursor is not None and parse_utc(START) <= parse_utc(self.cursor) < parse_utc(END),
                "Observation cursor outside exact D3 interval")
        return BASE + "datapoints?" + urlencode({"datastream_id": self.selected_stream,
            "time[$gte]": format_utc(self.cursor), "time[$lt]": END, "$sort[time]": 1, "$limit": 2016})

    def descriptor(self):
        return dict(kind=self.kind, selected_stream=self.selected_stream, cursor=self.cursor, url=self.url())


def validate_request(request, spec):
    """Compare decoded, unique query values; reject alternate paths/headers/origins."""
    url, expected = urlsplit(request.full_url), urlsplit(spec.url())
    require(request.get_method() == "GET" and request.data is None, "GET without body required")
    require(url.scheme == "https" and url.netloc == "api.dendra.science" and
            not url.fragment and url.path == expected.path, "Endpoint allowlist")
    pairs = parse_qsl(url.query, keep_blank_values=True, strict_parsing=True)
    require(len(dict(pairs)) == len(pairs) and dict(pairs) == dict(parse_qsl(expected.query)),
            "Unapproved query parameters or values")
    headers = {k.lower(): v for k, v in request.header_items()}
    require(set(headers) <= {"accept", "user-agent"} and
            headers.get("accept") == "application/json" and
            headers.get("user-agent") in {"BRIM-Dendra-D3/1.0", "BRIM-Dendra-soil-moisture-prototype/0.1"},
            "Credentials/cookies/arbitrary headers forbidden")
    return spec


def seven_calls():
    return [RequestSpec("unit-vocabulary")] + [RequestSpec("station", sid) for sid in SELECTED] + [
        RequestSpec("datastream-list", sid) for sid in SELECTED] + [
        RequestSpec("observations", sid, START) for sid in SELECTED]


def make_campaign(inventory, authority, *, selected_ids, interval, provider_budget,
                  campaign_id, as_of, request_generation):
    require(type(inventory) is Inventory, "Exact bound inventory required")
    require(tuple(selected_ids) == SELECTED, "Explicit exact ordered two-stream selection required")
    require(tuple(map(format_utc, interval)) == (START, END), "Exact leap-day interval required")
    require(authority["catalog_sha256"] == CATALOG_SHA256, "Accepted scientific authority required")
    require(all(inventory.identity(sid) == IDENTITIES[sid] for sid in SELECTED), "Frozen identity mismatch")
    validate_budget(provider_budget)
    binding = campaign(inventory, campaign_id=campaign_id, selected_ids=selected_ids,
        as_of=as_of, horizons={sid: dict(start=START, end=END) for sid in SELECTED},
        metadata_bindings=authority["metadata_bindings"], dictionary_sha256=authority["dictionary_sha256"],
        budgets=provider_budget, request_generation=request_generation)
    binding.update(mode="d3_explicit_adapter", version=VERSION, d3=dict(
        authority=authority, calls=[s.descriptor() for s in seven_calls()],
        per_request_seconds=25, response_bytes=8*1024**2, max_pages=20,
        attempts_per_task_page=2, retry_delay_seconds=15, concurrency=1, workers=1))
    validate_binding(binding)
    # Plan pages preserve iteration order. Canonicalize before first creation so
    # reopening from the journal's canonical JSON has the identical page hashes.
    return decode(encode(binding)), decode(encode(plan(binding, [(sid, START, END) for sid in SELECTED])))


def validate_binding(binding, tasks=None):
    require(binding.get("mode") == "d3_explicit_adapter" and binding.get("version") == VERSION,
            "Only offline or exact D3 adapter campaigns supported")
    require(binding.get("inventory_sha256") == INVENTORY_SHA256 and
            binding.get("selected_ids") == sorted(SELECTED), "D3 inventory/selection binding")
    require(digest(binding.get("roster")) == ROSTER_SHA256, "Full frozen roster binding")
    require(all(binding["roster"].get(sid) == IDENTITIES[sid] for sid in SELECTED), "D3 identity binding")
    require(binding["horizons"] == {sid: dict(start=START, end=END) for sid in SELECTED}, "D3 horizon binding")
    validate_budget(binding["budgets"])
    d3 = binding["d3"]
    authority = d3["authority"]
    from .provider_metadata import validate_authority
    validate_authority(authority)
    require(authority["catalog_sha256"] == CATALOG_SHA256 and
            set(authority["scientific"]) == set(SELECTED) and
            authority["metadata_bindings"] == {sid: digest(authority["scientific"][sid]) for sid in SELECTED} and
            authority["dictionary_sha256"] == digest(authority["dictionary_terms"]) and
            binding["metadata_bindings"] == authority["metadata_bindings"] and
            binding["dictionary_sha256"] == authority["dictionary_sha256"], "Scientific authority binding")
    require(d3 == dict(authority=authority, calls=[s.descriptor() for s in seven_calls()],
        per_request_seconds=25, response_bytes=8*1024**2, max_pages=20,
        attempts_per_task_page=2, retry_delay_seconds=15, concurrency=1, workers=1), "D3 envelope binding")
    if tasks is not None:
        require(tasks == plan(binding, [(sid, START, END) for sid in SELECTED]), "Exact two D3 intervals required")
        require(list(tasks) == sorted(tasks), "Canonical D3 plan page order required")


def validate_receipt_details(details, *, witness=False, latest=False):
    require(isinstance(details, dict) and set(details) == {"kind", "outcome", "requested_at", "retrieved_at",
        "duration_ms", "retryable", "retry_after_seconds", "effective_limit", "page_complete",
        "privacy", "identity", "error_code"}, "Receipt detail allowlist")
    require(not (witness and latest), "Distinct witness receipt kinds")
    kinds = {"latest-witness"} if latest else {"authority-witness"} if witness else {"unit-vocabulary", "station", "datastream-list", "observations"}
    require(details["kind"] in kinds and
            details["outcome"] in {"received", "retry", "hold", "failure"}, "Receipt classification")
    require(parse_utc(details["requested_at"]) <= parse_utc(details["retrieved_at"]), "Receipt time order")
    require(type(details["duration_ms"]) is int and details["duration_ms"] >= 0 and
            type(details["retryable"]) is bool, "Receipt timing/retry type")
    delay = details["retry_after_seconds"]
    require(delay is None or (type(delay) in (int, float) and math.isfinite(delay) and 0 <= delay <= 15),
            "Receipt retry bound")
    limit = details["effective_limit"]
    require(limit is None or (type(limit) is int and 0 < limit <= 2016), "Receipt limit type")
    require(details["page_complete"] is None or type(details["page_complete"]) is bool, "Receipt complete type")
    require(details["privacy"] in {"public", "hold", "not_evaluated"} and
            details["identity"] in {"match", "hold", "not_evaluated"}, "Receipt metadata classification")
    require(details["error_code"] in {None, "transport", "http", "redirect", "deadline", "body_limit",
            "parse_or_privacy", "budget", "retry_after"}, "Safe receipt error code")
