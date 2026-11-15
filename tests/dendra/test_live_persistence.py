"""P1 synthetic persistence/relocation checks; no provider or historical mutation."""
from copy import deepcopy
from pathlib import Path
import shutil
from unittest.mock import patch

from scripts.dendra import live_state as storage
from scripts.dendra.live_export import range_preview, verify_range
from scripts.dendra.live_provider import Provider, sync
from scripts.dendra.history_acquisition.safety import Hold, encode, decode, digest, sha
from tests.dendra import test_live_range as fixture

legacy_plan = fixture.plan


def plan(*args, **kwargs):
    value = legacy_plan(*args, **kwargs)
    value.update(persistence_version=2, ledger_attempt_limit=20000)
    return value


class PersistenceTests(fixture.RangeTests):
    """Run the eight accepted range/science scenarios against format 2 as well."""
    def setUp(self):
        super().setUp()
        selected = patch.object(fixture, 'plan', plan)
        selected.start()
        self.addCleanup(selected.stop)

    def start(self, p):
        self.state = storage.State(self.root, fixture.rg.series_binding(
            p['roster'], 2027, p['ledger_attempt_limit'], version=2), create=True)

    def test_one_sample_and_receipt_object_with_compact_references(self):
        p = plan(self.root, days_back=1)
        self.start(p)
        fixture.Injected(p).sync(self.state)
        disk = decode(storage.read(self.root/'index.json'))
        key = next(iter(disk['cells']))
        ref = disk['cells'][key]['current']
        self.assertEqual(ref, disk['cells'][key]['last_candidate'])
        self.assertEqual(ref, disk['range_results'][key])
        revision = decode(storage.read(self.root/'objects'/disk['revisions'][0]['$p1']))
        self.assertEqual(ref, revision['result'])
        sample = decode(storage.read(self.root/'objects'/ref['$p1']))
        receipt = sample['evidence']['pages'][0]
        self.assertEqual(receipt, disk['attempts'][5])
        self.assertNotIn('sample_status', storage.read(self.root/'index.json').decode())
        self.assertEqual(storage.expand(self.root, disk), self.state.data)
        self.assertEqual(self.state._physical, sum(f.stat().st_size for f in self.root.rglob('*') if f.is_file()))
        self.assertEqual(self.state._files, sum(f.is_file() for f in self.root.rglob('*')))

    def test_relocated_authority_unknown_charge_and_resume(self):
        p = plan(self.root, days_back=1)
        self.start(p)
        fixture.Injected(p).sync(self.state)
        accepted = range_preview(self.state)
        p = plan(self.root, days_back=1, run='interrupted', clock='2026-11-14T11:00:00Z')
        injected = fixture.Injected(p)
        provider = Provider(self.state, p, opener=injected.open, now=injected.clock.now,
                            monotonic=injected.clock.mono, wait=injected.clock.wait)
        # Interruption before a response still spends the full reservation.
        self.state.reserve('synthetic-pending', 'synthetic:no-request', provider.clock(), 262144)
        self.state.data['range_pending'] = [s+'/'+d for s,d in p['range_targets']]
        self.state.commit()
        ordinal = len(self.state.data['attempts'])-1
        previous = deepcopy(self.state.data)
        binding = self.state.binding
        self.state.close(); self.state = None
        restored = self.root.parent/'restored'
        shutil.copytree(self.root, restored)
        self.state = storage.State(restored, binding)
        self.assertEqual(self.state.data, previous)
        verify_range(restored, accepted['manifest_path'], accepted['manifest_sha256'], binding)
        p = plan(restored, days_back=1, run='resume', clock='2026-11-14T12:00:00Z')
        fixture.Injected(p).sync(self.state)
        self.assertEqual(self.state.data['attempts'][ordinal]['status'], 'AMBIGUOUS')
        self.assertEqual(self.state.data['attempts'][ordinal]['cap'], 262144)
        self.assertEqual(self.state.data['range_runs'][0]['plan']['output_root'], str(self.root))
        self.assertEqual(self.state.data['range_runs'][0]['plan_sha256'], digest(previous['range_runs'][0]['plan']))
        self.assertGreaterEqual(self.state.data['season_totals']['charged_bytes'], 262144)
        self.assertEqual(range_preview(self.state)['cells'], 2)

    def test_missing_and_corrupt_references_fail_closed(self):
        p = plan(self.root, days_back=1); self.start(p)
        fixture.Injected(p).sync(self.state)
        binding = self.state.binding
        ref = decode(storage.read(self.root/'index.json'))['attempts'][0]['$p1']
        self.state.close(); self.state = None
        for mode in ('missing', 'corrupt'):
            root = self.root.parent/mode
            shutil.copytree(self.root, root)
            member = root/'objects'/ref
            member.chmod(0o600)
            if mode == 'missing': member.unlink()
            else: member.write_bytes(b'{}\n')
            with self.assertRaises(Hold): storage.State(root, binding)

    def test_finite_charge_and_index_boundary_preserve_last_transaction(self):
        p = plan(self.root, days_back=1); self.start(p)
        # Small executable boundary, not a year of simulated requests.
        original = deepcopy(storage.SEASON)
        with patch.dict(storage.SEASON, charged_bytes=10):
            self.state.binding['season_limits'] = deepcopy(storage.SEASON)
            self.state.data['binding'] = deepcopy(self.state.binding)
            self.state.commit()
            self.state.reserve('unknown', 'synthetic:none', p['not_before'], 10)
            self.state.data['provider'] = {'start_ordinal': 1}
            self.state.commit()
            before = storage.read(self.root/'index.json')
            with self.assertRaisesRegex(Hold, 'Aggregate body bound'):
                self.state.reserve('next', 'synthetic:none', p['not_before'], 1)
            self.assertEqual(storage.read(self.root/'index.json'), before)
            self.assertEqual(self.state.data['season_totals']['charged_bytes'], 10)
        self.state.binding['season_limits'] = original
        self.state.data['binding'] = deepcopy(self.state.binding)
        self.state.commit()
        before = storage.read(self.root/'index.json')
        self.state.data['oversized'] = 'x' * storage.MAX_INDEX
        with self.assertRaisesRegex(Hold, 'Write boundary'): self.state.commit()
        self.assertEqual(storage.read(self.root/'index.json'), before)
        self.assertNotIn('oversized', self.state.data)

    def test_finite_physical_reservation_counts_torn_file(self):
        p = plan(self.root, days_back=1); self.start(p)
        real = storage.durable
        def torn(path, body, **kwargs):
            def fail(): raise OSError('synthetic torn write')
            return real(path, body, fault=fail, **kwargs)
        before = self.state._physical
        with patch.object(storage, 'durable', torn):
            with self.assertRaises(OSError): self.state.put(self.root/'objects'/sha(b'abc'), b'abc')
        self.assertEqual(self.state._physical, before+3)
        self.assertEqual(len(list((self.root/'objects').glob('.pending-*'))), 1)
        with patch.dict(storage.SEASON, physical_bytes=self.state._physical+2):
            with self.assertRaisesRegex(Hold, 'physical reservation'):
                self.state.put(self.root/'objects'/sha(b'def'), b'def')

    def test_custody_capacity_refuses_before_injected_dispatch(self):
        p = plan(self.root, days_back=1); self.start(p)
        injected = fixture.Injected(p)
        self.state._physical = storage.SEASON['physical_bytes']-storage.MAX_INDEX
        with self.assertRaisesRegex(Hold, 'custody reservation'):
            injected.sync(self.state)
        self.assertEqual(injected.calls, [])
        self.assertEqual(self.state.data['attempts'], [])

    def test_cached_immutable_collision_still_fails(self):
        p = plan(self.root, days_back=1); self.start(p)
        path = self.root/'objects'/sha(b'first')
        self.state.put(path, b'first')
        with self.assertRaisesRegex(Hold, 'immutable collision'):
            self.state.put(path, b'second')
        with patch.dict(storage.SEASON, files=self.state._files+1):
            with self.assertRaisesRegex(Hold, 'physical reservation'):
                self.state.put(self.root/'objects'/sha(b'new'), b'new')

    def test_old_format_is_not_implicitly_migrated(self):
        p = legacy_plan(self.root, days_back=1)
        binding = fixture.rg.series_binding(p['roster'], 2027, p['ledger_attempt_limit'])
        self.state = storage.State(self.root, binding, create=True)
        fixture.Injected(p).sync(self.state)
        accepted = range_preview(self.state)
        before = storage.read(self.root/'index.json')
        copied = self.root.parent/'legacy-copy'; shutil.copytree(self.root, copied)
        with self.assertRaisesRegex(Hold, 'Snapshot run/root binding'):
            verify_range(copied, accepted['manifest_path'], accepted['manifest_sha256'], binding)
        verify_range(self.root, accepted['manifest_path'], accepted['manifest_sha256'], binding)
        self.assertEqual(storage.read(self.root/'index.json'), before)
        with self.assertRaises(Hold): fixture.rg.series_binding(p['roster'], 2028, 20000, version=2)
