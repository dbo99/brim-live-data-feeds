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
PREFIX_VERSION = "dendra-prefix-recovery-1"
# A second explicit incident, not a generic retry or campaign discovery API.
PREFIX_INCIDENT = dict(
    task_id="11650e37c474e083ca5ad35d454ea8d37fa42df8d06aa24ee26960066dc1b3e5",
    campaign_id="cohort-roster-741b1122e43892aebbe456de39fd215522ef5240886d2fce285cef877e651771",
    header_sha256="87ab2e3e88f1103be2b4c4ccb4ae48663251a33e9a38d953b93c44f8800f985a",
    anchor_sha256="7089c04930272a6f25cc0b0bd11b741878e80178f92b55b11dcf0f1c270d75b9",
    object_sha256="967082e2d8ea822ac47b9e874c5ff4862762f0fbc2728282198ee812bfc072f8",
    stream_id="5ae879eafe27f43c63102e86", station_id="58e68cacdf5ce600012602c3",
    start="2023-08-02T02:00:00.000Z", end="2023-09-01T02:00:00.000Z",
    cursor="2023-08-16T01:50:00.000Z")
PREFIX_CHARGES = dict(attempts=2, logical_requests=2, response_bytes=149207, source_rows=2016)
PREFIX_LIMITS = dict(attempts=4, logical_requests=4, response_bytes=16926423,
                     source_rows=6048, intervals=1, sessions=128, elapsed_ms=600000)


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


def rejected_empty_witness(journal, sid):
    """Interpret a hash-bound zero-row rejection; never change Journal counters.

    This read-only evidence statement is not a receipt replacement, retry,
    admissible empty witness or authority to use the historical source for IO.
    """
    from . import authority_witness as w, witness_diagnostic as d
    from .journal import Journal
    require(type(journal) is Journal and journal.inspect_only and journal.lock is not None,
            "Rejected witness recovery requires historical inspection")
    journal.verify_records()
    b = journal.binding
    require(b.get("mode") == w.MODE and (b, journal.tasks) == w._prepare(journal.inventory,
        campaign_id=b["campaign_id"], packets=b["metadata_packets"], as_of=b["prepared_at"],
        sources=b["collector_sources"]), "Rejected witness historical binding changed")
    task = "witness-" + sid
    require(task in b["witness_requests"], "Rejected witness outside selection")
    request = b["witness_requests"][task]
    attempts = [a for a in journal.snapshot()["attempts"].values() if a["task_key"] == task]
    require(len(attempts) == 1, "Rejected witness attempt closure")
    a = attempts[0]; details = a.get("details", {})
    logical = digest(dict(task=task, run=0, cursor=request["request_id"]))
    require(a["state"] == "failure" and a["ordinal"] == 1 and a["status"] == 200 and
        a["interval_key"] is None and a["run"] == 0 and a["cursor"] == request["request_id"] and
        a["logical_key"] == logical and a["attempt_key"] == digest(dict(task=task, page=logical, attempt=1)) and
        (a["source_rows"] is None or type(a["source_rows"]) is int and a["source_rows"] == 0) and
        a["representation"] == "sanitized" and a["body_retained"] is True and
        len(a["objects"]) == 1 and a.get("service_failure") is False and
        details.get("kind") == w.KIND and details.get("outcome") == "hold" and
        details.get("error_code") == "parse_or_privacy" and details.get("retryable") is False and
        details.get("privacy") == details.get("identity") == "hold", "Unsupported spent witness rejection")
    records = {}
    for kind in ("reserved", "started", "received", "failure"):
        found = [r for r in journal.events if r["kind"] == kind and r["data"].get("attempt_key") == a["attempt_key"]]
        require(len(found) == 1, "Rejected witness receipt chain closure")
        records[kind] = found[0]
    ordered = list(records.values())
    require([r["sequence"] for r in ordered] == sorted(r["sequence"] for r in ordered) and
        parse_utc(records["reserved"]["at"]) <= parse_utc(records["started"]["at"]) <=
        parse_utc(details["requested_at"]) <= parse_utc(details["retrieved_at"]) <=
        parse_utc(records["received"]["at"]) <= parse_utc(records["failure"]["at"]) <= parse_utc(journal.now()),
        "Rejected witness chronology")
    value = decode(journal.read_object(a["objects"][0]))
    require(d.known_zero_rejection(value, request) and value["body_sha256"] == a["response_sha256"] and
        value["body_bytes"] == a["response_bytes"], "Unknown response lacks bound zero-row rejection evidence")
    result = dict(schema_version="dendra-rejected-witness-row-interpretation-1", identity=b["roster"][sid],
        campaign_id=b["campaign_id"], task_key=task, attempt_key=a["attempt_key"],
        journal_header_sha256=journal.header_sha, journal_binding_sha256=journal.binding_sha,
        collector_fingerprint=digest(b["collector_sources"]), request=request,
        record_identities={k:r["record_sha256"] for k,r in records.items()},
        diagnostic_object=a["objects"][0], diagnostic_sha256=value["diagnostic_sha256"],
        original_response_sha256=a["response_sha256"], response_bytes=a["response_bytes"],
        original_receipt_source_rows=a["source_rows"], interpreted_returned_rows=0,
        original_unknown_row_responses=journal.snapshot()["counters"]["unknown_row_responses"],
        accounting_source="original Journal; interpretation is evidence only", attempt_spent=True,
        raw_body_retained=False, witness_admissible=False, reason=value["reason"]["code"],
        source_start_authority="UNKNOWN_SOURCE_START", dispatch_ready=False, provider_retry=False)
    return dict(result, interpretation_sha256=digest(result))


def predecessor(ref, inventory):
    """Independently recompute charges and immutable receipt/anchor identities."""
    if ref.get("incident") == PREFIX_VERSION:
        return prefix_predecessor(ref, inventory)
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


def prefix_predecessor(ref, inventory):
    """Validate the one pinned successful-prefix/deadline incident read-only."""
    from .daily_handoff import _binding
    from .eligibility import validate_decision
    from .provider_adapter import observation_shape
    from ..transport import format_utc
    require(set(ref) == {"incident", "root", "authorization_path", "authorization_sha256"},
            "Prefix predecessor reference fields")
    pin = PREFIX_INCIDENT
    with open_evidence(ref["root"], pin["campaign_id"], inventory) as j:
        require(j.header_sha == pin["header_sha256"] and not j.damage and
                j.events[-1]["record_sha256"] == pin["anchor_sha256"], "Prefix predecessor pins differ")
        j.verify_records()
        fingerprint = digest(j.binding["collector_sources"])
        _binding(j.binding, j.tasks, inventory, fingerprint)
        task = j.tasks[pin["task_id"]]
        require(task["identity"] == inventory.identity(pin["stream_id"]) and
                task["identity"]["station_id"] == pin["station_id"] and
                (task["start"], task["end"]) == (pin["start"], pin["end"]) and
                j.binding["quality_policy"] == quality.binding(), "Prefix identity/interval/policy differs")
        state = j.snapshot()
        attempts = [a for a in state["attempts"].values() if a["interval_key"] == pin["task_id"]]
        require(len(attempts) == 2 and state["intervals"][pin["task_id"]]["complete"] is None and
                state["intervals"][pin["task_id"]]["state"] == "held", "Prefix predecessor state differs")
        first, failed = attempts
        require(first["state"] == "received" and first["status"] == 200 and first["body_retained"] and
                first["representation"] == "original" and len(first["objects"]) == 1 and
                first["response_sha256"] == first["objects"][0]["sha256"] == pin["object_sha256"] and
                first["cursor"] == task["start"] and first["run"] == failed["run"] == 1 and
                failed["state"] == "failure" and failed["status"] is None and
                failed["details"]["error_code"] == "deadline" and failed["objects"] == [] and
                not failed["body_retained"] and failed["response_bytes"] == failed["source_rows"] == 0,
                "Prefix receipt/failure differs")
        charges = dict(attempts=len(attempts), logical_requests=len({a["logical_key"] for a in attempts}),
            response_bytes=sum(a["response_bytes"] for a in attempts), source_rows=sum(a["source_rows"] for a in attempts))
        require(charges == PREFIX_CHARGES, "Prefix predecessor charges differ")
        body = j.read_object(first["objects"][0])
        raw = observation_shape(body, pin["stream_id"], quality_policy=quality.binding())
        require(len(raw["data"]) == raw["limit"] == first["source_rows"] == 2016 and
                len(body) == first["response_bytes"], "Prefix page accounting differs")
        stamps = [parse_utc(row["t"]) for row in raw["data"]]
        require(stamps == sorted(stamps) and all(parse_utc(task["start"]) <= t < parse_utc(task["end"]) for t in stamps) and
                format_utc(stamps[-1]) == failed["cursor"] == pin["cursor"] and
                stamps[-1] > parse_utc(first["cursor"]), "Prefix cursor/order/bounds differ")
        auth_path = Path(ref["authorization_path"])
        require(auth_path.is_absolute(), "Explicit predecessor authorization path required")
        with Root(auth_path.parent) as fs:
            auth_body = fs.read(auth_path.name, PAGE_BYTES)
        require(sha(auth_body) == ref["authorization_sha256"], "Predecessor authorization hash")
        auth = decode(auth_body)
        require(auth["schema_version"] == "dendra-campaign-dispatch-1" and
                auth["binding_sha256"] == j.binding_sha and auth["task_root"] == ref["root"] and
                0 < (parse_utc(auth["window_end"])-parse_utc(auth["window_start"])).total_seconds() <= 600,
                "Prefix predecessor authorization differs")
        bundle = j.binding["reviewed_bundles"][pin["stream_id"]]
        for a in attempts:
            require(parse_utc(auth["window_start"]) <= parse_utc(a["reserved_at"]) <=
                    parse_utc(a["at"]) <= parse_utc(auth["window_end"]), "Prefix predecessor window")
            validate_decision(inventory, encode(bundle["packet"]), bundle["review"], bundle["decision"],
                              executor_fingerprint=fingerprint, now=a["reserved_at"])
        receipts = [e["record_sha256"] for e in j.events if e["kind"] == "received" and
                    e["data"]["attempt_key"] in {a["attempt_key"] for a in attempts}]
        failures = [e["record_sha256"] for e in j.events if e["kind"] == "failure" and
                    e["data"]["attempt_key"] == failed["attempt_key"]]
        require(len(receipts) == 2 and len(failures) == 1, "Prefix receipt lineage")
        ordinal = task["native_task"]["identity"]["configuration_ordinal"]
        configuration = next(w for w in bundle["decision"]["configuration_windows"] if w["ordinal"] == ordinal)
        return dict(task_id=pin["task_id"], campaign_id=pin["campaign_id"], header_sha256=j.header_sha,
            binding_sha256=j.binding_sha, source_fingerprint=fingerprint, execution_version=j.binding["version"],
            quality_policy=j.binding["quality_policy"], authorization=auth, authorization_sha256=sha(auth_body),
            historical_attempt_limit=3, charges=charges, receipt_sha256=receipts, failure_sha256=failures[0],
            last_anchor_sha256=j.events[-1]["record_sha256"], event_sha256=[e["record_sha256"] for e in j.events],
            campaign_counters=state["counters"], task=task, configuration=configuration,
            configuration_sha256=bundle["decision"]["configuration_sha256"],
            prefix_receipt=first, body_retained=True, cursor=pin["cursor"], seal=None)


def prefix_body(journal):
    """Return verified old bytes; never dispatch or create a new receipt."""
    if journal.binding.get("version") != PREFIX_VERSION:
        return None
    old = predecessor(journal.binding["predecessor_ref"], journal.inventory)
    require(old == journal.binding["predecessor"], "Prefix predecessor changed")
    with open_evidence(journal.binding["predecessor_ref"]["root"], old["campaign_id"], journal.inventory) as j:
        return j.read_object(old["prefix_receipt"]["objects"][0])


def admitted_attempts(journal, attempts):
    """Read-only page closure: original receipt once, then new receipts only."""
    if journal.binding.get("version") != PREFIX_VERSION:
        return attempts
    body = prefix_body(journal)
    receipt = journal.binding["predecessor"]["prefix_receipt"]
    require(journal.read_object(receipt["objects"][0]) == body, "Archived prefix differs")
    return [receipt, *attempts]


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
    return _prepare(inventory, predecessor_ref=predecessor_ref, bundle=bundle,
        source_start_ref=source_start_ref, authorization=authorization, now=now,
        sources=source_binding(), checkpoint_id=checkpoint())


def _prepare(inventory, *, predecessor_ref, bundle, source_start_ref, authorization,
             now, sources, checkpoint_id):
    from .eligibility import validate_decision
    from .campaign_execution import VERSION as execution_version
    old = predecessor(predecessor_ref, inventory)
    require(set(authorization) == {"checkpoint", "root", "window_start", "window_end", "approval_reference"},
            "Explicit recovery authorization required")
    require(authorization["checkpoint"] == checkpoint_id and
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
    fingerprint = digest(sources)
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
    version = PREFIX_VERSION if predecessor_ref.get("incident") == PREFIX_VERSION else VERSION
    new_attempts = 2 if version == PREFIX_VERSION else 3
    slot = digest(dict(version=version, predecessor_header=old["header_sha256"], predecessor_task=old["task_id"]))
    key = digest(dict(recovery_slot=slot, predecessor_task=old["task_id"]))
    task = dict(task, recovery_identity=key, predecessor_task_id=old["task_id"])
    binding = dict(version=version, mode=MODE, campaign_id="recovery-"+slot,
        execution_version=execution_version, collector_sources=sources,
        inventory_sha256=INVENTORY_SHA256, roster=inventory.roster(), selected_ids=[sid],
        quality_policy=quality.binding(), predecessor_ref=predecessor_ref, predecessor=old,
        source_start_ref=source_start_ref, reviewed_source_start=reviewed_start,
        reviewed_bundles={sid:bundle}, authorization=authorization, planned_at=now,
        request_policy=campaign.policy(logical_requests=new_attempts, attempts=new_attempts,
            total_bytes=new_attempts*8388608, wall_seconds=600),
        budgets=dict(PREFIX_LIMITS if version == PREFIX_VERSION else LIMITS))
    require(len(encode(binding))+4096 <= PAGE_BYTES, "Recovery binding header capacity")
    return decode(encode(binding)), {key:decode(encode(task))}


def validate_binding(binding, tasks, *, inventory):
    require(binding["mode"] == MODE and binding["version"] in (VERSION, PREFIX_VERSION), "Recovery version")
    expected = prepare(inventory, predecessor_ref=binding["predecessor_ref"],
        bundle=binding["reviewed_bundles"][binding["selected_ids"][0]],
        source_start_ref=binding["source_start_ref"], authorization=binding["authorization"], now=binding["planned_at"])
    require(encode((binding,tasks)) == encode(expected), "Recovery source/policy/task binding changed")


def validate_historical_binding(binding, tasks, *, inventory):
    """Inspection only: original source/approval, never writable admission.

    Callers must also verify the immutable Journal header/anchors and seal.
    The writable Journal and authorize() still require validate_binding().
    """
    require(binding["mode"] == MODE and binding["version"] in (VERSION, PREFIX_VERSION), "Recovery version")
    expected = _prepare(inventory, predecessor_ref=binding["predecessor_ref"],
        bundle=binding["reviewed_bundles"][binding["selected_ids"][0]],
        source_start_ref=binding["source_start_ref"], authorization=binding["authorization"],
        now=binding["planned_at"], sources=binding["collector_sources"],
        checkpoint_id=binding["authorization"]["checkpoint"])
    require(encode((binding,tasks)) == encode(expected), "Historical recovery binding changed")


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
    prefix = journal.binding["version"] == PREFIX_VERSION
    limits = PREFIX_LIMITS if prefix else LIMITS
    require(set(keys) == set(state["attempts"]) and 1 <= len(keys) <= (2 if prefix else 3) and
            all(state["counters"][k] <= v for k,v in limits.items()), "Recovery cumulative seal accounting")
    old = journal.binding["predecessor"]
    result = dict(version=journal.binding["version"], predecessor_state="PREDECESSOR_FAILED_ATTEMPT",
        predecessor_task_id=old["task_id"], predecessor_header_sha256=old["header_sha256"],
        predecessor_receipt_sha256=old["receipt_sha256"], predecessor_failure_sha256=old["failure_sha256"],
        predecessor_anchor_sha256=old["last_anchor_sha256"], predecessor_charges=old["charges"],
        predecessor_authorization=old["authorization"],
        recovery_state="RECOVERY_ADMITTED_PAGES", recovery_attempt_keys=keys,
        recovery_authorization=journal.binding["authorization"], query_state="QUERY_COMPLETE",
        observation_state=envelope["quality_disposition"]["state"],
        quality_policy_sha256=journal.binding["quality_policy"]["sha256"],
        cumulative_charges={k:state["counters"][k] for k in CHARGES})
    if prefix:
        result.update(predecessor_state="PREDECESSOR_SUCCESSFUL_PREFIX_AND_FAILED_ATTEMPT",
            prefix_receipt=old["prefix_receipt"], continuation_cursor=old["cursor"],
            predecessor_campaign_counters=old["campaign_counters"])
    return result
