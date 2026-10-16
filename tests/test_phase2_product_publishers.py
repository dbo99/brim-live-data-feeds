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
    from datetime import date, timedelta
    yesterday = (date.fromisoformat(report_date) - timedelta(days=1)).isoformat()
    provenance = {
        "source_name": "DWR Delta Operations Daily Summary",
        "source_url": "https://example.invalid/delta.pdf", "source_kind": "pdf", "review_only": False,
        "report_date": report_date, "feed_build_time_utc": build_time,
        "report_date_source": "parsed_from_dwr_pdf_executive_operations_summary_on_line",
        "x2_position_date_source": "report_date_minus_1_day_because_pdf_labels_x2_as_yesterday",
        "x2_position_date": yesterday, "x2_position_relation": ">",
        "preliminary_notice": "PRELIMINARY",
    }
    raw = {
        "clifton_court_inflow": "1,000 cfs", "jones_pumping_plant": "2,700 cfs",
        "total_delta_inflow": "~ 11,833 cfs", "sacramento_river": "9,785 cfs",
        "san_joaquin_river": "1,084 cfs", "delta_conditions": "Balanced",
        "cross_channel_gates_percent_open": "100%", "outflow_index": "~ 6,100 cfs",
        "percent_inflow_diverted": "30.2 (3-day avg)", "x2_position_yesterday": "> 81 km",
        "controlling_factors": "Delta WQ", "omr_index_daily_value": "-3,500 cfs",
        "shasta_storage": "2,376 TAF", "folsom_storage": "404 TAF", "oroville_storage": "1,533 TAF",
        "san_luis_total_storage": "1,465 TAF", "san_luis_swp_share": "983 TAF",
        "east_side_streams": "964 cfs", "export_total_cfs": "3,700 cfs",
        "export_total_kaf_day": "7.3 kaf/day", "outflow_kaf_day": "12.1 kaf/day",
        "cvp_export_kaf_day": "5.4 kaf/day", "swp_export_kaf_day": "2.0 kaf/day",
        "san_luis_cvp_share": "482 TAF",
    }
    specs = [
        ("cross_channel_gates", "cross_channel_gates_percent_open", 100, "percent"),
        ("jones_cvp_exports", "jones_pumping_plant", 2700, "cfs"),
        ("banks_swp_exports", "clifton_court_inflow", 1000, "cfs"),
        ("delta_outflow_index", "outflow_index", 6100, "cfs"),
        ("percent_inflow_diverted", "percent_inflow_diverted", 30.2, "percent_3day_avg"),
        ("omr_index", "omr_index_daily_value", -3500, "cfs"),
        ("sacramento_freeport", "sacramento_river", 9785, "cfs"),
        ("san_joaquin_vernalis", "san_joaquin_river", 1084, "cfs"),
        ("total_delta_inflow", "east_side_streams", 964, "cfs"),
        ("delta_conditions", "delta_conditions", None, "status"),
        ("controlling_factors", "controlling_factors", None, "status"),
        ("san_luis_reservoir", "san_luis_total_storage", 1465, "TAF"),
        ("x2_position_current", "x2_position_yesterday", 81, "km"),
    ]
    features = [point({**provenance, "feature_key": key, "display_name": key,
                       "feature_type": "metric", "metric_name": key, "units": unit,
                       "label_text": raw[source], "value_raw": raw[source], "value_numeric": number},
                      -121.5, 38.1) for key, source, number, unit in specs]
    values = {**provenance, "values": raw}
    summary = {**provenance, "feature_count": 13, "x2_lookup_added": True, "x2_position_km": 81,
               "parser_note": "Automated BRIM daily parser for DWR Delta Operations Daily Summary PDF; values are preliminary and subject to revision.",
               "cvp_export_cfs": 2700, "swp_export_cfs": 1000, "export_total_cfs": 3700,
               "cvp_export_kaf_day": 2700 * 1.98347 / 1000,
               "swp_export_kaf_day": 1000 * 1.98347 / 1000,
               "export_total_kaf_day": 3700 * 1.98347 / 1000,
               "outflow_kaf_day": 6100 * 1.98347 / 1000, "east_side_streams_cfs": 964}
    x2 = {"type": "FeatureCollection", "features": [point({
        "feature_key": "x2_reference", "display_name": "X2 81 km", "feature_type": "x2_reference",
        "source_name": "BRIM X2 lookup", "source_url": "reference/x2.shp", "river_km": 81}, -121.5, 38.1)]}
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

    def test_exact_keys_units_live_values_and_provenance_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for mutation in ("arbitrary_key", "unit", "null_live", "nan", "missing_provenance", "derived_total", "x2_date", "missing_status_numeric", "parser_provenance", "derived_text"):
                with self.subTest(mutation=mutation):
                    write_delta_product(root, "2026-08-18", "2026-08-18T12:00:00Z")
                    geo = json.loads((root / DELTA_PATHS[1]).read_text())
                    summary = json.loads((root / DELTA_PATHS[2]).read_text())
                    values = json.loads((root / DELTA_PATHS[0]).read_text())
                    first = geo["features"][0]["properties"]
                    if mutation == "arbitrary_key": first["feature_key"] = "metric_0"
                    if mutation == "unit": first["units"] = "cfs"
                    if mutation == "null_live": first["value_numeric"] = None
                    if mutation == "nan": first["value_numeric"] = float("nan")
                    if mutation == "missing_provenance": summary.pop("source_kind")
                    if mutation == "derived_total": summary["export_total_cfs"] += 1
                    if mutation == "x2_date": summary["x2_position_date"] = "2026-08-18"
                    if mutation == "missing_status_numeric": geo["features"][9]["properties"].pop("value_numeric")
                    if mutation == "parser_provenance": summary["parser_note"] = "parsed from email"
                    if mutation == "derived_text": values["values"]["export_total_kaf_day"] = "73 kaf/day"
                    write_json(root / DELTA_PATHS[0], values)
                    write_json(root / DELTA_PATHS[1], geo)
                    write_json(root / DELTA_PATHS[2], summary)
                    self.assertNotEqual(self.validate(root).returncode, 0)

    def test_new_candidate_replaces_legacy_percentage_defect_without_bypass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, prior = root / "candidate", root / "prior"
            write_delta_product(candidate, "2026-08-18", "2026-08-18T12:00:00Z")
            write_delta_product(prior, "2026-08-17", "2026-08-17T12:00:00Z")
            for path in DELTA_PATHS[:3]:
                document = json.loads((prior / path).read_text())
                objects = [document] if "features" not in document else [f["properties"] for f in document["features"]]
                for value in objects:
                    value.pop("source_kind", None)
                    value.pop("review_only", None)
                    if value.get("feature_key") == "percent_inflow_diverted":
                        value["value_numeric"] = None
                        value["label_text"] = "30.2 (3-day avg) (3-day avg)"
                write_json(prior / path, document)
            # Exactly these bytes are forbidden as a new candidate.
            self.assertNotEqual(self.validate(prior).returncode, 0)
            result = self.run_callback(phase="reconcile", candidate=candidate, worktree=prior,
                                       metadata=self.metadata(root, "2026-08-18"), result=root / "result.json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_copied(candidate, prior)

    def test_retained_public_product_can_be_replaced_in_private_scratch(self) -> None:
        import shutil
        from datetime import date, timedelta
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prior, candidate = root / "prior", root / "candidate"
            for path in DELTA_PATHS:
                (prior / path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO / path, prior / path)
            day = (date.fromisoformat(json.loads((prior / DELTA_PATHS[2]).read_text())["report_date"]) + timedelta(days=1)).isoformat()
            write_delta_product(candidate, day, day + "T12:00:00Z")
            result = self.run_callback(phase="reconcile", candidate=candidate, worktree=prior,
                                       metadata=self.metadata(root, day), result=root / "result.json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_copied(candidate, prior)

    def test_review_candidates_and_corrupt_staged_bytes_cannot_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, prior = root / "candidate", root / "prior"
            write_delta_product(candidate, "2026-08-18", "2026-08-18T12:00:00Z")
            write_delta_product(prior, "2026-08-17", "2026-08-17T12:00:00Z")
            before = [(prior / path).read_bytes() for path in DELTA_PATHS]
            for path in DELTA_PATHS[:3]:
                document = json.loads((candidate / path).read_text())
                for item in ([f["properties"] for f in document["features"]] if "features" in document else [document]):
                    item["review_only"] = True
                write_json(candidate / path, document)
            result = self.run_callback(phase="reconcile", candidate=candidate, worktree=prior,
                                       metadata=self.metadata(root, "2026-08-18"), result=root / "result.json")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(before, [(prior / path).read_bytes() for path in DELTA_PATHS])
            (prior / DELTA_PATHS[3]).unlink()
            checked = self.run_callback(phase="validate-staged", candidate=candidate, worktree=prior,
                                        metadata=self.metadata(root, "2026-08-18"), result=root / "result.json")
            self.assertNotEqual(checked.returncode, 0)

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
