#!/usr/bin/env python3
"""Offline, fail-and-retain Dendra bulk VWC importer; no acquisition interfaces.

Requires existing Arrow/Parquet C++ libraries, pkg-config, C++17, and R with
jsonlite/digest. Installs nothing. All generated data go to a fresh explicit
output root outside this repository. Originals and prior API evidence are read
only. On failure partial outputs remain unaccepted; never overwrite or resume.
"""
import argparse
import collections
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import resource
import shlex
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
import zipfile

VERSION = "dendra-bulk-vwc-r1"
CAMP = "63531a67a9b61453fa1ca4ed"
CAMP_STATION = "635319fcb055ac5348842453"
CLASSES = ("TARGET_VWC_PERCENT", "TARGET_VWC_FRACTION", "TARGET_VWC_UNIT_UNRESOLVED",
           "EXCLUDED_NON_VWC_DIAGNOSTIC", "EXCLUDED_OTHER_MEASUREMENT", "AMBIGUOUS_IDENTITY")
REPO = Path(__file__).resolve().parents[2]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def write_csv(path, rows, fields=None):
    fields = fields or list(dict.fromkeys(k for row in rows for k in row))
    with Path(path).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: json.dumps(v, separators=(",", ":")) if isinstance(v, (list, dict)) else v
                        for k, v in row.items()})


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, text=True, capture_output=True, **kwargs)


def compile_helper(destination):
    flags = shlex.split(run(["pkg-config", "--cflags", "--libs", "arrow", "parquet"]).stdout)
    run(["clang++", "-O2", "-std=c++17", Path(__file__).with_name("bulk_csv_arrow.cpp"),
         "-o", destination, *flags])
    return str(destination)


def classify(row):
    """Use exact candidate/parameter/unit evidence, never range or guessed depth."""
    candidates = json.loads(row["candidate_stream_ids"])
    if len(candidates) != 1 or row["mapping_state"] not in ("CORROBORATED_PROPOSAL", "EXACT"):
        return CLASSES[5], None, "Exact export-column identity is ambiguous"
    require(candidates == [row["proposed_stream_id"]], "Candidate/proposed identity mismatch")
    unit = row["native_unit"]
    if unit in ("Microsecond", "Hertz", "Volt", "Ohm"):
        return CLASSES[3], None, "Instrument/period/electrical diagnostic is not VWC"
    if unit == "Millimeter":
        return CLASSES[4], None, "Millimeter-valued quantity is not VWC percent or fraction"
    terms = json.loads(row["source_parameter_terms"])
    moisture = terms.get("ds", {}).get("Variable") in ("VolumetricWaterContent", "Moisture")
    moisture = moisture or terms.get("dq", {}).get("Measurement") == "SoilMoisture"
    if not moisture:
        return CLASSES[5], None, "Source parameter does not establish moisture/VWC"
    resolved = row["unit_resolution"] == "RETAINED_ACCEPTED_UNIT_DEFINITION"
    if unit == "Percent" and row["export_unit_token"] == "pct" and resolved:
        return CLASSES[0], 1, "Retained Percent definition and matching export unit"
    if unit == "VolumetricWaterContent" and row["export_unit_token"] == "m-3-m-3" and resolved:
        return CLASSES[1], 100, "Retained fractional VWC definition and matching export unit"
    return CLASSES[2], None, "Export scale unresolved; no accepted percent values"


def preflight(input_dir, intake, expected_columns=470, expected_files=12):
    inventory = json.loads((intake / "BULK_EXPORT_INVENTORY.json").read_text())
    with (intake / "BULK_SERIES_CROSSWALK.csv").open() as f:
        rows = list(csv.DictReader(f))
    require(len(rows) == expected_columns, "Selected-column closure differs")
    require(len(inventory["files"]) == expected_files, "Input-file closure differs")
    profiles, files, seen = {}, {}, set()
    for item in inventory["files"]:
        name = item["basename"]
        require(Path(name).name == name and name not in files, "Invalid/duplicate input basename")
        path = input_dir / name
        require(not path.is_symlink() and path.is_file(), "Input must be an original regular file")
        require(sha(path) == item["sha256"], "Original input hash mismatch: " + name)
        with path.open(newline="") as f:
            require(next(csv.reader(f)) == item["header"], "Input header differs")
        files[name] = item
        for series in item["series"]:
            profiles[series["export_local_series_key"]] = series
        print("HASH_VERIFIED " + name, flush=True)
    for row in rows:
        item = files[row["relative_asset_path"]]
        ordinal = int(row["column_ordinal_1_based"])
        key = f"csv-sha256:{item['sha256']}:column:{ordinal}"
        require(key == row["export_local_series_key"] and key not in seen, "Export-local identity collision")
        require(row["file_sha256"] == item["sha256"] and ordinal >= 2, "Input binding differs")
        require(item["header"][ordinal - 1] == row["original_header"], "Ordinal/header mismatch")
        profile = profiles[key]
        require(int(row["non_null_count"]) == profile["non_null_count"], "Crosswalk count differs")
        seen.add(key)
        product, multiplier, reason = classify(row)
        row.update(product_class=product, conversion_multiplier=multiplier,
                   classification_reason=reason, selected=True, target=product in CLASSES[:3],
                   observation_count=profile["non_null_count"], depth_cm=float(row["depth_cm"]) if row["depth_cm"] else None,
                   materialized=False, asset_id=hashlib.sha256(key.encode()).hexdigest()[:24],
                   native_asset=None, duplicate_of=None,
                   daily_eligibility="NOT_REVIEWED; native unit conversion does not grant daily acceptance")
    require(seen == set(profiles), "Inventory/crosswalk exact closure differs")
    return rows, files, profiles


def value_digest(input_dir, row):
    """Exact ordered timestamp/IEEE-value equality; ignores alignment-only blanks."""
    h = hashlib.sha256(); count = 0
    with (input_dir / row["relative_asset_path"]).open(newline="") as f:
        r = csv.reader(f); next(r)
        for rec in r:
            token = rec[int(row["column_ordinal_1_based"]) - 1]
            if token:
                v = float(token); require(math.isfinite(v), "Nonfinite duplicate evidence")
                h.update((rec[0] + "\t" + v.hex() + "\n").encode()); count += 1
    return h.hexdigest(), count


def resolve_duplicates(input_dir, rows):
    grouped = collections.defaultdict(list); proof = []
    for row in rows:
        if row["product_class"] in CLASSES[:2]:
            grouped[row["proposed_stream_id"]].append(row)
    for sid, group in grouped.items():
        if len(group) < 2:
            continue
        # Stable choice, not latest-wins; only exactly equivalent observations can alias.
        group.sort(key=lambda r: (r["relative_asset_path"], int(r["column_ordinal_1_based"])))
        signatures = [value_digest(input_dir, row) for row in group]
        require(len(set(signatures)) == 1, "Conflicting repeated stream selection: " + sid)
        require(len({(r["product_class"], r["native_unit"], r["depth_cm"], r["proposed_station_id"]) for r in group}) == 1,
                "Repeated stream metadata differs")
        for row in group[1:]:
            row["duplicate_of"] = group[0]["export_local_series_key"]
        proof.append(dict(stream_id=sid, ordered_timestamp_value_sha256=signatures[0][0],
                          observation_count=signatures[0][1], primary=group[0]["export_local_series_key"],
                          equivalent_export_columns=[r["export_local_series_key"] for r in group],
                          locator_rule="Primary row locator retained; aliases resolve by exact timestamp in original CSV"))
    return proof


def table_parquet(helper, path, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    write_csv(path, rows, fields)
    types = path.with_suffix(".types.csv")
    with types.open("w", newline="") as f:
        w = csv.writer(f)
        for name in fields:
            present = [r.get(name) for r in rows if r.get(name) is not None]
            typ = "string"
            if present and all(type(v) is bool for v in present): typ = "bool"
            elif present and all(type(v) is int for v in present): typ = "int64"
            elif present and all(type(v) in (int, float) for v in present): typ = "double"
            elif name == "depth_cm": typ = "double"
            w.writerow([name, typ])
    run([helper, "table", path, path.with_suffix(".parquet"), types])
    require(json.loads(run([helper, "readback", path.with_suffix(".parquet")]).stdout)["rows"] == len(rows), "Table readback differs")


def native_import(helper, input_dir, output, rows, files, profiles, block_bytes):
    native = output / "native"; native.mkdir(); controls = output / "controls"; controls.mkdir()
    qa, metrics, assets = [], [], []
    for name in files:
        selected = [r for r in rows if r["relative_asset_path"] == name and r["product_class"] in CLASSES[:2] and not r["duplicate_of"]]
        if not selected: continue
        ctl = controls / (files[name]["sha256"] + ".csv")
        with ctl.open("w", newline="") as f:
            w = csv.writer(f)
            for r in selected:
                w.writerow([r["column_ordinal_1_based"], r["asset_id"], r["export_local_series_key"],
                            r["export_unit_token"], r["conversion_multiplier"], r["proposed_stream_id"],
                            r["accepted_stream_id"], r["proposed_station_id"], r["depth_cm"], r["file_sha256"]])
        tick = time.monotonic()
        result = run([helper, "native", input_dir / name, ctl, native, block_bytes])
        stats = {r["asset"]: r for r in csv.DictReader(io.StringIO(result.stdout), delimiter="\t")}
        metric = json.loads(result.stderr); metric.update(file=name, elapsed_seconds=time.monotonic()-tick)
        metrics.append(metric)
        for r in selected:
            s = stats[r["asset_id"]]; p = profiles[r["export_local_series_key"]]
            for a, b in [("rows", "non_null_count"), ("zeros", "zeros"), ("negative", "negative"), ("duplicates", "duplicate_adjacent_timestamps")]:
                require(int(s[a]) == p[b], "Native/intake QA count mismatch: " + a)
            if int(s["rows"]):
                require(float(s["min"]) == p["minimum"] and float(s["max"]) == p["maximum"], "Native range differs")
            path = native / (r["asset_id"] + ".parquet")
            if path.exists():
                check = json.loads(run([helper, "readback", path]).stdout)
                require(check["rows"] == r["observation_count"], "Native Parquet row count differs")
                r.update(materialized=True, native_asset=path.relative_to(output).as_posix())
                assets.append(dict(path=r["native_asset"], sha256=sha(path), bytes=path.stat().st_size,
                                   rows=check["rows"], export_local_series_key=r["export_local_series_key"],
                                   proposed_stream_id=r["proposed_stream_id"], accepted_stream_id=r["accepted_stream_id"],
                                   station_id=r["proposed_station_id"], depth_cm=r["depth_cm"],
                                   source_file_sha256=r["file_sha256"], source_basename=name,
                                   source_column_ordinal_1_based=int(r["column_ordinal_1_based"]),
                                   first_timestamp_naive=p["first_observed_timestamp"], last_timestamp_naive=p["last_observed_timestamp"],
                                   product_class=r["product_class"], conversion_multiplier=r["conversion_multiplier"],
                                   metadata_evidence=json.loads(r["metadata_evidence"]),
                                   coverage_status="OBSERVATIONS_ONLY; blanks are not queried/complete-empty coverage"))
            qa.append(dict(**s, export_local_series_key=r["export_local_series_key"], station=r["proposed_station"],
                           stream_id=r["proposed_stream_id"], product_class=r["product_class"], depth_cm=r["depth_cm"],
                           depth_status=r["depth_status"], mapping_state=r["mapping_state"], native_unit=r["native_unit"],
                           multiplier=r["conversion_multiplier"], first_timestamp=p["first_observed_timestamp"],
                           last_timestamp=p["last_observed_timestamp"], observed_spacing=p["spacing_counts"],
                           min_spacing_seconds=p["minimum_positive_spacing_seconds"], max_spacing_seconds=p["maximum_positive_spacing_seconds"],
                           qa_rule="Diagnostic only; jumps >50 percentage points between adjacent observations; fixed sentinel candidates; no cleaning"))
        print(f"MATERIALIZED {name} targets={len(selected)} seconds={metric['elapsed_seconds']:.1f}", flush=True)
    by_key = {r["export_local_series_key"]: r for r in rows}
    for r in rows:
        if r["duplicate_of"]:
            primary = by_key[r["duplicate_of"]]
            r["native_asset"] = primary["native_asset"]
            q = next(x for x in qa if x["export_local_series_key"] == r["duplicate_of"]).copy()
            q.update(export_local_series_key=r["export_local_series_key"], duplicate_of=r["duplicate_of"])
            qa.append(q)
    return assets, qa, metrics


def daily_core(native_csv, output, start, end, context, core=None):
    """Invoke sole accepted R numerical authority, not a Python daily reduction."""
    core = core or REPO / "scripts/dendra/core.R"
    cfg = output.with_suffix(".input.json")
    write_json(cfg, dict(native_csv=str(native_csv), output=str(output), start=start, end=end, context=context,
                         stream=dict(datastream_id=CAMP, parameter="soil_moisture",
                                     unit_normalization=dict(status="verified_percent_conversion", multiplier=1, offset=0))))
    code = ('args<-commandArgs(TRUE); source(args[1]); c<-json_read(args[2]); '
            'x<-read_native(c$native_csv); r<-aggregate_daily(x,c$stream,c$start,c$end,c$context); '
            'json_write(list(rows=r$rows,summary=daily_summary(r$rows),policy=DENDRA_POLICY),c$output)')
    run(["Rscript", "--vanilla", "-e", code, core, cfg])
    return json.loads(output.read_text())


def compare_daily(new, prior, tolerance=1e-10):
    lookup = {r["date"]: r for r in prior}; comparisons = []
    categorical = ("water_year", "dowy", "water_day_aligned", "n_valid", "n_total", "n_out_of_range", "plot_eligible", "flags")
    numeric = ("mean_native", "mean_percent", "coverage_fraction", "temporal_span_fraction", "expected_samples", "cadence_seconds")
    for r in new:
        if r["date"] not in lookup: continue
        old = lookup[r["date"]]; differences = []
        exact = True
        for k in categorical:
            if r[k] != old[k]: differences.append(k)
        for k in numeric:
            if r[k] != old[k]:
                exact = False
                if r[k] is None or old[k] is None or abs(r[k]-old[k]) > tolerance: differences.append(k)
        comparisons.append(dict(date=r["date"], exact=exact and not differences, within_tolerance=not differences,
                                difference_fields=differences, mean_difference=None if r["mean_percent"] is None or old["mean_percent"] is None else r["mean_percent"]-old["mean_percent"]))
    return dict(overlap_days=len(comparisons), exact_matches=sum(r["exact"] for r in comparisons),
                tolerance_matches=sum(r["within_tolerance"] for r in comparisons),
                differences=sum(not r["within_tolerance"] for r in comparisons), tolerance=tolerance, days=comparisons)


def camp_fixture(helper, output, row, prior_root, object_root):
    """Accept only a bounded interval whose full CSV/time/value/quality binding is proven."""
    from dendra.history_acquisition.observation_quality import classify as quality_classify
    fixture = output / "camp_cady_20cm"; fixture.mkdir()
    handoff = json.loads((prior_root / "handoff.json").read_text())
    prior = json.loads((prior_root / "daily-output.json").read_text())
    s = next(x for x in handoff["streams"] if x["identity"]["stream_id"] == CAMP)
    require(s["identity"]["station_id"] == CAMP_STATION and s["identity"]["depth_cm"] == 20, "Prior fixture identity differs")
    require(sha(REPO / "scripts/dendra/core.R") == prior["science_binding"]["core_sha256"], "Prior/current daily science differs")
    for name in ("csv", "lineage"):
        require(sha(prior_root / s[name]["path"]) == s[name]["sha256"], "Prior fixture evidence hash differs")
    require(len(s["intervals"]) == 1, "Fixture requires one bounded retained interval")
    interval = s["intervals"][0]
    start = (datetime.fromisoformat(interval["start"].replace("Z", "+00:00"))-timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    end = (datetime.fromisoformat(interval["end"].replace("Z", "+00:00"))-timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    require(0 < (datetime.fromisoformat(end)-datetime.fromisoformat(start)).days <= 31, "Daily fixture exceeds bounded month")
    require(datetime.fromisoformat(interval["end"].replace("Z", "+00:00")) <= datetime.now(timezone.utc), "Uncompleted fixture interval")
    native_path = output / row["native_asset"]
    extracted = list(csv.DictReader(io.StringIO(run([helper, "extract", native_path, start, end]).stdout)))
    require(len(extracted) <= 50000, "Daily fixture row bound")
    lineage = json.loads((prior_root / s["lineage"]["path"]).read_text())
    records = lineage["records"]; require(len(records) == 1, "Fixture lineage closure")
    api = {}; held_days = set(); raw_refs = []
    for page in records[0]["envelope"]["pages"]:
        matches = list(object_root.rglob(page["response_sha256"] + ".bin"))
        require(len(matches) == 1, "Original API response not uniquely located")
        path = matches[0]; require(sha(path) == page["response_sha256"], "Original API response hash differs")
        payload = json.loads(path.read_text()); observations = payload if isinstance(payload, list) else payload["data"]
        raw_refs.append(dict(path=str(path), sha256=sha(path), rows=len(observations)))
        for obs in observations:
            t = datetime.fromisoformat(obs["t"].replace("Z", "+00:00"))
            naive = (t-timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
            value = float(obs["v"])
            require(naive not in api or api[naive] == value, "Conflicting prior API observations")
            api[naive] = value
            if quality_classify(obs)["quarantined"]: held_days.add(naive[:10])
    csv_values = {x["source_timestamp_naive"]: float(x["exported_value"]) for x in extracted}
    match = len(csv_values) == len(extracted) and csv_values == api
    native_csv = fixture / "science-input.csv"
    with native_csv.open("w", newline="") as f:
        w = csv.writer(f);w.writerow(["datastream_id", "t", "v", "value_status", "duplicate_conflict", "alternative_out_of_range"])
        for obs in extracted:
            utc = (datetime.fromisoformat(obs["source_timestamp_naive"])+timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
            w.writerow([CAMP, utc, obs["exported_value"], "number", False, False])
    science = daily_core(native_csv, fixture / "core-daily.json", start[:10], end[:10], prior["cadence_contexts"][CAMP])
    comparison = compare_daily(science["rows"], prior["rows"][CAMP])
    accepted = match and not held_days and comparison["differences"] == 0 and comparison["overlap_days"] == len(science["rows"])
    last = {x["source_timestamp_naive"][:10]: x["source_timestamp_naive"] for x in extracted}
    daily = []
    for r in science["rows"]:
        ok = accepted and r["plot_eligible"]
        daily.append(dict(station_id=CAMP_STATION, station_name="Camp Cady WA", stream_id=CAMP, depth_cm=20,
                          date_pst_fixed=r["date"], water_year=r["water_year"], dowy=r["dowy"], plot_day_aligned=r["water_day_aligned"],
                          daily_mean_vwc_percent=r["mean_percent"] if ok else None, diagnostic_mean_native=r["mean_native"],
                          valid_sample_count=r["n_valid"], expected_sample_count=r["expected_samples"],
                          sample_count_fraction=r["coverage_fraction"], first_last_span_fraction=r["temporal_span_fraction"],
                          daily_status="ACCEPTED" if ok else "WITHHELD", flags=r["flags"],
                          latest_source_timestamp_utc=(datetime.fromisoformat(last[r["date"]])+timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ") if r["date"] in last else None,
                          processing_version=science["policy"]))
    table_parquet(helper, fixture / "daily_fixed_pst.csv", daily)
    run([helper, "subset", native_path, start, end, fixture / "native.parquet"])
    require(json.loads(run([helper, "readback", fixture / "native.parquet"]).stdout)["rows"] == len(extracted), "Fixture native readback")
    write_csv(fixture / "native_sample.csv", extracted[:144])
    write_json(fixture / "comparison.json", comparison)
    manifest = dict(disposition="ACCEPTED_BOUNDED_DAILY_FIXTURE" if accepted else "DAILY_FIXTURE_HOLD",
                    native_rows=len(extracted), daily_rows=len(daily), interval_start_utc=interval["start"], interval_end_utc=interval["end"],
                    timestamp_semantics="CSV naive = fixed UTC-08; UTC = naive +8h; bounded full API match required",
                    full_interval_native_match=match, source_quality_held_days=sorted(held_days),
                    source_quality_scope="Only these preserved API responses; no whole-export quality acceptance",
                    prior_daily=dict(path=str(prior_root / "daily-output.json"), sha256=sha(prior_root / "daily-output.json")),
                    prior_handoff=dict(path=str(prior_root / "handoff.json"), sha256=sha(prior_root / "handoff.json")),
                    prior_lineage=dict(path=str(prior_root / s["lineage"]["path"]), sha256=s["lineage"]["sha256"]),
                    original_api_responses=raw_refs, interval=interval, core_sha256=sha(REPO / "scripts/dendra/core.R"),
                    comparison=comparison, files=[dict(path=p.name, sha256=sha(p), bytes=p.stat().st_size) for p in sorted(fixture.iterdir()) if p.is_file()])
    write_json(fixture / "manifest.json", manifest)
    return manifest


def import_library(input_dir, intake, output, prior_root, object_root, helper=None, block_bytes=1048576):
    started = time.monotonic()
    require(16384 <= block_bytes <= 4194304, "Arrow CSV block must be 16 KiB..4 MiB")
    require(not output.exists() and not output.resolve().is_relative_to(REPO), "Use a fresh output root outside repository")
    rows, files, profiles = preflight(input_dir, intake)
    duplicates = resolve_duplicates(input_dir, rows)
    camp = [r for r in rows if r["proposed_stream_id"] == CAMP]
    require(len(camp) == 1 and camp[0]["proposed_station_id"] == CAMP_STATION and camp[0]["depth_cm"] == 20
            and camp[0]["original_header"] == "camp-cady-wa-soil-moisture-200-mm-average-pct" and camp[0]["product_class"] == CLASSES[0], "Camp fixture mapping differs")
    camp[0].update(accepted_stream_id=CAMP, mapping_state="EXACT_REVIEWED_CAMP_FIXTURE", daily_eligibility="BOUNDED_FIXTURE_REVIEW_ONLY")
    output.mkdir(parents=True)
    try:
        helper = helper or compile_helper(output / "bulk-csv-arrow")
        source_hashes = {p.name: sha(p) for p in [Path(__file__), Path(__file__).with_name("bulk_csv_arrow.cpp"), REPO / "scripts/dendra/core.R"]}
        write_json(output / "IMPORT_INPUT_BINDING.json", dict(version=VERSION, input_directory=str(input_dir),
            intake_files={n: sha(intake/n) for n in ("BULK_EXPORT_INVENTORY.json", "BULK_SERIES_CROSSWALK.csv", "BULK_INTAKE_REPORT.md")},
            originals=[dict(basename=f["basename"], sha256=f["sha256"], bytes=f["bytes"]) for f in files.values()],
            source_sha256=source_hashes, generated_at=datetime.now(timezone.utc).isoformat()))
        assets, qa, metrics = native_import(helper, input_dir, output, rows, files, profiles, block_bytes)
        fixture = camp_fixture(helper, output, camp[0], prior_root, object_root)
        table_parquet(helper, output / "SERIES_CATALOG.csv", rows)
        write_csv(output / "VWC_QA_SUMMARY.csv", qa)
        excluded = [dict(export_local_series_key=r["export_local_series_key"], source_basename=r["relative_asset_path"],
                         source_file_sha256=r["file_sha256"], column_ordinal_1_based=r["column_ordinal_1_based"],
                         header=r["original_header"], product_class=r["product_class"], reason=r["classification_reason"],
                         observation_count=r["observation_count"], all_null=r["observation_count"] == 0)
                    for r in rows if r["product_class"] not in CLASSES[:2]]
        write_csv(output / "EXCLUDED_CHANNELS.csv", excluded)
        write_json(output / "NATIVE_VWC_ASSET_MANIFEST.json", dict(version=VERSION, assets=assets, duplicate_selections=duplicates,
            catalog=dict(path="SERIES_CATALOG.parquet", sha256=sha(output/"SERIES_CATALOG.parquet")),
            all_null_targets=[r["export_local_series_key"] for r in rows if r["product_class"] in CLASSES[:2] and not r["observation_count"]],
            metadata_resolution="Join export_local_series_key to catalog; never parse filenames",
            source_quality="CSV contains no row quality flags; daily acceptance limited to bound Camp fixture",
            source_timestamp_utc=None, timestamp_semantics="Naive timestamps preserved; global UTC assignment prohibited",
            gaps="No filling; export blanks do not prove complete-empty queries", source_sha256=source_hashes))
        for name, item in files.items():
            require(sha(input_dir / name) == item["sha256"], "Original changed during import")
        accounting = {c: dict(columns=sum(r["product_class"] == c for r in rows),
                             all_null=sum(r["product_class"] == c and not r["observation_count"] for r in rows),
                             source_non_null_cells=sum(r["observation_count"] for r in rows if r["product_class"] == c)) for c in CLASSES}
        result = dict(result="READY_FOR_BULK_DAILY_PRODUCTIZATION" if fixture["disposition"] == "ACCEPTED_BOUNDED_DAILY_FIXTURE" else "VWC_NATIVE_READY_DAILY_FIXTURE_HOLD",
                      version=VERSION, output_root=str(output), column_count=len(rows), accounting=accounting,
                      materialized_assets=len(assets), materialized_rows=sum(a["rows"] for a in assets), native_bytes=sum(a["bytes"] for a in assets),
                      duplicates=duplicates, camp_fixture=fixture, original_hashes_verified_before_and_after=True,
                      performance=dict(elapsed_seconds=time.monotonic()-started, csv_block_bytes=block_bytes, files=metrics,
                                       parent_peak_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                                       children_peak_rss=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                                       rss_unit="bytes on macOS; KiB on Linux", global_sort=False),
                      provider_requests=0, cloud_actions=0, original_csv_writes=0)
        write_json(output / "BULK_IMPORT_RESULT.json", result)
        write_json(output / "SCHEMA_AND_LIMITATIONS.json", dict(version=VERSION, native_schema=run(["parquet-dump-schema", output/assets[0]["path"]]).stdout,
            catalog_fields=list(rows[0]), timestamp_policy="Native timezone-free seconds; only Camp fixture fixed UTC-08 accepted",
            mapping_policy="Exact export-local identity; provider IDs are proposals except reviewed Camp Cady; no other mapping promoted",
            unit_policy="Source-supported Percent x1 / fractional VWC x100; export curation remains unverified outside bounded comparison",
            source_quality_policy="All broad daily products remain gated on source/time/quality review",
            qa_policy="No clipping, substitution, interpolation, forward-fill; jumps/sentinels are diagnostic flags only"))
        package = output / "L03_REVIEW_PACKAGE.zip"
        with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as z:
            for name in ("BULK_IMPORT_RESULT.json", "IMPORT_INPUT_BINDING.json", "SERIES_CATALOG.csv", "EXCLUDED_CHANNELS.csv", "VWC_QA_SUMMARY.csv", "NATIVE_VWC_ASSET_MANIFEST.json", "SCHEMA_AND_LIMITATIONS.json", "camp_cady_20cm/manifest.json", "camp_cady_20cm/comparison.json", "camp_cady_20cm/daily_fixed_pst.csv", "camp_cady_20cm/native_sample.csv"):
                z.write(output/name, name)
        require(package.stat().st_size <= 5*1024*1024, "Review ZIP exceeds 5 MiB")
        return result
    except Exception as exc:
        write_json(output / "IMPORT_HOLD.json", dict(result="HOLD", error=str(exc), originals_written=False, partial_outputs_accepted=False))
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ("input-dir", "intake-root", "output-root", "prior-daily-root", "api-object-root"):
        p.add_argument("--"+arg, type=Path, required=True)
    p.add_argument("--helper", type=Path)
    p.add_argument("--block-bytes", type=int, default=1048576)
    a = p.parse_args()
    result = import_library(a.input_dir, a.intake_root, a.output_root, a.prior_daily_root, a.api_object_root, a.helper, a.block_bytes)
    print(json.dumps({k: result[k] for k in ("result", "materialized_assets", "materialized_rows", "native_bytes")}))


if __name__ == "__main__":
    sys.path.insert(0, str(REPO / "scripts"))
    main()
