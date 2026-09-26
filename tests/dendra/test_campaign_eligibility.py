"""Synthetic reviewed eligibility: no transport, source assertions or live I/O."""
import copy
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from dendra.history_acquisition import dimensionless_probe as probe
from dendra.history_acquisition import eligibility, provider_metadata
from dendra.history_acquisition.model import Inventory, INVENTORY_SHA256, source_binding
from dendra.history_acquisition.safety import Hold, digest, encode, sha

NOW = "2026-09-26T14:00:00Z"
START = "2024-02-01T00:00:00.000Z"
END = "2024-03-01T00:00:00.000Z"


def setUpModule():
    global INVENTORY, EXECUTOR
    INVENTORY = Inventory.load(os.environ["DENDRA_INVENTORY"], INVENTORY_SHA256)
    EXECUTOR = digest(source_binding())


def inputs(sid=probe.STREAM):
    identity = INVENTORY.identity(sid)
    station = dict(_id=identity["station_id"], public_level=3, is_hidden=False,
                   is_geo_protected=False, name="Synthetic eligibility station")
    attributes = {}
    if identity["depth_cm"] is not None:
        attributes["depth"] = dict(value=identity["depth_cm"], unit="Centimeter")
    if identity["orientation"] is not None:
        attributes["orientation"] = identity["orientation"]
    row = dict(_id=sid, station_id=identity["station_id"], public_level=3,
               is_hidden=False, is_geo_protected=False, attributes=attributes,
               terms=dict(dt=dict(Unit=identity["native_unit"]),
                          ds=dict(Medium="Soil", Variable="VolumetricWaterContent")),
               datapoints_config=[dict(begins_at=START)])
    admitted = provider_metadata._parse_station(encode(station), identity["station_id"],
                                                checked_at=NOW, now=NOW)
    return row, admitted


def packet(sid=probe.STREAM, *, profile=probe.CAMPAIGN_TEMPORAL_PROFILE, row=None):
    base, station = inputs(sid)
    return probe.review_packet(encode(dict(data=[base if row is None else row], limit=500, total=1, skip=0)),
        station, INVENTORY, stream_id=sid, metadata_profile=profile, checked_at=NOW, now=NOW)


def reviewed(p=None):
    p = packet() if p is None else p
    body = encode(p)
    review = eligibility.propose(INVENTORY, body, packet_sha256=sha(body),
        packet_source_fingerprint=p["metadata_binding"]["collector_fingerprint"],
        executor_fingerprint=EXECUTOR, start=START, end=END)
    review.update(disposition="ACCEPT_NATIVE", reviewer_ref="synthetic-explicit-review",
        reviewed_at=NOW, expires_at="2026-09-27T14:00:00Z",
        acknowledgements=list(eligibility.ACKNOWLEDGEMENTS))
    return body, review


def issue(body, review):
    return eligibility.decide(INVENTORY, body, review, executor_fingerprint=EXECUTOR, now=NOW)


def validate(body, review, decision, **kwargs):
    return eligibility.validate_decision(INVENTORY, body, review, decision,
        executor_fingerprint=kwargs.get("executor_fingerprint", EXECUTOR), now=kwargs.get("now", NOW))


class ReviewedEligibilityTests(unittest.TestCase):
    def test_deterministic_restore_and_explicit_independent_statuses(self):
        body, review = reviewed()
        decision = issue(body, review)
        self.assertEqual(decision, issue(body, copy.deepcopy(review)))
        self.assertEqual(decision, validate(body, review, decision))
        self.assertEqual(decision["native_acquisition_status"], eligibility.NATIVE_ELIGIBLE)
        self.assertEqual(decision["normalized_percent_status"], eligibility.NORMALIZED_HOLD)
        self.assertEqual(decision["scale_state_sha256"], digest(decision["scale"]))
        self.assertTrue(decision["normalized_hold_reasons"])
        self.assertFalse(decision["daily_product_eligible"])
        self.assertFalse(decision["publication_eligible"])

    def test_percent_and_vwc_preserve_accepted_baseline_routes(self):
        roster = INVENTORY.roster()
        for unit, factor in (("Percent", 1), ("VolumetricWaterContent", 100)):
            sid = next(s for s in sorted(roster) if roster[s]["native_unit"] == unit)
            with self.subTest(unit=unit):
                body, review = reviewed(packet(sid))
                d = validate(body, review, issue(body, review))
                self.assertEqual(d["native_acquisition_status"], eligibility.NATIVE_ELIGIBLE)
                self.assertEqual(d["normalized_percent_status"], eligibility.NORMALIZED_ELIGIBLE)
                self.assertEqual(d["scale"]["conversion_factor"], factor)
                self.assertEqual(d["scale"]["primary_evidence"], [])
                self.assertFalse(d["normalized_percent_product_eligible"])

    def test_known_depth_and_orientation_require_exact_fresh_evidence(self):
        roster = INVENTORY.roster()
        for field in ("depth_cm", "orientation"):
            sid = next(s for s in sorted(roster) if roster[s][field] is not None)
            with self.subTest(field=field):
                self.assertEqual(packet(sid)["identity_check"][field], "matched_fresh_evidence")
                row, _ = inputs(sid)
                if field == "depth_cm":
                    row["attributes"]["depth"]["value"] += 1
                else:
                    row["attributes"]["orientation"] = "synthetic-conflict"
                with self.assertRaises(Hold):
                    packet(sid, row=row)

    def test_old_temporal_profile_remains_exact_target(self):
        p = packet(profile=probe.TEMPORAL_PROFILE)
        body, review = reviewed(p)
        self.assertEqual(validate(body, review, issue(body, review))["metadata_profile"], probe.TEMPORAL_PROFILE)
        sid = next(s for s in INVENTORY.roster() if s != probe.STREAM)
        with self.assertRaises(Hold):
            packet(sid, profile=probe.TEMPORAL_PROFILE)
        with self.assertRaises(Hold):
            probe.make_plan(INVENTORY, station_id=probe.STATION, stream_id=probe.STREAM,
                            metadata_profile=probe.CAMPAIGN_TEMPORAL_PROFILE)

    def test_metadata_hold_never_becomes_native_authority(self):
        body, review = reviewed()
        for disposition in ("HOLD", "PENDING"):
            with self.subTest(disposition=disposition):
                changed = dict(review, disposition=disposition)
                d = issue(body, changed)
                self.assertEqual(d["native_acquisition_status"], eligibility.NATIVE_HOLD)
                self.assertEqual(d["normalized_percent_status"], eligibility.NORMALIZED_HOLD)
                with self.assertRaises(Hold):
                    validate(body, changed, d)

    def test_access_hold_refuses_review(self):
        p = packet()
        p["access_evidence"]["stream_public_level"] = 1
        with self.assertRaises(Hold):
            reviewed(p)
        row, _ = inputs()
        row["is_hidden"] = True
        with self.assertRaises(Hold):
            packet(row=row)

    def test_temporal_actions_unknown_fields_and_overlap_hold(self):
        for change in (dict(actions={}), dict(unreviewed="withheld"), dict(interval=0)):
            with self.subTest(change=change):
                row, _ = inputs()
                row["datapoints_config"][0].update(change)
                with self.assertRaises(Hold):
                    packet(row=row)
        row, _ = inputs()
        row["datapoints_config"] += [dict(begins_at="2024-02-02T00:00:00.000Z")]
        with self.assertRaises(Hold):
            packet(row=row)

    def test_stale_at_dispatch_cannot_refresh_decision(self):
        body, review = reviewed()
        d = issue(body, review)
        for stamp in ("2026-09-25T14:00:00Z", "2026-09-27T14:00:01Z"):
            with self.subTest(stamp=stamp), self.assertRaises(Hold):
                validate(body, review, d, now=stamp)

    def test_source_change_refuses_without_modifying_old_bytes(self):
        body, review = reviewed()
        d = issue(body, review)
        before = encode(d)
        with self.assertRaises(Hold):
            validate(body, review, d, executor_fingerprint="f" * 64)
        self.assertEqual(encode(d), before)

    def test_self_rehashed_decision_not_authority(self):
        body, review = reviewed()
        d = issue(body, review)
        for field, value in (("native_acquisition_eligible", False),
                             ("configuration_evidence_sha256", "f" * 64),
                             ("normalized_percent_status", eligibility.NORMALIZED_ELIGIBLE),
                             ("valid_until", "2026-09-28T14:00:00Z")):
            with self.subTest(field=field), self.assertRaises(Hold):
                forged = dict(d, **{field: value})
                forged["decision_sha256"] = digest({k: v for k, v in forged.items() if k != "decision_sha256"})
                validate(body, review, forged)

    def test_other_stream_packet_and_review_cannot_reuse_decision(self):
        body, review = reviewed()
        original = issue(body, review)
        sid = next(s for s in INVENTORY.roster() if s != probe.STREAM)
        other_body, other_review = reviewed(packet(sid))
        with self.assertRaises(Hold):
            validate(other_body, other_review, original)
        with self.assertRaises(Hold):
            validate(other_body, review, original)

    def test_original_evidence_and_profile_cannot_be_rewritten(self):
        body, review = reviewed()
        d = issue(body, review)
        with self.assertRaises(Hold):
            validate(body + b" ", review, d)
        for key in ("schema_version", "metadata_profile"):
            p = packet()
            p[key] = "unsupported-version"
            with self.subTest(key=key), self.assertRaises(Hold):
                reviewed(p)


if __name__ == "__main__":
    unittest.main()
