"""Offline mocks only. Run with an OS network-denial policy and external TMPDIR."""

import http.client
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from soil_moisture import snotel_history as history
from soil_moisture import snotel_transport as transport


def spec(number=1):
    triplet = f"{90000 + number}:CA:SNTL"
    elements = [dict(elementCode="SMS", heightDepth=-d, ordinal=1, durationName="DAILY",
                     storedUnitCode="pct", originalUnitCode="pct") for d in (2, 8, 20)]
    metadata = history.canonical([dict(stationTriplet=triplet, networkCode="SNTL",
                                       dataTimeZone=-8, stationElements=elements)])
    params = dict(stationTriplets=triplet, elements="SMS:-2:1,SMS:-8:1,SMS:-20:1",
                  duration="DAILY", beginDate="2026-10-01", endDate="2026-10-02",
                  periodRef="END", returnFlags="true", returnOriginalValues="true",
                  returnSuspectData="true")
    return dict(metadata_body=metadata, metadata_sha256=history.sha256(metadata),
                request=dict(method="GET", url=history.TARGET + "?" + urlencode(params), parameters=params))


class Response:
    def __init__(self, body=b"[]", status=200, headers=None, read_hook=None):
        self.body, self.status, self.headers = io.BytesIO(body), status, headers or {}
        self.read_hook, self.requests, self.consumed, self.closed = read_hook, [], 0, False

    def read(self, count):
        self.requests.append(count)
        if self.read_hook:
            self.read_hook(self, count)
        chunk = self.body.read(count)
        self.consumed += len(chunk)
        return chunk

    def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.value = 1_790_812_800.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="snotel-transport-", dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "lane"
        self.clock = Clock()
        self.calls = []

    def client(self, responses=(), plan=None, **options):
        queue = iter(responses)

        def open_mock(request, timeout):
            checkpoint = json.loads((self.directory / "ledger.json").read_bytes())
            attempt = checkpoint["attempts"][-1]
            self.assertEqual(attempt["state"], "RESERVED")
            self.assertEqual(attempt["number"], len(checkpoint["attempts"]))
            self.calls.append(request.full_url)
            self.assertLessEqual(timeout, 30)
            value = next(queue)
            if isinstance(value, BaseException):
                raise value
            return value

        defaults = dict(opener=open_mock, wall=self.clock.now, monotonic=self.clock.now,
                        sleep=self.clock.sleep, retry_delay=0)
        defaults.update(options)
        return transport.StationTransport(self.directory, plan or [spec()], **defaults)

    def ledger(self):
        return json.loads((self.directory / "ledger.json").read_bytes())

    def test_offline_default_no_state_no_opener(self):
        client = transport.StationTransport(self.directory, [spec()])
        with patch("urllib.request.build_opener", side_effect=AssertionError("network")):
            with self.assertRaisesRegex(transport.Held, "offline default"):
                client.collect()
        self.assertFalse(self.directory.exists())

    def test_one_station_multiple_exact_elements_and_empty(self):
        client = self.client([Response()])
        captures = client.collect()
        self.assertEqual(len(captures), 1)
        self.assertTrue(captures[0]["receipt"]["complete"])
        archive = next(iter(client.station_histories().values()))
        self.assertEqual(len(archive["sensors"]), 3)
        self.assertTrue(all(s["height_depth"] < 0 for s in archive["sensors"]))
        self.assertEqual(history.source_timestamp("2026-10-01"), "2026-10-02T08:00:00Z")

    def test_zero_missing_suspect_original_and_flags_preserved(self):
        input_spec = spec()
        station = json.loads(input_spec["metadata_body"])[0]
        values = [dict(date="2026-10-01", value=0, qcFlag="V", origValue=0),
                  dict(date="2026-10-02", value=None, qcFlag="M")]
        body = history.canonical([dict(stationTriplet=station["stationTriplet"], data=[
            dict(stationElement=station["stationElements"][0], values=values),
            dict(stationElement=station["stationElements"][1], values=[
                dict(date="2026-10-01", qcFlag="S", origValue=12.5, origQcFlag="S")]),
            dict(stationElement=station["stationElements"][2], values=[])])])
        client = self.client([Response(body)])
        capture = client.collect()[0]
        self.assertEqual(capture["body"], body)
        archive = next(iter(client.station_histories().values()))
        states = {r["observation_state"] for r in archive["observations"]}
        self.assertEqual(states, {"OBSERVED", "EXPLICIT_MISSING", "SUSPECT_ORIGINAL_ONLY"})
        self.assertTrue(any(r["value_native"] == 0 for r in archive["observations"]))

    def test_32_logical_and_64_physical(self):
        plan = [spec(n) for n in range(32)]
        client = self.client([r for _ in plan for r in (Response(b"error", 500), Response())], plan)
        captures = client.collect()
        self.assertEqual(len(self.calls), 64)
        self.assertEqual(len(captures), 64)
        self.assertEqual(len(client.station_histories()), 32)

    def test_33_logical_rejected_without_io(self):
        with self.assertRaisesRegex(transport.Held, "32 logical"):
            self.client(plan=[spec(n) for n in range(33)])
        self.assertFalse(self.directory.exists())

    def test_65th_physical_rejected_before_open(self):
        plan = [spec(n) for n in range(32)]
        responses = [r for _ in range(31) for r in (Response(b"e", 500), Response())]
        client = self.client(responses + [Response(b"e", 500), Response(b"e", 500), Response()],
                             plan, retries=2)
        with self.assertRaisesRegex(transport.Held, "physical attempt limit"):
            client.collect()
        self.assertEqual(len(self.calls), 64)
        self.assertEqual(len(client.saved_captures()), 64)

    def test_limits_cannot_be_raised(self):
        for field, value in (("max_attempts", 65), ("max_response_bytes", transport.MIB + 1),
                             ("max_total_bytes", 64 * transport.MIB + 1), ("max_elapsed_seconds", 1801)):
            with self.subTest(field=field), self.assertRaises(transport.Held):
                self.client(**{field: value})

    def test_multistation_cartesian_and_metadata_request_rejected(self):
        for field, value in (("stationTriplets", "90001:CA:SNTL,90002:CA:SNTL"),
                             ("elements", "SMS:*:*"), ("returnFlags", "false")):
            s = spec(); s["request"]["parameters"][field] = value
            s["request"]["url"] = history.TARGET + "?" + urlencode(s["request"]["parameters"])
            with self.subTest(field=field), self.assertRaises(ValueError): self.client(plan=[s])
        s = spec(); s["request"]["url"] = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/stations"
        with self.assertRaises(ValueError): self.client(plan=[s])
        self.assertEqual(self.calls, [])

    def test_redirect_rejected_without_follow_and_body_charged(self):
        original = Response(b"redirect")
        error = urllib.error.HTTPError(history.TARGET, 302, "redirect", {"Location": "https://invalid.invalid"}, original)
        client = self.client([error])
        capture = client.collect()[0]
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(capture["receipt"]["total_consumed_body_bytes"], 8)
        self.assertFalse(capture["receipt"]["complete"])
        with self.assertRaises(urllib.error.HTTPError):
            transport._NoRedirect().redirect_request(
                transport.urllib.request.Request(history.TARGET), original, 302, "redirect", {},
                "https://invalid.invalid")

    def test_429_and_500_retry_bodies_all_charged(self):
        responses = [urllib.error.HTTPError(history.TARGET, 429, "limited", {"Retry-After": "1"}, Response(b"slow")),
                     Response(b"error", 500), Response()]
        client = self.client(responses, retries=2)
        captures = client.collect()
        self.assertEqual([c["receipt"]["total_consumed_body_bytes"] for c in captures], [4, 5, 2])
        self.assertEqual([c["receipt"]["complete"] for c in captures], [False, False, True])

    def test_near_cap_complete(self):
        response = Response(b"x" * (transport.MIB - 1))
        capture = self.client([response]).collect()[0]
        self.assertTrue(capture["receipt"]["complete"])
        self.assertEqual(response.consumed, transport.MIB - 1)
        self.assertTrue(all(0 < n <= 65536 for n in response.requests))

    def test_exact_cap_incomplete_receipt_is_adapter_compatible(self):
        response = Response(b" " * (transport.MIB - 2) + b"[]")
        client = self.client([response])
        with self.assertRaisesRegex(transport.Held, "without proven EOF"): client.collect()
        capture = client.saved_captures()[0]
        self.assertEqual(capture["receipt"]["response_bytes"], transport.MIB)
        self.assertEqual(capture["receipt"]["total_consumed_body_bytes"], transport.MIB)
        self.assertFalse(capture["receipt"]["complete"])
        self.assertEqual(response.consumed, transport.MIB)
        archive = next(iter(client.station_histories().values()))
        self.assertEqual(archive["observations"], [])

    def test_over_cap_never_reads_limit_plus_one(self):
        response = Response(b"x" * (transport.MIB + 1))
        client = self.client([response])
        with self.assertRaises(transport.Held): client.collect()
        self.assertEqual(response.consumed, transport.MIB)
        self.assertEqual(len(self.calls), 1)

    def test_declared_over_cap_rejected_before_body(self):
        response = Response(b"x", headers={"Content-Length": str(transport.MIB + 1)})
        client = self.client([response])
        with self.assertRaisesRegex(transport.Held, "declared body"): client.collect()
        self.assertEqual(response.requests, [])
        self.assertIsNone(client.saved_captures()[0]["receipt"]["total_consumed_body_bytes"])

    def test_aggregate_remaining_including_error_body(self):
        error, second = Response(b"1234567890", 500), Response(b"abcdef")
        client = self.client([error, second], max_total_bytes=15)
        with self.assertRaises(transport.Held): client.collect()
        self.assertEqual(error.consumed + second.consumed, 15)
        self.assertEqual(second.requests, [5])

    def test_per_station_archive_byte_bound_is_independent(self):
        plan = []
        for day in range(1, 10):
            s = spec()
            s["request"]["parameters"].update(beginDate=f"2026-09-{day:02d}", endDate=f"2026-09-{day:02d}")
            s["request"]["url"] = history.TARGET + "?" + urlencode(s["request"]["parameters"])
            plan.append(s)
        responses = [Response(b" " * (transport.MIB - 3) + b"[]") for _ in range(8)] + [Response(b"0123456789")]
        client = self.client(responses, plan)
        with self.assertRaises(transport.Held): client.collect()
        self.assertEqual(sum(r.consumed for r in responses), 8 * transport.MIB)
        self.assertEqual(responses[-1].requests, [8])
        self.assertEqual(len(client.station_histories()), 1)

    def test_per_station_archive_32_capture_limit(self):
        plan = []
        for day in range(1, 18):
            s = spec()
            s["request"]["parameters"].update(beginDate=f"2026-09-{day:02d}", endDate=f"2026-09-{day:02d}")
            s["request"]["url"] = history.TARGET + "?" + urlencode(s["request"]["parameters"])
            plan.append(s)
        client = self.client([r for _ in plan for r in (Response(b"e", 500), Response())], plan)
        with self.assertRaisesRegex(transport.Held, "per-station adapter"): client.collect()
        self.assertEqual(len(self.calls), 32)

    def test_partial_exception_accounts_prefix_and_unknown_total(self):
        def partial(response, count):
            if response.consumed:
                raise http.client.IncompleteRead(b"tail", 9)
        response = Response(b"x" * 65536, read_hook=partial)
        client = self.client([response])
        with self.assertRaisesRegex(transport.Held, "UNKNOWN"): client.collect()
        capture = client.saved_captures()[0]
        self.assertEqual(capture["receipt"]["response_bytes"], 65540)
        self.assertTrue(capture["body"].endswith(b"tail"))
        self.assertIsNone(capture["receipt"]["total_consumed_body_bytes"])
        resumed = self.client([Response()])
        with self.assertRaises(transport.Held): resumed.collect()
        self.assertEqual(len(self.calls), 1)

    def test_cancellation_holds_known_prefix(self):
        response = Response(read_hook=lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        client = self.client([response])
        with self.assertRaises(transport.Held): client.collect()
        self.assertIsNone(client.saved_captures()[0]["receipt"]["total_consumed_body_bytes"])

    def test_http_error_without_body_is_unknown(self):
        client = self.client([urllib.error.HTTPError(history.TARGET, 500, "error", {}, None)])
        with self.assertRaisesRegex(transport.Held, "accounting unavailable"): client.collect()
        capture = client.saved_captures()[0]
        self.assertIsNone(capture["receipt"]["total_consumed_body_bytes"])
        self.assertFalse(capture["receipt"]["complete"])

    def test_transport_exception_unknown_not_zero(self):
        client = self.client([OSError("synthetic failure")])
        with self.assertRaises(transport.Held): client.collect()
        self.assertIsNone(client.saved_captures()[0]["receipt"]["total_consumed_body_bytes"])

    def test_truncated_content_length_holds(self):
        response = Response(b"abc", headers={"Content-Length": "10"})
        client = self.client([response])
        with self.assertRaisesRegex(transport.Held, "truncated"): client.collect()
        self.assertEqual(client.saved_captures()[0]["body"], b"abc")

    def test_reader_breaking_bound_is_unknown(self):
        response = Response()
        response.read = lambda n: b"x" * (n + 1)
        client = self.client([response])
        with self.assertRaisesRegex(transport.Held, "reader violated"): client.collect()
        self.assertIsNone(client.saved_captures()[0]["receipt"]["total_consumed_body_bytes"])

    def test_deadline_during_body(self):
        response = Response(b"x", read_hook=lambda *_: self.clock.sleep(301))
        client = self.client([response])
        with self.assertRaisesRegex(transport.Held, "deadline"): client.collect()
        self.assertEqual(len(self.calls), 1)

    def test_reservation_delay_cannot_open_after_deadline(self):
        client = self.client([Response()], max_elapsed_seconds=5)
        persist = client._persist
        def delayed_reservation():
            persist()
            if client.ledger["attempts"] and client.ledger["attempts"][-1]["state"] == "RESERVED":
                self.clock.sleep(6)
        with patch.object(client, "_persist", delayed_reservation):
            with self.assertRaisesRegex(transport.Held, "deadline"): client.collect()
        self.assertEqual(self.calls, [])

    def test_non200_success_codes_are_failed_adapter_evidence(self):
        client = self.client([Response(b"[]", 204), Response(b"[]", 206)], [spec(1), spec(2)])
        captures = client.collect()
        self.assertTrue(all(not c["receipt"]["complete"] for c in captures))
        self.assertEqual(set(self.ledger()["logical"].values()), {"FAILED"})

    def test_real_alarm_interrupts_blocking_mock(self):
        with self.assertRaisesRegex(transport.Held, "deadline during"):
            with transport._deadline(0.01): time.sleep(0.1)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_backoff_over_remaining_deadline_no_retry(self):
        client = self.client([Response(b"error", 429, {"Retry-After": "9"}), Response()], max_elapsed_seconds=5)
        with self.assertRaisesRegex(transport.Held, "backoff"): client.collect()
        self.assertEqual(len(self.calls), 1)

    def test_clock_rollback(self):
        response = Response(b"abc", read_hook=lambda *_: setattr(self.clock, "value", self.clock.value - 1))
        client = self.client([response])
        with self.assertRaisesRegex(transport.Held, "clock rollback"): client.collect()

    def test_replay_and_retained_failed_retries(self):
        client = self.client([Response(b"error", 500), Response()])
        first = client.collect()
        self.assertEqual(self.client([]).collect(), first)
        self.assertEqual(len(self.calls), 2)

    def _checkpoint_after_first_logical(self):
        client = self.client([Response()], [spec(1), spec(2)])
        persist = client._persist
        def stop_after_first():
            persist()
            if "COMPLETE" in client.ledger["logical"].values():
                raise RuntimeError("simulated stop between logical requests")
        with patch.object(client, "_persist", stop_after_first):
            with self.assertRaises(RuntimeError): client.collect()

    def test_resume_remaining_request_without_replaying_first(self):
        self._checkpoint_after_first_logical()
        captures = self.client([Response()], [spec(1), spec(2)]).collect()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(captures), 2)

    def test_resumed_wall_rollback_rejected(self):
        self._checkpoint_after_first_logical()
        self.clock.value -= 1
        with self.assertRaisesRegex(transport.Held, "clock rollback"):
            self.client([Response()], [spec(1), spec(2)]).collect()
        self.assertEqual(len(self.calls), 1)

    def test_resume_does_not_reset_cumulative_deadline(self):
        self._checkpoint_after_first_logical()
        self.clock.sleep(301)
        with self.assertRaisesRegex(transport.Held, "deadline"):
            self.client([Response()], [spec(1), spec(2)]).collect()
        self.assertEqual(len(self.calls), 1)

    def test_terminal_failed_vs_empty_vs_unqueried(self):
        client = self.client([Response(b"error", 404), Response()], [spec(1), spec(2), spec(3)], max_attempts=2)
        with self.assertRaisesRegex(transport.Held, "physical attempt limit"): client.collect()
        ledger = self.ledger()
        self.assertEqual(list(ledger["logical"].values()).count("FAILED"), 1)
        self.assertEqual(list(ledger["logical"].values()).count("COMPLETE"), 1)
        self.assertEqual(list(ledger["logical"].values()).count("NOT_QUERIED"), 1)

    def test_interrupted_reservation_blocks_resume(self):
        client = self.client([])
        original = client._persist
        def stop_after_reservation():
            original()
            if client.ledger["attempts"]: raise RuntimeError("simulated process stop")
        with patch.object(client, "_persist", stop_after_reservation):
            with self.assertRaises(RuntimeError): client.collect()
        with self.assertRaises(transport.Held): self.client([Response()]).collect()
        self.assertEqual(self.calls, [])

    def test_interrupted_ledger_and_plan_mismatch(self):
        self.client([Response()]).collect()
        with self.assertRaisesRegex(transport.Held, "plan/budget mismatch"):
            self.client([], max_attempts=63).collect()
        (self.directory / "ledger.pending").write_bytes(b"partial")
        with self.assertRaisesRegex(transport.Held, "interrupted ledger"):
            self.client([]).collect()

    def test_body_tamper_rejected(self):
        self.client([Response()]).collect()
        (self.directory / "body-001.bin").write_bytes(b"xx")
        with self.assertRaisesRegex(transport.Held, "body hash"): self.client([]).collect()

    def test_ledger_corruption_rejected(self):
        self.client([Response()]).collect()
        path = self.directory / "ledger.json"
        value = json.loads(path.read_bytes()); value["attempts"] = []
        path.write_bytes(history.canonical(value))
        with self.assertRaisesRegex(transport.Held, "checksum"): self.client([]).collect()

    def test_rehashed_inconsistent_unknown_never_replays_as_complete(self):
        self.client([Response()]).collect()
        path = self.directory / "ledger.json"
        original = path.read_bytes()
        for field, value in (("total_consumed_bytes", None), ("total_consumed_bytes", True),
                             ("state", "READING"), ("status", 206), ("error", "failed")):
            data = json.loads(original)
            data["attempts"][0][field] = value
            data["sha256"] = history.sha256(history.canonical({k: v for k, v in data.items() if k != "sha256"}))
            path.write_bytes(history.canonical(data))
            with self.subTest(field=field), self.assertRaises(transport.Held): self.client([]).collect()
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
