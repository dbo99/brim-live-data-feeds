"""Admit one cleared SWE slot on the shared NRCS/CDEC calendar."""
from datetime import datetime, time, timedelta, timezone
import json
import os
import re
import subprocess
import sys

WORKFLOW = ".github/workflows/build-snow-pillow-latest.yml"
SHARED = {288862661, 287560809, 398301342, 281942719}
SOURCE = (WORKFLOW, ".github/actions/setup-r-hardened", "scripts/configure_apt_mirror_order.sh",
          "scripts/snow_pillow_daily_gate.py", "scripts/snow_pillow_daily_http.R",
          "scripts/build_snow_pillow_latest.R", "scripts/snow_pillow_partial_refresh_helpers.R",
          "scripts/snow_pillow_publisher.py", "scripts/main_publisher.py",
          "data/input/snow_pillow_station_index.csv", "data/input/snow_pillow_swe_normal_medians.csv")
PRODUCT = tuple("docs/data/snow_pillow_" + suffix for suffix in
                ("latest.geojson", "latest_summary.json", "current_wy_trace.csv", "current_wy_trace_summary.json"))


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def slot(env, now, phase):
    event, date, number, source, prior, issued_text = env.get("SWE_CLEARANCE", "").split("/")
    require(event == env["GITHUB_EVENT_NAME"] and event in {"schedule", "workflow_dispatch"}
            and (event != "schedule" or env["SWE_CRON"] == "20 22 * * *")
            and env["GITHUB_REPOSITORY"] == "dbo99/brim-live-data-feeds"
            and env["GITHUB_REF"] == "refs/heads/main"
            and env["GITHUB_WORKFLOW_REF"] == f'dbo99/brim-live-data-feeds/{WORKFLOW}@refs/heads/main'
            and env["GITHUB_RUN_ATTEMPT"] == "1" and number == env["GITHUB_RUN_NUMBER"]
            and number.isdecimal() and env.get("NRCS_HOLD") == "false", "Uncleared SWE event")
    require(all(re.fullmatch("[0-9a-f]{40}", sha) for sha in (source, prior)), "Invalid slot pins")
    day = datetime.strptime(date, "%Y-%m-%d").date()
    start = datetime.combine(day, time(22, 20), timezone.utc)
    # Leave more than the entire publisher job timeout before 00:10 UTC.
    cutoff = start + timedelta(minutes={"start": 25, "acquire": 25, "publish": 90}[phase])
    require(start <= now < cutoff, "SWE slot closed")
    issued = datetime.strptime(issued_text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    require(start - timedelta(minutes=30) <= issued <= now, "Invalid SWE clearance issuance")
    if phase != "publish":
        require(now - issued <= timedelta(minutes=30), "Stale resource clearance")
    return source, prior


def github(endpoint):
    raw = subprocess.check_output(["gh", "api", "repos/dbo99/brim-live-data-feeds/" + endpoint],
                                  timeout=15)
    return json.loads(raw)


def nonconflict(run_id, get):
    for status in ("queued", "in_progress", "requested", "waiting", "pending"):
        response = get("actions/runs?per_page=100&status=" + status)
        rows = response["workflow_runs"]
        require(len({r["id"] for r in rows}) == len(rows) == response["total_count"],
                "Incomplete shared provider queue")
        require(all(r["workflow_id"] not in SHARED or r["id"] == run_id for r in rows),
                "Competing NRCS, CDEC or SWE run")


def main(phase):
    env = os.environ
    clock = lambda: datetime.now(timezone.utc)
    source, prior = slot(env, clock(), phase)
    head = env["GITHUB_SHA"]
    require(subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == head,
            "Checkout differs from event")
    for pin, paths in ((source, SOURCE), (prior, PRODUCT)):
        subprocess.run(["git", "merge-base", "--is-ancestor", pin, head], check=True)
        subprocess.run(["git", "diff", "--quiet", pin, head, "--", *paths], check=True)
    subprocess.run(["git", "diff", "--quiet", head, "--", *SOURCE, *PRODUCT], check=True)
    if phase == "acquire":
        nonconflict(int(env["GITHUB_RUN_ID"]), github)
    slot(env, clock(), phase)


if __name__ == "__main__":
    main(sys.argv[1])
