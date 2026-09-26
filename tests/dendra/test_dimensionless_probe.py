"""Exact-target readiness with synthetic metadata; all networking is denied."""
import copy
import io
import os
from pathlib import Path
import signal
import sys
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

NETWORK_ATTEMPTS, PROVIDER_ATTEMPTS, SLEEP_ATTEMPTS = [], [], []


def deny_network(event, args):
    if event.startswith("socket."):
        NETWORK_ATTEMPTS.append(event)
        raise AssertionError("Readiness forbids sockets/DNS")


def deny_provider(*args, **kwargs):
    PROVIDER_ATTEMPTS.append(True)
    raise AssertionError("Readiness forbids provider dispatch")


def deny_sleep(*args, **kwargs):
    SLEEP_ATTEMPTS.append(True)
    raise AssertionError("Readiness forbids real sleeps")


sys.addaudithook(deny_network)
GUARDS = [patch("time.sleep", deny_sleep), patch("urllib.request.urlopen", deny_provider),
          patch("urllib.request.OpenerDirector.open", deny_provider)]
for guard in GUARDS:
    guard.start()
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from dendra.history_acquisition import dimensionless_probe as probe
from dendra.history_acquisition import scale_resolution as scale
from dendra.history_acquisition.model import Inventory, INVENTORY_SHA256
from dendra.history_acquisition.safety import Hold, encode, decode, digest, sha
from dendra.history_acquisition.d3_plan import validate_request, RequestSpec as D3RequestSpec
from dendra.history_acquisition.provider_metadata import parse_station
from dendra.history_acquisition.provider_adapter import MetadataAdmissionHold, NoRedirect

NOW = "2026-09-26T14:00:00Z"
OTHER = "0" * 24


def setUpModule():
    global INVENTORY, PLAN
    INVENTORY = Inventory.load(os.environ["DENDRA_INVENTORY"], INVENTORY_SHA256)
    PLAN = probe.make_plan(INVENTORY, station_id=probe.STATION, stream_id=probe.STREAM)


def tearDownModule():
    for guard in reversed(GUARDS):
        guard.stop()
    if NETWORK_ATTEMPTS or PROVIDER_ATTEMPTS or SLEEP_ATTEMPTS:
        raise AssertionError("Forbidden network/provider/sleep attempt")


def station(**updates):
    row = dict(_id=probe.STATION, public_level=3, is_hidden=False,
               is_geo_protected=False, geo=dict(type="Point", coordinates=[-116.5, 34.5]),
               name="Synthetic station", revision="synthetic-r1")
    row.update(updates)
    return row


def stream(**updates):
    row = dict(_id=probe.STREAM, station_id=probe.STATION, public_level=3,
               is_hidden=False, is_geo_protected=False,
               terms=dict(dt=dict(Unit="Dimensionless", Variable="SoilMoisture")),
               attributes={}, datapoints_config=[dict(interval=60000)])
    row.update(updates)
    return row


def page(rows=None, **updates):
    rows = [stream()] if rows is None else rows
    value = dict(data=rows, limit=500, total=len(rows), skip=0)
    value.update(updates)
    return value


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status, self.headers = status, headers or {}

    def read(self, size=-1):
        if signal.getitimer(signal.ITIMER_REAL)[0] <= 0:
            raise AssertionError("Every body read needs the existing total deadline")
        if not 0 < size <= 65536:
            raise AssertionError("Bounded chunk required")
        return super().read(size)


class Harness:
    def __init__(self, bodies=None):
        self.clock = 0.0
        self.responses = [Response(encode(value)) for value in (
            [station(), page()] if bodies is None else bodies)]
        self.sent, self.reserved, self.saved = [], [], []
        self.probe = probe.Probe(INVENTORY, PLAN)

    def reserve(self, spec, binding):
        self.reserved.append((spec, binding))

    def record(self, receipt, sanitized):
        self.saved.append((receipt, sanitized))

    def execute(self, request, timeout):
        if signal.getitimer(signal.ITIMER_REAL)[0] <= 0 or not 0 < timeout <= 25:
            raise AssertionError("Total deadline before dispatch")
        if len(self.reserved) != len(self.sent) + 1:
            raise AssertionError("Reservation before dispatch")
        self.sent.append(request.full_url)
        return self.responses.pop(0)

    def run(self, **overrides):
        options = dict(executor=self.execute, reserve=self.reserve, record=self.record,
                       now=lambda: NOW, monotonic=lambda: self.clock)
        options.update(overrides)
        return self.probe.run(**options)


class ProbeTests(unittest.TestCase):
    def assert_holds(self, h, requests):
        with self.assertRaises((Hold, OSError, urllib.error.URLError)):
            h.run()
        self.assertEqual(len(h.sent), requests)
        self.assertEqual(h.probe.counters["http_attempts"], requests)
        self.assertEqual(h.probe.counters["retries"], 0)
        with self.assertRaises(Hold):
            h.run()
        self.assertEqual(len(h.sent), requests)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_exact_frozen_target_and_two_request_plan(self):
        self.assertEqual(PLAN["frozen_identity"], INVENTORY.identity(probe.STREAM))
        self.assertEqual(PLAN["frozen_identity"], probe.IDENTITY)
        self.assertEqual(PLAN["envelope"], probe.ENVELOPE)
        self.assertEqual([r["kind"] for r in PLAN["requests"]], ["station", "datastream-list"])
        self.assertTrue(PLAN["requests"][0]["url"].endswith("stations/" + probe.STATION))
        self.assertEqual(PLAN["requests"][1]["url"], probe.BASE + "datastreams?station_id=" +
                         probe.STATION + "&%24limit=500&%24sort%5B_id%5D=1")

    def test_arbitrary_station_stream_or_endpoint_rejected(self):
        for station_id, stream_id in ((OTHER, probe.STREAM), (probe.STATION, OTHER), (OTHER, OTHER)):
            with self.subTest(station=station_id, stream=stream_id), self.assertRaises(Hold):
                probe.make_plan(INVENTORY, station_id=station_id, stream_id=stream_id)
        for spec in (probe.RequestSpec("observations"), probe.RequestSpec("unit-vocabulary"),
                     probe.RequestSpec("station", station_id=OTHER),
                     probe.RequestSpec("datastream-list", selected_stream=OTHER)):
            with self.assertRaises(Hold):
                spec.url()

    def test_original_d3_selection_not_broadened(self):
        with self.assertRaises(Hold):
            D3RequestSpec("station", probe.STREAM).url()
        with self.assertRaises(Hold):
            parse_station(encode(station()), probe.STATION, checked_at=NOW, now=NOW)

    def test_plan_mutation_and_stale_source_binding_rejected(self):
        for field in ("envelope", "frozen_identity", "collector_sources", "requests"):
            value = copy.deepcopy(PLAN)
            value[field] = {}
            with self.subTest(field=field), self.assertRaises(Hold):
                probe.Probe(INVENTORY, value)
        h = Harness()
        with patch.object(probe, "source_binding", return_value={}):
            self.assert_holds(h, 0)

    def test_source_drift_between_requests_prevents_second(self):
        h = Harness()
        original = probe.source_binding()
        with patch.object(probe, "source_binding", side_effect=[original, {}]):
            self.assert_holds(h, 1)

    def test_headers_queries_pagination_methods_and_other_origins_rejected(self):
        spec = probe.RequestSpec("datastream-list")
        for url in (spec.url()+"&%24skip=500", spec.url()+"&station_id="+probe.STATION,
                    spec.url().replace("api.dendra.science", "invalid.example"),
                    spec.url().replace("https:", "http:"), spec.url()+"#fragment"):
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "BRIM-Dendra-D3/1.0"})
            with self.subTest(url=url), self.assertRaises(Hold):
                validate_request(req, spec)
        for key in ("Cookie", "Authorization", "X-Api-Key"):
            req = urllib.request.Request(spec.url(), headers={"Accept": "application/json",
                "User-Agent": "BRIM-Dendra-D3/1.0", key: "synthetic-denied"})
            with self.assertRaises(Hold):
                validate_request(req, spec)
        req = urllib.request.Request(spec.url(), data=b"{}", method="POST")
        with self.assertRaises(Hold):
            validate_request(req, spec)

    def test_success_is_exactly_two_requests_and_never_reusable(self):
        h = Harness()
        result = h.run()
        self.assertEqual(h.probe.counters["logical_requests"], 2)
        self.assertEqual(h.probe.counters["http_attempts"], 2)
        self.assertEqual(result["stream_id"], probe.STREAM)
        self.assertEqual(result["scale_assertions"], [])
        self.assertEqual(result["historical_applicability"], {"kind": "unknown_history"})
        with self.assertRaises(Hold):
            h.run()
        self.assertEqual(len(h.sent), 2)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_any_station_admission_failure_prevents_second_request(self):
        for update in (dict(_id=OTHER), dict(public_level=0), dict(public_level=True),
                       dict(is_hidden=True), dict(is_deleted=True), dict(deleted_at=NOW),
                       dict(public_level=3, access_levels_resolved=dict(public_level=0))):
            with self.subTest(update=update):
                self.assert_holds(Harness([station(**update), page()]), 1)

    def test_missing_selected_target_is_not_absence_or_pagination(self):
        self.assert_holds(Harness([station(), page([])]), 2)
        self.assert_holds(Harness([station(), page([stream(_id=OTHER)])]), 2)

    def test_selected_and_unselected_wrong_station_or_duplicate_holds(self):
        for rows in ([stream(station_id=OTHER)], [stream(), stream()],
                     [stream(), stream(_id=OTHER, station_id=OTHER)],
                     [stream(), stream(_id="invalid")]):
            with self.subTest(rows=len(rows)):
                self.assert_holds(Harness([station(), page(rows)]), 2)

    def test_incomplete_ambiguous_full_or_offset_lists_hold_without_pagination(self):
        for update in (dict(limit=None), dict(limit=True), dict(limit=501), dict(limit=1),
                       dict(skip=1), dict(skip=True), dict(total=2), dict(total=False)):
            with self.subTest(update=update):
                self.assert_holds(Harness([station(), page(**update)]), 2)

    def test_target_privacy_and_native_unit_drift_hold(self):
        for update in (dict(public_level=0), dict(is_hidden=True), dict(is_deleted=True),
                       dict(terms={"dt": {"Unit": "Percent"}})):
            with self.subTest(update=update):
                self.assert_holds(Harness([station(), page([stream(**update)])]), 2)

    def test_target_specific_diagnostic_has_safe_reason_and_parser_location(self):
        for target, code in (
                (stream(terms={"dt": {"Unit": "Percent"}}), "target.native_unit_changed"),
                (stream(attributes={"output_unit": {"private": "PRIVATE_TARGET_MARKER"}}),
                 "target.unsupported_scientific_leaf")):
            h = Harness([station(), page([target])]); self.assert_holds(h, 2)
            diagnostic = decode(h.saved[-1][1])
            self.assertEqual(diagnostic["reason"]["code"], code)
            self.assertEqual(diagnostic["reason"]["parser_site"]["module"], "dimensionless_probe")
            self.assertNotIn(b"PRIVATE_TARGET_MARKER", h.saved[-1][1])

    def test_scientific_cadence_and_configured_end_guards_preserved(self):
        for config in ([], [dict(interval=0)], [dict(interval=True)],
                       [dict(interval=1), dict(interval=2)],
                       [dict(interval=1000, ends_before="invalid")]):
            with self.subTest(config=config):
                self.assert_holds(Harness([station(), page([stream(datapoints_config=config)])]), 2)
        self.assert_holds(Harness([station(), page([stream(ended_at="2020-01-01T00:00:00Z",
            datapoints_config=[dict(interval=1000, ends_before="2021-01-01T00:00:00Z")])])]), 2)

    def test_other_streams_are_ids_only_even_with_private_scale_claims(self):
        other = stream(_id=OTHER, public_level=0, is_hidden=True,
                       attributes=dict(scale=dict(value=100, description="SIBLING_PRIVATE_MARKER")),
                       geo=dict(type="Point", coordinates=[1, 2, 3]))
        result = Harness([station(), page([stream(), other])]).run()
        self.assertEqual(result["unexpected_ids"], [OTHER])
        self.assertEqual(result["pagination"]["row_count"], 2)
        self.assertEqual(result["scale_assertions"], [])
        self.assertNotIn(b"SIBLING_PRIVATE_MARKER", encode(result))
        self.assertEqual(result["scientific_claims"], Harness().run()["scientific_claims"])

    def test_target_only_sanitized_scale_configuration_is_hash_bound_unreviewed(self):
        target = stream(attributes=dict(scale=dict(value=100, unit="percent", description="Synthetic explicit output"),
                                         credentials="NOT_RETAINED", geo=dict(coordinates=[1, 2, 3])),
                        datapoints_config=[dict(interval=60000, output_unit="percent", multiplier=100,
                                                valid_from="2020-01-01T00:00:00Z", secret="NOT_RETAINED")])
        value = page([target]); result = Harness([station(), value]).run()
        self.assertEqual(result["original_response_sha256"], sha(encode(value)))
        self.assertEqual(result["selected_record_sha256"], digest(target))
        self.assertEqual(result["configuration_sha256"], digest(target["datapoints_config"]))
        self.assertEqual(result["scientific_claims"]["attributes"]["scale"]["value"], 100)
        self.assertEqual(result["scientific_claims"]["datapoints_config"]["valid_from"], "2020-01-01T00:00:00Z")
        self.assertEqual(result["omitted_scientific_fields"], dict(terms=0, attributes=2, datapoints_config=1))
        self.assertEqual(result["historical_applicability"], dict(kind="unknown_history"))
        self.assertEqual(result["claim_review"], "unreviewed_provider_metadata")
        self.assertEqual(result["scale_assertions"], [])
        self.assertNotIn(b"NOT_RETAINED", encode(result))

    def test_scientific_and_configuration_changes_change_bound_hashes(self):
        a = Harness().run()
        b = Harness([station(), page([stream(datapoints_config=[dict(interval=60000, multiplier=100)])])]).run()
        self.assertNotEqual(a["configuration_sha256"], b["configuration_sha256"])
        self.assertNotEqual(a["selected_record_sha256"], b["selected_record_sha256"])
        c = Harness([station(), page([stream(attributes=dict(output_unit="percent"))])]).run()
        self.assertNotEqual(a["scientific_sha256"], c["scientific_sha256"])

    def test_no_scale_decisions_or_provider_campaigns_are_created(self):
        with patch.object(scale, "resolve", side_effect=AssertionError("No decisions")), \
             patch.object(scale, "build_state", side_effect=AssertionError("No state")), \
             patch("dendra.history_acquisition.journal.Journal", side_effect=AssertionError("No campaign")):
            self.assertEqual(Harness().run()["scale_assertions"], [])

    def test_supporting_only_cannot_resolve_and_unknown_primary_cannot_resolve_por(self):
        packet = Harness().run(); body = encode(packet)
        for role, kind in (("supporting", "sister_stream"), ("primary", "datastream_metadata")):
            claim = dict(schema_version=scale.EVIDENCE_VERSION, station_id=probe.STATION, stream_id=probe.STREAM,
                role=role, kind=kind, scale="fraction", applicability=dict(kind="unknown_history"),
                source=dict(ref="synthetic/target-packet.json", sha256=sha(body), version=probe.PACKET_VERSION))
            item = scale.Evidence.bind(claim, body)
            result = scale.resolve(INVENTORY, probe.STREAM, [item])
            self.assertEqual(result["resolution_status"], "unresolved")
            self.assertIsNone(result["conversion_factor"])
        self.assertEqual(scale.resolve(INVENTORY, probe.STREAM)["resolution_status"], "unresolved")

    def test_other_identity_evidence_cannot_enter_target_scale_decision(self):
        source = encode(dict(synthetic=True))
        claim = dict(schema_version=scale.EVIDENCE_VERSION, station_id=probe.STATION, stream_id=OTHER,
            role="primary", kind="datastream_metadata", scale="fraction", applicability=dict(kind="whole_history"),
            source=dict(ref="synthetic/other.json", sha256=sha(source), version="synthetic-v1"))
        with self.assertRaises(Hold):
            scale.resolve(INVENTORY, probe.STREAM, [scale.Evidence.bind(claim, source)])

    def test_optional_z_matches_existing_parser_and_stays_station_only(self):
        old_id = "635319fcb055ac5348842453"
        for coords in ([-116.5, 34.5], [-116.5, 34.5, -123.25]):
            h = Harness([station(geo=dict(type="Point", coordinates=coords)), page()]); result = h.run()
            admitted = decode(h.saved[0][1])
            old = parse_station(encode(station(_id=old_id, geo=dict(type="Point", coordinates=coords))),
                                old_id, checked_at=NOW, now=NOW)
            old["exact_id"] = probe.STATION
            self.assertEqual(encode(admitted), encode(old))
            self.assertFalse(any(k.startswith("geo_z") for k in result))
            self.assertNotIn("geometry", result)

    def test_protected_or_unknown_station_z_is_suppressed(self):
        for protected in (True, None):
            h = Harness([station(is_geo_protected=protected, geo=dict(type="Point", coordinates=[1, 2, 123])), page()])
            h.run(); admitted = decode(h.saved[0][1])
            self.assertIsNone(admitted["geometry"])
            self.assertFalse(any(k.startswith("geo_z") for k in admitted))

    def test_malformed_optional_z_stops_before_list(self):
        for coords in ([1, 2, True], [1, 2, 3, 4], [181, 2, 3]):
            self.assert_holds(Harness([station(geo=dict(type="Point", coordinates=coords)), page()]), 1)

    def test_rejection_retains_only_bounded_diagnostic_no_private_body(self):
        h = Harness([station(public_level=0, name="PRIVATE_REJECTED_MARKER", geo=dict(type="Point", coordinates=[1, 2, 999])), page()])
        self.assert_holds(h, 1)
        receipt, body = h.saved[0]
        self.assertEqual(receipt["outcome"], "HOLD")
        self.assertFalse(receipt["original_body_retained"])
        self.assertLessEqual(len(body), 12288)
        self.assertNotIn(b"PRIVATE_REJECTED_MARKER", body)
        self.assertEqual(decode(body)["reason"]["code"], "access.public_nonhidden_required")

    def test_http_failures_and_redirects_never_retry(self):
        for status in (301, 302, 404, 429, 500, 503):
            h = Harness(); h.responses[0] = Response(b"PRIVATE_ERROR", status=status)
            self.assert_holds(h, 1)
            self.assertEqual(h.saved[0][0]["http_status"], status)
            self.assertIsNone(h.saved[0][1])
        request = urllib.request.Request(probe.RequestSpec("station").url())
        with self.assertRaises(urllib.error.HTTPError):
            NoRedirect().redirect_request(request, None, 302, "redirect", {}, request.full_url)

    def test_transport_and_http_exceptions_have_no_second_attempt(self):
        for exception in (urllib.error.URLError("synthetic"),
                          urllib.error.HTTPError(probe.RequestSpec("station").url(), 503, "synthetic", {}, io.BytesIO())):
            h = Harness()
            def fail(request, timeout):
                h.sent.append(request.full_url)
                raise exception
            h.execute = fail
            self.assert_holds(h, 1)

    def test_durable_reservation_and_receipt_failures_are_terminal(self):
        h = Harness()
        def fail(*args):
            raise OSError("synthetic persistence failure")
        h.reserve = fail
        self.assert_holds(h, 0)
        h = Harness(); h.record = fail
        self.assert_holds(h, 1)

    def test_encoded_and_oversized_response_hold(self):
        h = Harness(); h.responses[0] = Response(b"encoded", headers={"Content-Encoding": "gzip"})
        self.assert_holds(h, 1)
        h = Harness(); h.responses[0] = Response(b"x" * (probe.BODY_BYTES + 50))
        self.assert_holds(h, 1)
        self.assertEqual(h.saved[0][0]["response_bytes"], probe.BODY_BYTES + 1)
        self.assertFalse(h.saved[0][0]["response_complete"])
        self.assertIsNone(h.saved[0][1])

    def test_partial_read_failure_hashes_consumed_prefix_without_retaining_it(self):
        h = Harness()
        class Broken(Response):
            def read(self, size=-1):
                if self.tell():
                    raise OSError("synthetic body failure")
                return super().read(size)
        h.responses[0] = Broken(b"prefix")
        self.assert_holds(h, 1)
        receipt, retained = h.saved[0]
        self.assertEqual(receipt["response_bytes"], 6)
        self.assertEqual(receipt["response_sha256"], sha(b"prefix"))
        self.assertFalse(receipt["response_complete"])
        self.assertIsNone(retained)

    def test_per_request_and_total_time_limits_without_real_sleeps(self):
        h = Harness(); original = h.execute
        def slow(request, timeout):
            value = original(request, timeout); h.clock += 25.1; return value
        h.execute = slow
        self.assert_holds(h, 1)
        h = Harness(); original_record = h.record
        def late(receipt, body):
            original_record(receipt, body); h.clock = 50.1
        h.record = late
        self.assert_holds(h, 1)

    def test_reentrant_runner_is_rejected_before_an_extra_dispatch(self):
        h = Harness(); original = h.execute
        def reentrant(request, timeout):
            with self.assertRaises(Hold):
                h.run()
            return original(request, timeout)
        h.execute = reentrant
        h.run()
        self.assertEqual(len(h.sent), 2)

    def test_last_receipt_overrun_does_not_return_success(self):
        h = Harness(); original = h.record
        def late(receipt, body):
            original(receipt, body)
            if len(h.saved) == 2:
                h.clock = 50.1
        h.record = late
        self.assert_holds(h, 2)

    def test_receipts_and_sanitized_hashes_bind_exact_responses(self):
        values = [station(), page()]; h = Harness(values); h.run()
        for index, (receipt, sanitized) in enumerate(h.saved):
            self.assertEqual(receipt["response_sha256"], sha(encode(values[index])))
            self.assertEqual(receipt["response_bytes"], len(encode(values[index])))
            self.assertEqual(receipt["sanitized_sha256"], sha(sanitized))
            self.assertEqual(receipt["plan_sha256"], digest(PLAN))
            self.assertEqual(receipt["ordinal"], index+1)
        self.assertEqual(h.probe.counters["response_bytes"], sum(len(encode(v)) for v in values))

    def test_no_network_provider_or_real_sleep(self):
        self.assertEqual((NETWORK_ATTEMPTS, PROVIDER_ATTEMPTS, SLEEP_ATTEMPTS), ([], [], []))
