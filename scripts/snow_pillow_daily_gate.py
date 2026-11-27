"""Admit one bounded SWE acquisition for an eligible Pacific source day."""
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
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


PACIFIC = ZoneInfo("America/Los_Angeles")
SWE_ID = 288862661
BUILD = "Build isolated Snow Pillow candidate"
RECEIPT = "snow-pillow-generation-"


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def slot(env, now, phase, created):
    event, first, last, source, prior, anchor = env.get("SWE_CLEARANCE", "").split("/")
    require(event == env["GITHUB_EVENT_NAME"] and event in {"schedule", "workflow_dispatch"}
            and (event != "schedule" or env["SWE_CRON"] == "20 22 * * *")
            and env["GITHUB_REPOSITORY"] == "dbo99/brim-live-data-feeds"
            and env["GITHUB_REF"] == "refs/heads/main"
            and env["GITHUB_WORKFLOW_REF"] == f'dbo99/brim-live-data-feeds/{WORKFLOW}@refs/heads/main'
            and env["GITHUB_RUN_ATTEMPT"] == "1" and env.get("NRCS_HOLD") == "false",
            "Uncleared SWE event")
    require(all(re.fullmatch("[0-9a-f]{40}", sha) for sha in (source, prior)), "Invalid term pins")
    require(re.fullmatch("[1-9][0-9]{0,19}", anchor), "Invalid historical SWE run ID")
    first, last = date.fromisoformat(first), date.fromisoformat(last)
    day = created.astimezone(PACIFIC).date()
    require(first <= day <= last and (last-first).days < 7
            and (event == "schedule" or first == last), "Outside finite SWE term")
    release = datetime.combine(day + timedelta(days=1), time.min, PACIFIC)
    target = datetime.combine(day, time(22, 20), timezone.utc)
    require(target <= created <= now and now + timedelta(minutes={
        "start": 80, "acquire": 50, "publish": 20
    }[phase]) < release, "SWE day or remaining headroom ineligible")
    return source, prior, int(anchor), first, day


def github(endpoint):
    return json.loads(subprocess.check_output(
        ["gh", "api", "repos/dbo99/brim-live-data-feeds/" + endpoint], timeout=15))


def complete(result, key):
    rows = result[key]
    require(len({r["id"] for r in rows}) == len(rows) == result["total_count"],
            "Incomplete GitHub inventory")
    return rows


def jobs(run, get):
    rows = complete(get(f"actions/runs/{run['id']}/jobs?filter=all&per_page=100"), "jobs")
    require(rows and all(j["run_id"] == run["id"] and j["head_sha"] == run["head_sha"]
                         and j["run_attempt"] == run["run_attempt"] for j in rows),
            "Missing or mismatched jobs")
    return rows


def nonconflict(run_id, get):
    for status in ("queued", "in_progress", "requested", "waiting", "pending"):
        for run in complete(get("actions/runs?per_page=100&status=" + status), "workflow_runs"):
            if run["workflow_id"] not in SHARED or run["id"] == run_id:
                continue
            # A queued run without its preparation job is still a potential acquirer.
            peer_jobs = jobs(run, get)
            prep = [j for j in peer_jobs if j["name"] == "prepare-candidate"]
            require(len(prep) == 1 and prep[0]["status"] == "completed"
                    and prep[0]["conclusion"] in {"success", "skipped"}
                    and all(j["status"] == "completed" or j["name"] == "publish-to-main"
                            for j in peer_jobs),
                    "Competing or unknown provider preparation")


def git(*args):
    return subprocess.check_output(["git", *args], text=True, timeout=30).strip()


def created_at(run):
    return datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))


def product_commit(rev):
    commit = git("log", "-1", "--format=%H", rev, "--", *PRODUCT)
    require(re.fullmatch("[0-9a-f]{40}", commit), "Missing product history")
    return commit


def verify_successor(previous, commit, run_id):
    if previous == commit:
        return  # A successful publisher may retain its unchanged canonical product.
    require(product_commit(commit + "^") == previous and
            git("show", "-s", "--format=%s", commit) ==
            f"Update snow pillow SWE latest feed (run {run_id})", "Wrong SWE publication")
    changed = git("diff", "--name-only", commit + "^", commit).splitlines()
    require(changed and set(changed) <= set(PRODUCT), "Unexpected SWE publication paths")


def generation(source, prior, history, current, anchor, get):
    git("merge-base", "--is-ancestor", prior, current["head_sha"])
    prior = product_commit(prior)
    charged_days = set()
    require(anchor["workflow_id"] == SWE_ID and anchor["head_branch"] == "main"
            and anchor["status"] == "completed" and anchor["run_attempt"] == 1
            and created_at(anchor) < created_at(current),
            "Unreviewed SWE anchor")
    ordered = sorted(history, key=lambda r: r["run_number"])
    require(ordered and ordered[0]["id"] == anchor["id"] and ordered[-1]["id"] == current["id"]
            and all(b["run_number"] == a["run_number"] + 1 for a, b in zip(ordered, ordered[1:])),
            "Incomplete SWE history")
    for run in ordered[1:-1]:
        require(run["workflow_id"] == SWE_ID and run["head_branch"] == "main"
                and run["run_attempt"] == 1 and run["status"] == "completed"
                and run["run_number"] < current["run_number"], "Unresolved SWE run")
        git("diff", "--quiet", source, run["head_sha"], "--", *SOURCE)
        records = jobs(run, get)
        prep = [j for j in records if j["name"] == "prepare-candidate"]
        pub = [j for j in records if j["name"] == "publish-to-main"]
        require(len(records) == 2 and len(prep) == len(pub) == 1
                and all(j["status"] == "completed" for j in records), "Unknown SWE job graph")
        build = [s["conclusion"] for s in prep[0]["steps"] if s["name"] == BUILD]
        if pub[0]["conclusion"] == "skipped" and (build == ["skipped"] or
                (prep[0]["conclusion"] == "skipped" and prep[0]["steps"] == [])):
            continue
        require(build == ["success"] and run["conclusion"] == "success"
                and pub[0]["conclusion"] == "success", "Unreconciled SWE spending")
        artifacts = complete(get(f"actions/runs/{run['id']}/artifacts?per_page=100"), "artifacts")
        receipts = [a["name"][len(RECEIPT):] for a in artifacts
                    if not a["expired"] and a["name"].startswith(RECEIPT)]
        require(len(receipts) == 1 and re.fullmatch("[0-9a-f]{40}", receipts[0]),
                "Missing SWE publication readback")
        git("merge-base", "--is-ancestor", prior, receipts[0])
        verify_successor(prior, receipts[0], run["id"])
        charged_day = created_at(run).astimezone(PACIFIC).date()
        require(charged_day not in charged_days and charged_day < created_at(current).astimezone(PACIFIC).date(),
                "SWE source day already acquired")
        charged_days.add(charged_day)
        prior = receipts[0]
    return prior


def predecessor(source, prior, head, day):
    git("fetch", "--quiet", "--no-tags", "origin", "refs/heads/main")
    live = git("rev-parse", "FETCH_HEAD")
    for rev in (source, prior, head):
        git("merge-base", "--is-ancestor", rev, live)
    git("merge-base", "--is-ancestor", source, head)
    git("merge-base", "--is-ancestor", prior, head)
    git("diff", "--quiet", source, live, "--", *SOURCE)
    git("diff", "--quiet", head, live, "--", *SOURCE, *PRODUCT)
    require(product_commit(live) == product_commit(prior), "Wrong live SWE predecessor")
    git("diff", "--quiet", prior, live, "--", *PRODUCT)
    git("diff", "--quiet", head, "--", *SOURCE, *PRODUCT)
    for path in (PRODUCT[1], PRODUCT[3]):
        summary = json.loads(Path(path).read_text())
        require(summary["current_water_year"] == day.year + (day.month >= 10)
                and date.fromisoformat(summary["fetch_end_date"]) < day,
                "Duplicate, stale or unsupported SWE source day")


def check_enabled(get):
    require(get(f"actions/workflows/{SWE_ID}")["state"] == "active", "SWE authority revoked")


def main(phase):
    env = os.environ
    head = env["GITHUB_SHA"]
    require(git("rev-parse", "HEAD") == head, "Checkout differs from event")
    if phase == "receipt":
        git("fetch", "--quiet", "--no-tags", "origin", "refs/heads/main")
        commit = product_commit("FETCH_HEAD")
        verify_successor(env["SWE_PREDECESSOR_GENERATION"], commit, env["GITHUB_RUN_ID"])
        (Path(env["RUNNER_TEMP"]) / "swe-generation.txt").write_text(commit + "\n")
        with open(env["GITHUB_OUTPUT"], "a") as out:
            print("generation=" + commit, file=out)
        return
    run_id = int(env["GITHUB_RUN_ID"])
    current = github(f"actions/runs/{run_id}")
    require(current["id"] == run_id and current["workflow_id"] == SWE_ID and
            current["head_sha"] == head and current["head_branch"] == "main" and
            current["event"] == env["GITHUB_EVENT_NAME"] and current["run_attempt"] == 1
            and current["status"] == "in_progress",
            "Unbound SWE run")
    created = created_at(current)
    source, prior, anchor_id, first, day = slot(env, datetime.now(timezone.utc), phase, created)
    anchor = github(f"actions/runs/{anchor_id}")
    require(anchor["id"] == anchor_id and created_at(anchor).astimezone(PACIFIC).date() < first,
            "Wrong SWE anchor or overlapping operating term")
    start = created_at(anchor).astimezone(timezone.utc)
    rows = complete(github(f"actions/workflows/{SWE_ID}/runs?"
                           f"created=%3E%3D{start:%Y-%m-%dT%H:%M:%SZ}&per_page=100"), "workflow_runs")
    rows = [row for row in rows if row["run_number"] >= anchor["run_number"]]
    prior = generation(source, prior, rows, current, anchor, github)
    predecessor(source, prior, head, day)
    if phase == "acquire":
        nonconflict(run_id, github)
        with open(env["GITHUB_ENV"], "a") as out:
            print(f"SNOW_PILLOW_FETCH_END_DATE={day}", file=out)
    if phase == "publish":
        with open(env["GITHUB_ENV"], "a") as out:
            print(f"SWE_PREDECESSOR_GENERATION={prior}", file=out)
    check_enabled(github)
    slot(env, datetime.now(timezone.utc), phase, created)


if __name__ == "__main__":
    main(sys.argv[1])
