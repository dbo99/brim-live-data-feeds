"""Finite bootstrap/successor admission and private preparation supervision.

Manual grants bind an exact run. A scheduled term binds reviewed source, an
acknowledged predecessor, finite source days and the daily release time.
Only positive, explicitly selected accounting is printed; raw source and failures
remain in the private runner directory, never an Actions artifact.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time

REPO = 'dbo99/brim-live-data-feeds'
WORKFLOW = '.github/workflows/build-snotel-sms-current-wy.yml'
EXPIRY = '2026-11-14T07:59:59Z'
APPROVAL = 'L11-SNOTEL-SMS-R9-HOSTED-ORIGIN-AND-FIRST-PRODUCT-20261112'
LIMITS = dict(attempts=32, station_bytes=1048576, station_seconds=60,
              total_bytes=33554432, total_seconds=1920, finalization_seconds=120)
FIELDS = {'version', 'approval_id', 'source_sha', 'run_number', 'window_end', 'activated_at_utc',
          'catalog_sha256', 'roster_sha256', 'limits'}


def require(value, label):
    if not value:
        raise ValueError(label)


def utc(value):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value), 'UTC format')
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def authorize(env, phase, current):
    require(phase in ('prepare', 'publish'), 'Phase')
    raw = env.get('SNOTEL_SMS_FIRST_RUN_JSON', '')
    require(len(raw.encode()) <= 4096, 'Grant size')
    def pairs(items):
        result = {}
        for k, v in items:
            require(k not in result, 'Duplicate grant key')
            result[k] = v
        return result
    grant = json.loads(raw, object_pairs_hook=pairs)
    require(type(grant) is dict and set(grant) == FIELDS, 'Grant fields')
    require(grant['version'] == 'snotel-first-run-1' and
            grant['approval_id'] == APPROVAL, 'Approval identity')
    require(env.get('GITHUB_REPOSITORY') == REPO and env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
            and env.get('GITHUB_REF') == 'refs/heads/main'
            and env.get('GITHUB_WORKFLOW_REF') == REPO+'/'+WORKFLOW+'@refs/heads/main', 'Hosted workflow identity')
    require(env.get('GITHUB_RUN_ATTEMPT') == '1' and
            re.fullmatch('[1-9][0-9]*', env.get('GITHUB_RUN_ID', '')) is not None, 'Hosted attempt identity')
    require(type(grant['run_number']) is int and grant['run_number'] == 2 and
            env.get('GITHUB_RUN_NUMBER') == str(grant['run_number']), 'Exact run number')
    require(isinstance(grant['source_sha'], str) and re.fullmatch('[0-9a-f]{40}', grant['source_sha'])
            and env.get('GITHUB_SHA') == grant['source_sha'], 'Exact event source')
    for field, variable in [('catalog_sha256', 'CATALOG_PIN'), ('roster_sha256', 'ROSTER_PIN')]:
        require(isinstance(grant[field], str) and re.fullmatch('[0-9a-f]{64}', grant[field])
                and env.get(variable) == grant[field], 'Catalog/roster binding')
    require(env.get('RELEASE_APPROVED') == 'true', 'Public disclosure approval')
    require(type(grant['limits']) is dict and set(grant['limits']) == set(LIMITS)
            and all(type(grant['limits'][k]) is int and grant['limits'][k] == v for k, v in LIMITS.items()), 'Exact budgets')
    deadline = utc(grant['window_end'])
    activated = utc(grant['activated_at_utc'])
    require(activated <= current and 0 < (deadline-activated).total_seconds() <= 14400, 'Activation/window bound')
    require(deadline <= utc(EXPIRY) and current < deadline, 'Finite expiry')
    require((deadline-current).total_seconds() >= (5400 if phase == 'prepare' else 1500), 'Complete remaining fit')
    if phase == 'prepare':
        require(env.get('WINDOW_END') == grant['window_end'] and
                all(env.get(k) == 'true' for k in ('ACQUIRE', 'PUBLISH', 'BOOTSTRAP')), 'First-product modes')
    return grant


# The first two hosted grants are permanently spent. The second campaign was
# positively accounted before the separately authenticated retained publication.
ACCOUNTED_R9 = 43298591880
OPERATING_WORKFLOW_ID = 398301342
SHARED_WORKFLOWS = {OPERATING_WORKFLOW_ID, 287560809, 288862661}
SLOT_LENGTH = timedelta(hours=3)
DAILY_CRON = '11 18 * * *'
TERM_VERSION = 'snotel-successor-2'
# The acknowledged manual predecessor and the closed commissioning slots.
COMMISSIONING_WORKFLOW = 'f15565d1c62dcc576e82c2d0c06182f9850c8683'
TERM_STEP = 'Check scheduled term before acquisition'
BUILD_STEP = 'Prepare complete bounded current-WY candidate'
UPLOAD_STEP = 'Upload only the validated public candidate'


def strict_object(raw, cap):
    require(isinstance(raw, str) and 0 < len(raw.encode()) <= cap, 'Missing or oversized policy')
    def unique(items):
        obj = dict(items)
        require(len(obj) == len(items), 'Duplicate policy key')
        return obj
    return json.loads(raw, object_pairs_hook=unique)


def slot(policy, env, clock):
    fields = {'version', 'event', 'reviewed_head', 'predecessor_commit', 'predecessor_generation',
              'predecessor_run_id', 'first_run', 'first_start_utc', 'first_cutoff', 'slots',
              'catalog_sha256', 'roster_sha256', 'limits', 'python_version'}
    require(isinstance(policy, dict), 'Successor policy')
    term = policy.get('version') == TERM_VERSION
    if term:
        fields = (fields - {'first_run'}) | {'daily_release_utc'}
    require(set(policy) == fields and (term or policy['version'] == 'snotel-successor-1'), 'Successor policy')
    event = policy['event']
    require(env.get('GITHUB_ACTIONS') == 'true' and env.get('GITHUB_REPOSITORY') == REPO
            and env.get('GITHUB_REPOSITORY_ID') == '1247282299'
            and env.get('GITHUB_REF') == 'refs/heads/main' and env.get('GITHUB_REF_TYPE') == 'branch'
            and env.get('GITHUB_WORKFLOW_REF') == REPO+'/'+WORKFLOW+'@refs/heads/main'
            and env.get('GITHUB_WORKFLOW_SHA') == env['GITHUB_SHA']
            and event == env.get('GITHUB_EVENT_NAME') and event in ('workflow_dispatch', 'schedule'),
            'Successor source event')
    for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_NUMBER'):
        require(re.fullmatch(r'[1-9][0-9]*', env.get(key, '')), 'Run identity')
    require(env.get('GITHUB_RUN_ATTEMPT') == '1' and env.get('BOOTSTRAP') == 'false'
            and env.get('ACQUIRE') == env.get('PUBLISH') == env.get('RELEASE_APPROVED') == 'true',
            'Successor acquisition/publication modes')
    for key in ('reviewed_head', 'predecessor_commit'):
        require(re.fullmatch('[0-9a-f]{40}', policy[key]), 'Reviewed source/product commit')
    require(re.fullmatch('[0-9a-f]{40}', env['GITHUB_SHA']), 'Source commit')
    for key, var in [('catalog_sha256', 'CATALOG_PIN'), ('roster_sha256', 'ROSTER_PIN')]:
        require(re.fullmatch('[0-9a-f]{64}', policy[key]) and policy[key] == env.get(var), 'Catalog/roster')
    require(re.fullmatch('[0-9a-f]{64}', policy['predecessor_generation']), 'Prior generation')
    require(policy['limits'] == LIMITS and all(type(x) is int for x in policy['limits'].values()), 'Budgets')
    for key in ('predecessor_run_id', 'slots'):
        require(type(policy[key]) is int and policy[key] > 0, 'Finite run binding')
    require((term or (type(policy['first_run']) is int and policy['first_run'] > 2))
            and 1 <= policy['slots'] <= (7 if event == 'schedule' else 1),
            'Spent or unbounded run')
    start = utc(policy['first_start_utc'])
    if event == 'schedule':
        require(start.strftime('%H:%M:%S') == '18:11:00' and env.get('SCHEDULE') == DAILY_CRON,
                'Scheduled slot')
    if term:
        require(event == 'schedule', 'Scheduled term only')
        require(re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', policy['daily_release_utc']), 'Daily release')
        first = (start - timedelta(hours=8, days=1)).date()
        year = first.year + (first.month >= 10)
        last_operating_day = first + timedelta(days=policy['slots'])
        require(policy['first_cutoff'] == first.isoformat()
                and last_operating_day.year + (last_operating_day.month >= 10) == year,
                'Term exceeds the eligible current-WY prefix')
        index = (clock - start).days
        require(0 <= index < policy['slots'], 'Outside authorized term')
        start += timedelta(days=index)
        hour, minute = map(int, policy['daily_release_utc'].split(':'))
        end = start.replace(hour=hour, minute=minute)
        if end <= start:
            end += timedelta(days=1)
        require(end <= (start + timedelta(days=1)).replace(hour=8, minute=0) and start <= clock < end,
                'Outside daily release window')
        return index, start, end, first + timedelta(days=index)
    index = int(env['GITHUB_RUN_NUMBER']) - policy['first_run']
    require(0 <= index < policy['slots'], 'Outside authorized run numbers')
    cutoff = (start - timedelta(hours=8, days=1)).date()
    require(policy['first_cutoff'] == cutoff.isoformat(), 'Source cutoff')
    start += timedelta(days=index)
    end = start + SLOT_LENGTH
    cutoff += timedelta(days=index)
    require(start <= clock < end and (end - timedelta(hours=8, days=1)).date() == cutoff, 'Slot window')
    if event == 'workflow_dispatch':
        require(env.get('WINDOW_END') == end.strftime('%Y-%m-%dT%H:%M:%SZ'), 'Manual deadline')
    return index, start, end, cutoff


def get_github(path, token):
    # Paths are constructed here from fixed endpoints and validated numeric IDs.
    require(token, 'Missing job token')
    result = subprocess.run(['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
                             'repos/'+REPO+'/'+path], capture_output=True, timeout=15,
                            env={'PATH': os.environ['PATH'], 'GH_TOKEN': token, 'GH_PROMPT_DISABLED': '1', 'HOME': os.environ['HOME']})
    require(result.returncode == 0 and len(result.stdout) <= 8 * 1024**2, 'GitHub readback')
    return json.loads(result.stdout)


def complete(response, key):
    rows = response[key]
    require(isinstance(rows, list) and len(rows) == response['total_count'] <= 100
            and len({r['id'] for r in rows}) == len(rows), 'Incomplete GitHub listing')
    return rows


def check_history(policy, env, index, start, anchor, runs, get):
    require(anchor['id'] == policy['predecessor_run_id'] and 0 < anchor['run_number'] < policy['first_run']
            and anchor['workflow_id'] == OPERATING_WORKFLOW_ID and anchor['head_branch'] == 'main'
            and anchor['run_attempt'] == 1 and anchor['status'] == 'completed', 'Predecessor run')
    require(utc(anchor['created_at']) < start, 'Predecessor chronology')
    if anchor['conclusion'] != 'success':
        require(policy['event'] == 'workflow_dispatch' and anchor['id'] == ACCOUNTED_R9
                and anchor['run_number'] == 2 and anchor['conclusion'] == 'failure', 'Unresolved prior spend')
    require(int(env['GITHUB_RUN_NUMBER'])-anchor['run_number'] < 100, 'Run-history bound')
    by_number = {r['run_number']: r for r in runs}
    require(set(by_number) == set(range(anchor['run_number'], int(env['GITHUB_RUN_NUMBER'])+1))
            and len(runs) == len(by_number) and by_number.get(anchor['run_number']) == anchor,
            'Incomplete or competing run history')
    # A disabled cron still receives a run number. A separately frozen manual
    # grant may bridge only positively proven all-skipped jobs since its anchor.
    for number in range(anchor['run_number']+1, policy['first_run']):
        run = by_number[number]
        require(policy['event'] == 'workflow_dispatch' and run['event'] == 'schedule'
                and run['workflow_id'] == OPERATING_WORKFLOW_ID and run['head_branch'] == 'main'
                and run['run_attempt'] == 1 and run['status'] == 'completed' and run['conclusion'] == 'skipped',
                'Intervening spend unresolved')
        jobs = complete(get(f"actions/runs/{run['id']}/jobs?filter=all&per_page=100"), 'jobs')
        require(len(jobs) == 2 and {j['name'] for j in jobs} == {'prepare-candidate','publish-to-main'}
                and all(j['run_attempt'] == 1 and j['conclusion'] == 'skipped' and
                        not any(s['conclusion'] != 'skipped' for s in j['steps']) for j in jobs),
                'No-spend proof missing')
    for offset in range(index+1):
        run = by_number[policy['first_run']+offset]
        day_start = start - timedelta(days=index-offset)
        require(run['workflow_id'] == OPERATING_WORKFLOW_ID and run['head_branch'] == 'main'
                and run['event'] == policy['event'] and run['run_attempt'] == 1
                and day_start <= utc(run['created_at']) < day_start+SLOT_LENGTH, 'Duplicate or mistimed run')
        if offset == index:
            require(str(run['id']) == env['GITHUB_RUN_ID'] and run['head_sha'] == env['GITHUB_SHA']
                    and run['status'] == 'in_progress', 'Current hosted run')
        else:
            require(run['status'] == 'completed' and run['conclusion'] == 'success', 'Ambiguous earlier slot')


def require_delivered(run, get):
    jobs = complete(get(f"actions/runs/{run['id']}/jobs?filter=all&per_page=100"), 'jobs')
    publishers = [j for j in jobs if j['name'] == 'publish-to-main' and j['run_attempt'] == 1]
    require(len(publishers) == 1 and publishers[0]['conclusion'] == 'success' and
            any(step['name'] == 'Authenticate committed delivery independently' and step['conclusion'] == 'success'
                for step in publishers[0]['steps']), 'Previous run lacked committed acknowledgment')


def term_deliveries(reader, policy, env, anchor, rows, start, end, get):
    """Account for every actual run since the owner-selected successful delivery."""
    current_number = int(env['GITHUB_RUN_NUMBER'])
    ordered = sorted(rows, key=lambda r: r['run_number'])
    require(1 < len(ordered) <= 100 and ordered[0] == anchor
            and 0 < current_number - anchor['run_number'] < 100
            and [r['run_number'] for r in ordered] == list(range(anchor['run_number'], current_number+1)), 'Incomplete term history')
    current = ordered[-1]
    require(anchor['id'] == policy['predecessor_run_id'] and anchor['conclusion'] == 'success'
            and utc(anchor['created_at']) < utc(policy['first_start_utc']), 'Wrong term anchor')
    require(str(current['id']) == env['GITHUB_RUN_ID'] and current['head_sha'] == env['GITHUB_SHA']
            and current['status'] == 'in_progress' and current['event'] == 'schedule'
            and start <= utc(current['created_at']) < end, 'Wrong or late current run')
    deliveries = []
    active_workflow = reader.git(['rev-parse', env['GITHUB_SHA']+':'+WORKFLOW]).decode().strip()
    created = [utc(r['created_at']) for r in ordered]
    require(created == sorted(created), 'Out-of-order term history')
    prep_names = ['Set up job','Validate explicit modes','Check out exact source event',
                  'Qualify this runner offline before provider admission',TERM_STEP,BUILD_STEP,UPLOAD_STEP,
                  'Post Check out exact source event','Complete job']
    pub_names = ['Set up job','Record publisher job start','Check out candidate source event',
                 'Download closed candidate','Check finite publication admission',
                 'Publish through the unchanged shared owner','Authenticate committed delivery independently',
                 'Post Check out candidate source event','Complete job']
    def graph(job, names, conclusions):
        steps = job['steps']
        return ([s['name'] for s in steps] == names
                and [s['number'] for s in steps] == sorted({s['number'] for s in steps})
                and all(s['status'] == 'completed' for s in steps)
                and [s['conclusion'] for s in steps] == conclusions)
    for run in ordered:
        require(run['workflow_id'] == OPERATING_WORKFLOW_ID and run['head_branch'] == 'main'
                and run['run_attempt'] == 1 and run['event'] in ('schedule','workflow_dispatch'),
                'Unexpected term run')
        if run is current:
            break
        require(run['status'] == 'completed', 'Unfinished prior run')
        source = reader.commit(run['head_sha'])
        reader.git(['merge-base','--is-ancestor',source,env['GITHUB_SHA']])
        workflow = reader.git(['rev-parse',source+':'+WORKFLOW]).decode().strip()
        require(workflow in (active_workflow, COMMISSIONING_WORKFLOW), 'Unknown prior workflow')
        jobs = complete(get(f"actions/runs/{run['id']}/jobs?filter=all&per_page=100"), 'jobs')
        named = {j['name']:j for j in jobs}
        require(len(jobs) == 2 and set(named) == {'prepare-candidate','publish-to-main'}
                and all(j['status'] == 'completed' and j['run_attempt'] == 1 and j['run_id'] == run['id']
                        and j['head_sha'] == source and j['head_branch'] == 'main' for j in jobs),
                'Incomplete predecessor jobs')
        prep, pub = named['prepare-candidate'], named['publish-to-main']
        if run['id'] != anchor['id'] and pub['conclusion'] == 'skipped' and pub['steps'] == []:
            require(run['event'] == 'schedule', 'Unresolved manual spend')
            if prep['conclusion'] == run['conclusion'] == 'skipped' and prep['steps'] == []:
                continue
            # Only the reviewed term workflow has this separate, provider-free gate.
            reader.git(['diff','--quiet',policy['reviewed_head'],source,'--',*operating_paths()])
            require(workflow == active_workflow and prep['conclusion'] == run['conclusion'] == 'failure'
                    and graph(prep, prep_names, ['success']*4 + ['failure','skipped','skipped','success','success']),
                    'Ambiguous term spending')
            continue
        require(run['conclusion'] == prep['conclusion'] == pub['conclusion'] == 'success',
                'Ambiguous predecessor spending')
        expected = [name for name in prep_names if name != TERM_STEP or workflow == active_workflow]
        outcomes = ['skipped' if name == TERM_STEP and run['event'] == 'workflow_dispatch' else 'success'
                    for name in expected]
        require(graph(prep, expected, outcomes) and graph(pub, pub_names, ['success']*len(pub_names)),
                'Incomplete acknowledged delivery')
        published = next(s for s in pub['steps'] if s['name'] == 'Publish through the unchanged shared owner')
        day = (utc(run['created_at']) - timedelta(hours=8, days=1)).date()
        if run['id'] != anchor['id']:
            require(run['event'] == 'schedule' and workflow == active_workflow, 'Unapproved term acquisition')
            reader.git(['diff','--quiet',policy['reviewed_head'],source,'--',*operating_paths()])
            _, prior_start, prior_end, _ = slot(policy, env, utc(run['created_at']))
            require(prior_start < start and utc(published['completed_at']) <= prior_end
                    and day > deliveries[-1][0], 'Duplicate or out-of-term acquisition')
        deliveries.append((day, source, utc(published['started_at']), utc(published['completed_at'])))
    return deliveries


def operating_paths():
    import soil_network_workflow as w
    # The scheduled workflow runs the origin and successor suites. Bind their
    # imported fixtures as well as the production entry points and inputs.
    return (WORKFLOW, w.SNOTEL_CATALOG, *w.SNOTEL_SOURCES, 'scripts/snotel_first_run.py',
            'scripts/soil_moisture/__init__.py',
            'tests/soil_moisture/test_snotel_origin.py', 'tests/soil_moisture/test_snotel_successor.py',
            'tests/soil_moisture/test_snotel_first_run.py', 'tests/soil_moisture/test_snotel_workflow.py',
            'tests/soil_moisture/test_snotel_snapshot.py', 'tests/soil_moisture/test_snotel_transport.py')


def check_product_lineage(reader, policy, source, index, cutoff, deliveries=None):
    import soil_network_workflow as w
    anchor = policy['predecessor_commit']
    reader.git(['merge-base', '--is-ancestor', anchor, source])
    reader.git(['merge-base', '--is-ancestor', policy['reviewed_head'], source])
    reader.git(['diff', '--quiet', policy['reviewed_head'], source, '--', *operating_paths()])
    commits = reader.git(['log', '--first-parent', '--format=%H', '--max-count=8',
                          anchor+'..'+source, '--', *w.ss.PATHS]).decode().splitlines()
    if deliveries is not None:
        require(len(commits)+1 == len(deliveries), 'Unmatched delivery or product change')
        for commit, (day, run_source, begin, finish) in zip([anchor, *reversed(commits)], deliveries):
            reader.git(['merge-base','--is-ancestor',run_source,commit+'^'])
            parents = reader.git(['show','-s','--format=%P',commit]).split()
            changed = reader.git(['diff','--name-only',commit+'^',commit]).decode().splitlines()
            stamp = int(reader.git(['show','-s','--format=%ct',commit]))
            require(len(parents) == 1 and set(changed) == set(w.ss.PATHS) and day < cutoff
                    and begin.timestamp() <= stamp <= finish.timestamp(),
                    'Wrong acknowledged publication')
        index = len(commits)
    require(len(commits) == index, 'Unexpected predecessor product history')
    files, binding = reader.product(anchor)
    require(binding['generation'] == policy['predecessor_generation'], 'Approved predecessor generation')
    if deliveries is not None:
        require(json.loads(files[w.ss.PATHS[1]])['end'] == str(deliveries[0][0]), 'Wrong anchor source day')
    for n, commit in enumerate(reversed(commits)):
        child, _ = reader.product(commit)
        require(w.sp.classify(child, files)['decision'] == 'publish' and
                json.loads(child[w.ss.PATHS[1]])['end'] == str(deliveries[n+1][0] if deliveries is not None else cutoff-timedelta(days=index-n)),
                'Broken daily predecessor chain')
        files = child
    actual, current = reader.product(source)
    require(actual == files and json.loads(actual[w.ss.PATHS[1]])['end'] < str(cutoff),
            'Changed predecessor or already queried cutoff')
    return current


def admit_successor(env, phase, raw, reader, get, clock):
    import hashlib
    policy = strict_object(raw, 4096)
    index, start, end, cutoff = slot(policy, env, clock())
    require(env.get('PROVIDER_HOLD') == 'false', 'Shared-provider status or GitHub access unknown')
    image = platform.freedesktop_os_release()
    require(image.get('ID') == 'ubuntu' and image.get('VERSION_ID') == '24.04'
            and platform.system() == 'Linux' and sys.version_info[:2] == (3, 12)
            and (policy['python_version'] == platform.python_version() or
                 (policy['event'] == 'workflow_dispatch' and policy['python_version'] == '3.12.x')),
            'Qualified Ubuntu Python runtime')
    source = env['GITHUB_SHA']
    require(not reader.test_stub and reader.git(['rev-parse','HEAD']).decode().strip() == source,
            'Actual event checkout required')
    term = policy['version'] == TERM_VERSION
    digest = hashlib.sha256(raw.encode()).hexdigest()
    if term:
        require(env.get('NRCS_CLEARANCE') == 'schedule/term/'+digest, 'Term revoked or clearance mismatch')
    else:
        current = check_product_lineage(reader, policy, source, index, cutoff)
        # A fresh scoped clearance binds this exact policy, run, and observed prior.
        # The owner installs it only after confirming shared capacity and backoff.
        clearance = f"{policy['event']}/{digest}/{env['GITHUB_RUN_NUMBER']}/{cutoff}/{current['generation']}"
        parts = env.get('NRCS_CLEARANCE', '').rsplit('/', 2)
        require(len(parts) == 3 and parts[0] == clearance, 'Fresh slot clearance missing')
        issued, expires = utc(parts[1]), utc(parts[2])
        require(start-timedelta(minutes=30) <= issued <= clock() < expires == end
                and (phase != 'prepare' or (clock()-issued).total_seconds() <= 1800), 'Clearance validity')
    anchor = get(f"actions/runs/{policy['predecessor_run_id']}")
    since = utc(anchor['created_at']).strftime('%Y-%m-%d')
    rows = complete(get(f'actions/workflows/{OPERATING_WORKFLOW_ID}/runs?created=%3E%3D{since}&per_page=100'), 'workflow_runs')
    runs = [row for row in rows if row['run_number'] >= anchor['run_number']]
    # Run-detail responses can include additional keys absent from list entries.
    anchor_fields = ('id','run_number','run_attempt','workflow_id','head_branch','status','conclusion','head_sha','created_at','event')
    def compact(row):
        return {key: row[key] for key in anchor_fields}
    if term:
        deliveries = term_deliveries(reader, policy, env, compact(anchor), [compact(row) for row in runs], start, end, get)
        current = check_product_lineage(reader, policy, source, index, cutoff, deliveries)
    else:
        check_history(policy, env, index, start, compact(anchor), [compact(row) for row in runs], get)
        for row in runs:
            if row['status'] == 'completed' and row['conclusion'] == 'success':
                require_delivered(row, get)
        if policy['event'] == 'schedule':
            for row in runs:
                reader.git(['diff','--quiet',row['head_sha'],source,'--', *operating_paths()])
    if phase == 'prepare':
        for state in ('queued','in_progress','pending','waiting','requested'):
            active = complete(get('actions/runs?status='+state+'&per_page=100'), 'workflow_runs')
            require(all(r['workflow_id'] not in SHARED_WORKFLOWS or str(r['id']) == env['GITHUB_RUN_ID']
                        for r in active), 'Shared NRCS workflow active')
    fresh = get('git/ref/heads/main')['object']['sha']
    reader.commit(fresh)
    reader.git(['merge-base','--is-ancestor',source,fresh])
    reader.git(['diff','--quiet',source,fresh,'--', *operating_paths(), 'docs/data/snotel-sms'])
    now = clock()
    require((end-now).total_seconds() >= (5400 if phase == 'prepare' else 1500), 'Remaining slot headroom')
    elapsed = now.timestamp() - int(env['SNOTEL_JOB_STARTED'])
    # Leave the owned child its full preflight/acquisition/finalization, plus upload.
    prep_reserve = 300 + LIMITS['total_seconds'] + LIMITS['finalization_seconds'] + 180
    require(0 <= elapsed <= (3600-prep_reserve if phase == 'prepare' else 900-600),
            'Remaining hosted job headroom')
    return dict(source_sha=source, catalog_sha256=policy['catalog_sha256'], roster_sha256=policy['roster_sha256'],
                prior=current, cutoff=str(cutoff), policy_sha256=digest, window_end=end.strftime('%Y-%m-%dT%H:%M:%SZ'))


def consume_term_admission(env, raw, path, now):
    import hashlib
    policy = strict_object(raw, 4096)
    _, _, end, cutoff = slot(policy, env, now)
    require(policy['version'] == TERM_VERSION and not path.is_symlink(), 'Term admission required')
    record = json.loads(path.read_bytes())
    grant = record['grant']
    digest = hashlib.sha256(raw.encode()).hexdigest()
    require(env.get('NRCS_CLEARANCE') == 'schedule/term/'+digest and env.get('PROVIDER_HOLD') == 'false'
            and grant['policy_sha256'] == digest and grant['source_sha'] == env['GITHUB_SHA']
            and record['run'] == env['GITHUB_RUN_ID'] and 0 <= now.timestamp()-record['admitted_at'] <= 60
            and utc(grant['window_end']) == end and str(cutoff) == grant['cutoff']
            and (end-now).total_seconds() >= 5400, 'Expired or changed term admission')
    return grant


def hosted_environment(env):
    """The workflow compares the complete canonical variable without logging it.

    Only an equality boolean and public source/pin/clock fields cross into the
    step environment. GitHub's string comparison rejects extra/duplicate fields,
    altered budgets and old approvals but is case-insensitive. The operation is
    independently restricted to the fixed approval and exact event/source pins;
    this is not a byte-for-byte attestation of the private variable.
    """
    require('SNOTEL_SMS_FIRST_RUN_JSON' not in env and env.get('R9_GRANT_EXACT_MATCH') == 'true', 'Private grant binding')
    grant = dict(version='snotel-first-run-1', approval_id=APPROVAL, source_sha=env.get('GITHUB_SHA'),
        run_number=2, activated_at_utc=env.get('GRANT_ACTIVATED_AT'), window_end=env.get('WINDOW_END'),
        catalog_sha256=env.get('CATALOG_PIN'), roster_sha256=env.get('ROSTER_PIN'), limits=dict(LIMITS))
    return dict(env, SNOTEL_SMS_FIRST_RUN_JSON=json.dumps(grant, sort_keys=True, separators=(',', ':')))


def check_source(env):
    """Actual non-stub checkout, invoked only in the offline qualification step."""
    import soil_network_workflow as w
    reader = w.SnotelGitReader('.')
    require(not reader.test_stub, 'Actual Git reader required')
    source = env['GITHUB_SHA']; pins = reader.source(source)
    groups = w.snotel_catalog(reader, source, env['CATALOG_PIN'], env['ROSTER_PIN'], synthetic=False)
    bootstrap = env.get('SUCCESSOR') != 'true'
    prior, binding = reader.product(source, bootstrap=bootstrap)
    if bootstrap:
        require(prior is None and binding is None, 'First-product complete absence')
    proof = dict(version=1, source_sha=source, run_id=env['GITHUB_RUN_ID'], attempt=env['GITHUB_RUN_ATTEMPT'],
        catalog_sha256=env['CATALOG_PIN'], roster_sha256=env['ROSTER_PIN'],
        checked_at_utc=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        origin=reader.git(['remote','get-url','origin']).decode().strip(),
        source_files=len(pins), stations=len(groups), sensors=sum(len(g['sensor_identities']) for g in groups),
        test_stub=False, product_absent=prior is None)
    if not bootstrap:
        proof['prior'] = binding
    write(Path(env['RUNNER_TEMP'])/'snotel-real-reader-check.json', proof)
    return proof


def require_source_proof(env, grant, current):
    proof = json.loads((Path(env['RUNNER_TEMP'])/'snotel-real-reader-check.json').read_bytes())
    require(proof['source_sha'] == grant['source_sha'] and proof['run_id'] == env['GITHUB_RUN_ID']
        and proof['attempt'] == '1' and proof['test_stub'] is False
        and proof['product_absent'] is ('prior' not in grant)
        and proof.get('prior') == grant.get('prior')
        and proof['catalog_sha256'] == grant['catalog_sha256'] and proof['roster_sha256'] == grant['roster_sha256']
        and proof['stations'] == 32 and proof['sensors'] == 98
        and 0 <= (current-utc(proof['checked_at_utc'])).total_seconds() <= 300, 'Fresh actual source proof')
    return proof


def write(path, value):
    with path.open('x') as f:
        json.dump(value, f, sort_keys=True); f.write('\n'); f.flush(); os.fsync(f.fileno())


def supervise(argv, root, *, preflight_seconds=300, acquisition_seconds=1920,
              finalization_seconds=120, poll=.1):
    """Owned serial child; deadlines include close/persistence, not just HTTP.

    Marker absence cannot extend admission. A killed child's incomplete native
    reservations remain UNKNOWN; this supervisor never restarts a child.
    """
    start = time.monotonic(); phase_start = start; phase = 'preflight'
    process = None; reason = None
    with (root.parent/'prepare.stdout').open('xb') as out, (root.parent/'prepare.stderr').open('xb') as err:
        try:
            process = subprocess.Popen(argv, stdout=out, stderr=err, start_new_session=True)
            while process.poll() is None:
                current = time.monotonic()
                if phase == 'preflight' and (root/'acquisition-started.json').is_file():
                    phase = 'acquisition'; phase_start = current
                if phase == 'acquisition' and (root/'acquisition-completed.json').is_file():
                    phase = 'finalization'; phase_start = current
                limit = {'preflight': preflight_seconds, 'acquisition': acquisition_seconds,
                         'finalization': finalization_seconds}[phase]
                # Poll observation uncertainty is deducted, not added to the ceiling.
                if current-phase_start >= max(0, limit-2*poll):
                    reason = phase+'_deadline'; break
                if out.tell()+err.tell() > 1024**2:
                    reason = 'private_diagnostic_limit'; break
                time.sleep(poll)
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            code = process.wait(timeout=10) if process is not None else None
            out.flush(); err.flush(); os.fsync(out.fileno()); os.fsync(err.fileno())
    receipt = dict(child_exit=code, reason=reason, elapsed_seconds=time.monotonic()-start,
                   phase=phase, restarted=False)
    write(root.parent/'supervision.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('prepare', 'publish', 'check-source', 'admit-term'))
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.phase == 'check-source':
            proof = check_source(os.environ)
            print(json.dumps(dict(status='ACTUAL_CHECKOUT_QUALIFIED', **proof), sort_keys=True)); return 0
        current = datetime.now(timezone.utc)
        successor = os.environ.get('SUCCESSOR') == 'true'
        if successor:
            from soil_network_workflow import SnotelGitReader
            raw = os.environ.pop('SUCCESSOR_POLICY', '')
            token = os.environ.pop('GH_TOKEN', '')
            reader = SnotelGitReader('.')
            term_file = Path(os.environ['RUNNER_TEMP'])/'snotel-scheduled-admission.json'
            if args.phase == 'prepare' and strict_object(raw, 4096).get('version') == TERM_VERSION:
                grant = consume_term_admission(os.environ, raw, term_file, current)
                fresh = get_github('git/ref/heads/main', token)['object']['sha']
                reader.commit(fresh)
                reader.git(['merge-base','--is-ancestor',grant['source_sha'],fresh])
                reader.git(['diff','--quiet',grant['source_sha'],fresh,'--',*operating_paths(),'docs/data/snotel-sms'])
            else:
                phase = 'prepare' if args.phase == 'admit-term' else args.phase
                grant = admit_successor(os.environ, phase, raw, reader,
                                        lambda path: get_github(path, token), lambda: datetime.now(timezone.utc))
                if args.phase == 'admit-term':
                    require(strict_object(raw, 4096)['version'] == TERM_VERSION, 'Scheduled term only')
                    write(term_file, dict(grant=grant, run=os.environ['GITHUB_RUN_ID'],
                                          admitted_at=time.time()))
                    return 0
            current = datetime.now(timezone.utc)
        else:
            grant = authorize(hosted_environment(os.environ), args.phase, current)
        require(sys.platform == 'linux' and sys.version_info >= (3, 12), 'Qualified Linux Python runtime')
        if args.phase == 'publish':
            print('SNOTEL_FINITE_PUBLICATION_ADMISSION_PASS'); return 0
        require_source_proof(os.environ, grant, current)
        temp = Path(os.environ['RUNNER_TEMP']); private = temp/'snotel-private'
        require(not private.exists() and not temp.is_symlink(), 'Fresh private runner root')
        require(os.statvfs(temp).f_bavail*os.statvfs(temp).f_frsize >= 2*1024**3, 'Runner storage headroom')
        require(os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_AVPHYS_PAGES') >= 2*1024**3, 'Runner memory headroom')
        private.mkdir(mode=0o700)
        write(private/'admission.json', dict(grant=grant, run_id=os.environ['GITHUB_RUN_ID'],
            attempt=1, python=platform.python_version(), platform=platform.platform()))
        root = private/'run'
        argv = [sys.executable, '-B', 'scripts/soil_network_workflow.py', 'snotel-prepare',
                '--repo', '.', '--root', str(root), '--source-sha', grant['source_sha'],
                '--prior-commit', grant['source_sha'], '--catalog-sha256', grant['catalog_sha256'],
                '--roster-sha256', grant['roster_sha256'], '--window-end', grant['window_end'],
                '--budget-json', json.dumps(LIMITS), '--acquire', '--publish', '--release-approved']
        if not successor:
            argv.append('--bootstrap')
        receipt = supervise(argv, root)
        result = json.loads((root/'result.json').read_bytes()) if (root/'result.json').is_file() else {}
        summary = {k: result.get(k) for k in ('status', 'candidate_ready', 'synthetic', 'attempts',
            'consumed_body_bytes', 'completed_stations', 'station_call_seconds', 'source_check', 'latest_measurement',
            'failure_phase', 'error_type')}
        summary.update(child_exit=receipt['child_exit'], supervision_reason=receipt['reason'])
        print(json.dumps(summary, sort_keys=True))
        require(receipt['reason'] is None and receipt['child_exit'] == 0 and
                result.get('status') == 'CANDIDATE_READY' and result.get('synthetic') is False
                and result.get('completed_stations') == result.get('attempts') == 32, 'Preparation held')
        transfer = root/'transfer'
        from soil_moisture import snotel_snapshot as s
        import main_publisher as mp
        import snotel_sms_publisher as sp
        files = sp.validate_product(transfer/'candidate', candidate=True)
        if successor:
            product = json.loads(files[s.PATHS[1]])
            require(product['end'] == grant['cutoff'] and product['parent'] ==
                    {k: v for k, v in grant['prior'].items() if k != 'commit'}, 'Successor transfer provenance')
            print(json.dumps(dict(python_version=platform.python_version(), generation=product['generation'],
                                  source_cutoff=product['end'], prior_generation=product['parent']['generation'])))
        allowed = {'candidate/'+n for n in s.PATHS}|{'candidate-metadata.json'}
        require({p.relative_to(transfer).as_posix() for p in transfer.rglob('*') if p.is_file()} == allowed
                and not any(p.is_symlink() for p in transfer.rglob('*')), 'Public transfer closure')
        require(sum(p.stat().st_size for p in transfer.rglob('*') if p.is_file()) <= s.MAX_PRODUCT, 'Complete public transfer size')
        mp.validate_candidate_metadata(root=transfer/'candidate', metadata_path=transfer/'candidate-metadata.json',
            product_id=s.PRODUCT, allowlist=s.PATHS, expected_source_sha=grant['source_sha'])
        with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
            f.write('candidate_ready=true\nsource_sha='+grant['source_sha']+'\n')
        return 0
    except Exception as exc:
        # Never interpolate an exception body, URL, path or provider payload.
        print(json.dumps(dict(status='HELD', stage='finite_host_admission_or_preparation', exception_type=type(exc).__name__)))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
