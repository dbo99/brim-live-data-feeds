"""Offline slot tests and real libcurl requests to an ephemeral loopback fixture."""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import snow_pillow_daily_gate as gate


class DailySlot(unittest.TestCase):
    def setUp(self):
        self.env = dict(SWE_CLEARANCE="schedule/2026-11-26/405/" + "a"*40 + "/" + "b"*40 + "/2026-11-26T22:10:00Z",
                        GITHUB_EVENT_NAME="schedule", SWE_CRON="20 22 * * *",
                        GITHUB_REPOSITORY="dbo99/brim-live-data-feeds", GITHUB_REF="refs/heads/main",
                        GITHUB_WORKFLOW_REF=f"dbo99/brim-live-data-feeds/{gate.WORKFLOW}@refs/heads/main",
                        NRCS_HOLD="false", GITHUB_RUN_ATTEMPT="1", GITHUB_RUN_NUMBER="405")
        self.now = datetime(2026, 11, 26, 22, 20, tzinfo=timezone.utc)

    def test_utc_windows(self):
        for phase in ("start", "acquire", "publish"):
            self.assertEqual(gate.slot(self.env, self.now, phase), ("a"*40, "b"*40))
        for minute, phase in ((19, "acquire"), (45, "acquire"), (50, "publish")):
            now = self.now.replace(hour=23 if phase == "publish" else 22, minute=minute)
            with self.assertRaises(ValueError):
                gate.slot(self.env, now, phase)
        with self.assertRaises(ValueError):
            gate.slot(self.env, self.now.replace(day=27), "acquire")

    def test_old_or_future_clearance(self):
        for stamp in ("21:55:00", "22:21:00"):
            env = {**self.env, "SWE_CLEARANCE": self.env["SWE_CLEARANCE"].replace("22:10:00", stamp)}
            with self.assertRaises(ValueError):
                gate.slot(env, self.now.replace(minute=30 if stamp == "21:55:00" else 20), "acquire")

    def test_refusal(self):
        for key, value in dict(SWE_CLEARANCE="", SWE_CRON="52 5,13,21 * * *", NRCS_HOLD="",
                               GITHUB_RUN_ATTEMPT="2", GITHUB_RUN_NUMBER="406",
                               GITHUB_REF="refs/heads/other", GITHUB_EVENT_NAME="workflow_dispatch",
                               GITHUB_WORKFLOW_REF="other").items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                gate.slot({**self.env, key: value}, self.now, "acquire")

    def test_shared_workflows_and_incomplete_queue(self):
        def response(row, count=1):
            return lambda path: {"workflow_runs": [row], "total_count": count}
        gate.nonconflict(12, response({"id": 12, "workflow_id": 288862661}))
        for workflow in gate.SHARED:
            with self.assertRaises(ValueError):
                gate.nonconflict(12, response({"id": 13, "workflow_id": workflow}))
        with self.assertRaises(ValueError):
            gate.nonconflict(12, response({"id": 12, "workflow_id": 288862661}, 2))


class NativeHTTP(unittest.TestCase):
    def test_curl_transport(self):
        seen = []

        class Fixture(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen.append(self.path)
                codes = {"/error": 500, "/redirect": 302, "/backoff": 503}
                self.send_response(codes.get(self.path, 200))
                body = b"[]"
                if self.path == "/redirect":
                    self.send_header("Location", "/unexpected")
                if self.path == "/backoff":
                    self.send_header("Retry-After", "600")
                if self.path in {"/large", "/chunked"}:
                    body = b"x"*(4*1024*1024+1)
                if self.path == "/chunked":
                    self.send_header("Transfer-Encoding", "chunked")
                    body = f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
                else:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Fixture) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                subprocess.run(["Rscript", "--vanilla", "tests/test_snow_pillow_daily_http.R",
                                f"http://127.0.0.1:{server.server_port}"], cwd=ROOT, check=True)
            finally:
                server.shutdown()
                worker.join()
        self.assertEqual(seen, ["/ok", "/error", "/redirect", "/backoff", "/large", "/chunked"])


if __name__ == "__main__":
    unittest.main()
