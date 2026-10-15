"""Opt-in serial AWDB seam, inactive until separately reviewed and authorized.

Default construction/collection refuses real I/O. Tests inject a raw, nonretrying,
nonredirecting opener. A future explicitly authorized caller may pass live=True;
no CLI, workflow or existing collector invokes this module. The ledger reserves
physical attempts before opening and charges bytes returned by the response-body
reader (including errors). These are consumed body bytes, NOT full wire bytes.

Plans contain {request, metadata_body, metadata_sha256} for each station-scoped
logical request. Metadata must already be saved/pinned; this seam rejects all
metadata HTTP requests, so none can bypass the physical budget. Source science,
flags and exact identities are delegated to the unchanged saved-response adapter.
"""

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import signal
import threading
import time
import urllib.error
import urllib.request

from . import snotel_history as history

MIB = 1024**2
MAX_LOGICAL = 32
MAX_ATTEMPTS = 64
MAX_RESPONSE = MIB
MAX_AGGREGATE = 64 * MIB


class Held(RuntimeError):
    """No further provider I/O is safe under the present plan/checkpoint."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise urllib.error.HTTPError(request.full_url, code, message, headers, fp)


def _hash(body):
    return hashlib.sha256(body).hexdigest()


def _check(condition, message):
    if not condition:
        raise Held(message)


def _utc(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


@contextmanager
def _deadline(seconds):
    _check(threading.current_thread() is threading.main_thread(), "serial main thread required")
    _check(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "existing process alarm")
    previous = signal.getsignal(signal.SIGALRM)

    def expired(signum, frame):
        raise Held("deadline during physical attempt")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class StationTransport:
    """One private checkpoint directory per immutable plan; no implicit reset.

    Complete logical requests replay without I/O. Failed retries remain captures.
    Pending/reading/uncertain or cap-truncated attempts hold the entire lane on
    resume. Metadata is supplied separately for EACH station, preserving the
    adapter's 32-sensor, 32-capture, 8 MiB per-station archive limits.
    """

    def __init__(self, directory, plan, *, live=False, opener=None, max_attempts=64,
                 max_response_bytes=MIB, max_total_bytes=64 * MIB,
                 max_elapsed_seconds=300, retries=1, retry_delay=1,
                 wall=time.time, monotonic=time.monotonic, sleep=time.sleep):
        for value, ceiling in ((max_attempts, MAX_ATTEMPTS),
                               (max_response_bytes, MAX_RESPONSE),
                               (max_total_bytes, MAX_AGGREGATE),
                               (max_elapsed_seconds, 1800)):
            _check(type(value) is int and 0 < value <= ceiling, "invalid budget")
        _check(type(live) is bool, "explicit live boolean required")
        _check(type(retries) is int and 0 <= retries <= 2, "retry bound")
        _check(type(retry_delay) in (int, float) and math.isfinite(retry_delay)
               and 0 <= retry_delay <= 15, "backoff bound")
        _check(isinstance(plan, list) and 0 < len(plan) <= MAX_LOGICAL, "32 logical request limit")
        self.specs, station_metadata = {}, {}
        for spec in plan:
            _check(set(spec) == {"request", "metadata_body", "metadata_sha256"}, "exact plan fields")
            sensors = history._sensors(spec["metadata_body"], spec["metadata_sha256"])
            stations = {s["station_triplet"] for s in sensors.values()}
            _check(len(stations) == 1, "one station per metadata/archive boundary")
            request = copy.deepcopy(spec["request"])
            identities, dates = history._request(request, sensors)
            station = request["parameters"]["stationTriplets"]
            _check(stations == {station}, "station metadata mismatch")
            _check(station_metadata.setdefault(station, spec["metadata_sha256"]) ==
                   spec["metadata_sha256"], "station metadata changed within plan")
            key = _hash(history.canonical(request))
            _check(key not in self.specs, "duplicate logical request")
            self.specs[key] = dict(request=request, station=station, identities=identities,
                                   metadata_body=spec["metadata_body"],
                                   metadata_sha256=spec["metadata_sha256"])
        _check(not Path(directory).is_symlink(), "symlink checkpoint")
        self.directory = Path(directory).resolve()
        repo = Path(__file__).resolve().parents[2]
        _check(self.directory != repo and repo not in self.directory.parents,
               "checkpoint must be outside repository")
        self.limits = dict(attempts=max_attempts, response=max_response_bytes,
                           total=max_total_bytes, seconds=max_elapsed_seconds,
                           retries=retries, retry_delay=retry_delay)
        self.plan_sha256 = _hash(history.canonical({
            "limits": self.limits, "requests": {k: {n: v[n] for n in
                ("request", "station", "identities", "metadata_sha256")}
                for k, v in self.specs.items()}}))
        self.live, self.opener = live, opener
        self.wall, self.monotonic, self.sleep = wall, monotonic, sleep
        self.started_mono = monotonic()
        self.last_mono = self.started_mono
        self.ledger = None

    @contextmanager
    def _lock(self):
        self.directory.mkdir(mode=0o700, parents=False, exist_ok=True)
        _check(not self.directory.is_symlink(), "symlink checkpoint")
        with (self.directory / "lane.lock").open("a+b") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Held("another checkpoint writer") from exc
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _persist(self):
        self.ledger["sha256"] = _hash(history.canonical(
            {k: v for k, v in self.ledger.items() if k != "sha256"}))
        temporary = self.directory / "ledger.pending"
        # An orphan pending file is evidence, not disposable cleanup.
        with temporary.open("xb") as file:
            file.write(history.canonical(self.ledger))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, self.directory / "ledger.json")
        fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _load(self):
        _check(not (self.directory / "ledger.pending").exists(), "interrupted ledger replacement")
        path = self.directory / "ledger.json"
        if not path.exists():
            _check(set(p.name for p in self.directory.iterdir()) == {"lane.lock"},
                   "checkpoint collision")
            wall = self.wall()
            _check(type(wall) in (int, float) and math.isfinite(wall), "invalid clock")
            self.ledger = dict(version=1, plan_sha256=self.plan_sha256, created_wall=wall,
                               last_wall=wall, attempts=[], logical={k: "NOT_QUERIED" for k in self.specs})
            self._persist()
            return
        _check(path.stat().st_size <= 1024**2, "ledger bound")
        data = history._json(path.read_bytes())
        _check(data.get("sha256") == _hash(history.canonical(
            {k: v for k, v in data.items() if k != "sha256"})), "ledger checksum mismatch")
        _check(data.get("version") == 1 and data.get("plan_sha256") == self.plan_sha256,
               "checkpoint plan/budget mismatch")
        _check(set(data["logical"]) == set(self.specs) and
               len(data["attempts"]) <= self.limits["attempts"], "ledger scope/count")
        known_total, expected_files = 0, {"lane.lock", "ledger.json"}
        by_key = {k: [] for k in self.specs}
        by_station = {}
        for n, attempt in enumerate(data["attempts"], 1):
            _check(attempt["number"] == n and attempt["key"] in self.specs, "attempt identity")
            _check(attempt["state"] in ("RESERVED", "READING", "COMPLETE", "HELD"), "attempt state")
            _check(attempt["status"] is None or type(attempt["status"]) is int and
                   100 <= attempt["status"] <= 599, "stored HTTP status")
            _check(attempt["error"] is None or isinstance(attempt["error"], str), "stored error")
            size = attempt["captured_prefix_bytes"]
            _check(type(size) is int and 0 <= size <= self.limits["response"], "attempt byte count")
            known_total += size
            filename = f"body-{n:03d}.bin"
            expected_files.add(filename)
            body_path = self.directory / filename
            if body_path.exists():
                _check(not body_path.is_symlink() and body_path.stat().st_size == size,
                       "checkpoint body length changed")
                _check(_hash(body_path.read_bytes()) == attempt["prefix_sha256"], "checkpoint body hash")
            else:
                _check(attempt["state"] == "RESERVED" and size == 0, "checkpoint body missing")
            consumed = attempt["total_consumed_bytes"]
            _check(consumed is None or type(consumed) is int and consumed == size, "consumed byte mismatch")
            if attempt["state"] == "COMPLETE":
                _check(type(consumed) is int and consumed == size and
                       type(attempt["status"]) is int, "complete attempt has unknown accounting")
            by_key[attempt["key"]].append(attempt)
            by_station.setdefault(self.specs[attempt["key"]]["station"], []).append(attempt)
        _check(known_total <= self.limits["total"], "aggregate byte count")
        for attempts in by_station.values():
            _check(len(attempts) <= 32 and sum(a["captured_prefix_bytes"] for a in attempts) <= 8 * MIB,
                   "stored per-station adapter bounds")
        for key, state in data["logical"].items():
            _check(state in ("NOT_QUERIED", "IN_PROGRESS", "COMPLETE", "FAILED", "HELD"), "logical state")
            attempts = by_key[key]
            _check(len(attempts) <= self.limits["retries"] + 1, "stored logical retry bound")
            _check((state == "NOT_QUERIED") == (not attempts), "logical attempt binding")
            if state in ("COMPLETE", "FAILED"):
                _check(all(a["state"] == "COMPLETE" for a in attempts), "terminal logical contains incomplete body")
                last = attempts[-1]
                if state == "COMPLETE":
                    _check(last["status"] == 200 and last["error"] is None, "logical success lacks successful attempt")
                else:
                    _check(last["status"] != 200 and isinstance(last["error"], str), "logical failure binding")
        _check(set(p.name for p in self.directory.iterdir()) <= expected_files, "unexpected checkpoint file")
        for clock in ("created_wall", "last_wall"):
            _check(type(data[clock]) in (int, float) and math.isfinite(data[clock]), "invalid stored clock")
        _check(data["last_wall"] >= data["created_wall"], "stored clock rollback")
        self.ledger = data

    def _remaining(self):
        current, mono = self.wall(), self.monotonic()
        _check(type(current) in (int, float) and math.isfinite(current) and
               type(mono) in (int, float) and math.isfinite(mono), "invalid clock")
        _check(current >= self.ledger["last_wall"] and mono >= self.last_mono, "clock rollback")
        self.last_mono = mono
        self.ledger["last_wall"] = current
        remaining = self.limits["seconds"] - max(mono - self.started_mono,
                                                 current - self.ledger["created_wall"])
        _check(remaining > 0, "cumulative deadline exhausted")
        return remaining

    def _safe_to_open(self, key):
        attempts = self.ledger["attempts"]
        _check(all(a["state"] == "COMPLETE" and a["total_consumed_bytes"] is not None
                   for a in attempts), "interrupted/unknown/incomplete body accounting; review required")
        _check(len(attempts) < self.limits["attempts"], "physical attempt limit")
        _check(sum(a["captured_prefix_bytes"] for a in attempts) < self.limits["total"],
               "aggregate body limit")
        station = self.specs[key]["station"]
        prior = [a for a in attempts if self.specs[a["key"]]["station"] == station]
        _check(len(prior) < 32 and sum(a["captured_prefix_bytes"] for a in prior) < 8 * MIB,
               "per-station adapter capture/body bound")
        return self._remaining()

    def _attempt(self, key):
        remaining = self._safe_to_open(key)
        attempts = self.ledger["attempts"]
        a = dict(number=len(attempts) + 1, key=key, state="RESERVED", status=None,
                 captured_prefix_bytes=0, total_consumed_bytes=None, prefix_sha256=_hash(b""),
                 requested_at_utc=_utc(self.ledger["last_wall"]), retrieved_at_utc=None,
                 error=None, retry_after=None)
        attempts.append(a)
        self.ledger["logical"][key] = "IN_PROGRESS"
        self._persist()  # Durable reservation BEFORE opener; crash holds resume.
        response = None
        with (self.directory / f"body-{a['number']:03d}.bin").open("xb") as saved:
            try:
                # Reservation/fsync time is part of the same cumulative window.
                remaining = self._remaining()
                with _deadline(remaining):
                    request = urllib.request.Request(self.specs[key]["request"]["url"],
                                                     headers={"Accept-Encoding": "identity"}, method="GET")
                    opener = self.opener
                    if opener is None:
                        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open
                    error_response = False
                    try:
                        response = opener(request, timeout=min(30, remaining))
                    except urllib.error.HTTPError as exc:
                        response, error_response = exc, True
                    a["status"] = response.code if error_response else response.status
                    _check(type(a["status"]) is int and 100 <= a["status"] <= 599, "invalid HTTP status")
                    if error_response:
                        _check(response.fp is not None and not
                               (type(response.fp) is io.BytesIO and response.fp.getbuffer().nbytes == 0),
                               "HTTP error body accounting unavailable")
                    a["retry_after"] = response.headers.get("Retry-After") if response.headers else None
                    self._read(response, a, saved)
                    self._remaining()
                    if a["status"] != 200:
                        a["error"] = "HTTP_" + str(a["status"])
            except BaseException as exc:
                if a["state"] != "COMPLETE":
                    a["state"] = "HELD"
                a["error"] = type(exc).__name__ + ": " + (str(exc) if isinstance(exc, Held) else "transport interrupted")
                self.ledger["logical"][key] = "HELD"
                raise Held(a["error"]) from exc
            finally:
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        a["error"] = "response close failed"
                        a["state"] = "HELD"
                saved.flush()
                os.fsync(saved.fileno())
                a["retrieved_at_utc"] = _utc(self.wall())
                self._persist()
        _check(a["state"] == "COMPLETE", "response close/accounting held")
        return a

    def _read(self, response, attempt, saved):
        attempt["state"] = "READING"
        self._persist()
        previous = self.ledger["attempts"][:-1]
        station = self.specs[attempt["key"]]["station"]
        allowance = min(self.limits["response"],
                        self.limits["total"] - sum(a["captured_prefix_bytes"] for a in previous),
                        8 * MIB - sum(a["captured_prefix_bytes"] for a in previous
                                     if self.specs[a["key"]]["station"] == station))
        length = response.headers.get("Content-Length") if response.headers else None
        declared = None
        if length is not None:
            _check(isinstance(length, str) and length.isascii() and length.isdecimal(), "invalid Content-Length")
            declared = int(length)
            _check(declared <= allowance, "declared body exceeds remaining allowance")
        hasher = hashlib.sha256()

        def charge(chunk, requested):
            _check(type(chunk) is bytes and len(chunk) <= requested, "reader violated byte boundary")
            saved.write(chunk)
            saved.flush()
            os.fsync(saved.fileno())
            hasher.update(chunk)
            attempt["captured_prefix_bytes"] += len(chunk)
            attempt["prefix_sha256"] = hasher.hexdigest()
            self._persist()

        while attempt["captured_prefix_bytes"] < allowance:
            self._remaining()
            requested = min(64 * 1024, allowance - attempt["captured_prefix_bytes"])
            try:
                chunk = response.read(requested)
            except BaseException as exc:
                partial = getattr(exc, "partial", None)
                if type(partial) is bytes:
                    charge(partial, requested)
                # Saved bytes are known; total consumption is still UNKNOWN.
                raise Held("partial/canceled read; total body consumption UNKNOWN") from exc
            charge(chunk, requested)
            if chunk == b"":
                _check(declared is None or declared == attempt["captured_prefix_bytes"], "truncated Content-Length")
                attempt["state"] = "COMPLETE"
                attempt["total_consumed_bytes"] = attempt["captured_prefix_bytes"]
                self._persist()
                return
        # Never spend limit+1 on an EOF probe; header length alone is not proof.
        attempt["total_consumed_bytes"] = attempt["captured_prefix_bytes"]
        raise Held("body allowance reached without proven EOF")

    def collect(self):
        """Return every attempted capture; a held lane raises and preserves evidence."""
        _check(self.live or self.opener is not None, "offline default: real provider I/O disabled")
        with self._lock():
            self._load()
            for key in self.specs:
                state = self.ledger["logical"][key]
                if state in ("COMPLETE", "FAILED"):
                    continue  # Idempotent replay, including terminal known failures.
                _check(state == "NOT_QUERIED", "interrupted logical request; review required")
                for retry in range(self.limits["retries"] + 1):
                    attempt = self._attempt(key)
                    status = attempt["status"]
                    if status == 200:
                        self.ledger["logical"][key] = "COMPLETE"
                        break
                    if status not in (429, 500, 502, 503, 504) or retry == self.limits["retries"]:
                        self.ledger["logical"][key] = "FAILED"
                        break
                    delay = self.limits["retry_delay"] * (2**retry)
                    if attempt["retry_after"] is not None:
                        header = attempt["retry_after"]
                        _check(isinstance(header, str) and header.isascii() and header.isdecimal(),
                               "unsupported Retry-After; review required")
                        delay = max(delay, int(header))
                    remaining = self._remaining()
                    self._persist()
                    _check(delay <= 15 and delay < remaining, "retry backoff exceeds deadline/bound")
                    with _deadline(remaining):
                        self.sleep(delay)
                    self._remaining()
                self._persist()
            return self._captures()

    def _captures(self):
        output = []
        for a in self.ledger["attempts"]:
            path = self.directory / f"body-{a['number']:03d}.bin"
            body = path.read_bytes() if path.exists() else b""
            receipt = dict(request=copy.deepcopy(self.specs[a["key"]]["request"]),
                           retrieved_at_utc=a["retrieved_at_utc"] or a["requested_at_utc"],
                           http_status=a["status"], complete=a["state"] == "COMPLETE" and
                           a["error"] is None and a["status"] == 200 and
                           type(a["total_consumed_bytes"]) is int and a["total_consumed_bytes"] == len(body),
                           error=a["error"] or (None if a["state"] == "COMPLETE" else "INCOMPLETE"),
                           response_bytes=len(body), response_sha256=_hash(body),
                           physical_attempt=a["number"], captured_prefix_bytes=len(body),
                           total_consumed_body_bytes=a["total_consumed_bytes"],
                           body_accounting_state=a["state"], plan_sha256=self.plan_sha256)
            output.append(dict(body=body, receipt=receipt,
                               receipt_sha256=_hash(history.canonical(receipt))))
        return output

    def saved_captures(self):
        """Read retained prefixes/receipts without any provider I/O, even when held."""
        _check((self.directory / "ledger.json").is_file(), "no retained checkpoint")
        with self._lock():
            self._load()
            return self._captures()

    def station_histories(self):
        """Validate saved captures separately per station; never a merged archive."""
        captures = self.saved_captures()
        output = {}
        for spec in self.specs.values():
            station = spec["station"]
            if station in output:
                continue
            subset = [c for c in captures if c["receipt"]["request"]["parameters"]["stationTriplets"] == station]
            if subset:
                output[station] = history.build_history(spec["metadata_body"], spec["metadata_sha256"], subset)
        return output
