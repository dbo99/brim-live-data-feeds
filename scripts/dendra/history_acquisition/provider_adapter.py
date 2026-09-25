"""Explicit synchronous D3 boundary. Import/construction/planning never sends HTTP.

Only run_authorized_probe opens a real anonymous connection, when explicitly
called with a separately approved plan/root/window. Offline tests inject a
finite response callable into Adapter.run; there is no command-line dispatch.
"""
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
import io
import math
from pathlib import Path
import re
import signal
import threading
import time
import urllib.error
import urllib.request

from ..transport import DendraFetcher, FetchError, parse_utc, format_utc
from .d3_plan import (RequestSpec, SELECTED, START, END, validate_binding,
                      validate_request, seven_calls)
from .provider_metadata import parse_vocabulary, parse_station, parse_datastreams
from .safety import Hold, require, encode, decode, digest

RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})
BODY_LIMIT = 8 * 1024**2


def retry_delay(value, *, ordinal, now, remaining):
    """Strict admission before the existing fetcher sees a Retry-After value."""
    require(ordinal in (1, 2) and math.isfinite(remaining) and remaining > 0, "Retry budget")
    if value is None:
        delay = float(2 ** (ordinal - 1))
    else:
        require(isinstance(value, str) and 0 < len(value) <= 128, "Invalid Retry-After")
        if re.fullmatch(r"[0-9]+", value):
            delay = float(value)
        else:
            try:
                until = parsedate_to_datetime(value)
                require(until.tzinfo is not None and until.utcoffset().total_seconds() == 0,
                        "Retry-After date must be UTC")
                delay = max(0.0, (until - parse_utc(now)).total_seconds())
            except (ValueError, TypeError, OverflowError) as exc:
                raise Hold("Invalid Retry-After") from exc
    require(math.isfinite(delay) and 0 <= delay <= 15 and delay < remaining,
            "Retry-After exceeds remaining probe budget")
    return delay


class Deadline(Hold):
    pass


@contextmanager
def total_deadline(seconds):
    """Serial POSIX total deadline, including slow-drip reads (as in bridge)."""
    require(threading.current_thread() is threading.main_thread(), "Serial main thread required")
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "Existing process timer conflicts")
    require(0 < seconds <= 25, "Per-request deadline bound")
    previous = signal.getsignal(signal.SIGALRM)
    def expire(*_):
        raise Deadline("Per-request total deadline exhausted")
    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class MemoryResponse(io.BytesIO):
    status = 200
    headers = {}


def observation_shape(body, sid):
    payload = decode(body)
    require(isinstance(payload, dict) and set(payload) <= {"data", "limit", "total", "skip"},
            "Unexpected observation envelope fields")
    rows, limit = payload.get("data"), payload.get("limit")
    require(isinstance(rows, list) and type(limit) is int and 0 < limit <= 2016 and len(rows) <= limit,
            "Effective observation limit")
    for name in ("total", "skip"):
        require(name not in payload or (type(payload[name]) is int and payload[name] >= 0),
                "Restricted observation pagination metadata")
    for row in rows:
        require(isinstance(row, dict) and set(row) <= {"_id", "datastream_id", "t", "v", "lt", "q"} and
                row.get("datastream_id", sid) == sid, "Restricted or unselected observation metadata")
        parse_utc(row.get("t"))
        for name, value in row.items():
            require(value is None or type(value) in (str, int, float, bool), "Nested observation metadata")
            require(not isinstance(value, str) or len(value) <= 256, "Observation scalar bound")
    return payload


class Adapter:
    def __init__(self, journal):
        validate_binding(journal.binding, journal.tasks)
        require(not journal.damage, "Recovery journal cannot dispatch")
        self.journal = journal
        self.binding_hash = digest(journal.binding)
        self.authority = journal.binding["d3"]["authority"]
        self.executor = self.wait = None
        self.active = False
        self.halted = False
        self.window_end = None
        self.permissions = {}
        self.vocabulary, self.stations = None, {}
        self.current_spec = self.current_interval = self.current_run = None
        self.successful = []

    def plan(self):
        return [s.descriptor() for s in seven_calls()]

    def remaining(self):
        require(not self.halted and not self.journal.damage, "Receipt failure requires review")
        require(digest(self.journal.binding) == self.binding_hash, "Adapter binding changed")
        elapsed = self.journal.check_budget()
        value = (self.journal.binding["budgets"]["elapsed_ms"] - elapsed) / 1000
        if self.window_end is not None:
            value = min(value, (parse_utc(self.window_end) - parse_utc(self.journal.now())).total_seconds())
        require(value > 0, "D3 wall ceiling exhausted")
        return value

    def pause(self, seconds):
        require(self.active and self.wait is not None and 0 <= seconds <= 15 and seconds < self.remaining(),
                "Retry delay cannot fit probe")
        counts = self.journal.snapshot()["counters"]
        require(counts["attempts"] < self.journal.binding["budgets"]["attempts"], "No retry attempts remain")
        self.wait(seconds)
        self.remaining()

    def _request(self, spec):
        return urllib.request.Request(spec.url(), method="GET", headers={
            "Accept": "application/json", "User-Agent": "BRIM-Dendra-D3/1.0"})

    def _details(self, spec, began, mono):
        return dict(kind=spec.kind, outcome="received", requested_at=began,
            retrieved_at=format_utc(self.journal.now()),
            duration_ms=max(0, int((self.journal.monotonic() - mono) * 1000)), retryable=False,
            retry_after_seconds=None, effective_limit=None, page_complete=None,
            privacy="not_evaluated", identity="not_evaluated", error_code=None)

    def _persist(self, operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except OSError as exc:
            self.halted = True
            raise Hold("Local receipt persistence failed; no HTTP retry") from exc

    def _parse_metadata(self, spec, body):
        if spec.kind == "unit-vocabulary":
            return parse_vocabulary(body, self.authority)
        if spec.kind == "station":
            station_id = self.journal.binding["roster"][spec.selected_stream]["station_id"]
            return parse_station(body, station_id, checked_at=self.journal.now(), now=self.journal.now())
        require(spec.kind == "datastream-list" and self.vocabulary is not None and
                spec.selected_stream in self.stations, "Metadata order")
        return parse_datastreams(body, self.journal.binding["roster"][spec.selected_stream],
            self.stations[spec.selected_stream], self.vocabulary, self.authority,
            checked_at=self.journal.now(), now=self.journal.now())

    def exchange(self, request, spec, *, interval_key=None, run=0):
        require(self.active and self.executor is not None, "Explicit runner required")
        require((spec.kind == "observations") == (interval_key is not None), "Receipt task kind mismatch")
        if interval_key is not None:
            require(interval_key in self.journal.tasks and self.journal.tasks[interval_key]["identity"]["stream_id"]
                    == spec.selected_stream, "Receipt selected-stream mismatch")
        validate_request(request, spec)
        remaining = self.remaining()
        counts = self.journal.snapshot()["counters"]
        budgets = self.journal.binding["budgets"]
        require(counts["response_bytes"] < budgets["response_bytes"] and
                counts["source_rows"] < budgets["source_rows"], "No response capacity remains")
        read_limit = min(BODY_LIMIT, budgets["response_bytes"] - counts["response_bytes"])
        if interval_key is not None:
            self._permission(spec.selected_stream)
        task = interval_key or ("unit-vocabulary" if spec.kind == "unit-vocabulary"
                                else "metadata-" + spec.selected_stream)
        cursor = spec.cursor if interval_key else spec.kind
        key = self._persist(self.journal.reserve, task, cursor, interval_key=interval_key, run=run)
        self._persist(self.journal.started, key)
        began, mono = format_utc(self.journal.now()), self.journal.monotonic()
        body, status, row_count, value, headers, caught = b"", None, 0, None, {}, None
        details = self._details(spec, began, mono)
        retain, sanitized = False, None
        try:
            remaining = self.remaining()
            with total_deadline(min(25, remaining)):
                try:
                    response = self.executor(request, timeout=min(25, remaining))
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    status = response.status if getattr(response, "status", None) is not None else response.code
                    require(type(status) is int and 100 <= status <= 599, "Invalid response status")
                    headers = response.headers
                    require(headers.get("Content-Encoding", "identity").lower() == "identity",
                            "Encoded response body not supported")
                    # A hard total alarm bounds slow reads. Read one sentinel byte
                    # beyond the body ceiling; persist/count the bytes actually read.
                    while len(body) <= read_limit:
                        chunk = response.read(min(65536, read_limit + 1 - len(body)))
                        require(isinstance(chunk, bytes), "Response must be bytes")
                        if not chunk:
                            break
                        body += chunk
                        require(self.journal.monotonic() - mono <= 25, "Per-request elapsed ceiling")
                        self.remaining()
                details = self._details(spec, began, mono)
                require(len(body) <= BODY_LIMIT, "Response exceeds 8 MiB")
                require(len(body) <= read_limit, "Cumulative response-byte ceiling")
                if status != 200:
                    details.update(outcome="failure", error_code="redirect" if 300 <= status <= 399 else "http")
                    if status in RETRYABLE:
                        ordinal = self.journal.snapshot()["attempts"][key]["ordinal"]
                        if ordinal < 2:
                            details["retryable"] = True
                            try:
                                details["retry_after_seconds"] = retry_delay(headers.get("Retry-After"),
                                    ordinal=ordinal, now=self.journal.now(), remaining=self.remaining())
                            except Hold:
                                details.update(outcome="hold", retryable=False, error_code="retry_after")
                                raise
                            details["outcome"] = "retry"
                    raise urllib.error.HTTPError(spec.url(), status, "D3 HTTP status", {}, None)
                row_count = None
                payload = decode(body)
                if isinstance(payload, dict) and isinstance(payload.get("data"), list):
                    row_count = len(payload["data"])
                elif spec.kind in ("station", "unit-vocabulary"):
                    row_count = 0
                if spec.kind == "observations":
                    value = observation_shape(body, spec.selected_stream)
                    details.update(effective_limit=value["limit"], page_complete=len(value["data"]) < value["limit"])
                    retain = True
                else:
                    value = self._parse_metadata(spec, body)
                    sanitized = encode(value)
                    if spec.kind == "datastream-list":
                        details.update(effective_limit=payload["limit"], page_complete=True)
                details.update(privacy="public", identity="match")
                # Validate capacity before writing raw/sanitized objects, while
                # still charging rejected bytes/rows through the same receipt.
                self.journal.check_budget({"response_bytes": len(body), "source_rows": row_count or 0})
                require(self.journal.monotonic() - mono <= 25, "Per-request elapsed ceiling")
        except BaseException as exc:
            caught = exc
            retain, sanitized = False, None
            if body and value is None and status == 200 and row_count == 0:
                row_count = None
            if details["error_code"] is None:
                transport = isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError))
                ordinal = self.journal.snapshot()["attempts"][key]["ordinal"]
                can_retry = transport and ordinal < 2 and row_count is not None
                details.update(outcome="retry" if can_retry else "failure" if transport else "hold",
                    retryable=can_retry, error_code="transport" if transport else
                    "deadline" if isinstance(exc, Deadline) else "body_limit" if len(body) > BODY_LIMIT
                    else "parse_or_privacy", privacy="hold", identity="hold")
        details.update(retrieved_at=format_utc(self.journal.now()),
                       duration_ms=max(0, int((self.journal.monotonic() - mono) * 1000)))
        # Reserve/start have already been durable even if this receipt cannot be
        # written (crash/storage failure). Never issue an unreserved retry.
        self._persist(self.journal.received, key, body, source_rows=row_count, status=status, retain=retain,
                      sanitized_body=sanitized, details=details)
        if caught is not None:
            self._persist(self.journal.failed, key, status)
            if isinstance(caught, urllib.error.HTTPError):
                if 300 <= status <= 399:
                    raise Hold("Redirect refused") from caught
                safe_headers = {}
                if details["retry_after_seconds"] is not None:
                    safe_headers["Retry-After"] = str(details["retry_after_seconds"])
                raise urllib.error.HTTPError(spec.url(), status, "D3 HTTP status", safe_headers, None) from None
            raise caught
        return body, value, key

    def metadata(self, spec):
        for attempt in (1, 2):
            try:
                return self.exchange(self._request(spec), spec)[1]
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRYABLE or attempt == 2:
                    raise Hold("Metadata HTTP failure") from None
                self.pause(float(exc.headers.get("Retry-After", "1")))
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
                if attempt == 2:
                    raise Hold("Metadata transport failure") from None
                self.pause(1)
        raise AssertionError("unreachable")

    def _permission(self, sid):
        require(sid in self.permissions, "No permission from this explicit metadata wave")
        view = self.journal.snapshot()["metadata"].get(sid)
        require(view and view["raw_eligible"] and view["checked_at"] == self.permissions[sid] and
                0 <= (parse_utc(self.journal.now()) - parse_utc(view["checked_at"])).total_seconds() <= 86400,
                "Fresh public metadata required; no cached-permission fallback")

    def observations(self, sid, *, recheck=False):
        self._permission(sid)
        key = next(k for k, v in self.journal.tasks.items() if v["identity"]["stream_id"] == sid)
        saved = self.journal.completed(key)
        if saved is not None and not recheck:
            return dict(cache_hit=True, envelope=saved)
        run = self.journal.start_run(key, recheck=recheck)
        successful = []
        def open_page(request, timeout):
            from urllib.parse import urlsplit, parse_qs
            params = parse_qs(urlsplit(request.full_url).query)
            cursor = params.get("time[$gte]", [None])[0]
            spec = RequestSpec("observations", sid, cursor)
            body, _, receipt = self.exchange(request, spec, interval_key=key, run=run)
            successful.append(receipt)
            return MemoryResponse(body)
        fetcher = DendraFetcher(opener=open_page, timeout=25, max_attempts=2, max_pages=20,
            page_size=2016, max_retry_delay=15, now_fn=lambda: parse_utc(self.journal.now()), sleep_fn=self.pause)
        try:
            result = fetcher.fetch_interval(sid, START, END)
            self.journal.seal(key, run, result, successful)
            return dict(cache_hit=False, envelope=result)
        except Exception:
            self.journal.hold(key, "transport_or_parse")
            raise

    def run(self, *, executor, wait, window_end=None):
        """Explicit injection boundary. Tests supply finite bytes and a fake clock."""
        require(callable(executor) and callable(wait) and not self.active, "Explicit serial runner required")
        require(threading.current_thread() is threading.main_thread(), "One local acquisition worker")
        if window_end is not None:
            require(0 < (parse_utc(window_end) - parse_utc(self.journal.now())).total_seconds() <= 300,
                    "Explicit runner window bound")
        self.window_end = window_end
        self.journal.session()
        self.executor, self.wait, self.active = executor, wait, True
        self.permissions = {}
        try:
            self.vocabulary = self.metadata(RequestSpec("unit-vocabulary"))
            self.stations = {}
            for sid in SELECTED:
                self.stations[sid] = self.metadata(RequestSpec("station", sid))
            for sid in SELECTED:
                claims = self.metadata(RequestSpec("datastream-list", sid))
                checked = format_utc(self.journal.now())
                view = self.journal.metadata(sid, claims, checked)
                require(view["raw_eligible"], "Metadata scientific/privacy HOLD")
                self.permissions[sid] = checked
            return {sid: self.observations(sid) for sid in SELECTED}
        except Exception:
            if len(self.permissions) < 2:
                # Clear any earlier permissive overlay even when a failed/404/
                # private response cannot yield safe complete metadata claims.
                for sid in SELECTED:
                    fields = self.authority["scientific"][sid]
                    denied = dict(self.journal.binding["roster"][sid], complete=False,
                        access_state="metadata-only", scientific_fields=fields,
                        scientific_sha256=self.authority["metadata_bindings"][sid],
                        dictionary_sha256=self.authority["dictionary_sha256"])
                    self.journal.metadata(sid, denied, self.journal.now())
            for key in self.journal.tasks:
                self.journal.hold(key, "metadata" if len(self.permissions) < 2 else "transport_or_parse")
            raise
        finally:
            self.executor = self.wait = None
            self.active = False
            self.permissions = {}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Redirect refused", headers, fp)


def run_authorized_probe(journal, *, authorization):
    """Live entry only for a future explicit task; never invoked by this gate.

    This declaration records the caller's reviewed authority; it is not a way
    to create permission. No default arguments, environment flag, CLI or daemon.
    """
    require(isinstance(authorization, dict) and set(authorization) == {
        "approval_reference", "binding_sha256", "task_root", "window_start", "window_end"},
        "Separate exact D3 authorization required")
    require(isinstance(authorization["approval_reference"], str) and
            1 <= len(authorization["approval_reference"]) <= 160, "Approval reference required")
    from .journal import Journal, utc_now
    require(type(journal) is Journal and journal.now is utc_now and journal.monotonic is time.monotonic,
            "Live entry requires real journal clocks; injected clocks are offline only")
    validate_binding(journal.binding)
    require(authorization["binding_sha256"] == digest(journal.binding), "Approved binding differs")
    # Compare an already open directory identity; do not create production roots.
    import os
    root = Path(authorization["task_root"])
    require(root.is_absolute() and not root.is_symlink() and root.is_dir(), "Explicit existing task root")
    require(os.stat(root).st_ino == os.fstat(journal.fs.fd).st_ino and
            os.stat(root).st_dev == os.fstat(journal.fs.fd).st_dev, "Approved root differs")
    start, end, now = map(parse_utc, (authorization["window_start"], authorization["window_end"], journal.now()))
    require(start <= now < end and (end - start).total_seconds() <= 300, "Approved probe window")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    def execute(request, timeout):
        remaining = (end - parse_utc(journal.now())).total_seconds()
        require(remaining > 0, "Approved resource window ended")
        return opener.open(request, timeout=min(timeout, remaining))
    def wait(seconds):
        require((end - parse_utc(journal.now())).total_seconds() > seconds, "Retry exceeds authorized window")
        time.sleep(seconds)
    return Adapter(journal).run(executor=execute, wait=wait, window_end=authorization["window_end"])
