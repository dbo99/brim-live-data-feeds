#!/usr/bin/env python3
"""SM2C narrow exception: real shared transactions only inside an explicit fixture root.

Every Git cwd/remote is resolved and checked before execution. No network transport,
credentials, inherited hooks/config, implementation-repository commit, or external push.
"""
import argparse,contextlib,hashlib,importlib.util,json,os,shutil,subprocess,sys,tempfile,time
from pathlib import Path
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from archive_fixture import ArchiveFixture as Fixture,dc,ROOT
import dendra_state as ds

class LocalGit:
    def __init__(self,root):
        self.root=Path(root).resolve()
        # Scope cannot be widened merely by supplying an arbitrary --root.
        expected=ROOT.parent/'sm2c/git-fixtures'
        dc.require(self.root.is_relative_to(expected.resolve()),'Fixture root must be beneath work/sm2c/git-fixtures')
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

def run(root):
    g=LocalGit(root);source=g.root/'source';bare=g.root/'remote.git';g.run('init','--bare','--initial-branch=main',bare);g.run('init','--initial-branch=main',source)
    source_files=['scripts/dendra_validation.py','scripts/build_dendra_daily.R','scripts/dendra/archive.R','scripts/main_publisher.py','scripts/dendra_candidate.py','scripts/dendra_archive.py','scripts/dendra_publisher.py','scripts/dendra_state.py','scripts/dendra/core.R','scripts/dendra/integrated.R','scripts/dendra/semantic.R','data/input/dendra/pilot_catalog.json']
    for rel in source_files:
        dest=source/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/rel,dest)
    dc.write(source/'source-bytes.json',{rel:dc.sha(ROOT/rel) for rel in source_files})
    dc.write(source/'docs/data/other/keep.json',{'unrelated':'must survive every Dendra transaction'})
    g.run('add','.',cwd=source);g.run('commit','-m','Synthetic SM2C source fixture only',cwd=source);sha=g.run('rev-parse','HEAD',cwd=source)
    g.run('remote','add','origin',bare,cwd=source);g.run('push','origin','HEAD:refs/heads/main',cwd=source)
    g.env.update(GITHUB_ACTIONS='true',GITHUB_REF='refs/heads/main',GITHUB_REF_TYPE='branch',GITHUB_SHA=sha,BRIM_LIVE_MAIN_PUBLISH='true')
    sys.dont_write_bytecode=True
    spec=importlib.util.spec_from_file_location('fixture_main_publisher',source/'scripts/main_publisher.py');publisher=importlib.util.module_from_spec(spec);spec.loader.exec_module(publisher)
    real_run=publisher._run;hook=None;records=[]
    def safe_run(args,**kw):
        nonlocal hook
        cwd=kw.get('cwd',g.root);g.guard(args,cwd)
        if hook:
            result=hook(args,kw)
            if result is not None:return result
        return real_run(args,**kw)
    publisher._run=safe_run
    original_env=os.environ.copy();os.environ.clear();os.environ.update(g.env)
    tempfile.tempdir=str(g.root/'tmp')
    def head():return g.run('rev-parse','refs/heads/main',cwd=bare)
    def index():return json.loads(g.run('show','refs/heads/main:'+dc.FIXED[0],cwd=bare))
    def args(candidate):
        metadata=dc.prepare(candidate,sha)
        return SimpleNamespace(repo=str(source),candidate_root=str(candidate),metadata=str(metadata),product_id=dc.PRODUCT,target_ref='refs/heads/main',commit_subject='Synthetic Dendra local publication',allowlist=dc.FIXED,owned_root=dc.OWNED,candidate_validator=str(source/'scripts/dendra_publisher.py'),reconcile_callback=str(source/'scripts/dendra_publisher.py'),staged_validator=str(source/'scripts/dendra_publisher.py'))
    def publish(label,candidate,expect=True,unchanged=False):
        before=head();t=time.monotonic();error=None
        try:publisher.publish(args(candidate));passed=True
        except (publisher.PublisherError,dc.shared.PublisherError,ValueError) as e:passed=False;error=str(e)
        dc.require(passed==expect,label+': '+str(error));after=head()
        if unchanged:dc.require(before==after,label+' unexpectedly advanced public state')
        changed=g.run('diff','--name-only',before,after,cwd=bare).splitlines() if before!=after else []
        dc.require(all(x==dc.FIXED[0] or any(x.startswith(p+'/') for p in dc.OWNED) or (label=='unrelated-concurrent-update' and x=='docs/data/other/concurrent.json') for x in changed),'Out-of-scope public changes')
        dc.require(json.loads(g.run('show','refs/heads/main:docs/data/other/keep.json',cwd=bare))=={'unrelated':'must survive every Dendra transaction'},'Other product changed')
        records.append({'scenario':label,'expected_success':expect,'actual_success':passed,'before':before,'after':after,'changed_paths':changed,'elapsed_seconds':time.monotonic()-t,'rejection':error});return after
    def restore(name):
        state=g.root/'states'/name;ds.restore_public(bare,state);return state
    descendant_clock=datetime.now(timezone.utc)-timedelta(minutes=20)
    def descendant(name,parent_state,variant=0):
        f=Fixture(g.root/'synthetic'/name,descendant_clock);f.native(variant=variant)
        p=f.invoke('update',state=parent_state);return ds.candidate_at(parent_state,p)
    try:
        for remote in ('https://example.invalid/x.git','ssh://example.invalid/x.git','git@example.invalid:x.git','/tmp/outside-fixture.git','../outside.git'):
            try:g.remote(remote)
            except ValueError:pass
            else:raise AssertionError('External remote accepted')
        f=Fixture(g.root/'synthetic/first',datetime.now(timezone.utc)-timedelta(hours=1));f.bootstrap();f.native();p=f.invoke();first=ds.candidate_at(f.root/'state',p)
        publish('first-publication',first);published=restore('first');first_pointer=(published/'published.json').read_bytes()
        publish('identical-no-op',first,unchanged=True)
        child=descendant('child',published);dc.require((published/'published.json').read_bytes()==first_pointer,'Preparing advanced published pointer')
        publish('valid-descendant',child);publish('older-candidate',first,unchanged=True)
        # Sibling descendants prepared from the same acknowledged parent are both valid
        # individually; after one wins, the other cannot overwrite it on retry.
        parentA=restore('race-a');parentB=restore('race-b');a=descendant('a',parentA,1);b=descendant('b',parentB,2)
        trigger=[True]
        def conflict(cmd,kw):
            if cmd[:2]==['git','push'] and trigger[0]:trigger[0]=False;publish('conflict-winner',b)
        hook=conflict;publish('two-conflicting-descendants',a,expect=False);hook=None
        dc.require(index()['generation']==dc.load(b/dc.FIXED[0])['generation'],'Conflict overwrote winner')
        # A same-cutoff wrong parent is rejected; retrying the losing descendant is held.
        publish('wrong-same-cutoff-parent',a,expect=False,unchanged=True)
        parent=restore('unrelated');c=descendant('unrelated',parent)
        peer=g.root/'peer';g.run('clone',bare,peer)
        trigger=[True]
        def unrelated(cmd,kw):
            if cmd[:2]==['git','push'] and trigger[0]:
                trigger[0]=False;dc.write(peer/'docs/data/other/concurrent.json',{'concurrent':True});g.run('add','docs/data/other/concurrent.json',cwd=peer);g.run('commit','-m','Synthetic unrelated product update',cwd=peer);g.run('push','origin','HEAD:refs/heads/main',cwd=peer)
        hook=unrelated;publish('unrelated-concurrent-update',c);hook=None
        parent=restore('failure');d=descendant('failure',parent);ack=(parent/'published.json').read_bytes()
        def before(cmd,kw):
            if cmd[:2]==['git','fetch']:raise publisher.PublisherError('Injected transport failure before publication')
        hook=before;publish('failure-before-publication',d,expect=False,unchanged=True);hook=None
        def during(cmd,kw):
            if cmd[:2]==['git','push']:return subprocess.CompletedProcess(cmd,1,'','Injected transport failure during push before remote acceptance')
        hook=during;publish('failure-during-publication',d,expect=False,unchanged=True);hook=None
        dc.require((parent/'published.json').read_bytes()==ack,'Failed push advanced acknowledgement')
        # Real R chooses published parent, even though a prepared child exists.
        e=descendant('retry-from-published',parent);dc.require(dc.load(e/dc.FIXED[0])['parent_generation']==dc.load(parent/'published.json')['generation'],'Unpublished parent leaked')
        # Mutate actual staged bytes after reconciliation and before the real
        # staged callback. Public and acknowledged pointers must remain intact.
        def staged_mutation(cmd,kw):
            if kw.get('env',{}).get('BRIM_PUBLISH_PHASE')=='validate-staged':
                tree=Path(kw['env']['BRIM_PUBLISH_WORKTREE'])
                i=dc.load(tree/dc.FIXED[0]);i['scope']='SYNTHETIC after-reconcile mutation';dc.write(tree/dc.FIXED[0],i)
        hook=staged_mutation;publish('after-reconcile-staged-index-mutation',e,expect=False,unchanged=True);hook=None
        dc.require((parent/'published.json').read_bytes()==ack,'Staged failure advanced acknowledgement')
        publish('clean-retry',e);publish('idempotent-retry',e,unchanged=True)
        def copy_candidate(name):
            target=g.root/'mutations'/name/'candidate';shutil.copytree(e,target);return target
        bad=copy_candidate('semantic');i=dc.load(bad/dc.FIXED[0]);i['product_id']='wrong-product';dc.write(bad/dc.FIXED[0],i);publish('semantic-rejection',bad,expect=False,unchanged=True)
        bad=copy_candidate('scope');dc.write(bad/'docs/data/other/forbidden.json',{});publish('out-of-scope-file',bad,expect=False,unchanged=True)
        # Validate that callbacks, not preparation alone, reject semantically invalid but
        # integrity-rebound metadata, using the actual transaction before staged writes.
        bad=copy_candidate('callback-semantic');i=dc.load(bad/dc.FIXED[0]);i['policy_version']='INVALID';dc.write(bad/dc.FIXED[0],i)
        metadata=bad.parent/'candidate-metadata.json';publisher.prepare_metadata(SimpleNamespace(candidate_root=str(bad),output=str(metadata),product_id=dc.PRODUCT,semantic_key_type='dendra_state_generation',semantic_key=i['generation'],source_event_sha=sha,allowlist=dc.FIXED,owned_root=dc.OWNED))
        aargs=SimpleNamespace(**vars(args(e)));aargs.candidate_root=str(bad);aargs.metadata=str(metadata);before_head=head()
        try:publisher.publish(aargs)
        except publisher.PublisherError as exc:records.append({'scenario':'actual-callback-semantic-rejection','rejection':str(exc),'before':before_head,'after':head()})
        else:raise AssertionError('Invalid candidate passed callback')
        dc.require(before_head==head(),'Invalid candidate advanced remote')
        # Move HEAD during bulk extraction through an unrelated ordinary local
        # commit; captured old bytes must not be acknowledged as the new HEAD.
        mover=g.root/'restore-race-peer';g.run('clone',bare,mover)
        original_popen=ds.subprocess.Popen;trigger=[True]
        def move_head(command,*aa,**kk):
            if 'cat-file' in command and trigger[0]:
                trigger[0]=False
                dc.write(mover/'docs/data/other/restore-race.json',{'race':True})
                g.run('add','docs/data/other/restore-race.json',cwd=mover);g.run('commit','-m','SM2C restore race other product',cwd=mover);g.run('push','origin','HEAD:refs/heads/main',cwd=mover)
            return original_popen(command,*aa,**kk)
        from unittest.mock import patch
        raced=g.root/'states/rejected-restore-race'
        with patch.object(ds.subprocess,'Popen',side_effect=move_head):
            try:ds.restore_public(bare,raced)
            except ValueError as exc:dc.require('moved during restore' in str(exc),'Wrong restore rejection')
            else:raise AssertionError('Moving HEAD acknowledged')
        dc.require(not raced.exists(),'Failed restore exposed destination')
        records.append({'scenario':'HEAD-move-during-bulk-restore-held','destination_absent':True})
        cold=restore('acknowledged-final');receipt=dc.load(cold/'published.json');dc.require(receipt['publication_commit']==head(),'Wrong acknowledgement commit');dc.require(receipt['generation']==index()['generation'],'Reader/state generation mismatch')
        snapshot=g.root/'published-snapshot';ds.export_snapshot(cold,snapshot,'published');ds.restore_snapshot(snapshot,g.root/'cold-published');dc.require(dc.load(g.root/'cold-published/published.json')==receipt,'Published cold restore mismatch')
        # Source fixture checkout remains at its original source commit and clean.
        dc.require(g.run('rev-parse','HEAD',cwd=source)==sha and not g.run('status','--porcelain',cwd=source),'Source fixture checkout mutated')
        report={'status':'passed','scope':'SYNTHETIC local file-protocol Git fixture only','source_fixture_commit':sha,'source_bytes':dc.load(source/'source-bytes.json'),'final_publication_commit':head(),'scenarios':records,'unsafe_remotes_rejected':5,'git_commands':g.commands,'published_snapshot_files':len(ds.inventory(snapshot))}
        dc.write(g.root/'report.json',report);print(json.dumps({'status':'passed','scenarios':len(records),'fixture':str(g.root),'final_commit':head()}));return report
    finally:
        os.environ.clear();os.environ.update(original_env);tempfile.tempdir=None

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);a=p.parse_args();run(a.root)
