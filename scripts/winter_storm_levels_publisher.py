#!/usr/bin/env python3
"""Validate and reconcile the two-cycle Winter Storm Levels product tree."""

from __future__ import annotations

import argparse
import copy
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


PRODUCT_ID = "winter-storm-levels"
WIRE_PRODUCT_ID = "winter_storm_levels"
SOURCE_ID = "nbm_snow_level"
SCHEMA_VERSION = "1.0.0"
MANIFEST_PATH = Path(
    "docs/data/winter-storm-levels/winter_storm_levels_manifest.json"
)
TARGET_ROOT = Path("docs/data/winter-storm-levels/nbm/snow-level")
BUNDLE_ROOT = Path("docs/data/winter-storm-levels")
TARGET_LEADS = (1, 6, 12, 18, 24, 30, 36, 42, 48, 60, 72)
TARGET_PATTERN = re.compile(
    r"winter_storm_levels_nbm_snow_level_(\d{10})_f(\d{3})_([0-9a-f]{12})\.geojson"
)
TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
SHA_PATTERN = re.compile(r"[0-9a-f]{64}")
STATUS_VALUES = {
    "current",
    "delayed_but_usable",
    "stale_last_known_good",
    "expired",
}
EXPECTED_DOMAIN = {
    "id": "winter_storm_levels_west_v1",
    "label": (
        "California, southern Oregon, Nevada, northwest Arizona, lower Colorado "
        "corridor, and adjacent Pacific waters"
    ),
    "bbox_wgs84": [-130, 30, -112, 44.5],
}
EXPECTED_CONTOUR = {
    "geometry": "LineString",
    "datum": "mean_sea_level",
    "unit": "ft_msl",
    "minimum_ft": 0,
    "maximum_ft": 20000,
    "interval_ft": 1000,
    "simplify_tolerance_m": 750,
}
EXPECTED_FRESHNESS = {
    "current_after_hours": 9,
    "delayed_after_hours": 15,
    "expire_after_hours": 24,
    "valid_tolerance_hours": 3,
}
EXPECTED_SOURCE = {
    "id": SOURCE_ID,
    "name": "NOAA/NWS National Blend of Models deterministic snow level",
    "agency": "NOAA/NWS/NCEP Meteorological Development Laboratory",
    "parameter": "SNOWLVL",
    "definition": "Elevation where wet-bulb temperature reaches 0.5 degrees C",
    "source_unit": "m above mean sea level",
    "output_unit": "ft above mean sea level",
    "product_url": "https://www.nco.ncep.noaa.gov/pmb/products/blend/",
    "retrieval_base_url": "https://noaa-nbm-grib2-pds.s3.amazonaws.com/",
    "alternate_retrieval_base_url": "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/",
}
MANIFEST_KEYS = {
    "product_id",
    "schema_version",
    "contract_version",
    "status",
    "source",
    "domain",
    "contour",
    "freshness",
    "cycle_time_utc",
    "retrieval_time_utc",
    "publication_time_utc",
    "target_count",
    "diagnostics",
    "targets",
}
ENTRY_KEYS = {
    "source_id",
    "cycle_time_utc",
    "valid_time_utc",
    "valid_from_utc",
    "valid_through_utc",
    "lead_hours",
    "retrieval_url",
    "inventory_url",
    "inventory_record",
    "path",
    "media_type",
    "sha256",
    "bytes",
    "feature_count",
    "contour_levels_ft_msl",
    "source_grid",
    "output_bbox_wgs84",
}
PROPERTY_KEYS = {
    "product_id",
    "source_id",
    "parameter",
    "definition",
    "level_ft_msl",
    "label",
    "unit",
    "cycle_time_utc",
    "valid_time_utc",
    "lead_hours",
    "segment",
    "length_m",
}


class ProductError(RuntimeError):
    """A malformed or ambiguous Winter Storm Levels state."""


@dataclass(frozen=True)
class EntryState:
    entry: Dict[str, object]
    cycle: datetime
    valid: datetime
    lead: int
    relative_path: Path
    repository_path: Path
    sha256: str


@dataclass(frozen=True)
class ProductState:
    root: Path
    manifest: Dict[str, object]
    current_cycle: datetime
    cycles: tuple[datetime, ...]
    entries: tuple[EntryState, ...]
    targets: Dict[Path, EntryState]


def _read_json(path: Path) -> object:
    if not path.is_file() or path.is_symlink():
        raise ProductError(f"missing plain Winter Storm Levels JSON: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductError(f"unreadable Winter Storm Levels JSON {path}: {exc}") from exc


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not TIMESTAMP_PATTERN.fullmatch(value):
        raise ProductError(f"{label} must be an RFC 3339 UTC timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ProductError(f"{label} is not a valid UTC timestamp") from exc


def _integer(value: object, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProductError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ProductError(f"{label} must be at least {minimum}")
    return value


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProductError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ProductError(f"{label} must be finite")
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _target_path(value: object) -> tuple[Path, Path, datetime, int, str]:
    if not isinstance(value, str) or not value:
        raise ProductError("manifest target path must be a nonempty string")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != value:
        raise ProductError(f"unsafe Winter Storm Levels target path: {value!r}")
    repository_path = BUNDLE_ROOT / relative
    if repository_path.parent != TARGET_ROOT:
        raise ProductError(f"target is outside the exact owned root: {value}")
    match = TARGET_PATTERN.fullmatch(repository_path.name)
    if match is None:
        raise ProductError(f"target filename identity is invalid: {value}")
    try:
        cycle = datetime.strptime(match.group(1), "%Y%m%d%H").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ProductError(f"target filename cycle is invalid: {value}") from exc
    return relative, repository_path, cycle, int(match.group(2)), match.group(3)


def _validate_bbox(value: object, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 4:
        raise ProductError(f"{label} must contain four coordinates")
    bbox = [_finite(item, label) for item in value]
    west, south, east, north = bbox
    if west > east or south > north:
        raise ProductError(f"{label} is inverted")
    domain_west, domain_south, domain_east, domain_north = EXPECTED_DOMAIN[
        "bbox_wgs84"
    ]
    if (
        west < domain_west - 1e-6
        or east > domain_east + 1e-6
        or south < domain_south - 1e-6
        or north > domain_north + 1e-6
    ):
        raise ProductError(f"{label} exceeds the configured domain")
    return bbox


def _validate_geojson(
    root: Path,
    repository_path: Path,
    *,
    cycle_text: str,
    valid_text: str,
    lead: int,
) -> tuple[int, list[int], list[float]]:
    value = _read_json(root / repository_path)
    if not isinstance(value, dict) or set(value) != {
        "type",
        "contract_version",
        "bbox",
        "features",
    }:
        raise ProductError(f"GeoJSON root shape is invalid: {repository_path}")
    if value.get("type") != "FeatureCollection" or value.get(
        "contract_version"
    ) != SCHEMA_VERSION:
        raise ProductError(f"GeoJSON contract identity is invalid: {repository_path}")
    declared_bbox = _validate_bbox(value.get("bbox"), "GeoJSON bbox")
    features = value.get("features")
    if not isinstance(features, list) or not features:
        raise ProductError(f"GeoJSON has no contour features: {repository_path}")
    identifiers: set[str] = set()
    levels: set[int] = set()
    all_coordinates: list[tuple[float, float]] = []
    segment_keys: set[tuple[int, int]] = set()
    for index, feature in enumerate(features):
        label = f"GeoJSON feature {index}"
        if not isinstance(feature, dict) or set(feature) != {
            "type",
            "id",
            "properties",
            "geometry",
        }:
            raise ProductError(f"{label} shape is invalid")
        identifier = feature.get("id")
        if feature.get("type") != "Feature" or not isinstance(identifier, str) or not identifier:
            raise ProductError(f"{label} identity is invalid")
        if identifier in identifiers:
            raise ProductError("GeoJSON feature IDs are not unique")
        identifiers.add(identifier)
        properties = feature.get("properties")
        if not isinstance(properties, dict) or set(properties) != PROPERTY_KEYS:
            raise ProductError(f"{label} property shape is invalid")
        expected_strings = {
            "product_id": WIRE_PRODUCT_ID,
            "source_id": SOURCE_ID,
            "parameter": "snow_level",
            "definition": "height of the wet-bulb 0.5 degree C surface",
            "unit": "ft_msl",
            "cycle_time_utc": cycle_text,
            "valid_time_utc": valid_text,
        }
        for key, expected in expected_strings.items():
            if properties.get(key) != expected:
                raise ProductError(f"{label} {key} is invalid")
        if _integer(properties.get("lead_hours"), f"{label} lead_hours") != lead:
            raise ProductError(f"{label} lead disagrees with manifest")
        level = _integer(properties.get("level_ft_msl"), f"{label} level")
        if level < 0 or level > 20000 or level % 1000:
            raise ProductError(f"{label} contour level is invalid")
        if properties.get("label") != f"{level:,} ft MSL":
            raise ProductError(f"{label} contour label is invalid")
        segment = _integer(properties.get("segment"), f"{label} segment", minimum=1)
        if (level, segment) in segment_keys:
            raise ProductError("GeoJSON contour level/segment identities are duplicated")
        segment_keys.add((level, segment))
        if _finite(properties.get("length_m"), f"{label} length_m") <= 0:
            raise ProductError(f"{label} length must be positive")
        levels.add(level)
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict) or set(geometry) != {"type", "coordinates"}:
            raise ProductError(f"{label} geometry shape is invalid")
        coordinates = geometry.get("coordinates")
        if geometry.get("type") != "LineString" or not isinstance(
            coordinates, list
        ) or len(coordinates) < 2:
            raise ProductError(f"{label} is not a complete LineString")
        previous: tuple[float, float] | None = None
        for coordinate in coordinates:
            if not isinstance(coordinate, list) or len(coordinate) != 2:
                raise ProductError(f"{label} coordinate shape is invalid")
            point = (
                _finite(coordinate[0], f"{label} longitude"),
                _finite(coordinate[1], f"{label} latitude"),
            )
            if not (
                -130 - 1e-6 <= point[0] <= -112 + 1e-6
                and 30 - 1e-6 <= point[1] <= 44.5 + 1e-6
            ):
                raise ProductError(f"{label} coordinate exceeds the configured domain")
            if point == previous:
                raise ProductError(f"{label} contains adjacent duplicate coordinates")
            all_coordinates.append(point)
            previous = point
    actual_bbox = [
        min(point[0] for point in all_coordinates),
        min(point[1] for point in all_coordinates),
        max(point[0] for point in all_coordinates),
        max(point[1] for point in all_coordinates),
    ]
    if any(abs(left - right) > 1e-5 for left, right in zip(actual_bbox, declared_bbox)):
        raise ProductError(f"GeoJSON bbox disagrees with serialized coordinates: {repository_path}")
    return len(features), sorted(levels), declared_bbox


def _validate_entry(root: Path, value: object, index: int) -> EntryState:
    if not isinstance(value, dict) or set(value) != ENTRY_KEYS:
        raise ProductError(f"manifest target {index} shape is invalid")
    if value.get("source_id") != SOURCE_ID or value.get("media_type") != "application/geo+json":
        raise ProductError(f"manifest target {index} source or media type is invalid")
    lead = _integer(value.get("lead_hours"), f"manifest target {index} lead")
    if lead not in TARGET_LEADS:
        raise ProductError(f"manifest target {index} lead is unsupported")
    cycle = _timestamp(value.get("cycle_time_utc"), f"manifest target {index} cycle")
    valid = _timestamp(value.get("valid_time_utc"), f"manifest target {index} valid time")
    if cycle.minute or cycle.second or cycle.hour % 6 or valid != cycle + timedelta(hours=lead):
        raise ProductError(f"manifest target {index} cycle/lead/valid time is incoherent")
    tolerance = timedelta(hours=EXPECTED_FRESHNESS["valid_tolerance_hours"])
    if _timestamp(value.get("valid_from_utc"), f"manifest target {index} valid_from") != valid - tolerance:
        raise ProductError(f"manifest target {index} validity start is incoherent")
    if _timestamp(value.get("valid_through_utc"), f"manifest target {index} valid_through") != valid + tolerance:
        raise ProductError(f"manifest target {index} validity end is incoherent")
    relative_path, repository_path, path_cycle, path_lead, short_sha = _target_path(
        value.get("path")
    )
    if path_cycle != cycle or path_lead != lead:
        raise ProductError(f"manifest target {index} path identity disagrees with time")
    sha = value.get("sha256")
    if not isinstance(sha, str) or SHA_PATTERN.fullmatch(sha) is None or sha[:12] != short_sha:
        raise ProductError(f"manifest target {index} SHA/path identity is invalid")
    target = root / repository_path
    if not target.is_file() or target.is_symlink():
        raise ProductError(f"manifest target is missing or not a plain file: {repository_path}")
    if _sha256(target) != sha:
        raise ProductError(f"manifest target checksum mismatch: {repository_path}")
    if target.stat().st_size != _integer(
        value.get("bytes"), f"manifest target {index} bytes", minimum=1
    ):
        raise ProductError(f"manifest target size mismatch: {repository_path}")
    for key in ("retrieval_url", "inventory_url", "inventory_record"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ProductError(f"manifest target {index} {key} is invalid")
    if not value["retrieval_url"].startswith("https://"):
        raise ProductError(f"manifest target {index} retrieval URL is invalid")
    if value["inventory_url"] != value["retrieval_url"] + ".idx":
        raise ProductError(f"manifest target {index} inventory URL is invalid")
    if ":SNOWLVL:0 m above mean sea level:" not in value["inventory_record"]:
        raise ProductError(f"manifest target {index} inventory record is invalid")
    source_grid = value.get("source_grid")
    if not isinstance(source_grid, dict) or set(source_grid) != {
        "rows",
        "columns",
        "resolution_m",
        "finite_coverage",
        "min_m",
        "max_m",
    }:
        raise ProductError(f"manifest target {index} source grid is invalid")
    _integer(source_grid.get("rows"), "source grid rows", minimum=1)
    _integer(source_grid.get("columns"), "source grid columns", minimum=1)
    if _finite(source_grid.get("resolution_m"), "source grid resolution") <= 0:
        raise ProductError("source grid resolution must be positive")
    coverage = _finite(source_grid.get("finite_coverage"), "source grid coverage")
    low = _finite(source_grid.get("min_m"), "source grid minimum")
    high = _finite(source_grid.get("max_m"), "source grid maximum")
    if not 0 < coverage <= 1 or low > high:
        raise ProductError(f"manifest target {index} source grid values are incoherent")
    feature_count, levels, bbox = _validate_geojson(
        root,
        repository_path,
        cycle_text=value["cycle_time_utc"],
        valid_text=value["valid_time_utc"],
        lead=lead,
    )
    if feature_count != _integer(
        value.get("feature_count"), f"manifest target {index} feature_count", minimum=1
    ):
        raise ProductError(f"manifest target {index} feature count is incoherent")
    contour_levels = value.get("contour_levels_ft_msl")
    if not isinstance(contour_levels, list) or contour_levels != levels:
        raise ProductError(f"manifest target {index} contour levels are incoherent")
    output_bbox = _validate_bbox(value.get("output_bbox_wgs84"), "target output bbox")
    if any(abs(left - right) > 1e-5 for left, right in zip(output_bbox, bbox)):
        raise ProductError(f"manifest target {index} output bbox is incoherent")
    return EntryState(
        entry=dict(value),
        cycle=cycle,
        valid=valid,
        lead=lead,
        relative_path=relative_path,
        repository_path=repository_path,
        sha256=sha,
    )


def _inventory(root: Path) -> set[Path]:
    bundle = root / BUNDLE_ROOT
    if not bundle.is_dir() or bundle.is_symlink():
        raise ProductError("Winter Storm Levels bundle root is missing or unsafe")
    allowed_directories = {
        BUNDLE_ROOT,
        BUNDLE_ROOT / "nbm",
        TARGET_ROOT,
    }
    files: set[Path] = set()
    for path in bundle.rglob("*"):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ProductError(f"Winter Storm Levels ownership tree contains a symlink: {relative}")
        if path.is_dir():
            if relative not in allowed_directories:
                raise ProductError(f"unexpected Winter Storm Levels directory: {relative}")
        elif path.is_file():
            files.add(relative)
        else:
            raise ProductError(f"unsupported Winter Storm Levels tree member: {relative}")
    return files


def validate_product(root: Path) -> ProductState:
    manifest = _read_json(root / MANIFEST_PATH)
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        raise ProductError("manifest root shape is invalid")
    if (
        manifest.get("product_id") != WIRE_PRODUCT_ID
        or manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("contract_version") != SCHEMA_VERSION
    ):
        raise ProductError("manifest product/schema/contract identity is invalid")
    if manifest.get("status") not in STATUS_VALUES:
        raise ProductError("manifest status is invalid")
    source = manifest.get("source")
    if source != EXPECTED_SOURCE:
        raise ProductError("manifest source semantics are invalid")
    if manifest.get("domain") != EXPECTED_DOMAIN:
        raise ProductError("manifest domain contract is invalid")
    if manifest.get("contour") != EXPECTED_CONTOUR:
        raise ProductError("manifest contour contract is invalid")
    if manifest.get("freshness") != EXPECTED_FRESHNESS:
        raise ProductError("manifest freshness contract is invalid")
    current_cycle = _timestamp(manifest.get("cycle_time_utc"), "manifest root cycle")
    retrieval = _timestamp(manifest.get("retrieval_time_utc"), "manifest retrieval time")
    if current_cycle.minute or current_cycle.second or current_cycle.hour % 6:
        raise ProductError("manifest root cycle is not a six-hour NBM cycle")
    if retrieval < current_cycle:
        raise ProductError("manifest retrieval time predates its model cycle")
    if manifest.get("publication_time_utc") is not None:
        raise ProductError("manifest publication time must remain null in contract 1.0.0")
    values = manifest.get("targets")
    if not isinstance(values, list) or len(values) not in {11, 22}:
        raise ProductError("manifest must contain one or two complete 11-target cycles")
    entries = tuple(_validate_entry(root, value, index) for index, value in enumerate(values))
    cycles = tuple(dict.fromkeys(entry.cycle for entry in entries))
    if not 1 <= len(cycles) <= 2 or cycles != tuple(sorted(cycles, reverse=True)):
        raise ProductError("manifest retained cycles are incomplete or not newest-first")
    if cycles[0] != current_cycle:
        raise ProductError("manifest root cycle is not the newest retained cycle")
    for cycle in cycles:
        leads = [entry.lead for entry in entries if entry.cycle == cycle]
        if leads != list(TARGET_LEADS):
            raise ProductError(f"manifest cycle lead set/order is incomplete: {cycle}")
    if len({(entry.cycle, entry.valid) for entry in entries}) != len(entries):
        raise ProductError("manifest contains duplicate cycle/valid-time identities")
    diagnostics = manifest.get("diagnostics")
    expected_diagnostics = {
        "expected_current_cycle_target_count": len(TARGET_LEADS),
        "actual_current_cycle_target_count": len(TARGET_LEADS),
        "retained_cycle_count": len(cycles),
        "complete_bundle_validated": True,
    }
    if diagnostics != expected_diagnostics:
        raise ProductError("manifest diagnostics are incoherent")
    if manifest.get("target_count") != len(entries):
        raise ProductError("manifest target count is incoherent")
    targets = {entry.repository_path: entry for entry in entries}
    if len(targets) != len(entries):
        raise ProductError("manifest references duplicate target paths")
    files = _inventory(root)
    expected_files = {MANIFEST_PATH, *targets}
    if files != expected_files:
        raise ProductError(
            "manifest-target closure failed; "
            f"missing={sorted(str(path) for path in expected_files - files)}; "
            f"unexpected={sorted(str(path) for path in files - expected_files)}"
        )
    return ProductState(
        root=root,
        manifest=dict(manifest),
        current_cycle=current_cycle,
        cycles=cycles,
        entries=entries,
        targets=targets,
    )


def semantic_key(root: Path) -> str:
    state = validate_product(root)
    digest = hashlib.sha256()
    for path in (MANIFEST_PATH, *sorted(state.targets)):
        digest.update(path.as_posix().encode("utf-8") + b"\0")
        digest.update(_sha256(root / path).encode("ascii") + b"\n")
    return digest.hexdigest()


def _semantic_manifest(value: Mapping[str, object]) -> Dict[str, object]:
    result = copy.deepcopy(dict(value))
    result.pop("retrieval_time_utc", None)
    result.pop("publication_time_utc", None)
    return result


def _copy_if_changed(source: Path, destination: Path) -> None:
    if destination.is_file() and not destination.is_symlink():
        if _sha256(source) == _sha256(destination):
            return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _apply_desired_state(
    candidate: ProductState,
    canonical: ProductState | None,
    worktree: Path,
) -> None:
    if canonical is None:
        desired_entries = [copy.deepcopy(entry.entry) for entry in candidate.entries]
        sources = {path: candidate.root for path in candidate.targets}
    else:
        current_entries = [
            copy.deepcopy(entry.entry)
            for entry in candidate.entries
            if entry.cycle == candidate.current_cycle
        ]
        prior_entries = [
            copy.deepcopy(entry.entry)
            for entry in canonical.entries
            if entry.cycle == canonical.current_cycle
        ]
        desired_entries = current_entries + prior_entries
        sources = {
            **{
                entry.repository_path: candidate.root
                for entry in candidate.entries
                if entry.cycle == candidate.current_cycle
            },
            **{
                entry.repository_path: canonical.root
                for entry in canonical.entries
                if entry.cycle == canonical.current_cycle
            },
        }
    desired_paths = set(sources)
    target_root = worktree / TARGET_ROOT
    target_root.mkdir(parents=True, exist_ok=True)
    for path in sorted(target_root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise ProductError(f"publication target tree contains a symlink: {path}")
        if path.is_file() and path.relative_to(worktree) not in desired_paths:
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    for relative_path, source_root in sources.items():
        _copy_if_changed(source_root / relative_path, worktree / relative_path)
    manifest = copy.deepcopy(candidate.manifest)
    manifest["targets"] = desired_entries
    manifest["target_count"] = len(desired_entries)
    manifest["diagnostics"] = {
        "expected_current_cycle_target_count": len(TARGET_LEADS),
        "actual_current_cycle_target_count": len(TARGET_LEADS),
        "retained_cycle_count": len({entry["cycle_time_utc"] for entry in desired_entries}),
        "complete_bundle_validated": True,
    }
    destination = worktree / MANIFEST_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def reconcile(candidate_root: Path, canonical_root: Path) -> tuple[str, str]:
    candidate = validate_product(candidate_root)
    if not (canonical_root / MANIFEST_PATH).exists():
        _apply_desired_state(candidate, None, canonical_root)
        validate_product(canonical_root)
        return "new", "canonical Winter Storm Levels product was absent"
    canonical = validate_product(canonical_root)
    if _semantic_manifest(candidate.manifest) == _semantic_manifest(canonical.manifest):
        return "same", "Winter Storm Levels two-cycle semantic state is canonical"
    if candidate.current_cycle < canonical.current_cycle:
        return "stale", "candidate cycle is older than fresh canonical"
    if candidate.current_cycle == canonical.current_cycle:
        raise ProductError("same-cycle Winter Storm Levels states conflict")
    _apply_desired_state(candidate, canonical, canonical_root)
    desired = validate_product(canonical_root)
    if desired.cycles != (candidate.current_cycle, canonical.current_cycle):
        raise ProductError("fresh-main two-cycle rollover did not retain the canonical current cycle")
    return "new", "newer complete cycle advanced with exact two-cycle retention"


def _callback() -> int:
    phase = os.environ.get("BRIM_PUBLISH_PHASE")
    candidate_root = Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"])
    worktree = Path(os.environ["BRIM_PUBLISH_WORKTREE"])
    if os.environ.get("BRIM_PUBLISH_PRODUCT_ID") != PRODUCT_ID:
        raise ProductError("publisher product ID is not Winter Storm Levels")
    if phase == "validate-candidate":
        metadata = _read_json(Path(os.environ["BRIM_PUBLISH_METADATA"]))
        expected = {
            "type": "winter_storm_two_cycle_state_sha256_v1",
            "value": semantic_key(candidate_root),
        }
        if not isinstance(metadata, dict) or metadata.get("semantic_key") != expected:
            raise ProductError("candidate semantic key disagrees with product bytes")
        return 0
    if phase == "validate-staged":
        validate_product(worktree)
        return 0
    if phase == "reconcile":
        state, reason = reconcile(candidate_root, worktree)
        Path(os.environ["BRIM_PUBLISH_RESULT"]).write_text(
            json.dumps(
                {
                    "decision": "publish" if state == "new" else "no-op",
                    "candidate_state": state,
                    "reason": reason,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return 0
    raise ProductError(f"unsupported Winter Storm Levels publisher phase: {phase!r}")


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
                "Validated Winter Storm Levels product: "
                f"{len(state.cycles)} complete cycle(s); {len(state.entries)} targets"
            )
        else:
            print(semantic_key(Path(args.root)))
        return 0
    except ProductError as exc:
        print(f"WINTER_STORM_LEVELS_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
