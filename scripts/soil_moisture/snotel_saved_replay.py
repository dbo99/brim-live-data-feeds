#!/usr/bin/env python3
"""Replay one pinned synthetic fixture; no collection or publication interface.

Only SUMMARY.json and MANIFEST.json are suitable for a hosted test artifact.
The native offline checkpoints stay in isolated temporary storage. Successful
synthetic replay does not authenticate any real station, acquisition or roster.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from collections import Counter

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
from soil_moisture import snotel_history as history
import soil_moisture_operations as operations

FIXTURE = REPO / 'tests/soil_moisture/fixtures/snotel_saved_replay/fixture.json'
FIXTURE_SHA256 = '2b27810b27e72c1f8cbca815ba0871531e4c3f507a7e2420f53cd3f7beb71204'
FIXTURE_VERSION = 'synthetic-snotel-saved-replay-1'
FIXED_CLOCK = '2026-10-04T12:00:00Z'


def load_fixture(path=FIXTURE):
    path = operations.plain(path)
    operations.require(path.is_file() and path.stat().st_size <= 65536, 'Fixture size/type')
    raw = path.read_bytes()
    operations.require(history.sha256(raw) == FIXTURE_SHA256, 'Fixed synthetic fixture pin')
    return json.loads(raw)


def build_replay(fixture):
    """Bind saved native bytes to the existing compact bridge, without transport."""
    operations.require(set(fixture) == {'version', 'evidence_kind', 'fixed_clock',
                       'description', 'bundle', 'request', 'batches', 'expected'}, 'Fixture fields')
    operations.require(fixture['version'] == FIXTURE_VERSION and
                       fixture['evidence_kind'] == 'SYNTHETIC_ONLY_NOT_PROVIDER_ACQUISITION' and
                       fixture['fixed_clock'] == FIXED_CLOCK, 'Synthetic fixture identity/clock')
    request = copy.deepcopy(fixture['request'])
    operations.require(all(c['as_of'] == FIXED_CLOCK for c in request['cutoffs'].values()),
                       'Fixed source cutoff')
    base = copy.deepcopy(fixture['bundle'])
    combined = copy.deepcopy(base)
    operations.require(len(fixture['batches']) == 4, 'Fixed saved batch scope')
    for batch in fixture['batches']:
        groups = [dict(request=g['request'],
                       metadata_body=base64.b64decode(g['metadata_base64'], validate=True),
                       metadata_sha256=g['metadata_sha256']) for g in batch['groups']]
        captures = [dict(body=base64.b64decode(c['body_base64'], validate=True),
                         receipt=c['receipt'], receipt_sha256=c['receipt_sha256'])
                    for c in batch['captures']]
        selected = copy.deepcopy(request)
        selected['selection']['snotel'] = batch['selection']
        bridge = operations.offline_snotel_capture_bridge(
            base, selected, groups, captures, compact=True, **batch['pins'])
        combined = operations.offline_append_snotel_compact(combined, bridge)
    return combined, request


def facts(bundle, reconciliation):
    """Count already validated results; science and admission belong to operations."""
    states = reconciliation['identities']
    return dict(
        events=len(bundle['events']), roster=len(bundle['roster']),
        admitted_observations=sum(len(s['observations']) for s in states.values()),
        genuine_zeros=sum(row['value_native'] == 0 for s in states.values()
                          for value in s['observations'].values() for row in value['rows']),
        attempt_outcomes=dict(Counter(a['outcome'] for s in states.values() for a in s['attempts'])),
        final_outcomes=dict(Counter(s['outcome'] for s in states.values())),
        reference_holds=sum(r['eligibility']['reference'] == 'HELD' for r in bundle['roster']),
        reconciliation_sha256=history.sha256(history.canonical(reconciliation)),
        bundle_sha256=history.sha256(history.canonical(bundle)))


def write_new(path, value):
    raw = history.canonical(value)
    with path.open('xb') as stream:
        os.chmod(stream.fileno(), 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return dict(bytes=len(raw), sha256=history.sha256(raw))


def replay(output):
    """Fresh temporary destination only. Existing or interrupted results are retained."""
    fixture = load_fixture()
    bundle, request = build_replay(fixture)
    output = operations.plain(output)
    temporary = Path(tempfile.gettempdir()).resolve()
    runner = os.environ.get('RUNNER_TEMP')
    roots = [temporary] + ([Path(runner).resolve()] if runner else [])
    operations.require(any(root in output.parents for root in roots), 'Temporary output required')
    operations.require(output.parent.is_dir() and not output.exists(), 'Fresh output required')
    operations.require(not any((p / '.git').exists() for p in (output, *output.parents)),
                       'Output inside repository')
    output.mkdir(mode=0o700)
    inputs = output / 'inputs'
    inputs.mkdir(mode=0o700)
    bundle_pin = write_new(inputs / 'bundle.json', bundle)
    request['bundle'] = dict(path=str(inputs / 'bundle.json'), sha256=bundle_pin['sha256'])
    request['scratch_root'] = str(output)
    results = []
    for name in ('replay-1', 'replay-2'):
        result = operations.offline_run(request, output / name)
        checkpoint = json.loads((output / name / 'CHECKPOINT_RECONCILIATION_DRYRUN.json').read_bytes())
        operations.require(result['provider_requests'] == 0 and
                           result['publication_eligible'] is False and
                           result['pending_events'] == 0, 'Offline replay incomplete')
        results.append(checkpoint['reconciliation'])
    operations.require(results[0] == results[1], 'Repeated replay changed results')
    measured = facts(bundle, results[0])
    operations.require(measured == fixture['expected'], 'Fixed synthetic expectation mismatch')
    summary = dict(version=FIXTURE_VERSION, status='SYNTHETIC_OFFLINE_REPLAY_PASS',
                   synthetic=True, private_source_verified=False, provider_requests=0,
                   publication_eligible=False, fixture_sha256=FIXTURE_SHA256,
                   source_clock=FIXED_CLOCK, deterministic_replays=2,
                   same_reconciliation=True, **measured)
    # Only explicit aggregate fields and hashes leave the native scratch results.
    public = output / 'summary'
    public.mkdir(mode=0o700)
    summary_pin = write_new(public / 'SUMMARY.json', summary)
    sources = {}
    for module in tuple(sys.modules.values()):
        source = getattr(module, '__file__', None)
        if source:
            path = Path(source).resolve()
            if path.is_file() and path.suffix == '.py' and REPO / 'scripts' in path.parents:
                sources[str(path.relative_to(REPO))] = history.sha256(path.read_bytes())
    write_new(public / 'MANIFEST.json', dict(version=FIXTURE_VERSION, synthetic=True,
              files={'SUMMARY.json': summary_pin}, source_sha256=sources))
    return summary


def deny_external_effects(event, args):
    """CLI defense in depth: no sockets, child commands or executable replacement."""
    if event.startswith(('socket.', 'os.exec', 'os.spawn')) or event in {
            'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.fork', 'os.forkpty'}:
        raise RuntimeError('Offline external effect denied')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='Fresh directory in temporary storage')
    args = parser.parse_args(argv)
    sys.addaudithook(deny_external_effects)
    try:
        summary = replay(args.output)
    except Exception:
        # Never print input values, source identities, private paths or tracebacks.
        print('SYNTHETIC_OFFLINE_REPLAY_FAILED; existing inputs/results retained', file=sys.stderr)
        return 2
    print(json.dumps(summary, sort_keys=True, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
