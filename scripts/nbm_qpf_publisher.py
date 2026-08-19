#!/usr/bin/env python3
"""Validate and reconcile the bounded public NBM QPF product tree.

Raster science remains in the R producer.  This module only validates the
producer candidate, constructs the public manifest, reconciles current and
previous complete cycles, and implements the shared-publisher callback.
"""

from __future__ import annotations

import argparse
import array
import copy
import csv
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Mapping, Sequence


PRODUCT_ID = "nbm_qpf"
SOURCE_ID = "noaa_nbm_core_conus_apcp"
MANIFEST_PATH = Path("docs/data/nbm-qpf/nbm_qpf_manifest.json")
TARGET_ROOT = Path("docs/data/nbm-qpf/nbm/qpf")
FIXED_PATHS = (MANIFEST_PATH,)
OWNED_ROOTS = (TARGET_ROOT,)
R_CANDIDATE_MANIFEST = Path("nbm_qpf_candidate.json")
R_VALIDATION = Path("validation.json")
R_PREFLIGHT = Path("preflight.csv")
SCHEMA_VERSION = "1.0.0"
SEMANTIC_KEY_TYPE = "nbm_qpf_cycle_state_sha256_v1"
SUPPORTED_LEADS = tuple(range(6, 241, 6))
LEGACY_SUPPORTED_LEADS = (6, 12, 18, 24, 30, 36, 42, 48, 60, 72)
ACCUMULATION_HOURS = 6
PALETTE_ID = "brim_nbm_qpf_6h_west_v1"
PALETTE_VERSION = 1
IMAGE_WIDTH = 720
IMAGE_HEIGHT = 733
GRID_CONTRACT_ID = "nbm_qpf_lossless_webp_v1"
NUMERIC_ENCODING = "uint16_le"
NUMERIC_COMPRESSION = "gzip"
NUMERIC_SCALE = 0.001
NUMERIC_OFFSET = 0
NUMERIC_NODATA = 65535
NUMERIC_VALID_MAX = 65534
NUMERIC_UNCOMPRESSED_BYTES = IMAGE_WIDTH * IMAGE_HEIGHT * 2
FORECAST_STATE_BINDING = "nbm_qpf_forecast_state_sha256_v1"
BOUNDS_WGS84 = [-130.0, 30.0, -112.0, 44.5]
EXTENT_3857 = [
    -14471533.803125564,
    3503549.843504374,
    -12467782.96884664,
    5543147.203861799,
]
PIXEL_SIZE_M = [2782.9872698322874, 2782.533915903717]
MANIFEST_MAX_BYTES = 512 * 1024
TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
TARGET_PATTERN = re.compile(
    r"nbm_qpf_(\d{8}T\d{6}Z)_f(\d{3})_([0-9a-f]{12})\.webp"
)
NUMERIC_PATTERN = re.compile(
    r"nbm_qpf_(\d{8}T\d{6}Z)_f(\d{3})_([0-9a-f]{12})\.u16le\.gz"
)

SOURCE_CONTRACT = {
    "source_id": SOURCE_ID,
    "agency": "NOAA/NWS/NCEP/MDL",
    "dataset": "National Blend of Models",
    "family": "core",
    "domain": "conus",
    "parameter": "APCP",
    "level": "surface",
    "field_kind": "deterministic accumulated precipitation",
    "native_units": "kg/m^2",
    "normalized_units": "mm",
    "display_units": "in",
}

FRESHNESS_CONTRACT = {
    "basis": "source_cycle_age",
    "current_through_hours": 9,
    "delayed_through_hours": 15,
    "stale_through_hours": 24,
    "expired_after_hours": 24,
    "product_status_independent_from_snow": True,
}

ROOT_FIELDS = {
    "schema_version",
    "product_id",
    "generated_at_utc",
    "source",
    "palette",
    "spatial_representation",
    "numeric_representation",
    "forecast_state_binding",
    "freshness",
    "retention_mode",
    "current_cycle_utc",
    "previous_cycle_utc",
    "cycles",
}
CYCLE_FIELDS = {
    "cycle_utc",
    "cycle_status",
    "cycle_max_qpf_in",
    "legend_cap_in",
    "legend_overflow",
    "target_count",
    "complete_required_leads_hours",
    "targets",
}
TARGET_FIELDS = {
    "product_id",
    "forecast_state_id",
    "source_id",
    "parameter",
    "level",
    "cycle_utc",
    "lead_hours",
    "lead_end_hours",
    "accumulation_start_utc",
    "accumulation_end_utc",
    "valid_time_utc",
    "accumulation_hours",
    "source_parameter",
    "source_level",
    "source_inventory_semantics",
    "native_units",
    "normalized_units",
    "stored_numeric_units",
    "display_units",
    "grid_contract_id",
    "columns",
    "rows",
    "crs",
    "extent_m",
    "row_order",
    "column_order",
    "pixel_is_area",
    "image_path",
    "image_media_type",
    "image_encoding",
    "image_width",
    "image_height",
    "bounds_wgs84",
    "bytes",
    "sha256",
    "image",
    "numeric",
    "palette_id",
    "palette_version",
}
R_CANDIDATE_FIELDS = {
    "candidate_schema_version",
    "candidate_kind",
    "product_id",
    "publication_ready",
    "mutable_public_manifest_included",
    "source",
    "palette",
    "spatial_representation",
    "numeric_representation",
    "forecast_state_binding",
    "cycle",
}
LEGACY_ROOT_FIELDS = ROOT_FIELDS - {"numeric_representation", "forecast_state_binding"}
LEGACY_TARGET_FIELDS = TARGET_FIELDS - {
    "forecast_state_id",
    "parameter",
    "level",
    "stored_numeric_units",
    "grid_contract_id",
    "columns",
    "rows",
    "crs",
    "extent_m",
    "row_order",
    "column_order",
    "pixel_is_area",
    "image",
    "numeric",
}


class ProductError(RuntimeError):
    """A malformed or ambiguous QPF publication state."""


@dataclass(frozen=True)
class TargetState:
    entry: Dict[str, object]
    image_path: Path
    image_sha256: str
    numeric_path: Path
    numeric_sha256: str


@dataclass(frozen=True)
class CycleState:
    entry: Dict[str, object]
    cycle: datetime
    targets: tuple[TargetState, ...]
    signature: str


@dataclass(frozen=True)
class ProductState:
    root: Path
    manifest: Dict[str, object]
    cycles: tuple[CycleState, ...]
    current: CycleState
    previous: CycleState | None


def _read_json(path: Path) -> object:
    if not path.is_file() or path.is_symlink():
        raise ProductError(f"missing plain JSON file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductError(f"unreadable JSON {path}: {exc}") from exc


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not TIMESTAMP_PATTERN.fullmatch(value):
        raise ProductError(f"{label} must be an RFC 3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ProductError(f"{label} is not a valid UTC timestamp") from exc
    return parsed


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _json_signature(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _byte_diagnostics(state: ProductState) -> Dict[str, int]:
    targets = [target for cycle in state.cycles for target in cycle.targets]
    return {
        "retained_cycle_count": len(state.cycles),
        "retained_state_count": len(targets),
        "retained_image_bytes": sum(int(target.entry["bytes"]) for target in targets),
        "retained_numeric_compressed_bytes": sum(
            int(target.entry["numeric"]["compressed_bytes"]) for target in targets
        ),
        "retained_numeric_uncompressed_bytes": sum(
            int(target.entry["numeric"]["uncompressed_bytes"]) for target in targets
        ),
    }


def _ensure_plain_tree(root: Path) -> None:
    if not root.is_dir() or root.is_symlink():
        raise ProductError(f"product root is not a plain directory: {root}")
    for member in root.rglob("*"):
        if member.is_symlink():
            raise ProductError(f"product tree contains a symlink: {member}")


def _ensure_no_symlink_ancestors(root: Path, relative: Path) -> None:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ProductError(f"QPF product path traverses a symlink: {current}")


def _inventory(root: Path) -> set[Path]:
    _ensure_plain_tree(root)
    return {
        member.relative_to(root)
        for member in root.rglob("*")
        if member.is_file()
    }


def _palette_contract() -> Dict[str, object]:
    palette_path = Path(__file__).resolve().parents[1] / "data/input/nbm_qpf_palette.csv"
    if not palette_path.is_file():
        raise ProductError("locked QPF palette CSV is unavailable")
    with palette_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 22 or [int(row["class_index"]) for row in rows] != list(range(22)):
        raise ProductError("locked QPF palette CSV has an invalid class inventory")
    classes = []
    for row in rows[1:]:
        classes.append(
            {
                "lower_inclusive_in": float(row["lower_inclusive_in"]),
                "upper_exclusive_in": (
                    None if row["upper_exclusive_in"] == "" else float(row["upper_exclusive_in"])
                ),
                "color_hex": row["color_hex"].upper(),
                "alpha_u8": int(row["alpha_u8"]),
            }
        )
    return {
        "palette_id": PALETTE_ID,
        "palette_version": PALETTE_VERSION,
        "display_units": "in",
        "class_interval": "lower-inclusive upper-exclusive",
        "below_0_01_in": "transparent",
        "nodata": "transparent",
        "overflow": ">=20 in uses the final fixed class",
        "classes": classes,
    }


def _validate_palette(value: object) -> Dict[str, object]:
    expected = _palette_contract()
    if value != expected:
        raise ProductError("QPF manifest palette differs from the locked palette")
    return copy.deepcopy(expected)


def _numbers_close(actual: object, expected: Sequence[float], label: str) -> None:
    if not isinstance(actual, list) or len(actual) != len(expected):
        raise ProductError(f"{label} has an invalid shape")
    for index, (item, wanted) in enumerate(zip(actual, expected)):
        number = _finite_number(item, f"{label}[{index}]")
        if abs(number - wanted) > 1e-6:
            raise ProductError(f"{label} differs from the locked contract")


def _validate_spatial(value: object) -> Dict[str, object]:
    if not isinstance(value, dict):
        raise ProductError("QPF spatial representation must be an object")
    expected_fields = {
        "contract_id", "media_type", "encoding", "crs", "bounds_wgs84",
        "extent_m", "image_width", "image_height", "pixel_size_m",
        "row_order", "column_order", "pixel_is_area", "leaflet_bounds",
        "default_leaflet_opacity",
    }
    if set(value) != expected_fields:
        raise ProductError("QPF spatial representation has missing or unexpected fields")
    scalar_expected = {
        "contract_id": GRID_CONTRACT_ID,
        "media_type": "image/webp",
        "encoding": "lossless VP8L RGBA8 WebP",
        "crs": "EPSG:3857",
        "image_width": IMAGE_WIDTH,
        "image_height": IMAGE_HEIGHT,
        "row_order": "north_to_south",
        "column_order": "west_to_east",
        "pixel_is_area": True,
        "leaflet_bounds": [[30.0, -130.0], [44.5, -112.0]],
        "default_leaflet_opacity": 0.55,
    }
    for key, expected in scalar_expected.items():
        if value.get(key) != expected:
            raise ProductError(f"QPF spatial representation {key} is invalid")
    _numbers_close(value.get("bounds_wgs84"), BOUNDS_WGS84, "bounds_wgs84")
    _numbers_close(value.get("extent_m"), EXTENT_3857, "extent_m")
    _numbers_close(value.get("pixel_size_m"), PIXEL_SIZE_M, "pixel_size_m")
    return copy.deepcopy(value)


def _numeric_contract() -> Dict[str, object]:
    return {
        "contract_id": "nbm_qpf_uint16_le_gzip_v1",
        "media_type": "application/octet-stream",
        "encoding": NUMERIC_ENCODING,
        "compression": NUMERIC_COMPRESSION,
        "stored_units": "in",
        "scale": NUMERIC_SCALE,
        "offset": NUMERIC_OFFSET,
        "nodata": NUMERIC_NODATA,
        "valid_stored_min": 0,
        "valid_stored_max": NUMERIC_VALID_MAX,
        "represented_min": 0,
        "represented_max": NUMERIC_VALID_MAX * NUMERIC_SCALE,
        "uncompressed_bytes": NUMERIC_UNCOMPRESSED_BYTES,
        "grid_contract_id": GRID_CONTRACT_ID,
        "columns": IMAGE_WIDTH,
        "rows": IMAGE_HEIGHT,
        "crs": "EPSG:3857",
        "bounds_wgs84": copy.deepcopy(BOUNDS_WGS84),
        "extent_m": copy.deepcopy(EXTENT_3857),
        "row_order": "north_to_south",
        "column_order": "west_to_east",
        "pixel_is_area": True,
    }


def _validate_numeric_contract(value: object) -> Dict[str, object]:
    expected = _numeric_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ProductError("QPF numeric representation has missing or unexpected fields")
    for key, wanted in expected.items():
        if key in {"bounds_wgs84", "extent_m"}:
            continue
        if value.get(key) != wanted:
            raise ProductError(f"QPF numeric representation {key} is invalid")
    _numbers_close(value.get("bounds_wgs84"), BOUNDS_WGS84, "numeric bounds_wgs84")
    _numbers_close(value.get("extent_m"), EXTENT_3857, "numeric extent_m")
    return copy.deepcopy(value)


def _forecast_state_contract() -> Dict[str, object]:
    return {
        "algorithm": FORECAST_STATE_BINDING,
        "digest": "sha256",
        "canonicalization": "ordered UTF-8 key=value lines",
        "binds": [
            "forecast metadata",
            "image path and SHA-256",
            "numeric path and SHA-256",
        ],
    }


def _forecast_state_id(
    value: Mapping[str, object],
    image_path: str,
    image_sha256: str,
    numeric_path: str,
    numeric_sha256: str,
) -> str:
    fields = (
        ("binding", FORECAST_STATE_BINDING),
        ("product_id", PRODUCT_ID),
        ("source_id", SOURCE_ID),
        ("parameter", "APCP"),
        ("level", "surface"),
        ("cycle_utc", str(value["cycle_utc"])),
        ("lead_hours", str(value["lead_hours"])),
        ("valid_time_utc", str(value["valid_time_utc"])),
        ("accumulation_start_utc", str(value["accumulation_start_utc"])),
        ("accumulation_end_utc", str(value["accumulation_end_utc"])),
        ("accumulation_hours", str(ACCUMULATION_HOURS)),
        ("source_inventory_semantics", str(value["source_inventory_semantics"])),
        ("native_units", "kg/m^2"),
        ("normalized_units", "mm"),
        ("stored_numeric_units", "in"),
        ("display_units", "in"),
        ("grid_contract_id", GRID_CONTRACT_ID),
        ("columns", str(IMAGE_WIDTH)),
        ("rows", str(IMAGE_HEIGHT)),
        ("crs", "EPSG:3857"),
        ("bounds_wgs84", "-130,30,-112,44.5"),
        (
            "extent_m",
            "-14471533.803125564,3503549.843504374,"
            "-12467782.96884664,5543147.203861799",
        ),
        ("row_order", "north_to_south"),
        ("column_order", "west_to_east"),
        ("pixel_is_area", "true"),
        ("palette_id", PALETTE_ID),
        ("palette_version", str(PALETTE_VERSION)),
        ("image_path", image_path),
        ("image_sha256", image_sha256),
        ("image_media_type", "image/webp"),
        ("image_encoding", "lossless_vp8l_rgba8"),
        ("numeric_path", numeric_path),
        ("numeric_sha256", numeric_sha256),
        ("numeric_media_type", "application/octet-stream"),
        ("numeric_encoding", NUMERIC_ENCODING),
        ("numeric_compression", NUMERIC_COMPRESSION),
        ("numeric_scale", "0.001"),
        ("numeric_offset", "0"),
        ("numeric_nodata", str(NUMERIC_NODATA)),
    )
    canonical = "\n".join(f"{key}={item}" for key, item in fields).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _legend_for_maximum(maximum: float) -> tuple[int, bool]:
    classes = _palette_contract()["classes"]
    if maximum < 0.01:
        return 3, False
    active = None
    for item in classes:
        upper = item["upper_exclusive_in"]
        if maximum >= item["lower_inclusive_in"] and (upper is None or maximum < upper):
            active = item
            break
    if active is None:
        raise ProductError("cycle maximum does not map to the locked QPF palette")
    if active["upper_exclusive_in"] is None:
        return 20, True
    required = max(3.0, float(active["upper_exclusive_in"]))
    for cap in (3, 4, 6, 8, 10, 12, 15, 20):
        if cap >= required:
            return cap, False
    raise ProductError("cycle maximum exceeds the locked legend policy")


def _run_tool(command: str, arguments: Sequence[str]) -> str:
    executable = shutil.which(command)
    if executable is None:
        raise ProductError(f"required WebP validation tool is unavailable: {command}")
    result = subprocess.run(
        [executable, *arguments],
        text=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        raise ProductError(f"{command} failed for QPF target: {detail}")
    return (result.stdout + result.stderr).decode("utf-8", errors="replace")


def _validate_webp(path: Path, palette_colors: set[bytes]) -> bytes:
    header = path.read_bytes()[:16]
    if len(header) < 16 or header[:4] != b"RIFF" or header[8:12] != b"WEBP" or header[12:16] != b"VP8L":
        raise ProductError(f"QPF target is not a lossless VP8L WebP: {path}")
    info = _run_tool("webpinfo", ["-summary", str(path)])
    required = (
        f"Width: {IMAGE_WIDTH}",
        f"Height: {IMAGE_HEIGHT}",
        "Alpha: 1",
        "Animation: 0",
        "Format: Lossless",
        "Number of frames: 1",
        "No error detected.",
    )
    if any(item not in info for item in required):
        raise ProductError(f"QPF WebP structure/dimensions are invalid: {path}")
    with tempfile.TemporaryDirectory(prefix="nbm-qpf-webp-") as temporary:
        pam = Path(temporary) / "decoded.pam"
        _run_tool("dwebp", [str(path), "-pam", "-o", str(pam)])
        payload = pam.read_bytes()
    marker = b"ENDHDR\n"
    boundary = payload.find(marker)
    if boundary < 0:
        raise ProductError(f"decoded QPF WebP is not a PAM image: {path}")
    header_text = payload[:boundary].decode("ascii", errors="strict")
    expected_lines = {
        f"WIDTH {IMAGE_WIDTH}",
        f"HEIGHT {IMAGE_HEIGHT}",
        "DEPTH 4",
        "MAXVAL 255",
        "TUPLTYPE RGB_ALPHA",
    }
    if not expected_lines.issubset(set(header_text.splitlines())):
        raise ProductError(f"decoded QPF WebP PAM metadata is invalid: {path}")
    pixels = payload[boundary + len(marker) :]
    if len(pixels) != IMAGE_WIDTH * IMAGE_HEIGHT * 4:
        raise ProductError(f"decoded QPF WebP pixel count is invalid: {path}")
    for offset in range(0, len(pixels), 4):
        rgb = pixels[offset : offset + 3]
        alpha = pixels[offset + 3]
        if alpha == 0:
            if rgb != b"\x00\x00\x00":
                raise ProductError("transparent QPF WebP pixels must be RGBA 0,0,0,0")
        elif alpha == 255:
            if rgb not in palette_colors:
                raise ProductError("painted QPF WebP pixel is outside the locked palette")
        else:
            raise ProductError("QPF WebP alpha must be exactly 0 or 255")
    return pixels


def _validate_numeric(path: Path) -> array.array[int]:
    try:
        compressed = path.read_bytes()
        payload = gzip.decompress(compressed)
    except (OSError, EOFError, gzip.BadGzipFile, zlib.error) as exc:
        raise ProductError(f"QPF numeric target is not valid gzip: {path}") from exc
    if len(payload) != NUMERIC_UNCOMPRESSED_BYTES:
        raise ProductError(
            f"QPF numeric uncompressed byte count is invalid: {path}"
        )
    values = array.array("H")
    values.frombytes(payload)
    if sys.byteorder != "little":
        values.byteswap()
    if len(values) != IMAGE_WIDTH * IMAGE_HEIGHT:
        raise ProductError(f"QPF numeric decoded cell count is invalid: {path}")
    # Every uint16 is structurally valid; 65535 alone is reserved for NoData.
    # Decoding explicitly as little-endian proves the application payload
    # contract independently of any HTTP Content-Encoding behavior.
    if any(value < 0 or value > NUMERIC_NODATA for value in values):
        raise ProductError(f"QPF numeric target contains an invalid uint16 value: {path}")
    return values


def _allowed_rgba_by_stored_value() -> tuple[tuple[bytes, ...], ...]:
    classes = _palette_contract()["classes"]
    transparent = b"\x00\x00\x00\x00"

    def color(value: float) -> bytes:
        if value < 0.01:
            return transparent
        for item in classes:
            upper = item["upper_exclusive_in"]
            if value >= item["lower_inclusive_in"] and (
                upper is None or value < upper
            ):
                return bytes.fromhex(item["color_hex"].lstrip("#")) + b"\xff"
        raise ProductError("numeric QPF value does not map to the locked palette")

    result: list[tuple[bytes, ...]] = []
    for stored in range(NUMERIC_NODATA):
        decoded = stored * NUMERIC_SCALE + NUMERIC_OFFSET
        possible = {
            color(max(0.0, decoded - 0.000499999999)),
            color(decoded),
            color(decoded + 0.000499999999),
        }
        result.append(tuple(possible))
    result.append((transparent,))
    return tuple(result)


NUMERIC_ALLOWED_RGBA = _allowed_rgba_by_stored_value()
VALIDATED_IMAGE_NUMERIC_PAIRS: set[tuple[str, str]] = set()


def _validate_image_numeric_pair(pixels: bytes, values: array.array[int]) -> None:
    for index, stored in enumerate(values):
        rgba = pixels[index * 4 : index * 4 + 4]
        if rgba not in NUMERIC_ALLOWED_RGBA[stored]:
            raise ProductError(
                "QPF image/numeric pixels disagree beyond the numeric quantization tolerance"
            )


def _validate_target(
    root: Path,
    value: object,
    cycle: datetime,
    index: int,
    webp_cache: Dict[str, bytes],
    numeric_cache: Dict[str, array.array[int]],
) -> TargetState:
    if not isinstance(value, dict) or set(value) != TARGET_FIELDS:
        raise ProductError(f"QPF target {index} has missing or unexpected fields")
    lead = _integer(value.get("lead_hours"), f"QPF target {index} lead_hours")
    if lead not in SUPPORTED_LEADS or value.get("lead_end_hours") != lead:
        raise ProductError(f"QPF target {index} has an unsupported or incoherent lead")
    if (
        value.get("product_id") != PRODUCT_ID
        or value.get("source_id") != SOURCE_ID
        or value.get("parameter") != "APCP"
        or value.get("level") != "surface"
    ):
        raise ProductError(f"QPF target {index} identity is invalid")
    if value.get("cycle_utc") != _stamp(cycle):
        raise ProductError(f"QPF target {index} crosses cycles")
    expected_end = cycle + timedelta(hours=lead)
    expected_start = expected_end - timedelta(hours=ACCUMULATION_HOURS)
    if (
        value.get("accumulation_start_utc") != _stamp(expected_start)
        or value.get("accumulation_end_utc") != _stamp(expected_end)
        or value.get("valid_time_utc") != _stamp(expected_end)
        or value.get("accumulation_hours") != ACCUMULATION_HOURS
    ):
        raise ProductError(f"QPF target {index} six-hour temporal alignment is invalid")
    expected_semantics = f"{lead - 6}-{lead} hour acc fcst"
    scalar_expected = {
        "source_parameter": "APCP",
        "source_level": "surface",
        "source_inventory_semantics": expected_semantics,
        "native_units": "kg/m^2",
        "normalized_units": "mm",
        "stored_numeric_units": "in",
        "display_units": "in",
        "grid_contract_id": GRID_CONTRACT_ID,
        "columns": IMAGE_WIDTH,
        "rows": IMAGE_HEIGHT,
        "crs": "EPSG:3857",
        "row_order": "north_to_south",
        "column_order": "west_to_east",
        "pixel_is_area": True,
        "image_media_type": "image/webp",
        "image_encoding": "lossless_vp8l_rgba8",
        "image_width": IMAGE_WIDTH,
        "image_height": IMAGE_HEIGHT,
        "palette_id": PALETTE_ID,
        "palette_version": PALETTE_VERSION,
    }
    for key, expected in scalar_expected.items():
        if value.get(key) != expected:
            raise ProductError(f"QPF target {index} {key} is invalid")
    _numbers_close(value.get("bounds_wgs84"), BOUNDS_WGS84, f"target {index} bounds")
    _numbers_close(value.get("extent_m"), EXTENT_3857, f"target {index} extent")
    sha = value.get("sha256")
    byte_count = value.get("bytes")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ProductError(f"QPF target {index} SHA-256 is invalid")
    if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count <= 0:
        raise ProductError(f"QPF target {index} byte count is invalid")
    image_path = value.get("image_path")
    if not isinstance(image_path, str):
        raise ProductError(f"QPF target {index} image_path is invalid")
    image_relative = Path(image_path)
    match = TARGET_PATTERN.fullmatch(image_relative.name)
    expected_cycle_token = cycle.strftime("%Y%m%dT%H%M%SZ")
    if (
        image_relative.parent != TARGET_ROOT
        or match is None
        or match.group(1) != expected_cycle_token
        or int(match.group(2)) != lead
        or match.group(3) != sha[:12]
    ):
        raise ProductError(f"QPF target {index} content-addressed path is invalid")
    image_file = root / image_relative
    if not image_file.is_file() or image_file.is_symlink():
        raise ProductError(f"QPF manifest references a missing target: {image_relative}")
    if image_file.stat().st_size != byte_count or _sha256(image_file) != sha:
        raise ProductError(f"QPF target bytes/hash mismatch: {image_relative}")
    image = value.get("image")
    expected_image = {
        "path": image_path,
        "media_type": "image/webp",
        "encoding": "lossless_vp8l_rgba8",
        "bytes": byte_count,
        "sha256": sha,
    }
    if image != expected_image:
        raise ProductError(f"QPF target {index} nested image identity is invalid")
    if sha not in webp_cache:
        colors = {
            bytes.fromhex(item["color_hex"].lstrip("#"))
            for item in _palette_contract()["classes"]
        }
        webp_cache[sha] = _validate_webp(image_file, colors)

    numeric = value.get("numeric")
    numeric_fields = {
        "path",
        "media_type",
        "encoding",
        "compression",
        "stored_units",
        "scale",
        "offset",
        "nodata",
        "compressed_bytes",
        "uncompressed_bytes",
        "sha256",
    }
    if not isinstance(numeric, dict) or set(numeric) != numeric_fields:
        raise ProductError(f"QPF target {index} numeric metadata shape is invalid")
    numeric_expected = {
        "media_type": "application/octet-stream",
        "encoding": NUMERIC_ENCODING,
        "compression": NUMERIC_COMPRESSION,
        "stored_units": "in",
        "scale": NUMERIC_SCALE,
        "offset": NUMERIC_OFFSET,
        "nodata": NUMERIC_NODATA,
        "uncompressed_bytes": NUMERIC_UNCOMPRESSED_BYTES,
    }
    for key, expected in numeric_expected.items():
        if numeric.get(key) != expected:
            raise ProductError(f"QPF target {index} numeric {key} is invalid")
    numeric_sha = numeric.get("sha256")
    numeric_bytes = numeric.get("compressed_bytes")
    if not isinstance(numeric_sha, str) or not re.fullmatch(
        r"[0-9a-f]{64}", numeric_sha
    ):
        raise ProductError(f"QPF target {index} numeric SHA-256 is invalid")
    if (
        isinstance(numeric_bytes, bool)
        or not isinstance(numeric_bytes, int)
        or numeric_bytes <= 0
    ):
        raise ProductError(f"QPF target {index} numeric byte count is invalid")
    numeric_path = numeric.get("path")
    if not isinstance(numeric_path, str):
        raise ProductError(f"QPF target {index} numeric path is invalid")
    numeric_relative = Path(numeric_path)
    numeric_match = NUMERIC_PATTERN.fullmatch(numeric_relative.name)
    if (
        numeric_relative.parent != TARGET_ROOT
        or numeric_match is None
        or numeric_match.group(1) != expected_cycle_token
        or int(numeric_match.group(2)) != lead
        or numeric_match.group(3) != numeric_sha[:12]
    ):
        raise ProductError(f"QPF target {index} numeric content-addressed path is invalid")
    numeric_file = root / numeric_relative
    if not numeric_file.is_file() or numeric_file.is_symlink():
        raise ProductError(f"QPF manifest references a missing numeric target: {numeric_relative}")
    if numeric_file.stat().st_size != numeric_bytes or _sha256(numeric_file) != numeric_sha:
        raise ProductError(f"QPF numeric target bytes/hash mismatch: {numeric_relative}")
    if numeric_sha not in numeric_cache:
        numeric_cache[numeric_sha] = _validate_numeric(numeric_file)
    pair_identity = (sha, numeric_sha)
    if pair_identity not in VALIDATED_IMAGE_NUMERIC_PAIRS:
        _validate_image_numeric_pair(webp_cache[sha], numeric_cache[numeric_sha])
        VALIDATED_IMAGE_NUMERIC_PAIRS.add(pair_identity)

    expected_state_id = _forecast_state_id(
        value, image_path, sha, numeric_path, numeric_sha
    )
    state_id = value.get("forecast_state_id")
    if (
        not isinstance(state_id, str)
        or not re.fullmatch(r"[0-9a-f]{64}", state_id)
        or state_id != expected_state_id
    ):
        raise ProductError(f"QPF target {index} image/numeric forecast-state binding is invalid")
    return TargetState(
        copy.deepcopy(value),
        image_relative,
        sha,
        numeric_relative,
        numeric_sha,
    )


def _validate_cycle(
    root: Path,
    value: object,
    index: int,
    webp_cache: Dict[str, bytes],
    numeric_cache: Dict[str, array.array[int]],
) -> CycleState:
    if not isinstance(value, dict) or set(value) != CYCLE_FIELDS:
        raise ProductError(f"QPF cycle {index} has missing or unexpected fields")
    cycle = _timestamp(value.get("cycle_utc"), f"QPF cycle {index} cycle_utc")
    if cycle.minute != 0 or cycle.second != 0 or cycle.hour not in {0, 6, 12, 18}:
        raise ProductError(f"QPF cycle {index} is not a primary NBM cycle")
    if value.get("cycle_status") != "complete":
        raise ProductError(f"QPF cycle {index} is not complete")
    maximum = _finite_number(value.get("cycle_max_qpf_in"), "cycle maximum")
    if maximum < 0:
        raise ProductError("QPF cycle maximum must be nonnegative")
    expected_cap, expected_overflow = _legend_for_maximum(maximum)
    if (
        value.get("legend_cap_in") != expected_cap
        or value.get("legend_overflow") is not expected_overflow
    ):
        raise ProductError(f"QPF cycle {index} legend-cap metadata is inconsistent")
    if value.get("target_count") != len(SUPPORTED_LEADS):
        raise ProductError(
            f"QPF cycle {index} target_count must equal {len(SUPPORTED_LEADS)}"
        )
    if value.get("complete_required_leads_hours") != list(SUPPORTED_LEADS):
        raise ProductError(f"QPF cycle {index} required lead set is invalid")
    values = value.get("targets")
    if not isinstance(values, list) or len(values) != len(SUPPORTED_LEADS):
        raise ProductError(
            f"QPF cycle {index} must contain exactly {len(SUPPORTED_LEADS)} targets"
        )
    targets = tuple(
        _validate_target(
            root, target, cycle, target_index, webp_cache, numeric_cache
        )
        for target_index, target in enumerate(values)
    )
    leads = [int(target.entry["lead_hours"]) for target in targets]
    if leads != list(SUPPORTED_LEADS) or len(set(leads)) != len(leads):
        raise ProductError(f"QPF cycle {index} targets are duplicated, missing, or unordered")
    signature = _json_signature(value)
    return CycleState(copy.deepcopy(value), cycle, targets, signature)


def validate_product(root: Path, *, allow_bootstrap: bool) -> ProductState:
    root = root.resolve()
    _ensure_no_symlink_ancestors(root, MANIFEST_PATH)
    _ensure_no_symlink_ancestors(root, TARGET_ROOT)
    manifest_path = root / MANIFEST_PATH
    manifest = _read_json(manifest_path)
    if manifest_path.stat().st_size > MANIFEST_MAX_BYTES:
        raise ProductError("QPF public manifest exceeds the 512-KB contract budget")
    if not isinstance(manifest, dict) or set(manifest) != ROOT_FIELDS:
        raise ProductError("QPF public manifest has missing or unexpected root fields")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ProductError("QPF public manifest schema_version is unsupported")
    if manifest.get("product_id") != PRODUCT_ID:
        raise ProductError("QPF public manifest product identity is invalid")
    _timestamp(manifest.get("generated_at_utc"), "QPF manifest generated_at_utc")
    if manifest.get("source") != SOURCE_CONTRACT:
        raise ProductError("QPF public manifest source identity is invalid")
    _validate_palette(manifest.get("palette"))
    _validate_spatial(manifest.get("spatial_representation"))
    _validate_numeric_contract(manifest.get("numeric_representation"))
    if manifest.get("forecast_state_binding") != _forecast_state_contract():
        raise ProductError("QPF public manifest forecast-state binding is invalid")
    if manifest.get("freshness") != FRESHNESS_CONTRACT:
        raise ProductError("QPF public manifest freshness contract is invalid")
    values = manifest.get("cycles")
    if not isinstance(values, list) or len(values) not in {1, 2}:
        raise ProductError("QPF public manifest must retain one bootstrap or two steady cycles")
    webp_cache: Dict[str, bytes] = {}
    numeric_cache: Dict[str, array.array[int]] = {}
    cycles = tuple(
        _validate_cycle(root, cycle, index, webp_cache, numeric_cache)
        for index, cycle in enumerate(values)
    )
    mode = manifest.get("retention_mode")
    if len(cycles) == 1:
        if not allow_bootstrap:
            raise ProductError("QPF public manifest has not completed two-cycle seeding")
        if mode != "bootstrap" or manifest.get("previous_cycle_utc") is not None:
            raise ProductError("one-cycle QPF state must be explicit bootstrap state")
        previous = None
    else:
        if mode != "steady" or manifest.get("previous_cycle_utc") != cycles[1].entry["cycle_utc"]:
            raise ProductError("two-cycle QPF retention metadata is invalid")
        if not cycles[0].cycle > cycles[1].cycle:
            raise ProductError("QPF current cycle must be strictly newer than previous")
        previous = cycles[1]
    if manifest.get("current_cycle_utc") != cycles[0].entry["cycle_utc"]:
        raise ProductError("QPF current_cycle_utc does not identify the first cycle")
    referenced = {
        path
        for cycle in cycles
        for target in cycle.targets
        for path in (target.image_path, target.numeric_path)
    }
    target_root = root / TARGET_ROOT
    if not target_root.is_dir() or target_root.is_symlink():
        raise ProductError("QPF immutable target root is missing or unsafe")
    actual = {
        path.relative_to(root)
        for path in target_root.rglob("*")
        if path.is_file()
    }
    for path in target_root.rglob("*"):
        if path.is_symlink():
            raise ProductError(f"QPF immutable target tree contains a symlink: {path}")
    if actual != referenced:
        missing = sorted(path.as_posix() for path in referenced - actual)
        unexpected = sorted(path.as_posix() for path in actual - referenced)
        raise ProductError(
            f"QPF manifest-target closure failed; missing={missing}; unexpected={unexpected}"
        )
    product_root = root / MANIFEST_PATH.parent
    _ensure_plain_tree(product_root)
    expected_product_files = {MANIFEST_PATH, *referenced}
    actual_product_files = {
        path.relative_to(root)
        for path in product_root.rglob("*")
        if path.is_file()
    }
    if actual_product_files != expected_product_files:
        missing = sorted(
            path.as_posix() for path in expected_product_files - actual_product_files
        )
        unexpected = sorted(
            path.as_posix() for path in actual_product_files - expected_product_files
        )
        raise ProductError(
            f"QPF product-root closure failed; missing={missing}; unexpected={unexpected}"
        )
    return ProductState(root, copy.deepcopy(manifest), cycles, cycles[0], previous)


def _validate_legacy_target(
    root: Path,
    value: object,
    cycle: datetime,
    lead: int,
    webp_cache: set[str],
) -> Path:
    if not isinstance(value, dict) or set(value) != LEGACY_TARGET_FIELDS:
        raise ProductError("legacy QPF target shape is invalid")
    end = cycle + timedelta(hours=lead)
    start = end - timedelta(hours=ACCUMULATION_HOURS)
    expected = {
        "product_id": PRODUCT_ID,
        "source_id": SOURCE_ID,
        "cycle_utc": _stamp(cycle),
        "lead_hours": lead,
        "lead_end_hours": lead,
        "accumulation_start_utc": _stamp(start),
        "accumulation_end_utc": _stamp(end),
        "valid_time_utc": _stamp(end),
        "accumulation_hours": ACCUMULATION_HOURS,
        "source_parameter": "APCP",
        "source_level": "surface",
        "source_inventory_semantics": f"{lead - 6}-{lead} hour acc fcst",
        "native_units": "kg/m^2",
        "normalized_units": "mm",
        "display_units": "in",
        "image_media_type": "image/webp",
        "image_encoding": "lossless_vp8l_rgba8",
        "image_width": IMAGE_WIDTH,
        "image_height": IMAGE_HEIGHT,
        "palette_id": PALETTE_ID,
        "palette_version": PALETTE_VERSION,
    }
    if any(value.get(key) != item for key, item in expected.items()):
        raise ProductError("legacy QPF target contract is invalid")
    _numbers_close(value.get("bounds_wgs84"), BOUNDS_WGS84, "legacy target bounds")
    sha = value.get("sha256")
    byte_count = value.get("bytes")
    image_path = value.get("image_path")
    if (
        not isinstance(sha, str)
        or not re.fullmatch(r"[0-9a-f]{64}", sha)
        or isinstance(byte_count, bool)
        or not isinstance(byte_count, int)
        or byte_count <= 0
        or not isinstance(image_path, str)
    ):
        raise ProductError("legacy QPF target identity is invalid")
    relative = Path(image_path)
    match = TARGET_PATTERN.fullmatch(relative.name)
    if (
        relative.parent != TARGET_ROOT
        or match is None
        or match.group(1) != cycle.strftime("%Y%m%dT%H%M%SZ")
        or int(match.group(2)) != lead
        or match.group(3) != sha[:12]
    ):
        raise ProductError("legacy QPF target path identity is invalid")
    path = root / relative
    if (
        not path.is_file()
        or path.is_symlink()
        or path.stat().st_size != byte_count
        or _sha256(path) != sha
    ):
        raise ProductError("legacy QPF target bytes/hash are invalid")
    if sha not in webp_cache:
        colors = {
            bytes.fromhex(item["color_hex"].lstrip("#"))
            for item in _palette_contract()["classes"]
        }
        _validate_webp(path, colors)
        webp_cache.add(sha)
    return relative


def _validate_legacy_product(root: Path) -> datetime:
    root = root.resolve()
    manifest = _read_json(root / MANIFEST_PATH)
    if not isinstance(manifest, dict) or set(manifest) != LEGACY_ROOT_FIELDS:
        raise ProductError("legacy QPF manifest root shape is invalid")
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("product_id") != PRODUCT_ID
        or manifest.get("source") != SOURCE_CONTRACT
        or manifest.get("freshness") != FRESHNESS_CONTRACT
    ):
        raise ProductError("legacy QPF manifest root contract is invalid")
    _timestamp(manifest.get("generated_at_utc"), "legacy QPF generated_at_utc")
    _validate_palette(manifest.get("palette"))
    _validate_spatial(manifest.get("spatial_representation"))
    cycles = manifest.get("cycles")
    if not isinstance(cycles, list) or len(cycles) not in {1, 2}:
        raise ProductError("legacy QPF retained-cycle inventory is invalid")
    webp_cache: set[str] = set()
    referenced: set[Path] = set()
    parsed_cycles: list[datetime] = []
    for cycle_index, cycle_value in enumerate(cycles):
        if not isinstance(cycle_value, dict) or set(cycle_value) != CYCLE_FIELDS:
            raise ProductError("legacy QPF cycle shape is invalid")
        cycle = _timestamp(cycle_value.get("cycle_utc"), "legacy QPF cycle")
        if cycle.minute or cycle.second or cycle.hour not in {0, 6, 12, 18}:
            raise ProductError("legacy QPF cycle identity is invalid")
        if (
            cycle_value.get("cycle_status") != "complete"
            or cycle_value.get("target_count") != len(LEGACY_SUPPORTED_LEADS)
            or cycle_value.get("complete_required_leads_hours")
            != list(LEGACY_SUPPORTED_LEADS)
        ):
            raise ProductError("legacy QPF cycle completeness is invalid")
        targets = cycle_value.get("targets")
        if not isinstance(targets, list) or len(targets) != len(LEGACY_SUPPORTED_LEADS):
            raise ProductError("legacy QPF target inventory is invalid")
        for lead, target in zip(LEGACY_SUPPORTED_LEADS, targets):
            referenced.add(
                _validate_legacy_target(root, target, cycle, lead, webp_cache)
            )
        parsed_cycles.append(cycle)
    if parsed_cycles != sorted(parsed_cycles, reverse=True):
        raise ProductError("legacy QPF cycles are not newest-first")
    if manifest.get("current_cycle_utc") != _stamp(parsed_cycles[0]):
        raise ProductError("legacy QPF current cycle identity is invalid")
    mode = manifest.get("retention_mode")
    previous = manifest.get("previous_cycle_utc")
    if (len(parsed_cycles) == 1 and (mode != "bootstrap" or previous is not None)) or (
        len(parsed_cycles) == 2
        and (mode != "steady" or previous != _stamp(parsed_cycles[1]))
    ):
        raise ProductError("legacy QPF retention metadata is invalid")
    actual = {
        path.relative_to(root)
        for path in (root / TARGET_ROOT).rglob("*")
        if path.is_file()
    }
    if actual != referenced:
        raise ProductError("legacy QPF manifest-target closure is invalid")
    return parsed_cycles[0]


def validate_candidate(root: Path) -> ProductState:
    state = validate_product(root, allow_bootstrap=True)
    if state.manifest.get("retention_mode") != "bootstrap" or len(state.cycles) != 1:
        raise ProductError("QPF publication candidate must contain exactly one complete cycle")
    return state


def _validate_r_candidate(root: Path) -> Dict[str, object]:
    root = root.resolve()
    _ensure_plain_tree(root)
    manifest = _read_json(root / R_CANDIDATE_MANIFEST)
    if not isinstance(manifest, dict) or set(manifest) != R_CANDIDATE_FIELDS:
        raise ProductError("R QPF candidate manifest has an invalid shape")
    scalar_expected = {
        "candidate_schema_version": "1.0.0",
        "candidate_kind": "offline_complete_cycle",
        "product_id": PRODUCT_ID,
        "publication_ready": False,
        "mutable_public_manifest_included": False,
    }
    for key, expected in scalar_expected.items():
        if manifest.get(key) != expected:
            raise ProductError(f"R QPF candidate {key} is invalid")
    if manifest.get("source") != SOURCE_CONTRACT:
        raise ProductError("R QPF candidate source identity is invalid")
    _validate_palette(manifest.get("palette"))
    _validate_spatial(manifest.get("spatial_representation"))
    _validate_numeric_contract(manifest.get("numeric_representation"))
    if manifest.get("forecast_state_binding") != _forecast_state_contract():
        raise ProductError("R QPF candidate forecast-state binding is invalid")
    webp_cache: Dict[str, bytes] = {}
    numeric_cache: Dict[str, array.array[int]] = {}
    cycle = _validate_cycle(
        root, manifest.get("cycle"), 0, webp_cache, numeric_cache
    )
    validation = _read_json(root / R_VALIDATION)
    if not isinstance(validation, dict):
        raise ProductError("R QPF candidate validation root is invalid")
    validation_expected = {
        "validation_result": "passed",
        "complete_cycle": True,
        "source_record_count": len(SUPPORTED_LEADS),
        "target_count": len(SUPPORTED_LEADS),
        "numeric_reprojection_before_classification": True,
        "interpolation": "bilinear",
        "manifest_target_closure": True,
        "content_hashes_validated": True,
        "image_numeric_state_binding_validated": True,
        "numeric_encoding": NUMERIC_ENCODING,
        "numeric_compression": NUMERIC_COMPRESSION,
        "numeric_uncompressed_bytes_per_target": NUMERIC_UNCOMPRESSED_BYTES,
    }
    for key, expected in validation_expected.items():
        if validation.get(key) != expected:
            raise ProductError(f"R QPF validation {key} is invalid")
    expected_cycle_bytes = {
        "cycle_image_bytes": sum(int(target.entry["bytes"]) for target in cycle.targets),
        "cycle_numeric_compressed_bytes": sum(
            int(target.entry["numeric"]["compressed_bytes"])
            for target in cycle.targets
        ),
        "cycle_numeric_uncompressed_bytes": len(SUPPORTED_LEADS)
        * NUMERIC_UNCOMPRESSED_BYTES,
    }
    for key, expected in expected_cycle_bytes.items():
        if validation.get(key) != expected:
            raise ProductError(f"R QPF byte diagnostic {key} is invalid")
    preflight_path = root / R_PREFLIGHT
    if not preflight_path.is_file() or preflight_path.is_symlink():
        raise ProductError("R QPF candidate preflight.csv is missing")
    with preflight_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != len(SUPPORTED_LEADS) or [
        int(row["lead_hours"]) for row in rows
    ] != list(SUPPORTED_LEADS):
        raise ProductError("R QPF preflight lead inventory is incomplete or unordered")
    if any(row["cycle_utc"] != cycle.entry["cycle_utc"] for row in rows):
        raise ProductError("R QPF preflight crosses cycles")
    referenced = {
        path
        for target in cycle.targets
        for path in (target.image_path, target.numeric_path)
    }
    expected_inventory = {R_CANDIDATE_MANIFEST, R_VALIDATION, R_PREFLIGHT, *referenced}
    actual_inventory = _inventory(root)
    if actual_inventory != expected_inventory:
        unexpected = sorted(path.as_posix() for path in actual_inventory - expected_inventory)
        missing = sorted(path.as_posix() for path in expected_inventory - actual_inventory)
        raise ProductError(
            f"R QPF candidate inventory is not closed; missing={missing}; unexpected={unexpected}"
        )
    return manifest


def _public_manifest(
    source_state: ProductState | None,
    source_manifest: Mapping[str, object],
    current: CycleState,
    previous: CycleState | None,
    now: datetime,
) -> Dict[str, object]:
    del source_state
    return {
        "schema_version": SCHEMA_VERSION,
        "product_id": PRODUCT_ID,
        "generated_at_utc": _stamp(now),
        "source": copy.deepcopy(source_manifest["source"]),
        "palette": copy.deepcopy(source_manifest["palette"]),
        "spatial_representation": copy.deepcopy(source_manifest["spatial_representation"]),
        "numeric_representation": copy.deepcopy(source_manifest["numeric_representation"]),
        "forecast_state_binding": copy.deepcopy(source_manifest["forecast_state_binding"]),
        "freshness": copy.deepcopy(FRESHNESS_CONTRACT),
        "retention_mode": "steady" if previous is not None else "bootstrap",
        "current_cycle_utc": current.entry["cycle_utc"],
        "previous_cycle_utc": None if previous is None else previous.entry["cycle_utc"],
        "cycles": [
            copy.deepcopy(current.entry),
            *([] if previous is None else [copy.deepcopy(previous.entry)]),
        ],
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def assemble_candidate(r_candidate_root: Path, output_root: Path) -> ProductState:
    r_manifest = _validate_r_candidate(r_candidate_root)
    if output_root.exists() and (
        output_root.is_symlink() or any(output_root.iterdir())
    ):
        raise ProductError("QPF publication candidate output root must be absent or empty")
    output_root.mkdir(parents=True, exist_ok=True)
    cycle_value = r_manifest["cycle"]
    for target in cycle_value["targets"]:
        for relative_text in (target["image_path"], target["numeric"]["path"]):
            relative = Path(relative_text)
            destination = output_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(r_candidate_root / relative, destination)
    webp_cache: Dict[str, bytes] = {}
    numeric_cache: Dict[str, array.array[int]] = {}
    cycle = _validate_cycle(
        output_root, cycle_value, 0, webp_cache, numeric_cache
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "product_id": PRODUCT_ID,
        "generated_at_utc": _stamp(datetime.now(timezone.utc)),
        "source": copy.deepcopy(r_manifest["source"]),
        "palette": copy.deepcopy(r_manifest["palette"]),
        "spatial_representation": copy.deepcopy(r_manifest["spatial_representation"]),
        "numeric_representation": copy.deepcopy(r_manifest["numeric_representation"]),
        "forecast_state_binding": copy.deepcopy(r_manifest["forecast_state_binding"]),
        "freshness": copy.deepcopy(FRESHNESS_CONTRACT),
        "retention_mode": "bootstrap",
        "current_cycle_utc": cycle.entry["cycle_utc"],
        "previous_cycle_utc": None,
        "cycles": [copy.deepcopy(cycle.entry)],
    }
    _write_json(output_root / MANIFEST_PATH, manifest)
    return validate_candidate(output_root)


def semantic_key(root: Path) -> str:
    return validate_candidate(root).current.signature


def _contracts_match(candidate: ProductState, canonical: ProductState) -> bool:
    keys = (
        "source",
        "palette",
        "spatial_representation",
        "numeric_representation",
        "forecast_state_binding",
        "freshness",
    )
    return all(candidate.manifest[key] == canonical.manifest[key] for key in keys)


def _apply_desired_state(
    worktree: Path,
    manifest: Mapping[str, object],
    desired: Mapping[Path, tuple[Path, str]],
) -> None:
    _ensure_no_symlink_ancestors(worktree, MANIFEST_PATH)
    _ensure_no_symlink_ancestors(worktree, TARGET_ROOT)
    target_root = worktree / TARGET_ROOT
    if target_root.exists() and (target_root.is_symlink() or not target_root.is_dir()):
        raise ProductError("canonical QPF target root is unsafe")
    target_root.mkdir(parents=True, exist_ok=True)
    desired_paths = set(desired)
    for member in target_root.rglob("*"):
        if member.is_symlink():
            raise ProductError(f"canonical QPF target root contains a symlink: {member}")
    for member in sorted(target_root.rglob("*"), reverse=True):
        if member.is_file() and member.relative_to(worktree) not in desired_paths:
            member.unlink()
        elif member.is_dir():
            try:
                member.rmdir()
            except OSError:
                pass
    for relative, (source, expected_sha) in desired.items():
        destination = worktree / relative
        if destination.is_file() and not destination.is_symlink() and _sha256(destination) == expected_sha:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    _write_json(worktree / MANIFEST_PATH, manifest)


def reconcile(
    candidate_root: Path,
    canonical_root: Path,
    *,
    now: datetime | None = None,
) -> tuple[str, str]:
    candidate = validate_candidate(candidate_root)
    manifest_path = canonical_root / MANIFEST_PATH
    now = now or datetime.now(timezone.utc)
    if not manifest_path.exists():
        product_root = canonical_root / MANIFEST_PATH.parent
        if product_root.exists() and any(path.is_file() for path in product_root.rglob("*")):
            raise ProductError("canonical QPF root has files but no valid root manifest")
        manifest = _public_manifest(None, candidate.manifest, candidate.current, None, now)
        desired: Dict[Path, tuple[Path, str]] = {}
        for target in candidate.current.targets:
            desired[target.image_path] = (
                candidate.root / target.image_path,
                target.image_sha256,
            )
            desired[target.numeric_path] = (
                candidate.root / target.numeric_path,
                target.numeric_sha256,
            )
        _apply_desired_state(canonical_root, manifest, desired)
        validate_product(canonical_root, allow_bootstrap=True)
        return "new", "canonical QPF product was absent; established one-cycle bootstrap"
    try:
        canonical = validate_product(canonical_root, allow_bootstrap=True)
    except ProductError as modern_error:
        try:
            legacy_cycle = _validate_legacy_product(canonical_root)
        except ProductError as legacy_error:
            raise ProductError(
                "canonical QPF state is neither a valid f240 product nor the exact "
                f"legacy migration state: modern={modern_error}; legacy={legacy_error}"
            ) from legacy_error
        proposed = candidate.current
        if proposed.cycle <= legacy_cycle:
            return "stale", "candidate cycle is not newer than legacy canonical QPF"
        manifest = _public_manifest(
            None, candidate.manifest, proposed, None, now
        )
        desired: Dict[Path, tuple[Path, str]] = {}
        for target in proposed.targets:
            desired[target.image_path] = (
                candidate.root / target.image_path,
                target.image_sha256,
            )
            desired[target.numeric_path] = (
                candidate.root / target.numeric_path,
                target.numeric_sha256,
            )
        _apply_desired_state(canonical_root, manifest, desired)
        validate_product(canonical_root, allow_bootstrap=True)
        return (
            "new",
            "validated legacy sparse QPF state migrated to one complete f240 bootstrap cycle",
        )
    if not _contracts_match(candidate, canonical):
        raise ProductError("candidate and canonical QPF root contracts differ")
    proposed = candidate.current
    current = canonical.current
    if proposed.cycle < current.cycle:
        return "stale", "candidate cycle is older than fresh canonical QPF"
    if proposed.cycle == current.cycle:
        if proposed.signature != current.signature:
            raise ProductError("same-cycle QPF candidate has conflicting semantic/content identity")
        return "same", "candidate complete cycle is already canonical"
    manifest = _public_manifest(canonical, candidate.manifest, proposed, current, now)
    desired: Dict[Path, tuple[Path, str]] = {}
    for cycle, source_root in ((proposed, candidate.root), (current, canonical.root)):
        for target in cycle.targets:
            desired[target.image_path] = (
                source_root / target.image_path,
                target.image_sha256,
            )
            desired[target.numeric_path] = (
                source_root / target.numeric_path,
                target.numeric_sha256,
            )
    _apply_desired_state(canonical_root, manifest, desired)
    validate_product(canonical_root, allow_bootstrap=False)
    return "new", "newer QPF cycle advanced current and retained prior current as previous"


def _callback_contract() -> None:
    if os.environ.get("BRIM_PUBLISH_PRODUCT_ID") != PRODUCT_ID:
        raise ProductError("publisher product ID is not nbm_qpf")
    try:
        fixed = json.loads(os.environ["BRIM_PUBLISH_FIXED_PATHS"])
        owned = json.loads(os.environ["BRIM_PUBLISH_OWNED_ROOTS"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise ProductError("publisher ownership environment is invalid") from exc
    if fixed != [MANIFEST_PATH.as_posix()] or owned != [TARGET_ROOT.as_posix()]:
        raise ProductError("publisher ownership does not match the static QPF product root")


def _callback() -> int:
    _callback_contract()
    phase = os.environ.get("BRIM_PUBLISH_PHASE")
    candidate_root = Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"])
    worktree = Path(os.environ["BRIM_PUBLISH_WORKTREE"])
    if phase == "validate-candidate":
        state = validate_candidate(candidate_root)
        metadata = _read_json(Path(os.environ["BRIM_PUBLISH_METADATA"]))
        expected = {"type": SEMANTIC_KEY_TYPE, "value": state.current.signature}
        if not isinstance(metadata, dict) or metadata.get("semantic_key") != expected:
            raise ProductError("candidate QPF semantic key disagrees with product bytes")
        return 0
    if phase == "validate-staged":
        manifest = _read_json(worktree / MANIFEST_PATH)
        if not isinstance(manifest, dict):
            raise ProductError("staged QPF manifest root is invalid")
        validate_product(
            worktree,
            allow_bootstrap=manifest.get("retention_mode") == "bootstrap",
        )
        return 0
    if phase == "reconcile":
        state, reason = reconcile(candidate_root, worktree)
        decision = "publish" if state == "new" else "no-op"
        diagnostics: Dict[str, int] = {}
        try:
            diagnostics = _byte_diagnostics(
                validate_product(worktree, allow_bootstrap=True)
            )
        except ProductError:
            if state == "new":
                raise
        Path(os.environ["BRIM_PUBLISH_RESULT"]).write_text(
            json.dumps(
                {
                    "decision": decision,
                    "candidate_state": state,
                    "reason": reason,
                    "diagnostics": diagnostics,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return 0
    raise ProductError(f"unsupported QPF publisher phase: {phase!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    assemble = subparsers.add_parser("assemble-candidate")
    assemble.add_argument("--r-candidate-root", required=True)
    assemble.add_argument("--output-root", required=True)
    for command in ("validate-candidate", "semantic-key"):
        child = subparsers.add_parser(command)
        child.add_argument("--root", required=True)
    public = subparsers.add_parser("validate-public")
    public.add_argument("--root", required=True)
    public.add_argument("--allow-bootstrap", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        if os.environ.get("BRIM_PUBLISH_PHASE"):
            return _callback()
        args = build_parser().parse_args(argv)
        if args.command == "assemble-candidate":
            state = assemble_candidate(
                Path(args.r_candidate_root), Path(args.output_root)
            )
            print(
                f"Assembled QPF publication candidate: "
                f"{state.current.entry['cycle_utc']} with {len(SUPPORTED_LEADS)} targets"
            )
        elif args.command == "validate-candidate":
            state = validate_candidate(Path(args.root))
            diagnostics = _byte_diagnostics(state)
            print(
                f"Validated QPF publication candidate: "
                f"{state.current.entry['cycle_utc']} with {len(SUPPORTED_LEADS)} targets; "
                f"image_bytes={diagnostics['retained_image_bytes']}; "
                f"numeric_bytes={diagnostics['retained_numeric_compressed_bytes']}"
            )
        elif args.command == "validate-public":
            state = validate_product(
                Path(args.root), allow_bootstrap=args.allow_bootstrap
            )
            diagnostics = _byte_diagnostics(state)
            print(
                f"Validated public QPF tree: {len(state.cycles)} cycles, "
                f"{sum(len(cycle.targets) for cycle in state.cycles)} targets; "
                f"retained_image_bytes={diagnostics['retained_image_bytes']}; "
                f"retained_numeric_bytes={diagnostics['retained_numeric_compressed_bytes']}"
            )
        else:
            print(semantic_key(Path(args.root)))
        return 0
    except ProductError as exc:
        print(f"NBM_QPF_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
