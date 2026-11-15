"""Private saved-pilot and range exports; separate one-day reference proof.

The consumer sample schema stays V2. Additional provenance belongs exclusively
to the manifest-bound validation sidecar. Range exports alone replace a private
verified-generation pointer; this never admits science or publishes a feed.
"""
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
import importlib
import io
from pathlib import Path
import sys
from types import SimpleNamespace

from .history_acquisition.safety import Hold, decode, digest, encode, require, sha
from .live_product import V2_VERSION, derive_science_v2
from .live_state import durable, read, P1, expand, check_season
from .transport import parse_utc

VERSION = 'dendra-private-producer-export-2'
PROOF_VERSION = 'dendra-exact-stream-one-day-binding-1'
HISTORY_PIN = 'e88df74bbac66036f98f8e9f32d5b6f880699eea5cbe5b8762c7567163ac87e7'
REFERENCE_VERSION = 'dendra-historical-day-context-candidate-1'
HISTORY_LABEL = 'Historical fixed-PST arithmetic daily means — same stream; physical instrument continuity unverified'
INPUT_FIELDS = {'science_root','science_manifest_sha256','capture_root','plan',
                'reference_root','component_pins_sha256','history_root','historical_source_root'}
NO_CURRENT = {'FAILED','INCOMPLETE','HELD','UNQUERIED','INACTIVE','COMPLETE_EMPTY',
              'MISSING_VALUE','WITHHELD','CONFLICT','INVALID'}


def member(root, ref):
    """Read one bounded relative, hash-bound member without following aliases."""
    rel = Path(ref['path'])
    require(not rel.is_absolute() and '..' not in rel.parts, 'Member path escape')
    body = read(Path(root)/rel)
    require(len(body) == ref['bytes'] and sha(body) == ref['sha256'], 'Member bytes/hash')
    return decode(body)


def project_cells(cells, identities, *, clock_kinds=('OFFLINE_REPLAY',)):
    """Validate/project the existing cell model; never invent a second state store."""
    require(set(cells) == set(identities), 'Exact export cell roster')
    result = deepcopy(cells)
    for key, cell in result.items():
        sid, day = key.split('/')
        require(set(cell) == {'version','current','last_candidate','latest_attempt'} and
                cell['version'] == V2_VERSION, 'V2 cell shape')
        attempt = cell['latest_attempt']
        require(set(attempt) == {'clock','outcome','clock_kind'} and
                attempt['clock_kind'] in clock_kinds, 'Separate supported attempt clock')
        if attempt['clock'] is not None:
            parse_utc(attempt['clock'])
        else:
            require(attempt['outcome'] == 'UNQUERIED' and cell['last_candidate'] is None,
                    'Missing attempted clock')
        for name in ('current','last_candidate'):
            value = cell[name]
            if value is None:
                continue
            require(value['identity'] == identities[key] and value['identity']['stream_id'] == sid and
                    value['window']['target_date'] == day,
                    'Cross-source/stream/day cell')
            require(value['version'] == V2_VERSION and value['candidate_normalized_eligible'] is True and
                    value['sample_status'] == 'AVAILABLE' and value['query_complete'] is True and
                    value['scientific_acceptance'] == 'CANDIDATE_NOT_ADMITTED' and
                    value['publication_eligible'] is False, 'Ineligible current/last candidate')
            require(attempt['clock'] is not None and
                    parse_utc(value['source_timestamp_utc']) <= parse_utc(attempt['clock']),
                    'Sample/attempt clock order')
            timestamp = parse_utc(value['source_timestamp_utc'])
            require(parse_utc(value['window']['start']) <= timestamp < parse_utc(value['window']['end']) and
                    value['source_date_pst_fixed'] == (timestamp-timedelta(hours=8)).date().isoformat(),
                    'Source window/fixed-PST date')
        if cell['current'] is None:
            require(attempt['outcome'] in NO_CURRENT, 'Missing eligible current')
        else:
            require(attempt['outcome'] == 'AVAILABLE' and cell['current'] == cell['last_candidate'],
                    'Current/last-candidate disagreement')
    return result


def saved_science(inputs):
    root = Path(inputs['science_root'])
    body = read(root/'MANIFEST.json')
    require(sha(body) == inputs['science_manifest_sha256'], 'Saved science manifest pin')
    manifest = decode(body)
    require(manifest['version'] == V2_VERSION and manifest['publication_eligible'] is False,
            'Saved science boundary')
    ref = manifest['product']
    require(ref['path'] == 'products/candidate-'+ref['sha256']+'.json', 'Saved science member path')
    product = member(root, ref)
    expected = derive_science_v2(Path(inputs['capture_root']), inputs['plan'], built_at=product['built_at_utc'])
    # The explicitly pinned prior product binds its prior implementation. Verify
    # all science again with this implementation without rewriting that product.
    expected['implementation_sha256'] = product['implementation_sha256']
    require(product == expected, 'Saved science versus authentic capture')
    identities = {key: c['current']['identity'] for key,c in product['cells'].items()}
    project_cells(product['cells'], identities)
    return product


def reference_engine():
    """Load the existing standalone reference modules only for this offline proof."""
    old_path = sys.path[:]
    directory = Path(__file__).resolve().parents[1]/'soil_moisture'
    try:
        sys.path.insert(0, str(directory))
        engine = importlib.import_module('reference_v1')
        require(Path(engine.__file__).resolve() == directory/'reference_v1.py', 'Reference module origin')
        return engine
    finally:
        sys.path[:] = old_path


def reference_binding(inputs, product):
    """Check just the two frozen November 13 cohorts; never re-average history."""
    from .history_acquisition.candidate3_delivery import validate_record
    engine = reference_engine()
    root = Path(inputs['history_root']); reads = []
    body = read(root/'manifest.json'); require(sha(body) == HISTORY_PIN, 'Frozen history manifest')
    manifest = decode(body); members = {r['path']:r for r in manifest['files']}
    def history(ref):
        require(members.get(ref['path']) == ref, 'History member not in frozen manifest')
        value = member(root,ref);reads.append(ref);return value
    index = history(manifest['station_index']); binding = history(manifest['source_binding'])
    original = Path(inputs['historical_source_root'])
    source_manifest = member(original,binding['candidate_manifest'])
    require(binding['candidate_manifest']['sha256'] == manifest['candidate_manifest_sha256'], 'History source generation')
    catalog_ref = next(r for r in binding['catalog'] if r['path'].endswith('.csv'))
    catalog_body = read(original/catalog_ref['path'])
    require(sha(catalog_body) == catalog_ref['sha256'] and len(catalog_body) == catalog_ref['bytes'], 'Frozen catalog pin')
    catalog = list(csv.DictReader(io.StringIO(catalog_body.decode())))
    rr = Path(inputs['reference_root']); pins_body = read(rr/'COMPONENT_PINS.json')
    require(sha(pins_body) == inputs['component_pins_sha256'], 'Reference component-pins hash')
    pins = decode(pins_body)
    require(set(pins) == {k.split('/')[0] for k in product['cells']}, 'Exact reference roster')
    components, proofs = {}, {}
    for key,cell in product['cells'].items():
        current = cell['current']; native = current['identity']; sid, day = key.split('/')
        require(day == '2026-11-13', 'Only November 13 context supported')
        pref = pins[sid]
        require(pref['path'] == 'reference/component-'+sid+'.json', 'Saved component path')
        component = member(rr.parent,pref)
        stations = [s for s in index['stations'] if s['station_id'] == native['station_id']]
        require(len(stations) == 1, 'Exact historical station')
        hits = [s for s in stations[0]['streams'] if s['stream_id'] == sid]
        require(len(hits) == 1, 'Exact historical stream')
        descriptor_ref = hits[0]['descriptor']; desc = history(descriptor_ref); ident = desc['identity']
        require(all(ident[k] == native[k] for k in ('station_id','stream_id','depth_cm','native_unit')),
                'Native/history identity conflict')
        src = {k:ident[k] for k in ('station_id','stream_id','accepted_stream_id',
               'export_local_series_key','depth_cm','native_unit','conversion_multiplier')}
        src['depth_status'] = desc['catalog_depth_status']
        identity = dict(network='Dendra', station_id=ident['station_id'], sensor_id=sid, source=src)
        assets = [a for a in source_manifest['assets'] if a['stream_id'] == sid]
        require(len(assets) == 1, 'Exact source asset')
        asset = assets[0]; n = asset['source_native']
        cats = [c for c in catalog if c['export_local_series_key'] == ident['export_local_series_key']]
        require(len(cats) == 1, 'Exact export-series catalog binding')
        cat = cats[0]
        require(n['proposed_stream_id'] == cat['proposed_stream_id'] == sid and
                n['station_id'] == cat['proposed_station_id'] == ident['station_id'] and
                asset['export_local_series_key'] == n['export_local_series_key'] == ident['export_local_series_key'] and
                ident['export_local_series_key'] == 'csv-sha256:'+n['source_file_sha256']+':column:'+str(n['source_column_ordinal_1_based']) and
                ident['accepted_stream_id'] == (n['accepted_stream_id'] or None) == (cat['accepted_stream_id'] or None) and
                ident['accepted_stream_id'] in (None,sid) and cat['daily_asset'] == asset['path'] and
                cat['native_unit'] == ident['native_unit'] and n['depth_cm'] == ident['depth_cm'] and
                float(cat['conversion_multiplier']) == n['conversion_multiplier'] == ident['conversion_multiplier'],
                'Source/catalog/native exact-stream chain')
        require(desc['source']['parquet'] == {k:asset[k] for k in ('path','sha256','bytes')} and
                desc['source']['csv'] == asset['csv'] and desc['source']['native_asset_sha256'] == n['sha256'] and
                all(desc['source'][k] == asset[k] for k in ('science_sha256','input_sha256','quality_proof_sha256','original_csv_sha256')),
                'Descriptor provenance chain')
        rows, selected = [], []
        for part in desc['history']:
            document = history(part['file'])
            require((document['station_id'],document['stream_id'],document['water_year']) ==
                    (ident['station_id'],sid,part['water_year']), 'History shard identity/year')
            for row in document['records']:
                if row['date'][5:] != '11-13' or row['water_year'] > 2026:
                    continue
                validate_record(row,binding['row_schema'],ident)
                require(row['water_year'] == part['water_year'] and row['date'] < binding['completed_end_exclusive'],
                        'Completed frozen source day')
                rows.append(row)
                state = {'ACCEPTED':'ACCEPTED','MISSING':'MISSING','WITHHELD_BY_EXISTING_SCREEN':'WITHHELD',
                         'UNRESOLVED_SEMANTICS':'HELD'}[row['daily_status']]
                selected.append(dict(source_date=row['date'],water_year=row['water_year'],true_dowy=row['dowy'],
                                     plot_day=row['plot_day_aligned'],state=state,value=row['daily_mean_vwc_percent']))
        reference = engine.reference(SimpleNamespace(identity=identity,observations=selected),2026)
        one_day = next(d for d in reference['days'] if d['month_day'] == '11-13')
        expected = dict(version=REFERENCE_VERSION,status='CANDIDATE_NOT_ADMITTED',identity=identity,day=one_day,
                        method=HISTORY_LABEL,physical_deployment_continuity='UNVERIFIED',policy=reference['policy_id'],
                        source_contract=reference['contract'],completed_wy_cutoff=2026,source_manifest_sha256=HISTORY_PIN,
                        descriptor_sha256=descriptor_ref['sha256'],cohort_sha256=digest(rows),
                        science_implementation_sha256=engine.implementation_hash(),not_full_366_day_component=True,
                        current_sample_percentile_rank=None)
        require(component == expected, 'Saved one-day component versus frozen cohort/science')
        components[sid] = component
        proofs[key] = dict(status='VERIFIED_PRIVATE_ONE_DAY_BINDING',reference_state=one_day['state'],
                          context_available=one_day['state']=='ELIGIBLE',component_sha256=pref['sha256'],
                          descriptor_sha256=descriptor_ref['sha256'],cohort_sha256=component['cohort_sha256'],
                          identity=identity,accepted_stream_id_preserved=ident['accepted_stream_id'],
                          metadata_evidence=n['metadata_evidence'],source_asset_sha256=asset['sha256'],
                          physical_deployment_continuity='UNVERIFIED',n_distinct_wy=one_day['n_distinct_wy'])
    proof = dict(version=PROOF_VERSION,status='VERIFIED_PRIVATE_ONE_DAY_BINDING',calendar_day='11-13',
                 completed_wy_cutoff=2026,minimum_distinct_wy=9,publication_eligible=False,
                 scientific_acceptance='CANDIDATE_NOT_ADMITTED',bindings=proofs,history_manifest_sha256=HISTORY_PIN,
                 source_manifest=binding['candidate_manifest'],catalog=catalog_ref,history_members=reads,
                 reference_implementation_sha256=engine.implementation_hash())
    return proof, components


def build(inputs, *, built_at):
    from .live_provider import source_fingerprint
    product = saved_science(inputs)
    require(parse_utc(built_at) >= parse_utc(product['built_at_utc']), 'Export precedes saved science')
    try:
        proof, components = reference_binding(inputs,product)
    except (Hold, FileNotFoundError) as exc:
        # Missing/corrupt reference evidence never changes sample admission.
        # Keep the old UNVERIFIED field and report the failed private association.
        proof = dict(version=PROOF_VERSION,status='REFERENCE_HELD',
                     reason=str(exc) if isinstance(exc,Hold) else 'Missing reference dependency',
                     bindings={},publication_eligible=False,scientific_acceptance='CANDIDATE_NOT_ADMITTED')
        components = {}
    original_hash = digest(product)
    product['built_at_utc'] = built_at  # Original sample/retrieval/attempt clocks remain unchanged.
    proof.update(source_science_manifest_sha256=inputs['science_manifest_sha256'],
                 source_science_product_sha256=original_hash,export_built_at_utc=built_at,
                 export_implementation_sha256=sha(read(Path(__file__))),
                 producer_implementation_sha256=source_fingerprint(),sample_schema=V2_VERSION)
    return dict(product=product,validation=proof,**{'reference-'+sid:c for sid,c in components.items()})


def verify(root, pin, inputs):
    root = Path(root); body = read(root/'MANIFEST.json')
    require(sha(body) == pin, 'Export manifest pin')
    m = decode(body)
    require(m['version'] == VERSION and m['visibility'] == 'NOT_PUBLIC' and
            m['publication_eligible'] is False and m['input_sha256'] == digest(inputs), 'Private export boundary/input')
    expected = build(inputs,built_at=m['built_at_utc'])
    require(set(m['artifacts']) == set(expected), 'Complete export member set')
    for role, value in expected.items():
        ref = m['artifacts'][role]
        require(ref['path'] == 'products/'+role+'-'+ref['sha256']+'.json', 'Export member path')
        require(member(root,ref) == value, 'Export semantic readback')
    status = ('PRIVATE_EXPORT_PASS_REFERENCE_HELD' if expected['validation']['status'] == 'REFERENCE_HELD'
              else 'V2_PRIVATE_PRODUCER_INTEGRATION_PASS')
    return dict(status=status,version=VERSION,
                manifest_sha256=pin,product_sha256=m['artifacts']['product']['sha256'],
                validation_sha256=m['artifacts']['validation']['sha256'],provider_requests=0,candidate_cells=2)


def export(manifest, pin, output, *, before_manifest=lambda: None):
    body = read(Path(manifest),128*1024);require(sha(body) == pin, 'Explicit export input pin')
    inputs = decode(body);require(set(inputs) == INPUT_FIELDS, 'Exact export input fields')
    root = Path(output).absolute();repo = Path(__file__).resolve().parents[2]
    sources = [Path(inputs[k]).resolve() for k in INPUT_FIELDS if k.endswith('_root')]
    require(root == root.resolve() and not root.exists() and repo not in (root,*root.parents) and
            'private_custody' not in root.parts and all(s not in (root,*root.parents) and root not in s.parents for s in sources),
            'Fresh isolated private export root')
    built_at = datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    artifacts = build(inputs,built_at=built_at)  # Verify everything before opening output.
    root.mkdir(mode=0o700);(root/'products').mkdir(mode=0o700);refs = {}
    for role,value in artifacts.items():
        b = encode(value);name = 'products/'+role+'-'+sha(b)+'.json';durable(root/name,b)
        refs[role] = dict(path=name,sha256=sha(b),bytes=len(b))
    before_manifest()  # An interrupted export has no admission manifest or pointer.
    m = dict(version=VERSION,visibility='NOT_PUBLIC',publication_eligible=False,built_at_utc=built_at,
             input_sha256=digest(inputs),artifacts=refs)
    b = encode(m);durable(root/'MANIFEST.json',b)
    return verify(root,sha(b),inputs)


def range_preview(state, *, before_pointer=lambda: None):
    """Immutable private generation followed by one existing durable pointer write."""
    validate_snapshot(state.root,state.data)
    snapshot=deepcopy(state.data)
    from .live_provider import request_count
    version='2' if state.p1 else '1'
    product=dict(version='dendra-private-range-preview-'+version,visibility='NOT_PUBLIC',status='PARTIAL',
                 scientific_acceptance='CANDIDATE_NOT_ADMITTED',publication_eligible=False,provider_requests=request_count(snapshot['attempts']),
                 representation=state.data['range_runs'][-1]['plan']['representation'],
                 built_at_utc=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
                 water_year=state.binding['water_year'],cells=deepcopy(state.data['cells']),
                 pending=state.data['range_pending'],unrepresented_dates='UNQUERIED',
                 historical_reference=dict(status='UNVERIFIED',component=None),current_sample_percentile_rank=None)
    refs={}
    for role,value in [('snapshot',snapshot),('product',product)]:
        b=encode(state.packed(value));name='products/range-'+role+'-'+sha(b)+'.json';state.put(state.root/name,b)
        refs[role]=dict(path=name,bytes=len(b),sha256=sha(b))
    m=dict(version='dendra-private-range-manifest-'+version,publication_eligible=False,artifacts=refs)
    b=encode(m);name='products/range-manifest-'+sha(b)+'.json';state.put(state.root/name,b)
    result=verify_range(state.root,name,sha(b),state.binding)
    before_pointer()
    state.put(state.root/'accepted.json',encode(dict(manifest_path=name,manifest_sha256=sha(b))),replace=True)
    return result


def verify_range(root, manifest_path, pin, binding):
    root=Path(root);require(manifest_path=='products/range-manifest-'+pin+'.json','Range manifest path')
    b=read(root/manifest_path);require(sha(b)==pin,'Range manifest pin');m=decode(b)
    version='2' if binding.get('lineage')==P1 else '1'
    require(m['version']=='dendra-private-range-manifest-'+version and m['publication_eligible'] is False and
            set(m['artifacts'])=={'snapshot','product'},'Range manifest boundary')
    values={}
    for role,ref in m['artifacts'].items():
        require(ref['path']=='products/range-'+role+'-'+ref['sha256']+'.json','Range artifact path')
        values[role]=member(root,ref)
        if version=='2':values[role]=expand(root,values[role])
    data,p=values['snapshot'],values['product'];require(data['binding']==binding,'Range snapshot binding')
    counts=validate_snapshot(root,data)
    from .live_provider import request_count
    requests=request_count(data['attempts'])
    expected=dict(version='dendra-private-range-preview-'+version,visibility='NOT_PUBLIC',status='PARTIAL',
                  scientific_acceptance='CANDIDATE_NOT_ADMITTED',publication_eligible=False,provider_requests=requests,
                  representation=data['range_runs'][-1]['plan']['representation'],built_at_utc=p['built_at_utc'],
                  water_year=binding['water_year'],cells=data['cells'],pending=data['range_pending'],
                  unrepresented_dates='UNQUERIED',historical_reference=dict(status='UNVERIFIED',component=None),
                  current_sample_percentile_rank=None)
    parse_utc(p['built_at_utc']);require(p==expected,'Range product/state parity')
    live=any(r['plan']['status']=='APPROVED' for r in data['range_runs'])
    return dict(status='RANGE_SYNC_PRIVATE_VERIFIED' if live else 'RANGE_SYNC_OFFLINE_PASS',manifest_path=manifest_path,manifest_sha256=pin,
                product_sha256=m['artifacts']['product']['sha256'],provider_requests=requests,**counts)

def validate_snapshot(root, data):
    """Read-only replay of retained receipts using the same paginator and science."""
    from .live_provider import admissions
    from .live_range import series_binding,dates
    from .live_sampling import window
    from .live_product import candidate_sample_v2
    from .transport import parse_utc
    from .live_capture import shape
    from .transport import DendraFetcher
    binding=data['binding'];p1=binding.get('lineage')==P1
    require(binding==series_binding(binding['roster'],binding['water_year'],binding['attempt_limit'],version=2 if p1 else 1), 'Snapshot lineage')
    require(data['version']==('dendra-offline-state-2' if p1 else 'dendra-offline-state-1'),'Snapshot format')
    if p1:check_season(data)
    runs={r['plan_sha256']:{**r,'plan':r['plan'] if p1 else dict(r['plan'],output_root=str(root))} for r in data['range_runs']}
    require(all(pin==digest(r['plan']) for pin,r in runs.items()), 'Snapshot run/root binding')
    state=SimpleNamespace(root=root,data=data)
    for i,a in enumerate(data['attempts']):
        require(a['ordinal']==i and a['status'] in ('RECEIVED','RESERVED','AMBIGUOUS'), 'Snapshot attempt order/state')
        if a['body_sha256'] is not None:
            b=read(root/'objects'/a['body_sha256']);require(sha(b)==a['body_sha256'] and len(b)==a['bytes'], 'Snapshot raw body')
    for key,cell in data['cells'].items():
        sid,day=key.split('/');require(day in dates(binding['water_year'],data['range_runs'][-1]['plan']['not_before']), 'Snapshot WY/day')
        kinds=('LIVE_PROVIDER',) if runs and all(r['plan']['status']=='APPROVED' for r in runs.values()) else ('OFFLINE_REPLAY',)
        project_cells({key:cell},{key:binding['roster'][sid]['identity']},clock_kinds=kinds)
        result=data['range_results'][key]
        require(cell['current']==(result if result and result['candidate_normalized_eligible'] else None), 'Snapshot current eligibility')
        for sample in [s for s in (result,cell['last_candidate']) if s is not None]:
            ev=sample['evidence'];run=runs[ev['range_plan_sha256']];plan=run['plan'];raw=[];seen=[]
            require(sample['identity']==binding['roster'][sid]['identity'] and sample['window']['target_date']==day,
                    'Snapshot sample key')
            class Response(io.BytesIO): status,headers=200,{}
            def saved(request,timeout):
                require(len(seen)<len(ev['pages']), 'Snapshot missing page')
                page=ev['pages'][len(seen)];a=data['attempts'][page['ordinal']]
                require(a==page and a['status']=='RECEIVED' and a['details']['complete'] and a['details']['status']==200 and
                        a['context']['plan_sha256']==digest(plan) and a['context']['target_day']==day and
                        a['query']==request.full_url, 'Snapshot exact query receipt')
                body=read(root/'objects'/a['body_sha256']);raw.extend(shape(body,sid)['data']);seen.append(a)
                return Response(body)
            win=window(day,plan['not_before'])
            envelope=DendraFetcher(opener=saved,max_attempts=1,max_pages=plan['limits']['pages_per_stream'],page_size=2016,
                now_fn=lambda:parse_utc(ev['query_completed_at']),sleep_fn=lambda _:None).fetch_interval(sid,win['start'],win['end'])
            require(len(seen)==len(ev['pages']) and envelope['content_sha256']==ev['envelope_sha256'], 'Snapshot query completion')
            ad=admissions(state,plan,day,ev['query_completed_at'],run['start_ordinal'])[sid]
            require(candidate_sample_v2(raw,ad,day,plan['not_before'],ev)==sample, 'Snapshot science replay')
    return dict(cells=len(data['cells']),attempts=len(data['attempts']),revisions=len(data['revisions']),pending=len(data['range_pending']))
