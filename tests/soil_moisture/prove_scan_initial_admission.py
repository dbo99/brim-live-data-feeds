"""Single historical SCAN initial-admission proof on the isolated QA ref."""
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

REPO = "dbo99/brim-live-data-feeds"
REVIEW = "881b5035cc4d1d810c14822f838018437d19952d"
EVENT = "4d8d1c4b34d48495e4cc068bebe9c3b80658683a"
REVIEWED_HEAD = "c0bef58b65bf2f47e5a950a6190d2dd028be3317"
GUARD_SHA256 = "bc2a5fb25b11dc37b6695e14bf4a1e49e095e16a611a0435f46e95787dada554"
FIXTURE_SHA256 = "5281b8a30c5b1e423381b601ee69e11a7a8a1603c570f4d933ad6a499f4439e0"
RUN = 45755436422
WORKFLOW = ".github/workflows/build-scan-soil-moisture-latest.yml"
ROUTES = {
    f"actions/runs/{RUN}": "RUN_DETAIL",
    "actions/workflows/287560809/runs?created=2026-11-24..2026-11-26&per_page=100": "RUN_HISTORY",
    "pulls/68": "REVIEWED_PR",
    "actions/runs/45510985646/jobs?filter=all&per_page=100": "JOBS_162",
    "actions/runs/45719264713/jobs?filter=all&per_page=100": "JOBS_163",
}


class FrozenClock(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 26, 4, 31, 37, tzinfo=datetime.timezone.utc).astimezone(tz)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def revision(root, name):
    return subprocess.check_output(["git", "-C", root, "rev-parse", "--verify", name], text=True).strip()


def restore_active_state(row):
    assert (row["id"], row["run_number"], row["run_attempt"], row["head_sha"], row["head_branch"],
            row["event"], row["status"], row["conclusion"]) == (
                RUN, 164, 1, EVENT, "main", "workflow_dispatch", "completed", "failure")
    row.update(status="in_progress", conclusion=None)


def fixture(guard, root, clocks):
    # The historical bytes are canonical JSON. The independent digest binds
    # every value, including the 22 pins read from the historical checkout.
    start, end, release = clocks.split(",")
    value = dict(schema=guard.RENEWABLE, approved=True, kind="manual", acquire_only=False,
                 code_sha256=guard.code_pins(root), budgets=guard.BUDGETS, source_pr=68,
                 reviewed_head_sha=REVIEWED_HEAD, run_number=164, predecessor_run_id=45510985646,
                 source_cutoff_date="2026-11-24", not_before_utc=start, not_after_utc=end,
                 release_not_after_utc=release)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    assert len(raw) == 3004 and digest(raw) == FIXTURE_SHA256
    return raw.decode()


def prove(report):
    workspace = Path(os.environ["GITHUB_WORKSPACE"])
    checked, historical = workspace / "reviewed", workspace / "historical"
    inputs = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())["inputs"]
    assert os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch" and os.environ["GITHUB_REPOSITORY"] == REPO
    assert os.environ["GITHUB_REF"] == "refs/heads/codex/scan-run164-hosted-admission-proof"
    assert os.environ["GITHUB_RUN_ATTEMPT"] == "1" and os.environ["GITHUB_RUN_NUMBER"] == "4"
    assert revision(checked, "HEAD") == inputs["expected_source_sha"] == os.environ["GITHUB_SHA"]
    assert revision(checked, "HEAD^") == REVIEW and revision(historical, "HEAD") == EVENT
    path = "scripts/scan_operational_guard.py"
    assert revision(checked, "HEAD:" + path) == revision(checked, REVIEW + ":" + path)
    assert digest((checked / path).read_bytes()) == GUARD_SHA256
    spec = importlib.util.spec_from_file_location("reviewed_scan_guard", checked / path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    report["stage"] = "HISTORICAL_FIXTURE"
    policy = fixture(guard, historical, inputs["historical_window"])
    read_metadata = guard.github

    def historical_read(route):
        if route not in ROUTES or ROUTES[route] in report["started"]:
            raise guard.Refused("UNEXPECTED_PROOF_ROUTE")
        stage = ROUTES[route]
        report["started"].append(stage)
        report["stage"] = stage
        data = read_metadata(route)
        if stage == "RUN_DETAIL":
            restore_active_state(data)
        elif stage == "RUN_HISTORY":
            matches = [row for row in data["workflow_runs"] if row["id"] == RUN]
            assert len(matches) == 1
            restore_active_state(matches[0])
        report["read"].append(stage)
        return data

    def audit(event, args):
        if event == "urllib.Request":
            url, body, headers, method = args
            prefix = "https://api.github.com/repos/" + REPO + "/"
            if not url.startswith(prefix) or url[len(prefix):] not in ROUTES or method != "GET" or body is not None:
                raise guard.Refused("UNEXPECTED_PROOF_REQUEST")
            expected_auth = not url.endswith("/pulls/68")
            if ("authorization" in {name.lower() for name in headers}) != expected_auth:
                raise guard.Refused("PROOF_REQUEST_AUTH_MISMATCH")

    os.chdir(historical)
    sys.addaudithook(audit)
    clock = SimpleNamespace(**vars(datetime))
    clock.datetime = FrozenClock
    with tempfile.TemporaryDirectory(dir=os.environ["RUNNER_TEMP"]) as temp:
        env = dict(GITHUB_REF="refs/heads/main", GITHUB_RUN_ID=str(RUN), GITHUB_RUN_NUMBER="164",
                   GITHUB_WORKFLOW_REF=REPO + "/" + WORKFLOW + "@refs/heads/main", GITHUB_SHA=EVENT,
                   SCAN_SOURCE_CUTOFF_DATE="2026-11-24", SCAN_ACQUIRE_ONLY="false", SCAN_PROVIDER_HOLD="false",
                   SCAN_LIVE_APPROVAL_JSON=policy, RUNNER_TEMP=temp, GITHUB_ENV=temp + "/env",
                   GITHUB_OUTPUT=temp + "/output")
        report["stage"] = "INITIAL"
        with patch.dict(os.environ, env), patch.object(guard, "dt", clock), patch.object(
                guard, "github", historical_read), patch.object(sys, "argv", ["guard", "initial"]):
            try:
                guard.main()
            except SystemExit:
                pass
        control = Path(temp) / "scan-control"
        assert {p.name for p in control.iterdir()} == {"approval-initial.json"}
        raw = (control / "approval-initial.json").read_bytes()
        receipt = json.loads(raw)
        assert receipt["provider_invoked_by_guard"] is False and receipt["run_id"] == str(RUN)
        assert receipt["source_sha"] == EVENT and receipt["run_attempt"] == "1"
        status = receipt["status"]
        assert re.fullmatch("[A-Z][A-Z0-9_]{0,95}", status)
        if status == "ADMITTED":
            assert report["read"] == list(ROUTES.values())
            assert receipt["policy_sha256"] == FIXTURE_SHA256
        report.update(stage="INITIAL_ADMISSION", status=status, reservation=False, receipt_sha256=digest(raw))
        return status == "ADMITTED"


if __name__ == "__main__":
    os.umask(0o077)
    report = dict(stage="QA_SOURCE", status="PROOF_SETUP_FAILURE", started=[], read=[])
    try:
        passed = prove(report)
    except Exception:
        passed = False
    print("SCAN_PROOF_RESULT " + json.dumps(report, sort_keys=True), flush=True)
    sys.exit(0 if passed else 1)
