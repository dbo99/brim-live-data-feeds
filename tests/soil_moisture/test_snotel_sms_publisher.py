"""Mock filesystem/publication contexts only; no Git transaction."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from test_snotel_snapshot import fixture, build, doc
from soil_moisture import snotel_snapshot as s
import snotel_sms_publisher as p


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.g, self.c = fixture()
        self.first = build(self.g, self.c)
        self.second = build(self.g, self.c, prior=self.first)

    def test_explicit_unseeded_same_stale_successor_and_sibling(self):
        self.assertEqual(p.classify(self.first, None)['candidate_state'], 'new')
        self.assertEqual(p.classify(self.first, self.first)['candidate_state'], 'same')
        self.assertEqual(p.classify(self.first, self.second)['candidate_state'], 'stale')
        self.assertEqual(p.classify(self.second, self.first)['candidate_state'], 'new')
        g, c = fixture(revision=1); sibling = build(g, c, prior=self.first)
        with self.assertRaises(ValueError): p.classify(sibling, self.second)
        with self.assertRaises(ValueError): p.classify(self.second, None)
        third = build(g, c, prior=self.second)
        with self.assertRaises(ValueError): p.classify(self.first, third)  # no invented remote ancestry

    def test_new_query_provenance_is_not_scientific_noop(self):
        self.assertNotEqual(self.first, self.second)
        self.assertEqual(doc(self.second)['coverage']['admission_counts']['NO_DATA_CHANGE'], 98)
        self.assertEqual(p.classify(self.second, self.first)['decision'], 'publish')

    def test_same_generation_different_bytes_and_wrong_year_rejected(self):
        changed = copy.deepcopy(self.first); changed[s.PATHS[1]] += b' '
        with self.assertRaises(ValueError): p.classify(changed, self.first)
        d = doc(self.second); d['water_year'] += 1
        with self.assertRaises(ValueError): s.encode_product(d)

    def test_fresh_main_unrelated_products_preserved_and_staged_exact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); candidate, tree = root/'candidate', root/'main'
            s.write_new_product(candidate, self.second); s.write_new_product(tree, self.first)
            unrelated = tree/'docs/data/SYNTHETIC_OTHER.json'; unrelated.write_bytes(b'newer unrelated feed')
            result = p.reconcile(candidate, tree)
            self.assertEqual(result['decision'], 'publish')
            self.assertEqual(unrelated.read_bytes(), b'newer unrelated feed')
            p.validate_staged(candidate, tree)
            self.assertEqual(p.reconcile(candidate, tree)['decision'], 'no-op')
            third = build(self.g, self.c, prior=self.second)
            for name, body in third.items(): (tree/name).write_bytes(body)
            with self.assertRaises(ValueError): p.validate_staged(candidate, tree)
            self.assertEqual(unrelated.read_bytes(), b'newer unrelated feed')

    def test_failure_leaves_last_good_and_candidate_closure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); candidate, tree = root/'candidate', root/'main'
            s.write_new_product(candidate, self.second); s.write_new_product(tree, self.first)
            (candidate/'undeclared.txt').write_text('synthetic')
            with self.assertRaises(ValueError): p.reconcile(candidate, tree)
            self.assertEqual(s.read_product(tree), self.first)
            (candidate/'undeclared.txt').unlink()
            (candidate/s.PATHS[1]).unlink(); (candidate/s.PATHS[1]).symlink_to(tree/s.PATHS[1])
            with self.assertRaises(ValueError): p.reconcile(candidate, tree)
            self.assertEqual(s.read_product(tree), self.first)

    def test_partial_current_is_not_unseeded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); candidate, tree = root/'candidate', root/'main'
            s.write_new_product(candidate, self.first); (tree/'docs/data/snotel-sms').mkdir(parents=True)
            with self.assertRaises(ValueError): p.reconcile(candidate, tree)

    def test_year_rollover_parent_only(self):
        g, c = fixture(year=2028, mode='empty'); rollover = build(g, c, year=2028, prior=self.first)
        self.assertEqual(p.classify(rollover, self.first)['candidate_state'], 'new')
        self.assertEqual(doc(rollover)['coverage']['last_admitted_numeric'], 0)

    def test_ambiguous_acknowledgment_and_verified_commit(self):
        trust = dict(commit='a'*40, **s._binding(self.second[s.PATHS[0]], self.second[s.PATHS[1]]))
        self.assertEqual(p.verify_acknowledgment(self.second, self.second, commit_binding=trust)['status'], 'ACKNOWLEDGED_EXACT_PRODUCT')
        for current, binding in [(self.first, trust), (self.second, {}), (self.second, dict(trust, product_sha256='0'*64))]:
            with self.assertRaises(ValueError): p.verify_acknowledgment(self.second, current, commit_binding=binding)

    def test_actual_callback_environment_contract(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); candidate, tree = root/'candidate', root/'main'; tree.mkdir()
            s.write_new_product(candidate, self.first)
            env = dict(BRIM_PUBLISH_PRODUCT_ID=s.PRODUCT, BRIM_PUBLISH_FIXED_PATHS=json.dumps(s.PATHS),
                       BRIM_PUBLISH_OWNED_ROOTS='[]', BRIM_PUBLISH_CANDIDATE_ROOT=str(candidate),
                       BRIM_PUBLISH_WORKTREE=str(tree), BRIM_PUBLISH_RESULT=str(root/'result.json'),
                       BRIM_PUBLISH_METADATA=str(root/'metadata.json'))
            metadata = dict(product_id=s.PRODUCT, semantic_key=dict(type='generation', value=doc(self.first)['generation']))
            (root/'metadata.json').write_bytes(s.h.canonical(metadata))
            for phase in ('validate-candidate', 'reconcile', 'validate-staged'):
                self.assertEqual(p.callback_main(dict(env, BRIM_PUBLISH_PHASE=phase)), 0)
            self.assertEqual(json.loads((root/'result.json').read_text())['decision'], 'publish')
            for bad in [dict(BRIM_PUBLISH_PRODUCT_ID='other'), dict(BRIM_PUBLISH_FIXED_PATHS='[]'), dict(BRIM_PUBLISH_OWNED_ROOTS='["docs/data"]')]:
                with self.assertRaises(ValueError): p.callback_main(dict(env, BRIM_PUBLISH_PHASE='reconcile', **bad))
            metadata['semantic_key']['value'] = '0'*64
            (root/'metadata.json').write_bytes(s.h.canonical(metadata))
            with self.assertRaises(ValueError): p.callback_main(dict(env, BRIM_PUBLISH_PHASE='validate-candidate'))

    def test_callback_rejects_current_wy_crossing_and_carry_forward_loss(self):
        d = doc(self.first); d['built_at'] = '2027-10-01T08:00:00Z'
        crossed = s.encode_product(d)
        with self.assertRaisesRegex(ValueError, 'boundary crossed'): p.classify(crossed, None)
        d = doc(self.first); d['as_of'] = d['built_at'] = '2027-10-01T08:00:00Z'
        with self.assertRaises(ValueError): p.classify(s.encode_product(d), None)
        g, c = fixture(mode='missing', revision=1); missing = build(g, c, prior=self.first)
        d = doc(missing)
        for sensor in d['sensors']: sensor['days'][0][3] = None
        # Remove now-unreferenced prior tables; still coherent and rehashed.
        d['elements'] = [d['elements'][i] for i in sorted({x['days'][0][4][7] for x in d['sensors']})]
        d['evidence'] = d['evidence'][32:]
        for sensor in d['sensors']:
            row = sensor['days'][0]; row[5] -= 32; row[4][8] -= 32
        d['coverage'] = s._coverage(d['sensors'])
        forged = s.encode_product(d)
        with self.assertRaisesRegex(ValueError, 'last-admitted'): p.classify(forged, self.first)

    def test_no_fabricated_initial_or_new_year_admitted_baseline(self):
        d = doc(self.second); d['parent'] = None
        with self.assertRaisesRegex(ValueError, 'revision classification'): p.classify(s.encode_product(d), None)
        g, c = fixture(mode='missing', revision=1); d = doc(build(g, c, prior=self.first)); d['parent'] = None
        with self.assertRaisesRegex(ValueError, 'last-admitted'): p.classify(s.encode_product(d), None)
        g, c = fixture(year=2028); seed = build(g, c, year=2028)
        g, c = fixture(year=2028, mode='missing', revision=1); d = doc(build(g, c, year=2028, prior=seed))
        d['parent'] = s._binding(self.first[s.PATHS[0]], self.first[s.PATHS[1]])
        with self.assertRaisesRegex(ValueError, 'last-admitted'): p.classify(s.encode_product(d), self.first)


if __name__ == '__main__': unittest.main()
