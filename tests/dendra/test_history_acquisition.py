"""Focused D1+D2 tests. Requires an explicit accepted inventory and local test root.

No provider calls, real history replay, R execution or browser products.
All synthetic roots (including damaged evidence) are preserved for inspection.
"""
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from dendra.history_acquisition.safety import Hold, Root, encode, decode, digest, sha
from dendra.history_acquisition.model import (
    Inventory, INVENTORY_SHA256, identities, campaign, plan, index_pages,
    unit_route, metadata_view, boundary, PAGE_BYTES,
)
from dendra.history_acquisition.journal import Journal
from dendra.history_acquisition.offline import collect, ReplayTransport, OfflineOnly
from dendra.transport import FetchError, PaginationError

KNOWN = "63531a67a9b61453fa1ca4ed"
UNKNOWN_DEPTH = "5d8e42e72da5c3cc53f6531d"
UNRESOLVED = "5d9272a12da5c3cff0f655ed"
START = "2024-02-29T08:00:00Z"
END = "2024-03-01T08:00:00Z"
NETWORK_ATTEMPTS = []


def no_network(event, args):
    if event.startswith("socket."):
        NETWORK_ATTEMPTS.append(event)
        raise AssertionError("Network is forbidden in D1+D2 tests")


def setUpModule():
    global INV, DOCUMENT, TEST_ROOT
    sys.addaudithook(no_network)
    INV = Inventory.load(os.environ["DENDRA_INVENTORY"], INVENTORY_SHA256)
    DOCUMENT = decode(INV.source)
    TEST_ROOT = Path(os.environ["DENDRA_TEST_ROOT"])
    if not TEST_ROOT.is_absolute() or not TEST_ROOT.is_dir():
        raise ValueError("Explicit existing task-owned test root required")


def tearDownModule():
    if NETWORK_ATTEMPTS:
        raise AssertionError("Unexpected socket attempt: " + repr(NETWORK_ATTEMPTS))


class Clock:
    def __init__(self):
        self.seconds = 0

    def now(self):
        return (datetime(2026, 9, 25, 18, 45, tzinfo=timezone.utc) +
                timedelta(seconds=self.seconds)).isoformat().replace("+00:00", "Z")

    def monotonic(self):
        return self.seconds


def body(rows, limit=2016):
    return encode(dict(data=rows, limit=limit))


def row(t="2024-02-29T09:00:00Z", v=0, **extra):
    return dict(t=t, v=v, **extra)


def science(sid):
    return dict(parameter="soil_moisture", terms={"Unit": INV.identity(sid)["native_unit"],
                "Medium": "Soil"}, cadence_seconds=600, time_semantics="canonical_t_unshifted")


def claims(sid=KNOWN, **updates):
    value = {**INV.identity(sid), "complete": True, "access_state": "accessible",
             "public_level": 3, "is_hidden": False, "station_public_level": 3,
             "station_is_hidden": False, "geo_protected": False,
             "display_name": "Synthetic public station", "geometry": [-116, 34],
             "activity": "active", "scientific_sha256": digest(science(sid)),
             "scientific_fields": science(sid),
             "dictionary_sha256": "b" * 64}
    value.update(updates)
    return value


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + "-", dir=TEST_ROOT))
        self.clock = Clock()
        self.binding = self.make_binding()
        self.tasks = plan(self.binding, [(KNOWN, START, END)])
        self.key = next(iter(self.tasks))

    def make_binding(self, selected=(KNOWN,), **budget_updates):
        budgets = dict(logical_requests=80, attempts=80, response_bytes=64*1024**2,
                       source_rows=1000000, intervals=16, elapsed_ms=300000, sessions=16)
        budgets.update(budget_updates)
        return campaign(INV, campaign_id="synthetic", selected_ids=list(selected),
                        as_of=self.clock.now(), horizons={s: {"start": "2022-10-01T08:00:00Z",
                        "end": "2024-10-01T08:00:00Z"} for s in selected},
                        metadata_bindings={s: digest(science(s)) for s in selected}, dictionary_sha256="b"*64,
                        budgets=budgets, request_generation="frozen-test")

    def open(self, create=True, recovery=False):
        return Journal(self.root, self.binding, self.tasks, create=create, recovery=recovery,
                       now=self.clock.now, monotonic=self.clock.monotonic)

    def ready(self):
        journal = self.open()
        for sid in self.binding["selected_ids"]:
            journal.metadata(sid, claims(sid), self.clock.now())
        return journal

    def run_bytes(self, responses, **kwargs):
        with self.ready() as journal:
            return collect(journal, self.key, transport=ReplayTransport(responses), **kwargs)


class InventoryTests(Fixture):
    def test_exact_frozen_inventory_and_closure(self):
        self.assertEqual(sha(INV.source), INVENTORY_SHA256)
        roster = INV.roster()
        self.assertEqual(len(roster), 434)
        self.assertEqual(len({x["station_id"] for x in roster.values()}), 122)
        self.assertEqual(sum(x["unit_status"] == "native_only_scale_unresolved" for x in roster.values()), 97)
        INV.check_document(DOCUMENT)

    def test_wrong_hash_and_equivalent_reencoded_bytes_fail(self):
        with self.assertRaises(Hold):
            Inventory.load(os.environ["DENDRA_INVENTORY"], "0"*64)
        p = self.root / "inventory.json"
        p.write_bytes(encode(DOCUMENT))
        with self.assertRaisesRegex(Hold, "bytes changed"):
            Inventory.load(p, INVENTORY_SHA256)

    def test_duplicate_json_key_fails(self):
        with self.assertRaisesRegex(Hold, "Duplicate JSON"):
            decode(b'{"id":1,"id":2}')

    def test_duplicate_station(self):
        data = copy.deepcopy(DOCUMENT)
        data["stations"].append(data["stations"][0])
        with self.assertRaisesRegex(Hold, "Duplicate"):
            INV.check_document(data)

    def test_duplicate_stream(self):
        data = copy.deepcopy(DOCUMENT)
        data["stations"][1]["catalog"].append(data["stations"][0]["catalog"][0])
        with self.assertRaisesRegex(Hold, "Duplicate"):
            INV.check_document(data)

    def test_missing_extra_substituted_ids(self):
        for change in ("missing", "extra", "substitution"):
            with self.subTest(change=change):
                data = copy.deepcopy(DOCUMENT)
                catalog = data["stations"][0]["catalog"]
                if change == "missing":
                    catalog.pop()
                elif change == "extra":
                    value = copy.deepcopy(catalog[0])
                    value["id"] = "f"*24
                    catalog.append(value)
                else:
                    catalog[0]["id"] = "f"*24
                with self.assertRaises(Hold):
                    INV.check_document(data)

    def test_station_association_change(self):
        data = copy.deepcopy(DOCUMENT)
        data["stations"][1]["catalog"].append(data["stations"][0]["catalog"].pop())
        with self.assertRaisesRegex(Hold, "identity changed"):
            INV.check_document(data)

    def test_depth_orientation_unit_status_changes(self):
        for key, value in [("depth", 987), ("orientation", "invented"),
                           ("unit", "Dimensionless"), ("unit_status", "native_only_scale_unresolved")]:
            with self.subTest(field=key):
                data = copy.deepcopy(DOCUMENT)
                data["stations"][0]["catalog"][0][key] = value
                with self.assertRaises(Hold):
                    INV.check_document(data)

    def test_all_unresolved_remain_nonpercent(self):
        unknown = [i for i in INV.roster().values() if i["native_unit"] == "Dimensionless"]
        self.assertEqual(len(unknown), 97)
        for identity in unknown:
            route = unit_route(identity)
            self.assertFalse(route["percent_product_eligible"])
            self.assertFalse(route["browser_history_available"])
            self.assertIsNone(route["multiplier"])
            self.assertIsNone(route["offset"])

    def test_existing_unit_routes_and_unknown_depth(self):
        self.assertEqual(unit_route(INV.identity(KNOWN))["multiplier"], 1)
        self.assertEqual(unit_route(INV.identity(UNKNOWN_DEPTH))["multiplier"], 100)
        self.assertIsNone(INV.identity(UNKNOWN_DEPTH)["depth_cm"])
        self.assertEqual(INV.identity(UNKNOWN_DEPTH)["orientation"], "Vertical")
        self.assertIsNone(INV.identity(UNRESOLVED)["depth_cm"])

    def test_fabricated_inventory_object_is_rejected(self):
        with self.assertRaises(Hold):
            Inventory(b"{}\n", INV.source)


class PlannerTests(Fixture):
    def test_leap_day_fixed_pst_and_half_open(self):
        task = self.tasks[self.key]
        self.assertEqual((boundary(task["end"]) - boundary(task["start"])).days, 1)
        result = self.run_bytes([body([row(t=START, v=0)])])
        self.assertEqual(len(result["envelope"]["rows"]), 1)
        with self.assertRaises(PaginationError):
            # A second task root isn't needed to establish parser end exclusion.
            self.run_end_boundary()

    def run_end_boundary(self):
        with self.open(create=False) as journal:
            collect(journal, self.key, recheck=True, transport=ReplayTransport([body([row(t=END)])]))

    def test_october_and_thirty_day_split(self):
        tasks = plan(self.binding, [(KNOWN, "2023-09-15T08:00:00Z", "2023-11-01T08:00:00Z")])
        parts = list(tasks.values())
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0]["end"], parts[1]["start"])
        self.assertTrue(all((boundary(t["end"]) - boundary(t["start"])).days <= 30 for t in parts))
        crossing = plan(self.binding, [(KNOWN, "2023-09-30T08:00:00Z", "2023-10-02T08:00:00Z")])
        self.assertEqual(len(crossing), 1)

    def test_no_dst_boundary_guess(self):
        for value in ("2024-03-10T07:00:00Z", "2024-03-10T08:00:00.000001Z",
                      "2024-03-10T00:00:00-08:00"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                boundary(value)

    def test_overlap_and_unapproved_stream_or_horizon(self):
        for intervals in ([(KNOWN, START, END)]*2, [("f"*24, START, END)],
                          [(KNOWN, "2010-01-01T08:00:00Z", END)]):
            with self.subTest(intervals=intervals), self.assertRaises(Hold):
                plan(self.binding, intervals)

    def test_keys_deterministic_and_binding_sensitive(self):
        self.assertEqual(self.tasks, plan(self.binding, [(KNOWN, START, END)]))
        binding = copy.deepcopy(self.binding)
        binding["request_generation"] = "different-reviewed-generation"
        self.assertNotEqual(self.key, next(iter(plan(binding, [(KNOWN, START, END)]))))

    def test_index_pages_are_bounded(self):
        pages = index_pages([{"id": n} for n in range(300)])
        self.assertEqual(list(map(len, pages)), [128, 128, 44])
        with self.assertRaises(Hold):
            index_pages([{"huge": "x"*PAGE_BYTES}])
        with self.assertRaises(Hold):
            index_pages([{}]*4097)
        with self.assertRaises(Hold):
            index_pages([], page_entries=500)

    def test_explicit_budgets_and_completed_cutoff(self):
        self.assertEqual(self.binding["completed_cutoff"], "2026-09-24")
        with self.assertRaises(Hold):
            self.make_binding(attempts=True)
        with self.assertRaises(Hold):
            self.make_binding(selected=(KNOWN, KNOWN))


class MetadataTests(Fixture):
    def view(self, **overrides):
        return metadata_view(INV.identity(KNOWN), claims(**overrides), checked_at=self.clock.now(),
                             now=self.clock.now(), scientific_sha256=digest(science(KNOWN)),
                             dictionary_sha256="b"*64)

    def test_changed_terms_or_cadence_are_bound_and_diagnostic_only(self):
        for changed in ({**science(KNOWN), "cadence_seconds": 3600},
                        {**science(KNOWN), "terms": {"Unit": "unverified"}}):
            view = self.view(scientific_fields=changed)
            self.assertFalse(view["raw_eligible"])
            self.assertEqual(view["diagnostics"]["scientific_claim_sha256"], digest(changed))
            self.assertEqual(view["identity"], INV.identity(KNOWN))

    def test_diagnostic_witness_pagination_ids_and_refresh_clocks(self):
        view = self.view(witnesses={"first": {"t": START, "response_sha256": "c"*64}},
                         unexpected_ids=["f"*24], pagination={"effective_limit": 2016, "row_count": 1},
                         activity="inactive/ended", ended_at=END, metadata_updated_at=START)
        self.assertTrue(view["raw_eligible"])
        self.assertEqual(view["diagnostics"]["unexpected_id_count"], 1)
        self.assertEqual(view["diagnostics"]["witnesses"]["first"]["t"], START)
        self.assertEqual(view["ended_at"], END)
        self.assertEqual(len(INV.roster()), 434)

    def test_name_change_keeps_identity(self):
        a, b = self.view(), self.view(display_name="Changed public name")
        self.assertEqual(a["identity_sha256"], b["identity_sha256"])
        self.assertTrue(b["raw_eligible"])

    def test_private_hidden_missing_and_failed_preserve_roster(self):
        with self.open() as journal:
            for access, hidden in (("private/protected", False), ("accessible", True),
                                   ("missing/deleted", False), ("request-failed", False)):
                view = journal.metadata(KNOWN, claims(access_state=access, is_hidden=hidden),
                                        self.clock.now())
                self.assertFalse(view["raw_eligible"])
                self.assertIsNone(view["geometry"])
                self.assertEqual(len(journal.snapshot()["roster"]), 434)
                self.assertIn(KNOWN, journal.snapshot()["roster"])

    def test_protected_geometry_not_persisted(self):
        with self.open() as journal:
            view = journal.metadata(KNOWN, claims(geo_protected=True, geometry=[-117.123456, 35.123456]),
                                    self.clock.now())
            self.assertTrue(view["raw_eligible"])
            self.assertIsNone(view["geometry"])
        blobs = b"".join(p.read_bytes() for p in self.root.rglob("*.json"))
        self.assertNotIn(b"117.123456", blobs)

    def test_scientific_changes_hold(self):
        for key, value in (("depth_cm", None), ("orientation", "Horizontal"),
                           ("native_unit", "Dimensionless"), ("unit_status", "unresolved"),
                           ("scientific_sha256", "c"*64), ("dictionary_sha256", "c"*64)):
            with self.subTest(field=key):
                self.assertFalse(self.view(**{key: value})["raw_eligible"])

    def test_less_restrictive_requires_complete_fresh_evidence(self):
        with self.open() as journal:
            journal.metadata(KNOWN, claims(is_hidden=True), self.clock.now())
            self.assertFalse(journal.metadata(KNOWN, claims(complete=False), self.clock.now())["raw_eligible"])
            self.clock.seconds += 86401
            self.assertFalse(journal.metadata(KNOWN, claims(), "2026-09-25T18:45:00Z")["raw_eligible"])
            self.assertTrue(journal.metadata(KNOWN, claims(), self.clock.now())["raw_eligible"])

    def test_unresolved_can_archive_native_without_percent(self):
        self.binding = self.make_binding(selected=(UNRESOLVED,))
        self.tasks = plan(self.binding, [(UNRESOLVED, START, END)])
        self.key = next(iter(self.tasks))
        with self.ready() as journal:
            value = collect(journal, self.key, transport=ReplayTransport([body([row(v=0.13)])]))
            self.assertEqual(value["envelope"]["rows"][0]["v"], 0.13)
            self.assertFalse(journal.snapshot()["metadata"][UNRESOLVED]["unit"]["percent_product_eligible"])
            with self.assertRaises(Hold):
                journal.daily_evidence(self.key, "2024-02-29", "d"*64)

    def test_metadata_hold_performs_no_attempt(self):
        with self.open() as journal:
            fake = ReplayTransport([body([])])
            with self.assertRaises(Hold):
                collect(journal, self.key, transport=fake)
            self.assertEqual(fake.calls, 0)
            self.assertEqual(journal.snapshot()["counters"]["attempts"], 0)


class StorageBudgetTests(Fixture):
    def test_active_then_interrupted_state(self):
        with self.open() as journal:
            journal.start_run(self.key)
            self.assertEqual(journal.snapshot()["intervals"][self.key]["state"], "in_progress")
        with self.open(create=False) as journal:
            self.assertEqual(journal.snapshot()["intervals"][self.key]["state"], "incomplete")

    def test_in_memory_budget_change_cannot_authorize_reservation(self):
        with self.open() as journal:
            journal.binding["budgets"]["attempts"] += 1
            with self.assertRaisesRegex(Hold, "In-memory"):
                journal.reserve("unit-vocabulary", "metadata")
            self.assertEqual(journal.snapshot()["counters"]["attempts"], 0)

    def test_logical_request_budget_is_not_retry_budget(self):
        self.binding = self.make_binding(logical_requests=1)
        self.tasks = plan(self.binding, [(KNOWN, START, END)])
        self.key = next(iter(self.tasks))
        with self.open() as journal:
            journal.reserve("unit-vocabulary", "first-page")
            journal.reserve("unit-vocabulary", "first-page")
            with self.assertRaisesRegex(Hold, "logical_requests"):
                journal.reserve("unit-vocabulary", "second-page")

    def test_interval_and_session_limits(self):
        self.binding = self.make_binding(intervals=0, sessions=0)
        self.tasks = plan(self.binding, [(KNOWN, START, END)])
        self.key = next(iter(self.tasks))
        with self.open() as journal:
            with self.assertRaisesRegex(Hold, "intervals"):
                journal.start_run(self.key)
            with self.assertRaisesRegex(Hold, "sessions"):
                journal.session()

    def test_response_bytes_are_charged_on_over_budget_response(self):
        self.binding = self.make_binding(response_bytes=1)
        self.tasks = plan(self.binding, [(KNOWN, START, END)])
        self.key = next(iter(self.tasks))
        raw = body([row()])
        with self.ready() as journal:
            with self.assertRaisesRegex(Hold, "response_bytes"):
                collect(journal, self.key, transport=ReplayTransport([raw]))
            self.assertEqual(journal.snapshot()["counters"]["response_bytes"], len(raw))
            self.assertIsNone(journal.completed(self.key))
        with self.open(create=False) as journal:
            with self.assertRaisesRegex(Hold, "response_bytes"):
                journal.reserve("unit-vocabulary", "metadata")

    def test_source_row_budget_holds_without_refunding(self):
        self.binding = self.make_binding(source_rows=0)
        self.tasks = plan(self.binding, [(KNOWN, START, END)])
        self.key = next(iter(self.tasks))
        with self.ready() as journal:
            with self.assertRaisesRegex(Hold, "source_rows"):
                collect(journal, self.key, transport=ReplayTransport([body([row(v=0)])]))
            self.assertEqual(journal.snapshot()["counters"]["source_rows"], 1)
            self.assertIsNone(journal.completed(self.key))

    def test_traversal_absolute_and_symlink_writes_fail(self):
        with Root(self.root) as root:
            for name in ("../escaped", "/private/tmp/escaped", "x/../../escaped", "x//y"):
                with self.subTest(name=name), self.assertRaises(Hold):
                    root.write_new(name, b"x", 10)
            outside = self.root / "outside"
            outside.mkdir()
            (self.root / "link").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(OSError):
                root.write_new("link/body", b"x", 10)
            self.assertFalse((outside / "body").exists())
        with self.assertRaises(OSError):
            Root(self.root / "link")

    def test_inventory_symlink_rejected(self):
        p = self.root / "input.json"
        p.symlink_to(os.environ["DENDRA_INVENTORY"])
        with self.assertRaises(OSError):
            Inventory.load(p, INVENTORY_SHA256)

    def test_hardlink_and_overwrite_rejected(self):
        p = self.root / "a"
        p.write_bytes(b"accepted")
        os.link(p, self.root / "b")
        with Root(self.root) as root:
            with self.assertRaises(Hold):
                root.read("a", 100)
            with self.assertRaises(FileExistsError):
                root.write_new("b", b"replacement", 100)
        self.assertEqual(p.read_bytes(), b"accepted")

    def test_reservation_consumes_budget_before_execution_and_after_crash(self):
        with self.open() as journal:
            run = journal.start_run(self.key)
            key = journal.reserve(self.key, self.tasks[self.key]["start"], interval_key=self.key, run=run)
            self.assertEqual(journal.snapshot()["counters"]["attempts"], 1)
            self.assertEqual(journal.snapshot()["attempts"][key]["state"], "reserved")
        with self.open(create=False) as journal:
            self.assertEqual(journal.snapshot()["counters"]["attempts"], 1)
            self.assertEqual(journal.snapshot()["intervals"][self.key]["state"], "incomplete")

    def test_exhaustion_retry_restart_cannot_reset(self):
        self.binding = self.make_binding(attempts=1)
        self.tasks = plan(self.binding, [(KNOWN, START, END)])
        self.key = next(iter(self.tasks))
        with self.open() as journal:
            run = journal.start_run(self.key)
            key = journal.reserve(self.key, START, interval_key=self.key, run=run)
            journal.failed(key)
        with self.open(create=False) as journal:
            with self.assertRaisesRegex(Hold, "Budget exhausted"):
                journal.reserve(self.key, START, interval_key=self.key, run=run)
        with self.assertRaises(FileExistsError):
            self.open(create=True)

    def test_retry_uses_same_logical_request_and_total_attempts(self):
        with self.open() as journal:
            run = journal.start_run(self.key)
            a = journal.reserve(self.key, START, interval_key=self.key, run=run)
            journal.failed(a)
            b = journal.reserve(self.key, START, interval_key=self.key, run=run)
            self.assertNotEqual(a, b)
            counts = journal.snapshot()["counters"]
            self.assertEqual((counts["logical_requests"], counts["attempts"]), (1, 2))
            with self.assertRaises(Hold):
                journal.reserve(self.key, START, interval_key=self.key, run=run)

    def test_budget_binding_cannot_be_enlarged_on_reopen(self):
        with self.open():
            pass
        self.binding["budgets"]["attempts"] += 1
        with self.assertRaises(Hold):
            self.open(create=False)

    def test_new_state_filename_cannot_replace_fixed_journal(self):
        with self.open() as journal:
            journal.reserve("unit-vocabulary", "metadata")
        directory = self.root / "campaigns/synthetic/events"
        directory.rename(directory.with_name("preserved-events"))
        directory.mkdir()
        with self.assertRaises(Hold):
            self.open(create=False)
        with self.open(create=False, recovery=True) as journal:
            self.assertIsNotNone(journal.damage)
            with self.assertRaises(Hold):
                journal.reserve("unit-vocabulary", "metadata")

    def test_session_deadline_survives_restart_and_clock_reversal(self):
        with self.open() as journal:
            journal.session()
        self.clock.seconds = 301
        with self.open(create=False) as journal:
            with self.assertRaisesRegex(Hold, "elapsed_ms"):
                journal.session()
        self.clock.seconds = -1
        with self.open(create=False) as journal:
            with self.assertRaises(Hold):
                journal.session()

    def test_writer_lock_prevents_competing_writer(self):
        with self.open():
            with self.assertRaises(BlockingIOError):
                self.open(create=False)

    def test_receipt_corruption_fails_closed(self):
        with self.open() as journal:
            journal.reserve("unit-vocabulary", "metadata")
        p = next((self.root / "campaigns/synthetic/events").rglob("*.json"))
        original = p.read_bytes()
        p.write_bytes(original.replace(b"reserved", b"reserVed"))
        with self.assertRaises(Hold):
            self.open(create=False)
        self.assertEqual(p.read_bytes(), original.replace(b"reserved", b"reserVed"))

    def test_object_corruption_does_not_get_rewritten(self):
        with self.ready() as journal:
            collect(journal, self.key, transport=ReplayTransport([body([row()])]))
        p = next((self.root / "campaigns/synthetic/objects").glob("*.bin"))
        p.write_bytes(b"corrupt")
        with self.assertRaises(Hold):
            self.open(create=False)
        self.assertEqual(p.read_bytes(), b"corrupt")

    def test_torn_tail_readonly_recovery_keeps_prior_seal(self):
        with self.ready() as journal:
            collect(journal, self.key, transport=ReplayTransport([body([row()])]))
            before = journal.completed(self.key)
            original = journal.fs.write_new
            def torn(name, data, limit):
                if "/events/" in name:
                    original(name, data[:17], limit)
                    raise OSError("synthetic interrupted write")
                return original(name, data, limit)
            with patch.object(journal.fs, "write_new", side_effect=torn):
                with self.assertRaises(OSError):
                    journal.reserve("unit-vocabulary", "metadata")
        with self.assertRaises(Hold):
            self.open(create=False)
        with self.open(create=False, recovery=True) as recovered:
            self.assertEqual(recovered.completed(self.key), before)
            self.assertIsNotNone(recovered.damage)
            with self.assertRaises(Hold):
                recovered.session()
            self.assertEqual(NETWORK_ATTEMPTS, [])

    def test_completed_object_is_never_overwritten(self):
        with self.open() as journal:
            descriptor = journal.put_object(b"immutable")
            self.assertEqual(journal.put_object(b"immutable"), descriptor)
            self.assertEqual(journal.read_object(descriptor), b"immutable")
            with self.assertRaises(Hold):
                journal.read_object({**descriptor, "path": "../escape"})


class ResponseResumeTests(Fixture):
    def test_private_refresh_holds_cached_reuse_without_losing_archive(self):
        with self.ready() as journal:
            collect(journal, self.key, transport=ReplayTransport([body([row()])]))
            journal.metadata(KNOWN, claims(is_hidden=True), self.clock.now())
            fake = ReplayTransport([])
            with self.assertRaisesRegex(Hold, "public identity"):
                collect(journal, self.key, transport=fake)
            self.assertEqual(fake.calls, 0)
            self.assertIsNotNone(journal.completed(self.key))
    def test_real_clock_offline_replay_orders_receipt_times(self):
        with Journal(self.root, self.binding, self.tasks, create=True) as journal:
            journal.metadata(KNOWN, claims(), journal.now())
            result = collect(journal, self.key, transport=ReplayTransport([body([row()])]))
            self.assertTrue(result["envelope"]["query_complete"])

    def test_rehashed_invented_rows_cannot_seal(self):
        from dendra.transport import _content_hash
        with self.ready() as journal:
            result = collect(journal, self.key, transport=ReplayTransport([body([row(v=0)])]))
            seal = journal.snapshot()["intervals"][self.key]["complete"]
            invented = copy.deepcopy(result["envelope"])
            invented["rows"][0]["v"] = 99
            invented["content_sha256"] = _content_hash(invented)
            with self.assertRaisesRegex(Hold, "Normalized envelope"):
                journal.seal(self.key, seal["run"], invented, seal["attempt_keys"])
            self.assertEqual(journal.completed(self.key)["rows"][0]["v"], 0)

    def test_incompatible_run_pages_cannot_seal(self):
        with self.ready() as journal:
            result = collect(journal, self.key, transport=ReplayTransport([body([row()])]))
            seal = journal.snapshot()["intervals"][self.key]["complete"]
            next_run = journal.start_run(self.key, recheck=True)
            with self.assertRaisesRegex(Hold, "receipt closure"):
                journal.seal(self.key, next_run, result["envelope"], seal["attempt_keys"])
            self.assertIsNotNone(journal.completed(self.key))

    def test_unqueried_empty_zero_are_distinct(self):
        with self.ready() as journal:
            self.assertEqual(journal.snapshot()["intervals"][self.key]["state"], "unqueried")
            collect(journal, self.key, transport=ReplayTransport([body([])]))
            self.assertEqual(journal.snapshot()["intervals"][self.key]["state"], "complete_empty")
            result = collect(journal, self.key, recheck=True, transport=ReplayTransport([body([row(v=0)])]))
            self.assertEqual(result["envelope"]["rows"][0]["v"], 0)
            self.assertEqual(journal.snapshot()["intervals"][self.key]["state"], "complete_nonempty")

    def test_empty_completed_resume_never_reacquires(self):
        with self.ready() as journal:
            collect(journal, self.key, transport=ReplayTransport([body([])]))
        with self.open(create=False) as journal:
            fake = ReplayTransport([])
            self.assertTrue(collect(journal, self.key, transport=fake)["cache_hit"])
            self.assertEqual(fake.calls, 0)
            self.assertEqual(journal.snapshot()["counters"]["attempts"], 1)

    def test_short_page_seals(self):
        result = self.run_bytes([body([row()], 2)])
        self.assertTrue(result["envelope"]["query_complete"])

    def test_full_page_then_overlap_duplicate_conflict(self):
        a, b = row(), row("2024-02-29T10:00:00Z", 1)
        result = self.run_bytes([body([a, b], 2), body([dict(b, v=2)], 2)])
        rows = result["envelope"]["rows"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[-1]["duplicate_conflict"])
        self.assertEqual(len(rows[-1]["conflicting_values"]), 2)
        self.assertEqual(result["envelope"]["diagnostics"]["duplicate_rows"], 1)

    def test_equal_duplicate_preserves_zero(self):
        result = self.run_bytes([body([row(), row()], 3)])
        self.assertEqual(result["envelope"]["rows"][0]["v"], 0)
        self.assertEqual(len(result["envelope"]["rows"]), 1)
        self.assertFalse(result["envelope"]["rows"][0].get("duplicate_conflict", False))

    def test_nonadvancing_cursor_holds(self):
        same = [row(), row()]
        with self.ready() as journal:
            with self.assertRaises(PaginationError):
                collect(journal, self.key, transport=ReplayTransport([body(same, 2), body(same, 2)]))
            self.assertIsNone(journal.completed(self.key))

    def test_malformed_out_of_bounds_precision_and_unsorted_hold(self):
        cases = [[row(t="not-a-time")], [row(t=END)], [row(t="2024-02-29T07:59:59Z")],
                 [row(t="2024-02-29T09:00:00.000000001Z")],
                 [row(t="2024-02-29T10:00:00Z"), row(t="2024-02-29T09:00:00Z")]]
        with self.ready() as journal:
            for rows in cases:
                with self.subTest(rows=rows), self.assertRaises(PaginationError):
                    collect(journal, self.key, transport=ReplayTransport([body(rows)]))
                self.assertIsNone(journal.completed(self.key))

    def test_effective_limit_missing_or_invalid_holds(self):
        with self.ready() as journal:
            for payload in ({"data": []}, {"data": [], "limit": True}, {"data": [], "limit": 0},
                            {"data": [], "limit": 2017}, {"data": [row(), row()], "limit": 1}):
                with self.subTest(payload=payload), self.assertRaises(PaginationError):
                    collect(journal, self.key, transport=ReplayTransport([encode(payload)]))
                self.assertIsNone(journal.completed(self.key))

    def test_partial_resume_starts_from_interval_start(self):
        with self.ready() as journal:
            with self.assertRaises(Hold):
                collect(journal, self.key, transport=ReplayTransport([body([row(), row("2024-02-29T10:00:00Z")], 2)]))
            self.assertIsNone(journal.completed(self.key))
        with self.open(create=False) as journal:
            collect(journal, self.key, transport=ReplayTransport([body([row()])]))
            reservations = [e["data"] for e in journal.events if e["kind"] == "reserved"]
            self.assertEqual(reservations[-1]["cursor"], journal.tasks[self.key]["start"])
            self.assertEqual(reservations[-1]["run"], 2)
            self.assertEqual(len(journal.completed(self.key)["rows"]), 1)

    def test_later_failure_preserves_prior_check_and_data(self):
        with self.ready() as journal:
            collect(journal, self.key, transport=ReplayTransport([body([row()])]))
            prior = copy.deepcopy(journal.snapshot()["intervals"][self.key])
            self.clock.seconds += 1
            with self.assertRaises(FetchError):
                collect(journal, self.key, recheck=True, transport=ReplayTransport([503, 503]))
            current = journal.snapshot()["intervals"][self.key]
            self.assertEqual(current["complete"], prior["complete"])
            self.assertEqual(current["last_successful_source_check"], prior["last_successful_source_check"])
            self.assertNotEqual(current["latest_attempt"], prior["latest_attempt"])
            self.assertIsNotNone(journal.completed(self.key))

    def test_fake_retry_counts_and_persistent_circuit(self):
        with self.ready() as journal:
            collect(journal, self.key, transport=ReplayTransport([503, body([row()])]))
            counters = journal.snapshot()["counters"]
            self.assertEqual((counters["logical_requests"], counters["attempts"]), (1, 2))

    def test_circuit_cannot_be_reset_by_reopen(self):
        with self.ready() as journal:
            with self.assertRaises(FetchError):
                collect(journal, self.key, transport=ReplayTransport([503, 503]))
        with self.open(create=False) as journal:
            fake = ReplayTransport([body([])])
            with self.assertRaisesRegex(Hold, "circuit"):
                collect(journal, self.key, transport=fake)
            self.assertEqual(fake.calls, 0)

    def test_raw_privacy_screen_omits_body_but_counts_receipt(self):
        raw = body([row(geometry=[-123.98765, 42])])
        with self.ready() as journal:
            with self.assertRaises(Hold):
                collect(journal, self.key, transport=ReplayTransport([raw]))
            self.assertEqual(journal.snapshot()["counters"]["response_bytes"], len(raw))
        self.assertFalse((self.root / "campaigns/synthetic/objects").exists())
        self.assertNotIn(b"123.98765", b"".join(p.read_bytes() for p in self.root.rglob("*.json")))

    def test_malformed_bytes_accounted_without_raw_persistence(self):
        with self.ready() as journal:
            with self.assertRaises(Hold):
                collect(journal, self.key, transport=ReplayTransport([b'{"data":']))
            self.assertEqual(journal.snapshot()["counters"]["response_bytes"], 8)
            self.assertEqual(journal.snapshot()["counters"]["unknown_row_responses"], 1)
            with self.assertRaisesRegex(Hold, "Unknown source row count"):
                journal.reserve("unit-vocabulary", "metadata")

    def test_no_network_default_or_custom_transport(self):
        with self.ready() as journal:
            for transport in (None, lambda: socket.socket(), object()):
                with self.assertRaises(OfflineOnly):
                    collect(journal, self.key, transport=transport)
            self.assertEqual(journal.snapshot()["counters"]["attempts"], 0)
        self.assertEqual(NETWORK_ATTEMPTS, [])

    def test_unrelated_interval_does_not_restart(self):
        self.binding = self.make_binding(selected=(KNOWN, UNKNOWN_DEPTH))
        self.tasks = plan(self.binding, [(KNOWN, START, END), (UNKNOWN_DEPTH, START, END)])
        keys = list(self.tasks)
        with self.ready() as journal:
            collect(journal, keys[0], transport=ReplayTransport([body([])]))
            with self.assertRaises(Hold):
                collect(journal, keys[1], transport=ReplayTransport([]))
        with self.open(create=False) as journal:
            fake = ReplayTransport([])
            self.assertTrue(collect(journal, keys[0], transport=fake)["cache_hit"])
            self.assertIsNone(journal.completed(keys[1]))
            self.assertEqual(fake.calls, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
