"""Inactive SNOTEL callbacks for the unchanged shared publisher. No Git calls."""
import json
import os
from pathlib import Path

from soil_moisture import snotel_snapshot as s


def validate_product(root, *, candidate=False):
    root = Path(root)
    if candidate:
        files = set()
        for path in root.rglob('*'):
            s.require(not path.is_symlink(), 'Candidate symlink')
            if path.is_file(): files.add(path.relative_to(root).as_posix())
        s.require(files == set(s.PATHS), 'Candidate exact file closure')
    files = s.read_product(root)
    if candidate: s.validate_routine_candidate(s.h._json(files[s.PATHS[1]]))
    return files


def classify(candidate, current):
    """Caller supplies validated fresh-main bytes, not a build-clock ordering."""
    c = s.validate_product_bytes(candidate[s.PATHS[0]], candidate[s.PATHS[1]])
    if current is None:
        s.validate_successor(None, c)
        s.require(c['parent'] is None, 'Initial publication must be explicitly unseeded')
        return dict(decision='publish', candidate_state='new', reason='explicit_unseeded_product')
    m = s.validate_product_bytes(current[s.PATHS[0]], current[s.PATHS[1]])
    cb, mb = s._binding(candidate[s.PATHS[0]], candidate[s.PATHS[1]]), s._binding(current[s.PATHS[0]], current[s.PATHS[1]])
    if c['generation'] == m['generation']:
        s.require(candidate == current, 'Same generation different bytes')
        return dict(decision='no-op', candidate_state='same', reason='exact_product_bytes')
    if m['parent'] == cb:
        return dict(decision='no-op', candidate_state='stale', reason='verified_immediate_product_ancestor')
    s.require(c['parent'] == mb, 'Product parent conflict or unproven ancestry')
    s.require(c['water_year'] in (m['water_year'], m['water_year'] + 1), 'Publication year conflict')
    s.require(c['water_year'] != m['water_year'] or c['end'] >= m['end'], 'Same-WY coverage regression')
    s.validate_successor(m, c)
    return dict(decision='publish', candidate_state='new', reason='exact_product_parent_successor')


def reconcile(candidate_root, worktree):
    candidate = validate_product(candidate_root, candidate=True)
    worktree = Path(worktree)
    # Absence of the entire product subtree is the only unseeded case.
    subtree = worktree/'docs/data/snotel-sms'
    for path in (worktree, worktree/'docs', worktree/'docs/data', subtree):
        s.require(not path.is_symlink(), 'Publication path symlink')
    current = validate_product(worktree) if subtree.exists() else None
    result = classify(candidate, current)
    if result['decision'] == 'publish':
        # Transaction worktree only. Complete validation precedes any copying;
        # Git publication atomicity belongs to main_publisher, not these writes.
        for name in s.PATHS:
            path = worktree/name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(candidate[name])
    return result


def validate_staged(candidate_root, worktree):
    candidate = validate_product(candidate_root, candidate=True)
    staged = validate_product(worktree)
    s.require(staged == candidate, 'Staged bytes differ from validated candidate')


def verify_acknowledgment(expected_files, committed_files, *, commit_binding):
    """Pure verification of externally read committed bytes, not a Git reader."""
    s.require(committed_files == expected_files, 'Ambiguous acknowledgment: committed product differs')
    s.import_product(committed_files, trusted_binding=commit_binding)
    return dict(status='ACKNOWLEDGED_EXACT_PRODUCT', **commit_binding)


def callback_main(environment=None):
    env = os.environ if environment is None else environment
    s.require(env['BRIM_PUBLISH_PRODUCT_ID'] == s.PRODUCT
              and json.loads(env['BRIM_PUBLISH_FIXED_PATHS']) == list(s.PATHS)
              and json.loads(env['BRIM_PUBLISH_OWNED_ROOTS']) == [], 'Exact publisher ownership required')
    phase = env['BRIM_PUBLISH_PHASE']; candidate = env['BRIM_PUBLISH_CANDIDATE_ROOT']; tree = env['BRIM_PUBLISH_WORKTREE']
    files = validate_product(candidate, candidate=True)
    metadata_path = Path(env['BRIM_PUBLISH_METADATA'])
    s.require(metadata_path.is_file() and not metadata_path.is_symlink() and metadata_path.stat().st_size <= 65536,
              'Publisher metadata file required')
    metadata = s.h._json(metadata_path.read_bytes())
    generation = s.h._json(files[s.PATHS[1]])['generation']
    s.require(metadata.get('product_id') == s.PRODUCT and
              metadata.get('semantic_key') == dict(type='generation', value=generation), 'Publisher semantic generation binding')
    if phase == 'validate-candidate': validate_product(candidate, candidate=True)
    elif phase == 'validate-staged': validate_staged(candidate, tree)
    elif phase == 'reconcile':
        result = reconcile(candidate, tree)
        path = Path(env['BRIM_PUBLISH_RESULT'])
        s.require(not path.is_symlink(), 'Result symlink')
        path.write_bytes(s.h.canonical(result))
    else: raise ValueError('Unsupported publisher phase')
    return 0


if __name__ == '__main__':
    raise SystemExit(callback_main())
