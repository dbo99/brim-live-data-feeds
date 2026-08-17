#!/usr/bin/env python3
"""Static mixed-state lock and CDEC workflow safety checks."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
CANARY = WORKFLOWS / "build-cdec-reservoir-feed.yml"
WRITERS = (
    "build-asos-awos-wind-latest.yml",
    "build-cbrfc-major-water-supply-forecasts.yml",
    "build-cocorahs-daily-precip-feed.yml",
    "build-cdec-reservoir-feed.yml",
    "build-delta-ops-daily-summary.yml",
    "build-gfs-wind-latest.yml",
    "build-hrrr-wind-latest.yml",
    "build-major-water-supply-basin-forecasts.yml",
    "build-nbm-wind-guidance-latest.yml",
    "build-scan-soil-moisture-latest.yml",
    "build-snow-pillow-latest.yml",
    "build-usgs-groundwater-latest-ca.yml",
    "build-usgs-streamflow-latest-ca.yml",
    "build-winter-storm-levels.yml",
)


class MainPublisherWorkflowSafetyTests(unittest.TestCase):
    def test_main_writer_inventory_is_complete(self) -> None:
        discovered = {
            path.name
            for path in WORKFLOWS.glob("*.yml")
            if "  schedule:\n" in path.read_text(encoding="utf-8")
            and "contents: write" in path.read_text(encoding="utf-8")
        }
        self.assertEqual(discovered, set(WRITERS))

    def test_all_fourteen_writers_retain_the_old_whole_workflow_lock(self) -> None:
        self.assertEqual(len(WRITERS), 14)
        for filename in WRITERS:
            text = (WORKFLOWS / filename).read_text(encoding="utf-8")
            self.assertIn("group: live-data-feed-writes-${{ github.ref }}", text, filename)
            self.assertRegex(text, r"(?m)^  cancel-in-progress: false$", filename)
            self.assertRegex(text, r"(?m)^  queue: max$", filename)

    def test_only_canary_uses_the_new_main_publication_lock(self) -> None:
        for filename in WRITERS:
            text = (WORKFLOWS / filename).read_text(encoding="utf-8")
            expected = 1 if filename == CANARY.name else 0
            self.assertEqual(text.count("group: brim-live-main-publish"), expected, filename)
        text = CANARY.read_text(encoding="utf-8")
        publish_job = text.split("  publish-to-main:\n", 1)[1]
        self.assertIn("    concurrency:\n      group: brim-live-main-publish", publish_job)
        self.assertIn("      cancel-in-progress: false", publish_job)
        self.assertIn("      queue: max", publish_job)

    def test_canary_has_read_only_prepare_and_main_only_write_publisher(self) -> None:
        text = CANARY.read_text(encoding="utf-8")
        prepare, publish = text.split("  publish-to-main:\n", 1)
        self.assertIn("  prepare-candidate:\n", prepare)
        self.assertIn("      contents: read", prepare)
        self.assertNotIn("contents: write", prepare)
        self.assertIn("    if: ${{ github.ref == 'refs/heads/main' }}", publish)
        self.assertIn("      contents: write", publish)
        self.assertIn("actions/upload-artifact@v7", prepare)
        self.assertIn("actions/download-artifact@v8", publish)
        self.assertIn('source("scripts/build_cdec_reservoir_latest.R")', prepare)
        self.assertIn("CDEC_RESERVOIR_GEOJSON:", prepare)
        self.assertIn("CDEC_RESERVOIR_SUMMARY_JSON:", prepare)
        self.assertNotRegex(text, r"(?m)^\s+git (add|commit|push)\b")

    def test_canary_declares_only_its_two_exact_product_paths(self) -> None:
        text = CANARY.read_text(encoding="utf-8")
        paths = set(re.findall(r"docs/data/[A-Za-z0-9_./-]+", text))
        self.assertEqual(
            paths,
            {
                "docs/data/cdec_reservoir_latest.geojson",
                "docs/data/cdec_reservoir_latest_summary.json",
            },
        )
        self.assertIn("scripts/main_publisher.py prepare", text)
        self.assertIn("scripts/main_publisher.py publish", text)
        self.assertEqual(text.count("--allowlist docs/data/cdec_reservoir_latest.geojson"), 2)
        self.assertEqual(
            text.count("--allowlist docs/data/cdec_reservoir_latest_summary.json"), 2
        )

    def test_shared_publisher_has_no_force_push_or_merge_strategy(self) -> None:
        text = (REPO / "scripts" / "main_publisher.py").read_text(encoding="utf-8")
        self.assertNotIn('"push", "--force"', text)
        self.assertNotIn('"push", "-f"', text)
        self.assertNotRegex(text, r"\bgit (pull|merge|rebase)\b")
        self.assertIn('"git", "push", "origin", "HEAD:refs/heads/main"', text)


if __name__ == "__main__":
    unittest.main()
