"""Offline bridge proof with real synthetic Journal seals, Parquet and R science.

Run via unittest discovery in tests/dendra with DENDRA_INVENTORY and a fresh
DENDRA_TEST_ROOT. Existing fixture helpers install socket/DNS/sleep denial.
"""
import copy
from datetime import timedelta
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_campaign_integration as c
import test_candidate3_delivery as cd
import test_routine_update as rt
from dendra.history_acquisition import candidate3_routine as bridge
from dendra.history_acquisition import candidate3_delivery as delivery, routine_update as r
from dendra.history_acquisition.safety import Hold, decode, encode


def setUpModule():
    c.setUpModule()


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="c3-routine-", dir=c.TEST_ROOT))
        cls.intervals = {sid: dict(start=rt.START, end="2024-02-27T08:00:00.000Z")
                         for sid in (c.VWC, c.PERCENT)}
        cls.seed = rt.seed(cls.root, {sid: (w["start"], w["end"]) for sid, w in cls.intervals.items()})
        g = r.read(cls.seed)
        _, daily = r.prepared(g["daily"], c.INVENTORY)
        cls.dim = next(sid for sid, i in c.INVENTORY.roster().items() if i["native_unit"] == "Dimensionless")
        # Reuse the delivery fixture's actual CSV/Parquet writer and validator.
        cd.DeliveryTests.setUpClass()
        def rows(target):
            target.clear()
            for sid in (c.VWC, c.PERCENT, cls.dim):
                identity = c.INVENTORY.identity(sid)
                for n in range(16, 29) if sid != cls.dim else [19]:
                    state = {16: delivery.STATES[1], 17: delivery.STATES[2], 18: delivery.STATES[3]}.get(n, "ACCEPTED")
                    if sid == cls.dim:
                        state = delivery.STATES[3]
                    row = cd.record(1, f"2024-02-{n}", unit=identity["native_unit"], status=state,
                                    triplet=delivery.TRIPLETS[1], value=.25 if sid != c.PERCENT else 25)
                    row.update({k: identity[k] for k in ("station_id", "stream_id", "depth_cm", "native_unit")})
                    row["depth_status"] = "UNKNOWN" if identity["depth_cm"] is None else "ACCEPTED_EXACT_STREAM_REVIEW"
                    row["export_local_series_key"] = "fixture:" + sid
                    if state == "ACCEPTED":
                        template = daily["rows"][sid][0]
                        for k in bridge.MATERIAL:
                            if k not in ("date", "water_year", "dowy", "water_day", "water_year_days", "water_day_aligned"):
                                row[k] = copy.deepcopy(template[k])
                        row["daily_mean_vwc_percent"] = row["mean_percent"]
                    target.append(row)
        source, expectations = cd.DeliveryTests.fixture(rows)
        cls.export = cls.root / "bulk"
        delivery.export(source, cls.export, reader=cd.DeliveryTests.reader, expectations=expectations,
                        built_at="2026-10-06T00:00:00Z")
        cls.manifest = r.reference(cls.export / "manifest.json")
        cls.candidate_pin = expectations.pins[delivery.MANIFEST]
        cls.registered = bridge.register(cls.manifest, candidate_manifest_sha256=cls.candidate_pin,
                                         output_root=cls.root / "registered")
        cls.base = bridge.bootstrap(cls.registered, cls.seed, cls.intervals, inventory=c.INVENTORY,
                                    output_root=cls.root / "bootstrapped")
        cls.results = {}
        for kind in ("good", "correction", "quality", "empty", "zero", "unqueried", "invalid", "failed", "new-day"):
            target = rt.TARGET if kind == "new-day" else rt.END
            cycle = r.plan(cls.seed, inventory=c.INVENTORY, target=target, as_of=rt.ASOF,
                           selected_ids=[c.VWC, c.PERCENT], output_root=cls.root / (kind + "-cycle"))
            work = r.read(cycle)["work"][c.VWC]
            replacements, attempts = {}, {}
            if kind not in ("unqueried", "invalid", "failed"):
                original = rt.samples
                def samples(sid, lo, hi, sample_kind="good"):
                    data = original(sid, lo, hi, sample_kind)
                    if kind == "correction":
                        for row in data:
                            if "2024-02-28T08" <= row["t"] < "2024-02-29T08":
                                row["v"] = .30
                    return data
                with patch.object(rt, "samples", samples):
                    replacements[c.VWC] = rt.seal_set(cls.root / (kind + "-source"),
                        {c.VWC: (work["start"], work["end"])}, kind if kind in ("quality", "empty", "zero") else "good")
            elif kind == "invalid":
                replacements[c.VWC] = dict(cls.seed, sha256="0" * 64)
            elif kind == "failed":
                attempts[c.VWC] = [cls.failed_attempt(cls.root / "failure-journal", work)]
            gen = r.assemble(cycle, replacements, inventory=c.INVENTORY, attempt_refs=attempts,
                             output_root=cls.root / (kind + "-generation"))
            prepared = r.prepare(gen, inventory=c.INVENTORY, as_of=rt.ASOF,
                                 output_root=cls.root / (kind + "-daily"))
            result = bridge.reconcile(cls.base, gen, prepared, inventory=c.INVENTORY,
                                      output_root=cls.root / (kind + "-bridge"))
            cls.results[kind] = (result, cycle, gen, prepared)

    @classmethod
    def failed_attempt(cls, root, work):
        root.mkdir()
        bundle = c.bundle(c.VWC, start=rt.START, end="2024-05-01T08:00:00.000Z")
        manifest = c.campaign.make_campaign(c.INVENTORY, campaign_id="bridge-failure",
            executor_fingerprint=c.FINGERPRINT, horizons={c.VWC: {k: work[k] for k in ("start", "end")}},
            chunk_days=30, decisions={c.VWC: bundle["decision"]},
            budgets=c.campaign.policy(logical_requests=3, attempts=3, total_bytes=25165824, wall_seconds=600))
        binding, tasks = c.campaign_execution.prepare(manifest, c.INVENTORY, {c.VWC: bundle}, now=c.NOW)
        clock = c.Clock()
        with c.Journal(root, binding, tasks, create=True, inventory=c.INVENTORY,
                       now=clock.now, monotonic=clock.monotonic) as journal:
            fake = c.FiniteExecutor(journal, clock, [503])
            try:
                c.provider_adapter.CampaignAdapter(journal).run(executor=fake, wait=fake.wait)
            except (Hold, c.campaign_execution.Stop):
                pass
            assert len(fake.calls) == 1
            return dict(root=str(root), campaign_id=binding["campaign_id"], header_sha256=journal.header_sha,
                        task_id=next(iter(tasks)))

    def view(self, kind, sid=c.VWC):
        state, baseline, g, daily = bridge.load(self.results[kind][0], inventory=c.INVENTORY)
        return state, baseline, bridge.history(baseline, g, daily, sid)

    def test_baseline_has_no_api_frontier_or_cutover_and_all_states(self):
        state, baseline, g, daily = bridge.load(self.registered, inventory=c.INVENTORY)
        self.assertIsNone(g)
        self.assertIsNone(state["bootstrap"])
        self.assertEqual(baseline.audit(), state["census"])
        self.assertEqual(set(state["census"]["states"]), set(delivery.STATES))
        for entry in bridge.status(state, baseline, g, inventory=c.INVENTORY).values():
            self.assertIsNone(entry["api_coverage"])
        for sid in baseline.streams:
            self.assertEqual(bridge.history(baseline, g, daily, sid), baseline.history(sid))

    def test_explicit_bootstrap_only_and_no_backdated_bulk_coverage(self):
        state, baseline, g, daily = bridge.load(self.base, inventory=c.INVENTORY)
        self.assertEqual(g["streams"][c.VWC]["start"], rt.START)
        self.assertEqual(state["bootstrap"]["intervals"], self.intervals)
        rows = bridge.history(baseline, g, daily, c.VWC)
        self.assertEqual(rows["2024-02-19"]["authority"], "CANDIDATE3_BULK")
        self.assertEqual(rows["2024-02-20"]["authority"], "SEALED_API")
        self.assertEqual(rows["2024-02-28"]["authority"], "CANDIDATE3_BULK")
        for intervals in ({}, {c.VWC: self.intervals[c.VWC]},
                          dict(self.intervals, **{c.VWC: dict(start="2024-02-19T08:00:00.000Z", end=self.intervals[c.VWC]["end"])})):
            with self.assertRaises(Hold):
                bridge.bootstrap(self.registered, self.seed, intervals, inventory=c.INVENTORY,
                                 output_root=self.root / "refused-bootstrap")

    def test_new_completed_day_and_untouched_bulk(self):
        state, baseline, rows = self.view("new-day")
        original = baseline.history(c.VWC)
        self.assertEqual(state["changes"][c.VWC]["material_changed_dates"], ["2024-02-29"])
        self.assertEqual(rows["2024-02-29"]["record"]["water_day_aligned"], 152)
        self.assertNotIn("2024-03-01", rows)
        for day in original:
            if day < "2024-02-20":
                self.assertEqual(rows[day], original[day])
        self.assertFalse(state["publication_eligible"])

    def test_overlap_unchanged_and_no_op_source_check_are_distinct(self):
        state, baseline, rows = self.view("good")
        old = baseline.history(c.VWC)["2024-02-28"]
        new = rows["2024-02-28"]
        self.assertEqual(len(rows), len(baseline.history(c.VWC)))
        self.assertEqual(new["record"]["mean_percent"], old["record"]["mean_percent"])
        self.assertEqual(new["bulk_evidence"], old["bulk_evidence"])
        self.assertEqual(new["authority"], "SEALED_API")
        self.assertEqual(state["changes"][c.VWC]["material_changed_dates"], [])
        self.assertEqual(state["changes"][c.VWC]["outcome"], "STREAM_UPDATE_SUCCESS")
        self.assertIsNotNone(state["changes"][c.VWC]["last_attempted_source_check"])

    def test_late_correction_only_one_material_day(self):
        state, baseline, rows = self.view("correction")
        self.assertEqual(state["changes"][c.VWC]["material_changed_dates"], ["2024-02-28"])
        self.assertEqual(rows["2024-02-28"]["record"]["mean_percent"], 30)
        self.assertEqual(rows["2024-02-28"]["bulk_evidence"], baseline.history(c.VWC)["2024-02-28"]["bulk_evidence"])
        self.assertTrue(all(d >= "2024-02-20" for d in state["changes"][c.VWC]["routine_recomputed_dates"]))

    def test_quality_veto_explicit_and_original_traceable(self):
        state, baseline, rows = self.view("quality")
        row = rows["2024-02-28"]
        self.assertEqual(row["state"], "DAILY_VALUE_WITHHELD")
        self.assertIsNone(row["record"]["mean_native"])
        self.assertEqual(row["bulk_evidence"], baseline.history(c.VWC)["2024-02-28"]["bulk_evidence"])
        self.assertEqual(baseline.history(c.VWC)["2024-02-28"]["record"]["mean_percent"], 25)
        self.assertEqual(state["changes"][c.VWC]["material_changed_dates"], ["2024-02-28"])

    def test_valid_empty_not_zero_and_loss_safeguard_retained(self):
        state, baseline, rows = self.view("empty")
        self.assertTrue(state["changes"][c.VWC]["coverage"]["queried_empty"])
        self.assertIsNone(rows["2024-02-28"]["record"]["mean_native"])
        self.assertTrue(rows["2024-02-28"]["record"]["query_complete"])
        self.assertEqual(rows["2024-02-19"], baseline.history(c.VWC)["2024-02-19"])
        daily = r.prepared(self.results["empty"][3], c.INVENTORY)[1]
        self.assertTrue(daily["routine_publication_loss_assessment"][c.VWC]["hold"])
        self.assertFalse(state["publication_eligible"])

    def test_failed_and_invalid_replacement_retain_bulk_and_prior_api(self):
        _, baseline, prior, daily = bridge.load(self.base, inventory=c.INVENTORY)
        expected = bridge.history(baseline, prior, daily, c.VWC)
        for kind in ("failed", "invalid"):
            state, _, rows = self.view(kind)
            self.assertEqual({d: x["record"] for d, x in rows.items()},
                             {d: x["record"] for d, x in expected.items()})
            self.assertEqual({d: x["bulk_evidence"] for d, x in rows.items()},
                             {d: x["bulk_evidence"] for d, x in expected.items()})
            self.assertEqual(state["changes"][c.VWC]["material_changed_dates"], [])
            self.assertTrue(state["changes"][c.VWC]["coverage"]["gaps"])
            self.assertEqual(state["changes"][c.VWC]["coverage"]["queried_empty"], [])
        failed = self.view("failed")[0]["changes"][c.VWC]
        self.assertIsNotNone(failed["last_attempted_source_check"])
        self.assertEqual(failed["attempt_evidence"]["journals"][0]["spent_attempts"], 1)
        self.assertEqual(self.view("invalid")[0]["changes"][c.VWC]["replacement"]["status"], "REPLACEMENT_NOT_ADMITTED")

    def test_unqueried_no_failure_or_empty_claim_and_partial_success(self):
        state, _, rows = self.view("unqueried")
        change = state["changes"][c.VWC]
        self.assertIsNone(change["attempt_evidence"])
        self.assertIsNone(change["last_attempted_source_check"])
        self.assertIsNone(change["replacement"])
        self.assertTrue(change["coverage"]["gaps"])
        self.assertEqual(rows["2024-02-28"]["authority"], "CANDIDATE3_BULK")
        self.assertEqual(self.view("good")[0]["changes"][c.PERCENT]["material_changed_dates"], [])
        self.assertEqual(self.view("good")[0]["changes"][c.PERCENT]["outcome"], "KEEP_PRIOR_ACKNOWLEDGED_HISTORY")

    def test_zero_unknown_depth_and_dimensionless_boundary(self):
        _, baseline, rows = self.view("zero")
        row = rows["2024-02-28"]
        self.assertEqual(row["record"]["mean_percent"], 0)
        self.assertEqual(row["state"], "ACCEPTED")
        self.assertIsNone(row["record"]["identity"]["depth_cm"])
        original = baseline.history(c.VWC)["2024-02-28"]["record"]
        self.assertEqual(original["depth_status"], "UNKNOWN")
        state, baseline, g, daily = bridge.load(self.base, inventory=c.INVENTORY)
        self.assertEqual(bridge.status(state, baseline, g, inventory=c.INVENTORY)[self.dim]["api_status"], "DIMENSIONLESS_OUTSIDE_NUMERIC_UPDATE_ROUTE")
        self.assertEqual(bridge.history(baseline, g, daily, self.dim), baseline.history(self.dim))
        with self.assertRaises(Hold):
            bridge._identity(baseline, self.dim, c.INVENTORY.identity(self.dim))

    def test_restart_next_cycle_and_no_completed_work_reacquisition(self):
        ref, cycle, gen, daily = self.results["good"]
        state, _, g, _ = bridge.load(ref, inventory=c.INVENTORY)
        self.assertEqual(r.resume(cycle, c.INVENTORY)[0], r.read(cycle))
        follow = r.plan(state["api"], inventory=c.INVENTORY, target=rt.TARGET, as_of=rt.ASOF,
                        selected_ids=[c.VWC], output_root=self.root / "follow-cycle")
        self.assertEqual(r.read(follow)["policy"], r.policy())
        self.assertEqual(r.read(follow)["work"][c.VWC]["start"], "2024-02-22T08:00:00.000Z")
        with self.assertRaises(Hold):
            bridge.reconcile(ref, gen, daily, inventory=c.INVENTORY, output_root=self.root / "stale-cycle")
        self.assertIsNone(g["streams"][c.VWC]["latest_eligible_instantaneous_timestamp"])
        self.assertIsNone(g["streams"][c.VWC]["last_acknowledged_publication_receipt"])

    def test_identity_cutoff_and_preserved_output_guards(self):
        _, baseline, _, _ = bridge.load(self.base, inventory=c.INVENTORY)
        with self.assertRaises(Hold):
            bridge._identity(baseline, c.VWC, dict(c.INVENTORY.identity(c.VWC), depth_cm=10))
        with self.assertRaises(Hold):
            r.plan(self.seed, inventory=c.INVENTORY, target="2024-03-01T09:00:00.000Z", as_of=rt.ASOF,
                   selected_ids=[c.VWC], output_root=self.root / "unfinished")
        with self.assertRaises(Hold):
            bridge.register(self.manifest, candidate_manifest_sha256=self.candidate_pin,
                            output_root=self.export / "forbidden")
        with self.assertRaises(Hold):
            bridge.register(dict(self.manifest, sha256="0" * 64), candidate_manifest_sha256=self.candidate_pin,
                            output_root=self.root / "changed-manifest")

    def test_unmatched_inventory_is_a_hold_without_changing_bulk_identity(self):
        state, baseline, g, _ = bridge.load(self.registered, inventory=c.INVENTORY)
        roster = c.INVENTORY.roster()
        roster.pop(c.PERCENT)
        roster[c.VWC]["depth_cm"] = 20
        statuses = bridge.status(state, baseline, g, inventory=SimpleNamespace(roster=lambda: roster))
        self.assertEqual(statuses[c.PERCENT]["api_status"], "API_INVENTORY_ADMISSION_REQUIRED")
        self.assertEqual(statuses[c.VWC]["api_status"], "BULK_API_IDENTITY_REVIEW_REQUIRED")
        self.assertIsNone(statuses[c.VWC]["api_coverage"])
        self.assertIsNone(baseline.streams[c.VWC]["identity"]["depth_cm"])
        self.assertEqual(baseline.history(c.VWC)["2024-02-28"]["record"]["depth_status"], "UNKNOWN")

    def test_restart_requires_original_bootstrap_pin_and_matching_preparation(self):
        state = r.read(self.base)
        state.pop("bridge_id")
        state["bootstrap"]["seed"]["sha256"] = "0" * 64
        altered = bridge._save(state, self.root / "bad-seed-pointer")
        with self.assertRaises(Hold):
            bridge.load(altered, inventory=c.INVENTORY)
        with self.assertRaises(Hold):
            bridge.reconcile(self.base, self.results["good"][2], self.results["empty"][3],
                             inventory=c.INVENTORY, output_root=self.root / "wrong-preparation")

    @classmethod
    def tearDownClass(cls):
        if c.NETWORK_ATTEMPTS or c.SLEEP_ATTEMPTS or cd.ATTEMPTS:
            raise AssertionError("Forbidden network/sleep activity")
