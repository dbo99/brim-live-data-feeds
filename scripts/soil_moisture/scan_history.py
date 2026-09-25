#!/usr/bin/env python3
"""Explicit-input, offline-only SCAN history export and bounded validation.

Uses Python's standard library and base R readRDS; contains no provider client.
The CLI never writes docs/data, overwrites an output directory, or publishes.
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile

SCHEMA = "brim-soil-history-1"
CURRENT_FILES = (
    "scan_soil_moisture_latest.geojson",
    "scan_soil_moisture_latest_summary.json",
    "scan_soil_moisture_current_wy_trace.csv",
    "scan_soil_moisture_current_wy_trace_summary.json",
    "scan_depth_style.csv",
    "scan_sms_monthly_context.csv",
    "scan_sms_prior_wy_fallback_traces.csv",
    "scan_sms_waterday_percentiles.csv",
)
ARCHIVE = "scan_sms_daily_history.rds"
MAX_FILE = 16_000_000
MAX_TOTAL = 128_000_000
MAX_INPUT = 64_000_000
MAX_ROWS = 2_000_000
MAX_HISTORY_ROWS = 50_000
MAX_HISTORIES = 256
MAX_MANIFEST = 256_000
COLUMNS = ["date", "sms_pct", "water_year", "water_day", "sensor_count", "sensor_ids"]
POLICY = {
    "membership": "saved_latest_station_depth_roster",
    "current_water_year": "current_trace_only_including_gaps",
    "archive": "strictly_before_current_water_year_start",
    "missing": "absent_history_date_or_null_hover_value;never_interpolate",
    "composites": "preserve_saved_same_depth_daily_composites",
    "quality": "preserve_finite_saved_values;report_outside_0_100;never_clamp",
    "hover_days": 30,
}
CAPABILITIES = ("hover_30d", "history_last3", "history_all_available", "reference_band")
CLOSURE = "scan-history-gate1-closure-1"
REFERENCE = "scan_sms_waterday_percentiles.csv"
LAST3_POLICY = {
    "selection": "latest_up_to_three_usable_completed_water_years_for_exact_pair",
    "usable": "at_least_one_accepted_saved_finite_observation;no_new_coverage_threshold",
    "completed": "water_year_less_than_current_water_year",
    "selected_years_order": "ascending",
    "current_water_year": "separate_existing_current_product_trace",
    "missing": "preserve_absent_dates;no_fill_or_interpolation",
    "values_and_composites": "unchanged_saved_rows_and_sensor_membership",
}
HOVER_POLICY = {
    "scope": "SCAN_network_131_advertised_pairs",
    "request_event": "first_SCAN_hover_cache_miss",
    "required_on_normal_activation": False,
    "cache_reuse": "same_generation_and_payload_sha256_only",
    "generation_change": "invalidate",
    "missing_or_mismatched": "unavailable;no_other_generation_fallback",
    "exception": "SHARED_SCAN_HOVER_EXCEPTION=APPROVED;SCAN_only",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode()


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def decoded(data):
    def reject_constant(value):
        raise ValueError("nonfinite JSON value: " + value)
    return json.loads(data, object_pairs_hook=no_duplicates, parse_constant=reject_constant)


def read_bytes(path, limit):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "missing file or symlink: " + str(path))
    require(path.stat().st_size <= limit, "file size bound: " + str(path))
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    require(len(data) <= limit, "file size bound")
    return data


def integer(value):
    require(not isinstance(value, bool), "boolean is not an integer")
    n = int(value)
    require(float(value) == n, "non-integral identity/count")
    return n


def pair(site, depth):
    site, depth = integer(site), integer(depth)
    require(1 <= site <= 99999 and depth in (2, 4, 8, 20, 40), "SCAN site/depth bounds")
    return site, depth


def day(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), "ISO date required")
    result = dt.date.fromisoformat(value)
    require(dt.date(1900, 1, 1) <= result <= dt.date(2200, 12, 31), "date bound")
    return result


def wy(date):
    year = date.year + (date.month >= 10)
    return year, (date - dt.date(year - 1, 10, 1)).days + 1


def checked_row(row):
    require(isinstance(row, list) and len(row) == len(COLUMNS), "history row width")
    date = day(row[0])
    require(type(row[1]) in (int, float) and math.isfinite(row[1]), "nonfinite/non-numeric moisture")
    require(type(row[2]) is int and type(row[3]) is int and tuple(row[2:4]) == wy(date),
            "water-year/day mismatch")
    require(type(row[4]) is int and 1 <= row[4] <= 8, "sensor count bound")
    ids = row[5]
    require(isinstance(ids, list) and ids == sorted(set(ids)) and len(ids) == row[4],
            "sensor composite identity/count mismatch")
    require(all(isinstance(s, str) and re.fullmatch(r"SMS\.I(?:-\d+)?_(2|4|8|20|40)", s)
                for s in ids), "invalid SCAN sensor id")
    return date


def normalize(row, archive):
    key = pair(row["site_code"], row["depth_in"])
    require(row["station_uid"] == f"NRCS_scan_{key[0]}", "station identity mismatch")
    ids = row["sensor_ids" if archive else "sensor_id"].split(";" if archive else ",")
    ids = sorted(s.strip() for s in ids)
    value = float(row["sms_pct"])
    result = [row["date" if archive else "obs_date"], value,
              integer(row["water_year"]), integer(row["water_day"]),
              integer(row["sensor_count"]), ids]
    checked_row(result)
    require(all(integer(s.rsplit("_", 1)[1]) == key[1] for s in ids), "sensor/depth mismatch")
    return key, result


def scope_checked(scope):
    require(set(scope) == {"stations", "histories", "archive_only"}, "scope fields")
    require(type(scope["stations"]) is int and 1 <= scope["stations"] <= 64, "station bound")
    require(type(scope["histories"]) is int and 1 <= scope["histories"] <= MAX_HISTORIES, "history bound")
    extras = [pair(*k) for k in scope["archive_only"]]
    require(extras == sorted(set(extras)), "archive-only identities must be sorted/unique")
    return set(extras)


def binding_checked(binding):
    require(binding["schema"] == SCHEMA + "-inputs", "input schema")
    scope_checked(binding["scope"])
    require(set(binding["files"]) == {ARCHIVE, *CURRENT_FILES}, "exact input allowlist required")
    for descriptor in binding["files"].values():
        require(set(descriptor) == {"bytes", "sha256"} and type(descriptor["bytes"]) is int
                and 0 < descriptor["bytes"] <= MAX_INPUT and
                isinstance(descriptor["sha256"], str) and
                re.fullmatch(r"[0-9a-f]{64}", descriptor["sha256"]), "input descriptor bound/hash")


def load_binding(path, expected_hash, archive, current):
    raw = read_bytes(path, MAX_MANIFEST)
    require(sha(raw) == expected_hash, "input manifest checksum mismatch")
    binding = decoded(raw)
    binding_checked(binding)
    descriptors = binding["files"]
    paths = {ARCHIVE: Path(archive), **{name: Path(current) / name for name in CURRENT_FILES}}
    for name, path in paths.items():
        content = read_bytes(path, MAX_INPUT)
        require(descriptors[name] == {"bytes": len(content), "sha256": sha(content)},
                "input checksum/size mismatch: " + name)
    return binding, paths


def load_current(current, scope):
    current = Path(current)
    geo = decoded(read_bytes(current / CURRENT_FILES[0], MAX_INPUT))
    summary = decoded(read_bytes(current / CURRENT_FILES[3], MAX_MANIFEST))
    require(geo["type"] == "FeatureCollection", "latest shape")
    start, end = day(summary["current_wy_start_date"]), day(summary["last_date"])
    year = integer(summary["current_water_year"])
    require(start == dt.date(year - 1, 10, 1) and start <= end < dt.date(year, 10, 1), "current-WY bounds")
    build = summary["feed_build_time_utc"]
    require(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", build), "source build timestamp")
    require(end <= dt.datetime.fromisoformat(build.replace("Z", "+00:00")).date(), "future observation")
    roster, sites = {}, set()
    for feature in geo["features"]:
        p = feature["properties"]
        site = integer(p["site_code"])
        require(site not in sites and p["station_uid"] == f"NRCS_scan_{site}", "duplicate/mismatched station")
        sites.add(site)
        require(p["current_water_year"] == year and p["current_wy_start_date"] == str(start)
                and p["feed_build_time_utc"] == build, "latest/trace generation mismatch")
        for depth in decoded(p["depth_values_json"]):
            key = pair(site, depth["depth_in"])
            require(key not in roster, "duplicate depth")
            roster[key] = {"site_code": site, "station_uid": p["station_uid"],
                           "station_name": p["station_name"], "depth_in": key[1]}
    require(len(sites) == scope["stations"] and len(roster) == scope["histories"], "browser scope mismatch")
    rows = {key: [] for key in roster}
    count = 0
    with (current / CURRENT_FILES[2]).open(newline="", encoding="utf-8") as handle:
        for source in csv.DictReader(handle):
            count += 1
            require(count <= MAX_ROWS, "current row bound")
            key, row = normalize(source, False)
            require(key in rows, "current trace depth absent from latest roster")
            require(start <= day(row[0]) <= end, "current row outside authority interval")
            rows[key].append(row)
    require(count == summary["trace_rows"] and len(sites) == summary["stations"], "trace summary counts")
    require(all(rows.values()), "empty browser history in current trace")
    require(min(r[0] for rr in rows.values() for r in rr) == summary["first_date"], "trace first date")
    require(max(r[0] for rr in rows.values() for r in rr) == str(end), "trace last date")
    latest_summary = decoded(read_bytes(current / CURRENT_FILES[1], MAX_MANIFEST))
    require(latest_summary["feed_build_time_utc"] == build and
            latest_summary["current_water_year"] == year and
            latest_summary["current_wy_start_date"] == str(start) and
            latest_summary["features_written"] == len(sites) and
            latest_summary["depth_rows_latest"] == len(roster) and
            latest_summary["current_wy_trace_rows"] == count, "latest summary coherence")
    return roster, rows, {"current_water_year": year, "current_wy_start_date": str(start),
                          "as_of_date": str(end), "source_feed_build_time_utc": build}


def combine(roster, current_rows, archive_rows, authority, scope):
    """Drop ALL archive rows in the current WY, including dates absent from current."""
    start = day(authority["current_wy_start_date"])
    end = day(authority["as_of_date"])
    histories = {key: list(current_rows[key]) for key in roster}
    archive_stats, seen = {}, set()
    ignored = 0
    for number, source in enumerate(archive_rows, 1):
        require(number <= MAX_ROWS, "archive row bound")
        key, row = normalize(source, True)
        date = checked_row(row)
        identity = (*key, row[0])
        require(identity not in seen, "duplicate archive station/depth/date")
        seen.add(identity)
        require(date <= end, "archive extends beyond saved current cutoff")
        s = archive_stats.setdefault(key, {"site_code": key[0], "depth_in": key[1],
                                          "rows": 0, "first_date": row[0], "last_date": row[0],
                                          "outside_0_100": 0})
        s["rows"] += 1
        s["first_date"] = min(s["first_date"], row[0])
        s["last_date"] = max(s["last_date"], row[0])
        s["outside_0_100"] += not 0 <= row[1] <= 100
        if date >= start:
            ignored += 1
        elif key in roster:
            histories[key].append(row)
    extras = scope_checked(scope)
    require(set(archive_stats) - set(roster) == extras, "unexpected archive-only membership")
    require(set(roster) <= set(archive_stats), "browser history missing from saved archive")
    for key, rows in histories.items():
        rows.sort(key=lambda row: row[0])
        require(len(rows) <= MAX_HISTORY_ROWS and len({r[0] for r in rows}) == len(rows),
                "duplicate/oversize combined history")
    inventory = {"histories": [dict(archive_stats[k], browser_advertised=k in roster)
                               for k in sorted(archive_stats)],
                 "ignored_archive_current_wy_rows": ignored,
                 "archive_rows": len(seen)}
    return histories, inventory


def statistics(rows):
    return {"rows": len(rows), "first_date": rows[0][0], "last_date": rows[-1][0],
            "zero_values": sum(r[1] == 0 for r in rows),
            "outside_0_100": sum(not 0 <= r[1] <= 100 for r in rows),
            "composite_rows": sum(r[4] > 1 for r in rows)}


def envelope(kind, binding_hash):
    return {"schema": SCHEMA, "network": "SCAN", "kind": kind, "input_manifest_sha256": binding_hash}


def write_object(root, stem, value):
    raw = encoded(value)
    require(len(raw) <= MAX_FILE, "output file bound")
    digest = sha(raw)
    name = f"{stem}-{digest}.json"
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
    return {"path": name, "bytes": len(raw), "sha256": digest}


def read_target(root, descriptor):
    """Read one bounded manifest-selected target without external/path fallback."""
    name = descriptor["path"]
    require(isinstance(name, str) and re.fullmatch(
        r"(?:(?:(?:history|last3)/[0-9]{1,5}-(?:2|4|8|20|40)|history-index|hover-30d|archive-inventory)-[0-9a-f]{64}\.json|reference/scan_sms_waterday_percentiles-[0-9a-f]{64}\.csv)", name),
        "unsafe or unrecognized target path")
    require(set(descriptor) == {"path", "bytes", "sha256"} and type(descriptor["bytes"]) is int
            and 0 < descriptor["bytes"] <= MAX_FILE, "file descriptor bound")
    require(name.endswith("-" + descriptor["sha256"] + Path(name).suffix), "immutable filename/hash mismatch")
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), "output root must be a real directory")
    require(all(not (root / p).is_symlink() for p in Path(name).parents), "symlink ancestor")
    raw = read_bytes(root / name, MAX_FILE)
    require(len(raw) == descriptor["bytes"] and sha(raw) == descriptor["sha256"], "file checksum/size mismatch: " + name)
    return raw


def capability_payload(root, capability, generation):
    """Offline contract probe: unresolved/mismatched targets are unavailable.

    Does not implement a browser, request, cache, activation or fallback path.
    Callers first resolve an index capability's manifest_capability reference.
    """
    if capability.get("generation") != generation:
        return {"status": "unavailable", "reason": "generation_mismatch"}
    if capability.get("status") != "available":
        return {"status": "unavailable", "reason": capability.get("reason") or "not_available"}
    try:
        raw = read_target(root, capability["file"])
        if capability["file"]["path"].endswith(".json"):
            obj = decoded(raw)
            require(obj["schema"] == SCHEMA and obj["network"] == "SCAN" and
                    obj["input_manifest_sha256"] == generation, "payload generation mismatch")
        return {"status": "available", "bytes": len(raw), "sha256": sha(raw)}
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "unavailable", "reason": "payload_missing_or_mismatched"}


def select_last3(rows, current_water_year):
    years = sorted({r[2] for r in rows if r[2] < current_water_year})[-3:]
    return years, [r for r in rows if r[2] in years]


def reference_identity(raw):
    # Read identity/coverage only; preserve the entire authoritative CSV unchanged.
    rows = csv.DictReader(io.StringIO(raw.decode("utf-8")))
    required = {"site_code", "depth_in", "water_day", "build_time_utc", "p00", "p10", "p30",
                "p50", "p70", "p90", "p100", "climatology_ok", "min_years_for_context",
                "years_min", "years_max", "current_water_year_excluded"}
    require(required <= set(rows.fieldnames or []), "reference product columns")
    keys, builds = set(), set()
    for row in rows:
        keys.add(pair(row["site_code"], row["depth_in"]))
        builds.add(row["build_time_utc"])
    require(len(builds) == 1 and keys, "reference source generation")
    return {"authoritative_repo_path": "docs/data/" + REFERENCE,
            "source_generation": builds.pop(), "source_sha256": sha(raw)}, keys


def manifest_capabilities(generation, index_file, hover_file, reference_file, reference_source):
    common = {"status": "available", "generation": generation}
    return {
        "hover_30d": dict(common, file=hover_file, policy=HOVER_POLICY),
        "history_last3": dict(common, index=index_file, entry_key="history_last3", policy=LAST3_POLICY),
        "history_all_available": dict(common, index=index_file, entry_key="history_all_available",
                                      policy="selected_exact_station_depth_on_demand;unchanged_all_available_rows"),
        "reference_band": dict(common, file=reference_file, source=reference_source,
                               policy="existing_SCAN_waterday_percentiles;honor_source_flags_thresholds_and_reference_periods;no_recomputation"),
    }


def entry_capabilities(generation, entry, last3_file, years, has_reference):
    common = {"generation": generation}
    last3 = dict(common, selected_years=years, policy_ref="history_last3")
    if years:
        last3.update(status="available", file=last3_file)
    else:
        last3.update(status="unavailable", reason="no_usable_completed_water_year")
    reference = dict(common, manifest_capability="reference_band",
                     selector={"site_code": entry["site_code"], "depth_in": entry["depth_in"]})
    reference.update({"status": "available"} if has_reference else
                     {"status": "unavailable", "reason": "exact_pair_absent_from_authoritative_reference"})
    return {
        "hover_30d": dict(common, status="available", manifest_capability="hover_30d",
                          selector={"site_code": entry["site_code"], "depth_in": entry["depth_in"]}),
        "history_last3": last3,
        "history_all_available": dict(common, status="available", file=entry["file"], policy_ref="history_all_available"),
        "reference_band": reference,
    }


def close_contract(accepted, accepted_hash, reference_csv, output):
    """Extend a bound Gate 1 delivery; never reopen/reprocess its RDS or providers."""
    accepted, output = Path(accepted), Path(output)
    require(not output.exists() and not output.is_symlink(), "output must be fresh")
    require(not output.resolve().is_relative_to(accepted.resolve()), "output cannot modify accepted fixture")
    product_root = Path(__file__).resolve().parents[2] / "docs" / "data"
    require(not output.resolve().is_relative_to(product_root), "offline export cannot write docs/data")
    validate(accepted, accepted_hash)
    old = decoded(read_bytes(accepted / "manifest.json", MAX_MANIFEST))
    require("contract_closure" not in old, "closure requires the accepted original Gate 1 fixture")
    index = decoded(read_target(accepted, old["index"]))
    generation = old["input_manifest_sha256"]
    ref = read_bytes(reference_csv, MAX_FILE)
    require(old["input_binding"]["files"][REFERENCE] == {"bytes": len(ref), "sha256": sha(ref)},
            "reference does not match accepted source binding")
    reference_source, reference_keys = reference_identity(ref)
    output.mkdir(parents=False, exist_ok=False)
    files = []
    for descriptor in old["files"]:
        if descriptor == old["index"]:
            continue
        raw = read_target(accepted, descriptor)
        target = output / descriptor["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write(raw)
        files.append(descriptor)
    ref_file = {"path": f"reference/scan_sms_waterday_percentiles-{sha(ref)}.csv", "bytes": len(ref), "sha256": sha(ref)}
    (output / "reference").mkdir()
    with (output / ref_file["path"]).open("xb") as handle:
        handle.write(ref)
    files.append(ref_file)
    for entry in index["histories"]:
        obj = decoded(read_target(accepted, entry["file"]))
        years, rows = select_last3(obj["rows"], old["authority"]["current_water_year"])
        last3_file = None
        if years:
            last3 = dict(obj, kind="history_last3", rows=rows, selected_years=years,
                         selection_policy=LAST3_POLICY, current_water_year=old["authority"]["current_water_year"])
            last3_file = write_object(output, f"last3/{entry['site_code']}-{entry['depth_in']}", last3)
            files.append(last3_file)
        key = pair(entry["site_code"], entry["depth_in"])
        entry["capabilities"] = entry_capabilities(generation, entry, last3_file, years, key in reference_keys)
    index.update(generation=generation, contract_closure=CLOSURE,
                 capabilities={name: {"generation": generation, "manifest_capability": name,
                                      "status": "available"} for name in CAPABILITIES})
    index_file = write_object(output, "history-index", index)
    files.append(index_file)
    manifest = dict(old, generation=generation, contract_closure=CLOSURE, accepted_manifest_sha256=accepted_hash,
                    index=index_file, reference_band=ref_file, reference_source=reference_source,
                    files=sorted(files, key=lambda f: f["path"]), payload_bytes=sum(f["bytes"] for f in files))
    manifest["capabilities"] = manifest_capabilities(generation, index_file, old["hover"], ref_file, reference_source)
    manifest["normal_activation"] = {"history_body_bytes": 0, "hover_body_bytes": 0,
                                     "required_capability_payloads": []}
    raw = encoded(manifest)
    require(len(raw) <= MAX_MANIFEST, "manifest size bound")
    with (output / "manifest.json").open("xb") as handle:
        handle.write(raw)
    return validate(output, sha(raw))


def write_delivery(output, binding, binding_hash, roster, histories, inventory, authority):
    output = Path(output)
    # Exclusive directory creation is the failure/retention boundary. No output reuse.
    output.mkdir(parents=False, exist_ok=False)
    entries, files = [], []
    for key in sorted(roster):
        obj = dict(envelope("history", binding_hash), **roster[key], columns=COLUMNS, rows=histories[key])
        descriptor = write_object(output, f"history/{key[0]}-{key[1]}", obj)
        files.append(descriptor)
        entries.append(dict(roster[key], **statistics(histories[key]), file=descriptor))
    index = dict(envelope("index", binding_hash), authority=authority, policy=POLICY,
                 units={"sms_pct": "percent_volumetric_water_content", "depth_in": "inch"},
                 stations=binding["scope"]["stations"], histories=entries)
    index_file = write_object(output, "history-index", index)
    dates = [str(day(authority["as_of_date"]) - dt.timedelta(days=i)) for i in range(29, -1, -1)]
    hover_rows = []
    for key in sorted(roster):
        values = {r[0]: r[1] for r in histories[key]}
        hover_rows.append({"site_code": key[0], "depth_in": key[1], "values": [values.get(d) for d in dates]})
    hover = dict(envelope("hover", binding_hash), authority=authority,
                 first_date=dates[0], last_date=dates[-1], days=30, histories=hover_rows)
    hover_file = write_object(output, "hover-30d", hover)
    inventory_file = write_object(output, "archive-inventory", dict(envelope("archive_inventory", binding_hash), **inventory))
    files += [index_file, hover_file, inventory_file]
    manifest = dict(envelope("manifest", binding_hash), input_binding=binding, scope=binding["scope"],
                    authority=authority, policy=POLICY, index=index_file, hover=hover_file,
                    archive_inventory=inventory_file, files=sorted(files, key=lambda f: f["path"]),
                    payload_bytes=sum(f["bytes"] for f in files), publication="offline_export_only")
    require(manifest["payload_bytes"] <= MAX_TOTAL, "total output bound")
    raw = encoded(manifest)
    with (output / "manifest.json").open("xb") as handle:
        handle.write(raw)
    # A failure leaves task-owned diagnostics, never a success marker/publication.
    return validate(output, sha(raw), expected=histories)


def validate(output, manifest_hash, expected=None):
    root = Path(output)
    require(root.is_dir() and not root.is_symlink(), "output root must be a real directory")
    raw = read_bytes(root / "manifest.json", MAX_MANIFEST)
    require(re.fullmatch(r"[0-9a-f]{64}", manifest_hash or "") and sha(raw) == manifest_hash, "manifest checksum mismatch")
    manifest = decoded(raw)
    closure = "contract_closure" in manifest
    if closure:
        require(manifest["contract_closure"] == CLOSURE, "unsupported closure")
    binding_hash = manifest["input_manifest_sha256"]
    require(sha(encoded(manifest["input_binding"])) == binding_hash, "embedded input binding mismatch")
    binding_checked(manifest["input_binding"])
    require(manifest["scope"] == manifest["input_binding"]["scope"], "scope binding mismatch")
    extras = scope_checked(manifest["scope"])
    require(manifest["publication"] == "offline_export_only" and manifest["policy"] == POLICY, "export policy mismatch")
    authority = manifest["authority"]
    start, end = day(authority["current_wy_start_date"]), day(authority["as_of_date"])
    require(start == dt.date(integer(authority["current_water_year"]) - 1, 10, 1)
            and wy(end)[0] == authority["current_water_year"] and start <= end, "authority bounds")
    build = authority["source_feed_build_time_utc"]
    require(isinstance(build, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", build),
            "source build timestamp")
    require(end <= dt.datetime.fromisoformat(build.replace("Z", "+00:00")).date(), "future observation")
    files = manifest["files"]
    count = manifest["scope"]["histories"]
    require((count + 4 <= len(files) <= count * 2 + 4) if closure else len(files) == count + 3,
            "file count bound")
    require(len({f["path"] for f in files}) == len(files), "duplicate file descriptor")
    objects, total = {}, 0
    for f in files:
        name = f["path"]
        data = read_target(root, f)
        total += len(data)
        require(total <= MAX_TOTAL, "total output bound")
        if name.endswith(".csv"):
            require(closure and f == manifest["reference_band"], "unexpected reference target")
            objects[name] = data
            continue
        obj = decoded(data)
        for k, v in envelope(obj["kind"], binding_hash).items():
            require(obj[k] == v, "generation/schema mismatch")
        objects[name] = obj
    for k, v in envelope("manifest", binding_hash).items():
        require(manifest[k] == v, "manifest schema/identity mismatch")
    require(total == manifest["payload_bytes"], "payload total mismatch")
    expected_paths = {"manifest.json", *objects}
    observed_paths = set()
    for path in root.rglob("*"):
        require(not path.is_symlink(), "output contains symlink")
        if path.is_file():
            observed_paths.add(path.relative_to(root).as_posix())
        else:
            require(path.relative_to(root).as_posix() in ({"history", "last3", "reference"} if closure else {"history"}),
                    "unexpected output directory")
    require(observed_paths == expected_paths, "output allowlist mismatch")

    def selected(role, kind):
        f = manifest[role]
        require(f in files, "selected descriptor not in manifest")
        obj = objects[f["path"]]
        require(obj["kind"] == kind, "wrong selected object kind")
        return obj

    index = selected("index", "index")
    hover = selected("hover", "hover")
    inventory = selected("archive_inventory", "archive_inventory")
    require(index["authority"] == hover["authority"] == authority and index["policy"] == POLICY, "authority/policy mismatch")
    require(index["units"] == {"sms_pct": "percent_volumetric_water_content", "depth_in": "inch"}, "units mismatch")
    histories, selected_paths = {}, set()
    total_rows = zeroes = outliers = composites = 0
    for entry in index["histories"]:
        key = pair(entry["site_code"], entry["depth_in"])
        require(key not in histories and key not in extras, "duplicate or archive-only browser identity")
        descriptor = entry["file"]
        require(descriptor in files and descriptor["path"].startswith(f"history/{key[0]}-{key[1]}-"), "exact shard path mismatch")
        selected_paths.add(descriptor["path"])
        obj = objects[descriptor["path"]]
        require(obj["kind"] == "history" and obj["columns"] == COLUMNS, "history schema")
        require(entry["station_uid"] == f"NRCS_scan_{key[0]}" and
                all(obj[k] == entry[k] for k in ("site_code", "depth_in", "station_uid", "station_name")), "shard identity mismatch")
        rows = obj["rows"]
        require(isinstance(rows, list) and 0 < len(rows) <= MAX_HISTORY_ROWS, "history row bound")
        previous = None
        for row in rows:
            date = checked_row(row)
            require((previous is None or previous < date) and date <= end, "history order/date bound")
            require(all(integer(s.rsplit("_", 1)[1]) == key[1] for s in row[5]), "shard sensor/depth mismatch")
            previous = date
        require(all(entry[k] == v for k, v in statistics(rows).items()), "history statistics mismatch")
        if expected is not None:
            require(rows == expected[key], "saved-input round-trip mismatch")
        histories[key] = rows
        total_rows += len(rows)
        require(total_rows <= MAX_ROWS, "total row bound")
        zeroes += entry["zero_values"]
        outliers += entry["outside_0_100"]
        composites += entry["composite_rows"]
    require(len(histories) == manifest["scope"]["histories"] and
            len({k[0] for k in histories}) == index["stations"] == manifest["scope"]["stations"], "scope count mismatch")
    require(selected_paths == {f["path"] for f in files if f["path"].startswith("history/")}, "orphan/unselected shard")
    dates = [str(end - dt.timedelta(days=i)) for i in range(29, -1, -1)]
    require(hover["days"] == 30 and hover["first_date"] == dates[0] and hover["last_date"] == dates[-1], "hover window")
    hover_keys = set()
    missing = 0
    for entry in hover["histories"]:
        key = pair(entry["site_code"], entry["depth_in"])
        require(key in histories and key not in hover_keys, "hover membership mismatch")
        hover_keys.add(key)
        values = {r[0]: r[1] for r in histories[key]}
        require(isinstance(entry["values"], list) and len(entry["values"]) == 30 and
                all(v is None or (type(v) in (int, float) and math.isfinite(v)) for v in entry["values"]),
                "hover value type/bound")
        require(entry["values"] == [values.get(d) for d in dates], "hover/history gap/value mismatch")
        missing += entry["values"].count(None)
    require(hover_keys == set(histories), "hover scope mismatch")
    inventory_keys, inventory_extras = set(), set()
    inventory_rows = 0
    for entry in inventory["histories"]:
        key = pair(entry["site_code"], entry["depth_in"])
        require(key not in inventory_keys and type(entry["browser_advertised"]) is bool, "archive inventory identity")
        inventory_keys.add(key)
        require(type(entry["rows"]) is int and 0 < entry["rows"] <= MAX_HISTORY_ROWS and
                day(entry["first_date"]) <= day(entry["last_date"]) <= end and
                0 <= entry["outside_0_100"] <= entry["rows"], "archive inventory bounds")
        require(entry["browser_advertised"] == (key in histories), "archive advertisement mismatch")
        if not entry["browser_advertised"]:
            inventory_extras.add(key)
        inventory_rows += entry["rows"]
    require(inventory_keys == set(histories) | extras and inventory_extras == extras, "archive scope mismatch")
    require(inventory_rows == inventory["archive_rows"] <= MAX_ROWS and
            0 <= inventory["ignored_archive_current_wy_rows"] <= inventory_rows, "archive row count mismatch")
    result = {"schema": SCHEMA, "manifest_sha256": sha(raw), "input_manifest_sha256": binding_hash,
            "stations": index["stations"], "histories": len(histories), "history_rows": total_rows,
            "archive_only": [list(k) for k in sorted(extras)], "zero_values": zeroes,
            "outside_0_100": outliers, "composite_rows": composites, "hover_nulls": missing,
            "files": len(files) + 1, "payload_bytes": total, "manifest_bytes": len(raw),
            "total_bytes": total + len(raw), "index_bytes": manifest["index"]["bytes"],
            "hover_bytes": manifest["hover"]["bytes"], "outcome": "PASS"}
    if closure:
        result.update(validate_closure(root, manifest, index, objects, histories))
    return result


def validate_closure(root, manifest, index, objects, histories):
    generation = manifest["input_manifest_sha256"]
    require(manifest["generation"] == index["generation"] == generation and
            index["contract_closure"] == CLOSURE, "closure generation mismatch")
    require(re.fullmatch(r"[0-9a-f]{64}", manifest["accepted_manifest_sha256"]), "accepted manifest identity")
    require(manifest["normal_activation"] == {"history_body_bytes": 0, "hover_body_bytes": 0,
                                             "required_capability_payloads": []}, "activation requires body")
    require(index["capabilities"] == {name: {"generation": generation, "manifest_capability": name,
                                            "status": "available"} for name in CAPABILITIES}, "index capability closure")
    reference_file = manifest["reference_band"]
    require(reference_file in manifest["files"], "reference descriptor missing")
    reference_raw = objects[reference_file["path"]]
    reference_source, reference_keys = reference_identity(reference_raw)
    require(manifest["reference_source"] == reference_source and
            manifest["input_binding"]["files"][REFERENCE] ==
            {"sha256": sha(reference_raw), "bytes": len(reference_raw)}, "reference source binding mismatch")
    require(manifest["capabilities"] == manifest_capabilities(
        generation, manifest["index"], manifest["hover"], reference_file, reference_source), "manifest capability closure")
    paths, total_rows = set(), 0
    for entry in index["histories"]:
        key = pair(entry["site_code"], entry["depth_in"])
        years, rows = select_last3(histories[key], manifest["authority"]["current_water_year"])
        caps = entry["capabilities"]
        descriptor = caps["history_last3"].get("file")
        require(caps == entry_capabilities(generation, entry, descriptor, years, key in reference_keys),
                "entry capability/selected years mismatch")
        if not years:
            require(descriptor is None, "unavailable Last-3 must not reference payload")
            continue
        require(descriptor in manifest["files"] and
                descriptor["path"].startswith(f"last3/{key[0]}-{key[1]}-"), "Last-3 exact path mismatch")
        paths.add(descriptor["path"])
        expected = dict(objects[entry["file"]["path"]], kind="history_last3", rows=rows,
                        selected_years=years, selection_policy=LAST3_POLICY,
                        current_water_year=manifest["authority"]["current_water_year"])
        require(encoded(objects[descriptor["path"]]) == encoded(expected), "Last-3 identity/selection/rows mismatch")
        total_rows += len(rows)
    require(paths == {f["path"] for f in manifest["files"] if f["path"].startswith("last3/")},
            "orphan/unselected Last-3 shard")
    require(len(manifest["files"]) == len(histories) + len(paths) + 4, "closure file count mismatch")
    return {"contract_closure": CLOSURE, "generation": generation, "last3_shards": len(paths),
            "last3_rows": total_rows, "reference_bytes": reference_file["bytes"],
            "normal_activation_history_hover_body_bytes": 0}


def export(archive, current, input_manifest, input_hash, output):
    require(not Path(output).exists() and not Path(output).is_symlink(), "output must be fresh")
    # No output may live under the repository's published product directory.
    product_root = Path(__file__).resolve().parents[2] / "docs" / "data"
    resolved = Path(output).resolve()
    require(not resolved.is_relative_to(product_root), "offline export cannot write docs/data")
    require(not resolved.is_relative_to(Path(current).resolve()), "output cannot be within input products")
    binding, paths = load_binding(input_manifest, input_hash, archive, current)
    require(read_bytes(input_manifest, MAX_MANIFEST) == encoded(binding), "input binding must use canonical JSON")
    roster, current_rows, authority = load_current(current, binding["scope"])
    with tempfile.TemporaryDirectory(prefix="scan-history-") as scratch:
        csv_path = Path(scratch) / "saved-history.csv"
        subprocess.run(["Rscript", "--vanilla", str(Path(__file__).with_name("read_scan_history_rds.R")),
                        str(paths[ARCHIVE]), str(csv_path)], check=True, timeout=120,
                       stdout=subprocess.DEVNULL)
        require(csv_path.stat().st_size <= MAX_TOTAL * 2, "normalized archive size bound")
        with csv_path.open(newline="", encoding="utf-8") as handle:
            histories, inventory = combine(roster, current_rows, csv.DictReader(handle), authority, binding["scope"])
    # Detect changed inputs before writing any delivery bytes.
    load_binding(input_manifest, input_hash, archive, current)
    return write_delivery(output, binding, input_hash, roster, histories, inventory, authority)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("export")
    for name in ("archive-rds", "current-dir", "input-manifest", "input-manifest-sha256", "output"):
        build.add_argument("--" + name, required=True)
    check = commands.add_parser("validate")
    check.add_argument("--output", required=True)
    check.add_argument("--manifest-sha256", required=True)
    close = commands.add_parser("close")
    for name in ("accepted-fixture", "manifest-sha256", "reference-csv", "output"):
        close.add_argument("--" + name, required=True)
    args = parser.parse_args()
    if args.command == "export":
        result = export(args.archive_rds, args.current_dir, args.input_manifest, args.input_manifest_sha256, args.output)
    elif args.command == "close":
        result = close_contract(args.accepted_fixture, args.manifest_sha256, args.reference_csv, args.output)
    else:
        result = validate(args.output, args.manifest_sha256)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
