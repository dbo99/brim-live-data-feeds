#!/usr/bin/env python3
"""Offline Snow Pillow provider reconciliation and workflow-boundary tests."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
import main_publisher  # noqa: E402
import snow_pillow_publisher as snow  # noqa: E402


CALLBACK = SCRIPTS / "snow_pillow_publisher.py"
PROVIDERS = snow.PROVIDERS


def write_json(path: Path, value: object, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":") if compact else None,
            indent=None if compact else 2,
        ),
        encoding="utf-8",
    )


def provider_state(
    observation_date: str,
    build_time_local: str,
    value: float,
) -> dict[str, object]:
    return {
        "observation_date": observation_date,
        "build_time_local": build_time_local,
        "value": value,
    }


def write_product(
    root: Path,
    states: dict[str, dict[str, object]],
    *,
    actions: dict[str, str] | None = None,
) -> None:
    actions = actions or {provider: "refreshed" for provider in PROVIDERS}
    features = []
    trace_rows = []
    provider_labels = {
        "cdec_snow_sensor": "CDEC",
        "nrcs_snotel": "NRCS",
    }
    station_ids = {
        "cdec_snow_sensor": "CDEC_1",
        "nrcs_snotel": "NRCS_1",
    }
    source_classes = {
        "cdec_snow_sensor": "cdec_sno_adj_82",
        "nrcs_snotel": "nrcs_wteq",
    }
    source_labels = {
        "cdec_snow_sensor": "CDEC SNO ADJ (#82)",
        "nrcs_snotel": "NRCS WTEQ",
    }
    source_elements = {
        "cdec_snow_sensor": "CDEC_SENSOR_82_SNO_ADJ",
        "nrcs_snotel": "WTEQ",
    }
    for index, provider in enumerate(PROVIDERS):
        state = states[provider]
        station_uid = station_ids[provider]
        observation = str(state["observation_date"])
        value = float(state["value"])
        properties = {
            "station_uid": station_uid,
            "live_provider_key": provider,
            "provider": provider_labels[provider],
            "provider_station_id": "1",
            "station_name": f"Station {station_uid}",
            "current_water_year": 2026,
            "latitude": 39.0 + index,
            "longitude": -121.0 + index,
            "latest_swe_in": value,
            "latest_swe_date_local": observation,
            "latest_swe_report_status": "reported_positive",
            "latest_swe_source_class": source_classes[provider],
            "latest_swe_source_element": source_elements[provider],
            "latest_swe_source_label": source_labels[provider],
            "latest_swe_source_note": "fixture",
            "latest_snow_depth_in": value if provider == "nrcs_snotel" else None,
            "latest_snow_depth_date_local": observation if provider == "nrcs_snotel" else None,
            "feed_build_date_local": str(state["build_time_local"])[:10],
            "feed_build_time_local": state["build_time_local"],
            "fetch_start_date": "2025-10-01",
            "fetch_end_date": observation,
            "swe_delta_1day_in": 0,
            "swe_delta_3day_in": 0,
            "swe_delta_7day_in": 0,
            "swe_delta_suppressed_stale_latest": False,
            "normal_fixed_pct_median_swe": None,
            "normal_rolling_pct_median_swe": None,
        }
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [properties["longitude"], properties["latitude"]],
                },
                "properties": properties,
            }
        )
        day = (date.fromisoformat(observation) - date(2025, 10, 1)).days + 1
        trace_rows.append(
            {
                "station_uid": station_uid,
                "live_provider_key": provider,
                "provider": provider_labels[provider],
                "provider_station_id": "1",
                "station_name": f"Station {station_uid}",
                "water_year": "2026",
                "water_day": str(day),
                "obs_date_local": observation,
                "swe_in": str(value).rstrip("0").rstrip(".") if value else "0",
                "source_element": source_elements[provider],
                "swe_source_class": source_classes[provider],
                "swe_source_label": source_labels[provider],
                "swe_source_note": "fixture",
            }
        )

    features.sort(
        key=lambda feature: (
            feature["properties"]["live_provider_key"],
            feature["properties"]["station_name"],
        )
    )
    trace_rows.sort(key=lambda row: (row["station_uid"], row["obs_date_local"]))
    write_json(
        root / snow.GEOJSON_PATH,
        {"type": "FeatureCollection", "features": features},
        compact=True,
    )
    trace_path = root / snow.TRACE_PATH
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=snow.TRACE_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(trace_rows)

    status_counts = [{"latest_swe_report_status": "reported_positive", "n": 2}]
    provider_status_counts = [
        {
            "live_provider_key": provider,
            "latest_swe_report_status": "reported_positive",
            "n": 1,
        }
        for provider in PROVIDERS
    ]
    cdec_latest = [
        {
            "swe_source_class": source_classes["cdec_snow_sensor"],
            "swe_source_label": source_labels["cdec_snow_sensor"],
            "stations": 1,
        }
    ]
    cdec_trace = [
        {
            "swe_source_class": source_classes["cdec_snow_sensor"],
            "swe_source_label": source_labels["cdec_snow_sensor"],
            "rows": 1,
        }
    ]
    refresh_providers = {}
    for provider in PROVIDERS:
        action = actions[provider]
        carried = action == "carried_forward"
        refresh_providers[provider] = {
            "fetch_status": "failed" if carried else "success",
            "qa_status": "failed" if carried else "passed",
            "publication_action": action,
            "fetch_started_at_utc": "2026-08-18T12:00:00Z",
            "fetch_completed_at_utc": "2026-08-18T12:01:00Z",
            "result_built_at_utc": "2026-08-18T12:02:00Z",
            "fresh_latest_rows": 0 if carried else 1,
            "fresh_trace_rows": 0 if carried else 1,
            "fresh_site_count": 0 if carried else 1,
            "carried_forward_latest_rows": 1 if carried else 0,
            "carried_forward_trace_rows": 1 if carried else 0,
            "carried_forward_site_count": 1 if carried else 0,
            "last_successful_data_preserved": carried,
            "failure_reason": "fixture provider unavailable" if carried else None,
        }
    carried_count = sum(action == "carried_forward" for action in actions.values())
    refresh = {
        "mode": "partial" if carried_count else "full",
        "build_time_utc": "2026-08-18T12:02:00Z",
        "last_known_good_provider_data_preserved": bool(carried_count),
        "providers": refresh_providers,
    }
    summary = {
        "layer": "Snow pillows / SWE latest",
        "build_time_local": "2026-08-18 05:02 AM PDT",
        "build_date_local": "2026-08-18",
        "local_time_zone": "America/Los_Angeles",
        "current_water_year": 2026,
        "fetch_start_date": "2025-10-01",
        "fetch_end_date": max(str(state["observation_date"]) for state in states.values()),
        "station_rows": 2,
        "latest_geojson_rows": 2,
        "current_wy_trace_rows": 2,
        "max_valid_swe_in": 250,
        "latest_rows_with_fixed_pct_median": 0,
        "latest_rows_with_rolling_pct_median": 0,
        "cdec_selected_latest_source_counts": cdec_latest,
        "cdec_trace_source_counts": cdec_trace,
        "rows_with_latest_swe": 2,
        "rows_without_latest_swe": 0,
        "rows_reported_zero": 0,
        "rows_reported_positive": 2,
        "rows_missing_recent_value": 0,
        "rows_outside_snow_season_no_recent_report": 0,
        "rows_stale_last_value": 0,
        "rows_with_1day_delta": 2,
        "rows_with_3day_delta": 2,
        "rows_with_7day_delta": 2,
        "rows_with_1day_delta_current_display": 2,
        "rows_with_3day_delta_current_display": 2,
        "rows_with_7day_delta_current_display": 2,
        "rows_suppressed_delta_stale_latest": 0,
        "refresh": refresh,
        "status_counts": status_counts,
        "provider_status_counts": provider_status_counts,
        "qa_guardrails": {
            "observed_current_wy_trace_rows": 2,
            "observed_latest_swe_rows": 2,
            "combined_output_qa": {
                "passed": True,
                "problems": [],
                "latest_rows": 2,
                "trace_rows": 2,
                "latest_sites": 2,
                "trace_sites": 2,
            },
        },
    }
    trace_summary = {
        "layer": "Snow pillows / SWE current water-year trace",
        "build_time_local": summary["build_time_local"],
        "build_date_local": summary["build_date_local"],
        "local_time_zone": summary["local_time_zone"],
        "current_water_year": 2026,
        "fetch_start_date": summary["fetch_start_date"],
        "fetch_end_date": summary["fetch_end_date"],
        "current_wy_trace_rows": 2,
        "stations_with_trace_rows": 2,
        "providers": list(PROVIDERS),
        "cdec_trace_source_counts": cdec_trace,
        "refresh": copy.deepcopy(refresh),
    }
    write_json(root / snow.SUMMARY_PATH, summary)
    write_json(root / snow.TRACE_SUMMARY_PATH, trace_summary)


def write_metadata(path: Path, candidate: Path) -> None:
    write_json(
        path,
        {
            "semantic_key": {
                "type": "snow_provider_state_v1",
                "value": snow.semantic_key(snow.validate_product(candidate)),
            }
        },
    )


class SnowPillowPublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old = {
            "cdec_snow_sensor": provider_state(
                "2025-10-02", "2026-08-18 01:00 AM PDT", 1
            ),
            "nrcs_snotel": provider_state(
                "2025-10-02", "2026-08-18 01:00 AM PDT", 2
            ),
        }
        self.new = {
            "cdec_snow_sensor": provider_state(
                "2025-10-04", "2026-08-18 03:00 AM PDT", 11
            ),
            "nrcs_snotel": provider_state(
                "2025-10-04", "2026-08-18 03:00 AM PDT", 12
            ),
        }

    def run_callback(
        self,
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
                "BRIM_PUBLISH_PRODUCT_ID": snow.PRODUCT_ID,
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

    def reconcile(
        self,
        candidate: Path,
        worktree: Path,
        metadata: Path,
        result: Path,
        *,
        expected: int = 0,
    ) -> dict[str, object] | None:
        completed = self.run_callback(
            "reconcile", candidate, worktree, metadata, result
        )
        self.assertEqual(completed.returncode, expected, completed.stderr)
        return json.loads(result.read_text()) if result.exists() else None

    def test_01_both_providers_fresh_is_new(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            write_product(candidate, self.new)
            write_product(worktree, self.old)
            metadata = root / "candidate-metadata.json"
            write_metadata(metadata, candidate)
            result = self.reconcile(candidate, worktree, metadata, root / "result.json")
            self.assertEqual(result["candidate_state"], "new")
            self.assertEqual(result["decision"], "publish")
            snow.validate_product(worktree)

    def test_02_equivalent_providers_are_same(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            write_product(candidate, self.new)
            write_product(worktree, self.new)
            metadata = root / "candidate-metadata.json"
            write_metadata(metadata, candidate)
            before = [(worktree / path).read_bytes() for path in snow.ALLOWLIST]
            result = self.reconcile(candidate, worktree, metadata, root / "result.json")
            self.assertEqual(result["candidate_state"], "same")
            self.assertEqual(before, [(worktree / path).read_bytes() for path in snow.ALLOWLIST])

    def test_03_globally_stale_candidate_is_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            write_product(candidate, self.old)
            write_product(worktree, self.new)
            metadata = root / "candidate-metadata.json"
            write_metadata(metadata, candidate)
            result = self.reconcile(candidate, worktree, metadata, root / "result.json")
            self.assertEqual(result["candidate_state"], "stale")
            self.assertEqual(result["decision"], "no-op")

    def _assert_one_failed_carries_fresh_main(self, failed: str) -> None:
        healthy = next(provider for provider in PROVIDERS if provider != failed)
        event_states = copy.deepcopy(self.old)
        event_states[healthy] = copy.deepcopy(self.new[healthy])
        fresh_main = copy.deepcopy(self.old)
        fresh_main[failed] = provider_state(
            "2025-10-03", "2026-08-18 02:00 AM PDT", 7
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            write_product(
                candidate,
                event_states,
                actions={
                    provider: "carried_forward" if provider == failed else "refreshed"
                    for provider in PROVIDERS
                },
            )
            write_product(worktree, fresh_main)
            canonical_before = snow.validate_product(worktree)
            candidate_before = snow.validate_product(candidate)
            metadata = root / "candidate-metadata.json"
            write_metadata(metadata, candidate)
            result = self.reconcile(candidate, worktree, metadata, root / "result.json")
            self.assertEqual(result["decision"], "publish")
            staged = snow.validate_product(worktree)
            self.assertEqual(
                staged.provider_states[failed]["stable_digest"],
                canonical_before.provider_states[failed]["stable_digest"],
            )
            self.assertNotEqual(
                staged.provider_states[failed]["stable_digest"],
                candidate_before.provider_states[failed]["stable_digest"],
            )
            self.assertEqual(staged.actions[failed], "carried_forward")

    def test_04_nrcs_fresh_cdec_failed_carries_fresh_main(self) -> None:
        self._assert_one_failed_carries_fresh_main("cdec_snow_sensor")

    def test_05_cdec_fresh_nrcs_failed_carries_fresh_main(self) -> None:
        self._assert_one_failed_carries_fresh_main("nrcs_snotel")

    def test_06_both_provider_failure_refuses_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "candidate"
            write_product(
                root,
                self.old,
                actions={provider: "carried_forward" for provider in PROVIDERS},
            )
            with self.assertRaisesRegex(snow.ProductError, "both-provider failure"):
                snow.validate_product(root)

    def test_07_successful_provider_older_than_canonical_does_not_regress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            candidate_states = copy.deepcopy(self.new)
            candidate_states["nrcs_snotel"] = copy.deepcopy(
                self.old["nrcs_snotel"]
            )
            canonical_states = copy.deepcopy(self.old)
            canonical_states["nrcs_snotel"] = copy.deepcopy(
                self.new["nrcs_snotel"]
            )
            write_product(candidate, candidate_states)
            write_product(worktree, canonical_states)
            before = snow.validate_product(worktree)
            metadata = root / "candidate-metadata.json"
            write_metadata(metadata, candidate)
            result = self.reconcile(candidate, worktree, metadata, root / "result.json")
            self.assertEqual(result["decision"], "publish")
            after = snow.validate_product(worktree)
            self.assertEqual(
                before.provider_states["nrcs_snotel"]["stable_digest"],
                after.provider_states["nrcs_snotel"]["stable_digest"],
            )
            self.assertEqual(after.actions["nrcs_snotel"], "carried_forward")
            self.assertEqual(
                after.provider_states["cdec_snow_sensor"]["stable_digest"],
                snow.validate_product(candidate).provider_states[
                    "cdec_snow_sensor"
                ]["stable_digest"],
            )

    def test_08_event_sha_carry_older_than_fresh_main_fresh_main_wins(self) -> None:
        self._assert_one_failed_carries_fresh_main("nrcs_snotel")

    def test_09_equal_semantic_key_with_different_digest_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            revised = copy.deepcopy(self.new)
            revised["cdec_snow_sensor"]["value"] = 99
            write_product(candidate, revised)
            write_product(worktree, self.new)
            metadata = root / "candidate-metadata.json"
            write_metadata(metadata, candidate)
            completed = self.run_callback(
                "reconcile", candidate, worktree, metadata, root / "result.json"
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("equal Snow Pillow provider semantic key", completed.stderr)

    def test_10_malformed_candidate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_product(root, self.new)
            geojson = json.loads((root / snow.GEOJSON_PATH).read_text())
            del geojson["features"][0]["properties"]["latest_swe_in"]
            write_json(root / snow.GEOJSON_PATH, geojson, compact=True)
            with self.assertRaises(snow.ProductError):
                snow.validate_product(root)

    def test_11_partial_candidate_metadata_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            write_product(candidate, self.new)
            write_product(worktree, self.old)
            metadata = root / "candidate-metadata.json"
            write_metadata(metadata, candidate)
            value = json.loads(metadata.read_text())
            payload = json.loads(value["semantic_key"]["value"])
            del payload["providers"]["nrcs_snotel"]
            value["semantic_key"]["value"] = json.dumps(
                payload, sort_keys=True, separators=(",", ":")
            )
            write_json(metadata, value)
            completed = self.run_callback(
                "validate-candidate", candidate, worktree, metadata, root / "result.json"
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("does not match artifact metadata", completed.stderr)

    def test_12_malformed_canonical_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            write_product(candidate, self.new)
            write_product(worktree, self.old)
            (worktree / snow.TRACE_PATH).write_text("bad,header\n", encoding="utf-8")
            metadata = root / "candidate-metadata.json"
            write_metadata(metadata, candidate)
            completed = self.run_callback(
                "reconcile", candidate, worktree, metadata, root / "result.json"
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("trace header is invalid", completed.stderr)

    def test_13_missing_canonical_carry_provider_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            write_product(
                candidate,
                self.new,
                actions={
                    "cdec_snow_sensor": "refreshed",
                    "nrcs_snotel": "carried_forward",
                },
            )
            worktree.mkdir()
            metadata = root / "candidate-metadata.json"
            write_metadata(metadata, candidate)
            completed = self.run_callback(
                "reconcile", candidate, worktree, metadata, root / "result.json"
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("lacks canonical carry-forward rows", completed.stderr)

    def test_14_unexpected_candidate_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            write_product(candidate, self.new)
            (candidate / "docs/data/unexpected.json").write_text("{}")
            args = argparse.Namespace(
                candidate_root=str(candidate),
                output=str(root / "candidate-metadata.json"),
                allowlist=[path.as_posix() for path in snow.ALLOWLIST],
                product_id=snow.PRODUCT_ID,
                source_event_sha="a" * 40,
                semantic_key_type="snow_provider_state_v1",
                semantic_key="fixture",
            )
            with self.assertRaisesRegex(main_publisher.PublisherError, "inventory"):
                main_publisher.prepare_metadata(args)

    def test_15_unexpected_staged_path_is_rejected(self) -> None:
        with self.assertRaisesRegex(main_publisher.PublisherError, "outside the allowlist"):
            main_publisher._require_allowed(
                {snow.GEOJSON_PATH.as_posix(), "docs/data/unexpected.json"},
                {snow.GEOJSON_PATH.as_posix()},
                "Snow Pillow fixture staging",
            )

    def test_16_fresh_main_advancement_is_reconciled_again(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, worktree = root / "candidate", root / "worktree"
            event = copy.deepcopy(self.old)
            event["cdec_snow_sensor"] = copy.deepcopy(self.new["cdec_snow_sensor"])
            actions = {
                "cdec_snow_sensor": "refreshed",
                "nrcs_snotel": "carried_forward",
            }
            write_product(candidate, event, actions=actions)
            metadata = root / "candidate-metadata.json"
            write_metadata(metadata, candidate)
            main_one = copy.deepcopy(self.old)
            main_one["nrcs_snotel"] = provider_state(
                "2025-10-03", "2026-08-18 02:00 AM PDT", 7
            )
            write_product(worktree, main_one)
            self.reconcile(candidate, worktree, metadata, root / "result-one.json")
            first_digest = snow.validate_product(worktree).provider_states["nrcs_snotel"]["stable_digest"]

            main_two = copy.deepcopy(self.old)
            main_two["nrcs_snotel"] = provider_state(
                "2025-10-05", "2026-08-18 02:30 AM PDT", 8
            )
            write_product(worktree, main_two)
            expected_digest = snow.validate_product(worktree).provider_states["nrcs_snotel"]["stable_digest"]
            self.reconcile(candidate, worktree, metadata, root / "result-two.json")
            final_digest = snow.validate_product(worktree).provider_states["nrcs_snotel"]["stable_digest"]
            self.assertNotEqual(first_digest, expected_digest)
            self.assertEqual(final_digest, expected_digest)

    def _publisher_args(self, root: Path) -> SimpleNamespace:
        repo = root / "repo"
        repo.mkdir()
        callback = repo / "callback.py"
        callback.write_text("pass\n")
        candidate = root / "artifact" / "candidate"
        candidate.mkdir(parents=True)
        metadata = candidate.parent / "candidate-metadata.json"
        metadata.write_text("{}")
        return SimpleNamespace(
            target_ref=main_publisher.MAIN_REF,
            product_id=snow.PRODUCT_ID,
            allowlist=[snow.GEOJSON_PATH.as_posix()],
            commit_subject="fixture",
            repo=str(repo),
            candidate_root=str(candidate),
            metadata=str(metadata),
            candidate_validator=str(callback),
            reconcile_callback=str(callback),
            staged_validator=str(callback),
        )

    def test_17_shared_publisher_retries_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = self._publisher_args(Path(temporary))
            def fake_run(command, *, cwd, **kwargs):
                output = str(Path(cwd).resolve()) if "--show-toplevel" in command else "a" * 40
                return SimpleNamespace(stdout=output + "\n")

            with mock.patch.object(main_publisher, "_verify_main_authorization", return_value="a" * 40), mock.patch.object(
                main_publisher, "_run", side_effect=fake_run
            ), mock.patch.object(main_publisher, "_ensure_clean_checkout"), mock.patch.object(
                main_publisher,
                "_publish_attempt",
                side_effect=["non-fast-forward", "published"],
            ) as attempt:
                self.assertEqual(main_publisher.publish(args), 0)
                self.assertEqual(attempt.call_count, 2)

    def test_18_second_unresolved_race_fails_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = self._publisher_args(Path(temporary))
            def fake_run(command, *, cwd, **kwargs):
                output = str(Path(cwd).resolve()) if "--show-toplevel" in command else "a" * 40
                return SimpleNamespace(stdout=output + "\n")

            with mock.patch.object(main_publisher, "_verify_main_authorization", return_value="a" * 40), mock.patch.object(
                main_publisher, "_run", side_effect=fake_run
            ), mock.patch.object(main_publisher, "_ensure_clean_checkout"), mock.patch.object(
                main_publisher,
                "_publish_attempt",
                return_value="non-fast-forward",
            ) as attempt:
                with self.assertRaisesRegex(main_publisher.PublisherError, "refusing further retries"):
                    main_publisher.publish(args)
                self.assertEqual(attempt.call_count, 2)


if __name__ == "__main__":
    unittest.main()
