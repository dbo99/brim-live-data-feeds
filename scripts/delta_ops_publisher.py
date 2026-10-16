#!/usr/bin/env python3
"""Validate and reconcile the fixed Delta Ops publication set."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Sequence, Tuple
from urllib.parse import urlsplit

from delta_ops_email import LABELS, PUBLIC_ATTRIBUTION, ReportError, normalize_values


PRODUCT_ID = "delta-ops-daily-summary"
VALUES_PATH = Path("docs/data/delta_ops_daily_summary.json")
FEATURES_PATH = Path("docs/data/delta_ops_daily_summary_features.geojson")
SUMMARY_PATH = Path("docs/data/delta_ops_daily_summary_summary.json")
X2_PATH = Path("docs/data/delta_ops_x2_reference.geojson")
ALLOWLIST = (VALUES_PATH, FEATURES_PATH, SUMMARY_PATH, X2_PATH)


class ProductError(RuntimeError):
    """A malformed or ambiguous Delta Ops state."""


def _read_json(path: Path) -> Dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise ProductError(f"missing plain Delta Ops file: {path}")
    try:
        def unique_object(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise ProductError("duplicate JSON object key in Delta Ops product")
                result[key] = item
            return result
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductError(f"unreadable JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProductError(f"JSON root must be an object: {path}")
    return value


def _date(value: object, label: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ProductError(f"{label} must be an ISO calendar date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ProductError(f"{label} is not a valid date: {value!r}") from exc


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        raise ProductError(f"{label} must be an RFC 3339 UTC timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ProductError(f"{label} is not a valid timestamp: {value!r}") from exc


def _point_geometry(feature: object, label: str) -> Dict[str, object]:
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise ProductError(f"{label} is not a GeoJSON Feature")
    geometry = feature.get("geometry")
    properties = feature.get("properties")
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        raise ProductError(f"{label} is not a Point")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) != 2:
        raise ProductError(f"{label} has invalid coordinates")
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
        raise ProductError(f"{label} coordinates are not finite lon/lat")
    if not isinstance(properties, dict):
        raise ProductError(f"{label} properties must be an object")
    return properties


def _feature_collection(value: Dict[str, object], label: str) -> list[object]:
    if value.get("type") != "FeatureCollection":
        raise ProductError(f"{label} must be a GeoJSON FeatureCollection")
    features = value.get("features")
    if not isinstance(features, list):
        raise ProductError(f"{label} features must be an array")
    return features


FEATURE_SPECS = {
    "cross_channel_gates": ("cross_channel_gates_percent_open", "percent"),
    "jones_cvp_exports": ("jones_pumping_plant", "cfs"),
    "banks_swp_exports": ("clifton_court_inflow", "cfs"),
    "delta_outflow_index": ("outflow_index", "cfs"),
    "percent_inflow_diverted": ("percent_inflow_diverted", "percent_3day_avg"),
    "omr_index": ("omr_index_daily_value", "cfs"),
    "sacramento_freeport": ("sacramento_river", "cfs"),
    "san_joaquin_vernalis": ("san_joaquin_river", "cfs"),
    "total_delta_inflow": ("east_side_streams", "cfs"),
    "delta_conditions": ("delta_conditions", "status"),
    "controlling_factors": ("controlling_factors", "status"),
    "san_luis_reservoir": ("san_luis_total_storage", "TAF"),
}
DERIVED_KEYS = {"east_side_streams", "export_total_cfs", "export_total_kaf_day",
                "outflow_kaf_day", "cvp_export_kaf_day", "swp_export_kaf_day", "san_luis_cvp_share"}


def _equal_number(actual, expected, label, *, tolerance=1e-9):
    if (isinstance(actual, bool) or not isinstance(actual, (int, float))
            or not math.isfinite(actual) or not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=tolerance)):
        raise ProductError(f"Delta Ops numeric mismatch: {label}")


def _email_evidence(values, root):
    # At preparation, reverify private raw/key evidence and bind all source
    # fields. No caller-supplied method/boolean alone authorizes an email product.
    if os.environ.get("DELTA_OPS_DKIM_EVIDENCE"):
        try:
            from delta_ops_gmail import load_verified_evidence
            record = load_verified_evidence(os.environ["DELTA_OPS_DKIM_EVIDENCE"]).record()
            if (record["report_date"] != values["report_date"] or record["review_only"]
                    or any(record["values"][k] != values["values"][k] for k in LABELS)):
                raise ValueError()
            return
        except Exception:
            raise ProductError("UNVERIFIED_SIGNATURE: private source binding failed") from None
    # Across jobs, the existing trusted preparation/artifact boundary carries
    # exact validated bytes. Recheck its full metadata and staged-byte binding;
    # raw MIME/key evidence is never uploaded or required by the publisher job.
    if os.environ.get("BRIM_PUBLISH_PHASE") not in {"validate-candidate", "reconcile", "validate-staged"}:
        raise ProductError("UNVERIFIED_SIGNATURE: no verified preparation evidence")
    try:
        from main_publisher import validate_candidate_metadata
        candidate = Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"]).resolve()
        metadata_path = Path(os.environ["BRIM_PUBLISH_METADATA"]).resolve()
        metadata = _read_json(metadata_path)
        validate_candidate_metadata(root=candidate, metadata_path=metadata_path,
                                    product_id=PRODUCT_ID, allowlist=[str(p) for p in ALLOWLIST],
                                    expected_source_sha=os.environ.get("GITHUB_SHA", metadata["source_event_sha"]))
        for entry in metadata["files"]:
            if hashlib.sha256((root / entry["path"]).read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError()
    except Exception:
        raise ProductError("UNVERIFIED_SIGNATURE: validated artifact binding failed") from None


def _validate_contract(values, summary, features, references, *, root, legacy=False, allow_review=False):
    by_key = {f["properties"]["feature_key"]: f["properties"] for f in features}
    expected = set(FEATURE_SPECS) | ({"x2_position_current"} if summary["x2_lookup_added"] else set())
    if set(by_key) != expected:
        raise ProductError("Delta Ops exact operational feature set is required")
    if set(values["values"]) != set(LABELS) | DERIVED_KEYS:
        raise ProductError("Delta Ops exact source and derived field set is required")
    source = {key: values["values"][key] for key in LABELS}
    if legacy:
        # Earlier PDF metadata retained a label on rows separated with '~'.
        if source["x2_position_yesterday"] is None:
            source["x2_position_yesterday"] = "Unavailable"
        for key, label in LABELS.items():
            if isinstance(source[key], str):
                source[key] = re.sub(r"^\s*" + re.escape(label) + r"\s*", "", source[key], flags=re.I)
    try:
        raw, numeric, x2 = normalize_values(source)
    except ReportError as exc:
        raise ProductError("Delta Ops required source values are invalid: " + exc.code) from None
    numeric["east_side_streams"] = numeric["total_delta_inflow"] - numeric["sacramento_river"] - numeric["san_joaquin_river"]
    if numeric["san_luis_swp_share"] > numeric["san_luis_total_storage"]:
        raise ProductError("Delta Ops reservoir shares exceed total storage")
    for key, (source_key, unit) in FEATURE_SPECS.items():
        feature = by_key[key]
        if feature["units"] != unit:
            raise ProductError("Delta Ops feature units disagree")
        actual, wanted = feature.get("value_numeric"), numeric[source_key]
        if wanted is None:
            if actual is not None or feature["value_raw"] != raw[source_key]:
                raise ProductError("Delta Ops status representation disagrees")
        elif legacy and key == "percent_inflow_diverted" and actual is None:
            pass  # Retained annotated-percentage parser defect; new inputs cannot opt in.
        else:
            _equal_number(actual, wanted, key, tolerance=0.0001 if legacy else 1e-9)
        if not legacy and source_key in raw and key != "san_luis_reservoir" and feature["value_raw"] != raw[source_key]:
            raise ProductError("Delta Ops raw feature value disagrees")
    expected_x2 = x2["km"] is not None
    if summary["x2_lookup_added"] != expected_x2 or bool(references) != expected_x2:
        raise ProductError("Delta Ops X2 availability disagrees")
    if expected_x2:
        point = by_key["x2_position_current"]
        from datetime import timedelta
        yesterday = (_date(summary["report_date"], "report date") - timedelta(days=1)).isoformat()
        for document in [values, summary, point]:
            if document.get("x2_position_date") != yesterday or document.get("x2_position_relation") != x2["relation"]:
                raise ProductError("Delta Ops X2 date or relation disagrees")
        _equal_number(summary.get("x2_position_km"), x2["km"], "X2 summary")
        _equal_number(point.get("value_numeric"), x2["km"], "X2 feature")
        if point["units"] != "km" or point["value_raw"] != x2["value_raw"]:
            raise ProductError("Delta Ops X2 raw value or units disagree")
        nearest = min(references, key=lambda f: (abs(f["properties"]["river_km"] - x2["km"]), f["properties"]["river_km"]))
        actual_point = next(f for f in features if f["properties"]["feature_key"] == "x2_position_current")
        if actual_point["geometry"] != nearest["geometry"]:
            raise ProductError("Delta Ops X2 placement disagrees with reference")
    elif summary.get("x2_position_km") is not None or summary.get("x2_position_relation") is not None:
        raise ProductError("Delta Ops unavailable X2 must be null")
    if not expected_x2 and (summary.get("x2_position_date") is not None or summary.get("x2_position_date_source") is not None):
        raise ProductError("Delta Ops unavailable X2 summary dates must be null")
    if legacy:
        return
    kind = values.get("source_kind")
    review = values.get("review_only")
    if kind not in {"pdf", "email"} or not isinstance(review, bool):
        raise ProductError("Delta Ops candidate source provenance is required")
    if kind == "email" and not review:
        documents = [values, summary, *by_key.values()]
        if any(d.get("source_authentication") != "dkim-rsa-sha256-full-body" for d in documents):
            raise ProductError("UNVERIFIED_SIGNATURE: email source method is required")
        _email_evidence(values, root)
    elif any("source_authentication" in d for d in [values, summary, *by_key.values()]):
        raise ProductError("PDF/review product cannot claim authenticated email provenance")
    if review and not allow_review:
        raise ProductError("saved-input review candidate cannot enter production publication")
    url = values.get("source_url", "")
    parsed_url = urlsplit(url)
    if (parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc or parsed_url.username
            or parsed_url.password or parsed_url.query or parsed_url.fragment
            or (kind == "email" and url != PUBLIC_ATTRIBUTION)):
        raise ProductError("Delta Ops source URL must be safe public attribution")
    for document in [values, summary, *by_key.values()]:
        if (document.get("source_kind") != kind or document.get("review_only") is not review
                or document.get("source_url") != url
                or document.get("source_name") != "DWR Delta Operations Daily Summary"):
            raise ProductError("Delta Ops provenance disagrees across outputs")
    report_provenance = f"parsed_from_dwr_{kind}_executive_operations_summary_on_line"
    x2_provenance = f"report_date_minus_1_day_because_{kind}_labels_x2_as_yesterday"
    for document in [values, summary]:
        if document.get("report_date_source") != report_provenance:
            raise ProductError("Delta Ops report date provenance is incorrect")
    expected_note = ("Automated BRIM daily parser for DWR Delta Operations Daily Summary "
                     + ("PDF" if kind == "pdf" else "email")
                     + "; values are preliminary and subject to revision.")
    if summary.get("parser_note") != expected_note:
        raise ProductError("Delta Ops parser provenance is incorrect")
    if expected_x2 and any(d.get("x2_position_date_source") != x2_provenance for d in [values, summary, by_key["x2_position_current"]]):
        raise ProductError("Delta Ops X2 date provenance is incorrect")
    conversion = 1.98347 / 1000
    totals = {"cvp_export_cfs": numeric["jones_pumping_plant"], "swp_export_cfs": numeric["clifton_court_inflow"]}
    totals["export_total_cfs"] = totals["cvp_export_cfs"] + totals["swp_export_cfs"]
    totals.update({key.replace("_cfs", "_kaf_day"): value * conversion for key, value in list(totals.items())})
    totals["outflow_kaf_day"] = numeric["outflow_index"] * conversion
    totals["east_side_streams_cfs"] = numeric["east_side_streams"]
    for key, wanted in totals.items():
        _equal_number(summary.get(key), wanted, key)
    derived = {"east_side_streams": (numeric["east_side_streams"], "cfs", 0),
               "export_total_cfs": (totals["export_total_cfs"], "cfs", 0),
               "san_luis_cvp_share": (numeric["san_luis_total_storage"] - numeric["san_luis_swp_share"], "TAF", 0)}
    derived.update({key: (wanted, "kaf/day", 1) for key, wanted in totals.items() if key.endswith("kaf_day")})
    for key, (wanted, unit, decimals) in derived.items():
        text = values["values"][key]
        suffix = r"(?:\.\d)?" if decimals else ""
        match = re.fullmatch(r"([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)" + suffix + r") " + re.escape(unit), str(text))
        if not match or abs(float(match[1].replace(",", "")) - wanted) > 0.5 * 10 ** -decimals + 1e-9:
            raise ProductError("Delta Ops derived display value disagrees")
    if by_key["percent_inflow_diverted"]["label_text"].count("(3-day avg)") != raw["percent_inflow_diverted"].count("(3-day avg)"):
        raise ProductError("Delta Ops percentage qualifier is duplicated or missing")


def validate_product(root: Path, *, legacy=False, allow_review=False) -> Tuple[str, int]:
    root = root.resolve()
    values = _read_json(root / VALUES_PATH)
    summary = _read_json(root / SUMMARY_PATH)
    feature_collection = _read_json(root / FEATURES_PATH)
    x2_collection = _read_json(root / X2_PATH)

    report_date = summary.get("report_date")
    _date(report_date, "Delta Ops report_date")
    build_time = summary.get("feed_build_time_utc")
    _timestamp(build_time, "Delta Ops feed_build_time_utc")
    for label, value in (("parsed values", values),):
        if value.get("report_date") != report_date:
            raise ProductError(f"Delta Ops {label} report date disagrees with summary")
        if value.get("feed_build_time_utc") != build_time:
            raise ProductError(f"Delta Ops {label} build time disagrees with summary")
        if not isinstance(value.get("source_name"), str) or not value["source_name"]:
            raise ProductError(f"Delta Ops {label} source name is missing")
        if not isinstance(value.get("source_url"), str) or not value["source_url"]:
            raise ProductError(f"Delta Ops {label} source URL is missing")
    if not isinstance(values.get("values"), dict) or not values["values"]:
        raise ProductError("Delta Ops parsed values object is empty")

    features = _feature_collection(feature_collection, "Delta Ops operational GeoJSON")
    if len(features) < 10:
        raise ProductError("Delta Ops operational GeoJSON has fewer than 10 features")
    if summary.get("feature_count") != len(features):
        raise ProductError("Delta Ops summary feature_count does not match GeoJSON")
    seen_keys = set()
    for index, feature in enumerate(features):
        properties = _point_geometry(feature, f"Delta Ops feature {index}")
        for key in (
            "feature_key",
            "display_name",
            "feature_type",
            "metric_name",
            "units",
            "label_text",
            "source_name",
            "source_url",
            "preliminary_notice",
        ):
            if not isinstance(properties.get(key), str) or not properties[key]:
                raise ProductError(f"Delta Ops feature {index} is missing string {key}")
        feature_key = properties["feature_key"]
        if feature_key in seen_keys:
            raise ProductError(f"Delta Ops feature {index} duplicates feature_key")
        seen_keys.add(feature_key)
        if properties.get("report_date") != report_date:
            raise ProductError(f"Delta Ops feature {index} report date disagrees")
        if properties.get("feed_build_time_utc") != build_time:
            raise ProductError(f"Delta Ops feature {index} build time disagrees")
        value_raw = properties.get("value_raw")
        if not isinstance(value_raw, str) or not value_raw:
            raise ProductError(f"Delta Ops feature {index} has invalid value_raw")
        value_numeric = properties.get("value_numeric")
        if "value_numeric" not in properties:
            raise ProductError(f"Delta Ops feature {index} is missing value_numeric")
        if value_numeric is not None and (
            isinstance(value_numeric, bool)
            or not isinstance(value_numeric, (int, float))
            or not math.isfinite(value_numeric)
        ):
            raise ProductError(f"Delta Ops feature {index} has invalid value_numeric")

    x2_features = _feature_collection(x2_collection, "Delta Ops X2 GeoJSON")
    x2_lookup_added = summary.get("x2_lookup_added")
    if not isinstance(x2_lookup_added, bool):
        raise ProductError("Delta Ops x2_lookup_added must be boolean")
    if x2_lookup_added and not x2_features:
        raise ProductError("Delta Ops summary declares X2 lookup without X2 features")
    for index, feature in enumerate(x2_features):
        properties = _point_geometry(feature, f"Delta Ops X2 feature {index}")
        for key in ("feature_key", "display_name", "feature_type", "source_name", "source_url"):
            if not isinstance(properties.get(key), str) or not properties[key]:
                raise ProductError(f"Delta Ops X2 feature {index} is missing string {key}")
        river_km = properties.get("river_km")
        if isinstance(river_km, bool) or not isinstance(river_km, (int, float)) or not math.isfinite(river_km):
            raise ProductError(f"Delta Ops X2 feature {index} has invalid river_km")

    _validate_contract(values, summary, features, x2_features, root=root, legacy=legacy, allow_review=allow_review)
    return str(report_date), len(features)


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
        raise ProductError("publisher product ID does not select the Delta Ops callback")

    candidate_date_text, _ = validate_product(candidate_root)
    semantic_key = metadata.get("semantic_key")
    if not isinstance(semantic_key, dict):
        raise ProductError("Delta Ops candidate metadata semantic key is missing")
    if semantic_key.get("type") != "report_date":
        raise ProductError("Delta Ops semantic key type must be report_date")
    if semantic_key.get("value") != candidate_date_text:
        raise ProductError("Delta Ops report date does not match artifact metadata")
    candidate_date = _date(candidate_date_text, "candidate Delta Ops report_date")

    canonical_presence = [(worktree / path).is_file() for path in ALLOWLIST]
    if not any(canonical_presence):
        state = "new"
    elif not all(canonical_presence):
        raise ProductError("current main contains a partial Delta Ops product set")
    else:
        canonical_date_text, _ = validate_product(worktree, legacy=True)
        canonical_date = _date(canonical_date_text, "canonical Delta Ops report_date")
        if candidate_date < canonical_date:
            _write_result(
                result_path,
                decision="no-op",
                state="stale",
                reason="candidate source report date is older than current main",
            )
            return
        if candidate_date == canonical_date:
            _write_result(
                result_path,
                decision="no-op",
                state="same",
                reason="candidate source report date already exists on current main",
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
        reason="candidate source report date is newer than current main",
    )


def callback_main() -> int:
    phase = os.environ.get("BRIM_PUBLISH_PHASE")
    if phase == "validate-candidate":
        report_date, count = validate_product(Path(os.environ["BRIM_PUBLISH_CANDIDATE_ROOT"]))
        print(f"Validated Delta Ops candidate: {count} features for {report_date}")
    elif phase == "reconcile":
        reconcile()
    elif phase == "validate-staged":
        report_date, count = validate_product(Path(os.environ["BRIM_PUBLISH_WORKTREE"]))
        print(f"Validated staged Delta Ops product: {count} features for {report_date}")
    else:
        raise ProductError(f"unsupported publisher callback phase: {phase!r}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--root", required=True)
    validate_parser.add_argument("--review", action="store_true", help="validate a nonpublishable private saved-input candidate")
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            report_date, count = validate_product(Path(args.root), allow_review=args.review)
            print(f"Validated Delta Ops product: {count} features for {report_date}")
            return 0
        if args.command is None:
            return callback_main()
        raise ProductError(f"unsupported command: {args.command}")
    except (KeyError, ProductError) as exc:
        print(f"DELTA_OPS_PUBLISHER_REJECTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
