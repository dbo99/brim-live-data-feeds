"""Private fixture preview and read-back validator against external pinned oracles.

Validation does not call the sampler. Hashes alone are insufficient: selected
records, scientific fields, coverage and durable receipts must also agree.
"""
from copy import deepcopy
from datetime import datetime, timezone

from .live_state import durable, read
from .transport import parse_utc
from .history_acquisition.safety import encode, decode, sha, require

VERSION = 'dendra-private-fixture-preview-1'
SCIENCE = ('method', 'aggregate_label', 'identity', 'metadata', 'window', 'quality',
           'query_complete', 'source_records', 'source_timestamp_utc', 'source_date_pst_fixed',
           'selection_offset_seconds', 'status', 'value_native', 'value_status', 'value_vwc_percent')


def coverage(cells):
    return dict(representatives=len({k.split('/')[0] for k in cells}),
                target_dates=sorted({k.split('/')[1] for k in cells}),
                queried=len(cells), available=sum(c['current']['status'] == 'AVAILABLE' for c in cells.values()),
                other_dates='NOT_QUERIED', other_streams='NOT_ADMITTED')


def validate(root, manifest_sha256, binding, oracles):
    """Caller supplies independently authenticated binding/oracles and manifest pin."""
    manifest_body = read(root/'MANIFEST.json')
    require(sha(manifest_body) == manifest_sha256, 'Manifest pin mismatch')
    m = decode(manifest_body)
    require(m['version'] == VERSION and m['visibility'] == 'NOT_PUBLIC' and m['status'] == 'PARTIAL',
            'Private partial boundary')
    require(m['binding'] == binding and m['fixture_based'] is True and m['publication_eligible'] is False,
            'Fixture lineage mismatch')
    values = {}
    for role in ('product', 'state'):
        ref = m['artifacts'][role]
        require(ref['path'] == 'products/'+role+'-'+ref['sha256']+'.json', 'Artifact path')
        body = read(root/ref['path'])
        require(len(body) == ref['bytes'] and sha(body) == ref['sha256'], 'Artifact integrity')
        values[role] = decode(body)
    p, state = values['product'], values['state']
    require(p['version'] == VERSION and p['visibility'] == 'NOT_PUBLIC' and p['status'] == 'PARTIAL' and
            p['fixture_based'] is True and p['publication_eligible'] is False and p['provider_requests'] == 0,
            'Product boundary')
    require(p['binding'] == binding == state['binding'] and p['cells'] == state['cells'], 'State coherence')
    require(state['version'] == 'dendra-offline-state-1' and len(state['revisions']) == 4, 'Finite lineage')
    cells, attempts = p['cells'], state['attempts']
    require(set(cells) == set(oracles) == set(binding['bodies']) and len(cells) == 4, 'Exact roster/tasks')
    require(p['coverage'] == coverage(cells) == m['coverage'] and
            p['coverage']['representatives'] == 2 and len(p['coverage']['target_dates']) == 2, 'Coverage')
    require(p['water_year'] == 2027 and p['as_of_source_cutoff'] == binding['as_of_source_cutoff'], 'Source cutoff')
    require(parse_utc(p['built_at_utc']) >= parse_utc(binding['as_of_source_cutoff']), 'Build clock')
    require(p['unverified_scope'] == ['UC Nature Research', 'Pepperwood', 'all other representatives'],
            'Unsupported family admission')
    require(len(attempts) == 4 and p['attempt_count'] == 4 and m['body_bytes'] == sum(a['bytes'] for a in attempts),
            'Attempt accounting')
    by_task = {}
    for i, a in enumerate(attempts):
        require(a['ordinal'] == i and a['task'] not in by_task and a['status'] == 'RECEIVED' and
                a['provider_request'] is False and a['clock_kind'] == 'OFFLINE_REPLAY', 'Attempt receipt')
        require(a['body_sha256'] == binding['bodies'][a['task']], 'Body binding')
        body = read(root/'objects'/a['body_sha256'])
        require(sha(body) == a['body_sha256'] and len(body) == a['bytes'] <= a['cap'], 'Body integrity')
        rows = decode(body)['data']
        require(len(rows) == a['rows'] and rows, 'Populated authentic representative required')
        by_task[a['task']] = (a, rows)
    for key, cell in cells.items():
        reference, current = oracles[key], cell['current']
        a, rows = by_task[key]
        require(all(current[k] == reference[k] for k in SCIENCE), 'Independent scientific oracle mismatch')
        require(current['version'] == 'dendra-offline-sample-stage-a-1' and current['status'] == 'AVAILABLE' and
                current['normalized_eligible'] is True and current['aggregate'] == 'Average' and
                current['support_interval'] == current['timestamp_position'] == 'UNKNOWN', 'Sample semantics')
        require(current['identity_attributes'] == dict(sensor_id=None, deployment_id=None, status='UNVERIFIED'),
                'Invented identity')
        evidence = current['evidence']
        require(evidence['original'] == reference['evidence'] and evidence['kind'] == 'OFFLINE_INJECTED_PAGES' and
                evidence['replay_at_utc'] == a['attempted_at'] and evidence['fixture_source'] == binding['fixture_source'],
                'Source/replay clock binding')
        require(evidence['pages'] == [dict(ordinal=a['ordinal'], sha256=a['body_sha256'], bytes=a['bytes'], url=a['query'])],
                'Page receipts')
        win = reference['window']
        stamps = [parse_utc(r['t']) for r in rows]
        require(all(parse_utc(win['start']) <= t < parse_utc(win['end']) for t in stamps) and stamps == sorted(stamps),
                'Native window/order')
        chosen = min(stamps, key=lambda t: (abs((t-parse_utc(win['target_timestamp_utc'])).total_seconds()), t))
        require(current['source_records'] == [r for r in rows if parse_utc(r['t']) == chosen], 'Native record parity')
        require(cell['last_accepted'] == current and cell['latest_attempt'] ==
                dict(clock=a['attempted_at'], clock_kind='OFFLINE_REPLAY', status='AVAILABLE'), 'Admission freshness')
        require(state['revisions'][a['ordinal']] == dict(key=key, clock=a['attempted_at'], status='AVAILABLE', result=current),
                'Revision accounting')
    return dict(manifest_sha256=manifest_sha256, verified_cells=len(cells), provider_requests=0,
                body_bytes=m['body_bytes'], status='NOT_PUBLIC_PARTIAL_FIXTURE_VERIFIED')


def publish_private(state, oracles, *, pointer_fault=lambda: None):
    """Validate immutable candidate set before atomically admitting its manifest."""
    p = dict(version=VERSION, visibility='NOT_PUBLIC', status='PARTIAL', fixture_based=True,
             publication_eligible=False, provider_requests=0, binding=deepcopy(state.binding),
             water_year=2027, as_of_source_cutoff=state.binding['as_of_source_cutoff'],
             built_at_utc=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
             cells=deepcopy(state.data['cells']), coverage=coverage(state.data['cells']),
             attempt_count=len(state.data['attempts']),
             unverified_scope=['UC Nature Research', 'Pepperwood', 'all other representatives'])
    refs = {}
    for role, value in (('product', p), ('state', state.data)):
        body = encode(value)
        name = 'products/'+role+'-'+sha(body)+'.json'
        durable(state.root/name, body)
        refs[role] = dict(path=name, sha256=sha(body), bytes=len(body))
    manifest = dict(version=VERSION, visibility='NOT_PUBLIC', status='PARTIAL', fixture_based=True,
                    publication_eligible=False, binding=state.binding, coverage=p['coverage'], artifacts=refs,
                    body_bytes=sum(a['bytes'] for a in state.data['attempts']))
    body = encode(manifest)
    durable(state.root/'MANIFEST.json', body)
    result = validate(state.root, sha(body), state.binding, oracles)
    durable(state.root/'accepted.json', encode(dict(manifest_sha256=sha(body))), replace=True, fault=pointer_fault)
    require(decode(read(state.root/'accepted.json')) == dict(manifest_sha256=sha(body)), 'Admission pointer')
    return result


def verify_readiness(root, pin, plan, oracles):
    """Stage B readback: external plan/oracle, all receipts and original bodies."""
    from .history_acquisition.safety import digest
    from .live_metadata import admit_all
    from types import SimpleNamespace
    body = read(root/'MANIFEST.json')
    require(sha(body) == pin, 'Readiness manifest pin')
    m = decode(body)
    require(m['version'] == 'dendra-offline-readiness-1' and m['visibility'] == 'NOT_PUBLIC' and
            m['status'] == 'PARTIAL' and m['fixture_based'] is True and m['provider_requests'] == 0 and
            m['plan_sha256'] == digest(plan), 'Readiness boundary')
    data = {}
    for role in ('state','product'):
        ref = m['artifacts'][role]
        require(ref['path'] == 'products/'+role+'-'+ref['sha256']+'.json','Readiness artifact path')
        b = read(root/ref['path'])
        require(sha(b) == ref['sha256'] and len(b) == ref['bytes'],'Readiness artifact hash')
        data[role] = decode(b)
    p, s = data['product'], data['state']
    require(s['binding']['plan_sha256'] == digest(plan) and s['provider']['closed'] is True and
            s['provider']['live'] is False and len(s['attempts']) == 7, 'Finite completed dry run')
    require(p['version'] == m['version'] and p['visibility'] == 'NOT_PUBLIC' and p['status'] == 'PARTIAL' and
            p['fixture_based'] is True and p['provider_requests'] == 0 and p['cells'] == s['cells'] and
            p['publication_eligible'] is False and p['plan_sha256'] == digest(plan), 'Readiness product coherence')
    require(set(s['cells']) == {sid+'/'+plan['target_day'] for sid in plan['roster']} and
            len(s['revisions']) == 2, 'Exact two target days')
    require(p['metadata_representation'] == 'SYNTHETIC_FROM_RETAINED_PROJECTIONS' and
            p['observation_representation'] == 'AUTHENTIC_R1_SAVED_NATIVE_RESPONSES', 'Dry-run provenance')
    for i,a in enumerate(s['attempts']):
        b = read(root/'objects'/a['body_sha256'])
        require(a['ordinal'] == i and a['status'] == 'RECEIVED' and a['provider_request'] is False and
                a['clock_kind'] == 'OFFLINE_DRY_RUN' and a['details']['complete'] is True and
                sha(b) == a['body_sha256'] and len(b) == a['bytes'] <= a['cap'], 'Complete body/charge closure')
        require(a['context']['plan_sha256'] == digest(plan) and
                parse_utc(a['attempted_at']) <= parse_utc(a['details']['retrieved_at']) <= parse_utc(a['context']['deadline_utc']),
                'Request clock/deadline')
        if i:
            require((parse_utc(a['attempted_at'])-parse_utc(s['attempts'][i-1]['attempted_at'])).total_seconds() >= 1,
                    'Request spacing')
    at = s['attempts'][-1]['details']['retrieved_at']
    metadata = admit_all(SimpleNamespace(root=root,binding=s['binding'],data=s),plan,at)
    for key,cell in s['cells'].items():
        sid = key.split('/')[0];cur = cell['current'];ref = oracles[key]
        for field in ('identity','method','aggregate_label','window','source_records','source_timestamp_utc',
                      'source_date_pst_fixed','selection_offset_seconds','value_native','value_status','quality','query_complete'):
            require(cur[field] == ref[field], 'Readiness original scientific oracle')
        require(cur['metadata'] == metadata[sid]['metadata'] and cur['admission'] == metadata[sid], 'Fresh metadata binding')
        require(cur['status'] == 'HELD' and cur['value_vwc_percent'] is None and
                cur['normalized_eligible'] is False and cell['last_accepted'] is None, 'Unknown precision stays native-only')
        evidence = cur['evidence']
        obs = [a for a in s['attempts'] if a['context']['kind'] == 'observations' and a['context']['stream_id'] == sid]
        require(len(obs) == 1 and evidence['pages'] == obs and evidence['original'] == ref['evidence'] and
                evidence['kind'] == 'OFFLINE_DRY_RUN', 'Observation receipt/provenance')
        rows = decode(read(root/'objects'/obs[0]['body_sha256']))['data']
        stamps = [parse_utc(r['t']) for r in rows];w=cur['window']
        require(stamps == sorted(stamps) and all(parse_utc(w['start']) <= t < parse_utc(w['end']) for t in stamps), 'Native window')
        chosen=min(stamps,key=lambda t:(abs((t-parse_utc(w['target_timestamp_utc'])).total_seconds()),t))
        require(cur['source_records'] == [r for r in rows if parse_utc(r['t']) == chosen], 'Native selection')
        require(cell['latest_attempt']['status'] == 'HELD' and len([r for r in s['revisions'] if r['key']==key and
                r['result']==cur and r['status']=='HELD']) == 1, 'Held query history')
    require(p['coverage'] == dict(representatives=2,complete_queries=2,native_only_held=2,available=0,
            other_dates='NOT_QUERIED',other_streams='NOT_ADMITTED'), 'Readiness coverage')
    require(m['body_bytes'] == sum(a['bytes'] for a in s['attempts']) <= plan['limits']['aggregate_bytes'], 'Body total')
    return dict(status='STAGE_B_PRIVATE_DRY_RUN_VERIFIED',manifest_sha256=pin,attempts=7,
                native_only_held=2,provider_requests=0,body_bytes=m['body_bytes'])


def publish_readiness(state, plan, oracles):
    from .history_acquisition.safety import digest
    state.data['provider']['closed']=True;state.commit()
    product = dict(version='dendra-offline-readiness-1',visibility='NOT_PUBLIC',status='PARTIAL',
        fixture_based=True,provider_requests=0,publication_eligible=False,plan_sha256=digest(plan),
        built_at_utc=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),cells=deepcopy(state.data['cells']),
        metadata_representation='SYNTHETIC_FROM_RETAINED_PROJECTIONS',
        observation_representation='AUTHENTIC_R1_SAVED_NATIVE_RESPONSES',
        coverage=dict(representatives=2,complete_queries=2,native_only_held=2,available=0,
                      other_dates='NOT_QUERIED',other_streams='NOT_ADMITTED'))
    refs={}
    for role,value in (('state',state.data),('product',product)):
        body=encode(value);name='products/'+role+'-'+sha(body)+'.json';durable(state.root/name,body)
        refs[role]=dict(path=name,sha256=sha(body),bytes=len(body))
    m=dict(version=product['version'],visibility='NOT_PUBLIC',status='PARTIAL',fixture_based=True,provider_requests=0,
           plan_sha256=digest(plan),artifacts=refs,body_bytes=sum(a['bytes'] for a in state.data['attempts']))
    body=encode(m);durable(state.root/'MANIFEST.json',body)
    result=verify_readiness(state.root,sha(body),plan,oracles)
    durable(state.root/'accepted.json',encode(dict(manifest_sha256=sha(body))),replace=True)
    return result


V2_VERSION = 'dendra-native-sample-private-candidate-2'
PILOT_PIN = '2045a3cf7896c149fb6527fe130c4c28fee25ec8cb5b8f0c05937387073cec1d'


def candidate_sample_v2(raw, admission, day, cutoff, evidence):
    """Project unchanged sampling into candidate-only fields; no accepted value."""
    from .live_sampling import sample
    from .live_metadata import V2_POLICY
    require(admission['version'] == V2_POLICY and not admission['precision_holds'] and
            admission['stream_identity'] == admission['configuration_applicability'] == 'SUPPORTED',
            'V2 source predicates required')
    require(evidence.get('query_complete') is True, 'Incomplete query cannot be sampled')
    sampled = sample(raw, admission['metadata'], day, cutoff, evidence)
    eligible = sampled['status'] == 'AVAILABLE' and sampled['normalized_eligible']
    result = {k: deepcopy(sampled[k]) for k in
              ('identity','window','quality','source_timestamp_utc','source_date_pst_fixed',
               'selection_offset_seconds','value_native','value_status','query_complete')}
    result.update(version=V2_VERSION, admission=deepcopy(admission), evidence=deepcopy(evidence),
                  method='DAILY NATIVE SAMPLE', statistic='Average', support_interval='UNKNOWN',
                  timestamp_position='UNKNOWN',
                  label='Daily native sample — provider Average; support interval unknown',
                  sample_status=sampled['status'], candidate_normalized_eligible=eligible,
                  candidate_value_vwc_percent=sampled['value_vwc_percent'] if eligible else None,
                  scientific_acceptance='CANDIDATE_NOT_ADMITTED', publication_eligible=False)
    return result


def candidate_cell_v2(previous=None, *, result=None, clock=None, outcome='UNQUERIED'):
    """Independent per-day projection. A failure never masquerades as current data."""
    cell = deepcopy(previous) if previous is not None else dict(
        version=V2_VERSION, current=None, last_candidate=None, latest_attempt=None)
    require(cell['version'] == V2_VERSION, 'No V1 state migration')
    require(clock is not None or result is None and outcome == 'UNQUERIED', 'Attempt clock required')
    if clock is not None:
        parse_utc(clock)
    if cell['latest_attempt'] and cell['latest_attempt']['clock'] is not None:
        require(clock is not None and parse_utc(clock) > parse_utc(cell['latest_attempt']['clock']),
                'Reversed candidate attempt clock')
    if result is not None:
        require(result['version'] == V2_VERSION, 'Candidate result version')
        if cell['last_candidate'] is not None:
            require(result['identity'] == cell['last_candidate']['identity'] and
                    result['window']['target_date'] == cell['last_candidate']['window']['target_date'],
                    'No cross-stream/day last-good carry')
        outcome = result['sample_status']
        if result['candidate_normalized_eligible']:
            cell['last_candidate'] = deepcopy(result)
    else:
        require(outcome in ('UNQUERIED','FAILED','INCOMPLETE','HELD','INACTIVE'), 'Candidate failure outcome')
    cell['current'] = deepcopy(result) if result is not None and result['candidate_normalized_eligible'] else None
    cell['latest_attempt'] = dict(clock=clock, outcome=outcome, clock_kind='OFFLINE_REPLAY')
    return cell


def derive_science_v2(source_root, plan, *, built_at):
    """Read the exact immutable pilot only; never construct a provider or writer."""
    from pathlib import Path
    from types import SimpleNamespace
    from .history_acquisition.safety import digest
    from .live_metadata import admit_v2, V2_POLICY
    from .live_capture import shape
    from .live_sampling import window
    from .live_provider import spec, source_fingerprint
    root = Path(source_root)
    body = read(root/'PILOT_RECEIPT_MANIFEST.json')
    require(sha(body) == PILOT_PIN, 'Only the authenticated November 13 pilot is supported')
    manifest = decode(body); values = {}
    for role in ('receipt','state'):
        ref = manifest['artifacts'][role]
        require(ref['path'] == 'products/'+role+'-'+ref['sha256']+'.json', 'Pilot artifact path')
        b = read(root/ref['path'])
        require(sha(b) == ref['sha256'] and len(b) == ref['bytes'], 'Pilot artifact pin')
        values[role] = decode(b)
    state, receipt = values['state'], values['receipt']
    require(manifest['execution_plan_sha256'] == digest(plan) == state['binding']['plan_sha256'] ==
            receipt['execution_plan_sha256'], 'Original plan binding')
    require(manifest['proposal_sha256'] == state['binding']['proposal_sha256'] and
            receipt['source_sha256'] == plan['source_sha256'], 'Original authority/source binding')
    require(not receipt['fixture_based'] and not manifest['publication_eligible'] and
            not receipt['publication_eligible'] and receipt['cells'] == state['cells'], 'Original private lineage')
    attempts = state['attempts']
    require(len(attempts) == receipt['provider_requests'] == 7 and receipt['body_bytes'] == 211284,
            'Exact retained capture scope')
    for i, a in enumerate(attempts):
        b = read(root/'objects'/a['body_sha256'])
        require(a['ordinal'] == i and sha(b) == a['body_sha256'] and len(b) == a['bytes'] and
                a['status'] == 'RECEIVED' and a['details']['complete'] is True and
                a['details']['status'] == 200 and a['clock_kind'] == 'LIVE_PROVIDER', 'Complete original body')
    at = attempts[-1]['details']['retrieved_at']
    require(parse_utc(built_at) >= parse_utc(at), 'Build precedes original retrieval')
    admissions = admit_v2(SimpleNamespace(root=root, data=state, binding=state['binding']), plan, at)
    cells = {}
    for sid in plan['roster']:
        key = sid+'/'+plan['target_day']; original = receipt['cells'][key]
        require(original['current']['status'] == 'HELD' and original['last_accepted'] is None,
                'Original HELD lineage required')
        pages = [a for a in attempts if a['context']['kind'] == 'observations' and a['context']['stream_id'] == sid]
        require(len(pages) == 1, 'Only the witnessed single-page query is supported')
        a = pages[0];win = window(plan['target_day'], plan['not_before'])
        require(a['query'] == spec(plan, 'observations', sid, win['start'])['url'], 'Exact observation query')
        payload = shape(read(root/'objects'/a['body_sha256']), sid)
        require(len(payload['data']) == a['rows'] < payload['limit'] and payload.get('skip',0) == 0 and
                payload.get('total',len(payload['data'])) == len(payload['data']), 'Incomplete page')
        evidence = dict(query_complete=True, original_query=a['query'], original_attempted_at=a['attempted_at'],
                        original_retrieved_at=a['details']['retrieved_at'], body_sha256=a['body_sha256'],
                        eligibility_cutoff_utc=plan['not_before'], capture_manifest_sha256=PILOT_PIN)
        result = candidate_sample_v2(payload['data'], admissions[sid], plan['target_day'], plan['not_before'], evidence)
        cells[key] = candidate_cell_v2(result=result, clock=built_at)
    return dict(version=V2_VERSION, policy=V2_POLICY, visibility='NOT_PUBLIC',
                scientific_acceptance='CANDIDATE_NOT_ADMITTED', publication_eligible=False,
                provider_requests=0, mode='OFFLINE_PINNED_PILOT_REPLAY', built_at_utc=built_at,
                source_capture_manifest_sha256=PILOT_PIN, original_plan_sha256=digest(plan),
                original_source_sha256=receipt['source_sha256'], implementation_sha256=source_fingerprint(),
                cells=cells, other_dates='UNQUERIED', other_streams='NOT_ADMITTED',
                historical_reference=dict(status='UNVERIFIED',component=None),
                current_sample_percentile_rank=None)


def verify_science_v2(root, pin, source_root, plan):
    body = read(root/'MANIFEST.json');require(sha(body) == pin, 'V2 manifest pin')
    m = decode(body);require(m['version'] == V2_VERSION and m['publication_eligible'] is False, 'V2 boundary')
    ref = m['product'];require(ref['path'] == 'products/candidate-'+ref['sha256']+'.json', 'V2 product path')
    b = read(root/ref['path']);require(sha(b) == ref['sha256'] and len(b) == ref['bytes'], 'V2 product hash')
    product = decode(b)
    require(product == derive_science_v2(source_root, plan, built_at=product['built_at_utc']), 'V2 evidence/science parity')
    return dict(status='SCIENCE_V2_PRIVATE_CANDIDATE_VERIFIED', version=V2_VERSION,
                manifest_sha256=pin, product_sha256=ref['sha256'], provider_requests=0,
                candidate_eligible=sum(c['current']['candidate_normalized_eligible'] for c in product['cells'].values()))
