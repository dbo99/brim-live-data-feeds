#!/usr/bin/env python3
"""CDEC product validation and fresh-main reconciliation callback."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Sequence, Tuple


PRODUCT_ID = "cdec-reservoir-feed"
GEOJSON_PATH = Path("docs/data/cdec_reservoir_latest.geojson")
SUMMARY_PATH = Path("docs/data/cdec_reservoir_latest_summary.json")
ALLOWLIST = (GEOJSON_PATH, SUMMARY_PATH)


class ProductError(RuntimeError):
    """A CDEC candidate or reconciliation rejection."""


def _read_json(path: Path) -> Dict[str, object]:
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
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ProductError(f"{label} is not a valid timestamp: {value!r}") from exc
    if parsed.microsecond != 0:
        raise ProductError(f"{label} must have whole-second precision")
    return parsed


def validate_product(root: Path) -> Tuple[str, int]:
    root = root.resolve()
    geo_path = root / GEOJSON_PATH
    summary_path = root / SUMMARY_PATH
    if not geo_path.is_file() or geo_path.is_symlink():
        raise ProductError(f"missing plain CDEC GeoJSON: {geo_path}")
    if not summary_path.is_file() or summary_path.is_symlink():
        raise ProductError(f"missing plain CDEC summary: {summary_path}")

    geojson = _read_json(geo_path)
    summary = _read_json(summary_path)
    if geojson.get("type") != "FeatureCollection":
        raise ProductError("CDEC GeoJSON must be a FeatureCollection")
    features = geojson.get("features")
    if not isinstance(features, list) or not features:
        raise ProductError("CDEC GeoJSON must contain at least one feature")
    if geojson.get("feature_count") != len(features):
        raise ProductError("CDEC GeoJSON feature_count does not match features")
    if summary.get("output_feature_count") != len(features):
        raise ProductError("CDEC summary output_feature_count does not match GeoJSON")

    build_time = geojson.get("feed_build_time_utc")
    if summary.get("feed_build_time_utc") != build_time:
        raise ProductError("CDEC GeoJSON and summary build timestamps disagree")
    _timestamp(build_time, "feed_build_time_utc")

    latest_count = summary.get("output_features_with_latest_storage")
    minimum = summary.get("min_latest_rows_to_publish")
    degraded = summary.get("allow_degraded_publish")
    if not isinstance(latest_count, int) or latest_count < 0:
        raise ProductError("CDEC latest-storage count must be a nonnegative integer")
    if not isinstance(minimum, int) or minimum < 1:
        raise ProductError("CDEC minimum latest-storage count must be positive")
    if not isinstance(degraded, bool):
        raise ProductError("CDEC degraded-publication flag must be boolean")
    if latest_count < minimum and not degraded:
        raise ProductError("CDEC candidate is below its declared publication minimum")

    seen_ids = set()
    counted_latest = 0
    counted_storage = 0
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ProductError(f"CDEC feature {index} is not a GeoJSON Feature")
        geometry = feature.get("geometry")
        properties = feature.get("properties")
        if not isinstance(geometry, dict) or geometry.get("type") != "Point":
            raise ProductError(f"CDEC feature {index} is not a Point")
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) != 2:
            raise ProductError(f"CDEC feature {index} has invalid coordinates")
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
            raise ProductError(f"CDEC feature {index} coordinates are not finite lon/lat")
        if not isinstance(properties, dict):
            raise ProductError(f"CDEC feature {index} properties must be an object")
        for key in ("cdec_id", "reservoir_name", "display_storage_source", "source_url"):
            if not isinstance(properties.get(key), str) or not properties[key]:
                raise ProductError(f"CDEC feature {index} is missing string {key}")
        if properties["cdec_id"] in seen_ids:
            raise ProductError(f"CDEC feature {index} duplicates cdec_id")
        seen_ids.add(properties["cdec_id"])
        if properties.get("feed_build_time_utc") != build_time:
            raise ProductError(f"CDEC feature {index} build timestamp disagrees")
        if not isinstance(properties.get("has_storage_value"), bool):
            raise ProductError(f"CDEC feature {index} has invalid storage-value flag")
        if not isinstance(properties.get("has_latest_storage"), bool):
            raise ProductError(f"CDEC feature {index} has invalid latest-storage flag")
        counted_latest += int(properties["has_latest_storage"])
        counted_storage += int(properties["has_storage_value"])
        display_storage = properties.get("display_storage_af")
        if properties["has_storage_value"] and (
            isinstance(display_storage, bool)
            or not isinstance(display_storage, (int, float))
            or not math.isfinite(display_storage)
            or display_storage < 0
        ):
            raise ProductError(
                f"CDEC feature {index} marks a nonfinite/negative storage value usable"
            )
        for key in ("obs_stale_12h", "obs_stale_24h"):
            if not isinstance(properties.get(key), bool):
                raise ProductError(f"CDEC feature {index} has invalid {key}")

    if counted_latest != latest_count:
        raise ProductError("CDEC feature latest-storage flags do not match summary")
    if counted_storage < minimum and not degraded:
        raise ProductError("CDEC candidate lacks enough consumer-usable storage features")

    return str(build_time), len(features)


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
        raise ProductError("publisher product ID does not select the CDEC callback")

    candidate_time_text, _ = validate_product(candidate_root)
    semantic_key = metadata.get("semantic_key")
    if not isinstance(semantic_key, dict):
        raise ProductError("candidate metadata semantic key is missing")
    if semantic_key.get("type") != "feed_build_time_utc":
        raise ProductError("CDEC semantic key type must be feed_build_time_utc")
    if semantic_key.get("value") != candidate_time_text:
        raise ProductError("CDEC candidate timestamp does not match artifact metadata")
    candidate_time = _timestamp(candidate_time_text, "candidate feed_build_time_utc")

    canonical_presence = [(worktree / path).is_file() for path in ALLOWLIST]
    if not any(canonical_presence):
        state = "new"
    elif not all(canonical_presence):
        raise ProductError("current main contains a partial CDEC product set")
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
                    "same CDEC semantic time has different bytes; refusing ambiguous overwrite"
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
        print(f"Validated CDEC candidate: {count} features at {build_time}")
    elif phase == "reconcile":
        reconcile()
    elif phase == "validate-staged":
        build_time, count = validate_product(Path(os.environ["BRIM_PUBLISH_WORKTREE"]))
        print(f"Validated staged CDEC product: {count} features at {build_time}")
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
            print(f"Validated CDEC product: {count} features at {build_time}")
            return 0
        if args.command is None:
            return callback_main()
        raise ProductError(f"unsupported command: {args.command}")
    except (KeyError, ProductError) as exc:
        print(f"CDEC_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
