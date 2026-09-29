"""Exact first-observation authority witnesses; no history plan or HTTP client.

Preparation is offline. Fresh admitted packets and later explicit execution
approval are trusted caller inputs, not permissions manufactured by their hashes.
The existing Journal and Adapter persist every attempted witness.
"""
from dataclasses import dataclass
import math
from urllib.parse import urlencode

from ..transport import parse_utc, format_utc
from .d3_plan import BASE, IDENTITIES
from .eligibility import _packet
from .model import Inventory, INVENTORY_SHA256, NAME, PAGE_BYTES, ID, source_binding
from .provider_metadata import _fresh
from .safety import decode, digest, encode, require, sha

MODE = "authority_witness_adapter"
VERSION = "dendra-first-witness-journal-1"
REQUEST = "dendra-first-witness-request-1"
EVIDENCE = "dendra-journal-first-evidence-1"
REVIEW = "dendra-journal-first-review-1"
KIND = "authority-witness"
POLICY = dict(version="dendra-first-witness-policy-1", concurrency=1, retries=0,
              redirects=0, minimum_spacing_seconds=1, request_deadline_seconds=25,
              response_bytes=8*1024**2, requests_per_stream=1, limit=1)


@dataclass(frozen=True)
class RequestSpec:
    station_id: str
    selected_stream: str
    frozen_identity: dict | None = None
    kind = KIND

    def identity(self):
        # Legacy callers/receipts retain their exact descriptor. Generalized
        # callers supply an identity verified against Inventory by preparation.
        identity = self.frozen_identity or IDENTITIES.get(self.selected_stream)
        require(isinstance(identity, dict) and identity.get("stream_id") == self.selected_stream and
                identity.get("station_id") == self.station_id and ID.fullmatch(self.selected_stream) and
                ID.fullmatch(self.station_id) and identity.get("native_unit") in ("Percent", "VolumetricWaterContent"),
                "Exact witness station/stream required")
        return identity

    def url(self):
        self.identity()
        return BASE + "datapoints?" + urlencode({"datastream_id": self.selected_stream,
                                                 "$sort[time]": 1, "$limit": 1})

    def descriptor(self):
        return dict(schema_version=REQUEST, kind=KIND, method="GET", url=self.url(),
                    identity=self.identity())


def check_metadata(inventory, sid, packet, *, now):
    """Reuse unchanged scientific/configuration/access checks and 24h freshness.

This admits only a separately authorized authority witness. It creates no native
history eligibility decision and does not override any packet's false flags.
"""
    return _check_metadata(inventory, sid, packet, now=now, fingerprint=digest(source_binding()))


def _check_metadata(inventory, sid, packet, *, now, fingerprint):
    body = encode(packet)
    p, _ = _packet(inventory, body, sha(body), fingerprint)
    require(p["stream_id"] == sid, "Witness packet stream mismatch")
    for stamp in (p["checked_at"], p["access_evidence"]["station_checked_at"],
                  p["access_evidence"]["stream_checked_at"]):
        _fresh(stamp, now)
    return p


def prepare(inventory, *, campaign_id, packets, as_of):
    return _prepare(inventory, campaign_id=campaign_id, packets=packets,
                    as_of=as_of, sources=source_binding())


def _prepare(inventory, *, campaign_id, packets, as_of, sources):
    require(type(inventory) is Inventory and isinstance(campaign_id, str) and
            NAME.fullmatch(campaign_id), "Explicit witness campaign/inventory required")
    require(isinstance(packets, dict) and 1 <= len(packets) <= 2,
            "One/two frozen witness targets per bounded journal required")
    requests = {}
    fingerprint = digest(sources)
    for sid in sorted(packets):
        identity = inventory.identity(sid)
        require(identity["native_unit"] in ("Percent", "VolumetricWaterContent") and
                identity["unit_status"] == "verified_percent_conversion", "Resolved frozen witness required")
        _check_metadata(inventory, sid, packets[sid], now=as_of, fingerprint=fingerprint)
        request = dict(request=RequestSpec(identity["station_id"], sid, identity).descriptor(),
                       inventory_sha256=INVENTORY_SHA256, collector_fingerprint=fingerprint,
                       policy=POLICY, metadata_packet_sha256=digest(packets[sid]))
        requests["witness-" + sid] = dict(request, request_id=digest(request))
    n = len(packets)
    binding = dict(version=VERSION, mode=MODE, campaign_id=campaign_id,
        inventory_sha256=INVENTORY_SHA256, collector_sources=sources,
        roster=inventory.roster(), selected_ids=sorted(packets), prepared_at=format_utc(as_of),
        metadata_packets=packets, witness_requests=requests, request_policy=POLICY,
        budgets=dict(logical_requests=n, attempts=n, response_bytes=n*8*1024**2,
                     source_rows=n, intervals=0, elapsed_ms=60000, sessions=1))
    require(len(encode(binding))+4096 <= PAGE_BYTES, "Witness journal header bound")
    return decode(encode(binding)), {}


def validate_binding(binding, tasks, *, inventory):
    require(binding.get("mode") == MODE and tasks == {}, "Witnesses are not history interval tasks")
    expected = prepare(inventory, campaign_id=binding["campaign_id"],
                       packets=binding["metadata_packets"], as_of=binding["prepared_at"])
    require((binding, tasks) == expected, "Witness source/request/budget binding changed")


def total_complete(row_count, present, total):
    """A single selected first row needs no total; empty evidence still does."""
    return ((type(total) is int and total >= row_count) if present and type(row_count) is int
            else not present and row_count == 1)


def response_shape(body, sid, *, retrieved_at):
    # Shared observation privacy allowlist rejects coordinates, nested values,
    # credentials and foreign stream fields before any raw object is retained.
    from .provider_adapter import observation_shape
    value = observation_shape(body, sid)
    require(value["limit"] == 1 and len(value["data"]) <= 1 and
            type(value.get("skip", 0)) is int and value.get("skip", 0) == 0 and
            total_complete(len(value["data"]), "total" in value, value.get("total")),
            "First witness completeness/selection")
    if not value["data"]:
        require(value["total"] == 0, "Ambiguous empty first witness")
    else:
        row = value["data"][0]
        stamp = parse_utc(row["t"])
        require(1900 <= stamp.year and stamp <= parse_utc(retrieved_at) and
                type(row.get("v")) in (int, float) and math.isfinite(row["v"]),
                "Invalid first witness observation")
    return value


def evidence(journal, sid):
    """Read and verify immutable original receipt/object; never dispatch or seal.

Reopening a Journal verifies its chain. Recheck the bound on-disk records here
too, so an in-memory edited receipt cannot supply review authority.
"""
    from .journal import Journal, EVENT_BYTES
    require(type(journal) is Journal and not journal.damage and journal.lock is not None,
            "Intact locked witness journal required")
    if journal.inspect_only:
        b = journal.binding
        expected = _prepare(journal.inventory, campaign_id=b["campaign_id"],
                            packets=b["metadata_packets"], as_of=b["prepared_at"],
                            sources=b["collector_sources"])
        require((b, journal.tasks) == expected, "Historical witness binding changed")
    else:
        validate_binding(journal.binding, journal.tasks, inventory=journal.inventory)
    require(digest(journal.binding) == journal.binding_sha and digest(journal.tasks) == journal.tasks_sha,
            "Witness journal memory binding changed")
    header = journal.fs.read(journal.prefix + "/manifest.json", PAGE_BYTES)
    require(sha(header) == journal.header_sha, "Witness journal header changed")
    previous = journal.header_sha
    for record in journal.events:
        body = encode(record)
        require(record["previous_sha256"] == previous and record["header_sha256"] == journal.header_sha and
                record["record_sha256"] == digest({k:v for k,v in record.items() if k != "record_sha256"}) and
                journal.fs.read(journal._event_path(record["sequence"]), EVENT_BYTES) == body and
                journal.fs.read("anchors/" + journal.binding["campaign_id"] +
                    f"/{record['sequence']:08d}.json", EVENT_BYTES) == body, "Witness receipt/anchor changed")
        previous = record["record_sha256"]
    key = "witness-" + sid
    require(key in journal.binding["witness_requests"], "Witness outside bound selection")
    request = journal.binding["witness_requests"][key]
    attempts = [a for a in journal.snapshot()["attempts"].values() if a["task_key"] == key]
    require(len(attempts) == 1, "Missing/ambiguous witness attempt")
    a = attempts[0]
    require(a["state"] == "received" and a["status"] == 200 and a["ordinal"] == 1 and
            a["interval_key"] is None and a["run"] == 0 and a["cursor"] == request["request_id"] and
            a["representation"] == "original" and a["body_retained"] and len(a["objects"]) == 1,
            "Witness is not a complete original response")
    logical = digest(dict(task=key, run=0, cursor=request["request_id"]))
    require(a["logical_key"] == logical and
            a["attempt_key"] == digest(dict(task=key, page=logical, attempt=1)), "Witness reservation identity")
    records = {}
    for kind in ("reserved", "started", "received"):
        found = [r for r in journal.events if r["kind"] == kind and r["data"].get("attempt_key") == a["attempt_key"]]
        require(len(found) == 1, "Witness receipt chain closure")
        records[kind] = found[0]
    require(records["reserved"]["sequence"] < records["started"]["sequence"] < records["received"]["sequence"],
            "Witness reserve/start/receipt order")
    details = a["details"]
    require(details["kind"] == KIND and details["outcome"] == "received" and
            details["effective_limit"] == 1 and details["page_complete"] is True and
            details["privacy"] == "public" and details["identity"] == "match" and
            details["retryable"] is False and details["error_code"] is None,
            "Witness admission receipt HOLD")
    require(parse_utc(records["reserved"]["at"]) <= parse_utc(records["started"]["at"]) <=
            parse_utc(details["requested_at"]) <= parse_utc(details["retrieved_at"]) <=
            parse_utc(records["received"]["at"]) <= parse_utc(journal.now()), "Witness receipt chronology")
    body = journal.read_object(a["objects"][0])
    require(sha(body) == a["response_sha256"] and len(body) == a["response_bytes"], "Witness original object binding")
    payload = response_shape(body, sid, retrieved_at=details["retrieved_at"])
    require(a["source_rows"] == len(payload["data"]), "Witness result accounting")
    # Revalidate permission at dispatch time, not today's metadata freshness;
    # reading historical evidence never authorizes a fresh request.
    _check_metadata(journal.inventory, sid, journal.binding["metadata_packets"][sid],
                    now=details["requested_at"], fingerprint=digest(journal.binding["collector_sources"]))
    row = payload["data"][0] if payload["data"] else None
    value = dict(schema_version=EVIDENCE, identity=journal.binding["roster"][sid],
        collector_fingerprint=digest(journal.binding["collector_sources"]), request=request,
        journal_binding_sha256=journal.binding_sha, journal_header_sha256=journal.header_sha,
        attempt_key=a["attempt_key"], record_identities={k:r["record_sha256"] for k,r in records.items()},
        response_object=a["objects"][0], requested_at=details["requested_at"], retrieved_at=details["retrieved_at"],
        result=dict(state="COMPLETE_EMPTY" if row is None else "FIRST_OBSERVATION",
                    timestamp=None if row is None else format_utc(row["t"]), row_sha256=None if row is None else digest(row)),
        review_policy=REVIEW, history_coverage=False, dispatch_ready=False)
    return dict(value, evidence_sha256=digest(value))
