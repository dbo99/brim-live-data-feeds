"""Synthetic campaign/journal/adapter integration; preserve every fixture root.

Socket/DNS and real sleep guards are installed before repository imports. All
metadata, observation pages and execution approvals here are synthetic fixtures,
not decisions or authorization for a real provider request.
"""
import copy
from contextlib import redirect_stdout
from datetime import timedelta
import inspect
import io
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

NETWORK_ATTEMPTS = []
SLEEP_ATTEMPTS = []


def deny_network(event, args):
    if event.startswith("socket."):
        NETWORK_ATTEMPTS.append(event)
        raise AssertionError("Campaign integration forbids socket/DNS activity")


def deny_sleep(seconds):
    SLEEP_ATTEMPTS.append(seconds)
    raise AssertionError("Campaign integration forbids real sleep")


sys.addaudithook(deny_network)
SLEEP_PATCH = patch("time.sleep", deny_sleep)
SLEEP_PATCH.start()
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from dendra.history_acquisition import campaign, campaign_cli, campaign_execution
from dendra.history_acquisition import dimensionless_probe as probe
from dendra.history_acquisition import eligibility, provider_adapter, provider_metadata
from dendra.history_acquisition.journal import Journal
from dendra.history_acquisition.model import Inventory, INVENTORY_SHA256, source_binding
from dendra.history_acquisition.safety import Hold, decode, digest, encode, sha
from dendra.transport import format_utc, parse_utc

NOW = "2026-09-26T14:00:00.000Z"
START = "2024-02-29T00:00:00.000Z"
MIDDLE = "2024-03-01T00:00:00.000Z"
END = "2024-03-02T00:00:00.000Z"
PERCENT = "63531a67a9b61453fa1ca4ed"
VWC = "5d8e42e72da5c3cc53f6531d"


def setUpModule():
    global INVENTORY, FINGERPRINT, TEST_ROOT
    INVENTORY = Inventory.load(os.environ["DENDRA_INVENTORY"], INVENTORY_SHA256)
    FINGERPRINT = digest(source_binding())
    TEST_ROOT = Path(os.environ["DENDRA_TEST_ROOT"])
    if not TEST_ROOT.is_absolute() or not TEST_ROOT.is_dir():
        raise ValueError("Explicit existing task-owned test root required")


def tearDownModule():
    SLEEP_PATCH.stop()
    if NETWORK_ATTEMPTS or SLEEP_ATTEMPTS:
        raise AssertionError(f"Forbidden activity: sockets={NETWORK_ATTEMPTS}, sleep={SLEEP_ATTEMPTS}")


class Clock:
    def __init__(self):
        self.seconds = 0.0

    def now(self):
        return format_utc(parse_utc(NOW) + timedelta(seconds=self.seconds))

    def monotonic(self):
        return self.seconds

    def advance(self, seconds):
        self.seconds += seconds


class Reply(io.BytesIO):
    def __init__(self, body=b"", status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = {} if headers is None else headers

    def read(self, size=-1):
        if not 0 < signal.getitimer(signal.ITIMER_REAL)[0] <= 25:
            raise AssertionError("No total deadline during synthetic response read")
        if not 0 <= size <= 65536:
            raise AssertionError("Synthetic provider read was not bounded")
        return super().read(size)


class Crash(BaseException):
    """Abrupt synthetic process interruption, not a retryable transport error."""


class FiniteExecutor:
    """Finite in-memory bytes; inspect on-disk reservation before every dispatch."""
    def __init__(self, journal, clock, replies):
        self.journal, self.clock = journal, clock
        self.replies = list(replies)
        self.calls, self.waits = [], []
        self.in_call = False

    def __call__(self, request, timeout):
        if self.in_call:
            raise AssertionError("Provider concurrency exceeded one")
        self.in_call = True
        try:
            events = self.journal.events
            if not events or events[-1]["kind"] != "started":
                raise AssertionError("Dispatch before durable reserve/start")
            record = events[-1]
            path = self.journal._event_path(record["sequence"])
            if decode(self.journal.fs.read(path, 65536)) != record:
                raise AssertionError("Started event is not durably persisted")
            attempt = self.journal.snapshot()["attempts"][record["data"]["attempt_key"]]
            if attempt["state"] != "started":
                raise AssertionError("Attempt state differs from durable start")
            if not 0 < timeout <= 25:
                raise AssertionError("Unbounded synthetic request deadline")
            if not 0 < signal.getitimer(signal.ITIMER_REAL)[0] <= 25:
                raise AssertionError("Total deadline absent before synthetic dispatch")
            self.calls.append((request.full_url, timeout, attempt["attempt_key"]))
            if not self.replies:
                raise AssertionError("Finite synthetic responses exhausted")
            response = self.replies.pop(0)
            self.clock.advance(0.01)
            if isinstance(response, BaseException):
                raise response
            return Reply(response) if isinstance(response, bytes) else response
        finally:
            self.in_call = False

    def wait(self, seconds):
        self.waits.append(seconds)
        self.clock.advance(seconds)


def page(rows=(), limit=2016):
    return encode(dict(data=list(rows), limit=limit))


def row(t, value=0, **extra):
    return dict(t=t, v=value, **extra)


def bundle(sid=probe.STREAM, *, start=START, end=END, disposition="ACCEPT_NATIVE"):
    identity = INVENTORY.identity(sid)
    attributes = {}
    if identity["depth_cm"] is not None:
        attributes["depth"] = dict(value=identity["depth_cm"], unit="Centimeter")
    if identity["orientation"] is not None:
        attributes["orientation"] = identity["orientation"]
    station = dict(_id=identity["station_id"], public_level=3, is_hidden=False,
                   is_geo_protected=False, name="Synthetic integration station")
    admitted = provider_metadata._parse_station(encode(station), identity["station_id"],
                                                checked_at=NOW, now=NOW)
    source = dict(_id=sid, station_id=identity["station_id"], public_level=3,
                  is_hidden=False, is_geo_protected=False, attributes=attributes,
                  terms=dict(dt=dict(Unit=identity["native_unit"]),
                             ds=dict(Medium="Soil", Variable="VolumetricWaterContent")),
                  datapoints_config=[dict(begins_at=start)])
    packet = probe.review_packet(encode(dict(data=[source], limit=500, total=1, skip=0)),
        admitted, INVENTORY, stream_id=sid, metadata_profile=probe.CAMPAIGN_TEMPORAL_PROFILE,
        checked_at=NOW, now=NOW)
    body = encode(packet)
    review = eligibility.propose(INVENTORY, body, packet_sha256=sha(body),
        packet_source_fingerprint=packet["metadata_binding"]["collector_fingerprint"],
        executor_fingerprint=FINGERPRINT, start=start, end=end)
    if disposition != "PENDING":
        review.update(disposition=disposition, reviewer_ref="synthetic-integration-review",
            reviewed_at=NOW, expires_at="2026-09-27T14:00:00.000Z",
            acknowledgements=list(eligibility.ACKNOWLEDGEMENTS))
    decision = eligibility.decide(INVENTORY, body, review, executor_fingerprint=FINGERPRINT, now=NOW)
    return dict(packet=packet, review=review, decision=decision)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + "-", dir=TEST_ROOT))
        self.clock = Clock()
        self.journals = []

    def tearDown(self):
        for journal in reversed(self.journals):
            journal.close()

    def fixture(self, *, selected=(probe.STREAM,), end=MIDDLE, attempts=12):
        bundles = {sid: bundle(sid, end=end) for sid in selected}
        manifest = campaign.make_campaign(INVENTORY, campaign_id="synthetic-integration",
            executor_fingerprint=FINGERPRINT,
            horizons={sid: dict(start=START, end=end) for sid in selected}, chunk_days=1,
            decisions={sid: b["decision"] for sid, b in bundles.items()},
            budgets=campaign.policy(logical_requests=attempts, attempts=attempts,
                                   total_bytes=32*1024**2, wall_seconds=300))
        binding, tasks = campaign_execution.prepare(manifest, INVENTORY, bundles, now=NOW)
        return manifest, bundles, binding, tasks

    def open(self, binding, tasks, *, create=True, root=None):
        journal = Journal(self.root if root is None else root, binding, tasks, inventory=INVENTORY,
                          create=create, now=self.clock.now, monotonic=self.clock.monotonic)
        self.journals.append(journal)
        return journal

    def close(self, journal):
        journal.close()
        self.journals.remove(journal)

    def run_fake(self, journal, replies, *, keys=None):
        fake = FiniteExecutor(journal, self.clock, replies)
        result = provider_adapter.CampaignAdapter(journal).run(executor=fake, wait=fake.wait,
            window_end=format_utc(parse_utc(NOW)+timedelta(seconds=290)), task_keys=keys)
        return result, fake

    def test_prepare_reuses_logical_ids_and_full_roster_for_all_three_native_routes(self):
        manifest, bundles, binding, tasks = self.fixture(selected=(PERCENT, VWC, probe.STREAM))
        planned = campaign.plan(manifest, INVENTORY, now=NOW)
        self.assertEqual(set(tasks), {t["task_id"] for t in planned["tasks"]})
        self.assertEqual(binding["mode"], "campaign_reviewed_adapter")
        self.assertEqual(len(binding["roster"]), 434)
        self.assertEqual(len({v["station_id"] for v in binding["roster"].values()}), 122)
        for sid, item in bundles.items():
            self.assertTrue(item["decision"]["native_acquisition_eligible"])
            self.assertEqual(item["decision"]["normalized_conversion_eligible"], sid != probe.STREAM)
        self.assertEqual(campaign_execution.prepare(manifest, INVENTORY, bundles, now=NOW), (binding, tasks))

    def test_missing_pending_stale_or_tampered_decisions_refuse_before_dispatch(self):
        manifest, bundles, _, _ = self.fixture()
        altered = copy.deepcopy(bundles)
        altered[probe.STREAM]["decision"]["stream_id"] = PERCENT
        changed_packet = copy.deepcopy(bundles)
        changed_packet[probe.STREAM]["packet"]["configuration_sha256"] = "f"*64
        wrong_source = copy.deepcopy(bundles)
        wrong_source[probe.STREAM]["decision"]["executor_fingerprint"] = "f"*64
        held = {probe.STREAM: bundle(end=MIDDLE, disposition="HOLD")}
        for label, value, now in (("missing", {}, NOW), ("wrong_stream", altered, NOW),
                ("packet_changed", changed_packet, NOW), ("source_changed", wrong_source, NOW),
                ("review_hold", held, NOW), ("stale", bundles, "2026-09-28T14:00:00Z")):
            with self.subTest(label=label), self.assertRaises(Hold):
                campaign_execution.prepare(manifest, INVENTORY, value, now=now)
        self.assertEqual(NETWORK_ATTEMPTS, [])

    def test_durable_reservation_before_dispatch_and_complete_empty_resume(self):
        _, _, binding, tasks = self.fixture()
        journal = self.open(binding, tasks)
        _, fake = self.run_fake(journal, [page()])
        key = next(iter(tasks))
        state = journal.snapshot()
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(state["counters"]["attempts"], 1)
        self.assertEqual(state["counters"]["logical_requests"], 1)
        self.assertEqual(state["intervals"][key]["state"], "complete_empty")
        self.assertEqual(journal.completed(key)["rows"], [])
        self.close(journal)
        resumed = self.open(binding, tasks, create=False)
        _, replay = self.run_fake(resumed, [])
        self.assertEqual(replay.calls, [])
        self.assertEqual(resumed.snapshot()["counters"]["attempts"], 1)

    def test_multi_page_native_conflicts_zero_and_quality_are_preserved(self):
        _, _, binding, tasks = self.fixture()
        journal = self.open(binding, tasks)
        times = [format_utc(parse_utc(START)+timedelta(minutes=i)) for i in (1, 2, 3)]
        replies = [page([row(times[0], 0), row(times[1], 1, q="a")], limit=2),
                   page([row(times[1], 9, q="b"), row(times[2], 2)], limit=2),
                   page([row(times[2], 2)], limit=2)]
        _, fake = self.run_fake(journal, replies)
        envelope = journal.completed(next(iter(tasks)))
        self.assertEqual(envelope["page_count"], 3)
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(envelope["rows"][0]["v"], 0)
        self.assertTrue(envelope["rows"][1]["duplicate_conflict"])
        self.assertTrue(envelope["rows"][1]["source_quality_conflict"])
        self.assertEqual(envelope["diagnostics"]["conflicting_timestamps"], 1)
        self.assertEqual(envelope["diagnostics"]["duplicate_rows"], 2)
        self.assertEqual(journal.snapshot()["counters"]["source_rows"], 5)
        self.assertEqual(journal.snapshot()["counters"]["response_bytes"], sum(map(len, replies)))
        for (_, _, attempt_key), expected in zip(fake.calls, replies):
            attempt = journal.snapshot()["attempts"][attempt_key]
            self.assertEqual(attempt["response_sha256"], sha(expected))

    def test_raw_null_missing_and_zero_are_not_science_or_conversion(self):
        _, _, binding, tasks = self.fixture(selected=(VWC,))
        journal = self.open(binding, tasks)
        times = [format_utc(parse_utc(START)+timedelta(minutes=i)) for i in (1, 2, 3)]
        self.run_fake(journal, [page([row(times[0], 0), row(times[1], None), dict(t=times[2])])])
        saved = journal.completed(next(iter(tasks)))
        self.assertEqual([r["value_status"] for r in saved["rows"]], ["number", "null", "missing"])
        self.assertEqual(saved["rows"][0]["v"], 0)
        self.assertNotIn("v", saved["rows"][2])
        self.assertFalse(journal.snapshot()["authoritative_for_publication"])

    def test_unknown_task_and_changed_interval_refuse_without_execution(self):
        _, _, binding, tasks = self.fixture()
        journal = self.open(binding, tasks)
        fake = FiniteExecutor(journal, self.clock, [])
        with self.assertRaises(Hold):
            provider_adapter.CampaignAdapter(journal).run(executor=fake, wait=fake.wait,
                window_end=format_utc(parse_utc(NOW)+timedelta(seconds=290)), task_keys=["f"*64])
        self.assertEqual(fake.calls, [])
        self.assertEqual(journal.snapshot()["counters"]["attempts"], 0)
        changed = copy.deepcopy(tasks)
        changed[next(iter(changed))]["end"] = END
        with self.assertRaises(campaign_execution.Stop):
            self.open(binding, changed, root=Path(tempfile.mkdtemp(prefix="changed-task-", dir=TEST_ROOT)))

    def test_provider_concurrency_is_serial_and_not_a_worker_argument(self):
        _, _, binding, tasks = self.fixture(selected=(PERCENT, VWC, probe.STREAM))
        journal = self.open(binding, tasks)
        _, fake = self.run_fake(journal, [page(), page(), page()])
        self.assertEqual(len(fake.calls), 3)
        self.assertFalse(fake.in_call)
        for function in (campaign_execution.prepare, provider_adapter.CampaignAdapter.run):
            parameters = inspect.signature(function).parameters
            self.assertFalse({"agent_count", "workers", "concurrency"} & set(parameters))
        for url, _, _ in fake.calls:
            parsed = urlsplit(url)
            query = parse_qs(parsed.query)
            self.assertEqual(parsed.path, "/v2/datapoints")
            self.assertEqual(query["$sort[time]"], ["1"])
            self.assertEqual(query["$limit"], ["2016"])

    def test_first_429_and_transport_failures_pause_without_retry_or_resume_refund(self):
        for status in (429, 503):
            with self.subTest(status=status):
                root = Path(tempfile.mkdtemp(prefix=f"status-{status}-", dir=TEST_ROOT))
                _, _, binding, tasks = self.fixture(end=END)
                journal = self.open(binding, tasks, root=root)
                fake = FiniteExecutor(journal, self.clock,
                                      [Reply(b"", status=status, headers={"Retry-After": "1"}), page()])
                with self.assertRaises(Hold):
                    provider_adapter.CampaignAdapter(journal).run(executor=fake, wait=fake.wait,
                        window_end=format_utc(parse_utc(NOW)+timedelta(seconds=290)))
                self.assertEqual(len(fake.calls), 1)
                self.assertEqual(fake.waits, [])
                self.assertEqual(journal.snapshot()["counters"]["attempts"], 1)
                self.close(journal)
                reopened = self.open(binding, tasks, create=False, root=root)
                never = FiniteExecutor(reopened, self.clock, [])
                with self.assertRaises(Hold):
                    provider_adapter.CampaignAdapter(reopened).run(executor=never, wait=never.wait,
                        window_end=format_utc(parse_utc(NOW)+timedelta(seconds=290)))
                self.assertEqual(never.calls, [])
                self.assertEqual(reopened.snapshot()["counters"]["attempts"], 1)

    def test_accounted_task_hold_preserves_unrelated_complete_empty_and_partial_summary(self):
        _, _, binding, tasks = self.fixture(selected=(PERCENT, VWC))
        journal = self.open(binding, tasks)
        keys = list(tasks)
        result, fake = self.run_fake(journal, [Reply(b"", status=403), page()], keys=keys)
        self.assertEqual(len(fake.calls), 2)
        self.assertTrue(result[keys[0]]["held"])
        self.assertIsNone(journal.completed(keys[0]))
        self.assertEqual(journal.completed(keys[1])["rows"], [])
        summary = campaign_execution.summary(journal)
        self.assertEqual((summary["station_count"], summary["stream_count"]), (122, 434))
        self.assertEqual(len(summary["streams"]), 434)
        self.assertEqual(sum(s["task_count"] for s in summary["streams"].values()), 2)
        self.assertEqual(sum(s["sealed_count"] for s in summary["streams"].values()), 1)
        self.assertEqual(sum(s["covered_empty"] for s in summary["streams"].values()), 1)
        self.assertEqual(summary["counters"]["attempts"], 2)
        self.assertFalse(summary["publication_eligible"])

    def test_unknown_schema_stops_after_durable_receipt_before_unrelated_dispatch(self):
        _, _, binding, tasks = self.fixture(selected=(PERCENT, VWC))
        journal = self.open(binding, tasks)
        body = encode(dict(data=[row(START)], limit=2016, unexpected="synthetic-schema-change"))
        fake = FiniteExecutor(journal, self.clock, [body, page()])
        with self.assertRaises(campaign_execution.Stop):
            provider_adapter.CampaignAdapter(journal).run(executor=fake, wait=fake.wait,
                window_end=format_utc(parse_utc(NOW)+timedelta(seconds=290)))
        self.assertEqual(len(fake.calls), 1)
        state = journal.snapshot()
        self.assertEqual(state["counters"]["attempts"], 1)
        self.assertEqual(state["counters"]["response_bytes"], len(body))
        self.assertTrue(any(e["kind"] == "received" for e in journal.events))
        self.assertTrue(all(journal.completed(key) is None for key in tasks))

    def test_incomplete_pagination_never_seals_or_replays_automatically(self):
        _, _, binding, tasks = self.fixture()
        journal = self.open(binding, tasks)
        t = format_utc(parse_utc(START)+timedelta(minutes=1))
        fake = FiniteExecutor(journal, self.clock, [page([row(t), row(t)], limit=2),
                                                   page([row(t), row(t)], limit=2)])
        result = provider_adapter.CampaignAdapter(journal).run(executor=fake, wait=fake.wait,
            window_end=format_utc(parse_utc(NOW)+timedelta(seconds=290)))
        key = next(iter(tasks))
        self.assertTrue(result[key]["held"])
        self.assertIsNone(journal.completed(key))
        self.assertEqual(len(fake.calls), 2)
        before = journal.snapshot()["counters"]["attempts"]
        self.close(journal)
        reopened = self.open(binding, tasks, create=False)
        result, resumed = self.run_fake(reopened, [])
        self.assertTrue(result[key]["held"])
        self.assertEqual(resumed.calls, [])
        self.assertEqual(reopened.snapshot()["counters"]["attempts"], before)

    def test_seven_crash_boundaries_preserve_spent_attempts_and_verified_coverage(self):
        stages = ("before_reservation", "reserved_before_dispatch", "dispatch_response_unknown",
                  "response_before_receipt", "receipt_before_archive", "archive_before_seal",
                  "seal_before_summary")
        for stage in stages:
            with self.subTest(stage=stage):
                root = Path(tempfile.mkdtemp(prefix=stage+"-", dir=TEST_ROOT))
                _, _, binding, tasks = self.fixture()
                journal = self.open(binding, tasks, root=root)
                key = next(iter(tasks))
                fake = FiniteExecutor(journal, self.clock,
                    [Crash(stage)] if stage == "dispatch_response_unknown" else [page()])
                if stage == "before_reservation":
                    target, replacement = "reserve", lambda *a, **k: (_ for _ in ()).throw(Crash(stage))
                elif stage == "reserved_before_dispatch":
                    target, replacement = "started", lambda *a, **k: (_ for _ in ()).throw(Crash(stage))
                elif stage in ("dispatch_response_unknown", "response_before_receipt"):
                    target, replacement = "received", lambda *a, **k: (_ for _ in ()).throw(Crash(stage))
                elif stage == "receipt_before_archive":
                    target, replacement = "seal", lambda *a, **k: (_ for _ in ()).throw(Crash(stage))
                elif stage == "archive_before_seal":
                    target = "_append"
                    original = journal._append
                    def replacement(kind, data):
                        if kind == "sealed":
                            raise Crash(stage)
                        return original(kind, data)
                else:
                    target = "seal"
                    original = journal.seal
                    def replacement(*args, **kwargs):
                        original(*args, **kwargs)
                        raise Crash(stage)
                with patch.object(journal, target, side_effect=replacement), self.assertRaises(Crash):
                    provider_adapter.CampaignAdapter(journal).run(executor=fake, wait=fake.wait,
                        window_end=format_utc(parse_utc(NOW)+timedelta(seconds=290)))
                expected_attempts = 0 if stage == "before_reservation" else 1
                expected_calls = 0 if stage in ("before_reservation", "reserved_before_dispatch") else 1
                self.assertEqual(journal.snapshot()["counters"]["attempts"], expected_attempts)
                self.assertEqual(len(fake.calls), expected_calls)
                before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
                self.close(journal)
                reopened = self.open(binding, tasks, create=False, root=root)
                state = campaign_execution.summary(reopened)
                self.assertEqual(state["counters"]["attempts"], expected_attempts)
                if stage == "before_reservation":
                    _, resumed = self.run_fake(reopened, [page()])
                    self.assertEqual(len(resumed.calls), 1)
                    self.assertEqual(reopened.snapshot()["counters"]["attempts"], 1)
                elif stage == "seal_before_summary":
                    self.assertEqual(state["recovery"][key], "REUSE_SEAL_NO_REQUEST")
                    _, resumed = self.run_fake(reopened, [])
                    self.assertEqual(resumed.calls, [])
                    self.assertEqual(reopened.snapshot()["counters"]["attempts"], 1)
                    self.assertEqual(reopened.completed(key)["rows"], [])
                else:
                    self.assertEqual(state["recovery"][key],
                                     "UNSEALED_ATTEMPT_OPERATOR_REVIEW_NO_AUTOMATIC_REQUEST")
                    with self.assertRaises(Hold):
                        self.run_fake(reopened, [])
                    self.assertIsNone(reopened.completed(key))
                    self.assertEqual(reopened.snapshot()["counters"]["attempts"], 1)
                # Existing immutable evidence survives every resume outcome.
                for path, body in before.items():
                    self.assertEqual((root/path).read_bytes(), body)

    def test_old_source_refuses_execution_but_allows_immutable_readonly_inspection(self):
        _, _, binding, tasks = self.fixture()
        journal = self.open(binding, tasks)
        self.run_fake(journal, [page()])
        self.close(journal)
        saved = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        changed_sources = dict(source_binding())
        changed_sources[next(iter(changed_sources))] = "f"*64
        with patch.object(campaign_execution, "source_binding", return_value=changed_sources):
            with self.assertRaises(campaign_execution.Stop):
                self.open(binding, tasks, create=False)
            summary = campaign_execution.inspect_state(self.root, binding["campaign_id"])
            self.assertFalse(summary["source_compatible"])
            self.assertTrue(summary["inspection_only"])
            self.assertEqual(summary["task_status_counts"], {"complete_empty": 1})
            self.assertEqual(summary["stream_count"], 434)
        self.assertEqual({p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}, saved)

    def test_cumulative_attempt_budget_and_readonly_mode_refuse_new_dispatch(self):
        _, _, binding, tasks = self.fixture(end=END, attempts=1)
        journal = self.open(binding, tasks)
        keys = list(tasks)
        self.run_fake(journal, [page()], keys=keys[:1])
        fake = FiniteExecutor(journal, self.clock, [])
        result = provider_adapter.CampaignAdapter(journal).run(executor=fake, wait=fake.wait,
            window_end=format_utc(parse_utc(NOW)+timedelta(seconds=290)), task_keys=keys[1:])
        self.assertTrue(result[keys[1]]["held"])
        self.assertEqual(fake.calls, [])
        self.assertEqual(journal.snapshot()["counters"]["attempts"], 1)
        self.close(journal)
        with Journal(self.root, binding, tasks, inspect_only=True) as readonly:
            with self.assertRaises(Hold):
                provider_adapter.CampaignAdapter(readonly)
            with self.assertRaises(Hold):
                readonly.session()

    def test_fresh_permission_is_rechecked_after_preparation_before_reservation(self):
        manifest, bundles, _, _ = self.fixture()
        item = bundles[probe.STREAM]
        item["review"]["expires_at"] = "2026-09-26T14:00:01.000Z"
        item["decision"] = eligibility.decide(INVENTORY, encode(item["packet"]), item["review"],
                                              executor_fingerprint=FINGERPRINT, now=NOW)
        manifest = campaign.make_campaign(INVENTORY, campaign_id=manifest["core"]["campaign_id"],
            executor_fingerprint=FINGERPRINT, horizons=manifest["core"]["horizons"], chunk_days=1,
            decisions={probe.STREAM: item["decision"]}, budgets=manifest["budgets"])
        binding, tasks = campaign_execution.prepare(manifest, INVENTORY, bundles, now=NOW)
        journal = self.open(binding, tasks)
        self.clock.advance(2)
        result, fake = self.run_fake(journal, [])
        self.assertTrue(result[next(iter(tasks))]["held"])
        self.assertEqual(fake.calls, [])
        self.assertEqual(journal.snapshot()["counters"]["attempts"], 0)

    def invoke(self, args, **kwargs):
        with redirect_stdout(io.StringIO()) as output:
            code = campaign_cli.main(args, **kwargs)
        return code, decode(output.getvalue().encode())

    def test_cli_plan_status_collect_and_resume_use_the_genuine_reviewed_path(self):
        _, bundles, _, _ = self.fixture()
        item = bundles[probe.STREAM]
        packet_path, review_path, config_path = [self.root/name for name in
                                                ("packet.json", "review.json", "config.json")]
        packet_path.write_bytes(encode(item["packet"]))
        review_bytes = encode(item["review"])
        review_path.write_bytes(review_bytes)
        limits = campaign.policy(logical_requests=3, attempts=3, total_bytes=8*1024**2, wall_seconds=300)
        config_path.write_bytes(encode(dict(campaign_id="synthetic-cli-integrated", chunk_days=1,
            horizons={probe.STREAM: dict(start=START, end=MIDDLE)}, budgets=limits,
            reviews=[dict(packet_path=str(packet_path), review_path=str(review_path), review_sha256=sha(review_bytes))])))
        common = ["--inventory", os.environ["DENDRA_INVENTORY"], "--inventory-sha256", INVENTORY_SHA256]
        code, planned = self.invoke(["plan", *common, "--config", str(config_path),
            "--state-root", str(self.root), "--now", NOW, "--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(planned["provider_requests"], 0)
        plan_path = self.root/planned["plan_path"]
        execution_path = plan_path.with_name("execution.json")
        execution = decode(execution_path.read_bytes())
        binding = execution["binding"]
        for mode in ("status", "verify"):
            with self.subTest(mode=mode):
                code, inspected = self.invoke([mode, *common, "--manifest", str(plan_path.with_name("manifest.json"))])
                self.assertEqual(code, 0)
                self.assertEqual(inspected["provider_requests"], 0)
        authorization_path = self.root/"synthetic-dispatch-authorization.json"
        authorization_path.write_bytes(encode(dict(schema_version="dendra-campaign-dispatch-1",
            approval_reference="synthetic-offline-injection-only", binding_sha256=digest(binding),
            task_root=str(self.root), window_start=NOW,
            window_end=format_utc(parse_utc(NOW)+timedelta(seconds=290)))))
        dispatch = [*common, "--execution", str(execution_path), "--authorization", str(authorization_path),
            "--state-root", str(self.root), "--logical-requests", "3", "--http-attempts", "3",
            "--total-bytes", str(8*1024**2), "--wall-seconds", "300", "--now", NOW]
        calls = []
        def execute(request, timeout):
            event_root = self.root/"campaigns"/binding["campaign_id"]/"events"
            files = sorted(event_root.glob("*/*.json"))
            latest = decode(files[-1].read_bytes())
            previous = decode(files[-2].read_bytes())
            self.assertEqual((previous["kind"], latest["kind"]), ("reserved", "started"))
            self.assertEqual(previous["data"]["attempt_key"], latest["data"]["attempt_key"])
            self.assertTrue(0 < timeout <= 25)
            calls.append(request.full_url)
            self.clock.advance(0.01)
            return Reply(page())
        dependencies = dict(executor=execute, wait=self.clock.advance,
                            now_fn=self.clock.now, monotonic=self.clock.monotonic)
        wrong_budget = list(dispatch)
        wrong_budget[wrong_budget.index("--http-attempts")+1] = "4"
        code, rejected = self.invoke(["collect", *wrong_budget], **dependencies)
        self.assertEqual(code, campaign_cli.EXIT_HOLD)
        self.assertEqual(rejected["outcome"], "HOLD")
        self.assertEqual(calls, [])
        self.assertFalse((self.root/"registry").exists())
        # A synthetic --now cannot be used to enter the real provider path.
        with patch.object(provider_adapter, "anonymous_executor", side_effect=AssertionError("Live construction forbidden")):
            code, rejected = self.invoke(["collect", *dispatch])
        self.assertEqual(code, campaign_cli.EXIT_HOLD)
        self.assertEqual(calls, [])
        self.assertFalse((self.root/"registry").exists())
        code, collected = self.invoke(["collect", *dispatch], **dependencies)
        self.assertEqual(code, 0)
        self.assertEqual(collected["outcome"], "SUCCESS")
        self.assertTrue(collected["synthetic_transport"])
        self.assertEqual(collected["status"]["counters"]["attempts"], 1)
        self.assertEqual(len(calls), 1)
        code, resumed = self.invoke(["resume", *dispatch], **dependencies)
        self.assertEqual(code, 0)
        self.assertEqual(resumed["status"]["counters"]["attempts"], 1)
        self.assertEqual(len(calls), 1)
        for mode in ("status", "verify"):
            code, inspected = self.invoke([mode, *common, "--state-root", str(self.root),
                                           "--campaign-id", binding["campaign_id"]])
            self.assertEqual(code, 0)
            self.assertEqual(inspected["stream_count"], 434)
            self.assertEqual(inspected["task_status_counts"], {"complete_empty": 1})
            self.assertEqual(inspected["provider_requests"], 0)
            self.assertTrue(inspected["inspection_only"])

    def test_cli_collect_without_eligibility_or_explicit_authority_never_calls_executor(self):
        calls = []
        def forbidden(*args, **kwargs):
            calls.append(args)
            raise AssertionError("No execution is authorized by this fixture")
        for mode in ("collect", "resume", "prepare-product"):
            code, response = self.invoke([mode], executor=forbidden, wait=forbidden)
            self.assertEqual(code, campaign_cli.EXIT_STOP)
            self.assertEqual(response["provider_requests"], 0)
        _, _, binding, tasks = self.fixture()
        descriptor = dict(binding=copy.deepcopy(binding), tasks=tasks)
        descriptor["binding"]["reviewed_bundles"][probe.STREAM]["decision"]["native_acquisition_eligible"] = False
        execution_path = self.root/"tampered-execution.json"
        execution_path.write_bytes(encode(descriptor))
        auth_path = self.root/"authorization.json"
        auth_path.write_bytes(encode(dict(schema_version="dendra-campaign-dispatch-1",
            approval_reference="synthetic-refusal-only", binding_sha256=digest(descriptor["binding"]),
            task_root=str(self.root), window_start=NOW,
            window_end=format_utc(parse_utc(NOW)+timedelta(seconds=290)))))
        code, rejected = self.invoke(["collect", "--inventory", os.environ["DENDRA_INVENTORY"],
            "--inventory-sha256", INVENTORY_SHA256, "--execution", str(execution_path),
            "--authorization", str(auth_path), "--state-root", str(self.root), "--now", NOW,
            "--logical-requests", "12", "--http-attempts", "12", "--total-bytes", str(32*1024**2),
            "--wall-seconds", "300"], executor=forbidden, wait=forbidden,
            now_fn=self.clock.now, monotonic=self.clock.monotonic)
        self.assertEqual(code, campaign_cli.EXIT_STOP)
        self.assertEqual(rejected["outcome"], "STOP")
        self.assertEqual(calls, [])
        self.assertFalse((self.root/"registry").exists())

    def test_cli_plan_continuation_does_not_create_execution_descriptor(self):
        _, bundles, _, _ = self.fixture(end=END)
        item = bundles[probe.STREAM]
        packet_path, review_path, config_path = [self.root/name for name in
                                                ("packet.json", "review.json", "config.json")]
        packet_path.write_bytes(encode(item["packet"]))
        review_body = encode(item["review"])
        review_path.write_bytes(review_body)
        config_path.write_bytes(encode(dict(campaign_id="synthetic-continuation", chunk_days=1,
            horizons={probe.STREAM: dict(start=START, end=END)},
            budgets=campaign.policy(logical_requests=6, attempts=6, total_bytes=16*1024**2, wall_seconds=300),
            reviews=[dict(packet_path=str(packet_path), review_path=str(review_path), review_sha256=sha(review_body))])))
        args = ["plan", "--inventory", os.environ["DENDRA_INVENTORY"],
            "--inventory-sha256", INVENTORY_SHA256, "--config", str(config_path),
            "--state-root", str(self.root), "--now", NOW, "--dry-run"]
        code, first = self.invoke([*args, "--max-tasks", "1"])
        self.assertEqual(code, 0)
        first_path = self.root/first["plan_path"]
        first_plan = decode(first_path.read_bytes())
        self.assertEqual(len(first_plan["tasks"]), 1)
        self.assertFalse(first_path.with_name("execution.json").exists())
        code, second = self.invoke([*args, "--after-task", first_plan["next_after_task"]])
        self.assertEqual(code, 0)
        second_path = self.root/second["plan_path"]
        second_plan = decode(second_path.read_bytes())
        self.assertEqual(len(second_plan["tasks"]), 1)
        self.assertNotEqual(first_plan["tasks"][0]["task_id"], second_plan["tasks"][0]["task_id"])
        self.assertFalse(second_path.with_name("execution.json").exists())


    def test_first_batch_missing_baseline_review_is_not_ready_and_scale_is_separate(self):
        manifest, _, _, _ = self.fixture()
        selected = (PERCENT, VWC, probe.STREAM)
        tasks = [dict(proposed_ordinal=i, station_id=INVENTORY.identity(sid)["station_id"],
                      stream_id=sid, native_unit=INVENTORY.identity(sid)["native_unit"],
                      start=START, end=MIDDLE) for i, sid in enumerate(selected)]
        result = campaign_execution.first_batch_readiness(tasks, manifest, INVENTORY, now=NOW)
        self.assertFalse(result["eligibility_complete"])
        self.assertEqual([r["status"] for r in result["tasks"]],
            ["NEEDS_FRESH_METADATA_REVIEW", "NEEDS_FRESH_METADATA_REVIEW", "HOLD_SCALE_ONLY_BUT_NATIVE_ELIGIBLE"])
        self.assertIsNotNone(result["tasks"][-1]["task_id"])
        expired = campaign_execution.first_batch_readiness(tasks, manifest, INVENTORY,
                                                           now="2026-09-28T00:00:00Z")
        self.assertEqual(expired["tasks"][-1]["status"], "HOLD_ACCESS")
        full, _, _, _ = self.fixture(selected=selected)
        ready = campaign_execution.first_batch_readiness(tasks, full, INVENTORY, now=NOW)
        self.assertTrue(ready["eligibility_complete"])
        self.assertFalse(ready["network_execution_authorized"])


if __name__ == "__main__":
    unittest.main()
