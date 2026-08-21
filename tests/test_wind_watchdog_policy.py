#!/usr/bin/env python3
"""Deterministic tests for the wind watchdog's dispatch policy."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.wind_watchdog_policy import (
    HEALTHY_NO_ACTION,
    STALE_DISPATCHED,
    STALE_SUPPRESSED_ACTIVE_RUN,
    STALE_SUPPRESSED_IMMINENT_SCHEDULE,
    STALE_SUPPRESSED_RECENT_RUN,
    decide_repair_action,
    schedule_bounds,
)


UTC = timezone.utc
REPO = Path(__file__).resolve().parents[1]
WATCHDOG = REPO / ".github" / "workflows" / "check-wind-feeds.yml"


def at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def workflow_run(
    *,
    run_id: int,
    event: str = "schedule",
    status: str = "completed",
    conclusion: str | None = "success",
    created_at: str = "2026-08-20T12:00:00Z",
    started_at: str | None = None,
    updated_at: str = "2026-08-20T12:05:00Z",
    branch: str = "main",
) -> dict[str, object]:
    return {
        "id": run_id,
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "created_at": created_at,
        "run_started_at": started_at or created_at,
        "updated_at": updated_at,
        "head_branch": branch,
    }


class WindWatchdogPolicyTests(unittest.TestCase):
    def decide(
        self,
        *,
        stale: bool = True,
        now: str = "2026-08-20T12:33:00Z",
        runs: list[dict[str, object]] | None = None,
        ref: str = "main",
        schedules: list[str] | None = None,
        before: float = 10,
        after: float = 45,
        cooldown: float = 15,
    ):
        return decide_repair_action(
            stale=stale,
            now=at(now),
            runs=runs or [],
            ref=ref,
            cooldown_minutes=cooldown,
            schedules=schedules if schedules is not None else ["47 * * * *"],
            schedule_before_minutes=before,
            schedule_after_minutes=after,
        )

    def test_stale_without_relevant_run_dispatches(self) -> None:
        decision = self.decide()
        self.assertEqual(decision.code, STALE_DISPATCHED)
        self.assertTrue(decision.should_dispatch)

    def test_queued_run_suppresses(self) -> None:
        run = workflow_run(
            run_id=101,
            status="queued",
            conclusion=None,
            created_at="2026-08-20T12:19:00Z",
            updated_at="2026-08-20T12:19:00Z",
        )
        decision = self.decide(runs=[run])
        self.assertEqual(decision.code, STALE_SUPPRESSED_ACTIVE_RUN)
        self.assertIn("run_id=101", decision.detail)
        self.assertIn("status=queued", decision.detail)

    def test_in_progress_and_waiting_runs_suppress(self) -> None:
        for index, status in enumerate(("in_progress", "waiting"), start=102):
            with self.subTest(status=status):
                run = workflow_run(
                    run_id=index,
                    status=status,
                    conclusion=None,
                    created_at="2026-08-20T12:10:00Z",
                    updated_at="2026-08-20T12:19:00Z",
                )
                decision = self.decide(runs=[run])
                self.assertEqual(decision.code, STALE_SUPPRESSED_ACTIVE_RUN)

    def test_recent_successful_scheduled_and_manual_runs_suppress(self) -> None:
        for index, event in enumerate(("schedule", "workflow_dispatch"), start=201):
            with self.subTest(event=event):
                run = workflow_run(
                    run_id=index,
                    event=event,
                    created_at="2026-08-20T12:25:00Z",
                    updated_at="2026-08-20T12:31:00Z",
                )
                decision = self.decide(runs=[run])
                self.assertEqual(decision.code, STALE_SUPPRESSED_RECENT_RUN)
                self.assertIn(f"event={event}", decision.detail)
                self.assertIn("completed 2.0 min ago", decision.detail)

    def test_recent_failed_and_cancelled_runs_do_not_block_repair(self) -> None:
        for index, conclusion in enumerate(("failure", "cancelled"), start=301):
            with self.subTest(conclusion=conclusion):
                run = workflow_run(
                    run_id=index,
                    conclusion=conclusion,
                    created_at="2026-08-20T12:10:00Z",
                    updated_at="2026-08-20T12:19:00Z",
                )
                decision = self.decide(runs=[run])
                self.assertEqual(decision.code, STALE_DISPATCHED)

    def test_imminent_and_recently_due_schedule_suppress(self) -> None:
        imminent = self.decide(now="2026-08-20T12:40:00Z")
        self.assertEqual(imminent.code, STALE_SUPPRESSED_IMMINENT_SCHEDULE)
        self.assertIn("is in 7.0 min", imminent.detail)

        late = self.decide(now="2026-08-20T12:52:00Z")
        self.assertEqual(late.code, STALE_SUPPRESSED_IMMINENT_SCHEDULE)
        self.assertIn("was due 5.0 min ago", late.detail)

    def test_schedule_grace_expiry_allows_dispatch(self) -> None:
        decision = self.decide(now="2026-08-20T12:33:00Z")
        self.assertEqual(decision.code, STALE_DISPATCHED)

    def test_healthy_feed_never_consults_run_or_schedule_state(self) -> None:
        active = workflow_run(
            run_id=401,
            status="queued",
            conclusion=None,
            created_at="2026-08-20T12:20:00Z",
            updated_at="2026-08-20T12:20:00Z",
        )
        decision = self.decide(
            stale=False,
            runs=[active],
            schedules=["not a cron"],
        )
        self.assertEqual(decision.code, HEALTHY_NO_ACTION)
        self.assertFalse(decision.should_dispatch)

    def test_second_watchdog_check_sees_first_dispatch_as_active(self) -> None:
        first = self.decide(now="2026-08-20T12:33:00Z")
        self.assertEqual(first.code, STALE_DISPATCHED)

        visible_dispatch = workflow_run(
            run_id=501,
            event="workflow_dispatch",
            status="queued",
            conclusion=None,
            created_at="2026-08-20T12:33:01Z",
            updated_at="2026-08-20T12:33:01Z",
        )
        second = self.decide(
            now="2026-08-20T12:33:02Z",
            runs=[visible_dispatch],
        )
        self.assertEqual(second.code, STALE_SUPPRESSED_ACTIVE_RUN)

    def test_other_branch_run_is_not_equivalent(self) -> None:
        feature_run = workflow_run(
            run_id=601,
            status="queued",
            conclusion=None,
            branch="feature",
            created_at="2026-08-20T12:19:00Z",
            updated_at="2026-08-20T12:19:00Z",
        )
        decision = self.decide(runs=[feature_run])
        self.assertEqual(decision.code, STALE_DISPATCHED)

    def test_feature_branch_does_not_use_main_only_schedule(self) -> None:
        decision = self.decide(
            now="2026-08-20T12:47:00Z",
            ref="feature",
            schedules=[],
        )
        self.assertEqual(decision.code, STALE_DISPATCHED)
        self.assertIn("no normal schedule applies", decision.detail)

    def test_utc_schedule_math_handles_hour_and_day_rollover(self) -> None:
        previous, following = schedule_bounds(
            at("2026-08-21T00:02:00Z"), ["55 * * * *"]
        )
        self.assertEqual(previous, at("2026-08-20T23:55:00Z"))
        self.assertEqual(following, at("2026-08-21T00:55:00Z"))

    def test_multiple_daily_slots_and_different_cadences_are_utc(self) -> None:
        cases = (
            (
                "ASOS",
                "2026-08-20T13:05:00Z",
                ["17,42 * * * *"],
                15,
                5,
            ),
            ("GFS", "2026-08-21T01:43:00Z", ["47 * * * *"], 10, 45),
            ("HRRR", "2026-08-20T10:36:00Z", ["33 * * * *"], 10, 45),
            (
                "NBM",
                "2026-08-20T19:37:28Z",
                ["23 1,7,13,19 * * *"],
                15,
                45,
            ),
        )
        for name, now, schedules, before, after in cases:
            with self.subTest(product=name):
                decision = self.decide(
                    now=now,
                    schedules=schedules,
                    before=before,
                    after=after,
                )
                self.assertEqual(
                    decision.code, STALE_SUPPRESSED_IMMINENT_SCHEDULE
                )

    def test_each_product_retains_a_nominal_last_resort_check(self) -> None:
        # Watchdog nominal checks are :05/:20/:35/:50 UTC. Each product must
        # have at least one check outside schedule grace if no run appears.
        cases = (
            ("ASOS", "2026-08-20T12:50:00Z", ["17,42 * * * *"], 15, 5),
            ("GFS", "2026-08-20T12:35:00Z", ["47 * * * *"], 10, 45),
            ("HRRR", "2026-08-20T12:20:00Z", ["33 * * * *"], 10, 45),
            (
                "NBM",
                "2026-08-20T15:20:00Z",
                ["23 1,7,13,19 * * *"],
                15,
                45,
            ),
        )
        for name, now, schedules, before, after in cases:
            with self.subTest(product=name):
                decision = self.decide(
                    now=now,
                    schedules=schedules,
                    before=before,
                    after=after,
                )
                self.assertEqual(decision.code, STALE_DISPATCHED)

    def test_observed_nbm_duplicate_is_suppressed_by_new_policy(self) -> None:
        old_success = workflow_run(
            run_id=32377945494,
            created_at="2026-08-20T14:04:27Z",
            updated_at="2026-08-20T14:10:44Z",
        )
        # Run 32409659425 checked at 19:37:28Z. The captured previous NBM run
        # was completed and had started more than the old 35-minute cooldown
        # ago. NBM had no schedule slots in the old policy, so it dispatched
        # run 32409679587.
        observed_now = at("2026-08-20T19:37:28Z")
        legacy_start_age = (
            observed_now - at(str(old_success["run_started_at"]))
        ).total_seconds() / 60
        old_policy_would_dispatch = (
            old_success["status"] not in {"queued", "in_progress"}
            and legacy_start_age >= 35
        )
        self.assertTrue(old_policy_would_dispatch)

        decision = self.decide(
            now=observed_now.isoformat(),
            runs=[old_success],
            schedules=["23 1,7,13,19 * * *"],
            before=15,
            after=45,
        )
        self.assertEqual(decision.code, STALE_SUPPRESSED_IMMINENT_SCHEDULE)
        self.assertIn("2026-08-20T19:23:00Z", decision.detail)
        self.assertIn("14.5 min ago", decision.detail)

    def test_legitimate_pre_change_nbm_rescue_still_dispatches(self) -> None:
        # Historical run 32305289422 repaired the 12Z cycle after the nearby
        # normal run had been cancelled. Failed/cancelled completion is not a
        # cooldown signal, and the next 01:23Z slot was hours away.
        cancelled = workflow_run(
            run_id=32294741947,
            conclusion="cancelled",
            created_at="2026-08-19T19:45:02Z",
            updated_at="2026-08-19T21:24:22Z",
        )
        decision = self.decide(
            now="2026-08-19T21:42:50Z",
            runs=[cancelled],
            schedules=["23 1,7,13,19 * * *"],
            before=15,
            after=45,
        )
        self.assertEqual(decision.code, STALE_DISPATCHED)


class WindWatchdogWorkflowIntegrationTests(unittest.TestCase):
    def test_all_watched_products_use_the_shared_policy_and_current_crons(self) -> None:
        text = WATCHDOG.read_text(encoding="utf-8")
        self.assertIn("from scripts.wind_watchdog_policy import (", text)
        self.assertEqual(text.count("decide_repair_action("), 1)
        for workflow, cron in (
            ("build-asos-awos-wind-latest.yml", "17,42 * * * *"),
            ("build-gfs-wind-latest.yml", "47 * * * *"),
            ("build-hrrr-wind-latest.yml", "33 * * * *"),
            ("build-nbm-wind-guidance-latest.yml", "23 1,7,13,19 * * *"),
        ):
            self.assertIn(f'"workflow": "{workflow}"', text)
            self.assertIn(f'"schedules": ["{cron}"]', text)

    def test_run_state_lookup_is_ref_scoped_retried_and_fail_safe(self) -> None:
        text = WATCHDOG.read_text(encoding="utf-8")
        self.assertIn("?branch={branch}&per_page=30", text)
        self.assertIn('(3 if method == "GET" else 1)', text)
        self.assertIn("STALE_SUPPRESSED_RUN_STATE_UNAVAILABLE", text)
        self.assertIn("dispatch withheld", text)
        self.assertIn("if REF == \"main\" else []", text)


if __name__ == "__main__":
    unittest.main()
