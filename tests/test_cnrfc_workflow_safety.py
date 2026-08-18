from __future__ import annotations

import importlib.util
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/build-major-water-supply-basin-forecasts.yml"
SLOT_SCRIPT = ROOT / "scripts/cnrfc_forecast_slot.py"
STATE_HELPER = ROOT / "scripts/cnrfc_major_water_supply_basin_forecast_state.R"

spec = importlib.util.spec_from_file_location("cnrfc_forecast_slot", SLOT_SCRIPT)
slot_module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(slot_module)


class LogicalSlotTests(unittest.TestCase):
    def slot(self, instant: str) -> str:
        completed = subprocess.run(
            ["python3", str(SLOT_SCRIPT), "--now", instant],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def test_am_and_pm_slots_use_pacific_date(self) -> None:
        self.assertEqual(self.slot("2026-07-31T11:20:00-07:00"), "2026-07-31-AM")
        self.assertEqual(self.slot("2026-07-31T18:20:00-07:00"), "2026-07-31-PM")

    def test_delayed_pm_run_after_midnight_keeps_previous_slot(self) -> None:
        self.assertEqual(self.slot("2026-08-01T02:00:00-07:00"), "2026-07-31-PM")

    def test_manual_override_is_validated(self) -> None:
        self.assertEqual(slot_module.validate_override("2026-12-31-PM"), "2026-12-31-PM")
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD-AM or YYYY-MM-DD-PM"):
            slot_module.validate_override("2026-12-31-evening")


class WorkflowSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text()

    def test_native_pacific_schedule_is_exact(self) -> None:
        self.assertIn('- cron: "56 10,16 * * *"', self.text)
        self.assertIn('timezone: "America/Los_Angeles"', self.text)
        self.assertNotIn("schedule_guard", self.text)
        self.assertNotRegex(self.text, r"date\s+\+%[HM]")

    def test_manual_default_is_dry_run_and_feature_branch_cannot_publish(self) -> None:
        self.assertRegex(
            self.text,
            r"publish:\n\s+description:.*\n\s+required: false\n\s+default: false",
        )
        self.assertIn(
            'elif [[ "${PUBLISH_INPUT}" == "true" && "${REF_NAME}" == "main" ]]',
            self.text,
        )
        self.assertIn("${RUNNER_TEMP}/cnrfc-forecast-candidate", self.text)
        self.assertIn("needs.prepare-candidate.outputs.publish == 'true'", self.text)

    def test_scheduled_run_selects_main_tip_and_manual_run_selects_branch_tip(self) -> None:
        self.assertIn(
            "ref: ${{ github.event_name == 'schedule' && 'main' || github.ref_name }}",
            self.text,
        )
        self.assertNotIn("github.sha", self.text)

    def test_publication_uses_the_exact_shared_transaction_allowlist(self) -> None:
        staged = re.findall(r"^\s*git add (.+)$", self.text, flags=re.MULTILINE)
        self.assertEqual(staged, [])
        self.assertIn("scripts/main_publisher.py prepare", self.text)
        self.assertIn("scripts/main_publisher.py publish", self.text)
        self.assertEqual(
            self.text.count("--allowlist docs/data/major_water_supply_basin_forecasts.json"),
            2,
        )
        self.assertIn("group: brim-live-main-publish", self.text)
        self.assertNotRegex(self.text, r"(?m)^\s+git (add|commit|push)\b")

    def test_failed_validation_cannot_reach_publish_step(self) -> None:
        build_index = self.text.index("Build and validate isolated CNRFC candidate")
        publish_index = self.text.index("Reconcile and publish CNRFC candidate")
        self.assertLess(build_index, publish_index)
        self.assertIn("if: ${{ needs.prepare-candidate.outputs.publish == 'true'", self.text)

    def test_writer_concurrency_queues_instead_of_cancelling(self) -> None:
        match = re.search(
            r"(?ms)^concurrency:\n(?P<body>(?:^  [^\n]+\n)+)", self.text
        )
        self.assertIsNotNone(match)
        assert match is not None
        concurrency = dict(
            re.findall(r"^  ([a-z-]+):\s*(\S.*?)\s*$", match.group("body"), re.MULTILINE)
        )
        self.assertEqual(
            set(concurrency), {"group", "queue", "cancel-in-progress"}
        )
        self.assertEqual(
            concurrency.get("group"), "live-data-feed-writes-${{ github.ref }}"
        )
        self.assertEqual(concurrency.get("queue"), "max")
        self.assertEqual(concurrency.get("cancel-in-progress"), "false")
        self.assertIn("group: live-data-feed-writes-${{ github.ref }}", self.text)
        self.assertIn("queue: max", self.text)
        self.assertIn("cancel-in-progress: false", self.text)

    def test_payload_promotion_is_a_single_validated_rename(self) -> None:
        helper = STATE_HELPER.read_text()
        self.assertIn("staged_payload <- cnrfc_read_payload(staged, roster)", helper)
        self.assertIn("cnrfc_validate_payload(staged_payload, roster)", helper)
        self.assertEqual(helper.count("file.rename(staged, output_path)"), 1)


if __name__ == "__main__":
    unittest.main()
