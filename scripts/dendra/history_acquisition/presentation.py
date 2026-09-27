"""Explicit offline presentation horizons; source-start review is not dispatch.

Audit endpoints can describe an estimate but cannot supply an executable horizon.
Trusted reviewed starts bind exact frozen identities and the evidence reviewed;
hashes establish integrity, not the reviewer's authority. No discovery or I/O.
"""
from datetime import datetime, timedelta, timezone

from ..transport import parse_utc, format_utc
from .model import Inventory, INVENTORY_SHA256, HASH
from .safety import decode, digest, encode, require

FIRST_RULE = "dendra-reviewed-first-observation-1"
AUTHORITY = "dendra-source-start-classification-1"
AUDIT_SHA256 = "138a7102fe568ebda82da54e3630c1df956c2c818faa0dea942e70d95b3b62cd"

VERSION = "dendra-presentation-horizon-1"
INITIAL = "INITIAL_PRESENTATION_10_WY"
FULL_POR = "FULL_POR"
REVIEWED = "REVIEWED_SOURCE_START"
AUDIT = "AUDIT_ESTIMATED_START"
UNKNOWN = "UNKNOWN_SOURCE_START"
PST = timezone(timedelta(hours=-8))


def _source_start(inventory, sid, item, as_of):
    require(isinstance(item, dict), "Source-start descriptor required")
    common = {"state", "station_id", "stream_id", "inventory_sha256", "start"}
    state = item.get("state")
    fields = common | ({"evidence_sha256", "reviewer_ref", "reviewed_at"} if state == REVIEWED
                       else {"evidence_sha256"} if state == AUDIT else set())
    require(state in (REVIEWED, AUDIT, UNKNOWN) and set(item) == fields,
            "Source-start authority fields")
    identity = inventory.identity(sid)
    require(item["station_id"] == identity["station_id"] and item["stream_id"] == sid and
            item["inventory_sha256"] == INVENTORY_SHA256, "Source-start identity mismatch")
    if state == UNKNOWN:
        require(item["start"] is None, "Unknown source start must remain null")
        return None
    require(isinstance(item["evidence_sha256"], str) and HASH.fullmatch(item["evidence_sha256"]),
            "Source-start evidence SHA-256 required")
    start = parse_utc(item["start"])
    if state == REVIEWED:
        require(isinstance(item["reviewer_ref"], str) and 0 < len(item["reviewer_ref"]) <= 128 and
                parse_utc(item["reviewed_at"]) <= as_of, "Explicit nonfuture source-start review required")
    return start


def make(inventory, *, mode, as_of, source_starts):
    """Derive completed-day query horizons without asserting coverage or access.

    INITIAL includes at most the ending-year current WY and its previous nine.
    FULL_POR starts at the reviewed source boundary without that clipping. Exact
    intraday reviewed starts are retained, leaving first-day science to core.R.
    Neither mode includes the incomplete current fixed-PST day or supplies a
    metadata decision, request budget, network permission or publication gate.
    """
    require(type(inventory) is Inventory and mode in (INITIAL, FULL_POR),
            "Explicit supported presentation mode and inventory required")
    require(isinstance(source_starts, dict) and source_starts and
            set(source_starts) <= set(inventory.roster()), "Exact source-start stream selection required")
    now = parse_utc(as_of)
    local = now.astimezone(PST)
    current_wy = local.year + (local.month >= 10)
    cutoff = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    floor = datetime(current_wy - 10, 10, 1, 8, tzinfo=timezone.utc) if mode == INITIAL else None
    horizons, streams = {}, {}
    for sid, source in sorted(source_starts.items()):
        start = _source_start(inventory, sid, source, now)
        proposed = max(start, floor) if start is not None and floor is not None else start
        available = proposed is not None and proposed < cutoff
        interval = dict(start=format_utc(proposed), end=format_utc(cutoff)) if available else None
        reviewed = source["state"] == REVIEWED
        state = ("REVIEWED_HORIZON_AVAILABLE" if available else "NO_COMPLETED_INTERVAL") if reviewed else (
            "SOURCE_START_REVIEW_REQUIRED" if source["state"] == AUDIT else "SOURCE_START_UNKNOWN")
        if reviewed and interval is not None:
            horizons[sid] = interval
        streams[sid] = dict(identity=inventory.identity(sid), source_start_state=source["state"],
            planning_state=state, eligible_for_campaign_planning=bool(reviewed and available),
            horizon=interval if reviewed else None, estimated_horizon=interval if not reviewed else None,
            source_boundary_is_intraday=bool(start is not None and
                (start.hour, start.minute, start.second, start.microsecond) != (8, 0, 0, 0)),
            coverage="NOT_QUERIED_NO_COVERAGE_CLAIM", inactive_tail="UNKNOWN_NOT_INFERRED")
    value = dict(schema_version=VERSION, mode=mode, as_of=format_utc(now),
        inventory_sha256=INVENTORY_SHA256, source_starts=source_starts,
        fixed_pst_offset="-08:00", current_water_year=current_wy,
        earliest_water_year=current_wy - 9 if mode == INITIAL else None,
        water_year_floor=format_utc(floor) if floor is not None else None,
        completed_before=format_utc(cutoff), current_incomplete_day=dict(
            start=format_utc(cutoff), end=format_utc(cutoff + timedelta(days=1)), included=False),
        streams=streams, horizons=horizons, network_execution_authorized=False,
        reference_band_authorized=False, publication_eligible=False)
    return decode(encode(dict(value, planning_sha256=digest(value))))


def verify(value, inventory):
    require(isinstance(value, dict) and value.get("schema_version") == VERSION,
            "Presentation horizon version")
    expected = make(inventory, mode=value["mode"], as_of=value["as_of"],
                    source_starts=value["source_starts"])
    require(value == expected, "Presentation horizon binding mismatch")
    return True


def review_first(inventory, sid, *, response, receipt, review, as_of):
    """Review an original first-query response, never an audit's derived row.

    Receipt/review are explicitly trusted immutable caller inputs. Hashes provide
    integrity, not signatures. This grants source-start authority only, no access.
    """
    from urllib.parse import urlsplit, parse_qsl
    from .authority_witness import total_complete
    import math
    from .safety import sha
    ident = inventory.identity(sid)
    require(ident["native_unit"] in ("Percent", "VolumetricWaterContent"), "Resolved stream required")
    require(isinstance(response, bytes) and 0 < len(response) <= 1024**2, "Bounded original response required")
    require(set(receipt) == {"schema_version", "inventory_sha256", "identity", "method", "url",
        "status", "response_bytes", "response_sha256", "requested_at", "retrieved_at", "complete_body"}, "First receipt fields")
    require(receipt["schema_version"] == "dendra-first-response-receipt-1" and
        receipt["inventory_sha256"] == INVENTORY_SHA256 and receipt["identity"] == ident and
        receipt["method"] == "GET" and type(receipt["status"]) is int and receipt["status"] == 200 and
        receipt["response_bytes"] == len(response) and receipt["response_sha256"] == sha(response) and
        receipt["complete_body"] is True, "First response identity/integrity")
    url = urlsplit(receipt["url"])
    require(url.scheme == "https" and url.netloc == "api.dendra.science" and
        url.path == "/v2/datapoints" and not url.fragment and sorted(parse_qsl(url.query, keep_blank_values=True)) ==
        sorted([("datastream_id", sid), ("$sort[time]", "1"), ("$limit", "1")]), "Unbounded ascending first query required")
    require(set(review) == {"rule", "disposition", "receipt_sha256", "response_sha256", "evidence_identity", "reviewer_ref", "reviewed_at"}, "First review fields")
    require(review["rule"] == FIRST_RULE and review["disposition"] == "ACCEPT_SOURCE_START" and
        review["receipt_sha256"] == digest(receipt) and review["response_sha256"] == sha(response) and
        isinstance(review["evidence_identity"], str) and 0 < len(review["evidence_identity"]) <= 256 and
        isinstance(review["reviewer_ref"], str) and 0 < len(review["reviewer_ref"]) <= 128, "Explicit first review binding")
    requested, retrieved, reviewed, now = map(parse_utc, (receipt["requested_at"],receipt["retrieved_at"],review["reviewed_at"],as_of))
    require(requested <= retrieved <= reviewed <= now, "First evidence chronology")
    value = decode(response)
    require(isinstance(value, dict) and isinstance(value.get("data"), list) and
        type(value.get("limit")) is int and value["limit"] == 1 and
        type(value.get("skip",0)) is int and value.get("skip",0) == 0 and
        len(value["data"]) <= 1 and total_complete(len(value["data"]), "total" in value, value.get("total")), "First query completeness")
    if not value["data"]:
        require(value["total"] == 0, "Ambiguous empty first result")
        return dict(state=UNKNOWN, start=None, reason="complete_empty_no_source_start", evidence_sha256=digest(dict(receipt=receipt,review=review)))
    row = value["data"][0]
    require(isinstance(row,dict) and row.get("datastream_id",sid) == sid and
        row.get("station_id",ident["station_id"]) == ident["station_id"], "Contradictory returned identity")
    at = parse_utc(row["t"])
    require(at <= retrieved and at.year >= 1900 and type(row.get("v")) in (int,float) and math.isfinite(row["v"]), "Invalid first observation")
    return dict(state=REVIEWED, start=format_utc(at), reason=FIRST_RULE,
        evidence_sha256=digest(dict(receipt=receipt,review=review)), response_sha256=sha(response),
        evidence_identity=review["evidence_identity"], receipt_sha256=digest(receipt),
        query_semantics="exact stream; ascending time; limit=1; no range filter",
        retrieved_at=receipt["retrieved_at"], reviewer_ref=review["reviewer_ref"], reviewed_at=review["reviewed_at"])


def review_journal_first(journal, sid, *, review, as_of):
    """Feed verified original Journal bytes into the existing source-start rule.

    Reviewer approval is still required. No metadata/access/history eligibility
    decision is created, and empty responses cannot establish a source start.
    """
    from . import authority_witness as witness
    from .safety import sha
    bound = witness.evidence(journal, sid)
    require(isinstance(review, dict) and set(review) == {
        "rule", "disposition", "evidence_sha256", "reviewer_ref", "reviewed_at"} and
        review["rule"] == witness.REVIEW and review["disposition"] == "ACCEPT_SOURCE_START" and
        review["evidence_sha256"] == bound["evidence_sha256"], "Exact journal witness review required")
    response = journal.read_object(bound["response_object"])
    receipt = dict(schema_version="dendra-first-response-receipt-1", inventory_sha256=INVENTORY_SHA256,
        identity=bound["identity"], method="GET", url=bound["request"]["request"]["url"], status=200,
        response_bytes=len(response), response_sha256=sha(response), complete_body=True,
        requested_at=bound["requested_at"], retrieved_at=bound["retrieved_at"])
    approval = dict(rule=FIRST_RULE, disposition=review["disposition"], receipt_sha256=digest(receipt),
        response_sha256=sha(response), evidence_identity=bound["evidence_sha256"],
        reviewer_ref=review["reviewer_ref"], reviewed_at=review["reviewed_at"])
    result = review_first(journal.inventory, sid, response=response, receipt=receipt, review=approval, as_of=as_of)
    return dict(result, journal_evidence=bound, journal_review_sha256=digest(review), dispatch_ready=False)


def classify_starts(inventory, audit_bytes, *, evidence=None, as_of):
    """Close over all 337 resolved IDs; absent originals never inherit audit authority."""
    from .safety import sha, Hold
    require(type(inventory) is Inventory and sha(audit_bytes) == AUDIT_SHA256, "Accepted audit binding required")
    audit = decode(audit_bytes)
    require(audit["schema_version"] == "1.0.0" and audit["age_as_of_utc"] == "2026-09-20T04:50:00+00:00", "Audit vintage")
    roster = inventory.roster(); indexed = {}
    for item in audit["streams"]:
        sid = item["datastream_id"]
        require(sid not in indexed and sid in roster and item["station_id"] == roster[sid]["station_id"] and
            item["native_unit_name"] == roster[sid]["native_unit"], "Audit/inventory closure")
        indexed[sid] = item
    require(set(indexed) == set(roster), "Audit roster incomplete")
    selected = {sid for sid,i in roster.items() if i["native_unit"] != "Dimensionless"}
    require(len(selected) == 337, "Resolved roster closure")
    evidence = {} if evidence is None else evidence
    require(set(evidence) <= selected, "First evidence outside resolved roster")
    now = parse_utc(as_of); result = []
    for sid in sorted(selected):
        item, ident = indexed[sid], roster[sid]
        start = None
        candidate = item.get("oldest_observed_numeric_utc")
        if candidate is not None:
            try:
                stamp = datetime.fromisoformat(candidate)
                if stamp.tzinfo is not None and 1900 <= stamp.year and stamp <= now:
                    start = format_utc(stamp.astimezone(timezone.utc))
            except (ValueError, TypeError): pass
        state, reason = (AUDIT,"underlying_first_response_unavailable_audit_only") if start else (UNKNOWN,"no_adequate_first_observation_evidence")
        proof = dict(audit_sha256=AUDIT_SHA256, audit_entry_sha256=digest(item))
        if sid in evidence:
            try:
                if set(evidence[sid]) == {"journal", "review"}:
                    from .journal import Journal
                    journal = evidence[sid]["journal"]
                    require(type(journal) is Journal and type(journal.inventory) is Inventory and
                            journal.inventory.roster() == roster, "Witness journal/inventory differs")
                    checked = review_journal_first(sid=sid,as_of=as_of,**evidence[sid])
                else:
                    checked = review_first(inventory,sid,as_of=as_of,**evidence[sid])
                require(checked["state"] != UNKNOWN or start is None,
                        "Empty original contradicts nonempty audit; review required")
                require(checked["state"] != REVIEWED or start is None or checked["start"] == start,
                        "Original first result contradicts audit; review required")
                state, start, reason, proof = checked["state"],checked["start"],checked["reason"],checked
            except (Hold, ValueError, KeyError, TypeError):
                reason = "underlying_first_response_invalid_or_ambiguous"
        result.append(dict(stream_id=sid,station_id=ident["station_id"],native_unit=ident["native_unit"],
            source_start_authority=state,source_start_timestamp=start,source_start_evidence=proof,
            source_start_review_reason=reason))
    value = dict(schema_version=AUTHORITY,inventory_sha256=INVENTORY_SHA256,audit_sha256=AUDIT_SHA256,
        as_of=as_of,streams=result,counts={s:sum(x["source_start_authority"]==s for x in result) for s in (REVIEWED,AUDIT,UNKNOWN)},
        classes={s:[x["stream_id"] for x in result if x["source_start_authority"]==s] for s in (REVIEWED,AUDIT,UNKNOWN)},
        network_execution_authorized=False)
    return dict(value,classification_sha256=digest(value))


def authority_start(inventory, item):
    """Adapt the reviewed result to the existing presentation schema unchanged."""
    sid = item["stream_id"]; ident = inventory.identity(sid)
    require(item["station_id"] == ident["station_id"] and item["native_unit"] == ident["native_unit"], "Authority identity mismatch")
    state, proof = item["source_start_authority"], item["source_start_evidence"]
    value = dict(state=state,station_id=ident["station_id"],stream_id=sid,inventory_sha256=INVENTORY_SHA256,
                 start=item["source_start_timestamp"])
    if state == REVIEWED:
        require(item["source_start_review_reason"] == FIRST_RULE and proof["reason"] == FIRST_RULE and proof["state"] == REVIEWED and proof["start"] == value["start"], "Reviewed first-evidence rule required")
        value.update(evidence_sha256=proof["evidence_sha256"],reviewer_ref=proof["reviewer_ref"],reviewed_at=proof["reviewed_at"])
    elif state == AUDIT: value["evidence_sha256"] = proof["audit_sha256"]
    else: require(state == UNKNOWN and value["start"] is None, "Unknown source start")
    return value


def refresh_plan(inventory, sid, *, source_start, as_of, mode, last_complete_end=None, completed_water_year=None):
    """Planning only: exact interval replacement after complete valid refetch.

    last_complete_end is a trusted verified contiguous query frontier, not the
    latest observation or maximum end across disconnected seals.
    """
    now = parse_utc(as_of); start = _source_start(inventory,sid,source_start,now)
    require(source_start["state"] == REVIEWED and start is not None, "Reviewed source start required")
    local = now.astimezone(PST); wy = local.year+(local.month>=10)
    cutoff = local.replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc)
    require(mode in ("ROUTINE_REFRESH","CURRENT_WY_RECONCILIATION","WY_CLOSE_RECONCILIATION"), "Explicit refresh mode")
    end = cutoff
    if mode == "ROUTINE_REFRESH":
        require(last_complete_end is not None, "Verified complete query frontier required")
        frontier = parse_utc(last_complete_end)
        require(start <= frontier <= cutoff, "Invalid/future query frontier")
        lo = max(start,min(frontier,cutoff-timedelta(days=7)))
    elif mode == "CURRENT_WY_RECONCILIATION": lo = max(start,datetime(wy-1,10,1,8,tzinfo=timezone.utc))
    else:
        require(type(completed_water_year) is int and 1900 <= completed_water_year < wy, "Only a completed WY can reconcile")
        lo = max(start,datetime(completed_water_year-1,10,1,8,tzinfo=timezone.utc));end = datetime(completed_water_year,10,1,8,tzinfo=timezone.utc)
    interval = dict(start=format_utc(lo),end=format_utc(end)) if lo<end else None
    return dict(schema_version="dendra-refresh-planning-1",mode=mode,identity=inventory.identity(sid),as_of=as_of,
        interval=interval,fixed_pst_offset="-08:00",overlap_days=7 if mode=="ROUTINE_REFRESH" else None,
        replacement="replace_retained_native_inside_exact_valid_complete_half_open_query_only",
        earlier_history="untouched", recompute_completed_days=None if interval is None else dict(
            first=lo.astimezone(PST).date().isoformat(),last=(end-timedelta(microseconds=1)).astimezone(PST).date().isoformat()),
        reconciliation_frequency="UNSET",network_execution_authorized=False,publication_eligible=False)
