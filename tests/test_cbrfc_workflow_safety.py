from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/build-cbrfc-major-water-supply-forecasts.yml"
STATE_HELPER = ROOT / "scripts/cbrfc_major_water_supply_forecast_state.R"
PARSER_HELPER = ROOT / "scripts/cbrfc_major_water_supply_forecast_helpers.R"
BUILDER = ROOT / "scripts/build_cbrfc_major_water_supply_forecasts.R"
ROSTER = ROOT / "data/input/cbrfc_major_water_supply_forecast_sources.csv"


class CbrfcWorkflowSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text()

    def test_native_pacific_daily_schedule_is_collision_resistant(self) -> None:
        self.assertIn('- cron: "14 8 * * *"', self.text)
        self.assertIn('timezone: "America/Los_Angeles"', self.text)
        other_workflows = [
            path.read_text()
            for path in (ROOT / ".github/workflows").glob("*.yml")
            if path != WORKFLOW
        ]
        self.assertFalse(any('cron: "14 8 * * *"' in text for text in other_workflows))

    def test_manual_default_is_dry_run_and_feature_branch_cannot_publish(self) -> None:
        self.assertRegex(
            self.text,
            r"publish:\n\s+description:.*\n\s+required: false\n\s+default: false",
        )
        self.assertIn(
            'elif [[ "${PUBLISH_INPUT}" == "true" && "${REF_NAME}" == "main" ]]',
            self.text,
        )
        self.assertIn("cbrfc_major_water_supply_forecasts_dry_run.json", self.text)

    def test_scheduled_run_selects_main_tip(self) -> None:
        self.assertIn(
            "ref: ${{ github.event_name == 'schedule' && 'main' || github.ref_name }}",
            self.text,
        )
        self.assertNotIn("github.sha", self.text)

    def test_publication_is_exactly_allowlisted_and_non_force(self) -> None:
        staged = re.findall(r"^\s*git add (.+)$", self.text, flags=re.MULTILINE)
        self.assertEqual(staged, ["docs/data/cbrfc_major_water_supply_forecasts.json"])
        self.assertIn('git push origin "HEAD:${GITHUB_REF}"', self.text)
        self.assertNotIn("HEAD:main", self.text)
        self.assertNotIn("git pull --rebase", self.text)
        self.assertNotRegex(self.text, r"git push[^\n]*(--force|-f\b)")
        self.assertNotIn("docs/data/major_water_supply_basin_forecasts.json", self.text)

    def test_failed_validation_cannot_reach_publish_step(self) -> None:
        build_index = self.text.index(
            "Build and validate CBRFC major water-supply forecast feed"
        )
        commit_index = self.text.index("Commit updated CBRFC feed file")
        self.assertLess(build_index, commit_index)
        self.assertIn("if: steps.run-mode.outputs.publish == 'true'", self.text)

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
        self.assertIn("staged_payload <- cbrfc_read_payload(staged, roster)", helper)
        self.assertIn("cbrfc_validate_payload(staged_payload, roster)", helper)
        self.assertEqual(helper.count("file.rename(staged, output_path)"), 1)

    def test_parser_dependencies_and_source_specific_freshness_are_declared(self) -> None:
        self.assertIn("libxml2-dev", self.text)
        self.assertIn("any::xml2", self.text)
        self.assertIn('CBRFC_FORECAST_WATER_YEAR_STALE_AFTER_DAYS: "40"', self.text)
        self.assertIn(
            'CBRFC_FORECAST_LOCAL_MONTHLY_STALE_AFTER_DAYS: "40"', self.text
        )

    def test_dashboard_and_reviewed_archive_are_retrieved_without_replacing_primary(self) -> None:
        builder = BUILDER.read_text()
        helper = PARSER_HELPER.read_text()
        self.assertIn("official_situational_awareness_secondary", builder)
        self.assertIn("dashboard_html =", builder)
        self.assertIn("official_lake_mead_local_prior_issue_evidence", builder)
        self.assertIn("lakemead.060126.csv", helper)
        self.assertIn("CBRFC_LKSA3_LOCAL_JANUARY_ROLLOVER_2026", helper)

    def test_popup_link_roles_are_explicit_and_exclude_reclamation_operations(self) -> None:
        roster = ROSTER.read_text()
        header = roster.splitlines()[0].split(",")
        self.assertIn("source_url", header)
        self.assertIn("retrieval_url_template", header)
        self.assertIn("summary_url", header)
        self.assertIn("archive_url", header)
        self.assertNotIn("reservoir_conditions_url", header)
        self.assertNotIn("usbr.gov", roster.lower())

    def test_python_cache_artifacts_are_ignored(self) -> None:
        ignore = (ROOT / ".gitignore").read_text().splitlines()
        self.assertIn("__pycache__/", ignore)
        self.assertIn("*.py[cod]", ignore)


if __name__ == "__main__":
    unittest.main()
