#!/usr/bin/env python3
"""Validate and reconcile the CBRFC record-level forecast product."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import re
import shutil
import sys
from datetime import date
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


PRODUCT_ID = "cbrfc-major-water-supply-forecasts"
PAYLOAD_PRODUCT_ID = "cbrfc_major_water_supply_forecasts"
ROSTER_VERSION = "cbrfc-colorado-river-v1.3.0"
SEMANTIC_TYPE = "cbrfc_record_state_sha256_v1"
PRODUCT_PATH = Path("docs/data/cbrfc_major_water_supply_forecasts.json")
ROSTER_PATH = (
    Path(__file__).resolve().parents[1]
    / "data/input/cbrfc_major_water_supply_forecast_sources.csv"
)
FAMILY_NAMES = (
    "april_july_water_supply_forecast",
    "water_year_unregulated_inflow_forecast",
    "lake_mead_local_intervening_monthly_forecast",
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
STATUSES = {
    "current",
    "current_partial",
    "not_yet_valid",
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
    "source_issue_date",
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
VOLATILE_MONTH_KEYS = {
    "metric_state",
    "status",
    "value_origin",
    "stale_since",
    "missing_reason",
}


def _read_roster() -> list[dict[str, str]]:
    with ROSTER_PATH.open(encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row.get("enabled") == "TRUE"]
    if len(rows) != 3:
        raise RFCProductError("reviewed CBRFC roster must contain exactly three enabled records")
    keys = [row["forecast_key"] for row in rows]
    if len(keys) != len(set(keys)):
        raise RFCProductError("reviewed CBRFC roster contains duplicate keys")
    return rows


ROSTER = _read_roster()
EXPECTED_KEYS = tuple(row["forecast_key"] for row in ROSTER)
ROSTER_BY_KEY = {row["forecast_key"]: row for row in ROSTER}


def metric_fields(product_type: str) -> tuple[str, ...]:
    if product_type == "april_july_water_supply_forecast":
        return ("forecast_volume", "percent_average", "percent_median")
    if product_type == "water_year_unregulated_inflow_forecast":
        return ("forecast_volume", "percent_average")
    return ()


def _has_value(record: Mapping[str, object]) -> bool:
    if record.get("product_type") == "lake_mead_local_intervening_monthly_forecast":
        return bool(record.get("monthly_forecasts"))
    return any(record.get(field) is not None for field in metric_fields(str(record.get("product_type"))))


def _scalar_record_status(states: Mapping[str, Mapping[str, object]], outcome: str) -> str:
    statuses = [state.get("status") for state in states.values()]
    if all(status == "current" for status in statuses):
        return "current"
    if any(status == "source_stale" for status in statuses):
        return "source_stale"
    if any(status == "stale_last_known_good" for status in statuses):
        return "stale_last_known_good"
    if any(status == "expired" for status in statuses):
        return "expired"
    if outcome == "source_unavailable" or all(status == "unavailable" for status in statuses):
        return "unavailable"
    return "failed_no_data"


def _monthly_record_status(items: Sequence[Mapping[str, object]], outcome: str) -> str:
    if not items:
        return "unavailable" if outcome == "source_unavailable" else "failed_no_data"
    statuses = [item.get("status") for item in items]
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
    return "unavailable" if outcome == "source_unavailable" else "failed_no_data"


def _origin(states: Mapping[str, Mapping[str, object]]) -> str:
    origins = {state.get("value_origin") for state in states.values()}
    if "last_known_good" in origins:
        return "last_known_good"
    if "current_source" in origins:
        return "current_source"
    return "none"


def _record_semantic_view(record: Mapping[str, object]) -> dict[str, object]:
    return {
        "forecast_key": record.get("forecast_key"),
        "forecast_issue_date": record.get("forecast_issue_date"),
        "source_page_signature": record.get("source_page_signature"),
        "attempt_outcome": record.get("attempt_outcome"),
        "status": record.get("status"),
        "value_origin": record.get("value_origin"),
        "valid_through": record.get("valid_through"),
        "stale_since": record.get("stale_since"),
    }


def semantic_key(payload: Mapping[str, object]) -> str:
    return semantic_digest(payload, _record_semantic_view)


def _source_view(record: Mapping[str, object]) -> dict[str, object]:
    view = {key: value for key, value in record.items() if key not in VOLATILE_RECORD_KEYS}
    monthly = view.get("monthly_forecasts")
    if isinstance(monthly, list):
        view["monthly_forecasts"] = [
            {key: value for key, value in item.items() if key not in VOLATILE_MONTH_KEYS}
            for item in monthly
        ]
    return view


def _validate_state(
    state: object,
    *,
    value_present: bool,
    issue_date: object,
    label: str,
    allow_future: bool = False,
) -> None:
    if not isinstance(state, dict) or set(state) != STATE_KEYS:
        raise RFCProductError(f"{label} metric-state shape changed")
    status = state.get("status")
    if status not in STATUSES or (status == "not_yet_valid" and not allow_future):
        raise RFCProductError(f"{label} metric-state status is invalid")
    origin = state.get("value_origin")
    if origin not in ORIGINS:
        raise RFCProductError(f"{label} metric-state origin is invalid")
    if state.get("source_issue_date") != issue_date:
        raise RFCProductError(f"{label} metric-state issue date disagrees")
    optional_instant(state.get("valid_through"), f"{label} valid_through")
    optional_instant(state.get("stale_since"), f"{label} stale_since")
    expected_map = value_present and status == "current"
    expected_popup = value_present and status in {
        "current",
        "not_yet_valid",
        "source_stale",
        "stale_last_known_good",
        "expired",
    }
    if state.get("map_eligible") is not expected_map or state.get("popup_eligible") is not expected_popup:
        raise RFCProductError(f"{label} metric-state eligibility disagrees")
    if value_present != (origin != "none"):
        raise RFCProductError(f"{label} metric-state provenance disagrees with value")


def _month_sequence(issue_date: str) -> list[str]:
    start = parse_date(issue_date, "CBRFC monthly issue date")
    current = date(start.year, start.month, 1)
    values = []
    for _ in range(12):
        values.append(current.strftime("%Y-%m"))
        current = date(current.year + (current.month == 12), 1 if current.month == 12 else current.month + 1, 1)
    return values


def _validate_monthly(record: Mapping[str, object], key: str) -> None:
    items = record.get("monthly_forecasts")
    if not isinstance(items, list):
        raise RFCProductError(f"CBRFC {key} monthly_forecasts must be an array")
    outcome = str(record.get("attempt_outcome"))
    issue = record.get("forecast_issue_date")
    if items:
        if not isinstance(issue, str):
            raise RFCProductError(f"CBRFC {key} monthly values lack issue date")
        parse_date(issue, f"CBRFC {key} forecast_issue_date")
        if len(items) != 12:
            raise RFCProductError(f"CBRFC {key} must contain exactly 12 monthly items")
        months = [item.get("forecast_month") if isinstance(item, dict) else None for item in items]
        if months != _month_sequence(issue):
            raise RFCProductError(f"CBRFC {key} monthly sequence is not consecutive from issue month")
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise RFCProductError(f"CBRFC {key} monthly item {index} is not an object")
            finite_nonnegative(item.get("forecast_volume"), f"CBRFC {key} monthly volume", nullable=False)
            finite_nonnegative(item.get("percent_median"), f"CBRFC {key} monthly percent", nullable=False)
            if item.get("source_statistic") != "ESP 50% EXCEEDANCE" or item.get("source_percentage_label") != "%Med":
                raise RFCProductError(f"CBRFC {key} monthly source labels changed")
            if not isinstance(item.get("raw_forecast_month_label"), str):
                raise RFCProductError(f"CBRFC {key} monthly raw label is missing")
            optional_instant(item.get("valid_from"), f"CBRFC {key} monthly valid_from")
            optional_instant(item.get("valid_through"), f"CBRFC {key} monthly valid_through")
            optional_instant(item.get("stale_since"), f"CBRFC {key} monthly stale_since")
            states = item.get("metric_state")
            if not isinstance(states, dict) or list(states) != ["forecast_volume", "percent_median"]:
                raise RFCProductError(f"CBRFC {key} monthly metric-state ordering changed")
            for field in ("forecast_volume", "percent_median"):
                _validate_state(
                    states[field],
                    value_present=True,
                    issue_date=issue,
                    label=f"CBRFC {key}/{months[index]}/{field}",
                    allow_future=True,
                )
            expected_origin = _origin(states)
            if item.get("value_origin") != expected_origin:
                raise RFCProductError(f"CBRFC {key} monthly origin does not reconcile")
            status = item.get("status")
            state_statuses = {state.get("status") for state in states.values()}
            if state_statuses != {status}:
                raise RFCProductError(f"CBRFC {key} monthly status does not reconcile")
    expected_status = _monthly_record_status(items, outcome)
    if record.get("status") != expected_status:
        raise RFCProductError(f"CBRFC {key} monthly record status does not reconcile")
    expected_origin = "none" if not items else (
        "current_source" if all(item.get("value_origin") == "current_source" for item in items)
        else "last_known_good"
    )
    if record.get("value_origin") != expected_origin:
        raise RFCProductError(f"CBRFC {key} monthly record origin does not reconcile")
    expected_valid = items[-1].get("valid_through") if items else None
    if record.get("valid_through") != expected_valid:
        raise RFCProductError(f"CBRFC {key} monthly record validity does not reconcile")


def _validate_record(record: Mapping[str, object], roster: Mapping[str, str]) -> None:
    key = roster["forecast_key"]
    for name, expected in (
        ("forecast_key", key),
        ("rfc", roster["rfc"]),
        ("nws_lid", roster["nws_lid"]),
        ("product_type", roster["product_type"]),
        ("display_name", roster["display_name"]),
        ("forecast_period", roster["forecast_period"]),
        ("source_normal_term", roster["source_normal_term"]),
        ("forecast_type", roster["expected_forecast_type"]),
    ):
        if record.get(name) != expected:
            raise RFCProductError(f"CBRFC {key} changed reviewed {name}")
    for name in ("source_url", "retrieval_url", "summary_url", "archive_url"):
        value = record.get(name)
        if not isinstance(value, str) or not value.startswith("https://www.cbrfc.noaa.gov/"):
            raise RFCProductError(f"CBRFC {key} has an invalid {name}")
    outcome = record.get("attempt_outcome")
    if outcome not in ATTEMPT_OUTCOMES:
        raise RFCProductError(f"CBRFC {key} has an invalid attempt_outcome")
    expected_stage = None if outcome == "success" else FAILURE_STAGES[str(outcome)]
    if record.get("failure_stage") != expected_stage:
        raise RFCProductError(f"CBRFC {key} failure_stage disagrees with outcome")
    parse_instant(record.get("last_attempt_at"), f"CBRFC {key} last_attempt_at")
    optional_instant(record.get("last_successful_retrieval_at"), f"CBRFC {key} successful retrieval")
    if record.get("forecast_issue_date") is not None:
        parse_date(record.get("forecast_issue_date"), f"CBRFC {key} forecast_issue_date")
    optional_instant(record.get("valid_through"), f"CBRFC {key} valid_through")
    optional_instant(record.get("stale_since"), f"CBRFC {key} stale_since")
    if record.get("source_time_precision") != "date":
        raise RFCProductError(f"CBRFC {key} source time precision changed")

    product_type = roster["product_type"]
    if product_type == "lake_mead_local_intervening_monthly_forecast":
        if record.get("forecast_statistic") != "50_percent_exceedance":
            raise RFCProductError(f"CBRFC {key} monthly statistic changed")
        _validate_monthly(record, key)
    else:
        fields = metric_fields(product_type)
        states = record.get("metric_state")
        if not isinstance(states, dict) or list(states) != list(fields):
            raise RFCProductError(f"CBRFC {key} metric-state ordering/shape changed")
        for field in fields:
            if field not in record:
                raise RFCProductError(f"CBRFC {key} is missing {field}")
            finite_nonnegative(record.get(field), f"CBRFC {key} {field}")
            _validate_state(
                states[field],
                value_present=record.get(field) is not None,
                issue_date=record.get("forecast_issue_date"),
                label=f"CBRFC {key}/{field}",
            )
        if record.get("status") != _scalar_record_status(states, str(outcome)):
            raise RFCProductError(f"CBRFC {key} scalar status does not reconcile")
        if record.get("value_origin") != _origin(states):
            raise RFCProductError(f"CBRFC {key} scalar origin does not reconcile")
        if product_type == "april_july_water_supply_forecast":
            if record.get("forecast_statistic") != "50_percent_exceedance":
                raise RFCProductError(f"CBRFC {key} April-July statistic changed")
        elif record.get("forecast_statistic") != "official_full_forecast" or "percent_median" in record:
            raise RFCProductError(f"CBRFC {key} water-year contract changed")
    signature = record.get("source_page_signature")
    if signature is not None:
        require_signature(signature, f"CBRFC {key} source_page_signature")
    if _has_value(record):
        if record.get("forecast_issue_date") is None:
            raise RFCProductError(f"CBRFC {key} values lack forecast_issue_date")
        require_signature(signature, f"CBRFC {key} source_page_signature")
    if not isinstance(record.get("diagnostic"), dict):
        raise RFCProductError(f"CBRFC {key} diagnostic must be an object")


def _source_summary(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    statuses = [record.get("status") for record in records]
    outcomes = [record.get("attempt_outcome") for record in records]
    return {
        "rfc": "CBRFC",
        "successful_attempt_count": outcomes.count("success"),
        "failed_attempt_count": len(outcomes) - outcomes.count("success"),
        "current_count": statuses.count("current"),
        "current_partial_count": statuses.count("current_partial"),
        "source_stale_count": statuses.count("source_stale"),
        "stale_last_known_good_count": statuses.count("stale_last_known_good"),
        "expired_count": statuses.count("expired"),
        "source_unavailable_count": outcomes.count("source_unavailable"),
        "failed_no_data_count": statuses.count("failed_no_data"),
    }


def _active(product_type: str, generated_at: object) -> bool:
    month = parse_instant(generated_at, "CBRFC generated_at").astimezone(
        ZoneInfo("America/Los_Angeles")
    ).month
    if product_type == "april_july_water_supply_forecast":
        return month <= 7
    if product_type == "water_year_unregulated_inflow_forecast":
        return month <= 9
    return True


def _family_summary(record: Mapping[str, object], generated_at: object) -> dict[str, object]:
    active = _active(str(record["product_type"]), generated_at)
    status = str(record["status"])
    success = record.get("attempt_outcome") == "success"
    unavailable = record.get("attempt_outcome") == "source_unavailable"
    healthy = (
        success and status in {"current", "current_partial", "source_stale"}
        if active
        else (success and status == "expired") or (unavailable and status == "unavailable")
    )
    health = (
        "healthy" if healthy else
        "degraded" if status in {"current", "current_partial", "source_stale"} else
        "outage_using_last_known_good" if status in {"stale_last_known_good", "expired"} and not success else
        "unusable"
    )
    return {
        "expected_structural_count": 1,
        "expected_available_count": 1 if active else 0,
        "current_count": int(status == "current"),
        "current_partial_count": int(status == "current_partial"),
        "source_stale_count": int(status == "source_stale"),
        "stale_last_known_good_count": int(status == "stale_last_known_good"),
        "expired_count": int(status == "expired"),
        "source_unavailable_count": int(unavailable),
        "failed_no_data_count": int(status == "failed_no_data"),
        "successful_attempt_count": int(success),
        "failed_attempt_count": int(not success),
        "health": health,
        "season_state": "active" if active else "inactive",
    }


def _notices(records: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    notices: list[dict[str, object]] = []
    messages = {
        "lagging": "The Lake Powell dashboard lagged the accepted structured official issue; the dashboard was not used to replace the newer primary values.",
        "missing_field": "The Lake Powell dashboard omitted a direct Apr-Jul cross-check field; the complete structured official point remained authoritative.",
        "fetch_failed": "The Lake Powell dashboard could not be retrieved for secondary cross-checking; the complete structured official point remained authoritative.",
        "unavailable_or_unparseable": "The Lake Powell dashboard was unavailable or not parseable for secondary cross-checking; the complete structured official point remained authoritative.",
    }
    for record in records:
        if (
            record.get("product_type") == "april_july_water_supply_forecast"
            and record.get("attempt_outcome") == "success"
        ):
            diagnostic = record.get("diagnostic")
            status = diagnostic.get("dashboard_crosscheck_status") if isinstance(diagnostic, dict) else None
            if status is not None and status != "matched":
                status_text = str(status)
                notices.append({
                    "notice_id": "CBRFC_GLDA3_APR_JUL_DASHBOARD_" + re.sub(r"[^A-Za-z0-9]+", "_", status_text).upper(),
                    "forecast_key": record["forecast_key"],
                    "notice_type": "summary_crosscheck",
                    "severity": "info" if status_text in {"lagging", "missing_field"} else "warning",
                    "message": messages.get(status_text, "The Lake Powell dashboard cross-check was not complete."),
                    "evidence_url": record.get("summary_url"),
                })
        if record.get("product_type") == "lake_mead_local_intervening_monthly_forecast":
            items = record.get("monthly_forecasts")
            corrected = [item for item in items if item.get("source_date_override_applied") is True] if isinstance(items, list) else []
            if len(corrected) == 1:
                item = corrected[0]
                notices.append({
                    "notice_id": item.get("source_date_override_id"),
                    "forecast_key": record["forecast_key"],
                    "notice_type": "reviewed_source_date_override",
                    "severity": "warning",
                    "message": item.get("source_date_override_reason"),
                    "evidence_url": item.get("source_date_override_evidence_url"),
                })
    return notices


def rebuild_aggregates(payload: dict[str, object]) -> None:
    records = payload["records"]
    assert isinstance(records, list)
    payload["source_summary"] = _source_summary(records)
    payload["family_health"] = {
        str(record["product_type"]): _family_summary(record, payload["generated_at"])
        for record in records
    }
    payload["operational_notices"] = _notices(records)


def validate_product(root: Path) -> dict[str, object]:
    payload = read_json(root.resolve() / PRODUCT_PATH, "CBRFC forecast")
    if set(payload) != ROOT_KEYS:
        raise RFCProductError("CBRFC payload contains missing or unexpected top-level fields")
    if (
        payload.get("schema_version") != "1.0"
        or payload.get("product_id") != PAYLOAD_PRODUCT_ID
        or payload.get("roster_version") != ROSTER_VERSION
        or payload.get("publication_mode") not in {"bootstrap", "steady_state"}
    ):
        raise RFCProductError("unsupported CBRFC schema/product/roster/publication mode")
    parse_instant(payload.get("generated_at"), "CBRFC generated_at")
    if payload.get("expected_record_count") != 3 or payload.get("actual_record_count") != 3:
        raise RFCProductError("CBRFC payload must declare exactly three records")
    by_key = records_by_key(payload, EXPECTED_KEYS, "CBRFC payload")
    for key in EXPECTED_KEYS:
        _validate_record(by_key[key], ROSTER_BY_KEY[key])
    records = payload["records"]
    assert isinstance(records, list)
    if payload.get("source_summary") != _source_summary(records):
        raise RFCProductError("CBRFC source_summary does not reconcile")
    expected_health = {
        str(record["product_type"]): _family_summary(record, payload["generated_at"])
        for record in records
    }
    if payload.get("family_health") != expected_health:
        raise RFCProductError("CBRFC family_health does not reconcile")
    if payload.get("operational_notices") != _notices(records):
        raise RFCProductError("CBRFC operational_notices do not reconcile")
    recursive = json.dumps(records, ensure_ascii=False)
    if re.search(
        r'"[^"]*(?:geometry|polygon|coordinates|latitude|longitude|huc|dateline|^esp|ensemble_member)[^"]*"\s*:',
        recursive,
        re.IGNORECASE,
    ):
        raise RFCProductError("CBRFC payload contains a forbidden geometry/raw-guidance field")
    return payload


def _failure_reason(record: Mapping[str, object]) -> str:
    reason = record.get("missing_reason")
    if not isinstance(reason, str) or not reason:
        raise RFCProductError(f"CBRFC failed record {record.get('forecast_key')} lacks a reason")
    return reason


def _carry_scalar_failure(
    candidate: Mapping[str, object], canonical: Mapping[str, object]
) -> dict[str, object]:
    carried = copy.deepcopy(canonical)
    outcome = str(candidate["attempt_outcome"])
    attempt_text = str(candidate["last_attempt_at"])
    attempt = parse_instant(attempt_text, "CBRFC failure attempt")
    reason = _failure_reason(candidate)
    prior_states = canonical.get("metric_state")
    assert isinstance(prior_states, dict)
    states: dict[str, dict[str, object]] = {}
    for field in metric_fields(str(canonical["product_type"])):
        prior_state = prior_states[field]
        assert isinstance(prior_state, dict)
        valid_through = prior_state.get("valid_through")
        value_present = canonical.get(field) is not None
        if not value_present:
            status = "unavailable" if outcome == "source_unavailable" else "failed_no_data"
            origin = "none"
            stale_since = None
        else:
            expired = (
                isinstance(valid_through, str)
                and attempt >= parse_instant(valid_through, "CBRFC LKG valid_through")
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
            "source_issue_date": canonical.get("forecast_issue_date"),
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
    carried["status"] = _scalar_record_status(states, outcome)
    carried["value_origin"] = _origin(states)
    carried["stale_since"] = next(
        (state["stale_since"] for state in states.values() if state["stale_since"] is not None),
        None,
    )
    carried["missing_reason"] = reason
    carried["diagnostic"] = copy.deepcopy(candidate.get("diagnostic"))
    return carried


def _carry_monthly_failure(
    candidate: Mapping[str, object], canonical: Mapping[str, object]
) -> dict[str, object]:
    carried = copy.deepcopy(canonical)
    outcome = str(candidate["attempt_outcome"])
    attempt_text = str(candidate["last_attempt_at"])
    attempt = parse_instant(attempt_text, "CBRFC monthly failure attempt")
    reason = _failure_reason(candidate)
    items = carried.get("monthly_forecasts")
    assert isinstance(items, list)
    for item in items:
        valid_through = item.get("valid_through")
        expired = isinstance(valid_through, str) and attempt >= parse_instant(
            valid_through, "CBRFC monthly LKG valid_through"
        )
        status = "expired" if expired else "stale_last_known_good"
        stale_since = (
            valid_through if expired else
            item.get("stale_since") if item.get("status") == "stale_last_known_good" and item.get("stale_since") else
            attempt_text
        )
        states = {
            field: {
                "status": status,
                "value_origin": "last_known_good",
                "source_issue_date": carried.get("forecast_issue_date"),
                "valid_through": valid_through,
                "stale_since": stale_since,
                "map_eligible": False,
                "popup_eligible": True,
                "missing_reason": reason,
            }
            for field in ("forecast_volume", "percent_median")
        }
        item["metric_state"] = states
        item["status"] = status
        item["value_origin"] = "last_known_good"
        item["stale_since"] = stale_since
        item["missing_reason"] = reason
    carried["last_successful_retrieval_at"] = canonical.get("last_successful_retrieval_at")
    carried["last_attempt_at"] = attempt_text
    carried["attempt_outcome"] = outcome
    carried["failure_stage"] = candidate.get("failure_stage")
    carried["status"] = _monthly_record_status(items, outcome)
    carried["value_origin"] = "last_known_good" if items else "none"
    carried["valid_through"] = items[-1].get("valid_through") if items else None
    carried["stale_since"] = next((item.get("stale_since") for item in items if item.get("stale_since")), None)
    carried["missing_reason"] = reason
    carried["diagnostic"] = copy.deepcopy(candidate.get("diagnostic"))
    return carried


def _carry_failure(
    candidate: Mapping[str, object], canonical: Mapping[str, object]
) -> dict[str, object]:
    if canonical.get("product_type") == "lake_mead_local_intervening_monthly_forecast":
        return _carry_monthly_failure(candidate, canonical)
    return _carry_scalar_failure(candidate, canonical)


def _attempt(record: Mapping[str, object]):
    return parse_instant(record.get("last_attempt_at"), "CBRFC record attempt")


def _successful_retrieval(record: Mapping[str, object]):
    value = record.get("last_successful_retrieval_at")
    return parse_instant(value, "CBRFC successful retrieval") if value is not None else _attempt(record)


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
                f"equal-attempt CBRFC failure is ambiguous for {candidate.get('forecast_key')}"
            )
        carried = _carry_failure(candidate, canonical)
        return carried, "new" if carried != canonical else "same"

    candidate_issue_text = candidate.get("forecast_issue_date")
    if not isinstance(candidate_issue_text, str):
        raise RFCProductError(f"successful CBRFC record lacks issue date: {candidate.get('forecast_key')}")
    candidate_issue = parse_date(candidate_issue_text, "candidate CBRFC issue date")
    canonical_issue_text = canonical.get("forecast_issue_date")
    if not isinstance(canonical_issue_text, str):
        return copy.deepcopy(candidate), "new"
    canonical_issue = parse_date(canonical_issue_text, "canonical CBRFC issue date")
    if candidate_issue < canonical_issue:
        return copy.deepcopy(canonical), "stale"
    if candidate_issue > canonical_issue:
        return copy.deepcopy(candidate), "new"
    candidate_signature = require_signature(
        candidate.get("source_page_signature"), "candidate CBRFC source_page_signature"
    )
    canonical_signature = require_signature(
        canonical.get("source_page_signature"), "canonical CBRFC source_page_signature"
    )
    candidate_retrieval = _successful_retrieval(candidate)
    canonical_retrieval = _successful_retrieval(canonical)
    if candidate_signature != canonical_signature:
        if candidate_retrieval < canonical_retrieval:
            return copy.deepcopy(canonical), "stale"
        if candidate_retrieval == canonical_retrieval:
            raise RFCProductError(
                f"equal-time CBRFC source revision is ambiguous for {candidate.get('forecast_key')}"
            )
        return copy.deepcopy(candidate), "new"
    if _source_view(candidate) != _source_view(canonical):
        raise RFCProductError(
            f"same CBRFC issue/signature has different source fields for {candidate.get('forecast_key')}"
        )
    if candidate_attempt < canonical_attempt:
        return copy.deepcopy(canonical), "stale"
    if candidate_attempt == canonical_attempt:
        if candidate == canonical:
            return copy.deepcopy(canonical), "same"
        raise RFCProductError(
            f"same CBRFC source and attempt has ambiguous state for {candidate.get('forecast_key')}"
        )
    return (copy.deepcopy(canonical), "same") if candidate == canonical else (copy.deepcopy(candidate), "new")


def _substantive(payload: Mapping[str, object]) -> dict[str, object]:
    value = copy.deepcopy(payload)
    value.pop("generated_at", None)
    value.pop("publication_mode", None)
    return value


def reconcile() -> None:
    candidate_root = Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"])
    worktree = Path(os.environ["BRIM_PUBLISH_WORKTREE"])
    metadata = read_json(Path(os.environ["BRIM_PUBLISH_METADATA"]), "candidate metadata")
    result_path = Path(os.environ["BRIM_PUBLISH_RESULT"])
    if os.environ.get("BRIM_PUBLISH_PRODUCT_ID") != PRODUCT_ID:
        raise RFCProductError("publisher product ID does not select the CBRFC callback")
    candidate = validate_product(candidate_root)
    require_metadata_semantic(metadata, SEMANTIC_TYPE, semantic_key(candidate))
    canonical_path = worktree / PRODUCT_PATH
    if not canonical_path.exists():
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(candidate_root / PRODUCT_PATH, canonical_path)
        write_result(
            result_path,
            decision="publish",
            state="new",
            reason="current main has no CBRFC canonical product",
        )
        return
    canonical = validate_product(worktree)
    candidate_records = records_by_key(candidate, EXPECTED_KEYS, "CBRFC candidate")
    canonical_records = records_by_key(canonical, EXPECTED_KEYS, "CBRFC canonical")
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
            reason="fresh main already contains every selected CBRFC record",
        )
        return
    write_json(canonical_path, final)
    validate_product(worktree)
    write_result(
        result_path,
        decision="publish",
        state="new",
        reason=(
            f"record reconciliation advanced {actions.count('new')} CBRFC record(s), "
            f"retained {actions.count('stale')} stale candidate record(s) from fresh main"
        ),
    )


def callback_main() -> int:
    phase = os.environ.get("BRIM_PUBLISH_PHASE")
    if phase == "validate-candidate":
        payload = validate_product(Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"]))
        print(f"Validated CBRFC candidate: {len(payload['records'])} records")
    elif phase == "reconcile":
        reconcile()
    elif phase == "validate-staged":
        payload = validate_product(Path(os.environ["BRIM_PUBLISH_WORKTREE"]))
        print(f"Validated staged CBRFC product: {len(payload['records'])} records")
    else:
        raise RFCProductError(f"unsupported CBRFC publisher phase: {phase!r}")
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
            print(f"CBRFC_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
            return 1
    args = build_parser().parse_args(argv)
    try:
        payload = validate_product(Path(args.root))
        if args.command == "semantic-key":
            print(semantic_key(payload))
        else:
            print(f"Validated CBRFC forecast product: {len(payload['records'])} records")
        return 0
    except RFCProductError as exc:
        print(f"CBRFC_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
