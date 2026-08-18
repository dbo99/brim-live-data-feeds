#!/usr/bin/env python3
"""Deterministic Winter Storm Levels publication and retention tests."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from scripts import winter_storm_levels_publisher as winter


REPO = Path(__file__).resolve().parents[1]
MAIN_PUBLISHER = REPO / "scripts" / "main_publisher.py"


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(command, *, cwd: Path, env=None, check=True):
    result = subprocess.run(
        list(command),
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed: {command}\n{result.stdout}\n{result.stderr}"
        )
    return result


class WinterFixtureMixin:
    base = datetime(2026, 8, 18, 18, tzinfo=timezone.utc)

    @classmethod
    def _write_target(
        cls,
        root: Path,
        cycle: datetime,
        lead: int,
        marker: float,
    ) -> tuple[Path, dict[str, object]]:
        valid = cycle + timedelta(hours=lead)
        level = 1000
        coordinates = [
            [-125.0 + marker / 1000, 35.0],
            [-124.0 + marker / 1000, 36.0],
        ]
        bbox = [coordinates[0][0], 35.0, coordinates[1][0], 36.0]
        payload = {
            "type": "FeatureCollection",
            "contract_version": winter.SCHEMA_VERSION,
            "bbox": bbox,
            "features": [
                {
                    "type": "Feature",
                    "id": f"{cycle:%Y%m%d%H}_f{lead:03d}_{level:05d}_001",
                    "properties": {
                        "product_id": winter.WIRE_PRODUCT_ID,
                        "source_id": winter.SOURCE_ID,
                        "parameter": "snow_level",
                        "definition": "height of the wet-bulb 0.5 degree C surface",
                        "level_ft_msl": level,
                        "label": "1,000 ft MSL",
                        "unit": "ft_msl",
                        "cycle_time_utc": stamp(cycle),
                        "valid_time_utc": stamp(valid),
                        "lead_hours": lead,
                        "segment": 1,
                        "length_m": 1000.0 + marker,
                    },
                    "geometry": {"type": "LineString", "coordinates": coordinates},
                }
            ],
        }
        serialized = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
        sha = hashlib.sha256(serialized).hexdigest()
        filename = (
            f"winter_storm_levels_nbm_snow_level_{cycle:%Y%m%d%H}_"
            f"f{lead:03d}_{sha[:12]}.geojson"
        )
        repository_path = winter.TARGET_ROOT / filename
        destination = root / repository_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(serialized)
        relative_path = repository_path.relative_to(winter.BUNDLE_ROOT)
        retrieval = (
            "https://noaa-nbm-grib2-pds.s3.amazonaws.com/"
            f"blend.{cycle:%Y%m%d}/{cycle:%H}/core/"
            f"blend.t{cycle:%H}z.core.f{lead:03d}.co.grib2"
        )
        entry = {
            "source_id": winter.SOURCE_ID,
            "cycle_time_utc": stamp(cycle),
            "valid_time_utc": stamp(valid),
            "valid_from_utc": stamp(valid - timedelta(hours=3)),
            "valid_through_utc": stamp(valid + timedelta(hours=3)),
            "lead_hours": lead,
            "retrieval_url": retrieval,
            "inventory_url": retrieval + ".idx",
            "inventory_record": (
                f"1:0:d={cycle:%Y%m%d%H}:SNOWLVL:0 m above mean sea level:"
                f"{lead} hour fcst:"
            ),
            "path": relative_path.as_posix(),
            "media_type": "application/geo+json",
            "sha256": sha,
            "bytes": len(serialized),
            "feature_count": 1,
            "contour_levels_ft_msl": [level],
            "source_grid": {
                "rows": 2,
                "columns": 2,
                "resolution_m": 2539.703,
                "finite_coverage": 1,
                "min_m": 300,
                "max_m": 600,
            },
            "output_bbox_wgs84": bbox,
        }
        return repository_path, entry

    @classmethod
    def write_product(
        cls,
        root: Path,
        current: datetime,
        previous: datetime | None = None,
        *,
        marker: float = 1,
    ) -> None:
        shutil.rmtree(root, ignore_errors=True)
        cycles = [current] + ([previous] if previous is not None else [])
        entries = []
        for cycle_index, cycle in enumerate(cycles):
            for lead in winter.TARGET_LEADS:
                _, entry = cls._write_target(
                    root,
                    cycle,
                    lead,
                    marker + cycle_index * 100 + lead / 100,
                )
                entries.append(entry)
        manifest = {
            "product_id": winter.WIRE_PRODUCT_ID,
            "schema_version": winter.SCHEMA_VERSION,
            "contract_version": winter.SCHEMA_VERSION,
            "status": "current",
            "source": copy.deepcopy(winter.EXPECTED_SOURCE),
            "domain": copy.deepcopy(winter.EXPECTED_DOMAIN),
            "contour": copy.deepcopy(winter.EXPECTED_CONTOUR),
            "freshness": copy.deepcopy(winter.EXPECTED_FRESHNESS),
            "cycle_time_utc": stamp(current),
            "retrieval_time_utc": stamp(current + timedelta(hours=3)),
            "publication_time_utc": None,
            "target_count": len(entries),
            "diagnostics": {
                "expected_current_cycle_target_count": len(winter.TARGET_LEADS),
                "actual_current_cycle_target_count": len(winter.TARGET_LEADS),
                "retained_cycle_count": len(cycles),
                "complete_bundle_validated": True,
            },
            "targets": entries,
        }
        destination = root / winter.MANIFEST_PATH
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(manifest, indent=2) + "\n")

    @staticmethod
    def load(root: Path, path: Path) -> dict[str, object]:
        return json.loads((root / path).read_text())

    @staticmethod
    def dump(root: Path, path: Path, value: object) -> None:
        (root / path).write_text(json.dumps(value, indent=2) + "\n")

    @classmethod
    def rewrite_target(
        cls,
        root: Path,
        index: int,
        mutation,
        *,
        repair_manifest: bool = True,
    ) -> None:
        manifest = cls.load(root, winter.MANIFEST_PATH)
        entry = manifest["targets"][index]
        old_repository_path = winter.BUNDLE_ROOT / Path(entry["path"])
        payload = cls.load(root, old_repository_path)
        mutation(payload)
        serialized = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
        (root / old_repository_path).write_bytes(serialized)
        if repair_manifest:
            sha = hashlib.sha256(serialized).hexdigest()
            match = winter.TARGET_PATTERN.fullmatch(old_repository_path.name)
            assert match is not None
            new_name = (
                f"winter_storm_levels_nbm_snow_level_{match.group(1)}_"
                f"f{match.group(2)}_{sha[:12]}.geojson"
            )
            new_repository_path = old_repository_path.with_name(new_name)
            (root / old_repository_path).rename(root / new_repository_path)
            entry["path"] = new_repository_path.relative_to(
                winter.BUNDLE_ROOT
            ).as_posix()
            entry["sha256"] = sha
            entry["bytes"] = len(serialized)
            cls.dump(root, winter.MANIFEST_PATH, manifest)


class WinterStormLevelsPublisherTests(WinterFixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.candidate = self.root / "candidate"
        self.canonical = self.root / "canonical"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def product(self, root: Path, offset: int, previous: int | None = None, **kwargs):
        self.write_product(
            root,
            self.base + timedelta(hours=offset),
            self.base + timedelta(hours=previous) if previous is not None else None,
            **kwargs,
        )

    def manifest(self, root: Path) -> dict[str, object]:
        return self.load(root, winter.MANIFEST_PATH)

    def save_manifest(self, root: Path, value: object) -> None:
        self.dump(root, winter.MANIFEST_PATH, value)

    def test_valid_one_and_two_cycle_products_and_semantic_key(self):
        self.product(self.candidate, 0)
        self.assertEqual(len(winter.validate_product(self.candidate).entries), 11)
        self.product(self.candidate, 0, -6)
        self.assertEqual(len(winter.validate_product(self.candidate).entries), 22)
        self.assertEqual(len(winter.semantic_key(self.candidate)), 64)

    def test_current_repository_product_validates(self):
        state = winter.validate_product(Path(__file__).resolve().parents[1])
        self.assertEqual(len(state.cycles), 2)
        self.assertEqual(len(state.entries), 22)

    def test_new_cycle_rolls_to_exact_fresh_main_two_cycle_state(self):
        self.product(self.canonical, -6, -12, marker=10)
        prior = winter.validate_product(self.canonical)
        retained = {
            path: (self.canonical / path).read_bytes()
            for path, entry in prior.targets.items()
            if entry.cycle == prior.current_cycle
        }
        self.product(self.candidate, 0, -12, marker=20)
        state, _ = winter.reconcile(self.candidate, self.canonical)
        result = winter.validate_product(self.canonical)
        self.assertEqual(state, "new")
        self.assertEqual(result.cycles, (self.base, self.base - timedelta(hours=6)))
        self.assertEqual(len(result.entries), 22)
        for path, content in retained.items():
            self.assertEqual((self.canonical / path).read_bytes(), content)

    def test_rollover_prunes_old_canonical_and_candidate_prior_cycles(self):
        self.product(self.canonical, -6, -12)
        old_paths = set(winter.validate_product(self.canonical).targets)
        self.product(self.candidate, 0, -18)
        candidate_prior = {
            path
            for path, entry in winter.validate_product(self.candidate).targets.items()
            if entry.cycle == self.base - timedelta(hours=18)
        }
        winter.reconcile(self.candidate, self.canonical)
        result_paths = set(winter.validate_product(self.canonical).targets)
        self.assertFalse(candidate_prior & result_paths)
        self.assertEqual(len(result_paths), 22)
        self.assertEqual(len(old_paths - result_paths), 11)

    def test_same_semantic_state_ignores_only_retrieval_time(self):
        self.product(self.candidate, 0, -6)
        shutil.copytree(self.candidate, self.canonical)
        manifest = self.manifest(self.candidate)
        manifest["retrieval_time_utc"] = stamp(self.base + timedelta(hours=4))
        self.save_manifest(self.candidate, manifest)
        before = {
            path.relative_to(self.canonical): path.read_bytes()
            for path in self.canonical.rglob("*")
            if path.is_file()
        }
        self.assertEqual(winter.reconcile(self.candidate, self.canonical)[0], "same")
        after = {
            path.relative_to(self.canonical): path.read_bytes()
            for path in self.canonical.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_older_cycle_is_stale_and_does_not_mutate_canonical(self):
        self.product(self.canonical, 0, -6)
        self.product(self.candidate, -6, -12)
        before = (self.canonical / winter.MANIFEST_PATH).read_bytes()
        self.assertEqual(winter.reconcile(self.candidate, self.canonical)[0], "stale")
        self.assertEqual((self.canonical / winter.MANIFEST_PATH).read_bytes(), before)

    def test_same_cycle_conflict_fails_closed(self):
        self.product(self.canonical, 0, -6, marker=1)
        self.product(self.candidate, 0, -6, marker=9)
        before = (self.canonical / winter.MANIFEST_PATH).read_bytes()
        with self.assertRaisesRegex(winter.ProductError, "same-cycle"):
            winter.reconcile(self.candidate, self.canonical)
        self.assertEqual((self.canonical / winter.MANIFEST_PATH).read_bytes(), before)

    def test_missing_target_is_rejected(self):
        self.product(self.candidate, 0, -6)
        state = winter.validate_product(self.candidate)
        (self.candidate / state.entries[0].repository_path).unlink()
        with self.assertRaises(winter.ProductError):
            winter.validate_product(self.candidate)

    def test_checksum_mismatch_is_rejected(self):
        self.product(self.candidate, 0, -6)
        self.rewrite_target(self.candidate, 0, lambda value: value.update(extra=True), repair_manifest=False)
        with self.assertRaisesRegex(winter.ProductError, "checksum mismatch"):
            winter.validate_product(self.candidate)

    def test_byte_count_mismatch_is_rejected(self):
        self.product(self.candidate, 0, -6)
        manifest = self.manifest(self.candidate)
        manifest["targets"][0]["bytes"] += 1
        self.save_manifest(self.candidate, manifest)
        with self.assertRaisesRegex(winter.ProductError, "size mismatch"):
            winter.validate_product(self.candidate)

    def test_content_address_prefix_mismatch_is_rejected(self):
        self.product(self.candidate, 0, -6)
        manifest = self.manifest(self.candidate)
        manifest["targets"][0]["sha256"] = "0" * 64
        self.save_manifest(self.candidate, manifest)
        with self.assertRaisesRegex(winter.ProductError, "SHA/path"):
            winter.validate_product(self.candidate)

    def test_unsafe_target_path_is_rejected(self):
        self.product(self.candidate, 0, -6)
        manifest = self.manifest(self.candidate)
        manifest["targets"][0]["path"] = "../escape.geojson"
        self.save_manifest(self.candidate, manifest)
        with self.assertRaisesRegex(winter.ProductError, "unsafe"):
            winter.validate_product(self.candidate)

    def test_unexpected_owned_file_is_rejected(self):
        self.product(self.candidate, 0, -6)
        extra = self.candidate / winter.TARGET_ROOT / "unexpected.geojson"
        extra.write_text("{}\n")
        with self.assertRaisesRegex(winter.ProductError, "closure"):
            winter.validate_product(self.candidate)

    def test_target_symlink_is_rejected(self):
        self.product(self.candidate, 0, -6)
        state = winter.validate_product(self.candidate)
        target = self.candidate / state.entries[0].repository_path
        real = target.with_suffix(".real")
        target.rename(real)
        target.symlink_to(real.name)
        with self.assertRaises(winter.ProductError):
            winter.validate_product(self.candidate)

    def test_incomplete_cycle_is_rejected(self):
        self.product(self.candidate, 0, -6)
        manifest = self.manifest(self.candidate)
        removed = manifest["targets"].pop()
        manifest["target_count"] -= 1
        self.save_manifest(self.candidate, manifest)
        (self.candidate / winter.BUNDLE_ROOT / removed["path"]).unlink()
        with self.assertRaisesRegex(winter.ProductError, "one or two complete"):
            winter.validate_product(self.candidate)

    def test_duplicate_or_unsorted_leads_are_rejected(self):
        self.product(self.candidate, 0, -6)
        manifest = self.manifest(self.candidate)
        manifest["targets"][0], manifest["targets"][1] = (
            manifest["targets"][1],
            manifest["targets"][0],
        )
        self.save_manifest(self.candidate, manifest)
        with self.assertRaisesRegex(winter.ProductError, "lead set/order"):
            winter.validate_product(self.candidate)

    def test_missing_required_lead_is_rejected(self):
        self.product(self.candidate, 0, -6)
        manifest = self.manifest(self.candidate)
        manifest["targets"][0]["lead_hours"] = 54
        self.save_manifest(self.candidate, manifest)
        with self.assertRaisesRegex(winter.ProductError, "unsupported"):
            winter.validate_product(self.candidate)

    def test_duplicate_lead_is_rejected(self):
        self.product(self.candidate, 0, -6)
        manifest = self.manifest(self.candidate)
        manifest["targets"][1]["lead_hours"] = manifest["targets"][0]["lead_hours"]
        self.save_manifest(self.candidate, manifest)
        with self.assertRaises(winter.ProductError):
            winter.validate_product(self.candidate)

    def test_cross_cycle_target_is_rejected(self):
        self.product(self.candidate, 0, -6)
        manifest = self.manifest(self.candidate)
        manifest["targets"][0]["cycle_time_utc"] = stamp(
            self.base - timedelta(hours=6)
        )
        self.save_manifest(self.candidate, manifest)
        with self.assertRaises(winter.ProductError):
            winter.validate_product(self.candidate)

    def test_malformed_candidate_manifest_is_rejected(self):
        self.product(self.candidate, 0, -6)
        (self.candidate / winter.MANIFEST_PATH).write_text("{not-json\n")
        with self.assertRaisesRegex(winter.ProductError, "unreadable"):
            winter.validate_product(self.candidate)

    def test_malformed_fresh_canonical_fails_before_mutation(self):
        self.product(self.candidate, 0, -6)
        self.product(self.canonical, -6, -12)
        canonical_manifest = self.canonical / winter.MANIFEST_PATH
        canonical_manifest.write_text("{not-json\n")
        before = canonical_manifest.read_bytes()
        with self.assertRaisesRegex(winter.ProductError, "unreadable"):
            winter.reconcile(self.candidate, self.canonical)
        self.assertEqual(canonical_manifest.read_bytes(), before)

    def test_root_cycle_must_be_newest(self):
        self.product(self.candidate, 0, -6)
        manifest = self.manifest(self.candidate)
        manifest["cycle_time_utc"] = stamp(self.base - timedelta(hours=6))
        self.save_manifest(self.candidate, manifest)
        with self.assertRaisesRegex(winter.ProductError, "root cycle"):
            winter.validate_product(self.candidate)

    def test_publication_time_must_remain_null(self):
        self.product(self.candidate, 0, -6)
        manifest = self.manifest(self.candidate)
        manifest["publication_time_utc"] = stamp(self.base + timedelta(hours=3))
        self.save_manifest(self.candidate, manifest)
        with self.assertRaisesRegex(winter.ProductError, "must remain null"):
            winter.validate_product(self.candidate)

    def test_manifest_identity_contracts_are_rejected_when_changed(self):
        mutations = (
            ("product_id", "other"),
            ("schema_version", "2.0.0"),
            ("contract_version", "2.0.0"),
            ("status", "unknown"),
        )
        for key, value in mutations:
            with self.subTest(key=key):
                self.product(self.candidate, 0, -6)
                manifest = self.manifest(self.candidate)
                manifest[key] = value
                self.save_manifest(self.candidate, manifest)
                with self.assertRaises(winter.ProductError):
                    winter.validate_product(self.candidate)

    def test_source_domain_contour_and_freshness_are_fixed(self):
        for key in ("source", "domain", "contour", "freshness"):
            with self.subTest(key=key):
                self.product(self.candidate, 0, -6)
                manifest = self.manifest(self.candidate)
                manifest[key][next(iter(manifest[key]))] = "changed"
                self.save_manifest(self.candidate, manifest)
                with self.assertRaises(winter.ProductError):
                    winter.validate_product(self.candidate)

    def test_retrieval_time_cannot_predate_cycle(self):
        self.product(self.candidate, 0, -6)
        manifest = self.manifest(self.candidate)
        manifest["retrieval_time_utc"] = stamp(self.base - timedelta(hours=1))
        self.save_manifest(self.candidate, manifest)
        with self.assertRaisesRegex(winter.ProductError, "predates"):
            winter.validate_product(self.candidate)

    def test_valid_time_and_window_must_match_cycle_and_lead(self):
        self.product(self.candidate, 0, -6)
        manifest = self.manifest(self.candidate)
        manifest["targets"][0]["valid_from_utc"] = stamp(self.base)
        self.save_manifest(self.candidate, manifest)
        with self.assertRaisesRegex(winter.ProductError, "validity start"):
            winter.validate_product(self.candidate)

    def test_provenance_inventory_contract_is_required(self):
        self.product(self.candidate, 0, -6)
        manifest = self.manifest(self.candidate)
        manifest["targets"][0]["inventory_record"] = "not snow level"
        self.save_manifest(self.candidate, manifest)
        with self.assertRaisesRegex(winter.ProductError, "inventory record"):
            winter.validate_product(self.candidate)

    def test_geojson_root_and_feature_contract_are_required(self):
        self.product(self.candidate, 0, -6)
        self.rewrite_target(self.candidate, 0, lambda value: value.update(contract_version="2.0.0"))
        with self.assertRaisesRegex(winter.ProductError, "contract identity"):
            winter.validate_product(self.candidate)

    def test_geojson_properties_must_match_target_time(self):
        self.product(self.candidate, 0, -6)
        self.rewrite_target(
            self.candidate,
            0,
            lambda value: value["features"][0]["properties"].update(lead_hours=6),
        )
        with self.assertRaisesRegex(winter.ProductError, "lead disagrees"):
            winter.validate_product(self.candidate)

    def test_geojson_rejects_nonfinite_or_out_of_domain_coordinates(self):
        for coordinate in (float("nan"), -131.0):
            with self.subTest(coordinate=coordinate):
                self.product(self.candidate, 0, -6)
                self.rewrite_target(
                    self.candidate,
                    0,
                    lambda value, coordinate=coordinate: value["features"][0]["geometry"]["coordinates"][0].__setitem__(0, coordinate),
                )
                with self.assertRaises(winter.ProductError):
                    winter.validate_product(self.candidate)

    def test_geojson_rejects_adjacent_duplicate_coordinates(self):
        self.product(self.candidate, 0, -6)
        self.rewrite_target(
            self.candidate,
            0,
            lambda value: value["features"][0]["geometry"].update(
                coordinates=[[-125.0, 35.0], [-125.0, 35.0]]
            ),
        )
        with self.assertRaisesRegex(winter.ProductError, "adjacent duplicate"):
            winter.validate_product(self.candidate)

    def test_geojson_bbox_and_manifest_levels_must_match_payload(self):
        self.product(self.candidate, 0, -6)
        manifest = self.manifest(self.candidate)
        manifest["targets"][0]["contour_levels_ft_msl"] = [2000]
        self.save_manifest(self.candidate, manifest)
        with self.assertRaisesRegex(winter.ProductError, "contour levels"):
            winter.validate_product(self.candidate)

    def test_source_grid_and_diagnostics_must_be_coherent(self):
        self.product(self.candidate, 0, -6)
        manifest = self.manifest(self.candidate)
        manifest["targets"][0]["source_grid"]["finite_coverage"] = 2
        self.save_manifest(self.candidate, manifest)
        with self.assertRaisesRegex(winter.ProductError, "source grid values"):
            winter.validate_product(self.candidate)

    def test_bootstrap_without_canonical_is_validated_and_copied(self):
        self.product(self.candidate, 0)
        self.assertEqual(winter.reconcile(self.candidate, self.canonical)[0], "new")
        self.assertEqual(len(winter.validate_product(self.canonical).entries), 11)

    def test_callback_rejects_wrong_semantic_metadata(self):
        self.product(self.candidate, 0, -6)
        self.product(self.canonical, -6, -12)
        metadata = self.root / "metadata.json"
        metadata.write_text(json.dumps({"semantic_key": {"type": "wrong", "value": "0"}}))
        with mock.patch.dict(
            os.environ,
            {
                "BRIM_PUBLISH_PHASE": "validate-candidate",
                "BRIM_PUBLISH_PRODUCT_ID": winter.PRODUCT_ID,
                "BRIM_PUBLISH_CANDIDATE_ROOT": str(self.candidate),
                "BRIM_PUBLISH_WORKTREE": str(self.canonical),
                "BRIM_PUBLISH_METADATA": str(metadata),
            },
            clear=False,
        ):
            with self.assertRaisesRegex(winter.ProductError, "semantic key"):
                winter._callback()

    def test_callback_emits_new_same_stale_contract(self):
        for candidate_offset, canonical_offset, expected in (
            (0, -6, "new"),
            (0, 0, "same"),
            (-6, 0, "stale"),
        ):
            with self.subTest(expected=expected):
                self.product(self.candidate, candidate_offset, candidate_offset - 6)
                if expected == "same":
                    shutil.rmtree(self.canonical, ignore_errors=True)
                    shutil.copytree(self.candidate, self.canonical)
                else:
                    self.product(self.canonical, canonical_offset, canonical_offset - 6)
                result = self.root / f"result-{expected}.json"
                with mock.patch.dict(
                    os.environ,
                    {
                        "BRIM_PUBLISH_PHASE": "reconcile",
                        "BRIM_PUBLISH_PRODUCT_ID": winter.PRODUCT_ID,
                        "BRIM_PUBLISH_CANDIDATE_ROOT": str(self.candidate),
                        "BRIM_PUBLISH_WORKTREE": str(self.canonical),
                        "BRIM_PUBLISH_RESULT": str(result),
                    },
                    clear=False,
                ):
                    winter._callback()
                self.assertEqual(json.loads(result.read_text())["candidate_state"], expected)


CALLBACK_WRAPPER = r'''#!/usr/bin/env python3
import os
from pathlib import Path

from winter_storm_levels_publisher import _callback

status = _callback()
if (
    os.environ.get("BRIM_PUBLISH_PHASE") == "reconcile"
    and os.environ.get("TEST_UNDECLARED_ADD") == "true"
):
    unexpected = Path(os.environ["BRIM_PUBLISH_WORKTREE"]) / "docs/data/unowned.json"
    unexpected.parent.mkdir(parents=True, exist_ok=True)
    unexpected.write_text("unexpected\n", encoding="utf-8")
raise SystemExit(status)
'''


RACE_WRAPPER = r'''#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path

git = os.environ["REAL_GIT"]
args = sys.argv[1:]
if args and args[0] == "push" and "HEAD:refs/heads/main" in args:
    count_path = Path(os.environ["RACE_COUNT"])
    count = int(count_path.read_text() if count_path.exists() else "0")
    if count < int(os.environ["RACE_LIMIT"]):
        count += 1
        count_path.write_text(str(count), encoding="utf-8")
        racer = Path(os.environ["RACE_CLONE"])
        subprocess.run([git, "-C", str(racer), "fetch", "origin", "main"], check=True)
        subprocess.run([git, "-C", str(racer), "checkout", "-B", "main", "origin/main"], check=True)
        race_file = racer / f"unrelated-race-{count}.txt"
        race_file.write_text(f"race {count}\n", encoding="utf-8")
        subprocess.run([git, "-C", str(racer), "add", race_file.name], check=True)
        subprocess.run([git, "-C", str(racer), "commit", "-m", f"Race {count}"], check=True)
        subprocess.run([git, "-C", str(racer), "push", "origin", "HEAD:refs/heads/main"], check=True)
os.execv(git, [git, *args])
'''


class WinterStormMainPublisherIntegrationTests(WinterFixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        self.runner = self.root / "runner"
        self.candidate = self.root / "artifact" / "candidate"
        self.metadata = self.root / "artifact" / "candidate-metadata.json"
        run(["git", "init", "--bare", str(self.remote)], cwd=self.root)
        self.write_product(
            self.seed,
            self.base - timedelta(hours=6),
            self.base - timedelta(hours=12),
            marker=10,
        )
        run(["git", "init", str(self.seed)], cwd=self.root)
        run(["git", "config", "user.name", "Test Author"], cwd=self.seed)
        run(["git", "config", "user.email", "test@example.invalid"], cwd=self.seed)
        scripts = self.seed / "scripts"
        scripts.mkdir(parents=True)
        shutil.copyfile(REPO / "scripts" / "winter_storm_levels_publisher.py", scripts / "winter_storm_levels_publisher.py")
        (scripts / "winter_callback_wrapper.py").write_text(
            CALLBACK_WRAPPER, encoding="utf-8"
        )
        sibling = self.seed / "docs/data/sibling.json"
        sibling.parent.mkdir(parents=True, exist_ok=True)
        sibling.write_text('{"preserve":true}\n', encoding="utf-8")
        run(["git", "add", "."], cwd=self.seed)
        run(["git", "commit", "-m", "Initial main"], cwd=self.seed)
        run(["git", "branch", "-M", "main"], cwd=self.seed)
        run(["git", "remote", "add", "origin", str(self.remote)], cwd=self.seed)
        run(["git", "push", "-u", "origin", "main"], cwd=self.seed)
        run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=self.remote)
        run(["git", "clone", str(self.remote), str(self.runner)], cwd=self.root)
        self.source_sha = run(["git", "rev-parse", "HEAD"], cwd=self.runner).stdout.strip()
        self.callback = self.runner / "scripts" / "winter_callback_wrapper.py"
        self.write_product(
            self.candidate,
            self.base,
            self.base - timedelta(hours=18),
            marker=20,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def prepare(self):
        return run(
            [
                sys.executable,
                str(MAIN_PUBLISHER),
                "prepare",
                "--candidate-root",
                str(self.candidate),
                "--output",
                str(self.metadata),
                "--product-id",
                winter.PRODUCT_ID,
                "--semantic-key-type",
                "winter_storm_two_cycle_state_sha256_v1",
                "--semantic-key",
                winter.semantic_key(self.candidate),
                "--source-event-sha",
                self.source_sha,
                "--allowlist",
                winter.MANIFEST_PATH.as_posix(),
                "--owned-root",
                winter.TARGET_ROOT.as_posix(),
            ],
            cwd=REPO,
            check=False,
        )

    def environment(self, **extra):
        return {
            **os.environ,
            "GITHUB_ACTIONS": "true",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_REF_TYPE": "branch",
            "GITHUB_SHA": self.source_sha,
            "BRIM_LIVE_MAIN_PUBLISH": "true",
            **extra,
        }

    def publish(self, *, env=None):
        return run(
            [
                sys.executable,
                str(MAIN_PUBLISHER),
                "publish",
                "--repo",
                str(self.runner),
                "--candidate-root",
                str(self.candidate),
                "--metadata",
                str(self.metadata),
                "--product-id",
                winter.PRODUCT_ID,
                "--commit-subject",
                "Update Winter Storm Levels feed",
                "--allowlist",
                winter.MANIFEST_PATH.as_posix(),
                "--owned-root",
                winter.TARGET_ROOT.as_posix(),
                "--candidate-validator",
                str(self.callback),
                "--reconcile-callback",
                str(self.callback),
                "--staged-validator",
                str(self.callback),
            ],
            cwd=REPO,
            env=env or self.environment(),
            check=False,
        )

    def remote_file(self, path: Path | str, revision: str = "main") -> bytes:
        return subprocess.run(
            [
                "git",
                "--git-dir",
                str(self.remote),
                "show",
                f"{revision}:{Path(path).as_posix()}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout

    def remote_paths(self) -> set[str]:
        return set(
            run(
                ["git", "--git-dir", str(self.remote), "ls-tree", "-r", "--name-only", "main"],
                cwd=self.root,
            ).stdout.splitlines()
        )

    def install_race_wrapper(self, limit: int):
        racer = self.root / "racer"
        run(["git", "clone", str(self.remote), str(racer)], cwd=self.root)
        run(["git", "config", "user.name", "Race Author"], cwd=racer)
        run(["git", "config", "user.email", "race@example.invalid"], cwd=racer)
        wrapper_dir = self.root / "bin"
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / "git"
        wrapper.write_text(RACE_WRAPPER, encoding="utf-8")
        wrapper.chmod(0o755)
        return self.environment(
            PATH=f"{wrapper_dir}{os.pathsep}{os.environ['PATH']}",
            REAL_GIT=shutil.which("git"),
            RACE_CLONE=str(racer),
            RACE_LIMIT=str(limit),
            RACE_COUNT=str(self.root / "race-count"),
        )

    def advance_remote_product(self) -> None:
        fresh = self.root / "fresh"
        run(["git", "clone", str(self.remote), str(fresh)], cwd=self.root)
        run(["git", "config", "user.name", "Fresh Author"], cwd=fresh)
        run(["git", "config", "user.email", "fresh@example.invalid"], cwd=fresh)
        state = self.root / "fresh-state"
        self.write_product(
            state,
            self.base + timedelta(hours=6),
            self.base,
            marker=30,
        )
        shutil.rmtree(fresh / winter.BUNDLE_ROOT)
        shutil.copytree(state / winter.BUNDLE_ROOT, fresh / winter.BUNDLE_ROOT)
        run(["git", "add", winter.BUNDLE_ROOT.as_posix()], cwd=fresh)
        run(["git", "commit", "-m", "Advance Winter product"], cwd=fresh)
        run(["git", "push", "origin", "HEAD:refs/heads/main"], cwd=fresh)

    def test_exact_staging_reuses_current_and_prunes_old_previous(self):
        canonical = winter.validate_product(self.runner)
        retained = next(
            entry.repository_path
            for entry in canonical.entries
            if entry.cycle == canonical.current_cycle
        )
        retained_before = self.remote_file(retained)
        old_previous = {
            path.as_posix()
            for path, entry in canonical.targets.items()
            if entry.cycle != canonical.current_cycle
        }
        self.assertEqual(self.prepare().returncode, 0)
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        paths = self.remote_paths()
        self.assertFalse(old_previous & paths)
        self.assertEqual(self.remote_file(retained), retained_before)
        changed = run(
            ["git", "--git-dir", str(self.remote), "diff-tree", "--no-commit-id", "--name-only", "-r", "main"],
            cwd=self.root,
        ).stdout.splitlines()
        self.assertTrue(changed)
        self.assertTrue(
            all(
                path == winter.MANIFEST_PATH.as_posix()
                or path.startswith(winter.TARGET_ROOT.as_posix() + "/")
                for path in changed
            )
        )
        self.assertEqual(self.remote_file("docs/data/sibling.json"), b'{"preserve":true}\n')

    def test_prepare_rejects_ownership_violation(self):
        unexpected = self.candidate / "docs/data/unowned.json"
        unexpected.parent.mkdir(parents=True, exist_ok=True)
        unexpected.write_text("{}\n", encoding="utf-8")
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the allowlist/declared owned roots", result.stderr)

    def test_unexpected_staged_path_is_rejected_and_remote_is_retained(self):
        before = self.remote_file(winter.MANIFEST_PATH)
        self.assertEqual(self.prepare().returncode, 0)
        result = self.publish(env=self.environment(TEST_UNDECLARED_ADD="true"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the allowlist", result.stderr)
        self.assertEqual(self.remote_file(winter.MANIFEST_PATH), before)

    def test_fresh_main_advancement_reconciles_to_stale_noop(self):
        self.assertEqual(self.prepare().returncode, 0)
        self.advance_remote_product()
        before = self.remote_file(winter.MANIFEST_PATH)
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("candidate is stale", result.stdout)
        self.assertEqual(self.remote_file(winter.MANIFEST_PATH), before)

    def test_one_push_race_retries_once_and_succeeds(self):
        self.assertEqual(self.prepare().returncode, 0)
        result = self.publish(env=self.install_race_wrapper(1))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("retrying reconciliation exactly once", result.stdout)
        self.assertEqual((self.root / "race-count").read_text(), "1")

    def test_unresolved_second_push_race_fails_closed(self):
        self.assertEqual(self.prepare().returncode, 0)
        before = self.remote_file(winter.MANIFEST_PATH)
        result = self.publish(env=self.install_race_wrapper(2))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("advanced during both publication attempts", result.stderr)
        self.assertEqual((self.root / "race-count").read_text(), "2")
        self.assertEqual(self.remote_file(winter.MANIFEST_PATH), before)


if __name__ == "__main__":
    unittest.main()
