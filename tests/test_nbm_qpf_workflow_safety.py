import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
QPF = WORKFLOWS / "build-nbm-qpf.yml"
HELPER_COMMAND = "sudo bash scripts/configure_apt_mirror_order.sh"


class NbmQpfWorkflowSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = QPF.read_text(encoding="utf-8")
        cls.prepare, cls.publish = cls.text.split("  publish-to-main:\n", 1)

    def test_hardening_runs_once_in_prepare_before_first_setup_r(self) -> None:
        self.assertEqual(self.text.count(HELPER_COMMAND), 1)
        self.assertIn(HELPER_COMMAND, self.prepare)
        self.assertNotIn(HELPER_COMMAND, self.publish)
        self.assertLess(
            self.prepare.index(HELPER_COMMAND),
            self.prepare.index("r-lib/actions/setup-r@v2"),
        )
        self.assertGreater(
            self.prepare.index(HELPER_COMMAND),
            self.prepare.index("actions/checkout@v6"),
        )

    def test_no_other_workflow_uses_the_qpf_canary_helper(self) -> None:
        users = {
            path.name
            for path in WORKFLOWS.glob("*.yml")
            if HELPER_COMMAND in path.read_text(encoding="utf-8")
        }
        self.assertEqual(users, {QPF.name})

    def test_schedules_and_whole_workflow_lock_are_unchanged(self) -> None:
        self.assertIn('- cron: "18 2,8,14,20 * * *"', self.text)
        self.assertIn('- cron: "10 3,9,15,21 * * *"', self.text)
        self.assertEqual(self.text.count("- cron:"), 2)
        self.assertIn("group: live-data-feed-writes-${{ github.ref }}", self.text)
        self.assertRegex(self.text, r"(?m)^  cancel-in-progress: false$")
        self.assertRegex(self.text, r"(?m)^  queue: max$")

    def test_prepare_and_publisher_timeouts_are_unchanged(self) -> None:
        prepare_header = self.prepare.split("    steps:\n", 1)[0]
        publish_header = self.publish.split("    steps:\n", 1)[0]
        self.assertIn("timeout-minutes: 60", prepare_header)
        self.assertIn("timeout-minutes: 15", publish_header)
        self.assertEqual(self.text.count("timeout-minutes:"), 2)

    def test_main_publisher_lock_and_publish_gate_are_unchanged(self) -> None:
        self.assertIn(
            "if: ${{ github.ref == 'refs/heads/main' && "
            "needs.prepare-candidate.outputs.should_build == 'true' }}",
            self.publish,
        )
        self.assertIn("group: brim-live-main-publish", self.publish)
        self.assertIn("cancel-in-progress: false", self.publish)
        self.assertIn("queue: max", self.publish)
        self.assertEqual(self.text.count('BRIM_LIVE_MAIN_PUBLISH: "true"'), 1)
        self.assertNotIn("BRIM_LIVE_MAIN_PUBLISH", self.prepare)
        self.assertIn("python3 scripts/main_publisher.py publish", self.publish)

    def test_qpf_science_candidate_and_callback_contract_are_unchanged(self) -> None:
        self.assertEqual(self.text.count("qpf_discover_cycle("), 1)
        self.assertEqual(
            self.text.count('source("scripts/build_nbm_qpf_candidate.R")'), 1
        )
        self.assertIn('BRIM_NBM_QPF_LOOKBACK_HOURS: "36"', self.prepare)
        self.assertIn('BRIM_NBM_QPF_REPEAT_BUILD: "false"', self.prepare)
        self.assertEqual(
            self.text.count("--allowlist docs/data/nbm-qpf/nbm_qpf_manifest.json"),
            2,
        )
        self.assertEqual(
            self.text.count("--owned-root docs/data/nbm-qpf/nbm/qpf"), 2
        )
        self.assertEqual(self.text.count("scripts/nbm_qpf_publisher.py"), 6)
        self.assertIn("--candidate-validator scripts/nbm_qpf_publisher.py", self.publish)
        self.assertIn("--reconcile-callback scripts/nbm_qpf_publisher.py", self.publish)
        self.assertIn("--staged-validator scripts/nbm_qpf_publisher.py", self.publish)

    def test_qpf_artifact_retention_and_permissions_are_unchanged(self) -> None:
        self.assertIn("contents: read", self.prepare)
        self.assertNotIn("contents: write", self.prepare)
        self.assertIn("contents: write", self.publish)
        self.assertEqual(
            re.findall(r"(?m)^\s+retention-days: (\d+)$", self.text), ["2", "14"]
        )


if __name__ == "__main__":
    unittest.main()
