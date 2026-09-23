"""Actual R runner; deterministic clock and a transport boundary that never uses HTTP."""
import csv, hashlib, json, subprocess, tempfile, unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / 'scripts/build_dendra_daily.R'
def write(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

class SafeguardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name).resolve()
        self.now = self.asof = '2026-09-20T12:00:00Z'
        self.streams = [dict(datastream_id=c*24, station_id='a'*24, parameter='soil_moisture',
            public_level=3, source_is_hidden=False, source_is_geo_protected=False,
            source_name='Fixture', sensor_label=f'{depth} cm', depth_cm=depth,
            orientation='horizontal', native_unit_name='Percent', observed_start_utc='2026-08-21T08:00:00Z',
            unit_normalization=dict(status='verified_percent_conversion',multiplier=1,offset=0,
                target_unit='% volumetric water content',unit_definition=dict(label='Percent',abbreviation='%')),
            source_terms=dict(ds=dict(Aggregate='Average',Medium='Soil',Variable='VolumetricWaterContent'),dt=dict(Unit='Percent')))
            for c,depth in [('1',20),('2',60)]]
        self.catalog = dict(streams=self.streams, stations=[dict(station_id='a'*24,public_level=3,
            source_is_hidden=False,source_is_geo_protected=False)],live_identity_status='verified',verified_at_utc=self.now)
        self.native('2026-08-21','2026-09-20')
        self.invoke('replay', days=30)
        self.prior = (self.p/'state/current.json').read_bytes()
        self.usable = self.snapshot()
    def tearDown(self): self.tmp.cleanup()
    def snapshot(self):
        pointer=json.loads((self.p/'state/current.json').read_text())
        paths=[self.p/'state/generations'/pointer['generation'],Path(pointer['candidate_root'])]
        return {str(f):sha(f) for p in paths for f in p.rglob('*') if f.is_file()}
    def native(self,start,end,empty=(),sparse=(),hours_last=24,partial=False):
        self.start,self.end=start,end
        items=[]
        begin=datetime.fromisoformat(start).replace(tzinfo=timezone.utc)+timedelta(hours=8)
        days=(datetime.fromisoformat(end)-datetime.fromisoformat(start)).days
        for j,s in enumerate(self.streams):
            path=self.p/f'native-{j}.csv';count=0
            with path.open('w',newline='') as f:
                w=csv.writer(f);w.writerow(['t','datastream_id','v','value_status','duplicate_conflict','alternative_out_of_range'])
                for d in range(days):
                    if j in empty: continue
                    for n in range((hours_last if d==days-1 else 24)*6):
                        if j in sparse and n%5==0: continue
                        t=begin+timedelta(days=d,minutes=n*10)
                        w.writerow([t.isoformat().replace('+00:00','Z'),s['datastream_id'],10+j,'number','FALSE','FALSE']);count+=1
            items.append(dict(stream=s,start=start,end=end,native_csv=str(path),native_sha256=sha(path),native_row_count=count,
                chunks=[dict(requested_interval=dict(start_inclusive=start+'T08:00:00.000Z',end_exclusive=end+'T08:00:00.000Z'),
                content_sha256=sha(path),retrieval_last_utc=self.now,latest_observation_utc=None)]))
        write(self.p/'native.json',dict(streams=items,complete=not partial,failures=['second stream failed'] if partial else []))
    def invoke(self,mode='update',days=7,expect=0,transport_failure=False,extra=None):
        write(self.p/'catalog.json',self.catalog)
        args=dict(mode=mode,catalog=str(self.p/'catalog.json'),state=str(self.p/'state'),output=str(self.p/'output'),
                  days=str(days),ledger=str(self.p/'unused-ledger.json'))
        args['as-of']=self.asof
        if mode=='replay': args['native-manifest']=str(self.p/'native.json')
        if extra: args.update(extra)
        wrapper=f'''source({json.dumps(str(RUNNER))})
Sys.time<-function() parse_utc({json.dumps(self.now)})
real_system2<-system2
system2<-function(command,args,...) {{
 if(any(grepl("collect",args,fixed=TRUE))) {{
  if({str(transport_failure).upper()}) return(2L)
  out<-gsub("^'|'$","",args[match("'--output'",args)+1])
  dir.create(out,recursive=TRUE,showWarnings=FALSE)
  file.copy({json.dumps(str(self.p/'native.json'))},file.path(out,"native_manifest.json"))
  return(0L)
 }}
 if(any(grepl("dendra_candidate.py",args,fixed=TRUE))) {{
  clean<-gsub("^'|'$","",args)
  code<-paste0("import sys;sys.path.insert(0,",{json.dumps(repr(str(ROOT/'scripts')))},");import dendra_candidate as a;from datetime import datetime; a.build(sys.argv[1],sys.argv[2],now=datetime.fromisoformat('",{json.dumps(self.now)},"'.replace('Z','+00:00')))")
  return(real_system2(command,shQuote(c("-c",code,clean[match("--generation",clean)+1],clean[match("--output",clean)+1])),...))
 }}
 real_system2(command,args,...)
}}
'''
        rargs='list('+','.join(json.dumps(k)+'='+json.dumps(v) for k,v in args.items())+')'
        wrapper+=f'tryCatch(run({rargs}),error=function(e){{message(conditionMessage(e));quit(status=2)}})\n'
        path=self.p/'run.R';path.write_text(wrapper)
        result=subprocess.run(['Rscript','--vanilla',str(path)],capture_output=True,text=True)
        if expect is not None:self.assertEqual(result.returncode,expect,result.stdout+result.stderr)
        self.assertFalse((self.p/'unused-ledger.json').exists(), 'fixture must never issue HTTP')
        return result
    def held(self):
        self.assertEqual((self.p/'state/current.json').read_bytes(),self.prior)
        self.assertEqual(self.snapshot(),self.usable)
    def test_both_sensors_seven_day_loss(self):
        self.native('2026-09-13','2026-09-20',empty=(0,1))
        self.invoke(expect=2);self.held()
    def test_one_sensor_windows_and_repeat_hold(self):
        for days in (7,14,30):
            with self.subTest(days=days):
                start=(datetime(2026,9,20)-timedelta(days=days)).date().isoformat()
                self.native(start,'2026-09-20',empty=(0,))
                for _ in range(2):self.invoke(days=days,expect=2);self.held()
                report=json.loads(sorted((self.p/'state/runs').glob('*/loss_assessment.json'))[-1].read_text())
                self.assertTrue(report['hold']);self.assertEqual(len(report['streams']),2)
                self.assertIn(self.streams[0]['datastream_id'],report['affected_stream_ids'])
    def test_selection_wide_windows(self):
        for days in (1,7,14,30):
            with self.subTest(days=days):
                self.native((datetime(2026,9,20)-timedelta(days=days)).date().isoformat(),'2026-09-20',empty=(0,1))
                self.invoke(days=days,expect=2);self.held()
    def test_legitimate_single_day_removal(self):
        self.native('2026-09-19','2026-09-20',empty=(0,));self.invoke(days=1)
        p=json.loads((self.p/'state/current.json').read_text())
        daily=json.loads((self.p/'state/generations'/p['generation']/'daily'/('1'*24+'.json')).read_text())
        self.assertEqual(daily['rows'][-1]['n_total'],0);self.assertEqual(daily['rows'][-2]['n_total'],144)
    def test_missing_new_day_is_not_removal(self):
        self.now=self.asof='2026-09-21T12:00:00Z';self.catalog['verified_at_utc']=self.now
        self.native('2026-09-20','2026-09-21',empty=(0,1));self.invoke(days=1)
    def test_full_and_sparse_normal_updates(self):
        for sparse in ((),(0,1)):
            self.native('2026-09-13','2026-09-20',sparse=sparse);self.invoke()
    def test_timeout_and_partial_failure(self):
        self.native('2026-09-13','2026-09-20');self.invoke(transport_failure=True,expect=2);self.held()
        self.native('2026-09-13','2026-09-20',partial=True);self.invoke(expect=2);self.held()
    def test_future_asof_crossing_boundary(self):
        self.now='2026-09-21T06:00:00Z';self.asof='2026-09-21T09:00:00Z';self.catalog['verified_at_utc']=self.now
        self.native('2026-09-20','2026-09-21',hours_last=22)
        self.invoke(days=1,expect=2);self.held()
    def test_clock_boundary_and_recent_asof(self):
        for now,asof,end in [('2026-09-21T07:59:59Z','2026-09-21T07:00:00Z','2026-09-20'),
                              ('2026-09-21T08:00:00Z','2026-09-21T08:00:00Z','2026-09-21')]:
            self.now,self.asof=now,asof;self.catalog['verified_at_utc']=asof
            start=(datetime.fromisoformat(end)-timedelta(days=1)).date().isoformat()
            self.native(start,end);self.invoke(days=1)
    def test_future_catalog_and_retrieval(self):
        self.native('2026-09-19','2026-09-20');self.catalog['verified_at_utc']='2026-09-21T12:00:00Z'
        self.invoke(days=1,expect=2);self.held()
        self.catalog['verified_at_utc']=self.now
        p=json.loads((self.p/'native.json').read_text());p['streams'][0]['chunks'][0]['retrieval_last_utc']='2099-01-01T00:00:00Z';write(self.p/'native.json',p)
        self.invoke(days=1,expect=2);self.held()
    def test_live_modes_reject_future_before_transport(self):
        self.now='2026-09-21T06:00:00Z';self.asof='2026-09-21T09:00:00Z'
        for mode in ('backfill','update','reconcile'):
            r=self.invoke(mode,expect=2,transport_failure=True)
            self.assertIn('Live as-of',r.stderr);self.held()
    def test_old_live_rejected_frozen_replay_allowed(self):
        self.asof='2026-09-18T12:00:00Z'
        self.invoke(expect=2);self.held()
        self.now='2026-09-20T12:00:00Z';self.asof='2026-09-19T12:00:00Z'
        self.native('2026-08-21','2026-09-19')
        self.invoke('replay',days=30,extra={'state':str(self.p/'old-replay'),'start':'2026-08-21'})
    def test_exact_reviewed_reconciliation_only(self):
        self.native('2026-09-13','2026-09-20',empty=(0,1))
        extra={'start':'2026-09-13','end':'2026-09-20','request-generation':'review-fixture'}
        self.invoke('reconcile',expect=2,extra=extra);self.held()
        report=json.loads(sorted((self.p/'state/runs').glob('*/loss_assessment.json'))[-1].read_text())
        review={k:report[k] for k in ('version','assessment_sha256','prior_index_sha256','request_generation')}
        review.update(decision='approve_exact_removal',review_id='synthetic-fixture-only',reviewed_by='test',reason='deterministic source correction fixture')
        good=review['assessment_sha256'];review['assessment_sha256']='0'*64;write(self.p/'review.json',review)
        extra['loss-review']=str(self.p/'review.json')
        self.invoke('reconcile',expect=2,extra=extra);self.held()
        review['assessment_sha256']=good;write(self.p/'review.json',review)
        self.invoke('update',expect=2,extra=extra);self.held()
        self.invoke('reconcile',extra=extra)
        self.assertNotEqual((self.p/'state/current.json').read_bytes(),self.prior)

if __name__=='__main__': unittest.main()
