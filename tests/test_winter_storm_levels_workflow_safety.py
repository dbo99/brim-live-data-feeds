from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/build-winter-storm-levels.yml"
BUILDER = ROOT / "scripts/build_winter_storm_levels.R"
HELPER = ROOT / "scripts/winter_storm_levels_helpers.R"
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

    def test_schedule_is_deferred_pending_first_publication_approval(self) -> None:
        self.assertNotRegex(self.text, r"(?m)^\s+schedule:")
        self.assertIn("first production schedule is intentionally deferred", self.text)

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
        self.assertIn("if: steps.run-mode.outputs.publish == 'true'", self.text)

    def test_shared_writer_concurrency_queues(self) -> None:
        self.assertIn("group: live-data-feed-writes-${{ github.ref }}", self.text)
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn("queue: max", self.text)

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
