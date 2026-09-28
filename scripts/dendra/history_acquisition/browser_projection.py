"""Offline, descriptor-driven projection of explicitly pinned prepared science.

No retrieval, R execution, publication, or journal mutation. Hash pins are caller
trust inputs, not authentication. Validation reconstructs the deterministic graph
from its pinned input metadata and accepted rows; numerical science stays in R.
"""
from datetime import date, datetime, timedelta, timezone
import calendar
import math
import os
from pathlib import Path
import re

from .safety import Root, decode, digest, encode, require, sha
from .model import ID, HASH, INVENTORY_SHA256
from ..transport import parse_utc, format_utc

SCHEMA = "brim-soil-history-1"
PROFILE = "dendra-history-profile-1"
DAILY = "dendra-completed-daily-projection-1"
LATEST = "dendra-latest-instantaneous-projection-1"
INPUT = "dendra-browser-input-1"
PST = timezone(timedelta(hours=-8))
LIMITS = dict(streams=32, water_years=10, rows_per_shard=366,
              body_bytes=1048576, root_bytes=262144, files=386,
              delivery_bytes=33554432, input_bytes=8388608)
CAPABILITY_POLICY = dict(hover_30d="selected_stream_30_completed_fixed_pst_days",
    history_last3="current_ending_WY_and_immediately_preceding_two",
    history_all_available="accepted_acquired_daily_in_product_horizon_not_provider_POR",
    reference_band="not_computed")
POLICY = dict(version=PROFILE, fixed_pst_offset="-08:00", limits=LIMITS,
    capabilities=CAPABILITY_POLICY, geometry="omitted", latest="unavailable_in_sealed_preparation",
    normal_activation=dict(history_body_bytes=0, hover_body_bytes=0, required_capability_payloads=[]))
ROW_FIELDS = set("date water_year dowy water_day water_year_days water_day_aligned mean_native mean_percent mean_value n_valid n_total n_null n_invalid n_missing n_duplicate_conflicts n_duplicate_rows n_out_of_range expected_samples cadence_seconds cadence_source coverage_fraction temporal_span_fraction plot_eligible flags representation identity query_complete presentation_eligible source_intervals".split())
INTERVAL_FIELDS = set("task_id start end query_state seal_record_sha256 content_sha256 parsed_sha256 row_count".split())
SOURCE_FIELDS = set("task_id query_state seal_record_sha256 content_sha256 parsed_sha256".split())


def exact(value, fields):
    require(type(value) is dict and set(value) == set(fields), "Exact object fields required")


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) <= 2**53


def hash_value(value):
    require(isinstance(value, str) and HASH.fullmatch(value), "SHA-256 required")


def identity(value):
    exact(value, "station_id stream_id native_unit unit_status depth_cm orientation".split())
    require(all(isinstance(value[k], str) and ID.fullmatch(value[k]) for k in ("station_id", "stream_id")), "Exact station/stream identity")
    unit = value["native_unit"]
    require(unit in ("Percent", "VolumetricWaterContent", "Dimensionless"), "Unsupported unit")
    require(value["unit_status"] == ("native_only_scale_unresolved" if unit == "Dimensionless" else "verified_percent_conversion"), "Scale state")
    require(value["depth_cm"] is None or finite(value["depth_cm"]), "Depth type")
    require(value["orientation"] is None or (type(value["orientation"]) is str and len(value["orientation"]) <= 64), "Orientation type")


def horizon(as_of):
    now = parse_utc(as_of).astimezone(PST)
    wy = now.year + (now.month >= 10)
    cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return dict(mode="INITIAL_PRESENTATION_10_WY", maximum_water_year_count=10,
        current_water_year=wy, earliest_water_year=wy-9, fixed_pst_offset="-08:00",
        completed_before=format_utc(cutoff.astimezone(timezone.utc)), last_completed_date=(cutoff.date()-timedelta(days=1)).isoformat(),
        floor_date=f"{wy-10}-10-01", full_por_complete=False, full_por_state="not_established")


def validate_path(descriptor):
    exact(descriptor, "kind path bytes sha256 generation station_id stream_id water_year row_count first_date last_date".split())
    path = descriptor["path"]
    Root.parts(path)
    require(re.fullmatch(r"[a-z0-9/.-]+", path) is not None, "No encoded, URL, or ambiguous path")
    hash_value(descriptor["sha256"]); hash_value(descriptor["generation"])
    require(type(descriptor["bytes"]) is int and 0 < descriptor["bytes"] <= LIMITS["body_bytes"], "Body size bound")
    kind, sid, wy = descriptor["kind"], descriptor["stream_id"], descriptor["water_year"]
    if kind == "map-index":
        require(sid is None and descriptor["station_id"] is None and wy is None, "Index identity")
        prefix = "map-index/0"
    else:
        require(isinstance(sid, str) and ID.fullmatch(sid) and isinstance(descriptor["station_id"], str) and ID.fullmatch(descriptor["station_id"]), "Path stream identity")
        require(kind in ("streams", "hover", "history"), "Object kind/prefix")
        if kind == "history":
            require(type(wy) is int and 1000 <= wy <= 9999, "Path WY")
            prefix = f"history/{sid}/{wy}"
        else:
            require(wy is None, "Unexpected WY")
            prefix = kind+"/"+sid
    require(path == prefix+"-"+descriptor["sha256"]+".json", "Path kind/identity/hash mismatch")
    require(type(descriptor["row_count"]) is int and descriptor["row_count"] >= 0, "Row count")


def _coverage(intervals, lo, hi):
    cursor, contributors = lo, []
    for item in intervals:
        start, end = parse_utc(item["start"]), parse_utc(item["end"])
        if start < hi and end > lo:
            contributors.append(item)
            if start <= cursor:
                cursor = max(cursor, end)
    return cursor >= hi, contributors


def validate_row(row, meta, view):
    exact(row, ROW_FIELDS)
    require(row["identity"] == meta["identity"] and meta["identity"]["native_unit"] != "Dimensionless", "Row identity/scale")
    day = date.fromisoformat(row["date"])
    require(day.isoformat() == row["date"] and view["floor_date"] <= row["date"] <= view["last_completed_date"], "Incomplete/out-of-horizon daily date")
    wy = day.year + (day.month >= 10)
    dowy = (day-date(wy-1, 10, 1)).days+1
    aligned = dowy + (not calendar.isleap(wy) and day >= date(wy, 3, 1))
    require((row["water_year"], row["dowy"], row["water_day"], row["water_day_aligned"], row["water_year_days"]) ==
            (wy, dowy, dowy, aligned, 366 if calendar.isleap(wy) else 365), "WY/date/leap fields")
    require(all(row[k] is True for k in ("plot_eligible", "query_complete", "presentation_eligible")) and row["representation"] == "completed_daily", "Only accepted completed_daily")
    require(all(finite(row[k]) for k in ("mean_native", "mean_percent", "mean_value", "cadence_seconds", "coverage_fraction", "temporal_span_fraction")), "Null/nonfinite daily numeric")
    factor = 1 if meta["identity"]["native_unit"] == "Percent" else 100
    require(0 <= row["mean_percent"] <= 100 and math.isclose(row["mean_native"]*factor, row["mean_percent"], abs_tol=1e-10) and row["mean_value"] == row["mean_percent"], "Resolved normalization")
    require(row["cadence_seconds"] > 0 and 0 <= row["coverage_fraction"] <= 1 and 0 <= row["temporal_span_fraction"] <= 1, "QC fractions")
    counts = [k for k in ROW_FIELDS if k.startswith("n_")] + ["expected_samples"]
    require(all(type(row[k]) is int and row[k] >= 0 for k in counts) and row["n_valid"] > 0 and row["n_total"] >= row["n_valid"], "Sample counts")
    require(type(row["flags"]) is list and all(type(f) is str and len(f) <= 80 for f in row["flags"]) and type(row["cadence_source"]) is str, "QC encoding")
    lo = datetime.combine(day, datetime.min.time(), PST)
    complete, contributors = _coverage(meta["intervals"], lo, lo+timedelta(days=1))
    require(complete and row["source_intervals"] == [{k:i[k] for k in SOURCE_FIELDS} for i in contributors], "Daily query/seal lineage")


def _meta_check(meta):
    exact(meta, "identity intervals lineage_sha256 metadata_review_sha256 scale_decision_sha256 retrieval_first_utc retrieval_last_utc historical_terminal_source_timestamp source_start_authority rejected_dates".split())
    identity(meta["identity"])
    require(meta["source_start_authority"] == "UNKNOWN_SOURCE_START", "No source-start authority promotion")
    for k in ("lineage_sha256", "metadata_review_sha256", "scale_decision_sha256"): hash_value(meta[k])
    require(parse_utc(meta["retrieval_first_utc"]) <= parse_utc(meta["retrieval_last_utc"]), "Retrieval order")
    if meta["historical_terminal_source_timestamp"] is not None: parse_utc(meta["historical_terminal_source_timestamp"])
    require(type(meta["intervals"]) is list and 0 < len(meta["intervals"]) <= 128, "Interval bound")
    previous = None
    for item in meta["intervals"]:
        exact(item, INTERVAL_FIELDS)
        for k in ("task_id", "seal_record_sha256", "content_sha256", "parsed_sha256"): hash_value(item[k])
        lo, hi = parse_utc(item["start"]), parse_utc(item["end"])
        require(lo < hi and (previous is None or previous <= lo), "Interval overlap/order")
        previous = hi
        require(type(item["row_count"]) is int and item["row_count"] >= 0 and item["query_state"] == ("COVERED_EMPTY" if item["row_count"] == 0 else "COMPLETE_NONEMPTY"), "Covered-empty count")
    require(meta["rejected_dates"] == sorted(set(meta["rejected_dates"])), "Rejected date ordering")
    for day in meta["rejected_dates"]: date.fromisoformat(day)


def load_prepared(prepared_root, *, pins, prepared_fingerprint):
    """Pins bind accepted preparation, not a request to recalculate or resume it."""
    hash_value(prepared_fingerprint)
    require(type(pins) is list and 0 < len(pins) <= 100, "Bounded explicit input pins")
    docs = {}
    with Root(prepared_root) as source:
        for pin in pins:
            exact(pin, ("path", "bytes", "sha256")); hash_value(pin["sha256"])
            name = pin["path"]
            require(name not in docs and (name in ("handoff.json", "daily-output.json", "result.json", "r-receipt.json") or re.fullmatch(r"lineage/[0-9a-f]{24}\.json", name)), "Input closure")
            body = source.read(name, LIMITS["input_bytes"])
            require(len(body) == pin["bytes"] and sha(body) == pin["sha256"], "Prepared input changed")
            docs[name] = decode(body)
    h, d = docs["handoff.json"], docs["daily-output.json"]
    require(h["schema_version"] == d["schema_version"] and h["schema_version"] in
            ("dendra-sealed-daily-handoff-1", "dendra-sealed-daily-handoff-2", "dendra-sealed-daily-handoff-3") and
            h["science_binding"] == d["science_binding"] and h["science_binding"]["collector_fingerprint"] == prepared_fingerprint, "Preparation source mismatch")
    require(sha((Path(__file__).parent.parent/"core.R").read_bytes()) == h["science_binding"]["core_sha256"], "Numerical authority changed")
    require(h["source_scope"] == "historical_sealed_intervals" and h["as_of"] == d["as_of"] and h["latest_instantaneous"] == d["latest_instantaneous"] == [] and h["publication_eligible"] is False and d["publication_eligible"] is False, "Historical preparation only; no latest witness")
    require(docs["r-receipt.json"]["exit_code"] == 0 and docs["result.json"]["outcome"] == "OFFLINE_DAILY_PREPARED" and docs["result.json"]["science_binding"] == h["science_binding"], "Unaccepted daily preparation")
    compact = h["schema_version"] == "dendra-sealed-daily-handoff-3"
    if compact:
        require(docs["result.json"]["daily_output_sha256"] == digest(d), "Daily output binding changed")
    view = horizon(d["as_of"])
    require(d["completed_fixed_pst_cutoff"] == view["last_completed_date"] and d["daily_schema"] == "dendra-daily-1.0.0" and d["numerical_policy"] == "dendra-daily-1.0.0-frozen-cadence", "Daily policy/cutoff")
    streams, rows = {}, {}
    for s in h["streams"]:
        ident = s["identity"]; identity(ident); sid = ident["stream_id"]
        require(sid not in streams, "Duplicate stream")
        lineage = docs["lineage/"+sid+".json"]
        require(lineage["identity"] == ident and next(p for p in pins if p["path"] == s["lineage"]["path"]) == s["lineage"], "Lineage pin/identity")
        factor = {"Percent":1, "VolumetricWaterContent":100}.get(ident["native_unit"])
        if compact:
            from .sealed_history import validate_compact
            refs = validate_compact(lineage, s, h)
            review_hash, scale_hash = lineage["review_set_sha256"], lineage["scale_set_sha256"]
            retrieval_first = min(r["retrieval_first_utc"] for r in refs)
            retrieval_last = max(r["retrieval_last_utc"] for r in refs)
        else:
            records = lineage["records"]
            require(records and all(r["task"]["identity"] == ident and r["decision"]["scale"] == lineage["scale"] for r in records), "Lineage scale identity")
            require(lineage["scale"]["normalized_percent_eligible"] == (factor is not None) and lineage["scale"]["conversion_factor"] == factor, "Unaccepted percent scale")
            expected_intervals = [dict(task_id=r["task_id"], start=r["task"]["start"], end=r["task"]["end"], query_state="COVERED_EMPTY" if not r["envelope"]["rows"] else "COMPLETE_NONEMPTY", seal_record_sha256=r["seal_record_sha256"], content_sha256=r["envelope"]["content_sha256"], parsed_sha256=r["seal"]["objects"][0]["sha256"], row_count=len(r["envelope"]["rows"])) for r in records]
            require(s["intervals"] == expected_intervals, "Preparation interval lineage")
            review_hash, scale_hash = digest([r["decision"] for r in records]), lineage["scale"]["decision_sha256"]
            retrieval_first = min(r["envelope"]["retrieval_first_utc"] for r in records)
            retrieval_last = max(r["envelope"]["retrieval_last_utc"] for r in records)
        source_rows = d["rows"].get(sid, [])
        if h["schema_version"] in ("dendra-sealed-daily-handoff-2", "dendra-sealed-daily-handoff-3"):
            if not compact:
                from .daily_handoff import disposition
                native_rows = [r for record in records for r in record["science_rows"]]
                require(all("q" not in r for r in native_rows +
                            [r for record in records for r in record["envelope"]["rows"]]), "Exact quality is native-evidence-only")
                require(s["quarantine"] == disposition(native_rows), "Quality disposition lineage mismatch")
            withheld = set(s["quarantine"]["withheld_days"])
            for row in source_rows:
                if row["date"] in withheld:
                    require(row["plot_eligible"] is False and row["presentation_eligible"] is False and
                            all(row[k] is None for k in ("mean_native", "mean_percent", "mean_value")),
                            "Quarantined day cannot enter browser science")
            terminal = s["historical_terminal"]
            if terminal and terminal["source_fixed_pst_date"] in withheld:
                require(terminal["native_value"] is None and terminal["normalized_percent"] is None,
                        "Quarantined terminal value")
        require(not source_rows or factor is not None, "Dimensionless daily denied")
        require(len({x["date"] for x in source_rows}) == len(source_rows), "Duplicate daily date")
        accepted = [x for x in source_rows if x["presentation_eligible"] is True]
        rows[sid] = [x for x in accepted if view["floor_date"] <= x["date"] <= view["last_completed_date"]]
        require(all(x["date"] <= view["last_completed_date"] for x in accepted), "Incomplete day in accepted output")
        terminal = s["historical_terminal"]
        require(terminal is None or (terminal["representation"] == "historical_terminal" and terminal["latest_witness"] is False), "Historical terminal is not latest")
        streams[sid] = dict(identity=ident, intervals=s["intervals"], lineage_sha256=s["lineage"]["sha256"],
            metadata_review_sha256=review_hash, scale_decision_sha256=scale_hash,
            retrieval_first_utc=retrieval_first, retrieval_last_utc=retrieval_last,
            historical_terminal_source_timestamp=terminal["source_timestamp"] if terminal else None,
            source_start_authority="UNKNOWN_SOURCE_START", rejected_dates=sorted(x["date"] for x in source_rows if x["query_complete"] is True and x["plot_eligible"] is False))
    require(set(d["rows"]) <= set(streams) and set(d["summaries"]) == set(streams), "Unexpected prepared stream")
    require(set(docs) == {"handoff.json", "daily-output.json", "result.json", "r-receipt.json"} | {"lineage/"+s+".json" for s in streams}, "Exact input closure")
    inputs = dict(schema_version=INPUT, inventory_sha256=INVENTORY_SHA256, preparation_files=sorted(pins, key=lambda x:x["path"]),
        preparation_science=h["science_binding"], acquisition=h["evidence_binding"], as_of=d["as_of"],
        daily_schema=d["daily_schema"], numerical_policy=d["numerical_policy"], streams=streams)
    # Acquisition checkpoint may contain local source paths? Export only hashes/versions.
    inputs["acquisition"] = {k:h["evidence_binding"][k] for k in ("acquisition_fingerprint", "evidence_manifest_sha256", "execution_binding_sha256", "task_binding_sha256")}
    return inputs, rows


def render(inputs, rows):
    """Pure deterministic projection; inputs carry authoritative source hashes only."""
    exact(inputs, "schema_version inventory_sha256 preparation_files preparation_science acquisition as_of daily_schema numerical_policy streams".split())
    require(inputs["schema_version"] == INPUT and inputs["inventory_sha256"] == INVENTORY_SHA256, "Input version/inventory")
    require(inputs["daily_schema"] == "dendra-daily-1.0.0" and inputs["numerical_policy"] == "dendra-daily-1.0.0-frozen-cadence", "Input science policy")
    exact(inputs["preparation_science"], ("collector_fingerprint", "core_sha256", "wrapper_sha256"))
    exact(inputs["acquisition"], ("acquisition_fingerprint", "evidence_manifest_sha256", "execution_binding_sha256", "task_binding_sha256"))
    for value in list(inputs["preparation_science"].values())+list(inputs["acquisition"].values()): hash_value(value)
    expected_pins = {"handoff.json", "daily-output.json", "result.json", "r-receipt.json"} | {"lineage/"+s+".json" for s in inputs["streams"]}
    require(type(inputs["preparation_files"]) is list and len(inputs["preparation_files"]) == len(expected_pins) and {p["path"] for p in inputs["preparation_files"]} == expected_pins, "Input pin closure")
    for pin in inputs["preparation_files"]:
        exact(pin, ("path", "sha256", "bytes")); hash_value(pin["sha256"])
        require(type(pin["bytes"]) is int and 0 < pin["bytes"] <= LIMITS["input_bytes"], "Input byte bound")
    require(0 < len(inputs["streams"]) <= LIMITS["streams"] and set(rows) == set(inputs["streams"]), "Selected stream closure")
    view = horizon(inputs["as_of"])
    generation = digest(dict(input_binding=inputs, projection_policy=POLICY))
    files, descriptions = {}, []
    def header(kind):
        return dict(schema_version=SCHEMA, network="Dendra", profile=PROFILE, generation=generation, kind=kind)
    def store(kind, payload, ident=None, wy=None, dates=()):
        value = dict(header(kind), **payload); body = encode(value); checksum = sha(body)
        sid = ident["stream_id"] if ident else None
        prefix = "map-index/0" if kind == "map-index" else f"history/{sid}/{wy}" if kind == "history" else f"{kind}/{sid}"
        desc = dict(kind=kind, path=prefix+"-"+checksum+".json", bytes=len(body), sha256=checksum, generation=generation,
            station_id=ident["station_id"] if ident else None, stream_id=sid, water_year=wy,
            row_count=len(dates), first_date=min(dates) if dates else None, last_date=max(dates) if dates else None)
        validate_path(desc); files[desc["path"]] = body; descriptions.append(desc); return desc
    entries = []
    for sid, meta in sorted(inputs["streams"].items()):
        _meta_check(meta); ident = meta["identity"]; require(sid == ident["stream_id"], "Selected stream key")
        selected = rows[sid]; require(type(selected) is list and len(selected) <= 3660, "Daily row bound")
        dates = [r["date"] for r in selected]
        require(dates == sorted(set(dates)), "Duplicate/unsorted daily date")
        require(not set(dates) & set(meta["rejected_dates"]), "Accepted/rejected conflict")
        for row in selected: validate_row(row, meta, view)
        resolved = ident["native_unit"] != "Dimensionless"
        require(resolved or not selected, "Dimensionless numeric denial")
        shards = []
        for wy in sorted({r["water_year"] for r in selected}):
            rr = [r for r in selected if r["water_year"] == wy]
            require(len(rr) <= LIMITS["rows_per_shard"], "Shard count bound")
            shards.append(store("history", dict(payload_schema=DAILY, representation="completed_daily", identity=ident,
                water_year=wy, lineage_sha256=meta["lineage_sha256"], rows=rr), ident, wy, [r["date"] for r in rr]))
        def cap(status, reason, **extra):
            return dict(status=status, reason=reason, generation=generation, **extra)
        capabilities = {k:cap("unavailable", "scale_unresolved") for k in CAPABILITY_POLICY}
        capabilities["reference_band"] = cap("unavailable", "not_computed")
        if resolved:
            slots = []
            byday = {r["date"]:r for r in selected}
            end = date.fromisoformat(view["last_completed_date"])
            for offset in range(29, -1, -1):
                day = end-timedelta(days=offset); ds = day.isoformat()
                lo = datetime.combine(day, datetime.min.time(), PST)
                complete, contributors = _coverage(meta["intervals"], lo, lo+timedelta(days=1))
                state = "accepted" if ds in byday else "ineligible" if ds in meta["rejected_dates"] else "covered_empty" if complete and all(i["query_state"] == "COVERED_EMPTY" for i in contributors) else "missing_measurement" if complete else "unqueried"
                slots.append(dict(date=ds, state=state, mean_percent=byday[ds]["mean_percent"] if ds in byday else None))
            hover = store("hover", dict(payload_schema="dendra-hover-1", identity=ident, days=30, completed_before=view["completed_before"], slots=slots), ident, dates=[s["date"] for s in slots])
            capabilities["hover_30d"] = cap("available", "selected_stream_calendar_window", file=hover)
            years = []
            for wy in range(view["current_water_year"]-2, view["current_water_year"]+1):
                shard = next((x for x in shards if x["water_year"] == wy), None)
                lo, hi = datetime(wy-1, 10, 1, tzinfo=PST), datetime(wy, 10, 1, tzinfo=PST)
                complete, contributors = _coverage(meta["intervals"], lo, hi)
                state = "available_acquired_shard" if shard else "acquired_empty" if contributors and all(x["query_state"] == "COVERED_EMPTY" for x in contributors) else "unavailable_held" if contributors else "unqueried_not_acquired"
                years.append(dict(water_year=wy, state=state, reason=state, query_complete_for_water_year=complete, file=shard))
            capabilities["history_last3"] = cap("available", "display_selection_not_coverage_claim", years=years)
            capabilities["history_all_available"] = cap("available", "acquired_product_horizon_only", files=shards)
        coverage = dict(acquired_start=min(i["start"] for i in meta["intervals"]), acquired_end=max(i["end"] for i in meta["intervals"]),
            completeness="listed_intervals_complete_only", horizon_complete=False, intervals=meta["intervals"], source_start_authority=meta["source_start_authority"])
        descriptor = store("streams", dict(payload_schema="dendra-stream-descriptor-1", identity=ident, scale_state="RESOLVED_PERCENT" if resolved else "NORMALIZED_PERCENT_HOLD",
            normalized_target_unit="Percent" if resolved else None, coverage=coverage, freshness=dict(
                evaluated_at=inputs["as_of"], retrieval_first_utc=meta["retrieval_first_utc"], retrieval_last_utc=meta["retrieval_last_utc"],
                historical_terminal_source_timestamp=meta["historical_terminal_source_timestamp"], current_state_claim=False),
            last_accepted_completed_day=dates[-1] if dates else None, presentation_horizon=view,
            capabilities=capabilities, history_shards=shards, point_semantics="completed_daily_separate_from_latest_instantaneous",
            latest_instantaneous=dict(schema_version=LATEST, status="UNAVAILABLE", reason="no_latest_witness_in_sealed_historical_input", record=None),
            full_por_complete=False, full_por_state="not_established", lineage_sha256=meta["lineage_sha256"]), ident)
        entries.append(dict(station_id=ident["station_id"], stream_id=sid, descriptor=descriptor))
    page = store("map-index", dict(payload_schema="dendra-map-index-1", entries=entries))
    manifest = dict(header("manifest"), manifest_schema="dendra-browser-manifest-1", publication_state="offline_export_only", input_binding=inputs, projection_policy=POLICY,
        map_indexes=[page], files=sorted(descriptions, key=lambda d:d["path"]), presentation_horizon=view,
        capability_policy=CAPABILITY_POLICY, normal_activation=POLICY["normal_activation"], limits=LIMITS,
        closure=dict(stream_count=len(entries), station_count=len({e["station_id"] for e in entries}),
            body_file_count=len(files), body_bytes=sum(map(len, files.values())), accepted_daily_rows=sum(map(len, rows.values()))),
        full_por_complete=False, publication_receipt=None)
    files["manifest.json"] = encode(manifest)
    require(len(files["manifest.json"]) <= LIMITS["root_bytes"] and len(files) <= LIMITS["files"] and sum(map(len, files.values())) <= LIMITS["delivery_bytes"], "Delivery bounds")
    return files


def validate_delivery(root, *, manifest_sha256, generation):
    """Full offline closure validator. Consumers validate only selected bodies."""
    hash_value(manifest_sha256); hash_value(generation)
    with Root(root) as source:
        raw = source.read("manifest.json", LIMITS["root_bytes"])
        require(sha(raw) == manifest_sha256, "Root revision hash")
        manifest = decode(raw)
        require(manifest["generation"] == generation and manifest["profile"] == PROFILE and manifest["schema_version"] == SCHEMA, "Generation/profile mismatch")
        descriptors = manifest["files"]
        require(type(descriptors) is list and len(descriptors) < LIMITS["files"], "File count bound")
        for desc in descriptors: validate_path(desc)
        require(sum(d["bytes"] for d in descriptors)+len(raw) <= LIMITS["delivery_bytes"], "Aggregate delivery bound")
        rows = {sid:[] for sid in manifest["input_binding"]["streams"]}
        actual = {"manifest.json":raw}
        for desc in descriptors:
            validate_path(desc); require(desc["generation"] == generation and desc["path"] not in actual, "Mixed generation/duplicate descriptor")
            body = source.read(desc["path"], min(desc["bytes"], LIMITS["body_bytes"]))
            require(len(body) == desc["bytes"] and sha(body) == desc["sha256"], "Descriptor bytes/hash mismatch")
            value = decode(body)
            require(value["generation"] == generation and value["kind"] == desc["kind"], "Mixed body generation/kind")
            if desc["kind"] == "history":
                require(desc["stream_id"] in rows, "Unrelated stream body")
                rows[desc["stream_id"]].extend(value["rows"])
            actual[desc["path"]] = body
        for rr in rows.values(): rr.sort(key=lambda x:x["date"])
        expected = render(manifest["input_binding"], rows)
        require(actual == expected, "Projection schema/semantic/closure mismatch")
        # Bounded inventory of this delivery only; never traverses evidence roots.
        def walk(prefix=""):
            names = []
            for name in source.list(prefix or None, LIMITS["files"]):
                rel = prefix+"/"+name if prefix else name
                require(len(rel.split("/")) <= 3, "Delivery depth")
                if rel in expected: names.append(rel)
                else:
                    require(any(p.startswith(rel+"/") for p in expected), "Unexpected delivery path")
                    names.extend(walk(rel))
            return names
        require(set(walk()) == set(expected), "Delivery file closure")
    return dict(generation=generation, manifest_sha256=manifest_sha256, files=len(actual), bytes=sum(map(len, actual.values())))


def export(prepared_root, *, pins, prepared_fingerprint, output_root):
    inputs, rows = load_prepared(prepared_root, pins=pins, prepared_fingerprint=prepared_fingerprint)
    files = render(inputs, rows)
    out = Path(output_root)
    require(out.is_absolute() and ".." not in out.parts and out != Path(prepared_root) and Path(prepared_root) not in out.parents, "Fresh separate absolute output root")
    with Root(out.parent) as parent: os.mkdir(out.name, 0o700, dir_fd=parent.fd)
    with Root(out) as target:
        for path, body in sorted(files.items()): target.write_new(path, body, LIMITS["body_bytes"])
    return validate_delivery(out, manifest_sha256=sha(files["manifest.json"]), generation=decode(files["manifest.json"])["generation"])


def validate_latest(value):
    """Separate synthetic-only AVAILABLE specimen; no live admission is provided."""
    exact(value, "schema_version synthetic publication_allowed current_state_claim status reason record".split())
    require(value["schema_version"] == LATEST and value["synthetic"] is True and value["publication_allowed"] is False and value["current_state_claim"] is False, "Nonpublishable synthetic specimen only")
    require(value["status"] == "AVAILABLE" and value["reason"] == "synthetic_contract_example", "Synthetic available state")
    r = value["record"]
    exact(r, "representation identity native_value normalized_percent source_timestamp retrieved_at evaluated_at observation_age_seconds retrieval_age_seconds scale_state generation source_lineage_sha256 presentation_eligible latest_witness".split())
    identity(r["identity"])
    require(r["representation"] == "latest_instantaneous" and r["latest_witness"] is True and r["presentation_eligible"] is True, "Instantaneous witness required; never daily/terminal")
    require(r["identity"]["station_id"] == "0"*24 and r["identity"]["stream_id"] == "1"*24, "Test-only identity")
    factor = {"Percent":1, "VolumetricWaterContent":100}.get(r["identity"]["native_unit"])
    require(factor is not None and r["scale_state"] == "RESOLVED_PERCENT" and finite(r["native_value"]) and finite(r["normalized_percent"]) and 0 <= r["normalized_percent"] <= 100 and math.isclose(r["native_value"]*factor, r["normalized_percent"], abs_tol=1e-10), "Latest resolved scale")
    at, retrieved, now = (parse_utc(r[k]) for k in ("source_timestamp", "retrieved_at", "evaluated_at"))
    require(at <= retrieved <= now and r["observation_age_seconds"] == (now-at).total_seconds() and r["retrieval_age_seconds"] == (now-retrieved).total_seconds(), "Latest source/retrieval age")
    hash_value(r["generation"]); hash_value(r["source_lineage_sha256"])
    return True
