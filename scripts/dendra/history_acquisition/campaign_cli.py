"""Maintainer campaign CLI; only explicitly authorized collect/resume may dispatch."""
import argparse
import os
import time
from pathlib import Path

from . import campaign, campaign_execution
from .journal import Journal, utc_now
from ..transport import parse_utc
from .eligibility import decide
from .model import Inventory, INVENTORY_SHA256, source_binding
from .safety import Root, Hold, decode, digest, encode, require, sha

EXIT_HOLD = 2
EXIT_STOP = 3


def _read(path, limit=2*1024**2):
    path = Path(path)
    require(path.is_absolute(), "Explicit absolute input path required")
    with Root(path.parent) as root:
        return root.read(path.name, limit)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("plan", "status", "verify", "collect", "resume", "prepare-product"))
    p.add_argument("--inventory")
    p.add_argument("--inventory-sha256", choices=(INVENTORY_SHA256,))
    p.add_argument("--config", help="Absolute saved planning config; never a provider URL")
    p.add_argument("--manifest", help="Absolute existing offline campaign manifest")
    p.add_argument("--state-root", help="Existing absolute task-owned output root; exclusive writes only")
    p.add_argument("--now", help="Explicit UTC evaluation time; not provider time")
    p.add_argument("--stream", action="append")
    p.add_argument("--station", action="append")
    p.add_argument("--max-tasks", type=int, default=128)
    p.add_argument("--after-task")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--execution", help="Absolute prepared execution.json; no implied dispatch authority")
    p.add_argument("--authorization", help="Absolute explicit reviewed dispatch authorization")
    p.add_argument("--campaign-id", help="Read-only journal inspection within --state-root")
    for name in ("logical-requests", "http-attempts", "total-bytes", "wall-seconds"):
        p.add_argument("--" + name, type=int)
    return p


def _execute(args, inventory, *, executor, wait, now_fn, monotonic):
    require(args.execution and args.authorization and args.state_root and not args.dry_run and
            not args.config and not args.manifest and not args.stream and not args.station and
            not args.after_task and not args.campaign_id,
            "collect/resume require only prepared execution, authorization, state and explicit budgets")
    require((executor is None) == (wait is None), "Transport and pacing dependency pair required")
    require(executor is not None or (now_fn is None and monotonic is None), "Live dispatch requires real clocks")
    require(args.now is None or executor is not None, "Live dispatch uses the real UTC clock")
    saved = decode(_read(args.execution))
    require(set(saved) == {"binding", "tasks"}, "Execution descriptor fields")
    binding, tasks = saved["binding"], saved["tasks"]
    campaign_execution.validate_binding(binding, tasks, inventory=inventory)
    limits = binding["request_policy"]
    require((args.logical_requests, args.http_attempts, args.total_bytes, args.wall_seconds) ==
            tuple(limits[k] for k in ("logical_requests", "http_attempts", "total_bytes", "wall_seconds")),
            "Explicit dispatch budgets differ from immutable campaign")
    auth = decode(_read(args.authorization, 16384))
    require(set(auth) == {"schema_version", "approval_reference", "binding_sha256", "task_root",
                         "window_start", "window_end"} and
            auth["schema_version"] == "dendra-campaign-dispatch-1" and
            isinstance(auth["approval_reference"], str) and 0 < len(auth["approval_reference"]) <= 128 and
            auth["binding_sha256"] == digest(binding) and
            Path(auth["task_root"]) == Path(args.state_root) and Path(args.state_root).is_absolute(),
            "Explicit exact campaign/root authorization required")
    clock = now_fn or ((lambda: args.now) if args.now else utc_now)
    a, b, now = map(parse_utc, (auth["window_start"], auth["window_end"], clock()))
    require(a <= now < b and 0 < (b-a).total_seconds() <= min(600, limits["wall_seconds"]),
            "Authorized wall window invalid or expired")
    # All local bindings and fresh permissions precede opener construction.
    with Journal(args.state_root, binding, tasks, create=args.mode == "collect",
                 inventory=inventory, now=clock, monotonic=monotonic or time.monotonic) as journal:
        authorized_root = os.stat(auth["task_root"], follow_symlinks=False)
        opened_root = os.fstat(journal.fs.fd)
        require((authorized_root.st_dev, authorized_root.st_ino) == (opened_root.st_dev, opened_root.st_ino),
                "Authorized state root changed")
        for key in tasks:
            if journal.completed(key) is None:
                campaign_execution.authorize_task(journal, key, now=clock())
        from .provider_adapter import CampaignAdapter, anonymous_executor, anonymous_wait
        live = executor is None
        if live:
            executor, wait = anonymous_executor(b), anonymous_wait(b)
        results = CampaignAdapter(journal).run(executor=executor, wait=wait,
                                               window_end=b, task_keys=list(tasks))
        state = campaign_execution.summary(journal)
        held = any(journal.completed(key) is None for key in tasks)
        print(encode(dict(outcome="HOLD" if held else "SUCCESS", results=results, status=state,
                          receipt_prefix=journal.prefix, synthetic_transport=not live)).decode(), end="")
        return EXIT_HOLD if held else 0


def main(argv=None, *, executor=None, wait=None, now_fn=None, monotonic=None):
    args = parser().parse_args(argv)
    if args.mode == "prepare-product" or (args.mode in ("collect", "resume") and
            not (args.execution and args.authorization and args.state_root and args.inventory)):
        print(encode(dict(outcome="STOP", reason="explicit_execution_authority_required_or_product_unsupported",
                          mode=args.mode, provider_requests=0)).decode(), end="")
        return EXIT_STOP
    try:
        require(args.inventory and args.inventory_sha256, "Explicit inventory path/hash required")
        inventory = Inventory.load(args.inventory, args.inventory_sha256)
        fingerprint = digest(source_binding())
        if args.mode in ("collect", "resume"):
            return _execute(args, inventory, executor=executor, wait=wait, now_fn=now_fn, monotonic=monotonic)
        require(executor is None and wait is None and now_fn is None and monotonic is None and not args.execution and not args.authorization and
                all(v is None for v in (args.logical_requests, args.http_attempts, args.total_bytes, args.wall_seconds)),
                "Offline modes do not accept dispatch arguments")
        if args.mode in ("status", "verify") and args.state_root:
            require(args.campaign_id and not args.manifest and not args.config and not args.stream and
                    not args.station and not args.after_task, "Journal inspection needs state root and campaign ID")
            value = campaign_execution.inspect_state(args.state_root, args.campaign_id)
            value.update(provider_requests=0, execution_ready=False,
                         scope="hash_checked_archive_state_not_current_access_permission")
            print(encode(value).decode(), end="")
            return EXIT_HOLD if value["damage"] else 0
        if args.mode == "plan":
            require(args.dry_run and args.config and args.state_root and args.now and not args.manifest,
                    "plan requires --dry-run --config --state-root --now")
            config = decode(_read(args.config))
            require(set(config) == {"campaign_id", "horizons", "chunk_days", "budgets", "reviews"},
                    "Planning config fields")
            require(isinstance(config["reviews"], list) and len(config["reviews"]) <= 434, "Review input bound")
            decisions, bundles = {}, {}
            for entry in config["reviews"]:
                require(set(entry) == {"packet_path", "review_path", "review_sha256"}, "Explicit review references")
                review_body = _read(entry["review_path"], 65536)
                require(sha(review_body) == entry["review_sha256"], "Review file changed")
                packet_body = _read(entry["packet_path"], 65536)
                d = decide(inventory, packet_body, decode(review_body),
                           executor_fingerprint=fingerprint, now=args.now)
                require(d["stream_id"] not in decisions, "Duplicate stream review")
                decisions[d["stream_id"]] = d
                bundles[d["stream_id"]] = dict(packet=decode(packet_body), review=decode(review_body), decision=d)
            manifest = campaign.make_campaign(inventory, campaign_id=config["campaign_id"],
                executor_fingerprint=fingerprint, horizons=config["horizons"], decisions=decisions,
                chunk_days=config["chunk_days"], budgets=config["budgets"])
            plan = campaign.plan(manifest, inventory, stream_ids=args.stream, station_ids=args.station,
                                 max_tasks=args.max_tasks, after_task=args.after_task, now=args.now)
            state = campaign.partition(manifest, inventory)
            execution = None
            if not args.after_task and plan["tasks"] and not plan["blocked_streams"] and not plan["configuration_gaps"] and not plan["remaining_count"] and all(
                    config["budgets"][k] > 0 for k in ("logical_requests", "http_attempts", "total_bytes", "wall_seconds")):
                selected = plan["selected_streams"]
                binding, tasks = campaign_execution.prepare(manifest, inventory,
                    {sid: bundles[sid] for sid in selected}, now=args.now, stream_ids=selected)
                execution = dict(binding=binding, tasks=tasks)
            # Offline output, not campaign registration or a request-budget ledger.
            # Partial writes are preserved; there is no overwrite/recovery shortcut.
            prefix = "plans/" + config["campaign_id"] + "/" + digest(plan)
            with Root(args.state_root) as root:
                for name, value in (("manifest.json", manifest), ("plan.json", plan), ("status.json", state)):
                    root.write_new(prefix+"/"+name, encode(value), 2*1024**2)
                if execution is not None:
                    root.write_new(prefix+"/execution.json", encode(execution), 2*1024**2)
            print(encode(dict(outcome="HOLD" if plan["blocked_streams"] else "DRY_PLAN",
                              plan_path=prefix+"/plan.json", task_count=len(plan["tasks"]),
                              blocked_stream_count=len(plan["blocked_streams"]), provider_requests=0,
                              execution_ready=False)).decode(), end="")
            return EXIT_HOLD if plan["blocked_streams"] else 0
        require(args.manifest and not args.config and not args.state_root and not args.stream and
                not args.station and not args.after_task, "status/verify require only a saved manifest")
        manifest = decode(_read(args.manifest))
        campaign.verify(manifest, inventory, executor_fingerprint=fingerprint)
        value = campaign.partition(manifest, inventory) if args.mode == "status" else dict(outcome="VERIFIED")
        value.update(provider_requests=0, execution_ready=False,
                     scope="saved_offline_manifest_integrity_not_current_access_or_archive_verification")
        print(encode(value).decode(), end="")
        return 0
    except campaign_execution.Stop as exc:
        print(encode(dict(outcome="STOP", reason=str(exc), dispatch_count="consult_durable_receipts")).decode(), end="")
        return EXIT_STOP
    except Hold as exc:
        print(encode(dict(outcome="HOLD", reason=str(exc),
                          **({"dispatch_count": "consult_durable_receipts"} if args.mode in ("collect", "resume")
                             else {"provider_requests": 0}))).decode(), end="")
        return EXIT_HOLD
    except (OSError, KeyError, TypeError, ValueError):
        print(encode(dict(outcome="STOP", reason="input_or_persistence_failure",
                          **({"dispatch_count": "consult_durable_receipts"} if args.mode in ("collect", "resume")
                             else {"provider_requests": 0}))).decode(), end="")
        return EXIT_STOP


if __name__ == "__main__":
    raise SystemExit(main())
