"""Compact real-Parquet fixtures; no dependency on a workstation candidate."""
from collections import Counter
import copy
import csv
from datetime import date
import importlib.util
import io
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from dendra.history_acquisition import candidate3_delivery as c
from dendra.history_acquisition.safety import Hold, decode, digest, encode, sha

ATTEMPTS = []


def deny_network(event, args):
    if event.startswith("socket."):
        ATTEMPTS.append(event)
        raise AssertionError("Offline delivery forbids sockets/DNS")


sys.addaudithook(deny_network)


def record(stream, day, unit="Percent", status="ACCEPTED", triplet=c.TRIPLETS[0], value=0):
    r = {k: 0 for k in c.INTEGER_FIELDS}
    r.update({k: None for k in c.NUMBER_FIELDS + c.NULL_STRING_FIELDS})
    r.update({k: False for k in c.BOOL_FIELDS})
    r.update({k: "fixture" for k in c.STRING_FIELDS})
    d = date.fromisoformat(day)
    wy = d.year + (d.month >= 10)
    dowy = (d - date(wy - 1, 10, 1)).days + 1
    aligned = (date(1999 if d.month >= 10 else 2000, d.month, d.day) - date(1999, 10, 1)).days + 1
    r.update(date=day, date_pst_fixed=day, water_year=wy, dowy=dowy, water_day=dowy,
             water_year_days=(date(wy, 10, 1) - date(wy - 1, 10, 1)).days,
             water_day_aligned=aligned, plot_day_aligned=aligned,
             stream_id=f"{stream:024x}", station_id=f"{1 if stream != 3 else 2:024x}",
             station_name="Fixture station", export_local_series_key=f"csv-sha256:{'f' * 64}:column:{stream}",
             native_unit=unit, depth_cm=10.0 if stream == 3 else None,
             depth_status="ACCEPTED_EXACT_STREAM_REVIEW" if stream == 3 else "UNKNOWN",
             conversion_multiplier=100.0 if unit == "VolumetricWaterContent" else 1.0,
             processing_version=triplet[0], source_quality_status=triplet[1], source_quality_reason=triplet[2],
             daily_status=status, flags=[], n_valid=144, n_total=144, valid_sample_count=144,
             expected_samples=144.0, expected_sample_count=144.0, cadence_seconds=600.0,
             coverage_fraction=1.0, sample_count_fraction=1.0, temporal_span_fraction=0.99,
             first_last_span_fraction=0.99, mean_native=value,
             mean_percent=None if unit == "Dimensionless" else value * (100 if unit == "VolumetricWaterContent" else 1),
             mean_value=value, daily_mean_vwc_percent=None,
             latest_source_timestamp_utc=day + "T20:00:00Z")
    if status == "ACCEPTED":
        r.update(daily_mean_vwc_percent=r["mean_percent"], plot_eligible=True)
    if status == "MISSING":
        r.update(mean_native=None, mean_percent=None, mean_value=None, n_valid=0, n_total=0, valid_sample_count=0)
    return r


def schema_fixture():
    props = {k: dict(type="integer") for k in c.INTEGER_FIELDS}
    props.update({k: dict(type=["number", "null"]) for k in c.NUMBER_FIELDS})
    props.update({k: dict(type="boolean") for k in c.BOOL_FIELDS})
    props.update({k: dict(type="string") for k in c.STRING_FIELDS})
    props.update({k: dict(type=["string", "null"]) for k in c.NULL_STRING_FIELDS})
    props.update(flags=dict(type="array", items=dict(type="string")), conversion_multiplier=dict(type="number"))
    props["date"]["pattern"] = r"^\d{4}-\d{2}-\d{2}$"
    props["daily_status"]["enum"] = list(c.STATES)
    row = dict(type="object", additionalProperties=False, required=list(props), properties=props)
    return dict(properties=dict(examples=dict(properties={k: dict(properties=dict(records=dict(items=row)))
                 for k in ("known_percent", "known_fraction", "unknown_depth")})))


class DeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="test-candidate3-")).resolve()
        cls.reader = c.ArrowReader()
        spec = importlib.util.spec_from_file_location("bulk_fixture", REPO / "scripts/dendra/bulk_csv_import.py")
        cls.bulk = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.bulk)
        cls.writer = cls.bulk.compile_helper(cls.scratch / "fixture-writer")
        cls.source, cls.expected = cls.fixture()
        cls.candidate = c.load_candidate(cls.source, cls.reader, expectations=cls.expected)
        cls.files = dict(c.render(cls.candidate, cls.reader, "2026-10-06T00:00:00Z"))

    @classmethod
    def tearDownClass(cls):
        if ATTEMPTS:
            raise AssertionError(ATTEMPTS)

    @classmethod
    def table(cls, root, name, rows, types):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(types))
            writer.writeheader()
            for row in rows:
                writer.writerow({k: (encode(v).decode().strip() if isinstance(v, list) else v) for k, v in row.items()})
        types_path = path.with_suffix(".types.csv")
        with types_path.open("w", newline="") as f:
            csv.writer(f).writerows(types.items())
        subprocess.run([str(cls.writer), "table", str(path), str(path.with_suffix(".parquet")), str(types_path)],
                       check=True, capture_output=True, timeout=30)

    @classmethod
    def fixture(cls, mutate=None):
        root = Path(tempfile.mkdtemp(prefix="source-", dir=cls.scratch))
        for directory in ("series", "science", "evidence"):
            (root / directory).mkdir()
        rows = [record(1, "2024-02-29"), record(1, "2024-03-01", triplet=c.TRIPLETS[1], value=12.3456789012345),
                record(1, "2024-03-02", triplet=c.TRIPLETS[2], value=25),
                record(1, "2024-03-03", status=c.STATES[1], value=9),
                record(1, "2024-03-04", status=c.STATES[2]),
                record(1, "2024-10-01", status=c.STATES[3], value=0.8),
                record(2, "2024-02-29"),
                record(3, "2024-02-29", unit="VolumetricWaterContent", triplet=c.TRIPLETS[1], value=0.2),
                record(4, "2024-02-29", unit="Dimensionless", status=c.STATES[3], value=0.3)]
        rows[3]["depth_status"] = "CONFLICTING"
        if mutate:
            mutate(rows)
        assets, catalog = [], []
        types = {k: "int64" for k in c.INTEGER_FIELDS}
        types.update({k: "double" for k in c.NUMBER_FIELDS + ["conversion_multiplier"]})
        types.update({k: "bool" for k in c.BOOL_FIELDS})
        types.update({k: "string" for k in c.STRING_FIELDS + c.NULL_STRING_FIELDS + ["flags"]})
        for stream in sorted({r["stream_id"] for r in rows}):
            part = [r for r in rows if r["stream_id"] == stream]
            row = part[0]
            name = f"series/{stream}.csv"
            cls.table(root, name, part, types)
            parquet = str(Path(name).with_suffix(".parquet"))
            desc = lambda n: c.file_descriptor(n, (root / n).read_bytes())
            native = dict(station_id=row["station_id"], proposed_stream_id=stream, accepted_stream_id="",
                          depth_cm=row["depth_cm"], conversion_multiplier=row["conversion_multiplier"],
                          export_local_series_key=row["export_local_series_key"], sha256="c" * 64)
            asset = dict(desc(parquet), csv=desc(name), stream_id=stream, source_native=native,
                         export_local_series_key=row["export_local_series_key"], rows=len(part),
                         states=dict(Counter(r["daily_status"] for r in part)), original_csv_sha256="f" * 64,
                         observation_api_q="PARTIALLY_KNOWN", provider_purpose="ReadytoUse",
                         historical_source_route="DENDRA_WEBSITE_HISTORICAL_BULK_EXPORT")
            for directory, suffix, key in (("science", ".json", "science_sha256"),
                                            ("evidence", ".input.json", "input_sha256"),
                                            ("evidence", ".proof.json", "quality_proof_sha256")):
                body = encode(dict(synthetic=True, stream_id=stream))
                (root / directory / (stream + suffix)).write_bytes(body)
                asset[key] = sha(body)
            assets.append(asset)
            catalog.append(dict(daily_asset=parquet, proposed_station_id=row["station_id"],
                                proposed_station=row["station_name"], proposed_stream_id=stream,
                                accepted_stream_id=None, export_local_series_key=row["export_local_series_key"],
                                native_unit=row["native_unit"], depth_cm=row["depth_cm"],
                                depth_status="CONFLICTING" if stream == f"{1:024x}" else row["depth_status"],
                                conversion_multiplier=str(row["conversion_multiplier"]),
                                provenance_register_keys="x" * 27000, duplicate_of=None,
                                materialized="True", eligibility="LOCAL_DAILY_CANDIDATE"))
        catalog.append(dict(catalog[0], export_local_series_key="explicit-fixture-alias",
                            duplicate_of=catalog[0]["export_local_series_key"], materialized="False",
                            eligibility="DUPLICATE_EXPORT_ALIAS"))
        cls.table(root, c.CATALOG + ".csv", catalog,
                  {k: "double" if k == "depth_cm" else "string" for k in catalog[0]})
        manifest = dict(candidate_version=c.CANDIDATE, publication_eligible=False, assets=assets,
                        as_of="2026-10-05T08:01:08Z", completed_end_exclusive="2026-10-05",
                        daily_policy="dendra-daily-1.0.0-frozen-cadence",
                        historical_bulk_policy="dendra-historical-bulk-readytouse-1",
                        native_catalog_pins={"fixture.json": "b" * 64}, source_sha256={"fixture.py": "a" * 64},
                        r2_regression_binding=dict(manifest_sha256="e" * 64), timestamp_evidence_sha256=sha(b"{}\n"))
        for key in ("bulk_metadata_binding", "coverage_summary", "impact_review_binding"):
            manifest[key] = dict(sha256="d" * 64)
        (root / c.MANIFEST).write_bytes(encode(manifest))
        (root / c.SCHEMA).write_bytes(encode(schema_fixture()))
        (root / "TIMESTAMP_EVIDENCE.json").write_bytes(b"{}\n")
        with zipfile.ZipFile(root / "L03_DAILY_R3_REVIEW.zip", "w") as z:
            for name in (c.MANIFEST, c.SCHEMA, c.CATALOG + ".csv"):
                z.writestr(name, (root / name).read_bytes())
        pins = {n: sha((root / n).read_bytes()) for n in (c.MANIFEST, c.SCHEMA, "L03_DAILY_R3_REVIEW.zip")}
        census = dict(stations=len({r["station_id"] for r in rows}), streams=len(assets), rows=len(rows),
                      states={s: sum(r["daily_status"] == s for r in rows) for s in c.STATES})
        accepted = Counter(tuple(r[k] for k in ("processing_version", "source_quality_status", "source_quality_reason"))
                           for r in rows if r["daily_status"] == "ACCEPTED")
        return root, c.Expectations(pins, sha((root / (c.CATALOG + ".parquet")).read_bytes()), census, dict(accepted))

    def directory(self, files=None):
        root = Path(tempfile.mkdtemp(prefix="delivery-", dir=self.scratch))
        for name, body in (files or self.files).items():
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body)
        return root

    def validate(self, root, files=None):
        return c.validate_delivery(self.source, root, reader=self.reader, expectations=self.expected,
                                   manifest_sha256=sha((files or self.files)["manifest.json"]))

    def test_export_reopen_and_all_state_conservation(self):
        output = self.scratch / (uuid.uuid4().hex + "-export")
        result = c.export(self.source, output, reader=self.reader, expectations=self.expected,
                          built_at="2026-10-06T00:00:00Z")
        self.assertEqual(result["census"], self.expected.census)
        self.assertEqual(result["wy_shards"], 5)
        self.assertEqual(result["output_validation"], "PASS")
        rows = [r for body in self.files.values() if decode(body)["kind"] == "history" for r in decode(body)["records"]]
        self.assertEqual(len(rows), 9)
        self.assertEqual(set(r["daily_status"] for r in rows), set(c.STATES))
        self.assertTrue(any(r["daily_mean_vwc_percent"] == 0 for r in rows))
        self.assertTrue(any(r["mean_native"] == 0.2 and r["daily_mean_vwc_percent"] == 20 for r in rows))
        self.assertTrue(any(r["native_unit"] == "Dimensionless" and r["mean_native"] == 0.3 and r["mean_percent"] is None for r in rows))
        self.assertEqual(sum(r["source_quality_reason"] == c.TRIPLETS[2][2] for r in rows), 1)
        self.assertTrue(any(r["daily_status"] == "MISSING" and r["mean_native"] is None for r in rows))
        self.assertEqual(len([r for r in rows if r["date"] == "2024-02-29" and r["depth_cm"] is None]), 3)
        descriptor = next(decode(v) for v in self.files.values() if decode(v)["kind"] == "stream" and
                          decode(v)["identity"]["stream_id"] == f"{1:024x}")
        self.assertEqual(descriptor["catalog_depth_status"], "CONFLICTING")
        self.assertEqual(descriptor["daily_depth_status_counts"], dict(CONFLICTING=1, UNKNOWN=5))

    def test_determinism_station_stream_wy_order(self):
        candidate = copy.deepcopy(self.candidate)
        candidate.manifest["assets"].reverse()
        self.assertEqual(dict(c.render(candidate, self.reader, "2026-10-06T00:00:00Z")), self.files)
        manifest = decode(self.files["manifest.json"])
        stations = decode(self.files[manifest["station_index"]["path"]])["stations"]
        self.assertEqual([s["station_id"] for s in stations], sorted(s["station_id"] for s in stations))
        self.assertEqual(manifest["census"]["streams"], 4)  # Eight twins, four streams.
        self.assertEqual(manifest["closure_sha256"], digest(manifest["files"]))

    def test_manifest_hash_failure_and_census_failure(self):
        bad = c.Expectations(dict(self.expected.pins, **{c.MANIFEST: "0" * 64}),
                             self.expected.catalog_parquet_sha256, self.expected.census, self.expected.accepted)
        with self.assertRaisesRegex(Hold, "hash mismatch"):
            c.load_candidate(self.source, self.reader, expectations=bad)
        candidate = copy.deepcopy(self.candidate)
        candidate.expectations.census["rows"] += 1
        with self.assertRaisesRegex(Hold, "census"):
            dict(c.render(candidate, self.reader, "2026-10-06T00:00:00Z"))

    def test_twins_disagree_even_when_declared_hashes_are_consistent(self):
        body = b"value\r\n0\r\n"
        reader = lambda _: dict(fields=[["value", "double"]], rows=[[1.0]])
        with self.assertRaisesRegex(Hold, "Twin disagreement"):
            c.resolve_twins(body, b"synthetic-reader", b"value,double\n", reader)
        reader = lambda _: dict(fields=[["value", "double"]], rows=[[None]])
        with self.assertRaisesRegex(Hold, "Twin disagreement"):
            c.resolve_twins(body, b"synthetic-reader", b"value,double\n", reader)

    def test_only_explicit_same_identity_catalog_aliases(self):
        primary = next(iter(self.candidate.catalog.values()))
        alias = dict(primary, export_local_series_key="alias", duplicate_of=primary["export_local_series_key"],
                     materialized="False", eligibility="DUPLICATE_EXPORT_ALIAS")
        catalog, aliases = c.resolve_catalog([alias, primary])
        self.assertEqual(len(catalog), 1)
        self.assertEqual(len(aliases), 1)
        for change in (dict(proposed_stream_id="f" * 24), dict(depth_cm=5), dict(native_unit="Percent-other"),
                       dict(duplicate_of="wrong-key"), dict(duplicate_of=None)):
            with self.subTest(change=change), self.assertRaises(Hold):
                c.resolve_catalog([primary, dict(alias, **change)])

    def test_illegal_provenance_identity_calendar_and_native_only(self):
        schema = self.candidate.schema
        original = record(1, "2024-02-29")
        identity = {k: original[k] for k in c.IDENTITY_FIELDS}
        mutations = [dict(processing_version="dendra-bulk-daily-3"),
                     dict(source_quality_status="PROVIDER_READY_TO_USE"),
                     dict(source_quality_reason="q_good"), dict(station_id="f" * 24),
                     dict(depth_cm=0), dict(water_year=2025), dict(dowy=1), dict(plot_day_aligned=1),
                     dict(conversion_multiplier=100), dict(daily_mean_vwc_percent=None),
                     dict(native_unit="Dimensionless", mean_percent=10), dict(flags=[0])]
        for change in mutations:
            with self.subTest(change=change), self.assertRaises((Hold, ValueError)):
                c.validate_record(dict(original, **change), schema, identity)
        native = record(4, "2024-02-29", unit="Dimensionless", status=c.STATES[3], value=0.3)
        native["mean_percent"] = 30.0
        with self.assertRaisesRegex(Hold, "Dimensionless"):
            c.validate_record(native, schema, {k: native[k] for k in c.IDENTITY_FIELDS})
        # Frozen null depth can also carry an explicit conflicting status;
        # preserving it must not relabel it UNKNOWN or infer a numeric depth.
        conflicting = dict(original, depth_status="CONFLICTING")
        c.validate_record(conflicting, schema, {k: conflicting[k] for k in c.IDENTITY_FIELDS})

    def test_duplicate_daily_key_and_unknown_triplet_are_not_auto_admitted(self):
        for mutate, message in ((lambda rows: rows.append(copy.deepcopy(rows[0])), "Duplicate"),
                                (lambda rows: rows[0].update(source_quality_reason="NEW_REASON"), "Unapproved")):
            source, expected = self.fixture(mutate)
            candidate = c.load_candidate(source, self.reader, expectations=expected)
            with self.assertRaisesRegex(Hold, message):
                dict(c.render(candidate, self.reader, "2026-10-06T00:00:00Z"))

    def test_hash_checked_again_at_consumption(self):
        candidate = copy.deepcopy(self.candidate)
        candidate.manifest["assets"][0]["path"] = "../escape.parquet"
        with self.assertRaises(Hold):
            c.stream_rows(candidate, candidate.manifest["assets"][0], self.reader)

    def test_output_value_row_state_provenance_and_reference_tampering(self):
        history = next(n for n in self.files if n.startswith("history/"))
        for mutation in (lambda v: v["records"][0].update(daily_mean_vwc_percent=None),
                         lambda v: v["records"].pop(),
                         lambda v: v["records"].append(copy.deepcopy(v["records"][0])),
                         lambda v: v["records"][0].update(daily_status="MISSING"),
                         lambda v: v["records"][0].update(depth_status="CHANGED"),
                         lambda v: v["records"][0].update(processing_version="changed"),
                         lambda v: v.update(water_year=1)):
            files = dict(self.files)
            value = decode(files[history])
            mutation(value)
            files[history] = encode(value)
            with self.assertRaisesRegex(Hold, "reconstruction"):
                self.validate(self.directory(files), files)
        files = dict(self.files)
        value = decode(files["manifest.json"])
        value["station_index"]["path"] = "../escape"
        files["manifest.json"] = encode(value)
        with self.assertRaisesRegex(Hold, "reconstruction"):
            self.validate(self.directory(files), files)

    def test_missing_station_and_duplicate_descriptor_reference(self):
        name = decode(self.files["manifest.json"])["station_index"]["path"]
        for mutation in (lambda v: v["stations"].pop(),
                         lambda v: v["stations"][0]["streams"].append(copy.deepcopy(v["stations"][0]["streams"][0]))):
            files = dict(self.files)
            value = decode(files[name])
            mutation(value)
            files[name] = encode(value)
            with self.assertRaisesRegex(Hold, "reconstruction"):
                self.validate(self.directory(files), files)

    def test_missing_extra_and_linked_output_closure(self):
        missing = dict(self.files)
        del missing[next(n for n in missing if n.startswith("streams/"))]
        with self.assertRaises(FileNotFoundError):
            self.validate(self.directory(missing))
        extra = dict(self.files, **{"unexpected.json": b"{}\n"})
        with self.assertRaisesRegex(Hold, "closure"):
            self.validate(self.directory(extra))
        root = self.directory()
        (root / "empty-extra").mkdir()
        with self.assertRaisesRegex(Hold, "closure"):
            self.validate(root)
        root = self.directory()
        (root / "linked.json").symlink_to(root / "manifest.json")
        with self.assertRaisesRegex(Hold, "Unsafe"):
            self.validate(root)

    def test_no_overwrite_source_overlap_public_root_and_symlink(self):
        root = self.directory()
        before = (root / "manifest.json").read_bytes()
        with self.assertRaisesRegex(Hold, "already exists"):
            c.export(self.source, root, reader=self.reader, expectations=self.expected)
        self.assertEqual(before, (root / "manifest.json").read_bytes())
        for destination in (self.source / "output", self.scratch / "docs/data/new", Path("relative-output")):
            with self.assertRaises(Hold):
                c.export(self.source, destination, reader=self.reader, expectations=self.expected)
        linked = self.scratch / uuid.uuid4().hex
        linked.symlink_to(self.scratch, target_is_directory=True)
        with self.assertRaises(OSError):
            c.export(self.source, linked / "output", reader=self.reader, expectations=self.expected)

    def test_no_network_or_publisher_calls(self):
        with patch.object(socket, "socket", side_effect=AssertionError("No socket")), \
             patch.object(socket, "create_connection", side_effect=AssertionError("No connection")):
            self.validate(self.directory())
        for module in (c,):
            source = Path(module.__file__).read_text()
            self.assertNotIn("import requests", source)
            self.assertNotIn("import urllib", source)
            self.assertNotIn("main_publisher", source)


if __name__ == "__main__":
    unittest.main()
