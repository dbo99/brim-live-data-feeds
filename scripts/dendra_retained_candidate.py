#!/usr/bin/env python3
"""One approved retained CDFW generation; Git objects are data, never code."""
from __future__ import annotations
import argparse
import hashlib
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess

import main_publisher
import dendra_sms_publisher as publisher
from dendra_sms_publisher import EXPECTED, GENERATION, PATHS, PRODUCT, require

REPOSITORY = 'dbo99/brim-live-data-feeds'
WORKFLOW = '.github/workflows/publish-dendra-retained-cdfw.yml'
STAGING_SHA = '8f4991bbb816fdd1f5d8cf4df1a0a8e97a96b49b'
EXPIRES = datetime(2026, 11, 24, 20, 10, tzinfo=timezone.utc)
ORDERED_ALLOWLIST = PATHS
MAX_OBJECT = 2 * 1024**2



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
    require(all(env.get(k) == v for k,v in expected.items()), 'Finite retained-release identity')
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
    for name in (WORKFLOW, 'scripts/main_publisher.py', 'scripts/dendra_sms_publisher.py',
                 'scripts/dendra_retained_candidate.py'):
        entry = tree_entries(git(repo, 'ls-tree', '-rz', event, '--', name), [name])
        raw = object_body(repo, 'blob', entry[name])
        require(plain(repo/name).read_bytes() == raw, 'Effective source changed')


def absent(repo, commit):
    object_body(repo, 'commit', commit)
    require(not git(repo, 'ls-tree', '-z', commit, '--', 'docs/data/dendra-sms/current-wy'), 'Unexpected prior product')


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
    require(headers[0] == b'tree 537b160b70161074ed12b9f5a099fb94143204b9', 'Pinned staging tree')
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
    return publisher.validate_product(root, candidate=True)


def prepare(repo, artifact, event, prior):
    absent(repo, event); absent(repo, prior)
    new_directory(artifact)
    export_blobs(repo, STAGING_SHA, artifact/'candidate')
    verify_candidate(artifact/'candidate')
    main_publisher.prepare_metadata(argparse.Namespace(candidate_root=str(artifact/'candidate'),
        output=str(artifact/'candidate-metadata.json'), product_id=PRODUCT,
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
        product_id=PRODUCT, allowlist=ORDERED_ALLOWLIST, expected_source_sha=event)
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
    print('CDFW retained product verified; zero provider requests.')


if __name__ == '__main__':
    try: main()
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError, main_publisher.PublisherError):
        raise SystemExit('CDFW retained product refused; no publication authorized.')
