"""Bounded temporal metadata acquisition through the existing Journal.

No parser, HTTP client, native eligibility acceptance or witness is invented here.
Live callers must separately authorize the bounded run and supply the existing
anonymous executor/wait. Tests inject synthetic IO through the same path.
"""
import threading
import urllib.error
from dataclasses import dataclass
from urllib.parse import urlencode

from ..transport import parse_utc, format_utc
from .d3_plan import RequestSpec, IDENTITIES, BASE, validate_request
from .dimensionless_probe import review_packet, CAMPAIGN_TEMPORAL_PROFILE, TARGET_REASONS
from .model import Inventory, INVENTORY_SHA256, NAME, PAGE_BYTES, MAX_PLAN, source_binding
from .provider_metadata import validate_authority, parse_station, _parse_station, parse_vocabulary, _fresh
from .provider_adapter import Adapter, CampaignAdapter, MetadataAdmissionHold
from .safety import Hold, require, encode, decode, digest

MODE = "temporal_metadata_adapter"
VERSION = "dendra-temporal-metadata-acquisition-1"
ROSTER_VERSION = "dendra-roster-metadata-acquisition-1"
EVIDENCE = "dendra-journal-temporal-metadata-evidence-1"
POLICY = dict(concurrency=1, retries=0, redirects=0, minimum_spacing_seconds=1,
              request_deadline_seconds=25, response_bytes=8*1024**2,
              list_limit=500, pagination=False, wall_seconds=150)


@dataclass(frozen=True)
class RosterRequestSpec(RequestSpec):
    station_id: str | None = None

    def url(self):
        from .model import ID
        require(self.cursor is None, "Metadata pagination forbidden")
        if self.kind == "unit-vocabulary":
            require(self.selected_stream is None and self.station_id is None, "Vocabulary arguments")
            return BASE + "vocabularies/dt-unit"
        require(self.kind in ("station", "datastream-list") and
                isinstance(self.station_id, str) and ID.fullmatch(self.station_id) and
                isinstance(self.selected_stream, str) and ID.fullmatch(self.selected_stream),
                "Exact frozen metadata request required")
        return (BASE + "stations/" + self.station_id if self.kind == "station" else
                BASE + "datastreams?" + urlencode({"station_id": self.station_id, "$limit": 500, "$sort[_id]": 1}))


def _spec(binding, kind, sid=None):
    if binding["version"] != ROSTER_VERSION:
        return RequestSpec(kind, sid)
    representative = None if kind == "unit-vocabulary" else binding["selected_ids"][0]
    require(sid is None or sid in binding["selected_ids"], "Metadata stream outside package")
    return RosterRequestSpec(kind, representative, station_id=None if representative is None else
                             binding["roster"][representative]["station_id"])


def slot(spec):
    return "unit-vocabulary" if spec.kind == "unit-vocabulary" else spec.kind + "-" + spec.selected_stream


def prepare(inventory, authority, *, selected_ids, campaign_id, package=None):
    require(type(inventory) is Inventory and isinstance(campaign_id, str) and NAME.fullmatch(campaign_id),
            "Explicit frozen inventory/campaign required")
    require(isinstance(selected_ids, (list, tuple)) and selected_ids and
            len(set(selected_ids)) == len(selected_ids), "Exact unique metadata targets required")
    validate_authority(authority)
    ids = sorted(selected_ids)
    if package is None:
        require(len(ids) <= 2 and set(ids) <= set(IDENTITIES) and
                all(inventory.identity(s) == IDENTITIES[s] for s in ids), "Frozen legacy metadata identity changed")
        specs = [RequestSpec("unit-vocabulary")] + [RequestSpec(k,s) for s in ids for k in ("station","datastream-list")]
    else:
        from .authority_package import validate, station_groups, metadata_campaign_id
        validate(package, inventory)
        station_id = inventory.identity(ids[0])["station_id"]
        require(station_groups(package).get(station_id) == ids and len(ids) <= 16 and
                campaign_id == metadata_campaign_id(package, station_id), "Exact package station membership required")
        specs = [RosterRequestSpec("unit-vocabulary")] + [
            RosterRequestSpec(k, ids[0], station_id=station_id) for k in ("station", "datastream-list")]
    n = len(specs)
    binding = dict(version=VERSION, mode=MODE, campaign_id=campaign_id,
        inventory_sha256=INVENTORY_SHA256, collector_sources=source_binding(),
        roster=inventory.roster(), selected_ids=ids, authority=authority,
        metadata_profile=CAMPAIGN_TEMPORAL_PROFILE, request_policy=POLICY,
        requests={slot(s):s.descriptor() for s in specs},
        budgets=dict(logical_requests=n, attempts=n, response_bytes=n*8*1024**2,
                     source_rows=20000, intervals=0, elapsed_ms=150000, sessions=1))
    if package is not None:
        binding.update(version=ROSTER_VERSION, authority_package=package)
    require(len(encode(binding))+4096 <= PAGE_BYTES, "Metadata header capacity")
    return decode(encode(binding)), {}


def validate_binding(binding, tasks, *, inventory):
    require(binding.get("mode") == MODE and tasks == {}, "Metadata only; no interval tasks")
    require((binding,tasks) == prepare(inventory,binding["authority"],selected_ids=binding["selected_ids"],
            campaign_id=binding["campaign_id"], package=binding.get("authority_package")), "Metadata source/plan/policy binding changed")


def traffic_guard(journal):
    require(journal.lock is not None and not journal.damage and not journal.inspect_only,
            "Writable metadata journal required")
    require(journal.binding["collector_sources"] == source_binding(), "Metadata source fingerprint changed")
    journal.check_budget()
    for a in journal.snapshot()["attempts"].values():
        d = a.get("details", {})
        require(a["state"] not in ("reserved","started") and a.get("status") not in (None,408,429) and
                not a.get("service_failure") and d.get("error_code") not in ("transport","deadline","body_limit","budget"),
                "Metadata transport/ambiguous spent attempt requires review")
        require(a["task_key"] != "unit-vocabulary" or
                (a["state"] == "received" and d.get("error_code") is None), "Vocabulary HOLD")


def _receipt(journal, spec):
    """Verify an admitted sanitized object plus original-response accounting."""
    journal.verify_records()
    require(journal.binding["requests"].get(slot(spec)) == spec.descriptor(), "Metadata request binding")
    task = "unit-vocabulary" if spec.kind == "unit-vocabulary" else "metadata-"+spec.selected_stream
    attempts = [a for a in journal.snapshot()["attempts"].values() if a["task_key"] == task and a["cursor"] == spec.kind]
    require(len(attempts) == 1, "Missing/ambiguous metadata receipt")
    a = attempts[0]; d = a.get("details", {})
    require(a["state"] == "received" and a["ordinal"] == 1 and a["status"] == 200 and
            a["interval_key"] is None and a["run"] == 0 and a["representation"] == "sanitized" and
            len(a["objects"]) == 1 and d.get("kind") == spec.kind and d.get("outcome") == "received" and
            d.get("privacy") == "public" and d.get("identity") == "match" and
            d.get("error_code") is None and d.get("retryable") is False, "Metadata admission receipt HOLD")
    logical = digest(dict(task=task,run=0,cursor=spec.kind))
    require(a["logical_key"] == logical and a["attempt_key"] == digest(dict(task=task,page=logical,attempt=1)),
            "Metadata reservation identity")
    records = {}
    for kind in ("reserved","started","received"):
        found = [r for r in journal.events if r["kind"] == kind and r["data"].get("attempt_key") == a["attempt_key"]]
        require(len(found) == 1, "Metadata receipt chain closure")
        records[kind] = found[0]
    require(records["reserved"]["sequence"] < records["started"]["sequence"] < records["received"]["sequence"] and
            parse_utc(records["reserved"]["at"]) <= parse_utc(records["started"]["at"]) <=
            parse_utc(d["requested_at"]) <= parse_utc(d["retrieved_at"]) <= parse_utc(records["received"]["at"]) <=
            parse_utc(journal.now()), "Metadata receipt chronology")
    value = decode(journal.read_object(a["objects"][0]))
    provenance = dict(request=spec.descriptor(),request_sha256=digest(spec.descriptor()),
        attempt_key=a["attempt_key"],records={k:v["record_sha256"] for k,v in records.items()},
        original_response_sha256=a["response_sha256"],original_response_bytes=a["response_bytes"],
        sanitized_object=a["objects"][0],requested_at=d["requested_at"],retrieved_at=d["retrieved_at"])
    return value, provenance


def validate_reservation(journal, task_key, cursor, *, interval_key, run):
    require(interval_key is None and run == 0, "Metadata cannot reserve history")
    require(cursor in ("unit-vocabulary","station","datastream-list"), "Metadata request kind")
    sid = None if cursor == "unit-vocabulary" else task_key.removeprefix("metadata-")
    spec = _spec(journal.binding,cursor,sid)
    require(task_key == ("unit-vocabulary" if sid is None else "metadata-"+sid) and
            journal.binding["requests"].get(slot(spec)) == spec.descriptor(), "Metadata request outside plan")
    traffic_guard(journal)
    require(not any(a["task_key"] == task_key and a["cursor"] == cursor for a in journal.snapshot()["attempts"].values()),
            "Metadata attempts remain spent; no retries")
    if cursor != "unit-vocabulary": _receipt(journal,_spec(journal.binding,"unit-vocabulary"))
    if cursor == "datastream-list":
        station,_ = _receipt(journal,_spec(journal.binding,"station",sid)); _fresh(station["checked_at"],journal.now())
    starts = [r for r in journal.events if r["kind"] == "started"]
    require(not starts or (parse_utc(journal.now())-parse_utc(starts[-1]["at"])).total_seconds() >= 1,
            "Metadata request-start spacing")
    require(len(journal.events)+6 <= MAX_PLAN, "Metadata event capacity before dispatch")


def packet_evidence(journal, sid, *, now):
    """Current-source, fresh evidence only; historical objects remain readable.

    Inspection does not renew a timestamp, accept eligibility or acquire a witness.
    Old-source objects may be read through inspect_only Journal/read_object, but
    they cannot be promoted here under a new fingerprint.
    """
    validate_binding(journal.binding,journal.tasks,inventory=journal.inventory)
    vocabulary,vp = _receipt(journal,_spec(journal.binding,"unit-vocabulary"))
    station,sp = _receipt(journal,_spec(journal.binding,"station",sid))
    packet,pp = _receipt(journal,_spec(journal.binding,"datastream-list",sid))
    if journal.binding["version"] == ROSTER_VERSION:
        require(packet["results"][sid].get("packet") is not None, "Selected stream metadata HOLD")
        packet = packet["results"][sid]["packet"]
    require(vocabulary == dict(dictionary_sha256=journal.binding["authority"]["dictionary_sha256"],
                               terms=journal.binding["authority"]["dictionary_terms"]), "Vocabulary object changed")
    require(station["exact_id"] == journal.binding["roster"][sid]["station_id"] and
            packet["access_evidence"]["station_metadata_sha256"] == digest(station) and
            packet["access_evidence"]["station_checked_at"] == station["checked_at"] and
            packet["original_response_sha256"] == pp["original_response_sha256"] and
            packet["original_response_bytes"] == pp["original_response_bytes"], "Packet/receipt/station binding")
    require(parse_utc(sp["requested_at"]) <= parse_utc(station["checked_at"]) <= parse_utc(sp["retrieved_at"]) and
            parse_utc(pp["requested_at"]) <= parse_utc(packet["checked_at"]) <= parse_utc(pp["retrieved_at"]),
            "Packet check/retrieval time binding")
    from .authority_witness import check_metadata
    check_metadata(journal.inventory,sid,packet,now=now)
    value = dict(schema_version=EVIDENCE,identity=journal.binding["roster"][sid],
        source_fingerprint=digest(journal.binding["collector_sources"]),journal_binding_sha256=journal.binding_sha,
        journal_header_sha256=journal.header_sha,packet=packet,packet_sha256=digest(packet),
        provenance=dict(vocabulary=vp,station=sp,datastream_list=pp),
        source_start_reviewed=False,dispatch_ready=False)
    return dict(value,evidence_sha256=digest(value))


def station_results(journal, *, now):
    """Reconstruct every selected outcome from one immutable shared receipt."""
    validate_binding(journal.binding,journal.tasks,inventory=journal.inventory)
    value,_ = _receipt(journal,_spec(journal.binding,"datastream-list",journal.binding["selected_ids"][0]))
    require(set(value["results"]) == set(journal.binding["selected_ids"]), "Station result closure")
    return {sid: packet_evidence(journal,sid,now=now) if "packet" in result else result
            for sid,result in value["results"].items()}


class MetadataAdapter(Adapter):
    attempts_per_page = 1

    def __init__(self,journal):
        validate_binding(journal.binding,journal.tasks,inventory=journal.inventory)
        require(not journal.inspect_only and not journal.damage and journal.lock is not None,
                "Writable current metadata journal required")
        self._initialize(journal,journal.binding["authority"])
        self.last_dispatch_mono = None
        self.used = False

    def plan(self):
        return decode(encode(self.journal.binding["requests"]))

    def _validate_dispatch(self,request,spec,interval_key):
        expected_type = RosterRequestSpec if self.journal.binding["version"] == ROSTER_VERSION else RequestSpec
        require(type(spec) is expected_type and spec is self.current_spec and interval_key is None and
                self.plan().get(slot(spec)) == spec.descriptor(), "Exact serial metadata specification required")
        validate_request(request,spec)
        traffic_guard(self.journal)
        CampaignAdapter._spacing(self)

    def _execute(self,request,*,timeout,interval_key):
        require(self.journal.binding["collector_sources"] == source_binding(), "Metadata source changed before dispatch")
        self.remaining()
        self.last_dispatch_mono = self.journal.monotonic()
        return self.executor(request,timeout=timeout)

    def _parse_metadata(self,spec,body):
        if spec.kind == "unit-vocabulary": return parse_vocabulary(body,self.authority)
        if spec.kind == "station":
            parser = _parse_station if self.journal.binding["version"] == ROSTER_VERSION else parse_station
            return parser(body,self.journal.binding["roster"][spec.selected_stream]["station_id"],
                                 checked_at=self.journal.now(),now=self.journal.now())
        require(spec.kind == "datastream-list", "Metadata-only parser")
        station,_ = _receipt(self.journal,_spec(self.journal.binding,"station",spec.selected_stream))
        if self.journal.binding["version"] == ROSTER_VERSION:
            # One response, independently parsed targets. No foreign raw record
            # escapes the existing sanitized temporal parser. Completeness is
            # checked for every target; incomplete lists cannot admit any packet.
            results = {}
            for sid in self.journal.binding["selected_ids"]:
                try:
                    packet = review_packet(body,station,self.journal.inventory,stream_id=sid,
                        metadata_profile=CAMPAIGN_TEMPORAL_PROFILE,checked_at=self.journal.now(),now=self.journal.now())
                    results[sid] = dict(packet=packet)
                except Hold as exc:
                    diagnostic = MetadataAdmissionHold(spec,body,decode(body),exc).diagnostic
                    code = TARGET_REASONS.get(str(exc),(diagnostic["reason"]["code"],None))[0]
                    # A shared page-level rejection must remain a rejected
                    # receipt, never page_complete=True. Missing one selected
                    # target on an otherwise complete list is stream-local.
                    if code.startswith("list.") and code != "list.selected_stream_absent":
                        raise
                    results[sid] = dict(outcome="HOLD",reason=code)
            return dict(results=results)
        return review_packet(body,station,self.journal.inventory,stream_id=spec.selected_stream,
            metadata_profile=CAMPAIGN_TEMPORAL_PROFILE,checked_at=self.journal.now(),now=self.journal.now())

    def run(self,*,executor,wait,authorization):
        require(callable(executor) and callable(wait) and not self.active and not self.used and
                threading.current_thread() is threading.main_thread(), "One-shot serial metadata runner required")
        require(isinstance(authorization,dict) and set(authorization) == {
            "approval_reference","binding_sha256","window_start","window_end"} and
            authorization["binding_sha256"] == self.binding_hash and
            isinstance(authorization["approval_reference"],str) and 0 < len(authorization["approval_reference"]) <= 160,
            "Separate exact metadata approval required")
        start,end,now = map(parse_utc,(authorization["window_start"],authorization["window_end"],self.journal.now()))
        require(start <= now < end and (end-start).total_seconds() <= POLICY["wall_seconds"], "Metadata runner window")
        require(not self.journal.snapshot()["attempts"], "Spent metadata journal cannot dispatch again")
        self.window_end = authorization["window_end"]
        self.journal.session(); self.used = True
        self.executor,self.wait,self.active = executor,wait,True
        results = {}
        def one(spec):
            self.current_spec = spec
            return self.exchange(self._request(spec),spec)[1]
        try:
            if self.journal.binding["version"] == ROSTER_VERSION:
                for kind in ("unit-vocabulary","station","datastream-list"):
                    one(_spec(self.journal.binding,kind))
                return station_results(self.journal,now=self.journal.now())
            one(RequestSpec("unit-vocabulary"))
            for sid in self.journal.binding["selected_ids"]:
                try:
                    one(RequestSpec("station",sid))
                    one(RequestSpec("datastream-list",sid))
                    results[sid] = packet_evidence(self.journal,sid,now=self.journal.now())
                except (MetadataAdmissionHold,urllib.error.HTTPError) as exc:
                    # Preserve independent stream HOLD only when shared durable
                    # accounting allows continuation. Service/unknown-row or
                    # ambiguous failures still stop all traffic.
                    traffic_guard(self.journal)
                    results[sid] = dict(outcome="HOLD",reason=exc.diagnostic["reason"]["code"]
                        if isinstance(exc,MetadataAdmissionHold) else "http_status_"+str(exc.code))
            return results
        finally:
            self.current_spec = self.executor = self.wait = None
            self.active = False
