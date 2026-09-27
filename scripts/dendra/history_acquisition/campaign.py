"""Pure campaign partition/planning contracts consumed by campaign_execution.

Completion input must come from verified accepted seals, never an HTTP success
or a caller's guessed coverage. This module does not attest external receipts.
"""
from collections import Counter
from datetime import timedelta
from urllib.parse import urlencode

from ..transport import parse_utc, format_utc
from .eligibility import DECISION, _hash
from .model import Inventory, INVENTORY_SHA256, NAME, MAX_PLAN, source_binding
from .safety import decode, digest, encode, require
from .scale_resolution import resolve
from . import presentation as presentation_contract

VERSION = "dendra-native-campaign-1"
TASK = "dendra-native-task-1"
POLICY = "dendra-native-request-policy-1"


def policy(*, logical_requests=0, attempts=0, total_bytes=0, wall_seconds=0):
    values = (logical_requests, attempts, total_bytes, wall_seconds)
    require(all(type(n) is int and n >= 0 for n in values) and attempts >= logical_requests,
            "Finite explicit budgets required")
    return dict(version=POLICY, logical_requests=logical_requests, http_attempts=attempts,
                total_bytes=total_bytes, wall_seconds=wall_seconds, concurrency=1, retries=0,
                request_deadline_seconds=25, response_bytes=8*1024**2, pages_per_task=3,
                rows_per_page=2016, redirects=0, first_429="CAMPAIGN_PAUSE",
                minimum_spacing_seconds=1, executor_implemented=True)


def failure_scope(kind):
    """Proposed campaign disposition, not a transport/retry implementation."""
    scopes = {"429": "CAMPAIGN_PAUSE", "403": "STREAM_ACCESS_HOLD",
              "5xx": "CAMPAIGN_PAUSE", "timeout": "CAMPAIGN_PAUSE",
              "malformed": "TASK_SCHEMA_HOLD", "oversized": "CAMPAIGN_STOP",
              "incomplete_pagination": "TASK_INCOMPLETE", "schema_change": "CAMPAIGN_STOP",
              "identity_change": "STREAM_IDENTITY_HOLD", "integrity": "CAMPAIGN_STOP"}
    require(kind in scopes, "Unknown failure class")
    return scopes[kind]


def make_campaign(inventory, *, campaign_id, executor_fingerprint, horizons, decisions=None,
                  chunk_days=30, budgets=None, presentation=None):
    require(type(inventory) is Inventory and isinstance(campaign_id, str) and NAME.fullmatch(campaign_id),
            "Explicit campaign identity required")
    require(type(chunk_days) is int and 1 <= chunk_days <= 30, "Chunk ceiling is 30 days")
    roster = inventory.roster()
    require(isinstance(horizons, dict) and set(horizons) <= set(roster), "Horizon outside frozen roster")
    for v in horizons.values():
        require(set(v) == {"start", "end"} and parse_utc(v["start"]) < parse_utc(v["end"]), "Half-open horizon required")
    if presentation is not None:
        presentation_contract.verify(presentation, inventory)
        require(horizons == presentation["horizons"], "Presentation horizon differs from reviewed source starts")
    decisions = {} if decisions is None else decisions
    require(isinstance(decisions, dict) and set(decisions) <= set(roster), "Decision outside roster")
    source = _hash(executor_fingerprint)
    for sid, d in decisions.items():
        require(d["schema_version"] == DECISION and d["stream_id"] == sid and
                d["identity"] == roster[sid] and d["inventory_sha256"] == INVENTORY_SHA256 and
                d["executor_fingerprint"] == source and
                d["decision_sha256"] == digest({k: v for k, v in d.items() if k != "decision_sha256"}),
                "Decision identity/source integrity")
    limits = policy() if budgets is None else budgets
    require(limits == policy(logical_requests=limits["logical_requests"], attempts=limits["http_attempts"],
                            total_bytes=limits["total_bytes"], wall_seconds=limits["wall_seconds"]),
            "Unsupported provider policy")
    core = dict(version=VERSION, campaign_id=campaign_id, executor_fingerprint=source,
                inventory_sha256=INVENTORY_SHA256, horizons=horizons, chunk_days=chunk_days,
                request_policy=POLICY)
    # Omission preserves the exact legacy manifest and task identity contract.
    # A new explicit planning mode binds new campaigns; it never migrates one.
    if presentation is not None:
        core["presentation"] = presentation
    value = dict(core=core, campaign_identity=digest(core), roster=roster, decisions=decisions,
                 budgets=limits, network_execution_authorized=False)
    return decode(encode(dict(value, manifest_sha256=digest(value))))


def verify(campaign, inventory, *, executor_fingerprint):
    require(campaign["core"]["version"] == VERSION and
            campaign["core"]["executor_fingerprint"] == executor_fingerprint, "Campaign source/version mismatch")
    expected = make_campaign(inventory, campaign_id=campaign["core"]["campaign_id"],
        executor_fingerprint=executor_fingerprint, horizons=campaign["core"]["horizons"],
        decisions=campaign["decisions"], chunk_days=campaign["core"]["chunk_days"], budgets=campaign["budgets"],
        presentation=campaign["core"].get("presentation"))
    require(campaign == expected, "Campaign manifest/roster mismatch")
    return True


def partition(campaign, inventory):
    verify(campaign, inventory, executor_fingerprint=digest(source_binding()))
    states = {}
    for sid, identity in campaign["roster"].items():
        d = campaign["decisions"].get(sid)
        scale = resolve(inventory, sid) if d is None else d["scale"]
        reasons = ["metadata_review_required"] if d is None else d["hold_reasons"]
        group = "metadata_review_required"
        if d is not None:
            group = "native_eligible" if d["native_acquisition_eligible"] else (
                "access_hold" if d["access_status"] == "stale" else "review_required")
        states[sid] = dict(roster_member=True, station_id=identity["station_id"], partition=group,
            scale_route=scale["resolution_status"], scale_decision_sha256=scale["decision_sha256"],
            access_status="unverified" if d is None else d["access_status"],
            metadata_review_status="required" if d is None else d["metadata_review_status"],
            scientific_identity_status="frozen_only" if d is None else d["scientific_identity_status"],
            historical_applicability="unknown_history", native_acquisition_eligible=bool(d and d["native_acquisition_eligible"]),
            normalized_conversion_eligible=bool(d and d["normalized_conversion_eligible"]),
            normalized_percent_product_eligible=False, queried_intervals=None, archived_intervals=None,
            daily_product_eligible=False, publication_eligible=False, hold_reasons=reasons)
    return dict(version=VERSION, station_count=122, stream_count=len(states), states=states,
                partition_counts=dict(Counter(s["partition"] for s in states.values())),
                scale_counts=dict(Counter(s["scale_route"] for s in states.values())),
                coverage_authority="not_loaded_no_coverage_claim")


def plan(campaign, inventory, *, stream_ids=None, station_ids=None, max_tasks=128,
         after_task=None, completed=None, now=None):
    """Dry plan; completed maps task IDs to verified complete/empty seal refs.

    The future journal bridge must verify those seals and archives before calling
    this pure function. CLI does not expose this unimplemented trust boundary.
    """
    verify(campaign, inventory, executor_fingerprint=digest(source_binding()))
    require(type(max_tasks) is int and 1 <= max_tasks <= MAX_PLAN, "Bounded task maximum required")
    roster = campaign["roster"]
    presentation = campaign["core"].get("presentation")
    if presentation is not None:
        require(now is not None and parse_utc(presentation["as_of"]) <= parse_utc(now),
                "Presentation as_of must not exceed explicit planning time")
    available = set(presentation["streams"]) if presentation is not None else set(campaign["core"]["horizons"])
    selected = available.copy()
    if stream_ids is not None:
        require(isinstance(stream_ids, list) and stream_ids and len(stream_ids) == len(set(stream_ids)) and
                set(stream_ids) <= set(roster), "Invalid exact stream subset")
        selected = set(stream_ids)
    if station_ids is not None:
        require(isinstance(station_ids, list) and station_ids and len(station_ids) == len(set(station_ids)) and
                set(station_ids) <= {v["station_id"] for v in roster.values()}, "Invalid exact station subset")
        subset = {sid for sid in roster if roster[sid]["station_id"] in station_ids}
        selected = selected & subset if stream_ids is not None else subset
    require(selected and selected <= available, "Explicit horizon required for every selected stream")
    tasks, blocked, gaps = [], {}, []
    for sid in sorted(selected):
        if presentation is not None and not presentation["streams"][sid]["eligible_for_campaign_planning"]:
            blocked[sid] = [presentation["streams"][sid]["planning_state"]]
            continue
        d = campaign["decisions"].get(sid)
        if not d or not d["native_acquisition_eligible"]:
            blocked[sid] = ["metadata_review_required"] if not d else d["hold_reasons"]
            continue
        require(now is not None and parse_utc(d["evaluated_at"]) <= parse_utc(now) <= parse_utc(d["valid_until"]),
                "Fresh explicit planning time required; re-evaluate expired decisions")
        h = campaign["core"]["horizons"][sid]
        lo, hi = parse_utc(h["start"]), parse_utc(h["end"])
        require(parse_utc(d["scope"]["start"]) <= lo < hi <= parse_utc(d["scope"]["end"]), "Horizon exceeds reviewed scope")
        cuts = {lo, hi}
        cursor = lo
        while cursor < hi:
            cursor = min(hi, cursor + timedelta(days=campaign["core"]["chunk_days"]))
            cuts.add(cursor)
            require(len(cuts) <= MAX_PLAN+1, "Plan interval bound")
        windows = d["configuration_windows"]
        for w in windows:
            for point in (w["start"], w["end"]):
                if point is not None and lo < parse_utc(point) < hi:
                    cuts.add(parse_utc(point))
        points = sorted(cuts)
        for a, b in zip(points, points[1:]):
            owners = [w for w in windows if parse_utc(w["start"]) <= a and
                      (w["end"] is None or b <= parse_utc(w["end"]))]
            if not owners:
                gaps.append(dict(stream_id=sid, start=format_utc(a), end=format_utc(b), state="CONFIGURATION_GAP_NOT_QUERIED"))
                continue
            require(len(owners) == 1, "Ambiguous configuration interval")
            start, end = format_utc(a), format_utc(b)
            query = [("datastream_id", sid), ("time[$gte]", start), ("time[$lt]", end),
                     ("$sort[time]", "1"), ("$limit", "2016")]
            identity = dict(schema_version=TASK, campaign_identity=campaign["campaign_identity"],
                campaign_version=VERSION, collector_fingerprint=campaign["core"]["executor_fingerprint"],
                inventory_sha256=INVENTORY_SHA256, frozen_identity=roster[sid],
                native_authority_sha256=d["native_authority_sha256"],
                configuration_evidence_sha256=d["configuration_evidence_sha256"],
                configuration_ordinal=owners[0]["ordinal"], start=start, end=end, request_policy=POLICY)
            tasks.append(dict(task_id=digest(identity), identity=identity,
                scale_decision_sha256=d["scale"]["decision_sha256"],
                request=dict(method="GET", url="https://api.dendra.science/v2/datapoints?"+urlencode(query)),
                state="PLANNED", executable=False))
            require(len(tasks)+len(gaps) <= MAX_PLAN, "Campaign task/gap bound")
    tasks.sort(key=lambda t:(t["identity"]["start"],t["identity"]["frozen_identity"]["stream_id"],t["identity"]["end"]))
    completed = {} if completed is None else completed
    require(isinstance(completed, dict) and set(completed) <= {t["task_id"] for t in tasks}, "Completion outside exact plan")
    for tid, seal in completed.items():
        require(set(seal) == {"task_id", "source_fingerprint", "campaign_identity", "status", "seal_sha256", "archive_sha256"} and
                seal["task_id"] == tid and seal["source_fingerprint"] == campaign["core"]["executor_fingerprint"] and
                seal["campaign_identity"] == campaign["campaign_identity"] and
                seal["status"] in ("COMPLETE_EMPTY", "ARCHIVE_COMPLETE"), "Completion identity/status mismatch")
        _hash(seal["seal_sha256"]); _hash(seal["archive_sha256"])
    all_ids = [t["task_id"] for t in tasks]
    require(after_task is None or after_task in all_ids, "Unknown continuation cursor")
    offset = 0 if after_task is None else all_ids.index(after_task)+1
    pending = [t for t in tasks[offset:] if t["task_id"] not in completed]
    batch = pending[:max_tasks]
    return dict(version=VERSION, campaign_identity=campaign["campaign_identity"], dry_run=True,
                network_requests=0, executor_available=True, selected_streams=sorted(selected),
                tasks=batch, total_logical_tasks=len(tasks), completed_count=len(completed),
                remaining_count=max(0,len(pending)-len(batch)),
                next_after_task=batch[-1]["task_id"] if len(pending)>len(batch) else None,
                blocked_streams=blocked, configuration_gaps=gaps,
                readiness="REQUIRES_REVIEWED_EXECUTION_BINDING" if batch else "NO_ELIGIBLE_TASKS")
