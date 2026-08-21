#!/usr/bin/env python3
"""Pure decision policy for BRIM's wind-feed repair watchdog."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence


UTC = timezone.utc
ACTIVE_STATUSES = frozenset(
    {"queued", "in_progress", "requested", "waiting", "pending"}
)

HEALTHY_NO_ACTION = "HEALTHY_NO_ACTION"
STALE_DISPATCHED = "STALE_DISPATCHED"
STALE_SUPPRESSED_ACTIVE_RUN = "STALE_SUPPRESSED_ACTIVE_RUN"
STALE_SUPPRESSED_RECENT_RUN = "STALE_SUPPRESSED_RECENT_RUN"
STALE_SUPPRESSED_IMMINENT_SCHEDULE = "STALE_SUPPRESSED_IMMINENT_SCHEDULE"


@dataclass(frozen=True)
class RepairDecision:
    """One bounded, log-ready watchdog action."""

    code: str
    should_dispatch: bool
    detail: str


def utc_time(value: Any) -> datetime | None:
    """Parse a GitHub timestamp or datetime and normalize it to aware UTC."""

    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _require_utc(value: datetime) -> datetime:
    parsed = utc_time(value)
    if parsed is None:
        raise ValueError("now must be a valid datetime")
    return parsed


def _expand_cron_field(text: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for component in text.split(","):
        component = component.strip()
        if not component:
            raise ValueError(f"empty cron component in {text!r}")
        base, separator, step_text = component.partition("/")
        step = int(step_text) if separator else 1
        if step < 1:
            raise ValueError(f"invalid cron step in {component!r}")
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(base)
        if start < minimum or end > maximum or start > end:
            raise ValueError(f"cron field outside {minimum}-{maximum}: {component}")
        values.update(range(start, end + 1, step))
    return values


def _daily_schedule_values(cron: str) -> tuple[set[int], set[int]]:
    fields = cron.split()
    if len(fields) != 5:
        raise ValueError(f"expected five-field UTC cron: {cron!r}")
    minute, hour, day_of_month, month, day_of_week = fields
    if (day_of_month, month, day_of_week) != ("*", "*", "*"):
        raise ValueError(
            "wind watchdog schedule policy supports the current daily UTC "
            f"schedule shape only: {cron!r}"
        )
    return _expand_cron_field(minute, 0, 59), _expand_cron_field(hour, 0, 23)


def schedule_bounds(
    now: datetime, schedules: Sequence[str]
) -> tuple[datetime, datetime]:
    """Return the previous and next configured UTC cron slots."""

    current = _require_utc(now)
    if not schedules:
        raise ValueError("at least one schedule is required")

    slots: set[datetime] = set()
    midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
    for day_offset in (-1, 0, 1):
        day = midnight + timedelta(days=day_offset)
        for cron in schedules:
            minutes, hours = _daily_schedule_values(cron)
            slots.update(
                day.replace(hour=hour, minute=minute)
                for hour in hours
                for minute in minutes
            )

    previous = max(slot for slot in slots if slot <= current)
    following = min(slot for slot in slots if slot > current)
    return previous, following


def _run_time(run: Mapping[str, Any], keys: Sequence[str]) -> datetime | None:
    for key in keys:
        parsed = utc_time(run.get(key))
        if parsed is not None:
            return parsed
    return None


def _run_sort_time(run: Mapping[str, Any]) -> datetime:
    return _run_time(
        run,
        ("completed_at", "updated_at", "run_started_at", "created_at"),
    ) or datetime.min.replace(tzinfo=UTC)


def describe_run(run: Mapping[str, Any]) -> str:
    """Return bounded run metadata suitable for watchdog logs."""

    created = utc_time(run.get("created_at"))
    started = utc_time(run.get("run_started_at"))
    completed = _run_time(run, ("completed_at", "updated_at"))

    def rendered(value: datetime | None) -> str:
        return value.isoformat().replace("+00:00", "Z") if value else "unknown"

    return (
        f"run_id={run.get('id', 'unknown')} "
        f"event={run.get('event', 'unknown')} "
        f"status={run.get('status', 'unknown')} "
        f"conclusion={run.get('conclusion') or 'none'} "
        f"created={rendered(created)} started={rendered(started)} "
        f"completed={rendered(completed)}"
    )


def _equivalent_runs(
    runs: Sequence[Mapping[str, Any]], ref: str
) -> list[Mapping[str, Any]]:
    return [run for run in runs if run.get("head_branch") == ref]


def decide_repair_action(
    *,
    stale: bool,
    now: datetime,
    runs: Sequence[Mapping[str, Any]],
    ref: str,
    cooldown_minutes: float,
    schedules: Sequence[str],
    schedule_before_minutes: float,
    schedule_after_minutes: float,
) -> RepairDecision:
    """Choose one repair action without performing network or dispatch work."""

    current = _require_utc(now)
    if not stale:
        return RepairDecision(
            HEALTHY_NO_ACTION,
            False,
            "feed checks passed; run and schedule state not consulted",
        )

    equivalent = _equivalent_runs(runs, ref)
    active = [run for run in equivalent if run.get("status") in ACTIVE_STATUSES]
    if active:
        run = max(active, key=_run_sort_time)
        return RepairDecision(
            STALE_SUPPRESSED_ACTIVE_RUN,
            False,
            describe_run(run),
        )

    successful = [
        run
        for run in equivalent
        if run.get("status") == "completed" and run.get("conclusion") == "success"
    ]
    if successful:
        run = max(successful, key=_run_sort_time)
        completed = _run_time(
            run,
            ("completed_at", "updated_at", "run_started_at", "created_at"),
        )
        if completed is not None:
            age_minutes = (current - completed).total_seconds() / 60.0
            if -1.0 <= age_minutes < cooldown_minutes:
                return RepairDecision(
                    STALE_SUPPRESSED_RECENT_RUN,
                    False,
                    (
                        f"successful run completed {max(age_minutes, 0.0):.1f} "
                        f"min ago (< {cooldown_minutes:g} min cooldown); "
                        + describe_run(run)
                    ),
                )

    previous = following = None
    if schedules:
        previous, following = schedule_bounds(current, schedules)
        since_previous = (current - previous).total_seconds() / 60.0
        until_following = (following - current).total_seconds() / 60.0
        if since_previous <= schedule_after_minutes:
            return RepairDecision(
                STALE_SUPPRESSED_IMMINENT_SCHEDULE,
                False,
                (
                    f"normal UTC slot "
                    f"{previous.isoformat().replace('+00:00', 'Z')} was due "
                    f"{since_previous:.1f} min ago "
                    f"(<= {schedule_after_minutes:g} min late grace)"
                ),
            )
        if until_following <= schedule_before_minutes:
            return RepairDecision(
                STALE_SUPPRESSED_IMMINENT_SCHEDULE,
                False,
                (
                    f"next normal UTC slot "
                    f"{following.isoformat().replace('+00:00', 'Z')} is in "
                    f"{until_following:.1f} min "
                    f"(<= {schedule_before_minutes:g} min early grace)"
                ),
            )

    schedule_detail = (
        f"previous_slot={previous.isoformat().replace('+00:00', 'Z')} "
        f"next_slot={following.isoformat().replace('+00:00', 'Z')}"
        if previous is not None and following is not None
        else "no normal schedule applies to the selected ref"
    )

    return RepairDecision(
        STALE_DISPATCHED,
        True,
        (
            "no active or recently successful equivalent run and outside "
            f"bounded schedule grace; {schedule_detail}"
        ),
    )
