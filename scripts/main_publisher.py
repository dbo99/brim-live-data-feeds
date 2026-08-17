#!/usr/bin/env python3
"""Publish one validated product candidate onto the current main branch.

The shared primitive owns Git authorization, isolation, allowlisted staging,
fresh-main retries, and non-force publication. Product callbacks own product
semantics, including same/stale/new decisions and final staged validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Mapping, Sequence, Set


MAIN_REF = "refs/heads/main"
METADATA_SCHEMA_VERSION = 1


class PublisherError(RuntimeError):
    """A fail-safe publisher rejection."""


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=str(cwd),
        env=None if env is None else dict(env),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise PublisherError(
            f"command failed ({result.returncode}): {' '.join(command)}"
            + (f"\n{detail}" if detail else "")
        )
    return result


def _normalize_relpath(value: str) -> str:
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise PublisherError(f"invalid allowlist path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or path == PurePosixPath(".") or ".." in path.parts:
        raise PublisherError(f"allowlist path must be repository-relative: {value}")
    normalized = path.as_posix()
    if normalized != value or not normalized.startswith("docs/data/"):
        raise PublisherError(
            f"allowlist path must be normalized beneath docs/data/: {value}"
        )
    return normalized


def _normalize_allowlist(values: Iterable[str]) -> List[str]:
    paths = [_normalize_relpath(value) for value in values]
    if not paths:
        raise PublisherError("the product allowlist must be nonempty")
    if len(paths) != len(set(paths)):
        raise PublisherError("the product allowlist contains duplicate paths")
    return paths


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_inventory(root: Path) -> List[str]:
    if not root.is_dir() or root.is_symlink():
        raise PublisherError(f"candidate root is not a plain directory: {root}")
    inventory: List[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise PublisherError(f"candidate artifact contains a symlink: {path}")
        if path.is_file():
            inventory.append(path.relative_to(root).as_posix())
    return inventory


def _read_metadata(path: Path) -> Dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise PublisherError(f"candidate metadata is not a plain file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublisherError(f"candidate metadata is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise PublisherError("candidate metadata root must be an object")
    return value


def _parse_utc_timestamp(value: object, label: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        raise PublisherError(f"{label} must be an RFC 3339 UTC timestamp")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PublisherError(f"{label} is not a valid UTC timestamp") from exc


def _validate_product_id(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,79}", value):
        raise PublisherError(f"invalid publisher product ID: {value!r}")
    return value


def _validate_source_sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", value):
        raise PublisherError("source event SHA must be a full 40-character Git SHA")
    return value.lower()


def prepare_metadata(args: argparse.Namespace) -> int:
    root = Path(args.candidate_root).resolve()
    output = Path(args.output).resolve()
    allowlist = _normalize_allowlist(args.allowlist)
    product_id = _validate_product_id(args.product_id)
    source_sha = _validate_source_sha(args.source_event_sha)
    if (
        root.name != "candidate"
        or output.parent != root.parent
        or output.name != "candidate-metadata.json"
    ):
        raise PublisherError(
            "candidate preparation must use candidate/ plus candidate-metadata.json"
        )
    if output.is_symlink():
        raise PublisherError("candidate metadata output must not be a symlink")
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{1,79}", args.semantic_key_type):
        raise PublisherError("semantic key type is invalid")
    if not args.semantic_key.strip():
        raise PublisherError("semantic key must be nonempty")

    inventory = _candidate_inventory(root)
    if inventory != sorted(allowlist):
        raise PublisherError(
            "candidate inventory does not exactly match allowlist: "
            f"candidate={inventory!r}, allowlist={sorted(allowlist)!r}"
        )

    files = []
    for relative_path in allowlist:
        path = root / relative_path
        files.append(
            {
                "path": relative_path,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )

    metadata = {
        "schema_version": METADATA_SCHEMA_VERSION,
        "product_id": product_id,
        "semantic_key": {
            "type": args.semantic_key_type,
            "value": args.semantic_key,
        },
        "source_event_sha": source_sha,
        "prepared_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "files": files,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Prepared validated candidate metadata for {product_id}: {output}")
    return 0


def validate_candidate_metadata(
    *,
    root: Path,
    metadata_path: Path,
    product_id: str,
    allowlist: Sequence[str],
    expected_source_sha: str,
) -> Dict[str, object]:
    metadata = _read_metadata(metadata_path)
    if set(metadata) != {
        "schema_version",
        "product_id",
        "semantic_key",
        "source_event_sha",
        "prepared_at_utc",
        "files",
    }:
        raise PublisherError("candidate metadata contains missing or unexpected fields")
    if metadata.get("schema_version") != METADATA_SCHEMA_VERSION:
        raise PublisherError("unsupported candidate metadata schema version")
    if metadata.get("product_id") != product_id:
        raise PublisherError("candidate product ID does not match publisher request")
    if str(metadata.get("source_event_sha", "")).lower() != expected_source_sha:
        raise PublisherError("candidate source event SHA does not match this workflow run")
    semantic_key = metadata.get("semantic_key")
    if not isinstance(semantic_key, dict):
        raise PublisherError("candidate semantic_key must be an object")
    if set(semantic_key) != {"type", "value"}:
        raise PublisherError("candidate semantic key has an invalid shape")
    if not isinstance(semantic_key.get("type"), str) or not semantic_key["type"]:
        raise PublisherError("candidate semantic key type is missing")
    if not isinstance(semantic_key.get("value"), str) or not semantic_key["value"]:
        raise PublisherError("candidate semantic key value is missing")
    _parse_utc_timestamp(metadata.get("prepared_at_utc"), "candidate prepared_at_utc")

    file_entries = metadata.get("files")
    if not isinstance(file_entries, list) or not file_entries:
        raise PublisherError("candidate file inventory must be a nonempty array")
    paths = [entry.get("path") if isinstance(entry, dict) else None for entry in file_entries]
    if paths != list(allowlist):
        raise PublisherError("candidate metadata allowlist does not match publisher allowlist")
    if _candidate_inventory(root) != sorted(allowlist):
        raise PublisherError("downloaded candidate inventory does not exactly match allowlist")

    for entry in file_entries:
        if not isinstance(entry, dict):
            raise PublisherError("candidate file inventory entry must be an object")
        if set(entry) != {"path", "bytes", "sha256"}:
            raise PublisherError("candidate file inventory entry has an invalid shape")
        relative_path = str(entry["path"])
        path = root / relative_path
        expected_bytes = entry.get("bytes")
        expected_hash = entry.get("sha256")
        if not isinstance(expected_bytes, int) or expected_bytes < 0:
            raise PublisherError(f"invalid candidate byte count for {relative_path}")
        if not isinstance(expected_hash, str) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_hash
        ):
            raise PublisherError(f"invalid candidate SHA-256 for {relative_path}")
        if path.stat().st_size != expected_bytes or _sha256(path) != expected_hash:
            raise PublisherError(f"candidate bytes do not match metadata: {relative_path}")

    if metadata_path.parent != root.parent or root.name != "candidate":
        raise PublisherError("candidate artifact must use the candidate/ plus metadata layout")
    expected_artifact_files = {
        "candidate-metadata.json",
        *{f"candidate/{path}" for path in allowlist},
    }
    artifact_files = set(_candidate_inventory(root.parent))
    if artifact_files != expected_artifact_files:
        raise PublisherError("candidate artifact contains unexpected or missing members")
    return metadata


def _verify_main_authorization(target_ref: str) -> str:
    if target_ref != MAIN_REF:
        raise PublisherError(f"publisher target must be {MAIN_REF}")
    required = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_REF": MAIN_REF,
        "GITHUB_REF_TYPE": "branch",
        "BRIM_LIVE_MAIN_PUBLISH": "true",
    }
    for name, expected in required.items():
        if os.environ.get(name) != expected:
            raise PublisherError(
                f"main publication is not authorized: {name} must equal {expected!r}"
            )
    return _validate_source_sha(os.environ.get("GITHUB_SHA", ""))


def _ensure_clean_checkout(repo: Path) -> None:
    status = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=repo
    ).stdout
    if status:
        raise PublisherError("publisher checkout/index must be clean before publication")


def _git_paths(repo: Path, command: Sequence[str]) -> Set[str]:
    output = _run(command, cwd=repo).stdout
    return {path for path in output.split("\0") if path}


def _unstaged_and_untracked_paths(repo: Path) -> Set[str]:
    paths = _git_paths(repo, ["git", "diff", "--name-only", "-z"])
    paths.update(
        _git_paths(
            repo,
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        )
    )
    return paths


def _staged_paths(repo: Path) -> Set[str]:
    return _git_paths(repo, ["git", "diff", "--cached", "--name-only", "-z"])


def _require_allowed(paths: Set[str], allowlist: Set[str], stage: str) -> None:
    unexpected = sorted(paths - allowlist)
    if unexpected:
        raise PublisherError(f"{stage} touched paths outside the allowlist: {unexpected}")


def _callback_command(path: Path) -> List[str]:
    if not path.is_file():
        raise PublisherError(f"publisher callback does not exist: {path}")
    if path.suffix == ".py":
        return [sys.executable, str(path)]
    return [str(path)]


def _run_callback(
    callback: Path,
    *,
    cwd: Path,
    environment: Mapping[str, str],
    label: str,
) -> None:
    result = _run(
        _callback_command(callback), cwd=cwd, env=environment, check=False
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode != 0:
        detail = result.stderr.strip()
        raise PublisherError(
            f"{label} callback failed ({result.returncode})"
            + (f": {detail}" if detail else "")
        )


def _is_non_fast_forward(result: subprocess.CompletedProcess[str]) -> bool:
    text = f"{result.stdout}\n{result.stderr}".lower()
    markers = (
        "non-fast-forward",
        "(fetch first)",
        "remote contains work that you do not have locally",
        "updates were rejected because the remote contains work",
    )
    return any(marker in text for marker in markers)


def _remove_transaction_worktree(repo: Path, worktree: Path, container: Path) -> None:
    if worktree.exists():
        _run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=repo,
            check=False,
        )
    shutil.rmtree(container, ignore_errors=True)


def _publish_attempt(
    *,
    repo: Path,
    candidate_root: Path,
    metadata_path: Path,
    product_id: str,
    allowlist: Sequence[str],
    source_sha: str,
    candidate_validator: Path,
    reconcile_callback: Path,
    staged_validator: Path,
    commit_subject: str,
    attempt: int,
) -> str:
    validate_candidate_metadata(
        root=candidate_root,
        metadata_path=metadata_path,
        product_id=product_id,
        allowlist=allowlist,
        expected_source_sha=source_sha,
    )

    _run(
        [
            "git",
            "fetch",
            "--no-tags",
            "origin",
            "refs/heads/main:refs/remotes/origin/main",
        ],
        cwd=repo,
    )
    container = Path(tempfile.mkdtemp(prefix=f"brim-main-publish-{product_id}-"))
    worktree = container / "worktree"
    result_path = container / "reconcile-result.json"
    try:
        _run(
            [
                "git",
                "worktree",
                "add",
                "--detach",
                str(worktree),
                "refs/remotes/origin/main",
            ],
            cwd=repo,
        )
        _run(["git", "config", "user.name", "github-actions[bot]"], cwd=worktree)
        _run(
            [
                "git",
                "config",
                "user.email",
                "41898282+github-actions[bot]@users.noreply.github.com",
            ],
            cwd=worktree,
        )

        callback_environment = dict(os.environ)
        callback_environment.update(
            {
                "BRIM_PUBLISH_ATTEMPT": str(attempt),
                "BRIM_PUBLISH_CANDIDATE_ROOT": str(candidate_root),
                "BRIM_PUBLISH_METADATA": str(metadata_path),
                "BRIM_PUBLISH_PRODUCT_ID": product_id,
                "BRIM_PUBLISH_RESULT": str(result_path),
                "BRIM_PUBLISH_WORKTREE": str(worktree),
            }
        )

        candidate_env = dict(callback_environment)
        candidate_env["BRIM_PUBLISH_PHASE"] = "validate-candidate"
        _run_callback(
            candidate_validator,
            cwd=repo,
            environment=candidate_env,
            label="candidate validation",
        )
        if _unstaged_and_untracked_paths(worktree) or _staged_paths(worktree):
            raise PublisherError("candidate validator modified the publication worktree")

        reconcile_env = dict(callback_environment)
        reconcile_env["BRIM_PUBLISH_PHASE"] = "reconcile"
        _run_callback(
            reconcile_callback,
            cwd=repo,
            environment=reconcile_env,
            label="product reconciliation",
        )
        if not result_path.is_file() or result_path.is_symlink():
            raise PublisherError("product reconciliation did not write a result object")
        try:
            reconciliation = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PublisherError(f"invalid product reconciliation result: {exc}") from exc
        if not isinstance(reconciliation, dict):
            raise PublisherError("product reconciliation result must be an object")
        decision = reconciliation.get("decision")
        candidate_state = reconciliation.get("candidate_state")
        if decision not in {"publish", "no-op"}:
            raise PublisherError("product reconciliation decision must be publish or no-op")
        if candidate_state not in {"new", "same", "stale"}:
            raise PublisherError("product reconciliation must classify new/same/stale")

        allowed = set(allowlist)
        changed = _unstaged_and_untracked_paths(worktree) | _staged_paths(worktree)
        _require_allowed(changed, allowed, "reconciliation")
        if decision == "no-op":
            if changed:
                raise PublisherError("no-op reconciliation modified product paths")
            print(
                f"Publisher no-op for {product_id}: candidate is {candidate_state}; "
                f"reason={reconciliation.get('reason', 'not supplied')}"
            )
            return "no-op"

        if candidate_state != "new":
            raise PublisherError("only a product-classified new candidate may be published")
        if not changed:
            print(f"Publisher no-op for {product_id}: candidate produced an empty diff")
            return "no-op"

        _run(["git", "add", "-A", "--", *allowlist], cwd=worktree)
        staged = _staged_paths(worktree)
        _require_allowed(staged, allowed, "staging")
        if _unstaged_and_untracked_paths(worktree):
            raise PublisherError("reconciliation left unstaged or untracked changes")
        if not staged:
            print(f"Publisher no-op for {product_id}: allowlisted staged diff is empty")
            return "no-op"

        validate_env = dict(callback_environment)
        validate_env["BRIM_PUBLISH_PHASE"] = "validate-staged"
        _run_callback(
            staged_validator,
            cwd=repo,
            environment=validate_env,
            label="staged product validation",
        )
        staged_after = _staged_paths(worktree)
        if staged_after != staged or _unstaged_and_untracked_paths(worktree):
            raise PublisherError("staged validator modified the publication transaction")
        _require_allowed(staged_after, allowed, "post-validation staging")

        _run(["git", "commit", "-m", commit_subject], cwd=worktree)
        push = _run(
            ["git", "push", "origin", "HEAD:refs/heads/main"],
            cwd=worktree,
            check=False,
        )
        if push.returncode == 0:
            print(f"Published {product_id} to main on attempt {attempt}.")
            return "published"
        if _is_non_fast_forward(push):
            detail = (push.stderr or push.stdout).strip()
            print(f"Non-fast-forward publication race on attempt {attempt}: {detail}")
            return "non-fast-forward"
        detail = (push.stderr or push.stdout).strip()
        raise PublisherError(
            "main publication push failed without a retryable non-fast-forward"
            + (f": {detail}" if detail else "")
        )
    finally:
        _remove_transaction_worktree(repo, worktree, container)


def publish(args: argparse.Namespace) -> int:
    source_sha = _verify_main_authorization(args.target_ref)
    product_id = _validate_product_id(args.product_id)
    allowlist = _normalize_allowlist(args.allowlist)
    if not args.commit_subject.strip() or "\n" in args.commit_subject:
        raise PublisherError("commit subject must be one nonempty line")

    repo = Path(args.repo).resolve()
    candidate_root = Path(args.candidate_root).resolve()
    metadata_path = Path(args.metadata).resolve()
    callbacks = [
        Path(args.candidate_validator).resolve(),
        Path(args.reconcile_callback).resolve(),
        Path(args.staged_validator).resolve(),
    ]
    if _run(["git", "rev-parse", "--show-toplevel"], cwd=repo).stdout.strip() != str(
        repo
    ):
        raise PublisherError("--repo must be the Git worktree root")
    if _run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip().lower() != source_sha:
        raise PublisherError("publisher checkout HEAD does not match the source event SHA")
    _ensure_clean_checkout(repo)
    for artifact_path in (candidate_root, metadata_path):
        try:
            artifact_path.relative_to(repo)
        except ValueError:
            pass
        else:
            raise PublisherError("candidate artifact must remain outside the Git worktree")
    for callback in callbacks:
        try:
            callback.relative_to(repo)
        except ValueError as exc:
            raise PublisherError(
                "publisher callbacks must be files in the source checkout"
            ) from exc
        if not callback.is_file() or callback.is_symlink():
            raise PublisherError("publisher callbacks must be plain tracked source files")

    for attempt in (1, 2):
        outcome = _publish_attempt(
            repo=repo,
            candidate_root=candidate_root,
            metadata_path=metadata_path,
            product_id=product_id,
            allowlist=allowlist,
            source_sha=source_sha,
            candidate_validator=callbacks[0],
            reconcile_callback=callbacks[1],
            staged_validator=callbacks[2],
            commit_subject=args.commit_subject,
            attempt=attempt,
        )
        if outcome in {"published", "no-op"}:
            return 0
        if attempt == 1:
            print("Fetching fresh origin/main and retrying reconciliation exactly once.")
            continue
        raise PublisherError(
            "main advanced during both publication attempts; refusing further retries"
        )
    raise PublisherError("publisher reached an impossible terminal state")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser(
        "prepare", help="validate a candidate and write integrity metadata"
    )
    prepare_parser.add_argument("--candidate-root", required=True)
    prepare_parser.add_argument("--output", required=True)
    prepare_parser.add_argument("--product-id", required=True)
    prepare_parser.add_argument("--semantic-key-type", required=True)
    prepare_parser.add_argument("--semantic-key", required=True)
    prepare_parser.add_argument("--source-event-sha", required=True)
    prepare_parser.add_argument("--allowlist", action="append", required=True)
    prepare_parser.set_defaults(handler=prepare_metadata)

    publish_parser = subparsers.add_parser(
        "publish", help="reconcile and non-force-push one candidate to current main"
    )
    publish_parser.add_argument("--repo", default=".")
    publish_parser.add_argument("--candidate-root", required=True)
    publish_parser.add_argument("--metadata", required=True)
    publish_parser.add_argument("--product-id", required=True)
    publish_parser.add_argument("--target-ref", default=MAIN_REF)
    publish_parser.add_argument("--commit-subject", required=True)
    publish_parser.add_argument("--allowlist", action="append", required=True)
    publish_parser.add_argument("--candidate-validator", required=True)
    publish_parser.add_argument("--reconcile-callback", required=True)
    publish_parser.add_argument("--staged-validator", required=True)
    publish_parser.set_defaults(handler=publish)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except PublisherError as exc:
        print(f"MAIN_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
