#!/usr/bin/env python3
"""Direct offline integration tests for the shared main publisher."""

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
PRODUCT_PATH = Path("docs/data/product.json")


CALLBACK_SOURCE = r'''#!/usr/bin/env python3
import json
import os
import shutil
import sys
from pathlib import Path

phase = os.environ["BRIM_PUBLISH_PHASE"]
candidate_root = Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"])
worktree = Path(os.environ["BRIM_PUBLISH_WORKTREE"])
metadata_path = Path(os.environ["BRIM_PUBLISH_METADATA"])
result_path = Path(os.environ["BRIM_PUBLISH_RESULT"])
relative = Path("docs/data/product.json")

def load(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("version"), int):
        raise RuntimeError("invalid test product")
    if value.get("invalid"):
        raise RuntimeError("test product marked invalid")
    return value

if phase == "validate-candidate":
    load(candidate_root / relative)
elif phase == "validate-staged":
    if os.environ.get("TEST_FAIL_STAGED") == "true":
        raise RuntimeError("deterministic staged validation failure")
    load(worktree / relative)
elif phase == "reconcile":
    candidate_file = candidate_root / relative
    candidate = load(candidate_file)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    semantic = metadata["semantic_key"]
    if semantic != {"type": "version", "value": str(candidate["version"])}:
        raise RuntimeError("candidate semantic version mismatch")
    canonical_file = worktree / relative
    if not canonical_file.exists():
        state = "new"
    else:
        canonical = load(canonical_file)
        if candidate["version"] < canonical["version"]:
            result_path.write_text(json.dumps({"decision":"no-op","candidate_state":"stale","reason":"older"}))
            sys.exit(0)
        if candidate["version"] == canonical["version"]:
            if candidate_file.read_bytes() != canonical_file.read_bytes():
                raise RuntimeError("same version byte conflict")
            result_path.write_text(json.dumps({"decision":"no-op","candidate_state":"same","reason":"identical"}))
            sys.exit(0)
        state = "new"
    canonical_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(candidate_file, canonical_file)
    if os.environ.get("TEST_TOUCH_UNEXPECTED") == "true":
        (worktree / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    result_path.write_text(json.dumps({"decision":"publish","candidate_state":state,"reason":"newer"}))
else:
    raise RuntimeError("unknown callback phase")
'''


RACE_WRAPPER_SOURCE = r'''#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path

real_git = os.environ["REAL_GIT"]
args = sys.argv[1:]
if args and args[0] == "push" and "HEAD:refs/heads/main" in args:
    log_path = Path(os.environ["RACE_PUSH_LOG"])
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(" ".join(args) + "\n")
    count_path = Path(os.environ["RACE_COUNT"])
    count = int(count_path.read_text() if count_path.exists() else "0")
    limit = int(os.environ["RACE_LIMIT"])
    if count < limit:
        count += 1
        count_path.write_text(str(count), encoding="utf-8")
        racer = Path(os.environ["RACE_CLONE"])
        subprocess.run([real_git, "-C", str(racer), "fetch", "origin", "main"], check=True)
        subprocess.run([real_git, "-C", str(racer), "checkout", "-B", "main", "origin/main"], check=True)
        race_product_version = os.environ.get("RACE_PRODUCT_VERSION")
        if race_product_version:
            race_file = racer / "docs/data/product.json"
            race_file.write_text('{"version": ' + race_product_version + '}\n', encoding="utf-8")
            race_relative = "docs/data/product.json"
        else:
            race_file = racer / f"race-{count}.txt"
            race_file.write_text(f"race {count}\n", encoding="utf-8")
            race_relative = race_file.name
        subprocess.run([real_git, "-C", str(racer), "add", race_relative], check=True)
        subprocess.run([real_git, "-C", str(racer), "commit", "-m", f"Race {count}"], check=True)
        subprocess.run([real_git, "-C", str(racer), "push", "origin", "HEAD:refs/heads/main"], check=True)
os.execv(real_git, [real_git, *args])
'''


def run(command, *, cwd: Path, env=None, check=True):
    result = subprocess.run(
        list(command),
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"command failed: {command}\n{result.stdout}\n{result.stderr}")
    return result


class MainPublisherTests(unittest.TestCase):
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
        self.write_product(self.seed, 1)
        callback = self.seed / "scripts" / "test_callback.py"
        callback.parent.mkdir(parents=True)
        callback.write_text(CALLBACK_SOURCE, encoding="utf-8")
        (self.seed / "unrelated.txt").write_text("preserve me\n", encoding="utf-8")
        run(["git", "add", "."], cwd=self.seed)
        run(["git", "commit", "-m", "Initial main"], cwd=self.seed)
        run(["git", "branch", "-M", "main"], cwd=self.seed)
        run(["git", "remote", "add", "origin", str(self.remote)], cwd=self.seed)
        run(["git", "push", "-u", "origin", "main"], cwd=self.seed)
        run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=self.remote)
        run(["git", "clone", str(self.remote), str(self.runner)], cwd=self.root)
        self.source_sha = run(["git", "rev-parse", "HEAD"], cwd=self.runner).stdout.strip()
        self.callback = self.runner / "scripts" / "test_callback.py"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def write_product(root: Path, version: int, **extra) -> None:
        path = root / PRODUCT_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        value = {"version": version, **extra}
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")

    def prepare(self, version: int, **extra) -> None:
        self.write_product(self.candidate, version, **extra)
        run(
            [
                sys.executable,
                str(PUBLISHER),
                "prepare",
                "--candidate-root",
                str(self.candidate),
                "--output",
                str(self.metadata),
                "--product-id",
                "test-product",
                "--semantic-key-type",
                "version",
                "--semantic-key",
                str(version),
                "--source-event-sha",
                self.source_sha,
                "--allowlist",
                PRODUCT_PATH.as_posix(),
            ],
            cwd=SOURCE_REPO,
        )

    def publisher_environment(self, **extra) -> dict:
        return {
            **os.environ,
            "GITHUB_ACTIONS": "true",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_REF_TYPE": "branch",
            "GITHUB_SHA": self.source_sha,
            "BRIM_LIVE_MAIN_PUBLISH": "true",
            **extra,
        }

    def publish(self, *, env=None) -> subprocess.CompletedProcess[str]:
        return run(
            [
                sys.executable,
                str(PUBLISHER),
                "publish",
                "--repo",
                str(self.runner),
                "--candidate-root",
                str(self.candidate),
                "--metadata",
                str(self.metadata),
                "--product-id",
                "test-product",
                "--commit-subject",
                "Update test product",
                "--allowlist",
                PRODUCT_PATH.as_posix(),
                "--candidate-validator",
                str(self.callback),
                "--reconcile-callback",
                str(self.callback),
                "--staged-validator",
                str(self.callback),
            ],
            cwd=SOURCE_REPO,
            env=env or self.publisher_environment(),
            check=False,
        )

    def remote_file(self, relative_path: str) -> bytes:
        result = subprocess.run(
            ["git", "--git-dir", str(self.remote), "show", f"main:{relative_path}"],
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return result.stdout

    def remote_head(self) -> str:
        return run(
            ["git", "--git-dir", str(self.remote), "rev-parse", "main"], cwd=self.root
        ).stdout.strip()

    def test_new_candidate_publishes_and_preserves_unrelated_main(self) -> None:
        self.prepare(2)
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.remote_file(PRODUCT_PATH.as_posix()))["version"], 2)
        self.assertEqual(self.remote_file("unrelated.txt"), b"preserve me\n")
        subject = run(
            ["git", "--git-dir", str(self.remote), "log", "-1", "--format=%s", "main"],
            cwd=self.root,
        ).stdout.strip()
        self.assertEqual(subject, "Update test product")

    def test_prepared_metadata_carries_exact_integrity_and_provenance(self) -> None:
        self.prepare(2)
        metadata = json.loads(self.metadata.read_text(encoding="utf-8"))
        self.assertEqual(metadata["schema_version"], 1)
        self.assertEqual(metadata["product_id"], "test-product")
        self.assertEqual(metadata["semantic_key"], {"type": "version", "value": "2"})
        self.assertEqual(metadata["source_event_sha"], self.source_sha)
        self.assertRegex(metadata["prepared_at_utc"], r"^\d{4}-\d{2}-\d{2}T.*Z$")
        self.assertEqual([entry["path"] for entry in metadata["files"]], [PRODUCT_PATH.as_posix()])
        self.assertEqual(metadata["files"][0]["bytes"], (self.candidate / PRODUCT_PATH).stat().st_size)
        self.assertRegex(metadata["files"][0]["sha256"], r"^[0-9a-f]{64}$")

    def test_same_and_stale_candidates_are_noops(self) -> None:
        initial = self.remote_head()
        self.prepare(1)
        same = self.publish()
        self.assertEqual(same.returncode, 0, same.stderr)
        self.assertIn("candidate is same", same.stdout)
        self.assertEqual(self.remote_head(), initial)

        shutil.rmtree(self.artifact)
        self.prepare(0)
        stale = self.publish()
        self.assertEqual(stale.returncode, 0, stale.stderr)
        self.assertIn("candidate is stale", stale.stdout)
        self.assertEqual(self.remote_head(), initial)

    def test_authorization_and_artifact_tamper_fail_safe(self) -> None:
        self.prepare(2)
        unauthorized = self.publish(
            env=self.publisher_environment(GITHUB_REF="refs/heads/feature/test")
        )
        self.assertNotEqual(unauthorized.returncode, 0)
        self.assertIn("not authorized", unauthorized.stderr)

        (self.candidate / PRODUCT_PATH).write_text('{"version":99}\n', encoding="utf-8")
        tampered = self.publish()
        self.assertNotEqual(tampered.returncode, 0)
        self.assertIn("do not match metadata", tampered.stderr)

    def test_unexpected_artifact_member_and_dirty_checkout_fail_safe(self) -> None:
        self.prepare(2)
        (self.artifact / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
        unexpected = self.publish()
        self.assertNotEqual(unexpected.returncode, 0)
        self.assertIn("unexpected or missing members", unexpected.stderr)

        (self.artifact / "unexpected.txt").unlink()
        (self.runner / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        dirty = self.publish()
        self.assertNotEqual(dirty.returncode, 0)
        self.assertIn("checkout/index must be clean", dirty.stderr)

    def test_allowlist_and_staged_validation_failures_preserve_lkg(self) -> None:
        self.prepare(2)
        initial = self.remote_head()
        unexpected = self.publish(env=self.publisher_environment(TEST_TOUCH_UNEXPECTED="true"))
        self.assertNotEqual(unexpected.returncode, 0)
        self.assertIn("outside the allowlist", unexpected.stderr)
        self.assertEqual(self.remote_head(), initial)

        staged_failure = self.publish(env=self.publisher_environment(TEST_FAIL_STAGED="true"))
        self.assertNotEqual(staged_failure.returncode, 0)
        self.assertIn("staged product validation", staged_failure.stderr)
        self.assertEqual(self.remote_head(), initial)

    def test_ambiguous_reconciliation_failure_preserves_lkg(self) -> None:
        self.prepare(1, revision="different bytes at same version")
        initial = self.remote_head()
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("same version byte conflict", result.stderr)
        self.assertEqual(self.remote_head(), initial)

    def install_race_wrapper(self, limit: int, *, product_version: int | None = None) -> dict:
        racer = self.root / "racer"
        run(["git", "clone", str(self.remote), str(racer)], cwd=self.root)
        run(["git", "config", "user.name", "Race Author"], cwd=racer)
        run(["git", "config", "user.email", "race@example.invalid"], cwd=racer)
        wrapper_dir = self.root / "wrapper-bin"
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / "git"
        wrapper.write_text(RACE_WRAPPER_SOURCE, encoding="utf-8")
        wrapper.chmod(0o755)
        real_git = shutil.which("git")
        assert real_git is not None
        extra = {}
        if product_version is not None:
            extra["RACE_PRODUCT_VERSION"] = str(product_version)
        return self.publisher_environment(
            PATH=f"{wrapper_dir}{os.pathsep}{os.environ['PATH']}",
            REAL_GIT=real_git,
            RACE_CLONE=str(racer),
            RACE_LIMIT=str(limit),
            RACE_COUNT=str(self.root / "race-count"),
            RACE_PUSH_LOG=str(self.root / "race-push.log"),
            **extra,
        )

    def test_one_non_fast_forward_reconciles_against_fresh_main(self) -> None:
        self.prepare(2)
        result = self.publish(env=self.install_race_wrapper(1))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("retrying reconciliation exactly once", result.stdout)
        self.assertEqual(json.loads(self.remote_file(PRODUCT_PATH.as_posix()))["version"], 2)
        self.assertEqual(self.remote_file("race-1.txt"), b"race 1\n")

    def test_one_non_fast_forward_can_reconcile_to_stale_noop(self) -> None:
        self.prepare(2)
        result = self.publish(env=self.install_race_wrapper(1, product_version=3))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("retrying reconciliation exactly once", result.stdout)
        self.assertIn("candidate is stale", result.stdout)
        self.assertEqual(json.loads(self.remote_file(PRODUCT_PATH.as_posix()))["version"], 3)

    def test_second_non_fast_forward_fails_without_force(self) -> None:
        self.prepare(2)
        initial_product = self.remote_file(PRODUCT_PATH.as_posix())
        result = self.publish(env=self.install_race_wrapper(2))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing further retries", result.stderr)
        self.assertEqual(self.remote_file(PRODUCT_PATH.as_posix()), initial_product)
        push_log = (self.root / "race-push.log").read_text(encoding="utf-8")
        self.assertEqual(len(push_log.strip().splitlines()), 2)
        self.assertNotIn("--force", push_log)
        self.assertNotIn(" -f", push_log)


if __name__ == "__main__":
    unittest.main()
