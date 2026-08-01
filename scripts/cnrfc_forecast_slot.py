#!/usr/bin/env python3
"""Return the stable Pacific logical slot for a scheduled or repair run."""

from __future__ import annotations

import argparse
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


SLOT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}-(AM|PM)$")
PACIFIC = ZoneInfo("America/Los_Angeles")


def validate_override(value: str) -> str:
    if not SLOT_PATTERN.fullmatch(value):
        raise ValueError("logical slot must use YYYY-MM-DD-AM or YYYY-MM-DD-PM")
    datetime.strptime(value[:10], "%Y-%m-%d")
    return value


def logical_slot(now: datetime) -> str:
    local = now.astimezone(PACIFIC)
    candidates: list[tuple[datetime, str]] = []
    for day_offset in (-1, 0):
        day = local.date() + timedelta(days=day_offset)
        candidates.extend(
            (
                (datetime.combine(day, time(10, 56), PACIFIC), f"{day.isoformat()}-AM"),
                (datetime.combine(day, time(16, 56), PACIFIC), f"{day.isoformat()}-PM"),
            )
        )
    eligible = [candidate for candidate in candidates if candidate[0] <= local]
    if not eligible:
        raise RuntimeError("could not resolve a prior Pacific collection slot")
    return max(eligible, key=lambda candidate: candidate[0])[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--override", default="")
    parser.add_argument("--now", help="ISO timestamp used only for deterministic tests")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.override:
        print(validate_override(args.override))
        return 0
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(tz=PACIFIC)
    if now.tzinfo is None:
        raise ValueError("--now must include a UTC offset")
    print(logical_slot(now))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
