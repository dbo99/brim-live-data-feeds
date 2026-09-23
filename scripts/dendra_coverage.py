#!/usr/bin/env python3
"""Bounded query planning only; daily arithmetic remains in dendra/core.R.

The runner supplies a fully scientifically validated materialized parent. A plan
is collection work, never an acknowledgement or a semantic validation receipt.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

VERSION = 'dendra-coverage-plan-1'
POLICY = 'dendra-daily-1.0.0-frozen-cadence'
CALENDAR = 'fixed-UTC-08:00-completed-days'
IDENTITY = ('datastream_id', 'station_id', 'parameter', 'depth_cm', 'orientation',
            'native_unit_name', 'unit_normalization', 'source_terms',
            'source_attributes', 'public_level', 'source_is_hidden',
            'source_is_geo_protected')
LIMITS = dict(attempts=80, elapsed_seconds=300, response_bytes=64*1024**2,
              source_rows=1_000_000, new_intervals=16, planned_intervals=4096)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def utc(value):
    require(isinstance(value, str) and value.endswith('Z'), 'Explicit UTC Z time required')
    result = datetime.fromisoformat(value[:-1]+'+00:00')
    require(result.utcoffset() == timedelta(0), 'UTC required')
    return result


def day(value):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value), 'ISO day required')
    return date.fromisoformat(value)


def identity(stream):
    return {key: stream.get(key) for key in IDENTITY}


def identity_hash(stream):
    return digest(identity(stream))


def collector_binding():
    root = Path(__file__).resolve().parent
    return {name: hashlib.sha256((root/name).read_bytes()).hexdigest()
            for name in ('dendra_coverage.py', 'dendra/coverage_collect.py', 'dendra/bridge.py',
                         'dendra/transport.py', 'dendra/core.R')}


def completed_intervals(product, asof):
    """Require the same date/response provenance used by the R verifier.

    Row presence, numeric values and last eligible observation are deliberately
    not used as evidence that a day has been queried.
    """
    snapshot = product['source_snapshot']
    chunks = snapshot['chunks']
    validated = []
    for chunk in chunks:
        bounds = chunk['requested_interval']
        lo, hi = utc(bounds['start_inclusive']), utc(bounds['end_exclusive'])
        retrieved = utc(chunk['retrieval_last_utc'])
        require(lo < hi and lo.hour == hi.hour == 8 and
                lo.minute == hi.minute == lo.second == hi.second == 0,
                'Query boundaries must be complete fixed-offset days')
        require(retrieved <= asof and re.fullmatch('[0-9a-f]{64}', chunk['content_sha256']),
                'Invalid/future query provenance')
        validated.append((lo.date(), hi.date(), chunk))
    require(validated, 'No approved source observation window in parent')
    covered = set()
    for key, query in snapshot['query_by_date'].items():
        current = day(key)
        if query['status'] != 'queried':
            require(query['status'] in ('unqueried', 'before_saved_source_start'),
                    'Unknown query status')
            continue
        matching = [c for lo, hi, c in validated if lo <= current < hi and
                    c['content_sha256'] == query['content_sha256'] and
                    c['retrieval_last_utc'] == query['retrieved_at_utc']]
        require(matching, 'Queried date has no complete response provenance')
        covered.add(current)
    return covered, min(lo for lo, _, _ in validated)


def bounded_limits(overrides=None):
    result = dict(LIMITS)
    for key, value in (overrides or {}).items():
        require(key in result and isinstance(value, int) and not isinstance(value, bool)
                and 0 <= value <= result[key], 'Invalid/increased collection bound')
        result[key] = value
    require(all(result[k] > 0 for k in result if k != 'attempts'), 'Positive work limits required')
    return result


def plan(catalog, products, parent, as_of_utc, *, request_generation=None, limits=None, pending_tasks=()):
    asof = utc(as_of_utc)
    end = (asof-timedelta(hours=8)).date()
    cutoff = end-timedelta(days=1)
    limits = bounded_limits(limits)
    selected = [s['datastream_id'] for s in catalog['streams']]
    require(0 < len(selected) <= 1024 and len(set(selected)) == len(selected), 'Selection bound/duplicate')
    require(set(products) <= set(selected), 'Selection removal requires a separate review')
    if parent is not None:
        require(parent.get('state_role') in ('seed', 'published'), 'Prepared state cannot be a planning parent')
        require(re.fullmatch('[0-9a-f]{64}', parent.get('index_sha256', '')), 'Parent index binding')
        if parent['state_role'] == 'published':
            require(re.fullmatch('[0-9a-f]{40}', parent.get('publication_commit', '')), 'Published receipt commit required')
    windows = catalog.get('observation_windows', {})
    if windows:
        require(windows.get('version') == 'dendra-observation-windows-1', 'Unknown onboarding version')
    approvals = windows.get('streams', [])
    require(len({x['datastream_id'] for x in approvals}) == len(approvals), 'Duplicate onboarding entry')
    sources = collector_binding()
    tasks, summaries, holds = [], [], []
    generation = request_generation or ('parent-'+parent['index_sha256'][:32] if parent else 'initialize-'+digest(catalog.get('observation_windows',{}))[:32])
    require(isinstance(generation, str) and re.fullmatch('[A-Za-z0-9_-]{1,80}', generation), 'Request generation bound')
    for stream in catalog['streams']:
        sid = stream['datastream_id']
        require(re.fullmatch('[0-9a-f]{24}', sid), 'Stream identity')
        require(stream['parameter'] in ('soil_moisture', 'soil_temperature'), 'Unsupported parameter')
        require(stream['public_level'] == 3 and stream['source_is_hidden'] is False, 'Public selected streams only')
        prior = products.get(sid)
        if prior is not None:
            require(identity(prior['stream']) == identity(stream), 'Stream/units identity changed')
            covered, start = completed_intervals(prior, asof)
            authority = 'accepted_parent_complete_source_intervals'
        else:
            approval = next((x for x in approvals if x['datastream_id'] == sid), None)
            if approval is None:
                holds.append(dict(datastream_id=sid, reason='initialization_horizon_not_approved'))
                continue
            require(approval['identity_sha256'] == identity_hash(stream) and
                    approval.get('end_policy') == 'completed_cutoff' and
                    isinstance(approval.get('review_id'), str) and approval['review_id'].strip() and
                    isinstance(approval.get('reason'), str) and approval['reason'].strip(),
                    'Explicit source-bound onboarding approval required')
            start, covered, authority = day(approval['start_date']), set(), 'explicit_reviewed_initialization'
        require(start < end and (end-start).days <= 6000, 'Approved observation window bound')
        original_start = start
        if stream['parameter'] == 'soil_temperature':
            retention = catalog['integration'].get('temperature_days', 90)
            require(isinstance(retention, int) and 1 <= retention <= 3660, 'Explicit temperature retention')
            start = max(start, end-timedelta(days=retention))
        required = {start+timedelta(days=i) for i in range((end-start).days)}
        overlap = {end-timedelta(days=i) for i in range(1, 8)} & required
        gaps = required-covered
        needed = sorted(overlap | gaps)
        maxdays = 90 if stream['parameter'] == 'soil_temperature' else 30
        remaining=set(needed)
        # Completed collection work is distinct from the acknowledged parent. Reuse
        # it within the same parent-bound reconciliation epoch even after midnight.
        for cached in sorted(pending_tasks,key=lambda t:(t['start'],t['end'])):
            b=cached['collection_binding']
            if (b['request_generation']!=generation or b['identity']!=identity(stream) or
                    b['collector_sources']!=sources):continue
            lo,hi=day(cached['start']),day(cached['end'])
            dates={lo+timedelta(days=i) for i in range((hi-lo).days)}
            if dates and dates<=remaining:
                tasks.append(cached);remaining-=dates
        groups = []
        for current in sorted(remaining):
            if not groups or current != groups[-1][-1]+timedelta(days=1) or len(groups[-1]) >= maxdays:
                groups.append([])
            groups[-1].append(current)
        for group in groups:
            lo, hi = group[0].isoformat(), (group[-1]+timedelta(days=1)).isoformat()
            binding = dict(version=VERSION, source='dendra', identity=identity(stream),
                           policy_version=POLICY, calendar=CALENDAR, start=lo, end=hi,
                           request_generation=generation, collector_sources=sources)
            key = digest(binding)
            tasks.append(dict(task_id=key, stream=stream, start=lo, end=hi,
                              collection_binding=binding, checkpoint_key='coverage-'+key))
        summaries.append(dict(datastream_id=sid, parameter=stream['parameter'],
                              window_start=start.isoformat(), window_end_exclusive=end.isoformat(),
                              window_authority=authority, before_retention_days=(start-original_start).days,
                              required_days=len(required), completed_parent_query_days=len(covered & required),
                              unqueried_days=len(gaps), recent_overlap_days=len(overlap),
                              planned_unique_days=len(needed), intervals=sum(t['stream']['datastream_id']==sid for t in tasks)))
    require(len(tasks) <= limits['planned_intervals'], 'Plan interval envelope exceeded; explicit smaller selection needed')
    tasks.sort(key=lambda t:(t['end'],t['start'],t['stream']['datastream_id']))
    result = dict(version=VERSION, policy_version=POLICY, source='dendra', calendar=CALENDAR,
                  as_of_utc=as_of_utc, cutoff=cutoff.isoformat(), end_exclusive=end.isoformat(),
                  catalog=catalog, parent=parent, request_generation=generation, limits=limits,
                  collector_sources=sources, intervals=tasks, stream_coverage=summaries,
                  holds=holds, collection_authorized=not holds,
                  interpretation='Required queries, not proof of observations or publication; incomplete collection holds the whole network.')
    result['plan_sha256'] = digest(result)
    return result


def validate_plan(value):
    require(value.get('version') == VERSION and value.get('policy_version') == POLICY and
            value.get('source') == 'dendra' and value.get('calendar') == CALENDAR, 'Plan contract mismatch')
    body = {k: v for k, v in value.items() if k != 'plan_sha256'}
    require(digest(body) == value.get('plan_sha256'), 'Plan content binding')
    require(value['collector_sources'] == collector_binding(), 'Collection source changed; regenerate plan')
    require(value['limits'] == bounded_limits(value['limits']), 'Plan limits')
    require(value['collection_authorized'] and not value['holds'], 'Plan is held pending initialization approval')
    require(len(value['intervals']) <= value['limits']['planned_intervals'], 'Plan task limit')
    streams = {s['datastream_id']: s for s in value['catalog']['streams']}
    require(len(streams) == len(value['catalog']['streams']), 'Duplicate plan selection')
    require(day(value['end_exclusive']) == (utc(value['as_of_utc'])-timedelta(hours=8)).date(), 'Cutoff binding')
    seen = set()
    for task in value['intervals']:
        binding = task['collection_binding']
        require(task['stream'] == streams.get(task['stream']['datastream_id']), 'Task outside selected catalog')
        require(task['task_id'] == digest(binding) and task['checkpoint_key'] == 'coverage-'+task['task_id'], 'Checkpoint key binding')
        require(binding == dict(version=VERSION, source='dendra', identity=identity(task['stream']),
                                policy_version=POLICY, calendar=CALENDAR, start=task['start'], end=task['end'],
                                request_generation=value['request_generation'], collector_sources=value['collector_sources']), 'Task identity binding')
        maxdays = 90 if task['stream']['parameter'] == 'soil_temperature' else 30
        lo, hi = day(task['start']), day(task['end'])
        require(0 < (hi-lo).days <= maxdays and hi <= day(value['end_exclusive']), 'Task source interval bound')
        for n in range((hi-lo).days):
            key = (task['stream']['datastream_id'], lo+timedelta(days=n))
            require(key not in seen, 'Overlapping duplicate task date')
            seen.add(key)
    return value


def pending_checkpoints(root, catalog, asof):
    # Strictly bounded and fully revalidated: no trust from filenames/sidecars alone.
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parent/'dendra'))
    from coverage_collect import BoundChunks
    streams={s['datastream_id']:s for s in catalog['streams']};out=[];size=0
    root=Path(root)
    for path in sorted((root/'coverage-v1/chunks').glob('*/*.json')):
        size+=path.stat().st_size
        require(len(out)<4096 and size<=256*1024**2, 'Pending checkpoint envelope exceeded')
        require(not path.is_symlink(), 'Pending checkpoint symlink')
        raw=json.loads(path.read_text());b=raw.get('collection_binding',{});sid=raw.get('datastream_id')
        if sid not in streams or b.get('collector_sources')!=collector_binding():continue
        require(b.get('identity')==identity(streams[sid]), 'Pending checkpoint source identity changed')
        task=dict(task_id=digest(b),checkpoint_key='coverage-'+digest(b),stream=streams[sid],start=b['start'],end=b['end'],collection_binding=b)
        require(path.name==task['checkpoint_key']+'.json' and path.parent.name==sid,'Pending checkpoint path binding')
        envelope=BoundChunks(root,task).load(sid,task['checkpoint_key'])
        require(utc(envelope['retrieval_last_utc'])<=utc(asof),'Future pending query')
        out.append(task)
    return out


def main():
    parser = argparse.ArgumentParser()
    for name in ('catalog', 'as-of', 'output'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--prior-view'); parser.add_argument('--parent')
    parser.add_argument('--request-generation'); parser.add_argument('--budget', type=int, default=0)
    parser.add_argument('--limits'); parser.add_argument('--checkpoint-state'); parser.add_argument('--checked-at')
    args = parser.parse_args()
    load = lambda p: json.loads(Path(p).read_text())
    products = {}
    if args.prior_view:
        root = Path(args.prior_view)
        for stream in load(root/'index.json')['streams']:
            sid = stream['datastream_id']
            require(re.fullmatch('[0-9a-f]{24}', sid), 'Prior stream identity')
            products[sid] = load(root/'daily'/(sid+'.json'))
    limits = load(args.limits) if args.limits else {}
    require('attempts' not in limits or limits['attempts'] == args.budget, 'Budget/limits mismatch')
    catalog=load(args.catalog)
    pending=pending_checkpoints(args.checkpoint_state,catalog,args.checked_at or args.as_of) if args.checkpoint_state else []
    value = plan(catalog, products, load(args.parent) if args.parent else None,
                 args.as_of, request_generation=args.request_generation,
                 limits={**limits, 'attempts': args.budget},pending_tasks=pending)
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, sort_keys=True, separators=(',', ':'))+'\n')


if __name__ == '__main__':
    main()
