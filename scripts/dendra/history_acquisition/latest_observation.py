"""Private one-request latest evidence, separate from history and publication.

Preparation requires already accepted reviews and explicit caller authorization.
No import or preparation performs HTTP. The shared Adapter/Journal performs the
only dispatch; this owner supplies selection, binding and private projection.
"""
from dataclasses import dataclass
from pathlib import Path
import os
import math
import threading
from urllib.parse import urlencode

from ..transport import parse_utc, format_utc
from . import observation_quality as quality
from .browser_projection import LATEST
from .d3_plan import BASE, IDENTITIES, validate_request
from .eligibility import validate_decision
from .model import Inventory, INVENTORY_SHA256, NAME, PAGE_BYTES, source_binding
from .provider_adapter import Adapter, observation_shape
from .recovery import checkpoint, open_evidence
from .safety import Root, decode, digest, encode, require, sha

MODE = "latest_evidence_adapter"
VERSION = "dendra-private-latest-evidence-1"
REQUEST = "dendra-latest-request-1"
KIND = "latest-witness"
MAX_PROOF_RECEIPT_AGE_SECONDS = 300
POLICY = dict(version="dendra-private-latest-policy-1", concurrency=1, retries=0,
              redirects=0, minimum_spacing_seconds=1, request_deadline_seconds=25,
              response_bytes=8*1024**2, requests=1, limit=2, pagination=False,
              proof_receipt_max_age_seconds=MAX_PROOF_RECEIPT_AGE_SECONDS)


@dataclass(frozen=True)
class RequestSpec:
    station_id: str
    selected_stream: str
    kind = KIND

    def url(self):
        require(self.selected_stream in IDENTITIES and
                self.station_id == IDENTITIES[self.selected_stream]["station_id"],
                "Exact latest station/stream required")
        return BASE + "datapoints?" + urlencode({"datastream_id": self.selected_stream,
                                                 "$sort[time]": -1, "$limit": 2})

    def descriptor(self):
        return dict(schema_version=REQUEST, kind=KIND, method="GET", url=self.url(),
                    identity=IDENTITIES[self.selected_stream])


def authority(inventory, sid, bundle, first_ref, *, fingerprint, now):
    """Verify accepted provenance; never issue or accept a review."""
    from .presentation import review_journal_first
    from .authority_witness import evidence as first_evidence
    require(set(bundle) == {"packet", "review", "decision"} and
            set(first_ref) == {"root", "campaign_id", "review"}, "Exact latest authority inputs")
    decision = validate_decision(inventory, encode(bundle["packet"]), bundle["review"],
        bundle["decision"], executor_fingerprint=fingerprint, now=now)
    require(sid in IDENTITIES and decision["identity"] == inventory.identity(sid) == IDENTITIES[sid] and
            decision["packet_source_fingerprint"] == fingerprint and
            decision["normalized_conversion_eligible"] is True, "Latest identity/source/scale HOLD")
    with open_evidence(first_ref["root"], first_ref["campaign_id"], inventory) as journal:
        first = first_evidence(journal, sid)
        reviewed = review_journal_first(journal, sid, review=first_ref["review"], as_of=now)
        require(first["collector_fingerprint"] == fingerprint and
                journal.binding["metadata_packets"][sid] == bundle["packet"] and
                reviewed["state"] == "REVIEWED_SOURCE_START", "Latest first-witness authority mismatch")
    return dict(source_start=reviewed, first_evidence=first,
                native_decision_sha256=decision["decision_sha256"],
                configuration_sha256=decision["configuration_sha256"],
                scale_state_sha256=decision["scale_state_sha256"])


def prepare(inventory, *, campaign_id, sid, bundle, first_ref, authorization, now):
    """One fresh, root-bound approval; old approvals cannot reset their budget."""
    return _prepare(inventory, campaign_id=campaign_id, sid=sid, bundle=bundle,
        first_ref=first_ref, authorization=authorization, now=now,
        sources=source_binding(), checkpoint_id=checkpoint())


def _prepare(inventory, *, campaign_id, sid, bundle, first_ref, authorization, now,
             sources, checkpoint_id):
    require(type(inventory) is Inventory and isinstance(campaign_id, str) and
            NAME.fullmatch(campaign_id), "Latest campaign/inventory required")
    require(set(authorization) == {"checkpoint", "root", "approval_reference", "window_start", "window_end",
                                  "previous_request_started_at"}, "Exact latest authorization required")
    require(authorization["checkpoint"] == checkpoint_id and
            isinstance(authorization["approval_reference"], str) and
            0 < len(authorization["approval_reference"]) <= 160, "Latest checkpoint/approval binding")
    root = Path(authorization["root"])
    require(root.is_absolute() and str(root.resolve()) == str(root) and
            root != Path(first_ref["root"]).resolve() and
            Path(first_ref["root"]).resolve() not in root.parents, "Separate latest evidence root required")
    start, end, at = map(parse_utc, (authorization["window_start"], authorization["window_end"], now))
    require(start <= at < end and 0 < (end-start).total_seconds() <= 300, "Latest proof window bound")
    auth = authority(inventory, sid, bundle, first_ref, fingerprint=digest(sources), now=now)
    previous = parse_utc(authorization["previous_request_started_at"])
    require(parse_utc(auth["first_evidence"]["requested_at"]) <= previous <= at,
            "Latest cross-request spacing anchor required")
    request = RequestSpec(inventory.identity(sid)["station_id"], sid).descriptor()
    value = dict(version=VERSION, mode=MODE, campaign_id=campaign_id,
        checkpoint=checkpoint_id, collector_sources=sources, inventory_sha256=INVENTORY_SHA256,
        roster={sid:inventory.identity(sid)}, selected_ids=[sid], prepared_at=format_utc(now),
        bundle=bundle, first_ref=first_ref, authority=auth, authorization=authorization,
        request=request, request_policy=POLICY, quality_policy=quality.binding(),
        budgets=dict(logical_requests=1, attempts=1, response_bytes=8*1024**2,
                     source_rows=2, intervals=0, elapsed_ms=300000, sessions=1))
    value["request_id"] = digest(dict(request=request, checkpoint=checkpoint_id,
        collector_fingerprint=digest(sources), authority=auth, policy=POLICY,
        quality_policy=value["quality_policy"]))
    require(len(encode(value))+4096 <= PAGE_BYTES, "Latest header bound")
    return decode(encode(value)), {}


def validate_binding(binding, tasks, *, inventory, historical=False):
    require(binding.get("mode") == MODE and tasks == {}, "Latest is not historical query coverage")
    sources = binding["collector_sources"] if historical else source_binding()
    head = binding["checkpoint"] if historical else checkpoint()
    expected = _prepare(inventory, campaign_id=binding["campaign_id"], sid=binding["selected_ids"][0],
        bundle=binding["bundle"], first_ref=binding["first_ref"], authorization=binding["authorization"],
        now=binding["prepared_at"], sources=sources, checkpoint_id=head)
    require((binding, tasks) == expected, "Latest binding changed")


def validate_storage(binding, fs):
    with Root(binding["authorization"]["root"]) as bound:
        a, b = os.fstat(fs.fd), os.fstat(bound.fd)
        require((a.st_dev, a.st_ino) == (b.st_dev, b.st_ino), "Latest approval cannot move to fresh storage")
    # One approved root has one ledger, even if a restarted caller changes the
    # campaign name. Renaming must not manufacture another single-attempt budget.
    try:
        registry = fs.list("registry", 2)
    except FileNotFoundError:
        registry = []
    require(not registry or registry == [binding["campaign_id"] + ".json"],
            "Latest root already bound to another campaign")


def response_shape(body, binding, *, retrieved_at):
    sid = binding["selected_ids"][0]
    value = observation_shape(body, sid, quality_policy=binding["quality_policy"])
    rows = value["data"]
    require(value["limit"] == 2 and len(rows) <= 2 and value.get("skip", 0) == 0 and
            ("total" not in value or value["total"] >= len(rows)), "Latest top-two shape; no pagination")
    decision = binding["bundle"]["decision"]
    times = []
    for row in rows:
        at = parse_utc(row["t"])
        require(row.get("datastream_id") == sid and 1900 <= at.year and at <= parse_utc(retrieved_at),
                "Latest stream/source timestamp invalid")
        require(parse_utc(binding["authority"]["source_start"]["start"]) <= at and
                parse_utc(decision["scope"]["start"]) <= at < parse_utc(decision["scope"]["end"]),
                "Latest outside reviewed source/native scope")
        windows = [w for w in decision["configuration_windows"] if parse_utc(w["start"]) <= at and
                   (w["end"] is None or at < parse_utc(w["end"]))]
        require(len(windows) == 1, "Latest configuration not uniquely reviewed")
        require(row.get("v") is None or (type(row["v"]) in (int, float) and
                abs(row["v"]) <= 1e308 and math.isfinite(row["v"])),
                "Malformed latest value")
        times.append(at)
    require(len(times) < 2 or times[0] > times[1], "Latest tied/non-descending source timestamps")
    return value


def evidence(journal, *, evaluated_at, live_proof=False):
    """Recompute evidence from immutable originals; historical reads do not reauthorize."""
    validate_binding(journal.binding, journal.tasks, inventory=journal.inventory, historical=journal.inspect_only)
    validate_storage(journal.binding, journal.fs)
    journal.verify_records()
    b = journal.binding
    attempts = list(journal.snapshot()["attempts"].values())
    require(len(attempts) == 1, "Missing/ambiguous latest attempt")
    a = attempts[0]
    logical = digest(dict(task="latest-witness", run=0, cursor=b["request_id"]))
    require(a["state"] == "received" and a["status"] == 200 and a["ordinal"] == 1 and
            a["task_key"] == "latest-witness" and a["interval_key"] is None and a["run"] == 0 and
            a["cursor"] == b["request_id"] and a["logical_key"] == logical and
            a["attempt_key"] == digest(dict(task="latest-witness", page=logical, attempt=1)) and
            a["representation"] == "original" and a["body_retained"] and len(a["objects"]) == 1,
            "Latest complete original receipt required")
    records = {}
    for kind in ("reserved", "started", "received"):
        found = [r for r in journal.events if r["kind"] == kind and r["data"].get("attempt_key") == a["attempt_key"]]
        require(len(found) == 1, "Latest receipt chain closure")
        records[kind] = found[0]
    d = a["details"]
    require(d["kind"] == KIND and d["outcome"] == "received" and d["effective_limit"] == 2 and
            d["page_complete"] is True and d["privacy"] == "public" and d["identity"] == "match" and
            d["retryable"] is False and d["retry_after_seconds"] is None and d["error_code"] is None,
            "Latest admission receipt HOLD")
    require(records["reserved"]["sequence"] < records["started"]["sequence"] < records["received"]["sequence"] and
            parse_utc(records["reserved"]["at"]) <= parse_utc(records["started"]["at"]) <=
            parse_utc(d["requested_at"]) <= parse_utc(d["retrieved_at"]) <=
            parse_utc(records["received"]["at"]) <= parse_utc(evaluated_at), "Latest receipt chronology")
    start, end = (parse_utc(b["authorization"][k]) for k in ("window_start", "window_end"))
    require(start <= parse_utc(d["requested_at"]) <= parse_utc(d["retrieved_at"]) < end and
            (parse_utc(d["requested_at"])-parse_utc(b["authorization"]["previous_request_started_at"])).total_seconds() >= 1,
            "Latest dispatch window/spacing binding")
    authority(journal.inventory, b["selected_ids"][0], b["bundle"], b["first_ref"],
              fingerprint=digest(b["collector_sources"]), now=d["requested_at"])
    body = journal.read_object(a["objects"][0])
    require(sha(body) == a["response_sha256"] and len(body) == a["response_bytes"], "Latest object hash/size mismatch")
    payload = response_shape(body, b, retrieved_at=d["retrieved_at"])
    require(a["source_rows"] == len(payload["data"]), "Latest source-row accounting")
    age = (parse_utc(evaluated_at)-parse_utc(d["retrieved_at"])).total_seconds()
    if live_proof:
        require(b["checkpoint"] == checkpoint() and b["collector_sources"] == source_binding() and
                age <= MAX_PROOF_RECEIPT_AGE_SECONDS, "Fresh current-source proof receipt required")
    row = payload["data"][0] if payload["data"] else None
    disposition = quality.classify(row) if row is not None else None
    scale = b["bundle"]["decision"]["scale"]
    factor = scale["conversion_factor"]
    status, reason, native, normalized = "UNAVAILABLE", "empty_latest_response", None, None
    if row is not None:
        reason = "provider_quality_quarantined" if disposition["quarantined"] else "missing_latest_value"
        if not disposition["quarantined"] and row.get("v") is not None:
            native, normalized = row["v"], row["v"] * factor
            require(math.isfinite(normalized) and 0 <= normalized <= 100, "Latest resolved display bounds")
            status, reason = "AVAILABLE", "verified_private_latest_witness"
    stamp = row["t"] if row is not None else None
    source_age = None if stamp is None else (parse_utc(evaluated_at)-parse_utc(stamp)).total_seconds()
    result = dict(schema_version=VERSION, checkpoint=b["checkpoint"], collector_fingerprint=digest(b["collector_sources"]),
        identity=b["roster"][b["selected_ids"][0]], configuration_sha256=b["authority"]["configuration_sha256"],
        authority=b["authority"], request=b["request"], request_id=b["request_id"],
        journal_binding_sha256=journal.binding_sha, journal_header_sha256=journal.header_sha,
        attempt_key=a["attempt_key"], record_identities={k:r["record_sha256"] for k,r in records.items()},
        response_object=a["objects"][0], source_rows=a["source_rows"], requested_at=d["requested_at"],
        retrieved_at=d["retrieved_at"], evaluated_at=format_utc(evaluated_at), source_timestamp=stamp,
        selected_row_sha256=None if row is None else digest(row), native_value=native,
        normalized_percent=normalized, scale=scale, quality=disposition, status=status, reason=reason,
        observation_age_seconds=source_age, retrieval_age_seconds=age, history_coverage=False,
        publication_allowed=False, current_state_claim=False,
        state=dict(latest_request_attempt_time=d["requested_at"], last_successful_source_check=d["retrieved_at"],
            latest_eligible_source_timestamp=stamp if status == "AVAILABLE" else None,
            latest_retrieval_timestamp=d["retrieved_at"], latest_quality_disposition=disposition,
            latest_receipt_sha256=records["received"]["record_sha256"], latest_object_sha256=a["response_sha256"],
            source_age_seconds=source_age, retrieval_age_seconds=age))
    return dict(result, evidence_sha256=digest(result))


def project(journal, *, evaluated_at, live_proof=False):
    """Existing four-field marker/AVAILABLE record shape, private output only.

    Real identities cannot pass the intentionally unchanged synthetic validator.
    This route verifies original journal evidence instead. It never edits the
    historical exporter, descriptor graph, daily rows, or a publication pointer.
    """
    e = evidence(journal, evaluated_at=evaluated_at, live_proof=live_proof)
    record = None
    if e["status"] == "AVAILABLE":
        record = dict(representation="latest_instantaneous", identity=e["identity"],
            native_value=e["native_value"], normalized_percent=e["normalized_percent"],
            source_timestamp=e["source_timestamp"], retrieved_at=e["retrieved_at"], evaluated_at=e["evaluated_at"],
            observation_age_seconds=e["observation_age_seconds"], retrieval_age_seconds=e["retrieval_age_seconds"],
            scale_state="RESOLVED_PERCENT", generation=e["evidence_sha256"],
            source_lineage_sha256=e["response_object"]["sha256"], presentation_eligible=True, latest_witness=True)
    return dict(schema_version=LATEST, status=e["status"], reason=e["reason"], record=record)


class LatestAdapter(Adapter):
    attempts_per_page = 1

    def __init__(self, journal):
        validate_binding(journal.binding, journal.tasks, inventory=journal.inventory)
        validate_storage(journal.binding, journal.fs)
        require(not journal.inspect_only and not journal.damage and journal.lock is not None,
                "Writable latest journal required")
        self._initialize(journal, None)
        self.used = False

    def plan(self):
        return [self.journal.binding["request"]]

    def _permission(self):
        b = self.journal.binding
        require(b["collector_sources"] == source_binding() and b["checkpoint"] == checkpoint(),
                "Latest current source changed")
        authority(self.journal.inventory, b["selected_ids"][0], b["bundle"], b["first_ref"],
                  fingerprint=digest(b["collector_sources"]), now=self.journal.now())
        self.remaining()

    def _validate_dispatch(self, request, spec, interval_key):
        require(type(spec) is RequestSpec and spec is self.current_spec and interval_key is None and
                spec.descriptor() == self.journal.binding["request"], "Exact latest dispatch required")
        validate_request(request, spec)
        self._permission()
        previous = parse_utc(self.journal.binding["authorization"]["previous_request_started_at"])
        delay = max(0, 1-(parse_utc(self.journal.now())-previous).total_seconds())
        if delay:
            self.pause(delay)
        require((parse_utc(self.journal.now())-previous).total_seconds() >= 1, "Latest request-start spacing")
        self._permission()

    def _execute(self, request, *, timeout, interval_key):
        self._permission()
        return self.executor(request, timeout=timeout)

    def run(self, *, executor, wait, authorization):
        b = self.journal.binding
        require(callable(executor) and callable(wait) and not self.active and not self.used and
                threading.current_thread() is threading.main_thread(), "Explicit serial latest runner required")
        require(authorization == b["authorization"], "Latest execution approval differs")
        require(parse_utc(authorization["window_start"]) <= parse_utc(self.journal.now()) <
                parse_utc(authorization["window_end"]), "Latest execution window expired")
        require(not self.journal.snapshot()["attempts"], "Spent latest attempt cannot restart")
        self.window_end = authorization["window_end"]
        self.journal.session()
        self.used = True
        self.executor, self.wait, self.active = executor, wait, True
        try:
            sid = b["selected_ids"][0]
            self.current_spec = RequestSpec(b["roster"][sid]["station_id"], sid)
            self.exchange(self._request(self.current_spec), self.current_spec)
            result = evidence(self.journal, evaluated_at=self.journal.now(), live_proof=True)
            self.journal.fs.write_new(self.journal.prefix + "/latest-evidence.json", encode(result), PAGE_BYTES)
            return result
        finally:
            self.current_spec = self.executor = self.wait = None
            self.active = False
