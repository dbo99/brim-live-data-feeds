#!/usr/bin/env python3
"""Default-off Gmail read adapter and Delta builder orchestration.

Sender eligibility requires the approved From identity and independently verified
full-body DKIM. Message-carried authentication claims confer no trust. DKIM proves
signing-domain responsibility, not a human author or an unforwarded delivery path.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
import hashlib
import http.client
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import re
import signal
import threading
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from delta_ops_email import (Delivery, MAX_MESSAGE_BYTES, PUBLIC_ATTRIBUTION,
                             ReportError, _outside_repository, inspect_delivery,
                             normalize_values, parse_message, parse_report, select_source)

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
PDF_URL = ("https://water.ca.gov/-/media/DWR-Website/Web-Pages/Programs/State-Water-Project/"
           "Operations-And-Maintenance/Files/Operations-Control-Office/Delta-Status-And-Operations/"
           "Delta-Operations-Daily-Summary.pdf")
PAGE_SIZE, MAX_PAGES, MAX_MESSAGES = 10, 3, 24
MAX_HTTP_BYTES, MAX_AGGREGATE_BYTES, MAX_REQUESTS = 3 * 1024**2, 16 * 1024**2, 60
REQUEST_SECONDS, RUN_SECONDS, MAX_RETRIES = 15, 90, 1
ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
STATES = {"UNCONFIGURED", "RETRIEVAL_UNAVAILABLE", "COMPLETE_VALID_EMPTY",
          "MESSAGE_REJECTED", "TRUST_UNVERIFIED", "SCAN_INCOMPLETE", "VERIFIED_ELIGIBLE"}
REPO = Path(__file__).resolve().parents[1]
APPROVED_SENDER = "DWR@public.govdelivery.com"
SIGNING_DOMAIN = b"public.govdelivery.com"
AUTH_METHOD = "dkim-rsa-sha256-full-body"


class ApiError(ValueError):
    """Safe fixed code, never a raw exception or response."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def json_object(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ApiError("INVALID_JSON")
            result[key] = value
        return result
    def invalid(_):
        raise ApiError("INVALID_JSON")
    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
        if not isinstance(result, dict):
            raise ApiError("INVALID_JSON")
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise ApiError("INVALID_JSON") from None


@contextmanager
def request_deadline(seconds):
    # The supported Mac/Linux foreground runner uses a process-local alarm to
    # bound DNS/TLS/header reads as well as socket reads. A socket timeout alone
    # does not place a wall-clock ceiling on DNS resolution or a slow response.
    if (not hasattr(signal, "setitimer") or threading.current_thread() is not threading.main_thread()
            or signal.getitimer(signal.ITIMER_REAL)[0] != 0):
        raise ApiError("TIMEOUT_GUARD_UNAVAILABLE")
    previous = signal.getsignal(signal.SIGALRM)
    def expired(*_):
        raise ApiError("REQUEST_TIMEOUT")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class StrictHTTPResponse(http.client.HTTPResponse):
    """Require chunk CRLFs and a complete trailer terminator, including at EOF."""

    def _read_next_chunk_size(self):
        line = self.fp.readline(65537)
        if len(line) > 65536 or not re.fullmatch(br"[0-9A-Fa-f]+(?:;[^\r\n]*)?\r\n", line):
            raise http.client.IncompleteRead(b"")
        return int(line.split(b";", 1)[0].strip(), 16)

    def _get_chunk_left(self):
        left = self.chunk_left
        if left is None or left == 0:
            if left == 0 and self._safe_read(2) != b"\r\n":
                raise http.client.IncompleteRead(b"")
            try:
                left = self._read_next_chunk_size()
            except ValueError:
                raise http.client.IncompleteRead(b"") from None
            if left < 0:
                raise http.client.IncompleteRead(b"")
            if left == 0:
                self._read_and_discard_trailer()
                self._close_conn()
                left = None
            self.chunk_left = left
        return left

    def _read_and_discard_trailer(self):
        for _ in range(100):
            line = self.fp.readline(65537)
            if line == b"\r\n":
                return
            if len(line) > 65536 or not line.endswith(b"\r\n") or b":" not in line:
                raise http.client.IncompleteRead(b"")
        raise http.client.IncompleteRead(b"")


def retry_delay(values, now):
    if isinstance(values, str):
        values = [values] if values else []
    if not values:
        return 1
    if len(values) != 1:
        raise ApiError("RETRY_AFTER_AMBIGUOUS")
    value = values[0].strip()
    if value.isascii() and value.isdecimal():
        delay = int(value) if len(value) < 10 else math.inf
    else:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            delay = max(0, math.ceil(when.timestamp() - now))
        except (ValueError, TypeError, OverflowError):
            raise ApiError("RETRY_AFTER_INVALID") from None
    if delay > 2:
        raise ApiError("BACKOFF_REQUIRED")
    return delay


class BoundedHTTP:
    """Fixed HTTPS hosts, no redirects/proxies, bounded bytes/time/attempts."""

    def __init__(self, *, clock=time.monotonic, sleep=time.sleep, wall_clock=time.time):
        self.clock, self.sleep = clock, sleep
        self.wall_clock = wall_clock
        self.deadline = clock() + RUN_SECONDS
        self.requests = self.bytes = 0

    def _one(self, method, url, headers, body, limit):
        parsed = urlsplit(url)
        token = parsed.hostname == "oauth2.googleapis.com" and parsed.path == "/token" and method == "POST" and not parsed.query
        read = (parsed.hostname == "gmail.googleapis.com" and method == "GET" and
                re.fullmatch(r"/gmail/v1/users/me/(?:profile|messages(?:/[A-Za-z0-9_-]{1,128})?)", parsed.path))
        if (parsed.scheme != "https" or parsed.port not in {None, 443} or parsed.username
                or parsed.password or parsed.fragment or not (token or read)):
            raise ApiError("ENDPOINT_REFUSED")
        deadline = min(self.deadline, self.clock() + REQUEST_SECONDS)
        remaining = deadline - self.clock()
        if remaining <= 0 or self.requests >= MAX_REQUESTS:
            raise ApiError("REQUEST_BUDGET")
        self.requests += 1
        connection = http.client.HTTPSConnection(parsed.hostname, timeout=min(5, remaining), context=ssl.create_default_context())
        connection.response_class = StrictHTTPResponse
        try:
            with request_deadline(remaining):
                connection.request(method, parsed.path + (("?" + parsed.query) if parsed.query else ""), body=body, headers=headers)
                response = connection.getresponse()
                lengths = response.headers.get_all("Content-Length", [])
                transfers = response.headers.get_all("Transfer-Encoding", [])
                if (len(lengths) > 1 or len(transfers) > 1 or (lengths and transfers)
                        or (transfers and transfers[0].lower().strip() != "chunked")):
                    raise ApiError("HTTP_FRAMING_INVALID")
                expected = None
                if lengths:
                    if not re.fullmatch(r"[0-9]{1,12}", lengths[0].strip()):
                        raise ApiError("HTTP_FRAMING_INVALID")
                    expected = int(lengths[0])
                    if expected > limit:
                        raise ApiError("RESPONSE_LIMIT")
                chunks, size = [], 0
                while True:
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        raise ApiError("REQUEST_TIMEOUT")
                    if connection.sock is not None:
                        connection.sock.settimeout(min(5, remaining))
                    chunk = response.read1(min(65536, limit + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    self.bytes += len(chunk)
                    if size > limit or self.bytes > MAX_AGGREGATE_BYTES:
                        raise ApiError("RESPONSE_LIMIT")
                    chunks.append(chunk)
                if expected is not None and size != expected:
                    raise ApiError("HTTP_PREMATURE_EOF")
                return response.status, response.headers.get_all("Retry-After", []), b"".join(chunks)
        except ApiError:
            raise
        except (OSError, http.client.HTTPException, ValueError):
            raise ApiError("TRANSPORT_UNAVAILABLE") from None
        finally:
            connection.close()

    def request(self, method, url, *, headers=None, form=None, limit=MAX_HTTP_BYTES):
        if not 0 < limit <= MAX_HTTP_BYTES:
            raise ApiError("RESPONSE_LIMIT")
        headers = dict(headers or {})
        headers["Accept"] = "application/json"
        headers["Accept-Encoding"] = "identity"
        body = None
        if form is not None:
            body = urlencode(form).encode("ascii")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        for attempt in range(1 + (MAX_RETRIES if method == "GET" else 0)):
            try:
                status, retry_after, raw = self._one(method, url, headers, body, limit)
            except ApiError as error:
                if error.code not in {"TRANSPORT_UNAVAILABLE", "REQUEST_TIMEOUT"} or method != "GET" or attempt == MAX_RETRIES:
                    raise
                status, retry_after, raw = 503, "", b""
            if status == 200:
                return json_object(raw)
            retryable = status in {429, 500, 502, 503, 504}
            if not retryable or method != "GET" or attempt == MAX_RETRIES:
                code = {400: "TOKEN_REFUSED", 401: "AUTH_EXPIRED", 403: "ACCESS_REFUSED", 429: "QUOTA_LIMIT"}.get(status, "API_UNAVAILABLE")
                raise ApiError(code)
            delay = retry_delay(retry_after, self.wall_clock())
            if self.clock() + delay >= self.deadline:
                raise ApiError("REQUEST_BUDGET")
            self.sleep(delay)
        raise ApiError("API_UNAVAILABLE")


@dataclass(frozen=True, repr=False)
class Config:
    enabled: bool = False
    client_id: str = field(default="", repr=False)
    client_secret: str = field(default="", repr=False)
    refresh_token: str = field(default="", repr=False)
    expected_account: str = field(default="", repr=False)
    expected_sender: str = field(default="", repr=False)

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("DELTA_OPS_GMAIL_ENABLED") == "true",
                   *[os.environ.get("DELTA_OPS_GMAIL_" + key, "") for key in
                     ["CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN", "EXPECTED_ACCOUNT", "EXPECTED_SENDER"]])

    def configured(self):
        return self.enabled and all([self.client_id, self.client_secret, self.refresh_token,
                                     self.expected_account, self.expected_sender])


def validate_access_token(value):
    # Require the provider's granted scope, including for refreshed credentials.
    # An absent scope is not evidence that an existing grant is read-only.
    scope, token_type = value.get("scope"), value.get("token_type")
    if not isinstance(scope, str) or scope.split() != [SCOPE]:
        raise ApiError("SCOPE_UNVERIFIED")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise ApiError("TOKEN_INVALID")
    token, lifetime = value.get("access_token"), value.get("expires_in")
    if (not isinstance(token, str) or not 1 <= len(token) <= 4096 or any(c.isspace() for c in token)
            or isinstance(lifetime, bool) or not isinstance(lifetime, (int, float)) or not 60 <= lifetime <= 86400):
        raise ApiError("TOKEN_INVALID")
    return token


def verify_profile(http, token, expected_account):
    profile = http.request("GET", API + "/profile", headers={"Authorization": "Bearer " + token}, limit=16384)
    if not isinstance(profile.get("emailAddress"), str) or profile["emailAddress"].casefold() != expected_account.casefold():
        raise ApiError("WRONG_MAILBOX")


def verification_modules():
    try:
        from importlib.metadata import version
        if version("dkimpy") != "1.1.8" or version("dnspython") != "2.9.0":
            raise ImportError()
        import dkim
        import dns.query
        import dns.resolver
        return dkim, dns
    except Exception:
        raise ApiError("VERIFIER_DEPENDENCIES_UNAVAILABLE") from None


def key_name(name):
    try:
        name = name.decode("ascii") if isinstance(name, bytes) else name
        name = name.rstrip(".").lower()
        suffix = "._domainkey." + SIGNING_DOMAIN.decode()
        selector = name[:-len(suffix)] if name.endswith(suffix) else ""
        if (not 1 <= len(selector) <= 128 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
                                                   for part in selector.split("."))):
            raise ValueError()
        return name
    except (ValueError, TypeError, UnicodeError, AttributeError):
        raise ApiError("DKIM_SELECTOR_REJECTED") from None


class KeyResolver:
    """One run's system-recursive DNS cache; counts each UDP/TCP send explicitly.

    Only approved selector TXT questions leave the process. Recursive delegation
    is performed by the configured resolver, not an arbitrary URL/name fetcher.
    """
    def __init__(self, *, deadline=None, clock=time.monotonic):
        _, self.dns = verification_modules()
        self.clock = clock
        self.deadline = min(deadline if deadline is not None else math.inf, clock() + 40)
        try:
            resolver = self.dns.resolver.Resolver(configure=True)
            self.servers = [str(ipaddress.ip_address(server)) for server in resolver.nameservers
                            if isinstance(server, str)]
        except (self.dns.exception.DNSException, OSError, ValueError):
            raise ApiError("DNS_RESOLVER_UNAVAILABLE") from None
        if not self.servers:
            raise ApiError("DNS_RESOLVER_UNAVAILABLE")
        self.cache = {}
        self.queries = 0
        self.query_seconds = 0.0

    def _query(self, question, server, tcp=False):
        remaining = self.deadline - self.clock()
        if self.queries >= 16 or remaining <= 0:
            raise ApiError("DNS_BUDGET")
        timeout = min(5, remaining)
        self.queries += 1
        start = self.clock()
        try:
            with request_deadline(timeout):
                if tcp:
                    return self.dns.query.tcp(question, server, timeout=timeout)
                return self.dns.query.udp(question, server, timeout=timeout, raise_on_truncation=True)
        finally:
            self.query_seconds += self.clock() - start

    def __call__(self, name, timeout=5):
        name = key_name(name)
        if name in self.cache:
            return self.cache[name]
        if len(self.cache) >= 8:
            raise ApiError("DNS_SELECTOR_BUDGET")
        self.cache[name] = None  # Negative/error results also cache once per run.
        question = self.dns.message.make_query(name + ".", self.dns.rdatatype.TXT)
        for server in self.servers[:2]:
            try:
                try:
                    answer = self._query(question, server)
                except self.dns.message.Truncated:
                    answer = self._query(question, server, tcp=True)
                if answer.rcode() != self.dns.rcode.NOERROR:
                    return None
                rrset = answer.resolve_chaining().answer
                if rrset is None or rrset.rdtype != self.dns.rdatatype.TXT or len(rrset) != 1:
                    return None
                key = b"".join(next(iter(rrset)).strings)
                if not key or len(key) > 8192:
                    return None
                self.cache[name] = key
                return key
            except ApiError:
                raise
            except (self.dns.exception.DNSException, OSError, ValueError):
                continue
        return None


class SavedKeys:
    """Process-private, single-run DNS evidence; never a runtime permanent pin."""
    def __init__(self, cache):
        if not isinstance(cache, dict) or len(cache) > 8:
            raise ApiError("KEY_EVIDENCE_INVALID")
        self.cache = {}
        for name, value in cache.items():
            if value is not None and (not isinstance(value, bytes) or len(value) > 8192):
                raise ApiError("KEY_EVIDENCE_INVALID")
            self.cache[key_name(name)] = value

    def __call__(self, name, timeout=5):
        return self.cache.get(key_name(name))


def qualifying_signatures(raw):
    dkim, _ = verification_modules()
    if not isinstance(raw, bytes) or len(raw) > MAX_MESSAGE_BYTES:
        raise ApiError("MESSAGE_SIZE_LIMIT")
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    required = {"from", "subject", "content-type", "mime-version"}
    if parsed.get_all("Content-Transfer-Encoding"):
        required.add("content-transfer-encoding")
    critical = required | {"sender", "reply-to", "content-disposition"}
    if (parsed.defects or any(getattr(value, "defects", ()) for _, value in parsed.items())
            or any(len(parsed.get_all(name, [])) != 1 for name in required)
            or any(len(parsed.get_all(name, [])) > 1 for name in critical)
            or any(name.lower().startswith("resent-") for name in parsed.keys())
            or any(part.defects for part in parsed.walk())):
        raise ApiError("SIGNED_MIME_AMBIGUOUS")
    senders = getaddresses([str(parsed["From"])])
    if len(senders) != 1 or senders[0][1] != APPROVED_SENDER:
        raise ApiError("SENDER_CLAIM_REJECTED")
    logger = logging.Logger("delta-dkim-private", level=logging.CRITICAL + 1)
    logger.addHandler(logging.NullHandler())
    verifier = dkim.DKIM(raw, minkey=1024, timeout=5, logger=logger)
    signatures = [(n, v) for n, v in verifier.headers if n.lower() == b"dkim-signature"]
    if len(signatures) > 8:
        raise ApiError("SIGNATURE_COUNT_LIMIT")
    candidates = []
    reason = "NO_QUALIFYING_SIGNATURE"
    for index, (_, value) in enumerate(signatures):
        try:
            tags = dkim.parse_tag_value(value)
            if tags.get(b'd', b'').lower() != SIGNING_DOMAIN:
                continue
            dkim.validate_signature_fields(tags)
            covered = {x.strip().lower().decode("ascii") for x in tags[b'h'].split(b':')}
            if tags[b'a'] != b'rsa-sha256' or b'l' in tags:
                reason = "DKIM_ALGORITHM_OR_BODY_LIMIT_REJECTED"
                continue
            if not required <= covered:
                reason = "DKIM_REQUIRED_HEADER_UNSIGNED"
                continue
            name = key_name(tags[b's'] + b'._domainkey.' + tags[b'd'])
            candidates.append((index, name, tags))
        except (dkim.DKIMException, dkim.InvalidTagValueList, ValueError, UnicodeError, KeyError):
            reason = "DKIM_SIGNATURE_INVALID"
    if len(candidates) > 4:
        raise ApiError("SIGNATURE_COUNT_LIMIT")
    return verifier, candidates, reason


def receiver_verdict(raw, expected_sender=APPROVED_SENDER, *, keys=None):
    """Verify signer responsibility; never trust receiver/ARC header claims."""
    try:
        if expected_sender != APPROVED_SENDER:
            raise ApiError("SENDER_CONFIG_REJECTED")
        verifier, candidates, reason = qualifying_signatures(raw)
        keys = keys if keys is not None else KeyResolver()
        dkim, _ = verification_modules()
        for index, name, tags in candidates:
            key = keys(name.encode() + b".")
            if not key:
                reason = "DKIM_KEY_UNAVAILABLE"
                continue
            try:
                key_tags = dkim.parse_tag_value(key)
                # Enforce key restrictions not fully enforced by dkimpy 1.1.8.
                hashes = key_tags.get(b'h', b'sha256').lower().split(b':')
                services = key_tags.get(b's', b'*').lower().split(b':')
                flags = key_tags.get(b't', b'').lower().split(b':')
                if (b'sha256' not in hashes or not {b'*', b'email'} & set(services)
                        or b'y' in flags or key_tags.get(b'k', b'rsa') != b'rsa'):
                    reason = "DKIM_KEY_POLICY_REJECTED"
                    continue
                if b's' in flags and tags.get(b'i', b'@' + SIGNING_DOMAIN).rsplit(b'@', 1)[-1].lower() != SIGNING_DOMAIN:
                    reason = "DKIM_KEY_POLICY_REJECTED"
                    continue
                # dkimpy checks RSA signature, full body hash, canonicalization,
                # signature fields and minimum 1024-bit RSA key size.
                remaining = getattr(keys, "deadline", time.monotonic() + 5) - time.monotonic()
                if remaining <= 0:
                    raise ApiError("VERIFICATION_BUDGET")
                with request_deadline(min(5, remaining)):
                    if verifier.verify(idx=index, dnsfunc=SavedKeys({name: key})):
                        return True, "DKIM_VERIFIED"
                reason = "DKIM_SIGNATURE_INVALID"
            except (dkim.DKIMException, dkim.InvalidTagValueList, ValueError, TypeError, KeyError):
                reason = "DKIM_SIGNATURE_INVALID"
        return False, reason
    except ApiError as error:
        return False, error.code
    except Exception:
        return False, "DKIM_INPUT_INVALID"


@dataclass(frozen=True, repr=False)
class VerifiedMessage:
    raw: bytes
    keys: dict
    record_json: str

    def record(self):
        record = parse_message(self.raw)
        record["source_authentication"] = AUTH_METHOD
        if json.dumps(record, sort_keys=True) != self.record_json:
            raise ApiError("VERIFIED_RECORD_BINDING")
        trusted, _ = receiver_verdict(self.raw, keys=SavedKeys(self.keys))
        if not trusted:
            raise ApiError("UNVERIFIED_SIGNATURE")
        return record


def load_verified_evidence(path):
    path = private_file(path, maximum=4 * 1024**2)
    value = json_object(path.read_bytes())
    if set(value) != {"created", "raw", "keys", "record_json"} or not 0 <= time.time() - value["created"] <= 600:
        raise ApiError("VERIFICATION_EVIDENCE_EXPIRED")
    raw = base64.b64decode(value["raw"], validate=True)
    keys = {key_name(k): base64.b64decode(v, validate=True) if v is not None else None for k, v in value["keys"].items()}
    verified = VerifiedMessage(raw, keys, value["record_json"])
    verified.record()
    return verified


@dataclass(frozen=True)
class Retrieval:
    state: str
    reason: str
    deliveries: tuple[Delivery, ...] = field(default=(), repr=False)
    listed: int = 0
    fetched: int = 0
    rejected: int = 0
    bindings: tuple[VerifiedMessage, ...] = field(default=(), repr=False)

    def diagnostic(self):
        return {"state": self.state, "reason": self.reason, "listed": self.listed,
                "fetched": self.fetched, "rejected": self.rejected}


def discovery_query(as_of, sender):
    # Search is discovery only. A 16-day receipt window accommodates lagging
    # delivery around the separately enforced 7-day report-heading window.
    if as_of.tzinfo is None or not re.fullmatch(r"[A-Za-z0-9_.+\-]+@[A-Za-z0-9.\-]+", sender):
        raise ApiError("DISCOVERY_CONFIG_INVALID")
    day = as_of.astimezone(ZoneInfo("America/Los_Angeles")).date()
    start = datetime.combine(day - timedelta(days=15), datetime.min.time(), ZoneInfo("America/Los_Angeles"))
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), ZoneInfo("America/Los_Angeles"))
    return f'from:({sender}) subject:("Delta Operations Summary") after:{int(start.timestamp())} before:{int(end.timestamp())}'


def decode_message(value, expected_id):
    raw = value.get("raw")
    if value.get("id") != expected_id or not isinstance(raw, str) or len(raw) > ((MAX_MESSAGE_BYTES + 2) // 3) * 4:
        raise ApiError("MESSAGE_ENVELOPE_INVALID")
    if not re.fullmatch(r"[A-Za-z0-9_-]*={0,2}", raw):
        raise ApiError("MESSAGE_ENCODING_INVALID")
    try:
        data = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
    except ValueError:
        raise ApiError("MESSAGE_ENCODING_INVALID") from None
    if not data or len(data) > MAX_MESSAGE_BYTES:
        raise ApiError("MESSAGE_SIZE_LIMIT")
    return data


def retrieve(config, *, as_of, http=None, keys=None):
    if not config.configured():
        return Retrieval("UNCONFIGURED", "DISABLED_OR_MISSING_CONFIG")
    http = http or BoundedHTTP()
    deliveries, bindings, seen, pages_seen = [], [], set(), set()
    fetched = rejected = 0
    listing_started = False
    try:
        if config.expected_sender != APPROVED_SENDER:
            raise ApiError("SENDER_CONFIG_REJECTED")
        verification_modules()
        keys = keys if keys is not None else KeyResolver(deadline=getattr(http, "deadline", time.monotonic() + RUN_SECONDS))
        query = discovery_query(as_of, config.expected_sender)
        response = http.request("POST", TOKEN_URL, form={"grant_type": "refresh_token", "client_id": config.client_id,
                                "client_secret": config.client_secret, "refresh_token": config.refresh_token}, limit=16384)
        token = validate_access_token(response)
        verify_profile(http, token, config.expected_account)
        headers = {"Authorization": "Bearer " + token}
        page_token = None
        for page in range(MAX_PAGES):
            params = {"q": query, "maxResults": PAGE_SIZE, "includeSpamTrash": "false"}
            if page_token:
                params["pageToken"] = page_token
            value = http.request("GET", API + "/messages?" + urlencode(params), headers=headers, limit=65536)
            listing_started = True
            messages = value.get("messages", [])
            if (not isinstance(messages, list) or len(messages) > PAGE_SIZE
                    or ("messages" not in value and value.get("resultSizeEstimate") != 0)):
                raise ApiError("LIST_INVALID")
            for entry in messages:
                identifier = entry.get("id") if isinstance(entry, dict) else None
                if not isinstance(identifier, str) or not ID.fullmatch(identifier):
                    raise ApiError("LIST_INVALID")
                if identifier in seen:
                    continue
                if len(seen) >= MAX_MESSAGES:
                    raise ApiError("SCAN_LIMIT")
                seen.add(identifier)
                item = http.request("GET", API + "/messages/" + identifier + "?format=raw&fields=id,raw", headers=headers)
                raw = decode_message(item, identifier)
                fetched += 1
                trusted, verification_reason = receiver_verdict(raw, config.expected_sender, keys=keys)
                delivery = inspect_delivery(raw, trusted=trusted)
                if trusted and delivery.record is not None:
                    delivery.record["source_authentication"] = AUTH_METHOD
                    bindings.append(VerifiedMessage(raw, dict(keys.cache), json.dumps(delivery.record, sort_keys=True)))
                rejected += int(delivery.rejected)
                deliveries.append(delivery)
            page_token = value.get("nextPageToken")
            if page_token is None:
                break
            if not isinstance(page_token, str) or not 1 <= len(page_token) <= 2048 or page_token in pages_seen:
                raise ApiError("PAGINATION_INVALID")
            pages_seen.add(page_token)
        else:
            raise ApiError("SCAN_LIMIT")
        if not seen:
            return Retrieval("COMPLETE_VALID_EMPTY", "LIST_COMPLETE")
        eligible = any(d.trusted is True and d.record is not None for d in deliveries)
        state = "VERIFIED_ELIGIBLE" if eligible else "MESSAGE_REJECTED" if rejected == fetched else "TRUST_UNVERIFIED"
        reason = "DKIM_VERIFIED" if eligible else "CONTENT_REJECTED" if rejected == fetched else verification_reason
        return Retrieval(state, reason, tuple(deliveries), len(seen), fetched, rejected, tuple(bindings))
    except ApiError as error:
        # Never select a partially scanned email set: an unseen correction/conflict
        # could invalidate it. Independent PDF recovery is still attempted once.
        return Retrieval("SCAN_INCOMPLETE" if listing_started else "RETRIEVAL_UNAVAILABLE", error.code,
                         listed=len(seen), fetched=fetched, rejected=rejected)


def select_retrieved(batch, pdf_fetch, *, as_of):
    if batch.state not in STATES:
        raise ApiError("RETRIEVAL_STATE_INVALID")
    state = "not_configured" if batch.state == "UNCONFIGURED" else "unavailable" if batch.state in {"RETRIEVAL_UNAVAILABLE", "SCAN_INCOMPLETE"} else "completed"
    deliveries = list(batch.deliveries) if state == "completed" else []
    return select_source(deliveries, pdf_fetch, as_of=as_of, retrieval_state=state)


def child_environment():
    return {key: value for key, value in os.environ.items()
            if not key.startswith(("DELTA_OPS_GMAIL_", "DELTA_OPS_SELECTED_", "DELTA_OPS_DKIM_"))}


def fetch_pdf_record():
    """Call the existing R transport once; its internal timeout/retries are intact."""
    with tempfile.TemporaryDirectory(prefix="delta-pdf-selection-") as folder:
        output = Path(folder) / "source.txt"
        env = child_environment()
        env["DELTA_OPS_PRIVATE_PDF_TEXT"] = str(output)
        env["DELTA_OPS_SUMMARY_PDF_URL"] = os.environ.get("DELTA_OPS_SUMMARY_PDF_URL", PDF_URL)
        expression = ('source("scripts/delta_ops_pdf_transport.R"); '
                      'writeLines(delta_ops_fetch_pdf_text(Sys.getenv("DELTA_OPS_SUMMARY_PDF_URL")), '
                      'Sys.getenv("DELTA_OPS_PRIVATE_PDF_TEXT"), useBytes=TRUE)')
        run = subprocess.run(["Rscript", "-e", expression], cwd=REPO, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        if run.returncode or not output.is_file() or output.stat().st_size > MAX_MESSAGE_BYTES:
            raise ApiError("PDF_UNAVAILABLE")
        record = parse_report(output.read_text(), source_kind="pdf")
        parsed = urlsplit(env["DELTA_OPS_SUMMARY_PDF_URL"])
        record["source_url"] = f"{parsed.scheme}://{parsed.hostname}" + (f":{parsed.port}" if parsed.port else "") + parsed.path
        return record


def private_file(path, maximum=32768):
    path = Path(path)
    metadata = path.lstat()
    parent = path.parent.stat()
    if (not _outside_repository(path) or path.is_symlink() or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077
            or parent.st_uid != os.getuid() or parent.st_mode & 0o077 or metadata.st_size > maximum):
        raise ApiError("HANDOFF_BOUNDARY")
    return path


def private_handoff(path, evidence=None):
    path = private_file(path)
    value = json_object(path.read_bytes())
    if set(value) != {"as_of", "record"}:
        raise ApiError("HANDOFF_RECORD")
    as_of = datetime.fromisoformat(value["as_of"])
    if as_of.tzinfo is None:
        raise ApiError("HANDOFF_CLOCK")
    record = value["record"]
    fields = {"report_date", "report_date_raw", "values", "numeric_values", "x2", "source_kind", "source_url", "review_only"}
    if isinstance(record, dict) and record.get("source_kind") == "email" and record.get("review_only") is False:
        fields.add("source_authentication")
    if not isinstance(record, dict) or set(record) != fields:
        raise ApiError("HANDOFF_RECORD")
    if datetime.strptime(record["report_date_raw"], "%m/%d/%Y").date().isoformat() != record["report_date"]:
        raise ApiError("HANDOFF_REPORT_DATE")
    raw, numeric, x2 = normalize_values(record["values"])
    if (record.get("source_kind") not in {"pdf", "email"} or not isinstance(record.get("review_only"), bool)
            or record.get("values") != raw or record.get("numeric_values") != numeric or record.get("x2") != x2):
        raise ApiError("HANDOFF_RECORD")
    if record["source_kind"] == "email" and not record["review_only"]:
        verified = load_verified_evidence(evidence or os.environ.get("DELTA_OPS_DKIM_EVIDENCE", ""))
        if record != verified.record():
            raise ApiError("VERIFIED_RECORD_BINDING")
    if record["review_only"] and os.environ.get("GITHUB_ACTIONS") == "true":
        raise ApiError("REPLAY_HOSTED_REFUSED")
    return value


def run_builder(record, *, as_of, out_dir, verification=None, artifact_metadata=None):
    """Private normalized handoff; tokens/raw MIME never enter the R process."""
    if not _outside_repository(Path(out_dir)) or (artifact_metadata and not _outside_repository(Path(artifact_metadata))):
        raise ApiError("CANDIDATE_BOUNDARY")
    with tempfile.TemporaryDirectory(prefix="delta-selected-") as folder:
        evidence = None
        if record.get("source_kind") == "email" and record.get("review_only") is False:
            if not isinstance(verification, VerifiedMessage) or verification.record() != record:
                raise ApiError("UNVERIFIED_SIGNATURE")
            evidence = Path(folder) / "verification.json"
            with open(evidence, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as out:
                json.dump({"created": time.time(), "raw": base64.b64encode(verification.raw).decode(),
                           "keys": {k: base64.b64encode(v).decode() if v is not None else None for k, v in verification.keys.items()},
                           "record_json": verification.record_json}, out)
        selected = Path(folder) / "record.json"
        with open(selected, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as out:
            json.dump({"as_of": as_of.isoformat(), "record": record}, out, allow_nan=False)
        private_handoff(selected, evidence)
        env = child_environment()
        env.update(DELTA_OPS_SELECTED_RECORD=str(selected), DELTA_OPS_OUT_DIR=str(out_dir), DELTA_OPS_SAVED_EMAIL="")
        if evidence is not None:
            env["DELTA_OPS_DKIM_EVIDENCE"] = str(evidence)
        # Builder diagnostics contain scientific values, but suppress them here:
        # this reader's logs expose status/counts only, including failure paths.
        result = subprocess.run(["Rscript", "scripts/build_delta_ops_daily_summary.R"], cwd=REPO, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        if result.returncode:
            raise ApiError("BUILDER_REJECTED")
        if artifact_metadata is not None:
            candidate_root = Path(out_dir).resolve().parent.parent
            # Finish validation and existing hash-metadata preparation while the
            # single-run private DKIM evidence still exists. Never export it.
            checked = subprocess.run([sys.executable, "scripts/delta_ops_publisher.py", "validate", "--root", str(candidate_root)],
                                     cwd=REPO, env=env, capture_output=True, timeout=30)
            if checked.returncode:
                raise ApiError("CANDIDATE_REJECTED")
            summary = json_object((candidate_root / "docs/data/delta_ops_daily_summary_summary.json").read_bytes())
            from delta_ops_publisher import ALLOWLIST, PRODUCT_ID
            args = [sys.executable, "scripts/main_publisher.py", "prepare", "--candidate-root", str(candidate_root),
                    "--output", str(artifact_metadata), "--product-id", PRODUCT_ID, "--semantic-key-type", "report_date",
                    "--semantic-key", summary["report_date"], "--source-event-sha", os.environ.get("GITHUB_SHA", "")]
            for path in ALLOWLIST:
                args.extend(["--allowlist", path])
            prepared = subprocess.run(args, cwd=REPO, env=child_environment(), capture_output=True, timeout=30)
            if prepared.returncode:
                raise ApiError("ARTIFACT_METADATA_REJECTED")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["build", "handoff"])
    parser.add_argument("--out-dir")
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--artifact-metadata")
    args = parser.parse_args(argv)
    try:
        if args.mode == "handoff":
            value = private_handoff(args.input)
            output = Path(args.output)
            if not args.out_dir or not _outside_repository(Path(args.out_dir)) or not _outside_repository(output):
                raise ApiError("HANDOFF_BOUNDARY")
            with open(output, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as out:
                json.dump(value, out, allow_nan=False)
            return 0
        if not args.out_dir or not _outside_repository(Path(args.out_dir)):
            raise ApiError("CANDIDATE_BOUNDARY")
        as_of = datetime.now(timezone.utc)
        batch = retrieve(Config.from_env(), as_of=as_of)
        print("DELTA_GMAIL " + json.dumps(batch.diagnostic(), sort_keys=True))
        selected = select_retrieved(batch, fetch_pdf_record, as_of=as_of)
        print("DELTA_SELECTION " + selected["decision"])
        if selected["record"] is None:
            raise ApiError("NO_ACCEPTABLE_SOURCE")
        binding = next((item for item in batch.bindings if item.record_json == json.dumps(selected["record"], sort_keys=True)), None)
        run_builder(selected["record"], as_of=as_of, out_dir=args.out_dir,
                    verification=binding, artifact_metadata=args.artifact_metadata)
        print("DELTA_BUILD COMPLETE")
        return 0
    except (ApiError, ReportError, OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
        print("DELTA_GMAIL_OPERATION_REJECTED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
