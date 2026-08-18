#!/usr/bin/env python3
"""Static mixed-state lock and migrated-writer workflow safety checks."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
CDEC = WORKFLOWS / "build-cdec-reservoir-feed.yml"
DELTA_OPS = WORKFLOWS / "build-delta-ops-daily-summary.yml"
SCAN = WORKFLOWS / "build-scan-soil-moisture-latest.yml"
COCORAHS = WORKFLOWS / "build-cocorahs-daily-precip-feed.yml"
STREAMFLOW = WORKFLOWS / "build-usgs-streamflow-latest-ca.yml"
GROUNDWATER = WORKFLOWS / "build-usgs-groundwater-latest-ca.yml"
ASOS_AWOS = WORKFLOWS / "build-asos-awos-wind-latest.yml"
SNOW_PILLOW = WORKFLOWS / "build-snow-pillow-latest.yml"
CNRFC = WORKFLOWS / "build-major-water-supply-basin-forecasts.yml"
CBRFC = WORKFLOWS / "build-cbrfc-major-water-supply-forecasts.yml"
GFS = WORKFLOWS / "build-gfs-wind-latest.yml"
HRRR = WORKFLOWS / "build-hrrr-wind-latest.yml"
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
MIGRATED = (
    CDEC,
    DELTA_OPS,
    SCAN,
    COCORAHS,
    STREAMFLOW,
    GROUNDWATER,
    ASOS_AWOS,
    SNOW_PILLOW,
    CNRFC,
    CBRFC,
    GFS,
    HRRR,
)
PRODUCT_PATHS = {
    CDEC: {
        "docs/data/cdec_reservoir_latest.geojson",
        "docs/data/cdec_reservoir_latest_summary.json",
    },
    DELTA_OPS: {
        "docs/data/delta_ops_daily_summary.json",
        "docs/data/delta_ops_daily_summary_features.geojson",
        "docs/data/delta_ops_daily_summary_summary.json",
        "docs/data/delta_ops_x2_reference.geojson",
    },
    SCAN: {
        "docs/data/scan_soil_moisture_latest.geojson",
        "docs/data/scan_soil_moisture_latest_summary.json",
        "docs/data/scan_soil_moisture_current_wy_trace.csv",
        "docs/data/scan_soil_moisture_current_wy_trace_summary.json",
        "docs/data/scan_depth_style.csv",
    },
    COCORAHS: {
        "docs/data/cocorahs_daily_precip_ca_latest.geojson",
        "docs/data/cocorahs_daily_precip_ca_latest_summary.json",
        "docs/data/cocorahs_daily_precip_conus_latest.geojson",
        "docs/data/cocorahs_daily_precip_conus_latest_summary.json",
    },
    STREAMFLOW: {
        "docs/data/usgs_streamflow_latest_ca.geojson",
        "docs/data/usgs_streamflow_latest_ca_summary.json",
    },
    GROUNDWATER: {
        "docs/data/usgs_groundwater_latest_ca.geojson",
        "docs/data/usgs_groundwater_latest_ca_summary.json",
    },
    ASOS_AWOS: {
        "docs/data/wind/asos_awos_wind_latest.geojson",
        "docs/data/wind/asos_awos_wind_latest_summary.json",
        "docs/data/wind/asos_awos_wind_feed_manifest.json",
    },
    SNOW_PILLOW: {
        "docs/data/snow_pillow_latest.geojson",
        "docs/data/snow_pillow_latest_summary.json",
        "docs/data/snow_pillow_current_wy_trace.csv",
        "docs/data/snow_pillow_current_wy_trace_summary.json",
    },
    CNRFC: {
        "docs/data/major_water_supply_basin_forecasts.json",
    },
    CBRFC: {
        "docs/data/cbrfc_major_water_supply_forecasts.json",
    },
}


def job_level_env_blocks(text: str) -> list[str]:
    """Return job-level env blocks without mistaking step env for job env."""
    lines = text.splitlines()
    blocks: list[str] = []
    for index, line in enumerate(lines):
        if line != "    env:":
            continue
        block = [line]
        for following in lines[index + 1 :]:
            if following and len(following) - len(following.lstrip()) <= 4:
                break
            block.append(following)
        blocks.append("\n".join(block))
    return blocks


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

    def test_only_migrated_writers_use_the_new_main_publication_lock(self) -> None:
        self.assertEqual(len(MIGRATED), 12)
        for filename in WRITERS:
            text = (WORKFLOWS / filename).read_text(encoding="utf-8")
            expected = 1 if WORKFLOWS / filename in MIGRATED else 0
            self.assertEqual(text.count("group: brim-live-main-publish"), expected, filename)
        for workflow in MIGRATED:
            text = workflow.read_text(encoding="utf-8")
            publish_job = text.split("  publish-to-main:\n", 1)[1]
            self.assertIn("    concurrency:\n      group: brim-live-main-publish", publish_job)
            self.assertIn("      cancel-in-progress: false", publish_job)
            self.assertIn("      queue: max", publish_job)

    def test_migrated_writers_have_read_only_prepare_and_main_only_publish(self) -> None:
        for workflow in MIGRATED:
            text = workflow.read_text(encoding="utf-8")
            prepare, publish = text.split("  publish-to-main:\n", 1)
            self.assertIn("  prepare-candidate:\n", prepare, workflow.name)
            self.assertIn("      contents: read", prepare, workflow.name)
            self.assertNotIn("contents: write", prepare, workflow.name)
            self.assertIn("github.ref == 'refs/heads/main'", publish, workflow.name)
            self.assertIn("      contents: write", publish, workflow.name)
            self.assertIn("actions/upload-artifact@v7", prepare, workflow.name)
            self.assertIn("actions/download-artifact@v8", publish, workflow.name)
            self.assertNotRegex(text, r"(?m)^\s+git (add|commit|push)\b", workflow.name)

        cdec_text = CDEC.read_text(encoding="utf-8")
        self.assertIn('source("scripts/build_cdec_reservoir_latest.R")', cdec_text)
        self.assertIn("CDEC_RESERVOIR_GEOJSON:", cdec_text)
        self.assertIn("CDEC_RESERVOIR_SUMMARY_JSON:", cdec_text)

    def test_migrated_writers_declare_only_exact_product_paths(self) -> None:
        for workflow, expected_paths in PRODUCT_PATHS.items():
            text = workflow.read_text(encoding="utf-8")
            paths = set(re.findall(r"docs/data/[A-Za-z0-9_./-]+", text))
            self.assertEqual(paths, expected_paths, workflow.name)
            self.assertIn("scripts/main_publisher.py prepare", text, workflow.name)
            self.assertIn("scripts/main_publisher.py publish", text, workflow.name)
            for path in expected_paths:
                self.assertEqual(text.count(f"--allowlist {path}"), 2, workflow.name)

    def test_product_callbacks_and_candidate_roots_are_product_specific(self) -> None:
        delta = DELTA_OPS.read_text(encoding="utf-8")
        scan = SCAN.read_text(encoding="utf-8")
        cocorahs = COCORAHS.read_text(encoding="utf-8")
        streamflow = STREAMFLOW.read_text(encoding="utf-8")
        groundwater = GROUNDWATER.read_text(encoding="utf-8")
        asos_awos = ASOS_AWOS.read_text(encoding="utf-8")
        snow_pillow = SNOW_PILLOW.read_text(encoding="utf-8")
        cnrfc = CNRFC.read_text(encoding="utf-8")
        cbrfc = CBRFC.read_text(encoding="utf-8")
        gfs = GFS.read_text(encoding="utf-8")
        hrrr = HRRR.read_text(encoding="utf-8")
        for text, callback in (
            (delta, "scripts/delta_ops_publisher.py"),
            (scan, "scripts/scan_soil_moisture_publisher.py"),
            (cocorahs, "scripts/cocorahs_publisher.py"),
            (streamflow, "scripts/usgs_streamflow_publisher.py"),
            (groundwater, "scripts/usgs_groundwater_publisher.py"),
        ):
            self.assertEqual(text.count(callback), 4)
        self.assertEqual(asos_awos.count("scripts/asos_awos_wind_publisher.py"), 5)
        self.assertEqual(snow_pillow.count("scripts/snow_pillow_publisher.py"), 6)
        self.assertEqual(cnrfc.count("scripts/cnrfc_forecast_publisher.py"), 5)
        self.assertEqual(cbrfc.count("scripts/cbrfc_forecast_publisher.py"), 5)
        self.assertEqual(gfs.count("scripts/gfs_wind_publisher.py"), 5)
        self.assertEqual(hrrr.count("scripts/hrrr_wind_publisher.py"), 5)
        self.assertIn("DELTA_OPS_OUT_DIR:", delta)
        self.assertIn("delta-ops-candidate/candidate/docs/data", delta)
        self.assertIn("scan-soil-moisture-candidate/candidate", scan)
        self.assertIn("SCAN_STATION_INDEX_CSV: ${{ github.workspace }}", scan)
        self.assertNotIn("SCAN_SMS_GEOJSON:", scan)
        self.assertIn("COCORAHS_CA_DAILY_PRECIP_GEOJSON:", cocorahs)
        self.assertIn("COCORAHS_CONUS_DAILY_PRECIP_SUMMARY_JSON:", cocorahs)
        self.assertIn("USGS_STREAMFLOW_GEOJSON:", streamflow)
        self.assertIn("USGS_STREAMFLOW_SUMMARY_JSON:", streamflow)
        self.assertIn("USGS_GW_GEOJSON:", groundwater)
        self.assertIn("USGS_GW_SUMMARY_JSON:", groundwater)
        self.assertIn("BRIM_OBS_PROJECT_ROOT: ${{ runner.temp }}/asos-awos-build-root", asos_awos)
        self.assertIn('candidate_root="${candidate_artifact_root}/candidate"', asos_awos)
        self.assertIn('candidate_root="${candidate_artifact_root}/candidate"', snow_pillow)
        self.assertIn("snow_provider_state_v1", snow_pillow)
        self.assertIn("SNOW_PILLOW_BUILD_ROOT:", snow_pillow)

    def test_migrated_workflows_resolve_runner_temp_only_after_allocation(self) -> None:
        for workflow in MIGRATED:
            text = workflow.read_text(encoding="utf-8")
            for block in job_level_env_blocks(text):
                self.assertNotRegex(block, r"\$\{\{\s*runner\.", workflow.name)
            self.assertNotIn("/home/runner/", text, workflow.name)
            self.assertNotRegex(text, r"(?<![A-Z_])/tmp/", workflow.name)

        text = CDEC.read_text(encoding="utf-8")
        self.assertEqual(
            text.count(
                'candidate_artifact_root="${RUNNER_TEMP}/cdec-reservoir-candidate"'
            ),
            2,
        )
        self.assertIn('candidate_root="${candidate_artifact_root}/candidate"', text)
        self.assertEqual(text.count("${{ runner.temp }}"), 4)

        for workflow, root_name in (
            (DELTA_OPS, "delta-ops-candidate"),
            (SCAN, "scan-soil-moisture-candidate"),
            (COCORAHS, "cocorahs-candidate"),
            (STREAMFLOW, "usgs-streamflow-candidate"),
            (GROUNDWATER, "usgs-groundwater-candidate"),
            (ASOS_AWOS, "asos-awos-candidate"),
            (SNOW_PILLOW, "snow-pillow-candidate"),
            (CNRFC, "cnrfc-forecast-candidate"),
            (CBRFC, "cbrfc-forecast-candidate"),
            (GFS, "gfs-wind-candidate"),
            (HRRR, "hrrr-wind-candidate"),
        ):
            text = workflow.read_text(encoding="utf-8")
            self.assertIn(f'candidate_artifact_root="${{RUNNER_TEMP}}/{root_name}"', text)

    def test_phase3_schedules_and_product_health_switches_are_unchanged(self) -> None:
        cocorahs = COCORAHS.read_text(encoding="utf-8")
        streamflow = STREAMFLOW.read_text(encoding="utf-8")
        groundwater = GROUNDWATER.read_text(encoding="utf-8")
        self.assertIn('cron: "37 0,8,16 * * *"', cocorahs)
        self.assertIn('cron: "23 */4 * * *"', streamflow)
        self.assertIn('cron: "41 13 * * *"', groundwater)
        self.assertIn('USGS_STREAMFLOW_MIN_LATEST_Q_TO_PUBLISH: "100"', streamflow)
        self.assertIn('USGS_STREAMFLOW_ALLOW_DEGRADED_PUBLISH: "false"', streamflow)
        self.assertIn('USGS_GW_MIN_API_SITES_TO_PUBLISH: "300"', groundwater)
        self.assertIn('USGS_GW_MIN_FEATURES_TO_PUBLISH: "300"', groundwater)
        self.assertIn('USGS_GW_ALLOW_INDEX_FALLBACK: "true"', groundwater)

    def test_phase4_asos_freshness_and_watchdog_contract_are_unchanged(self) -> None:
        asos_awos = ASOS_AWOS.read_text(encoding="utf-8")
        watchdog = (WORKFLOWS / "check-wind-feeds.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "17,42 * * * *"', asos_awos)
        self.assertIn("age_minutes >= 45.0", asos_awos)
        self.assertIn("manual dispatch always runs", asos_awos)
        self.assertIn("needs.prepare-candidate.outputs.should_publish == 'true'", asos_awos)
        self.assertIn('BRIM_OBS_MAX_AGE_HOURS: "3.0"', asos_awos)
        self.assertIn('BRIM_OBS_STALE_AFTER_HOURS: "2.0"', asos_awos)
        self.assertIn('"workflow": "build-asos-awos-wind-latest.yml"', watchdog)
        self.assertIn('"hourly_slots": [17, 42]', watchdog)
        self.assertIn('"cooldown": 20', watchdog)

    def test_phase5_snow_pillow_schedule_and_provider_boundary_are_unchanged(self) -> None:
        snow_pillow = SNOW_PILLOW.read_text(encoding="utf-8")
        self.assertIn('cron: "52 5,13,21 * * *"', snow_pillow)
        self.assertIn('source(file.path(', snow_pillow)
        self.assertIn('"build_snow_pillow_latest.R"', snow_pillow)
        self.assertIn("scripts/snow_pillow_partial_refresh_helpers.R", snow_pillow)
        self.assertIn("data/input/snow_pillow_station_index.csv", snow_pillow)
        self.assertIn("data/input/snow_pillow_swe_normal_medians.csv", snow_pillow)
        self.assertIn("scripts/main_publisher.py publish", snow_pillow)

    def test_phase6_rfc_schedules_manual_defaults_and_record_callbacks_are_unchanged(self) -> None:
        cnrfc = CNRFC.read_text(encoding="utf-8")
        cbrfc = CBRFC.read_text(encoding="utf-8")
        self.assertIn('- cron: "56 10,16 * * *"', cnrfc)
        self.assertIn('- cron: "14 8 * * *"', cbrfc)
        for text, callback, semantic_type in (
            (cnrfc, "scripts/cnrfc_forecast_publisher.py", "cnrfc_record_state_sha256_v1"),
            (cbrfc, "scripts/cbrfc_forecast_publisher.py", "cbrfc_record_state_sha256_v1"),
        ):
            self.assertRegex(
                text,
                r"publish:\n\s+description:.*\n\s+required: false\n\s+default: false",
            )
            self.assertIn("needs.prepare-candidate.outputs.publish == 'true'", text)
            self.assertIn(semantic_type, text)
            self.assertEqual(text.count(callback), 5)
            self.assertNotRegex(text, r"(?m)^\s+git (add|commit|push)\b")

    def test_phase7_gfs_rolling_tree_ownership_and_schedule_are_bounded(self) -> None:
        text = GFS.read_text(encoding="utf-8")
        fixed = {
            "docs/data/wind/gfs_surface_wind_latest.json",
            "docs/data/wind/gfs_surface_wind_latest_summary.json",
            "docs/data/wind/gfs_surface_wind_feed_manifest.json",
        }
        self.assertIn('- cron: "47 * * * *"', text)
        self.assertIn('source("scripts/build_gfs_wind_latest.R")', text)
        self.assertIn("BRIM_RTW_PROJECT_ROOT: ${{ runner.temp }}/gfs-wind-build-root", text)
        for path in fixed:
            self.assertEqual(text.count(f"--allowlist {path}"), 2)
        self.assertEqual(text.count("--owned-root docs/data/wind/gfs/surface"), 2)
        self.assertIn("gfs_rolling_state_sha256_v1", text)
        self.assertNotRegex(text, r"(?m)^\s+git (add|commit|push)\b")

    def test_phase8_hrrr_rolling_tree_ownership_and_schedule_are_bounded(self) -> None:
        text = HRRR.read_text(encoding="utf-8")
        fixed = {
            "docs/data/wind/hrrr_surface_wind_latest.json",
            "docs/data/wind/hrrr_surface_wind_latest_summary.json",
            "docs/data/wind/hrrr_surface_wind_feed_manifest.json",
        }
        self.assertIn('- cron: "33 * * * *"', text)
        self.assertIn("Rscript scripts/build_hrrr_wind_latest.R", text)
        self.assertIn("BRIM_HRRR_PROJECT_ROOT: ${{ runner.temp }}/hrrr-wind-build-root", text)
        self.assertIn('BRIM_HRRR_RETAIN_PAST_HOURS: "4"', text)
        self.assertIn('BRIM_HRRR_RETAIN_FUTURE_HOURS: "14"', text)
        self.assertIn('BRIM_HRRR_MAX_MANIFEST_ENTRIES: "24"', text)
        for path in fixed:
            self.assertEqual(text.count(f"--allowlist {path}"), 2)
        self.assertEqual(text.count("--owned-root docs/data/wind/hrrr/surface"), 2)
        self.assertIn("hrrr_rolling_state_sha256_v1", text)
        self.assertNotRegex(text, r"(?m)^\s+git (add|commit|push)\b")

    def test_shared_publisher_has_no_force_push_or_merge_strategy(self) -> None:
        text = (REPO / "scripts" / "main_publisher.py").read_text(encoding="utf-8")
        self.assertNotIn('"push", "--force"', text)
        self.assertNotIn('"push", "-f"', text)
        self.assertNotRegex(text, r"\bgit (pull|merge|rebase)\b")
        self.assertIn('"git", "push", "origin", "HEAD:refs/heads/main"', text)


if __name__ == "__main__":
    unittest.main()
