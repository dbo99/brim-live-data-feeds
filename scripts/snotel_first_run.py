"""Finite bootstrap/successor admission and private preparation supervision.

This does not grant authority by itself. The repository variable must bind one
reviewed source event, workflow run number, first attempt and finite UTC window.
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
    require(isinstance(policy, dict) and set(policy) == fields and
            policy['version'] == 'snotel-successor-1', 'Successor policy')
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
    for key in ('first_run', 'predecessor_run_id', 'slots'):
        require(type(policy[key]) is int and policy[key] > 0, 'Finite run binding')
    require(policy['first_run'] > 2 and 1 <= policy['slots'] <= (7 if event == 'schedule' else 1),
            'Spent or unbounded run')
    start = utc(policy['first_start_utc'])
    if event == 'schedule':
        require(start.strftime('%H:%M:%S') == '18:11:00' and env.get('SCHEDULE') == DAILY_CRON,
                'Scheduled slot')
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


def operating_paths():
    import soil_network_workflow as w
    # Bind the workflow, its executable dependencies and its hosted qualification.
    # A SCAN policy-only change is independent of these entry points.
    return [WORKFLOW, w.SNOTEL_CATALOG, *w.SNOTEL_SOURCES, 'scripts/snotel_first_run.py',
             'scripts/dendra_candidate.py', 'scripts/scan_soil_moisture_publisher.py',
             ':(glob)scripts/**/__init__.py', ':(glob)tests/soil_moisture/test_snotel*.py']


def check_product_lineage(reader, policy, source, index, cutoff):
    import soil_network_workflow as w
    anchor = policy['predecessor_commit']
    reader.git(['merge-base', '--is-ancestor', anchor, source])
    reader.git(['merge-base', '--is-ancestor', policy['reviewed_head'], source])
    reader.git(['diff', '--quiet', policy['reviewed_head'], source, '--', *operating_paths()])
    commits = reader.git(['log', '--first-parent', '--format=%H', '--max-count=8',
                          anchor+'..'+source, '--', *w.ss.PATHS]).decode().splitlines()
    require(len(commits) == index, 'Unexpected predecessor product history')
    files, binding = reader.product(anchor)
    require(binding['generation'] == policy['predecessor_generation'], 'Approved predecessor generation')
    for n, commit in enumerate(reversed(commits)):
        child, _ = reader.product(commit)
        require(w.sp.classify(child, files)['decision'] == 'publish' and
                json.loads(child[w.ss.PATHS[1]])['end'] == str(cutoff-timedelta(days=index-n)),
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
    current = check_product_lineage(reader, policy, source, index, cutoff)
    # A fresh scoped clearance binds this exact policy, run, and observed prior.
    # The owner installs it only after confirming shared capacity and backoff.
    digest = hashlib.sha256(raw.encode()).hexdigest()
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
    reader.git(['diff','--quiet',source,fresh,'--','scripts', WORKFLOW, 'tests/soil_moisture',
                'data/input/snotel_sms_catalog.json', 'docs/data/snotel-sms'])
    now = clock()
    require((end-now).total_seconds() >= (5400 if phase == 'prepare' else 1500), 'Remaining slot headroom')
    elapsed = now.timestamp() - int(env['SNOTEL_JOB_STARTED'])
    # Leave the owned child its full preflight/acquisition/finalization, plus upload.
    prep_reserve = 300 + LIMITS['total_seconds'] + LIMITS['finalization_seconds'] + 180
    require(0 <= elapsed <= (3600-prep_reserve if phase == 'prepare' else 900-600),
            'Remaining hosted job headroom')
    return dict(source_sha=source, catalog_sha256=policy['catalog_sha256'], roster_sha256=policy['roster_sha256'],
                prior=current, cutoff=str(cutoff), policy_sha256=digest, window_end=end.strftime('%Y-%m-%dT%H:%M:%SZ'))


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
    parser.add_argument('phase', choices=('prepare', 'publish', 'check-source'))
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
            grant = admit_successor(os.environ, args.phase, raw, SnotelGitReader('.'),
                                    lambda path: get_github(path, token), lambda: datetime.now(timezone.utc))
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
