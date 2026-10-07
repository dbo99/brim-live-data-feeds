"""Offline synthetic edge cases and optional independently pinned saved corpus.

SNOTEL_PRESERVED_FIXTURES points to a LOCAL JSON descriptor: metadata={path,
sha256}, captures=[{path, sha256, receipt_path, receipt_sha256}], hourly={path,
sha256}, start_control={path,sha256}. Paths resolve relative to the descriptor.
No fixtures are downloaded; the real corpus is required for an acceptance run.
SNOTEL_SUSPECT_FIXTURES optionally adds a local descriptor with pinned metadata,
one capture (same fields), expected_sensor/expected_rows and legacy_archives
({station_triplet, path, manifest_sha256}) for real representation/version replay.
"""

import base64
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/soil_moisture"))
import snotel_history as sh


def element(depth=-8, ordinal=1):
    return dict(elementCode="SMS", heightDepth=depth, ordinal=ordinal,
                durationName="DAILY", storedUnitCode="pct", originalUnitCode="pct")


def station(triplet="356:CA:SNTL"):
    return dict(stationTriplet=triplet, stationId=triplet.split(":")[0],
                networkCode="SNTL", shefId="SYNTHETIC", dataTimeZone=-8,
                stationElements=[element(), element(-8, 2), element(-20), element(-2)])


def record(day="2026-03-07", value=0.0, **kwargs):
    return dict(date=day, value=value, qcFlag="V", origValue=value, origQcFlag="V", **kwargs)


def capture(rows=None, depth=-8, ordinal=1, triplet="356:CA:SNTL",
            begin="2026-03-07", end="2026-03-09", raw=None, **outcome):
    params = dict(stationTriplets=triplet, elements=f"SMS:{depth}:{ordinal}", duration="DAILY",
                  beginDate=begin, endDate=end, periodRef="END", returnFlags="true",
                  returnOriginalValues="true", returnSuspectData="true")
    body = raw if raw is not None else sh.canonical([dict(stationTriplet=triplet,
        data=[dict(stationElement=element(depth, ordinal), values=rows if rows is not None else [record()])])])
    receipt = dict(request=dict(method="GET", url=sh.TARGET + "?" + urlencode(params), parameters=params),
                   retrieved_at_utc="2026-10-06T21:15:09Z", http_status=200,
                   complete=True, error=None, response_sha256=sh.sha256(body), response_bytes=len(body),
                   evidence_kind="SYNTHETIC_TEST")
    receipt.update(outcome)
    return dict(body=body, receipt=receipt, receipt_sha256=sh.sha256(sh.canonical(receipt)))


def repin(cap):
    cap["receipt"]["response_sha256"] = sh.sha256(cap["body"])
    cap["receipt"]["response_bytes"] = len(cap["body"])
    cap["receipt_sha256"] = sh.sha256(sh.canonical(cap["receipt"]))
    return cap


class Synthetic(unittest.TestCase):
    def setUp(self):
        self.metadata = sh.canonical([station(), station("574:CA:SNTL")])
        self.identity = "356:CA:SNTL|SMS:-8:1"

    def build(self, *captures):
        return sh.build_history(self.metadata, sh.sha256(self.metadata), list(captures))

    def test_exact_sensor_and_ordinal_no_composite(self):
        caps = [capture(depth=-8, ordinal=1), capture(depth=-8, ordinal=2, rows=[record(value=16)]),
                capture(depth=-20, rows=[record(value=20)]), capture(depth=-2, rows=[record(value=2)]),
                capture(triplet="574:CA:SNTL", rows=[record(value=57)])]
        doc = self.build(*caps)
        self.assertEqual(len(doc["observations"]), 5)
        self.assertEqual({r["sensor_identity"]: r["value_native"] for r in doc["observations"]},
                         {"356:CA:SNTL|SMS:-8:1": 0.0, "356:CA:SNTL|SMS:-8:2": 16,
                          "356:CA:SNTL|SMS:-20:1": 20, "356:CA:SNTL|SMS:-2:1": 2,
                          "574:CA:SNTL|SMS:-8:1": 57})
        self.assertTrue(all(r["unit_native"] == "pct" for r in doc["observations"]))

    def test_fixed_offset_dst_leap_year_and_year_end(self):
        for day, following in (("2026-03-07", "2026-03-08"), ("2026-03-08", "2026-03-09"),
                               ("2026-10-31", "2026-11-01"), ("2026-11-01", "2026-11-02"),
                               ("2024-02-28", "2024-02-29"), ("2024-02-29", "2024-03-01"),
                               ("2026-12-31", "2027-01-01")):
            with self.subTest(day=day):
                row = self.build(capture([record(day)], begin=day, end=day))["observations"][0]
                self.assertEqual(row["provider_date"], day)
                self.assertEqual(row["source_timestamp_utc"], following + "T08:00:00Z")
                self.assertEqual(row["source_boundary_timezone"], "GMT-08")

    def test_zero_and_absent_qa_are_preserved(self):
        row = self.build(capture())["observations"][0]
        self.assertIs(type(row["value_native"]), float)
        self.assertEqual(row["value_native"], 0.0)
        self.assertEqual(row["original_value"], 0.0)
        self.assertEqual(row["observation_state"], "OBSERVED")
        self.assertEqual((row["qc_flag"], row["original_qc_flag"]), ("V", "V"))
        self.assertNotIn("qaFlag", row["provider_record"])
        self.assertNotIn("qaFlag", row["source_fields_present"])

    def test_explicit_missing_null_or_m_keeps_native_record(self):
        cases = [dict(date="2026-03-07", value=None, qcFlag="V"),
                 dict(date="2026-03-07", value=12.5, qcFlag="M", qaFlag="R"),
                 dict(date="2026-03-07", qcFlag="M", qaFlag=None)]
        for source in cases:
            with self.subTest(source=source):
                doc = self.build(capture([source]))
                row = doc["observations"][0]
                self.assertEqual(row["provider_record"], source)
                self.assertEqual(row["observation_state"], "EXPLICIT_MISSING")
                self.assertNotIn("original_value", row)
                self.assertEqual(doc["query_ledger"][0]["status"], "successful_nonempty")
                self.assertEqual(sh.coverage_on(doc, self.identity, "2026-03-07")[0]["state"],
                                 "EXPLICIT_MISSING")

    def test_suspect_original_only_preserves_fields_and_returned_coverage(self):
        for original in (0.0, 12.5):
            for flags in ({}, dict(qaFlag=None, origQcFlag=None), dict(qaFlag="R", origQcFlag="V")):
                source = dict(date="2026-03-07", qcFlag="S", origValue=original, **flags)
                with self.subTest(source=source):
                    doc = self.build(capture([source]))
                    row = doc["observations"][0]
                    self.assertEqual(row["observation_state"], "SUSPECT_ORIGINAL_ONLY")
                    self.assertIsNone(row["value_native"])
                    self.assertEqual(row["original_value"], original)
                    self.assertIs(type(row["original_value"]), type(original))
                    self.assertEqual(row["provider_record"], source)
                    self.assertNotIn("value", row["provider_record"])
                    self.assertEqual(row["source_fields_present"], sorted(source))
                    self.assertEqual((row["qc_flag"], row["qa_flag"], row["original_qc_flag"]),
                                     ("S", source.get("qaFlag"), source.get("origQcFlag")))
                    self.assertEqual(row["source_timestamp_utc"], "2026-03-08T08:00:00Z")
                    entry = doc["query_ledger"][0]
                    self.assertEqual(entry["returned_dates"], ["2026-03-07"])
                    self.assertEqual(entry["omitted_dates"], ["2026-03-08", "2026-03-09"])
                    self.assertTrue(entry["successful_coverage"])
                    event = sh.coverage_on(doc, self.identity, "2026-03-07")[0]
                    self.assertEqual(event["state"], "SUSPECT_ORIGINAL_ONLY")
                    self.assertTrue(event["successful_coverage"])
                    for item in (doc, row, entry):
                        self.assertEqual(item["adapter_version"], "snotel-awdb-offline-1.1.0")

    def test_suspect_explicit_null_and_qc_m_still_mean_missing(self):
        sources = [dict(date="2026-03-07", value=None, qcFlag="S", origValue=12.5),
                   dict(date="2026-03-07", qcFlag="M", origValue=12.5),
                   dict(date="2026-03-07", value=None, qcFlag="M", origValue=0)]
        for source in sources:
            with self.subTest(source=source):
                row = self.build(capture([source]))["observations"][0]
                self.assertEqual(row["observation_state"], "EXPLICIT_MISSING")
                self.assertIsNone(row["value_native"])
                self.assertEqual(row["provider_record"], source)

    def test_numeric_current_suspect_remains_observed(self):
        for value in (0.0, 12.5):
            source = dict(date="2026-03-07", value=value, qcFlag="S", origValue=99.0, origQcFlag="V")
            row = self.build(capture([source]))["observations"][0]
            self.assertEqual(row["observation_state"], "OBSERVED")
            self.assertEqual((row["value_native"], row["original_value"], row["qc_flag"]),
                             (value, 99.0, "S"))
            self.assertEqual(row["provider_record"], source)

    def test_other_absent_current_value_combinations_fail_whole_capture(self):
        for fields in ([dict(qcFlag=flag) for flag in ("V", "R", "P", "A", None, "s", "unknown")] + [{}]):
            source = dict(date="2026-03-08", origValue=0.0, **fields)
            with self.subTest(source=source):
                cap = capture([record(), source])
                doc = self.build(cap)
                self.assertEqual(doc["observations"], [])
                self.assertEqual(doc["query_ledger"][0]["status"], "failed")
                self.assertFalse(doc["query_ledger"][0]["successful_coverage"])
                self.assertEqual(base64.b64decode(doc["captures"][0]["body_base64"]), cap["body"])

    def test_suspect_original_must_be_present_finite_numeric(self):
        originals = [{}, *[dict(origValue=v) for v in (None, True, "12", float("nan"), float("inf"), float("-inf"))]]
        for fields in originals:
            source = dict(date="2026-03-08", qcFlag="S", **fields)
            raw = json.dumps([dict(stationTriplet="356:CA:SNTL", data=[dict(
                stationElement=element(), values=[record(), source])])]).encode()
            with self.subTest(source=source):
                doc = self.build(capture(raw=raw))
                self.assertEqual(doc["observations"], [])
                self.assertEqual(doc["query_ledger"][0]["status"], "failed")

    def test_suspect_original_does_not_mask_invalid_present_current_value(self):
        for value in (True, "12", float("nan"), float("inf")):
            source = dict(date="2026-03-07", value=value, qcFlag="S", origValue=12.5)
            raw = json.dumps([dict(stationTriplet="356:CA:SNTL", data=[dict(
                stationElement=element(), values=[source])])]).encode()
            doc = self.build(capture(raw=raw))
            self.assertEqual(doc["observations"], [])
            self.assertEqual(doc["query_ledger"][0]["status"], "failed")

    def test_legacy_golden_archives_keep_bytes_and_rejected_representation(self):
        # Pins captured using the unmodified 1.0.0 adapter, not the new replay.
        cases = [(capture(), "62bf980b625a76728048290360dafa9f84109cae59d7f4bb8785a8808efbb09d",
                  "811b0e7e6c5d47d5a8a2e2cb3559c63c09de3bb97867b922e4b4279aed4f21e1", "successful_nonempty"),
                 (capture([dict(date="2026-03-07", qcFlag="S", origValue=0.0, origQcFlag="V")]),
                  "018e008f6e234657dc1161428a9d0bda9dbf23f5d3dbfb5d69b830d803ac41bc",
                  "f697e73f2ff614c84f34db866b8f17ffe25c3056e36b26246bbb203a0adb90ee", "failed")]
        for cap, history_pin, manifest_pin, status in cases:
            doc = sh._build_history(self.metadata, sh.sha256(self.metadata), [cap], "snotel-awdb-offline-1.0.0")
            self.assertEqual(sh.sha256(sh.canonical(doc)), history_pin)
            self.assertEqual(doc["query_ledger"][0]["status"], status)
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "legacy"
                self.assertEqual(sh.write_archive(root, doc), manifest_pin)
                before = {name: (root / name).read_bytes() for name in ("history.json", "manifest.json")}
                reopened = sh.open_archive(root, manifest_pin)
                self.assertEqual(reopened["adapter_version"], "snotel-awdb-offline-1.0.0")
                self.assertEqual(sh.canonical(reopened), before["history.json"])
                self.assertEqual(sh.write_archive(Path(tmp) / "copy", reopened), manifest_pin)
                self.assertEqual(before, {name: (root / name).read_bytes() for name in before})

    def test_unknown_versions_and_manifest_version_mismatch_rejected(self):
        doc = self.build(capture())
        for version in (None, "snotel-awdb-offline-1.2.0", "unknown"):
            bad = copy.deepcopy(doc)
            bad["adapter_version"] = version
            with self.subTest(version=version), self.assertRaises(ValueError): sh.validate_history(bad)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "archive"
            sh.write_archive(root, doc)
            manifest = json.loads((root / "manifest.json").read_bytes())
            manifest["adapter_version"] = "snotel-awdb-offline-1.0.0"
            body = sh.canonical(manifest)
            (root / "manifest.json").write_bytes(body)
            with self.assertRaises(ValueError): sh.open_archive(root, sh.sha256(body))

    def test_new_suspect_archive_deterministic_and_derived_tamper_rejected(self):
        source = dict(date="2026-03-07", qcFlag="S", origValue=0.0, origQcFlag="V")
        caps = [capture([source]), capture(depth=-20)]
        doc = self.build(*caps)
        self.assertEqual(sh.canonical(doc), sh.canonical(self.build(*reversed(caps))))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "new"
            pin = sh.write_archive(root, doc)
            reopened = sh.open_archive(root, pin)
            self.assertEqual(reopened["adapter_version"], "snotel-awdb-offline-1.1.0")
            self.assertEqual(sh.canonical(reopened), sh.canonical(doc))
            self.assertEqual(sh.write_archive(Path(tmp) / "again", reopened), pin)
        index = next(i for i, row in enumerate(doc["observations"]) if row["observation_state"] == "SUSPECT_ORIGINAL_ONLY")
        for field, value in (("value_native", 0.0), ("original_value", 7.0), ("observation_state", "EXPLICIT_MISSING"),
                             ("observation_state", "OBSERVED"), ("source_fields_present", ["date", "value"])):
            bad = copy.deepcopy(doc)
            bad["observations"][index][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError): sh.validate_history(bad)
        bad = copy.deepcopy(doc)
        for item in [bad, *bad["observations"], *bad["query_ledger"]]:
            item["adapter_version"] = "snotel-awdb-offline-1.0.0"
        with self.assertRaises(ValueError): sh.validate_history(bad)

    def test_omission_no_interpolation_or_forward_fill(self):
        doc = self.build(capture([record(), record("2026-03-09", 9)]))
        self.assertEqual(len(doc["observations"]), 2)
        self.assertEqual(doc["query_ledger"][0]["omitted_dates"], ["2026-03-08"])
        self.assertEqual(sh.coverage_on(doc, self.identity, "2026-03-08")[0]["state"], "OMITTED_SOURCE_GAP")
        self.assertEqual(sh.coverage_on(doc, self.identity, "2026-03-10")[0]["state"], "UNQUERIED")

    def test_empty_and_unqueried_need_request_evidence(self):
        for cap in (capture(raw=b"[]"), capture([])):
            doc = self.build(cap)
            self.assertEqual(doc["observations"], [])
            self.assertEqual(doc["query_ledger"][0]["status"], "successful_empty")
            self.assertEqual(sh.coverage_on(doc, self.identity, "2026-03-07")[0]["state"], "QUERIED_EMPTY")
        doc = self.build()
        self.assertEqual(doc["query_ledger"], [])
        self.assertEqual(sh.coverage_on(doc, self.identity, "2026-03-07")[0]["status"], "unqueried")
        self.assertFalse(sh.coverage_on(doc, self.identity, "2026-03-07")[0]["successful_coverage"])

    def test_transport_failure_never_advances_coverage(self):
        for outcome in (dict(http_status=500), dict(http_status=None, complete=False, error="timeout"),
                        dict(complete=False), dict(error="truncated"), dict(http_status=302)):
            with self.subTest(outcome=outcome):
                doc = self.build(capture(raw=b"[]", **outcome))
                self.assertEqual(doc["observations"], [])
                self.assertFalse(doc["query_ledger"][0]["successful_coverage"])
                self.assertEqual(sh.coverage_on(doc, self.identity, "2026-03-07")[0]["state"], "FAILED")

    def test_invalid_responses_fail_without_partial_rows(self):
        base = json.loads(capture()["body"])
        variants = [b"<html>rejected</html>", b"null", b"[", b'{"error":"rejected"}',
                    b'[{"stationTriplet":"356:CA:SNTL","stationTriplet":"356:CA:SNTL"}]']
        for change in ("station", "depth", "ordinal", "units", "date", "duplicate", "absent", "block", "error"):
            altered = copy.deepcopy(base)
            block = altered[0]["data"][0]
            if change == "station": altered[0]["stationTriplet"] = "574:CA:SNTL"
            if change == "depth": block["stationElement"]["heightDepth"] = -20
            if change == "ordinal": block["stationElement"]["ordinal"] = 2
            if change == "units": block["stationElement"]["storedUnitCode"] = "fraction"
            if change == "date": block["values"][0]["date"] = "2026-03-06"
            if change == "duplicate": block["values"].append(block["values"][0])
            if change == "absent": block["values"][0].pop("value")
            if change == "block": altered[0]["data"] = ["malformed"]
            if change == "error": block["error"] = "provider error"
            variants.append(sh.canonical(altered))
        for raw in variants:
            with self.subTest(raw=raw[:90]):
                doc = self.build(capture(raw=raw))
                self.assertEqual(doc["observations"], [])
                self.assertEqual(doc["query_ledger"][0]["status"], "failed")
                self.assertEqual(base64.b64decode(doc["captures"][0]["body_base64"]), raw)

    def test_nonfinite_boolean_and_numeric_strings_are_not_observations(self):
        for value in (True, "12", float("nan"), float("inf"), float("-inf")):
            raw = json.dumps([dict(stationTriplet="356:CA:SNTL", data=[dict(
                stationElement=element(), values=[record(), record("2026-03-08", value)])])]).encode()
            doc = self.build(capture(raw=raw))
            self.assertEqual(doc["query_ledger"][0]["status"], "failed")
            self.assertEqual(doc["observations"], [])
        raw = capture()["body"].replace(b'"value":0.0', b'"value":1e999')
        self.assertEqual(self.build(capture(raw=raw))["query_ledger"][0]["status"], "failed")

    def test_unknown_finite_value_not_clamped_or_imported_sentinel(self):
        row = self.build(capture([record(value=-99.9, qaFlag="unrecognized")]))["observations"][0]
        self.assertEqual(row["value_native"], -99.9)
        self.assertEqual(row["qa_flag"], "unrecognized")
        self.assertEqual(row["observation_state"], "OBSERVED")

    def test_quality_and_value_revisions_remain_separate(self):
        caps = [capture([record(value=v, qaFlag=qa)], retrieved_at_utc=f"2026-10-0{i}T00:00:00Z")
                for i, (v, qa) in enumerate(((1, "R"), (1, "P"), (2, "A")), 1)]
        doc = self.build(*caps, capture(http_status=503))
        self.assertEqual(sorted((r["value_native"], r["qa_flag"]) for r in doc["observations"]),
                         [(1, "P"), (1, "R"), (2, "A")])
        events = sh.coverage_on(doc, self.identity, "2026-03-07")
        self.assertEqual(len(events), 4)
        self.assertEqual(sum(e["successful_coverage"] for e in events), 3)
        self.assertIn("FAILED", [e["state"] for e in events])

    def test_provenance_and_raw_bytes_survive(self):
        cap = capture()
        doc = self.build(cap)
        row = doc["observations"][0]
        self.assertEqual(row["station_id"], "356")
        self.assertEqual(row["station_metadata_identifiers"]["shefId"], "SYNTHETIC")
        self.assertEqual(row["response_sha256"], sh.sha256(cap["body"]))
        self.assertEqual(row["query_begin_date"], "2026-03-07")
        self.assertEqual(row["query_end_date"], "2026-03-09")
        self.assertEqual(row["request_identity"], sh.sha256(sh.canonical(cap["receipt"]["request"])))
        self.assertEqual(row["retrieved_at_utc"], cap["receipt"]["retrieved_at_utc"])
        self.assertEqual(doc["captures"][0]["receipt"], cap["receipt"])
        self.assertEqual(base64.b64decode(doc["captures"][0]["body_base64"]), cap["body"])
        self.assertEqual(base64.b64decode(doc["metadata"]["body_base64"]), self.metadata)

    def test_request_binding_and_scope_fail_closed(self):
        for parameter, value in (("periodRef", "START"), ("duration", "HOURLY"),
                                 ("returnFlags", "false"), ("elements", "SMS:*:*"),
                                 ("elements", "SMS:-9:1"), ("stationTriplets", "*"),
                                 ("endDate", "2027-03-09")):
            cap = capture()
            params = cap["receipt"]["request"]["parameters"]
            params[parameter] = value
            cap["receipt"]["request"]["url"] = sh.TARGET + "?" + urlencode(params)
            with self.subTest(parameter=parameter, value=value), self.assertRaises(ValueError):
                self.build(repin(cap))
        cap = capture()
        cap["receipt"]["request"]["url"] += "&periodRef=END"
        with self.assertRaises(ValueError): self.build(repin(cap))
        cap = capture()
        cap["receipt"]["request"]["parameters"]["endDate"] = "2026-03-08"
        with self.assertRaises(ValueError): self.build(repin(cap))

    def test_raw_receipt_and_metadata_pins(self):
        cap = capture()
        cap["body"] += b" "
        with self.assertRaises(ValueError): self.build(cap)
        cap = capture()
        cap["receipt"]["request"]["parameters"]["beginDate"] = "2026-03-08"
        with self.assertRaises(ValueError): self.build(cap)
        with self.assertRaises(ValueError):
            sh.build_history(self.metadata + b" ", sh.sha256(self.metadata), [])

    def test_raw_fields_cannot_be_rehashed_without_changing_trusted_receipt(self):
        for key, value in (("date", "2026-03-08"), ("value", 1), ("qcFlag", "M"), ("qaFlag", "A")):
            cap = capture()
            raw = json.loads(cap["body"])
            raw[0]["data"][0]["values"][0][key] = value
            cap["body"] = sh.canonical(raw)
            # Updating just the raw hash/size still conflicts with the caller's receipt pin.
            cap["receipt"]["response_sha256"] = sh.sha256(cap["body"])
            cap["receipt"]["response_bytes"] = len(cap["body"])
            with self.subTest(key=key), self.assertRaises(ValueError): self.build(cap)

    def test_derived_identity_date_value_flag_tampering(self):
        doc = self.build(capture())
        for key, value in (("sensor_identity", "356:CA:SNTL|SMS:-20:1"), ("provider_date", "2026-03-08"),
                           ("source_timestamp_utc", "2026-03-08T07:00:00Z"), ("value_native", 99),
                           ("qa_flag", "A"), ("qc_flag", "M"), ("station_id", "574")):
            bad = copy.deepcopy(doc)
            bad["observations"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): sh.validate_history(bad)
        bad = copy.deepcopy(doc)
        bad["query_ledger"][0]["omitted_dates"] = []
        with self.assertRaises(ValueError): sh.validate_history(bad)

    def test_deterministic_reopen_and_external_manifest_pin(self):
        caps = [capture(), capture(depth=-20)]
        doc = self.build(*caps)
        self.assertEqual(sh.canonical(doc), sh.canonical(self.build(*reversed(caps))))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "archive"
            pin = sh.write_archive(root, doc)
            self.assertEqual(sh.canonical(sh.open_archive(root, pin)), sh.canonical(doc))
            other = Path(tmp) / "reopen"
            self.assertEqual(sh.write_archive(other, sh.open_archive(root, pin)), pin)
            for name in ("manifest.json", "history.json"):
                self.assertEqual((root / name).read_bytes(), (other / name).read_bytes())
                out = json.loads((root / name).read_bytes())
                for key in sh.STATUS: self.assertIs(out[key], False)
            with self.assertRaises(FileExistsError): sh.write_archive(root, doc)
            bad = copy.deepcopy(doc)
            bad["observations"][0]["value_native"] = 99
            content = sh.canonical(bad)
            (root / "history.json").write_bytes(content)
            with self.assertRaises(ValueError): sh.open_archive(root, pin)
            manifest = json.loads((root / "manifest.json").read_bytes())
            manifest["files"]["history.json"] = dict(bytes=len(content), sha256=sh.sha256(content))
            (root / "manifest.json").write_bytes(sh.canonical(manifest))
            with self.assertRaises(ValueError): sh.open_archive(root, pin)
            # Even a mistakenly repinned envelope cannot legitimize altered derived rows.
            with self.assertRaises(ValueError): sh.open_archive(root, sh.sha256(sh.canonical(manifest)))

    def test_file_closure_symlinks_and_input_bounds(self):
        doc = self.build(capture())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "archive"
            pin = sh.write_archive(root, doc)
            (root / "extra.json").write_text("{}")
            with self.assertRaises(ValueError): sh.open_archive(root, pin)
            (root / "extra.json").unlink()
            (root / "history.json").rename(Path(tmp) / "outside")
            (root / "history.json").symlink_to(Path(tmp) / "outside")
            with self.assertRaises(ValueError): sh.open_archive(root, pin)
        with self.assertRaises(ValueError): self.build(capture(raw=b" " * (sh.MAX_BODY + 1)))
        with self.assertRaises(ValueError): self.build(*[capture() for _ in range(33)])

    def test_incomplete_multi_sensor_response_does_not_claim_empty(self):
        cap = capture()
        params = cap["receipt"]["request"]["parameters"]
        params["elements"] += ",SMS:-20:1"
        cap["receipt"]["request"]["url"] = sh.TARGET + "?" + urlencode(params)
        doc = self.build(repin(cap))
        self.assertEqual(doc["observations"], [])
        self.assertEqual([e["status"] for e in doc["query_ledger"]], ["failed", "failed"])

    def test_no_network_and_no_input_mutation(self):
        cap = capture()
        saved = copy.deepcopy(cap)
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), \
                patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden")):
            sh.validate_history(self.build(cap))
        self.assertEqual(cap, saved)


@unittest.skipUnless(os.environ.get("SNOTEL_PRESERVED_FIXTURES"), "set local preserved fixture descriptor")
class PreservedEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = Path(os.environ["SNOTEL_PRESERVED_FIXTURES"])
        cls.spec = json.loads(cls.path.read_text())

        def pinned(spec):
            body = (cls.path.parent / spec["path"]).read_bytes()
            if sh.sha256(body) != spec["sha256"]: raise ValueError("preserved fixture pin mismatch")
            return body

        cls.pinned = staticmethod(pinned)
        cls.captures = []
        for item in cls.spec["captures"]:
            receipt = json.loads((cls.path.parent / item["receipt_path"]).read_bytes())
            cls.captures.append(dict(body=pinned(item), receipt=receipt, receipt_sha256=item["receipt_sha256"]))
        cls.doc = sh.build_history(pinned(cls.spec["metadata"]), cls.spec["metadata"]["sha256"], cls.captures)

    def test_three_station_six_sensor_native_row_parity(self):
        pilot = [c for c in self.captures if c["receipt"].get("fixture_role") == "pilot"]
        self.assertEqual(len(pilot), 3)
        identities, trips, count = set(), set(), 0
        for cap in pilot:
            capid = sh.sha256(sh.canonical(cap["receipt"]))
            output = [r for r in self.doc["observations"] if r["capture_identity"] == capid]
            native = json.loads(cap["body"])
            for station_data in native:
                trips.add(station_data["stationTriplet"])
                for block in station_data["data"]:
                    e = block["stationElement"]
                    identity = sh.sensor_identity(station_data["stationTriplet"], e["heightDepth"], e["ordinal"])
                    identities.add(identity)
                    rows = [r for r in output if r["sensor_identity"] == identity]
                    self.assertEqual([r["provider_record"] for r in rows], block["values"])
                    self.assertEqual({r["unit_native"] for r in rows}, {"pct"})
                    count += len(rows)
        self.assertEqual(trips, {"356:CA:SNTL", "574:CA:SNTL", "1051:CA:SNTL"})
        self.assertEqual(len(identities), 6)
        self.assertEqual(count, 540)

    def test_historical_zero_empty_and_omission(self):
        identity = "356:CA:SNTL|SMS:-2:1"
        rows = [r for r in self.doc["observations"] if r["sensor_identity"] == identity]
        self.assertEqual([r["provider_date"] for r in rows], ["2005-08-22", "2005-08-23", "2005-08-24"])
        for row in rows:
            self.assertEqual((row["value_native"], row["original_value"], row["observation_state"]),
                             (0.0, 0.0, "OBSERVED"))
            self.assertNotIn("qaFlag", row["provider_record"])
            self.assertEqual((row["qc_flag"], row["original_qc_flag"]), ("V", "V"))
        self.assertEqual({e["state"] for e in sh.coverage_on(self.doc, identity, "2005-08-21")},
                         {"QUERIED_EMPTY", "OMITTED_SOURCE_GAP"})
        self.assertEqual(sh.coverage_on(self.doc, identity, "2005-08-19")[0]["state"], "UNQUERIED")

    def test_daily_end_matches_saved_next_hourly_boundary_and_start_control(self):
        hourly = json.loads(self.pinned(self.spec["hourly"]))[0]["data"][0]["values"]
        by_clock = {r["date"]: r["value"] for r in hourly}
        rows = [r for r in self.doc["observations"] if r["sensor_identity"] == "356:CA:SNTL|SMS:-8:1"
                and "2026-09-14" <= r["provider_date"] <= "2026-09-19"
                and r["query_begin_date"] == "2026-06-23"]
        self.assertEqual(len(rows), 6)
        for row in rows:
            following = row["source_timestamp_utc"][:10]
            self.assertEqual(row["value_native"], by_clock[following + " 00:00"])
        changed = next(r for r in rows if r["provider_date"] == "2026-09-18")
        self.assertNotEqual(changed["value_native"], by_clock["2026-09-18 00:00"])
        start = json.loads(self.pinned(self.spec["start_control"]))[0]["data"][0]["values"]
        self.assertEqual(len(start), 3)
        for row in start:
            self.assertEqual(row["value"], by_clock[row["date"] + " 00:00"])

    def test_real_archive_deterministic_and_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "archive"
            pin = sh.write_archive(root, self.doc)
            self.assertEqual(sh.canonical(sh.open_archive(root, pin)), sh.canonical(self.doc))
        self.assertEqual(sh.canonical(self.doc), sh.canonical(sh.build_history(
            self.pinned(self.spec["metadata"]), self.spec["metadata"]["sha256"], list(reversed(self.captures)))))


@unittest.skipUnless(os.environ.get("SNOTEL_SUSPECT_FIXTURES"), "set local suspect/legacy fixture descriptor")
class SuspectPreservedEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = Path(os.environ["SNOTEL_SUSPECT_FIXTURES"])
        cls.spec = json.loads(cls.path.read_bytes())
        md, cap = cls.spec["metadata"], cls.spec["capture"]
        cls.metadata = (cls.path.parent / md["path"]).read_bytes()
        cls.body = (cls.path.parent / cap["path"]).read_bytes()
        receipt_bytes = (cls.path.parent / cap["receipt_path"]).read_bytes()
        if sh.sha256(cls.metadata) != md["sha256"] or sh.sha256(cls.body) != cap["sha256"] \
                or sh.sha256(receipt_bytes) != cap["receipt_sha256"]:
            raise ValueError("real suspect fixture pin mismatch")
        cls.capture = dict(body=cls.body, receipt=json.loads(receipt_bytes), receipt_sha256=cap["receipt_sha256"])
        cls.doc = sh.build_history(cls.metadata, md["sha256"], [cls.capture])

    def test_real_suspect_records_preserve_originals_fields_and_dates(self):
        native = {}
        for station_data in json.loads(self.body):
            for block in station_data["data"]:
                e = block["stationElement"]
                identity = sh.sensor_identity(station_data["stationTriplet"], e["heightDepth"], e["ordinal"])
                native.update({(identity, r["date"]): r for r in block["values"]})
        rows = [o for o in self.doc["observations"] if o["observation_state"] == "SUSPECT_ORIGINAL_ONLY"]
        self.assertEqual(len(rows), self.spec["expected_rows"])
        for row in rows:
            source = native[(row["sensor_identity"], row["provider_date"])]
            self.assertEqual(row["sensor_identity"], self.spec["expected_sensor"])
            self.assertNotIn("value", source)
            self.assertIsNone(row["value_native"])
            self.assertEqual(row["original_value"], source["origValue"])
            self.assertEqual(row["qc_flag"], "S")
            self.assertEqual(row["provider_record"], source)
            self.assertEqual(row["source_fields_present"], sorted(source))
            self.assertEqual((row["qa_flag"], row["original_qc_flag"]), (source.get("qaFlag"), source.get("origQcFlag")))
            event = sh.coverage_on(self.doc, row["sensor_identity"], row["provider_date"])[0]
            self.assertEqual(event["state"], "SUSPECT_ORIGINAL_ONLY")
            self.assertTrue(event["successful_coverage"])
        self.assertEqual(len(self.doc["observations"]), len(native))

    def test_real_whole_response_success_and_receipt_is_unchanged(self):
        self.assertEqual(len(self.doc["query_ledger"]), 3)
        for entry in self.doc["query_ledger"]:
            returned = sorted(o["provider_date"] for o in self.doc["observations"] if o["sensor_identity"] == entry["sensor_identity"])
            self.assertEqual(entry["status"], "successful_nonempty")
            self.assertTrue(entry["successful_coverage"])
            self.assertEqual(entry["returned_dates"], returned)
            self.assertEqual(entry["omitted_dates"], ["2010-12-02"])
        self.assertEqual(self.doc["captures"][0]["receipt"], self.capture["receipt"])
        self.assertEqual(base64.b64decode(self.doc["captures"][0]["body_base64"]), self.body)
        self.assertEqual(sh.canonical(sh.validate_history(self.doc)), sh.canonical(self.doc))

    def test_real_legacy_pilot_archives_reopen_without_byte_changes(self):
        archives = self.spec["legacy_archives"]
        self.assertEqual({a["station_triplet"] for a in archives}, {"356:CA:SNTL", "574:CA:SNTL", "1051:CA:SNTL"})
        for item in archives:
            root = self.path.parent / item["path"]
            before = {name: (root / name).read_bytes() for name in ("history.json", "manifest.json")}
            reopened = sh.open_archive(root, item["manifest_sha256"])
            self.assertEqual(reopened["adapter_version"], "snotel-awdb-offline-1.0.0")
            self.assertEqual(sh.canonical(reopened), before["history.json"])
            self.assertEqual(before, {name: (root / name).read_bytes() for name in before})


if __name__ == "__main__":
    unittest.main()
