"""Single historical SCAN initial-admission proof on the isolated QA ref."""
import copy
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
    "actions/workflows/287560809/runs?created=2026-11-19..2026-11-27&per_page=100": "RECOVERY_HISTORY",
    f"actions/runs/{RUN}/jobs?filter=all&per_page=100": "JOBS_164",
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
    del value["source_pr"], value["reviewed_head_sha"]
    value["source_sha"] = EVENT
    assert digest(canonical(value).encode()) == "1bdab7a13dbe67dcefeca51f3a040ebbc7fdd18ba53e4d0658f3099f3581a6a8"
    return value


PREVIOUS_QA = "6fc68bcafc931f13ab7a611ad10ff00b220c2e55"
SIM_MANUAL = 999999990166
SIM_CRON = 999999990165


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def archived_pilot(guard, root, context):
    number, source, signature, old_guard = context.split(",")
    pins = guard.code_pins(root)
    pins["scripts/scan_operational_guard.py"] = old_guard
    value = dict(approved=True, schema="scan-c13r-finite-1", kind="pilot", code_sha256=pins,
                 budgets=guard.BUDGETS, acquire_only=False, dates=[d.isoformat() for d in guard.PILOT_DATES],
                 expires_utc="2026-11-20T08:00:00Z", manual_run_id=int(number),
                 manual_source_sha=source, runtime_signature=signature)
    raw = json.dumps(value, separators=(",", ":"))
    assert digest(raw.encode()) == "465d45e2eaf854fa32731faee89454592717c4693614b2e4bc4cfa6240aaff86"
    return raw


class RecoveryClock(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 27, 2, 36, tzinfo=datetime.timezone.utc).astimezone(tz)


def check_request(event, args):
    if event == "urllib.Request":
        url, body, headers, method = args
        prefix = "https://api.github.com/repos/" + REPO + "/"
        assert url.startswith(prefix) and url[len(prefix):] in ROUTES
        assert method == "GET" and body is None and "authorization" in {k.lower() for k in headers}


def prove(report, recovery):
    workspace = Path(os.environ["GITHUB_WORKSPACE"])
    checked, historical, candidate = (workspace / name for name in ("reviewed", "historical", "candidate"))
    inputs = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())["inputs"]
    assert os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch" and os.environ["GITHUB_REPOSITORY"] == REPO
    assert os.environ["GITHUB_REF"] == "refs/heads/codex/scan-run164-hosted-admission-proof"
    assert os.environ["GITHUB_RUN_ATTEMPT"] == "1" and os.environ["GITHUB_RUN_NUMBER"] == "5"
    assert revision(checked, "HEAD") == inputs["expected_source_sha"] == os.environ["GITHUB_SHA"]
    assert revision(checked, "HEAD^") == PREVIOUS_QA
    assert revision(historical, "HEAD") == EVENT and revision(candidate, "HEAD") == REVIEW
    path = "scripts/scan_operational_guard.py"
    assert revision(checked, "HEAD:" + path) == revision(candidate, "HEAD:" + path)
    assert digest((candidate / path).read_bytes()) == GUARD_SHA256
    spec = importlib.util.spec_from_file_location("reviewed_scan_guard", candidate / path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    report["stage"] = "FIXTURE"
    policy = fixture(guard, historical, inputs["historical_window"])
    daily = archived_pilot(guard, candidate, inputs["archived_pilot_context"])
    root, run_id, clock_class = historical, RUN, FrozenClock
    real_read = guard.github
    synthetic = {}

    def authenticated(route):
        stage = ROUTES[route]
        assert stage not in report["started"]
        report["started"].append(stage)
        report["stage"] = stage
        data = real_read(route)
        report["authenticated"][stage] = digest(canonical(data).encode())
        return data

    def metadata(route):
        if recovery and route == f"actions/runs/{SIM_MANUAL}":
            anchor = authenticated(f"actions/runs/{RUN}")
            assert anchor["status"] == "completed" and anchor["conclusion"] == "failure"
            assert anchor["id"] == RUN and anchor["run_attempt"] == 1 and anchor["head_sha"] == EVENT
            synthetic["active"] = dict(anchor, id=SIM_MANUAL, run_number=166, head_sha=REVIEW,
                                       created_at="2026-11-27T02:35:00Z", status="in_progress", conclusion=None)
            synthetic["cron"] = dict(anchor, id=SIM_CRON, run_number=165, event="schedule",
                                     created_at="2026-11-26T14:35:00Z")
            return synthetic["active"]
        if recovery and route == f"actions/runs/{SIM_CRON}/jobs?filter=all&per_page=100":
            jobs = copy.deepcopy(synthetic["job_template"])
            for job in jobs["jobs"]:
                job.update(id=job["id"] + 300000000, run_id=SIM_CRON)
            report["simulated_cron_jobs"] = True
            return jobs
        data = authenticated(route)
        stage = ROUTES[route]
        if recovery and stage == "RECOVERY_HISTORY":
            assert data["total_count"] == len(data["workflow_runs"])
            assert max(row["run_number"] for row in data["workflow_runs"]) == 164
            data["workflow_runs"] += [synthetic["cron"], synthetic["active"]]
            data["total_count"] += 2
        elif recovery and stage == "JOBS_164":
            synthetic["job_template"] = copy.deepcopy(data)
        elif not recovery and stage == "RUN_DETAIL":
            restore_active_state(data)
        elif not recovery and stage == "RUN_HISTORY":
            matches = [row for row in data["workflow_runs"] if row["id"] == RUN]
            assert len(matches) == 1
            restore_active_state(matches[0])
        return data

    if recovery:
        root, run_id, clock_class = candidate, SIM_MANUAL, RecoveryClock
        policy.update(code_sha256=guard.code_pins(candidate), source_sha=REVIEW, predecessor_run_id=RUN,
                      run_number=166, source_cutoff_date="2026-11-25", not_before_utc="2026-11-27T02:30:00Z",
                      not_after_utc="2026-11-27T05:45:00Z", release_not_after_utc="2026-11-27T06:20:00Z",
                      manual_recovery=dict(run_number=164, source_sha=EVENT, out_of_campaign_run_ids=[SIM_CRON],
                                           daily_policy_sha256=digest(daily.encode())))
    os.chdir(root)
    clock = SimpleNamespace(**vars(datetime))
    clock.datetime = clock_class
    raw_policy = canonical(policy)
    with tempfile.TemporaryDirectory(dir=os.environ["RUNNER_TEMP"]) as temp:
        env = dict(GITHUB_REF="refs/heads/main", GITHUB_RUN_ID=str(run_id), GITHUB_RUN_NUMBER=str(policy["run_number"]),
                   GITHUB_WORKFLOW_REF=REPO + "/" + WORKFLOW + "@refs/heads/main", GITHUB_SHA=policy["source_sha"],
                   SCAN_SOURCE_CUTOFF_DATE=policy["source_cutoff_date"], SCAN_ACQUIRE_ONLY="false", SCAN_PROVIDER_HOLD="false",
                   SCAN_LIVE_APPROVAL_JSON=raw_policy, SCAN_DAILY_PILOT_JSON=daily, RUNNER_TEMP=temp,
                   GITHUB_ENV=temp + "/env", GITHUB_OUTPUT=temp + "/output")
        report["stage"] = "INITIAL"
        with patch.dict(os.environ, env), patch.object(guard, "dt", clock), patch.object(
                guard, "github", metadata), patch.object(sys, "argv", ["guard", "initial"]):
            try:
                guard.main()
            except SystemExit:
                pass
        control = Path(temp) / "scan-control"
        assert {p.name for p in control.iterdir()} == {"approval-initial.json"}
        raw = (control / "approval-initial.json").read_bytes()
        receipt = json.loads(raw)
        assert receipt["provider_invoked_by_guard"] is False and receipt["run_id"] == str(run_id)
        assert receipt["source_sha"] == policy["source_sha"] and receipt["run_attempt"] == "1"
        status = receipt["status"]
        assert re.fullmatch("[A-Z][A-Z0-9_]{0,95}", status)
        if status == "ADMITTED":
            required = ["RUN_DETAIL", "RECOVERY_HISTORY", "JOBS_164"] if recovery else [
                "RUN_DETAIL", "RUN_HISTORY", "JOBS_162", "JOBS_163"]
            assert report["started"] == list(report["authenticated"]) == required
            assert receipt["source_cutoff_date"] == policy["source_cutoff_date"]
            assert receipt["policy_sha256"] == digest(raw_policy.encode())
            assert len(policy["code_sha256"]) == 22 and receipt["budgets"] == guard.BUDGETS
            assert not recovery or report["simulated_cron_jobs"]
        report.update(stage="INITIAL_ADMISSION", status=status, reservation=False, receipt_sha256=digest(raw),
                      policy_sha256=digest(raw_policy.encode()), source_sha=policy["source_sha"])
        return status == "ADMITTED"


if __name__ == "__main__":
    os.umask(0o077)
    sys.addaudithook(check_request)
    for case in ("historical", "simulated_recovery"):
        report = dict(case=case, stage="QA_SOURCE", status="PROOF_SETUP_FAILURE", started=[], authenticated={})
        try:
            passed = prove(report, case == "simulated_recovery")
        except Exception:
            passed = False
        print("SCAN_PROOF_RESULT " + json.dumps(report, sort_keys=True), flush=True)
        if not passed:
            sys.exit(1)
