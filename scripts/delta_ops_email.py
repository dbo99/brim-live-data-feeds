#!/usr/bin/env python3
"""Bounded Delta report parsing and pure selection; no mailbox/network client.

Delivery trust is supplied by a future independently authenticated retriever.
The CLI only normalizes PDF text or produces explicitly nonpublishable replay
records. Headers in a saved message never confer delivery trust.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from email import policy
from email.errors import MessageError
from email.parser import BytesParser
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Callable
from zoneinfo import ZoneInfo

MAX_MESSAGE_BYTES = 2 * 1024 * 1024
MAX_PART_BYTES = 1024 * 1024
MAX_PARTS = 16
MAX_DEPTH = 5
MAX_MESSAGES = 64
PUBLIC_ATTRIBUTION = "https://water.ca.gov/"
LABELS = {
    "clifton_court_inflow": "Clifton Court Inflow",
    "jones_pumping_plant": "Jones Pumping Plant",
    "total_delta_inflow": "Total Delta Inflow",
    "sacramento_river": "Sacramento River",
    "san_joaquin_river": "San Joaquin River",
    "delta_conditions": "Delta Conditions",
    "cross_channel_gates_percent_open": "Delta x-channel Gates (% of day is open)",
    "outflow_index": "Outflow Index",
    "percent_inflow_diverted": "% Inflow Diverted",
    "x2_position_yesterday": "X2 Position (yesterday)",
    "controlling_factors": "Controlling Factor(s)",
    "omr_index_daily_value": "OMR Index Daily Value",
    "shasta_storage": "Shasta Reservoir",
    "folsom_storage": "Folsom Reservoir",
    "oroville_storage": "Oroville Reservoir",
    "san_luis_total_storage": "San Luis Res. Total",
    "san_luis_swp_share": "SWP Share",
}
STATUS_KEYS = {"delta_conditions", "controlling_factors"}
STORAGE_KEYS = {k for k in LABELS if k.endswith("storage")} | {"san_luis_swp_share"}
PERCENT_KEYS = {"cross_channel_gates_percent_open", "percent_inflow_diverted"}
NUMBER = r"[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
HEADING = re.compile(r"EXECUTIVE\s+OPERATIONS\s+SUMMARY\s+ON\s+(\d{1,2}/\d{1,2}/\d{4})(?!\d)", re.I)
SECTIONS = re.compile(r"SCHEDULED EXPORTS FOR TODAY|ESTIMATED DELTA HYDROLOGY|DELTA OPERATIONS|RESERVOIR STORAGES(?:\s*\(AS OF MIDNIGHT\))?", re.I)


class ReportError(ValueError):
    """Safe diagnostic code only; never include input text or paths."""

    def __init__(self, code: str, heading_date: date | None = None):
        super().__init__(code)
        self.code = code
        self.heading_date = heading_date


def _spaces(value: str) -> str:
    return " ".join(value.translate(str.maketrans({"−": "-", "‐": "-", "‑": "-"})).split())


def _number(value: str) -> float:
    try:
        result = float(Decimal(value.replace(",", "").replace("−", "-")))
    except (InvalidOperation, ValueError, OverflowError):
        raise ReportError("invalid_number") from None
    if not math.isfinite(result):
        raise ReportError("nonfinite_number")
    return result


def normalize_values(values: dict) -> tuple[dict, dict, dict]:
    """Field-aware parsing shared by both transports and candidate validation."""
    if not isinstance(values, dict) or set(values) != set(LABELS):
        raise ReportError("required_field_set")
    raw, numeric = {}, {}
    x2 = {"km": None, "relation": None, "value_raw": "Unavailable"}
    for key, value in values.items():
        if not isinstance(value, str) or len(value) > 160:
            raise ReportError("invalid_field")
        value = _spaces(value)
        if key == "x2_position_yesterday":
            value = re.sub(r"^=\s*(?=[<>≤≥])", "", value)
            match = re.fullmatch(r"([<>]\s*=?|[=≤≥])?\s*(\d+(?:\.\d+)?)\s*km", value, re.I)
            if match:
                relation = re.sub(r"\s", "", match[1] or "=")
                x2 = {"km": _number(match[2]), "relation": relation,
                      "value_raw": ("" if relation == "=" else relation + " ") + match[2] + " km"}
            raw[key] = x2["value_raw"]
            numeric[key] = x2["km"]
            continue
        value = re.sub(r"^=\s*", "", value)
        if key in STATUS_KEYS:
            if not value or not re.fullmatch(r"[A-Za-z0-9 ,().%+&'/\-]+", value):
                raise ReportError("invalid_status")
            raw[key], numeric[key] = value, None
            continue
        unit = "TAF" if key in STORAGE_KEYS else "cfs"
        if key in PERCENT_KEYS:
            suffix = r"%" if key == "cross_channel_gates_percent_open" else r"%?(?:\s*\(3-day avg\))?"
        else:
            suffix = unit
        match = re.fullmatch(r"(?P<approx>[~≈])?\s*(?P<number>" + NUMBER + r")\s*(?P<suffix>" + suffix + r")", value, re.I)
        if not match:
            raise ReportError("invalid_required_number_or_unit")
        number = _number(match["number"])
        if key in PERCENT_KEYS and not 0 <= number <= 100:
            raise ReportError("percentage_range")
        if key in STORAGE_KEYS and number < 0:
            raise ReportError("storage_range")
        # Preserve annotations and source decimal text, normalize harmless spacing.
        suffix_text = _spaces(match["suffix"])
        if key in PERCENT_KEYS:
            suffix_text = re.sub(r"\(3-day avg\)", "(3-day avg)", suffix_text, flags=re.I)
            rendered = match["number"] + ("%" if suffix_text.startswith("%") else "")
            if "(" in suffix_text:
                rendered += " (3-day avg)"
        else:
            rendered = match["number"] + " " + unit
        raw[key] = ((match["approx"] + " ") if match["approx"] else "") + rendered
        numeric[key] = number
    return raw, numeric, x2


def heading_date(text: str) -> date | None:
    dates = HEADING.findall(text)
    if not dates:
        return None
    try:
        parsed = {datetime.strptime(x, "%m/%d/%Y").date() for x in dates}
    except ValueError:
        return None
    return next(iter(parsed)) if len(parsed) == 1 else None


def parse_report(text: str, *, source_kind: str) -> dict:
    if source_kind not in {"email", "pdf"}:
        raise ReportError("invalid_source_kind")
    if len(text.encode("utf-8")) > MAX_PART_BYTES:
        raise ReportError("report_size_limit")
    # Plain email emphasis is presentation, not a scientific qualifier.
    text = re.sub(r"(?m)^\s*\*+|\*+\s*$", "", text)
    found = list(HEADING.finditer(text))
    day = heading_date(text)
    if len(found) != 1 or day is None:
        raise ReportError("missing_or_ambiguous_heading", day)
    body = text[found[0].end():]
    body = re.split(r"RESERVOIR\s+RELEASES", body, maxsplit=1, flags=re.I)[0]
    footer = re.search(r"data\s+and\s+reports\s+are\s+available\b", body, re.I)
    if footer:
        body = body[:body.rfind("\n", 0, footer.start()) + 1]
    body = SECTIONS.sub(" ", _spaces(body))
    body = re.sub(r"[~_\-]{3,}", " ", body)
    positions = []
    for key, label in LABELS.items():
        pattern = re.escape(label).replace(r"\ ", r"\s+")
        if key == "x2_position_yesterday":
            pattern = r"\bX\s*2\b(?:\s+Position\s*\(\s*yesterday\s*\))?"
        # re.escape uses backslash-space for ordinary spaces.
        matches = list(re.finditer(pattern, body, re.I))
        if len(matches) != 1 and key != "x2_position_yesterday":
            raise ReportError("missing_or_duplicate_label", day)
        # Multiple X2 rows remain explicitly unavailable, as in the existing
        # parser. Keep their boundaries so they cannot contaminate other fields.
        positions.extend((match.start(), match.end(), key) for match in matches)
    positions.sort()
    values = {"x2_position_yesterday": "Unavailable"}
    for i, (_, end, key) in enumerate(positions):
        stop = positions[i + 1][0] if i + 1 < len(positions) else len(body)
        if key == "x2_position_yesterday" and sum(row[2] == key for row in positions) != 1:
            continue
        values[key] = _spaces(body[end:stop])
    try:
        raw, numeric, x2 = normalize_values(values)
    except ReportError as error:
        raise ReportError(error.code, day) from None
    return {"report_date": day.isoformat(), "report_date_raw": found[0][1],
            "values": raw, "numeric_values": numeric, "x2": x2,
            "source_kind": source_kind, "source_url": PUBLIC_ATTRIBUTION,
            "review_only": False}


class _InertHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored = 0
        self.quoted = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored += 1
        if tag == "blockquote":
            self.quoted = True
        if tag in {"p", "br", "td", "th", "tr", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)
        if tag in {"p", "td", "th", "tr", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def semantic_key(record: dict) -> str:
    raw, numeric, x2 = normalize_values(record["values"])

    def exact_number(text):
        token = re.search(NUMBER, text)[0].replace(",", "").replace("−", "-")
        rendered = format(Decimal(token), "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return "0" if Decimal(token) == 0 else rendered

    fields = {}
    for key in LABELS:
        if key in STATUS_KEYS:
            fields[key] = raw[key]
        elif key == "x2_position_yesterday":
            fields[key] = ([exact_number(x2["value_raw"]), {"≤": "<=", "≥": ">="}.get(x2["relation"], x2["relation"])]
                           if x2["km"] is not None else [None, None])
        else:
            fields[key] = [exact_number(raw[key]), "approximate" if raw[key].startswith(("~", "≈")) else "reported",
                           "3-day avg" if "(3-day avg)" in raw[key] else ""]
    return json.dumps([record["report_date"], fields], sort_keys=True, allow_nan=False)


def parse_message(raw: bytes) -> dict:
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ReportError("message_size_limit")
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        if message.defects or len(message.get_all("Subject", [])) > 1:
            raise ReportError("malformed_mime")
        subject = str(message.get("Subject", ""))
        if re.match(r"\s*(?:fw|fwd)\s*:", subject, re.I):
            raise ReportError("forwarded_message")
        parts, part_errors, part_days, count = [], [], [], 0

        def visit(part, depth=0):
            nonlocal count
            count += 1
            if count > MAX_PARTS or depth > MAX_DEPTH:
                raise ReportError("mime_complexity_limit")
            if part.defects:
                raise ReportError("malformed_mime")
            if part.get_content_maintype() == "message":
                raise ReportError("nested_message")
            if part.get_content_disposition() == "attachment" or part.get_filename():
                return
            if part.is_multipart():
                for child in part.iter_parts():
                    visit(child, depth + 1)
                return
            if part.get_content_type() not in {"text/plain", "text/html"}:
                return
            decoded = part.get_payload(decode=True)
            if part.defects or decoded is None or len(decoded) > MAX_PART_BYTES:
                raise ReportError("invalid_part_or_size")
            text = decoded.decode(part.get_content_charset() or "ascii", errors="strict")
            if re.search(r"Begin forwarded message|[- ]+Forwarded message|^\s*>.*EXECUTIVE", text, re.I | re.M):
                raise ReportError("forwarded_content")
            if part.get_content_type() == "text/html":
                html = _InertHTML()
                html.feed(text)
                if html.quoted:
                    raise ReportError("quoted_html")
                text = "".join(html.parts)
            if re.search(r"EXECUTIVE\s+OPERATIONS|Clifton\s+Court\s+Inflow|RESERVOIR\s+STORAGES", text, re.I):
                if heading_date(text) is not None:
                    part_days.append(heading_date(text))
                try:
                    parts.append(parse_report(text, source_kind="email"))
                except ReportError as error:
                    part_errors.append(error.code)

        visit(message)
        if part_errors:
            raise ReportError("rejected_report_representation", max(part_days, default=None))
        if not parts:
            raise ReportError("no_report")
        if len({semantic_key(part) for part in parts}) != 1:
            day = max(date.fromisoformat(part["report_date"]) for part in parts)
            raise ReportError("representation_disagreement", day)
        record = min(parts, key=lambda x: json.dumps(x, sort_keys=True))
        labels = re.findall(r"Delta Operations Summary for\s+([A-Za-z]+\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})", subject, re.I)
        for label in labels:
            fmt = "%B %d, %Y" if "," in label else "%Y-%m-%d" if "-" in label else "%m/%d/%Y"
            try:
                subject_day = datetime.strptime(label, fmt).date().isoformat()
            except ValueError:
                raise ReportError("invalid_subject_report_date", date.fromisoformat(record["report_date"])) from None
            if subject_day != record["report_date"]:
                raise ReportError("subject_heading_disagreement", date.fromisoformat(record["report_date"]))
        return record
    except ReportError:
        raise
    except (ValueError, LookupError, UnicodeError, TypeError, RecursionError, MessageError):
        raise ReportError("invalid_message_encoding_or_structure") from None


@dataclass(frozen=True)
class Delivery:
    record: dict | None = None
    trusted: bool = False
    malformed_date: date | None = None
    rejected: bool = False


def inspect_delivery(raw: bytes, *, trusted: bool = False) -> Delivery:
    """trusted is boundary evidence, NEVER derived from the message's headers."""
    try:
        return Delivery(record=parse_message(raw), trusted=trusted)
    except ReportError as error:
        return Delivery(trusted=trusted, malformed_date=error.heading_date, rejected=True)


def select_source(deliveries: list[Delivery], pdf_fetch: Callable[[], dict], *,
                  as_of: datetime, retrieval_state: str = "completed", max_lag: int = 7) -> dict:
    """Call the existing bounded PDF route at most once; never merge sources."""
    if as_of.tzinfo is None or not 0 <= max_lag <= 7:
        raise ReportError("invalid_selection_clock_or_lag")
    if retrieval_state not in {"completed", "unavailable", "not_configured"}:
        raise ReportError("invalid_retrieval_state")
    if len(deliveries) > MAX_MESSAGES or (retrieval_state != "completed" and deliveries):
        raise ReportError("invalid_delivery_batch")
    today = as_of.astimezone(ZoneInfo("America/Los_Angeles")).date()

    def acceptable(record):
        if not isinstance(record, dict) or record.get("source_kind") not in {"email", "pdf"}:
            return False
        try:
            day = date.fromisoformat(record["report_date"])
            semantic_key(record)
            return 0 <= (today - day).days <= max_lag
        except (KeyError, TypeError, ValueError):
            return False

    groups, malformed = {}, []
    for delivery in deliveries:
        if delivery.trusted is not True:
            continue
        if delivery.malformed_date is not None and 0 <= (today - delivery.malformed_date).days <= max_lag:
            malformed.append(delivery.malformed_date)
        if delivery.record is not None and acceptable(delivery.record) and delivery.record["source_kind"] == "email":
            groups.setdefault(date.fromisoformat(delivery.record["report_date"]), []).append(delivery.record)
    conflicts = [day for day, records in groups.items() if len({semantic_key(x) for x in records}) > 1]
    conflict_barrier = max(conflicts, default=date.min)
    malformed_barrier = max(malformed, default=date.min)
    candidates = [x for day, records in groups.items() if day > conflict_barrier and day >= malformed_barrier
                  for x in [min(records, key=lambda r: json.dumps(r, sort_keys=True))]]
    email = max(candidates, key=lambda x: x["report_date"], default=None)
    # A malformed trusted bulletin needs independent same/later PDF recovery.
    email_state = retrieval_state
    if retrieval_state == "completed":
        email_state = ("eligible" if groups else "rejected_message" if any(d.trusted and d.rejected for d in deliveries)
                       else "unverified_only" if deliveries else "completed_no_eligible_message")
    diagnostic = {"retrieval_state": retrieval_state, "email_state": email_state}
    if email and email["report_date"] == today.isoformat() and date.fromisoformat(email["report_date"]) > malformed_barrier:
        return {"record": email, "decision": "email_primary", "pdf_attempted": False, **diagnostic}
    pdf = None
    try:
        attempted = pdf_fetch()
        if acceptable(attempted) and attempted["source_kind"] == "pdf":
            pdf = attempted
    except Exception:
        # No raw upstream exception, body, headers, URL or filesystem path escapes.
        pass
    valid = [x for x in [email, pdf] if x is not None]
    if email and pdf and email["report_date"] == pdf["report_date"] and semantic_key(email) != semantic_key(pdf):
        conflict_barrier = max(conflict_barrier, date.fromisoformat(email["report_date"]))
    valid = [x for x in valid if date.fromisoformat(x["report_date"]) > conflict_barrier
             and date.fromisoformat(x["report_date"]) >= malformed_barrier]
    if malformed_barrier != date.min and (pdf is None or date.fromisoformat(pdf["report_date"]) < malformed_barrier):
        valid = [x for x in valid if date.fromisoformat(x["report_date"]) > malformed_barrier]
    if not valid:
        return {"record": None, "decision": "failure_retained", "pdf_attempted": True, **diagnostic}
    selected = max(valid, key=lambda x: (x["report_date"], x["source_kind"] == "email"))
    decision = "pdf_recovery" if selected["source_kind"] == "pdf" else "accepted_lagging_email"
    if selected["source_kind"] == "email" and selected["report_date"] == today.isoformat():
        decision = "email_after_pdf_check"
    return {"record": selected, "decision": decision, "pdf_attempted": True, **diagnostic}


def _outside_repository(path: Path) -> bool:
    resolved = path.resolve()
    return not any((parent / ".git").exists() for parent in (resolved, *resolved.parents))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["pdf-text", "replay"])
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--candidate-dir")
    args = parser.parse_args(argv)
    try:
        source, output = Path(args.input), Path(args.output)
        if source.stat().st_size > MAX_MESSAGE_BYTES or not _outside_repository(output):
            raise ReportError("input_size_or_output_boundary")
        if args.mode == "replay":
            if (os.environ.get("GITHUB_ACTIONS") == "true" or not _outside_repository(source)
                    or not args.candidate_dir or not _outside_repository(Path(args.candidate_dir))):
                raise ReportError("saved_replay_boundary")
            record = parse_message(source.read_bytes())
            record["review_only"] = True
        else:
            record = parse_report(source.read_text(encoding="utf-8"), source_kind="pdf")
        output.write_text(json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print("DELTA_OPS_SOURCE_NORMALIZED " + args.mode)
        return 0
    except (ReportError, OSError, UnicodeError) as error:
        code = error.code if isinstance(error, ReportError) else "input_output_error"
        print("DELTA_OPS_SOURCE_REJECTED " + code, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
