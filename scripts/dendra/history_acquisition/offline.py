"""Synthetic/saved-byte transport only. There is intentionally no live adapter."""
import io
import urllib.error
import urllib.parse

from ..transport import DendraFetcher, parse_utc
from .safety import Hold, require, decode


class OfflineOnly(Hold):
    pass


class Response(io.BytesIO):
    status = 200
    headers = {}


class ReplayTransport:
    """Finite in-memory response sequence; cannot accept callbacks or URLs."""
    def __init__(self, responses):
        require(isinstance(responses, (list, tuple)) and len(responses) <= 80, "Replay response bound")
        require(all(isinstance(r, bytes) or type(r) is int for r in responses), "Bytes/status only")
        self.responses = list(responses)
        self.calls = 0

    def take(self):
        require(self.responses, "Replay exhausted; no network fallback")
        self.calls += 1
        value = self.responses.pop(0)
        if type(value) is int:
            require(400 <= value <= 599, "Synthetic HTTP failure status")
            raise urllib.error.HTTPError("offline:", value, "synthetic failure", {}, None)
        return value


def screen(value):
    """Fail closed on restricted metadata accidentally mixed into raw evidence."""
    if isinstance(value, dict):
        forbidden = {"geo", "geometry", "coordinates", "authorization", "cookie", "token", "secret"}
        require(not forbidden.intersection(k.lower() for k in value), "Restricted raw metadata")
        require(value.get("is_hidden") is not True, "Hidden raw metadata")
        for item in value.values():
            screen(item)
    elif isinstance(value, list):
        for item in value:
            screen(item)


def collect(journal, key, *, transport=None, recheck=False):
    """Replay one interval. Real network transport cannot be selected here."""
    if type(transport) is not ReplayTransport:
        raise OfflineOnly("Only explicit ReplayTransport is supported; live transport is not implemented")
    require(not journal.damage, "Recovery is read-only")
    require(key in journal.tasks, "Unplanned interval")
    existing = journal.completed(key)
    task = journal.tasks[key]
    sid = task["identity"]["stream_id"]
    view = journal.snapshot()["metadata"].get(sid)
    fresh = view and 0 <= (parse_utc(journal.now()) - parse_utc(view["checked_at"])).total_seconds() <= 86400
    if not fresh or not view["raw_eligible"]:
        journal.hold(key, "metadata")
        raise Hold("Fresh complete public identity required")
    if existing is not None and not recheck:
        return dict(cache_hit=True, envelope=existing)
    journal.session()
    run = journal.start_run(key, recheck=recheck)
    successful = []

    def open_replay(request, timeout):
        parsed = urllib.parse.urlsplit(request.full_url)
        params = urllib.parse.parse_qs(parsed.query)
        require(parsed.scheme == "https" and parsed.netloc == "api.dendra.science" and
                parsed.path == "/v2/datapoints" and
                set(params) == {"datastream_id", "time[$gte]", "time[$lt]", "$sort[time]", "$limit"} and
                all(len(v) == 1 for v in params.values()) and
                params["datastream_id"] == [sid] and params["time[$lt]"] == [task["end"]] and
                params["$sort[time]"] == ["1"] and params["$limit"] == ["2016"],
                "Unexpected replay request")
        cursor = params["time[$gte]"][0]
        attempt = journal.reserve(key, cursor, interval_key=key, run=run)
        journal.started(attempt)
        body, accounted, row_count = None, False, None
        try:
            body = transport.take()
            require(len(body) <= 8 * 1024**2, "Response byte ceiling")
            payload = decode(body)
            rows = payload.get("data", [])
            require(isinstance(rows, list), "Data array required")
            row_count = len(rows)
            screen(payload)
            require(all(isinstance(row, dict) and row.get("datastream_id", sid) == sid for row in rows),
                    "Observation outside selected stream")
            accounted = True
            journal.received(attempt, body, source_rows=len(rows))
            successful.append(attempt)
            return Response(body)
        except BaseException as exc:
            if body is not None and not accounted and not journal.damage:
                journal.received(attempt, body, source_rows=row_count, retain=False)
            if journal.damage:
                raise
            journal.failed(attempt, getattr(exc, "code", None))
            raise

    # Reuse accepted paging/normalization without invoking its default opener.
    # No sleep or live-clock wait is performed by offline replay.
    fetcher = DendraFetcher(opener=open_replay, timeout=25, max_attempts=2, max_pages=20,
                            page_size=2016, max_retry_delay=15,
                            now_fn=lambda: parse_utc(journal.now()), sleep_fn=lambda _: None)
    try:
        envelope = fetcher.fetch_interval(sid, task["start"], task["end"])
        journal.seal(key, run, envelope, successful)
        return dict(cache_hit=False, envelope=envelope)
    except Exception:
        journal.hold(key, "transport_or_parse")
        raise
