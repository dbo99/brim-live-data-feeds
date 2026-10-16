#!/usr/bin/env python3
"""Dormant, explicit maintainer-only Desktop OAuth setup. Never run in CI.

No browser or network activity without --authorize and complete private inputs.
Only gmail.readonly is requested. Outputs are exclusive, owner-only local files;
logs contain fixed status codes, never URLs, codes, tokens, or private paths.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import re
import stat
import sys
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

from delta_ops_email import _outside_repository
from delta_ops_gmail import ApiError, BoundedHTTP, SCOPE, TOKEN_URL, json_object, request_deadline, validate_access_token, verify_profile

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
CLIENT_AUTH_URIS = {AUTH_URL, "https://accounts.google.com/o/oauth2/auth"}
CONSENT_SECONDS = 180
ISSUER = "https://accounts.google.com"
BROWSER_ACCEPTED = b"Authorization callback accepted. Check Terminal for final credential completion."
BROWSER_REJECTED = b"Authorization callback rejected. Check Terminal for the safe failure reason."
SAFE_REASONS = {
    "INPUT": {"LOCAL_SETUP_ONLY", "AMBIGUOUS_EXPECTED_ACCOUNT", "EXPECTED_ACCOUNT_REQUIRED",
              "HANDOFF_BOUNDARY", "PRIVATE_PATH_REQUIRED", "PRIVATE_DIRECTORY_REQUIRED",
              "PRIVATE_CLIENT_REQUIRED", "DESKTOP_CLIENT_REQUIRED", "CLIENT_INVALID", "INVALID_JSON"},
    "CALLBACK": {"CALLBACK_REDIRECT_INVALID", "CALLBACK_INVALID", "CALLBACK_AMBIGUOUS",
                 "CALLBACK_STATE_INVALID", "CALLBACK_ISSUER_INVALID", "CALLBACK_RESPONSE_INVALID",
                 "CONSENT_REFUSED", "CONSENT_EXPIRED_OR_USED"},
    "CONSENT": {"CONSENT_EXPIRED", "CALLBACK_REQUEST_LIMIT", "CONSENT_NOT_COMPLETED"},
    "BROWSER": {"BROWSER_OPEN_FAILED"},
    "TOKEN": {"CONSENT_EXPIRED_OR_UNUSED", "TOKEN_REFUSED", "AUTH_EXPIRED", "ACCESS_REFUSED",
              "QUOTA_LIMIT", "API_UNAVAILABLE", "TRANSPORT_UNAVAILABLE", "REQUEST_TIMEOUT",
              "REQUEST_BUDGET", "HTTP_PREMATURE_EOF", "HTTP_FRAMING_INVALID", "RESPONSE_LIMIT",
              "INVALID_JSON", "TOKEN_INVALID", "OFFLINE_REFRESH_TOKEN_REQUIRED"},
    "SCOPE": {"SCOPE_UNVERIFIED"},
    "PROFILE": {"WRONG_MAILBOX", "AUTH_EXPIRED", "ACCESS_REFUSED", "QUOTA_LIMIT", "API_UNAVAILABLE",
                "TRANSPORT_UNAVAILABLE", "REQUEST_TIMEOUT", "REQUEST_BUDGET", "BACKOFF_REQUIRED",
                "RETRY_AFTER_INVALID", "RETRY_AFTER_AMBIGUOUS", "HTTP_PREMATURE_EOF",
                "HTTP_FRAMING_INVALID", "RESPONSE_LIMIT", "INVALID_JSON"},
    "OUTPUT": {"OUTPUT_EXISTS", "PRIVATE_PATH_REQUIRED", "PRIVATE_DIRECTORY_REQUIRED"},
}


class SetupError(ApiError):
    """Closed stage/reason labels only; never carry provider/request text."""
    def __init__(self, stage, error):
        self.stage = stage if stage in SAFE_REASONS else "INPUT"
        code = error.code if isinstance(error, ApiError) else "FAILED"
        super().__init__(code if code in SAFE_REASONS[self.stage] else "FAILED")


@contextmanager
def setup_stage(stage):
    try:
        yield
    except SetupError:
        raise
    except Exception as error:
        raise SetupError(stage, error) from None


def diagnostic(error, prefix="DELTA_OAUTH_SETUP_REJECTED"):
    # All callers supply SetupError, and construction filters both labels.
    return f"{prefix} stage={error.stage} reason={error.code}"


def private_parent(path):
    path = Path(path)
    parent = path.parent
    if path.is_symlink() or parent.is_symlink() or not _outside_repository(path):
        raise ApiError("PRIVATE_PATH_REQUIRED")
    info = parent.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077 or not stat.S_ISDIR(info.st_mode):
        raise ApiError("PRIVATE_DIRECTORY_REQUIRED")
    return path


def read_client(path):
    path = private_parent(path)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 16384):
        raise ApiError("PRIVATE_CLIENT_REQUIRED")
    value = json_object(path.read_bytes()).get("installed")
    if (not isinstance(value, dict) or value.get("auth_uri") not in CLIENT_AUTH_URIS
            or value.get("token_uri") != TOKEN_URL or not value.get("client_id") or not value.get("client_secret")):
        raise ApiError("DESKTOP_CLIENT_REQUIRED")
    if any(not isinstance(value[k], str) or len(value[k]) > 4096 for k in ["client_id", "client_secret"]):
        raise ApiError("CLIENT_INVALID")
    return value


@dataclass(repr=False)
class Consent:
    client_id: str = field(repr=False)
    redirect: str
    clock: object = field(default=time.monotonic, repr=False)
    state: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    verifier: str = field(default_factory=lambda: secrets.token_urlsafe(64), repr=False)
    consumed: bool = False
    accepted: bool = False
    exchanged: bool = False
    started: float = field(init=False)

    def __post_init__(self):
        parsed = urlsplit(self.redirect)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
                or parsed.path != "/" or parsed.query or parsed.fragment or parsed.username or parsed.password):
            raise ApiError("LOOPBACK_REDIRECT_REQUIRED")
        self.started = self.clock()

    def authorization_url(self):
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        return AUTH_URL + "?" + urlencode({"client_id": self.client_id, "redirect_uri": self.redirect,
                                          "response_type": "code", "scope": SCOPE, "state": self.state,
                                          "code_challenge": challenge, "code_challenge_method": "S256",
                                          "access_type": "offline", "prompt": "consent",
                                          "include_granted_scopes": "false"})

    def accept(self, request_target, host):
        if self.consumed or self.clock() - self.started >= CONSENT_SECONDS:
            raise ApiError("CONSENT_EXPIRED_OR_USED")
        if len(request_target) > 8192:
            raise ApiError("CALLBACK_INVALID")
        try:
            parsed = urlsplit(request_target)
        except ValueError:
            raise ApiError("CALLBACK_INVALID") from None
        if (parsed.scheme or parsed.netloc or parsed.path != "/" or parsed.fragment
                or host != urlsplit(self.redirect).netloc):
            raise ApiError("CALLBACK_REDIRECT_INVALID")
        try:
            if re.search(r"%(?![0-9a-fA-F]{2})", parsed.query):
                raise ValueError()
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True,
                             max_num_fields=12, errors="strict")
        except (ValueError, UnicodeError):
            raise ApiError("CALLBACK_INVALID") from None
        # Unrelated/malformed probes cannot consume a session. Establish one
        # exact state before treating any response as a terminal callback.
        states = query.get("state", [])
        if (len(states) != 1 or not states[0] or not states[0].isascii()
                or not secrets.compare_digest(states[0], self.state)):
            raise ApiError("CALLBACK_STATE_INVALID")
        self.consumed = True
        allowed = {"iss", "state", "code", "scope", "authuser", "prompt", "error", "error_description", "error_uri"}
        if not set(query) <= allowed or any(len(v) != 1 for v in query.values()):
            raise ApiError("CALLBACK_AMBIGUOUS")
        if query.get("iss") != [ISSUER]:
            raise ApiError("CALLBACK_ISSUER_INVALID")
        if "error" in query and "code" in query:
            raise ApiError("CALLBACK_RESPONSE_INVALID")
        if "error" in query:
            raise ApiError("CONSENT_REFUSED")
        if ("error_description" in query or "error_uri" in query or "code" not in query
                or not 1 <= len(query["code"][0]) <= 4096):
            raise ApiError("CALLBACK_RESPONSE_INVALID")
        self.accepted = True
        return query["code"][0]


def exchange(client, consent, code, *, expected_account, http):
    with setup_stage("TOKEN"):
        if (not consent.consumed or not consent.accepted or consent.exchanged
                or consent.clock() - consent.started >= CONSENT_SECONDS):
            raise ApiError("CONSENT_EXPIRED_OR_UNUSED")
        consent.exchanged = True
        result = http.request("POST", TOKEN_URL, form={"grant_type": "authorization_code",
                              "client_id": client["client_id"], "client_secret": client["client_secret"],
                              "code": code, "code_verifier": consent.verifier, "redirect_uri": consent.redirect}, limit=16384)
    try:
        access = validate_access_token(result)
    except Exception as error:
        stage = "SCOPE" if isinstance(error, ApiError) and error.code == "SCOPE_UNVERIFIED" else "TOKEN"
        raise SetupError(stage, error) from None
    with setup_stage("TOKEN"):
        refresh = result.get("refresh_token")
        if not isinstance(refresh, str) or not 1 <= len(refresh) <= 4096 or any(c.isspace() for c in refresh):
            raise ApiError("OFFLINE_REFRESH_TOKEN_REQUIRED")
    with setup_stage("PROFILE"):
        verify_profile(http, access, expected_account)
    # Access tokens and authorization codes are deliberately not persisted.
    return {"client_id": client["client_id"], "client_secret": client["client_secret"],
            "refresh_token": refresh, "scope": SCOPE, "expected_account": expected_account}


def save_credentials(path, value):
    path = private_parent(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as out:
            json.dump(value, out, allow_nan=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
    except Exception:
        path.unlink(missing_ok=True)  # Only this newly created file is task-owned.
        raise


class LoopbackServer(HTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address

    def finish_request(self, request, client_address):
        # Bound the whole callback, including slowly delivered request headers.
        with request_deadline(2):
            super().finish_request(request, client_address)

    def handle_error(self, request, client_address):
        # Do not emit a traceback or private callback details on malformed input.
        pass


def authorize(client, output, expected_account, *, open_browser=webbrowser.open, http_factory=BoundedHTTP):
    """Explicit local setup only. Tests inject the browser and HTTP boundary."""
    with setup_stage("OUTPUT"):
        private_parent(output)
        if Path(output).exists() or Path(output).is_symlink():
            raise ApiError("OUTPUT_EXISTS")
    received = {}
    requests = 0
    consent = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send_error(self, code, message=None, explain=None):
            # BaseHTTPRequestHandler otherwise reflects malformed request text.
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(BROWSER_REJECTED)

        def do_GET(self):
            nonlocal requests
            requests += 1
            try:
                hosts = self.headers.get_all("Host", [])
                if len(hosts) != 1:
                    raise ApiError("CALLBACK_REDIRECT_INVALID")
                received["code"] = consent.accept(self.path, hosts[0])
                status = 200
            except ApiError as error:
                status = 400
                failure = SetupError("CALLBACK", error)
                if consent.consumed:
                    received["error"] = failure
                else:
                    print(diagnostic(failure, "DELTA_OAUTH_CALLBACK_IGNORED"), file=sys.stderr)
            self.send_response(status)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(BROWSER_ACCEPTED if status == 200 else BROWSER_REJECTED)

    with LoopbackServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 1
        consent = Consent(client["client_id"], f"http://127.0.0.1:{server.server_port}/")
        with setup_stage("BROWSER"):
            if not open_browser(consent.authorization_url()):
                raise ApiError("BROWSER_OPEN_FAILED")
        while not consent.consumed and requests < 8 and consent.clock() - consent.started < CONSENT_SECONDS:
            server.handle_request()
        if "error" in received:
            raise received["error"]
        if "code" not in received:
            reason = "CALLBACK_REQUEST_LIMIT" if requests >= 8 else "CONSENT_EXPIRED"
            raise SetupError("CONSENT", ApiError(reason))
    print("DELTA_OAUTH CALLBACK_ACCEPTED")
    # Start a fresh bounded HTTP budget after the bounded human-consent interval.
    value = exchange(client, consent, received["code"], expected_account=expected_account, http=http_factory())
    with setup_stage("OUTPUT"):
        save_credentials(output, value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorize", action="store_true")
    parser.add_argument("--client-file")
    parser.add_argument("--output")
    parser.add_argument("--expected-account")
    parser.add_argument("--expected-account-file", help="owner-only file containing the expected mailbox; allows path-only setup arguments")
    args = parser.parse_args(argv)
    if not args.authorize or not all([args.client_file, args.output, args.expected_account or args.expected_account_file]):
        print("DELTA_OAUTH UNCONFIGURED")
        return 0
    try:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise ApiError("LOCAL_SETUP_ONLY")
        if args.expected_account_file:
            if args.expected_account:
                raise ApiError("AMBIGUOUS_EXPECTED_ACCOUNT")
            from delta_ops_gmail import private_file
            account_file = private_file(args.expected_account_file, maximum=4096)
            args.expected_account = account_file.read_text(encoding="ascii").strip()
        if not args.expected_account or any(c.isspace() for c in args.expected_account) or args.expected_account.count("@") != 1:
            raise ApiError("EXPECTED_ACCOUNT_REQUIRED")
        client = read_client(args.client_file)
        authorize(client, args.output, args.expected_account)
        print("DELTA_OAUTH PRIVATE_CREDENTIAL_FILE_CREATED")
        return 0
    except Exception as error:
        failure = error if isinstance(error, SetupError) else SetupError("INPUT", error)
        print(diagnostic(failure), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
