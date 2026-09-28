"""Reviewed campaign bridge to the existing immutable Journal and Adapter.

No HTTP client, alternate receipt store, publication or inferred authority.
Packets/reviews are trusted local inputs; hashes provide integrity, not signatures.
"""
from collections import Counter

from ..transport import parse_utc

from . import campaign
from .eligibility import validate_decision
from .model import Inventory, INVENTORY_SHA256, NAME, PAGE_BYTES, source_binding
from .safety import Root, Hold, decode, digest, encode, require

VERSION = "dendra-campaign-execution-2"
MODE = "campaign_reviewed_adapter"


class Stop(ValueError):
    """Fatal source, identity or persistent state incompatibility."""


def prepare(manifest, inventory, bundles, *, now, stream_ids=None):
    fingerprint = digest(source_binding())
    campaign.verify(manifest, inventory, executor_fingerprint=fingerprint)
    planned = campaign.plan(manifest, inventory, stream_ids=stream_ids, now=now, max_tasks=128)
    require(not planned["blocked_streams"] and not planned["configuration_gaps"] and
            not planned["remaining_count"] and planned["tasks"], "Execution requires a complete eligible bounded plan")
    selected = sorted({t["identity"]["frozen_identity"]["stream_id"] for t in planned["tasks"]})
    require(isinstance(bundles, dict) and set(bundles) == set(selected), "Exact reviewed bundle closure required")
    for sid, bundle in bundles.items():
        require(set(bundle) == {"packet", "review", "decision"}, "Reviewed bundle fields")
        require(bundle["decision"] == manifest["decisions"][sid], "Campaign decision differs from bundle")
        validate_decision(inventory, encode(bundle["packet"]), bundle["review"], bundle["decision"],
                          executor_fingerprint=fingerprint, now=now)
    tasks = {t["task_id"]: dict(identity=t["identity"]["frozen_identity"],
               start=t["identity"]["start"], end=t["identity"]["end"], native_task=t)
             for t in planned["tasks"]}
    policy = manifest["budgets"]
    require(all(policy[k] > 0 for k in ("logical_requests", "http_attempts", "total_bytes", "wall_seconds")),
            "Explicit positive provider budgets required")
    from .observation_quality import binding as quality_binding
    binding = dict(version=VERSION, mode=MODE, campaign_id=manifest["core"]["campaign_id"],
        quality_policy=quality_binding(),
        collector_sources=source_binding(), inventory_sha256=INVENTORY_SHA256,
        roster=inventory.roster(), selected_ids=selected,
        campaign_manifest={k: v for k, v in manifest.items() if k != "roster"},
        reviewed_bundles=bundles, planned_at=now, request_policy=policy,
        budgets=dict(logical_requests=policy["logical_requests"], attempts=policy["http_attempts"],
                     response_bytes=policy["total_bytes"], source_rows=policy["http_attempts"]*2016,
                     intervals=len(tasks), sessions=128, elapsed_ms=policy["wall_seconds"]*1000))
    # Check capacity before journal registration, not after its first durable write.
    require(len(encode(binding)) + 4096 <= PAGE_BYTES, "Execution binding exceeds bounded journal header")
    return decode(encode(binding)), decode(encode(tasks))


def validate_binding(binding, tasks, *, inventory):
    try:
        require(type(inventory) is Inventory and binding["version"] == VERSION and binding["mode"] == MODE,
                "Campaign execution version/inventory required")
        manifest = dict(binding["campaign_manifest"], roster=binding["roster"])
        expected = prepare(manifest, inventory, binding["reviewed_bundles"],
                           now=binding["planned_at"], stream_ids=binding["selected_ids"])
        require((binding, tasks) == expected, "Execution binding/task identity differs")
    except (Hold, KeyError, TypeError) as exc:
        raise Stop("Campaign execution binding/source mismatch") from exc


def authorize_task(journal, key, *, now):
    if journal.inspect_only or journal.damage or journal.inventory is None:
        raise Stop("Read-only/damaged/unbound journal cannot execute")
    if (digest(journal.binding) != journal.binding_sha or digest(journal.tasks) != journal.tasks_sha or
            journal.binding["collector_sources"] != source_binding()):
        raise Stop("Journal/source binding changed")
    if key not in journal.tasks:
        raise Stop("Task outside bound campaign")
    if journal.binding["mode"] == "task37_recovery_adapter":
        from .recovery import authorize
        return authorize(journal, key, now=now)
    task = journal.tasks[key]
    sid = task["identity"]["stream_id"]
    bundle = journal.binding["reviewed_bundles"][sid]
    # Constructor has reconstructed the whole task graph. Recheck immutable
    # originals and freshness at every page, before any reservation or dispatch.
    return validate_decision(journal.inventory, encode(bundle["packet"]), bundle["review"],
        bundle["decision"], executor_fingerprint=digest(source_binding()), now=now)


def summary(journal):
    state = journal.snapshot()
    streams = {sid: dict(task_count=0, sealed_count=0, covered_empty=0, held_count=0,
                        coverage="UNQUERIED") for sid in state["roster"]}
    recovery = {}
    for key, item in state["intervals"].items():
        stream = streams[journal.tasks[key]["identity"]["stream_id"]]
        stream["task_count"] += 1
        attempts = [a for a in state["attempts"].values() if a["interval_key"] == key]
        if item["complete"]:
            journal.completed(key)  # recheck archived bytes before coverage claim
            stream["sealed_count"] += 1
            stream["covered_empty"] += item["state"] == "complete_empty"
            recovery[key] = "REUSE_SEAL_NO_REQUEST"
        elif attempts:
            stream["held_count"] += 1
            recovery[key] = "UNSEALED_ATTEMPT_OPERATOR_REVIEW_NO_AUTOMATIC_REQUEST"
        else:
            recovery[key] = "UNSPENT_TASK_REQUIRES_CURRENT_ELIGIBILITY_AND_BUDGET"
    for item in streams.values():
        item["coverage"] = ("COVERED" if item["task_count"] and item["sealed_count"] == item["task_count"]
                            else "PARTIAL_OR_HOLD" if item["sealed_count"] or item["held_count"] else "UNQUERIED")
    return dict(version=VERSION, campaign_id=journal.binding["campaign_id"],
                binding_sha256=journal.binding_sha, source_fingerprint=digest(journal.binding["collector_sources"]),
                source_compatible=journal.binding["collector_sources"] == source_binding(),
                inspection_only=journal.inspect_only, counters=state["counters"],
                station_count=len({v["station_id"] for v in state["roster"].values()}),
                stream_count=len(streams), streams=streams, recovery=recovery,
                task_status_counts=dict(Counter(v["state"] for v in state["intervals"].values())),
                damage=state["damage"], publication_eligible=False)


def first_batch_readiness(proposed_tasks, manifest, inventory, *, now):
    """Classify the unchanged proposal without substituting streams or I/O."""
    campaign.verify(manifest, inventory, executor_fingerprint=digest(source_binding()))
    rows = []
    for task in proposed_tasks:
        identity = inventory.identity(task["stream_id"])
        require(identity["station_id"] == task["station_id"] and
                identity["native_unit"] == task["native_unit"], "First batch identity changed")
        decision = manifest["decisions"].get(task["stream_id"])
        status, reason, planned = "NEEDS_FRESH_METADATA_REVIEW", "Fresh station and exact-stream temporal packet plus explicit native review", None
        if decision is not None:
            if not decision["native_acquisition_eligible"]:
                status, reason = "HOLD_METADATA", ";".join(decision["hold_reasons"])
            elif not parse_utc(decision["evaluated_at"]) <= parse_utc(now) <= parse_utc(decision["valid_until"]):
                status, reason = "HOLD_ACCESS", "Reviewed access evidence expired or future"
            else:
                plan = campaign.plan(manifest, inventory, stream_ids=[task["stream_id"]], now=now)
                matches = [t for t in plan["tasks"] if (t["identity"]["start"], t["identity"]["end"]) ==
                           (task["start"], task["end"])]
                if len(matches) != 1 or plan["configuration_gaps"] or plan["remaining_count"]:
                    status, reason = "HOLD_TEMPORAL", "Reviewed configuration does not preserve exact proposed task"
                else:
                    planned = matches[0]
                    status = "ELIGIBLE_NOW" if decision["normalized_conversion_eligible"] else "HOLD_SCALE_ONLY_BUT_NATIVE_ELIGIBLE"
                    reason = "Separate explicit dispatch authorization still required"
        rows.append(dict(proposed_ordinal=task["proposed_ordinal"], station_id=task["station_id"],
            stream_id=task["stream_id"], native_unit=task["native_unit"], start=task["start"], end=task["end"],
            status=status, reason=reason, decision_sha256=None if decision is None else decision["decision_sha256"],
            task_id=None if planned is None else planned["task_id"],
            configuration_ordinal=None if planned is None else planned["identity"]["configuration_ordinal"]))
    eligible = {"ELIGIBLE_NOW", "HOLD_SCALE_ONLY_BUT_NATIVE_ELIGIBLE"}
    return dict(tasks=rows, eligibility_complete=all(r["status"] in eligible for r in rows),
                network_execution_authorized=False)


def inspect_state(task_root, campaign_id):
    """Hash-checked old journal inspection, never current execution approval."""
    require(isinstance(campaign_id, str) and NAME.fullmatch(campaign_id), "Campaign name required")
    prefix = "campaigns/" + campaign_id
    with Root(task_root) as root:
        header = decode(root.read(prefix + "/manifest.json", PAGE_BYTES))
        tasks = {}
        for desc in header["plan"]:
            page = decode(root.read(prefix + "/" + desc["path"], PAGE_BYTES))
            require(digest(page) == desc["sha256"] and len(encode(page)) == desc["bytes"], "Plan page integrity")
            for entry in page:
                require(entry["key"] not in tasks, "Duplicate task")
                tasks[entry["key"]] = entry["task"]
    from .journal import Journal
    with Journal(task_root, header["binding"], tasks, inspect_only=True) as journal:
        return summary(journal)
