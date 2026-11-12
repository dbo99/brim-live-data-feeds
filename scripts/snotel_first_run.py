"""Finite first-product host admission and private preparation supervision.

This does not grant authority by itself. The repository variable must bind one
reviewed source event, workflow run number, first attempt and finite UTC window.
Only positive, explicitly selected accounting is printed; raw source and failures
remain in the private runner directory, never an Actions artifact.
"""
import argparse
from datetime import datetime, timezone
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
    prior, binding = reader.product(source, bootstrap=True)
    require(prior is None and binding is None, 'First-product complete absence')
    proof = dict(version=1, source_sha=source, run_id=env['GITHUB_RUN_ID'], attempt=env['GITHUB_RUN_ATTEMPT'],
        catalog_sha256=env['CATALOG_PIN'], roster_sha256=env['ROSTER_PIN'],
        checked_at_utc=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        origin=reader.git(['remote','get-url','origin']).decode().strip(),
        source_files=len(pins), stations=len(groups), sensors=sum(len(g['sensor_identities']) for g in groups),
        test_stub=False, product_absent=True)
    write(Path(env['RUNNER_TEMP'])/'snotel-real-reader-check.json', proof)
    return proof


def require_source_proof(env, grant, current):
    proof = json.loads((Path(env['RUNNER_TEMP'])/'snotel-real-reader-check.json').read_bytes())
    require(proof['source_sha'] == grant['source_sha'] and proof['run_id'] == env['GITHUB_RUN_ID']
        and proof['attempt'] == '1' and proof['test_stub'] is False and proof['product_absent'] is True
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
                '--budget-json', json.dumps(LIMITS), '--acquire', '--publish', '--bootstrap', '--release-approved']
        receipt = supervise(argv, root)
        result = json.loads((root/'result.json').read_bytes()) if (root/'result.json').is_file() else {}
        summary = {k: result.get(k) for k in ('status', 'candidate_ready', 'synthetic', 'attempts',
            'consumed_body_bytes', 'completed_stations', 'station_call_seconds', 'source_check', 'latest_measurement')}
        summary.update(child_exit=receipt['child_exit'], supervision_reason=receipt['reason'])
        print(json.dumps(summary, sort_keys=True))
        require(receipt['reason'] is None and receipt['child_exit'] == 0 and
                result.get('status') == 'CANDIDATE_READY' and result.get('synthetic') is False
                and result.get('completed_stations') == result.get('attempts') == 32, 'Preparation held')
        transfer = root/'transfer'
        from soil_moisture import snotel_snapshot as s
        import main_publisher as mp
        import snotel_sms_publisher as sp
        sp.validate_product(transfer/'candidate', candidate=True)
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
