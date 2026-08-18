#!/usr/bin/env python3
"""Validate and reconcile the rolling NOAA GFS surface-wind product tree."""

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


PRODUCT_ID = "gfs-wind-latest"
DATA_PRODUCT_ID = "gfs_surface_wind"
MANIFEST_PATH = Path("docs/data/wind/gfs_surface_wind_feed_manifest.json")
LATEST_PATH = Path("docs/data/wind/gfs_surface_wind_latest.json")
SUMMARY_PATH = Path("docs/data/wind/gfs_surface_wind_latest_summary.json")
TARGET_ROOT = Path("docs/data/wind/gfs/surface")
FIXED_PATHS = (LATEST_PATH, SUMMARY_PATH, MANIFEST_PATH)
TARGET_PATTERN = re.compile(r"gfs_surface_wind_(\d{8})T(\d{2})Z_f(\d{3})\.json")
TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
GRID_KEYS = ("nx", "ny", "lo1", "la1", "lo2", "la2", "dx", "dy", "refTime", "forecastTime")


class ProductError(RuntimeError):
    """A malformed or ambiguous GFS rolling-product state."""


@dataclass(frozen=True)
class EntryState:
    entry: Dict[str, object]
    valid: datetime
    cycle: datetime
    forecast_hour: int
    relative_path: Path
    sha256: str


@dataclass(frozen=True)
class ProductState:
    root: Path
    manifest: Dict[str, object]
    summary: Dict[str, object]
    generated: datetime
    entries: tuple[EntryState, ...]
    selected: EntryState


def _read_json(path: Path) -> object:
    if not path.is_file() or path.is_symlink():
        raise ProductError(f"missing plain GFS product file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductError(f"unreadable GFS JSON {path}: {exc}") from exc


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_target(path: Path, *, cycle: datetime, forecast_hour: int) -> Mapping[str, object]:
    value = _read_json(path)
    if not isinstance(value, list) or len(value) != 2:
        raise ProductError(f"GFS target must contain exactly U/V fields: {path}")
    headers: list[Mapping[str, object]] = []
    parameters: set[int] = set()
    for index, field in enumerate(value):
        if not isinstance(field, dict) or set(field) != {"header", "data"}:
            raise ProductError(f"GFS target field {index} has an invalid shape: {path}")
        header = field["header"]
        data = field["data"]
        if not isinstance(header, dict) or not isinstance(data, list):
            raise ProductError(f"GFS target field {index} header/data is invalid: {path}")
        if header.get("parameterCategory") != 2 or header.get("parameterNumber") not in {2, 3}:
            raise ProductError(f"GFS target field {index} is not a U/V wind component")
        parameters.add(int(header["parameterNumber"]))
        nx, ny = header.get("nx"), header.get("ny")
        if not isinstance(nx, int) or not isinstance(ny, int) or nx <= 0 or ny <= 0:
            raise ProductError(f"GFS target grid dimensions are invalid: {path}")
        if len(data) != nx * ny:
            raise ProductError(f"GFS target data length does not equal nx * ny: {path}")
        for data_index, item in enumerate(data):
            _finite_number(item, f"GFS target data value {data_index}")
        if _timestamp(header.get("refTime"), "GFS target refTime") != cycle:
            raise ProductError(f"GFS target refTime disagrees with manifest: {path}")
        if header.get("forecastTime") != forecast_hour:
            raise ProductError(f"GFS target forecastTime disagrees with manifest: {path}")
        for key in ("lo1", "la1", "lo2", "la2", "dx", "dy"):
            _finite_number(header.get(key), f"GFS target header {key}")
        headers.append(header)
    if parameters != {2, 3}:
        raise ProductError(f"GFS target must contain one U and one V component: {path}")
    for key in GRID_KEYS:
        if headers[1].get(key) != headers[0].get(key):
            raise ProductError(f"GFS target U/V grids disagree for {key}: {path}")
    return headers[0]


def _target_relative_path(entry: Mapping[str, object], *, cycle: datetime, forecast_hour: int) -> Path:
    filename = entry.get("filename")
    relative_url = entry.get("relative_url")
    if not isinstance(filename, str) or not TARGET_PATTERN.fullmatch(filename):
        raise ProductError("GFS entry filename is invalid")
    expected = f"gfs_surface_wind_{cycle:%Y%m%dT%H}Z_f{forecast_hour:03d}.json"
    if filename != expected or relative_url != f"gfs/surface/{filename}":
        raise ProductError("GFS entry filename/relative_url disagrees with cycle and lead")
    return TARGET_ROOT / filename


def _validate_entry(root: Path, value: object, index: int) -> EntryState:
    if not isinstance(value, dict) or value.get("product_id") != DATA_PRODUCT_ID:
        raise ProductError(f"GFS manifest entry {index} identity is invalid")
    if value.get("variables") != ["UGRD", "VGRD"]:
        raise ProductError(f"GFS manifest entry {index} variables are invalid")
    cycle = _timestamp(value.get("model_cycle_utc"), f"GFS entry {index} model cycle")
    valid = _timestamp(value.get("valid_time_utc"), f"GFS entry {index} valid time")
    forecast_hour = value.get("forecast_hour")
    if not isinstance(forecast_hour, int) or isinstance(forecast_hour, bool) or not 0 <= forecast_hour <= 384:
        raise ProductError(f"GFS entry {index} forecast_hour is invalid")
    if valid != cycle + timedelta(hours=forecast_hour):
        raise ProductError(f"GFS entry {index} valid time is incoherent")
    if value.get("forecast_hour_label") != f"f{forecast_hour:03d}":
        raise ProductError(f"GFS entry {index} forecast label is incoherent")
    relative_path = _target_relative_path(value, cycle=cycle, forecast_hour=forecast_hour)
    target_path = root / relative_path
    if not target_path.is_file() or target_path.is_symlink():
        raise ProductError(f"GFS manifest references a missing target: {relative_path}")
    if value.get("file_bytes") != target_path.stat().st_size:
        raise ProductError(f"GFS manifest target byte count is wrong: {relative_path}")
    if value.get("status") not in {"downloaded", "reused_existing"}:
        raise ProductError(f"GFS entry {index} status is invalid")
    for unit in ("ms", "mph"):
        speeds = value.get(f"speed_{unit}")
        if not isinstance(speeds, dict):
            raise ProductError(f"GFS entry {index} speed_{unit} is invalid")
        for key in ("min", "p50", "p75", "p90", "p95", "max", "median"):
            _finite_number(speeds.get(key), f"GFS entry {index} speed_{unit}.{key}")
        for key in ("min", "p50", "p75", "p90", "p95", "max"):
            _finite_number(
                value.get(f"speed_{unit}_{key}"),
                f"GFS entry {index} speed_{unit}_{key}",
            )
    _finite_number(
        value.get("recommended_velocity_scale_ms"),
        f"GFS entry {index} recommended velocity scale",
    )
    _finite_number(
        value.get("recommended_velocity_scale_mph"),
        f"GFS entry {index} recommended velocity scale mph",
    )
    if not isinstance(value.get("velocity_scale_reference"), str):
        raise ProductError(f"GFS entry {index} velocity scale reference is invalid")
    _validate_target(target_path, cycle=cycle, forecast_hour=forecast_hour)
    return EntryState(value, valid, cycle, forecast_hour, relative_path, _sha256(target_path))


def _inventory(root: Path) -> set[Path]:
    target_root = root / TARGET_ROOT
    if not target_root.is_dir() or target_root.is_symlink():
        raise ProductError(f"missing plain GFS target directory: {target_root}")
    paths: set[Path] = set()
    for path in target_root.rglob("*"):
        if path.is_symlink():
            raise ProductError(f"GFS target tree contains a symlink: {path}")
        if path.is_file():
            paths.add(path.relative_to(root))
    return paths


def validate_product(root: Path) -> ProductState:
    root = root.resolve()
    manifest, summary = _read_json(root / MANIFEST_PATH), _read_json(root / SUMMARY_PATH)
    if not isinstance(manifest, dict) or not isinstance(summary, dict):
        raise ProductError("GFS manifest and summary roots must be objects")
    if manifest.get("product_id") != DATA_PRODUCT_ID or manifest.get("feed_mode") != "time_set":
        raise ProductError("GFS manifest identity is invalid")
    if summary.get("product_id") != DATA_PRODUCT_ID or summary.get("feed_mode") != "time_set_with_legacy_latest" or summary.get("status") != "success":
        raise ProductError("GFS summary identity/status is invalid")
    if manifest.get("version") != summary.get("version"):
        raise ProductError("GFS manifest/summary versions disagree")
    generated = _timestamp(manifest.get("generated_utc"), "GFS generated_utc")
    if _timestamp(summary.get("build_time_utc"), "GFS build_time_utc") != generated:
        raise ProductError("GFS manifest/summary build times disagree")
    for key in ("target_past_hours", "target_future_hours", "max_forecast_hour"):
        if not isinstance(manifest.get(key), int) or manifest[key] < 0 or summary.get(key) != manifest.get(key):
            raise ProductError(f"GFS fixed files disagree on {key}")
    offsets = manifest.get("target_offsets_hours")
    if not isinstance(offsets, list) or not offsets or not all(isinstance(item, int) and not isinstance(item, bool) for item in offsets) or summary.get("target_offsets_hours") != offsets:
        raise ProductError("GFS target offset policy is invalid")
    if manifest["target_past_hours"] != abs(min(0, *offsets)) or manifest["target_future_hours"] != max(0, *offsets):
        raise ProductError("GFS target window does not match configured offsets")
    stale_after = _finite_number(manifest.get("stale_after_hours"), "GFS stale_after_hours")
    if stale_after <= 0 or summary.get("stale_after_hours") != manifest.get("stale_after_hours"):
        raise ProductError("GFS stale threshold is invalid or incoherent")
    expected_output_files = {
        "wind_json": LATEST_PATH.as_posix(),
        "summary_json": SUMMARY_PATH.as_posix(),
        "manifest_json": MANIFEST_PATH.as_posix(),
        "time_set_directory": TARGET_ROOT.as_posix(),
    }
    if summary.get("output_files") != expected_output_files:
        raise ProductError("GFS summary output-file inventory is invalid")
    legacy = manifest.get("legacy_latest")
    if not isinstance(legacy, dict) or legacy.get("wind_json") != LATEST_PATH.as_posix() or legacy.get("summary_json") != SUMMARY_PATH.as_posix():
        raise ProductError("GFS manifest legacy fixed-file paths are invalid")
    values = manifest.get("entries")
    if not isinstance(values, list) or not values:
        raise ProductError("GFS manifest entries must be a nonempty array")
    entries = tuple(_validate_entry(root, value, index) for index, value in enumerate(values))
    if manifest.get("entry_count") != len(entries) or summary.get("available_entry_count") != len(entries):
        raise ProductError("GFS entry counts are incoherent")
    if [entry.valid for entry in entries] != sorted(entry.valid for entry in entries) or len({entry.valid for entry in entries}) != len(entries):
        raise ProductError("GFS entries are unsorted or have duplicate valid times")
    expected_valid_times = {
        generated.replace(minute=0, second=0, microsecond=0) + timedelta(hours=offset)
        for offset in offsets
    }
    if any(entry.valid not in expected_valid_times for entry in entries):
        raise ProductError("GFS entry lies outside the configured rolling valid-time set")
    if any(entry.forecast_hour > manifest["max_forecast_hour"] for entry in entries):
        raise ProductError("GFS entry exceeds max_forecast_hour")
    referenced, actual = {entry.relative_path for entry in entries}, _inventory(root)
    if actual != referenced:
        missing = sorted(str(path) for path in referenced - actual)
        orphan = sorted(str(path) for path in actual - referenced)
        raise ProductError(f"GFS manifest-target closure failed; missing={missing}; orphan={orphan}")
    selected_value = legacy.get("selected_entry") if isinstance(legacy, dict) else None
    matches = [entry for entry in entries if entry.entry == selected_value]
    if len(matches) != 1:
        raise ProductError("GFS legacy selected entry is not exactly one manifest entry")
    selected = matches[0]
    if summary.get("selected_entry") != selected.entry:
        raise ProductError("GFS summary selected entry disagrees with manifest")
    for key in ("model_cycle_utc", "forecast_hour", "forecast_hour_label", "valid_time_utc"):
        if summary.get(key) != selected.entry.get(key):
            raise ProductError(f"GFS summary {key} disagrees with selected entry")
    selected_path = root / selected.relative_path
    if (root / LATEST_PATH).read_bytes() != selected_path.read_bytes():
        raise ProductError("GFS legacy latest bytes disagree with selected target")
    if summary.get("output_json_bytes") != selected_path.stat().st_size:
        raise ProductError("GFS summary output bytes disagree with selected target")
    return ProductState(root, manifest, summary, generated, entries, selected)


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


def _window_bounds(state: ProductState) -> tuple[datetime, datetime]:
    anchor = state.generated.replace(minute=0, second=0, microsecond=0)
    return (
        anchor - timedelta(hours=int(state.manifest["target_past_hours"])),
        anchor + timedelta(hours=int(state.manifest["target_future_hours"])),
    )


def _choose_entries(
    candidate: ProductState, canonical: ProductState
) -> list[tuple[EntryState, Path]]:
    start, end = _window_bounds(candidate)
    proposed = {entry.valid: entry for entry in candidate.entries}
    current = {
        entry.valid: entry for entry in canonical.entries if start <= entry.valid <= end
    }
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
                raise ProductError("same-cycle GFS entries disagree on forecast-hour identity")
            if candidate_entry.sha256 != canonical_entry.sha256:
                raise ProductError("same-cycle GFS target identity has conflicting bytes")
            chosen.append((candidate_entry, candidate.root))
    if not chosen:
        raise ProductError("fresh-main GFS reconciliation produced an empty target set")
    return chosen


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


def _rebuild_fixed_files(
    candidate: ProductState,
    chosen: Sequence[tuple[EntryState, Path]],
) -> tuple[Dict[str, object], Dict[str, object], EntryState, Path]:
    manifest = copy.deepcopy(candidate.manifest)
    entries = [copy.deepcopy(state.entry) for state, _ in chosen]
    manifest["entries"] = entries
    manifest["entry_count"] = len(entries)
    selected_state = _select_entry([state for state, _ in chosen], candidate.generated)
    selected_index = next(
        index for index, (state, _) in enumerate(chosen) if state is selected_state
    )
    selected_entry = entries[selected_index]
    legacy = manifest.get("legacy_latest")
    if not isinstance(legacy, dict):
        raise ProductError("GFS manifest legacy_latest is invalid")
    legacy["selected_entry"] = copy.deepcopy(selected_entry)

    summary = copy.deepcopy(candidate.summary)
    summary["selected_entry"] = copy.deepcopy(selected_entry)
    summary["available_entry_count"] = len(entries)
    for key in (
        "model_cycle_utc", "model_cycle_local", "forecast_hour",
        "forecast_hour_label", "valid_time_utc", "valid_time_local",
    ):
        summary[key] = selected_entry[key]
    lag_minutes = (candidate.generated - selected_state.valid).total_seconds() / 60.0
    summary["valid_lag_minutes_at_build"] = round(lag_minutes, 6)
    summary["valid_time_age_hours"] = round(lag_minutes / 60.0, 6)
    summary["model_cycle_age_hours"] = round(
        (candidate.generated - selected_state.cycle).total_seconds() / 3600.0, 6
    )
    summary["is_stale"] = summary["valid_time_age_hours"] > float(summary["stale_after_hours"])
    for key in (
        "speed_ms", "speed_mph", "recommended_velocity_scale_ms",
        "recommended_velocity_scale_mph", "velocity_scale_reference",
        "speed_ms_min", "speed_ms_p50", "speed_ms_p75", "speed_ms_p90",
        "speed_ms_p95", "speed_ms_max", "speed_mph_min", "speed_mph_p50",
        "speed_mph_p75", "speed_mph_p90", "speed_mph_p95", "speed_mph_max",
    ):
        summary[key] = copy.deepcopy(selected_entry[key])
    selected_root = chosen[selected_index][1]
    selected_path = selected_root / selected_state.relative_path
    summary["output_json_bytes"] = selected_path.stat().st_size
    target = _read_json(selected_path)
    header = target[0]["header"]
    summary["grid"] = {key: header[key] for key in ("nx", "ny", "dx", "dy")}
    summary["domain"] = {
        "west": header["lo1"] - header["dx"] / 2,
        "east": header["lo2"] + header["dx"] / 2,
        "south": min(header["la1"], header["la2"]) - header["dy"] / 2,
        "north": max(header["la1"], header["la2"]) + header["dy"] / 2,
    }
    return manifest, summary, selected_state, selected_root


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _apply_complete_candidate(candidate: ProductState, worktree: Path) -> None:
    fixed_bytes = {path: (candidate.root / path).read_bytes() for path in FIXED_PATHS}
    target_bytes = {
        entry.relative_path: (candidate.root / entry.relative_path).read_bytes()
        for entry in candidate.entries
    }
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
    target_bytes = {
        state.relative_path: (source_root / state.relative_path).read_bytes()
        for state, source_root in chosen
    }
    selected_bytes = (selected_root / selected.relative_path).read_bytes()
    target_root = worktree / TARGET_ROOT
    shutil.rmtree(target_root, ignore_errors=True)
    target_root.mkdir(parents=True, exist_ok=True)
    for state, _ in chosen:
        destination = worktree / state.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(target_bytes[state.relative_path])
    _write_json(worktree / MANIFEST_PATH, manifest)
    _write_json(worktree / SUMMARY_PATH, summary)
    (worktree / LATEST_PATH).write_bytes(selected_bytes)


def reconcile(candidate_root: Path, canonical_root: Path) -> tuple[str, str]:
    candidate = validate_product(candidate_root)
    if not (canonical_root / MANIFEST_PATH).exists():
        _apply_complete_candidate(candidate, canonical_root)
        return "new", "canonical GFS product was absent"
    canonical = validate_product(canonical_root)
    if _same_complete_state(candidate, canonical):
        return "same", "complete GFS desired state is already canonical"
    if candidate.generated < canonical.generated:
        return "stale", "candidate generation time is older than fresh canonical GFS"
    if candidate.generated == canonical.generated:
        raise ProductError("equal-generation GFS states have conflicting complete bytes")
    chosen = _choose_entries(candidate, canonical)
    if [state for state, source_root in chosen if source_root == candidate.root] == list(candidate.entries) and len(chosen) == len(candidate.entries):
        _apply_complete_candidate(candidate, canonical_root)
        validate_product(canonical_root)
        return "new", "newer complete candidate controls the rolling window"
    manifest, summary, selected, selected_root = _rebuild_fixed_files(candidate, chosen)
    _apply_desired_state(canonical_root, manifest, summary, chosen, selected, selected_root)
    validate_product(canonical_root)
    return "new", "newer rolling window reconciled with fresh canonical target dominance"


def _callback() -> int:
    phase = os.environ.get("BRIM_PUBLISH_PHASE")
    candidate_root = Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"])
    worktree = Path(os.environ["BRIM_PUBLISH_WORKTREE"])
    if os.environ.get("BRIM_PUBLISH_PRODUCT_ID") != PRODUCT_ID:
        raise ProductError("publisher product ID is not the GFS product")
    if phase == "validate-candidate":
        metadata = _read_json(Path(os.environ["BRIM_PUBLISH_METADATA"]))
        if not isinstance(metadata, dict):
            raise ProductError("candidate metadata root is invalid")
        expected = {
            "type": "gfs_rolling_state_sha256_v1",
            "value": semantic_key(candidate_root),
        }
        if metadata.get("semantic_key") != expected:
            raise ProductError("candidate GFS semantic key disagrees with product bytes")
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
    raise ProductError(f"unsupported GFS publisher phase: {phase!r}")


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
            print(f"Validated GFS rolling product: {len(state.entries)} targets")
        else:
            print(semantic_key(Path(args.root)))
        return 0
    except ProductError as exc:
        print(f"GFS_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
