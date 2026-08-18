#!/usr/bin/env python3
"""Validate and reconcile the rolling NOAA NBM wind-guidance product tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Mapping, Sequence


PRODUCT_ID = "nbm-wind-guidance"
MANIFEST_PATH = Path("docs/data/wind/nbm_wind_guidance_feed_manifest.json")
SUMMARY_PATH = Path("docs/data/wind/nbm_wind_guidance_latest_summary.json")
TARGET_ROOT = Path("docs/data/wind/nbm/guidance")
FIXED_PATHS = (MANIFEST_PATH, SUMMARY_PATH)
TARGET_PATTERN = re.compile(
    r"nbm_wind_guidance_(\d{8})T(\d{2})Z_f(\d{3})\.geojson"
)
TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
FEED_VERSION = "RTW-NBM005"
TARGET_LEADS = (6, 12, 24, 48)
PUBLISHED_LEADS = (6, 12, 18, 24, 30, 48, 54)
SUPPORT_WINDOW_HOURS = 6
RETENTION_HOURS = 18
MAX_TARGET_DISTANCE_HOURS = 3.1
MIN_FEATURE_COUNT = 100
AVAILABLE_FORECAST_HOURS = frozenset(
    (*range(1, 49), *range(51, 193, 3), *range(198, 265, 6))
)
PROPERTY_KEYS = {
    "grid_i",
    "grid_j",
    "wind_dir_degrees",
    "wind_dir_cardinal",
    "wind_p10_mph",
    "wind_p50_mph",
    "wind_p90_mph",
    "gust_p10_mph",
    "gust_p50_mph",
    "gust_p90_mph",
}
CARDINALS = (
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
)
EXPECTED_DOMAIN = {
    "id": "hydrologic_ca_adjacent",
    "label": "Hydrologic California + adjacent basins",
    "west": -125.5,
    "east": -112.0,
    "south": 31.0,
    "north": 43.5,
    "resolution_degrees": 0.25,
    "nx": 55,
    "ny": 51,
}


class ProductError(RuntimeError):
    """A malformed or ambiguous NBM rolling-product state."""


@dataclass(frozen=True)
class TargetState:
    relative_path: Path
    cycle: datetime
    forecast_hour: int
    valid: datetime
    target_lead: int
    feature_count: int
    sha256: str
    metadata: Dict[str, object]


@dataclass(frozen=True)
class EntryState:
    entry: Dict[str, object]
    cycle: datetime
    forecast_hour: int
    valid: datetime
    target_lead: int
    relative_path: Path
    target: TargetState


@dataclass(frozen=True)
class ProductState:
    root: Path
    manifest: Dict[str, object]
    summary: Dict[str, object]
    generated: datetime
    current_cycle: datetime
    entries: tuple[EntryState, ...]
    targets: Dict[Path, TargetState]


def _read_json(path: Path) -> object:
    if not path.is_file() or path.is_symlink():
        raise ProductError(f"missing plain NBM product file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductError(f"unreadable NBM JSON {path}: {exc}") from exc


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not TIMESTAMP_PATTERN.fullmatch(value):
        raise ProductError(f"{label} must be an RFC 3339 UTC timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ProductError(f"{label} is not a valid UTC timestamp") from exc


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProductError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ProductError(f"{label} must be finite")
    return result


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProductError(f"{label} must be an integer")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_domain(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ProductError(f"{label} must be an object")
    for key, expected in EXPECTED_DOMAIN.items():
        actual = value.get(key)
        if isinstance(expected, str):
            if actual != expected:
                raise ProductError(f"{label}.{key} is invalid")
        elif _finite_number(actual, f"{label}.{key}") != expected:
            raise ProductError(f"{label}.{key} is invalid")
    return value


def _validate_stats(value: object, label: str) -> None:
    if not isinstance(value, dict):
        raise ProductError(f"{label} must be an object")
    numbers = [
        _finite_number(value.get(key), f"{label}.{key}")
        for key in ("min", "p50", "p75", "p90", "p95", "max")
    ]
    if numbers[0] < 0 or numbers != sorted(numbers):
        raise ProductError(f"{label} quantiles are invalid")


def _validate_land_mask(
    value: object, label: str, feature_count: int
) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ProductError(f"{label} must be an object")
    for key in (
        "method",
        "polygon_database",
        "operational_mask_id",
        "operational_mask_label",
    ):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ProductError(f"{label}.{key} is invalid")
    if value.get("operational_mask_id") != "hydrologic_ca_operational_v1":
        raise ProductError(f"{label}.operational_mask_id is invalid")
    if not isinstance(value.get("operational_mask_vertices"), list):
        raise ProductError(f"{label}.operational_mask_vertices is invalid")
    counts = {
        key: _integer(value.get(key), f"{label}.{key}")
        for key in (
            "source_grid_points",
            "mapped_land_points",
            "coastal_fringe_points",
            "land_and_coast_points",
            "outside_operational_mask_removed",
            "retained_points",
            "offshore_points_removed",
        )
    }
    if any(count < 0 for count in counts.values()):
        raise ProductError(f"{label} counts must be nonnegative")
    if (
        counts["retained_points"] != feature_count
        or counts["mapped_land_points"] + counts["coastal_fringe_points"]
        != counts["land_and_coast_points"]
        or counts["retained_points"]
        + counts["outside_operational_mask_removed"]
        + counts["offshore_points_removed"]
        != counts["source_grid_points"]
    ):
        raise ProductError(f"{label} counts are incoherent")
    return value


def _target_identity(path: Path) -> tuple[datetime, int]:
    match = TARGET_PATTERN.fullmatch(path.name)
    if match is None:
        raise ProductError(f"NBM target filename is invalid: {path}")
    try:
        cycle = datetime.strptime(
            f"{match.group(1)}T{match.group(2)}Z", "%Y%m%dT%HZ"
        ).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ProductError(f"NBM target filename cycle is invalid: {path}") from exc
    return cycle, int(match.group(3))


def _validate_target(root: Path, relative_path: Path) -> TargetState:
    if relative_path.parent != TARGET_ROOT:
        raise ProductError(f"NBM target is outside the flat product root: {relative_path}")
    cycle, forecast_hour = _target_identity(relative_path)
    if forecast_hour not in AVAILABLE_FORECAST_HOURS:
        raise ProductError(f"NBM target forecast hour is unsupported: {relative_path}")
    value = _read_json(root / relative_path)
    if not isinstance(value, dict) or value.get("type") != "FeatureCollection":
        raise ProductError(f"NBM target is not a FeatureCollection: {relative_path}")
    if set(value) != {"type", "name", "metadata", "features"}:
        raise ProductError(f"NBM target root shape is invalid: {relative_path}")
    if value.get("name") != "NOAA NBM wind guidance":
        raise ProductError(f"NBM target name is invalid: {relative_path}")
    metadata, features = value.get("metadata"), value.get("features")
    if not isinstance(metadata, dict) or not isinstance(features, list):
        raise ProductError(f"NBM target metadata/features are invalid: {relative_path}")
    if metadata.get("feed_version") != FEED_VERSION:
        raise ProductError(f"NBM target feed version is invalid: {relative_path}")
    if metadata.get("model") != "NOAA/NWS National Blend of Models":
        raise ProductError(f"NBM target model is invalid: {relative_path}")
    if _timestamp(metadata.get("model_cycle_utc"), "NBM target model cycle") != cycle:
        raise ProductError(f"NBM target cycle disagrees with filename: {relative_path}")
    valid = _timestamp(metadata.get("valid_time_utc"), "NBM target valid time")
    if valid != cycle + timedelta(hours=forecast_hour):
        raise ProductError(f"NBM target valid time is incoherent: {relative_path}")
    if metadata.get("forecast_hour") != forecast_hour:
        raise ProductError(f"NBM target forecast hour disagrees with filename: {relative_path}")
    target_lead = _integer(metadata.get("target_lead_hours"), "NBM target lead")
    if target_lead not in PUBLISHED_LEADS:
        raise ProductError(f"NBM target lead is unsupported: {relative_path}")
    domain = _validate_domain(metadata.get("domain"), "NBM target domain")
    if len(features) < MIN_FEATURE_COUNT:
        raise ProductError(f"NBM target has too few features: {relative_path}")
    land_mask = _validate_land_mask(
        metadata.get("land_mask"), "NBM target land mask", len(features)
    )
    guidance = metadata.get("guidance")
    if not isinstance(guidance, dict) or set(guidance) != {
        "direction",
        "sustained",
        "gust",
    } or any(not isinstance(value, str) or not value for value in guidance.values()):
        raise ProductError(f"NBM target guidance metadata is invalid: {relative_path}")

    cells: set[tuple[int, int]] = set()
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or set(feature) != {
            "type",
            "geometry",
            "properties",
        }:
            raise ProductError(f"NBM feature {index} shape is invalid: {relative_path}")
        if feature.get("type") != "Feature":
            raise ProductError(f"NBM feature {index} identity is invalid: {relative_path}")
        geometry, properties = feature.get("geometry"), feature.get("properties")
        if (
            not isinstance(geometry, dict)
            or geometry.get("type") != "Point"
            or set(geometry) != {"type", "coordinates"}
            or not isinstance(geometry.get("coordinates"), list)
            or len(geometry["coordinates"]) != 2
            or not isinstance(properties, dict)
            or set(properties) != PROPERTY_KEYS
        ):
            raise ProductError(f"NBM feature {index} geometry/properties are invalid")
        lon = _finite_number(
            geometry["coordinates"][0], f"NBM feature {index} longitude"
        )
        lat = _finite_number(
            geometry["coordinates"][1], f"NBM feature {index} latitude"
        )
        if not domain["west"] <= lon <= domain["east"] or not (
            domain["south"] <= lat <= domain["north"]
        ):
            raise ProductError(f"NBM feature {index} coordinate is outside the domain")
        grid_i = _integer(properties.get("grid_i"), f"NBM feature {index} grid_i")
        grid_j = _integer(properties.get("grid_j"), f"NBM feature {index} grid_j")
        if not 0 <= grid_i < int(domain["nx"]) or not (
            0 <= grid_j < int(domain["ny"])
        ):
            raise ProductError(f"NBM feature {index} grid index is invalid")
        if (grid_i, grid_j) in cells:
            raise ProductError(f"NBM target has duplicate grid cells: {relative_path}")
        cells.add((grid_i, grid_j))
        direction = _finite_number(
            properties.get("wind_dir_degrees"),
            f"NBM feature {index} wind direction",
        )
        expected_cardinal = CARDINALS[int(math.floor((direction + 11.25) / 22.5)) % 16]
        if not 0 <= direction < 360 or properties.get("wind_dir_cardinal") != expected_cardinal:
            raise ProductError(f"NBM feature {index} wind direction is invalid")
        wind = [
            _finite_number(
                properties.get(key), f"NBM feature {index} {key}"
            )
            for key in ("wind_p10_mph", "wind_p50_mph", "wind_p90_mph")
        ]
        gust = [
            _finite_number(
                properties.get(key), f"NBM feature {index} {key}"
            )
            for key in ("gust_p10_mph", "gust_p50_mph", "gust_p90_mph")
        ]
        if wind[0] < 0 or gust[0] < 0 or wind != sorted(wind) or gust != sorted(gust):
            raise ProductError(f"NBM feature {index} percentile values are invalid")
    return TargetState(
        relative_path=relative_path,
        cycle=cycle,
        forecast_hour=forecast_hour,
        valid=valid,
        target_lead=target_lead,
        feature_count=len(features),
        sha256=_sha256(root / relative_path),
        metadata=dict(metadata),
    )


def _inventory(root: Path) -> Dict[Path, TargetState]:
    target_root = root / TARGET_ROOT
    if not target_root.is_dir() or target_root.is_symlink():
        raise ProductError(f"missing plain NBM target directory: {target_root}")
    targets: Dict[Path, TargetState] = {}
    for path in sorted(target_root.rglob("*")):
        if path.is_symlink():
            raise ProductError(f"NBM target tree contains a symlink: {path}")
        if path.is_file():
            relative_path = path.relative_to(root)
            targets[relative_path] = _validate_target(root, relative_path)
    if not targets:
        raise ProductError("NBM target inventory is empty")
    return targets


def _entry_relative_path(value: Mapping[str, object], cycle: datetime, forecast_hour: int) -> Path:
    expected_name = (
        f"nbm_wind_guidance_{cycle:%Y%m%dT%H}Z_f{forecast_hour:03d}.geojson"
    )
    expected_url = f"nbm/guidance/{expected_name}"
    if value.get("relative_url") != expected_url:
        raise ProductError("NBM manifest entry relative URL is incoherent")
    return TARGET_ROOT / expected_name


def _validate_entry(
    root: Path,
    targets: Mapping[Path, TargetState],
    value: object,
    index: int,
    generated: datetime,
) -> EntryState:
    if not isinstance(value, dict):
        raise ProductError(f"NBM manifest entry {index} must be an object")
    required = {
        "feed_version",
        "model",
        "product",
        "target_lead_hours",
        "target_label",
        "model_cycle_utc",
        "model_cycle_local",
        "forecast_hour",
        "forecast_hour_label",
        "valid_time_utc",
        "valid_time_local",
        "relative_url",
        "feature_count",
        "land_mask",
        "grid",
        "wind_p50_mph",
        "gust_p50_mph",
        "recommended_gust_scale_mph",
        "color_scale_reference",
        "source_core",
        "source_qmd",
    }
    if not required.issubset(value):
        raise ProductError(f"NBM manifest entry {index} is missing required fields")
    if value.get("feed_version") != FEED_VERSION:
        raise ProductError(f"NBM manifest entry {index} feed version is invalid")
    if (
        value.get("model") != "NOAA/NWS National Blend of Models"
        or value.get("product")
        != "NBM wind central guidance + p10-p90 uncertainty"
    ):
        raise ProductError(f"NBM manifest entry {index} product identity is invalid")
    target_lead = _integer(
        value.get("target_lead_hours"), f"NBM manifest entry {index} target lead"
    )
    if target_lead not in PUBLISHED_LEADS or value.get("target_label") != f"+{target_lead} hr":
        raise ProductError(f"NBM manifest entry {index} target identity is invalid")
    cycle = _timestamp(value.get("model_cycle_utc"), f"NBM entry {index} cycle")
    forecast_hour = _integer(
        value.get("forecast_hour"), f"NBM entry {index} forecast hour"
    )
    if forecast_hour not in AVAILABLE_FORECAST_HOURS:
        raise ProductError(f"NBM manifest entry {index} forecast hour is unsupported")
    valid = _timestamp(value.get("valid_time_utc"), f"NBM entry {index} valid time")
    if valid != cycle + timedelta(hours=forecast_hour):
        raise ProductError(f"NBM manifest entry {index} valid time is incoherent")
    if value.get("forecast_hour_label") != f"f{forecast_hour:03d}":
        raise ProductError(f"NBM manifest entry {index} forecast label is incoherent")
    target_time = generated + timedelta(hours=target_lead)
    if abs((valid - target_time).total_seconds()) > MAX_TARGET_DISTANCE_HOURS * 3600:
        raise ProductError(f"NBM manifest entry {index} misses its target time")
    relative_path = _entry_relative_path(value, cycle, forecast_hour)
    target = targets.get(relative_path)
    if target is None:
        raise ProductError(f"NBM manifest references a missing target: {relative_path}")
    if (
        target.cycle != cycle
        or target.forecast_hour != forecast_hour
        or target.valid != valid
        or target.target_lead != target_lead
        or value.get("feature_count") != target.feature_count
    ):
        raise ProductError(f"NBM manifest entry {index} disagrees with its target")
    if value.get("land_mask") != target.metadata.get("land_mask"):
        raise ProductError(f"NBM manifest entry {index} land mask disagrees with target")
    grid = value.get("grid")
    if not isinstance(grid, dict) or grid != {
        "nx": EXPECTED_DOMAIN["nx"],
        "ny": EXPECTED_DOMAIN["ny"],
        "resolution_degrees": EXPECTED_DOMAIN["resolution_degrees"],
    }:
        raise ProductError(f"NBM manifest entry {index} grid is invalid")
    _validate_stats(value.get("wind_p50_mph"), f"NBM entry {index} wind stats")
    _validate_stats(value.get("gust_p50_mph"), f"NBM entry {index} gust stats")
    scale = _finite_number(
        value.get("recommended_gust_scale_mph"),
        f"NBM entry {index} gust scale",
    )
    if not 15 <= scale <= 70:
        raise ProductError(f"NBM manifest entry {index} gust scale is invalid")
    for key in ("model_cycle_local", "valid_time_local", "color_scale_reference"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ProductError(f"NBM manifest entry {index} {key} is invalid")
    for product in ("core", "qmd"):
        key = f"source_{product}"
        expected = (
            "https://noaa-nbm-grib2-pds.s3.amazonaws.com/"
            f"blend.{cycle:%Y%m%d}/{cycle:%H}/{product}/"
            f"blend.t{cycle:%H}z.{product}.f{forecast_hour:03d}.co.grib2"
        )
        if value.get(key) != expected:
            raise ProductError(f"NBM manifest entry {index} {key} is invalid")
    return EntryState(
        entry=dict(value),
        cycle=cycle,
        forecast_hour=forecast_hour,
        valid=valid,
        target_lead=target_lead,
        relative_path=relative_path,
        target=target,
    )


def validate_product(root: Path) -> ProductState:
    root = root.resolve()
    manifest = _read_json(root / MANIFEST_PATH)
    summary = _read_json(root / SUMMARY_PATH)
    if not isinstance(manifest, dict) or not isinstance(summary, dict):
        raise ProductError("NBM manifest and summary roots must be objects")
    if (
        manifest.get("model") != "NOAA/NWS National Blend of Models"
        or manifest.get("product") != "Wind central guidance and uncertainty"
    ):
        raise ProductError("NBM manifest product identity is invalid")
    if (
        manifest.get("feed_version") != FEED_VERSION
        or summary.get("feed_version") != FEED_VERSION
    ):
        raise ProductError("NBM manifest/summary feed version is invalid")
    generated = _timestamp(manifest.get("generated_at_utc"), "NBM generated_at_utc")
    if _timestamp(summary.get("generated_at_utc"), "NBM summary generated_at_utc") != generated:
        raise ProductError("NBM manifest/summary generation times disagree")
    if manifest.get("target_lead_hours") != list(TARGET_LEADS) or summary.get(
        "target_lead_hours"
    ) != list(TARGET_LEADS):
        raise ProductError("NBM browser target leads are invalid")
    if manifest.get("published_support_lead_hours") != list(
        PUBLISHED_LEADS
    ) or summary.get("published_support_lead_hours") != list(PUBLISHED_LEADS):
        raise ProductError("NBM published support leads are invalid")
    if manifest.get("support_window_hours") != SUPPORT_WINDOW_HOURS or summary.get(
        "support_window_hours"
    ) != SUPPORT_WINDOW_HOURS:
        raise ProductError("NBM support-window policy is invalid")
    domain = _validate_domain(manifest.get("domain"), "NBM manifest domain")
    if summary.get("domain") != domain:
        raise ProductError("NBM manifest/summary domains disagree")
    values = manifest.get("entries")
    if not isinstance(values, list) or len(values) != len(PUBLISHED_LEADS):
        raise ProductError("NBM manifest must contain exactly seven complete-cycle entries")
    targets = _inventory(root)
    entries = tuple(
        _validate_entry(root, targets, value, index, generated)
        for index, value in enumerate(values)
    )
    leads = [entry.target_lead for entry in entries]
    if leads != list(PUBLISHED_LEADS) or len(set(leads)) != len(leads):
        raise ProductError("NBM manifest support leads are incomplete, duplicated, or unsorted")
    cycles = {entry.cycle for entry in entries}
    if len(cycles) != 1:
        raise ProductError("NBM manifest entries do not form one complete model cycle")
    current_cycle = next(iter(cycles))
    if (
        current_cycle.minute != 0
        or current_cycle.second != 0
        or current_cycle.hour % 6
        or generated < current_cycle
    ):
        raise ProductError("NBM selected model cycle is invalid")
    if _timestamp(summary.get("selected_cycle_utc"), "NBM selected cycle") != current_cycle:
        raise ProductError("NBM summary selected cycle disagrees with manifest")
    expected_summary = {
        "entry_count": len(entries),
        "feature_count_per_entry": [entry.target.feature_count for entry in entries],
        "valid_times_utc": [entry.entry["valid_time_utc"] for entry in entries],
        "published_support_lead_hours": leads,
        "offshore_points_removed_per_entry": [
            entry.target.metadata["land_mask"]["offshore_points_removed"]
            for entry in entries
        ],
        "outside_operational_mask_removed_per_entry": [
            entry.target.metadata["land_mask"]["outside_operational_mask_removed"]
            for entry in entries
        ],
        "operational_mask_id": "hydrologic_ca_operational_v1",
    }
    for key, expected in expected_summary.items():
        if summary.get(key) != expected:
            raise ProductError(f"NBM summary {key} is incoherent")
    referenced = {entry.relative_path for entry in entries}
    if len(referenced) != len(entries):
        raise ProductError("NBM manifest references duplicate targets")
    cutoff = current_cycle - timedelta(hours=RETENTION_HOURS)
    expired = sorted(
        path.as_posix()
        for path, target in targets.items()
        if target.cycle < cutoff
    )
    future = sorted(
        path.as_posix()
        for path, target in targets.items()
        if target.cycle > current_cycle
    )
    if expired or future:
        raise ProductError(
            "NBM retained-target inventory violates the cycle window; "
            f"expired={expired}; future={future}"
        )
    return ProductState(
        root=root,
        manifest=dict(manifest),
        summary=dict(summary),
        generated=generated,
        current_cycle=current_cycle,
        entries=entries,
        targets=targets,
    )


def semantic_key(root: Path) -> str:
    state = validate_product(root)
    digest = hashlib.sha256()
    paths = (*FIXED_PATHS, *sorted(state.targets))
    for path in paths:
        digest.update(path.as_posix().encode("utf-8") + b"\0")
        digest.update(_sha256(state.root / path).encode("ascii") + b"\n")
    return digest.hexdigest()


def _same_complete_state(left: ProductState, right: ProductState) -> bool:
    left_paths = {*FIXED_PATHS, *left.targets}
    right_paths = {*FIXED_PATHS, *right.targets}
    return left_paths == right_paths and all(
        (left.root / path).read_bytes() == (right.root / path).read_bytes()
        for path in left_paths
    )


def _desired_targets(
    candidate: ProductState, canonical: ProductState
) -> Dict[Path, tuple[TargetState, Path]]:
    cutoff = candidate.current_cycle - timedelta(hours=RETENTION_HOURS)
    desired: Dict[Path, tuple[TargetState, Path]] = {}
    for state, source_root in (
        (canonical, canonical.root),
        (candidate, candidate.root),
    ):
        for path, target in state.targets.items():
            if target.cycle < cutoff or target.cycle > candidate.current_cycle:
                continue
            previous = desired.get(path)
            if previous is not None and previous[0].sha256 != target.sha256:
                raise ProductError(
                    f"same-identity NBM target has conflicting bytes: {path}"
                )
            if previous is None:
                desired[path] = (target, source_root)
    for entry in candidate.entries:
        if entry.relative_path not in desired:
            raise ProductError(
                f"fresh-main NBM reconciliation lost a current target: {entry.relative_path}"
            )
    return desired


def _copy_if_changed(source: Path, destination: Path) -> None:
    if destination.is_file() and not destination.is_symlink():
        if _sha256(source) == _sha256(destination):
            return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _apply_desired_state(
    candidate: ProductState,
    worktree: Path,
    desired: Mapping[Path, tuple[TargetState, Path]],
) -> None:
    target_root = worktree / TARGET_ROOT
    target_root.mkdir(parents=True, exist_ok=True)
    desired_paths = set(desired)
    for path in sorted(target_root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise ProductError(f"NBM publication target tree contains a symlink: {path}")
        if path.is_file() and path.relative_to(worktree) not in desired_paths:
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    for relative_path, (_, source_root) in desired.items():
        _copy_if_changed(
            source_root / relative_path,
            worktree / relative_path,
        )
    for fixed_path in FIXED_PATHS:
        _copy_if_changed(candidate.root / fixed_path, worktree / fixed_path)


def reconcile(candidate_root: Path, canonical_root: Path) -> tuple[str, str]:
    candidate = validate_product(candidate_root)
    if not (canonical_root / MANIFEST_PATH).exists():
        _apply_desired_state(
            candidate,
            canonical_root,
            {
                path: (target, candidate.root)
                for path, target in candidate.targets.items()
            },
        )
        validate_product(canonical_root)
        return "new", "canonical NBM wind product was absent"
    canonical = validate_product(canonical_root)
    if _same_complete_state(candidate, canonical):
        return "same", "complete NBM cycle and retained target state are canonical"
    if candidate.current_cycle < canonical.current_cycle:
        return "stale", "candidate NBM cycle is older than fresh canonical"
    if candidate.current_cycle == canonical.current_cycle:
        if candidate.generated < canonical.generated:
            return "stale", "candidate generation is older within the canonical NBM cycle"
        if candidate.generated == canonical.generated:
            raise ProductError(
                "same-generation NBM states have conflicting complete bytes"
            )
    elif candidate.generated <= canonical.generated:
        raise ProductError(
            "newer NBM model cycle does not have a newer generation timestamp"
        )
    desired = _desired_targets(candidate, canonical)
    _apply_desired_state(candidate, canonical_root, desired)
    validate_product(canonical_root)
    if candidate.current_cycle == canonical.current_cycle:
        return "new", "newer complete selection within the same NBM cycle was reconciled"
    return "new", "newer complete NBM cycle advanced with exact 18-hour retention"


def _callback() -> int:
    phase = os.environ.get("BRIM_PUBLISH_PHASE")
    candidate_root = Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"])
    worktree = Path(os.environ["BRIM_PUBLISH_WORKTREE"])
    if os.environ.get("BRIM_PUBLISH_PRODUCT_ID") != PRODUCT_ID:
        raise ProductError("publisher product ID is not the NBM wind product")
    if phase == "validate-candidate":
        metadata = _read_json(Path(os.environ["BRIM_PUBLISH_METADATA"]))
        if not isinstance(metadata, dict):
            raise ProductError("candidate metadata root is invalid")
        expected = {
            "type": "nbm_wind_complete_cycle_state_sha256_v1",
            "value": semantic_key(candidate_root),
        }
        if metadata.get("semantic_key") != expected:
            raise ProductError(
                "candidate NBM semantic key disagrees with product bytes"
            )
        return 0
    if phase == "validate-staged":
        validate_product(worktree)
        return 0
    if phase == "reconcile":
        state, reason = reconcile(candidate_root, worktree)
        decision = "publish" if state == "new" else "no-op"
        Path(os.environ["BRIM_PUBLISH_RESULT"]).write_text(
            json.dumps(
                {
                    "decision": decision,
                    "candidate_state": state,
                    "reason": reason,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return 0
    raise ProductError(f"unsupported NBM publisher phase: {phase!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "semantic-key"):
        child = subparsers.add_parser(command)
        child.add_argument("--root", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        if os.environ.get("BRIM_PUBLISH_PHASE"):
            return _callback()
        args = build_parser().parse_args(argv)
        if args.command == "validate":
            state = validate_product(Path(args.root))
            print(
                "Validated NBM wind product: "
                f"{len(state.entries)} current targets; "
                f"{len(state.targets)} retained targets"
            )
        else:
            print(semantic_key(Path(args.root)))
        return 0
    except ProductError as exc:
        print(f"NBM_WIND_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
