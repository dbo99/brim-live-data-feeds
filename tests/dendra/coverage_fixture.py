"""Small actual archive_run with only source I/O/science clock injected."""
import json,subprocess
from pathlib import Path
from archive_fixture import ArchiveFixture,ROOT,dc
class CoverageFixture(ArchiveFixture):
    def coverage_update(self,*,state=None,expect=0,limits=None,settings=None,request_generation=None):
        state=Path(state or self.root/'state');self.catalog['verified_at_utc']=self.utc(self.now);dc.write(self.root/'catalog.json',self.catalog)
        setting={'clock':self.utc(self.now+__import__('datetime').timedelta(seconds=1)),**(settings or {})};dc.write(self.root/'boundary-settings.json',setting)
        args=dict(mode='update',catalog=str(self.root/'catalog.json'),state=str(state),output=str(self.root/'output'),ledger=str(self.root/f'ledger-{len(list(self.root.glob("ledger-*")))}.json'),budget='80',**{'as-of':self.utc(self.now)})
        if limits:
            dc.write(self.root/'limits.json',limits);args['collection-limits']=str(self.root/'limits.json')
        if request_generation:args['request-generation']=request_generation
        wrapper='source('+json.dumps(str(ROOT/'scripts/build_dendra_daily.R'))+')\n'
        wrapper+='clock_count<-0L\nSys.time<-function(){clock_count<<-clock_count+1L;parse_utc('+json.dumps(self.utc(self.now))+')+if(clock_count==1L)0 else 2}\nreal_system2<-system2\n'
        wrapper+='''system2<-function(command,args,...) {
 clean<-gsub("^'|'$","",args)
 if(any(grepl("dendra/bridge.py",clean,fixed=TRUE))) {
  args<-shQuote(c('''+json.dumps(str(ROOT/'tests/dendra/coverage_boundary.py'))+''',"--settings",'''+json.dumps(str(self.root/'boundary-settings.json'))+''',clean[-1]))
 }
 real_system2(command,args,...)
}
'''
        wrapper+='tryCatch(run(list('+','.join(json.dumps(k)+'='+json.dumps(v) for k,v in args.items())+')),error=function(e){message(conditionMessage(e));quit(status=2)})\n'
        p=self.root/'coverage-run.R';p.write_text(wrapper);before=set((state/'runs').glob('*'));r=subprocess.run(['Rscript','--vanilla',str(p)],capture_output=True,text=True,timeout=300)
        added=set((state/'runs').glob('*'))-before;self.last_run=added.pop() if added else None
        log=self.root/f'coverage-{self.last_run.name if self.last_run else "early"}.log';log.write_text(r.stdout+r.stderr)
        assert r.returncode==expect,r.stdout+r.stderr
        return dc.load(state/'current.json') if expect==0 else r

class WorkflowBoundary:
    """Executes the exact workflow preparation argv, replacing bridge I/O only."""
    def __init__(self,root,clock,settings=None):
        self.root=Path(root);self.clock=clock;self.root.mkdir(parents=True,exist_ok=True)
        dc.write(self.root/'settings.json',{'clock':(clock+__import__('datetime').timedelta(seconds=1)).isoformat().replace('+00:00','Z'),**(settings or {})})
    def __call__(self,args,*,cwd=None,env=None):
        from soil_network_workflow import command
        if len(args)>2 and str(args[2]).endswith('build_dendra_daily.R'):
            pairs={str(args[i])[2:]:str(args[i+1]) for i in range(3,len(args),2)}
            text='source('+json.dumps(str(ROOT/'scripts/build_dendra_daily.R'))+')\n'
            text+='clock_count<-0L\nSys.time<-function(){clock_count<<-clock_count+1L;parse_utc('+json.dumps(self.clock.isoformat().replace('+00:00','Z'))+')+if(clock_count==1L)0 else 2}\nreal_system2<-system2\n'
            text+='''system2<-function(command,args,...) {
 clean<-gsub("^'|'$","",args)
 if(any(grepl("dendra/bridge.py",clean,fixed=TRUE)))args<-shQuote(c('''+json.dumps(str(ROOT/'tests/dendra/coverage_boundary.py'))+''',"--settings",'''+json.dumps(str(self.root/'settings.json'))+''',clean[-1]))
 real_system2(command,args,...)
}
'''
            text+='run(list('+','.join(json.dumps(k)+'='+json.dumps(v) for k,v in pairs.items())+'))\n';path=self.root/'workflow.R';path.write_text(text)
            return command(['Rscript','--vanilla',path],cwd=cwd,env=env)
        return command(args,cwd=cwd,env=env)
