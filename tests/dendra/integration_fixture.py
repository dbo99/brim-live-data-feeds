"""Synthetic observations only. Inject transport/clock failures at the real R boundary."""
import copy,csv,json,subprocess
from pathlib import Path
from datetime import datetime,timedelta,timezone
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import dendra_candidate as dc

class Fixture:
    def __init__(self,root,now=None):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.now=(now or datetime.now(timezone.utc)).replace(microsecond=0)
        self.catalog={'integration':dc.load(ROOT/'data/input/dendra/combined_selection.json')['integration'],
            'live_identity_status':'verified','verified_at_utc':self.utc(self.now),'streams':[],
            'stations':[{'station_id':'f'*24,'name':'SYNTHETIC publisher/state test','public_level':3,'source_is_hidden':False,'source_is_geo_protected':False,'geometry':{'type':'Point','coordinates':[-116.65,34.93]}}]}
        for char,depth,param in [('1',20,'soil_moisture'),('2',60,'soil_moisture'),('3',20,'soil_temperature')]:
            temp=param=='soil_temperature';unit='DegreeCelsius' if temp else 'Percent'
            self.catalog['streams'].append(dict(datastream_id=char*24,station_id='f'*24,parameter=param,public_level=3,source_is_hidden=False,source_is_geo_protected=False,source_name='SYNTHETIC TEST',sensor_label=f'{depth} cm',depth_cm=depth,orientation='horizontal',native_unit_name=unit,observed_start_utc='2010-01-01T08:00:00Z',source_attributes={'depth':{'unit_tag':'dt_Unit_Centimeter','value':depth},'orientation':'horizontal'},unit_normalization={'status':'verified_temperature_conversion' if temp else 'verified_percent_conversion','multiplier':1,'offset':0,'target_unit':'degree Celsius' if temp else '% volumetric water content','unit_definition':{'label':unit}},source_terms={'ds':{'Medium':'Soil','Variable':'Temperature' if temp else 'VolumetricWaterContent','Aggregate':'Average'},'dt':{'Unit':unit}}))
    @staticmethod
    def utc(dt):return dt.isoformat().replace('+00:00','Z')
    def native(self,backfill=False,start=None,end=None,variant=0):
        end=end or (self.now-timedelta(hours=8)).date().isoformat();items=[]
        for j,s in enumerate(self.catalog['streams']):
            days=(90 if s['parameter']=='soil_temperature' else 30) if backfill else 7
            begin=start or (datetime.fromisoformat(end)-timedelta(days=days)).date().isoformat()
            csvpath=self.root/f"native-{s['datastream_id']}.csv";count=0;latest=None
            with csvpath.open('w',newline='') as f:
                w=csv.writer(f);w.writerow(['t','datastream_id','v','value_status','duplicate_conflict','alternative_out_of_range'])
                t=datetime.fromisoformat(begin).replace(tzinfo=timezone.utc)+timedelta(hours=8);stop=datetime.fromisoformat(end).replace(tzinfo=timezone.utc)+timedelta(hours=8)
                while t<stop:
                    latest=self.utc(t);w.writerow([latest,s['datastream_id'],(-3 if j==2 else 10+j)+variant,'number','FALSE','FALSE']);count+=1;t+=timedelta(hours=1)
            items.append({'stream':s,'start':begin,'end':end,'native_csv':str(csvpath),'native_sha256':dc.sha(csvpath),'native_row_count':count,'chunks':[{'requested_interval':{'start_inclusive':begin+'T08:00:00.000Z','end_exclusive':end+'T08:00:00.000Z'},'content_sha256':dc.sha(csvpath),'retrieval_last_utc':self.utc(self.now),'latest_observation_utc':latest}]})
        path=self.root/'native.json';dc.write(path,{'streams':items,'complete':True,'failures':[]});return path
    def invoke(self,mode='backfill',state=None,manifest=None,asof=None,failure=None,extra=None,expect=0):
        state=Path(state or self.root/'state');manifest=Path(manifest or self.root/'native.json')
        self.catalog['verified_at_utc']=self.utc(self.now);dc.write(self.root/'catalog.json',self.catalog)
        args={'mode':mode,'catalog':str(self.root/'catalog.json'),'state':str(state),'output':str(self.root/'output'),'as-of':asof or self.utc(self.now),'ledger':str(self.root/'NEVER_HTTP.json')}
        if mode.startswith('replay'):args['native-manifest']=str(manifest)
        if extra:args.update(extra)
        wrapper=f'''source({json.dumps(str(ROOT/'scripts/build_dendra_daily.R'))})
Sys.time<-function()parse_utc({json.dumps(self.utc(self.now))})
real_system2<-system2
system2<-function(command,args,...) {{
 clean<-gsub("^'|'$","",args)
 if(any(grepl("collect",args,fixed=TRUE))) {{
  if({str(failure=='source').upper()})return(2L)
  out<-clean[match("--output",clean)+1];dir.create(out,recursive=TRUE,showWarnings=FALSE)
  file.copy({json.dumps(str(manifest))},file.path(out,"native_manifest.json"));return(0L)
 }}
 if(any(grepl("dendra_candidate.py",args,fixed=TRUE))) {{
  if({str(failure=='build').upper()})return(2L)
  generation<-clean[match("--generation",clean)+1]
  if({str(failure=='validation').upper()}) {{i<-json_read(file.path(generation,"index.json"));i$policy_version<-"INVALID";json_write(i,file.path(generation,"index.json"))}}
  code<-{json.dumps("import sys;sys.path.insert(0,"+repr(str(ROOT/'scripts'))+");import dendra_candidate as d;from datetime import datetime;d.build(sys.argv[1],sys.argv[2],now=datetime.fromisoformat("+repr(self.utc(self.now))+".replace('Z','+00:00')))")}
  return(real_system2(command,shQuote(c("-c",code,generation,clean[match("--output",clean)+1])),...))
 }}
 if(any(grepl("dendra_state.py",args,fixed=TRUE))) {{
  code<-{json.dumps("import sys;sys.path.insert(0,"+repr(str(ROOT/'scripts'))+");import dendra_state as d;from datetime import datetime;d.stage(sys.argv[1],sys.argv[2],now=datetime.fromisoformat("+repr(self.utc(self.now))+".replace('Z','+00:00')))")}
  return(real_system2(command,shQuote(c("-c",code,clean[match("--state",clean)+1],clean[match("--candidate",clean)+1])),...))
 }}
 real_system2(command,args,...)
}}
'''
        if failure=='activation':wrapper+='real_rename<-file.rename\nfile.rename<-function(from,to)if(basename(to)=="current.json")FALSE else real_rename(from,to)\n'
        wrapper+='tryCatch(run(list('+','.join(json.dumps(k)+'='+json.dumps(str(v)) for k,v in args.items())+')),error=function(e){message(conditionMessage(e));quit(status=2)})\n'
        path=self.root/'run.R';path.write_text(wrapper)
        result=subprocess.run(['Rscript','--vanilla',str(path)],capture_output=True,text=True)
        (self.root/f'last-{mode}-{failure or "ok"}.log').write_text(result.stdout+result.stderr)
        assert result.returncode==expect,result.stdout+result.stderr
        assert not (self.root/'NEVER_HTTP.json').exists()
        if expect==0:return dc.load(state/'current.json')
        return result
