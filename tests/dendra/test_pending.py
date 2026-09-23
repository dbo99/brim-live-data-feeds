"""Portable complete-query bounds, integrity and failure atomicity (synthetic parent)."""
import copy,json,shutil,tempfile,unittest,subprocess,sys
from unittest.mock import patch
from pathlib import Path
from test_coverage import catalog,prior,opener,NOW
import dendra_coverage as cp
import dendra_pending as p
from bridge import BudgetClient
from coverage_collect import collect_coverage
import dendra_candidate as dc
class PendingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.cat=catalog(self.root/'fixture');self.cat['streams']=self.cat['streams'][:1];self.cat['integration']['version']='dendra-integration-2';self.catalog=self.root/'catalog.json';dc.write(self.catalog,self.cat);self.now=NOW.isoformat().replace('+00:00','Z');self.state=self.root/'state';self.parent=self.new_parent(self.state)
        self.plan=cp.plan(self.cat,{self.cat['streams'][0]['datastream_id']:prior(self.cat['streams'][0],end='2024-01-02')},self.parent,self.now,limits={'new_intervals':1})
        c=BudgetClient(self.root/'ledger.json',opener=opener(),now=lambda:NOW);self.first=collect_coverage(self.plan,c,self.state/'intervals',self.root/'collection');assert not self.first['complete']
        self.snap=self.root/'snapshot';self.report=p.export(self.state,self.catalog,self.snap,self.now)
    def tearDown(self):self.tmp.cleanup()
    def new_parent(self,state):
        path=state/'artifacts/SYNTHETIC-parent/candidate'/dc.FIXED[0];dc.write(path,{'explicit':'SYNTHETIC unit-test parent, real committed-parent proof is separate'})
        parent=dict(state_role='published',generation='SYNTHETIC-parent',index_sha256=dc.sha(path),publication_commit='c'*40,candidate_relpath='artifacts/SYNTHETIC-parent/candidate');dc.write(state/'published.json',parent);return parent
    def restore(self,snapshot=None,checksum=None,catalog=None,now=None):
        state=self.root/('fresh-'+str(len(list(self.root.glob('fresh-*')))));self.new_parent(state)
        return p.restore(snapshot or self.snap,checksum or self.report['manifest_sha256'],state,catalog or self.catalog,now or self.now),state
    def mutate(self,fn):
        m=dc.load(self.snap/'manifest.json');fn(m);dc.write(self.snap/'manifest.json',m);return dc.sha(self.snap/'manifest.json')
    def test_fresh_original_bytes_and_next_day_reuse(self):
        report,state=self.restore(now='2024-03-03T20:00:00Z');self.assertEqual(report['completed_intervals'],1)
        original=next((self.state/'intervals/coverage-v1/chunks').glob('*/*.json'));copied=next((state/'intervals/coverage-v1/chunks').glob('*/*.json'));self.assertEqual(original.read_bytes(),copied.read_bytes())
        pending=cp.pending_checkpoints(state/'intervals',self.cat,'2024-03-03T20:00:00Z');plan=cp.plan(self.cat,{self.cat['streams'][0]['datastream_id']:prior(self.cat['streams'][0],end='2024-01-02')},self.parent,'2024-03-03T20:00:00Z',pending_tasks=pending,limits={'new_intervals':1})
        self.assertEqual(plan['intervals'][0]['task_id'],self.plan['intervals'][0]['task_id']);self.assertEqual(dc.load(state/'published.json'),self.parent)
    def test_manifest_envelope_extra_and_symlink_rejected(self):
        raw=(self.snap/'manifest.json').read_bytes();(self.snap/'manifest.json').write_bytes(raw+b' ')
        with self.assertRaises(ValueError):self.restore()
        (self.snap/'manifest.json').write_bytes(raw);chunk=next((self.snap/'chunks').glob('*/*.json'));b=chunk.read_bytes();chunk.write_bytes(b+b' ')
        with self.assertRaises(ValueError):self.restore()
        chunk.write_bytes(b);extra=self.snap/'extra';extra.write_text('extra')
        with self.assertRaises(ValueError):self.restore()
        extra.unlink();extra.symlink_to(chunk)
        with self.assertRaises(ValueError):self.restore()
    def test_parent_selection_code_epoch_and_expiry(self):
        for key,value in [('parent',{**self.parent,'publication_commit':'d'*40}),('selection',{}),('sources',{}),('epoch','wrong')]:
            old=(self.snap/'manifest.json').read_bytes();h=self.mutate(lambda m:m.update({key:value}))
            with self.assertRaises(ValueError):self.restore(checksum=h)
            (self.snap/'manifest.json').write_bytes(old)
        with self.assertRaises(ValueError):self.restore(now='2024-03-17T20:00:00Z')
        cat=copy.deepcopy(self.cat);cat['streams'][0]['source_is_hidden']=True;dc.write(self.root/'hidden.json',cat)
        with self.assertRaises(ValueError):self.restore(catalog=self.root/'hidden.json')
        with self.assertRaises((ValueError,OSError)):self.restore(snapshot=self.root/'missing')
    def test_restore_failure_is_atomic(self):
        with patch.object(p.os,'replace',side_effect=OSError('SYNTHETIC install failure')):
            with self.assertRaises(OSError):self.restore()
        state=next(self.root.glob('fresh-*'));self.assertFalse((state/'intervals/coverage-v1').exists());self.assertEqual(dc.load(state/'published.json'),self.parent)
        self.assertFalse(list((state/'intervals').glob('pending.restore-*')))
    def test_partial_pagination_never_exported(self):
        state=self.root/'partial-state';self.new_parent(state);sid=self.cat['streams'][0]['datastream_id'];rows=[dict(t=f'2024-01-02T{h:02}:00:00Z',datastream_id=sid,v=20) for h in range(8,12)]
        c=BudgetClient(self.root/'partial-ledger.json',max_attempts=2,opener=opener(rows,fail_at=2),now=lambda:NOW);c.fetcher.page_size=2;c.fetcher.sleep_fn=lambda _:None
        out=collect_coverage(self.plan,c,state/'intervals',self.root/'partial-output');self.assertFalse(out['complete']);self.assertEqual(out['completed_intervals'],0)
        r=p.export(state,self.catalog,self.root/'partial-snapshot',self.now);self.assertEqual(r['completed_intervals'],0)
    def test_completed_interval_survives_timeout_and_attempt_exhaustion(self):
        state=self.root/'timeout-state';self.new_parent(state)
        plan=cp.plan(self.cat,{self.cat['streams'][0]['datastream_id']:prior(self.cat['streams'][0],end='2024-01-02')},self.parent,self.now,limits={'new_intervals':2,'attempts':2})
        c=BudgetClient(self.root/'timeout-ledger.json',max_attempts=2,opener=opener(fail_at=2),now=lambda:NOW);c.fetcher.sleep_fn=lambda _:None
        out=collect_coverage(plan,c,state/'intervals',self.root/'timeout-output');self.assertFalse(out['complete']);self.assertEqual(out['completed_intervals'],1)
        r=p.export(state,self.catalog,self.root/'timeout-snapshot',self.now);self.assertEqual(r['completed_intervals'],1);self.assertEqual(dc.load(state/'published.json'),self.parent)
    def test_missing_snapshot_CLI_holds_unless_explicit_reviewed_refetch(self):
        for reviewed in (False,True):
            state=self.root/('CLI-'+str(reviewed));self.new_parent(state);report=state/'recovery.json'
            args=[sys.executable,str(Path(p.__file__)),'restore','--state',str(state),'--catalog',str(self.catalog),'--input',str(self.root/'absent'),'--sha256','a'*64,'--as-of',self.now,'--report',str(report)]
            if reviewed:args.append('--reviewed-refetch')
            proc=subprocess.run(args,capture_output=True,text=True,timeout=20);self.assertEqual(proc.returncode,0 if reviewed else 2)
            r=dc.load(report);self.assertEqual(r['status'],'reviewed_bounded_refetch_required' if reviewed else 'pending_recovery_hold');self.assertFalse(r['durable_progress']);self.assertFalse((state/'intervals/coverage-v1').exists());self.assertEqual(dc.load(state/'published.json'),self.parent)
    def test_bounds_and_incomplete_resealed_response(self):
        h=self.mutate(lambda m:m['files'][0].update(path='../outside'))
        with self.assertRaises(ValueError):self.restore(checksum=h)
        with patch.object(p,'MAX_ROWS',-1):
            with self.assertRaises(ValueError):p.export(self.state,self.catalog,self.root/'too-many',self.now)
        chunk=next((self.state/'intervals/coverage-v1/chunks').glob('*/*.json'));x=dc.load(chunk);x['query_complete']=False;x['collection_sha256']=p.BoundChunks.seal(x);dc.write(chunk,x)
        with self.assertRaises(ValueError):p.export(self.state,self.catalog,self.root/'incomplete',self.now)
if __name__=='__main__':unittest.main()
