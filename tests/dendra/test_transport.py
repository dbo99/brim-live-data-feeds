"""Offline tests of request correctness and durable interval replacement."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "dendra"))
from transport import (ChunkStore, DendraFetcher, FetchError, PaginationError,
                          CacheError, format_utc, monthly_windows, normalize_rows,
                          parse_utc)

UTC = timezone.utc
BASE = datetime(2025, 9, 1, tzinfo=UTC)


def stamp(minutes):
    return format_utc(BASE + timedelta(minutes=minutes))


def row(minutes, value):
    return {"t": stamp(minutes), "v": value}


class Response:
    def __init__(self, payload=None, status=200, headers=None, body=None):
        self.status, self.headers = status, headers or {}
        self.body = body if body is not None else json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


class DatasetOpener:
    def __init__(self, rows, cap=2016):
        self.rows, self.cap, self.calls = rows, cap, []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        begin, end = parse_utc(query["time[$gte]"][0]), parse_utc(query["time[$lt]"][0])
        limit = min(self.cap, int(query["$limit"][0]))
        data = [r for r in self.rows if begin <= parse_utc(r["t"]) < end]
        data.sort(key=lambda r: parse_utc(r["t"]))
        return Response({"data": data[:limit], "limit": limit})


class FetchTests(unittest.TestCase):
    def client(self, opener, **options):
        return DendraFetcher(opener=opener, now_fn=lambda: BASE + timedelta(days=400),
                             sleep_fn=lambda _: None, **options)

    def test_utc_precision_calendar_and_local_rejection(self):
        self.assertEqual(format_utc("2025-09-01T00:00:00.123456Z"), "2025-09-01T00:00:00.123456Z")
        self.assertEqual(format_utc("2025-09-01T00:00:00.123000000Z"), "2025-09-01T00:00:00.123Z")
        for value in ("2025-09-01T00:00:00", "2025-02-30T00:00:00Z",
                      "2025-09-01T24:00:00Z", "2025-09-01T00:00:60Z",
                      "2025-09-01T00:00:00.123456001Z", datetime(2025, 9, 1),
                      datetime(2025, 9, 1, tzinfo=timezone(timedelta(hours=-8)))):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_utc(value)

    def test_month_chunks_include_leap_february_and_clip_edges(self):
        windows = list(monthly_windows("2024-01-31T23:00:00Z", "2024-03-02T05:00:00Z"))
        self.assertEqual([window[0] for window in windows], ["2024-01", "2024-02", "2024-03"])
        self.assertEqual(windows[0][1], "2024-01-31T23:00:00.000Z")
        self.assertEqual(parse_utc(windows[1][2]) - parse_utc(windows[1][1]), timedelta(days=29))
        self.assertEqual(windows[-1][2], "2024-03-02T05:00:00.000Z")
        self.assertEqual(windows[0][2], windows[1][1])
        self.assertEqual(windows[1][2], windows[2][1])

    def test_zero_null_missing_invalid_negative_and_source_quality_are_separate(self):
        data = [row(0, 0), row(10, None), {"t": stamp(20)}, row(30, "0"),
                row(40, False), row(50, -0.031), {**row(60, 1), "q": {"code": "source-note"}}]
        rows, info = normalize_rows(data, stamp(0), stamp(70))
        self.assertEqual([r["value_status"] for r in rows],
                         ["number", "null", "missing", "invalid", "invalid", "number", "number"])
        self.assertEqual(rows[0]["v"], 0)
        self.assertIsNone(rows[1]["v"])
        self.assertNotIn("v", rows[2])
        self.assertEqual(rows[3]["v"], "0")
        self.assertEqual(rows[5]["v"], -0.031)
        self.assertEqual(rows[6]["q"], {"code": "source-note"})
        self.assertEqual(info["value_counts"]["number"], 3)

    def test_normalization_uses_utc_not_lt_sorts_filters_and_flags_conflicts(self):
        data = [row(20, 2), {**row(0, 0), "lt": "2040-01-01T00:00:00"}, row(10, 1),
                row(10, 1.0), row(10, 99), row(-1, 0), row(30, 3)]
        rows, info = normalize_rows(data, stamp(0), stamp(30))
        self.assertEqual([r["t"] for r in rows], [stamp(0), stamp(10), stamp(20)])
        self.assertEqual(info["duplicate_rows"], 2)
        self.assertEqual(info["conflicting_timestamps"], 1)
        self.assertTrue(rows[1]["duplicate_conflict"])
        self.assertEqual([v["v"] for v in rows[1]["conflicting_values"]], [1, 99])
        self.assertEqual(info["outside_interval_rows"], 2)

    def test_anonymous_get_with_effective_cap_and_overlapping_cursor(self):
        opener = DatasetOpener([row(i * 10, i) for i in range(7)], cap=3)
        result = self.client(opener, page_size=10080).fetch_interval("stream1", stamp(0), stamp(60))
        self.assertEqual([r["v"] for r in result["rows"]], [0, 1, 2, 3, 4, 5])
        self.assertEqual(result["page_count"], 3)
        self.assertEqual(result["diagnostics"]["duplicate_rows"], 2)
        self.assertTrue(result["query_complete"])
        self.assertEqual(result["pages"][0]["effective_limit"], 3)
        self.assertEqual(result["pages"][0]["attempt_count"], 1)
        expected_body = json.dumps({"data": [row(0, 0), row(10, 1), row(20, 2)], "limit": 3}).encode()
        self.assertEqual(result["pages"][0]["response_bytes"], len(expected_body))
        self.assertEqual(result["latest_observation_utc"], stamp(50))
        request, timeout = opener.calls[1]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.get_header("Authorization"))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        self.assertEqual(query["time[$gte]"], [stamp(20)])
        self.assertNotIn("$skip", query)
        self.assertNotIn("time_local", query)
        self.assertNotIn("uom_id", query)
        self.assertEqual(timeout, 30)
        self.assertEqual(len(result["content_sha256"]), 64)

    def test_empty_interval_checked_end_differs_from_latest_observation(self):
        result = self.client(DatasetOpener([])).fetch_interval("stream1", stamp(0), stamp(60))
        self.assertEqual(result["rows"], [])
        self.assertIsNone(result["latest_observation_utc"])
        self.assertEqual(result["requested_interval"]["end_exclusive"], stamp(60))
        self.assertEqual(result["page_count"], 1)
        self.assertEqual(result["diagnostics"]["completion_reason"], "empty_page")

    def test_conflicting_boundary_rows_remain_flagged(self):
        pages = iter([{"limit": 3, "data": [row(0, 1), row(10, 2), row(20, 3)]},
                      {"limit": 3, "data": [row(20, 33), row(30, 4)]}])
        result = self.client(lambda *_, **__: Response(next(pages))).fetch_interval("stream1", stamp(0), stamp(40))
        self.assertEqual(result["diagnostics"]["conflicting_timestamps"], 1)
        self.assertTrue(result["rows"][2]["duplicate_conflict"])

    def test_nonadvancing_full_page_never_claims_completion(self):
        response = {"limit": 2, "data": [row(0, 1), row(0, 2)]}
        with self.assertRaises(PaginationError):
            self.client(lambda *_, **__: Response(response)).fetch_interval("stream1", stamp(0), stamp(10))

    def test_short_page_cannot_hide_a_broken_cursor_filter(self):
        pages = iter([{"limit": 3, "data": [row(0, 1), row(10, 2), row(20, 3)]},
                      {"limit": 3, "data": [row(10, 2), row(20, 3)]}])
        with self.assertRaises(PaginationError):
            self.client(lambda *_, **__: Response(next(pages))).fetch_interval("stream1", stamp(0), stamp(40))

    def test_source_cannot_include_exclusive_end_or_exceed_advertised_cap(self):
        for payload in ({"limit": 3, "data": [row(0, 0), row(10, 1)]},
                        {"limit": 1, "data": [row(0, 0), row(5, 1)]},
                        {"limit": 5000, "data": [row(0, 0)]}):
            with self.subTest(payload=payload), self.assertRaises(PaginationError):
                self.client(lambda *_, **__: Response(payload)).fetch_interval("stream1", stamp(0), stamp(10))

    def test_malformed_limits_order_timestamps_and_payload_fail(self):
        cases = [({"data": [row(0, 1)]}, PaginationError),
                 ({"data": [row(0, 1)], "limit": True}, PaginationError),
                 ({"data": [row(10, 2), row(0, 1)], "limit": 3}, PaginationError),
                 ({"data": [{"lt": stamp(0), "v": 1}], "limit": 3}, PaginationError),
                 ({"data": "not an array", "limit": 3}, FetchError)]
        for payload, expected in cases:
            with self.subTest(payload=payload), self.assertRaises(expected):
                self.client(lambda *_, **__: Response(payload)).fetch_interval("stream1", stamp(0), stamp(20))

    def test_nonstandard_nan_json_is_explicit_source_failure(self):
        with self.assertRaises(FetchError):
            self.client(lambda *_, **__: Response(body=b'{"limit":3,"data":[{"t":"2025-09-01T00:00:00Z","v":NaN}]}')).fetch_interval("stream1", stamp(0), stamp(10))

    def test_overflowing_json_number_does_not_turn_into_an_infinite_daily_value(self):
        with self.assertRaises(FetchError):
            self.client(lambda *_, **__: Response(body=b'{"limit":3,"data":[{"t":"2025-09-01T00:00:00Z","v":1e999}]}')).fetch_interval("stream1", stamp(0), stamp(10))

    def test_page_ceiling_preserves_partial_page_evidence_without_success(self):
        opener = DatasetOpener([row(i, i) for i in range(20)], cap=2)
        with self.assertRaises(PaginationError) as raised:
            self.client(opener, max_pages=2).fetch_interval("stream1", stamp(0), stamp(20))
        self.assertEqual(len(raised.exception.details["completed_pages"]), 2)

    def test_retry_transient_503_then_success_is_bounded_and_recorded(self):
        calls, sleeps = [], []
        def opener(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 503, "busy", {"Retry-After": "2"}, None)
            return Response({"limit": 3, "data": [row(0, 1)]})
        client = DendraFetcher(opener=opener, sleep_fn=sleeps.append)
        result = client.fetch_interval("stream1", stamp(0), stamp(10))
        self.assertEqual(sleeps, [2])
        self.assertEqual(result["pages"][0]["attempt_count"], 2)
        self.assertEqual(result["pages"][0]["prior_attempts"][0]["status"], 503)

    def test_404_is_unavailable_and_not_retried_or_labeled_deleted(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            raise urllib.error.HTTPError(request.full_url, 404, "not available", {}, None)
        with self.assertRaises(FetchError) as raised:
            self.client(opener).fetch_interval("stream1", stamp(0), stamp(10))
        self.assertEqual(len(calls), 1)
        self.assertNotIn("deleted", str(raised.exception))

    def test_large_retry_after_stops_without_hammering_or_sleeping(self):
        sleeps = []
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 429, "slow down", {"Retry-After": "3600"}, None)
        with self.assertRaises(FetchError) as raised:
            DendraFetcher(opener=opener, sleep_fn=sleeps.append).fetch_interval("stream1", stamp(0), stamp(10))
        self.assertEqual(sleeps, [])
        self.assertIn("later run", str(raised.exception))
        self.assertEqual(raised.exception.details["status"], 429)
        self.assertIn("datastream_id=stream1", raised.exception.details["url"])

    def test_network_retries_stop_at_max_attempts(self):
        calls, sleeps = [], []
        def opener(request, timeout):
            calls.append(request)
            raise urllib.error.URLError("offline")
        with self.assertRaises(FetchError) as raised:
            DendraFetcher(opener=opener, sleep_fn=sleeps.append, max_attempts=3).fetch_interval("stream1", stamp(0), stamp(10))
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [1, 2])
        self.assertEqual(len(raised.exception.details["attempts"]), 3)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = ChunkStore(Path(self.temporary.name) / "state")
        self.opener = DatasetOpener([row(0, 0), row(10, 1), row(20, 2)], cap=3)
        self.client = DendraFetcher(opener=self.opener, now_fn=lambda: BASE + timedelta(days=400),
                                    sleep_fn=lambda _: None, max_attempts=1)

    def fetch(self, **options):
        return self.client.fetch_chunk(self.store, "stream1", stamp(0), stamp(30), chunk_key="2025-09", **options)

    def test_exact_chunk_resume_needs_no_network_and_validates_hash(self):
        first = self.fetch()
        calls = len(self.opener.calls)
        second = self.fetch()
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(len(self.opener.calls), calls)
        self.assertEqual(first["content_sha256"], second["content_sha256"])
        self.assertEqual(first["retrieval_last_utc"], second["retrieval_last_utc"])

    def test_checkpoint_distinguishes_query_completion_and_latest_reading(self):
        self.fetch()
        checkpoint = json.loads((self.store.root / "checkpoints/stream1/2025-09.json").read_text())
        self.assertEqual(checkpoint["checked_through_exclusive_utc"], stamp(30))
        self.assertEqual(checkpoint["latest_observation_utc"], stamp(20))
        self.assertTrue(checkpoint["query_complete"])
        self.assertEqual(checkpoint["row_count"], 3)

    def test_explicit_refresh_replaces_interval_including_source_removed_rows(self):
        first = self.fetch()
        self.opener.rows = [row(0, 99), row(20, 2)]  # Dendra excluded t=10
        second = self.fetch(refresh=True)
        self.assertEqual([(r["t"], r["v"]) for r in second["rows"]], [(stamp(0), 99), (stamp(20), 2)])
        self.assertNotEqual(first["content_sha256"], second["content_sha256"])
        self.assertEqual(len(self.store.load("stream1", "2025-09")["rows"]), 2)

    def test_successful_empty_refresh_removes_previously_stored_observations(self):
        self.fetch()
        self.opener.rows = []
        second = self.fetch(refresh=True)
        self.assertEqual(second["rows"], [])
        self.assertIsNone(second["latest_observation_utc"])
        self.assertTrue(second["query_complete"])

    def test_changed_interval_bound_requires_complete_refetch_not_cache_mislabel(self):
        self.fetch()
        self.opener.rows.append(row(30, 3))
        result = self.client.fetch_chunk(self.store, "stream1", stamp(0), stamp(40), chunk_key="2025-09")
        self.assertFalse(result["cache_hit"])
        self.assertEqual(result["requested_interval"]["end_exclusive"], stamp(40))
        self.assertEqual(len(result["rows"]), 4)

    def test_failed_refresh_preserves_good_chunk_and_checkpoint_byte_for_byte(self):
        self.fetch()
        chunk = self.store.chunk_path("stream1", "2025-09")
        checkpoint = self.store.root / "checkpoints/stream1/2025-09.json"
        original_chunk, original_checkpoint = chunk.read_bytes(), checkpoint.read_bytes()
        def offline(*_, **__):
            raise urllib.error.URLError("offline")
        self.client.opener = offline
        with self.assertRaises(FetchError):
            self.fetch(refresh=True)
        self.assertEqual(chunk.read_bytes(), original_chunk)
        self.assertEqual(checkpoint.read_bytes(), original_checkpoint)
        failure = json.loads((self.store.root / "failures/stream1/2025-09.json").read_text())
        self.assertFalse(failure["query_complete"])
        self.assertTrue(failure["previous_completed_chunk_preserved"])

    def test_partial_first_download_never_publishes_an_incomplete_chunk(self):
        calls = []
        def fails_second(*_, **__):
            calls.append(1)
            if len(calls) == 1:
                return Response({"limit": 2, "data": [row(0, 0), row(10, 1)]})
            raise urllib.error.URLError("connection lost")
        self.client.opener = fails_second
        with self.assertRaises(FetchError):
            self.fetch()
        self.assertFalse(self.store.chunk_path("stream1", "2025-09").exists())
        self.assertFalse((self.store.root / "checkpoints/stream1/2025-09.json").exists())
        failure = json.loads((self.store.root / "failures/stream1/2025-09.json").read_text())
        self.assertEqual(len(failure["details"]["completed_pages"]), 1)

    def test_missing_or_stale_sidecar_is_repaired_from_authoritative_chunk(self):
        self.fetch()
        checkpoint = self.store.root / "checkpoints/stream1/2025-09.json"
        checkpoint.unlink()
        loaded = self.store.load("stream1", "2025-09")
        self.assertEqual(json.loads(checkpoint.read_text())["content_sha256"], loaded["content_sha256"])

    def test_corrupt_cache_is_not_silently_used(self):
        self.fetch()
        chunk = self.store.chunk_path("stream1", "2025-09")
        payload = json.loads(chunk.read_text())
        payload["rows"][0]["v"] = 999
        chunk.write_text(json.dumps(payload))
        with self.assertRaises(CacheError):
            self.fetch()

    def test_interrupted_atomic_rename_preserves_previous_good_file(self):
        self.fetch()
        path = self.store.chunk_path("stream1", "2025-09")
        original = path.read_bytes()
        self.opener.rows = [row(0, 99)]
        with mock.patch("transport.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.fetch(refresh=True)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_path_components_cannot_escape_state_directory(self):
        for stream, key in (("../outside", "2025-09"), ("stream1", "../../outside"), ("stream1", "")):
            with self.subTest(stream=stream, key=key), self.assertRaises(ValueError):
                self.store.chunk_path(stream, key)

    def test_completed_months_survive_a_later_failure_and_resume_without_refetch(self):
        self.opener.rows = [{"t": "2025-09-30T23:50:00.000Z", "v": 1},
                            {"t": "2025-10-01T00:00:00.000Z", "v": 2}]
        original_opener = self.opener
        def october_offline(request, timeout):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
            if query["time[$gte]"][0] == "2025-10-01T00:00:00.000Z":
                raise urllib.error.URLError("temporary October failure")
            return original_opener(request, timeout)
        self.client.opener = october_offline
        start, end = "2025-09-30T23:00:00Z", "2025-10-01T01:00:00Z"
        with self.assertRaises(FetchError):
            list(self.client.fetch_months(self.store, "stream1", start, end))
        self.assertTrue(self.store.chunk_path("stream1", "2025-09").exists())
        self.assertFalse(self.store.chunk_path("stream1", "2025-10").exists())
        self.client.opener = original_opener
        results = list(self.client.fetch_months(self.store, "stream1", start, end))
        self.assertEqual([result["cache_hit"] for result in results], [True, False])


if __name__ == "__main__":
    unittest.main()
