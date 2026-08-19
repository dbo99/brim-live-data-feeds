#!/usr/bin/env python3
"""Validate and reconcile the rolling NOAA HRRR surface-wind product tree."""

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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Mapping, Sequence


PRODUCT_ID = "hrrr-wind-latest"
DATA_PRODUCT_ID = "hrrr_surface_wind"
MANIFEST_PATH = Path("docs/data/wind/hrrr_surface_wind_feed_manifest.json")
LATEST_PATH = Path("docs/data/wind/hrrr_surface_wind_latest.json")
SUMMARY_PATH = Path("docs/data/wind/hrrr_surface_wind_latest_summary.json")
TARGET_ROOT = Path("docs/data/wind/hrrr/surface")
FIXED_PATHS = (LATEST_PATH, SUMMARY_PATH, MANIFEST_PATH)
TARGET_PATTERN = re.compile(r"hrrr_surface_wind_(\d{8})T(\d{2})Z_f(\d{2})\.json")
TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
GRID_KEYS = ("nx", "ny", "lo1", "la1", "lo2", "la2", "dx", "dy", "refTime", "forecastTime")
SUPPORTED_LEADS = (0, 6, 12)
RETAIN_PAST_HOURS = 4.0
RETAIN_FUTURE_HOURS = 14.0
STALE_AFTER_HOURS = 4.0
MAX_MANIFEST_ENTRIES = 24
MAX_FORECAST_HOUR = 18
MAX_TARGET_DISTANCE_MINUTES = 126.0
# Prior R outputs derived freshness from a fractional timestamp, then truncated
# the serialized timestamp to whole seconds. Permit exactly that directional
# legacy discrepancy, plus 2 ms for six-decimal JSON number serialization.
LEGACY_TIMESTAMP_TRUNCATION_SECONDS = 1.0
NUMERIC_SERIALIZATION_EPSILON_SECONDS = 0.002


class ProductError(RuntimeError):
    """A malformed or ambiguous HRRR rolling-product state."""


@dataclass(frozen=True)
class EntryState:
    entry: Dict[str, object]
    valid: datetime
    target: datetime
    cycle: datetime
    forecast_hour: int
    requested_lead: int
    relative_path: Path
    sha256: str


@dataclass(frozen=True)
class ProductState:
    root: Path
    manifest: Dict[str, object]
    summary: Dict[str, object]
    generated: datetime
    target_base: datetime
    entries: tuple[EntryState, ...]
    selected: EntryState


def _read_json(path: Path) -> object:
    if not path.is_file() or path.is_symlink():
        raise ProductError(f"missing plain HRRR product file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductError(f"unreadable HRRR JSON {path}: {exc}") from exc


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


def _matches_legacy_timestamp_truncation(
    reported: float, reconstructed: float, *, seconds_per_unit: float
) -> bool:
    discrepancy_seconds = (reported - reconstructed) * seconds_per_unit
    return (
        -NUMERIC_SERIALIZATION_EPSILON_SECONDS
        <= discrepancy_seconds
        <= LEGACY_TIMESTAMP_TRUNCATION_SECONDS
        + NUMERIC_SERIALIZATION_EPSILON_SECONDS
    )


def _validate_summary_freshness(
    summary: Mapping[str, object], reconstructed_lag_minutes: float
) -> None:
    reported_lag_minutes = _finite_number(
        summary.get("valid_lag_minutes_at_build"), "HRRR summary valid lag"
    )
    reported_age_hours = _finite_number(
        summary.get("valid_time_age_hours"), "HRRR summary valid age"
    )
    if (
        not _matches_legacy_timestamp_truncation(
            reported_lag_minutes,
            reconstructed_lag_minutes,
            seconds_per_unit=60.0,
        )
        or not _matches_legacy_timestamp_truncation(
            reported_age_hours,
            reconstructed_lag_minutes / 60.0,
            seconds_per_unit=3600.0,
        )
        or summary.get("is_stale") is not (reported_age_hours > STALE_AFTER_HOURS)
    ):
        raise ProductError("HRRR summary valid-time freshness is incoherent")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_target(
    path: Path, *, cycle: datetime, forecast_hour: int
) -> Mapping[str, object]:
    value = _read_json(path)
    if not isinstance(value, list) or len(value) != 2:
        raise ProductError(f"HRRR target must contain exactly U/V fields: {path}")
    headers: list[Mapping[str, object]] = []
    parameters: set[int] = set()
    for index, field in enumerate(value):
        if not isinstance(field, dict) or set(field) != {"header", "data"}:
            raise ProductError(f"HRRR target field {index} has an invalid shape: {path}")
        header, data = field["header"], field["data"]
        if not isinstance(header, dict) or not isinstance(data, list):
            raise ProductError(f"HRRR target field {index} header/data is invalid: {path}")
        if (
            header.get("parameterCategory") != 2
            or header.get("parameterNumber") not in {2, 3}
            or header.get("parameterUnit") != "m.s-1"
        ):
            raise ProductError(f"HRRR target field {index} is not a U/V wind component")
        parameters.add(int(header["parameterNumber"]))
        nx, ny = header.get("nx"), header.get("ny")
        if not isinstance(nx, int) or not isinstance(ny, int) or nx <= 0 or ny <= 0:
            raise ProductError(f"HRRR target grid dimensions are invalid: {path}")
        if len(data) != nx * ny:
            raise ProductError(f"HRRR target data length does not equal nx * ny: {path}")
        for data_index, item in enumerate(data):
            _finite_number(item, f"HRRR target data value {data_index}")
        if _timestamp(header.get("refTime"), "HRRR target refTime") != cycle:
            raise ProductError(f"HRRR target refTime disagrees with manifest: {path}")
        if header.get("forecastTime") != forecast_hour:
            raise ProductError(f"HRRR target forecastTime disagrees with manifest: {path}")
        for key in ("lo1", "la1", "lo2", "la2", "dx", "dy"):
            _finite_number(header.get(key), f"HRRR target header {key}")
        headers.append(header)
    if parameters != {2, 3}:
        raise ProductError(f"HRRR target must contain one U and one V component: {path}")
    for key in GRID_KEYS:
        if headers[1].get(key) != headers[0].get(key):
            raise ProductError(f"HRRR target U/V grids disagree for {key}: {path}")
    return headers[0]


def _target_relative_path(
    entry: Mapping[str, object], *, cycle: datetime, forecast_hour: int
) -> Path:
    filename, relative_url = entry.get("filename"), entry.get("relative_url")
    if not isinstance(filename, str) or not TARGET_PATTERN.fullmatch(filename):
        raise ProductError("HRRR entry filename is invalid")
    expected = f"hrrr_surface_wind_{cycle:%Y%m%dT%H}Z_f{forecast_hour:02d}.json"
    if filename != expected or relative_url != f"hrrr/surface/{filename}":
        raise ProductError("HRRR entry filename/relative_url disagrees with cycle and lead")
    return TARGET_ROOT / filename


def _validate_speed_object(value: object, label: str) -> None:
    if not isinstance(value, dict):
        raise ProductError(f"{label} is invalid")
    numbers = [_finite_number(value.get(key), f"{label}.{key}") for key in ("min", "p50", "p75", "p90", "p95", "max")]
    if numbers[0] < 0 or numbers != sorted(numbers):
        raise ProductError(f"{label} quantiles are invalid")


def _validate_entry(root: Path, value: object, index: int) -> EntryState:
    if not isinstance(value, dict) or value.get("product_id") != DATA_PRODUCT_ID:
        raise ProductError(f"HRRR manifest entry {index} identity is invalid")
    if value.get("variables") != ["UGRD", "VGRD"]:
        raise ProductError(f"HRRR manifest entry {index} variables are invalid")
    requested_lead = value.get("requested_lead_hours")
    if not isinstance(requested_lead, int) or isinstance(requested_lead, bool) or requested_lead not in SUPPORTED_LEADS:
        raise ProductError(f"HRRR manifest entry {index} requested lead is invalid")
    target = _timestamp(value.get("target_valid_time_utc"), f"HRRR entry {index} target time")
    cycle = _timestamp(value.get("model_cycle_utc"), f"HRRR entry {index} model cycle")
    valid = _timestamp(value.get("valid_time_utc"), f"HRRR entry {index} valid time")
    forecast_hour = value.get("forecast_hour")
    if not isinstance(forecast_hour, int) or isinstance(forecast_hour, bool) or not 0 <= forecast_hour <= MAX_FORECAST_HOUR:
        raise ProductError(f"HRRR manifest entry {index} forecast_hour is invalid")
    if valid != cycle + timedelta(hours=forecast_hour):
        raise ProductError(f"HRRR manifest entry {index} valid time is incoherent")
    if value.get("forecast_hour_label") != f"f{forecast_hour:02d}":
        raise ProductError(f"HRRR manifest entry {index} forecast label is incoherent")
    distance = _finite_number(value.get("target_distance_minutes"), f"HRRR entry {index} target distance")
    expected_distance = abs((valid - target).total_seconds()) / 60.0
    if distance < 0 or abs(distance - expected_distance) > 1e-6 or distance > MAX_TARGET_DISTANCE_MINUTES:
        raise ProductError(f"HRRR manifest entry {index} target distance is invalid")
    relative_path = _target_relative_path(value, cycle=cycle, forecast_hour=forecast_hour)
    target_path = root / relative_path
    if not target_path.is_file() or target_path.is_symlink():
        raise ProductError(f"HRRR manifest references a missing target: {relative_path}")
    if value.get("file_bytes") != target_path.stat().st_size:
        raise ProductError(f"HRRR manifest target byte count is wrong: {relative_path}")
    header = _validate_target(target_path, cycle=cycle, forecast_hour=forecast_hour)
    domain, grid = value.get("domain"), value.get("grid")
    if not isinstance(domain, dict) or not isinstance(grid, dict):
        raise ProductError(f"HRRR manifest entry {index} domain/grid is invalid")
    expected_domain = {
        "id": "hydrologic_ca_adjacent", "west": -125.5, "east": -112.0,
        "south": 31.0, "north": 43.5,
    }
    for key, expected in expected_domain.items():
        actual = domain.get(key)
        if key == "id":
            if actual != expected:
                raise ProductError(f"HRRR manifest entry {index} domain is invalid")
        elif _finite_number(actual, f"HRRR entry {index} domain.{key}") != expected:
            raise ProductError(f"HRRR manifest entry {index} domain is invalid")
    if not isinstance(domain.get("label"), str) or not domain["label"]:
        raise ProductError(f"HRRR manifest entry {index} domain label is invalid")
    expected_grid = {
        "nx": header["nx"], "ny": header["ny"],
        "resolution_degrees": header["dx"], "cell_count": header["nx"] * header["ny"],
    }
    for key, expected in expected_grid.items():
        if grid.get(key) != expected:
            raise ProductError(f"HRRR manifest entry {index} grid.{key} is invalid")
    if (
        header["lo1"] != expected_domain["west"]
        or header["lo2"] != expected_domain["east"]
        or max(header["la1"], header["la2"]) != expected_domain["north"]
        or min(header["la1"], header["la2"]) != expected_domain["south"]
        or header["dx"] != header["dy"]
    ):
        raise ProductError(f"HRRR manifest entry {index} target grid/domain is incoherent")
    if value.get("earth_relative_winds_confirmed") is not True:
        raise ProductError(f"HRRR manifest entry {index} is not earth-relative")
    if not isinstance(value.get("vector_regrid_method"), str) or not value["vector_regrid_method"]:
        raise ProductError(f"HRRR manifest entry {index} regrid method is invalid")
    _validate_speed_object(value.get("speed_ms"), f"HRRR entry {index} speed_ms")
    _validate_speed_object(value.get("speed_mph"), f"HRRR entry {index} speed_mph")
    for key in ("recommended_velocity_scale_ms", "recommended_velocity_scale_mph"):
        if _finite_number(value.get(key), f"HRRR entry {index} {key}") <= 0:
            raise ProductError(f"HRRR manifest entry {index} {key} is invalid")
    if not isinstance(value.get("velocity_scale_reference"), str) or not value["velocity_scale_reference"]:
        raise ProductError(f"HRRR manifest entry {index} velocity scale reference is invalid")
    for key in ("source", "source_request_url"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ProductError(f"HRRR manifest entry {index} {key} is invalid")
    return EntryState(value, valid, target, cycle, forecast_hour, requested_lead, relative_path, _sha256(target_path))


def _inventory(root: Path) -> set[Path]:
    target_root = root / TARGET_ROOT
    if not target_root.is_dir() or target_root.is_symlink():
        raise ProductError(f"missing plain HRRR target directory: {target_root}")
    paths: set[Path] = set()
    for path in target_root.rglob("*"):
        if path.is_symlink():
            raise ProductError(f"HRRR target tree contains a symlink: {path}")
        if path.is_file():
            paths.add(path.relative_to(root))
    return paths


def _select_entry(entries: Sequence[EntryState], generated: datetime) -> EntryState:
    return min(
        entries,
        key=lambda entry: (
            abs((entry.valid - generated).total_seconds()),
            1 if entry.valid > generated else 0,
            -entry.cycle.timestamp(),
            entry.forecast_hour,
        ),
    )


def validate_product(root: Path) -> ProductState:
    root = root.resolve()
    manifest, summary = _read_json(root / MANIFEST_PATH), _read_json(root / SUMMARY_PATH)
    if not isinstance(manifest, dict) or not isinstance(summary, dict):
        raise ProductError("HRRR manifest and summary roots must be objects")
    if manifest.get("product_id") != DATA_PRODUCT_ID or manifest.get("feed_mode") != "multi_target_time_set":
        raise ProductError("HRRR manifest identity is invalid")
    if summary.get("product_id") != DATA_PRODUCT_ID or summary.get("feed_mode") != "multi_target_time_set_with_legacy_latest" or summary.get("status") != "success":
        raise ProductError("HRRR summary identity/status is invalid")
    if manifest.get("version") != summary.get("version"):
        raise ProductError("HRRR manifest/summary versions disagree")
    generated = _timestamp(manifest.get("generated_utc"), "HRRR generated_utc")
    if _timestamp(summary.get("build_time_utc"), "HRRR build_time_utc") != generated:
        raise ProductError("HRRR manifest/summary build times disagree")
    target_base = _timestamp(manifest.get("target_base_utc"), "HRRR target_base_utc")
    if _timestamp(summary.get("target_base_utc"), "HRRR summary target_base_utc") != target_base or abs((target_base - generated).total_seconds()) > 3600:
        raise ProductError("HRRR target base is invalid or incoherent")
    leads = manifest.get("supported_browser_lead_hours")
    browser = manifest.get("browser_selection")
    if leads != list(SUPPORTED_LEADS) or summary.get("supported_browser_lead_hours") != leads or not isinstance(browser, dict) or browser.get("supported_lead_hours") != leads:
        raise ProductError("HRRR supported browser leads are invalid or incoherent")
    for key, expected in (
        ("retain_past_hours", RETAIN_PAST_HOURS),
        ("retain_future_hours", RETAIN_FUTURE_HOURS),
        ("stale_after_hours", STALE_AFTER_HOURS),
    ):
        manifest_value = _finite_number(manifest.get(key), f"HRRR {key}")
        if manifest_value != expected or summary.get(key) != manifest.get(key):
            raise ProductError(f"HRRR fixed files disagree on {key}")
    failures = manifest.get("target_failures")
    if not isinstance(failures, list) or not all(isinstance(item, str) for item in failures) or summary.get("target_failures") != failures:
        raise ProductError("HRRR target failures are invalid or incoherent")
    expected_output_files = {
        "wind_json": LATEST_PATH.as_posix(),
        "summary_json": SUMMARY_PATH.as_posix(),
        "manifest_json": MANIFEST_PATH.as_posix(),
        "time_set_directory": TARGET_ROOT.as_posix(),
    }
    if summary.get("output_files") != expected_output_files:
        raise ProductError("HRRR summary output-file inventory is invalid")
    legacy = manifest.get("legacy_latest")
    if not isinstance(legacy, dict) or legacy.get("wind_json") != LATEST_PATH.as_posix() or legacy.get("summary_json") != SUMMARY_PATH.as_posix():
        raise ProductError("HRRR manifest legacy fixed-file paths are invalid")
    values = manifest.get("entries")
    if not isinstance(values, list) or not values or len(values) > MAX_MANIFEST_ENTRIES:
        raise ProductError("HRRR manifest entries must be a bounded nonempty array")
    entries = tuple(_validate_entry(root, value, index) for index, value in enumerate(values))
    if manifest.get("entry_count") != len(entries) or summary.get("available_entry_count") != len(entries):
        raise ProductError("HRRR entry counts are incoherent")
    if [entry.valid for entry in entries] != sorted(entry.valid for entry in entries) or len({entry.valid for entry in entries}) != len(entries):
        raise ProductError("HRRR entries are unsorted or have duplicate valid times")
    lower = generated - timedelta(hours=RETAIN_PAST_HOURS)
    upper = generated + timedelta(hours=RETAIN_FUTURE_HOURS)
    if any(not lower <= entry.valid <= upper for entry in entries):
        raise ProductError("HRRR entry lies outside the configured rolling valid-time window")
    referenced, actual = {entry.relative_path for entry in entries}, _inventory(root)
    if actual != referenced:
        missing = sorted(str(path) for path in referenced - actual)
        orphan = sorted(str(path) for path in actual - referenced)
        raise ProductError(f"HRRR manifest-target closure failed; missing={missing}; orphan={orphan}")
    selected_value = legacy.get("selected_entry")
    matches = [entry for entry in entries if entry.entry == selected_value]
    if len(matches) != 1:
        raise ProductError("HRRR legacy selected entry is not exactly one manifest entry")
    selected = matches[0]
    if selected is not _select_entry(entries, generated):
        raise ProductError("HRRR legacy selected entry is not closest to build time")
    if summary.get("selected_entry") != selected.entry:
        raise ProductError("HRRR summary selected entry disagrees with manifest")
    for key in ("model_cycle_utc", "forecast_hour", "forecast_hour_label", "valid_time_utc"):
        if summary.get(key) != selected.entry.get(key):
            raise ProductError(f"HRRR summary {key} disagrees with selected entry")
    lag_minutes = (generated - selected.valid).total_seconds() / 60.0
    _validate_summary_freshness(summary, lag_minutes)
    for key in (
        "source", "domain", "grid", "earth_relative_winds_confirmed",
        "vector_regrid_method", "speed_ms", "speed_mph",
        "recommended_velocity_scale_ms", "recommended_velocity_scale_mph",
        "velocity_scale_reference",
    ):
        if summary.get(key) != selected.entry.get(key):
            raise ProductError(f"HRRR summary {key} disagrees with selected entry")
    selected_path = root / selected.relative_path
    if (root / LATEST_PATH).read_bytes() != selected_path.read_bytes():
        raise ProductError("HRRR legacy latest bytes disagree with selected target")
    if summary.get("output_json_bytes") != selected_path.stat().st_size:
        raise ProductError("HRRR summary output bytes disagree with selected target")
    build_results = summary.get("target_build_results")
    if not isinstance(build_results, list) or [item.get("lead_hours") if isinstance(item, dict) else None for item in build_results] != list(SUPPORTED_LEADS):
        raise ProductError("HRRR target-build results are invalid")
    for item in build_results:
        distance = _finite_number(item.get("nearest_distance_hours"), "HRRR target-build distance")
        if distance < 0 or item.get("available_within_2_hours") is not (distance <= 2):
            raise ProductError("HRRR target-build availability is invalid")
    return ProductState(root, manifest, summary, generated, target_base, entries, selected)


def semantic_key(root: Path) -> str:
    state = validate_product(root)
    digest = hashlib.sha256()
    paths = (*FIXED_PATHS, *(entry.relative_path for entry in state.entries))
    for path in paths:
        digest.update(path.as_posix().encode("utf-8") + b"\0")
        digest.update(_sha256(state.root / path).encode("ascii") + b"\n")
    return digest.hexdigest()


def _same_complete_state(left: ProductState, right: ProductState) -> bool:
    left_paths = {*FIXED_PATHS, *(entry.relative_path for entry in left.entries)}
    right_paths = {*FIXED_PATHS, *(entry.relative_path for entry in right.entries)}
    return left_paths == right_paths and all(
        (left.root / path).read_bytes() == (right.root / path).read_bytes()
        for path in left_paths
    )


def _window_entries(state: ProductState, generated: datetime) -> dict[datetime, EntryState]:
    lower = generated - timedelta(hours=RETAIN_PAST_HOURS)
    upper = generated + timedelta(hours=RETAIN_FUTURE_HOURS)
    return {entry.valid: entry for entry in state.entries if lower <= entry.valid <= upper}


def _coverage(entries: Sequence[EntryState], target_base: datetime) -> dict[int, float]:
    return {
        lead: min(abs((entry.valid - (target_base + timedelta(hours=lead))).total_seconds()) / 3600.0 for entry in entries)
        for lead in SUPPORTED_LEADS
    }


def _choose_entries(candidate: ProductState, canonical: ProductState) -> list[tuple[EntryState, Path]]:
    proposed = _window_entries(candidate, candidate.generated)
    current = _window_entries(canonical, candidate.generated)
    chosen: list[tuple[EntryState, Path]] = []
    for valid in sorted(set(proposed) | set(current)):
        candidate_entry, canonical_entry = proposed.get(valid), current.get(valid)
        if candidate_entry is None:
            assert canonical_entry is not None
            chosen.append((canonical_entry, canonical.root))
        elif canonical_entry is None or candidate_entry.cycle > canonical_entry.cycle:
            chosen.append((candidate_entry, candidate.root))
        elif candidate_entry.cycle < canonical_entry.cycle:
            chosen.append((canonical_entry, canonical.root))
        else:
            if candidate_entry.forecast_hour != canonical_entry.forecast_hour:
                raise ProductError("same-cycle HRRR entries disagree on forecast-hour identity")
            if candidate_entry.sha256 != canonical_entry.sha256:
                raise ProductError("same-cycle HRRR target identity has conflicting bytes")
            chosen.append((candidate_entry, candidate.root))
    if not chosen:
        raise ProductError("fresh-main HRRR reconciliation produced an empty target set")
    if len(chosen) > MAX_MANIFEST_ENTRIES:
        raise ProductError("fresh-main HRRR reconciliation exceeds the target-count limit")
    chosen_states = [state for state, _ in chosen]
    final_coverage = _coverage(chosen_states, candidate.target_base)
    for source in (list(proposed.values()), list(current.values())):
        if not source:
            continue
        source_coverage = _coverage(source, candidate.target_base)
        if any(final_coverage[lead] > source_coverage[lead] + 1e-9 for lead in SUPPORTED_LEADS):
            raise ProductError("fresh-main HRRR reconciliation regressed target coverage")
    return chosen


def _rebuild_fixed_files(
    candidate: ProductState,
    chosen: Sequence[tuple[EntryState, Path]],
) -> tuple[Dict[str, object], Dict[str, object], EntryState, Path]:
    manifest = copy.deepcopy(candidate.manifest)
    entries = [copy.deepcopy(state.entry) for state, _ in chosen]
    manifest["entries"] = entries
    manifest["entry_count"] = len(entries)
    selected_state = _select_entry([state for state, _ in chosen], candidate.generated)
    selected_index = next(index for index, (state, _) in enumerate(chosen) if state is selected_state)
    selected_entry = entries[selected_index]
    legacy = manifest.get("legacy_latest")
    if not isinstance(legacy, dict):
        raise ProductError("HRRR manifest legacy_latest is invalid")
    legacy["selected_entry"] = copy.deepcopy(selected_entry)

    summary = copy.deepcopy(candidate.summary)
    summary["selected_entry"] = copy.deepcopy(selected_entry)
    summary["available_entry_count"] = len(entries)
    for key in (
        "source", "model_cycle_utc", "model_cycle_local", "forecast_hour",
        "forecast_hour_label", "valid_time_utc", "valid_time_local", "domain",
        "grid", "earth_relative_winds_confirmed", "vector_regrid_method",
        "speed_ms", "speed_mph", "recommended_velocity_scale_ms",
        "recommended_velocity_scale_mph", "velocity_scale_reference",
    ):
        summary[key] = copy.deepcopy(selected_entry[key])
    lag_minutes = (candidate.generated - selected_state.valid).total_seconds() / 60.0
    summary["valid_lag_minutes_at_build"] = round(lag_minutes, 6)
    summary["valid_time_age_hours"] = round(lag_minutes / 60.0, 6)
    summary["is_stale"] = summary["valid_time_age_hours"] > STALE_AFTER_HOURS
    summary["target_build_results"] = [
        {
            "lead_hours": lead,
            "nearest_distance_hours": round(distance, 6),
            "available_within_2_hours": distance <= 2,
        }
        for lead, distance in _coverage([state for state, _ in chosen], candidate.generated).items()
    ]
    selected_root = chosen[selected_index][1]
    selected_path = selected_root / selected_state.relative_path
    summary["output_json_bytes"] = selected_path.stat().st_size
    return manifest, summary, selected_state, selected_root


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _apply_complete_candidate(candidate: ProductState, worktree: Path) -> None:
    fixed_bytes = {path: (candidate.root / path).read_bytes() for path in FIXED_PATHS}
    target_bytes = {entry.relative_path: (candidate.root / entry.relative_path).read_bytes() for entry in candidate.entries}
    shutil.rmtree(worktree / TARGET_ROOT, ignore_errors=True)
    for path, content in {**fixed_bytes, **target_bytes}.items():
        destination = worktree / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


def _apply_desired_state(
    worktree: Path,
    manifest: Mapping[str, object],
    summary: Mapping[str, object],
    chosen: Sequence[tuple[EntryState, Path]],
    selected: EntryState,
    selected_root: Path,
) -> None:
    target_bytes = {state.relative_path: (source_root / state.relative_path).read_bytes() for state, source_root in chosen}
    selected_bytes = (selected_root / selected.relative_path).read_bytes()
    shutil.rmtree(worktree / TARGET_ROOT, ignore_errors=True)
    (worktree / TARGET_ROOT).mkdir(parents=True, exist_ok=True)
    for path, content in target_bytes.items():
        destination = worktree / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    _write_json(worktree / MANIFEST_PATH, manifest)
    _write_json(worktree / SUMMARY_PATH, summary)
    (worktree / LATEST_PATH).write_bytes(selected_bytes)


def reconcile(candidate_root: Path, canonical_root: Path) -> tuple[str, str]:
    candidate = validate_product(candidate_root)
    if not (canonical_root / MANIFEST_PATH).exists():
        _apply_complete_candidate(candidate, canonical_root)
        return "new", "canonical HRRR product was absent"
    canonical = validate_product(canonical_root)
    if _same_complete_state(candidate, canonical):
        return "same", "complete HRRR desired state is already canonical"
    if candidate.generated < canonical.generated:
        return "stale", "candidate generation time is older than fresh canonical HRRR"
    if candidate.generated == canonical.generated:
        raise ProductError("equal-generation HRRR states have conflicting complete bytes")
    chosen = _choose_entries(candidate, canonical)
    if all(source_root == candidate.root for _, source_root in chosen) and len(chosen) == len(candidate.entries):
        _apply_complete_candidate(candidate, canonical_root)
        validate_product(canonical_root)
        return "new", "newer candidate controls the complete HRRR desired window"
    manifest, summary, selected, selected_root = _rebuild_fixed_files(candidate, chosen)
    _apply_desired_state(canonical_root, manifest, summary, chosen, selected, selected_root)
    validate_product(canonical_root)
    return "new", "newer HRRR window reconciled with fresh canonical cycle dominance and coverage preservation"


def _callback() -> int:
    phase = os.environ.get("BRIM_PUBLISH_PHASE")
    candidate_root = Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"])
    worktree = Path(os.environ["BRIM_PUBLISH_WORKTREE"])
    if os.environ.get("BRIM_PUBLISH_PRODUCT_ID") != PRODUCT_ID:
        raise ProductError("publisher product ID is not the HRRR product")
    if phase == "validate-candidate":
        metadata = _read_json(Path(os.environ["BRIM_PUBLISH_METADATA"]))
        if not isinstance(metadata, dict):
            raise ProductError("candidate metadata root is invalid")
        expected = {"type": "hrrr_rolling_state_sha256_v1", "value": semantic_key(candidate_root)}
        if metadata.get("semantic_key") != expected:
            raise ProductError("candidate HRRR semantic key disagrees with product bytes")
        return 0
    if phase == "validate-staged":
        validate_product(worktree)
        return 0
    if phase == "reconcile":
        state, reason = reconcile(candidate_root, worktree)
        decision = "publish" if state == "new" else "no-op"
        Path(os.environ["BRIM_PUBLISH_RESULT"]).write_text(
            json.dumps({"decision": decision, "candidate_state": state, "reason": reason}) + "\n",
            encoding="utf-8",
        )
        return 0
    raise ProductError(f"unsupported HRRR publisher phase: {phase!r}")


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
            print(f"Validated HRRR rolling product: {len(state.entries)} targets")
        else:
            print(semantic_key(Path(args.root)))
        return 0
    except ProductError as exc:
        print(f"HRRR_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
