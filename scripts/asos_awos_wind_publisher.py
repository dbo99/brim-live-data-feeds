#!/usr/bin/env python3
"""Validate and reconcile the fixed ASOS/AWOS observed-wind product set."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Sequence


PRODUCT_ID = "asos-awos-wind-latest"
DATA_PRODUCT_ID = "asos_awos_wind_obs"
SOURCE = "NOAA/NWS Aviation Weather Center METAR cache"
GEOJSON_PATH = Path("docs/data/wind/asos_awos_wind_latest.geojson")
SUMMARY_PATH = Path("docs/data/wind/asos_awos_wind_latest_summary.json")
MANIFEST_PATH = Path("docs/data/wind/asos_awos_wind_feed_manifest.json")
ALLOWLIST = (GEOJSON_PATH, SUMMARY_PATH, MANIFEST_PATH)
PUBLIC_FILES = {
    "geojson": GEOJSON_PATH.as_posix(),
    "summary_json": SUMMARY_PATH.as_posix(),
    "manifest_json": MANIFEST_PATH.as_posix(),
}
DOMAIN = {"west": -125.5, "east": -112.0, "south": 31.0, "north": 43.5}
MIN_FEATURES = 25
MAX_AGE_HOURS = 3.0
STALE_AFTER_HOURS = 2.0


class ProductError(RuntimeError):
    """A malformed or ambiguous ASOS/AWOS product state."""


@dataclass(frozen=True)
class ProductState:
    build_time_text: str
    build_time: datetime
    newest_observation: datetime
    feature_count: int


def _read_json(path: Path) -> Dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise ProductError(f"missing plain ASOS/AWOS file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductError(f"unreadable JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProductError(f"JSON root must be an object: {path}")
    return value


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        raise ProductError(f"{label} must be an RFC 3339 UTC timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ProductError(f"{label} is not a valid timestamp: {value!r}") from exc


def _number(
    value: object,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ProductError(f"{label} must be a finite number")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ProductError(f"{label} is below {minimum}")
    if maximum is not None and result > maximum:
        raise ProductError(f"{label} is above {maximum}")
    return result


def _count(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProductError(f"{label} must be a nonnegative integer")
    return value


def _close(value: object, expected: float, label: str, tolerance: float) -> None:
    if abs(_number(value, label) - expected) > tolerance:
        raise ProductError(f"{label} is incoherent with the ASOS/AWOS product")


def _identity(value: Dict[str, object], label: str) -> None:
    if value.get("product_id") != DATA_PRODUCT_ID:
        raise ProductError(f"{label} product_id is invalid")
    if value.get("source") != SOURCE:
        raise ProductError(f"{label} source is invalid")


def _validate_feature(
    feature: object,
    index: int,
    build_time: datetime,
) -> tuple[str, datetime, bool, float]:
    label = f"ASOS/AWOS feature {index}"
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise ProductError(f"{label} is not a GeoJSON Feature")
    geometry = feature.get("geometry")
    properties = feature.get("properties")
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        raise ProductError(f"{label} geometry is not a Point")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) != 2:
        raise ProductError(f"{label} coordinates are invalid")
    longitude = _number(
        coordinates[0], f"{label} longitude", minimum=DOMAIN["west"], maximum=DOMAIN["east"]
    )
    latitude = _number(
        coordinates[1], f"{label} latitude", minimum=DOMAIN["south"], maximum=DOMAIN["north"]
    )
    if not math.isfinite(longitude + latitude):
        raise ProductError(f"{label} coordinates are invalid")
    if not isinstance(properties, dict):
        raise ProductError(f"{label} properties must be an object")
    _identity(properties, label)

    station_id = properties.get("station_id")
    if not isinstance(station_id, str) or not station_id.strip():
        raise ProductError(f"{label} station_id is missing")
    observation = _timestamp(properties.get("observation_time_utc"), f"{label} observation time")
    computed_age = (build_time - observation).total_seconds() / 3600.0
    if computed_age < -0.25 or computed_age > MAX_AGE_HOURS + 0.05:
        raise ProductError(f"{label} observation is outside the producer age window")
    age_hours = _number(properties.get("age_hours"), f"{label} age_hours")
    age_minutes = _number(properties.get("age_minutes"), f"{label} age_minutes")
    if abs(age_hours - computed_age) > 0.03 or abs(age_minutes - computed_age * 60.0) > 2.0:
        raise ProductError(f"{label} observation age is incoherent with build time")

    speed_kt = _number(properties.get("wind_speed_kt"), f"{label} wind_speed_kt", minimum=0)
    _close(properties.get("wind_barb_speed_kt"), speed_kt, f"{label} wind_barb_speed_kt", 0.001)
    _close(properties.get("wind_speed_mph"), speed_kt * 1.150779448, f"{label} wind_speed_mph", 0.16)
    _close(properties.get("wind_speed_ms"), speed_kt * 0.514444, f"{label} wind_speed_ms", 0.04)
    calm = properties.get("calm")
    if not isinstance(calm, bool) or (calm and speed_kt != 0):
        raise ProductError(f"{label} calm flag disagrees with sustained wind")

    direction = properties.get("wind_dir_degrees")
    wind_from = properties.get("wind_from_degrees")
    wind_to = properties.get("wind_to_degrees")
    if direction is not None or wind_from is not None or wind_to is not None:
        direction_value = _number(direction, f"{label} wind_dir_degrees", minimum=0, maximum=360)
        from_value = _number(wind_from, f"{label} wind_from_degrees", minimum=0, maximum=360)
        to_value = _number(wind_to, f"{label} wind_to_degrees", minimum=0, maximum=360)
        if abs(direction_value - from_value) > 0.001:
            raise ProductError(f"{label} wind-from direction disagrees")
        expected_to = (from_value + 180.0) % 360.0
        if min(abs(to_value - expected_to), abs(to_value - expected_to + 360), abs(to_value - expected_to - 360)) > 0.001:
            raise ProductError(f"{label} wind-to direction disagrees")

    has_gust = properties.get("has_gust")
    if not isinstance(has_gust, bool):
        raise ProductError(f"{label} has_gust must be boolean")
    gust_values = [
        properties.get("wind_gust_kt"),
        properties.get("wind_gust_mph"),
        properties.get("wind_gust_ms"),
    ]
    if has_gust:
        gust_kt = _number(gust_values[0], f"{label} wind_gust_kt", minimum=0)
        _close(gust_values[1], gust_kt * 1.150779448, f"{label} wind_gust_mph", 0.16)
        _close(gust_values[2], gust_kt * 0.514444, f"{label} wind_gust_ms", 0.04)
    elif any(value is not None for value in gust_values):
        raise ProductError(f"{label} no-gust state contains gust values")
    return station_id, observation, has_gust, age_hours


def validate_product(root: Path) -> ProductState:
    root = root.resolve()
    geojson_path = root / GEOJSON_PATH
    summary_path = root / SUMMARY_PATH
    manifest_path = root / MANIFEST_PATH
    geojson = _read_json(geojson_path)
    summary = _read_json(summary_path)
    manifest = _read_json(manifest_path)

    _identity(geojson, "ASOS/AWOS GeoJSON")
    _identity(summary, "ASOS/AWOS summary")
    _identity(manifest, "ASOS/AWOS manifest")
    if geojson.get("type") != "FeatureCollection" or geojson.get("name") != "asos_awos_wind_latest":
        raise ProductError("ASOS/AWOS GeoJSON identity is invalid")
    bbox = geojson.get("bbox")
    expected_bbox = [DOMAIN["west"], DOMAIN["south"], DOMAIN["east"], DOMAIN["north"]]
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ProductError("ASOS/AWOS GeoJSON bbox is invalid")
    for index, expected in enumerate(expected_bbox):
        _close(bbox[index], expected, f"ASOS/AWOS bbox {index}", 0.000001)
    if summary.get("status") != "success":
        raise ProductError("ASOS/AWOS summary status must be success")
    if summary.get("output_files") != PUBLIC_FILES or manifest.get("public_files") != PUBLIC_FILES:
        raise ProductError("ASOS/AWOS public file inventory is invalid")

    build_time_text = summary.get("build_time_utc")
    build_time = _timestamp(build_time_text, "ASOS/AWOS build_time_utc")
    if manifest.get("build_time_utc") != build_time_text or geojson.get("generated_utc") != build_time_text:
        raise ProductError("ASOS/AWOS build times disagree across product files")
    generated = _timestamp(manifest.get("generated_utc"), "ASOS/AWOS manifest generated_utc")
    if generated < build_time or generated > build_time + timedelta(minutes=10):
        raise ProductError("ASOS/AWOS manifest generation time is incoherent")

    features = geojson.get("features")
    if not isinstance(features, list) or len(features) < MIN_FEATURES:
        raise ProductError(f"ASOS/AWOS GeoJSON must contain at least {MIN_FEATURES} features")
    stations: set[str] = set()
    observations: list[datetime] = []
    ages: list[float] = []
    gust_count = 0
    for index, feature in enumerate(features):
        station, observation, has_gust, age = _validate_feature(feature, index, build_time)
        stations.add(station)
        observations.append(observation)
        ages.append(age)
        gust_count += int(has_gust)

    oldest = min(observations)
    newest = max(observations)
    oldest_text = summary.get("oldest_observation_utc")
    newest_text = summary.get("newest_observation_utc")
    if _timestamp(oldest_text, "ASOS/AWOS oldest observation") != oldest:
        raise ProductError("ASOS/AWOS oldest observation disagrees with features")
    if _timestamp(newest_text, "ASOS/AWOS newest observation") != newest:
        raise ProductError("ASOS/AWOS newest observation disagrees with features")
    if manifest.get("newest_observation_utc") != newest_text:
        raise ProductError("ASOS/AWOS manifest newest observation disagrees")

    feature_count = len(features)
    expected_counts = {
        "feature_count": feature_count,
        "station_count": len(stations),
        "gust_feature_count": gust_count,
    }
    for key, expected in expected_counts.items():
        if _count(summary.get(key), f"ASOS/AWOS summary {key}") != expected:
            raise ProductError(f"ASOS/AWOS summary {key} disagrees with features")
        if _count(manifest.get(key), f"ASOS/AWOS manifest {key}") != expected:
            raise ProductError(f"ASOS/AWOS manifest {key} disagrees with features")
    gust_percent = round(100.0 * gust_count / feature_count, 1)
    _close(summary.get("gust_feature_percent"), gust_percent, "ASOS/AWOS summary gust percent", 0.05)
    _close(manifest.get("gust_feature_percent"), gust_percent, "ASOS/AWOS manifest gust percent", 0.05)

    newest_age = (build_time - newest).total_seconds() / 3600.0
    _close(summary.get("newest_observation_age_hours"), newest_age, "ASOS/AWOS summary newest age", 0.03)
    _close(manifest.get("newest_observation_age_hours"), newest_age, "ASOS/AWOS manifest newest age", 0.03)
    _close(summary.get("newest_observation_age_minutes"), newest_age * 60, "ASOS/AWOS summary newest age minutes", 2.0)
    _close(manifest.get("newest_observation_age_minutes"), newest_age * 60, "ASOS/AWOS manifest newest age minutes", 2.0)
    _close(summary.get("observation_age_hours", {}).get("max") if isinstance(summary.get("observation_age_hours"), dict) else None, max(ages), "ASOS/AWOS maximum age", 0.002)
    _close(summary.get("max_age_filter_hours"), MAX_AGE_HOURS, "ASOS/AWOS max age filter", 0.001)
    _close(summary.get("stale_after_hours"), STALE_AFTER_HOURS, "ASOS/AWOS summary stale threshold", 0.001)
    _close(manifest.get("stale_after_hours"), STALE_AFTER_HOURS, "ASOS/AWOS manifest stale threshold", 0.001)
    expected_stale = newest_age > STALE_AFTER_HOURS
    if summary.get("is_stale") is not expected_stale or manifest.get("is_stale") is not expected_stale:
        raise ProductError("ASOS/AWOS stale flags disagree with observation age")

    domain = summary.get("domain")
    if not isinstance(domain, dict):
        raise ProductError("ASOS/AWOS summary domain is invalid")
    for key, expected in DOMAIN.items():
        _close(domain.get(key), expected, f"ASOS/AWOS domain {key}", 0.000001)
    if summary.get("domain_id") != "hydrologic_ca_adjacent" or manifest.get("domain_id") != "hydrologic_ca_adjacent":
        raise ProductError("ASOS/AWOS domain identity is invalid")

    if _count(summary.get("output_geojson_bytes"), "ASOS/AWOS GeoJSON byte count") != geojson_path.stat().st_size:
        raise ProductError("ASOS/AWOS summary GeoJSON byte count disagrees")
    file_bytes = manifest.get("file_bytes")
    if not isinstance(file_bytes, dict):
        raise ProductError("ASOS/AWOS manifest file_bytes is invalid")
    if _count(file_bytes.get("geojson"), "ASOS/AWOS manifest GeoJSON bytes") != geojson_path.stat().st_size:
        raise ProductError("ASOS/AWOS manifest GeoJSON byte count disagrees")
    if _count(file_bytes.get("summary_json"), "ASOS/AWOS manifest summary bytes") != summary_path.stat().st_size:
        raise ProductError("ASOS/AWOS manifest summary byte count disagrees")

    return ProductState(str(build_time_text), build_time, newest, feature_count)


def _write_result(path: Path, *, decision: str, state: str, reason: str) -> None:
    path.write_text(
        json.dumps(
            {"decision": decision, "candidate_state": state, "reason": reason},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def reconcile() -> None:
    candidate_root = Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"])
    worktree = Path(os.environ["BRIM_PUBLISH_WORKTREE"])
    metadata = _read_json(Path(os.environ["BRIM_PUBLISH_METADATA"]))
    result_path = Path(os.environ["BRIM_PUBLISH_RESULT"])
    if os.environ.get("BRIM_PUBLISH_PRODUCT_ID") != PRODUCT_ID:
        raise ProductError("publisher product ID does not select the ASOS/AWOS callback")

    candidate = validate_product(candidate_root)
    semantic_key = metadata.get("semantic_key")
    if not isinstance(semantic_key, dict):
        raise ProductError("ASOS/AWOS candidate metadata semantic key is missing")
    if semantic_key.get("type") != "build_time_utc":
        raise ProductError("ASOS/AWOS semantic key type must be build_time_utc")
    if semantic_key.get("value") != candidate.build_time_text:
        raise ProductError("ASOS/AWOS build time does not match artifact metadata")

    canonical_presence = [(worktree / path).is_file() for path in ALLOWLIST]
    if not any(canonical_presence):
        state = "new"
        reason = "candidate is the first complete ASOS/AWOS product set"
    elif not all(canonical_presence):
        raise ProductError("current main contains a partial ASOS/AWOS product set")
    else:
        canonical = validate_product(worktree)
        if candidate.build_time < canonical.build_time:
            _write_result(
                result_path,
                decision="no-op",
                state="stale",
                reason="candidate build time is older than current main",
            )
            return
        if candidate.build_time == canonical.build_time:
            identical = all(
                (candidate_root / path).read_bytes() == (worktree / path).read_bytes()
                for path in ALLOWLIST
            )
            if not identical:
                raise ProductError(
                    "same ASOS/AWOS build time has different bytes; refusing ambiguous overwrite"
                )
            _write_result(
                result_path,
                decision="no-op",
                state="same",
                reason="candidate bytes already exist on current main",
            )
            return
        if candidate.newest_observation < canonical.newest_observation:
            _write_result(
                result_path,
                decision="no-op",
                state="stale",
                reason="candidate observations regress behind current main",
            )
            return
        state = "new"
        reason = "candidate build is newer with non-regressing valid observations"

    for relative_path in ALLOWLIST:
        source = candidate_root / relative_path
        destination = worktree / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    _write_result(result_path, decision="publish", state=state, reason=reason)


def callback_main() -> int:
    phase = os.environ.get("BRIM_PUBLISH_PHASE")
    if phase == "validate-candidate":
        state = validate_product(Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"]))
        print(f"Validated ASOS/AWOS candidate: {state.feature_count} features at {state.build_time_text}")
    elif phase == "reconcile":
        reconcile()
    elif phase == "validate-staged":
        state = validate_product(Path(os.environ["BRIM_PUBLISH_WORKTREE"]))
        print(f"Validated staged ASOS/AWOS product: {state.feature_count} features at {state.build_time_text}")
    else:
        raise ProductError(f"unsupported publisher callback phase: {phase!r}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            state = validate_product(Path(args.root))
            print(f"Validated ASOS/AWOS product: {state.feature_count} features at {state.build_time_text}")
            return 0
        if args.command is None:
            return callback_main()
        raise ProductError(f"unsupported command: {args.command}")
    except (KeyError, ProductError) as exc:
        print(f"ASOS_AWOS_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
