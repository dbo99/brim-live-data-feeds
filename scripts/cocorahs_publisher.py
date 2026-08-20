#!/usr/bin/env python3
"""Validate and reconcile the fixed CoCoRaHS publication set."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Sequence, Tuple


PRODUCT_ID = "cocorahs-daily-precip"
CA_GEOJSON_PATH = Path("docs/data/cocorahs_daily_precip_ca_latest.geojson")
CA_SUMMARY_PATH = Path("docs/data/cocorahs_daily_precip_ca_latest_summary.json")
CONUS_GEOJSON_PATH = Path("docs/data/cocorahs_daily_precip_conus_latest.geojson")
CONUS_SUMMARY_PATH = Path("docs/data/cocorahs_daily_precip_conus_latest_summary.json")
ALLOWLIST = (
    CA_GEOJSON_PATH,
    CA_SUMMARY_PATH,
    CONUS_GEOJSON_PATH,
    CONUS_SUMMARY_PATH,
)


class ProductError(RuntimeError):
    """A malformed or ambiguous CoCoRaHS product state."""


def _read_json(path: Path) -> Dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise ProductError(f"missing plain CoCoRaHS file: {path}")
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


def _offset_timestamp(value: object, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise ProductError(f"{label} must be an offset-bearing timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProductError(f"{label} is not a valid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ProductError(f"{label} must include a UTC offset")


def _date(value: object, label: str) -> None:
    if not isinstance(value, str):
        raise ProductError(f"{label} must be an ISO date")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ProductError(f"{label} is not a valid date: {value!r}") from exc


def _finite(value: object, label: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ProductError(f"{label} must be a finite number")


def _optional_omission_count(summary: Dict[str, object], label: str) -> int | None:
    key = "omitted_missing_station_name"
    if key not in summary:
        return None
    value = summary[key]
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10:
        raise ProductError(f"{label} {key} must be an integer from 0 through 10")
    return value


def _validate_collection(
    value: Dict[str, object],
    summary: Dict[str, object],
    *,
    label: str,
    expected_name: str,
    expected_scope: str,
) -> Tuple[str, list[object]]:
    if value.get("type") != "FeatureCollection" or value.get("name") != expected_name:
        raise ProductError(f"{label} GeoJSON identity is invalid")
    if value.get("metadata") != summary:
        raise ProductError(f"{label} GeoJSON metadata does not match its summary")
    features = value.get("features")
    if not isinstance(features, list):
        raise ProductError(f"{label} features must be an array")
    if summary.get("scope") != expected_scope:
        raise ProductError(f"{label} summary scope is invalid")
    if summary.get("output_feature_count") != len(features):
        raise ProductError(f"{label} summary feature count does not match GeoJSON")
    build_time = summary.get("feed_build_time_utc")
    _timestamp(build_time, f"{label} feed_build_time_utc")
    _date(summary.get("start_date"), f"{label} start_date")
    _date(summary.get("end_date"), f"{label} end_date")
    if not isinstance(summary.get("units"), str) or not summary["units"]:
        raise ProductError(f"{label} summary units are missing")
    _optional_omission_count(summary, label)

    seen_stations = set()
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ProductError(f"{label} feature {index} is not a GeoJSON Feature")
        geometry = feature.get("geometry")
        properties = feature.get("properties")
        if not isinstance(geometry, dict) or geometry.get("type") != "Point":
            raise ProductError(f"{label} feature {index} is not a Point")
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) != 2:
            raise ProductError(f"{label} feature {index} has invalid coordinates")
        longitude, latitude = coordinates
        _finite(longitude, f"{label} feature {index} longitude")
        _finite(latitude, f"{label} feature {index} latitude")
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise ProductError(f"{label} feature {index} coordinates are outside lon/lat")
        if not isinstance(properties, dict):
            raise ProductError(f"{label} feature {index} properties must be an object")
        for key in (
            "stationNumber",
            "stationName",
            "obsDateTime",
            "feedBuildTimeUtc",
            "source",
            "stationUrl",
            "units",
            "sourceWindowStartDate",
            "sourceWindowEndDate",
        ):
            if (
                not isinstance(properties.get(key), str)
                or not properties[key]
                or (key == "stationName" and not properties[key].strip())
            ):
                raise ProductError(f"{label} feature {index} is missing string {key}")
        if properties["stationNumber"] in seen_stations:
            raise ProductError(f"{label} duplicates stationNumber {properties['stationNumber']}")
        seen_stations.add(properties["stationNumber"])
        if properties["feedBuildTimeUtc"] != build_time:
            raise ProductError(f"{label} feature {index} build time disagrees")
        _offset_timestamp(properties["obsDateTime"], f"{label} feature {index} obsDateTime")
        _date(properties["sourceWindowStartDate"], f"{label} feature {index} window start")
        _date(properties["sourceWindowEndDate"], f"{label} feature {index} window end")
        _finite(properties.get("longitude"), f"{label} feature {index} property longitude")
        _finite(properties.get("latitude"), f"{label} feature {index} property latitude")
        if properties["longitude"] != longitude or properties["latitude"] != latitude:
            raise ProductError(f"{label} feature {index} coordinate properties disagree")
        for key in ("precip", "gaugeCatch"):
            _finite(properties.get(key), f"{label} feature {index} {key}", nullable=True)
            if properties.get(key) is not None and properties[key] < 0:
                raise ProductError(f"{label} feature {index} {key} is negative")
        for key in ("precipIsTrace", "gaugeCatchIsTrace"):
            if not isinstance(properties.get(key), bool):
                raise ProductError(f"{label} feature {index} has invalid {key}")
    return str(build_time), features


def validate_product(root: Path) -> Tuple[str, int]:
    root = root.resolve()
    ca_geojson = _read_json(root / CA_GEOJSON_PATH)
    ca_summary = _read_json(root / CA_SUMMARY_PATH)
    conus_geojson = _read_json(root / CONUS_GEOJSON_PATH)
    conus_summary = _read_json(root / CONUS_SUMMARY_PATH)
    ca_time, ca_features = _validate_collection(
        ca_geojson,
        ca_summary,
        label="California CoCoRaHS",
        expected_name="cocorahs_daily_precip_ca_latest",
        expected_scope="California",
    )
    conus_time, conus_features = _validate_collection(
        conus_geojson,
        conus_summary,
        label="CONUS CoCoRaHS",
        expected_name="cocorahs_daily_precip_conus_latest",
        expected_scope="CONUS",
    )
    if ca_time != conus_time:
        raise ProductError("CoCoRaHS CA and CONUS build times disagree")
    if _optional_omission_count(ca_summary, "California CoCoRaHS") != (
        _optional_omission_count(conus_summary, "CONUS CoCoRaHS")
    ):
        raise ProductError("CoCoRaHS CA and CONUS omission counts disagree")
    conus_by_station = {
        feature["properties"]["stationNumber"]: feature for feature in conus_features
    }
    for feature in ca_features:
        station = feature["properties"]["stationNumber"]
        if conus_by_station.get(station) != feature:
            raise ProductError(f"California station {station} is not identical to CONUS")
    return ca_time, len(ca_features) + len(conus_features)


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
        raise ProductError("publisher product ID does not select the CoCoRaHS callback")

    candidate_time_text, _ = validate_product(candidate_root)
    semantic_key = metadata.get("semantic_key")
    if not isinstance(semantic_key, dict):
        raise ProductError("CoCoRaHS candidate metadata semantic key is missing")
    if semantic_key.get("type") != "feed_build_time_utc":
        raise ProductError("CoCoRaHS semantic key type must be feed_build_time_utc")
    if semantic_key.get("value") != candidate_time_text:
        raise ProductError("CoCoRaHS build time does not match artifact metadata")
    candidate_time = _timestamp(candidate_time_text, "candidate feed_build_time_utc")

    canonical_presence = [(worktree / path).is_file() for path in ALLOWLIST]
    if not any(canonical_presence):
        state = "new"
    elif not all(canonical_presence):
        raise ProductError("current main contains a partial CoCoRaHS product set")
    else:
        canonical_time_text, _ = validate_product(worktree)
        canonical_time = _timestamp(canonical_time_text, "canonical feed_build_time_utc")
        if candidate_time < canonical_time:
            _write_result(
                result_path,
                decision="no-op",
                state="stale",
                reason="candidate feed build time is older than current main",
            )
            return
        if candidate_time == canonical_time:
            identical = all(
                (candidate_root / path).read_bytes() == (worktree / path).read_bytes()
                for path in ALLOWLIST
            )
            if not identical:
                raise ProductError(
                    "same CoCoRaHS semantic time has different bytes; refusing ambiguous overwrite"
                )
            _write_result(
                result_path,
                decision="no-op",
                state="same",
                reason="candidate bytes already exist on current main",
            )
            return
        state = "new"

    for relative_path in ALLOWLIST:
        source = candidate_root / relative_path
        destination = worktree / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    _write_result(
        result_path,
        decision="publish",
        state=state,
        reason="candidate feed build time is newer than current main",
    )


def callback_main() -> int:
    phase = os.environ.get("BRIM_PUBLISH_PHASE")
    if phase == "validate-candidate":
        build_time, count = validate_product(Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"]))
        print(f"Validated CoCoRaHS candidate: {count} total features at {build_time}")
    elif phase == "reconcile":
        reconcile()
    elif phase == "validate-staged":
        build_time, count = validate_product(Path(os.environ["BRIM_PUBLISH_WORKTREE"]))
        print(f"Validated staged CoCoRaHS product: {count} total features at {build_time}")
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
            build_time, count = validate_product(Path(args.root))
            print(f"Validated CoCoRaHS product: {count} total features at {build_time}")
            return 0
        if args.command is None:
            return callback_main()
        raise ProductError(f"unsupported command: {args.command}")
    except (KeyError, ProductError) as exc:
        print(f"COCORAHS_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
