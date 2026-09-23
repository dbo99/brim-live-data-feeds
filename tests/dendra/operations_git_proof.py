#!/usr/bin/env python3
"""SM3A explicit disposable file-Git exception; actual source-owned callbacks."""
import argparse,importlib.util,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path
from datetime import datetime,timedelta,timezone
from archive_fixture import ROOT,dc
from coverage_fixture import CoverageFixture,WorkflowBoundary
import dendra_validation as dv
import dendra_state as ds
import dendra_archive as da
import soil_network_workflow as workflow
import soil_moisture_operations as ops
import scan_soil_moisture_publisher as scan
class LocalGit:
    def __init__(self,root):
        self.root=Path(root).resolve()
        # Scope cannot be widened merely by supplying an arbitrary --root.
        expected=ROOT.parent/'sm3a/git-fixtures'
        dc.require(self.root.is_relative_to(expected.resolve()),'Fixture root must be beneath work/sm3a/git-fixtures')
        dc.require(not self.root.exists(),'Use a new disposable fixture directory')
        self.root.mkdir(parents=True);(self.root/'tmp').mkdir();(self.root/'no-hooks').mkdir()
        env={k:v for k,v in os.environ.items() if not k.startswith('GIT_') and k not in ('BRIM_LIVE_MAIN_PUBLISH','GITHUB_SHA','GITHUB_REF','GITHUB_REF_TYPE','GITHUB_ACTIONS')}
        env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_ALLOW_PROTOCOL='file',GIT_TERMINAL_PROMPT='0',PYTHONDONTWRITEBYTECODE='1',TMPDIR=str(self.root/'tmp'))
        config={'core.hooksPath':str(self.root/'no-hooks'),'credential.helper':'','protocol.allow':'never','protocol.file.allow':'always','user.name':'Dendra synthetic fixture','user.email':'dendra-fixture@example.invalid','commit.gpgSign':'false','tag.gpgSign':'false'}
        env['GIT_CONFIG_COUNT']=str(len(config))
        for i,(k,v) in enumerate(config.items()):env[f'GIT_CONFIG_KEY_{i}']=k;env[f'GIT_CONFIG_VALUE_{i}']=v
        self.env=env;self.commands=[]
    def path(self,p):
        p=Path(p).resolve();dc.require(p.is_relative_to(self.root),'Git path escapes fixture boundary');return p
    def remote(self,value):
        dc.require('://' not in value or value.startswith('file:///'),'Only a local absolute/file remote is allowed')
        if value.startswith('file://'):value=value[7:]
        dc.require(Path(value).is_absolute(),'Relative/SSH remote forbidden');return self.path(value)
    def guard(self,args,cwd):
        self.path(cwd)
        if args[0]!='git':return
        if args[1] in ('push','fetch'):
            remote_name=next(a for a in args[2:] if not str(a).startswith('-'))
            remote=subprocess.check_output(['git','remote','get-url',remote_name],cwd=cwd,env=self.env,text=True).strip();self.remote(remote)
            # pushurl could differ; reject it independently before a real push.
            push=subprocess.check_output(['git','remote','get-url','--push',remote_name],cwd=cwd,env=self.env,text=True).strip();self.remote(push)
        if args[1]=='clone':self.remote(args[-2]);self.path(args[-1])
        if args[1]=='worktree' and args[2]=='add':self.path(args[-2])
        if args[1]=='worktree' and args[2]=='remove':self.path(args[-1])
    def run(self,*args,cwd=None):
        cwd=self.path(cwd or self.root);cmd=['git',*map(str,args)];self.guard(cmd,cwd)
        r=subprocess.run(cmd,cwd=cwd,env=self.env,capture_output=True,text=True);self.commands.append({'args':cmd,'cwd':str(cwd.relative_to(self.root)),'exit':r.returncode})
        if r.returncode:raise RuntimeError(r.stderr)
        return r.stdout.strip()

def run(root,scan_input):
    g=LocalGit(root);source=g.root/'source';bare=g.root/'remote.git';g.run('init','--bare','--initial-branch=main',bare);g.run('init','--initial-branch=main',source)
    names={str(Path('scripts')/n) for n in dv.SOURCES if not n.startswith('../')}|{'data/input/dendra/pilot_catalog.json','scripts/scan_soil_moisture_publisher.py'}
    for name in names:
        dest=source/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
    dc.write(source/'docs/data/other/keep.json',{'unrelated':'retained'});g.run('add','.',cwd=source);g.run('commit','-m','SM3A disposable source fixture only',cwd=source);sha=g.run('rev-parse','HEAD',cwd=source);g.run('remote','add','origin',bare,cwd=source);g.run('push','origin','HEAD:refs/heads/main',cwd=source)
    g.env.update(GITHUB_ACTIONS='true',GITHUB_REF='refs/heads/main',GITHUB_REF_TYPE='branch',GITHUB_SHA=sha,BRIM_LIVE_MAIN_PUBLISH='true')
    spec=importlib.util.spec_from_file_location('sm3a_actual_publisher',source/'scripts/main_publisher.py');pub=importlib.util.module_from_spec(spec);spec.loader.exec_module(pub);real=pub._run;hook=None;callbacks=[];checks=[]
    def safe(args,**kw):
        g.guard(args,kw.get('cwd',g.root))
        if hook:
            result=hook(args,kw)
            if result is not None:return result
        r=real(args,**kw)
        phase=kw.get('env',{}).get('BRIM_PUBLISH_PHASE')
        if phase:callbacks.append(dict(phase=phase,product=kw['env']['BRIM_PUBLISH_PRODUCT_ID'],exit=r.returncode))
        return r
    pub._run=safe;oldenv=os.environ.copy();os.environ.clear();os.environ.update(g.env);tempfile.tempdir=str(g.root/'tmp')
    def head():return g.run('rev-parse','HEAD',cwd=bare)
    def files(network):
        paths=[str(p) for p in scan.ALLOWLIST] if network=='scan' else g.run('ls-tree','-r','--name-only','HEAD','--','docs/data/dendra',cwd=bare).splitlines()
        return {p:g.run('rev-parse','HEAD:'+p,cwd=bare) for p in paths}
    def publish(n,transfer):
        argv=workflow.publisher_args(n,source,transfer);a=pub.build_parser().parse_args(argv[2:]);before=head();pub.publish(a);return before,head()
    def ok(name,**kw):checks.append(dict(scenario=name,passed=True,**kw));print(name,flush=True)
    try:
        for remote in ('https://example.invalid/x','ssh://example.invalid/x','git@example.invalid:x','/tmp/outside.git','../outside'):
            try:g.remote(remote)
            except ValueError:pass
            else:raise AssertionError('External remote permitted')
        now=datetime.now(timezone.utc).replace(microsecond=0)-timedelta(minutes=5);stamp=lambda d:d.isoformat().replace('+00:00','Z');clock=lambda:stamp(now+timedelta(seconds=3));health=g.root/'health';cycle=now.date().isoformat()
        f=CoverageFixture(g.root/'dendra-first',now);f.bootstrap();state=f.root/'state';dc.write(f.root/'catalog.json',f.catalog)
        # Exact workflow helper -> actual R argv -> injected HTTP boundary -> transfer.
        result=workflow.prepare('dendra',g.root/'dendra-prepare',health,'dendra-1',cycle,sha,state=state,catalog=f.root/'catalog.json',as_of=stamp(now),budget=80,enabled=True,runner=WorkflowBoundary(g.root/'boundary',now),clock=clock)
        assert result['outcome']=='success_no_data_change',result
        assert result['last_successful_source_check']==stamp(now+timedelta(seconds=1)).replace('Z','.000Z') and result['acknowledgement'] is None
        publish('dendra',g.root/'dendra-prepare/transfer');ds.restore_public(bare,g.root/'ack-first');ack1=dc.load(g.root/'ack-first/published.json');assert ack1['publication_commit']==head();dendra_before=files('dendra')
        # Dendra fails while saved SCAN prepares through actual validator/metadata, then publishes.
        def failed(*a,**k):raise TimeoutError('SYNTHETIC Dendra source timeout')
        failure=workflow.prepare('dendra',g.root/'dendra-failed',health,'dendra-fail',cycle,sha,state=g.root/'ack-first',catalog=f.root/'catalog.json',as_of=stamp(now),budget=80,enabled=True,runner=failed,clock=clock);assert failure['outcome']=='provider_failure'
        def saved_scan(args,**kwargs):
            if str(args[0])=='Rscript':
                out=Path(kwargs['cwd'])
                for p in scan.ALLOWLIST:
                    target=out/p;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(Path(scan_input)/p.name,target)
                return 'Saved accepted SCAN output at acquisition boundary; no R builder executed'
            return workflow.command(args,**kwargs)
        sr=workflow.prepare('scan',g.root/'scan-prepare',health,'scan-1',cycle,sha,enabled=True,runner=saved_scan,clock=clock);assert sr['outcome']=='success_changes';before,after=publish('scan',g.root/'scan-prepare/transfer');assert after!=before and files('dendra')==dendra_before
        scan_tree=g.root/'scan-committed';g.run('worktree','add','--detach',scan_tree,after,cwd=source)
        scan_receipt=workflow.record_publication(health,g.root/'scan-prepare/run-result.json',scan_tree,True,clock=clock);assert scan_receipt['acknowledgement']['commit']==after and scan_receipt['acknowledgement']['generation']==sr['candidate_generation']
        ok('Dendra-fails-SCAN-advances-actual-callbacks-exact-Dendra-retention',before=before,after=after,retained=dendra_before)
        # NRCS failure and gated SNOTEL do not hold a healthy Dendra no-data-change check.
        scan_before=files('scan');nr=workflow.prepare('scan',g.root/'nrcs-failure',health,'nrcs-fail',cycle,sha,enabled=True,runner=failed,clock=clock);sn=workflow.prepare('snotel',g.root/'snotel-gated',health,'snotel',cycle,sha,enabled=True,runner=failed,clock=clock);assert nr['outcome']=='provider_failure' and nr['requests']['attempts'] is None and sn['outcome']=='not_enabled'
        state=g.root/'ack-second';ds.restore_public(bare,state);oldack=(state/'published.json').read_bytes();now+=timedelta(seconds=5);f.catalog['verified_at_utc']=stamp(now);dc.write(f.root/'catalog.json',f.catalog)
        second=workflow.prepare('dendra',g.root/'dendra-second',health,'dendra-2',cycle,sha,state=state,catalog=f.root/'catalog.json',as_of=stamp(now),budget=80,enabled=True,runner=WorkflowBoundary(g.root/'boundary-second',now),clock=clock);assert second['outcome']=='success_no_data_change',second;assert (state/'published.json').read_bytes()==oldack
        # Publication failure retains successful source-check time and previous committed receipt.
        failed_pub=workflow.record_publication(health,g.root/'dendra-second/run-result.json',bare,False,clock=clock);assert failed_pub['outcome']=='publication_failure' and failed_pub['last_successful_source_check']==second['last_successful_source_check'];assert files('scan')==scan_before
        # A separate attempt key may later acknowledge success (immutable phase records).
        retry=dict(second,run_id='dendra-2-retry');dc.write(g.root/'retry-result.json',retry)
        before,after=publish('dendra',g.root/'dendra-second/transfer');assert after!=before and files('scan')==scan_before
        rec=workflow.record_publication(health,g.root/'retry-result.json',bare,True,clock=clock);assert rec['acknowledgement']['commit']==after
        ok('NRCS-common-failure-Dendra-advances-SCAN-exact',before=before,after=after,retained=scan_before,query_success_publication_failure_distinct=True)
        # Source checkout remains stale at source SHA, while publication uses fresh parent.
        assert g.run('rev-parse','HEAD',cwd=source)==sha and not g.run('status','--porcelain',cwd=source)
        cold=g.root/'cold-latest';ds.restore_public(bare,cold);latest=dc.load(cold/'published.json');assert latest['generation']==rec['acknowledgement']['generation'] and latest['publication_commit']==head()
        view=g.root/'cold-view';da.materialize(ds.candidate_at(cold,latest),view)
        import dendra_coverage as cp
        catalog=dc.load(f.root/'catalog.json');products={s['datastream_id']:dc.load(view/'daily'/(s['datastream_id']+'.json')) for s in catalog['streams']};planned=cp.plan(catalog,products,latest,stamp(now+timedelta(seconds=4)))
        assert planned['parent']==latest and all(s['unqueried_days']==0 for s in planned['stream_coverage'])
        pending=dc.load(state/'current.json')
        try:cp.plan(catalog,products,pending,stamp(now+timedelta(seconds=4)))
        except ValueError:pass
        else:raise AssertionError('Prepared pointer became planning parent')
        ok('exact-latest-cold-recovery-plans-from-committed-parent',receipt=latest,plan=planned['stream_coverage'])
        # Late receipt of old committed bytes cannot replace descendant acknowledgement.
        oldtree=g.root/'old-tree';g.run('worktree','add','--detach',oldtree,ack1['publication_commit'],cwd=source)
        oldprepared=dc.load(g.root/'dendra-prepare/run-result.json');oldprepared['run_id']='old-delayed';dc.write(g.root/'old-result.json',oldprepared)
        delayed=workflow.record_publication(health,g.root/'old-result.json',oldtree,True,clock=lambda:stamp(now+timedelta(seconds=20)));assert delayed['acknowledgement']
        h=ops.aggregate(ops.records(health),stamp(now+timedelta(seconds=21)));assert h['networks']['dendra']['acknowledgement']['commit']==after
        snapshot=g.root/'health-export';healthsha=ops.export(health,snapshot);ops.restore(snapshot,healthsha,g.root/'health-restored');assert ops.records(g.root/'health-restored')==ops.records(health)
        ok('late-old-committed-receipt-and-independent-health-restore',latest=after,late_old=delayed['acknowledgement']['commit'])
        report=dict(status='passed',external_requests=0,fixture_scope=str(g.root),source_fixture_commit=sha,source_bytes={n:dc.sha(source/n) for n in names},checks=checks,actual_callbacks=callbacks,source_boundary_doubles=['Dendra opener hourly synthetic','SCAN saved accepted five files copied at R acquisition boundary','NRCS/Dendra failure exceptions','SNOTEL unconditional source gate'],git_commands=g.commands,health=h)
        dc.write(g.root/'report.json',report);return report
    finally:os.environ.clear();os.environ.update(oldenv);tempfile.tempdir=None
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--scan-input',required=True);a=p.parse_args();run(a.root,a.scan_input)
