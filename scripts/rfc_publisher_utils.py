#!/usr/bin/env python3
"""Small publication-only helpers shared by the two RFC callbacks."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Mapping, Sequence


HEX_SHA256 = re.compile(r"[0-9a-f]{64}")


class RFCProductError(RuntimeError):
    """A malformed or ambiguous RFC publication state."""


def read_json(path: Path, label: str) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise RFCProductError(f"missing plain {label} JSON: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RFCProductError(f"unreadable {label} JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RFCProductError(f"{label} JSON root must be an object")
    return value


def write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_instant(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})",
        value,
    ):
        raise RFCProductError(f"{label} must be an offset-bearing timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise RFCProductError(f"{label} is not a valid timestamp") from exc
    if parsed.tzinfo is None:
        raise RFCProductError(f"{label} must include a UTC offset")
    return parsed


def optional_instant(value: object, label: str) -> datetime | None:
    return None if value is None else parse_instant(value, label)


def parse_date(value: object, label: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise RFCProductError(f"{label} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RFCProductError(f"{label} is not a valid date") from exc


def finite_nonnegative(value: object, label: str, *, nullable: bool = True) -> None:
    if value is None and nullable:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise RFCProductError(f"{label} must be a finite nonnegative number")


def require_signature(value: object, label: str) -> str:
    if not isinstance(value, str) or not HEX_SHA256.fullmatch(value):
        raise RFCProductError(f"{label} must be a lowercase SHA-256 signature")
    return value


def records_by_key(
    payload: Mapping[str, object], expected_keys: Sequence[str], label: str
) -> dict[str, dict[str, object]]:
    records = payload.get("records")
    if not isinstance(records, list):
        raise RFCProductError(f"{label} records must be an array")
    if not all(isinstance(record, dict) for record in records):
        raise RFCProductError(f"{label} records must contain only objects")
    keys = [record.get("forecast_key") for record in records]
    if len(keys) != len(set(keys)):
        raise RFCProductError(f"{label} contains a duplicate forecast_key")
    if keys != list(expected_keys):
        missing = sorted(set(expected_keys) - set(keys))
        unknown = sorted(set(keys) - set(expected_keys), key=str)
        raise RFCProductError(
            f"{label} record ordering/identity changed; missing={missing}, unknown={unknown}"
        )
    return {str(record["forecast_key"]): record for record in records}


def semantic_digest(
    payload: Mapping[str, object], record_view: Callable[[Mapping[str, object]], object]
) -> str:
    records = payload.get("records")
    if not isinstance(records, list):
        raise RFCProductError("RFC semantic digest requires a records array")
    encoded = json.dumps(
        [record_view(record) for record in records],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require_metadata_semantic(
    metadata: Mapping[str, object], semantic_type: str, semantic_value: str
) -> None:
    semantic = metadata.get("semantic_key")
    if not isinstance(semantic, dict):
        raise RFCProductError("candidate metadata semantic_key is missing")
    if semantic != {"type": semantic_type, "value": semantic_value}:
        raise RFCProductError("candidate record semantic digest does not match metadata")


def write_result(path: Path, *, decision: str, state: str, reason: str) -> None:
    write_json(
        path,
        {"decision": decision, "candidate_state": state, "reason": reason},
    )
