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
        self.assertIn(
            "ref: ${{ github.event_name == 'schedule' && 'main' || github.ref_name }}",
            self.text,
        )
        self.assertIn("ref: ${{ needs.prepare-candidate.outputs.source-sha }}", self.text)
        self.assertNotIn("github.sha", self.text)

    def test_publication_is_product_allowlisted_and_non_force(self) -> None:
        manifest = "docs/data/winter-storm-levels/winter_storm_levels_manifest.json"
        owned_root = "docs/data/winter-storm-levels/nbm/snow-level"
        self.assertEqual(self.text.count(f"--allowlist {manifest}"), 2)
        self.assertEqual(self.text.count(f"--owned-root {owned_root}"), 2)
        self.assertIn("scripts/main_publisher.py prepare", self.text)
        self.assertIn("scripts/main_publisher.py publish", self.text)
        self.assertEqual(self.text.count("scripts/winter_storm_levels_publisher.py"), 5)
        self.assertNotRegex(self.text, r"(?m)^\s+git (add|commit|push)\b")
        self.assertNotIn("git pull", self.text)
        self.assertNotRegex(self.text, r"\bgit (merge|rebase)\b")
        self.assertNotRegex(self.text, r"git push[^\n]*(--force|-f\b)")

    def test_validation_precedes_publication(self) -> None:
        build = self.text.index("Build and validate Winter Storm Levels bundle")
        validate = self.text.index(
            "Validate and describe Winter Storm Levels publication candidate"
        )
        publish_job = self.text.index("  publish-to-main:")
        self.assertLess(build, validate)
        self.assertLess(validate, publish_job)
        self.assertIn(
            "if: steps.run-mode.outputs.publish == 'true' && "
            "steps.bundle.outcome == 'success' && "
            "steps.bundle.outputs.build_started == 'true'",
            self.text,
        )
        self.assertIn(
            "needs.prepare-candidate.outputs.publish == 'true' && "
            "needs.prepare-candidate.outputs.build-started == 'true' && "
            "github.ref == 'refs/heads/main'",
            self.text,
        )

    def test_prepare_may_run_concurrently_and_publish_queues(self) -> None:
        self.assertNotIn("live-data-feed-writes-", self.text)
        self.assertNotRegex(self.text, r"(?m)^concurrency:\s*$")
        self.assertIn("group: brim-live-main-publish", self.text)
        self.assertEqual(self.text.count("cancel-in-progress: false"), 1)
        self.assertEqual(self.text.count("queue: max"), 1)
        groups = re.findall(r"(?m)^\s+group:\s*(.+)$", self.text)
        self.assertEqual(groups, ["brim-live-main-publish"])

    def test_prepare_is_read_only_and_publish_is_the_only_write_job(self) -> None:
        prepare, publish = self.text.split("  publish-to-main:\n", 1)
        self.assertIn("permissions: {}", prepare)
        self.assertIn("  prepare-candidate:\n", prepare)
        self.assertIn("      contents: read", prepare)
        self.assertNotIn("contents: write", prepare)
        self.assertEqual(publish.count("contents: write"), 1)
        self.assertIn("actions/upload-artifact@v7", prepare)
        self.assertIn("actions/download-artifact@v8", publish)

    def test_producer_always_builds_runner_temporary_candidate(self) -> None:
        self.assertIn('BRIM_WSL_PUBLISH: "false"', self.text)
        self.assertIn(
            'echo "output_root=${RUNNER_TEMP}/winter-storm-levels-bundle"',
            self.text,
        )
        self.assertIn(
            'candidate_artifact_root="${RUNNER_TEMP}/winter-storm-levels-candidate"',
            self.text,
        )
        self.assertNotIn("/home/runner/", self.text)

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
        self.assertIn("Upload guarded no-op diagnostics", self.text)
        self.assertIn("steps.bundle.outputs.build_started != 'true'", self.text)
        self.assertNotRegex(
            self.text,
            r"(?s)SOURCE_NOT_READY.*scripts/main_publisher.py prepare",
        )

    def test_manual_dry_run_bypasses_guard_but_manual_publish_is_guarded(self) -> None:
        self.assertIn("publish=false\n          guard_preflight=false", self.text)
        self.assertIn(
            'elif [[ "${PUBLISH_INPUT}" == "true" && "${REF_NAME}" == "main" ]]',
            self.text,
        )
        self.assertIn("Feature branches are dry-run only.", self.text)

    def test_final_publication_guard_requires_strictly_newer_cycle(self) -> None:
        callback = (ROOT / "scripts" / "winter_storm_levels_publisher.py").read_text()
        self.assertIn("candidate.current_cycle < canonical.current_cycle", callback)
        self.assertIn("candidate.current_cycle == canonical.current_cycle", callback)
        self.assertIn("same-cycle Winter Storm Levels states conflict", callback)
        self.assertIn("newer complete cycle advanced with exact two-cycle retention", callback)

    def test_publication_uses_schema2_fresh_main_transaction(self) -> None:
        self.assertIn("winter_storm_two_cycle_state_sha256_v1", self.text)
        self.assertIn("--target-ref refs/heads/main", self.text)
        self.assertIn("BRIM_LIVE_MAIN_PUBLISH: \"true\"", self.text)
        self.assertIn("--candidate-validator scripts/winter_storm_levels_publisher.py", self.text)
        self.assertIn("--reconcile-callback scripts/winter_storm_levels_publisher.py", self.text)
        self.assertIn("--staged-validator scripts/winter_storm_levels_publisher.py", self.text)

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
