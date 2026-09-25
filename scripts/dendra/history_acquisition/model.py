"""Frozen identities, metadata overlays and pure bounded request planning."""
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
import math
import re

from ..transport import parse_utc, format_utc
from .safety import Root, decode, encode, digest, sha, require

INVENTORY_SHA256 = "f81b91bd86ade8e5380063e1ce3b37d1dddb1ca5d21368cd3ecb4514688190d9"
VERSION = "dendra-history-acquisition-offline-1"
POLICY = "dendra-history-request-1"
ID = re.compile(r"[0-9a-f]{24}")
HASH = re.compile(r"[0-9a-f]{64}")
NAME = re.compile(r"[A-Za-z0-9_-]{1,80}")
PAGE_BYTES = 262144
PAGE_ENTRIES = 128
MAX_PLAN = 4096
UNIT_STATUS = {"Percent": "verified_percent_conversion",
               "VolumetricWaterContent": "verified_percent_conversion",
               "Dimensionless": "native_only_scale_unresolved"}


def identities(document):
    require(document.get("version") == "0.2.0-review", "Inventory version")
    stations, streams = set(), {}
    for station in document["stations"]:
        sid = station["id"]
        require(isinstance(sid, str) and ID.fullmatch(sid) and sid not in stations,
                "Duplicate or invalid station")
        stations.add(sid)
        for stream in station["catalog"]:
            ident = stream["id"]
            require(isinstance(ident, str) and ID.fullmatch(ident) and ident not in streams,
                    "Duplicate or invalid stream")
            depth = stream["depth"]
            require(depth is None or (type(depth) in (float, int) and math.isfinite(depth)),
                    "Invalid depth")
            require(stream["orientation"] is None or isinstance(stream["orientation"], str),
                    "Invalid orientation")
            require(stream["unit"] in UNIT_STATUS and
                    stream["unit_status"] == UNIT_STATUS[stream["unit"]], "Unit/status mismatch")
            streams[ident] = dict(station_id=sid, stream_id=ident, depth_cm=depth,
                                  orientation=stream["orientation"], native_unit=stream["unit"],
                                  unit_status=stream["unit_status"])
    require(len(stations) == 122 and len(streams) == 434, "Exact inventory closure/count")
    require(sum(s["native_unit"] == "Dimensionless" for s in streams.values()) == 97,
            "Exact unresolved roster")
    require(sum(s["native_unit"] == "Percent" for s in streams.values()) == 177 and
            sum(s["native_unit"] == "VolumetricWaterContent" for s in streams.values()) == 160,
            "Exact resolved roster")
    return dict(sorted(streams.items()))


@dataclass(frozen=True)
class Inventory:
    """Immutable serialized closure; callers receive copies, not mutable authority."""
    closure: bytes
    source: bytes

    def __post_init__(self):
        require(len(self.source) == 3534468 and sha(self.source) == INVENTORY_SHA256 and
                self.closure == encode(identities(decode(self.source))), "Unbound inventory closure")

    @classmethod
    def load(cls, path, expected_sha256):
        require(expected_sha256 == INVENTORY_SHA256, "Unapproved inventory hash")
        path = Path(path)
        with Root(path.parent) as root:
            body = root.read(path.name, 8 * 1024**2)
        require(len(body) == 3534468 and sha(body) == expected_sha256, "Inventory bytes changed")
        return cls(encode(identities(decode(body))), body)

    def roster(self):
        return decode(self.closure)

    def check_document(self, document):
        require(encode(identities(document)) == self.closure, "Frozen identity changed")

    def identity(self, stream_id):
        require(stream_id in self.roster(), "Unknown selected stream")
        return self.roster()[stream_id]


def unit_route(identity):
    unit = identity["native_unit"]
    resolved = identity["unit_status"] == "verified_percent_conversion"
    return dict(native_unit=unit, unit_status=identity["unit_status"],
                percent_identity_resolved=resolved,
                multiplier=(1 if unit == "Percent" else 100) if resolved else None,
                offset=0 if resolved else None,
                # This package creates no accepted daily values/browser capability.
                percent_product_eligible=False, browser_history_available=False,
                route="resolved_native" if resolved else "unresolved_native_diagnostic")


def metadata_view(identity, claims, *, checked_at, now, scientific_sha256, dictionary_sha256):
    """Sanitized overlay only; never return arbitrary claims or protected geometry."""
    require(isinstance(claims, dict) and len(encode(claims)) <= PAGE_BYTES, "Metadata input bound")
    for field in ("public_level", "station_public_level"):
        require(claims.get(field) is None or (type(claims[field]) is int and 0 <= claims[field] <= 3),
                "Public-level type")
    for field in ("is_hidden", "station_is_hidden", "geo_protected"):
        require(claims.get(field) is None or type(claims[field]) is bool, "Privacy flag type")
    fresh = (parse_utc(checked_at) <= parse_utc(now) and
             parse_utc(now) - parse_utc(checked_at) <= timedelta(hours=24))
    reasons = []
    if claims.get("complete") is not True or not fresh:
        reasons.append("metadata_incomplete_or_stale")
    if any(claims.get(k) != v for k, v in identity.items()):
        reasons.append("scientific_identity_changed")
    scientific = claims.get("scientific_fields")
    scientific_claim = digest(scientific) if isinstance(scientific, dict) else None
    if (scientific_claim != scientific_sha256 or claims.get("scientific_sha256") != scientific_sha256 or
            claims.get("dictionary_sha256") != dictionary_sha256):
        reasons.append("scientific_binding_changed")
    access = claims.get("access_state", "metadata-only")
    require(access in ("accessible", "metadata-only", "private/protected",
                       "missing/deleted", "request-failed"), "Unknown access state")
    public = (access == "accessible" and claims.get("public_level") == 3 and
              claims.get("is_hidden") is False and claims.get("station_public_level") == 3 and
              claims.get("station_is_hidden") is False)
    if not public:
        reasons.append("access_hold")
    protected = claims.get("geo_protected") is not False
    geometry = None
    if public and fresh and claims.get("complete") is True and not protected:
        xy = claims.get("geometry")
        if xy is not None:
            require(isinstance(xy, list) and len(xy) == 2 and
                    all(type(x) in (float, int) and math.isfinite(x) for x in xy) and
                    -180 <= xy[0] <= 180 and -90 <= xy[1] <= 90, "Invalid public geometry")
            geometry = xy
    name = claims.get("display_name")
    require(name is None or (isinstance(name, str) and len(name) <= 256), "Display name bound")
    activity = claims.get("activity", "unknown")
    require(activity in ("unknown", "active", "inactive/ended"), "Activity state")
    unexpected = claims.get("unexpected_ids", [])
    require(isinstance(unexpected, list) and len(unexpected) <= 500 and
            all(isinstance(s, str) and ID.fullmatch(s) for s in unexpected), "Unexpected-ID diagnostic bound")
    witnesses = {}
    source_witnesses = claims.get("witnesses", {})
    require(isinstance(source_witnesses, dict) and len(source_witnesses) <= 2, "Witness count")
    for key, witness in source_witnesses.items():
        require(key in ("first", "latest") and isinstance(witness, dict) and
                set(witness) == {"t", "response_sha256"} and HASH.fullmatch(witness["response_sha256"]),
                "Witness diagnostic shape")
        if witness["t"] is not None:
            parse_utc(witness["t"])
        if public:
            witnesses[key] = witness
    pagination = claims.get("pagination", {})
    require(isinstance(pagination, dict) and set(pagination) <= {"effective_limit", "row_count"} and
            all(type(n) is int and 0 <= n <= 1000000 for n in pagination.values()), "Pagination diagnostic")
    clocks = {}
    for key in ("metadata_updated_at", "ended_at"):
        value = claims.get(key)
        if value is not None:
            parse_utc(value)
        clocks[key] = value if public else None
    cadence = scientific.get("cadence_seconds") if isinstance(scientific, dict) else None
    require(cadence is None or (type(cadence) in (int, float) and math.isfinite(cadence)),
            "Cadence diagnostic")
    diagnostic = dict(scientific_claim_sha256=scientific_claim,
                      expected_scientific_sha256=scientific_sha256,
                      identity_claim_sha256=digest({k: claims.get(k) for k in identity}),
                      cadence_claim_seconds=cadence if public else None,
                      unexpected_id_count=len(unexpected), unexpected_ids_sha256=digest(unexpected),
                      pagination=pagination, witnesses=witnesses)
    return dict(identity=identity, identity_sha256=digest(identity), checked_at=checked_at,
                access_state=access, activity_state=activity, coordinate_protected=protected,
                display_name=name if public else None, geometry=geometry,
                metadata_revision=str(claims.get("revision", ""))[:128] if public else None,
                public_level=claims.get("public_level"), is_hidden=claims.get("is_hidden"),
                station_public_level=claims.get("station_public_level"),
                station_is_hidden=claims.get("station_is_hidden"),
                raw_eligible=not reasons, hold_reasons=reasons, unit=unit_route(identity),
                diagnostics=diagnostic, **clocks)


def boundary(value):
    stamp = parse_utc(value)
    require((stamp.hour, stamp.minute, stamp.second, stamp.microsecond) == (8, 0, 0, 0),
            "Fixed-PST boundary must be 08:00Z")
    return stamp


def source_binding():
    here = Path(__file__).parent
    paths = sorted(here.glob("*.py")) + [here.parent / "transport.py", here.parent / "core.R"]
    return {str(p.relative_to(here.parent)): sha(p.read_bytes()) for p in paths}


def campaign(inventory, *, campaign_id, selected_ids, as_of, horizons, metadata_bindings,
             dictionary_sha256, budgets, request_generation, parent=None):
    require(NAME.fullmatch(campaign_id) and NAME.fullmatch(request_generation), "Campaign/generation ID")
    require(selected_ids and len(selected_ids) == len(set(selected_ids)), "Duplicate/empty selection")
    selected = sorted(selected_ids)
    roster = inventory.roster()
    require(set(selected) <= roster.keys(), "Selection outside inventory")
    require(set(metadata_bindings) == set(selected), "Metadata binding closure")
    require(HASH.fullmatch(dictionary_sha256) and
            all(isinstance(v, str) and HASH.fullmatch(v) for v in metadata_bindings.values()),
            "Explicit dictionary/scientific hashes required")
    keys = {"logical_requests", "attempts", "response_bytes", "source_rows",
            "intervals", "elapsed_ms", "sessions"}
    require(set(budgets) == keys and all(type(v) is int and v >= 0 for v in budgets.values()),
            "Explicit finite budgets required")
    require(set(horizons) <= set(selected), "Horizon outside selection")
    cutoff = (parse_utc(as_of) - timedelta(hours=8)).date() - timedelta(days=1)
    for value in horizons.values():
        lo, hi = boundary(value["start"]), boundary(value["end"])
        require(lo < hi and hi.date() <= cutoff + timedelta(days=1), "Uncompleted/invalid horizon")
    if parent is not None:
        require(set(parent) == {"role", "sha256"} and parent["role"] in ("seed", "published") and
                HASH.fullmatch(parent["sha256"]), "Explicit seed/published binding")
    value = dict(version=VERSION, mode="offline_only", campaign_id=campaign_id,
                 inventory_sha256=INVENTORY_SHA256, inventory_version="0.2.0-review",
                 roster=roster, selected_ids=selected, as_of=format_utc(as_of),
                 completed_cutoff=cutoff.isoformat(), horizons=horizons,
                 metadata_bindings=metadata_bindings, dictionary_sha256=dictionary_sha256,
                 request_policy=POLICY, collector_sources=source_binding(), budgets=budgets,
                 request_generation=request_generation, seed_parent=parent)
    require(len(encode(value)) <= PAGE_BYTES, "Campaign manifest bound")
    return decode(encode(value))


def plan(binding, intervals):
    """Explicit intervals only; no POR discovery, no implicit horizon expansion."""
    tasks = {}
    occupied = {}
    require(len(intervals) <= MAX_PLAN, "Input plan bound")
    for sid, start, end in intervals:
        require(sid in binding["selected_ids"] and sid in binding["horizons"], "Unapproved horizon")
        lo, hi = boundary(start), boundary(end)
        horizon = binding["horizons"][sid]
        require(boundary(horizon["start"]) <= lo < hi <= boundary(horizon["end"]),
                "Interval outside approved horizon")
        for a, b in occupied.setdefault(sid, []):
            require(hi <= a or lo >= b, "Overlapping plan intervals")
        occupied[sid].append((lo, hi))
        while lo < hi:
            stop = min(hi, lo + timedelta(days=30))
            item = dict(campaign_sha256=digest(binding), identity=binding["roster"][sid],
                        start=format_utc(lo), end=format_utc(stop),
                        request_generation=binding["request_generation"])
            tasks[digest(item)] = item
            require(len(tasks) <= MAX_PLAN, "Planned interval bound")
            lo = stop
    return dict(sorted(tasks.items(), key=lambda x: (x[1]["start"], x[1]["identity"]["stream_id"])))


def index_pages(entries, *, page_entries=PAGE_ENTRIES, page_bytes=PAGE_BYTES):
    require(type(page_entries) is int and 0 < page_entries <= PAGE_ENTRIES, "Index entry limit")
    require(type(page_bytes) is int and 0 < page_bytes <= PAGE_BYTES, "Index byte limit")
    require(len(entries) <= MAX_PLAN, "Index total limit")
    pages, page = [], []
    for entry in entries:
        require(len(encode([entry])) <= page_bytes, "Single index entry oversized")
        if len(page) == page_entries or len(encode(page + [entry])) > page_bytes:
            pages.append(page)
            page = []
        page.append(entry)
    if page:
        pages.append(page)
    return pages
