#!/usr/bin/env python3
"""Validate and reconcile the fixed USGS streamflow publication set."""

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


PRODUCT_ID = "usgs-streamflow-latest-ca"
GEOJSON_PATH = Path("docs/data/usgs_streamflow_latest_ca.geojson")
SUMMARY_PATH = Path("docs/data/usgs_streamflow_latest_ca_summary.json")
ALLOWLIST = (GEOJSON_PATH, SUMMARY_PATH)


class ProductError(RuntimeError):
    """A malformed or ambiguous USGS streamflow product state."""


def _read_json(path: Path) -> Dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise ProductError(f"missing plain USGS streamflow file: {path}")
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


def _finite(value: object, label: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
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
        raise ProductError("USGS streamflow GeoJSON must be a FeatureCollection")
    if geojson.get("name") != "USGS Streamflow Latest CA":
        raise ProductError("USGS streamflow GeoJSON name is invalid")
    features = geojson.get("features")
    if not isinstance(features, list) or not features:
        raise ProductError("USGS streamflow GeoJSON must contain features")
    metadata = geojson.get("metadata")
    if not isinstance(metadata, dict):
        raise ProductError("USGS streamflow metadata must be an object")
    build_time = summary.get("feed_build_time_utc")
    _timestamp(build_time, "USGS streamflow feed_build_time_utc")
    if metadata.get("feed_build_time_utc") != build_time:
        raise ProductError("USGS streamflow metadata and summary build times disagree")
    if summary.get("scope") != "CA" or metadata.get("scope") != "CA":
        raise ProductError("USGS streamflow scope must be CA")
    if summary.get("output_feature_count") != len(features):
        raise ProductError("USGS streamflow summary feature count does not match GeoJSON")
    if summary.get("station_index_rows") != len(features):
        raise ProductError("USGS streamflow station-index count does not match GeoJSON")

    seen_sites = set()
    discharge_count = 0
    stage_count = 0
    stale_6h_count = 0
    stale_24h_count = 0
    stale_72h_count = 0
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ProductError(f"USGS streamflow feature {index} is not a Feature")
        geometry = feature.get("geometry")
        properties = feature.get("properties")
        if not isinstance(geometry, dict) or geometry.get("type") != "Point":
            raise ProductError(f"USGS streamflow feature {index} is not a Point")
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) != 2:
            raise ProductError(f"USGS streamflow feature {index} has invalid coordinates")
        longitude, latitude = coordinates
        _finite(longitude, f"USGS streamflow feature {index} longitude")
        _finite(latitude, f"USGS streamflow feature {index} latitude")
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise ProductError(f"USGS streamflow feature {index} coordinates are outside lon/lat")
        if not isinstance(properties, dict):
            raise ProductError(f"USGS streamflow feature {index} properties must be an object")
        for key in ("site_no", "station_nm", "latest_status", "feed_source"):
            if not isinstance(properties.get(key), str) or not properties[key]:
                raise ProductError(f"USGS streamflow feature {index} is missing string {key}")
        site_no = properties["site_no"]
        if site_no in seen_sites:
            raise ProductError(f"USGS streamflow duplicates site_no {site_no}")
        seen_sites.add(site_no)
        if properties.get("feed_build_time_utc") != build_time:
            raise ProductError(f"USGS streamflow feature {index} build time disagrees")

        has_q = properties.get("has_latest_iv_q")
        has_stage = properties.get("has_latest_iv_stage")
        if not isinstance(has_q, bool) or not isinstance(has_stage, bool):
            raise ProductError(f"USGS streamflow feature {index} has invalid live-value flags")
        q_cfs = properties.get("q_cfs")
        stage_ft = properties.get("stage_ft")
        _finite(q_cfs, f"USGS streamflow feature {index} q_cfs", nullable=True)
        _finite(stage_ft, f"USGS streamflow feature {index} stage_ft", nullable=True)
        if has_q != (q_cfs is not None) or has_stage != (stage_ft is not None):
            raise ProductError(f"USGS streamflow feature {index} live-value flags disagree")
        if has_q:
            _timestamp(properties.get("q_datetime_utc"), f"USGS streamflow feature {index} q time")
            _finite(properties.get("q_obs_age_hours"), f"USGS streamflow feature {index} q age")
        if has_stage:
            _timestamp(
                properties.get("stage_datetime_utc"),
                f"USGS streamflow feature {index} stage time",
            )
            _finite(properties.get("stage_obs_age_hours"), f"USGS streamflow feature {index} stage age")
        for key in ("q_stale_6h", "q_stale_24h", "q_stale_72h"):
            if not isinstance(properties.get(key), bool):
                raise ProductError(f"USGS streamflow feature {index} has invalid {key}")
        discharge_count += int(has_q)
        stage_count += int(has_stage)
        stale_6h_count += int(properties["q_stale_6h"])
        stale_24h_count += int(properties["q_stale_24h"])
        stale_72h_count += int(properties["q_stale_72h"])

    expected_counts = {
        "latest_iv_discharge_count": discharge_count,
        "latest_iv_stage_count": stage_count,
        "stale_discharge_6h_count": stale_6h_count,
        "stale_discharge_24h_count": stale_24h_count,
        "stale_discharge_72h_count": stale_72h_count,
    }
    for key, expected in expected_counts.items():
        if _count(summary.get(key), f"USGS streamflow {key}") != expected:
            raise ProductError(f"USGS streamflow summary {key} disagrees with features")
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
        raise ProductError("publisher product ID does not select the streamflow callback")

    candidate_time_text, _ = validate_product(candidate_root)
    semantic_key = metadata.get("semantic_key")
    if not isinstance(semantic_key, dict):
        raise ProductError("streamflow candidate metadata semantic key is missing")
    if semantic_key.get("type") != "feed_build_time_utc":
        raise ProductError("streamflow semantic key type must be feed_build_time_utc")
    if semantic_key.get("value") != candidate_time_text:
        raise ProductError("streamflow build time does not match artifact metadata")
    candidate_time = _timestamp(candidate_time_text, "candidate feed_build_time_utc")

    canonical_presence = [(worktree / path).is_file() for path in ALLOWLIST]
    if not any(canonical_presence):
        state = "new"
    elif not all(canonical_presence):
        raise ProductError("current main contains a partial USGS streamflow product set")
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
                    "same streamflow semantic time has different bytes; refusing ambiguous overwrite"
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
        print(f"Validated USGS streamflow candidate: {count} features at {build_time}")
    elif phase == "reconcile":
        reconcile()
    elif phase == "validate-staged":
        build_time, count = validate_product(Path(os.environ["BRIM_PUBLISH_WORKTREE"]))
        print(f"Validated staged USGS streamflow product: {count} features at {build_time}")
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
            print(f"Validated USGS streamflow product: {count} features at {build_time}")
            return 0
        if args.command is None:
            return callback_main()
        raise ProductError(f"unsupported command: {args.command}")
    except (KeyError, ProductError) as exc:
        print(f"USGS_STREAMFLOW_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
