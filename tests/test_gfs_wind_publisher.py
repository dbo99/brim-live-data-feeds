#!/usr/bin/env python3
"""Deterministic fixtures for GFS rolling-tree validation and reconciliation."""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import gfs_wind_publisher as gfs


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GfsWindPublisherTests(unittest.TestCase):
    SPARSE_OFFSETS = [-3, -2, -1, 0, 1, 2, 3, 21, 24, 27, 30]

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.candidate = self.root / "candidate"
        self.canonical = self.root / "canonical"
        self.base = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def spec(self, valid_offset: int, cycle_offset: int, marker: float = 1.0):
        return (self.base + timedelta(hours=valid_offset), self.base + timedelta(hours=cycle_offset), marker)

    @staticmethod
    def write_product(root: Path, generated: datetime, specs, *, offsets=None) -> None:
        specs = list(specs)
        anchor = generated.replace(minute=0, second=0, microsecond=0)
        if offsets is None:
            offset_values = [
                (valid - anchor).total_seconds() / 3600
                for valid, _, _ in specs
            ]
            if any(not value.is_integer() for value in offset_values):
                raise ValueError("fixture valid times must align to whole-hour offsets")
            offsets = sorted({int(value) for value in offset_values})
        else:
            offsets = sorted({int(value) for value in offsets})
        if not offsets:
            raise ValueError("fixture target offsets must be nonempty")
        past = abs(min(0, *offsets))
        future = max(0, *offsets)
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        entries = []
        for valid, cycle, marker in sorted(specs):
            forecast_hour = int((valid - cycle).total_seconds() / 3600)
            filename = f"gfs_surface_wind_{cycle:%Y%m%dT%H}Z_f{forecast_hour:03d}.json"
            target = root / gfs.TARGET_ROOT / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            fields = []
            for parameter, value in ((2, marker), (3, marker + 0.5)):
                fields.append({
                    "header": {
                        "parameterCategory": 2, "parameterNumber": parameter,
                        "parameterNumberName": "U" if parameter == 2 else "V",
                        "parameterUnit": "m.s-1", "nx": 2, "ny": 1,
                        "lo1": -180, "la1": 75, "lo2": -179.75, "la2": 75,
                        "dx": 0.25, "dy": 0.25, "refTime": stamp(cycle),
                        "forecastTime": forecast_hour,
                    },
                    "data": [value, value + 0.25],
                })
            target.write_text(json.dumps(fields, separators=(",", ":")) + "\n", encoding="utf-8")
            speeds = {"min": 1.0, "p50": 2.0, "p75": 3.0, "p90": 4.0, "p95": 5.0, "max": 6.0, "median": 2.0}
            entry = {
                "product_id": gfs.DATA_PRODUCT_ID,
                "model": "NOAA GFS 0.25-degree", "level": "10 m above ground",
                "variables": ["UGRD", "VGRD"],
                "domain_id": "broad_npac_namerica", "domain_label": "fixture",
                "model_cycle_utc": stamp(cycle), "model_cycle_local": stamp(cycle),
                "forecast_hour": forecast_hour, "forecast_hour_label": f"f{forecast_hour:03d}",
                "valid_time_utc": stamp(valid), "valid_time_local": stamp(valid),
                "valid_lag_minutes_at_build": (generated - valid).total_seconds() / 60,
                "relative_url": f"gfs/surface/{filename}", "filename": filename,
                "file_bytes": target.stat().st_size, "status": "downloaded",
                "source_request_url": "https://example.invalid/gfs", "source_grib_bytes": 10000,
                "speed_ms": speeds, "speed_mph": speeds,
                "recommended_velocity_scale_ms": 8.0,
                "recommended_velocity_scale_mph": 17.9,
                "velocity_scale_reference": "fixture",
            }
            for unit in ("ms", "mph"):
                for key in ("min", "p50", "p75", "p90", "p95", "max"):
                    entry[f"speed_{unit}_{key}"] = speeds[key]
            entries.append(entry)
        selected = min(
            entries,
            key=lambda entry: (
                abs((datetime.fromisoformat(entry["valid_time_utc"][:-1] + "+00:00") - generated).total_seconds()),
                datetime.fromisoformat(entry["valid_time_utc"][:-1] + "+00:00") > generated,
            ),
        )
        manifest = {
            "product_id": gfs.DATA_PRODUCT_ID, "product": "fixture", "feed_mode": "time_set",
            "version": "RTW017", "source": "NOAA/NCEP NOMADS",
            "generated_utc": stamp(generated), "generated_local": stamp(generated),
            "legacy_latest": {
                "wind_json": gfs.LATEST_PATH.as_posix(),
                "summary_json": gfs.SUMMARY_PATH.as_posix(),
                "selected_entry": copy.deepcopy(selected),
            },
            "stale_after_hours": 9, "supported_browser_lead_hours": [0, 24],
            "target_offsets_hours": offsets,
            "target_past_hours": past, "target_future_hours": future,
            "max_forecast_hour": 42, "entry_count": len(entries), "entries": entries,
        }
        selected_target = root / gfs.TARGET_ROOT / selected["filename"]
        summary = {
            "product_id": gfs.DATA_PRODUCT_ID, "product": "fixture",
            "feed_mode": "time_set_with_legacy_latest", "version": "RTW017",
            "status": "success", "build_time_utc": stamp(generated),
            "build_time_local": stamp(generated), "local_time_zone": "UTC",
            "model_cycle_utc": selected["model_cycle_utc"],
            "model_cycle_local": selected["model_cycle_local"],
            "forecast_hour": selected["forecast_hour"],
            "forecast_hour_label": selected["forecast_hour_label"],
            "valid_time_utc": selected["valid_time_utc"],
            "valid_time_local": selected["valid_time_local"],
            "valid_lag_minutes_at_build": selected["valid_lag_minutes_at_build"],
            "valid_time_age_hours": selected["valid_lag_minutes_at_build"] / 60,
            "model_cycle_age_hours": (generated - datetime.fromisoformat(selected["model_cycle_utc"][:-1] + "+00:00")).total_seconds() / 3600,
            "stale_after_hours": 9, "is_stale": False,
            "domain": {"west": -180.125, "east": -179.625, "south": 74.875, "north": 75.125},
            "grid": {"nx": 2, "ny": 1, "dx": 0.25, "dy": 0.25},
            "selected_entry": copy.deepcopy(selected), "available_entry_count": len(entries),
            "target_offsets_hours": offsets,
            "target_past_hours": past, "target_future_hours": future,
            "max_forecast_hour": 42, "output_json_bytes": selected_target.stat().st_size,
            "output_files": {
                "wind_json": gfs.LATEST_PATH.as_posix(),
                "summary_json": gfs.SUMMARY_PATH.as_posix(),
                "manifest_json": gfs.MANIFEST_PATH.as_posix(),
                "time_set_directory": gfs.TARGET_ROOT.as_posix(),
            },
            "speed_ms": selected["speed_ms"], "speed_mph": selected["speed_mph"],
            "recommended_velocity_scale_ms": selected["recommended_velocity_scale_ms"],
            "recommended_velocity_scale_mph": selected["recommended_velocity_scale_mph"],
            "velocity_scale_reference": selected["velocity_scale_reference"],
        }
        for unit in ("ms", "mph"):
            for key in ("min", "p50", "p75", "p90", "p95", "max"):
                summary[f"speed_{unit}_{key}"] = selected[f"speed_{unit}_{key}"]
        for path, value in ((gfs.MANIFEST_PATH, manifest), (gfs.SUMMARY_PATH, summary)):
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        latest = root / gfs.LATEST_PATH
        latest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(selected_target, latest)

    def entries(self, root: Path):
        return gfs.validate_product(root).entries

    def valid_times(self, root: Path):
        return [entry.valid for entry in self.entries(root)]

    def test_hosted_canary_one_hour_sparse_set_shift(self):
        old_valid_offsets = set(self.SPARSE_OFFSETS)
        canonical_specs = [self.spec(offset, -6, 1.0) for offset in self.SPARSE_OFFSETS]
        candidate_specs = [
            self.spec(
                1 + offset,
                -6,
                1.0 if 1 + offset in old_valid_offsets else 2.0,
            )
            for offset in self.SPARSE_OFFSETS
        ]
        self.write_product(
            self.canonical,
            self.base,
            canonical_specs,
            offsets=self.SPARSE_OFFSETS,
        )
        old_entries = self.entries(self.canonical)
        old_bytes = {
            entry.valid: (self.canonical / entry.relative_path).read_bytes()
            for entry in old_entries
        }
        self.write_product(
            self.candidate,
            self.base + timedelta(hours=1),
            candidate_specs,
            offsets=self.SPARSE_OFFSETS,
        )
        expected_valid = {
            self.base + timedelta(hours=1 + offset)
            for offset in self.SPARSE_OFFSETS
        }

        self.assertEqual(gfs.reconcile(self.candidate, self.canonical)[0], "new")
        final = gfs.validate_product(self.canonical)
        self.assertEqual({entry.valid for entry in final.entries}, expected_valid)
        self.assertEqual(len(final.entries), len(self.SPARSE_OFFSETS))
        self.assertEqual(
            {path.relative_to(self.canonical) for path in (self.canonical / gfs.TARGET_ROOT).iterdir()},
            {entry.relative_path for entry in final.entries},
        )
        for entry in final.entries:
            if entry.valid in old_bytes:
                self.assertEqual(
                    (self.canonical / entry.relative_path).read_bytes(),
                    old_bytes[entry.valid],
                )
        self.assertTrue(
            all(
                not (self.canonical / entry.relative_path).exists()
                for entry in old_entries
                if entry.valid not in expected_valid
            )
        )

    def test_multi_hour_sparse_set_shift_prunes_old_slots(self):
        offsets = [-2, 0, 2, 22, 24, 26]
        self.write_product(
            self.canonical,
            self.base,
            [self.spec(offset, -6, 1.0) for offset in offsets],
            offsets=offsets,
        )
        old_paths = {entry.relative_path for entry in self.entries(self.canonical)}
        generated = self.base + timedelta(hours=4)
        old_valid_offsets = set(offsets)
        self.write_product(
            self.candidate,
            generated,
            [
                self.spec(
                    4 + offset,
                    -6,
                    1.0 if 4 + offset in old_valid_offsets else 2.0,
                )
                for offset in offsets
            ],
            offsets=offsets,
        )
        expected_valid = {generated + timedelta(hours=offset) for offset in offsets}

        gfs.reconcile(self.candidate, self.canonical)

        final = gfs.validate_product(self.canonical)
        self.assertEqual({entry.valid for entry in final.entries}, expected_valid)
        self.assertTrue(
            all(
                not (self.canonical / path).exists()
                for path in old_paths - {entry.relative_path for entry in final.entries}
            )
        )

    def test_complete_candidate_replaces_exact_sparse_set(self):
        offsets = [-1, 0, 1, 23, 24, 25]
        old_valid_offsets = set(offsets)
        self.write_product(
            self.canonical,
            self.base,
            [self.spec(offset, -6, 1.0) for offset in offsets],
            offsets=offsets,
        )
        self.write_product(
            self.candidate,
            self.base + timedelta(hours=2),
            [
                self.spec(
                    2 + offset,
                    -6,
                    1.0 if 2 + offset in old_valid_offsets else 2.0,
                )
                for offset in offsets
            ],
            offsets=offsets,
        )
        expected = gfs.semantic_key(self.candidate)

        self.assertEqual(gfs.reconcile(self.candidate, self.canonical)[0], "new")
        self.assertEqual(gfs.semantic_key(self.canonical), expected)

    def test_partial_candidate_uses_canonical_only_for_same_desired_slot(self):
        self.write_product(
            self.canonical,
            self.base,
            [self.spec(1, 0, 1.0), self.spec(2, 0, 2.0), self.spec(3, 0, 3.0)],
            offsets=[1, 2, 3],
        )
        canonical_entries = {entry.valid: entry for entry in self.entries(self.canonical)}
        retained = canonical_entries[self.base + timedelta(hours=3)]
        retained_bytes = (self.canonical / retained.relative_path).read_bytes()
        self.write_product(
            self.candidate,
            self.base + timedelta(hours=1),
            [self.spec(1, 0, 1.0)],
            offsets=[0, 2],
        )

        gfs.reconcile(self.candidate, self.canonical)

        final = {entry.valid: entry for entry in self.entries(self.canonical)}
        self.assertEqual(
            set(final),
            {self.base + timedelta(hours=1), self.base + timedelta(hours=3)},
        )
        self.assertEqual(
            (self.canonical / final[self.base + timedelta(hours=3)].relative_path).read_bytes(),
            retained_bytes,
        )

    def test_broad_window_canonical_target_outside_sparse_set_is_pruned(self):
        self.write_product(
            self.canonical,
            self.base,
            [self.spec(1, 0, 1.0), self.spec(2, 0, 2.0)],
            offsets=[1, 2],
        )
        obsolete = next(
            entry
            for entry in self.entries(self.canonical)
            if entry.valid == self.base + timedelta(hours=2)
        )
        self.write_product(
            self.candidate,
            self.base + timedelta(hours=1),
            [self.spec(1, 0, 1.0)],
            offsets=[0, 3],
        )

        gfs.reconcile(self.candidate, self.canonical)

        self.assertEqual(self.valid_times(self.canonical), [self.base + timedelta(hours=1)])
        self.assertFalse((self.canonical / obsolete.relative_path).exists())

    def test_same_cycle_same_valid_conflicting_bytes_fail_closed(self):
        self.write_product(self.canonical, self.base, [self.spec(2, 0, 1.0)])
        self.write_product(
            self.candidate,
            self.base + timedelta(hours=1),
            [self.spec(2, 0, 2.0)],
        )
        with self.assertRaisesRegex(gfs.ProductError, "conflicting bytes"):
            gfs.reconcile(self.candidate, self.canonical)

    def test_new_model_cycle_wins_for_same_valid_time(self):
        self.write_product(self.canonical, self.base, [self.spec(6, 0, 1)])
        self.write_product(self.candidate, self.base + timedelta(hours=1), [self.spec(6, 3, 2)])
        self.assertEqual(gfs.reconcile(self.candidate, self.canonical)[0], "new")
        self.assertEqual(self.entries(self.canonical)[0].cycle, self.base + timedelta(hours=3))

    def test_partial_target_advancement_preserves_other_coverage(self):
        self.write_product(self.canonical, self.base, [self.spec(2, 0, 1), self.spec(3, 0, 1)])
        self.write_product(
            self.candidate,
            self.base + timedelta(hours=1),
            [self.spec(2, 1, 2)],
            offsets=[1, 2],
        )
        gfs.reconcile(self.candidate, self.canonical)
        self.assertEqual(len(self.entries(self.canonical)), 2)

    def test_reusable_canonical_target_bytes_are_preserved(self):
        specs = [self.spec(2, 0, 1)]
        self.write_product(self.canonical, self.base, specs)
        original = (self.canonical / self.entries(self.canonical)[0].relative_path).read_bytes()
        self.write_product(
            self.candidate,
            self.base + timedelta(hours=1),
            [self.spec(1, 0, 2)],
            offsets=[0, 1],
        )
        gfs.reconcile(self.candidate, self.canonical)
        retained = next(entry for entry in self.entries(self.canonical) if entry.valid == self.base + timedelta(hours=2))
        self.assertEqual((self.canonical / retained.relative_path).read_bytes(), original)

    def test_valid_time_window_prunes_outside_targets(self):
        self.write_product(
            self.canonical,
            self.base,
            [self.spec(-5, -6, 1), self.spec(1, 0, 1)],
        )
        self.write_product(self.candidate, self.base + timedelta(hours=1), [self.spec(1, 0, 1)])
        gfs.reconcile(self.candidate, self.canonical)
        self.assertEqual([entry.valid for entry in self.entries(self.canonical)], [self.base + timedelta(hours=1)])

    def test_stale_candidate_cycle_is_noop(self):
        self.write_product(self.canonical, self.base + timedelta(hours=2), [self.spec(3, 0, 1)])
        self.write_product(self.candidate, self.base + timedelta(hours=1), [self.spec(3, 0, 1)])
        before = gfs.semantic_key(self.canonical)
        self.assertEqual(gfs.reconcile(self.candidate, self.canonical)[0], "stale")
        self.assertEqual(gfs.semantic_key(self.canonical), before)

    def test_candidate_missing_valid_reusable_target_retains_it(self):
        self.write_product(self.canonical, self.base, [self.spec(1, 0, 1), self.spec(2, 0, 1)])
        self.write_product(
            self.candidate,
            self.base + timedelta(hours=1),
            [self.spec(1, 0, 1)],
            offsets=[0, 1],
        )
        gfs.reconcile(self.candidate, self.canonical)
        self.assertEqual(len(self.entries(self.canonical)), 2)

    def test_target_referenced_but_missing_is_rejected(self):
        self.write_product(self.candidate, self.base, [self.spec(1, 0, 1)])
        (self.candidate / self.entries(self.candidate)[0].relative_path).unlink()
        with self.assertRaisesRegex(gfs.ProductError, "missing target"):
            gfs.validate_product(self.candidate)

    def test_orphan_target_is_rejected_under_current_policy(self):
        self.write_product(self.candidate, self.base, [self.spec(1, 0, 1)])
        (self.candidate / gfs.TARGET_ROOT / "orphan.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(gfs.ProductError, "orphan"):
            gfs.validate_product(self.candidate)

    def test_mixed_target_cycles_remain_valid(self):
        self.write_product(self.candidate, self.base, [self.spec(1, 0, 1), self.spec(3, -3, 2)])
        self.assertEqual(len({entry.cycle for entry in self.entries(self.candidate)}), 2)

    def test_canonical_newer_cycle_dominates_overlap(self):
        self.write_product(self.canonical, self.base, [self.spec(3, 1, 3)])
        self.write_product(self.candidate, self.base + timedelta(hours=1), [self.spec(3, 0, 1)])
        gfs.reconcile(self.candidate, self.canonical)
        self.assertEqual(self.entries(self.canonical)[0].cycle, self.base + timedelta(hours=1))

    def test_canonical_advancement_while_prepare_runs_wins(self):
        self.write_product(self.candidate, self.base, [self.spec(1, 0, 1)])
        self.write_product(self.canonical, self.base + timedelta(hours=1), [self.spec(2, 0, 2)])
        self.assertEqual(gfs.reconcile(self.candidate, self.canonical)[0], "stale")

    def test_candidate_cannot_delete_target_required_by_fresh_main(self):
        self.write_product(self.canonical, self.base, [self.spec(1, 0, 1), self.spec(2, 0, 2)])
        self.write_product(
            self.candidate,
            self.base + timedelta(hours=1),
            [self.spec(1, 0, 1)],
            offsets=[0, 1],
        )
        gfs.reconcile(self.candidate, self.canonical)
        self.assertIn(self.base + timedelta(hours=2), {entry.valid for entry in self.entries(self.canonical)})

    def test_reconciliation_changes_only_fixed_files_and_target_tree(self):
        self.write_product(self.canonical, self.base, [self.spec(1, 0, 1)])
        unrelated = self.canonical / "docs/data/wind/hrrr/keep.json"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("keep\n", encoding="utf-8")
        self.write_product(self.candidate, self.base + timedelta(hours=1), [self.spec(2, 0, 2)])
        gfs.reconcile(self.candidate, self.canonical)
        self.assertEqual(unrelated.read_bytes(), b"keep\n")

    def test_same_complete_state_is_same(self):
        specs = [self.spec(1, 0, 1)]
        self.write_product(self.canonical, self.base, specs)
        shutil.copytree(self.canonical, self.candidate)
        self.assertEqual(gfs.reconcile(self.candidate, self.canonical)[0], "same")

    def test_stale_outcome_is_explicit(self):
        self.write_product(self.canonical, self.base + timedelta(hours=1), [self.spec(2, 0, 1)])
        self.write_product(self.candidate, self.base, [self.spec(1, 0, 1)])
        self.assertEqual(gfs.reconcile(self.candidate, self.canonical)[0], "stale")

    def test_new_outcome_is_explicit(self):
        self.write_product(self.canonical, self.base, [self.spec(1, 0, 1)])
        self.write_product(self.candidate, self.base + timedelta(hours=1), [self.spec(2, 0, 2)])
        self.assertEqual(gfs.reconcile(self.candidate, self.canonical)[0], "new")

    def test_malformed_candidate_and_canonical_fail_closed(self):
        self.write_product(self.candidate, self.base + timedelta(hours=1), [self.spec(2, 0, 2)])
        self.write_product(self.canonical, self.base, [self.spec(1, 0, 1)])
        (self.candidate / gfs.MANIFEST_PATH).write_text("{}\n", encoding="utf-8")
        with self.assertRaises(gfs.ProductError):
            gfs.reconcile(self.candidate, self.canonical)
        self.write_product(self.candidate, self.base + timedelta(hours=1), [self.spec(2, 0, 2)])
        (self.canonical / gfs.SUMMARY_PATH).write_text("{}\n", encoding="utf-8")
        with self.assertRaises(gfs.ProductError):
            gfs.reconcile(self.candidate, self.canonical)


if __name__ == "__main__":
    unittest.main()
