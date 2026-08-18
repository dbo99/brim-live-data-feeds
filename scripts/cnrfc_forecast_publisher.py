#!/usr/bin/env python3
"""Validate and reconcile the CNRFC record-level forecast product."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from rfc_publisher_utils import (
    RFCProductError,
    finite_nonnegative,
    optional_instant,
    parse_date,
    parse_instant,
    read_json,
    records_by_key,
    require_metadata_semantic,
    require_signature,
    semantic_digest,
    write_json,
    write_result,
)


PRODUCT_ID = "cnrfc-major-water-supply-forecasts"
PAYLOAD_PRODUCT_ID = "major_water_supply_basin_forecasts"
ROSTER_VERSION = "cnrfc-major-water-supply-v1.1.0"
SEMANTIC_TYPE = "cnrfc_record_state_sha256_v1"
PRODUCT_PATH = Path("docs/data/major_water_supply_basin_forecasts.json")
ROSTER_PATH = (
    Path(__file__).resolve().parents[1]
    / "data/input/cnrfc_major_water_supply_basin_forecast_sources.csv"
)

FAMILY_NAMES = (
    "water_year_fnf",
    "water_year_index",
    "ten_day_streamflow_volume_accumulation",
    "april_july_streamflow_volume_forecast",
)
ATTEMPT_OUTCOMES = {
    "success",
    "source_unavailable",
    "fetch_failed",
    "parse_failed",
    "validation_failed",
}
FAILURE_STAGES = {
    "source_unavailable": "source",
    "fetch_failed": "fetch",
    "parse_failed": "parse",
    "validation_failed": "validate",
}
METRIC_STATUSES = {
    "current",
    "source_stale",
    "stale_last_known_good",
    "expired",
    "unavailable",
    "failed_no_data",
}
ORIGINS = {"current_source", "last_known_good", "none"}
STATE_KEYS = {
    "status",
    "value_origin",
    "source_issue_at",
    "source_data_updated_at",
    "valid_through",
    "stale_since",
    "map_eligible",
    "popup_eligible",
    "missing_reason",
}
ROOT_KEYS = {
    "schema_version",
    "product_id",
    "roster_version",
    "generated_at",
    "publication_mode",
    "expected_record_count",
    "actual_record_count",
    "source_summary",
    "family_health",
    "operational_notices",
    "records",
}
VOLATILE_RECORD_KEYS = {
    "last_successful_retrieval_at",
    "last_attempt_at",
    "attempt_outcome",
    "failure_stage",
    "metric_state",
    "status",
    "value_origin",
    "valid_through",
    "stale_since",
    "missing_reason",
    "diagnostic",
}


def _read_roster() -> list[dict[str, str]]:
    with ROSTER_PATH.open(encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row.get("enabled") == "TRUE"]
    if len(rows) != 51:
        raise RFCProductError("reviewed CNRFC roster must contain exactly 51 enabled records")
    keys = [row["forecast_key"] for row in rows]
    if len(keys) != len(set(keys)):
        raise RFCProductError("reviewed CNRFC roster contains duplicate keys")
    return rows


ROSTER = _read_roster()
EXPECTED_KEYS = tuple(row["forecast_key"] for row in ROSTER)
ROSTER_BY_KEY = {row["forecast_key"]: row for row in ROSTER}


def metric_fields(product_type: str) -> tuple[str, ...]:
    if product_type == "ten_day_streamflow_volume_accumulation":
        return (
            "day_3_median_volume",
            "day_5_median_volume",
            "day_10_median_volume",
            "day_3_deterministic_volume",
            "day_5_deterministic_volume",
        )
    if product_type == "april_july_streamflow_volume_forecast":
        return (
            "forecast_volume",
            "percent_average",
            "percent_median",
            "normal_average_volume",
        )
    return ("forecast_volume", "percent_mean", "percent_median")


def source_anchor(record: Mapping[str, object]) -> str | None:
    if record.get("product_type") == "ten_day_streamflow_volume_accumulation":
        updated = record.get("source_data_updated_at")
        if isinstance(updated, str) and updated:
            return updated
    issued = record.get("forecast_issued_at")
    return issued if isinstance(issued, str) and issued else None


def _record_status(states: Mapping[str, Mapping[str, object]], outcome: str) -> str:
    statuses = [state.get("status") for state in states.values()]
    if not statuses:
        raise RFCProductError("CNRFC record has no metric states")
    if all(status == "current" for status in statuses):
        return "current"
    if any(status == "current" for status in statuses):
        return "current_partial"
    if any(status == "source_stale" for status in statuses):
        return "source_stale"
    if any(status == "stale_last_known_good" for status in statuses):
        return "stale_last_known_good"
    if any(status == "expired" for status in statuses):
        return "expired"
    if outcome == "source_unavailable" or all(status == "unavailable" for status in statuses):
        return "unavailable"
    return "failed_no_data"


def _record_origin(states: Mapping[str, Mapping[str, object]]) -> str:
    origins = {state.get("value_origin") for state in states.values()}
    if "last_known_good" in origins:
        return "last_known_good"
    if "current_source" in origins:
        return "current_source"
    return "none"


def _extreme_timestamp(values: Sequence[object], *, latest: bool) -> str | None:
    present = [value for value in values if isinstance(value, str) and value]
    if not present:
        return None
    return (max if latest else min)(present, key=lambda value: parse_instant(value, "metric time"))


def _has_value(record: Mapping[str, object]) -> bool:
    return any(record.get(field) is not None for field in metric_fields(str(record.get("product_type"))))


def _source_view(record: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in record.items() if key not in VOLATILE_RECORD_KEYS}


def _record_semantic_view(record: Mapping[str, object]) -> dict[str, object]:
    return {
        "forecast_key": record.get("forecast_key"),
        "source_anchor": source_anchor(record),
        "source_page_signature": record.get("source_page_signature"),
        "attempt_outcome": record.get("attempt_outcome"),
        "status": record.get("status"),
        "value_origin": record.get("value_origin"),
        "valid_through": record.get("valid_through"),
        "stale_since": record.get("stale_since"),
    }


def semantic_key(payload: Mapping[str, object]) -> str:
    return semantic_digest(payload, _record_semantic_view)


def _validate_state(record: Mapping[str, object], field: str) -> None:
    states = record.get("metric_state")
    if not isinstance(states, dict) or field not in states or not isinstance(states[field], dict):
        raise RFCProductError(f"CNRFC metric state is missing for {record.get('forecast_key')}/{field}")
    state = states[field]
    if set(state) != STATE_KEYS:
        raise RFCProductError(f"CNRFC metric state shape changed for {record.get('forecast_key')}/{field}")
    status = state.get("status")
    origin = state.get("value_origin")
    if status not in METRIC_STATUSES or origin not in ORIGINS:
        raise RFCProductError(f"CNRFC metric state enum is invalid for {record.get('forecast_key')}/{field}")
    for name in ("source_issue_at", "source_data_updated_at", "valid_through", "stale_since"):
        optional_instant(state.get(name), f"CNRFC {field} {name}")
    if state.get("source_issue_at") != record.get("forecast_issued_at"):
        raise RFCProductError(f"CNRFC source issue state disagrees for {record.get('forecast_key')}/{field}")
    expected_updated = (
        record.get("source_data_updated_at")
        if record.get("product_type") == "ten_day_streamflow_volume_accumulation"
        else None
    )
    if state.get("source_data_updated_at") != expected_updated:
        raise RFCProductError(f"CNRFC source update state disagrees for {record.get('forecast_key')}/{field}")
    value_present = record.get(field) is not None
    expected_map = value_present and status == "current"
    expected_popup = value_present and status in {
        "current",
        "source_stale",
        "stale_last_known_good",
        "expired",
    }
    if state.get("map_eligible") is not expected_map or state.get("popup_eligible") is not expected_popup:
        raise RFCProductError(f"CNRFC eligibility flags disagree for {record.get('forecast_key')}/{field}")
    if value_present and origin == "none":
        raise RFCProductError(f"CNRFC numeric metric lacks provenance for {record.get('forecast_key')}/{field}")
    if not value_present and origin != "none":
        raise RFCProductError(f"CNRFC null metric claims provenance for {record.get('forecast_key')}/{field}")


def _validate_record(record: Mapping[str, object], roster: Mapping[str, str]) -> None:
    key = roster["forecast_key"]
    for name, expected in (
        ("forecast_key", key),
        ("rfc", roster["rfc"]),
        ("nws_lid", roster["nws_lid"]),
        ("product_type", roster["product_type"]),
        ("display_name", roster["display_name"]),
        ("source_url", roster["source_url"]),
    ):
        if record.get(name) != expected:
            raise RFCProductError(f"CNRFC {key} changed reviewed {name}")
    outcome = record.get("attempt_outcome")
    if outcome not in ATTEMPT_OUTCOMES:
        raise RFCProductError(f"CNRFC {key} has an invalid attempt_outcome")
    expected_stage = None if outcome == "success" else FAILURE_STAGES[outcome]
    if record.get("failure_stage") != expected_stage:
        raise RFCProductError(f"CNRFC {key} failure_stage disagrees with outcome")
    parse_instant(record.get("last_attempt_at"), f"CNRFC {key} last_attempt_at")
    optional_instant(record.get("last_successful_retrieval_at"), f"CNRFC {key} last successful retrieval")
    optional_instant(record.get("forecast_issued_at"), f"CNRFC {key} forecast_issued_at")
    optional_instant(record.get("source_data_updated_at"), f"CNRFC {key} source_data_updated_at")
    optional_instant(record.get("valid_through"), f"CNRFC {key} valid_through")
    optional_instant(record.get("stale_since"), f"CNRFC {key} stale_since")

    product_type = roster["product_type"]
    fields = metric_fields(product_type)
    states = record.get("metric_state")
    if not isinstance(states, dict) or list(states) != list(fields):
        raise RFCProductError(f"CNRFC {key} metric_state ordering/shape changed")
    for field in fields:
        if field not in record:
            raise RFCProductError(f"CNRFC {key} is missing {field}")
        finite_nonnegative(record.get(field), f"CNRFC {key} {field}")
        _validate_state(record, field)
    if product_type == "ten_day_streamflow_volume_accumulation":
        if "day_10_deterministic_volume" in record:
            raise RFCProductError("CNRFC deterministic Day 10 is forbidden")
        for field in (
            "day_3_median_valid_date",
            "day_5_median_valid_date",
            "day_10_median_valid_date",
            "day_3_deterministic_valid_date",
            "day_5_deterministic_valid_date",
        ):
            if record.get(field) is not None:
                parse_date(record.get(field), f"CNRFC {key} {field}")
    if product_type == "april_july_streamflow_volume_forecast":
        if record.get("forecast_statistic") != "50_percent_exceedance" or record.get("forecast_period") != "April-July":
            raise RFCProductError(f"CNRFC {key} April-July semantics changed")
    elif product_type in {"water_year_fnf", "water_year_index"}:
        if record.get("forecast_statistic") != "median":
            raise RFCProductError(f"CNRFC {key} forecast statistic changed")

    status = _record_status(states, str(outcome))
    origin = _record_origin(states)
    valid_through = _extreme_timestamp([state.get("valid_through") for state in states.values()], latest=True)
    stale_since = _extreme_timestamp([state.get("stale_since") for state in states.values()], latest=False)
    if record.get("status") != status or record.get("value_origin") != origin:
        raise RFCProductError(f"CNRFC {key} aggregate status/origin does not reconcile")
    if record.get("valid_through") != valid_through or record.get("stale_since") != stale_since:
        raise RFCProductError(f"CNRFC {key} aggregate state times do not reconcile")
    signature = record.get("source_page_signature")
    if signature is not None:
        require_signature(signature, f"CNRFC {key} source_page_signature")
    if _has_value(record):
        if source_anchor(record) is None:
            raise RFCProductError(f"CNRFC {key} values lack an authoritative source anchor")
        require_signature(signature, f"CNRFC {key} source_page_signature")
    if not isinstance(record.get("diagnostic"), dict):
        raise RFCProductError(f"CNRFC {key} diagnostic must be an object")


def _source_summary(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    statuses = [record["status"] for record in records]
    success = lambda record: record.get("attempt_outcome") == "success"
    accumulation = [
        record for record in records
        if record.get("product_type") == "ten_day_streamflow_volume_accumulation"
    ]
    median = ("day_3_median_volume", "day_5_median_volume", "day_10_median_volume")
    deterministic = ("day_3_deterministic_volume", "day_5_deterministic_volume")
    family_success = lambda family: sum(
        success(record) and record.get("product_type") == family for record in records
    )
    return {
        "rfc": "CNRFC",
        "successful_attempt_count": sum(success(record) for record in records),
        "failed_attempt_count": sum(not success(record) for record in records),
        "water_year_fnf_success_count": family_success("water_year_fnf"),
        "water_year_index_success_count": family_success("water_year_index"),
        "april_july_success_count": family_success("april_july_streamflow_volume_forecast"),
        "accumulation_median_success_count": sum(
            success(record) and all(record.get(field) is not None for field in median)
            for record in accumulation
        ),
        "accumulation_deterministic_success_count": sum(
            success(record) and all(record.get(field) is not None for field in deterministic)
            for record in accumulation
        ),
        "current_count": statuses.count("current"),
        "current_partial_count": statuses.count("current_partial"),
        "source_stale_count": statuses.count("source_stale"),
        "stale_last_known_good_count": statuses.count("stale_last_known_good"),
        "expired_count": statuses.count("expired"),
        "source_unavailable_count": sum(record.get("attempt_outcome") == "source_unavailable" for record in records),
        "failed_no_data_count": statuses.count("failed_no_data"),
    }


def _april_july_active(generated_at: object) -> bool:
    month = parse_instant(generated_at, "CNRFC generated_at").astimezone(
        ZoneInfo("America/Los_Angeles")
    ).month
    return month <= 7 or month >= 10


def _family_summary(
    records: Sequence[Mapping[str, object]], family_name: str, generated_at: object
) -> dict[str, object]:
    family = [record for record in records if record.get("product_type") == family_name]
    statuses = [record["status"] for record in family]
    expected = {
        "water_year_fnf": 15,
        "water_year_index": 3,
        "ten_day_streamflow_volume_accumulation": 14,
        "april_july_streamflow_volume_forecast": 18 if _april_july_active(generated_at) else 0,
    }[family_name]
    successful = sum(record.get("attempt_outcome") == "success" for record in family)
    unavailable = sum(record.get("attempt_outcome") == "source_unavailable" for record in family)
    current = statuses.count("current")
    partial = statuses.count("current_partial")
    stale = statuses.count("source_stale")
    lkg = statuses.count("stale_last_known_good")
    expired = statuses.count("expired")
    failed = statuses.count("failed_no_data")
    inactive = family_name == "april_july_streamflow_volume_forecast" and expected == 0
    if inactive:
        healthy = (
            current == partial == stale == lkg == failed == 0
            and expired + unavailable == len(family)
            and successful + unavailable == len(family)
        )
    else:
        healthy = (
            current + partial == expected
            and unavailable == len(family) - expected
            and lkg == expired == stale == failed == 0
        )
    health = (
        "healthy" if healthy else
        "degraded" if current + partial + stale > 0 else
        "outage_using_last_known_good" if lkg + expired > 0 else
        "unusable"
    )
    summary: dict[str, object] = {
        "expected_structural_count": len(family),
        "expected_available_count": expected,
        "current_count": current,
        "current_partial_count": partial,
        "source_stale_count": stale,
        "stale_last_known_good_count": lkg,
        "expired_count": expired,
        "source_unavailable_count": unavailable,
        "failed_no_data_count": failed,
        "successful_attempt_count": successful,
        "failed_attempt_count": len(family) - successful,
        "health": health,
    }
    if family_name == "april_july_streamflow_volume_forecast":
        summary["season_state"] = "inactive" if inactive else "active"
    if family_name == "ten_day_streamflow_volume_accumulation":
        median = ("day_3_median_volume", "day_5_median_volume", "day_10_median_volume")
        deterministic = ("day_3_deterministic_volume", "day_5_deterministic_volume")
        summary["median_success_count"] = sum(
            record.get("attempt_outcome") == "success"
            and all(record.get(field) is not None for field in median)
            for record in family
        )
        summary["deterministic_success_count"] = sum(
            record.get("attempt_outcome") == "success"
            and all(record.get(field) is not None for field in deterministic)
            for record in family
        )
    return summary


def _notices(records: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    for record in records:
        if record.get("forecast_key") == "CNRFC:MHBC1:10D_VOLUME_ACCUM":
            primary = ("day_3_median_volume", "day_5_median_volume", "day_10_median_volume")
            if record.get("attempt_outcome") == "success" and all(
                record.get(field) is not None for field in primary
            ):
                return [{
                    "code": "product_2_expected_availability_changed",
                    "forecast_key": record["forecast_key"],
                    "message": "MHBC1 now publishes a valid product-2 median table; review the expected-availability roster.",
                }]
    return []


def rebuild_aggregates(payload: dict[str, object]) -> None:
    records = payload["records"]
    assert isinstance(records, list)
    payload["source_summary"] = _source_summary(records)
    payload["family_health"] = {
        family: _family_summary(records, family, payload["generated_at"])
        for family in FAMILY_NAMES
    }
    payload["operational_notices"] = _notices(records)


def validate_product(root: Path) -> dict[str, object]:
    payload = read_json(root.resolve() / PRODUCT_PATH, "CNRFC forecast")
    if set(payload) != ROOT_KEYS:
        raise RFCProductError("CNRFC payload contains missing or unexpected top-level fields")
    if (
        payload.get("schema_version") != "1.0"
        or payload.get("product_id") != PAYLOAD_PRODUCT_ID
        or payload.get("roster_version") != ROSTER_VERSION
        or payload.get("publication_mode") not in {"bootstrap", "steady_state"}
    ):
        raise RFCProductError("unsupported CNRFC schema/product/roster/publication mode")
    parse_instant(payload.get("generated_at"), "CNRFC generated_at")
    if payload.get("expected_record_count") != 51 or payload.get("actual_record_count") != 51:
        raise RFCProductError("CNRFC payload must declare exactly 51 records")
    by_key = records_by_key(payload, EXPECTED_KEYS, "CNRFC payload")
    for key in EXPECTED_KEYS:
        _validate_record(by_key[key], ROSTER_BY_KEY[key])
    records = payload["records"]
    assert isinstance(records, list)
    if payload.get("source_summary") != _source_summary(records):
        raise RFCProductError("CNRFC source_summary does not reconcile")
    expected_health = {
        family: _family_summary(records, family, payload["generated_at"])
        for family in FAMILY_NAMES
    }
    if payload.get("family_health") != expected_health:
        raise RFCProductError("CNRFC family_health does not reconcile")
    if payload.get("operational_notices") != _notices(records):
        raise RFCProductError("CNRFC operational_notices do not reconcile")
    recursive = json.dumps(records, ensure_ascii=False)
    if re.search(
        r'"[^"]*(?:geometry|polygon|coordinates|latitude|longitude|component|normal_value|ensemble_member|flow_rate)[^"]*"\s*:',
        recursive,
        re.IGNORECASE,
    ):
        raise RFCProductError("CNRFC payload contains a forbidden geometry/derived field")
    return payload


def _failure_reason(record: Mapping[str, object]) -> str:
    reason = record.get("missing_reason")
    if not isinstance(reason, str) or not reason:
        raise RFCProductError(f"CNRFC failed record {record.get('forecast_key')} lacks a reason")
    return reason


def _carry_failure(
    candidate: Mapping[str, object], canonical: Mapping[str, object]
) -> dict[str, object]:
    carried = copy.deepcopy(canonical)
    outcome = str(candidate["attempt_outcome"])
    attempt_text = str(candidate["last_attempt_at"])
    attempt = parse_instant(attempt_text, "CNRFC failure attempt")
    reason = _failure_reason(candidate)
    states: dict[str, dict[str, object]] = {}
    canonical_states = canonical.get("metric_state")
    assert isinstance(canonical_states, dict)
    for field in metric_fields(str(canonical["product_type"])):
        prior_state = canonical_states[field]
        assert isinstance(prior_state, dict)
        value_present = canonical.get(field) is not None
        valid_through = prior_state.get("valid_through")
        if not value_present:
            status = "unavailable" if outcome == "source_unavailable" else "failed_no_data"
            origin = "none"
            stale_since = None
        else:
            expired = (
                isinstance(valid_through, str)
                and attempt >= parse_instant(valid_through, "CNRFC LKG valid_through")
            )
            status = "expired" if expired else "stale_last_known_good"
            origin = "last_known_good"
            if expired:
                stale_since = valid_through
            elif prior_state.get("status") == "stale_last_known_good" and prior_state.get("stale_since"):
                stale_since = prior_state.get("stale_since")
            else:
                stale_since = attempt_text
        states[field] = {
            "status": status,
            "value_origin": origin,
            "source_issue_at": canonical.get("forecast_issued_at"),
            "source_data_updated_at": (
                canonical.get("source_data_updated_at")
                if canonical.get("product_type") == "ten_day_streamflow_volume_accumulation"
                else None
            ),
            "valid_through": valid_through,
            "stale_since": stale_since,
            "map_eligible": False,
            "popup_eligible": value_present and status in {"stale_last_known_good", "expired"},
            "missing_reason": reason,
        }
    carried["last_successful_retrieval_at"] = canonical.get("last_successful_retrieval_at")
    carried["last_attempt_at"] = attempt_text
    carried["attempt_outcome"] = outcome
    carried["failure_stage"] = candidate.get("failure_stage")
    carried["metric_state"] = states
    carried["status"] = _record_status(states, outcome)
    carried["value_origin"] = _record_origin(states)
    carried["valid_through"] = _extreme_timestamp(
        [state["valid_through"] for state in states.values()], latest=True
    )
    carried["stale_since"] = _extreme_timestamp(
        [state["stale_since"] for state in states.values()], latest=False
    )
    carried["missing_reason"] = reason
    carried["diagnostic"] = copy.deepcopy(candidate.get("diagnostic"))
    return carried


def _attempt(record: Mapping[str, object]) -> object:
    return parse_instant(record.get("last_attempt_at"), "CNRFC record attempt")


def _successful_retrieval(record: Mapping[str, object]) -> object:
    value = record.get("last_successful_retrieval_at")
    return parse_instant(value, "CNRFC successful retrieval") if value is not None else _attempt(record)


def select_record(
    candidate: Mapping[str, object], canonical: Mapping[str, object]
) -> tuple[dict[str, object], str]:
    candidate_attempt = _attempt(candidate)
    canonical_attempt = _attempt(canonical)
    if candidate.get("attempt_outcome") != "success":
        if candidate_attempt < canonical_attempt:
            return copy.deepcopy(canonical), "stale"
        if candidate_attempt == canonical_attempt:
            if candidate == canonical:
                return copy.deepcopy(canonical), "same"
            raise RFCProductError(
                f"equal-attempt CNRFC failure is ambiguous for {candidate.get('forecast_key')}"
            )
        carried = _carry_failure(candidate, canonical)
        return carried, "new" if carried != canonical else "same"

    candidate_anchor_text = source_anchor(candidate)
    if candidate_anchor_text is None:
        raise RFCProductError(f"successful CNRFC record lacks source anchor: {candidate.get('forecast_key')}")
    candidate_anchor = parse_instant(candidate_anchor_text, "candidate CNRFC source anchor")
    canonical_anchor_text = source_anchor(canonical)
    if canonical_anchor_text is None:
        return copy.deepcopy(candidate), "new"
    canonical_anchor = parse_instant(canonical_anchor_text, "canonical CNRFC source anchor")
    if candidate_anchor < canonical_anchor:
        return copy.deepcopy(canonical), "stale"
    if candidate_anchor > canonical_anchor:
        return copy.deepcopy(candidate), "new"

    candidate_signature = require_signature(
        candidate.get("source_page_signature"), "candidate CNRFC source_page_signature"
    )
    canonical_signature = require_signature(
        canonical.get("source_page_signature"), "canonical CNRFC source_page_signature"
    )
    candidate_retrieval = _successful_retrieval(candidate)
    canonical_retrieval = _successful_retrieval(canonical)
    if candidate_signature != canonical_signature:
        if candidate_retrieval < canonical_retrieval:
            return copy.deepcopy(canonical), "stale"
        if candidate_retrieval == canonical_retrieval:
            raise RFCProductError(
                f"equal-time CNRFC source revision is ambiguous for {candidate.get('forecast_key')}"
            )
        return copy.deepcopy(candidate), "new"
    if _source_view(candidate) != _source_view(canonical):
        raise RFCProductError(
            f"same CNRFC source issue/signature has different source fields for {candidate.get('forecast_key')}"
        )
    if candidate_attempt < canonical_attempt:
        return copy.deepcopy(canonical), "stale"
    if candidate_attempt == canonical_attempt:
        if candidate == canonical:
            return copy.deepcopy(canonical), "same"
        raise RFCProductError(
            f"same CNRFC source and attempt has ambiguous state for {candidate.get('forecast_key')}"
        )
    return (copy.deepcopy(canonical), "same") if candidate == canonical else (copy.deepcopy(candidate), "new")


def _substantive(payload: Mapping[str, object]) -> dict[str, object]:
    value = copy.deepcopy(payload)
    value.pop("generated_at", None)
    return value


def reconcile() -> None:
    candidate_root = Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"])
    worktree = Path(os.environ["BRIM_PUBLISH_WORKTREE"])
    metadata = read_json(Path(os.environ["BRIM_PUBLISH_METADATA"]), "candidate metadata")
    result_path = Path(os.environ["BRIM_PUBLISH_RESULT"])
    if os.environ.get("BRIM_PUBLISH_PRODUCT_ID") != PRODUCT_ID:
        raise RFCProductError("publisher product ID does not select the CNRFC callback")
    candidate = validate_product(candidate_root)
    require_metadata_semantic(metadata, SEMANTIC_TYPE, semantic_key(candidate))
    canonical_path = worktree / PRODUCT_PATH
    if not canonical_path.exists():
        destination = worktree / PRODUCT_PATH
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(candidate_root / PRODUCT_PATH, destination)
        write_result(
            result_path,
            decision="publish",
            state="new",
            reason="current main has no CNRFC canonical product",
        )
        return
    canonical = validate_product(worktree)
    candidate_records = records_by_key(candidate, EXPECTED_KEYS, "CNRFC candidate")
    canonical_records = records_by_key(canonical, EXPECTED_KEYS, "CNRFC canonical")
    final = copy.deepcopy(candidate)
    final_records: list[dict[str, object]] = []
    actions: list[str] = []
    for key in EXPECTED_KEYS:
        selected, action = select_record(candidate_records[key], canonical_records[key])
        final_records.append(selected)
        actions.append(action)
    final["records"] = final_records
    final["publication_mode"] = "steady_state"
    rebuild_aggregates(final)

    if _substantive(final) == _substantive(canonical):
        state = "stale" if "stale" in actions else "same"
        write_result(
            result_path,
            decision="no-op",
            state=state,
            reason="fresh main already contains every selected CNRFC record",
        )
        return
    write_json(canonical_path, final)
    validate_product(worktree)
    write_result(
        result_path,
        decision="publish",
        state="new",
        reason=(
            f"record reconciliation advanced {actions.count('new')} CNRFC record(s), "
            f"retained {actions.count('stale')} stale candidate record(s) from fresh main"
        ),
    )


def callback_main() -> int:
    phase = os.environ.get("BRIM_PUBLISH_PHASE")
    if phase == "validate-candidate":
        payload = validate_product(Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"]))
        print(f"Validated CNRFC candidate: {len(payload['records'])} records")
    elif phase == "reconcile":
        reconcile()
    elif phase == "validate-staged":
        payload = validate_product(Path(os.environ["BRIM_PUBLISH_WORKTREE"]))
        print(f"Validated staged CNRFC product: {len(payload['records'])} records")
    else:
        raise RFCProductError(f"unsupported CNRFC publisher phase: {phase!r}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--root", required=True)
    semantic = subparsers.add_parser("semantic-key")
    semantic.add_argument("--root", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if os.environ.get("BRIM_PUBLISH_PHASE"):
        try:
            return callback_main()
        except RFCProductError as exc:
            print(f"CNRFC_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
            return 1
    args = build_parser().parse_args(argv)
    try:
        payload = validate_product(Path(args.root))
        if args.command == "semantic-key":
            print(semantic_key(payload))
        else:
            print(f"Validated CNRFC forecast product: {len(payload['records'])} records")
        return 0
    except RFCProductError as exc:
        print(f"CNRFC_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
