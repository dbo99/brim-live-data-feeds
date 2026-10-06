"""Offline, local-only NRCS AWDB SNOTEL DAILY/END preservation.

Contract snotel-awdb-history-1 is NOT a public feed or a collector. No HTTP,
soilDB, interpolation, depth composites, display conversion or revision winner.

build_history(metadata_body, metadata_sha256, captures) accepts pinned station
metadata bytes and captures of {body: bytes, receipt: dict, receipt_sha256: str}.
Each receipt has request={method, url, parameters}, retrieved_at_utc (UTC ISO),
http_status (integer or null), complete (bool), error (string or null),
response_bytes and response_sha256. Additional receipt provenance is retained.
The caller must verify completeness and pin receipts from acquisition evidence;
hashes cannot authenticate a caller who changes both evidence and trusted pins.

History fields: contract, adapter_version, three false publication-status flags,
metadata={body_base64, sha256}, sensors, captures, observations, query_ledger.
Sensors preserve exact signed stationTriplet/SMS/heightDepth/ordinal identity,
station identifiers, native stationElement and metadata hash. Captures retain
exact bytes (base64), receipt, receipt hash and request identity. Observations
retain native record/element and field presence, flags, value, date, fixed GMT-08
END timestamp, identity, units and query/retrieval/hash provenance. Ledger entries
record per-capture/per-sensor status, returned/omitted dates and failure reason.
coverage_on returns ALL overlapping events; no evidence yields UNQUERIED.

write_archive creates a fresh directory with exactly history.json and
manifest.json (both canonical JSON and explicitly ineligible for publication).
open_archive requires the independently retained manifest SHA-256 and rebuilds
all derived content from pinned raw inputs. No replacement of existing output.
This bounded layout allows 32 captures, 32 sensors, 366 days/query, 1 MiB/body,
8 MiB total response bytes, 20,000 rows and 64 MiB history; no statewide claim.
"""

import base64
import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

CONTRACT = "snotel-awdb-history-1"
ADAPTER_VERSION = "snotel-awdb-offline-1.0.0"
TARGET = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data"
MAX_BODY = 1024 * 1024
MAX_HISTORY = 64 * 1024 * 1024
STATUS = dict(publication_eligible=False, consumer_accepted=False,
              historical_backfill_complete=False)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(body):
    return hashlib.sha256(body).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("nonfinite JSON constant: " + value)


def _finite_float(value):
    number = float(value)
    require(math.isfinite(number), "nonfinite JSON number")
    return number


def _json(body):
    return json.loads(body.decode("utf-8"), object_pairs_hook=_object,
                      parse_constant=_reject_constant, parse_float=_finite_float)


def _date(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value),
            "date must be native YYYY-MM-DD")
    return date.fromisoformat(value)


def source_timestamp(provider_date):
    """END date D represents next midnight at fixed GMT-08, never DST."""
    return (_date(provider_date) + timedelta(days=1)).isoformat() + "T08:00:00Z"


def _utc(value):
    require(isinstance(value, str), "retrieval time required")
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(instant.utcoffset() == timedelta(0), "retrieval time must be UTC")
    return instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sensor_identity(triplet, depth, ordinal):
    require(isinstance(triplet, str) and
            re.fullmatch(r"\d+:[A-Z]{2}:SNTL", triplet), "SNOTEL triplet required")
    require(type(depth) is int and depth < 0, "signed negative soil depth required")
    require(type(ordinal) is int and ordinal > 0, "positive exact ordinal required")
    return f"{triplet}|SMS:{depth}:{ordinal}"


def _element(triplet, element):
    require(isinstance(element, dict), "stationElement required")
    require(element.get("elementCode") == "SMS" and
            element.get("durationName") == "DAILY", "only SMS DAILY supported")
    require(element.get("storedUnitCode") == "pct" and
            element.get("originalUnitCode") == "pct", "native/original unit must be pct")
    return sensor_identity(triplet, element.get("heightDepth"), element.get("ordinal"))


def _sensors(body, expected_sha256):
    require(type(body) is bytes and len(body) <= MAX_BODY, "metadata byte bound")
    require(sha256(body) == expected_sha256, "metadata hash mismatch")
    stations = _json(body)
    require(isinstance(stations, list) and 0 < len(stations) <= 32, "metadata stations")
    sensors, seen = {}, set()
    for station in stations:
        triplet = station["stationTriplet"]
        require(triplet not in seen, "duplicate metadata station")
        seen.add(triplet)
        require(station.get("networkCode") == "SNTL" and
                type(station.get("dataTimeZone")) in (int, float) and
                station["dataTimeZone"] == -8, "metadata network/timezone mismatch")
        identifiers = {k: station[k] for k in
                       ("stationId", "stateCode", "networkCode", "shefId", "name")
                       if k in station}
        for element in station["stationElements"]:
            if element.get("elementCode") != "SMS" or element.get("durationName") != "DAILY":
                continue
            identity = _element(triplet, element)
            require(identity not in sensors, "duplicate metadata sensor")
            sensors[identity] = dict(
                provider="NRCS_AWDB", network="SNOTEL", sensor_identity=identity,
                station_triplet=triplet, station_metadata_identifiers=identifiers,
                element_code="SMS", height_depth=element["heightDepth"],
                ordinal=element["ordinal"], duration="DAILY", period_ref="END",
                unit_native="pct", station_element=element,
                metadata_sha256=expected_sha256)
            if "stationId" in station:
                sensors[identity]["station_id"] = station["stationId"]
    require(0 < len(sensors) <= 32, "metadata sensor bound")
    return sensors


def _request(request, sensors):
    require(set(request) == {"method", "url", "parameters"}, "exact request fields required")
    require(request["method"] == "GET", "only saved GET supported")
    url = urlsplit(request["url"])
    require(url._replace(query="").geturl() == TARGET, "unexpected request target")
    pairs = parse_qsl(url.query, keep_blank_values=True, strict_parsing=True)
    params = request["parameters"]
    require(len(pairs) == len(dict(pairs)) and dict(pairs) == params,
            "URL and request specification differ")
    require(set(params) == {"stationTriplets", "elements", "duration", "beginDate", "endDate",
                            "periodRef", "returnFlags", "returnOriginalValues", "returnSuspectData"},
            "explicit request parameters required")
    require(params["duration"] == "DAILY" and params["periodRef"] == "END",
            "only DAILY END supported")
    require(all(params[k] == "true" for k in
                ("returnFlags", "returnOriginalValues", "returnSuspectData")),
            "flags, originals and suspect records must be requested")
    begin, end = _date(params["beginDate"]), _date(params["endDate"])
    require(0 <= (end - begin).days < 366, "query interval bound")
    # One explicit station per capture avoids wildcard/cartesian scope ambiguity.
    identities = []
    for item in params["elements"].split(","):
        require(re.fullmatch(r"SMS:-[1-9]\d*:[1-9]\d*", item), "exact SMS element required")
        _, depth, ordinal = item.split(":")
        identity = sensor_identity(params["stationTriplets"], int(depth), int(ordinal))
        require(identity in sensors and identity not in identities, "unknown/duplicate requested sensor")
        identities.append(identity)
    require(identities, "empty request scope")
    return sorted(identities), [(begin + timedelta(days=i)).isoformat()
                                for i in range((end - begin).days + 1)]


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _records(body, requested, dates, sensors):
    response = _json(body)
    require(isinstance(response, list), "response must be an array")
    result = {identity: [] for identity in requested}
    if not response:  # Only trusted complete successful evidence reaches here.
        return result
    require(len(response) == 1, "one exact station response required")
    station = response[0]
    triplet = sensors[requested[0]]["station_triplet"]
    require(station["stationTriplet"] == triplet, "response station mismatch")
    require(not any(station.get(k) for k in ("error", "errors", "errorMessage")), "station error")
    require(isinstance(station["data"], list), "data must be an array")
    seen = set()
    for block in station["data"]:
        require(not any(block.get(k) for k in ("error", "errors", "errorMessage")), "element error")
        element = block["stationElement"]
        identity = _element(triplet, element)
        require(identity in result and identity not in seen, "response sensor mismatch/duplicate")
        seen.add(identity)
        rows = block["values"]
        require(isinstance(rows, list) and len(rows) <= len(dates), "row array/bound")
        returned = set()
        for row in rows:
            day = row["date"]
            _date(day)
            require(day in dates and day not in returned, "out-of-scope/duplicate date")
            returned.add(day)
            for key in ("qcFlag", "qaFlag", "origQcFlag"):
                require(key not in row or row[key] is None or isinstance(row[key], str),
                        "flag must be string or null")
            for key in ("value", "origValue"):
                require(key not in row or row[key] is None or _number(row[key]),
                        "nonfinite/nonnumeric native value")
            missing = ("value" in row and row["value"] is None) or row.get("qcFlag") == "M"
            require(missing or ("value" in row and _number(row["value"])), "unclassified absent value")
            result[identity].append(dict(
                provider_date=day, source_timestamp_utc=source_timestamp(day),
                source_boundary_timezone="GMT-08", value_native=row.get("value"),
                qc_flag=row.get("qcFlag"), qa_flag=row.get("qaFlag"),
                original_qc_flag=row.get("origQcFlag"),
                observation_state="EXPLICIT_MISSING" if missing else "OBSERVED",
                source_fields_present=sorted(row), provider_record=row,
                response_station_element=element,
                **({"original_value": row["origValue"]} if "origValue" in row else {})))
    require(seen == set(requested), "incomplete response sensor blocks")
    return result


def build_history(metadata_body, metadata_sha256, captures):
    """Preserve attempts independently. Invalid responses fail the whole attempt.

    Bad input pins/request identities raise ValueError before assigning coverage.
    HTTP/transport/response failures remain captured FAILED ledger entries.
    """
    sensors = _sensors(metadata_body, metadata_sha256)
    require(isinstance(captures, list) and len(captures) <= 32, "capture count bound")
    total, stored, observations, ledger, seen = 0, [], [], [], set()
    for capture in captures:
        body, receipt = capture["body"], capture["receipt"]
        require(type(body) is bytes and len(body) <= MAX_BODY, "response byte bound")
        total += len(body)
        require(total <= 8 * MAX_BODY, "aggregate response byte bound")
        # Canonical copy prevents caller mutation of any retained receipt fields.
        receipt_bytes = canonical(receipt)
        receipt = _json(receipt_bytes)
        capture_id = sha256(receipt_bytes)
        require(capture_id == capture["receipt_sha256"], "receipt hash mismatch")
        require(capture_id not in seen, "duplicate capture receipt")
        seen.add(capture_id)
        require(sha256(body) == receipt["response_sha256"] and
                type(receipt["response_bytes"]) is int and
                len(body) == receipt["response_bytes"], "response hash/size mismatch")
        require(type(receipt["complete"]) is bool and
                (receipt["http_status"] is None or type(receipt["http_status"]) is int) and
                (receipt["error"] is None or isinstance(receipt["error"], str)),
                "explicit acquisition outcome required")
        retrieved = _utc(receipt["retrieved_at_utc"])
        requested, dates = _request(receipt["request"], sensors)
        request_id = sha256(canonical(receipt["request"]))
        stored.append(dict(capture_identity=capture_id, request_identity=request_id,
                           receipt_sha256=capture_id, receipt=receipt,
                           body_base64=base64.b64encode(body).decode("ascii")))
        failure, records = None, {}
        if receipt["http_status"] != 200 or not receipt["complete"] or receipt["error"] is not None:
            failure = "HTTP_OR_INCOMPLETE_ACQUISITION"
        else:
            try:
                records = _records(body, requested, dates, sensors)
            except (ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError) as exc:
                # Raw evidence survives; no partial rows or successful coverage escape.
                failure = "INVALID_RESPONSE:" + type(exc).__name__ + ":" + str(exc)
        provenance = dict(capture_identity=capture_id, request_identity=request_id,
                          query_begin_date=dates[0], query_end_date=dates[-1],
                          retrieved_at_utc=retrieved, response_sha256=receipt["response_sha256"],
                          adapter_version=ADAPTER_VERSION)
        for identity in requested:
            rows = records.get(identity, []) if failure is None else []
            returned = sorted(row["provider_date"] for row in rows)
            status = "failed" if failure else ("successful_nonempty" if rows else "successful_empty")
            ledger.append(dict(provenance, sensor_identity=identity, status=status,
                               successful_coverage=failure is None, failure_reason=failure,
                               returned_dates=returned,
                               omitted_dates=[day for day in dates if day not in returned]
                               if rows else []))
            observations.extend(dict(sensors[identity], **row, **provenance) for row in rows)
        require(len(observations) <= 20000, "observation bound")
    document = dict(
        STATUS, contract=CONTRACT, adapter_version=ADAPTER_VERSION,
        metadata=dict(body_base64=base64.b64encode(metadata_body).decode("ascii"), sha256=metadata_sha256),
        sensors=[sensors[key] for key in sorted(sensors)],
        captures=sorted(stored, key=lambda item: item["capture_identity"]),
        observations=sorted(observations, key=lambda item:
                            (item["sensor_identity"], item["provider_date"], item["capture_identity"])),
        query_ledger=sorted(ledger, key=lambda item: (item["sensor_identity"], item["capture_identity"])))
    require(len(canonical(document)) <= MAX_HISTORY, "history byte bound")
    return document


def validate_history(document):
    """Recompute every derived field. Does not replace external trust pins."""
    metadata = document["metadata"]
    rebuilt = build_history(base64.b64decode(metadata["body_base64"], validate=True), metadata["sha256"],
                            [dict(body=base64.b64decode(c["body_base64"], validate=True),
                                  receipt=c["receipt"], receipt_sha256=c["receipt_sha256"])
                             for c in document["captures"]])
    require(canonical(document) == canonical(rebuilt), "derived history mismatch")
    return rebuilt


def coverage_on(document, identity, provider_date):
    """Inspect validated history; retain overlaps, including success then failure."""
    _date(provider_date)
    require(identity in {s["sensor_identity"] for s in document["sensors"]}, "unknown sensor")
    events = []
    for entry in document["query_ledger"]:
        if entry["sensor_identity"] != identity or not (
                entry["query_begin_date"] <= provider_date <= entry["query_end_date"]):
            continue
        status = entry["status"]
        if status == "failed":
            state = "FAILED"
        elif status == "successful_empty":
            state = "QUERIED_EMPTY"
        elif provider_date in entry["omitted_dates"]:
            state = "OMITTED_SOURCE_GAP"
        else:
            state = next(row["observation_state"] for row in document["observations"]
                         if row["sensor_identity"] == identity and row["provider_date"] == provider_date
                         and row["capture_identity"] == entry["capture_identity"])
        events.append(dict(entry, state=state))
    return events or [dict(sensor_identity=identity, provider_date=provider_date,
                           status="unqueried", state="UNQUERIED", successful_coverage=False)]


def write_archive(root, document):
    """Fresh output only; return a manifest pin to retain outside this directory."""
    history = canonical(validate_history(document))
    manifest = canonical(dict(STATUS, contract=CONTRACT, adapter_version=ADAPTER_VERSION,
                              files={"history.json": {"bytes": len(history), "sha256": sha256(history)}}))
    root = Path(root)
    root.mkdir()  # Refuses any existing file, directory or symlink; no replacement.
    (root / "history.json").write_bytes(history)
    (root / "manifest.json").write_bytes(manifest)
    return sha256(manifest)


def open_archive(root, expected_manifest_sha256):
    """Require an independent pin, exact file closure, hashes and semantic replay."""
    root = Path(root)
    require(not root.is_symlink() and root.is_dir(), "archive must be a directory")
    require({p.name for p in root.iterdir()} == {"manifest.json", "history.json"}, "archive file closure")
    for name, bound in (("manifest.json", 4096), ("history.json", MAX_HISTORY)):
        path = root / name
        require(not path.is_symlink() and path.is_file() and path.stat().st_size <= bound,
                "archive file type/byte bound")
    manifest_body = (root / "manifest.json").read_bytes()
    require(sha256(manifest_body) == expected_manifest_sha256, "manifest hash mismatch")
    manifest = _json(manifest_body)
    history = (root / "history.json").read_bytes()
    expected = dict(STATUS, contract=CONTRACT, adapter_version=ADAPTER_VERSION,
                    files={"history.json": {"bytes": len(history), "sha256": sha256(history)}})
    require(manifest_body == canonical(expected), "manifest/history mismatch")
    require(manifest == expected, "manifest schema mismatch")
    document = validate_history(_json(history))
    require(history == canonical(document), "noncanonical history")
    return document
