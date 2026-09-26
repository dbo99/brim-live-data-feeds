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
from dendra.history_acquisition import provider_metadata as historical
from dendra.history_acquisition.model import Inventory, INVENTORY_SHA256, metadata_view
from dendra.history_acquisition.safety import Hold, encode, decode, digest, sha
from dendra.history_acquisition.d3_plan import validate_request, RequestSpec as D3RequestSpec
from dendra.history_acquisition.provider_metadata import parse_station
from dendra.history_acquisition.provider_adapter import (
    MetadataAdmissionHold, NoRedirect, metadata_shape, SHAPE_KEYS, SHAPE_FIELDS, DIAGNOSTIC_BYTES,
)

NOW = "2026-09-26T14:00:00Z"
OTHER = "0" * 24


def setUpModule():
    global INVENTORY, PLAN
    INVENTORY = Inventory.load(os.environ["DENDRA_INVENTORY"], INVENTORY_SHA256)
    PLAN = probe.make_plan(INVENTORY, station_id=probe.STATION, stream_id=probe.STREAM,
                           metadata_profile=probe.REVIEW_PROFILE)


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
               terms=dict(dt=dict(Unit="Dimensionless"),
                          ds=dict(Medium="Soil", Variable="VolumetricWaterContent")),
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
                probe.make_plan(INVENTORY, station_id=station_id, stream_id=stream_id,
                                metadata_profile=probe.REVIEW_PROFILE)
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
        for field in ("envelope", "frozen_identity", "collector_sources", "requests",
                      "metadata_profile", "metadata_profile_sha256"):
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

    def test_target_scientific_shape_type_matrix_with_explicit_conditional_profile(self):
        variants = [("missing", None), ("null", None), ("object", {}),
                    ("array", ["WITHHELD_ARRAY_VALUE"]), ("string", "WITHHELD_STRING_VALUE"),
                    ("integer", 987654321), ("number", 98765.4321), ("boolean", True)]
        for terms_type, terms in variants:
            for attributes_type, attributes in variants:
                with self.subTest(terms=terms_type, attributes=attributes_type):
                    target = stream()
                    expected = {}
                    for name, kind, value in (("terms", terms_type, terms),
                                              ("attributes", attributes_type, attributes)):
                        expected[name] = dict(present=kind != "missing", json_type=kind)
                        if kind == "missing":
                            del target[name]
                        elif kind != "object":
                            target[name] = value
                    value = page([target]); h = Harness([station(), value])
                    if terms_type == "object" and attributes_type in ("object", "missing"):
                        result = h.run()
                        scientific = dict(profile=probe.REVIEW_PROFILE, terms=target["terms"],
                                          attributes_state="ABSENT" if attributes_type == "missing" else "PRESENT_EMPTY",
                                          datapoints_config=target["datapoints_config"])
                        if attributes_type == "object":
                            scientific["attributes"] = target["attributes"]
                        self.assertEqual(result["scientific_sha256"], digest(scientific))
                        self.assertEqual(result["scientific_claims"]["terms"], target["terms"])
                        self.assertEqual("attributes" in result["scientific_claims"], attributes_type == "object")
                        self.assertTrue(all(r["outcome"] == "ADMITTED" for r, _ in h.saved))
                        self.assertTrue(all("target_scientific_shape" not in decode(b) for _, b in h.saved))
                        continue
                    # This parser is unchanged; the boundary must retain its HOLD.
                    with self.assertRaisesRegex(Hold, "^Scientific metadata shape$"):
                        historical._scientific(target)
                    with self.assertRaises(MetadataAdmissionHold) as caught:
                        h.run()
                    receipt, saved = h.saved[-1]; diagnostic = decode(saved)
                    self.assertEqual(diagnostic, caught.exception.diagnostic)
                    self.assertEqual(diagnostic["version"], "dendra-metadata-diagnostic-1")
                    self.assertEqual(diagnostic["reason"]["code"], "science.terms_attributes_shape")
                    self.assertEqual(diagnostic["reason"]["parser_site"]["module"], "dimensionless_probe")
                    self.assertEqual(diagnostic["reason"]["parser_site"]["function"], "_review_science")
                    self.assertEqual(diagnostic["target_scientific_shape"], expected)
                    self.assertEqual(diagnostic["body_sha256"], sha(encode(value)))
                    self.assertEqual(diagnostic["body_bytes"], len(encode(value)))
                    self.assertEqual(receipt["outcome"], "HOLD")
                    self.assertEqual(receipt["sanitized_sha256"], sha(saved))
                    self.assertFalse(receipt["original_body_retained"])
                    self.assertEqual((len(h.sent), h.probe.counters["http_attempts"],
                                      h.probe.counters["retries"]), (2, 2, 0))
                    self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
                    self.assertLessEqual(len(saved), DIAGNOSTIC_BYTES)
                    for secret in (b"WITHHELD_ARRAY_VALUE", b"WITHHELD_STRING_VALUE",
                                   b"987654321", b"98765.4321"):
                        self.assertNotIn(secret, saved)

    def test_target_shape_beyond_sample_has_no_values_or_other_stream_facts(self):
        others = [stream(_id=f"{i:024x}", attributes={"OTHER_PRIVATE_KEY": "OTHER_PRIVATE_VALUE"})
                  for i in range(2)]
        target = stream(terms=None, attributes=[{"TARGET_PRIVATE_KEY": "TARGET_PRIVATE_VALUE"}],
                        geo={"coordinates": [123.987654321, 34.987654321]})
        h = Harness([station(), page(others + [target])]); self.assert_holds(h, 2)
        saved = h.saved[-1][1]; diagnostic = decode(saved)
        self.assertEqual(diagnostic["target_scientific_shape"], dict(
            terms=dict(present=True, json_type="null"), attributes=dict(present=True, json_type="array")))
        self.assertFalse(any("$.data[2]" in f["path"] for f in diagnostic["shape"]["fields"]))
        for secret in (b"OTHER_PRIVATE_KEY", b"OTHER_PRIVATE_VALUE", b"TARGET_PRIVATE_KEY",
                       b"TARGET_PRIVATE_VALUE", b"123.987654321", b"34.987654321", probe.STREAM.encode()):
            self.assertNotIn(secret, saved)

    def test_target_shape_is_absent_before_public_admission_and_for_other_holds(self):
        cases = [page([stream(terms=None, attributes=[], public_level=0)]),
                 page([stream(terms=None, attributes=[], is_hidden=True)]),
                 page([stream(terms=None, attributes=[], station_id=OTHER)]),
                 page([stream(terms=None, attributes=[])], skip=1),
                 page([stream(terms=None, attributes=[])], total=2),
                 page([stream(terms=None, attributes=[])], limit=1),
                 page([stream(_id=OTHER, terms=None, attributes=[])]),
                 page([stream(), stream(terms=None)]),
                 page([stream(datapoints_config=[])]),
                 page([stream(terms={"dt": {"Unit": "Percent"}})])]
        for i, value in enumerate(cases):
            with self.subTest(case=i):
                h = Harness([station(), value]); self.assert_holds(h, 2)
                diagnostic = decode(h.saved[-1][1])
                self.assertNotIn("target_scientific_shape", diagnostic)
                self.assertNotEqual(diagnostic["reason"]["code"], "science.terms_attributes_shape")
                if i < 2:
                    try:
                        probe._public(value["data"][0])
                    except Hold as old:
                        self.assertEqual(str(old), "Private, hidden or unknown public metadata")
                        baseline = MetadataAdmissionHold(probe.RequestSpec("datastream-list"),
                                                         encode(value), value, old)
                    else:
                        self.fail("Existing public admission must still HOLD")
                    # Preserve the existing whole-list diagnostic mapping too.
                    self.assertEqual(diagnostic["reason"], baseline.diagnostic["reason"])

    def test_target_shape_survives_generic_size_trimming(self):
        keys = sorted(SHAPE_KEYS)[:16]
        value = page([stream(terms=None)])
        value.update({a: {b: {c: None for c in keys} for b in keys}
                      for a in keys if a not in value})
        self.assertGreater(len(encode(metadata_shape(value, "datastream-list"))), DIAGNOSTIC_BYTES)
        h = Harness([station(), value]); self.assert_holds(h, 2)
        saved = h.saved[-1][1]; diagnostic = decode(saved)
        self.assertEqual(DIAGNOSTIC_BYTES, 12288)
        self.assertLessEqual(len(saved), DIAGNOSTIC_BYTES)
        self.assertLess(len(diagnostic["shape"]["fields"]), SHAPE_FIELDS)
        self.assertTrue(diagnostic["shape"]["truncated"])
        self.assertEqual(diagnostic["reason"]["code"], "science.terms_attributes_shape")
        self.assertEqual(diagnostic["target_scientific_shape"], dict(
            terms=dict(present=True, json_type="null"), attributes=dict(present=True, json_type="object")))

    def test_target_shape_constructor_rejects_extra_values_and_open_types(self):
        context = dict(terms=dict(present=False, json_type="missing"),
                       attributes=dict(present=True, json_type="object"))
        bad = [dict(context, provider_key={}), dict(terms=context["terms"]),
               dict(context, terms=dict(present=False, json_type="missing", value="PRIVATE")),
               dict(context, terms=dict(present=True, json_type="unparsed")),
               dict(context, terms=dict(present=1, json_type="missing")),
               dict(context, terms=dict(present=True, json_type="missing")),
               dict(context, terms=dict(present=False, json_type="null")),
               dict(context, terms=dict(present=True, json_type="object"))]
        spec = probe.RequestSpec("datastream-list")
        for value in bad:
            with self.subTest(context=value), self.assertRaises(Hold):
                MetadataAdmissionHold(spec, b"{}", {}, Hold("Scientific metadata shape"),
                                      target_scientific_shape=value)
        for spec, reason in ((probe.RequestSpec("station"), "Scientific metadata shape"),
                             (probe.RequestSpec("datastream-list"), "Missing or ambiguous configured cadence")):
            with self.assertRaises(Hold):
                MetadataAdmissionHold(spec, b"{}", {}, Hold(reason), target_scientific_shape=context)

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


class ConditionalReviewTests(unittest.TestCase):
    """Invented valid claims, never assertions about the real target's contents."""
    def packet(self, target=None, sid=probe.STREAM, **options):
        identity = INVENTORY.identity(sid)
        target = stream() if target is None else target
        admitted = historical._parse_station(encode(station(_id=identity["station_id"])),
                                              identity["station_id"], checked_at=NOW, now=NOW)
        kwargs = dict(stream_id=sid, metadata_profile=probe.REVIEW_PROFILE, checked_at=NOW, now=NOW)
        kwargs.update(options)
        return probe.review_packet(encode(page([target])), admitted, INVENTORY, **kwargs)

    def known(self):
        sid = "63531a67a9b61453fa1ca4ed"
        identity = INVENTORY.identity(sid)
        target = stream(_id=sid, station_id=identity["station_id"],
                        attributes=dict(depth=dict(value=200, unit_tag="dt_Unit_Millimeter"),
                                        orientation="horizontal"))
        target["terms"]["dt"]["Unit"] = identity["native_unit"]
        return sid, target

    def test_profile_required_and_bound_in_plan_and_packet(self):
        with self.assertRaises(TypeError):
            probe.make_plan(INVENTORY, station_id=probe.STATION, stream_id=probe.STREAM)
        for name in (None, "", "dendra-d3-adapter-1"):
            with self.subTest(profile=name), self.assertRaises(Hold):
                self.packet(metadata_profile=name)
        packet = self.packet()
        self.assertEqual(packet["metadata_profile"], PLAN["metadata_profile"])
        self.assertEqual(packet["metadata_binding"]["profile"], probe.REVIEW_PROFILE)
        self.assertEqual(packet["metadata_binding_sha256"], digest(packet["metadata_binding"]))
        self.assertEqual(packet["metadata_binding"]["collector_fingerprint"], digest(probe.source_binding()))
        self.assertEqual(PLAN["metadata_profile_sha256"], digest(dict(
            profile=probe.REVIEW_PROFILE, packet_version=probe.PACKET_VERSION)))
        old = copy.deepcopy(PLAN)
        del old["metadata_profile"]
        with self.assertRaises(Hold):
            probe.Probe(INVENTORY, old)

    def test_absent_can_continue_but_does_not_invent_science(self):
        target = stream(); del target["attributes"]
        packet = self.packet(target)
        self.assertEqual(packet["attributes_state"], "ABSENT")
        self.assertNotIn("attributes", packet["scientific_claims"])
        for path in ("Medium", "Variable"):
            bad = copy.deepcopy(target); del bad["terms"]["ds"][path]
            with self.subTest(path=path), self.assertRaisesRegex(Hold, "Review core_terms"):
                self.packet(bad)
        target["datapoints_config"] = []
        with self.assertRaisesRegex(Hold, "Missing or ambiguous configured cadence"):
            self.packet(target)

    def test_required_terms_exact_values_and_no_unit_only_admission(self):
        for section, key, valid in (("ds", "Medium", "Soil"),
                                     ("ds", "Variable", "VolumetricWaterContent"),
                                     ("dt", "Unit", "Dimensionless")):
            self.assertEqual(self.packet()["scientific_claims"]["terms"][section][key], valid)
            for value in (None, "wrong", "", valid.lower(), [], {}, True, 1):
                target = stream(); target["terms"][section][key] = value
                with self.subTest(path=key, value=value), self.assertRaises(Hold):
                    self.packet(target)
            target = stream(); del target["terms"][section][key]
            with self.assertRaises(Hold):
                self.packet(target)
        with self.assertRaises(Hold):
            self.packet(stream(terms=dict(dt=dict(Unit="Dimensionless"))))

    def test_aggregate_optional_exact_supported_values(self):
        packet = self.packet()
        self.assertEqual(packet["aggregate_state"], "ABSENT_UNSPECIFIED")
        self.assertFalse(packet["terms_presence"]["ds"]["fields"]["Aggregate"])
        self.assertNotIn("Aggregate", packet["scientific_claims"]["terms"]["ds"])
        for aggregate in ("Average", "Instantaneous"):
            target = stream(); target["terms"]["ds"]["Aggregate"] = aggregate
            result = self.packet(target)
            self.assertEqual(result["aggregate_state"], "PRESENT")
            self.assertEqual(result["scientific_claims"]["terms"]["ds"]["Aggregate"], aggregate)
        for bad in (None, "Sum", "average", {}, [], True):
            target = stream(); target["terms"]["ds"]["Aggregate"] = bad
            with self.subTest(value=bad), self.assertRaisesRegex(Hold, "Review aggregate"):
                self.packet(target)

    def test_dq_retained_bounded_without_universal_measurement_token(self):
        for value in ("SoilMoisture", "VolumetricWaterContent", "SyntheticOtherMeasurement"):
            target = stream(); target["terms"]["dq"] = dict(Measurement=value, Purpose="ReadytoUse")
            packet = self.packet(target)
            self.assertEqual(packet["scientific_claims"]["terms"]["dq"], target["terms"]["dq"])
        for value in (None, 1, True, {}, [], "", "x"*257, "bad\nvalue"):
            target = stream(); target["terms"]["dq"] = dict(Measurement=value)
            with self.assertRaises(Hold):
                self.packet(target)
        self.assertFalse(self.packet()["terms_presence"]["dq"]["present"])

    def test_five_states_and_all_original_record_hashes_distinct(self):
        values = [("ABSENT", {}), ("NULL", dict(attributes=None)),
                  ("PRESENT_EMPTY", dict(attributes={})),
                  ("PRESENT_POPULATED", dict(attributes=dict(output_unit="native")))]
        values += [("MALFORMED_NON_OBJECT", dict(attributes=x)) for x in ([], "bad", 2, 2.5, True, False)]
        hashes, packets = [], []
        for state, override in values:
            target = stream(); del target["attributes"]; target.update(override)
            self.assertEqual(probe.attributes_state(target), state)
            hashes.append(digest(target))
            if state in ("NULL", "MALFORMED_NON_OBJECT"):
                with self.assertRaisesRegex(Hold, "Scientific metadata shape"):
                    self.packet(target)
            else:
                packet = self.packet(target); packets.append(packet)
                self.assertEqual(packet["attributes_state"], state)
        self.assertEqual(len(hashes), len(set(hashes)))
        for field in ("scientific_sha256", "metadata_binding_sha256", "selected_record_sha256"):
            self.assertEqual(len({p[field] for p in packets}), 3)
        self.assertEqual(len({digest(p) for p in packets}), 3)
        # This is synthetic output in a fresh task-owned test root, not a live capture.
        root = Path(os.environ["DENDRA_TEST_ROOT"])
        for packet in packets:
            (root / (packet["attributes_state"] + "-synthetic-review.json")).write_bytes(encode(packet))

    def test_known_depth_orientation_require_fresh_exact_evidence(self):
        sid, target = self.known()
        packet = self.packet(target, sid)
        self.assertEqual(packet["identity_check"], dict(depth_cm="matched_fresh_evidence",
                                                      orientation="matched_fresh_evidence"))
        for attrs in ({}, dict(depth=dict(value=200, unit_tag="dt_Unit_Millimeter")),
                      dict(orientation="horizontal"),
                      dict(depth=dict(value=600, unit_tag="dt_Unit_Millimeter"), orientation="horizontal"),
                      dict(depth=dict(value=200, unit_tag="dt_Unit_Millimeter"), orientation="Vertical")):
            with self.subTest(attributes=attrs), self.assertRaises(Hold):
                self.packet(dict(target, attributes=attrs), sid)
        del target["attributes"]
        with self.assertRaises(Hold):
            self.packet(target, sid)

    def test_depth_units_aliases_and_conflicts_are_not_inferred(self):
        sid, target = self.known()
        for attrs in (dict(depth=dict(value=20, units="Centimeter"), Orientation="horizontal"),
                      dict(Depth=200, LengthUnits="Millimeter", orientation="horizontal")):
            self.packet(dict(target, attributes=attrs), sid)
        bads = [dict(value=20), dict(value=True, unit="Centimeter"),
                dict(value=20, unit="Meter"), dict(value=20, unit="Centimeter", units="Millimeter"),
                dict(value=20, unit=None), "20cm"]
        for depth in bads:
            with self.subTest(depth=depth), self.assertRaises(Hold):
                self.packet(dict(target, attributes=dict(depth=depth, orientation="horizontal")), sid)
        for extra in (dict(Depth=30, LengthUnits="Centimeter"), dict(Orientation="Vertical")):
            bad = copy.deepcopy(target); bad["attributes"].update(extra)
            with self.assertRaises(Hold):
                self.packet(bad, sid)

    def test_unknown_identity_stays_unknown_even_with_fresh_claims(self):
        packet = self.packet(stream(attributes=dict(depth=dict(value=20, unit="Centimeter"),
                                                   Orientation="Vertical")))
        self.assertEqual(packet["frozen_identity"], probe.IDENTITY)
        self.assertIsNone(packet["frozen_identity"]["depth_cm"])
        self.assertIsNone(packet["frozen_identity"]["orientation"])
        self.assertEqual(packet["identity_check"], dict(depth_cm="frozen_unknown", orientation="frozen_unknown"))
        self.assertEqual(packet["scientific_claims"]["attributes"]["depth"]["value"], 20)

    def test_projection_omits_unknown_values_but_binds_them_and_presence(self):
        target = stream(attributes=dict(unknown=dict(secret="OMIT_MARKER")))
        target["terms"]["ds"]["unknown"] = "OMIT_MARKER"
        target["terms"]["unknown"] = {"private": "OMIT_MARKER"}
        packet = self.packet(target)
        self.assertNotIn(b"OMIT_MARKER", encode(packet))
        self.assertEqual(packet["attributes_state"], "PRESENT_POPULATED")
        self.assertEqual(packet["scientific_claims"]["attributes"], {})
        self.assertEqual(packet["omitted_scientific_fields"]["terms"], 2)
        self.assertEqual(packet["omitted_scientific_fields"]["attributes"], 1)
        self.assertEqual(packet["scientific_field_sha256"]["attributes"], digest(target["attributes"]))
        target["attributes"]["unknown"]["secret"] = "CHANGED_OMITTED"
        changed = self.packet(target)
        self.assertEqual(changed["scientific_claims"], packet["scientific_claims"])
        self.assertNotEqual(changed["scientific_sha256"], packet["scientific_sha256"])

    def test_claim_shape_value_and_size_guards(self):
        for attrs in (dict(scale=[]), dict(scale=dict(value=True)), dict(output_scale="unsupported"),
                      dict(calibration=None), dict(calibration=dict(multiplier="100")),
                      dict(orientation=None), dict(output_units=123),
                      dict(output_unit="x"*257), dict(output_unit="bad\nvalue")):
            with self.subTest(attributes=attrs), self.assertRaises(Hold):
                self.packet(stream(attributes=attrs))

    def test_configured_dates_and_cadence_remain_metadata_only(self):
        config = dict(interval=60000, starts_at="2020-01-01T00:00:00Z",
                      ends_before="2021-01-01T00:00:00Z", valid_from="2020-01-01T00:00:00Z",
                      valid_to="2021-01-01T00:00:00Z")
        packet = self.packet(stream(datapoints_config=[config]))
        self.assertEqual(packet["configuration_state"], "PRESENT_SINGLE")
        self.assertEqual(packet["configured_cadence_seconds"], 60)
        self.assertEqual(packet["cadence_role"], "provider_metadata_only")
        self.assertEqual(packet["historical_applicability"], dict(kind="unknown_history"))
        self.assertEqual(packet["scientific_claims"]["datapoints_config"], config)
        self.assertNotIn("first_observation", packet)
        self.assertNotIn("last_observation", packet)
        for key in ("starts_at", "ends_before", "valid_from", "valid_to"):
            for invalid in (True, 123, {}, "not-time"):
                with self.subTest(key=key, value=invalid), self.assertRaises(Hold):
                    self.packet(stream(datapoints_config=[dict(config, **{key: invalid})]))
        for bounds in (dict(starts_at=NOW, ends_before="2020-01-01T00:00:00Z"),
                       dict(valid_from=NOW, valid_to=NOW)):
            with self.assertRaisesRegex(Hold, "Review date_bounds"):
                self.packet(stream(datapoints_config=[dict(interval=1000, **bounds)]))

    def test_calibration_bounds_preserved_without_scale_or_history_decision(self):
        calibration = dict(multiplier=100, offset=0, output_unit="Percent",
                           valid_from="2020-01-01T00:00:00Z", valid_to="2021-01-01T00:00:00Z")
        packet = self.packet(stream(attributes=dict(calibration=calibration)))
        self.assertEqual(packet["scientific_claims"]["attributes"]["calibration"], calibration)
        self.assertEqual(packet["scale_assertions"], [])
        self.assertEqual(packet["historical_applicability"], dict(kind="unknown_history"))
        calibration["valid_to"] = calibration["valid_from"]
        with self.assertRaisesRegex(Hold, "Review date_bounds"):
            self.packet(stream(attributes=dict(calibration=calibration)))

    def test_all_admitted_states_never_grant_acquisition_or_scale(self):
        rows = [stream(), stream(attributes=dict(scale=dict(value=100, unit="Percent")))]
        absent = stream(); del absent["attributes"]; rows.append(absent)
        for target in rows:
            packet = self.packet(target)
            self.assertEqual(packet["unit_status"], "native_only_scale_unresolved")
            self.assertEqual(packet["scale_assertions"], [])
            for key in ("raw_eligible", "observation_acquisition_authorized", "daily_science_accepted",
                        "browser_publication_eligible"):
                self.assertIs(packet[key], False)
            body = encode(packet)
            claim = dict(schema_version=scale.EVIDENCE_VERSION, station_id=probe.STATION,
                stream_id=probe.STREAM, role="supporting", kind="sister_stream", scale="fraction",
                applicability=dict(kind="whole_history"),
                source=dict(ref="synthetic/review.json", sha256=sha(body), version=probe.PACKET_VERSION))
            decision = scale.resolve(INVENTORY, probe.STREAM, [scale.Evidence.bind(claim, body)])
            self.assertEqual(decision["resolution_status"], "unresolved")
            self.assertIsNone(decision["conversion_factor"])

    def test_deterministic_packet_and_exact_hash_bindings(self):
        target = stream(); a = self.packet(target); b = self.packet(copy.deepcopy(target))
        self.assertEqual(encode(a), encode(b))
        self.assertEqual(a["frozen_identity_sha256"], digest(INVENTORY.identity(probe.STREAM)))
        self.assertEqual(a["selected_record_sha256"], digest(target))
        self.assertEqual(a["original_response_sha256"], sha(encode(page([target]))))
        self.assertEqual(a["configuration_sha256"], digest(target["datapoints_config"]))
        self.assertLessEqual(len(encode(a)), 65536)
        self.assertFalse(any(key in a for key in ("geometry", "geo_z_native", "scientific_fields", "dictionary_sha256")))

    def test_generic_packet_cannot_satisfy_historical_d3_authority(self):
        sid, target = self.known()
        packet = self.packet(target, sid)
        catalog = Path(__file__).resolve().parents[2] / "data/input/dendra/pilot_catalog.json"
        authority = historical.load_authority(INVENTORY, catalog)
        with self.assertRaisesRegex(Hold, "Accepted metadata authority changed"):
            historical.validate_authority(packet)
        self.assertNotEqual(packet["scientific_sha256"], authority["metadata_bindings"][sid])
        result = metadata_view(INVENTORY.identity(sid), packet, checked_at=NOW, now=NOW,
                               scientific_sha256=authority["metadata_bindings"][sid],
                               dictionary_sha256=authority["dictionary_sha256"])
        self.assertFalse(result["raw_eligible"])
        self.assertIn("scientific_binding_changed", result["hold_reasons"])
        mutated = copy.deepcopy(authority)
        mutated["metadata_bindings"][sid] = packet["scientific_sha256"]
        with self.assertRaisesRegex(Hold, "Scientific binding changed"):
            historical.validate_authority(mutated)

    def test_historical_d3_exact_science_and_attributes_still_pinned(self):
        catalog = Path(__file__).resolve().parents[2] / "data/input/dendra/pilot_catalog.json"
        authority = historical.load_authority(INVENTORY, catalog)
        self.assertEqual(digest(authority["scientific"]), historical.SCIENTIFIC_SHA256)
        self.assertEqual(digest(authority["dictionary_terms"]), historical.DICTIONARY_SHA256)
        for sid, science in authority["scientific"].items():
            identity = INVENTORY.identity(sid)
            target = stream(_id=sid, station_id=identity["station_id"],
                            terms=copy.deepcopy(science["source_terms"]),
                            attributes=copy.deepcopy(science["source_attributes"]),
                            datapoints_config=[dict(interval=science["cadence_seconds"]*1000)])
            station_metadata = historical.parse_station(encode(station(_id=identity["station_id"])),
                                                         identity["station_id"], checked_at=NOW, now=NOW)
            vocabulary = dict(terms=authority["dictionary_terms"], dictionary_sha256=authority["dictionary_sha256"])
            def admit(row):
                return historical.parse_datastreams(encode(page([row])), identity, station_metadata,
                                                     vocabulary, authority, checked_at=NOW, now=NOW)
            self.assertEqual(admit(target)["scientific_sha256"], authority["metadata_bindings"][sid])
            absent = copy.deepcopy(target); del absent["attributes"]
            with self.assertRaisesRegex(Hold, "Scientific metadata shape"):
                admit(absent)
            for attrs in (None, [], "bad", True):
                with self.assertRaisesRegex(Hold, "Scientific metadata shape"):
                    admit(dict(target, attributes=attrs))
            for field, value in (("attributes", {}), ("terms", stream()["terms"]),
                                  ("datapoints_config", [dict(interval=123000)])):
                with self.assertRaisesRegex(Hold, "Scientific metadata mismatch"):
                    admit(dict(target, **{field: value}))


class ConfigShapeDiagnosticTests(unittest.TestCase):
    """Synthetic shape facts only; the real target's configuration is unknown."""
    def hold(self, target, others=None, **page_options):
        value = page((others or []) + [target], **page_options)
        h = Harness([station(), value])
        with self.assertRaises(MetadataAdmissionHold) as caught:
            h.run()
        receipt, body = h.saved[-1]
        diagnostic = decode(body)
        self.assertEqual(diagnostic, caught.exception.diagnostic)
        self.assertEqual(diagnostic["version"], "dendra-metadata-diagnostic-1")
        self.assertEqual(diagnostic["reason"]["code"], "science.cadence_shape")
        self.assertEqual(diagnostic["reason"]["parser_site"]["module"], "dimensionless_probe")
        self.assertEqual(diagnostic["reason"]["parser_site"]["function"], "_review_science")
        self.assertEqual(diagnostic["body_sha256"], sha(encode(value)))
        self.assertEqual(diagnostic["body_bytes"], len(encode(value)))
        self.assertEqual(receipt["outcome"], "HOLD")
        self.assertEqual(receipt["sanitized_sha256"], sha(body))
        self.assertEqual(receipt["sanitized_bytes"], len(body))
        self.assertFalse(receipt["original_body_retained"])
        self.assertEqual((len(h.sent), h.probe.counters["http_attempts"], h.probe.counters["retries"]), (2, 2, 0))
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        self.assertEqual(set(diagnostic["target_configuration_shape"]), {"datapoints_config"})
        self.assertLessEqual(len(body), DIAGNOSTIC_BYTES)
        return diagnostic, body

    def test_missing_config_still_holds_and_is_not_null_or_defaulted(self):
        target = stream(); del target["datapoints_config"]
        diagnostic, body = self.hold(target)
        self.assertEqual(diagnostic["target_configuration_shape"],
                         dict(datapoints_config=dict(present=False, json_type="missing")))
        self.assertNotIn("datapoints_config", target)
        self.assertNotIn("target_scientific_shape", diagnostic)
        self.assertNotIn(probe.STREAM.encode(), body)

    def test_null_config_still_holds_with_explicit_null(self):
        diagnostic, _ = self.hold(stream(datapoints_config=None))
        self.assertEqual(diagnostic["target_configuration_shape"],
                         dict(datapoints_config=dict(present=True, json_type="null")))

    def test_object_instead_of_list_still_holds_without_keys_or_values(self):
        value = dict(interval=60000, PRIVATE_CONFIG_KEY="PRIVATE_CONFIG_VALUE")
        diagnostic, body = self.hold(stream(datapoints_config=value))
        self.assertEqual(diagnostic["target_configuration_shape"],
                         dict(datapoints_config=dict(present=True, json_type="object")))
        self.assertNotIn(b"PRIVATE_CONFIG_KEY", body)
        self.assertNotIn(b"PRIVATE_CONFIG_VALUE", body)
        self.assertNotIn(b"60000", body)

    def test_scalar_config_types_still_hold(self):
        for value, kind in (("PRIVATE_CONFIG_STRING", "string"), (876543210, "integer"),
                            (876543.210987, "number"), (True, "boolean"), (False, "boolean")):
            with self.subTest(kind=kind, value=value):
                diagnostic, body = self.hold(stream(datapoints_config=value))
                self.assertEqual(diagnostic["target_configuration_shape"],
                                 dict(datapoints_config=dict(present=True, json_type=kind)))
                if type(value) is not bool:
                    self.assertNotIn(str(value).encode(), body)

    def test_empty_list_retains_zero_count_and_no_default_object(self):
        diagnostic, _ = self.hold(stream(datapoints_config=[]))
        self.assertEqual(diagnostic["target_configuration_shape"], dict(datapoints_config=dict(
            present=True, json_type="array", item_count=0, item_types=[], item_types_truncated=False)))

    def test_one_nonobject_entry_still_holds_and_retains_exact_type(self):
        for value, kind in ((None, "null"), ([], "array"), ("WITHHELD", "string"),
                            (876543210, "integer"), (876543.210987, "number"), (True, "boolean")):
            with self.subTest(kind=kind):
                diagnostic, _ = self.hold(stream(datapoints_config=[value]))
                self.assertEqual(diagnostic["target_configuration_shape"], dict(datapoints_config=dict(
                    present=True, json_type="array", item_count=1, item_types=[kind],
                    item_types_truncated=False)))

    def test_multi_config_never_falls_back_to_first_valid_object(self):
        for configs in ([dict(interval=60000), dict(interval=60000)],
                        [dict(interval=60000), None, "WITHHELD"]):
            diagnostic, _ = self.hold(stream(datapoints_config=configs))
            facts = diagnostic["target_configuration_shape"]["datapoints_config"]
            self.assertEqual(facts["item_count"], len(configs))
            self.assertEqual(facts["item_types"], ["object", "object"] if len(configs) == 2 else
                             ["object", "null", "string"])
            self.assertFalse(facts["item_types_truncated"])

    def test_ordered_types_have_fixed_eight_item_bound_and_exact_total(self):
        entries = [None, {}, [], "WITHHELD", 876543210, 876543.210987, True, {}]
        types = ["null", "object", "array", "string", "integer", "number", "boolean", "object"]
        self.assertEqual(probe.CONFIG_ITEM_TYPE_LIMIT, 8)
        for tail in ([], [dict(PRIVATE_TAIL="WITHHELD")], [None] * 992):
            configs = entries + tail
            diagnostic, body = self.hold(stream(datapoints_config=configs))
            self.assertEqual(diagnostic["target_configuration_shape"], dict(datapoints_config=dict(
                present=True, json_type="array", item_count=len(configs), item_types=types,
                item_types_truncated=bool(tail))))
            self.assertNotIn(b"PRIVATE_TAIL", body)
            self.assertNotIn(b"WITHHELD", body)

    def test_target_beyond_generic_sample_uses_target_not_sibling_shape(self):
        others = [stream(_id=f"{i:024x}", datapoints_config=[dict(interval=60000)],
                         public_level=0, is_hidden=True) for i in range(3)]
        target = stream(datapoints_config=[None, [], False])
        diagnostic, body = self.hold(target, others)
        self.assertEqual(diagnostic["target_configuration_shape"], dict(datapoints_config=dict(
            present=True, json_type="array", item_count=3,
            item_types=["null", "array", "boolean"], item_types_truncated=False)))
        self.assertFalse(any("$.data[3]" in field["path"] for field in diagnostic["shape"]["fields"]))
        self.assertNotIn(probe.STREAM.encode(), body)
        for other in others:
            self.assertNotIn(other["_id"].encode(), body)

    def test_earlier_access_page_terms_and_attributes_holds_have_no_config_context(self):
        missing_config = stream(); del missing_config["datapoints_config"]
        wrong_medium = stream(datapoints_config=[])
        wrong_medium["terms"]["ds"]["Medium"] = "Air"
        wrong_aggregate = stream(datapoints_config=[])
        wrong_aggregate["terms"]["ds"]["Aggregate"] = "Unsupported"
        bad_dq = stream(datapoints_config=[])
        bad_dq["terms"]["dq"] = dict(Measurement=None)
        cases = [
            [station(public_level=0), page([missing_config])],
            [station(is_hidden=True), page([missing_config])],
            [station(), page([dict(missing_config, public_level=0)])],
            [station(), page([dict(missing_config, is_hidden=True)])],
            [station(), page([dict(missing_config, is_deleted=True)])],
            [station(), page([dict(missing_config, station_id=OTHER)])],
            [station(), page([missing_config], total=2)],
            [station(), page([missing_config], limit=1)],
            [station(), page([missing_config], skip=1)],
            [station(), page([dict(missing_config, _id=OTHER)])],
            [station(), page([missing_config, missing_config])],
            [station(), page([dict(missing_config, terms=None)])],
            [station(), page([dict(missing_config, attributes=None)])],
            [station(), page([dict(missing_config, attributes=[])])],
            [station(), page([wrong_medium])],
            [station(), page([wrong_aggregate])],
            [station(), page([bad_dq])],
        ]
        for i, bodies in enumerate(cases):
            with self.subTest(case=i):
                h = Harness(bodies)
                with self.assertRaises(MetadataAdmissionHold):
                    h.run()
                diagnostic = decode(h.saved[-1][1])
                self.assertNotIn("target_configuration_shape", diagnostic)
                self.assertNotEqual(diagnostic["reason"]["code"], "science.cadence_shape")

    def test_later_interval_date_and_projection_holds_have_no_config_context(self):
        for target in (stream(datapoints_config=[{}]), stream(datapoints_config=[dict(interval=0)]),
                       stream(datapoints_config=[dict(interval=True)]),
                       stream(datapoints_config=[dict(interval=60000, starts_at="INVALID_TIME")]),
                       stream(attributes=dict(output_unit=[]))):
            with self.subTest(target=target):
                h = Harness([station(), page([target])])
                with self.assertRaises(MetadataAdmissionHold):
                    h.run()
                diagnostic = decode(h.saved[-1][1])
                self.assertNotIn("target_configuration_shape", diagnostic)
                self.assertNotEqual(diagnostic["reason"]["code"], "science.cadence_shape")

    def test_diagnostic_contains_no_configuration_values_or_nested_names(self):
        configs = [dict(PRIVATE_CONFIGURATION_KEY="PRIVATE_CONFIG_VALUE", interval=876543210,
                        starts_at="2098-07-06T05:04:03Z", calibration=dict(PRIVATE_NESTED="WITHHELD")),
                   ["PRIVATE_ARRAY_VALUE"], "PRIVATE_SCALAR_VALUE", 876543.210987]
        diagnostic, body = self.hold(stream(datapoints_config=configs,
            geo=dict(coordinates=[123.987654321, 34.987654321, 987.654321])))
        for marker in (b"PRIVATE_CONFIGURATION_KEY", b"PRIVATE_CONFIG_VALUE", b"876543210",
                       b"2098-07-06T05:04:03Z", b"PRIVATE_NESTED", b"WITHHELD", b"PRIVATE_ARRAY_VALUE",
                       b"PRIVATE_SCALAR_VALUE", b"876543.210987", b"123.987654321",
                       b"34.987654321", b"987.654321", probe.STREAM.encode(), probe.STATION.encode()):
            self.assertNotIn(marker, body)
        context = diagnostic["target_configuration_shape"]["datapoints_config"]
        self.assertEqual(set(context), {"present", "json_type", "item_count", "item_types", "item_types_truncated"})

    def test_target_configuration_context_survives_generic_byte_trimming(self):
        keys = sorted(SHAPE_KEYS)[:16]
        extra = {a: {b: {c: None for c in keys} for b in keys}
                 for a in keys if a not in page()}
        target = stream(datapoints_config=[{}] * 20)
        self.assertGreater(len(encode(metadata_shape(page([target], **extra), "datastream-list"))), DIAGNOSTIC_BYTES)
        diagnostic, body = self.hold(target, **extra)
        self.assertEqual(DIAGNOSTIC_BYTES, 12288)
        self.assertLessEqual(len(body), 12288)
        self.assertTrue(diagnostic["shape"]["truncated"])
        self.assertLess(len(diagnostic["shape"]["fields"]), SHAPE_FIELDS)
        self.assertEqual(diagnostic["target_configuration_shape"], dict(datapoints_config=dict(
            present=True, json_type="array", item_count=20, item_types=["object"]*8,
            item_types_truncated=True)))

    def test_successful_conditional_packets_and_optional_z_unchanged(self):
        for attrs in ("missing", {}, dict(output_unit="native")):
            for coords in ([-116.5, 34.5], [-116.5, 34.5, -123.25]):
                target = stream()
                if attrs == "missing":
                    del target["attributes"]
                else:
                    target["attributes"] = attrs
                h = Harness([station(geo=dict(type="Point", coordinates=coords)), page([target])])
                packet = h.run()
                for _, saved in h.saved:
                    self.assertNotIn("target_configuration_shape", decode(saved))
                self.assertEqual(packet["schema_version"], probe.PACKET_VERSION)
                self.assertEqual(packet["scale_assertions"], [])
                self.assertEqual(packet["historical_applicability"], dict(kind="unknown_history"))
                self.assertFalse(packet["raw_eligible"])
                self.assertNotIn("geometry", packet)
                self.assertNotIn("geo_z_native", packet)
                admitted = decode(h.saved[0][1])
                expected = historical._parse_station(encode(station(geo=dict(type="Point", coordinates=coords))),
                                                      probe.STATION, checked_at=NOW, now=NOW)
                self.assertEqual(encode(admitted), encode(expected))

    def test_configuration_context_is_not_attached_for_other_roster_stream(self):
        sid = "63531a67a9b61453fa1ca4ed"
        identity = INVENTORY.identity(sid)
        target = stream(_id=sid, station_id=identity["station_id"], datapoints_config=[])
        target["terms"]["dt"]["Unit"] = identity["native_unit"]
        admitted = historical._parse_station(encode(station(_id=identity["station_id"])),
                                              identity["station_id"], checked_at=NOW, now=NOW)
        with self.assertRaisesRegex(Hold, "Missing or ambiguous configured cadence") as caught:
            probe.review_packet(encode(page([target])), admitted, INVENTORY, stream_id=sid,
                                metadata_profile=probe.REVIEW_PROFILE, checked_at=NOW, now=NOW)
        self.assertFalse(hasattr(caught.exception, "target_configuration_shape"))


class TemporalConfigurationTests(unittest.TestCase):
    A = "2020-02-29T00:00:00.000Z"
    B = "2021-01-01T00:00:00.000Z"
    C = "2022-01-01T00:00:00.000Z"

    def config(self, start=None, end=None, **extra):
        return dict(begins_at=start or self.A, ends_before=end or self.B, interval=60000, **extra)

    def packet(self, configs=None, **updates):
        target = stream(datapoints_config=configs if configs is not None else [self.config()], **updates)
        admitted = historical._parse_station(encode(station()), probe.STATION, checked_at=NOW, now=NOW)
        return probe.review_packet(encode(page([target])), admitted, INVENTORY, stream_id=probe.STREAM,
                                   metadata_profile=probe.TEMPORAL_PROFILE, checked_at=NOW, now=NOW)

    def evidence(self, configs, reason=None, **updates):
        with self.assertRaises(Hold) as caught:
            self.packet(configs, **updates)
        if reason:
            self.assertIn(reason, str(caught.exception))
        return caught.exception.temporal_configuration_evidence

    def harness(self, target, station_row=None):
        h = Harness([station_row or station(), page([target])])
        plan = probe.make_plan(INVENTORY, station_id=probe.STATION, stream_id=probe.STREAM,
                               metadata_profile=probe.TEMPORAL_PROFILE)
        h.probe = probe.Probe(INVENTORY, plan)
        return h

    def test_adjacent_half_open_windows_and_differing_cadence(self):
        configs = [self.config(), dict(self.config(self.B, self.C), interval=300000)]
        packet = self.packet(configs)
        evidence = packet["configuration_evidence"]
        self.assertEqual(evidence["relations"], [dict(left_ordinal=0, right_ordinal=1, relation="ADJACENT")])
        self.assertEqual([x["fields"]["interval"]["value"] for x in evidence["configurations"]], [60000, 300000])
        self.assertNotIn("configured_cadence_seconds", packet)
        self.assertEqual(packet["configuration_state"], "PRESENT_TEMPORAL_ARRAY")

    def test_original_order_retained_separately_from_temporal_order(self):
        configs = [self.config(self.B, self.C), self.config()]
        evidence = self.packet(configs)["configuration_evidence"]
        self.assertEqual(evidence["temporal_order"], [1, 0])
        self.assertEqual([x["ordinal"] for x in evidence["configurations"]], [0, 1])
        self.assertEqual([x["object_sha256"] for x in evidence["configurations"]], [digest(c) for c in configs])

    def test_gaps_remain_gaps_without_filling_or_por_claim(self):
        e = self.packet([self.config(), self.config(self.C, "2023-01-01T00:00:00.000Z")])["configuration_evidence"]
        self.assertEqual(e["relations"][0]["relation"], "GAP")
        self.assertEqual(len(e["configurations"]), 2)
        self.assertEqual(e["historical_applicability"], dict(kind="unknown_history"))

    def test_overlaps_never_choose_a_winner(self):
        e = self.evidence([self.config(self.A, self.C), self.config()], "temporal_overlap")
        self.assertEqual(len(e["configurations"]), 2)
        self.assertEqual(e["relations"][0]["relation"], "OVERLAP")
        self.assertFalse(e["metadata_admitted"])

    def test_nested_overlap_checks_all_pairs(self):
        e = self.evidence([self.config(self.A, self.C), self.config(),
                           self.config(self.B, self.C)], "temporal_overlap")
        self.assertEqual(len(e["relations"]), 3)
        self.assertEqual([x["relation"] for x in e["relations"]], ["OVERLAP", "OVERLAP", "ADJACENT"])

    def test_duplicate_objects_preserved_not_collapsed(self):
        c = self.config()
        e = self.evidence([c, copy.deepcopy(c)], "temporal_duplicate")
        self.assertEqual(len(e["configurations"]), 2)
        self.assertEqual(e["configurations"][1]["duplicate_of_ordinal"], 0)
        self.assertIn("Review temporal_overlap", e["hold_reasons"])

    def test_absent_end_is_open_and_not_a_fabricated_timestamp(self):
        c = self.config(self.B, self.C); del c["ends_before"]
        e = self.packet([self.config(), c])["configuration_evidence"]
        end = e["configurations"][1]["fields"]["ends_before"]
        self.assertEqual(end["validation"], "ABSENT_OPEN_END")
        self.assertNotIn("value", end)
        self.assertEqual(e["relations"][0]["relation"], "ADJACENT")
        self.assertIn("Review temporal_overlap", self.evidence([c, self.config(self.C, "2023-01-01T00:00:00.000Z")])["hold_reasons"])

    def test_missing_start_and_null_bounds_remain_distinct(self):
        c = self.config(); del c["begins_at"]
        e = self.evidence([c], "temporal_unknown_bound")
        self.assertEqual(e["configurations"][0]["fields"]["begins_at"]["validation"], "ABSENT_UNKNOWN")
        for key in ("begins_at", "ends_before"):
            with self.subTest(key=key):
                e = self.evidence([dict(self.config(), **{key: None})], "temporal_date")
                self.assertEqual(e["configurations"][0]["fields"][key]["validation"], "NULL_UNSUPPORTED")

    def test_invalid_dates_and_inverted_or_empty_windows_hold(self):
        for raw in ("2021-02-29T00:00:00.000Z", "2020-01-01T25:00:00.000Z", "2020-01-01T00:00:00+24:00",
                    "credential_MARKER", "2020-01-01", [], True, 123):
            with self.subTest(raw=raw):
                e = self.evidence([dict(self.config(), begins_at=raw)], "temporal_date")
                self.assertNotIn("value", e["configurations"][0]["fields"]["begins_at"])
        for end in (self.A, "2019-01-01T00:00:00.000Z"):
            self.evidence([self.config(self.A, end)], "temporal_window")

    def test_source_precision_and_timezone_preserved_on_unsupported_format_hold(self):
        for raw in ("2020-02-29T00:00:00.123456789012345678Z", "2020-02-28T16:00:00.123-08:00",
                    "2020-02-29T00:00:00Z", "2020-02-29T00:00:00.000-00:00"):
            with self.subTest(raw=raw):
                e = self.evidence([dict(self.config(), begins_at=raw)], "temporal_date")
                f = e["configurations"][0]["fields"]["begins_at"]
                self.assertEqual(f["value"], raw)
                self.assertEqual(f["validation"], "UNSUPPORTED_R2_FORMAT")
                self.assertFalse(e["temporal_order_complete"])

    def test_exact_millisecond_shared_boundary_not_rounded(self):
        end = "2021-01-01T00:00:00.001Z"
        e = self.packet([self.config(self.A, end), self.config(end, self.C)])["configuration_evidence"]
        self.assertEqual(e["relations"][0]["relation"], "ADJACENT")
        e = self.evidence([self.config(self.A, end), self.config(self.B, self.C)], "temporal_overlap")
        self.assertEqual(e["relations"][0]["relation"], "OVERLAP")

    def test_missing_interval_not_invented_null_and_invalid_hold(self):
        c = self.config(); del c["interval"]
        e = self.packet([c])["configuration_evidence"]
        f = e["configurations"][0]["fields"]["interval"]
        self.assertEqual(f["validation"], "ABSENT_UNSPECIFIED")
        self.assertNotIn("value", f)
        for val in (None, True, 0, -1, "60000", [], {}, 2**54):
            with self.subTest(val=val):
                e = self.evidence([dict(self.config(), interval=val)], "temporal_interval")
                self.assertNotIn("value", e["configurations"][0]["fields"]["interval"])

    def test_starts_at_never_aliases_begins_at(self):
        c = self.config(); c["starts_at"] = c.pop("begins_at")
        e = self.evidence([c], "temporal_unreviewed_fields")
        self.assertEqual(e["configurations"][0]["unknown_field_count"], 1)
        self.assertIn("Review temporal_unknown_bound", e["hold_reasons"])

    def test_backend_values_and_unknown_key_names_never_retained(self):
        c = self.config(connection="SECRET_CONNECTION", path="PRIVATE_PATH", params={"password": "SECRET_PARAM"})
        e = self.packet([c])["configuration_evidence"]
        self.assertEqual(e["configurations"][0]["omitted_backend_field_count"], 3)
        for marker in (b"SECRET_", b"PRIVATE_PATH", b"password"):
            self.assertNotIn(marker, encode(e))
        c["PRIVATE_KEY"] = {"SECRET_NESTED": 99}
        e = self.evidence([c], "temporal_unreviewed_fields")
        self.assertEqual(e["configurations"][0]["unknown_field_count"], 1)
        self.assertNotIn(b"PRIVATE_KEY", encode(e))
        self.assertNotIn(b"SECRET_NESTED", encode(e))

    def test_actions_held_without_execution_or_transform_claim(self):
        c = self.config(actions=dict(evaluate="SECRET_EXPRESSION"))
        e = self.evidence([c], "temporal_actions")
        self.assertNotIn(b"SECRET_EXPRESSION", encode(e))
        self.assertEqual(e["scale_assertions"], [])
        for key, value in (("connection", {}), ("path", None), ("params", "SECRET")):
            self.evidence([dict(self.config(), **{key: value})], "temporal_backend_shape")

    def test_safe_evidence_survives_later_science_and_description_holds(self):
        cases = [dict(attributes=None), dict(attributes=dict(calibration=dict(multiplier="SECRET"))),
                 dict(is_active="SECRET"), dict(terms=dict(dt=dict(Unit="Percent")))]
        for updates in cases:
            with self.subTest(updates=updates):
                e = self.evidence([self.config()], **updates)
                self.assertEqual(e["configurations"][0]["fields"]["interval"]["value"], 60000)
                self.assertFalse(e["metadata_admitted"])
                self.assertNotIn(b"SECRET", encode(e))

    def test_probe_persists_partial_record_separate_from_bounded_diagnostic(self):
        h = self.harness(stream(datapoints_config=[self.config()], attributes=None))
        with self.assertRaises(MetadataAdmissionHold) as caught:
            h.run()
        receipt, raw = h.saved[-1]; value = decode(raw)
        self.assertEqual(receipt["outcome"], "HOLD")
        self.assertEqual(value["schema_version"], probe.TEMPORAL_EVIDENCE)
        self.assertEqual(value["configuration_evidence"], caught.exception.temporal_configuration_evidence)
        self.assertEqual(value["diagnostic"], caught.exception.diagnostic)
        self.assertLessEqual(len(encode(value["diagnostic"])), DIAGNOSTIC_BYTES)
        self.assertLessEqual(len(raw), 65536)
        self.assertFalse(value["metadata_admitted"])
        self.assertEqual(receipt["sanitized_sha256"], sha(raw))
        with self.assertRaises(Hold):
            h.run()

    def test_private_target_or_station_never_yields_temporal_values(self):
        for hidden_station, hidden_stream in ((True, False), (False, True)):
            h = self.harness(stream(datapoints_config=[self.config()], is_hidden=hidden_stream),
                             station(is_hidden=hidden_station))
            with self.assertRaises(MetadataAdmissionHold):
                h.run()
            self.assertNotIn(b"configuration_evidence", h.saved[-1][1])
            self.assertNotIn(self.A.encode(), h.saved[-1][1])

    def test_optional_z_and_coordinates_not_in_new_packet(self):
        h = self.harness(stream(datapoints_config=[self.config()]),
                         station(geo=dict(type="Point", coordinates=[-116.5, 34.5, 123.456789])))
        packet = h.run()
        self.assertNotIn(b"123.456789", encode(packet))
        self.assertNotIn(b"coordinates", encode(packet))
        self.assertNotIn(b"geo_z_native", encode(packet))
        self.assertEqual(decode(h.saved[0][1]), historical._parse_station(
            encode(station(geo=dict(type="Point", coordinates=[-116.5, 34.5, 123.456789]))),
            probe.STATION, checked_at=NOW, now=NOW))

    def test_profile_source_packet_and_object_bindings_deterministic(self):
        a = self.packet(); b = self.packet()
        self.assertEqual(encode(a), encode(b))
        self.assertEqual(a["schema_version"], probe.TEMPORAL_PACKET)
        self.assertEqual(a["metadata_binding"]["configuration_evidence_sha256"], digest(a["configuration_evidence"]))
        e = a["configuration_evidence"]
        self.assertEqual(e["configuration"]["sha256"], digest([self.config()]))
        self.assertEqual(e["selected_record_sha256"], digest(stream(datapoints_config=[self.config()])))
        self.assertEqual(e["original_response_sha256"], sha(encode(page([stream(datapoints_config=[self.config()])]))))
        plan = probe.make_plan(INVENTORY, station_id=probe.STATION, stream_id=probe.STREAM,
                               metadata_profile=probe.TEMPORAL_PROFILE)
        self.assertNotEqual(plan["metadata_profile_sha256"], PLAN["metadata_profile_sha256"])
        plan["metadata_profile"] = probe.REVIEW_PROFILE
        with self.assertRaises(Hold):
            probe.Probe(INVENTORY, plan)

    def test_no_scale_or_permissions_or_frozen_identity_changes(self):
        packet = self.packet(attributes=dict(scale=dict(value=100, unit="Percent"),
                                            depth=dict(value=20, unit="Centimeter")))
        for p in (packet, packet["configuration_evidence"]):
            for key in ("raw_eligible", "observation_acquisition_authorized", "daily_science_accepted", "browser_publication_eligible"):
                self.assertIs(p[key], False)
            self.assertEqual(p["scale_assertions"], [])
            self.assertEqual(p["historical_applicability"], dict(kind="unknown_history"))
        self.assertEqual(packet["frozen_identity"], probe.IDENTITY)
        self.assertEqual(packet["unit_status"], "native_only_scale_unresolved")

    def test_shape_and_count_limits_record_omissions_without_partial_admission(self):
        for configs in (None, {}, [], [None], [42]):
            # packet helper defaults None, so use the injected runner for null.
            h = self.harness(stream(datapoints_config=configs))
            with self.assertRaises(MetadataAdmissionHold):
                h.run()
            self.assertEqual(decode(h.saved[-1][1])["outcome"], "HOLD")
        configs = [self.config() for _ in range(probe.TEMPORAL_CONFIG_LIMIT + 1)]
        e = self.evidence(configs, "temporal_bound")
        self.assertFalse(e["evidence_complete"])
        self.assertEqual(e["omitted_configurations"], 1)
        self.assertEqual(e["configuration"]["sha256"], digest(configs))
        self.assertEqual(len(e["configurations"]), probe.TEMPORAL_CONFIG_LIMIT)
        self.assertLessEqual(len(encode(e)), probe.TEMPORAL_EVIDENCE_BYTES)

    def test_unknown_content_changes_full_hash_and_keeps_hold(self):
        one = self.config(unknown=dict(value=1)); two = self.config(unknown=dict(value=2))
        a = self.evidence([one]); b = self.evidence([two])
        self.assertNotEqual(a["configuration"]["sha256"], b["configuration"]["sha256"])
        self.assertNotEqual(a["configurations"][0]["unknown_fields_sha256"], b["configurations"][0]["unknown_fields_sha256"])
        self.assertEqual(a["hold_reasons"], ["Review temporal_unreviewed_fields"])

    def test_old_singleton_profile_and_historical_guard_unchanged(self):
        h = Harness([station(), page([stream(datapoints_config=[self.config(), self.config(self.B, self.C)])])])
        with self.assertRaises(MetadataAdmissionHold) as caught:
            h.run()
        self.assertEqual(caught.exception.diagnostic["reason"]["code"], "science.cadence_shape")
        self.assertNotIn("configuration_evidence", decode(h.saved[-1][1]))
        self.assertEqual(Harness().run()["configured_cadence_seconds"], 60)
        with self.assertRaisesRegex(Hold, "Missing or ambiguous configured cadence"):
            historical._scientific(stream(datapoints_config=[self.config(), self.config(self.B, self.C)]))

    def test_r2_ascii_date_grammar_rejects_unicode_fractional_digits(self):
        e = self.evidence([dict(self.config(), begins_at="2020-02-29T00:00:00.٠١٢Z")], "temporal_date")
        self.assertNotIn("value", e["configurations"][0]["fields"]["begins_at"])

    def test_maximum_projection_remains_bounded_and_durable_on_hold(self):
        c = dict(begins_at="2020-02-29T00:00:00.123456789012345678+23:59",
                 ends_before="2022-02-28T00:00:00.123456789012345678-23:59",
                 interval=2**53-1, connection="SECRET", path="SECRET", params=dict(secret="SECRET"),
                 actions=dict(evaluate="SECRET"), unknown="SECRET")
        h = self.harness(stream(datapoints_config=[c for _ in range(probe.TEMPORAL_CONFIG_LIMIT)]))
        with self.assertRaises(MetadataAdmissionHold):
            h.run()
        raw = h.saved[-1][1]; e = decode(raw)["configuration_evidence"]
        self.assertLessEqual(len(raw), 65536)
        self.assertLessEqual(len(encode(e)), probe.TEMPORAL_EVIDENCE_BYTES)
        self.assertEqual(len(e["configurations"]), probe.TEMPORAL_CONFIG_LIMIT)
        self.assertNotIn(b"SECRET", raw)
        self.assertFalse(decode(raw)["metadata_admitted"])
