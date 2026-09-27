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
SHARDS = "dendra-campaign-shards-1"


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


def shard_capacity(manifest, inventory, bundles, *, now):
    """Measure the real existing execution/header path, without journal creation."""
    from .campaign_execution import prepare
    from .model import PAGE_BYTES, index_pages
    binding,tasks = prepare(manifest,inventory,bundles,now=now)
    pages = index_pages([{"key":k,"task":t} for k,t in tasks.items()])
    descriptors = [dict(path=f"plan/{i:04d}.json",sha256=digest(p),bytes=len(encode(p))) for i,p in enumerate(pages)]
    header = dict(binding=binding,plan=descriptors,task_count=len(tasks))
    header_bytes = len(encode(header)); require(header_bytes <= PAGE_BYTES,"Journal header capacity HOLD")
    n = len(tasks); req = 3*n
    # Worst normal zero-retry task: run + 3*(reserved,started,received) + seal;
    # reserve an additional terminal HOLD event and every allowed session.
    event_bound = 12*n+binding["budgets"]["sessions"]
    require(n<=128 and event_bound<=MAX_PLAN,"Executor/journal task/event capacity HOLD")
    return dict(task_count=n,interval_count=n,executor_task_ceiling=128,maximum_pages=req,
        maximum_logical_requests=req,maximum_http_attempts=req,rows_per_page=2016,
        maximum_received_rows=req*2016,body_bytes_per_response=8*1024**2,
        maximum_provider_bytes=req*8*1024**2,request_timeout_seconds=25,
        maximum_provider_request_seconds=req*25,campaign_wall_budget_seconds=manifest["budgets"]["wall_seconds"],
        concurrency=1,minimum_request_start_spacing_seconds=1,retries=0,redirects=0,
        spacing_only_floor_for_maximum_requests_seconds=max(0,req-1),
        measured_execution_binding_bytes=len(encode(binding)),measured_journal_header_bytes=header_bytes,
        journal_header_byte_ceiling=PAGE_BYTES,measured_plan_page_bytes=sum(map(lambda p:len(encode(p)),pages)),
        journal_event_bound=event_bound,journal_event_ceiling=MAX_PLAN,
        archive_object_byte_ceiling=8*1024**2,
        state_upper_bound_components=dict(raw_pages=req*8*1024**2,native_envelopes=n*8*1024**2,
            mirrored_event_and_anchor_bytes=event_bound*65536*2,header=header_bytes,
            plan_pages=sum(len(encode(p)) for p in pages)),
        sensitivity_only=dict(cadence_seconds=600,rows_per_complete_day=144,
            note="Not a roster-wide cadence assertion; full pages may exhaust three-page ceiling."),
        unknown_quantities=["actual_cadence","gaps","compression","native_envelope_expansion","local_processing_seconds"],
        legacy_coverage_pending_limits_apply=False)


def plan_shards(inventory, authority, bundles, *, as_of):
    """Deterministic small campaign envelopes, using the existing task engine.

    Every new shard has its own existing-format campaign, keeping independent
    journals without changing task schema or rewriting earlier campaigns. Native
    tasks come only from plan(); no guessed task IDs for missing authority.
    Freshness affects readiness, never shard membership or identities.
    """
    from . import eligibility
    from .safety import Hold
    require(authority["schema_version"] == presentation_contract.AUTHORITY and
        authority["classification_sha256"] == digest({k:v for k,v in authority.items() if k!="classification_sha256"}) and
        authority["as_of"] == as_of, "Authority/as-of binding")
    entries = authority["streams"]
    resolved = {sid for sid,i in inventory.roster().items() if i["native_unit"] != "Dimensionless"}
    require(len(entries)==337 and {x["stream_id"] for x in entries}==resolved and set(bundles)<=resolved, "Resolved decision closure")
    fingerprint = digest(source_binding()); shards=[]; classifications=[]; held=[]
    for entry in sorted(entries,key=lambda x:x["stream_id"]):
        sid = entry["stream_id"]; source = presentation_contract.authority_start(inventory,entry)
        view = presentation_contract.make(inventory,mode=presentation_contract.INITIAL,as_of=as_of,source_starts={sid:source})
        scope = view["horizons"].get(sid); bundle = bundles.get(sid)
        readiness = eligibility.dispatch_readiness(inventory,sid,source_start_authority=source["state"],bundle=bundle,
            executor_fingerprint=fingerprint,now=as_of,horizon=scope)
        classifications.append(dict(entry,**{k:v for k,v in readiness.items() if k!="stream_id"}))
        if scope is None: continue
        if bundle is None:
            held.append(dict(stream_id=sid,reason="planning_requires_reviewed_metadata_configuration",horizon=scope));continue
        try:
            d = bundle["decision"]
            eligibility.validate_decision(inventory,encode(bundle["packet"]),bundle["review"],d,
                executor_fingerprint=fingerprint,now=d["evaluated_at"])
            lo,hi = parse_utc(scope["start"]),parse_utc(scope["end"])
            require(parse_utc(d["scope"]["start"])<=lo<hi<=parse_utc(d["scope"]["end"]),"Planning scope exceeds original reviewed eligibility")
            ordinal=0
            while lo<hi:
                require(len(shards)<128,"Bounded review package exceeds 128 planning shards; retain plan and HOLD remainder")
                end=min(hi,lo+timedelta(days=30))
                while True:
                    h=dict(start=format_utc(lo),end=format_utc(end))
                    seed=dict(version=SHARDS,source_start=source,source_fingerprint=fingerprint,as_of=as_of,
                        horizon=h,ordinal=ordinal,native_authority_sha256=d["native_authority_sha256"],policy=POLICY)
                    name="shard-"+digest(seed)
                    trial=make_campaign(inventory,campaign_id=name,executor_fingerprint=fingerprint,horizons={sid:h},
                        decisions={sid:d},budgets=policy(logical_requests=21,attempts=21,total_bytes=21*8*1024**2,wall_seconds=600))
                    planned=plan(trial,inventory,stream_ids=[sid],now=d["evaluated_at"],max_tasks=128)
                    require(not planned["configuration_gaps"] and not planned["remaining_count"] and planned["tasks"],"Configuration/capacity HOLD")
                    # Conservative 600-second proposal: seven tasks maximum, not
                    # a changed executor limit. Configuration cuts may add tasks.
                    if len(planned["tasks"])<=7: break
                    end=parse_utc(planned["tasks"][6]["identity"]["end"])
                n=len(planned["tasks"]); budgets=policy(logical_requests=3*n,attempts=3*n,total_bytes=3*n*8*1024**2,wall_seconds=600)
                manifest=make_campaign(inventory,campaign_id=name,executor_fingerprint=fingerprint,horizons={sid:h},decisions={sid:d},budgets=budgets)
                tasks=plan(manifest,inventory,stream_ids=[sid],now=d["evaluated_at"],max_tasks=128)["tasks"]
                capacity=shard_capacity(manifest,inventory,{sid:bundle},now=d["evaluated_at"])
                ready=readiness["dispatch_readiness"]=="DISPATCH_READY"
                core=dict(schema_version=SHARDS,campaign_identity=manifest["campaign_identity"],source_authority_sha256=digest(source),
                    ordered_task_ids=[t["task_id"] for t in tasks],execution_limit_policy=budgets,planning_as_of=as_of)
                shards.append(dict(shard_id=digest(core),identity=core,campaign=manifest,streams=[sid],
                    station_ids=[entry["station_id"]],native_units=[entry["native_unit"]],tasks=tasks,
                    dispatch_readiness=readiness,dispatch_ready_task_count=n if ready else 0,not_ready_task_count=0 if ready else n,
                    capacity=capacity,execution_authorized=False))
                lo=end;ordinal+=1
        except (Hold,ValueError,KeyError,TypeError) as exc:
            held.append(dict(stream_id=sid,reason="reviewed_configuration_or_capacity_hold",horizon=scope,
                detail=str(exc) if isinstance(exc,Hold) else type(exc).__name__))
    shards.sort(key=lambda s:(s["tasks"][0]["identity"]["start"],s["streams"][0],s["tasks"][-1]["identity"]["end"]))
    # If a stream cannot close its complete horizon, no earlier piece of that
    # stream is called executable. Preserve planning pieces for capacity review.
    held_ids={h["stream_id"] for h in held}
    for item in classifications:
        if item["stream_id"] in held_ids:
            item["dispatch_readiness"]="NOT_READY"
            item["dispatch_blockers"]=sorted(set(item["dispatch_blockers"]+["planning_configuration_or_capacity_hold"]))
    for s in shards:
        if s["streams"][0] in held_ids:
            s["dispatch_ready_task_count"]=0;s["not_ready_task_count"]=len(s["tasks"])
            s["dispatch_readiness"]=next(x for x in classifications if x["stream_id"]==s["streams"][0])
    executable=[s["shard_id"] for s in shards if s["dispatch_ready_task_count"]==len(s["tasks"])]
    value=dict(schema_version=SHARDS,as_of=as_of,collector_fingerprint=fingerprint,authority_sha256=authority["classification_sha256"],
        classifications=classifications,planning_holds=held,shards=shards,executable_shard_ids=executable,planning_shard_bound=128,
        first_real_acquisition_wave="NOT_READY" if not executable else executable[0],network_execution_authorized=False,
        ordering="first interval start, stream ID, final interval end; existing plan() order within each campaign",
        resume_rule="Independent original Journal per campaign; reuse verified seals, never refund spent/unsealed attempts or repack on completion.")
    return dict(value,plan_sha256=digest(value))
