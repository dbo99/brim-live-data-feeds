"""Small offline contract tests; no network, provider package or browser required."""
import copy
import csv
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/soil_moisture"))
import scan_history as h


def source(date, value, site=2116, depth=2, ids="SMS.I_2"):
    year, water_day = h.wy(dt.date.fromisoformat(date))
    return {"station_uid": f"NRCS_scan_{site}", "site_code": str(site), "depth_in": str(depth),
            "date": date, "obs_date": date, "water_year": str(year), "water_day": str(water_day),
            "sms_pct": str(value), "sensor_count": str(len(ids.split(";"))),
            "sensor_ids": ids, "sensor_id": ids.replace(";", ", ")}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "delivery"
        self.scope = {"stations": 1, "histories": 1, "archive_only": [[2116, 40]]}
        self.roster = {(2116, 2): {"site_code": 2116, "station_uid": "NRCS_scan_2116",
                                  "station_name": "Saved station", "depth_in": 2}}
        self.authority = {"current_water_year": 2026, "current_wy_start_date": "2025-10-01",
                          "as_of_date": "2025-10-03", "source_feed_build_time_utc": "2025-10-03T18:00:00Z"}
        self.archive = [source("2024-02-28", 10), source("2024-02-29", 0),
                        source("2024-03-01", 1.55, ids="SMS.I-2_2;SMS.I_2"),
                        source("2025-09-30", 109.4), source("2025-10-01", 99),
                        source("2025-10-02", 88),
                        source("2020-01-01", 23, depth=40, ids="SMS.I_40")]
        self.current = {(2116, 2): [h.normalize(source("2025-10-01", 0), False)[1],
                                   h.normalize(source("2025-10-03", 3.25), False)[1]]}
        self.binding = {"schema": h.SCHEMA + "-inputs", "scope": self.scope,
                        "files": {name: {"bytes": 1, "sha256": "0" * 64}
                                  for name in (h.ARCHIVE, *h.CURRENT_FILES)}}

    def delivery(self):
        self.histories, self.inventory = h.combine(self.roster, self.current, self.archive,
                                                  self.authority, self.scope)
        self.report = h.write_delivery(self.output, self.binding, h.sha(h.encoded(self.binding)),
                                      self.roster, self.histories, self.inventory, self.authority)
        self.manifest = json.loads((self.output / "manifest.json").read_text())
        return self.report

    def object(self, role):
        return json.loads((self.output / self.manifest[role]["path"]).read_text())

    def rewrite_manifest(self):
        raw = h.encoded(self.manifest)
        (self.output / "manifest.json").write_bytes(raw)
        return h.sha(raw)

    def test_round_trip_values_composites_and_archive_only_inventory(self):
        report = self.delivery()
        self.assertEqual(report["history_rows"], 6)
        rows = self.histories[(2116, 2)]
        self.assertEqual(rows[1], ["2024-02-29", 0.0, 2024, 152, 1, ["SMS.I_2"]])
        self.assertEqual(rows[2], ["2024-03-01", 1.55, 2024, 153, 2, ["SMS.I-2_2", "SMS.I_2"]])
        self.assertEqual(rows[-2], ["2025-10-01", 0.0, 2026, 1, 1, ["SMS.I_2"]])
        self.assertEqual(report["outside_0_100"], 1)
        self.assertEqual(report["composite_rows"], 1)
        self.assertEqual(len(self.object("index")["histories"]), 1)
        inv = self.object("archive_inventory")
        self.assertEqual(inv["histories"][1]["browser_advertised"], False)
        self.assertEqual(inv["ignored_archive_current_wy_rows"], 2)
        self.assertEqual(len(list((self.output / "history").glob("*.json"))), 1)
        self.assertEqual(h.validate(self.output, report["manifest_sha256"]), report)

    def test_gap_zero_and_current_wy_authority(self):
        self.delivery()
        rows = self.histories[(2116, 2)]
        self.assertNotIn("2025-10-02", [r[0] for r in rows])  # archive has 88; must stay missing
        hover = self.object("hover")
        self.assertEqual(hover["histories"][0]["values"][-4:], [109.4, 0, None, 3.25])
        self.assertEqual(hover["days"], 30)
        self.assertEqual(hover["first_date"], "2025-09-04")
        self.assertEqual(self.report["zero_values"], 2)

    def test_leap_calendar_and_water_year_rollover(self):
        self.assertEqual(h.wy(dt.date(2024, 2, 29)), (2024, 152))
        self.assertEqual(h.wy(dt.date(2024, 9, 30)), (2024, 366))
        self.assertEqual(h.wy(dt.date(2024, 10, 1)), (2025, 1))
        authority = dict(self.authority, current_water_year=2027,
                         current_wy_start_date="2026-10-01", as_of_date="2026-10-02")
        archive = [source("2026-09-30", 4), source("2026-10-01", 9),
                   source("2026-10-02", 8), source("2020-01-01", 23, depth=40, ids="SMS.I_40")]
        current = {(2116, 2): [h.normalize(source("2026-10-02", 0), False)[1]]}
        histories, _ = h.combine(self.roster, current, archive, authority, self.scope)
        self.assertEqual([(r[0], r[1]) for r in histories[(2116, 2)]], [("2026-09-30", 4), ("2026-10-02", 0)])

    def test_hash_corruption_and_external_manifest_binding(self):
        report = self.delivery()
        with self.assertRaisesRegex(ValueError, "manifest checksum"):
            h.validate(self.output, "0" * 64)
        p = self.output / self.manifest["index"]["path"]
        p.write_bytes(p.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "checksum/size"):
            h.validate(self.output, report["manifest_sha256"])

    def test_path_traversal_and_file_bounds(self):
        self.delivery()
        original = copy.deepcopy(self.manifest)
        self.manifest["files"][0]["path"] = "../outside.json"
        with self.assertRaisesRegex(ValueError, "unsafe"):
            h.validate(self.output, self.rewrite_manifest())
        self.manifest = original
        self.manifest["files"][0]["bytes"] = h.MAX_FILE + 1
        with self.assertRaisesRegex(ValueError, "descriptor bound"):
            h.validate(self.output, self.rewrite_manifest())

    def test_rehashed_hover_corruption_fails_semantic_validation(self):
        self.delivery()
        original = self.object("hover")
        for replacement, message in ((0, "gap/value"), (False, "type/bound")):
            changed = copy.deepcopy(original)
            changed["histories"][0]["values"][-2] = replacement
            previous = self.manifest["hover"]
            descriptor = h.write_object(self.output, "hover-30d", changed)
            self.manifest["files"] = [descriptor if f == previous else f for f in self.manifest["files"]]
            self.manifest["hover"] = descriptor
            self.manifest["payload_bytes"] += descriptor["bytes"] - previous["bytes"]
            (self.output / previous["path"]).unlink()  # owns this temporary test object
            with self.subTest(replacement=replacement), self.assertRaisesRegex(ValueError, message):
                h.validate(self.output, self.rewrite_manifest())

    def test_symlink_and_unadvertised_file_rejected(self):
        self.delivery()
        extra = self.output / "extra.json"
        extra.write_text("{}")
        with self.assertRaisesRegex(ValueError, "allowlist"):
            h.validate(self.output, self.report["manifest_sha256"])
        extra.unlink()  # owns this temporary test file only
        descriptor = self.manifest["index"]
        original = self.output / descriptor["path"]
        target = self.root / "target.json"
        original.rename(target)
        original.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            h.validate(self.output, self.report["manifest_sha256"])

    def test_fresh_output_preserves_previous_delivery(self):
        self.delivery()
        before = {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob("*") if p.is_file()}
        with self.assertRaises(FileExistsError):
            h.write_delivery(self.output, self.binding, h.sha(h.encoded(self.binding)), self.roster,
                             self.histories, self.inventory, self.authority)
        self.assertEqual(before, {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob("*") if p.is_file()})
        with patch.object(h.subprocess, "run", side_effect=AssertionError("must not run R")):
            with self.assertRaisesRegex(ValueError, "fresh"):
                h.export("missing", "missing", "missing", "0" * 64, self.output)

    def test_duplicate_nonfinite_identity_and_water_day_fail(self):
        cases = [dict(self.archive[0], sms_pct="NaN"), dict(self.archive[0], water_day="999"),
                 dict(self.archive[0], station_uid="NRCS_scan_9999"),
                 dict(self.archive[0], sensor_count="2"),
                 dict(self.archive[0], sensor_ids="SMS.I_4")]
        for row in cases:
            with self.subTest(row=row), self.assertRaises(ValueError):
                h.normalize(row, True)
        with self.assertRaisesRegex(ValueError, "duplicate archive"):
            h.combine(self.roster, self.current, self.archive + [self.archive[0]], self.authority, self.scope)
        with self.assertRaisesRegex(ValueError, "duplicate JSON"):
            h.decoded(b'{"a":1,"a":2}')

    def test_archive_membership_and_future_dates_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "archive-only"):
            h.combine(self.roster, self.current, self.archive[:-1], self.authority, self.scope)
        with self.assertRaisesRegex(ValueError, "beyond"):
            h.combine(self.roster, self.current, self.archive + [source("2026-01-01", 1)], self.authority, self.scope)

    def test_deterministic_output(self):
        report = self.delivery()
        other = self.root / "other"
        result = h.write_delivery(other, self.binding, h.sha(h.encoded(self.binding)), self.roster,
                                  self.histories, self.inventory, self.authority)
        self.assertEqual(result, report)
        self.assertEqual({p.relative_to(other): p.read_bytes() for p in other.rglob("*") if p.is_file()},
                         {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob("*") if p.is_file()})

    def test_bound_inputs_and_rds_export_end_to_end(self):
        # Tiny synthetic RDS verifies base-R numeric precision and the full CLI path.
        archive = self.root / h.ARCHIVE
        subprocess.run(["Rscript", "--vanilla", "-e", """
args <- commandArgs(TRUE)
x <- data.frame(station_uid=c('NRCS_scan_2116','NRCS_scan_2116'), site_code=c(2116L,2116L),
 date=as.Date(c('2024-02-29','2020-01-01')),water_year=c(2024,2020),water_day=c(152L,93L),
 depth_in=c(2,40),sms_pct=c(1.2345678901234567,23),sensor_count=c(2L,1L),
 sensor_ids=c('SMS.I-2_2;SMS.I_2','SMS.I_40'))
saveRDS(x,args[[1]])
""", str(archive)], check=True, capture_output=True, timeout=20)
        current_dir = self.root / "current"
        current_dir.mkdir()
        for name in h.CURRENT_FILES:
            (current_dir / name).write_text("preserved reference\n")
        p = dict(self.roster[(2116, 2)], current_water_year=2026, current_wy_start_date="2025-10-01",
                 feed_build_time_utc=self.authority["source_feed_build_time_utc"],
                 depth_values_json=json.dumps([{"depth_in": 2}]))
        (current_dir / h.CURRENT_FILES[0]).write_bytes(h.encoded({"type": "FeatureCollection", "features": [{"properties": p}]}))
        summary = dict(self.authority, feed_build_time_utc=self.authority["source_feed_build_time_utc"],
                       first_date="2025-10-01", last_date="2025-10-03", trace_rows=2, stations=1,
                       features_written=1, depth_rows_latest=1, current_wy_trace_rows=2)
        for name in (h.CURRENT_FILES[1], h.CURRENT_FILES[3]):
            (current_dir / name).write_bytes(h.encoded(summary))
        rows = [source("2025-10-01", 0), source("2025-10-03", 3.25)]
        with (current_dir / h.CURRENT_FILES[2]).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        for name in self.binding["files"]:
            raw = (archive if name == h.ARCHIVE else current_dir / name).read_bytes()
            self.binding["files"][name] = {"bytes": len(raw), "sha256": h.sha(raw)}
        binding_path = self.root / "input-binding.json"
        binding_path.write_bytes(h.encoded(self.binding))
        digest = h.sha(binding_path.read_bytes())
        result = h.export(archive, current_dir, binding_path, digest, self.output)
        manifest = json.loads((self.output / "manifest.json").read_text())
        index = json.loads((self.output / manifest["index"]["path"]).read_text())
        shard = json.loads((self.output / index["histories"][0]["file"]["path"]).read_text())
        self.assertEqual(shard["rows"][0][1], 1.2345678901234567)
        self.assertEqual(result["history_rows"], 3)
        (current_dir / h.CURRENT_FILES[-1]).write_text("changed reference\n")
        with patch.object(h.subprocess, "run", side_effect=AssertionError("must not run R")):
            with self.assertRaisesRegex(ValueError, "input checksum"):
                h.export(archive, current_dir, binding_path, digest, self.root / "bad-output")
        self.assertFalse((self.root / "bad-output").exists())


if __name__ == "__main__":
    unittest.main()
