#!/usr/bin/env python3
"""Deterministic NBM complete-cycle publication and retention tests."""

from __future__ import annotations

import copy
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

from scripts import nbm_wind_publisher as nbm


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


class NbmFixtureMixin:
    base = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)

    @staticmethod
    def _land_mask(feature_count: int) -> dict[str, object]:
        return {
            "method": "fixture land mask",
            "polygon_database": "maps::world",
            "coastal_buffer_cells": 1,
            "operational_mask_id": "hydrologic_ca_operational_v1",
            "operational_mask_label": "fixture operational envelope",
            "operational_mask_vertices": [],
            "source_grid_points": feature_count,
            "mapped_land_points": feature_count,
            "coastal_fringe_points": 0,
            "land_and_coast_points": feature_count,
            "outside_operational_mask_removed": 0,
            "retained_points": feature_count,
            "offshore_points_removed": 0,
        }

    @classmethod
    def _write_target(
        cls,
        root: Path,
        cycle: datetime,
        forecast_hour: int,
        lead: int,
        marker: float,
    ) -> Path:
        filename = (
            f"nbm_wind_guidance_{cycle:%Y%m%dT%H}Z_f{forecast_hour:03d}.geojson"
        )
        relative_path = nbm.TARGET_ROOT / filename
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        features = []
        for index in range(nbm.MIN_FEATURE_COUNT):
            grid_i = index % 50
            grid_j = index // 50
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [-125.5 + grid_i * 0.25, 31 + grid_j * 0.25],
                    },
                    "properties": {
                        "grid_i": grid_i,
                        "grid_j": grid_j,
                        "wind_dir_degrees": 270,
                        "wind_dir_cardinal": "W",
                        "wind_p10_mph": marker,
                        "wind_p50_mph": marker + 1,
                        "wind_p90_mph": marker + 2,
                        "gust_p10_mph": marker + 3,
                        "gust_p50_mph": marker + 4,
                        "gust_p90_mph": marker + 5,
                    },
                }
            )
        value = {
            "type": "FeatureCollection",
            "name": "NOAA NBM wind guidance",
            "metadata": {
                "feed_version": nbm.FEED_VERSION,
                "model": "NOAA/NWS National Blend of Models",
                "model_cycle_utc": stamp(cycle),
                "valid_time_utc": stamp(cycle + timedelta(hours=forecast_hour)),
                "forecast_hour": forecast_hour,
                "target_lead_hours": lead,
                "domain": copy.deepcopy(nbm.EXPECTED_DOMAIN),
                "land_mask": cls._land_mask(len(features)),
                "guidance": {
                    "direction": "fixture",
                    "sustained": "fixture",
                    "gust": "fixture",
                },
            },
            "features": features,
        }
        destination.write_text(json.dumps(value, separators=(",", ":")) + "\n")
        return relative_path

    @classmethod
    def write_product(
        cls,
        root: Path,
        cycle: datetime,
        generated: datetime,
        *,
        marker: float = 1,
        retained: tuple[tuple[datetime, int, int, float], ...] = (),
    ) -> None:
        shutil.rmtree(root, ignore_errors=True)
        entries = []
        valid_times = []
        for lead in nbm.PUBLISHED_LEADS:
            target_time = generated + timedelta(hours=lead)
            forecast_hour = min(
                nbm.AVAILABLE_FORECAST_HOURS,
                key=lambda value: (
                    abs(
                        (
                            cycle + timedelta(hours=value) - target_time
                        ).total_seconds()
                    ),
                    value,
                ),
            )
            valid = cycle + timedelta(hours=forecast_hour)
            relative_path = cls._write_target(
                root, cycle, forecast_hour, lead, marker + lead / 100
            )
            land_mask = cls._land_mask(nbm.MIN_FEATURE_COUNT)
            stats = {"min": 1, "p50": 2, "p75": 3, "p90": 4, "p95": 5, "max": 6}
            entries.append(
                {
                    "feed_version": nbm.FEED_VERSION,
                    "model": "NOAA/NWS National Blend of Models",
                    "product": "NBM wind central guidance + p10-p90 uncertainty",
                    "target_lead_hours": lead,
                    "target_label": f"+{lead} hr",
                    "model_cycle_utc": stamp(cycle),
                    "model_cycle_local": "fixture local cycle",
                    "forecast_hour": forecast_hour,
                    "forecast_hour_label": f"f{forecast_hour:03d}",
                    "valid_time_utc": stamp(valid),
                    "valid_time_local": "fixture local valid",
                    "relative_url": relative_path.relative_to(
                        Path("docs/data/wind")
                    ).as_posix(),
                    "feature_count": nbm.MIN_FEATURE_COUNT,
                    "land_mask": land_mask,
                    "grid": {
                        "nx": nbm.EXPECTED_DOMAIN["nx"],
                        "ny": nbm.EXPECTED_DOMAIN["ny"],
                        "resolution_degrees": nbm.EXPECTED_DOMAIN[
                            "resolution_degrees"
                        ],
                    },
                    "wind_p50_mph": copy.deepcopy(stats),
                    "gust_p50_mph": copy.deepcopy(stats),
                    "recommended_gust_scale_mph": 20,
                    "color_scale_reference": "fixture",
                    "source_core": (
                        "https://noaa-nbm-grib2-pds.s3.amazonaws.com/"
                        f"blend.{cycle:%Y%m%d}/{cycle:%H}/core/"
                        f"blend.t{cycle:%H}z.core.f{forecast_hour:03d}.co.grib2"
                    ),
                    "source_qmd": (
                        "https://noaa-nbm-grib2-pds.s3.amazonaws.com/"
                        f"blend.{cycle:%Y%m%d}/{cycle:%H}/qmd/"
                        f"blend.t{cycle:%H}z.qmd.f{forecast_hour:03d}.co.grib2"
                    ),
                }
            )
            valid_times.append(stamp(valid))
        for old_cycle, forecast_hour, lead, old_marker in retained:
            cls._write_target(root, old_cycle, forecast_hour, lead, old_marker)
        manifest = {
            "feed_version": nbm.FEED_VERSION,
            "generated_at_utc": stamp(generated),
            "generated_at_local": "fixture local generation",
            "model": "NOAA/NWS National Blend of Models",
            "product": "Wind central guidance and uncertainty",
            "description": "fixture",
            "update_note": "fixture",
            "domain": copy.deepcopy(nbm.EXPECTED_DOMAIN),
            "target_lead_hours": list(nbm.TARGET_LEADS),
            "published_support_lead_hours": list(nbm.PUBLISHED_LEADS),
            "support_window_hours": nbm.SUPPORT_WINDOW_HOURS,
            "land_mask": {"method": "fixture"},
            "entries": entries,
        }
        summary = {
            "feed_version": nbm.FEED_VERSION,
            "generated_at_utc": stamp(generated),
            "selected_cycle_utc": stamp(cycle),
            "selected_cycle_local": "fixture local cycle",
            "entry_count": len(entries),
            "feature_count_per_entry": [nbm.MIN_FEATURE_COUNT] * len(entries),
            "valid_times_utc": valid_times,
            "target_lead_hours": list(nbm.TARGET_LEADS),
            "published_support_lead_hours": list(nbm.PUBLISHED_LEADS),
            "support_window_hours": nbm.SUPPORT_WINDOW_HOURS,
            "offshore_points_removed_per_entry": [0] * len(entries),
            "outside_operational_mask_removed_per_entry": [0] * len(entries),
            "operational_mask_id": "hydrologic_ca_operational_v1",
            "domain": copy.deepcopy(nbm.EXPECTED_DOMAIN),
        }
        for relative_path, value in (
            (nbm.MANIFEST_PATH, manifest),
            (nbm.SUMMARY_PATH, summary),
        ):
            destination = root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(value, indent=2) + "\n")

    @staticmethod
    def load(root: Path, path: Path) -> dict:
        return json.loads((root / path).read_text())

    @staticmethod
    def dump(root: Path, path: Path, value: dict) -> None:
        (root / path).write_text(json.dumps(value, indent=2) + "\n")


class NbmWindPublisherTests(NbmFixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.candidate = self.root / "candidate"
        self.canonical = self.root / "canonical"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def product(self, root: Path, cycle_hours: int, generated_hours: int, **kwargs):
        self.write_product(
            root,
            self.base + timedelta(hours=cycle_hours),
            self.base + timedelta(hours=generated_hours + 6),
            **kwargs,
        )

    def target_paths(self, root: Path) -> set[Path]:
        return set(nbm.validate_product(root).targets)

    def test_valid_complete_cycle_and_semantic_key(self):
        self.product(self.candidate, 0, 2)
        state = nbm.validate_product(self.candidate)
        self.assertEqual([entry.target_lead for entry in state.entries], list(nbm.PUBLISHED_LEADS))
        self.assertEqual(len(nbm.semantic_key(self.candidate)), 64)

    def test_newer_complete_cycle_is_new(self):
        self.product(self.canonical, -6, -4)
        self.product(self.candidate, 0, 2)
        self.assertEqual(nbm.reconcile(self.candidate, self.canonical)[0], "new")
        self.assertEqual(nbm.validate_product(self.canonical).current_cycle, self.base)

    def test_same_complete_cycle_is_same(self):
        self.product(self.candidate, 0, 2)
        shutil.copytree(self.candidate, self.canonical)
        self.assertEqual(nbm.reconcile(self.candidate, self.canonical)[0], "same")

    def test_older_cycle_is_stale(self):
        self.product(self.canonical, 0, 2)
        self.product(self.candidate, -6, -4)
        self.assertEqual(nbm.reconcile(self.candidate, self.canonical)[0], "stale")

    def test_incomplete_candidate_cycle_is_rejected(self):
        self.product(self.candidate, 0, 2)
        manifest = self.load(self.candidate, nbm.MANIFEST_PATH)
        manifest["entries"].pop()
        self.dump(self.candidate, nbm.MANIFEST_PATH, manifest)
        with self.assertRaisesRegex(nbm.ProductError, "exactly seven"):
            nbm.validate_product(self.candidate)

    def test_missing_required_lead_is_rejected(self):
        self.product(self.candidate, 0, 2)
        manifest = self.load(self.candidate, nbm.MANIFEST_PATH)
        manifest["entries"][3]["target_lead_hours"] = 21
        self.dump(self.candidate, nbm.MANIFEST_PATH, manifest)
        with self.assertRaisesRegex(nbm.ProductError, "target identity"):
            nbm.validate_product(self.candidate)

    def test_duplicate_lead_is_rejected(self):
        self.product(self.candidate, 0, 2)
        manifest = self.load(self.candidate, nbm.MANIFEST_PATH)
        manifest["entries"][1] = copy.deepcopy(manifest["entries"][0])
        self.dump(self.candidate, nbm.MANIFEST_PATH, manifest)
        with self.assertRaisesRegex(nbm.ProductError, "duplicated|unsorted"):
            nbm.validate_product(self.candidate)

    def test_wrong_cycle_target_is_rejected(self):
        self.product(self.candidate, 0, 2)
        state = nbm.validate_product(self.candidate)
        path = state.entries[0].relative_path
        target = self.load(self.candidate, path)
        target["metadata"]["model_cycle_utc"] = stamp(self.base - timedelta(hours=6))
        self.dump(self.candidate, path, target)
        with self.assertRaisesRegex(nbm.ProductError, "disagrees with filename"):
            nbm.validate_product(self.candidate)

    def test_same_cycle_conflicting_bytes_fail_closed(self):
        self.product(self.canonical, 0, 2, marker=1)
        self.product(self.candidate, 0, 2, marker=9)
        with self.assertRaisesRegex(nbm.ProductError, "same-generation"):
            nbm.reconcile(self.candidate, self.canonical)

    def test_canonical_target_bytes_are_reused(self):
        self.product(self.canonical, -6, -4, marker=3)
        old_path = next(iter(nbm.validate_product(self.canonical).targets))
        old_bytes = (self.canonical / old_path).read_bytes()
        self.product(self.candidate, 0, 2, marker=5)
        nbm.reconcile(self.candidate, self.canonical)
        self.assertEqual((self.canonical / old_path).read_bytes(), old_bytes)

    def test_retained_prior_cycle_target_survives(self):
        old_cycle = self.base - timedelta(hours=12)
        self.product(self.canonical, -6, -4, retained=((old_cycle, 8, 6, 2),))
        retained_path = nbm.TARGET_ROOT / f"nbm_wind_guidance_{old_cycle:%Y%m%dT%H}Z_f008.geojson"
        self.product(self.candidate, 0, 2)
        nbm.reconcile(self.candidate, self.canonical)
        self.assertIn(retained_path, self.target_paths(self.canonical))

    def test_retention_boundary_is_inclusive(self):
        boundary = self.base - timedelta(hours=18)
        self.product(self.canonical, -6, -4, retained=((boundary, 8, 6, 2),))
        boundary_path = nbm.TARGET_ROOT / f"nbm_wind_guidance_{boundary:%Y%m%dT%H}Z_f008.geojson"
        self.product(self.candidate, 0, 2)
        nbm.reconcile(self.candidate, self.canonical)
        self.assertIn(boundary_path, self.target_paths(self.canonical))

    def test_expired_prior_cycle_target_is_pruned(self):
        expired = self.base - timedelta(hours=24)
        self.product(self.canonical, -6, -4, retained=((expired, 8, 6, 2),))
        expired_path = nbm.TARGET_ROOT / f"nbm_wind_guidance_{expired:%Y%m%dT%H}Z_f008.geojson"
        self.product(self.candidate, 0, 2)
        nbm.reconcile(self.candidate, self.canonical)
        self.assertNotIn(expired_path, self.target_paths(self.canonical))

    def test_multiple_expired_targets_are_pruned(self):
        old = self.base - timedelta(hours=24)
        self.product(
            self.canonical,
            -6,
            -4,
            retained=((old, 8, 6, 2), (old, 14, 12, 3), (old, 20, 18, 4)),
        )
        self.product(self.candidate, 0, 2)
        nbm.reconcile(self.candidate, self.canonical)
        self.assertTrue(all(target.cycle >= self.base - timedelta(hours=18) for target in nbm.validate_product(self.canonical).targets.values()))

    def test_orphan_non_product_target_is_rejected(self):
        self.product(self.candidate, 0, 2)
        orphan = self.candidate / nbm.TARGET_ROOT / "orphan.geojson"
        orphan.write_text("{}\n")
        with self.assertRaisesRegex(nbm.ProductError, "filename is invalid"):
            nbm.validate_product(self.candidate)

    def test_missing_referenced_target_is_rejected(self):
        self.product(self.candidate, 0, 2)
        entry = nbm.validate_product(self.candidate).entries[0]
        (self.candidate / entry.relative_path).unlink()
        with self.assertRaisesRegex(nbm.ProductError, "missing target"):
            nbm.validate_product(self.candidate)

    def test_malformed_candidate_manifest_is_rejected(self):
        self.product(self.candidate, 0, 2)
        (self.candidate / nbm.MANIFEST_PATH).write_text("not json\n")
        with self.assertRaisesRegex(nbm.ProductError, "unreadable"):
            nbm.validate_product(self.candidate)

    def test_malformed_canonical_state_fails_closed(self):
        self.product(self.candidate, 0, 2)
        self.product(self.canonical, -6, -4)
        (self.canonical / nbm.SUMMARY_PATH).write_text("{}\n")
        with self.assertRaises(nbm.ProductError):
            nbm.reconcile(self.candidate, self.canonical)

    def test_fresh_main_same_cycle_advance_is_reconciled(self):
        self.product(self.canonical, 0, 2, marker=1)
        self.product(self.candidate, 0, 3, marker=1)
        self.assertEqual(nbm.reconcile(self.candidate, self.canonical)[0], "new")
        self.assertEqual(nbm.validate_product(self.canonical).generated, self.base + timedelta(hours=9))

    def test_fresh_main_newer_cycle_makes_candidate_stale(self):
        self.product(self.canonical, 6, 8)
        self.product(self.candidate, 0, 2)
        self.assertEqual(nbm.reconcile(self.candidate, self.canonical)[0], "stale")

    def test_callback_protocol_validates_and_reconciles_schema2(self):
        self.product(self.canonical, -6, -4)
        self.product(self.candidate, 0, 2)
        metadata = self.root / "candidate-metadata.json"
        metadata.write_text(
            json.dumps(
                {
                    "semantic_key": {
                        "type": "nbm_wind_complete_cycle_state_sha256_v1",
                        "value": nbm.semantic_key(self.candidate),
                    }
                }
            )
        )
        result = self.root / "result.json"
        environment = {
            "BRIM_PUBLISH_PRODUCT_ID": nbm.PRODUCT_ID,
            "BRIM_PUBLISH_CANDIDATE_ROOT": str(self.candidate),
            "BRIM_PUBLISH_WORKTREE": str(self.canonical),
            "BRIM_PUBLISH_METADATA": str(metadata),
            "BRIM_PUBLISH_RESULT": str(result),
        }
        with mock.patch.dict(os.environ, {**environment, "BRIM_PUBLISH_PHASE": "validate-candidate"}, clear=True):
            self.assertEqual(nbm._callback(), 0)
        with mock.patch.dict(os.environ, {**environment, "BRIM_PUBLISH_PHASE": "reconcile"}, clear=True):
            self.assertEqual(nbm._callback(), 0)
        self.assertEqual(json.loads(result.read_text())["candidate_state"], "new")
        with mock.patch.dict(os.environ, {**environment, "BRIM_PUBLISH_PHASE": "validate-staged"}, clear=True):
            self.assertEqual(nbm._callback(), 0)

    def test_candidate_metadata_semantic_key_mismatch_is_rejected(self):
        self.product(self.candidate, 0, 2)
        self.product(self.canonical, -6, -4)
        metadata = self.root / "candidate-metadata.json"
        metadata.write_text(json.dumps({"semantic_key": {"type": "wrong", "value": "wrong"}}))
        with mock.patch.dict(
            os.environ,
            {
                "BRIM_PUBLISH_PHASE": "validate-candidate",
                "BRIM_PUBLISH_PRODUCT_ID": nbm.PRODUCT_ID,
                "BRIM_PUBLISH_CANDIDATE_ROOT": str(self.candidate),
                "BRIM_PUBLISH_WORKTREE": str(self.canonical),
                "BRIM_PUBLISH_METADATA": str(metadata),
            },
            clear=True,
        ):
            with self.assertRaisesRegex(nbm.ProductError, "semantic key"):
                nbm._callback()


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
        count_path.write_text(str(count))
        racer = Path(os.environ["RACE_CLONE"])
        subprocess.run([git, "-C", str(racer), "fetch", "origin", "main"], check=True)
        subprocess.run([git, "-C", str(racer), "checkout", "-B", "main", "origin/main"], check=True)
        race_file = racer / f"unrelated-race-{count}.txt"
        race_file.write_text(f"race {count}\n")
        subprocess.run([git, "-C", str(racer), "add", race_file.name], check=True)
        subprocess.run([git, "-C", str(racer), "commit", "-m", f"Race {count}"], check=True)
        subprocess.run([git, "-C", str(racer), "push", "origin", "HEAD:refs/heads/main"], check=True)
os.execv(git, [git, *args])
'''


class NbmWindMainPublisherIntegrationTests(NbmFixtureMixin, unittest.TestCase):
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
            self.base + timedelta(hours=2),
        )
        run(["git", "init", str(self.seed)], cwd=self.root)
        run(["git", "config", "user.name", "Test Author"], cwd=self.seed)
        run(["git", "config", "user.email", "test@example.invalid"], cwd=self.seed)
        callback = self.seed / "scripts" / "nbm_wind_publisher.py"
        callback.parent.mkdir(parents=True)
        shutil.copyfile(REPO / "scripts" / "nbm_wind_publisher.py", callback)
        sibling = self.seed / "docs/data/sibling.json"
        sibling.parent.mkdir(parents=True, exist_ok=True)
        sibling.write_text("{\"preserve\":true}\n")
        run(["git", "add", "."], cwd=self.seed)
        run(["git", "commit", "-m", "Initial main"], cwd=self.seed)
        run(["git", "branch", "-M", "main"], cwd=self.seed)
        run(["git", "remote", "add", "origin", str(self.remote)], cwd=self.seed)
        run(["git", "push", "-u", "origin", "main"], cwd=self.seed)
        run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=self.remote)
        run(["git", "clone", str(self.remote), str(self.runner)], cwd=self.root)
        self.source_sha = run(["git", "rev-parse", "HEAD"], cwd=self.runner).stdout.strip()
        self.callback = self.runner / "scripts" / "nbm_wind_publisher.py"
        self.write_product(self.candidate, self.base, self.base + timedelta(hours=8))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def prepare(self, *, extra_allowlist=()):
        command = [
            sys.executable,
            str(MAIN_PUBLISHER),
            "prepare",
            "--candidate-root",
            str(self.candidate),
            "--output",
            str(self.metadata),
            "--product-id",
            nbm.PRODUCT_ID,
            "--semantic-key-type",
            "nbm_wind_complete_cycle_state_sha256_v1",
            "--semantic-key",
            nbm.semantic_key(self.candidate),
            "--source-event-sha",
            self.source_sha,
            "--allowlist",
            nbm.MANIFEST_PATH.as_posix(),
            "--allowlist",
            nbm.SUMMARY_PATH.as_posix(),
            "--owned-root",
            nbm.TARGET_ROOT.as_posix(),
        ]
        for path in extra_allowlist:
            command.extend(("--allowlist", path))
        return run(command, cwd=REPO, check=False)

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
                nbm.PRODUCT_ID,
                "--commit-subject",
                "Update NBM wind guidance live feed",
                "--allowlist",
                nbm.MANIFEST_PATH.as_posix(),
                "--allowlist",
                nbm.SUMMARY_PATH.as_posix(),
                "--owned-root",
                nbm.TARGET_ROOT.as_posix(),
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

    def install_race_wrapper(self, limit: int):
        racer = self.root / "racer"
        run(["git", "clone", str(self.remote), str(racer)], cwd=self.root)
        run(["git", "config", "user.name", "Race Author"], cwd=racer)
        run(["git", "config", "user.email", "race@example.invalid"], cwd=racer)
        wrapper_dir = self.root / "bin"
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / "git"
        wrapper.write_text(RACE_WRAPPER)
        wrapper.chmod(0o755)
        return self.environment(
            PATH=f"{wrapper_dir}{os.pathsep}{os.environ['PATH']}",
            REAL_GIT=shutil.which("git"),
            RACE_CLONE=str(racer),
            RACE_LIMIT=str(limit),
            RACE_COUNT=str(self.root / "race-count"),
        )

    def test_exact_schema2_staging_publishes_only_nbm_owned_paths(self):
        self.assertEqual(self.prepare().returncode, 0)
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        changed = run(
            ["git", "--git-dir", str(self.remote), "diff-tree", "--no-commit-id", "--name-only", "-r", "main"],
            cwd=self.root,
        ).stdout.splitlines()
        self.assertTrue(changed)
        self.assertTrue(
            all(
                path in {nbm.MANIFEST_PATH.as_posix(), nbm.SUMMARY_PATH.as_posix()}
                or path.startswith(nbm.TARGET_ROOT.as_posix() + "/")
                for path in changed
            )
        )
        self.assertEqual(
            run(["git", "--git-dir", str(self.remote), "show", "main:docs/data/sibling.json"], cwd=self.root).stdout,
            '{"preserve":true}\n',
        )

    def test_prepare_rejects_ownership_violation(self):
        unexpected = self.candidate / "docs/data/unowned.json"
        unexpected.parent.mkdir(parents=True, exist_ok=True)
        unexpected.write_text("{}\n")
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the allowlist/declared owned roots", result.stderr)

    def test_one_push_race_retries_once_and_succeeds(self):
        self.assertEqual(self.prepare().returncode, 0)
        result = self.publish(env=self.install_race_wrapper(1))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("retrying reconciliation exactly once", result.stdout)
        self.assertEqual((self.root / "race-count").read_text(), "1")

    def test_unresolved_second_push_race_fails_closed(self):
        self.assertEqual(self.prepare().returncode, 0)
        result = self.publish(env=self.install_race_wrapper(2))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("advanced during both publication attempts", result.stderr)
        self.assertEqual((self.root / "race-count").read_text(), "2")


if __name__ == "__main__":
    unittest.main()
