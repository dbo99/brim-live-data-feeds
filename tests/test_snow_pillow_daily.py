"""Offline slot tests and real libcurl requests to an ephemeral loopback fixture."""
from datetime import date, datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import tempfile
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import snow_pillow_daily_gate as gate


class DailySlot(unittest.TestCase):
    def setUp(self):
        self.env = dict(SWE_CLEARANCE="schedule/2026-11-26/2026-11-30/" + "a"*40 + "/" + "b"*40 + "/123",
                        GITHUB_EVENT_NAME="schedule", SWE_CRON="20 22 * * *",
                        GITHUB_REPOSITORY="dbo99/brim-live-data-feeds", GITHUB_REF="refs/heads/main",
                        GITHUB_WORKFLOW_REF=f"dbo99/brim-live-data-feeds/{gate.WORKFLOW}@refs/heads/main",
                        NRCS_HOLD="false", GITHUB_RUN_ATTEMPT="1")
        self.created = datetime(2026, 11, 27, 3, 30, tzinfo=timezone.utc)

    def test_delayed_event_uses_pacific_day(self):
        for phase in ("start", "acquire", "publish"):
            result = gate.slot(self.env, self.created, phase, self.created)
            self.assertEqual(result[-1], date(2026, 11, 26))

    def test_headroom_and_stale_day(self):
        midnight = self.created.replace(hour=8, minute=0)
        for phase, minutes in (("start", 80), ("acquire", 50), ("publish", 20)):
            with self.subTest(phase=phase), self.assertRaises(ValueError):
                gate.slot(self.env, midnight - timedelta(minutes=minutes), phase, self.created)
        with self.assertRaises(ValueError):
            gate.slot(self.env, midnight + timedelta(hours=17), "acquire", self.created)

    def test_expiry_identity_and_holds(self):
        for key, value in dict(SWE_CLEARANCE="", SWE_CRON="other", NRCS_HOLD="true",
                               GITHUB_RUN_ATTEMPT="2", GITHUB_EVENT_NAME="workflow_dispatch",
                               GITHUB_REF="refs/heads/other").items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                gate.slot({**self.env, key: value}, self.created, "acquire", self.created)
        for dates in ("2026-11-20/2026-11-23", "2026-11-26/2026-12-20"):
            term = self.env["SWE_CLEARANCE"].replace("2026-11-26/2026-11-30", dates)
            with self.assertRaises(ValueError):
                gate.slot({**self.env, "SWE_CLEARANCE":term}, self.created, "acquire", self.created)

    def test_revocation_checked_live(self):
        gate.check_enabled(lambda _: {"state": "active"})
        with self.assertRaises(ValueError):
            gate.check_enabled(lambda _: {"state": "disabled_manually"})

    def test_shared_acquirer_versus_publisher(self):
        run = dict(id=15, workflow_id=281942719, head_sha="x", run_attempt=1)
        job = dict(id=23, run_id=15, head_sha="x", run_attempt=1, name="prepare-candidate", status="completed", conclusion="success")
        jobs = [job, dict(job, id=24, name="publish-to-main", status="in_progress")]

        def get(path):
            if "/jobs?" in path:
                return dict(total_count=len(jobs), jobs=jobs)
            return dict(total_count=1, workflow_runs=[run])
        gate.nonconflict(13, get)
        job["status"] = "in_progress"
        with self.assertRaises(ValueError):
            gate.nonconflict(13, get)
        jobs.clear()
        with self.assertRaises(ValueError):
            gate.nonconflict(13, get)
        with self.assertRaises(ValueError):
            gate.nonconflict(13, lambda _: dict(total_count=1, workflow_runs=[]))


class GenerationChain(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(area.name)
        gate.git("init", "-q", "-b", "main")
        gate.git("config", "user.name", "Fixture")
        gate.git("config", "user.email", "fixture@example.invalid")
        gate.git("remote", "add", "origin", ".")
        path = Path(gate.SOURCE[0])
        path.parent.mkdir(parents=True)
        path.write_text("source fixture")
        for name in gate.PRODUCT:
            p = Path(name)
            p.parent.mkdir(parents=True, exist_ok=True)
            if name in (gate.PRODUCT[1], gate.PRODUCT[3]):
                p.write_text(json.dumps({"current_water_year": 2027, "fetch_end_date": "2026-11-25"}))
            else:
                p.write_text("product identity fixture")
        gate.git("add", ".")
        gate.git("commit", "-qm", "Accepted saved predecessor")
        self.anchor = gate.git("rev-parse", "HEAD")
        self.current = dict(id=57, run_number=409, run_attempt=1, head_sha=self.anchor,
                          head_branch="main", event="schedule", workflow_id=gate.SWE_ID,
                          created_at="2026-11-28T02:00:00Z", status="in_progress")
        self.prior = dict(self.current, id=32, run_number=408, status="completed", conclusion="success",
                        created_at="2026-11-27T01:00:00Z")
        self.anchor_run = dict(self.prior, id=31, run_number=407, created_at="2026-11-25T22:20:00Z")
        self.prep = dict(id=11, run_id=32, head_sha=self.anchor, run_attempt=1, status="completed",
                       name="prepare-candidate", conclusion="success",
                       steps=[dict(name=gate.BUILD, conclusion="success")])
        self.pub = dict(self.prep, id=12, name="publish-to-main", steps=[])
        self.artifacts = []

    def get(self, path):
        if "/artifacts?" in path:
            return dict(total_count=len(self.artifacts), artifacts=self.artifacts)
        return dict(total_count=2, jobs=[self.prep, self.pub])

    def history(self):
        return gate.generation(self.anchor, self.anchor,
                               [self.anchor_run, self.prior, self.current],
                               self.current, self.anchor_run, self.get)

    def test_predecessor_successor_and_drift(self):
        day = date(2026, 11, 26)
        gate.predecessor(self.anchor, self.anchor, self.anchor, day)
        for stale in (day - timedelta(days=1), date(2027, 10, 1)):
            with self.assertRaises(ValueError):
                gate.predecessor(self.anchor, self.anchor, self.anchor, stale)
        for f in (gate.PRODUCT[1], gate.PRODUCT[3]):
            data = json.loads(Path(f).read_text())
            data["fetch_end_date"] = day.isoformat()
            Path(f).write_text(json.dumps(data))
        gate.git("commit", "-qam", "Update snow pillow SWE latest feed (run 32)")
        published = gate.git("rev-parse", "HEAD")
        self.artifacts = [dict(id=100, name=gate.RECEIPT+published, expired=False)]
        self.assertEqual(self.history(), published)
        gate.predecessor(self.anchor, published, published, day + timedelta(days=1))
        with self.assertRaises(ValueError):
            gate.predecessor(self.anchor, published, published, day)
        with self.assertRaises(ValueError):
            gate.predecessor(self.anchor, self.anchor, published, day + timedelta(days=1))
        Path(gate.SOURCE[0]).write_text("drift")
        gate.git("commit", "-qam", "Source drift")
        with self.assertRaises(subprocess.CalledProcessError):
            gate.predecessor(self.anchor, published, gate.git("rev-parse", "HEAD"), day + timedelta(days=1))

    def test_normal_entry_points_with_late_event(self):
        now = datetime(2026, 11, 28, 2, 15, tzinfo=timezone.utc)
        current = dict(self.current, run_number=408)
        env = dict(GITHUB_SHA=self.anchor, GITHUB_RUN_ID=str(current["id"]),
                   SWE_CLEARANCE=f"schedule/2026-11-27/2026-11-30/{self.anchor}/{self.anchor}/31",
                   GITHUB_EVENT_NAME="schedule", SWE_CRON="20 22 * * *",
                   GITHUB_REPOSITORY="dbo99/brim-live-data-feeds", GITHUB_REF="refs/heads/main",
                   GITHUB_WORKFLOW_REF=f"dbo99/brim-live-data-feeds/{gate.WORKFLOW}@refs/heads/main",
                   NRCS_HOLD="false", GITHUB_RUN_ATTEMPT="1", GITHUB_ENV="runner.env",
                   GITHUB_OUTPUT="runner.out", RUNNER_TEMP=".")

        def get(path):
            if path == "actions/runs/57":
                return current
            if path == "actions/runs/31":
                return self.anchor_run
            if path.startswith(f"actions/workflows/{gate.SWE_ID}/runs?"):
                return dict(total_count=2, workflow_runs=[self.anchor_run, current])
            if path.startswith("actions/runs?"):
                return dict(total_count=0, workflow_runs=[])
            if path == f"actions/workflows/{gate.SWE_ID}":
                return dict(state="active")
            self.fail(f"Unexpected API path {path}")
        with patch.dict(os.environ, env), patch.object(gate, "github", get), \
                patch.object(gate, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            for phase in ("start", "acquire", "publish"):
                gate.main(phase)
            self.assertEqual(Path("runner.env").read_text().splitlines(),
                             ["SNOW_PILLOW_FETCH_END_DATE=2026-11-27",
                              f"SWE_PREDECESSOR_GENERATION={self.anchor}"])
            with patch.dict(os.environ, SWE_PREDECESSOR_GENERATION=self.anchor):
                gate.main("receipt")
            self.assertEqual(Path("swe-generation.txt").read_text().strip(), self.anchor)

    def test_missing_historical_run_refused(self):
        self.current["run_number"] += 1
        with self.assertRaisesRegex(ValueError, "Incomplete SWE history"):
            self.history()

    def test_noop_spends_once_and_keeps_last_good(self):
        self.artifacts = [dict(id=100, name=gate.RECEIPT + self.anchor, expired=False)]
        self.assertEqual(self.history(), self.anchor)
        self.current["created_at"] = self.prior["created_at"]
        with self.assertRaisesRegex(ValueError, "already acquired"):
            self.history()

    def test_incomplete_receipt_and_interrupted_spending(self):
        with self.assertRaises(ValueError):
            self.history()
        self.pub["conclusion"] = "skipped"
        self.prior["conclusion"] = "failure"
        self.prep["steps"][0]["conclusion"] = "cancelled"
        with self.assertRaises(ValueError):
            self.history()
        self.prep["steps"][0]["conclusion"] = "skipped"
        self.assertEqual(self.history(), self.anchor)


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
