#!/usr/bin/env python3
"""Authoritative repository-wide R setup policy and workflow-control checks."""

from __future__ import annotations

import hashlib
import re
import unittest
from dataclasses import dataclass
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
HARDENED_ACTION_PATH = REPO / ".github" / "actions" / "setup-r-hardened" / "action.yml"
HARDENED_ACTION = "./.github/actions/setup-r-hardened"
APT_HELPER_COMMAND = "sudo bash scripts/configure_apt_mirror_order.sh"
PINNED_R_VERSION = "4.6.1"

PRODUCTION = "PRODUCTION_WRITER"
SUPPORT = "SUPPORT_JOB"

EXPECTED_SETUP_JOBS = {
    "build-asos-awos-wind-latest.yml": ("prepare-candidate", PRODUCTION),
    "build-cbrfc-major-water-supply-forecasts.yml": (
        "prepare-candidate",
        PRODUCTION,
    ),
    "build-cdec-reservoir-feed.yml": ("prepare-candidate", PRODUCTION),
    "build-cocorahs-daily-precip-feed.yml": ("prepare-candidate", PRODUCTION),
    "build-delta-ops-daily-summary.yml": ("prepare-candidate", PRODUCTION),
    "build-gfs-wind-latest.yml": ("prepare-candidate", PRODUCTION),
    "build-hrrr-wind-latest.yml": ("prepare-candidate", PRODUCTION),
    "build-hrrr-wind-sandbox.yml": ("build-sandbox", SUPPORT),
    "build-major-water-supply-basin-forecasts.yml": (
        "prepare-candidate",
        PRODUCTION,
    ),
    "build-nbm-qpf.yml": ("prepare-candidate", PRODUCTION),
    "build-nbm-wind-guidance-latest.yml": ("prepare-candidate", PRODUCTION),
    "build-scan-soil-moisture-latest.yml": ("prepare-candidate", PRODUCTION),
    "build-snow-pillow-latest.yml": ("prepare-candidate", PRODUCTION),
    "build-usgs-groundwater-latest-ca.yml": ("prepare-candidate", PRODUCTION),
    "build-usgs-streamflow-latest-ca.yml": ("prepare-candidate", PRODUCTION),
    "build-winter-storm-levels.yml": ("prepare-candidate", PRODUCTION),
    "preview-usgs-groundwater-candidates.yml": ("preview-candidates", SUPPORT),
}

EXPECTED_WORKFLOWS_WITHOUT_R = {"check-wind-feeds.yml"}

# These are SHA-256 fingerprints of every workflow after replacing only its
# setup-R step with one canonical marker. Any drift in triggers, permissions,
# jobs, conditions, runners, timeouts, commands, artifacts, APT setup, or
# concurrency changes the fingerprint and requires explicit review.
EXPECTED_NORMALIZED_WORKFLOW_SHA256 = {
    "build-asos-awos-wind-latest.yml": "b566e6be7822e0e869d1efeb528d705424e37ac6cf0328f487a8b121ee2d3985",
    "build-cbrfc-major-water-supply-forecasts.yml": "d96d30dd4c09c6ea7c811ff6ef1e843aec5980efea8ddc2d7333cdb07733f18c",
    "build-cdec-reservoir-feed.yml": "41b4091cca96939194eae2536dc4d8deacc1fab01dc9b3f8abfb11fb18b3299e",
    "build-cocorahs-daily-precip-feed.yml": "800d383be1413db8e33786c71252b1cf96e1aa35d66b62fa566c97a92ba214ed",
    "build-delta-ops-daily-summary.yml": "332b842668e6fbe18be7e0cf583502d73d9482ec1475653018b2c1095f7794b3",
    "build-gfs-wind-latest.yml": "676b3991f739a59a565b75ee2f8c00ba40c15da56efbd6d1075af7ae15ad82f8",
    "build-hrrr-wind-latest.yml": "25f9f12fd076e51373cc2d7245399a532576be487b9b710e2954fee9158b8490",
    "build-hrrr-wind-sandbox.yml": "bf8bed904f7282691c1bcaa0a759d2ee46a3d1d8b323c83e511447e30ba01f8f",
    "build-major-water-supply-basin-forecasts.yml": "d066f588b02d083fbee79d78b4f6ce0c71e5003bdcffec09e47cba2b459a917b",
    "build-nbm-qpf.yml": "a97bdae76bcbcd8db9c490297a61a68fc9b826851692dc00b1648019088d3a9b",
    "build-nbm-wind-guidance-latest.yml": "d99f26ec2e8168ab5e81e0e0b6fb01d49364a071eca0bf8b8c8a5cd1299d69d5",
    "build-scan-soil-moisture-latest.yml": "591467444eb7e36a7c34416e8bf6c424d8ecdf5db5d9191a59fdd35c45fd2eae",
    "build-snow-pillow-latest.yml": "3c7ba9e42d4f03c3c1fb44a7ef0b99aa50c806b295167326329d7dc3fcd79ad1",
    "build-usgs-groundwater-latest-ca.yml": "04a898e07a641f4f46f6d32aba4a3ffbe8c6d7e4ab1b34f73a007e6d5876c6bc",
    "build-usgs-streamflow-latest-ca.yml": "4907f48726b518b9c035fd81361d8323c50b996d30d1e3997f520fd687bb7500",
    "build-winter-storm-levels.yml": "a28156a7163044320c230190d291ec79a80b279dcf71d610f1546131f559682d",
    "preview-usgs-groundwater-candidates.yml": "5d455dc01e3fe4cf05937ca75384729b6ac2bf334543ff581ea2d3e624ed567e",
}

JOB_RE = re.compile(r"^  ([A-Za-z0-9_-]+):$")
STEP_RE = re.compile(r"^      - ")
DIRECT_SETUP_RE = re.compile(r"^r-lib/actions/setup-r@")
RELEASE_ALIAS_RE = re.compile(
    r"(?m)^\s*r-version:\s*(?:release|'release'|\"release\")\s*$"
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


@dataclass(frozen=True)
class Step:
    name: str
    uses: str | None
    condition: str | None
    lines: tuple[str, ...]

    @property
    def invokes_apt_helper(self) -> bool:
        return any(APT_HELPER_COMMAND in line for line in self.lines)

    @property
    def is_r_setup(self) -> bool:
        return bool(
            self.uses == HARDENED_ACTION
            or (self.uses and DIRECT_SETUP_RE.match(self.uses))
        )


@dataclass(frozen=True)
class Job:
    name: str
    runs_on: str | None
    steps: tuple[Step, ...]


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
            steps.append(
                Step(
                    name=step_scalar(step_lines, "name") or "(unnamed)",
                    uses=step_scalar(step_lines, "uses"),
                    condition=step_scalar(step_lines, "if"),
                    lines=tuple(step_lines),
                )
            )
        jobs[job_name] = Job(
            name=job_name,
            runs_on=scalar(job_lines, "    runs-on:"),
            steps=tuple(steps),
        )
    return jobs


def normalized_workflow_text(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    output: list[str] = []
    setup_count = 0
    index = 0
    while index < len(lines):
        if lines[index].startswith("      - "):
            end = index + 1
            while (
                end < len(lines)
                and not lines[end].startswith("      - ")
                and not (
                    lines[end].strip()
                    and len(lines[end]) - len(lines[end].lstrip()) <= 4
                )
            ):
                end += 1
            block = lines[index:end]
            if any(
                "uses: r-lib/actions/setup-r@" in line
                or f"uses: {HARDENED_ACTION}" in line
                for line in block
            ):
                setup_count += 1
                for line in block:
                    if line.startswith("        uses: "):
                        output.append("        uses: __BRIM_HARDENED_R_SETUP__\n")
                        break
                    output.append(line)
                output.append("\n")
                index = end
                continue
        output.append(lines[index])
        index += 1

    if setup_count != 1:
        raise AssertionError(f"{path.name} has {setup_count} setup-R steps")
    return "".join(output)


class RSetupHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.paths = sorted(WORKFLOWS.glob("*.yml"))
        cls.jobs = {path.name: parse_jobs(path) for path in cls.paths}

    def test_setup_r_job_inventory_is_complete_and_classified(self) -> None:
        self.assertEqual(
            {path.name for path in self.paths},
            set(EXPECTED_SETUP_JOBS) | EXPECTED_WORKFLOWS_WITHOUT_R,
        )
        actual = {
            (workflow, job_name)
            for workflow, jobs in self.jobs.items()
            for job_name, job in jobs.items()
            if any(step.is_r_setup for step in job.steps)
        }
        expected = {
            (workflow, job_name)
            for workflow, (job_name, _classification) in EXPECTED_SETUP_JOBS.items()
        }
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 17)
        self.assertEqual(
            sum(1 for _job, classification in EXPECTED_SETUP_JOBS.values() if classification == PRODUCTION),
            15,
        )
        self.assertEqual(
            sum(1 for _job, classification in EXPECTED_SETUP_JOBS.values() if classification == SUPPORT),
            2,
        )

    def test_every_setup_job_uses_the_central_hardened_policy(self) -> None:
        for workflow, (job_name, _classification) in EXPECTED_SETUP_JOBS.items():
            job = self.jobs[workflow][job_name]
            setup_steps = [step for step in job.steps if step.is_r_setup]
            self.assertEqual(len(setup_steps), 1, f"{workflow}:{job_name}")
            self.assertEqual(setup_steps[0].name, "Set up R", workflow)
            self.assertEqual(setup_steps[0].uses, HARDENED_ACTION, workflow)
            self.assertEqual(job.runs_on, "ubuntu-latest", workflow)

        workflow_text = "\n".join(
            path.read_text(encoding="utf-8") for path in self.paths
        )
        self.assertNotRegex(workflow_text, r"uses:\s*r-lib/actions/setup-r@")
        self.assertNotRegex(workflow_text, RELEASE_ALIAS_RE)

    def test_central_policy_pins_proven_runtime_and_bounds_retries(self) -> None:
        text = HARDENED_ACTION_PATH.read_text(encoding="utf-8")
        self.assertEqual(text.count("uses: r-lib/actions/setup-r@v2"), 3)
        self.assertEqual(text.count(f'r-version: "{PINNED_R_VERSION}"'), 3)
        self.assertEqual(text.count('use-public-rspm: "true"'), 3)
        self.assertEqual(text.count("continue-on-error: true"), 2)
        self.assertEqual(text.count("run: sleep 5"), 1)
        self.assertEqual(text.count("run: sleep 15"), 1)
        self.assertIn("id: setup_r_1", text)
        self.assertIn("id: setup_r_2", text)
        self.assertIn("steps.setup_r_1.outcome == 'failure'", text)
        self.assertIn("steps.setup_r_2.outcome == 'failure'", text)
        self.assertNotRegex(text, RELEASE_ALIAS_RE)

    def test_apt_helper_remains_before_setup_r_with_the_same_condition(self) -> None:
        for workflow, (job_name, _classification) in EXPECTED_SETUP_JOBS.items():
            steps = self.jobs[workflow][job_name].steps
            setup_indexes = [index for index, step in enumerate(steps) if step.is_r_setup]
            helper_indexes = [
                index for index, step in enumerate(steps) if step.invokes_apt_helper
            ]
            identity = f"{workflow}:{job_name}"
            self.assertEqual(len(setup_indexes), 1, identity)
            self.assertEqual(len(helper_indexes), 1, identity)
            self.assertLess(helper_indexes[0], setup_indexes[0], identity)
            self.assertEqual(
                steps[helper_indexes[0]].condition,
                steps[setup_indexes[0]].condition,
                identity,
            )

    def test_normalized_workflows_match_reviewed_control_baseline(self) -> None:
        self.assertEqual(
            set(EXPECTED_NORMALIZED_WORKFLOW_SHA256), set(EXPECTED_SETUP_JOBS)
        )
        for workflow, expected_digest in EXPECTED_NORMALIZED_WORKFLOW_SHA256.items():
            normalized = normalized_workflow_text(WORKFLOWS / workflow)
            actual_digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            self.assertEqual(actual_digest, expected_digest, workflow)


if __name__ == "__main__":
    unittest.main()
