"""Offline localhost HTTP/Poppler/builder regression; all outputs are temporary."""

import contextlib
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


REPO = Path(__file__).resolve().parents[1]
BUILDER = r'''
builder <- parse("scripts/build_delta_ops_daily_summary.R")
args <- commandArgs(TRUE)
assigned <- function(expr, name) {
  is.call(expr) && identical(expr[[1]], as.name("<-")) &&
    identical(expr[[2]], as.name(name))
}
for (i in seq_along(builder)) {
  if (assigned(builder[[i]], "feed_build_time")) {
    builder[[i]][[3]] <- as.call(list(as.name("as.POSIXct"), args[[1]], tz = "UTC"))
  }
  if (length(args) > 1L && assigned(builder[[i]], "raw_text")) {
    # Baseline bypasses transport only: same Poppler input and complete parser.
    builder[[i]][[3]] <- substitute(paste(pdftools::pdf_text(path), collapse = "\n"),
                                   list(path = args[[2]]))
  }
}
eval(builder, new.env(parent = globalenv()))
'''


@contextlib.contextmanager
def server(responses):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, dict(self.headers)))
            status, mime, body, extra = responses[min(len(calls), len(responses)) - 1]
            self.send_response(status)
            if mime is not None:
                self.send_header("Content-Type", mime)
            for key, value in extra.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/source.pdf", calls
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


class DeltaPdfIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="delta-pdf-integration-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pdf = self.root / "synthetic.pdf"
        result = subprocess.run([
            "Rscript", "-e", r'''
lines <- readLines("tests/fixtures/delta_ops/dwr_2026-09-08.txt")
grDevices::pdf(commandArgs(TRUE)[1], width=8.5, height=11, family="Courier")
par(mar=c(0,0,0,0))
plot.new(); plot.window(xlim=c(0,1), ylim=c(0,1))
text(0.03, 0.97, paste(lines, collapse="\n"), adj=c(0,1), cex=0.65)
invisible(dev.off())
''', str(self.pdf),
        ], cwd=REPO, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.valid = self.pdf.read_bytes()

    def build(self, root, url, *, direct=False, day="2026-09-08", skip=False):
        env = {key: value for key, value in os.environ.items() if not key.startswith("DELTA_OPS_")}
        env.update(
            DELTA_OPS_OUT_DIR=str(root / "docs/data"),
            DELTA_OPS_SUMMARY_PDF_URL=url,
            DELTA_OPS_SKIP_IF_CURRENT_DATE=str(skip).lower(),
        )
        args = ["Rscript", "-e", BUILDER, f"{day} 19:00:19"]
        if direct:
            args.append(str(self.pdf))
        return subprocess.run(args, cwd=REPO, env=env, text=True, capture_output=True, timeout=45)

    @staticmethod
    def snapshot(root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def test_real_curl_redirect_missing_mime_and_unchanged_complete_product(self):
        with server([(302, None, b"", {"Location": "/final.pdf"}),
                     (200, None, self.valid, {})]) as (url, calls):
            baseline, candidate = self.root / "baseline", self.root / "candidate"
            direct = self.build(baseline, url, direct=True)
            built = self.build(candidate, url)
            self.assertEqual(direct.returncode, 0, direct.stderr)
            self.assertEqual(built.returncode, 0, built.stderr)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0][1]["Accept"], "application/pdf")
            self.assertIn("BRIM-live-data-feeds Delta-Ops", calls[0][1]["User-Agent"])
            self.assertEqual(self.snapshot(candidate), self.snapshot(baseline))
            self.assertEqual(len(self.snapshot(candidate)), 4)
            self.assertIn(f"final_url={url.replace('source.pdf', 'final.pdf')}", built.stderr)
            checked = subprocess.run(["python3", "scripts/delta_ops_publisher.py", "validate", "--root", str(candidate)],
                                     cwd=REPO, text=True, capture_output=True, timeout=30)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertIn("13 features for 2026-09-08", checked.stdout)

    def test_rejection_preserves_existing_product_and_creates_no_candidate_files(self):
        body = b"<html><title>Request Rejected</title>synthetic-body-marker</html>"
        with server([(200, "text/html", body, {})]) as (url, calls):
            prior = self.root / "prior"
            seeded = self.build(prior, url, direct=True)
            self.assertEqual(seeded.returncode, 0, seeded.stderr)
            before = self.snapshot(prior)
            for target in (prior, self.root / "empty-candidate"):
                failed = self.build(target, url)
                self.assertNotEqual(failed.returncode, 0)
                self.assertIn("DELTA_OPS_UPSTREAM_NON_PDF_RESPONSE", failed.stderr)
                self.assertIn("attempt=3/3", failed.stderr)
                self.assertNotIn("PDF error", failed.stderr)
                self.assertNotIn("synthetic-body-marker", failed.stderr)
            self.assertEqual(len(calls), 6)
            self.assertEqual(self.snapshot(prior), before)
            self.assertEqual(self.snapshot(self.root / "empty-candidate"), {})

    def test_partial_transport_then_valid_pdf_retries_real_curl_error(self):
        with server([(200, "application/pdf", b"%PDF-", {"Content-Length": "100"}),
                     (200, "application/pdf", self.valid, {})]) as (url, calls):
            built = self.build(self.root / "candidate", url)
            self.assertEqual(built.returncode, 0, built.stderr)
            self.assertEqual(len(calls), 2)
            self.assertIn("outcome=transport_failure", built.stderr)
            self.assertIn("attempt=2/3 outcome=pdf_validated", built.stderr)

    def test_same_report_date_precheck_and_post_download_noop_preserve_bytes(self):
        with server([(200, "application/pdf", self.valid, {})]) as (url, calls):
            candidate = self.root / "candidate"
            seeded = self.build(candidate, url, direct=True)
            self.assertEqual(seeded.returncode, 0, seeded.stderr)
            before = self.snapshot(candidate)
            for day, expected_calls in (("2026-09-08", 0), ("2026-09-09", 1)):
                noop = self.build(candidate, url, skip=True, day=day)
                self.assertEqual(noop.returncode, 0, noop.stderr)
                self.assertEqual(len(calls), expected_calls)
                self.assertEqual(self.snapshot(candidate), before)


if __name__ == "__main__":
    unittest.main()
