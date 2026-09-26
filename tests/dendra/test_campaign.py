"""Offline campaign gates using synthetic metadata and the explicit frozen roster.

Completion seals below are caller-trusted synthetic inputs, not archive proofs.
No test invokes a provider, a collector, or a live execution adapter.
"""
import copy
import inspect
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from collections import Counter
from datetime import timedelta
from contextlib import contextmanager, redirect_stdout

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from dendra.history_acquisition import campaign, campaign_cli, eligibility
from dendra.history_acquisition import dimensionless_probe as probe
from dendra.history_acquisition import provider_metadata
from dendra.history_acquisition import scale_resolution as scale
from dendra.history_acquisition.model import Inventory, INVENTORY_SHA256, source_binding
from dendra.history_acquisition.safety import Hold, decode, digest, encode, sha
from dendra.transport import parse_utc

NOW = "2026-09-26T14:00:00Z"
START = "2024-02-01T00:00:00.000Z"
END = "2024-04-02T00:00:00.000Z"
BOUNDARY = "2024-02-29T00:00:00.000Z"


@contextmanager
def preserved_scratch():
    """Keep this task's CLI fixture/output bytes available for inspection."""
    yield tempfile.mkdtemp(prefix="campaign-cli-", dir=os.environ["DENDRA_TEST_ROOT"])


def setUpModule():
    global INVENTORY, EXECUTOR
    INVENTORY = Inventory.load(os.environ["DENDRA_INVENTORY"], INVENTORY_SHA256)
    EXECUTOR = digest(source_binding())


def synthetic_packet(configs=None):
    # Same minimal metadata shapes as test_dimensionless_probe.stream/station;
    # construct locally to avoid importing that suite's global guard lifecycle.
    station = dict(_id=probe.STATION, public_level=3, is_hidden=False,
                   is_geo_protected=False, name="Synthetic campaign station")
    row = dict(_id=probe.STREAM, station_id=probe.STATION, public_level=3,
               is_hidden=False, is_geo_protected=False, attributes={},
               terms=dict(dt=dict(Unit="Dimensionless"),
                          ds=dict(Medium="Soil", Variable="VolumetricWaterContent")),
               datapoints_config=configs if configs is not None else [dict(begins_at=START)])
    admitted = provider_metadata._parse_station(encode(station), probe.STATION,
                                                checked_at=NOW, now=NOW)
    return probe.review_packet(encode(dict(data=[row], limit=500, total=1, skip=0)),
                               admitted, INVENTORY, stream_id=probe.STREAM,
                               metadata_profile=probe.TEMPORAL_PROFILE, checked_at=NOW, now=NOW)


def proposal(packet):
    body = encode(packet)
    review = eligibility.propose(INVENTORY, body, packet_sha256=sha(body),
        packet_source_fingerprint=packet["metadata_binding"]["collector_fingerprint"],
        executor_fingerprint=EXECUTOR, start=START, end=END)
    return body, review


def reviewed(packet=None):
    body, review = proposal(synthetic_packet() if packet is None else packet)
    review.update(disposition="ACCEPT_NATIVE", reviewer_ref="synthetic-manual-review",
                  reviewed_at=NOW, expires_at="2026-09-27T14:00:00Z",
                  acknowledgements=list(eligibility.ACKNOWLEDGEMENTS))
    return body, review


def decision(packet=None):
    body, review = reviewed(packet)
    return eligibility.decide(INVENTORY, body, review, executor_fingerprint=EXECUTOR, now=NOW)


def manifest(d=None, **overrides):
    kwargs = dict(campaign_id="synthetic-campaign", executor_fingerprint=EXECUTOR,
                  horizons={probe.STREAM: dict(start=START, end=END)},
                  decisions={} if d is None else {probe.STREAM: d})
    kwargs.update(overrides)
    return campaign.make_campaign(INVENTORY, **kwargs)


def dry_plan(m, inventory, **kwargs):
    return campaign.plan(m, inventory, now=NOW, **kwargs)


class EligibilityTests(unittest.TestCase):
    def test_pending_is_not_acquisition_authority(self):
        body, review = proposal(synthetic_packet())
        self.assertEqual(review["disposition"], "PENDING")
        d = eligibility.decide(INVENTORY, body, review, executor_fingerprint=EXECUTOR, now=NOW)
        self.assertFalse(d["native_acquisition_eligible"])
        self.assertIn("review_required", d["hold_reasons"])
        self.assertFalse(d["live_execution_authorized"])
        self.assertEqual(dry_plan(manifest(d), INVENTORY)["tasks"], [])

    def test_explicit_synthetic_review_allows_native_only_preserves_packet(self):
        packet = synthetic_packet()
        before = encode(packet)
        d = decision(packet)
        self.assertEqual(encode(packet), before)
        for field in ("raw_eligible", "observation_acquisition_authorized",
                      "daily_science_accepted", "browser_publication_eligible"):
            self.assertIs(packet[field], False)
        self.assertTrue(d["native_acquisition_eligible"])
        for field in ("normalized_conversion_eligible", "normalized_percent_product_eligible",
                      "daily_product_eligible", "publication_eligible", "live_execution_authorized"):
            self.assertIs(d[field], False)
        self.assertIsNone(d["identity"]["depth_cm"])
        self.assertIsNone(d["identity"]["orientation"])
        self.assertEqual(d["historical_applicability"], "unknown_history")

    def test_exact_packet_source_profile_and_config_bindings(self):
        packet = synthetic_packet()
        body, review = reviewed(packet)
        with self.assertRaises(Hold):
            eligibility.decide(INVENTORY, body + b" ", review, executor_fingerprint=EXECUTOR, now=NOW)
        with self.assertRaises(Hold):
            eligibility.decide(INVENTORY, body, review, executor_fingerprint="b" * 64, now=NOW)
        for field, value in (("metadata_profile", "unaccepted-profile"),
                             ("schema_version", "unaccepted-packet"),
                             ("configuration_sha256", "b" * 64)):
            with self.subTest(field=field), self.assertRaises(Hold):
                changed = copy.deepcopy(packet)
                changed[field] = value
                proposal(changed)
        for field in ("configuration_evidence_sha256", "scientific_sha256", "selected_record_sha256"):
            with self.subTest(review_field=field), self.assertRaises(Hold):
                changed = dict(review, **{field: "b" * 64})
                eligibility.decide(INVENTORY, body, changed, executor_fingerprint=EXECUTOR, now=NOW)
        with self.assertRaises(Hold):
            eligibility.propose(INVENTORY, body, packet_sha256=sha(body),
                packet_source_fingerprint="b" * 64, executor_fingerprint=EXECUTOR, start=START, end=END)

    def test_stale_access_metadata_cannot_be_refreshed_by_new_review(self):
        body, review = reviewed()
        review.update(reviewed_at="2026-09-28T14:00:00Z", expires_at="2026-09-29T14:00:00Z")
        d = eligibility.decide(INVENTORY, body, review, executor_fingerprint=EXECUTOR,
                               now="2026-09-28T14:00:00Z")
        self.assertFalse(d["native_acquisition_eligible"])
        self.assertEqual(d["hold_reasons"], ["access_metadata_stale_or_future"])
        self.assertEqual(campaign.partition(manifest(d), INVENTORY)["partition_counts"]["access_hold"], 1)

    def test_inner_evidence_tampering_rejected_even_after_outer_rehash(self):
        for field, value in (("metadata_profile", "unknown-profile"),
                             ("packet_version", "unknown-version"),
                             ("frozen_identity_sha256", "b" * 64),
                             ("original_response_sha256", "invalid-hash")):
            with self.subTest(field=field), self.assertRaises(Hold):
                packet = synthetic_packet()
                packet["configuration_evidence"][field] = value
                packet["metadata_binding"]["configuration_evidence_sha256"] = digest(packet["configuration_evidence"])
                packet["metadata_binding_sha256"] = digest(packet["metadata_binding"])
                proposal(packet)
        for field, value in (("ordinal", True), ("ordinal", 7), ("object_sha256", "not-a-hash")):
            with self.subTest(config_field=field, value=value), self.assertRaises(Hold):
                packet = synthetic_packet()
                packet["configuration_evidence"]["configurations"][0][field] = value
                packet["metadata_binding"]["configuration_evidence_sha256"] = digest(packet["configuration_evidence"])
                packet["metadata_binding_sha256"] = digest(packet["metadata_binding"])
                proposal(packet)

    def test_synthetic_scale_sidecar_changes_conversion_but_not_native_task_ids(self):
        body, review = reviewed()
        kwargs = dict(executor_fingerprint=EXECUTOR, now=NOW)
        before = eligibility.decide(INVENTORY, body, review, **kwargs)
        scope = dict(kind="interval", start=START, end=END)
        source = encode(dict(synthetic=True, stream_id=probe.STREAM, scale="fraction", applicability=scope))
        claim = dict(schema_version=scale.EVIDENCE_VERSION, station_id=probe.STATION,
                     stream_id=probe.STREAM, role="primary", kind="provider_scale_statement",
                     scale="fraction", applicability=scope,
                     source=dict(ref="synthetic/campaign-scale.json", sha256=sha(source), version="synthetic-v1"))
        evidence = scale.Evidence.bind(claim, source)
        after = eligibility.decide(INVENTORY, body, review, scale_evidence=[evidence], **kwargs)
        self.assertFalse(before["normalized_conversion_eligible"])
        self.assertTrue(after["normalized_conversion_eligible"])
        self.assertFalse(after["normalized_percent_product_eligible"])
        self.assertEqual(before["native_authority_sha256"], after["native_authority_sha256"])
        a = dry_plan(manifest(before), INVENTORY)["tasks"]
        b = dry_plan(manifest(after), INVENTORY)["tasks"]
        self.assertEqual([t["task_id"] for t in a], [t["task_id"] for t in b])
        self.assertNotEqual(a[0]["scale_decision_sha256"], b[0]["scale_decision_sha256"])

    def test_acceptance_requires_explicit_review_and_acknowledgements(self):
        body, review = reviewed()
        for changes in (dict(reviewer_ref=None), dict(acknowledgements=[]),
                        dict(expires_at="2026-09-28T14:00:00Z")):
            with self.subTest(changes=changes), self.assertRaises(Hold):
                eligibility.decide(INVENTORY, body, dict(review, **changes),
                                   executor_fingerprint=EXECUTOR, now=NOW)


class CampaignTests(unittest.TestCase):
    def test_frozen_roster_partition_closure_does_not_claim_coverage(self):
        m = manifest()
        roster = m["roster"]
        self.assertEqual(len(roster), 434)
        self.assertEqual(len({x["station_id"] for x in roster.values()}), 122)
        self.assertEqual(Counter(x["native_unit"] for x in roster.values()),
                         dict(Percent=177, VolumetricWaterContent=160, Dimensionless=97))
        p = campaign.partition(m, INVENTORY)
        self.assertEqual(p["partition_counts"], {"metadata_review_required": 434})
        self.assertEqual(p["coverage_authority"], "not_loaded_no_coverage_claim")
        self.assertFalse(any(s["native_acquisition_eligible"] for s in p["states"].values()))
        eligible = campaign.partition(manifest(decision()), INVENTORY)
        self.assertEqual(eligible["partition_counts"], {"metadata_review_required": 433, "native_eligible": 1})
        self.assertFalse(eligible["states"][probe.STREAM]["normalized_conversion_eligible"])

    def test_deterministic_subset_task_identity(self):
        m = manifest(decision())
        full = dry_plan(m, INVENTORY)
        subset = dry_plan(m, INVENTORY, stream_ids=[probe.STREAM])
        self.assertEqual(full, subset)
        self.assertEqual(full, dry_plan(copy.deepcopy(m), INVENTORY))
        self.assertTrue(all(not t["executable"] for t in full["tasks"]))
        self.assertEqual(full["network_requests"], 0)
        self.assertFalse(m["network_execution_authorized"])

    def test_30_day_leap_and_configuration_boundaries(self):
        configs = [dict(begins_at=START, ends_before=BOUNDARY), dict(begins_at=BOUNDARY)]
        tasks = dry_plan(manifest(decision(synthetic_packet(configs))), INVENTORY)["tasks"]
        intervals = [(t["identity"]["start"], t["identity"]["end"]) for t in tasks]
        self.assertEqual(intervals, [(START, BOUNDARY), (BOUNDARY, "2024-03-02T00:00:00.000Z"),
                                   ("2024-03-02T00:00:00.000Z", "2024-04-01T00:00:00.000Z"),
                                   ("2024-04-01T00:00:00.000Z", END)])
        self.assertTrue(all(timedelta(0) < parse_utc(b)-parse_utc(a) <= timedelta(days=30)
                            for a, b in intervals))
        self.assertEqual([t["identity"]["configuration_ordinal"] for t in tasks], [0, 1, 1, 1])
        for size in (0, 31, True):
            with self.subTest(size=size), self.assertRaises(Hold):
                manifest(decision(), chunk_days=size)

    def test_configuration_gap_is_not_empty_coverage(self):
        gap_end = "2024-03-02T00:00:00.000Z"
        configs = [dict(begins_at=START, ends_before=BOUNDARY), dict(begins_at=gap_end)]
        p = dry_plan(manifest(decision(synthetic_packet(configs))), INVENTORY)
        self.assertEqual(p["configuration_gaps"], [dict(stream_id=probe.STREAM,
            start=BOUNDARY, end=gap_end, state="CONFIGURATION_GAP_NOT_QUERIED")])
        self.assertEqual(p["completed_count"], 0)
        self.assertFalse(any(t["identity"]["start"] == BOUNDARY for t in p["tasks"]))

    def test_complete_empty_skip_and_explicit_continuation(self):
        m = manifest(decision())
        all_tasks = dry_plan(m, INVENTORY)["tasks"]
        first = all_tasks[0]["task_id"]
        seal = dict(task_id=first, source_fingerprint=EXECUTOR,
                    campaign_identity=m["campaign_identity"], status="COMPLETE_EMPTY",
                    seal_sha256="c" * 64, archive_sha256="d" * 64)
        resumed = dry_plan(m, INVENTORY, completed={first: seal})
        self.assertEqual(resumed["tasks"], all_tasks[1:])
        self.assertEqual(resumed["completed_count"], 1)
        batch = dry_plan(m, INVENTORY, max_tasks=1)
        continuation = dry_plan(m, INVENTORY, after_task=batch["next_after_task"])
        self.assertEqual(batch["tasks"] + continuation["tasks"], all_tasks)
        self.assertIsNone(continuation["next_after_task"])
        for changes in (dict(status="RESPONSE_RECEIVED"), dict(source_fingerprint="e" * 64),
                        dict(campaign_identity="e" * 64)):
            with self.subTest(changes=changes), self.assertRaises(Hold):
                dry_plan(m, INVENTORY, completed={first: dict(seal, **changes)})
        for kwargs in (dict(max_tasks=0), dict(max_tasks=4097), dict(after_task="f" * 64)):
            with self.subTest(kwargs=kwargs), self.assertRaises(Hold):
                dry_plan(m, INVENTORY, **kwargs)

    def test_changed_configuration_changes_native_task_identity(self):
        first = manifest(decision())
        changed = manifest(decision(synthetic_packet([dict(begins_at=START, ends_before=BOUNDARY),
                                                       dict(begins_at=BOUNDARY)])))
        old_ids = {t["task_id"] for t in dry_plan(first, INVENTORY)["tasks"]}
        new_ids = {t["task_id"] for t in dry_plan(changed, INVENTORY)["tasks"]}
        self.assertFalse(old_ids & new_ids)
        with self.assertRaises(Hold):
            campaign.verify(first, INVENTORY, executor_fingerprint="b" * 64)

    def test_no_compute_count_control_and_conservative_failure_scopes(self):
        for function in (campaign.make_campaign, campaign.plan, campaign.policy):
            params = inspect.signature(function).parameters
            self.assertNotIn("agent_count", params)
            self.assertNotIn("workers", params)
            self.assertNotIn("concurrency", params)
        p = campaign.policy(logical_requests=6, attempts=6, total_bytes=1000, wall_seconds=60)
        self.assertEqual((p["concurrency"], p["retries"]), (1, 0))
        self.assertEqual(campaign.failure_scope("429"), "CAMPAIGN_PAUSE")
        self.assertEqual(campaign.failure_scope("403"), "STREAM_ACCESS_HOLD")
        self.assertEqual(campaign.failure_scope("schema_change"), "CAMPAIGN_STOP")
        self.assertEqual(campaign.failure_scope("incomplete_pagination"), "TASK_INCOMPLETE")
        with self.assertRaises(Hold):
            campaign.policy(logical_requests=2, attempts=1)

    def test_baseline_scale_candidates_still_need_metadata_review(self):
        selected = ["63531a67a9b61453fa1ca4ed", "5d8e42e72da5c3cc53f6531d"]
        m = manifest(horizons={sid: dict(start=START, end=END) for sid in selected})
        p = dry_plan(m, INVENTORY, stream_ids=selected)
        self.assertEqual(p["tasks"], [])
        self.assertEqual(set(p["blocked_streams"]), set(selected))

    def test_planning_requires_fresh_explicit_evaluation_time(self):
        m = manifest(decision())
        for kwargs in ({}, dict(now="2026-09-28T14:00:00Z")):
            with self.subTest(kwargs=kwargs), self.assertRaises(Hold):
                campaign.plan(m, INVENTORY, **kwargs)

    def test_cli_execution_modes_stop_without_io(self):
        for mode in ("collect", "resume", "prepare-product"):
            with self.subTest(mode=mode), redirect_stdout(io.StringIO()) as output:
                code = campaign_cli.main([mode])
            result = decode(output.getvalue().encode())
            self.assertEqual(code, campaign_cli.EXIT_STOP)
            self.assertEqual(result["outcome"], "STOP")
            self.assertEqual(result["provider_requests"], 0)

    def test_cli_requires_explicit_inventory_and_exposes_no_worker_parameter(self):
        with redirect_stdout(io.StringIO()) as output:
            code = campaign_cli.main(["plan", "--dry-run"])
        self.assertEqual(code, campaign_cli.EXIT_HOLD)
        self.assertEqual(decode(output.getvalue().encode())["outcome"], "HOLD")
        destinations = {action.dest for action in campaign_cli.parser()._actions}
        self.assertTrue({"stream", "station", "after_task", "max_tasks", "dry_run"} <= destinations)
        self.assertFalse({"workers", "agent_count", "concurrency"} & destinations)

    def test_cli_offline_roundtrip_refuses_overwrite_and_changed_review_hash(self):
        def invoke(args):
            with redirect_stdout(io.StringIO()) as output:
                code = campaign_cli.main(args)
            return code, decode(output.getvalue().encode())

        with preserved_scratch() as work:
            root = Path(work).resolve()
            body, review = proposal(synthetic_packet())
            packet_path, review_path, config_path = [root / name for name in
                                                    ("packet.json", "review.json", "config.json")]
            packet_path.write_bytes(body)
            review_body = encode(review)
            review_path.write_bytes(review_body)
            config = dict(campaign_id="synthetic-cli-campaign",
                          horizons={probe.STREAM: dict(start=START, end=END)}, chunk_days=30,
                          budgets=campaign.policy(), reviews=[dict(packet_path=str(packet_path),
                              review_path=str(review_path), review_sha256=sha(review_body))])
            config_path.write_bytes(encode(config))
            common = ["--inventory", os.environ["DENDRA_INVENTORY"],
                      "--inventory-sha256", INVENTORY_SHA256]
            args = ["plan", *common, "--config", str(config_path), "--state-root", str(root),
                    "--now", NOW, "--dry-run"]
            code, result = invoke(args)
            self.assertEqual(code, campaign_cli.EXIT_HOLD)
            self.assertEqual((result["task_count"], result["blocked_stream_count"], result["provider_requests"]), (0, 1, 0))
            plan_path = root / result["plan_path"]
            manifest_path = plan_path.with_name("manifest.json")
            status_path = plan_path.with_name("status.json")
            saved = {p: p.read_bytes() for p in (plan_path, manifest_path, status_path)}
            status = decode(saved[status_path])
            self.assertEqual(status["stream_count"], 434)
            self.assertEqual(sum(status["partition_counts"].values()), 434)
            for mode in ("status", "verify"):
                with self.subTest(mode=mode):
                    code, response = invoke([mode, *common, "--manifest", str(manifest_path)])
                    self.assertEqual(code, 0)
                    self.assertEqual(response["provider_requests"], 0)
                    self.assertFalse(response["execution_ready"])
            code, repeated = invoke(args)
            self.assertEqual(code, campaign_cli.EXIT_STOP)
            self.assertEqual(repeated["provider_requests"], 0)
            self.assertEqual({p: p.read_bytes() for p in saved}, saved)
            config["reviews"][0]["review_sha256"] = "f" * 64
            config_path.write_bytes(encode(config))
            code, changed = invoke(args)
            self.assertEqual(code, campaign_cli.EXIT_HOLD)
            self.assertEqual(changed["reason"], "Review file changed")
            self.assertEqual({p: p.read_bytes() for p in saved}, saved)


if __name__ == "__main__":
    unittest.main()
