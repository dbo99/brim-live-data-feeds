"""Explicitly SYNTHETIC integration2 fixtures; transport/clock injected, never HTTP."""
import copy,csv,json,subprocess
from pathlib import Path
from integration_fixture import Fixture,ROOT,dc
class ArchiveFixture(Fixture):
    def __init__(self,root,now=None):
        super().__init__(root,now);self.catalog['integration']={'version':'dendra-integration-2','temperature_days':90,'update_days':{'soil_moisture':7,'soil_temperature':7}}
    def native(self,**kwargs):
        path=super().native(**kwargs);manifest=dc.load(path)
        for item in manifest['streams']:
            source=Path(item['native_csv']);rows=list(csv.DictReader(source.open()));stream=item['stream']
            value=-3 if stream['parameter']=='soil_temperature' else .2 if stream['native_unit_name']=='VolumetricWaterContent' else 20
            for row in rows:row['v']=str(value+kwargs.get('variant',0)*(0.001 if stream['native_unit_name']=='VolumetricWaterContent' else .1))
            with source.open('w',newline='') as handle:
                writer=csv.DictWriter(handle,fieldnames=['t','datastream_id','v','value_status','duplicate_conflict','alternative_out_of_range']);writer.writeheader();writer.writerows(rows)
            item['native_sha256']=dc.sha(source)
            for chunk in item['chunks']:chunk['content_sha256']=item['native_sha256']
        dc.write(path,manifest);return path
    def bootstrap(self):
        old=Fixture(self.root/'legacy-science',self.now);manifest=old.native(backfill=True);pointer=old.invoke(manifest=manifest)
        candidate=old.root/'state'/pointer['candidate_relpath'];idx=dc.load(candidate/dc.FIXED[0]);seed={'version':'dendra-saved-seed-2','mode':'saved','streams':[]}
        for s in idx['streams']:
            p=candidate/s['diagnostics_path'];seed['streams'].append({'datastream_id':s['datastream_id'],'path':str(p),'sha256':dc.sha(p)})
        path=self.root/'seed.json';dc.write(path,seed)
        return self.invoke('bootstrap',extra={'seed-manifest':str(path)})
    def invoke(self,mode='update',state=None,manifest=None,asof=None,failure=None,extra=None,expect=0):
        # Older explicit-width regression fixtures exercise reconciliation, not the new routine coverage planner.
        if mode=='update' and extra and extra.get('days') not in (None,'7'):
            mode='reconcile';extra={'request-generation':'SYNTHETIC-explicit-interval',**extra}
        state=Path(state or self.root/'state');manifest=Path(manifest or self.root/'native.json');self.catalog['verified_at_utc']=self.utc(self.now);dc.write(self.root/'catalog.json',self.catalog)
        args={'mode':mode,'catalog':str(self.root/'catalog.json'),'state':str(state),'output':str(self.root/'output'),'as-of':asof or self.utc(self.now),'ledger':str(self.root/'NEVER_HTTP.json'),'budget':'1'}
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
  m<-json_read({json.dumps(str(manifest))});plan<-json_read(clean[match("--plan",clean)+1])
  if(identical(plan$version,"dendra-coverage-plan-1")) {{
   m$streams<-lapply(plan$intervals,function(task) {{
    item<-Filter(function(x)x$stream$datastream_id==task$stream$datastream_id&&x$start==task$start&&x$end==task$end,m$streams)
    if(length(item)!=1)stop("Synthetic boundary cannot claim an interval absent from supplied native fixture")
    item<-item[[1]];item$task_id<-task$task_id;item
   }})
   m$version<-"dendra-coverage-collection-1";m$plan_sha256<-plan$plan_sha256
  }}
  json_write(m,file.path(out,"native_manifest.json"));return(0L)
 }}
 if(any(grepl("dendra_candidate.py",args,fixed=TRUE))&&{str(failure=='build').upper()})return(2L)
 real_system2(command,args,...)
}}
tryCatch(run(list('''+','.join(json.dumps(k)+'='+json.dumps(str(v)) for k,v in args.items())+')),error=function(e){message(conditionMessage(e));quit(status=2)})\n'
        path=self.root/'run.R';path.write_text(wrapper);r=subprocess.run(['Rscript','--vanilla',str(path)],capture_output=True,text=True)
        (self.root/f'last-{mode}-{failure or "ok"}.log').write_text(r.stdout+r.stderr);assert r.returncode==expect,r.stdout+r.stderr;assert not (self.root/'NEVER_HTTP.json').exists()
        return dc.load(state/'current.json') if expect==0 else r
