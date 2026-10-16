"""Synthetic, offline Delta content/selection and real-builder seam regressions."""

import copy
from datetime import date, datetime, timezone
from email.message import EmailMessage
from html import escape
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import delta_ops_email as adapter

CANARY = "SYNTHETIC_PRIVATE_CANARY"
RAW = {
    "clifton_court_inflow": "1,000 cfs", "jones_pumping_plant": "2,700 cfs",
    "total_delta_inflow": "~ 11,833 cfs", "sacramento_river": "9,785 cfs",
    "san_joaquin_river": "1,084 cfs", "delta_conditions": "Balanced",
    "cross_channel_gates_percent_open": "100%", "outflow_index": "~ 6,100 cfs",
    "percent_inflow_diverted": "30.2 (3-day avg)", "x2_position_yesterday": "> 81 km",
    "controlling_factors": "Delta WQ", "omr_index_daily_value": "-3,500 cfs",
    "shasta_storage": "2,376 TAF", "folsom_storage": "404 TAF", "oroville_storage": "1,533 TAF",
    "san_luis_total_storage": "1,465 TAF", "san_luis_swp_share": "983 TAF",
}


def report(day="2026-10-15", changes=None, *, wrapped=False):
    values = dict(RAW, **(changes or {}))
    lines = ["PRELIMINARY DATA", "EXECUTIVE OPERATIONS SUMMARY ON " + date.fromisoformat(day).strftime("%m/%d/%Y")]
    for key, label in adapter.LABELS.items():
        value = values[key]
        if value is None:
            continue
        op = "="
        if value.startswith(("=", ">", "<", "≤", "≥", "~", "≈")):
            op, value = value.split(" ", 1)
        lines.append(("\n" if wrapped else " ").join([label, op, value]))
    lines.extend(["RESERVOIR RELEASES", "Keswick = 6,000 cfs"])
    return "\n".join(lines)


def html_report(text):
    return "<html><body><table>" + "".join("<tr><td>" + escape(line) + "</td></tr>" for line in text.splitlines()) + "</table></body></html>"


def message(text=None, html=None, *, subject="Delta Operations Summary for October 15, 2026", charset="utf-8", cte="quoted-printable"):
    msg = EmailMessage()
    msg["From"] = "Synthetic Bulletin <bulletin@example.invalid>"
    msg["To"] = CANARY + "@example.invalid"
    msg["Message-ID"] = "<" + CANARY + "@example.invalid>"
    msg["Authentication-Results"] = "synthetic.invalid; dkim=pass; spf=pass; dmarc=pass"
    msg["Subject"] = subject
    msg["Date"] = "Fri, 16 Oct 2026 01:00:00 +0000"
    if text is not None:
        msg.set_content(text, charset=charset, cte=cte)
        if html is not None:
            msg.add_alternative(html, subtype="html", charset=charset, cte=cte)
    else:
        msg.set_content(html, subtype="html", charset=charset, cte=cte)
    return msg


def record(day="2026-10-15", changes=None, kind="email"):
    return adapter.parse_report(report(day, changes), source_kind=kind)


class ContentTests(unittest.TestCase):
    def test_plain_html_and_alternatives_agree_without_resource_access(self):
        text = report(wrapped=True)
        html = html_report(text).replace("</body>", f'<img src="https://example.invalid/{CANARY}"><script>{CANARY}</script></body>')
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            parsed = [adapter.parse_message(m.as_bytes()) for m in [message(text), message(html=html), message(text, html)]]
        self.assertEqual(len({adapter.semantic_key(x) for x in parsed}), 1)
        for value in parsed:
            self.assertEqual(value["numeric_values"]["percent_inflow_diverted"], 30.2)
            self.assertEqual(value["x2"]["relation"], ">")
            self.assertNotIn(CANARY, json.dumps(value))

    def test_encodings_charsets_and_separated_operator_cells(self):
        for charset in ["utf-8", "iso-8859-1"]:
            for cte in ["base64", "quoted-printable", "8bit"]:
                with self.subTest(charset=charset, cte=cte):
                    self.assertEqual(adapter.parse_message(message(report(wrapped=True), charset=charset, cte=cte).as_bytes())["report_date"], "2026-10-15")
        body = html_report(report(wrapped=True)).replace("&gt;", "&gt;\n=").replace("81 km", "81.250001\u00a0km")
        result = adapter.parse_message(message(html=body).as_bytes())
        self.assertEqual(result["x2"], {"km": 81.250001, "relation": ">=", "value_raw": ">= 81.250001 km"})

    def test_percentages_signs_zero_units_and_precision(self):
        unicode_pdf = report().replace("x-channel", "x−channel").replace("3-day", "3−day").replace("-3,500", "−3,500")
        normalized = adapter.parse_report(unicode_pdf, source_kind="pdf")
        self.assertEqual(normalized["numeric_values"]["percent_inflow_diverted"], 30.2)
        self.assertEqual(normalized["numeric_values"]["omr_index_daily_value"], -3500)
        for value, expected in [("30.2 (3-day avg)", 30.2), ("59.5 (3-day avg)", 59.5), ("0.302% (3-day avg)", .302)]:
            parsed = record(changes={"percent_inflow_diverted": value, "omr_index_daily_value": "−3500.123456 cfs", "clifton_court_inflow": "0 cfs"})
            self.assertEqual(parsed["numeric_values"]["percent_inflow_diverted"], expected)
            self.assertEqual(parsed["numeric_values"]["omr_index_daily_value"], -3500.123456)
            self.assertEqual(parsed["numeric_values"]["clifton_court_inflow"], 0)
            self.assertTrue(parsed["values"]["total_delta_inflow"].startswith("~"))
        for value in ["NaN cfs", "1,00 cfs", "3 TAF", "", "1e309 cfs", "3 cfs " + CANARY]:
            with self.subTest(value=value), self.assertRaises(adapter.ReportError):
                record(changes={"jones_pumping_plant": value})
        with self.assertRaises(adapter.ReportError):
            record(changes={"cross_channel_gates_percent_open": "101%"})

    def test_x2_relations_and_explicit_unavailable_exceptions(self):
        for relation in ["=", "<", ">", "<=", ">=", "≤", "≥"]:
            result = record(changes={"x2_position_yesterday": relation + " 73.123456 km"})
            self.assertEqual(result["x2"]["relation"], relation)
            self.assertEqual(result["x2"]["km"], 73.123456)
        for value in [None, "Unavailable", "NA", "> 7x3 km", "-73 km", "73 cfs"]:
            self.assertIsNone(record(changes={"x2_position_yesterday": value})["x2"]["km"])
        self.assertIsNone(record(changes={"delta_conditions": "N/A"})["numeric_values"]["delta_conditions"])
        for label in ["X2", "X 2", "X2 Position ( yesterday )"]:
            text = report().replace("X2 Position (yesterday)", label).replace("> 81 km", "= > 81.25 km")
            self.assertEqual(adapter.parse_report(text, source_kind="pdf")["x2"],
                             {"km": 81.25, "relation": ">", "value_raw": "> 81.25 km"})
        duplicate = report().replace("X2 Position (yesterday) > 81 km", "X2 > 81 km\nX2 = 73 km")
        self.assertIsNone(adapter.parse_report(duplicate, source_kind="pdf")["x2"]["km"])

    def test_verified_sample_format_variants_with_synthetic_values(self):
        # Saved originals include equality, partial gate opening and an integer
        # annotated percentage. No original MIME or header material is embedded.
        for gates, diverted, x2 in [("40%", "54 (3-day avg)", "= 77 km"),
                                    ("60%", "52.1 (3-day avg)", "> 81 km"),
                                    ("0%", "0 (3-day avg)", "≤ 77.25 km")]:
            parsed = record(changes={"cross_channel_gates_percent_open": gates,
                                     "percent_inflow_diverted": diverted,
                                     "x2_position_yesterday": x2})
            self.assertEqual(parsed["numeric_values"]["cross_channel_gates_percent_open"], float(gates[:-1]))
            self.assertEqual(parsed["numeric_values"]["percent_inflow_diverted"], float(diverted.split()[0]))
            self.assertEqual(parsed["values"]["percent_inflow_diverted"].count("(3-day avg)"), 1)

    def test_heading_is_only_date_source(self):
        self.assertEqual(adapter.parse_message(message(report()).as_bytes())["report_date"], "2026-10-15")
        missing = report().replace("EXECUTIVE OPERATIONS SUMMARY ON 10/15/2026", "")
        with self.assertRaises(adapter.ReportError):
            adapter.parse_message(message(missing).as_bytes())
        with self.assertRaisesRegex(adapter.ReportError, "subject_heading_disagreement"):
            adapter.parse_message(message(report(), subject="Delta Operations Summary for October 14, 2026").as_bytes())
        with self.assertRaises(adapter.ReportError):
            adapter.parse_report(report() + "\nEXECUTIVE OPERATIONS SUMMARY ON 10/14/2026", source_kind="email")

    def test_disagreeing_incomplete_and_duplicate_reports_reject(self):
        for other in [report(changes={"jones_pumping_plant": "2,701 cfs"}), report().replace("Jones Pumping Plant", "Unknown Plant"), report().replace("EXECUTIVE OPERATIONS SUMMARY ON 10/15/2026", "")]:
            with self.subTest(other=other[:15]), self.assertRaises(adapter.ReportError):
                adapter.parse_message(message(report(), html_report(other)).as_bytes())
        with self.assertRaises(adapter.ReportError):
            adapter.parse_report(report().replace("RESERVOIR RELEASES", "Jones Pumping Plant = 1 cfs\nRESERVOIR RELEASES"), source_kind="email")

    def test_forwarded_nested_and_attachment_only_are_not_reports(self):
        for subject, body in [("Fwd: Delta Operations Summary", report()), ("Report", "Begin forwarded message\n" + report())]:
            with self.assertRaises(adapter.ReportError):
                adapter.parse_message(message(body, subject=subject).as_bytes())
        outer = message("No report here", subject="Synthetic envelope")
        outer.add_attachment(message(report()))
        with self.assertRaises(adapter.ReportError):
            adapter.parse_message(outer.as_bytes())
        outer = message("No report here", subject="Synthetic envelope")
        outer.add_attachment(report(), subtype="plain", filename="synthetic.txt")
        with self.assertRaisesRegex(adapter.ReportError, "no_report"):
            adapter.parse_message(outer.as_bytes())
        with self.assertRaisesRegex(adapter.ReportError, "quoted_html"):
            adapter.parse_message(message(html="<blockquote>" + html_report(report()) + "</blockquote>").as_bytes())

    def test_bounded_parts_sizes_and_invalid_encodings(self):
        with self.assertRaisesRegex(adapter.ReportError, "message_size_limit"):
            adapter.parse_message(b"x" * (adapter.MAX_MESSAGE_BYTES + 1))
        msg = message(report())
        for _ in range(adapter.MAX_PARTS):
            msg.add_attachment(b"synthetic", maintype="application", subtype="octet-stream")
        with self.assertRaisesRegex(adapter.ReportError, "mime_complexity_limit"):
            adapter.parse_message(msg.as_bytes())
        bad = b'Content-Type: text/plain; charset="utf-8"\r\n\r\n\xff'
        with self.assertRaises(adapter.ReportError):
            adapter.parse_message(bad)

    def test_private_footer_and_error_text_are_not_exported(self):
        text = report().replace("RESERVOIR RELEASES", f"Historical data and reports are available at: _https://example.invalid/{CANARY}_\nRESERVOIR RELEASES")
        msg = message(text + f"\nhttps://example.invalid/unsubscribe?private={CANARY}", html_report(text))
        parsed = adapter.parse_message(msg.as_bytes())
        self.assertNotIn(CANARY, json.dumps(parsed))
        with self.assertRaises(adapter.ReportError) as error:
            adapter.parse_message(message(report(changes={"jones_pumping_plant": CANARY})).as_bytes())
        self.assertNotIn(CANARY, str(error.exception))


class SelectionTests(unittest.TestCase):
    clock = datetime(2026, 10, 15, 19, tzinfo=timezone.utc)

    def choose(self, deliveries, pdf=None, *, state="completed", clock=None):
        fetch = Mock(return_value=pdf)
        result = adapter.select_source(deliveries, fetch, as_of=clock or self.clock, retrieval_state=state)
        self.assertLessEqual(fetch.call_count, 1)
        self.assertEqual(result["pdf_attempted"], bool(fetch.call_count))
        return result

    def trusted(self, day="2026-10-15", changes=None):
        return adapter.Delivery(record(day, changes), trusted=True)

    def test_current_email_primary_and_duplicate_order_independence(self):
        first = self.trusted()
        second = self.trusted(changes={"clifton_court_inflow": "1000.0 cfs"})
        a = self.choose([first, second])
        b = self.choose([second, first])
        self.assertEqual(a, b)
        self.assertEqual(a["decision"], "email_primary")
        unicode_relation = self.choose([self.trusted(changes={"x2_position_yesterday": "≥ 81 km"}), self.trusted(changes={"x2_position_yesterday": ">= 81.0 km"})])
        self.assertEqual(unicode_relation["decision"], "email_primary")
        precise_conflict = self.choose([self.trusted(changes={"x2_position_yesterday": "> 81.12345678901234567 km"}), self.trusted(changes={"x2_position_yesterday": "> 81.12345678901234568 km"})])
        self.assertEqual(precise_conflict["decision"], "failure_retained")

    def test_forged_headers_never_establish_eligibility_or_barrier(self):
        forged = adapter.inspect_delivery(message(report()).as_bytes())
        self.assertFalse(forged.trusted)
        result = self.choose([forged], record("2026-10-14", kind="pdf"))
        self.assertEqual(result["email_state"], "unverified_only")
        self.assertEqual(result["record"]["report_date"], "2026-10-14")
        bad = adapter.Delivery(trusted=False, malformed_date=date(2026, 10, 15), rejected=True)
        self.assertEqual(self.choose([bad, self.trusted()])["decision"], "email_primary")

    def test_retrieval_states_and_rejected_messages_stay_distinct(self):
        for state, expected in [("not_configured", "not_configured"), ("unavailable", "unavailable"), ("completed", "completed_no_eligible_message")]:
            result = self.choose([], record(kind="pdf"), state=state)
            self.assertEqual(result["email_state"], expected)
            self.assertEqual(result["decision"], "pdf_recovery")
        invalid = adapter.inspect_delivery(message("No report").as_bytes(), trusted=True)
        self.assertEqual(self.choose([invalid], record(kind="pdf"))["email_state"], "rejected_message")

    def test_lagging_email_pdf_dates_agreement_and_outage(self):
        email = self.trusted("2026-10-14")
        cases = [(None, "email", "2026-10-14"), (record("2026-10-13", kind="pdf"), "email", "2026-10-14"),
                 (record("2026-10-14", kind="pdf"), "email", "2026-10-14"), (record(kind="pdf"), "pdf", "2026-10-15")]
        for pdf, kind, day in cases:
            result = self.choose([email], pdf)
            self.assertEqual(result["record"]["source_kind"], kind)
            self.assertEqual(result["record"]["report_date"], day)
        fetch = Mock(side_effect=RuntimeError(CANARY))
        result = adapter.select_source([email], fetch, as_of=self.clock)
        self.assertEqual(result["decision"], "accepted_lagging_email")
        self.assertNotIn(CANARY, json.dumps(result))

    def test_same_date_cross_source_scientific_conflict_retains(self):
        result = self.choose([self.trusted("2026-10-14")], record("2026-10-14", {"jones_pumping_plant": "2701 cfs"}, "pdf"))
        self.assertEqual(result["decision"], "failure_retained")

    def test_conflicting_newest_emails_require_later_whole_report(self):
        for conflict_day, pdf_day, accepted in [("2026-10-15", "2026-10-15", False), ("2026-10-15", "2026-10-14", False), ("2026-10-14", "2026-10-15", True)]:
            deliveries = [self.trusted("2026-10-13"), self.trusted(conflict_day), self.trusted(conflict_day, {"outflow_index": "6101 cfs"})]
            result = self.choose(deliveries, record(pdf_day, kind="pdf"))
            self.assertEqual(result["record"] is not None, accepted)
        self.assertEqual(self.choose([self.trusted("2026-10-14"), self.trusted("2026-10-14", {"outflow_index": "6101 cfs"}), self.trusted()])["decision"], "email_primary")

    def test_newer_malformed_heading_barrier_and_independent_recovery(self):
        bad = adapter.inspect_delivery(message(report(changes={"jones_pumping_plant": "bad"})).as_bytes(), trusted=True)
        self.assertEqual(bad.malformed_date, date(2026, 10, 15))
        for pdf, accept in [(None, False), (record("2026-10-14", kind="pdf"), False), (record(kind="pdf"), True)]:
            self.assertEqual(self.choose([self.trusted("2026-10-14"), bad], pdf)["record"] is not None, accept)
        no_heading = adapter.Delivery(trusted=True, rejected=True)
        self.assertIsNotNone(self.choose([no_heading], record("2026-10-14", kind="pdf"))["record"])
        old_bad = adapter.Delivery(trusted=True, malformed_date=date(2026, 10, 14), rejected=True)
        self.assertEqual(self.choose([old_bad, self.trusted()])["decision"], "email_primary")

    def test_qualifiers_statuses_and_relations_are_part_of_equivalence(self):
        for changes in [{"total_delta_inflow": "11,833 cfs"}, {"delta_conditions": "Excess"}, {"x2_position_yesterday": "= 81 km"}, {"percent_inflow_diverted": "30.2"}]:
            result = self.choose([self.trusted(), self.trusted(changes=changes)], record(kind="pdf"))
            self.assertEqual(result["decision"], "failure_retained")

    def test_pacific_day_future_age_leap_and_year_boundaries(self):
        for day, accept in [("2026-10-08", True), ("2026-10-07", False), ("2026-10-16", False)]:
            self.assertEqual(self.choose([self.trusted(day)])["record"] is not None, accept)
        clock = datetime(2026, 10, 16, 1, tzinfo=timezone.utc)
        self.assertEqual(self.choose([self.trusted()], clock=clock)["decision"], "email_primary")
        for day, clock in [("2028-02-29", datetime(2028, 2, 29, 20, tzinfo=timezone.utc)), ("2027-01-01", datetime(2027, 1, 1, 20, tzinfo=timezone.utc))]:
            self.assertEqual(self.choose([self.trusted(day)], clock=clock)["decision"], "email_primary")


BUILDER = r'''
expressions <- parse("scripts/build_delta_ops_daily_summary.R")
args <- commandArgs(TRUE)
assigned <- function(expr, name) is.call(expr) && identical(expr[[1]], as.name("<-")) && identical(expr[[2]], as.name(name))
for (i in seq_along(expressions)) {
  if (assigned(expressions[[i]], "feed_build_time")) expressions[[i]][[3]] <- substitute(as.POSIXct(clock, tz="UTC"), list(clock=args[[1]]))
  if (length(args) > 1 && assigned(expressions[[i]], "raw_text")) expressions[[i]][[3]] <- substitute(paste(readLines(path), collapse="\n"), list(path=args[[2]]))
}
eval(expressions, new.env(parent=globalenv()))
'''


class BuilderReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="delta-email-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.saved = self.root / (CANARY + ".eml")
        self.saved.write_bytes(message(report(), html_report(report(wrapped=True))).as_bytes())

    def build(self, target, *, pdf=False, clock="2026-10-15 19:00:00"):
        env = {k: v for k, v in os.environ.items() if not k.startswith("DELTA_OPS_")}
        env.update(DELTA_OPS_OUT_DIR=str(target / "docs/data"), DELTA_OPS_SKIP_IF_CURRENT_DATE="false", DELTA_OPS_SAVED_EMAIL="" if pdf else str(self.saved))
        args = ["Rscript", "-e", BUILDER, clock]
        if pdf:
            source = self.root / "synthetic-pdf-text.txt"
            source.write_text(report())
            args.append(str(source))
        return subprocess.run(args, cwd=REPO, env=env, capture_output=True, text=True, timeout=60)

    @staticmethod
    def snapshot(root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def test_real_builder_replay_precision_provenance_and_publication_block(self):
        target = self.root / "candidate"
        result = self.build(target, clock="2026-10-16 01:00:00")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(CANARY, result.stdout + result.stderr)
        files = self.snapshot(target)
        self.assertEqual(len(files), 4)
        self.assertNotIn(CANARY, b"".join(files.values()).decode())
        summary = json.loads(files["docs/data/delta_ops_daily_summary_summary.json"])
        self.assertEqual(summary["report_date_guard_build_local_date"], "2026-10-15")
        self.assertEqual((summary["x2_position_km"], summary["x2_position_relation"], summary["x2_position_date"]), (81, ">", "2026-10-14"))
        self.assertTrue(summary["review_only"])
        self.assertEqual(summary["source_kind"], "email")
        feature = {f["properties"]["feature_key"]: f["properties"] for f in json.loads(files["docs/data/delta_ops_daily_summary_features.geojson"])["features"]}
        self.assertEqual(feature["percent_inflow_diverted"]["value_numeric"], 30.2)
        self.assertEqual(feature["percent_inflow_diverted"]["label_text"], "Diverted: 30.2% (3-day avg)")
        self.assertEqual(feature["omr_index"]["value_numeric"], -3500)
        checked = subprocess.run([sys.executable, "scripts/delta_ops_publisher.py", "validate", "--root", str(target)], cwd=REPO, capture_output=True, text=True)
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("cannot enter production", checked.stderr)

    def test_malformed_saved_input_preserves_prior_bytes(self):
        target = self.root / "prior"
        self.assertEqual(self.build(target).returncode, 0)
        before = self.snapshot(target)
        self.saved.write_bytes(message(report(changes={"jones_pumping_plant": CANARY})).as_bytes())
        rejected = self.build(target)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn(CANARY, rejected.stdout + rejected.stderr)
        self.assertEqual(self.snapshot(target), before)

    def test_synthetic_email_pdf_scientific_parity_and_reference_geometry(self):
        email_root, pdf_root = self.root / "email", self.root / "pdf"
        for root, pdf in [(email_root, False), (pdf_root, True)]:
            result = self.build(root, pdf=pdf)
            self.assertEqual(result.returncode, 0, result.stderr)
        email, pdf = self.snapshot(email_root), self.snapshot(pdf_root)
        for path in email:
            first, second = json.loads(email[path]), json.loads(pdf[path])
            if path.endswith("reference.geojson"):
                self.assertEqual(email[path], pdf[path])
            elif "features" in first:
                for left, right in zip(first["features"], second["features"]):
                    self.assertEqual(left["geometry"], right["geometry"])
                    for key in ["feature_key", "value_raw", "value_numeric", "units", "label_text"]:
                        self.assertEqual(left["properties"][key], right["properties"][key])
            elif "values" in first:
                self.assertEqual(first["values"], second["values"])
            else:
                for key in ["x2_position_km", "x2_position_relation", "x2_position_date", "export_total_cfs", "export_total_kaf_day", "east_side_streams_cfs"]:
                    self.assertEqual(first[key], second[key])

    def test_replay_cannot_target_repository_or_hosted_execution(self):
        output = self.root / "record.json"
        command = [sys.executable, "scripts/delta_ops_email.py", "replay", "--input", str(self.saved), "--output", str(output), "--candidate-dir", str(REPO / "docs/data")]
        blocked = subprocess.run(command, cwd=REPO, capture_output=True, text=True)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertFalse(output.exists())
        command[-1] = str(self.root / "candidate")
        blocked = subprocess.run(command, cwd=REPO, env=dict(os.environ, GITHUB_ACTIONS="true"), capture_output=True, text=True)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertNotIn(CANARY, blocked.stderr)


if __name__ == "__main__":
    unittest.main()
