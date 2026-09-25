"""Focused offline SCAN closure tests; synthetic accepted delivery only."""
import copy
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/soil_moisture"))
import scan_history as h


class ClosureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original = self.root / "accepted"
        self.output = self.root / "closed"
        self.reference = self.root / h.REFERENCE
        fields = ["site_code", "depth_in", "water_day", "build_time_utc", "p00", "p10", "p30",
                  "p50", "p70", "p90", "p100", "climatology_ok", "min_years_for_context",
                  "years_min", "years_max", "current_water_year_excluded"]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fields)
        writer.writeheader()
        writer.writerow(dict(zip(fields, [2116, 2, 1, "2026-06-08T19:46:43Z", 0, 1, 2, 3, 4, 5, 6,
                                         "TRUE", 7, 2006, 2025, "TRUE"])))
        raw = buf.getvalue().encode()
        self.reference.write_bytes(raw)
        scope = {"stations": 1, "histories": 1, "archive_only": [[2116, 40]]}
        self.binding = {"schema": h.SCHEMA + "-inputs", "scope": scope,
                        "files": {name: {"bytes": 1, "sha256": "0" * 64}
                                  for name in (h.ARCHIVE, *h.CURRENT_FILES)}}
        self.binding["files"][h.REFERENCE] = {"bytes": len(raw), "sha256": h.sha(raw)}
        self.generation = h.sha(h.encoded(self.binding))
        self.authority = {"current_water_year": 2026, "current_wy_start_date": "2025-10-01",
                          "as_of_date": "2026-09-20", "source_feed_build_time_utc": "2026-09-20T17:36:08Z"}
        roster = {(2116, 2): {"site_code": 2116, "depth_in": 2, "station_uid": "NRCS_scan_2116",
                              "station_name": "Saved station"}}
        self.rows = [self.row("2020-01-01", 30), self.row("2021-01-01", 40),
                     self.row("2023-01-01", 105), self.row("2024-02-29", 1.55, ["SMS.I-2_2", "SMS.I_2"]),
                     self.row("2025-01-01", 0), self.row("2025-01-03", 3), self.row("2026-09-20", 0)]
        inventory = {"archive_rows": 7, "ignored_archive_current_wy_rows": 0,
                     "histories": [dict(site_code=2116, depth_in=2, rows=6, first_date="2020-01-01",
                                        last_date="2025-01-03", outside_0_100=1, browser_advertised=True),
                                   dict(site_code=2116, depth_in=40, rows=1, first_date="2019-01-01",
                                        last_date="2019-01-01", outside_0_100=0, browser_advertised=False)]}
        report = h.write_delivery(self.original, self.binding, self.generation, roster,
                                  {(2116, 2): self.rows}, inventory, self.authority)
        self.accepted_hash = report["manifest_sha256"]

    def row(self, date, value, sensors=None):
        sensors = sensors or ["SMS.I_2"]
        year, day = h.wy(h.day(date))
        return [date, float(value), year, day, len(sensors), sensors]

    def close(self):
        report = h.close_contract(self.original, self.accepted_hash, self.reference, self.output)
        self.manifest = json.loads((self.output / "manifest.json").read_text())
        self.index = json.loads((self.output / self.manifest["index"]["path"]).read_text())
        return report

    def rewrite_manifest(self):
        raw = h.encoded(self.manifest)
        (self.output / "manifest.json").write_bytes(raw)
        return h.sha(raw)

    def test_four_capabilities_and_exact_reused_payloads(self):
        result = self.close()
        self.assertEqual(result["last3_shards"], 1)
        self.assertEqual(result["normal_activation_history_hover_body_bytes"], 0)
        for obj in (self.manifest, self.index, self.index["histories"][0]):
            self.assertEqual(set(obj["capabilities"]), set(h.CAPABILITIES))
            for cap in obj["capabilities"].values():
                self.assertEqual(cap["status"], "available")
                self.assertEqual(cap["generation"], self.generation)
        old = json.loads((self.original / "manifest.json").read_text())
        for d in old["files"]:
            if d != old["index"]:
                self.assertEqual((self.original / d["path"]).read_bytes(), (self.output / d["path"]).read_bytes())
        self.assertEqual(self.reference.read_bytes(), (self.output / self.manifest["reference_band"]["path"]).read_bytes())
        self.assertEqual(self.manifest["reference_source"]["source_generation"], "2026-06-08T19:46:43Z")
        self.assertEqual(self.manifest["normal_activation"]["required_capability_payloads"], [])
        policy = self.manifest["capabilities"]["hover_30d"]["policy"]
        self.assertFalse(policy["required_on_normal_activation"])
        self.assertEqual(policy["request_event"], "first_SCAN_hover_cache_miss")
        self.assertEqual(policy["generation_change"], "invalidate")

    def test_exact_completed_years_values_gaps_composites_and_identity(self):
        self.close()
        entry = self.index["histories"][0]
        cap = entry["capabilities"]["history_last3"]
        self.assertEqual(cap["selected_years"], [2023, 2024, 2025])
        shard = json.loads((self.output / cap["file"]["path"]).read_text())
        self.assertEqual(shard["rows"], self.rows[2:6])
        self.assertEqual(shard["rows"][0][1], 105)
        self.assertEqual(shard["rows"][1][4:], [2, ["SMS.I-2_2", "SMS.I_2"]])
        self.assertEqual(shard["rows"][2][1], 0)
        self.assertNotIn("2025-01-02", [r[0] for r in shard["rows"]])
        self.assertNotIn(2026, [r[2] for r in shard["rows"]])
        self.assertEqual((shard["station_uid"], shard["site_code"], shard["depth_in"]), ("NRCS_scan_2116", 2116, 2))
        hover = json.loads((self.output / self.manifest["hover"]["path"]).read_text())
        self.assertEqual(hover["histories"][0]["values"], [None] * 29 + [0])
        self.assertEqual(len(self.index["histories"]), 1)
        self.assertFalse(any("2116-40-" in f["path"] for f in self.manifest["files"]))

    def test_up_to_three_usable_years_and_rollover(self):
        years, rows = h.select_last3([self.row("2024-02-29", 0), self.row("2026-09-20", 1)], 2026)
        self.assertEqual(years, [2024])
        self.assertEqual(len(rows), 1)
        years, _ = h.select_last3(self.rows, 2027)
        self.assertEqual(years, [2024, 2025, 2026])
        years, rows = h.select_last3([self.row("2026-09-20", 1)], 2026)
        self.assertEqual((years, rows), ([], []))
        cap = h.entry_capabilities(self.generation, {"site_code": 2116, "depth_in": 2, "file": {}},
                                   None, [], False)
        self.assertEqual(cap["history_last3"]["status"], "unavailable")
        self.assertEqual(cap["history_last3"]["reason"], "no_usable_completed_water_year")
        self.assertEqual(cap["reference_band"]["status"], "unavailable")

    def test_deterministic_closure_and_fresh_output(self):
        result = self.close()
        second = self.root / "second"
        other = h.close_contract(self.original, self.accepted_hash, self.reference, second)
        self.assertEqual(result, other)
        for f in self.manifest["files"]:
            self.assertEqual((self.output / f["path"]).read_bytes(), (second / f["path"]).read_bytes())
        with self.assertRaisesRegex(ValueError, "fresh"):
            h.close_contract(self.original, self.accepted_hash, self.reference, self.output)

    def test_generation_hash_missing_and_path_mismatch_become_unavailable(self):
        self.close()
        cap = self.manifest["capabilities"]["hover_30d"]
        self.assertEqual(h.capability_payload(self.output, cap, self.generation)["status"], "available")
        self.assertEqual(h.capability_payload(self.output, cap, "f" * 64),
                         {"status": "unavailable", "reason": "generation_mismatch"})
        for mutation in ("hash", "missing", "path", "bound"):
            bad = copy.deepcopy(cap)
            if mutation == "hash": bad["file"]["sha256"] = "0" * 64
            if mutation == "missing": bad["file"]["path"] = "hover-30d-" + "0" * 64 + ".json"; bad["file"]["sha256"] = "0" * 64
            if mutation == "path": bad["file"]["path"] = "../outside.json"
            if mutation == "bound": bad["file"]["bytes"] = h.MAX_FILE + 1
            with self.subTest(mutation=mutation):
                self.assertEqual(h.capability_payload(self.output, bad, self.generation)["status"], "unavailable")
        # Even a freshly rehashed foreign-generation JSON cannot be substituted.
        body = json.loads((self.output / cap["file"]["path"]).read_text())
        body["input_manifest_sha256"] = "f" * 64
        foreign = h.write_object(self.root, "hover-30d", body)
        bad = dict(cap, file=foreign)
        self.assertEqual(h.capability_payload(self.root, bad, self.generation)["status"], "unavailable")

    def test_manifest_policy_generation_and_activation_guards(self):
        self.close()
        original = copy.deepcopy(self.manifest)
        for mutation in ("generation", "activation", "capabilities"):
            self.manifest = copy.deepcopy(original)
            if mutation == "generation": self.manifest["generation"] = "a" * 64
            if mutation == "activation": self.manifest["normal_activation"]["hover_body_bytes"] = 1
            if mutation == "capabilities": del self.manifest["capabilities"]["reference_band"]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                h.validate(self.output, self.rewrite_manifest())

    def test_wrong_reference_binding_fails_before_output(self):
        self.reference.write_bytes(self.reference.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "reference does not match"):
            h.close_contract(self.original, self.accepted_hash, self.reference, self.output)
        self.assertFalse(self.output.exists())

    def test_rehashed_last3_wrong_pair_or_year_is_rejected(self):
        self.close()
        # Mutate the payload and every descriptor honestly: semantics must still fail.
        entry = self.index["histories"][0]
        cap = entry["capabilities"]["history_last3"]
        original = json.loads((self.output / cap["file"]["path"]).read_text())
        for mutation in ("identity", "years", "zero", "composite", "current_wy"):
            body = copy.deepcopy(original)
            if mutation == "identity": body["depth_in"] = 4
            if mutation == "years": body["selected_years"] = [2021, 2024, 2025]
            if mutation == "zero": body["rows"][2][1] = None
            if mutation == "composite": body["rows"][1][4:] = [1, ["SMS.I_2"]]
            if mutation == "current_wy": body["rows"].append(self.rows[-1])
            old_shard = cap["file"]
            new_shard = h.write_object(self.output, "last3/2116-2", body)
            cap["file"] = new_shard
            self.manifest["files"] = [new_shard if f == old_shard else f for f in self.manifest["files"]]
            (self.output / old_shard["path"]).unlink()  # test-owned object only
            old_index = self.manifest["index"]
            new_index = h.write_object(self.output, "history-index", self.index)
            self.manifest["index"] = new_index
            self.manifest["files"] = [new_index if f == old_index else f for f in self.manifest["files"]]
            for name in ("history_last3", "history_all_available"):
                self.manifest["capabilities"][name]["index"] = new_index
            (self.output / old_index["path"]).unlink()
            self.manifest["payload_bytes"] = sum(f["bytes"] for f in self.manifest["files"])
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, "Last-3 identity/selection/rows"):
                h.validate(self.output, self.rewrite_manifest())


if __name__ == "__main__":
    unittest.main()
