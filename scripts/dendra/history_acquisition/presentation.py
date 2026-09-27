"""Explicit offline presentation horizons; source-start review is not dispatch.

Audit endpoints can describe an estimate but cannot supply an executable horizon.
Trusted reviewed starts bind exact frozen identities and the evidence reviewed;
hashes establish integrity, not the reviewer's authority. No discovery or I/O.
"""
from datetime import datetime, timedelta, timezone

from ..transport import parse_utc, format_utc
from .model import Inventory, INVENTORY_SHA256, HASH
from .safety import decode, digest, encode, require

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
