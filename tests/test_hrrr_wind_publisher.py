#!/usr/bin/env python3
"""Deterministic fixtures for HRRR rolling-tree validation and reconciliation."""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import hrrr_wind_publisher as hrrr


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HrrrWindPublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.candidate = self.root / "candidate"
        self.canonical = self.root / "canonical"
        self.base = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
        self.generated = self.base + timedelta(minutes=20)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def spec(
        self,
        valid_offset: int,
        cycle_offset: int,
        marker: float = 1.0,
        requested_lead: int = 0,
        target_offset: int | None = None,
    ):
        valid = self.base + timedelta(hours=valid_offset)
        target = self.base + timedelta(hours=valid_offset if target_offset is None else target_offset)
        return valid, self.base + timedelta(hours=cycle_offset), marker, requested_lead, target

    @staticmethod
    def write_product(root: Path, generated: datetime, specs, *, failures=None) -> None:
        specs = list(specs)
        failures = list(failures or [])
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        entries = []
        for valid, cycle, marker, requested_lead, target_time in sorted(specs):
            forecast_hour = int((valid - cycle).total_seconds() / 3600)
            filename = f"hrrr_surface_wind_{cycle:%Y%m%dT%H}Z_f{forecast_hour:02d}.json"
            target = root / hrrr.TARGET_ROOT / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            fields = []
            for parameter, value in ((2, marker), (3, marker + 0.5)):
                fields.append(
                    {
                        "header": {
                            "parameterCategory": 2,
                            "parameterNumber": parameter,
                            "parameterNumberName": "U" if parameter == 2 else "V",
                            "parameterUnit": "m.s-1",
                            "nx": 2,
                            "ny": 1,
                            "lo1": -125.5,
                            "la1": 43.5,
                            "lo2": -112.0,
                            "la2": 31.0,
                            "dx": 0.05,
                            "dy": 0.05,
                            "refTime": stamp(cycle),
                            "forecastTime": forecast_hour,
                        },
                        "data": [value, value + 0.25],
                    }
                )
            target.write_text(json.dumps(fields, separators=(",", ":")) + "\n", encoding="utf-8")
            speeds = {"min": 1.0, "p50": 2.0, "p75": 3.0, "p90": 4.0, "p95": 5.0, "max": 6.0}
            entry = {
                "product_id": hrrr.DATA_PRODUCT_ID,
                "product": "NOAA/NCEP HRRR 10-m wind",
                "model": "NOAA/NCEP HRRR",
                "level": "10 m above ground",
                "variables": ["UGRD", "VGRD"],
                "requested_lead_hours": requested_lead,
                "target_valid_time_utc": stamp(target_time),
                "target_valid_time_local": stamp(target_time),
                "target_distance_minutes": abs((valid - target_time).total_seconds()) / 60,
                "model_cycle_utc": stamp(cycle),
                "model_cycle_local": stamp(cycle),
                "forecast_hour": forecast_hour,
                "forecast_hour_label": f"f{forecast_hour:02d}",
                "valid_time_utc": stamp(valid),
                "valid_time_local": stamp(valid),
                "valid_lag_minutes_at_build": (generated - valid).total_seconds() / 60,
                "relative_url": f"hrrr/surface/{filename}",
                "filename": filename,
                "file_bytes": target.stat().st_size,
                "source": "NOAA fixture",
                "source_request_url": "https://example.invalid/hrrr",
                "source_index_url": None,
                "source_grib_bytes": 10000,
                "domain": {
                    "id": "hydrologic_ca_adjacent",
                    "label": "Hydrologic California + adjacent basins",
                    "west": -125.5,
                    "east": -112.0,
                    "south": 31.0,
                    "north": 43.5,
                },
                "grid": {"nx": 2, "ny": 1, "resolution_degrees": 0.05, "cell_count": 2},
                "earth_relative_winds_confirmed": True,
                "vector_regrid_method": "fixture earth-relative regrid",
                "speed_ms": copy.deepcopy(speeds),
                "speed_mph": copy.deepcopy(speeds),
                "recommended_velocity_scale_ms": 8.0,
                "recommended_velocity_scale_mph": 17.9,
                "velocity_scale_reference": "fixture",
            }
            entries.append(entry)
        selected = min(
            entries,
            key=lambda entry: (
                abs((datetime.fromisoformat(entry["valid_time_utc"][:-1] + "+00:00") - generated).total_seconds()),
                datetime.fromisoformat(entry["valid_time_utc"][:-1] + "+00:00") > generated,
                -datetime.fromisoformat(entry["model_cycle_utc"][:-1] + "+00:00").timestamp(),
                entry["forecast_hour"],
            ),
        )
        target_base = generated.replace(minute=0, second=0, microsecond=0)
        manifest = {
            "product_id": hrrr.DATA_PRODUCT_ID,
            "product": "fixture",
            "feed_mode": "multi_target_time_set",
            "version": "RTW-HRRR002",
            "source": "NOAA fixture",
            "generated_utc": stamp(generated),
            "generated_local": stamp(generated),
            "browser_selection": {
                "supported_lead_hours": list(hrrr.SUPPORTED_LEADS),
                "recommended": True,
            },
            "legacy_latest": {
                "wind_json": hrrr.LATEST_PATH.as_posix(),
                "summary_json": hrrr.SUMMARY_PATH.as_posix(),
                "selected_entry": copy.deepcopy(selected),
            },
            "stale_after_hours": hrrr.STALE_AFTER_HOURS,
            "supported_browser_lead_hours": list(hrrr.SUPPORTED_LEADS),
            "target_base_utc": stamp(target_base),
            "target_failures": failures,
            "retain_past_hours": hrrr.RETAIN_PAST_HOURS,
            "retain_future_hours": hrrr.RETAIN_FUTURE_HOURS,
            "entry_count": len(entries),
            "entries": entries,
        }
        selected_target = root / hrrr.TARGET_ROOT / selected["filename"]
        build_results = []
        for lead in hrrr.SUPPORTED_LEADS:
            distance = min(
                abs(
                    (
                        datetime.fromisoformat(entry["valid_time_utc"][:-1] + "+00:00")
                        - (generated + timedelta(hours=lead))
                    ).total_seconds()
                )
                / 3600
                for entry in entries
            )
            build_results.append(
                {
                    "lead_hours": lead,
                    "nearest_distance_hours": distance,
                    "available_within_2_hours": distance <= 2,
                }
            )
        lag_minutes = (generated - datetime.fromisoformat(selected["valid_time_utc"][:-1] + "+00:00")).total_seconds() / 60
        summary = {
            "product_id": hrrr.DATA_PRODUCT_ID,
            "product": "fixture",
            "feed_mode": "multi_target_time_set_with_legacy_latest",
            "version": "RTW-HRRR002",
            "status": "success",
            "source": selected["source"],
            "model_cycle_utc": selected["model_cycle_utc"],
            "model_cycle_local": selected["model_cycle_local"],
            "forecast_hour": selected["forecast_hour"],
            "forecast_hour_label": selected["forecast_hour_label"],
            "valid_time_utc": selected["valid_time_utc"],
            "valid_time_local": selected["valid_time_local"],
            "build_time_utc": stamp(generated),
            "build_time_local": stamp(generated),
            "local_time_zone": "UTC",
            "valid_lag_minutes_at_build": lag_minutes,
            "valid_time_age_hours": lag_minutes / 60,
            "stale_after_hours": hrrr.STALE_AFTER_HOURS,
            "is_stale": lag_minutes / 60 > hrrr.STALE_AFTER_HOURS,
            "supported_browser_lead_hours": list(hrrr.SUPPORTED_LEADS),
            "target_base_utc": stamp(target_base),
            "target_build_results": build_results,
            "target_failures": failures,
            "domain": copy.deepcopy(selected["domain"]),
            "grid": copy.deepcopy(selected["grid"]),
            "earth_relative_winds_confirmed": True,
            "vector_regrid_method": selected["vector_regrid_method"],
            "speed_ms": copy.deepcopy(selected["speed_ms"]),
            "speed_mph": copy.deepcopy(selected["speed_mph"]),
            "recommended_velocity_scale_ms": selected["recommended_velocity_scale_ms"],
            "recommended_velocity_scale_mph": selected["recommended_velocity_scale_mph"],
            "velocity_scale_reference": selected["velocity_scale_reference"],
            "selected_entry": copy.deepcopy(selected),
            "available_entry_count": len(entries),
            "retain_past_hours": hrrr.RETAIN_PAST_HOURS,
            "retain_future_hours": hrrr.RETAIN_FUTURE_HOURS,
            "output_json_bytes": selected_target.stat().st_size,
            "output_files": {
                "wind_json": hrrr.LATEST_PATH.as_posix(),
                "summary_json": hrrr.SUMMARY_PATH.as_posix(),
                "manifest_json": hrrr.MANIFEST_PATH.as_posix(),
                "time_set_directory": hrrr.TARGET_ROOT.as_posix(),
            },
        }
        for path, value in ((hrrr.MANIFEST_PATH, manifest), (hrrr.SUMMARY_PATH, summary)):
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        latest = root / hrrr.LATEST_PATH
        latest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(selected_target, latest)

    def state(self, root: Path) -> hrrr.ProductState:
        return hrrr.validate_product(root)

    def valid_offsets(self, root: Path) -> list[int]:
        return [int((entry.valid - self.base).total_seconds() / 3600) for entry in self.state(root).entries]

    def test_valid_product_and_semantic_key(self):
        self.write_product(self.candidate, self.generated, [self.spec(0, -1), self.spec(6, 0, requested_lead=6), self.spec(12, 0, requested_lead=12)])
        self.assertEqual(len(self.state(self.candidate).entries), 3)
        self.assertEqual(len(hrrr.semantic_key(self.candidate)), 64)

    def test_one_hour_window_movement_prunes_expired_and_adds_new(self):
        self.write_product(self.canonical, self.generated, [self.spec(i, i - 1) for i in range(-3, 15)])
        self.write_product(self.candidate, self.generated + timedelta(hours=1), [self.spec(i, i - 1, 1.0 if i < 15 else 2.0) for i in range(-2, 16)])
        self.assertEqual(hrrr.reconcile(self.candidate, self.canonical)[0], "new")
        self.assertEqual(self.valid_offsets(self.canonical), list(range(-2, 16)))

    def test_newer_candidate_cycle_wins_same_valid_time(self):
        self.write_product(self.canonical, self.generated, [self.spec(0, -2, 1.0)])
        self.write_product(self.candidate, self.generated + timedelta(hours=1), [self.spec(0, -1, 2.0)])
        hrrr.reconcile(self.candidate, self.canonical)
        self.assertEqual(self.state(self.canonical).entries[0].cycle, self.base - timedelta(hours=1))

    def test_canonical_newer_cycle_wins_same_valid_time(self):
        self.write_product(self.canonical, self.generated, [self.spec(0, -1, 2.0)])
        self.write_product(self.candidate, self.generated + timedelta(hours=1), [self.spec(0, -2, 1.0)])
        old = (self.canonical / self.state(self.canonical).entries[0].relative_path).read_bytes()
        hrrr.reconcile(self.candidate, self.canonical)
        final = self.state(self.canonical).entries[0]
        self.assertEqual(final.cycle, self.base - timedelta(hours=1))
        self.assertEqual((self.canonical / final.relative_path).read_bytes(), old)

    def test_partial_candidate_preserves_fresh_canonical_coverage(self):
        self.write_product(self.canonical, self.generated, [self.spec(0, -1), self.spec(6, 0, requested_lead=6), self.spec(12, 0, requested_lead=12)])
        self.write_product(self.candidate, self.generated + timedelta(hours=1), [self.spec(1, 0, 2.0)])
        hrrr.reconcile(self.candidate, self.canonical)
        self.assertEqual(self.valid_offsets(self.canonical), [0, 1, 6, 12])

    def test_reusable_canonical_target_bytes_are_preserved(self):
        self.write_product(self.canonical, self.generated, [self.spec(0, -1), self.spec(6, 0, requested_lead=6)])
        reusable = self.state(self.canonical).entries[1]
        original = (self.canonical / reusable.relative_path).read_bytes()
        self.write_product(self.candidate, self.generated + timedelta(hours=1), [self.spec(1, 0, 2.0)])
        hrrr.reconcile(self.candidate, self.canonical)
        self.assertEqual((self.canonical / reusable.relative_path).read_bytes(), original)

    def test_target_still_required_by_fresh_main_cannot_be_deleted(self):
        self.write_product(self.canonical, self.generated, [self.spec(0, -1), self.spec(5, 0, requested_lead=6)])
        self.write_product(self.candidate, self.generated + timedelta(hours=1), [self.spec(1, 0, 2.0)])
        hrrr.reconcile(self.candidate, self.canonical)
        self.assertEqual(self.valid_offsets(self.canonical), [0, 1, 5])

    def test_legitimate_outside_window_pruning(self):
        self.write_product(self.canonical, self.generated, [self.spec(-3, -4), self.spec(0, -1)])
        self.write_product(self.candidate, self.generated + timedelta(hours=1), [self.spec(1, 0, 2.0)])
        hrrr.reconcile(self.candidate, self.canonical)
        self.assertEqual(self.valid_offsets(self.canonical), [0, 1])

    def test_same_complete_state_is_noop(self):
        self.write_product(self.candidate, self.generated, [self.spec(0, -1)])
        shutil.copytree(self.candidate, self.canonical)
        self.assertEqual(hrrr.reconcile(self.candidate, self.canonical)[0], "same")

    def test_stale_candidate_is_noop(self):
        self.write_product(self.canonical, self.generated + timedelta(hours=1), [self.spec(1, 0, 2.0)])
        self.write_product(self.candidate, self.generated, [self.spec(0, -1, 1.0)])
        before = hrrr.semantic_key(self.canonical)
        self.assertEqual(hrrr.reconcile(self.candidate, self.canonical)[0], "stale")
        self.assertEqual(hrrr.semantic_key(self.canonical), before)

    def test_absent_canonical_accepts_complete_candidate(self):
        self.write_product(self.candidate, self.generated, [self.spec(0, -1)])
        self.canonical.mkdir()
        self.assertEqual(hrrr.reconcile(self.candidate, self.canonical)[0], "new")
        self.assertEqual(hrrr.semantic_key(self.candidate), hrrr.semantic_key(self.canonical))

    def test_complete_candidate_replaces_old_window(self):
        self.write_product(self.canonical, self.generated, [self.spec(-3, -4)])
        self.write_product(self.candidate, self.generated + timedelta(hours=2), [self.spec(2, 1, 2.0), self.spec(8, 2, 2.0, 6)])
        hrrr.reconcile(self.candidate, self.canonical)
        self.assertEqual(hrrr.semantic_key(self.candidate), hrrr.semantic_key(self.canonical))

    def test_equal_generation_conflicting_state_fails_closed(self):
        self.write_product(self.canonical, self.generated, [self.spec(0, -1, 1.0)])
        self.write_product(self.candidate, self.generated, [self.spec(0, -1, 2.0)])
        with self.assertRaises(hrrr.ProductError):
            hrrr.reconcile(self.candidate, self.canonical)

    def test_same_cycle_same_valid_conflicting_bytes_fail_closed(self):
        self.write_product(self.canonical, self.generated, [self.spec(0, -1, 1.0)])
        self.write_product(self.candidate, self.generated + timedelta(hours=1), [self.spec(0, -1, 2.0)])
        with self.assertRaises(hrrr.ProductError):
            hrrr.reconcile(self.candidate, self.canonical)

    def test_mixed_cycles_remain_valid(self):
        self.write_product(self.candidate, self.generated, [self.spec(0, -1), self.spec(6, -2, requested_lead=6), self.spec(12, 0, requested_lead=12)])
        self.assertEqual(len({entry.cycle for entry in self.state(self.candidate).entries}), 3)

    def test_manifest_reference_to_missing_target_is_rejected(self):
        self.write_product(self.candidate, self.generated, [self.spec(0, -1)])
        (self.candidate / self.state(self.candidate).entries[0].relative_path).unlink()
        with self.assertRaises(hrrr.ProductError):
            self.state(self.candidate)

    def test_orphan_target_is_rejected(self):
        self.write_product(self.candidate, self.generated, [self.spec(0, -1)])
        (self.candidate / hrrr.TARGET_ROOT / "orphan.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaises(hrrr.ProductError):
            self.state(self.candidate)

    def test_entry_outside_window_is_rejected(self):
        self.write_product(self.candidate, self.generated, [self.spec(-4, -5)])
        with self.assertRaises(hrrr.ProductError):
            self.state(self.candidate)

    def test_target_distance_over_consumer_tolerance_is_rejected(self):
        self.write_product(self.candidate, self.generated, [self.spec(0, -1, target_offset=3)])
        with self.assertRaises(hrrr.ProductError):
            self.state(self.candidate)

    def test_target_failures_are_preserved_during_partial_reconciliation(self):
        self.write_product(self.canonical, self.generated, [self.spec(0, -1), self.spec(6, 0, requested_lead=6)])
        self.write_product(self.candidate, self.generated + timedelta(hours=1), [self.spec(1, 0, 2.0)], failures=["+6h target failed: fixture"])
        hrrr.reconcile(self.candidate, self.canonical)
        self.assertEqual(self.state(self.canonical).manifest["target_failures"], ["+6h target failed: fixture"])

    def test_reconciliation_changes_only_owned_hrrr_paths(self):
        self.write_product(self.canonical, self.generated, [self.spec(0, -1)])
        unrelated = self.canonical / "docs/data/wind/gfs/keep.json"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("keep\n", encoding="utf-8")
        self.write_product(self.candidate, self.generated + timedelta(hours=1), [self.spec(1, 0, 2.0)])
        hrrr.reconcile(self.candidate, self.canonical)
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep\n")

    def test_canonical_advancement_while_prepare_runs_wins(self):
        self.write_product(self.candidate, self.generated + timedelta(hours=1), [self.spec(1, -1, 1.0)])
        self.write_product(self.canonical, self.generated, [self.spec(1, 0, 2.0)])
        hrrr.reconcile(self.candidate, self.canonical)
        self.assertEqual(self.state(self.canonical).entries[0].cycle, self.base)

    def test_malformed_summary_fails_closed(self):
        self.write_product(self.candidate, self.generated, [self.spec(0, -1)])
        (self.candidate / hrrr.SUMMARY_PATH).write_text("{}\n", encoding="utf-8")
        with self.assertRaises(hrrr.ProductError):
            self.state(self.candidate)

    def test_schema2_callback_protocol_validates_reconciles_and_revalidates(self):
        self.write_product(self.canonical, self.generated, [self.spec(0, -1)])
        self.write_product(
            self.candidate,
            self.generated + timedelta(hours=1),
            [self.spec(1, 0, 2.0)],
        )
        metadata = self.root / "candidate-metadata.json"
        metadata.write_text(
            json.dumps(
                {
                    "semantic_key": {
                        "type": "hrrr_rolling_state_sha256_v1",
                        "value": hrrr.semantic_key(self.candidate),
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result = self.root / "reconcile-result.json"
        common = {
            "BRIM_PUBLISH_CANDIDATE_ROOT": str(self.candidate),
            "BRIM_PUBLISH_WORKTREE": str(self.canonical),
            "BRIM_PUBLISH_PRODUCT_ID": hrrr.PRODUCT_ID,
            "BRIM_PUBLISH_METADATA": str(metadata),
            "BRIM_PUBLISH_RESULT": str(result),
        }
        with mock.patch.dict(os.environ, {**common, "BRIM_PUBLISH_PHASE": "validate-candidate"}):
            self.assertEqual(hrrr._callback(), 0)
        with mock.patch.dict(os.environ, {**common, "BRIM_PUBLISH_PHASE": "reconcile"}):
            self.assertEqual(hrrr._callback(), 0)
        self.assertEqual(json.loads(result.read_text())["candidate_state"], "new")
        with mock.patch.dict(os.environ, {**common, "BRIM_PUBLISH_PHASE": "validate-staged"}):
            self.assertEqual(hrrr._callback(), 0)


if __name__ == "__main__":
    unittest.main()
