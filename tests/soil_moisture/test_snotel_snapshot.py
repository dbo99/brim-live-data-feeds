"""Synthetic-only current-WY replacement; never opens a provider connection."""
import copy
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
from soil_moisture import snotel_snapshot as s, snotel_transport as transport
from test_snotel_transport import Response


def fixture(year=2027, end=None, mode='numeric', flag_size=1, revision=0):
    start = f'{year-1}-10-01'; end = end or start; dates = s.interval(year, start, end)
    groups, captures = [], []
    clock = s.h.source_timestamp(end).replace('08:00:00', f'{12+revision:02d}:00:00')
    for i in range(32):
        station = f'{990000000+i}:ZZ:SNTL'; depths = [-2, -8, -20] + ([-30, -40] if i == 0 else [])
        elements = [dict(elementCode='SMS', heightDepth=d, ordinal=1, durationName='DAILY', storedUnitCode='pct', originalUnitCode='pct') for d in depths]
        metadata = s.h.canonical([dict(stationTriplet=station, networkCode='SNTL', dataTimeZone=-8, stationElements=elements)])
        ids = sorted(s.h.sensor_identity(station, d, 1) for d in depths)
        group = dict(metadata_body=metadata, metadata_sha256=s.h.sha256(metadata), sensor_identities=ids); groups.append(group)
        binding = dict(station_triplet=station, sensor_identities=ids, metadata_sha256=group['metadata_sha256'])
        blocks = []
        for j, element in enumerate(elements):
            values = []
            for k, day in enumerate(dates):
                flag = (f'QA_SYNTHETIC_{i}_{j}_{k}_' + 'Q'*flag_size)[:flag_size]
                if flag_size == s.MAX_FLAG_BYTES and k % 2:
                    flag = '"'*(s.MAX_FLAG_BYTES//2)  # maximal permitted JSON escaping
                row = dict(date=day, value=0 if k % 7 == 0 else 14.5+revision,
                           origValue=-(10**31-1), qcFlag=flag, qaFlag=flag, origQcFlag=flag)
                if mode == 'withheld': row['qcFlag'] = 'S'; row.pop('value')
                elif mode == 'missing': row['value'] = None
                elif mode == 'mixed':
                    if k % 7 == 1: row['value'] = None; row['qcFlag'] = 'M'
                    if k % 7 == 2: row.pop('value'); row['qcFlag'] = 'S'
                if mode != 'omitted' or k % 2 == 0: values.append(row)
            blocks.append(dict(stationElement=element, values=values))
        body = s.h.canonical([] if mode == 'empty' else [dict(stationTriplet=station, data=blocks)])
        receipt = dict(request=s.request_for(binding, start, end), retrieved_at_utc=clock,
                       response_sha256=s.h.sha256(body), response_bytes=len(body), http_status=200,
                       complete=True, error=None, physical_attempt=1, body_accounting_state='COMPLETE',
                       captured_prefix_bytes=len(body), total_consumed_body_bytes=len(body))
        captures.append(dict(body=body, receipt=receipt, receipt_sha256=s.h.sha256(s.h.canonical(receipt))))
    return groups, captures


def build(groups, captures, year=2027, end=None, prior=None):
    end = end or f'{year-1}-10-01'
    clock = s.h.source_timestamp(end).replace('08:00:00', '20:00:00')
    trust = None if prior is None else dict(commit='a'*40, **s._binding(prior[s.PATHS[0]], prior[s.PATHS[1]]))
    return s.build_saved_snapshot(groups, iter(captures), year=year, start=f'{year-1}-10-01', end=end,
        as_of=clock, built_at=clock, expected_roster_sha256=s.h.sha256(s.h.canonical(s.roster_binding(groups))),
        expected_capture_sha256s=[c['receipt_sha256'] for c in captures], prior_files=prior, trusted_prior=trust)


def doc(files): return s.h._json(files[s.PATHS[1]])


def repin(capture):
    capture['receipt']['response_sha256'] = s.h.sha256(capture['body'])
    for k in ('response_bytes', 'captured_prefix_bytes', 'total_consumed_body_bytes'): capture['receipt'][k] = len(capture['body'])
    capture['receipt_sha256'] = s.h.sha256(s.h.canonical(capture['receipt']))


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.groups, self.captures = fixture()
        self.base = build(self.groups, self.captures)

    def test_current_wy_boundaries(self):
        pin = s.h.sha256(s.h.canonical(s.roster_binding(self.groups)))
        def plan(clock): return s.plan_current_wy(clock, self.groups, expected_roster_sha256=pin)
        empty = plan('2027-10-01T08:00:00Z')
        self.assertEqual((empty['water_year'], empty['end'], empty['requests']), (2028, None, []))
        self.assertEqual(plan('2027-10-02T08:00:00Z')['end'], '2027-10-01')
        self.assertEqual(plan('2027-09-30T23:00:00Z')['end'], '2027-09-29')
        self.assertEqual(plan('2027-10-01T07:59:59Z')['water_year'], 2027)
        self.assertEqual(plan('2027-03-15T07:30:00Z')['end'], '2027-03-13')
        self.assertEqual(plan('2028-03-01T08:00:00Z')['end'], '2028-02-29')
        with self.assertRaisesRegex(ValueError, 'stop and replan'): s.check_run_year(plan('2027-09-30T12:00:00Z'), '2027-10-01T08:00:00Z')
        with self.assertRaises(ValueError): s.interval(2028, '2026-10-01', '2027-10-02')
        with self.assertRaises(ValueError): s.interval(2027, '2026-10-01', '2027-10-01')
        self.assertEqual(len(plan('2026-10-15T16:00:00Z')['requests']), 32)

    def test_exact_roster_and_request_pins(self):
        for change in ('sensor', 'metadata', 'request', 'pin'):
            with self.subTest(change=change):
                g, c = copy.deepcopy((self.groups, self.captures))
                if change == 'sensor': g[0]['sensor_identities'].pop()
                if change == 'metadata': g[0]['metadata_sha256'] = '0'*64
                if change == 'request': c[0]['receipt']['request']['parameters']['endDate'] = '2026-10-02'; repin(c[0])
                if change == 'pin': c[0]['receipt_sha256'] = '0'*64
                with self.assertRaises(ValueError): build(g, c)

    def test_zeros_no_change_and_qc_revision(self):
        again = build(self.groups, self.captures, prior=self.base)
        self.assertEqual(doc(again)['coverage']['admission_counts']['NO_DATA_CHANGE'], 98)
        self.assertEqual(doc(again)['coverage']['genuine_zeros'], 98)
        c = copy.deepcopy(self.captures)
        body = s.h._json(c[0]['body']); body[0]['data'][0]['values'][0]['qaFlag'] = 'CHANGED'
        c[0]['body'] = s.h.canonical(body); repin(c[0])
        revised = doc(build(self.groups, c, prior=self.base))
        self.assertEqual(revised['coverage']['admission_counts']['ACCEPTED_REVISION'], 1)

    def test_presence_null_and_python_numeric_equality(self):
        c = copy.deepcopy(self.captures)
        for cap in c:
            b = s.h._json(cap['body'])
            for block in b[0]['data']: block['values'][0]['value'] = -0.0
            cap['body'] = s.h.canonical(b); repin(cap)
        self.assertEqual(doc(build(self.groups, c, prior=self.base))['coverage']['admission_counts']['NO_DATA_CHANGE'], 98)
        for cap in c:
            b = s.h._json(cap['body'])
            for block in b[0]['data']: block['values'][0].pop('qaFlag')
            cap['body'] = s.h.canonical(b); repin(cap)
        absent = build(self.groups, c, prior=self.base)
        for cap in c:
            b = s.h._json(cap['body'])
            for block in b[0]['data']: block['values'][0]['qaFlag'] = None
            cap['body'] = s.h.canonical(b); repin(cap)
        self.assertEqual(doc(build(self.groups, c, prior=absent))['coverage']['admission_counts']['ACCEPTED_REVISION'], 98)

    def test_nonnumeric_source_loss_retains_last_admitted(self):
        for mode, state in [('withheld', 'WITHHELD'), ('missing', 'MISSING'), ('empty', 'COMPLETE_VALID_EMPTY')]:
            with self.subTest(mode=mode):
                g, c = fixture(mode=mode, revision=1); d = doc(build(g, c, prior=self.base))
                self.assertEqual(d['coverage']['admission_counts'][state], 98)
                self.assertEqual(d['coverage']['retained_after_nonnumeric'], 98)
                self.assertEqual(d['coverage']['genuine_zeros'], 98)
                self.assertTrue(all(row[3][8] != row[5] for sensor in d['sensors'] for row in sensor['days']))

    def test_omitted_is_not_empty_or_failed(self):
        g, c = fixture(end='2026-10-02'); old = build(g, c, end='2026-10-02')
        g, c = fixture(end='2026-10-02', mode='omitted', revision=1)
        d = doc(build(g, c, end='2026-10-02', prior=old))
        self.assertEqual(d['coverage']['admission_counts']['MISSING_WITHIN_COMPLETED_QUERY'], 98)
        self.assertEqual(d['coverage']['retained_after_nonnumeric'], 98)

    def test_all_required_station_failures_hold_whole_candidate(self):
        before = copy.deepcopy(self.base)
        for group in range(32):
            with self.subTest(group=group):
                c = copy.deepcopy(self.captures); c[group]['receipt']['http_status'] = 503; repin(c[group])
                with self.assertRaises(ValueError): build(self.groups, c, prior=self.base)
                self.assertEqual(self.base, before)

    def test_incomplete_truncated_and_accounting_fail_closed(self):
        for defect in ('complete', 'body', 'accounting', 'metadata', 'missing', 'extra'):
            g, c = copy.deepcopy((self.groups, self.captures))
            if defect == 'complete': c[0]['receipt']['complete'] = False; repin(c[0])
            if defect == 'body': c[0]['body'] = b'['; repin(c[0])
            if defect == 'accounting': c[0]['receipt']['body_accounting_state'] = 'UNKNOWN'; repin(c[0])
            if defect == 'metadata':
                b = s.h._json(c[0]['body']); b[0]['data'][0]['stationElement']['storedUnitCode'] = 'other'; c[0]['body'] = s.h.canonical(b); repin(c[0])
            if defect == 'missing': c.pop()
            if defect == 'extra': c.append(c[0])
            with self.subTest(defect=defect), self.assertRaises(ValueError): build(g, c, prior=self.base)

    def test_flag_and_private_field_bounds(self):
        for key, value in [('qaFlag', 'X'*65), ('qaFlag', '"'*33), ('qaFlag', 'bad\nflag'), ('privateLocator', 'SYNTHETIC_PRIVATE'), ('origValue', 10**33)]:
            c = copy.deepcopy(self.captures); b = s.h._json(c[0]['body']); b[0]['data'][0]['values'][0][key] = value
            c[0]['body'] = s.h.canonical(b); repin(c[0])
            with self.assertRaises(ValueError): build(self.groups, c)

    def test_import_requires_independent_trust_and_semantics(self):
        trust = dict(commit='a'*40, **s._binding(self.base[s.PATHS[0]], self.base[s.PATHS[1]]))
        self.assertEqual(s.import_product(self.base, trusted_binding=trust), doc(self.base))
        for change in ('hash', 'commit', 'missing', 'noncanonical', 'status', 'year', 'orphan'):
            files, t = copy.deepcopy((self.base, trust))
            if change == 'hash': t['product_sha256'] = '0'*64
            if change == 'commit': t.pop('commit')
            if change == 'missing': files.pop(s.PATHS[1])
            if change == 'noncanonical': files[s.PATHS[1]] += b' '
            if change in ('status', 'year', 'orphan'):
                d = doc(files)
                if change == 'status': d['sensors'][0]['days'][0][2] = 'MISSING'
                if change == 'year': d['water_year'] = 2028
                if change == 'orphan': d['elements'].append('0'*64)
                with self.assertRaises(ValueError): s.encode_product(d)
                continue
            with self.subTest(change=change), self.assertRaises(ValueError): s.import_product(files, trusted_binding=t)

    def test_rollover_never_carries_prior_year_rows(self):
        g, c = fixture(year=2028, mode='empty')
        new = doc(build(g, c, year=2028, prior=self.base))
        self.assertEqual(new['parent']['water_year'], 2027)
        self.assertEqual(new['water_year'], 2028)
        self.assertEqual(new['coverage']['last_admitted_numeric'], 0)
        self.assertTrue(all(r[0] == '2027-10-01' and r[3] is None for x in new['sensors'] for r in x['days']))
        self.assertEqual(doc(self.base)['water_year'], 2027)

    def test_product_hard_cap_and_filesystem_closure(self):
        with self.assertRaises(ValueError): s.validate_product_bytes(self.base[s.PATHS[0]], b' '*s.MAX_PRODUCT)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)/'product'; s.write_new_product(root, self.base)
            self.assertEqual(s.read_product(root), self.base)
            with self.assertRaises(ValueError): s.write_new_product(root, self.base)
            (root/'docs/data/snotel-sms/extra').write_text('not allowed')
            with self.assertRaises(ValueError): s.read_product(root)

    def test_query_empty_station_capture_and_request_closure(self):
        g, c = fixture(end='2026-10-02', mode='omitted')
        files = build(g, c, end='2026-10-02')
        d = doc(files); d['sensors'][0]['days'][1][2] = 'COMPLETE_VALID_EMPTY'
        d['coverage'] = s._coverage(d['sensors'])
        with self.assertRaisesRegex(ValueError, 'Empty versus omitted'): s.encode_product(d)
        d = doc(self.base); d['evidence'][0][6] = '0'*64
        with self.assertRaisesRegex(ValueError, 'exact request hash'): s.encode_product(d)
        d = doc(self.base); extra = copy.deepcopy(d['evidence'][0]); extra[5] = '0'*64
        d['evidence'].append(extra)
        for row in d['sensors'][0]['days']:
            row[5] = 32; row[3][8] = row[4][8] = 32
        with self.assertRaisesRegex(ValueError, 'Mixed latest station'): s.encode_product(d)

    def test_fresh_process_restore_and_interruption(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); s.write_new_product(root/'prior', self.base)
            trust = dict(commit='a'*40, **s._binding(self.base[s.PATHS[0]], self.base[s.PATHS[1]]))
            (root/'trust.json').write_bytes(s.h.canonical(trust))
            g, c = fixture(mode='withheld', revision=1)
            # The child reads declared saved bytes rather than regenerating inputs.
            import base64
            inputs = dict(groups=[dict(x, metadata_body=base64.b64encode(x['metadata_body']).decode()) for x in g],
                          captures=[dict(x, body=base64.b64encode(x['body']).decode()) for x in c])
            (root/'inputs.json').write_bytes(s.h.canonical(inputs))
            child = '''import json,sys,os
import base64
from pathlib import Path
from soil_moisture import snotel_snapshot as s
from test_snotel_snapshot import build
p=Path(sys.argv[1]);f=s.read_product(p/'prior');s.import_product(f,trusted_binding=json.loads((p/'trust.json').read_bytes()))
x=json.loads((p/'inputs.json').read_bytes());g=x['groups'];c=x['captures']
for item in g:item['metadata_body']=base64.b64decode(item['metadata_body'],validate=True)
for item in c:item['body']=base64.b64decode(item['body'],validate=True)
out=build(g,c,prior=f)
if sys.argv[2]=='interrupt': os._exit(73)
s.write_new_product(p/sys.argv[2],out)
'''
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join([str(Path(s.__file__).resolve().parents[1]), str(Path(__file__).parent)]))
            def run(mode): return subprocess.run([sys.executable, '-B', '-c', child, str(root), mode], env=env, capture_output=True, timeout=45)
            self.assertEqual(run('interrupt').returncode, 73)
            self.assertEqual(s.read_product(root/'prior'), self.base); self.assertFalse((root/'interrupt').exists())
            for name in ('first', 'second'):
                result = run(name); self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(s.read_product(root/'first'), s.read_product(root/'second'))
            (root/'trust.json').write_bytes(s.h.canonical(dict(trust, product_sha256='0'*64)))
            self.assertNotEqual(run('wrong-trust').returncode, 0)
            self.assertFalse((root/'wrong-trust').exists())
            self.assertEqual(s.read_product(root/'prior'), self.base)


class FullSeasonTests(unittest.TestCase):
    def test_independent_365_and_366_day_complete_serializers(self):
        measurements = []
        for year, days in ((2027, 365), (2028, 366)):
            tick = time.monotonic(); end = f'{year}-09-30'
            g, c = fixture(year, end, flag_size=s.MAX_FLAG_BYTES)
            # Exercise unchanged transport station by station with explicit mocks.
            with tempfile.TemporaryDirectory(prefix='SYNTHETIC_ONLY_snapshot_') as td:
                captured = []
                for i, (group, cap) in enumerate(zip(g, c)):
                    spec = dict(request=cap['receipt']['request'], metadata_body=group['metadata_body'], metadata_sha256=group['metadata_sha256'])
                    client = transport.StationTransport(Path(td)/str(i), [spec], max_attempts=1, retries=0, retry_delay=0,
                        max_response_bytes=1048576, max_total_bytes=1048576, max_elapsed_seconds=60,
                        opener=lambda req, timeout, body=cap['body']: Response(body),
                        wall=lambda: s._clock(cap['receipt']['retrieved_at_utc']).timestamp())
                    captured.extend(client.collect())
                first = build(g, captured, year, end)
            numeric = doc(first)
            self.assertEqual(numeric['coverage']['positions'], 98*days)
            # Worst retained provenance: one distinct prior station query per day.
            # Constructed synthetic committed state, not extra transport attempts.
            gm, cm = fixture(year, end, mode='missing', flag_size=s.MAX_FLAG_BYTES)
            d = doc(build(gm, cm, year, end)); original = numeric['evidence']
            bindings = s.roster_binding(g)
            for station_index, ev in enumerate(original):
                for day_index in range(days):
                    day = str(date(year-1, 10, 1)+timedelta(days=day_index))
                    item = copy.deepcopy(ev); item[2] = day
                    item[3] = s.h.source_timestamp(day).replace('08:00:00', '10:00:00')
                    for j in (4, 5): item[j] = s.h.sha256(s.h.canonical(['SYNTHETIC_DAILY', station_index, day, j]))
                    item[6] = s.h.sha256(s.h.canonical(s.request_for(bindings[station_index], item[1], item[2])))
                    d['evidence'].append(item)
            for sensor, prior_sensor in zip(d['sensors'], numeric['sensors']):
                for k, row in enumerate(sensor['days']):
                    e = 32+row[5]*days+k
                    row[3] = copy.deepcopy(prior_sensor['days'][k][3])
                    row[3][0] = -(10**31-1); row[3][8] = e
                    # Distinct retained element-science provenance at every date;
                    # latest station response still has one element per sensor.
                    d['elements'].append(s.h.sha256(s.h.canonical(['SYNTHETIC_PRIOR_ELEMENT', sensor['sensor_identity'], k])))
                    row[3][7] = len(d['elements'])-1
            d['coverage'] = s._coverage(d['sensors']); prior = s.encode_product(d)
            g2, c2 = fixture(year, end, mode='missing', flag_size=s.MAX_FLAG_BYTES, revision=1)
            final = build(g2, c2, year, end, prior)
            final_doc = doc(final); size = sum(map(len, final.values()))
            self.assertEqual(final_doc['coverage']['retained_after_nonnumeric'], 98*days)
            self.assertEqual(len(final_doc['evidence']), 32*days+32)
            self.assertLessEqual(size, s.MAX_PRODUCT)
            with tempfile.TemporaryDirectory() as td:
                s.write_new_product(Path(td)/'candidate', final)
                self.assertEqual(s.read_product(Path(td)/'candidate'), final)
            measurements.append(dict(water_year=year, days=days, sensors=98, stations=32,
                positions=98*days, combined_product_bytes=size, index_bytes=len(final[s.PATHS[0]]),
                native_product_bytes=len(final[s.PATHS[1]]), max_supported_flag_bytes=s.MAX_FLAG_BYTES,
                simultaneous_prior_latest_records=2*98*days, evidence_records=len(final_doc['evidence']),
                element_digest_records=len(final_doc['elements']),
                mock_transport_calls=32, provider_requests=0, source_body_bytes=sum(len(x['body']) for x in captured),
                seconds=time.monotonic()-tick, peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))
        print('FULL_SEASON_MEASUREMENTS='+json.dumps(measurements, sort_keys=True), flush=True)
        output = os.environ.get('SNOTEL_SNAPSHOT_TEST_METRICS')
        if output: Path(output).write_bytes(s.h.canonical(measurements))


if __name__ == '__main__': unittest.main()
