#!/usr/bin/env python3
"""Finite SCAN manual and seven-date pilot admission; no provider client."""
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

def admit(policy, env, now, pins, current, history):
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
    require(policy.get("approved") is True and policy.get("schema") == "scan-c13r-finite-1", "NO_FINITE_POLICY")
    require(policy.get("code_sha256") == pins and set(pins) == set(CODE_PATHS), "CODE_CHANGED")
    require(policy.get("budgets") == BUDGETS and all(type(x) is int for x in policy['budgets'].values()), "WRONG_BUDGETS")
    require(env.get("SCAN_PROVIDER_HOLD") == "false", "PROVIDER_HOLD_OR_UNKNOWN")
    require(type(history) is list and len(history) <= 100, "INCOMPLETE_RUN_HISTORY")
    event = env["GITHUB_EVENT_NAME"]
    require(sum(x.get('id') == current['id'] for x in history) == 1, "CURRENT_RUN_MISSING_OR_DUPLICATE")
    require(all(x.get('workflow_id') == WORKFLOW_ID for x in history), "WRONG_HISTORY_WORKFLOW")
    common = {'approved','schema','kind','code_sha256','budgets','acquire_only'}
    extra = {'source_sha','run_number','not_before_utc','not_after_utc','release_not_after_utc','source_cutoff_date'} if event == 'workflow_dispatch' else {'dates','expires_utc','manual_run_id','manual_source_sha','runtime_signature'}
    require(set(policy) == common | extra and policy['acquire_only'] is False, "UNDECLARED_POLICY_OR_MODE")
    today = now.astimezone(ZoneInfo("America/Los_Angeles")).date()
    if event == "workflow_dispatch":
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
    require(today.year == 2026 and today.month == 11 and policy.get("acquire_only", False) is False and
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
    request = urllib.request.Request("https://api.github.com/repos/"+REPOSITORY+"/"+path,
        headers={"Authorization":"Bearer "+token, "Accept":"application/vnd.github+json", "X-GitHub-Api-Version":"2022-11-28"})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
        body=response.read(4*1024**2+1)
        require(response.status == 200 and len(body) <= 4*1024**2, "HISTORY_RESPONSE_LIMIT")
    return json.loads(body)

def valid_history_endpoint(path):
    return bool(re.fullmatch(r'actions/runs/[1-9][0-9]*',path)) or path == (
        'actions/workflows/'+str(WORKFLOW_ID)+'/runs?created=2026-11-11..2026-11-20&per_page=100')

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
        if event == "schedule":
            require(now < EXPIRY, "INACTIVE_EXPIRED")
            require(now.date() in PILOT_DATES, "INACTIVE_OUTSIDE_PILOT_DATE")
        head=subprocess.check_output(["git", "rev-parse", "HEAD"], timeout=5, text=True).strip()
        require(head == os.environ.get("GITHUB_SHA"), "CHECKOUT_MISMATCH")
        current=github("actions/runs/"+os.environ["GITHUB_RUN_ID"])
        response=github("actions/workflows/"+str(WORKFLOW_ID)+"/runs?created=2026-11-11..2026-11-20&per_page=100")
        require(response["total_count"] == len(response["workflow_runs"]) <= 100, "INCOMPLETE_RUN_HISTORY")
        now=dt.datetime.now(dt.timezone.utc)  # Do not reuse a pre-network clock.
        receipt.update(admit(policy, dict(os.environ), now, code_pins(root), current, response["workflow_runs"]))
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
