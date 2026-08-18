#!/usr/bin/env python3
"""Validate and reconcile the fixed USGS groundwater publication set."""

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


PRODUCT_ID = "usgs-groundwater-latest-ca"
GEOJSON_PATH = Path("docs/data/usgs_groundwater_latest_ca.geojson")
SUMMARY_PATH = Path("docs/data/usgs_groundwater_latest_ca_summary.json")
ALLOWLIST = (GEOJSON_PATH, SUMMARY_PATH)


class ProductError(RuntimeError):
    """A malformed or ambiguous USGS groundwater product state."""


def _read_json(path: Path) -> Dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise ProductError(f"missing plain USGS groundwater file: {path}")
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


def _date(value: object, label: str) -> None:
    if not isinstance(value, str):
        raise ProductError(f"{label} must be an ISO date")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ProductError(f"{label} is not a valid date: {value!r}") from exc


def _finite(value: object, label: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ProductError(f"{label} must be a finite number")


def _count(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProductError(f"{label} must be a nonnegative integer")
    return value


def validate_product(root: Path) -> Tuple[str, int]:
    root = root.resolve()
    geojson = _read_json(root / GEOJSON_PATH)
    summary = _read_json(root / SUMMARY_PATH)
    if geojson.get("type") != "FeatureCollection":
        raise ProductError("USGS groundwater GeoJSON must be a FeatureCollection")
    if geojson.get("name") != "USGS Groundwater Latest CA":
        raise ProductError("USGS groundwater GeoJSON name is invalid")
    features = geojson.get("features")
    if not isinstance(features, list) or not features:
        raise ProductError("USGS groundwater GeoJSON must contain features")
    metadata = geojson.get("metadata")
    if not isinstance(metadata, dict):
        raise ProductError("USGS groundwater metadata must be an object")
    build_time = summary.get("feed_build_time_utc")
    _timestamp(build_time, "USGS groundwater feed_build_time_utc")
    if metadata.get("feed_build_time_utc") != build_time:
        raise ProductError("USGS groundwater metadata and summary build times disagree")
    if summary.get("scope") != "CA" or metadata.get("scope") != "CA":
        raise ProductError("USGS groundwater scope must be CA")
    if summary.get("output_feature_count") != len(features):
        raise ProductError("USGS groundwater summary feature count does not match GeoJSON")
    station_count = _count(summary.get("station_index_rows"), "groundwater station_index_rows")
    if station_count < len(features):
        raise ProductError("USGS groundwater output exceeds its station index")
    api_count = _count(summary.get("api_latest_site_count"), "groundwater API count")
    fallback_count = _count(summary.get("index_fallback_count"), "groundwater fallback count")
    allow_fallback = summary.get("allow_index_fallback")
    if not isinstance(allow_fallback, bool):
        raise ProductError("USGS groundwater fallback policy must be boolean")
    if metadata.get("allow_index_fallback") != allow_fallback:
        raise ProductError("USGS groundwater metadata and summary fallback policy disagree")
    if not allow_fallback and fallback_count:
        raise ProductError("USGS groundwater reports fallback rows while fallback is disabled")
    if api_count + fallback_count != len(features):
        raise ProductError("USGS groundwater source counts do not match GeoJSON")

    seen_sites = set()
    counted_api = 0
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ProductError(f"USGS groundwater feature {index} is not a Feature")
        geometry = feature.get("geometry")
        properties = feature.get("properties")
        if not isinstance(geometry, dict) or geometry.get("type") != "Point":
            raise ProductError(f"USGS groundwater feature {index} is not a Point")
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) != 2:
            raise ProductError(f"USGS groundwater feature {index} has invalid coordinates")
        longitude, latitude = coordinates
        _finite(longitude, f"USGS groundwater feature {index} longitude")
        _finite(latitude, f"USGS groundwater feature {index} latitude")
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise ProductError(f"USGS groundwater feature {index} coordinates are outside lon/lat")
        if not isinstance(properties, dict):
            raise ProductError(f"USGS groundwater feature {index} properties must be an object")
        for key in (
            "site_no",
            "station_nm",
            "latest_wl_units",
            "latest_wl_source",
            "latest_wl_status",
            "latest_status",
        ):
            if not isinstance(properties.get(key), str) or not properties[key]:
                raise ProductError(f"USGS groundwater feature {index} is missing string {key}")
        site_no = properties["site_no"]
        if site_no in seen_sites:
            raise ProductError(f"USGS groundwater duplicates site_no {site_no}")
        seen_sites.add(site_no)
        if properties.get("feed_build_time_utc") != build_time:
            raise ProductError(f"USGS groundwater feature {index} build time disagrees")
        _finite(properties.get("latest_wl_ft_bgs"), f"USGS groundwater feature {index} water level")
        _finite(properties.get("latest_age_days"), f"USGS groundwater feature {index} age")
        _timestamp(
            properties.get("latest_wl_datetime_utc"),
            f"USGS groundwater feature {index} measurement time",
        )
        _date(properties.get("latest_wl_date"), f"USGS groundwater feature {index} measurement date")
        has_api = properties.get("has_api_latest_wl")
        if not isinstance(has_api, bool):
            raise ProductError(f"USGS groundwater feature {index} has invalid API-source flag")
        counted_api += int(has_api)
    if counted_api != api_count or len(features) - counted_api != fallback_count:
        raise ProductError("USGS groundwater feature source flags disagree with summary")
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
        raise ProductError("publisher product ID does not select the groundwater callback")

    candidate_time_text, _ = validate_product(candidate_root)
    semantic_key = metadata.get("semantic_key")
    if not isinstance(semantic_key, dict):
        raise ProductError("groundwater candidate metadata semantic key is missing")
    if semantic_key.get("type") != "feed_build_time_utc":
        raise ProductError("groundwater semantic key type must be feed_build_time_utc")
    if semantic_key.get("value") != candidate_time_text:
        raise ProductError("groundwater build time does not match artifact metadata")
    candidate_time = _timestamp(candidate_time_text, "candidate feed_build_time_utc")

    canonical_presence = [(worktree / path).is_file() for path in ALLOWLIST]
    if not any(canonical_presence):
        state = "new"
    elif not all(canonical_presence):
        raise ProductError("current main contains a partial USGS groundwater product set")
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
                    "same groundwater semantic time has different bytes; refusing ambiguous overwrite"
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
        print(f"Validated USGS groundwater candidate: {count} features at {build_time}")
    elif phase == "reconcile":
        reconcile()
    elif phase == "validate-staged":
        build_time, count = validate_product(Path(os.environ["BRIM_PUBLISH_WORKTREE"]))
        print(f"Validated staged USGS groundwater product: {count} features at {build_time}")
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
            print(f"Validated USGS groundwater product: {count} features at {build_time}")
            return 0
        if args.command is None:
            return callback_main()
        raise ProductError(f"unsupported command: {args.command}")
    except (KeyError, ProductError) as exc:
        print(f"USGS_GROUNDWATER_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
