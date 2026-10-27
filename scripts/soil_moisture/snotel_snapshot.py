"""Inactive, saved-input current-WY replacement. No opener or acquisition API.

Trust comes from the caller's accepted roster, capture and committed-product
bindings. Hashes alone neither authenticate NRCS nor grant disclosure authority.
"""
import copy
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from soil_moisture import snotel_history as h
from soil_moisture_operations import reconcile_admitted, scientific_digest, snotel_admission

VERSION = 'snotel-sms-current-wy-1'
PRODUCT = 'snotel-sms-current-wy'
PATHS = ('docs/data/snotel-sms/index.json', 'docs/data/snotel-sms/current-wy.json')
MAX_PRODUCT = 32 * 1024**2
MAX_FLAG_BYTES = 64
MAX_NUMBER_BYTES = 32
IDENTITY_FIELDS = ('sensor_identity', 'station_triplet', 'element_code', 'height_depth',
                   'ordinal', 'duration', 'period_ref', 'unit_native', 'metadata_sha256')
PRESENCE_FIELDS = ('value', 'origValue', 'qcFlag', 'qaFlag', 'origQcFlag')
RECORD_COLUMNS = ('value_native', 'original_value', 'source_fields_present_bits',
                  'qc_flag', 'qa_flag', 'original_qc_flag', 'observation_state',
                  'element_digest_index', 'evidence_index')
DAY_COLUMNS = ('provider_date', 'source_timestamp_utc', 'admission',
               'last_admitted', 'latest_record', 'latest_evidence_index')
EVIDENCE_COLUMNS = ('station_triplet', 'begin_date', 'end_date', 'retrieved_at_utc',
                    'response_sha256', 'receipt_sha256', 'request_sha256',
                    'response_bytes', 'metadata_sha256', 'adapter_version')
STATES = {'OBSERVED_ACCEPTED', 'NO_DATA_CHANGE', 'ACCEPTED_REVISION', 'WITHHELD',
          'MISSING', 'MISSING_WITHIN_COMPLETED_QUERY', 'COMPLETE_VALID_EMPTY'}
ADMITTED = {'OBSERVED_ACCEPTED', 'NO_DATA_CHANGE', 'ACCEPTED_REVISION'}
require = h.require


def _hash(value):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value), 'SHA-256 required')
    return value


def _clock(value):
    return datetime.fromisoformat(h._utc(value).replace('Z', '+00:00'))


def water_year(as_of):
    local = (_clock(as_of) - timedelta(hours=8)).date()
    return local.year + (local.month >= 10)


def interval(year, start, end):
    require(type(year) is int and 1901 <= year <= 9998, 'Ending-year WY required')
    lo, hi = date(year - 1, 10, 1), date(year, 9, 30)
    require(h._date(start) == lo and lo <= h._date(end) <= hi, 'Single full-prefix WY interval required')
    return [(lo + timedelta(days=i)).isoformat() for i in range((h._date(end) - lo).days + 1)]


def roster_binding(groups):
    """Authenticate caller-selected exact membership against pinned metadata."""
    require(isinstance(groups, list) and len(groups) == 32, 'Exactly 32 station groups required')
    bound, identities, stations = [], set(), set()
    for group in groups:
        require(set(group) == {'metadata_body', 'metadata_sha256', 'sensor_identities'}, 'Roster group fields')
        sensors = h._sensors(group['metadata_body'], group['metadata_sha256'])
        ids = group['sensor_identities']
        require(isinstance(ids, list) and ids == sorted(set(ids)) and ids, 'Sorted exact membership required')
        require(set(ids) <= sensors.keys() and not identities.intersection(ids), 'Roster identity mismatch')
        station = sensors[ids[0]]['station_triplet']
        require(station not in stations and all(sensors[x]['station_triplet'] == station for x in ids), 'One station per group')
        require(len(station) <= 40 and all(len(x) <= 80 for x in ids), 'Identity byte bound')
        stations.add(station); identities.update(ids)
        bound.append(dict(station_triplet=station, metadata_sha256=group['metadata_sha256'], sensor_identities=ids))
    require(len(identities) == 98, 'Exactly 98 selected sensors required')
    return bound


def request_for(binding, start, end):
    parameters = dict(stationTriplets=binding['station_triplet'],
                      elements=','.join(x.split('|', 1)[1] for x in binding['sensor_identities']),
                      duration='DAILY', periodRef='END', beginDate=start, endDate=end,
                      returnFlags='true', returnOriginalValues='true', returnSuspectData='true')
    return dict(method='GET', url=h.TARGET + '?' + urlencode(parameters), parameters=parameters)


def plan_current_wy(as_of, groups, *, expected_roster_sha256):
    """No previous-year mode. October 1 can deliberately plan zero requests."""
    bound = roster_binding(groups)
    require(h.sha256(h.canonical(bound)) == expected_roster_sha256, 'Roster trust binding mismatch')
    as_of = h._utc(as_of); year = water_year(as_of)
    start = date(year - 1, 10, 1); end = (_clock(as_of) - timedelta(hours=8)).date() - timedelta(days=1)
    no_work = end < start
    return dict(version=VERSION, as_of=as_of, water_year=year, start=str(start),
                end=None if no_work else str(end), status='NO_ELIGIBLE_INTERVAL' if no_work else 'PLANNED',
                roster_sha256=expected_roster_sha256,
                requests=[] if no_work else [request_for(b, str(start), str(end)) for b in bound])


def check_run_year(plan, now):
    require(_clock(now) >= _clock(plan['as_of']) and water_year(now) == plan['water_year'],
            'Source-WY boundary crossed: stop and replan; no second-year request')


def _native(row, element_index, evidence_index):
    fields = row['source_fields_present']
    require(set(fields) <= {'date', *PRESENCE_FIELDS} and 'date' in fields, 'Undisclosed provider field')
    bits = sum(1 << i for i, f in enumerate(PRESENCE_FIELDS) if f in fields)
    flags = [row.get(f) for f in ('qc_flag', 'qa_flag', 'original_qc_flag')]
    for value in flags:
        require(_valid_flag(value), 'Flag syntax/byte bound')
    return [row['value_native'], row.get('original_value'), bits, *flags,
            row['observation_state'], element_index, evidence_index]


def _valid_flag(value):
    # Bound both native UTF-8 and JSON escaping expansion; never truncate.
    return value is None or (isinstance(value, str) and len(value.encode()) <= MAX_FLAG_BYTES
        and len(h.canonical(value).strip()) <= MAX_FLAG_BYTES + 2
        and not any(ord(c) < 32 for c in value))


def _record_science(record, identity, day, elements):
    if record is None: return None
    provider = {'date': day}
    for i, value in enumerate((record[0], record[1], *record[3:6])):
        if record[2] & (1 << i): provider[PRESENCE_FIELDS[i]] = value
    # Same normalized native scientific projection as legacy deep equality.
    return (scientific_digest(provider), elements[record[7]], record[6],
            h.source_timestamp(day), identity, 'pct')


def _coverage(sensors):
    counts = {x: 0 for x in sorted(STATES)}
    numeric = zeros = retained = 0
    for sensor in sensors:
        for row in sensor['days']:
            counts[row[2]] += 1
            if row[3] is not None:
                numeric += 1; zeros += row[3][0] == 0
                retained += row[2] not in ADMITTED
    return dict(positions=sum(counts.values()), query_accounted=sum(counts.values()),
                admission_counts=counts, last_admitted_numeric=numeric, genuine_zeros=zeros,
                retained_after_nonnumeric=retained, failed=0, not_queried=0)


def _binding(index_bytes, product_bytes):
    d = h._json(product_bytes)
    return dict(generation=d['generation'], index_sha256=h.sha256(index_bytes),
                product_sha256=h.sha256(product_bytes), water_year=d['water_year'])


def encode_product(document):
    """Complete two-file serialization; never truncate to meet the hard cap."""
    d = copy.deepcopy(document); d.pop('generation', None)
    d['generation'] = h.sha256(h.canonical(d))
    raw = h.canonical(d)
    index = h.canonical(dict(version=VERSION, product=PRODUCT, generation=d['generation'],
                            water_year=d['water_year'], parent=d['parent'],
                            files={'current-wy.json': dict(bytes=len(raw), sha256=h.sha256(raw))}))
    require(len(raw) + len(index) <= MAX_PRODUCT, 'Combined two-file 32 MiB bound')
    validate_product_bytes(index, raw)
    return {PATHS[0]: index, PATHS[1]: raw}


def validate_product_bytes(index_bytes, product_bytes):
    require(type(index_bytes) is bytes and type(product_bytes) is bytes and
            0 < len(index_bytes) <= 8192 and len(index_bytes) + len(product_bytes) <= MAX_PRODUCT,
            'Combined product byte bound')
    index, d = h._json(index_bytes), h._json(product_bytes)
    require(h.canonical(index) == index_bytes and h.canonical(d) == product_bytes, 'Canonical product bytes required')
    required = {'version', 'product', 'generation', 'parent', 'water_year', 'start', 'end', 'as_of', 'built_at',
                'roster_sha256', 'record_columns', 'day_columns', 'evidence_columns', 'presence_fields',
                'elements', 'evidence', 'sensors', 'coverage', 'annual_maintenance'}
    require(set(d) == required and d['version'] == VERSION and d['product'] == PRODUCT, 'Product schema/version')
    content = {k: v for k, v in d.items() if k != 'generation'}
    require(d['generation'] == h.sha256(h.canonical(content)), 'Generation mismatch')
    expected = dict(version=VERSION, product=PRODUCT, generation=d['generation'], water_year=d['water_year'],
                    parent=d['parent'], files={'current-wy.json': dict(bytes=len(product_bytes), sha256=h.sha256(product_bytes))})
    require(index == expected, 'Manifest closure or binding mismatch')
    if d['parent'] is not None:
        p = d['parent']; require(set(p) == {'generation', 'index_sha256', 'product_sha256', 'water_year'}, 'Parent binding fields')
        for k in ('generation', 'index_sha256', 'product_sha256'): _hash(p[k])
        require(type(p['water_year']) is int and p['water_year'] in (d['water_year'], d['water_year'] - 1), 'Parent WY lineage')
        require(p['generation'] != d['generation'], 'Self parent')
    dates = interval(d['water_year'], d['start'], d['end'])
    require(_clock(h.source_timestamp(d['end'])) <= _clock(d['as_of']) <= _clock(d['built_at']), 'Source/query/build clock order')
    _hash(d['roster_sha256'])
    require(d['record_columns'] == list(RECORD_COLUMNS) and d['day_columns'] == list(DAY_COLUMNS)
            and d['evidence_columns'] == list(EVIDENCE_COLUMNS) and d['presence_fields'] == list(PRESENCE_FIELDS), 'Native columns')
    require(d['annual_maintenance'] == dict(policy='SEPARATE_DAVE_DIRECTED', season_end=f"{d['water_year']}-09-30",
            unfinished_tail=d['end'] < f"{d['water_year']}-09-30", reference_policy='NINE_DISTINCT_COMPLETED_WY_PER_CALENDAR_DAY'), 'Maintenance truth')
    elements, evidence = d['elements'], d['evidence']
    require(isinstance(elements, list) and len(elements) <= 2 * 98 * 366 and len(set(elements)) == len(elements), 'Element table bound/duplicates')
    for e in elements: _hash(e)
    require(isinstance(evidence, list) and 0 < len(evidence) <= 32 * 367, 'Evidence table bound')
    require(len({h.sha256(h.canonical(e)) for e in evidence}) == len(evidence), 'Duplicate evidence')
    for e in evidence:
        require(isinstance(e, list) and len(e) == len(EVIDENCE_COLUMNS), 'Evidence schema')
        require(re.fullmatch(r'\d+:[A-Z]{2}:SNTL', e[0]) and len(e[0]) <= 40, 'Evidence station')
        require(d['start'] <= e[1] <= e[2] <= d['end'], 'Evidence outside current-WY prefix')
        h._date(e[1]); h._date(e[2])
        require(_clock(h.source_timestamp(e[2])) <= _clock(e[3]) <= _clock(d['built_at']), 'Evidence clock')
        for i in (4, 5, 6, 8): _hash(e[i])
        require(type(e[7]) is int and 0 < e[7] <= h.MAX_BODY and e[9] == h.ADAPTER_VERSION, 'Evidence body/adapter')
    require(isinstance(d['sensors'], list) and len(d['sensors']) == 98, '98-sensor product required')
    used_e, used_m, ids, stations, station_latest = set(), set(), set(), {}, {}
    for sensor in d['sensors']:
        require(set(sensor) == {*IDENTITY_FIELDS, 'days'}, 'Sensor fields')
        sid = sensor['sensor_identity']; station = sensor['station_triplet']
        require(sid == h.sensor_identity(station, sensor['height_depth'], sensor['ordinal']) and sid not in ids
                and len(sid) <= 80 and len(station) <= 40, 'Exact sensor identity/duplicate')
        ids.add(sid); stations.setdefault(station, []).append(sid)
        require([sensor[k] for k in ('element_code', 'duration', 'period_ref', 'unit_native')] == ['SMS', 'DAILY', 'END', 'pct'], 'Native identity semantics')
        _hash(sensor['metadata_sha256'])
        require(isinstance(sensor['days'], list) and len(sensor['days']) == len(dates), 'Date coverage count')
        for day, row in zip(dates, sensor['days']):
            require(isinstance(row, list) and len(row) == 6 and row[0] == day and row[1] == h.source_timestamp(day)
                    and row[2] in STATES, 'Date/source timestamp/admission')
            def evidence_at(i):
                require(type(i) is int and 0 <= i < len(evidence), 'Unresolved evidence')
                e = evidence[i]; used_e.add(i)
                require(e[0] == station and e[1] <= day <= e[2] and e[8] == sensor['metadata_sha256'], 'Evidence identity/interval/metadata')
                return e
            latest_e = evidence_at(row[5])
            for rec in row[3:5]:
                if rec is None: continue
                require(isinstance(rec, list) and len(rec) == len(RECORD_COLUMNS), 'Native record schema')
                require(type(rec[2]) is int and 0 <= rec[2] < 32, 'Field-presence bits')
                for i, value in enumerate((rec[0], rec[1], *rec[3:6])):
                    require(rec[2] & (1 << i) or value is None, 'Absent field has value')
                    if i < 2: require(value is None or type(value) in (int, float) and math.isfinite(value)
                                      and len(h.canonical(value).strip()) <= MAX_NUMBER_BYTES, 'Native finite number/byte bound')
                    else: require(_valid_flag(value), 'Flag syntax/byte bound')
                require(type(rec[7]) is int and 0 <= rec[7] < len(elements), 'Unresolved element')
                used_m.add(rec[7]); old_e = evidence_at(rec[8])
                require(_clock(old_e[3]) <= _clock(latest_e[3]), 'Prior numeric newer than latest query')
                missing = bool(rec[2] & 1) and rec[0] is None or rec[3] == 'M'
                original = not (rec[2] & 1) and rec[3] == 'S' and h._number(rec[1])
                suspect = not (rec[2] & 1) and rec[3] == 'S' and not (rec[2] & 2)
                require(missing or original or suspect or bool(rec[2] & 1) and h._number(rec[0]), 'Unclassified native row')
                state = 'EXPLICIT_MISSING' if missing else 'SUSPECT_ORIGINAL_ONLY' if original else 'SUSPECT_NO_NUMERIC_VALUE' if suspect else 'OBSERVED'
                require(rec[6] == state, 'Native observation state')
            if row[3] is not None:
                require(row[3][6] == 'OBSERVED' and row[3][3] != 'S' and h._number(row[3][0]), 'Last admitted must be numeric')
            if row[4] is None:
                require(row[2] in {'COMPLETE_VALID_EMPTY', 'MISSING_WITHIN_COMPLETED_QUERY'}, 'Missing latest native record')
            else:
                require(row[4][8] == row[5], 'Latest evidence binding')
                admitted = snotel_admission(row[4][6], [{'qc_flag': row[4][3]}])
                require(row[2] in ADMITTED if admitted == 'OBSERVED_ACCEPTED' else row[2] == admitted, 'Admission contradicts native fields')
            if row[2] in ADMITTED: require(row[3] == row[4], 'Accepted/latest mismatch')
        latest_indices = {r[5] for r in sensor['days']}
        require(len(latest_indices) == 1, 'Complete replacement requires one latest station query')
        station_latest.setdefault(station, set()).update(latest_indices)
        latest = evidence[next(iter(latest_indices))]
        require(latest[1:3] == [d['start'], d['end']], 'Latest query must cover entire prefix')
        returned = any(r[4] is not None for r in sensor['days'])
        require(all(r[2] != 'COMPLETE_VALID_EMPTY' if returned else r[2] == 'COMPLETE_VALID_EMPTY'
                    for r in sensor['days']), 'Empty versus omitted sensor-query inconsistency')
        require(len({r[4][7] for r in sensor['days'] if r[4] is not None}) <= 1,
                'One native stationElement per latest sensor response')
    require(len(stations) == 32 and used_e == set(range(len(evidence))) and used_m == set(range(len(elements))), 'Station/evidence/element closure')
    require(all(len(v) == 1 for v in station_latest.values()), 'Mixed latest station captures')
    bound = [dict(station_triplet=s, metadata_sha256=next(x['metadata_sha256'] for x in d['sensors'] if x['station_triplet'] == s), sensor_identities=sorted(v)) for s, v in sorted(stations.items())]
    require(all(len({x['metadata_sha256'] for x in d['sensors'] if x['station_triplet'] == s}) == 1 for s in stations), 'Station metadata conflict')
    require(d['roster_sha256'] == h.sha256(h.canonical(bound)), 'Product roster binding')
    by_station = {b['station_triplet']: b for b in bound}
    for ev in evidence:
        require(ev[6] == h.sha256(h.canonical(request_for(by_station[ev[0]], ev[1], ev[2]))), 'Evidence exact request hash')
    require(d['coverage'] == _coverage(d['sensors']), 'Coverage mismatch')
    return d


def import_product(files, *, trusted_binding):
    """Caller must independently verify this exact product in a trusted commit."""
    require(set(files) == set(PATHS), 'Exact two-file product required')
    require(set(trusted_binding) == {'commit', 'generation', 'index_sha256', 'product_sha256', 'water_year'}
            and isinstance(trusted_binding['commit'], str) and re.fullmatch('[0-9a-f]{40}', trusted_binding['commit']), 'Trusted commit binding required')
    require(_binding(files[PATHS[0]], files[PATHS[1]]) == {k: v for k, v in trusted_binding.items() if k != 'commit'}, 'Independent product trust mismatch')
    return validate_product_bytes(files[PATHS[0]], files[PATHS[1]])


def validate_routine_candidate(document):
    """Publication admission is narrower than saved historical capacity replay."""
    year = document['water_year']
    require(water_year(document['as_of']) == year == water_year(document['built_at']),
            'Routine source-WY boundary crossed: stop and replan')
    completed = (_clock(document['as_of']) - timedelta(hours=8)).date() - timedelta(days=1)
    require(document['end'] == completed.isoformat(), 'Routine full-current-WY prefix required')


def validate_successor(current, candidate):
    """Independently check carry-forward against trusted fresh-main science."""
    validate_routine_candidate(candidate)
    if current is not None:
        require(candidate['water_year'] in (current['water_year'], current['water_year'] + 1), 'Successor WY')
    same_year = current is not None and candidate['water_year'] == current['water_year']
    if same_year:
        require(candidate['roster_sha256'] == current['roster_sha256'] and candidate['end'] >= current['end'],
                'Same-WY roster/metadata/coverage drift')
    old = {v['sensor_identity']: v for v in current['sensors']} if same_year else {}
    def expanded(rec, d):
        if rec is None: return None
        return rec[:7] + [d['elements'][rec[7]], d['evidence'][rec[8]]]
    for sensor in candidate['sensors']:
        sid = sensor['sensor_identity']; before = old.get(sid)
        if before is not None: require(all(before[k] == sensor[k] for k in IDENTITY_FIELDS), 'Successor identity drift')
        previous = {} if before is None else {row[0]: row for row in before['days']}
        for row in sensor['days']:
            prior = previous.get(row[0]); admitted = None if prior is None else prior[3]
            if prior is not None:
                require(_clock(candidate['evidence'][row[5]][3]) >= _clock(current['evidence'][prior[5]][3]), 'Successor query chronology regression')
            if row[2] not in ADMITTED:
                require(expanded(row[3], candidate) == expanded(admitted, current), 'Successor discarded or changed last-admitted science')
            else:
                old_science = _record_science(admitted, sid, row[0], current['elements'] if same_year else [])
                new_science = _record_science(row[4], sid, row[0], candidate['elements'])
                expected, _ = reconcile_admitted('OBSERVED_ACCEPTED', old_science, new_science, fingerprint=lambda v: v)
                require(row[2] == expected, 'Successor scientific revision classification')


def read_product(root):
    root = Path(root)
    for p in (root, *root.parents): require(not p.is_symlink(), 'Symlink product root')
    folder = root / 'docs/data/snotel-sms'
    for p in (root/'docs', root/'docs/data', folder): require(not p.is_symlink(), 'Symlink product path')
    require(folder.is_dir() and {p.name for p in folder.iterdir()} == {'index.json', 'current-wy.json'}, 'Product subtree closure')
    files = {}
    for name in PATHS:
        p = root/name; require(p.is_file() and not p.is_symlink() and p.stat().st_size <= MAX_PRODUCT, 'Product file bound/type')
        files[name] = p.read_bytes()
    validate_product_bytes(files[PATHS[0]], files[PATHS[1]])
    return files


def write_new_product(root, files):
    """Fresh scratch/candidate directory only; never replaces last-good files."""
    require(set(files) == set(PATHS), 'Exact output closure')
    validate_product_bytes(files[PATHS[0]], files[PATHS[1]])
    root = Path(root); require(not root.exists() and not root.is_symlink(), 'Fresh output required')
    root.mkdir()
    for name in PATHS:
        p = root/name; p.parent.mkdir(parents=True, exist_ok=True)
        with p.open('xb') as stream: stream.write(files[name]); stream.flush()


def build_saved_snapshot(groups, captures, *, year, start, end, as_of, built_at,
                         expected_roster_sha256, expected_capture_sha256s, prior_files=None, trusted_prior=None):
    """Pure saved-input seam, including independent full-season capacity fixtures.

    This is not a planner or a previous-year acquisition option. Routine request
    orchestration must use plan_current_wy/check_run_year, not this replay seam.
    Captures are consumed serially; only one native history is expanded at once.
    """
    dates = interval(year, start, end); as_of = h._utc(as_of); built_at = h._utc(built_at)
    require(_clock(h.source_timestamp(end)) <= _clock(as_of) <= _clock(built_at), 'Saved source/build clock order')
    bound = roster_binding(groups)
    require(bound == sorted(bound, key=lambda b: b['station_triplet']), 'Canonical station order')
    require(h.sha256(h.canonical(bound)) == expected_roster_sha256, 'Roster trust mismatch')
    require(len(expected_capture_sha256s) == 32 and len(set(expected_capture_sha256s)) == 32, 'Exact 32 capture pins')
    parent = None; old_sensors = {}; elements = []; evidence = []
    if prior_files is not None:
        prior = import_product(prior_files, trusted_binding=trusted_prior)
        require(prior['water_year'] in (year, year - 1), 'Prior WY must be same or immediate predecessor')
        parent = _binding(prior_files[PATHS[0]], prior_files[PATHS[1]])
        if prior['water_year'] == year:
            require(prior['end'] <= end and prior['roster_sha256'] == expected_roster_sha256, 'Same-WY shrink/metadata drift')
            old_sensors = {s['sensor_identity']: s for s in prior['sensors']}
            elements = copy.deepcopy(prior['elements']); evidence = copy.deepcopy(prior['evidence'])
    else: require(trusted_prior is None, 'No fabricated public parent')
    sensors_out = []; total_body = 0; iterator = iter(captures)
    for i, (group, binding) in enumerate(zip(groups, bound)):
        capture = next(iterator, None); require(capture is not None, 'Required station capture absent')
        receipt = capture['receipt']; raw = capture['body']
        require(capture['receipt_sha256'] == expected_capture_sha256s[i] == h.sha256(h.canonical(receipt)), 'Capture trust binding')
        require(receipt['request'] == request_for(binding, start, end), 'Exact station/date request binding')
        require(receipt.get('http_status') == 200 and receipt.get('complete') is True and receipt.get('error') is None
                and receipt.get('body_accounting_state') == 'COMPLETE'
                and type(receipt.get('physical_attempt')) is int and receipt['physical_attempt'] >= 1
                and type(receipt.get('total_consumed_body_bytes')) is int and type(receipt.get('captured_prefix_bytes')) is int
                and receipt.get('total_consumed_body_bytes') == receipt.get('captured_prefix_bytes') == len(raw)
                and type(raw) is bytes and 0 < len(raw) <= h.MAX_BODY, 'Required station incomplete/failed/accounting hold')
        require(_clock(h.source_timestamp(end)) <= _clock(receipt['retrieved_at_utc']) <= _clock(built_at), 'Capture clock outside completed interval/build')
        total_body += len(raw); require(total_body <= 32 * h.MAX_BODY, 'Aggregate consumed body bound')
        history = h.validate_history(h.build_history(group['metadata_body'], group['metadata_sha256'], [capture]))
        require(all(q['successful_coverage'] for q in history['query_ledger']), 'Required station source validation hold')
        ev = [binding['station_triplet'], start, end, h._utc(receipt['retrieved_at_utc']), receipt['response_sha256'],
              capture['receipt_sha256'], h.sha256(h.canonical(receipt['request'])), len(raw), group['metadata_sha256'], h.ADAPTER_VERSION]
        if ev not in evidence: evidence.append(ev)
        ei = evidence.index(ev)
        by_id = {s['sensor_identity']: s for s in history['sensors']}
        rows = {(r['sensor_identity'], r['provider_date']): r for r in history['observations']}
        queries = {q['sensor_identity']: q for q in history['query_ledger']}
        for sid in binding['sensor_identities']:
            native = by_id[sid]; sensor = {k: native[k] for k in IDENTITY_FIELDS}; sensor['days'] = []
            old = {d[0]: d for d in old_sensors.get(sid, {}).get('days', [])}
            for day in dates:
                row = rows.get((sid, day)); rec = None
                if row is not None:
                    element = scientific_digest(row['response_station_element'])
                    if element not in elements: elements.append(element)
                    rec = _native(row, elements.index(element), ei)
                    outcome = snotel_admission(row['observation_state'], [row])
                else: outcome = 'COMPLETE_VALID_EMPTY' if queries[sid]['status'] == 'successful_empty' else 'MISSING_WITHIN_COMPLETED_QUERY'
                previous = old.get(day, [None, None, None, None])[3]
                if day in old:
                    require(_clock(evidence[old[day][5]][3]) <= _clock(ev[3]), 'Query chronology regression')
                outcome, retained = reconcile_admitted(outcome, previous, rec,
                    fingerprint=lambda r: _record_science(r, sid, day, elements))
                sensor['days'].append([day, h.source_timestamp(day), outcome, copy.deepcopy(retained), rec, ei])
            sensors_out.append(sensor)
        del history
    require(next(iterator, None) is None, 'Extra station capture')
    # Keep every referenced last-admitted provenance; remove only unreachable table entries.
    used_e, used_m = set(), set()
    for sensor in sensors_out:
        for row in sensor['days']:
            used_e.add(row[5])
            for rec in row[3:5]:
                if rec is not None: used_m.add(rec[7]); used_e.add(rec[8])
    e_order, m_order = sorted(used_e), sorted(used_m)
    e_map, m_map = {v: i for i, v in enumerate(e_order)}, {v: i for i, v in enumerate(m_order)}
    for sensor in sensors_out:
        for row in sensor['days']:
            row[5] = e_map[row[5]]
            for rec in row[3:5]:
                if rec is not None: rec[7] = m_map[rec[7]]; rec[8] = e_map[rec[8]]
    doc = dict(version=VERSION, product=PRODUCT, parent=parent, water_year=year, start=start, end=end,
               as_of=as_of, built_at=built_at, roster_sha256=expected_roster_sha256,
               record_columns=list(RECORD_COLUMNS), day_columns=list(DAY_COLUMNS), evidence_columns=list(EVIDENCE_COLUMNS),
               presence_fields=list(PRESENCE_FIELDS), elements=[elements[i] for i in m_order], evidence=[evidence[i] for i in e_order],
               sensors=sorted(sensors_out, key=lambda s: s['sensor_identity']), coverage=_coverage(sensors_out),
               annual_maintenance=dict(policy='SEPARATE_DAVE_DIRECTED', season_end=f'{year}-09-30',
                   unfinished_tail=end < f'{year}-09-30', reference_policy='NINE_DISTINCT_COMPLETED_WY_PER_CALENDAR_DAY'))
    return encode_product(doc)
