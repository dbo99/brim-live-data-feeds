#!/usr/bin/env python3
"""End-to-end controlled-fixture tests for the CoCoRaHS R producer."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
BUILDER = REPO / "scripts" / "build_cocorahs_daily_precip_latest.R"
VALIDATOR = REPO / "scripts" / "cocorahs_publisher.py"
ABSENT = object()


def observation(index: int, name: object, *, state: str = "CO") -> dict[str, object]:
    record: dict[str, object] = {
        "id": f"obs-{index:03d}",
        "stationNumber": f"{state}-TEST-{index:03d}",
        "latitude": 38.0 + index / 1000,
        "longitude": -121.0 + index / 1000,
        "obsDateTime": "2026-08-19T07:00:00-07:00",
        "entryDateTime": "2026-08-19T07:05:00-07:00",
        "dateTimeStamp": "2026-08-19T14:05:00Z",
        "precip": 0.5 + index / 100,
        "gaugeCatch": 0.5 + index / 100,
        "precipIsTrace": False,
        "gaugeCatchIsTrace": False,
        "units": "english",
        "source": "CoCoRaHS DailyPrecipObs API",
        "state": state,
    }
    if name is not ABSENT:
        record["stationName"] = name
    return record


@unittest.skipUnless(shutil.which("Rscript"), "Rscript is required")
class CoCoRaHSProducerTests(unittest.TestCase):
    maxDiff = None

    def run_builder(self, records: list[dict[str, object]]) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture_path = root / "cocorahs-fixture.json"
            fixture_path.write_text(
                json.dumps(
                    {
                        "metadata": {"resultset": {"totalCount": len(records)}},
                        "results": records,
                    }
                ),
                encoding="utf-8",
            )
            paths = {
                "ca_geojson": root / "docs/data/cocorahs_daily_precip_ca_latest.geojson",
                "ca_summary": root / "docs/data/cocorahs_daily_precip_ca_latest_summary.json",
                "conus_geojson": root / "docs/data/cocorahs_daily_precip_conus_latest.geojson",
                "conus_summary": root / "docs/data/cocorahs_daily_precip_conus_latest_summary.json",
            }
            env = os.environ.copy()
            env.update(
                {
                    "COCORAHS_TEST_FIXTURE_JSON": str(fixture_path),
                    "COCORAHS_CA_DAILY_PRECIP_GEOJSON": str(paths["ca_geojson"]),
                    "COCORAHS_CA_DAILY_PRECIP_SUMMARY_JSON": str(paths["ca_summary"]),
                    "COCORAHS_CONUS_DAILY_PRECIP_GEOJSON": str(paths["conus_geojson"]),
                    "COCORAHS_CONUS_DAILY_PRECIP_SUMMARY_JSON": str(paths["conus_summary"]),
                    "COCORAHS_PAGE_LIMIT": "5000",
                    "COCORAHS_MAX_PAGES_PER_STATE": "1",
                    "COCORAHS_REQUEST_PAUSE_SEC": "0",
                }
            )
            build = subprocess.run(
                [shutil.which("Rscript"), str(BUILDER)],
                cwd=REPO,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            output_exists = {name: path.exists() for name, path in paths.items()}
            parsed = {
                name: json.loads(path.read_text(encoding="utf-8"))
                for name, path in paths.items()
                if path.exists()
            }
            validator = None
            if all(output_exists.values()):
                validator = subprocess.run(
                    ["python3", str(VALIDATOR), "validate", "--root", str(root)],
                    cwd=REPO,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
            return {
                "build": build,
                "output_exists": output_exists,
                "parsed": parsed,
                "validator": validator,
            }

    def assert_valid_candidate(self, result: dict[str, object]) -> None:
        build = result["build"]
        validator = result["validator"]
        self.assertEqual(build.returncode, 0, build.stderr)
        self.assertIsNotNone(validator)
        self.assertEqual(validator.returncode, 0, validator.stderr)
        for key in ("ca_geojson", "conus_geojson"):
            for feature in result["parsed"][key]["features"]:
                station_name = feature["properties"]["stationName"]
                self.assertIsInstance(station_name, str)
                self.assertTrue(station_name.strip())

    @staticmethod
    def stable_feature(feature: dict[str, object]) -> dict[str, object]:
        feature = copy.deepcopy(feature)
        feature["properties"].pop("feedBuildTimeUtc")
        return feature

    def test_all_unusable_station_name_forms_are_omitted(self) -> None:
        records = [
            observation(1, "Valid station", state="CA"),
            observation(2, None),
            observation(3, ABSENT),
            observation(4, "NA"),
            observation(5, "NaN"),
            observation(6, ""),
            observation(7, " \t "),
        ]
        result = self.run_builder(records)
        self.assert_valid_candidate(result)
        summary = result["parsed"]["conus_summary"]
        self.assertEqual(summary["omitted_missing_station_name"], 6)
        self.assertEqual(summary["output_feature_count"], 1)

    def test_historical_failure_family_omits_one_retained_conus_record(self) -> None:
        valid = observation(1, "Valid station", state="CA")
        baseline = self.run_builder([valid])
        repaired = self.run_builder([valid, observation(2, None, state="CO")])
        self.assert_valid_candidate(baseline)
        self.assert_valid_candidate(repaired)

        conus = repaired["parsed"]["conus_geojson"]
        ca = repaired["parsed"]["ca_geojson"]
        self.assertEqual(len(conus["features"]), 1)
        self.assertEqual(len(ca["features"]), 1)
        self.assertEqual(
            self.stable_feature(conus["features"][0]),
            self.stable_feature(baseline["parsed"]["conus_geojson"]["features"][0]),
        )
        for summary_key in ("ca_summary", "conus_summary"):
            summary = repaired["parsed"][summary_key]
            self.assertEqual(summary["omitted_missing_station_name"], 1)
        self.assertIn("retained observations before filtering=2", repaired["build"].stderr)
        self.assertIn("omitted_missing_station_name=1", repaired["build"].stderr)
        self.assertIn("retained observations after filtering=1", repaired["build"].stderr)
        self.assertIn("threshold=10", repaired["build"].stderr)
        self.assertIn("stationNumber=CO-TEST-002", repaired["build"].stderr)

    def test_exactly_ten_invalid_records_are_allowed_once_before_split(self) -> None:
        records = [observation(1, "Valid station", state="CA")]
        records.extend(observation(index, None, state="CA") for index in range(2, 12))
        result = self.run_builder(records)
        self.assert_valid_candidate(result)
        for summary_key in ("ca_summary", "conus_summary"):
            summary = result["parsed"][summary_key]
            self.assertEqual(summary["omitted_missing_station_name"], 10)
            self.assertEqual(summary["output_feature_count"], 1)
        self.assertEqual(
            result["build"].stderr.count("stationNumber=CA-TEST-"),
            10,
        )

    def test_eleven_invalid_records_fail_before_candidate_files_are_written(self) -> None:
        records = [observation(1, "Valid station", state="CA")]
        records.extend(observation(index, None) for index in range(2, 13))
        result = self.run_builder(records)
        self.assertNotEqual(result["build"].returncode, 0)
        self.assertFalse(any(result["output_exists"].values()))
        self.assertIsNone(result["validator"])
        self.assertIn("omitted_missing_station_name=11; threshold=10", result["build"].stderr)
        self.assertIn("stationNumber=CO-TEST-011", result["build"].stderr)
        self.assertNotIn("stationNumber=CO-TEST-012", result["build"].stderr)


if __name__ == "__main__":
    unittest.main()
