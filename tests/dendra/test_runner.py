"""Real R orchestration with fixture transport and activation fault injection."""
import csv,hashlib,json,os,subprocess,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
ROOT=Path(__file__).resolve().parents[2]
R=ROOT/'scripts/build_dendra_daily.R'
def write(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x))
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name).resolve();self.sid='1'*24;self.station='2'*24
        self.now=datetime.now(timezone.utc).replace(microsecond=0);self.asof=self.now.isoformat().replace('+00:00','Z');self.end=(self.now-timedelta(hours=8)).date();self.start=self.end-timedelta(days=30)
        self.s={'datastream_id':self.sid,'station_id':self.station,'public_level':3,'source_is_hidden':False,'source_is_geo_protected':False,'source_name':'Fixture','sensor_label':'20 cm','depth_cm':20,'orientation':'horizontal','native_unit_name':'Percent','observed_start_utc':self.start.isoformat()+'T08:00:00Z','unit_normalization':{'status':'verified_percent_conversion','multiplier':1,'offset':0,'target_unit':'% volumetric water content','unit_definition':{'label':'Percent','abbreviation':'%'}},'source_terms':{'dt':{'Unit':'Percent'},'ds':{'Aggregate':'Average','Medium':'Soil','Variable':'VolumetricWaterContent'}}}
        self.catalog={'streams':[self.s],'stations':[{'station_id':self.station,'public_level':3,'source_is_hidden':False,'source_is_geo_protected':False}],'live_identity_status':'verified','verified_at_utc':self.asof}
        write(self.p/'catalog.json',self.catalog);self.native(self.start,self.end,False,self.p/'initial.json');self.invoke('replay',self.p/'initial.json')
        self.initial=(self.p/'state/current.json').read_bytes()
    def tearDown(self):self.tmp.cleanup()
    def native(self,start,end,empty,dest):
        csvpath=dest.with_suffix('.csv')
        with csvpath.open('w',newline='') as f:
            w=csv.writer(f);w.writerow(['t','datastream_id','v','value_status','duplicate_conflict','alternative_out_of_range'])
            if not empty:
                t=datetime.combine(start,datetime.min.time(),timezone.utc)+timedelta(hours=8)
                for i in range((end-start).days*24):w.writerow([(t+timedelta(hours=i)).isoformat().replace('+00:00','Z'),self.sid,10,'number','FALSE','FALSE'])
        item={'stream':self.s,'start':start.isoformat(),'end':end.isoformat(),'native_csv':str(csvpath),'native_sha256':digest(csvpath),'native_row_count':0 if empty else (end-start).days*24,'chunks':[{'requested_interval':{'start_inclusive':start.isoformat()+'T08:00:00.000Z','end_exclusive':end.isoformat()+'T08:00:00.000Z'},'content_sha256':'a'*64,'retrieval_last_utc':self.asof,'latest_observation_utc':None}]}
        write(dest,{'streams':[item],'catalog':self.catalog,'complete':True,'failures':[]})
    def invoke(self,mode,native=None,days=1,fail_transport=False,fail_activation=False,expect=0):
        args={'mode':mode,'catalog':str(self.p/'catalog.json'),'state':str(self.p/'state'),'output':str(self.p/'output'),'as-of':self.asof,'days':str(days),'ledger':str(self.p/'ledger.json')}
        if mode=='replay':args['native-manifest']=str(native)
        if mode=='update':
            # Fresh transport is replaced before it can issue any HTTP call.
            manifest=json.loads(native.read_text()) if native else {}
            wrapper=f'''source({json.dumps(str(R))})
real_system2<-system2
system2<-function(command,args,...) {{
 if(any(grepl("collect",args,fixed=TRUE))) {{
  if({str(fail_transport).upper()}) return(2L)
  out<-gsub("^'|'$","",args[match("'--output'",args)+1])
  dir.create(out,recursive=TRUE,showWarnings=FALSE)
  file.copy({json.dumps(str(native))},file.path(out,"native_manifest.json"))
  return(0L)
 }}
 real_system2(command,args,...)
}}
'''
        else:wrapper=f'source({json.dumps(str(R))})\n'
        if fail_activation:wrapper+='real_rename<-file.rename\nfile.rename<-function(from,to) if(basename(to)=="current.json") FALSE else real_rename(from,to)\n'
        rargs='list('+','.join(json.dumps(k)+'='+json.dumps(v) for k,v in args.items())+')'
        wrapper+=f'tryCatch(run({rargs}),error=function(e){{message(conditionMessage(e));quit(status=2)}})\n'
        path=self.p/'wrapper.R';path.write_text(wrapper);r=subprocess.run(['Rscript','--vanilla',str(path)],capture_output=True,text=True)
        self.assertEqual(r.returncode,expect,r.stdout+r.stderr);return r
    def test_real_build_passes_shared_artifact_preparation(self):
        p=json.loads(self.initial)
        r=subprocess.run(['python3',str(ROOT/'scripts/dendra_candidate.py'),'prepare','--root',p['candidate_root'],'--source-sha','a'*40],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
    def test_failed_transport_retains_state_and_candidate(self):
        self.invoke('update',self.p/'initial.json',fail_transport=True,expect=2);self.assertEqual((self.p/'state/current.json').read_bytes(),self.initial)
    def test_complete_empty_replaces_one_day(self):
        self.native(self.end-timedelta(days=1),self.end,True,self.p/'empty.json');self.invoke('update',self.p/'empty.json')
        pointer=json.loads((self.p/'state/current.json').read_text());p=json.loads((self.p/'state/generations'/pointer['generation']/'daily'/f'{self.sid}.json').read_text());self.assertEqual(p['rows'][-1]['n_total'],0);self.assertEqual(p['rows'][-2]['n_total'],24)
    def test_mass_loss_holds_prior(self):
        self.native(self.start,self.end,True,self.p/'empty.json');self.invoke('update',self.p/'empty.json',days=30,expect=2);self.assertEqual((self.p/'state/current.json').read_bytes(),self.initial)
    def test_metadata_change_held(self):
        self.catalog['streams'][0]['unit_normalization']['multiplier']=100;write(self.p/'catalog.json',self.catalog);self.invoke('update',self.p/'initial.json',days=30,expect=2);self.assertEqual((self.p/'state/current.json').read_bytes(),self.initial)
    def test_incomplete_checksum_inventory_held(self):
        p=json.loads(self.initial);p['files']=[];write(self.p/'state/current.json',p);self.invoke('update',self.p/'initial.json',expect=2)
    def test_daily_tamper_held(self):
        p=json.loads(self.initial);daily=self.p/'state/generations'/p['generation']/'daily'/f'{self.sid}.json';daily.write_text('{}');self.invoke('update',self.p/'initial.json',expect=2)
    def test_failed_activation_has_no_candidate_pointer(self):
        self.invoke('update',self.p/'initial.json',days=30,fail_activation=True,expect=2);self.assertEqual((self.p/'state/current.json').read_bytes(),self.initial);self.assertFalse((self.p/'output/current.json').exists())
    def test_update_requires_prior_state(self):
        (self.p/'state/current.json').rename(self.p/'saved.json');self.invoke('update',self.p/'initial.json',expect=2)
    def test_ordinary_resume_key_is_cutoff_stable(self):
        self.invoke('update',self.p/'initial.json',days=30);self.asof=(self.now+timedelta(seconds=1)).isoformat().replace('+00:00','Z');self.invoke('update',self.p/'initial.json',days=30)
        plans=[json.loads(f.read_text()) for f in sorted((self.p/'state/runs').glob('*/plan.json'))];keys={p['request_generation'] for p in plans if p['mode']=='update'};self.assertEqual(len(keys),1)
if __name__=='__main__':unittest.main()
