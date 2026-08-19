#!/usr/bin/env python3
"""Repository-wide structural APT mirror and workflow-control checks."""

from __future__ import annotations

import re
import unittest
from dataclasses import dataclass
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
HELPER_COMMAND = "sudo bash scripts/configure_apt_mirror_order.sh"

EXPECTED_SCHEDULES = {
    "build-asos-awos-wind-latest.yml": (("17,42 * * * *", None),),
    "build-cbrfc-major-water-supply-forecasts.yml": (
        ("14 8 * * *", "America/Los_Angeles"),
    ),
    "build-cdec-reservoir-feed.yml": (("29 */3 * * *", None),),
    "build-cocorahs-daily-precip-feed.yml": (("37 0,8,16 * * *", None),),
    "build-delta-ops-daily-summary.yml": (
        ("41 15 * * *", None),
        ("11,41 16 * * *", None),
        ("11,41 17 * * *", None),
    ),
    "build-gfs-wind-latest.yml": (("47 * * * *", None),),
    "build-hrrr-wind-latest.yml": (("33 * * * *", None),),
    "build-hrrr-wind-sandbox.yml": (),
    "build-major-water-supply-basin-forecasts.yml": (
        ("56 10,16 * * *", "America/Los_Angeles"),
    ),
    "build-nbm-qpf.yml": (
        ("18 2,8,14,20 * * *", None),
        ("10 3,9,15,21 * * *", None),
    ),
    "build-nbm-wind-guidance-latest.yml": (("23 1,7,13,19 * * *", None),),
    "build-scan-soil-moisture-latest.yml": (("30 14 * * *", None),),
    "build-snow-pillow-latest.yml": (("52 5,13,21 * * *", None),),
    "build-usgs-groundwater-latest-ca.yml": (("41 13 * * *", None),),
    "build-usgs-streamflow-latest-ca.yml": (("23 */4 * * *", None),),
    "build-winter-storm-levels.yml": (
        ("8 2,8,14,20 * * *", None),
        ("54 2,8,14,20 * * *", None),
    ),
    "check-wind-feeds.yml": (("5,20,35,50 * * * *", None),),
    "preview-usgs-groundwater-candidates.yml": (("37 13 * * 1", None),),
}

SUPPORT_WORKFLOWS = {
    "build-hrrr-wind-sandbox.yml",
    "check-wind-feeds.yml",
    "preview-usgs-groundwater-candidates.yml",
}
WRITERS = frozenset(EXPECTED_SCHEDULES) - SUPPORT_WORKFLOWS
EXPECTED_APT_JOBS = {
    (workflow, "prepare-candidate") for workflow in WRITERS
} | {
    ("build-nbm-qpf.yml", "publish-to-main"),
    ("build-hrrr-wind-sandbox.yml", "build-sandbox"),
    ("preview-usgs-groundwater-candidates.yml", "preview-candidates"),
}

WRITER_CONCURRENCY = {
    "group": "live-data-feed-writes-${{ github.ref }}",
    "cancel-in-progress": "false",
    "queue": "max",
}
PUBLISH_CONCURRENCY = {
    "group": "brim-live-main-publish",
    "cancel-in-progress": "false",
    "queue": "max",
}

SCHEDULE_RE = re.compile(
    r'(?m)^    - cron: "([^"]+)"(?:\n      timezone: "([^"]+)")?'
)
JOB_RE = re.compile(r"^  ([A-Za-z0-9_-]+):$")
STEP_RE = re.compile(r"^      - ")
SUPPORTED_UBUNTU_RE = re.compile(r"^ubuntu-(?:latest|\d{2}\.\d{2})$")
APT_ACTION_RE = re.compile(r"^r-lib/actions/setup-r(?:-dependencies)?@")
APT_COMMAND_RE = re.compile(
    r"(?m)^\s*(?:sudo\s+)?apt(?:-get)?\s+(?:update|install)\b"
)


def scalar(lines: list[str], prefix: str) -> str | None:
    for line in lines:
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def step_scalar(lines: list[str], key: str) -> str | None:
    inline_prefix = f"      - {key}:"
    if lines and lines[0].startswith(inline_prefix):
        return lines[0][len(inline_prefix) :].strip()
    return scalar(lines, f"        {key}:")


def run_value(lines: list[str]) -> str:
    for index, line in enumerate(lines):
        prefixes = ("      - run:", "        run:")
        prefix = next((item for item in prefixes if line.startswith(item)), None)
        if prefix is None:
            continue
        value = line[len(prefix) :].strip()
        if value not in {"|", "|-", ">", ">-"}:
            return value
        return "\n".join(following[10:] for following in lines[index + 1 :])
    return ""


def mapping(lines: list[str], indent: int, key: str) -> dict[str, str] | None:
    prefix = " " * indent + f"{key}:"
    for index, line in enumerate(lines):
        if not line.startswith(prefix):
            continue
        suffix = line[len(prefix) :].strip()
        if suffix == "{}":
            return {}
        values: dict[str, str] = {}
        child_indent = indent + 2
        child_re = re.compile(
            rf"^ {{{child_indent}}}([A-Za-z0-9_-]+):\s*(.*?)\s*$"
        )
        for following in lines[index + 1 :]:
            if following and len(following) - len(following.lstrip()) <= indent:
                break
            match = child_re.match(following)
            if match:
                values[match.group(1)] = match.group(2).strip('"')
        return values
    return None


@dataclass(frozen=True)
class Step:
    name: str
    uses: str | None
    condition: str | None
    run: str

    @property
    def exposes_apt(self) -> bool:
        return bool(
            (self.uses and APT_ACTION_RE.match(self.uses))
            or APT_COMMAND_RE.search(self.run)
        )

    @property
    def invokes_helper(self) -> bool:
        return HELPER_COMMAND in self.run


@dataclass(frozen=True)
class Job:
    name: str
    runs_on: str | None
    has_container: bool
    lines: list[str]
    steps: list[Step]

    @property
    def is_supported_ubuntu(self) -> bool:
        return bool(
            self.runs_on
            and SUPPORTED_UBUNTU_RE.fullmatch(self.runs_on)
            and not self.has_container
        )


def parse_jobs(path: Path) -> dict[str, Job]:
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        jobs_index = lines.index("jobs:")
    except ValueError as exc:
        raise AssertionError(f"{path.name} has no jobs mapping") from exc

    starts = [
        (index, match.group(1))
        for index, line in enumerate(lines[jobs_index + 1 :], jobs_index + 1)
        if (match := JOB_RE.match(line))
    ]
    jobs: dict[str, Job] = {}
    for position, (start, job_name) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        job_lines = lines[start + 1 : end]
        step_starts = [
            index for index, line in enumerate(job_lines) if STEP_RE.match(line)
        ]
        steps: list[Step] = []
        for step_position, step_start in enumerate(step_starts):
            step_end = (
                step_starts[step_position + 1]
                if step_position + 1 < len(step_starts)
                else len(job_lines)
            )
            step_lines = job_lines[step_start:step_end]
            name = step_scalar(step_lines, "name") or "(unnamed)"
            steps.append(
                Step(
                    name=name,
                    uses=step_scalar(step_lines, "uses"),
                    condition=step_scalar(step_lines, "if"),
                    run=run_value(step_lines),
                )
            )
        jobs[job_name] = Job(
            name=job_name,
            runs_on=scalar(job_lines, "    runs-on:"),
            has_container=any(
                line == "    container:" or line.startswith("    container: ")
                for line in job_lines
            ),
            lines=job_lines,
            steps=steps,
        )
    return jobs


class AptWorkflowHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.paths = sorted(WORKFLOWS.glob("*.yml"))
        cls.jobs = {path.name: parse_jobs(path) for path in cls.paths}

    def test_active_workflow_and_apt_exposure_inventory_is_complete(self) -> None:
        self.assertEqual({path.name for path in self.paths}, set(EXPECTED_SCHEDULES))
        self.assertEqual(sum(len(jobs) for jobs in self.jobs.values()), 33)
        apt_jobs = {
            (workflow, job_name)
            for workflow, jobs in self.jobs.items()
            for job_name, job in jobs.items()
            if any(step.exposes_apt for step in job.steps)
        }
        self.assertEqual(apt_jobs, EXPECTED_APT_JOBS)
        self.assertEqual(len(apt_jobs), 18)

    def test_supported_apt_jobs_have_one_effective_helper_before_first_use(self) -> None:
        for workflow, jobs in self.jobs.items():
            for job_name, job in jobs.items():
                exposures = [
                    index for index, step in enumerate(job.steps) if step.exposes_apt
                ]
                if not exposures or not job.is_supported_ubuntu:
                    continue
                helpers = [
                    index for index, step in enumerate(job.steps) if step.invokes_helper
                ]
                identity = f"{workflow}:{job_name}"
                self.assertEqual(len(helpers), 1, identity)
                helper_index = helpers[0]
                self.assertLess(helper_index, exposures[0], identity)
                checkout_indexes = [
                    index
                    for index, step in enumerate(job.steps)
                    if step.uses and step.uses.startswith("actions/checkout@")
                ]
                self.assertTrue(
                    any(index < helper_index for index in checkout_indexes), identity
                )
                helper_condition = job.steps[helper_index].condition
                if helper_condition is not None:
                    self.assertTrue(
                        all(
                            job.steps[index].condition == helper_condition
                            for index in exposures
                        ),
                        identity,
                    )

    def test_no_apt_and_unsupported_jobs_do_not_invoke_helper(self) -> None:
        for workflow, jobs in self.jobs.items():
            for job_name, job in jobs.items():
                has_exposure = any(step.exposes_apt for step in job.steps)
                helpers = [step for step in job.steps if step.invokes_helper]
                identity = f"{workflow}:{job_name}"
                if not has_exposure or not job.is_supported_ubuntu:
                    self.assertEqual(helpers, [], identity)

    def test_schedules_permissions_and_concurrency_retain_baseline(self) -> None:
        for path in self.paths:
            text = path.read_text(encoding="utf-8")
            self.assertEqual(
                tuple(
                    (cron, timezone or None)
                    for cron, timezone in SCHEDULE_RE.findall(text)
                ),
                EXPECTED_SCHEDULES[path.name],
                path.name,
            )

        for workflow in WRITERS:
            path = WORKFLOWS / workflow
            lines = path.read_text(encoding="utf-8").splitlines()
            jobs = self.jobs[workflow]
            self.assertEqual(mapping(lines, 0, "permissions"), {}, workflow)
            self.assertEqual(
                mapping(lines, 0, "concurrency"), WRITER_CONCURRENCY, workflow
            )
            self.assertEqual(
                set(jobs), {"prepare-candidate", "publish-to-main"}, workflow
            )
            self.assertEqual(
                mapping(jobs["prepare-candidate"].lines, 4, "permissions"),
                {"contents": "read"},
                workflow,
            )
            self.assertEqual(
                mapping(jobs["publish-to-main"].lines, 4, "permissions"),
                {"contents": "write"},
                workflow,
            )
            self.assertEqual(
                mapping(jobs["publish-to-main"].lines, 4, "concurrency"),
                PUBLISH_CONCURRENCY,
                workflow,
            )

        sandbox = (WORKFLOWS / "build-hrrr-wind-sandbox.yml").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(mapping(sandbox, 0, "permissions"), {"contents": "read"})
        self.assertEqual(
            mapping(sandbox, 0, "concurrency"),
            {
                "group": "hrrr-wind-sandbox-${{ github.ref }}",
                "cancel-in-progress": "true",
            },
        )

        watchdog = (WORKFLOWS / "check-wind-feeds.yml").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(
            mapping(watchdog, 0, "permissions"),
            {"actions": "write", "contents": "read"},
        )
        self.assertEqual(
            mapping(watchdog, 0, "concurrency"),
            {
                "group": "wind-feed-watchdog-${{ github.ref }}",
                "cancel-in-progress": "true",
            },
        )

        preview = (WORKFLOWS / "preview-usgs-groundwater-candidates.yml").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(mapping(preview, 0, "permissions"), {"contents": "read"})
        self.assertIsNone(mapping(preview, 0, "concurrency"))


if __name__ == "__main__":
    unittest.main()
