"""Only fictional committed products exported under a prebound private temporary root."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_snotel_workflow import StrictGit, PRIOR, w, s
from test_snotel_snapshot import fixture, build, doc
from soil_moisture import snotel_year_end as y


class YearEndTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='snotel-export-test-',dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        groups,caps=fixture(year=2026,end='2025-10-03',mode='mixed')
        self.files=build(groups,caps,year=2026,end='2025-10-03')
        self.git=StrictGit(self.root/'repo',groups,self.files)
        self.parent=self.root/'approved';self.parent.mkdir(mode=0o700)
        self.protected=self.root/'protected';self.protected.mkdir()
        self.args=dict(commit=PRIOR,**s._binding(self.files[s.PATHS[0]],self.files[s.PATHS[1]]),
            destination=self.parent/'wy2026',approved_parent=self.parent,
            protected_roots=[self.protected],as_of='2026-10-01T08:00:00Z')
        self.args['expected_year']=self.args.pop('water_year')

    def export(self, **changes):
        return y.export_generation(self.git.reader(),**dict(self.args,**changes))

    def test_exact_bytes_provisional_coverage_noop(self):
        r=self.export();self.assertEqual(r['status'],'EXPORTED_PROVISIONAL')
        dest=self.args['destination'];raw=(dest/y.MANIFEST).read_bytes();m=s.h._json(raw)
        self.assertEqual(m['status'],'PROVISIONAL — NOT FINALIZED')
        self.assertTrue(m['annual_maintenance']['unfinished_tail'])
        self.assertEqual(m['coverage'],doc(self.files)['coverage'])
        self.assertEqual(m['end'],'2025-10-03')
        self.assertEqual(m['coverage']['admission_counts']['WITHHELD'],98)
        self.assertEqual(m['coverage']['admission_counts']['MISSING'],98)
        for n,b in self.files.items():self.assertEqual((dest/n).read_bytes(),b)
        before={str(p):p.stat().st_mtime_ns for p in dest.rglob('*')}
        again=self.export();self.assertEqual(again['status'],'NO_OP')
        self.assertEqual(before,{str(p):p.stat().st_mtime_ns for p in dest.rglob('*')})
        self.assertEqual({p.relative_to(dest).as_posix() for p in dest.rglob('*') if p.is_file()},
                         {*s.PATHS,y.MANIFEST})

    def test_commit_generation_hash_year_and_boundary(self):
        for field,value in [('commit','0'*40),('generation','0'*64),('index_sha256','0'*64),
                            ('product_sha256','0'*64),('expected_year',2025),
                            ('as_of','2026-10-01T07:59:59Z'),('expected_year',2027)]:
            with self.subTest(field=field),self.assertRaises(ValueError):self.export(**{field:value})
            self.assertFalse(self.args['destination'].exists())

    def test_absent_protected_binding_and_loose_permissions(self):
        for roots in ([],[self.root/'typo']):
            with self.assertRaises(ValueError):self.export(protected_roots=roots)
        self.parent.chmod(0o755)
        with self.assertRaises(ValueError):self.export()

    def test_partial_corrupt_extra_and_conflict_unchanged(self):
        self.export();dest=self.args['destination']
        for name,value in [(y.MANIFEST,b'{}'),(s.PATHS[0],b'{}'),('extra.txt',b'extra')]:
            p=dest/name;old=p.read_bytes() if p.exists() else None;p.write_bytes(value)
            with self.subTest(name=name),self.assertRaises(ValueError):self.export()
            self.assertEqual(p.read_bytes(),value)
            if old is None:p.unlink()
            else:p.write_bytes(old)
        extra=dest/'extra-dir';extra.mkdir()
        with self.assertRaises(ValueError):self.export()
        extra.rmdir()
        p=dest/s.PATHS[0];old=p.read_bytes();p.unlink()
        with self.assertRaises(ValueError):self.export()
        self.assertFalse(p.exists());p.write_bytes(old)

    def test_symlinks_ancestor_destination_member_and_dangling(self):
        link=self.root/'alias';link.symlink_to(self.parent,target_is_directory=True)
        with self.assertRaises(ValueError):self.export(approved_parent=link,destination=link/'out')
        dest=self.args['destination'];dest.symlink_to(self.root/'absent')
        with self.assertRaises(ValueError):self.export()
        dest.unlink();self.export()
        p=dest/s.PATHS[0];p.unlink();p.symlink_to(self.protected/'absent')
        with self.assertRaises(ValueError):self.export()

    def test_both_direction_overlap_and_traversal(self):
        for root in (self.parent,self.args['destination']):
            if not root.exists():root.mkdir()
            with self.subTest(root=root),self.assertRaises(ValueError):self.export(protected_roots=[root])
        with self.assertRaises(ValueError):
            self.export(approved_parent=self.git.root,destination=self.git.root/'export')
        with self.assertRaises(ValueError):
            self.export(destination=self.parent/'..'/'escape')

    def test_collision_after_stage_never_replaced(self):
        dest=self.args['destination']
        def competing(stage,target):
            target.mkdir()
            y._expose(stage,target)
        with self.assertRaises(OSError):self.export(expose=competing)
        self.assertTrue(dest.is_dir());self.assertEqual(list(dest.iterdir()),[])
        stages=list(self.parent.glob('.snotel-export-stage-*'))
        self.assertEqual(len(stages),1);self.assertTrue((stages[0]/y.MANIFEST).exists())

    def test_write_interruption_preserves_source_and_no_exposure(self):
        real=w.snotel_write;calls=[]
        def interrupted(path,data):
            calls.append(path)
            if len(calls)==2:raise OSError('synthetic interruption')
            return real(path,data)
        with patch.object(w,'snotel_write',interrupted),self.assertRaises(OSError):self.export()
        self.assertFalse(self.args['destination'].exists())
        self.assertEqual(self.git.reader().product(PRIOR)[0],self.files)

    def test_no_provider_or_real_git_path(self):
        with patch.object(w.st.StationTransport,'collect',side_effect=AssertionError('forbidden acquisition')), \
             patch.object(w.subprocess,'run',side_effect=AssertionError('forbidden real Git')):
            self.assertEqual(self.export()['status'],'EXPORTED_PROVISIONAL')
