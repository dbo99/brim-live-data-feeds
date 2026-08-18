#!/usr/bin/env python3
"""Deterministic tests for the Phase 3 fixed-file publisher callbacks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
import main_publisher  # noqa: E402


COCORAHS_CALLBACK = SCRIPTS / "cocorahs_publisher.py"
STREAMFLOW_CALLBACK = SCRIPTS / "usgs_streamflow_publisher.py"
GROUNDWATER_CALLBACK = SCRIPTS / "usgs_groundwater_publisher.py"
PUBLISHER = SCRIPTS / "main_publisher.py"

COCORAHS_PATHS = (
    Path("docs/data/cocorahs_daily_precip_ca_latest.geojson"),
    Path("docs/data/cocorahs_daily_precip_ca_latest_summary.json"),
    Path("docs/data/cocorahs_daily_precip_conus_latest.geojson"),
    Path("docs/data/cocorahs_daily_precip_conus_latest_summary.json"),
)
STREAMFLOW_PATHS = (
    Path("docs/data/usgs_streamflow_latest_ca.geojson"),
    Path("docs/data/usgs_streamflow_latest_ca_summary.json"),
)
GROUNDWATER_PATHS = (
    Path("docs/data/usgs_groundwater_latest_ca.geojson"),
    Path("docs/data/usgs_groundwater_latest_ca_summary.json"),
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def point_feature(properties: dict[str, object], coordinates: list[float]) -> dict[str, object]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": coordinates},
        "properties": properties,
    }


def write_cocorahs_product(root: Path, build_time: str, *, variant: str = "normal") -> None:
    def feature(station: str, name: str, longitude: float, latitude: float) -> dict[str, object]:
        return point_feature(
            {
                "stationNumber": station,
                "stationName": f"{name}-{variant}",
                "obsDateTime": "2026-08-18T07:00:00-07:00",
                "feedBuildTimeUtc": build_time,
                "source": "CoCoRaHS DailyPrecipObs API",
                "stationUrl": f"https://example.invalid/{station}",
                "units": "english",
                "sourceWindowStartDate": "2026-08-17",
                "sourceWindowEndDate": "2026-08-18",
                "longitude": longitude,
                "latitude": latitude,
                "precip": 0.5,
                "gaugeCatch": 0.5,
                "precipIsTrace": False,
                "gaugeCatchIsTrace": False,
            },
            [longitude, latitude],
        )

    ca_feature = feature("CA-AA-1", "California", -121.0, 38.0)
    conus_features = [ca_feature, feature("CO-AA-1", "Colorado", -105.0, 39.0)]
    for scope, name, features, geo_path, summary_path in (
        (
            "California",
            "cocorahs_daily_precip_ca_latest",
            [ca_feature],
            COCORAHS_PATHS[0],
            COCORAHS_PATHS[1],
        ),
        (
            "CONUS",
            "cocorahs_daily_precip_conus_latest",
            conus_features,
            COCORAHS_PATHS[2],
            COCORAHS_PATHS[3],
        ),
    ):
        summary = {
            "scope": scope,
            "output_feature_count": len(features),
            "feed_build_time_utc": build_time,
            "start_date": "2026-08-17",
            "end_date": "2026-08-18",
            "units": "english",
        }
        write_json(root / summary_path, summary)
        write_json(
            root / geo_path,
            {"type": "FeatureCollection", "name": name, "metadata": summary, "features": features},
        )


def write_streamflow_product(root: Path, build_time: str, *, variant: str = "normal") -> None:
    properties = {
        "site_no": "12345678",
        "station_nm": f"Stream-{variant}",
        "latest_status": "latest_iv_discharge",
        "feed_source": "USGS Water Data API",
        "feed_build_time_utc": build_time,
        "has_latest_iv_q": True,
        "has_latest_iv_stage": True,
        "q_cfs": 12.5,
        "stage_ft": 2.5,
        "q_datetime_utc": "2026-08-18T11:00:00Z",
        "stage_datetime_utc": "2026-08-18T11:00:00Z",
        "q_obs_age_hours": 1.0,
        "stage_obs_age_hours": 1.0,
        "q_stale_6h": False,
        "q_stale_24h": False,
        "q_stale_72h": False,
    }
    features = [point_feature(properties, [-121.0, 38.0])]
    write_json(
        root / STREAMFLOW_PATHS[0],
        {
            "type": "FeatureCollection",
            "name": "USGS Streamflow Latest CA",
            "metadata": {"feed_build_time_utc": build_time, "scope": "CA"},
            "features": features,
        },
    )
    write_json(
        root / STREAMFLOW_PATHS[1],
        {
            "feed_build_time_utc": build_time,
            "scope": "CA",
            "station_index_rows": 1,
            "output_feature_count": 1,
            "latest_iv_discharge_count": 1,
            "latest_iv_stage_count": 1,
            "stale_discharge_6h_count": 0,
            "stale_discharge_24h_count": 0,
            "stale_discharge_72h_count": 0,
        },
    )


def write_groundwater_product(root: Path, build_time: str, *, variant: str = "normal") -> None:
    properties = {
        "site_no": "87654321",
        "station_nm": f"Well-{variant}",
        "latest_wl_units": "ft",
        "latest_wl_source": "USGS Water Data API",
        "latest_wl_status": "approved",
        "latest_status": "latest_api_measurement",
        "latest_wl_ft_bgs": 20.5,
        "latest_age_days": 4.0,
        "latest_wl_datetime_utc": "2026-08-14T12:00:00Z",
        "latest_wl_date": "2026-08-14",
        "has_api_latest_wl": True,
        "feed_build_time_utc": build_time,
    }
    write_json(
        root / GROUNDWATER_PATHS[0],
        {
            "type": "FeatureCollection",
            "name": "USGS Groundwater Latest CA",
            "metadata": {
                "feed_build_time_utc": build_time,
                "scope": "CA",
                "allow_index_fallback": True,
            },
            "features": [point_feature(properties, [-120.0, 37.0])],
        },
    )
    write_json(
        root / GROUNDWATER_PATHS[1],
        {
            "feed_build_time_utc": build_time,
            "scope": "CA",
            "station_index_rows": 1,
            "output_feature_count": 1,
            "api_latest_site_count": 1,
            "index_fallback_count": 0,
            "allow_index_fallback": True,
        },
    )


class CallbackCase:
    callback: Path
    product_id: str
    paths: tuple[Path, ...]
    writer = staticmethod(write_cocorahs_product)

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
        write_json(path, {"semantic_key": {"type": "feed_build_time_utc", "value": value}})
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

    def test_new_same_and_stale_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            self.writer(candidate, "2026-08-18T12:00:00Z")
            self.writer(worktree, "2026-08-18T09:00:00Z")
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

            self.writer(candidate, "2026-08-18T08:00:00Z")
            result = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, "2026-08-18T08:00:00Z"),
                result=result_path,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result_path.read_text())["candidate_state"], "stale")

    def test_equal_timestamp_with_different_bytes_is_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            build_time = "2026-08-18T12:00:00Z"
            self.writer(candidate, build_time, variant="candidate")
            self.writer(worktree, build_time, variant="canonical")
            result = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, build_time),
                result=root / "result.json",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ambiguous overwrite", result.stderr)

    def test_malformed_partial_and_malformed_canonical_fail_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            build_time = "2026-08-18T12:00:00Z"
            self.writer(candidate, build_time)
            self.writer(worktree, "2026-08-18T09:00:00Z")
            (candidate / self.paths[-1]).unlink()
            self.assertNotEqual(self.validate(candidate).returncode, 0)

            self.writer(candidate, build_time)
            write_json(candidate / self.paths[-1], {"malformed": True})
            self.assertNotEqual(self.validate(candidate).returncode, 0)

            self.writer(candidate, build_time)
            (worktree / self.paths[-1]).unlink()
            partial = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, build_time),
                result=root / "result.json",
            )
            self.assertNotEqual(partial.returncode, 0)
            self.assertIn("partial", partial.stderr)

            self.writer(worktree, "2026-08-18T09:00:00Z")
            write_json(worktree / self.paths[-1], {"malformed": True})
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

    def test_candidate_and_staged_callback_phases_validate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            build_time = "2026-08-18T12:00:00Z"
            self.writer(candidate, build_time)
            self.writer(worktree, build_time)
            metadata = self.metadata(root, build_time)
            for phase in ("validate-candidate", "validate-staged"):
                result = self.run_callback(
                    phase=phase,
                    candidate=candidate,
                    worktree=worktree,
                    metadata=metadata,
                    result=root / "result.json",
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_exact_candidate_and_staged_allowlists_reject_unexpected_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            build_time = "2026-08-18T12:00:00Z"
            self.writer(candidate, build_time)
            write_json(candidate / "docs/data/unexpected.json", {"unexpected": True})
            command = [
                sys.executable,
                str(PUBLISHER),
                "prepare",
                "--candidate-root",
                str(candidate),
                "--output",
                str(root / "candidate-metadata.json"),
                "--product-id",
                self.product_id,
                "--semantic-key-type",
                "feed_build_time_utc",
                "--semantic-key",
                build_time,
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

            allowed = {path.as_posix() for path in self.paths}
            with self.assertRaises(main_publisher.PublisherError):
                main_publisher._require_allowed(
                    allowed | {"docs/data/unexpected.json"},
                    allowed,
                    "staging",
                )


class CoCoRaHSPublisherTests(CallbackCase, unittest.TestCase):
    callback = COCORAHS_CALLBACK
    product_id = "cocorahs-daily-precip"
    paths = COCORAHS_PATHS
    writer = staticmethod(write_cocorahs_product)


class StreamflowPublisherTests(CallbackCase, unittest.TestCase):
    callback = STREAMFLOW_CALLBACK
    product_id = "usgs-streamflow-latest-ca"
    paths = STREAMFLOW_PATHS
    writer = staticmethod(write_streamflow_product)


class GroundwaterPublisherTests(CallbackCase, unittest.TestCase):
    callback = GROUNDWATER_CALLBACK
    product_id = "usgs-groundwater-latest-ca"
    paths = GROUNDWATER_PATHS
    writer = staticmethod(write_groundwater_product)


if __name__ == "__main__":
    unittest.main()
