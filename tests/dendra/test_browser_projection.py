"""Focused offline interface tests; real input roots/pins are explicit env inputs."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"scripts"))
from dendra.history_acquisition import browser_projection as p
from dendra.history_acquisition.safety import Hold, encode, decode, sha, digest

ATTEMPTS = []


def deny_network(event, args):
    if event.startswith("socket."):
        ATTEMPTS.append(event)
        raise AssertionError("Offline projection forbids sockets/DNS")


sys.addaudithook(deny_network)
PERCENT = "63531a67a9b61453fa1ca4ed"
VWC = "5d8e42e72da5c3cc53f6531d"
DIMENSIONLESS = "5d9272a12da5c3cff0f655ed"


def synthetic_latest(unit="VolumetricWaterContent"):
    ident = dict(station_id="0"*24, stream_id="1"*24, native_unit=unit,
        unit_status="verified_percent_conversion", depth_cm=None, orientation=None)
    return dict(schema_version=p.LATEST, synthetic=True, publication_allowed=False, current_state_claim=False,
        status="AVAILABLE", reason="synthetic_contract_example", record=dict(representation="latest_instantaneous",
            identity=ident, native_value=0.25 if unit == "VolumetricWaterContent" else 25,
            normalized_percent=25, source_timestamp="2024-02-29T12:00:00.000Z",
            retrieved_at="2024-03-01T12:00:00.000Z", evaluated_at="2024-03-02T12:00:00.000Z",
            observation_age_seconds=172800, retrieval_age_seconds=86400, scale_state="RESOLVED_PERCENT",
            generation=digest({"synthetic":True}), source_lineage_sha256=digest({"test_only":True}),
            presentation_eligible=True, latest_witness=True))


class ProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prepared = Path(os.environ["DENDRA_PROJECTION_PREPARED"])
        cls.pins = json.loads(Path(os.environ["DENDRA_PROJECTION_PINS"]).read_text())
        cls.fingerprint = os.environ["DENDRA_PREPARED_FINGERPRINT"]
        cls.scratch = Path(os.environ["DENDRA_PROJECTION_SCRATCH"])
        cls.inputs, cls.rows = p.load_prepared(cls.prepared, pins=cls.pins, prepared_fingerprint=cls.fingerprint)
        cls.files = p.render(cls.inputs, cls.rows)
        cls.manifest = decode(cls.files["manifest.json"])

    @classmethod
    def tearDownClass(cls):
        if ATTEMPTS: raise AssertionError(ATTEMPTS)

    def body(self, kind, sid=PERCENT):
        return next(decode(v) for v in self.files.values() if decode(v)["kind"] == kind and
                    (kind == "manifest" or decode(v).get("identity", {}).get("stream_id") == sid))

    def directory(self, files=None):
        root = Path(tempfile.mkdtemp(prefix="projection-", dir=self.scratch))
        for name, body in (self.files if files is None else files).items():
            path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)
        return root

    def validate(self, root, files=None):
        files = self.files if files is None else files
        return p.validate_delivery(root, manifest_sha256=sha(files["manifest.json"]), generation=decode(files["manifest.json"])["generation"])

    def test_real_fixture_export(self):
        parent = Path(tempfile.mkdtemp(dir=self.scratch))
        result = p.export(self.prepared, pins=self.pins, prepared_fingerprint=self.fingerprint, output_root=parent/"delivery")
        self.assertEqual(result["files"], 9)
        self.assertEqual(self.rows[PERCENT][0]["date"], "2024-02-15")
        self.assertEqual([len(self.rows[x]) for x in (PERCENT, VWC, DIMENSIONLESS)], [15, 15, 0])

    def test_deterministic_bytes_and_hashes(self):
        self.assertEqual(p.render(copy.deepcopy(self.inputs), copy.deepcopy(self.rows)), self.files)

    def test_exact_root_closure(self):
        root = self.directory(); result = self.validate(root)
        self.assertEqual(result["bytes"], sum(map(len, self.files.values())))
        self.assertEqual(self.manifest["closure"]["accepted_daily_rows"], 30)

    def test_root_revision_pin_required(self):
        with self.assertRaises(Hold): p.validate_delivery(self.directory(), manifest_sha256="0"*64, generation=self.manifest["generation"])

    def test_tampered_body(self):
        root = self.directory(); name = self.manifest["files"][0]["path"]
        (root/name).write_bytes(b"{}");
        with self.assertRaises(Hold): self.validate(root)

    def test_missing_body(self):
        files = dict(self.files); files.pop(self.manifest["files"][0]["path"])
        with self.assertRaises(FileNotFoundError): self.validate(self.directory(files))

    def test_byte_count_mismatch(self):
        files = dict(self.files); m = copy.deepcopy(self.manifest); m["files"][0]["bytes"] += 1
        files["manifest.json"] = encode(m)
        with self.assertRaises(Hold): self.validate(self.directory(files), files)

    def path_case(self, change):
        d = copy.deepcopy(next(x for x in self.manifest["files"] if x["kind"] == "history")); change(d)
        with self.assertRaises(Hold): p.validate_path(d)

    def test_safe_relative_path(self):
        for d in self.manifest["files"]: p.validate_path(d)

    def test_absolute_path_rejected(self): self.path_case(lambda d:d.update(path="/"+d["path"]))
    def test_traversal_rejected(self): self.path_case(lambda d:d.update(path="../"+d["path"]))
    def test_encoded_traversal_rejected(self): self.path_case(lambda d:d.update(path="%252e%252e/"+d["path"]))
    def test_backslash_rejected(self): self.path_case(lambda d:d.update(path=d["path"].replace("/", "\\")))
    def test_wrong_prefix_rejected(self): self.path_case(lambda d:d.update(kind="hover"))
    def test_hash_suffix_rejected(self): self.path_case(lambda d:d.update(sha256="0"*64))
    def test_stream_filename_identity_rejected(self): self.path_case(lambda d:d.update(stream_id="0"*24))
    def test_wy_filename_identity_rejected(self): self.path_case(lambda d:d.update(water_year=2025))

    def test_symlink_body_rejected(self):
        files = dict(self.files); name = self.manifest["files"][0]["path"]; body = files.pop(name)
        root = self.directory(files); outside = self.scratch/(root.name+"-target.json"); outside.write_bytes(body)
        (root/name).parent.mkdir(parents=True, exist_ok=True); (root/name).symlink_to(outside)
        with self.assertRaises(OSError): self.validate(root)

    def test_mixed_descriptor_generation(self):
        files = dict(self.files); m = copy.deepcopy(self.manifest); m["files"][0]["generation"] = "0"*64
        files["manifest.json"] = encode(m)
        with self.assertRaises(Hold): self.validate(self.directory(files), files)

    def test_expected_generation_mismatch(self):
        with self.assertRaises(Hold): p.validate_delivery(self.directory(), manifest_sha256=sha(self.files["manifest.json"]), generation="0"*64)

    def test_rehashed_mixed_body_generation_rejected(self):
        files = dict(self.files); m = copy.deepcopy(self.manifest)
        desc = next(d for d in m["files"] if d["kind"] == "history")
        value = decode(files.pop(desc["path"])); value["generation"] = "0"*64
        body = encode(value); desc["path"] = desc["path"].replace(desc["sha256"],sha(body))
        desc.update(sha256=sha(body),bytes=len(body)); files[desc["path"]] = body; files["manifest.json"] = encode(m)
        with self.assertRaisesRegex(Hold,"Mixed body generation"): self.validate(self.directory(files),files)

    def test_rehashed_payload_identity_rejected(self):
        files = dict(self.files); m = copy.deepcopy(self.manifest)
        desc = next(d for d in m["files"] if d["kind"] == "history")
        value = decode(files.pop(desc["path"])); value["identity"]["station_id"] = "0"*24
        body = encode(value); desc["path"] = desc["path"].replace(desc["sha256"],sha(body))
        desc.update(sha256=sha(body),bytes=len(body)); files[desc["path"]] = body; files["manifest.json"] = encode(m)
        with self.assertRaises(Hold): self.validate(self.directory(files),files)

    def test_changed_authoritative_input_changes_generation(self):
        inputs = copy.deepcopy(self.inputs); inputs["acquisition"]["evidence_manifest_sha256"] = "a"*64
        files = p.render(inputs,self.rows)
        self.assertNotEqual(decode(files["manifest.json"])["generation"],self.manifest["generation"])

    def test_false_full_por_claim_rejected(self):
        files = dict(self.files); m = copy.deepcopy(self.manifest); m["full_por_complete"] = True
        files["manifest.json"] = encode(m)
        with self.assertRaises(Hold): self.validate(self.directory(files),files)

    def test_prepared_source_mismatch(self):
        with self.assertRaises(Hold): p.load_prepared(self.prepared, pins=self.pins, prepared_fingerprint="0"*64)

    def test_tampered_input_pin(self):
        pins = copy.deepcopy(self.pins); pins[0]["sha256"] = "0"*64
        with self.assertRaises(Hold): p.load_prepared(self.prepared, pins=pins, prepared_fingerprint=self.fingerprint)

    def test_percent_times_one(self):
        for r in self.rows[PERCENT]: self.assertEqual(r["mean_native"], r["mean_percent"])

    def test_vwc_times_100(self):
        for r in self.rows[VWC]: self.assertAlmostEqual(r["mean_native"]*100, r["mean_percent"], places=10)

    def test_unresolved_dimensionless_capabilities(self):
        d = self.body("streams", DIMENSIONLESS)
        self.assertEqual(d["history_shards"], []); self.assertIsNone(d["normalized_target_unit"])
        for k in ("hover_30d", "history_last3", "history_all_available"):
            self.assertEqual(d["capabilities"][k]["reason"], "scale_unresolved")

    def test_dimensionless_numeric_denied(self):
        rows = copy.deepcopy(self.rows); r = copy.deepcopy(rows[PERCENT][0]); r["identity"] = self.inputs["streams"][DIMENSIONLESS]["identity"]; rows[DIMENSIONLESS] = [r]
        with self.assertRaises(Hold): p.render(self.inputs, rows)

    def test_daily_and_instantaneous_disjoint(self):
        rows = copy.deepcopy(self.rows); rows[PERCENT][0]["representation"] = "latest_instantaneous"
        with self.assertRaises(Hold): p.render(self.inputs, rows)

    def test_incomplete_day_rejected(self):
        rows = copy.deepcopy(self.rows); rows[PERCENT][0]["date"] = "2026-09-26"; rows[PERCENT].sort(key=lambda r:r["date"])
        with self.assertRaises(Hold): p.render(self.inputs, rows)

    def test_real_latest_unavailable(self):
        for sid in self.rows:
            point = self.body("streams", sid)["latest_instantaneous"]
            self.assertEqual(point["status"], "UNAVAILABLE"); self.assertIsNone(point["record"])

    def test_terminal_cannot_be_latest(self):
        point = synthetic_latest(); point["record"].update(representation="historical_terminal", latest_witness=False)
        with self.assertRaises(Hold): p.validate_latest(point)

    def test_synthetic_latest_available(self):
        for unit in ("Percent", "VolumetricWaterContent"):
            self.assertTrue(p.validate_latest(synthetic_latest(unit)))

    def test_synthetic_daily_mean_denied(self):
        point = synthetic_latest(); point["record"]["representation"] = "completed_daily"
        with self.assertRaises(Hold): p.validate_latest(point)

    def test_synthetic_timestamp_preserved_and_stale(self):
        point = synthetic_latest(); p.validate_latest(point)
        self.assertEqual(point["record"]["source_timestamp"], "2024-02-29T12:00:00.000Z")
        self.assertEqual(point["record"]["observation_age_seconds"], 172800)

    def test_synthetic_publication_denied(self):
        point = synthetic_latest(); point["publication_allowed"] = True
        with self.assertRaises(Hold): p.validate_latest(point)

    def test_covered_empty_native_evidence(self):
        intervals = self.body("streams", DIMENSIONLESS)["coverage"]["intervals"]
        self.assertEqual([(i["query_state"], i["row_count"]) for i in intervals], [("COMPLETE_NONEMPTY",52),("COVERED_EMPTY",0)])

    def test_zero_preserved(self):
        rows = copy.deepcopy(self.rows)
        for k in ("mean_native", "mean_percent", "mean_value"): rows[PERCENT][0][k] = 0
        files = p.render(self.inputs, rows)
        row = next(decode(v)["rows"][0] for v in files.values() if decode(v)["kind"] == "history" and decode(v)["identity"]["stream_id"] == PERCENT)
        self.assertEqual(row["mean_percent"], 0); self.assertIsNotNone(row["mean_percent"])

    def test_leap_day_and_fixed_pst(self):
        row = self.rows[PERCENT][-1]
        self.assertEqual((row["date"],row["water_year"],row["dowy"],row["water_day_aligned"]), ("2024-02-29",2024,152,152))
        self.assertEqual(self.manifest["presentation_horizon"]["fixed_pst_offset"], "-08:00")

    def test_invalid_wy_date_relationship(self):
        rows = copy.deepcopy(self.rows); rows[PERCENT][0]["water_year"] = 2025
        with self.assertRaises(Hold): p.render(self.inputs, rows)

    def test_duplicate_conflicting_daily_identity(self):
        rows = copy.deepcopy(self.rows); rows[PERCENT].insert(0, copy.deepcopy(rows[PERCENT][0]))
        with self.assertRaises(Hold): p.render(self.inputs, rows)

    def test_null_nonfinite_and_boolean_numeric_denied(self):
        for bad in (None, float("nan"), float("inf"), True):
            rows = copy.deepcopy(self.rows); rows[PERCENT][0]["mean_percent"] = bad
            with self.assertRaises(Hold): p.render(self.inputs, rows)

    def test_query_incomplete_row_denied(self):
        rows = copy.deepcopy(self.rows); rows[PERCENT][0]["query_complete"] = False
        with self.assertRaises(Hold): p.render(self.inputs, rows)

    def test_explicit_ten_wy_not_full_por(self):
        view = self.manifest["presentation_horizon"]
        self.assertEqual((view["mode"],view["maximum_water_year_count"],view["earliest_water_year"]), ("INITIAL_PRESENTATION_10_WY",10,2017))
        self.assertFalse(view["full_por_complete"]); self.assertEqual(view["full_por_state"], "not_established")

    def test_rollover_fixed_pst(self):
        self.assertEqual(p.horizon("2026-10-01T07:59:59.000Z")["current_water_year"],2026)
        self.assertEqual(p.horizon("2026-10-01T08:00:00.000Z")["current_water_year"],2027)

    def test_all_available_scope(self):
        d = self.body("streams")
        self.assertEqual(d["capabilities"]["history_all_available"]["reason"], "acquired_product_horizon_only")
        self.assertEqual(len(d["history_shards"]), 1)

    def test_last3_exact_year_selection(self):
        years = self.body("streams")["capabilities"]["history_last3"]["years"]
        self.assertEqual([x["water_year"] for x in years], [2024,2025,2026])
        self.assertEqual([x["state"] for x in years], ["available_acquired_shard","unqueried_not_acquired","unqueried_not_acquired"])
        self.assertFalse(any(x["query_complete_for_water_year"] for x in years))

    def test_reference_not_computed(self):
        for sid in self.rows:
            c = self.body("streams",sid)["capabilities"]["reference_band"]
            self.assertEqual(c["status"], "unavailable"); self.assertEqual(c["reason"], "not_computed"); self.assertNotIn("file", c)

    def test_normal_activation_zero_bodies(self):
        self.assertEqual(self.manifest["normal_activation"], dict(history_body_bytes=0, hover_body_bytes=0, required_capability_payloads=[]))

    def test_selected_paths_no_filename_guessing(self):
        d = self.body("streams")
        for f in d["capabilities"]["history_all_available"]["files"]+[d["capabilities"]["hover_30d"]["file"]]:
            payload = decode(self.files[f["path"]]); self.assertEqual(payload["identity"]["stream_id"], PERCENT)
            self.assertEqual(sha(self.files[f["path"]]), f["sha256"])

    def test_unrelated_body_rejected(self):
        root = self.directory(); (root/"extra.json").write_text("{}")
        with self.assertRaises(Hold): self.validate(root)

    def test_private_material_absent(self):
        joined = b"".join(self.files.values())
        for token in (b'"coordinates":', b'"geometry":{', b'/Users/', b'api.dendra', b'Authorization', b'BLM'):
            self.assertNotIn(token,joined)

    def test_unknown_field_rejected(self):
        files = dict(self.files); m = copy.deepcopy(self.manifest); m["made_up"] = True; files["manifest.json"] = encode(m)
        with self.assertRaises(Hold): self.validate(self.directory(files),files)

    def test_no_overwrite(self):
        root = self.directory()
        with self.assertRaises(FileExistsError): p.export(self.prepared,pins=self.pins,prepared_fingerprint=self.fingerprint,output_root=root)

    def test_hover_thirty_unqueried_slots_not_stale_shift(self):
        slots = self.body("hover")["slots"]
        self.assertEqual(len(slots),30); self.assertEqual(slots[-1]["date"],"2026-09-25")
        self.assertTrue(all(x["state"] == "unqueried" and x["mean_percent"] is None for x in slots))

    def test_hover_gap_ineligible_empty_and_zero_states(self):
        inputs, rows = copy.deepcopy(self.inputs), copy.deepcopy(self.rows)
        inputs["as_of"] = "2024-03-03T08:00:00.000Z"
        meta = inputs["streams"][PERCENT]
        rows[PERCENT].pop(1); meta["rejected_dates"] = ["2024-02-16"]
        for k in ("mean_native", "mean_percent", "mean_value"): rows[PERCENT][0][k] = 0
        empty = dict(meta["intervals"][0], start="2024-03-01T08:00:00.000Z", end="2024-03-02T08:00:00.000Z", query_state="COVERED_EMPTY", row_count=0)
        meta["intervals"].append(empty)
        rows[PERCENT].pop(2)  # complete query, no accepted or explicitly rejected measurement
        files = p.render(inputs,rows)
        slots = next(decode(v)["slots"] for v in files.values() if decode(v)["kind"]=="hover" and decode(v)["identity"]["stream_id"]==PERCENT)
        byday = {s["date"]:s for s in slots}
        self.assertEqual(byday["2024-02-15"]["mean_percent"],0)
        self.assertEqual(byday["2024-02-16"]["state"],"ineligible")
        self.assertEqual(byday["2024-02-18"]["state"],"missing_measurement")
        self.assertEqual(byday["2024-03-01"]["state"],"covered_empty")
        self.assertEqual(byday["2024-03-02"]["state"],"unqueried")


if __name__ == "__main__": unittest.main()
