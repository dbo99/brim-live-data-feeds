#!/usr/bin/env python3
"""Offline tests for CDEC candidate validation and reconciliation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CALLBACK = REPO / "scripts" / "cdec_reservoir_publisher.py"
GEOJSON = Path("docs/data/cdec_reservoir_latest.geojson")
SUMMARY = Path("docs/data/cdec_reservoir_latest_summary.json")


def write_product(root: Path, build_time: str, *, reservoir_name: str = "Test Lake") -> None:
    geojson = {
        "type": "FeatureCollection",
        "name": "cdec_reservoir_latest_storage",
        "feed_build_time_utc": build_time,
        "feature_count": 1,
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-120.25, 38.5]},
                "properties": {
                    "cdec_id": "TST",
                    "reservoir_name": reservoir_name,
                    "display_storage_source": "cdec_latest",
                    "source_url": "https://example.invalid/cdec",
                    "feed_build_time_utc": build_time,
                    "has_storage_value": True,
                    "has_latest_storage": True,
                    "display_storage_af": 1000,
                    "obs_stale_12h": False,
                    "obs_stale_24h": False,
                },
            }
        ],
    }
    summary = {
        "feed_build_time_utc": build_time,
        "output_feature_count": 1,
        "output_features_with_latest_storage": 1,
        "min_latest_rows_to_publish": 1,
        "allow_degraded_publish": False,
    }
    for relative_path, value in ((GEOJSON, geojson), (SUMMARY, summary)):
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8")


class CdecPublisherTests(unittest.TestCase):
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
                "BRIM_PUBLISH_PRODUCT_ID": "cdec-reservoir-feed",
            }
        )
        return subprocess.run(
            [sys.executable, str(CALLBACK)],
            cwd=REPO,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def metadata(self, root: Path, build_time: str) -> Path:
        path = root / "candidate-metadata.json"
        path.write_text(
            json.dumps(
                {
                    "semantic_key": {
                        "type": "feed_build_time_utc",
                        "value": build_time,
                    }
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_new_candidate_is_copied_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            write_product(candidate, "2026-08-17T12:00:00Z")
            write_product(worktree, "2026-08-17T09:00:00Z")
            metadata = self.metadata(root, "2026-08-17T12:00:00Z")
            result_path = root / "result.json"

            result = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=metadata,
                result=result_path,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result_path.read_text())["candidate_state"], "new")
            for relative_path in (GEOJSON, SUMMARY):
                self.assertEqual(
                    (worktree / relative_path).read_bytes(),
                    (candidate / relative_path).read_bytes(),
                )

    def test_same_candidate_is_a_clean_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            write_product(candidate, "2026-08-17T12:00:00Z")
            write_product(worktree, "2026-08-17T12:00:00Z")
            before = [(worktree / path).read_bytes() for path in (GEOJSON, SUMMARY)]
            result_path = root / "result.json"
            result = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, "2026-08-17T12:00:00Z"),
                result=result_path,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result_path.read_text())["candidate_state"], "same")
            self.assertEqual(
                before, [(worktree / path).read_bytes() for path in (GEOJSON, SUMMARY)]
            )

    def test_stale_candidate_is_a_clean_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            write_product(candidate, "2026-08-17T09:00:00Z")
            write_product(worktree, "2026-08-17T12:00:00Z")
            before = [(worktree / path).read_bytes() for path in (GEOJSON, SUMMARY)]
            result_path = root / "result.json"
            result = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, "2026-08-17T09:00:00Z"),
                result=result_path,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result_path.read_text())["candidate_state"], "stale")
            self.assertEqual(
                before, [(worktree / path).read_bytes() for path in (GEOJSON, SUMMARY)]
            )

    def test_same_time_with_different_bytes_fails_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            build_time = "2026-08-17T12:00:00Z"
            write_product(candidate, build_time, reservoir_name="Candidate Lake")
            write_product(worktree, build_time, reservoir_name="Canonical Lake")
            result = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, build_time),
                result=root / "result.json",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ambiguous overwrite", result.stderr)

    def test_candidate_validation_rejects_nonfinite_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            write_product(candidate, "2026-08-17T12:00:00Z")
            geojson = json.loads((candidate / GEOJSON).read_text())
            geojson["features"][0]["geometry"]["coordinates"][0] = float("nan")
            (candidate / GEOJSON).write_text(json.dumps(geojson), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(CALLBACK), "validate", "--root", str(candidate)],
                cwd=REPO,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not finite", result.stderr)


if __name__ == "__main__":
    unittest.main()
