"""One explicitly authorized cross-source recovery; the Journal owns accounting.

Trusted approval pins a single output root and immutable window. No discovery,
provider client, retry dispatcher, authority issuance, or historical mutation.
"""
from pathlib import Path
import os
import subprocess

from ..transport import parse_utc
from . import campaign, observation_quality as quality
from .model import INVENTORY_SHA256, PAGE_BYTES, source_binding
from .safety import Root, require, encode, decode, digest, sha

VERSION = "dendra-task37-recovery-1"
MODE = "task37_recovery_adapter"
TASK_ID = "82e376db5f25f2151abab73ce27ff7db64a411a4d26ec79dab5666bf611b390b"
CAMPAIGN_ID = "shard-1c295bab410a571d83dc9e23cdfab2d6e11325cf50273729644671949ad32f8f"
HEADER_SHA = "ecb762f0c4ea16be3d69b9067d7282b5c0c49687ba137aa4f8684a5a3657f866"
LAST_ANCHOR_SHA = "04c8f78b6373387af55a738593ec75decde3bf10293a3e330575e39c915b982e"
CHARGES = dict(attempts=1, logical_requests=1, response_bytes=177290, source_rows=2016)
LIMITS = dict(attempts=4, logical_requests=4, response_bytes=33554432,
              source_rows=8064, intervals=1, sessions=128, elapsed_ms=600000)


def checkpoint():
    """Local object identity only; never invoke a remote or refresh the index."""
    return subprocess.check_output(["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[3],
        env=dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1"), text=True).strip()


def open_evidence(root, campaign_id, inventory):
    """Open existing canonical Journal state without creating/recovering it."""
    from .journal import Journal
    prefix = "campaigns/" + campaign_id
    with Root(root) as fs:
        header = decode(fs.read(prefix + "/manifest.json", PAGE_BYTES))
        tasks = {}
        for desc in header["plan"]:
            body = fs.read(prefix + "/" + desc["path"], PAGE_BYTES)
            require(sha(body) == desc["sha256"] and len(body) == desc["bytes"], "Evidence plan hash")
            for entry in decode(body):
                require(entry["key"] not in tasks, "Duplicate evidence task")
                tasks[entry["key"]] = entry["task"]
    return Journal(root, header["binding"], tasks, inspect_only=True, inventory=inventory)


def predecessor(ref, inventory):
    """Independently recompute charges and immutable receipt/anchor identities."""
    require(set(ref) == {"root", "authorization_path", "authorization_sha256"}, "Predecessor reference fields")
    from .daily_handoff import _binding
    with open_evidence(ref["root"], CAMPAIGN_ID, inventory) as j:
        require(j.header_sha == HEADER_SHA and not j.damage and set(j.tasks) == {TASK_ID},
                "Approved predecessor header/task differs")
        j.verify_records()
        _binding(j.binding, j.tasks, inventory, digest(j.binding["collector_sources"]))
        state = j.snapshot()
        require(state["intervals"][TASK_ID]["complete"] is None and len(state["attempts"]) == 1,
                "Predecessor sealed or attempt count changed")
        require(all(state["counters"][k] == v for k, v in CHARGES.items()), "Predecessor charges differ")
        attempt_key, attempt = next(iter(state["attempts"].items()))
        require(attempt["interval_key"] == TASK_ID and attempt["state"] == "failure" and
                attempt["status"] == 200 and attempt["objects"] == [] and not attempt["body_retained"] and
                attempt["cursor"] == j.tasks[TASK_ID]["start"] and
                attempt["details"]["error_code"] == "parse_or_privacy",
                "Predecessor is not the omitted failed first page")
        require([e["kind"] for e in j.events] ==
                ["session", "run", "reserved", "started", "received", "failure", "held"],
                "Predecessor lineage changed")
        require(j.events[-1]["record_sha256"] == LAST_ANCHOR_SHA and j.binding["budgets"]["attempts"] == 3,
                "Approved predecessor anchor/attempt limit differs")
        authorization_body = j.fs.read(ref["authorization_path"], PAGE_BYTES)
        require(sha(authorization_body) == ref["authorization_sha256"], "Predecessor authorization hash")
        authorization = decode(authorization_body)
        require(authorization["campaign_id"] == CAMPAIGN_ID and
                authorization["binding_sha256"] == j.binding_sha and
                authorization["ordered_task_ids"] == [TASK_ID] and
                0 < (parse_utc(authorization["window_end"])-parse_utc(authorization["window_start"])).total_seconds() <= 600,
                "Predecessor authorization binding")
        require(parse_utc(authorization["window_start"]) <= parse_utc(attempt["reserved_at"]) <=
                parse_utc(attempt["at"]) <= parse_utc(authorization["window_end"]), "Predecessor execution window")
        task = j.tasks[TASK_ID]
        sid = task["identity"]["stream_id"]
        old_bundle = j.binding["reviewed_bundles"][sid]
        ordinal = task["native_task"]["identity"]["configuration_ordinal"]
        configuration = next(w for w in old_bundle["decision"]["configuration_windows"] if w["ordinal"] == ordinal)
        return dict(task_id=TASK_ID, campaign_id=CAMPAIGN_ID, header_sha256=j.header_sha,
            binding_sha256=j.binding_sha, source_fingerprint=digest(j.binding["collector_sources"]),
            execution_version=j.binding["version"], quality_policy=j.binding.get("quality_policy"),
            authorization=authorization, authorization_sha256=ref["authorization_sha256"],
            historical_attempt_limit=j.binding["budgets"]["attempts"], charges=dict(CHARGES),
            attempt_key=attempt_key, receipt_sha256=j.events[4]["record_sha256"],
            response_sha256=attempt["response_sha256"], failure_sha256=j.events[5]["record_sha256"],
            last_anchor_sha256=j.events[-1]["record_sha256"],
            event_sha256=[e["record_sha256"] for e in j.events],
            task=task, configuration=configuration, configuration_sha256=old_bundle["decision"]["configuration_sha256"],
            body_retained=False, cursor=None, seal=None)


def source_start(ref, inventory, *, now, packet):
    from .presentation import review_journal_first, REVIEWED
    require(set(ref) == {"root", "campaign_id", "binding_sha256", "review"}, "Source-start reference fields")
    sid = packet["stream_id"]
    with open_evidence(ref["root"], ref["campaign_id"], inventory) as j:
        require(j.binding_sha == ref["binding_sha256"] and j.binding["metadata_packets"][sid] == packet,
                "Witness metadata/source binding differs")
        result = review_journal_first(j, sid, review=ref["review"], as_of=now)
        require(result["state"] == REVIEWED, "Reviewed source start required")
        return result


def prepare(inventory, *, predecessor_ref, bundle, source_start_ref, authorization, now):
    """Require fresh separately reviewed authority and an explicit recovery slot.

    This does not create a Journal. The future approved caller must supply the
    real checkpoint, unique task-owned root, reviewer reference, start/deadline.
    """
    from .eligibility import validate_decision
    from .campaign_execution import VERSION as execution_version
    old = predecessor(predecessor_ref, inventory)
    require(set(authorization) == {"checkpoint", "root", "window_start", "window_end", "approval_reference"},
            "Explicit recovery authorization required")
    require(authorization["checkpoint"] == checkpoint() and
            isinstance(authorization["approval_reference"], str) and 0 < len(authorization["approval_reference"]) <= 256,
            "Recovery checkpoint/approval binding")
    out = Path(authorization["root"])
    require(out.is_absolute() and str(out.resolve()) == str(out) and
            out != Path(predecessor_ref["root"]).resolve() and
            Path(predecessor_ref["root"]).resolve() not in out.parents,
            "Fresh recovery root cannot be predecessor storage")
    start, end, at = map(parse_utc, (authorization["window_start"], authorization["window_end"], now))
    require(0 < (end-start).total_seconds() <= 600 and start <= at < end and
            parse_utc(old["authorization"]["window_end"]) < start, "Distinct bounded recovery window")
    require(set(bundle) == {"packet", "review", "decision"}, "Exact reviewed bundle required")
    sources = source_binding(); fingerprint = digest(sources)
    decision = validate_decision(inventory, encode(bundle["packet"]), bundle["review"], bundle["decision"],
                                 executor_fingerprint=fingerprint, now=now)
    task = old["task"]; sid = task["identity"]["stream_id"]
    require(decision["identity"] == task["identity"] == inventory.identity(sid) and
            parse_utc(decision["scope"]["start"]) <= parse_utc(task["start"]) < parse_utc(task["end"]) <=
            parse_utc(decision["scope"]["end"]), "Recovery frozen identity/scope differs")
    require(old["configuration"] in decision["configuration_windows"] and
            old["configuration_sha256"] == decision["configuration_sha256"], "Recovery configuration changed")
    reviewed_start = source_start(source_start_ref, inventory, now=now, packet=bundle["packet"])
    require(parse_utc(reviewed_start["start"]) <= parse_utc(task["start"]), "Source start excludes recovery interval")
    # Stable slot identity: changing source, authority, root or window cannot
    # create a second namespace in the authorized Journal storage.
    slot = digest(dict(version=VERSION, predecessor_header=old["header_sha256"], predecessor_task=TASK_ID))
    key = digest(dict(recovery_slot=slot, predecessor_task=TASK_ID))
    task = dict(task, recovery_identity=key, predecessor_task_id=TASK_ID)
    binding = dict(version=VERSION, mode=MODE, campaign_id="recovery-"+slot,
        execution_version=execution_version, collector_sources=sources,
        inventory_sha256=INVENTORY_SHA256, roster=inventory.roster(), selected_ids=[sid],
        quality_policy=quality.binding(), predecessor_ref=predecessor_ref, predecessor=old,
        source_start_ref=source_start_ref, reviewed_source_start=reviewed_start,
        reviewed_bundles={sid:bundle}, authorization=authorization, planned_at=now,
        request_policy=campaign.policy(logical_requests=3, attempts=3, total_bytes=3*8388608, wall_seconds=600),
        budgets=dict(LIMITS))
    require(len(encode(binding))+4096 <= PAGE_BYTES, "Recovery binding header capacity")
    return decode(encode(binding)), {key:decode(encode(task))}


def validate_binding(binding, tasks, *, inventory):
    require(binding["mode"] == MODE and binding["version"] == VERSION, "Recovery version")
    expected = prepare(inventory, predecessor_ref=binding["predecessor_ref"],
        bundle=binding["reviewed_bundles"][binding["selected_ids"][0]],
        source_start_ref=binding["source_start_ref"], authorization=binding["authorization"], now=binding["planned_at"])
    require(encode((binding,tasks)) == encode(expected), "Recovery source/policy/task binding changed")


def authorize(journal, key, *, now):
    require(not journal.inspect_only and journal.inventory is not None and key in journal.tasks,
            "Writable recovery and exact task required")
    validate_binding(journal.binding, journal.tasks, inventory=journal.inventory)
    # Reconstruct freshness from current time, never from the historic plan time.
    b = journal.binding
    prepare(journal.inventory, predecessor_ref=b["predecessor_ref"],
        bundle=b["reviewed_bundles"][b["selected_ids"][0]], source_start_ref=b["source_start_ref"],
        authorization=b["authorization"], now=now)
    validate_storage(b, journal.fs)
    return b["reviewed_bundles"][b["selected_ids"][0]]["decision"]


def validate_storage(binding, fs):
    expected, actual = os.stat(binding["authorization"]["root"]), os.fstat(fs.fd)
    require((expected.st_dev, expected.st_ino) == (actual.st_dev, actual.st_ino),
            "Recovery execution root differs")


def check_window(binding, now):
    window = binding["authorization"]
    require(parse_utc(window["window_start"]) <= parse_utc(now) < parse_utc(window["window_end"]),
            "Recovery authorization window expired or not started")


def seal_lineage(journal, keys, envelope):
    """The only ledger is Journal.snapshot, seeded with immutable old charges."""
    state = journal.snapshot()
    require(set(keys) == set(state["attempts"]) and 1 <= len(keys) <= 3 and
            all(state["counters"][k] <= v for k,v in LIMITS.items()), "Recovery cumulative seal accounting")
    old = journal.binding["predecessor"]
    return dict(version=VERSION, predecessor_state="PREDECESSOR_FAILED_ATTEMPT",
        predecessor_task_id=old["task_id"], predecessor_header_sha256=old["header_sha256"],
        predecessor_receipt_sha256=old["receipt_sha256"], predecessor_failure_sha256=old["failure_sha256"],
        predecessor_anchor_sha256=old["last_anchor_sha256"], predecessor_charges=old["charges"],
        predecessor_authorization=old["authorization"],
        recovery_state="RECOVERY_ADMITTED_PAGES", recovery_attempt_keys=keys,
        recovery_authorization=journal.binding["authorization"], query_state="QUERY_COMPLETE",
        observation_state=envelope["quality_disposition"]["state"],
        quality_policy_sha256=journal.binding["quality_policy"]["sha256"],
        cumulative_charges={k:state["counters"][k] for k in CHARGES})
