"""Offline handoff checks with explicit immutable first-batch inputs.

Every test output remains in DENDRA_TEST_ROOT. Socket/DNS and sleeps are denied;
the one R invocation uses installed packages and the unchanged daily core.
"""
import copy
from contextlib import redirect_stdout
import csv
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"scripts"))
NETWORK_ATTEMPTS = []
SLEEP_ATTEMPTS = []


def deny_network(event, args):
    if event.startswith("socket."):
        NETWORK_ATTEMPTS.append(event)
        raise AssertionError("Offline handoff forbids socket/DNS")


def deny_sleep(seconds):
    SLEEP_ATTEMPTS.append(seconds)
    raise AssertionError("Offline handoff forbids sleep")


sys.addaudithook(deny_network)
from dendra.history_acquisition import daily_handoff as handoff
from dendra.history_acquisition import campaign_cli
from dendra.history_acquisition.model import Inventory, INVENTORY_SHA256
from dendra.history_acquisition.safety import Hold, decode, encode, sha

PERCENT = "63531a67a9b61453fa1ca4ed"
VWC = "5d8e42e72da5c3cc53f6531d"
DIMENSIONLESS = "5d9272a12da5c3cff0f655ed"
AS_OF = "2026-09-27T02:00:00.000Z"


class HandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sleep = patch("time.sleep", deny_sleep)
        cls.sleep.start()
        cls.inventory = Inventory.load(os.environ["DENDRA_INVENTORY"], INVENTORY_SHA256)
        cls.sealed = Path(os.environ["DENDRA_SEALED_ROOT"])
        cls.pin = os.environ["DENDRA_SEALED_MANIFEST_SHA256"]
        cls.fingerprint = os.environ["DENDRA_ACQUISITION_FINGERPRINT"]
        cls.root = Path(tempfile.mkdtemp(prefix="daily-handoff-", dir=os.environ["DENDRA_TEST_ROOT"]))
        cls.verified = handoff.verify_sealed(cls.sealed, manifest_sha256=cls.pin,
            inventory=cls.inventory, acquisition_fingerprint=cls.fingerprint)
        cls.output = cls.root/"real-preparation"
        log = io.StringIO()
        argv = ["prepare-product", "--inventory", os.environ["DENDRA_INVENTORY"],
            "--inventory-sha256", INVENTORY_SHA256, "--sealed-root", str(cls.sealed),
            "--sealed-manifest-sha256", cls.pin, "--acquisition-fingerprint", cls.fingerprint,
            "--output-root", str(cls.output), "--now", AS_OF, "--cadence-mode", "initialize"]
        with redirect_stdout(log):
            code = campaign_cli.main(argv)
        (cls.root/"cli-receipt.json").write_bytes(encode(dict(argv=argv,exit_code=code,stdout=log.getvalue())))
        if code != 0:
            raise AssertionError("Offline prepare-product CLI failed: " + log.getvalue())
        cls.result = decode(log.getvalue().encode())
        cls.daily = decode((cls.output/"daily-output.json").read_bytes())
        cls.document = decode((cls.output/"handoff.json").read_bytes())
        cls.execution = decode((cls.sealed/"execution-binding.json").read_bytes())

    @classmethod
    def tearDownClass(cls):
        cls.sleep.stop()
        if NETWORK_ATTEMPTS or SLEEP_ATTEMPTS:
            raise AssertionError("Forbidden offline activity")

    def record(self, sid=PERCENT, empty=False):
        return copy.deepcopy(next(r for r in self.verified["records"] if
            r["task"]["identity"]["stream_id"] == sid and bool(r["envelope"]["rows"]) != empty))

    def check_synthetic_seal(self, record, mutate=None):
        # This synthetic view changes no immutable archive. It exercises the
        # semantic verifier after the real object's hash-bound read boundary.
        class View:
            pass
        view = View()
        key = record["task_id"]
        view.tasks = {key: record["task"]}
        view.binding = self.execution["binding"]
        view.completed = lambda ignored: record["envelope"]
        prefix = self.sealed/"campaigns"/view.binding["campaign_id"]
        objects = {d["sha256"]: (prefix/d["path"]).read_bytes() for a in record["receipts"] for d in a["objects"]}
        view.read_object = lambda d: objects[d["sha256"]]
        state = dict(intervals={key: dict(state=record["seal"]["state"], complete=record["seal"], runs=record["seal"]["run"])},
                     attempts={a["attempt_key"]:a for a in record["receipts"]})
        if mutate:
            mutate(view, state, objects)
        return handoff._seal(view, key, state, self.inventory, self.fingerprint)

    def test_sealed_percent_real_daily_accounting(self):
        rows = self.daily["rows"][PERCENT]
        self.assertEqual(len(rows), 15)
        self.assertEqual(sum(r["plot_eligible"] for r in rows), 15)
        self.assertTrue(all(r["n_valid"] == r["n_total"] == r["expected_samples"] == 144 for r in rows))

    def test_sealed_vwc_real_daily_accounting(self):
        rows = self.daily["rows"][VWC]
        self.assertEqual(len(rows), 15)
        self.assertTrue(all(r["plot_eligible"] and r["presentation_eligible"] for r in rows))

    def test_exact_percent_identity_route(self):
        self.assertTrue(all(r["mean_native"] == r["mean_percent"] for r in self.daily["rows"][PERCENT]))

    def test_vwc_times_100_route(self):
        for row in self.daily["rows"][VWC]:
            self.assertAlmostEqual(row["mean_percent"], row["mean_native"]*100, places=13)

    def test_completed_fixed_pst_day_grouping_and_cadence(self):
        for sid in (PERCENT, VWC):
            rows = self.daily["rows"][sid]
            self.assertEqual(rows[0]["date"], "2024-02-15")
            self.assertEqual(rows[-1]["date"], "2024-02-29")
            self.assertTrue(all(r["cadence_seconds"] == 600 and r["cadence_source"] == "observed_day_mode" for r in rows))
            self.assertTrue(all(r["coverage_fraction"] == 1 and r["temporal_span_fraction"] > .99 for r in rows))

    def test_leap_day_true_dowy_and_aligned_x(self):
        for sid in (PERCENT, VWC):
            row = self.daily["rows"][sid][-1]
            self.assertEqual((row["date"], row["water_year"], row["dowy"], row["water_day_aligned"], row["water_year_days"]),
                             ("2024-02-29", 2024, 152, 152, 366))

    def test_science_policy_and_provenance_survive(self):
        self.assertEqual(self.daily["numerical_policy"], "dendra-daily-1.0.0-frozen-cadence")
        self.assertTrue(all(r["query_complete"] and len(r["source_intervals"]) == 1 for r in self.daily["rows"][PERCENT]))
        self.assertEqual(self.daily["science_binding"]["core_sha256"], self.verified["acquisition_checkpoint"]["sources"]["core.R"])

    def test_covered_empty_remains_distinct_without_synthetic_rows(self):
        r = self.record(DIMENSIONLESS, empty=True)
        self.assertEqual(r["envelope"]["rows"], [])
        self.check_synthetic_seal(r)
        stream = next(s for s in self.document["streams"] if s["identity"]["stream_id"] == DIMENSIONLESS)
        self.assertEqual([i["query_state"] for i in stream["intervals"]], ["COMPLETE_NONEMPTY", "COVERED_EMPTY"])
        self.assertEqual(self.daily["summaries"][DIMENSIONLESS]["native_rows"], 52)

    def test_dimensionless_never_gets_percent_daily(self):
        self.assertNotIn(DIMENSIONLESS, self.daily["rows"])
        self.assertEqual(self.daily["summaries"][DIMENSIONLESS]["normalized_percent_status"], "NORMALIZED_PERCENT_HOLD")

    def test_historical_terminal_is_not_latest(self):
        self.assertEqual(self.document["latest_instantaneous"], [])
        for stream in self.document["streams"]:
            point = stream["historical_terminal"]
            self.assertEqual(point["representation"], "historical_terminal")
            self.assertFalse(point["latest_witness"])
            self.assertFalse(point["current_state_claim"])

    def test_stale_timestamp_and_day_are_not_moved(self):
        point = next(s for s in self.document["streams"] if s["identity"]["stream_id"] == PERCENT)["historical_terminal"]
        self.assertEqual(point["source_timestamp"], "2024-03-01T07:50:00.000Z")
        self.assertEqual(point["source_fixed_pst_date"], "2024-02-29")
        self.assertGreater(point["age_seconds"], 86400)

    def test_dimensionless_terminal_percent_withheld(self):
        point = next(s for s in self.document["streams"] if s["identity"]["stream_id"] == DIMENSIONLESS)["historical_terminal"]
        self.assertIsNone(point["normalized_percent"])
        self.assertIsNotNone(point["native_value"])

    def test_fixed_pst_does_not_follow_dst(self):
        r = self.record()
        row = dict(t="2024-07-01T07:30:00.000Z", v=0, value_status="number")
        point = handoff.historical_terminal(r["task"]["identity"], [row], r["decision"]["scale"], as_of=AS_OF)
        self.assertEqual(point["source_fixed_pst_date"], "2024-06-30")
        self.assertEqual(point["normalized_percent"], 0)

    def test_finite_native_overflow_is_withheld_from_point_only(self):
        r = self.record(VWC)
        point = handoff.historical_terminal(r["task"]["identity"],
            [dict(t="2024-02-15T08:00:00Z",v=1e308,value_status="number")],r["decision"]["scale"],as_of=AS_OF)
        self.assertEqual(point["native_value"], 1e308)
        self.assertIsNone(point["normalized_percent"])
        self.assertEqual(point["normalized_status"], "nonfinite_conversion_withheld")

    def test_frozen_context_is_explicit_initialization(self):
        self.assertEqual(self.document["cadence_mode"], "initialize")
        self.assertEqual(self.daily["cadence_contexts"][PERCENT]["source"], "observed_stream_mode")
        self.assertEqual(self.daily["cadence_contexts"][PERCENT]["seconds"], 600)
        self.assertEqual(self.daily["cadence_contexts"][PERCENT]["version"], "frozen-cadence-context-1")

    def test_reuse_context_mode_refuses_without_initializing(self):
        with self.assertRaises(Hold):
            handoff.prepare_product(self.sealed,manifest_sha256=self.pin,inventory=self.inventory,
                acquisition_fingerprint=self.fingerprint,output_root=self.root/"not-created",as_of=AS_OF,cadence_mode="reuse")
        self.assertFalse((self.root/"not-created").exists())

    def test_configured_fallback_only_from_matching_reviewed_claims(self):
        one = dict(configured_cadence_claim=dict(validation="VALID_LOCAL_CADENCE_CLAIM",unit="millisecond",value=600000))
        two = copy.deepcopy(one)
        self.assertEqual(handoff.configured_cadence([one,two]),600)
        two["configured_cadence_claim"]["value"] = 3600000
        self.assertIsNone(handoff.configured_cadence([one,two]))
        self.assertIsNone(handoff.configured_cadence([dict(configured_cadence_claim=dict(validation="ABSENT_UNSPECIFIED"))]))

    def test_csv_zero_missing_null_invalid_and_conflict(self):
        r = self.record()
        rows = [dict(t="2024-02-15T08:00:00.000Z",v=0,value_status="number"),
                dict(t="2024-02-15T08:10:00.000Z",value_status="missing"),
                dict(t="2024-02-15T08:20:00.000Z",v=None,value_status="null"),
                dict(t="2024-02-15T08:30:00.000Z",v="bad",value_status="invalid"),
                dict(t="2024-02-15T08:40:00.000Z",v=2,value_status="number",duplicate_conflict=True,
                     conflicting_values=[dict(value_status="number",v=2),dict(value_status="number",v=101)])]
        data = list(csv.DictReader(io.StringIO(handoff.csv_bytes(rows,r["task"]["identity"],r["decision"]["scale"]).decode())))
        self.assertEqual(data[0]["v"], "0")
        self.assertEqual([d["value_status"] for d in data], ["number","missing","null","invalid","number"])
        self.assertEqual(data[-1]["duplicate_conflict"], "True")
        self.assertEqual(data[-1]["alternative_out_of_range"], "True")

    def test_unsealed_and_held_refuse(self):
        for status in ("unqueried", "held", "incomplete"):
            with self.subTest(status=status), self.assertRaises(Hold):
                self.check_synthetic_seal(self.record(), lambda v,s,o:s["intervals"][next(iter(s["intervals"]))].update(state=status))

    def test_altered_parsed_content_refuses(self):
        r = self.record(); r["envelope"]["rows"][0]["v"] = 1
        with self.assertRaises(Hold): self.check_synthetic_seal(r)

    def test_altered_page_hash_refuses(self):
        r = self.record(); r["envelope"]["pages"][0]["response_sha256"] = "f"*64
        with self.assertRaises(Hold): self.check_synthetic_seal(r)

    def test_incomplete_pagination_refuses(self):
        r = self.record(); r["envelope"]["query_complete"] = False
        with self.assertRaises(Hold): self.check_synthetic_seal(r)

    def test_wrong_stream_refuses(self):
        r = self.record(); r["envelope"]["datastream_id"] = VWC
        with self.assertRaises(Hold): self.check_synthetic_seal(r)

    def test_wrong_source_fingerprint_refuses(self):
        with self.assertRaises(Hold):
            handoff._binding(self.execution["binding"], self.execution["tasks"], self.inventory, "f"*64)

    def test_scientific_identity_conflict_refuses(self):
        tasks = copy.deepcopy(self.execution["tasks"])
        tasks[next(iter(tasks))]["identity"]["depth_cm"] = 777
        with self.assertRaises(Hold): handoff._binding(self.execution["binding"],tasks,self.inventory,self.fingerprint)

    def test_decision_inventory_mismatch_refuses(self):
        b = copy.deepcopy(self.execution["binding"]); b["inventory_sha256"] = "f"*64
        with self.assertRaises(Hold): handoff._binding(b,self.execution["tasks"],self.inventory,self.fingerprint)

    def test_out_of_bounds_raw_observation_refuses(self):
        def change(v,s,objects):
            key = next(iter(objects)); raw = decode(objects[key]); raw["data"][0]["t"] = "2020-01-01T00:00:00.000Z"
            objects[key] = encode(raw)
        with self.assertRaises(Hold): self.check_synthetic_seal(self.record(),change)

    def test_submicrosecond_precision_refuses(self):
        r = self.record()
        with self.assertRaises(ValueError):
            handoff.csv_bytes([dict(t="2024-02-15T08:00:00.0000001Z",v=0,value_status="number")],r["task"]["identity"],r["decision"]["scale"])

    def test_integer_precision_refuses(self):
        r = self.record()
        with self.assertRaises(Hold):
            handoff.csv_bytes([dict(t="2024-02-15T08:00:00Z",v=2**53+1,value_status="number")],r["task"]["identity"],r["decision"]["scale"])

    def test_manifest_hash_refuses_before_output(self):
        with self.assertRaises(Hold):
            handoff.verify_sealed(self.sealed,manifest_sha256="f"*64,inventory=self.inventory,acquisition_fingerprint=self.fingerprint)

    def test_overlapping_seals_refuse_before_output(self):
        verified = copy.deepcopy(self.verified)
        verified["records"].append(copy.deepcopy(verified["records"][0]))
        output = self.root/"overlap-refused"
        with patch.object(handoff,"verify_sealed",return_value=verified), self.assertRaises(Hold):
            handoff.prepare_product(self.sealed,manifest_sha256=self.pin,inventory=self.inventory,
                acquisition_fingerprint=self.fingerprint,output_root=output,as_of=AS_OF,cadence_mode="initialize")
        self.assertFalse(output.exists())

    def test_missing_raw_and_parsed_objects_refuse_before_output(self):
        record = self.record()
        prefix = "campaigns/"+self.execution["binding"]["campaign_id"]+"/"
        original = handoff.Root.read
        for kind, name in (("raw",prefix+record["receipts"][0]["objects"][0]["path"]),
                           ("parsed",prefix+record["seal"]["objects"][0]["path"])):
            output = self.root/(kind+"-missing-refused")
            def read(root, path, limit):
                if path == name:
                    raise FileNotFoundError("synthetic missing sealed object")
                return original(root,path,limit)
            with self.subTest(kind=kind), patch.object(handoff.Root,"read",read), self.assertRaises(FileNotFoundError):
                handoff.prepare_product(self.sealed,manifest_sha256=self.pin,inventory=self.inventory,
                    acquisition_fingerprint=self.fingerprint,output_root=output,as_of=AS_OF,cadence_mode="initialize")
            self.assertFalse(output.exists())

    def test_output_reuse_refuses(self):
        with self.assertRaises(FileExistsError):
            handoff.prepare_product(self.sealed,manifest_sha256=self.pin,inventory=self.inventory,
                acquisition_fingerprint=self.fingerprint,output_root=self.output,as_of=AS_OF,cadence_mode="initialize")

    def test_no_provider_activity(self):
        self.assertEqual(NETWORK_ATTEMPTS, [])
        self.assertEqual(SLEEP_ATTEMPTS, [])
        self.assertEqual(self.result["provider_requests"], 0)
        self.assertFalse(self.result["publication_eligible"])

    def test_cli_preparation_success(self):
        self.assertEqual(decode((self.root/"cli-receipt.json").read_bytes())["exit_code"],0)
        self.assertEqual(self.result["outcome"],"OFFLINE_DAILY_PREPARED")


if __name__ == "__main__":
    unittest.main()
