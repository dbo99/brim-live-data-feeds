import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "scripts" / "configure_apt_mirror_order.sh"

CURRENT_GITHUB_LAYOUT = b"""\
http://azure.archive.ubuntu.com/ubuntu/ priority:1
http://archive.ubuntu.com/ubuntu/ priority:2
http://security.ubuntu.com/ubuntu/ priority:3
"""

CANONICAL_FIRST_LAYOUT = b"""\
https://archive.ubuntu.com/ubuntu/ priority:1
http://azure.archive.ubuntu.com/ubuntu/ priority:2
https://security.ubuntu.com/ubuntu/ priority:3
"""


class AptMirrorHardeningTests(unittest.TestCase):
    def run_helper(
        self,
        temp_root: Path,
        mirror_file: Path,
        *,
        platform: str = "Linux",
        os_id: str = "ubuntu",
    ) -> subprocess.CompletedProcess[str]:
        os_release = temp_root / "os-release"
        os_release.write_text(f'NAME="Test OS"\nID={os_id}\n', encoding="utf-8")
        env = os.environ.copy()
        env.update(
            {
                "BRIM_APT_MIRRORS_FILE": str(mirror_file),
                "BRIM_APT_OS_RELEASE_FILE": str(os_release),
                "BRIM_APT_PLATFORM_OVERRIDE": platform,
                "TMPDIR": str(temp_root),
            }
        )
        return subprocess.run(
            ["bash", str(HELPER)],
            cwd=REPO,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

    def assert_success(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def active_hosts_by_priority(self, content: bytes) -> dict[int, str]:
        entries: dict[int, str] = {}
        pattern = re.compile(
            rb"^https?://([^/]+)/ubuntu/\s+priority:([123])(?:\s|$)"
        )
        for line in content.splitlines():
            match = pattern.match(line.strip())
            if match:
                entries[int(match.group(2))] = match.group(1).decode("ascii")
        return entries

    def test_expected_github_layout_becomes_canonical_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            mirror_file = temp_root / "apt-mirrors.txt"
            mirror_file.write_bytes(CURRENT_GITHUB_LAYOUT)

            result = self.run_helper(temp_root, mirror_file)

            self.assert_success(result)
            content = mirror_file.read_bytes()
            self.assertEqual(
                self.active_hosts_by_priority(content),
                {
                    1: "archive.ubuntu.com",
                    2: "azure.archive.ubuntu.com",
                    3: "security.ubuntu.com",
                },
            )
            self.assertIn(b"https://archive.ubuntu.com/ubuntu/ priority:1", content)
            self.assertIn(
                b"http://azure.archive.ubuntu.com/ubuntu/ priority:2", content
            )
            self.assertIn(b"https://security.ubuntu.com/ubuntu/ priority:3", content)
            self.assertNotIn(b"example.", content)
            self.assertIn("resulting configuration", result.stdout)

    def test_already_canonical_first_is_byte_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            mirror_file = temp_root / "apt-mirrors.txt"
            mirror_file.write_bytes(CANONICAL_FIRST_LAYOUT)

            result = self.run_helper(temp_root, mirror_file)

            self.assert_success(result)
            self.assertEqual(mirror_file.read_bytes(), CANONICAL_FIRST_LAYOUT)
            self.assertIn("already active; no rewrite needed", result.stdout)

    def test_comments_blank_lines_and_benign_formatting_are_preserved(self) -> None:
        original = b"""\
# GitHub-hosted Ubuntu mirror preference

  http://azure.archive.ubuntu.com/ubuntu/   priority:1   # Azure
\thttp://archive.ubuntu.com/ubuntu\tpriority:2 # Canonical
https://security.ubuntu.com/ubuntu/    priority:3 # Security
"""
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            mirror_file = temp_root / "apt-mirrors.txt"
            mirror_file.write_bytes(original)

            result = self.run_helper(temp_root, mirror_file)

            self.assert_success(result)
            content = mirror_file.read_bytes()
            self.assertTrue(
                content.startswith(
                    b"# GitHub-hosted Ubuntu mirror preference\n\n"
                )
            )
            self.assertIn(b"   # Azure\n", content)
            self.assertIn(b" # Canonical\n", content)
            self.assertIn(b" # Security\n", content)
            self.assertEqual(
                self.active_hosts_by_priority(content),
                {
                    1: "archive.ubuntu.com",
                    2: "azure.archive.ubuntu.com",
                    3: "security.ubuntu.com",
                },
            )

    def test_missing_mirror_file_warns_and_does_not_create_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            mirror_file = temp_root / "missing-apt-mirrors.txt"

            result = self.run_helper(temp_root, mirror_file)

            self.assert_success(result)
            self.assertFalse(mirror_file.exists())
            self.assertIn("::warning title=BRIM APT mirror hardening::", result.stderr)
            self.assertIn("is missing", result.stderr)

    def test_missing_expected_endpoint_is_byte_for_byte_noop(self) -> None:
        original = b"""\
# Security entry is unexpectedly absent
http://azure.archive.ubuntu.com/ubuntu/ priority:1
http://archive.ubuntu.com/ubuntu/ priority:2
"""
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            mirror_file = temp_root / "apt-mirrors.txt"
            mirror_file.write_bytes(original)

            result = self.run_helper(temp_root, mirror_file)

            self.assert_success(result)
            self.assertEqual(mirror_file.read_bytes(), original)
            self.assertIn("preserving the original file byte-for-byte", result.stderr)

    def test_materially_changed_priority_layout_is_byte_for_byte_noop(self) -> None:
        original = CURRENT_GITHUB_LAYOUT.replace(b"priority:3", b"priority:10")
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            mirror_file = temp_root / "apt-mirrors.txt"
            mirror_file.write_bytes(original)

            result = self.run_helper(temp_root, mirror_file)

            self.assert_success(result)
            self.assertEqual(mirror_file.read_bytes(), original)
            self.assertIn("Mirror priorities are not", result.stderr)

    def test_unexpected_additional_active_entry_is_byte_for_byte_noop(self) -> None:
        original = CURRENT_GITHUB_LAYOUT + (
            b"https://mirror.example.invalid/ubuntu/ priority:4\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            mirror_file = temp_root / "apt-mirrors.txt"
            mirror_file.write_bytes(original)

            result = self.run_helper(temp_root, mirror_file)

            self.assert_success(result)
            self.assertEqual(mirror_file.read_bytes(), original)
            self.assertIn("expected three-official-endpoint shape", result.stderr)

    def test_repeat_invocation_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            mirror_file = temp_root / "apt-mirrors.txt"
            mirror_file.write_bytes(CURRENT_GITHUB_LAYOUT)

            first = self.run_helper(temp_root, mirror_file)
            first_content = mirror_file.read_bytes()
            second = self.run_helper(temp_root, mirror_file)

            self.assert_success(first)
            self.assert_success(second)
            self.assertEqual(mirror_file.read_bytes(), first_content)
            self.assertIn("already active; no rewrite needed", second.stdout)

    def test_non_ubuntu_platform_is_successful_byte_for_byte_noop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            mirror_file = temp_root / "apt-mirrors.txt"
            mirror_file.write_bytes(CURRENT_GITHUB_LAYOUT)

            result = self.run_helper(temp_root, mirror_file, os_id="debian")

            self.assert_success(result)
            self.assertEqual(mirror_file.read_bytes(), CURRENT_GITHUB_LAYOUT)
            self.assertIn("Expected Ubuntu", result.stderr)

    def test_symlink_mirror_file_is_successful_noop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            target = temp_root / "real-apt-mirrors.txt"
            mirror_file = temp_root / "apt-mirrors.txt"
            target.write_bytes(CURRENT_GITHUB_LAYOUT)
            mirror_file.symlink_to(target)

            result = self.run_helper(temp_root, mirror_file)

            self.assert_success(result)
            self.assertTrue(mirror_file.is_symlink())
            self.assertEqual(target.read_bytes(), CURRENT_GITHUB_LAYOUT)
            self.assertIn("non-symlink", result.stderr)


if __name__ == "__main__":
    unittest.main()
