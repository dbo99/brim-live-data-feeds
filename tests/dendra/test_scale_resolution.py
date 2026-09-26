"""Focused scale policy tests: accepted inventory plus synthetic reviewed claims."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

NETWORK_ATTEMPTS, PROVIDER_ATTEMPTS, SLEEP_ATTEMPTS = [], [], []


def deny_network(event, args):
    if event.startswith("socket."):
        NETWORK_ATTEMPTS.append(event)
        raise AssertionError("Scale policy is offline")


def deny_provider(*args, **kwargs):
    PROVIDER_ATTEMPTS.append(True)
    raise AssertionError("Provider request forbidden")


def deny_sleep(*args, **kwargs):
    SLEEP_ATTEMPTS.append(True)
    raise AssertionError("Real sleep forbidden")


sys.addaudithook(deny_network)
GUARDS = [patch("time.sleep", deny_sleep), patch("urllib.request.urlopen", deny_provider),
          patch("urllib.request.OpenerDirector.open", deny_provider)]
for guard in GUARDS:
    guard.start()
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from dendra.history_acquisition import scale_resolution as scale
from dendra.history_acquisition.model import Inventory, INVENTORY_SHA256, unit_route, metadata_view, campaign, plan
from dendra.history_acquisition.safety import Hold, Root, encode, decode, digest, sha

UNRESOLVED = "5d9272a12da5c3cff0f655ed"
WHOLE = {"kind": "whole_history"}
UNKNOWN = {"kind": "unknown_history"}
ERA1 = {"kind": "interval", "start": "2020-01-01T00:00:00Z", "end": "2021-01-01T00:00:00Z"}
ERA2 = {"kind": "interval", "start": "2021-01-01T00:00:00Z", "end": "2022-01-01T00:00:00Z"}


def setUpModule():
    global INV, BASELINE, TEST_ROOT
    INV = Inventory.load(os.environ["DENDRA_INVENTORY"], INVENTORY_SHA256)
    TEST_ROOT = Path(os.environ["DENDRA_TEST_ROOT"])
    if not TEST_ROOT.is_absolute() or not TEST_ROOT.is_dir():
        raise ValueError("Explicit task-owned test directory required")
    BASELINE = scale.build_state(INV)


def tearDownModule():
    for guard in reversed(GUARDS):
        guard.stop()
    if NETWORK_ATTEMPTS or PROVIDER_ATTEMPTS or SLEEP_ATTEMPTS:
        raise AssertionError("Forbidden network/provider/sleep attempted")


def evidence(*, sid=UNRESOLVED, role="primary", kind=None, conversion="fraction", scope=None,
             source_version="synthetic-v1", ref="synthetic/scale.json", extra_source=None):
    identity = INV.identity(sid)
    applicability = WHOLE if scope is None else scope
    source = encode(dict(synthetic=True, stream_id=sid, scale=conversion,
                         applicability=applicability, extra=extra_source))
    claim = dict(schema_version=scale.EVIDENCE_VERSION, station_id=identity["station_id"], stream_id=sid,
                 role=role, kind=kind or ("datastream_metadata" if role == "primary" else "range_distribution"),
                 scale=conversion, applicability=applicability,
                 source=dict(ref=ref, sha256=sha(source), version=source_version))
    return scale.Evidence.bind(claim, source)


def resolve(*items, scope=None):
    return scale.resolve(INV, UNRESOLVED, items, scope=scope)


class ScaleTests(unittest.TestCase):
    def test_exact_frozen_closure_and_baseline_counts(self):
        self.assertEqual(sha(INV.source), INVENTORY_SHA256)
        self.assertEqual((BASELINE["station_count"], BASELINE["stream_count"]), (122, 434))
        self.assertEqual({d["stream_id"] for d in BASELINE["decisions"]}, set(INV.roster()))
        counts = {}
        for decision in BASELINE["decisions"]:
            counts[decision["resolution_status"]] = counts.get(decision["resolution_status"], 0) + 1
            self.assertEqual(decision["frozen_identity"], INV.identity(decision["stream_id"]))
        self.assertEqual(counts, dict(accepted_resolved_percent=177, accepted_resolved_fraction=160, unresolved=97))
        INV.check_document(decode(INV.source))

    def test_all_337_accepted_multipliers_and_eligibility_preserved(self):
        for decision in BASELINE["decisions"]:
            if decision["native_unit"] == "Dimensionless":
                continue
            with self.subTest(unit=decision["native_unit"]):
                self.assertEqual(decision["conversion_factor"], 1 if decision["native_unit"] == "Percent" else 100)
                self.assertEqual(decision["evidence_status"], "accepted_baseline")
                self.assertTrue(decision["normalized_percent_eligible"])
                self.assertTrue(decision["absolute_percent_product_eligible"])

    def test_all_97_remain_native_eligible_under_synthetic_public_access(self):
        decisions = [d for d in BASELINE["decisions"] if d["native_unit"] == "Dimensionless"]
        self.assertEqual(len(decisions), 97)
        for decision in decisions:
            identity = decision["frozen_identity"]
            self.assertEqual(decision["native_unit_status"], "native_only_scale_unresolved")
            self.assertTrue(decision["acquisition_eligible"] and decision["native_archive_eligible"])
            self.assertFalse(decision["normalized_percent_eligible"] or decision["absolute_percent_product_eligible"])
            self.assertIsNone(decision["conversion_factor"])
            self.assertIsNone(decision["normalized_unit"])
            self.assertIsNone(unit_route(identity)["multiplier"])
            science = dict(parameter="soil_moisture", native_unit="Dimensionless")
            claims = dict(identity, complete=True, access_state="accessible", public_level=3, is_hidden=False,
                          station_public_level=3, station_is_hidden=False, geo_protected=True,
                          scientific_fields=science, scientific_sha256=digest(science), dictionary_sha256="a" * 64)
            view = metadata_view(identity, claims, checked_at="2026-09-26T08:00:00Z", now="2026-09-26T08:00:00Z",
                                 scientific_sha256=digest(science), dictionary_sha256="a" * 64)
            self.assertTrue(view["raw_eligible"])
            self.assertFalse(view["unit"]["percent_product_eligible"])

    def test_authoritative_percent_and_fraction_transitions(self):
        for meaning, factor in (("percent", 1), ("fraction", 100)):
            item = evidence(conversion=meaning)
            result = resolve(item)
            self.assertEqual(result["resolution_status"], "evidence_resolved_" + meaning)
            self.assertEqual(result["conversion_factor"], factor)
            self.assertEqual(result["evidence_status"], "primary_evidence_resolved")
            self.assertEqual(result["frozen_identity"], INV.identity(UNRESOLVED))
            self.assertEqual(result["native_unit_status"], "native_only_scale_unresolved")
            self.assertNotEqual(result["decision_sha256"], resolve()["decision_sha256"])
            self.assertEqual(result["primary_evidence"], [item.reference()])

    def test_every_primary_kind_can_bind_an_explicit_reviewed_scale(self):
        for kind in scale.PRIMARY:
            with self.subTest(kind=kind):
                self.assertEqual(resolve(evidence(kind=kind))["conversion_factor"], 100)

    def test_wrong_stream_or_station_is_rejected(self):
        item = evidence()
        claim = decode(item.claim_bytes)
        other = next(s for s in INV.roster() if s != UNRESOLVED)
        with self.assertRaisesRegex(Hold, "mismatch"):
            scale.resolve(INV, other, [item])
        claim["station_id"] = "a" * 24
        with self.assertRaisesRegex(Hold, "mismatch"):
            resolve(scale.Evidence.bind(claim, item.source_bytes))

    def test_source_byte_hash_mismatch_and_missing_binding_hold(self):
        item = evidence()
        with self.assertRaisesRegex(Hold, "bytes differ"):
            scale.Evidence.bind(decode(item.claim_bytes), item.source_bytes + b" ")
        with self.assertRaises(Hold):
            resolve(item.reference())

    def test_supporting_only_never_resolves_individually_or_combined(self):
        items = [evidence(role="supporting", kind=kind) for kind in sorted(scale.SUPPORTING)]
        for group in ([items[0]], [items[1]], [items[2]], items):
            with self.subTest(count=len(group)):
                result = resolve(*group)
                self.assertEqual(result["resolution_status"], "unresolved")
                self.assertEqual(result["evidence_status"], "insufficient_primary_evidence")
                self.assertIsNone(result["conversion_factor"])

    def test_supporting_cannot_masquerade_as_primary(self):
        for kind in scale.SUPPORTING:
            with self.subTest(kind=kind), self.assertRaisesRegex(Hold, "hierarchy"):
                evidence(role="primary", kind=kind)

    def test_primary_conflict_and_supporting_override_rejected(self):
        first, second = evidence(conversion="percent"), evidence(conversion="fraction")
        result = resolve(first, second, evidence(role="supporting", conversion="percent"))
        self.assertEqual(result["resolution_status"], "unresolved")
        self.assertEqual(result["evidence_status"], "primary_evidence_conflict")
        self.assertEqual(result["unresolved_reason"], "contradictory_primary_evidence")

    def test_ambiguous_or_missing_primary_scale_stays_unresolved(self):
        for meaning in ("ambiguous", None):
            with self.subTest(meaning=meaning):
                result = resolve(evidence(conversion=meaning), evidence(conversion="fraction"))
                self.assertEqual(result["evidence_status"], "primary_evidence_conflict")
                self.assertEqual(result["unresolved_reason"], "ambiguous_primary_evidence")

    def test_order_and_duplicates_do_not_change_decision(self):
        a, b = evidence(), evidence(role="supporting", kind="sister_stream")
        self.assertEqual(encode(resolve(a, b)), encode(resolve(b, a, a)))

    def test_evidence_hash_version_or_reference_change_changes_decision(self):
        original = resolve(evidence())["decision_sha256"]
        for item in (evidence(extra_source="changed"), evidence(source_version="synthetic-v2"),
                     evidence(ref="synthetic/other.json")):
            self.assertNotEqual(resolve(item)["decision_sha256"], original)

    def test_policy_version_change_changes_decision_identity(self):
        item = evidence()
        before = resolve(item)
        with patch.object(scale, "POLICY_VERSION", "synthetic-policy-revision"):
            after = resolve(item)
        self.assertNotEqual(before["decision_sha256"], after["decision_sha256"])
        self.assertEqual(before["frozen_identity_sha256"], after["frozen_identity_sha256"])

    def test_current_metadata_with_unknown_history_never_resolves_por(self):
        item = evidence(scope=UNKNOWN)
        for scope in (WHOLE, ERA1):
            result = resolve(item, scope=scope)
            self.assertEqual(result["resolution_status"], "unresolved")
            self.assertEqual(result["evidence_status"], "temporal_scope_unresolved")
            self.assertEqual(result["segments"][0]["unresolved_reason"], "primary_historical_scope_unknown")

    def test_unknown_temporal_primary_cannot_be_overridden(self):
        self.assertEqual(resolve(evidence(), evidence(scope=UNKNOWN))["resolution_status"], "unresolved")

    def test_interval_evidence_resolves_only_covered_interval(self):
        item = evidence(scope=ERA1)
        self.assertEqual(resolve(item, scope=ERA1)["conversion_factor"], 100)
        self.assertIsNone(resolve(item, scope=ERA2)["conversion_factor"])
        whole = resolve(item)
        self.assertIsNone(whole["conversion_factor"])
        self.assertEqual([s["conversion_factor"] for s in whole["segments"]], [None, 100, None])
        self.assertIsNone(whole["segments"][0]["start"])
        self.assertIsNone(whole["segments"][-1]["end"])

    def test_incompatible_historical_eras_remain_separate(self):
        a, b = evidence(scope=ERA1, conversion="percent"), evidence(scope=ERA2, conversion="fraction")
        scope = dict(kind="interval", start=ERA1["start"], end=ERA2["end"])
        result = resolve(a, b, scope=scope)
        self.assertEqual([s["conversion_factor"] for s in result["segments"]], [1, 100])
        self.assertIsNone(result["conversion_factor"])
        self.assertEqual(result["evidence_status"], "temporal_scope_unresolved")

    def test_same_scale_adjacent_eras_cover_bounded_query_but_not_whole_history(self):
        a, b = evidence(scope=ERA1), evidence(scope=ERA2)
        scope = dict(kind="interval", start=ERA1["start"], end=ERA2["end"])
        self.assertEqual(resolve(a, b, scope=scope)["conversion_factor"], 100)
        self.assertIsNone(resolve(a, b)["conversion_factor"])

    def test_gaps_are_explicit_unresolved_segments(self):
        later = dict(kind="interval", start="2022-01-01T00:00:00Z", end="2023-01-01T00:00:00Z")
        result = resolve(evidence(scope=ERA1), evidence(scope=later),
                         scope=dict(kind="interval", start=ERA1["start"], end=later["end"]))
        self.assertEqual([s["conversion_factor"] for s in result["segments"]], [100, None, 100])

    def test_bounded_conflict_blocks_whole_history_without_poisoning_disjoint_era(self):
        items = (evidence(), evidence(scope=ERA1, conversion="percent"))
        self.assertEqual(resolve(*items)["evidence_status"], "primary_evidence_conflict")
        self.assertEqual(resolve(*items, scope=ERA2)["conversion_factor"], 100)

    def test_overlapping_primary_conflict_has_exact_half_open_boundaries(self):
        overlap = dict(kind="interval", start="2020-07-01T00:00:00Z", end="2021-07-01T00:00:00Z")
        result = resolve(evidence(scope=ERA1), evidence(scope=overlap, conversion="percent"),
                         scope=dict(kind="interval", start=ERA1["start"], end=overlap["end"]))
        self.assertEqual([s["conversion_factor"] for s in result["segments"]], [100, None, 1])
        self.assertEqual(result["segments"][1]["start"], "2020-07-01T00:00:00.000Z")
        self.assertEqual(result["segments"][1]["end"], "2021-01-01T00:00:00.000Z")

    def test_scope_validation_and_utc_precision(self):
        for scope in ({"kind": "current"}, {"kind": "whole_history", "start": ERA1["start"]},
                      dict(ERA1, end=ERA1["start"]), dict(ERA1, start="2021-02-30T00:00:00Z"),
                      dict(ERA1, start="2020-01-01T00:00:00.0000001Z")):
            with self.subTest(scope=scope), self.assertRaises(Hold):
                evidence(scope=scope)
        tiny = dict(kind="interval", start="2020-01-01T00:00:00.000001Z", end="2020-01-01T00:00:00.001Z")
        result = resolve(evidence(scope=tiny), scope=tiny)
        self.assertEqual(result["segments"][0]["start"], tiny["start"])

    def test_baseline_primary_changes_require_another_review(self):
        sid = next(k for k, i in INV.roster().items() if i["native_unit"] == "Percent")
        with self.assertRaisesRegex(Hold, "separate review"):
            scale.resolve(INV, sid, [evidence(sid=sid)])
        self.assertEqual(scale.resolve(INV, sid)["conversion_factor"], 1)

    def test_state_roundtrip_preserves_refs_eligibility_and_unresolved_reasons(self):
        item = evidence(scope=ERA1)
        state = scale.build_state(INV, [item])
        restored = scale.restore_state(encode(state), INV, {sha(item.source_bytes): item.source_bytes})
        self.assertEqual(encode(restored), encode(state))
        result = next(d for d in restored["decisions"] if d["stream_id"] == UNRESOLVED)
        self.assertEqual(result["primary_evidence"], [item.reference()])
        self.assertEqual([s["conversion_factor"] for s in result["segments"]], [None, 100, None])
        self.assertFalse(result["absolute_percent_product_eligible"])
        self.assertEqual(result["unresolved_reason"], "mixed_or_unsupported_historical_scope")

    def test_immutable_saved_states_preserve_prior_decision_and_native_receipt(self):
        root = Path(tempfile.mkdtemp(prefix="scale-state-", dir=TEST_ROOT))
        item = evidence()
        new = scale.build_state(INV, [item])
        # Synthetic native receipt remains independent; no observation replay.
        native = encode(dict(identity=INV.identity(UNRESOLVED), native_body_sha256=sha(b"synthetic native history")))
        with Root(root) as files:
            files.write_new("native-receipt.json", native, 4096)
            for state in (BASELINE, new):
                name = "decisions/" + state["state_sha256"] + ".json"
                files.write_new(name, encode(state), scale.STATE_BYTES)
                restored = scale.restore_state(files.read(name, scale.STATE_BYTES), INV,
                                               {sha(item.source_bytes): item.source_bytes})
                self.assertEqual(restored, state)
            self.assertEqual(files.read("native-receipt.json", 4096), native)
            self.assertEqual(len(files.list("decisions")), 2)

    def test_derived_change_does_not_change_native_plan_identity(self):
        def native_plan():
            binding = campaign(INV, campaign_id="synthetic-scale", selected_ids=[UNRESOLVED],
                               as_of="2026-09-26T08:00:00Z", horizons={UNRESOLVED: dict(start="2024-02-29T08:00:00Z", end="2024-03-01T08:00:00Z")},
                               metadata_bindings={UNRESOLVED: "a" * 64}, dictionary_sha256="b" * 64,
                               budgets=dict(logical_requests=0, attempts=0, response_bytes=0, source_rows=0,
                                            intervals=0, elapsed_ms=0, sessions=0), request_generation="synthetic")
            return encode(plan(binding, [(UNRESOLVED, "2024-02-29T08:00:00Z", "2024-03-01T08:00:00Z")]))
        before = native_plan()
        resolve(evidence())
        self.assertEqual(native_plan(), before)
        self.assertIsNone(unit_route(INV.identity(UNRESOLVED))["multiplier"])

    def test_state_rehash_tampering_and_missing_source_are_rejected(self):
        item = evidence()
        state = scale.build_state(INV, [item])
        with self.assertRaisesRegex(Hold, "unavailable"):
            scale.restore_state(encode(state), INV, {})
        with self.assertRaisesRegex(Hold, "bytes differ"):
            scale.restore_state(encode(state), INV, {sha(item.source_bytes): b"different"})
        bad = copy.deepcopy(state)
        record = next(d for d in bad["decisions"] if d["stream_id"] == UNRESOLVED)
        record["conversion_factor"] = 1
        record["decision_sha256"] = digest({k: v for k, v in record.items() if k != "decision_sha256"})
        bad["state_sha256"] = digest({k: v for k, v in bad.items() if k != "state_sha256"})
        with self.assertRaisesRegex(Hold, "differs"):
            scale.restore_state(encode(bad), INV, {sha(item.source_bytes): item.source_bytes})

    def test_state_closure_versions_and_noncanonical_bytes_rejected(self):
        for field, value in (("decisions", BASELINE["decisions"][:-1]), ("schema_version", "other"),
                             ("policy_version", "other"), ("frozen_inventory_sha256", "0" * 64)):
            bad = copy.deepcopy(BASELINE)
            bad[field] = value
            with self.subTest(field=field), self.assertRaises(Hold):
                scale.restore_state(encode(bad), INV, {})
        with self.assertRaises(Hold):
            scale.restore_state(encode(BASELINE) + b" ", INV, {})

    def test_evidence_bounds_and_extra_unrestricted_fields_rejected(self):
        item = evidence()
        with self.assertRaises(Hold):
            resolve(*([item] * (scale.MAX_EVIDENCE + 1)))
        bad = decode(item.claim_bytes)
        bad["unrestricted"] = "not retained"
        with self.assertRaises(Hold):
            scale.Evidence.bind(bad, item.source_bytes)
        for ref in ("/private/source", "../source", "https://example.invalid/?token=value"):
            with self.subTest(ref=ref), self.assertRaises(Hold):
                evidence(ref=ref)

    def test_decision_has_no_source_values_or_numeric_confidence(self):
        marker = "synthetic-source-only-text"
        result = resolve(evidence(extra_source=marker))
        self.assertNotIn(marker.encode(), encode(result))
        self.assertNotIn(b"confidence", encode(result))

    def test_scale_resolution_does_not_mutate_or_bypass_metadata_admission(self):
        from dendra.history_acquisition import provider_metadata as metadata

        # Historical parser identity stays in immutable gate evidence. Optional-Z
        # behavior belongs to the dedicated metadata and adapter regression tests.
        parser_path = Path(metadata.__file__)
        parser_before = parser_path.read_bytes()
        public, scientific = metadata._public, metadata._scientific
        native = dict(public_level=3, is_hidden=False,
                      terms={"dt": {"Unit": "Dimensionless"}}, attributes={},
                      datapoints_config=[dict(interval=60000)])
        native_before = encode(native)
        admitted_before = (public(native), scientific(native))
        claim = decode(evidence().claim_bytes)
        claim["source"]["sha256"] = sha(native_before)
        reviewed = scale.Evidence.bind(claim, native_before)

        def assert_admission():
            self.assertIs(metadata._public, public)
            self.assertIs(metadata._scientific, scientific)
            self.assertEqual((metadata._public(native), metadata._scientific(native)), admitted_before)
            for update in (dict(public_level=0), dict(is_hidden=True)):
                with self.subTest(access=update), self.assertRaisesRegex(Hold, "public metadata"):
                    metadata._public(dict(native, **update))
            for update in (dict(terms=None), dict(attributes=[])):
                with self.subTest(science=update), self.assertRaisesRegex(Hold, "Scientific metadata shape"):
                    metadata._scientific(dict(native, **update))

        assert_admission()
        for items in ((), (reviewed,)):
            decision = resolve(*items)
            self.assertEqual(decision["eligibility_scope"],
                             "scale_only_subject_to_access_and_daily_acceptance")
            assert_admission()
        self.assertEqual(encode(native), native_before)
        self.assertEqual(reviewed.source_bytes, native_before)
        self.assertEqual(parser_path.read_bytes(), parser_before)

    def test_no_provider_network_or_sleep_attempted(self):
        self.assertEqual((NETWORK_ATTEMPTS, PROVIDER_ATTEMPTS, SLEEP_ATTEMPTS), ([], [], []))


if __name__ == "__main__":
    unittest.main()
