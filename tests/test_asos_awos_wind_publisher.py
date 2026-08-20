#!/usr/bin/env python3
"""Offline ASOS/AWOS shared-publisher and workflow invariance tests."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
import main_publisher  # noqa: E402


CALLBACK = SCRIPTS / "asos_awos_wind_publisher.py"
PUBLISHER = SCRIPTS / "main_publisher.py"
WORKFLOW = REPO / ".github/workflows/build-asos-awos-wind-latest.yml"
WATCHDOG = REPO / ".github/workflows/check-wind-feeds.yml"
GEOJSON_PATH = Path("docs/data/wind/asos_awos_wind_latest.geojson")
SUMMARY_PATH = Path("docs/data/wind/asos_awos_wind_latest_summary.json")
MANIFEST_PATH = Path("docs/data/wind/asos_awos_wind_feed_manifest.json")
PATHS = (GEOJSON_PATH, SUMMARY_PATH, MANIFEST_PATH)
PUBLIC_FILES = {
    "geojson": GEOJSON_PATH.as_posix(),
    "summary_json": SUMMARY_PATH.as_posix(),
    "manifest_json": MANIFEST_PATH.as_posix(),
}


def utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def stamp(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, value: object, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(value, separators=(",", ":")) + "\n"
    else:
        text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def write_product(
    root: Path,
    build_time_text: str,
    *,
    newest_observation_text: str | None = None,
    variant: str = "normal",
) -> None:
    build_time = utc(build_time_text)
    newest = utc(newest_observation_text) if newest_observation_text else build_time - dt.timedelta(minutes=5)
    features = []
    ages = []
    for index in range(25):
        observation = newest - dt.timedelta(minutes=index)
        age_hours = (build_time - observation).total_seconds() / 3600.0
        ages.append(age_hours)
        has_gust = index % 5 == 0
        gust_kt = 15.0 if has_gust else None
        properties = {
            "product_id": "asos_awos_wind_obs",
            "station_id": f"K{index:03d}",
            "observation_time_utc": stamp(observation),
            "observation_time_local": stamp(observation),
            "age_hours": round(age_hours, 3),
            "age_minutes": round(age_hours * 60),
            "age_display": f"{round(age_hours * 60)} min",
            "wind_dir_degrees": 180,
            "wind_from_degrees": 180,
            "wind_to_degrees": 0,
            "wind_dir_cardinal": "S",
            "wind_speed_kt": 10,
            "wind_barb_speed_kt": 10,
            "wind_speed_mph": 11.5,
            "wind_speed_ms": 5.14,
            "wind_gust_kt": gust_kt,
            "wind_gust_mph": 17.3 if has_gust else None,
            "wind_gust_ms": 7.72 if has_gust else None,
            "wind_gust_display": "17 mph" if has_gust else "not reported",
            "has_gust": has_gust,
            "calm": False,
            "display_label": f"K{index:03d} 12 mph {variant}",
            "speed_gust_label": f"K{index:03d} 12 mph {variant}",
            "label_short": f"K{index:03d} 12",
            "style_class": "wind_10plus",
            "metar_type": "METAR",
            "flight_category": "VFR",
            "elevation_m": 10,
            "raw_text": f"fixture-{variant}-{index}",
            "source": "NOAA/NWS Aviation Weather Center METAR cache",
        }
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [-124.0 + index * 0.1, 32.0 + index * 0.1],
                },
                "properties": properties,
            }
        )

    geojson = {
        "type": "FeatureCollection",
        "name": "asos_awos_wind_latest",
        "product_id": "asos_awos_wind_obs",
        "generated_utc": build_time_text,
        "source": "NOAA/NWS Aviation Weather Center METAR cache",
        "bbox": [-125.5, 31.0, -112.0, 43.5],
        "features": features,
    }
    write_json(root / GEOJSON_PATH, geojson, compact=True)
    newest_age = (build_time - newest).total_seconds() / 3600.0
    gust_count = sum(feature["properties"]["has_gust"] for feature in features)
    summary = {
        "product_id": "asos_awos_wind_obs",
        "product": "Observed wind speed + gusts from METAR/ASOS stations",
        "version": "RTW019",
        "status": "success",
        "domain_id": "hydrologic_ca_adjacent",
        "domain_label": "Hydrologic California + adjacent basins",
        "source": "NOAA/NWS Aviation Weather Center METAR cache",
        "source_url": "https://example.invalid/metars.csv.gz",
        "build_time_utc": build_time_text,
        "oldest_observation_utc": stamp(newest - dt.timedelta(minutes=24)),
        "newest_observation_utc": stamp(newest),
        "newest_observation_age_hours": round(newest_age, 3),
        "newest_observation_age_minutes": round(newest_age * 60),
        "max_age_filter_hours": 3.0,
        "stale_after_hours": 2.0,
        "is_stale": newest_age > 2.0,
        "feature_count": len(features),
        "station_count": len(features),
        "gust_feature_count": gust_count,
        "gust_feature_percent": round(100 * gust_count / len(features), 1),
        "observation_age_hours": {"median": round(ages[12], 3), "max": round(max(ages), 3)},
        "domain": {"west": -125.5, "east": -112.0, "south": 31.0, "north": 43.5},
        "output_geojson_bytes": (root / GEOJSON_PATH).stat().st_size,
        "output_files": PUBLIC_FILES,
    }
    write_json(root / SUMMARY_PATH, summary)
    manifest = {
        "product_id": "asos_awos_wind_obs",
        "version": "RTW019",
        "public_files": PUBLIC_FILES,
        "source": "NOAA/NWS Aviation Weather Center METAR cache",
        "source_url": "https://example.invalid/metars.csv.gz",
        "build_time_utc": build_time_text,
        "newest_observation_utc": stamp(newest),
        "newest_observation_age_hours": round(newest_age, 3),
        "newest_observation_age_minutes": round(newest_age * 60),
        "stale_after_hours": 2.0,
        "is_stale": newest_age > 2.0,
        "domain_id": "hydrologic_ca_adjacent",
        "feature_count": len(features),
        "station_count": len(features),
        "gust_feature_count": gust_count,
        "gust_feature_percent": round(100 * gust_count / len(features), 1),
        "file_bytes": {
            "geojson": (root / GEOJSON_PATH).stat().st_size,
            "summary_json": (root / SUMMARY_PATH).stat().st_size,
        },
        "generated_utc": build_time_text,
    }
    write_json(root / MANIFEST_PATH, manifest)


class AsosAwosPublisherTests(unittest.TestCase):
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
                "BRIM_PUBLISH_PRODUCT_ID": "asos-awos-wind-latest",
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

    @staticmethod
    def metadata(root: Path, value: str) -> Path:
        path = root / "candidate-metadata.json"
        write_json(path, {"semantic_key": {"type": "build_time_utc", "value": value}})
        return path

    def validate(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CALLBACK), "validate", "--root", str(root)],
            cwd=REPO,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_current_canonical_and_callback_phases_validate(self) -> None:
        current = self.validate(REPO)
        self.assertEqual(current.returncode, 0, current.stderr)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            build_time = "2026-08-18T12:00:00Z"
            write_product(candidate, build_time)
            write_product(worktree, build_time)
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

    def test_new_same_stale_and_fresh_main_advancement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            write_product(candidate, "2026-08-18T12:00:00Z")
            write_product(worktree, "2026-08-18T11:00:00Z")
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
            for path in PATHS:
                self.assertEqual((candidate / path).read_bytes(), (worktree / path).read_bytes())
            self.assertEqual(json.loads(unrelated.read_text()), {"preserve": True})

            same = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, "2026-08-18T12:00:00Z"),
                result=result_path,
            )
            self.assertEqual(same.returncode, 0, same.stderr)
            self.assertEqual(json.loads(result_path.read_text())["candidate_state"], "same")

            write_product(candidate, "2026-08-18T10:00:00Z")
            stale = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, "2026-08-18T10:00:00Z"),
                result=result_path,
            )
            self.assertEqual(stale.returncode, 0, stale.stderr)
            self.assertEqual(json.loads(result_path.read_text())["candidate_state"], "stale")

            # A fresh-main advance may make a formerly new candidate stale.
            write_product(worktree, "2026-08-18T13:00:00Z")
            write_product(candidate, "2026-08-18T12:30:00Z")
            advanced = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, "2026-08-18T12:30:00Z"),
                result=result_path,
            )
            self.assertEqual(advanced.returncode, 0, advanced.stderr)
            self.assertEqual(json.loads(result_path.read_text())["candidate_state"], "stale")

    def test_newer_build_with_regressed_observations_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            write_product(worktree, "2026-08-18T12:00:00Z", newest_observation_text="2026-08-18T11:55:00Z")
            write_product(candidate, "2026-08-18T13:00:00Z", newest_observation_text="2026-08-18T11:50:00Z")
            result_path = root / "result.json"
            result = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, "2026-08-18T13:00:00Z"),
                result=result_path,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result_path.read_text())["candidate_state"], "stale")

    def test_equal_semantic_key_with_different_bytes_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            build_time = "2026-08-18T12:00:00Z"
            write_product(candidate, build_time, variant="candidate")
            write_product(worktree, build_time, variant="canonical")
            result = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, build_time),
                result=root / "result.json",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ambiguous overwrite", result.stderr)

    def test_malformed_partial_canonical_and_observation_incoherence_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            build_time = "2026-08-18T12:00:00Z"
            write_product(candidate, build_time)
            (candidate / MANIFEST_PATH).write_text("{bad", encoding="utf-8")
            self.assertNotEqual(self.validate(candidate).returncode, 0)

            write_product(candidate, build_time)
            (candidate / MANIFEST_PATH).unlink()
            self.assertNotEqual(self.validate(candidate).returncode, 0)

            write_product(candidate, build_time)
            manifest = json.loads((candidate / MANIFEST_PATH).read_text())
            manifest["newest_observation_utc"] = "2026-08-18T10:00:00Z"
            write_json(candidate / MANIFEST_PATH, manifest)
            self.assertNotEqual(self.validate(candidate).returncode, 0)

            write_product(candidate, build_time)
            write_product(worktree, "2026-08-18T11:00:00Z")
            write_json(worktree / SUMMARY_PATH, {"malformed": True})
            malformed = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, build_time),
                result=root / "result.json",
            )
            self.assertNotEqual(malformed.returncode, 0)

            write_product(worktree, "2026-08-18T11:00:00Z")
            (worktree / SUMMARY_PATH).unlink()
            partial = self.run_callback(
                phase="reconcile",
                candidate=candidate,
                worktree=worktree,
                metadata=self.metadata(root, build_time),
                result=root / "result.json",
            )
            self.assertNotEqual(partial.returncode, 0)
            self.assertIn("partial", partial.stderr)

    def test_exact_candidate_and_staged_allowlists_reject_unexpected_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            build_time = "2026-08-18T12:00:00Z"
            write_product(candidate, build_time)
            write_json(candidate / "docs/data/wind/unexpected.json", {"unexpected": True})
            command = [
                sys.executable,
                str(PUBLISHER),
                "prepare",
                "--candidate-root",
                str(candidate),
                "--output",
                str(root / "candidate-metadata.json"),
                "--product-id",
                "asos-awos-wind-latest",
                "--semantic-key-type",
                "build_time_utc",
                "--semantic-key",
                build_time,
                "--source-event-sha",
                "0" * 40,
            ]
            for path in PATHS:
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

            allowed = {path.as_posix() for path in PATHS}
            with self.assertRaises(main_publisher.PublisherError):
                main_publisher._require_allowed(
                    allowed | {"docs/data/wind/unexpected.json"}, allowed, "staging"
                )

    def test_scheduled_freshness_skip_produces_no_candidate(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        script = workflow.split("          python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
        script = textwrap.dedent(script)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / MANIFEST_PATH
            now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
            write_json(manifest, {"build_time_utc": stamp(now)})
            output = root / "output.txt"
            env = {
                **os.environ,
                "GITHUB_EVENT_NAME": "schedule",
                "GITHUB_OUTPUT": str(output),
            }
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("should_run=false", output.read_text())
        self.assertIn("should_publish: ${{ steps.freshness.outputs.should_run }}", workflow)
        self.assertIn("if: steps.freshness.outputs.should_run == 'true'", workflow)
        self.assertIn("needs.prepare-candidate.outputs.should_publish == 'true'", workflow)

    def test_workflow_watchdog_and_bounded_retry_invariants(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        watchdog = WATCHDOG.read_text(encoding="utf-8")
        publisher = PUBLISHER.read_text(encoding="utf-8")
        self.assertIn('cron: "17,42 * * * *"', workflow)
        self.assertIn("age_minutes >= 45.0", workflow)
        self.assertNotIn("live-data-feed-writes-", workflow)
        self.assertNotRegex(workflow, r"(?m)^concurrency:\s*$")
        self.assertIn("group: brim-live-main-publish", workflow)
        self.assertIn("scripts/main_publisher.py publish", workflow)
        self.assertIn('"workflow": "build-asos-awos-wind-latest.yml"', watchdog)
        self.assertIn('"cooldown": 20', watchdog)
        self.assertIn('"hourly_slots": [17, 42]', watchdog)
        self.assertIn('"docs/data/wind/asos_awos_wind_feed_manifest.json",\n                      75,', watchdog)
        self.assertIn("for attempt in (1, 2):", publisher)
        self.assertIn("retrying reconciliation exactly once", publisher)
        self.assertNotIn('"push", "--force"', publisher)


if __name__ == "__main__":
    unittest.main()
