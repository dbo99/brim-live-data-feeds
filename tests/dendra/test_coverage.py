"""Small synthetic source outcomes through the actual planner and HTTP transport."""
import copy,io,json,sys,tempfile,unittest,urllib.error,urllib.parse,time
from pathlib import Path
from datetime import datetime,timedelta,timezone
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'scripts/dendra')]
import dendra_coverage as p
from bridge import BudgetClient
from coverage_collect import collect_coverage,BoundChunks
from integration_fixture import Fixture

NOW=datetime(2024,3,2,20,tzinfo=timezone.utc)
def catalog(root):return Fixture(root,NOW).catalog
def parent():return dict(state_role='published',generation='SYNTHETIC-parent',index_sha256='b'*64,publication_commit='c'*40)
def prior(stream,start='2024-01-01',end='2024-02-19',holes=()):
    lo,hi=p.day(start),p.day(end);chunk=dict(requested_interval=dict(start_inclusive=start+'T08:00:00Z',end_exclusive=end+'T08:00:00Z'),content_sha256='a'*64,retrieval_last_utc=end+'T20:00:00Z')
    queries={str(lo+timedelta(days=n)):dict(status='queried',content_sha256='a'*64,retrieved_at_utc=chunk['retrieval_last_utc']) for n in range((hi-lo).days)}
    for k in holes:queries[k]={'status':'unqueried'}
    return dict(stream=stream,source_snapshot=dict(chunks=[chunk],query_by_date=queries))
class Response(io.BytesIO):
    status=200;headers={}
def opener(rows=(),fail_at=None,clock=None):
    calls=[]
    def fetch(req,timeout):
        q=urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query);calls.append(q)
        if fail_at and len(calls)>=fail_at:raise TimeoutError('SYNTHETIC second-page failure')
        if clock is not None:clock[0]+=2
        # Honor transport canonical cursor, inclusive overlap, and effective page bound.
        lo=q.get('t[$gte]',q.get('time[$gte]',[]))[0];hi=q.get('t[$lt]',q.get('time[$lt]',[]))[0]
        limit=int(q['$limit'][0]);data=[x for x in rows if p.utc(lo)<=p.utc(x['t'])<p.utc(hi)][:limit]
        return Response(json.dumps(dict(data=data,limit=limit)).encode())
    fetch.calls=calls;return fetch

class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.cat=catalog(self.root/'fixture');self.cat['streams']=self.cat['streams'][:1];self.cat['integration']['version']='dendra-integration-2'
    def tearDown(self):self.tmp.cleanup()
    def plan(self,products=None,**kw):
        s=self.cat['streams'][0];return p.plan(self.cat,products if products is not None else {s['datastream_id']:prior(s)},parent(),'2024-03-02T20:00:00Z',**kw)
    def client(self,op,**kw):
        c=BudgetClient(self.root/(str(len(list(self.root.glob('*.ledger.json'))))+'.ledger.json'),opener=op,now=lambda:NOW,**kw);c.fetcher.sleep_fn=lambda _:None;return c
    def test_missed_twelve_internal_hole_nonreporting(self):
        v=self.plan();self.assertEqual([(x['start'],x['end']) for x in v['intervals']],[('2024-02-19','2024-03-02')]);self.assertEqual(v['stream_coverage'][0]['planned_unique_days'],12)
        s=self.cat['streams'][0];old=prior(s,end='2024-03-02',holes=('2024-01-05',))
        v=self.plan({s['datastream_id']:old});self.assertEqual([(x['start'],x['end']) for x in v['intervals']],[('2024-01-05','2024-01-06'),('2024-02-24','2024-03-02')])
        # No numeric/eligible/latest observation participates; an empty but checked source is current.
        v=self.plan({s['datastream_id']:prior(s,end='2024-03-02')});self.assertEqual(v['stream_coverage'][0]['unqueried_days'],0);self.assertEqual(v['stream_coverage'][0]['planned_unique_days'],7)
    def test_multi_chunk_defer_resume_and_original_retrieval(self):
        s=self.cat['streams'][0];v=self.plan({s['datastream_id']:prior(s,end='2024-01-02')},limits={'new_intervals':1});self.assertEqual(len(v['intervals']),2)
        op=opener();a=collect_coverage(v,self.client(op),self.root/'state',self.root/'a');self.assertFalse(a['complete']);self.assertEqual((a['completed_intervals'],a['backlog_intervals']),(1,1))
        b=collect_coverage(v,self.client(op),self.root/'state',self.root/'b');self.assertTrue(b['complete']);self.assertEqual(len(op.calls),2);self.assertTrue(b['streams'][0]['cache_hit']);self.assertEqual(a['streams'][0]['chunks'],b['streams'][0]['chunks'])
        self.assertFalse(b['acknowledged_parent_advanced']);self.assertFalse((self.root/'state/published.json').exists())
    def test_onboarding_requires_explicit_identity_bound_horizon(self):
        v=self.plan({});self.assertFalse(v['collection_authorized']);self.assertEqual(v['intervals'],[])
        s=self.cat['streams'][0];self.cat['observation_windows']=dict(version='dendra-observation-windows-1',streams=[dict(datastream_id=s['datastream_id'],identity_sha256=p.identity_hash(s),start_date='2024-02-28',end_policy='completed_cutoff',review_id='synthetic',reason='three day test')]);v=self.plan({});self.assertTrue(v['collection_authorized']);self.assertEqual(v['stream_coverage'][0]['required_days'],3)
        self.cat['streams'][0]['depth_cm']=1
        with self.assertRaises(ValueError):self.plan({})
    def test_calendar_and_temperature_expiry(self):
        s=self.cat['streams'][0];v=self.plan();self.assertEqual(v['end_exclusive'],'2024-03-02');self.assertTrue(any(x['start']<='2024-02-29'<x['end'] for x in v['intervals']))
        v=p.plan(self.cat,{s['datastream_id']:prior(s)},parent(),'2024-10-01T07:59:59Z');self.assertEqual(v['cutoff'],'2024-09-29')
        v=p.plan(self.cat,{s['datastream_id']:prior(s)},parent(),'2024-10-01T08:00:00Z');self.assertEqual(v['cutoff'],'2024-09-30')
        s['parameter']='soil_temperature';self.cat['integration']['temperature_days']=7;v=self.plan();self.assertEqual(v['stream_coverage'][0]['window_start'],'2024-02-24');self.assertGreater(v['stream_coverage'][0]['before_retention_days'],0)
    def test_partial_second_page_and_retry_budget_never_empty(self):
        s=self.cat['streams'][0];rows=[dict(t=f'2024-02-19T{h:02}:00:00Z',datastream_id=s['datastream_id'],v=20) for h in range(8,12)];op=opener(rows,fail_at=2);c=self.client(op,max_attempts=2);c.fetcher.page_limit=2
        # Actual fetcher configuration uses page_size (asserted by request below).
        c.fetcher.page_size=2
        v=self.plan(limits={'attempts':2});out=collect_coverage(v,c,self.root/'state',self.root/'out');self.assertFalse(out['complete']);self.assertEqual(out['completed_intervals'],0);self.assertEqual(len(op.calls),2);self.assertFalse(list((self.root/'state').glob('**/chunks/**/*.json')))
    def test_complete_empty_and_checkpoint_corruption(self):
        v=self.plan();op=opener();a=collect_coverage(v,self.client(op),self.root/'state',self.root/'a');self.assertTrue(a['complete']);self.assertEqual(a['completed_native_rows'],0)
        t=v['intervals'][0];store=BoundChunks(self.root/'state',t);path=store.chunk_path(t['stream']['datastream_id'],t['checkpoint_key']);x=json.loads(path.read_text());x['retrieval_last_utc']='2024-03-03T00:00:00Z';path.write_text(json.dumps(x));b=collect_coverage(v,self.client(op),self.root/'state',self.root/'b');self.assertFalse(b['complete']);self.assertEqual(len(op.calls),1)
    def test_budget_bytes_rows_time_and_backwards_clock(self):
        v=self.plan()
        for field,value in [('max_response_bytes',1),('max_elapsed_seconds',1),('max_source_rows',1)]:
            clock=[0];s=self.cat['streams'][0];rows=[dict(t=f'2024-02-19T{h:02}:00:00Z',datastream_id=s['datastream_id'],v=20) for h in (8,9)]
            op=opener(rows,clock=clock if field=='max_elapsed_seconds' else None);c=self.client(op,**{field:value},monotonic=lambda:clock[0],wall=lambda:clock[0]);out=collect_coverage(v,c,self.root/field,self.root/(field+'-out'));self.assertFalse(out['complete']);self.assertEqual(len(op.calls),1)
        c=self.client(opener(),wall=lambda:0);c.last_wall=1
        with self.assertRaises(Exception):c.remaining([])
    def test_real_deadline_interrupts_slow_drip_read(self):
        class Drip(Response):
            def read(self,*args):
                while True:time.sleep(.05)
        def drip(req,timeout):return Drip(b'')
        c=self.client(drip,max_elapsed_seconds=1);start=time.monotonic()
        out=collect_coverage(self.plan(),c,self.root/'drip',self.root/'drip-out')
        self.assertFalse(out['complete']);self.assertLess(time.monotonic()-start,2);self.assertEqual(out['failures'][0]['error_class'],'budget_limited')
    def test_retry_backoff_respects_remaining_global_deadline(self):
        clock=[0];calls=[]
        def throttled(request,timeout):
            calls.append(request.full_url);clock[0]=299.9
            raise urllib.error.HTTPError(request.full_url,429,'SYNTHETIC throttle',{'Retry-After':'15'},None)
        c=BudgetClient(self.root/'throttle-ledger.json',opener=throttled,now=lambda:NOW,monotonic=lambda:clock[0],wall=lambda:clock[0])
        start=time.monotonic();out=collect_coverage(self.plan(),c,self.root/'throttle',self.root/'throttle-out')
        self.assertEqual(len(calls),1);self.assertFalse(out['complete']);self.assertLess(time.monotonic()-start,1);self.assertEqual(out['failures'][0]['error_class'],'budget_limited')
    def test_prepared_parent_and_binding_mutations_rejected(self):
        bad=parent();bad['state_role']='prepared'
        with self.assertRaises(ValueError):p.plan(self.cat,{},bad,'2024-03-02T20:00:00Z')
        v=self.plan();v['intervals'][0]['stream']=dict(v['intervals'][0]['stream'],depth_cm=3)
        v['plan_sha256']=p.digest({k:x for k,x in v.items() if k!='plan_sha256'})
        with self.assertRaises(ValueError):p.validate_plan(v)
if __name__=='__main__':unittest.main()
