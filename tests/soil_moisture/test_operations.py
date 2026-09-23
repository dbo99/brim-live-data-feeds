"""Fake-clock result/retry proof; no source collection or dispatch."""
import copy,json,sys,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
import soil_moisture_operations as o
import soil_network_workflow as w
T='2026-09-22T12:00:00Z';OLD='2026-09-21T12:00:00Z';END='2026-09-22T12:01:00Z';C='2026-09-22'
def event(n='dendra',rid='one',outcome='success_no_data_change',started=T,finished=END,**kw):
    return o.result(n,rid,C,started,finished,outcome,**kw)
def ack(g='new',clock=END):return dict(generation=g,commit=('a' if g=='new' else 'b')*40,index_sha256='c'*64,observed_at_utc=clock,evidence='verified_committed_product',commit_ancestry=['a'*40,'b'*40] if g=='new' else ['b'*40])
class OperationsTests(unittest.TestCase):
    def test_failed_query_publish_and_out_of_order_clocks(self):
        old=event(rid='old',started=OLD,finished=OLD,last_successful_source_check=OLD,latest_eligible_observation='2026-09-18',candidate_generation='old',acknowledgement=ack('old',OLD))
        success=event(last_successful_source_check=T,candidate_generation='prepared',latest_eligible_observation='2026-09-18')
        failed=event(outcome='publication_failure',phase='publication',last_successful_source_check=T,candidate_generation='prepared',latest_eligible_observation='2026-09-18')
        h=o.aggregate([failed,success,old],END)['networks']['dendra'];self.assertEqual(h['state'],'publication_failure');self.assertEqual(h['last_successful_source_check'],T);self.assertEqual(h['latest_eligible_observation'],'2026-09-18');self.assertEqual(h['acknowledgement'],old['acknowledgement'])
        newer=event(rid='newer',started='2026-09-22T12:02:00Z',finished='2026-09-22T12:03:00Z',acknowledgement=ack(clock='2026-09-22T12:03:00Z'))
        old['finished_at_utc']='2026-09-22T12:04:00Z';old['acknowledgement']['observed_at_utc']='2026-09-22T12:04:00Z'
        h=o.aggregate([newer,old,failed,success],'2026-09-22T12:05:00Z')['networks']['dendra'];self.assertEqual(h['last_attempt']['run_id'],'newer');self.assertEqual(h['acknowledgement']['generation'],'new')
    def test_source_independence_no_barrier_or_clock_substitution(self):
        for failed,healthy in [('dendra','scan'),('scan','dendra')]:
            h=o.aggregate([event(failed,'fail','provider_failure'),event(healthy,'good',acknowledgement=ack())],END)['networks'];self.assertEqual(h[failed]['state'],'provider_failure');self.assertIsNone(h[failed]['last_successful_source_check']);self.assertEqual(h[healthy]['acknowledgement']['generation'],'new');self.assertEqual(h['snotel']['state'],'not_enabled')
    def test_failure_durable_restore_without_candidate_and_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td).resolve();x=event(outcome='provider_failure');o.append(p/'health',x);o.append(p/'health',x)
            with self.assertRaises(ValueError):o.append(p/'health',event(outcome='semantic_hold'))
            sha=o.export(p/'health',p/'snapshot');o.restore(p/'snapshot',sha,p/'restored');self.assertEqual(o.records(p/'restored'),[x]);self.assertFalse(list(p.glob('**/candidate*')))
            (p/'snapshot/extra').write_text('unbound')
            with self.assertRaises(ValueError):o.restore(p/'snapshot',sha,p/'bad')
    def test_morning_boundaries(self):
        import morning_boundaries
        self.assertEqual(morning_boundaries.run()['status'],'passed')
    def test_fractional_query_clock_and_unrelated_ancestry_hold(self):
        a=event(rid='a',last_successful_source_check='2026-09-22T12:00:00Z')
        b=event(rid='b',last_successful_source_check='2026-09-22T12:00:00.900Z')
        self.assertEqual(o.aggregate([a,b],END)['networks']['dendra']['last_successful_source_check'],b['last_successful_source_check'])
        a['acknowledgement']=ack('old');b['acknowledgement']=ack();b['acknowledgement']['commit_ancestry']=['a'*40]
        a['null_reasons'].pop('acknowledgement');b['null_reasons'].pop('acknowledgement')
        with self.assertRaises(ValueError):o.aggregate([a,b],END)
    def test_prepare_failure_and_snotel_gate_always_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td).resolve()
            def never(*a,**kw):raise AssertionError('Must not acquire')
            x=w.prepare('snotel',p/'run',p/'health','gated',C,'a'*40,enabled=True,runner=never,clock=lambda:T);self.assertEqual(x['outcome'],'not_enabled');self.assertEqual(x['requests']['attempts'],0)
            def failure(*a,**kw):raise TimeoutError('SYNTHETIC NRCS common timeout')
            x=w.prepare('scan',p/'scan',p/'health','nrcs-failed',C,'a'*40,enabled=True,runner=failure,clock=lambda:T);self.assertEqual(x['outcome'],'provider_failure');self.assertIsNone(x['candidate_generation']);self.assertIsNone(x['requests']['attempts']);self.assertEqual(len(o.records(p/'health')),2)
    def test_reviewed_latest_removal_and_known_no_eligible_are_truthful(self):
        old=event(rid='old',started=OLD,finished=OLD,candidate_generation='old',latest_eligible_observation='2026-09-21')
        corrected=event(rid='new',candidate_generation='new',latest_eligible_observation='2026-09-20')
        self.assertEqual(o.aggregate([corrected,old],END)['networks']['dendra']['latest_eligible_observation'],'2026-09-20')
        corrected=event(rid='new',candidate_generation='new')
        h=o.aggregate([corrected,old],END)['networks']['dendra'];self.assertIsNone(h['latest_eligible_observation']);self.assertEqual(h['latest_eligible_reason'],'validated candidate has no eligible observation')
    def test_late_receipt_validation_failure_is_durable(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();prepared=root/'prepared.json';prepared.write_text(json.dumps(event('scan',candidate_generation='prepared')))
            for p in w.scan.ALLOWLIST:
                q=root/p;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(b'saved')
            with patch.object(w.scan,'validate_product',return_value=(T,28)),patch.object(w,'source_details',return_value=('prepared',None)),patch.object(w,'command',side_effect=['a'*40,'b'*40]),patch.object(w.subprocess,'check_output',return_value=b'saved'):
                r=w.record_publication(root/'health',prepared,root,True,clock=lambda:END)
            self.assertEqual(r['outcome'],'publication_failure');self.assertIsNone(r['acknowledgement']);self.assertEqual(o.records(root/'health'),[r])
    def test_publisher_arguments_are_source_owned(self):
        scan=w.publisher_args('scan',ROOT,'/private/tmp/SYNTHETIC');dendra=w.publisher_args('dendra',ROOT,'/private/tmp/SYNTHETIC')
        self.assertEqual(scan.count('--allowlist'),5);self.assertEqual(dendra.count('--allowlist'),1);self.assertEqual(dendra.count('--owned-root'),4)
        self.assertNotIn('docs/data/dendra/index.json',scan)
        with self.assertRaises(ValueError):w.publisher_args('snotel',ROOT,'/private/tmp/SYNTHETIC')
if __name__=='__main__':unittest.main()
