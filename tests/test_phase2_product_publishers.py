#!/usr/bin/env python3
"""Offline validation and reconciliation tests for Phase 2 product callbacks."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
DELTA_CALLBACK = REPO / "scripts" / "delta_ops_publisher.py"
SCAN_CALLBACK = REPO / "scripts" / "scan_soil_moisture_publisher.py"
PUBLISHER = REPO / "scripts" / "main_publisher.py"
DELTA_PATHS = (
    Path("docs/data/delta_ops_daily_summary.json"),
    Path("docs/data/delta_ops_daily_summary_features.geojson"),
    Path("docs/data/delta_ops_daily_summary_summary.json"),
    Path("docs/data/delta_ops_x2_reference.geojson"),
)
SCAN_PATHS = (
    Path("docs/data/scan_soil_moisture_latest.geojson"),
    Path("docs/data/scan_soil_moisture_latest_summary.json"),
    Path("docs/data/scan_soil_moisture_current_wy_trace.csv"),
    Path("docs/data/scan_soil_moisture_current_wy_trace_summary.json"),
    Path("docs/data/scan_depth_style.csv"),
)
TRACE_FIELDS = (
    "station_uid",
    "station_name",
    "site_code",
    "depth_in",
    "depth_label",
    "depth_order",
    "depth_color_hex",
    "water_year",
    "water_day",
    "obs_date",
    "obs_datetime_local",
    "sms_pct",
    "sensor_count",
    "sensor_id",
)
STYLE_FIELDS = ("depth_in", "depth_label", "depth_order", "depth_color_hex", "depth_role")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8")


def point(properties: dict[str, object], longitude: float, latitude: float) -> dict[str, object]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
        "properties": properties,
    }


def write_delta_product(root: Path, report_date: str, build_time: str) -> None:
    source_url = "https://example.invalid/delta.pdf"
    source_name = "DWR Delta Operations Daily Summary"
    features = []
    for index in range(10):
        features.append(
            point(
                {
                    "feature_key": f"metric_{index}",
                    "display_name": f"Metric {index}",
                    "feature_type": "metric",
                    "metric_name": f"Metric {index}",
                    "units": "cfs",
                    "label_text": f"Metric {index}: {index}",
                    "value_raw": str(index),
                    "value_numeric": index,
                    "source_name": source_name,
                    "source_url": source_url,
                    "preliminary_notice": "PRELIMINARY",
                    "report_date": report_date,
                    "feed_build_time_utc": build_time,
                },
                -121.0 + index / 100,
                38.0 + index / 100,
            )
        )
    values = {
        "source_name": source_name,
        "source_url": source_url,
        "report_date": report_date,
        "feed_build_time_utc": build_time,
        "values": {"metric": "1 cfs"},
    }
    summary = {
        "source_name": source_name,
        "source_url": source_url,
        "report_date": report_date,
        "feed_build_time_utc": build_time,
        "feature_count": len(features),
        "x2_lookup_added": True,
    }
    x2 = {
        "type": "FeatureCollection",
        "features": [
            point(
                {
                    "feature_key": "x2_reference",
                    "display_name": "X2 75 km",
                    "feature_type": "x2_reference",
                    "source_name": "BRIM X2 lookup",
                    "source_url": "reference/x2.shp",
                    "river_km": 75,
                },
                -121.5,
                38.1,
            )
        ],
    }
    write_json(root / DELTA_PATHS[0], values)
    write_json(root / DELTA_PATHS[1], {"type": "FeatureCollection", "features": features})
    write_json(root / DELTA_PATHS[2], summary)
    write_json(root / DELTA_PATHS[3], x2)


def write_scan_product(root: Path, build_time: str, *, station_name: str = "Station") -> None:
    current_water_year = 2026
    start_date = "2025-10-01"
    features = []
    trace_rows = []
    for index in range(10):
        site_code = 1000 + index
        uid = f"NRCS_scan_{site_code}"
        features.append(
            point(
                {
                    "station_uid": uid,
                    "station_name": f"{station_name} {index}",
                    "site_code": site_code,
                    "source_url": "https://example.invalid/scan",
                    "feed_build_time_utc": build_time,
                    "display_sms_pct": 20 + index,
                    "display_depth_in": 8,
                    "display_obs_datetime_utc": "2026-08-18T08:00:00Z",
                },
                -120.0 + index / 100,
                39.0 + index / 100,
            )
        )
        trace_rows.append(
            {
                "station_uid": uid,
                "station_name": f"{station_name} {index}",
                "site_code": site_code,
                "depth_in": 8,
                "depth_label": "8 in",
                "depth_order": 3,
                "depth_color_hex": "#009E73",
                "water_year": current_water_year,
                "water_day": 1,
                "obs_date": start_date,
                "obs_datetime_local": "2025-10-01 01:00 PM PDT",
                "sms_pct": 20 + index,
                "sensor_count": 1,
                "sensor_id": "SMS.I_8",
            }
        )
    summary = {
        "feed_build_time_utc": build_time,
        "station_index_rows": 10,
        "features_written": 10,
        "stations_with_any_current_wy_sms": 10,
        "stations_without_current_wy_sms": 0,
        "depth_rows_latest": 10,
        "current_wy_trace_rows": 10,
        "current_water_year": current_water_year,
        "current_wy_start_date": start_date,
        "depth_style_output_csv": "docs/data/scan_depth_style.csv",
    }
    trace_summary = {
        "feed_build_time_utc": build_time,
        "trace_rows": 10,
        "stations": 10,
        "current_water_year": current_water_year,
        "current_wy_start_date": start_date,
        "depth_style_output_csv": "docs/data/scan_depth_style.csv",
    }
    write_json(
        root / SCAN_PATHS[0],
        {
            "type": "FeatureCollection",
            "feed_build_time_utc": build_time,
            "current_water_year": current_water_year,
            "current_wy_start_date": start_date,
            "features": features,
        },
    )
    write_json(root / SCAN_PATHS[1], summary)
    write_json(root / SCAN_PATHS[3], trace_summary)
    for relative_path, fieldnames, rows in (
        (SCAN_PATHS[2], TRACE_FIELDS, trace_rows),
        (
            SCAN_PATHS[4],
            STYLE_FIELDS,
            [
                {
                    "depth_in": 8,
                    "depth_label": "8 in",
                    "depth_order": 3,
                    "depth_color_hex": "#009E73",
                    "depth_role": "default-display",
                }
            ],
        ),
    ):
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)


class CallbackCase(unittest.TestCase):
    callback: Path
    product_id: str
    semantic_type: str
    paths: tuple[Path, ...]

    def run_callback(
        self,
        *,
        phase: str,
        candidate: Path,
        worktree: Path,
        metadata: Path,
        result: Path,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.update(
            {
                "BRIM_PUBLISH_PHASE": phase,
                "BRIM_PUBLISH_CANDIDATE_ROOT": str(candidate),
                "BRIM_PUBLISH_WORKTREE": str(worktree),
                "BRIM_PUBLISH_METADATA": str(metadata),
                "BRIM_PUBLISH_RESULT": str(result),
                "BRIM_PUBLISH_PRODUCT_ID": self.product_id,
            }
        )
        return subprocess.run(
            [sys.executable, str(self.callback)],
            cwd=REPO,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def metadata(self, root: Path, value: str) -> Path:
        path = root / "candidate-metadata.json"
        write_json(path, {"semantic_key": {"type": self.semantic_type, "value": value}})
        return path

    def validate(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.callback), "validate", "--root", str(root)],
            cwd=REPO,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def assert_copied(self, candidate: Path, worktree: Path) -> None:
        for relative_path in self.paths:
            self.assertEqual(
                (worktree / relative_path).read_bytes(),
                (candidate / relative_path).read_bytes(),
            )

    def assert_extra_candidate_rejected(self, candidate: Path, semantic: str) -> None:
        unexpected = candidate / "docs/data/unexpected.json"
        write_json(unexpected, {"unexpected": True})
        command = [
            sys.executable,
            str(PUBLISHER),
            "prepare",
            "--candidate-root",
            str(candidate),
            "--output",
            str(candidate.parent / "candidate-metadata.json"),
            "--product-id",
            self.product_id,
            "--semantic-key-type",
            self.semantic_type,
            "--semantic-key",
            semantic,
            "--source-event-sha",
            "0" * 40,
        ]
        for path in self.paths:
            command.extend(("--allowlist", path.as_posix()))
        result = subprocess.run(
            command,
            cwd=REPO,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("inventory does not exactly match allowlist", result.stderr)


class DeltaOpsPublisherTests(CallbackCase):
    callback = DELTA_CALLBACK
    product_id = "delta-ops-daily-summary"
    semantic_type = "report_date"
    paths = DELTA_PATHS

    def test_new_same_and_stale_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            write_delta_product(candidate, "2026-08-18", "2026-08-18T12:00:00Z")
            write_delta_product(worktree, "2026-08-17", "2026-08-17T12:00:00Z")
            unrelated = worktree / "docs/data/unrelated.json"
            write_json(unrelated, {"preserve": True})
            result_path = root / "result.json"
            result = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, "2026-08-18"),
                result=result_path,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result_path.read_text())["candidate_state"], "new")
            self.assert_copied(candidate, worktree)
            self.assertEqual(json.loads(unrelated.read_text()), {"preserve": True})

            write_delta_product(candidate, "2026-08-18", "2026-08-18T13:00:00Z")
            before = [(worktree / path).read_bytes() for path in self.paths]
            result = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, "2026-08-18"),
                result=result_path,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result_path.read_text())["candidate_state"], "same")
            self.assertEqual(before, [(worktree / path).read_bytes() for path in self.paths])

            write_delta_product(candidate, "2026-08-16", "2026-08-16T12:00:00Z")
            result = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, "2026-08-16"),
                result=result_path,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result_path.read_text())["candidate_state"], "stale")

    def test_malformed_partial_and_semantic_mismatch_fail_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            write_delta_product(candidate, "2026-08-18", "2026-08-18T12:00:00Z")
            write_delta_product(worktree, "2026-08-17", "2026-08-17T12:00:00Z")
            (candidate / DELTA_PATHS[3]).unlink()
            self.assertNotEqual(self.validate(candidate).returncode, 0)
            write_delta_product(candidate, "2026-08-18", "2026-08-18T12:00:00Z")
            write_json(worktree / DELTA_PATHS[2], {"malformed": True})
            malformed = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, "2026-08-18"),
                result=root / "result.json",
            )
            self.assertNotEqual(malformed.returncode, 0)
            mismatch = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=root / "empty",
                metadata=self.metadata(root, "2026-08-19"),
                result=root / "result.json",
            )
            self.assertNotEqual(mismatch.returncode, 0)

    def test_unexpected_candidate_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "candidate"
            write_delta_product(candidate, "2026-08-18", "2026-08-18T12:00:00Z")
            self.assert_extra_candidate_rejected(candidate, "2026-08-18")


class ScanPublisherTests(CallbackCase):
    callback = SCAN_CALLBACK
    product_id = "scan-soil-moisture-latest"
    semantic_type = "feed_build_time_utc"
    paths = SCAN_PATHS

    def test_new_same_and_stale_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            write_scan_product(candidate, "2026-08-18T12:00:00Z")
            write_scan_product(worktree, "2026-08-18T09:00:00Z")
            unrelated = worktree / "docs/data/unrelated.json"
            write_json(unrelated, {"preserve": True})
            result_path = root / "result.json"
            result = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, "2026-08-18T12:00:00Z"),
                result=result_path,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result_path.read_text())["candidate_state"], "new")
            self.assert_copied(candidate, worktree)
            self.assertEqual(json.loads(unrelated.read_text()), {"preserve": True})

            result = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, "2026-08-18T12:00:00Z"),
                result=result_path,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result_path.read_text())["candidate_state"], "same")

            write_scan_product(candidate, "2026-08-18T08:00:00Z")
            result = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, "2026-08-18T08:00:00Z"),
                result=result_path,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result_path.read_text())["candidate_state"], "stale")

    def test_same_time_different_bytes_is_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            build_time = "2026-08-18T12:00:00Z"
            write_scan_product(candidate, build_time, station_name="Candidate")
            write_scan_product(worktree, build_time, station_name="Canonical")
            result = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, build_time),
                result=root / "result.json",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ambiguous overwrite", result.stderr)

    def test_malformed_partial_and_semantic_mismatch_fail_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            build_time = "2026-08-18T12:00:00Z"
            write_scan_product(candidate, build_time)
            write_scan_product(worktree, "2026-08-18T09:00:00Z")
            (candidate / SCAN_PATHS[2]).unlink()
            self.assertNotEqual(self.validate(candidate).returncode, 0)
            write_scan_product(candidate, build_time)
            write_json(worktree / SCAN_PATHS[1], {"malformed": True})
            malformed = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, build_time),
                result=root / "result.json",
            )
            self.assertNotEqual(malformed.returncode, 0)
            mismatch = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=root / "empty",
                metadata=self.metadata(root, "2026-08-18T13:00:00Z"),
                result=root / "result.json",
            )
            self.assertNotEqual(mismatch.returncode, 0)

    def test_unexpected_candidate_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "candidate"
            build_time = "2026-08-18T12:00:00Z"
            write_scan_product(candidate, build_time)
            self.assert_extra_candidate_rejected(candidate, build_time)


if __name__ == "__main__":
    unittest.main()
