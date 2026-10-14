"""Bounded native quality retention, never interpretation of provider flags.

Exact values belong only to immutable native pages. Classifications contain
fixed codes/counts/hashes; occurrence indices resolve back to those pages.
"""
import math
import unicodedata
from copy import deepcopy
from datetime import timedelta, timezone

from .safety import Hold, encode, digest, require

VERSION = "dendra-observation-quality-1"
POLICY = dict(version=VERSION, q_bytes=4096, array_items=8, string_characters=256,
              slots=["attrib", "flag", "annotation_ids", "annotationIds"],
              nonempty="quarantine", absent="allow", null="allow",
              daily="withhold_completed_fixed_pst_day")
PST = timezone(timedelta(hours=-8))


class UnsupportedQuality(Hold):
    """Unsupported source schema: stop campaign traffic, never quarantine it."""


def binding():
    return dict(policy=deepcopy(POLICY), sha256=digest(POLICY))


def validate_policy(value):
    require(encode(value) == encode(binding()), "Quality policy binding mismatch")


def _scalar(value):
    require(value is None or type(value) in (str, bool, int, float), "Unsupported quality scalar")
    if isinstance(value, str):
        require(len(value) <= 256 and all(not unicodedata.category(c).startswith("C") for c in value),
                "Unsafe quality string")
    if type(value) in (int, float):
        require(abs(value) <= 1e308 and math.isfinite(value), "Unsafe quality number")


def classify(row):
    """Validate exact supported q; no values or unknown keys in the result."""
    try:
        return _classify(row)
    except Hold as exc:
        raise UnsupportedQuality(str(exc)) from exc


def _classify(row):
    present = "q" in row
    q = row.get("q")
    if type(q) is dict:
        require(set(q) <= set(POLICY["slots"]), "Unknown quality slot")
        for key, value in q.items():
            if type(value) is list:
                require(len(value) <= 8, "Quality array bound")
                for item in value:
                    _scalar(item)
                    require(key == "attrib" or type(item) is str, "Quality identifier type")
            else:
                require(key == "attrib" or value is None, "Quality slot requires array or null")
                _scalar(value)
    else:
        _scalar(q)
    require(len(encode(q)) <= 4096, "Quality byte bound")
    state = "NO_PROVIDER_QUALITY_CLAIM" if not present else "EXPLICIT_NULL" if q is None else "PRESENT"
    # False and numeric zero are explicit scalar claims, not empty containers.
    quarantined = present and q is not None and not (type(q) in (str, dict) and len(q) == 0)
    kind = {type(None):"null", str:"string", bool:"boolean", int:"integer", float:"number", dict:"object"}[type(q)]
    return dict(presence=state, json_type=kind if present else "missing",
                sha256=digest(dict(present=present, value=q)), quarantined=quarantined,
                reason="PROVIDER_QUALITY_UNREVIEWED" if quarantined else "NO_QUALITY_VETO")


def attach(rows, source_rows):
    """Add presence/type-sensitive alternatives with exact native row locations."""
    from ..transport import parse_utc, format_utc
    groups = {}
    for index, row in enumerate(source_rows):
        stamp = format_utc(parse_utc(row["t"]))
        claim = classify(row)
        alternatives = groups.setdefault(stamp, {})
        item = alternatives.setdefault(claim["sha256"], dict(claim, source_occurrences=[]))
        item["source_occurrences"].append(index)
    for row in rows:
        alternatives = list(groups[row["t"]].values())
        row["quality"] = dict(version=VERSION, alternatives=alternatives,
                              quarantined=any(x["quarantined"] for x in alternatives))
        row.pop("source_quality_conflict", None)
        if len(alternatives) > 1:
            row["source_quality_conflict"] = True


def summary(rows):
    groups = sum(row["quality"]["quarantined"] for row in rows)
    return dict(policy=binding(), state="OBSERVATIONS_QUARANTINED" if groups else "NO_QUALITY_QUARANTINE",
                unique_observations=len(rows), quarantined_groups=groups,
                quarantined_occurrences=sum(len(a["source_occurrences"]) for row in rows
                    for a in row["quality"]["alternatives"] if a["quarantined"]),
                classifications_sha256=digest([row["quality"] for row in rows]))


def days(rows):
    from ..transport import parse_utc
    return sorted({parse_utc(row["t"]).astimezone(PST).date().isoformat() for row in rows
                   if row.get("quality", {}).get("quarantined", False)})
