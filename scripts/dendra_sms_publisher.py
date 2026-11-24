"""Validate and reconcile the finite CDFW dated-point release."""
import hashlib
import json
import os
from pathlib import Path

PRODUCT = 'dendra-brim-current-wy-1'
ROOT = Path('docs/data/dendra-sms/current-wy')
GENERATION = '19a2b8741a1c5e81b5c2369145ef5e8753da722487b5441227e0e4f86457fb06'
DAYS = ['2026-11-16', '2026-11-17']
# Approved path, length and SHA-256. Insertion order is the publisher allowlist.
EXPECTED = {
    'docs/data/dendra-sms/current-wy/current.json': (166, '201e312ae5ffde3adf51254368738884ca55c796978299dabb5204b1b40a177d'),
    'docs/data/dendra-sms/current-wy/manifest-19a2b8741a1c5e81b5c2369145ef5e8753da722487b5441227e0e4f86457fb06.json': (5550, '19a2b8741a1c5e81b5c2369145ef5e8753da722487b5441227e0e4f86457fb06'),
    'docs/data/dendra-sms/current-wy/streams/0b2a5408917d6ec04857f31404634cbb3ba76c480ac5657eaca69f75fe653bc3.json': (19761, '0b2a5408917d6ec04857f31404634cbb3ba76c480ac5657eaca69f75fe653bc3'),
    'docs/data/dendra-sms/current-wy/streams/1845f7dc0e130ec3ef51f14dfa42a75459b315ab1dc5c0ccd8757a5ee7af620e.json': (19761, '1845f7dc0e130ec3ef51f14dfa42a75459b315ab1dc5c0ccd8757a5ee7af620e'),
    'docs/data/dendra-sms/current-wy/streams/33cb9f58504db9f961dde810abd04a61d36c3191944fb529f11d177a0257beb1.json': (19769, '33cb9f58504db9f961dde810abd04a61d36c3191944fb529f11d177a0257beb1'),
    'docs/data/dendra-sms/current-wy/streams/3a216d04721cee251a9bd19b825018cba65101c4b4fda5120b4f25e4f30bcfef.json': (19757, '3a216d04721cee251a9bd19b825018cba65101c4b4fda5120b4f25e4f30bcfef'),
    'docs/data/dendra-sms/current-wy/streams/47303a3488d6f2eb6c3f237025950dabb751bd37ebab7cba8cf3c053255db41d.json': (19765, '47303a3488d6f2eb6c3f237025950dabb751bd37ebab7cba8cf3c053255db41d'),
    'docs/data/dendra-sms/current-wy/streams/6139f3e8b811e85a28bdce50d54507d5a415d5be96e00fa6b0b91a1d96b01068.json': (19769, '6139f3e8b811e85a28bdce50d54507d5a415d5be96e00fa6b0b91a1d96b01068'),
    'docs/data/dendra-sms/current-wy/streams/7673197be38f52e9545a5d755b44cd6502c439a13713f7e079d510f2d3b37c1f.json': (19769, '7673197be38f52e9545a5d755b44cd6502c439a13713f7e079d510f2d3b37c1f'),
    'docs/data/dendra-sms/current-wy/streams/8d724133fa839d95720a887895f0ea07912d0d7e28ae28773b51740556dc8a36.json': (19765, '8d724133fa839d95720a887895f0ea07912d0d7e28ae28773b51740556dc8a36'),
    'docs/data/dendra-sms/current-wy/streams/bb5e0180306c3a8d09ceb029716282e2320dbae5639aa245282aa2e11d97855a.json': (19745, 'bb5e0180306c3a8d09ceb029716282e2320dbae5639aa245282aa2e11d97855a'),
    'docs/data/dendra-sms/current-wy/streams/be045f41772ac8708cbd24d0c38148733d57517c4403aa3345c5eee880f2be08.json': (19773, 'be045f41772ac8708cbd24d0c38148733d57517c4403aa3345c5eee880f2be08'),
    'docs/data/dendra-sms/current-wy/streams/bebd49af524fb345249f52dea113942018340e82413a894535ec66498c2d4d19.json': (19761, 'bebd49af524fb345249f52dea113942018340e82413a894535ec66498c2d4d19'),
    'docs/data/dendra-sms/current-wy/streams/c6cb7d39035b85cee84b6232923d9677e89bb539a762cae1e3984555d5990d43.json': (19761, 'c6cb7d39035b85cee84b6232923d9677e89bb539a762cae1e3984555d5990d43'),
    'docs/data/dendra-sms/current-wy/streams/cf67f5913d3df6cc2372f7e69e06e376c289a4979d62dae3a057f226de89aaf4.json': (19761, 'cf67f5913d3df6cc2372f7e69e06e376c289a4979d62dae3a057f226de89aaf4'),
    'docs/data/dendra-sms/current-wy/streams/f4a945975a62d16481f136713bdad5d2ae6272eb9e46a1c7911c45ec9fc41760.json': (19765, 'f4a945975a62d16481f136713bdad5d2ae6272eb9e46a1c7911c45ec9fc41760'),
    'docs/data/dendra-sms/current-wy/streams/faeec75bb23d2c93e0a00dbe5712b0b97c9e9f1ede3321649e29851ff4be6fa4.json': (19773, 'faeec75bb23d2c93e0a00dbe5712b0b97c9e9f1ede3321649e29851ff4be6fa4'),
}
PATHS = tuple(EXPECTED)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_ancestry(root):
    for path in (root, *root.parents):
        require(not path.is_symlink(), 'Linked product path')


def validate_science(files):
    pointer = json.loads(files[str(ROOT/'current.json')])
    require(pointer == {'sha256': GENERATION, 'path': f'manifest-{GENERATION}.json'}, 'Pointer identity')
    manifest = json.loads(files[str(ROOT/pointer['path'])])
    require(manifest['version'] == PRODUCT and manifest['publication_eligible'] is False and
            manifest['plot_mode'] == 'INDIVIDUAL_DATED_POINTS' and manifest['deployment_continuity'] == 'UNVERIFIED',
            'Generation semantics')
    registry = manifest['family_registry']
    require(len(registry) == 9 and [f['source_family'] for f in registry if f['selector_enabled']] == ['CDFW'],
            'Family availability')
    shards = manifest['files']
    require(len(shards) == 15 and set(files) == {str(ROOT/n) for n in (*shards, 'current.json', pointer['path'])},
            'Manifest inventory')
    stations, streams = set(), set()
    for name, pin in shards.items():
        path = str(ROOT/name)
        require(EXPECTED[path] == (pin['bytes'], pin['sha256']) and name == f"streams/{pin['sha256']}.json",
                'Shard binding')
        stream = json.loads(files[path])
        identity = stream['identity']
        require(stream['family'] == 'CDFW' and stream['current'] is None and
                identity['stream_id'] == stream['source_series_id'] and identity['depth_cm'] is not None and
                identity['orientation'] and identity['native_unit'] == 'Percent', 'Stream identity')
        streams.add(identity['stream_id'])
        stations.add(identity['station_id'])
        admitted = []
        for row in stream['records']:
            sample = row['sample']
            require(row['current_eligible'] is False, 'Current eligibility')
            if sample is None:
                require(row['value_status'] != 'ADMITTED_DATED_POINT', 'Empty display point')
                continue
            day = row['source_day']
            require(row['value_status'] == 'ADMITTED_DATED_POINT' and sample['source_day'] == day and
                    sample['source_timestamp_utc'] == day+'T08:00:00.000Z' and
                    sample['source_identity_at_collection'] == identity and sample['statistic'] == 'Average' and
                    sample['support_interval'] == sample['timestamp_position'] == 'UNKNOWN' and
                    sample['unit_multiplier'] == 1 and sample['value_native'] == sample['value_vwc_percent'] and
                    sample['scientific_acceptance'] == 'CANDIDATE_NOT_ADMITTED' and
                    sample['publication_eligible'] is False and sample['quality']['quarantined'] is False and
                    sample['percentile_rank'] is None and sample['historical_daily_mean_equivalent'] is False,
                    'Dated native observation')
            admitted.append(day)
        require(admitted == DAYS, 'Exact reviewed dates')
    require(len(streams) == 15 and len(stations) == 4 and stations == set(manifest['locator']), 'Locator scope')


def validate_product(root, *, candidate=False):
    root = Path(root)
    subtree = root/ROOT
    check_ancestry(subtree)
    dirs = {p for n in PATHS for p in (root/n).parents}
    files = {}
    for path in (root if candidate else subtree).rglob('*'):
        require(not path.is_symlink(), 'Linked member')
        if path.is_dir():
            require(path in dirs, 'Unexpected directory')
            continue
        name = path.relative_to(root).as_posix()
        require(name in EXPECTED and path.is_file() and path.stat().st_size == EXPECTED[name][0], 'Unexpected file')
        data = path.read_bytes()
        require(hashlib.sha256(data).hexdigest() == EXPECTED[name][1], 'Approved bytes')
        files[name] = data
    require(set(files) == set(PATHS), 'Incomplete product')
    validate_science(files)
    return files


def reconcile(candidate_root, worktree):
    files = validate_product(candidate_root, candidate=True)
    root = Path(worktree)
    check_ancestry(root/ROOT)
    if (root/ROOT).exists():
        require(validate_product(root) == files, 'Existing product differs')
        return dict(decision='no-op', candidate_state='same', reason='exact_product_bytes')
    for name in PATHS:
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as out:
            out.write(files[name])
    return dict(decision='publish', candidate_state='new', reason='approved_initial_generation')


def validate_staged(candidate_root, worktree):
    require(validate_product(candidate_root, candidate=True) == validate_product(worktree), 'Staged product differs')


def callback_main():
    env = os.environ
    require(env['BRIM_PUBLISH_PRODUCT_ID'] == PRODUCT and json.loads(env['BRIM_PUBLISH_FIXED_PATHS']) == list(PATHS)
            and json.loads(env['BRIM_PUBLISH_OWNED_ROOTS']) == [], 'Publisher ownership')
    meta = json.loads(Path(env['BRIM_PUBLISH_METADATA']).read_bytes())
    require(meta['product_id'] == PRODUCT and meta['semantic_key'] == dict(type='generation', value=GENERATION),
            'Publisher generation')
    phase = env['BRIM_PUBLISH_PHASE']
    candidate, tree = env['BRIM_PUBLISH_CANDIDATE_ROOT'], env['BRIM_PUBLISH_WORKTREE']
    if phase == 'validate-candidate':
        validate_product(candidate, candidate=True)
    elif phase == 'reconcile':
        result = reconcile(candidate, tree)
        path = Path(env['BRIM_PUBLISH_RESULT'])
        require(not path.is_symlink(), 'Linked result')
        path.write_text(json.dumps(result))
    elif phase == 'validate-staged':
        validate_staged(candidate, tree)
    else:
        raise ValueError('Unsupported publisher phase')


if __name__ == '__main__':
    callback_main()
