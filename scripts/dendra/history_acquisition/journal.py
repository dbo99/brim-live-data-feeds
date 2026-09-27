"""Immutable local receipts with a fixed campaign namespace and bounded replay.

No publication pointer, production state format, HTTP client or cleanup API.
Checksums provide local integrity, not signatures or protection from deliberate
deletion/replacement of the entire trusted task root by an external actor.
"""
import fcntl
import os
import time
from datetime import datetime, timezone

from ..transport import parse_utc, format_utc, normalize_rows, _content_hash
from .model import PAGE_BYTES, PAGE_ENTRIES, MAX_PLAN, index_pages, source_binding, metadata_view, plan
from .safety import Root, Hold, require, encode, decode, digest, sha

EVENT_BYTES = 65536
BODY_BYTES = 8 * 1024**2
KINDS = {"session", "metadata", "run", "reserved", "started", "received",
         "failure", "held", "sealed", "daily_evidence"}


class UnknownSourceRowCount(Hold):
    """The unchanged unknown-row guard, distinguishable after a durable receipt."""


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Journal:
    def __init__(self, task_root, binding, tasks, *, create=False, recovery=False,
                 now=utc_now, monotonic=time.monotonic, inventory=None, inspect_only=False):
        self.fs = Root(task_root)
        self.lock = None
        self.binding, self.tasks = decode(encode(binding)), decode(encode(tasks))
        self.binding_sha, self.tasks_sha = digest(binding), digest(tasks)
        self.now, self.monotonic = now, monotonic
        self.open_mono, self.open_wall = monotonic(), parse_utc(now())
        self.prefix = "campaigns/" + binding["campaign_id"]
        self.damage = None
        self.events = []
        self.inventory, self.inspect_only = inventory, inspect_only
        try:
            require(not (inspect_only and create), "Inspection cannot initialize state")
            if inspect_only:
                require(binding["mode"] in ("offline_only", "d3_explicit_adapter", "campaign_reviewed_adapter", "authority_witness_adapter"),
                        "Unsupported historical journal format")
            elif binding["mode"] == "authority_witness_adapter":
                from .authority_witness import validate_binding
                validate_binding(binding, tasks, inventory=inventory)
            elif binding["mode"] == "campaign_reviewed_adapter":
                from .campaign_execution import validate_binding
                validate_binding(binding, tasks, inventory=inventory)
            elif binding["mode"] != "offline_only":
                from .d3_plan import validate_binding
                validate_binding(binding, tasks)
            if not inspect_only:
                require(binding["collector_sources"] == source_binding(), "Collector source binding changed")
            if not inspect_only and binding["mode"] not in ("campaign_reviewed_adapter", "authority_witness_adapter"):
                require(all(k == digest(t) and t["campaign_sha256"] == digest(binding) and
                        t["identity"] == binding["roster"].get(t["identity"]["stream_id"]) and
                        t["identity"]["stream_id"] in binding["selected_ids"]
                        for k, t in tasks.items()), "Task/campaign binding mismatch")
                require(plan(binding, [(t["identity"]["stream_id"], t["start"], t["end"])
                                   for t in tasks.values()]) == tasks, "Unvalidated interval plan")
            pages = index_pages([{"key": k, "task": t} for k, t in tasks.items()])
            descriptors = [{"path": f"plan/{i:04d}.json", "sha256": digest(p), "bytes": len(encode(p))}
                           for i, p in enumerate(pages)]
            header = dict(binding=binding, plan=descriptors, task_count=len(tasks))
            require(len(encode(header)) <= PAGE_BYTES, "Campaign header exceeds bounded journal capacity")
            self.header_sha = digest(header)
            registry = "registry/" + binding["campaign_id"] + ".json"
            registration = dict(header_sha256=self.header_sha, path=self.prefix, version=binding["version"])
            if create:
                # Reserve the unique campaign ID first. Interrupted creation is a
                # hold, never an invitation to initialize fresh counters.
                self.fs.write_new(registry, encode(registration), PAGE_BYTES)
                self.fs.mkdir(self.prefix)
                self.fs.write_new(self.prefix + "/writer.lock", b"", 0)
                self.fs.write_new(self.prefix + "/manifest.json", encode(header), PAGE_BYTES)
                for descriptor, page in zip(descriptors, pages):
                    self.fs.write_new(self.prefix + "/" + descriptor["path"], encode(page), PAGE_BYTES)
                self.fs.mkdir(self.prefix + "/events")
                self.fs.mkdir("anchors/" + binding["campaign_id"])
            require(self.fs.read(registry, PAGE_BYTES) == encode(registration), "Campaign registry mismatch")
            require(self.fs.read(self.prefix + "/manifest.json", PAGE_BYTES) == encode(header),
                    "Campaign manifest changed/missing")
            for descriptor, page in zip(descriptors, pages):
                require(self.fs.read(self.prefix + "/" + descriptor["path"], PAGE_BYTES) == encode(page),
                        "Plan index changed")
            self.lock = self.fs.lock_fd(self.prefix + "/writer.lock")
            fcntl.flock(self.lock, (fcntl.LOCK_SH if inspect_only else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            self._load(recovery)
            self.open_sequence = len(self.events)
            self.base_elapsed = self.snapshot()["counters"]["elapsed_ms"]
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None
        self.fs.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _event_path(self, n):
        return f"{self.prefix}/events/{n // PAGE_ENTRIES:04d}/{n:08d}.json"

    def _load(self, recovery):
        try:
            directory = self.prefix + "/events"
            anchor_names = self.fs.list("anchors/" + self.binding["campaign_id"], MAX_PLAN)
            names = []
            for page in self.fs.list(directory, MAX_PLAN // PAGE_ENTRIES):
                require(len(page) == 4 and page.isdecimal(), "Unexpected journal page")
                entries = self.fs.list(directory + "/" + page, PAGE_ENTRIES)
                for name in entries:
                    names.append(directory + "/" + page + "/" + name)
            require(len(names) <= MAX_PLAN, "Journal event bound")
            previous = self.header_sha
            for n, name in enumerate(names):
                require(name == self._event_path(n), "Journal gap/unexpected file")
                body = self.fs.read(name, EVENT_BYTES)
                record = decode(body)
                require(body == encode(record), "Noncanonical/mutated receipt bytes")
                claimed = record.pop("record_sha256")
                require(record["sequence"] == n and record["previous_sha256"] == previous and
                        record["header_sha256"] == self.header_sha and claimed == digest(record) and
                        record["kind"] in KINDS, "Journal chain/hash mismatch")
                anchor = self.fs.read("anchors/" + self.binding["campaign_id"] + f"/{n:08d}.json",
                                      EVENT_BYTES)
                require(decode(anchor) == {**record, "record_sha256": claimed}, "Reservation anchor differs")
                if self.events:
                    require(parse_utc(record["at"]) >= parse_utc(self.events[-1]["at"]),
                            "Journal clock order")
                for descriptor in record["data"].get("objects", []):
                    self.read_object(descriptor)
                record["record_sha256"] = claimed
                self.events.append(record)
                previous = claimed
            require(anchor_names == [f"{i:08d}.json" for i in range(len(names))],
                    "Journal missing anchored reservations/events")
        except (ValueError, OSError, KeyError, TypeError) as exc:
            self.damage = type(exc).__name__ + ": incomplete/corrupt journal"
            if not recovery:
                raise Hold(self.damage) from exc

    def _append(self, kind, data):
        require(not self.inspect_only, "Historical inspection is read-only")
        require(not self.damage, "Recovery prefix is read-only; preserve damaged evidence")
        require(digest(self.binding) == self.binding_sha and digest(self.tasks) == self.tasks_sha,
                "In-memory campaign/plan changed")
        require(kind in KINDS and len(self.events) < MAX_PLAN, "Journal kind/event bound")
        at = format_utc(self.now())
        if self.events:
            require(parse_utc(at) >= parse_utc(self.events[-1]["at"]), "Wall clock moved backwards")
        previous = self.events[-1]["record_sha256"] if self.events else self.header_sha
        record = dict(sequence=len(self.events), previous_sha256=previous, header_sha256=self.header_sha,
                      kind=kind, at=at, data=data)
        record["record_sha256"] = digest(record)
        # Exclusive final-name creation: a crash/torn write is detectable and
        # cannot be overwritten by a reopened process.
        try:
            self.fs.write_new("anchors/" + self.binding["campaign_id"] +
                              f"/{len(self.events):08d}.json", encode(record), EVENT_BYTES)
            self.fs.write_new(self._event_path(len(self.events)), encode(record), EVENT_BYTES)
        except BaseException:
            self.damage = "Interrupted append; preserve anchor and journal"
            raise
        self.events.append(record)
        return record

    def put_object(self, body):
        require(not self.inspect_only, "Historical inspection is read-only")
        require(not self.damage, "Damaged journal cannot accept objects")
        descriptor = dict(path="objects/" + sha(body) + ".bin", sha256=sha(body), bytes=len(body))
        path = self.prefix + "/" + descriptor["path"]
        try:
            existing = self.fs.read(path, BODY_BYTES)
        except FileNotFoundError:
            self.fs.write_new(path, body, BODY_BYTES)
        else:
            require(existing == body, "Previously stored object changed")
        return descriptor

    def read_object(self, descriptor):
        require(set(descriptor) == {"path", "sha256", "bytes"} and
                descriptor["path"] == "objects/" + descriptor["sha256"] + ".bin",
                "Object descriptor/path")
        body = self.fs.read(self.prefix + "/" + descriptor["path"], BODY_BYTES)
        require(len(body) == descriptor["bytes"] and sha(body) == descriptor["sha256"], "Object hash/size")
        return body

    def snapshot(self):
        counters = dict(logical_requests=0, attempts=0, response_bytes=0, source_rows=0,
                        intervals=0, interval_runs=0, sessions=0, elapsed_ms=0,
                        unknown_row_responses=0)
        states = {key: dict(state="unqueried", latest_attempt=None, last_successful_source_check=None,
                           latest_source_observation=None, latest_eligible_daily_date=None,
                           complete=None, runs=0) for key in self.tasks}
        attempts, logical, intervals, metadata = {}, set(), set(), {}
        for event in self.events:
            data, kind = event["data"], event["kind"]
            counters["elapsed_ms"] = max(counters["elapsed_ms"], data.get("elapsed_ms", 0))
            if kind == "session":
                counters["sessions"] += 1
            elif kind == "metadata":
                metadata[data["stream_id"]] = data["view"]
            elif kind == "run":
                key = data["interval_key"]
                intervals.add(key)
                counters["interval_runs"] += 1
                states[key].update(state="in_progress", runs=data["run"], run_sequence=event["sequence"])
            elif kind == "reserved":
                attempts[data["attempt_key"]] = {**data, "state": "reserved", "at": event["at"],
                                                 "reserved_at": event["at"]}
                logical.add(data["logical_key"])
                counters["attempts"] += 1
                if data["interval_key"]:
                    states[data["interval_key"]]["latest_attempt"] = event["at"]
            elif kind in ("started", "received", "failure"):
                attempt = attempts[data["attempt_key"]]
                attempt.update(state=kind, at=event["at"])
                if kind == "received":
                    attempt.update(data)
                    counters["response_bytes"] += data["response_bytes"]
                    counters["source_rows"] += data["source_rows"] or 0
                    counters["unknown_row_responses"] += int(data["source_rows"] is None)
                elif kind == "failure":
                    attempt.update(data)
                    if attempt["interval_key"]:
                        states[attempt["interval_key"]]["state"] = "request_failed"
            elif kind == "held":
                states[data["interval_key"]].update(state="held", reason=data["reason"])
            elif kind == "sealed":
                states[data["interval_key"]].update(
                    state=data["state"], complete=data, last_successful_source_check=data["checked_at"],
                    latest_source_observation=data["latest_source_observation"])
            elif kind == "daily_evidence":
                states[data["interval_key"]]["latest_eligible_daily_date"] = data["date"]
        counters["logical_requests"], counters["intervals"] = len(logical), len(intervals)
        for state in states.values():
            if state["state"] == "in_progress" and state["run_sequence"] < self.open_sequence:
                state["state"] = "incomplete"
        return dict(counters=counters, intervals=states, attempts=attempts, metadata=metadata,
                    roster=self.binding["roster"], damage=self.damage,
                    authoritative_for_publication=False)

    def elapsed(self):
        wall = parse_utc(self.now())
        require(wall >= self.open_wall, "Wall clock moved backwards")
        if self.events:
            require(wall >= parse_utc(self.events[-1]["at"]), "Restart clock reversal")
        first = parse_utc(self.events[0]["at"]) if self.events else self.open_wall
        return max(int((wall - first).total_seconds() * 1000),
                   self.base_elapsed + int((self.monotonic() - self.open_mono) * 1000))

    def check_budget(self, increments=None):
        counts = self.snapshot()["counters"]
        if counts["unknown_row_responses"] != 0:
            raise UnknownSourceRowCount("Unknown source row count requires review")
        counts["elapsed_ms"] = self.elapsed()
        for key, limit in self.binding["budgets"].items():
            require(counts[key] + (increments or {}).get(key, 0) <= limit, "Budget exhausted: " + key)
        return counts["elapsed_ms"]

    def session(self):
        elapsed = self.check_budget({"sessions": 1})
        self._append("session", dict(elapsed_ms=elapsed))

    def metadata(self, stream_id, claims, checked_at):
        require(stream_id in self.binding["selected_ids"], "Metadata outside selection")
        view = metadata_view(self.binding["roster"][stream_id], claims, checked_at=checked_at,
                             now=self.now(), scientific_sha256=self.binding["metadata_bindings"][stream_id],
                             dictionary_sha256=self.binding["dictionary_sha256"])
        self._append("metadata", dict(stream_id=stream_id, view=view))
        return view

    def start_run(self, key, *, recheck=False):
        require(key in self.tasks, "Unplanned interval")
        state = self.snapshot()["intervals"][key]
        if self.binding["mode"] == "campaign_reviewed_adapter":
            require(not recheck and not state["complete"], "Campaign seals are immutable; use fresh authorized state")
            require(not any(a["interval_key"] == key for a in self.snapshot()["attempts"].values()),
                    "Unsealed spent attempt requires operator review; retries are zero")
        require(not state["complete"] or recheck, "Completed interval must be reused")
        elapsed = self.check_budget({"intervals": int(state["runs"] == 0)})
        run = state["runs"] + 1
        self._append("run", dict(interval_key=key, run=run, elapsed_ms=elapsed))
        return run

    def reserve(self, task_key, cursor, *, interval_key=None, run=0):
        witness = self.binding["mode"] == "authority_witness_adapter"
        if witness:
            require(interval_key is None and run == 0 and task_key in self.binding["witness_requests"] and
                    cursor == self.binding["witness_requests"][task_key]["request_id"], "Exact witness reservation required")
            require(self.binding["collector_sources"] == source_binding(), "Witness source binding changed")
        elif interval_key is not None:
            require(interval_key in self.tasks and task_key == interval_key and
                    run == self.snapshot()["intervals"][interval_key]["runs"] and run > 0,
                    "Attempt interval/run binding")
        else:
            allowed = {"unit-vocabulary"} | {"metadata-" + sid for sid in self.binding["selected_ids"]}
            require(task_key in allowed and run == 0, "Metadata task outside selection")
        require(isinstance(cursor, str) and len(cursor) <= 128, "Page/cursor identity")
        logical_key = digest(dict(task=task_key, run=run, cursor=cursor))
        attempts = self.snapshot()["attempts"]
        recent = list(attempts.values())[-2:]
        require(len(recent) < 2 or not all(a.get("service_failure") for a in recent),
                "Persistent service-failure circuit open")
        prior = [a for a in attempts.values() if a["logical_key"] == logical_key]
        if witness:
            require(not prior and all(a["state"] == "received" and a.get("status") == 200 and
                    a.get("details", {}).get("error_code") is None for a in attempts.values()),
                    "Witness spent/ambiguous attempt requires review; retries are zero")
            starts = [e for e in self.events if e["kind"] == "started"]
            require(not starts or (parse_utc(self.now()) - parse_utc(starts[-1]["at"])).total_seconds() >= 1,
                    "Witness request-start spacing")
            require(len(self.events) + 6 <= MAX_PLAN, "Journal capacity before witness dispatch")
        if self.binding["mode"] == "campaign_reviewed_adapter":
            require(interval_key is not None and not prior, "Campaign permits observations only and zero retries")
            require(not any(a.get("status") == 429 or a.get("service_failure") or
                        a.get("details", {}).get("error_code") in ("transport", "deadline")
                        for a in attempts.values()), "Campaign paused after service/transport failure")
            require(len([a for a in attempts.values() if a["interval_key"] == interval_key]) < 3,
                    "Campaign page ceiling")
            # Reserve sufficient ledger capacity for started, received, failure/hold
            # or seal; never dispatch with an already exhausted event ledger.
            require(len(self.events) + 6 <= MAX_PLAN, "Journal capacity before dispatch")
        require(len(prior) < 2, "Two-attempt page ceiling")
        elapsed = self.check_budget({"attempts": 1, "logical_requests": int(not prior)})
        ordinal = len(prior) + 1
        attempt_key = digest(dict(task=task_key, page=logical_key, attempt=ordinal))
        self._append("reserved", dict(attempt_key=attempt_key, logical_key=logical_key,
                                     ordinal=ordinal, task_key=task_key, interval_key=interval_key,
                                     run=run, cursor=cursor, elapsed_ms=elapsed))
        return attempt_key

    def started(self, key):
        require(self.snapshot()["attempts"][key]["state"] == "reserved", "Attempt not reserved")
        self._append("started", dict(attempt_key=key))

    def received(self, key, body, *, source_rows, status=200, retain=True,
                 sanitized_body=None, details=None):
        require(self.snapshot()["attempts"][key]["state"] == "started", "Attempt not started")
        require(source_rows is None or (type(source_rows) is int and source_rows >= 0), "Response row count")
        extra = {}
        if sanitized_body is not None or details is not None:
            from .d3_plan import validate_receipt_details
            witness = self.binding["mode"] == "authority_witness_adapter"
            validate_receipt_details(details, witness=witness)
            require(not witness or sanitized_body is None, "Witness requires original response")
            require(self.binding["mode"] in ("d3_explicit_adapter", "campaign_reviewed_adapter", "authority_witness_adapter"),
                    "Provider receipt extension only")
            require(sanitized_body is None or (not retain and
                    self.snapshot()["attempts"][key]["interval_key"] is None and
                    isinstance(sanitized_body, bytes)), "Sanitized metadata only")
            extra = dict(details=details, representation="sanitized" if sanitized_body is not None
                         else "original" if retain else "omitted")
        # Count actual received bytes even when too large; never refund them.
        descriptors = [] if len(body) > BODY_BYTES or not retain else [self.put_object(body)]
        if sanitized_body is not None and len(body) <= BODY_BYTES:
            descriptors = [self.put_object(sanitized_body)]
        self._append("received", dict(attempt_key=key, response_bytes=len(body),
                                     source_rows=source_rows, status=status, objects=descriptors,
                                     response_sha256=sha(body), body_retained=bool(descriptors),
                                     elapsed_ms=self.elapsed(), **extra))
        require(len(body) <= BODY_BYTES, "Response exceeds per-body ceiling")
        self.check_budget()
        return descriptors[0] if descriptors else None

    def failed(self, key, status=None):
        require(self.snapshot()["attempts"][key]["state"] in ("reserved", "started", "received"),
                "Attempt already terminal")
        self._append("failure", dict(attempt_key=key, status=status,
                                    service_failure=status == 429 or
                                    (isinstance(status, int) and status >= 500)))

    def hold(self, key, reason):
        require(key in self.tasks and reason in ("transport_or_parse", "metadata", "interrupted"),
                "Hold classification")
        self._append("held", dict(interval_key=key, reason=reason))

    def seal(self, key, run, envelope, attempt_keys):
        """Only a complete single-run source envelope may advance coverage."""
        state = self.snapshot()
        task = self.tasks[key]
        require(run == state["intervals"][key]["runs"] and attempt_keys, "Seal run")
        attempts = [state["attempts"][a] for a in attempt_keys]
        require(len(set(attempt_keys)) == len(attempt_keys) and all(
            a["interval_key"] == key and a["run"] == run and a["state"] == "received" and
            a["status"] == 200 and len(a["objects"]) == 1 for a in attempts), "Seal receipt closure")
        require(envelope["query_complete"] is True and
                envelope["datastream_id"] == task["identity"]["stream_id"] and
                envelope["requested_interval"] == dict(start_inclusive=task["start"], end_exclusive=task["end"])
                and envelope["page_count"] == len(attempts), "Incomplete/mismatched envelope")
        # Independently bind parsed pages to received raw bytes. The production
        # parser is replayed offline by the caller; hashes are not semantic proof.
        pages = envelope["pages"]
        require(len(pages) == len(attempts), "Page count differs from receipt closure")
        cursor, end = parse_utc(task["start"]), parse_utc(task["end"])
        source_rows = []
        for page, attempt in zip(pages, attempts):
            require(page["response_sha256"] == attempt["objects"][0]["sha256"] and
                    page["response_bytes"] == attempt["objects"][0]["bytes"], "Page/receipt hash")
            require(parse_utc(page["requested_at_utc"]) <= parse_utc(attempt["reserved_at"]) <=
                    parse_utc(attempt["at"]) <= parse_utc(page["retrieved_at_utc"]) <=
                    parse_utc(self.now()), "Receipt retrieval clock")
            raw = decode(self.read_object(attempt["objects"][0]))
            require(type(raw.get("limit")) is int and 0 < raw["limit"] <= 2016 and
                    isinstance(raw.get("data"), list) and len(raw["data"]) <= raw["limit"],
                    "Effective response limit")
            require(parse_utc(attempt["cursor"]) == cursor, "Page cursor/receipt mismatch")
            previous = cursor
            for row in raw["data"]:
                stamp = parse_utc(row["t"])
                require(previous <= stamp < end and
                        row.get("datastream_id", task["identity"]["stream_id"]) == task["identity"]["stream_id"],
                        "Source time/identity mismatch")
                previous = stamp
            source_rows.extend(raw["data"])
            if page is not pages[-1]:
                require(len(raw["data"]) == raw["limit"] and previous > cursor,
                        "Nonadvancing/incomplete pagination")
                cursor = previous
        require(envelope["diagnostics"]["completion_reason"] in
                ("empty_page", "short_page_with_effective_limit"), "No completion proof")
        final = decode(self.read_object(attempts[-1]["objects"][0]))
        require(type(final.get("limit")) is int and 0 < final["limit"] <= 2016 and
                len(final["data"]) < final["limit"], "Full final page cannot seal")
        rows, diagnostics = normalize_rows(source_rows, task["start"], task["end"])
        require(rows == envelope["rows"] and envelope["content_sha256"] == _content_hash(envelope)
                and all(envelope["diagnostics"].get(k) == v for k, v in diagnostics.items()),
                "Normalized envelope differs from exact received pages")
        require(envelope["latest_observation_utc"] == (rows[-1]["t"] if rows else None) and
                envelope["retrieval_first_utc"] == pages[0]["retrieved_at_utc"] and
                envelope["retrieval_last_utc"] == pages[-1]["retrieved_at_utc"] and
                parse_utc(envelope["retrieval_last_utc"]) <= parse_utc(self.now()),
                "Observation/retrieval clocks")
        self.check_budget()
        obj = self.put_object(encode(envelope))
        result = dict(interval_key=key, run=run, state="complete_nonempty" if envelope["rows"] else "complete_empty",
                      checked_at=envelope["retrieval_last_utc"],
                      latest_source_observation=envelope["latest_observation_utc"],
                      attempt_keys=attempt_keys, objects=[obj])
        self._append("sealed", result)
        return result

    def completed(self, key):
        result = self.snapshot()["intervals"][key]["complete"]
        if result:
            return decode(self.read_object(result["objects"][0]))
        return None

    def daily_evidence(self, key, date, evidence_sha256):
        from datetime import date as civil_date
        from .model import HASH
        require(civil_date.fromisoformat(date).isoformat() == date, "Civil date required")
        task = self.tasks[key]
        require(self.completed(key) is not None and
                task["identity"]["unit_status"] == "verified_percent_conversion" and
                task["start"][:10] <= date < task["end"][:10] and HASH.fullmatch(evidence_sha256),
                "Daily evidence requires resolved complete input")
        self._append("daily_evidence", dict(interval_key=key, date=date, evidence_sha256=evidence_sha256,
                                           acceptance="externally_supplied_not_verified_here"))
