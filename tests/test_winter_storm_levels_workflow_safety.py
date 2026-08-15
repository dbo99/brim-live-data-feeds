from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/build-winter-storm-levels.yml"
BUILDER = ROOT / "scripts/build_winter_storm_levels.R"
HELPER = ROOT / "scripts/winter_storm_levels_helpers.R"
PREFLIGHT = ROOT / "scripts/preflight_winter_storm_levels.R"
QA_PAGE = ROOT / "qa/winter_storm_levels/index.html"


class WinterStormLevelsWorkflowSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text()

    def test_manual_default_is_dry_run_and_feature_branch_cannot_publish(self) -> None:
        self.assertRegex(
            self.text,
            r"publish:\n\s+description:.*\n\s+required: false\n\s+default: false",
        )
        self.assertIn(
            'if [[ "${PUBLISH_INPUT}" == "true" && "${REF_NAME}" == "main" ]]',
            self.text,
        )
        self.assertIn("Feature branches are dry-run only.", self.text)

    def test_schedule_uses_only_the_approved_primary_and_fallback_crons(self) -> None:
        crons = re.findall(r'^\s+- cron: "([^"]+)"$', self.text, flags=re.MULTILINE)
        self.assertEqual(crons, ["8 2,8,14,20 * * *", "54 2,8,14,20 * * *"])

    def test_checkout_uses_selected_branch_tip(self) -> None:
        self.assertIn("ref: ${{ github.ref_name }}", self.text)
        self.assertNotIn("github.sha", self.text)

    def test_publication_is_product_allowlisted_and_non_force(self) -> None:
        staged = re.findall(r"^\s*git add (.+)$", self.text, flags=re.MULTILINE)
        self.assertEqual(staged, ["-- docs/data/winter-storm-levels"])
        self.assertIn("git diff --cached --name-only", self.text)
        self.assertIn("docs/data/winter-storm-levels/*", self.text)
        self.assertIn('git push origin "HEAD:${GITHUB_REF}"', self.text)
        self.assertNotIn("HEAD:main", self.text)
        self.assertNotIn("git pull", self.text)
        self.assertNotRegex(self.text, r"\bgit (merge|rebase)\b")
        self.assertNotRegex(self.text, r"(?m)^\s*git push\s*$")
        self.assertNotRegex(self.text, r"git push[^\n]*(--force|-f\b)")

    def test_validation_precedes_publication(self) -> None:
        build = self.text.index("Build and validate Winter Storm Levels bundle")
        commit = self.text.index("Commit updated Winter Storm Levels bundle")
        self.assertLess(build, commit)
        self.assertIn(
            "if: steps.run-mode.outputs.publish == 'true' && "
            "steps.bundle.outcome == 'success' && "
            "steps.bundle.outputs.build_started == 'true' && "
            "steps.upload-bundle.outcome == 'success'",
            self.text,
        )

    def test_shared_writer_concurrency_queues(self) -> None:
        self.assertIn("group: live-data-feed-writes-${{ github.ref }}", self.text)
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn("queue: max", self.text)
        groups = re.findall(r"(?m)^\s+group:\s*(.+)$", self.text)
        self.assertEqual(groups, ["live-data-feed-writes-${{ github.ref }}"])

    def test_scheduled_publication_is_guarded_by_inventory_preflight(self) -> None:
        run_mode = self.text.index("Resolve publication mode")
        preflight = self.text.index("Run inventory-only cycle preflight")
        dependencies = self.text.index("Install geospatial system libraries")
        build = self.text.index("Build and validate Winter Storm Levels bundle")
        self.assertLess(run_mode, preflight)
        self.assertLess(preflight, dependencies)
        self.assertLess(preflight, build)
        self.assertIn('if [[ "${EVENT_NAME}" == "schedule" ]]', self.text)
        self.assertIn("guard_preflight=true", self.text)
        eligibility = (
            "steps.run-mode.outputs.guard_preflight != 'true' || "
            "steps.preflight.outputs.outcome == 'NEW_CYCLE'"
        )
        self.assertGreaterEqual(self.text.count(eligibility), 4)
        self.assertIn("steps.preflight.outputs.outcome == 'SOURCE_ERROR'", self.text)

    def test_guarded_noops_cannot_enter_publication_transaction(self) -> None:
        self.assertIn("steps.bundle.outputs.build_started == 'true'", self.text)
        self.assertIn("steps.upload-bundle.outcome == 'success'", self.text)
        self.assertIn("Upload guarded no-op diagnostics", self.text)
        self.assertIn("steps.bundle.outputs.build_started != 'true'", self.text)
        self.assertNotRegex(
            self.text,
            r"(?s)SOURCE_NOT_READY.*git add -- docs/data/winter-storm-levels",
        )

    def test_manual_dry_run_bypasses_guard_but_manual_publish_is_guarded(self) -> None:
        self.assertIn("publish=false\n          guard_preflight=false", self.text)
        self.assertIn(
            'elif [[ "${PUBLISH_INPUT}" == "true" && "${REF_NAME}" == "main" ]]',
            self.text,
        )
        self.assertIn("Feature branches are dry-run only.", self.text)

    def test_final_publication_guard_requires_strictly_newer_cycle(self) -> None:
        self.assertIn('git show "HEAD:${canonical_manifest}"', self.text)
        self.assertIn(
            '[[ "${candidate_cycle}" < "${current_cycle}" || '
            '"${candidate_cycle}" == "${current_cycle}" ]]',
            self.text,
        )
        self.assertIn("publication_attempted=false", self.text)
        self.assertIn("Publication guard accepted", self.text)

    def test_preflight_is_inventory_only_and_machine_readable(self) -> None:
        preflight = PREFLIGHT.read_text()
        helper = HELPER.read_text()
        self.assertIn("wsl_preflight(", preflight)
        self.assertIn('required_packages <- c("httr2", "jsonlite")', preflight)
        self.assertNotIn("wsl_fetch_range", preflight)
        self.assertNotRegex(preflight, r"\b(terra|sf|isoband)::")
        for field in (
            "trigger_type",
            "observation_time_utc",
            "candidate_cycles",
            "newest_nominal_candidate",
            "newest_complete_cycle",
            "canonical_current_cycle",
            "preflight_outcome",
            "build_started",
            "publication_attempted",
        ):
            self.assertIn(field, helper)
        self.assertIn("wsl_discover_cycle(observation_time, config, fetch_text)", helper)

    def test_candidate_validates_before_manifest_last_promotion(self) -> None:
        helper = HELPER.read_text()
        builder = BUILDER.read_text()
        self.assertIn("wsl_validate_manifest(manifest_path, stage_root", builder)
        self.assertIn("for (entry in candidate$targets)", helper)
        self.assertIn("file.rename(staged_manifest, canonical_manifest_path)", helper)
        self.assertLess(
            helper.index("for (entry in candidate$targets)"),
            helper.index("file.rename(staged_manifest, canonical_manifest_path)"),
        )

    def test_dry_run_artifact_contains_browser_qa(self) -> None:
        self.assertIn('cp qa/winter_storm_levels/index.html "${OUTPUT_ROOT}/qa.html"', self.text)
        self.assertIn("winter-storm-levels-qa", self.text)
        qa_page = QA_PAGE.read_text()
        self.assertIn("Missing local target", qa_page)
        self.assertIn("Target checksum mismatch", qa_page)
        self.assertIn("manifest.targets.map", qa_page)


if __name__ == "__main__":
    unittest.main()
