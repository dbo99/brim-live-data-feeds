#!/usr/bin/env python3
"""Finite SCAN manual and daily admission; no provider client."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import urllib.request
from zoneinfo import ZoneInfo

REPOSITORY = "dbo99/brim-live-data-feeds"
REPOSITORY_ID = 1247282299
WORKFLOW_ID = 287560809
WORKFLOW = ".github/workflows/build-scan-soil-moisture-latest.yml"
PILOT_DATES = tuple(dt.date(2026, 11, d) for d in range(13, 20))
EXPIRY = dt.datetime(2026, 11, 20, 8, tzinfo=dt.timezone.utc)
MANUAL_EXPIRY = dt.datetime(2026, 11, 12, 7, 59, 59, tzinfo=dt.timezone.utc)
# Reserve the existing transport's single possible EOF-probe byte INSIDE each
# approved decoded-body ceiling. No retries: one GET and one POST per station.
BUDGETS = dict(SCAN_HTTP_MAX_REQUESTS=87, SCAN_HTTP_RESPONSE_BYTES=2097151,
               SCAN_HTTP_TOTAL_BYTES=182452223, SCAN_HTTP_CAMPAIGN_SECONDS=1200,
               SCAN_FETCH_TIMEOUT_SEC=20)
CODE_PATHS = (
    WORKFLOW, "scripts/scan_operational_guard.py", "scripts/run_scan_operational.R",
    "scripts/run_scan_operational.py",
    "scripts/build_scan_soil_moisture_latest.R", "scripts/soil_moisture/scan_metered_http.R",
    "scripts/soil_moisture/scan_nwcc_compat.R", "scripts/main_publisher.py",
    "scripts/scan_soil_moisture_publisher.py", "scripts/configure_apt_mirror_order.sh",
    ".github/actions/setup-r-hardened/action.yml",
    "tests/soil_moisture/hosted_runtime_lock.json",
    "tests/soil_moisture/run_sms_hosted_qualification.py",
    "tests/soil_moisture/run_scan_operational_qualification.py",
    "tests/soil_moisture/run_scan_operational_qualification.sh",
    "tests/soil_moisture/test_scan_native_timeout.R",
    "tests/soil_moisture/test_scan_metered_http.R",
    "tests/soil_moisture/test_scan_nwcc_compat.R",
    "tests/soil_moisture/scan_http_fixture_server.py",
    "data/input/scan_station_index.csv", "data/input/scan_depth_style.csv",
    "data/input/scan_sms_waterday_percentiles.csv",
)
ROSTER_SHA = "25aff0d1468f20c80b4a7b89b5ff3c733a1e57c61d6086385d296a04fc0e1e5d"

class Refused(Exception):
    pass

def require(ok, reason):
    if not ok:
        raise Refused(reason)

def stamp(value):
    require(isinstance(value, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value)), "INVALID_CLOCK")
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))

def code_pins(root):
    pins = {}
    for name in CODE_PATHS:
        p = root / name
        require(not any(x.is_symlink() for x in [p, *p.parents]), "LINKED_CODE")
        s = p.stat()
        require(stat.S_ISREG(s.st_mode) and s.st_size <= 8 * 1024**2, "INVALID_CODE_FILE")
        pins[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    require(pins["data/input/scan_station_index.csv"] == ROSTER_SHA, "ROSTER_CHANGED")
    return pins

def runtime_signature(value):
    require(value.get("R") == "4.6.1", "RUNTIME_R_CHANGED")
    selected = value["selected"]
    for name, version in {"soilDB":"2.9.2", "httr":"1.4.9", "curl":"8.1.0"}.items():
        require(selected[name]["version"] == version, "RUNTIME_PACKAGE_CHANGED")
    stable = {"R":value["R"], "selected":selected,
              "reused_platform_packages":value["reused_platform_packages"]}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

RENEWABLE = "scan-finite-2"
PACIFIC = ZoneInfo("America/Los_Angeles")
DAY = dt.timedelta(days=1)
LEGACY_HISTORY = f"actions/workflows/{WORKFLOW_ID}/runs?created=2026-11-11..2026-11-20&per_page=100"


def date_value(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value), "INVALID_DATE")
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise Refused("INVALID_DATE") from None


def operating_day(run):
    return stamp(run["created_at"]).astimezone(PACIFIC).date()


def scheduled_window(day, minutes):
    start = dt.datetime.combine(day, dt.time(14, 30), dt.timezone.utc)
    end = start + dt.timedelta(minutes=minutes)
    return start, end, end + dt.timedelta(minutes=15)


def supported_cutoff(day):
    cutoff = day-DAY
    wy_start = dt.date(day.year if day.month >= 10 else day.year-1, 10, 1)
    require(cutoff >= wy_start, "CUTOFF_PRECEDES_CURRENT_WY")
    # soilDB queries station/calendar-year. Both unchanged accounting checks
    # accept exactly one endpoint GET and one POST for each of the 28 stations.
    require(cutoff.year == wy_start.year, "UNQUALIFIED_TWO_YEAR_ACCOUNT")
    return cutoff


def calendar(policy, event):
    """Derive a small GitHub UTC date range from the actual approved slots."""
    require(policy.get("approved") is True and policy.get("acquire_only") is False, "NO_FINITE_POLICY")
    if event == "workflow_dispatch":
        require(policy.get("kind") == "manual", "WRONG_EVENT")
        start, end, release = (stamp(policy[k]) for k in ("not_before_utc", "not_after_utc", "release_not_after_utc"))
        require(start < end <= release <= start + dt.timedelta(hours=4), "INVALID_MANUAL_WINDOW")
        require(type(policy.get("predecessor_run_id")) is int and policy["predecessor_run_id"] > 0, "INVALID_PREDECESSOR")
        day = start.astimezone(PACIFIC).date()
        require((release-dt.timedelta(microseconds=1)).astimezone(PACIFIC).date() == day and
                date_value(policy["source_cutoff_date"]) == supported_cutoff(day), "WRONG_CUTOFF")
        return [day], start, release
    require(event == "schedule" and policy.get("kind") == "daily", "WRONG_EVENT")
    values = policy.get("dates")
    require(type(values) is list and 1 <= len(values) <= 7, "UNBOUNDED_DAILY_SLOTS")
    days = [date_value(value) for value in values]
    require(all(b == a+DAY for a,b in zip(days, days[1:])), "NONCONSECUTIVE_SLOTS")
    for day in days:
        supported_cutoff(day)
    minutes = policy.get("window_minutes")
    require(type(minutes) is int and 150 <= minutes <= 840, "INVALID_DAILY_WINDOW")
    start = scheduled_window(days[0], minutes)[0]
    release = scheduled_window(days[-1], minutes)[2]
    require(stamp(policy["expires_utc"]) == release, "WRONG_DAILY_EXPIRY")
    for key in ("manual_run_id", "predecessor_run_id"):
        require(type(policy.get(key)) is int and policy[key] > 0, "INVALID_PREDECESSOR")
    require(isinstance(policy.get("manual_source_sha"), str) and re.fullmatch(r"[0-9a-f]{40}",policy["manual_source_sha"]),
            "WRONG_MANUAL_SOURCE")
    return days, start, release


def history_path(days):
    # Include the previous Pacific operating day and the following UTC date.
    return (f"actions/workflows/{WORKFLOW_ID}/runs?created="
            f"{days[0]-DAY}..{days[-1]+DAY}&per_page=100")


def complete_history(response, key):
    rows = response[key]
    require(type(rows) is list and type(response["total_count"]) is int and
            response["total_count"] == len(rows) <= 100 and
            len({row["id"] for row in rows}) == len(rows), "INCOMPLETE_RUN_HISTORY")
    return rows


def initial_refusal(root, run):
    """One out-of-campaign cron may have failed before reservation on the manual day."""
    require(run["workflow_id"] == WORKFLOW_ID and run["repository"]["id"] == REPOSITORY_ID and
            run["event"] == "schedule" and run["head_branch"] == "main" and
            run["run_attempt"] == 1 and run["status"] == "completed" and run["conclusion"] == "failure",
            "PRIOR_SPEND_OR_UNKNOWN")
    source = run["head_sha"]
    require(isinstance(source,str) and re.fullmatch(r"[0-9a-f]{40}",source), "WRONG_HISTORY_SOURCE")
    def blob(rev, path):
        return subprocess.check_output(["git","rev-parse","--verify",f"{rev}:{path}"],cwd=root,text=True,timeout=5).strip()
    require(blob(source, WORKFLOW) == blob("HEAD", WORKFLOW) and
            blob(source, "scripts/scan_operational_guard.py") in
            {blob("HEAD", "scripts/scan_operational_guard.py"), "6be892b42888ee191d19c57c05097c927765df1d"},
            "UNKNOWN_PRIOR_ADMISSION_CODE")
    jobs = complete_history(github(f"actions/runs/{run['id']}/jobs?filter=all&per_page=100"), "jobs")
    require(all(j["run_id"] == run["id"] and j["run_attempt"] == 1 and
                j["head_sha"] == source and j["head_branch"] == "main" and j["status"] == "completed"
                for j in jobs), "WRONG_PRIOR_JOB")
    by_name = {j["name"]: j for j in jobs}
    require(len(jobs) == 3 and set(by_name) == {
        "prepare-candidate", "refuse-private-acquisition", "publish-to-main"
    }, "PRIOR_SPEND_OR_UNKNOWN")
    for name in ("refuse-private-acquisition", "publish-to-main"):
        require(by_name[name]["conclusion"] == "skipped" and by_name[name]["steps"] == [],
                "PRIOR_SPEND_OR_UNKNOWN")

    prepare = by_name["prepare-candidate"]
    steps = prepare["steps"]
    require(prepare["conclusion"] == "failure" and
            all(s["status"] == "completed" and type(s["number"]) is int and s["number"] > 0
                for s in steps), "PRIOR_SPEND_OR_UNKNOWN")
    numbers = [s["number"] for s in steps]
    require(numbers == sorted(set(numbers)), "PRIOR_SPEND_OR_UNKNOWN")
    results = [(s["name"], s["conclusion"]) for s in steps]
    # Only these two early exits are proved unused. Check the whole ordered
    # job, including the always-run finalizer and receipt retention.
    require(results[:1] == [("Set up job", "success")] and results[1:3] in (
        [("Check out source event", "success"), ("Require finite manual or scheduled admission", "failure")],
        [("Check out source event", "failure"), ("Require finite manual or scheduled admission", "skipped")],
    ), "PRIOR_SPEND_OR_UNKNOWN")
    skipped = (
        "Prefer Canonical Ubuntu APT mirror with Azure fallback",
        "Set up R",
        "Provision the supported bounded SCAN runtime",
        "Qualify actual Linux SCAN runtime without external egress",
        "Prepare isolated SCAN candidate directory",
        "Recheck admission and reserve this campaign once",
        "Build SCAN soil moisture latest feed",
        "Validate SCAN candidate",
        "Describe SCAN candidate artifact",
    )
    require(results[3:] == [(name, "skipped") for name in skipped] + [
        ("Record fixed-label SCAN outcome", "failure"),
        ("Upload validated SCAN publication candidate", "skipped"),
        ("Retain bounded SCAN control and accounting receipts", "success"),
        ("Post Check out source event", "success"),
        ("Complete job", "success"),
    ], "PRIOR_SPEND_OR_UNKNOWN")


def reviewed_merge(root, policy, head):
    """Resolve an owner-pinned PR only to its exact, already merged main commit."""
    number = policy["source_pr"]
    candidate = policy["reviewed_head_sha"]
    require(type(number) is int and number > 0 and isinstance(candidate, str) and
            re.fullmatch(r"[0-9a-f]{40}", candidate), "INVALID_MERGE_BINDING")
    pr = github(f"pulls/{number}")
    require(pr["number"] == number and pr["merged"] is True and pr["merge_commit_sha"] == head and
            pr["head"]["sha"] == candidate and pr["head"]["repo"]["id"] == REPOSITORY_ID and
            pr["base"]["ref"] == "main" and pr["base"]["repo"]["id"] == REPOSITORY_ID, "WRONG_MERGED_SOURCE")
    parents = subprocess.check_output(["git","rev-list","--parents","-1","HEAD"],cwd=root,text=True,timeout=5).split()
    require(len(parents) == 3 and parents[0] == head and parents[2] == candidate, "WRONG_MERGE_PARENTS")
    return head


def check_daily_sources(root, head, policy, manual, history):
    """Compare the commissioned code and each credited success to the current closure."""
    require(manual.get("head_sha") == policy["manual_source_sha"], "WRONG_MANUAL_SOURCE")
    sources = {policy["manual_source_sha"]}
    for run in history:
        if run["event"] == "schedule" and run["conclusion"] == "success" and (
                run["id"] == policy["predecessor_run_id"] or operating_day(run).isoformat() in policy["dates"]):
            sources.add(run["head_sha"])
    for source in sources:
        require(isinstance(source, str) and re.fullmatch(r"[0-9a-f]{40}", source) and
                subprocess.run(["git","diff","--quiet",source,head,"--",*CODE_PATHS],
                               cwd=root,timeout=10).returncode == 0, "COMMISSIONED_EXECUTABLE_CHANGED")


def run_binding(run):
    # A detail response can contain extra metadata absent from a list response.
    return tuple(run[k] for k in ("id","run_number","run_attempt","workflow_id","event","head_sha",
                                  "head_branch","created_at","status","conclusion")) + (run["repository"]["id"],)


def renewable_slot(policy, env, now, current, history, *, root, manual, predecessor):
    event = current["event"]
    days, start, release = calendar(policy, event)
    require(start <= now < release and current.get("status") == "in_progress" and
            current.get("conclusion") is None and stamp(current["created_at"]) <= now, "INACTIVE_WINDOW_OR_RUN")
    day = operating_day(current)
    require(day == now.astimezone(PACIFIC).date() and day in days, "INACTIVE_OUTSIDE_APPROVED_DATE")
    cutoff = day-DAY
    require(all(days[0]-DAY <= stamp(r["created_at"]).date() <= days[-1]+DAY for r in history),
            "WRONG_HISTORY_RANGE")
    by_id = {r["id"]:r for r in history}
    require(len(by_id) == len(history) and run_binding(by_id[current["id"]]) == run_binding(current), "INCONSISTENT_RUN_HISTORY")
    anchor = by_id.get(policy["predecessor_run_id"], predecessor)
    require(anchor and anchor["id"] == policy["predecessor_run_id"] and anchor["head_branch"] == "main" and
            anchor["workflow_id"] == WORKFLOW_ID and anchor["repository"]["id"] == REPOSITORY_ID and
            anchor["status"] == "completed" and anchor["run_attempt"] == 1, "PREDECESSOR_UNPROVED")
    following = [r for r in history if r["run_number"] > anchor["run_number"]]
    numbers = sorted(r["run_number"] for r in following)
    require(len(numbers) == current["run_number"]-anchor["run_number"] and
            all(n == anchor["run_number"]+i for i,n in enumerate(numbers,1)), "INCOMPLETE_SUCCESSION")
    if event == "workflow_dispatch":
        end = stamp(policy["not_after_utc"])
        source = reviewed_merge(root, policy, env["GITHUB_SHA"]) if "source_pr" in policy else policy["source_sha"]
        require(start <= stamp(current["created_at"]) <= now < end and source == env["GITHUB_SHA"],
                "WRONG_MANUAL_SOURCE_OR_WINDOW")
        require(type(policy["run_number"]) is int and policy["run_number"] == current["run_number"], "WRONG_MANUAL_RUN")
        require(policy["source_cutoff_date"] == env.get("SCAN_SOURCE_CUTOFF_DATE") == cutoff.isoformat(), "WRONG_CUTOFF")
        require(not any(r["id"] != current["id"] and stamp(r["created_at"]) >= start for r in history), "COMPETING_CAMPAIGN")
        require(operating_day(anchor) < day and all(operating_day(r) == day for r in following),
                "WRONG_MANUAL_PREDECESSOR")
        if anchor["conclusion"] != "success":
            initial_refusal(root, anchor)
        outside = [r for r in following if r["id"] != current["id"]]
    else:
        start, end, release = scheduled_window(day, policy["window_minutes"])
        require(start <= stamp(current["created_at"]) <= now < end, "DAILY_WINDOW_CLOSED")
        require(manual and manual["id"] == policy["manual_run_id"] and manual["event"] == "workflow_dispatch" and
                manual["head_sha"] == policy["manual_source_sha"] and manual["repository"]["id"] == REPOSITORY_ID and
                manual["workflow_id"] == WORKFLOW_ID and manual["head_branch"] == "main" and manual["run_attempt"] == 1 and
                manual["status"] == "completed" and manual["conclusion"] == "success", "MANUAL_HEALTH_UNPROVED")
        require(isinstance(policy["runtime_signature"], str) and re.fullmatch(r"[0-9a-f]{64}",policy["runtime_signature"]),
                "RUNTIME_UNBOUND")
        manual_anchor = anchor["id"] == manual["id"]
        require(operating_day(anchor) == days[0]-DAY and anchor["conclusion"] == "success" and
                (run_binding(anchor) == run_binding(manual) if manual_anchor else
                 anchor["event"] == "schedule" and anchor["run_number"] > manual["run_number"]),
                "PREDECESSOR_UNPROVED")
        if not manual_anchor:
            require(sum(operating_day(r) == operating_day(anchor) for r in history) == 1,
                    "DUPLICATE_PREDECESSOR_DATE")
        outside = [r for r in following if operating_day(r) == days[0]-DAY]
        require(not outside or manual_anchor, "PRIOR_CAMPAIGN_FAILED_OR_UNKNOWN")
        slots = [r for r in following if r not in outside]
        require(sorted(operating_day(r) for r in slots) == [d for d in days if d <= day] and
                all(r["event"] == "schedule" and r["head_branch"] == "main" and r["run_attempt"] == 1 for r in slots),
                "MISSING_DUPLICATE_OR_WRONG_SLOT")
        require(all(r["id"] == current["id"] or (r["status"] == "completed" and r["conclusion"] == "success")
                    for r in slots), "PRIOR_CAMPAIGN_FAILED_OR_UNKNOWN")
    require(len(outside) <= 1, "PRIOR_SPEND_OR_UNKNOWN")
    for row in outside:
        initial_refusal(root, row)
    return cutoff.isoformat(), end, release


def admit(policy, env, now, pins, current, history, *, root=None, manual=None, predecessor=None):
    """Inputs are acquired fresh by main; tests exercise this exact decision."""
    require(env.get("GITHUB_ACTIONS") == "true" and env.get("GITHUB_REPOSITORY") == REPOSITORY,
            "WRONG_REPOSITORY")
    require(env.get("GITHUB_REF") == "refs/heads/main" and env.get("GITHUB_REF_TYPE") == "branch", "WRONG_REF")
    require(env.get("GITHUB_RUN_ATTEMPT") == "1" and current.get("run_attempt") == 1, "ATTEMPT_SPENT")
    require(env.get("GITHUB_WORKFLOW_REF") == REPOSITORY + "/" + WORKFLOW + "@refs/heads/main", "WRONG_WORKFLOW")
    require(current.get("workflow_id") == WORKFLOW_ID and current.get("repository", {}).get("id") == REPOSITORY_ID,
            "WRONG_RUN_REPOSITORY")
    require(str(current.get("id")) == env.get("GITHUB_RUN_ID") and
            str(current.get("run_number")) == env.get("GITHUB_RUN_NUMBER"), "WRONG_RUN")
    require(current.get("head_sha") == env.get("GITHUB_SHA") and current.get("head_branch") == "main" and
            current.get("event") == env.get("GITHUB_EVENT_NAME"), "WRONG_SOURCE_EVENT")
    require(policy.get("approved") is True and policy.get("schema") in ("scan-c13r-finite-1", RENEWABLE), "NO_FINITE_POLICY")
    require(policy.get("code_sha256") == pins and set(pins) == set(CODE_PATHS), "CODE_CHANGED")
    require(policy.get("budgets") == BUDGETS and all(type(x) is int for x in policy['budgets'].values()), "WRONG_BUDGETS")
    require(env.get("SCAN_PROVIDER_HOLD") == "false", "PROVIDER_HOLD_OR_UNKNOWN")
    require(type(history) is list and len(history) <= 100, "INCOMPLETE_RUN_HISTORY")
    event = env["GITHUB_EVENT_NAME"]
    require(sum(x.get('id') == current['id'] for x in history) == 1, "CURRENT_RUN_MISSING_OR_DUPLICATE")
    require(all(x.get('workflow_id') == WORKFLOW_ID for x in history), "WRONG_HISTORY_WORKFLOW")
    common = {'approved','schema','kind','code_sha256','budgets','acquire_only'}
    extra = {'source_sha','run_number','not_before_utc','not_after_utc','release_not_after_utc','source_cutoff_date'} if event == 'workflow_dispatch' else {'dates','expires_utc','manual_run_id','manual_source_sha','runtime_signature'}
    renewable = policy["schema"] == RENEWABLE
    if renewable and event == "workflow_dispatch":
        extra.add("predecessor_run_id")
        if "source_pr" in policy:
            extra = (extra - {"source_sha"}) | {"source_pr","reviewed_head_sha"}
    if renewable and event == "schedule":
        extra |= {"predecessor_run_id", "window_minutes"}
    require(set(policy) == common | extra and policy['acquire_only'] is False, "UNDECLARED_POLICY_OR_MODE")
    today = now.astimezone(ZoneInfo("America/Los_Angeles")).date()
    if renewable:
        cutoff, end, release = renewable_slot(policy, env, now, current, history, root=root, manual=manual, predecessor=predecessor)
    elif event == "workflow_dispatch":
        require(policy.get("kind") == "manual" and policy.get("source_sha") == env["GITHUB_SHA"], "WRONG_MANUAL_SOURCE")
        require(type(policy.get("run_number")) is int and policy["run_number"] == current["run_number"], "WRONG_MANUAL_RUN")
        start, end, release = (stamp(policy[k]) for k in ("not_before_utc", "not_after_utc", "release_not_after_utc"))
        require(start <= now < end <= release <= MANUAL_EXPIRY and release-start <= dt.timedelta(hours=4), "MANUAL_WINDOW_CLOSED")
        cutoff = policy.get("source_cutoff_date")
        require(cutoff == (today-dt.timedelta(days=1)).isoformat() == env.get("SCAN_SOURCE_CUTOFF_DATE"), "WRONG_CUTOFF")
        require(not any(x["id"] != current["id"] and stamp(x["created_at"]) >= start for x in history), "COMPETING_CAMPAIGN")
    elif event == "schedule":
        require(policy.get("kind") == "pilot" and policy.get("dates") == [d.isoformat() for d in PILOT_DATES], "NO_PILOT")
        require(policy.get("expires_utc") == "2026-11-20T08:00:00Z" and now < EXPIRY, "INACTIVE_EXPIRED")
        day = stamp(current["created_at"]).date()
        require(day in PILOT_DATES and day == now.date(), "INACTIVE_OUTSIDE_PILOT_DATE")
        start = dt.datetime.combine(day, dt.time(14, 30), dt.timezone.utc)
        end = dt.datetime.combine(day, dt.time(17), dt.timezone.utc)
        release = end + dt.timedelta(minutes=15)
        require(start <= now < end and release < EXPIRY, "PILOT_DAILY_WINDOW_CLOSED")
        manual = [x for x in history if x["id"] == policy.get("manual_run_id")]
        require(len(manual) == 1 and manual[0]["event"] == "workflow_dispatch" and
                manual[0]["run_attempt"] == 1 and manual[0]["status"] == "completed" and
                manual[0]["conclusion"] == "success" and manual[0]["head_sha"] == policy.get("manual_source_sha"), "MANUAL_HEALTH_UNPROVED")
        runs = [x for x in history if stamp(x["created_at"]).date() in PILOT_DATES]
        require(len(runs) <= 7 and all(x["event"] == "schedule" and x["run_attempt"] == 1 for x in runs), "PILOT_DUPLICATE_OR_MANUAL")
        days = [stamp(x["created_at"]).date() for x in runs]
        require(len(set(days)) == len(days) and sum(x["id"] == current["id"] for x in runs) == 1, "PILOT_DATE_SPENT")
        prior = [x for x in runs if x["id"] != current["id"]]
        require(all(x["status"] == "completed" and x["conclusion"] == "success" for x in prior), "PRIOR_CAMPAIGN_FAILED_OR_UNKNOWN")
        require(set(d for d in PILOT_DATES if d < day) == set(stamp(x["created_at"]).date() for x in prior), "PRIOR_SCHEDULE_MISSING")
        require(isinstance(policy.get("runtime_signature"), str) and re.fullmatch(r"[0-9a-f]{64}", policy["runtime_signature"]), "RUNTIME_UNBOUND")
        cutoff = (today-dt.timedelta(days=1)).isoformat()
    else:
        raise Refused("WRONG_EVENT")
    require((renewable or (today.year == 2026 and today.month == 11)) and policy.get("acquire_only", False) is False and
            env.get("SCAN_ACQUIRE_ONLY", "false") == "false", "WRONG_MODE_OR_YEAR")
    require(now + dt.timedelta(seconds=BUDGETS["SCAN_HTTP_CAMPAIGN_SECONDS"]+30) < end and
            now + dt.timedelta(seconds=1200+900+1800) < release, "INSUFFICIENT_REMAINING_WINDOW")
    return {"status":"ADMITTED", "kind":policy["kind"], "source_cutoff_date":cutoff,
            "not_after_utc":end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "release_not_after_utc":release.strftime("%Y-%m-%dT%H:%M:%SZ"), "budgets":BUDGETS}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise Refused("GITHUB_REDIRECT")

def github(path):
    require(valid_history_endpoint(path), "INVALID_HISTORY_ENDPOINT")
    token = os.environ.get("GH_TOKEN", "")
    require(bool(token), "MISSING_JOB_READ_TOKEN")
    headers = {"Accept":"application/vnd.github+json", "X-GitHub-Api-Version":"2022-11-28"}
    # This public PR is readable over GitHub HTTPS with no PR permission on the job token.
    # Run and job history always use the job's authenticated Actions read permission.
    if not path.startswith("pulls/"):
        headers["Authorization"] = "Bearer "+token
    request = urllib.request.Request("https://api.github.com/repos/"+REPOSITORY+"/"+path, headers=headers)
    with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
        body=response.read(4*1024**2+1)
        require(response.status == 200 and len(body) <= 4*1024**2, "HISTORY_RESPONSE_LIMIT")
    return json.loads(body)

def valid_history_endpoint(path):
    if re.fullmatch(r"pulls/[1-9][0-9]*", path):
        return True
    if re.fullmatch(r"actions/runs/[1-9][0-9]*(?:/jobs\?filter=all&per_page=100)?", path) or path == LEGACY_HISTORY:
        return True
    match = re.fullmatch(rf"actions/workflows/{WORKFLOW_ID}/runs\?created=([0-9-]+)\.\.([0-9-]+)&per_page=100", path)
    if not match:
        return False
    try:
        return 0 <= (date_value(match[2])-date_value(match[1])).days <= 8
    except Refused:
        return False


def durable(path, value):
    with path.open("x") as f:
        json.dump(value, f, sort_keys=True);f.write("\n");f.flush();os.fsync(f.fileno())

def main():
    parser=argparse.ArgumentParser();parser.add_argument("phase", choices=["initial", "pre-acquisition"])
    phase=parser.parse_args().phase
    control=Path(os.environ["RUNNER_TEMP"])/"scan-control";control.mkdir(exist_ok=True)
    receipt={"status":"REFUSED", "provider_invoked_by_guard":False, "source_sha":os.environ.get("GITHUB_SHA"),
             "run_id":os.environ.get("GITHUB_RUN_ID"), "run_attempt":os.environ.get("GITHUB_RUN_ATTEMPT")}
    try:
        root=Path.cwd().resolve();now=dt.datetime.now(dt.timezone.utc)
        event=os.environ.get("GITHUB_EVENT_NAME")
        raw=os.environ.get("SCAN_DAILY_PILOT_JSON" if event == "schedule" else "SCAN_LIVE_APPROVAL_JSON", "")
        require(0 < len(raw.encode()) <= 16384, "NO_FINITE_POLICY")
        policy=json.loads(raw)
        # Expired/inactive schedules refuse without a history or provider call.
        history_url = LEGACY_HISTORY
        renewable = policy.get("schema") == RENEWABLE
        if renewable:
            days, start, release = calendar(policy,event)
            require(start <= now < release, "INACTIVE_WINDOW_OR_RUN")
            history_url = history_path(days)
        elif event == "schedule":
            require(now < EXPIRY, "INACTIVE_EXPIRED")
            require(now.date() in PILOT_DATES, "INACTIVE_OUTSIDE_PILOT_DATE")
        head=subprocess.check_output(["git", "rev-parse", "HEAD"], timeout=5, text=True).strip()
        require(head == os.environ.get("GITHUB_SHA"), "CHECKOUT_MISMATCH")
        current=github("actions/runs/"+os.environ["GITHUB_RUN_ID"])
        history = complete_history(github(history_url), "workflow_runs")
        manual = predecessor = None
        if renewable and event == "workflow_dispatch":
            if not any(r["id"] == policy["predecessor_run_id"] for r in history):
                predecessor = github(f"actions/runs/{policy['predecessor_run_id']}")
        if renewable and event == "schedule":
            manual = github(f"actions/runs/{policy['manual_run_id']}")
            check_daily_sources(root, head, policy, manual, history)
        now=dt.datetime.now(dt.timezone.utc)  # Do not reuse a pre-network clock.
        receipt.update(admit(policy, dict(os.environ), now, code_pins(root), current, history, root=root, manual=manual, predecessor=predecessor))
        now=dt.datetime.now(dt.timezone.utc)  # Recheck fit after any prior-job evidence read.
        if renewable:
            require(now + dt.timedelta(seconds=BUDGETS["SCAN_HTTP_CAMPAIGN_SECONDS"]+30) < stamp(receipt["not_after_utc"]) and
                    now + dt.timedelta(seconds=1200+900+1800) < stamp(receipt["release_not_after_utc"]),
                    "INSUFFICIENT_REMAINING_WINDOW")
        # Setup/provision/qualification 60 min + acquisition 20 + publisher 15 + queue/Pages/
        # closure reserve 30. This is configured fit, not a runtime prediction.
        if phase == 'initial':
            require(now + dt.timedelta(seconds=3600+1200+900+1800) < stamp(receipt['release_not_after_utc']), 'INSUFFICIENT_COMPLETE_RUN_FIT')
        require(shutil.disk_usage(root).free >= 12*1024**3, "INSUFFICIENT_HOST_HEADROOM")
        if phase == 'initial':
            mem=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
            require(int(mem['MemAvailable'].split()[0])*1024 > 5*1024**3, 'INSUFFICIENT_MEMORY_HEADROOM')
        if phase == "pre-acquisition":
            qualification=json.loads((control/"hosted-qualification.json").read_text())
            require(qualification.get("status") == "PASS" and qualification.get("platform") == "linux" and
                    qualification.get("source_sha") == head, "HOSTED_QUALIFICATION_MISSING")
            runtime=json.loads((control/"runtime-lock.json").read_text());signature=runtime_signature(runtime)
            require(policy["kind"] == "manual" or signature == policy["runtime_signature"], "RUNTIME_CHANGED")
            receipt["runtime_signature"]=signature
            durable(control/"campaign-reservation.json", {"state":"UNKNOWN_RESERVED_BEFORE_LAUNCH", "run_id":current["id"],
                "attempt":1, "maximum_requests":87, "maximum_decoded_bytes":182452224})
        receipt["policy_sha256"]=hashlib.sha256(raw.encode()).hexdigest()
        with open(os.environ["GITHUB_ENV"], "a") as f:
            for k,v in BUDGETS.items():f.write(k+"="+str(v)+"\n")
            f.write("SCAN_SOURCE_CUTOFF_DATE="+receipt["source_cutoff_date"]+"\n")
            f.write("SCAN_RUN_DEADLINE_UTC="+receipt["not_after_utc"]+"\n")
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:f.write("release_not_after="+receipt["release_not_after_utc"]+"\n")
    except Refused as e:
        receipt["status"]=str(e);raise SystemExit("SCAN admission: "+str(e))
    except Exception:
        receipt["status"]="UNCLASSIFIED_ADMISSION_FAILURE";raise SystemExit("SCAN admission failed closed")
    finally:
        durable(control/("approval-"+phase+".json"), receipt)
    print("SCAN finite "+phase+" admission passed; no provider request from guard.")

if __name__ == "__main__":main()
