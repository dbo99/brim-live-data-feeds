#!/usr/bin/env python3
"""Authoritative main-writer publication-concurrency safety checks."""

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
NBM_WIND = WORKFLOWS / "build-nbm-wind-guidance-latest.yml"
NBM_QPF = WORKFLOWS / "build-nbm-qpf.yml"
WINTER_STORM = WORKFLOWS / "build-winter-storm-levels.yml"
MAIN_WRITERS = (
    "build-asos-awos-wind-latest.yml",
    "build-cbrfc-major-water-supply-forecasts.yml",
    "build-cocorahs-daily-precip-feed.yml",
    "build-cdec-reservoir-feed.yml",
    "build-delta-ops-daily-summary.yml",
    "build-gfs-wind-latest.yml",
    "build-hrrr-wind-latest.yml",
    "build-major-water-supply-basin-forecasts.yml",
    "build-nbm-wind-guidance-latest.yml",
    "build-nbm-qpf.yml",
    "build-scan-soil-moisture-latest.yml",
    "build-snow-pillow-latest.yml",
    "build-usgs-groundwater-latest-ca.yml",
    "build-usgs-streamflow-latest-ca.yml",
    "build-winter-storm-levels.yml",
)
MAIN_WRITER_PATHS = tuple(WORKFLOWS / filename for filename in MAIN_WRITERS)
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
        discovered = set()
        for path in WORKFLOWS.glob("*.yml"):
            text = path.read_text(encoding="utf-8")
            if "  schedule:\n" in text and (
                "contents: write" in text
                or "scripts/main_publisher.py publish" in text
                or re.search(r"(?m)^\s+git push\b", text)
            ):
                discovered.add(path.name)
        self.assertEqual(len(MAIN_WRITERS), 15)
        self.assertEqual(discovered, set(MAIN_WRITERS))

    def test_all_main_writers_serialize_only_publish_to_main(self) -> None:
        for workflow in MAIN_WRITER_PATHS:
            text = workflow.read_text(encoding="utf-8")
            self.assertNotIn("live-data-feed-writes-", text, workflow.name)
            self.assertNotRegex(text, r"(?m)^concurrency:\s*$", workflow.name)
            self.assertEqual(text.count("\n  publish-to-main:\n"), 1, workflow.name)
            self.assertEqual(text.count("group: brim-live-main-publish"), 1, workflow.name)
            self.assertEqual(text.count("cancel-in-progress: false"), 1, workflow.name)
            self.assertEqual(text.count("queue: max"), 1, workflow.name)

            prepare, publish = text.split("  publish-to-main:\n", 1)
            publish_header = publish.split("    steps:\n", 1)[0]
            self.assertIn(
                "    concurrency:\n"
                "      group: brim-live-main-publish\n"
                "      cancel-in-progress: false\n"
                "      queue: max",
                publish_header,
                workflow.name,
            )
            self.assertIn("  prepare-candidate:\n", prepare, workflow.name)
            self.assertIn("      contents: read", prepare, workflow.name)
            self.assertNotIn("contents: write", prepare, workflow.name)
            self.assertNotIn("scripts/main_publisher.py publish", prepare, workflow.name)
            self.assertNotRegex(
                prepare,
                r"(?m)^\s+git (?:add|commit|push|merge|rebase)\b",
                workflow.name,
            )
            self.assertIn("github.ref == 'refs/heads/main'", publish, workflow.name)
            self.assertEqual(text.count("contents: write"), 1, workflow.name)
            self.assertIn("      contents: write", publish_header, workflow.name)
            self.assertEqual(
                prepare.count("scripts/main_publisher.py prepare"), 1, workflow.name
            )
            self.assertEqual(
                publish.count("scripts/main_publisher.py publish"), 1, workflow.name
            )
            self.assertIn("actions/upload-artifact@v7", prepare, workflow.name)
            self.assertIn("actions/download-artifact@v8", publish, workflow.name)
            callbacks = []
            for option in (
                "candidate-validator",
                "reconcile-callback",
                "staged-validator",
            ):
                matches = re.findall(
                    rf"--{option}\s+(scripts/[A-Za-z0-9_./-]+\.py)", publish
                )
                self.assertEqual(len(matches), 1, f"{workflow.name}:{option}")
                callbacks.extend(matches)
            self.assertEqual(len(set(callbacks)), 1, workflow.name)

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
        nbm_wind = NBM_WIND.read_text(encoding="utf-8")
        nbm_qpf = NBM_QPF.read_text(encoding="utf-8")
        winter_storm = WINTER_STORM.read_text(encoding="utf-8")
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
        self.assertEqual(nbm_wind.count("scripts/nbm_wind_publisher.py"), 5)
        self.assertEqual(nbm_qpf.count("scripts/nbm_qpf_publisher.py"), 6)
        self.assertEqual(
            winter_storm.count("scripts/winter_storm_levels_publisher.py"), 5
        )
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
        for workflow in MAIN_WRITER_PATHS:
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
            (NBM_WIND, "nbm-wind-candidate"),
            (NBM_QPF, "nbm-qpf-publication-candidate"),
            (WINTER_STORM, "winter-storm-levels-candidate"),
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

    def test_phase9_nbm_complete_cycle_ownership_and_schedule_are_bounded(self) -> None:
        text = NBM_WIND.read_text(encoding="utf-8")
        fixed = {
            "docs/data/wind/nbm_wind_guidance_feed_manifest.json",
            "docs/data/wind/nbm_wind_guidance_latest_summary.json",
        }
        self.assertIn('- cron: "23 1,7,13,19 * * *"', text)
        self.assertIn("Rscript scripts/build_nbm_wind_guidance_latest.R", text)
        self.assertIn(
            "BRIM_NBM_PROJECT_ROOT: ${{ runner.temp }}/nbm-wind-build-root",
            text,
        )
        self.assertIn('BRIM_NBM_TARGET_LEAD_HOURS: "6,12,24,48"', text)
        self.assertIn('BRIM_NBM_SUPPORT_WINDOW_HOURS: "6"', text)
        self.assertIn('BRIM_NBM_RETAIN_HOURS: "18"', text)
        for path in fixed:
            self.assertEqual(text.count(f"--allowlist {path}"), 2)
        self.assertEqual(text.count("--owned-root docs/data/wind/nbm/guidance"), 2)
        self.assertIn("nbm_wind_complete_cycle_state_sha256_v1", text)
        self.assertNotRegex(text, r"(?m)^\s+git (add|commit|push)\b")

    def test_nbm_qpf_two_cycle_ownership_schedule_and_callbacks_are_bounded(self) -> None:
        text = NBM_QPF.read_text(encoding="utf-8")
        self.assertIn('- cron: "18 2,8,14,20 * * *"', text)
        self.assertIn('- cron: "10 3,9,15,21 * * *"', text)
        self.assertEqual(text.count("- cron:"), 2)
        self.assertIn("Approved source-readiness schedule", text)
        self.assertIn('source("scripts/build_nbm_qpf_candidate.R")', text)
        self.assertEqual(text.count("qpf_discover_cycle("), 1)
        self.assertIn("canonical_cycle=\"$(jq -er '.current_cycle_utc'", text)
        self.assertIn('"${CANDIDATE_CYCLE}" < "${canonical_cycle}"', text)
        self.assertIn("should_build=false", text)
        self.assertEqual(
            text.count("--allowlist docs/data/nbm-qpf/nbm_qpf_manifest.json"), 2
        )
        self.assertEqual(text.count("--owned-root docs/data/nbm-qpf/nbm/qpf"), 2)
        self.assertIn("nbm_qpf_cycle_state_sha256_v1", text)
        self.assertIn("needs.prepare-candidate.outputs.should_build == 'true'", text)
        self.assertNotIn("--owned-root docs/data/nbm-qpf", text.replace(
            "--owned-root docs/data/nbm-qpf/nbm/qpf", ""
        ))
        self.assertNotRegex(text, r"(?m)^\s+git (add|commit|push)\b")

    def test_phase10_winter_storm_two_cycle_ownership_and_schedule_are_bounded(self) -> None:
        text = WINTER_STORM.read_text(encoding="utf-8")
        manifest = "docs/data/winter-storm-levels/winter_storm_levels_manifest.json"
        target_root = "docs/data/winter-storm-levels/nbm/snow-level"
        self.assertIn('- cron: "8 2,8,14,20 * * *"', text)
        self.assertIn('- cron: "54 2,8,14,20 * * *"', text)
        self.assertIn("Rscript scripts/preflight_winter_storm_levels.R", text)
        self.assertIn("Rscript scripts/build_winter_storm_levels.R", text)
        self.assertIn('BRIM_WSL_PUBLISH: "false"', text)
        self.assertEqual(text.count(f"--allowlist {manifest}"), 2)
        self.assertEqual(text.count(f"--owned-root {target_root}"), 2)
        self.assertIn("winter_storm_two_cycle_state_sha256_v1", text)
        self.assertNotRegex(text, r"(?m)^\s+git (add|commit|push)\b")

    def test_shared_publisher_has_no_force_push_or_merge_strategy(self) -> None:
        text = (REPO / "scripts" / "main_publisher.py").read_text(encoding="utf-8")
        self.assertNotIn('"push", "--force"', text)
        self.assertNotIn('"push", "-f"', text)
        self.assertNotRegex(text, r"\bgit (pull|merge|rebase)\b")
        self.assertIn('"git", "push", "origin", "HEAD:refs/heads/main"', text)


if __name__ == "__main__":
    unittest.main()
