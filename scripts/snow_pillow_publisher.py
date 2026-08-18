#!/usr/bin/env python3
"""Validate and reconcile the provider-aware Snow Pillow product set."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Mapping, Sequence


PRODUCT_ID = "snow-pillow-latest"
PROVIDERS = ("cdec_snow_sensor", "nrcs_snotel")
GEOJSON_PATH = Path("docs/data/snow_pillow_latest.geojson")
SUMMARY_PATH = Path("docs/data/snow_pillow_latest_summary.json")
TRACE_PATH = Path("docs/data/snow_pillow_current_wy_trace.csv")
TRACE_SUMMARY_PATH = Path("docs/data/snow_pillow_current_wy_trace_summary.json")
ALLOWLIST = (GEOJSON_PATH, SUMMARY_PATH, TRACE_PATH, TRACE_SUMMARY_PATH)
TRACE_FIELDS = (
    "station_uid",
    "live_provider_key",
    "provider",
    "provider_station_id",
    "station_name",
    "water_year",
    "water_day",
    "obs_date_local",
    "swe_in",
    "source_element",
    "swe_source_class",
    "swe_source_label",
    "swe_source_note",
)
LATEST_REQUIRED_FIELDS = {
    "station_uid",
    "live_provider_key",
    "provider",
    "provider_station_id",
    "station_name",
    "current_water_year",
    "latitude",
    "longitude",
    "latest_swe_in",
    "latest_swe_date_local",
    "latest_swe_report_status",
    "latest_swe_source_class",
    "latest_swe_source_element",
    "latest_swe_source_label",
    "latest_swe_source_note",
    "latest_snow_depth_in",
    "latest_snow_depth_date_local",
    "feed_build_date_local",
    "feed_build_time_local",
    "fetch_start_date",
    "fetch_end_date",
    "swe_delta_1day_in",
    "swe_delta_3day_in",
    "swe_delta_7day_in",
    "swe_delta_suppressed_stale_latest",
}
REFRESH_PROVIDER_FIELDS = {
    "fetch_status",
    "qa_status",
    "publication_action",
    "fetch_started_at_utc",
    "fetch_completed_at_utc",
    "result_built_at_utc",
    "fresh_latest_rows",
    "fresh_trace_rows",
    "fresh_site_count",
    "carried_forward_latest_rows",
    "carried_forward_trace_rows",
    "carried_forward_site_count",
    "last_successful_data_preserved",
    "failure_reason",
}
LOCAL_BUILD_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}) (AM|PM) (PST|PDT)"
)
UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class ProductError(RuntimeError):
    """A malformed or ambiguous Snow Pillow publication state."""


@dataclass
class Product:
    root: Path
    geojson: Dict[str, object]
    features: list[Dict[str, object]]
    trace_rows: list[Dict[str, str]]
    summary: Dict[str, object]
    trace_summary: Dict[str, object]
    provider_features: Dict[str, list[Dict[str, object]]]
    provider_trace: Dict[str, list[Dict[str, str]]]
    provider_states: Dict[str, Dict[str, str]]
    actions: Dict[str, str]


def _read_json(path: Path) -> Dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise ProductError(f"missing plain Snow Pillow file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductError(f"unreadable JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProductError(f"JSON root must be an object: {path}")
    return value


def _read_trace(path: Path) -> list[Dict[str, str]]:
    if not path.is_file() or path.is_symlink():
        raise ProductError(f"missing plain Snow Pillow file: {path}")
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != TRACE_FIELDS:
                raise ProductError("Snow Pillow trace header is invalid")
            return list(reader)
    except (OSError, csv.Error) as exc:
        raise ProductError(f"unreadable Snow Pillow trace CSV: {exc}") from exc


def _count(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProductError(f"{label} must be a nonnegative integer")
    return value


def _number(
    value: object,
    label: str,
    *,
    nullable: bool = False,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProductError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ProductError(f"{label} must be a finite number")
    if minimum is not None and result < minimum:
        raise ProductError(f"{label} is below {minimum}")
    if maximum is not None and result > maximum:
        raise ProductError(f"{label} is above {maximum}")
    return result


def _date(value: object, label: str, *, nullable: bool = False) -> date | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        raise ProductError(f"{label} must be an ISO local date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ProductError(f"{label} must be an ISO local date") from exc


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not UTC_RE.fullmatch(value):
        raise ProductError(f"{label} must be an RFC 3339 UTC timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ProductError(f"{label} is not a valid timestamp") from exc


def _provider_build_time(value: object, label: str) -> tuple[str, datetime]:
    if not isinstance(value, str):
        raise ProductError(f"{label} is missing")
    match = LOCAL_BUILD_RE.fullmatch(value)
    if not match:
        raise ProductError(f"{label} has an invalid local build timestamp")
    day, hour_text, minute_text, am_pm, zone = match.groups()
    hour = int(hour_text) % 12 + (12 if am_pm == "PM" else 0)
    offset_hours = -7 if zone == "PDT" else -8
    parsed = datetime.fromisoformat(f"{day}T{hour:02d}:{minute_text}:00{offset_hours:+03d}:00")
    return value, parsed.astimezone(timezone.utc)


def _stable_digest(
    features: Iterable[Dict[str, object]], trace_rows: Iterable[Dict[str, str]]
) -> str:
    feature_values = sorted(
        features,
        key=lambda feature: str(feature.get("properties", {}).get("station_uid", "")),
    )
    trace_values = sorted(
        trace_rows,
        key=lambda row: (
            row.get("station_uid", ""),
            row.get("water_year", ""),
            int(row.get("water_day", "0")),
        ),
    )
    payload = {"features": feature_values, "trace": trace_values}
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _count_rows(values: Iterable[Mapping[str, object]], keys: Sequence[str], count_name: str) -> list[Dict[str, object]]:
    counts: Counter[tuple[object, ...]] = Counter(
        tuple(value.get(key) for key in keys) for value in values
    )
    return [
        {**dict(zip(keys, key_values)), count_name: count}
        for key_values, count in sorted(counts.items(), key=lambda item: tuple(str(v) for v in item[0]))
    ]


def _validate_refresh(
    summary: Dict[str, object],
    trace_summary: Dict[str, object],
    provider_features: Mapping[str, list[Dict[str, object]]],
    provider_trace: Mapping[str, list[Dict[str, str]]],
) -> Dict[str, str]:
    refresh = summary.get("refresh")
    if not isinstance(refresh, dict) or refresh != trace_summary.get("refresh"):
        raise ProductError("Snow Pillow refresh metadata is missing or differs between summaries")
    if refresh.get("mode") not in {"full", "partial"}:
        raise ProductError("Snow Pillow refresh mode must be full or partial")
    _utc(refresh.get("build_time_utc"), "Snow Pillow refresh build_time_utc")
    providers = refresh.get("providers")
    if not isinstance(providers, dict) or set(providers) != set(PROVIDERS):
        raise ProductError("Snow Pillow refresh provider inventory is invalid")

    actions: Dict[str, str] = {}
    for provider in PROVIDERS:
        value = providers[provider]
        if not isinstance(value, dict) or not REFRESH_PROVIDER_FIELDS.issubset(value):
            raise ProductError(f"Snow Pillow refresh metadata is incomplete for {provider}")
        action = value.get("publication_action")
        if action not in {"refreshed", "carried_forward"}:
            raise ProductError(f"Snow Pillow publication action is invalid for {provider}")
        for timestamp_name in (
            "fetch_started_at_utc",
            "fetch_completed_at_utc",
            "result_built_at_utc",
        ):
            _utc(value.get(timestamp_name), f"{provider} {timestamp_name}")
        for count_name in (
            "fresh_latest_rows",
            "fresh_trace_rows",
            "fresh_site_count",
            "carried_forward_latest_rows",
            "carried_forward_trace_rows",
            "carried_forward_site_count",
        ):
            _count(value.get(count_name), f"{provider} {count_name}")
        trace_sites = len({row["station_uid"] for row in provider_trace[provider]})
        if action == "refreshed":
            if value.get("fetch_status") != "success" or value.get("qa_status") != "passed":
                raise ProductError(f"refreshed provider {provider} did not pass fetch and QA")
            if value.get("failure_reason") is not None:
                raise ProductError(f"refreshed provider {provider} has a failure reason")
            if value.get("last_successful_data_preserved") is not False:
                raise ProductError(f"refreshed provider {provider} is marked carried")
            if any(
                value.get(name) != 0
                for name in (
                    "carried_forward_latest_rows",
                    "carried_forward_trace_rows",
                    "carried_forward_site_count",
                )
            ):
                raise ProductError(f"refreshed provider {provider} has carry-forward counts")
            if (
                value.get("fresh_latest_rows") != len(provider_features[provider])
                or value.get("fresh_trace_rows") != len(provider_trace[provider])
                or value.get("fresh_site_count") != trace_sites
            ):
                raise ProductError(f"refreshed provider {provider} counts are incoherent")
        else:
            healthy_carry = (
                value.get("fetch_status") == "success"
                and value.get("qa_status") == "passed"
            )
            if not isinstance(value.get("failure_reason"), str) or not value[
                "failure_reason"
            ].strip():
                raise ProductError(f"carried provider {provider} lacks a failure reason")
            if healthy_carry and value.get("failure_reason") != (
                "publisher preserved newer canonical provider state"
            ):
                raise ProductError(f"carried healthy provider {provider} has no reconciliation reason")
            if value.get("last_successful_data_preserved") is not True:
                raise ProductError(f"carried provider {provider} is not marked preserved")
            if (
                value.get("carried_forward_latest_rows") != len(provider_features[provider])
                or value.get("carried_forward_trace_rows") != len(provider_trace[provider])
                or value.get("carried_forward_site_count") != trace_sites
            ):
                raise ProductError(f"carried provider {provider} counts are incoherent")
        actions[provider] = action

    carried = sum(action == "carried_forward" for action in actions.values())
    if carried == 2:
        raise ProductError("both-provider failure may not form a Snow Pillow candidate")
    expected_mode = "partial" if carried else "full"
    if refresh.get("mode") != expected_mode:
        raise ProductError("Snow Pillow refresh mode disagrees with provider actions")
    if refresh.get("last_known_good_provider_data_preserved") is not bool(carried):
        raise ProductError("Snow Pillow last-known-good refresh flag is incoherent")
    return actions


def validate_product(root: Path) -> Product:
    root = root.resolve()
    geojson = _read_json(root / GEOJSON_PATH)
    summary = _read_json(root / SUMMARY_PATH)
    trace_rows = _read_trace(root / TRACE_PATH)
    trace_summary = _read_json(root / TRACE_SUMMARY_PATH)

    if geojson.get("type") != "FeatureCollection":
        raise ProductError("Snow Pillow GeoJSON must be a FeatureCollection")
    features_value = geojson.get("features")
    if not isinstance(features_value, list) or not features_value:
        raise ProductError("Snow Pillow GeoJSON has no features")
    if not trace_rows:
        raise ProductError("Snow Pillow current-water-year trace has no rows")

    max_swe = _number(summary.get("max_valid_swe_in"), "max_valid_swe_in", minimum=0)
    summary_fetch_start = _date(
        summary.get("fetch_start_date"), "Snow Pillow fetch start"
    )
    summary_fetch_end = _date(summary.get("fetch_end_date"), "Snow Pillow fetch end")
    if summary_fetch_start > summary_fetch_end:
        raise ProductError("Snow Pillow fetch date range is invalid")
    features: list[Dict[str, object]] = []
    provider_features: Dict[str, list[Dict[str, object]]] = {provider: [] for provider in PROVIDERS}
    stations: Dict[str, Dict[str, object]] = {}
    provider_builds: Dict[str, set[tuple[str, datetime]]] = {provider: set() for provider in PROVIDERS}
    latest_dates: Dict[str, list[date]] = {provider: [] for provider in PROVIDERS}

    for index, feature_value in enumerate(features_value):
        label = f"Snow Pillow feature {index}"
        if not isinstance(feature_value, dict) or feature_value.get("type") != "Feature":
            raise ProductError(f"{label} is invalid")
        geometry = feature_value.get("geometry")
        properties = feature_value.get("properties")
        if not isinstance(geometry, dict) or geometry.get("type") != "Point":
            raise ProductError(f"{label} geometry is not a Point")
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) != 2:
            raise ProductError(f"{label} coordinates are invalid")
        if not isinstance(properties, dict) or not LATEST_REQUIRED_FIELDS.issubset(properties):
            raise ProductError(f"{label} properties are incomplete")
        provider = properties.get("live_provider_key")
        if provider not in PROVIDERS:
            raise ProductError(f"{label} provider identity is invalid")
        station_uid = properties.get("station_uid")
        if not isinstance(station_uid, str) or not station_uid or station_uid in stations:
            raise ProductError(f"{label} station_uid is missing or duplicated")
        for field in ("provider", "provider_station_id", "station_name"):
            if not isinstance(properties.get(field), str) or not str(properties[field]).strip():
                raise ProductError(f"{label} {field} is missing")
        longitude = _number(properties.get("longitude"), f"{label} longitude", minimum=-180, maximum=180)
        latitude = _number(properties.get("latitude"), f"{label} latitude", minimum=-90, maximum=90)
        if abs(_number(coordinates[0], f"{label} geometry longitude") - longitude) > 1e-7 or abs(
            _number(coordinates[1], f"{label} geometry latitude") - latitude
        ) > 1e-7:
            raise ProductError(f"{label} geometry disagrees with coordinate properties")
        if properties.get("current_water_year") != summary.get("current_water_year"):
            raise ProductError(f"{label} water year disagrees with summary")
        _number(properties.get("latest_swe_in"), f"{label} latest SWE", nullable=True, minimum=0, maximum=max_swe)
        _number(properties.get("latest_snow_depth_in"), f"{label} latest snow depth", nullable=True, minimum=0)
        for field in ("swe_delta_1day_in", "swe_delta_3day_in", "swe_delta_7day_in"):
            _number(properties.get(field), f"{label} {field}", nullable=True)
        if properties.get("swe_delta_suppressed_stale_latest") is not None and not isinstance(
            properties.get("swe_delta_suppressed_stale_latest"), bool
        ):
            raise ProductError(f"{label} delta suppression flag is invalid")
        for field in ("latest_swe_date_local", "latest_snow_depth_date_local"):
            parsed = _date(properties.get(field), f"{label} {field}", nullable=True)
            if parsed is not None:
                if parsed < summary_fetch_start or parsed > summary_fetch_end:
                    raise ProductError(f"{label} {field} is outside the fetch window")
                latest_dates[provider].append(parsed)
        build = _provider_build_time(properties.get("feed_build_time_local"), f"{label} feed build time")
        provider_builds[provider].add(build)
        stations[station_uid] = properties
        provider_features[provider].append(feature_value)
        features.append(feature_value)

    if set(provider for provider, rows in provider_features.items() if rows) != set(PROVIDERS):
        raise ProductError("Snow Pillow latest product does not contain both provider families")
    for provider, values in provider_builds.items():
        if len(values) != 1:
            raise ProductError(f"Snow Pillow provider {provider} has mixed feature build times")

    provider_trace: Dict[str, list[Dict[str, str]]] = {provider: [] for provider in PROVIDERS}
    duplicate_keys: set[tuple[str, str, int, int]] = set()
    current_wy = summary.get("current_water_year")
    if isinstance(current_wy, bool) or not isinstance(current_wy, int):
        raise ProductError("Snow Pillow current water year is invalid")
    wy_start = date(current_wy - 1, 10, 1)
    for index, row in enumerate(trace_rows):
        label = f"Snow Pillow trace row {index}"
        provider = row.get("live_provider_key")
        if provider not in PROVIDERS:
            raise ProductError(f"{label} provider identity is invalid")
        station_uid = row.get("station_uid", "")
        station = stations.get(station_uid)
        if station is None:
            raise ProductError(f"{label} station is absent from latest product")
        for field in ("provider", "provider_station_id", "station_name"):
            if not row.get(field) or row[field] != str(station[field]):
                raise ProductError(f"{label} {field} disagrees with latest product")
        try:
            water_year = int(row["water_year"])
            water_day = int(row["water_day"])
            observation = date.fromisoformat(row["obs_date_local"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProductError(f"{label} date/water-year values are invalid") from exc
        if water_year != current_wy or water_day != (observation - wy_start).days + 1:
            raise ProductError(f"{label} water day is incoherent")
        if observation < summary_fetch_start or observation > summary_fetch_end:
            raise ProductError(f"{label} observation is outside the fetch window")
        key = (provider, station_uid, water_year, water_day)
        if key in duplicate_keys:
            raise ProductError("Snow Pillow trace has a duplicate provider/station/water-day key")
        duplicate_keys.add(key)
        try:
            swe = float(row["swe_in"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProductError(f"{label} SWE is invalid") from exc
        if not math.isfinite(swe) or swe < 0 or swe > max_swe:
            raise ProductError(f"{label} SWE is invalid")
        for field in ("source_element", "swe_source_class", "swe_source_label", "swe_source_note"):
            if not row.get(field, "").strip():
                raise ProductError(f"{label} {field} is missing")
        provider_trace[provider].append(row)
        latest_dates[provider].append(observation)
    if set(provider for provider, rows in provider_trace.items() if rows) != set(PROVIDERS):
        raise ProductError("Snow Pillow trace does not contain both provider families")

    required_summary = {
        "layer",
        "build_time_local",
        "build_date_local",
        "local_time_zone",
        "current_water_year",
        "fetch_start_date",
        "fetch_end_date",
        "station_rows",
        "latest_geojson_rows",
        "current_wy_trace_rows",
        "status_counts",
        "provider_status_counts",
        "qa_guardrails",
    }
    required_trace_summary = {
        "layer",
        "build_time_local",
        "build_date_local",
        "local_time_zone",
        "current_water_year",
        "fetch_start_date",
        "fetch_end_date",
        "current_wy_trace_rows",
        "stations_with_trace_rows",
        "providers",
    }
    if not required_summary.issubset(summary) or not required_trace_summary.issubset(trace_summary):
        raise ProductError("Snow Pillow summaries are incomplete")
    for field in (
        "build_time_local",
        "build_date_local",
        "local_time_zone",
        "current_water_year",
        "fetch_start_date",
        "fetch_end_date",
    ):
        if summary.get(field) != trace_summary.get(field):
            raise ProductError(f"Snow Pillow summary {field} values disagree")
    _date(summary.get("build_date_local"), "Snow Pillow build date")
    if (
        _count(summary.get("station_rows"), "station_rows") != len(features)
        or _count(summary.get("latest_geojson_rows"), "latest_geojson_rows") != len(features)
        or _count(summary.get("current_wy_trace_rows"), "current_wy_trace_rows") != len(trace_rows)
        or _count(trace_summary.get("current_wy_trace_rows"), "trace current_wy_trace_rows") != len(trace_rows)
        or _count(trace_summary.get("stations_with_trace_rows"), "stations_with_trace_rows")
        != len({row["station_uid"] for row in trace_rows})
    ):
        raise ProductError("Snow Pillow summary row counts are incoherent")
    if trace_summary.get("providers") != list(PROVIDERS):
        raise ProductError("Snow Pillow trace provider inventory is invalid")

    properties = [feature["properties"] for feature in features]
    expected_status = _count_rows(properties, ("latest_swe_report_status",), "n")
    expected_provider_status = _count_rows(
        properties, ("live_provider_key", "latest_swe_report_status"), "n"
    )
    if summary.get("status_counts") != expected_status or summary.get("provider_status_counts") != expected_provider_status:
        raise ProductError("Snow Pillow summary status counts are incoherent")
    expected_cdec_latest = _count_rows(
        [
            value
            for value in properties
            if value["live_provider_key"] == "cdec_snow_sensor"
            and value.get("latest_swe_source_class") is not None
        ],
        ("latest_swe_source_class", "latest_swe_source_label"),
        "stations",
    )
    expected_cdec_latest = [
        {
            "swe_source_class": value["latest_swe_source_class"],
            "swe_source_label": value["latest_swe_source_label"],
            "stations": value["stations"],
        }
        for value in expected_cdec_latest
    ]
    expected_cdec_trace = _count_rows(
        provider_trace["cdec_snow_sensor"],
        ("swe_source_class", "swe_source_label"),
        "rows",
    )
    if summary.get("cdec_selected_latest_source_counts") != expected_cdec_latest:
        raise ProductError("Snow Pillow CDEC latest-source counts are incoherent")
    if summary.get("cdec_trace_source_counts") != expected_cdec_trace or trace_summary.get(
        "cdec_trace_source_counts"
    ) != expected_cdec_trace:
        raise ProductError("Snow Pillow CDEC trace-source counts are incoherent")

    aggregate_counts = {
        "rows_with_latest_swe": sum(value.get("latest_swe_in") is not None for value in properties),
        "rows_without_latest_swe": sum(value.get("latest_swe_in") is None for value in properties),
        "rows_reported_zero": sum(value.get("latest_swe_report_status") == "reported_zero" for value in properties),
        "rows_reported_positive": sum(value.get("latest_swe_report_status") == "reported_positive" for value in properties),
        "rows_missing_recent_value": sum(value.get("latest_swe_report_status") == "missing_recent_value" for value in properties),
        "rows_outside_snow_season_no_recent_report": sum(value.get("latest_swe_report_status") == "outside_snow_season_no_recent_report" for value in properties),
        "rows_stale_last_value": sum(value.get("latest_swe_report_status") == "stale_last_value" for value in properties),
        "rows_with_1day_delta": sum(value.get("swe_delta_1day_in") is not None for value in properties),
        "rows_with_3day_delta": sum(value.get("swe_delta_3day_in") is not None for value in properties),
        "rows_with_7day_delta": sum(value.get("swe_delta_7day_in") is not None for value in properties),
        "rows_with_1day_delta_current_display": sum(value.get("swe_delta_1day_in") is not None for value in properties),
        "rows_with_3day_delta_current_display": sum(value.get("swe_delta_3day_in") is not None for value in properties),
        "rows_with_7day_delta_current_display": sum(value.get("swe_delta_7day_in") is not None for value in properties),
        "rows_suppressed_delta_stale_latest": sum(value.get("swe_delta_suppressed_stale_latest") is True for value in properties),
        "latest_rows_with_fixed_pct_median": sum(value.get("normal_fixed_pct_median_swe") is not None for value in properties),
        "latest_rows_with_rolling_pct_median": sum(value.get("normal_rolling_pct_median_swe") is not None for value in properties),
    }
    for field, expected in aggregate_counts.items():
        if _count(summary.get(field), field) != expected:
            raise ProductError(f"Snow Pillow summary {field} is incoherent")

    guardrails = summary.get("qa_guardrails")
    if not isinstance(guardrails, dict) or not isinstance(guardrails.get("combined_output_qa"), dict):
        raise ProductError("Snow Pillow combined-output QA metadata is missing")
    combined = guardrails["combined_output_qa"]
    expected_combined = {
        "passed": True,
        "problems": [],
        "latest_rows": len(features),
        "trace_rows": len(trace_rows),
        "latest_sites": len(stations),
        "trace_sites": len({row["station_uid"] for row in trace_rows}),
    }
    if combined != expected_combined:
        raise ProductError("Snow Pillow combined-output QA is incoherent")
    if guardrails.get("observed_current_wy_trace_rows") != len(trace_rows) or guardrails.get(
        "observed_latest_swe_rows"
    ) != aggregate_counts["rows_with_latest_swe"]:
        raise ProductError("Snow Pillow observed output QA counts are incoherent")

    actions = _validate_refresh(summary, trace_summary, provider_features, provider_trace)
    provider_states: Dict[str, Dict[str, str]] = {}
    for provider in PROVIDERS:
        build_text, build_time = next(iter(provider_builds[provider]))
        provider_states[provider] = {
            "observation_date": max(latest_dates[provider]).isoformat(),
            "provider_build_time_local": build_text,
            "provider_build_time_utc": build_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "stable_digest": _stable_digest(provider_features[provider], provider_trace[provider]),
        }

    return Product(
        root=root,
        geojson=geojson,
        features=features,
        trace_rows=trace_rows,
        summary=summary,
        trace_summary=trace_summary,
        provider_features=provider_features,
        provider_trace=provider_trace,
        provider_states=provider_states,
        actions=actions,
    )


def semantic_payload(product: Product) -> Dict[str, object]:
    refresh_providers = product.summary["refresh"]["providers"]
    return {
        "schema_version": 1,
        "validation_result": "passed",
        "providers": {
            provider: {
                "publication_action": product.actions[provider],
                "fetch_status": refresh_providers[provider]["fetch_status"],
                "qa_status": refresh_providers[provider]["qa_status"],
                "included_state": product.provider_states[provider],
            }
            for provider in PROVIDERS
        },
    }


def semantic_key(product: Product) -> str:
    return json.dumps(
        semantic_payload(product), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _validate_metadata(metadata_path: Path, product: Product) -> None:
    metadata = _read_json(metadata_path)
    key = metadata.get("semantic_key")
    if not isinstance(key, dict) or key.get("type") != "snow_provider_state_v1":
        raise ProductError("Snow Pillow candidate metadata semantic key type is invalid")
    if key.get("value") != semantic_key(product):
        raise ProductError("Snow Pillow candidate provider state does not match artifact metadata")


def _compare_provider(candidate: Dict[str, str], canonical: Dict[str, str]) -> str:
    candidate_date = date.fromisoformat(candidate["observation_date"])
    canonical_date = date.fromisoformat(canonical["observation_date"])
    if candidate_date > canonical_date:
        return "new"
    if candidate_date < canonical_date:
        return "stale"
    candidate_build = _utc(candidate["provider_build_time_utc"], "candidate provider build")
    canonical_build = _utc(canonical["provider_build_time_utc"], "canonical provider build")
    if candidate_build > canonical_build:
        return "new"
    if candidate_build < canonical_build:
        return "stale"
    if candidate["stable_digest"] != canonical["stable_digest"]:
        raise ProductError(
            "equal Snow Pillow provider semantic key has different content digest"
        )
    return "same"


def _update_reconstructed_summaries(
    candidate: Product,
    features: list[Dict[str, object]],
    trace_rows: list[Dict[str, str]],
    selected_from: Mapping[str, str],
) -> tuple[Dict[str, object], Dict[str, object]]:
    summary = copy.deepcopy(candidate.summary)
    trace_summary = copy.deepcopy(candidate.trace_summary)
    properties = [feature["properties"] for feature in features]
    trace_sites = len({row["station_uid"] for row in trace_rows})
    summary["station_rows"] = len(features)
    summary["latest_geojson_rows"] = len(features)
    summary["current_wy_trace_rows"] = len(trace_rows)
    trace_summary["current_wy_trace_rows"] = len(trace_rows)
    trace_summary["stations_with_trace_rows"] = trace_sites
    trace_summary["providers"] = list(PROVIDERS)
    selected_dates = [date.fromisoformat(row["obs_date_local"]) for row in trace_rows]
    for value in properties:
        for field in ("latest_swe_date_local", "latest_snow_depth_date_local"):
            if value.get(field):
                selected_dates.append(date.fromisoformat(value[field]))
    reconstructed_fetch_end = max(
        date.fromisoformat(str(summary["fetch_end_date"])), *selected_dates
    ).isoformat()
    summary["fetch_end_date"] = reconstructed_fetch_end
    trace_summary["fetch_end_date"] = reconstructed_fetch_end

    summary["status_counts"] = _count_rows(properties, ("latest_swe_report_status",), "n")
    summary["provider_status_counts"] = _count_rows(
        properties, ("live_provider_key", "latest_swe_report_status"), "n"
    )
    cdec_latest = _count_rows(
        [
            value
            for value in properties
            if value["live_provider_key"] == "cdec_snow_sensor"
            and value.get("latest_swe_source_class") is not None
        ],
        ("latest_swe_source_class", "latest_swe_source_label"),
        "stations",
    )
    summary["cdec_selected_latest_source_counts"] = [
        {
            "swe_source_class": value["latest_swe_source_class"],
            "swe_source_label": value["latest_swe_source_label"],
            "stations": value["stations"],
        }
        for value in cdec_latest
    ]
    cdec_trace = _count_rows(
        [row for row in trace_rows if row["live_provider_key"] == "cdec_snow_sensor"],
        ("swe_source_class", "swe_source_label"),
        "rows",
    )
    summary["cdec_trace_source_counts"] = cdec_trace
    trace_summary["cdec_trace_source_counts"] = cdec_trace

    updates = {
        "rows_with_latest_swe": sum(value.get("latest_swe_in") is not None for value in properties),
        "rows_without_latest_swe": sum(value.get("latest_swe_in") is None for value in properties),
        "rows_reported_zero": sum(value.get("latest_swe_report_status") == "reported_zero" for value in properties),
        "rows_reported_positive": sum(value.get("latest_swe_report_status") == "reported_positive" for value in properties),
        "rows_missing_recent_value": sum(value.get("latest_swe_report_status") == "missing_recent_value" for value in properties),
        "rows_outside_snow_season_no_recent_report": sum(value.get("latest_swe_report_status") == "outside_snow_season_no_recent_report" for value in properties),
        "rows_stale_last_value": sum(value.get("latest_swe_report_status") == "stale_last_value" for value in properties),
        "rows_with_1day_delta": sum(value.get("swe_delta_1day_in") is not None for value in properties),
        "rows_with_3day_delta": sum(value.get("swe_delta_3day_in") is not None for value in properties),
        "rows_with_7day_delta": sum(value.get("swe_delta_7day_in") is not None for value in properties),
        "rows_with_1day_delta_current_display": sum(value.get("swe_delta_1day_in") is not None for value in properties),
        "rows_with_3day_delta_current_display": sum(value.get("swe_delta_3day_in") is not None for value in properties),
        "rows_with_7day_delta_current_display": sum(value.get("swe_delta_7day_in") is not None for value in properties),
        "rows_suppressed_delta_stale_latest": sum(value.get("swe_delta_suppressed_stale_latest") is True for value in properties),
        "latest_rows_with_fixed_pct_median": sum(value.get("normal_fixed_pct_median_swe") is not None for value in properties),
        "latest_rows_with_rolling_pct_median": sum(value.get("normal_rolling_pct_median_swe") is not None for value in properties),
    }
    summary.update(updates)
    guardrails = summary["qa_guardrails"]
    guardrails["observed_current_wy_trace_rows"] = len(trace_rows)
    guardrails["observed_latest_swe_rows"] = updates["rows_with_latest_swe"]
    guardrails["combined_output_qa"] = {
        "passed": True,
        "problems": [],
        "latest_rows": len(features),
        "trace_rows": len(trace_rows),
        "latest_sites": len(features),
        "trace_sites": trace_sites,
    }

    refresh = summary["refresh"]
    any_canonical = False
    for provider in PROVIDERS:
        value = refresh["providers"][provider]
        provider_features = [
            feature
            for feature in features
            if feature["properties"]["live_provider_key"] == provider
        ]
        provider_trace = [row for row in trace_rows if row["live_provider_key"] == provider]
        sites = len({row["station_uid"] for row in provider_trace})
        if selected_from[provider] == "canonical":
            any_canonical = True
            was_refreshed = value["publication_action"] == "refreshed"
            value["publication_action"] = "carried_forward"
            value["carried_forward_latest_rows"] = len(provider_features)
            value["carried_forward_trace_rows"] = len(provider_trace)
            value["carried_forward_site_count"] = sites
            value["last_successful_data_preserved"] = True
            if was_refreshed:
                value["failure_reason"] = (
                    "publisher preserved newer canonical provider state"
                )
        else:
            value["publication_action"] = "refreshed"
            value["carried_forward_latest_rows"] = 0
            value["carried_forward_trace_rows"] = 0
            value["carried_forward_site_count"] = 0
            value["last_successful_data_preserved"] = False
    refresh["mode"] = "partial" if any_canonical else "full"
    refresh["last_known_good_provider_data_preserved"] = any_canonical
    trace_summary["refresh"] = copy.deepcopy(refresh)
    return summary, trace_summary


def _write_product(
    worktree: Path,
    candidate: Product,
    canonical: Product,
    selected_from: Mapping[str, str],
) -> None:
    if all(value == "candidate" for value in selected_from.values()):
        for relative_path in ALLOWLIST:
            destination = worktree / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(candidate.root / relative_path, destination)
        return

    features: list[Dict[str, object]] = []
    trace_rows: list[Dict[str, str]] = []
    for provider in PROVIDERS:
        source = candidate if selected_from[provider] == "candidate" else canonical
        features.extend(copy.deepcopy(source.provider_features[provider]))
        trace_rows.extend(copy.deepcopy(source.provider_trace[provider]))
    features.sort(
        key=lambda feature: (
            feature["properties"]["live_provider_key"],
            feature["properties"]["station_name"],
            feature["properties"]["provider_station_id"],
        )
    )
    trace_rows.sort(
        key=lambda row: (row["station_uid"], row["obs_date_local"])
    )
    summary, trace_summary = _update_reconstructed_summaries(
        candidate, features, trace_rows, selected_from
    )
    geojson = {"type": "FeatureCollection", "features": features}

    (worktree / GEOJSON_PATH).write_text(
        json.dumps(geojson, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    (worktree / SUMMARY_PATH).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (worktree / TRACE_PATH).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRACE_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(trace_rows)
    (worktree / TRACE_SUMMARY_PATH).write_text(
        json.dumps(trace_summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_result(path: Path, decision: str, state: str, reason: str) -> None:
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
    metadata_path = Path(os.environ["BRIM_PUBLISH_METADATA"])
    result_path = Path(os.environ["BRIM_PUBLISH_RESULT"])
    if os.environ.get("BRIM_PUBLISH_PRODUCT_ID") != PRODUCT_ID:
        raise ProductError("publisher product ID does not select the Snow Pillow callback")
    candidate = validate_product(candidate_root)
    _validate_metadata(metadata_path, candidate)

    canonical_presence = [(worktree / path).is_file() for path in ALLOWLIST]
    if any(canonical_presence) and not all(canonical_presence):
        raise ProductError("current main contains a partial Snow Pillow product set")
    canonical = validate_product(worktree) if all(canonical_presence) else None

    outcomes: Dict[str, str] = {}
    selected_from: Dict[str, str] = {}
    for provider in PROVIDERS:
        if candidate.actions[provider] == "carried_forward":
            if canonical is None or not canonical.provider_features[provider] or not canonical.provider_trace[provider]:
                raise ProductError(f"fresh main lacks canonical carry-forward rows for {provider}")
            outcomes[provider] = "carried_from_fresh_main"
            selected_from[provider] = "canonical"
            continue
        if canonical is None:
            outcomes[provider] = "new"
            selected_from[provider] = "candidate"
            continue
        outcome = _compare_provider(
            candidate.provider_states[provider], canonical.provider_states[provider]
        )
        outcomes[provider] = outcome
        selected_from[provider] = "canonical" if outcome == "stale" else "candidate"

    new_providers = [provider for provider, outcome in outcomes.items() if outcome == "new"]
    if not new_providers:
        state = "stale" if "stale" in outcomes.values() else "same"
        _write_result(
            result_path,
            "no-op",
            state,
            "provider reconciliation produced no NEW state: "
            + ", ".join(f"{provider}={outcomes[provider]}" for provider in PROVIDERS),
        )
        return
    if canonical is None and any(value == "canonical" for value in selected_from.values()):
        raise ProductError("first Snow Pillow publication cannot require canonical carry-forward")
    if canonical is None:
        for relative_path in ALLOWLIST:
            destination = worktree / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(candidate.root / relative_path, destination)
    else:
        _write_product(worktree, candidate, canonical, selected_from)
    _write_result(
        result_path,
        "publish",
        "new",
        "provider reconciliation selected NEW content without regression: "
        + ", ".join(f"{provider}={outcomes[provider]}" for provider in PROVIDERS),
    )


def callback_main() -> int:
    phase = os.environ.get("BRIM_PUBLISH_PHASE")
    if phase == "validate-candidate":
        product = validate_product(Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"]))
        _validate_metadata(Path(os.environ["BRIM_PUBLISH_METADATA"]), product)
        print("Validated Snow Pillow candidate and provider metadata.")
    elif phase == "reconcile":
        reconcile()
    elif phase == "validate-staged":
        product = validate_product(Path(os.environ["BRIM_PUBLISH_WORKTREE"]))
        print(
            "Validated staged Snow Pillow product: "
            f"{len(product.features)} stations, {len(product.trace_rows)} trace rows."
        )
    else:
        raise ProductError(f"unsupported Snow Pillow publisher callback phase: {phase!r}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--root", required=True)
    semantic_parser = subparsers.add_parser("semantic-key")
    semantic_parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            product = validate_product(Path(args.root))
            print(
                "Validated Snow Pillow product: "
                f"{len(product.features)} stations, {len(product.trace_rows)} trace rows."
            )
            return 0
        if args.command == "semantic-key":
            print(semantic_key(validate_product(Path(args.root))))
            return 0
        if args.command is None:
            return callback_main()
        raise ProductError(f"unsupported command: {args.command}")
    except (KeyError, ProductError) as exc:
        print(f"SNOW_PILLOW_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
