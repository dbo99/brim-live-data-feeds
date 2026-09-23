"""Lossless SCAN review transport and bounded complete-output preflight.

CSV cells remain original strings. Only repeated column names are factored out;
the decoder restores the exact row mappings consumed by existing plot functions.
"""
import hashlib
import json
import re
from pathlib import Path

INDEX_BYTES = 262_144
BUNDLE_BYTES = 6_000_000
EXPANDED_BYTES = 12_000_000
CACHE_BYTES = 12_000_000  # compact serialized bytes, not a claim about JS heap
CACHE_ENTRIES = 8
TABLE_CELLS = 1_000_000
TABLE_ROWS = 50_000
TABLE_COLUMNS = 64
TABLE_NAMES = (
    'scan_depth_style.csv',
    'scan_sms_monthly_context.csv',
    'scan_sms_prior_wy_fallback_traces.csv',
    'scan_sms_waterday_percentiles.csv',
    'scan_soil_moisture_current_wy_trace.csv',
)

# Fixed version-2 schemas are the saved public CSV headers, in original order.
TABLE_SCHEMAS = {'scan_depth_style.csv': ['depth_in',
                          'depth_label',
                          'depth_order',
                          'depth_color_hex',
                          'depth_role'],
 'scan_sms_monthly_context.csv': ['site_code',
                                  'depth_in',
                                  'month_date',
                                  'calendar_month',
                                  'actual_sms_pct',
                                  'actual_n_days',
                                  'actual_start_date',
                                  'actual_end_date',
                                  'record_start_date',
                                  'record_end_date',
                                  'record_start_wy',
                                  'record_end_wy',
                                  'record_label',
                                  'ref_p30',
                                  'ref_p50',
                                  'ref_p70',
                                  'ref_n_years',
                                  'monthly_ref_ok',
                                  'ref_start_date',
                                  'ref_end_date',
                                  'ref_start_wy',
                                  'ref_end_wy',
                                  'ref_label',
                                  'ref_wy_label',
                                  'current_wy_excluded',
                                  'context_years_back',
                                  'context_stat_min_years',
                                  'min_days_per_month',
                                  'display_timezone'],
 'scan_sms_prior_wy_fallback_traces.csv': ['station_uid',
                                           'station_name',
                                           'site_code',
                                           'depth_in',
                                           'water_year',
                                           'water_day',
                                           'obs_date',
                                           'sms_pct',
                                           'trace_order',
                                           'trace_label',
                                           'n_days_in_trace',
                                           'water_day_min',
                                           'water_day_max',
                                           'water_day_span',
                                           'min_daily_rows_per_prior_wy',
                                           'min_waterday_span_per_prior_wy',
                                           'daily_ribbon_min_years',
                                           'daily_ribbon_min_days',
                                           'fallback_reason'],
 'scan_sms_waterday_percentiles.csv': ['station_uid',
                                       'station_name',
                                       'site_code',
                                       'depth_in',
                                       'water_day',
                                       'p00',
                                       'p10',
                                       'p30',
                                       'p50',
                                       'p70',
                                       'p90',
                                       'p100',
                                       'n_obs',
                                       'n_years',
                                       'years_min',
                                       'years_max',
                                       'climatology_ok',
                                       'min_years_for_context',
                                       'current_water_year_excluded',
                                       'build_time_utc'],
 'scan_soil_moisture_current_wy_trace.csv': ['station_uid',
                                             'station_name',
                                             'site_code',
                                             'depth_in',
                                             'depth_label',
                                             'depth_order',
                                             'depth_color_hex',
                                             'water_year',
                                             'water_day',
                                             'obs_date',
                                             'obs_datetime_local',
                                             'sms_pct',
                                             'sensor_count',
                                             'sensor_id']}


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def encode_scan(feature, tables, columns):
    assert set(tables) == set(TABLE_NAMES), 'SCAN table inventory'
    packed = {}
    for name, rows in tables.items():
        cols = columns[name]
        assert all(list(row) == cols for row in rows), 'CSV column order/shape changed'
        packed[name] = {'columns': cols, 'rows': [[row[c] for c in cols] for row in rows]}
    value = {'schema': 'sm1-scan-popup-2', 'feature': feature, 'tables': packed}
    # A round-trip before writing catches any encoding loss, including empty tables.
    assert decode_scan(value)['tables'] == tables
    return value


def decode_scan(value):
    assert value.get('schema') == 'sm1-scan-popup-2', 'SCAN transport schema'
    assert set(value.get('tables', {})) == set(TABLE_NAMES), 'SCAN table inventory'
    feature = value.get('feature', {})
    assert feature.get('type') == 'Feature' and isinstance(feature.get('properties'), dict), 'SCAN feature'
    restored, cells, size = {}, 0, 0
    for name, table in value['tables'].items():
        cols, rows = table.get('columns'), table.get('rows')
        assert isinstance(cols, list) and 0 < len(cols) <= TABLE_COLUMNS, 'SCAN columns'
        assert all(isinstance(c, str) and c and c not in ('__proto__', 'prototype', 'constructor') for c in cols), 'SCAN column name'
        assert len(set(cols)) == len(cols), 'Duplicate SCAN columns'
        assert cols == TABLE_SCHEMAS[name], 'SCAN column schema'
        assert isinstance(rows, list) and len(rows) <= TABLE_ROWS, 'SCAN row bound'
        cells += len(rows) * len(cols)
        assert cells <= TABLE_CELLS, 'SCAN cell bound'
        restored[name] = []
        for row in rows:
            assert isinstance(row, list) and len(row) == len(cols) and all(isinstance(v, str) for v in row), 'SCAN row/cell shape'
            obj = dict(zip(cols, row))
            size += len(json_bytes(obj))
            assert size <= EXPANDED_BYTES, 'Expanded SCAN bound'
            restored[name].append(obj)
    decoded = {'schema': 'sm1-scan-popup-1', 'feature': feature, 'tables': restored}
    assert len(json_bytes(decoded)) <= EXPANDED_BYTES, 'Expanded SCAN bound'
    return decoded


def preflight(out):
    """Reject an unusable candidate before its caller reports preparation complete."""
    out = Path(out)
    checked = []

    def check(path, limit, descriptor=None):
        with path.open('rb') as handle:data = handle.read(limit+1)
        assert 0 < len(data) <= limit, f'Consumer byte bound: {path.name}'
        digest = hashlib.sha256(data).hexdigest()
        if descriptor:
            assert descriptor['bytes'] == len(data) and descriptor['sha256'] == digest, f'Descriptor integrity: {path.name}'
        checked.append({'path': path.relative_to(out).as_posix(), 'bytes': len(data), 'limit': limit, 'sha256': digest})
        return data

    common = {}
    for source in ('scan', 'dendra', 'snotel'):
        index = json.loads(check(out / f'{source}.json', INDEX_BYTES))
        from common_index import expand
        index = expand(index,lambda d:check(out/d['path'],INDEX_BYTES,d))
        assert index['schema'] == 'brim-soil-moisture-1' and index['source'] == source, 'Common index schema'
        assert isinstance(index['generation'], str) and len(index['stations']) <= 1500
        stations, sensors, histories, keys = index['stations'], [], 0, set()
        for station in stations:
            assert station['key'] == source + ':' + station['id'] and station['key'] not in keys
            keys.add(station['key'])
            expanded = [{**index['sensor_defaults'], **s} for s in station['sensors']]
            sensors.extend(expanded)
            histories += sum(bool(s.get('history', {}) and s['history']['available']) for s in expanded)
            if source == 'dendra':
                continue  # All advertised native files checked below with its unchanged reader.
            desc = station['bundle']
            assert re.fullmatch(r'[a-z0-9-]+\.json', desc['path']), 'Scoped companion path'
            body = json.loads(check(out / desc['path'], BUNDLE_BYTES, desc))
            if source == 'scan':
                decoded = decode_scan(body)
                assert decoded['feature']['properties']['station_triplet'] == station['id'], 'SCAN bundle station'
                checked[-1]['expanded_bytes'] = len(json_bytes(decoded))
                checked[-1]['transport_schema'] = body['schema']
            else:
                assert body['schema'] == 'sm1-snotel-popup-1'
                assert body['metadata']['stationTriplet'] == station['id']
                assert body['data']['stationTriplet'] == station['id']
        assert len(sensors) <= 10000
        assert index['counts'] == {'stations': len(stations), 'sensors': len(sensors), 'imported_histories': histories}
        common[source] = index

    # Mirror existing Dendra browser bounds (decimal 256000 / strict <2000 and <12e6).
    # Its existing reader/candidate validators remain authoritative and unchanged.
    native = json.loads(check(out / 'dendra/index.json', 256_000))
    assert native['generation'] == common['dendra']['generation'], 'Dendra generation mismatch'
    if native['schema_version']=='dendra-daily-2.0.0':
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]));import dendra_archive as da
        da.dc.require(native['limits']==da.LIMITS,'Native archive bound contract')
        files=[]
        for d in native['file_pages']:
            page=json.loads(check(out/'dendra'/Path(d['path']).relative_to('docs/data/dendra'),262144,d));assert page['schema_version']=='dendra-file-page-2' and len(page['files'])<=500;files+=page['files']
        assert len(files)+len(native['file_pages'])+1==native['file_count']<=20000
        native['files']=files
    else:assert native['schema_version'] == 'dendra-daily-1.1.0' and len(native['files']) < 2000
    for desc in native['files']:
        path = desc['path']
        assert re.fullmatch(r'docs/data/dendra/(history|companion|diagnostics|state)/[a-zA-Z0-9_./-]+', path)
        assert '..' not in Path(path).parts and desc['bytes'] < 12_000_000
        data = check(out / 'dendra' / Path(path).relative_to('docs/data/dendra'), da.expected_limit(path) if native['schema_version']=='dendra-daily-2.0.0' else 11_999_999, desc)
        if path.endswith('.json'):
            json.loads(data)  # Detailed unchanged reader schemas are exercised before success by the CLI preflight below.
        else:
            assert path.endswith('.csv') and data.startswith(b'datastream_id,date,')
    assert len({x['path'] for x in checked}) == len(checked), 'Duplicate advertised path'
    return {'passed': True, 'schema': 'sm1r1-preflight-1', 'files': checked,
            'bounds': {'index': INDEX_BYTES, 'bundle': BUNDLE_BYTES, 'expanded_scan': EXPANDED_BYTES,
                       'cache_compact_bytes': CACHE_BYTES, 'cache_entries': CACHE_ENTRIES,
                       'table_cells': TABLE_CELLS, 'table_rows': TABLE_ROWS, 'table_columns': TABLE_COLUMNS}}
