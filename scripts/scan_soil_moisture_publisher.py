#!/usr/bin/env python3
"""Validate and reconcile the fixed SCAN soil-moisture publication set."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Sequence, Tuple


PRODUCT_ID = "scan-soil-moisture-latest"
GEOJSON_PATH = Path("docs/data/scan_soil_moisture_latest.geojson")
SUMMARY_PATH = Path("docs/data/scan_soil_moisture_latest_summary.json")
TRACE_PATH = Path("docs/data/scan_soil_moisture_current_wy_trace.csv")
TRACE_SUMMARY_PATH = Path("docs/data/scan_soil_moisture_current_wy_trace_summary.json")
STYLE_PATH = Path("docs/data/scan_depth_style.csv")
ALLOWLIST = (GEOJSON_PATH, SUMMARY_PATH, TRACE_PATH, TRACE_SUMMARY_PATH, STYLE_PATH)
TRACE_FIELDS = (
    "station_uid",
    "station_name",
    "site_code",
    "depth_in",
    "depth_label",
    "depth_order",
    "depth_color_hex",
    "water_year",
    "water_day",
    "obs_date",
    "obs_datetime_local",
    "sms_pct",
    "sensor_count",
    "sensor_id",
)
STYLE_FIELDS = ("depth_in", "depth_label", "depth_order", "depth_color_hex", "depth_role")


class ProductError(RuntimeError):
    """A malformed or ambiguous SCAN state."""


def _plain_file(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise ProductError(f"missing plain SCAN file: {path}")


def _read_json(path: Path) -> Dict[str, object]:
    _plain_file(path)
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


def _date(value: object, label: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ProductError(f"{label} must be an ISO calendar date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ProductError(f"{label} is not a valid date: {value!r}") from exc


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProductError(f"{label} must be an integer >= {minimum}")
    return value


def _number(value: str, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ProductError(f"{label} is not numeric") from exc
    if not math.isfinite(number):
        raise ProductError(f"{label} is not finite")
    return number


def _read_csv(path: Path, fields: tuple[str, ...]) -> list[Dict[str, str]]:
    _plain_file(path)
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != fields:
                raise ProductError(f"CSV header mismatch for {path}")
            rows = list(reader)
    except OSError as exc:
        raise ProductError(f"unreadable CSV {path}: {exc}") from exc
    if not rows:
        raise ProductError(f"CSV contains no data rows: {path}")
    return rows


def validate_product(root: Path) -> Tuple[str, int]:
    root = root.resolve()
    geojson = _read_json(root / GEOJSON_PATH)
    summary = _read_json(root / SUMMARY_PATH)
    trace_summary = _read_json(root / TRACE_SUMMARY_PATH)
    trace_rows = _read_csv(root / TRACE_PATH, TRACE_FIELDS)
    style_rows = _read_csv(root / STYLE_PATH, STYLE_FIELDS)

    build_time = summary.get("feed_build_time_utc")
    _timestamp(build_time, "SCAN feed_build_time_utc")
    if geojson.get("feed_build_time_utc") != build_time:
        raise ProductError("SCAN GeoJSON and summary build times disagree")
    if trace_summary.get("feed_build_time_utc") != build_time:
        raise ProductError("SCAN trace summary and latest summary build times disagree")

    if geojson.get("type") != "FeatureCollection":
        raise ProductError("SCAN GeoJSON must be a FeatureCollection")
    features = geojson.get("features")
    if not isinstance(features, list) or not features:
        raise ProductError("SCAN GeoJSON must contain features")
    features_written = _integer(summary.get("features_written"), "SCAN features_written", minimum=1)
    if len(features) != features_written:
        raise ProductError("SCAN summary features_written does not match GeoJSON")
    if _integer(summary.get("station_index_rows"), "SCAN station_index_rows", minimum=1) != len(features):
        raise ProductError("SCAN station-index count does not match GeoJSON")
    stations_with_sms = _integer(
        summary.get("stations_with_any_current_wy_sms"),
        "SCAN stations_with_any_current_wy_sms",
        minimum=10,
    )
    stations_without_sms = _integer(
        summary.get("stations_without_current_wy_sms"),
        "SCAN stations_without_current_wy_sms",
    )
    if stations_with_sms + stations_without_sms != len(features):
        raise ProductError("SCAN station coverage counts do not match GeoJSON")
    _integer(summary.get("depth_rows_latest"), "SCAN depth_rows_latest", minimum=1)

    seen_uids = set()
    features_with_sms = 0
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ProductError(f"SCAN feature {index} is not a GeoJSON Feature")
        geometry = feature.get("geometry")
        properties = feature.get("properties")
        if not isinstance(geometry, dict) or geometry.get("type") != "Point":
            raise ProductError(f"SCAN feature {index} is not a Point")
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) != 2:
            raise ProductError(f"SCAN feature {index} has invalid coordinates")
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
            raise ProductError(f"SCAN feature {index} coordinates are not finite lon/lat")
        if not isinstance(properties, dict):
            raise ProductError(f"SCAN feature {index} properties must be an object")
        for key in ("station_uid", "station_name", "source_url", "feed_build_time_utc"):
            if not isinstance(properties.get(key), str) or not properties[key]:
                raise ProductError(f"SCAN feature {index} is missing string {key}")
        if properties["feed_build_time_utc"] != build_time:
            raise ProductError(f"SCAN feature {index} build time disagrees")
        if properties["station_uid"] in seen_uids:
            raise ProductError(f"SCAN feature {index} duplicates station_uid")
        seen_uids.add(properties["station_uid"])
        _integer(properties.get("site_code"), f"SCAN feature {index} site_code", minimum=1)
        display_sms = properties.get("display_sms_pct")
        if display_sms is not None:
            if (
                isinstance(display_sms, bool)
                or not isinstance(display_sms, (int, float))
                or not math.isfinite(display_sms)
            ):
                raise ProductError(f"SCAN feature {index} has invalid display_sms_pct")
            features_with_sms += 1
            display_depth = properties.get("display_depth_in")
            if (
                isinstance(display_depth, bool)
                or not isinstance(display_depth, (int, float))
                or not math.isfinite(display_depth)
            ):
                raise ProductError(f"SCAN feature {index} has invalid display_depth_in")
            _timestamp(
                properties.get("display_obs_datetime_utc"),
                f"SCAN feature {index} display_obs_datetime_utc",
            )
    if features_with_sms != stations_with_sms:
        raise ProductError("SCAN usable display-value count does not match summary")

    expected_trace_rows = _integer(
        summary.get("current_wy_trace_rows"), "SCAN current_wy_trace_rows", minimum=1
    )
    if len(trace_rows) != expected_trace_rows:
        raise ProductError("SCAN latest summary trace count does not match CSV")
    if _integer(trace_summary.get("trace_rows"), "SCAN trace summary rows", minimum=1) != len(trace_rows):
        raise ProductError("SCAN trace summary count does not match CSV")

    trace_sites = set()
    for index, row in enumerate(trace_rows):
        for key in ("station_uid", "station_name", "depth_label", "obs_date", "sensor_id"):
            if not row.get(key):
                raise ProductError(f"SCAN trace row {index} is missing {key}")
        trace_sites.add(row["site_code"])
        for key in ("site_code", "depth_in", "depth_order", "water_year", "water_day", "sensor_count"):
            if _number(row[key], f"SCAN trace row {index} {key}") < 0:
                raise ProductError(f"SCAN trace row {index} {key} is negative")
        _number(row["sms_pct"], f"SCAN trace row {index} sms_pct")
        _date(row["obs_date"], f"SCAN trace row {index} obs_date")
    if _integer(trace_summary.get("stations"), "SCAN trace stations", minimum=1) != len(trace_sites):
        raise ProductError("SCAN trace summary station count does not match CSV")

    style_depths = set()
    for index, row in enumerate(style_rows):
        depth = _number(row["depth_in"], f"SCAN depth style row {index} depth_in")
        if depth in style_depths:
            raise ProductError(f"SCAN depth style row {index} duplicates depth")
        style_depths.add(depth)
        if not row["depth_label"] or not row["depth_role"]:
            raise ProductError(f"SCAN depth style row {index} is missing labels")
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", row["depth_color_hex"]):
            raise ProductError(f"SCAN depth style row {index} has invalid color")
        _number(row["depth_order"], f"SCAN depth style row {index} depth_order")

    for label, value in (("summary", summary), ("trace summary", trace_summary)):
        if value.get("depth_style_output_csv") != STYLE_PATH.as_posix():
            raise ProductError(f"SCAN {label} embeds a noncanonical depth-style path")
    for key in ("current_water_year", "current_wy_start_date"):
        if summary.get(key) != trace_summary.get(key) or summary.get(key) != geojson.get(key):
            raise ProductError(f"SCAN {key} disagrees across product files")
    _integer(summary.get("current_water_year"), "SCAN current_water_year", minimum=1)
    _date(summary.get("current_wy_start_date"), "SCAN current_wy_start_date")

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
        raise ProductError("publisher product ID does not select the SCAN callback")

    candidate_time_text, _ = validate_product(candidate_root)
    semantic_key = metadata.get("semantic_key")
    if not isinstance(semantic_key, dict):
        raise ProductError("SCAN candidate metadata semantic key is missing")
    if semantic_key.get("type") != "feed_build_time_utc":
        raise ProductError("SCAN semantic key type must be feed_build_time_utc")
    if semantic_key.get("value") != candidate_time_text:
        raise ProductError("SCAN build time does not match artifact metadata")
    candidate_time = _timestamp(candidate_time_text, "candidate SCAN feed_build_time_utc")

    canonical_presence = [(worktree / path).is_file() for path in ALLOWLIST]
    if not any(canonical_presence):
        state = "new"
    elif not all(canonical_presence):
        raise ProductError("current main contains a partial SCAN product set")
    else:
        canonical_time_text, _ = validate_product(worktree)
        canonical_time = _timestamp(canonical_time_text, "canonical SCAN feed_build_time_utc")
        if candidate_time < canonical_time:
            _write_result(
                result_path,
                decision="no-op",
                state="stale",
                reason="candidate build time is older than current main",
            )
            return
        if candidate_time == canonical_time:
            identical = all(
                (candidate_root / path).read_bytes() == (worktree / path).read_bytes()
                for path in ALLOWLIST
            )
            if not identical:
                raise ProductError("same SCAN build time has different bytes; refusing ambiguous overwrite")
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
        reason="candidate build time is newer than current main",
    )


def callback_main() -> int:
    phase = os.environ.get("BRIM_PUBLISH_PHASE")
    if phase == "validate-candidate":
        build_time, count = validate_product(Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"]))
        print(f"Validated SCAN candidate: {count} features at {build_time}")
    elif phase == "reconcile":
        reconcile()
    elif phase == "validate-staged":
        build_time, count = validate_product(Path(os.environ["BRIM_PUBLISH_WORKTREE"]))
        print(f"Validated staged SCAN product: {count} features at {build_time}")
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
            print(f"Validated SCAN product: {count} features at {build_time}")
            return 0
        if args.command is None:
            return callback_main()
        raise ProductError(f"unsupported command: {args.command}")
    except (KeyError, ProductError) as exc:
        print(f"SCAN_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
