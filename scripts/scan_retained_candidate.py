#!/usr/bin/env python3
"""Import one pinned public SCAN snapshot; never execute its staging tree."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

import main_publisher
import scan_soil_moisture_publisher as scan

REPOSITORY = "dbo99/brim-live-data-feeds"
WORKFLOW = ".github/workflows/publish-scan-retained-c11.yml"
STAGING_SHA = "ded6d52503aefc7a02f176fb1a15bc1e39d1d87a"
EXPIRES = datetime(2026, 11, 12, 7, 59, 59, tzinfo=timezone.utc)
EXPECTED = {
    "docs/data/scan_soil_moisture_latest.geojson": "3e3acecdfdc012acd9ca9e1498985cbaaa60dbbd890a7b1f9924f74faffba409",
    "docs/data/scan_soil_moisture_latest_summary.json": "e777b9b8d9602be119f6266df9099ed76296058d6a6f7059d642d83b2312f4c8",
    "docs/data/scan_soil_moisture_current_wy_trace.csv": "44bad6563d20d1c63efc576ff9459d7e15460efc55a9cc6177a5e5cd1cee83a3",
    "docs/data/scan_soil_moisture_current_wy_trace_summary.json": "f3313496caca3ce69790dc1dbd24603d551db90b9340ffea9dabe4b4002b2c01",
    "docs/data/scan_depth_style.csv": "c884032a3fcc598a389007c5c7425d78b048a78f956e1113375fc3118f5ca0a0",
}
MAX_FILE = 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                            timeout=30, check=True)
    require(len(result.stdout) <= MAX_TOTAL, "Git response limit")
    return result.stdout


def admit(env: dict[str, str], checkout_sha: str, now: datetime) -> str:
    required = {
        "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_TYPE": "branch", "GITHUB_RUN_NUMBER": "1", "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_WORKFLOW_REF": REPOSITORY + "/" + WORKFLOW + "@refs/heads/main",
        "RETAINED_STAGING_SHA": STAGING_SHA,
    }
    require(all(env.get(k) == v for k, v in required.items()), "finite workflow admission")
    event = env.get("GITHUB_SHA", "")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", event)), "event SHA")
    require(event == checkout_sha == env.get("RETAINED_EVENT_SHA"), "trusted source binding")
    require(bool(re.fullmatch(r"[1-9][0-9]*", env.get("GITHUB_RUN_ID", ""))), "run identity")
    # Reserve the whole 15-minute job before starting; no deadline extension.
    require(now.tzinfo is not None and now + timedelta(minutes=15) <= EXPIRES, "expired publication grant")
    return event


def tree_entries(raw: bytes, *, exact: bool) -> dict[str, str]:
    entries = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        header, name = record.split(b"\t", 1)
        mode, kind, oid = header.decode("ascii").split()
        path = name.decode("utf-8")
        require(path in EXPECTED and path not in entries, "unexpected or duplicate tree path")
        require(mode == "100644" and kind == "blob" and bool(re.fullmatch(r"[0-9a-f]{40}", oid)),
                "not a regular data blob")
        entries[path] = oid
    require(set(entries) == set(EXPECTED), "incomplete five-file tree")
    return entries


def new_directory(path: Path) -> None:
    require(not path.exists() and not path.is_symlink(), "destination already exists")
    for parent in path.parents:
        require(not parent.is_symlink(), "linked destination ancestry")
    path.mkdir(mode=0o700, parents=True)


def export_blobs(repo: Path, commit: str, destination: Path, *, pinned: bool) -> None:
    require(bool(re.fullmatch(r"[0-9a-f]{40}", commit)), "commit identity")
    if pinned:
        require(commit == STAGING_SHA, "wrong staging commit")
        require(git(repo, "rev-list", "--parents", "-n", "1", commit).decode().split() == [commit],
                "staging must be a data-only root commit")
    args = ["ls-tree", "-rz", commit]
    if not pinned:
        args += ["--", *sorted(EXPECTED)]
    entries = tree_entries(git(repo, *args), exact=pinned)
    bodies = {}
    for path, oid in entries.items():
        size = int(git(repo, "cat-file", "-s", oid))
        require(0 < size <= MAX_FILE, "blob size limit")
        body = git(repo, "cat-file", "blob", oid)
        require(len(body) == size, "blob size disagreement")
        if pinned:
            require(hashlib.sha256(body).hexdigest() == EXPECTED[path], "candidate digest")
        bodies[path] = body
    require(sum(map(len, bodies.values())) <= MAX_TOTAL, "aggregate candidate limit")
    # Read and validate all identities before creating any output.
    new_directory(destination)
    for path, body in bodies.items():
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with target.open("xb") as handle:
            handle.write(body)


def verify_candidate(root: Path) -> tuple[str, int]:
    require(not root.is_symlink(), "linked candidate root")
    files = set()
    for path in root.rglob("*"):
        require(not path.is_symlink(), "linked artifact member")
        if path.is_file():
            require(stat.S_ISREG(path.stat().st_mode), "nonregular candidate")
            name = path.relative_to(root).as_posix()
            require(name in EXPECTED and path.stat().st_size <= MAX_FILE, "candidate inventory/size")
            require(hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED[name], "candidate digest")
            files.add(name)
        else:
            require(path.is_dir(), "nonregular artifact member")
    require(files == set(EXPECTED), "candidate inventory")
    return scan.validate_product(root)


def compatibility(candidate: Path, prior: Path) -> None:
    new_time, _ = scan.validate_product(candidate)
    old_time, _ = scan.validate_product(prior)
    require(new_time > old_time or (new_time == old_time and all(
        (candidate / n).read_bytes() == (prior / n).read_bytes() for n in EXPECTED)), "older or ambiguous candidate")
    def js(root, name):
        return json.loads((root / "docs/data" / name).read_text())
    new = js(candidate, "scan_soil_moisture_latest.geojson")
    old = js(prior, "scan_soil_moisture_latest.geojson")
    nb = {x["properties"]["station_uid"]: x for x in new["features"]}
    ob = {x["properties"]["station_uid"]: x for x in old["features"]}
    require(set(nb) == set(ob), "station membership changed")
    for uid, feature in nb.items():
        require(feature["geometry"] == ob[uid]["geometry"], "station geometry changed")
        require(set(feature["properties"]) == set(ob[uid]["properties"]), "feature fields changed")
        for key in ("site_code", "station_name", "station_triplet", "current_water_year", "current_wy_start_date"):
            require(feature["properties"][key] == ob[uid]["properties"][key], "station/time identity changed")
    require((candidate / scan.STYLE_PATH).read_bytes() == (prior / scan.STYLE_PATH).read_bytes(), "depth style changed")
    with (candidate / scan.TRACE_PATH).open() as a, (prior / scan.TRACE_PATH).open() as b:
        require(next(csv.reader(a)) == next(csv.reader(b)), "trace fields changed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["admit", "prepare", "verify-artifact"])
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--prior-sha")
    args = parser.parse_args()
    repo = Path.cwd().resolve()
    event = admit(dict(os.environ), git(repo, "rev-parse", "HEAD").decode().strip(), datetime.now(timezone.utc))
    if args.command == "admit":
        print("Finite retained-snapshot admission passed.")
        return
    require(args.artifact is not None, "artifact destination required")
    artifact = args.artifact.absolute()
    if args.command == "prepare":
        require(args.prior_sha is not None, "fresh main identity required")
        new_directory(artifact)
        export_blobs(repo, STAGING_SHA, artifact / "candidate", pinned=True)
        prior = artifact.with_name(artifact.name + "-prior")
        export_blobs(repo, args.prior_sha, prior, pinned=False)
        build, _ = verify_candidate(artifact / "candidate")
        compatibility(artifact / "candidate", prior)
        main_publisher.prepare_metadata(argparse.Namespace(
            candidate_root=str(artifact / "candidate"), output=str(artifact / "candidate-metadata.json"),
            product_id=scan.PRODUCT_ID, semantic_key_type="feed_build_time_utc", semantic_key=build,
            source_event_sha=event, allowlist=sorted(EXPECTED), owned_root=None))
    else:
        require(not artifact.is_symlink() and set(x.name for x in artifact.iterdir()) == {"candidate", "candidate-metadata.json"}, "artifact inventory")
        verify_candidate(artifact / "candidate")
        metadata = artifact / "candidate-metadata.json"
        require(metadata.is_file() and not metadata.is_symlink() and metadata.stat().st_size < 16384, "metadata file")
        main_publisher.validate_candidate_metadata(
            root=artifact / "candidate", metadata_path=metadata,
            product_id=scan.PRODUCT_ID, allowlist=sorted(EXPECTED), expected_source_sha=event)
    print("Verified the pinned five-file public snapshot; no acquisition performed.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError, scan.ProductError, main_publisher.PublisherError):
        # Do not echo arbitrary file contents, Git stderr, paths or environment.
        raise SystemExit("Retained snapshot refused; no publication authorized.")
