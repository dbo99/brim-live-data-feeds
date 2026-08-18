#!/usr/bin/env python3
"""Validate and reconcile the fixed Delta Ops publication set."""

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


PRODUCT_ID = "delta-ops-daily-summary"
VALUES_PATH = Path("docs/data/delta_ops_daily_summary.json")
FEATURES_PATH = Path("docs/data/delta_ops_daily_summary_features.geojson")
SUMMARY_PATH = Path("docs/data/delta_ops_daily_summary_summary.json")
X2_PATH = Path("docs/data/delta_ops_x2_reference.geojson")
ALLOWLIST = (VALUES_PATH, FEATURES_PATH, SUMMARY_PATH, X2_PATH)


class ProductError(RuntimeError):
    """A malformed or ambiguous Delta Ops state."""


def _read_json(path: Path) -> Dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise ProductError(f"missing plain Delta Ops file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductError(f"unreadable JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProductError(f"JSON root must be an object: {path}")
    return value


def _date(value: object, label: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ProductError(f"{label} must be an ISO calendar date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ProductError(f"{label} is not a valid date: {value!r}") from exc


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        raise ProductError(f"{label} must be an RFC 3339 UTC timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ProductError(f"{label} is not a valid timestamp: {value!r}") from exc


def _point_geometry(feature: object, label: str) -> Dict[str, object]:
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise ProductError(f"{label} is not a GeoJSON Feature")
    geometry = feature.get("geometry")
    properties = feature.get("properties")
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        raise ProductError(f"{label} is not a Point")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) != 2:
        raise ProductError(f"{label} has invalid coordinates")
    longitude, latitude = coordinates
    if (
        isinstance(longitude, bool)
        or isinstance(latitude, bool)
        or not isinstance(longitude, (int, float))
        or not isinstance(latitude, (int, float))
        or not math.isfinite(longitude)
        or not math.isfinite(latitude)
        or not -180 <= longitude <= 180
        or not -90 <= latitude <= 90
    ):
        raise ProductError(f"{label} coordinates are not finite lon/lat")
    if not isinstance(properties, dict):
        raise ProductError(f"{label} properties must be an object")
    return properties


def _feature_collection(value: Dict[str, object], label: str) -> list[object]:
    if value.get("type") != "FeatureCollection":
        raise ProductError(f"{label} must be a GeoJSON FeatureCollection")
    features = value.get("features")
    if not isinstance(features, list):
        raise ProductError(f"{label} features must be an array")
    return features


def validate_product(root: Path) -> Tuple[str, int]:
    root = root.resolve()
    values = _read_json(root / VALUES_PATH)
    summary = _read_json(root / SUMMARY_PATH)
    feature_collection = _read_json(root / FEATURES_PATH)
    x2_collection = _read_json(root / X2_PATH)

    report_date = summary.get("report_date")
    _date(report_date, "Delta Ops report_date")
    build_time = summary.get("feed_build_time_utc")
    _timestamp(build_time, "Delta Ops feed_build_time_utc")
    for label, value in (("parsed values", values),):
        if value.get("report_date") != report_date:
            raise ProductError(f"Delta Ops {label} report date disagrees with summary")
        if value.get("feed_build_time_utc") != build_time:
            raise ProductError(f"Delta Ops {label} build time disagrees with summary")
        if not isinstance(value.get("source_name"), str) or not value["source_name"]:
            raise ProductError(f"Delta Ops {label} source name is missing")
        if not isinstance(value.get("source_url"), str) or not value["source_url"]:
            raise ProductError(f"Delta Ops {label} source URL is missing")
    if not isinstance(values.get("values"), dict) or not values["values"]:
        raise ProductError("Delta Ops parsed values object is empty")

    features = _feature_collection(feature_collection, "Delta Ops operational GeoJSON")
    if len(features) < 10:
        raise ProductError("Delta Ops operational GeoJSON has fewer than 10 features")
    if summary.get("feature_count") != len(features):
        raise ProductError("Delta Ops summary feature_count does not match GeoJSON")
    seen_keys = set()
    for index, feature in enumerate(features):
        properties = _point_geometry(feature, f"Delta Ops feature {index}")
        for key in (
            "feature_key",
            "display_name",
            "feature_type",
            "metric_name",
            "units",
            "label_text",
            "source_name",
            "source_url",
            "preliminary_notice",
        ):
            if not isinstance(properties.get(key), str) or not properties[key]:
                raise ProductError(f"Delta Ops feature {index} is missing string {key}")
        feature_key = properties["feature_key"]
        if feature_key in seen_keys:
            raise ProductError(f"Delta Ops feature {index} duplicates feature_key")
        seen_keys.add(feature_key)
        if properties.get("report_date") != report_date:
            raise ProductError(f"Delta Ops feature {index} report date disagrees")
        if properties.get("feed_build_time_utc") != build_time:
            raise ProductError(f"Delta Ops feature {index} build time disagrees")
        value_raw = properties.get("value_raw")
        if not isinstance(value_raw, str) or not value_raw:
            raise ProductError(f"Delta Ops feature {index} has invalid value_raw")
        value_numeric = properties.get("value_numeric")
        if value_numeric is not None and (
            isinstance(value_numeric, bool)
            or not isinstance(value_numeric, (int, float))
            or not math.isfinite(value_numeric)
        ):
            raise ProductError(f"Delta Ops feature {index} has invalid value_numeric")

    x2_features = _feature_collection(x2_collection, "Delta Ops X2 GeoJSON")
    x2_lookup_added = summary.get("x2_lookup_added")
    if not isinstance(x2_lookup_added, bool):
        raise ProductError("Delta Ops x2_lookup_added must be boolean")
    if x2_lookup_added and not x2_features:
        raise ProductError("Delta Ops summary declares X2 lookup without X2 features")
    for index, feature in enumerate(x2_features):
        properties = _point_geometry(feature, f"Delta Ops X2 feature {index}")
        for key in ("feature_key", "display_name", "feature_type", "source_name", "source_url"):
            if not isinstance(properties.get(key), str) or not properties[key]:
                raise ProductError(f"Delta Ops X2 feature {index} is missing string {key}")
        river_km = properties.get("river_km")
        if isinstance(river_km, bool) or not isinstance(river_km, (int, float)) or not math.isfinite(river_km):
            raise ProductError(f"Delta Ops X2 feature {index} has invalid river_km")

    return str(report_date), len(features)


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
        raise ProductError("publisher product ID does not select the Delta Ops callback")

    candidate_date_text, _ = validate_product(candidate_root)
    semantic_key = metadata.get("semantic_key")
    if not isinstance(semantic_key, dict):
        raise ProductError("Delta Ops candidate metadata semantic key is missing")
    if semantic_key.get("type") != "report_date":
        raise ProductError("Delta Ops semantic key type must be report_date")
    if semantic_key.get("value") != candidate_date_text:
        raise ProductError("Delta Ops report date does not match artifact metadata")
    candidate_date = _date(candidate_date_text, "candidate Delta Ops report_date")

    canonical_presence = [(worktree / path).is_file() for path in ALLOWLIST]
    if not any(canonical_presence):
        state = "new"
    elif not all(canonical_presence):
        raise ProductError("current main contains a partial Delta Ops product set")
    else:
        canonical_date_text, _ = validate_product(worktree)
        canonical_date = _date(canonical_date_text, "canonical Delta Ops report_date")
        if candidate_date < canonical_date:
            _write_result(
                result_path,
                decision="no-op",
                state="stale",
                reason="candidate source report date is older than current main",
            )
            return
        if candidate_date == canonical_date:
            _write_result(
                result_path,
                decision="no-op",
                state="same",
                reason="candidate source report date already exists on current main",
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
        reason="candidate source report date is newer than current main",
    )


def callback_main() -> int:
    phase = os.environ.get("BRIM_PUBLISH_PHASE")
    if phase == "validate-candidate":
        report_date, count = validate_product(Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"]))
        print(f"Validated Delta Ops candidate: {count} features for {report_date}")
    elif phase == "reconcile":
        reconcile()
    elif phase == "validate-staged":
        report_date, count = validate_product(Path(os.environ["BRIM_PUBLISH_WORKTREE"]))
        print(f"Validated staged Delta Ops product: {count} features for {report_date}")
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
            report_date, count = validate_product(Path(args.root))
            print(f"Validated Delta Ops product: {count} features for {report_date}")
            return 0
        if args.command is None:
            return callback_main()
        raise ProductError(f"unsupported command: {args.command}")
    except (KeyError, ProductError) as exc:
        print(f"DELTA_OPS_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
