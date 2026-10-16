"""Synthetic-only Gmail/OAuth boundaries and real R builder convergence tests."""
import base64
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import hashlib
from functools import lru_cache
from email import policy
from email.message import Message
from email.utils import format_datetime
import shutil
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
sys.path.insert(0, str(REPO / 'tests'))
import delta_ops_gmail as gmail
import delta_ops_gmail_oauth as oauth
import delta_ops_email as email_adapter
from test_delta_ops_email import message, record, report, CANARY

AS_OF = datetime(2026, 10, 15, 19, tzinfo=timezone.utc)
ACCOUNT = 'dedicated-synthetic@example.invalid'
SENDER = gmail.APPROVED_SENDER
TOKEN = 'SYNTHETIC_ACCESS_CANARY'
REFRESH = 'SYNTHETIC_REFRESH_CANARY'
CLIENT_SECRET = 'SYNTHETIC_CLIENT_SECRET_CANARY'
CLIENT_ID = 'synthetic-client.apps.googleusercontent.com'


def config(**changes):
    return gmail.Config(**dict(dict(enabled=True, client_id=CLIENT_ID, client_secret=CLIENT_SECRET,
                                   refresh_token=REFRESH, expected_account=ACCOUNT, expected_sender=SENDER), **changes))


def token(**changes):
    return dict(dict(access_token=TOKEN, token_type='Bearer', expires_in=3600, scope=gmail.SCOPE), **changes)


def envelope(body=None, identifier='synthetic1'):
    if body is None:
        body = message(report()); body.replace_header('From', SENDER)
    raw = body if isinstance(body, bytes) else body.as_bytes()
    return {'id': identifier, 'raw': base64.urlsafe_b64encode(raw).rstrip(b'=').decode()}


class FakeHTTP:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.replies:
            raise AssertionError('unexpected synthetic API call')
        value = self.replies.pop(0)
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


def mailbox(items=None, pages=None):
    items = [envelope()] if items is None else items
    listing = {'messages': [{'id': x['id']} for x in items]}
    return FakeHTTP([token(), {'emailAddress': ACCOUNT}, *(pages or [listing, *items])])


class GmailReaderTests(unittest.TestCase):
    def test_default_off_or_missing_credentials_never_calls_http(self):
        for changes in [{'enabled': False}, {'client_id': ''}, {'client_secret': ''}, {'refresh_token': ''},
                        {'expected_account': ''}, {'expected_sender': ''}]:
            http = FakeHTTP([])
            result = gmail.retrieve(config(**changes), as_of=AS_OF, http=http)
            self.assertEqual(result.state, 'UNCONFIGURED')
            self.assertFalse(http.calls)

    def test_exact_profile_and_scope_required_before_list(self):
        variants = [token(scope=''), token(scope=None), token(scope=gmail.SCOPE + ' https://mail.google.com/'),
                    token(scope='https://www.googleapis.com/auth/gmail.modify'), token(token_type=None),
                    token(expires_in=0), token(expires_in=True), token(access_token='')]
        for value in variants:
            http = FakeHTTP([value])
            result = gmail.retrieve(config(), as_of=AS_OF, http=http)
            self.assertEqual(result.state, 'RETRIEVAL_UNAVAILABLE')
            self.assertEqual(len(http.calls), 1)
        http = FakeHTTP([token(), {'emailAddress': 'wrong@example.invalid'}])
        self.assertEqual(gmail.retrieve(config(), as_of=AS_OF, http=http).reason, 'WRONG_MAILBOX')
        self.assertEqual(len(http.calls), 2)

    def test_expired_refused_refresh_and_api_errors_are_safe(self):
        for code in ['TOKEN_REFUSED', 'AUTH_EXPIRED', 'ACCESS_REFUSED', 'QUOTA_LIMIT', 'API_UNAVAILABLE', 'TRANSPORT_UNAVAILABLE']:
            result = gmail.retrieve(config(), as_of=AS_OF, http=FakeHTTP([gmail.ApiError(code)]))
            self.assertEqual(result.state, 'RETRIEVAL_UNAVAILABLE')
            self.assertEqual(result.reason, code)
            self.assertNotIn(TOKEN, json.dumps(result.diagnostic()))

    def test_read_endpoints_only_and_discovery_ignores_read_status(self):
        http = mailbox()
        result = gmail.retrieve(config(), as_of=AS_OF, http=http)
        self.assertEqual(result.state, 'TRUST_UNVERIFIED')
        self.assertTrue(all(not d.trusted for d in result.deliveries))
        self.assertEqual([method for method, _, _ in http.calls], ['POST', 'GET', 'GET', 'GET'])
        self.assertEqual(http.calls[0][1], gmail.TOKEN_URL)
        query = parse_qs(urlsplit(http.calls[2][1]).query)
        self.assertEqual(query['includeSpamTrash'], ['false'])
        self.assertNotIn('is:unread', query['q'][0])
        self.assertNotIn('labelIds', query)
        self.assertEqual(parse_qs(urlsplit(http.calls[3][1]).query)['format'], ['raw'])
        self.assertEqual(result.deliveries[0].record['report_date'], '2026-10-15')
        for secret in [ACCOUNT, SENDER, TOKEN, REFRESH, 'synthetic1', CANARY]:
            self.assertNotIn(secret, json.dumps(result.diagnostic()))

    def test_empty_is_complete_but_absent_or_contradictory_list_is_not_empty(self):
        for value, state in [({'resultSizeEstimate': 0}, 'COMPLETE_VALID_EMPTY'), ({'messages': []}, 'COMPLETE_VALID_EMPTY'),
                             ({}, 'SCAN_INCOMPLETE'), ({'resultSizeEstimate': 9}, 'SCAN_INCOMPLETE'),
                             ({'messages': None}, 'SCAN_INCOMPLETE')]:
            http = FakeHTTP([token(), {'emailAddress': ACCOUNT}, value])
            self.assertEqual(gmail.retrieve(config(), as_of=AS_OF, http=http).state, state)

    def test_pagination_duplicate_ids_and_complete_scan(self):
        first, second = envelope(), envelope(identifier='synthetic2')
        http = mailbox(pages=[{'messages': [{'id': 'synthetic1'}], 'nextPageToken': 'next'}, first,
                              {'messages': [{'id': 'synthetic1'}, {'id': 'synthetic2'}]}, second])
        result = gmail.retrieve(config(), as_of=AS_OF, http=http)
        self.assertEqual((result.listed, result.fetched), (2, 2))
        self.assertEqual(len(result.deliveries), 2)
        self.assertEqual(len(http.calls), 6)

    def test_scan_limits_repeated_tokens_and_interruptions_discard_partial_set(self):
        for pages in [[{'messages': [], 'nextPageToken': 'same'}] * 2,
                      [{'messages': [], 'nextPageToken': str(n)} for n in range(gmail.MAX_PAGES)],
                      [{'messages': [{'id': 'synthetic1'}]}, gmail.ApiError('TRANSPORT_UNAVAILABLE')],
                      [{'messages': [{'id': '../unsafe'}]}]]:
            result = gmail.retrieve(config(), as_of=AS_OF, http=mailbox(pages=pages))
            self.assertEqual(result.state, 'SCAN_INCOMPLETE')
            self.assertFalse(result.deliveries)
        with patch.object(gmail, 'MAX_MESSAGES', 1):
            items = [envelope(), envelope(identifier='synthetic2')]
            self.assertEqual(gmail.retrieve(config(), as_of=AS_OF, http=mailbox(items)).state, 'SCAN_INCOMPLETE')

    def test_invalid_envelopes_and_base64_cannot_be_empty_success(self):
        for value in [{'id': 'other', 'raw': 'AAAA'}, {'id': 'synthetic1', 'raw': '!'},
                      {'id': 'synthetic1', 'raw': ''}, {'id': 'synthetic1', 'raw': 'A'},
                      {'id': 'synthetic1', 'raw': 'A' * (gmail.MAX_MESSAGE_BYTES * 2)}]:
            result = gmail.retrieve(config(), as_of=AS_OF, http=mailbox([value]))
            self.assertIn(result.state, ['SCAN_INCOMPLETE', 'MESSAGE_REJECTED'])
        result = gmail.retrieve(config(), as_of=AS_OF, http=mailbox([envelope(message('No report here'))]))
        self.assertEqual(result.state, 'MESSAGE_REJECTED')
        self.assertEqual(result.rejected, 1)

    def test_receiver_auth_forged_repeated_ambiguous_non_google_never_grants_trust(self):
        for auths in [[], ['mx.google.com; dkim=pass; spf=pass; dmarc=pass'],
                      ['mx.google.com; dkim=pass', 'mx.google.com; dkim=fail'],
                      ['attacker.invalid; dkim=pass'], ['mx.google.com.attacker.invalid; dkim=pass']]:
            m = message(report())
            del m['Authentication-Results']
            for auth in auths:
                m['Authentication-Results'] = auth
            m['Received'] = 'by mx.google.com; synthetic copied receiver trace'
            m['ARC-Authentication-Results'] = 'i=1; mx.google.com; dkim=pass'
            trusted, reason = gmail.receiver_verdict(m.as_bytes(), SENDER)
            self.assertFalse(trusted)
            self.assertIn(reason, ['NO_QUALIFYING_SIGNATURE', 'SENDER_CLAIM_REJECTED'])
        m = message(report())
        m.replace_header('From', 'forged@example.invalid')
        self.assertEqual(gmail.receiver_verdict(m.as_bytes(), SENDER)[1], 'SENDER_CLAIM_REJECTED')

    def test_query_epoch_bounds_and_report_dates_are_separate(self):
        q = gmail.discovery_query(datetime(2027, 1, 1, 1, tzinfo=timezone.utc), SENDER)
        start, end = [int(x) for x in __import__('re').findall(r'(?:after|before):(\d+)', q)]
        self.assertEqual(datetime.fromtimestamp(end, timezone.utc), datetime(2027, 1, 1, 8, tzinfo=timezone.utc))
        self.assertEqual(end - start, 16 * 86400)
        with self.assertRaises(gmail.ApiError):
            gmail.discovery_query(AS_OF, 'sender@example.invalid OR from:other')

    def test_partial_scan_and_unavailable_states_call_pdf_once(self):
        for state in ['SCAN_INCOMPLETE', 'RETRIEVAL_UNAVAILABLE', 'UNCONFIGURED', 'COMPLETE_VALID_EMPTY', 'TRUST_UNVERIFIED']:
            pdf = Mock(return_value=record(kind='pdf'))
            batch = gmail.Retrieval(state, 'SYNTHETIC_STATUS')
            chosen = gmail.select_retrieved(batch, pdf, as_of=AS_OF)
            self.assertEqual(chosen['decision'], 'pdf_recovery')
            pdf.assert_called_once_with()

    def test_verified_boundary_preserves_conflict_malformed_and_duplicate_policy(self):
        for changed, decision in [({'jones_pumping_plant':'2701 cfs'}, 'failure_retained'),
                                  ({'jones_pumping_plant':'invalid'}, 'email_after_pdf_check')]:
            first, keys = signed_message()
            second, _ = signed_message(report(changes=changed))
            batch = gmail.retrieve(config(), as_of=AS_OF, http=mailbox([envelope(first), envelope(second,'synthetic2')]), keys=keys)
            self.assertEqual(batch.state, 'VERIFIED_ELIGIBLE')
            chosen = gmail.select_retrieved(batch, lambda: record(kind='pdf'), as_of=AS_OF)
            self.assertEqual(chosen['decision'], decision)


class HTTPBoundTests(unittest.TestCase):
    def test_wall_deadline_covers_blocked_connection_and_restores_alarm(self):
        connection = Mock()
        connection.request.side_effect = lambda *a, **k: time.sleep(1)
        before = gmail.signal.getsignal(gmail.signal.SIGALRM)
        started = time.monotonic()
        client = gmail.BoundedHTTP(sleep=Mock())
        with patch.object(gmail, 'REQUEST_SECONDS', 0.02), patch.object(gmail.http.client, 'HTTPSConnection', return_value=connection):
            with self.assertRaisesRegex(gmail.ApiError, 'REQUEST_TIMEOUT'):
                client.request('GET', gmail.API+'/profile')
        self.assertLess(time.monotonic()-started, 0.5)
        self.assertEqual(gmail.signal.getitimer(gmail.signal.ITIMER_REAL)[0], 0)
        self.assertEqual(gmail.signal.getsignal(gmail.signal.SIGALRM), before)

    def test_redirects_and_mutation_endpoints_are_refused(self):
        client = gmail.BoundedHTTP()
        for method, url in [('POST', gmail.API + '/messages/send'), ('GET', 'http://gmail.googleapis.com/'),
                            ('GET', 'https://attacker.invalid/'), ('GET', gmail.API + '/messages/../profile')]:
            with self.assertRaisesRegex(gmail.ApiError, 'ENDPOINT_REFUSED'):
                client.request(method, url)
        with patch.object(client, '_one', return_value=(302, '', b'')) as one:
            with self.assertRaises(gmail.ApiError):
                client.request('GET', gmail.API + '/profile')
            one.assert_called_once()

    def test_retries_status_codes_and_retry_after_are_bounded(self):
        for status, calls in [(401, 1), (403, 1), (429, 2), (500, 2), (503, 2)]:
            sleep = Mock()
            client = gmail.BoundedHTTP(sleep=sleep)
            with patch.object(client, '_one', return_value=(status, ['1'], b'')) as one:
                with self.assertRaises(gmail.ApiError):
                    client.request('GET', gmail.API + '/profile')
                self.assertEqual(one.call_count, calls)
                self.assertLessEqual(sum(x.args[0] for x in sleep.call_args_list), 2)
        client = gmail.BoundedHTTP(sleep=Mock())
        with patch.object(client, '_one', return_value=(503, '1', b'')) as one:
            with self.assertRaises(gmail.ApiError):
                client.request('POST', gmail.TOKEN_URL, form={'grant_type': 'refresh_token'})
            one.assert_called_once()

    def test_invalid_json_duplicate_keys_nonfinite_and_total_deadline(self):
        for raw in [b'[]', b'{', b'{"a":1,"a":2}', b'{"a":NaN}']:
            with self.assertRaises(gmail.ApiError):
                gmail.json_object(raw)
        now = [0]
        client = gmail.BoundedHTTP(clock=lambda: now[0])
        now[0] = gmail.RUN_SECONDS + 1
        with self.assertRaisesRegex(gmail.ApiError, 'REQUEST_BUDGET'):
            client.request('GET', gmail.API + '/profile')

    def test_body_and_aggregate_caps_and_interrupted_reads(self):
        class Response:
            status = 200
            headers = Message()
            def getheader(self, *_): return ''
            def read1(self, n): return b'x' * n
        connection = Mock()
        connection.getresponse.return_value = Response()
        for total in [0, gmail.MAX_AGGREGATE_BYTES]:
            client = gmail.BoundedHTTP()
            client.bytes = total
            with patch.object(gmail.http.client, 'HTTPSConnection', return_value=connection):
                with self.assertRaisesRegex(gmail.ApiError, 'RESPONSE_LIMIT'):
                    client.request('GET', gmail.API + '/profile', limit=8)
        connection.getresponse.side_effect = gmail.http.client.IncompleteRead(b'SYNTHETIC_PRIVATE')
        client = gmail.BoundedHTTP(sleep=Mock())
        with patch.object(gmail.http.client, 'HTTPSConnection', return_value=connection):
            with self.assertRaisesRegex(gmail.ApiError, 'TRANSPORT_UNAVAILABLE') as error:
                client.request('GET', gmail.API + '/profile')
        self.assertNotIn('SYNTHETIC_PRIVATE', str(error.exception))


class OAuthTests(unittest.TestCase):
    def test_desktop_client_file_accepts_google_export_and_rejects_other_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic-client.json'
            installed = {'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
                         'token_uri': gmail.TOKEN_URL}
            for uri in oauth.CLIENT_AUTH_URIS:
                installed['auth_uri'] = uri
                path.write_text(json.dumps({'installed': installed})); path.chmod(0o600)
                self.assertEqual(oauth.read_client(path), installed)
            for value in [{'web': installed}, {'installed': dict(installed, auth_uri='https://attacker.invalid/')},
                          {'installed': dict(installed, token_uri='https://attacker.invalid/')}]:
                path.write_text(json.dumps(value))
                with self.assertRaises(gmail.ApiError): oauth.read_client(path)

    def test_mock_browser_loopback_roundtrip_and_listener_cleanup(self):
        results, threads = [], []
        def browser(url):
            query = parse_qs(urlsplit(url).query)
            redirect = urlsplit(query['redirect_uri'][0])
            def callback():
                connection = gmail.http.client.HTTPConnection(redirect.hostname, redirect.port, timeout=5)
                try:
                    connection.request('GET', '/?'+urlencode({'state':query['state'][0], 'code':'SYNTHETIC_CODE', 'iss':oauth.ISSUER}))
                    response = connection.getresponse()
                    results.append(response.status)
                    response.read()
                finally:
                    connection.close()
            worker=threading.Thread(target=callback)
            threads.append(worker);worker.start()
            return True
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'synthetic-private.json'
            fake=FakeHTTP([token(refresh_token=REFRESH),{'emailAddress':ACCOUNT}])
            with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
                oauth.authorize({'client_id':CLIENT_ID,'client_secret':CLIENT_SECRET},output,ACCOUNT,
                                open_browser=browser,http_factory=lambda:fake)
            for worker in threads:worker.join(timeout=5);self.assertFalse(worker.is_alive())
            self.assertEqual(results,[200]);self.assertEqual(output.stat().st_mode & 0o777,0o600)
            self.assertEqual(stdout.getvalue(),'DELTA_OAUTH CALLBACK_ACCEPTED\n');self.assertEqual(stderr.getvalue(),'')
            self.assertNotIn('access_token',json.loads(output.read_text()))

    def test_path_only_setup_arguments_use_private_expected_account_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);account=root/'account.txt';account.write_text(ACCOUNT);account.chmod(0o600)
            client=root/'client.json';client.write_text(json.dumps({'installed':{'client_id':CLIENT_ID,'client_secret':CLIENT_SECRET,'auth_uri':oauth.AUTH_URL,'token_uri':gmail.TOKEN_URL}}));client.chmod(0o600)
            with patch.object(oauth,'authorize') as start,contextlib.redirect_stdout(io.StringIO()) as output:
                result=oauth.main(['--authorize','--client-file',str(client),'--output',str(root/'new.json'),'--expected-account-file',str(account)])
            self.assertEqual(result,0);self.assertEqual(start.call_args.args[2],ACCOUNT)
            self.assertNotIn(ACCOUNT,output.getvalue())
            self.assertNotIn(CLIENT_SECRET,output.getvalue())

    def test_unconfigured_helper_does_not_open_browser_or_network(self):
        with patch.object(oauth, 'authorize', side_effect=AssertionError('must stay dormant')):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(oauth.main([]), 0)
                self.assertEqual(oauth.main(['--authorize']), 0)
            self.assertIn('UNCONFIGURED', output.getvalue())

    def test_state_pkce_scope_and_loopback_redirect(self):
        consent = oauth.Consent(CLIENT_ID, 'http://127.0.0.1:12345/')
        query = parse_qs(urlsplit(consent.authorization_url()).query)
        expected = base64.urlsafe_b64encode(hashlib.sha256(consent.verifier.encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(query['code_challenge'], [expected])
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertEqual(query['scope'], [gmail.SCOPE])
        self.assertEqual(query['access_type'], ['offline'])
        self.assertTrue(43 <= len(consent.verifier) <= 128)
        for redirect in ['https://attacker.invalid/', 'http://0.0.0.0:12345/', 'http://localhost:12345/', 'http://127.0.0.1:12345/other']:
            with self.assertRaises(gmail.ApiError): oauth.Consent(CLIENT_ID, redirect)

    def test_callback_forgery_duplicates_expiry_and_error(self):
        for target, host in [('http://attacker.invalid/?state=wrong&code=x', '127.0.0.1:12345'),
                             ('/?state=wrong&code=x', '127.0.0.1:12345'),
                             ('/?state={state}&state={state}&code=x', '127.0.0.1:12345'),
                             ('/?state={state}&code=x', 'attacker.invalid')]:
            consent = oauth.Consent(CLIENT_ID, 'http://127.0.0.1:12345/')
            with self.assertRaises(gmail.ApiError): consent.accept(target.format(state=consent.state), host)
        now = [0]
        consent = oauth.Consent(CLIENT_ID, 'http://127.0.0.1:12345/', clock=lambda: now[0])
        now[0] = oauth.CONSENT_SECONDS
        with self.assertRaises(gmail.ApiError): consent.accept('/?state='+consent.state+'&code=x', '127.0.0.1:12345')
        consent = oauth.Consent(CLIENT_ID, 'http://127.0.0.1:12345/')
        with self.assertRaisesRegex(gmail.ApiError, 'CONSENT_REFUSED'):
            consent.accept('/?state='+consent.state+'&iss=https%3A%2F%2Faccounts.google.com&error=access_denied', '127.0.0.1:12345')
        self.assertTrue(consent.consumed)

    def test_code_exchange_exact_redirect_verifier_identity_and_no_access_persistence(self):
        consent = oauth.Consent(CLIENT_ID, 'http://127.0.0.1:12345/')
        code = consent.accept('/?'+urlencode({'state': consent.state, 'code': 'SYNTHETIC_CODE', 'iss': oauth.ISSUER}), '127.0.0.1:12345')
        client = {'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET}
        http = FakeHTTP([token(refresh_token=REFRESH), {'emailAddress': ACCOUNT}])
        result = oauth.exchange(client, consent, code, expected_account=ACCOUNT, http=http)
        form = http.calls[0][2]['form']
        self.assertEqual(form['code_verifier'], consent.verifier)
        self.assertEqual(form['redirect_uri'], consent.redirect)
        self.assertNotIn('access_token', result)
        self.assertNotIn('code', result)
        for replies in [[token()], [token(refresh_token=REFRESH), {'emailAddress': 'other@example.invalid'}]]:
            another = oauth.Consent(CLIENT_ID, 'http://127.0.0.1:12345/')
            another.accept('/?state='+another.state+'&iss=https%3A%2F%2Faccounts.google.com&code=SYNTHETIC_CODE', '127.0.0.1:12345')
            with self.assertRaises(gmail.ApiError): oauth.exchange(client, another, code, expected_account=ACCOUNT, http=FakeHTTP(replies))
        with self.assertRaises(gmail.ApiError): oauth.exchange(client, consent, code, expected_account=ACCOUNT, http=FakeHTTP([]))
        with self.assertRaises(gmail.ApiError): consent.accept('/?state='+consent.state+'&code=x', '127.0.0.1:12345')

    def test_private_files_exclusive_permissions_symlink_and_hosted_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'synthetic-credentials.json'
            oauth.save_credentials(output, {'synthetic': 'NO_REAL_CREDENTIAL'})
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            before = output.read_bytes()
            with self.assertRaises(OSError): oauth.save_credentials(output, {})
            self.assertEqual(output.read_bytes(), before)
            alias = root / 'alias';alias.symlink_to(output)
            with self.assertRaises(gmail.ApiError): oauth.read_client(alias)
            os.chmod(root, 0o755)
            with self.assertRaises(gmail.ApiError): oauth.save_credentials(root / 'other', {})
            os.chmod(root, 0o700)
            with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}), patch.object(oauth, 'authorize') as start:
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(oauth.main(['--authorize','--client-file',str(output),'--output',str(root/'new'),'--expected-account',ACCOUNT]), 1)
                start.assert_not_called()


class OAuthCallbackRegressionTests(unittest.TestCase):
    def callback(self, kind, state):
        pairs = [('state', state), ('code', 'SYNTHETIC_CALLBACK_CODE'), ('iss', oauth.ISSUER),
                 ('scope', gmail.SCOPE), ('authuser', '0'), ('prompt', 'consent')]
        host, path = None, '/'
        if kind in {'missing_issuer', 'wrong_issuer', 'blank_issuer'}:
            pairs = [(k, v) for k, v in pairs if k != 'iss']
            if kind != 'missing_issuer': pairs.append(('iss', '' if kind == 'blank_issuer' else 'https://attacker.invalid/'))
        elif kind == 'duplicate_issuer': pairs.append(('iss', oauth.ISSUER))
        elif kind == 'blank_duplicate_issuer': pairs.insert(0, ('iss', ''))
        elif kind == 'wrong_state': pairs[0] = ('state', 'SYNTHETIC_WRONG_STATE')
        elif kind == 'blank_state': pairs[0] = ('state', '')
        elif kind == 'duplicate_state': pairs.insert(0, ('state', state))
        elif kind == 'blank_duplicate_state': pairs.insert(0, ('state', ''))
        elif kind == 'wrong_host': host = 'attacker.invalid'
        elif kind == 'wrong_path': path = '/other'
        elif kind == 'favicon': return '/favicon.ico', None
        elif kind in {'refused', 'mixed'}:
            if kind == 'refused': pairs = [(k, v) for k, v in pairs if k != 'code']
            pairs += [('error', 'access_denied'), ('error_description', CANARY), ('error_uri', 'https://attacker.invalid/'+CANARY)]
        elif kind == 'blank_code': pairs = [(k, '' if k == 'code' else v) for k, v in pairs]
        elif kind == 'duplicate_code': pairs.append(('code', ''))
        elif kind == 'unknown': pairs.append(('unexpected', CANARY))
        return path+'?'+urlencode(pairs), host

    def roundtrip(self, kinds=('valid',), replies=None, save_error=None):
        responses, threads, endpoints, worker_errors = [], [], [], []
        real_authorize = oauth.authorize
        fake = FakeHTTP(replies if replies is not None else [token(refresh_token=REFRESH), {'emailAddress': ACCOUNT}])
        def browser(url):
            query = parse_qs(urlsplit(url).query)
            redirect = urlsplit(query['redirect_uri'][0]);endpoints.append(redirect)
            def callback():
                try:
                    for kind in kinds:
                        target, host = self.callback(kind, query['state'][0])
                        connection = gmail.http.client.HTTPConnection(redirect.hostname, redirect.port, timeout=2)
                        try:
                            connection.request('GET', target, headers={'Host': host or redirect.netloc})
                            response = connection.getresponse();responses.append((response.status, response.read()))
                        finally: connection.close()
                except Exception as error: worker_errors.append(type(error).__name__)
            worker=threading.Thread(target=callback);threads.append(worker);worker.start();return True
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'.config/brim-live-delta-ops';root.mkdir(parents=True,mode=0o700)
            client=root/'client.json';account=root/'expected-account.txt';output=root/'oauth.json'
            client.write_text(json.dumps({'installed':{'client_id':CLIENT_ID,'client_secret':CLIENT_SECRET,'auth_uri':oauth.AUTH_URL,'token_uri':gmail.TOKEN_URL}}));client.chmod(0o600)
            account.write_text(ACCOUNT);account.chmod(0o600)
            before={p.name:(p.read_bytes(),p.stat().st_mode) for p in [client,account]}
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(oauth,'CONSENT_SECONDS',3))
                stack.enter_context(patch.object(oauth,'authorize',side_effect=lambda c,o,a:real_authorize(c,o,a,open_browser=browser,http_factory=lambda:fake)))
                if save_error is not None:stack.enter_context(patch.object(oauth,'save_credentials',side_effect=save_error))
                stdout=stack.enter_context(contextlib.redirect_stdout(io.StringIO()));stderr=stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
                start=time.monotonic()
                result=oauth.main(['--authorize','--client-file',str(client),'--expected-account-file',str(account),'--output',str(output)])
                elapsed=time.monotonic()-start
                for worker in threads:worker.join(timeout=3);self.assertFalse(worker.is_alive())
            self.assertFalse(worker_errors);self.assertLess(elapsed,2,'callback waited for consent timeout')
            for p in [client,account]:self.assertEqual((p.read_bytes(),p.stat().st_mode),before[p.name])
            exists=output.exists()
            if exists:
                self.assertEqual(output.stat().st_mode & 0o777,0o600)
                self.assertNotIn('access_token',json.loads(output.read_text()))
            transcript=stdout.getvalue()+stderr.getvalue()+''.join(x[1].decode() for x in responses)
            for secret in [CLIENT_ID,CLIENT_SECRET,TOKEN,REFRESH,ACCOUNT,CANARY,'SYNTHETIC_CALLBACK_CODE','SYNTHETIC_WRONG_STATE',str(root)]:self.assertNotIn(secret,transcript)
        for endpoint in endpoints:
            with socket.socket() as connection:
                connection.settimeout(.2);self.assertNotEqual(connection.connect_ex((endpoint.hostname,endpoint.port)),0)
        return result,stdout.getvalue(),stderr.getvalue(),responses,fake.calls,exists

    def test_current_google_callback_completes_and_saves_exclusively(self):
        result,out,err,responses,calls,exists=self.roundtrip()
        self.assertEqual((result,err,exists),(0,'',True))
        self.assertEqual(responses,[(200,oauth.BROWSER_ACCEPTED)])
        self.assertEqual(out,'DELTA_OAUTH CALLBACK_ACCEPTED\nDELTA_OAUTH PRIVATE_CREDENTIAL_FILE_CREATED\n')
        self.assertEqual([c[0] for c in calls],['POST','GET'])

    def test_session_bound_rejections_end_promptly_without_exchange_or_save(self):
        cases={'wrong_issuer':'CALLBACK_ISSUER_INVALID','missing_issuer':'CALLBACK_ISSUER_INVALID',
               'blank_issuer':'CALLBACK_ISSUER_INVALID','duplicate_issuer':'CALLBACK_AMBIGUOUS',
               'blank_duplicate_issuer':'CALLBACK_AMBIGUOUS','refused':'CONSENT_REFUSED',
               'mixed':'CALLBACK_RESPONSE_INVALID','blank_code':'CALLBACK_RESPONSE_INVALID',
               'duplicate_code':'CALLBACK_AMBIGUOUS','unknown':'CALLBACK_AMBIGUOUS'}
        for kind,reason in cases.items():
            with self.subTest(kind=kind):
                result,out,err,responses,calls,exists=self.roundtrip([kind])
                self.assertEqual((result,out,calls,exists),(1,'',[],False))
                self.assertEqual(responses,[(400,oauth.BROWSER_REJECTED)])
                self.assertEqual(err,f'DELTA_OAUTH_SETUP_REJECTED stage=CALLBACK reason={reason}\n')

    def test_unrelated_probes_do_not_consume_session_before_valid_callback(self):
        for kind in ['favicon','wrong_state','blank_state','duplicate_state','blank_duplicate_state','wrong_host','wrong_path']:
            with self.subTest(kind=kind):
                result,out,err,responses,calls,exists=self.roundtrip([kind,'valid'])
                self.assertEqual((result,exists),(0,True));self.assertEqual(len(calls),2)
                self.assertEqual(responses,[(400,oauth.BROWSER_REJECTED),(200,oauth.BROWSER_ACCEPTED)])
                self.assertIn('DELTA_OAUTH_CALLBACK_IGNORED stage=CALLBACK reason=',err)

    def test_probe_count_remains_bounded_without_exchange(self):
        result,out,err,responses,calls,exists=self.roundtrip(['wrong_state']*8)
        self.assertEqual((result,out,calls,exists),(1,'',[],False));self.assertEqual(len(responses),8)
        self.assertIn('stage=CONSENT reason=CALLBACK_REQUEST_LIMIT',err)

    def test_token_scope_profile_and_output_failures_have_closed_stage_diagnostics(self):
        cases=[([gmail.ApiError('TOKEN_REFUSED')],None,'TOKEN','TOKEN_REFUSED'),
               ([RuntimeError(CANARY)],None,'TOKEN','FAILED'),
               ([gmail.ApiError(CANARY)],None,'TOKEN','FAILED'),
               ([token(scope='https://mail.google.com/',refresh_token=REFRESH)],None,'SCOPE','SCOPE_UNVERIFIED'),
               ([token(refresh_token=REFRESH),{'emailAddress':'wrong@example.invalid'}],None,'PROFILE','WRONG_MAILBOX'),
               ([token(refresh_token=REFRESH),RuntimeError(CANARY)],None,'PROFILE','FAILED'),
               (None,OSError(CANARY),'OUTPUT','FAILED')]
        for replies,error,stage,reason in cases:
            with self.subTest(stage=stage,reason=reason):
                result,out,err,responses,calls,exists=self.roundtrip(replies=replies,save_error=error)
                self.assertEqual((result,exists),(1,False));self.assertEqual(responses,[(200,oauth.BROWSER_ACCEPTED)])
                self.assertEqual(out,'DELTA_OAUTH CALLBACK_ACCEPTED\n')
                self.assertEqual(err,f'DELTA_OAUTH_SETUP_REJECTED stage={stage} reason={reason}\n')

    def test_issuer_exactness_and_rejected_session_cannot_exchange(self):
        for issuer in ['',None,'accounts.google.com','http://accounts.google.com','https://accounts.google.com/',
                       'https://accounts.google.com?x=y','https://accounts.google.com@attacker.invalid','https://ACCOUNTS.google.com']:
            consent=oauth.Consent(CLIENT_ID,'http://127.0.0.1:12345/')
            params={'state':consent.state,'code':'SYNTHETIC_CALLBACK_CODE'}
            if issuer is not None:params['iss']=issuer
            with self.assertRaisesRegex(gmail.ApiError,'CALLBACK_ISSUER_INVALID'):consent.accept('/?'+urlencode(params),'127.0.0.1:12345')
            self.assertTrue(consent.consumed);self.assertFalse(consent.accepted)
            fake=FakeHTTP([])
            with self.assertRaises(gmail.ApiError):oauth.exchange({'client_id':CLIENT_ID,'client_secret':CLIENT_SECRET},consent,'SYNTHETIC_CALLBACK_CODE',expected_account=ACCOUNT,http=fake)
            self.assertEqual(fake.calls,[])

    def test_malformed_query_expiry_and_reuse_do_not_exchange(self):
        now=[0];consent=oauth.Consent(CLIENT_ID,'http://127.0.0.1:12345/',clock=lambda:now[0])
        for target in ['/?state=%ZZ','/?state=%FF','/?state='+('x'*8193),'/?'+urlencode([('x',str(n)) for n in range(13)])]:
            with self.assertRaises(gmail.ApiError):consent.accept(target,'127.0.0.1:12345')
            self.assertFalse(consent.consumed)
        now[0]=oauth.CONSENT_SECONDS
        with self.assertRaisesRegex(gmail.ApiError,'CONSENT_EXPIRED_OR_USED'):consent.accept(self.callback('valid',consent.state)[0],'127.0.0.1:12345')
        fake=FakeHTTP([])
        with self.assertRaises(gmail.ApiError):oauth.exchange({},consent,'SYNTHETIC_CALLBACK_CODE',expected_account=ACCOUNT,http=fake)
        self.assertFalse(fake.calls)
        consent=oauth.Consent(CLIENT_ID,'http://127.0.0.1:12345/')
        consent.accept(self.callback('valid',consent.state)[0],'127.0.0.1:12345')
        with self.assertRaisesRegex(gmail.ApiError,'CONSENT_EXPIRED_OR_USED'):consent.accept(self.callback('valid',consent.state)[0],'127.0.0.1:12345')

    def test_existing_output_refuses_before_browser_and_stays_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'.config/brim-live-delta-ops';root.mkdir(parents=True,mode=0o700)
            output=root/'oauth.json';output.write_text('SYNTHETIC_EXISTING');output.chmod(0o600)
            browser=Mock();http=Mock()
            with self.assertRaises(oauth.SetupError) as error:oauth.authorize({},output,ACCOUNT,open_browser=browser,http_factory=http)
            self.assertEqual((error.exception.stage,error.exception.code),('OUTPUT','OUTPUT_EXISTS'))
            self.assertEqual(output.read_text(),'SYNTHETIC_EXISTING');browser.assert_not_called();http.assert_not_called()


class BuilderIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def build(self, batch, pdf):
        fetch = Mock(return_value=pdf)
        selected = gmail.select_retrieved(batch, fetch, as_of=AS_OF)
        if selected['record'] is not None:
            gmail.run_builder(selected['record'], as_of=AS_OF, out_dir=self.root/'docs/data')
        return selected, fetch

    def test_verified_email_selected_through_real_builder(self):
        raw, keys = signed_message()
        batch = gmail.retrieve(config(), as_of=AS_OF, http=mailbox([envelope(raw)]), keys=keys)
        selected = gmail.select_retrieved(batch, Mock(side_effect=AssertionError('no PDF needed')), as_of=AS_OF)
        gmail.run_builder(selected['record'], as_of=AS_OF, out_dir=self.root/'docs/data', verification=batch.bindings[0])
        summary = json.loads((self.root/'docs/data/delta_ops_daily_summary_summary.json').read_text())
        self.assertEqual(summary['source_kind'], 'email')
        self.assertFalse(summary['review_only'])
        self.assertEqual(summary['source_authentication'], gmail.AUTH_METHOD)
        self.assertEqual(summary['x2_position_date'], '2026-10-14')
        self.assertEqual(summary['export_total_cfs'], 3700)
        self.assertEqual(len(list((self.root/'docs/data').iterdir())), 4)
        # A standalone caller cannot elevate public method/flags into evidence.
        checked = subprocess.run([sys.executable,'scripts/delta_ops_publisher.py','validate','--root',str(self.root)],cwd=REPO,capture_output=True)
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn(b'UNVERIFIED_SIGNATURE', checked.stderr)

    def test_handoff_and_publisher_cannot_promote_email_by_flipping_review_flag(self):
        with self.assertRaisesRegex(gmail.ApiError, 'UNVERIFIED_SIGNATURE'):
            gmail.run_builder(record(),as_of=AS_OF,out_dir=self.root/'docs/data')
        review=record();review['review_only']=True
        gmail.run_builder(review,as_of=AS_OF,out_dir=self.root/'docs/data')
        for path in (self.root/'docs/data').iterdir():
            if path.name.endswith('reference.geojson'):continue
            document=json.loads(path.read_text())
            objects=[f['properties'] for f in document['features']] if 'features' in document else [document]
            for value in objects:
                value['review_only']=False
                value['source_authentication']=gmail.AUTH_METHOD
            path.write_text(json.dumps(document))
        for phase in ['validate-candidate','validate-staged']:
            result=subprocess.run([sys.executable,'scripts/delta_ops_publisher.py'],cwd=REPO,
                 env=dict(os.environ,BRIM_PUBLISH_PHASE=phase,BRIM_PUBLISH_CANDIDATE_ROOT=str(self.root),BRIM_PUBLISH_WORKTREE=str(self.root)),capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn(b'UNVERIFIED_SIGNATURE',result.stderr)

    def test_gmail_down_and_trust_hold_select_pdf_through_real_builder(self):
        for batch in [gmail.Retrieval('RETRIEVAL_UNAVAILABLE','AUTH_EXPIRED'), gmail.retrieve(config(),as_of=AS_OF,http=mailbox())]:
            chosen, fetch = self.build(batch, record(kind='pdf'))
            self.assertEqual(chosen['decision'],'pdf_recovery')
            fetch.assert_called_once_with()
            summary=json.loads((self.root/'docs/data/delta_ops_daily_summary_summary.json').read_text())
            self.assertEqual(summary['source_kind'],'pdf')

    def test_pdf_fallback_bridge_uses_existing_transport_once_and_real_builder(self):
        import test_delta_ops_pdf_integration as pdf_fixture
        fixture = pdf_fixture.DeltaPdfIntegrationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        as_of = datetime(2026, 9, 8, 19, tzinfo=timezone.utc)
        batch = gmail.retrieve(config(), as_of=as_of, http=FakeHTTP([gmail.ApiError('AUTH_EXPIRED')]))
        with pdf_fixture.server([(503, 'text/plain', b'SYNTHETIC_UPSTREAM_ERROR', {}),
                                 (200, 'application/pdf', fixture.valid, {})]) as (url, calls):
            with patch.dict(os.environ, {'DELTA_OPS_SUMMARY_PDF_URL': url+'?private=SYNTHETIC_QUERY',
                                        'DELTA_OPS_GMAIL_REFRESH_TOKEN': REFRESH}):
                fetch = Mock(wraps=gmail.fetch_pdf_record)
                selected = gmail.select_retrieved(batch, fetch, as_of=as_of)
                fetch.assert_called_once_with()
                self.assertEqual(selected['decision'], 'pdf_recovery')
                self.assertEqual(len(calls), 2)  # Only the unchanged transport retries.
                gmail.run_builder(selected['record'], as_of=as_of, out_dir=self.root/'docs/data')
        summary = json.loads((self.root/'docs/data/delta_ops_daily_summary_summary.json').read_text())
        self.assertEqual(summary['report_date'], '2026-09-08')
        self.assertEqual(summary['source_kind'], 'pdf')
        artifacts = b''.join(p.read_bytes() for p in (self.root/'docs/data').iterdir())
        for private in [REFRESH, 'SYNTHETIC_QUERY', 'SYNTHETIC_UPSTREAM_ERROR']:
            self.assertNotIn(private.encode(), artifacts)
        checked = subprocess.run([sys.executable, 'scripts/delta_ops_publisher.py', 'validate',
                                  '--root', str(self.root)], cwd=REPO, capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_conflict_failure_and_same_date_noop_preserve_bytes(self):
        self.build(gmail.Retrieval('UNCONFIGURED','DISABLED'),record(kind='pdf'))
        snapshot=lambda:{p.name:p.read_bytes() for p in (self.root/'docs/data').iterdir()}
        before=snapshot()
        deliveries=tuple(email_adapter.Delivery(record(changes=changes),trusted=True) for changes in [None,{'jones_pumping_plant':'2701 cfs'}])
        chosen,_=self.build(gmail.Retrieval('VERIFIED_ELIGIBLE','SYNTHETIC_VERIFIED_BOUNDARY',deliveries),record(kind='pdf'))
        self.assertEqual(chosen['decision'],'failure_retained');self.assertEqual(snapshot(),before)
        # Existing same-day precheck in the real builder must preserve byte identity.
        gmail.run_builder(record(changes={'jones_pumping_plant':'2701 cfs'},kind='pdf'),as_of=AS_OF,out_dir=self.root/'docs/data')
        self.assertEqual(snapshot(),before)

    def test_verified_email_artifact_through_synthetic_git_publisher_and_replay_noop(self):
        import test_main_publisher as fixture
        case=fixture.MainPublisherTests();case.setUp();self.addCleanup(case.tearDown)
        for name in ['delta_ops_publisher.py','delta_ops_email.py','main_publisher.py']:
            shutil.copyfile(REPO/'scripts'/name,case.runner/'scripts'/name)
        for command in [['git','config','user.name','Synthetic Delta Test'],['git','config','user.email','test@example.invalid'],
                        ['git','add','scripts'],['git','commit','-m','Synthetic Delta callbacks'],['git','push','origin','main']]:
            fixture.run(command,cwd=case.runner)
        case.source_sha=fixture.run(['git','rev-parse','HEAD'],cwd=case.runner).stdout.strip()
        raw,keys=signed_message()
        batch=gmail.retrieve(config(),as_of=AS_OF,http=mailbox([envelope(raw)]),keys=keys)
        selected=gmail.select_retrieved(batch,Mock(side_effect=AssertionError()),as_of=AS_OF)
        with patch.dict(os.environ,{'GITHUB_SHA':case.source_sha}):
            gmail.run_builder(selected['record'],as_of=AS_OF,out_dir=case.candidate/'docs/data',
                              verification=batch.bindings[0],artifact_metadata=case.metadata)
        from delta_ops_publisher import ALLOWLIST,PRODUCT_ID
        self.assertEqual(set(p.relative_to(case.artifact).as_posix() for p in case.artifact.rglob('*') if p.is_file()),
                         {'candidate-metadata.json',*('candidate/'+str(p) for p in ALLOWLIST)})
        exported=b''.join(p.read_bytes() for p in case.artifact.rglob('*') if p.is_file())
        for private in [b'DKIM-Signature:',b'._domainkey.',SENDER.encode(),ACCOUNT.encode(),CANARY.encode(),TOKEN.encode()]:
            self.assertNotIn(private,exported)
        callback=case.runner/'scripts/delta_ops_publisher.py'
        args=[sys.executable,str(fixture.PUBLISHER),'publish','--repo',str(case.runner),'--candidate-root',str(case.candidate),
              '--metadata',str(case.metadata),'--product-id',PRODUCT_ID,'--commit-subject','Synthetic Delta update']
        for p in ALLOWLIST:args.extend(['--allowlist',str(p)])
        for flag in ['--candidate-validator','--reconcile-callback','--staged-validator']:args.extend([flag,str(callback)])
        result=fixture.run(args,cwd=REPO,env=case.publisher_environment(),check=False)
        self.assertEqual(result.returncode,0,result.stderr)
        before={str(p):case.remote_file(str(p)) for p in ALLOWLIST}
        replay=fixture.run(args,cwd=REPO,env=case.publisher_environment(),check=False)
        self.assertEqual(replay.returncode,0,replay.stderr)
        self.assertEqual(before,{str(p):case.remote_file(str(p)) for p in ALLOWLIST})
        self.assertEqual(case.remote_file('unrelated.txt'),b'preserve me\n')
        older,older_keys=signed_message(report(day='2026-10-14'))
        older_batch=gmail.retrieve(config(),as_of=AS_OF,http=mailbox([envelope(older)]),keys=older_keys)
        older_choice=gmail.select_retrieved(older_batch,lambda:None,as_of=AS_OF)
        self.assertEqual(older_choice['decision'],'accepted_lagging_email')
        older_candidate=case.root/'older-artifact/candidate';older_metadata=older_candidate.parent/'candidate-metadata.json'
        with patch.dict(os.environ,{'GITHUB_SHA':case.source_sha}):
            gmail.run_builder(older_choice['record'],as_of=AS_OF,out_dir=older_candidate/'docs/data',
                              verification=older_batch.bindings[0],artifact_metadata=older_metadata)
        older_args=[str(older_candidate) if x==str(case.candidate) else str(older_metadata) if x==str(case.metadata) else x for x in args]
        older_result=fixture.run(older_args,cwd=REPO,env=case.publisher_environment(),check=False)
        self.assertEqual(older_result.returncode,0,older_result.stderr)
        self.assertEqual(before,{str(p):case.remote_file(str(p)) for p in ALLOWLIST})
        # Artifact corruption cannot be rescued by the public method discriminator.
        summary=case.candidate/'docs/data/delta_ops_daily_summary_summary.json'
        summary.write_text(summary.read_text()+' ')
        rejected=fixture.run(args,cwd=REPO,env=case.publisher_environment(),check=False)
        self.assertNotEqual(rejected.returncode,0)
        self.assertEqual(before,{str(p):case.remote_file(str(p)) for p in ALLOWLIST})

    def test_review_marker_preserved_and_secrets_absent_from_child_and_outputs(self):
        review=record();review['review_only']=True
        with patch.dict(os.environ, {'DELTA_OPS_GMAIL_CLIENT_SECRET':CLIENT_SECRET,'DELTA_OPS_GMAIL_REFRESH_TOKEN':REFRESH}):
            self.assertFalse(any(k.startswith('DELTA_OPS_GMAIL_') for k in gmail.child_environment()))
            gmail.run_builder(review,as_of=AS_OF,out_dir=self.root/'docs/data')
        all_bytes=b''.join(p.read_bytes() for p in (self.root/'docs/data').iterdir())
        for value in [TOKEN,REFRESH,CLIENT_SECRET,ACCOUNT,SENDER,CANARY]:self.assertNotIn(value.encode(),all_bytes)
        checked=subprocess.run([sys.executable,'scripts/delta_ops_publisher.py','validate','--root',str(self.root)],cwd=REPO,capture_output=True)
        self.assertNotEqual(checked.returncode,0)
        with patch.dict(os.environ, {'GITHUB_ACTIONS':'true'}):
            with self.assertRaises(gmail.ApiError):gmail.run_builder(review,as_of=AS_OF,out_dir=self.root/'other/docs/data')
        with self.assertRaises(gmail.ApiError):gmail.run_builder(review,as_of=AS_OF,out_dir=REPO/'docs/data')


@lru_cache(maxsize=4)
def synthetic_key(bits=2048, generation=1):
    """Generate ephemeral TEST-ONLY RSA material; never a credential fixture."""
    with tempfile.TemporaryDirectory(prefix='synthetic-dkim-test-') as directory:
        key = Path(directory)/'SYNTHETIC_TEST_ONLY.pem'
        subprocess.run(['openssl','genrsa','-out',str(key),str(bits)],check=True,capture_output=True,timeout=10)
        public = subprocess.run(['openssl','rsa','-in',str(key),'-pubout','-outform','DER'],check=True,capture_output=True,timeout=10).stdout
        return key.read_bytes(), b'v=DKIM1; k=rsa; p='+base64.b64encode(public)


def signed_message(text=None, *, domain=b'public.govdelivery.com', sender=SENDER, include=None,
                   length=False, algorithm=b'rsa-sha256', bits=2048, generation=1, selector=b'synthetic'):
    import dkim
    private, public = synthetic_key(bits, generation)
    text = report() if text is None else text
    day = email_adapter.heading_date(text)
    m = message(text, subject='Delta Operations Summary for '+day.strftime('%B %d, %Y'))
    m.replace_header('From', sender)
    raw = m.as_bytes(policy=policy.SMTP)
    include = include if include is not None else [b'from',b'subject',b'content-type',b'mime-version',b'content-transfer-encoding']
    signature = dkim.sign(raw, selector, domain, private, include_headers=include, length=length, signature_algorithm=algorithm)
    keys = gmail.SavedKeys({(selector+b'._domainkey.public.govdelivery.com').decode():public})
    return signature+raw, keys


class SignatureTests(unittest.TestCase):
    def test_approved_signature_and_copied_receiver_claims(self):
        raw, keys = signed_message()
        self.assertEqual(gmail.receiver_verdict(raw, keys=keys), (True,'DKIM_VERIFIED'))
        self.assertTrue(gmail.receiver_verdict(b'Authentication-Results: mx.google.com; dkim=pass\r\n'+raw, keys=keys)[0])
        unsigned = raw[raw.index(b'From:'):]
        self.assertFalse(gmail.receiver_verdict(unsigned, keys=keys)[0])

    def test_wrong_sender_domain_irrelevant_first_and_multiple_signatures(self):
        for kwargs in [{'sender':'Other@public.govdelivery.com'}, {'sender':'DWR@sub.public.govdelivery.com'},
                       {'domain':b'messagingfabric.com'}, {'domain':b'sub.public.govdelivery.com'}]:
            raw, keys = signed_message(**kwargs)
            self.assertFalse(gmail.receiver_verdict(raw, keys=keys)[0])
        raw, keys = signed_message()
        prefix=b'DKIM-Signature: v=1; d=unrelated.invalid; broken\r\n'
        self.assertTrue(gmail.receiver_verdict(prefix+raw, keys=keys)[0])
        header=raw[:raw.index(b'From:')]
        self.assertTrue(gmail.receiver_verdict(header+raw,keys=keys)[0])

    def test_body_changes_appends_and_body_length_signatures_reject(self):
        raw, keys = signed_message()
        for changed in [raw.replace(b'2,700',b'2,701'),raw+b'UNSIGNED APPENDED REPORT\r\n']:
            self.assertFalse(gmail.receiver_verdict(changed,keys=keys)[0])
        raw, keys = signed_message(length=True)
        self.assertEqual(gmail.receiver_verdict(raw,keys=keys)[1],'DKIM_ALGORITHM_OR_BODY_LIMIT_REJECTED')

    def test_header_coverage_duplicates_and_signed_mime(self):
        headers=[b'from',b'subject',b'content-type',b'mime-version',b'content-transfer-encoding']
        for missing in headers:
            if missing==b'from':continue  # dkimpy itself refuses signing without From.
            raw, keys = signed_message(include=[h for h in headers if h!=missing])
            self.assertEqual(gmail.receiver_verdict(raw,keys=keys)[1],'DKIM_REQUIRED_HEADER_UNSIGNED')
        raw, keys=signed_message()
        for extra in [b'From: '+SENDER.encode()+b'\r\n',b'Subject: injected\r\n',b'Content-Type: text/html\r\n',
                      b'MIME-Version: 1.0\r\n',b'Content-Transfer-Encoding: base64\r\n',b'Resent-From: relay@example.invalid\r\n']:
            self.assertFalse(gmail.receiver_verdict(extra+raw,keys=keys)[0])
        self.assertFalse(gmail.receiver_verdict(raw.replace(b'Content-Type:',b'Broken-Content-Type:',1),keys=keys)[0])

    def test_algorithm_key_size_key_policy_rotation_and_unavailable_keys(self):
        for kwargs in [{'algorithm':b'rsa-sha1'},{'bits':512}]:
            raw,keys=signed_message(**kwargs)
            self.assertFalse(gmail.receiver_verdict(raw,keys=keys)[0])
        raw,keys=signed_message()
        self.assertFalse(gmail.receiver_verdict(raw,keys=gmail.SavedKeys({}))[0])
        name=next(iter(keys.cache));public=keys.cache[name]
        for bad in [b'v=DKIM1; p=',public+b'; h=sha1',public+b'; s=tlsrpt',public+b'; t=y',b'not a key']:
            self.assertFalse(gmail.receiver_verdict(raw,keys=gmail.SavedKeys({name:bad}))[0])
        newer,new_keys=signed_message(generation=2)
        self.assertFalse(gmail.receiver_verdict(raw,keys=new_keys)[0])
        self.assertTrue(gmail.receiver_verdict(newer,keys=new_keys)[0])
        self.assertFalse(gmail.receiver_verdict(newer,keys=keys)[0])

    def test_binding_prevents_record_substitution_and_saved_replay_stays_review(self):
        raw,keys=signed_message()
        batch=gmail.retrieve(config(),as_of=AS_OF,http=mailbox([envelope(raw)]),keys=keys)
        self.assertEqual(batch.state,'VERIFIED_ELIGIBLE')
        changed=copy.deepcopy(batch.deliveries[0].record);changed['values']['jones_pumping_plant']='2701 cfs'
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(gmail.ApiError,'UNVERIFIED_SIGNATURE'):
                gmail.run_builder(changed,as_of=AS_OF,out_dir=Path(directory)/'out',verification=batch.bindings[0])
            source=Path(directory)/'synthetic.eml';source.write_bytes(raw)
            output=Path(directory)/'review.json'
            result=subprocess.run([sys.executable,'scripts/delta_ops_email.py','replay','--input',str(source),'--output',str(output),'--candidate-dir',directory],cwd=REPO,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(json.loads(output.read_text())['review_only'])

    def test_missing_dependencies_and_untrusted_messages_cannot_veto(self):
        with patch.object(gmail,'verification_modules',side_effect=gmail.ApiError('VERIFIER_DEPENDENCIES_UNAVAILABLE')):
            http=FakeHTTP([]);batch=gmail.retrieve(config(),as_of=AS_OF,http=http)
            self.assertEqual(batch.state,'RETRIEVAL_UNAVAILABLE');self.assertFalse(http.calls)
            chosen=gmail.select_retrieved(batch,lambda:record(kind='pdf'),as_of=AS_OF)
            self.assertEqual(chosen['decision'],'pdf_recovery')
        good,keys=signed_message()
        bad,_=signed_message(report(changes={'jones_pumping_plant':'invalid'}),sender='forged@example.invalid')
        batch=gmail.retrieve(config(),as_of=AS_OF,http=mailbox([envelope(good),envelope(bad,'synthetic2')]),keys=keys)
        self.assertEqual(gmail.select_retrieved(batch,Mock(side_effect=AssertionError()),as_of=AS_OF)['decision'],'email_primary')


class HTTPFramingAndBackoffTests(unittest.TestCase):
    def test_retry_after_never_shortens_valid_delay(self):
        now=AS_OF.timestamp()
        for value in [['120'],[format_datetime(AS_OF+timedelta(seconds=120),usegmt=True)],['1','2'],['garbage']]:
            sleep=Mock();client=gmail.BoundedHTTP(sleep=sleep,wall_clock=lambda:now)
            with patch.object(client,'_one',return_value=(503,value,b'')) as call:
                with self.assertRaises(gmail.ApiError):client.request('GET',gmail.API+'/profile')
                call.assert_called_once();sleep.assert_not_called()
        for value,delay in [([],1),(['2'],2),([format_datetime(AS_OF+timedelta(seconds=2),usegmt=True)],2),
                            ([format_datetime(AS_OF-timedelta(seconds=1),usegmt=True)],0)]:
            sleep=Mock();client=gmail.BoundedHTTP(sleep=sleep,wall_clock=lambda:now)
            with patch.object(client,'_one',side_effect=[(503,value,b''),(200,[],b'{}')]):
                self.assertEqual(client.request('GET',gmail.API+'/profile'),{})
            sleep.assert_called_once_with(delay)
        client=gmail.BoundedHTTP(clock=lambda:0,sleep=Mock());client.deadline=1
        with patch.object(client,'_one',return_value=(429,['2'],b'')) as call:
            with self.assertRaisesRegex(gmail.ApiError,'REQUEST_BUDGET'):client.request('GET',gmail.API+'/profile')
            call.assert_called_once()

    def wire(self, raw):
        class Socket:
            def makefile(self,*_):return io.BytesIO(raw)
        response=gmail.StrictHTTPResponse(Socket());response.begin()
        connection=Mock();connection.sock=None;connection.getresponse.return_value=response
        with patch.object(gmail.http.client,'HTTPSConnection',return_value=connection):
            return gmail.BoundedHTTP()._one('GET',gmail.API+'/profile',{},None,100)

    def test_real_httpresponse_premature_content_length_and_conflicting_framing(self):
        for headers in [b'Content-Length: 50\r\n',b'Content-Length: 2\r\nContent-Length: 50\r\n',
                        b'Content-Length: 2\r\nTransfer-Encoding: chunked\r\n',b'Content-Length: nonsense\r\n']:
            with self.assertRaises(gmail.ApiError):self.wire(b'HTTP/1.1 200 OK\r\n'+headers+b'\r\n{}')
        self.assertEqual(self.wire(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}')[2],b'{}')

    def test_real_httpresponse_chunk_termination_and_interruption(self):
        prefix=b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n'
        for body in [b'2\r\n{}',b'2\r\n{}\r\n0\r\n',b'2\r\n{}XX0\r\n\r\n',b'3\r\n{}',b'2\r\n{}\r\nZ\r\n\r\n',b'2\n{}\r\n0\r\n\r\n']:
            with self.assertRaises(gmail.ApiError):self.wire(prefix+body)
        self.assertEqual(self.wire(prefix+b'2\r\n{}\r\n0\r\n\r\n')[2],b'{}')


class DNSBudgetTests(unittest.TestCase):
    def test_cache_names_query_tcp_fallback_and_limits(self):
        _,dns=gmail.verification_modules()
        resolver=gmail.KeyResolver()
        name=b'synthetic._domainkey.public.govdelivery.com.'
        question=dns.message.make_query(name.decode(),'TXT');response=dns.message.make_response(question)
        response.answer.append(dns.rrset.from_text(name.decode(),60,'IN','TXT','"v=DKIM1; p=SYNTHETIC"'))
        response.index = None
        with patch.object(dns.query,'udp',side_effect=dns.message.Truncated()),patch.object(dns.query,'tcp',return_value=response) as tcp:
            self.assertEqual(resolver(name),b'v=DKIM1; p=SYNTHETIC')
            self.assertEqual(resolver(name),b'v=DKIM1; p=SYNTHETIC')
            self.assertEqual(resolver.queries,2);tcp.assert_called_once()
            self.assertLessEqual(tcp.call_args.kwargs['timeout'],5)
        for bad in [b'other.example.invalid',b'../x._domainkey.public.govdelivery.com',b'x._domainkey.sub.public.govdelivery.com']:
            with self.assertRaises(gmail.ApiError):resolver(bad)
        resolver.cache={f's{i}._domainkey.public.govdelivery.com':None for i in range(8)}
        with self.assertRaisesRegex(gmail.ApiError,'DNS_SELECTOR_BUDGET'):resolver(name)
        resolver.cache={};resolver.queries=16
        with self.assertRaisesRegex(gmail.ApiError,'DNS_BUDGET'):resolver(name)
        resolver.queries=0;resolver.deadline=time.monotonic()-1;resolver.cache={}
        with self.assertRaisesRegex(gmail.ApiError,'DNS_BUDGET'):resolver(name)


if __name__=='__main__':unittest.main()
