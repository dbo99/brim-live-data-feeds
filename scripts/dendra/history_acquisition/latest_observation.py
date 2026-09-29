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
from .provider_adapter import Adapter, observation_shape, OBSERVATION_FIELDS, OBSERVATION_ENVELOPE
from .recovery import checkpoint, open_evidence
from .safety import Root, decode, digest, encode, require, sha

MODE = "latest_evidence_adapter"
VERSION = "dendra-private-latest-evidence-1"
DIAGNOSTIC = "dendra-latest-rejection-diagnostic-1"
DIAGNOSTIC_PURPOSE = "rejection_diagnostic_only"
DIAGNOSTIC_BYTES = 12288
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


def prepare_diagnostic(inventory, *, campaign_id, sid, bundle, first_ref, authorization, now):
    """Separate immutable purpose; never reinterpret an ordinary spent request."""
    return _prepare(inventory, campaign_id=campaign_id, sid=sid, bundle=bundle,
        first_ref=first_ref, authorization=authorization, now=now,
        sources=source_binding(), checkpoint_id=checkpoint(), diagnostic=True)


def _prepare(inventory, *, campaign_id, sid, bundle, first_ref, authorization, now,
             sources, checkpoint_id, diagnostic=False):
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
    if diagnostic:
        value.update(version=DIAGNOSTIC, purpose=DIAGNOSTIC_PURPOSE)
        value["request_id"] = digest(dict(request_id=value["request_id"],
                                         version=DIAGNOSTIC, purpose=DIAGNOSTIC_PURPOSE))
    require(len(encode(value))+4096 <= PAGE_BYTES, "Latest header bound")
    return decode(encode(value)), {}


def validate_binding(binding, tasks, *, inventory, historical=False):
    require(binding.get("mode") == MODE and tasks == {}, "Latest is not historical query coverage")
    require(binding.get("version") in (VERSION, DIAGNOSTIC), "Unknown latest purpose/version")
    sources = binding["collector_sources"] if historical else source_binding()
    head = binding["checkpoint"] if historical else checkpoint()
    expected = _prepare(inventory, campaign_id=binding["campaign_id"], sid=binding["selected_ids"][0],
        bundle=binding["bundle"], first_ref=binding["first_ref"], authorization=binding["authorization"],
        now=binding["prepared_at"], sources=sources, checkpoint_id=head,
        diagnostic=binding["version"] == DIAGNOSTIC)
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
    require(binding["version"] == VERSION, "Diagnostic is never latest admission")
    require(type(binding["selected_ids"]) is list and len(binding["selected_ids"]) == 1,
            "Exact single-stream latest request required")
    sid = binding["selected_ids"][0]
    require(binding["request"] == RequestSpec(binding["roster"][sid]["station_id"], sid).descriptor(),
            "Exact single-stream latest request required")
    # Shared admission inherits only absent IDs; supplied null/wrong IDs refuse.
    # Dispatch and _receipt separately verify the full authority/Journal binding.
    value = observation_shape(body, sid, quality_policy=binding["quality_policy"])
    rows = value["data"]
    require(value["limit"] == 2 and len(rows) <= 2 and value.get("skip", 0) == 0 and
            ("total" not in value or value["total"] >= len(rows)), "Latest top-two shape; no pagination")
    decision = binding["bundle"]["decision"]
    times = []
    for row in rows:
        at = parse_utc(row["t"])
        require(1900 <= at.year and at <= parse_utc(retrieved_at), "Latest source timestamp invalid")
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
    require(len(times) < 2 or times[0] >= times[1], "Latest non-descending source timestamps")
    return value


def _latest_group(rows):
    """Select only within admitted newest rows; keep originals and QA private.

    A limit-two tie is agreement/conflict among returned occurrences, never
    proof of exhaustive timestamp-group coverage. No older-row fallback.
    """
    group = [r for r in rows if parse_utc(r["t"]) == parse_utc(rows[0]["t"])] if rows else []
    conflict = any(r.get("v") != group[0].get("v") for r in group)
    inherited = [i for i, r in enumerate(rows) if "datastream_id" not in r]
    qa = dict(identity_inherited_row_indices=inherited, identity_inherited_count=len(inherited),
        newest_group_occurrence_count=len(group), newest_group_duplicate_count=max(0, len(group)-1),
        newest_group_row_sha256=[digest(r) for r in group], newest_group_value_conflict=conflict,
        newest_group_exhaustive=False, newest_group_at_response_limit=len(group) == 2)
    if not group:
        return None, None, qa
    if len(group) == 1:
        disposition = quality.classify(group[0])
    else:
        # Reuse the history policy's union of claims and source occurrences.
        derived = {"t": format_utc(parse_utc(group[0]["t"]))}
        quality.attach([derived], group)
        disposition = derived["quality"]
    return None if conflict else group[0], disposition, qa


def diagnostic_projection(body, binding, *, retrieved_at):
    """Fixed predicate facts, never returned identifiers, timestamps or values.

    The existing decoder/UTC and quality validators remain authoritative. Shape
    checks do not coerce source fields, repair a row, or imply latest admission.
    """
    require(binding.get("version") == DIAGNOSTIC and binding.get("purpose") == DIAGNOSTIC_PURPOSE,
            "Explicit latest diagnostic purpose required")
    require(type(body) is bytes and len(body) <= POLICY["response_bytes"], "Diagnostic body bound")
    sid = binding["selected_ids"][0]
    request_valid = binding["request"] == RequestSpec(binding["roster"][sid]["station_id"], sid).descriptor()
    require(request_valid, "Diagnostic request identity changed")
    retrieval = parse_utc(retrieved_at)
    try:
        value = decode(body)
    except (ValueError, TypeError, RecursionError):
        value = None
    obj = value if type(value) is dict else {}
    rows = obj.get("data") if type(obj.get("data")) is list else None
    count = len(rows) if rows is not None else None
    schema = (type(value) is dict and set(obj) <= OBSERVATION_ENVELOPE and rows is not None and
              type(obj.get("limit")) is int and obj["limit"] == 2 and count <= 2 and
              all(k not in obj or (type(obj[k]) is int and obj[k] >= 0) for k in ("total", "skip")) and
              obj.get("skip", 0) == 0 and ("total" not in obj or obj["total"] >= count))
    facts, times = [], []
    # Out-of-bounds arrays refuse as a whole; never truncate/coerce their rows.
    for item in rows if rows is not None and count <= 2 else []:
        row = item if type(item) is dict else {}
        stream_type = type(row.get("datastream_id")) is str and len(row["datastream_id"]) <= 256
        time_type = type(row.get("t")) is str and len(row["t"]) <= 256
        stamp = None
        if time_type:
            try:
                stamp = parse_utc(row["t"])
            except (ValueError, TypeError, OverflowError):
                pass  # Never emit the parser exception containing the source.
        safe_row = type(item) is dict and set(row) <= OBSERVATION_FIELDS
        for name, v in row.items():
            if name == "q":
                try:
                    quality.validate_policy(binding["quality_policy"])
                    quality.classify(row)
                except (ValueError, TypeError, OverflowError, RecursionError):
                    safe_row = False
            elif v is not None and (type(v) not in (str, int, float, bool) or
                                    (type(v) is str and len(v) > 256)):
                safe_row = False
        v = row.get("v")
        if v is not None and not (type(v) in (int, float) and abs(v) <= 1e308 and math.isfinite(v)):
            safe_row = False
        # Missing/unsupported identity/time is explicit, not an accepted schema.
        schema = bool(schema and safe_row and stream_type and time_type and stamp is not None)
        previous = times[-1] if times else None
        comparable = previous is not None and stamp is not None
        facts.append(dict(row_present=type(item) is dict, stream_field_present="datastream_id" in row,
            stream_field_type_supported=stream_type, stream_matches_selected=stream_type and row["datastream_id"] == sid,
            timestamp_field_present="t" in row, timestamp_field_type_supported=time_type,
            timestamp_parseable=stamp is not None,
            timestamp_year_at_least_1900=None if stamp is None else stamp.year >= 1900,
            timestamp_not_after_retrieval=None if stamp is None else stamp <= retrieval,
            relative_order_valid=previous > stamp if comparable else None,
            timestamp_tied_with_previous=previous == stamp if comparable else None))
        times.append(stamp)
    ordering = None if rows is None or count > 2 or any(t is None for t in times) else (
        count < 2 or times[0] > times[1])
    tied = facts[1]["timestamp_tied_with_previous"] if len(facts) == 2 else None
    passed = bool(schema and ordering and all(f["stream_matches_selected"] and
        f["timestamp_year_at_least_1900"] and f["timestamp_not_after_retrieval"] for f in facts))
    result = dict(schema_version=DIAGNOSTIC, request_identity_valid=request_valid,
        request_sha256=digest(binding["request"]), request_id=binding["request_id"],
        source_fingerprint=digest(binding["collector_sources"]), checkpoint=binding["checkpoint"],
        response_sha256=sha(body), response_bytes=len(body), retrieval_time_sha256=digest(retrieved_at),
        row_count=count, rows=facts, schema_supported=bool(schema), descending_order_valid=ordering,
        newest_timestamp_tied=tied, diagnostic_classification="PASSED_STRUCTURE_ONLY" if passed else
        "REJECTED_SCHEMA" if not schema else "REJECTED_PREDICATES",
        latest_available_allowed=False, latest_unavailable_allowed=False, publication_allowed=False)
    require(len(encode(result)) <= DIAGNOSTIC_BYTES, "Latest diagnostic output bound")
    return result


def validate_diagnostic(body, sanitized, binding, *, retrieved_at):
    require(type(sanitized) is bytes and len(sanitized) <= DIAGNOSTIC_BYTES and
            sanitized == encode(diagnostic_projection(body, binding, retrieved_at=retrieved_at)),
            "Latest diagnostic projection differs from original response")


def _receipt(journal, evaluated_at, *, diagnostic=False):
    """Shared immutable request/receipt/object provenance, with distinct purposes."""
    validate_binding(journal.binding, journal.tasks, inventory=journal.inventory, historical=journal.inspect_only)
    require(journal.binding["version"] == (DIAGNOSTIC if diagnostic else VERSION),
            "Diagnostic is never latest admission")
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
            a["representation"] == ("sanitized" if diagnostic else "original") and
            a["body_retained"] and len(a["objects"]) == 1, "Latest purpose/receipt representation mismatch")
    records = {}
    for kind in ("reserved", "started", "received"):
        found = [r for r in journal.events if r["kind"] == kind and r["data"].get("attempt_key") == a["attempt_key"]]
        require(len(found) == 1, "Latest receipt chain closure")
        records[kind] = found[0]
    d = a["details"]
    require(d["kind"] == KIND and d["outcome"] == "received" and
            d["effective_limit"] == (None if diagnostic else 2) and
            d["page_complete"] is (None if diagnostic else True) and
            d["privacy"] == ("not_evaluated" if diagnostic else "public") and
            d["identity"] == ("not_evaluated" if diagnostic else "match") and
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
    return b, a, records, d, body


def diagnostic_evidence(journal, *, evaluated_at):
    """Read the received-event-bound sanitized object; never project a marker."""
    b, a, records, d, body = _receipt(journal, evaluated_at, diagnostic=True)
    require(len(body) <= DIAGNOSTIC_BYTES, "Latest diagnostic output bound")
    value = decode(body)
    require(value["schema_version"] == DIAGNOSTIC and value["request_id"] == b["request_id"] and
            value["request_sha256"] == digest(b["request"]) and value["checkpoint"] == b["checkpoint"] and
            value["source_fingerprint"] == digest(b["collector_sources"]) and
            value["response_sha256"] == a["response_sha256"] and value["response_bytes"] == a["response_bytes"] and
            value["retrieval_time_sha256"] == digest(d["retrieved_at"]) and value["row_count"] == a["source_rows"] and
            all(value[k] is False for k in ("latest_available_allowed", "latest_unavailable_allowed", "publication_allowed")),
            "Latest diagnostic receipt/object binding")
    return dict(diagnostic=value, journal_binding_sha256=journal.binding_sha,
        journal_header_sha256=journal.header_sha, attempt_key=a["attempt_key"],
        records={k:r["record_sha256"] for k,r in records.items()}, diagnostic_object=a["objects"][0],
        raw_body_retained=False, history_coverage=False, latest_available_allowed=False,
        latest_unavailable_allowed=False, publication_allowed=False)


def evidence(journal, *, evaluated_at, live_proof=False):
    """Recompute evidence from immutable originals; historical reads do not reauthorize."""
    b, a, records, d, body = _receipt(journal, evaluated_at)
    require(sha(body) == a["response_sha256"] and len(body) == a["response_bytes"], "Latest object hash/size mismatch")
    payload = response_shape(body, b, retrieved_at=d["retrieved_at"])
    require(a["source_rows"] == len(payload["data"]), "Latest source-row accounting")
    age = (parse_utc(evaluated_at)-parse_utc(d["retrieved_at"])).total_seconds()
    if live_proof:
        require(b["checkpoint"] == checkpoint() and b["collector_sources"] == source_binding() and
                age <= MAX_PROOF_RECEIPT_AGE_SECONDS, "Fresh current-source proof receipt required")
    row, disposition, qa = _latest_group(payload["data"])
    scale = b["bundle"]["decision"]["scale"]
    factor = scale["conversion_factor"]
    status, reason, native, normalized = "UNAVAILABLE", "empty_latest_response", None, None
    if qa["newest_group_value_conflict"]:
        reason = "conflicting_latest_values"
    elif row is not None:
        reason = "provider_quality_quarantined" if disposition["quarantined"] else "missing_latest_value"
        if not disposition["quarantined"] and row.get("v") is not None:
            native, normalized = row["v"], row["v"] * factor
            require(math.isfinite(normalized) and 0 <= normalized <= 100, "Latest resolved display bounds")
            status, reason = "AVAILABLE", "verified_private_latest_witness"
    stamp = payload["data"][0]["t"] if payload["data"] else None
    source_age = None if stamp is None else (parse_utc(evaluated_at)-parse_utc(stamp)).total_seconds()
    result = dict(schema_version=VERSION, checkpoint=b["checkpoint"], collector_fingerprint=digest(b["collector_sources"]),
        identity=b["roster"][b["selected_ids"][0]], configuration_sha256=b["authority"]["configuration_sha256"],
        authority=b["authority"], request=b["request"], request_id=b["request_id"],
        journal_binding_sha256=journal.binding_sha, journal_header_sha256=journal.header_sha,
        attempt_key=a["attempt_key"], record_identities={k:r["record_sha256"] for k,r in records.items()},
        response_object=a["objects"][0], source_rows=a["source_rows"], requested_at=d["requested_at"],
        retrieved_at=d["retrieved_at"], evaluated_at=format_utc(evaluated_at), source_timestamp=stamp,
        selected_row_sha256=None if row is None else digest(row), native_value=native,
        normalized_percent=normalized, scale=scale, quality=disposition, qa=qa, status=status, reason=reason,
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
        self.journal.verify_records()
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
            diagnostic = b["version"] == DIAGNOSTIC
            result = (diagnostic_evidence(self.journal, evaluated_at=self.journal.now()) if diagnostic else
                      evidence(self.journal, evaluated_at=self.journal.now(), live_proof=True))
            name = "latest-diagnostic.json" if diagnostic else "latest-evidence.json"
            self.journal.fs.write_new(self.journal.prefix + "/" + name, encode(result), PAGE_BYTES)
            return result
        finally:
            self.current_spec = self.executor = self.wait = None
            self.active = False
