"""Small synthetic-only saved-replay gate; no private fixtures or live transport."""
import base64
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from soil_moisture import snotel_saved_replay as replay
from soil_moisture import snotel_history as history
import soil_moisture_operations as operations


class SavedReplay(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-saved-replay-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.fixture = replay.load_fixture()
        self.bundle, self.request = replay.build_replay(self.fixture)
        self.states = operations.offline_reconcile(
            self.bundle, self.request, operations.offline_roster(self.bundle, self.request))
        self.ids = self.request['selection']['snotel']

    def state(self, index):
        return self.states['identities']['snotel:' + self.ids[index]]

    def native(self):
        return list(operations._snotel_pool(self.bundle).values())

    def repin(self, batch):
        for cap in batch['captures']:
            cap['receipt_sha256'] = history.sha256(history.canonical(cap['receipt']))
        batch['pins']['expected_capture_set_sha256'] = history.sha256(
            history.canonical([c['receipt_sha256'] for c in batch['captures']]))

    def test_fixed_fixture_bytes_and_no_input_mutation(self):
        before = replay.FIXTURE.read_bytes()
        self.assertEqual(history.sha256(before), replay.FIXTURE_SHA256)
        original = copy.deepcopy(self.fixture)
        replay.build_replay(self.fixture)
        self.assertEqual(self.fixture, original)
        self.assertEqual(replay.FIXTURE.read_bytes(), before)

    def test_determinism_and_idempotence_across_fresh_destinations(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('No sockets')):
            one = replay.replay(self.root / 'one')
            two = replay.replay(self.root / 'two')
        self.assertEqual(one, two)
        self.assertEqual((self.root / 'one/summary/SUMMARY.json').read_bytes(),
                         (self.root / 'two/summary/SUMMARY.json').read_bytes())
        self.assertEqual(one['admitted_observations'], 5)
        self.assertEqual(one['events'], 16)
        for name in ('replay-1', 'replay-2'):
            cp = json.loads((self.root / 'one' / name / 'CHECKPOINT_RECONCILIATION_DRYRUN.json').read_bytes())
            self.assertEqual(cp['reconciliation'], self.states)

    def test_exact_depth_ordinal_and_source_end_timestamp(self):
        self.assertNotEqual(self.ids[1], self.ids[2])
        self.assertTrue(self.ids[1].endswith('|SMS:-8:1'))
        self.assertTrue(self.ids[2].endswith('|SMS:-8:2'))
        for doc in self.native():
            history.validate_history(doc)
            for row in doc['observations']:
                self.assertEqual(row['unit_native'], 'pct')
                self.assertEqual(row['source_timestamp_utc'], history.source_timestamp(row['provider_date']))
                self.assertLess(row['height_depth'], 0)
        self.assertEqual(history.source_timestamp('2026-10-01'), '2026-10-02T08:00:00Z')

    def test_zero_missing_withheld_and_absent_null_flags(self):
        initial = self.native()[0]
        rows = {(x['sensor_identity'], x['provider_date']): x for x in initial['observations']}
        self.assertEqual(rows[self.ids[1], '2026-10-01']['value_native'], 0)
        missing = rows[self.ids[1], '2026-10-02']
        self.assertEqual(missing['observation_state'], 'EXPLICIT_MISSING')
        self.assertIn('value', missing['source_fields_present'])
        self.assertIsNone(missing['provider_record']['value'])
        withheld = rows[self.ids[2], '2026-10-01']
        self.assertEqual(withheld['original_value'], 7.25)
        self.assertNotIn('value', withheld['source_fields_present'])
        self.assertEqual(withheld['qc_flag'], 'S')
        self.assertNotIn('qaFlag', rows[self.ids[3], '2026-10-01']['provider_record'])
        self.assertIsNone(rows[self.ids[3], '2026-10-02']['provider_record']['qaFlag'])
        self.assertEqual(self.state(2)['observations'], {})
        self.assertNotIn('2026-10-02', self.state(1)['observations'])

    def test_revisions_and_failure_retain_accepted_values(self):
        row = self.state(0)['observations']['2026-10-01']['rows'][0]
        self.assertEqual(row['value_native'], 14.5)
        self.assertEqual(row['qc_flag'], 'E')
        self.assertEqual(self.state(0)['outcome'], 'FAILED')
        outcomes = [a['outcome'] for a in self.state(0)['attempts']]
        self.assertEqual(outcomes.count('ACCEPTED_REVISION'), 2)
        self.assertEqual(outcomes.count('NO_DATA_CHANGE'), 2)
        self.assertEqual(len(self.state(0)['observations']), 2)

    def test_complete_empty_failed_unqueried_are_distinct(self):
        self.assertEqual(self.state(4)['outcome'], 'COMPLETE_VALID_EMPTY')
        self.assertEqual(self.state(5)['outcome'], 'FAILED')
        self.assertEqual(self.state(6)['outcome'], 'NOT_QUERIED')
        self.assertEqual(self.state(6)['attempts'], [])
        self.assertEqual(self.state(5)['observations'], {})
        initial = self.native()[0]
        self.assertTrue(history.coverage_on(initial, self.ids[4], '2026-10-01')[0]['successful_coverage'])
        failed = self.native()[1]
        self.assertFalse(history.coverage_on(failed, self.ids[5], '2026-10-01')[0]['successful_coverage'])

    def test_reference_hold_and_planner_diagnostic_preserved(self):
        roster = operations.offline_roster(self.bundle, self.request)
        self.assertEqual(roster['snotel:' + self.ids[3]]['eligibility']['reference'], 'HELD')
        self.assertEqual(len(self.state(3)['observations']), 2)
        plan = operations.offline_plan(roster, self.request)
        self.assertEqual(plan['actual_requests'], 0)
        self.assertFalse(plan['acquisition_authorized'])
        self.assertEqual(plan['networks']['snotel']['attempt_estimate'], 6)
        self.assertFalse(plan['networks']['snotel']['budget_sufficient'])

    def test_missing_or_tampered_fixture_rejected(self):
        with self.assertRaises(ValueError):
            replay.load_fixture(self.root / 'missing.json')
        damaged = self.root / 'damaged.json'
        damaged.write_bytes(replay.FIXTURE.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'fixture pin'):
            replay.load_fixture(damaged)

    def test_schema_identity_and_clock_contradictions(self):
        for kind in ('schema', 'depth', 'clock'):
            with self.subTest(kind=kind):
                bad = copy.deepcopy(self.fixture)
                if kind == 'schema': bad['unexpected'] = True
                elif kind == 'depth': bad['bundle']['roster'][0]['source']['signed_depth'] = -99
                else: bad['request']['cutoffs']['snotel']['as_of'] = '2026-10-05T12:00:00Z'
                with self.assertRaises(ValueError): replay.build_replay(bad)

    def test_tampered_receipt_and_unknown_body_accounting(self):
        for kind in ('pin', 'unknown', 'incomplete'):
            with self.subTest(kind=kind):
                bad = copy.deepcopy(self.fixture)
                batch = bad['batches'][0];receipt = batch['captures'][0]['receipt']
                if kind == 'pin': receipt['retrieved_at_utc'] = '2026-10-03T12:00:01Z'
                elif kind == 'unknown':
                    receipt['total_consumed_body_bytes'] = None;self.repin(batch)
                else:
                    receipt['complete'] = False;self.repin(batch)
                with self.assertRaises(ValueError): replay.build_replay(bad)

    def test_duplicate_capture_event_rejected(self):
        bad = copy.deepcopy(self.bundle)
        bad['events'].append(copy.deepcopy(bad['events'][0]))
        with self.assertRaises(ValueError):
            operations.offline_reconcile(bad, self.request, operations.offline_roster(bad, self.request))

    def test_existing_success_cannot_be_replaced(self):
        destination = self.root / 'valid'
        replay.replay(destination)
        before = {str(p.relative_to(destination)): p.read_bytes() for p in destination.rglob('*') if p.is_file()}
        with self.assertRaises(ValueError): replay.replay(destination)
        self.assertEqual(before, {str(p.relative_to(destination)): p.read_bytes() for p in destination.rglob('*') if p.is_file()})

    def test_interrupted_result_retained_and_fresh_restart(self):
        destination = self.root / 'interrupted'
        real_sync = os.fsync;calls = []
        def fail(fd):
            calls.append(fd)
            if len(calls) == 3: raise OSError('synthetic interruption')
            return real_sync(fd)
        with patch.object(os, 'fsync', side_effect=fail), self.assertRaises(OSError):
            replay.replay(destination)
        self.assertTrue((destination / 'inputs/bundle.json').is_file())
        self.assertFalse((destination / 'replay-1/RESULT.json').exists())
        self.assertFalse((destination / 'summary').exists())
        before = {str(p.relative_to(destination)): p.read_bytes() for p in destination.rglob('*') if p.is_file()}
        with self.assertRaises(ValueError): replay.replay(destination)
        replay.replay(self.root / 'restart')
        self.assertEqual(before, {str(p.relative_to(destination)): p.read_bytes() for p in destination.rglob('*') if p.is_file()})

    def test_partial_checkpoint_resume_and_tamper(self):
        scratch = self.root / 'checkpoint';scratch.mkdir();inputs=scratch/'inputs';inputs.mkdir()
        pin = replay.write_new(inputs/'bundle.json', self.bundle)
        request = copy.deepcopy(self.request)
        request.update(scratch_root=str(scratch), bundle=dict(path=str(inputs/'bundle.json'), sha256=pin['sha256']))
        operations.offline_run(request, scratch/'partial', max_events=8)
        p = scratch/'partial/CHECKPOINT_RECONCILIATION_DRYRUN.json'
        resume = dict(path=str(p), sha256=history.sha256(p.read_bytes()))
        operations.offline_run(request, scratch/'complete', resume=resume)
        result=json.loads((scratch/'complete/CHECKPOINT_RECONCILIATION_DRYRUN.json').read_bytes())
        self.assertEqual(result['reconciliation'], self.states)
        with self.assertRaises(ValueError):
            operations.offline_run(request, scratch/'bad', resume=dict(resume,sha256='0'*64))
        self.assertFalse((scratch/'bad').exists())

    def test_temporary_nonrepository_symlink_output_boundaries(self):
        for path in (ROOT/'forbidden-saved-replay', replay.FIXTURE.parent/'forbidden'):
            with self.assertRaises(ValueError): replay.replay(path)
            self.assertFalse(path.exists())
        (self.root/'link').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError): replay.replay(self.root/'link/forbidden')

    def test_sanitized_summary_and_manifest_closure(self):
        out=self.root/'summary-check';replay.replay(out);public=out/'summary'
        self.assertEqual({p.name for p in public.iterdir()}, {'SUMMARY.json','MANIFEST.json'})
        manifest=json.loads((public/'MANIFEST.json').read_bytes());raw=(public/'SUMMARY.json').read_bytes()
        self.assertEqual(manifest['files'], {'SUMMARY.json':dict(bytes=len(raw),sha256=history.sha256(raw))})
        for path in public.iterdir():
            text=path.read_text()
            self.assertNotIn(str(self.root),text)
            self.assertNotIn(':ZZ:SNTL',text)
            self.assertNotIn('provider_record',text)
        for name,pin in manifest['source_sha256'].items():self.assertEqual(history.sha256((ROOT/name).read_bytes()),pin)

    def test_cli_success_and_bounded_failure(self):
        cmd=[sys.executable,'-B',str(Path(replay.__file__)), '--output',str(self.root/'cli')]
        one=subprocess.run(cmd,capture_output=True,timeout=15)
        self.assertEqual(one.returncode,0,one.stderr)
        self.assertEqual(json.loads(one.stdout)['provider_requests'],0)
        two=subprocess.run(cmd,capture_output=True,timeout=15)
        self.assertEqual(two.returncode,2)
        self.assertLess(len(two.stderr),160)
        self.assertNotIn(str(self.root).encode(),two.stderr)
        self.assertEqual(two.stdout,b'')

    def test_cli_network_and_subprocess_audit_denial(self):
        for action in ('import socket; socket.socket()', 'import subprocess; subprocess.run(["false"])'):
            code='import sys;sys.path.insert(0,'+repr(str(ROOT/'scripts'))+');from soil_moisture.snotel_saved_replay import deny_external_effects;sys.addaudithook(deny_external_effects);'+action
            result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertIn(b'Offline external effect denied',result.stderr)

    def test_workflow_yaml_and_manual_readonly_shape(self):
        path=ROOT/'.github/workflows/qa-snotel-sms-saved-replay.yml'
        parsed=subprocess.run(['ruby','-r','yaml','-r','json','-e',
                               'puts JSON.generate(YAML.load_file(ARGV[0]))',str(path)],
                              capture_output=True,check=True,timeout=10)
        workflow=json.loads(parsed.stdout)
        self.assertEqual(workflow['on'],{'workflow_dispatch':None})
        self.assertEqual(workflow['permissions'],{'contents':'read'})
        self.assertNotIn('concurrency',workflow)
        self.assertEqual(set(workflow['jobs']),{'offline-replay'})
        job=workflow['jobs']['offline-replay'];self.assertEqual(job['timeout-minutes'],5)
        checkout,run,artifact=job['steps'];self.assertEqual(checkout['uses'],'actions/checkout@v6')
        self.assertEqual(checkout['with'],{'ref':'${{ github.sha }}','fetch-depth':1,'persist-credentials':False})
        self.assertIn('scripts/soil_moisture/snotel_saved_replay.py',run['run'])
        self.assertIn('${RUNNER_TEMP}',run['run'])
        self.assertEqual(artifact['uses'],'actions/upload-artifact@v7')
        self.assertEqual(artifact['if'],'${{ success() }}')
        self.assertEqual(artifact['with']['retention-days'],14)
        self.assertEqual(artifact['with']['if-no-files-found'],'error')
        self.assertEqual([Path(p).name for p in artifact['with']['path'].splitlines()],['SUMMARY.json','MANIFEST.json'])
        text=path.read_text()
        for forbidden in ('secrets.', 'schedule:', 'workflow_run:', 'workflow_call:', 'contents: write',
                          'brim-live-main-publish', 'docs/data', 'data/input', 'git push', 'git commit',
                          'main_publisher', 'curl ', 'pip install', 'http://', 'https://'):
            self.assertNotIn(forbidden,text)


if __name__ == '__main__':
    unittest.main()
