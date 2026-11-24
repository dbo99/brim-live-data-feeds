"""Offline acceptance using the pinned public staging objects or published bytes."""
from datetime import timedelta
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO/'scripts'))
import dendra_retained_candidate as candidate
import dendra_sms_publisher as publisher


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.root = self.temp/'candidate'
        if (REPO/publisher.ROOT).exists():
            shutil.copytree(REPO/publisher.ROOT, self.root/publisher.ROOT)
        else:
            candidate.export_blobs(REPO, candidate.STAGING_SHA, self.root)

    def test_generation_and_staged_transaction(self):
        tree = self.temp/'tree'
        tree.mkdir()
        self.assertEqual(publisher.reconcile(self.root, tree)['decision'], 'publish')
        publisher.validate_staged(self.root, tree)
        self.assertEqual(publisher.reconcile(self.root, tree)['decision'], 'no-op')
        (tree/publisher.PATHS[-1]).write_text('{}')
        with self.assertRaises(ValueError):
            publisher.validate_staged(self.root, tree)
        with self.assertRaises(ValueError):
            publisher.reconcile(self.root, tree)

    def test_reject_prior_content(self):
        tree = self.temp/'tree'
        destination = tree/publisher.ROOT
        destination.mkdir(parents=True)
        (destination/'unexpected').touch()
        with self.assertRaises(ValueError):
            publisher.reconcile(self.root, tree)
        self.assertEqual(list(destination.iterdir()), [destination/'unexpected'])

    def test_closure_and_pins(self):
        stray = self.root/'other.json'
        stray.touch()
        with self.assertRaises(ValueError):
            publisher.validate_product(self.root, candidate=True)
        stray.unlink()
        shard = self.root/publisher.PATHS[-1]
        raw = shard.read_bytes()
        shard.write_bytes(raw.replace(b'CDFW', b'AAAA', 1))
        with self.assertRaises(ValueError):
            publisher.validate_product(self.root, candidate=True)
        shard.unlink()
        with self.assertRaises(ValueError):
            publisher.validate_product(self.root, candidate=True)

    def test_scientific_boundary(self):
        files = publisher.validate_product(self.root, candidate=True)
        shard_name = publisher.PATHS[-1]
        for field in ('current', 'admission', 'geometry', 'generation'):
            with self.subTest(field=field):
                altered = files.copy()
                name = shard_name
                shard = json.loads(files[name])
                point = next(r for r in shard['records'] if r['sample'])
                if field == 'current':
                    shard['current'] = point['sample']
                elif field == 'admission':
                    point['value_status'] = 'ACCEPTED_CURRENT'
                elif field == 'geometry':
                    name = publisher.PATHS[1]
                    shard = json.loads(files[name])
                    shard['locator']['unreviewed'] = {}
                else:
                    name = publisher.PATHS[0]
                    shard = json.loads(files[name])
                    shard['sha256'] = '0'*64
                altered[name] = json.dumps(shard).encode()
                with self.assertRaises(ValueError):
                    publisher.validate_science(altered)

    def test_preparation_metadata_and_order(self):
        # The data may already be present in a later checkout. Use the original
        # pre-release main as the authenticated absent prior for this test.
        prior = 'f1bedc09dfba3e66e3d14248f97cf27561b5c932'
        artifact = self.temp/'artifact'
        candidate.prepare(REPO, artifact, prior, prior)
        candidate.verify_artifact(artifact, prior)
        workflow = (REPO/candidate.WORKFLOW).read_text()
        self.assertEqual(re.findall(r'--allowlist (\S+)', workflow), list(publisher.PATHS))

    def test_finite_admission(self):
        event = 'a'*40
        env = dict(GITHUB_ACTIONS='true', GITHUB_REPOSITORY=candidate.REPOSITORY,
                   GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/main',
                   GITHUB_REF_TYPE='branch', GITHUB_RUN_NUMBER='1', GITHUB_RUN_ATTEMPT='1',
                   GITHUB_WORKFLOW_REF=candidate.REPOSITORY+'/'+candidate.WORKFLOW+'@refs/heads/main',
                   GITHUB_SHA=event, RETAINED_EVENT_SHA=event, RETAINED_STAGING_SHA=candidate.STAGING_SHA,
                   GITHUB_RUN_ID='123')
        now = candidate.EXPIRES - timedelta(hours=1)
        self.assertEqual(candidate.admit(env, event, now), event)
        for field in ('GITHUB_REF','GITHUB_RUN_NUMBER','GITHUB_RUN_ATTEMPT','RETAINED_EVENT_SHA','RETAINED_STAGING_SHA'):
            with self.assertRaises(ValueError):
                candidate.admit(dict(env, **{field:'wrong'}), event, now)
        with self.assertRaises(ValueError):
            candidate.admit(env, 'b'*40, now)
        with self.assertRaises(ValueError):
            candidate.admit(env, event, candidate.EXPIRES)


if __name__ == '__main__':
    unittest.main()
