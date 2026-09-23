"""Bounded anonymous Dendra v2 reads with atomic, resumable monthly chunks.

This module performs no aggregation, unit conversion, or annotation correction.
Run at most two streams concurrently in the caller. Completed chunk envelopes
are the authoritative checkpoints; their small sidecars can be rebuilt.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any, Callable, Iterator
import urllib.error
import urllib.parse
import urllib.request

UTC = timezone.utc
DEFAULT_BASE = "https://api.dendra.science/v2/"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_UTC_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?Z$")


class FetchError(RuntimeError):
    """A source query did not finish successfully and must not be published."""

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


class PaginationError(FetchError):
    pass


class CacheError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_utc(value: str | datetime) -> datetime:
    """Parse an explicit UTC time without local-zone guesses or precision loss.

    Source strings must end in Z. Aware datetimes are accepted only if their
    offset is zero. Nonzero submicrosecond digits are rejected rather than
    silently rounded; source millisecond timestamps remain exact.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("A UTC-aware datetime or explicit Z timestamp is required.")
        return value.astimezone(UTC)
    if not isinstance(value, str):
        raise ValueError("A UTC-aware datetime or explicit Z timestamp is required.")
    match = _UTC_TIMESTAMP.fullmatch(value)
    if not match:
        raise ValueError(f"Unsupported UTC timestamp: {value!r}")
    fraction = match.group(2) or ""
    if any(char != "0" for char in fraction[6:]):
        raise ValueError("Nonzero submicrosecond timestamp precision is unsupported.")
    canonical = match.group(1) + "." + (fraction + "000000")[:6] + "+00:00"
    # datetime rejects impossible dates, 24:00 and leap-second normalization.
    return datetime.fromisoformat(canonical)


def format_utc(value: str | datetime) -> str:
    value = parse_utc(value)
    precision = "milliseconds" if value.microsecond % 1000 == 0 else "microseconds"
    return value.isoformat(timespec=precision).replace("+00:00", "Z")


def monthly_windows(start: str | datetime, end: str | datetime) -> Iterator[tuple[str, str, str]]:
    """Yield (YYYY-MM, inclusive_start_utc, exclusive_end_utc) UTC month chunks.

    Partial first/last months are clipped. A caller using a different calendar
    timezone may instead supply its own converted UTC bounds to fetch_chunk.
    """
    cursor, stop = parse_utc(start), parse_utc(end)
    if cursor >= stop:
        raise ValueError("Interval start must precede end.")
    while cursor < stop:
        if cursor.month == 12:
            boundary = datetime(cursor.year + 1, 1, 1, tzinfo=UTC)
        else:
            boundary = datetime(cursor.year, cursor.month + 1, 1, tzinfo=UTC)
        bound = min(boundary, stop)
        yield cursor.strftime("%Y-%m"), format_utc(cursor), format_utc(bound)
        cursor = bound


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _content_hash(envelope: dict) -> str:
    # Retrieval dates can change without changing the observed data.
    return _sha256(_canonical_json({"datastream_id": envelope["datastream_id"],
                                   "requested_interval": envelope["requested_interval"],
                                   "rows": envelope["rows"]}))


def _value_status(row: dict) -> str:
    if "v" not in row:
        return "missing"
    value = row["v"]
    if value is None:
        return "null"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            if math.isfinite(value):
                return "number"
        except OverflowError:
            pass
    return "invalid"


def normalize_rows(rows: list, start: str | datetime, end: str | datetime) -> tuple[list[dict], dict]:
    """Normalize native rows, flag conflicts, and enforce [start, end).

    Unparseable UTC times fail the interval: they cannot safely advance a
    pagination cursor. Native values are not coerced. Source lt and q fields,
    when present, are retained but do not establish UTC or BRIM quality.
    """
    begin, stop = parse_utc(start), parse_utc(end)
    if begin >= stop:
        raise ValueError("Interval start must precede end.")
    if not isinstance(rows, list):
        raise FetchError("Dendra rows must be an array.")
    by_time: dict[datetime, dict] = {}
    duplicate_count, outside_count = 0, 0
    for source in rows:
        if not isinstance(source, dict):
            raise PaginationError("Observation row is not an object.")
        try:
            stamp = parse_utc(source.get("t"))
        except (ValueError, TypeError) as exc:
            raise PaginationError("Observation contains an invalid or unsupported UTC t.") from exc
        if not begin <= stamp < stop:
            outside_count += 1
            continue
        # Source fields are copied without reapplying annotations/corrections.
        row = dict(source)
        row["t"] = format_utc(stamp)
        row["value_status"] = _value_status(source)
        if stamp not in by_time:
            by_time[stamp] = row
            continue
        duplicate_count += 1
        previous = by_time[stamp]
        old = {"value_status": previous["value_status"]}
        new = {"value_status": row["value_status"]}
        if "v" in previous:
            old["v"] = previous["v"]
        if "v" in row:
            new["v"] = row["v"]
        same_value = (old["value_status"] == new["value_status"] and
                      (old.get("v") == new.get("v") if old["value_status"] == "number" else
                       _canonical_json(old) == _canonical_json(new)))
        if not same_value:
            previous["duplicate_conflict"] = True
            alternatives = previous.setdefault("conflicting_values", [old])
            if not any(_canonical_json(item) == _canonical_json(new) for item in alternatives):
                alternatives.append(new)
        # Changing source quality at an overlapping boundary is also retained.
        if previous.get("q") != row.get("q"):
            previous["source_quality_conflict"] = True
    normalized = [by_time[key] for key in sorted(by_time)]
    counts = Counter(row["value_status"] for row in normalized)
    diagnostics = {"raw_row_count": len(rows), "row_count": len(normalized),
                   "duplicate_rows": duplicate_count, "outside_interval_rows": outside_count,
                   "conflicting_timestamps": sum(bool(row.get("duplicate_conflict")) for row in normalized),
                   "source_quality_conflicts": sum(bool(row.get("source_quality_conflict")) for row in normalized),
                   "value_counts": {key: counts[key] for key in ("number", "null", "missing", "invalid")}}
    return normalized, diagnostics


def _safe_component(value: str, expression: re.Pattern, description: str) -> str:
    if not isinstance(value, str) or not expression.fullmatch(value):
        raise ValueError(f"Invalid {description}.")
    return value


def _atomic_json(path: Path, value: dict) -> None:
    """Replace one JSON file only after complete serialization and fsync."""
    payload = _canonical_json(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        # Directory fsync makes the rename durable on supported POSIX systems.
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ChunkStore:
    """Local persistent state; a completed chunk is its own checkpoint.

    The caller must persist this directory between runner jobs. Small checkpoint
    sidecars are projections and are repaired from the validated chunk on load.
    This class does not treat an ephemeral runner cache as durable storage.
    """

    def __init__(self, root: str | Path = "state"):
        self.root = Path(root)
        self._locks: dict[str, threading.RLock] = {}
        self._locks_lock = threading.Lock()

    def _lock(self, stream_id: str) -> threading.RLock:
        with self._locks_lock:
            return self._locks.setdefault(stream_id, threading.RLock())

    def chunk_path(self, stream_id: str, chunk_key: str) -> Path:
        stream_id = _safe_component(stream_id, _SAFE_ID, "datastream ID")
        chunk_key = _safe_component(chunk_key, _SAFE_KEY, "chunk key")
        return self.root / "chunks" / stream_id / f"{chunk_key}.json"

    def _checkpoint(self, envelope: dict, chunk_key: str) -> dict:
        return {"schema_version": "1.0", "datastream_id": envelope["datastream_id"],
                "chunk_key": chunk_key, "query_complete": True,
                "requested_interval": envelope["requested_interval"],
                "checked_through_exclusive_utc": envelope["requested_interval"]["end_exclusive"],
                "latest_observation_utc": envelope["latest_observation_utc"],
                "retrieval_last_utc": envelope["retrieval_last_utc"],
                "content_sha256": envelope["content_sha256"],
                "row_count": len(envelope["rows"]), "page_count": envelope["page_count"],
                "authoritative_chunk": str(self.chunk_path(envelope["datastream_id"], chunk_key).relative_to(self.root))}

    def _write_checkpoint(self, envelope: dict, chunk_key: str) -> None:
        path = self.root / "checkpoints" / envelope["datastream_id"] / f"{chunk_key}.json"
        desired = self._checkpoint(envelope, chunk_key)
        try:
            if json.loads(path.read_text(encoding="utf-8")) == desired:
                return
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        _atomic_json(path, desired)

    def load(self, stream_id: str, chunk_key: str) -> dict | None:
        path = self.chunk_path(stream_id, chunk_key)
        with self._lock(stream_id):
            if not path.exists():
                return None
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
                if (envelope.get("datastream_id") != stream_id or
                        envelope.get("query_complete") is not True or
                        envelope.get("content_sha256") != _content_hash(envelope)):
                    raise CacheError(f"Chunk validation failed: {path}")
            except (ValueError, KeyError, TypeError) as exc:
                raise CacheError(f"Unreadable or invalid chunk: {path}") from exc
            self._write_checkpoint(envelope, chunk_key)
            return envelope

    def save(self, envelope: dict, chunk_key: str) -> Path:
        stream_id = envelope["datastream_id"]
        path = self.chunk_path(stream_id, chunk_key)
        if envelope.get("query_complete") is not True or envelope.get("content_sha256") != _content_hash(envelope):
            raise CacheError("Only a validated, completed source interval can be saved.")
        with self._lock(stream_id):
            _atomic_json(path, envelope)
            self._write_checkpoint(envelope, chunk_key)
        return path

    def record_failure(self, stream_id: str, chunk_key: str, interval: dict, exc: Exception,
                       failed_at_utc: str) -> None:
        self.chunk_path(stream_id, chunk_key)  # validate components before writing
        path = self.root / "failures" / stream_id / f"{chunk_key}.json"
        _atomic_json(path, {"schema_version": "1.0", "datastream_id": stream_id,
                           "chunk_key": chunk_key, "requested_interval": interval,
                           "query_complete": False, "failed_at_utc": failed_at_utc,
                           "error_type": type(exc).__name__, "error": str(exc),
                           "details": getattr(exc, "details", {}),
                           "previous_completed_chunk_preserved": self.chunk_path(stream_id, chunk_key).exists()})


class DendraFetcher:
    """Synchronous public API client; the driver limits stream concurrency to 2."""

    def __init__(self, *, base_url: str = DEFAULT_BASE, timeout: float = 30,
                 max_attempts: int = 3, page_size: int = 2016, max_pages: int = 100,
                 opener: Callable | None = None, sleep_fn: Callable = time.sleep,
                 now_fn: Callable[[], datetime] = utc_now, max_retry_delay: float = 30):
        if not 2 <= page_size <= 10080 or not isinstance(page_size, int):
            raise ValueError("page_size must be an integer from 2 through 10080.")
        if not isinstance(max_attempts, int) or not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be an integer from 1 through 5.")
        if not isinstance(max_pages, int) or max_pages < 1:
            raise ValueError("max_pages must be a positive integer.")
        if timeout <= 0 or max_retry_delay < 0:
            raise ValueError("Timeout must be positive; retry delay cannot be negative.")
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout, self.max_attempts, self.page_size = timeout, max_attempts, page_size
        self.max_pages, self.max_retry_delay = max_pages, max_retry_delay
        self.opener, self.sleep_fn, self.now_fn = opener or urllib.request.urlopen, sleep_fn, now_fn

    def _retry_delay(self, attempt: int, headers: Any) -> float:
        value = headers.get("Retry-After") if headers else None
        if value:
            try:
                delay = float(value)
            except ValueError:
                try:
                    until = parsedate_to_datetime(value)
                    if until.tzinfo is None:
                        until = until.replace(tzinfo=UTC)
                    delay = (until - self.now_fn()).total_seconds()
                except (ValueError, TypeError):
                    delay = 2 ** (attempt - 1)
            delay = max(0, delay)
        else:
            delay = 2 ** (attempt - 1)
        if delay > self.max_retry_delay:
            raise FetchError("Source Retry-After exceeds this run's retry budget; retry on a later run.",
                             details={"retry_after_seconds": delay})
        return delay

    def _request(self, params: dict) -> tuple[dict, dict]:
        url = urllib.parse.urljoin(self.base_url, "datapoints") + "?" + urllib.parse.urlencode(params)
        attempts = []
        for attempt in range(1, self.max_attempts + 1):
            requested_at = format_utc(self.now_fn())
            request = urllib.request.Request(url, headers={"Accept": "application/json",
                                                         "User-Agent": "BRIM-Dendra-soil-moisture-prototype/0.1"}, method="GET")
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    status = getattr(response, "status", None) or response.getcode()
                    if status != 200:
                        raise urllib.error.HTTPError(url, status, "Unexpected HTTP status", response.headers, None)
                    body = response.read()
                retrieved_at = format_utc(self.now_fn())
                try:
                    def invalid_constant(value: str) -> None:
                        raise ValueError(f"Nonstandard JSON numeric constant: {value}")
                    def finite_float(value: str) -> float:
                        parsed = float(value)
                        if not math.isfinite(parsed):
                            raise ValueError("JSON number exceeds finite floating-point range.")
                        return parsed
                    payload = json.loads(body, parse_constant=invalid_constant, parse_float=finite_float)
                except (ValueError, UnicodeError) as exc:
                    raise FetchError("Dendra returned invalid JSON or an unsupported numeric value.",
                                     details={"url": url, "status": status}) from exc
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    raise FetchError("Dendra response did not contain a data array.", details={"url": url, "status": status})
                info = {"url": url, "requested_at_utc": requested_at,
                        "retrieved_at_utc": retrieved_at, "status": status,
                        "attempt_count": attempt, "prior_attempts": attempts,
                        "response_sha256": _sha256(body), "response_bytes": len(body),
                        "row_count": len(payload["data"])}
                return payload, info
            except urllib.error.HTTPError as exc:
                attempts.append({"attempt": attempt, "requested_at_utc": requested_at,
                                 "status": exc.code, "error": str(exc)})
                retryable = exc.code in (408, 429, 500, 502, 503, 504)
                if not retryable or attempt == self.max_attempts:
                    raise FetchError(f"Dendra returned HTTP {exc.code}; interval not published.",
                                     details={"url": url, "attempts": attempts, "status": exc.code}) from exc
                try:
                    delay = self._retry_delay(attempt, exc.headers)
                except FetchError as budget_error:
                    budget_error.details.update({"url": url, "attempts": attempts, "status": exc.code})
                    raise
                self.sleep_fn(delay)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                attempts.append({"attempt": attempt, "requested_at_utc": requested_at,
                                 "error": str(exc), "error_type": type(exc).__name__})
                if attempt == self.max_attempts:
                    raise FetchError("Dendra network request failed; interval not published.",
                                     details={"url": url, "attempts": attempts}) from exc
                try:
                    delay = self._retry_delay(attempt, None)
                except FetchError as budget_error:
                    budget_error.details.update({"url": url, "attempts": attempts})
                    raise
                self.sleep_fn(delay)
        raise AssertionError("Unreachable request state")

    def fetch_interval(self, stream_id: str, start: str | datetime, end: str | datetime) -> dict:
        stream_id = _safe_component(stream_id, _SAFE_ID, "datastream ID")
        begin, stop = parse_utc(start), parse_utc(end)
        if begin >= stop:
            raise ValueError("Interval start must precede end.")
        cursor, continuing, source_rows, pages = begin, False, [], []
        completion = None
        for _ in range(self.max_pages):
            params = {"datastream_id": stream_id, "time[$gte]": format_utc(cursor),
                      "time[$lt]": format_utc(stop), "$sort[time]": 1, "$limit": self.page_size}
            try:
                payload, info = self._request(params)
            except FetchError as exc:
                exc.details["completed_pages"] = pages
                raise
            data, limit = payload["data"], payload.get("limit")
            if (isinstance(limit, bool) or not isinstance(limit, int) or
                    not 1 <= limit <= self.page_size):
                raise PaginationError("Missing or invalid effective page limit; completeness unresolved.",
                                      details={"completed_pages": pages, "last_request": info})
            if len(data) > limit:
                raise PaginationError("Source returned more rows than its effective limit.",
                                      details={"completed_pages": pages, "last_request": info})
            info["effective_limit"] = limit
            pages.append(info)
            if not data:
                completion = "empty_page"
                break
            previous, last = None, None
            for row in data:
                try:
                    stamp = parse_utc(row.get("t")) if isinstance(row, dict) else None
                    if stamp is None:
                        raise ValueError("Observation is not an object")
                except (ValueError, TypeError) as exc:
                    raise PaginationError("Invalid or unsupported UTC t; cannot page safely.",
                                          details={"completed_pages": pages}) from exc
                if not cursor <= stamp < stop:
                    raise PaginationError("Source row violates the requested page time bounds.",
                                          details={"completed_pages": pages, "offending_t": row["t"],
                                                   "cursor_inclusive_utc": format_utc(cursor),
                                                   "end_exclusive_utc": format_utc(stop)})
                if previous is not None and stamp < previous:
                    raise PaginationError("Dendra returned rows out of requested ascending time order.",
                                          details={"completed_pages": pages})
                previous = stamp
                last = stamp
            source_rows.extend(data)
            if last is None:
                raise PaginationError("Nonempty page contains no rows within its requested time bounds.",
                                      details={"completed_pages": pages})
            info["first_source_t"], info["last_source_t"] = data[0]["t"], data[-1]["t"]
            if len(data) < limit:
                completion = "short_page_with_effective_limit"
                break
            if continuing and last <= cursor:
                raise PaginationError("Full overlapping page cannot advance; completeness unresolved.",
                                      details={"completed_pages": pages})
            cursor, continuing = last, True
        if completion is None:
            raise PaginationError("Page ceiling reached before interval completion.", details={"completed_pages": pages})
        normalized, diagnostics = normalize_rows(source_rows, begin, stop)
        diagnostics["completion_reason"] = completion
        envelope = {"schema_version": "1.0", "datastream_id": stream_id,
                    "source": "Dendra v2 anonymous datapoints",
                    "query_complete": True,
                    "requested_interval": {"start_inclusive": format_utc(begin), "end_exclusive": format_utc(stop)},
                    "retrieval_first_utc": pages[0]["retrieved_at_utc"],
                    "retrieval_last_utc": pages[-1]["retrieved_at_utc"],
                    "latest_observation_utc": normalized[-1]["t"] if normalized else None,
                    "rows": normalized, "page_count": len(pages), "pages": pages,
                    "diagnostics": diagnostics,
                    "interpretation": "Query completion is not proof of continuous observations or source data quality."}
        envelope["content_sha256"] = _content_hash(envelope)
        return envelope

    def fetch_chunk(self, store: ChunkStore, stream_id: str, start: str | datetime,
                    end: str | datetime, *, chunk_key: str, refresh: bool = False) -> dict:
        interval = {"start_inclusive": format_utc(start), "end_exclusive": format_utc(end)}
        if parse_utc(start) >= parse_utc(end):
            raise ValueError("Interval start must precede end.")
        store.chunk_path(stream_id, chunk_key)
        existing = store.load(stream_id, chunk_key)
        if existing and not refresh and existing["requested_interval"] == interval:
            return dict(existing, cache_hit=True)
        try:
            envelope = self.fetch_interval(stream_id, start, end)
        except Exception as exc:
            store.record_failure(stream_id, chunk_key, interval, exc, format_utc(self.now_fn()))
            raise
        # Replace the complete interval, including removal of rows now excluded
        # by source annotations. Never upsert into a stale prior interval.
        store.save(envelope, chunk_key)
        return dict(envelope, cache_hit=False)

    def fetch_months(self, store: ChunkStore, stream_id: str, start: str | datetime,
                     end: str | datetime, *, refresh: bool = False) -> Iterator[dict]:
        """Sequential, resumable UTC-calendar chunks for one stream."""
        for key, begin, stop in monthly_windows(start, end):
            yield self.fetch_chunk(store, stream_id, begin, stop, chunk_key=key, refresh=refresh)
