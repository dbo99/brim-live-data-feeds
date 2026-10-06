"""Frozen Candidate-3 all-state offline delivery; no acquisition or publication.

The CLI uses FROZEN exclusively. Explicit alternate expectations are for compact
local fixtures, not an admission policy for other production candidates.
"""
from collections import Counter, defaultdict
import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
import io
import math
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import tempfile
import zipfile

from .safety import Hold, Root, decode, digest, encode, require, sha

CONTRACT = "dendra-candidate3-delivery-1"
POLICY = "dendra-candidate3-delivery-adapter-1"
CANDIDATE = "dendra-00g-local-candidate-3"
MANIFEST = "DAILY_ASSET_MANIFEST.json"
CATALOG = "DAILY_SERIES_CATALOG"
SCHEMA = "00G_CANDIDATE_SCHEMA.json"
STATES = ("ACCEPTED", "WITHHELD_BY_EXISTING_SCREEN", "MISSING", "UNRESOLVED_SEMANTICS")
TRIPLETS = (
    ("dendra-bulk-daily-2", "RESOLVED_CLEAR", "EXACT_DAY_MATCH_NO_PROVIDER_QUALITY_VETO"),
    ("dendra-bulk-daily-3", "PROVIDER_READY_TO_USE", "API_Q_UNAVAILABLE_BULK_READYTOUSE"),
    ("dendra-bulk-daily-3", "PROVIDER_READY_TO_USE", "API_Q_PARTIALLY_KNOWN_BULK_READYTOUSE"),
)
MAX_FILE = 64 * 1024 * 1024
MAX_ROWS = 100000  # Per stream/table; no profile-1 station or WY limits.
MAX_FILES = 20000
HEX_ID = re.compile(r"[0-9a-f]{24}\Z")
HEX_SHA = re.compile(r"[0-9a-f]{64}\Z")
INTEGER_FIELDS = ("water_year dowy water_day water_year_days water_day_aligned n_valid n_total n_null "
                  "n_invalid n_missing n_duplicate_conflicts n_duplicate_rows n_out_of_range "
                  "observation_count nominal_range_observations plot_day_aligned valid_sample_count").split()
NUMBER_FIELDS = ("mean_native mean_percent mean_value daily_mean_vwc_percent expected_samples cadence_seconds "
                 "coverage_fraction temporal_span_fraction depth_cm expected_sample_count sample_count_fraction "
                 "first_last_span_fraction").split()
BOOL_FIELDS = "plot_eligible query_complete source_empty".split()
STRING_FIELDS = ("date cadence_source daily_status source_quality_status source_quality_reason stream_id "
                 "export_local_series_key station_id station_name depth_status native_unit processing_version "
                 "date_pst_fixed").split()
NULL_STRING_FIELDS = "accepted_stream_id latest_source_timestamp_utc".split()
ROW_FIELDS = set(INTEGER_FIELDS + NUMBER_FIELDS + BOOL_FIELDS + STRING_FIELDS + NULL_STRING_FIELDS +
                 ["flags", "conversion_multiplier"])
IDENTITY_FIELDS = ("station_id station_name stream_id accepted_stream_id export_local_series_key "
                   "depth_cm native_unit conversion_multiplier").split()


@dataclass(frozen=True)
class Expectations:
    pins: dict
    catalog_parquet_sha256: str
    census: dict
    accepted: dict


FROZEN = Expectations(
    pins={
        "DAILY_PRODUCT_RESULT.json": "facedaf17e0733b156f0afe7e1a9aa3091d27b9d013159a101f91b6fd575c620",
        MANIFEST: "23efbf1811f90ace46e7cb7ddec67b406d913659b3f560b43fdd9b492cee5296",
        "00G_CANDIDATE_FIXTURE.json": "fb5cff5a4c50ea34f314642ed12c541e55501f38403d6ef652b97afe5c41d47f",
        SCHEMA: "2e3a9ddcbc3c56cb4bd633fa2b3fcadb78e255bfef9b7fb907a6793610342c3d",
        "L03_DAILY_R3_REVIEW.zip": "cd7093fb8b0aa5905cd77e74487c68fc5955107f6e5052fd2e5ed1f9cd7b0343",
        "00G_MULTI_UNKNOWN_a51b6bda6c75eeb9bb9de2ba.json": "ad0c339bbb9dbb7475816ba117a1cc9e99e7540c51efbb9787bbcd387a74a2a5",
        "00G_MULTI_UNKNOWN_c571d4d4342517963831649b.json": "f2bce7d1263bce88d9f0d62cccc671171836f66bcbee63cc23574552b3ad95dd",
    },
    catalog_parquet_sha256="5abac5186acc57433e064ca444c45a5661aebdf8298171f6604f5c877b298d92",
    census=dict(stations=97, streams=310, rows=623241,
                states=dict(zip(STATES, (559122, 36145, 27873, 101)))),
    accepted=dict(zip(TRIPLETS, (138735, 420356, 31))),
)

# The existing bulk producer requires Arrow/Parquet C++ and clang++. This tiny
# read-only bridge uses that same local toolchain, accepts verified bytes on
# stdin, and emits doubles with round-trip precision. parquet-reader --dump is
# unsuitable because its human display rounds numbers. No source executable is
# trusted or invoked. This source is part of the adapter policy hash.
ARROW_READER_CPP = r'''
#include <arrow/api.h>
#include <arrow/io/api.h>
#include <parquet/arrow/reader.h>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <sstream>
#include <stdexcept>
#include <string>
template<class T> T take(arrow::Result<T> r) {
  if (!r.ok()) throw std::runtime_error(r.status().ToString());
  return std::move(r).ValueOrDie();
}
void quoted(std::ostream& out, const std::string& s) {
  if (s.size() > 65536) throw std::runtime_error("String bound");
  const char* hex = "0123456789abcdef";
  out << '"';
  for (unsigned char c : s) {
    if (c == '"' || c == '\\') out << '\\' << c;
    else if (c < 32) out << "\\u00" << hex[c >> 4] << hex[c & 15];
    else out << c;
  }
  out << '"';
}
int main() {
  try {
    std::string input; char buf[65536];
    while (std::cin) {
      std::cin.read(buf, sizeof(buf)); input.append(buf, std::cin.gcount());
      if (input.size() > 67108864) throw std::runtime_error("Input bound");
    }
    auto reader = take(parquet::arrow::OpenFile(
        std::make_shared<arrow::io::BufferReader>(arrow::Buffer::FromString(input)),
        arrow::default_memory_pool()));
    auto meta = reader->parquet_reader()->metadata();
    if (meta->num_rows() > 100000 || meta->num_columns() > 128)
      throw std::runtime_error("Table bound");
    int64_t decoded_bytes = 0;
    for (int i=0; i<meta->num_row_groups(); i++) {
      decoded_bytes += meta->RowGroup(i)->total_byte_size();
      if (decoded_bytes > 134217728)
        throw std::runtime_error("Row group bound");
    }
    std::shared_ptr<arrow::Table> raw_table;
    auto status = reader->ReadTable(&raw_table);
    if(!status.ok()) throw std::runtime_error(status.ToString());
    auto table = take(raw_table->CombineChunks());
    std::ostringstream out; out << std::setprecision(17) << "{\"fields\":[";
    for (int c=0; c<table->num_columns(); c++) {
      if(c) out << ','; out << '['; quoted(out, table->field(c)->name());
      out << ','; quoted(out, table->field(c)->type()->ToString()); out << ']';
    }
    out << "],\"rows\":[";
    for (int64_t r=0; r<table->num_rows(); r++) {
      if(r) out << ','; out << '[';
      for (int c=0; c<table->num_columns(); c++) {
        if(c) out << ',';
        auto value = take(table->column(c)->GetScalar(r));
        if(!value->is_valid) { out << "null"; continue; }
        switch(value->type->id()) {
          case arrow::Type::STRING: quoted(out, value->ToString()); break;
          case arrow::Type::BOOL: out << (std::static_pointer_cast<arrow::BooleanScalar>(value)->value ? "true" : "false"); break;
          case arrow::Type::INT64: out << std::static_pointer_cast<arrow::Int64Scalar>(value)->value; break;
          case arrow::Type::DOUBLE: {
            double v=std::static_pointer_cast<arrow::DoubleScalar>(value)->value;
            if(!std::isfinite(v)) throw std::runtime_error("Nonfinite number");
            out << v; break;
          }
          default: throw std::runtime_error("Unsupported Parquet type");
        }
      }
      out << ']';
      if(out.tellp() > 134217728) throw std::runtime_error("Decoded bound");
    }
    out << "]}"; std::cout << out.str();
  } catch(const std::exception& e) { std::cerr << e.what(); return 1; }
}
'''


class ArrowReader:
    """Local, finite subprocesses; retained task scratch, never candidate writes."""
    def __init__(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="dendra-c3-arrow-")).resolve()
        source = self.scratch / "reader.cpp"
        self.executable = self.scratch / "reader"
        source.write_text(ARROW_READER_CPP)
        flags = subprocess.run(["pkg-config", "--cflags", "--libs", "arrow", "parquet"],
                               check=True, capture_output=True, text=True, timeout=30).stdout
        result = subprocess.run(["clang++", "-O2", "-std=c++17", str(source), "-o", str(self.executable),
                                 *shlex.split(flags)], capture_output=True, timeout=120)
        require(result.returncode == 0, "Local Arrow compiler failure: " +
                result.stderr.decode(errors="replace")[:2000])

    def __call__(self, body):
        require(len(body) <= MAX_FILE, "Parquet input bound")
        result = subprocess.run([str(self.executable)], input=body, capture_output=True, timeout=60)
        require(result.returncode == 0, "Parquet read failure: " + result.stderr.decode(errors="replace")[-800:])
        require(len(result.stdout) <= 128 * 1024 * 1024, "Parquet decoded bound")
        return decode(result.stdout)


def file_descriptor(path, body):
    Root.parts(path)
    return dict(path=path, bytes=len(body), sha256=sha(body))


def csv_records(body):
    try:
        reader = csv.DictReader(io.StringIO(body.decode("utf-8"), newline=""), strict=True)
        require(reader.fieldnames and len(reader.fieldnames) == len(set(reader.fieldnames)), "CSV header closure")
        rows = []
        for row in reader:
            require(None not in row and None not in row.values(), "CSV ragged row")
            require(len(rows) < MAX_ROWS, "CSV row bound")
            rows.append(row)
        return reader.fieldnames, rows
    except (csv.Error, UnicodeError) as exc:
        raise Hold("Invalid CSV") from exc


def typed_csv(value, kind):
    if value == "":
        return None
    if kind == "string":
        return value
    if kind == "bool":
        require(value in ("True", "False"), "CSV boolean")
        return value == "True"
    if kind == "int64":
        require(re.fullmatch(r"-?[0-9]+", value), "CSV integer")
        return int(value)
    require(kind == "double", "CSV physical type")
    value = float(value)
    require(math.isfinite(value), "CSV finite number")
    return value


def resolve_twins(csv_body, parquet_body, types_body, reader):
    """Parquet canonical; CSV must equal every typed cell, not just metadata."""
    types = list(csv.reader(io.StringIO(types_body.decode("utf-8")), strict=True))
    require(types and all(len(t) == 2 for t in types), "Twin types closure")
    require(len({t[0] for t in types}) == len(types), "Duplicate twin field")
    fields, csv_rows = csv_records(csv_body)
    table = reader(parquet_body)
    require(table.keys() == {"fields", "rows"} and table["fields"] == types, "Twin Parquet schema disagreement")
    require(fields == [t[0] for t in types], "Twin CSV schema disagreement")
    require(len(csv_rows) == len(table["rows"]) <= MAX_ROWS, "Twin row-count disagreement")
    rows = []
    for ordinal, (csv_row, values) in enumerate(zip(csv_rows, table["rows"])):
        require(len(values) == len(types), "Parquet row closure")
        row = {}
        for (key, kind), value in zip(types, values):
            expected = typed_csv(csv_row[key], kind)
            if value is not None and kind == "double":
                require(type(value) in (int, float) and math.isfinite(value), "Parquet number")
                value = float(value)
            require(type(value) is type(expected) and value == expected,
                    f"Twin disagreement row={ordinal} field={key}")
            row[key] = value
        rows.append(row)
    return rows


def row_schema(schema):
    definitions = schema["properties"]["examples"]["properties"]
    records = [definitions[k]["properties"]["records"]["items"]
               for k in ("unknown_depth", "known_percent", "known_fraction")]
    require(all(r == records[0] for r in records), "Frozen row schemas disagree")
    result = records[0]
    require(result["additionalProperties"] is False and set(result["required"]) == ROW_FIELDS and
            set(result["properties"]) == ROW_FIELDS, "Frozen row closure changed")
    return result


def validate_record(row, schema, identity):
    require(set(row) == ROW_FIELDS, "Daily row field closure")
    for key, spec in schema["properties"].items():
        value = row[key]
        types = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        actual = ("null" if value is None else "boolean" if type(value) is bool else
                  "integer" if type(value) is int else "number" if type(value) is float else
                  "string" if type(value) is str else "array" if type(value) is list else "invalid")
        require(actual in types or (actual == "integer" and "number" in types), f"Daily field type {key}")
        if actual in ("integer", "number"):
            require(math.isfinite(value), f"Nonfinite daily field {key}")
        if "enum" in spec:
            require(value in spec["enum"], f"Daily field enum {key}")
        if "pattern" in spec:
            require(re.fullmatch(spec["pattern"], value), f"Daily field pattern {key}")
    require(all(type(f) is str for f in row["flags"]), "Daily flags")
    require(all(row[k] == identity[k] for k in IDENTITY_FIELDS), "Daily identity/depth/unit disagreement")
    d = date.fromisoformat(row["date"])
    wy = d.year + (d.month >= 10)
    require(row["water_year"] == wy, "Daily water-year mismatch")
    require(row["date_pst_fixed"] == row["date"] and row["dowy"] == row["water_day"] ==
            (d - date(wy - 1, 10, 1)).days + 1, "Daily fixed-PST/DOWY mismatch")
    require(row["water_year_days"] == (date(wy, 10, 1) - date(wy - 1, 10, 1)).days and
            row["plot_day_aligned"] == row["water_day_aligned"] ==
            (date(1999 if d.month >= 10 else 2000, d.month, d.day) - date(1999, 10, 1)).days + 1,
            "Daily leap-aligned coordinate mismatch")
    require(row["daily_status"] in STATES, "Unknown daily state")
    if row["daily_status"] == "ACCEPTED":
        triplet = tuple(row[k] for k in ("processing_version", "source_quality_status", "source_quality_reason"))
        require(triplet in TRIPLETS, "Unapproved accepted provenance triplet")
        require(row["daily_mean_vwc_percent"] is not None and row["plot_eligible"] and row["n_valid"] > 0,
                "Invalid frozen acceptance")
    else:
        require(row["daily_mean_vwc_percent"] is None, "Nonaccepted row has accepted value")
    if row["native_unit"] == "Dimensionless":
        require(row["mean_percent"] is None and row["daily_mean_vwc_percent"] is None,
                "Fabricated Dimensionless percent")
    elif row["native_unit"] in ("Percent", "VolumetricWaterContent"):
        require(row["conversion_multiplier"] == (1 if row["native_unit"] == "Percent" else 100),
                "Frozen unit multiplier disagreement")
    else:
        raise Hold("Unsupported Candidate-3 native unit")
    require(row["depth_status"] in ("UNKNOWN", "CONFLICTING", "ACCEPTED_EXACT_STREAM_REVIEW"),
            "Unknown depth status")
    require((row["depth_status"] in ("UNKNOWN", "CONFLICTING")) == (row["depth_cm"] is None),
            "Depth status/value disagreement")


@dataclass
class Candidate:
    root: Path
    manifest: dict
    schema: dict
    catalog: dict
    source_binding: dict
    expectations: Expectations


def resolve_catalog(rows):
    """Honor explicit producer aliases, never deduplicate by labels or depth."""
    require(len({r["export_local_series_key"] for r in rows}) == len(rows), "Duplicate catalog source key")
    catalog, aliases = {}, []
    for row in rows:
        if row["daily_asset"] and not row["duplicate_of"]:
            require(row["materialized"] == "True" and row["eligibility"] == "LOCAL_DAILY_CANDIDATE",
                    "Unmaterialized catalog daily asset")
            require(row["daily_asset"] not in catalog, "Duplicate catalog daily asset")
            catalog[row["daily_asset"]] = row
    identity = ("proposed_station_id", "proposed_station", "proposed_stream_id", "accepted_stream_id",
                "depth_cm", "depth_status", "native_unit", "conversion_multiplier")
    for row in rows:
        if row["daily_asset"] and row["duplicate_of"]:
            primary = catalog.get(row["daily_asset"])
            require(primary is not None and row["duplicate_of"] == primary["export_local_series_key"] and
                    row["materialized"] == "False" and row["eligibility"] == "DUPLICATE_EXPORT_ALIAS" and
                    all(row[k] == primary[k] for k in identity), "Invalid explicit catalog alias")
            aliases.append(dict(alias_export_local_series_key=row["export_local_series_key"],
                                canonical_export_local_series_key=row["duplicate_of"],
                                daily_asset=row["daily_asset"], station_id=row["proposed_station_id"],
                                stream_id=row["proposed_stream_id"]))
    return catalog, sorted(aliases, key=lambda a: a["alias_export_local_series_key"])


def load_candidate(source_root, reader, *, expectations=FROZEN):
    """Verify all frozen pins, directory closure, provenance and catalog twins."""
    source_root = Path(source_root)
    with Root(source_root) as source:
        files = {}

        def read(name, expected=None, size=None):
            body = source.read(name, MAX_FILE)
            desc = file_descriptor(name, body)
            require(expected is None or desc["sha256"] == expected, f"Source hash mismatch: {name}")
            require(size is None or desc["bytes"] == size, f"Source size mismatch: {name}")
            files[name] = desc
            return body

        for name, pin in expectations.pins.items():
            read(name, pin)
        require(MANIFEST in expectations.pins and SCHEMA in expectations.pins, "Required freeze pins")
        manifest = decode(read(MANIFEST, expectations.pins[MANIFEST]))
        require(manifest["candidate_version"] == CANDIDATE and manifest["publication_eligible"] is False,
                "Frozen candidate identity/publication mismatch")
        archive = "L03_DAILY_R3_REVIEW.zip"
        with zipfile.ZipFile(io.BytesIO(read(archive, expectations.pins[archive]))) as z:
            names = z.namelist()
            require(len(names) == len(set(names)) <= 32, "Review ZIP closure")
            require({MANIFEST, SCHEMA, CATALOG + ".csv"} <= set(names), "Review ZIP missing required binding")
            for name in names:
                require(len(Root.parts(name)) == 1 and z.getinfo(name).file_size < 2 * 1024 * 1024,
                        "Review ZIP member bound/path")
                read(name, sha(z.read(name)))
        schema = row_schema(decode(read(SCHEMA, expectations.pins[SCHEMA])))
        catalog_rows = resolve_twins(read(CATALOG + ".csv", files[CATALOG + ".csv"]["sha256"]),
                                    read(CATALOG + ".parquet", expectations.catalog_parquet_sha256),
                                    read(CATALOG + ".types.csv"), reader)
        catalog, aliases = resolve_catalog(catalog_rows)
        assets = manifest["assets"]
        require(0 < len(assets) <= 2000 and len({a["stream_id"] for a in assets}) == len(assets),
                "Duplicate exact stream or stream bound")
        require(set(catalog) == {a["path"] for a in assets} and len(catalog) == len(assets),
                "Manifest/catalog asset closure")
        directories = dict(series=set(), science=set(), evidence=set())
        for asset in assets:
            name = asset["path"]
            stem = Path(name).stem
            require(HEX_ID.fullmatch(stem) and name == f"series/{stem}.parquet" and
                    asset["csv"]["path"] == f"series/{stem}.csv", "Twin path identity")
            require(HEX_ID.fullmatch(asset["stream_id"]) and
                    HEX_ID.fullmatch(asset["source_native"]["station_id"]), "Exact source IDs")
            for desc in (asset, asset["csv"]):
                read(desc["path"], desc["sha256"], desc["bytes"])
                directories["series"].add(desc["path"])
            types = f"series/{stem}.types.csv"
            read(types)
            directories["series"].add(types)
            for directory, suffix, key in (("science", ".json", "science_sha256"),
                                            ("evidence", ".input.json", "input_sha256"),
                                            ("evidence", ".proof.json", "quality_proof_sha256")):
                path = f"{directory}/{stem}{suffix}"
                decode(read(path, asset[key]))
                directories[directory].add(path)
        for directory, expected in directories.items():
            require({directory + "/" + n for n in source.list(directory, MAX_FILES)} == expected,
                    f"Frozen {directory} closure differs")
        read("TIMESTAMP_EVIDENCE.json", manifest["timestamp_evidence_sha256"])
        # These are identity references, not permission to follow workstation
        # paths or reopen historical journals/native observation libraries.
        upstream = {key: manifest[key]["sha256"] for key in
                    ("bulk_metadata_binding", "coverage_summary", "impact_review_binding")}
        upstream["r2_manifest"] = manifest["r2_regression_binding"]["manifest_sha256"]
        upstream["native_catalog_pins"] = manifest["native_catalog_pins"]
        upstream["producer_sources"] = manifest["source_sha256"]
        binding = dict(candidate_id=CANDIDATE, adapter_policy=POLICY,
                       canonical_representation="parquet", twin_comparison="all_typed_cells_exact",
                       candidate_manifest=files[MANIFEST],
                       catalog=[files[CATALOG + ext] for ext in (".csv", ".parquet", ".types.csv")],
                       daily_schema=files[SCHEMA], row_schema=schema,
                       provenance_manifest=files[MANIFEST], upstream_identities=upstream, catalog_aliases=aliases,
                       producer_as_of=manifest["as_of"], completed_end_exclusive=manifest["completed_end_exclusive"],
                       daily_policy=manifest["daily_policy"], historical_bulk_policy=manifest["historical_bulk_policy"],
                       timestamp_semantics="fixed UTC-08 completed daily; ending-year WY; true DOWY; separate leap alignment",
                       files=[files[k] for k in sorted(files)])
        return Candidate(source_root, manifest, schema, catalog, binding, expectations)


def stream_rows(candidate, asset, reader):
    """Rehash consumed bytes; canonical Parquet and full CSV comparison."""
    pins = {d["path"]: d for d in candidate.source_binding["files"]}
    with Root(candidate.root) as source:
        def read(name):
            body = source.read(name, MAX_FILE)
            require(file_descriptor(name, body) == pins[name], f"Source changed: {name}")
            return body
        rows = resolve_twins(read(asset["csv"]["path"]), read(asset["path"]),
                             read(str(Path(asset["path"]).with_suffix(".types.csv"))), reader)
    require(rows and len(rows) == asset["rows"], "Source stream row census")
    catalog = candidate.catalog[asset["path"]]
    native = asset["source_native"]
    identity = dict(station_id=catalog["proposed_station_id"], station_name=catalog["proposed_station"],
                    stream_id=catalog["proposed_stream_id"], accepted_stream_id=catalog["accepted_stream_id"],
                    export_local_series_key=catalog["export_local_series_key"], depth_cm=catalog["depth_cm"],
                    native_unit=catalog["native_unit"],
                    conversion_multiplier=float(catalog["conversion_multiplier"]))
    require(identity["stream_id"] == asset["stream_id"] == native["proposed_stream_id"] and
            identity["station_id"] == native["station_id"] and
            identity["export_local_series_key"] == asset["export_local_series_key"] == native["export_local_series_key"] and
            identity["depth_cm"] == native["depth_cm"] and
            identity["conversion_multiplier"] == native["conversion_multiplier"] and
            identity["accepted_stream_id"] == (native["accepted_stream_id"] or None),
            "Manifest/catalog/native identity disagreement")
    seen = set()
    for row in rows:
        try:
            row["flags"] = decode(row["flags"])
            validate_record(row, candidate.schema, identity)
        except (Hold, ValueError, TypeError) as exc:
            raise Hold(f"Stream {asset['stream_id']} date {str(row.get('date'))[:10]}: {exc}") from exc
        require(row["date"] < candidate.manifest["completed_end_exclusive"], "Uncompleted source day")
        require(row["date"] not in seen, "Duplicate source stream/date")
        seen.add(row["date"])
    require(dict(Counter(r["daily_status"] for r in rows)) == asset["states"], "Source per-stream state census")
    return identity, sorted(rows, key=lambda r: r["date"])


def provenance_inventory(counter):
    return [dict(processing_version=t[0], source_quality_status=t[1], source_quality_reason=t[2], rows=n)
            for t, n in sorted(counter.items())]


def render(candidate, reader, built_at):
    """Stream deterministic components; retain at most one stream's daily rows."""
    require(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", built_at), "Export UTC timestamp")
    datetime.strptime(built_at, "%Y-%m-%dT%H:%M:%SZ")
    files, stations = [], defaultdict(list)
    states, versions, accepted = Counter(), Counter(), Counter()
    shard_count = row_count = 0

    def component(prefix, value):
        body = encode(dict(schema_version=CONTRACT, **value))
        require(len(body) <= MAX_FILE, "Output component bound")
        path = f"{prefix}.{sha(body)}.json"
        desc = file_descriptor(path, body)
        files.append(desc)
        return desc, (path, body)

    source_desc, source_file = component("source-binding", dict(kind="source_binding", **candidate.source_binding))
    yield source_file
    for asset in sorted(candidate.manifest["assets"], key=lambda a: (a["source_native"]["station_id"], a["stream_id"])):
        identity, rows = stream_rows(candidate, asset, reader)
        station, stream = identity["station_id"], identity["stream_id"]
        by_year = defaultdict(list)
        for row in rows:
            by_year[row["water_year"]].append(row)
            states[row["daily_status"]] += 1
            versions[row["processing_version"]] += 1
            if row["daily_status"] == "ACCEPTED":
                accepted[tuple(row[k] for k in ("processing_version", "source_quality_status", "source_quality_reason"))] += 1
        histories = []
        for year, records in sorted(by_year.items()):
            desc, item = component(f"history/{station}/{stream}/wy-{year}",
                                   dict(kind="history", station_id=station, stream_id=stream,
                                        water_year=year, records=records))
            histories.append(dict(water_year=year, rows=len(records),
                                  states=dict(Counter(r["daily_status"] for r in records)), file=desc))
            shard_count += 1
            yield item
        source = dict(parquet={k: asset[k] for k in ("path", "bytes", "sha256")}, csv=asset["csv"],
                      science_sha256=asset["science_sha256"], input_sha256=asset["input_sha256"],
                      quality_proof_sha256=asset["quality_proof_sha256"],
                      native_asset_sha256=asset["source_native"]["sha256"],
                      original_csv_sha256=asset["original_csv_sha256"],
                      observation_api_q=asset["observation_api_q"], provider_purpose=asset["provider_purpose"],
                      historical_source_route=asset["historical_source_route"])
        desc, item = component(f"streams/{station}/{stream}",
                               dict(kind="stream", identity=identity, source=source, rows=len(rows),
                                    catalog_depth_status=candidate.catalog[asset["path"]]["depth_status"],
                                    daily_depth_status_counts=dict(Counter(r["depth_status"] for r in rows)),
                                    states=dict(Counter(r["daily_status"] for r in rows)), history=histories))
        stations[station].append(dict(stream_id=stream, descriptor=desc))
        row_count += len(rows)
        yield item
    census = dict(stations=len(stations), streams=len(candidate.manifest["assets"]), rows=row_count,
                  states={s: states[s] for s in STATES})
    require(census == candidate.expectations.census, "Frozen candidate census disagreement")
    require(dict(accepted) == candidate.expectations.accepted, "Accepted provenance census disagreement")
    index_desc, item = component("stations", dict(kind="station_index", stations=[
        dict(station_id=k, streams=stations[k]) for k in sorted(stations)]))
    yield item
    files.sort(key=lambda f: f["path"])
    closure = digest(files)
    adapter = dict(policy=POLICY, source_sha256=sha(Path(__file__).read_bytes()),
                   arrow_reader_sha256=sha(ARROW_READER_CPP.encode()))
    generation = digest(dict(schema_version=CONTRACT, source_binding=source_desc, census=census,
                             adapter=adapter, closure_sha256=closure))
    manifest = dict(schema_version=CONTRACT, kind="generation", candidate_id=CANDIDATE,
                    publication_state="offline_export_only", publication_eligible=False,
                    generation_sha256=generation, export_built_at=built_at, adapter=adapter,
                    candidate_manifest_sha256=candidate.expectations.pins[MANIFEST],
                    source_binding=source_desc, station_index=index_desc, census=census,
                    wy_shards=shard_count, processing_versions=dict(sorted(versions.items())),
                    accepted_provenance=provenance_inventory(accepted), files=files,
                    output_file_count=len(files) + 1, closure_sha256=closure)
    yield "manifest.json", encode(manifest)


def output_inventory(root):
    """Descriptor-relative traversal also rejects empty extra directories/links."""
    files, directories = set(), set()
    def walk(fd, prefix=""):
        for name in sorted(os.listdir(fd)):
            path = prefix + name
            Root.parts(path)
            require(len(files) + len(directories) < MAX_FILES, "Output closure bound")
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                directories.add(path)
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    walk(child, path + "/")
                finally:
                    os.close(child)
            else:
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Unsafe output link/file")
                files.add(path)
    walk(root.fd)
    return files, directories


def validate_delivery(source_root, output_root, *, manifest_sha256, reader, expectations=FROZEN):
    """Reopen everything and compare every output byte with source reconstruction.

    This is stronger than trusting a newly edited output checksum list: it proves
    rows, references, per-WY partitions, inventories and values from frozen source.
    No second export is written.
    """
    require(HEX_SHA.fullmatch(manifest_sha256), "Explicit delivery manifest SHA required")
    candidate = load_candidate(source_root, reader, expectations=expectations)
    with Root(output_root) as output:
        manifest_body = output.read("manifest.json", MAX_FILE)
        require(sha(manifest_body) == manifest_sha256, "Output manifest hash mismatch")
        manifest = decode(manifest_body)
        seen, directories, total_bytes = set(), set(), 0
        for name, expected in render(candidate, reader, manifest["export_built_at"]):
            actual = output.read(name, MAX_FILE)
            require(actual == expected, f"Output/source reconstruction mismatch: {name}")
            require(name not in seen, "Duplicate output component")
            seen.add(name)
            total_bytes += len(actual)
            directories.update(str(p) for p in Path(name).parents if str(p) != ".")
        require(output_inventory(output) == (seen, directories), "Output file/directory closure mismatch")
    return dict(manifest_sha256=manifest_sha256, generation_sha256=manifest["generation_sha256"],
                closure_sha256=manifest["closure_sha256"], source_binding_sha256=manifest["source_binding"]["sha256"],
                census=manifest["census"], wy_shards=manifest["wy_shards"],
                processing_versions=manifest["processing_versions"], accepted_provenance=manifest["accepted_provenance"],
                files=len(seen), bytes=total_bytes, row_parity="PASS", state_parity="PASS",
                accepted_provenance_parity="PASS", output_validation="PASS", publication_eligible=False)


def export(source_root, output_root, *, reader=None, built_at=None, expectations=FROZEN):
    source_root, output_root = Path(source_root), Path(output_root)
    require(source_root.is_absolute() and output_root.is_absolute() and ".." not in output_root.parts,
            "Explicit absolute source/output roots required")
    require(source_root != output_root and source_root not in output_root.parents and output_root not in source_root.parents,
            "Source and output roots must be disjoint")
    require(not any(output_root.parts[i:i + 2] == ("docs", "data") for i in range(len(output_root.parts) - 1)),
            "Public docs/data output forbidden")
    # Validate ancestors before creating anything. Destination must be absent,
    # a stronger rule than refusing an existing nonempty directory.
    with Root(output_root.parent) as parent:
        require(output_root.name not in parent.list(limit=MAX_FILES), "Output destination already exists")
        reader = reader or ArrowReader()
        candidate = load_candidate(source_root, reader, expectations=expectations)
        os.mkdir(output_root.name, 0o700, dir_fd=parent.fd)
    built_at = built_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with Root(output_root) as output:
        for name, body in render(candidate, reader, built_at):
            output.write_new(name, body, MAX_FILE)
            if name == "manifest.json":
                manifest_sha256 = sha(body)
    return validate_delivery(source_root, output_root, manifest_sha256=manifest_sha256,
                             reader=reader, expectations=expectations)
