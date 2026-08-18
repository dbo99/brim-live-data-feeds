#!/usr/bin/env python3
"""Offline integration tests for bounded rolling-tree publication ownership."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE_REPO = Path(__file__).resolve().parents[1]
PUBLISHER = SOURCE_REPO / "scripts" / "main_publisher.py"
FIXED = Path("docs/data/rolling/manifest.json")
OWNED_ROOT = Path("docs/data/rolling/targets")


CALLBACK_SOURCE = r'''#!/usr/bin/env python3
import json
import os
import shutil
import sys
from pathlib import Path

phase = os.environ["BRIM_PUBLISH_PHASE"]
candidate = Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"])
worktree = Path(os.environ["BRIM_PUBLISH_WORKTREE"])
result = Path(os.environ["BRIM_PUBLISH_RESULT"])
fixed = Path("docs/data/rolling/manifest.json")
owned = Path("docs/data/rolling/targets")

def load(root):
    manifest_path = root / fixed
    if not manifest_path.is_file():
        raise RuntimeError("missing rolling manifest")
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(value.get("version"), int) or not isinstance(value.get("files"), list):
        raise RuntimeError("malformed rolling manifest")
    actual = sorted(
        path.relative_to(root / owned).as_posix()
        for path in (root / owned).rglob("*")
        if path.is_file()
    ) if (root / owned).is_dir() else []
    if actual != value["files"]:
        raise RuntimeError("rolling manifest-target closure failure")
    return value

if phase == "validate-candidate":
    load(candidate)
elif phase == "validate-staged":
    load(worktree)
    if os.environ.get("TEST_TOUCH_DURING_STAGED") == "true":
        (worktree / owned / "staged.txt").write_text("unexpected\n", encoding="utf-8")
elif phase == "reconcile":
    proposed = load(candidate)
    current_path = worktree / fixed
    if current_path.exists():
        current = load(worktree)
        if proposed["version"] < current["version"]:
            result.write_text(json.dumps({"decision":"no-op","candidate_state":"stale","reason":"older"}))
            sys.exit(0)
        if proposed["version"] == current["version"]:
            same = (candidate / fixed).read_bytes() == current_path.read_bytes()
            for name in proposed["files"]:
                same = same and (candidate / owned / name).read_bytes() == (worktree / owned / name).read_bytes()
            if not same:
                raise RuntimeError("same rolling version differs")
            result.write_text(json.dumps({"decision":"no-op","candidate_state":"same","reason":"identical"}))
            sys.exit(0)
    (worktree / fixed).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(candidate / fixed, worktree / fixed)
    shutil.rmtree(worktree / owned, ignore_errors=True)
    if (candidate / owned).exists():
        shutil.copytree(candidate / owned, worktree / owned)
    if os.environ.get("TEST_UNDECLARED_ADD") == "true":
        sibling = worktree / "docs/data/rolling/sibling.json"
        sibling.write_text("unexpected\n", encoding="utf-8")
    if os.environ.get("TEST_UNDECLARED_DELETE") == "true":
        (worktree / "docs/data/other/keep.json").unlink()
    result.write_text(json.dumps({"decision":"publish","candidate_state":"new","reason":"newer"}))
else:
    raise RuntimeError("unknown callback phase")
'''


RACE_WRAPPER_SOURCE = r'''#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

git = os.environ["REAL_GIT"]
args = sys.argv[1:]
if args and args[0] == "push" and "HEAD:refs/heads/main" in args:
    count_path = Path(os.environ["RACE_COUNT"])
    count = int(count_path.read_text() if count_path.exists() else "0")
    if count < int(os.environ["RACE_LIMIT"]):
        count += 1
        count_path.write_text(str(count), encoding="utf-8")
        racer = Path(os.environ["RACE_CLONE"])
        subprocess.run([git, "-C", str(racer), "fetch", "origin", "main"], check=True)
        subprocess.run([git, "-C", str(racer), "checkout", "-B", "main", "origin/main"], check=True)
        if os.environ.get("RACE_ADVANCE_PRODUCT") == "true":
            target_root = racer / "docs/data/rolling/targets"
            shutil.rmtree(target_root, ignore_errors=True)
            target = target_root / "fresh.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("fresh canonical\n", encoding="utf-8")
            manifest = racer / "docs/data/rolling/manifest.json"
            manifest.write_text(json.dumps({"version": 3, "files": ["fresh.txt"]}, sort_keys=True) + "\n")
            paths = ["docs/data/rolling/manifest.json", "docs/data/rolling/targets"]
        else:
            race_file = racer / f"race-{count}.txt"
            race_file.write_text(f"race {count}\n", encoding="utf-8")
            paths = [race_file.name]
        subprocess.run([git, "-C", str(racer), "add", *paths], check=True)
        subprocess.run([git, "-C", str(racer), "commit", "-m", f"Race {count}"], check=True)
        subprocess.run([git, "-C", str(racer), "push", "origin", "HEAD:refs/heads/main"], check=True)
os.execv(git, [git, *args])
'''


def run(command, *, cwd: Path, env=None, check=True):
    result = subprocess.run(
        list(command), cwd=cwd, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"command failed: {command}\n{result.stdout}\n{result.stderr}")
    return result


class OwnedRootPublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        self.runner = self.root / "runner"
        self.artifact = self.root / "artifact"
        self.candidate = self.artifact / "candidate"
        self.metadata = self.artifact / "candidate-metadata.json"
        run(["git", "init", "--bare", str(self.remote)], cwd=self.root)
        run(["git", "init", str(self.seed)], cwd=self.root)
        run(["git", "config", "user.name", "Test Author"], cwd=self.seed)
        run(["git", "config", "user.email", "test@example.invalid"], cwd=self.seed)
        self.write_state(self.seed, 1, {"keep.txt": "one\n", "old.txt": "old\n"})
        other = self.seed / "docs/data/other/keep.json"
        other.parent.mkdir(parents=True)
        other.write_text("preserve\n", encoding="utf-8")
        callback = self.seed / "scripts/callback.py"
        callback.parent.mkdir(parents=True)
        callback.write_text(CALLBACK_SOURCE, encoding="utf-8")
        run(["git", "add", "."], cwd=self.seed)
        run(["git", "commit", "-m", "Initial main"], cwd=self.seed)
        run(["git", "branch", "-M", "main"], cwd=self.seed)
        run(["git", "remote", "add", "origin", str(self.remote)], cwd=self.seed)
        run(["git", "push", "-u", "origin", "main"], cwd=self.seed)
        run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=self.remote)
        run(["git", "clone", str(self.remote), str(self.runner)], cwd=self.root)
        self.source_sha = run(["git", "rev-parse", "HEAD"], cwd=self.runner).stdout.strip()
        self.callback = self.runner / "scripts/callback.py"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def write_state(root: Path, version: int, targets: dict[str, str]) -> None:
        manifest = root / FIXED
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps({"version": version, "files": sorted(targets)}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        target_root = root / OWNED_ROOT
        shutil.rmtree(target_root, ignore_errors=True)
        for name, content in targets.items():
            path = target_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def prepare(self, version=2, targets=None, *, owned_root=OWNED_ROOT.as_posix()):
        self.write_state(self.candidate, version, targets or {"keep.txt": "one\n", "new.txt": "new\n"})
        return run([
            sys.executable, str(PUBLISHER), "prepare",
            "--candidate-root", str(self.candidate),
            "--output", str(self.metadata),
            "--product-id", "rolling-test",
            "--semantic-key-type", "version",
            "--semantic-key", str(version),
            "--source-event-sha", self.source_sha,
            "--allowlist", FIXED.as_posix(),
            "--owned-root", owned_root,
        ], cwd=SOURCE_REPO, check=False)

    def environment(self, **extra):
        return {
            **os.environ,
            "GITHUB_ACTIONS": "true",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_REF_TYPE": "branch",
            "GITHUB_SHA": self.source_sha,
            "BRIM_LIVE_MAIN_PUBLISH": "true",
            **extra,
        }

    def publish(self, *, env=None, owned_root=OWNED_ROOT.as_posix()):
        return run([
            sys.executable, str(PUBLISHER), "publish",
            "--repo", str(self.runner),
            "--candidate-root", str(self.candidate),
            "--metadata", str(self.metadata),
            "--product-id", "rolling-test",
            "--commit-subject", "Update rolling test",
            "--allowlist", FIXED.as_posix(),
            "--owned-root", owned_root,
            "--candidate-validator", str(self.callback),
            "--reconcile-callback", str(self.callback),
            "--staged-validator", str(self.callback),
        ], cwd=SOURCE_REPO, env=env or self.environment(), check=False)

    def remote_file(self, path: str) -> bytes:
        return subprocess.run(
            ["git", "--git-dir", str(self.remote), "show", f"main:{path}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        ).stdout

    def remote_paths(self) -> set[str]:
        output = run(
            ["git", "--git-dir", str(self.remote), "ls-tree", "-r", "--name-only", "main"],
            cwd=self.root,
        ).stdout
        return set(output.splitlines())

    def install_race_wrapper(self, limit: int, *, advance_product=False):
        racer = self.root / "racer"
        run(["git", "clone", str(self.remote), str(racer)], cwd=self.root)
        run(["git", "config", "user.name", "Race Author"], cwd=racer)
        run(["git", "config", "user.email", "race@example.invalid"], cwd=racer)
        wrapper_dir = self.root / "bin"
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / "git"
        wrapper.write_text(RACE_WRAPPER_SOURCE, encoding="utf-8")
        wrapper.chmod(0o755)
        return self.environment(
            PATH=f"{wrapper_dir}{os.pathsep}{os.environ['PATH']}",
            REAL_GIT=shutil.which("git"),
            RACE_CLONE=str(racer), RACE_LIMIT=str(limit),
            RACE_COUNT=str(self.root / "race-count"),
            RACE_ADVANCE_PRODUCT=str(advance_product).lower(),
        )

    def test_01_fixed_file_add_or_update(self):
        self.assertEqual(self.prepare().returncode, 0)
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.remote_file(FIXED.as_posix()))["version"], 2)

    def test_02_owned_root_target_add(self):
        self.prepare(targets={"keep.txt": "one\n", "added.txt": "added\n"})
        self.assertEqual(self.publish().returncode, 0)
        self.assertEqual(self.remote_file(f"{OWNED_ROOT}/added.txt"), b"added\n")

    def test_03_owned_root_target_update(self):
        self.prepare(targets={"keep.txt": "updated\n"})
        self.assertEqual(self.publish().returncode, 0)
        self.assertEqual(self.remote_file(f"{OWNED_ROOT}/keep.txt"), b"updated\n")

    def test_04_legitimate_target_deletion(self):
        self.prepare(targets={"keep.txt": "one\n"})
        self.assertEqual(self.publish().returncode, 0)
        self.assertNotIn(f"{OWNED_ROOT}/old.txt", self.remote_paths())

    def test_05_multiple_deletions(self):
        self.write_state(
            self.runner,
            1,
            {"keep.txt": "one\n", "old.txt": "old\n", "extra.txt": "extra\n"},
        )
        run(["git", "add", FIXED.as_posix(), OWNED_ROOT.as_posix()], cwd=self.runner)
        run(["git", "commit", "-m", "add extra"], cwd=self.runner)
        run(["git", "push", "origin", "main"], cwd=self.runner)
        self.source_sha = run(["git", "rev-parse", "HEAD"], cwd=self.runner).stdout.strip()
        self.prepare(targets={"keep.txt": "one\n"})
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        paths = self.remote_paths()
        self.assertNotIn(f"{OWNED_ROOT}/old.txt", paths)
        self.assertNotIn(f"{OWNED_ROOT}/extra.txt", paths)

    def test_06_retained_target(self):
        before = self.remote_file(f"{OWNED_ROOT}/keep.txt")
        self.prepare(targets={"keep.txt": "one\n"})
        self.assertEqual(self.publish().returncode, 0)
        self.assertEqual(self.remote_file(f"{OWNED_ROOT}/keep.txt"), before)

    def test_07_undeclared_target_addition_rejected(self):
        self.write_state(self.candidate, 2, {"keep.txt": "one\n"})
        sibling = self.candidate / "docs/data/rolling/sibling/new.txt"
        sibling.parent.mkdir(parents=True)
        sibling.write_text("bad\n", encoding="utf-8")
        result = run([
            sys.executable, str(PUBLISHER), "prepare", "--candidate-root", str(self.candidate),
            "--output", str(self.metadata), "--product-id", "rolling-test",
            "--semantic-key-type", "version", "--semantic-key", "2",
            "--source-event-sha", self.source_sha, "--allowlist", FIXED.as_posix(),
            "--owned-root", OWNED_ROOT.as_posix(),
        ], cwd=SOURCE_REPO, check=False)
        self.assertNotEqual(result.returncode, 0)

    def test_08_undeclared_deletion_rejected(self):
        self.prepare()
        result = self.publish(env=self.environment(TEST_UNDECLARED_DELETE="true"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the allowlist", result.stderr)

    def test_09_path_traversal_rejected(self):
        result = self.prepare(owned_root="docs/data/rolling/targets/../other")
        self.assertNotEqual(result.returncode, 0)

    def test_10_absolute_path_rejected(self):
        result = self.prepare(owned_root="/docs/data/rolling/targets")
        self.assertNotEqual(result.returncode, 0)

    def test_11_symlink_escape_rejected(self):
        self.write_state(self.candidate, 2, {"keep.txt": "one\n"})
        link = self.candidate / OWNED_ROOT / "escape"
        link.symlink_to("/tmp")
        result = run([
            sys.executable, str(PUBLISHER), "prepare", "--candidate-root", str(self.candidate),
            "--output", str(self.metadata), "--product-id", "rolling-test",
            "--semantic-key-type", "version", "--semantic-key", "2",
            "--source-event-sha", self.source_sha, "--allowlist", FIXED.as_posix(),
            "--owned-root", OWNED_ROOT.as_posix(),
        ], cwd=SOURCE_REPO, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr)

    def test_12_sibling_product_path_rejected(self):
        self.write_state(self.candidate, 2, {"keep.txt": "one\n"})
        sibling = self.candidate / "docs/data/other/sibling.txt"
        sibling.parent.mkdir(parents=True, exist_ok=True)
        sibling.write_text("bad\n", encoding="utf-8")
        result = run([
            sys.executable, str(PUBLISHER), "prepare", "--candidate-root", str(self.candidate),
            "--output", str(self.metadata), "--product-id", "rolling-test",
            "--semantic-key-type", "version", "--semantic-key", "2",
            "--source-event-sha", self.source_sha, "--allowlist", FIXED.as_posix(),
            "--owned-root", OWNED_ROOT.as_posix(),
        ], cwd=SOURCE_REPO, check=False)
        self.assertNotEqual(result.returncode, 0)

    def test_13_broad_docs_data_root_rejected(self):
        result = self.prepare(owned_root="docs/data/wind")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("too broad", result.stderr)

    def test_14_artifact_metadata_cannot_expand_ownership(self):
        self.prepare()
        metadata = json.loads(self.metadata.read_text(encoding="utf-8"))
        metadata["owned_roots"] = ["docs/data/rolling/targets", "docs/data/other/product"]
        self.metadata.write_text(json.dumps(metadata), encoding="utf-8")
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("do not match static", result.stderr)

    def test_15_staged_unexpected_path_rejected(self):
        self.prepare()
        result = self.publish(env=self.environment(TEST_UNDECLARED_ADD="true"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the allowlist", result.stderr)

    def test_16_fresh_main_advancement_reconciles_to_stale(self):
        self.prepare()
        result = self.publish(env=self.install_race_wrapper(1, advance_product=True))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("candidate is stale", result.stdout)
        self.assertEqual(json.loads(self.remote_file(FIXED.as_posix()))["version"], 3)

    def test_17_one_bounded_retry(self):
        self.prepare()
        result = self.publish(env=self.install_race_wrapper(1))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("retrying reconciliation exactly once", result.stdout)

    def test_18_unresolved_second_race_fails_without_force(self):
        self.prepare()
        result = self.publish(env=self.install_race_wrapper(2))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing further retries", result.stderr)


if __name__ == "__main__":
    unittest.main()
