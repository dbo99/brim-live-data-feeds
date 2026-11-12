#!/usr/bin/env python3
"""One approved retained SNOTEL product; Git objects are data, never code."""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

import main_publisher
import snotel_sms_publisher as publisher
from soil_moisture import snotel_snapshot as s

REPOSITORY = 'dbo99/brim-live-data-feeds'
WORKFLOW = '.github/workflows/publish-snotel-retained-r11.yml'
STAGING_SHA = 'd09d1e20d7a6ac23f0581e1a83d788e3135f2ca7'
EXPIRES = datetime(2026, 11, 18, tzinfo=timezone.utc)
ORDERED_ALLOWLIST = s.PATHS
EXPECTED = {
    s.PATHS[0]: (305, '661b759c5c4a6b9913a95f1c586ac3679f7657bbd93b225317a4804ca56334e3'),
    s.PATHS[1]: (638980, '0177a9b1846920124a7b172811b5fd26033e062f1b0aa6f23ac459c995e71434'),
}
GENERATION = 'fca38b343291707f2c0de7ec2e8693b3cf155502fbd1ef783a23c753385b0e89'
CATALOG = 'data/input/snotel_sms_catalog.json'
CATALOG_SHA256 = 'd82973ad46b00df2737d6b902a2dbea3a450210d859bf10c82d80384e405beb8'
ROSTER = 'c85422467f7b49d603cbce3d7ce92e8cd55f01fcdcbb42053373a11f01ab66da'
MAX_OBJECT = 2 * 1024**2
require = s.require


def git(repo, *args):
    p = subprocess.run(['git', '--no-optional-locks', '-C', str(repo), *args],
        env=dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_NO_REPLACE_OBJECTS='1'),
        capture_output=True, timeout=30)
    require(p.returncode == 0 and len(p.stdout) <= MAX_OBJECT, 'Bounded Git read failed')
    return p.stdout


def oid(kind, body):
    return hashlib.sha1(kind.encode() + b' ' + str(len(body)).encode() + b'\0' + body).hexdigest()


def object_body(repo, kind, identity, cap=MAX_OBJECT):
    require(bool(re.fullmatch('[0-9a-f]{40}', identity)), 'Full object identity required')
    require(git(repo, 'cat-file', '-t', identity).strip() == kind.encode(), 'Object type')
    size = int(git(repo, 'cat-file', '-s', identity))
    require(0 <= size <= cap, 'Object size')
    raw = git(repo, 'cat-file', kind, identity)
    require(len(raw) == size and oid(kind, raw) == identity, 'Object identity')
    return raw


def admit(env, checkout_sha, now):
    expected = dict(GITHUB_ACTIONS='true', GITHUB_REPOSITORY=REPOSITORY,
        GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/main',
        GITHUB_REF_TYPE='branch', GITHUB_RUN_NUMBER='1', GITHUB_RUN_ATTEMPT='1',
        GITHUB_WORKFLOW_REF=REPOSITORY+'/'+WORKFLOW+'@refs/heads/main',
        RETAINED_STAGING_SHA=STAGING_SHA)
    require(all(env.get(k) == v for k,v in expected.items()), 'Finite R11 identity')
    event = env.get('GITHUB_SHA', '')
    require(bool(re.fullmatch('[0-9a-f]{40}', event)) and
            event == checkout_sha == env.get('RETAINED_EVENT_SHA'), 'Event source binding')
    require(bool(re.fullmatch('[1-9][0-9]*', env.get('GITHUB_RUN_ID', ''))), 'Run identity')
    # Includes preparation (10 min) plus the complete 15-minute publisher reserve.
    require(now.tzinfo is not None and now + timedelta(minutes=25) <= EXPIRES, 'Publication fit')
    return event


def tree_entries(raw, expected):
    entries = {}
    for row in raw.split(b'\0'):
        if not row: continue
        header, name = row.split(b'\t', 1)
        mode, kind, identity = header.decode('ascii').split()
        path = name.decode('utf-8')
        require(path in expected and path not in entries, 'Exact tree membership')
        require(mode == '100644' and kind == 'blob' and
                bool(re.fullmatch('[0-9a-f]{40}', identity)), 'Regular blob required')
        entries[path] = identity
    require(set(entries) == set(expected), 'Complete tree required')
    return entries


def plain(path):
    for p in (path, *path.parents): require(not p.is_symlink(), 'Linked path')
    require(path.is_file() and stat.S_ISREG(path.stat().st_mode), 'Regular file required')
    return path


def source(repo, event):
    require(repo == Path(__file__).resolve().parent.parent, 'Executing source checkout')
    require(git(repo, 'remote', 'get-url', 'origin').decode().strip() in (
        'https://github.com/'+REPOSITORY, 'https://github.com/'+REPOSITORY+'.git',
        'git@github.com:'+REPOSITORY+'.git'), 'Repository identity')
    object_body(repo, 'commit', event)
    require(git(repo, 'rev-parse', 'HEAD').decode().strip() == event, 'Source HEAD')
    names = [n.decode() for n in git(repo, 'ls-tree', '-rz', '--name-only', event, '--', 'scripts').split(b'\0')
             if n.endswith(b'.py')]
    require({'scripts/snotel_retained_candidate.py','scripts/main_publisher.py',
             'scripts/snotel_sms_publisher.py','scripts/soil_moisture/snotel_snapshot.py'} <= set(names),
            'Complete source dependencies')
    for name in names + [WORKFLOW, CATALOG]:
        entry = tree_entries(git(repo, 'ls-tree', '-rz', event, '--', name), [name])
        raw = object_body(repo, 'blob', entry[name])
        require(plain(repo/name).read_bytes() == raw, 'Effective source changed')
    raw = plain(repo/CATALOG).read_bytes()
    require(len(raw) == 142304 and hashlib.sha256(raw).hexdigest() == CATALOG_SHA256, 'Catalog pin')
    catalog = json.loads(raw)
    require(catalog['version'] == 'snotel-sms-catalog-1' and
        catalog['disclosure'] == 'RELEASE_APPROVED_PUBLIC' and catalog['roster_sha256'] == ROSTER, 'Catalog authority')
    groups = [dict(metadata_body=base64.b64decode(g['metadata_base64'], validate=True),
        metadata_sha256=g['metadata_sha256'], sensor_identities=g['sensor_identities']) for g in catalog['groups']]
    binding = s.roster_binding(groups)
    require(s.h.sha256(s.h.canonical(binding)) == ROSTER, 'Catalog native membership')


def absent(repo, commit):
    object_body(repo, 'commit', commit)
    require(not git(repo, 'ls-tree', '-z', commit, '--', 'docs/data/snotel-sms'), 'Unexpected prior product')


def new_directory(path):
    require(not path.exists() and not path.is_symlink(), 'Destination exists')
    for p in path.parents: require(not p.is_symlink(), 'Linked destination ancestry')
    path.mkdir(mode=0o700, parents=True)


def export_blobs(repo, commit, destination):
    require(commit == STAGING_SHA, 'Wrong staging identity')
    raw = object_body(repo, 'commit', commit)
    headers = raw.split(b'\n\n',1)[0].splitlines()
    require(sum(x.startswith(b'tree ') for x in headers)==1 and
            not any(x.startswith(b'parent ') for x in headers), 'Standalone root commit required')
    entries = tree_entries(git(repo, 'ls-tree', '-rz', commit), EXPECTED)
    bodies = {}
    for name, identity in entries.items():
        body = object_body(repo, 'blob', identity, EXPECTED[name][0])
        require((len(body), hashlib.sha256(body).hexdigest()) == EXPECTED[name], 'Original data pin')
        bodies[name] = body
    new_directory(destination)
    for name, body in bodies.items():
        p = destination/name; p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with p.open('xb') as f: f.write(body)


def verify_candidate(root):
    require(root.is_dir() and not root.is_symlink(), 'Candidate root')
    names = set()
    directories = {str(p) for name in EXPECTED for p in Path(name).parents if str(p) != '.'}
    for path in root.rglob('*'):
        require(not path.is_symlink(), 'Linked member')
        name = path.relative_to(root).as_posix()
        if path.is_dir(): require(name in directories, 'Unexpected directory')
        else:
            require(name in EXPECTED, 'Unexpected file')
            plain(path)
            require(path.stat().st_size == EXPECTED[name][0], 'Candidate length')
            require(hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED[name][1], 'Original candidate hash')
            names.add(name)
    require(names == set(EXPECTED), 'Complete original pair')
    files = publisher.validate_product(root, candidate=True)
    d = s.validate_product_bytes(files[s.PATHS[0]], files[s.PATHS[1]])
    require(d['generation'] == GENERATION and d['parent'] is None and d['roster_sha256'] == ROSTER
        and d['water_year'] == 2027 and d['start'] == '2026-10-01' and d['end'] == '2026-11-11'
        and d['as_of'] == '2026-11-12T20:58:57.264890Z'
        and d['built_at'] == '2026-11-12T20:59:26.890937Z', 'Original generation and clocks')
    require(max(e[3] for e in d['evidence']) == '2026-11-12T20:59:26.871879Z', 'Original source check')
    return d


def prepare(repo, artifact, event, prior):
    absent(repo, event); absent(repo, prior)
    new_directory(artifact)
    export_blobs(repo, STAGING_SHA, artifact/'candidate')
    verify_candidate(artifact/'candidate')
    main_publisher.prepare_metadata(argparse.Namespace(candidate_root=str(artifact/'candidate'),
        output=str(artifact/'candidate-metadata.json'), product_id=s.PRODUCT,
        semantic_key_type='generation', semantic_key=GENERATION,
        source_event_sha=event, allowlist=ORDERED_ALLOWLIST, owned_root=None))
    verify_artifact(artifact, event)


def verify_artifact(artifact, event):
    require(artifact.is_dir() and not artifact.is_symlink() and
        {p.name for p in artifact.iterdir()} == {'candidate','candidate-metadata.json'}, 'Artifact closure')
    verify_candidate(artifact/'candidate')
    meta = plain(artifact/'candidate-metadata.json')
    require(meta.stat().st_size <= 16384, 'Metadata bound')
    main_publisher.validate_candidate_metadata(root=artifact/'candidate', metadata_path=meta,
        product_id=s.PRODUCT, allowlist=ORDERED_ALLOWLIST, expected_source_sha=event)
    value = json.loads(meta.read_bytes())
    require(value['semantic_key'] == dict(type='generation',value=GENERATION), 'Semantic binding')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['admit','prepare','verify-artifact'])
    parser.add_argument('--artifact', type=Path)
    parser.add_argument('--prior-sha')
    args = parser.parse_args(); repo = Path.cwd().resolve()
    event = admit(dict(os.environ), git(repo,'rev-parse','HEAD').decode().strip(), datetime.now(timezone.utc))
    source(repo, event)
    if args.command == 'prepare':
        require(args.artifact is not None and args.prior_sha is not None, 'Preparation inputs')
        prepare(repo, args.artifact.absolute(), event, args.prior_sha)
    elif args.command == 'verify-artifact':
        require(args.artifact is not None, 'Artifact input')
        verify_artifact(args.artifact.absolute(), event)
    print('R11 retained product verified; zero provider requests.')


if __name__ == '__main__':
    try: main()
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError, main_publisher.PublisherError):
        raise SystemExit('R11 retained product refused; no publication authorized.')
