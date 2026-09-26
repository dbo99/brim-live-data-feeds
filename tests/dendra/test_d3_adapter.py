"""Focused D3 boundary tests using finite synthetic bytes and retained task roots.

Requires explicit DENDRA_INVENTORY and DENDRA_TEST_ROOT. No live probe, provider
data, real sleep, numerical replay or production output is used by this module.
"""
import copy
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import io
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

NETWORK_ATTEMPTS = []
SLEEP_ATTEMPTS = []


def deny_network(event, args):
    if event.startswith("socket."):
        NETWORK_ATTEMPTS.append(event)
        raise AssertionError("Socket/DNS activity is forbidden in D3 readiness tests")


def deny_sleep(seconds):
    SLEEP_ATTEMPTS.append(seconds)
    raise AssertionError("Real sleep is forbidden in D3 readiness tests")


# Install before importing any repository module, including the live boundary.
sys.addaudithook(deny_network)
SLEEP_PATCH = patch("time.sleep", deny_sleep)
SLEEP_PATCH.start()
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from dendra.history_acquisition.d3_plan import (
    BASE, CAMP, DEEP, SELECTED, START, END, IDENTITIES, CEILINGS,
    RequestSpec, make_campaign, seven_calls, validate_binding, validate_request,
)
from dendra.history_acquisition.journal import Journal, UnknownSourceRowCount
from dendra.history_acquisition.model import Inventory, INVENTORY_SHA256
from dendra.history_acquisition.provider_adapter import (
    Adapter, BODY_LIMIT, RETRYABLE, NoRedirect, retry_delay, run_authorized_probe,
    MetadataAdmissionHold, metadata_shape, DIAGNOSTIC_BYTES, SHAPE_FIELDS, SHAPE_KEYS,
)
from dendra.history_acquisition.provider_metadata import load_authority, parse_station
from dendra.history_acquisition.safety import Hold, decode, digest, encode, sha
from dendra.transport import FetchError, parse_utc, format_utc


def setUpModule():
    global INVENTORY, AUTHORITY, TEST_ROOT
    INVENTORY = Inventory.load(os.environ["DENDRA_INVENTORY"], INVENTORY_SHA256)
    catalog = Path(__file__).resolve().parents[2] / "data/input/dendra/pilot_catalog.json"
    AUTHORITY = load_authority(INVENTORY, catalog)
    TEST_ROOT = Path(os.environ["DENDRA_TEST_ROOT"])
    if not TEST_ROOT.is_absolute() or not TEST_ROOT.is_dir():
        raise ValueError("Explicit existing task-owned test root is required")


def tearDownModule():
    SLEEP_PATCH.stop()
    if NETWORK_ATTEMPTS or SLEEP_ATTEMPTS:
        raise AssertionError(f"Forbidden activity: sockets={NETWORK_ATTEMPTS}, sleeps={SLEEP_ATTEMPTS}")


class Clock:
    def __init__(self):
        self.seconds = 0.0

    def now(self):
        return (datetime(2026, 9, 25, 22, tzinfo=timezone.utc) +
                timedelta(seconds=self.seconds)).isoformat().replace("+00:00", "Z")

    def monotonic(self):
        return self.seconds

    def advance(self, seconds):
        self.seconds += seconds


class Reply(io.BytesIO):
    def __init__(self, body=b"", status=200, headers=None, *, delay=0):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}
        self.delay = delay

    def read(self, size=-1):
        if not 0 < signal.getitimer(signal.ITIMER_REAL)[0] <= 25:
            raise AssertionError("Total request timer is absent during body read")
        if not 0 <= size <= 65536:
            raise AssertionError("Unbounded fake response read")
        return super().read(size)


class FiniteExecutor:
    """Every fake dispatch verifies its existing durable reservation first."""
    def __init__(self, journal, clock, responses):
        self.journal, self.clock = journal, clock
        self.responses = list(responses)
        self.calls, self.waits = [], []

    def __call__(self, request, timeout):
        attempts = list(self.journal.snapshot()["attempts"].values())
        if not attempts or attempts[-1]["state"] != "started":
            raise AssertionError("Fake dispatch preceded durable reserve/start")
        if not 0 < timeout <= 25:
            raise AssertionError("Unbounded request timeout")
        if not 0 < signal.getitimer(signal.ITIMER_REAL)[0] <= 25:
            raise AssertionError("Total request timer is absent at fake dispatch")
        self.calls.append((request.full_url, timeout, attempts[-1]["attempt_key"]))
        if not self.responses:
            raise AssertionError("Finite fake responses exhausted; no live fallback")
        response = self.responses.pop(0)
        self.clock.advance(0.01)
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, bytes):
            response = Reply(response)
        self.clock.advance(response.delay)
        return response

    def wait(self, seconds):
        self.waits.append(seconds)
        self.clock.advance(seconds)


def vocabulary():
    return {"_id": "dt-unit", "terms": copy.deepcopy(list(AUTHORITY["dictionary_terms"].values()))}


def station(sid):
    return dict(_id=IDENTITIES[sid]["station_id"], name="Synthetic public station",
                public_level=3, is_hidden=False, is_geo_protected=False,
                geo=dict(type="Point", coordinates=[-116.2, 34.1]),
                updated_at="2026-09-24T00:00:00Z", revision="synthetic-1")


def datastreams(sid):
    science = AUTHORITY["scientific"][sid]
    stream = dict(_id=sid, station_id=IDENTITIES[sid]["station_id"],
                  name="Synthetic public selected stream", public_level=3,
                  is_hidden=False, is_geo_protected=False, is_enabled=True,
                  terms=copy.deepcopy(science["source_terms"]),
                  attributes=copy.deepcopy(science["source_attributes"]),
                  datapoints_config=[dict(interval=science["cadence_seconds"] * 1000)],
                  updated_at="2026-09-24T00:00:00Z", revision="synthetic-1")
    return dict(data=[stream], limit=500, total=1, skip=0)


def page(rows=None, limit=2016):
    return encode(dict(data=[] if rows is None else rows, limit=limit))


def metadata_bodies():
    return [encode(vocabulary()), encode(station(CAMP)), encode(station(DEEP)),
            encode(datastreams(CAMP)), encode(datastreams(DEEP))]


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.open_journals = []

    def tearDown(self):
        for journal in reversed(self.open_journals):
            journal.close()

    def new_journal(self, *, budget=None):
        clock = Clock()
        binding, tasks = make_campaign(INVENTORY, copy.deepcopy(AUTHORITY), selected_ids=SELECTED,
            interval=(START, END), provider_budget=copy.deepcopy(budget or CEILINGS),
            campaign_id="d3-synthetic", as_of=clock.now(), request_generation="synthetic-1")
        root = Path(tempfile.mkdtemp(prefix=self._testMethodName + "-", dir=TEST_ROOT))
        journal = Journal(root, binding, tasks, create=True, now=clock.now, monotonic=clock.monotonic)
        self.open_journals.append(journal)
        return journal, clock, root

    def run_fake(self, responses=None, *, journal=None, clock=None):
        if journal is None:
            journal, clock, _ = self.new_journal()
        executor = FiniteExecutor(journal, clock, responses or metadata_bodies() + [page(), page()])
        result = Adapter(journal).run(executor=executor, wait=executor.wait)
        return journal, clock, executor, result

    def assert_run_holds(self, responses, *, budget=None):
        journal, clock, _ = self.new_journal(budget=budget)
        executor = FiniteExecutor(journal, clock, responses)
        with self.assertRaises((Hold, FetchError, ValueError)):
            Adapter(journal).run(executor=executor, wait=executor.wait)
        return journal, executor

    def diagnostic_failure(self, body, code, *, prefix=None):
        journal, clock, root = self.new_journal()
        bodies = ([encode(vocabulary())] if prefix is None else prefix) + [body]
        executor = FiniteExecutor(journal, clock, bodies)
        with self.assertRaises(MetadataAdmissionHold) as caught:
            Adapter(journal).run(executor=executor, wait=executor.wait)
        self.assertIn(code, str(caught.exception))
        snapshot = journal.snapshot()
        receipt = list(snapshot["attempts"].values())[-1]
        diagnostic = decode(journal.read_object(receipt["objects"][0]))
        self.assertEqual(diagnostic, caught.exception.diagnostic)
        self.assertEqual(diagnostic["reason"]["code"], code)
        self.assertEqual(diagnostic["body_sha256"], receipt["response_sha256"])
        self.assertEqual(diagnostic["body_bytes"], receipt["response_bytes"])
        self.assertEqual(receipt["representation"], "sanitized")
        self.assertEqual(receipt["details"]["outcome"], "hold")
        self.assertFalse(receipt["details"]["retryable"])
        self.assertEqual(executor.waits, [])
        self.assertEqual(len(executor.calls), len(bodies))
        self.assertEqual(snapshot["counters"]["attempts"], len(bodies))
        self.assertEqual(snapshot["counters"]["response_bytes"], sum(map(len, bodies)))
        self.assertTrue(all(s["state"] == "held" for s in snapshot["intervals"].values()))
        self.assertTrue(all("/datapoints?" not in call[0] for call in executor.calls))
        return journal, clock, root, receipt, diagnostic

    def test_diagnostic_masked_station_reason_survives_unknown_row_guard(self):
        value = station(CAMP)
        del value["_id"]
        journal, _, _, receipt, diagnostic = self.diagnostic_failure(encode(value), "station.id_missing")
        self.assertIsNone(receipt["source_rows"])
        self.assertEqual(journal.snapshot()["counters"]["unknown_row_responses"], 1)
        self.assertEqual(receipt["state"], "failure")
        self.assertEqual(diagnostic["reason"]["category"], "missing_field")
        self.assertEqual(diagnostic["reason"]["parser_site"]["function"], "parse_station")
        before = journal.snapshot()["counters"]
        with self.assertRaisesRegex(UnknownSourceRowCount, "Unknown source row count"):
            journal.reserve("metadata-" + DEEP, "station")
        self.assertEqual(journal.snapshot()["counters"], before)

    def test_diagnostic_missing_wrong_type_and_unsupported_shape_are_distinct(self):
        missing = station(CAMP)
        del missing["is_hidden"]
        cases = [(list(), "metadata.object_required", "unsupported_shape"),
                 ({}, "station.id_missing", "missing_field"),
                 (station(CAMP) | {"_id": []}, "station.id_type", "unexpected_type"),
                 (missing, "access.hidden_missing", "missing_field"),
                 (station(CAMP) | {"public_level": "3"}, "access.level_type", "unexpected_type"),
                 (station(CAMP) | {"access_levels_resolved": []}, "access.level_shape", "unexpected_type")]
        for value, code, category in cases:
            with self.subTest(code=code):
                # The unchanged parser rejects the very same synthetic body.
                with self.assertRaises(Hold):
                    parse_station(encode(value), IDENTITIES[CAMP]["station_id"],
                                  checked_at=Clock().now(), now=Clock().now())
                *_, diagnostic = self.diagnostic_failure(encode(value), code)
                self.assertEqual(diagnostic["reason"]["category"], category)
        *_, diagnostic = self.diagnostic_failure(encode({}), "station.id_missing")
        self.assertFalse(diagnostic["shape"]["required_key_presence"]["_id"])

    def test_diagnostic_nested_shape_and_sensitive_values_are_not_retained(self):
        secret = "DO-NOT-PERSIST-synthetic-sensitive-content"
        value = station(CAMP)
        del value["public_level"]
        value.update(access_levels_resolved={"public_level": {"value": secret}},
                     name=secret, is_hidden=True, is_geo_protected=True,
                     geo={"type": "Point", "coordinates": [-117.987654321, 34.987654321]})
        value[secret] = {"cookie": secret, "Authorization": secret}
        journal, _, root, _, diagnostic = self.diagnostic_failure(encode(value), "access.level_type")
        fields = {f["path"]: f for f in diagnostic["shape"]["fields"]}
        self.assertEqual(fields["$.access_levels_resolved.public_level"]["type"], "object")
        self.assertEqual(fields["$.access_levels_resolved.public_level.value"]["type"], "string")
        persisted = b"".join(p.read_bytes() for p in root.rglob("*") if p.is_file())
        for forbidden in (secret.encode(), b"117.987654321", b"34.987654321", b"Authorization", b"cookie"):
            self.assertNotIn(forbidden, persisted)
        self.assertFalse(journal.snapshot()["metadata"][CAMP]["raw_eligible"])

    def test_diagnostic_shape_inventory_is_deterministic_and_bounded(self):
        value = {"unknown-sensitive-key-" + str(i): "withheld" for i in range(700)}
        value.update(station(CAMP))
        nested = {"data": [{"data": [{"data": [{"data": [dict(value)]}]}]}]}
        a = metadata_shape(nested, "station")
        b = metadata_shape(copy.deepcopy(nested), "station")
        self.assertEqual(a, b)
        self.assertTrue(a["truncated"])
        self.assertLessEqual(len(a["fields"]), SHAPE_FIELDS)
        self.assertTrue(all(len(f["path"]) <= 96 and len(f.get("keys", [])) <= 16 for f in a["fields"]))
        *_, diagnostic = self.diagnostic_failure(encode(value), "metadata.field_bound")
        self.assertTrue(diagnostic["shape"]["truncated"])
        self.assertLessEqual(len(encode(diagnostic)), DIAGNOSTIC_BYTES)
        self.assertNotIn(b"unknown-sensitive-key", encode(diagnostic))
        self.assertNotIn(b"withheld", encode(diagnostic))
        dense = {k: {name: [{"data": [1, 2, 3]}] for name in
                 ("_id", "data", "terms", "attributes", "geo", "name", "value")} for k in
                 ("_id", "data", "terms", "attributes", "geo", "name", "value")}
        bounded = metadata_shape(dense, "station")
        self.assertEqual(len(bounded["fields"]), SHAPE_FIELDS)
        self.assertTrue(bounded["truncated"])
        keys = sorted(SHAPE_KEYS)[:16]
        wide = {a: {b: {c: None for c in keys} for b in keys} for a in keys}
        self.assertGreater(len(encode(metadata_shape(wide, "station"))), DIAGNOSTIC_BYTES)
        *_, diagnostic = self.diagnostic_failure(encode(wide), "station.id_type")
        self.assertLessEqual(len(encode(diagnostic)), DIAGNOSTIC_BYTES)
        self.assertLess(len(diagnostic["shape"]["fields"]), SHAPE_FIELDS)
        self.assertTrue(diagnostic["shape"]["truncated"])

    def test_diagnostic_malformed_json_preserves_specific_safe_reason(self):
        body = b'{"sensitive-token":"do-not-store",'
        _, _, root, receipt, diagnostic = self.diagnostic_failure(body, "json.invalid")
        self.assertIsNone(receipt["source_rows"])
        self.assertEqual(diagnostic["shape"]["json_type"], "unparsed")
        self.assertNotIn(b"do-not-store", b"".join(p.read_bytes() for p in root.rglob("*") if p.is_file()))

    def test_diagnostic_private_hidden_and_identity_mismatch_still_hold(self):
        for changes, code in [(dict(public_level=0), "access.public_nonhidden_required"),
                              (dict(is_hidden=True), "access.public_nonhidden_required"),
                              (dict(is_deleted=True), "access.missing_or_deleted"),
                              (dict(_id=IDENTITIES[DEEP]["station_id"]), "station.id_mismatch")]:
            with self.subTest(changes=changes):
                journal, *rest = self.diagnostic_failure(encode(station(CAMP) | changes), code)
                self.assertFalse(journal.snapshot()["metadata"][CAMP]["raw_eligible"])

    def test_diagnostic_science_unit_and_completeness_rules_still_hold(self):
        changed = datastreams(CAMP)
        changed["data"][0]["attributes"]["depth"] = {"value": 300, "unit": "mm"}
        cases = [(changed, "science.identity_mismatch"),
                 (datastreams(CAMP) | {"total": 2}, "list.total"),
                 (datastreams(CAMP) | {"limit": 1}, "list.incomplete")]
        for value, code in cases:
            with self.subTest(code=code):
                _, _, _, receipt, _ = self.diagnostic_failure(encode(value), code,
                    prefix=metadata_bodies()[:3])
                self.assertEqual(receipt["source_rows"], 1)
        changed = vocabulary()
        changed["terms"][0]["abbreviation"] = "unaccepted"
        self.diagnostic_failure(encode(changed), "vocabulary.term_mismatch", prefix=[])

    def test_diagnostic_valid_and_descriptive_name_only_keep_prior_projection(self):
        value = station(CAMP)
        value["name"] = "Different permitted descriptive name"
        expected = parse_station(encode(value), IDENTITIES[CAMP]["station_id"],
                                 checked_at=Clock().now(), now=Clock().now())
        bodies = metadata_bodies()
        bodies[1] = encode(value)
        journal, _, executor, _ = self.run_fake(bodies + [page(), page()])
        receipt = list(journal.snapshot()["attempts"].values())[1]
        actual = decode(journal.read_object(receipt["objects"][0]))
        actual.pop("checked_at"); expected.pop("checked_at")
        self.assertEqual(actual, expected)
        self.assertNotIn("reason", actual)
        self.assertEqual(len(executor.calls), 7)
        self.assertEqual(journal.snapshot()["metadata"][DEEP]["identity"]["depth_cm"], None)

    def test_diagnostic_observation_unknown_rows_keep_original_guard(self):
        journal, clock, _ = self.new_journal()
        executor = FiniteExecutor(journal, clock, metadata_bodies() + [b'{"data":'])
        with self.assertRaisesRegex(UnknownSourceRowCount, "Unknown source row count"):
            Adapter(journal).run(executor=executor, wait=executor.wait)
        receipt = list(journal.snapshot()["attempts"].values())[-1]
        self.assertEqual(len(executor.calls), 6)
        self.assertIsNone(receipt["source_rows"])
        self.assertEqual(receipt["objects"], [])
        self.assertEqual(receipt["representation"], "omitted")
        self.assertEqual(journal.snapshot()["counters"]["unknown_row_responses"], 1)

    def test_diagnostic_complete_evidence_and_reopen_recovery_preserve_reason(self):
        journal, clock, root = self.new_journal()
        _, _, _, result = self.run_fake(metadata_bodies() + [page([dict(t=START, v=0)]), page()],
                                       journal=journal, clock=clock)
        key = next(k for k, v in journal.tasks.items() if v["identity"]["stream_id"] == CAMP)
        original = journal.completed(key)
        value = station(CAMP) | {"updated_at": []}
        adapter = Adapter(journal)
        executor = FiniteExecutor(journal, clock, [encode(value)])
        adapter.active, adapter.executor, adapter.wait = True, executor, executor.wait
        try:
            with self.assertRaisesRegex(MetadataAdmissionHold, "description.updated_timestamp"):
                adapter.metadata(RequestSpec("station", CAMP))
        finally:
            adapter.active, adapter.executor, adapter.wait = False, None, None
        self.assertEqual(journal.completed(key), original)
        self.assertEqual(original, result[CAMP]["envelope"])
        receipt = list(journal.snapshot()["attempts"].values())[-1]
        diagnostic = journal.read_object(receipt["objects"][0])
        self.assertEqual(journal.snapshot()["counters"]["attempts"], 8)
        before_files = {str(p.relative_to(root)): sha(p.read_bytes()) for p in root.rglob("*") if p.is_file()}
        binding, tasks = journal.binding, journal.tasks
        journal.close(); self.open_journals.remove(journal)
        for recovery in (False, True):
            with Journal(root, binding, tasks, recovery=recovery, now=clock.now, monotonic=clock.monotonic) as reopened:
                self.assertEqual(reopened.completed(key), original)
                self.assertEqual(reopened.read_object(receipt["objects"][0]), diagnostic)
                with self.assertRaises(UnknownSourceRowCount):
                    reopened.reserve("metadata-" + DEEP, "station")
                self.assertEqual(reopened.snapshot()["counters"]["attempts"], 8)
        self.assertEqual(before_files, {str(p.relative_to(root)): sha(p.read_bytes()) for p in root.rglob("*") if p.is_file()})
        self.assertEqual(len(executor.calls), 1)

    def test_diagnostic_storage_or_other_integrity_failure_is_not_suppressed(self):
        for failure in (OSError("synthetic storage"), Hold("Synthetic integrity failure")):
            journal, clock, _ = self.new_journal()
            executor = FiniteExecutor(journal, clock, [encode({})])
            with patch.object(journal, "received", side_effect=failure):
                with self.assertRaises(Hold) as caught:
                    Adapter(journal).run(executor=executor, wait=executor.wait)
            self.assertNotIsInstance(caught.exception, MetadataAdmissionHold)
            self.assertEqual(len(executor.calls), 1)
            self.assertEqual(executor.waits, [])
            self.assertEqual(journal.snapshot()["counters"]["attempts"], 1)

    def test_exact_seven_plan_and_encoded_queries(self):
        calls = seven_calls()
        self.assertEqual(len(calls), 7)
        self.assertEqual([s.kind for s in calls], ["unit-vocabulary", "station", "station",
            "datastream-list", "datastream-list", "observations", "observations"])
        self.assertEqual([s.selected_stream for s in calls[1:]], [CAMP, DEEP] * 3)
        for spec in calls:
            request = urllib.request.Request(spec.url(), headers={"Accept": "application/json",
                "User-Agent": "BRIM-Dendra-D3/1.0"}, method="GET")
            self.assertEqual(validate_request(request, spec), spec)
            self.assertNotIn("temperature", spec.url())
            self.assertNotIn("$skip", spec.url())
        for spec in calls[-2:]:
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(spec.url()).query))
            self.assertEqual(query, {"datastream_id": spec.selected_stream, "time[$gte]": START,
                "time[$lt]": END, "$sort[time]": "1", "$limit": "2016"})
            self.assertIn("time%5B%24gte%5D=2024-02-29T08%3A00%3A00.000Z", spec.url())

    def test_endpoint_method_query_header_and_identifier_rejections(self):
        spec = RequestSpec("observations", CAMP, START)
        original = spec.url()
        urls = [original.replace("https:", "http:"), original.replace("api.dendra.science", "example.invalid"),
                original.replace("api.dendra.science", "api.dendra.science:443"),
                original.replace("api.dendra.science", "user@api.dendra.science"),
                original.replace("/datapoints?", "/stations?"), original + "#fragment",
                original + "&unexpected=1", original + "&%24limit=2016", original + "&%24skip=1"]
        for url in urls:
            with self.subTest(url=url), self.assertRaises((Hold, ValueError)):
                validate_request(urllib.request.Request(url, headers={"Accept": "application/json",
                    "User-Agent": "BRIM-Dendra-D3/1.0"}), spec)
        for method in ("POST", "PUT", "DELETE", "HEAD"):
            with self.subTest(method=method), self.assertRaises(Hold):
                validate_request(urllib.request.Request(original, method=method), spec)
        for name in ("Cookie", "Authorization", "X-Extra"):
            with self.subTest(header=name), self.assertRaises(Hold):
                validate_request(urllib.request.Request(original, headers={"Accept": "application/json",
                    "User-Agent": "BRIM-Dendra-D3/1.0", name: "synthetic"}), spec)
        for sid in (CAMP + "/bad", CAMP + "?x=1", "", "not-approved"):
            with self.subTest(identifier=sid), self.assertRaises(Hold):
                RequestSpec("station", sid).url()
        with self.assertRaises(Hold):
            RequestSpec("datastream-list", CAMP, "next").url()
        with self.assertRaises(Hold):
            RequestSpec("observations", CAMP, END).url()

    def test_exact_selection_interval_and_budget_cannot_expand(self):
        kwargs = dict(selected_ids=SELECTED, interval=(START, END), provider_budget=dict(CEILINGS),
                      campaign_id="synthetic-plan", as_of=Clock().now(), request_generation="synthetic-1")
        mutations = [dict(selected_ids=(DEEP, CAMP)), dict(selected_ids=(CAMP,)),
                     dict(interval=("2024-02-28T08:00:00Z", END))]
        for key in CEILINGS:
            value = dict(CEILINGS)
            value[key] += 1
            mutations.append(dict(provider_budget=value))
        for changes in mutations:
            with self.subTest(changes=changes), self.assertRaises(Hold):
                make_campaign(INVENTORY, AUTHORITY, **(kwargs | changes))

    def test_forged_authority_and_self_consistent_rehash_hold(self):
        journal, _, _ = self.new_journal()
        forged = copy.deepcopy(journal.binding)
        authority = forged["d3"]["authority"]
        authority["scientific"][CAMP]["source_terms"]["ds"]["Aggregate"] = "Sum"
        authority["metadata_bindings"][CAMP] = digest(authority["scientific"][CAMP])
        forged["metadata_bindings"] = copy.deepcopy(authority["metadata_bindings"])
        with self.assertRaises(Hold):
            validate_binding(forged)

    def test_exact_two_interval_task_closure_required(self):
        journal, clock, root = self.new_journal()
        binding, tasks = copy.deepcopy(journal.binding), copy.deepcopy(journal.tasks)
        tasks.pop(next(iter(tasks)))
        with self.assertRaises(Hold):
            validate_binding(binding, tasks)
        with self.assertRaises(Hold):
            Journal(root, binding, tasks, now=clock.now, monotonic=clock.monotonic)

    def test_unselected_private_roster_identity_cannot_change_or_disappear(self):
        journal, _, _ = self.new_journal()
        unselected = next(sid for sid in journal.binding["roster"] if sid not in SELECTED)
        for remove in (False, True):
            binding = copy.deepcopy(journal.binding)
            if remove:
                del binding["roster"][unselected]
            else:
                binding["roster"][unselected]["orientation"] = "unapproved"
            with self.subTest(remove=remove), self.assertRaises(Hold):
                validate_binding(binding)

    def test_complete_seven_fake_calls_use_one_journal_and_sanitized_receipts(self):
        bodies = metadata_bodies() + [page([dict(t=START, v=0)]), page()]
        journal, _, executor, result = self.run_fake(bodies)
        self.assertEqual(len(executor.calls), 7)
        snapshot = journal.snapshot()
        self.assertEqual(snapshot["counters"]["attempts"], 7)
        self.assertEqual(snapshot["counters"]["logical_requests"], 7)
        self.assertEqual(snapshot["counters"]["response_bytes"], sum(map(len, bodies)))
        self.assertEqual(snapshot["counters"]["source_rows"], 3)
        attempts = list(snapshot["attempts"].values())
        for original, receipt in zip(bodies[:5], attempts[:5]):
            self.assertEqual(receipt["response_sha256"], sha(original))
            self.assertEqual(receipt["response_bytes"], len(original))
            self.assertEqual(receipt["representation"], "sanitized")
            self.assertNotEqual(receipt["objects"][0]["sha256"], sha(original))
            self.assertEqual(receipt["details"]["privacy"], "public")
            self.assertEqual(receipt["details"]["identity"], "match")
        for receipt in attempts[5:]:
            self.assertEqual(receipt["representation"], "original")
            self.assertEqual(receipt["response_sha256"], receipt["objects"][0]["sha256"])
        self.assertEqual(result[CAMP]["envelope"]["rows"][0]["v"], 0)
        self.assertEqual(result[DEEP]["envelope"]["rows"], [])
        self.assertEqual({s["state"] for s in snapshot["intervals"].values()},
                         {"complete_empty", "complete_nonempty"})

    def test_private_hidden_and_missing_metadata_never_dispatch_observations(self):
        for private in (dict(public_level=0), dict(is_hidden=True), dict(is_deleted=True)):
            bodies = metadata_bodies()
            value = station(CAMP) | private
            bodies[1] = encode(value)
            with self.subTest(private=private):
                journal, executor = self.assert_run_holds(bodies)
                self.assertTrue(all("/datapoints?" not in url for url, _, _ in executor.calls))
                self.assertEqual(len(journal.binding["roster"]), 434)
                receipt = list(journal.snapshot()["attempts"].values())[-1]
                self.assertEqual(receipt["representation"], "sanitized")
                self.assertEqual(decode(journal.read_object(receipt["objects"][0]))["version"],
                                 "dendra-metadata-diagnostic-1")
        journal, executor = self.assert_run_holds([encode(vocabulary()), Reply(b"synthetic missing", 404)])
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(executor.waits, [])
        self.assertEqual(list(journal.snapshot()["attempts"].values())[-1]["status"], 404)

    def test_selected_hidden_stream_prevents_both_observation_calls(self):
        bodies = metadata_bodies()
        value = datastreams(DEEP)
        value["data"][0]["is_hidden"] = True
        bodies[4] = encode(value)
        _, executor = self.assert_run_holds(bodies)
        self.assertEqual(len(executor.calls), 5)
        self.assertTrue(all("/datapoints?" not in url for url, _, _ in executor.calls))

    def test_protected_station_geometry_not_retained_in_metadata_projection(self):
        bodies = metadata_bodies()
        value = station(CAMP)
        value["is_geo_protected"] = True
        value["geo"]["coordinates"] = [-117.987654321, 34.987654321]
        bodies[1] = encode(value)
        journal, _, _, _ = self.run_fake(bodies + [page(), page()])
        receipt = list(journal.snapshot()["attempts"].values())[1]
        projection = journal.read_object(receipt["objects"][0])
        self.assertNotIn(b"117.987654321", projection)
        self.assertIsNone(decode(projection)["geometry"])
        self.assertEqual(receipt["response_sha256"], sha(bodies[1]))
        self.assertIsNone(journal.snapshot()["metadata"][CAMP]["geometry"])

    def test_retry_after_seconds_http_date_and_budget(self):
        now = Clock().now()
        self.assertEqual(retry_delay(None, ordinal=1, now=now, remaining=300), 1)
        for delay in ("0", "1", "15"):
            self.assertEqual(retry_delay(delay, ordinal=1, now=now, remaining=300), float(delay))
        later = parse_utc(now) + timedelta(seconds=15)
        self.assertEqual(retry_delay(format_datetime(later, usegmt=True), ordinal=1,
                                     now=now, remaining=300), 15)
        for value in ("16", "-1", "1.5", "NaN", "inf", "bad date", "", "9" * 129):
            with self.subTest(value=value), self.assertRaises(Hold):
                retry_delay(value, ordinal=1, now=now, remaining=300)
        with self.assertRaises(Hold):
            retry_delay("15", ordinal=1, now=now, remaining=15)

    def test_retryable_statuses_use_same_logical_key_and_at_most_two_attempts(self):
        for status in RETRYABLE:
            with self.subTest(status=status):
                bodies = metadata_bodies() + [page(), page()]
                journal, _, executor, _ = self.run_fake([Reply(b"synthetic status", status,
                    {"Retry-After": "1"})] + bodies)
                attempts = list(journal.snapshot()["attempts"].values())
                self.assertEqual(len(executor.calls), 8)
                self.assertEqual(executor.waits, [1])
                self.assertEqual(attempts[0]["logical_key"], attempts[1]["logical_key"])
                self.assertEqual([a["ordinal"] for a in attempts[:2]], [1, 2])
                self.assertTrue(attempts[0]["details"]["retryable"])
        journal, executor = self.assert_run_holds([Reply(b"a", 408), Reply(b"b", 408),
                                                 encode(vocabulary())])
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(journal.snapshot()["counters"]["attempts"], 2)

    def test_transport_retry_and_terminal_4xx_redirect_no_retry(self):
        journal, _, executor, _ = self.run_fake([urllib.error.URLError("synthetic transport")]
                                               + metadata_bodies() + [page(), page()])
        self.assertEqual(len(executor.calls), 8)
        self.assertEqual(executor.waits, [1])
        self.assertEqual(list(journal.snapshot()["attempts"].values())[0]["details"]["error_code"], "transport")
        for status in (400, 401, 403, 404, 410, 301, 302, 307, 308):
            with self.subTest(status=status):
                _, executor = self.assert_run_holds([Reply(b"synthetic error", status)])
                self.assertEqual(len(executor.calls), 1)
                self.assertEqual(executor.waits, [])
        request = urllib.request.Request(BASE + "vocabularies/dt-unit")
        with self.assertRaises(urllib.error.HTTPError):
            NoRedirect().redirect_request(request, io.BytesIO(), 302, "synthetic", {},
                                          "https://example.invalid/redirect")

    def test_retry_after_invalid_or_over_budget_records_hold_without_wait(self):
        for value in ("16", "bad", "NaN"):
            with self.subTest(value=value):
                journal, executor = self.assert_run_holds([Reply(b"synthetic busy", 429,
                                                                 {"Retry-After": value})])
                receipt = list(journal.snapshot()["attempts"].values())[0]
                self.assertEqual(receipt["details"]["outcome"], "hold")
                self.assertEqual(receipt["details"]["error_code"], "retry_after")
                self.assertEqual(executor.waits, [])
                self.assertEqual(len(executor.calls), 1)

    def test_reserved_crash_consumes_attempt_after_reopen(self):
        journal, clock, root = self.new_journal()
        reserved = journal.reserve("unit-vocabulary", "unit-vocabulary")
        binding, tasks = copy.deepcopy(journal.binding), copy.deepcopy(journal.tasks)
        journal.close()
        self.open_journals.remove(journal)
        reopened = Journal(root, binding, tasks, now=clock.now, monotonic=clock.monotonic)
        self.open_journals.append(reopened)
        self.assertEqual(reopened.snapshot()["attempts"][reserved]["state"], "reserved")
        self.assertEqual(reopened.snapshot()["counters"]["attempts"], 1)
        second = reopened.reserve("unit-vocabulary", "unit-vocabulary")
        self.assertEqual(reopened.snapshot()["attempts"][second]["ordinal"], 2)
        with self.assertRaisesRegex(Hold, "Two-attempt"):
            reopened.reserve("unit-vocabulary", "unit-vocabulary")

    def test_global_fourteen_attempt_budget_wins_without_reset(self):
        journal, _, _ = self.new_journal()
        key = next(iter(journal.tasks))
        run = journal.start_run(key)
        for cursor_n in range(7):
            cursor = format_utc(parse_utc(START) + timedelta(seconds=cursor_n))
            for _ in range(2):
                journal.reserve(key, cursor, interval_key=key, run=run)
        self.assertEqual(journal.snapshot()["counters"]["attempts"], 14)
        with self.assertRaisesRegex(Hold, "Budget exhausted: attempts"):
            journal.reserve(key, format_utc(parse_utc(START) + timedelta(seconds=8)),
                            interval_key=key, run=run)

    def test_body_over_eight_mib_charged_but_never_persisted(self):
        oversized = encode(vocabulary()) + b" " * BODY_LIMIT
        journal, executor = self.assert_run_holds([oversized])
        receipt = list(journal.snapshot()["attempts"].values())[0]
        self.assertEqual(receipt["response_bytes"], BODY_LIMIT + 1)
        self.assertFalse(receipt["body_retained"])
        self.assertEqual(receipt["objects"], [])
        self.assertEqual(len(executor.calls), 1)

    def test_cumulative_sixteen_mib_limit_includes_metadata(self):
        bodies = metadata_bodies()
        for index in (0, 1):
            # Leave ten bytes so the next response reaches the cumulative
            # sentinel rather than being denied before fake dispatch.
            target = BODY_LIMIT if index == 0 else BODY_LIMIT - 10
            bodies[index] += b" " * (target - len(bodies[index]))
        journal, executor = self.assert_run_holds(bodies)
        self.assertEqual(len(executor.calls), 3)
        self.assertGreater(journal.snapshot()["counters"]["response_bytes"], 16 * 1024**2)
        receipt = list(journal.snapshot()["attempts"].values())[-1]
        self.assertFalse(receipt["body_retained"])

    def test_twenty_thousand_row_budget_counts_metadata_and_received_rows(self):
        journal, _, _ = self.new_journal()
        first = journal.reserve("unit-vocabulary", "unit-vocabulary")
        journal.started(first)
        journal.received(first, b"synthetic counted rows", source_rows=20000, retain=False)
        second = journal.reserve("metadata-" + CAMP, "station")
        journal.started(second)
        with self.assertRaisesRegex(Hold, "Budget exhausted: source_rows"):
            journal.received(second, b"synthetic additional row", source_rows=1, retain=False)
        self.assertEqual(journal.snapshot()["counters"]["source_rows"], 20001)

    def test_per_request_and_total_wall_deadlines_hold_without_sleep(self):
        journal, executor = self.assert_run_holds([Reply(encode(vocabulary()), delay=26)])
        self.assertEqual(len(executor.calls), 1)
        self.assertFalse(list(journal.snapshot()["attempts"].values())[0]["body_retained"])
        journal, clock, _ = self.new_journal()
        journal.session()
        clock.advance(301)
        with self.assertRaises(Hold):
            Adapter(journal).remaining()
        self.assertEqual(journal.snapshot()["counters"]["attempts"], 0)

    def test_explicit_resource_window_bounds_active_response(self):
        journal, clock, _ = self.new_journal()
        executor = FiniteExecutor(journal, clock, [Reply(encode(vocabulary()), delay=2)])
        end = format_utc(parse_utc(clock.now()) + timedelta(seconds=1))
        with self.assertRaises(Hold):
            Adapter(journal).run(executor=executor, wait=executor.wait, window_end=end)
        self.assertEqual(len(executor.calls), 1)

    def test_full_page_repeated_boundary_then_short_page_preserves_zero(self):
        t1 = "2024-02-29T08:10:00.000Z"
        t2 = "2024-02-29T08:20:00.000Z"
        bodies = metadata_bodies() + [page([dict(t=START, v=0), dict(t=t1, v=1)], 2),
            page([dict(t=t1, v=1), dict(t=t2, v=2)], 2), page([dict(t=t2, v=2)], 2), page()]
        journal, _, executor, result = self.run_fake(bodies)
        rows = result[CAMP]["envelope"]["rows"]
        self.assertEqual([r["v"] for r in rows], [0, 1, 2])
        self.assertEqual(result[CAMP]["envelope"]["diagnostics"]["duplicate_rows"], 2)
        self.assertEqual(result[CAMP]["envelope"]["page_count"], 3)
        self.assertEqual(len(executor.calls), 9)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(executor.calls[6][0]).query)
        self.assertEqual(query["time[$gte]"], [t1])
        self.assertEqual(journal.snapshot()["counters"]["logical_requests"], 9)

    def test_full_nonadvancing_page_cannot_seal(self):
        rows = [dict(t=START, v=0), dict(t=START, v=0)]
        journal, executor = self.assert_run_holds(metadata_bodies() + [page(rows, 2), page(rows, 2)])
        self.assertEqual(len(executor.calls), 7)
        self.assertTrue(all(state["complete"] is None for state in journal.snapshot()["intervals"].values()))

    def test_malformed_outside_precision_order_and_effective_limits_hold(self):
        invalid = [page([dict(t="not-time", v=1)]), page([dict(t=END, v=1)]),
                   page([dict(t="2024-02-29T07:59:59Z", v=1)]),
                   page([dict(t="2024-02-29T08:00:00.0000001Z", v=1)]),
                   page([dict(t="2024-02-29T08:10:00Z", v=1), dict(t=START, v=2)]),
                   encode(dict(data=[])), page(limit=2017), page(limit=0), page(limit=True),
                   page([dict(t=START, v=1)] * 3, limit=2)]
        for index, body in enumerate(invalid):
            with self.subTest(case=index):
                journal, executor = self.assert_run_holds(metadata_bodies() + [body])
                self.assertEqual(len(executor.calls), 6)
                self.assertTrue(all(s["complete"] is None for s in journal.snapshot()["intervals"].values()))

    def test_restricted_observation_body_hash_count_retained_without_raw_body(self):
        body = page([dict(t=START, v=1, geometry=dict(type="Point", coordinates=[0, 0]))])
        journal, _ = self.assert_run_holds(metadata_bodies() + [body])
        receipt = list(journal.snapshot()["attempts"].values())[-1]
        self.assertEqual(receipt["response_sha256"], sha(body))
        self.assertEqual(receipt["response_bytes"], len(body))
        self.assertEqual(receipt["source_rows"], 1)
        self.assertEqual(receipt["objects"], [])

    def test_restricted_total_or_skip_never_persists_observation_body(self):
        for field in ("total", "skip"):
            with self.subTest(field=field):
                body = encode(dict(data=[], limit=2016,
                                   **{field: dict(geometry=[0, 0], is_hidden=True)}))
                journal, _ = self.assert_run_holds(metadata_bodies() + [body])
                receipt = list(journal.snapshot()["attempts"].values())[-1]
                self.assertEqual(receipt["response_sha256"], sha(body))
                self.assertFalse(receipt["body_retained"])
                self.assertEqual(receipt["objects"], [])

    def test_receipt_storage_failure_is_not_retried_as_transport(self):
        journal, clock, _ = self.new_journal()
        executor = FiniteExecutor(journal, clock, metadata_bodies() + [page(), page()])
        with patch.object(journal, "received", side_effect=OSError("synthetic storage failure")):
            with self.assertRaises(Hold):
                Adapter(journal).run(executor=executor, wait=executor.wait)
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(executor.waits, [])
        self.assertEqual(journal.snapshot()["counters"]["attempts"], 1)
        self.assertEqual(list(journal.snapshot()["attempts"].values())[0]["state"], "started")

    def test_success_survives_later_failed_explicit_same_wave_recheck(self):
        journal, clock, _, result = self.run_fake(metadata_bodies() + [page([dict(t=START, v=0)]), page()])
        key = next(k for k, v in journal.tasks.items() if v["identity"]["stream_id"] == CAMP)
        original = journal.completed(key)
        adapter = Adapter(journal)
        executor = FiniteExecutor(journal, clock, [Reply(b"synthetic unavailable", 404)])
        # Exercise recheck within the already budgeted synthetic session; this
        # does not add a second session or replace fresh metadata with a cache.
        adapter.active, adapter.executor, adapter.wait = True, executor, executor.wait
        adapter.permissions = {sid: view["checked_at"] for sid, view in journal.snapshot()["metadata"].items()}
        try:
            with self.assertRaises(FetchError):
                adapter.observations(CAMP, recheck=True)
        finally:
            adapter.active, adapter.executor, adapter.wait = False, None, None
            adapter.permissions = {}
        self.assertEqual(journal.completed(key), original)
        self.assertEqual(original, result[CAMP]["envelope"])
        self.assertEqual(journal.snapshot()["intervals"][key]["state"], "held")

    def test_construct_plan_reopen_recovery_and_default_paths_do_not_dispatch(self):
        journal, clock, root = self.new_journal()
        adapter = Adapter(journal)
        self.assertEqual(len(adapter.plan()), 7)
        self.assertEqual(journal.snapshot()["counters"]["attempts"], 0)
        with self.assertRaises(TypeError):
            adapter.run()
        with self.assertRaises(Hold):
            run_authorized_probe(journal, authorization={})
        with self.assertRaises(Hold):
            adapter.observations(CAMP)
        binding, tasks = copy.deepcopy(journal.binding), copy.deepcopy(journal.tasks)
        journal.close()
        self.open_journals.remove(journal)
        reopened = Journal(root, binding, tasks, recovery=True, now=clock.now, monotonic=clock.monotonic)
        self.open_journals.append(reopened)
        self.assertEqual(reopened.snapshot()["counters"]["attempts"], 0)
        self.assertEqual(len(Adapter(reopened).plan()), 7)
        self.assertEqual(NETWORK_ATTEMPTS, [])
        self.assertEqual(SLEEP_ATTEMPTS, [])

    def test_live_entry_rejects_injected_clock_before_constructing_opener(self):
        journal, clock, root = self.new_journal()
        authorization = dict(approval_reference="synthetic offline rejection check",
            binding_sha256=digest(journal.binding), task_root=str(root),
            window_start=clock.now(),
            window_end=format_utc(parse_utc(clock.now()) + timedelta(seconds=300)))
        with patch("urllib.request.build_opener", side_effect=AssertionError("Live opener constructed")) as opener:
            with self.assertRaisesRegex(Hold, "real journal clocks"):
                run_authorized_probe(journal, authorization=authorization)
            opener.assert_not_called()
        self.assertEqual(journal.snapshot()["counters"]["attempts"], 0)


if __name__ == "__main__":
    unittest.main()
