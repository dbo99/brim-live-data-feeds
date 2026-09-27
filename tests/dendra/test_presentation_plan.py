"""Offline calendar/source-authority planning with synthetic reviewed evidence."""
import copy
from datetime import timedelta
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

NETWORK_ATTEMPTS, SLEEP_ATTEMPTS = [], []


def deny_network(event, args):
    if event.startswith("socket."):
        NETWORK_ATTEMPTS.append(event)
        raise AssertionError("Presentation planning forbids socket/DNS activity")


def deny_sleep(seconds):
    SLEEP_ATTEMPTS.append(seconds)
    raise AssertionError("Presentation planning forbids real sleep")


sys.addaudithook(deny_network)
SLEEP_PATCH = patch("time.sleep", deny_sleep)
SLEEP_PATCH.start()
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from dendra.history_acquisition import campaign, eligibility, presentation, provider_metadata
from dendra.history_acquisition import dimensionless_probe as probe
from dendra.history_acquisition.model import Inventory, INVENTORY_SHA256, source_binding
from dendra.history_acquisition.safety import Hold, digest, encode, sha
from dendra.transport import parse_utc

NOW = "2026-09-26T14:00:00.000Z"
SID = probe.STREAM


def setUpModule():
    global INVENTORY, FINGERPRINT
    INVENTORY = Inventory.load(os.environ["DENDRA_INVENTORY"], INVENTORY_SHA256)
    FINGERPRINT = digest(source_binding())


def tearDownModule():
    SLEEP_PATCH.stop()
    if NETWORK_ATTEMPTS or SLEEP_ATTEMPTS:
        raise AssertionError(f"Forbidden activity: sockets={NETWORK_ATTEMPTS}, sleeps={SLEEP_ATTEMPTS}")


def source(state=presentation.REVIEWED, start="2010-01-01T08:00:00.000Z", *, sid=SID):
    value = dict(state=state, station_id=INVENTORY.identity(sid)["station_id"], stream_id=sid,
                 inventory_sha256=INVENTORY_SHA256, start=None if state == presentation.UNKNOWN else start)
    if state != presentation.UNKNOWN:
        value["evidence_sha256"] = "a" * 64
    if state == presentation.REVIEWED:
        value.update(reviewer_ref="synthetic-source-start-review", reviewed_at="2024-01-01T00:00:00.000Z")
    return value


def descriptor(*, mode=presentation.INITIAL, as_of=NOW, entry=None):
    return presentation.make(INVENTORY, mode=mode, as_of=as_of,
                             source_starts={SID: source() if entry is None else entry})


def decision(configs=None):
    station = dict(_id=probe.STATION, public_level=3, is_hidden=False, is_geo_protected=False)
    row = dict(_id=SID, station_id=probe.STATION, public_level=3, is_hidden=False,
               is_geo_protected=False, attributes={}, terms=dict(dt=dict(Unit="Dimensionless"),
                   ds=dict(Medium="Soil", Variable="VolumetricWaterContent")),
               datapoints_config=configs or [dict(begins_at="2010-01-01T08:00:00.000Z")])
    admitted = provider_metadata._parse_station(encode(station), probe.STATION, checked_at=NOW, now=NOW)
    packet = probe.review_packet(encode(dict(data=[row], limit=500, total=1, skip=0)), admitted,
        INVENTORY, stream_id=SID, metadata_profile=probe.TEMPORAL_PROFILE, checked_at=NOW, now=NOW)
    body = encode(packet)
    review = eligibility.propose(INVENTORY, body, packet_sha256=sha(body),
        packet_source_fingerprint=packet["metadata_binding"]["collector_fingerprint"],
        executor_fingerprint=FINGERPRINT, start="2010-01-01T08:00:00.000Z", end="2030-01-01T08:00:00.000Z")
    review.update(disposition="ACCEPT_NATIVE", reviewer_ref="synthetic-native-review", reviewed_at=NOW,
                  expires_at="2026-09-27T14:00:00.000Z", acknowledgements=list(eligibility.ACKNOWLEDGEMENTS))
    return eligibility.decide(INVENTORY, body, review, executor_fingerprint=FINGERPRINT, now=NOW)


def manifest(p, d=None):
    return campaign.make_campaign(INVENTORY, campaign_id="synthetic-presentation",
        executor_fingerprint=FINGERPRINT, horizons=p["horizons"], presentation=p,
        decisions={} if d is None else {SID: d})


class PresentationCalendarTests(unittest.TestCase):
    def test_current_wy_and_nine_preceding_exact_boundary(self):
        p = descriptor()
        self.assertEqual((p["current_water_year"], p["earliest_water_year"]), (2026, 2017))
        self.assertEqual(p["horizons"][SID], dict(start="2016-10-01T08:00:00.000Z",
                                                end="2026-09-26T08:00:00.000Z"))
        self.assertFalse(p["network_execution_authorized"])
        self.assertFalse(p["reference_band_authorized"])

    def test_october_rollover_uses_fixed_pst_not_utc_date(self):
        before = descriptor(as_of="2026-10-01T07:59:59.999Z")
        after = descriptor(as_of="2026-10-01T08:00:00.000Z")
        self.assertEqual(before["current_water_year"], 2026)
        self.assertEqual(after["current_water_year"], 2027)
        self.assertEqual(after["water_year_floor"], "2017-10-01T08:00:00.000Z")
        self.assertEqual(after["completed_before"], "2026-10-01T08:00:00.000Z")

    def test_ten_water_years_are_calendar_years_not_3650_days(self):
        p = descriptor(as_of="2026-09-30T12:00:00.000Z")
        floor = parse_utc(p["water_year_floor"])
        complete_ten_wy_end = parse_utc("2026-10-01T08:00:00.000Z")
        self.assertEqual((complete_ten_wy_end - floor).days, 3652)
        self.assertNotEqual((complete_ten_wy_end - floor).days, 3650)

    def test_february_29_remains_a_complete_fixed_pst_day(self):
        p = descriptor(as_of="2024-03-01T08:00:00.000Z", entry=source(start="2024-02-29T08:00:00.000Z"))
        h = p["horizons"][SID]
        self.assertEqual(parse_utc(h["end"]) - parse_utc(h["start"]), timedelta(days=1))
        self.assertEqual(p["current_water_year"], 2024)

    def test_current_incomplete_day_is_separate_and_excluded(self):
        p = descriptor(as_of="2026-09-26T23:59:59.999Z")
        self.assertEqual(p["current_incomplete_day"], dict(start="2026-09-26T08:00:00.000Z",
            end="2026-09-27T08:00:00.000Z", included=False))
        self.assertEqual(p["horizons"][SID]["end"], p["current_incomplete_day"]["start"])

    def test_dst_transition_does_not_change_eight_utc_boundary(self):
        a = descriptor(as_of="2024-03-10T15:00:00.000Z")
        b = descriptor(as_of="2024-03-11T15:00:00.000Z")
        self.assertEqual(a["completed_before"], "2024-03-10T08:00:00.000Z")
        self.assertEqual(b["completed_before"], "2024-03-11T08:00:00.000Z")
        self.assertEqual(parse_utc(b["completed_before"]) - parse_utc(a["completed_before"]), timedelta(days=1))

    def test_shorter_reviewed_record_clamps_exact_start(self):
        p = descriptor(entry=source(start="2024-02-15T08:00:00.000Z"))
        self.assertEqual(p["horizons"][SID]["start"], "2024-02-15T08:00:00.000Z")
        self.assertEqual(p["streams"][SID]["coverage"], "NOT_QUERIED_NO_COVERAGE_CLAIM")

    def test_intraday_source_start_preserved_without_preboundary_query(self):
        p = descriptor(entry=source(start="2024-02-15T12:34:56.123456Z"))
        self.assertEqual(p["horizons"][SID]["start"], "2024-02-15T12:34:56.123456Z")
        self.assertTrue(p["streams"][SID]["source_boundary_is_intraday"])

    def test_no_completed_source_interval_is_not_empty_coverage(self):
        for start in ("2026-09-26T08:00:00.000Z", "2026-09-27T08:00:00.000Z"):
            with self.subTest(start=start):
                p = descriptor(entry=source(start=start))
                self.assertEqual(p["horizons"], {})
                self.assertEqual(p["streams"][SID]["planning_state"], "NO_COMPLETED_INTERVAL")
                self.assertEqual(campaign.plan(manifest(p), INVENTORY, now=NOW)["tasks"], [])

    def test_full_por_is_explicit_and_distinct(self):
        initial, por = descriptor(), descriptor(mode=presentation.FULL_POR)
        self.assertEqual(por["horizons"][SID]["start"], "2010-01-01T08:00:00.000Z")
        self.assertIsNone(por["water_year_floor"])
        self.assertNotEqual(manifest(initial)["campaign_identity"], manifest(por)["campaign_identity"])
        self.assertEqual(por["horizons"][SID]["end"], initial["horizons"][SID]["end"])


class SourceStartAuthorityTests(unittest.TestCase):
    def test_unknown_start_has_no_horizon_or_execution(self):
        p = descriptor(entry=source(presentation.UNKNOWN))
        plan = campaign.plan(manifest(p), INVENTORY, now=NOW)
        self.assertEqual(p["horizons"], {})
        self.assertIsNone(p["streams"][SID]["estimated_horizon"])
        self.assertEqual(plan["blocked_streams"], {SID: ["SOURCE_START_UNKNOWN"]})
        self.assertEqual(plan["tasks"], [])

    def test_audit_estimate_cannot_become_executable_horizon(self):
        p = descriptor(entry=source(presentation.AUDIT, start="2022-05-01T08:00:00.000Z"))
        self.assertEqual(p["horizons"], {})
        self.assertEqual(p["streams"][SID]["estimated_horizon"]["start"], "2022-05-01T08:00:00.000Z")
        self.assertEqual(campaign.plan(manifest(p), INVENTORY, now=NOW)["blocked_streams"],
                         {SID: ["SOURCE_START_REVIEW_REQUIRED"]})
        with self.assertRaises(Hold):
            campaign.make_campaign(INVENTORY, campaign_id="synthetic-audit", executor_fingerprint=FINGERPRINT,
                presentation=p, horizons={SID: p["streams"][SID]["estimated_horizon"]})

    def test_full_por_also_requires_reviewed_source_start(self):
        p = descriptor(mode=presentation.FULL_POR, entry=source(presentation.UNKNOWN))
        self.assertEqual(p["horizons"], {})
        self.assertFalse(p["streams"][SID]["eligible_for_campaign_planning"])

    def test_wrong_station_stream_or_inventory_binding_refuses(self):
        for key, value in (("station_id", "a" * 24), ("stream_id", "a" * 24),
                           ("inventory_sha256", "a" * 64)):
            with self.subTest(key=key), self.assertRaises(Hold):
                descriptor(entry=dict(source(), **{key: value}))

    def test_missing_review_or_hash_and_future_review_refuse(self):
        for field in ("reviewer_ref", "reviewed_at", "evidence_sha256"):
            with self.subTest(field=field), self.assertRaises(Hold):
                row = source()
                del row[field]
                descriptor(entry=row)
        for changes in (dict(evidence_sha256="bad"), dict(reviewer_ref=""),
                        dict(reviewed_at="2027-01-01T08:00:00.000Z")):
            with self.subTest(changes=changes), self.assertRaises(Hold):
                descriptor(entry=dict(source(), **changes))

    def test_unknown_start_cannot_smuggle_a_timestamp(self):
        with self.assertRaises(Hold):
            descriptor(entry=dict(source(presentation.UNKNOWN), start="2020-01-01T08:00:00.000Z"))

    def test_mode_required_and_unsupported_modes_refuse(self):
        for mode in (None, "ten-years", "3650-days", ""):
            with self.subTest(mode=mode), self.assertRaises(Hold):
                descriptor(mode=mode)

    def test_descriptor_tamper_even_with_rehash_refuses(self):
        p = descriptor()
        p["horizons"][SID]["start"] = "2010-01-01T08:00:00.000Z"
        p["planning_sha256"] = digest({k: v for k, v in p.items() if k != "planning_sha256"})
        with self.assertRaises(Hold):
            presentation.verify(p, INVENTORY)

    def test_descriptor_does_not_mutate_source_inputs(self):
        entries = {SID: source()}
        before = encode(entries)
        p = presentation.make(INVENTORY, mode=presentation.INITIAL, as_of=NOW, source_starts=entries)
        self.assertEqual(encode(entries), before)
        p["source_starts"][SID]["start"] = "2025-01-01T08:00:00.000Z"
        self.assertEqual(encode(entries), before)


class PresentationCampaignTests(unittest.TestCase):
    def test_future_presentation_as_of_refuses_even_when_source_is_unknown(self):
        for state in (presentation.REVIEWED, presentation.UNKNOWN):
            with self.subTest(state=state):
                m = manifest(descriptor(as_of="2026-10-01T08:00:00.000Z", entry=source(state)))
                for now in (NOW, None):
                    with self.subTest(now=now), self.assertRaises(Hold):
                        campaign.plan(m, INVENTORY, now=now)

    def test_legacy_explicit_horizons_manifest_stays_exact(self):
        h = {SID: dict(start="2010-01-01T08:00:00.000Z", end="2026-09-26T08:00:00.000Z")}
        core = dict(version=campaign.VERSION, campaign_id="synthetic-legacy", executor_fingerprint=FINGERPRINT,
            inventory_sha256=INVENTORY_SHA256, horizons=h, chunk_days=30, request_policy=campaign.POLICY)
        body = dict(core=core, campaign_identity=digest(core), roster=INVENTORY.roster(), decisions={},
                    budgets=campaign.policy(), network_execution_authorized=False)
        expected = dict(body, manifest_sha256=digest(body))
        actual = campaign.make_campaign(INVENTORY, campaign_id="synthetic-legacy",
            executor_fingerprint=FINGERPRINT, horizons=h)
        self.assertEqual(actual, expected)
        before = encode(actual)
        self.assertTrue(campaign.verify(actual, INVENTORY, executor_fingerprint=FINGERPRINT))
        self.assertEqual(encode(actual), before)
        self.assertNotIn("presentation", actual["core"])

    def test_deterministic_30_day_config_chunks_and_continuation(self):
        boundary = "2024-02-29T12:00:00.000Z"
        d = decision([dict(begins_at="2010-01-01T08:00:00.000Z", ends_before=boundary),
                      dict(begins_at=boundary)])
        m = manifest(descriptor(entry=source(start="2024-02-01T08:00:00.000Z")), d)
        before = encode(m)
        all_tasks = campaign.plan(m, INVENTORY, now=NOW)["tasks"]
        self.assertEqual(all_tasks, campaign.plan(copy.deepcopy(m), INVENTORY, now=NOW)["tasks"])
        self.assertTrue(all(parse_utc(t["identity"]["end"]) - parse_utc(t["identity"]["start"]) <=
                            timedelta(days=30) for t in all_tasks))
        self.assertTrue(any(t["identity"]["end"] == boundary for t in all_tasks))
        self.assertTrue(any(t["identity"]["start"] == boundary for t in all_tasks))
        first = campaign.plan(m, INVENTORY, now=NOW, max_tasks=1)
        rest = campaign.plan(m, INVENTORY, now=NOW, after_task=first["next_after_task"])
        self.assertEqual(first["tasks"] + rest["tasks"], all_tasks)
        self.assertEqual(encode(m), before)
        self.assertTrue(all(not t["executable"] for t in all_tasks))

    def test_configuration_gap_remains_unqueried(self):
        d = decision([dict(begins_at="2010-01-01T08:00:00.000Z", ends_before="2024-02-29T08:00:00.000Z"),
                      dict(begins_at="2024-03-01T08:00:00.000Z")])
        p = campaign.plan(manifest(descriptor(entry=source(start="2024-02-15T08:00:00.000Z")), d), INVENTORY, now=NOW)
        self.assertEqual(p["configuration_gaps"], [dict(stream_id=SID, start="2024-02-29T08:00:00.000Z",
            end="2024-03-01T08:00:00.000Z", state="CONFIGURATION_GAP_NOT_QUERIED")])

    def test_reviewed_start_does_not_replace_metadata_review(self):
        p = campaign.plan(manifest(descriptor()), INVENTORY, now=NOW)
        self.assertEqual(p["tasks"], [])
        self.assertEqual(p["blocked_streams"], {SID: ["metadata_review_required"]})

    def test_presentation_mode_does_not_change_request_policy(self):
        m = manifest(descriptor())
        self.assertEqual(m["budgets"], campaign.policy())
        self.assertEqual((m["budgets"]["concurrency"], m["budgets"]["retries"]), (1, 0))
        self.assertEqual(m["core"]["chunk_days"], 30)
        self.assertFalse(m["network_execution_authorized"])

    def test_audit_to_reviewed_requires_new_campaign_identity(self):
        estimated = manifest(descriptor(entry=source(presentation.AUDIT)))
        saved = encode(estimated)
        reviewed = manifest(descriptor())
        self.assertNotEqual(estimated["campaign_identity"], reviewed["campaign_identity"])
        self.assertEqual(encode(estimated), saved)


if __name__ == "__main__":
    unittest.main()
