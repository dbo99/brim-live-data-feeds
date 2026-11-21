"""Exact two-stream metadata admission; no discovery or controller imports.

Native parsing preserves original bodies in the single state store. Unknown
depth/deployment stays native-only; provider config windows are not aggregation
or sensor deployment proof. October saved qualifications are offline only.
"""
from copy import deepcopy
import re

from .live_sampling import window, qualification
from .live_state import read
from .transport import parse_utc
from .history_acquisition.safety import decode, digest, sha, require

ROSTER_SHA256 = 'c40c1d869e67524c8422d624e621a8bc83def46ddcd1a506ef75e771366aafeb'
UNITS = {'Percent':dict(abbreviation='%',label='Percent'),
         'VolumetricWaterContent':dict(abbreviation='m^3/m^3',label='VolumetricWaterContent',name='Volumetric Water Content')}
VERSION = 'dendra-selected-fresh-metadata-1'
FAMILY_PAIR = 'dendra-exact-family-pair-1'


def validate_roster(roster, *, diagnostic=False):
    require(type(roster) is dict and len(roster) == (1 if diagnostic else 2),
            'One diagnostic stream or exactly two streams per lineage')
    if digest(roster) == ROSTER_SHA256: return
    # Explicit new pair policy, never an enlargement of the original pilot root.
    # Evidence pins select identities; independent exact-plan approval authorizes
    # traffic, and fresh source admission below must still match every member.
    families = []
    for sid, r in roster.items():
        require(set(r) == {'identity','family','terms','attributes','selection'}, 'Exact family member fields')
        ident, selected = r['identity'], r['selection']
        require(selected.get('policy') == FAMILY_PAIR and set(selected) ==
                {'policy','station_sha256','stream_sha256','crosswalk_sha256','configuration_sha256'},
                'Explicit source-qualified family selection')
        require(all(type(v) is str and re.fullmatch('[a-f0-9]{64}',v)
                    for k,v in selected.items() if k != 'policy'), 'Family evidence pins')
        require(sid == ident['stream_id'] and ident['native_unit'] in UNITS and
                all(type(ident[k]) is str and re.fullmatch('[a-f0-9]{24}',ident[k])
                    for k in ('station_id','stream_id')), 'Family stream identity/unit')
        terms = r['terms']
        require(terms.get('dq',{}).get('Purpose') == 'ReadytoUse' and
                terms.get('ds',{}).get('Aggregate') == 'Average' and
                terms.get('ds',{}).get('Medium') == 'Soil' and
                terms.get('ds',{}).get('Variable') == 'VolumetricWaterContent' and
                terms.get('dt',{}).get('Unit') == ident['native_unit'], 'Family method/unit')
        require(type(r['family']['organization_id']) is str and
                re.fullmatch('[a-f0-9]{24}',r['family']['organization_id']), 'Family identity')
        families.append(r['family'])
    require(all(f == families[0] for f in families), 'One exact family per pair')


def payload(body):
    value = decode(body)
    require(type(value) is dict, 'Native metadata object required')
    count, stack = 0, [(value,0)]
    while stack:
        item, depth = stack.pop();count += 1
        require(count <= 20000 and depth <= 16, 'Metadata traversal bound')
        if type(item) is dict:
            require(len(item) <= 512 and all(len(k) <= 256 for k in item), 'Metadata object bound')
            stack.extend((v,depth+1) for v in item.values())
        elif type(item) is list:
            require(len(item) <= 10000, 'Metadata array bound');stack.extend((v,depth+1) for v in item)
        elif type(item) is str: require(len(item) <= 65536, 'Metadata scalar bound')
    return value


def public(value):
    access = value.get('access_levels_resolved',{})
    require(type(access) is dict, 'Access shape')
    level = value.get('public_level',access.get('public_level'))
    require(type(level) is int and level == 3 and value.get('is_hidden') is False,
            'Positive public/nonhidden metadata required')
    require('public_level' not in access or access['public_level'] == level, 'Conflicting visibility')
    require(value.get('is_geo_protected') is False, 'Unknown/protected geometry held')
    require(value.get('is_deleted',False) is False and value.get('deleted',False) is False and
            value.get('deleted_at') is None and value.get('state') not in ('deleted','missing','inactive','ended','disabled'),
            'Missing/deleted/inactive metadata')
    for k in ('is_enabled','is_active'):
        require(k not in value or value[k] is True, 'Inactive/unknown activity')


def vocabulary(value):
    require(value.get('_id') == 'dt-unit', 'Vocabulary identity')
    found, stack = {}, [value]
    while stack:
        item = stack.pop()
        if type(item) is dict:
            label = item.get('label')
            if type(label) is str and label in UNITS:
                require(label not in found and item == UNITS[label], 'Unit definition changed/duplicated')
                found[label] = item
            stack.extend(item.values())
        elif type(item) is list: stack.extend(item)
    require(found == UNITS, 'Selected unit vocabulary incomplete')


def admit_all(state, plan, now, *, partial=False):
    diagnostic = 'diagnostic_interval' in plan
    validate_roster(plan['roster'], diagnostic=diagnostic)
    require(state.binding['plan_sha256'] == digest(plan), 'Metadata plan binding')
    records = [a for a in state.data['attempts'] if a['context']['kind'] != 'observations']
    count = 3 if diagnostic else 5
    require((0 < len(records) <= count if partial else len(records) == count) and all(a['status'] == 'RECEIVED' and a['details']['complete'] and
            a['context']['plan_sha256'] == digest(plan) for a in records), 'Complete source metadata receipts required')
    by_key = {}
    for a in records:
        key = (a['context']['kind'],a['context']['stream_id'])
        require(key not in by_key, 'Duplicate metadata receipt')
        from .live_provider import spec
        require(a['query'] == spec(plan,*key)['url'], 'Metadata request binding')
        at = parse_utc(a['details']['retrieved_at'])
        require(parse_utc(plan['not_before']) <= at <= parse_utc(now) and
                (parse_utc(now)-at).total_seconds() <= 300, 'Stale/future metadata')
        b = read(state.root/'objects'/a['body_sha256'])
        require(sha(b) == a['body_sha256'] and len(b) == a['bytes'], 'Metadata body integrity')
        by_key[key] = (payload(b),a)
    vocabulary(by_key[('unit-vocabulary',None)][0])
    win = window(plan['target_day'],now)
    if diagnostic:
        from .live_range import diagnostic_window
        win = diagnostic_window(plan,now)
    results = {}
    for sid, expected in plan['roster'].items():
        if partial and ('station',sid) not in by_key: continue
        station, sr = by_key[('station',sid)]
        ident = expected['identity']
        require(station.get('_id') == ident['station_id'] and
                station.get('organization_id') == expected['family']['organization_id'], 'Station/family identity')
        public(station)
        if partial and ('datastream-list',sid) not in by_key: continue
        streams, dr = by_key[('datastream-list',sid)]
        rows, limit = streams.get('data'), streams.get('limit')
        require(type(rows) is list and type(limit) is int and 1 <= limit <= 500 and len(rows) < limit,
                'Full/incomplete metadata list held; no extra page grant')
        require(type(streams.get('skip',0)) is int and streams.get('skip',0) == 0 and
                ('total' not in streams or type(streams['total']) is int and streams['total'] == len(rows)),
                'Metadata completion unknown')
        by_id = {}
        for row in rows:
            require(type(row) is dict and type(row.get('_id')) is str and re.fullmatch('[a-f0-9]{24}',row['_id']) and
                    row['_id'] not in by_id and row.get('station_id') == ident['station_id'], 'Stream list identity/association')
            by_id[row['_id']] = row
        require(sid in by_id, 'Selected stream missing')
        selected = by_id[sid];public(selected)
        require(selected.get('organization_id') == expected['family']['organization_id'], 'Stream organizational family')
        require(selected.get('terms') == expected['terms'] and selected.get('attributes') == expected['attributes'],
                'Native unit/statistic/depth/attributes changed')
        for value in (station,selected):
            require(value.get('updated_at') is not None and parse_utc(value['updated_at']) <= parse_utc(dr['details']['retrieved_at']),
                    'Source update clock absent/future')
        configs = selected.get('datapoints_config')
        require(type(configs) is list and 0 < len(configs) <= 16 and all(type(c) is dict for c in configs),
                'Temporal configuration shape')
        covering = []
        for c in configs:
            start = parse_utc(c['begins_at'])
            end = None if c.get('ends_before') is None else parse_utc(c['ends_before'])
            require(end is None or start < end, 'Configuration temporal order')
            if start < parse_utc(win['end']) and (end is None or end > parse_utc(win['start'])):
                require(start <= parse_utc(win['start']) and (end is None or end >= parse_utc(win['end'])),
                        'Configuration changes inside selected window')
                covering.append(c)
        require(len(covering) == 1, 'Missing/overlapping configuration applicability')
        if 'selection' in expected:
            require(digest(covering) == expected['selection']['configuration_sha256'],
                    'Selected family configuration changed')
        meta = dict(identity=deepcopy(ident),scope=dict(start=win['start'],end=win['end']),
                    authority_sha256=digest(plan),metadata_sha256=dr['body_sha256'],configuration_sha256=digest(covering),
                    unit_multiplier={'Percent':1,'VolumetricWaterContent':100}[ident['native_unit']],
                    public_level=3,is_hidden=False,geo_protected=False,purpose='ReadytoUse',aggregate='Average')
        qualification(meta,win)
        holds = ['DEPLOYMENT_UNVERIFIED']
        if ident['depth_cm'] is None: holds.append('UNKNOWN_DEPTH')
        # These fields are preserved as claims, never promoted to reviewed
        # deployment semantics absent an independently qualified source schema.
        identity_claims = {k:deepcopy(selected.get(k)) for k in ('sensor_id','deployment_id')}
        require(all(v is None or type(v) is str and len(v) <= 128 for v in identity_claims.values()), 'Identity claim shape')
        results[sid] = dict(version=VERSION,metadata=meta,family=deepcopy(expected['family']),
            checked_at=dr['details']['retrieved_at'],source_updated_at=selected['updated_at'],
            station_updated_at=station['updated_at'],identity_claims=identity_claims,
            precision_holds=holds,normalized_eligible=False,native_acquisition_eligible=True,
            source_pins=dict(station=sr['body_sha256'],datastreams=dr['body_sha256'],
                             vocabulary=by_key[('unit-vocabulary',None)][1]['body_sha256']))
    return results


def acquire(provider):
    from .live_provider import spec
    try:
        provider.exchange(spec(provider.plan,'unit-vocabulary'))
        admit_all(provider.state,provider.plan,provider.clock(),partial=True)
        for sid in provider.plan['roster']:
            for kind in ('station','datastream-list'):
                provider.exchange(spec(provider.plan,kind,sid))
                admit_all(provider.state,provider.plan,provider.clock(),partial=True)
        return admit_all(provider.state,provider.plan,provider.clock())
    except BaseException:
        provider.state.data['provider']['closed']=True
        provider.state.data['provider']['failure']='METADATA_ADMISSION_HOLD'
        provider.state.commit()
        if 'diagnostic_interval' not in provider.plan:
            for sid in provider.plan['roster']:
                provider.state.outcome(sid+'/'+provider.plan['target_day'],provider.clock(),status='HELD')
        raise


V2_POLICY = 'dendra-exact-provider-stream-candidate-2'


def admit_v2(state, plan, source_check_at):
    """Explicit saved-evidence policy; V1/default admission remains unchanged.

    Reuse every positive V1 predicate at the original acquisition clock, not
    today's replay clock. Missing precision fields do not defeat stream identity.
    This does not authorize acquisition or establish physical continuity.
    """
    admitted = admit_all(state, plan, source_check_at)
    for sid, a in admitted.items():
        attempt = next(x for x in state.data['attempts']
                       if x['context']['kind'] == 'datastream-list' and x['context']['stream_id'] == sid)
        native = payload(read(state.root/'objects'/attempt['body_sha256']))
        selected = next(x for x in native['data'] if x['_id'] == sid)
        station_attempt = next(x for x in state.data['attempts']
                               if x['context']['kind'] == 'station' and x['context']['stream_id'] == sid)
        station = payload(read(state.root/'objects'/station_attempt['body_sha256']))
        require(all(v.get('is_enabled') is True and v.get('state') == 'ready'
                    for v in (station, selected)), 'V2 positive current activity unverified')
        win = a['metadata']['scope']
        configs = [x for x in selected['datapoints_config']
                   if parse_utc(x['begins_at']) <= parse_utc(win['start']) and
                   (x.get('ends_before') is None or parse_utc(x['ends_before']) >= parse_utc(win['end']))]
        require(len(configs) == 1, 'V2 configuration applicability')
        config = configs[0]
        for key, expected in (('station_id', a['metadata']['identity']['station_id']), ('stream_id', sid)):
            require(key not in config or config[key] == expected, 'Conflicting configuration identity')
        presence = {}
        for key in ('sensor_id', 'deployment_id'):
            value, configured = selected.get(key), config.get(key)
            require(all(v is None or type(v) is str and 0 < len(v) <= 128
                        for v in (value, configured)), 'Unsupported precision claim')
            require(value is None or configured is None or value == configured,
                    'Conflicting physical identity claims')
            presence[key] = 'NOT_PRESENT' if key not in selected else ('PRESENT_NULL' if value is None else 'PRESENT')
        a.update(version=V2_POLICY, precision_holds=[], normalized_eligible=False,
                 stream_identity='SUPPORTED', configuration_applicability='SUPPORTED',
                 depth_status='UNKNOWN' if a['metadata']['identity']['depth_cm'] is None else 'SUPPORTED',
                 field_presence=presence, physical_deployment_continuity='UNVERIFIED',
                 source_check_at=source_check_at, evaluation_scope='SAVED_ACQUISITION_ONLY')
    return admitted
